"""Power breakdown and COP definition figures for the PLR power/COP page.

The question this set answers is narrow: at a fixed boundary condition, how do
``E_cmp`` and the fan terms split the total electrical input as the part-load
ratio falls, and how much of the gap between ``cop_ref`` and ``cop_sys`` that
split accounts for.

Nothing is re-simulated. Both maps come from ``final_report`` -- the same
baseline the narrative page draws on -- so the numbers here and there are the
same run. The symbols are the library's own::

    ASHP    E_tot = E_cmp + E_ou_fan + E_iu_fan,  cop_ref = Q_ref_iu / E_cmp
    ASHPB   E_tot = E_cmp + E_ou_fan,             cop_ref = Q_ref_tank / E_cmp
            cop_sys = Q / E_tot   in both

``plr_map_*.csv`` stores ``cop_sys`` under that name and ``Q/E_cmp`` under
``cop_cmp``; the latter is a local alias of the library's ``cop_ref [-]`` and is
recomputed here from ``q_delivered_W / E_cmp`` so the figure axis and the model
source say the same word.

A1-A3  power breakdown against PLR, one model-duty per figure
B1-B3  cop_ref and cop_sys against PLR, same conditions
C1-C3  fan share of E_tot across every outdoor temperature

Page rules follow ``professor_summary_figures``: one duty per canvas, type
sized for a Notion column, legend above the axes.

Run::

    uv run python3 -m validation.diagnostics.power_cop_breakdown
"""

from __future__ import annotations

import argparse

import dartwork_mpl as dm
import matplotlib

matplotlib.use("Agg")
import matplotlib as mpl  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from scripts.visualization._dmpl_common import (  # noqa: E402
    COLORS,
    GRIDLINE,
    HAIRLINE,
    apply_style,
    finalize,
    ticks,
)

from tmhp import compressor_efficiency as ce  # noqa: E402
from validation.compressor_maps.schema import REPO_ROOT  # noqa: E402

# The legend/title headroom measurement is shared with the narrative page's
# figure set rather than copied, so the two pages cannot drift apart on it.
from validation.diagnostics.professor_summary_figures import (  # noqa: E402
    _title_headroom,
    _top_legend,
)

SIM = REPO_ROOT / "validation" / "data" / "final_report"
OUT = REPO_ROOT / "validation" / "data" / "power_cop"
FIG_DIR = REPO_ROOT / "validation" / "coefficients" / ce.COEFFICIENT_VERSION / "figures" / "power_cop"

FS_BASE = 11.0
FS_TITLE = 0.0
FS_LEGEND = -0.5
MS = 3.0

# One representative boundary condition per model-duty. Heating 5 degC is the
# available grid point nearest the EN 14825 rating point; cooling 35 degC is
# the rating point itself.
CASES = (
    ("A_heating", "plr_map_ashp.csv", "ASHP", "heating", 5.0,
     "[ASHP] Heating · outdoor 5 °C, room 20 °C"),
    ("A_cooling", "plr_map_ashp.csv", "ASHP", "cooling", 35.0,
     "[ASHP] Cooling · outdoor 35 °C, room 27 °C"),
    ("B_heating", "plr_map_ashpb.csv", "ASHPB", "heating", 5.0,
     "[ASHPB] Heating · outdoor 5 °C, tank 42.5 °C"),
)

MAPS = {
    "ASHP": ("plr_map_ashp.csv", {"heating": "viridis", "cooling": "plasma"}),
    "ASHPB": ("plr_map_ashpb.csv", {"heating": "viridis"}),
}

STACK = (
    ("E_cmp", r"$E_{cmp}$", COLORS["accent"]),
    ("E_ou_fan", r"$E_{ou\_fan}$", COLORS["warm"]),
    ("E_iu_fan", r"$E_{iu\_fan}$", COLORS["ess"]),
)


