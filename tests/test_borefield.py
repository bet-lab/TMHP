"""Bore-field layouts, pipe configurations and the assembled field response (pygfunction)."""

from __future__ import annotations

import warnings
from types import SimpleNamespace

import numpy as np
import pytest

gt = pytest.importorskip("pygfunction")

from pygfunction.enums import PipeType  # noqa: E402

from tmhp.borefield import BoreField, BoreNetwork, FieldGroundResponse  # noqa: E402
from tmhp.borehole import (  # noqa: E402
    BoreholePipe,
    calc_borehole_thermal_resistance,
    calc_effective_borehole_thermal_resistance,
    calc_local_borehole_thermal_resistance,
)
from tmhp.constants import c_w, k_w, mu_w, rho_w  # noqa: E402
from tmhp.g_function import GFunction, precompute_gfunction  # noqa: E402

K_S, ALPHA, R_B = 2.0, 2.0 / (2000.0 * 800.0), 0.08
YEAR = 8760 * 3600.0
M_24LPM = 24.0 / 60000.0 * rho_w  # 24 L/min of water [kg/s]
FLUID = SimpleNamespace(rho=rho_w, mu=mu_w, cp=c_w, k=k_w)


@pytest.fixture(autouse=True)
def _no_pygfunction_warnings():
    # pygfunction warns when it silently switches solver; every call here
    # must choose its solver explicitly.
    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)
        yield


def _pyg_grid(t_max_s: float, num: int = 100) -> np.ndarray:
    return np.geomspace(3600.0, 1.5 * t_max_s, num=num)


# ---------------------------------------------------------------------------
# Legacy path unchanged
# ---------------------------------------------------------------------------

# precompute_gfunction(n, n, 6, 100, 0, 0.08, ALPHA, 2, YEAR, 3600, bc) at grid
# nodes 1, 25, 60, 100, taken from origin/main 315f8a8 on macOS arm64. The
# direct-call comparison below is exact; these literals pin the values across
# pygfunction/numpy upgrades, at 1e-9 because Linux and macOS differ at that
# level (BLAS, quadrature).
_LEGACY_X = [3600.0, 35871.526807084934, 1025224.3925581449, 47304000.0]
_LEGACY_Y = {
    (1, "UBWT"): [0.03113470997530641, 0.11085024900896275, 0.24142326897609273, 0.38206211996517553],
    (3, "UBWT"): [0.03113470997530642, 0.11085024900896269, 0.24143496489366242, 0.6101171541602993],
    (2, "UHTR"): [0.03113527743513844, 0.11085988467613161, 0.24168040339792055, 0.5236987273069774],
}


@pytest.mark.parametrize(("n", "bc"), list(_LEGACY_Y))
def test_legacy_grid_is_unchanged(n, bc):
    g = precompute_gfunction(n, n, 6.0, 100.0, 0.0, R_B, ALPHA, K_S, YEAR, 3600.0, boundary_condition=bc)
    idx = [1, 25, 60, 100]
    np.testing.assert_allclose(g.x[idx], _LEGACY_X, rtol=1e-12)
    np.testing.assert_allclose(g.y[idx], _LEGACY_Y[(n, bc)], rtol=1e-9)
    # Bit for bit what pygfunction gives for the same call.
    times = _pyg_grid(YEAR)
    field = gt.borefield.Borefield.rectangle_field(N_1=n, N_2=n, B_1=6.0, B_2=6.0, H=100.0, D=0.0, r_b=R_B)
    direct = np.asarray(gt.gfunction.gFunction(field, ALPHA, time=times, boundary_condition=bc).gFunc)
    np.testing.assert_array_equal(g.y[1:], direct / (2 * np.pi * K_S))
    np.testing.assert_array_equal(g.x, np.r_[0.0, times])


