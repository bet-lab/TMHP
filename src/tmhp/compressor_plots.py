"""Fixed-form figures of the compressor efficiency maps and of a calibration's performance (#256).

Three figures, always drawn the same way so that results recorded in
different issues can be compared at a glance:

:func:`plot_compressor_map`
    One efficiency over (pressure ratio, speed) at a few fixed evaporating
    temperatures: row 1 *before*, row 2 *after*, one column per ``T_evap``.
    Axis ranges and colour levels are fixed per quantity. Every heat-pump
    model also has it as a method (:class:`CompressorMapPlotMixin`), with its
    own efficiencies as *after* and the default maps as *before*.
:func:`plot_calibration_stages`
    COP error after each calibration stage: RMSE of ``ln(COP_model /
    COP_catalogue)`` on the left, MAPE on the right; a line for the fit to all
    points and markers for the cross-validated value (filled = adopted).
:func:`plot_cop_parity`
    Model against catalogue COP, *before* on the left and *after* on the right,
    on the same axes, with the error scores in the panel titles.

Each function returns a :class:`matplotlib.figure.Figure` drawn in the
dartwork-mpl ``scientific`` preset (:data:`FIGURE_STYLE`) at the width of two
journal columns (17 cm), whatever style the caller has set; the caller's
global ``rcParams`` are left as they were (#258). Every panel carries its own
tick labels. Colours: grey = before (default maps), violet = after
(calibrated), orange = the data range of the default maps, blue = efficiency
magnitude.
"""

from __future__ import annotations

import functools
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ParamSpec, Protocol, TypeVar, cast

import numpy as np

from .compressor_efficiency import (
    DOMAIN,
    QUANTITIES,
    InvalidCompressorEfficiency,
    SuctionState,
    _eval_eff,
    default_efficiencies,
)

if TYPE_CHECKING:
    import pandas as pd
    from matplotlib.axes import Axes
    from matplotlib.figure import Figure

__all__ = [
    "DEFAULT_T_EVAP_C",
    "FIGURE_STYLE",
    "MAP_QUANTITIES",
    "CompressorMapPlotMixin",
    "CopErrors",
    "OperatingPoints",
    "StageScore",
    "cop_errors",
    "efficiency_grid",
    "plot_calibration_stages",
    "plot_compressor_map",
    "plot_cop_parity",
]

#: Evaporating temperatures [°C] of the map columns: cold heating, mild heating, cooling.
DEFAULT_T_EVAP_C: tuple[float, ...] = (-15.0, 0.0, 10.0)
#: Default axis ranges of the map panels: pressure ratio (log axis) and speed [rev/s].
DEFAULT_PR_RANGE: tuple[float, float] = (1.4, 13.0)
DEFAULT_RPS_RANGE: tuple[float, float] = (15.0, 120.0)

#: The dartwork-mpl preset every figure of this module is drawn in.
FIGURE_STYLE = "scientific"
#: Figure widths and heights (dartwork-mpl lengths): two journal columns wide.
_WIDTH = "17cm"
_MAP_ROW_HEIGHT_CM = 6.0
_STAGES_HEIGHT = "7.5cm"
_PARITY_HEIGHT = "9cm"

GREY = "oc.gray6"
VIOLET = "tw.violet600"
ORANGE = "tw.orange500"
INK = "oc.gray8"
FAINT = "oc.gray5"


@dataclass(frozen=True)
class _MapQuantity:
    """How one plotted efficiency is named and coloured (fixed levels keep figures comparable)."""

    label: str
    symbol: str
    levels: tuple[float, ...]


def _levels(lo: float, hi: float, step: float) -> tuple[float, ...]:
    """Evenly spaced contour levels from ``lo`` to ``hi`` inclusive."""
    n = round((hi - lo) / step)
    return tuple(round(lo + i * step, 6) for i in range(n + 1))


