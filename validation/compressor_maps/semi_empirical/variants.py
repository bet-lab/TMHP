"""Rotary speed-shape variants (DESIGN 11.3 item 2): which sources inform the rotary speed terms.

Variant ``b`` (Ossorio-anchored) is the archive's default rotary shape since the 2026-11-16 revision (owner-delegated
decision, DESIGN 12); variant ``a`` is the documented alternative. The first archive's rotary shape (variant ``a``,
"Shao-informed") is fitted on Shao 2004, Ossorio 2019 and AREP #26 together; its
low-speed fall is a compromise between Shao (-23 % at 30 rev/s, power basis not stated) and Ossorio (-2 %, measured
with the drive separated). Variant ``b`` ("Ossorio-anchored") keeps Shao out of the *speed* shape: every Shao speed
record becomes its own unit with its own level, so Shao's level-versus-speed variation is absorbed by the record
levels and only its within-record pressure-ratio variation reaches the shared shape. The speed dependence is then
carried by Ossorio (AREP #26 is fixed-speed). This is an option on the data arrangement, not a change of any number
by hand. A residual coupling remains: in the rotary form the loss terms carry different powers of ``n``, so the
relative weight of the terms inside a Shao record still depends on that record's speed.

For both variants the archive's selection rule (backward elimination from every candidate rotary term on the LOCO
shape RMSE, ``pipeline.DROP_TOL``) picks the terms on that variant's data arrangement; variant ``a`` thereby
reproduces the first archive's rotary fit. The same rule is then applied explicitly to the high-speed side: the candidate term
``qf`` (port/valve flow loss ~ rho N^2, which makes the efficiency fall at high speed) is kept only if it lowers the
leave-one-machine-out shape RMSE by more than ``pipeline.DROP_TOL``; the cross-validation numbers are written
either way. Level form (``scaled``) and speed coordinate (relative) are the archive's selections and are not
re-selected here.

Writes ``validation/coefficients/semi-empirical-2026-11-15/rotary_speed_variants.json``,
``rotary_speed_variants_cv.csv`` and ``rotary_speed_variants_backward.csv``; the archive run
(``python -m validation.compressor_maps.semi_empirical``) calls :func:`run_variants` and writes them with the rest.
Needs the same inputs as the archive run (including the untracked Copeland point file, because ``assemble.build``
reads every source)::

    uv run python -m validation.compressor_maps.semi_empirical.variants
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from validation.compressor_maps.semi_empirical import assemble, cv
from validation.compressor_maps.semi_empirical import inverter as inv
from validation.compressor_maps.semi_empirical import pipeline as P
from validation.compressor_maps.semi_empirical.model import Data, SemiEmpirical
from validation.compressor_maps.semi_empirical.run import ARCHIVE, _clean
from validation.compressor_maps.semi_empirical.sources import LOSO_GROUPS

HIGH_SPEED_TERM = "qf"
SPEED = "rel"
LEVEL = "scaled"
VARIANTS = {
    "a": "Shao-informed (the first archive's data arrangement): Shao, Ossorio and AREP #26 inform the shared shape",
    "b": "Ossorio-anchored: each Shao speed record carries its own level (Shao informs the PR shape only)",
}
#: The default rotary speed shape (owner-delegated decision, DESIGN 12: the measured modern source with the drive
#: measured separately, and the variant whose high-speed term the cross-validation supports).
DEFAULT = "b"


def arrange(rot: pd.DataFrame, variant: str) -> pd.DataFrame:
    """The rotary shape rows for ``variant`` (``b``: Shao speed records as units)."""
    out = rot.copy()
    if variant == "b":
        sh = out.source_id == "shao_2004"
        out.loc[sh, "unit"] = out.loc[sh, "record"]
    return out


def speed_ratios(form: SemiEmpirical, st, rows: pd.DataFrame, source: str) -> dict[str, float]:
    """Model eta_oi(N)/eta_oi(60 rev/s) along the rows of one source (its own condition, unit level refitted)."""
    t = rows[rows.source_id == source]
    out = {}
    for unit, g in t.groupby("unit"):
        d = Data.of(g, form.speed)
        lv = form.fit_levels(st, d)
        base = g.iloc[[0]].copy()
        preds = {}
        for n in (30.0, 60.0, 90.0, 120.0):
            b = base.assign(N_rps=n)
            preds[n] = float(form.predict(st, Data.of(b, form.speed), lv)[0])
        out[unit] = {f"{n:g}": preds[n] / preds[60.0] for n in (30.0, 90.0, 120.0)}
    return out


def data_ratios(rows: pd.DataFrame, source: str) -> dict[str, dict[str, float]]:
    t = rows[rows.source_id == source]
    out = {}
    for unit, g in t.groupby("unit"):
        m = g.groupby(g.N_rps.round(0)).eta_oi.mean()
        if 60.0 not in m.index:
            continue
        out[unit] = {f"{n:g}": float(m.get(n, np.nan) / m[60.0]) for n in (30.0, 90.0, 120.0)}
    return out


def run_variants(d: pd.DataFrame) -> tuple[dict, pd.DataFrame, pd.DataFrame, dict]:
    """Both variants on the fit table ``d``: (json result, CV table, backward table, per-variant fit objects).

    The fit objects (``form``, ``state``, the variant's ``Evaluator``) let the archive run use the default variant's
    fit itself, so ``coefficients.json`` and ``rotary_speed_variants.json`` cannot drift apart.
    """
    rot = P.shape_rows(d, "rotary")
    cv_rows: list[dict] = []
    backward_rows: list[dict] = []
    result: dict = {"variants": {}, "default": DEFAULT}
    fits: dict = {}
    for v, what in VARIANTS.items():
        rows = arrange(rot, v)
        ev = P.Evaluator({"rotary": rows})
        # the archive's selection rule (backward elimination from every candidate term) on this arrangement
        terms, hist = P.backward(ev, "rotary", SPEED, LEVEL)
        backward_rows.extend({**h, "variant": v} for h in hist)
        without_hs = tuple(t for t in terms if t != HIGH_SPEED_TERM)
        with_hs = (*without_hs, HIGH_SPEED_TERM)
        base = SemiEmpirical("rotary", without_hs, SPEED, LEVEL)
        hs = SemiEmpirical("rotary", with_hs, SPEED, LEVEL)
        ev.run([base, hs])
        scores = {}
        for tag, form in (("selected terms without qf", base), ("selected terms with qf", hs)):
            summ, _, st = ev.cache[form.name]
            lo, _ = cv.loso(form, rows, LOSO_GROUPS["rotary"], "source_id", warm=st)
            ed, _ = cv.edges(form, rows, warm=st)
            L = summ.set_index("group")
            scores[tag] = (form, st, float(L.loc["all", "rmse_shape"]))
            row = {
                "variant": v,
                "candidate": tag,
                "form": form.name,
                "loco_shape_all": L.loc["all", "rmse_shape"],
                "loco_total_all": L.loc["all", "rmse_total"],
                "loso_shape_mean": lo.rmse_shape.mean(),
                "parameters": "; ".join(f"{p}={x:.4g}" for p, x in zip(form.shape_names, st.theta, strict=True)),
            }
            for _, r in lo.iterrows():
                row[f"loso_shape_{r.held_out}"] = r.rmse_shape
            for _, r in ed[ed.group == "all"].iterrows():
                key = "top" if "top" in r.holdout else "bottom" if "bottom" in r.holdout else "low_speed"
                row[f"edge_bias_{key}"] = r.bias
            for src in ("ossorio_2019_cnit", "shao_2004"):
                for ratios in speed_ratios(form, st, rows, src).values():
                    if src == "shao_2004" and v == "b":
                        continue  # per-record levels: the model has no Shao speed curve to compare
                    for n, x in ratios.items():
                        row[f"ratio_{src.split('_')[0]}_{n}"] = row.get(f"ratio_{src.split('_')[0]}_{n}", []) + [x]
            for k in [k for k in row if k.startswith("ratio_")]:
                row[k] = float(np.mean(row[k]))
            cv_rows.append(row)
        s_base, s_hs = scores["selected terms without qf"][2], scores["selected terms with qf"][2]
        keep_hs = s_hs < s_base - P.DROP_TOL
        form, st, _ = scores["selected terms with qf"] if keep_hs else scores["selected terms without qf"]
        levels, _ = P.rotary_levels(form, st, d)
        params = dict(zip(form.shape_names, st.theta.tolist(), strict=True))
        fits[v] = {"form": form, "state": st, "evaluator": ev, "rows": rows}
        result["variants"][v] = {
            "description": what,
            "default": v == DEFAULT,
            "terms": list(form.terms),
            "backward_elimination_selected": list(terms),
            "parameters": params,
            "high_speed_term": {
                "term": HIGH_SPEED_TERM,
                "loco_shape_without": s_base,
                "loco_shape_with": s_hs,
                "kept": bool(keep_hs),
                "rule": f"kept only if LOCO shape RMSE falls by more than {P.DROP_TOL}",
            },
            "level_twin": levels["rotary_twin_inverter"]["a_median"],
            "level_twin_by_refrigerant": {
                r: x["a_median"] for r, x in levels["rotary_twin_inverter"]["by_refrigerant"].items()
            },
            "level_single_fixed": levels["rotary_single_fixed"]["a_median"],
        }
    result["data_speed_ratios"] = {
        "ossorio_2019_cnit": data_ratios(rot, "ossorio_2019_cnit"),
        "shao_2004": data_ratios(rot, "shao_2004"),
    }
    result["note"] = (
        "Level form scaled, relative speed coordinate n = N/N_rated (the archive's selections on the original data "
        "arrangement). Variant b is the default rotary shape of the archive (coefficients.json types.rotary_*); "
        "variant a, the original arrangement, is kept as the documented alternative. Levels are refitted on the "
        "same rating points with each variant's shape."
    )
    return result, pd.DataFrame(cv_rows), pd.DataFrame(backward_rows), fits


def write(result: dict, table: pd.DataFrame, backward: pd.DataFrame, out=ARCHIVE) -> None:
    table.to_csv(out / "rotary_speed_variants_cv.csv", index=False, float_format="%.6g")
    backward.to_csv(out / "rotary_speed_variants_backward.csv", index=False, float_format="%.6g")
    (out / "rotary_speed_variants.json").write_text(json.dumps(_clean(result), indent=1) + "\n")


def main() -> int:
    """Write the variant files alone (the archive run ``python -m ...semi_empirical`` writes them too)."""
    inv_fit = inv.default_model(inv.load())
    d = assemble.build(inv_fit)
    result, table, backward, _ = run_variants(d)
    write(result, table, backward)
    print(table.to_string())
    print(json.dumps(_clean(result["variants"]), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