@pytest.mark.parametrize("bc", ["UBWT", "UHTR"])
def test_from_field_rectangle_equals_legacy_constructor(bc):
    legacy = GFunction(3, 2, 6.0, 120.0, 2.0, R_B, ALPHA, K_S, YEAR, 3600.0, boundary_condition=bc)
    new = GFunction.from_field(BoreField.rectangle(3, 2, 6.0, 120.0, D=2.0), ALPHA, K_S, YEAR, boundary_condition=bc)
    np.testing.assert_array_equal(new.x, legacy.x)
    np.testing.assert_array_equal(new.y, legacy.y)
    # The horizon extension rebuilds both the same way.
    t = np.array([5.0, 20.0]) * YEAR
    np.testing.assert_array_equal(new(t), legacy(t))
    assert new.n_builds == legacy.n_builds == 2


def test_legacy_g_has_no_load_shares():
    g = GFunction(1, 1, 6.0, 100.0, 0.0, R_B, ALPHA, K_S, YEAR, 3600.0)
    with pytest.raises(RuntimeError, match="from_field"):
        g.borehole_load_shares(3600.0)


# ---------------------------------------------------------------------------
# BoreField
# ---------------------------------------------------------------------------


def test_borefield_properties_and_readonly_copies():
    xy = np.array([[0.0, 0.0], [6.0, 0.0], [3.0, 5.0]])
    H = np.array([150.0, 200.0, 100.0])
    f = BoreField.from_coordinates(xy, H, D=1.5)
    xy[0, 0] = 99.0
    H[0] = 1.0
    assert f.x[0] == 0.0 and f.H[0] == 150.0
    assert f.n_boreholes == 3 and f.total_length == 450.0 and f.mean_depth == 150.0
    np.testing.assert_array_equal(f.D, [1.5, 1.5, 1.5])
    with pytest.raises(ValueError):
        f.H[0] = 5.0
    pyg = f.to_pygfunction()
    np.testing.assert_array_equal(pyg.H, f.H)
    np.testing.assert_array_equal(pyg.x, f.x)


def test_borefield_shapes_match_pygfunction():
    cases = [
        (
            BoreField.rectangle(4, 3, 5.0, 150.0, D=1.0),
            gt.borefield.Borefield.rectangle_field(4, 3, 5.0, 5.0, 150.0, 1.0, 0.08),
        ),
        (
            BoreField.l_shape(4, 3, 5.0, 6.0, 150.0),
            gt.borefield.Borefield.L_shaped_field(4, 3, 5.0, 6.0, 150.0, 0.0, 0.08),
        ),
        (
            BoreField.u_shape(4, 3, 5.0, 6.0, 150.0),
            gt.borefield.Borefield.U_shaped_field(4, 3, 5.0, 6.0, 150.0, 0.0, 0.08),
        ),
        (
            BoreField.box(4, 3, 5.0, 6.0, 150.0),
            gt.borefield.Borefield.box_shaped_field(4, 3, 5.0, 6.0, 150.0, 0.0, 0.08),
        ),
        (BoreField.circle(8, 10.0, 150.0), gt.borefield.Borefield.circle_field(8, 10.0, 150.0, 0.0, 0.08)),
    ]
    for ours, theirs in cases:
        assert ours.n_boreholes == len(theirs)
        np.testing.assert_array_equal(ours.x, theirs.x)
        np.testing.assert_array_equal(ours.y, theirs.y)
        np.testing.assert_array_equal(ours.H, theirs.H)


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        (dict(xy=[[0, 0], [6, 0]], H=[100.0, -1.0]), "H must be positive"),
        (dict(xy=[[0, 0], [6, 0]], H=100.0, D=-1.0), "nonnegative"),
        (dict(xy=[[0, 0], [6, 0]], H=100.0, r_b=0.0), "r_b"),
        (dict(xy=[[0, 0], [0.1, 0]], H=100.0), "apart"),
        (dict(xy=[[0, 0], [0, 0]], H=100.0), "apart"),
        (dict(xy=[[0, 0], [6, np.nan]], H=100.0), "finite"),
        (dict(xy=[[0, 0], [6, 0]], H=[100.0, 100.0, 100.0]), "shape"),
        (dict(xy=[0.0, 6.0], H=100.0), r"\(n, 2\)"),
        (dict(xy=np.zeros((0, 2)), H=100.0), r"\(n, 2\)"),
    ],
)
def test_borefield_validation(kwargs, match):
    with pytest.raises(ValueError, match=match):
        BoreField.from_coordinates(**kwargs)


