"""Role-specific calibration preserves physical settings and applies complete fits only."""

from dataclasses import asdict
from random import Random

import pytest

from tmhp.calibration import EfficiencyScales, fit_compressor, predict_catalogue, scaled_efficiencies


class AnalyticHeatPump:
    """A deterministic heat balance exposing the same public boundary as ASHP."""

    def __init__(self):
        """Use known constant efficiencies and a fixed 20 W indoor fan."""
        self.eta_cmp_vol = 0.8
        self.eta_cmp_isen = 0.7
        self.eta_cmp = 0.9
        self.rps_min = 10.0
        self.rps_max = 100.0
        self.V_cmp_ref = 10e-6
        self.UA_iu_rated = 300.0
        self.calls = 0

    def analyze_steady(self, Q_r_iu, T0, T_a_room=None, **kwargs):
        """Return a consistent synthetic cycle with net heat including indoor fan heat."""
        self.calls += 1
        pr = 3.0
        n = abs(Q_r_iu) / 50 / self._value(self.eta_cmp_vol, pr, 50)
        v = self._value(self.eta_cmp_vol, pr, n)
        isen = self._value(self.eta_cmp_isen, pr, n)
        em = self._value(self.eta_cmp, pr, n)
        power = abs(Q_r_iu) / (5 * isen * em) + 20
        heat = abs(Q_r_iu) + (20 if Q_r_iu < 0 else -20)
        return {
            "converged": True,
            "failure_reason": "none",
            "hp_is_on": True,
            "Q_a_iu_in [W]": 0,
            "Q_a_iu_out [W]": heat if Q_r_iu < 0 else -heat,
            "E_iu_fan [W]": 20,
            "E_tot [W]": power,
            "E_cmp [W]": power - 20,
            "cop_sys [-]": heat / power,
            "cmp_rpm [rpm]": n * 60,
            "pr_cmp [-]": pr,
            "eta_cmp_vol [-]": v,
            "eta_cmp_isen [-]": isen,
            "eta_cmp [-]": em,
            "h_ref_cmp_in [J/kg]": 400000,
            "h_ref_cmp_out [J/kg]": 400000 + 50000 / isen,
            "m_dot_ref [kg/s]": 0.02,
        }

    @staticmethod
    def _value(value, pr, speed):
        """Evaluate scalar or two-input callable through the model's public contract."""
        return value(pr, speed) if callable(value) else value


def measured_points(scale=1.12):
    """Generate synthetic net heat/COP observations from a known efficiency scale."""
    hp = AnalyticHeatPump()
    hp.eta_cmp_isen *= scale
    template = [(20.0, 7.0, 1200.0, 1.0), (20.0, 12.0, 2500.0, 1.0)]
    predictions = predict_catalogue(hp, template)
    return [(p.T_indoor_C, p.T_outdoor_C, p.Q_requested_W, p.COP_model) for p in predictions]


def test_known_overall_scale_is_recovered_without_changing_other_parameters():
    """COP observations identify one scale while volumetric/loss scales stay fixed."""
    hp = AnalyticHeatPump()
    report = fit_compressor(hp, measured_points(), lambda_bounds={"overall_efficiency": (0.8, 1.3)})
    assert report.applied
    assert report.candidate_scales.lambda_eta_ov == pytest.approx(1.12, abs=2e-4)
    assert report.candidate_scales.lambda_eta_v == 1
    assert report.candidate_scales.lambda_f_loss == 1
    assert report.final_cop_rmse < 1e-4
    assert hp.V_cmp_ref == 10e-6 and hp.UA_iu_rated == 300
    assert hp.calls == 0


def test_refit_does_not_multiply_the_baseline_twice():
    """Repeated fits retain the original map and absolute scale rather than compounding."""
    hp = AnalyticHeatPump()
    first = fit_compressor(hp, measured_points())
    second = fit_compressor(hp, measured_points())
    assert second.applied
    assert second.candidate_scales.lambda_eta_ov == pytest.approx(first.candidate_scales.lambda_eta_ov, abs=2e-4)


