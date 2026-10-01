"""Numerical helpers specific to coupled ground-loop flow control.

Candidate evaluation never advances the g-function history. Failed candidates
carry explicit diagnostics and cannot contribute a finite power objective.
"""

from collections.abc import Callable

import numpy as np
from scipy.optimize import brentq, minimize_scalar

from .constants import c_w, rho_w
from .ground_loop import calc_bhe_fluid_temperatures


def failed_ground_point(reason: str, diagnostic: dict | None = None) -> dict:
    """A failed requested operating point: no delivered heat or usable COP."""
    result = dict(diagnostic or {})
    result.update(
        {
            "candidate_ground_flow_ratio": result.get("ground_flow_ratio", np.nan),
            "ground_flow_ratio": np.nan,
            "ground_flow_ref_ratio": np.nan,
            "ground_flow_at_min": False,
            "ground_flow_at_max": False,
            "converged": False,
            "hp_is_on": False,
            "failure_reason": reason,
            "hx_feasible": bool(result.get("hx_feasible", False)) if reason.startswith("compressor_") else False,
            "approach_solver_success": False,
            "flow_optimizer_success": False,
            "flow_bound_active": False,
        }
    )
    for key in (
        "Q_ref_iu [W]",
        "Q_ref_tank [W]",
        "Q_ref_ground [W]",
        "Q_bhe [W]",
        "E_cmp [W]",
        "E_cmp_ref [W]",
        "E_cmp_loss [W]",
        "E_pmp [W]",
        "E_iu_fan [W]",
        "E_tot [W]",
        "E_cmp_plus_pmp [W]",
        "dV_bhe_f [m3/s]",
        "ground_flow [m3/s]",
        "m_dot_borehole [kg/s]",
        "m_dot_ref [kg/s]",
        "cmp_rpm [rpm]",
    ):
        result[key] = 0.0
    result["cop_ref [-]"] = result["cop_sys [-]"] = np.nan
    return result


def close_ground_temperature(
    evaluate: Callable[[float], dict | None], wall_K: float | Callable[[float], float], n: int, depth: float
) -> dict | None:
    """Close Tout = Twall - q' Rb* + Q/(2 m cp) at fixed approaches/flow.

    Relaxed iteration uses the same cycle and pump heat at each evaluation.
    It accepts only a resolved temperature balance, never a last iterate.
    """
    initial_wall = wall_K(0.0) if callable(wall_K) else wall_K
    temperature = initial_wall
    previous_temperature, previous_error = initial_wall, None
    for _ in range(40):
        try:
            result = evaluate(temperature)
        except (ValueError, OverflowError, ZeroDivisionError):
            return None
        if result is None:
            return None
        mean, inlet, outlet = calc_bhe_fluid_temperatures(
            wall_K(result["Q_bhe [W]"]) if callable(wall_K) else wall_K,
            result["Q_bhe [W]"],
            n,
            depth,
            result["R_b_eff [mK/W]"],
            result["dV_bhe_f [m3/s]"] * rho_w,
            c_w,
        )
        error = outlet - temperature
        if abs(error) < 1e-5:
            result["ground_temperature_residual [K]"] = error
            return result
        next_temperature = temperature + 0.7 * error
        if previous_error is not None and abs(error - previous_error) > 1e-10:
            secant = temperature - error * (temperature - previous_temperature) / (error - previous_error)
            if abs(secant - temperature) < 30 and np.isfinite(secant):
                next_temperature = secant
        previous_temperature, previous_error = temperature, error
        temperature = next_temperature
        if not np.isfinite(temperature) or abs(temperature - initial_wall) > 100:
            return None
    return None


def solve_ground_approach(
    evaluate: Callable[[float], dict | None],
    load_key: str,
    requested_load: float,
    bounds: tuple[float, float] = (1.0, 20.0),
) -> dict:
    """Bracket HX duty equality over valid cycle points, then check load/speed."""
    cache: dict[float, dict | None] = {}

    def point(x: float) -> dict | None:
        if x not in cache:
            cache[x] = evaluate(x)
        return cache[x]

    def residual(x: float) -> float:
        row = point(x)
        if row is None:
            raise ValueError("Invalid cycle inside approach bracket")
        return float(row["Q_ref_required [W]"] - row["Q_HX_available [W]"])

    # Sampling prevents invalid end states from hiding an interior feasible root.
    grid = np.linspace(*bounds, 13)
    candidates = []
    previous = None
    for x in grid:
        x = float(x)
        row = point(x)
        if row is None:
            previous = None
            continue
        value = residual(x)
        if abs(value) <= max(0.01, 1e-5 * row["Q_ref_required [W]"]):
            candidates.append((x, row))
        if previous is not None and value * previous[1] < 0:
            try:
                root = float(brentq(residual, previous[0], x, xtol=1e-7))
                solved = point(root)
                if solved is not None:
                    candidates.append((root, solved))
            except (ValueError, RuntimeError):
                pass
        previous = (x, value)
    failures = []
    for approach, row in candidates:
        if not row.get("hx_feasible", False):
            continue
        if abs(row[load_key] - requested_load) > max(0.1, requested_load * 1e-5):
            failures.append(
                {"min": "compressor_min_speed", "max": "compressor_max_speed"}.get(
                    str(row.get("capacity_clamped")), "compressor_capacity_limit"
                )
            )
            continue
        if not row.get("converged_rps", True):
            failures.append(str(row.get("failure_reason", "compressor_speed_limit")))
            continue
        if not row.get("converged", False):
            failures.append("load_hx_capacity_insufficient")
            continue
        row.update(
            {
                "converged": True,
                "failure_reason": "none",
                "approach_solver_success": True,
                "approach_at_bound": bool(min(abs(approach - bounds[0]), abs(approach - bounds[1])) < 1e-4),
                "ground_approach [K]": approach,
            }
        )
        return row
    valid = [r for r in cache.values() if r is not None]
    diagnostic = max(valid, key=lambda r: r["hx_capacity_ratio"], default={})
    reason = failures[0] if failures else ("ground_hx_capacity_insufficient" if valid else "cycle_invalid")
    return failed_ground_point(reason, diagnostic)


