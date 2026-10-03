"""Copeland OPI AHRI-540 coefficient sets -> CompressorPoint grid rows.

Each harvested record (``fetch.copeland_opi``) carries, for one variable-speed
scroll at one rated speed, the ten AHRI 540 coefficients of

    X(S, D) = c0 + c1 S + c2 D + c3 S^2 + c4 S D + c5 D^2 + c6 S^3 + c7 D S^2 + c8 D^2 S + c9 D^3

with S = suction dew-point temperature [degF], D = discharge dew-point [degF],
for capacity [Btu/h] (Rt_C*), power [W] (Rt_W*), current [A] (Rt_A*) and mass
flow [lb/h] (Rt_M*).  Rating conventions are printed alongside: ``Rt_MidDew='D'``
(dew point), ``Rt_Superheat=20`` degF constant, ``Rt_SubCool=15`` degF, and the
valid window ``Rt_MinEvap..Rt_MaxEvap`` x ``Rt_MinCond..Rt_MaxCond`` [degF].  The
rated speed of the record is the RPM in the search row / ``Rt_Comment``; the
power is measured at the *drive* input (comment: "rating using EV... drive"),
so it includes inverter losses.

The polynomial is a manufacturer fit to calorimeter tests, not raw data, so
rows are tagged ``method='published_model'`` and every compressor x speed grid
is down-weighted to count once in the pooled fit (``weight = 1/n_grid``).

Displacement: ``Mechanical Displacement (in^3/Rev)`` from the CSummary text.
N_rated: the speed variant whose ``Ad_AppDescription`` says "Capacity Rating
Speed" (basis 'rating_row'); models without that variant fall back to the
highest listed speed and are flagged 'unresolved'.
"""

from __future__ import annotations

import html
import json
import re
from collections import defaultdict

from validation.compressor_maps.schema import DATA_DIR, EVIDENCE_DIR, CompressorPoint, write_points

OPI_DIR = EVIDENCE_DIR / "copeland" / "opi"
SOURCE_ID = "copeland_opi"
GRID_STEP_F = 10.0
IN3_TO_CM3 = 16.387064
BTUH_TO_W = 0.29307107
LBH_TO_KGS = 0.45359237 / 3600.0
REFRIGERANT_MAP = {
    "410A": "R410A",
    "R-410A": "R410A",
    "R410A": "R410A",
    "454B": "R454B",
    "R-454B": "R454B",
    "R454B": "R454B",
    "32": "R32",
    "R-32": "R32",
    "R32": "R32",
    "134A": "R134a",
    "R-134A": "R134a",
    "R134A": "R134a",
    "407C": "R407C",
    "R-407C": "R407C",
    "22": "R22",
    "R-22": "R22",
    "R22": "R22",
    "452B": "R452B",
    "R-452B": "R452B",
}


def _f2c(f: float) -> float:
    return (f - 32.0) / 1.8


def ahri540(c: list[float], s: float, d: float) -> float:
    return (
        c[0]
        + c[1] * s
        + c[2] * d
        + c[3] * s * s
        + c[4] * s * d
        + c[5] * d * d
        + c[6] * s**3
        + c[7] * d * s * s
        + c[8] * d * d * s
        + c[9] * d**3
    )


def _coeffs(item: dict, prefix: str) -> list[float]:
    return [float(item.get(f"Rt_{prefix}{i}") or 0.0) for i in range(10)]


def _displacement_cm3(summary_html: str) -> float | None:
    s = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", summary_html or "")))
    m = re.search(r"Mechanical Displacement \(in\^3/Rev\):\s*([0-9.]+)", s)
    return float(m.group(1)) * IN3_TO_CM3 if m else None


def load_records() -> list[dict]:
    recs = []
    for p in sorted(OPI_DIR.glob("*.json")):
        d = json.loads(p.read_text())
        items = d.get("coefficients", {}).get("ResponseItem") or []
        if not items:
            continue
        it = items[0]
        idx = d["index"]
        rpm = float(idx.get("rpm") or 0)
        if rpm <= 0:
            m = re.search(r"(\d{3,5})\s*RPM", it.get("Rt_Comment", "") or "")
            rpm = float(m.group(1)) if m else 0.0
        if 0 < rpm < 200:  # the OPI result grid prints Hz for some families (XPV): 2-pole, 1 Hz = 60 rpm
            rpm *= 60.0
        if rpm <= 0:
            continue
        recs.append(
            {
                "path": p,
                "index": idx,
                "item": it,
                "rpm": rpm,
                "V_disp_cm3": _displacement_cm3(d.get("summary_html", "")),
            }
        )
    return recs


