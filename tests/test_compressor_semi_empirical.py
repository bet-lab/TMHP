"""Type-specific semi-empirical compressor models: archive equality, bounds, call form, wiring.

The archive is ``validation/coefficients/semi-empirical-2026-11-15/`` (coefficients.json and
rotary_speed_variants.json); the library carries its numbers in a generated block.
"""

from __future__ import annotations

import math
import warnings
from pathlib import Path

import CoolProp.CoolProp as CP
import numpy as np
import pandas as pd
import pytest

import tmhp
from tmhp import compressor_efficiency as ce
from tmhp._opt_utils import efficiency_evaluator
from tmhp.compressor_efficiency import (
    COMPRESSOR_TYPES,
    ROTARY_SPEED_VARIANTS,
    CompressorSpec,
    InvalidCompressorEfficiency,
    SemiEmpiricalCompressor,
    SuctionState,
    SuctionStateRequired,
    _eval_eff,
    efficiency_extrapolated,
)

ARCHIVE = Path(__file__).resolve().parents[1] / "validation" / "coefficients" / "semi-empirical-2026-11-15"


def state_at(ref: str, t_evap_c: float, t_cond_c: float, superheat_k: float = 10.0) -> tuple[float, SuctionState]:
    """(PR, suction state) at dew-point saturation temperatures, as the archive defines its states."""
    p1 = CP.PropsSI("P", "T", t_evap_c + 273.15, "Q", 1, ref)
    p2 = CP.PropsSI("P", "T", t_cond_c + 273.15, "Q", 1, ref)
    t1 = t_evap_c + 273.15 + superheat_k
    h1, s1, rho1 = (CP.PropsSI(k, "P", p1, "T", t1, ref) for k in ("H", "S", "D"))
    h2s = CP.PropsSI("H", "P", p2, "S", s1, ref)
    return p2 / p1, SuctionState(p1, rho1, h2s - h1)


def state_at_pr(ref: str, pr: float, t_cond_c: float, superheat_k: float = 10.0) -> tuple[float, SuctionState]:
    p2 = CP.PropsSI("P", "T", t_cond_c + 273.15, "Q", 1, ref)
    t_evap_c = CP.PropsSI("T", "P", p2 / pr, "Q", 1, ref) - 273.15
    return state_at(ref, t_evap_c, t_cond_c, superheat_k)


# ---------------------------------------------------------------------------------------------- the archive
def test_generated_block_equals_the_archive():
    from validation.compressor_maps.semi_empirical.emit import block_data

    data = block_data()
    assert data["SEMI_EMPIRICAL_VERSION"] == ce.SEMI_EMPIRICAL_VERSION
    assert data["SEMI_EMPIRICAL_TYPES"] == ce.SEMI_EMPIRICAL_TYPES
    assert data["SEMI_EMPIRICAL_ROTARY_SPEED_VARIANTS"] == ce.SEMI_EMPIRICAL_ROTARY_SPEED_VARIANTS
    assert data["SEMI_EMPIRICAL_DRIVE"] == ce.SEMI_EMPIRICAL_DRIVE


@pytest.mark.parametrize("ctype", COMPRESSOR_TYPES)
def test_eta_oi_reproduces_the_archive_grid(ctype):
    """grid_vs_shipped.csv: R410A, condensing dew point 45 degC, superheat 10 K; twin rotary at its R410A level."""
    grid = pd.read_csv(ARCHIVE / "grid_vs_shipped.csv")
    grid = grid[grid.type == ctype]
    n_rated = {"scroll": 60.0, "rotary_twin_inverter": 60.0, "rotary_single_fixed": 50.0}[ctype]
    comp = SemiEmpiricalCompressor(CompressorSpec(ctype), "R410A", n_rated)
    for row in grid.itertuples():
        pr, st = state_at_pr("R410A", row.PR, 45.0)
        assert comp.eta_oi(pr, row.N_rps, st) == pytest.approx(row.eta_oi_model, abs=3e-6)


