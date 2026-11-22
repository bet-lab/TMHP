"""Part-load figure: is the modelled COP-against-load trend the right shape?

Two curves, because two different questions get confused with each other.

**Panel (a) -- fixed temperatures.** Hold the source and sink where they are and
vary the load over the machine's whole speed range: from the compressor's speed
floor up to its top speed. The horizontal axis is the delivered heat as a share
of what the *same machine delivers at the same temperatures at its top speed*
(``Q_max [W]``, the ``capacity_clamped == "max"`` ceiling), not of the 9 kW
nameplate. The nameplate is a label for one rating point; at these conditions it
sits well below the top-speed capacity (the table in ``part-load.rst``), and a
curve cut off at the nameplate shows only the left half of the shape. Each curve
carries a marker where the nameplate duty falls.

COP rises as load is taken off the top speed: the heat exchangers grow
relative to the duty while the lift shrinks. The compressor maps take back part
of that at low speed, not all of it, so with the shipped maps each curve rises
at every step down to the speed floor (``part-load.rst`` gives the numbers).
Where the maps are evaluated outside their data (``efficiency_extrapolated``:
outside 30-110 rev/s or the measured pressure-ratio and evaporating-temperature
hull) the curve is dashed; with these conditions that is both ends of each curve.

Below that floor the curve ends and that is the honest answer rather than a
part-load result: the machine cannot follow a smaller request, so it runs at its
minimum capacity and every request below the floor resolves to the same
operating point -- same speed, same delivered heat, same condensing temperature,
same COP. Those requests therefore share one point on a delivered-heat axis;
the shaded band marks the heat the machine cannot deliver. (Two earlier artefacts
used to live there. The operating-point search once minimised absolute electrical
input and therefore starved the outdoor coil to its 5 % flow bound, pushing the
air-side drop to about 17 K against a catalogue maximum of 7.7 K across the 1,414
coils inverted in ``validation/extraction/``; and the tank-side condenser was
closed on the *requested* duty, so the condensing temperature kept falling as the
request did. Both are gone -- see ``tmhp._opt_utils`` and
``AirSourceHeatPumpBoiler._calc_state``.)

A real machine cycles below its modulation floor. This figure shows the
running machine (the model is built with ``Cd_cycling=None``), so these rows
are a continuous-operation figure at minimum capacity; by default
``analyze_steady`` reports the mean over the cycling period instead, with the
EN 14825 degradation (``tmhp.cycling``).

**Panel (b) -- the certification trajectory.** EN 14825 lowers the flow
temperature as it lowers the load, so its four test points are a different curve
from panel (a): both the duty and the lift fall together. Heat Pump Keymark
publishes the measured result for thousands of machines, and the modelled
trajectory has to sit inside that band and rise the same way. The band here is
the p10-p90 of 9,062 certified air-to-water records, read straight from the
certificates by ``validation.extraction.keymark_declared``.

Panel (b) is the one that decides whether the trend is right. Panel (a) shows
what the model does with a question the certification data never asks.

Both panels name the *leaving* water. The model's sink is a plate condenser
with circulating water, so each point is solved with the water entering it
at a fixed flow, the nameplate duty at the 5 K rise of the EN 14511 30/35 °C
rating, the rule of the EN 14825 study
(``validation.analysis.en14825_trend.solve_at_leaving_water``; what of it is
not verified against the standard is listed there).

The plotted values are written to ``validation/data/part_load_figure/`` with a
provenance record (``validation.study_provenance``), and
``docs/source/validation/part-load.rst`` quotes them;
``tests/test_part_load_page.py`` checks the page against those CSVs and the CSVs
against a rerun of this script's functions.
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

import dartwork_mpl as dm
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "visualization"))
from _dmpl_common import (  # noqa: E402
    COLORS,
    EXTRAPOLATED_LINESTYLE,
    GRIDLINE,
    HAIRLINE,
    apply_style,
    finalize,
    plot_extrapolation_split,
    static_path,
    ticks,
)

from tmhp import AirSourceHeatPumpBoiler, OperatingLimitWarning  # noqa: E402
from tmhp.constants import c_w  # noqa: E402

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from validation.analysis.en14825_trend import en14825_water_flow, solve_at_leaving_water  # noqa: E402

KEYMARK_SUMMARY = REPO_ROOT / "validation" / "data" / "keymark_en14825_summary.csv"
DATA_DIR = REPO_ROOT / "validation" / "data" / "part_load_figure"
SWEEP_CSV = DATA_DIR / "sweep.csv"
TRAJECTORY_CSV = DATA_DIR / "trajectory.csv"
ENTERING_WATER_CSV = DATA_DIR / "entering_water.csv"
PROVENANCE = REPO_ROOT / "validation" / "data" / "provenance" / "part_load_figure.json"

#: Fixed-temperature sweep: outdoor air and leaving water held, load varied.
SWEEP_CONDITIONS = (
    (7.0, 35.0, COLORS["accent"]),
    (7.0, 45.0, COLORS["warm"]),
    (-7.0, 45.0, COLORS["cool"]),
)
#: The same sweep with the *entering* water held instead of the leaving water:
#: outdoor air and the water entering the condenser [°C], at the same water
#: flow. 30 °C is the entering water of the EN 14511 30/35 °C rating, so at the
#: nameplate duty this is the 7/35 °C point of the sweep above. Written to
#: ``entering_water.csv`` and quoted by the page; not drawn.
ENTERING_WATER_CONDITION = (7.0, 30.0)
#: Requested heat as a share of the maximum capacity at the same temperatures,
#: top speed (1.00) down to below the speed floor (0.08; the floor is 13-15 % of
#: the maximum at these conditions) in steps of 2 %. The nameplate duty is added
#: as one more request per condition (:func:`sweep_fixed_temperatures`).
LOAD_FRACTIONS_OF_MAX = tuple(round(1.0 - 0.02 * i, 2) for i in range(47))

#: EN 14825 average climate, low-temperature application: outdoor air, leaving
#: water and part load of the design heating demand at each test point.
EN14825_LOW = (
    ("A", -7.0, 34.0, 0.88),
    ("B", 2.0, 30.0, 0.54),
    ("C", 7.0, 27.0, 0.35),
    ("D", 12.0, 24.0, 0.15),
)

NOMINAL_W = 9000.0
#: Design heating demand for the trajectory. The EN 14825 part loads are
#: fractions of the design demand, not of the machine's full capacity, so a
#: machine is normally oversized against it.
DESIGN_LOAD_W = 6000.0
#: Water flow through the condenser at every point [kg/s]: the nameplate duty
#: at the 5 K rise of the EN 14511 low-temperature rating (30/35 °C).
WATER_FLOW_KG_S = en14825_water_flow(NOMINAL_W, "low")

#: The model this figure asks its question of, chosen explicitly so the figure
#: does not move with a library default: the running machine (below the speed
#: floor ``analyze_steady`` would otherwise report the mean over the on/off
#: cycling period, ``tmhp.cycling``), with frost and defrost on, as shipped.
MODEL_SETTINGS = {"hp_capacity": NOMINAL_W, "ref": "R32", "Cd_cycling": None, "defrost": True}

#: A request this far above the nameplate is above what the machine delivers at
#: any of the sweep conditions, so the model returns its top-speed capacity
#: (``capacity_clamped == "max"``, ``Q_max [W]``). The same request every time,
#: because the water-entering iteration makes ``Q_max [W]`` differ by under a
#: watt between two different over-requests.
MAX_PROBE_W = 3.0 * NOMINAL_W


def _model() -> AirSourceHeatPumpBoiler:
    return AirSourceHeatPumpBoiler(**MODEL_SETTINGS)


def maximum_capacity(model: AirSourceHeatPumpBoiler, t_outdoor: float, lwt: float) -> float:
    """Heat the model delivers at its top speed at these temperatures [W] (``Q_max [W]``)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", OperatingLimitWarning)  # the clamp is the point of the request
        result, _ = solve_at_leaving_water(model, t_outdoor, lwt, MAX_PROBE_W, WATER_FLOW_KG_S)
    if result.get("failure_reason", "none") != "none" or result.get("capacity_clamped") != "max":
        raise RuntimeError(f"{t_outdoor}/{lwt} °C: a {MAX_PROBE_W:.0f} W request did not reach the top speed: {result}")
    return float(result["Q_max [W]"])