def test_borefield_count_validation():
    with pytest.raises(ValueError, match="positive integer"):
        BoreField.rectangle(0, 2, 6.0, 100.0)
    with pytest.raises(ValueError, match="positive integer"):
        BoreField.circle(2.5, 10.0, 100.0)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Pipes
# ---------------------------------------------------------------------------

_GEOM = dict(k_s=K_S, k_g=1.5, k_p=0.4, r_b=R_B, r_out=0.016, r_in=0.013, D_s=0.025)
_FLUID = dict(rho_f=rho_w, mu_f=mu_w, cp_f=c_w, k_f=k_w)


@pytest.mark.parametrize("m_flow", [0.05, M_24LPM, 1.0])
def test_single_u_matches_legacy_functions(m_flow):
    pipe = BoreholePipe()
    local, R_a = calc_local_borehole_thermal_resistance(m_flow_pipe=m_flow, **_GEOM, **_FLUID)
    assert pipe.local_resistance(r_b=R_B, k_s=K_S, m_flow_borehole=m_flow) == local
    eff = pipe.effective_resistance(200.0, r_b=R_B, k_s=K_S, m_flow_borehole=m_flow)
    assert eff == calc_borehole_thermal_resistance(H_b=200.0, m_flow_borehole=m_flow, **_GEOM, **_FLUID)
    # Hellström's closed form gives the same R_b* (to rounding).
    hellstrom = calc_effective_borehole_thermal_resistance(local, R_a, 200.0, m_flow, c_w)
    assert eff == pytest.approx(hellstrom, rel=1e-12)


@pytest.mark.parametrize(
    ("pipe", "pipe_type"),
    [
        (BoreholePipe(), PipeType.SINGLE_UTUBE),
        (BoreholePipe(kind="double_u_parallel"), PipeType.DOUBLE_UTUBE_PARALLEL),
        (BoreholePipe(kind="double_u_series"), PipeType.DOUBLE_UTUBE_SERIES),
    ],
)
def test_u_tube_resistances_match_pygfunction(pipe, pipe_type):
    R_fp = gt.pipes.fluid_to_pipe_thermal_resistance(pipe_type, M_24LPM, 0.013, 0.016, 0.4, 1e-6, FLUID)
    assert pipe.fluid_to_pipe_resistance(M_24LPM) == R_fp
    b = gt.boreholes.Borehole(200.0, 0.0, R_B, 0.0, 0.0)
    if pipe.kind == "single_u":
        ref = gt.pipes.SingleUTube([(-0.025, 0.0), (0.025, 0.0)], 0.013, 0.016, b, K_S, 1.5, R_fp)
    else:
        pos = [(-0.025, 0.0), (0.0, -0.025), (0.025, 0.0), (0.0, 0.025)]
        config = pipe.kind.removeprefix("double_u_")
        ref = gt.pipes.MultipleUTube(pos, 0.013, 0.016, b, K_S, 1.5, R_fp, nPipes=2, config=config)
    assert pipe.local_resistance(r_b=R_B, k_s=K_S, m_flow_borehole=M_24LPM) == ref.local_borehole_thermal_resistance()
    assert pipe.effective_resistance(
        200.0, r_b=R_B, k_s=K_S, m_flow_borehole=M_24LPM
    ) == ref.effective_borehole_thermal_resistance(M_24LPM, c_w)


