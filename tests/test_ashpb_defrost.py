"""Frost and defrost on the air-source heat pump boiler (#96).

Below 5 degC outdoor the coil frosts and the unit defrosts; a rating at A2 or
A-7 is the mean over both. The boiler had no such correction and reported the
frost-free cycle (default 15 kW unit, 8 kW to a 45 degC tank: system COP 3.74
at A2, 3.52 at A0). It now applies the correction of
:class:`~tmhp.AirSourceHeatPump` (:mod:`tmhp.defrost`), on by default, in
``analyze_steady`` and in the cycle of a dynamic step. ``defrost=False``
returns what the boiler returned before, which the pinned numbers below hold.
"""

from __future__ import annotations

import math
import os
import warnings

import numpy as np
import pandas as pd
import pytest

from tmhp import ASHPB_PV_ESS, AirSourceHeatPumpBoiler, ASHPB_STC_tank
from tmhp.defrost import DefrostModel
from tmhp.subsystems import PhotovoltaicSystem, SolarThermalCollector

os.environ.setdefault("TQDM_DISABLE", "1")

FROST_KEYS = (
    "defrost_fraction [-]",
    "frost_capacity_factor [-]",
    "frost_power_factor [-]",
    "E_defrost [W]",
    "Q_defrost [W]",
    "RH_out [-]",
    "Q_ref_tank_frost_free [W]",
    "E_tot_frost_free [W]",
)

#: ``analyze_steady`` of the default boiler before the correction existed
#: (branch ``fix/ashpb-min-speed-search``, 5210553): (T0, T_tank_w, request) ->
#: (Q_ref_tank, E_cmp, E_ou_fan, E_tot, cop_sys, m_dot_ref, T_ref_evap_sat).
FROST_FREE_STEADY = {
    (2.0, 45.0, 8000.0): (8000.0, 2058.0963759653637, 83.21162190872863, 2141.3079978740925, 3.736034240726898, 0.0436970962412576, -3.186495116386652),
    (0.0, 45.0, 8000.0): (8000.0, 2176.817586285296, 93.25396578350201, 2270.0715520687977, 3.5241179921000216, 0.043236232607607186, -4.9134103007576755),
    (-7.0, 45.0, 8000.0): (8000.0, 2849.330105725291, 150.5811469998417, 2999.9112527251327, 2.666745555466922, 0.03959349552934585, -10.790197287406954),
    (2.0, 35.0, 15000.0): (15000.0, 3477.039010363018, 186.78585193481177, 3663.82486229783, 4.094082158335619, 0.07710318702173866, -6.097483129171451),
    (2.0, 35.0, 1500.0): (1500.0, 340.1046385069265, 14.399414221343127, 354.50405272826964, 4.231263333820792, 0.007864306785436105, -0.5889135593868104),
    (-15.0, 55.0, 15000.0): (9000.0, 4760.342892673035, 218.645866475615, 4978.9887591486495, 1.807595966844259, 0.038774177422600926, -17.807342529296875),
}  # fmt: skip
STEADY_KEYS = (
    "Q_ref_tank [W]",
    "E_cmp [W]",
    "E_ou_fan [W]",
    "E_tot [W]",
    "cop_sys [-]",
    "m_dot_ref [kg/s]",
    "T_ref_evap_sat [°C]",
)
#: The 6 h run of :func:`_cold_run` before the correction existed: E_tot [kWh],
#: Q_ref_tank [kWh], final and lowest tank temperature [degC], summed runtime
#: fraction, steps with the compressor on.
FROST_FREE_DYNAMIC = (
    8.884288807219031,
    18.742163179623088,
    63.75835951394356,
    46.557184756577044,
    7.496865271849234,
    9,
)


@pytest.fixture(autouse=True)
def _quiet():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        yield


def _build(cls=AirSourceHeatPumpBoiler, **kwargs):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return cls(**kwargs)


@pytest.fixture(scope="module")
def frosting() -> AirSourceHeatPumpBoiler:
    return _build()


