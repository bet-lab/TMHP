# Layout replay of S1_shape_summary -- geometry and text only, no data marks.
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

SPEC = json.loads(r"""{"name": "S1_shape_summary", "size_inches": [6.299212598425196, 2.141732283464567], "axes": [{"position": [0.06746514756944445, 0.14651048902331198, 0.25633001472768413, 0.7734895109766882], "xlim": [-0.6679999999999999, 5.668], "ylim": [1.0, 1.4000000000000001], "x": {"ticks": [0.0, 1.0, 2.0, 3.0, 4.0, 5.0], "labels": ["R32", "R410A", "R290", "R407C", "R134a", "R22"], "size": 4.0, "rotation": 45.0, "ha": "right"}, "y": {"ticks": [1.0, 1.1, 1.2000000000000002, 1.3000000000000003], "labels": ["1.0", "1.1", "1.2", "1.3"], "size": 4.5, "rotation": 0.0, "ha": "right"}, "ytick_side": "left", "has_content": true, "xlabel": "", "xlabel_size": 8.0, "ylabel": "COP(low PLR) / COP(rated) [-]", "ylabel_size": 8.0, "title": "", "title_size": 9.0, "title_loc": "center", "texts": [{"x": 0.03, "y": 0.96, "s": "H  ASHP heating\nread at 28 % PLR", "size": 4.5, "ha": "left", "va": "top", "rotation": 0.0, "axes_fraction": true, "linespacing": 1.4, "bbox": false}], "legend": {"labels": ["$R_{comp}$", "$R_{sys}$"], "kinds": ["patch", "patch"], "loc": 1, "size": 4.0, "ncol": 1, "frameon": false, "handlelength": 1.2, "columnspacing": 0.75, "labelspacing": 0.4}}, {"position": [0.39556756642088015, 0.14651048902331198, 0.2563300147276841, 0.7734895109766882], "xlim": [-0.6679999999999999, 5.668], "ylim": [1.0, 1.4000000000000001], "x": {"ticks": [0.0, 1.0, 2.0, 3.0, 4.0, 5.0], "labels": ["R32", "R410A", "R290", "R407C", "R134a", "R22"], "size": 4.0, "rotation": 45.0, "ha": "right"}, "y": {"ticks": [1.0, 1.1, 1.2000000000000002, 1.3000000000000003], "labels": ["1.0", "1.1", "1.2", "1.3"], "size": 4.5, "rotation": 0.0, "ha": "right"}, "ytick_side": "left", "has_content": true, "xlabel": "", "xlabel_size": 8.0, "ylabel": "", "ylabel_size": 8.0, "title": "", "title_size": 9.0, "title_loc": "center", "texts": [{"x": 0.03, "y": 0.96, "s": "C  ASHP cooling\nread at 40 % PLR", "size": 4.5, "ha": "left", "va": "top", "rotation": 0.0, "axes_fraction": true, "linespacing": 1.4, "bbox": false}], "legend": null}, {"position": [0.7236699852723157, 0.14651048902331198, 0.25633001472768413, 0.7734895109766882], "xlim": [-0.6679999999999999, 5.668], "ylim": [1.0, 1.2000000000000002], "x": {"ticks": [0.0, 1.0, 2.0, 3.0, 4.0, 5.0], "labels": ["R32", "R410A", "R290", "R407C", "R134a", "R22"], "size": 4.0, "rotation": 45.0, "ha": "right"}, "y": {"ticks": [1.0, 1.05, 1.1, 1.1500000000000001], "labels": ["1.00", "1.05", "1.10", "1.15"], "size": 4.5, "rotation": 0.0, "ha": "right"}, "ytick_side": "left", "has_content": true, "xlabel": "", "xlabel_size": 8.0, "ylabel": "", "ylabel_size": 8.0, "title": "", "title_size": 9.0, "title_loc": "center", "texts": [{"x": 0.03, "y": 0.96, "s": "B  ASHPB heating\nread at 40 % PLR", "size": 4.5, "ha": "left", "va": "top", "rotation": 0.0, "axes_fraction": true, "linespacing": 1.4, "bbox": false}], "legend": null}], "figure_texts": [{"x": 0.01, "y": 0.98, "s": "Same machine (R32 displacement), every fluid read at the deepest PLR they all modulate at", "size": 6.0, "ha": "left", "va": "top", "rotation": 0.0, "axes_fraction": false, "linespacing": 1.2, "bbox": false}]}""")

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
                bbox={"facecolor": "white", "edgecolor": "none", "pad": 1.5} if t["bbox"] else None)
    lg = a["legend"]
    if lg:
        handles = [Patch() if k == "patch" else Line2D([], []) for k in lg["kinds"]]
        ax.legend(handles, lg["labels"], loc=lg["loc"], frameon=lg["frameon"],
                  fontsize=lg["size"], ncol=lg["ncol"], handlelength=lg["handlelength"],
                  columnspacing=lg["columnspacing"], labelspacing=lg["labelspacing"])
for t in SPEC["figure_texts"]:
    fig.text(t["x"], t["y"], t["s"], fontsize=t["size"], ha=t["ha"], va=t["va"])
