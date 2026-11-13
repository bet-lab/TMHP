"""Hot-water service accounting of the boiler models (#96 T7).

A draw schedule is a request. Once the tank is colder than the service set
point the mixing valve passes tank water unmixed and the water arrives cold;
the boilers used to report that only as a lower ``T_mix_w_out [°C]`` (GSHPB,
dt 60 s, 24 h: 100 draw steps below 40 °C, 6.3 kWh of 41.5 kWh, and nothing
in the result said so). Every boiler and combination now reports
``Q_dhw_request [W]`` and ``Q_dhw_unmet [W]`` per step, warns once at the end
of a run that left demand unmet, and ``summarize_dhw_service`` gives the totals.

The runs here keep the heat pump off (no cycle is solved), so the file takes
a few seconds.
"""

from __future__ import annotations

import math
import warnings

import numpy as np
import pandas as pd
import pytest

import tmhp
from tmhp.constants import c_w, rho_w
from tmhp.dhw import (
    DHW_REQUEST_KEY,
    DHW_SETPOINT_TOL_K,
    DHW_UNMET_KEY,
    DHWUnmetWarning,
    dhw_service_columns,
    summarize_dhw_service,
    warn_if_dhw_unmet,
)
from tmhp.simulation_summary import print_dhw_service_summary, print_simulation_summary
from tmhp.subsystems import PhotovoltaicSystem, SolarThermalCollector

RC = c_w * rho_w
OFF = [(0.0, 0.0)]  # heat pump never scheduled
T_SUP, T_SET = 15.0, 40.0
DT = 600
N_COLD = 8  # steps of the run that drains the tank's heat

BASE = {
    "ASHPB": tmhp.AirSourceHeatPumpBoiler,
    "GSHPB": tmhp.GroundSourceHeatPumpBoiler,
    "WSHPB": tmhp.WaterSourceHeatPumpBoiler,
}
COMBINED = {
    "ASHPB_STC_preheat": "stc",
    "ASHPB_STC_tank": "stc",
    "ASHPB_PV_ESS": "pv",
    "GSHPB_STC_preheat": "stc",
    "GSHPB_STC_tank": "stc",
    "GSHPB_STC_ground": "stc",
    "GSHPB_STC_routed": "stc",
    "GSHPB_PV_ESS": "pv",
}


def _build(name: str, **kw):
    """A boiler or combination with the heat pump switched off."""
    kw.setdefault("hp_on_schedule", OFF)
    if "GSHPB" in name or "WSHPB" in name:
        kw.update(dt_s=DT, t_max_s=86400.0)
    if name in COMBINED:
        kw[COMBINED[name]] = SolarThermalCollector() if COMBINED[name] == "stc" else PhotovoltaicSystem()
        return getattr(tmhp, name)(**kw)
    return BASE[name](**kw)


def _run(model, n: int, T_init: float, draw_lpm: float, **kw) -> tuple[pd.DataFrame, np.ndarray, list]:
    """``n`` steps of a constant mixed-water draw; returns the frame, the demand and the DHW warnings."""
    demand = np.full(n, draw_lpm / 60000.0)
    extra = {"T_source_w_schedule": np.full(n, 10.0)} if isinstance(model, tmhp.WaterSourceHeatPumpBoiler) else {}
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        df = model.analyze_dynamic(
            n * DT,
            DT,
            T_init,
            demand,
            np.full(n, 5.0),
            T_sup_w_schedule=np.full(n, T_SUP),
            T_sur_schedule=np.full(n, 20.0),
            **extra,
            **kw,
        )
    return df, demand, [w for w in caught if issubclass(w.category, DHWUnmetWarning)]


def _col(df: pd.DataFrame, key: str) -> np.ndarray:
    return np.nan_to_num(pd.to_numeric(df[key], errors="coerce").to_numpy(dtype=float))


# ---------------------------------------------------------------------------
# the accounting itself
# ---------------------------------------------------------------------------


def test_delivery_at_the_set_point_leaves_nothing_unmet():
    out = dhw_service_columns(1e-4, 1e-4, 313.15, 313.15, 288.15)
    assert out[DHW_REQUEST_KEY] == pytest.approx(RC * 1e-4 * 25.0, rel=1e-12)
    assert out[DHW_UNMET_KEY] == 0.0


