"""Model forms and their fits: the semi-empirical eta_oi forms, the eta_v form and the shipped-form baseline.

eta_oi (terminal basis, power-identified), one level ``a_u`` per unit (machine x refrigerant x campaign)::

    scroll  1/eta_oi = a_u phi(PR; rv, k) + (q0/n + q1 + q2 n) 1e5/MEP_s + cL (PR - 1)/n
    rotary  1/eta_oi = a_u                + (q0/n + q1 + q2 n) 1e5/MEP_s + cL (PR - 1)/n

The loss terms are losses per unit of isentropic power: a loss power ``C0 + C1 N + C2 N^2`` divided by
``m_dot w_s ~ V N MEP_s`` gives ``(C0/N + C1 + C2 N)/MEP_s`` -- q0 a speed-independent loss power (standby,
constant drive loss), q1 a constant torque (Coulomb friction), q2 a loss growing with N^2 (viscous friction,
flow/valve losses); q in bar. ``cL (PR - 1)/n`` is leakage that is recompressed: a mass flow set by the pressure
difference, as a share of the swept flow ~ 1/n. ``n = N/50 rev/s`` (absolute) or ``N/N_rated`` (relative).

Candidate terms beyond the brief's starting form (each kept only if leave-one-machine-out error falls with it):

    q1  constant friction torque                         q1 1e5/MEP_s
    qf  port/valve flow loss, pressure drop ~ rho N^2    qf n^2 1e4 rho_1/MEP_s   (= qf n^2 1e4 / w_s)
    cu  motor copper loss ~ torque^2                     cu (MEP_s/1e6)/n
    cH  suction-gas heating, ~ the discharge-suction temperature rise (high-side shell rotaries)
                                                         cH (PR^((k-1)/k) - 1)
    zv  scroll only: share of the over-compression loss relieved below PR = rv^k (discharge check / dynamic
        valve): the base becomes phi - zv (phi - 1) where PR < rv^k

``a_u >= 1`` and every loss coefficient >= 0, with ``phi - zv (phi - 1) >= 1``, make ``0 < eta_oi <= 1`` a
property of the form.

Level form ``scaled`` (an "equivalent" machine factor): ``1/eta_oi = base + a_u L`` with ``q2 = 1`` as the
normaliser -- a machine's losses scale together instead of sharing absolute loss coefficients. ``a_u >= 0`` keeps
``0 < eta_oi <= 1``. (A form ``base + a_u (1 + L)`` was tried first; its fit drove the constant to zero and the loss
coefficients to their bounds, i.e. it degenerates to this one.)

eta_v (same units), the archive's form since the 2026-11-16 revision (:class:`EtaVBounded`, positive by
construction)::

    eta_v = c0_u exp(-c_re (PR^(1/k) - 1)) / (1 + c_L (PR - 1)/n + c_s/n + c_f n^2)

and the first archive's linear form, kept for comparison (:class:`EtaV`; crosses zero at high PR and low n)::

    eta_v = c0_u - c_re (PR^(1/k) - 1) - c_Lv (PR - 1)/n          (V1; V0 fixes c0_u = 1)

Baseline ("shipped form"): ``eta = b0_u + b1 PR + b2 N + b3 PR^2 + b4 N^2 + b5 PR N`` with N in rev/s and the same
unit intercepts, robust loss and weights.

All fits minimise ``sum_i w_i rho(eta_pred_i - eta_i)`` with the Huber function ``rho`` (threshold 0.05 in
efficiency, DESIGN 4.1) -- implemented exactly by the residual transform ``psi`` below, so one least-squares call
carries both the weights and the robust loss.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.optimize import least_squares

from validation.compressor_maps.semi_empirical.thermo import dphi_drv, phi

HUBER = 0.05
N_REF = 50.0  # rev/s, absolute speed coordinate
TERMS = ("q0", "q1", "q2", "cL", "qf", "cu", "cH", "zv")
BOUNDS = {
    "rv": (1.2, 5.0),
    "q0": (0.0, 20.0),
    "q1": (0.0, 20.0),
    "q2": (0.0, 20.0),
    "cL": (0.0, 2.0),
    "qf": (0.0, 20.0),
    "cu": (0.0, 20.0),
    "cH": (0.0, 20.0),
    "zv": (0.0, 1.0),
}
START = {"rv": 2.7, "q0": 0.1, "q1": 0.1, "q2": 0.5, "cL": 0.05, "qf": 0.1, "cu": 0.1, "cH": 0.1, "zv": 0.3}
A_BOUNDS = {"add": (1.0, 3.0), "scaled": (0.0, 10.0)}


def psi(r: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Huber residual transform: psi^2 = 2 rho(r); returns psi and d psi / d r."""
    a = np.abs(r)
    big = a > HUBER
    root = np.sqrt(np.maximum(2.0 * HUBER * a - HUBER**2, 1e-300))
    p = np.where(big, np.sign(r) * root, r)
    dp = np.where(big, HUBER / root, 1.0)
    return p, dp


