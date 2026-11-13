"""GSHPB 탱크 백엔드 비교 — tank_backends.md §4 의 표.

24 h 시험 시나리오(R32, 2×1 보어홀 100 m, 100 L 탱크, 8 kW, UA_tank_hx 500 W/K, 시작 56 °C,
3.6 L/min 급탕 6 h)를 lumped / stratified / hybrid 와 코일 위치별로 돌려 마크다운 표 행을 낸다.

사용: .venv/bin/python docs/tank_backends/gshpb_tank_backends.py [dt ...]     (기본 dt: 3600 600)
"""

from __future__ import annotations

import sys
import warnings

import numpy as np

import tmhp
from tmhp.constants import c_w, rho_w

HOURS = 24
T_INIT = 56.0
CONFIGS: list[tuple[str, dict]] = [
    ("lumped", {}),
    ("stratified, N=1", dict(tank_model="stratified", n_tank_nodes=1)),
    ("stratified, N=10", dict(tank_model="stratified", n_tank_nodes=10)),
    ("hybrid, N=10", dict(tank_model="hybrid", n_tank_nodes=10)),
    ("stratified, N=8, 상단 코일", dict(tank_model="stratified", n_tank_nodes=8, condenser_node=0)),
    ("stratified, N=8, 하단 코일", dict(tank_model="stratified", n_tank_nodes=8, condenser_node=7)),
    ("hybrid, N=8, 하단 코일", dict(tank_model="hybrid", n_tank_nodes=8, condenser_node=7)),
]


def run(label: str, dt: int, **kw) -> str:
    n = int(HOURS * 3600 / dt)
    model = tmhp.GroundSourceHeatPumpBoiler(
        ref="R32", N_1=2, N_2=1, H_b=100.0, dt_s=float(dt), t_max_s=200 * 3600.0, **kw
    )
    hour = np.floor(np.arange(n) * dt / 3600.0).astype(int)
    dhw = np.where(np.isin(hour, (3, 4, 9, 10, 15, 16)), 6.0e-5, 0.0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df = model.analyze_dynamic(n * dt, dt, T_INIT, dhw, np.full(n, 15.0))

    T = df["T_tank_w [°C]"]
    T_out = df["T_tank_w_out [°C]"] if "T_tank_w_out [°C]" in df else T
    q = float(df["Q_ref_tank [W]"].fillna(0).sum() * dt)
    e = float(df["E_cmp [W]"].fillna(0).sum() * dt)
    drawn = float((rho_w * c_w * df["dV_tank_w_out [m3/s]"].fillna(0) * (T_out - df["T_tank_w_in [°C]"])).sum() * dt)
    loss = float(df["Q_tank_loss [W]"].sum() * dt)
    residual = q - model.C_tank * (T.iloc[-1] - T_INIT) - drawn - loss
    top = df["T_tank_w_top [°C]"].max() if "T_tank_w_top [°C]" in df else T.max()
    mix = df.loc[df["dV_mix_w_out [m3/s]"].fillna(0) > 0, "T_mix_w_out [°C]"].min()
    return (
        f"| {label} | {dt} | {q / 3.6e6:.2f} | {e / 3.6e6:.2f} | {T.min():.1f}–{T.max():.1f} | {top:.1f} | "
        f"{mix:.1f} | {abs(residual) / q:.0e} |"
    )


if __name__ == "__main__":
    steps = [int(a) for a in sys.argv[1:]] or [3600, 600]
    print("| 구성 | dt [s] | Q_ref_tank [kWh] | E_cmp [kWh] | 평균온도 범위 [°C] | 최고 노드 [°C] | 급탕 최저 [°C] | 수지 잔차 |")
    print("|---|---|---|---|---|---|---|---|")
    for dt in steps:
        for label, kw in CONFIGS:
            print(run(label, dt, **kw), flush=True)
