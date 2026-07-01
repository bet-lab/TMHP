"""1-D hybrid continuous–discrete multi-node stratified tank (Cruz-Loredo 2023).

Implements the hybrid thermocline model of De la Cruz-Loredo et al.
(*Experimental validation of a hybrid 1-D multi-node model of a hot water
thermal energy storage tank*, Applied Energy 2023, 332:120556). It augments the
standard multi-node model (:class:`~tmhp.stratified_tank.StratifiedTank`) with a
**flat thermocline barrier** at vertical position ``y_th`` that travels in plug
flow at ``v_th = V̇/A_c``.

The key device against numerical diffusion: while charging, the advective inflow
into each node uses a *discrete reference temperature* of its upstream neighbour
that is **frozen until the thermocline front passes that neighbour's mid-height**
(``y_mid``). The transition therefore propagates at the physical front speed
instead of smearing across nodes. This is the charge-only thermocline form
(Cruz-Loredo Eq. 7): discharge/idle destroys the barrier and the model reverts to
the standard continuous multi-node behaviour.

Targets the **plant / ground-truth** role (high fidelity, non-smooth); the smooth
:class:`~tmhp.stratified_tank.StratifiedTank` (Cadau) targets the MPC-internal
role. Same geometry/units conventions as ``StratifiedTank`` (node 0 = top/hot).
"""

from __future__ import annotations

import numpy as np
from scipy.linalg import solve_banded

from .constants import c_w, k_w, rho_w