def test_cold_delivery_is_short_by_the_temperature_it_misses():
    out = dhw_service_columns(1e-4, 1e-4, 303.15, 313.15, 288.15)  # 30 °C instead of 40 °C
    assert out[DHW_UNMET_KEY] == pytest.approx(RC * 1e-4 * 10.0, rel=1e-12)
    assert out[DHW_UNMET_KEY] / out[DHW_REQUEST_KEY] == pytest.approx(0.4, rel=1e-12)


def test_a_cut_draw_is_short_by_the_water_it_misses():
    out = dhw_service_columns(1e-4, 0.25e-4, 313.15, 313.15, 288.15)
    assert out[DHW_UNMET_KEY] == pytest.approx(0.75 * out[DHW_REQUEST_KEY], rel=1e-12)
    # Nothing delivered at all (the delivered temperature is then undefined).
    none = dhw_service_columns(1e-4, 0.0, math.nan, 313.15, 288.15)
    assert none[DHW_UNMET_KEY] == none[DHW_REQUEST_KEY] > 0.0


def test_a_shortfall_within_the_set_point_tolerance_counts_as_met():
    inside = dhw_service_columns(1e-4, 1e-4, 313.15 - 0.5 * DHW_SETPOINT_TOL_K, 313.15, 288.15)
    outside = dhw_service_columns(1e-4, 1e-4, 313.15 - 3.0 * DHW_SETPOINT_TOL_K, 313.15, 288.15)
    assert inside[DHW_UNMET_KEY] == 0.0
    assert outside[DHW_UNMET_KEY] == pytest.approx(RC * 1e-4 * 3.0 * DHW_SETPOINT_TOL_K, rel=1e-6)


def test_no_draw_and_warm_mains_request_nothing():
    assert dhw_service_columns(0.0, 0.0, math.nan, 313.15, 288.15) == {DHW_REQUEST_KEY: 0.0, DHW_UNMET_KEY: 0.0}
    # Mains water already above the set point: nothing to heat, nothing unmet.
    assert dhw_service_columns(1e-4, 1e-4, 318.15, 313.15, 318.15) == {DHW_REQUEST_KEY: 0.0, DHW_UNMET_KEY: 0.0}
    # Hotter than the set point is not credited against the request.
    assert dhw_service_columns(1e-4, 1e-4, 323.15, 313.15, 288.15)[DHW_UNMET_KEY] == 0.0


def test_summary_totals_and_missing_columns():
    df = pd.DataFrame(
        {
            DHW_REQUEST_KEY: [0.0, 1000.0, 1000.0, 2000.0],
            DHW_UNMET_KEY: [0.0, 0.0, 250.0, 2000.0],
            "T_mix_w_out [°C]": [40.0, 40.0, 33.75, np.nan],
        }
    )
    s = summarize_dhw_service(df, 900)
    assert s["Q_dhw_request [kWh]"] == pytest.approx(1.0)
    assert s["Q_dhw_unmet [kWh]"] == pytest.approx(0.5625)
    assert s["Q_dhw_delivered [kWh]"] == pytest.approx(0.4375)
    assert s["dhw_unmet_fraction [-]"] == pytest.approx(0.5625)
    assert (s["dhw_draw_steps [-]"], s["dhw_unmet_steps [-]"]) == (3, 2)
    assert s["T_mix_w_out_min [°C]"] == pytest.approx(33.75)
    with pytest.raises(KeyError, match="Q_dhw_request"):
        summarize_dhw_service(pd.DataFrame({"E_cmp [W]": [1.0]}), 900)
    # No draw at all: a zero request has no unmet fraction and no coldest delivery.
    idle = summarize_dhw_service(pd.DataFrame({DHW_REQUEST_KEY: [0.0], DHW_UNMET_KEY: [0.0]}), 900)
    assert idle["dhw_unmet_fraction [-]"] == 0.0 and math.isnan(idle["T_mix_w_out_min [°C]"])