@pytest.fixture(scope="module")
def frost_free() -> AirSourceHeatPumpBoiler:
    return _build(defrost=False)


def _cold_run(model: AirSourceHeatPumpBoiler, **kwargs) -> pd.DataFrame:
    """Six hours at -4 to +4 degC outdoor with a morning draw, 10-minute steps."""
    n = 36
    dhw = np.zeros(n)
    dhw[3:9] = 10.0 / 60000.0
    return model.analyze_dynamic(
        simulation_period_sec=n * 600,
        dt_s=600,
        T_tank_w_init_C=58.0,
        dhw_usage_schedule=dhw,
        T0_schedule=np.linspace(-4.0, 4.0, n),
        **kwargs,
    )


@pytest.fixture(scope="module")
def cold_runs(frosting, frost_free) -> tuple[pd.DataFrame, pd.DataFrame]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return _cold_run(frosting), _cold_run(frost_free)


def _summary(df: pd.DataFrame) -> tuple[float, float, float, float, float, int]:
    to_kwh = 600.0 / 3.6e6
    return (
        float(df["E_tot [W]"].sum() * to_kwh),
        float(df["Q_ref_tank [W]"].sum() * to_kwh),
        float(df["T_tank_w [°C]"].iloc[-1]),
        float(df["T_tank_w [°C]"].min()),
        float(df["runtime_fraction [-]"].sum()),
        int(df["hp_is_on"].sum()),
    )


# ---------------------------------------------------------------------------
# the switch
# ---------------------------------------------------------------------------


def test_defrost_is_on_by_default_and_takes_the_arguments_of_the_air_to_air_model():
    assert _build().defrost == DefrostModel()
    assert _build(defrost=True).defrost == DefrostModel()
    assert _build(defrost=False).defrost is None
    assert _build(defrost=None).defrost is None
    custom = DefrostModel(RH_out=0.6, transition_K=0.0)
    assert _build(defrost=custom).defrost is custom
    with pytest.raises(ValueError, match="defrost must be"):
        _build(defrost="on")


def test_derived_boilers_pass_the_argument_on():
    assert _build(ASHPB_PV_ESS, pv=PhotovoltaicSystem()).defrost == DefrostModel()
    assert _build(ASHPB_PV_ESS, pv=PhotovoltaicSystem(), defrost=False).defrost is None
    model = DefrostModel(RH_out=0.5)
    assert _build(ASHPB_STC_tank, stc=SolarThermalCollector(), defrost=model).defrost is model


@pytest.mark.parametrize("point", list(FROST_FREE_STEADY))
def test_defrost_off_is_the_steady_result_from_before(frost_free, point):
    T0, T_tank_w, request = point
    result = frost_free.analyze_steady(T_tank_w=T_tank_w, T0=T0, Q_ref_tank=request)
    for key, expected in zip(STEADY_KEYS, FROST_FREE_STEADY[point], strict=True):
        assert result[key] == pytest.approx(expected, rel=1e-6), key
    assert result["defrost_fraction [-]"] == 0.0
    assert result["frost_capacity_factor [-]"] == result["frost_power_factor [-]"] == 1.0
    assert result["E_defrost [W]"] == result["Q_defrost [W]"] == 0.0
    assert result["Q_ref_tank_frost_free [W]"] == result["Q_ref_tank [W]"]
    assert result["E_tot_frost_free [W]"] == result["E_tot [W]"]


def test_defrost_off_is_the_dynamic_result_from_before(cold_runs):
    _, off = cold_runs
    for value, expected in zip(_summary(off), FROST_FREE_DYNAMIC, strict=True):
        assert value == pytest.approx(expected, rel=1e-6)
    assert (off["defrost_fraction [-]"] == 0.0).all()
    assert (off["E_defrost [W]"] == 0.0).all() and (off["Q_defrost [W]"] == 0.0).all()