def machine_weights(df: pd.DataFrame) -> np.ndarray:
    """Each machine 1; units equal within a machine, speed records equal within a unit, rows equal within a record."""
    n_units = df.groupby("machine").unit.transform("nunique")
    n_rec = df.groupby("unit").record.transform("nunique")
    n_rows = df.groupby("record").record.transform("size")
    return (1.0 / (n_units * n_rec * n_rows)).to_numpy(float)


@dataclass
class Data:
    pr: np.ndarray
    k: np.ndarray
    mep: np.ndarray
    rho: np.ndarray
    n: np.ndarray
    N: np.ndarray
    y: np.ndarray
    w: np.ndarray
    u: np.ndarray
    units: list[str]

    @classmethod
    def of(cls, df: pd.DataFrame, speed: str, ycol: str = "eta_oi", w: np.ndarray | None = None) -> Data:
        units = sorted(df.unit.unique())
        code = {k: i for i, k in enumerate(units)}
        N = df.N_rps.to_numpy(float)
        n = N / N_REF if speed == "abs" else N / df.N_rated_rps.to_numpy(float)
        return cls(
            pr=df.PR.to_numpy(float),
            k=df.k.to_numpy(float),
            mep=df.mep.to_numpy(float),
            rho=df.rho_suc.to_numpy(float),
            n=n,
            N=N,
            y=df[ycol].to_numpy(float),
            w=machine_weights(df) if w is None else w,
            u=df.unit.map(code).to_numpy(int),
            units=units,
        )


@dataclass
class State:
    """Fitted shape (``theta``) and one level per unit."""

    theta: np.ndarray
    levels: dict[str, float] = field(default_factory=dict)

    def level_array(self, data: Data, default: float) -> np.ndarray:
        return np.array([self.levels.get(k, default) for k in data.units])


