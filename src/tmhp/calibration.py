"""Catalogue calibration for the existing ASHP class, without an API facade.

Only overall efficiency is fitted to COP. Volumetric efficiency and effective
heat loss are optional feasibility adjustments. Candidate evaluations use an
isolated model copy; unsuccessful fits never replace the caller's efficiencies.
"""

import copy
import inspect
import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any, Literal, Protocol

import numpy as np
from scipy.optimize import minimize_scalar

Parameter = Literal["overall_efficiency", "volumetric_efficiency", "heat_loss_fraction"]
Mode = Literal["heating", "cooling"]
Efficiency = float | Callable[..., float]
FittingPoint = tuple[float, float, float, float]

__all__ = [
    "EfficiencyScales",
    "PointResult",
    "FitStage",
    "FitReport",
    "Parameter",
    "Mode",
    "FittingPoint",
    "fit_compressor",
    "predict_catalogue",
    "scaled_efficiencies",
]

_FIELDS = {
    "overall_efficiency": "lambda_eta_ov",
    "volumetric_efficiency": "lambda_eta_v",
    "heat_loss_fraction": "lambda_f_loss",
}
_DEFAULT_BOUNDS = {
    "overall_efficiency": (0.5, 1.5),
    "volumetric_efficiency": (0.5, 1.5),
    "heat_loss_fraction": (0.0, 2.0),
}


class CatalogueModel(Protocol):
    """Public ASHP inputs and steady-state outputs needed by calibration."""

    eta_cmp_vol: Efficiency
    eta_cmp_isen: Efficiency
    eta_cmp: Efficiency
    rps_min: float
    rps_max: float

    def analyze_steady(
        self,
        Q_r_iu: float,
        T0: float,
        T_a_room: float | None = None,
        *,
        return_dict: bool = True,
        postprocess: bool = True,
        verbose: bool = True,
    ) -> Any:
        """Evaluate signed coil duty and return ASHP's documented result mapping."""
        ...


@dataclass(frozen=True)
class EfficiencyScales:
    """Absolute multipliers of the original map; unity is the uncalibrated map."""

    lambda_eta_v: float = 1.0
    lambda_eta_ov: float = 1.0
    lambda_f_loss: float = 1.0

    def __post_init__(self) -> None:
        """Reject nonfinite or nonphysical scale values at construction."""
        values = (self.lambda_eta_v, self.lambda_eta_ov, self.lambda_f_loss)
        if not all(math.isfinite(v) for v in values) or min(values[:2]) <= 0 or values[2] < 0:
            raise ValueError("Efficiency scales must be finite; efficiency scales > 0 and loss scale >= 0")


@dataclass(frozen=True)
class PointResult:
    """One requested catalogue row, retained even when the solver cannot supply it."""

    T_indoor_C: float
    T_outdoor_C: float
    Q_requested_W: float
    COP_catalogue: float
    Q_model_W: float | None = None
    COP_model: float | None = None
    power_W: float | None = None
    compressor_power_W: float | None = None
    speed_rps: float | None = None
    pressure_ratio: float | None = None
    eta_v: float | None = None
    eta_ov: float | None = None
    f_loss: float | None = None
    h1_J_kg: float | None = None
    h2_J_kg: float | None = None
    h2s_J_kg: float | None = None
    mass_flow_kg_s: float | None = None
    capacity_relative_error: float | None = None
    operating_valid: bool = False
    valid: bool = False
    failure_reason: str = "not_evaluated"
    constraint_violations: tuple[str, ...] = ()


@dataclass(frozen=True)
class FitStage:
    """An attempted stage with its outcome, unchanged or adjusted scales, and all rows."""

    role: str
    scales: EfficiencyScales
    cop_rmse: float | None
    changed: bool
    points: tuple[PointResult, ...]
    status: Literal["completed", "no_feasible_candidate"] = "completed"


@dataclass(frozen=True)
class FitReport:
    """Complete attempted fit; applied distinguishes accepted parameters from candidates."""

    success: bool
    applied: bool
    reason: str
    mode: Mode
    parameters: tuple[Parameter, ...]
    baseline_label: str
    initial_scales: EfficiencyScales
    candidate_scales: EfficiencyScales
    applied_scales: EfficiencyScales
    initial_cop_rmse: float | None
    final_cop_rmse: float | None
    initial_points: tuple[PointResult, ...]
    final_points: tuple[PointResult, ...]
    stages: tuple[FitStage, ...]
    evaluations: int
    lambda_bounds: tuple[tuple[str, float, float], ...]
    assumptions: tuple[str, ...] = (
        "COP-only data do not identify three independent compressor efficiencies",
        "eta_is <= 1 is the adopted calibration constraint, not a universal nonadiabatic compressor law",
        "Lambda bounds are numerical search settings, not manufacturer operating limits",
        "Input temperatures are dry-bulb; wet-bulb conversion must be documented by the caller",
    )