def test_nothing_changes_where_the_coil_does_not_frost(frosting, frost_free):
    """Above 5 degC the default boiler is the frost-free one, key for key."""
    point = {"T_tank_w": 45.0, "T0": 7.0, "Q_ref_tank": 8000.0}
    on, off = frosting.analyze_steady(**point), frost_free.analyze_steady(**point)
    assert on.keys() == off.keys()
    for key, value in off.items():
        if key == "RH_out [-]":
            continue
        same = on[key] == value or (isinstance(value, float) and math.isnan(value) and math.isnan(on[key]))
        assert same, key
    assert on["RH_out [-]"] == 0.84 and math.isnan(off["RH_out [-]"])
    assert on["defrost_fraction [-]"] == 0.0


# ---------------------------------------------------------------------------
# steady state under frost
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("T0", [2.0, 0.0, -7.0])
def test_frosted_row_is_the_mean_of_the_frost_free_cycle_at_the_gross_duty(frosting, frost_free, T0):
    request = 8000.0
    result = frosting.analyze_steady(T_tank_w=45.0, T0=T0, Q_ref_tank=request)
    frost = DefrostModel().frost_state(T0)
    assert frost.active and result["failure_reason"] == "none" and result["capacity_clamped"] is None
    # The keys and their values are those of the air-to-air model at this outdoor state.
    assert result["defrost_fraction [-]"] == pytest.approx(frost.defrost_fraction, rel=1e-12)
    assert result["frost_capacity_factor [-]"] == pytest.approx(frost.capacity_multiplier, rel=1e-12)
    assert result["frost_power_factor [-]"] == pytest.approx(frost.power_multiplier, rel=1e-12)
    assert result["RH_out [-]"] == 0.84
    # The request is met by the mean ...
    assert result["Q_ref_tank [W]"] == pytest.approx(request, rel=1e-9)
    assert result["Q_ref_tank_request [W]"] == request
    q_defrost = DefrostModel.defrost_load(frost, T0, frosting.hp_capacity)
    gross = (request + q_defrost) / frost.capacity_multiplier
    assert result["Q_defrost [W]"] == pytest.approx(q_defrost, rel=1e-12)
    assert result["Q_ref_tank_frost_free [W]"] == pytest.approx(gross, rel=1e-9)
    # ... of the frost-free cycle at the gross duty.
    cycle = frost_free.analyze_steady(T_tank_w=45.0, T0=T0, Q_ref_tank=gross)
    t_frac, power = frost.defrost_fraction, frost.power_multiplier
    assert result["E_tot_frost_free [W]"] == pytest.approx(cycle["E_tot [W]"], rel=1e-9)
    assert result["E_cmp [W]"] == pytest.approx(cycle["E_cmp [W]"] * (power + t_frac), rel=1e-9)
    assert result["E_defrost [W]"] == pytest.approx(cycle["E_cmp [W]"] * t_frac, rel=1e-9)
    assert result["E_ou_fan [W]"] == pytest.approx(cycle["E_ou_fan [W]"] * power, rel=1e-9)
    assert result["E_tot [W]"] == pytest.approx(result["E_cmp [W]"] + result["E_ou_fan [W]"], rel=1e-12)
    assert result["cop_sys [-]"] == pytest.approx(request / result["E_tot [W]"], rel=1e-12)
    assert result["cop_ref [-]"] == pytest.approx(request / result["E_cmp [W]"], rel=1e-12)
    assert result["PLR_delivered [-]"] == pytest.approx(request / frosting.hp_capacity, rel=1e-9)
    for key in ("T_ref_evap_sat [°C]", "T_ref_cmp_out [°C]", "cmp_rpm [rpm]", "eta_cmp_isen [-]"):
        assert result[key] == pytest.approx(cycle[key], rel=1e-9), key
    # Heat and flow rates are scaled together: the refrigerant balance of the row closes.
    work = result["m_dot_ref [kg/s]"] * (result["h_ref_cmp_out [J/kg]"] - result["h_ref_cmp_in [J/kg]"])
    assert result["Q_ref_tank [W]"] - result["Q_ref_ou [W]"] == pytest.approx(work, rel=1e-9)
    # Frost costs efficiency: less than the frost-free row at the same request.
    assert result["cop_sys [-]"] < 0.9 * FROST_FREE_STEADY[(T0, 45.0, request)][4]