# ---------------------------------------------------------------------------------------------------- semi-empirical
@dataclass(frozen=True)
class SemiEmpirical:
    family: str
    terms: tuple[str, ...]
    speed: str  # abs | rel
    level: str = "add"  # add: a_u*base + L ; scaled: base + a_u*L with q2 = 1
    kind: str = "semi"

    @property
    def name(self) -> str:
        tag = "" if self.level == "add" else ":scaled"
        return f"{self.family}:{'+'.join(self.terms)}:{self.speed}{tag}"

    def _D(self, a_rows: np.ndarray, base: np.ndarray, loss: np.ndarray) -> np.ndarray:
        return a_rows * base + loss if self.level == "add" else base + a_rows * loss

    def _dD_da(self, base: np.ndarray, loss: np.ndarray) -> np.ndarray:
        return base if self.level == "add" else loss

    def _a0(self, y: np.ndarray, base: np.ndarray, loss: np.ndarray) -> float:
        if self.level == "add":
            return float(np.median((1.0 / y - loss) / base))
        return float(np.median((1.0 / y - base) / loss))

    @property
    def shape_names(self) -> list[str]:
        free = [t for t in self.terms if not (self.level == "scaled" and t == "q2")]
        return (["rv"] if self.family == "scroll" else []) + free

    def _unpack(self, th: np.ndarray) -> dict[str, float]:
        p = dict.fromkeys(BOUNDS, 0.0)
        if self.level == "scaled":
            p["q2"] = 1.0  # normaliser: the unit level a_u carries the loss scale
        p.update(zip(self.shape_names, th, strict=True))
        return p

    def _base_loss(self, th: np.ndarray, d: Data) -> tuple[np.ndarray, np.ndarray, dict]:
        p = self._unpack(th)
        if self.family == "scroll":
            ph = phi(d.pr, d.k, p["rv"])
            base = ph - p["zv"] * (ph - 1.0) * self._over(d, p["rv"])
        else:
            base = np.ones_like(d.pr)
        loss = (p["q0"] / d.n + p["q1"] + p["q2"] * d.n) * 1e5 / d.mep + p["cL"] * (d.pr - 1.0) / d.n
        loss = loss + p["qf"] * d.n**2 * 1e4 * d.rho / d.mep + p["cu"] * d.mep / 1e6 / d.n
        loss = loss + p["cH"] * (d.pr ** ((d.k - 1.0) / d.k) - 1.0)
        return base, loss, p

    @staticmethod
    def _over(d: Data, rv: float) -> np.ndarray:
        """1 where the scroll over-compresses (PR below rv^k), else 0."""
        return (d.pr < rv**d.k).astype(float)

    def eta(self, th: np.ndarray, a: np.ndarray, d: Data) -> np.ndarray:
        base, loss, _ = self._base_loss(th, d)
        return 1.0 / self._D(a[d.u], base, loss)

    def _dD(self, th: np.ndarray, a: np.ndarray, d: Data) -> np.ndarray:
        """dD/dtheta for the shape parameters, columns in shape_names order."""
        p = self._unpack(th)
        fb, fl = (a[d.u], 1.0) if self.level == "add" else (1.0, a[d.u])
        cols = []
        for name in self.shape_names:
            if name == "rv":
                cols.append(fb * (1.0 - p["zv"] * self._over(d, p["rv"])) * dphi_drv(d.pr, d.k, p["rv"]))
                continue
            if name == "zv":
                cols.append(-fb * (phi(d.pr, d.k, p["rv"]) - 1.0) * self._over(d, p["rv"]))
                continue
            if name == "qf":
                c = d.n**2 * 1e4 * d.rho / d.mep
            elif name == "cu":
                c = d.mep / 1e6 / d.n
            elif name == "cH":
                c = d.pr ** ((d.k - 1.0) / d.k) - 1.0
            elif name == "q0":
                c = 1e5 / (d.n * d.mep)
            elif name == "q1":
                c = 1e5 / d.mep
            elif name == "q2":
                c = 1e5 * d.n / d.mep
            else:  # cL
                c = (d.pr - 1.0) / d.n
            cols.append(fl * c)
        return np.column_stack(cols) if cols else np.zeros((len(d.pr), 0))

    def fit(self, d: Data, warm: State | None = None) -> State:
        ns, nu = len(self.shape_names), len(d.units)
        th0 = np.array([START[s] for s in self.shape_names])
        if warm is not None and len(warm.theta) == ns:
            th0 = np.clip(
                warm.theta, [BOUNDS[s][0] for s in self.shape_names], [BOUNDS[s][1] for s in self.shape_names]
            )
        base, loss, _ = self._base_loss(th0, d)
        a0 = np.empty(nu)
        for i in range(nu):
            m = d.u == i
            a0[i] = self._a0(d.y[m], base[m], loss[m])
        if warm is not None:
            a0 = np.array([warm.levels.get(k, v) for k, v in zip(d.units, a0, strict=True)])
        ab = A_BOUNDS[self.level]
        a0 = np.clip(a0, ab[0] + 1e-9, ab[1] - 1e-9)
        sw = np.sqrt(d.w)
        lo = np.r_[[BOUNDS[s][0] for s in self.shape_names], np.full(nu, ab[0])]
        hi = np.r_[[BOUNDS[s][1] for s in self.shape_names], np.full(nu, ab[1])]
        x0 = np.clip(np.r_[th0, a0], lo + 1e-12, hi - 1e-12)

        def fun(x):
            e = self.eta(x[:ns], x[ns:], d)
            return sw * psi(e - d.y)[0]

        def jac(x):
            th, a = x[:ns], x[ns:]
            base, loss, _ = self._base_loss(th, d)
            e = 1.0 / self._D(a[d.u], base, loss)
            dp = psi(e - d.y)[1]
            g = -(e**2) * dp * sw
            J = np.zeros((len(d.y), ns + nu))
            J[:, :ns] = g[:, None] * self._dD(th, a, d)
            J[np.arange(len(d.y)), ns + d.u] = g * self._dD_da(base, loss)
            return J

        r = least_squares(fun, x0, jac=jac, bounds=(lo, hi), method="trf", x_scale="jac", max_nfev=400)
        return State(r.x[:ns].copy(), dict(zip(d.units, r.x[ns:].tolist(), strict=True)))

    def fit_levels(self, st: State, d: Data) -> dict[str, float]:
        """Levels of the units in ``d`` with the shape fixed (the level-free prediction of a held-out machine)."""
        base, loss, _ = self._base_loss(st.theta, d)
        ab = A_BOUNDS[self.level]
        out = {}
        for i, name in enumerate(d.units):
            m = d.u == i
            b, lo_, y, w = base[m], loss[m], d.y[m], np.sqrt(d.w[m])
            a0 = float(np.clip(self._a0(y, b, lo_), ab[0] + 1e-9, ab[1] - 1e-9))

            def fun(a, b=b, lo_=lo_, y=y, w=w):
                return w * psi(1.0 / self._D(a[0], b, lo_) - y)[0]

            r = least_squares(fun, [a0], bounds=([ab[0]], [ab[1]]))
            out[name] = float(r.x[0])
        return out

    def predict(self, st: State, d: Data, levels: dict[str, float] | float) -> np.ndarray:
        a = np.array([levels[k] for k in d.units]) if isinstance(levels, dict) else np.full(len(d.units), float(levels))
        return self.eta(st.theta, a, d)

    def population_level(self, st: State, units: list[str] | None = None) -> float:
        vals = [v for k, v in st.levels.items() if units is None or k in units]
        return float(np.median(vals))


