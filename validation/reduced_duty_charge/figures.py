"""Figures for issue #227: why reduced-duty charging delivers more heat in the hour although it runs slower.

R32 15 kW ASHPB (the #226 example): one hour from 50 degC, set points 60-62 degC, 15 degC outdoor.

- ``figures/reduce_duty_time.png``: tank and condensing temperature, and the heat rate, with
  ``condensing_limit_control="stop"`` and ``"reduce_duty"``.
- ``figures/reduce_duty_th.png``: T-h cycles where the nameplate duty first meets the
  condensing limit (tank 57.9 degC) and on the limit at the end of the charge (tank 62 degC).
- ``figures/timeseries.csv``: the two runs the first figure draws.
- ``figures/reduce_duty_window.png``: the duty against the tank temperature, where the stretch on
  the limit starts (the nameplate duty) and ends (the speed floor's duty at -7, 15 and 35 degC).

Run from the repository root::

    uv run python -m validation.reduced_duty_charge.figures
"""

import warnings
from pathlib import Path

import CoolProp.CoolProp as CP
import dartwork_mpl as dm
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scripts.visualization._dmpl_common import HAIRLINE, finalize  # noqa: E402

import tmhp  # noqa: E402
from validation.compressor_maps.ossorio_te import figures as base  # noqa: E402  (scientific preset)

warnings.simplefilter("ignore")
# No mathtext in any label: a $...$ string is set in the math font, which has no Hangul.
TXT_SEC, TXT_HL = "#1D4ED8", "#9A3412"  # darker text shades of C_SECONDARY / C_HIGHLIGHT

# ── config ─────────────────────────────────────────────────────────────
ISSUE = dict(ref="R32", hp_capacity=15000.0, T_tank_w_lower_bound=60.0, T_tank_w_upper_bound=62.0)
T0, T_INIT, DT, HORIZON = 15.0, 50.0, 5, 3600  # [degC], [degC], [s], [s]
T_MAX_MIN = 14.0  # x range of panel a [min]
T_A, T_B = 57.9, 62.0  # tank temperatures of the two cycles in panel b [degC]
SIZE_A, SIZE_B = ("13cm", "9.5cm"), ("13cm", "8.6cm")
OUT = Path(__file__).parent / "figures"
FS = base.LEGEND_FS
C_STOP, C_RED = base.C_SECONDARY, base.C_HIGHLIGHT
# ═════════════════════════════════════════════════════════════════════


def run(control):
    m = tmhp.AirSourceHeatPumpBoiler(**ISSUE, condensing_limit_control=control)
    n = HORIZON // DT
    df = m.analyze_dynamic(HORIZON, DT, T_INIT, np.zeros(n), np.full(n, T0))
    return m, df


ms, stop = run("stop")
mr, red = run("reduce_duty")
t_min = (stop["time [s]"].to_numpy() + DT) / 60.0
kwh = {k: float((d["Q_ref_tank [W]"].fillna(0) * DT).sum() / 3.6e6) for k, d in (("stop", stop), ("red", red))}
ekwh = {k: float((d["E_cmp [W]"].fillna(0) * DT).sum() / 3.6e6) for k, d in (("stop", stop), ("red", red))}
run_s = {k: float((d["runtime_fraction [-]"].fillna(0) * DT).sum()) for k, d in (("stop", stop), ("red", red))}
print("heat kWh", kwh, "elec kWh", ekwh, "run s", run_s)
print("T max", stop["T_tank_w [°C]"].max(), red["T_tank_w [°C]"].max())
OUT.mkdir(parents=True, exist_ok=True)
cols = [
    "time [s]",
    "T_tank_w [°C]",
    "Q_ref_tank [W]",
    "E_cmp [W]",
    "T_ref_cond_sat_l [°C]",
    "runtime_fraction [-]",
    "reduced_duty_fraction [-]",
]
pd.concat(
    {"stop": stop.reindex(columns=cols), "reduce_duty": red.reindex(columns=cols)}, names=["condensing_limit_control"]
).to_csv(OUT / "timeseries.csv", float_format="%.6g")

# ── a: time series ────────────────────────────────────────────────────
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=dm.figsize(*SIZE_A), sharex=True, gridspec_kw=dict(hspace=0.12))
ax1.plot(t_min, stop["T_tank_w [°C]"], color=C_STOP, lw=1.0, label="정지 방식(기본): 탱크 물")
ax1.plot(t_min, red["T_tank_w [°C]"], color=C_RED, lw=1.0, label="감속 충전: 탱크 물")
on = red["runtime_fraction [-]"].fillna(0).to_numpy() > 0.5
Tc = red["T_ref_cond_sat_l [°C]"].to_numpy(dtype=float)
ax1.plot(t_min[on], Tc[on], color=C_RED, lw=1.0, ls="--", label="감속 충전: 냉매 응축온도")
ax1.axhline(65.0, color=base.C_INK, lw=HAIRLINE, ls=":")
ax1.text(0.2, 65.4, "냉매 온도 상한 65 °C", fontsize=FS, color=base.C_INK)
ax1.axhline(57.99, color=C_STOP, lw=HAIRLINE, ls=":")
ax1.text(T_MAX_MIN - 0.1, 57.99 + 0.45, "정지 방식이 멈추는 온도 57.99 °C", fontsize=FS, color=TXT_SEC, ha="right")
ax1.set_ylim(48, 68)
ax1.set_ylabel("온도 [°C]")
ax1.legend(loc="lower right", fontsize=FS, frameon=False, bbox_to_anchor=(1.0, 0.18))

