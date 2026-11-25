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

Each function returns a :class:`matplotlib.figure.Figure` and sets no global
style; apply one (for instance ``dartwork_mpl.style.use("report")``) before
calling. Colours: grey = before (default maps), violet = after (calibrated),
orange = the data range of the default maps, blue = efficiency magnitude.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, cast

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

GREY = "#8a8f98"
VIOLET = "#7c3aed"
ORANGE = "#f97316"


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


def _plt():
    import matplotlib.pyplot as plt

    return plt


def _peak(prs: np.ndarray, ns: np.ndarray, Z: np.ndarray) -> tuple[float, float, float] | None:
    """(value, pressure ratio, speed) of the largest finite grid value, or None if the grid has none."""
    if not np.isfinite(Z).any():
        return None
    j, i = np.unravel_index(np.nanargmax(Z), Z.shape)
    return float(Z[j, i]), float(prs[i]), float(ns[j])


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
    ``pr_range``) and speed ``N`` [rev/s] (``rps_range``), shared by all panels.

    Drawn on every panel: the efficiency (filled contours, labelled lines),
    the data range of the default maps at that ``T_evap`` (orange), and the
    grid peak (diamond; value and place in the panel title). ``points``
    (an :class:`OperatingPoints` or model result rows) are drawn on the column
    whose ``T_evap`` is nearest to each point's. ``resolution`` is the grid
    size (pressure ratio, speed).

    Returns the figure (2 x ``len(T_evap_C)`` map axes and one colour bar).
    Raises ``ValueError`` for an unknown quantity or no temperatures, and
    ``TypeError`` for an efficiency source without the three efficiencies.
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

    plt = _plt()
    from matplotlib.lines import Line2D

    ncol = len(temps)
    fig, axes = plt.subplots(
        2, ncol, figsize=(2.9 * ncol + 1.3, 5.9), sharex=True, sharey=True, squeeze=False, layout="constrained"
    )
    levels = np.asarray(spec.levels)
    filled = None
    for r, eff in enumerate(rows):
        for c, T_e in enumerate(temps):
            ax = axes[r, c]
            Z = efficiency_grid(eff, quantity, prs, ns, T_e)
            filled = ax.contourf(prs, ns, Z, levels=levels, cmap="Blues", extend="both")
            lines = ax.contour(prs, ns, Z, levels=levels[::2], colors="white", linewidths=0.5)
            ax.clabel(lines, fontsize=6, fmt="%.2f")
            inside = np.array([[DOMAIN.contains(pr, n, T_e) for pr in prs] for n in ns], dtype=float)
            if 0.0 < inside.mean() < 1.0:
                ax.contour(prs, ns, inside, levels=[0.5], colors=ORANGE, linewidths=1.1)
            peak = _peak(prs, ns, Z)
            head = f"T_evap {T_e:g} °C"
            if peak is not None:
                ax.scatter(
                    [peak[1]],
                    [peak[2]],
                    marker="D",
                    s=26,
                    color=GREY if r == 0 else VIOLET,
                    edgecolor="white",
                    lw=0.6,
                    zorder=5,
                )
                head += f" · peak {peak[0]:.3f} (r_p {peak[1]:.2f}, N {peak[2]:.0f})"
            ax.set_title(head, fontsize=7.5, loc="left")
            if pts is not None and column_of_point is not None:
                sel = column_of_point == c
                ax.scatter(
                    pts.pressure_ratio[sel],
                    pts.rps[sel],
                    s=11,
                    facecolor="none",
                    edgecolor="black",
                    lw=0.6,
                    zorder=4,
                    gid="operating-points",
                )
            ax.set_xscale("log")
            ticks = [t for t in (1.5, 2, 3, 4, 6, 8, 12) if pr_range[0] <= t <= pr_range[1]]
            ax.set_xticks(ticks, [f"{t:g}" for t in ticks])
            ax.minorticks_off()
            ax.set_xlim(*pr_range)
            ax.set_ylim(*rps_range)
        axes[r, 0].set_ylabel(f"{labels[r]}\nSpeed (N) [rev/s]")
    for ax in axes[-1]:
        ax.set_xlabel("Pressure ratio (r_p)")
    bar = fig.colorbar(filled, ax=axes, label=f"{spec.label} ({spec.symbol})", fraction=0.04, pad=0.02)
    bar.set_ticks(levels[::2])
    handles = [
        Line2D([], [], color=ORANGE, lw=1.1, label="Data range of the default maps"),
        Line2D([], [], color=GREY, marker="D", ms=6, ls="", label=f"Peak, {labels[0]}"),
        Line2D([], [], color=VIOLET, marker="D", ms=6, ls="", label=f"Peak, {labels[1]}"),
    ]
    if pts is not None:
        handles.append(
            Line2D([], [], mfc="none", mec="black", marker="o", ms=5, ls="", label="Operating points (nearest T_evap)")
        )
    fig.legend(handles=handles, loc="outside lower center", ncol=len(handles), fontsize=7, frameon=False)
    fig.suptitle(title or f"{spec.label} ({spec.symbol}): {labels[0]} (top) vs {labels[1]} (bottom)", fontsize=9)
    return cast("Figure", fig)


