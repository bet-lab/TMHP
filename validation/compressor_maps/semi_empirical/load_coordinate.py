"""Rotary loss terms read against the design point of the refrigerant: does the compressor data support it? (#183)

The rotary eta_oi form (:mod:`.model`) reads its load-dependent loss terms in the suction MEP ``rho_1 dh_is``
[Pa], an absolute coordinate. One reading of the low-speed question of #183 is that a machine's losses scale with
its *own* load rather than with an absolute pressure, i.e. that the terms should see the MEP per unit of a design
MEP. This module tests that reading on the compressor data with the archive's own selection rule, without changing
any recorded number.

Coordinate under test
---------------------
``MEP_eff = 10 bar x MEP / MEP_design(refrigerant)``, with ``MEP_design`` the suction MEP of the refrigerant at the
standard compressor rating condition (7.2 degC evaporating and 54.4 degC condensing dew point, 35 degC return gas;
:func:`.thermo.state_k_mep`). The suction density is scaled by the same factor, so ``rho / MEP`` -- the coordinate
of the port-flow term ``qf`` -- is unchanged and only the MEP-carrying terms move. The 10 bar keeps the scaled MEP
of the order of the absolute one, so the parameter bounds of :data:`.model.BOUNDS` mean the same. Only the rotary
rows are rescaled (the scroll fit does not enter :func:`.variants.run_variants`).

Rule
----
:func:`.variants.run_variants` unchanged on both tables: backward elimination from every candidate rotary term on the
LOCO shape RMSE for each data arrangement (variant ``a`` Shao-informed, ``b`` Ossorio-anchored), then the explicit
high-speed term rule. On the absolute table this *is* the archive's run, so its rows must equal
``rotary_speed_variants_cv.csv`` (checked in ``tests/test_compressor_semi_empirical.py``).

Outputs (in the archive folder; the archive's own files are not touched)
------------------------------------------------------------------------
``rotary_load_coordinate_cv.csv``
    The CV table of :func:`.variants.run_variants` for both coordinates (``coordinate`` = ``absolute`` /
    ``design_point``), with the terms each variant's rule chose (``chosen``) and the parameters at a bound of
    :data:`.model.BOUNDS` (``at_bound``).
``rotary_load_coordinate_design_mep.csv``
    ``MEP_design`` and the scale factor per refrigerant of the rotary rows.

Needs the same inputs as the archive run (including the untracked Copeland point file, because
:func:`.assemble.build` reads every source)::

    uv run python -m validation.compressor_maps.semi_empirical.load_coordinate
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from validation.compressor_maps.semi_empirical import assemble
from validation.compressor_maps.semi_empirical import inverter as inv
from validation.compressor_maps.semi_empirical import variants as V
from validation.compressor_maps.semi_empirical.model import BOUNDS
from validation.compressor_maps.semi_empirical.run import ARCHIVE
from validation.compressor_maps.semi_empirical.thermo import state_k_mep

#: Standard compressor rating condition: evaporating / condensing dew point [degC], return gas [degC].
DESIGN_T_EVAP_C = 7.2
DESIGN_T_COND_C = 54.4
DESIGN_T_SUCTION_C = 35.0
#: The scaled MEP is expressed in units of this pressure [Pa] (10 bar).
MEP_UNIT_PA = 1.0e6

OUT_CV = ARCHIVE / "rotary_load_coordinate_cv.csv"
OUT_DESIGN = ARCHIVE / "rotary_load_coordinate_design_mep.csv"


def design_mep(refrigerant: str) -> float:
    """Suction MEP ``rho_1 dh_is`` [Pa] at the standard compressor rating condition."""
    return float(state_k_mep(refrigerant, DESIGN_T_EVAP_C, DESIGN_T_COND_C, DESIGN_T_SUCTION_C - DESIGN_T_EVAP_C)[2])


def design_point_table(d: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(fit table with the rotary MEP and suction density in per-unit of the design point, factor table)."""
    out = d.copy()
    rot = out.family == "rotary"
    refs = sorted(out.loc[rot, "refrigerant"].unique())
    mep = {r: design_mep(r) for r in refs}
    factor = out.loc[rot, "refrigerant"].map(mep).rdiv(MEP_UNIT_PA)
    out.loc[rot, "mep"] = out.loc[rot, "mep"] * factor
    out.loc[rot, "rho_suc"] = out.loc[rot, "rho_suc"] * factor
    table = pd.DataFrame(
        {
            "refrigerant": refs,
            "design_mep_bar": [mep[r] / 1e5 for r in refs],
            "scale_factor": [MEP_UNIT_PA / mep[r] for r in refs],
            "rotary_rows": [int(((d.family == "rotary") & (d.refrigerant == r)).sum()) for r in refs],
        }
    )
    return out, table


def _at_bound(parameters: str) -> str:
    """Names of the parameters of a CV row's ``parameters`` string that sit on a bound of :data:`BOUNDS`."""
    hits = []
    for item in parameters.split("; "):
        name, value = item.split("=")
        lo, hi = BOUNDS[name]
        x = float(value)
        if np.isclose(x, lo, rtol=0, atol=1e-6 * max(1.0, abs(hi))) or np.isclose(x, hi, rtol=1e-4):
            hits.append(name)
    return " ".join(hits)


def coordinate_rows(d: pd.DataFrame, coordinate: str) -> pd.DataFrame:
    """The variants' CV table on one fit table, with the chosen candidate and the bound flags."""
    result, table, _, _ = V.run_variants(d)
    table = table.copy()
    chosen = {v: tuple(r["terms"]) for v, r in result["variants"].items()}
    table.insert(0, "coordinate", coordinate)
    table["chosen"] = [
        tuple(form.split(":")[1].split("+")) == chosen[v] for v, form in zip(table.variant, table.form, strict=True)
    ]
    table["at_bound"] = table.parameters.map(_at_bound)
    return table


def run(d: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    scaled, factors = design_point_table(d)
    cv = pd.concat([coordinate_rows(d, "absolute"), coordinate_rows(scaled, "design_point")], ignore_index=True)
    return cv, factors


def main() -> int:
    d = assemble.build(inv.default_model(inv.load()))
    cv, factors = run(d)
    cv.to_csv(OUT_CV, index=False, float_format="%.6g")
    factors.to_csv(OUT_DESIGN, index=False, float_format="%.6g")
    cols = ["coordinate", "variant", "candidate", "form", "loco_shape_all", "loso_shape_mean", "edge_bias_low_speed"]
    print(factors.to_string(index=False))
    print(cv[[*cols, "parameters", "chosen", "at_bound"]].to_string(index=False))
    print(f"wrote {OUT_CV.name}, {OUT_DESIGN.name} in {ARCHIVE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
