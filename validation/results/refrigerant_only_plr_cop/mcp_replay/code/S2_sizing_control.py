# Layout replay of S2_sizing_control -- geometry and text only, no data marks.
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

SPEC = json.loads(r"""{"name": "S2_sizing_control", "size_inches": [6.299212598425196, 2.2677165354330704], "axes": [{"position": [0.07752923611111112, 0.14114879518868362, 0.25068632330246915, 0.7788512048113164], "xlim": [-0.6679999999999999, 5.668], "ylim": [1.0, 1.33], "x": {"ticks": [0.0, 1.0, 2.0, 3.0, 4.0, 5.0], "labels": ["R32", "R410A", "R290", "R407C", "R134a", "R22"], "size": 4.0, "rotation": 45.0, "ha": "right"}, "y": {"ticks": [1.0, 1.05, 1.1, 1.1500000000000001, 1.2000000000000002, 1.2500000000000002, 1.3000000000000003], "labels": ["1.00", "1.05", "1.10", "1.15", "1.20", "1.25", "1.30"], "size": 4.5, "rotation": 0.0, "ha": "right"}, "ytick_side": "left", "has_content": true, "xlabel": "", "xlabel_size": 8.0, "ylabel": "$R_{comp}$ at common low PLR [-]", "ylabel_size": 8.0, "title": "", "title_size": 9.0, "title_loc": "center", "texts": [{"x": 0.03, "y": 0.97, "s": "H  ASHP heating\nspread without R407C  0.045 $\\to$ 0.002,  $r(n^{*}_{rated})$ = +0.99", "size": 4.5, "ha": "left", "va": "top", "rotation": 0.0, "axes_fraction": true, "linespacing": 1.4, "bbox": false}], "legend": {"labels": ["same machine (R32 displacement)", "machine sized per fluid"], "kinds": ["patch", "patch"], "loc": 4, "size": 3.5, "ncol": 1, "frameon": false, "handlelength": 1.2, "columnspacing": 0.75, "labelspacing": 0.25}}, {"position": [0.40342145640432103, 0.14114879518868362, 0.2506863233024692, 0.7788512048113164], "xlim": [-0.6679999999999999, 5.668], "ylim": [1.0, 1.46], "x": {"ticks": [0.0, 1.0, 2.0, 3.0, 4.0, 5.0], "labels": ["R32", "R410A", "R290", "R407C", "R134a", "R22"], "size": 4.0, "rotation": 45.0, "ha": "right"}, "y": {"ticks": [1.0, 1.1, 1.2000000000000002, 1.3000000000000003, 1.4000000000000004], "labels": ["1.0", "1.1", "1.2", "1.3", "1.4"], "size": 4.5, "rotation": 0.0, "ha": "right"}, "ytick_side": "left", "has_content": true, "xlabel": "", "xlabel_size": 8.0, "ylabel": "", "ylabel_size": 8.0, "title": "", "title_size": 9.0, "title_loc": "center", "texts": [{"x": 0.03, "y": 0.97, "s": "C  ASHP cooling\nspread without R407C  0.019 $\\to$ 0.024,  $r(n^{*}_{rated})$ = -0.18", "size": 4.5, "ha": "left", "va": "top", "rotation": 0.0, "axes_fraction": true, "linespacing": 1.4, "bbox": false}], "legend": null}, {"position": [0.729313676697531, 0.14114879518868362, 0.250686323302469, 0.7788512048113164], "xlim": [-0.6679999999999999, 5.668], "ylim": [1.0, 1.2300000000000002], "x": {"ticks": [0.0, 1.0, 2.0, 3.0, 4.0, 5.0], "labels": ["R32", "R410A", "R290", "R407C", "R134a", "R22"], "size": 4.0, "rotation": 45.0, "ha": "right"}, "y": {"ticks": [1.0, 1.05, 1.1, 1.1500000000000001, 1.2000000000000002], "labels": ["1.00", "1.05", "1.10", "1.15", "1.20"], "size": 4.5, "rotation": 0.0, "ha": "right"}, "ytick_side": "left", "has_content": true, "xlabel": "", "xlabel_size": 8.0, "ylabel": "", "ylabel_size": 8.0, "title": "", "title_size": 9.0, "title_loc": "center", "texts": [{"x": 0.03, "y": 0.97, "s": "B  ASHPB heating\nspread without R407C  0.086 $\\to$ 0.009,  $r(n^{*}_{rated})$ = +0.99", "size": 4.5, "ha": "left", "va": "top", "rotation": 0.0, "axes_fraction": true, "linespacing": 1.4, "bbox": false}], "legend": null}], "figure_texts": [{"x": 0.01, "y": 0.98, "s": "Control: with R32's displacement the fluids separate along rated $n^{*}$;  size each machine and they merge", "size": 6.0, "ha": "left", "va": "top", "rotation": 0.0, "axes_fraction": false, "linespacing": 1.2, "bbox": false}]}""")

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
