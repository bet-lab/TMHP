"""Render validation and two conference figures from recorded CSVs only."""

from pathlib import Path

import dartwork_mpl as dm
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.colors import ListedColormap

dm.style.use("scientific")

# ═══ 사용자 조정 설정: 크기·서체·색상은 이곳에서 변경 ═══════════════
WIDTH_CM = 13.0  # 검증 그림 가로 길이
HEIGHT_CM = 8.0  # 검증 그림 세로 길이
PAPER_WIDTH_CM = 8.0  # 학회 원고의 2열 그림 한 칸 너비
PAPER_HEIGHT_CM = 4.3  # 학회 원고의 그림 높이
FONT_SIZE = 9  # 검증 그림 기본 글꼴 크기 (pt)
PAPER_FONT_SIZE = 7  # 한 페이지 원고용 글꼴 크기 (pt)
LINE_WIDTH = 1.4  # 선 두께 (pt)
MARKER_SIZE = 3  # 부하별 표식 크기 (pt)
DPI = 600  # PNG 출력 해상도
BASELINE = "#475569"  # 정유량 기준선
PRIMARY = "#14B8A6"  # 최적 유량
SECONDARY = "#2563EB"  # 두 번째 물리량
HIGHLIGHT = "#F97316"  # 제한 또는 세 번째 부하
# ══════════════════════════════════════════════════════════════════

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "validation/gshp_ground_flow/results"
OUT = Path(__file__).resolve().parent / "output"
OUT.mkdir(exist_ok=True)
plt.rcParams.update(
    {
        "font.family": "DejaVu Sans",
        "font.size": FONT_SIZE,
        "axes.labelsize": FONT_SIZE,
        "xtick.labelsize": FONT_SIZE - 1,
        "ytick.labelsize": FONT_SIZE - 1,
        "legend.fontsize": FONT_SIZE - 1,
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "savefig.facecolor": "white",
        "text.usetex": False,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "lines.linewidth": LINE_WIDTH,
    }
)


def axes(n=1, paper=False):
    w, h = (PAPER_WIDTH_CM, PAPER_HEIGHT_CM) if paper else (WIDTH_CM, HEIGHT_CM)
    return plt.subplots(1, n, figsize=(w / 2.54, h / 2.54), layout="constrained", squeeze=False)


def save(fig, name):
    for ext in ("pdf", "png"):
        fig.savefig(OUT / f"fig_{name}.{ext}", dpi=DPI)
    plt.close(fig)


def label_panel(ax, letter):
    ax.text(-0.18, 1.06, letter, transform=ax.transAxes, weight="bold", fontsize=10, va="bottom")


