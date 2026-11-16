"""State quantities the semi-empirical forms use: effective exponent k, isentropic MEP and the scroll work ratio phi.

* ``MEP_s = rho_1 * w_s`` [Pa], the isentropic mean effective pressure (``w_s = h_2s - h_1``).
* ``k``: the exponent of the ideal-gas isentropic work that equals the real-gas one at the row's PR,
  ``k/(k-1) (PR^((k-1)/k) - 1) = w_s rho_1 / p_1``.
* ``phi(PR; rv, k)``: work of isentropic compression to the built-in volume ratio ``rv`` plus isochoric
  compression (or expansion) to the discharge pressure, over the isentropic work. ``phi >= 1``, with equality
  at ``PR = rv^k``: over-compression below it, under-compression above it.

As in ``prototype/p01_scroll_semi_empirical.py``.
"""

from __future__ import annotations

from functools import cache

import CoolProp.CoolProp as CP
import numpy as np
from scipy.optimize import brentq

K_LO, K_HI = 0.80, 2.0
FLUIDS = {
    "R454B": "HEOS::R32[0.829248]&R1234yf[0.170752]",
    "R452B": "HEOS::R32[0.7124]&R125[0.0616]&R1234yf[0.2260]",
}


def fluid_of(refrigerant: str) -> str:
    return FLUIDS.get(refrigerant, refrigerant)


def _pow_term(e, log_x):
    """(x^e - 1) / e, continuous through e = 0 (where it is ln x)."""
    e = np.asarray(e, float)
    safe = np.where(np.abs(e) < 1e-9, 1e-9, e)
    return np.expm1(safe * log_x) / safe


def _work_ratio(k: float, pr: float) -> float:
    return float(_pow_term((k - 1.0) / k, np.log(pr)))


def k_effective(pr: np.ndarray, x: np.ndarray) -> np.ndarray:
    """Solve k/(k-1)(PR^((k-1)/k)-1) = x for each row (x = w_s rho_1 / p_1). NaN where no root in [K_LO, K_HI].

    Near saturation at high suction pressure the real-gas work falls below p_1 ln PR / rho_1, which needs k < 1;
    the expression is continuous through k = 1, so the bracket includes it.
    """
    out = np.full(len(pr), np.nan)
    for i, (p, xi) in enumerate(zip(pr, x, strict=True)):
        if not (np.isfinite(p) and np.isfinite(xi)) or p <= 1.0:
            continue
        f_lo, f_hi = _work_ratio(K_LO, p) - xi, _work_ratio(K_HI, p) - xi
        if f_lo * f_hi > 0:
            continue
        out[i] = brentq(lambda k, p=p, xi=xi: _work_ratio(k, p) - xi, K_LO, K_HI, xtol=1e-12)
    return out


def isentropic_work(pr: np.ndarray, k: np.ndarray) -> np.ndarray:
    """k/(k-1)(PR^((k-1)/k) - 1): isentropic work over p_1 v_1."""
    return _pow_term((k - 1.0) / k, np.log(pr))


def phi(pr: np.ndarray, k: np.ndarray, rv: float) -> np.ndarray:
    ws = isentropic_work(pr, k)
    wi = k * _pow_term(k - 1.0, np.log(rv)) + (pr / rv - rv ** (k - 1.0))
    return wi / ws


def dphi_drv(pr: np.ndarray, k: np.ndarray, rv: float) -> np.ndarray:
    return (rv ** (k - 2.0) - pr / rv**2) / isentropic_work(pr, k)


@cache
def _props(out: str, a: str, av: float, b: str, bv: float, fluid: str) -> float:
    return float(CP.PropsSI(out, a, av, b, bv, fluid))


def h_isentropic(p: float, s: float, fluid: str, t_lo: float) -> float:
    if "&" not in fluid:
        return _props("H", "P", p, "S", s, fluid)
    lo, hi = t_lo, t_lo + 150.0
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if _props("S", "P", p, "T", mid, fluid) < s:
            lo = mid
        else:
            hi = mid
    return _props("H", "P", p, "T", 0.5 * (lo + hi), fluid)


def suction_state(refrigerant: str, t_evap_c: float, t_cond_c: float, t_suc_c: float) -> dict[str, float]:
    """Dew-point pressures, suction state and isentropic discharge for one condition (pure or HEOS fluid)."""
    f = fluid_of(refrigerant)
    te, tc = t_evap_c + 273.15, t_cond_c + 273.15
    p1 = _props("P", "T", te, "Q", 1, f)
    p2 = _props("P", "T", tc, "Q", 1, f)
    t1 = max(t_suc_c + 273.15, te + 0.3)
    h1 = _props("H", "P", p1, "T", t1, f)
    s1 = _props("S", "P", p1, "T", t1, f)
    rho1 = _props("D", "P", p1, "T", t1, f)
    h2s = h_isentropic(p2, s1, f, tc)
    return {"p_suc": p1, "p_dis": p2, "PR": p2 / p1, "h1": h1, "rho1": rho1, "dh_is": h2s - h1, "fluid": f}


def state_k_mep(refrigerant: str, t_evap_c: float, t_cond_c: float, superheat_k: float) -> tuple[float, float, float]:
    """(PR, k, MEP_s) at a dew-point condition -- for model evaluation on a grid."""
    st = suction_state(refrigerant, t_evap_c, t_cond_c, t_evap_c + superheat_k)
    x = st["dh_is"] * st["rho1"] / st["p_suc"]
    k = float(k_effective(np.array([st["PR"]]), np.array([x]))[0])
    return st["PR"], k, st["rho1"] * st["dh_is"]


def state_at_pr(refrigerant: str, pr: float, t_cond_c: float, superheat_k: float) -> tuple[float, float, float, float]:
    """(T_evap [C], k, MEP_s, rho_1) for a given PR at a fixed condensing dew point."""
    f = fluid_of(refrigerant)
    p2 = _props("P", "T", t_cond_c + 273.15, "Q", 1, f)
    te = _props("T", "P", p2 / pr, "Q", 1, f) - 273.15
    st = suction_state(refrigerant, te, t_cond_c, te + superheat_k)
    x = st["dh_is"] * st["rho1"] / st["p_suc"]
    k = float(k_effective(np.array([st["PR"]]), np.array([x]))[0])
    return te, k, st["rho1"] * st["dh_is"], st["rho1"]
