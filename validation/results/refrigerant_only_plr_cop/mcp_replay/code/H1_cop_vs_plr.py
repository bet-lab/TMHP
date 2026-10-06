# Layout replay of H1_cop_vs_plr -- geometry and text only, no data marks.
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

SPEC = json.loads(r"""{"name": "H1_cop_vs_plr", "size_inches": [5.905511811023622, 2.4803149606299213], "axes": [{"position": [0.07169840740740739, 0.1841255511463844, 0.38810359098228664, 0.6764461864660494], "xlim": [0.0, 100.0], "ylim": [5.0, 9.0], "x": {"ticks": [0.0, 20.0, 40.0, 60.0, 80.0, 100.0], "labels": ["0", "20", "40", "60", "80", "100"], "size": 8.0, "rotation": 0.0, "ha": "center"}, "y": {"ticks": [5.0, 6.0, 7.0, 8.0, 9.0], "labels": ["5", "6", "7", "8", "9"], "size": 8.0, "rotation": 0.0, "ha": "right"}, "ytick_side": "left", "has_content": true, "xlabel": "Requested part-load ratio [%]", "xlabel_size": 8.0, "ylabel": "Compressor COP  $Q/E_{comp}$ [-]", "ylabel_size": 8.0, "title": "ASHP heating, 7 \u00b0C outdoor / 20 \u00b0C room, 3.5 kW", "title_size": 9.0, "title_loc": "left", "texts": [{"x": -0.1, "y": 1.03, "s": "a", "size": 11.0, "ha": "left", "va": "bottom", "rotation": 0.0, "axes_fraction": true, "linespacing": 1.2, "bbox": false}], "legend": {"labels": ["R32", "R410A", "R290", "R407C", "R134a", "R22"], "kinds": ["line", "line", "line", "line", "line", "line"], "loc": 3, "size": 4.5, "ncol": 2, "frameon": false, "handlelength": 1.6, "columnspacing": 1.0, "labelspacing": 0.4}}, {"position": [0.5762330756843801, 0.1841255511463844, 0.38810359098228664, 0.6764461864660494], "xlim": [0.0, 100.0], "ylim": [4.0, 8.0], "x": {"ticks": [0.0, 20.0, 40.0, 60.0, 80.0, 100.0], "labels": ["0", "20", "40", "60", "80", "100"], "size": 8.0, "rotation": 0.0, "ha": "center"}, "y": {"ticks": [4.0, 5.0, 6.0, 7.0, 8.0], "labels": ["4", "5", "6", "7", "8"], "size": 8.0, "rotation": 0.0, "ha": "right"}, "ytick_side": "left", "has_content": true, "xlabel": "Requested part-load ratio [%]", "xlabel_size": 8.0, "ylabel": "System COP  $Q/(E_{comp}+E_{fan})$ [-]", "ylabel_size": 8.0, "title": "Same rows, fan power included", "title_size": 9.0, "title_loc": "left", "texts": [{"x": -0.1, "y": 1.03, "s": "b", "size": 11.0, "ha": "left", "va": "bottom", "rotation": 0.0, "axes_fraction": true, "linespacing": 1.2, "bbox": false}], "legend": null}], "figure_texts": []}""")

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
