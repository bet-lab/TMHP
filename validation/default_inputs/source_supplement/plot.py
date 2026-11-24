"""Plot catalogue regression candidates and their fixed-cohort holdout errors."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import dartwork_mpl as dm
import matplotlib.pyplot as plt
import numpy as np
from validation.default_inputs.regression import predict
from validation.default_inputs.source_supplement.compare import HERE, load_rows, read_json

# ── Plot configuration ────────────────────────────────────────────────
FIGSIZE = (7.0, 3.5)  # 그림 한 장에 회귀 곡선과 동일 표본 오차를 배치
DATA_SIZE = 12  # 기존 원문 수치의 점 크기
NEW_SIZE = 22  # 보완 자료의 마커 크기
DPI = 600  # PNG 저장 해상도
AXIS_STEPS = {
    "rotary_single_fixed_50Hz": (5.0, 5.0),  # 정속 배제체적 양축 간격
    "rotary_twin_inverter_60rps": (10.0, 10.0),  # 인버터 배제체적 양축 간격
}
FAN_STEPS = (2.0, 0.05)  # 풍량 그래프의 용량[kW], 풍량[m³/s] 간격
FAN_FLOOR_STEPS = (2.0, 0.10)  # 스탠드형의 풍량 간격
FAN_WATER_STEPS = (5.0, 0.25)  # 물-공기형의 용량·풍량 간격
FAN_OUTDOOR_STEPS = (2.0, 0.25)  # 실외기의 용량[kW], 풍량[m³/s] 간격
ERROR_STEPS = ((5, 1), (15, 2), (35, 5), (70, 10), (150, 20))  # MAPE 최댓값별 간격[%p]
DISPLACEMENT_TITLES = {
    "rotary_single_fixed_50Hz": "Single rotary · fixed 50 Hz supply",
    "rotary_twin_inverter_60rps": "Twin rotary · inverter 60 rps basis",
}
# ──────────────────────────────────────────────────────────────────────


def set_linear_ticks(ax, axis, low, high, step):
    """Set the range and every linear tick, including both interval boundaries."""
    first, last = math.floor(low / step), math.ceil(high / step)
    if last <= first or step <= 0:
        raise ValueError("Invalid linear axis interval")
    ticks = np.arange(first, last + 1) * step
    if axis == "x":
        ax.set_xticks(ticks)
        ax.set_xlim(ticks[0], ticks[-1])
    else:
        ax.set_yticks(ticks)
        ax.set_ylim(ticks[0], ticks[-1])
    return ticks


def dimensions(domain, rows):
    """Convert a displacement datum to the archive's common physical coordinate."""
    y = np.asarray([row["value"] for row in rows])
    if domain == "displacement":
        x = np.asarray([row["capacity_W"] * 1e6 / (row["rho_1_dh_J_m3"] * row["frequency_Hz"]) for row in rows])
    else:
        x = np.asarray([row["capacity_kW"] for row in rows])
    return x, y


def fit_values(domain, law, x):
    """Evaluate the fitted law in the plotted coordinate."""
    if domain == "displacement":
        return x / law["eta_vol_rps_per_f"]
    return predict(law, x)


