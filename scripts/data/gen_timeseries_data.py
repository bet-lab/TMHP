"""Run a representative 24-hour ASHPB dynamic simulation and emit JSON
for the interactive timeseries widget. The profile is a residential DHW
day: sinusoidal ambient, with morning (07:00) and evening (19:00) draws.
"""

from __future__ import annotations

import math

from scripts.data._common import write_json
from tmhp import AirSourceHeatPumpBoiler

STEP_MIN = 10
N_STEPS = (24 * 60) // STEP_MIN  # 144

AMBIENT_MEAN_C = 10.0
AMBIENT_AMP_C = 6.0
AMBIENT_PEAK_HOUR = 14.0

DHW_DRAWS = [(7.0, 60.0), (19.0, 80.0)]


def ambient_at(hour: float) -> float:
    phase = 2 * math.pi * (hour - (AMBIENT_PEAK_HOUR - 6.0)) / 24.0
    return AMBIENT_MEAN_C + AMBIENT_AMP_C * math.sin(phase)


def dhw_demand_kw_at(hour: float) -> float:
    """Crude pulse model: each draw spans ~10 min at 8 kW peak."""
    for h_peak, _vol in DHW_DRAWS:
        if abs(hour - h_peak) < 1 / 6:
            return 8.0
    return 0.0


def build_timeseries() -> dict:
    ashpb = AirSourceHeatPumpBoiler(ref="R32")
    series = []
    T_tank = 50.0
    for k in range(N_STEPS):
        t_min = k * STEP_MIN
        hour = t_min / 60.0
        t_amb = ambient_at(hour)
        q_demand_kw = dhw_demand_kw_at(hour)

        q_call_kw = max(8.0 if T_tank < 53.0 else 0.0, q_demand_kw)
        cop, p_cmp_kw, q_cond_kw = float("nan"), 0.0, 0.0
        if q_call_kw > 0:
            res = ashpb.analyze_steady(
                T_tank_w=T_tank, T0=t_amb, Q_ref_tank=q_call_kw * 1000.0,
            )
            cop = res["cop_sys [-]"]
            p_cmp_kw = res["E_cmp [W]"] / 1000.0
            q_cond_kw = res["Q_ref_tank [W]"] / 1000.0

        net_kw = q_cond_kw - q_demand_kw
        T_tank += net_kw * STEP_MIN * 60.0 / 837.0

        series.append({
            "t_min": t_min,
            "t_amb_c": round(t_amb, 2),
            "q_heat_kw": round(q_cond_kw, 3),
            "q_demand_kw": round(q_demand_kw, 3),
            "p_cmp_kw": round(p_cmp_kw, 3),
            "cop": None if math.isnan(cop) else round(cop, 3),
            "t_tank_c": round(T_tank, 2),
        })

    return {
        "step_min": STEP_MIN,
        "refrigerant": "R32",
        "series": series,
    }


def main() -> None:
    write_json("timeseries-24h.json", build_timeseries())


if __name__ == "__main__":
    main()
