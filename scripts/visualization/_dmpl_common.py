"""Shared dartwork-mpl bootstrap for ``scripts/visualization/`` figures.

Every docs figure goes through this module so the typography, colour
palette, and on-disk paths stay consistent across the documentation. The
public surface is intentionally tiny: ``apply_style``, ``static_path``,
``finalize`` (layout + save), and ``COLORS``.
"""

from __future__ import annotations

from pathlib import Path

import dartwork_mpl as dm
import matplotlib as mpl
import numpy as np
from matplotlib.figure import Figure

# Uniform buffer around the axes. ``margin=0`` (the dartwork default) snaps
# labels flush against the figure edge, which clips axis-edge tick labels on
# narrow figures — 2 % leaves room for the ``"-10"`` / ``"160"`` extremes
# without wasting headroom.
DEFAULT_MARGIN = "2%"

# ``dm.lw`` is a *relative* step on the active preset's base width, and under
# "report" it goes non-positive fast: lw(-1) = 0.0 and lw(-2) = -1.0.
# Matplotlib silently draws nothing for a non-positive width (and raises
# outright on a dashed line, whose dash pattern is scaled by the width and
# collapses to all zeros), so `linewidth=dm.lw(-2)` is not a thin line -- it is
# an invisible one. Sub-preset hairlines are therefore absolute widths.
HAIRLINE = 0.6  # marker edges, zero lines, reference rules
GRIDLINE = 0.4  # background grids


def finalize(
    fig: Figure,
    out_stem: Path,
    *,
    margin: str | float = DEFAULT_MARGIN,
    ml: str | float | None = None,
    mr: str | float | None = None,
    mt: str | float | None = None,
    mb: str | float | None = None,
    gutter: str | float | None = None,
    formats: tuple[str, ...] = ("svg",),
) -> None:
    """Apply the standard layout buffer and save through ``dm.save_formats``.

    ``out_stem`` is a path without extension (``dm.save_formats`` appends
    them itself). ``margin`` is forwarded to ``dm.simple_layout`` — pass a
    smaller value (e.g. ``"1%"``) for ultra-tight gallery thumbnails. The
    per-side ``ml/mr/mt/mb`` overrides forward to ``simple_layout`` for
    cases where one edge needs extra headroom (e.g. wide-aspect figures
    with ``set_title(loc="left")`` — ``simple_layout``'s measurement of
    ``_left_title`` is unreliable on those, so pass ``mt`` explicitly).

    After layout we verify nothing extends past the canvas: ``simple_layout``
    occasionally undercounts the headroom needed by left-aligned titles or
    legends, and rather than silently shipping a clipped figure we raise
    so the script fails loudly and the caller picks a margin that fits.
    """
    dm.simple_layout(fig, margin=margin, ml=ml, mr=mr, mt=mt, mb=mb, gutter=gutter)
    _assert_no_overflow(fig)
    dm.save_formats(fig, str(out_stem), formats=formats)


def _assert_no_overflow(fig: Figure, tol_in: float = 1.0 / 144.0) -> None:
    """Raise if any artist's tight bbox extends past the figure canvas.

    ``get_tightbbox()`` returns a ``TransformedBbox`` whose coordinates
    are in inches (the inverse-dpi affine sits on top of the pixel-space
    inner bbox), so we compare against ``fig.get_size_inches()`` rather
    than ``fig.bbox.size`` (pixels) to keep the units aligned. The
    default ``tol_in`` is ½ px at 72 dpi — small enough that genuine
    overflows fire while floating-point dust on a flush layout doesn't.
    """
    fig.canvas.draw()
    fw_in, fh_in = fig.get_size_inches()
    tb = fig.get_tightbbox()
    over = {
        "left": max(0.0, -tb.x0),
        "bottom": max(0.0, -tb.y0),
        "right": max(0.0, tb.x1 - fw_in),
        "top": max(0.0, tb.y1 - fh_in),
    }
    worst = max(over.values())
    if worst > tol_in:
        # Inches → display pixels for the diagnostic, so the number lines
        # up with what a user sees when they zoom into the SVG preview.
        dpi = fig.dpi
        bumps = ", ".join(f"{side}={ov * dpi:.1f}px" for side, ov in over.items() if ov > tol_in)
        side_to_kw = {"left": "ml", "bottom": "mb", "right": "mr", "top": "mt"}
        worst_side = max(over, key=over.__getitem__)
        raise RuntimeError(
            f"Figure content overflows canvas ({bumps}; "
            f"canvas={fw_in * dpi:.0f}x{fh_in * dpi:.0f}px). "
            f"Increase the corresponding side margin — e.g. pass "
            f'`{side_to_kw[worst_side]}="<larger %>"` to finalize().'
        )


def ticks(vmin: float, vmax: float, step: float) -> np.ndarray:
    """Tick positions from an explicit minimum, maximum and interval.

    Every axis in the figure set declares its own ticks through this rather
    than leaving matplotlib's locator to choose. An automatic locator picks a
    different count as the data moves, so the same plot regenerated after a
    rerun can change its gridlines without any number changing -- which makes
    figure diffs unreadable and invites the reader to compare two plots whose
    grids do not line up.

    Use a step that divides the range exactly. On a linear axis prefer an
    integer step; a log axis should keep its decade locator and not call this.
    """
    return np.arange(vmin, vmax + step * 0.5, step)


