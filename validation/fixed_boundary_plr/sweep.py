"""Fixed-boundary part-load sweep for both air-source models.

Conditions
----------
* ASHPB (air-to-water), 9 kW nominal, R32 / R410A / R290: outdoor 7 degC,
  tank 42.5 degC (A7/W45 in the sink-offset convention of the parity harness),
  and outdoor -7 degC / tank 32.5 degC as the cold, low-lift companion.
* ASHP (air-to-air), 3.5 kW nominal, R32: heating outdoor 7 degC / room
  20 degC and cooling outdoor 35 degC / room 27 degC.

Requested duty runs 1.00 -> 0.12 of nameplate in 0.04 steps.  Below the
compressor speed floor the model delivers its minimum capacity
(``capacity_clamped == "min"``) and the operating point is chosen by specific
energy among candidates that meet the request (``tmhp._opt_utils``); those
rows are kept and flagged, because the strategy document asks that the
sub-floor region be *separated*, not hidden.

Output: validation/data/fixed_boundary_plr/{ashpb,ashp}_sweep.csv
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from tmhp import AirSourceHeatPump, AirSourceHeatPumpBoiler
from tmhp.compressor_efficiency import COEFFICIENT_VERSION

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "validation" / "data" / "fixed_boundary_plr"
FRACTIONS = tuple(round(1.0 - 0.04 * i, 2) for i in range(23))  # 1.00 .. 0.12

ASHPB_CASES = (
    # (capacity W, refrigerant, outdoor degC, tank degC)
    (9000.0, "R32", 7.0, 42.5),
    (9000.0, "R410A", 7.0, 42.5),
    (9000.0, "R290", 7.0, 42.5),
    (9000.0, "R32", -7.0, 32.5),
)
ASHP_CASES = (
    # (capacity W, refrigerant, duty, outdoor degC, room degC)
    (3500.0, "R32", "heating", 7.0, 20.0),
    (3500.0, "R32", "cooling", 35.0, 27.0),
)

STATE_KEYS = {
    "rps": ("cmp_rpm [rpm]", 1 / 60.0),
    "n_star": ("n_star [-]", 1.0),
    "m_dot_ref": ("m_dot_ref [kg/s]", 1.0),
    "pr": ("pr_cmp [-]", 1.0),
    "eta_vol": ("eta_cmp_vol [-]", 1.0),
    "eta_isen": ("eta_cmp_isen [-]", 1.0),
    "eta_em": ("eta_cmp [-]", 1.0),
    "E_cmp": ("E_cmp [W]", 1.0),
    "E_tot": ("E_tot [W]", 1.0),
    "cop_sys": ("cop_sys [-]", 1.0),
    "T_evap_C": ("T_ref_evap_sat [°C]", 1.0),
    "T_cond_C": ("T_ref_cond_sat_v [°C]", 1.0),
    "T_dis_C": ("T_ref_cmp_out [°C]", 1.0),
}


def _state(result: dict) -> dict:
    out = {}
    for name, (key, scale) in STATE_KEYS.items():
        v = result.get(key)
        out[name] = float(v) * scale if isinstance(v, (int, float)) else float("nan")
    return out


def sweep_ashpb() -> pd.DataFrame:
    rows = []
    for cap, ref, t_out, t_tank in ASHPB_CASES:
        model = AirSourceHeatPumpBoiler(hp_capacity=cap, ref=ref)
        for f in FRACTIONS:
            r = model.analyze_steady(T_tank_w=t_tank, T0=t_out, Q_ref_tank=cap * f, return_dict=True)
            assert isinstance(r, dict)
            delivered = float(r.get("Q_ref_tank [W]", float("nan")))
            rows.append(
                {
                    "model_class": "ASHPB",
                    "duty": "heating",
                    "capacity_W": cap,
                    "refrigerant": ref,
                    "t_outdoor_C": t_out,
                    "t_sink_C": t_tank,
                    "plr_request": f,
                    "q_request_W": cap * f,
                    "q_delivered_W": delivered,
                    "cr_actual": delivered / cap,
                    "fan_fraction": float(r.get("dV_ou_a [m3/s]", float("nan"))) / model.dV_fan_a_rated,
                    "air_dT_K": float(r.get("T_ou_a_in [°C]", float("nan")))
                    - float(r.get("T_ou_a_out [°C]", float("nan"))),
                    "capacity_clamped": r.get("capacity_clamped"),
                    "failure_reason": r.get("failure_reason", "none"),
                    **_state(r),
                    "coefficient_version": COEFFICIENT_VERSION,
                }
            )
    return pd.DataFrame(rows)


def sweep_ashp() -> pd.DataFrame:
    rows = []
    for cap, ref, duty, t_out, t_room in ASHP_CASES:
        model = AirSourceHeatPump(hp_capacity=cap, ref=ref)
        sign = -1.0 if duty == "heating" else 1.0
        for f in FRACTIONS:
            r = model.analyze_steady(Q_r_iu=sign * cap * f, T0=t_out, T_a_room=t_room, return_dict=True, verbose=False)
            assert isinstance(r, dict)
            delivered = abs(float(r.get("Q_ref_iu [W]", float("nan"))))
            rows.append(
                {
                    "model_class": "ASHP",
                    "duty": duty,
                    "capacity_W": cap,
                    "refrigerant": ref,
                    "t_outdoor_C": t_out,
                    "t_sink_C": t_room,
                    "plr_request": f,
                    "q_request_W": cap * f,
                    "q_delivered_W": delivered,
                    "cr_actual": delivered / cap,
                    "fan_fraction": float(r.get("dV_ou_a [m3/s]", float("nan"))) / model.dV_ou_fan_a_rated,
                    "air_dT_K": abs(
                        float(r.get("T_ou_a_in [°C]", float("nan"))) - float(r.get("T_ou_a_out [°C]", float("nan")))
                    ),
                    "capacity_clamped": r.get("capacity_clamped"),
                    "failure_reason": r.get("failure_reason", "none"),
                    **_state(r),
                    "coefficient_version": COEFFICIENT_VERSION,
                }
            )
    return pd.DataFrame(rows)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    a = sweep_ashpb()
    a.to_csv(OUT_DIR / "ashpb_sweep.csv", index=False)
    b = sweep_ashp()
    b.to_csv(OUT_DIR / "ashp_sweep.csv", index=False)
    for name, df in (("ASHPB", a), ("ASHP", b)):
        ok = df[df.failure_reason == "none"]
        print(f"{name}: {len(df)} rows, {len(ok)} converged; clamped at floor: {(ok.capacity_clamped == 'min').sum()}")
        mod = ok[ok.capacity_clamped.isna()]
        print(
            f"   modulating range: COP {mod.cop_sys.min():.2f}-{mod.cop_sys.max():.2f}, n* {mod.n_star.min():.2f}-{mod.n_star.max():.2f}, "
            f"PR {mod.pr.min():.2f}-{mod.pr.max():.2f}, fan {100 * ok.fan_fraction.min():.0f}-{100 * ok.fan_fraction.max():.0f} %, air dT max {ok.air_dT_K.max():.1f} K"
        )
    print(f"wrote {OUT_DIR.relative_to(REPO_ROOT)}/")


if __name__ == "__main__":
    main()
