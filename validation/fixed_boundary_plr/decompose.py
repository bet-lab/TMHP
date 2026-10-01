"""Why the fixed-boundary COP moves with load: heat-exchanger relief against
compressor low-speed loss (plan v3 Sec. 8), and before/after the refit.

For every case of the fixed-boundary sweep the same model is solved four ways:

``current``       shipped correlations (``tmhp.compressor_efficiency``)
``hx_only``       the three efficiencies frozen at their PLR = 1 values of the
                  ``current`` run -- what the heat exchangers alone do when
                  the load falls (Effect A: lower lift at lower mass flow)
``previous``      the frozen v2026-09-15b module
                  (``validation/coefficients/v2026-09-15b/frozen_forms.py``)
``previous_hx``   v2026-09-15b efficiencies frozen at their PLR = 1 values

Effect B (compressor) at a given PLR is ``current - hx_only``; the refit's
change is ``current - previous``.  Rows at the compressor speed floor are kept
and flagged as in ``sweep.py``.

Output: ``validation/data/fixed_boundary_plr/decomposition.csv`` with a
``variant`` column, in the layout of ``sweep.py`` so ``shape_metrics.py``
reads it unchanged.

Run::

    uv run python3 -m validation.fixed_boundary_plr.decompose
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd

from tmhp import AirSourceHeatPump, AirSourceHeatPumpBoiler
from tmhp.compressor_efficiency import COEFFICIENT_VERSION
from tmhp.compressor_speed import RATED_POINT_AIR_TO_AIR, RATED_POINT_AIR_TO_WATER
from validation.fixed_boundary_plr.sweep import ASHP_CASES, ASHPB_CASES, FRACTIONS, _state

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "validation" / "data" / "fixed_boundary_plr"
PREVIOUS = REPO_ROOT / "validation" / "coefficients" / "v2026-09-15b" / "frozen_forms.py"


def _previous_module():
    spec = importlib.util.spec_from_file_location("tmhp_frozen_v20260915b", PREVIOUS)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _previous_kwargs(rps_rated: float) -> dict:
    prev = _previous_module()
    return {
        "eta_cmp_vol": prev.make_eta_vol(rps_rated),
        "eta_cmp_isen": prev.eta_isen_default,
        "eta_cmp": prev.make_eta_em(rps_rated),
    }


def _frozen_kwargs(row: dict) -> dict:
    return {
        "eta_cmp_vol": float(row["eta_vol"]),
        "eta_cmp_isen": float(row["eta_isen"]),
        "eta_cmp": float(row["eta_em"]),
    }


def _run_ashpb(cap, ref, t_out, t_tank, kwargs: dict, variant: str, version: str) -> list[dict]:
    model = AirSourceHeatPumpBoiler(hp_capacity=cap, ref=ref, **kwargs)
    rows = []
    for f in FRACTIONS:
        r = model.analyze_steady(T_tank_w=t_tank, T0=t_out, Q_ref_tank=cap * f, return_dict=True)
        assert isinstance(r, dict)
        delivered = float(r.get("Q_ref_tank [W]", float("nan")))
        rows.append(
            {
                "variant": variant,
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
                "capacity_clamped": r.get("capacity_clamped"),
                "failure_reason": r.get("failure_reason", "none"),
                **_state(r),
                "coefficient_version": version,
            }
        )
    return rows


def _run_ashp(cap, ref, duty, t_out, t_room, kwargs: dict, variant: str, version: str) -> list[dict]:
    model = AirSourceHeatPump(hp_capacity=cap, ref=ref, **kwargs)
    sign = -1.0 if duty == "heating" else 1.0
    rows = []
    for f in FRACTIONS:
        r = model.analyze_steady(Q_r_iu=sign * cap * f, T0=t_out, T_a_room=t_room, return_dict=True, verbose=False)
        assert isinstance(r, dict)
        delivered = abs(float(r.get("Q_ref_iu [W]", float("nan"))))
        rows.append(
            {
                "variant": variant,
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
                "capacity_clamped": r.get("capacity_clamped"),
                "failure_reason": r.get("failure_reason", "none"),
                **_state(r),
                "coefficient_version": version,
            }
        )
    return rows


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    prev_a2w = _previous_kwargs(RATED_POINT_AIR_TO_WATER.rps)
    prev_a2a = _previous_kwargs(RATED_POINT_AIR_TO_AIR.rps)
    for cap, ref, t_out, t_tank in ASHPB_CASES:
        cur = _run_ashpb(cap, ref, t_out, t_tank, {}, "current", COEFFICIENT_VERSION)
        rows += cur
        rows += _run_ashpb(cap, ref, t_out, t_tank, _frozen_kwargs(cur[0]), "hx_only", COEFFICIENT_VERSION)
        prev = _run_ashpb(cap, ref, t_out, t_tank, prev_a2w, "previous", "v2026-09-15b")
        rows += prev
        rows += _run_ashpb(cap, ref, t_out, t_tank, _frozen_kwargs(prev[0]), "previous_hx", "v2026-09-15b")
        print(f"ASHPB {ref} {t_out:g}/{t_tank:g} done")
    for cap, ref, duty, t_out, t_room in ASHP_CASES:
        cur = _run_ashp(cap, ref, duty, t_out, t_room, {}, "current", COEFFICIENT_VERSION)
        rows += cur
        rows += _run_ashp(cap, ref, duty, t_out, t_room, _frozen_kwargs(cur[0]), "hx_only", COEFFICIENT_VERSION)
        prev = _run_ashp(cap, ref, duty, t_out, t_room, prev_a2a, "previous", "v2026-09-15b")
        rows += prev
        rows += _run_ashp(cap, ref, duty, t_out, t_room, _frozen_kwargs(prev[0]), "previous_hx", "v2026-09-15b")
        print(f"ASHP {duty} {t_out:g}/{t_room:g} done")
    df = pd.DataFrame(rows)
    df.to_csv(OUT_DIR / "decomposition.csv", index=False)
    # console summary: COP at the lowest common modulating PLR of each case
    pd.set_option("display.width", 250)
    summ = []
    for key, g in df.groupby(["model_class", "duty", "refrigerant", "t_outdoor_C", "t_sink_C"]):
        mod = g[(g.failure_reason == "none") & g.capacity_clamped.isna()]
        plr_low = mod.groupby("variant").plr_request.min().max()
        at = mod[mod.plr_request == plr_low].set_index("variant").cop_sys
        top = mod[mod.plr_request == 1.0].set_index("variant").cop_sys
        summ.append(
            {
                "case": " ".join(map(str, key)),
                "plr_low": plr_low,
                "cop_rated_current": top.get("current"),
                "cop_low_current": at.get("current"),
                "cop_low_hx_only": at.get("hx_only"),
                "effect_B_compressor": at.get("current") - at.get("hx_only"),
                "cop_low_previous": at.get("previous"),
                "refit_change": at.get("current") - at.get("previous"),
            }
        )
    print(pd.DataFrame(summ).to_string(index=False, float_format=lambda v: f"{v:.3f}"))
    print(f"wrote {OUT_DIR / 'decomposition.csv'}")


if __name__ == "__main__":
    main()