def test_rating_reference_values():
    """coefficients.json eta_oi_at_rating_reference: R410A 7.2/54.4 degC, superheat 11.1 K, 60 rev/s (pooled level)."""
    import json

    coeffs = json.loads((ARCHIVE / "coefficients.json").read_text())
    pr, st = state_at("R410A", 7.2, 54.4, 11.1)
    for ctype, rated in (("scroll", 60.0), ("rotary_twin_inverter", 60.0), ("rotary_single_fixed", 50.0)):
        comp = SemiEmpiricalCompressor(CompressorSpec(ctype), "pooled", rated)
        expected = coeffs["types"][ctype]["eta_oi"]["eta_oi_at_rating_reference"]
        assert comp.eta_oi(pr, 60.0, st) == pytest.approx(expected, abs=3e-6)


# ---------------------------------------------------------------------------------------------- bounds and shape
SPECS = [CompressorSpec(t) for t in COMPRESSOR_TYPES] + [
    CompressorSpec(t, rotary_speed_variant="ossorio_anchored") for t in COMPRESSOR_TYPES if t != "scroll"
]


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: f"{s.compressor_type}-{s.rotary_speed_variant}")
@pytest.mark.parametrize("ref", ["R32", "R410A", "R290"])
def test_efficiencies_within_bounds(spec, ref):
    comp = SemiEmpiricalCompressor(spec, ref, 55.0)
    for pr_target in (1.5, 2.0, 3.0, 5.0, 8.0, 11.0):
        pr, st = state_at_pr(ref, pr_target, 50.0)
        for rps in (15.0, 30.0, 55.0, 90.0, 120.0):
            for value in (comp.eta_oi(pr, rps, st), comp.eta_em(pr, rps, st), comp.eta_isen(pr, rps, st)):
                assert 0.0 < value <= 1.0, (pr, rps, value)
            if comp.validity_domain.contains(pr, rps):
                assert 0.0 < comp.eta_vol(pr, rps, st) <= 1.02, (pr, rps)


# Envelope grid of the bound checks: PR beyond the single-stage limit (11) to 15; the lowest speed the models reach
# (rps_min 15 rev/s by default, any positive value accepted) against a rated speed up to the 150 rev/s rated-speed
# search bound, so relative speeds down to 0.05; absolute (scroll) speeds down to 2.5 rev/s.
ENV_PR = (1.05, 1.5, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 11.0, 13.0, 15.0)
ENV_RPS = (2.5, 5.0, 7.5, 10.0, 15.0, 20.0, 30.0, 45.0, 60.0, 90.0, 120.0, 150.0)


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: f"{s.compressor_type}-{s.rotary_speed_variant}")
@pytest.mark.parametrize("ref", ["R32", "R410A", "R290", "R134a"])
def test_eta_v_positive_and_bounded_over_the_envelope(spec, ref):
    """eta_v in (0, c0] for every PR and speed, falling with PR and (below the rated speed) with falling speed."""
    comp = SemiEmpiricalCompressor(spec, ref, 50.0)
    c0 = comp.eta_v_coefficients["c0"]
    grid = np.empty((len(ENV_PR), len(ENV_RPS)))
    for i, pr_target in enumerate(ENV_PR):
        pr, st = state_at_pr(ref, pr_target, 45.0 if ref != "R134a" else 60.0)
        for j, rps in enumerate(ENV_RPS):
            grid[i, j] = comp.eta_v(pr, rps, st)
            assert _eval_eff(comp.eta_vol, pr, rps, st) > 0.0
    assert np.all(grid > 0.0) and np.all(grid <= c0), grid
    assert np.all(np.diff(grid, axis=0) < 0.0)
    low = np.array(ENV_RPS) <= 50.0
    assert np.all(np.diff(grid[:, low], axis=1) > 0.0)