# ---------------------------------------------------------------------------------------------------- linear (IRLS)
def _irls(X: np.ndarray, u: np.ndarray, nu: int, y: np.ndarray, w: np.ndarray, intercepts: bool, offset: np.ndarray):
    """Weighted Huber linear fit with optional unit intercepts. Returns (beta, intercepts)."""
    D = np.zeros((len(y), nu if intercepts else 0))
    if intercepts:
        D[np.arange(len(y)), u] = 1.0
    A = np.column_stack([X, D])
    z = y - offset
    h = np.ones_like(y)
    coef = np.zeros(A.shape[1])
    for _ in range(50):
        sw = np.sqrt(w * h)
        coef_new = np.linalg.lstsq(A * sw[:, None], z * sw, rcond=None)[0]
        r = A @ coef_new - z
        h = np.minimum(1.0, HUBER / np.maximum(np.abs(r), 1e-12))
        if np.max(np.abs(coef_new - coef)) < 1e-10:
            coef = coef_new
            break
        coef = coef_new
    return coef[: X.shape[1]], coef[X.shape[1] :]


def _irls_levels(X, beta, u, nu, y, w, offset):
    """Unit intercepts with the shape fixed."""
    z = y - offset - X @ beta
    out = np.zeros(nu)
    for i in range(nu):
        m = u == i
        c = float(np.median(z[m]))
        for _ in range(50):
            r = c - z[m]
            h = w[m] * np.minimum(1.0, HUBER / np.maximum(np.abs(r), 1e-12))
            c_new = float(np.sum(h * z[m]) / np.sum(h))
            if abs(c_new - c) < 1e-12:
                break
            c = c_new
        out[i] = c
    return out


@dataclass(frozen=True)
class Quadratic:
    """Shipped functional form (quadratic in PR and absolute N) with unit intercepts."""

    family: str
    kind: str = "quadratic"
    speed: str = "abs"
    terms: tuple[str, ...] = ("PR", "N", "PR2", "N2", "PRN")

    @property
    def name(self) -> str:
        return f"{self.family}:quadratic(PR,N):abs"

    @property
    def shape_names(self) -> list[str]:
        return list(self.terms)

    @staticmethod
    def X(d: Data) -> np.ndarray:
        return np.column_stack([d.pr, d.N, d.pr**2, d.N**2, d.pr * d.N])

    def fit(self, d: Data, warm: State | None = None) -> State:
        beta, c = _irls(self.X(d), d.u, len(d.units), d.y, d.w, True, np.zeros_like(d.y))
        return State(beta, dict(zip(d.units, c.tolist(), strict=True)))

    def fit_levels(self, st: State, d: Data) -> dict[str, float]:
        c = _irls_levels(self.X(d), st.theta, d.u, len(d.units), d.y, d.w, np.zeros_like(d.y))
        return dict(zip(d.units, c.tolist(), strict=True))

    def predict(self, st: State, d: Data, levels: dict[str, float] | float) -> np.ndarray:
        c = np.array([levels[k] for k in d.units]) if isinstance(levels, dict) else np.full(len(d.units), levels)
        return self.X(d) @ st.theta + c[d.u]

    def population_level(self, st: State, units: list[str] | None = None) -> float:
        vals = [v for k, v in st.levels.items() if units is None or k in units]
        return float(np.median(vals))


