"""Borehole heat capacity, short-time response and fluid transit (tmhp.borehole_capacity)."""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy.special import kve

from tests._borehole_oracle import cycled_loop_outlet, plug_flow_outlet
from tests._ground_oracle import step_mean_quad
from tmhp.borehole import calc_effective_borehole_thermal_resistance, calc_local_borehole_thermal_resistance
from tmhp.borehole_capacity import (
    RHOC_GROUT_DEFAULT,
    DynamicGFunction,
    EquivalentCylinderBorehole,
    _talbot,
    _transit_section,
    cylinder_source_wall,
    fluid_to_pipe_resistance,
    line_source_wall,
    loop_outlet_response,
    section_admittance,
    with_borehole_capacity,
)
from tmhp.constants import c_w, k_w, mu_w, rho_w
from tmhp.g_function import precompute_gfunction
from tmhp.ground_coupling import AggregateGFunctionCoupler

# Default GSHPB borehole: r_b 80 mm, DN32 single U-tube, 200 m, 24 L/min.
R_B, R_OUT, R_IN, D_S, K_G, K_S, K_P, H = 0.08, 0.016, 0.013, 0.025, 1.5, 2.0, 0.4, 200.0
RHOC_S = 2000.0 * 800.0
ALPHA_S = K_S / RHOC_S
M_FLOW = 24.0 / 60000.0 * rho_w
M_CP = M_FLOW * c_w


def _resistances() -> tuple[float, float]:
    local, internal = calc_local_borehole_thermal_resistance(
        K_S, K_G, K_P, R_B, R_OUT, R_IN, D_S, M_FLOW, rho_w, mu_w, c_w, k_w
    )
    effective = calc_effective_borehole_thermal_resistance(local, internal, H, M_FLOW, c_w)
    return effective, fluid_to_pipe_resistance(M_FLOW, R_IN, R_OUT, K_P)


@pytest.fixture(scope="module")
def section() -> EquivalentCylinderBorehole:
    r_b_eff, r_fp = _resistances()
    return EquivalentCylinderBorehole.from_pipes(
        n_pipes=2, r_out=R_OUT, r_in=R_IN, r_b=R_B, R_b=r_b_eff, R_fp=r_fp, k_s=K_S, rhoc_s=RHOC_S
    )


@pytest.fixture(scope="module")
def g_field():
    return precompute_gfunction(1, 1, 6.0, H, 0.0, R_B, ALPHA_S, K_S, 30 * 86400.0, 3600.0)


# ---------------------------------------------------------------------------
# Analytical ground responses
# ---------------------------------------------------------------------------


def test_cylinder_source_matches_its_laplace_transform():
    """Carslaw-Jaeger real-axis integral == Talbot inversion of K0(z) / (s 2 pi k z K1(z))."""
    times = np.array([60.0, 600.0, 3600.0, 21600.0, 86400.0, 30 * 86400.0])

    def transform(s):
        z = R_B * np.sqrt(s / ALPHA_S)
        return kve(0, z) / (s * 2.0 * math.pi * K_S * z * kve(1, z))

    np.testing.assert_allclose(cylinder_source_wall(times, K_S, ALPHA_S, R_B), _talbot(transform, times), rtol=1e-7)


def test_cylinder_exceeds_line_source_and_they_meet_late():
    times = np.array([60.0, 600.0, 3600.0, 86400.0, 100 * 86400.0])
    cyl = cylinder_source_wall(times, K_S, ALPHA_S, R_B)
    line = line_source_wall(times, K_S, ALPHA_S, R_B)
    assert np.all(cyl > line)
    # 1 h at 27 W/m: 1.51 K (cylinder) against 0.84 K (line), the short-time gap of the steady model.
    assert 27.0 * cyl[2] == pytest.approx(1.512, abs=2e-3)
    assert 27.0 * line[2] == pytest.approx(0.841, abs=2e-3)
    assert (cyl[-1] - line[-1]) / line[-1] < 2e-3
    assert line_source_wall(np.array([0.0]), K_S, ALPHA_S, R_B)[0] == 0.0


