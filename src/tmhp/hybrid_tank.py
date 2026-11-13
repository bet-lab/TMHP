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
- conduction (zone-aware), side-wall loss, ``q_source`` and the immersed
  exchanger (``hx_ua`` / ``T_hx`` / ``q_hx``, as in ``StratifiedTank``) are
  then integrated implicitly (backward Euler) over the nodes plus the two zones;
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

Simultaneous charge and draw: only the net flow ``charge − draw`` moves the
column. The overlapping through-flow ``s = min(charge, draw)·Δt`` enters and
leaves at the same end, so it is exchanged with the inlet node there, treated
as a fully mixed volume (as in the standard model) and integrated exactly
(``T → T_in + (T − T_in)·exp(−s/V)``, independent of ``Δt``): hot charge water
mixes into the top node before the draw takes it, makeup mixes into the bottom
node before the charge return takes it. While the ports overlap, a new barrier
is formed below the mixed inlet node rather than at the inlet itself, so the
front position stays independent of ``Δt``. ``T_draw_out`` / ``T_charge_out``
in the result report the outflow temperatures.

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
from .stratified_tank import (
    OVERHEAT_TOL_K,
    T_BOIL_ATM_C,
    _flag_overheat,
    _heat_source_array,
    _hx_conductance,
    _hx_substeps,
    mix_inversions,
    solve_hx_scale,
    solve_hx_temperature,
    solve_one_sided_hx,
)

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

#: With overlapping port flows, the net plug shift and the inlet-node mixing are
#: alternated in slices of at most this fraction of a node volume. The split
#: error scales with it: at 0.1 the stored energy of a 1 h simultaneous
#: charge/draw differs by <= 0.04 % between dt = 10 s and 3600 s (1.0 gave 1 %).
_SLICE_FRACTION = 0.1