@pytest.mark.parametrize("ctype", COMPRESSOR_TYPES)
def test_split_is_bounded_for_any_eta_oi(ctype):
    """Loss-share split: 0 < eta_is <= 1 and 0 < eta_em <= 1 for every eta_oi in (0, 1], product eta_oi."""
    comp = SemiEmpiricalCompressor(CompressorSpec(ctype), "R32", 50.0)
    assert 0.0 <= comp.loss_share <= 1.0
    for eta_oi in np.r_[1e-4, np.linspace(0.01, 1.0, 100)]:
        eta_is, eta_em = comp.split(float(eta_oi))
        assert 0.0 < eta_is <= 1.0 + 1e-15 and 0.0 < eta_em <= 1.0 + 1e-15
        assert eta_is * eta_em == pytest.approx(eta_oi, rel=1e-12)
        assert eta_is >= eta_oi - 1e-15 and eta_em >= eta_oi - 1e-15


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: f"{s.compressor_type}-{s.rotary_speed_variant}")
def test_split_is_bounded_over_the_envelope(spec):
    comp = SemiEmpiricalCompressor(spec, "R32", 50.0)
    for pr_target in ENV_PR:
        pr, st = state_at_pr("R32", pr_target, 45.0)
        for rps in ENV_RPS:
            eta_is, eta_cmp = comp.eta_isen(pr, rps, st), comp.eta_em(pr, rps, st)
            assert 0.0 < eta_is <= 1.0 and 0.0 < eta_cmp <= 1.0, (pr, rps, eta_is, eta_cmp)


def test_split_stays_below_one_where_the_constant_split_did_not():
    """The first archive's constant rotary eta_em (0.858) put eta_oi / eta_em above one at low PR in variant b
    (R1234yf, pooled level, PR 2, 30 rev/s: eta_oi about 0.876)."""
    comp = SemiEmpiricalCompressor(
        CompressorSpec("rotary_twin_inverter", rotary_speed_variant="ossorio_anchored"), "R1234yf", 55.0
    )
    pr, st = state_at_pr("R1234yf", 2.0, 50.0)
    eta_oi = comp.eta_oi(pr, 30.0, st)
    assert eta_oi > 0.858
    assert comp.eta_isen(pr, 30.0, st) < 1.0
    assert comp.eta_isen(pr, 30.0, st) * comp.eta_em(pr, 30.0, st) == pytest.approx(eta_oi * comp.eta_inverter(30.0))


def test_rotary_eta_v_stays_positive_where_the_linear_form_reached_zero():
    """The first archive's linear eta_v reached zero at PR 11 and 15 rev/s (rated 55): the bounded form does not."""
    comp = SemiEmpiricalCompressor(CompressorSpec("rotary_twin_inverter"), "R32", 55.0)
    pr, st = state_at_pr("R32", 11.0, 50.0)
    assert 0.0 < comp.eta_vol(pr, 15.0, st) < comp.eta_vol(pr, 55.0, st)
    old = 0.989702 - 0.022817 * (pr ** (1.0 / comp._k(pr, st)) - 1.0) - 0.027815 * (pr - 1.0) / (15.0 / 55.0)
    assert old <= 0.0


def test_rotary_eta_v_level_matches_the_displacement_regression():
    """At each regression basis's reference condition (n = 1) the model eta_v is the basis's pooled eta_vol."""
    import json

    reg = json.loads((Path(tmhp.__file__).parent / "data" / "compressor_displacement_regression.json").read_text())
    for ctype, basis in (
        ("rotary_twin_inverter", "rotary_twin_inverter_60rps"),
        ("rotary_single_fixed", "rotary_single_fixed_50Hz"),
    ):
        b = reg["bases"][basis]
        cond = b["reference_condition"]
        comp = SemiEmpiricalCompressor(CompressorSpec(ctype), "R32", None)
        for ref in ("R32", "R410A"):
            pr, st = state_at(ref, cond["T_evap_C"], cond["T_cond_C"], cond["superheat_K"])
            assert comp.eta_v(pr, b["frequency_Hz"], st) == pytest.approx(b["eta_vol_rps_per_f"], rel=0.03), (
                ctype,
                ref,
            )


