"""The default compressor efficiency maps (#217, #218): archive, forms, call form and data domain.

The maps are fitted to 134 measured points of one R290 scroll (Ossorio's thesis, Appendix 1) and read the
pressure ratio, the shaft speed and -- for ``eta_v`` and ``eta_ov`` -- the evaporating temperature.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tmhp import compressor_efficiency as ce
from tmhp.compressor_efficiency import (
    COEFFICIENT_VERSION,
    MapEfficiency,
    SuctionState,
    SuctionStateRequired,
    default_efficiencies,
    efficiency_extrapolated,
    map_eta_ov,
    map_eta_v,
    map_f_loss,
)

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "validation" / "coefficients" / COEFFICIENT_VERSION


def _archive() -> dict:
    return json.loads((ARCHIVE / "coefficients.json").read_text())


def test_the_generated_block_is_the_archive():
    from validation.compressor_maps.ossorio_te.emit import block_data

    data = block_data()
    assert data["COEFFICIENT_VERSION"] == ce.COEFFICIENT_VERSION
    assert data["MAP_COEFFICIENTS"] == ce.MAP_COEFFICIENTS
    assert data["MAP_DOMAIN"] == ce.MAP_DOMAIN
    # N [rev/s] and T_e [C] enter as they are: no reference speed, no temperature scale (#247).
    assert set(data) == {"COEFFICIENT_VERSION", "MAP_COEFFICIENTS", "MAP_DOMAIN"}
    assert not hasattr(ce, "MAP_N_REF_RPS") and not hasattr(ce, "MAP_TE_SCALE_K")


def test_the_archive_refits_from_the_measured_points():
    """The archive's coefficients are the least-squares fit of its own derived points."""
    from validation.compressor_maps.ossorio_te.fit import fit

    points = pd.read_csv(ARCHIVE / "points.csv")
    assert len(points) == 134
    refit = fit(points)
    for quantity, entry in _archive()["maps"].items():
        assert refit[quantity]["coefficients"] == pytest.approx(entry["coefficients"], rel=1e-6, abs=1e-9)
        assert refit[quantity]["rmse"] == pytest.approx(entry["rmse"], rel=1e-6)


def test_the_maps_reproduce_their_points_to_the_archived_error():
    points = pd.read_csv(ARCHIVE / "points.csv")
    archive = _archive()["maps"]
    pred = {
        "eta_v": [map_eta_v(r.PR, r.N_rps, r.T_evap_C) for r in points.itertuples()],
        "eta_ov": [map_eta_ov(r.PR, r.N_rps, r.T_evap_C) for r in points.itertuples()],
        "f_loss": [map_f_loss(r.PR, r.N_rps) for r in points.itertuples()],
    }
    for quantity, values in pred.items():
        rmse = float(np.sqrt(np.mean((np.asarray(values) - points[quantity]) ** 2)))
        assert rmse == pytest.approx(archive[quantity]["rmse"], rel=1e-9)
    # the documented sizes: the evaporating-temperature term is what brings eta_ov to about 0.02
    assert archive["eta_ov"]["cv_rmse"] < 0.025 and archive["eta_v"]["cv_rmse"] < 0.01
    assert archive["f_loss"]["cv_rmse"] < 0.016


def test_points_are_derived_as_the_author_tabulated_them():
    """eta_v and the overall efficiency agree with the author's table (power = compressor + inverter)."""
    check = _archive()["check"]
    assert check["eta_v_vs_author_max_abs"] < 0.006
    assert check["eta_ov_vs_author_max_abs"] < 0.009


def test_quantities_follow_from_the_three_maps():
    models = default_efficiencies()
    state = SuctionState(268.15)  # -5 degC
    pr, rps = 3.2, 55.0
    eta_em = 1.0 - map_f_loss(pr, rps)
    assert models["eta_cmp"](pr, rps, state) == eta_em
    assert models["eta_cmp_vol"](pr, rps, state) == map_eta_v(pr, rps, -5.0)
    assert models["eta_cmp_isen"](pr, rps, state) == pytest.approx(map_eta_ov(pr, rps, -5.0) / eta_em, rel=1e-15)
    # eta_cmp_isen * eta_cmp is the overall efficiency
    assert models["eta_cmp_isen"](pr, rps, state) * models["eta_cmp"](pr, rps, state) == pytest.approx(
        map_eta_ov(pr, rps, -5.0), rel=1e-14
    )


