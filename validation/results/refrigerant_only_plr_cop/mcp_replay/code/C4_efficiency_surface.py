# Layout replay of C4_efficiency_surface -- geometry and text only, no data marks.
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

SPEC = json.loads(r"""{"name": "C4_efficiency_surface", "size_inches": [6.299212598425196, 2.141732283464567], "axes": [{"position": [0.07210527777777778, 0.19230433006535952, 0.2198960763888889, 0.7276956699346404], "xlim": [0.1, 1.736902999272268], "ylim": [1.310929061129813, 2.465854619265122], "x": {"ticks": [0.5, 1.0, 1.5], "labels": ["0.5", "1.0", "1.5"], "size": 4.5, "rotation": 0.0, "ha": "center"}, "y": {"ticks": [1.4, 1.6, 1.8, 2.0, 2.2, 2.4], "labels": ["1.4", "1.6", "1.8", "2.0", "2.2", "2.4"], "size": 4.5, "rotation": 0.0, "ha": "right"}, "ytick_side": "left", "has_content": true, "xlabel": "$n^{*}$ [-]", "xlabel_size": 8.0, "ylabel": "$r_p$ [-]", "ylabel_size": 8.0, "title": "", "title_size": 9.0, "title_loc": "center", "texts": [{"x": 0.97, "y": 0.96, "s": "Volumetric  $\\eta_v$", "size": 5.0, "ha": "right", "va": "top", "rotation": 0.0, "axes_fraction": true, "linespacing": 1.2, "bbox": true}], "legend": {"labels": ["R32", "R410A", "R290", "R407C", "R134a", "R22"], "kinds": ["line", "line", "line", "line", "line", "line"], "loc": 4, "size": 4.0, "ncol": 2, "frameon": false, "handlelength": 1.2, "columnspacing": 0.8, "labelspacing": 0.25}}, {"position": [0.3923886933876811, 0.19230433006535952, 0.21989607638888897, 0.7276956699346404], "xlim": [0.1, 1.736902999272268], "ylim": [1.310929061129813, 2.465854619265122], "x": {"ticks": [0.5, 1.0, 1.5], "labels": ["0.5", "1.0", "1.5"], "size": 4.5, "rotation": 0.0, "ha": "center"}, "y": {"ticks": [1.4, 1.6, 1.8, 2.0, 2.2, 2.4], "labels": ["1.4", "1.6", "1.8", "2.0", "2.2", "2.4"], "size": 4.5, "rotation": 0.0, "ha": "right"}, "ytick_side": "left", "has_content": true, "xlabel": "$n^{*}$ [-]", "xlabel_size": 8.0, "ylabel": "", "ylabel_size": 8.0, "title": "", "title_size": 9.0, "title_loc": "center", "texts": [{"x": 0.97, "y": 0.96, "s": "Isentropic  $\\eta_{is}$", "size": 5.0, "ha": "right", "va": "top", "rotation": 0.0, "axes_fraction": true, "linespacing": 1.2, "bbox": true}], "legend": null}, {"position": [0.7126721089975845, 0.19230433006535952, 0.21989607638888886, 0.7276956699346404], "xlim": [0.1, 1.736902999272268], "ylim": [1.310929061129813, 2.465854619265122], "x": {"ticks": [0.5, 1.0, 1.5], "labels": ["0.5", "1.0", "1.5"], "size": 4.5, "rotation": 0.0, "ha": "center"}, "y": {"ticks": [1.4, 1.6, 1.8, 2.0, 2.2, 2.4], "labels": ["1.4", "1.6", "1.8", "2.0", "2.2", "2.4"], "size": 4.5, "rotation": 0.0, "ha": "right"}, "ytick_side": "left", "has_content": true, "xlabel": "$n^{*}$ [-]", "xlabel_size": 8.0, "ylabel": "", "ylabel_size": 8.0, "title": "", "title_size": 9.0, "title_loc": "center", "texts": [{"x": 0.97, "y": 0.96, "s": "Electro-mechanical  $\\eta_{em}$", "size": 5.0, "ha": "right", "va": "top", "rotation": 0.0, "axes_fraction": true, "linespacing": 1.2, "bbox": true}], "legend": null}, {"position": [0.29917187839673914, 0.204655879244814, 0.011950873716787436, 0.7029925715757315], "xlim": [0.0, 1.0], "ylim": [0.76, 1.0], "x": {"ticks": [], "labels": [], "size": 7.0, "rotation": 0.0, "ha": "center"}, "y": {"ticks": [0.8, 0.85, 0.9, 0.95], "labels": ["0.80", "0.85", "0.90", "0.95"], "size": 4.0, "rotation": 0.0, "ha": "left"}, "ytick_side": "right", "has_content": true, "xlabel": "", "xlabel_size": 8.0, "ylabel": "", "ylabel_size": 8.0, "title": "", "title_size": 9.0, "title_loc": "center", "texts": [], "legend": null}, {"position": [0.6194552940066425, 0.20465587924481238, 0.011950873716787491, 0.7029925715757348], "xlim": [0.0, 1.0], "ylim": [0.58, 0.73], "x": {"ticks": [], "labels": [], "size": 7.0, "rotation": 0.0, "ha": "center"}, "y": {"ticks": [0.6, 0.625, 0.65, 0.675, 0.7], "labels": ["0.600", "0.625", "0.650", "0.675", "0.700"], "size": 4.0, "rotation": 0.0, "ha": "left"}, "ytick_side": "right", "has_content": true, "xlabel": "", "xlabel_size": 8.0, "ylabel": "", "ylabel_size": 8.0, "title": "", "title_size": 9.0, "title_loc": "center", "texts": [], "legend": null}, {"position": [0.9397387096165459, 0.20465587924481565, 0.01195087371678738, 0.7029925715757281], "xlim": [0.0, 1.0], "ylim": [0.7000000000000001, 0.9600000000000001], "x": {"ticks": [], "labels": [], "size": 7.0, "rotation": 0.0, "ha": "center"}, "y": {"ticks": [0.75, 0.8, 0.85, 0.9], "labels": ["0.75", "0.80", "0.85", "0.90"], "size": 4.0, "rotation": 0.0, "ha": "left"}, "ytick_side": "right", "has_content": true, "xlabel": "", "xlabel_size": 8.0, "ylabel": "", "ylabel_size": 8.0, "title": "", "title_size": 9.0, "title_loc": "center", "texts": [], "legend": null}], "figure_texts": [{"x": 0.01, "y": 0.98, "s": "ASHP cooling, 35 \u00b0C outdoor / 27 \u00b0C room, 3.5 kW \u2014 hatched: outside the compressor-map fitting domain", "size": 6.0, "ha": "left", "va": "top", "rotation": 0.0, "axes_fraction": false, "linespacing": 1.2, "bbox": false}]}""")

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
