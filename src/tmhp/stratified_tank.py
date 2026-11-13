"""Multi-node (1-D finite-volume) stratified thermal-storage tank.

Implements the multi-node model of Cadau et al. (*Development and Analysis of a
Multi-Node Dynamic Model for the Simulation of Stratified Thermal Energy
Storage*, Energies 2019, 12:4275): the tank is divided into ``N`` vertically
stacked nodes (node 0 = top/hottest, node N-1 = bottom/coldest), each with a
uniform temperature ``T_i``. The per-node energy balance combines

- **advection** — port inflow/outflow and inter-node vertical flow, upwinded by
  the flow direction (Cadau Eq. 4-6);
- **pseudo-conduction** — ``k·(T_{i-1}-T_i) - k·(T_i-T_{i+1})`` between
  neighbouring nodes, lumping conductive + convective exchange;
- **ambient loss** — ``UA_i·(T_i - T_amb)`` through the side wall; and
- **buoyancy (inversion) mixing** — a node warmer than the node above it rises
  and mixes instantly with it (TRNSYS Type 4/60 style), so the profile after a
  step is never inverted. See :func:`mix_inversions`.

It is integrated **implicitly** (backward Euler) as a tridiagonal solve, so the
update is unconditionally stable and smooth in the state — a property the legacy
single-node lumped tank lacks and that an MPC-internal model needs. The lumped
fully-mixed tank is exactly the ``N=1`` limit of this model.

Numerical diffusion
-------------------
First-order implicit upwinding smears a sharp thermocline like a physical axial
diffusivity ``D_num = (v·Δz/2)·(1 + Co)`` with ``v = V̇/A`` and the Courant number
``Co = V̇·Δt/V_node``. After the front has travelled a distance ``L`` its spread
is ``σ² ≈ L·(Δz + v·Δt)``: the time step acts like an extra node thickness
``v·Δt``, which is why refining the nodes alone stops helping once ``Co > 1``.
``max_courant`` (opt-in) splits a step into equal implicit sub-steps with
``Co ≤ max_courant`` and bounds the spread at ``σ² ≈ L·Δz·(1 + max_courant)``.
``docs/tank_backends/tank_backends.md`` tabulates the effect.

Conventions
-----------
- Node index increases downward: ``T[0]`` top (hot), ``T[N-1]`` bottom (cold).
- ``charge_flow`` [m³/s] ≥ 0 (HP charging) enters the **top** node at
  ``T_charge`` and exits the **bottom** node (hot return to top, cold draw to
  HP) — a downward internal flow.
- ``draw_flow`` [m³/s] ≥ 0 (load) draws hot water from the **top** node and
  admits cold makeup ``T_makeup`` at the **bottom** node — an upward internal
  flow.
- The net inter-node flow is ``charge_flow - draw_flow`` (downward positive);
  advection is upwinded by its direction (Cadau Eq. 4-6).
- Temperatures in °C; energy balances are temperature-difference based so the
  reference cancels.
- The model is single-phase liquid water. A node pushed above ``T_boil``
  (default 100 °C, atmospheric boiling) by ``q_source`` is outside the model's
  validity: the step flags it (``overheat`` in the result) and warns with
  :class:`TankOverheatWarning`; the state is not clipped (clipping would destroy
  energy). The tank does not know its heat source — the caller must limit it.
"""

from __future__ import annotations

import math
import warnings

import numpy as np
from scipy.linalg import solve_banded

from ._validation import validate_ctor, validate_tank_step
from .constants import c_w, k_w, rho_w

#: Temperature tolerance [K] of the buoyancy check: a node counts as inverted
#: only when it is warmer than the node above by more than this. 0.1 mK is far
#: below any measurable (or buoyancy-relevant) difference and far above float
#: round-off at tank temperatures (~1e-14 K), so a uniform or exactly layered
#: profile is never "mixed" on one platform and not on another.
INVERSION_TOL_K = 1.0e-4

#: Atmospheric boiling point [°C]: upper validity limit of the liquid model.
T_BOIL_ATM_C = 100.0

#: Tolerance [K] above ``T_boil`` before a node is flagged as overheated, so a
#: heater sized to land exactly on the limit is not flagged by round-off on one
#: platform only.
OVERHEAT_TOL_K = 1.0e-6

#: Relative slack when rounding ``Co / max_courant`` up to a sub-step count, so
#: a Courant number that is an exact multiple of the limit (up to round-off)
#: gives the same count on every platform.
_SUBSTEP_SLACK = 1.0e-9


class TankOverheatWarning(RuntimeWarning):
    """A tank node exceeded the liquid-water validity limit ``T_boil``."""