@pytest.mark.parametrize(
    ("flow_down", "pipe_type"), [("annulus", PipeType.COAXIAL_ANNULAR_IN), ("inner", PipeType.COAXIAL_ANNULAR_OUT)]
)
def test_coaxial_matches_pygfunction(flow_down, pipe_type):
    pipe = BoreholePipe.coaxial(flow_down=flow_down)
    inner, outer = (0.0221, 0.025), (0.0487, 0.055)
    first, second = (outer, inner) if flow_down == "annulus" else (inner, outer)
    r_in, r_out = np.array([first[0], second[0]]), np.array([first[1], second[1]])
    k_p = np.array([0.4, 0.4])
    R_fp = gt.pipes.fluid_to_pipe_thermal_resistance(pipe_type, M_24LPM, r_in, r_out, k_p, 1e-6, FLUID)
    R_ff = gt.pipes.fluid_to_fluid_thermal_resistance(pipe_type, M_24LPM, r_in, r_out, k_p, 1e-6, FLUID)
    assert pipe.fluid_to_pipe_resistance(M_24LPM) == pytest.approx(R_fp, rel=1e-14)
    assert pipe.fluid_to_fluid_resistance(M_24LPM) == pytest.approx(R_ff, rel=1e-14)
    b = gt.boreholes.Borehole(200.0, 0.0, R_B, 0.0, 0.0)
    ref = gt.pipes.Coaxial((0.0, 0.0), r_in, r_out, b, K_S, 1.5, R_ff, R_fp)
    ours = pipe.pygfunction_pipe(b, K_S, M_24LPM)
    assert ours.local_borehole_thermal_resistance() == pytest.approx(ref.local_borehole_thermal_resistance(), rel=1e-12)
    assert pipe.effective_resistance(200.0, r_b=R_B, k_s=K_S, m_flow_borehole=M_24LPM) == pytest.approx(
        ref.effective_borehole_thermal_resistance(M_24LPM, c_w), rel=1e-12
    )


def test_double_u_parallel_beats_single_u_at_the_same_flow():
    kw = dict(r_b=R_B, k_s=K_S, m_flow_borehole=M_24LPM)
    single = BoreholePipe()
    double = BoreholePipe(kind="double_u_parallel")
    assert double.local_resistance(**kw) < single.local_resistance(**kw)
    assert double.effective_resistance(200.0, **kw) < single.effective_resistance(200.0, **kw)
    assert double.n_pipes == 2 and single.n_pipes == 1 and BoreholePipe.coaxial().n_pipes == 1
    # Each parallel U-tube carries half the flow.
    half = BoreholePipe(kind="single_u").fluid_to_pipe_resistance(M_24LPM / 2)
    assert double.fluid_to_pipe_resistance(M_24LPM) == half


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        (dict(kind="triple_u"), "kind"),
        (dict(r_in=0.016, r_out=0.013), "r_out must be greater"),
        (dict(D_s=0.015), "overlap"),  # single U: legs 0.030 m apart < 2 r_out
        (dict(kind="double_u_parallel", D_s=0.02), "overlap"),  # sqrt(2)*0.02 < 0.032
        (dict(k_g=0.0), "k_g"),
        (dict(kind="coaxial"), "coaxial pipe needs"),
        (dict(kind="coaxial", r_in_inner=0.02, r_out_inner=0.05, r_in_outer=0.048, r_out_outer=0.055), "fit inside"),
        (
            dict(
                kind="coaxial", r_in_inner=0.02, r_out_inner=0.025, r_in_outer=0.048, r_out_outer=0.055, flow_down="up"
            ),
            "flow_down",
        ),
    ],
)
def test_pipe_validation(kwargs, match):
    with pytest.raises(ValueError, match=match):
        BoreholePipe(**kwargs)