@dataclass(frozen=True)
class _AppliedFit:
    """Retain the original maps and last applied callables to prevent compound refits."""

    baseline: tuple[Efficiency, Efficiency, Efficiency]
    efficiencies: tuple[Efficiency, Efficiency, Efficiency]
    scales: EfficiencyScales
    baseline_label: str


def _efficiency(model: Efficiency, pr: float, speed: float) -> float:
    """Evaluate a scalar or PR/PR-speed model without swallowing model TypeErrors."""
    if not callable(model):
        return float(model)
    signature = inspect.signature(model)
    try:
        signature.bind(pr, speed)
    except TypeError:
        signature.bind(pr)
        return float(model(pr))
    return float(model(pr, speed))


def scaled_efficiencies(
    baseline: tuple[Efficiency, Efficiency, Efficiency], scales: EfficiencyScales
) -> tuple[Callable[[float, float], float], Callable[[float, float], float], Callable[[float, float], float]]:
    """Return eta_v, eta_is, eta_em callables with physical scales applied before solving.

    The original maps are ordered eta_v, eta_is, eta_em. The transformation is
    eta_ov=lambda_ov*eta_is0*eta_em0 and f=lambda_f*(1-eta_em0). Nonpositive
    transformed eta_em returns NaN so the existing solver rejects that state.
    Negative baseline heat loss remains visible to final feasibility checks.
    """

    def volumetric(pr: float, speed: float) -> float:
        """Scale volumetric flow efficiency without modifying the speed or displacement."""
        return scales.lambda_eta_v * _efficiency(baseline[0], pr, speed)

    def electrical(pr: float, speed: float) -> float:
        """Map effective electrical loss back to the existing energy conversion factor."""
        return 1 - scales.lambda_f_loss * (1 - _efficiency(baseline[2], pr, speed))

    def isentropic(pr: float, speed: float) -> float:
        """Recover the enthalpy-rise factor from overall efficiency and effective loss."""
        original_em = _efficiency(baseline[2], pr, speed)
        em = 1 - scales.lambda_f_loss * (1 - original_em)
        if not math.isfinite(em) or em <= 0:
            return float("nan")
        return scales.lambda_eta_ov * _efficiency(baseline[1], pr, speed) * original_em / em

    return volumetric, isentropic, electrical


def _parse_points(points: Iterable[FittingPoint], mode: Mode) -> tuple[FittingPoint, ...]:
    """Parse net-output catalogue rows once, refusing ambiguous or nonfinite input."""
    if mode not in ("heating", "cooling"):
        raise ValueError("mode must be 'heating' or 'cooling'")
    parsed = []
    try:
        iterator = iter(points)
    except TypeError as exc:
        raise ValueError("Catalogue points must be an iterable of four-value rows") from exc
    for row in iterator:
        if not hasattr(row, "__len__") or len(row) != 4:
            raise ValueError("Each point must contain (Tin_DB_C, Tout_DB_C, Q_net_W, COP)")
        try:
            ti, to, q, cop = (float(value) for value in row)
        except (ValueError, TypeError) as exc:
            raise ValueError("Catalogue coordinates must be numbers") from exc
        if not all(math.isfinite(value) for value in (ti, to, q, cop)) or min(q, cop) <= 0:
            raise ValueError("Catalogue values must be finite and Q_net_W/COP positive")
        parsed.append((ti, to, q, cop))
    if not parsed:
        raise ValueError("At least one catalogue point is required")
    return tuple(parsed)


