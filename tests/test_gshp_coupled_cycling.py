"""On/off cycling in the coupled (prescribed or variable ground flow) solver of ``GroundSourceHeatPump``.

The fixed-flow solver reports a request below the minimum-speed duty as the
mean of on/off cycling (``tests/test_gshp_cycling.py``). The coupled solver --
taken whenever a ground flow is prescribed or ``ground_flow_control`` is not
plain constant flow -- answered the same request with the failure
``compressor_min_speed`` (heating) or ``ground_hx_capacity_insufficient``
(cooling): no heat at all, where the same unit at the same flow delivers the
request by cycling.

Now the minimum-speed machine of the coupled solver is the one the fixed-flow
search finds at that flow, averaged the same way, and the flow selection ranks
it on the mean input it draws for the request.
"""

import warnings

import numpy as np
import pytest

from tmhp import GroundSourceHeatPump
from tmhp.cycling import CAPACITY_RATIO_KEY, MIN_CAPACITY_KEY, PART_LOAD_FACTOR_KEY, RUNTIME_FRACTION_KEY
from tmhp.g_function import g_step_mean

HEATING = dict(Q_r_iu=-1000.0, T0=0.0, T_a_room=20.0, T_bhe_wall=0.0)
COOLING = dict(Q_r_iu=1000.0, T0=30.0, T_a_room=27.0, T_bhe_wall=25.0)

#: Keys of a row that say what the machine does (the coupled solver adds bookkeeping keys of its own).
MACHINE_KEYS = (
    "Q_ref_iu [W]",
    "Q_ref_ground [W]",
    "Q_bhe [W]",
    "E_cmp [W]",
    "E_pmp [W]",
    "E_iu_fan [W]",
    "E_tot [W]",
    "cop_ref [-]",
    "cop_sys [-]",
    "cmp_rpm [rpm]",
    "m_dot_ref [kg/s]",
    "dV_iu_a [m3/s]",
    "dV_bhe_f [m3/s]",
    "T_ref_evap_sat [°C]",
    "T_ref_cond_sat_l [°C]",
    "T_bhe_f_in [°C]",
    "T_bhe_f_out [°C]",
    "indoor_approach [K]",
    "ground_approach [K]",
    CAPACITY_RATIO_KEY,
    PART_LOAD_FACTOR_KEY,
    RUNTIME_FRACTION_KEY,
    MIN_CAPACITY_KEY,
    "E_cycling_loss [W]",
    "Q_request [W]",
    "Q_unmet [W]",
)


@pytest.fixture(scope="module")
def gshp():
    return GroundSourceHeatPump()


@pytest.fixture(scope="module")
def gshp_optimal():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return GroundSourceHeatPump(ground_flow_control="optimal_power")


@pytest.mark.parametrize("point", [HEATING, COOLING], ids=["heating", "cooling"])
def test_prescribed_reference_flow_cycles_like_the_fixed_flow_solver(gshp, point):
    """Same unit, same flow, same wall: the coupled solver returns the fixed-flow solver's mean row."""
    fixed = gshp.analyze_steady(**point)
    coupled = gshp.analyze_steady(**point, ground_flow_lpm=gshp.ground_flow_ref_lpm)
    assert fixed["capacity_clamped"] == "min" and 0.0 < fixed[CAPACITY_RATIO_KEY] < 1.0
    assert coupled["converged"] and coupled["hp_is_on"] and coupled["failure_reason"] == "none"
    assert coupled["capacity_clamped"] == "min"
    assert coupled["Q_ref_iu [W]"] == pytest.approx(1000.0, rel=1e-12)
    for key in MACHINE_KEYS:
        assert coupled[key] == pytest.approx(fixed[key], rel=1e-9, abs=1e-9), key
    assert coupled["ground_flow_ratio"] == pytest.approx(1.0)


def test_coupled_solver_without_the_cycling_model_keeps_its_failure():
    model = GroundSourceHeatPump(Cd_cycling=None)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        row = model.analyze_steady(**HEATING, ground_flow_lpm=model.ground_flow_ref_lpm)
    assert not row["converged"] and row["failure_reason"] == "compressor_min_speed"
    assert row["Q_ref_iu [W]"] == 0.0 and row["E_tot [W]"] == 0.0
    assert row[CAPACITY_RATIO_KEY] == 0.0 and row[RUNTIME_FRACTION_KEY] == 0.0


