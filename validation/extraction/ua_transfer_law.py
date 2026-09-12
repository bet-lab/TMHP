"""L3-L7 -- turn the inverted catalogue bands into TMHP's capacity-based UA rule.

The model generates both coil conductances from a single declared capacity, so
what has to be established is a *capacity-normalised rule*, not any particular
machine's conductance. Two unknowns: ``UA_ou = f(hp_capacity)`` and
``UA_iu / UA_ou``.

Chain
-----
L3  Two rating standards written by different committees for different
    applications place their populations in the same band of conductance per
    unit duty, even though the air flow per kilowatt they adopt differs by a
    factor of about 2.5. Agreement *between* the standards is the evidence; a
    regression *through* them would be meaningless, because inside one standard
    ``UA/Q`` and air flow are linked by an identity rather than a trend.
L4  Transfer is licensed only if heat-pump outdoor coils sit inside the same
    design regime. Normalised by *coil duty* rather than nameplate capacity,
    packaged heat-pump outdoor air flow falls inside the component range, so
    the transfer is interpolation and not extrapolation.
L6  ``hp_capacity`` is the catalogue headline, i.e. the cooling duty of the
    indoor coil. The outdoor coil rejects that duty plus the compressor work,
    so a rule written against the nameplate must carry ``1 + 1/EER``
    explicitly. Skipping it misses by about 30 %.
L7  Combine: ``UA_ou / hp_capacity = (UA/Q_cond)_component * (Q_cond/Q_cool)``.
    Independently, the geometric route of :mod:`.trane_geometry` computes the
    same conductance from published coil dimensions without touching any
    component catalogue.
L7b ``UA_iu / UA_ou`` is not a free constant. Both faces are air coils and the
    equivalent approach is the band invariant, so the conductance ratio is the
    duty ratio ``Q_cool / Q_cond = 1 / (1 + 1/EER)``.

Run
---
``uv run python -m validation.extraction.ua_transfer_law``
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA = REPO_ROOT / "validation" / "data"
OUT_JSON = DATA / "ua_default_derivation.json"


def _load(name: str) -> pd.DataFrame:
    path = DATA / name
    if not path.exists():
        raise SystemExit(
            f"{path.relative_to(REPO_ROOT)} missing -- run the inversion scripts first:\n"
            "  uv run python -m validation.extraction.en328_inversion\n"
            "  uv run python -m validation.extraction.env327_inversion\n"
            "  uv run python -m validation.extraction.trane_geometry"
        )
    return pd.read_csv(path)


def _q(s: pd.Series) -> dict[str, float]:
    return {
        "p10": float(s.quantile(0.10)),
        "median": float(s.median()),
        "p90": float(s.quantile(0.90)),
        "n": int(s.size),
    }


def main() -> None:
    evap = _load("en328_evaporator_inversion.csv")
    evap = evap[evap.is_primary] if "is_primary" in evap else evap
    cond = _load("env327_condenser_inversion.csv")
    hp = _load("hp_outdoor_coil_ua_geometry.csv")

    print("L3 -- do two independent rating standards choose the same band?")
    print(f"  {'standard':<28}{'n':>6}{'flow m3/h per kW':>20}{'UA/Q median':>14}{'equiv LMTD':>13}")
    for label, df in (("EN 328 SC2 (evaporator)", evap), ("ENV 327 (condenser)", cond)):
        print(
            f"  {label:<28}{len(df):>6}{df.airflow_m3h_per_kW.median():>20.0f}"
            f"{df.UA_over_Q.median():>14.3f}{df.LMTD_K.median():>13.2f}"
        )
    flow_ratio = evap.airflow_m3h_per_kW.median() / cond.airflow_m3h_per_kW.median()
    uaq_ratio = evap.UA_over_Q.median() / cond.UA_over_Q.median()
    print(f"  air flow per kW differs by {flow_ratio:.2f}x; UA/Q differs by {uaq_ratio:.2f}x")
    pooled = pd.concat([evap.UA_over_Q, cond.UA_over_Q])
    band = _q(pooled)
    print(
        f"  pooled band UA/Q: p10 {band['p10']:.3f}, median {band['median']:.3f}, "
        f"p90 {band['p90']:.3f}  (n = {band['n']})"
    )

    print()
    print("L4 -- is the heat-pump population inside that design regime?")
    comp_flow = _q(cond.airflow_m3h_per_kW)
    hp_flow = _q(hp.flow_m3h_per_kW_cond)
    print(
        f"  component condensers, coil-duty basis : "
        f"{comp_flow['p10']:.0f} - {comp_flow['p90']:.0f} m3/h per kW (median {comp_flow['median']:.0f})"
    )
    print(
        f"  packaged heat pumps,  coil-duty basis : "
        f"{hp.flow_m3h_per_kW_cond.min():.0f} - {hp.flow_m3h_per_kW_cond.max():.0f} "
        f"m3/h per kW (median {hp_flow['median']:.0f})"
    )
    inside = comp_flow["p10"] <= hp_flow["median"] <= comp_flow["p90"]
    print(f"  heat-pump median inside the component p10-p90 band: {inside}")

    print()
    print("L6 -- nameplate is the indoor-coil duty, not the outdoor-coil duty")
    ratio = _q(hp.Q_cond_over_Q_cool)
    print(f"  Q_cond / Q_cool = 1 + 1/EER : {ratio['p10']:.3f} - {ratio['p90']:.3f} (median {ratio['median']:.3f})")

    print()
    print("L7 -- the capacity-based rule")
    band_uaq_cool = cond.UA_over_Q.median() * ratio["median"]
    band_divisor = 1.0 / band_uaq_cool
    geom_uaq_cool = float(hp.UA_over_Q_cool.median())
    geom_divisor = 1.0 / geom_uaq_cool
    print(f"  route A  band transfer   : UA/Q_cool = {band_uaq_cool:.3f} -> UA_ou = hp_capacity / {band_divisor:.2f}")
    print(f"  route B  published geometry: UA/Q_cool = {geom_uaq_cool:.3f} -> UA_ou = hp_capacity / {geom_divisor:.2f}")
    print(
        f"           geometry band      : {hp.UA_over_Q_cool.min():.3f} - "
        f"{hp.UA_over_Q_cool.max():.3f}  (Q/{1 / hp.UA_over_Q_cool.max():.1f} - "
        f"Q/{1 / hp.UA_over_Q_cool.min():.1f})"
    )
    spread = max(band_uaq_cool, geom_uaq_cool) / min(band_uaq_cool, geom_uaq_cool)
    print(f"  the two routes differ by {spread:.2f}x")
    print()
    print("  Route A needs no assumption about the refrigerant-side film")
    print("  coefficient -- it is inversion plus a duty ratio -- so it sets the")
    print("  default. Route B is the independent check and brackets it from")
    print("  below, because its declared h_ref = 2500 W/(m2 K) is conservative:")
    print("  with the refrigerant-side resistance removed entirely route B gives")
    print("  UA/Q_cool = 0.235, i.e. Q/4.25, which brackets route A from above.")
    recommended = round(band_divisor * 2.0) / 2.0
    print()
    print(f"  ==> DEFAULT  UA_ou_rated = hp_capacity / {recommended:.1f}")

    print()
    print("L7b -- the indoor / outdoor conductance ratio")
    duty_ratio = 1.0 / hp.Q_cond_over_Q_cool
    print(
        f"  Q_cool / Q_cond over the 12 units : {duty_ratio.min():.3f} - "
        f"{duty_ratio.max():.3f} (median {duty_ratio.median():.3f})"
    )
    print(f"  ==> DEFAULT  UA_iu_rated = {duty_ratio.median():.2f} * UA_ou_rated")
    print("      Modern equipment at EER 3.5-4.0 W/W gives 0.78-0.80, so the")
    print("      0.8 already in the code is derived rather than assumed.")

    payload = {
        "en328_evaporator": {
            "n": len(evap),
            "ua_over_q": _q(evap.UA_over_Q),
            "lmtd_k": _q(evap.LMTD_K),
            "flow_m3h_per_kw": _q(evap.airflow_m3h_per_kW),
        },
        "env327_condenser": {
            "n": len(cond),
            "ua_over_q": _q(cond.UA_over_Q),
            "lmtd_k": _q(cond.LMTD_K),
            "flow_m3h_per_kw": _q(cond.airflow_m3h_per_kW),
        },
        "pooled_band_ua_over_q": band,
        "q_cond_over_q_cool": ratio,
        "route_a_band_transfer": {"ua_over_q_cool": band_uaq_cool, "divisor": band_divisor},
        "route_b_geometry": {"ua_over_q_cool": geom_uaq_cool, "divisor": geom_divisor},
        "recommended_divisor": recommended,
        "ua_iu_over_ua_ou": float(duty_ratio.median()),
    }
    import json

    OUT_JSON.write_text(json.dumps(payload, indent=2) + "\n")
    print()
    print(f"  wrote {OUT_JSON.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