def mix_inversions(T, cap, tol: float = INVERSION_TOL_K) -> tuple[np.ndarray, bool]:
    """Energy-conserving instantaneous buoyancy mixing of a top→bottom profile.

    Whenever a layer is warmer than the layer above it (by more than ``tol``)
    the two are fully mixed at their capacity-weighted mean, and the check is
    repeated against the layer above — the classic instantaneous inversion
    mixing of TRNSYS Type 4/60 (EnergyPlus ``WaterHeater:Stratified`` resolves
    the same physics with a large inversion-mixing conductance). Implemented as
    pool-adjacent-violators, so the result is the capacity-weighted isotonic
    (non-increasing) profile: every mixed block holds its block-mean, energy
    ``Σ cap·T`` is conserved, and the output satisfies
    ``T[i+1] <= T[i] + tol``.

    Parameters
    ----------
    T : array-like
        Layer temperatures, top first [°C].
    cap : array-like
        Layer heat capacities (any consistent unit, e.g. J/K or m³).
    tol : float
        Inversion tolerance [K] (see :data:`INVERSION_TOL_K`).

    Returns
    -------
    (T_mixed, mixed)
        ``T_mixed`` is the input array itself when nothing was inverted (so an
        already stable profile is returned bit-for-bit), else a new array.
    """
    T = np.asarray(T, dtype=float)
    cap = np.asarray(cap, dtype=float)
    if T.size < 2 or not np.any(T[1:] > T[:-1] + tol):
        return T, False
    vals: list[float] = []
    caps: list[float] = []
    counts: list[int] = []
    for t, c in zip(T.tolist(), cap.tolist(), strict=True):
        vals.append(t)
        caps.append(c)
        counts.append(1)
        while len(vals) > 1 and vals[-1] > vals[-2] + tol:
            v2, c2, k2 = vals.pop(), caps.pop(), counts.pop()
            c1 = caps[-1]
            vals[-1] = (c1 * vals[-1] + c2 * v2) / (c1 + c2)
            caps[-1] = c1 + c2
            counts[-1] += k2
    return np.repeat(np.asarray(vals), counts), True


def _flag_overheat(T: np.ndarray, T_boil: float, who: str) -> tuple[float, bool]:
    t_max = float(np.max(T))
    over = t_max > T_boil + OVERHEAT_TOL_K
    if over:
        warnings.warn(
            f"{who}: node temperature {t_max:.2f} °C exceeds T_boil = {T_boil:.1f} °C — the liquid-water "
            "tank model is outside its validity range; limit the heat source (q_source) in the caller",
            TankOverheatWarning,
            stacklevel=3,
        )
    return t_max, over


def _heat_source_array(q_source, n: int) -> np.ndarray:
    """Per-node heat input [W]: scalar → top node; length-``n`` array → per node."""
    q_arr = np.zeros(n)
    if q_source is None:
        return q_arr
    qs = np.asarray(q_source, dtype=float)
    if qs.ndim == 0:
        q_arr[0] = float(qs)
    elif qs.shape == (n,):
        q_arr = qs.copy()
    else:
        raise ValueError(f"q_source must be scalar or shape ({n},) — got {qs.shape}")
    if not np.all(np.isfinite(q_arr)):
        raise ValueError(f"q_source must be finite — got {q_source!r}")
    return q_arr