def _sweep_row(
    model: AirSourceHeatPumpBoiler, t_outdoor: float, lwt: float, q_request: float, q_max: float, *, nameplate: bool
) -> dict:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", OperatingLimitWarning)
        result, t_entering = solve_at_leaving_water(model, t_outdoor, lwt, q_request, WATER_FLOW_KG_S)
    failure = result.get("failure_reason", "none")
    rps = float(result.get("cmp_rpm [rpm]") or float("nan")) / 60.0
    q_delivered = float(result["Q_ref_tank [W]"]) if failure == "none" else np.nan
    return {
        "t_outdoor_C": t_outdoor,
        "lwt_C": lwt,
        "t_water_in_C": t_entering,
        "plr": q_request / NOMINAL_W,
        "q_request_W": q_request,
        "q_max_W": q_max,
        "plr_max": q_request / q_max,
        "delivered_fraction_of_max": q_delivered / q_max,
        "is_nameplate": nameplate,
        "q_delivered_W": q_delivered,
        "cop": float(result["cop_sys [-]"]) if failure == "none" else np.nan,
        "rps": rps,
        "at_floor": failure == "none" and abs(rps - model.rps_min) < 1e-6,
        "capacity_clamped": result.get("capacity_clamped") or "none",
        "efficiency_extrapolated": failure == "none" and bool(result.get("efficiency_extrapolated", False)),
        "failure_reason": failure,
    }