#: Quantities :func:`plot_compressor_map` can draw. ``eta_ov`` is the product
#: ``eta_cmp_isen * eta_cmp`` (the overall efficiency the calibration scales);
#: the others are the package's three efficiencies as the cycle evaluates them.
MAP_QUANTITIES: dict[str, _MapQuantity] = {
    "eta_ov": _MapQuantity("Overall efficiency", "η_ov", _levels(0.30, 0.85, 0.025)),
    "eta_cmp_isen": _MapQuantity("Isentropic efficiency", "η_is", _levels(0.40, 1.00, 0.025)),
    "eta_cmp_vol": _MapQuantity("Volumetric efficiency", "η_v", _levels(0.70, 1.00, 0.02)),
    "eta_cmp": _MapQuantity("Electromechanical efficiency", "η_em", _levels(0.70, 1.00, 0.02)),
}


class _HasEfficiencies(Protocol):
    eta_cmp_isen: float | Callable
    eta_cmp_vol: float | Callable
    eta_cmp: float | Callable


EfficiencySource = Mapping[str, float | Callable] | _HasEfficiencies


def _efficiency_set(source: EfficiencySource | None) -> Mapping[str, float | Callable]:
    """The three efficiencies of ``source``: a mapping keyed by :data:`QUANTITIES`, or a model with those attributes.

    ``None`` gives the default maps. Raises ``TypeError`` for anything that has
    not all three.
    """
    if source is None:
        return default_efficiencies()
    if isinstance(source, Mapping):
        missing = [q for q in QUANTITIES if q not in source]
        if missing:
            raise TypeError(f"efficiency mapping lacks {missing}; it needs the keys {QUANTITIES}")
        return {q: source[q] for q in QUANTITIES}
    try:
        return {q: getattr(source, q) for q in QUANTITIES}
    except AttributeError as exc:
        raise TypeError(f"{source!r} is neither a mapping of {QUANTITIES} nor a model with those attributes") from exc


def _efficiency_value(
    eff: Mapping[str, float | Callable], quantity: str, pr: float, rps: float, state: SuctionState
) -> float:
    """``quantity`` at one point as the cycle evaluates it (above one set to one); NaN where it is infeasible."""
    try:
        if quantity == "eta_ov":
            return _eval_eff(eff["eta_cmp_isen"], pr, rps, state) * _eval_eff(eff["eta_cmp"], pr, rps, state)
        return _eval_eff(eff[quantity], pr, rps, state)
    except InvalidCompressorEfficiency:
        return math.nan


def efficiency_grid(
    efficiencies: EfficiencySource | None,
    quantity: str,
    pressure_ratio: Sequence[float] | np.ndarray,
    rps: Sequence[float] | np.ndarray,
    T_evap_C: float,
) -> np.ndarray:
    """``quantity`` on the grid ``rps`` x ``pressure_ratio`` at one evaporating temperature.

    Returns an array of shape ``(len(rps), len(pressure_ratio))``. Each value is
    what the cycle would use (:func:`tmhp.compressor_efficiency._eval_eff`: a
    value above one is set to one); an infeasible point (nonpositive or
    nonfinite efficiency) is NaN. ``efficiencies`` is as for
    :func:`plot_compressor_map`. Raises ``ValueError`` for an unknown quantity.
    """
    if quantity not in MAP_QUANTITIES:
        raise ValueError(f"quantity must be one of {tuple(MAP_QUANTITIES)}, got {quantity!r}")
    eff = _efficiency_set(efficiencies)
    state = SuctionState(T_evap_C + 273.15)
    prs = np.asarray(pressure_ratio, dtype=float)
    ns = np.asarray(rps, dtype=float)
    out = np.empty((ns.size, prs.size))
    for j, n in enumerate(ns):
        for i, pr in enumerate(prs):
            out[j, i] = _efficiency_value(eff, quantity, float(pr), float(n), state)
    return out