def test_correction_grows_from_nothing_at_five_degrees(frosting):
    """No step at the limit: the COP is continuous at 5 degC and falls to the full correction at 2 degC."""
    cops = {
        T0: frosting.analyze_steady(T_tank_w=45.0, T0=T0, Q_ref_tank=8000.0)["cop_sys [-]"]
        for T0 in (5.2, 5.0, 4.8, 4.0, 3.0, 2.0)
    }
    assert cops[5.0] == pytest.approx(cops[5.2], rel=0.01)  # 0.2 K of outdoor temperature, no frost yet
    assert cops[4.8] == pytest.approx(cops[5.0], rel=0.03)
    ordered = [cops[T0] for T0 in (5.0, 4.8, 4.0, 3.0, 2.0)]
    assert all(a > b for a, b in zip(ordered, ordered[1:], strict=False))
    assert cops[2.0] < 0.8 * cops[5.0]


def test_outdoor_humidity_is_an_optional_input(frosting):
    point = {"T_tank_w": 45.0, "T0": 0.0, "Q_ref_tank": 8000.0}
    default = frosting.analyze_steady(**point)
    dry = frosting.analyze_steady(**point, RH_out=0.4)
    humid = frosting.analyze_steady(**point, RH_out=1.0)
    assert dry["RH_out [-]"] == 0.4 and humid["RH_out [-]"] == 1.0
    assert dry["defrost_fraction [-]"] < default["defrost_fraction [-]"] < humid["defrost_fraction [-]"]
    assert dry["cop_sys [-]"] > default["cop_sys [-]"] > humid["cop_sys [-]"]
    with pytest.raises(ValueError, match="relative humidity"):
        frosting.analyze_steady(**point, RH_out=84.0)
    frame = frosting.analyze_steady(**point, return_dict=False)
    assert isinstance(frame, pd.DataFrame) and frame["defrost_fraction [-]"].iloc[0] == default["defrost_fraction [-]"]


def test_request_the_unit_cannot_make_up_under_frost_is_delivered_short(frosting, frost_free):
    """A2/W35 at the nameplate: the compressor is at its speed ceiling before the mean reaches 15 kW."""
    result = frosting.analyze_steady(T_tank_w=35.0, T0=2.0, Q_ref_tank=15000.0)
    assert result["failure_reason"] == "none" and result["capacity_clamped"] == "max"
    assert result["cmp_rpm [rpm]"] == pytest.approx(60.0 * frosting.rps_max, rel=1e-9)
    assert result["Q_ref_tank_request [W]"] == 15000.0
    delivered = result["frost_capacity_factor [-]"] * result["Q_ref_tank_frost_free [W]"] - result["Q_defrost [W]"]
    assert result["Q_ref_tank [W]"] == pytest.approx(delivered, rel=1e-12)
    assert 0.9 * 15000.0 < result["Q_ref_tank [W]"] < 15000.0 * (1.0 - 0.01)
    # Frost-free, the same request is met.
    assert frost_free.analyze_steady(T_tank_w=35.0, T0=2.0, Q_ref_tank=15000.0)["capacity_clamped"] is None