@dataclass(frozen=True)
class CopErrors:
    """COP error scores of a model against reference (catalogue) values, as fractions.

    ``rmse``: root mean square of ``ln(COP_model / COP_ref)``; ``mape``: mean of
    ``|COP_model / COP_ref - 1|``; ``bias``: mean of ``COP_model / COP_ref - 1``.
    All over the ``n`` points with a finite model value; ``n_failed`` points
    had none (the model did not solve them). NaN scores when ``n`` is 0.
    """

    n: int
    n_failed: int
    rmse: float
    mape: float
    bias: float


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
    ok = np.isfinite(mod) & (mod > 0.0)
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
    ax.plot(xs, [getattr(s, fit) * 100.0 for s in stages], color=VIOLET, lw=1.2, alpha=0.55, marker=".", ms=4)
    for i, s in enumerate(stages):
        v = getattr(s, cv)
        if v is None:
            continue
        colour = GREY if i == 0 else VIOLET
        if s.adopted:
            ax.scatter([i], [v * 100.0], s=40, color=colour, zorder=3)
        else:
            ax.scatter([i], [v * 100.0], s=40, facecolor="white", edgecolor=colour, lw=1.0, zorder=3)
    ax.set_xticks(xs, [s.label for s in stages], rotation=45, ha="right")
    ax.set_ylim(bottom=0.0)
    ax.set_ylabel(ylabel)
    ax.grid(axis="y", lw=0.4, alpha=0.5)


def plot_calibration_stages(stages: Sequence[StageScore], *, title: str | None = None) -> Figure:
    """COP RMSE (left) and MAPE (right) [%] after each calibration stage, in the given order.

    Line: the fit to all points. Markers: the cross-validated value, filled
    for an adopted stage and open for a rejected one (none where the stage
    was not cross-validated). The first stage's markers are grey (the
    uncalibrated model), the rest violet. Raises ``ValueError`` for no stages.
    """
    if not stages:
        raise ValueError("no stages to plot")
    plt = _plt()
    from matplotlib.lines import Line2D

    fig, axes = plt.subplots(1, 2, figsize=(7.6, 3.6), layout="constrained")
    _stage_panel(axes[0], stages, "rmse_fit", "rmse_cv", "COP RMSE, ln(model/catalogue) [%]")
    _stage_panel(axes[1], stages, "mape_fit", "mape_cv", "COP MAPE [%]")
    handles = [
        Line2D([], [], color=VIOLET, alpha=0.55, marker=".", ms=6, label="Fit to all points"),
        Line2D([], [], color=VIOLET, marker="o", ms=7, ls="", label="Cross-validated, adopted"),
        Line2D([], [], mfc="white", mec=VIOLET, marker="o", ms=7, ls="", label="Cross-validated, rejected"),
    ]
    fig.legend(handles=handles, loc="outside lower center", ncol=3, fontsize=7.5, frameon=False)
    if title:
        fig.suptitle(title, fontsize=9)
    return cast("Figure", fig)


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
    """Model against catalogue COP: before (left, grey) and after (right, violet) on the same square axes.

    Equal-length arrays; a non-finite model value is a point the model did not
    solve, left out of the plot and the scores and counted in the title. Each
    panel title gives MAPE, bias and RMSE (:func:`cop_errors`). Lines: y = x and
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
    finite = np.concatenate([ref] + [p[np.isfinite(p)] for p in preds])
    lo = max(0.0, float(finite.min()) * 0.9)
    hi = float(finite.max()) * 1.1

    plt = _plt()
    fig, axes = plt.subplots(1, 2, figsize=(8.0, 4.7), sharex=True, sharey=True, layout="constrained")
    markers = {"heating": "o", "cooling": "s"}
    for ax, pred, score, label, colour in zip(axes, preds, scores, labels, (GREY, VIOLET), strict=True):
        ok = np.isfinite(pred)
        for m in dict.fromkeys(modes.tolist()):
            sel = ok & (modes == m)
            if sel.any():
                ax.scatter(
                    ref[sel],
                    pred[sel],
                    s=14,
                    marker=markers.get(m, "^"),
                    color=colour,
                    alpha=0.7,
                    lw=0,
                    label=m or None,
                )
        ax.plot([lo, hi], [lo, hi], color="#444444", lw=0.7)
        for f in (1.0 - band, 1.0 + band):
            ax.plot([lo, hi], [lo * f, hi * f], color="#999999", lw=0.6, ls="--")
        head = f"{label}: MAPE {score.mape * 100:.1f} %, bias {score.bias * 100:+.1f} %, RMSE {score.rmse * 100:.1f} %"
        if score.n_failed:
            head += f"\n{score.n_failed} of {ref.size} points not solved"
        ax.set_title(head, fontsize=8, loc="left")
        ax.set_xlim(lo, hi)
        ax.set_ylim(lo, hi)
        ax.set_box_aspect(1.0)
        ax.set_xlabel("Catalogue COP")
        ax.grid(lw=0.3, alpha=0.4)
    axes[0].set_ylabel("Model COP")
    if any(modes):
        axes[1].legend(fontsize=7, frameon=False, loc="lower right", markerscale=1.6)
    fig.suptitle(title or f"COP parity, {ref.size} points; dashed ±{band * 100:g} %", fontsize=9)
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