class StratifiedTank:
    """Multi-node stratified hot-water tank (implicit tridiagonal stepper).

    Parameters
    ----------
    n_nodes : int
        Number of vertical nodes ``N`` (``N=1`` recovers the lumped tank).
    volume : float
        Total tank volume [m³].
    height : float
        Tank height [m] (sets node thickness ``dz = height/N`` and the
        cross-sectional area ``volume/height`` for conduction).
    k_eff : float, optional
        Effective inter-node conductivity [W/m/K] (water conduction plus any
        turbulent-mixing enhancement). Defaults to water (``k_w``).
    ua : float, optional
        Total tank-to-ambient loss coefficient ``UA`` [W/K], split uniformly
        across nodes (side wall; no separate top/bottom plates). Defaults to 0
        (adiabatic).
    rho, cp : float, optional
        Water density [kg/m³] and specific heat [J/kg/K]; default to the project
        constants. Assumed constant (Cadau: density assumed constant per node).
    inversion_mixing : bool, optional
        Mix temperature inversions after every (sub-)step (default ``True``).
        ``False`` restores the pre-#96 behaviour, in which a bottom heater can
        leave the bottom hotter than the top indefinitely.
    max_courant : float or None, optional
        Opt-in sub-stepping: split each step into ``m = ceil(Co/max_courant)``
        equal implicit sub-steps, ``Co = max(charge_flow, draw_flow)·dt/V_node``.
        ``None`` (default) keeps one implicit step per call. Sub-stepping
        bounds the numerical diffusion independently of ``dt`` but makes the
        update piecewise in the flow (the sub-step count jumps at integer
        Courant numbers), so leave it off for gradient-based MPC.
    T_boil : float, optional
        Liquid-water validity limit [°C] for the overheat flag (default 100 °C;
        raise it for a pressurised tank).
    """

    def __init__(
        self,
        n_nodes: int,
        volume: float,
        height: float,
        *,
        k_eff: float = k_w,
        ua: float = 0.0,
        rho: float = rho_w,
        cp: float = c_w,
        inversion_mixing: bool = True,
        max_courant: float | None = None,
        T_boil: float = T_BOIL_ATM_C,
    ) -> None:
        validate_ctor("TANK", locals())
        if int(n_nodes) < 1:
            raise ValueError(f"n_nodes must be >= 1 — got {n_nodes}")
        if volume <= 0.0 or height <= 0.0:
            raise ValueError(f"volume and height must be > 0 — got {volume}, {height}")
        if max_courant is not None and not (math.isfinite(max_courant) and max_courant > 0.0):
            raise ValueError(f"max_courant must be None or a finite value > 0 — got {max_courant}")
        if not math.isfinite(T_boil):
            raise ValueError(f"T_boil must be finite — got {T_boil}")

        self.n = int(n_nodes)
        self.volume = float(volume)
        self.height = float(height)
        self.rho = float(rho)
        self.cp = float(cp)
        self.k_eff = float(k_eff)
        self.ua_total = float(ua)
        self.inversion_mixing = bool(inversion_mixing)
        self.max_courant = None if max_courant is None else float(max_courant)
        self.T_boil = float(T_boil)

        # Geometry / lumped node properties.
        self.area_cross = self.volume / self.height  # [m²]
        self.dz = self.height / self.n  # node thickness [m]
        self.v_node = self.volume / self.n  # node volume [m³]
        self.m_node = self.rho * self.v_node  # node mass [kg]
        self.G = self.k_eff * self.area_cross / self.dz  # inter-node conductance [W/K]
        self.ua_node = self.ua_total / self.n  # per-node loss [W/K]

        self.T: np.ndarray = np.zeros(self.n)

    # ------------------------------------------------------------------
    def reset(self, T_init) -> np.ndarray:
        """Set node temperatures (scalar = uniform, or length-N array)."""
        arr = np.asarray(T_init, dtype=float)
        if arr.ndim == 0:
            self.T = np.full(self.n, float(arr))
        else:
            if arr.shape != (self.n,):
                raise ValueError(f"T_init must be scalar or shape ({self.n},) — got {arr.shape}")
            self.T = arr.astype(float).copy()
        return self.T

    @property
    def stored_energy(self) -> float:
        """Sensible energy relative to 0 °C [J] (``Σ m_node·cp·T_i``)."""
        return float(self.m_node * self.cp * self.T.sum())

    def substeps(self, dt: float, charge_flow: float = 0.0, draw_flow: float = 0.0) -> int:
        """Number of implicit sub-steps ``step`` uses for these flows."""
        if self.max_courant is None:
            return 1
        co = max(charge_flow, draw_flow) * dt / self.v_node
        return max(1, math.ceil(co / self.max_courant - _SUBSTEP_SLACK))

    # ------------------------------------------------------------------
    def step(
        self,
        dt: float,
        *,
        charge_flow: float = 0.0,
        T_charge: float = 0.0,
        draw_flow: float = 0.0,
        T_makeup: float = 10.0,
        q_source=None,
        T_amb: float = 20.0,
    ) -> dict:
        """Advance one timestep (backward Euler, charge + draw + heat source).

        Parameters
        ----------
        dt : float
            Timestep [s].
        charge_flow : float, optional
            HP charge flow [m³/s] (hot ``T_charge`` into top, out at bottom).
        T_charge : float, optional
            Charge inlet temperature [°C] (used when ``charge_flow > 0``).
        draw_flow : float, optional
            Load draw flow [m³/s] (hot from top, cold ``T_makeup`` into bottom).
        T_makeup : float, optional
            Cold makeup temperature [°C] (used when ``draw_flow > 0``).
        q_source : float or array-like, optional
            Internal heat input [W] from an immersed heater/condenser. A scalar
            is applied to the top node; a length-``N`` array is applied per node.
        T_amb : float, optional
            Ambient temperature [°C] for the side-wall loss.

        Returns
        -------
        dict
            ``T`` (new node temperatures), ``T_top`` (top node), ``T_outlet``
            (bottom node), ``T_draw_out`` / ``T_charge_out`` (flow-weighted mean
            temperature of the water that left through the top draw port / the
            bottom charge-return port during the step [°C]; equal to the node
            temperature when that flow is zero), ``Q_loss`` (mean ambient loss
            [W]), ``T_max`` [°C], ``overheat`` (``T_max > T_boil``), ``mixed``
            (an inversion was mixed) and ``substeps``. The step closes
            ``ΔE = dt·[ρc·(V̇_c·T_charge + V̇_d·T_makeup − V̇_d·T_draw_out −
            V̇_c·T_charge_out) + Σq − Q_loss]`` to round-off. Without mixing
            and sub-steps ``T_draw_out == T_top`` and ``T_charge_out ==
            T_outlet``.
        """
        validate_tank_step(locals())
        q_arr = _heat_source_array(q_source, self.n)
        m = self.substeps(dt, charge_flow, draw_flow)
        h = dt / m
        cap = np.full(self.n, self.m_node * self.cp)
        draw_vt = charge_vt = loss_j = 0.0
        mixed = False
        T = self.T
        for _ in range(m):
            T = self._implicit_step(T, h, charge_flow, T_charge, draw_flow, T_makeup, q_arr, T_amb)
            # Outflows and losses are evaluated at the implicit end-of-step state
            # (before mixing), which is what the solve balanced.
            draw_vt += draw_flow * h * T[0]
            charge_vt += charge_flow * h * T[-1]
            loss_j += h * self.ua_node * float(np.sum(T - T_amb))
            if self.inversion_mixing:
                T, did = mix_inversions(T, cap)
                mixed = mixed or did
        self.T = T

        t_max, over = _flag_overheat(self.T, self.T_boil, "StratifiedTank")
        return {
            "T": self.T.copy(),
            "T_top": float(self.T[0]),
            "T_outlet": float(self.T[-1]),
            "T_draw_out": draw_vt / (draw_flow * dt) if draw_flow > 0.0 else float(self.T[0]),
            "T_charge_out": charge_vt / (charge_flow * dt) if charge_flow > 0.0 else float(self.T[-1]),
            "Q_loss": loss_j / dt,
            "T_max": t_max,
            "overheat": over,
            "mixed": mixed,
            "substeps": m,
        }

    def _implicit_step(self, T_old, dt, charge_flow, T_charge, draw_flow, T_makeup, q_arr, T_amb) -> np.ndarray:
        """One backward-Euler tridiagonal solve (the pre-#96 single-step update)."""
        n = self.n
        mc_dt = self.m_node * self.cp / dt  # capacitance/dt [W/K]
        G = self.G
        ua = self.ua_node
        rc = self.rho * self.cp
        mc_chg = rc * charge_flow  # charge advective conductance [W/K]
        mc_draw = rc * draw_flow  # draw advective conductance [W/K]
        v_net = charge_flow - draw_flow  # net downward inter-node flow [m³/s]
        mc_int = rc * abs(v_net)  # internal advective conductance [W/K]
        down = v_net >= 0.0

        lower = np.zeros(n)  # coupling of row i to T_{i-1}
        diag = np.zeros(n)
        upper = np.zeros(n)  # coupling of row i to T_{i+1}
        rhs = np.zeros(n)

        for i in range(n):
            diag[i] = mc_dt + ua
            rhs[i] = mc_dt * T_old[i] + ua * T_amb + q_arr[i]

            # Boundary ports.
            if i == 0:
                rhs[i] += mc_chg * T_charge  # charge hot inflow (top)
                diag[i] += mc_draw  # draw outflow at T_0 (top)
            if i == n - 1:
                rhs[i] += mc_draw * T_makeup  # cold makeup inflow (bottom)
                diag[i] += mc_chg  # charge outflow at T_{N-1} (bottom)

            # Internal advection (upwind by net direction).
            if down:
                if i >= 1:
                    lower[i] += -mc_int  # inflow from node above
                if i <= n - 2:
                    diag[i] += mc_int  # outflow to node below
            else:
                if i <= n - 2:
                    upper[i] += -mc_int  # inflow from node below
                if i >= 1:
                    diag[i] += mc_int  # outflow to node above

            # Pseudo-conduction to neighbours.
            g_above = G if i > 0 else 0.0
            g_below = G if i < n - 1 else 0.0
            diag[i] += g_above + g_below
            if i > 0:
                lower[i] += -g_above
            if i < n - 1:
                upper[i] += -g_below

        # Banded form for solve_banded((1, 1), ab, rhs):
        #   ab[0, 1:] = super-diagonal, ab[1] = diagonal, ab[2, :-1] = sub-diagonal.
        ab = np.zeros((3, n))
        ab[0, 1:] = upper[:-1]
        ab[1, :] = diag
        ab[2, :-1] = lower[1:]
        return np.asarray(solve_banded((1, 1), ab, rhs), dtype=float)