# ---------------------------------------------------------------------------
# Equivalent-cylinder section
# ---------------------------------------------------------------------------


def test_mapping_reproduces_target_resistance_and_grout_capacity(section):
    r_b_eff, r_fp = _resistances()
    assert section.steady_resistance == pytest.approx(r_b_eff, rel=1e-13)
    assert section.R_in == pytest.approx(r_fp / 2)
    # Grout cross-section (so its heat capacity) is the real one.
    grout_area = math.pi * (section.r_b**2 - section.r_eq**2)
    assert grout_area == pytest.approx(math.pi * (R_B**2 - 2 * R_OUT**2), rel=1e-13)
    assert section.C_f == pytest.approx(rho_w * c_w * 2 * math.pi * R_IN**2)
    # A grouted borehole stores more heat than the soil it replaces.
    assert 1.5 < section.capacity_ratio < 3.5
    assert section.without_capacity().borehole_heat_capacity == 0.0


@pytest.mark.parametrize(
    "kwargs, match",
    [
        (dict(n_pipes=0), "n_pipes"),
        (dict(r_in=0.02), "smaller than r_out"),
        (dict(r_out=0.06, r_in=0.05), "do not fit"),
        (dict(R_b=0.03), "negative"),
        (dict(rhoc_s=0.0), "rhoc_s"),
    ],
)
def test_mapping_rejects_unphysical_sections(kwargs, match):
    base = dict(n_pipes=2, r_out=R_OUT, r_in=R_IN, r_b=R_B, R_b=0.16, R_fp=0.086, k_s=K_S, rhoc_s=RHOC_S)
    with pytest.raises(ValueError, match=match):
        EquivalentCylinderBorehole.from_pipes(**{**base, **kwargs})


def test_load_step_conserves_energy_and_recovers_the_steady_limit(section):
    times = np.array([60.0, 600.0, 3600.0, 21600.0, 86400.0, 10 * 86400.0])
    resp = section.load_step_response(times)
    balance = resp.stored_fluid + resp.stored_borehole + resp.stored_ground + resp.far_outflow
    np.testing.assert_allclose(balance, times, rtol=1e-11)
    # Without capacity the section is the steady resistance on a cylindrical source, exactly.
    np.testing.assert_allclose(resp.fluid_no_capacity, section.steady_resistance + resp.wall_no_capacity, rtol=1e-12)
    np.testing.assert_allclose(resp.wall_no_capacity, resp.ground_cylinder, atol=3e-4)
    # The first minute heats the fluid almost alone; after 10 days 97 % of the heat is in the ground.
    assert resp.stored_fluid[0] / times[0] > 0.8
    assert resp.stored_ground[-1] / times[-1] > 0.95
    # Heat capacity delays the fluid: always below the steady model, converging to it.
    assert np.all(resp.fluid < resp.fluid_no_capacity)
    assert np.all(np.diff(resp.fluid_deviation) > 0)
    assert abs(resp.fluid_deviation[-1]) < 0.01 * section.steady_resistance
    assert resp.fluid_deviation[0] == pytest.approx(-section.steady_resistance, abs=0.02)


def test_finite_volume_response_matches_the_laplace_solution(section):
    """Two independent solutions of the same section: FV time march vs analytical admittance + Talbot."""
    times = np.array([30.0, 60.0, 300.0, 600.0, 1800.0, 3600.0, 7200.0, 21600.0, 86400.0])
    laplace = _talbot(lambda s: 1.0 / (s * (s * section.C_f + section_admittance(section, s))), times)
    default = section.load_step_response(times).fluid
    fine = section.load_step_response(times, cells_per_decade=100, dt_min=0.5, step_fraction=0.002).fluid
    np.testing.assert_allclose(fine, laplace, atol=5e-5)
    np.testing.assert_allclose(default, laplace, atol=2e-4)  # 0.1 % of R_b at the default resolution


def test_load_step_rejects_bad_times(section):
    for bad in ([], [0.0, 1.0], [2.0, 1.0], [1.0, np.inf]):
        with pytest.raises(ValueError, match="times"):
            section.load_step_response(bad)