@dataclass(frozen=True, eq=False)
class OperatingPoints:
    """Compressor operating points to overlay on the map: pressure ratio, speed [rev/s], T_evap [°C].

    Equal-length 1-D arrays; points with a non-finite value are dropped.
    Build one from model results with :meth:`from_results`.
    """

    pressure_ratio: np.ndarray
    rps: np.ndarray
    T_evap_C: np.ndarray

    def __post_init__(self) -> None:
        arrays = [np.asarray(a, dtype=float).ravel() for a in (self.pressure_ratio, self.rps, self.T_evap_C)]
        if len({a.size for a in arrays}) != 1:
            raise ValueError("pressure_ratio, rps and T_evap_C must have the same length")
        keep = np.all([np.isfinite(a) for a in arrays], axis=0) & (arrays[0] > 0.0)
        for name, a in zip(("pressure_ratio", "rps", "T_evap_C"), arrays, strict=True):
            object.__setattr__(self, name, a[keep])

    @classmethod
    def from_results(cls, results: pd.DataFrame | Mapping[str, Any]) -> OperatingPoints:
        """Points from model result rows (a DataFrame or a mapping of columns).

        Uses ``pr_cmp [-]`` when present, else ``P_ref_cmp_out [Pa] /
        P_ref_cmp_in [Pa]``; ``cmp_rpm [rpm]`` / 60; ``T_ref_evap_sat [°C]``.
        Raises ``KeyError`` naming a missing column.
        """
        if "pr_cmp [-]" in results:
            pr = np.asarray(results["pr_cmp [-]"], dtype=float)
        else:
            pr = np.asarray(results["P_ref_cmp_out [Pa]"], dtype=float) / np.asarray(
                results["P_ref_cmp_in [Pa]"], dtype=float
            )
        rps = np.asarray(results["cmp_rpm [rpm]"], dtype=float) / 60.0
        return cls(pr, rps, np.asarray(results["T_ref_evap_sat [°C]"], dtype=float))


_P = ParamSpec("_P")
_R = TypeVar("_R")


def _plt():
    import matplotlib.pyplot as plt

    return plt


def _dm():
    import dartwork_mpl

    return dartwork_mpl


def _pin_tick_style(fig: Figure) -> None:
    """Write the active preset's tick settings onto every axis of ``fig``.

    Matplotlib reads some tick settings from the global ``rcParams`` when the
    figure is drawn, not when it is built: ticks it adds at draw time, and the
    tick-label size that sets how many ticks fit. Pinning them makes a figure
    built inside :data:`FIGURE_STYLE` draw the same when it is saved or shown
    after the style is gone.
    """
    plt = _plt()
    rc = plt.rcParams
    for ax in fig.axes:
        for axis in ("x", "y"):
            colour = rc[f"{axis}tick.color"]
            label_colour = rc[f"{axis}tick.labelcolor"]
            for which in ("major", "minor"):
                ax.tick_params(
                    axis=axis,
                    which=which,
                    direction=rc[f"{axis}tick.direction"],
                    length=rc[f"{axis}tick.{which}.size"],
                    width=rc[f"{axis}tick.{which}.width"],
                    pad=rc[f"{axis}tick.{which}.pad"],
                    labelsize=rc[f"{axis}tick.labelsize"],
                    color=colour,
                    labelcolor=colour if label_colour == "inherit" else label_colour,
                    grid_color=rc["grid.color"],
                    grid_linewidth=rc["grid.linewidth"],
                    grid_linestyle=rc["grid.linestyle"],
                    grid_alpha=rc["grid.alpha"],
                )


def _finish(fig: Figure) -> None:
    """Pin the tick style, then fit the layout (dartwork-mpl ``simple_layout``) with a small margin."""
    dm = _dm()
    _pin_tick_style(fig)
    dm.simple_layout(fig, margin=dm.mm(1.5))