def test_envelope_ceiling_under_frost_reports_the_mean_it_delivers(frosting, frost_free):
    """A-15/W55: the pressure-ratio ceiling sets the duty, frost lowers what reaches the tank."""
    limited = frost_free.analyze_steady(T_tank_w=55.0, T0=-15.0, Q_ref_tank=15000.0)
    assert limited["capacity_clamped"] == "max" and limited["capacity_binding"] == "pressure_ratio"
    result = frosting.analyze_steady(T_tank_w=55.0, T0=-15.0, Q_ref_tank=15000.0)
    assert result["capacity_clamped"] == "max" and result["capacity_binding"] == "pressure_ratio"
    assert result["Q_request [W]"] == 15000.0 and result["Q_ref_tank_request [W]"] == 15000.0
    assert result["Q_max [W]"] == result["Q_ref_tank [W]"]
    assert result["Q_ref_tank_frost_free [W]"] == pytest.approx(limited["Q_ref_tank [W]"], rel=2e-3)
    assert result["Q_ref_tank [W]"] == pytest.approx(
        result["frost_capacity_factor [-]"] * result["Q_ref_tank_frost_free [W]"] - result["Q_defrost [W]"], rel=1e-12
    )
    assert result["Q_ref_tank [W]"] < 0.9 * limited["Q_ref_tank [W]"]


def test_cycling_unit_defrosts_only_while_it_runs(frosting):
    """Below the minimum capacity: the request is met, and the defrost loss follows the run time."""
    T0 = 2.0
    result = frosting.analyze_steady(T_tank_w=35.0, T0=T0, Q_ref_tank=1500.0)
    assert result["failure_reason"] == "none" and result["capacity_clamped"] == "min"
    assert result["Q_ref_tank [W]"] == pytest.approx(1500.0, rel=1e-9)
    frost = DefrostModel().frost_state(T0)
    continuous = DefrostModel.defrost_load(frost, T0, frosting.hp_capacity)
    runtime = result["runtime_fraction [-]"]
    assert 0.0 < runtime < 1.0
    assert runtime == pytest.approx(result["Q_ref_tank_frost_free [W]"] / result["Q_min [W]"], rel=1e-9)
    assert result["Q_defrost [W]"] == pytest.approx(runtime * continuous, rel=1e-9)
    assert result["Q_ref_tank [W]"] == pytest.approx(
        frost.capacity_multiplier * result["Q_ref_tank_frost_free [W]"] - result["Q_defrost [W]"], rel=1e-9
    )
    # A vanishing request pays a vanishing defrost loss, not the continuous one.
    small = frosting.analyze_steady(T_tank_w=35.0, T0=T0, Q_ref_tank=15.0)
    assert small["Q_ref_tank [W]"] == pytest.approx(15.0, rel=1e-9)
    assert small["Q_defrost [W]"] < 0.02 * continuous


def test_continuous_floor_row_under_frost_over_delivers_the_mean():
    model = _build(Cd_cycling=None)
    result = model.analyze_steady(T_tank_w=35.0, T0=2.0, Q_ref_tank=1500.0)
    assert result["capacity_clamped"] == "min" and result["runtime_fraction [-]"] == 1.0
    assert result["Q_ref_tank [W]"] > 1500.0
    assert result["Q_ref_tank [W]"] == pytest.approx(
        result["frost_capacity_factor [-]"] * result["Q_ref_tank_frost_free [W]"] - result["Q_defrost [W]"], rel=1e-12
    )


def test_rows_that_do_not_run_carry_neutral_frost_keys(frosting):
    off = frosting.analyze_steady(T_tank_w=45.0, T0=0.0, Q_ref_tank=0.0)
    refused = _build(ref="R32", hp_capacity=8000.0).analyze_steady(T_tank_w=55.0, T0=-7.0, Q_ref_tank=8000.0)
    assert refused["hp_is_on"] is False and refused["failure_reason"] != "none"
    for row in (off, refused):
        assert all(key in row for key in FROST_KEYS)
        assert row["defrost_fraction [-]"] == 0.0 and row["E_defrost [W]"] == 0.0 and row["Q_defrost [W]"] == 0.0
        assert row["frost_capacity_factor [-]"] == 1.0 and row["frost_power_factor [-]"] == 1.0
        assert row["Q_ref_tank_frost_free [W]"] == 0.0 and row["E_tot_frost_free [W]"] == 0.0