@dataclass(frozen=True)
class EtaV:
    """eta_v = c0_u - c_re (PR^(1/k) - 1) - c_Lv (PR - 1)/n; ``free_c0`` False fixes c0 = 1 (V0)."""

    family: str
    speed: str
    free_c0: bool = True
    kind: str = "eta_v"

    @property
    def name(self) -> str:
        return f"{self.family}:eta_v:{'V1' if self.free_c0 else 'V0'}:{self.speed}"

    @property
    def shape_names(self) -> list[str]:
        return ["c_re", "c_Lv"]

    @staticmethod
    def X(d: Data) -> np.ndarray:
        return np.column_stack([-(d.pr ** (1.0 / d.k) - 1.0), -(d.pr - 1.0) / d.n])

    def fit(self, d: Data, warm: State | None = None) -> State:
        if self.free_c0:
            beta, c = _irls(self.X(d), d.u, len(d.units), d.y, d.w, True, np.zeros_like(d.y))
            return State(beta, dict(zip(d.units, c.tolist(), strict=True)))
        beta, _ = _irls(self.X(d), d.u, len(d.units), d.y, d.w, False, np.ones_like(d.y))
        return State(beta, dict.fromkeys(d.units, 1.0))

    def fit_levels(self, st: State, d: Data) -> dict[str, float]:
        if not self.free_c0:
            return dict.fromkeys(d.units, 1.0)
        c = _irls_levels(self.X(d), st.theta, d.u, len(d.units), d.y, d.w, np.zeros_like(d.y))
        return dict(zip(d.units, c.tolist(), strict=True))

    def predict(self, st: State, d: Data, levels: dict[str, float] | float) -> np.ndarray:
        c = np.array([levels[k] for k in d.units]) if isinstance(levels, dict) else np.full(len(d.units), levels)
        return self.X(d) @ st.theta + c[d.u]

    def population_level(self, st: State, units: list[str] | None = None) -> float:
        vals = [v for k, v in st.levels.items() if units is None or k in units]
        return float(np.median(vals)) if vals else 1.0


#: Bounds of the bounded eta_v shape parameters (all >= 0: each term can only lower eta_v).
ETA_V_BOUNDS = {"c_re": (0.0, 0.5), "c_L": (0.0, 5.0), "c_s": (0.0, 5.0), "c_f": (0.0, 5.0)}
ETA_V_START = {"c_re": 0.02, "c_L": 0.02, "c_s": 0.05, "c_f": 0.01}
ETA_V_TERMS = ("c_re", "c_L", "c_s", "c_f")
ETA_V_LEVEL_BOUNDS = (0.2, 2.0)


