"""Regenerate the coefficient archive (see the package docstring for the smoke run)."""

from __future__ import annotations

import argparse
import hashlib
import json
from importlib import metadata
from pathlib import Path

import numpy as np
import pandas as pd

from validation.compressor_maps.schema import REPO_ROOT
from validation.compressor_maps.semi_empirical import assemble, etav, figures, verify
from validation.compressor_maps.semi_empirical import inverter as inv
from validation.compressor_maps.semi_empirical import pipeline as P
from validation.compressor_maps.semi_empirical.model import Data, Quadratic, SemiEmpirical
from validation.compressor_maps.semi_empirical.sources import SOURCES
from validation.compressor_maps.semi_empirical.split import fit_split

VERSION = "semi-empirical-2026-11-15"
#: Revised in place (the archive was not on main yet): what changed on 2026-11-16.
REVISION = (
    "2026-11-16: eta_v form bounded by construction (exp clearance x competing-outflow leakage), rotary eta_v level "
    "from the displacement-regression rating points; eta_is/eta_em split as a fixed loss share reaching the gas; "
    "default rotary speed shape = the Ossorio-anchored variant b (variant a kept in rotary_speed_variants*)"
)
ARCHIVE = REPO_ROOT / "validation" / "coefficients" / VERSION
TYPES = {
    "scroll": ("scroll", "scroll"),
    "rotary_twin_inverter": ("rotary", "rotary_twin_inverter"),
    "rotary_single_fixed": ("rotary", "rotary_single_fixed"),
}


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _clean(o):
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if not np.isfinite(o) else round(float(o), 6)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def data_sources_table(d: pd.DataFrame) -> pd.DataFrame:
    raw = assemble.raw_counts().set_index("source_id")
    rows = []
    for s in SOURCES:
        t = d[d.source_id == s.source_id]
        u = t[t.use_oi]
        rows.append(
            {
                "source_id": s.source_id,
                "family": s.family,
                "loso_group": s.group,
                "role": s.role,
                "origin": s.origin,
                "machines_used": int(u.machine.nunique()),
                "rows_total": int(len(t)),
                "rows_used_eta_oi": int(len(u)),
                "rows_used_eta_v": int(t.use_v.sum()),
                "rows_excluded_eta_oi": int(len(t) - len(u)),
                "rows_not_derivable": int(
                    raw.loc[s.source_id, "rows_in_point_files"] - raw.loc[s.source_id, "rows_derivable"]
                )
                if s.source_id in raw.index
                else 0,
                "PR_min": u.PR.min(),
                "PR_max": u.PR.max(),
                "N_min_rps": u.N_rps.min(),
                "N_max_rps": u.N_rps.max(),
                "V_disp_cm3": ",".join(f"{v:.1f}" for v in sorted(u.V_disp_cm3.round(1).unique())[:6])
                + (",..." if u.V_disp_cm3.round(1).nunique() > 6 else ""),
                "refrigerants": ",".join(sorted(u.refrigerant.unique())),
                "power_basis": s.power_basis,
                "basis_status": s.basis_status,
                "evidence": s.evidence,
            }
        )
    return pd.DataFrame(rows)