class HybridStratifiedTank:
    """Hybrid continuous–discrete multi-node tank with a flat thermocline."""

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
    ) -> None:
        if int(n_nodes) < 1:
            raise ValueError(f"n_nodes must be >= 1 — got {n_nodes}")
        if volume <= 0.0 or height <= 0.0:
            raise ValueError(f"volume and height must be > 0 — got {volume}, {height}")

        self.n = int(n_nodes)
        self.volume = float(volume)
        self.height = float(height)
        self.rho = float(rho)
        self.cp = float(cp)
        self.k_eff = float(k_eff)
        self.ua_total = float(ua)

        self.area_cross = self.volume / self.height
        self.dz = self.height / self.n
        self.v_node = self.volume / self.n
        self.m_node = self.rho * self.v_node
        self.G = self.k_eff * self.area_cross / self.dz
        self.ua_node = self.ua_total / self.n
        # Node mid-heights [m] (reference temperature positions): node 0 (top) is
        # highest, node N-1 (bottom) lowest.
        self.y_mid = self.height - (np.arange(self.n) + 0.5) * self.dz

        self.T: np.ndarray = np.zeros(self.n)
        self.y_th = self.height          # thermocline at the top (no active front)
        self.T_ref: np.ndarray = np.zeros(self.n)    # frozen upstream reference temperatures
        self._charging = False

    # ------------------------------------------------------------------
    def reset(self, T_init) -> np.ndarray:
        arr = np.asarray(T_init, dtype=float)
        if arr.ndim == 0:
            self.T = np.full(self.n, float(arr))
        else:
            if arr.shape != (self.n,):
                raise ValueError(f"T_init must be scalar or shape ({self.n},) — got {arr.shape}")
            self.T = arr.astype(float).copy()
        self.y_th = self.height
        self.T_ref = self.T.copy()
        self._charging = False
        return self.T

    @property
    def stored_energy(self) -> float:
        return float(self.m_node * self.cp * self.T.sum())

    # ------------------------------------------------------------------
    def step(self, dt: float, *, charge_flow: float = 0.0, T_charge: float = 0.0,
             draw_flow: float = 0.0, T_makeup: float = 10.0,
             T_amb: float = 20.0) -> dict:
        """Advance one timestep.

        Pure charge (``charge_flow > 0, draw_flow == 0``) activates the hybrid
        frozen-reference thermocline; draw/idle/mixed flow uses the standard
        continuous multi-node update (the barrier is destroyed).
        """
        n = self.n
        mc_dt = self.m_node * self.cp / dt
        G = self.G
        ua = self.ua_node
        rc = self.rho * self.cp

        charging = charge_flow > 0.0 and draw_flow == 0.0
        if charging:
            self._charge_thermocline(dt, charge_flow)
            mc = rc * charge_flow
            # Upstream reference (frozen) for downward advection into each node.
            adv_in = np.empty(n)
            adv_in[0] = T_charge
            adv_in[1:] = self.T_ref[:-1]
            self._solve(mc_dt, mc, adv_in, G, ua, T_amb)
        else:
            # Standard continuous multi-node update; barrier destroyed.
            self.y_th = self.height
            self.T_ref = self.T.copy()
            self._charging = False
            self._standard_step(dt, charge_flow, T_charge, draw_flow, T_makeup, T_amb)

        return {"T": self.T.copy(), "T_top": float(self.T[0]), "T_outlet": float(self.T[-1])}

    # ------------------------------------------------------------------
    def _charge_thermocline(self, dt: float, charge_flow: float) -> None:
        """Update the descending thermocline + release passed nodes' references."""
        if not self._charging:
            # Charge phase starts: front re-forms at the hot (top) inlet.
            self.y_th = self.height
            self.T_ref = self.T.copy()
            self._charging = True
        v_th = charge_flow / self.area_cross
        self.y_th -= v_th * dt
        # A node's reference tracks its continuous temperature once the front has
        # descended past the node's mid-height; otherwise it stays frozen.
        released = self.y_th <= self.y_mid
        self.T_ref = np.where(released, self.T, self.T_ref)

    def _solve(self, mc_dt, mc, adv_in, G, ua, T_amb) -> None:
        """Implicit tridiagonal solve with frozen-reference downward advection.

        Advective inflow uses the (constant) frozen upstream reference ``adv_in``,
        so only conduction couples neighbours — the front cannot smear across
        nodes via the advection term.
        """
        n = self.n
        lower = np.zeros(n)
        diag = np.zeros(n)
        upper = np.zeros(n)
        rhs = np.zeros(n)
        for i in range(n):
            g_above = G if i > 0 else 0.0
            g_below = G if i < n - 1 else 0.0
            diag[i] = mc_dt + mc + g_above + g_below + ua    # +mc = advection out
            rhs[i] = mc_dt * self.T[i] + ua * T_amb + mc * adv_in[i]
            if i > 0:
                lower[i] = -g_above
            if i < n - 1:
                upper[i] = -g_below
        ab = np.zeros((3, n))
        ab[0, 1:] = upper[:-1]
        ab[1, :] = diag
        ab[2, :-1] = lower[1:]
        self.T = solve_banded((1, 1), ab, rhs)

    def _standard_step(self, dt, charge_flow, T_charge, draw_flow, T_makeup, T_amb) -> None:
        """Standard continuous multi-node update (upwind advection)."""
        n = self.n
        mc_dt = self.m_node * self.cp / dt
        G = self.G
        ua = self.ua_node
        rc = self.rho * self.cp
        mc_chg = rc * charge_flow
        mc_draw = rc * draw_flow
        v_net = charge_flow - draw_flow
        mc_int = rc * abs(v_net)
        down = v_net >= 0.0

        lower = np.zeros(n)
        diag = np.zeros(n)
        upper = np.zeros(n)
        rhs = np.zeros(n)
        for i in range(n):
            diag[i] = mc_dt + ua
            rhs[i] = mc_dt * self.T[i] + ua * T_amb
            if i == 0:
                rhs[i] += mc_chg * T_charge
                diag[i] += mc_draw
            if i == n - 1:
                rhs[i] += mc_draw * T_makeup
                diag[i] += mc_chg
            if down:
                if i >= 1:
                    lower[i] += -mc_int
                if i <= n - 2:
                    diag[i] += mc_int
            else:
                if i <= n - 2:
                    upper[i] += -mc_int
                if i >= 1:
                    diag[i] += mc_int
            g_above = G if i > 0 else 0.0
            g_below = G if i < n - 1 else 0.0
            diag[i] += g_above + g_below
            if i > 0:
                lower[i] += -g_above
            if i < n - 1:
                upper[i] += -g_below
        ab = np.zeros((3, n))
        ab[0, 1:] = upper[:-1]
        ab[1, :] = diag
        ab[2, :-1] = lower[1:]
        self.T = solve_banded((1, 1), ab, rhs)