def _style() -> None:
    apply_style()
    mpl.rcParams.update({
        "font.size": FS_BASE,
        "axes.labelsize": FS_BASE,
        "axes.titlesize": FS_BASE,
        "xtick.labelsize": FS_BASE - 1.0,
        "ytick.labelsize": FS_BASE - 1.0,
        "legend.fontsize": FS_BASE - 0.5,
    })


def _save(fig, name: str, **margins) -> None:
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    for extra in (0, 2, 4, 6, 8):
        try:
            kw = {k: f"{float(v.rstrip('%')) + extra}%" for k, v in margins.items()}
            finalize(fig, FIG_DIR / name, formats=("svg", "png"), margin=f"{2 + extra}%", **kw)
            break
        except RuntimeError as exc:
            if extra == 8:
                raise exc
    plt.close(fig)


def _save_top_legend(fig, ax, name, *, ncol=None, gap: float = 2.0, **margins) -> None:
    frac = _top_legend(fig, ax, ncol=ncol, fs=FS_LEGEND) + _title_headroom(fig)
    _save(fig, name, mt=f"{100 * frac + gap:.1f}%", **margins)


def _grid(ax) -> None:
    ax.grid(True, alpha=0.25, linewidth=GRIDLINE)


def _load(fname: str, duty: str, t_out: float | None = None) -> pd.DataFrame:
    d = pd.read_csv(SIM / fname)
    d = d[(d.duty == duty) & (d.failure_reason == "none")]
    if t_out is not None:
        d = d[d.t_outdoor_C == t_out]
    d = d.sort_values("plr_request").copy()
    # ``cop_ref`` is the library's name for Q over compressor input alone; the
    # map stores it as ``cop_cmp``. Recompute rather than rename so the column
    # and the axis label are the same definition.
    d["cop_ref"] = d.q_delivered_W / d.E_cmp
    d["fan_share_pct"] = 100.0 * (d.E_tot - d.E_cmp) / d.E_tot
    d["plr_pct"] = 100.0 * d.plr_request
    return d


def _floor_span(d: pd.DataFrame) -> float:
    """Highest requested PLR still served at the speed floor, in percent."""
    clamped = d[d.capacity_clamped == "min"]
    return float(clamped.plr_pct.max()) if len(clamped) else 0.0


def _floor_band(ax, d: pd.DataFrame) -> None:
    """Shade the requested-PLR range the machine cannot follow.

    The band starts at the lowest simulated request rather than at zero: below
    that there is no point to stand on, and a band running to the axis would
    claim one.
    """
    x = _floor_span(d)
    if x <= 0:
        return
    x0 = float(d.plr_pct.min())
    ax.axvspan(x0, x, color=COLORS["band20"], alpha=0.5, lw=0, zorder=0)
    ax.text(0.5 * (x0 + x), 0.985, "speed\nfloor", transform=ax.get_xaxis_transform(),
            fontsize=dm.fs(-2.0), color=COLORS["ink"], ha="center", va="top",
            linespacing=1.1)


def _xaxis(ax) -> None:
    ax.set_xlim(0, 100)
    ax.set_xticks(ticks(0, 100, 20))
    ax.set_xlabel("Requested part-load ratio [%]")


def _split(d: pd.DataFrame):
    return d[d.capacity_clamped.isna()], d[d.capacity_clamped == "min"]


def _curve(ax, d, col, color, marker, label=None):
    """Solid line with filled markers above the floor, open markers on it."""
    mod, floor = _split(d)
    ax.plot(mod.plr_pct, mod[col], lw=dm.lw(0), color=color, marker=marker, ms=MS, label=label)
    if len(floor):
        ax.plot(floor.plr_pct, floor[col], ls="none", marker=marker, ms=MS,
                mfc="white", mec=color, mew=HAIRLINE)