qs, qr = stop["Q_ref_tank [W]"].fillna(0) / 1e3, red["Q_ref_tank [W]"].fillna(0) / 1e3
ax2.fill_between(t_min, 0, qs, step="pre", color=C_STOP, alpha=0.22, lw=0)
extra = np.where(qr > qs + 1e-9, qr, 0.0)
ax2.fill_between(t_min, 0, extra, step="pre", color=C_RED, alpha=0.30, lw=0)
ax2.step(t_min, qs, where="pre", color=C_STOP, lw=1.0)
ax2.step(t_min, qr, where="pre", color=C_RED, lw=1.0)
ax2.text(
    2.8,
    7.0,
    f"두 방식 공통\n{kwh['stop']:.2f} kWh\n(물 50 → 58 °C)",
    fontsize=FS,
    color=TXT_SEC,
    ha="center",
    va="center",
)
ax2.text(
    11.6,
    5.0,
    f"감속 충전만\n+{kwh['red'] - kwh['stop']:.2f} kWh\n(물 58 → 62 °C)",
    fontsize=FS,
    color=TXT_HL,
    ha="center",
    va="center",
)
ax2.annotate("", xy=(8.0, 4.0), xytext=(10.4, 5.0), arrowprops=dict(arrowstyle="-", color=C_RED, lw=HAIRLINE))
ax2.annotate(
    "출력이 15 kW에서\n줄어들며 계속 운전",
    xy=(7.0, 11.2),
    xytext=(10.2, 12.6),
    fontsize=FS,
    color=TXT_HL,
    arrowprops=dict(arrowstyle="-", color=C_RED, lw=HAIRLINE),
    va="center",
)
ax2.annotate(
    "정지 방식은 여기서 끔",
    xy=(run_s["stop"] / 60, 15.0),
    xytext=(6.4, 16.6),
    fontsize=FS,
    color=TXT_SEC,
    arrowprops=dict(arrowstyle="-", color=C_STOP, lw=HAIRLINE),
    va="center",
)
ax2.set_ylim(0, 18.5)
ax2.set_xlim(0, T_MAX_MIN)
ax2.set_ylabel("물에 넣는 열 [kW]")
ax2.set_xlabel("충전 시작 뒤 시간 [분]  (외기 15 °C, 설정 60–62 °C, 1시간 중 앞 14분)")
finalize(fig, OUT / "reduce_duty_time", formats=("png",))

# ── b: T-h cycles ─────────────────────────────────────────────────────
FL = "R32"


def sat(Tc_deg, q):
    return CP.PropsSI("H", "T", Tc_deg + 273.15, "Q", q, FL) / 1e3


def cycle(T_tank):
    r = mr.analyze_steady(T_tank_w=T_tank, T0=T0, Q_ref_tank=15000.0)
    p_c, p_e = r["P_ref_cmp_out [Pa]"], r["P_ref_cmp_in [Pa]"]
    h1, h2 = r["h_ref_cmp_in [J/kg]"] / 1e3, r["h_ref_cmp_out [J/kg]"] / 1e3
    hgv, hgl = r["h_ref_cond_sat_v [J/kg]"] / 1e3, r["h_ref_cond_sat_l [J/kg]"] / 1e3
    h3, h4 = r["h_ref_exp_in [J/kg]"] / 1e3, r["h_ref_exp_out [J/kg]"] / 1e3
    hev = r["h_ref_evap_sat [J/kg]"] / 1e3
    Tcv, Tcl = r["T_ref_cond_sat_v [°C]"], r["T_ref_cond_sat_l [°C]"]
    Tev = r["T_ref_evap_sat [°C]"]
    hh = np.linspace(h2, hgv, 30)
    TT = [CP.PropsSI("T", "P", p_c, "H", h * 1e3, FL) - 273.15 for h in hh]
    hs = np.linspace(hgl, h3, 10)
    Ts = [CP.PropsSI("T", "P", p_c, "H", h * 1e3, FL) - 273.15 for h in hs]
    he = np.linspace(hev, h1, 10)
    Te = [CP.PropsSI("T", "P", p_e, "H", h * 1e3, FL) - 273.15 for h in he]
    H = [h1, h2, *hh, hgv, hgl, *hs, h3, h4, hev, *he]
    T = [r["T_ref_cmp_in [°C]"], r["T_ref_cmp_out [°C]"], *TT, Tcv, Tcl, *Ts, r["T_ref_exp_in [°C]"], Tev, Tev, *Te]
    return r, H, T


