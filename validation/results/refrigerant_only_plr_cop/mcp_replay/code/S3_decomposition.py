# Layout replay of S3_decomposition -- geometry and text only, no data marks.
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

SPEC = json.loads(r"""{"name": "S3_decomposition", "size_inches": [6.299212598425196, 2.2677165354330704], "axes": [{"position": [0.07702322048611113, 0.19669982156635812, 0.244036374866453, 0.6930108265817898], "xlim": [0.0, 100.0], "ylim": [0.875, 1.375], "x": {"ticks": [0.0, 20.0, 40.0, 60.0, 80.0, 100.0], "labels": ["0", "20", "40", "60", "80", "100"], "size": 8.0, "rotation": 0.0, "ha": "center"}, "y": {"ticks": [0.875, 1.0, 1.125, 1.25, 1.375], "labels": ["0.875", "1.000", "1.125", "1.250", "1.375"], "size": 4.5, "rotation": 0.0, "ha": "right"}, "ytick_side": "left", "has_content": true, "xlabel": "Requested part-load ratio [%]", "xlabel_size": 8.0, "ylabel": "Value / value at rated [-]", "ylabel_size": 8.0, "title": "H  ASHP heating", "title_size": 9.0, "title_loc": "left", "texts": [], "legend": {"labels": ["R32", "R410A", "R290", "R407C", "R134a", "R22"], "kinds": ["line", "line", "line", "line", "line", "line"], "loc": 3, "size": 4.0, "ncol": 2, "frameon": false, "handlelength": 1.2, "columnspacing": 0.8, "labelspacing": 0.25}}, {"position": [0.3991512353098291, 0.19669982156635812, 0.2440363748664529, 0.6930108265817898], "xlim": [0.0, 100.0], "ylim": [0.9, 1.4000000000000001], "x": {"ticks": [0.0, 20.0, 40.0, 60.0, 80.0, 100.0], "labels": ["0", "20", "40", "60", "80", "100"], "size": 8.0, "rotation": 0.0, "ha": "center"}, "y": {"ticks": [0.9, 1.0, 1.1, 1.2, 1.2999999999999998, 1.4], "labels": ["0.9", "1.0", "1.1", "1.2", "1.3", "1.4"], "size": 4.5, "rotation": 0.0, "ha": "right"}, "ytick_side": "left", "has_content": true, "xlabel": "Requested part-load ratio [%]", "xlabel_size": 8.0, "ylabel": "", "ylabel_size": 8.0, "title": "C  ASHP cooling", "title_size": 9.0, "title_loc": "left", "texts": [], "legend": null}, {"position": [0.721279250133547, 0.19669982156635812, 0.24403637486645302, 0.6930108265817898], "xlim": [0.0, 100.0], "ylim": [0.9, 1.1500000000000004], "x": {"ticks": [0.0, 20.0, 40.0, 60.0, 80.0, 100.0], "labels": ["0", "20", "40", "60", "80", "100"], "size": 8.0, "rotation": 0.0, "ha": "center"}, "y": {"ticks": [0.9, 0.9500000000000001, 1.0, 1.0500000000000003, 1.1, 1.1500000000000004], "labels": ["0.90", "0.95", "1.00", "1.05", "1.10", "1.15"], "size": 4.5, "rotation": 0.0, "ha": "right"}, "ytick_side": "left", "has_content": true, "xlabel": "Requested part-load ratio [%]", "xlabel_size": 8.0, "ylabel": "", "ylabel_size": 8.0, "title": "B  ASHPB heating", "title_size": 9.0, "title_loc": "left", "texts": [], "legend": null}], "figure_texts": [{"x": 0.01, "y": 0.98, "s": "Machine sized per fluid \u2014 solid: isentropic-cycle COP $Q/(\\dot m\\,\\Delta h_{is})$;  dashed: $\\eta_{is}\\eta_{em}$", "size": 6.0, "ha": "left", "va": "top", "rotation": 0.0, "axes_fraction": false, "linespacing": 1.2, "bbox": false}]}""")

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