def test_warning_is_silent_without_a_shortfall_or_without_the_columns():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        warn_if_dhw_unmet(pd.DataFrame({DHW_REQUEST_KEY: [500.0], DHW_UNMET_KEY: [0.0]}), 600, "X", 40.0)
        warn_if_dhw_unmet(pd.DataFrame({"E_cmp [W]": [1.0]}), 600, "X", 40.0)
    with pytest.warns(DHWUnmetWarning, match=r"1 of 1 draw steps: 0\.05 kWh of 0\.08 kWh"):
        warn_if_dhw_unmet(pd.DataFrame({DHW_REQUEST_KEY: [500.0], DHW_UNMET_KEY: [300.0]}), 600, "X", 40.0)


def test_summary_printout(capsys):
    df = pd.DataFrame(
        {
            "converged": [True, True],
            "E_ou_fan [W]": [100.0, 25.0],
            "E_tot [W]": [1000.0, 250.0],
            "dV_ou_a [m3/s]": [1.0, 0.25],
            "cmp_rpm [rpm]": [3000.0, 3000.0],
            DHW_REQUEST_KEY: [6000.0, 6000.0],
            DHW_UNMET_KEY: [0.0, 1500.0],
            "T_mix_w_out [°C]": [40.0, 33.75],
        }
    )
    print_simulation_summary(df, 600, 1.0)
    text = capsys.readouterr().out
    assert "[Hot Water Service]" in text
    assert "Requested: 2.00 kWh | Delivered: 1.75 kWh" in text
    assert "Unmet: 0.25 kWh (12.5%) on 1 of 2 draw steps" in text
    assert "Coldest delivery: 33.8 °C" in text
    # A frame without the hot-water columns prints the summary as before, and the section alone is silent.
    print_simulation_summary(df.drop(columns=[DHW_REQUEST_KEY, DHW_UNMET_KEY]), 600, 1.0)
    assert "[Hot Water Service]" not in capsys.readouterr().out
    print_dhw_service_summary(df.drop(columns=[DHW_REQUEST_KEY, DHW_UNMET_KEY]), 600)
    assert capsys.readouterr().out == ""


# ---------------------------------------------------------------------------
# the boilers report it
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", params=list(BASE))
def cold_run(request):
    """Heat pump off, 55 °C tank, 4 L/min for 80 min: the tank falls through the 40 °C set point."""
    model = _build(request.param)
    df, demand, dhw_warnings = _run(model, N_COLD, 55.0, 4.0)
    return request.param, df, demand, dhw_warnings


def test_request_is_the_scheduled_draw_at_the_set_point(cold_run):
    _, df, demand, _ = cold_run
    np.testing.assert_allclose(_col(df, DHW_REQUEST_KEY), RC * demand * (T_SET - T_SUP), rtol=1e-12)


def test_unmet_is_the_heat_missing_from_a_cold_delivery(cold_run):
    family, df, demand, _ = cold_run
    T_mix = pd.to_numeric(df["T_mix_w_out [°C]"], errors="coerce").to_numpy(dtype=float)
    unmet = _col(df, DHW_UNMET_KEY)
    cold = T_mix < T_SET - 0.1
    assert cold.sum() >= 3, f"{family}: scenario must run the tank below the set point ({T_mix.round(1)})"
    assert (~cold).any(), f"{family}: scenario must also deliver at the set point"
    np.testing.assert_allclose(unmet[cold], RC * demand[cold] * (T_SET - T_mix[cold]), rtol=1e-9)
    assert np.all(unmet[T_mix >= T_SET - 1e-6] == 0.0)
    assert np.all((unmet >= 0.0) & (unmet <= _col(df, DHW_REQUEST_KEY) * (1.0 + 1e-12)))


def test_run_ends_with_one_warning_and_the_summary_matches(cold_run):
    family, df, _, dhw_warnings = cold_run
    assert len(dhw_warnings) == 1, [str(w.message) for w in dhw_warnings]
    s = summarize_dhw_service(df, DT)
    assert s["Q_dhw_unmet [kWh]"] == pytest.approx(_col(df, DHW_UNMET_KEY).sum() * DT / 3.6e6, rel=1e-12)
    assert s["dhw_unmet_steps [-]"] == int((_col(df, DHW_UNMET_KEY) > 0.0).sum()) >= 3
    assert 0.0 < s["dhw_unmet_fraction [-]"] < 1.0
    assert s["T_mix_w_out_min [°C]"] < T_SET - 1.0
    text = str(dhw_warnings[0].message)
    assert f"{s['dhw_unmet_steps [-]']} of {N_COLD} draw steps" in text and f"{s['Q_dhw_unmet [kWh]']:.2f} kWh" in text