rA, HA, TA = cycle(T_A)
rB, HB, TB = cycle(T_B)
for k, r in (("A", rA), ("B", rB)):
    print(
        k,
        {
            x: round(float(r[x]), 3)
            for x in (
                "Q_ref_tank [W]",
                "m_dot_ref [kg/s]",
                "cmp_rpm [rpm]",
                "T_ref_cond_sat_l [°C]",
                "T_ref_evap_sat [°C]",
                "E_cmp [W]",
                "cop_ref [-]",
            )
        },
        r["capacity_binding"],
    )

fig, (ax, az) = plt.subplots(1, 2, figsize=dm.figsize(*SIZE_B), gridspec_kw=dict(width_ratios=(1.25, 1), wspace=0.28))
Tcrit = CP.PropsSI("Tcrit", FL) - 273.15
Td = np.linspace(-20, Tcrit - 0.05, 300)
hc = CP.PropsSI("H", "T", Tcrit + 273.15, "D", CP.PropsSI("rhocrit", FL), FL) / 1e3
dome = ([sat(t, 0) for t in Td] + [hc] + [sat(t, 1) for t in Td[::-1]], list(Td) + [Tcrit] + list(Td[::-1]))
for a in (ax, az):
    a.plot(*dome, color=base.C_INK, lw=HAIRLINE)
    a.plot(
        HA,
        TA,
        color=C_STOP,
        lw=1.1,
        label=f"상한에 처음 닿을 때 (탱크 {T_A:.1f} °C, {rA['Q_ref_tank [W]'] / 1e3:.1f} kW)",
    )
    a.plot(
        HB,
        TB,
        color=C_RED,
        lw=1.1,
        ls="--",
        label=f"감속 충전 끝무렵 (탱크 {T_B:.0f} °C, {rB['Q_ref_tank [W]'] / 1e3:.1f} kW)",
    )
ax.annotate(
    "응축: 물에 열을 줌",
    xy=(380, 65.5),
    xytext=(228, 96),
    fontsize=FS,
    color=base.C_INK,
    arrowprops=dict(arrowstyle="-", color=base.C_INK, lw=HAIRLINE),
)
ax.text(228, -10, "증발: 바깥 공기에서 열을 얻음", fontsize=FS, color=base.C_INK)
ax.text(
    560,
    32,
    f"냉매 유량\n{rA['m_dot_ref [kg/s]'] * 1e3:.0f} → {rB['m_dot_ref [kg/s]'] * 1e3:.0f} g/s\n"
    f"압축기 회전수\n{rA['cmp_rpm [rpm]'] / 60:.0f} → {rB['cmp_rpm [rpm]'] / 60:.0f} rev/s",
    fontsize=FS,
    color=base.C_INK,
    va="top",
)
ax.add_patch(plt.Rectangle((335, 54), 160, 14, fill=False, ec=base.C_INK, lw=HAIRLINE, ls=":"))
ax.text(415, 50, "오른쪽 확대", fontsize=FS, color=base.C_INK, ha="center", va="top")
ax.set_xlim(220, 640)
ax.set_ylim(-15, 128)
ax.set_xlabel("비엔탈피 h [kJ/kg]")
ax.set_ylabel("온도 T [°C]")
ax.legend(loc="upper left", fontsize=FS, frameon=False, bbox_to_anchor=(0.0, 1.13))

h_w0, h_w1 = 340.0, 490.0
Tc_A, Tc_B = rA["T_ref_cond_sat_l [°C]"], rB["T_ref_cond_sat_l [°C]"]
az.plot([h_w0, h_w1], [T_A, T_A], color=C_STOP, lw=1.6, ls=":")
az.plot([h_w0, h_w1], [T_B, T_B], color=C_RED, lw=1.6, ls=":")
az.text(h_w0 + 2, T_A - 0.9, f"탱크 물 {T_A:.1f} °C", fontsize=FS, color=TXT_SEC)
az.text(h_w0 + 2, T_B - 0.9, f"탱크 물 {T_B:.0f} °C", fontsize=FS, color=TXT_HL)
az.text(h_w0 + 2, 65.5, "냉매 65 °C (상한)", fontsize=FS, color=base.C_INK)
xA, xB = 445.0, 470.0
az.annotate(
    "", xy=(xA, Tc_A), xytext=(xA, T_A), arrowprops=dict(arrowstyle="<->", color=C_STOP, lw=0.9, shrinkA=0, shrinkB=0)
)
az.annotate(
    "", xy=(xB, Tc_B), xytext=(xB, T_B), arrowprops=dict(arrowstyle="<->", color=C_RED, lw=0.9, shrinkA=0, shrinkB=0)
)
az.text(
    xA - 3, (Tc_A + T_A) / 2 - 1.8, f"{Tc_A - T_A:.0f} K\n→ 15 kW", fontsize=FS, color=TXT_SEC, ha="right", va="center"
)
az.text(
    xB + 3, (Tc_B + T_B) / 2 - 2.6, f"{Tc_B - T_B:.0f} K\n→ 6.4 kW", fontsize=FS, color=TXT_HL, ha="left", va="center"
)
az.set_xlim(335, 495)
az.set_ylim(54, 68)
az.set_xlabel("비엔탈피 h [kJ/kg]")
az.set_title("온도차가 줄면 넘어가는 열도 준다", loc="left", fontsize=FS)
finalize(fig, OUT / "reduce_duty_th", formats=("png",))