def sweep_fixed_temperatures(
    conditions: tuple[tuple[float, float], ...] | None = None,
    fractions: tuple[float, ...] | None = None,
) -> list[dict]:
    """Panel (a): every requested point, including any that did not converge (``cop`` NaN).

    ``fractions`` are shares of the maximum capacity at the condition
    (:func:`maximum_capacity`); the nameplate duty is added as one more request
    per condition (``is_nameplate``). ``plr`` stays the request over the
    nameplate, so it runs above 1 here; ``plr_max`` is the request over the
    maximum and ``delivered_fraction_of_max`` the heat actually delivered over
    it -- the two differ only below the speed floor, where the machine delivers
    more than it is asked.
    """
    model = _model()
    conditions = conditions or tuple((t, lwt) for t, lwt, _ in SWEEP_CONDITIONS)
    fractions = fractions or LOAD_FRACTIONS_OF_MAX
    rows = []
    for t_outdoor, lwt in conditions:
        q_max = maximum_capacity(model, t_outdoor, lwt)
        for fraction in fractions:
            rows.append(_sweep_row(model, t_outdoor, lwt, q_max * float(fraction), q_max, nameplate=False))
        rows.append(_sweep_row(model, t_outdoor, lwt, NOMINAL_W, q_max, nameplate=True))
    return rows


