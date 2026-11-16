"""Part-load behaviour of machines built with the 2026-09-24 correlations (an earlier release).

The correlations are a record kept outside the library
(``validation/coefficients/recorded_maps``); the machines here are given them
explicitly, with the rated speed and displacement they were examined with.
What the tests examine is the assembled model, not a default:

* at fixed temperatures the COP is the outcome of heat exchangers unloading
  against the compressor's low-speed losses; no COP shape is prescribed, so
  the guard is that both effects are present and of the size the compressor
  data show, not that COP rises;
* below the speed floor the search does not starve the outdoor coil;
* along the EN 14825 points the COP gradient lies inside the certified
  population (read from the committed Keymark quantiles, which are checked
  against their provenance here).

The correlations themselves are pinned to their archive in
``test_recorded_compressor_maps.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from validation.coefficients.recorded_maps.v2026_09_24 import (
    eta_oi_product,
    make_eta_em,
    make_eta_isen,
    make_eta_vol,
)

from tmhp import AirSourceHeatPump as _AirSourceHeatPump
from tmhp import AirSourceHeatPumpBoiler as _AirSourceHeatPumpBoiler


# The machines the historical refit was examined with: 60 rev/s (space
# conditioning) and 40 rev/s (boilers), the refrigerant-aware displacement of
# that rating point, and the 2026-09-24 correlations bound to that speed.
def AirSourceHeatPump(**kwargs):
    from tmhp.compressor_speed import RATED_POINT_AIR_TO_AIR, refrigerant_aware_displacement

    rated = kwargs.setdefault("rps_rated", 60.0)
    kwargs.setdefault(
        "V_cmp_ref",
        refrigerant_aware_displacement(
            kwargs.get("hp_capacity", 3500), kwargs.get("ref", "R32"), RATED_POINT_AIR_TO_AIR
        ),
    )
    for name, factory in (("eta_cmp_vol", make_eta_vol), ("eta_cmp_isen", make_eta_isen), ("eta_cmp", make_eta_em)):
        kwargs.setdefault(name, factory(rated))
    return _AirSourceHeatPump(**kwargs)


def AirSourceHeatPumpBoiler(**kwargs):
    from tmhp.compressor_speed import refrigerant_aware_displacement

    rated = kwargs.setdefault("rps_rated", 40.0)
    kwargs.setdefault(
        "V_cmp_ref", refrigerant_aware_displacement(kwargs.get("hp_capacity", 15000), kwargs.get("ref", "R32"))
    )
    for name, factory in (("eta_cmp_vol", make_eta_vol), ("eta_cmp_isen", make_eta_isen), ("eta_cmp", make_eta_em)):
        kwargs.setdefault(name, factory(rated))
    return _AirSourceHeatPumpBoiler(**kwargs)


REPO = Path(__file__).resolve().parents[1]


def test_the_wrapped_machine_carries_the_recorded_correlations() -> None:
    model = AirSourceHeatPump(hp_capacity=3500.0, ref="R32")
    assert model.eta_cmp_isen(3.0, 40.0) == make_eta_isen(60.0)(3.0, 40.0) < 1.0
    assert model.eta_cmp_vol(3.0, 40.0) == make_eta_vol(60.0)(3.0, 40.0) < 1.0
    assert model.eta_cmp(3.0, 40.0) == make_eta_em(60.0)(3.0, 40.0) < 1.0
    assert model.rps_rated == pytest.approx(60.0)


def test_models_report_the_compressor_internal_state() -> None:
    """The chain N -> m_dot -> lift -> PR -> W is what part-load validation reads."""
    model = AirSourceHeatPumpBoiler(hp_capacity=9000.0, ref="R32")
    result = model.analyze_steady(T_tank_w=42.5, T0=7.0, Q_ref_tank=9000.0, return_dict=True)
    assert isinstance(result, dict)
    for key in ("n_star [-]", "pr_cmp [-]", "eta_cmp_vol [-]", "eta_cmp_isen [-]", "eta_cmp [-]"):
        assert key in result, key
    assert result["n_star [-]"] == pytest.approx(result["cmp_rpm [rpm]"] / 60.0 / model.rps_rated)
    assert result["eta_cmp_isen [-]"] * result["eta_cmp [-]"] == pytest.approx(
        eta_oi_product(result["pr_cmp [-]"], result["n_star [-]"]), rel=1e-6
    )


def test_part_load_cop_is_heat_exchanger_gain_against_compressor_loss() -> None:
    """At fixed temperatures the model neither prescribes a rising COP nor a roll-over.

    Two things must hold while the compressor can still modulate: with the three
    efficiencies frozen at their rated values COP rises as load falls (heat
    exchangers unload), and with the correlations COP sits below that
    curve by the compressor's measured low-speed loss -- a few per cent at half
    load, more at the speed floor -- without collapsing.
    """
    # Isolate compressor correlations using the historical fan assumption.
    from tmhp.hx_fan import ASHRAE_VSD_COEFFICIENTS

    fan = ASHRAE_VSD_COEFFICIENTS | {"curve_type": "custom"}
    model = AirSourceHeatPumpBoiler(hp_capacity=9000.0, ref="R32", vsd_coeffs=fan, dV_fan_a_min=0.05 * 9000 * 0.00015)
    rated = model.analyze_steady(T_tank_w=42.5, T0=7.0, Q_ref_tank=9000.0, return_dict=True)
    assert isinstance(rated, dict)
    frozen = AirSourceHeatPumpBoiler(
        vsd_coeffs=fan,
        dV_fan_a_min=0.05 * 9000 * 0.00015,
        hp_capacity=9000.0,
        ref="R32",
        eta_cmp_vol=float(rated["eta_cmp_vol [-]"]),
        eta_cmp_isen=float(rated["eta_cmp_isen [-]"]),
        eta_cmp=float(rated["eta_cmp [-]"]),
    )
    hx_only, shipped = [], []
    for fraction in (1.0, 0.8, 0.6, 0.5, 0.4):
        for m, store in ((frozen, hx_only), (model, shipped)):
            result = m.analyze_steady(T_tank_w=42.5, T0=7.0, Q_ref_tank=9000.0 * fraction, return_dict=True)
            assert isinstance(result, dict)
            assert result.get("failure_reason", "none") == "none"
            assert result.get("capacity_clamped") is None, "floor reached earlier than expected"
            store.append(float(result["cop_sys [-]"]))
    assert all(b > a for a, b in zip(hx_only, hx_only[1:], strict=False)), hx_only
    assert shipped[0] == pytest.approx(hx_only[0], rel=1e-6)
    gap = [(h - s) / h for h, s in zip(hx_only, shipped, strict=True)]
    assert all(g >= -1e-9 for g in gap), gap
    assert 0.01 < gap[-1] < 0.20, gap  # the compressor loss at 40 % load is real but not a cliff
    assert shipped[-1] > 0.8 * shipped[0], shipped  # no roll-over below 80 % of the rated COP


def test_low_load_operation_meets_the_request_and_keeps_the_coil_fed() -> None:
    """Below the speed floor the model must not starve the outdoor coil to shave power.

    The optimiser picks the operating point by specific energy among candidates
    that meet the request; the outdoor fan must stay well above its bound and
    the air-side temperature drop within what real coils show (7.7 K max across
    1,414 catalogue coils).
    """
    model = AirSourceHeatPumpBoiler(hp_capacity=9000.0, ref="R32")
    for fraction in (0.5, 0.4, 0.3, 0.25, 0.2):
        result = model.analyze_steady(T_tank_w=42.5, T0=7.0, Q_ref_tank=9000.0 * fraction, return_dict=True)
        assert isinstance(result, dict)
        assert result.get("failure_reason", "none") == "none", (fraction, result.get("failure_reason"))
        request = 9000.0 * fraction
        assert result["Q_ref_tank [W]"] >= request * (1 - 0.02), (fraction, result["Q_ref_tank [W]"])
        assert result["dV_ou_a [m3/s]"] / model.dV_fan_a_rated >= 0.30, (
            fraction,
            result["dV_ou_a [m3/s]"] / model.dV_fan_a_rated,
        )
        assert result["T_ou_a_in [°C]"] - result["T_ou_a_out [°C]"] <= 8.0, (
            fraction,
            result["T_ou_a_in [°C]"] - result["T_ou_a_out [°C]"],
        )


def test_air_to_air_low_load_keeps_the_coil_fed_too() -> None:
    """Same guard for the air-to-air model: no starved coil at the speed floor."""
    model = AirSourceHeatPump(hp_capacity=3500.0, ref="R32")
    for fraction in (0.4, 0.3, 0.2):
        result = model.analyze_steady(Q_r_iu=-3500.0 * fraction, T0=7.0, T_a_room=20.0, return_dict=True, verbose=False)
        assert isinstance(result, dict)
        assert result.get("failure_reason", "none") == "none", (fraction, result.get("failure_reason"))
        assert abs(result["Q_ref_iu [W]"]) >= 3500.0 * fraction * (1 - 0.02)
        assert result["dV_ou_a [m3/s]"] / model.dV_ou_fan_a_rated >= 0.25, fraction
        assert abs(result["T_ou_a_in [°C]"] - result["T_ou_a_out [°C]"]) <= 8.0, fraction


def test_en14825_trajectory_cop_rises_monotonically() -> None:
    """Along the certified test points, load and lift fall together."""
    points = [("A", -7.0, 34.0, 0.88), ("B", 2.0, 30.0, 0.54), ("C", 7.0, 27.0, 0.35), ("D", 12.0, 24.0, 0.15)]
    design_load = 6000.0
    # The running machine at each point (see the gradient test below): the
    # certified COP of a condition is declared at the capacity run there.
    model = AirSourceHeatPumpBoiler(hp_capacity=9000.0, ref="R32", Cd_cycling=None)

    cops = []
    for label, t_outdoor, lwt, load_ratio in points:
        result = model.analyze_steady(
            T_tank_w=lwt - 2.5, T0=t_outdoor, Q_ref_tank=max(design_load * load_ratio, 0.0), return_dict=True
        )
        assert isinstance(result, dict)
        assert result.get("failure_reason", "none") == "none", (label, result.get("failure_reason"))
        cops.append((label, float(result["cop_sys [-]"])))

    for (lo_label, lo_cop), (hi_label, hi_cop) in zip(cops, cops[1:], strict=False):
        assert hi_cop > lo_cop, f"COP fell from point {lo_label} ({lo_cop:.2f}) to {hi_label} ({hi_cop:.2f})"


#: p10-p90 of the per-model COP(D)/COP(A) of the Heat Pump Keymark population,
#: committed with its provenance (hplib commit, input checksum, extraction
#: rules) by ``validation.extraction.keymark_gradient``. The band is read from
#: this file rather than pinned in the test, so the test states the certified
#: population instead of a number that would drift from it silently. The
#: 18,106-row per-model table it comes from stays untracked (3.5 MB).
KEYMARK_GRADIENT = REPO / "validation" / "data" / "keymark_en14825_gradient.csv"
KEYMARK_GRADIENT_PROVENANCE = REPO / "validation" / "data" / "provenance" / "keymark_en14825_gradient.json"
KEYMARK_DECLARED = REPO / "validation" / "data" / "keymark_en14825_declared.csv"


def _certified_gradient_band() -> tuple[float, float]:
    """p10-p90 of COP(D)/COP(A) over the Keymark low-temperature records, read from the committed file."""
    import pandas as pd

    low = pd.read_csv(KEYMARK_GRADIENT).set_index("application").loc["low"]
    return float(low.ratio_p10), float(low.ratio_p90)


def test_committed_gradient_band_is_the_one_its_provenance_describes() -> None:
    """The quantile file is a derived artefact: it must carry its source and match the record of it."""
    import hashlib
    import json

    import pandas as pd

    record = json.loads(KEYMARK_GRADIENT_PROVENANCE.read_text(encoding="utf-8"))
    assert record["output_sha256"] == hashlib.sha256(KEYMARK_GRADIENT.read_bytes()).hexdigest(), (
        "keymark_en14825_gradient.csv was edited without rerunning validation.extraction.keymark_gradient"
    )
    assert len(record["source"]["commit"]) == 40
    assert (record["csv_input"]["files"], record["csv_input"]["bytes"]) == (2634, 78663865)

    gradient = pd.read_csv(KEYMARK_GRADIENT).set_index("application")
    summary = pd.read_csv(REPO / "validation" / "data" / "keymark_en14825_summary.csv")
    for application in ("low", "medium"):
        # The same records feed the per-point summary the documentation cites.
        n = summary[(summary.application == application) & (summary.point.isin(["A", "D"]))].n
        assert set(n) == {gradient.loc[application, "n_models"]}, application
        row = gradient.loc[application]
        assert row.ratio_min <= row.ratio_p10 < row.ratio_median < row.ratio_p90 <= row.ratio_max


@pytest.mark.skipif(not KEYMARK_DECLARED.exists(), reason="the per-model Keymark table has not been extracted here")
def test_committed_gradient_band_matches_the_per_model_table_when_it_is_present() -> None:
    import pandas as pd
    from validation.extraction.keymark_gradient import gradient_quantiles

    fresh = gradient_quantiles(pd.read_csv(KEYMARK_DECLARED)).set_index("application")
    committed = pd.read_csv(KEYMARK_GRADIENT).set_index("application")
    pd.testing.assert_frame_equal(fresh, committed, rtol=1e-9)


def test_en14825_gradient_matches_the_certified_population() -> None:
    """A→D gain must land inside what certified machines declare (p10-p90, read from the data)."""
    points = [("A", -7.0, 34.0, 0.88), ("D", 12.0, 24.0, 0.15)]
    design_load = 6000.0
    # Point D is below the minimum-speed duty. The declared COP of a part-load
    # condition belongs to the declared capacity there -- the unit running at
    # its lowest step -- and the standard applies the cycling degradation
    # afterwards, in the bin calculation. The comparison is therefore with the
    # continuous minimum-speed point (``Cd_cycling=None``), not with the mean
    # over the cycling period that ``analyze_steady`` reports by default.
    model = AirSourceHeatPumpBoiler(hp_capacity=9000.0, ref="R32", Cd_cycling=None)

    cop = {}
    for label, t_outdoor, lwt, load_ratio in points:
        result = model.analyze_steady(
            T_tank_w=lwt - 2.5, T0=t_outdoor, Q_ref_tank=design_load * load_ratio, return_dict=True
        )
        assert isinstance(result, dict)
        assert result.get("failure_reason", "none") == "none", label
        cop[label] = float(result["cop_sys [-]"])

    lo, hi = _certified_gradient_band()
    gradient = cop["D"] / cop["A"]
    assert lo <= gradient <= hi, f"COP(D)/COP(A) = {gradient:.2f}, outside the certified p10-p90 of {lo:.2f}-{hi:.2f}"


def test_outdoor_fan_default_turndown_rejects_unmatched_heat_duty() -> None:
    """The retained 15% airflow assumption must not hide HX overdelivery."""
    from tmhp.enex_functions import calc_HX_perf_for_target_heat

    row = calc_HX_perf_for_target_heat(1.0, T_a_in_C=20, T_ref_sat_K=283.15, A_cross=0.5, UA_rated=1000, dV_fan_rated=1)
    assert not row["converged"]
    assert row["min_limit"]