def test_explicit_ossorio_baseline_version_is_inferred_in_the_fit_report():
    """Opting into the named research map retains its provenance without a manual label."""
    from tmhp.compressor_baseline import BASELINE_VERSION, ossorio_r290_efficiencies

    hp = AnalyticHeatPump()
    for name, efficiency in ossorio_r290_efficiencies().items():
        setattr(hp, name, efficiency)
    initial = predict_catalogue(hp, [(20, 7, 1200, 3)])[0]
    report = fit_compressor(hp, [(20, 7, 1200, initial.COP_model)])
    assert report.applied
    assert report.baseline_label == BASELINE_VERSION


def test_common_version_attribute_remains_a_baseline_label_fallback():
    """Existing custom maps with a version attribute retain automatic provenance."""
    hp = AnalyticHeatPump()

    def volume(pr, speed):
        """Return a constant test map through a callable carrying provenance."""
        return 0.8

    volume.version = "example-map-v2"
    hp.eta_cmp_vol = volume
    report = fit_compressor(hp, measured_points())
    assert report.applied
    assert report.baseline_label == "example-map-v2"


def test_failed_final_tolerance_preserves_efficiencies_and_previous_fit():
    """A failed fit reports candidate values without partially committing them."""
    hp = AnalyticHeatPump()
    old = (hp.eta_cmp_vol, hp.eta_cmp_isen, hp.eta_cmp)
    points = measured_points()
    points[1] = (*points[1][:3], points[1][3] * 1.2)
    report = fit_compressor(hp, points, max_cop_rmse=1e-5)
    assert not report.applied
    assert report.reason == "fit_tolerance_not_met"
    assert (hp.eta_cmp_vol, hp.eta_cmp_isen, hp.eta_cmp) == old
    assert not hasattr(hp, "compressor_fit_state")


def test_discharge_constraint_is_checked_after_overall_fit():
    """A COP fit requiring eta_is above one is rejected unless loss correction resolves it."""
    hp = AnalyticHeatPump()
    points = measured_points(scale=1.45)
    report = fit_compressor(hp, points, lambda_bounds={"overall_efficiency": (1, 1.5)})
    assert not report.applied
    assert any("discharge_state" in p.constraint_violations for p in report.final_points)


def test_heat_loss_adjustment_only_changes_when_discharge_constraint_requires_it():
    """A selected loss scale stays one when the original discharge constraint already passes."""
    hp = AnalyticHeatPump()
    report = fit_compressor(hp, measured_points(), parameters=("overall_efficiency", "heat_loss_fraction"))
    assert report.applied
    assert report.candidate_scales.lambda_f_loss == 1


def test_net_heat_matches_catalogue_in_both_modes():
    """The adapter corrects coil duty for fan heat instead of calibrating against wrong heat."""
    for mode in ("heating", "cooling"):
        hp = AnalyticHeatPump()
        p = predict_catalogue(hp, [(20, 7, 1200, 3)], mode=mode)[0]
        assert p.Q_model_W == pytest.approx(1200, abs=0.1)
        assert p.valid
        assert hp.calls == 0


def test_capacity_or_speed_failure_keeps_all_rows():
    """An invalid row cannot disappear from the objective or appear as a successful fit."""
    hp = AnalyticHeatPump()
    points = [(20, 7, 1000, 3), (20, 7, 20000, 3)]
    report = fit_compressor(hp, points)
    assert not report.applied
    assert len(report.final_points) == 2
    assert report.final_cop_rmse is None


def test_invalid_points_and_parameters_are_rejected_before_mutation():
    """Malformed catalogues and unknown parameter names are explicit input errors."""
    hp = AnalyticHeatPump()
    for points in ([], [(20, 7, -1, 3)], [(20, float("nan"), 1000, 3)], [(1, 2, 3)]):
        with pytest.raises(ValueError):
            fit_compressor(hp, points)
    with pytest.raises(ValueError):
        fit_compressor(hp, [(20, 7, 1000, 3)], parameters=("unknown",))
    assert hp.calls == 0