def test_rotary_eta_v_level_is_the_median_of_the_rating_points():
    pts = pd.read_csv(ARCHIVE / "eta_v_rating_points.csv")
    for ctype, basis, n in (
        ("rotary_twin_inverter", "rotary_twin_inverter_60rps", 41),
        ("rotary_single_fixed", "rotary_single_fixed_50Hz", 98),
    ):
        g = pts[pts.basis == basis]
        assert len(g) == n
        # the csv carries six significant digits
        assert ce.SEMI_EMPIRICAL_TYPES[ctype]["eta_v"]["c0"] == pytest.approx(g.c0.median(), abs=1e-5)
        # c0 of a point is its eta_v over the shape at its condition (n = 1)
        comp = SemiEmpiricalCompressor(CompressorSpec(ctype), "R32", None)
        unit = comp.eta_v_coefficients["c0"]
        for r in g.head(5).itertuples():
            shape = math.exp(-comp.eta_v_coefficients["parameters"].get("c_re", 0.0) * (r.PR ** (1.0 / r.k) - 1.0))
            p = comp.eta_v_coefficients["parameters"]
            shape /= 1.0 + p.get("c_L", 0.0) * (r.PR - 1.0) + p.get("c_s", 0.0) + p.get("c_f", 0.0)
            assert r.c0 * shape == pytest.approx(r.eta_v, rel=1e-5)
        assert unit > 0


def test_rotary_level_per_refrigerant_where_rating_points_exist():
    """R32 / R410A from LG + SCI, R290 from the Highly 2024 twins; other refrigerants take the pooled level."""
    import json

    coeffs = json.loads((ARCHIVE / "coefficients.json").read_text())
    for ctype, own in (("rotary_twin_inverter", {"R32", "R410A", "R290"}), ("rotary_single_fixed", {"R22", "R410A"})):
        level = coeffs["types"][ctype]["eta_oi"]["level"]
        used = {r for r, v in level["by_refrigerant"].items() if v["used_as_level"]}
        assert used == own
        for ref in ("R32", "R410A", "R290", "R22", "R134a", "R1234yf"):
            comp = SemiEmpiricalCompressor(CompressorSpec(ctype), ref, 60.0)
            expected = level["by_refrigerant"][ref]["a_median"] if ref in own else level["a_median"]
            assert comp.level == pytest.approx(expected, abs=1e-6), (ctype, ref)
    r290 = coeffs["types"]["rotary_twin_inverter"]["eta_oi"]["level"]["by_refrigerant"]["R290"]
    assert r290["machines"] == 7 and r290["sources"] == ["highly_catalogue_2024"]


def test_gmcc_check_uses_the_r290_level():
    """The GMCC inverter-input points (R290) against the model at the R290 level: an implied inverter efficiency."""
    import json

    coeffs = json.loads((ARCHIVE / "coefficients.json").read_text())
    comp = SemiEmpiricalCompressor(CompressorSpec("rotary_twin_inverter"), "R290", 60.0)
    for g in coeffs["checks"]["gmcc_inverter_input"]:
        pr, st = state_at("R290", g["T_evap_C"], g["T_cond_C"], 11.1)
        assert comp.eta_oi(pr, 60.0, st) == pytest.approx(g["model_terminal"], abs=2e-6)
        assert 0.85 < g["implied_inverter_eff"] < 1.0


def test_eta_v_uncertainty_above_the_data_is_recorded():
    import json

    coeffs = json.loads((ARCHIVE / "coefficients.json").read_text())
    unc = coeffs["types"]["rotary_twin_inverter"]["eta_v"]["uncertainty_above_data"]
    comp = SemiEmpiricalCompressor(CompressorSpec("rotary_twin_inverter"), "R32", None)
    for pr_target in (6.0, 8.0, 10.0):
        lo, hi = unc["range_R32_60rps_n1"][f"PR{pr_target:g}"]
        pr, st = state_at_pr("R32", pr_target, 50.0)
        assert lo - 1e-3 <= comp.eta_v(pr, 60.0, st) <= hi + 1e-3