@pytest.mark.parametrize("family", list(BASE))
def test_a_hot_tank_meets_the_demand_without_a_warning(family):
    df, demand, dhw_warnings = _run(_build(family), 2, 60.0, 3.0)
    assert dhw_warnings == []
    assert np.all(_col(df, DHW_UNMET_KEY) == 0.0)
    assert _col(df, DHW_REQUEST_KEY).sum() > 0.0
    # No draw: the columns are there and zero.
    idle, _, dhw_warnings = _run(_build(family), 2, 60.0, 0.0)
    assert dhw_warnings == []
    assert np.all(_col(idle, DHW_REQUEST_KEY) == 0.0) and np.all(_col(idle, DHW_UNMET_KEY) == 0.0)
    assert idle[DHW_REQUEST_KEY].notna().all() and idle[DHW_UNMET_KEY].notna().all()


@pytest.mark.parametrize("name", list(COMBINED))
def test_combinations_report_the_same_keys(name):
    df, demand, dhw_warnings = _run(_build(name), 2, 32.0, 6.0)
    assert len(dhw_warnings) == 1
    np.testing.assert_allclose(_col(df, DHW_REQUEST_KEY), RC * demand * (T_SET - T_SUP), rtol=1e-12)
    T_mix = pd.to_numeric(df["T_mix_w_out [°C]"], errors="coerce").to_numpy(dtype=float)
    np.testing.assert_allclose(_col(df, DHW_UNMET_KEY), RC * demand * (T_SET - T_mix), rtol=1e-9)


@pytest.mark.parametrize("tank_model", ["stratified", "hybrid"])
def test_multi_node_tank_reports_the_water_that_left_the_top(tank_model):
    """The delivered temperature is the tank outlet's, not the tank mean's."""
    model = _build("GSHPB", tank_model=tank_model)
    df, demand, dhw_warnings = _run(model, 6, 45.0, 6.0)
    T_mix = pd.to_numeric(df["T_mix_w_out [°C]"], errors="coerce").to_numpy(dtype=float)
    unmet = _col(df, DHW_UNMET_KEY)
    cold = T_mix < T_SET - 0.1
    assert cold.any() and len(dhw_warnings) == 1
    np.testing.assert_allclose(unmet[cold], RC * demand[cold] * (T_SET - T_mix[cold]), rtol=1e-9)
    # A valve settled on the outlet within its iteration tolerance is at the set point.
    assert np.all(unmet[np.abs(T_mix - T_SET) < DHW_SETPOINT_TOL_K] == 0.0)


def test_level_varying_tank_counts_the_water_it_could_not_supply():
    """ASHPB drained to its floor: the cut draw is unmet heat at the full set-point lift."""
    model = _build("ASHPB", tank_always_full=False, tank_level_lower_bound=0.2, dV_tank_w_in_refill=0.0001)
    df, demand, dhw_warnings = _run(model, 6, 60.0, 15.0)
    cut = _col(df, "dV_mix_w_out_unmet [m3/s]")
    delivered = _col(df, "dV_mix_w_out [m3/s]")
    T_mix = pd.to_numeric(df["T_mix_w_out [°C]"], errors="coerce").to_numpy(dtype=float)
    assert cut.sum() > 0.0 and len(dhw_warnings) == 1
    got = RC * delivered * (np.where(delivered > 0.0, np.minimum(T_mix, T_SET), T_SET) - T_SUP)
    np.testing.assert_allclose(_col(df, DHW_UNMET_KEY), RC * demand * (T_SET - T_SUP) - got, rtol=1e-9, atol=1e-6)
    # At least the heat of the water that never arrived.
    assert np.all(_col(df, DHW_UNMET_KEY) >= RC * cut * (T_SET - T_SUP) * (1.0 - 1e-9))
