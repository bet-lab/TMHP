"""Part-load COP of the air-to-air model, pre-refit correlations against current.

The question is not which is right but *what changed*: the same machine, the
same boundary conditions and the same requested part loads, solved once with
the pre-refit compressor correlations and the pre-refit low-load objective, and
once with the shipped ones.

What "legacy" means here
------------------------
The air-to-air model as it stood before this work carried **no compressor
efficiency model at all** -- ``eta_cmp_isen`` / ``eta_cmp_vol`` / ``eta_cmp``
all defaulted to ``None``, which the cycle read as 1.0.  Its own snapshot is
therefore not a usable baseline (and its 20 K fixed minimum-lift guard plus
10 rev/s speed floor leave most of this envelope infeasible: 236 of 247 points
fail to converge).  "Legacy" below is the v1 correlation set archived verbatim
in ``validation/coefficients/v1-legacy/legacy_forms.py`` -- the one the boiler
model shipped with and the one the report's ablation calls ``legacy-v1`` --
evaluated inside the current solver.

Two things changed together, so both are varied and then separated:

``legacy``            v1 correlations + the pre-refit objective (minimise total
                      electrical input)
``current``           v2026-09-15b + the specific-energy objective (U1)
``legacy_new_obj``    v1 correlations + the current objective
``current_old_obj``   v2026-09-15b + the pre-refit objective

The figure draws the first two; the other two exist so the console summary can
say how much of the low-load gap is the coefficients and how much is the
objective.  Held identical across all four: nameplate capacity, refrigerant,
room temperature, outdoor grid, part-load grid, speed floor (15 rev/s), cycle
guards and every fan / heat-exchanger input.

Run::

    uv run python3 -m validation.fixed_boundary_plr.legacy_compare
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import math
import warnings
from collections.abc import Iterator
from pathlib import Path

import pandas as pd

from tmhp import AirSourceHeatPump
from tmhp.compressor_efficiency import COEFFICIENT_VERSION

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "validation" / "data" / "fixed_boundary_plr"
LEGACY_PATH = REPO_ROOT / "validation" / "coefficients" / "v1-legacy" / "legacy_forms.py"

CAPACITY_W = 3500.0
REF = "R32"
RPS_RATED = 60.0  # the air-to-air rating point both correlation sets read speed against
DUTIES = (
    ("heating", 21.0, (-15.0, -10.0, -5.0, 0.0, 5.0, 10.0, 15.0)),
    ("cooling", 24.0, (20.0, 25.0, 30.0, 35.0, 40.0, 45.0)),
)
#: Shared COP axis per duty, so the two columns of a row are read against the same scale.
COP_AXIS = {"heating": (0.0, 9.0, 1.5), "cooling": (0.0, 12.0, 2.0)}
PLR_GRID = tuple(round(1.0 - 0.05 * i, 2) for i in range(19))  # 1.00 .. 0.10


def _load_legacy():
    spec = importlib.util.spec_from_file_location("tmhp_legacy_v1_forms", LEGACY_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextlib.contextmanager
def pre_refit_objective() -> Iterator[None]:
    """Restore the objective the solver used before U1: raw total input.

    The current solver minimises electrical input per unit of *credited* heat
    among candidates that meet the request.  Before that it minimised
    ``E_tot`` alone, and at low load a starved outdoor coil wins that contest:
    it delivers less heat, draws less power, and looks better.
    """
    import tmhp.air_source_heat_pump as mod

    original = mod.specific_energy_objective

    def e_tot_only(E_tot: float, Q_delivered: float, Q_request: float, Q_storable: float = 0.0, **_) -> float:
        del Q_delivered, Q_request, Q_storable
        if E_tot <= 0.0 or math.isnan(E_tot):
            return mod.PENALTY
        return E_tot

    mod.specific_energy_objective = e_tot_only
    try:
        yield
    finally:
        mod.specific_energy_objective = original


def _kwargs(coefficients: str) -> dict:
    if coefficients == "current":
        return {}
    legacy = _load_legacy()
    return {
        "eta_cmp_isen": legacy.eta_isen_default,
        "eta_cmp_vol": legacy.eta_vol_default,
        "eta_cmp": legacy.make_eta_em(RPS_RATED),
    }


def sweep(label: str, coefficients: str, objective: str) -> pd.DataFrame:
    kwargs = _kwargs(coefficients)
    rows: list[dict] = []
    context = pre_refit_objective() if objective == "pre_refit" else contextlib.nullcontext()
    with context:
        for duty, t_room, outdoor in DUTIES:
            sign = -1.0 if duty == "heating" else 1.0
            for t_out in outdoor:
                model = AirSourceHeatPump(hp_capacity=CAPACITY_W, ref=REF, **kwargs)
                for f in PLR_GRID:
                    base = {
                        "variant": label,
                        "coefficients": coefficients if coefficients != "current" else COEFFICIENT_VERSION,
                        "objective": objective,
                        "duty": duty,
                        "t_room_C": t_room,
                        "t_outdoor_C": t_out,
                        "plr_request": f,
                        "rps_min_setting": float(model.rps_min),
                    }
                    r = model.analyze_steady(
                        Q_r_iu=sign * CAPACITY_W * f, T0=t_out, T_a_room=t_room, return_dict=True, verbose=False
                    )
                    assert isinstance(r, dict)
                    rps = float(r.get("cmp_rpm [rpm]", float("nan"))) / 60.0
                    rows.append(
                        {
                            **base,
                            "q_request_W": CAPACITY_W * f,
                            "q_delivered_W": abs(float(r.get("Q_ref_iu [W]", float("nan")))),
                            "cop": float(r.get("cop_sys [-]", float("nan"))),
                            "rps": rps,
                            "n_star": rps / RPS_RATED,
                            "at_floor": bool(rps <= float(model.rps_min) * 1.001),
                            "pr": float(r.get("pr_cmp [-]", float("nan"))),
                            "eta_vol": float(r.get("eta_cmp_vol [-]", float("nan"))),
                            "eta_isen": float(r.get("eta_cmp_isen [-]", float("nan"))),
                            "eta_em": float(r.get("eta_cmp [-]", float("nan"))),
                            "E_tot": float(r.get("E_tot [W]", float("nan"))),
                            "fan_fraction": float(r.get("dV_ou_a [m3/s]", float("nan"))) / model.dV_ou_fan_a_rated,
                            "air_dT_K": abs(
                                float(r.get("T_ou_a_in [°C]", float("nan")))
                                - float(r.get("T_ou_a_out [°C]", float("nan")))
                            ),
                            "capacity_clamped": r.get("capacity_clamped"),
                            "failure_reason": r.get("failure_reason", "none"),
                        }
                    )
    return pd.DataFrame(rows)


def figure(df: pd.DataFrame, out: Path) -> None:
    import dartwork_mpl as dm
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # noqa: E402
    from scripts.visualization._dmpl_common import (  # noqa: E402
        COLORS,
        GRIDLINE,
        HAIRLINE,
        apply_style,
        finalize,
        panel_letter,
        ticks,
    )

    apply_style("scientific")
    fig, axes = plt.subplots(2, 2, figsize=dm.figsize("17cm", 0.80), gridspec_kw={"wspace": 0.34, "hspace": 0.62})
    columns = (
        ("legacy", "pre-refit v1 correlations + pre-refit objective"),
        ("current", f"{COEFFICIENT_VERSION} + specific-energy objective"),
    )
    letters = (("a", "b"), ("c", "d"))
    cmap = plt.get_cmap("viridis")
    for i, (duty, t_room, outdoor) in enumerate(DUTIES):
        levels = {t: cmap(0.08 + 0.84 * k / (len(outdoor) - 1)) for k, t in enumerate(outdoor)}
        for j, (variant, subtitle) in enumerate(columns):
            ax = axes[i][j]
            d = df[(df.duty == duty) & (df.variant == variant)]
            for t_out in outdoor:
                g = d[(d.t_outdoor_C == t_out) & d.cop.notna()].sort_values("plr_request")
                if g.empty:
                    continue
                ax.plot(g.plr_request, g.cop, color=levels[t_out], lw=dm.lw(0), zorder=3)
                free, held = g[~g.at_floor], g[g.at_floor]
                ax.scatter(free.plr_request, free.cop, s=dm.fs(1.2), color=levels[t_out], edgecolors="none", zorder=4)
                ax.scatter(
                    held.plr_request,
                    held.cop,
                    s=dm.fs(2.0),
                    facecolors="none",
                    edgecolors=levels[t_out],
                    linewidth=HAIRLINE,
                    zorder=5,
                )
            ax.set_xlim(0.0, 1.05)
            ax.set_xticks(ticks(0.0, 1.0, 0.25))
            ax.set_xlabel("Requested part-load ratio [-]")
            lo, hi, step = COP_AXIS[duty]
            ax.set_ylim(lo, hi)
            ax.set_yticks(ticks(lo, hi, step))
            ax.set_ylabel("System COP [-]")
            ax.set_title(
                f"{duty.capitalize()}, room {t_room:g} °C\n{subtitle}",
                loc="left",
                fontsize=dm.fs(-2.5),
                linespacing=1.4,
            )
            ax.grid(True, alpha=0.25, linewidth=GRIDLINE)
            panel_letter(ax, letters[i][j], x=-0.17, y=1.20)
        handles = [plt.Line2D([], [], color=levels[t], lw=dm.lw(0), label=f"{t:g}") for t in outdoor]
        axes[i][1].legend(
            handles=handles,
            loc="lower left",
            frameon=False,
            fontsize=dm.fs(-3.5),
            ncol=2,
            labelspacing=0.2,
            columnspacing=0.8,
            handletextpad=0.4,
            title="outdoor air [°C]",
            title_fontsize=dm.fs(-3.5),
        )
    marks = [
        plt.Line2D([], [], color=COLORS["ink"], marker="o", ls="none", ms=2.6, label="speed is a free variable"),
        plt.Line2D(
            [],
            [],
            color=COLORS["ink"],
            marker="o",
            ls="none",
            ms=3.2,
            markerfacecolor="none",
            markeredgewidth=HAIRLINE,
            label="speed floor 15 rev/s, supply > request",
        ),
    ]
    axes[0][0].legend(handles=marks, loc="lower left", frameon=False, fontsize=dm.fs(-3.5), labelspacing=0.25)
    out.mkdir(parents=True, exist_ok=True)
    finalize(fig, out / "G_legacy_vs_current_plr_cop", formats=("svg", "png"), mt="4%")
    plt.close(fig)


def _at(g: pd.DataFrame, plr: float) -> float:
    row = g[g.plr_request == plr]
    return float(row.cop.iloc[0]) if not row.empty else float("nan")


def summary(df: pd.DataFrame) -> None:
    for duty, t_out in (("heating", -5.0), ("cooling", 35.0)):
        print(f"\n{duty}, outdoor {t_out:g} °C, room {dict((d[0], d[1]) for d in DUTIES)[duty]:g} °C")
        print(
            f"  {'variant':18s} {'COP@1.00':>8s} {'COP@0.50':>8s} {'floor PLR':>9s} {'COP@floor':>9s} "
            f"{'fan min %':>9s} {'air dT max':>10s}"
        )
        for variant in ("legacy", "legacy_new_obj", "current_old_obj", "current"):
            g = df[(df.duty == duty) & (df.variant == variant) & (df.t_outdoor_C == t_out) & df.cop.notna()]
            if g.empty:
                print(f"  {variant:18s} no converged rows")
                continue
            free = g[~g.at_floor]
            entry = float(free.plr_request.min()) if not free.empty else float("nan")
            cop_entry = float(free.loc[free.plr_request.idxmin(), "cop"]) if not free.empty else float("nan")
            print(
                f"  {variant:18s} {_at(g, 1.00):8.2f} {_at(g, 0.50):8.2f} {entry:9.2f} {cop_entry:9.2f} "
                f"{g.fan_fraction.min() * 100:9.1f} {g.air_dT_K.max():10.1f}"
            )
        lo = df[(df.duty == duty) & (df.t_outdoor_C == t_out) & (df.plr_request == 0.10) & df.cop.notna()]
        book = {r.variant: r.cop for r in lo.itertuples()}
        if {"legacy", "legacy_new_obj", "current"} <= book.keys():
            obj = book["legacy_new_obj"] - book["legacy"]
            coef = book["current"] - book["legacy_new_obj"]
            print(
                f"  PLR 0.10 decomposition: objective {obj:+.2f}, coefficients {coef:+.2f}, "
                f"total {book['current'] - book['legacy']:+.2f} COP"
            )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(REPO_ROOT / "validation" / "coefficients"))
    ap.add_argument("--replot", action="store_true", help="redraw from the stored sweep instead of re-running it")
    a = ap.parse_args()
    warnings.filterwarnings("ignore")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if a.replot:
        df = pd.read_csv(OUT_DIR / "legacy_compare.csv")
        figure(df, Path(a.out) / COEFFICIENT_VERSION / "figures")
        summary(df)
        return

    frames = [
        sweep("legacy", "v1-legacy", "pre_refit"),
        sweep("legacy_new_obj", "v1-legacy", "specific_energy"),
        sweep("current_old_obj", "current", "pre_refit"),
        sweep("current", "current", "specific_energy"),
    ]
    df = pd.concat(frames, ignore_index=True)
    df.to_csv(OUT_DIR / "legacy_compare.csv", index=False)
    figure(df, Path(a.out) / COEFFICIENT_VERSION / "figures")
    summary(df)
    print(f"\n-> {OUT_DIR / 'legacy_compare.csv'}")


if __name__ == "__main__":
    main()