def test_default_rotary_speed_shape_is_shao_informed():
    """The EN 14825 trajectory-shape rule (archive README) chose variant a; b stays selectable."""
    import json

    assert ROTARY_SPEED_VARIANTS == ("shao_informed", "ossorio_anchored")
    assert CompressorSpec().rotary_speed_variant == "shao_informed"
    var = json.loads((ARCHIVE / "rotary_speed_variants.json").read_text())
    assert var["default"] == "a"
    a = ce.SEMI_EMPIRICAL_ROTARY_SPEED_VARIANTS["shao_informed"]
    for ctype in ("rotary_twin_inverter", "rotary_single_fixed"):
        assert ce.SEMI_EMPIRICAL_TYPES[ctype]["parameters"] == a["parameters"]
        assert ce.SEMI_EMPIRICAL_TYPES[ctype]["level"] == a["level"][ctype]
        assert ce.SEMI_EMPIRICAL_TYPES[ctype]["level_by_refrigerant"] == a["level_by_refrigerant"][ctype]
    # the alternative is still built when asked for
    b = SemiEmpiricalCompressor(
        CompressorSpec("rotary_twin_inverter", rotary_speed_variant="ossorio_anchored"), "R32", 60.0
    )
    assert b.parameters == ce.SEMI_EMPIRICAL_ROTARY_SPEED_VARIANTS["ossorio_anchored"]["parameters"]


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: f"{s.compressor_type}-{s.rotary_speed_variant}")
def test_monotone_decreasing_in_pressure_ratio_beyond_the_data(spec):
    comp = SemiEmpiricalCompressor(spec, "R410A", 60.0)
    pr_data_max = max(v[0] for v in comp.validity_domain.vertices)
    for rps in (20.0, 40.0, 60.0, 90.0, 120.0):
        values = []
        for pr_target in np.arange(pr_data_max, 12.01, 0.25):
            pr, st = state_at_pr("R410A", float(pr_target), 45.0)
            values.append(comp.eta_oi(pr, rps, st))
        assert np.all(np.diff(values) < 0.0), (rps, values)


def test_inverter_is_on_the_electrical_side_only():
    on = SemiEmpiricalCompressor(CompressorSpec("rotary_twin_inverter"), "R32", 50.0)
    off = SemiEmpiricalCompressor(CompressorSpec("rotary_twin_inverter", inverter=False), "R32", 50.0)
    pr, st = state_at("R32", -2.0, 45.0)
    d = ce.SEMI_EMPIRICAL_DRIVE
    for rps in (20.0, 60.0, 100.0):
        eta_inv = 1.0 - d["lam"] * (1.0 + d["beta"] * (60.0 / rps - 1.0))
        assert on.eta_em(pr, rps, st) == pytest.approx(off.eta_em(pr, rps, st) * eta_inv)
        assert on.eta_isen(pr, rps, st) == off.eta_isen(pr, rps, st)
        assert on.eta_vol(pr, rps, st) == off.eta_vol(pr, rps, st)
    # eta_isen * eta_cmp without the drive is the terminal-basis eta_oi
    assert off.eta_isen(pr, 60.0, st) * off.eta_em(pr, 60.0, st) == pytest.approx(off.eta_oi(pr, 60.0, st))


def test_eta_oi_scale_scales_the_overall_efficiency_before_the_split():
    a = SemiEmpiricalCompressor(CompressorSpec("scroll"), "R32", 50.0)
    b = SemiEmpiricalCompressor(CompressorSpec("scroll", eta_oi_scale=0.9), "R32", 50.0)
    pr, st = state_at("R32", 0.0, 50.0)
    inv = b.eta_inverter(40.0)
    overall_a = a.eta_isen(pr, 40.0, st) * a.eta_em(pr, 40.0, st) / inv
    overall_b = b.eta_isen(pr, 40.0, st) * b.eta_em(pr, 40.0, st) / inv
    assert overall_a == pytest.approx(a.eta_oi(pr, 40.0, st))
    assert overall_b == pytest.approx(0.9 * overall_a)
    assert b.eta_isen(pr, 40.0, st) < a.eta_isen(pr, 40.0, st)
    assert b.eta_em(pr, 40.0, st) < a.eta_em(pr, 40.0, st)