def test_undecorated_solve_is_still_reachable(frosting):
    """``analyze_steady.__wrapped__`` is one frost-free solve, as before."""
    raw = AirSourceHeatPumpBoiler.analyze_steady.__wrapped__(frosting, 45.0, 0.0, 8000.0)
    assert raw["Q_ref_tank [W]"] == pytest.approx(8000.0, rel=1e-9)
    assert raw["cop_sys [-]"] == pytest.approx(FROST_FREE_STEADY[(0.0, 45.0, 8000.0)][4], rel=1e-6)
    assert "defrost_fraction [-]" not in raw


# ---------------------------------------------------------------------------
# dynamic run under frost
# ---------------------------------------------------------------------------


def test_dynamic_run_reports_the_mean_over_frost_and_defrost(cold_runs):
    on, off = cold_runs
    assert all(key in on.columns for key in FROST_KEYS)
    assert set(on.columns) == set(off.columns)
    running = on[on["hp_is_on"]]
    resting = on[~on["hp_is_on"]]
    assert len(running) >= 6 and len(resting) >= 6
    # Every running step is below 5 degC here: all of them frost.
    assert (running["defrost_fraction [-]"] > 0.0).all() and (running["frost_capacity_factor [-]"] < 1.0).all()
    assert (running["E_defrost [W]"] > 0.0).all() and (running["Q_defrost [W]"] > 0.0).all()
    assert (resting["defrost_fraction [-]"] == 0.0).all() and (resting["E_defrost [W]"] == 0.0).all()
    assert (resting["Q_ref_tank_frost_free [W]"] == 0.0).all()
    # Step averages: heat and power of the frost-free cycle scale with the run time like the delivered ones.
    full = running[running["runtime_fraction [-]"] == 1.0]
    part = running[running["runtime_fraction [-]"] < 1.0]
    assert len(full) >= 2 and len(part) >= 1
    for frame in (full, part):
        delivered = frame["frost_capacity_factor [-]"] * frame["Q_ref_tank_frost_free [W]"] - frame["Q_defrost [W]"]
        np.testing.assert_allclose(frame["Q_ref_tank [W]"], delivered, rtol=1e-9)
        np.testing.assert_allclose(frame["cop_sys [-]"], frame["Q_ref_tank [W]"] / frame["E_tot [W]"], rtol=1e-9)
    # The thermostat asks for the nameplate and gets it as the mean, or less at the ceiling.
    assert (full["Q_ref_tank [W]"] <= 15000.0 * (1.0 + 1e-9)).all()
    # The same water is heated for more electricity.
    e_on, q_on, *_ = _summary(on)
    e_off, q_off, *_ = _summary(off)
    assert q_on == pytest.approx(q_off, rel=0.05)
    assert q_on / e_on < 0.92 * (q_off / e_off)


def test_dynamic_humidity_schedule_and_step_input(frosting):
    n = 6
    schedule = {
        "simulation_period_sec": n * 600,
        "dt_s": 600,
        "T_tank_w_init_C": 55.0,
        "dhw_usage_schedule": np.zeros(n),
        "T0_schedule": np.full(n, 0.0),
    }
    default = frosting.analyze_dynamic(**schedule)
    dry = frosting.analyze_dynamic(**schedule, RH_out_schedule=np.full(n, 0.4))
    assert default["hp_is_on"].iloc[0] and dry["hp_is_on"].iloc[0]
    assert dry["RH_out [-]"].iloc[0] == 0.4 and default["RH_out [-]"].iloc[0] == 0.84
    assert dry["defrost_fraction [-]"].iloc[0] < default["defrost_fraction [-]"].iloc[0]
    assert dry["E_tot [W]"].sum() < default["E_tot [W]"].sum()
    with pytest.raises(ValueError, match="RH_out_schedule length"):
        frosting.analyze_dynamic(**schedule, RH_out_schedule=np.full(n + 1, 0.4))
    # The step kernel takes the humidity as an optional input and forgets it on the next call.
    inputs = {"n": 0, "current_time_s": 0.0, "T0": 0.0, "dV_mix_w_out": 0.0, "T_sup_w": 15.0, "T_sur": 20.0}
    state = frosting.make_initial_state(55.0)
    _, with_humidity = frosting.step(state, {**inputs, "RH_out": 0.4}, 600.0)
    _, without = frosting.step(state, inputs, 600.0)
    assert with_humidity["RH_out [-]"] == 0.4 and without["RH_out [-]"] == 0.84
    assert without["defrost_fraction [-]"] == pytest.approx(default["defrost_fraction [-]"].iloc[0], rel=1e-12)