def test_mean_input_rises_with_the_request_through_the_minimum_capacity(gshp):
    """No dip, no jump and no off band where cycling hands over to continuous operation.

    Just above the smallest minimum-speed duty the coupled closure still has
    a continuous point, at the one indoor approach that reaches the request;
    it draws up to 16 % more than cycling at a better approach (491 W against
    423 W at 1,620 W). The lower mean input is returned.
    """
    floor = gshp.analyze_steady(**HEATING)[MIN_CAPACITY_KEY]
    requests = [0.90 * floor, 0.97 * floor, 0.985 * floor, 0.999 * floor, 1.01 * floor, 1.05 * floor]
    rows = [
        gshp.analyze_steady(**{**HEATING, "Q_r_iu": -q}, ground_flow_lpm=gshp.ground_flow_ref_lpm) for q in requests
    ]
    assert all(r["failure_reason"] == "none" for r in rows)
    assert [r["Q_ref_iu [W]"] for r in rows] == pytest.approx(requests, rel=1e-9)
    assert [r["capacity_clamped"] for r in rows] == ["min"] * 4 + [None] * 2
    power = [r["E_tot [W]"] for r in rows]
    assert all(b > a for a, b in zip(power, power[1:], strict=False))
    # Specific input (W per W delivered) is continuous through the hand-over to 1 %.
    specific = [p / q for p, q in zip(power, requests, strict=True)]
    assert specific[4] == pytest.approx(specific[3], rel=1e-2)


def test_variable_flow_cycles_at_the_flow_of_least_mean_input(gshp, gshp_optimal):
    reference = gshp.analyze_steady(**HEATING)
    row = gshp_optimal.analyze_steady(**HEATING)
    assert row["converged"] and row["failure_reason"] == "none" and row["capacity_clamped"] == "min"
    assert row["Q_ref_iu [W]"] == pytest.approx(1000.0, rel=1e-12)
    settings = gshp_optimal._ground_settings
    assert settings["min_ratio"] <= row["ground_flow_ratio"] <= settings["max_ratio"]
    assert row["ground_flow_ratio"] < 0.9  # less flow than the reference: less pump power
    # The hydraulic pump at the chosen flow draws less than the fixed 85 W at the reference flow.
    assert row["E_tot [W]"] < reference["E_tot [W]"]
    cr, plf = row[CAPACITY_RATIO_KEY], row[PART_LOAD_FACTOR_KEY]
    assert 0.0 < cr < 1.0 and plf == pytest.approx(1.0 - 0.25 * (1.0 - cr), rel=1e-12)
    assert row[RUNTIME_FRACTION_KEY] == pytest.approx(cr / plf, rel=1e-12)
    assert row["Q_bhe [W]"] == pytest.approx(row["Q_ref_ground [W]"] - row["E_pmp [W]"], rel=1e-12)


def test_dynamic_run_of_the_coupled_solver_cycles_and_loads_the_ground_with_the_mean():
    """A constant flow other than the reference flow runs the coupled solver in every step."""
    reference_lpm = GroundSourceHeatPump().ground_flow_ref_lpm
    steps, dt = 4, 3600
    model = GroundSourceHeatPump(
        ground_flow_constant_lpm=0.8 * reference_lpm, borehole_dynamics=False, t_max_s=steps * dt, dt_s=dt
    )
    assert model._ground_settings["active"]
    load = np.array([-600.0, -600.0, -1200.0, -2500.0])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df = model.analyze_dynamic(steps * dt, dt, load, np.zeros(steps), np.full(steps, 20.0))
    assert (df["failure_reason"] == "none").all()
    assert list(df["capacity_clamped"].fillna("none")) == ["min"] * 3 + ["none"]
    assert df["Q_ref_iu [W]"].to_numpy() == pytest.approx(-load, rel=1e-9)
    assert df["ground_flow_ratio"].to_numpy() == pytest.approx(np.full(steps, 0.8))
    assert (df[RUNTIME_FRACTION_KEY].iloc[:3] < 1.0).all() and df[RUNTIME_FRACTION_KEY].iloc[3] == 1.0
    # The ground history holds the mean load of each step, once.
    mean_load = (df["Q_ref_ground [W]"] - df["E_pmp [W]"]).to_numpy()
    assert df["Q_bhe [W]"].to_numpy() == pytest.approx(mean_load, rel=1e-12)
    q = mean_load / model.total_borehole_length
    w0, w1 = (float(v) for v in g_step_mean(model._gfunc_interp, np.array([0.0, float(dt)]), dt))
    assert df["T_bhe [°C]"].iloc[0] == pytest.approx(model.Ts - q[0] * w0, abs=1e-9)
    assert df["T_bhe [°C]"].iloc[1] == pytest.approx(model.Ts - (q[0] * w1 + (q[1] - q[0]) * w0), abs=1e-9)
    # The fluid of a cycling row is the running machine's: colder than the wall by more than the mean load explains.
    first = df.iloc[0]
    running = q[0] / first[CAPACITY_RATIO_KEY]
    fluid = 0.5 * (first["T_bhe_f_in [°C]"] + first["T_bhe_f_out [°C]"])
    assert first["T_bhe [°C]"] - fluid == pytest.approx(running * first["R_b_eff [mK/W]"], rel=3e-2)