def test_rotary_speed_is_relative_to_the_machine_rated_speed():
    pr, st = state_at("R32", 0.0, 45.0)
    rated = SemiEmpiricalCompressor(CompressorSpec("rotary_twin_inverter"), "R32", 47.0)
    solving = SemiEmpiricalCompressor(CompressorSpec("rotary_twin_inverter"), "R32", None)
    # at the rated speed the bound model equals the rated-speed-solve model (n = 1 in both)
    assert rated.eta_oi(pr, 47.0, st) == solving.eta_oi(pr, 47.0, st)
    assert rated.eta_vol(pr, 47.0, st) == solving.eta_vol(pr, 47.0, st)
    # the rated-speed-solve model sees every speed as rated
    assert solving.eta_oi(pr, 30.0, st) == solving.eta_oi(pr, 90.0, st)
    # scroll is absolute: no dependence on the rated speed
    s1 = SemiEmpiricalCompressor(CompressorSpec("scroll"), "R32", 47.0)
    s2 = SemiEmpiricalCompressor(CompressorSpec("scroll"), "R32", None)
    assert s1.eta_oi(pr, 30.0, st) == s2.eta_oi(pr, 30.0, st)


def test_variant_b_follows_ossorio_speed_shape_better_than_a():
    """Ossorio 2019: eta(30)/eta(60) about 0.97, eta(120)/eta(60) about 0.91 (R290, -7/50 degC)."""
    pr, st = state_at("R290", -7.0, 50.0, 5.0)
    ratios = {}
    for variant in ROTARY_SPEED_VARIANTS:
        c = SemiEmpiricalCompressor(CompressorSpec("rotary_twin_inverter", rotary_speed_variant=variant), "R290", 60.0)
        ratios[variant] = [c.eta_oi(pr, n, st) / c.eta_oi(pr, 60.0, st) for n in (30.0, 120.0)]
    assert abs(ratios["ossorio_anchored"][0] - 0.97) < abs(ratios["shao_informed"][0] - 0.97)
    assert ratios["shao_informed"][1] < 1.0 and ratios["ossorio_anchored"][1] < 1.0


# ---------------------------------------------------------------------------------------------- call form
def test_state_less_call_raises_a_clear_error():
    comp = SemiEmpiricalCompressor(CompressorSpec(), "R32", 50.0)
    # eta_cmp too: the loss-share split makes it depend on eta_oi, hence on the state
    for model in (comp.eta_isen, comp.eta_vol, comp.eta_em):
        assert model.needs_state
        with pytest.raises(SuctionStateRequired, match="suction state"):
            model(3.0, 50.0)
        with pytest.raises(SuctionStateRequired):
            _eval_eff(model, 3.0, 50.0)
        with pytest.raises(SuctionStateRequired):
            efficiency_evaluator(model)(3.0, 50.0)


def test_user_callables_keep_their_call_forms():
    pr, st = state_at("R32", 0.0, 45.0)
    calls = []

    def pr_only(p):
        return 0.7

    def pr_speed(p, n):
        return 0.5 + 0.002 * n

    class WithState:
        needs_state = True

        def __call__(self, p, n, state):
            calls.append(state)
            return 0.6

    stateful = WithState()
    for evaluate in (
        lambda m, s=None: _eval_eff(m, pr, 40.0, s),
        lambda m, s=None: efficiency_evaluator(m)(pr, 40.0, s),
    ):
        assert evaluate(pr_only) == 0.7 and evaluate(pr_only, st) == 0.7
        assert evaluate(pr_speed) == pytest.approx(0.58) and evaluate(pr_speed, st) == pytest.approx(0.58)
        assert evaluate(0.65) == 0.65
        assert evaluate(stateful, st) == 0.6
        with pytest.raises(SuctionStateRequired):
            evaluate(stateful)
    assert calls == [st, st]


def test_no_effective_exponent_is_an_infeasible_point_not_a_substitute():
    comp = SemiEmpiricalCompressor(CompressorSpec(), "R32", 50.0)
    with pytest.raises(InvalidCompressorEfficiency):
        comp.eta_isen(3.0, 50.0, SuctionState(1e6, 30.0, 0.0))