# ---------------------------------------------------------------------------
# Fluid transit
# ---------------------------------------------------------------------------


def test_transit_section_restores_the_effective_resistance(section):
    calibrated = _transit_section(section, H, M_CP)
    assert calibrated is not None
    x = H / (2.0 * M_CP * calibrated.steady_resistance)
    assert calibrated.steady_resistance * x / math.tanh(x) == pytest.approx(section.steady_resistance, rel=1e-10)
    assert calibrated.steady_resistance < section.steady_resistance
    # Very low flow: the loop temperature difference alone exceeds R_b; no calibration exists.
    assert _transit_section(section, H, M_CP / 20.0) is None


def test_nothing_reaches_the_outlet_before_one_transit(section):
    t_tr = section.C_f * H / M_CP
    assert t_tr == pytest.approx(531.0, abs=1.0)  # 8.9 min through 400 m of DN32 at 24 L/min
    rise = loop_outlet_response(section, np.array([0.2, 0.6, 0.999, 1.001, 1.5]) * t_tr, H, M_CP)
    assert np.all(rise[:3] == 0.0)
    # The front that arrives has lost all but exp(-NTU) of its temperature difference to the pipe wall.
    front = H / M_CP * math.exp(-H / (M_CP * section.R_in))
    assert rise[3] == pytest.approx(front, rel=0.05)
    assert rise[4] > rise[3]


def test_loop_outlet_matches_time_domain_plug_flow(section):
    """Laplace series vs upwind finite-volume march (first order: the gap halves with the segment size)."""
    t_tr = section.C_f * H / M_CP
    checks = np.array([0.5, 1.5, 2.5]) * t_tr
    laplace = loop_outlet_response(section, checks, H, M_CP)
    errors = []
    for n_axial, dt in ((40, 4.0), (80, 2.0)):
        times, outlet = plug_flow_outlet(section, H, M_CP, checks[-1] + dt, n_axial=n_axial, dt=dt)
        errors.append(np.abs(np.interp(checks, times, outlet) - laplace).max())
    assert errors[1] < 1.2e-3  # m.K/W, 0.7 % of R_b
    assert errors[1] < 0.65 * errors[0]


def test_fast_loop_tends_to_the_lumped_fluid(section):
    """With a short transit the outlet is the lumped fluid minus half the loop temperature difference."""
    m_cp = 50.0 * M_CP
    times = np.array([600.0, 3600.0, 21600.0])
    lumped = _talbot(lambda s: 1.0 / (s * (s * section.C_f + section_admittance(section, s))), times)
    loop = loop_outlet_response(section, times, H, m_cp) + H / (2.0 * m_cp)
    np.testing.assert_allclose(loop, lumped, atol=2e-4)


# ---------------------------------------------------------------------------
# Dynamic g-function
# ---------------------------------------------------------------------------


def test_dynamic_g_starts_at_minus_rb_and_joins_the_field_g(section, g_field):
    g_dyn = DynamicGFunction(g_field, section)
    assert g_dyn(0.0) == pytest.approx(-section.steady_resistance)
    assert g_dyn.wall(0.0) == 0.0
    # Lumped fluid: the splice g - G_ILS + S_cap, node for node, from the first g-function node on.
    times = np.array([3600.0, 7200.0, 21600.0, 86400.0, 10 * 86400.0])
    resp = section.load_step_response(times)
    np.testing.assert_allclose(g_dyn(times), g_field(times) + resp.fluid_deviation, atol=3e-4)
    np.testing.assert_allclose(g_dyn.wall(times), g_field(times) + resp.wall_deviation, atol=3e-4)
    np.testing.assert_allclose(g_dyn.fluid(times), g_dyn(times) + section.steady_resistance)
    # Long times: the steady model, to 0.3 % of R_b after a month and 0.05 % after a year.
    assert abs(g_dyn(30 * 86400.0) - g_field(30 * 86400.0)) < 3e-3 * section.steady_resistance
    assert abs(g_dyn(365 * 86400.0) - g_field(365 * 86400.0)) < 5e-4 * section.steady_resistance
    late = 10 * 365 * 86400.0
    assert g_dyn(late) == pytest.approx(float(g_field(late)), rel=1e-12)


