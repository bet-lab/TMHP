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
from matplotlib.figure import Figure

# Uniform buffer around the axes. ``margin=0`` (the dartwork default) snaps
# labels flush against the figure edge, which clips axis-edge tick labels on
# narrow figures — 2 % leaves room for the ``"-10"`` / ``"160"`` extremes
# without wasting headroom.
DEFAULT_MARGIN = "2%"


def finalize(fig: Figure, out_stem: Path, *, margin: str | float = DEFAULT_MARGIN,
             ml: str | float | None = None,
             mr: str | float | None = None,
             mt: str | float | None = None,
             mb: str | float | None = None,
             formats: tuple[str, ...] = ("svg",)) -> None:
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
    dm.simple_layout(fig, margin=margin, ml=ml, mr=mr, mt=mt, mb=mb)
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
        "left":   max(0.0, -tb.x0),
        "bottom": max(0.0, -tb.y0),
        "right":  max(0.0, tb.x1 - fw_in),
        "top":    max(0.0, tb.y1 - fh_in),
    }
    worst = max(over.values())
    if worst > tol_in:
        # Inches → display pixels for the diagnostic, so the number lines
        # up with what a user sees when they zoom into the SVG preview.
        dpi = fig.dpi
        bumps = ", ".join(
            f"{side}={ov * dpi:.1f}px" for side, ov in over.items() if ov > tol_in
        )
        side_to_kw = {"left": "ml", "bottom": "mb", "right": "mr", "top": "mt"}
        worst_side = max(over, key=over.__getitem__)
        raise RuntimeError(
            f"Figure content overflows canvas ({bumps}; "
            f"canvas={fw_in * dpi:.0f}x{fh_in * dpi:.0f}px). "
            f"Increase the corresponding side margin — e.g. pass "
            f"`{side_to_kw[worst_side]}=\"<larger %>\"` to finalize()."
        )


def panel_letter(ax, letter: str, *, x: float = -0.10, y: float = 1.03) -> None:
    """Place a bold subplot index (e.g. ``"a"``) above ``ax``'s upper-left.

    The canonical typography for subplot indexing across the docs gallery —
    ``dm.fs(3)`` / ``dm.fw(2)`` anchored at axes-fraction ``(-0.10, 1.03)``
    with ``va="bottom"`` so the letter sits clear of the top y-tick label
    and the y-axis gutter (journal-style). Pass ``x``/``y`` to nudge the
    anchor when a particular layout needs a different gutter (e.g. shorter
    y-tick labels can take a smaller ``|x|``). The overflow guard in
    :func:`finalize` will catch any layout that doesn't leave enough
    headroom; bump ``mt``/``ml`` if it fires.
    """
    ax.text(
        x, y, letter,
        transform=ax.transAxes,
        fontsize=dm.fs(3), fontweight=dm.fw(2),
        va="bottom", ha="left",
    )


def apply_style(preset: str = "report", *, hashsalt: str | None = None) -> None:
    """Activate a dartwork-mpl composite preset for figure scripts.

    ``preset`` defaults to ``"report"`` (larger body-text-matching type,
    looser ticks), which reads better when embedded inside the Shibuya
    theme. Use ``"scientific"`` for tighter publication figures.
    ``hashsalt`` pins matplotlib's SVG clip-path IDs so re-running a
    script produces byte-identical output — pass a unique string per
    figure.
    """
    dm.style.use(preset)
    if hashsalt is not None:
        mpl.rcParams["svg.hashsalt"] = hashsalt


def static_path(name: str) -> Path:
    """Resolve ``docs/source/_static/<name>`` relative to the repo root."""
    repo_root = Path(__file__).resolve().parents[2]
    out = repo_root / "docs" / "source" / "_static" / name
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


# Named tokens used across the docs figures. Keeping them here means every
# figure shares the same accent, refrigerant phase colours, and dim grey,
# which makes the gallery read as a single design system instead of seven
# unrelated plots.
COLORS = {
    "accent":   "oc.indigo6",   # primary line / scatter
    "accent2":  "oc.violet5",   # secondary series
    "warm":     "oc.orange6",   # ambient air / warm-side process
    "cool":     "oc.blue5",     # sat. liquid / cold-side process
    "hot":      "oc.red5",      # sat. vapour / discharge
    "ink":      "oc.gray8",     # node markers, axis ink
    "muted":    "oc.gray5",     # grids, secondary annotations
    "band20":   "oc.gray3",     # ±20 % band
    "band10":   "oc.blue2",     # ±10 % band
    "ess":      "oc.teal6",     # ESS / storage series
    "pv":       "oc.yellow6",   # PV irradiance / generation
    "load":     "oc.grape6",    # load / demand
}
