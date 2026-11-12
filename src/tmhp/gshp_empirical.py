"""Ground source heat pump — simple COP-based model with indoor unit.

This module provides a simplified GSHP model using the EnergyPlus
EquationFit COP correlation rather than a full refrigerant cycle
analysis. It is a lightweight alternative to the CoolProp-based
:class:`~tmhp.GroundSourceHeatPump` for quick parametric studies.

Borehole thermal response is tracked with pygfunction-based
g-functions and temporal superposition of dynamic building loads.
The effective borehole thermal resistance R_b* is automatically
computed using the pygfunction multipole method.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from . import calc_util as cu
from . import g_function as gf
from .constants import c_a, c_w, k_w, rho_a, rho_w
from .cop import (
    GSHP_EQFIT_COP_REF,
    GSHP_EQFIT_DOMAIN,
    GSHP_EQFIT_T_REF_K,
    EquationFitRangeError,
    gshp_equation_fit_ratios,
)
from .g_function import g_step_mean, precompute_gfunction
from .hx_fan import SINGLE_ZONE_VAV_COEFFICIENTS, calc_fan_power_from_dV_fan

# Aliases to match the borehole-fluid naming convention in the original code
c_f = c_w
rho_f = rho_w

#: Entering (room) air temperature [°C] used when ``T_a_room`` is not given.
T_A_ROOM_DEFAULT = {"cooling": 27.0, "heating": 21.0, "off": 22.0}


@dataclass
class GroundSourceHeatPumpEmpirical:
    """Ground source heat pump model using the EnergyPlus EquationFit COP model.

    Uses borehole heat exchangers with pygfunction step-response
    factor array for precise soil thermal response with temporal
    superposition of dynamic building loads.

    This model computes borehole thermal resistance R_b* automatically
    using the pygfunction multipole method (Hellström 1991), and fan
    power using the default single-zone VAV surrogate.

    **Performance curve and its limits.** COP and available capacity come
    from the ClimateMaster TCH072_GLHP EquationFit curves of EnergyPlus
    (:func:`~tmhp.cop.gshp_equation_fit_ratios`). They describe full-load
    operation at the rated air flow; a smaller load is met by cycling, so the
    COP does not depend on the part-load ratio (no cycling degradation is
    applied). The available capacity is ``Q_rated_{cooling,heating} ×
    CAP_ratio(T_air, EWT)``; a larger request is served at that ceiling and
    flagged ``capacity_clamped = "max"``. Outside the fitted domain
    (:data:`~tmhp.cop.GSHP_EQFIT_DOMAIN`: entering water −1.25–32.06 °C in
    heating, 9.85–48.72 °C in cooling; entering air 9.85–26.51 °C dry-bulb in
    heating, 9.85–23.73 °C wet-bulb in cooling; the water temperature is
    judged at the converged, self-consistent entering water, not at the
    undisturbed ground) the unit does not run for that step: heat, compressor and pump power are zero, ``COP`` is NaN and
    ``failure_reason`` says why. COP and compressor power are therefore never
    negative.

    **Time convention.** Each call of :meth:`system_update` is one step of
    ``dt_hours``; the borehole-wall temperature is the mean over that step,
    including the response to the step's own load (step-mean g-function, the
    same convention as the physics-based ground-source models).

    For a full refrigerant-cycle model, see :class:`~tmhp.GroundSourceHeatPump`.
    """

    # 1. Borehole parameters
    H_b: float = 150.0  # Borehole height [m]
    D_b: float = 2.0  # Borehole burial depth [m]
    r_b: float = 0.08  # Borehole radius [m]

    # 2. Pipe & Grout parameters
    k_p: float = 0.4  # Pipe thermal conductivity [W/mK] (HDPE)
    k_grout: float = 1.5  # Grout thermal conductivity [W/mK]
    r_out: float = 0.016  # Pipe outer radius [m] (32mm OD / 2)
    r_in: float = 0.013  # Pipe inner radius [m] (26mm ID / 2)
    D_s: float = 0.032  # Distance from borehole centre to pipe centre [m]

    # 3. Ground parameters
    k_g: float = 2.0  # Ground thermal conductivity [W/mK]
    c_g: float = 800.0  # Ground specific heat capacity [J/(kgK)]
    rho_g: float = 2000.0  # Ground density [kg/m³]
    T_g: float = 15.0  # Initial ground temperature [°C]

    # 4. Fluid parameters
    dV_f: float = 20.0  # Volumetric flow rate of fluid [L/min]

    # 5. Rated Performance & Design
    Q_rated_cooling: float = 20590.0  # [W]
    Q_rated_heating: float = 16450.0  # [W]
    E_pmp: float = 100.0  # Pump power input [W]
    dP_iu_fan_design: float = 60.0  # Design pressure drop [Pa]
    eta_iu_fan_design: float = 0.6  # Design fan efficiency

    # 6. Simulation Control
    dt_hours: int = 1
    sim_hours: int = 8760
    # Axial boundary condition for the g-function: 'UBWT' or 'UHTR'
    gfunc_boundary_condition: str = "UBWT"

    # 7. Runtime Inputs (per-timestep)
    Q_r_iu: float = 0.0  # Indoor load request [W]: >0 cooling, <0 heating, 0 off
    T0: float = 20.0  # Dead-state (outdoor) temperature [°C]
    # Entering (room) air temperature [°C]; None uses T_A_ROOM_DEFAULT per mode.
    T_a_room: float | None = None

    def __post_init__(self):
        # Initialize historical and temporal states
        self.time = 0.0
        self.dt_sec = self.dt_hours * 3600.0
        self.q_b_history = [0.0]
        self._n_steps = 0

        # Calculate Effective Borehole Thermal Resistance R_b*
        # Single U-tube (series): each pipe leg carries the full borehole flow.
        # Using water properties at approx 15-20 degC (mu_f = 0.00114 Pa·s)
        m_flow_borehole = self.dV_f * cu.L2m3 * cu.s2m * rho_f  # Total borehole mass flow [kg/s]
        self.R_b = gf.calc_borehole_thermal_resistance(
            k_s=self.k_g,
            k_g=self.k_grout,
            k_p=self.k_p,
            r_b=self.r_b,
            r_out=self.r_out,
            r_in=self.r_in,
            D_s=self.D_s,
            H_b=self.H_b,
            m_flow_borehole=m_flow_borehole,
            rho_f=rho_f,
            mu_f=0.00114,
            cp_f=c_f,
            k_f=k_w,
        )

        # Fan parameters (VSD model)
        _hp_capacity = max(self.Q_rated_cooling, self.Q_rated_heating)
        self.dV_iu_fan_design = _hp_capacity / (rho_a * c_a * 10.0)
        self.E_iu_fan_design = self.dV_iu_fan_design * self.dP_iu_fan_design / self.eta_iu_fan_design
        self.vsd_coeffs_iu = SINGLE_ZONE_VAV_COEFFICIENTS.copy()
        self.fan_params_iu = {
            "fan_design_flow_rate": self.dV_iu_fan_design,
            "fan_design_power": self.E_iu_fan_design,
        }

        # Precompute dimensional g-function [mK/W]
        alpha = self.k_g / (self.rho_g * self.c_g)
        self.g_func_interp = precompute_gfunction(
            N_1=1,
            N_2=1,
            B=6.0,
            H_b=self.H_b,
            D_b=self.D_b,
            r_b=self.r_b,
            alpha_s=alpha,
            k_s=self.k_g,
            t_max_s=self.sim_hours * 3600.0,
            dt_s=self.dt_sec,
            boundary_condition=self.gfunc_boundary_condition,
        )
        # Load-pulse history and the step-mean response for every lag.
        self._pulses = np.zeros(0)
        self._gbar_rev = np.zeros(0)
        self._ensure_history_capacity(max(int(math.ceil(self.sim_hours / self.dt_hours)), 1) + 1)

    # ------------------------------------------------------------------
    # Ground response bookkeeping
    # ------------------------------------------------------------------
    def _ensure_history_capacity(self, n: int) -> None:
        """Make room for ``n`` steps of pulses and step-mean responses."""
        if n <= len(self._pulses):
            return
        n = max(n, 2 * len(self._pulses))
        pulses = np.zeros(n)
        pulses[: len(self._pulses)] = self._pulses
        self._pulses = pulses
        lags = np.arange(n) * self.dt_sec
        gbar = np.asarray(g_step_mean(self.g_func_interp, lags, self.dt_sec), dtype=float)
        # Reversed so that Σ_{i<n} p_i ḡ[n-i] is one contiguous dot product.
        self._gbar_rev = np.ascontiguousarray(gbar[::-1])

    def _history_effect(self, n: int) -> float:
        """Mean wall-temperature change over step ``n`` from the loads of steps ``0..n-1`` [K]."""
        if n == 0:
            return 0.0
        L = len(self._gbar_rev)
        # ḡ[n-i] for i = 0..n-1 is rev[L-1-n+i].
        return float(np.dot(self._pulses[:n], self._gbar_rev[L - 1 - n : L - 1]))

    def system_update(self):
        """Advance the model by one timestep.

        Call this method once per timestep after setting ``Q_r_iu``,
        ``T0`` and optionally ``T_a_room``. The method computes COP,
        temperatures, fan power, and component exergy balances.

        Status attributes set on every call:

        - ``failure_reason`` — ``"none"``, or ``"outside_curve_domain"``
          (entering air or water temperature outside the fitted range; the
          unit is off for the step and ``failure_detail`` names the variable)
          or ``"not_converged"`` (the ground/fluid iteration did not settle).
        - ``capacity_clamped`` — ``"max"`` when the request exceeds the
          available capacity ``Q_cap`` and ``None`` otherwise.
        - ``Q_iu`` — the load actually served [W], signed like ``Q_r_iu``.

        Raises
        ------
        ValueError
            For a non-finite ``Q_r_iu``, ``T0`` or ``T_a_room``.
        """
        if not hasattr(self, "T0"):
            raise AttributeError("T0 must be provided before system_update().")
        for name in ("Q_r_iu", "T0"):
            if not math.isfinite(float(getattr(self, name))):
                raise ValueError(f"{name} must be finite")
        if self.T_a_room is not None and not math.isfinite(float(self.T_a_room)):
            raise ValueError("T_a_room must be finite")

        # Unit conversion
        dV_f_m3s = self.dV_f * cu.s2m * cu.L2m3  # Nominal flow rate [m³/s]

        # Determine mode based on load sign
        if self.Q_r_iu > 0:
            mode = "cooling"
        elif self.Q_r_iu < 0:
            mode = "heating"
        else:
            mode = "off"
        T_a_room = T_A_ROOM_DEFAULT[mode] if self.T_a_room is None else float(self.T_a_room)
        Q_request = abs(float(self.Q_r_iu))

        # Temperatures in Kelvin
        self.T0_K = cu.C2K(self.T0)
        self.T_a_iu_in = T_a_room
        self.T_a_room_K = cu.C2K(T_a_room)
        self.T_g_K = cu.C2K(self.T_g)

        # ---------------------------------------------------------------------
        # A. Historical temperature effect (superposition of committed pulses)
        # ---------------------------------------------------------------------
        n = self._n_steps
        self._ensure_history_capacity(n + 2)
        T_b_history_effect = self._history_effect(n)
        q_prev = self.q_b_history[-1]
        # Step-mean response to the current step's own pulse [mK/W]
        self.g_i_dim = float(self._gbar_rev[-1])
        self.T_b_history_effect = T_b_history_effect

        def ground_state(q_b: float, dV: float) -> tuple[float, float, float, float]:
            """(T_b, T_f, T_f_in, T_f_out) [K] for a per-metre load q_b (injection-positive)."""
            T_b = self.T_g_K + T_b_history_effect + (q_b - q_prev) * self.g_i_dim
            T_f = T_b + q_b * self.R_b
            dT = q_b * self.H_b / (2 * c_f * rho_f * dV) if dV > 0 else 0.0
            return T_b, T_f, T_f + dT, T_f - dT

        failure_reason = "none"
        failure_detail = None
        capacity_clamped = None
        converged = True
        Q_cap = float("nan")
        COP = 0.0
        E_cmp = 0.0
        Q_served = 0.0
        Q_r_ghx = 0.0
        q_b = 0.0
        running = mode != "off"

        T_b, T_f, T_f_in, T_f_out = ground_state(0.0, 0.0)
        if running:
            Q_rated = self.Q_rated_cooling if mode == "cooling" else self.Q_rated_heating
            x_lo, x_hi = (v * GSHP_EQFIT_T_REF_K for v in GSHP_EQFIT_DOMAIN[mode]["x"])
            try:
                # Entering air (and the rated air-flow ratio y = 1) does not
                # depend on the iteration: check it once, at a mid-range EWT.
                gshp_equation_fit_ratios(self.T_a_room_K, 0.5 * (x_lo + x_hi), 1.0, mode)
            except EquationFitRangeError as exc:
                failure_reason, failure_detail = "outside_curve_domain", str(exc)
                running = False
        if running:
            # Iterate the entering-water temperature, starting from the fluid
            # temperature with this step unloaded. Iterates are evaluated with
            # the EWT held inside the fitted range; only the converged EWT
            # decides whether the point is inside the curve domain, so a
            # first guess outside the range does not switch off a valid point.
            max_iter, tol = 20, 1e-2
            converged = False
            for _ in range(max_iter):
                T_f_in_old = T_f_in
                T_eval = min(max(T_f_out, x_lo), x_hi)
                # Rated air flow while the compressor runs (y = 1).
                cap_ratio, pwr_ratio = gshp_equation_fit_ratios(self.T_a_room_K, T_eval, 1.0, mode)
                Q_cap = Q_rated * cap_ratio
                Q_served = min(Q_request, Q_cap)
                COP = GSHP_EQFIT_COP_REF[mode] * cap_ratio / pwr_ratio
                E_cmp = Q_served / COP
                Q_r_ghx = (Q_served + E_cmp) if mode == "cooling" else (-Q_served + E_cmp)
                q_b = (Q_r_ghx + self.E_pmp) / self.H_b
                T_b, T_f, T_f_in, T_f_out = ground_state(q_b, dV_f_m3s)
                if abs(T_f_in - T_f_in_old) < tol:
                    converged = True
                    break
            if not converged:
                failure_reason = "not_converged"
            elif not x_lo <= T_f_out <= x_hi:
                # The self-consistent entering water lies outside the fit.
                try:
                    gshp_equation_fit_ratios(self.T_a_room_K, T_f_out, 1.0, mode)
                except EquationFitRangeError as exc:
                    failure_reason, failure_detail = "outside_curve_domain", str(exc)
            if failure_reason == "none":
                capacity_clamped = "max" if Q_request > Q_cap else None
        if failure_reason != "none":
            # The unit does not run this step: no heat, no power, no ground load.
            running = False
            COP = float("nan")
            E_cmp = Q_served = Q_r_ghx = q_b = 0.0
            T_b, T_f, T_f_in, T_f_out = ground_state(0.0, 0.0)

        mode_run = mode if running else "off"
        if mode_run == "cooling":
            dT_r_ghx, dT_r_iu, dT_a_iu = 3.0, -15.0, -10.0
        elif mode_run == "heating":
            dT_r_ghx, dT_r_iu, dT_a_iu = -3.0, 15.0, 10.0
        else:
            dT_r_ghx, dT_r_iu, dT_a_iu = 0.0, 0.0, 0.0
        dV_f_m3s_active = dV_f_m3s if running else 0.0
        E_pmp_active = self.E_pmp if running else 0.0

        self.failure_reason = failure_reason
        self.failure_detail = failure_detail
        self.converged = converged and failure_reason == "none"
        self.capacity_clamped = capacity_clamped
        self.Q_cap = Q_cap
        self.Q_iu = math.copysign(Q_served, self.Q_r_iu) if running else 0.0
        self.COP = COP
        self.E_cmp = E_cmp
        self.Q_r_ghx = Q_r_ghx
        self.q_b = q_b
        self.T_b, self.T_f, self.T_f_in, self.T_f_out = T_b, T_f, T_f_in, T_f_out
        self.dT_r_ghx = dT_r_ghx
        self.dT_r_iu = dT_r_iu
        if mode_run == "off":
            self.T_r_ghx = self.T0
            self.T_r_iu = self.T0
            self.T_r_iu_K = cu.C2K(self.T0)
            self.T_r_ghx_K = cu.C2K(self.T0)
        else:
            self.T_r_iu = T_a_room + dT_r_iu  # Indoor unit refrigerant [°C]
            self.T_r_iu_K = cu.C2K(self.T_r_iu)
            # Refrigerant on the ground side, from the entering water
            self.T_r_ghx_K = self.T_f_out + dT_r_ghx
            self.T_r_ghx = cu.K2C(self.T_r_ghx_K)

        # Indoor air flow (VAV surrogate: 10 K air temperature change)
        self.T_a_iu_out_K = self.T_a_room_K + dT_a_iu
        self.dV_a = Q_served / (c_a * rho_a * abs(dT_a_iu)) if running else 0.0

        # ---------------------------------------------------------------------
        # C. Store the finalized load to history for the next timestep
        # ---------------------------------------------------------------------
        self._pulses[n] = q_b - q_prev
        self.q_b_history.append(q_b)
        self._n_steps = n + 1
        self.time += self.dt_hours
        # ---------------------------------------------------------------------

        # Temperature
        self.T_a_iu_in_K = self.T_a_room_K
        self.T_a_iu_out = cu.K2C(self.T_a_iu_out_K)

        # Fan power (VSD model from hx_fan)
        self.E_fan_iu = calc_fan_power_from_dV_fan(
            dV_fan=self.dV_a,
            fan_params=self.fan_params_iu,
            vsd_coeffs=self.vsd_coeffs_iu,
            is_active=running,
        )

        # System COP calculation
        total_pwr = self.E_cmp + self.E_fan_iu + E_pmp_active
        if not running and mode != "off":
            self.COP_sys = float("nan")
        elif total_pwr > 0:
            self.COP_sys = Q_served / total_pwr
        else:
            self.COP_sys = 0.0

        # Helper for thermal exergy
        def get_thermal_exergy(c, rho, dV, T_stream, T_env):
            if T_stream <= 0 or dV <= 0:
                return 0.0
            return c * rho * dV * ((T_stream - T_env) - T_env * math.log(T_stream / T_env))

        # -------------------------------------------------------------
        # Exergy of air streams
        # -------------------------------------------------------------
        self.X_a_iu_in = get_thermal_exergy(c_a, rho_a, self.dV_a, self.T_a_iu_in_K, self.T0_K)
        self.X_a_iu_out = get_thermal_exergy(c_a, rho_a, self.dV_a, self.T_a_iu_out_K, self.T0_K)

        # -------------------------------------------------------------
        # Exergy of refrigerant streams
        # -------------------------------------------------------------
        if not running:
            self.X_g = 0.0
            self.X_b = 0.0
            self.X_r_iu = 0.0
            self.X_r_ghx = 0.0
        else:
            self.X_g = (1 - self.T0_K / self.T_g_K) * (-self.q_b * self.H_b)
            self.X_b = (1 - self.T0_K / self.T_b) * (-self.q_b * self.H_b)
            self.X_r_iu = -self.Q_iu * (1 - self.T0_K / self.T_r_iu_K)
            self.X_r_ghx = -self.Q_r_ghx * (1 - self.T0_K / self.T_r_ghx_K)

        # -------------------------------------------------------------
        # Exergy of water streams
        # -------------------------------------------------------------
        self.X_f_in = get_thermal_exergy(c_f, rho_f, dV_f_m3s_active, self.T_f_in, self.T0_K)
        self.X_f_out = get_thermal_exergy(c_f, rho_f, dV_f_m3s_active, self.T_f_out, self.T0_K)

        # -------------------------------------------------------------
        # Component exergy balance
        # -------------------------------------------------------------
        if not running:
            self.X_in_g = self.X_out_g = self.X_c_g = 0.0
            self.X_in_ghx = self.X_out_ghx = self.X_c_ghx = 0.0
            self.X_in_r = self.X_out_r = self.X_c_r = 0.0
            self.X_in_iu = self.X_out_iu = self.X_c_iu = 0.0
        else:
            # Ground
            self.X_in_g = self.X_g
            self.X_out_g = self.X_b
            self.X_c_g = self.X_in_g - self.X_out_g

            # Ground heat exchanger
            self.X_in_ghx = self.X_b + E_pmp_active
            self.X_out_ghx = self.X_r_ghx
            self.X_c_ghx = self.X_in_ghx - self.X_out_ghx

            # Refrigerant loop
            self.X_in_r = self.X_r_ghx + self.E_cmp
            self.X_out_r = self.X_r_iu
            self.X_c_r = self.X_in_r - self.X_out_r

            # Indoor unit
            self.X_in_iu = self.E_fan_iu + self.X_r_iu
            self.X_out_iu = self.X_a_iu_out - self.X_a_iu_in
            self.X_c_iu = self.X_in_iu - self.X_out_iu

        # -------------------------------------------------------------
        # Exergy efficiency
        # -------------------------------------------------------------
        if not running:
            self.X_eff = 0.0
        else:
            self.X_eff = (self.X_a_iu_out - self.X_a_iu_in) / (self.E_fan_iu + self.E_cmp + E_pmp_active)

        # -------------------------------------------------------------
        # Structured exergy balance
        # -------------------------------------------------------------
        self.exergy_bal = {
            "indoor unit": {
                "in": {
                    "X_r_iu": self.X_r_iu,
                    "E_fan_iu": self.E_fan_iu,
                },
                "out": {
                    "X_a_iu_out": self.X_a_iu_out,
                    "X_a_iu_in": self.X_a_iu_in,
                },
                "con": {"X_c_iu": self.X_c_iu},
            },
            "refrigerant loop": {
                "in": {
                    "X_r_ghx": self.X_r_ghx,
                    "E_cmp": self.E_cmp,
                },
                "out": {"X_r_iu": self.X_r_iu},
                "con": {"X_c_r": self.X_c_r},
            },
            "ground heat exchanger": {
                "in": {
                    "X_b": self.X_b,
                    "E_pmp": E_pmp_active,
                },
                "out": {"X_r_ghx": self.X_r_ghx},
                "con": {"X_c_ghx": self.X_c_ghx},
            },
            "ground": {
                "in": {"X_g": self.X_g},
                "out": {"X_b": self.X_b},
                "con": {"X_c_g": self.X_c_g},
            },
        }