def _finite(value: Any) -> float | None:
    """Convert a reported number to a finite float or an explicit missing value."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _point_result(model: CatalogueModel, point: FittingPoint, mode: Mode, capacity_tolerance: float) -> PointResult:
    """Match catalogue net delivery using repeated public coil-duty evaluations."""
    ti, to, q, cop = point
    blank = PointResult(ti, to, q, cop)
    coil = q
    state: Mapping[str, Any] = {}
    delivered: float | None = None
    for _ in range(10):
        try:
            state = model.analyze_steady(
                Q_r_iu=-coil if mode == "heating" else coil,
                T0=to,
                T_a_room=ti,
                return_dict=True,
                postprocess=False,
                verbose=False,
            )
        except (ValueError, ArithmeticError) as exc:
            return replace(blank, failure_reason=f"solver_error: {type(exc).__name__}: {exc}")
        if not isinstance(state, Mapping):
            return replace(blank, failure_reason="missing_solver_result")
        if not state.get("hp_is_on", False):
            return replace(blank, failure_reason=str(state.get("failure_reason", "off_or_invalid")))
        q_in = _finite(state.get("Q_a_iu_in [W]"))
        q_out = _finite(state.get("Q_a_iu_out [W]"))
        if q_in is None or q_out is None:
            return replace(blank, failure_reason="missing_net_heat")
        signed_heat = q_out - q_in
        delivered = signed_heat if mode == "heating" else -signed_heat
        if abs(delivered - q) <= capacity_tolerance * q:
            break
        if not state.get("converged", False) or state.get("capacity_clamped") == "max":
            break
        coil += q - delivered
        if coil <= 0:
            return replace(blank, Q_model_W=delivered, failure_reason="nonpositive_coil_duty")
    power = _finite(state.get("E_tot [W]"))
    speed_rpm = _finite(state.get("cmp_rpm [rpm]"))
    speed = speed_rpm / 60 if speed_rpm is not None else None
    pr = _finite(state.get("pr_cmp [-]"))
    v = _finite(state.get("eta_cmp_vol [-]"))
    isen = _finite(state.get("eta_cmp_isen [-]"))
    em = _finite(state.get("eta_cmp [-]"))
    h1 = _finite(state.get("h_ref_cmp_in [J/kg]"))
    h2 = _finite(state.get("h_ref_cmp_out [J/kg]"))
    hs = h1 + isen * (h2 - h1) if h1 is not None and h2 is not None and isen is not None else None
    eov = isen * em if isen is not None and em is not None else None
    loss = 1 - em if em is not None else None
    capacity_error = (delivered - q) / q if delivered is not None else None
    violations = []
    if capacity_error is None or abs(capacity_error) > capacity_tolerance:
        violations.append("net_capacity")
    if speed is None or not model.rps_min - 1e-7 <= speed <= model.rps_max + 1e-7:
        violations.append("speed_range")
    if any(value is None or value <= 0 for value in (v, eov, em)):
        violations.append("invalid_efficiency")
    if loss is None or not -1e-12 <= loss < 1:
        violations.append("heat_loss_domain")
    if isen is None or isen > 1 + 1e-7 or hs is None or h2 is None or h2 < hs - 1e-3:
        violations.append("discharge_state")
    solver_ok = bool(state.get("converged", False)) and state.get("failure_reason", "none") == "none"
    operating_valid = solver_ok and power is not None and power > 0 and delivered is not None and delivered > 0
    if not operating_valid:
        violations.append("solver_failure")
    predicted_cop = delivered / power if operating_valid and delivered is not None and power is not None else None
    return PointResult(
        ti,
        to,
        q,
        cop,
        delivered,
        predicted_cop,
        power,
        _finite(state.get("E_cmp [W]")),
        speed,
        pr,
        v,
        eov,
        loss,
        h1,
        h2,
        hs,
        _finite(state.get("m_dot_ref [kg/s]")),
        capacity_error,
        operating_valid,
        operating_valid and not violations,
        "none" if operating_valid and not violations else ",".join(violations),
        tuple(violations),
    )


def predict_catalogue(
    model: CatalogueModel, points: Iterable[FittingPoint], *, mode: Mode = "heating", capacity_tolerance: float = 1e-4
) -> tuple[PointResult, ...]:
    """Evaluate net-output catalogue points on a copy, without fitting or changing the model.

    Input rows are (indoor DB °C, outdoor DB °C, positive net output W, system
    COP). COP observations are retained for comparison, never used by the solver.
    Every requested row yields a result, including capacity or solver failures.
    """
    parsed = _parse_points(points, mode)
    if not math.isfinite(capacity_tolerance) or capacity_tolerance <= 0:
        raise ValueError("capacity_tolerance must be finite and positive")
    isolated = copy.deepcopy(model)
    return tuple(_point_result(isolated, p, mode, capacity_tolerance) for p in parsed)


def _rmse(points: tuple[PointResult, ...], *, require_constraints: bool = False) -> float | None:
    """Return all-row COP RMSE or no metric if any requested operating point fails."""
    for p in points:
        disallowed = set(p.constraint_violations) - {"discharge_state"}
        if not p.operating_valid or p.COP_model is None or disallowed or (require_constraints and not p.valid):
            return None
    return math.sqrt(sum((p.COP_model - p.COP_catalogue) ** 2 for p in points if p.COP_model is not None) / len(points))


def _bounds(
    supplied: Mapping[str, tuple[float, float]] | None, parameters: tuple[Parameter, ...]
) -> dict[str, tuple[float, float]]:
    """Validate caller search bounds without treating them as hardware limits."""
    result = dict(_DEFAULT_BOUNDS)
    if supplied:
        if set(supplied) - set(_FIELDS):
            raise ValueError("Unknown lambda_bounds parameter")
        result.update(supplied)
    for name in parameters:
        lower, upper = result[name]
        if not math.isfinite(lower) or not math.isfinite(upper) or lower >= upper:
            raise ValueError("Each lambda interval requires finite lower < upper")
        if lower < 0 or (name != "heat_loss_fraction" and lower == 0):
            raise ValueError("Efficiency bounds must be positive; loss bounds may include zero")
    return result


def fit_compressor(
    model: CatalogueModel,
    points: Iterable[FittingPoint],
    *,
    mode: Mode = "heating",
    parameters: tuple[Parameter, ...] = ("overall_efficiency",),
    lambda_bounds: Mapping[str, tuple[float, float]] | None = None,
    max_cop_rmse: float | None = None,
    auxiliary_grid_size: int = 21,
    capacity_tolerance: float = 1e-4,
    baseline_label: str | None = None,
) -> FitReport:
    """Fit overall efficiency, then optionally adjust flow and discharge feasibility.

    ``parameters`` selects allowed changes, never additional COP free variables.
    If every candidate in a selected COP fitting stage is infeasible, the fit
    cannot be applied even when a later auxiliary stage restores feasibility.
    Auxiliary search includes the current value and interval endpoints and takes
    the feasible candidate closest to the current scale. Resolution is explicit
    through ``auxiliary_grid_size``. No alternating optimisation is performed.
    Efficiency callables must be pure and deterministic: Python copies do not
    isolate side effects captured by arbitrary user function closures.
    All fixed model inputs remain unchanged. A failure stores ``fit_report`` but
    leaves the original efficiencies and previously applied state intact.
    """
    parsed = _parse_points(points, mode)
    if not parameters or len(set(parameters)) != len(parameters) or set(parameters) - set(_FIELDS):
        raise ValueError("parameters must contain distinct supported physical quantity names")
    bounds = _bounds(lambda_bounds, parameters)
    if max_cop_rmse is not None and (not math.isfinite(max_cop_rmse) or max_cop_rmse < 0):
        raise ValueError("max_cop_rmse must be finite and nonnegative")
    if not isinstance(auxiliary_grid_size, int) or auxiliary_grid_size < 2:
        raise ValueError("auxiliary_grid_size must be an integer >= 2")
    if not math.isfinite(capacity_tolerance) or capacity_tolerance <= 0:
        raise ValueError("capacity_tolerance must be finite and positive")
    existing = getattr(model, "compressor_fit_state", None)
    current = (model.eta_cmp_vol, model.eta_cmp_isen, model.eta_cmp)
    if isinstance(existing, _AppliedFit):
        if any(a is not b for a, b in zip(current, existing.efficiencies, strict=True)):
            raise ValueError("Efficiencies changed after fitting; create a new model to select a new baseline")
        baseline, initial = existing.baseline, existing.scales
        label = baseline_label or existing.baseline_label
    else:
        baseline, initial = current, EfficiencyScales()
        label = baseline_label or str(
            getattr(current[0], "source_version", None)
            or getattr(current[0], "version", "constructor_efficiency_overrides")
        )
    for name in parameters:
        if not bounds[name][0] <= getattr(initial, _FIELDS[name]) <= bounds[name][1]:
            raise ValueError(f"Current {name} scale lies outside lambda_bounds")
    isolated = copy.deepcopy(model)
    cache: dict[EfficiencyScales, tuple[PointResult, ...]] = {}

    def evaluate(scales: EfficiencyScales) -> tuple[PointResult, ...]:
        """Evaluate a complete candidate once, preserving all catalogue rows in the cache."""
        if scales not in cache:
            isolated.eta_cmp_vol, isolated.eta_cmp_isen, isolated.eta_cmp = scaled_efficiencies(baseline, scales)
            rows = []
            for point in parsed:
                result = _point_result(isolated, point, mode, capacity_tolerance)
                if result.pressure_ratio is not None and result.speed_rps is not None:
                    original_em = _efficiency(baseline[2], result.pressure_ratio, result.speed_rps)
                    if not math.isfinite(original_em) or not 0 < original_em <= 1:
                        result = replace(
                            result,
                            valid=False,
                            failure_reason="baseline_heat_loss_domain",
                            constraint_violations=(*result.constraint_violations, "baseline_heat_loss_domain"),
                        )
                rows.append(result)
            cache[scales] = tuple(rows)
        return cache[scales]

    initial_points = evaluate(initial)
    candidate = initial
    stages: list[FitStage] = []
    cop_stage_feasible = True
    if "overall_efficiency" in parameters:
        lower, upper = bounds["overall_efficiency"]

        def objective(value: float) -> float:
            """Use every requested COP observation; an invalid point invalidates the candidate."""
            rms = _rmse(evaluate(replace(initial, lambda_eta_ov=float(value))))
            return rms if rms is not None else 1e12

        grid = sorted(set(np.linspace(lower, upper, 9).tolist() + [initial.lambda_eta_ov]))
        ranked = [(objective(value), value) for value in grid]
        best_index = min(range(len(grid)), key=lambda i: (ranked[i][0], abs(grid[i] - initial.lambda_eta_ov)))
        left, right = grid[max(0, best_index - 1)], grid[min(len(grid) - 1, best_index + 1)]
        if left < right and ranked[best_index][0] < 1e12:
            optimum = minimize_scalar(
                objective, bounds=(left, right), method="bounded", options={"xatol": 1e-5, "maxiter": 35}
            )
            ranked.append((objective(float(optimum.x)), float(optimum.x)))
        _, value = min(ranked, key=lambda item: (item[0], abs(item[1] - initial.lambda_eta_ov)))
        candidate = replace(candidate, lambda_eta_ov=value)
        rows = evaluate(candidate)
        cop_rmse = _rmse(rows)
        cop_stage_feasible = cop_rmse is not None
        stages.append(
            FitStage(
                "COP fitting",
                candidate,
                cop_rmse,
                candidate != initial,
                rows,
                "completed" if cop_stage_feasible else "no_feasible_candidate",
            )
        )

    for name, checks, role in (
        (
            "volumetric_efficiency",
            {"net_capacity", "speed_range", "invalid_efficiency", "solver_failure"},
            "speed feasibility",
        ),
        (
            "heat_loss_fraction",
            {"discharge_state", "heat_loss_domain", "invalid_efficiency", "solver_failure"},
            "discharge feasibility",
        ),
    ):
        if name not in parameters:
            continue
        previous = candidate

        def feasible(rows: tuple[PointResult, ...], checked: set[str] = checks) -> bool:
            """Check this stage's constraints without fitting its coefficient to COP."""
            return all(p.operating_valid and not (checked & set(p.constraint_violations)) for p in rows)

        if not feasible(evaluate(candidate)):
            lower, upper = bounds[name]
            current_value = getattr(initial, _FIELDS[name])
            values = set(np.linspace(lower, upper, auxiliary_grid_size).tolist() + [current_value])
            for value in sorted(values, key=lambda v: (abs(v - current_value), v)):
                trial = replace(candidate, **{_FIELDS[name]: float(value)})
                if feasible(evaluate(trial)):
                    candidate = trial
                    break
        rows = evaluate(candidate)
        stages.append(
            FitStage(
                role,
                candidate,
                _rmse(rows),
                previous != candidate,
                rows,
                "completed" if feasible(rows) else "no_feasible_candidate",
            )
        )

    final_points = evaluate(candidate)
    initial_rmse, final_rmse = (
        _rmse(initial_points, require_constraints=True),
        _rmse(final_points, require_constraints=True),
    )
    if not cop_stage_feasible:
        success, reason = False, "cop_fit_no_feasible_candidate"
    elif not all(p.valid for p in final_points):
        success, reason = False, "constraints_not_satisfied"
    elif max_cop_rmse is not None and (final_rmse is None or final_rmse > max_cop_rmse):
        success, reason = False, "fit_tolerance_not_met"
    elif initial_rmse is not None and final_rmse is not None and final_rmse > initial_rmse + 1e-9:
        success, reason = False, "fit_worsened_after_auxiliary_adjustment"
    else:
        success, reason = True, "applied"
    report = FitReport(
        success,
        success,
        reason,
        mode,
        parameters,
        label,
        initial,
        candidate,
        candidate if success else initial,
        initial_rmse,
        final_rmse,
        initial_points,
        final_points,
        tuple(stages),
        len(cache),
        tuple((key, *bounds[key]) for key in parameters),
    )
    if success:
        applied = scaled_efficiencies(baseline, candidate)
        model.eta_cmp_vol, model.eta_cmp_isen, model.eta_cmp = applied
        model.compressor_fit_state = _AppliedFit(baseline, applied, candidate, label)  # type: ignore[attr-defined]
    model.fit_report = report  # type: ignore[attr-defined]
    return report
