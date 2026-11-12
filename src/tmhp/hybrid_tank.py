"""1-D hybrid continuous–discrete multi-node stratified tank (Cruz-Loredo 2023).

Implements the idea of the hybrid thermocline model of De la Cruz-Loredo et al.
(*Experimental validation of a hybrid 1-D multi-node model of a hot water
thermal energy storage tank*, Applied Energy 2023, 332:120556): the standard
multi-node model (:class:`~tmhp.stratified_tank.StratifiedTank`) is augmented by
a **flat thermocline barrier** at vertical position ``y_th`` that travels in plug
flow at ``v_th = V̇/A_c`` (paper Eq. 6). Across the barrier the temperature jumps;
the node that contains it is not treated as fully mixed for advection, so the
transition propagates at the physical front speed instead of smearing across
nodes (the numerical diffusion of the standard model).

Formulation (conservative, any time step)
-----------------------------------------
The paper realises the barrier with a *discrete reference temperature*
``T_{j,k}`` of the upstream node that stays frozen while the barrier is inside
it (Eq. 3-5) and updates it every ``Δt = 1 s`` — the barrier then moves a tiny
fraction of a node per update. Taken literally that scheme advects the frozen
value into the next node while the upstream node loses its continuous value, so
it does not conserve energy, and with a time step longer than one node transit
the front cannot get past one node per step (#96: 7.7–58 % of the injected heat
was stored). This module keeps the same states but closes the books:

- the node containing the barrier carries two zones — water above the barrier
  (``T_above``) and below it (``T_below``, the paper's discrete reference) — and
  its node temperature is their volume-weighted mean;
- advection is an exact plug-flow shift of that piecewise-constant column by the
  step volume ``V̇·Δt`` (any number of nodes per step), remapped onto the nodes
  conservatively; only the barrier is kept as a sub-node discontinuity;
- conduction (zone-aware), side-wall loss and ``q_source`` are then integrated
  implicitly (backward Euler) over the nodes plus the two zones;
- buoyancy inversions are mixed instantly
  (:func:`~tmhp.stratified_tank.mix_inversions`).

Every step closes ``ΔE = in − out + Σq·Δt − loss`` to round-off and the barrier
sits exactly ``V_injected/A`` below the inlet.

Barrier life cycle (paper §3.3)
-------------------------------
``front="both"`` (default, Eqs. 4-6): the barrier forms at the inlet when net
flow starts into a tank without one (top for net charge, bottom for net draw),
moves with the net flow in either direction, persists while idle (only
conduction acts on it) and disappears when it leaves the tank, when its two
sides equalise (``|T_above − T_below| ≤ FRONT_MERGE_TOL_K``) or when buoyancy
mixes them. ``front="charge"`` (Eq. 7) dissolves it whenever the net flow is
upward; ``front="discharge"`` (Eq. 8) whenever it is downward — for tanks whose
inlet mixing destroys the thermocline in one direction (the paper's own tank
had a sharp front only while discharging). Only one barrier is tracked: an
interface created while one already exists (e.g. a new charge temperature) is
carried by the ordinary node remap and diffuses like the standard model.

Port flows that act at the same time are short-circuited first: with both
``charge_flow`` and ``draw_flow``, ``min(charge, draw)`` of the charge water goes
straight to the draw and the same volume of makeup straight to the charge
return; only the net flow moves the column. ``T_draw_out`` / ``T_charge_out`` in
the result report the resulting outflow temperatures.

Validation status: energy conservation, front position and sharpness are
verified against plug flow (``tests/test_hybrid_tank.py``); no comparison with
measured tank data has been made in tmhp.

Same geometry/units conventions as ``StratifiedTank`` (node 0 = top/hot).
"""

from __future__ import annotations

import math

import numpy as np
from scipy.linalg import solve_banded

from ._validation import validate_ctor, validate_tank_step
from .constants import c_w, k_w, rho_w
from .stratified_tank import T_BOIL_ATM_C, _flag_overheat, _heat_source_array, mix_inversions

#: A barrier closer than this fraction of a node volume to a node boundary is
#: snapped onto the boundary, so an exact landing (e.g. one node volume per
#: step) does not depend on round-off and no zone of ~zero volume is created.
_BOUNDARY_SNAP = 1.0e-9

#: Temperature difference [K] at or below which the two sides of the barrier
#: are treated as the same water and the barrier is dropped (freeing it for the
#: next real front). 1 mK is below any sensor resolution and far above
#: round-off.
FRONT_MERGE_TOL_K = 1.0e-3

_FRONT_MODES = ("both", "charge", "discharge")


