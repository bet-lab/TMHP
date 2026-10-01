# Layout replay of C5_power_split -- geometry and text only, no data marks.
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

SPEC = json.loads(r"""{"name": "C5_power_split", "size_inches": [6.299212598425196, 3.9055118110236218], "axes": [{"position": [0.06222309027777778, 0.6076453186133683, 0.20715688056865827, 0.3063802190210403], "xlim": [0.0, 100.0], "ylim": [0.0, 800.0], "x": {"ticks": [0.0, 25.0, 50.0, 75.0, 100.0], "labels": ["0", "25", "50", "75", "100"], "size": 4.5, "rotation": 0.0, "ha": "center"}, "y": {"ticks": [0.0, 400.0, 800.0], "labels": ["0", "400", "800"], "size": 4.5, "rotation": 0.0, "ha": "right"}, "ytick_side": "left", "has_content": true, "xlabel": "", "xlabel_size": 8.0, "ylabel": "Power [W]", "ylabel_size": 5.0, "title": "R32", "title_size": 9.0, "title_loc": "left", "texts": [], "legend": {"labels": ["$E_{comp}$", "$E_{fan}$"], "kinds": ["patch", "patch"], "loc": 4, "size": 4.0, "ncol": 1, "frameon": false, "handlelength": 1.2, "columnspacing": 0.75, "labelspacing": 0.25}}, {"position": [0.3978172367990042, 0.6076453186133683, 0.20715688056865839, 0.3063802190210403], "xlim": [0.0, 100.0], "ylim": [0.0, 800.0], "x": {"ticks": [0.0, 25.0, 50.0, 75.0, 100.0], "labels": ["0", "25", "50", "75", "100"], "size": 4.5, "rotation": 0.0, "ha": "center"}, "y": {"ticks": [0.0, 400.0, 800.0], "labels": ["0", "400", "800"], "size": 4.5, "rotation": 0.0, "ha": "right"}, "ytick_side": "left", "has_content": true, "xlabel": "", "xlabel_size": 8.0, "ylabel": "", "ylabel_size": 8.0, "title": "R410A", "title_size": 9.0, "title_loc": "left", "texts": [], "legend": null}, {"position": [0.7334113833202307, 0.6076453186133683, 0.20715688056865833, 0.3063802190210403], "xlim": [0.0, 100.0], "ylim": [0.0, 800.0], "x": {"ticks": [0.0, 25.0, 50.0, 75.0, 100.0], "labels": ["0", "25", "50", "75", "100"], "size": 4.5, "rotation": 0.0, "ha": "center"}, "y": {"ticks": [0.0, 400.0, 800.0], "labels": ["0", "400", "800"], "size": 4.5, "rotation": 0.0, "ha": "right"}, "ytick_side": "left", "has_content": true, "xlabel": "", "xlabel_size": 8.0, "ylabel": "", "ylabel_size": 8.0, "title": "R290", "title_size": 9.0, "title_loc": "left", "texts": [], "legend": null}, {"position": [0.06222309027777778, 0.11130936379928325, 0.20715688056865827, 0.3063802190210402], "xlim": [0.0, 100.0], "ylim": [0.0, 800.0], "x": {"ticks": [0.0, 25.0, 50.0, 75.0, 100.0], "labels": ["0", "25", "50", "75", "100"], "size": 4.5, "rotation": 0.0, "ha": "center"}, "y": {"ticks": [0.0, 400.0, 800.0], "labels": ["0", "400", "800"], "size": 4.5, "rotation": 0.0, "ha": "right"}, "ytick_side": "left", "has_content": true, "xlabel": "Requested PLR [%]", "xlabel_size": 5.0, "ylabel": "Power [W]", "ylabel_size": 5.0, "title": "R407C", "title_size": 9.0, "title_loc": "left", "texts": [], "legend": null}, {"position": [0.3978172367990042, 0.11130936379928325, 0.20715688056865839, 0.3063802190210402], "xlim": [0.0, 100.0], "ylim": [0.0, 800.0], "x": {"ticks": [0.0, 25.0, 50.0, 75.0, 100.0], "labels": ["0", "25", "50", "75", "100"], "size": 4.5, "rotation": 0.0, "ha": "center"}, "y": {"ticks": [0.0, 400.0, 800.0], "labels": ["0", "400", "800"], "size": 4.5, "rotation": 0.0, "ha": "right"}, "ytick_side": "left", "has_content": true, "xlabel": "Requested PLR [%]", "xlabel_size": 5.0, "ylabel": "", "ylabel_size": 8.0, "title": "R134a", "title_size": 9.0, "title_loc": "left", "texts": [], "legend": null}, {"position": [0.7334113833202307, 0.11130936379928325, 0.20715688056865833, 0.3063802190210402], "xlim": [0.0, 100.0], "ylim": [0.0, 800.0], "x": {"ticks": [0.0, 25.0, 50.0, 75.0, 100.0], "labels": ["0", "25", "50", "75", "100"], "size": 4.5, "rotation": 0.0, "ha": "center"}, "y": {"ticks": [0.0, 400.0, 800.0], "labels": ["0", "400", "800"], "size": 4.5, "rotation": 0.0, "ha": "right"}, "ytick_side": "left", "has_content": true, "xlabel": "Requested PLR [%]", "xlabel_size": 5.0, "ylabel": "", "ylabel_size": 8.0, "title": "R22", "title_size": 9.0, "title_loc": "left", "texts": [], "legend": null}, {"position": [0.06222309027777778, 0.6076453186133683, 0.20715688056865827, 0.3063802190210403], "xlim": [0.0, 100.0], "ylim": [0.0, 30.0], "x": {"ticks": [], "labels": [], "size": 7.0, "rotation": 0.0, "ha": "center"}, "y": {"ticks": [0.0, 10.0, 20.0, 30.0], "labels": ["0", "10", "20", "30"], "size": 4.5, "rotation": 0.0, "ha": "left"}, "ytick_side": "right", "has_content": true, "xlabel": "", "xlabel_size": 8.0, "ylabel": "", "ylabel_size": 8.0, "title": "", "title_size": 9.0, "title_loc": "center", "texts": [], "legend": null}, {"position": [0.3978172367990042, 0.6076453186133683, 0.20715688056865839, 0.3063802190210403], "xlim": [0.0, 100.0], "ylim": [0.0, 30.0], "x": {"ticks": [], "labels": [], "size": 7.0, "rotation": 0.0, "ha": "center"}, "y": {"ticks": [0.0, 10.0, 20.0, 30.0], "labels": ["0", "10", "20", "30"], "size": 4.5, "rotation": 0.0, "ha": "left"}, "ytick_side": "right", "has_content": true, "xlabel": "", "xlabel_size": 8.0, "ylabel": "", "ylabel_size": 8.0, "title": "", "title_size": 9.0, "title_loc": "center", "texts": [], "legend": null}, {"position": [0.7334113833202307, 0.6076453186133683, 0.20715688056865833, 0.3063802190210403], "xlim": [0.0, 100.0], "ylim": [0.0, 30.0], "x": {"ticks": [], "labels": [], "size": 7.0, "rotation": 0.0, "ha": "center"}, "y": {"ticks": [0.0, 10.0, 20.0, 30.0], "labels": ["0", "10", "20", "30"], "size": 4.5, "rotation": 0.0, "ha": "left"}, "ytick_side": "right", "has_content": true, "xlabel": "", "xlabel_size": 8.0, "ylabel": "$E_{fan}/E_{tot}$ [%]", "ylabel_size": 5.0, "title": "", "title_size": 9.0, "title_loc": "center", "texts": [], "legend": null}, {"position": [0.06222309027777778, 0.11130936379928325, 0.20715688056865827, 0.3063802190210402], "xlim": [0.0, 100.0], "ylim": [0.0, 30.0], "x": {"ticks": [], "labels": [], "size": 7.0, "rotation": 0.0, "ha": "center"}, "y": {"ticks": [0.0, 10.0, 20.0, 30.0], "labels": ["0", "10", "20", "30"], "size": 4.5, "rotation": 0.0, "ha": "left"}, "ytick_side": "right", "has_content": true, "xlabel": "", "xlabel_size": 8.0, "ylabel": "", "ylabel_size": 8.0, "title": "", "title_size": 9.0, "title_loc": "center", "texts": [], "legend": null}, {"position": [0.3978172367990042, 0.11130936379928325, 0.20715688056865839, 0.3063802190210402], "xlim": [0.0, 100.0], "ylim": [0.0, 30.0], "x": {"ticks": [], "labels": [], "size": 7.0, "rotation": 0.0, "ha": "center"}, "y": {"ticks": [0.0, 10.0, 20.0, 30.0], "labels": ["0", "10", "20", "30"], "size": 4.5, "rotation": 0.0, "ha": "left"}, "ytick_side": "right", "has_content": true, "xlabel": "", "xlabel_size": 8.0, "ylabel": "", "ylabel_size": 8.0, "title": "", "title_size": 9.0, "title_loc": "center", "texts": [], "legend": null}, {"position": [0.7334113833202307, 0.11130936379928325, 0.20715688056865833, 0.3063802190210402], "xlim": [0.0, 100.0], "ylim": [0.0, 30.0], "x": {"ticks": [], "labels": [], "size": 7.0, "rotation": 0.0, "ha": "center"}, "y": {"ticks": [0.0, 10.0, 20.0, 30.0], "labels": ["0", "10", "20", "30"], "size": 4.5, "rotation": 0.0, "ha": "left"}, "ytick_side": "right", "has_content": true, "xlabel": "", "xlabel_size": 8.0, "ylabel": "$E_{fan}/E_{tot}$ [%]", "ylabel_size": 5.0, "title": "", "title_size": 9.0, "title_loc": "center", "texts": [], "legend": null}], "figure_texts": [{"x": 0.01, "y": 0.98, "s": "ASHP cooling, 35 \u00b0C outdoor / 27 \u00b0C room, 3.5 kW \u2014 shaded band: at the compressor speed floor", "size": 6.0, "ha": "left", "va": "top", "rotation": 0.0, "axes_fraction": false, "linespacing": 1.2, "bbox": false}]}""")

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