def test_short_time_wall_is_the_cylinder_source_not_the_line(g_field):
    """Without heat capacity the wall follows the hollow-cylinder source from the first minute."""
    r_b_eff, r_fp = _resistances()
    bare = EquivalentCylinderBorehole.from_pipes(
        n_pipes=2, r_out=R_OUT, r_in=R_IN, r_b=R_B, R_b=r_b_eff, R_fp=r_fp, k_s=K_S, rhoc_s=RHOC_S
    ).without_capacity()
    g_dyn = DynamicGFunction(g_field, bare)
    times = np.array([60.0, 600.0, 1800.0, 3600.0, 7200.0])
    np.testing.assert_allclose(g_dyn.wall(times), cylinder_source_wall(times, K_S, ALPHA_S, R_B), atol=4e-4)
    np.testing.assert_allclose(g_dyn(times), g_dyn.wall(times), atol=1e-12)  # fluid - R_b == wall
    # The steady g-function is far below it in the first hour (straight line from the origin).
    assert g_field(600.0) < 0.25 * g_dyn.wall(600.0)


def test_transit_keeps_the_outlet_still_for_one_pass(section, g_field):
    g_dyn = DynamicGFunction(g_field, section, H=H, m_cp=M_CP)
    half = H / (2.0 * M_CP)
    assert g_dyn.half_loop == pytest.approx(half)
    assert g_dyn.transit_time == pytest.approx(section.C_f * H / M_CP, rel=1e-12)
    # Host outlet for an injection step: T_s + q' (K + R_b - half). Zero until the first pass returns.
    early = np.array([0.0, 60.0, 300.0, 500.0])
    np.testing.assert_allclose(g_dyn(early) + section.steady_resistance - half, 0.0, atol=2e-4)
    assert g_dyn(1.5 * g_dyn.transit_time) + section.steady_resistance - half > 0.02
    # After the blending window it is the lumped-fluid response again.
    lumped = DynamicGFunction(g_field, section)
    late = np.array([24.0, 40.0, 400.0]) * g_dyn.transit_time
    np.testing.assert_allclose(g_dyn(late), lumped(late), atol=1e-6)
    # Inside the window the two models agree within 5 % of R_b (0.2 K at 27 W/m).
    window = np.array([2.0, 4.0, 8.0, 16.0]) * g_dyn.transit_time
    assert np.abs(g_dyn(window) - lumped(window)).max() < 0.05 * section.steady_resistance
    # The wall kernel is the same (the two tables only differ in their nodes).
    np.testing.assert_allclose(g_dyn.wall(window), lumped.wall(window), atol=1e-5)


@pytest.mark.parametrize("lpm", [24.0, 12.0, 6.0])
def test_effective_resistance_is_the_steady_limit_at_every_flow(g_field, lpm):
    """R_b* (3-D, flow dependent), not the local R_b, is what the fluid temperature settles to.

    At 6 L/min per borehole R_b* is twice the local resistance. The section
    is mapped to R_b* and the loop is calibrated to reproduce it, so both
    reach the host's steady relation; they hand over to each other within
    2.5 % of R_b* at eight transit times, and the mean of inlet and outlet
    drops by q' H / (2 m c_p) at once (the inlet follows the load immediately).
    """
    m_flow = lpm / 60000.0 * rho_w
    m_cp = m_flow * c_w
    local, internal = calc_local_borehole_thermal_resistance(
        K_S, K_G, K_P, R_B, R_OUT, R_IN, D_S, m_flow, rho_w, mu_w, c_w, k_w
    )
    effective = calc_effective_borehole_thermal_resistance(local, internal, H, m_flow, c_w)
    assert effective > local
    section = EquivalentCylinderBorehole.from_pipes(
        n_pipes=2,
        r_out=R_OUT,
        r_in=R_IN,
        r_b=R_B,
        R_b=effective,
        R_fp=fluid_to_pipe_resistance(m_flow, R_IN, R_OUT, K_P),
        k_s=K_S,
        rhoc_s=RHOC_S,
    )
    assert section.steady_resistance == pytest.approx(effective, rel=1e-12)
    calibrated = _transit_section(section, H, m_cp)
    assert calibrated is not None and calibrated.R_in < calibrated.steady_resistance < effective
    transit = DynamicGFunction(g_field, section, H=H, m_cp=m_cp)
    lumped = DynamicGFunction(g_field, section)
    hand_over = 8.0 * transit.transit_time
    assert abs(float(transit(hand_over) - lumped(hand_over))) < 0.025 * effective
    # Mean of inlet and outlet right after the load starts: q' (K + R_b*) = q' H / (2 m c_p).
    assert float(transit.fluid(1.0)) == pytest.approx(H / (2.0 * m_cp), rel=1e-3)
    # Long times: g + R_b* (the steady model), for both.
    month = 30 * 86400.0
    for g_dyn in (transit, lumped):
        assert float(g_dyn.fluid(month)) == pytest.approx(float(g_field(month)) + effective, abs=3e-3 * effective)