def test_warm_day_is_untouched_by_the_default(frosting, frost_free):
    n = 6
    schedule = {
        "simulation_period_sec": n * 600,
        "dt_s": 600,
        "T_tank_w_init_C": 55.0,
        "dhw_usage_schedule": np.zeros(n),
        "T0_schedule": np.full(n, 8.0),
    }
    on, off = frosting.analyze_dynamic(**schedule), frost_free.analyze_dynamic(**schedule)
    shared = [c for c in off.columns if c != "RH_out [-]"]
    pd.testing.assert_frame_equal(on[shared], off[shared], check_exact=True)


# ---------------------------------------------------------------------------
# every row carries the frost keys
# ---------------------------------------------------------------------------


def test_every_steady_row_has_the_same_keys_with_and_without_frost(frosting):
    """Running, frosting, cycling, capacity-limited, off and refused rows: one key set."""
    rows = {
        "frost-free": frosting.analyze_steady(T_tank_w=45.0, T0=7.0, Q_ref_tank=8000.0),
        "frosting": frosting.analyze_steady(T_tank_w=45.0, T0=0.0, Q_ref_tank=8000.0),
        "cycling": frosting.analyze_steady(T_tank_w=35.0, T0=2.0, Q_ref_tank=1500.0),
        "speed ceiling": frosting.analyze_steady(T_tank_w=35.0, T0=2.0, Q_ref_tank=15000.0),
        "reduced": frosting.analyze_steady(T_tank_w=55.0, T0=-15.0, Q_ref_tank=15000.0),
        "off": frosting.analyze_steady(T_tank_w=45.0, T0=0.0, Q_ref_tank=0.0),
        "refused": frosting.analyze_steady(T_tank_w=55.0, T0=-7.0, Q_ref_tank=1500.0),
    }
    assert rows["refused"]["hp_is_on"] is False and rows["refused"]["failure_reason"] != "none"
    keys = set(rows["frost-free"])
    for name, row in rows.items():
        assert set(row) == keys, (name, set(row) ^ keys)
        for key in FROST_KEYS:
            # The humidity is NaN where nothing is asked of the unit: no correction, no humidity used.
            expect_number = key != "RH_out [-]" or name != "off"
            assert isinstance(row[key], float) and math.isfinite(row[key]) is expect_number, (name, key)
    # A capacity-limited frosting row reports what it delivers, against what was asked.
    for name in ("speed ceiling", "reduced"):
        row = rows[name]
        assert row["capacity_clamped"] == "max" and row["capacity_binding"] is not None
        assert row["Q_max [W]"] == row["Q_ref_tank [W]"] < row["Q_request [W]"] == 15000.0


def test_every_dynamic_row_has_the_frost_keys(cold_runs):
    on, off = cold_runs
    numeric = [key for key in FROST_KEYS if key != "RH_out [-]"]
    assert not on[list(FROST_KEYS)].isna().any().any()
    assert not off[numeric].isna().any().any()
    assert off["RH_out [-]"].isna().all()  # no frost model, no humidity used
    # A step the thermostat called carries the nameplate request, frost or not.
    called = on[on["hp_is_on"]]
    assert (called["Q_request [W]"] == 15000.0).all() and (called["Q_ref_tank_request [W]"] == 15000.0).all()
    limited = called[called["capacity_clamped"] == "max"]
    running_heat = limited["Q_ref_tank [W]"] / limited["runtime_fraction [-]"]
    np.testing.assert_allclose(limited["Q_max [W]"], running_heat, rtol=1e-9)
