# ruff: noqa
# Kept as the source of figures/cycle_use.{svg,png,pdf} of the archive. It runs inside the betlab Figure System
# kit (Figure System core 2.0.0), not in this repository: copy it into a kit's figures/concept/src/ and run
# `uv run python src/MP2_cycle_use.py && uv run python tools/render.py --pdf MP2`.
"""MP2 — How the cycle uses the three default compressor maps (eta_v, eta_ov, f_loss) at one operating point (#218).

User request 2026-11-21: MP1 ends at the fitted maps; this figure shows what each map sets in the cycle.
Source of every relation: src/tmhp/compressor_efficiency.py (module docstring) and the cycle evaluation of
src/tmhp/air_source_heat_pump.py / air_source_heat_pump_boiler.py (_residual_rps and the state after it) in bet-lab/TMHP
at 76c35ff1:
  eta_cmp_vol = eta_v, eta_cmp = 1 - f_loss, eta_cmp_isen = eta_ov / (1 - f_loss)
  m_dot = V_cmp_ref rho_1 eta_cmp_vol N
  h_2 = h_1 + (h_2s - h_1) / eta_cmp_isen
  E_cmp = m_dot (h_2 - h_1) / eta_cmp
  the speed N is solved so that m_dot (h_2 - h_3) (heating; cooling m_dot (h_1 - h_3)) meets the request,
  and the efficiencies are evaluated at (PR, N, T_e) of that cycle state (SuctionState(T_evap)).

Full width (1044 u), one source and labelled edges, rows chosen so that no edge crosses another (merges are dots):
  rows 0-2  the maps f_loss, eta_ov, eta_v -> what each sets in the compressor (eta_cmp, h_2, m_dot)
  row 3     CoolProp states rho_1, h_1, h_2s, h_3, merged into rows 1-2 and into the balance
  right     the cycle balance (Q, E_cmp, T_dis, COP); accent: the speed loop back to the operating point
f_loss sits above eta_ov so that its branch into eta_cmp_isen = eta_ov / (1 - f_loss) runs one row.
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
X1, W1 = M, 140  # the operating point (one tall source node)
EDGE_W = 120  # room for the edge labels between the source and the maps
X2, W2 = X1 + W1 + EDGE_W, 160  # maps, CoolProp states
X3, W3 = X2 + W2 + GAP_FLOW, 270  # what each map sets in the compressor
X4 = X3 + W3 + GAP_FLOW  # the cycle balance
W4 = W - M - X4

# ── rows ──────────────────────────────────────────────────────────────────────────────────────
HEAD = M + CAP * T_BLOCK
SUBHEAD = HEAD + GAP_PAIR + CAP * T_SMALL
TOP = SUBHEAD + GAP_BLOCK
RH, RG = NODE_M_MIN_H + GAP_TIGHT, GAP_GROUP  # two-line node height (subscripts keep PAD_MIN), row gap
ROW_Y = [TOP + k * (RH + RG) for k in range(4)]
ROW_C = [y + RH / 2 for y in ROW_Y]
BOTTOM = ROW_Y[3] + RH
LOOP_LABEL = BOTTOM + GAP_BLOCK + CAP * T_SMALL  # the loop's label sits above its line
LOOP_Y = LOOP_LABEL + GAP_GROUP  # the speed loop runs under every node
BLOCK_SHIFT = 0.0  # vertical nudge of the balance text block [u] (keeps its baselines off the rows')

c = Canvas(W, 10)


def head(x, w, s, sub=None, anchor="middle"):
    xa = x + w / 2 if anchor == "middle" else x
    c.text(xa, HEAD, s, T_BLOCK, W_SEMI, INK, anchor)
    if sub:
        c.text(xa, SUBHEAD, sub, T_SMALL, W_REG, INK3, anchor)


def arrow(pts, accent=False):
    c.arrow(pts, stroke=ACC if accent else INK2, sw=S_EMPH if accent else S_ARROW, clear=ARROW_CLEAR, radius=R_NODE)


def line(pts, accent=False):
    d = "M " + " L ".join(f"{x:.2f} {y:.2f}" for x, y in pts)
    c.path(d, stroke=ACC if accent else INK2, sw=S_EMPH if accent else S_ARROW)


def dot(x, y, accent=False):
    c.circle(x, y, 3, fill=ACC if accent else INK2)


def edge_label(x, y, s, accent=False):
    """The quantities an edge carries, just above it."""
    lift = GAP_BLOCK + 2 if "_" in s else GAP_GROUP  # a subscript run drops: lift its baseline more
    c.text(x, y - lift, s, T_SMALL, W_MED, ACC_TEXT if accent else INK3, rich=True)


# ── headers ───────────────────────────────────────────────────────────────────────────────────
with c.group("headers"):
    head(X1, W1, "Cycle state", "solver trial", anchor="start")
    head(X2, W2, "Default maps", "and CoolProp")
    head(X3, W3, "Compressor")
    head(X4, W4, "Cycle balance")

# ── the source: the operating point the solver is trying ──────────────────────────────────────
with c.group("operating-point"):
    c.node(X1, ROW_Y[0], W1, BOTTOM - ROW_Y[0], "operating point", "T_e, T_c, N", role="neutral")

# ── maps (rows 0-2) and states (row 3), fed from the operating point ──────────────────────────
MAPS = ["f_{loss}(PR, N)", "η_{ov}(PR, N, T_e)", "η_v(PR, N, T_e)"]
CARRY = ["PR, N", "PR, N, T_e", "PR, N, T_e", "T_e, T_c"]
with c.group("maps"):
    for k in range(3):
        c.node(X2, ROW_Y[k], W2, RH, MAPS[k], role="neutral", align="start")
    c.node(X2, ROW_Y[3], W2, RH, "ρ_1, h_1, h_3, h_{2s}", "CoolProp", role="neutral", align="start")
    for k in range(4):
        edge_label(X1 + W1 + GAP_GROUP, ROW_C[k], CARRY[k])
        arrow([(X1 + W1, ROW_C[k]), (X2, ROW_C[k])])

# ── what each map sets in the compressor (rows 0-2) ───────────────────────────────────────────
SETS = [
    ("η_{cmp} = 1 − f_{loss}", "loss that never reaches the gas"),
    ("h_2 = h_1 + (h_{2s} − h_1) / η_{isen}", "with η_{isen} = η_{ov} / (1 − f_{loss})"),
    ("ṁ = ρ_1 V N η_v", "V: displacement per revolution"),
]
X_FL = X3 - 20  # f_loss also enters eta_isen
X_ST = X3 - 10  # the states enter h_2 and m_dot, and the balance
with c.group("compressor"):
    for k, (label, sub) in enumerate(SETS):
        c.node(X3, ROW_Y[k], W3, RH, label, sub, role="neutral", align="start")
        arrow([(X2 + W2, ROW_C[k]), (X3, ROW_C[k])])
    line([(X_FL, ROW_C[0]), (X_FL, ROW_C[1])])
    dot(X_FL, ROW_C[0])
    dot(X_FL, ROW_C[1])
    line([(X2 + W2 + ARROW_CLEAR, ROW_C[3]), (X_ST, ROW_C[3]), (X_ST, ROW_C[1])])
    for k in (1, 2, 3):
        dot(X_ST, ROW_C[k])
    edge_label(X3 + 2 * GAP_GROUP, ROW_C[3], "h_1, h_3")
    arrow([(X_ST, ROW_C[3]), (X4, ROW_C[3])])

# ── the cycle balance (one node, rows 0-3) ────────────────────────────────────────────────────
BALANCE = [
    ("Q = ṁ(h_2 − h_3)", T_NODE, W_MED, INK),
    ("cooling: Q = ṁ(h_1 − h_3)", T_SMALL, W_REG, INK3),
    ("E_{cmp} = ṁ (h_2 − h_1) / η_{cmp}", T_NODE, W_MED, INK),
    ("T_{dis}(p_c, h_2)", T_NODE, W_MED, INK),
    ("COP = Q / (E_{cmp} + E_{aux})", T_NODE, W_MED, INK),
]
with c.group("balance"):
    c.node(X4, ROW_Y[0], W4, BOTTOM - ROW_Y[0], role="neutral")
    for k in range(3):
        arrow([(X3 + W3, ROW_C[k]), (X4, ROW_C[k])])
    # main lines one line height apart, a note line GAP_PAIR under its main line; the block centred in the node
    ys, y = [], 0.0
    for k, (_, size, _, _) in enumerate(BALANCE):
        if k:
            y += (GAP_PAIR + CAP * size) if size == T_SMALL else (GAP_SECTION + CAP * size)
        ys.append(y)
    y0 = ROW_Y[0] + (BOTTOM - ROW_Y[0] - ys[-1]) / 2 + CAP * T_NODE / 2 + BLOCK_SHIFT
    for (s, size, weight, color), dy in zip(BALANCE, ys):
        c.text(X4 + NODE_M_PAD_X, y0 + dy, s, size, weight, color, rich=True)

# ── the speed loop (accent): N is changed until Q meets the request ───────────────────────────
with c.group("speed-loop"):
    xb, xs = X4 + W4 / 2, X1 + W1 / 2
    line([(xb, BOTTOM), (xb, LOOP_Y - R_NODE)], accent=True)
    c.path(
        f"M {xb:.2f} {LOOP_Y - R_NODE:.2f} Q {xb:.2f} {LOOP_Y:.2f} {xb - R_NODE:.2f} {LOOP_Y:.2f} "
        f"L {xs + R_NODE:.2f} {LOOP_Y:.2f} Q {xs:.2f} {LOOP_Y:.2f} {xs:.2f} {LOOP_Y - R_NODE:.2f}",
        stroke=ACC,
        sw=S_EMPH,
    )
    arrow([(xs, LOOP_Y - R_NODE), (xs, BOTTOM)], accent=True)
    c.text((xs + xb) / 2, LOOP_LABEL, "N is changed until Q meets the request", T_SMALL, W_MED, ACC_TEXT, "middle")

c.height = LOOP_Y + S_EMPH / 2 + M
save(
    c,
    dict(
        id="MP2-cycle-use",
        family="MP",
        title="How the cycle uses the three compressor maps",
        concept=(
            "At the operating point the solver tries, the maps give f_loss, eta_ov and eta_v; eta_v sets the mass "
            "flow, eta_ov with f_loss sets the discharge enthalpy, and 1 - f_loss sets the electrical input; the "
            "balance gives Q, E_cmp, T_dis and COP, and the speed is changed until Q meets the request."
        ),
        use="paper",
        width_note="full width (1044 u)",
    ),
)