def apply_style(
    preset: str = "report",
    *,
    hashsalt: str | None = None,
    svg_fonttype: str | None = "none",
) -> None:
    """Activate a dartwork-mpl composite preset for figure scripts.

    ``preset`` defaults to ``"report"`` (larger body-text-matching type,
    looser ticks), which reads better when embedded inside the Shibuya
    theme. Use ``"scientific"`` for tighter publication figures.
    ``hashsalt`` pins matplotlib's SVG clip-path IDs so re-running a
    script produces byte-identical output — pass a unique string per
    figure.

    ``svg_fonttype`` decides how text survives the trip out of matplotlib.
    ``"none"`` (the default here) leaves glyphs as live text referencing a
    font family by name, which keeps labels editable in Figma — but the
    presets ask for Roboto Light, and a viewer without that face silently
    substitutes its own, so the published figure stops matching the preset.
    Pass ``None`` to keep whatever the preset declares (``"path"``, i.e.
    glyphs converted to outlines) when the figure has to render identically
    everywhere it is embedded.
    """
    dm.style.use(preset)
    if svg_fonttype is not None:
        mpl.rcParams["svg.fonttype"] = svg_fonttype
    if hashsalt is not None:
        mpl.rcParams["svg.hashsalt"] = hashsalt


def static_path(name: str) -> Path:
    """Resolve ``docs/source/_static/<name>`` relative to the repo root."""
    repo_root = Path(__file__).resolve().parents[2]
    out = repo_root / "docs" / "source" / "_static" / name
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


def cover_data_ylim(ax, lo: float, hi: float, step: float, *, tol: float = 0.01, max_ticks: int = 9):
    """Widen a fixed y range, in whole steps of ``step``, to cover every point the axes' lines draw.

    A figure that fixes its range to the data it was first drawn with clips
    the curves once the model moves. This keeps the fixed range whenever the
    data fit (an overshoot within ``tol`` of the span is ignored, so a ratio
    of 1.0000001 does not add a step), and otherwise extends it outward to
    the next multiple of ``step``, coarsening the step (x2, x2.5, x5) while
    the axis would carry more than ``max_ticks`` ticks. Two-point constant
    lines (reference rules) are ignored. Sets the limits and ticks and
    returns ``(lo, hi, step)``.
    """
    import math

    ys = []
    for line in ax.get_lines():
        x = np.asarray(line.get_xdata(), dtype=float)
        y = np.asarray(line.get_ydata(), dtype=float)
        if y.size == 2 and (np.allclose(y, y[0]) or np.allclose(x, x[0])):
            continue
        ys.append(y[np.isfinite(y)])
    data = np.concatenate(ys) if ys else np.array([])
    margin = tol * (hi - lo)
    new_lo, new_hi = lo, hi
    if data.size and data.min() < lo - margin:
        new_lo = math.floor(data.min() / step) * step
    if data.size and data.max() > hi + margin:
        new_hi = math.ceil(data.max() / step) * step
    if (new_lo, new_hi) != (lo, hi):
        for factor in (1.0, 2.0, 2.5, 5.0, 10.0):
            s = step * factor
            a, b = math.floor(new_lo / s + 1e-9) * s, math.ceil(new_hi / s - 1e-9) * s
            if round((b - a) / s) + 1 <= max_ticks:
                break
        lo, hi, step = a, b, s
    ax.set_ylim(lo, hi)
    ax.set_yticks(ticks(lo, hi, step))
    return lo, hi, step


def record_figure(name: str, script: str, model_settings: dict, question: str, data: dict) -> Path:
    """Write ``docs/source/_static/provenance/<name>.json`` for a figure drawn from the model.

    The record names the script, the commit and source trees it ran on, the
    model settings the figure chose, and ``data``: the plotted values, so
    ``tests/test_study_results_current.py`` can recompute a few of them and
    say when the figure no longer shows what the model does.
    """
    import sys

    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from validation import study_provenance

    path = static_path(f"provenance/{name}.json")
    study_provenance.write(
        path,
        script,
        [static_path(f"{name}.svg")],
        model_settings,
        question,
        command=f"python {script}",
        extra={"data": data},
    )
    return path


# Named tokens used across the docs figures. Keeping them here means every
# figure shares the same accent, refrigerant phase colours, and dim grey,
# which makes the gallery read as a single design system instead of seven
# unrelated plots.
COLORS = {
    "accent": "oc.indigo6",  # primary line / scatter
    "accent2": "oc.violet5",  # secondary series
    "accent3": "oc.pink6",  # tertiary series -- kept clear of accent/accent2,
    # which are both blue-violet (hue 228 deg / 256 deg) and read as the same
    # colour once a categorical set needs more than four entries.
    "warm": "oc.orange6",  # ambient air / warm-side process
    "cool": "oc.blue5",  # sat. liquid / cold-side process
    "hot": "oc.red5",  # sat. vapour / discharge
    "ink": "oc.gray8",  # node markers, axis ink
    "muted": "oc.gray5",  # grids, secondary annotations
    "band20": "oc.gray3",  # ±20 % band
    "band10": "oc.blue2",  # ±10 % band
    "ess": "oc.teal6",  # ESS / storage series
    "pv": "oc.yellow6",  # PV irradiance / generation
    "load": "oc.grape6",  # load / demand
}
