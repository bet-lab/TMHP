# Layout replay of B4_efficiency_surface -- geometry and text only, no data marks.
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

SPEC = json.loads(r"""{"name": "B4_efficiency_surface", "size_inches": [6.299212598425196, 2.141732283464567], "axes": [{"position": [0.07203913194444445, 0.1923043300653594, 0.22113631076388884, 0.7276956699346406], "xlim": [0.225, 2.8418023270256403], "ylim": [2.6475072789369305, 4.791894933873937], "x": {"ticks": [0.5, 1.0, 1.5, 2.0, 2.5], "labels": ["0.5", "1.0", "1.5", "2.0", "2.5"], "size": 4.5, "rotation": 0.0, "ha": "center"}, "y": {"ticks": [3.0, 3.5, 4.0, 4.5], "labels": ["3.0", "3.5", "4.0", "4.5"], "size": 4.5, "rotation": 0.0, "ha": "right"}, "ytick_side": "left", "has_content": true, "xlabel": "$n^{*}$ [-]", "xlabel_size": 8.0, "ylabel": "$r_p$ [-]", "ylabel_size": 8.0, "title": "", "title_size": 9.0, "title_loc": "center", "texts": [{"x": 0.97, "y": 0.96, "s": "Volumetric  $\\eta_v$", "size": 5.0, "ha": "right", "va": "top", "rotation": 0.0, "axes_fraction": true, "linespacing": 1.2, "bbox": true}], "legend": {"labels": ["R32", "R410A", "R290", "R407C", "R134a", "R22"], "kinds": ["line", "line", "line", "line", "line", "line"], "loc": 4, "size": 4.0, "ncol": 2, "frameon": false, "handlelength": 1.2, "columnspacing": 0.8, "labelspacing": 0.25}}, {"position": [0.39412897588315216, 0.1923043300653594, 0.2211363107638888, 0.7276956699346406], "xlim": [0.225, 2.8418023270256403], "ylim": [2.6475072789369305, 4.791894933873937], "x": {"ticks": [0.5, 1.0, 1.5, 2.0, 2.5], "labels": ["0.5", "1.0", "1.5", "2.0", "2.5"], "size": 4.5, "rotation": 0.0, "ha": "center"}, "y": {"ticks": [3.0, 3.5, 4.0, 4.5], "labels": ["3.0", "3.5", "4.0", "4.5"], "size": 4.5, "rotation": 0.0, "ha": "right"}, "ytick_side": "left", "has_content": true, "xlabel": "$n^{*}$ [-]", "xlabel_size": 8.0, "ylabel": "", "ylabel_size": 8.0, "title": "", "title_size": 9.0, "title_loc": "center", "texts": [{"x": 0.97, "y": 0.96, "s": "Isentropic  $\\eta_{is}$", "size": 5.0, "ha": "right", "va": "top", "rotation": 0.0, "axes_fraction": true, "linespacing": 1.2, "bbox": true}], "legend": null}, {"position": [0.7162188198218598, 0.1923043300653594, 0.22113631076388884, 0.7276956699346406], "xlim": [0.225, 2.8418023270256403], "ylim": [2.6475072789369305, 4.791894933873937], "x": {"ticks": [0.5, 1.0, 1.5, 2.0, 2.5], "labels": ["0.5", "1.0", "1.5", "2.0", "2.5"], "size": 4.5, "rotation": 0.0, "ha": "center"}, "y": {"ticks": [3.0, 3.5, 4.0, 4.5], "labels": ["3.0", "3.5", "4.0", "4.5"], "size": 4.5, "rotation": 0.0, "ha": "right"}, "ytick_side": "left", "has_content": true, "xlabel": "$n^{*}$ [-]", "xlabel_size": 8.0, "ylabel": "", "ylabel_size": 8.0, "title": "", "title_size": 9.0, "title_loc": "center", "texts": [{"x": 0.97, "y": 0.96, "s": "Electro-mechanical  $\\eta_{em}$", "size": 5.0, "ha": "right", "va": "top", "rotation": 0.0, "axes_fraction": true, "linespacing": 1.2, "bbox": true}], "legend": null}, {"position": [0.30038640936367755, 0.20267340741776918, 0.012018277758906959, 0.7069575152298211], "xlim": [0.0, 1.0], "ylim": [0.8200000000000001, 0.9600000000000001], "x": {"ticks": [], "labels": [], "size": 7.0, "rotation": 0.0, "ha": "center"}, "y": {"ticks": [0.85, 0.9, 0.95], "labels": ["0.85", "0.90", "0.95"], "size": 4.0, "rotation": 0.0, "ha": "left"}, "ytick_side": "right", "has_content": true, "xlabel": "", "xlabel_size": 8.0, "ylabel": "", "ylabel_size": 8.0, "title": "", "title_size": 9.0, "title_loc": "center", "texts": [], "legend": null}, {"position": [0.6224762533023852, 0.20267340741776918, 0.012018277758906959, 0.7069575152298211], "xlim": [0.0, 1.0], "ylim": [0.48, 0.74], "x": {"ticks": [], "labels": [], "size": 7.0, "rotation": 0.0, "ha": "center"}, "y": {"ticks": [0.5, 0.55, 0.6, 0.65, 0.7], "labels": ["0.50", "0.55", "0.60", "0.65", "0.70"], "size": 4.0, "rotation": 0.0, "ha": "left"}, "ytick_side": "right", "has_content": true, "xlabel": "", "xlabel_size": 8.0, "ylabel": "", "ylabel_size": 8.0, "title": "", "title_size": 9.0, "title_loc": "center", "texts": [], "legend": null}, {"position": [0.9445660972410929, 0.20267340741776918, 0.012018277758906959, 0.7069575152298211], "xlim": [0.0, 1.0], "ylim": [0.864, 0.96], "x": {"ticks": [], "labels": [], "size": 7.0, "rotation": 0.0, "ha": "center"}, "y": {"ticks": [0.88, 0.9, 0.92, 0.94], "labels": ["0.88", "0.90", "0.92", "0.94"], "size": 4.0, "rotation": 0.0, "ha": "left"}, "ytick_side": "right", "has_content": true, "xlabel": "", "xlabel_size": 8.0, "ylabel": "", "ylabel_size": 8.0, "title": "", "title_size": 9.0, "title_loc": "center", "texts": [], "legend": null}], "figure_texts": [{"x": 0.01, "y": 0.98, "s": "ASHPB heating, 7 \u00b0C outdoor / 42.5 \u00b0C tank, 9 kW \u2014 hatched: outside the compressor-map fitting domain", "size": 6.0, "ha": "left", "va": "top", "rotation": 0.0, "axes_fraction": false, "linespacing": 1.2, "bbox": false}]}""")

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
