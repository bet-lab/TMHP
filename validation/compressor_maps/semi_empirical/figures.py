"""Figures of the archive: one y-axis, at most four series per panel, colour order fixed, thin lines."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from validation.compressor_maps.semi_empirical import pipeline as P  # noqa: E402
from validation.compressor_maps.semi_empirical.model import Data, machine_weights  # noqa: E402

C = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100")
DPI = 100


def _style(ax, xlabel: str, ylabel: str) -> None:
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.tick_params(labelsize=8)
    ax.legend(frameon=False, fontsize=7)


def _normalised(form, st, rows: pd.DataFrame, level: float) -> np.ndarray:
    """Data moved to the default level: y * eta(default level) / eta(own unit level), row by row."""
    d = Data.of(rows, form.speed)
    own = form.predict(st, d, {k: st.levels.get(k, level) for k in d.units})
    return d.y * form.predict(st, d, level) / own


def fig_eta_vs_pr(path: Path, d, chosen, level_value, twin_r410a) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.8), sharey=True)
    pr = np.linspace(1.3, 10.0, 80)
    rng = np.random.default_rng(0)
    for ax, (t, fam) in zip(
        axes, (("scroll", "scroll"), ("rotary_twin_inverter", "rotary"), ("rotary_single_fixed", "rotary")), strict=True
    ):
        form, st = chosen[fam]["form"], chosen[fam]["state"]
        lv = twin_r410a if (t == "rotary_twin_inverter" and twin_r410a) else level_value[t]
        rows = P.shape_rows(d, fam)
        if len(rows) > 2500:
            rows = rows.iloc[np.sort(rng.choice(len(rows), 2500, replace=False))]
        ax.scatter(
            rows.PR,
            _normalised(form, st, rows, lv),
            s=3,
            color=C[3],
            alpha=0.25,
            lw=0,
            label="data, moved to the default level",
        )
        for i, n in enumerate((20.0, 60.0, 90.0)):
            e = P.eval_eta(form, st, lv, "R410A", pr, n, 45.0, 10.0, P.N_RATED_DEFAULT[t])
            ax.plot(pr, e, color=C[i], lw=1.1, label=f"model N = {n:g} rev/s")
        ax.axvline(rows.PR.max(), color="0.6", lw=0.6, ls=":")
        ax.set_title(f"{t} (R410A, SDT 45 C)", fontsize=9)
        ax.set_ylim(0.2, 0.9)
        _style(ax, "pressure ratio", "eta_oi (terminal basis)" if t == "scroll" else "")
    fig.tight_layout()
    fig.savefig(path, dpi=DPI)
    plt.close(fig)


def fig_residuals(path: Path, d, chosen) -> None:
    fig, axes = plt.subplots(1, 4, figsize=(14, 3.6))
    specs = [
        ("scroll", "PR", [1.4, 2, 3, 4, 5, 6, 7, 9]),
        ("scroll", "N_rps", [0, 25, 35, 45, 55, 65, 75, 95]),
        ("rotary", "PR", [1.8, 2.5, 3, 3.5, 4, 5, 6, 7]),
        ("rotary", "N_rps", [25, 35, 45, 55, 65, 80, 100, 125]),
    ]
    for ax, (fam, col, bins) in zip(axes, specs, strict=True):
        form, st = chosen[fam]["form"], chosen[fam]["state"]
        rows = P.shape_rows(d, fam)
        dd = Data.of(rows, form.speed)
        rows = rows.assign(e=form.predict(st, dd, st.levels) - dd.y, w=machine_weights(rows))
        key = "group" if fam == "scroll" else "source_id"
        for i, (g, t) in enumerate(rows.groupby(key)):
            b = t.groupby(pd.cut(t[col], bins), observed=True)
            x = b[col].mean()
            y = b.apply(lambda h: np.average(h.e, weights=h.w), include_groups=False)
            if len(x) == 1:
                ax.plot(x, y, "o", color=C[i % 4], ms=3, label=g)
            else:
                ax.plot(x, y, "-o", color=C[i % 4], lw=1, ms=3, label=g)
        ax.axhline(0, color="0.6", lw=0.6)
        ax.set_ylim(-0.06, 0.06)
        ax.set_title(f"{fam}: mean residual by {'PR' if col == 'PR' else 'speed'}", fontsize=9)
        _style(ax, "pressure ratio" if col == "PR" else "N [rev/s]", "model - data (eta_oi)" if ax is axes[0] else "")
    fig.tight_layout()
    fig.savefig(path, dpi=DPI)
    plt.close(fig)


def _speed_ratios(rows: pd.DataFrame, n_ref: float = 60.0) -> pd.DataFrame:
    out = []
    for (_, _, _), g in rows.groupby(["unit", "T_evap_C", "T_cond_C"]):
        if g.N_rps.nunique() < 2:
            continue
        ref = g.iloc[(g.N_rps - n_ref).abs().argsort()[:1]]
        out.append(pd.DataFrame({"N": g.N_rps, "ratio": g.eta_oi.to_numpy() / float(ref.eta_oi.iloc[0])}))
    return pd.concat(out) if out else pd.DataFrame(columns=["N", "ratio"])


def fig_speed(path: Path, d, chosen, level_value, shao_alt) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.8), sharey=True)
    n = np.linspace(15, 120, 100)
    for ax, fam, srcs in (
        (axes[0], "scroll", ("bitzer", "copeland_opi")),
        (axes[1], "rotary", ("shao_2004", "ossorio_2019_cnit")),
    ):
        form, st = chosen[fam]["form"], chosen[fam]["state"]
        lv = level_value["scroll" if fam == "scroll" else "rotary_twin_inverter"]
        e = np.array([P.eval_at_condition(form, st, lv, "R410A", 0.0, 50.0, 10.0, x) for x in n])
        e60 = P.eval_at_condition(form, st, lv, "R410A", 0.0, 50.0, 10.0, 60.0)
        ax.plot(n, e / e60, color=C[0], lw=1.2, label="model, R410A 0/50 C")
        rows = P.shape_rows(d, fam)
        for i, s in enumerate(srcs, start=1):
            r = _speed_ratios(rows[rows.source_id == s])
            if r.empty:
                continue
            b = r.groupby(r.N.round(0)).ratio.median()
            ax.plot(b.index, b.values, "o", ms=3.5, color=C[i], label=f"{s} (median ratio)")
        if fam == "rotary":
            st_alt, lv_alt = shao_alt
            e2 = np.array([P.eval_at_condition(form, st_alt, lv_alt, "R410A", 0.0, 50.0, 10.0, x) for x in n])
            e2_60 = P.eval_at_condition(form, st_alt, lv_alt, "R410A", 0.0, 50.0, 10.0, 60.0)
            ax.plot(n, e2 / e2_60, color=C[3], lw=1.0, ls="--", label="model if Shao power incl. inverter")
        ax.set_title(f"{fam}: eta_oi(N) / eta_oi(60 rev/s)", fontsize=9)
        ax.set_ylim(0.6, 1.15)
        _style(ax, "N [rev/s]", "ratio" if fam == "scroll" else "")
    fig.tight_layout()
    fig.savefig(path, dpi=DPI)
    plt.close(fig)


def fig_inverter(path: Path, inv_data: pd.DataFrame, inv_fit) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.6), sharey=True)
    n = np.linspace(15, 120, 100)
    for ax, s in zip(axes, ("ossorio2023", "ossorio2019"), strict=True):
        ax.plot(n, inv_fit.eta(n), color=C[0], lw=1.2, label="model I1, median level")
        t = inv_data[inv_data.set == s]
        for i, (k, g) in enumerate(t.groupby("inverter"), start=1):
            ax.plot(g.N, g.eta, "o", ms=2.5, color=C[i], alpha=0.8, label=f"inverter {k}")
        ax.set_ylim(0.85, 1.0)
        ax.set_title(f"drive efficiency, {s}", fontsize=9)
        _style(ax, "N [rev/s]", "eta_inv" if s == "ossorio2023" else "")
    fig.tight_layout()
    fig.savefig(path, dpi=DPI)
    plt.close(fig)


def fig_levels(path: Path, chosen, levels_table: pd.DataFrame) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.8), sharey=True)
    form, st = chosen["scroll"]["form"], chosen["scroll"]["state"]
    sc = levels_table[levels_table.family == "scroll"].copy()
    ref = P.RATING_REF
    sc["eta"] = [
        P.eval_at_condition(
            form, st, a, ref["refrigerant"], ref["T_evap_C"], ref["T_cond_C"], ref["superheat_K"], ref["N"]
        )
        for a in sc.a
    ]
    for i, g in enumerate(("Copeland", "AREP", "literature", "BITZER")):
        t = sc[sc.group == g]
        ax = axes[0]
        ax.plot(t.V_disp_cm3, t.eta, "o", ms=3.5, color=C[i], label=g)
    axes[0].axvline(80, color="0.6", lw=0.6, ls=":")
    axes[0].set_xscale("log")
    axes[0].set_xticks([20, 50, 100, 200, 400])
    axes[0].xaxis.set_major_formatter(matplotlib.ticker.ScalarFormatter())
    axes[0].xaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
    axes[0].set_title("scroll: machine level as eta_oi at R410A 7.2/54.4 C, 60 rev/s", fontsize=9)
    _style(axes[0], "displacement [cm3/rev]", "eta_oi (terminal basis)")
    ro = levels_table[levels_table.family == "rotary"]
    groups = [
        ("twin inverter R32", (ro.level_class == "rotary_twin_inverter") & (ro.refrigerant == "R32")),
        ("twin inverter R410A", (ro.level_class == "rotary_twin_inverter") & (ro.refrigerant == "R410A")),
        ("single fixed R410A", (ro.level_class == "rotary_single_fixed") & (ro.refrigerant == "R410A")),
        ("single fixed R22 / R290", (ro.level_class == "rotary_single_fixed") & ro.refrigerant.isin(["R22", "R290"])),
    ]
    for i, (lab, m) in enumerate(groups):
        t = ro[m]
        axes[1].plot(t.V_disp_cm3, t.eta_oi, "o", ms=3.5, color=C[i], label=lab)
    axes[1].set_title("rotary rating points (PR 3.4): measured eta_oi", fontsize=9)
    _style(axes[1], "displacement [cm3/rev]", "")
    fig.tight_layout()
    fig.savefig(path, dpi=DPI)
    plt.close(fig)


def make_all(out: Path, *, d, chosen, level_value, twin_r410a, inv_data, inv_fit, levels_table, shao_alt) -> None:
    fig_eta_vs_pr(out / "fig1_eta_oi_vs_pr.png", d, chosen, level_value, twin_r410a)
    fig_residuals(out / "fig2_residuals_by_pr_and_speed.png", d, chosen)
    fig_speed(out / "fig3_speed_dependence.png", d, chosen, level_value, shao_alt)
    fig_inverter(out / "fig4_inverter_model.png", inv_data, inv_fit)
    fig_levels(out / "fig5_level_vs_displacement.png", chosen, levels_table)
