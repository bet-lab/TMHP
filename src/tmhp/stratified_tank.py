"""Multi-node (1-D finite-volume) stratified thermal-storage tank.

Implements the multi-node model of Cadau et al. (*Development and Analysis of a
Multi-Node Dynamic Model for the Simulation of Stratified Thermal Energy
Storage*, Energies 2019, 12:4275): the tank is divided into ``N`` vertically
stacked nodes (node 0 = top/hottest, node N-1 = bottom/coldest), each with a
uniform temperature ``T_i``. The per-node energy balance combines

- **advection** — port inflow/outflow and inter-node vertical flow, upwinded by
  the flow direction (Cadau Eq. 4-6);
- **pseudo-conduction** — ``k·(T_{i-1}-T_i) - k·(T_i-T_{i+1})`` between
  neighbouring nodes, lumping conductive + convective exchange; and
- **ambient loss** — ``UA_i·(T_i - T_amb)`` through the side wall.

It is integrated **implicitly** (backward Euler) as a tridiagonal solve, so the
update is unconditionally stable and smooth in the state — a property the legacy
single-node lumped tank lacks and that an MPC-internal model needs. The lumped
fully-mixed tank is exactly the ``N=1`` limit of this model.

This is a standalone, testable component; wiring it into
``GroundSourceHeatPumpBoiler`` as a swappable tank backend is a separate step.

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
"""

from __future__ import annotations

import numpy as np
from scipy.linalg import solve_banded

from .constants import c_w, k_w, rho_w


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
        across nodes. Defaults to 0 (adiabatic).
    rho, cp : float, optional
        Water density [kg/m³] and specific heat [J/kg/K]; default to the project
        constants. Assumed constant (Cadau: density assumed constant per node).
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

        # Geometry / lumped node properties.
        self.area_cross = self.volume / self.height          # [m²]
        self.dz = self.height / self.n                       # node thickness [m]
        self.v_node = self.volume / self.n                   # node volume [m³]
        self.m_node = self.rho * self.v_node                 # node mass [kg]
        self.G = self.k_eff * self.area_cross / self.dz      # inter-node conductance [W/K]
        self.ua_node = self.ua_total / self.n                # per-node loss [W/K]

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

    # ------------------------------------------------------------------
    def step(self, dt: float, *, charge_flow: float = 0.0, T_charge: float = 0.0,
             draw_flow: float = 0.0, T_makeup: float = 10.0,
             q_source=None, T_amb: float = 20.0) -> dict:
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
            ``T`` (new node temperatures), ``T_top`` (= hot draw outlet),
            ``T_outlet`` (bottom = cold HP return).
        """
        n = self.n
        mc_dt = self.m_node * self.cp / dt          # capacitance/dt [W/K]
        G = self.G
        ua = self.ua_node
        rc = self.rho * self.cp
        mc_chg = rc * charge_flow                    # charge advective conductance [W/K]
        mc_draw = rc * draw_flow                     # draw advective conductance [W/K]
        v_net = charge_flow - draw_flow              # net downward inter-node flow [m³/s]
        mc_int = rc * abs(v_net)                     # internal advective conductance [W/K]
        down = v_net >= 0.0

        # Heat source [W per node]: scalar -> top node; array -> per node.
        q_arr = np.zeros(n)
        if q_source is not None:
            qs = np.asarray(q_source, dtype=float)
            if qs.ndim == 0:
                q_arr[0] = float(qs)
            elif qs.shape == (n,):
                q_arr = qs
            else:
                raise ValueError(f"q_source must be scalar or shape ({n},) — got {qs.shape}")

        lower = np.zeros(n)   # coupling of row i to T_{i-1}
        diag = np.zeros(n)
        upper = np.zeros(n)   # coupling of row i to T_{i+1}
        rhs = np.zeros(n)

        for i in range(n):
            diag[i] = mc_dt + ua
            rhs[i] = mc_dt * self.T[i] + ua * T_amb + q_arr[i]

            # Boundary ports.
            if i == 0:
                rhs[i] += mc_chg * T_charge          # charge hot inflow (top)
                diag[i] += mc_draw                   # draw outflow at T_0 (top)
            if i == n - 1:
                rhs[i] += mc_draw * T_makeup         # cold makeup inflow (bottom)
                diag[i] += mc_chg                    # charge outflow at T_{N-1} (bottom)

            # Internal advection (upwind by net direction).
            if down:
                if i >= 1:
                    lower[i] += -mc_int              # inflow from node above
                if i <= n - 2:
                    diag[i] += mc_int                # outflow to node below
            else:
                if i <= n - 2:
                    upper[i] += -mc_int              # inflow from node below
                if i >= 1:
                    diag[i] += mc_int                # outflow to node above

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
        self.T = solve_banded((1, 1), ab, rhs)

        return {"T": self.T.copy(), "T_top": float(self.T[0]), "T_outlet": float(self.T[-1])}