# ---------------------------------------------------------------------------
# A  power breakdown
# ---------------------------------------------------------------------------
def a_power_breakdown() -> None:
    for key, fname, model, duty, t_out, title in CASES:
        d = _load(fname, duty, t_out)
        terms = [(c, lab, col) for c, lab, col in STACK if d[c].abs().max() > 0]
        fig, ax = plt.subplots(figsize=dm.figsize("12cm", 0.60))
        _floor_band(ax, d)
        base = 0.0 * d.plr_pct
        for col, lab, colour in terms:
            top = base + d[col]
            ax.fill_between(d.plr_pct, base, top, color=colour, alpha=0.85, lw=0, label=lab)
            base = top
        ax.plot(d.plr_pct, d.E_tot, lw=dm.lw(0), color=COLORS["ink"],
                marker="o", ms=MS, label=r"$E_{tot}$")
        _xaxis(ax)
        ax.set_ylim(0, None)
        ax.set_ylabel("Electrical power input [W]")
        ax.set_title(title, fontsize=dm.fs(FS_TITLE), loc="left")
        _grid(ax)
        _save_top_legend(fig, ax, f"A_power_breakdown_{key}")
        d.to_csv(OUT / f"power_cop_{key}.csv", index=False)


# ---------------------------------------------------------------------------
# B  cop_ref against cop_sys
# ---------------------------------------------------------------------------
def b_cop_definitions() -> None:
    for key, fname, model, duty, t_out, title in CASES:
        d = _load(fname, duty, t_out)
        fig, ax = plt.subplots(figsize=dm.figsize("12cm", 0.60))
        _floor_band(ax, d)
        _curve(ax, d, "cop_ref", COLORS["accent"], "o", label=r"$cop_{ref} = Q\,/\,E_{cmp}$")
        _curve(ax, d, "cop_sys", COLORS["accent3"], "s", label=r"$cop_{sys} = Q\,/\,E_{tot}$")
        _xaxis(ax)
        ax.set_ylabel(r"$\mathrm{COP}$ [-]")
        ax.set_title(title, fontsize=dm.fs(FS_TITLE), loc="left")
        _grid(ax)
        _save_top_legend(fig, ax, f"B_cop_ref_sys_{key}")


# ---------------------------------------------------------------------------
# C  fan share of E_tot over the whole outdoor range
# ---------------------------------------------------------------------------
def c_fan_share() -> None:
    for model, (fname, cmaps) in MAPS.items():
        for duty, cmap_name in cmaps.items():
            d = _load(fname, duty)
            temps = sorted(d.t_outdoor_C.unique())
            cmap = plt.get_cmap(cmap_name)
            fig, ax = plt.subplots(figsize=dm.figsize("12cm", 0.60))
            for i, t in enumerate(temps):
                col = cmap(0.1 + 0.8 * i / max(len(temps) - 1, 1))
                _curve(ax, d[d.t_outdoor_C == t], "fan_share_pct", col, "o", label=f"{t:g} °C")
            _xaxis(ax)
            ax.set_ylabel(r"$(E_{tot} - E_{cmp})\,/\,E_{tot}$ [%]")
            head = "Heating, room 20 °C" if model == "ASHP" else "Heating, tank 42.5 °C"
            if duty == "cooling":
                head = "Cooling, room 27 °C"
            ax.set_title(f"[{model}] {head}", fontsize=dm.fs(FS_TITLE), loc="left")
            _grid(ax)
            _save_top_legend(fig, ax, f"C_fan_share_{model}_{duty}")
            d.to_csv(OUT / f"fan_share_{model}_{duty}.csv", index=False)


FIGURES = (("A", a_power_breakdown), ("B", b_cop_definitions), ("C", c_fan_share))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="+", metavar="KEY")
    args = ap.parse_args()
    _style()
    OUT.mkdir(parents=True, exist_ok=True)
    for name, fn in FIGURES:
        if args.only and name not in args.only:
            continue
        print(name, flush=True)
        fn()
    print(f"figures -> {FIG_DIR}")


if __name__ == "__main__":
    main()