def test_transit_needs_both_depth_and_flow(section, g_field):
    with pytest.raises(ValueError, match="both H and m_cp"):
        DynamicGFunction(g_field, section, H=H)
    # Very low flow: no calibrated section exists. The fluid stays one node, and it says so.
    with pytest.warns(RuntimeWarning, match="fluid transit is not resolved"):
        slow = DynamicGFunction(g_field, section, H=H, m_cp=M_CP / 20.0)
    assert slow.transit_time is None and slow.half_loop == 0.0
    with pytest.warns(RuntimeWarning, match="no fluid heat capacity"):
        dry = DynamicGFunction(g_field, section.without_capacity(), H=H, m_cp=M_CP)
    assert dry.transit_time is None
    with pytest.raises(ValueError, match="m_cp"):
        DynamicGFunction(g_field, section, H=H, m_cp=0.0)


@pytest.mark.parametrize("transit", [False, True])
@pytest.mark.parametrize("tau, dt", [(0.0, 60.0), (0.0, 600.0), (480.0, 120.0), (1800.0, 3600.0), (86400.0, 600.0)])
def test_step_mean_is_the_exact_interval_mean(section, g_field, transit, tau, dt):
    g_dyn = DynamicGFunction(g_field, section, **(dict(H=H, m_cp=M_CP) if transit else {}))
    assert float(g_dyn.step_mean(tau, dt)) == pytest.approx(step_mean_quad(g_dyn, tau, dt), rel=1e-9, abs=1e-12)

    class _Wall:
        x = g_dyn.x

        def __call__(self, t):
            return g_dyn.wall(t)

    assert float(g_dyn.wall_step_mean(tau, dt)) == pytest.approx(step_mean_quad(_Wall(), tau, dt), rel=1e-9, abs=1e-12)


def test_horizon_follows_the_field_g_function(section):
    field = precompute_gfunction(1, 1, 6.0, H, 0.0, R_B, ALPHA_S, K_S, 10 * 86400.0, 3600.0)
    g_dyn = DynamicGFunction(field, section)
    builds = g_dyn.n_builds
    far = 5 * 365 * 86400.0
    assert g_dyn.t_end < far
    value = float(g_dyn(far))
    assert g_dyn.n_builds == builds + 1 and g_dyn.t_end >= far
    assert value == pytest.approx(float(field(far)), rel=1e-12)
    # A horizon extended through the field g-function itself is picked up too.
    field.ensure_horizon(40 * 365 * 86400.0)
    assert float(g_dyn.step_mean(30 * 365 * 86400.0, 3600.0)) == pytest.approx(
        float(field.step_mean(30 * 365 * 86400.0, 3600.0)), rel=1e-9
    )
    with pytest.raises(ValueError, match="non-finite"):
        g_dyn.ensure_horizon(math.inf)
    with pytest.raises(ValueError, match="dt must be positive"):
        g_dyn.step_mean(0.0, 0.0)


