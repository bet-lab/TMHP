"""Step-average and running quantities do not mix (#96 T6 follow-up, PR #110 review).

A boiler step the compressor runs only part of reports its heat, power and
refrigerant flow averaged over the step (``runtime_fraction [-]``). These tests
pin the places where averaged and running quantities used to be combined:

* the borehole loop: a solar collector's injection flows for the whole step,
  the compressor's extraction only while it runs, and the fluid temperatures
  must be continuous in the run fraction and tend to the injection-only state;
* the exergy post-processing of all three boilers: every heat-pump-circuit
  exergy flow and destruction is the full-run value times the run fraction at
  the same operating point, and the exergy balance closes on partial steps;
* ASHPB tank-side flow columns at the solved tank temperature;
* the simulation summary's running-machine statistics.
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
import pytest

import tmhp
from tmhp.ground_coupling import reset_ground_coupler
from tmhp.subsystems import SolarThermalCollector


@pytest.fixture(autouse=True)
def _quiet():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        yield


# ---------------------------------------------------------------------------
# borehole loop: solar injection vs compressor extraction
# ---------------------------------------------------------------------------

Q_RUN, Q_SOL, DT = 6000.0, 2000.0, 3600.0
FLUID = ("T_bhe_f [°C]", "T_bhe_f_in [°C]", "T_bhe_f_out [°C]")


def _ground_step(fraction: float, solar: bool, compressor: bool) -> tuple[tmhp.GSHPB_STC_ground, dict]:
    # Steady borehole: these tests pin the wall-to-fluid relations of one step
    # (T_f = T_wall - q' R_b). The dynamic borehole's first step is in
    # test_borehole_dynamics_models.py.
    m = tmhp.GSHPB_STC_ground(
        stc=SolarThermalCollector(), N_1=2, N_2=1, H_b=100.0, dt_s=DT, t_max_s=48 * 3600.0, borehole_dynamics=False
    )
    time = np.arange(2) * DT
    reset_ground_coupler(m._ground_coupler, 2, time, DT)
    m._q_solar_ground_W = Q_SOL if solar else 0.0
    ran = compressor and fraction > 0.0
    hp_result = {
        "hp_is_on": ran,
        "runtime_fraction [-]": fraction if ran else 0.0,
        "Q_bhe [W]": fraction * Q_RUN if ran else 0.0,
        "dV_bhe_f [m3/s]": m.dV_b_f_m3s,
    }
    m._compute_bhe_superposition(0, time, hp_result, ran)
    return m, hp_result


def test_solar_injection_enters_the_fluid_temperatures_with_the_compressor_off():
    """Was: a solar-only step put the fluid at the wall temperature (injection dropped)."""
    m, r = _ground_step(0.0, solar=True, compressor=False)
    q_unit = -Q_SOL / m.total_borehole_length
    assert r["T_bhe_f [°C]"] == pytest.approx(r["T_bhe [°C]"] - q_unit * m.R_b, abs=1e-12)
    assert r["T_bhe_f_in [°C]"] > r["T_bhe_f [°C]"] > r["T_bhe_f_out [°C]"]  # injection: fluid cools through the field
    assert r["Q_bhe [W]"] == pytest.approx(-Q_SOL)
    assert np.isnan(r["T_bhe_f_out_running [°C]"])


@pytest.mark.parametrize("solar", [True, False])
def test_fluid_temperatures_are_continuous_in_the_run_fraction(solar):
    """Was: the net load (extraction - injection) was divided by the run fraction, amplifying the
    injection tenfold at f = 0.1 (inlet 31.9 °C against 11.2 °C at f = 1)."""
    off = _ground_step(0.0, solar, compressor=False)[1]
    tiny = _ground_step(1e-7, solar, compressor=True)[1]
    for key in ("T_bhe [°C]", *FLUID):
        assert tiny[key] == pytest.approx(off[key], abs=1e-5), key
    # Step-time averages are linear in the run fraction between f = 0 and f = 1.
    full = _ground_step(1.0, solar, compressor=True)[1]
    tenth = _ground_step(0.1, solar, compressor=True)[1]
    for key in FLUID:
        drop_f = tenth[key] - tenth["T_bhe [°C]"]
        drop_0 = off[key] - off["T_bhe [°C]"]
        drop_1 = full[key] - full["T_bhe [°C]"]
        assert drop_f == pytest.approx(drop_0 + 0.1 * (drop_1 - drop_0), rel=1e-9, abs=1e-12), key


def test_running_fluid_state_keeps_the_injection_at_its_own_rate():
    """While the compressor runs the loop carries its running extraction minus the full injection."""
    m, both = _ground_step(0.1, solar=True, compressor=True)
    q_run_net = (Q_RUN - Q_SOL) / m.total_borehole_length
    assert both["T_bhe_f_running [°C]"] == pytest.approx(both["T_bhe [°C]"] - q_run_net * m.R_b, abs=1e-12)
    full = _ground_step(1.0, solar=True, compressor=True)[1]
    # At f = 1 the running state is the reported (average) state.
    for key in FLUID:
        assert full[key.replace(" [°C]", "_running [°C]")] == full[key]


# ---------------------------------------------------------------------------
# exergy: step averages proportional to the run fraction, balance closes
# ---------------------------------------------------------------------------

TANK_SIDE = ("X_tank", "X_mix", "Xst_tank", "Xc_tank", "Xc_mix", "X_eff")


def _model(family: str):
    if family == "ASHPB":
        return tmhp.AirSourceHeatPumpBoiler(ref="R32"), {}
    cls = tmhp.GroundSourceHeatPumpBoiler if family == "GSHPB" else tmhp.WaterSourceHeatPumpBoiler
    model = cls(dt_s=600, t_max_s=86400.0, UA_tank_hx=2000.0)
    return model, ({"T_source_w_schedule": np.full(3, 10.0)} if family == "WSHPB" else {})


def _same_point_rows(family: str, fractions=(1.0, 0.5, 0.1)) -> tuple[object, pd.DataFrame]:
    """One full-run operating point reported at several run fractions, post-processed."""
    model, extra = _model(family)
    df = model.analyze_dynamic(
        1800, 600, 30.0, np.zeros(3), np.full(3, 5.0), T_sup_w_schedule=np.full(3, 15.0), **extra
    )
    raw = [c for c in df.columns if not c.startswith("X")]
    full = df[df["runtime_fraction [-]"] == 1.0]
    assert len(full), "scenario must contain a full-run step"
    row = full.iloc[0][raw].to_dict()
    rows = []
    for f in fractions:
        r = dict(row)
        for key in model._RUNTIME_SCALED_KEYS:
            if key in r and np.isfinite(r[key]):
                r[key] = r[key] * f
        r["runtime_fraction [-]"] = f
        rows.append(r)
    return model, model.postprocess_exergy(pd.DataFrame(rows))


@pytest.mark.parametrize("family", ["ASHPB", "GSHPB", "WSHPB"])
def test_exergy_is_the_full_run_value_times_the_run_fraction(family):
    """Was (GSHPB, f = 0.1): Xc_ground 135.8 W against 0.1 x 215.3 = 21.5 W."""
    _, out = _same_point_rows(family)
    cols = [c for c in out.columns if c.startswith(("X_", "Xc_")) and not c.startswith(TANK_SIDE)]
    assert len(cols) >= 14
    for i, f in ((1, 0.5), (2, 0.1)):
        for c in cols:
            ref = float(out[c].iloc[0])
            assert float(out[c].iloc[i]) == pytest.approx(f * ref, rel=1e-9, abs=1e-9), (f, c)
    # Efficiencies (ratios of step averages) are those of the running machine.
    for c in ("X_eff_ref [-]", "X_eff_sys [-]"):
        assert out[c].iloc[2] == pytest.approx(out[c].iloc[0], rel=1e-9)


def _balance_residual(family: str, out: pd.DataFrame) -> pd.Series:
    """Heat-pump circuit exergy balance: inputs - outputs - destruction - (untracked evaporator term)."""
    if family == "ASHPB":
        inputs = out["X_cmp [W]"] + out["X_ou_fan [W]"] + out["X_a_ou_in [W]"] - out["X_a_ou_out [W]"]
        destroyed = out[["Xc_cmp [W]", "Xc_ref_tank [W]", "Xc_exp [W]", "Xc_ref_ou [W]", "Xc_ou_fan [W]"]].sum(axis=1)
        return inputs - out["X_ref_tank [W]"] - destroyed
    src = "ground" if family == "GSHPB" else "water"
    inputs = out["X_cmp [W]"] + out["E_pmp [W]"] + out["X_bhe_out [W]"] - out["X_bhe_in [W]"]
    destroyed = out[["Xc_cmp [W]", "Xc_ref_tank [W]", "Xc_exp [W]", f"Xc_{src} [W]", "Xc_pmp [W]"]].sum(axis=1)
    # Xc_<src> charges the evaporator at its saturation (Carnot) exergy; the
    # refrigerant's own gain across it is the rest of the evaporator.
    evap_rest = out[f"X_ref_{src} [W]"] - (out["X_ref_cmp_in [W]"] - out["X_ref_exp_out [W]"])
    return inputs - out["X_ref_tank [W]"] - destroyed - evap_rest


@pytest.mark.parametrize("family", ["ASHPB", "GSHPB", "WSHPB"])
def test_exergy_balance_closes_on_partial_steps(family):
    _, out = _same_point_rows(family)
    residual = _balance_residual(family, out)
    scale = out["X_cmp [W]"].abs()
    assert np.all(np.abs(residual) <= 1e-9 * scale + 1e-9), residual.tolist()


# ---------------------------------------------------------------------------
# ASHPB tank-side flows, simulation summary
# ---------------------------------------------------------------------------


def test_ashpb_tank_side_flows_use_the_solved_tank_temperature():
    # R290: the run has to reach the 65 °C set point inside a step (R32's envelope caps it at 60 °C).
    m = tmhp.AirSourceHeatPumpBoiler(ref="R290")
    n = 6
    df = m.analyze_dynamic(
        n * 600, 600, 59.0, np.full(n, 3.0 / 60000.0), np.full(n, 5.0), T_sup_w_schedule=np.full(n, 15.0)
    )
    T = df["T_tank_w [°C]"].to_numpy()
    alp = (m.T_mix_w_out - 15.0) / (T - 15.0)
    np.testing.assert_allclose(df["dV_tank_w_out [m3/s]"], alp * 3.0 / 60000.0, rtol=1e-12)
    f = df["runtime_fraction [-]"].to_numpy()
    assert ((f > 0) & (f < 1)).any()


def test_simulation_summary_reports_running_fan_values(capsys):
    from tmhp.simulation_summary import print_simulation_summary

    df = pd.DataFrame(
        {
            "converged": [True, True],
            "E_ou_fan [W]": [100.0, 25.0],
            "E_tot [W]": [1000.0, 250.0],
            "dV_ou_a [m3/s]": [1.0, 0.25],
            "cmp_rpm [rpm]": [3000.0, 3000.0],
            "runtime_fraction [-]": [1.0, 0.25],
        }
    )
    print_simulation_summary(df, 600, 1.0)
    text = capsys.readouterr().out
    assert "Min: 100.0 W | Max: 100.0 W" in text
    assert "Min: 1.000 m³/s | Max: 1.000 m³/s" in text
