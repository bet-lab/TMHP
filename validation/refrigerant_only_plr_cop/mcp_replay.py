"""Export each figure's final layout so the dartwork-mpl MCP can validate it.

The MCP validator runs in its own throwaway environment (``uv run --no-project
--with dartwork-mpl[mcp]``): a newer dartwork-mpl, a newer matplotlib, and no
pandas, no project code, no ``tmhp``.  Re-running ``figures.py`` there is not
possible, and re-running ``dm.simple_layout`` under a different layout engine
moves the axes, so the validator would be judging a figure nobody ships.

So we export the *finished* layout instead.  ``export`` re-runs the real
figures, and for each one records the canvas size, every axes rectangle as
laid out, and every piece of text with its font size and anchor.  ``emit``
turns one of those records into a standalone script that rebuilds the same
canvas with ``fig.add_axes`` and sets no layout of its own.

Only text and geometry are reproduced -- the data marks are not.  That is the
whole surface ``dm.validate_figure`` looks at (overflow, clipped text, tick
crowding, margins); a line or a filled band contributes no text and no extent
beyond its axes.  Legend boxes are rebuilt from dummy handles of the original
handle type, so their measured size is the shipped one.

Run::

    uv run python3 -m validation.refrigerant_only_plr_cop.mcp_replay export
    uv run python3 -m validation.refrigerant_only_plr_cop.mcp_replay emit H1
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from validation.refrigerant_only_plr_cop.simulate import OUT_DIR

REPLAY_DIR = OUT_DIR / "mcp_replay"


# ---------------------------------------------------------------------------
# capture
# ---------------------------------------------------------------------------
def _text_spec(t, ax=None) -> dict:
    """One text artist, in axes fraction when it was placed that way."""
    x, y = t.get_position()
    in_axes = ax is not None and t.get_transform() is ax.transAxes
    return {
        "x": float(x),
        "y": float(y),
        "s": t.get_text(),
        "size": float(t.get_fontsize()),
        "ha": t.get_ha(),
        "va": t.get_va(),
        "rotation": float(t.get_rotation()),
        "axes_fraction": bool(in_axes),
        "linespacing": float(getattr(t, "_linespacing", 1.2)),
        "bbox": t.get_bbox_patch() is not None,
    }


def _tick_spec(axis) -> dict:
    """Ticks as drawn.

    A ``twinx`` partner keeps a full x axis but matplotlib hides it, so the
    labels are still reachable through ``get_ticklabels``.  Recording them
    would stack a second copy of every x tick on top of the parent's in the
    replay, which reads to the validator as a cross-axes collision that the
    shipped figure does not have.
    """
    if not axis.get_visible():
        return {"ticks": [], "labels": [], "size": 7.0, "rotation": 0.0, "ha": "center"}
    labels = [t for t in axis.get_ticklabels()]
    return {
        "ticks": [float(v) for v in axis.get_ticklocs()],
        "labels": [t.get_text() for t in labels],
        "size": float(labels[0].get_fontsize()) if labels else 7.0,
        "rotation": float(labels[0].get_rotation()) if labels else 0.0,
        "ha": labels[0].get_ha() if labels else "center",
    }


def _legend_spec(leg) -> dict | None:
    if leg is None:
        return None
    kinds, labels = [], []
    for h, t in zip(leg.legend_handles, leg.get_texts()):
        kinds.append("patch" if h.__class__.__name__ in {"Rectangle", "Patch", "PolyCollection"} else "line")
        labels.append(t.get_text())
    loc = getattr(leg, "_loc", 0)
    return {
        "labels": labels,
        "kinds": kinds,
        "loc": loc if isinstance(loc, (int, str)) else list(loc),
        "size": float(leg.get_texts()[0].get_fontsize()) if leg.get_texts() else 6.0,
        "ncol": int(getattr(leg, "_ncols", 1)),
        "frameon": bool(leg.get_frame_on()),
        "handlelength": float(leg.handlelength),
        "columnspacing": float(leg.columnspacing),
        "labelspacing": float(leg.labelspacing),
    }


def capture(fig, name: str) -> dict:
    axes = []
    for ax in fig.axes:
        if not ax.get_visible():
            continue
        p = ax.get_position()
        axes.append(
            {
                "position": [float(p.x0), float(p.y0), float(p.width), float(p.height)],
                "xlim": [float(v) for v in ax.get_xlim()],
                "ylim": [float(v) for v in ax.get_ylim()],
                "x": _tick_spec(ax.xaxis),
                "y": _tick_spec(ax.yaxis),
                "ytick_side": ax.yaxis.get_ticks_position(),
                "has_content": bool(ax.lines or ax.patches or ax.collections or ax.images),
                "xlabel": ax.get_xlabel(),
                "xlabel_size": float(ax.xaxis.label.get_fontsize()),
                "ylabel": ax.get_ylabel(),
                "ylabel_size": float(ax.yaxis.label.get_fontsize()),
                "title": ax.get_title(loc="left") or ax.get_title(),
                "title_size": float(ax.title.get_fontsize()),
                "title_loc": "left" if ax.get_title(loc="left") else "center",
                "texts": [_text_spec(t, ax) for t in ax.texts],
                "legend": _legend_spec(ax.get_legend()),
            }
        )
    return {
        "name": name,
        "size_inches": [float(v) for v in fig.get_size_inches()],
        "axes": axes,
        "figure_texts": [_text_spec(t) for t in fig.texts],
    }


def export(only: list[str] | None = None) -> list[str]:
    """Re-run the real figures, recording each finished layout."""
    from validation.refrigerant_only_plr_cop import figures as F

    REPLAY_DIR.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    original_save = F._save

    # ``_save`` closes the figure after writing it, so the capture has to sit
    # inside the same call -- between the successful finalize and the close.

    def capturing_save(fig, name, **margins):
        F.layout_and_write(fig, name, **margins)  # the shipped layout, not a copy of it
        spec = capture(fig, name)
        (REPLAY_DIR / f"{name}.json").write_text(json.dumps(spec, indent=1), encoding="utf-8")
        written.append(name)
        F.plt.close(fig)

    F._save = capturing_save
    try:
        argv = ["figures"] + (["--only", *only] if only else [])
        import sys

        old = sys.argv
        sys.argv = argv
        try:
            F.main()
        finally:
            sys.argv = old
    finally:
        F._save = original_save
    return written


# ---------------------------------------------------------------------------
# emit
# ---------------------------------------------------------------------------
TEMPLATE = '''\
# Layout replay of {name} -- geometry and text only, no data marks.
# Axes rectangles are the ones dm.simple_layout produced in the project
# environment; this script sets them directly and runs no layout of its own.
import json

import dartwork_mpl as dm
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

dm.style.use("report")

SPEC = json.loads(r"""{payload}""")

fig = plt.figure(figsize=SPEC["size_inches"])
for a in SPEC["axes"]:
    ax = fig.add_axes(a["position"])
    ax.set_xlim(a["xlim"])
    ax.set_ylim(a["ylim"])
    ax.set_xticks(a["x"]["ticks"])
    ax.set_xticklabels(a["x"]["labels"], fontsize=a["x"]["size"],
                       rotation=a["x"]["rotation"], ha=a["x"]["ha"])
    ax.set_yticks(a["y"]["ticks"])
    ax.set_yticklabels(a["y"]["labels"], fontsize=a["y"]["size"],
                       rotation=a["y"]["rotation"])
    if a["has_content"]:
        # A placeholder mark inside the axes box: the shipped axes does carry
        # data, and an axes left genuinely empty reads as a finding.
        ax.plot(a["xlim"], a["ylim"], lw=0.4, alpha=0.25, color="0.5")
    if a["ytick_side"] == "right":
        ax.yaxis.tick_right()
        ax.yaxis.set_label_position("right")
    if a["xlabel"]:
        ax.set_xlabel(a["xlabel"], fontsize=a["xlabel_size"])
    if a["ylabel"]:
        ax.set_ylabel(a["ylabel"], fontsize=a["ylabel_size"])
    if a["title"]:
        ax.set_title(a["title"], fontsize=a["title_size"], loc=a["title_loc"])
    for t in a["texts"]:
        ax.text(t["x"], t["y"], t["s"], fontsize=t["size"], ha=t["ha"], va=t["va"],
                rotation=t["rotation"], linespacing=t["linespacing"],
                transform=ax.transAxes if t["axes_fraction"] else ax.transData,
                bbox={{"facecolor": "white", "edgecolor": "none", "pad": 1.5}} if t["bbox"] else None)
    lg = a["legend"]
    if lg:
        handles = [Patch() if k == "patch" else Line2D([], []) for k in lg["kinds"]]
        ax.legend(handles, lg["labels"], loc=lg["loc"], frameon=lg["frameon"],
                  fontsize=lg["size"], ncol=lg["ncol"], handlelength=lg["handlelength"],
                  columnspacing=lg["columnspacing"], labelspacing=lg["labelspacing"])
for t in SPEC["figure_texts"]:
    fig.text(t["x"], t["y"], t["s"], fontsize=t["size"], ha=t["ha"], va=t["va"])
'''


def emit(name: str) -> str:
    spec = json.loads((REPLAY_DIR / f"{name}.json").read_text(encoding="utf-8"))
    return TEMPLATE.format(name=name, payload=json.dumps(spec))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("action", choices=("export", "emit"))
    ap.add_argument("names", nargs="*")
    a = ap.parse_args()
    if a.action == "export":
        names = export(a.names or None)
        print(f"captured {len(names)} layouts into {REPLAY_DIR}")
    else:
        out_dir = REPLAY_DIR / "code"
        out_dir.mkdir(parents=True, exist_ok=True)
        for n in a.names or sorted(p.stem for p in REPLAY_DIR.glob("*.json")):
            src = emit(n)
            (out_dir / f"{n}.py").write_text(src, encoding="utf-8")
            print(f"{n}: {len(src)} chars -> {out_dir / f'{n}.py'}")


if __name__ == "__main__":
    main()