def _in_figure_style(draw: Callable[_P, _R]) -> Callable[_P, _R]:
    """Run ``draw`` inside the :data:`FIGURE_STYLE` preset; the caller's rcParams come back afterwards."""

    @functools.wraps(draw)
    def styled(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        with _dm().style.context(FIGURE_STYLE):
            return draw(*args, **kwargs)

    return styled


def _peak(prs: np.ndarray, ns: np.ndarray, Z: np.ndarray) -> tuple[float, float, float] | None:
    """(value, pressure ratio, speed) of the largest finite grid value, or None if the grid has none."""
    if not np.isfinite(Z).any():
        return None
    j, i = np.unravel_index(np.nanargmax(Z), Z.shape)
    return float(Z[j, i]), float(prs[i]), float(ns[j])


@_in_figure_style
def plot_compressor_map(
    before: EfficiencySource | None,
    after: EfficiencySource | None,
    *,
    T_evap_C: Sequence[float] = DEFAULT_T_EVAP_C,
    quantity: str = "eta_ov",
    points: OperatingPoints | pd.DataFrame | None = None,
    labels: tuple[str, str] = ("Default maps", "Calibrated"),
    pr_range: tuple[float, float] = DEFAULT_PR_RANGE,
    rps_range: tuple[float, float] = DEFAULT_RPS_RANGE,
    title: str | None = None,
    resolution: tuple[int, int] = (140, 120),
) -> Figure:
    """Efficiency map before (row 1) and after (row 2), one column per evaporating temperature.

    ``before`` and ``after`` are each a mapping of the three efficiencies
    (``eta_cmp_isen``, ``eta_cmp_vol``, ``eta_cmp``), a heat-pump model (its
    attributes of those names), or ``None`` for the default maps. ``quantity``
    is a key of :data:`MAP_QUANTITIES`; its colour levels are fixed, values
    beyond them take the end colours. Axes: pressure ratio ``r_p`` (log,
    ``pr_range``) and speed ``N`` [rev/s] (``rps_range``), the same on every
    panel.

    Each row is a block of its own, headed by its label (``labels``) and set
    apart from the other row; no axis is shared, so every panel has its own
    tick labels. Drawn on every panel: the efficiency (filled contours,
    labelled lines), the data range of the default maps at that ``T_evap``
    (orange), and the grid peak (diamond). The panel's left title is its
    ``T_evap``, the right title the peak value and place. ``points`` (an
    :class:`OperatingPoints` or model result rows) are drawn on the column
    whose ``T_evap`` is nearest to each point's. ``resolution`` is the grid
    size (pressure ratio, speed).

    Returns the figure: 2 x ``len(T_evap_C)`` map axes and one colour bar for
    both rows. Raises ``ValueError`` for an unknown quantity or no
    temperatures, and ``TypeError`` for an efficiency source without the three
    efficiencies.
    """
    if quantity not in MAP_QUANTITIES:
        raise ValueError(f"quantity must be one of {tuple(MAP_QUANTITIES)}, got {quantity!r}")
    temps = [float(t) for t in T_evap_C]
    if not temps:
        raise ValueError("T_evap_C needs at least one temperature")
    spec = MAP_QUANTITIES[quantity]
    rows = (_efficiency_set(before), _efficiency_set(after))
    pts = points if points is None or isinstance(points, OperatingPoints) else OperatingPoints.from_results(points)
    prs = np.exp(np.linspace(math.log(pr_range[0]), math.log(pr_range[1]), resolution[0]))
    ns = np.linspace(rps_range[0], rps_range[1], resolution[1])
    column_of_point = (
        np.argmin(np.abs(pts.T_evap_C[:, None] - np.asarray(temps)[None, :]), axis=1) if pts is not None else None
    )
    ticks = [t for t in (2, 3, 4, 6, 8, 12) if pr_range[0] <= t <= pr_range[1]]
    levels = np.asarray(spec.levels)

    plt, dm = _plt(), _dm()
    from matplotlib.transforms import Bbox

    ncol = len(temps)
    fig = plt.figure(figsize=dm.figsize(_WIDTH, dm.cm(2 * _MAP_ROW_HEIGHT_CM + 1.6)))
    maps_box, bar_box = fig.subfigures(1, 2, width_ratios=[ncol, 0.32], wspace=0.0)
    row_boxes = maps_box.subfigures(2, 1, hspace=0.0)
    filled = None
    for r, (eff, row_box) in enumerate(zip(rows, row_boxes, strict=True)):
        row_box.suptitle(labels[r], x=0.0, ha="left", fontweight=dm.fw(2))
        row_axes = row_box.subplots(1, ncol, squeeze=False, gridspec_kw={"wspace": 0.3})[0]
        colour = GREY if r == 0 else VIOLET
        for c, (ax, T_e) in enumerate(zip(row_axes, temps, strict=True)):
            Z = efficiency_grid(eff, quantity, prs, ns, T_e)
            filled = ax.contourf(prs, ns, Z, levels=levels, cmap="Blues", extend="both")
            lines = ax.contour(prs, ns, Z, levels=levels[::2], colors="white", linewidths=0.5)
            for text in ax.clabel(lines, levels=levels[::4], fontsize=dm.fs(-1.5), fmt="%.2f"):
                fill = filled.cmap(filled.norm(float(text.get_text())))
                text.set_color(dm.readable_text_color(fill))
            inside = np.array([[DOMAIN.contains(pr, n, T_e) for pr in prs] for n in ns], dtype=float)
            if 0.0 < inside.mean() < 1.0:
                ax.contour(prs, ns, inside, levels=[0.5], colors=ORANGE, linewidths=1.1)
                ax.plot([], [], color=ORANGE, lw=1.1, label="Data range of the default maps")
            ax.set_title(f"T_evap {T_e:g} °C", loc="left")
            peak = _peak(prs, ns, Z)
            if peak is not None:
                ax.scatter(
                    [peak[1]],
                    [peak[2]],
                    marker="D",
                    s=22,
                    color=colour,
                    edgecolor="white",
                    lw=0.6,
                    zorder=5,
                    label=f"Peak, {labels[r]}",
                )
                ax.set_title(
                    f"peak {peak[0]:.3f}\nr_p {peak[1]:.2f}, N {peak[2]:.0f}",
                    loc="right",
                    fontsize=dm.fs(-1),
                    fontweight=dm.fw(0),
                )
            if pts is not None and column_of_point is not None:
                sel = column_of_point == c
                # labelled on the last row so the legend lists it after both peaks
                point_label = "Operating points (nearest T_evap)" if r == len(rows) - 1 else None
                ax.scatter(
                    pts.pressure_ratio[sel],
                    pts.rps[sel],
                    s=9,
                    facecolor="none",
                    edgecolor="black",
                    lw=0.6,
                    zorder=4,
                    gid="operating-points",
                    label=point_label,
                )
            ax.set_xscale("log")
            ax.set_xticks(ticks, [f"{t:g}" for t in ticks])
            ax.minorticks_off()
            ax.set_xlim(*pr_range)
            ax.set_ylim(*rps_range)
            ax.set_xlabel("Pressure ratio (r_p)")
        row_axes[0].set_ylabel("Speed (N) [rev/s]")
    cax = bar_box.add_subplot()
    bar = fig.colorbar(filled, cax=cax, label=f"{spec.label} ({spec.symbol})")
    bar.set_ticks(levels[::2])
    fig.suptitle(title or f"{spec.label} ({spec.symbol}): {labels[0]} (top) vs {labels[1]} (bottom)")
    dm.figure_legend(fig, loc="below", frameon=False, markerscale=1.4)
    _finish(fig)
    # the colour bar spans the map panels of both rows, not the row titles and the legend
    panels = Bbox.union([ax.get_window_extent() for ax in fig.axes if ax is not cax])
    y0, y1 = bar_box.transSubfigure.inverted().transform([(0.0, panels.y0), (0.0, panels.y1)])[:, 1]
    x0, _, width, _ = cax.get_position().bounds
    cax.set_position((x0, y0, width, y1 - y0))
    return cast("Figure", fig)


@dataclass(frozen=True)
class CopErrors:
    """COP error scores of a model against reference (catalogue) values, as fractions.

    ``rmse``: root mean square of ``ln(COP_model / COP_ref)``; ``mape``: mean of
    ``|COP_model / COP_ref - 1|``; ``bias``: mean of ``COP_model / COP_ref - 1``.
    All over the ``n`` points with a finite, positive model value; ``n_failed``
    points had none (the model did not solve them). NaN scores when ``n`` is 0.
    """

    n: int
    n_failed: int
    rmse: float
    mape: float
    bias: float


def _solved(cop_model: np.ndarray) -> np.ndarray:
    """Mask of the model COPs that count as solved: finite and positive (else the point is unsolved)."""
    return np.asarray(np.isfinite(cop_model) & (cop_model > 0.0), dtype=bool)


def cop_errors(cop_reference: Sequence[float] | np.ndarray, cop_model: Sequence[float] | np.ndarray) -> CopErrors:
    """Score ``cop_model`` against ``cop_reference`` (equal-length arrays; see :class:`CopErrors`).

    Raises ``ValueError`` for unequal lengths or a non-positive or non-finite reference value.
    """
    ref = np.asarray(cop_reference, dtype=float).ravel()
    mod = np.asarray(cop_model, dtype=float).ravel()
    if ref.size != mod.size:
        raise ValueError(f"{ref.size} reference values but {mod.size} model values")
    if not (np.all(np.isfinite(ref)) and np.all(ref > 0.0)):
        raise ValueError("reference COP values must be finite and positive")
    ok = _solved(mod)
    n = int(ok.sum())
    if n == 0:
        return CopErrors(0, int(ref.size), math.nan, math.nan, math.nan)
    ratio = mod[ok] / ref[ok]
    return CopErrors(
        n=n,
        n_failed=int(ref.size - n),
        rmse=float(np.sqrt(np.mean(np.log(ratio) ** 2))),
        mape=float(np.mean(np.abs(ratio - 1.0))),
        bias=float(np.mean(ratio - 1.0)),
    )


@dataclass(frozen=True)
class StageScore:
    """COP errors after one calibration stage, as fractions (0.05 = 5 %).

    ``rmse_fit`` / ``mape_fit``: with the parameters fitted to all points.
    ``rmse_cv`` / ``mape_cv``: from the held-out predictions of a k-fold cross
    validation, or None when the stage was not cross-validated. ``adopted``:
    whether the stage was kept. The first stage is normally the uncalibrated
    model ("Default").
    """

    label: str
    rmse_fit: float
    mape_fit: float
    rmse_cv: float | None = None
    mape_cv: float | None = None
    adopted: bool = False

    def __post_init__(self) -> None:
        if not self.label:
            raise ValueError("a stage needs a label")
        for name in ("rmse_fit", "mape_fit", "rmse_cv", "mape_cv"):
            v = getattr(self, name)
            if v is not None and not (math.isfinite(v) and v >= 0.0):
                raise ValueError(f"{name} must be finite and non-negative, got {v!r}")


def _stage_panel(ax: Axes, stages: Sequence[StageScore], fit: str, cv: str, ylabel: str) -> None:
    """One metric across the stages: fit line, CV markers (filled = adopted; grey for the first stage)."""
    xs = np.arange(len(stages))
    ax.plot(
        xs,
        [getattr(s, fit) * 100.0 for s in stages],
        color=VIOLET,
        alpha=0.55,
        marker=".",
        ms=4,
        label="Fit to all points",
    )
    for i, s in enumerate(stages):
        v = getattr(s, cv)
        if v is None:
            continue
        colour = GREY if i == 0 else VIOLET
        label = None if i == 0 else ("Cross-validated, adopted" if s.adopted else "Cross-validated, rejected")
        if s.adopted:
            ax.scatter([i], [v * 100.0], s=30, color=colour, zorder=3, label=label)
        else:
            ax.scatter([i], [v * 100.0], s=30, facecolor="white", edgecolor=colour, lw=1.0, zorder=3, label=label)
    ax.set_xticks(xs, [s.label for s in stages])
    ax.set_ylim(bottom=0.0)
    ax.set_ylabel(ylabel)
    # every stage keeps its name; the names turn only where they would collide
    _dm().avoid_tick_overlap(ax, "x", max_visible=len(stages))


@_in_figure_style
def plot_calibration_stages(stages: Sequence[StageScore], *, title: str | None = None) -> Figure:
    """COP RMSE (left) and MAPE (right) [%] after each calibration stage, in the given order.

    Line: the fit to all points. Markers: the cross-validated value, filled
    for an adopted stage and open for a rejected one (none where the stage
    was not cross-validated). The first stage's markers are grey (the
    uncalibrated model), the rest violet. Raises ``ValueError`` for no stages.
    """
    if not stages:
        raise ValueError("no stages to plot")
    plt, dm = _plt(), _dm()
    fig, axes = plt.subplots(1, 2, figsize=dm.figsize(_WIDTH, _STAGES_HEIGHT), gridspec_kw={"wspace": 0.3})
    _stage_panel(axes[0], stages, "rmse_fit", "rmse_cv", "COP RMSE, ln(model/catalogue) [%]")
    _stage_panel(axes[1], stages, "mape_fit", "mape_cv", "COP MAPE [%]")
    if title:
        fig.suptitle(title)
    dm.figure_legend(fig, loc="below", frameon=False, markerscale=1.4)
    _finish(fig)
    return cast("Figure", fig)


@_in_figure_style
def plot_cop_parity(
    cop_catalogue: Sequence[float] | np.ndarray,
    cop_before: Sequence[float] | np.ndarray,
    cop_after: Sequence[float] | np.ndarray,
    *,
    mode: Sequence[str] | np.ndarray | None = None,
    labels: tuple[str, str] = ("Default maps", "Calibrated"),
    band: float = 0.10,
    title: str | None = None,
) -> Figure:
    """Model against catalogue COP: before (left, grey) and after (right, violet) on square axes of the same range.

    Equal-length arrays; a non-finite or non-positive model value is a point
    the model did not solve, left out of the plot, the axis range and the
    scores alike and counted in the title. Each panel's left title is its
    label, the right title its MAPE, bias and RMSE (:func:`cop_errors`). The
    panels share no axis: each has its own tick labels. Lines: y = x and
    ``±band`` (dashed). ``mode`` per point: ``"heating"`` circles, ``"cooling"``
    squares, anything else triangles. Raises ``ValueError`` as :func:`cop_errors`
    or when ``mode`` has another length.
    """
    ref = np.asarray(cop_catalogue, dtype=float).ravel()
    preds = [np.asarray(cop_before, dtype=float).ravel(), np.asarray(cop_after, dtype=float).ravel()]
    scores = [cop_errors(ref, p) for p in preds]
    modes = np.asarray(["" for _ in ref] if mode is None else mode, dtype=object).ravel()
    if modes.size != ref.size:
        raise ValueError(f"{modes.size} modes for {ref.size} points")
    finite = np.concatenate([ref] + [p[_solved(p)] for p in preds])
    lo = max(0.0, float(finite.min()) * 0.9)
    hi = float(finite.max()) * 1.1

    plt, dm = _plt(), _dm()
    fig, axes = plt.subplots(1, 2, figsize=dm.figsize(_WIDTH, _PARITY_HEIGHT), gridspec_kw={"wspace": 0.3})
    markers = {"heating": "o", "cooling": "s"}
    for ax, pred, score, label, colour in zip(axes, preds, scores, labels, (GREY, VIOLET), strict=True):
        ok = _solved(pred)
        for m in dict.fromkeys(modes.tolist()):
            sel = ok & (modes == m)
            if sel.any():
                ax.scatter(
                    ref[sel],
                    pred[sel],
                    s=12,
                    marker=markers.get(m, "^"),
                    color=colour,
                    alpha=0.7,
                    lw=0,
                    label=m or None,
                )
        ax.plot([lo, hi], [lo, hi], color=INK, lw=0.7)
        for f in (1.0 - band, 1.0 + band):
            ax.plot([lo, hi], [lo * f, hi * f], color=FAINT, lw=0.6, ls="--")
        scores_text = f"MAPE {score.mape * 100:.1f} %, bias {score.bias * 100:+.1f} %, RMSE {score.rmse * 100:.1f} %"
        if score.n_failed:
            scores_text += f"\n{score.n_failed} of {ref.size} points not solved"
        ax.set_title(label, loc="left")
        ax.set_title(scores_text, loc="right", fontsize=dm.fs(-1), fontweight=dm.fw(0))
        ax.set_xlim(lo, hi)
        ax.set_ylim(lo, hi)
        ax.set_box_aspect(1.0)
        ax.set_xlabel("Catalogue COP")
        ax.set_ylabel("Model COP")
        if any(modes):
            ax.legend(loc="lower right", frameon=False, markerscale=1.6)
    fig.suptitle(title or f"COP parity, {ref.size} points; dashed ±{band * 100:g} %")
    _finish(fig)
    return cast("Figure", fig)


class CompressorMapPlotMixin:
    """Gives a heat-pump model :meth:`plot_compressor_map` over its own compressor efficiencies."""

    eta_cmp_isen: float | Callable
    eta_cmp_vol: float | Callable
    eta_cmp: float | Callable

    def plot_compressor_map(
        self,
        before: EfficiencySource | None = None,
        *,
        labels: tuple[str, str] = ("Default maps", "This model"),
        **kwargs: Any,
    ) -> Figure:
        """This model's compressor map (row 2) against ``before`` (row 1; default maps when None).

        ``before`` is a mapping of the three efficiencies or another model.
        Other keywords go to :func:`plot_compressor_map` (``T_evap_C``,
        ``quantity``, ``points``, ``pr_range``, ``rps_range``, ``title``,
        ``resolution``). Returns the figure.
        """
        return plot_compressor_map(before, self, labels=labels, **kwargs)