def test_unity_transformation_preserves_energy_identities_over_seeded_inputs():
    """Unity scales preserve each efficiency and its product throughout a random valid domain."""
    rng = Random(761)
    for _ in range(50):
        v, isen, em = (rng.uniform(0.3, 0.99) for _ in range(3))
        funcs = scaled_efficiencies((v, isen, em), EfficiencyScales())
        actual = tuple(f(3, 50) for f in funcs)
        assert actual == pytest.approx((v, isen, em))
        assert actual[1] * actual[2] == pytest.approx(isen * em)


def test_report_is_json_serializable_without_nonfinite_values():
    """Missing predictions become null with a reason, never NaN or Infinity."""
    import json

    hp = AnalyticHeatPump()
    report = fit_compressor(hp, [(20, 7, 20000, 3)])
    json.dumps(asdict(report), allow_nan=False)


def test_selected_loss_adjustment_can_resolve_discharge_violation():
    """A global loss reduction restores admissibility without refitting it to COP."""
    hp = AnalyticHeatPump()
    report = fit_compressor(
        hp,
        measured_points(scale=1.45),
        parameters=("overall_efficiency", "heat_loss_fraction"),
        lambda_bounds={"overall_efficiency": (1, 1.5)},
    )
    assert report.applied
    assert report.candidate_scales.lambda_eta_ov == pytest.approx(1.45, abs=2e-4)
    assert report.candidate_scales.lambda_f_loss == pytest.approx(0.8)
    assert all(p.valid and p.h2_J_kg >= p.h2s_J_kg for p in report.final_points)


def test_selected_volumetric_adjustment_restores_speed_with_minimal_grid_change():
    """Volumetric feasibility uses the nearest admissible grid point without changing other scales."""
    hp = AnalyticHeatPump()
    report = fit_compressor(hp, [(20, 7, 4200, 3)], parameters=("volumetric_efficiency",))
    assert report.applied
    assert report.candidate_scales.lambda_eta_v == pytest.approx(1.05)
    assert report.candidate_scales.lambda_eta_ov == 1
    assert report.candidate_scales.lambda_f_loss == 1
    assert report.final_points[0].speed_rps <= 100


def test_later_speed_recovery_cannot_apply_a_fit_without_any_valid_cop_candidate():
    """A failed COP stage cannot be converted to fitting success by auxiliary recovery."""
    hp = AnalyticHeatPump()
    original = (hp.eta_cmp_vol, hp.eta_cmp_isen, hp.eta_cmp)
    report = fit_compressor(
        hp,
        [(20, 7, 4200, 3.48603478)],
        parameters=("overall_efficiency", "volumetric_efficiency"),
    )
    assert not report.applied
    assert not report.success
    assert report.reason == "cop_fit_no_feasible_candidate"
    assert report.stages[0].status == "no_feasible_candidate"
    assert report.stages[0].cop_rmse is None
    assert report.candidate_scales.lambda_eta_v > 1
    assert report.final_points[0].valid
    assert (hp.eta_cmp_vol, hp.eta_cmp_isen, hp.eta_cmp) == original


def test_negative_baseline_loss_cannot_be_hidden_by_zero_loss_scale():
    """A negative original effective loss is a baseline-domain error, not a fitting knob."""
    hp = AnalyticHeatPump()
    hp.eta_cmp = 1.1
    report = fit_compressor(hp, [(20, 7, 1200, 3)], parameters=("heat_loss_fraction",))
    assert not report.applied
    assert "baseline_heat_loss_domain" in report.final_points[0].constraint_violations