def test_pipe_flow_and_wall_errors():
    pipe = BoreholePipe()
    with pytest.raises(ValueError, match="m_flow_borehole"):
        pipe.fluid_to_pipe_resistance(0.0)
    with pytest.raises(ValueError, match="m_flow_borehole"):
        pipe.effective_resistance(100.0, r_b=R_B, k_s=K_S, m_flow_borehole=0.0)
    with pytest.raises(ValueError, match="borehole wall"):
        pipe.local_resistance(r_b=0.04, k_s=K_S, m_flow_borehole=0.3)  # 0.025 + 0.016 > 0.04
    with pytest.raises(ValueError, match="borehole wall"):
        BoreholePipe.coaxial().local_resistance(r_b=0.05, k_s=K_S, m_flow_borehole=0.3)
    with pytest.raises(ValueError, match="coaxial pipes only"):
        pipe.fluid_to_fluid_resistance(0.3)


# ---------------------------------------------------------------------------
# Arbitrary fields
# ---------------------------------------------------------------------------

_IRREGULAR_XY = [[0.0, 0.0], [6.0, 0.0], [3.0, 5.0]]
_IRREGULAR_H = [150.0, 200.0, 100.0]


def test_irregular_field_matches_pygfunction_and_ignores_order():
    field = BoreField.from_coordinates(_IRREGULAR_XY, _IRREGULAR_H)
    g = GFunction.from_field(field, ALPHA, K_S, YEAR, num=40)
    times = _pyg_grid(YEAR, 40)
    direct = gt.gfunction.gFunction(field.to_pygfunction(), ALPHA, time=times, boundary_condition="UBWT").gFunc
    np.testing.assert_allclose(g.y[1:], np.asarray(direct) / (2 * np.pi * K_S), rtol=1e-10)
    perm = [2, 0, 1]
    shuffled = BoreField.from_coordinates(np.asarray(_IRREGULAR_XY)[perm], np.asarray(_IRREGULAR_H)[perm])
    g2 = GFunction.from_field(shuffled, ALPHA, K_S, YEAR, num=40)
    # Same sorted input to pygfunction either way; the shares are un-sorted
    # back through different index paths, so allow round-off (not bit equality).
    np.testing.assert_allclose(g2.y, g.y, rtol=1e-12)
    np.testing.assert_allclose(g2.borehole_load_shares(YEAR), g.borehole_load_shares(YEAR)[perm], rtol=1e-9)


def test_interleaved_depths_avoid_pygfunction_cluster_bug():
    # pygfunction 2.3.1 'equivalent' mis-assigns clusters when depth groups
    # interleave; the sorted input given by from_field avoids it.
    x = np.arange(5) * 6.0
    H = np.array([100.0, 200.0, 100.0, 200.0, 100.0])
    field = BoreField.from_coordinates(np.c_[x, np.zeros(5)], H)
    g = GFunction.from_field(field, ALPHA, K_S, YEAR, num=30)
    times = _pyg_grid(YEAR, 30)
    ref = gt.gfunction.gFunction(field.to_pygfunction(), ALPHA, time=times, method="similarities").gFunc
    np.testing.assert_allclose(g.y[1:], np.asarray(ref) / (2 * np.pi * K_S), rtol=1e-9)
    shares = g.borehole_load_shares(YEAR)[:, 0]
    np.testing.assert_allclose(shares[0], shares[4], rtol=1e-12)
    np.testing.assert_allclose(shares[1], shares[3], rtol=1e-12)