def sweep_entering_water(fractions: tuple[float, ...] | None = None) -> list[dict]:
    """The fixed-temperature sweep with the entering water held (:data:`ENTERING_WATER_CONDITION`).

    The model's own boundary: ``analyze_steady`` takes the water entering the
    condenser, so no iteration is needed. The leaving water then follows the
    delivered heat, and the rise across the condenser shrinks with the load --
    which the leaving-water sweep of panel (a) does not let it do. Columns as
    :func:`sweep_fixed_temperatures`, with ``lwt_C`` the resulting leaving water.
    """
    model = _model()
    t_outdoor, t_entering = ENTERING_WATER_CONDITION

    def solve(q_request: float) -> dict:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", OperatingLimitWarning)
            result = model.analyze_steady(
                T_tank_w=t_entering, T0=t_outdoor, Q_ref_tank=q_request, m_dot_w=WATER_FLOW_KG_S, return_dict=True
            )
        assert isinstance(result, dict)
        return result

    top = solve(MAX_PROBE_W)
    if top.get("failure_reason", "none") != "none" or top.get("capacity_clamped") != "max":
        raise RuntimeError(f"entering water {t_entering} °C: a {MAX_PROBE_W:.0f} W request did not reach the top speed")
    q_max = float(top["Q_max [W]"])
    rows = []
    requests = [(q_max * float(f), False) for f in (fractions or LOAD_FRACTIONS_OF_MAX)] + [(NOMINAL_W, True)]
    for q_request, nameplate in requests:
        result = solve(q_request)
        failure = result.get("failure_reason", "none")
        rps = float(result.get("cmp_rpm [rpm]") or float("nan")) / 60.0
        q_delivered = float(result["Q_ref_tank [W]"]) if failure == "none" else np.nan
        rows.append(
            {
                "t_outdoor_C": t_outdoor,
                "lwt_C": t_entering + q_delivered / (WATER_FLOW_KG_S * c_w),
                "t_water_in_C": t_entering,
                "plr": q_request / NOMINAL_W,
                "q_request_W": q_request,
                "q_max_W": q_max,
                "plr_max": q_request / q_max,
                "delivered_fraction_of_max": q_delivered / q_max,
                "is_nameplate": nameplate,
                "q_delivered_W": q_delivered,
                "cop": float(result["cop_sys [-]"]) if failure == "none" else np.nan,
                "rps": rps,
                "at_floor": failure == "none" and abs(rps - model.rps_min) < 1e-6,
                "capacity_clamped": result.get("capacity_clamped") or "none",
                "efficiency_extrapolated": failure == "none" and bool(result.get("efficiency_extrapolated", False)),
                "failure_reason": failure,
            }
        )
    return rows


def trajectory_en14825() -> list[dict]:
    """Panel (b): the four EN 14825 test points."""
    model = _model()
    rows = []
    for label, t_outdoor, lwt, load_ratio in EN14825_LOW:
        result, t_entering = solve_at_leaving_water(model, t_outdoor, lwt, DESIGN_LOAD_W * load_ratio, WATER_FLOW_KG_S)
        rows.append(
            {
                "point": label,
                "plr": load_ratio,
                "t_outdoor_C": t_outdoor,
                "lwt_C": lwt,
                "t_water_in_C": t_entering,
                "q_required_W": DESIGN_LOAD_W * load_ratio,
                "q_delivered_W": float(result["Q_ref_tank [W]"]),
                "clamped": result.get("capacity_clamped") is not None,
                "rps": float(result.get("cmp_rpm [rpm]") or float("nan")) / 60.0,
                "cop": float(result["cop_sys [-]"]) if result.get("failure_reason") == "none" else np.nan,
                "failure_reason": result.get("failure_reason", "none"),
            }
        )
    return rows


