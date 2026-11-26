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

Axes follow the figure drawing rules of the ``figma-plot`` skill that do not
depend on the style (#258): every linear axis, the colour bars included, has
an explicit ``(min, max, interval)`` with ticks at both ends; tick labels carry only the decimals the
interval needs; percent is written ``(%)`` and physical units ``[unit]``;
titles name quantities in words; a colour bar stands upright to the right of
its panel, placed from the panel's final position, and shows its two end
values only, with no triangular ends.
"""

from __future__ import annotations

import functools
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
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
    from matplotlib.cm import ScalarMappable
    from matplotlib.figure import Figure
    from matplotlib.text import Text
    from matplotlib.ticker import Formatter

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
#: Default axis ranges of the map panels: pressure ratio (linear, a tick at every whole number) and speed [rev/s].
DEFAULT_PR_RANGE: tuple[float, float] = (1.0, 12.0)
DEFAULT_RPS_RANGE: tuple[float, float] = (15.0, 120.0)

#: The dartwork-mpl preset every figure of this module is drawn in.
FIGURE_STYLE = "scientific"
#: Figure widths and heights (dartwork-mpl lengths): two journal columns wide.
_WIDTH = "17cm"
_MAP_ROW_HEIGHT_CM = 6.0
_STAGES_HEIGHT = "7.5cm"
_PARITY_HEIGHT = "9cm"

#: Marker areas [pt^2] of the map's peak diamonds and operating points.
_PEAK_SIZE = 22.0
_POINT_SIZE = 4.5
#: Every how many pressure-ratio ticks carries a number (a tick at every whole number either way).
_PR_LABEL_EVERY = 1
#: Symbols with a rendered subscript. ``\mathregular`` draws them in the text's own font and
#: weight: plain mathtext would take its fonts from the rcParams at draw time, after the
#: preset is gone (DejaVu Sans, heavier than the preset's light Roboto).
_R_P = r"$\mathregular{r_p}$"
_N = "N"

GREY = "oc.gray6"
VIOLET = "tw.violet600"
ORANGE = "tw.orange500"
INK = "oc.gray8"
FAINT = "oc.gray5"
HEATING = "tw.red500"
COOLING = "tw.blue600"
_PARITY_POINT_SIZE = 12.0
_RIM_WIDTH = 0.3

#: Margin [pt] between the figure edge and its content (1.5 mm).
_MARGIN_PT = 1.5 / 25.4 * 72.0

#: An axis specification ``(min, max, interval)``: limits, and ticks from min to max every interval.
AxisSpec = tuple[float, float, float]
#: Interval mantissas an axis may use (x 10^k); speed axes may also use 1.5, 2.5 and 3 to keep a given range.
_STEPS = (1.0, 2.0, 5.0)
_FINE_STEPS = (1.0, 1.5, 2.0, 2.5, 3.0, 5.0)
#: Colour-bar styles of the figma-plot skill: (height / panel height, width / panel width, anchored edge).
_COLOURBAR_STYLES: dict[str, tuple[float, float, str]] = {
    "full-height-top": (1.0, 0.03, "top"),
    "half-height-bottom": (0.5, 0.015, "bottom"),
}
#: The style the map uses (``half-height-bottom`` is 0.6 mm wide on a 17 cm, three-column map: too thin).
_COLOURBAR_STYLE = "full-height-top"
#: Gap between a panel and its colour bar, and between the bar and its end values and label [pt].
_COLOURBAR_GAP_PT = 6.0
_COLOURBAR_PAD_PT = 3.0


@dataclass(frozen=True)
class _MapQuantity:
    """How one plotted efficiency is named and coloured (fixed levels keep figures comparable)."""

    label: str
    symbol: str
    levels: tuple[float, ...]
    #: The colour-bar label: one line, the values in percent.
    bar_label: str


def _levels(lo: float, hi: float, step: float) -> tuple[float, ...]:
    """Evenly spaced contour levels from ``lo`` to ``hi`` inclusive."""
    n = round((hi - lo) / step)
    return tuple(round(lo + i * step, 6) for i in range(n + 1))


def _axis_spec(lo: float, hi: float, *, steps: Sequence[float] = _STEPS, max_intervals: int = 8) -> AxisSpec:
    """The tightest ``(min, max, interval)`` holding ``[lo, hi]``, with 3 to ``max_intervals`` intervals.

    The interval is a mantissa of ``steps`` times a power of ten; min and max
    are multiples of it (``lo`` and ``hi`` rounded outward). Of equally tight
    specifications the one with the larger interval wins. ``hi <= lo`` widens
    the range to ``[lo, lo + 1]``.
    """
    if not (math.isfinite(lo) and math.isfinite(hi)):
        raise ValueError(f"an axis range needs finite ends, got ({lo}, {hi})")
    if hi <= lo:
        hi = lo + 1.0
    k0 = math.floor(math.log10(hi - lo))
    best: tuple[tuple[float, float], AxisSpec] | None = None
    for k in range(k0 - 2, k0 + 2):
        for m in steps:
            step = m * 10.0**k
            start = math.floor(lo / step + 1e-9) * step
            stop = math.ceil(hi / step - 1e-9) * step
            n = round((stop - start) / step)
            if 3 <= n <= max_intervals:
                key = (round(stop - start, 9), -step)
                if best is None or key < best[0]:
                    best = (key, (round(start, 9), round(stop, 9), round(step, 9)))
    if best is None:  # pragma: no cover - some mantissa always gives 3 to 8 intervals
        raise ValueError(f"no axis specification for ({lo}, {hi})")
    return best[1]


def _spec_ticks(spec: AxisSpec) -> list[float]:
    """The ticks of ``spec``: min, min + interval, ..., max (both ends included)."""
    lo, hi, step = spec
    n = round((hi - lo) / step)
    if not (lo < hi and step > 0 and math.isclose(n * step, hi - lo, rel_tol=1e-9)):
        raise ValueError(f"axis spec {spec} is not (min < max, interval > 0, (max - min) / interval an integer)")
    return [round(lo + i * step, 9) for i in range(n + 1)]


def _decimals(*values: float) -> int:
    """Decimal places needed to write every value exactly (0 for integers)."""
    return max(0, *(-int(Decimal(repr(round(v, 9))).normalize().as_tuple().exponent) for v in values))


def _tick_formatter(interval: float, start: float) -> Formatter:
    """Tick labels with only the decimals the interval needs: 15, 30 or 0.5, 1.0, 1.5 (never 15.0)."""
    from matplotlib.ticker import FuncFormatter

    places = _decimals(interval, start)

    def label(value: float, _pos: int | None = None) -> str:
        text = f"{value:.{places}f}"
        if float(text) == 0.0:
            text = f"{0.0:.{places}f}"
        return text.replace("-", "\N{MINUS SIGN}")

    return FuncFormatter(label)


def _percent_formatter(interval: float, start: float) -> Formatter:
    """Labels of fractions written in percent, with only the decimals the interval needs (0.3 -> 30)."""
    from matplotlib.ticker import FuncFormatter

    base = _tick_formatter(round(interval * 100.0, 9), round(start * 100.0, 9))
    return FuncFormatter(lambda value, pos=None: base(round(value * 100.0, 9), pos))


def _apply_spec(ax: Axes, which: str, spec: AxisSpec) -> None:
    """Set the limits and ticks of the linear ``which`` (``"x"`` or ``"y"``) axis of ``ax`` to ``spec``.

    The ticks are labelled with only the decimals the interval needs.
    """
    ticks = _spec_ticks(spec)
    axis = ax.xaxis if which == "x" else ax.yaxis
    ax.set(**{f"{which}lim": (spec[0], spec[1])})
    axis.set_ticks(ticks)
    axis.set_major_formatter(_tick_formatter(spec[2], spec[0]))


#: Quantities :func:`plot_compressor_map` can draw. ``eta_ov`` is the product
#: ``eta_cmp_isen * eta_cmp`` (the overall efficiency the calibration scales);
#: the others are the package's three efficiencies as the cycle evaluates them.
MAP_QUANTITIES: dict[str, _MapQuantity] = {
    "eta_ov": _MapQuantity("Overall efficiency", "η_ov", _levels(0.30, 0.85, 0.025), "Overall efficiency (%)"),
    "eta_cmp_isen": _MapQuantity(
        "Isentropic efficiency", "η_is", _levels(0.40, 1.00, 0.025), "Isentropic efficiency (%)"
    ),
    "eta_cmp_vol": _MapQuantity("Volumetric efficiency", "η_v", _levels(0.70, 1.00, 0.02), "Volumetric efficiency (%)"),
    "eta_cmp": _MapQuantity(
        "Electromechanical efficiency", "η_em", _levels(0.70, 1.00, 0.02), "Electromechanical efficiency (%)"
    ),
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


def _finish(fig: Figure, *, right_pt: float = 0.0) -> None:
    """Pin the tick style, then fit the layout (dartwork-mpl ``simple_layout``) with a small margin.

    ``right_pt`` more is kept free at the right edge (room for colour bars placed after the layout).
    """
    dm = _dm()
    _pin_tick_style(fig)
    dm.simple_layout(fig, margin=dm.pt(_MARGIN_PT), mr=dm.pt(_MARGIN_PT + right_pt))
    if _separate_corner_ticks(fig):
        dm.simple_layout(fig, margin=dm.pt(_MARGIN_PT), mr=dm.pt(_MARGIN_PT + right_pt))


def _separate_corner_ticks(fig: Figure, max_extra_pt: float = 8.0) -> bool:
    """Push y tick labels outward where they touch the x tick labels at a panel's lower-left corner.

    The figma-plot rule for the origin labels (``separate_origin_ticks``): the
    axes box stays, only the y label pad grows, 1 pt at a time. Returns whether
    any pad changed.
    """
    changed = False
    for ax in fig.axes:
        # the pad _pin_tick_style wrote (the matplotlib stubs type Tick.get_pad as returning None)
        pad = float(ax.yaxis.get_tick_params(which="major").get("pad", _plt().rcParams["ytick.major.pad"]))
        limit = pad + max_extra_pt
        while pad < limit:
            fig.canvas.draw()
            xs = [t.get_window_extent() for t in ax.get_xticklabels() if t.get_visible() and t.get_text()]
            ys = [t.get_window_extent() for t in ax.get_yticklabels() if t.get_visible() and t.get_text()]
            if not any(x.overlaps(y) for x in xs for y in ys):
                break
            pad += 1.0
            ax.tick_params(axis="y", which="major", pad=pad)
            changed = True
    return changed


def _text_width_pt(fig: Figure, text: str, fontsize: float) -> float:
    """Width [pt] ``text`` takes when drawn at ``fontsize`` in ``fig``."""
    t = fig.text(0.0, 0.0, text, fontsize=fontsize)
    width = t.get_window_extent().width * 72.0 / fig.dpi
    t.remove()
    return float(width)


def _colourbar_room_pt(fig: Figure, panel_width_pt: float, spec: AxisSpec) -> float:
    """Room [pt] right of a panel that a :func:`_add_colourbar` bar, its end values and its upright label take."""
    fontsize = float(_plt().rcParams["ytick.labelsize"])
    _, width_ratio, _ = _COLOURBAR_STYLES[_COLOURBAR_STYLE]
    fmt = _percent_formatter(spec[2], spec[0])
    values = max(_text_width_pt(fig, fmt(v), fontsize) for v in spec[:2])
    bar = width_ratio * panel_width_pt
    slack = 1.0
    # the label stands upright beside the end values: one line height wide
    label_height = 1.2 * fontsize
    return _COLOURBAR_GAP_PT + bar + _COLOURBAR_PAD_PT + values + _COLOURBAR_PAD_PT + label_height + slack


def _add_colourbar(fig: Figure, ax: Axes, mappable: ScalarMappable, spec: AxisSpec, label: str) -> Axes:
    """An upright colour bar right of ``ax`` in the :data:`_COLOURBAR_STYLE` style; returns its axes.

    Placed from the final position of ``ax`` (call it after the layout). It
    shows the two end values of ``spec`` = ``(min, max, max - min)``, which must
    be the colour range of ``mappable``, in percent, with no tick lines, and
    ``label`` as one upright line centred on the bar, right of the end values.
    The bar has no triangular ends.
    """
    plt = _plt()
    lo, hi, step = spec
    vmin, vmax = cast("tuple[float, float]", (mappable.norm.vmin, mappable.norm.vmax))
    if not (math.isclose(step, hi - lo) and np.allclose([vmin, vmax], [lo, hi])):
        raise ValueError(f"colour bar spec {spec} is not (min, max, max - min) of the colour range")
    height_ratio, width_ratio, anchor = _COLOURBAR_STYLES[_COLOURBAR_STYLE]
    fig.canvas.draw()
    box = ax.get_window_extent()
    height, width = box.height * height_ratio, box.width * width_ratio
    y0 = box.y1 - height if anchor == "top" else box.y0
    x0 = box.x1 + _COLOURBAR_GAP_PT * fig.dpi / 72.0
    (fx0, fy0), (fx1, fy1) = fig.transFigure.inverted().transform([(x0, y0), (x0 + width, y0 + height)])
    cax = fig.add_axes((fx0, fy0, fx1 - fx0, fy1 - fy0))
    bar = fig.colorbar(mappable, cax=cax, orientation="vertical", extend="neither")
    bar.set_ticks(_spec_ticks(spec))
    cax.yaxis.set_major_formatter(_percent_formatter(step, lo))
    cax.set_ylim(lo, hi)
    cax.minorticks_off()
    rc = plt.rcParams
    colour = rc["ytick.labelcolor"] if rc["ytick.labelcolor"] != "inherit" else rc["ytick.color"]
    cax.tick_params(
        axis="y", which="both", length=0, pad=_COLOURBAR_PAD_PT, labelsize=rc["ytick.labelsize"], labelcolor=colour
    )
    # one line, turned upright and centred on the bar, right of the end values
    cax.set_ylabel(label, rotation=90, fontsize=rc["ytick.labelsize"], color=colour, labelpad=_COLOURBAR_PAD_PT)
    cax.yaxis.set_label_position("right")
    return cast("Axes", cax)


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


def _label_every(axis: Any, every: int, spec: AxisSpec) -> None:
    """Keep a tick at every interval of ``spec`` but write a number only on every ``every``-th, from the minimum."""
    from matplotlib.ticker import FuncFormatter

    base = _tick_formatter(spec[2], spec[0])

    def label(value: float, pos: int | None = None) -> str:
        k = round((value - spec[0]) / spec[2])
        return base(value, pos) if k % every == 0 else ""

    axis.set_major_formatter(FuncFormatter(label))


def _map_legend(fig: Figure, *, has_range: bool, has_points: bool) -> None:
    """One legend under the map: what the orange outline, the diamond and the dots are, at their drawn sizes.

    The entries are stand-ins drawn on the first panel, not the plotted
    artists: the peak diamond takes each row's colour, and the row headings
    already say which row is which, so the legend shows one neutral diamond.
    """
    dm = _dm()
    ax = fig.axes[0]
    if has_range:
        ax.plot([], [], color=ORANGE, lw=1.1, label="Data range of the default maps")
    ax.plot([], [], ls="", marker="D", ms=math.sqrt(_PEAK_SIZE), mfc="white", mec=INK, mew=0.6, label="Peak efficiency")
    if has_points:
        ax.plot(
            [],
            [],
            ls="",
            marker="o",
            ms=math.sqrt(_POINT_SIZE),
            mfc="black",
            mec="white",
            mew=_RIM_WIDTH,
            label="Operating points (nearest column)",
        )
    dm.figure_legend(fig, loc="below", frameon=False, markerscale=1.0)


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
    beyond them take the end colours. Axes: pressure ratio ``r_p`` (linear,
    ``pr_range`` rounded out to whole numbers, a tick at each) and speed ``N`` [rev/s] (``rps_range``, with ticks at a
    round interval; a range that no such interval divides is widened to one,
    the default 15 to 120 has ticks every 15), the same on every panel.

    Each row is a block of its own, headed by its label (``labels``) and set
    apart from the other row; no axis is shared, so every panel has its own
    tick labels. Drawn on every panel: the efficiency (filled contours,
    labelled lines), the data range of the default maps at that ``T_evap``
    (orange), the grid peak (diamond, in the row's colour), and ``points`` as
    small black dots with a thin white rim. The panel's left title is its
    evaporating temperature, the right title the peak value and place. One
    legend under the figure names the outline, the peak and the points at
    their drawn sizes; the figure has no title unless ``title`` is given. Each
    row has its own colour bar right of its last panel, the same levels in
    both rows, showing the two end values. ``points`` (an
    :class:`OperatingPoints` or model result rows) are drawn on the column
    whose ``T_evap`` is nearest to each point's. ``resolution`` is the grid
    size (pressure ratio, speed).

    Returns the figure: 2 x ``len(T_evap_C)`` map axes and a colour bar for
    each row. Raises ``ValueError`` for an unknown quantity or no
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
    speed_spec = _axis_spec(*rps_range, steps=_FINE_STEPS)
    # a linear pressure-ratio axis with a tick at every whole number
    pr_spec = (float(math.floor(pr_range[0])), float(math.ceil(pr_range[1])), 1.0)
    prs = np.linspace(pr_spec[0], pr_spec[1], resolution[0])
    ns = np.linspace(speed_spec[0], speed_spec[1], resolution[1])
    column_of_point = (
        np.argmin(np.abs(pts.T_evap_C[:, None] - np.asarray(temps)[None, :]), axis=1) if pts is not None else None
    )
    levels = np.asarray(spec.levels)
    bar_spec = (float(levels[0]), float(levels[-1]), round(float(levels[-1] - levels[0]), 9))
    line_label = _percent_formatter(float(levels[4] - levels[0]), float(levels[0]))

    plt, dm = _plt(), _dm()

    ncol = len(temps)
    fig = plt.figure(figsize=dm.figsize(_WIDTH, dm.cm(2 * _MAP_ROW_HEIGHT_CM + 1.6)))
    row_boxes = fig.subfigures(2, 1, hspace=0.0)
    last_panels: list[tuple[Axes, ScalarMappable]] = []
    line_labels: list[tuple[Axes, Text]] = []
    has_range = False
    for r, (eff, row_box) in enumerate(zip(rows, row_boxes, strict=True)):
        # the row heading outranks the panel titles: larger and bolder
        row_box.suptitle(labels[r], x=0.0, ha="left", fontsize=dm.fs(2), fontweight=dm.fw(3))
        row_axes = row_box.subplots(1, ncol, squeeze=False, gridspec_kw={"wspace": 0.3})[0]
        colour = GREY if r == 0 else VIOLET
        for c, (ax, T_e) in enumerate(zip(row_axes, temps, strict=True)):
            # scale and limits first: the contour labels are placed in the panel as it will be shown
            _apply_spec(ax, "x", pr_spec)
            if _PR_LABEL_EVERY > 1:
                _label_every(ax.xaxis, _PR_LABEL_EVERY, pr_spec)
            _apply_spec(ax, "y", speed_spec)
            Z = efficiency_grid(eff, quantity, prs, ns, T_e)
            # values beyond the fixed levels are filled in the end colours, not left blank
            filled = ax.contourf(prs, ns, np.clip(Z, levels[0], levels[-1]), levels=levels, cmap="Blues")
            lines = ax.contour(prs, ns, Z, levels=levels[::2], colors="white", linewidths=0.5)
            for text in ax.clabel(lines, levels=levels[::4], fontsize=dm.fs(-1.5), fmt=line_label):
                fill = filled.cmap(filled.norm(float(text.get_text()) / 100.0))
                text.set_color(dm.readable_text_color(fill))
                line_labels.append((ax, text))
            inside = np.array([[DOMAIN.contains(pr, n, T_e) for pr in prs] for n in ns], dtype=float)
            if 0.0 < inside.mean() < 1.0:
                ax.contour(prs, ns, inside, levels=[0.5], colors=ORANGE, linewidths=1.1)
                has_range = True
            ax.set_title(
                f"Evaporating {_tick_formatter(1.0, T_e)(T_e)} °C", loc="left", fontsize=dm.fs(0), fontweight=dm.fw(2)
            )
            peak = _peak(prs, ns, Z)
            if peak is not None:
                ax.scatter(
                    [peak[1]],
                    [peak[2]],
                    marker="D",
                    s=_PEAK_SIZE,
                    color=colour,
                    edgecolor="white",
                    lw=0.6,
                    zorder=5,
                    gid="peak",
                )
                ax.set_title(
                    f"peak {peak[0] * 100.0:.1f} %\n{_R_P} {peak[1]:.2f}, {_N} {peak[2]:.0f}",
                    loc="right",
                    fontsize=dm.fs(-1),
                    fontweight=dm.fw(0),
                )
            if pts is not None and column_of_point is not None:
                sel = column_of_point == c
                # solid black with a hairline white rim: overlapping points stay apart
                ax.scatter(
                    pts.pressure_ratio[sel],
                    pts.rps[sel],
                    s=_POINT_SIZE,
                    facecolor="black",
                    edgecolor="white",
                    lw=_RIM_WIDTH,
                    zorder=4,
                    gid="operating-points",
                )
            ax.set_xlabel(f"Pressure ratio ({_R_P})")
        row_axes[0].set_ylabel(f"Speed ({_N}) [rev/s]")
        last_panels.append((row_axes[-1], filled))
    if title:
        fig.suptitle(title)
    _map_legend(fig, has_range=has_range, has_points=pts is not None)
    panel_width_pt = fig.get_figwidth() * 72.0 / ncol
    _finish(fig, right_pt=_colourbar_room_pt(fig, panel_width_pt, bar_spec))
    # a contour label that the panel edge would cut, or that would cover one already kept
    # (where the lines crowd at low pressure ratio), is left out rather than shown in part
    fig.canvas.draw()
    kept: dict[int, list[Any]] = {}
    for ax, text in line_labels:
        panel, box = ax.get_window_extent(), text.get_window_extent()
        inside = panel.contains(box.x0, box.y0) and panel.contains(box.x1, box.y1)
        if not inside or any(box.overlaps(k) for k in kept.get(id(ax), [])):
            text.remove()
        else:
            kept.setdefault(id(ax), []).append(box)
    # one bar per row, each from its last panel's final place, so each row reads on its own
    for ax, filled in last_panels:
        _add_colourbar(fig, ax, filled, bar_spec, spec.bar_label)
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
    values = [100.0 * v for s in stages for v in (getattr(s, fit), getattr(s, cv)) if v is not None]
    # from zero to just above the largest value, so no marker sits on the frame
    _apply_spec(ax, "y", _axis_spec(0.0, 1.05 * max(values)))
    ax.set_ylabel(ylabel)
    # every stage keeps its name; the names turn only where they would collide
    _dm().avoid_tick_overlap(ax, "x", max_visible=len(stages))


@_in_figure_style
def plot_calibration_stages(stages: Sequence[StageScore], *, title: str | None = None) -> Figure:
    """COP RMSE (left) and MAPE (right) (%) after each calibration stage, in the given order.

    Line: the fit to all points. Markers: the cross-validated value, filled
    for an adopted stage and open for a rejected one (none where the stage
    was not cross-validated). The first stage's markers are grey (the
    uncalibrated model), the rest violet. Each error axis runs from 0 to a
    round value just above the largest error, with ticks at a round interval.
    Raises ``ValueError`` for no stages.
    """
    if not stages:
        raise ValueError("no stages to plot")
    plt, dm = _plt(), _dm()
    fig, axes = plt.subplots(1, 2, figsize=dm.figsize(_WIDTH, _STAGES_HEIGHT), gridspec_kw={"wspace": 0.3})
    _stage_panel(axes[0], stages, "rmse_fit", "rmse_cv", "COP RMSE, ln(model/catalogue) (%)")
    _stage_panel(axes[1], stages, "mape_fit", "mape_cv", "COP MAPE (%)")
    if title:
        fig.suptitle(title)
    dm.figure_legend(fig, loc="below", frameon=False, markerscale=1.0)
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
    """Model against catalogue COP: before (left) and after (right) on square axes of the same range.

    Equal-length arrays; a non-finite or non-positive model value is a point
    the model did not solve, left out of the plot, the axis range and the
    scores alike and counted in the title. Each panel's left title is its
    label, the right title its MAPE, bias and RMSE (:func:`cop_errors`). The
    panels share no axis: each has its own tick labels. Both axes of both
    panels run over the drawn values rounded outward to a round interval
    (the same ``(min, max, interval)`` everywhere). Lines: y = x and
    ``±band`` (dashed). Every point is an opaque circle with a hairline white rim,
    so overlapping points stay apart; ``mode`` per point sets its colour:
    ``"heating"`` red, ``"cooling"`` blue, anything else grey. Raises ``ValueError`` as :func:`cop_errors`
    or when ``mode`` has another length.
    """
    ref = np.asarray(cop_catalogue, dtype=float).ravel()
    preds = [np.asarray(cop_before, dtype=float).ravel(), np.asarray(cop_after, dtype=float).ravel()]
    scores = [cop_errors(ref, p) for p in preds]
    modes = np.asarray(["" for _ in ref] if mode is None else mode, dtype=object).ravel()
    if modes.size != ref.size:
        raise ValueError(f"{modes.size} modes for {ref.size} points")
    finite = np.concatenate([ref] + [p[_solved(p)] for p in preds])
    lo, hi, _ = cop_spec = _axis_spec(max(0.0, float(finite.min()) * 0.9), float(finite.max()) * 1.1)

    plt, dm = _plt(), _dm()
    fig, axes = plt.subplots(1, 2, figsize=dm.figsize(_WIDTH, _PARITY_HEIGHT), gridspec_kw={"wspace": 0.3})
    colours = {"heating": HEATING, "cooling": COOLING}
    for ax, pred, score, label in zip(axes, preds, scores, labels, strict=True):
        ok = _solved(pred)
        for m in dict.fromkeys(modes.tolist()):
            sel = ok & (modes == m)
            if sel.any():
                ax.scatter(
                    ref[sel],
                    pred[sel],
                    s=_PARITY_POINT_SIZE,
                    marker="o",
                    facecolor=colours.get(m, GREY),
                    edgecolor="white",
                    linewidths=_RIM_WIDTH,
                    label=m or None,
                    zorder=3,
                )
        ax.plot([lo, hi], [lo, hi], color=INK, lw=0.7)
        for f in (1.0 - band, 1.0 + band):
            ax.plot([lo, hi], [lo * f, hi * f], color=FAINT, lw=0.6, ls="--")
        scores_text = f"MAPE {score.mape * 100:.1f} %, bias {score.bias * 100:+.1f} %, RMSE {score.rmse * 100:.1f} %"
        if score.n_failed:
            scores_text += f"\n{score.n_failed} of {ref.size} points not solved"
        ax.set_title(label, loc="left")
        ax.set_title(scores_text, loc="right", fontsize=dm.fs(-1), fontweight=dm.fw(0))
        _apply_spec(ax, "x", cop_spec)
        _apply_spec(ax, "y", cop_spec)
        ax.set_box_aspect(1.0)
        ax.set_xlabel("Catalogue COP")
        ax.set_ylabel("Model COP")
        if any(modes):
            ax.legend(loc="lower right", frameon=False, markerscale=1.0)
    if title:
        fig.suptitle(title)
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