def test_eta_ov_and_eta_v_rise_with_the_evaporating_temperature_and_f_loss_does_not_see_it():
    """At one pressure ratio and speed, the measured eta_ov is lower at a low evaporating temperature."""
    low, high = map_eta_ov(3.0, 70.0, -10.0), map_eta_ov(3.0, 70.0, 15.0)
    assert 0.07 < high - low < 0.075  # 2.5 x 0.0287
    assert map_eta_v(3.0, 70.0, 15.0) > map_eta_v(3.0, 70.0, -10.0)
    models = default_efficiencies()
    assert models["eta_cmp"](3.0, 70.0, SuctionState(263.15)) == models["eta_cmp"](3.0, 70.0, SuctionState(288.15))


def test_overall_efficiency_peaks_inside_the_data_at_every_measured_speed():
    pr = np.linspace(1.5, 10.0, 400)
    for rps in (30.0, 50.0, 70.0, 90.0, 110.0):
        values = [map_eta_ov(p, rps, 0.0) for p in pr]
        peak = pr[int(np.argmax(values))]
        assert 3.0 < peak < 4.5, (rps, peak)
        assert 0.6 < max(values) < 0.7


def test_the_defaults_are_called_with_the_suction_state():
    model = default_efficiencies()["eta_cmp_isen"]
    assert isinstance(model, MapEfficiency) and model.needs_state
    with pytest.raises(SuctionStateRequired):
        model(3.0, 60.0)
    assert ce._eval_eff(model, 3.0, 60.0, SuctionState(273.15)) == model(3.0, 60.0, SuctionState(273.15))


def test_default_models_compare_equal_by_quantity():
    a, b = default_efficiencies(), default_efficiencies()
    assert a == b
    assert len({a["eta_cmp"], b["eta_cmp"]}) == 1


def test_the_data_domain_is_the_speed_range_and_the_pr_te_hull():
    domain = default_efficiencies()["eta_cmp_vol"].validity_domain
    points = pd.read_csv(ARCHIVE / "points.csv")
    assert domain.rps_range == (30.0, 110.0)
    for r in points.itertuples():
        assert domain.contains(r.PR, r.N_rps, r.T_evap_C, rtol=1e-6), r.point_id
    model = default_efficiencies()["eta_cmp"]
    assert efficiency_extrapolated(model, 3.0, 20.0, 0.0)  # below the measured speeds
    assert efficiency_extrapolated(model, 9.0, 60.0, 10.0)  # a lift the data reach only at low T_e
    assert efficiency_extrapolated(model, 2.0, 60.0, 30.0)  # warmer than the data
    assert not efficiency_extrapolated(model, 9.0, 60.0, -28.0)
    assert not efficiency_extrapolated(0.7, 9.0, 60.0, 10.0)


@pytest.mark.parametrize("bad", [0.0, -1.0, math.nan, math.inf])
def test_an_undefined_pressure_ratio_is_an_invalid_efficiency(bad):
    with pytest.raises(ce.InvalidCompressorEfficiency):
        default_efficiencies()["eta_cmp"](bad, 60.0, SuctionState(273.15))


def test_every_family_runs_its_rated_point_inside_the_measured_speeds():
    from tmhp import (
        AirSourceHeatPump,
        AirSourceHeatPumpBoiler,
        GroundSourceHeatPump,
        GroundSourceHeatPumpBoiler,
        WaterSourceHeatPumpBoiler,
    )

    for cls in (
        AirSourceHeatPump,
        GroundSourceHeatPump,
        AirSourceHeatPumpBoiler,
        GroundSourceHeatPumpBoiler,
        WaterSourceHeatPumpBoiler,
    ):
        hp = cls()
        assert isinstance(hp.eta_cmp_isen, MapEfficiency)
        assert 30.0 <= hp.rps_rated <= 110.0, (cls.__name__, hp.rps_rated)