def write_data(sweep: pd.DataFrame, trajectory: pd.DataFrame, entering_water: pd.DataFrame) -> None:
    """The plotted values and their provenance record."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from validation import study_provenance

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    sweep.to_csv(SWEEP_CSV, index=False)
    trajectory.to_csv(TRAJECTORY_CSV, index=False)
    entering_water.to_csv(ENTERING_WATER_CSV, index=False)
    study_provenance.write(
        PROVENANCE,
        "scripts/validation/part_load_figure.py",
        [SWEEP_CSV, TRAJECTORY_CSV, ENTERING_WATER_CSV, static_path("validation_part_load.svg")],
        {
            "model": MODEL_SETTINGS,
            "water": "leaving water named; entering water at a fixed flow (EN 14825 study rule)",
            "water_flow_kg_s": WATER_FLOW_KG_S,
            "sweep_conditions": [(t, lwt) for t, lwt, _ in SWEEP_CONDITIONS],
            "entering_water_condition": ENTERING_WATER_CONDITION,
            "load_fractions_of_max": list(LOAD_FRACTIONS_OF_MAX),
            "max_probe_W": MAX_PROBE_W,
            "en14825_points": EN14825_LOW,
            "design_load_W": DESIGN_LOAD_W,
        },
        "air-to-water part load of the running machine: fixed-temperature sweep and EN 14825 trajectory "
        "(docs/source/validation/part-load.rst)",
        command="python -m scripts.validation.part_load_figure",
    )


def main() -> None:
    apply_style("report", hashsalt="tmhp.validation.part-load")
    sweep = pd.DataFrame(sweep_fixed_temperatures())
    trajectory = pd.DataFrame(trajectory_en14825())
    write_data(sweep, trajectory, pd.DataFrame(sweep_entering_water()))
    sweep = sweep[sweep.failure_reason == "none"]

    fig, axes = plt.subplots(1, 2, figsize=dm.figsize("17cm", 0.46))
    _panels: list = []

    # --- (a) fixed temperatures -------------------------------------------
    ax = axes[0]
    x = "delivered_fraction_of_max"
    for (t_outdoor, lwt, color), (_, group) in zip(
        SWEEP_CONDITIONS, sweep.groupby(["t_outdoor_C", "lwt_C"], sort=False), strict=False
    ):
        group = group.sort_values(x)
        plot_extrapolation_split(
            ax,
            group[x],
            group.cop,
            group.efficiency_extrapolated,
            color=color,
            linewidth=dm.lw(1),
            label=f"air {t_outdoor:.0f} °C, water {lwt:.0f} °C",
        )
        floor = group[group.at_floor]
        if not floor.empty:
            # Every request below the floor resolves to this one point.
            edge = floor.loc[floor[x].idxmax()]
            ax.plot([edge[x]], [edge.cop], marker="v", markersize=dm.fs(-1), color=color, zorder=5)
        mark = group[group.is_nameplate]
        ax.plot(
            mark[x],
            mark.cop,
            marker="D",
            markersize=dm.fs(-1.5),
            linestyle="none",
            markerfacecolor=color,
            markeredgecolor="white",
            markeredgewidth=HAIRLINE,
            zorder=6,
        )
    ax.plot(
        [],
        [],
        marker="D",
        markersize=dm.fs(-1.5),
        linestyle="none",
        color=COLORS["ink"],
        label=f"{NOMINAL_W / 1000:.0f} kW nameplate",
    )
    if sweep.efficiency_extrapolated.any():
        ax.plot(
            [],
            [],
            color=COLORS["ink"],
            linewidth=dm.lw(1),
            linestyle=EXTRAPOLATED_LINESTYLE,
            label="outside the compressor-map data",
        )
    ax.axvspan(
        0.0,
        # Below the lowest of the curves' floors: heat none of them can deliver. Each curve's own floor is its
        # triangle; a band to the highest floor would shade heat the other curves do deliver.
        float(sweep[sweep.at_floor].groupby(["t_outdoor_C", "lwt_C"])[x].max().min()) if sweep.at_floor.any() else 0.0,
        color=COLORS["band20"],
        alpha=0.35,
        linewidth=0,
        edgecolor="none",
        label="below the speed floor",
    )
    ax.set_xlabel("Delivered heat, share of maximum capacity [-]")
    ax.set_ylabel("System COP [-]")
    ax.set_xlim(0.0, 1.05)
    ax.set_xticks(ticks(0.0, 1.0, 0.2))
    ax.set_ylim(0.0, 8.0)
    ax.set_yticks(ticks(0.0, 8.0, 2.0))
    ax.invert_xaxis()
    ax.grid(True, alpha=0.25, linewidth=GRIDLINE)
    ax.legend(loc="upper left", frameon=False, fontsize=dm.fs(-2))
    ax.set_title("Load falls, temperatures held", loc="left", fontsize=dm.fs(0))
    _panels.append((ax, "a"))

    # --- (b) EN 14825 trajectory against the certified band ---------------
    ax = axes[1]
    if KEYMARK_SUMMARY.exists():
        band = pd.read_csv(KEYMARK_SUMMARY)
        band = band[band.application == "low"].sort_values("plr")
        ax.fill_between(
            band.plr,
            band.cop_p10,
            band.cop_p90,
            color=COLORS["band20"],
            alpha=0.5,
            linewidth=0,
            label=f"Keymark p10–p90 (n = {int(band.n.iloc[0]):,})",
        )
        ax.fill_between(
            band.plr,
            band.cop_p25,
            band.cop_p75,
            color=COLORS["band10"],
            alpha=0.6,
            linewidth=0,
            label="Keymark p25–p75",
        )
        ax.plot(
            band.plr, band.cop_median, color=COLORS["muted"], linestyle="--", linewidth=dm.lw(0), label="Keymark median"
        )
    good = trajectory.dropna(subset=["cop"])
    ax.plot(
        good.plr,
        good.cop,
        color=COLORS["accent"],
        linewidth=dm.lw(1),
        zorder=5,
        label="TMHP, defaults",
    )
    free = good[~good.clamped]
    held = good[good.clamped]
    ax.scatter(
        free.plr,
        free.cop,
        s=dm.fs(6),
        color=COLORS["accent"],
        zorder=6,
        edgecolor="white",
        linewidth=HAIRLINE,
    )
    if not held.empty:
        # Open marker where the compressor is at its floor and the machine is
        # therefore delivering more than the test point asks for. Just over half
        # of certified machines declare the same thing -- a higher heat output
        # at point D than at point C -- and the model reproduces it without
        # anything having been put in by hand to make it.
        ax.scatter(
            held.plr,
            held.cop,
            s=dm.fs(7),
            facecolor="white",
            zorder=6,
            edgecolor=COLORS["accent"],
            linewidth=dm.lw(1),
            label="delivers more than requested",
        )
    for _, row in good.iterrows():
        ax.annotate(
            f"{row['point']}  {row['t_outdoor_C']:.0f}/{row['lwt_C']:.0f} °C",
            (row.plr, row.cop),
            textcoords="offset points",
            xytext=(4, -10),
            fontsize=dm.fs(-2),
            color=COLORS["ink"],
        )
    ax.set_xlabel("Part load of design heating demand [-]")
    ax.set_ylabel("System COP [-]")
    ax.set_xlim(0.0, 1.0)
    ax.set_xticks(ticks(0.0, 1.0, 0.2))
    ax.set_ylim(0.0, 12.0)
    ax.set_yticks(ticks(0.0, 12.0, 3.0))
    ax.invert_xaxis()
    ax.grid(True, alpha=0.25, linewidth=GRIDLINE)
    ax.legend(loc="upper left", frameon=False, fontsize=dm.fs(-2))
    ax.set_title("Load and flow temperature fall together (EN 14825)", loc="left", fontsize=dm.fs(0))
    _panels.append((ax, "b"))

    out = static_path("validation_part_load.svg").with_suffix("")
    dm.label_axes([a for a, _ in _panels], labels=[l for _, l in _panels])
    finalize(fig, out, formats=("svg", "png"))
    plt.close(fig)

    print(f"wrote {out}.svg")
    print(trajectory.to_string(index=False))


if __name__ == "__main__":
    main()