class HybridStratifiedTank:
    """Hybrid continuous–discrete multi-node tank with a flat thermocline.

    Parameters
    ----------
    n_nodes, volume, height, k_eff, ua, rho, cp, T_boil
        As :class:`~tmhp.stratified_tank.StratifiedTank`.
    front : {"both", "charge", "discharge"}, optional
        Flow directions in which the thermocline barrier is tracked (see the
        module docstring). Default ``"both"``.
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
        front: str = "both",
        T_boil: float = T_BOIL_ATM_C,
    ) -> None:
        validate_ctor("TANK", locals())
        if int(n_nodes) < 1:
            raise ValueError(f"n_nodes must be >= 1 — got {n_nodes}")
        if volume <= 0.0 or height <= 0.0:
            raise ValueError(f"volume and height must be > 0 — got {volume}, {height}")
        if front not in _FRONT_MODES:
            raise ValueError(f"front must be one of {_FRONT_MODES} — got {front!r}")
        if not math.isfinite(T_boil):
            raise ValueError(f"T_boil must be finite — got {T_boil}")

        self.n = int(n_nodes)
        self.volume = float(volume)
        self.height = float(height)
        self.rho = float(rho)
        self.cp = float(cp)
        self.k_eff = float(k_eff)
        self.ua_total = float(ua)
        self.front = front
        self.T_boil = float(T_boil)

        self.area_cross = self.volume / self.height
        self.dz = self.height / self.n
        self.v_node = self.volume / self.n
        self.m_node = self.rho * self.v_node
        self.G = self.k_eff * self.area_cross / self.dz
        self.ua_node = self.ua_total / self.n

        self.T: np.ndarray = np.zeros(self.n)
        # Barrier: volume below the top surface [m³] (None = no barrier) and the
        # temperatures of the two zones of the node that contains it.
        self._x_front: float | None = None
        self._T_above = math.nan
        self._T_below = math.nan

    # ------------------------------------------------------------------
    def reset(self, T_init) -> np.ndarray:
        arr = np.asarray(T_init, dtype=float)
        if arr.ndim == 0:
            self.T = np.full(self.n, float(arr))
        else:
            if arr.shape != (self.n,):
                raise ValueError(f"T_init must be scalar or shape ({self.n},) — got {arr.shape}")
            self.T = arr.astype(float).copy()
        self._x_front = None
        self._T_above = self._T_below = math.nan
        return self.T

    @property
    def stored_energy(self) -> float:
        return float(self.m_node * self.cp * self.T.sum())

    @property
    def y_th(self) -> float | None:
        """Barrier height above the tank bottom [m], ``None`` without a barrier."""
        if self._x_front is None:
            return None
        return self.height - self._x_front / self.area_cross

    @property
    def front_temperatures(self) -> tuple[float, float] | None:
        """``(T_above, T_below)`` of the barrier [°C], ``None`` without a barrier."""
        if self._x_front is None:
            return None
        return (self._T_above, self._T_below)

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
        """Advance one timestep; arguments and result as ``StratifiedTank.step``.

        The result also carries ``y_th`` (barrier height [m] or ``None``).
        """
        validate_tank_step(locals())
        q_arr = _heat_source_array(q_source, self.n)
        net = (charge_flow - draw_flow) * dt  # net downward volume [m³]

        # 1. Barrier life cycle for this flow direction (paper §3.3).
        if self._x_front is not None and (
            (net < 0.0 and self.front == "charge") or (net > 0.0 and self.front == "discharge")
        ):
            self._x_front = None  # node mean already holds both zones
        form = self._x_front is None and (
            (net > 0.0 and self.front != "discharge") or (net < 0.0 and self.front != "charge")
        )

        # 2. Exact plug-flow advection of the piecewise-constant column.
        draw_vt, charge_vt = self._advect(net, dt, charge_flow, T_charge, draw_flow, T_makeup, form)

        # 3. Conduction, side-wall loss and heat source (implicit), then buoyancy.
        loss_j, mixed = self._diffuse(dt, q_arr, T_amb)

        t_max, over = _flag_overheat(self.T, self.T_boil, "HybridStratifiedTank")
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
            "y_th": self.y_th,
        }

    # ------------------------------------------------------------------
    def _column(self) -> tuple[list[float], list[float]]:
        """Piecewise-constant column, top first: (volumes [m³], temperatures [°C])."""
        vols: list[float] = []
        temps: list[float] = []
        f, phi = self._front_node()
        for i in range(self.n):
            if i == f and phi is not None:
                vols += [phi * self.v_node, (1.0 - phi) * self.v_node]
                temps += [self._T_above, self._T_below]
            else:
                vols.append(self.v_node)
                temps.append(float(self.T[i]))
        return vols, temps

    def _front_node(self) -> tuple[int, float | None]:
        """Node holding the barrier and the volume fraction above it.

        ``phi`` is ``None`` when there is no barrier or it lies on a node
        boundary (then the grid itself carries the jump).
        """
        if self._x_front is None:
            return -1, None
        k = self._x_front / self.v_node
        f = math.floor(k)
        phi = k - f
        if phi <= _BOUNDARY_SNAP or phi >= 1.0 - _BOUNDARY_SNAP or f >= self.n:
            return -1, None
        return f, phi

    @staticmethod
    def _take(vols: list[float], temps: list[float], amount: float, from_top: bool) -> float:
        """Remove ``amount`` m³ from one end of the column; return Σ(V·T) removed."""
        vt = 0.0
        idx = 0 if from_top else -1
        while amount > 0.0 and vols:
            v = vols[idx]
            if v <= amount:
                vt += v * temps[idx]
                amount -= v
                vols.pop(idx)
                temps.pop(idx)
            else:
                vt += amount * temps[idx]
                vols[idx] = v - amount
                amount = 0.0
        return vt

    def _advect(self, net, dt, charge_flow, T_charge, draw_flow, T_makeup, form) -> tuple[float, float]:
        short = min(charge_flow, draw_flow) * dt  # port-to-port short circuit [m³]
        draw_vt = short * T_charge
        charge_vt = short * T_makeup
        if net == 0.0:
            return draw_vt, charge_vt

        vols, temps = self._column()
        x = self._x_front
        if net > 0.0:
            vols.insert(0, net)
            temps.insert(0, T_charge)
            charge_vt += self._take(vols, temps, net, from_top=False)
            if form:
                x = 0.0
            x = None if x is None else x + net
        else:
            vols.append(-net)
            temps.append(T_makeup)
            draw_vt += self._take(vols, temps, -net, from_top=True)
            if form:
                x = self.volume
            x = None if x is None else x + net

        # Conservative remap onto the fixed nodes via the cumulative Σ(V·T).
        edges = np.concatenate(([0.0], np.cumsum(vols)))
        edges *= self.volume / edges[-1]  # absorb round-off in the total volume
        cum_vt = np.concatenate(([0.0], np.cumsum(np.asarray(vols) * np.asarray(temps))))
        node_edges = np.arange(self.n + 1) * self.v_node
        e_nodes = np.interp(node_edges, edges, cum_vt)
        self.T = np.diff(e_nodes) / self.v_node

        snap = _BOUNDARY_SNAP * self.v_node
        if x is not None and (x <= snap or x >= self.volume - snap):
            x = None  # the barrier has left the tank
        self._x_front = x
        f, phi = self._front_node()
        if phi is not None and x is not None:
            e_x = float(np.interp(x, edges, cum_vt))
            self._T_above = (e_x - e_nodes[f]) / (phi * self.v_node)
            self._T_below = (e_nodes[f + 1] - e_x) / ((1.0 - phi) * self.v_node)
        elif x is not None:
            b = round(x / self.v_node)  # barrier on the boundary above node b
            self._x_front = b * self.v_node
            self._T_above, self._T_below = float(self.T[b - 1]), float(self.T[b])
        return draw_vt, charge_vt

    def _diffuse(self, dt: float, q_arr: np.ndarray, T_amb: float) -> tuple[float, bool]:
        """Implicit conduction/loss/source over nodes + barrier zones, then mixing."""
        f, phi = self._front_node()
        # Control volumes, top first: volume fraction of a node, node index, temperature.
        frac: list[float] = []
        node: list[int] = []
        temps: list[float] = []
        for i in range(self.n):
            if i == f and phi is not None:
                frac += [phi, 1.0 - phi]
                node += [i, i]
                temps += [self._T_above, self._T_below]
            else:
                frac.append(1.0)
                node.append(i)
                temps.append(float(self.T[i]))
        fr = np.asarray(frac)
        cap = fr * self.m_node * self.cp  # [J/K]
        ua = fr * self.ua_node
        q = fr * q_arr[np.asarray(node)]
        # Conductance between CV centres: k·A / (half thickness above + below).
        g = self.G * 2.0 / (fr[:-1] + fr[1:])
        m = fr.size
        diag = cap / dt + ua
        diag[:-1] += g
        diag[1:] += g
        ab = np.zeros((3, m))
        ab[0, 1:] = -g
        ab[1] = diag
        ab[2, :-1] = -g
        rhs = cap / dt * np.asarray(temps) + ua * T_amb + q
        Tcv = solve_banded((1, 1), ab, rhs)
        loss_j = dt * float(np.sum(ua * (Tcv - T_amb)))

        Tcv, mixed = mix_inversions(Tcv, cap)

        # Back to node means; keep or drop the barrier.
        if phi is not None:
            ta, tb = float(Tcv[f]), float(Tcv[f + 1])
            Tn = np.delete(Tcv, f + 1)
            Tn[f] = phi * ta + (1.0 - phi) * tb
            self._T_above, self._T_below = ta, tb
        else:
            Tn = Tcv
            if self._x_front is not None:
                b = round(self._x_front / self.v_node)
                self._T_above, self._T_below = float(Tn[b - 1]), float(Tn[b])
        self.T = np.asarray(Tn, dtype=float)
        if self._x_front is not None and abs(self._T_above - self._T_below) <= FRONT_MERGE_TOL_K:
            self._x_front = None
        if self._x_front is None:
            self._T_above = self._T_below = math.nan
        return loss_j, mixed