def test_load_shares():
    m = M_24LPM
    single = FieldGroundResponse(
        BoreField.rectangle(1, 1, 6.0, 100.0), k_s=K_S, alpha_s=ALPHA, m_flow_total=m, t_max_s=YEAR, num=30
    )
    np.testing.assert_array_equal(single.borehole_load_shares([0.0, 3600.0, YEAR]), np.ones((1, 3)))
    resp = FieldGroundResponse(
        BoreField.rectangle(3, 3, 6.0, 100.0), k_s=K_S, alpha_s=ALPHA, m_flow_total=m, t_max_s=30 * YEAR, num=40
    )
    times = np.array([0.0, 1800.0, 86400.0, YEAR, 30 * YEAR])
    s = resp.borehole_load_shares(times)
    assert s.shape == (9, 5)
    np.testing.assert_allclose(s.sum(axis=0), 1.0, rtol=1e-14)
    np.testing.assert_allclose(s[:, 0], 1 / 9, rtol=1e-14)  # t = 0: uniform per metre
    late = s[:, -1]
    corners, edges, centre = late[[0, 2, 6, 8]], late[[1, 3, 5, 7]], late[4]
    np.testing.assert_allclose(corners, corners[0], rtol=1e-12)
    np.testing.assert_allclose(edges, edges[0], rtol=1e-12)
    assert corners[0] > edges[0] > centre
    # Interference grows with time: the centre's share falls.
    assert s[4, 2] > s[4, 3] > s[4, 4]
    uhtr = FieldGroundResponse(
        BoreField.from_coordinates(_IRREGULAR_XY, _IRREGULAR_H),
        k_s=K_S,
        alpha_s=ALPHA,
        m_flow_total=m,
        t_max_s=YEAR,
        boundary_condition="UHTR",
        num=20,
    )
    np.testing.assert_allclose(uhtr.borehole_load_shares([YEAR])[:, 0], np.array(_IRREGULAR_H) / 450.0, rtol=1e-14)
    with pytest.raises(ValueError, match="negative"):
        resp.borehole_load_shares(-1.0)


def test_equivalent_shares_are_cluster_means_of_detailed_shares():
    field = BoreField.l_shape(5, 4, 6.0, 6.0, 150.0)
    kw = dict(k_s=K_S, alpha_s=ALPHA, m_flow_total=0.6, t_max_s=30 * YEAR, num=25)
    eq = FieldGroundResponse(field, **kw).borehole_load_shares([30 * YEAR])[:, 0]
    sim = FieldGroundResponse(field, method="similarities", **kw).borehole_load_shares([30 * YEAR])[:, 0]
    np.testing.assert_allclose(eq.sum(), 1.0, rtol=1e-14)
    np.testing.assert_allclose(eq, sim, atol=5e-3)
    # The arm tips (corner of the L excluded) take more than the inner corner.
    assert sim[0] > sim[5] and eq[0] > eq[5]


# ---------------------------------------------------------------------------
# Field response, MIFT and networks
# ---------------------------------------------------------------------------


def test_parallel_uniform_field_uses_ubwt_and_pipe_resistance():
    field = BoreField.rectangle(2, 2, 6.0, 200.0)
    resp = FieldGroundResponse(field, k_s=K_S, alpha_s=ALPHA, m_flow_total=M_24LPM, t_max_s=YEAR, num=30)
    assert resp.boundary_condition == "UBWT"
    np.testing.assert_array_equal(resp.m_flow_boreholes, np.full(4, M_24LPM / 4))
    R_star = BoreholePipe().effective_resistance(200.0, r_b=R_B, k_s=K_S, m_flow_borehole=M_24LPM / 4)
    assert resp.R_b == pytest.approx(R_star, rel=1e-10)
    legacy = GFunction(2, 2, 6.0, 200.0, 0.0, R_B, ALPHA, K_S, YEAR, 3600.0, num=30)
    np.testing.assert_array_equal(resp.g.y, legacy.y)
    for pipe in (BoreholePipe(kind="double_u_parallel"), BoreholePipe.coaxial()):
        r = FieldGroundResponse(field, pipe, k_s=K_S, alpha_s=ALPHA, m_flow_total=M_24LPM, t_max_s=YEAR, num=20)
        ref = pipe.effective_resistance(200.0, r_b=R_B, k_s=K_S, m_flow_borehole=M_24LPM / 4)
        assert r.R_b == pytest.approx(ref, rel=1e-10)