#: Relative slack when rounding the slice count up, so an exact multiple (up to
#: round-off) gives the same count on every platform.
_SLICE_SLACK = 1.0e-9


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
        hx_ua=None,
        T_hx: float | None = None,
        q_hx: float | None = None,
    ) -> dict:
        """Advance one timestep; arguments and result as ``StratifiedTank.step``.

        The result carries ``y_th`` (barrier height [m] or ``None``) instead
        of ``substeps``.
        """
        return self._advance(locals(), commit=True)

    def preview(
        self,
        dt: float,
        *,
        charge_flow: float = 0.0,
        T_charge: float = 0.0,
        draw_flow: float = 0.0,
        T_makeup: float = 10.0,
        q_source=None,
        T_amb: float = 20.0,
        hx_ua=None,
        T_hx: float | None = None,
        q_hx: float | None = None,
    ) -> dict:
        """Result :meth:`step` would give, without advancing the tank (and without warning)."""
        return self._advance(locals(), commit=False)

    def _state(self) -> tuple[np.ndarray, float | None, float, float]:
        return self.T.copy(), self._x_front, self._T_above, self._T_below

    def _restore(self, state: tuple[np.ndarray, float | None, float, float]) -> None:
        self.T = state[0].copy()
        self._x_front, self._T_above, self._T_below = state[1], state[2], state[3]

    def _advance(self, args: dict, *, commit: bool) -> dict:
        validate_tank_step(args)
        dt, charge_flow, draw_flow = args["dt"], args["charge_flow"], args["draw_flow"]
        T_charge, T_makeup, T_amb, T_hx = args["T_charge"], args["T_makeup"], args["T_amb"], args["T_hx"]
        q_arr = _heat_source_array(args["q_source"], self.n)
        ua_hx = _hx_conductance(args["hx_ua"], T_hx, args["q_hx"], self.n)
        before = self._state()
        net = (charge_flow - draw_flow) * dt  # net downward volume [m³]
        through = min(charge_flow, draw_flow) * dt  # [m³]
        may_form = (net > 0.0 and self.front != "discharge") or (net < 0.0 and self.front != "charge")

        # Slices. Advection (an exact plug-flow shift) and the implicit part
        # (conduction, loss, heat input) are applied one after the other, an
        # operator split. Without a heat input the implicit part runs once, after
        # the whole shift. With one (``q_source`` or the exchanger) heating and
        # flow act together, so the two alternate in slices of at most one node
        # volume: otherwise a draw of a tank volume in one step would push out
        # unheated make-up water before any of the step's heat went in.
        # Overlapping port flows are sliced to _SLICE_FRACTION of a node (their
        # exchange with the inlet node is itself a split).
        heated = ua_hx is not None or bool(np.any(q_arr != 0.0))
        k = 1
        if through > 0.0:
            k = max(k, math.ceil((through + abs(net)) / (_SLICE_FRACTION * self.v_node) - _SLICE_SLACK))
        if heated:
            k = max(k, math.ceil(abs(net) / self.v_node - _SLICE_SLACK))

        def run(scale: float, T_hx: float | None, m: int) -> tuple[float, float, float, bool, np.ndarray, float]:
            """One pass from the start state: exchanger ``scale`` and temperature, ``m`` implicit sub-steps per slice."""
            self._restore(before)
            # 1. Barrier life cycle for this flow direction (paper §3.3).
            if self._x_front is not None and (
                (net < 0.0 and self.front == "charge") or (net > 0.0 and self.front == "discharge")
            ):
                self._x_front = None  # node mean already holds both zones
            hx = None if ua_hx is None else scale * ua_hx
            draw_vt = charge_vt = loss = 0.0
            mixed = formed = False
            heat = np.zeros(self.n)
            heated_max = -math.inf

            def implicit(h: float, pieces: int) -> None:
                nonlocal loss, mixed, heat, heated_max
                # A barrier just formed below a mixing inlet separates the mixed
                # node's outflow history from the old water; in its first step
                # both sides are still old water, so it is not dropped as
                # "equalised" yet (otherwise it would form one step late and its
                # position would depend on dt).
                keep_new = formed and through > 0.0 and self._x_front is not None
                for _ in range(pieces):
                    l_j, did, q_n, hot = self._diffuse(
                        h / pieces, q_arr, T_amb, allow_merge=not keep_new, hx=hx, T_hx=T_hx
                    )
                    heated_max = max(heated_max, hot)
                    loss += l_j
                    mixed = mixed or did
                    heat += h / pieces * q_n

            # 2. Exact plug-flow advection of the piecewise-constant column by the
            #    net flow, then the overlapping through-flow mixes with the inlet
            #    nodes; 3. conduction, side-wall loss, heat source and exchanger
            #    (implicit), then buoyancy.
            for _ in range(k):
                form = may_form and self._x_front is None
                d_vt, c_vt = self._advect(net / k, T_charge, T_makeup, form, mixing_inlet=through > 0.0)
                formed = formed or (form and self._x_front is not None)
                draw_vt += d_vt
                charge_vt += c_vt
                if through > 0.0:
                    d_vt, c_vt = self._port_exchange(through / k, T_charge, T_makeup)
                    draw_vt += d_vt
                    charge_vt += c_vt
                if heated:
                    implicit(dt / k, m)
            if not heated:
                implicit(dt, 1)
            return draw_vt, charge_vt, loss, mixed, heat, heated_max

        Pass = tuple[float, float, float, bool, np.ndarray, float]

        def solve(m: int) -> tuple[Pass, float, float | None, bool]:
            if ua_hx is None:
                return run(0.0, None, m), 0.0, None, False
            if args["q_hx"] is None:
                return run(1.0, T_hx, m), 1.0, T_hx, False
            if T_hx is None:
                # No water the step can reach is colder than this: nothing is delivered there.
                zones = [t for t in before[2:] if before[1] is not None and math.isfinite(t)]
                coldest = min(float(np.min(before[0])), *zones, T_makeup, T_amb)
                T_low = (min(coldest, T_charge) if charge_flow > 0.0 else coldest) - 1.0
                T_solved = solve_hx_temperature(
                    lambda v: float(run(1.0, v, m)[4].sum()) / dt,
                    args["q_hx"],
                    T_low,
                    float(np.max(before[0])) - T_low + args["q_hx"] / float(ua_hx.sum()),
                )
                return run(1.0, T_solved, m), 1.0, T_solved, False
            x, lim = solve_hx_scale(lambda v: float(run(v, T_hx, m)[4].sum()) / dt, args["q_hx"])
            return run(x, T_hx, m), x, T_hx, lim

        (draw_vt, charge_vt, loss_j, mixed, hx_j, heated_max), scale, T_hx_used, limited = solve(1)
        if ua_hx is not None and mixed:
            # Heated water rose out of the coil's nodes: resolve that in time.
            m_hx = math.ceil(_hx_substeps(dt, ua_hx, self.m_node * self.cp) / k)
            if m_hx > 1:
                (draw_vt, charge_vt, loss_j, mixed, hx_j, heated_max), scale, T_hx_used, limited = solve(m_hx)

        # The node holding the barrier reports the mean of its two zones, which can
        # hide an overheated zone (a thin 101 °C layer over 15 °C water averages
        # far below boiling), so the zones themselves enter the check.
        checked = self.T
        if self._front_node()[1] is not None:
            checked = np.append(self.T, (self._T_above, self._T_below))
        if commit:
            t_max, over = _flag_overheat(checked, self.T_boil, "HybridStratifiedTank")
        else:
            t_max = float(np.max(checked))
            over = t_max > self.T_boil + OVERHEAT_TOL_K
        out = {
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
            "q_hx": hx_j / dt,
            "Q_hx": float(hx_j.sum()) / dt,
            "hx_scale": scale,
            "T_hx": T_hx_used,
            "T_hx_heated_max": heated_max if math.isfinite(heated_max) else None,
            "hx_limited": limited,
        }
        if not commit:
            self._restore(before)
        return out

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

    def _advect(self, net, T_charge, T_makeup, form, *, mixing_inlet: bool) -> tuple[float, float]:
        """Shift the column by the net volume; return Σ(V·T) out at the top and bottom."""
        draw_vt = charge_vt = 0.0
        if net == 0.0:
            return draw_vt, charge_vt
        # With overlapping port flows the inlet node is a mixing volume, so a new
        # barrier forms at its lower (upper) face instead of at the inlet.
        x_form_top = self.v_node if mixing_inlet else 0.0
        x_form_bottom = self.volume - x_form_top

        vols, temps = self._column()
        x = self._x_front
        if net > 0.0:
            vols.insert(0, net)
            temps.insert(0, T_charge)
            charge_vt += self._take(vols, temps, net, from_top=False)
            if form:
                x = x_form_top
            x = None if x is None else x + net
        else:
            vols.append(-net)
            temps.append(T_makeup)
            draw_vt += self._take(vols, temps, -net, from_top=True)
            if form:
                x = x_form_bottom
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

    def _port_exchange(self, through: float, T_top_in: float, T_bottom_in: float) -> tuple[float, float]:
        """Exact fully-mixed exchange of ``through`` m³ with the inlet volume at each end.

        The inlet volume is the end node, or the barrier zone on the port side
        when the barrier sits inside that node. Returns Σ(V·T) leaving through
        the top (draw) and the bottom (charge return).
        """
        out = []
        for top, t_in in ((True, T_top_in), (False, T_bottom_in)):
            f, phi = self._front_node()
            end = 0 if top else self.n - 1
            if f == end and phi is not None:
                vol = (phi if top else 1.0 - phi) * self.v_node
                t_old = self._T_above if top else self._T_below
            else:
                vol, t_old = self.v_node, float(self.T[end])
            t_new = t_in + (t_old - t_in) * math.exp(-through / vol)
            out.append(through * t_in - vol * (t_new - t_old))
            if f == end and phi is not None:
                if top:
                    self._T_above = t_new
                else:
                    self._T_below = t_new
                self.T[end] = phi * self._T_above + (1.0 - phi) * self._T_below
            else:
                self.T[end] = t_new
        return out[0], out[1]

    def _diffuse(
        self,
        dt: float,
        q_arr: np.ndarray,
        T_amb: float,
        *,
        allow_merge: bool = True,
        hx: np.ndarray | None = None,
        T_hx: float | None = None,
    ) -> tuple[float, bool, np.ndarray, float]:
        """Implicit conduction/loss/source/exchanger over nodes + barrier zones, then mixing.

        ``hx`` is the exchanger conductance per node [W/K] at ``T_hx`` (a zone
        of the barrier node carries its volume share). Returns the loss [J],
        whether an inversion was mixed, the exchanger heat per node [W] and
        the hottest control volume the exchanger heated [°C] (``-inf`` if none).
        """
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
        q_hx_nodes = np.zeros(self.n)
        heated_max = -math.inf
        if hx is None or T_hx is None:
            Tcv = solve_banded((1, 1), ab, rhs)
        else:
            idx = np.asarray(node)
            Tcv, q_cv = solve_one_sided_hx(ab, rhs, fr * hx[idx], T_hx, np.asarray(temps))
            np.add.at(q_hx_nodes, idx, q_cv)
            if np.any(q_cv > 0.0):
                heated_max = float(np.max(Tcv[q_cv > 0.0]))
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
        if allow_merge and self._x_front is not None and abs(self._T_above - self._T_below) <= FRONT_MERGE_TOL_K:
            self._x_front = None
        if self._x_front is None:
            self._T_above = self._T_below = math.nan
        return loss_j, mixed, q_hx_nodes, heated_max