def make_figure(domain, group, data, base, added, out):
    """Plot before and after on an identical scale and compare matched holdouts."""
    key = "basis" if domain == "displacement" else "stratum"
    old_rows = [row for row in base if row["eligible"] and row[key] == group]
    excluded = [row for row in old_rows if row.get("eta_vol_rps_per_f", 0) > 1]
    old_x, old_y = dimensions(domain, old_rows)
    new_x, new_y = dimensions(domain, [row for row in added if row["eligible"] and row[key] == group])
    if domain == "displacement":
        xstep, ystep = AXIS_STEPS[group]
        title = DISPLACEMENT_TITLES[group]
    else:
        _, mode, side, form = group.split("|")
        xstep, ystep = {
            "floor_standing": FAN_FLOOR_STEPS,
            "wall_mounted": FAN_STEPS,
            "water_to_air": FAN_WATER_STEPS,
            "air_to_water": FAN_OUTDOOR_STEPS,
        }[form]
        title = f"{form.replace('_', ' ').capitalize()} · {side} fan · {mode}"
    colors = plt.rcParams["axes.prop_cycle"].by_key()["color"]
    fig, (curve, error) = plt.subplots(1, 2, figsize=FIGSIZE, layout="constrained", width_ratios=(1.2, 1))
    fig.suptitle(title)
    x = np.linspace(min(old_x.min(), new_x.min()), max(old_x.max(), new_x.max()), 200)
    before, after = (fit_values(domain, data[k]["selected_law"], x) for k in ("before", "after"))
    curve.scatter(old_x, old_y, s=DATA_SIZE, color="0.55", alpha=0.60, linewidths=0, label="Existing rows")
    if excluded:
        ex, ey = dimensions(domain, excluded)
        curve.scatter(ex, ey, s=NEW_SIZE, color=colors[0], marker="x", label="Excluded from refit")
    curve.scatter(new_x, new_y, s=NEW_SIZE, marker="^", color=colors[1], alpha=0.85, linewidths=0, label="Added rows")
    curve.plot(x, before, "--", color=colors[0], label="Before")
    curve.plot(x, after, "-", color=colors[1], label="After")
    curve.set_xlabel("Normalized capacity [cm³/rev]" if domain == "displacement" else "Rated capacity [kW]")
    curve.set_ylabel("Displacement [cm³/rev]" if domain == "displacement" else "Maximum airflow [m³/s]")
    curve.set_title("Catalogue fit")
    curve.legend(loc="upper left", ncols=1, frameon=False, fontsize=dm.fs(-1))
    set_linear_ticks(curve, "x", 0, x.max() * 1.03, xstep)
    set_linear_ticks(curve, "y", 0, max(old_y.max(), new_y.max(), before.max(), after.max()) * 1.05, ystep)

    keys = ("common_family_holdout", "common_manufacturer_holdout", "new_rows_manufacturer_holdout")
    names = ("Family\nexisting", "Maker\nexisting", "Maker\nadded")
    xs = np.arange(len(keys))
    maxima = []
    for phase, dx, marker, color in (("before", -0.12, "o", colors[0]), ("after", 0.12, "^", colors[1])):
        values = [data[k][phase].get("MAPE_pct", math.nan) for k in keys]
        maxima.extend(v for v in values if np.isfinite(v))
        error.scatter(xs + dx, values, marker=marker, color=color, s=NEW_SIZE, label=phase.capitalize(), zorder=3)
    error.set_xticks(xs, names)
    error.set_xlim(-0.5, len(xs) - 0.5)
    error.set_ylabel("Holdout MAPE [%]")
    error.set_title("Matched hold-out error")
    error.legend(loc="upper left", frameon=False, fontsize=dm.fs(-1))
    step = next((step for limit, step in ERROR_STEPS if max(maxima) <= limit), ERROR_STEPS[-1][1])
    set_linear_ticks(error, "y", 0, max(maxima) * 1.18 + step, step)
    fig.canvas.draw()
    for axis, name in ((curve, "x"), (curve, "y"), (error, "y")):
        ticks = getattr(axis, f"get_{name}ticks")()
        limits = getattr(axis, f"get_{name}lim")()
        assert np.allclose(limits, ticks[[0, -1]]) and np.allclose(np.diff(ticks), ticks[1] - ticks[0])
    fig.savefig(out.with_suffix(".png"), dpi=DPI, facecolor="white")
    fig.savefig(out.with_suffix(".pdf"), facecolor="white")
    plt.close(fig)


def main():
    """Render each changed stratum to PNG and PDF."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=HERE / "figures")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    dm.style.use("scientific")
    report = read_json(HERE / "comparison.json")
    for domain in report:
        base, added = load_rows(domain)
        for group, data in report[domain]["groups"].items():
            make_figure(domain, group, data, base, added, args.out / group.replace("|", "_"))


if __name__ == "__main__":
    main()