def test_auto_boundary_condition():
    kw = dict(k_s=K_S, alpha_s=ALPHA, m_flow_total=M_24LPM, t_max_s=YEAR, num=20)
    assert (
        FieldGroundResponse(BoreField.from_coordinates(_IRREGULAR_XY, _IRREGULAR_H), **kw).boundary_condition == "MIFT"
    )
    two = BoreField.from_coordinates([[0, 0], [6, 0]], 200.0)
    assert FieldGroundResponse(two, connectivity=[-1, 0], **kw).boundary_condition == "MIFT"
    assert FieldGroundResponse(two, **kw).boundary_condition == "UBWT"
    assert (
        FieldGroundResponse(BoreField.from_coordinates([[0, 0], [6, 0]], 200.0, D=[0.0, 2.0]), **kw).boundary_condition
        == "MIFT"
    )
    assert FieldGroundResponse(two, boundary_condition="mift", **kw).boundary_condition == "MIFT"


def _check_mift_identity(resp: FieldGroundResponse, method: str, times: np.ndarray) -> None:
    """(T_in + T_out)/2 − T_b* = −q'·R_b with T_b* = T_s − q'·g, from pygfunction's network model.

    The wall temperatures come from pygfunction's MIFT solution (profiles);
    the fluid temperatures are recomputed from them with the network's own
    inlet/outlet relations, independently of the solver's g definition.
    """
    field = resp.field
    network = resp.network.to_pygfunction(field)
    gfunc = gt.gfunction.gFunction(
        network,
        ALPHA,
        time=times,
        method=method,
        boundary_condition="MIFT",
        m_flow_network=resp.m_flow_total,
        cp_f=c_w,
        options={"profiles": True},
    )
    ours = GFunction.from_field(
        field, ALPHA, K_S, YEAR, boundary_condition="MIFT", network=resp.network, method=method, num=len(times)
    )
    np.testing.assert_allclose(ours.y[1:], np.asarray(gfunc.gFunc) / (2 * np.pi * K_S), rtol=1e-9)
    T_s, Q = 15.0, 6000.0  # field heat extraction [W]
    q = Q / field.total_length
    solver = gfunc.solver
    n_seg = solver.nBoreSegments
    for p, t in enumerate(times):
        T_b = T_s - q * np.asarray(solver.T_b[:, p]) / (2 * np.pi * K_S)
        T_in = network.get_network_inlet_temperature(
            Q, T_b, resp.m_flow_total, c_w, n_seg, segment_ratios=solver.segment_ratios
        )
        T_out = network.get_network_outlet_temperature(
            T_in, T_b, resp.m_flow_total, c_w, n_seg, segment_ratios=solver.segment_ratios
        )
        assert float(T_out - T_in) == pytest.approx(Q / (resp.m_flow_total * c_w), abs=1e-9)  # heat balance [K]
        T_f = 0.5 * float(T_in + T_out)
        T_b_star = T_s - q * float(resp.g(t))
        assert T_f - T_b_star == pytest.approx(-q * resp.R_b, abs=1e-8)  # [K]
        # Heat per borehole from the network equals the load shares.
        Q_seg = network.get_borehole_heat_extraction_rate(
            T_in, T_b, resp.m_flow_total, c_w, n_seg, segment_ratios=solver.segment_ratios
        )
        bounds = np.cumsum([0, *n_seg])
        Q_bh = np.array([np.sum(Q_seg[bounds[i] : bounds[i + 1]]) for i in range(field.n_boreholes)])
        np.testing.assert_allclose(Q_bh / Q, resp.borehole_load_shares(t)[:, 0], atol=1e-9)