@pytest.mark.parametrize("transit", [False, True])
def test_short_field_horizon_is_extended_not_clamped(section, transit):
    """A model built for a short run and run longer: the field g-function is recomputed.

    The capacity kernel spans about a month. With a field grid that ends
    earlier (t_max_s of two days -> three days) the table must end there too;
    otherwise later times read the last field value and the ground stops
    cooling.
    """
    field = precompute_gfunction(1, 1, 6.0, H, 0.0, R_B, ALPHA_S, K_S, 2 * 86400.0, 3600.0)
    reference = precompute_gfunction(1, 1, 6.0, H, 0.0, R_B, ALPHA_S, K_S, 400 * 86400.0, 3600.0)
    field_end = field.t_end
    g_dyn = DynamicGFunction(field, section, **(dict(H=H, m_cp=M_CP) if transit else {}))
    assert g_dyn.t_end == pytest.approx(field_end)
    times = np.array([5.0, 10.0, 20.0, 60.0, 300.0]) * 86400.0
    deviation = np.array([float(g_dyn(t) - reference(t)) for t in times])
    assert field.t_end >= times[-1] and field.n_builds > 1
    # Dynamic minus steady: the capacity correction only (negative, vanishing), never a flat g.
    assert np.all(deviation < 0) and np.all(np.diff(deviation) > 0)
    assert abs(deviation[0]) < 0.02 * section.steady_resistance
    assert abs(deviation[-1]) < 1e-3 * section.steady_resistance
    # The step-mean path extends the horizon as well.
    fresh = precompute_gfunction(1, 1, 6.0, H, 0.0, R_B, ALPHA_S, K_S, 2 * 86400.0, 3600.0)
    g_fresh = DynamicGFunction(fresh, section)
    lag = 15 * 86400.0
    assert float(g_fresh.step_mean(lag, 3600.0)) == pytest.approx(float(g_dyn.step_mean(lag, 3600.0)), rel=2e-3)
    assert float(g_fresh.wall_step_mean(lag, 3600.0)) > float(reference(10 * 86400.0))


def test_plain_callable_needs_a_horizon(section):
    def g_line(t):
        return line_source_wall(np.asarray(t, dtype=float), K_S, ALPHA_S, R_B)

    with pytest.raises(ValueError, match="t_max_s"):
        DynamicGFunction(g_line, section)
    g_dyn = DynamicGFunction(g_line, section, t_max_s=30 * 86400.0)
    assert g_dyn.n_builds == 1
    # On a pure line source the dynamic fluid response is the section response itself.
    times = np.array([600.0, 3600.0, 86400.0])
    np.testing.assert_allclose(g_dyn.fluid(times), section.load_step_response(times).fluid, atol=3e-4)
    g_dyn.ensure_horizon(90 * 86400.0)
    assert g_dyn.t_end >= 90 * 86400.0


def test_with_borehole_capacity_derives_the_section_from_model_inputs(g_field):
    r_b_eff, r_fp = _resistances()
    common = dict(r_b=R_B, k_s=K_S, rhoc_s=RHOC_S, R_b=r_b_eff, r_out=R_OUT, r_in=R_IN)
    g_dyn = with_borehole_capacity(g_field, k_p=K_P, m_flow_borehole=M_FLOW, H=H, **common)
    assert g_dyn.section.R_in == pytest.approx(r_fp / 2)
    assert g_dyn.section.rhoc_g == RHOC_GROUT_DEFAULT
    assert g_dyn.transit_time is not None
    assert with_borehole_capacity(g_field, R_fp=r_fp, **common).transit_time is None
    off = with_borehole_capacity(g_field, k_p=K_P, m_flow_borehole=M_FLOW, H=H, transit=False, **common)
    assert off.transit_time is None
    with pytest.raises(ValueError, match="R_fp"):
        with_borehole_capacity(g_field, **common)
    with pytest.raises(ValueError, match="m_flow_pipe"):
        fluid_to_pipe_resistance(0.0, R_IN, R_OUT, K_P)
    # Double U-tube in parallel: four pipes, each with half the borehole flow.
    double = with_borehole_capacity(g_field, n_pipes=4, k_p=K_P, m_flow_borehole=M_FLOW, **{**common, "R_b": 0.09})
    assert double.section.R_in == pytest.approx(fluid_to_pipe_resistance(M_FLOW / 2, R_IN, R_OUT, K_P) / 4)