def exclusions_table(d: pd.DataFrame) -> pd.DataFrame:
    a = d[d.reason_oi != ""].groupby(["source_id", "reason_oi"]).size().rename("rows").reset_index()
    a["affects"] = "eta_oi and eta_v"
    a = a.rename(columns={"reason_oi": "reason"})
    b = d[(d.reason_oi == "") & (d.reason_v != "") & d.role.isin(["shape"])]
    b = b.groupby(["source_id", "reason_v"]).size().rename("rows").reset_index().rename(columns={"reason_v": "reason"})
    b["affects"] = "eta_v only"
    return pd.concat([a, b], ignore_index=True).sort_values(["source_id", "affects", "reason"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=ARCHIVE)
    args = ap.parse_args(argv)
    out: Path = args.out
    (out / "figures").mkdir(parents=True, exist_ok=True)

    # ---- drive ----------------------------------------------------------------------------------------
    inv_data = inv.load()
    inv_cv = inv.cross_validate(inv_data)
    inv_fit = inv.default_model(inv_data)
    inv_i3 = inv.fit(inv_data, "I3")

    # ---- data -----------------------------------------------------------------------------------------
    d = assemble.build(inv_fit)
    ev = P.Evaluator({fam: P.shape_rows(d, fam) for fam in ("scroll", "rotary")})
    hist_rows, sel_rows, loco_rows, loso_rows, edge_rows, step_rows, stab_rows = [], [], [], [], [], [], []
    chosen: dict = {}
    speed_tables = {}
    for fam in ("scroll", "rotary"):
        best = {}
        for lev in ("add", "scaled") if fam == "rotary" else ("add",):
            for sp in ("abs", "rel"):
                terms, hist = P.backward(ev, fam, sp, lev)
                hist_rows += hist
                best[(lev, sp)] = SemiEmpirical(fam, terms, sp, lev)
        if fam == "scroll":
            for sp in ("abs", "rel"):
                best[("scaled", sp)] = SemiEmpirical(fam, best[("add", sp)].terms, sp, "scaled")
        ev.run(list(best.values()))
        la_, ls_ = ev.shape(best[("add", "abs")]), ev.shape(best[("scaled", "abs")])
        lev = "scaled" if ls_ < la_ - P.SPEED_TIE else "add"
        other = "add" if lev == "scaled" else "scaled"
        start = SemiEmpirical(fam, P.START_FORM, "abs")
        cands = {
            f"selected-abs ({best[(lev, 'abs')].name})": (best[(lev, "abs")], "finalist"),
            f"selected-rel ({best[(lev, 'rel')].name})": (best[(lev, "rel")], "finalist"),
            f"other level form ({best[(other, 'abs')].name})": (best[(other, "abs")], "level-form alternative"),
            f"start form ({start.name})": (start, "brief start form"),
            "shipped-form baseline (quadratic in PR and N, unit intercepts)": (Quadratic(fam), "baseline"),
        }
        res = {}
        for name, (form, role) in cands.items():
            if form.name in [r["form"].name for r in res.values()]:
                continue
            r = P.finalist_metrics(ev, form, fam)
            res[name] = r
            sel_rows.append(P.selection_row(name, fam, r, role))
            loco_rows.append(r["loco"].assign(family=fam, candidate=name))
            loso_rows.append(r["loso"].assign(family=fam, candidate=name))
            edge_rows.append(r["edges"].assign(family=fam, candidate=name))
            if not r["steps"].empty:
                step_rows.append(r["steps"].assign(family=fam, candidate=name))
            stab_rows.append(P.stability_table(name, form, r).assign(family=fam))
        ra = next(v for k, v in res.items() if k.startswith("selected-abs"))
        rr = next(v for k, v in res.items() if k.startswith("selected-rel"))
        la, lr = ra["loso"].rmse_shape.mean(), rr["loso"].rmse_shape.mean()
        speed = "rel" if lr < la - P.SPEED_TIE else "abs"
        chosen[fam] = ra if speed == "abs" else rr
        speed_tables[fam] = {
            "level_form": {"loco_shape_add": la_, "loco_shape_scaled": ls_, "selected": lev},
            "loso_shape_mean_abs": la,
            "loso_shape_mean_rel": lr,
            "loco_shape_abs": ev.shape(best[(lev, "abs")]),
            "loco_shape_rel": ev.shape(best[(lev, "rel")]),
            "selected": speed,
        }
        if fam == "scroll":
            ct = P.copeland_speed_test(ev.df["scroll"], best[(lev, "abs")].terms, lev)
            speed_tables[fam]["copeland_rated_speed_test"] = ct.to_dict(orient="records")

    # ---- default rotary shape: the Ossorio-anchored variant (owner-delegated decision, DESIGN 12) --------------
    # The level form and speed coordinate above were selected on the first arrangement (Shao, Ossorio and AREP #26
    # in one shared shape). The default shape is variant b of ``variants``: the same selection rule (backward
    # elimination, then the explicit high-speed-term rule) on the arrangement where every Shao speed record has its
    # own level, with the level form and speed coordinate kept. Its fit object is used as it is, so
    # coefficients.json and rotary_speed_variants.json carry the same numbers by construction.
    from validation.compressor_maps.semi_empirical import variants as V  # noqa: PLC0415  (imports this module)

    var_result, var_cv, var_backward, var_fits = V.run_variants(d)
    vb = var_fits[V.DEFAULT]
    res_b = {**P.finalist_metrics(vb["evaluator"], vb["form"], "rotary"), "rows": vb["rows"]}
    name_b = f"default: Ossorio-anchored variant b ({vb['form'].name})"
    sel_rows.append(P.selection_row(name_b, "rotary", res_b, "default (variant b)"))
    loco_rows.append(res_b["loco"].assign(family="rotary", candidate=name_b))
    loso_rows.append(res_b["loso"].assign(family="rotary", candidate=name_b))
    edge_rows.append(res_b["edges"].assign(family="rotary", candidate=name_b))
    if not res_b["steps"].empty:
        step_rows.append(res_b["steps"].assign(family="rotary", candidate=name_b))
    stab_rows.append(P.stability_table(name_b, vb["form"], res_b).assign(family="rotary"))
    first_rotary = chosen["rotary"]["form"].name
    chosen["rotary"] = res_b
    speed_tables["rotary"]["default_shape"] = {
        "variant": V.DEFAULT,
        "description": V.VARIANTS[V.DEFAULT],
        "candidate": vb["form"].name,
        "first_archive_selection_on_arrangement_a": first_rotary,
        "note": "level form and speed coordinate as selected on arrangement a (see the archive README for what the "
        "same rules give on arrangement b); kept, because the decision was made on variant b as measured",
    }

    # ---- sensitivity: Shao read as inverter-input ---------------------------------------------------------
    rot_form: SemiEmpirical = chosen["rotary"]["form"]
    d_alt = d.copy()
    sh = d_alt.source_id == "shao_2004"
    d_alt.loc[sh, "eta_inv"] = inv_fit.eta(d_alt.loc[sh, "N_rps"].to_numpy(float))
    d_alt.loc[sh, "eta_oi"] = d_alt.loc[sh, "eta_oi_meas"] / d_alt.loc[sh, "eta_inv"]
    alt_rows = V.arrange(P.shape_rows(d_alt, "rotary"), V.DEFAULT)
    st_alt = rot_form.fit(Data.of(alt_rows, rot_form.speed))
    lo_alt, _ = P.cv.loso(rot_form, alt_rows, P.LOSO_GROUPS["rotary"], "source_id", warm=st_alt)

    # ---- final fits, levels ------------------------------------------------------------------------------
    scr_form: SemiEmpirical = chosen["scroll"]["form"]
    st_s, st_r = chosen["scroll"]["state"], chosen["rotary"]["state"]
    lvl_s, mach_s = P.scroll_level(scr_form, st_s, ev.df["scroll"])
    lvl_r, units_r = P.rotary_levels(rot_form, st_r, d)
    levels = {"scroll": lvl_s, **lvl_r}
    level_value = {
        "scroll": lvl_s["a_median"],
        "rotary_twin_inverter": lvl_r["rotary_twin_inverter"]["a_median"],
        "rotary_single_fixed": lvl_r["rotary_single_fixed"]["a_median"],
    }
    twin_r410a = lvl_r["rotary_twin_inverter"]["by_refrigerant"].get("R410A", {}).get("a_median")
    alt_lv = rot_form.fit_levels(
        st_alt, Data.of(d_alt[d_alt.use_oi & (d_alt.source_id == "rating_lg_sci_twin")], rot_form.speed)
    )
    alt_twin = float(np.median(list(alt_lv.values())))

    # ---- eta_v (bounded form; rotary level from the displacement-regression rating points), split ----------------
    regression = json.loads((assemble.RATING.parent / "regression.json").read_text())
    ev_v = etav.fit_all(d, assemble.RATING, regression)
    split = {}
    split_tables = []
    for fam in ("scroll", "rotary"):
        s, t = fit_split(d, fam)
        split[fam] = s
        split_tables.append(t)

    # ---- checks -----------------------------------------------------------------------------------------
    eta_inv60 = float(inv_fit.eta(np.array([60.0]))[0])
    gmcc = []
    for g in assemble.GMCC_CHECK:
        m = P.eval_at_condition(
            rot_form,
            st_r,
            level_value["rotary_twin_inverter"],
            g["refrigerant"],
            g["T_evap_C"],
            g["T_cond_C"],
            11.1,
            60.0,
        )
        meas = 0.5 * (g["eta_oi_lo"] + g["eta_oi_hi"])
        gmcc.append({**g, "model_terminal": m, "model_x_inverter60": m * eta_inv60, "implied_inverter_eff": meas / m})
    hi = d[d.source_id == "highly_catalogue_2024"]
    hpred = rot_form.predict(st_r, Data.of(hi, rot_form.speed), level_value["rotary_twin_inverter"])
    highly = {
        "rows": int(len(hi)),
        "data_eta_oi_median": float(hi.eta_oi.median()),
        "model_median": float(np.median(hpred)),
        "ratio_data_model_median": float(np.median(hi.eta_oi.to_numpy() / hpred)),
    }
    repro, pv = verify.reproduce(d), verify.pipeline_variant(d)
    pr_max = {
        "scroll": float(ev.df["scroll"].PR.max()),
        "rotary": float(ev.df["rotary"].PR.max()),
    }
    mono = {
        t: P.monotonic_check(
            chosen[fam]["form"], chosen[fam]["state"], level_value[t], pr_max[fam], P.N_RATED_DEFAULT[t]
        )
        for t, (fam, _) in TYPES.items()
    }

    # ---- grid vs shipped -----------------------------------------------------------------------------------
    import sys  # noqa: PLC0415

    sys.path.insert(0, str(REPO_ROOT / "src"))
    from tmhp import compressor_efficiency as shipped  # noqa: PLC0415

    f_is, f_em = shipped.make_eta_isen(50.0), shipped.make_eta_em(50.0)
    grid = []
    for t, (fam, _) in TYPES.items():
        form, st = chosen[fam]["form"], chosen[fam]["state"]
        lv = twin_r410a if (t == "rotary_twin_inverter" and twin_r410a) else level_value[t]
        for n in P.GRID_N:
            e = P.eval_eta(
                form,
                st,
                lv,
                P.GRID_REF["refrigerant"],
                P.GRID_PR,
                n,
                P.GRID_REF["T_cond_C"],
                P.GRID_REF["superheat_K"],
                P.N_RATED_DEFAULT[t],
            )
            for pr, v in zip(P.GRID_PR, e, strict=True):
                grid.append(
                    {
                        "type": t,
                        "N_rps": n,
                        "PR": pr,
                        "eta_oi_model": v,
                        "shipped_eta_is_x_eta_em": f_is(pr, n) * f_em(pr, n),
                    }
                )
    grid = pd.DataFrame(grid)
    ref_eta = {
        t: P.eval_at_condition(
            chosen[fam]["form"],
            chosen[fam]["state"],
            level_value[t],
            P.RATING_REF["refrigerant"],
            P.RATING_REF["T_evap_C"],
            P.RATING_REF["T_cond_C"],
            P.RATING_REF["superheat_K"],
            P.RATING_REF["N"],
            P.N_RATED_DEFAULT[t],
        )
        for t, (fam, _) in TYPES.items()
    }

    # ---- rotary hull includes the rating points ---------------------------------------------------------------
    hulls = {
        "scroll": P.hull(ev.df["scroll"]),
        "rotary": P.hull(pd.concat([ev.df["rotary"], d[d.use_oi & (d.role == "level") & (d.family == "rotary")]])),
    }

    # ---- write -----------------------------------------------------------------------------------------
    pd.DataFrame(hist_rows).to_csv(out / "backward_elimination.csv", index=False, float_format="%.6g")
    pd.DataFrame(sel_rows).to_csv(out / "model_selection.csv", index=False, float_format="%.6g")
    pd.concat(loco_rows).to_csv(out / "loco.csv", index=False, float_format="%.6g")
    pd.concat(
        [*loso_rows, lo_alt.assign(family="rotary", candidate="sensitivity: Shao read as inverter-input")]
    ).to_csv(out / "loso.csv", index=False, float_format="%.6g")
    pd.concat(edge_rows).to_csv(out / "edge_holdout.csv", index=False, float_format="%.6g")
    pd.concat(step_rows).to_csv(out / "step_slopes.csv", index=False, float_format="%.6g")
    pd.concat(stab_rows).to_csv(out / "parameter_stability.csv", index=False, float_format="%.6g")
    data_sources_table(d).to_csv(out / "data_sources.csv", index=False, float_format="%.6g")
    exclusions_table(d).to_csv(out / "exclusions.csv", index=False)
    inv_cv.to_csv(out / "inverter_cv.csv", index=False, float_format="%.6g")
    pd.concat(split_tables).to_csv(out / "split.csv", index=False, float_format="%.6g")
    for name, table in ev_v["tables"].items():
        table.to_csv(out / f"{name}.csv", index=False, float_format="%.6g")
    V.write(var_result, var_cv, var_backward, out)
    grid.to_csv(out / "grid_vs_shipped.csv", index=False, float_format="%.6g")
    lv_tab = pd.concat(
        [
            mach_s.assign(family="scroll").rename(columns={"V": "V_disp_cm3"}),
            units_r.assign(family="rotary").rename(columns={"V": "V_disp_cm3"}),
        ],
        ignore_index=True,
    )
    lv_tab.to_csv(out / "levels.csv", index=False, float_format="%.6g")

    inputs = [
        {"path": str(p.relative_to(REPO_ROOT)), "sha256": _sha(p), "tracked": p.name != "points_copeland_opi.csv"}
        for p in assemble.input_files()
    ]
    types = {}
    for t, (fam, cls) in TYPES.items():
        form, st = chosen[fam]["form"], chosen[fam]["state"]
        v = ev_v[fam]
        if fam == "rotary":
            lv_v = v["levels"][t]
            eta_v = {
                "form": v["form"],
                "candidate": v["candidate"],
                "terms": v["terms"],
                "parameters": v["parameters"],
                "speed_coordinate": "n = N/N_rated",
                "c0": lv_v["c0_median"],
                "c0_iqr": lv_v["c0_iqr"],
                "level_source": f"catalogue rating points of the displacement-regression basis {lv_v['basis']}",
                "level": lv_v,
                "regression_consistency": v["regression_consistency"][t],
                "shape_rows": v["shape_rows"],
                "shape_machines": v["shape_machines"],
                "envelope": ev_v["envelope"][t],
            }
        else:
            eta_v = {
                "form": v["form"],
                "candidate": v["candidate"],
                "terms": v["terms"],
                "parameters": v["parameters"],
                "speed_coordinate": "n = N/50 rev/s",
                "c0": v["c0"],
                "c0_iqr": v["c0_iqr"],
                "level_source": "median of the unit levels of the scroll eta_v rows",
                "rows": v["rows"],
                "machines": v["machines"],
                "envelope": ev_v["envelope"][t],
            }
        types[t] = {
            "family_form": fam,
            "provisional": fam == "rotary",
            "eta_oi": {
                "quantity": "m_dot (h_2s - h_1) / P_terminal (inverter excluded)",
                "form": {
                    ("scroll", "add"): "1/eta = a*phi_z(PR; rv, k, zv) + losses",
                    ("scroll", "scaled"): "1/eta = phi_z(PR; rv, k, zv) + a*losses (q2 = 1)",
                    ("rotary", "add"): "1/eta = a + losses",
                    ("rotary", "scaled"): "1/eta = 1 + a*losses (q2 = 1)",
                }[(fam, form.level)],
                "level_form": form.level,
                "losses": "(q0/n + q1 + q2*n)*1e5/MEP_s + cL*(PR-1)/n + qf*n^2*1e4*rho_1/MEP_s + cu*(MEP_s/1e6)/n "
                "+ cH*(PR^((k-1)/k)-1); terms not listed in parameters are zero",
                "terms": list(form.terms),
                "parameters": dict(zip(form.shape_names, st.theta.tolist(), strict=True)),
                "speed_coordinate": "n = N/50 rev/s" if form.speed == "abs" else "n = N/N_rated",
                "N_rated_default_rps": P.N_RATED_DEFAULT[t] if form.speed == "rel" else None,
                "level_a": level_value[t],
                "level": levels[cls],
                "eta_oi_at_rating_reference": ref_eta[t],
                "rating_reference": P.RATING_REF,
            },
            "eta_v": eta_v,
            "split": split[fam],
            "hull_PR_N": hulls[fam],
            "power_basis_of_fit": "terminal (sources on the inverter-input basis divided by the drive model I1)",
            "monotonic_check": mono[t],
        }
    types["rotary_twin_inverter"]["eta_oi"]["level_note"] = (
        "Use level.by_refrigerant[ref].a_median when the refrigerant is R32 or R410A, else level_a (pooled)."
    )
    rotary_eta_v = {k: v for k, v in ev_v["rotary"].items() if k not in ("levels", "regression_consistency")}
    coeffs = {
        "version": VERSION,
        "status": "fitted and cross-validated; wired into src/tmhp/compressor_efficiency.py (generated block)",
        "revision": REVISION,
        "regenerate": "uv run python -m validation.compressor_maps.semi_empirical",
        "inputs": inputs,
        "conventions": {
            "MEP_s": "rho_1 * (h_2s - h_1) [Pa]",
            "k": "effective exponent: k/(k-1)(PR^((k-1)/k)-1) = (h_2s-h_1) rho_1 / p_1",
            "phi": "(isentropic work to rv + isochoric work to p_2) / isentropic work",
            "phi_z": "phi - zv*(phi-1) where PR < rv^k, else phi",
            "n": "N / 50 rev/s (absolute)",
            "states": "dew-point saturation pressures; suction superheat as the source states",
            "huber_threshold": 0.05,
            "weights": "each machine 1; units equal within a machine, speed records within a unit, rows within a record",
        },
        "types": types,
        "drive": {
            "form": "eta_inv = 1 - lam*(1 + beta*(60/N - 1)), N in rev/s",
            "beta": float(inv_fit.theta[0]),
            "lam_median": float(np.median(list(inv_fit.lam.values()))),
            "lam_by_inverter": inv_fit.lam,
            "lam_iqr": [float(np.quantile(list(inv_fit.lam.values()), q)) for q in (0.25, 0.75)],
            "eta_at_60rps": eta_inv60,
            "eta_at_20rps": float(inv_fit.eta(np.array([20.0]))[0]),
            "data_range_rps": [15.0, 120.0],
            "load_form_I3": {"theta_beta_gamma": inv_i3.theta.tolist(), "lam_by_inverter": inv_i3.lam},
        },
        "selection": {
            "speed_coordinate": speed_tables,
            "drop_tolerance": P.DROP_TOL,
            "eta_v_rotary": rotary_eta_v,
            "eta_v_scroll_old_form": ev_v["scroll"]["old_form"],
        },
        "sensitivity": {
            "shao_as_inverter_input": {
                "parameters": dict(zip(rot_form.shape_names, st_alt.theta.tolist(), strict=True)),
                "twin_level_a_median": alt_twin,
                "loso": lo_alt.to_dict(orient="records"),
            }
        },
        "checks": {
            "gmcc_inverter_input": gmcc,
            "highly_2024": highly,
            "prototype_reproduction": repro,
            "pipeline_variant_bitzer_only": pv,
        },
    }
    (out / "coefficients.json").write_text(json.dumps(_clean(coeffs), indent=1, ensure_ascii=False) + "\n")
    env = {k: metadata.version(k) for k in ("numpy", "scipy", "pandas", "coolprop")}
    (out / "environment.json").write_text(json.dumps(env, indent=1) + "\n")

    figures.make_all(
        out / "figures",
        d=d,
        chosen=chosen,
        level_value=level_value,
        twin_r410a=twin_r410a,
        inv_data=inv_data,
        inv_fit=inv_fit,
        levels_table=lv_tab,
        shao_alt=(st_alt, alt_twin),
    )
    print(json.dumps(_clean({"speed": speed_tables, "levels": level_value, "ref_eta": ref_eta}), indent=1))
    return 0
