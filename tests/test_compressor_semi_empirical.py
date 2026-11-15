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
            for value in (comp.eta_oi(pr, rps, st), comp.eta_em(pr, rps)):
                assert 0.0 < value <= 1.0, (pr, rps, value)
            # eta_isen = eta_oi / eta_em (constant split) exceeds one where eta_oi > eta_em; _eval_eff caps it
            # (variant b at low PR, see test_constant_split_can_put_eta_isen_above_one).
            assert comp.eta_isen(pr, rps, st) > 0.0
            if comp.validity_domain.contains(pr, rps):
                assert 0.0 < comp.eta_vol(pr, rps, st) <= 1.02, (pr, rps)


def test_constant_split_can_put_eta_isen_above_one():
    """Rotary split eta_em = 0.858 is a constant; variant b reaches eta_oi above it at low PR, so eta_oi / eta_em
    exceeds one. ``_eval_eff`` caps it at one (the package's range rule) -- a finding for the split, recorded here."""
    comp = SemiEmpiricalCompressor(
        CompressorSpec("rotary_twin_inverter", rotary_speed_variant="ossorio_anchored"), "R290", 55.0
    )
    pr, st = state_at_pr("R290", 1.5, 50.0)
    assert comp.eta_oi(pr, 30.0, st) > comp.eta_em_split
    assert comp.eta_isen(pr, 30.0, st) > 1.0
    assert _eval_eff(comp.eta_isen, pr, 30.0, st) == 1.0


def test_rotary_eta_v_is_not_bounded_by_its_form_and_such_a_point_is_infeasible():
    """The archive's linear eta_v form reaches zero at high PR and low relative speed (rotary c_Lv (PR-1)/n).

    Inside the data hull it stays positive (above). Outside, the value is not clamped: ``_eval_eff`` rejects it,
    so the operating point is infeasible -- a finding for the fit, recorded here so it is not lost.
    """
    comp = SemiEmpiricalCompressor(CompressorSpec("rotary_twin_inverter"), "R32", 55.0)
    pr, st = state_at_pr("R32", 11.0, 50.0)
    assert comp.eta_vol(pr, 15.0, st) <= 0.0
    with pytest.raises(InvalidCompressorEfficiency):
        _eval_eff(comp.eta_vol, pr, 15.0, st)


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
        assert on.eta_em(pr, rps) == pytest.approx(off.eta_em(pr, rps) * eta_inv)
        assert on.eta_isen(pr, rps, st) == off.eta_isen(pr, rps, st)
        assert on.eta_vol(pr, rps, st) == off.eta_vol(pr, rps, st)
    # eta_isen * eta_cmp without the drive is the terminal-basis eta_oi
    assert off.eta_isen(pr, 60.0, st) * off.eta_em(pr, 60.0) == pytest.approx(off.eta_oi(pr, 60.0, st))


def test_eta_oi_scale_scales_the_gas_side():
    a = SemiEmpiricalCompressor(CompressorSpec("scroll"), "R32", 50.0)
    b = SemiEmpiricalCompressor(CompressorSpec("scroll", eta_oi_scale=0.9), "R32", 50.0)
    pr, st = state_at("R32", 0.0, 50.0)
    assert b.eta_isen(pr, 40.0, st) == pytest.approx(0.9 * a.eta_isen(pr, 40.0, st))
    assert b.eta_em(pr, 40.0) == a.eta_em(pr, 40.0)


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
    assert abs(ratios["ossorio_anchored"][0] - 0.97) < abs(ratios["archive"][0] - 0.97)
    assert ratios["archive"][1] < 1.0 and ratios["ossorio_anchored"][1] < 1.0


# ---------------------------------------------------------------------------------------------- call form
def test_state_less_call_raises_a_clear_error():
    comp = SemiEmpiricalCompressor(CompressorSpec(), "R32", 50.0)
    for model in (comp.eta_isen, comp.eta_vol):
        with pytest.raises(SuctionStateRequired, match="suction state"):
            model(3.0, 50.0)
        with pytest.raises(SuctionStateRequired):
            _eval_eff(model, 3.0, 50.0)
        with pytest.raises(SuctionStateRequired):
            efficiency_evaluator(model)(3.0, 50.0)
    # the drive quantity needs no state
    assert _eval_eff(comp.eta_em, 3.0, 50.0) == comp.eta_em(3.0, 50.0)


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
    assert r["eta_cmp [-]"] == pytest.approx(m.eta_cmp(pr, rps))
    assert r["eta_cmp_isen [-]"] == pytest.approx(r["eta_cmp_isen [-]"])
    assert r["efficiency_extrapolated"] in (True, False)