# ---------------------------------------------------------------------------
# Coupler and host formula
# ---------------------------------------------------------------------------


def test_coupler_gives_the_dynamic_fluid_temperature_through_the_steady_formula(section, g_field):
    """T_f = T_s - dT - q' R_b with the fluid-referenced dT is the dynamic fluid temperature."""
    g_dyn = DynamicGFunction(g_field, section)
    r_b_eff = section.steady_resistance
    dt, n, q = 600.0, 6 * 24 * 3, 27.0
    time_arr = np.arange(n) * dt
    coupler = AggregateGFunctionCoupler(g_dyn)
    coupler.reset(n, time_arr, dt_s=dt)
    fluid, wall = np.empty(n), np.empty(n)
    for i in range(n):
        preview = coupler.preview_wall_temperature_rise(i, time_arr, q)
        rise = coupler.wall_temperature_rise(i, time_arr, q)
        assert preview == pytest.approx(rise, abs=1e-12)
        fluid[i] = rise + q * r_b_eff  # fluid temperature drop below T_s
        wall[i] = coupler.borehole_wall_rise(i, time_arr)
    expected = q * (np.asarray(g_dyn.step_mean(time_arr, dt)) + r_b_eff)
    np.testing.assert_allclose(fluid, expected, rtol=1e-12)
    np.testing.assert_allclose(wall, q * np.asarray(g_dyn.wall_step_mean(time_arr, dt)), rtol=1e-12)
    # First 10 minutes: 1.0 K instead of the steady model's 4.5 K; the wall has barely moved.
    steady = q * (float(g_field.step_mean(0.0, dt)) + r_b_eff)
    assert fluid[0] == pytest.approx(1.03, abs=0.05) and steady == pytest.approx(4.51, abs=0.05)
    assert wall[0] < 0.01
    # The fluid never leads the steady model and has joined it (0.3 K) after three days.
    steady_all = q * (np.asarray(g_field.step_mean(time_arr, dt)) + r_b_eff)
    assert np.all(fluid <= steady_all + 1e-9)
    assert steady_all[-1] - fluid[-1] < 0.3
    assert np.all(np.diff(fluid) > 0) and np.all(np.diff(wall) > 0)


def test_fluid_relaxes_towards_the_wall_after_the_load_stops(section, g_field):
    g_dyn = DynamicGFunction(g_field, section)
    r_b_eff = section.steady_resistance
    dt, n = 600.0, 36
    time_arr = np.arange(n) * dt
    load = np.where(np.arange(n) < 6, 27.0, 0.0)  # one hour on, five hours off
    coupler = AggregateGFunctionCoupler(g_dyn)
    coupler.reset(n, time_arr, dt_s=dt)
    fluid = np.array([coupler.wall_temperature_rise(i, time_arr, float(load[i])) + load[i] * r_b_eff for i in range(n)])
    wall = np.array([coupler.borehole_wall_rise(i, time_arr) for i in range(n)])
    # No jump when the load stops (the steady model drops q' R_b = 4.4 K at once) ...
    assert abs(fluid[6] - fluid[5]) < 0.9
    assert np.all(np.diff(fluid[6:]) < 0)
    # ... and fluid and wall end within 0.1 K of each other.
    assert abs(fluid[-1] - wall[-1]) < 0.1
    # Uniform-grid fast path == direct evaluation on a copied time array.
    other = AggregateGFunctionCoupler(g_dyn)
    other.reset(n, time_arr, dt_s=dt)
    shifted = time_arr + 0.0
    for i in range(8):
        other.wall_temperature_rise(i, time_arr, float(load[i]))
    other._gbar_rev = other._gwall_rev = None
    assert other.borehole_wall_rise(7, shifted) == pytest.approx(wall[7], rel=1e-10)