def select_ground_flow(
    evaluate: Callable[[float], dict], settings: dict, prescribed_ratio: float | None = None
) -> dict:
    """Optimize actual volume flow [m3/s] with a fixed normalization reference.

    The callback accepts ratio-to-reference for compatibility with cycle models;
    this ratio never supplies the optimizer's physical limits.
    """
    ref = settings.get("volume_flow_ref", 1.0)
    lo = settings.get("volume_flow_min", settings["min_ratio"] * ref)
    hi = settings.get("volume_flow_max", settings["max_ratio"] * ref)
    if prescribed_ratio is not None:
        volume = prescribed_ratio * ref
        tolerance = 8 * np.finfo(float).eps * max(abs(lo), abs(hi))
        if not np.isfinite(volume) or not lo - tolerance <= volume <= hi + tolerance:
            raise ValueError("Prescribed ground flow is outside the configured bounds")
        prescribed_ratio = min(max(volume, lo), hi) / ref
    if prescribed_ratio is not None or settings["control"] == "constant":
        ratio = prescribed_ratio if prescribed_ratio is not None else settings.get("volume_flow_constant", ref) / ref
        row = evaluate(ratio)
        row.update({"flow_optimizer_nfev": 0, "flow_optimizer_success": False, "flow_bound_active": False})
        return row
    cache: dict[float, dict] = {}

    def objective(volume: float) -> float:
        if volume not in cache:
            cache[volume] = evaluate(volume / ref)
        row = cache[volume]
        power = row.get("E_tot [W]", np.inf)
        return (
            float(power)
            if row.get("converged", False) and row.get("hx_feasible", False) and np.isfinite(power) and power > 0
            else np.inf
        )

    fixed = settings.get("volume_flow_constant", ref)
    grid = sorted(set(np.linspace(lo, hi, 9).tolist() + [v for v in (ref, fixed) if lo <= v <= hi]))
    powers = [objective(x) for x in grid]
    feasible = [i for i, p in enumerate(powers) if np.isfinite(p)]
    if not feasible:
        rows = list(cache.values())
        reasons = [r["failure_reason"] for r in rows]
        reason = "ground_hx_capacity_insufficient" if "ground_hx_capacity_insufficient" in reasons else reasons[0]
        result = failed_ground_point(reason, rows[0])
        result["flow_optimizer_nfev"] = len(cache)
        return result
    best = min(feasible, key=lambda i: powers[i])
    left, right = grid[max(0, best - 1)], grid[min(len(grid) - 1, best + 1)]
    # Finite penalty keeps scipy interpolation stable near invalid states;
    # final selection still uses explicit feasibility and finite actual power.
    optimum = minimize_scalar(
        lambda x: min(objective(float(x)), 1e30),
        bounds=(left, right),
        method="bounded",
        options={"xatol": 1e-3 * ref, "maxiter": 30},
    )
    objective(float(optimum.x))
    chosen = min(
        (
            r
            for r in cache.values()
            if r.get("converged", False)
            and r.get("hx_feasible", False)
            and np.isfinite(r.get("E_tot [W]", np.inf))
            and r.get("E_tot [W]", 0) > 0
        ),
        key=lambda r: r["E_tot [W]"],
    )
    chosen.update(
        {
            "flow_optimizer_success": bool(optimum.success),
            "flow_optimizer_nfev": len(cache),
            "flow_optimizer_method": "bounded" if optimum.success else "grid_fallback",
            "flow_bound_active": min(
                abs(chosen["ground_flow_ratio"] * ref - lo), abs(chosen["ground_flow_ratio"] * ref - hi)
            )
            < 2e-3 * ref,
        }
    )
    return chosen