def parse() -> list[CompressorPoint]:
    recs = load_records()
    by_model: dict[str, list[dict]] = defaultdict(list)
    for r in recs:
        by_model[r["index"]["model"]].append(r)
    pts: list[CompressorPoint] = []
    for model, group in sorted(by_model.items()):
        rated = [g for g in group if "rating" in (g["item"].get("Ad_AppDescription") or "").lower()]
        if rated:
            n_rated_rps, basis = rated[0]["rpm"] / 60.0, "rating_row"
        else:
            n_rated_rps, basis = max(g["rpm"] for g in group) / 60.0, "unresolved"
        vdisp = next((g["V_disp_cm3"] for g in group if g["V_disp_cm3"]), None)
        if not vdisp or n_rated_rps <= 0:
            continue
        for g in group:
            it = g["item"]
            if str(it.get("Rt_ConstSuperHeat", "Y")).upper() not in ("Y", "YES"):
                continue  # only the constant-superheat convention is handled
            mid = str(it.get("Rt_MidDew", "D")).upper()
            if mid not in ("D", "M"):
                continue
            # 'M' = mid-point saturation; identical to dew for R-32 and within 0.1 K for R-410A
            sat_conv = "dew_dew" if mid == "D" else "dew_mid"
            sh_f, sc_f = float(it.get("Rt_Superheat") or 20), float(it.get("Rt_SubCool") or 15)
            cC, cW, cM = _coeffs(it, "C"), _coeffs(it, "W"), _coeffs(it, "M")
            if not any(cC) or not any(cW):
                continue
            e0, e1 = float(it["Rt_MinEvap"]), float(it["Rt_MaxEvap"])
            d0, d1 = float(it["Rt_MinCond"]), float(it["Rt_MaxCond"])
            grid = []
            s = e0
            while s <= e1 + 1e-9:
                d = d0
                while d <= d1 + 1e-9:
                    lift_k = (d - s) / 1.8
                    if 15.0 <= lift_k <= 60.0:  # generic A/C scroll map; the polynomial is only a fit inside it
                        grid.append((s, d))
                    d += GRID_STEP_F
                s += GRID_STEP_F
            refr = REFRIGERANT_MAP.get(
                (it.get("Rf_RefDescription") or g["index"].get("ref") or "").upper().replace(" ", "")
            )
            if refr is None:
                continue
            w = 1.0 / max(len(grid), 1)
            for s, d in grid:
                q = ahri540(cC, s, d) * BTUH_TO_W
                p_el = ahri540(cW, s, d)
                m = ahri540(cM, s, d) * LBH_TO_KGS if any(cM) else None
                if q <= 0 or p_el <= 0 or (m is not None and m <= 0):
                    continue
                pr_guess = d - s  # placeholder; the real PR filter happens in derive/fit on CoolProp pressures
                del pr_guess
                pts.append(
                    CompressorPoint(
                        source_id=SOURCE_ID,
                        manufacturer="Copeland",
                        model=model,
                        comp_type="scroll",
                        refrigerant=refr,
                        V_disp_cm3=vdisp,
                        N_rated_rps=n_rated_rps,
                        N_rated_basis=basis,
                        N_rps=g["rpm"] / 60.0,
                        Q_evap_W=q,
                        P_el_W=p_el,
                        T_evap_C=_f2c(s),
                        T_cond_C=_f2c(d),
                        sat_convention=sat_conv,
                        sh_convention="custom",
                        dT_sh_K=sh_f / 1.8,
                        dT_sc_K=sc_f / 1.8,
                        m_dot_kg_s=m,
                        P_includes_inverter=True,
                        speed_variant=g["index"].get("app", ""),
                        method="published_model",
                        doc_path=str(g["path"].relative_to(EVIDENCE_DIR.parent)),
                        page="GetCCoefficients",
                        table_id=it.get("Rt_RatingRefNo", ""),
                        note=(it.get("Ad_AppDescription") or "") + " | " + it.get("Rt_Comment", "")[:70],
                        weight=w,
                    )
                )
    return pts


def main() -> None:
    pts = parse()
    out = DATA_DIR / "points_copeland_opi.csv"
    write_points(pts, out)
    models = sorted({p.model for p in pts})
    speeds = sorted({(p.model, round(p.N_rps, 1)) for p in pts})
    print(f"{len(pts)} grid points, {len(models)} models, {len(speeds)} model-speed records -> {out}")
    # verbatim guard against the OPI summary of ZPV0282E-2E9 @ 4500 rpm: 47400 Btu/h, 3040 W, 628 lb/h at 50/115 degF
    rec = [r for r in load_records() if r["index"]["model"] == "ZPV0282E-2E9" and r["rpm"] == 4500]
    if rec:
        it = rec[0]["item"]
        q, w, m = (
            ahri540(_coeffs(it, "C"), 50, 115),
            ahri540(_coeffs(it, "W"), 50, 115),
            ahri540(_coeffs(it, "M"), 50, 115),
        )
        print(f"guard ZPV0282E-2E9@4500: Q={q:.0f} Btu/h (47400), W={w:.0f} W (3040), m={m:.0f} lb/h (628)")
        assert abs(q - 47400) < 300 and abs(w - 3040) < 40 and abs(m - 628) < 8


if __name__ == "__main__":
    main()