# ── c: where reduced-duty charging starts and ends ───────────────────
from tmhp.compressor_envelope import (  # noqa: E402
    condensing_tank_cutout,
    condensing_tank_reach,
    smallest_duty_on_condensing_limit,
)

T0_WINDOW = (-7.0, 15.0, 35.0)  # outdoor temperatures whose stop is marked [degC]
SIZE_C = ("13cm", "7.6cm")
T_reach, K = condensing_tank_reach(mr), float(mr.K_tank_hx)
T_knee = condensing_tank_cutout(mr)
Q_cap = float(mr.hp_capacity)
stops = {}
for T0w in T0_WINDOW:
    q_min = smallest_duty_on_condensing_limit(mr, mr._frost_free_steady_floor(T0w))
    stops[T0w] = (q_min, T_reach - q_min / K)
    print(f"window: T0 {T0w:g}  Q_min {q_min / 1e3:.2f} kW  T_stop {T_reach - q_min / K:.2f}")

fig, ax = plt.subplots(figsize=dm.figsize(*SIZE_C))
Tt = np.linspace(50.0, T_knee, 20)
ax.plot(Tt, np.full_like(Tt, Q_cap / 1e3), color=base.C_INK, lw=1.1)
T_line = np.linspace(T_knee, T_reach, 50)
ax.plot(T_line, K * (T_reach - T_line) / 1e3, color=C_RED, lw=1.1)
ax.text(51.0, Q_cap / 1e3 + 0.6, f"정격 열량 {Q_cap / 1e3:.0f} kW로 충전", fontsize=FS, color=base.C_INK)
ax.annotate(
    f"감속 시작: 탱크 {T_knee:.2f} °C\n(정격 열량에서 응축온도가 {T_reach:.0f} °C 상한에 닿음)",
    xy=(T_knee, Q_cap / 1e3),
    xytext=(51.0, 9.5),
    fontsize=FS,
    color=TXT_HL,
    arrowprops=dict(arrowstyle="-", color=C_RED, lw=HAIRLINE),
)
ax.text(
    59.4,
    13.5,
    f"상한을 따라 감속\n열량 = {K / 1e3:.2f} kW/K × ({T_reach:.0f} °C − 탱크)",
    fontsize=FS,
    color=TXT_HL,
)
for T0w, (q_min, T_stop) in stops.items():
    ax.plot([T_stop], [q_min / 1e3], "o", ms=4, color=C_RED, mec="white", mew=0.5, zorder=4)
    ax.plot([50.0, T_stop], [q_min / 1e3, q_min / 1e3], color=C_RED, lw=HAIRLINE, ls=":")
    ax.text(
        50.2,
        q_min / 1e3 + 0.25,
        f"외기 {T0w:g} °C: 회전수 하한 15 rev/s의 열량 {q_min / 1e3:.1f} kW".replace("-", "−"),
        fontsize=FS,
        color=TXT_HL,
    )
ax.annotate(
    "감속 끝(정지): 하한 열량이 상한에 닿는 탱크 온도\n외기 −7 / 15 / 35 °C에서 "
    + " / ".join(f"{stops[t][1]:.1f}" for t in T0_WINDOW)
    + " °C",
    xy=(stops[15.0][1], stops[15.0][0] / 1e3),
    xytext=(57.2, 1.0),
    fontsize=FS,
    color=TXT_HL,
    arrowprops=dict(arrowstyle="-", color=C_RED, lw=HAIRLINE),
)
ax.axvline(T_reach, color=base.C_INK, lw=HAIRLINE, ls=":")
ax.set_xlim(50, 66)
ax.set_ylim(0, 17.5)
ax.set_xlabel("탱크 물 온도 [°C]")
ax.set_ylabel("물에 넣는 열 [kW]")
finalize(fig, OUT / "reduce_duty_window", formats=("png",))
