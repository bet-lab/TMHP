"""The prototype numbers as a special case (DESIGN 9.3, ``prototype/p03.log``).

p01/p03 fitted ``1/eta_is = a phi + (q0/n + q2 n) 1e5/MEP_s + cL (PR - 1)/n`` (n = N/50) by plain least squares to
the discharge-temperature eta_is of the tracked BITZER rows (2,467 valid, PR 2.29-7.15) and predicted the
never-seen low-lift rows (1,432, PR 1.50-3.19: bias +0.019, RMSE 0.040) and the PR 7.67 rows (24: bias -0.020).
``reproduce`` repeats exactly that on the rows this pipeline derives; ``pipeline_variant`` does the same split
with this pipeline's estimator (eta_oi, unit levels, Huber, machine weights, the exclusion rules).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import least_squares

from validation.compressor_maps.semi_empirical.model import Data, SemiEmpirical
from validation.compressor_maps.semi_empirical.thermo import phi

P0 = np.array([2.5, 1.1, 0.3, 0.1, 0.01])
LO = np.array([1.2, 1.0, 0.0, 0.0, 0.0])
HI = np.array([5.0, 2.0, 5.0, 5.0, 0.3])


def _semi(p, t):
    rv, a, q0, q2, cl = p
    n = t.N_rps.to_numpy(float) / 50.0
    pr, k, mep = t.PR.to_numpy(float), t.k.to_numpy(float), t.mep.to_numpy(float)
    return 1.0 / (a * phi(pr, k, rv) + (q0 / n + q2 * n) * 1e5 / mep + cl * (pr - 1.0) / n)


def _stats(e: np.ndarray) -> dict:
    return {"rows": int(len(e)), "bias": float(np.mean(e)), "rmse": float(np.sqrt(np.mean(e**2)))}


def reproduce(d: pd.DataFrame) -> dict:
    b = d[(d.source_id == "bitzer") & ~d.reason_oi.str.startswith("refused")]
    tr, lo, hi = (b[b.bitzer_set == s] for s in ("tracked", "lowlift", "highpr"))
    r = least_squares(lambda p: _semi(p, tr) - tr.eta_isen_t.to_numpy(float), P0, bounds=(LO, HI))
    return {
        "target": "eta_is (discharge temperature), plain least squares, n = N/50, as p01/p03",
        "train_rows": int(len(tr)),
        "params_rv_a_q0_q2_cL": r.x.tolist(),
        "lowlift": _stats(_semi(r.x, lo) - lo.eta_isen_t.to_numpy(float)),
        "highpr": _stats(_semi(r.x, hi) - hi.eta_isen_t.to_numpy(float)),
        "p03_log": {
            "params": [2.8236, 1.1331, 0.0, 0.9001, 0.0588],
            "lowlift": [0.0192, 0.0400],
            "highpr": [-0.0204, 0.0278],
        },
    }


def pipeline_variant(d: pd.DataFrame, terms: tuple[str, ...] = ("q0", "q2", "cL")) -> dict:
    b = d[(d.source_id == "bitzer") & d.use_oi]
    tr = b[b.bitzer_set == "tracked"]
    f = SemiEmpirical("scroll", terms, "abs")
    st = f.fit(Data.of(tr, "abs"))
    out = {
        "form": f.name,
        "train_rows": int(len(tr)),
        "params": dict(zip(f.shape_names, st.theta.tolist(), strict=True)),
    }
    for tag in ("lowlift", "highpr"):
        t = b[b.bitzer_set == tag]
        dd = Data.of(t, "abs")
        e = f.predict(st, dd, {k: st.levels[k] for k in dd.units}) - dd.y
        out[tag] = _stats(e)
    return out
