"""One-off diagnostic: how much of the low-load COP rise is the fan model?

Four sensitivities plus one diagnostic control law, run once on the shipped
defaults and the frozen compressor coefficients. Nothing here is a production
change: no file under ``src/`` is touched, every deviation is made on an
instance or through a shim installed inside this module.

The shipped air side, for reference (3.5 kW air-to-air, both coils):
rated flow ``0.70 m3/s``, rated static pressure ``60 Pa``, fan efficiency
``0.60`` -> rated fan power ``70 W``; ``UA ∝ (V/V_rated)^0.65``; fan power from
the ASHRAE 90.1 VSD polynomial; and -- the part this diagnostic is really
about -- **the airflow is not an input**. Each coil is solved for the airflow
that makes ``Q_air = Q_ref``, searching ``5–100 %`` of rated flow. Low load
therefore buys a low airflow, and a low airflow is nearly free.

``S1 min airflow``   the search floor, ``5 / 20 / 30 / 40 %`` of rated. Raising
                     it does not break the balance: the coil over-transfers at
                     the floor, so the optimiser answers with a smaller
                     approach temperature and ``Q_air = Q_ref`` still holds --
                     which is exactly the mechanism under test.
``S2 rated power``   ``0.7 / 1.0 / 1.3 x`` of 70 W, applied through the rated
                     static pressure so the airflow solve is untouched.
``S3 min power``     ``P = P_min + (P_rated - P_min) f_VSD(x)`` with ``P_min``
                     at ``0 / 5 / 10 / 20 %`` of rated.
``S4 power curve``   VSD polynomial (= S3 at 0 %), cubic affinity ``x^3``, and
                     VSD + 10 % floor (= S3 at 10 %).
``S5 speed-linked``  diagnostic only, never a proposed control law. Airflow is
                     *forced* to ``max(x_min, n*)``, so it is no longer the
                     unknown -- the approach temperatures are. They are solved
                     from the two coil energy balances rather than optimised,
                     with the residuals written to the CSV so the closure is
                     checkable rather than asserted.

Boundary conditions follow ``validation/compressor_maps/final_simulate.py``:
3.5 kW R32, heating 7/20 degC and cooling 35/27 degC, requested duty 1.00 ->
0.12 of nameplate. Rows past the compressor speed floor are kept and flagged
(``capacity_clamped == "min"``), drawn hollow, and excluded from every verdict.

Run::

    uv run python3 -m validation.diagnostics.fan_model_sensitivity_once
    uv run python3 -m validation.diagnostics.fan_model_sensitivity_once --only fig
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import warnings
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import least_squares, root_scalar

import tmhp.air_source_heat_pump as ashp_mod
from tmhp import AirSourceHeatPump
from tmhp.compressor_efficiency import COEFFICIENT_VERSION
from tmhp.constants import c_a, rho_a
from tmhp.hx_fan import calc_UA_from_dV_fan

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "validation" / "results" / "fan_model_sensitivity_once"
CSV_NAME = "fan_model_sensitivity_once.csv"

CAP, REF = 3500.0, "R32"
FRACTIONS = tuple(round(1.0 - 0.04 * i, 2) for i in range(23))  # 1.00 .. 0.12
DP_RATED = 60.0  # Pa, shipped default -> 0.70 * 60 / 0.6 = 70 W per fan

CONDITIONS = (
    ("heating_7", "heating", 7.0, 20.0),
    ("cooling_35", "cooling", 35.0, 27.0),
)

#: Every config is the shipped model with exactly one knob moved, so a
#: difference between two rows is attributable to that knob alone.
BASE_CFG = {
    "airflow_floor": 0.05,
    "fan_power_scale": 1.0,
    "p_fan_min_frac": 0.0,
    "fan_curve": "vsd",
    "airflow_law": "solver",
    "speed_link_floor": 0.0,
}
CONFIGS: dict[str, dict] = {
    "baseline": {},
    "floor_20": {"airflow_floor": 0.20},
    "floor_30": {"airflow_floor": 0.30},
    "floor_40": {"airflow_floor": 0.40},
    "power_070": {"fan_power_scale": 0.7},
    "power_130": {"fan_power_scale": 1.3},
    "pmin_05": {"p_fan_min_frac": 0.05},
    "pmin_10": {"p_fan_min_frac": 0.10},
    "pmin_20": {"p_fan_min_frac": 0.20},
    "pmin_30": {"p_fan_min_frac": 0.30},
    "curve_cubic": {"fan_curve": "cubic"},
    "speedlink_05": {"airflow_law": "speed_linked", "speed_link_floor": 0.05},
    "speedlink_40": {"airflow_law": "speed_linked", "speed_link_floor": 0.40},
}
CONFIGS = {k: {**BASE_CFG, **v} for k, v in CONFIGS.items()}

LABELS = {
    "baseline": "current TMHP",
    "floor_20": r"$x_{min}$ = 20 %",
    "floor_30": r"$x_{min}$ = 30 %",
    "floor_40": r"$x_{min}$ = 40 %",
    "power_070": r"0.7 $\times$ (49 W)",
    "power_130": r"1.3 $\times$ (91 W)",
    "pmin_05": r"$P_{min}$ = 5 %",
    "pmin_10": r"$P_{min}$ = 10 %",
    "pmin_20": r"$P_{min}$ = 20 %",
    "pmin_30": r"$P_{min}$ = 30 %",
    "curve_cubic": r"cubic $x^3$",
    "speedlink_05": r"speed-linked, $x_{min}$ = 5 %",
    "speedlink_40": r"speed-linked, $x_{min}$ = 40 %",
}

# ---------------------------------------------------------------------------
# shims
# ---------------------------------------------------------------------------
# ``AirSourceHeatPump._calc_state`` reaches ``calc_HX_perf_for_target_heat``
# and ``calc_fan_power_from_dV_fan`` as module globals of
# ``tmhp.air_source_heat_pump``, so rebinding those two names is enough and the
# library source stays untouched. ``_CFG`` is per-process state, set once per
# worker task; a worker solves one point at a time.
_CFG: dict = dict(BASE_CFG)
_ORIG_HX = ashp_mod.calc_HX_perf_for_target_heat
_ORIG_FAN_POWER = ashp_mod.calc_fan_power_from_dV_fan

#: Filled by the HX shim in speed-linked mode: coil -> (dT_required, dT_actual).
_CLOSURE: dict[str, tuple[float, float]] = {}
_COIL_UA: dict[str, float] = {}


def _hx_solver(Q_ref_target, T_a_in_K, T_ref_sat_K, A_cross, UA_rated, dV_fan_rated, exponent, floor):
    """Shipped ε-NTU airflow solve with the search floor exposed.

    Byte-identical to ``enex_functions.calc_HX_perf_for_target_heat`` at
    ``floor = 0.05``; :func:`selftest` checks that against the real function
    rather than taking it on faith.
    """

    def err(dV_fan):
        if dV_fan <= 0:
            return -Q_ref_target
        UA = calc_UA_from_dV_fan(dV_fan, dV_fan_rated, A_cross, UA_rated, exponent)
        C_air = c_a * rho_a * dV_fan
        eps = 1 - np.exp(-UA / C_air)
        return C_air * eps * abs(T_a_in_K - T_ref_sat_K) - Q_ref_target

    dV_min, dV_max = dV_fan_rated * floor, dV_fan_rated
    try:
        sol = root_scalar(err, bracket=[dV_min, dV_max], method="bisect")
        dV, converged = sol.root, sol.converged
    except ValueError:
        return {
            "converged": False,
            "dV_fan": np.nan,
            "UA": np.nan,
            "T_a_mid_C": np.nan,
            "Q_air": np.nan,
            "epsilon": np.nan,
            "min_limit": bool(err(dV_min) > 0),
            "max_limit": bool(err(dV_max) < 0),
        }
    UA = calc_UA_from_dV_fan(dV, dV_fan_rated, A_cross, UA_rated, exponent)
    C_air = c_a * rho_a * dV
    eps = 1 - np.exp(-UA / C_air) if dV > 0 else 0.0
    T_mid_K = T_a_in_K - (T_a_in_K - T_ref_sat_K) * eps
    return {
        "converged": converged,
        "dV_fan": dV,
        "UA": UA,
        "T_a_mid_C": T_mid_K - 273.15,
        "Q_air": C_air * abs(T_a_in_K - T_mid_K),
        "epsilon": eps,
        "min_limit": False,
        "max_limit": False,
    }


def _hx_forced(Q_ref_target, T_a_in_K, T_ref_sat_K, A_cross, UA_rated, dV_fan_rated, exponent, x_forced):
    """Airflow given, approach temperature *not* given.

    Returns the state the coil is actually in at this airflow and this
    saturation temperature, and records the approach the balance demands,
    ``dT_required = Q_ref_target / (C_air eps)``. The outer solve drives
    ``dT_required - dT_actual`` to zero; until it is zero, ``Q_air != Q_ref``
    and the row is not a valid operating point.
    """
    dV = max(1e-9, x_forced) * dV_fan_rated
    UA = calc_UA_from_dV_fan(dV, dV_fan_rated, A_cross, UA_rated, exponent)
    C_air = c_a * rho_a * dV
    eps = 1 - np.exp(-UA / C_air)
    dT_actual = abs(T_a_in_K - T_ref_sat_K)
    T_mid_K = T_a_in_K - (T_a_in_K - T_ref_sat_K) * eps
    return (
        {
            "converged": True,
            "dV_fan": dV,
            "UA": UA,
            "T_a_mid_C": T_mid_K - 273.15,
            "Q_air": C_air * eps * dT_actual,
            "epsilon": eps,
            "min_limit": False,
            "max_limit": False,
        },
        Q_ref_target / (C_air * eps),
        dT_actual,
    )


def _patched_hx(*, Q_ref_target, T_a_in_C, T_ref_sat_K, A_cross, UA_rated, dV_fan_rated, is_active, exponent):
    if not is_active or abs(Q_ref_target) < 1e-6:
        return _ORIG_HX(
            Q_ref_target=Q_ref_target,
            T_a_in_C=T_a_in_C,
            T_ref_sat_K=T_ref_sat_K,
            A_cross=A_cross,
            UA_rated=UA_rated,
            dV_fan_rated=dV_fan_rated,
            is_active=is_active,
            exponent=exponent,
        )
    T_a_in_K = T_a_in_C + 273.15
    coil = "ou" if abs(UA_rated - _COIL_UA["ou"]) < abs(UA_rated - _COIL_UA["iu"]) else "iu"

    if _CFG["airflow_law"] == "speed_linked":
        # n* lives in the caller's frame: ``cmp_rps`` is solved a few lines
        # above the coil calls and never reaches this signature.
        rps = float(sys._getframe(1).f_locals["cmp_rps"])
        x = min(1.0, max(_CFG["speed_link_floor"], rps / _CFG["rps_rated"]))
        res, dT_req, dT_act = _hx_forced(
            Q_ref_target, T_a_in_K, T_ref_sat_K, A_cross, UA_rated, dV_fan_rated, exponent, x
        )
        _CLOSURE[coil] = (dT_req, dT_act)
        return res
    return _hx_solver(
        Q_ref_target, T_a_in_K, T_ref_sat_K, A_cross, UA_rated, dV_fan_rated, exponent, _CFG["airflow_floor"]
    )


def _patched_fan_power(*, dV_fan, fan_params, vsd_coeffs, is_active=True):
    if not is_active:
        return np.nan
    V_rated = fan_params["fan_rated_flow_rate"]
    P_rated = fan_params["fan_rated_power"]
    if dV_fan < 0:
        raise ValueError("fan flow rate must be greater than 0")
    x = dV_fan / V_rated
    if _CFG["fan_curve"] == "cubic":
        f = x**3
    else:
        f = (
            vsd_coeffs.get("c1", 0.0013)
            + vsd_coeffs.get("c2", 0.1470) * x
            + vsd_coeffs.get("c3", 0.9506) * x**2
            + vsd_coeffs.get("c4", -0.0998) * x**3
            + vsd_coeffs.get("c5", 0.0) * x**4
        )
    f = max(0.0, f)
    P_min = _CFG["p_fan_min_frac"] * P_rated
    return float(P_min + (P_rated - P_min) * f)


ashp_mod.calc_HX_perf_for_target_heat = _patched_hx
ashp_mod.calc_fan_power_from_dV_fan = _patched_fan_power


# ---------------------------------------------------------------------------
# model
# ---------------------------------------------------------------------------
_MODELS: dict = {}


def _model(scale: float) -> AirSourceHeatPump:
    """Rated fan power is moved through the static pressure.

    ``E_fan_rated = dV_rated dP_rated / eta``, so scaling ``dP`` scales the
    rated power and leaves the rated flow, the cross-section and therefore the
    entire airflow solve untouched. The fan still changes the optimum, because
    ``E_fan`` is inside the objective the approach temperatures minimise.
    """
    if scale not in _MODELS:
        _MODELS[scale] = AirSourceHeatPump(
            hp_capacity=CAP,
            ref=REF,
            dP_ou_fan_rated=DP_RATED * scale,
            dP_iu_fan_rated=DP_RATED * scale,
        )
    return _MODELS[scale]


def _num(r: dict | None, key: str) -> float:
    if r is None:
        return float("nan")
    v = r.get(key)
    return float(v) if isinstance(v, (int, float)) else float("nan")


def _solve_speed_linked(m, q_signed, t_out, t_room, mode, x0):
    """Approach temperatures from the two coil energy balances.

    With the airflow forced there is nothing left to optimise: the approaches
    are determined, two equations in two unknowns. ``least_squares`` keeps the
    iterate inside the shipped approach box so the comparison stays like for
    like.
    """
    lo, hi = m.dT_approach_bounds

    def resid(x):
        _CLOSURE.clear()
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                perf = m._calc_state(
                    dT_ref_evap=float(x[0]), dT_ref_cond=float(x[1]), Q_r_iu=q_signed, T0=t_out, T_a_room=t_room
                )
        except Exception:  # noqa: BLE001 -- an invalid cycle is a bad iterate, not a crash
            perf = None
        if perf is None or len(_CLOSURE) < 2:
            return [1e3, 1e3]
        evap_coil, cond_coil = ("ou", "iu") if mode == "heating" else ("iu", "ou")
        return [
            _CLOSURE[evap_coil][0] - float(x[0]),
            _CLOSURE[cond_coil][0] - float(x[1]),
        ]

    sol = least_squares(
        resid, [min(max(x0[0], lo), hi), min(max(x0[1], lo), hi)], bounds=([lo, lo], [hi, hi]), xtol=1e-12, ftol=1e-12
    )
    return sol


def run_point(task: dict) -> dict:
    global _CFG
    cfg_key = task["config"]
    _CFG = dict(CONFIGS[cfg_key])
    duty, t_out, t_room, frac = task["duty"], task["T_outdoor"], task["T_room"], task["plr_request"]
    m = _model(_CFG["fan_power_scale"])
    _CFG["rps_rated"] = m.rps_rated
    _COIL_UA["ou"], _COIL_UA["iu"] = m.UA_ou_rated, m.UA_iu_rated
    sign = -1.0 if duty == "heating" else 1.0
    q_req = CAP * frac
    q_signed = sign * q_req

    closure_resid = float("nan")
    if _CFG["airflow_law"] == "speed_linked":
        # Warm start from the shipped solve of the same point: the two models
        # share a cycle, so the shipped optimum is the nearest known state.
        saved, _CFG = _CFG, dict(BASE_CFG, rps_rated=m.rps_rated)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            opt0 = m._optimize_operation(Q_r_iu=q_signed, T0=t_out, T_a_room=t_room)
        _CFG = saved
        sol = _solve_speed_linked(m, q_signed, t_out, t_room, duty, (float(opt0.x[0]), float(opt0.x[1])))
        x = (float(sol.x[0]), float(sol.x[1]))
        closure_resid = float(np.max(np.abs(sol.fun)))
        opt_success = bool(sol.success) and closure_resid < 1e-4
        opt_fun = float("nan")
    else:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            opt = m._optimize_operation(Q_r_iu=q_signed, T0=t_out, T_a_room=t_room)
        x = (float(opt.x[0]), float(opt.x[1]))
        opt_fun = float(getattr(opt, "fun", ashp_mod.OBJ_INFEASIBLE))
        opt_success = bool(getattr(opt, "success", False)) and opt_fun < ashp_mod.OBJ_INFEASIBLE

    _CLOSURE.clear()
    perf: dict | None = None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            perf = m._calc_state(dT_ref_evap=x[0], dT_ref_cond=x[1], Q_r_iu=q_signed, T0=t_out, T_a_room=t_room)
    except Exception:  # noqa: BLE001 -- mirrors analyze_steady's suppress
        perf = None

    pr_event = m._last_pr_event
    if perf is None:
        reason = "pr_above_max" if pr_event is not None and pr_event[0] == "pr_above_max" else "cycle_invalid"
    elif not perf.get("converged", False):
        reason = "hx_not_converged"
    elif not opt_success:
        reason = "closure_not_solved" if _CFG["airflow_law"] == "speed_linked" else "optimizer_failed"
    else:
        reason = "none"
    ok = reason == "none"

    delivered = abs(_num(perf, "Q_ref_iu [W]")) if ok else float("nan")
    e_cmp = _num(perf, "E_cmp [W]") if ok else float("nan")
    e_ou = _num(perf, "E_ou_fan [W]") if ok else float("nan")
    e_iu = _num(perf, "E_iu_fan [W]") if ok else float("nan")
    e_tot = _num(perf, "E_tot [W]") if ok else float("nan")
    dV_ou = _num(perf, "dV_ou_a [m3/s]") if ok else float("nan")
    dV_iu = _num(perf, "dV_iu_a [m3/s]") if ok else float("nan")
    # Energy-balance check, read off the returned state rather than trusted:
    # the shipped solver hits it by construction, the speed-linked case only
    # at the root of the closure solve.
    q_ref_ou = abs(_num(perf, "Q_ref_ou [W]")) if ok else float("nan")
    q_ref_iu = abs(_num(perf, "Q_ref_iu [W]")) if ok else float("nan")
    eps_ou, eps_iu = _num(perf, "epsilon_ou [-]"), _num(perf, "epsilon_iu [-]")
    t_out_K, t_room_K = t_out + 273.15, t_room + 273.15
    t_evap = _num(perf, "T_ref_evap_sat [°C]") if ok else float("nan")
    t_cond = _num(perf, "T_ref_cond_sat_l [°C]") if ok else float("nan")
    sat_ou = t_evap if duty == "heating" else t_cond
    sat_iu = t_cond if duty == "heating" else t_evap
    q_air_ou = c_a * rho_a * dV_ou * eps_ou * abs(t_out_K - (sat_ou + 273.15)) if ok else float("nan")
    q_air_iu = c_a * rho_a * dV_iu * eps_iu * abs(t_room_K - (sat_iu + 273.15)) if ok else float("nan")

    return {
        "config": cfg_key,
        "family": task["family"],
        "airflow_floor": _CFG["airflow_floor"],
        "fan_power_scale": _CFG["fan_power_scale"],
        "p_fan_min_frac": _CFG["p_fan_min_frac"],
        "fan_curve": _CFG["fan_curve"],
        "airflow_law": _CFG["airflow_law"],
        "speed_link_floor": _CFG["speed_link_floor"],
        "condition": task["condition"],
        "duty": duty,
        "T_outdoor": t_out,
        "T_room": t_room,
        "PLR_request": frac,
        "Q_request_W": q_req,
        "Q_delivered_W": delivered,
        "PLR_delivered": delivered / CAP,
        "n_star": _num(perf, "n_star [-]") if ok else float("nan"),
        "rps": (_num(perf, "cmp_rpm [rpm]") / 60.0) if ok else float("nan"),
        "m_dot_ref": _num(perf, "m_dot_ref [kg/s]") if ok else float("nan"),
        "V_air_ou": dV_ou,
        "V_air_iu": dV_iu,
        "V_air_ratio_ou": dV_ou / m.dV_ou_fan_a_rated if ok else float("nan"),
        "V_air_ratio_iu": dV_iu / m.dV_iu_fan_a_rated if ok else float("nan"),
        "UA_ou": _num(perf, "UA_ou [W/K]") if ok else float("nan"),
        "UA_iu": _num(perf, "UA_iu [W/K]") if ok else float("nan"),
        "epsilon_ou": eps_ou if ok else float("nan"),
        "epsilon_iu": eps_iu if ok else float("nan"),
        "dT_ref_evap": x[0] if ok else float("nan"),
        "dT_ref_cond": x[1] if ok else float("nan"),
        "T_evap": t_evap,
        "T_cond": t_cond,
        "r_p": _num(perf, "pr_cmp [-]") if ok else float("nan"),
        "E_cmp": e_cmp,
        "E_ou_fan": e_ou,
        "E_iu_fan": e_iu,
        "E_fan_total": e_ou + e_iu,
        "E_tot": e_tot,
        "fan_share": (e_ou + e_iu) / e_tot if ok and e_tot > 0 else float("nan"),
        "COP_sys": delivered / e_tot if ok and e_tot > 0 else float("nan"),
        "energy_balance_err_ou": abs(q_air_ou - q_ref_ou) / q_ref_ou if ok and q_ref_ou > 0 else float("nan"),
        "energy_balance_err_iu": abs(q_air_iu - q_ref_iu) / q_ref_iu if ok and q_ref_iu > 0 else float("nan"),
        "closure_residual_K": closure_resid,
        "E_fan_rated_each": m.E_ou_fan_rated,
        "capacity_clamped": (perf.get("capacity_clamped") if perf else None),
        "ou_fan_flow_min_limit": bool(perf.get("ou_fan_flow_min_limit", False)) if perf else False,
        "iu_fan_flow_min_limit": bool(perf.get("iu_fan_flow_min_limit", False)) if perf else False,
        "pr_clamped": bool(pr_event is not None and pr_event[0] == "pr_below_min"),
        "converged": ok,
        "failure_reason": reason,
        "opt_fun": opt_fun,
        "coefficient_version": COEFFICIENT_VERSION,
    }


FAMILY = {
    "baseline": "baseline",
    "floor_20": "S1_min_airflow",
    "floor_30": "S1_min_airflow",
    "floor_40": "S1_min_airflow",
    "power_070": "S2_rated_fan_power",
    "power_130": "S2_rated_fan_power",
    "pmin_05": "S3_min_fan_power",
    "pmin_10": "S3_min_fan_power",
    "pmin_20": "S3_min_fan_power",
    "pmin_30": "S3_min_fan_power",
    "curve_cubic": "S4_fan_power_curve",
    "speedlink_05": "S5_speed_linked",
    "speedlink_40": "S5_speed_linked",
}


def _tasks() -> list[dict]:
    return [
        {
            "config": cfg,
            "family": FAMILY[cfg],
            "condition": cond,
            "duty": duty,
            "T_outdoor": t_out,
            "T_room": t_room,
            "plr_request": f,
        }
        for cfg in CONFIGS
        for cond, duty, t_out, t_room in CONDITIONS
        for f in FRACTIONS
    ]


def selftest() -> dict:
    """The shims must be the identity at shipped settings.

    Checked two ways: the re-implemented ε-NTU solve against the real
    ``calc_HX_perf_for_target_heat``, and a whole solved operating point
    against the same point with both shims removed.
    """
    global _CFG
    rep: dict = {}
    _CFG = dict(BASE_CFG)
    m = _model(1.0)
    _CFG["rps_rated"] = m.rps_rated
    _COIL_UA["ou"], _COIL_UA["iu"] = m.UA_ou_rated, m.UA_iu_rated

    worst = 0.0
    for q in (3500.0, 1800.0, 700.0, 300.0):
        for sat in (-5.0, 0.0, 2.0):
            a = _ORIG_HX(
                Q_ref_target=q,
                T_a_in_C=7.0,
                T_ref_sat_K=sat + 273.15,
                A_cross=m.A_cross_ou,
                UA_rated=m.UA_ou_rated,
                dV_fan_rated=m.dV_ou_fan_a_rated,
                is_active=True,
                exponent=m.n_ou,
            )
            b = _hx_solver(q, 7.0 + 273.15, sat + 273.15, m.A_cross_ou, m.UA_ou_rated, m.dV_ou_fan_a_rated, m.n_ou, 0.05)
            if a["converged"] and b["converged"]:
                worst = max(worst, abs(a["dV_fan"] - b["dV_fan"]) / a["dV_fan"], abs(a["UA"] - b["UA"]) / a["UA"])
    rep["hx_reimplementation_max_rel_dev"] = worst

    def solve(frac, duty, t_out, t_room):
        sign = -1.0 if duty == "heating" else 1.0
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            o = m._optimize_operation(Q_r_iu=sign * CAP * frac, T0=t_out, T_a_room=t_room)
            return m._calc_state(
                dT_ref_evap=float(o.x[0]), dT_ref_cond=float(o.x[1]), Q_r_iu=sign * CAP * frac, T0=t_out, T_a_room=t_room
            )

    keys = ["E_tot [W]", "E_ou_fan [W]", "E_iu_fan [W]", "dV_ou_a [m3/s]", "cop_sys [-]", "T_ref_evap_sat [°C]"]
    patched = {(f, c[0]): solve(f, c[1], c[2], c[3]) for f in (1.0, 0.6, 0.28) for c in CONDITIONS}
    ashp_mod.calc_HX_perf_for_target_heat = _ORIG_HX
    ashp_mod.calc_fan_power_from_dV_fan = _ORIG_FAN_POWER
    try:
        plain = {(f, c[0]): solve(f, c[1], c[2], c[3]) for f in (1.0, 0.6, 0.28) for c in CONDITIONS}
    finally:
        ashp_mod.calc_HX_perf_for_target_heat = _patched_hx
        ashp_mod.calc_fan_power_from_dV_fan = _patched_fan_power
    dev = 0.0
    for k in patched:
        for key in keys:
            a, b = float(patched[k][key]), float(plain[k][key])
            dev = max(dev, abs(a - b) / max(abs(b), 1e-9))
    rep["baseline_vs_unpatched_max_rel_dev"] = dev
    rep["identity_at_shipped_settings"] = worst == 0.0 and dev == 0.0
    return rep


def simulate(jobs: int) -> pd.DataFrame:
    tasks = _tasks()
    with ProcessPoolExecutor(max_workers=jobs) as ex:
        rows = list(ex.map(run_point, tasks, chunksize=2))
    df = pd.DataFrame(rows)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_DIR / CSV_NAME, index=False)
    print(f"{CSV_NAME}: {len(df)} rows")
    print(df.groupby(["config", "failure_reason"]).size().to_string())
    return df


# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------
def check(df: pd.DataFrame) -> dict:
    rep: dict = {"points_total": int(len(df))}
    rep["failures_by_reason"] = {k: int(v) for k, v in df.failure_reason.value_counts().items()}
    ok = df[df.converged]
    rep["energy_balance_max_rel_err"] = {
        "solver": float(ok[ok.airflow_law == "solver"][["energy_balance_err_ou", "energy_balance_err_iu"]].max().max()),
        "speed_linked": float(
            ok[ok.airflow_law == "speed_linked"][["energy_balance_err_ou", "energy_balance_err_iu"]].max().max()
        ),
    }
    sl = ok[ok.airflow_law == "speed_linked"]
    rep["speed_linked_max_closure_residual_K"] = float(sl.closure_residual_K.max()) if len(sl) else None
    rep["capacity_clamped_counts"] = {
        str(k): int(v) for k, v in df.capacity_clamped.fillna("none").value_counts().items()
    }
    rep["pr_clamped_points"] = int(df.pr_clamped.sum())
    # Did each floor actually bind, and where?
    binds = {}
    for cfg in ("baseline", "floor_20", "floor_30", "floor_40"):
        g = ok[(ok.config == cfg) & ok.capacity_clamped.isna()]
        floor = CONFIGS[cfg]["airflow_floor"]
        binds[cfg] = {
            "min_V_ratio_ou": float(g.V_air_ratio_ou.min()),
            "points_at_floor": int((g.V_air_ratio_ou <= floor * 1.001).sum()),
            "n_modulating": int(len(g)),
        }
    rep["airflow_floor_binding"] = binds
    rep["approach_at_lower_bound"] = {
        cfg: int((ok[(ok.config == cfg)].dT_ref_evap <= 1.0 + 1e-6).sum()) for cfg in CONFIGS
    }
    return rep


def summary(df: pd.DataFrame) -> pd.DataFrame:
    """One row per (config, condition): the numbers the verdict is made from."""
    rows = []
    for (cfg, cond), g in df.groupby(["config", "condition"]):
        mod = g[g.converged & g.capacity_clamped.isna()].sort_values("PLR_request")
        if not len(mod):
            continue
        lo, hi = mod.iloc[0], mod.iloc[-1]
        rows.append(
            {
                "config": cfg,
                "family": g.family.iloc[0],
                "condition": cond,
                "PLR_low": lo.PLR_request,
                "COP_low": lo.COP_sys,
                "COP_rated": hi.COP_sys,
                "rise_pct": 100.0 * (lo.COP_sys / hi.COP_sys - 1.0),
                "COP_max": mod.COP_sys.max(),
                "PLR_at_COP_max": float(mod.loc[mod.COP_sys.idxmax()].PLR_request),
                "monotonic_rise": bool(mod.COP_sys.idxmax() == mod.index[0]),
                "V_ratio_low": lo.V_air_ratio_ou,
                "E_fan_low_W": lo.E_fan_total,
                "E_fan_rated_W": hi.E_fan_total,
                "fan_share_low": lo.fan_share,
                "fan_share_rated": hi.fan_share,
                "E_tot_low_W": lo.E_tot,
            }
        )
    s = pd.DataFrame(rows)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    s.to_csv(OUT_DIR / "fan_model_sensitivity_summary.csv", index=False)
    return s


# ---------------------------------------------------------------------------
# figures
# ---------------------------------------------------------------------------
def _figures(df: pd.DataFrame) -> None:
    import dartwork_mpl as dm
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from scripts.visualization._dmpl_common import COLORS, GRIDLINE, HAIRLINE, apply_style, finalize, panel_letter, ticks

    apply_style()
    MS = 2.6
    CONDS = ["heating_7", "cooling_35"]
    cond_title = {"heating_7": "Heating 7/20 °C", "cooling_35": "Cooling 35/27 °C"}

    def save(fig, name: str, **margins) -> None:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        for extra in (0, 2, 4, 6, 8, 12):
            try:
                kw = {k: f"{float(v.rstrip('%')) + extra}%" for k, v in margins.items()}
                finalize(fig, OUT_DIR / name, formats=("svg", "png"), margin=f"{2 + extra}%", **kw)
                break
            except RuntimeError as exc:
                if extra == 12:
                    raise exc
        plt.close(fig)
        print("wrote", name)

    def split(g: pd.DataFrame):
        g = g[g.converged].sort_values("PLR_request")
        return g[g.capacity_clamped.isna()], g[g.capacity_clamped == "min"], g[g.capacity_clamped == "max"]

    def curve(ax, g, col, color, label=None, ls="solid", ms=MS, marker="o"):
        """House convention: hollow markers past the compressor speed floor.

        A marker-less series keeps that region distinguishable with a fine
        dotted line instead, so the speed floor never reads as modulation.
        """
        mod, floor, ceil = split(g)
        ax.plot(100 * mod.PLR_request, mod[col], ls=ls, lw=dm.lw(0), color=color, marker=marker, ms=ms, label=label)
        if len(floor) and marker:
            ax.plot(
                100 * floor.PLR_request, floor[col], ls="none", marker="o", ms=ms, mfc="white", mec=color, mew=HAIRLINE
            )
        elif len(floor):
            ax.plot(100 * floor.PLR_request, floor[col], ls=(0, (1, 2)), lw=dm.lw(0), color=color)
        if len(ceil):
            ax.plot(100 * ceil.PLR_request, ceil[col], ls="none", marker="x", ms=ms + 0.6, color=color, mew=HAIRLINE)

    def xaxis(ax, label=True):
        ax.set_xlim(0, 100)
        ax.set_xticks(ticks(0, 100, 20))
        if label:
            ax.set_xlabel("Requested part-load ratio [%]")
        ax.grid(True, alpha=0.25, linewidth=GRIDLINE)

    def sel(cfg, cond):
        return df[(df.config == cfg) & (df.condition == cond)]

    # Several of these sensitivities change nothing at all, and a solid line
    # drawn last would hide that under whichever case happened to come after.
    # The baseline keeps markers and every variant is a distinct dash pattern
    # drawn on top, so "no effect" reads as dashes sitting exactly on markers.
    DASHES = ["solid", (0, (5, 1.6)), (0, (2.4, 1.3)), (0, (1, 1.3)), (0, (5, 1.3, 1, 1.3))]

    def series(ax, cfgs, cond, col, colors, first=False, lss=None):
        for k, cfg in enumerate(cfgs):
            curve(
                ax,
                sel(cfg, cond),
                col,
                colors[k],
                label=LABELS[cfg] if first else None,
                ls=(lss[k] if lss else DASHES[k % len(DASHES)]),
                marker="o" if cfg == "baseline" else "",
            )

    # ---- E1 minimum airflow -------------------------------------------
    cfgs = ["baseline", "floor_20", "floor_30", "floor_40"]
    cols = [COLORS["ink"], COLORS["cool"], COLORS["accent"], COLORS["hot"]]
    fig, axes = plt.subplots(2, 3, figsize=dm.figsize("17cm", 0.60), squeeze=False,
                             gridspec_kw={"wspace": 0.42, "hspace": 0.40})
    quants = [
        ("COP_sys", r"System $\mathrm{COP}$ [-]"),
        ("V_air_ratio_ou", r"$V_{air}\,/\,V_{air,rated}$, outdoor [-]"),
        ("E_fan_total", r"Fan power, both fans [W]"),
    ]
    for i, cond in enumerate(CONDS):
        for j, (col, ylab) in enumerate(quants):
            ax = axes[i][j]
            series(ax, cfgs, cond, col, cols, first=(i == 0 and j == 0))
            xaxis(ax, label=(i == 1))
            ax.set_ylabel(ylab)
            ax.set_title(f"{cond_title[cond]}", fontsize=dm.fs(-3), loc="left")
            panel_letter(ax, "abcdef"[i * 3 + j], x=-0.30, y=1.06)
    axes[0][0].legend(loc="best", frameon=False, fontsize=dm.fs(-4.5))
    save(fig, "E1_minimum_airflow", mt="6%", ml="3%")

    # ---- E2 rated fan power -------------------------------------------
    cfgs = ["power_070", "baseline", "power_130"]
    cols = [COLORS["cool"], COLORS["ink"], COLORS["hot"]]
    fig, axes = plt.subplots(1, 2, figsize=dm.figsize("13cm", 0.45))
    for j, cond in enumerate(CONDS):
        ax = axes[j]
        series(ax, cfgs, cond, "COP_sys", cols, first=(j == 0))
        xaxis(ax)
        ax.set_ylabel(r"System $\mathrm{COP}$ [-]")
        ax.set_title(cond_title[cond], fontsize=dm.fs(-2), loc="left")
        panel_letter(ax, "ab"[j])
    axes[0].legend(loc="best", frameon=False, fontsize=dm.fs(-4), title="Rated fan power", title_fontsize=dm.fs(-4))
    save(fig, "E2_rated_fan_power", mt="7%")

    # ---- E3 minimum fan power -----------------------------------------
    cfgs = ["baseline", "pmin_05", "pmin_10", "pmin_20", "pmin_30"]
    cols = [COLORS["ink"], COLORS["cool"], COLORS["ess"], COLORS["accent"], COLORS["hot"]]
    fig, axes = plt.subplots(2, 2, figsize=dm.figsize("13cm", 0.80), squeeze=False,
                             gridspec_kw={"wspace": 0.38, "hspace": 0.40})
    for i, cond in enumerate(CONDS):
        for j, (col, ylab) in enumerate([("E_fan_total", "Fan power, both fans [W]"), ("COP_sys", r"System $\mathrm{COP}$ [-]")]):
            ax = axes[i][j]
            series(ax, cfgs, cond, col, cols, first=(i == 0 and j == 0))
            xaxis(ax, label=(i == 1))
            ax.set_ylabel(ylab)
            ax.set_title(cond_title[cond], fontsize=dm.fs(-3), loc="left")
            panel_letter(ax, "abcd"[i * 2 + j], x=-0.26, y=1.06)
    axes[0][0].legend(loc="best", frameon=False, fontsize=dm.fs(-4.5))
    save(fig, "E3_minimum_fan_power", mt="6%", ml="3%")

    # ---- E4 fan power curve -------------------------------------------
    fig, axes = plt.subplots(1, 3, figsize=dm.figsize("17cm", 0.34))
    ax = axes[0]
    x = np.linspace(0, 1, 201)
    vsd = np.clip(0.0013 + 0.1470 * x + 0.9506 * x**2 - 0.0998 * x**3, 0, None)
    ax.plot(x, vsd, lw=dm.lw(0), color=COLORS["ink"], label="A — ASHRAE 90.1 VSD")
    ax.plot(x, x**3, lw=dm.lw(0), color=COLORS["cool"], ls=(0, (4, 1.6)), label=r"B — cubic $x^3$")
    ax.plot(x, 0.10 + 0.90 * vsd, lw=dm.lw(0), color=COLORS["hot"], ls=(0, (2, 1.2)), label="C — VSD + 10 % floor")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_xticks(ticks(0, 1, 0.2))
    ax.set_yticks(ticks(0, 1, 0.2))
    ax.set_xlabel(r"$V_{air}\,/\,V_{air,rated}$ [-]")
    ax.set_ylabel(r"$P_{fan}\,/\,P_{fan,rated}$ [-]")
    ax.grid(True, alpha=0.25, linewidth=GRIDLINE)
    ax.legend(loc="upper left", frameon=False, fontsize=dm.fs(-4))
    panel_letter(ax, "a")
    cfgs = ["baseline", "curve_cubic", "pmin_10"]
    cols = [COLORS["ink"], COLORS["cool"], COLORS["hot"]]
    lss = ["solid", (0, (4, 1.6)), (0, (2, 1.2))]
    labs = ["A — VSD (current)", r"B — cubic $x^3$", "C — VSD + 10 % floor"]
    for j, cond in enumerate(CONDS):
        ax = axes[j + 1]
        for k, cfg in enumerate(cfgs):
            curve(ax, sel(cfg, cond), "COP_sys", cols[k], label=labs[k] if j == 0 else None, ls=lss[k])
        xaxis(ax)
        ax.set_ylabel(r"System $\mathrm{COP}$ [-]")
        ax.set_title(cond_title[cond], fontsize=dm.fs(-2), loc="left")
        panel_letter(ax, "bc"[j])
    axes[1].legend(loc="best", frameon=False, fontsize=dm.fs(-4))
    save(fig, "E4_fan_power_curve", mt="8%")

    # ---- E5 speed-linked airflow --------------------------------------
    cfgs = ["baseline", "speedlink_05", "speedlink_40"]
    cols = [COLORS["ink"], COLORS["accent"], COLORS["hot"]]
    lss = ["solid", (0, (4, 1.6)), (0, (2, 1.2))]
    quants = [
        ("V_air_ratio_ou", r"$V_{air}/V_{air,rated}$, OU [-]"),
        ("dT_ref_evap", r"$\Delta T_{approach,evap}$ [K]"),
        ("E_fan_total", "Fan power [W]"),
        ("COP_sys", r"System $\mathrm{COP}$ [-]"),
    ]
    fig, axes = plt.subplots(2, 4, figsize=dm.figsize("17cm", 0.50), squeeze=False,
                             gridspec_kw={"wspace": 0.55, "hspace": 0.42})
    for i, cond in enumerate(CONDS):
        for j, (col, ylab) in enumerate(quants):
            ax = axes[i][j]
            for k, cfg in enumerate(cfgs):
                curve(ax, sel(cfg, cond), col, cols[k], label=LABELS[cfg] if (i == 0 and j == 0) else None, ls=lss[k])
            xaxis(ax, label=(i == 1))
            ax.set_ylabel(ylab, fontsize=dm.fs(-3))
            ax.set_title(cond_title[cond], fontsize=dm.fs(-4), loc="left")
            panel_letter(ax, "abcdefgh"[i * 4 + j], x=-0.38, y=1.08)
    axes[0][0].legend(loc="best", frameon=False, fontsize=dm.fs(-5))
    save(fig, "E5_speed_linked_airflow", mt="7%", ml="3%")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jobs", type=int, default=int(os.environ.get("SIM_JOBS", "16")))
    ap.add_argument("--only", nargs="*", default=None, choices=["sim", "fig"])
    a = ap.parse_args()
    want = set(a.only or ["sim", "fig"])
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    started = datetime.now(UTC).isoformat()
    st = selftest()
    print(json.dumps(st, indent=2))
    if "sim" in want:
        df = simulate(a.jobs)
    else:
        df = pd.read_csv(OUT_DIR / CSV_NAME)
    rep = check(df)
    print(json.dumps(rep, indent=2, ensure_ascii=False))
    s = summary(df)
    print(s.to_string(index=False))
    if "fig" in want:
        _figures(df)
    manifest = {
        "script": str(Path(__file__).resolve().relative_to(REPO_ROOT)),
        "purpose": "one-off fan-model sensitivity for the low-load COP rise",
        "started_utc": started,
        "finished_utc": datetime.now(UTC).isoformat(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "coefficient_version": COEFFICIENT_VERSION,
        "capacity_W": CAP,
        "refrigerant": REF,
        "plr_fractions": list(FRACTIONS),
        "conditions": [{"key": k, "duty": d, "T_outdoor_C": t, "T_room_C": r} for k, d, t, r in CONDITIONS],
        "configs": {k: {kk: vv for kk, vv in v.items() if kk != "rps_rated"} for k, v in CONFIGS.items()},
        "shipped_air_side": {
            "dV_fan_rated_m3s": 0.70,
            "dP_fan_rated_Pa": DP_RATED,
            "eta_fan": 0.60,
            "E_fan_rated_W_each": 70.0,
            "UA_exponent": 0.65,
            "airflow_search_range": [0.05, 1.0],
        },
        "src_modified": False,
        "selftest": st,
        "checks": rep,
        "outputs": sorted(p.name for p in OUT_DIR.iterdir() if p.is_file()),
    }
    (OUT_DIR / "run_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print("wrote", OUT_DIR)


if __name__ == "__main__":
    main()