@dataclass(frozen=True)
class EtaVBounded:
    """Volumetric efficiency, positive and bounded by construction::

        eta_v = c0_u * exp(-c_re (PR^(1/k) - 1)) / (1 + c_L (PR - 1)/n + c_s/n + c_f n^2)

    * ``exp(-c_re (PR^(1/k) - 1))`` -- clearance re-expansion. The ideal clearance law is
      ``1 - c_re (PR^(1/k) - 1)`` (``c_re`` the clearance volume over the swept volume); the exponential is the same
      to first order in ``c_re (PR^(1/k) - 1)`` (0.787 against 0.808 at PR 10, k 1.1, c_re 0.03) and stays positive
      at any PR.
    * ``1/(1 + x)`` -- competing outflows. Gas trapped in the compression chamber leaves through the discharge at a
      rate set by the swept flow (~ n) or back to suction through the leakage paths at a rate set by the pressure
      difference (``c_L (PR - 1)``); the delivered share is ``n / (n + c_L (PR - 1))``. ``c_s/n`` is a share that
      does not scale with the pressure difference (suction-gas heating: heat per kilogram ~ time per revolution);
      ``c_f n^2`` the suction-port pressure drop (~ rho N^2). To first order in ``x`` the denominator is the linear
      form ``1 - x`` of :class:`EtaV`, which crosses zero at high PR and low speed; ``1/(1 + x)`` cannot.

    Every parameter is >= 0, so eta_v falls monotonically with PR, with falling speed (below the port-drop optimum)
    and is in ``(0, c0_u]`` for every PR >= 1 and n > 0. Terms not in ``terms`` are zero.
    """

    family: str
    speed: str
    terms: tuple[str, ...] = ("c_re", "c_L")
    kind: str = "eta_v_bounded"

    @property
    def name(self) -> str:
        return f"{self.family}:eta_v_bounded:{'+'.join(self.terms)}:{self.speed}"

    @property
    def shape_names(self) -> list[str]:
        return list(self.terms)

    def _parts(self, th: np.ndarray, d: Data) -> tuple[np.ndarray, np.ndarray, dict[str, np.ndarray], dict]:
        p = dict.fromkeys(ETA_V_TERMS, 0.0)
        p.update(zip(self.terms, th, strict=True))
        u = d.pr ** (1.0 / d.k) - 1.0
        cols = {"c_L": (d.pr - 1.0) / d.n, "c_s": 1.0 / d.n, "c_f": d.n**2}
        den = 1.0 + sum(p[t] * c for t, c in cols.items())
        clr = np.exp(-p["c_re"] * u)
        return clr, den, {"c_re": u, **cols}, p

    def shape(self, th: np.ndarray, d: Data) -> np.ndarray:
        clr, den, _, _ = self._parts(th, d)
        return clr / den

    def eta(self, th: np.ndarray, c0: np.ndarray, d: Data) -> np.ndarray:
        return c0[d.u] * self.shape(th, d)

    def fit(self, d: Data, warm: State | None = None) -> State:
        ns, nu = len(self.terms), len(d.units)
        th0 = np.array([ETA_V_START[t] for t in self.terms])
        if warm is not None and len(warm.theta) == ns:
            th0 = np.clip(
                warm.theta, [ETA_V_BOUNDS[t][0] for t in self.terms], [ETA_V_BOUNDS[t][1] for t in self.terms]
            )
        s0 = self.shape(th0, d)
        c00 = np.array([np.median(d.y[d.u == i] / s0[d.u == i]) for i in range(nu)])
        if warm is not None:
            c00 = np.array([warm.levels.get(k, v) for k, v in zip(d.units, c00, strict=True)])
        lo = np.r_[[ETA_V_BOUNDS[t][0] for t in self.terms], np.full(nu, ETA_V_LEVEL_BOUNDS[0])]
        hi = np.r_[[ETA_V_BOUNDS[t][1] for t in self.terms], np.full(nu, ETA_V_LEVEL_BOUNDS[1])]
        x0 = np.clip(np.r_[th0, c00], lo + 1e-12, hi - 1e-12)
        sw = np.sqrt(d.w)

        def fun(x):
            return sw * psi(self.eta(x[:ns], x[ns:], d) - d.y)[0]

        def jac(x):
            th, c0 = x[:ns], x[ns:]
            clr, den, cols, _ = self._parts(th, d)
            e = c0[d.u] * clr / den
            g = psi(e - d.y)[1] * sw
            J = np.zeros((len(d.y), ns + nu))
            for j, t in enumerate(self.terms):
                J[:, j] = g * (-cols[t] * e if t == "c_re" else -e * cols[t] / den)
            J[np.arange(len(d.y)), ns + d.u] = g * clr / den
            return J

        r = least_squares(fun, x0, jac=jac, bounds=(lo, hi), method="trf", x_scale="jac", max_nfev=400)
        return State(r.x[:ns].copy(), dict(zip(d.units, r.x[ns:].tolist(), strict=True)))

    def fit_levels(self, st: State, d: Data) -> dict[str, float]:
        """Level of each unit with the shape fixed (weighted Huber, one parameter per unit)."""
        s = self.shape(st.theta, d)
        out = {}
        for i, name in enumerate(d.units):
            m = d.u == i
            y, sm, w = d.y[m], s[m], np.sqrt(d.w[m])
            c0 = float(np.clip(np.median(y / sm), ETA_V_LEVEL_BOUNDS[0] + 1e-9, ETA_V_LEVEL_BOUNDS[1] - 1e-9))

            def fun(c, y=y, sm=sm, w=w):
                return w * psi(c[0] * sm - y)[0]

            r = least_squares(fun, [c0], bounds=([ETA_V_LEVEL_BOUNDS[0]], [ETA_V_LEVEL_BOUNDS[1]]))
            out[name] = float(r.x[0])
        return out

    def predict(self, st: State, d: Data, levels: dict[str, float] | float) -> np.ndarray:
        c = np.array([levels[k] for k in d.units]) if isinstance(levels, dict) else np.full(len(d.units), levels)
        return self.eta(st.theta, c, d)

    def population_level(self, st: State, units: list[str] | None = None) -> float:
        vals = [v for k, v in st.levels.items() if units is None or k in units]
        return float(np.median(vals)) if vals else 1.0
