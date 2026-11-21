# ruff: noqa
# Kept as the source of figures/map_pipeline.{svg,png,pdf} of the archive. It runs inside the betlab Figure System
# kit (Figure System core 2.0.0), not in this repository: copy it into a kit's figures/concept/src/ and run
# `uv run python src/MP1_map_pipeline.py && uv run python tools/render.py --pdf MP1`.
"""MP1 — How the 134 measured points of one R290 scroll become TMHP's default efficiency maps (#218, #223).

The input is the test table itself (Ossorio's thesis, Appendix 1: the 134 tests of Compressor A in Ossorio &
Navarro-Peris 2023, measured on an EN 13771 bench with a Coriolis meter and two wattmeters), not a performance-curve
equation. The 2024 correlations (Eqs. 10/17/22) were fitted to the same tests and fed the earlier PR-N quadratic of #90;
they are not on this path (2026-11-21, user question).

Full width (1044 u), one source and labelled edges, rows chosen so that no edge crosses another (merges are dots):
  rows 0-2  per-point efficiencies f_loss, eta_ov, eta_v (fit targets) -> fitted maps
  row 3     CoolProp states rho_1, h_1, h_2s, h_2 (from T_e, T_c, T_suc, T_dis), merged into rows 0-2
  rows 4-6  the axes: PR -> ln PR, N -> N/70, T_e -> T_e/10
The map rows run f_loss, eta_ov, eta_v so that the T_e term (accent) reaches eta_v and eta_ov and stops short of
f_loss. How the cycle uses the three maps is MP2 (the column "read by the cycle" was taken out, user 2026-11-21).
Independent review 2026-11-21: v1 wired eta_ov to eta_cmp, fed t to f_loss and left the pressures out of CoolProp.

Source of every value: validation/coefficients/ossorio-r290-te-2026-11-21/ (README, coefficients.json),
validation/compressor_maps/ossorio_te/fit.py and src/tmhp/compressor_efficiency.py in bet-lab/TMHP at 76c35ff1.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from svgkit import *  # noqa: F403
from svgkit import CAP, Canvas, rich_width, token  # noqa: F401

M = MARGIN
W = PAPER_W
ACC = HIGHLIGHT
ACC_TEXT = HIGHLIGHT_TEXT

# ── columns ───────────────────────────────────────────────────────────────────────────────────
X1, W1 = M, 150  # the measured table (one tall source node)
EDGE_W = 168  # room for the edge labels between the source and the first boxes
X2, W2 = X1 + W1 + EDGE_W, 160  # CoolProp states, pressure ratio
X3, W3 = X2 + W2 + GAP_FLOW, 236  # per-point efficiencies, axis terms
X4 = X3 + W3 + GAP_FLOW  # fitted maps (the last column: how the cycle uses them is MP2)
W4 = W - M - X4

# ── rows ──────────────────────────────────────────────────────────────────────────────────────
HEAD = M + CAP * T_BLOCK
SUBHEAD = HEAD + GAP_PAIR + CAP * T_SMALL
TOP = SUBHEAD + GAP_BLOCK
RH, RG = NODE_M_MIN_H + GAP_TIGHT, GAP_GROUP  # two-line node height (subscripts keep PAD_MIN), row gap
SH = NODE_S_MIN_H + GAP_GROUP  # one-line node height (a subscript's ink keeps PAD_MIN to the box)
ROW_Y = [TOP + k * (RH + RG) for k in range(3)]
ROW_C = [y + RH / 2 for y in ROW_Y]  # rows 0-2 centres
STATES_C = ROW_Y[2] + RH + GAP_SECTION + SH / 2  # row 3
AX_C = [STATES_C + (k + 1) * (SH + GAP_GROUP) for k in range(3)]  # rows 4-6: PR, N, T_e
BOTTOM = AX_C[2] + SH / 2
AX_W = 120  # axis-term boxes
X_AX = X3 + (W3 - AX_W) / 2

c = Canvas(W, 10)


def head(x, w, s, sub=None, anchor="middle"):
    xa = x + w / 2 if anchor == "middle" else x
    c.text(xa, HEAD, s, T_BLOCK, W_SEMI, INK, anchor)
    if sub:
        c.text(xa, SUBHEAD, sub, T_SMALL, W_REG, INK3, anchor)


def box(x, yc, w, s, accent=False, align="center"):
    """One-line node centred on yc; the accent marks its left edge."""
    c.node(x, yc - SH / 2, w, SH, s, role="neutral", label_size=T_SMALL, align=align)
    if accent:
        c.rect(x, yc - SH / 2, 4, SH, fill=ACC, r=0)


def arrow(pts, accent=False):
    c.arrow(pts, stroke=ACC if accent else INK2, sw=S_EMPH if accent else S_ARROW, clear=ARROW_CLEAR, radius=R_NODE)


def line(pts, accent=False):
    d = "M " + " L ".join(f"{x:.2f} {y:.2f}" for x, y in pts)
    c.path(d, stroke=ACC if accent else INK2, sw=S_EMPH if accent else S_ARROW)


def dot(x, y, accent=False):
    c.circle(x, y, 3, fill=ACC if accent else INK2)


def edge_label(y, s, accent=False):
    """The quantities an edge carries, just above it, by the source."""
    lift = GAP_BLOCK + 2 if "_" in s else GAP_GROUP  # a subscript run drops: lift its baseline more
    c.text(X1 + W1 + GAP_GROUP, y - lift, s, T_SMALL, W_MED, ACC_TEXT if accent else INK3, rich=True)


# ── headers ───────────────────────────────────────────────────────────────────────────────────
with c.group("headers"):
    head(X1, W1, "Measured", "Ossorio thesis, App. 1", anchor="start")
    head(X2, W2, "CoolProp")
    head(X3, W3, "Per point")
    head(X4, W4, "Fitted maps", "least squares")

# ── the source: one measured table ────────────────────────────────────────────────────────────
with c.group("measured"):
    c.node(X1, ROW_Y[0], W1, BOTTOM - ROW_Y[0], "134 test points", "R290 scroll", role="neutral")

# ── per-point efficiencies (rows 0-2) and the states they read (row 3) ──────────────────────
EFF = ["f_{loss} = 1 − ṁ(h_2 − h_1)/W", "η_{ov} = ṁ(h_{2s} − h_1)/W", "η_v = ṁ/(ρ_1 V N)"]
CARRY = ["ṁ, W", "ṁ, W", "ṁ, N"]
X_MERGE = X3 - GAP_FLOW / 2  # where the states join the measured flow, power and speed
with c.group("targets"):
    for k in range(3):
        box(X3, ROW_C[k], W3, EFF[k])
        edge_label(ROW_C[k], CARRY[k])
        arrow([(X1 + W1, ROW_C[k]), (X3, ROW_C[k])])
        dot(X_MERGE, ROW_C[k])
    box(X2, STATES_C, W2, "ρ_1, h_1, h_{2s}, h_2")
    edge_label(STATES_C, "T_e, T_c, T_{suc}, T_{dis}")
    arrow([(X1 + W1, STATES_C), (X2, STATES_C)])
    c.path(
        f"M {X2 + W2 + ARROW_CLEAR:.2f} {STATES_C:.2f} L {X_MERGE - R_NODE:.2f} {STATES_C:.2f} "
        f"Q {X_MERGE:.2f} {STATES_C:.2f} {X_MERGE:.2f} {STATES_C - R_NODE:.2f} L {X_MERGE:.2f} {ROW_C[0]:.2f}",
        stroke=INK2,
        sw=S_ARROW,
    )

# ── the axes (rows 4-6) ─────────────────────────────────────────────────────────────────────────
with c.group("axes"):
    box(X2, AX_C[0], W2, "PR = p_c / p_e")
    edge_label(AX_C[0], "T_e, T_c")
    arrow([(X1 + W1, AX_C[0]), (X2, AX_C[0])])
    box(X_AX, AX_C[0], AX_W, "ln PR")
    arrow([(X2 + W2, AX_C[0]), (X_AX, AX_C[0])])
    box(X_AX, AX_C[1], AX_W, "N / 70")
    edge_label(AX_C[1], "N")
    arrow([(X1 + W1, AX_C[1]), (X_AX, AX_C[1])])
    box(X_AX, AX_C[2], AX_W, "T_e / 10", accent=True)
    edge_label(AX_C[2], "T_e", accent=True)
    arrow([(X1 + W1, AX_C[2]), (X_AX, AX_C[2])], accent=True)

# ── fitted maps (rows 0-2): targets from the left, axes from below ──────────────────────────────
MAPS = [
    ("f_{loss}(PR, N)", "CV RMSE 0.015", False),
    ("η_{ov}(PR, N, T_e)", "CV RMSE 0.022", True),
    ("η_v(PR, N, T_e)", "CV RMSE 0.008", True),
]
X_AXN = X4 - 22  # bus of ln PR and N/70: every map
X_AXT = X4 - 12  # bus of T_e/10: the two maps that carry it
with c.group("maps"):
    for k, (label, sub, with_t) in enumerate(MAPS):
        c.node(X4, ROW_Y[k], W4, RH, label, sub, role="neutral")
        if with_t:
            c.rect(X4, ROW_Y[k], 4, RH, fill=ACC, r=0)
        arrow([(X3 + W3, ROW_C[k]), (X4, ROW_C[k])])
    xr = X_AX + AX_W  # right edge of the axis-term boxes
    line([(xr + ARROW_CLEAR, AX_C[0]), (X_AXN, AX_C[0])])
    line([(xr + ARROW_CLEAR, AX_C[1]), (X_AXN, AX_C[1])])
    line([(X_AXN, AX_C[1]), (X_AXN, ROW_C[0])])
    for k in range(3):
        dot(X_AXN, ROW_C[k])
    line([(xr + ARROW_CLEAR, AX_C[2]), (X_AXT, AX_C[2]), (X_AXT, ROW_C[1])], accent=True)
    for k in (1, 2):
        dot(X_AXT, ROW_C[k], accent=True)

c.height = BOTTOM + M
save(
    c,
    dict(
        id="MP1-map-pipeline",
        family="MP",
        title="From 134 measured points to the default efficiency maps",
        concept=(
            "One measured table: flow, power and speed with CoolProp states give three efficiencies per point; "
            "pressures, speed and evaporating temperature give the axes; least squares joins them into f_loss in PR "
            "and N and eta_ov, eta_v in PR, N and T_e."
        ),
        use="paper",
        width_note="full width (1044 u)",
    ),
)