def test_validity_hull_flags_extrapolation():
    comp = SemiEmpiricalCompressor(CompressorSpec("scroll"), "R32", 50.0)
    assert not efficiency_extrapolated(comp.eta_isen, 3.0, 50.0)
    assert efficiency_extrapolated(comp.eta_isen, 9.5, 50.0)
    assert efficiency_extrapolated(comp.eta_isen, 3.0, 10.0)
    # a hull edge, not the bounding box: PR 7.5 at 90 rev/s is inside the box but outside the hull
    assert efficiency_extrapolated(comp.eta_isen, 7.5, 89.0)
    rot = SemiEmpiricalCompressor(CompressorSpec(), "R32", 50.0)
    assert efficiency_extrapolated(rot.eta_vol, 7.0, 60.0)
    assert not efficiency_extrapolated(rot.eta_vol, 3.0, 60.0)


# ---------------------------------------------------------------------------------------------- wiring
def _quiet(cls, **kwargs):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return cls(**kwargs)


@pytest.mark.parametrize(
    "cls",
    [
        tmhp.AirSourceHeatPump,
        tmhp.GroundSourceHeatPump,
        tmhp.AirSourceHeatPumpBoiler,
        tmhp.GroundSourceHeatPumpBoiler,
        tmhp.WaterSourceHeatPumpBoiler,
    ],
)
def test_default_is_the_twin_rotary_model(cls):
    m = _quiet(cls)
    assert m.compressor_type == CompressorSpec("rotary_twin_inverter")
    for name in ("eta_cmp_isen", "eta_cmp_vol", "eta_cmp"):
        model = getattr(m, name)
        assert isinstance(model, ce.SemiEmpiricalEfficiency)
        assert model.rps_rated == m.rps_rated


def test_compressor_type_selects_the_default_and_is_cached_apart():
    rot = _quiet(tmhp.AirSourceHeatPumpBoiler, ref="R32", hp_capacity=8000.0)
    scr = _quiet(tmhp.AirSourceHeatPumpBoiler, ref="R32", hp_capacity=8000.0, compressor_type="scroll")
    assert scr.eta_cmp_isen.compressor.spec.compressor_type == "scroll"
    assert rot.rps_rated != scr.rps_rated
    # an explicit efficiency still wins over the type
    fixed = _quiet(tmhp.AirSourceHeatPumpBoiler, ref="R32", hp_capacity=8000.0, compressor_type="scroll", eta_cmp=0.9)
    assert fixed.eta_cmp == 0.9


def test_unknown_compressor_type_is_refused():
    with pytest.raises(ValueError, match="compressor_type"):
        _quiet(tmhp.AirSourceHeatPumpBoiler, compressor_type="screw")


def test_rated_point_runs_at_unit_relative_speed():
    m = _quiet(tmhp.AirSourceHeatPumpBoiler, ref="R32", hp_capacity=8000.0)
    rs = m.reference_state
    comp = m.eta_cmp_isen.compressor
    assert comp._n(rs.rps_rated, "rel") == pytest.approx(1.0)
    assert math.isfinite(rs.eta_cmp_isen) and 0 < rs.eta_cmp_isen <= 1


def test_steady_result_reports_the_new_efficiencies():
    m = _quiet(tmhp.AirSourceHeatPumpBoiler, ref="R32", hp_capacity=8000.0)
    r = m.analyze_steady(T_tank_w=40.0, T0=7.0, Q_ref_tank=4000.0, return_dict=True)
    assert r["failure_reason"] == "none"
    pr, rps = r["pr_cmp [-]"], r["cmp_rpm [rpm]"] / 60.0
    assert 0.0 < r["eta_cmp [-]"] <= 1.0 and 0.0 < r["eta_cmp_isen [-]"] <= 1.0
    assert pr > 1.0
    # loss-share split: eta_em = eta_oi + f (1 - eta_oi) with eta_oi = eta_cmp_isen * eta_em
    comp = m.eta_cmp.compressor
    eta_em = r["eta_cmp [-]"] / comp.eta_inverter(rps)
    eta_oi = r["eta_cmp_isen [-]"] * eta_em
    assert eta_em == pytest.approx(eta_oi + comp.loss_share * (1.0 - eta_oi), rel=1e-9)
    assert r["efficiency_extrapolated"] in (True, False)
