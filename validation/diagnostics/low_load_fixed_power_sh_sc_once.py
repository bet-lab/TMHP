"""One-off diagnostic: does fixed power or the SH/SC setting remove the
low-load COP rise of the air-to-air model?

Two sensitivities, run once, on the shipped defaults and frozen coefficients.
Nothing here is a production model change: no file under ``src/`` is touched and
every deviation from the shipped behaviour is made on an instance or through a
temporary shim inside this module.

**A -- fixed electrical power.** Pure post-processing of one solved state:
``COP = Q_delivered / (E_tot + W_fixed)`` for ``W_fixed`` of 0/25/50/100 W. The
thermodynamic state is unchanged by construction -- each solved point is written
out four times, once per ``W_fixed``, with the same ``T_evap``/``T_cond``/
``r_p``/``n_star``; ``--check`` asserts that in the CSV.

**B -- superheat / subcooling.** Two modes that must not be mixed:

``mode_A``  the model exactly as shipped. ``_calc_state`` clips the setpoint,
            ``actual_SH = min(SH_set, max(0, dT_ref_evap - dT_hx_min))``, so a
            large setpoint survives only where the optimiser keeps a large
            approach. Measuring how much of it survives at low load is the
            point of this mode.
``mode_B``  the setpoint is made reachable: the optimiser is constrained to
            ``dT_ref_evap >= SH + dT_hx_min`` and ``dT_ref_cond >= SC +
            dT_hx_min``. ``AirSourceHeatPump`` passes one shared bounds tuple
            for both variables, so a per-variable lower bound needs the shim in
            :func:`_patched_minimize`. Whether it bound is *verified* from the
            result -- ``SH_actual``/``SC_actual`` are read back off the
            refrigerant state (compressor-inlet minus evaporating temperature,
            condensing minus expansion-inlet), not recomputed from the formula.

Boundary conditions follow ``validation/compressor_maps/final_simulate.py``:
3.5 kW R32, heating 7/20 degC (main) and cooling 35/27 degC, plus heating
-7/20 degC as a high-lift secondary case. Requested duty 1.00 -> 0.12 of
nameplate. Rows past the compressor speed floor are kept and flagged
(``capacity_clamped == "min"``); they are drawn hollow and excluded from every
verdict.

Run::

    uv run python3 -m validation.diagnostics.low_load_fixed_power_sh_sc_once
    uv run python3 -m validation.diagnostics.low_load_fixed_power_sh_sc_once --only fig
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

import pandas as pd

import tmhp.air_source_heat_pump as ashp_mod
from tmhp import AirSourceHeatPump
from tmhp.compressor_efficiency import COEFFICIENT_VERSION

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "validation" / "results" / "low_load_fixed_power_sh_sc_once"
CSV_NAME = "low_load_fixed_power_sh_sc_once.csv"

CAP, REF = 3500.0, "R32"
FRACTIONS = tuple(round(1.0 - 0.04 * i, 2) for i in range(23))  # 1.00 .. 0.12
W_FIXED = (0.0, 25.0, 50.0, 100.0)

#: (key, duty, outdoor degC, room degC, is the main case)
CONDITIONS = (
    ("heating_7", "heating", 7.0, 20.0, True),
    ("cooling_35", "cooling", 35.0, 27.0, True),
    ("heating_m7", "heating", -7.0, 20.0, False),
)

#: (SH_set, SC_set) -> label. Cases 0-6 of the spec plus the three extra
#: setpoints the SH-only / SC-only splits need.
SETPOINTS: dict[tuple[float, float], str] = {
    (3.0, 3.0): "Case 0 current 3/3",
    (3.0, 0.0): "Case 1 low 3/0",
    (5.0, 3.0): "Case 2 5/3",
    (5.0, 5.0): "Case 3 5/5",
    (8.0, 5.0): "Case 4 8/5",
    (10.0, 5.0): "Case 5 10/5",
    (10.0, 8.0): "Case 6 10/8",
    (3.0, 5.0): "split 3/5",
    (5.0, 0.0): "split 5/0",
    (5.0, 8.0): "split 5/8",
}
CURRENT = (3.0, 3.0)
SH_ONLY = [(3.0, 5.0), (5.0, 5.0), (8.0, 5.0), (10.0, 5.0)]  # SC fixed at 5 K
SC_ONLY = [(5.0, 0.0), (5.0, 3.0), (5.0, 5.0), (5.0, 8.0)]  # SH fixed at 5 K
CASE_ORDER = [
    (3.0, 3.0),
    (3.0, 0.0),
    (5.0, 3.0),
    (5.0, 5.0),
    (8.0, 5.0),
    (10.0, 5.0),
    (10.0, 8.0),
]

DT_HX_MIN = 0.5
APPROACH_HI = 20.0
APPROACH_LO = 1.0

# ---------------------------------------------------------------------------
# Mode B: per-variable approach bounds
# ---------------------------------------------------------------------------
# ``AirSourceHeatPump._optimize_operation`` hands ``scipy.optimize.minimize``
# the *same* tuple for both variables, so the physical constraint of mode B --
# a different floor on each -- cannot be expressed through the public
# constructor. The shim below rewrites the ``bounds`` keyword on the way in and
# clips ``x0`` into the rewritten box (SciPy only warns and clips silently,
# which would move the start point without saying so).
#
# ``_BOUNDS_OVERRIDE`` is module state, set for the duration of one solve. That
# is safe here because a worker process solves one point at a time, and it is
# reset in a ``finally``.
_BOUNDS_OVERRIDE: list[tuple[float, float]] | None = None
_SCIPY_MINIMIZE = ashp_mod.minimize


def _patched_minimize(fun, x0, *args, **kwargs):
    if _BOUNDS_OVERRIDE is not None and "bounds" in kwargs:
        kwargs["bounds"] = list(_BOUNDS_OVERRIDE)
        x0 = [min(max(float(v), lo), hi) for v, (lo, hi) in zip(x0, _BOUNDS_OVERRIDE, strict=True)]
    return _SCIPY_MINIMIZE(fun, x0, *args, **kwargs)


ashp_mod.minimize = _patched_minimize


def _mode_b_bounds(sh: float, sc: float) -> list[tuple[float, float]]:
    """``[(evap_lo, hi), (cond_lo, hi)]`` for a setpoint pair.

    The floor is ``max(APPROACH_LO, setpoint + dT_hx_min)``: never below the
    shipped lower bound, so mode B only ever *narrows* the domain relative to
    the shipped model and a difference between the two modes cannot come from
    mode B having been handed extra room.
    """
    return [
        (max(APPROACH_LO, sh + DT_HX_MIN), APPROACH_HI),
        (max(APPROACH_LO, sc + DT_HX_MIN), APPROACH_HI),
    ]


_MODELS: dict = {}


def _model(sh: float, sc: float, mode: str) -> AirSourceHeatPump:
    key = (sh, sc, mode)
    if key not in _MODELS:
        if mode == "mode_B":
            b = _mode_b_bounds(sh, sc)
            # The warm-start grid inside `_optimize_operation` is filtered
            # against the *instance* bounds, so give it the looser of the two
            # floors; the shim then applies the per-variable floor to the
            # Nelder-Mead box itself.
            approach = (min(b[0][0], b[1][0]), APPROACH_HI)
        else:
            approach = (APPROACH_LO, APPROACH_HI)
        _MODELS[key] = AirSourceHeatPump(
            hp_capacity=CAP,
            ref=REF,
            dT_superheat=sh,
            dT_subcool=sc,
            dT_hx_min=DT_HX_MIN,
            dT_approach_bounds=approach,
        )
    return _MODELS[key]


def _num(r: dict | None, key: str) -> float:
    if r is None:
        return float("nan")
    v = r.get(key)
    return float(v) if isinstance(v, (int, float)) else float("nan")


def run_point(task: dict) -> list[dict]:
    """Solve one requested duty; return one row per ``W_fixed``.

    Reproduces what ``analyze_steady`` does internally -- optimise, then
    evaluate the optimum -- but keeps the optimiser's own ``x`` instead of
    throwing it away, because ``dT_ref_evap``/``dT_ref_cond`` are the
    quantities this diagnostic is about and the result dict does not carry
    them. The HP-off fallback of ``analyze_steady`` is deliberately *not*
    reproduced: a failed point is reported as failed, not replaced by zeros.
    """
    global _BOUNDS_OVERRIDE

    sh, sc = task["SH_set"], task["SC_set"]
    mode, duty = task["sh_sc_mode"], task["duty"]
    t_out, t_room, frac = task["T_outdoor"], task["T_room"], task["plr_request"]
    m = _model(sh, sc, mode)
    sign = -1.0 if duty == "heating" else 1.0
    q_req = CAP * frac

    _BOUNDS_OVERRIDE = _mode_b_bounds(sh, sc) if mode == "mode_B" else None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            opt = m._optimize_operation(Q_r_iu=sign * q_req, T0=t_out, T_a_room=t_room)
            x = (float(opt.x[0]), float(opt.x[1]))
            perf: dict | None = None
            try:
                perf = m._calc_state(
                    dT_ref_evap=x[0], dT_ref_cond=x[1], Q_r_iu=sign * q_req, T0=t_out, T_a_room=t_room
                )
            except Exception:  # noqa: BLE001 -- mirrors analyze_steady's suppress
                perf = None
    finally:
        _BOUNDS_OVERRIDE = None

    pr_event = m._last_pr_event
    opt_fun = float(getattr(opt, "fun", ashp_mod.OBJ_INFEASIBLE))
    opt_success = bool(getattr(opt, "success", False)) and opt_fun < ashp_mod.OBJ_INFEASIBLE
    if perf is None:
        reason = "pr_above_max" if pr_event is not None and pr_event[0] == "pr_above_max" else "cycle_invalid"
    elif not perf.get("converged", False):
        reason = "hx_not_converged"
    elif not opt_success:
        reason = "optimizer_failed"
    else:
        reason = "none"

    ok = reason == "none"
    delivered = abs(_num(perf, "Q_ref_iu [W]")) if ok else float("nan")
    e_tot = _num(perf, "E_tot [W]") if ok else float("nan")
    t_evap = _num(perf, "T_ref_evap_sat [°C]") if ok else float("nan")
    t_cond = _num(perf, "T_ref_cond_sat_l [°C]") if ok else float("nan")
    # Read the achieved margins back off the refrigerant state rather than
    # recomputing the clipping formula: that is what makes mode B verifiable.
    sh_actual = (_num(perf, "T_ref_cmp_in [°C]") - t_evap) if ok else float("nan")
    sc_actual = (t_cond - _num(perf, "T_ref_exp_in [°C]")) if ok else float("nan")
    # The PR floor re-projects the condensing pressure, so the approach the
    # cycle ends up with is not always the optimiser variable. Both are kept:
    # the variable drives the SH/SC clipping, the effective value is the state.
    dt_cond_eff = (t_cond - t_room) if duty == "heating" else (t_cond - t_out)
    dt_evap_eff = (t_out - t_evap) if duty == "heating" else (t_room - t_evap)

    base = {
        "sh_sc_mode": mode,
        "condition": task["condition"],
        "duty": duty,
        "T_outdoor": t_out,
        "T_room": t_room,
        "is_main_case": task["is_main_case"],
        "case_label": SETPOINTS[(sh, sc)],
        "SH_set": sh,
        "SC_set": sc,
        "PLR_request": frac,
        "Q_request_W": q_req,
        "Q_delivered_W": delivered,
        "PLR_delivered": delivered / CAP,
        "SH_actual": sh_actual,
        "SC_actual": sc_actual,
        "dT_ref_evap": x[0] if ok else float("nan"),
        "dT_ref_cond": x[1] if ok else float("nan"),
        "dT_evap_effective": dt_evap_eff,
        "dT_cond_effective": dt_cond_eff,
        "T_evap": t_evap,
        "T_cond": t_cond,
        "r_p": _num(perf, "pr_cmp [-]") if ok else float("nan"),
        "n_star": _num(perf, "n_star [-]") if ok else float("nan"),
        "rps": (_num(perf, "cmp_rpm [rpm]") / 60.0) if ok else float("nan"),
        "m_dot_ref": _num(perf, "m_dot_ref [kg/s]") if ok else float("nan"),
        "eta_vol": _num(perf, "eta_cmp_vol [-]") if ok else float("nan"),
        "eta_isen": _num(perf, "eta_cmp_isen [-]") if ok else float("nan"),
        "eta_em": _num(perf, "eta_cmp [-]") if ok else float("nan"),
        "E_cmp": _num(perf, "E_cmp [W]") if ok else float("nan"),
        "E_iu_fan": _num(perf, "E_iu_fan [W]") if ok else float("nan"),
        "E_ou_fan": _num(perf, "E_ou_fan [W]") if ok else float("nan"),
        "E_tot_baseline": e_tot,
        "fan_fraction_ou": (_num(perf, "dV_ou_a [m3/s]") / m.dV_ou_fan_a_rated) if ok else float("nan"),
        "fan_fraction_iu": (_num(perf, "dV_iu_a [m3/s]") / m.dV_iu_fan_a_rated) if ok else float("nan"),
        "ou_fan_flow_min_limit": bool(perf.get("ou_fan_flow_min_limit", False)) if perf else False,
        "ou_fan_flow_max_limit": bool(perf.get("ou_fan_flow_max_limit", False)) if perf else False,
        "iu_fan_flow_min_limit": bool(perf.get("iu_fan_flow_min_limit", False)) if perf else False,
        "iu_fan_flow_max_limit": bool(perf.get("iu_fan_flow_max_limit", False)) if perf else False,
        "capacity_clamped": (perf.get("capacity_clamped") if perf else None),
        "pr_clamped": bool(pr_event is not None and pr_event[0] == "pr_below_min"),
        "converged": ok,
        "failure_reason": reason,
        "opt_success": opt_success,
        "V_disp_cm3": m.V_cmp_ref * 1e6,
        "rps_rated": m.rps_rated,
        "coefficient_version": COEFFICIENT_VERSION,
    }
    rows = []
    for w in W_FIXED:
        e_with = e_tot + w
        rows.append(
            {
                **base,
                "W_fixed": w,
                "E_tot_with_fixed": e_with,
                "COP_sys": delivered / e_with if e_with > 0 else float("nan"),
                "fixed_power_fraction": w / e_with if e_with > 0 else float("nan"),
            }
        )
    return rows


def _tasks() -> list[dict]:
    out = []
    for cond, duty, t_out, t_room, main in CONDITIONS:
        for mode in ("mode_A", "mode_B"):
            for sh, sc in SETPOINTS:
                for f in FRACTIONS:
                    out.append(
                        {
                            "condition": cond,
                            "duty": duty,
                            "T_outdoor": t_out,
                            "T_room": t_room,
                            "is_main_case": main,
                            "sh_sc_mode": mode,
                            "SH_set": sh,
                            "SC_set": sc,
                            "plr_request": f,
                        }
                    )
    return out


def simulate(jobs: int) -> pd.DataFrame:
    tasks = _tasks()
    with ProcessPoolExecutor(max_workers=jobs) as ex:
        chunks = list(ex.map(run_point, tasks, chunksize=2))
    df = pd.DataFrame([r for c in chunks for r in c])
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_DIR / CSV_NAME, index=False)
    solved = len(df) // len(W_FIXED)
    print(f"{CSV_NAME}: {len(df)} rows ({solved} solved points)")
    print(df[df.W_fixed == 0].groupby(["sh_sc_mode", "failure_reason"]).size().to_string())
    return df


# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------
def check(df: pd.DataFrame) -> dict:
    """Invariants that make the numbers readable, reported rather than assumed."""
    rep: dict = {}
    key = ["sh_sc_mode", "condition", "SH_set", "SC_set", "PLR_request"]
    ok = df[df.converged]

    # A: W_fixed is post-processing -- the state must not move with it.
    state_cols = ["T_evap", "T_cond", "r_p", "n_star", "dT_ref_evap", "dT_ref_cond", "E_tot_baseline"]
    spread = ok.groupby(key)[state_cols].agg(lambda s: float(s.max() - s.min()))
    worst = {c: float(spread[c].max()) for c in state_cols}
    rep["fixed_power_state_invariance_max_spread"] = worst
    rep["fixed_power_state_invariant"] = all(v == 0.0 for v in worst.values())

    # B: did the mode-B constraint actually bind?
    b = ok[(ok.sh_sc_mode == "mode_B") & (ok.W_fixed == 0)]
    tol = 0.05
    sh_bad = b[(b.SH_set - b.SH_actual).abs() > tol]
    sc_bad = b[(b.SC_set - b.SC_actual).abs() > tol]
    rep["mode_B_points"] = int(len(b))
    rep["mode_B_SH_not_achieved"] = int(len(sh_bad))
    rep["mode_B_SC_not_achieved"] = int(len(sc_bad))
    rep["mode_B_SH_max_shortfall_K"] = float((b.SH_set - b.SH_actual).abs().max()) if len(b) else None
    rep["mode_B_SC_max_shortfall_K"] = float((b.SC_set - b.SC_actual).abs().max()) if len(b) else None
    rep["mode_B_bound_violation_evap"] = int((b.dT_ref_evap < b.SH_set + DT_HX_MIN - 1e-6).sum())
    rep["mode_B_bound_violation_cond"] = int((b.dT_ref_cond < b.SC_set + DT_HX_MIN - 1e-6).sum())

    # counts of everything that must not be dropped silently
    z = df[df.W_fixed == 0]
    rep["points_total"] = int(len(z))
    rep["failures_by_reason"] = {k: int(v) for k, v in z.failure_reason.value_counts().items()}
    rep["capacity_clamped_counts"] = {
        str(k): int(v) for k, v in z.capacity_clamped.fillna("none").value_counts().items()
    }
    rep["pr_clamped_points"] = int(z.pr_clamped.sum())
    okz = z[z.converged]
    rep["fan_flow_floor_hits_ou"] = int((okz.fan_fraction_ou <= 0.0501).sum())
    rep["fan_flow_floor_hits_iu"] = int((okz.fan_fraction_iu <= 0.0501).sum())
    rep["fan_fraction_ou_min"] = float(okz.fan_fraction_ou.min())
    rep["fan_fraction_iu_min"] = float(okz.fan_fraction_iu.min())
    rep["fan_flow_min_limit_flags"] = int(okz[["ou_fan_flow_min_limit", "iu_fan_flow_min_limit"]].any(axis=1).sum())
    return rep


# ---------------------------------------------------------------------------
# figures -- style from validation/compressor_maps/final_figures.py
# ---------------------------------------------------------------------------
def _figures(df: pd.DataFrame) -> None:
    import dartwork_mpl as dm
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from scripts.visualization._dmpl_common import (
        COLORS,
        GRIDLINE,
        HAIRLINE,
        apply_style,
        finalize,
        panel_letter,
        ticks,
    )

    apply_style()
    MS = 2.6

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

    def grid(ax) -> None:
        ax.grid(True, alpha=0.25, linewidth=GRIDLINE)

    def pct(x):
        return 100.0 * x

    def split(g: pd.DataFrame):
        """Continuous modulation / speed floor / capacity ceiling."""
        g = g[g.converged].sort_values("PLR_request")
        return (
            g[g.capacity_clamped.isna()],
            g[g.capacity_clamped == "min"],
            g[g.capacity_clamped == "max"],
        )

    def curve(ax, g, col, color, label=None, ls="solid", ms=MS, marker="o"):
        """House convention: hollow markers past the compressor speed floor."""
        mod, floor, ceil = split(g)
        ax.plot(pct(mod.PLR_request), mod[col], ls=ls, lw=dm.lw(0), color=color, marker=marker, ms=ms, label=label)
        if marker and len(floor):
            ax.plot(pct(floor.PLR_request), floor[col], ls="none", marker=marker, ms=ms, mfc="white", mec=color, mew=HAIRLINE)
        elif len(floor):
            ax.plot(pct(floor.PLR_request), floor[col], ls=(0, (1, 2)), lw=dm.lw(0), color=color)
        if len(ceil):
            ax.plot(pct(ceil.PLR_request), ceil[col], ls="none", marker="x", ms=ms + 0.6, color=color, mew=HAIRLINE)

    def xaxis(ax, label=True) -> None:
        ax.set_xlim(0, 100)
        ax.set_xticks(ticks(0, 100, 20))
        if label:
            ax.set_xlabel("Requested part-load ratio [%]")
        grid(ax)

    cond_title = {
        "heating_7": "Heating 7/20 °C (main)",
        "cooling_35": "Cooling 35/27 °C",
        "heating_m7": "Heating −7/20 °C (secondary)",
    }
    cond_color = {"heating_7": COLORS["hot"], "cooling_35": COLORS["cool"], "heating_m7": COLORS["accent2"]}
    CONDS = ["heating_7", "cooling_35", "heating_m7"]
    case_palette = [
        COLORS["ink"],
        COLORS["muted"],
        COLORS["cool"],
        COLORS["ess"],
        COLORS["accent"],
        COLORS["accent2"],
        COLORS["accent3"],
    ]
    case_color = {p: case_palette[i] for i, p in enumerate(CASE_ORDER)}

    def sel(mode: str, sh: float, sc: float, cond: str, w: float = 0.0) -> pd.DataFrame:
        return df[
            (df.sh_sc_mode == mode)
            & (df.SH_set == sh)
            & (df.SC_set == sc)
            & (df.condition == cond)
            & (df.W_fixed == w)
        ]

    # -- 01 fixed power: COP vs PLR --------------------------------------
    fig, axes = plt.subplots(1, 3, figsize=dm.figsize("17cm", 0.34))
    w_ls = {0.0: "solid", 25.0: (0, (4, 1.6)), 50.0: (0, (2, 1.2)), 100.0: (0, (1, 1.2))}
    for ax, cond, letter in zip(axes, CONDS, "abc", strict=True):
        for w in W_FIXED:
            curve(
                ax,
                sel("mode_A", *CURRENT, cond, w),
                "COP_sys",
                cond_color[cond],
                label=f"$W_{{fixed}}$ = {w:g} W",
                ls=w_ls[w],
            )
        xaxis(ax)
        ax.set_ylabel(r"System $\mathrm{COP}$ [-]")
        ax.set_title(cond_title[cond], fontsize=dm.fs(-2), loc="left")
        ax.legend(loc="upper right", frameon=False, fontsize=dm.fs(-4))
        panel_letter(ax, letter)
    save(fig, "01_fixed_power_cop_vs_plr", mt="8%")

    # -- 02 fixed-power fraction -----------------------------------------
    fig, axes = plt.subplots(1, 3, figsize=dm.figsize("17cm", 0.34))
    for ax, cond, letter in zip(axes, CONDS, "abc", strict=True):
        for w in W_FIXED[1:]:
            curve(ax, sel("mode_A", *CURRENT, cond, w), "fixed_power_fraction", cond_color[cond], label=f"{w:g} W", ls=w_ls[w])
        xaxis(ax)
        ax.set_ylim(0, 0.2)
        ax.set_yticks(ticks(0, 0.2, 0.05))
        ax.set_ylabel(r"$W_{fixed}\,/\,E_{tot}$ [-]")
        ax.set_title(cond_title[cond], fontsize=dm.fs(-2), loc="left")
        ax.legend(loc="upper left", frameon=False, fontsize=dm.fs(-4))
        panel_letter(ax, letter)
    save(fig, "02_fixed_power_fraction_vs_plr", mt="8%")

    # -- 03 low-load summary ---------------------------------------------
    rows = []
    for cond in CONDS:
        for w in W_FIXED:
            g = sel("mode_A", *CURRENT, cond, w)
            mod, _, _ = split(g)
            if not len(mod):
                continue
            lo = mod.iloc[0]
            hi = mod.iloc[-1]
            rows.append(
                {
                    "condition": cond,
                    "W_fixed": w,
                    "PLR_low": lo.PLR_request,
                    "COP_low": lo.COP_sys,
                    "COP_rated": hi.COP_sys,
                    "ratio": lo.COP_sys / hi.COP_sys,
                }
            )
    s = pd.DataFrame(rows)
    fig, axes = plt.subplots(1, 2, figsize=dm.figsize("13cm", 0.45))
    ax = axes[0]
    for cond, g in s.groupby("condition"):
        ax.plot(g.W_fixed, g.ratio, "o-", ms=MS, lw=dm.lw(0), color=cond_color[cond], label=cond_title[cond])
    ax.axhline(1.0, color=COLORS["muted"], lw=HAIRLINE, ls=":")
    ax.set_xlim(0, 100)
    ax.set_xticks(ticks(0, 100, 25))
    ax.set_xlabel(r"$W_{fixed}$ [W]")
    ax.set_ylabel(r"$\mathrm{COP}_{low}\,/\,\mathrm{COP}_{rated}$ [-]")
    ax.legend(loc="lower left", frameon=False, fontsize=dm.fs(-4))
    grid(ax)
    panel_letter(ax, "a")
    ax = axes[1]
    for cond, g in s.groupby("condition"):
        base = float(g[g.W_fixed == 0].COP_low.iloc[0])
        ax.plot(g.W_fixed, (g.COP_low / base - 1.0) * 100.0, "o-", ms=MS, lw=dm.lw(0), color=cond_color[cond])
    ax.axhline(0.0, color=COLORS["muted"], lw=HAIRLINE, ls=":")
    ax.set_xlim(0, 100)
    ax.set_xticks(ticks(0, 100, 25))
    ax.set_xlabel(r"$W_{fixed}$ [W]")
    ax.set_ylabel(r"$\Delta\mathrm{COP}_{low}$ vs current TMHP [%]")
    grid(ax)
    panel_letter(ax, "b")
    s.to_csv(OUT_DIR / "fig03_fixed_power_lowload_summary.csv", index=False)
    save(fig, "03_fixed_power_lowload_summary", mt="5%")

    # -- 04..08 case comparisons, mode A solid / mode B dashed ------------
    def case_panels(name: str, quantities: list[tuple[str, str, tuple | None, float | None]], margins: dict) -> None:
        nq = len(quantities)
        fig, axes = plt.subplots(nq, 3, figsize=dm.figsize("17cm", 0.30 * nq + 0.06), squeeze=False,
                                 gridspec_kw={"wspace": 0.42, "hspace": 0.45})
        for i, (col, ylab, ylim, ystep) in enumerate(quantities):
            for j, cond in enumerate(CONDS):
                ax = axes[i][j]
                # Mode B underneath as a bare dashed line, mode A on top with
                # markers: in mode A the high-setpoint cases collapse onto one
                # another, and a shared marker style would hide that under
                # whichever case happened to be drawn last.
                for p in CASE_ORDER:
                    curve(ax, sel("mode_B", p[0], p[1], cond), col, case_color[p], ls=(0, (3, 1.5)), marker="")
                for p in CASE_ORDER:
                    curve(ax, sel("mode_A", p[0], p[1], cond), col, case_color[p], label=SETPOINTS[p] if (i == 0 and j == 0) else None, ms=MS * 0.75)
                xaxis(ax, label=(i == nq - 1))
                if ylim is not None:
                    ax.set_ylim(*ylim)
                    if ystep:
                        ax.set_yticks(ticks(ylim[0], ylim[1], ystep))
                ax.set_ylabel(ylab)
                if i == 0:
                    ax.set_title(cond_title[cond], fontsize=dm.fs(-2.5), loc="left")
                panel_letter(ax, "abcdefghi"[i * 3 + j], x=-0.30, y=1.06)
        axes[0][0].legend(loc="best", frameon=False, fontsize=dm.fs(-5), ncol=2)
        axes[0][2].text(0.03, 0.04, "solid: mode A (clipped)\ndashed: mode B (constrained)", transform=axes[0][2].transAxes, fontsize=dm.fs(-4.5), va="bottom")
        save(fig, name, **margins)

    case_panels(
        "04_actual_sh_sc_vs_plr",
        [("SH_actual", r"Actual superheat [K]", (0, 12), 2.0), ("SC_actual", r"Actual subcooling [K]", (0, 10), 2.0)],
        {"mt": "6%", "ml": "3%"},
    )
    case_panels(
        "05_approach_vs_plr",
        [(r"dT_ref_evap", r"$\Delta T_{approach,evap}$ [K]", (0, 20), 4.0), ("dT_ref_cond", r"$\Delta T_{approach,cond}$ [K]", (0, 20), 4.0)],
        {"mt": "6%", "ml": "3%"},
    )
    case_panels(
        "06_saturation_temperature_vs_plr",
        [("T_evap", r"$T_{evap}$ [°C]", None, None), ("T_cond", r"$T_{cond}$ [°C]", None, None)],
        {"mt": "6%", "ml": "3%"},
    )
    case_panels("07_pressure_ratio_vs_plr", [("r_p", r"Pressure ratio $r_p$ [-]", None, None)], {"mt": "10%", "ml": "3%"})
    case_panels("08_nstar_vs_plr", [("n_star", r"Relative speed $n^*$ [-]", (0, 1.6), 0.4)], {"mt": "10%", "ml": "3%"})

    # -- 09 SH/SC COP, one row per mode ----------------------------------
    fig, axes = plt.subplots(2, 3, figsize=dm.figsize("17cm", 0.62), squeeze=False,
                             gridspec_kw={"wspace": 0.40, "hspace": 0.45})
    for i, (mode, mlab) in enumerate((("mode_A", "mode A — shipped clipping"), ("mode_B", "mode B — constrained"))):
        for j, cond in enumerate(CONDS):
            ax = axes[i][j]
            for p in CASE_ORDER:
                curve(ax, sel(mode, p[0], p[1], cond), "COP_sys", case_color[p], label=SETPOINTS[p] if (i == 0 and j == 0) else None, ms=MS * 0.8)
            xaxis(ax, label=(i == 1))
            ax.set_ylabel(r"System $\mathrm{COP}$ [-]")
            ax.set_title(f"{cond_title[cond]} — {mlab}", fontsize=dm.fs(-3), loc="left")
            panel_letter(ax, "abcdef"[i * 3 + j], x=-0.28, y=1.06)
    axes[0][0].legend(loc="best", frameon=False, fontsize=dm.fs(-5), ncol=2)
    save(fig, "09_sh_sc_cop_vs_plr", mt="6%", ml="3%")

    # -- 10 / 11 SH-only and SC-only -------------------------------------
    def split_fig(name: str, pairs: list[tuple[float, float]], fixed_txt: str, label_fn) -> None:
        fig, axes = plt.subplots(2, 3, figsize=dm.figsize("17cm", 0.62), squeeze=False,
                                 gridspec_kw={"wspace": 0.40, "hspace": 0.45})
        cmap = plt.get_cmap("viridis")
        cols = [cmap(0.1 + 0.75 * k / max(len(pairs) - 1, 1)) for k in range(len(pairs))]
        for i, (mode, mlab) in enumerate((("mode_A", "mode A"), ("mode_B", "mode B"))):
            for j, cond in enumerate(CONDS):
                ax = axes[i][j]
                for p, c in zip(pairs, cols, strict=True):
                    curve(ax, sel(mode, p[0], p[1], cond), "COP_sys", c, label=label_fn(p) if (i == 0 and j == 0) else None, ms=MS * 0.8)
                xaxis(ax, label=(i == 1))
                ax.set_ylabel(r"System $\mathrm{COP}$ [-]")
                ax.set_title(f"{cond_title[cond]} — {mlab}, {fixed_txt}", fontsize=dm.fs(-3), loc="left")
                panel_letter(ax, "abcdef"[i * 3 + j], x=-0.28, y=1.06)
        axes[0][0].legend(loc="best", frameon=False, fontsize=dm.fs(-4.5))
        save(fig, name, mt="6%", ml="3%")

    split_fig("10_sh_only_cop_vs_plr", SH_ONLY, "SC = 5 K", lambda p: f"SH = {p[0]:g} K")
    split_fig("11_sc_only_cop_vs_plr", SC_ONLY, "SH = 5 K", lambda p: f"SC = {p[1]:g} K")

    # -- 12 combined ------------------------------------------------------
    fig, axes = plt.subplots(1, 3, figsize=dm.figsize("17cm", 0.34))
    combos = (
        ("mode_A", CURRENT, 0.0, COLORS["ink"], "solid", "current TMHP"),
        ("mode_A", CURRENT, 50.0, COLORS["accent"], (0, (4, 1.6)), r"+ $W_{fixed}$ = 50 W"),
        ("mode_B", (5.0, 5.0), 50.0, COLORS["warm"], (0, (1, 1.2)), r"+ 50 W + fixed SH/SC 5/5 K"),
    )
    for ax, cond, letter in zip(axes, CONDS, "abc", strict=True):
        for mode, p, w, color, ls, lbl in combos:
            curve(ax, sel(mode, p[0], p[1], cond, w), "COP_sys", color, label=lbl, ls=ls)
        xaxis(ax)
        ax.set_ylabel(r"System $\mathrm{COP}$ [-]")
        ax.set_title(cond_title[cond], fontsize=dm.fs(-2), loc="left")
        ax.legend(loc="upper right", frameon=False, fontsize=dm.fs(-4))
        panel_letter(ax, letter)
    save(fig, "12_combined_effect_cop_vs_plr", mt="8%")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jobs", type=int, default=int(os.environ.get("SIM_JOBS", "16")))
    ap.add_argument("--only", nargs="*", default=None, choices=["sim", "fig"])
    a = ap.parse_args()
    want = set(a.only or ["sim", "fig"])
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    started = datetime.now(UTC).isoformat()
    if "sim" in want:
        df = simulate(a.jobs)
    else:
        df = pd.read_csv(OUT_DIR / CSV_NAME)
    rep = check(df)
    print(json.dumps(rep, indent=2, ensure_ascii=False))
    if "fig" in want:
        _figures(df)
    manifest = {
        "script": str(Path(__file__).resolve().relative_to(REPO_ROOT)),
        "purpose": "one-off low-load sensitivity: fixed electrical power and superheat/subcooling",
        "started_utc": started,
        "finished_utc": datetime.now(UTC).isoformat(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "coefficient_version": COEFFICIENT_VERSION,
        "capacity_W": CAP,
        "refrigerant": REF,
        "plr_fractions": list(FRACTIONS),
        "W_fixed_W": list(W_FIXED),
        "conditions": [
            {"key": k, "duty": d, "T_outdoor_C": t, "T_room_C": r, "main": m} for k, d, t, r, m in CONDITIONS
        ],
        "setpoints": [{"SH_set": sh, "SC_set": sc, "label": lbl} for (sh, sc), lbl in SETPOINTS.items()],
        "sh_sc_modes": {
            "mode_A": "shipped model; actual_SH/SC clipped by the optimised approach",
            "mode_B": (
                "per-variable approach floors dT_ref_evap >= SH + dT_hx_min and "
                "dT_ref_cond >= SC + dT_hx_min, imposed by rewriting the bounds "
                "kwarg of scipy.optimize.minimize inside this module only"
            ),
        },
        "dT_hx_min": DT_HX_MIN,
        "approach_bounds_shipped": [APPROACH_LO, APPROACH_HI],
        "src_modified": False,
        "checks": rep,
        "outputs": sorted(p.name for p in OUT_DIR.iterdir() if p.is_file()),
    }
    (OUT_DIR / "run_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print("wrote", OUT_DIR)


if __name__ == "__main__":
    main()