def test_public_ashp_prediction_reproduces_real_net_heat_without_changing_model():
    """The production CoolProp/HX solver is exercised through the new public entry point."""
    from tmhp import AirSourceHeatPump

    hp = AirSourceHeatPump(
        ref="R32",
        hp_capacity=3500,
        V_cmp_ref=10.3e-6,
        eta_cmp_vol=0.9,
        eta_cmp_isen=0.7,
        eta_cmp=0.9,
        UA_iu_rated=400,
        UA_ou_rated=800,
        rps_min=15,
        rps_max=150,
    )
    p = hp.predict_catalogue([(20, 8, 2500, 4)])[0]
    assert p.valid, p
    assert p.Q_model_W == pytest.approx(2500, rel=1e-4)
    assert p.power_W > p.compressor_power_W > 0
    assert p.COP_model == pytest.approx(p.Q_model_W / p.power_W)
    assert hp.eta_cmp_isen == 0.7


def test_auxiliary_change_is_rechecked_against_final_cop_tolerance():
    """A loss adjustment that changes COP cannot reuse the earlier successful RMSE."""

    class LossSensitiveHeatPump(AnalyticHeatPump):
        """Expose a heating-like coupling between effective loss and COP."""

        def analyze_steady(self, Q_r_iu, T0, T_a_room=None, **kwargs):
            """Return a complete state whose power changes with effective loss."""
            state = super().analyze_steady(Q_r_iu, T0, T_a_room, **kwargs)
            loss = 1 - state["eta_cmp [-]"]
            state["E_tot [W]"] *= 1 + loss
            return state

    observed = LossSensitiveHeatPump()
    observed.eta_cmp_isen *= 1.45
    p = predict_catalogue(observed, [(20, 7, 1200, 3)])[0]
    hp = LossSensitiveHeatPump()
    report = fit_compressor(
        hp,
        [(20, 7, 1200, p.COP_model)],
        max_cop_rmse=1e-5,
        parameters=("overall_efficiency", "heat_loss_fraction"),
    )
    assert not report.applied
    assert report.reason == "fit_tolerance_not_met"
    assert report.stages[0].cop_rmse < 1e-4
    assert report.final_cop_rmse > 1e-2
    assert hp.eta_cmp_isen == 0.7 and hp.eta_cmp == 0.9


def test_overriding_an_applied_map_requires_an_explicit_new_baseline_instance():
    """Externally changed callables cannot silently mix with a previous fitted baseline."""
    hp = AnalyticHeatPump()
    fit_compressor(hp, measured_points())
    hp.eta_cmp_vol = 0.85
    with pytest.raises(ValueError, match="new model"):
        fit_compressor(hp, measured_points())


def test_trace_records_actual_grid_order_cache_reuse_and_complete_point_results():
    """Trace distinguishes solver work from cache reads and exposes only actual objective returns."""
    import json

    report = fit_compressor(AnalyticHeatPump(), measured_points())
    trace = report.trace
    assert [entry.sequence for entry in trace] == list(range(1, len(trace) + 1))
    assert trace[0].phase == "baseline" and not trace[0].cache_hit
    grid = [entry for entry in trace if entry.phase == "cop_grid"]
    assert [entry.scales.lambda_eta_ov for entry in grid] == [0.5, 0.625, 0.75, 0.875, 1, 1.125, 1.25, 1.375, 1.5]
    assert grid[4].cache_hit and grid[4].candidate_id == trace[0].candidate_id
    first_by_id = {}
    for entry in trace:
        assert len(entry.points) == len(report.initial_points)
        if entry.cache_hit:
            first = first_by_id[entry.candidate_id]
            assert entry.scales == first.scales and entry.points is first.points
        else:
            assert entry.candidate_id == len(first_by_id) + 1
            first_by_id[entry.candidate_id] = entry
        if entry.phase in {"cop_grid", "cop_optimizer", "cop_optimizer_result"}:
            assert entry.decision == "objective_returned"
            assert entry.objective_value == (entry.objective_rmse if entry.objective_rmse is not None else 1e12)
        else:
            assert entry.objective_value is None
    assert len(first_by_id) == report.evaluations
    assert len(trace) > report.evaluations
    assert any(entry.phase == "cop_optimizer" for entry in trace)
    assert trace[-1].phase == "final" and trace[-1].decision == "applied"
    assert trace[-1].scales == report.applied_scales
    assert trace[-1].points == report.final_points
    json.dumps(asdict(report), allow_nan=False)


