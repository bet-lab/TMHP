"""Attachable subsystems for heat-pump boiler models.

Given physical input state each subsystem returns a standardised output dict.
All simulation orchestration (activation logic, result assembly, exergy
calculation) is the responsibility of the scenario class that uses the
subsystem. The solar and UV subsystems are stateless pure-physics engines;
``EnergyStorageSystem`` additionally carries battery state (state of charge),
so it is stateful across time steps. A scenario class must call
:meth:`EnergyStorageSystem.reset` at the start of every run so that two runs
of the same model with the same inputs give the same result.

Subsystem catalogue
-------------------
- ``SolarThermalCollector`` — flat-plate / evacuated-tube STC physics engine
  (``calc_performance``, ``is_preheat_on``); stateless
- ``PhotovoltaicSystem`` — PV array + inverter chain; stateless
- ``EnergyStorageSystem`` — battery storage; stateful (tracks state of charge,
  ``reset()`` restores ``SOC_init``)
- ``route_pv_ess`` — one-step PV → inverter / ESS / dump / grid routing shared by
  ``ASHPB_PV_ESS`` and ``GSHPB_PV_ESS`` (importable, not star-exported)
- ``UVLamp`` — UV disinfection lamp; stateless
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from . import calc_util as cu
from .constants import c_w, k_a, k_D, k_d, rho_w
from .thermodynamics import calc_energy_flow

if TYPE_CHECKING:
    import pandas as pd

    from .dynamic_context import ControlState, StepContext, SubsystemExergy

__all__ = [
    "EnergyStorageSystem",
    "PhotovoltaicSystem",
    "SolarThermalCollector",
    "UVLamp",
]

#: Grid import below this is numerical noise, not a physical draw [W].
#: 1 mW sits twelve orders of magnitude above the double-precision round-off of a
#: kW-scale power balance (~1e-12 W) and far below any meter resolution, so a
#: ratio taken against a larger import is meaningful and one against a smaller
#: import is not.
GRID_IMPORT_FLOOR_W: float = 1e-3


# ------------------------------------------------------------------
# Default subsystem step() return — STC absent/inactive
# ------------------------------------------------------------------

STC_OFF_STEP: dict = {
    "stc_active": False,
    "stc_result": {},
    "T_stc_w_out_K": np.nan,
    "T_stc_pump_w_out_K": np.nan,
    "Q_stc_w_out": 0.0,
    "Q_stc_pump_w_out": 0.0,
    "Q_stc_w_in": 0.0,
    "E_stc_pump": 0.0,
    "Q_contribution": 0.0,
    "E_subsystem": 0.0,
    "T_tank_w_in_override_K": None,
}

# Backward-compatible alias
STC_OFF: dict = STC_OFF_STEP


@dataclass
class SolarThermalCollector:
    """Solar Thermal Collector (STC) — pure physics engine.

    Bundles collector geometry, optical and thermal properties,
    and pump parameters.  This class is a **stateless physics
    calculator**: given physical inputs it returns a performance
    dict.  All simulation orchestration (activation logic,
    result assembly, exergy calculation) is the responsibility
    of the scenario class that uses this engine.

    The public API consists of:

    - :meth:`calc_performance` — single-timestep thermal performance
    - :meth:`is_preheat_on` — schedule check

    Parameters
    ----------
    A_stc : float
        Collector gross area [m²].
    stc_tilt : float
        Tilt from horizontal [°].
    stc_azimuth : float
        Azimuth angle (180 = south) [°].
    A_stc_pipe : float
        Pipe surface area [m²].
    alpha_stc : float
        Absorptivity [–].
    h_o_stc : float
        External convective coefficient [W/(m²·K)].
    h_r_stc : float
        Radiative coefficient [W/(m²·K)].
    k_ins_stc : float
        Insulation conductivity [W/(m·K)].
    x_air_stc : float
        Air gap thickness [m].
    x_ins_stc : float
        Insulation thickness [m].
    preheat_start_hour : float
        Preheat window start hour.
    preheat_end_hour : float
        Preheat window end hour.
    dV_stc_w : float
        Default STC loop flow rate [m³/s].
    E_stc_pump : float
        STC pump rated power [W].
    """

    # Collector
    A_stc: float = 2.0
    stc_tilt: float = 35.0
    stc_azimuth: float = 180.0
    A_stc_pipe: float = 2.0
    alpha_stc: float = 0.95
    h_o_stc: float = 15.0
    h_r_stc: float = 2.0
    k_ins_stc: float = 0.03
    x_air_stc: float = 0.01
    x_ins_stc: float = 0.05

    # Pump / schedule
    preheat_start_hour: float = 6.0
    preheat_end_hour: float = 18.0
    dV_stc_w: float = 0.001
    E_stc_pump: float = 50.0

    # ----------------------------------------------------------
    # Physics helpers
    # ----------------------------------------------------------

    def calc_overall_heat_transfer_coeff(self) -> float:
        """Compute overall U-value from parallel resistances.

        The collector has two heat-loss paths in parallel:

        - **Path 1** (top): radiation gap ‖ air gap → external conv
        - **Path 2** (bottom): insulation → external conv

        Returns
        -------
        float
            Overall heat-loss coefficient [W/(m²·K)].
        """
        R_air = self.x_air_stc / k_a
        R_ins = self.x_ins_stc / self.k_ins_stc
        R_o = 1.0 / self.h_o_stc
        R_r = 1.0 / self.h_r_stc

        # Path 1: radiation ‖ air gap (parallel), then + external
        R1 = (R_r * R_air) / (R_r + R_air) + R_o
        # Path 2: insulation (series) + external
        R2 = R_ins + R_o

        # Two paths in parallel
        return 1.0 / R1 + 1.0 / R2

    def _calc_solar_absorption(
        self,
        I_DN_stc: float,
        I_dH_stc: float,
    ) -> tuple[float, float]:
        """Compute total irradiance and absorbed heat.

        Parameters
        ----------
        I_DN_stc : float
            Direct-normal irradiance [W/m²].
        I_dH_stc : float
            Diffuse-horizontal irradiance [W/m²].

        Returns
        -------
        tuple[float, float]
            ``(I_sol_stc, Q_sol_stc)`` — total irradiance and
            absorbed solar heat rate [W].
        """
        I_sol = I_DN_stc + I_dH_stc
        Q_sol = I_sol * self.A_stc_pipe * self.alpha_stc
        return I_sol, Q_sol

    def _calc_outlet_temperature(
        self,
        U_stc: float,
        G_stc: float,
        Q_sol_stc: float,
        Q_stc_w_in: float,
        T_stc_w_in_K: float,
        T0_K: float,
    ) -> tuple[float, float, float]:
        """Solve for collector outlet and mean plate temps.

        Parameters
        ----------
        U_stc : float
            Overall U-value [W/(m²·K)].
        G_stc : float
            Heat capacity flow rate ``c_w · ρ_w · V̇`` [W/K].
        Q_sol_stc : float
            Absorbed solar heat [W].
        Q_stc_w_in : float
            Inlet energy flow [W].
        T_stc_w_in_K : float
            Inlet water temperature [K].
        T0_K : float
            Dead-state temperature [K].

        Returns
        -------
        tuple[float, float, float]
            ``(T_stc_w_out_K, T_stc_K, ksi_stc)`` — outlet temp,
            mean plate temp, and dimensionless parameter.
        """
        A_U = max(1e-6, self.A_stc_pipe * U_stc)
        ksi = np.exp(-A_U / G_stc)

        T_out_K = ksi * T_stc_w_in_K + (1 - ksi) * T0_K + (1 - ksi) * Q_sol_stc / A_U
        T_stc_K = T0_K + (Q_sol_stc - G_stc * (T_out_K - T_stc_w_in_K)) / A_U

        return T_out_K, T_stc_K, ksi

    # ----------------------------------------------------------
    # Main performance calculation
    # ----------------------------------------------------------

    def calc_performance(
        self,
        I_DN_stc: float,
        I_dH_stc: float,
        T_stc_w_in_K: float,
        T0_K: float,
        dV_stc: float | None = None,
        is_active: bool = True,
    ) -> dict:
        """Compute STC thermal performance for one timestep.

        Parameters
        ----------
        I_DN_stc : float
            Direct-normal irradiance [W/m²].
        I_dH_stc : float
            Diffuse-horizontal irradiance [W/m²].
        T_stc_w_in_K : float
            Inlet water temperature [K].
        T0_K : float
            Dead-state temperature [K].
        dV_stc : float | None
            Override flow rate [m³/s]; defaults to
            ``self.dV_stc_w``.
        is_active : bool
            If ``False``, return NaN-filled dict.

        Returns
        -------
        dict
            Performance results including:
            ``I_sol_stc``, ``Q_sol_stc``, ``Q_stc_w_in``,
            ``Q_stc_w_out``, ``Q_stc_pump_w_out``,
            ``ksi_stc``, ``T_stc_w_out_K``,
            ``T_stc_pump_w_out_K``, ``T_stc_w_in_K``,
            ``T_stc_K``, ``Q_l_stc``.
        """
        if dV_stc is None:
            dV_stc = self.dV_stc_w

        if not is_active:
            return {
                "I_sol_stc": np.nan,
                "Q_sol_stc": np.nan,
                "S_sol_stc": np.nan,
                "X_sol_stc": np.nan,
                "Q_stc_w_in": np.nan,
                "Q_stc_w_out": np.nan,
                "Q_stc_pump_w_out": np.nan,
                "ksi_stc": np.nan,
                "T_stc_pump_w_out_K": T_stc_w_in_K,
                "T_stc_w_out_K": T_stc_w_in_K,
                "T_stc_w_in_K": T_stc_w_in_K,
                "T_stc_K": np.nan,
                "Q_l_stc": np.nan,
            }

        U_stc = self.calc_overall_heat_transfer_coeff()
        I_sol_stc, Q_sol_stc = self._calc_solar_absorption(
            I_DN_stc,
            I_dH_stc,
        )
        S_sol_stc = (k_D * I_DN_stc**0.9 + k_d * I_dH_stc**0.9) * self.A_stc_pipe
        X_sol_stc = Q_sol_stc - S_sol_stc * T0_K

        G_stc = c_w * rho_w * dV_stc
        Q_stc_w_in = calc_energy_flow(
            G_stc,
            T_stc_w_in_K,
            T0_K,
        )

        T_out_K, T_stc_K, ksi = self._calc_outlet_temperature(
            U_stc,
            G_stc,
            Q_sol_stc,
            Q_stc_w_in,
            T_stc_w_in_K,
            T0_K,
        )

        # Pump heat addition
        T_stc_pump_w_out_K = T_out_K + self.E_stc_pump / G_stc

        # Heat transport rates
        Q_stc_w_out = calc_energy_flow(
            G_stc,
            T_out_K,
            T0_K,
        )
        Q_stc_pump_w_out = calc_energy_flow(
            G_stc,
            T_stc_pump_w_out_K,
            T0_K,
        )

        # Collector heat loss
        Q_l_stc = self.A_stc_pipe * U_stc * (T_stc_K - T0_K)

        return {
            "I_sol_stc": I_sol_stc,
            "Q_sol_stc": Q_sol_stc,
            "S_sol_stc": S_sol_stc,
            "X_sol_stc": X_sol_stc,
            "Q_stc_w_in": Q_stc_w_in,
            "Q_stc_w_out": Q_stc_w_out,
            "Q_stc_pump_w_out": Q_stc_pump_w_out,
            "ksi_stc": ksi,
            "T_stc_pump_w_out_K": T_stc_pump_w_out_K,
            "T_stc_w_out_K": T_out_K,
            "T_stc_w_in_K": T_stc_w_in_K,
            "T_stc_K": T_stc_K,
            "Q_l_stc": Q_l_stc,
        }

    # ----------------------------------------------------------
    # Schedule helper
    # ----------------------------------------------------------

    def is_preheat_on(self, hour_of_day: float) -> bool:
        """Check whether *hour_of_day* falls in the window.

        Parameters
        ----------
        hour_of_day : float
            Hour within the day (0–24).

        Returns
        -------
        bool
        """
        return self.preheat_start_hour <= hour_of_day < self.preheat_end_hour


# ------------------------------------------------------------------
# Photovoltaic System


@dataclass
class PhotovoltaicSystem:
    """Photovoltaic System (PV + Charge Controller) — pure physics engine.

    Computes PV energy generation from irradiance inputs.  This class
    is a **stateless physics calculator**: given physical inputs it
    returns a performance dict.  All routing logic (ESS charge/discharge,
    Grid import, dump) is the responsibility of the scenario class.

    The public API:

    - :meth:`calc_performance` — single-timestep PV generation
    """

    # Panel physics
    A_pv: float = 5.0
    alp_pv: float = 0.9
    pv_tilt: float = 35.0
    pv_azimuth: float = 180.0
    h_o: float = 15.0

    # Efficiencies
    eta_pv: float = 0.15
    beta_pv: float = 0.0045
    T_ref_pv_K: float = 298.15
    eta_ctrl: float = 0.95
    T_ctrl_K: float = 308.15

    def calc_performance(
        self,
        I_DN: float,
        I_dH: float,
        T0_K: float,
    ) -> dict:
        """Compute PV generation for one timestep.

        Parameters
        ----------
        I_DN : float
            Direct-normal irradiance [W/m²].
        I_dH : float
            Diffuse-horizontal irradiance [W/m²].
        T0_K : float
            Dead-state (ambient) temperature [K].

        Returns
        -------
        dict
            Keys: ``I_sol_pv``, ``T_pv_K``, ``eta_pv_actual``, ``E_pv_out``, ``E_ctrl_out``,
            ``Q_l_pv``, ``Q_l_ctrl``, ``X_sol``, ``X_pv_out``,
            ``X_ctrl_out``, ``X_c_pv``, ``X_c_ctrl``,
            ``X_l_pv``, ``X_l_ctrl``.
        """
        T0 = max(1e-3, T0_K)
        I_sol = I_DN + I_dH

        # ── PV Cell ──────────────────────────────────────────────
        T_pv_K_approx = T0 + (I_sol * (self.alp_pv - self.eta_pv)) / (2.0 * self.h_o)

        eta_pv_actual = self.eta_pv * (1.0 - self.beta_pv * (T_pv_K_approx - self.T_ref_pv_K))
        eta_pv_actual = max(0.0, min(self.alp_pv, eta_pv_actual))

        T_pv_K = T0 + (I_sol * (self.alp_pv - eta_pv_actual)) / (2.0 * self.h_o)
        E_pv_out = self.A_pv * eta_pv_actual * I_sol
        Q_l_pv = 2.0 * self.A_pv * self.h_o * (T_pv_K - T0)

        s_sol = k_D * (I_DN**0.9) + k_d * (I_dH**0.9)
        S_sol = self.A_pv * self.alp_pv * s_sol
        S_l_pv = (1.0 / T_pv_K) * Q_l_pv if T_pv_K > 0 else 0.0
        S_g_pv = S_l_pv - S_sol  # S_pv_out = 0 (electricity)

        X_sol = self.A_pv * self.alp_pv * (I_sol - s_sol * T0)
        X_pv_out = E_pv_out
        X_l_pv = (1.0 - T0 / T_pv_K) * Q_l_pv if T_pv_K > 0 else 0.0
        X_c_pv = S_g_pv * T0

        # ── Charge Controller ─────────────────────────────────────
        E_ctrl_out = self.eta_ctrl * E_pv_out
        Q_l_ctrl = (1.0 - self.eta_ctrl) * E_pv_out
        S_l_ctrl = Q_l_ctrl / self.T_ctrl_K
        S_g_ctrl = S_l_ctrl  # S_ctrl_out=0, S_in=0 (electricity)

        X_ctrl_out = E_ctrl_out
        X_l_ctrl = Q_l_ctrl - S_l_ctrl * T0
        X_c_ctrl = S_g_ctrl * T0

        return {
            "I_sol_pv": I_sol,
            "T_pv_K": T_pv_K,
            "eta_pv": eta_pv_actual,
            "E_pv_out": E_pv_out,
            "E_ctrl_out": E_ctrl_out,
            "Q_l_pv": Q_l_pv,
            "Q_l_ctrl": Q_l_ctrl,
            "X_sol": X_sol,
            "X_pv_out": X_pv_out,
            "X_ctrl_out": X_ctrl_out,
            "X_c_pv": X_c_pv,
            "X_c_ctrl": X_c_ctrl,
            "X_l_pv": X_l_pv,
            "X_l_ctrl": X_l_ctrl,
        }


# ------------------------------------------------------------------
# Energy Storage System
# ------------------------------------------------------------------


@dataclass
class EnergyStorageSystem:
    """Energy Storage System (Battery) — pure physics engine.

    Accepts charging / discharging *requests* (DC power, [W]) and
    enforces capacity and SOC limits.  Internal losses and exergy
    destruction are computed from round-trip efficiencies.

    State (``SOC_ess``) is updated in-place at each call to
    :meth:`charge` or :meth:`discharge`.  Routing decisions (which
    request to fulfil and in what order) are entirely the
    responsibility of the scenario class.

    The state is part of the *run*, not of the model: the scenario classes
    call :meth:`reset` when a run starts, so a second ``analyze_dynamic`` on
    the same model starts from ``SOC_init`` again.  Do not share one instance
    between models that are stepped at the same time.

    Energy bookkeeping per call (``E`` are DC powers at the battery
    terminals, ``C = C_ess_max``)::

        charge:     C·ΔSOC/dt =  η_chg · E_ess_chg
        discharge:  C·ΔSOC/dt = -E_ess_dis / η_dis
        Q_l_ess     = (1 - η_chg)·E_ess_chg + (1/η_dis - 1)·E_ess_dis

    so ``E_ess_chg - E_ess_dis = C·ΔSOC/dt + Q_l_ess``.  A state of charge
    outside ``[SOC_min, SOC_max]`` is never moved *toward* the band by a
    call (a battery that starts below ``SOC_min`` cannot discharge, but it is
    not topped up for free either).

    Parameters
    ----------
    C_ess_max : float
        Rated energy capacity [J].  Default 3.6 MJ (= 1 kWh).
    SOC_init : float
        Initial state of charge [–].
    SOC_min : float
        Minimum allowable SOC [–] (depth-of-discharge guard).
    SOC_max : float
        Maximum allowable SOC [–].
    eta_ess_chg : float
        Charging efficiency (electricity-in to stored) [–].
    eta_ess_dis : float
        Discharging efficiency (stored to electricity-out) [–].
    T_ess_K : float
        Representative battery temperature used for entropy calc [K].
    """

    C_ess_max: float = 3.6e6
    SOC_init: float = 0.0
    SOC_min: float = 0.10
    SOC_max: float = 1.00
    eta_ess_chg: float = 0.90
    eta_ess_dis: float = 0.90
    T_ess_K: float = 313.15

    SOC_ess: float = field(init=False)

    def __post_init__(self) -> None:
        self.SOC_ess = self.SOC_init

    # ── helpers ──────────────────────────────────────────────────

    def _max_chg_power(self, dt: float) -> float:
        """Maximum charging power limited by remaining capacity [W]."""
        headroom = (self.SOC_max - self.SOC_ess) * self.C_ess_max
        return max(0.0, headroom / (self.eta_ess_chg * dt)) if dt > 0 else 0.0

    def _max_dis_power(self, dt: float) -> float:
        """Maximum discharge power limited by SOC_min [W]."""
        available = (self.SOC_ess - self.SOC_min) * self.C_ess_max
        return max(0.0, available * self.eta_ess_dis / dt) if dt > 0 else 0.0

    def _exergy(self, E_chg: float, E_dis: float, T0_K: float) -> dict:
        T0 = max(1e-3, T0_K)
        Q_l = (1.0 - self.eta_ess_chg) * E_chg + (1.0 / self.eta_ess_dis - 1.0) * E_dis
        S_l = Q_l / self.T_ess_K
        X_l_ess = Q_l - S_l * T0
        X_c_ess = S_l * T0
        return {"Q_l_ess": Q_l, "X_l_ess": X_l_ess, "X_c_ess": X_c_ess}

    # ── public API ───────────────────────────────────────────────

    def reset(self) -> None:
        """Restore the state of charge to ``SOC_init`` (start of a run)."""
        self.SOC_ess = self.SOC_init

    def charge(self, E_req_chg: float, dt: float, T0_K: float) -> dict:
        """Request to charge the ESS with *E_req_chg* [W] for *dt* [s].

        Returns a dict with keys ``E_ess_chg``, ``E_ess_dis``, ``SOC_ess``
        plus ``Q_l_ess`` (charging loss [W]) and exergy keys.
        """
        E_chg = min(max(E_req_chg, 0.0), self._max_chg_power(dt))
        soc = self.SOC_ess + E_chg * self.eta_ess_chg * dt / self.C_ess_max
        # Round-off guard only: never pull a state that is already above
        # SOC_max down (``E_chg`` is 0 there), and never exceed it by round-off.
        self.SOC_ess = min(soc, max(self.SOC_max, self.SOC_ess))
        return {
            "E_ess_chg": E_chg,
            "E_ess_dis": 0.0,
            "SOC_ess": self.SOC_ess,
            **self._exergy(E_chg, 0.0, T0_K),
        }

    def discharge(self, E_req_dis: float, dt: float, T0_K: float) -> dict:
        """Request to discharge *E_req_dis* [W] from the ESS for *dt* [s].

        Returns a dict with keys ``E_ess_dis`` (actual), ``E_ess_chg``,
        ``SOC_ess`` plus ``Q_l_ess`` (discharging loss [W]) and exergy keys.
        """
        E_dis = min(max(E_req_dis, 0.0), self._max_dis_power(dt))
        soc = self.SOC_ess - E_dis / self.eta_ess_dis * dt / self.C_ess_max
        # Round-off guard only: a state below SOC_min stays where it is
        # (E_dis is 0 there) instead of being raised to SOC_min for free.
        self.SOC_ess = max(soc, min(self.SOC_min, self.SOC_ess))
        return {
            "E_ess_chg": 0.0,
            "E_ess_dis": E_dis,
            "SOC_ess": self.SOC_ess,
            **self._exergy(0.0, E_dis, T0_K),
        }


# ------------------------------------------------------------------
# PV → inverter / ESS / dump / grid routing (one step)
# ------------------------------------------------------------------


def route_pv_ess(
    pv_result: dict,
    ess: EnergyStorageSystem,
    E_load: float,
    eta_inv: float,
    dt: float,
    T0_K: float,
) -> dict:
    """Route one step of PV power to the load, the battery, the dump and the grid.

    Shared by :class:`~tmhp.ASHPB_PV_ESS` and :class:`~tmhp.GSHPB_PV_ESS`.
    All routing logic lives here; the PV, ESS and inverter are physics only.

    The system has no grid export: DC power that neither the load nor the
    battery can take is **dumped** (``E_dump``).

    Parameters
    ----------
    pv_result : dict
        Output of :meth:`PhotovoltaicSystem.calc_performance`.
    ess : EnergyStorageSystem
        Battery; its state of charge is advanced by this call.
    E_load : float
        AC electric load of the system this step [W] (``E_tot``).
    eta_inv : float
        Inverter DC→AC efficiency, ``0 < eta_inv <= 1``.
    dt : float
        Time step [s].
    T0_K : float
        Dead-state temperature [K].

    Returns
    -------
    dict
        Powers [W] — DC unless noted:
        ``E_pv_to_inv`` (PV power sent to the inverter),
        ``E_ess_chg`` / ``E_ess_dis`` (battery terminals),
        ``E_dc_to_inv`` (``E_pv_to_inv + E_ess_dis``),
        ``E_inv_out`` (AC out of the inverter, ``eta_inv * E_dc_to_inv``),
        ``E_grid_import`` (AC), ``E_dump``,
        losses ``Q_l_ctrl``, ``Q_l_inv``, ``Q_l_ess`` and ``ess_result``
        (the raw dict of the battery call).

    Notes
    -----
    Two balances close every step (to round-off)::

        E_pv_out  = Q_l_ctrl + E_pv_to_inv + E_ess_chg + E_dump
        E_load    = E_inv_out + E_grid_import
        E_ess_chg - E_ess_dis = C_ess_max·ΔSOC/dt + Q_l_ess

    and together::

        E_pv_out + E_grid_import = E_load + Q_l_ctrl + Q_l_inv + Q_l_ess
                                   + C_ess_max·ΔSOC/dt + E_dump

    ``eta_ctrl`` (charge controller) acts between ``E_pv_out`` and
    ``E_ctrl_out``; ``eta_inv`` acts on the DC power that reaches the inverter
    (PV direct plus battery discharge); ``eta_ess_chg`` / ``eta_ess_dis`` act
    inside the battery.
    """
    if not 0.0 < eta_inv <= 1.0:
        raise ValueError(f"eta_inv must be in (0, 1], got {eta_inv!r}")
    E_ctrl_out: float = pv_result["E_ctrl_out"]
    E_load = max(float(E_load), 0.0)

    if E_load == 0.0:
        # Load off: all controller output goes to the battery, the rest is dumped.
        ess_r = ess.charge(E_ctrl_out, dt, T0_K)
        E_pv_to_inv = 0.0
        E_dis = 0.0
        E_dump = max(0.0, E_ctrl_out - ess_r["E_ess_chg"])
        E_dc_short = 0.0
    else:
        E_dc_req = E_load / eta_inv
        if E_ctrl_out >= E_dc_req:
            # PV covers the load; the excess charges the battery, the rest is dumped.
            E_pv_to_inv = E_dc_req
            E_dc_excess = E_ctrl_out - E_dc_req
            ess_r = ess.charge(E_dc_excess, dt, T0_K)
            E_dis = 0.0
            E_dump = max(0.0, E_dc_excess - ess_r["E_ess_chg"])
            E_dc_short = 0.0
        else:
            # PV deficit: the battery discharges, the grid supplies what is left.
            E_pv_to_inv = E_ctrl_out
            E_ess_needed = E_dc_req - E_ctrl_out
            ess_r = ess.discharge(E_ess_needed, dt, T0_K)
            E_dis = ess_r["E_ess_dis"]
            E_dump = 0.0
            # Exactly 0.0 when the battery delivers the whole request (no
            # round-off residue that a ratio could blow up).
            E_dc_short = E_ess_needed - E_dis

    E_dc_to_inv = E_pv_to_inv + E_dis
    E_inv_out = eta_inv * E_dc_to_inv
    E_grid_import = eta_inv * E_dc_short
    return {
        "E_pv_to_inv": E_pv_to_inv,
        "E_ess_chg": ess_r["E_ess_chg"],
        "E_ess_dis": E_dis,
        "E_dc_to_inv": E_dc_to_inv,
        "E_inv_out": E_inv_out,
        "E_grid_import": E_grid_import,
        "E_dump": E_dump,
        "Q_l_ctrl": pv_result["Q_l_ctrl"],
        "Q_l_inv": (1.0 - eta_inv) * E_dc_to_inv,
        "Q_l_ess": ess_r["Q_l_ess"],
        "ess_result": ess_r,
    }


def stc_result_columns(stc_result: dict, T_stc_w_in_K: float, dV_stc: float) -> dict:
    """Energy columns of one collector evaluation, named as in ``GSHPB_STC_tank``.

    ``Q_stc_w_out - Q_stc_w_in`` is the heat the collector adds to its fluid and
    ``Q_sol_stc = (Q_stc_w_out - Q_stc_w_in) + Q_l_stc`` closes the collector
    balance.  All collector columns are NaN while the collector is inactive.
    """
    T_out = stc_result.get("T_stc_w_out_K", np.nan)
    T_pump_out = stc_result.get("T_stc_pump_w_out_K", T_out)
    return {
        "I_sol_stc [W/m2]": stc_result.get("I_sol_stc", np.nan),
        "Q_sol_stc [W]": stc_result.get("Q_sol_stc", np.nan),
        "S_sol_stc [W/K]": stc_result.get("S_sol_stc", np.nan),
        "X_sol_stc [W]": stc_result.get("X_sol_stc", np.nan),
        "Q_stc_w_out [W]": stc_result.get("Q_stc_w_out", np.nan),
        "Q_stc_pump_w_out [W]": stc_result.get("Q_stc_pump_w_out", np.nan),
        "Q_stc_w_in [W]": stc_result.get("Q_stc_w_in", np.nan),
        "Q_l_stc [W]": stc_result.get("Q_l_stc", np.nan),
        "dV_stc [m3/s]": dV_stc,
        "T_stc_w_out [°C]": cu.K2C(T_out) if not np.isnan(T_out) else np.nan,
        "T_stc_pump_w_out [°C]": cu.K2C(T_pump_out) if not np.isnan(T_pump_out) else np.nan,
        "T_stc_w_in [°C]": cu.K2C(T_stc_w_in_K),
        "T_stc [°C]": cu.K2C(stc_result.get("T_stc_K", np.nan)),
    }


def warn_if_uv_ignored(model: object) -> None:
    """Warn when a scenario that never steps its subsystems was given a UV lamp.

    The STC scenario classes drive their own collector and do not step the
    subsystems registered through the ``uv=`` argument of the base model, so the
    lamp neither draws power nor heats the tank there.  Say so instead of
    silently dropping it.
    """
    if "uv" in getattr(model, "_subsystems", {}):
        warnings.warn(
            f"{type(model).__name__} does not simulate the 'uv' lamp: its power and heat are "
            "not part of the results (E_tot excludes it). Use ASHPB_PV_ESS / GSHPB_PV_ESS or the "
            "base boiler classes for a UV lamp.",
            UserWarning,
            stacklevel=3,
        )


def pv_ess_result_columns(
    pv_result: dict,
    route: dict,
    eta_inv: float,
    T_inv_K: float,
    T0_K: float,
) -> dict:
    """Result-row columns of one PV/ESS/inverter routing step.

    Column meaning (all [W] unless noted):

    - ``E_pv_out`` PV array DC output; ``E_ctrl_out`` after the charge
      controller (``Q_l_ctrl`` is the difference).
    - ``E_pv_to_inv`` DC PV power sent to the inverter (self-consumption path).
    - ``E_ess_chg`` / ``E_ess_dis`` DC power into / out of the battery terminals;
      ``Q_l_ess`` battery charge + discharge loss; ``SOC_ess`` [-].
    - ``E_inv_out`` AC power out of the inverter (``eta_inv`` × DC in);
      ``Q_l_inv`` inverter loss; ``E_grid_import`` AC power from the grid.
      ``E_inv_out + E_grid_import`` is the AC load, i.e. ``E_tot``.
    - ``E_dump`` DC power neither the load nor the battery could take.  The model
      has no grid export: surplus PV is discarded, never sold.
    """
    ess_r = route["ess_result"]
    S_l_inv = route["Q_l_inv"] / T_inv_K
    return {
        "I_sol_pv [W/m2]": pv_result["I_sol_pv"],
        "T_pv [°C]": cu.K2C(pv_result["T_pv_K"]),
        "eta_pv [-]": pv_result.get("eta_pv", np.nan),
        "E_pv_out [W]": pv_result["E_pv_out"],
        "E_ctrl_out [W]": pv_result["E_ctrl_out"],
        "E_pv_to_inv [W]": route["E_pv_to_inv"],
        "X_sol [W]": pv_result["X_sol"],
        "X_pv_out [W]": pv_result["X_pv_out"],
        "X_ctrl_out [W]": pv_result["X_ctrl_out"],
        "X_c_pv [W]": pv_result["X_c_pv"],
        "X_c_ctrl [W]": pv_result["X_c_ctrl"],
        "X_l_pv [W]": pv_result["X_l_pv"],
        "X_l_ctrl [W]": pv_result["X_l_ctrl"],
        "Q_l_ctrl [W]": route["Q_l_ctrl"],
        "E_ess_chg [W]": route["E_ess_chg"],
        "E_ess_dis [W]": route["E_ess_dis"],
        "SOC_ess [-]": ess_r["SOC_ess"],
        "Q_l_ess [W]": route["Q_l_ess"],
        "X_c_ess [W]": ess_r["X_c_ess"],
        "X_l_ess [W]": ess_r["X_l_ess"],
        "E_inv_out [W]": route["E_inv_out"],
        "E_grid_import [W]": route["E_grid_import"],
        "E_dump [W]": route["E_dump"],
        "Q_l_inv [W]": route["Q_l_inv"],
        "X_c_inv [W]": S_l_inv * T0_K,
        "X_l_inv [W]": route["Q_l_inv"] - S_l_inv * T0_K,
    }


def add_uv_power_to_e_tot(row: dict) -> float:
    """Add the UV lamp power of a result row to its ``E_tot [W]``; return ``E_tot``.

    The UV subsystem reports ``E_uv [W]`` through ``assemble_results`` but the
    cycle assembly that sets ``E_tot [W]`` runs before it, so the lamp is added
    here.  ``E_tot`` is the electricity the system draws:
    compressor + fans/pumps + solar-loop pump + UV lamp.
    """
    e_tot = row.get("E_tot [W]", 0.0)
    e_tot = 0.0 if e_tot is None or np.isnan(e_tot) else float(e_tot)
    e_uv = row.get("E_uv [W]", 0.0)
    if e_uv is not None and not np.isnan(e_uv):
        e_tot += float(e_uv)
    row["E_tot [W]"] = e_tot
    return e_tot


def pv_ess_postprocess(df: pd.DataFrame) -> pd.DataFrame:
    """Add the system-level exergy and grid-COP columns of the PV + ESS scenarios.

    ``cop_grid [-]`` is useful heat per grid electricity.  It is NaN while the
    grid import is at or below :data:`GRID_IMPORT_FLOOR_W`: with (almost) no import
    the ratio is arbitrarily large and carries no information (an infinite COP is
    not a physical quantity), so it is reported as undefined rather than as a
    number like 4e11.
    """
    # System exergy input: Solar exergy + Grid (= pure electricity = exergy)
    df["X_tot_sys [W]"] = df["X_sol [W]"].fillna(0) + df["E_grid_import [W]"].fillna(0)
    if "X_uv [W]" in df.columns:
        df["X_tot_sys [W]"] += df["X_uv [W]"].fillna(0)

    # Total exergy destruction across system (all Xc_ components)
    df["Xc_sys_tot [W]"] = df.filter(regex=r"^Xc_").sum(axis=1)

    grid = df["E_grid_import [W]"]
    df["cop_grid [-]"] = df["Q_tank_w_out [W]"] / grid.where(grid > GRID_IMPORT_FLOOR_W)

    # System exergy efficiency
    df["ex_eff_sys [-]"] = (df["Xst_tank [W]"] + df["X_tank_w_out [W]"]) / df["X_tot_sys [W]"].replace(0, np.nan)
    return df


# ------------------------------------------------------------------
# UV Disinfection Lamp
# ------------------------------------------------------------------


@dataclass
class UVLamp:
    """UV disinfection lamp subsystem.

    The lamp switches on periodically (``num_switching``
    times per ``period_sec``, each for ``exposure_sec``).
    All electrical input is converted to heat inside the
    tank (``Q_contribution = E_uv``).

    Parameters
    ----------
    lamp_watts : float
        Rated lamp power [W].
    exposure_sec : float
        Duration of each on-cycle [s].
    num_switching : int
        Number of on-cycles per period.
    period_sec : float
        Switching period [s] (default 3 h = 10 800 s).
    """

    lamp_watts: float = 0.0
    exposure_sec: float = 0.0
    num_switching: int = 1
    period_sec: float = 3 * cu.h2s

    def step(
        self,
        ctx: StepContext,
        ctrl: ControlState,
        dt: float,
        T_tank_w_in_K: float,
    ) -> dict:
        """Compute UV lamp state for one timestep."""
        from .uv_treatment import calc_uv_lamp_power

        E_uv: float = calc_uv_lamp_power(
            ctx.current_time_s,
            self.period_sec,
            self.num_switching,
            self.exposure_sec,
            self.lamp_watts,
        )
        return {
            "Q_contribution": E_uv,
            "E_subsystem": E_uv,
            "T_tank_w_in_override_K": None,
            "E_uv": E_uv,
        }

    def assemble_results(
        self,
        ctx: StepContext,
        ctrl: ControlState,
        step_state: dict,
        T_solved_K: float,
    ) -> dict:
        """Report UV power for DataFrame output."""
        E_uv: float = step_state.get("E_uv", 0.0)
        if E_uv > 0 or self.lamp_watts > 0:
            return {"E_uv [W]": E_uv}
        return {}

    def calc_exergy(
        self,
        df: pd.DataFrame,
        T0_K: pd.Series,
    ) -> SubsystemExergy | None:
        """UV exergy = electricity (handled by E→X conversion).

        No additional post-processing needed.
        Returns ``None``.
        """
        return None