def test_mift_identity_series_pair():
    field = BoreField.from_coordinates([[0, 0], [6, 0]], 200.0)
    resp = FieldGroundResponse(
        field, k_s=K_S, alpha_s=ALPHA, m_flow_total=M_24LPM, t_max_s=YEAR, connectivity=[-1, 0], num=25
    )
    _check_mift_identity(resp, "similarities", resp.g.x[1:])


def test_mift_identity_irregular_parallel():
    field = BoreField.from_coordinates(_IRREGULAR_XY, _IRREGULAR_H)
    resp = FieldGroundResponse(field, k_s=K_S, alpha_s=ALPHA, m_flow_total=M_24LPM, t_max_s=YEAR, num=25)
    assert resp.boundary_condition == "MIFT"
    _check_mift_identity(resp, "detailed", resp.g.x[1:])


def test_series_versus_parallel_pair():
    field = BoreField.from_coordinates([[0, 0], [6, 0]], 200.0)
    kw = dict(k_s=K_S, alpha_s=ALPHA, m_flow_total=M_24LPM, t_max_s=30 * YEAR, boundary_condition="MIFT", num=30)
    par = FieldGroundResponse(field, **kw)
    ser = FieldGroundResponse(field, connectivity=[-1, 0], **kw)
    np.testing.assert_array_equal(par.m_flow_boreholes, [M_24LPM / 2] * 2)
    np.testing.assert_array_equal(ser.m_flow_boreholes, [M_24LPM] * 2)
    # Twice the flow per borehole: better convection, smaller field resistance.
    assert ser.R_b < par.R_b
    times = np.array([86400.0, YEAR, 30 * YEAR])
    assert np.all(np.abs(ser.g(times) / par.g(times) - 1) > 1e-3)
    s_par, s_ser = par.borehole_load_shares(times), ser.borehole_load_shares(times)
    np.testing.assert_allclose(s_par, 0.5, rtol=1e-12)
    # The upstream borehole meets the colder (extraction) fluid and takes more.
    assert np.all(s_ser[0] > 0.5) and np.all(s_ser[0] > s_ser[1])


def test_network_and_response_validation():
    field = BoreField.from_coordinates([[0, 0], [6, 0], [12, 0]], 100.0)
    kw = dict(k_s=K_S, alpha_s=ALPHA, m_flow_total=M_24LPM, t_max_s=YEAR, num=10)
    for conn, match in (
        ([-1, 0], "one entry per borehole"),
        ([-1, 3, 1], "borehole index"),
        ([-1, 1, 0], "itself"),
        ([1, 2, 0], "loop"),
        ([-1, 0, 0], "more than one"),
    ):
        with pytest.raises(ValueError, match=match):
            FieldGroundResponse(field, connectivity=conn, **kw)
    with pytest.raises(ValueError, match="m_flow_total"):
        FieldGroundResponse(field, **{**kw, "m_flow_total": 0.0})
    with pytest.raises(ValueError, match="boundary_condition"):
        FieldGroundResponse(field, boundary_condition="UBHT", **kw)
    with pytest.raises(ValueError, match="borehole wall"):
        FieldGroundResponse(BoreField.from_coordinates([[0, 0], [6, 0]], 100.0, r_b=0.03), **kw)
    net = BoreNetwork(BoreholePipe(), k_s=K_S, m_flow_total=M_24LPM)
    with pytest.raises(ValueError, match="needs a network"):
        GFunction.from_field(field, ALPHA, K_S, YEAR, boundary_condition="MIFT")
    with pytest.raises(ValueError, match="MIFT"):
        GFunction.from_field(field, ALPHA, K_S, YEAR, network=net)
    with pytest.raises(ValueError, match="must equal k_s"):
        GFunction.from_field(field, ALPHA, 3.0, YEAR, boundary_condition="MIFT", network=net)
    with pytest.raises(ValueError, match="t_max_s"):
        GFunction.from_field(field, ALPHA, K_S, -1.0)
    with pytest.raises(ValueError, match="permutation"):
        net.to_pygfunction(field, order=[0, 0, 1])