def main():
    norm = pd.read_csv(DATA / "normalization.csv")
    comp = pd.read_csv(DATA / "components.csv")
    ua = pd.read_csv(DATA / "ua_map.csv")
    points = pd.read_csv(DATA / "operating_points.csv")
    baseline = points[points.kind == "constant"].sort_values("plr")
    optimum = points[points.kind == "optimal"].sort_values("plr")
    scan = points[points.kind == "flow_scan"].sort_values(["plr", "prescribed_ratio"])
    fig, axs = axes()
    ax = axs[0, 0]
    ax.plot(norm.n_boreholes, norm.linear_load_W_m, "o-", color=PRIMARY, label="Corrected: Q / (N H)")
    ax.axhline(norm.linear_load_W_m.iloc[0], color=BASELINE, ls="--", label="Previous: Q / H")
    ax.set(xlabel="Number of parallel boreholes", ylabel="Linear heat extraction (W/m)", xticks=norm.n_boreholes)
    ax.legend(frameon=False)
    save(fig, "01_normalization")

    fig, axs = axes()
    ax = axs[0, 0]
    for (depth, data), color, ls in zip(
        comp.groupby("depth_m"), (PRIMARY, SECONDARY, HIGHLIGHT), ("-", "--", ":"), strict=True
    ):
        ax.plot(data.ratio, data.pump_ratio, color=color, ls=ls, label=f"H = {depth:g} m")
    ax.text(0.03, 0.94, "Curves coincide after normalization", transform=ax.transAxes, va="top", fontsize=FONT_SIZE - 1)
    ax.set(xlabel="Field flow / rated flow", ylabel="Pump power / rated pump power")
    ax.legend(frameon=False, loc="lower right")
    save(fig, "02_pump")

    fig, axs = axes()
    ax = axs[0, 0]
    for (depth, data), color in zip(comp.groupby("depth_m"), (PRIMARY, SECONDARY, HIGHLIGHT), strict=True):
        ax.plot(data.ratio, data.Rb_mK_W, color=color, label=f"H = {depth:g} m")
    ax.set(xlabel="Borehole flow / rated borehole flow", ylabel="Effective borehole resistance (m K/W)")
    ax.legend(frameon=False)
    save(fig, "03_resistance")

    fig, axs = axes()
    ax = axs[0, 0]
    matrix = ua.pivot(index="refrigerant_ratio", columns="water_ratio", values="UA_ratio")
    mesh = ax.pcolormesh(matrix.columns, matrix.index, matrix, cmap="cividis", shading="nearest")
    ax.plot(1, 1, "+", color="white", ms=8)
    ax.set(xlabel="Water flow / rated water flow", ylabel="Refrigerant flow / rated refrigerant flow")
    fig.colorbar(mesh, ax=ax, label="UA / rated UA")
    save(fig, "04_ua_map")

    fig, axs = axes(2)
    for ax, key, title, letter in zip(
        axs[0], ("cop_ref [-]", "cop_sys [-]"), ("Compressor COP", "System COP"), ("a", "b"), strict=True
    ):
        for data, color, name, marker in ((baseline, BASELINE, "Constant", "s"), (optimum, PRIMARY, "Optimal", "o")):
            ax.plot(data.plr, data[key], marker=marker, ms=MARKER_SIZE, color=color, label=name)
        ax.set(xlabel="PLR", ylabel=title)
        label_panel(ax, letter)
    axs[0, 0].legend(frameon=False)
    save(fig, "05_cop")

    fig, axs = axes()
    ax = axs[0, 0]
    for p, color in zip((0.3, 0.6, 1.0), (PRIMARY, SECONDARY, HIGHLIGHT), strict=True):
        data = scan[scan.plr == p]
        ax.plot(
            data.prescribed_ratio, data["E_cmp_plus_pmp [W]"].where(data.converged), color=color, label=f"PLR {p:.1f}"
        )
        opt = optimum[optimum.plr == p].iloc[0]
        ax.plot(opt.ground_flow_ratio, opt["E_cmp_plus_pmp [W]"], "*", ms=10, color=color)
    ax.set(xlabel="Field flow / rated flow", ylabel="Compressor + pump power (W)")
    ax.legend(frameon=False)
    save(fig, "06_objective")

    fig, axs = axes()
    ax = axs[0, 0]
    ax.plot(optimum.plr, optimum.ground_flow_ratio, "o-", color=PRIMARY, label="Optimal")
    ax.axhline(1, ls="--", color=BASELINE, label="Constant")
    ax.axhspan(0.2, 1.2, color=BASELINE, alpha=0.07)
    ax.set(xlabel="PLR", ylabel="Field flow / rated flow", ylim=(0.15, 1.25))
    ax.legend(frameon=False)
    save(fig, "07_optimal_flow")

    fig, axs = axes()
    ax = axs[0, 0]
    matrix = scan.pivot(index="plr", columns="prescribed_ratio", values="converged").astype(int)
    mesh = ax.pcolormesh(
        matrix.columns,
        matrix.index,
        matrix,
        cmap=ListedColormap([HIGHLIGHT, PRIMARY]),
        vmin=0,
        vmax=1,
        shading="nearest",
    )
    ax.plot(optimum.ground_flow_ratio, optimum.plr, "o-", color="black", ms=MARKER_SIZE, label="Selected flow")
    ax.set(xlabel="Field flow / rated flow", ylabel="PLR")
    cb = fig.colorbar(mesh, ax=ax, ticks=[0.25, 0.75])
    cb.ax.set_yticklabels(["Infeasible", "Feasible"])
    ax.legend(frameon=False)
    save(fig, "08_feasibility")

    stress_path = DATA / "hx_stress.csv"
    if stress_path.exists():
        stress = pd.read_csv(stress_path)
        fig, axs = axes()
        ax = axs[0, 0]
        matrix = stress.pivot(index="plr", columns="prescribed_ratio", values="converged").astype(int)
        mesh = ax.pcolormesh(
            matrix.columns,
            matrix.index,
            matrix,
            cmap=ListedColormap([HIGHLIGHT, PRIMARY]),
            vmin=0,
            vmax=1,
            shading="nearest",
        )
        ax.set(xlabel="Field flow / rated flow", ylabel="PLR", title="Undersized ground HX: rated UA = 400 W/K")
        cb = fig.colorbar(mesh, ax=ax, ticks=[0.25, 0.75])
        cb.ax.set_yticklabels(["Infeasible", "Feasible"])
        save(fig, "09_hx_stress")

    plt.rcParams.update(
        {
            "font.size": PAPER_FONT_SIZE,
            "axes.labelsize": PAPER_FONT_SIZE,
            "xtick.labelsize": PAPER_FONT_SIZE - 1,
            "ytick.labelsize": PAPER_FONT_SIZE - 1,
            "legend.fontsize": PAPER_FONT_SIZE - 1,
        }
    )
    fig, axs = axes(2, paper=True)
    ax, bx = axs[0]
    ax.plot(optimum.plr, optimum.ground_flow_ratio, "o-", color=PRIMARY, ms=MARKER_SIZE, label="Flow")
    ax.plot(
        optimum.plr,
        optimum["E_pmp [W]"].to_numpy() / baseline["E_pmp [W]"].to_numpy(),
        "s--",
        color=SECONDARY,
        ms=MARKER_SIZE,
        label="Pump",
    )
    ax.axhline(1, color=BASELINE, ls=":")
    ax.set(xlabel="PLR", ylabel="Ratio to constant", ylim=(0, 1.08), xticks=[0.3, 0.6, 1.0])
    ax.legend(frameon=False, loc="lower right", handlelength=1.2)
    bx.plot(
        optimum.plr,
        optimum["UA_ground [W/K]"].to_numpy() / baseline["UA_ground [W/K]"].to_numpy(),
        "o-",
        color=PRIMARY,
        ms=MARKER_SIZE,
        label="UA",
    )
    bx.plot(
        optimum.plr,
        optimum["R_b_eff [mK/W]"].to_numpy() / baseline["R_b_eff [mK/W]"].to_numpy(),
        "s--",
        color=SECONDARY,
        ms=MARKER_SIZE,
        label="Rb*",
    )
    bx.axhline(1, color=BASELINE, ls=":")
    bx.set(xlabel="PLR", ylabel="Ratio to constant", xticks=[0.3, 0.6, 1.0])
    bx.legend(frameon=False, handlelength=1.2)
    label_panel(ax, "a")
    label_panel(bx, "b")
    save(fig, "paper_1_behavior")

    fig, axs = axes(2, paper=True)
    for ax, key, title, letter in zip(
        axs[0], ("cop_ref [-]", "cop_sys [-]"), ("Compressor COP", "System COP"), ("a", "b"), strict=True
    ):
        ax.plot(baseline.plr, baseline[key], "s--", color=BASELINE, ms=MARKER_SIZE, label="Constant")
        ax.plot(optimum.plr, optimum[key], "o-", color=PRIMARY, ms=MARKER_SIZE, label="Optimal")
        ax.set(xlabel="PLR", ylabel=title, xticks=[0.3, 0.6, 1.0])
        label_panel(ax, letter)
    axs[0, 0].legend(frameon=False, handlelength=1.2)
    save(fig, "paper_2_cop")


if __name__ == "__main__":
    main()