def test_trace_shows_unchanged_volume_and_real_loss_feasibility_decisions():
    """A discharge repair keeps rejected candidates and never pretends unchanged volume was fitted."""
    report = fit_compressor(
        AnalyticHeatPump(),
        measured_points(scale=1.45),
        parameters=("overall_efficiency", "volumetric_efficiency", "heat_loss_fraction"),
    )
    assert report.applied
    volume = [entry for entry in report.trace if entry.phase.startswith("volumetric_")]
    assert [entry.phase for entry in volume] == ["volumetric_check", "volumetric_selection"]
    assert all(entry.decision == "retained" and entry.scales.lambda_eta_v == 1 for entry in volume)
    loss = [entry for entry in report.trace if entry.phase == "loss_repair"]
    assert loss[0].decision == "rejected" and "discharge_state" in loss[0].constraint_violations
    assert loss[-1].decision == "selected" and loss[-1].constraint_rmse is not None
    assert loss[-1].scales.lambda_f_loss == report.applied_scales.lambda_f_loss < 1
    cop = next(entry for entry in report.trace if entry.phase == "cop_selection")
    assert cop.objective_rmse is not None and cop.constraint_rmse is None


def test_trace_retains_failed_final_decision_and_operating_penalties():
    """An unavailable operating candidate gets a penalty, while failed application retains original maps."""
    hp = AnalyticHeatPump()
    original = (hp.eta_cmp_vol, hp.eta_cmp_isen, hp.eta_cmp)
    report = fit_compressor(hp, [(20, 7, 10000, 4)])
    assert not report.applied
    scored = [entry for entry in report.trace if entry.decision == "objective_returned"]
    assert scored and all(entry.objective_rmse is None and entry.objective_value == 1e12 for entry in scored)
    assert report.trace[-1].decision == "not_applied"
    assert report.trace[-1].reason == report.reason == "cop_fit_no_feasible_candidate"
    assert report.trace[-1].constraint_violations
    assert (hp.eta_cmp_vol, hp.eta_cmp_isen, hp.eta_cmp) == original


def test_trace_does_not_add_or_reorder_solver_calls_on_cached_candidates():
    """An injected recording model observes exactly two fan-net matching calls per unique point."""
    import copy

    import numpy as np

    calls = []

    class RecordingHeatPump(AnalyticHeatPump):
        """Expose only solver-call observations across the fitter's isolated model copy."""

        def __deepcopy__(self, memo):
            """Keep scalar model state isolated while the test observer remains shared."""
            return copy.copy(self)

        def analyze_steady(self, Q_r_iu, T0, T_a_room=None, **kwargs):
            """Record actual physics inputs before returning the same analytic heat balance."""
            calls.append(
                (
                    Q_r_iu,
                    self._value(self.eta_cmp_vol, 3, 50),
                    self._value(self.eta_cmp_isen, 3, 50),
                    self._value(self.eta_cmp, 3, 50),
                )
            )
            return super().analyze_steady(Q_r_iu, T0, T_a_room, **kwargs)

    report = fit_compressor(
        RecordingHeatPump(),
        measured_points(scale=1.45),
        parameters=("overall_efficiency", "volumetric_efficiency", "heat_loss_fraction"),
    )
    expected = []
    for entry in report.trace:
        if entry.cache_hit:
            continue
        scales = entry.scales
        em = 1 - scales.lambda_f_loss * (1 - 0.9)
        isen = scales.lambda_eta_ov * 0.7 * 0.9 / em
        for point in entry.points:
            for duty in (-point.Q_requested_W, -point.Q_requested_W + 20):
                expected.append((duty, 0.8 * scales.lambda_eta_v, isen, em))
    np.testing.assert_allclose(calls, expected, rtol=1e-13, atol=1e-10)