@pytest.mark.parametrize("transit", [False, True])
def test_hourly_means_do_not_depend_on_the_step(section, g_field, transit):
    """An hourly load history gives the same hourly-mean response at 60, 600 and 3600 s steps."""
    g_dyn = DynamicGFunction(g_field, section, **(dict(H=H, m_cp=M_CP) if transit else {}))
    rng = np.random.default_rng(7)
    hourly = np.where(rng.random(30) < 0.5, rng.uniform(10.0, 40.0, 30), 0.0)
    means = {}
    for dt in (60.0, 600.0, 3600.0):
        per_hour = int(3600 / dt)
        load = np.repeat(hourly, per_hour)
        time_arr = np.arange(len(load)) * dt
        coupler = AggregateGFunctionCoupler(g_dyn)
        coupler.reset(len(load), time_arr, dt_s=dt)
        rise = np.array([coupler.wall_temperature_rise(i, time_arr, float(load[i])) for i in range(len(load))])
        means[dt] = rise.reshape(-1, per_hour).mean(axis=1)
    np.testing.assert_allclose(means[60.0], means[3600.0], atol=1e-10)
    np.testing.assert_allclose(means[600.0], means[3600.0], atol=1e-10)


def test_cycling_matches_a_time_domain_loop_and_bounds_the_stopped_pump(section, g_field):
    """10 min on / 20 min off through the kernel and the host formula, against a time-marched loop.

    The kernel is linear, so it describes a loop that keeps circulating while
    the load is off; that it reproduces to 1 % of R_b. A pump that stops
    leaves the fluid standing: the outlet then moves less (the model is on the
    cold side for extraction), by a few percent of R_b on average over a run
    and about 12 % in the first minute after a restart.
    """
    calibrated = _transit_section(section, H, M_CP)
    on_s, off_s, cycles, dt = 600.0, 1200.0, 6, 60.0
    r_b = section.steady_resistance
    g_dyn = DynamicGFunction(g_field, section, H=H, m_cp=M_CP)
    n = int(cycles * (on_s + off_s) / dt)
    time_arr = np.arange(n) * dt
    load = np.where(time_arr % (on_s + off_s) < on_s, 1.0, 0.0)
    coupler = AggregateGFunctionCoupler(g_dyn)
    coupler.reset(n, time_arr, dt_s=dt)
    # Outlet rise for an injection: K + R_b - H / (2 m c_p) while loaded, K alone otherwise.
    kernel = np.array(
        [coupler.wall_temperature_rise(i, time_arr, load[i]) + load[i] * (r_b - g_dyn.half_loop) for i in range(n)]
    )

    def binned(stop_pump: bool) -> np.ndarray:
        times, outlet, _ = cycled_loop_outlet(calibrated, H, M_CP, on_s, off_s, cycles, stop_pump=stop_pump)
        return np.array([outlet[(times > a) & (times <= a + dt)].mean() for a in time_arr])

    circulating, stopped = binned(False), binned(True)
    on = load > 0
    assert np.abs(circulating[on] - kernel[on]).max() < 0.012 * r_b
    per_run = (stopped - circulating)[on].reshape(cycles, -1).mean(axis=1) / r_b
    assert per_run[0] == pytest.approx(0.0, abs=1e-12)  # the first run has no history
    assert np.all(per_run[1:] < 0) and np.all(per_run[1:] > -0.06)
    assert np.abs(stopped[on] - circulating[on]).max() < 0.15 * r_b


def test_plain_g_function_reports_the_same_wall(g_field):
    dt, n = 3600.0, 12
    time_arr = np.arange(n) * dt
    coupler = AggregateGFunctionCoupler(g_field)
    coupler.reset(n, time_arr, dt_s=dt)
    for i in range(n):
        rise = coupler.wall_temperature_rise(i, time_arr, 20.0 if i < 8 else 0.0)
        assert coupler.borehole_wall_rise(i, time_arr) == rise
