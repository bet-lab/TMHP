"""One fit-ready table from every standalone-compressor source, with power-basis and exclusion bookkeeping.

Inputs (all hashed into the archive)
    BITZER scroll        validation/bitzer_efficiency_pilot/raw/bitzer_raw_points.csv           (tracked, 2,467 valid)
                         validation/coefficients/BITZER-three-C3-absolute-N-2026-10-06/
                             fit_input_lowlift_raw.csv                                         (tracked, 1,432 valid)
                         validation/data/compressor_maps/bitzer_highpr_raw.csv                 (tracked, 24 valid)
                         derived with the pilot's own scripts/derive.py
    other sources        validation/data/compressor_maps/points_derived.csv (``compressor_maps.derive``); its
                         Copeland rows come from the untracked points_copeland_opi.csv
    rotary rating points validation/default_inputs/compressor_displacement/measurements.csv

Columns added here
    family        scroll | rotary (which semi-empirical form the row feeds)
    machine       physical compressor: the leave-one-machine-out unit and the weight unit
    unit          machine x refrigerant x campaign (x power basis): the unit that carries its own level
    PR, k, mep    pressure ratio, effective isentropic exponent, isentropic MEP [Pa]
    eta_oi_meas   m_dot w_s / P on the source's own power basis
    eta_inv       inverter efficiency divided out (1 where the basis is terminal / line-fed)
    eta_oi        terminal (inverter-excluded) basis -- the fitted quantity
    eta_em_t      m_dot (h_2 - h_1) / P_terminal, only where the discharge temperature is measured
    use_oi/use_v  row enters the eta_oi / eta_v fits; ``reason_oi`` / ``reason_v`` say why not

Exclusion rules (DESIGN 4.1, fixed before the fit): rows refused or flagged by their own source; eta_v > 1.02;
eta_em > 1.02 on terminal power; discharge enthalpy below the isentropic one (eta_isen > 1); eta_oi > 1 on the
source's basis (power below the isentropic power); machines whose displacement is not confirmed (rated-record
median eta_v outside [0.85, 1.05], the rule of ``compressor_maps.prepare``, or a displacement inferred from eta_v
itself) are out of the eta_v fit only, and for them the eta_v > 1.02 rule is not applied to eta_oi.
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from validation.compressor_maps.schema import DATA_DIR, REPO_ROOT
from validation.compressor_maps.semi_empirical import inverter as inv
from validation.compressor_maps.semi_empirical.sources import BY_ID, MACHINE_ALIAS
from validation.compressor_maps.semi_empirical.thermo import k_effective, suction_state

PILOT = REPO_ROOT / "validation" / "bitzer_efficiency_pilot"
BITZER_FILES = {
    "tracked": PILOT / "raw" / "bitzer_raw_points.csv",
    "lowlift": REPO_ROOT
    / "validation"
    / "coefficients"
    / "BITZER-three-C3-absolute-N-2026-10-06"
    / "fit_input_lowlift_raw.csv",
    "highpr": DATA_DIR / "bitzer_highpr_raw.csv",
}
DERIVED = DATA_DIR / "points_derived.csv"
COPELAND_POINTS = DATA_DIR / "points_copeland_opi.csv"
RATING = REPO_ROOT / "validation" / "default_inputs" / "compressor_displacement" / "measurements.csv"
ETA_V_MAX, ETA_EM_MAX = 1.02, 1.02
INVERTER_N_MIN = 15.0  # lowest speed of the measured inverter data [rev/s]

# GMCC specification points, inverter-input basis (DESIGN 10.3; the documents are confidential -- only the
# numbers already in DESIGN.md are used, as a check of the inverter basis, never as fit data).
GMCC_CHECK = (
    {
        "model": "GMCC EDTN210",
        "refrigerant": "R290",
        "V_disp_cm3": 21.0,
        "T_evap_C": 2.7,
        "T_cond_C": 42.3,
        "PR": 2.80,
        "eta_oi_lo": 0.653,
        "eta_oi_hi": 0.669,
    },
    {
        "model": "GMCC LDTN180",
        "refrigerant": "R290",
        "V_disp_cm3": 18.1,
        "T_evap_C": -6.7,
        "T_cond_C": 54.4,
        "PR": 4.90,
        "eta_oi_lo": 0.639,
        "eta_oi_hi": 0.639,
    },
)

COLUMNS = [
    "source_id",
    "group",
    "family",
    "origin",
    "power_basis",
    "basis_status",
    "role",
    "machine",
    "unit",
    "record",
    "refrigerant",
    "V_disp_cm3",
    "N_rps",
    "N_rated_rps",
    "N_rated_basis",
    "T_evap_C",
    "T_cond_C",
    "PR",
    "k",
    "mep",
    "rho_suc",
    "dh_is",
    "p_suc",
    "eta_v",
    "eta_oi_meas",
    "eta_inv",
    "eta_oi",
    "eta_isen_t",
    "eta_em_t",
    "split_measured",
    "level_class",
    "target_level",
    "bitzer_set",
    "use_oi",
    "reason_oi",
    "use_v",
    "reason_v",
]


def _bitzer() -> pd.DataFrame:
    sys.path.insert(0, str(PILOT / "scripts"))
    from derive import derive  # noqa: PLC0415  (the pilot's own derivation)

    frames = []
    for tag, path in BITZER_FILES.items():
        with path.open() as fh:
            rows = [derive(r) for r in csv.DictReader(fh) if r["compressor_type"] == "scroll"]
        d = pd.DataFrame(rows)
        d["bitzer_set"] = tag
        frames.append(d)
    d = pd.concat(frames, ignore_index=True)
    ok = d.valid_bitzer == "yes"
    num = lambda c: pd.to_numeric(d[c], errors="coerce")  # noqa: E731
    out = pd.DataFrame(
        {
            "source_id": "bitzer",
            "machine": "BITZER " + d.compressor_id.astype(str),
            "unit": "BITZER " + d.compressor_id.astype(str) + " " + d.refrigerant.astype(str),
            "refrigerant": d.refrigerant.astype(str),
            "V_disp_cm3": num("displacement_m3_rev") * 1e6,
            "N_rps": num("N_rps_assumed"),
            "N_rated_rps": num("rated_rpm_if_available") / 60.0,
            "N_rated_basis": "nameplate",
            "T_evap_C": num("T_evap_C"),
            "T_cond_C": num("T_cond_C"),
            "PR": num("pressure_ratio"),
            "rho_suc": num("rho_suc"),
            "dh_is": num("h2s") - num("h1"),
            "p_suc": num("P_suc_Pa"),
            "eta_v": num("eta_v"),
            "eta_oi_meas": num("eta_oi"),
            "eta_isen_t": num("eta_is"),
            "eta_em_t": num("eta_em"),
            "bitzer_set": d.bitzer_set,
            "split_measured": False,
        }
    )
    out["reason_oi"] = np.where(ok, "", "refused by the BITZER software (limit status != 0)")
    # Refused rows carry no state; recompute N for the bookkeeping only.
    return out


def _derived() -> pd.DataFrame:
    if not COPELAND_POINTS.exists():
        raise SystemExit(
            f"{COPELAND_POINTS.relative_to(REPO_ROOT)} is missing. It is untracked (not redistributable): regenerate "
            "it with `python -m validation.compressor_maps.parse.copeland_opi` from the harvested OPI records "
            "(validation/compressor_maps/fetch), then run `python -m validation.compressor_maps.derive`."
        )
    d = pd.read_csv(DERIVED, low_memory=False)
    d = d[d.source_id != "bitzer"].copy()
    for c in ("eta_vol", "eta_oi", "eta_isen", "eta_em", "PR", "N_rps", "N_rated_rps", "V_disp_cm3", "T_dis_C"):
        d[c] = pd.to_numeric(d[c], errors="coerce")

    def machine_of(r) -> str:
        alias = MACHINE_ALIAS.get((r.source_id, r.model))
        if alias:
            return alias
        if r.source_id == "copeland_opi":
            return "Copeland " + re.split(r"-", str(r.model))[0]
        return f"{r.source_id} {r.model}"

    d["machine"] = [machine_of(r) for r in d.itertuples()]
    campaign = d.source_id + np.where(d.table_id.astype(str) == "Table 3", " T3", "")
    d["unit"] = d.machine + " " + d.refrigerant.astype(str) + " [" + campaign + "]"
    out = pd.DataFrame(
        {
            "source_id": d.source_id,
            "machine": d.machine,
            "unit": d.unit,
            "refrigerant": d.refrigerant.astype(str),
            "V_disp_cm3": d.V_disp_cm3,
            "N_rps": d.N_rps,
            "N_rated_rps": d.N_rated_rps,
            "N_rated_basis": d.N_rated_basis.astype(str),
            "T_evap_C": pd.to_numeric(d.T_evap_C, errors="coerce"),
            "T_cond_C": pd.to_numeric(d.T_cond_C, errors="coerce"),
            "PR": d.PR,
            "rho_suc": pd.to_numeric(d.rho_suc, errors="coerce"),
            "dh_is": pd.to_numeric(d.dh_is, errors="coerce"),
            "p_suc": pd.to_numeric(d.p_suc_Pa_d, errors="coerce"),
            "eta_v": d.eta_vol,
            "eta_oi_meas": d.eta_oi,
            "eta_isen_t": d.eta_isen,
            "eta_em_t": d.eta_em,
            "split_measured": d.split_basis.isin(["measured_Tdis", "measured_Qcond"]),
            "bitzer_set": "",
            "table_id": d.table_id.astype(str),
            "inv_printed": np.nan,
            "model": d.model.astype(str),
            "comp_type": d.comp_type.astype(str),
        }
    )
    oss = pd.read_csv(inv.INV_2019)
    out.loc[out.source_id == "ossorio_2019_cnit", "inv_printed"] = oss.eta_invert_pct_printed.to_numpy() / 100.0
    out["reason_oi"] = ""
    # Copeland voltage-code twins: identical rows under one machine stem
    cop = out.source_id == "copeland_opi"
    dup = out[cop].duplicated(subset=["machine", "N_rps", "T_evap_C", "T_cond_C", "eta_v", "eta_oi_meas"])
    out.loc[dup[dup].index, "reason_oi"] = "duplicate of a voltage-code twin (same machine stem, identical row)"
    return out


def _rating() -> pd.DataFrame:
    m = pd.read_csv(RATING)
    m = m[(m.kind == "compressor") & m.power_W.notna()].copy()
    rows = []
    for r in m.itertuples():
        st = suction_state(r.refrigerant_normalized, r.T_evap_C, r.T_cond_C, r.T_suction_C)
        from validation.compressor_maps.semi_empirical.thermo import _props  # noqa: PLC0415

        h_liq = _props("H", "P", st["p_dis"], "T", r.T_liquid_C + 273.15, st["fluid"])
        mdot = r.capacity_W / (st["h1"] - h_liq)
        n = float(r.mechanical_rps_measured) if pd.notna(r.mechanical_rps_measured) else float(r.frequency_Hz)
        twin = r.compressor_type == "rotary_twin"
        sid = "rating_lg_sci_twin" if twin else "rating_fixed_single"
        reason = "" if bool(r.eligible) else f"flagged in the source table / duplicate: {r.exclusion_reason}"
        rows.append(
            {
                "source_id": sid,
                "machine": f"{r.manufacturer} {r.model_id}",
                "unit": f"{r.manufacturer} {r.model_id} {r.refrigerant_normalized}",
                "refrigerant": r.refrigerant_normalized,
                "V_disp_cm3": float(r.value),
                "N_rps": n,
                "N_rated_rps": n,
                "N_rated_basis": "rating_row",
                "T_evap_C": r.T_evap_C,
                "T_cond_C": r.T_cond_C,
                "PR": st["PR"],
                "rho_suc": st["rho1"],
                "dh_is": st["dh_is"],
                "p_suc": st["p_suc"],
                "eta_v": mdot / (st["rho1"] * r.value * 1e-6 * n),
                "eta_oi_meas": mdot * st["dh_is"] / r.power_W,
                "eta_isen_t": np.nan,
                "eta_em_t": np.nan,
                "split_measured": False,
                "bitzer_set": "",
                "manufacturer": r.manufacturer,
                "reason_oi": reason,
            }
        )
    return pd.DataFrame(rows)


def _vdisp_suspect(df: pd.DataFrame) -> pd.Series:
    """Rated-record median eta_v outside [0.85, 1.05] (rule of compressor_maps.prepare), per machine."""
    mid = df[df.PR.between(2.0, 3.5) & df.eta_v.notna()].copy()
    mid["dn"] = (mid.N_rps / mid.N_rated_rps - 1.0).abs()
    nearest = mid.sort_values("dn").groupby("machine").N_rps.first()
    mid = mid[mid.N_rps == mid.machine.map(nearest)]
    med = mid.groupby("machine").eta_v.median()
    bad = med[~med.between(0.85, 1.05)].index
    return df.machine.isin(bad)


def build(inverter_fit: inv.InverterFit | None = None) -> pd.DataFrame:
    """The fit-ready table (all rows, with use flags and reasons)."""
    if inverter_fit is None:
        inverter_fit = inv.default_model(inv.load())
    d = pd.concat([_bitzer(), _derived(), _rating()], ignore_index=True)
    meta = d.source_id.map(BY_ID)
    for f in ("group", "family", "origin", "power_basis", "basis_status", "role"):
        d[f] = [getattr(s, f) for s in meta]
    # Highly 2024: the inverter twin rotaries are R290 rating points of the twin level; the one inverter single
    # rotary belongs to no level class and stays a check
    hi = d.source_id == "highly_catalogue_2024"
    d.loc[hi & (d.comp_type != "rotary_twin"), "role"] = "check"
    # Cuevas Table 3 is inverter-fed with the power at the inverter supply
    d.loc[(d.source_id == "cuevas_lebrun_2009") & (d.table_id == "Table 3"), "power_basis"] = "inverter_input"
    d["record"] = d.unit + " @" + d.N_rps.round(2).astype(str)

    # state quantities
    x = d.dh_is * d.rho_suc / d.p_suc
    d["k"] = k_effective(d.PR.to_numpy(float), x.to_numpy(float))
    d["mep"] = d.rho_suc * d.dh_is

    # power basis -> terminal
    d["eta_inv"] = 1.0
    to_conv = d.power_basis == "inverter_input"
    d.loc[to_conv, "eta_inv"] = inverter_fit.eta(d.loc[to_conv, "N_rps"].to_numpy(float))
    oss = d.source_id == "ossorio_2019_cnit"
    d.loc[oss, "eta_inv"] = d.loc[oss, "inv_printed"]
    d["eta_oi"] = d.eta_oi_meas / d.eta_inv
    d.loc[to_conv | oss, "eta_em_t"] = d.loc[to_conv | oss, "eta_em_t"] / d.loc[to_conv | oss, "eta_inv"]

    # ---- exclusions (first reason wins) ---------------------------------------------------------------
    r = d.reason_oi.fillna("").astype(str)

    def flag(mask, text):
        nonlocal r
        r = r.where(~((r == "") & mask), text)

    flag(d.role == "excluded", "source excluded: " + d.source_id.map(lambda s: BY_ID[s].evidence.split(":")[0]))
    flag(d.eta_oi_meas.isna(), "no power printed")
    flag(to_conv & (d.N_rps < INVERTER_N_MIN), "inverter-input power below the inverter data (15 rev/s)")
    flag(d.k.isna(), "state outside the k solver (PR <= 1 or two-phase suction)")
    suspect = _vdisp_suspect(d[d.reason_oi.fillna("") == ""]).reindex(d.index, fill_value=False)
    suspect |= oss  # displacement inferred from the printed eta_v itself
    flag(~suspect & (d.eta_v > ETA_V_MAX), "volume balance: eta_v > 1.02")
    flag(d.eta_em_t > ETA_EM_MAX, "energy balance: eta_em > 1.02 on terminal power")
    flag(d.eta_isen_t > 1.0, "discharge enthalpy below the isentropic one (eta_isen > 1)")
    flag(d.eta_oi_meas > 1.0, "energy balance: eta_oi > 1 (power below the isentropic power)")
    d["reason_oi"] = r
    d["use_oi"] = (r == "") & d.role.isin(["shape", "level"])
    rv = r.where(r != "", "")
    rv = rv.where(~((rv == "") & suspect), "displacement not confirmed (eta_v fit only)")
    rv = rv.where(~((rv == "") & d.eta_v.isna()), "no mass flow / displacement")
    d["reason_v"] = rv
    d["use_v"] = (rv == "") & (d.role == "shape")
    d["split_measured"] = d.split_measured.fillna(False).astype(bool) & (d.source_id != "bitzer")

    # level classes
    d["level_class"] = ""
    d.loc[d.family == "scroll", "level_class"] = "scroll"
    d.loc[d.source_id == "rating_lg_sci_twin", "level_class"] = "rotary_twin_inverter"
    d.loc[hi & (d.role == "level"), "level_class"] = "rotary_twin_inverter"
    d.loc[d.source_id.isin(["rating_fixed_single", "arep_rpt26_hitachi2013"]), "level_class"] = "rotary_single_fixed"
    d["target_level"] = (d.family == "scroll") & (d.group != "BITZER") & (d.V_disp_cm3 <= 80.0)
    return d


def raw_counts() -> pd.DataFrame:
    """Rows per source in the schema point files against rows that derive could evaluate (refrigerant blends
    unknown to CoolProp are skipped by ``compressor_maps.derive``)."""
    raw: dict[str, int] = {}
    for p in sorted(DATA_DIR.glob("points_*.csv")):
        if p.name in ("points_derived.csv", "points_fit_ready.csv"):
            continue
        s = pd.read_csv(p, usecols=["source_id"]).source_id.value_counts()
        for k, v in s.items():
            raw[k] = raw.get(k, 0) + int(v)
    der = pd.read_csv(DERIVED, usecols=["source_id"], low_memory=False).source_id.value_counts()
    rows = [
        {"source_id": k, "rows_in_point_files": v, "rows_derivable": int(der.get(k, 0))} for k, v in sorted(raw.items())
    ]
    return pd.DataFrame(rows)


def input_files() -> list[Path]:
    return [*BITZER_FILES.values(), DERIVED, COPELAND_POINTS, RATING, inv.INV_2023, inv.INV_2019]
