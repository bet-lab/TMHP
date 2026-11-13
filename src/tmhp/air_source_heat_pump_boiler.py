"""Integrated System Model: Air Source Heat Pump Boiler (ASHPB).

This system class orchestrates the dynamic interaction between distinct thermodynamic
sub-components to simulate the overall heating performance. While implemented as an
integrated model, its physical calculations represent the behavior of:

1. **Refrigerant Cycle (Vapor-Compression):**
   Evaluates thermodynamic states using CoolProp, enforcing superheat/subcool margins.
2. **Heat Pump Compressor:**
   Models the compression process using isentropic and volumetric efficiencies to compute
   the actual discharge enthalpy and mass flow rate. The compressor power is determined
   from the enthalpy difference and the mass flow rate.
3. **Expansion Valve:**
   Modeled as an isenthalpic expansion device (constant enthalpy) that throttles the
   refrigerant from the condensing pressure down to the evaporating pressure.
4. **Heat Exchangers (Condenser & Evaporator):**

   - **Condenser:** Placed inside the tank (hydronic), utilizing a static overall heat
     transfer coefficient (UA_tank_hx).
   - **Evaporator:** Air-coupled outdoor unit, utilizing a dynamic overall heat transfer
     coefficient (UA_ou) that scales non-linearly with fan airflow (Colburn j-factor analogy).
5. **Thermal Storage Tank:**
   Modeled with lumped-capacitance and DHW mixing logic.

At each time step, the model finds the minimum-power operating point
(compressor + fan) via bounded 1-D optimisation (Brent's method) over
the evaporator approach temperature difference.

.. note::
   See the project paper (KJACR 2025) for the underlying refrigerant-cycle
   theory and component-level modelling assumptions of this system.

Optional sub-components (injected via constructor):
- ``SolarThermalCollector`` — tank-circuit or mains-preheat placement
- (future) ``PVPanel`` — photovoltaic integration

Tank-level management and UV disinfection are built-in features
configured through constructor parameters.
"""

import contextlib
import math
from collections.abc import Callable, Mapping
from typing import Any, Literal

import numpy as np
import pandas as pd
from scipy.optimize import OptimizeResult, brentq, minimize_scalar
from tqdm import tqdm

from . import calc_util as cu
from ._opt_utils import PENALTY, margin_feasible_interval, safe_float_attr, specific_energy_objective
from ._tank_solve import recover_tank_temperature
from ._validation import validate_ctor, validate_dynamic, validate_initial_state, validate_steady, validate_step
from .compressor_efficiency import InvalidCompressorEfficiency, _eval_eff, reject_invalid_efficiency
from .compressor_envelope import (
    CYCLE_KEY,
    PR_CYCLE_MAX_SINGLE_STAGE,
    RATED_SPEED_SEARCH_MAX,
    X_SUCTION_MIN_DEFAULT,
    check_pr_envelope,
    discharge_controlled,
    envelope_excess,
    envelope_violation,
    reduce_duty_within_envelope,
    resolve_discharge_temp_max,
    resolve_x_suction_min,
    steady_envelope,
)
from .compressor_speed import default_displacement, solve_compressor_speed
from .constants import c_a, c_w, rho_a, rho_w
from .cycling import CC_WATER_DEFAULT, CyclingDegradation, report_boiler_cycling, resolve_cycling, steady_cycling
from .dhw import dhw_service_columns, warn_if_dhw_unmet
from .dynamic_context import (
    TANK_LEVEL_MIN,
    ControlState,
    DynamicState,
    StepContext,
    TankStepFlows,
    check_hp_schedule_active,
    collect_subsystem_state,
    delivered_tank_heat,
    determine_heat_source_on_off,
    determine_tank_refill_flow,
    resolve_step_operation,
    step_hp_result,
    tank_mass_energy_residual,
    tank_step_flows,
    unphysical_cycle_detail,
    warn_if_tank_boiling,
)
from .enex_functions import (
    calc_HX_perf_for_target_heat,
    calc_mixing_valve_flows,
    calc_mixing_valve_temp,
)
from .heat_exchanger import resolve_fan_flow_limits
from .heat_transfer import calc_simple_tank_UA
from .hx_fan import SINGLE_ZONE_VAV_COEFFICIENTS, calc_fan_power_from_dV_fan, is_generic_fan_curve
from .reference_state import HXSide, RatingCondition, ReferenceStateMixin
from .refrigerant import calc_ref_state, props_si, reportable_state
from .subsystems import PhotovoltaicSystem, SolarThermalCollector, add_uv_power_to_e_tot
from .thermodynamics import calc_energy_flow

#: Objective tiers above the specific-energy scale of
#: :func:`~tmhp._opt_utils.specific_energy_objective` (a feasible candidate
#: ranks below ``PENALTY``, one short of the request at the speed ceiling
#: between ``PENALTY`` and ``2 PENALTY``). Ordered so the search never prefers
#: a worse class of candidate, and so that among candidates none of which is an
#: operating point it returns the one closest to being one.
#: A solved, physical cycle outside the compressor envelope (discharge above
#: ``T_dis_max`` even under the discharge-temperature control, or the shipped
#: isentropic fit extrapolated below ``ETA_ISEN_MIN``); graded by how far
#: outside, so the search moves towards the smaller lift that may be inside.
#: It is not an operating point, but closer to one than an unphysical state.
_OBJ_OUTSIDE_ENVELOPE = 2.5 * PENALTY
_OBJ_UNPHYSICAL = 3.0 * PENALTY  # solved cycle, not an operating point
_OBJ_SPEED_UNSOLVED = 3.5 * PENALTY  # compressor speed root did not settle
_OBJ_COIL_UNMATCHED = 4.0 * PENALTY  # outdoor coil cannot carry the duty (+ deficit [W])
_OBJ_NO_CYCLE = 5.0 * PENALTY  # no refrigerant cycle at this approach

#: ``capacity_binding`` of a duty reduced to what the outdoor coil absorbs
#: (the family's name for the shared ``"source_heat_exchanger"``).
BINDING_OUTDOOR_COIL = "outdoor_coil"
_CAPACITY_BINDINGS = {"hx_not_converged": BINDING_OUTDOOR_COIL}

OUTDOOR_COIL_SHORT = "outdoor_coil_short_at_max_airflow"
OUTDOOR_COIL_EXCESS = "outdoor_coil_excess_at_min_airflow"
OUTDOOR_COIL_NO_BAND = "outdoor_coil_no_band"


class _NoCycle(Exception):
    """A coil margin was asked for at an approach with no refrigerant cycle."""


class _OutdoorSearch:
    """Cached evaluations of one ASHPB request over the outdoor approach.

    Each approach is evaluated once; the coarse samples, the margin roots and
    the minimiser share the cache. Calling the object returns the objective.
    """

    def __init__(
        self,
        model: "AirSourceHeatPumpBoiler",
        T_tank_w: float,
        Q_ref_tank: float,
        T0: float,
        flow_state: dict,
        m_dot_w: float | None,
    ) -> None:
        self.model = model
        self.T_tank_w = T_tank_w
        self.Q_ref_tank = Q_ref_tank
        self.T0 = T0
        self.flow_state = flow_state
        self.m_dot_w = m_dot_w
        # Surplus is credited only while the tank has thermal headroom to
        # store it (see `_opt_utils.specific_energy_objective`).
        self.storable = math.inf if T_tank_w < model.T_tank_w_upper_bound - 0.5 else 0.0
        #: approach -> (objective, max-flow margin [W], min-flow margin [W])
        self.rows: dict[float, tuple[float, float, float]] = {}
        #: approach -> (cycle state, pressure-ratio event, cycle failure reason) as evaluated
        self.states: dict[float, tuple[dict | None, tuple[str, float, float] | None, str | None]] = {}

    def entry(self, x: float) -> tuple[float, float, float]:
        x = float(x)
        hit = self.rows.get(x)
        if hit is not None:
            return hit
        diag: dict = {}
        model = self.model
        try:
            perf = model._calc_state(
                dT_ref_ou=x,
                T_tank_w=self.T_tank_w,
                Q_ref_tank=self.Q_ref_tank,
                T0=self.T0,
                flow_state=self.flow_state,
                m_dot_w=self.m_dot_w,
                hx_diag=diag,
            )
        except (ValueError, ArithmeticError):
            perf = None
        self.states[x] = (perf, model._last_pr_event, model._last_cycle_failure_reason)
        m_max = float(diag.get("max_flow_capacity_margin_W", math.nan)) if perf is not None else math.nan
        m_min = float(diag.get("min_flow_capacity_margin_W", math.nan)) if perf is not None else math.nan
        row = (self._rank(perf, m_max, m_min), m_max, m_min)
        self.rows[x] = row
        return row

    def _rank(self, perf: dict | None, m_max: float, m_min: float) -> float:
        if perf is None or not (math.isfinite(m_max) and math.isfinite(m_min)):
            return _OBJ_NO_CYCLE
        if not perf.get("converged", False):
            if "_hx_diag" in perf:
                deficit = max(-m_max, m_min, 0.0)
                return _OBJ_COIL_UNMATCHED + min(deficit, 0.5 * PENALTY)
            return _OBJ_SPEED_UNSOLVED
        if unphysical_cycle_detail(perf, self.model.hp_capacity) is not None:
            return _OBJ_UNPHYSICAL
        outside = envelope_excess(perf, self.model)
        if outside > 0.0:
            return _OBJ_OUTSIDE_ENVELOPE + min(outside, 0.45 * PENALTY)
        return specific_energy_objective(
            float(perf.get("E_tot [W]", math.nan)),
            float(perf.get("Q_ref_tank [W]", 0.0)),
            self.Q_ref_tank,
            self.storable,
        )

    def __call__(self, x: float) -> float:
        return self.entry(x)[0]

    def feasible(self, x: float) -> bool:
        """A solved operating point (possibly short of the request at ``rps_max``)."""
        return self.entry(x)[0] < _OBJ_OUTSIDE_ENVELOPE

    def has_cycle(self, x: float) -> bool:
        return math.isfinite(self.entry(x)[1])

    def margin_max_flow(self, x: float) -> float:
        value = self.entry(x)[1]
        if not math.isfinite(value):
            raise _NoCycle
        return value

    def margin_min_flow(self, x: float) -> float:
        value = self.entry(x)[2]
        if not math.isfinite(value):
            raise _NoCycle
        return value

    def best(self) -> tuple[float, float]:
        """``(objective, approach)`` of the best candidate evaluated so far."""
        value, x = min((row[0], x) for x, row in self.rows.items())
        return value, x

    def all_at_speed_floor(self) -> bool:
        """Every approach with a cycle has the compressor on its speed floor.

        The request is then below what the machine delivers at ``rps_min`` at
        every approach, and a smaller request is the same set of cycles: the
        speed stays on the floor and the tank-side approach follows the heat
        delivered there, not the request.
        """
        clamps = [state.get("capacity_clamped") for state, _, _ in self.states.values() if isinstance(state, dict)]
        return bool(clamps) and all(clamp == "min" for clamp in clamps)

    def coil_failure(self) -> str | None:
        """Why the outdoor coil carries the duty at no evaluated approach, if it does not."""
        margins = [(m_max, m_min) for _, m_max, m_min in self.rows.values() if math.isfinite(m_max)]
        if not margins:
            return None
        if any(m_max >= 0.0 and m_min <= 0.0 for m_max, m_min in margins):
            return None
        if all(m_max < 0.0 for m_max, _ in margins):
            return OUTDOOR_COIL_SHORT
        if all(m_min > 0.0 for _, m_min in margins):
            return OUTDOOR_COIL_EXCESS
        return OUTDOOR_COIL_NO_BAND


class AirSourceHeatPumpBoiler(ReferenceStateMixin):
    """Air source heat pump boiler with outdoor-air evaporator.

    The refrigerant cycle is resolved via CoolProp with
    user-specified superheat / subcool margins.  The condenser
    approach temperature is determined analytically from either the circulating-
    water flow (constant-surface-temperature ε-NTU) or the backward-compatible
    ``dT_ref_tank = Q_ref_tank / UA_tank_hx`` fallback, and a bounded
    1-D optimiser (Brent's method) minimises total electrical
    input (``E_cmp + E_ou_fan``) over the evaporator approach.

    Unless explicitly injected, compressor efficiencies use the
    shared baseline correlations in :mod:`tmhp.compressor_efficiency`
    (BITZER 2026-10-06), evaluated at PR and actual shaft speed in rev/s.
    Scalar and user-supplied callable overrides remain supported.

    **Below the minimum capacity.** ``analyze_steady`` reports a request below
    what the compressor delivers at ``rps_min`` as the mean of on/off cycling
    (``capacity_clamped == "min"``, :mod:`tmhp.cycling`): ``Q_ref_tank [W]``
    equals the request, ``runtime_fraction [-]`` is the share of the time the
    compressor runs, and the electrical input carries the EN 14825 degradation
    of a unit delivering to water -- ``Cd_cycling`` is its coefficient ``Cc``
    (0.9: while the compressor is off the unit draws a tenth of its running
    input; 1.0: nothing; a tested unit has ``Cc = 1 - P_off / P_on``).
    ``Cd_cycling=None`` returns the continuous minimum-speed point, which
    delivers more than the request. Dynamic runs are not affected: there the
    tank thermostat switches the compressor, which runs at ``hp_capacity`` for
    ``runtime_fraction [-]`` of a step.
    """

    _RATING_FAMILY = "ASHPB"
    # Heat, power and flow rates of the running cycle; a step reports them
    # multiplied by the fraction of the step the compressor ran.
    _RUNTIME_SCALED_KEYS: tuple[str, ...] = (
        "E_cmp [W]",
        "E_ou_fan [W]",
        "E_tot [W]",
        "Q_ou_a [W]",
        "Q_ref_ou [W]",
        "Q_ref_tank [W]",
        "dV_ou_a [m3/s]",
        "m_dot_ref [kg/s]",
    )
    #: The same rates as ``analyze_steady`` averages them when the unit cycles
    #: (:meth:`_report_part_load`): heat and flow rates with the capacity
    #: ratio, electrical inputs with the power fraction.
    _CYCLING_RATE_KEYS: tuple[str, ...] = (
        "Q_ou_a [W]",
        "Q_ref_ou [W]",
        "Q_ref_tank [W]",
        "dV_ou_a [m3/s]",
        "m_dot_ref [kg/s]",
    )
    _CYCLING_POWER_KEYS: tuple[str, ...] = ("E_cmp [W]", "E_ou_fan [W]")
    _CYCLING_SOURCE_HEAT_KEY: str | None = None
    _RATING_DEFAULT_MODE = "heating"

    def __init__(
        self,
        # 1. Refrigerant / cycle / compressor -----------
        ref: str = "R134a",
        V_cmp_ref: float | None = None,
        eta_cmp_isen: float | Callable | None = None,
        eta_cmp_vol: float | Callable | None = None,
        eta_cmp: float | Callable | None = None,
        dT_superheat: float = 5.0,
        dT_subcool: float = 5.0,
        # 2. Heat exchanger -----------------------------
        UA_tank_hx: float | None = None,
        UA_ou_rated: float | None = None,
        n_ou: float = 0.65,
        # 3. Outdoor unit fan ---------------------------
        dV_fan_a_rated: float | None = None,
        dP_fan_rated: float | None = None,
        A_cross: float | None = None,
        eta_fan_rated: float | None = None,
        # 4. Tank / control / load ----------------------
        T_tank_w_upper_bound: float = 65.0,
        T_tank_w_lower_bound: float = 60.0,
        T_mix_w_out: float = 40.0,
        T_sup_w: float = 15.0,
        hp_capacity: float = 15000.0,
        dV_mix_w_out_max: float = 0.0045,
        # Tank insulation
        r0: float = 0.2,
        H: float = 1.2,
        x_shell: float = 0.005,
        x_ins: float = 0.05,
        k_shell: float = 25,
        k_ins: float = 0.03,
        h_o: float = 15,
        # 5. Tank water level management ----------------
        tank_always_full: bool = True,
        tank_level_lower_bound: float = 0.5,
        tank_level_upper_bound: float = 1.0,
        dV_tank_w_in_refill: float = 0.001,
        prevent_simultaneous_flow: bool = False,
        # 7. HP operating schedule ----------------------
        hp_on_schedule: list[tuple[float, float]] | None = None,
        min_on_time_s: float = 0.0,
        min_off_time_s: float = 0.0,
        # 8. Subsystems (class-based injection) ---------
        stc: SolarThermalCollector | None = None,
        pv: PhotovoltaicSystem | None = None,
        uv=None,
        # ASHRAE 90.1-2022 VSD coefficients
        vsd_coeffs: dict | None = None,
        # Surrounding temperature parameter
        T_sur: float = 20.0,
        # Cycle guard: minimum condenser-to-evaporator saturation lift [K].
        dT_hx_min: float = 0.5,
        # Compressor pressure-ratio envelope (PR = P_cond / P_evap); the
        # ceiling is the single-stage scroll limit (see compressor_envelope).
        PR_cycle_min: float = 1.5,
        PR_cycle_max: float = PR_CYCLE_MAX_SINGLE_STAGE,
        # Compressor speed bounds [rev/s]; rps_max=None -> default_rps_max(rps_rated)
        rps_min: float = 15.0,
        rps_max: float | None = None,
        # Deprecated compat arguments:
        V_disp_cmp: float | None = None,
        eta_cmp_electro_mech: float | Callable | None = None,
        UA_tank: float | None = None,  # deprecated alias for UA_tank_hx
        UA_cond_design: float | None = None,
        UA_evap_design: float | None = None,
        dV_ou_fan_a_design: float | None = None,
        dP_ou_fan_design: float | None = None,
        A_cross_ou: float | None = None,
        eta_ou_fan_design: float | None = None,
        vsd_coeffs_ou: dict | None = None,
        *,
        rps_rated: float | None = None,
        m_dot_ref_rated: float | None = None,
        rated_condition: Mapping[str, Any] | RatingCondition | None = None,
        dV_fan_a_ref: float | None = None,
        dV_fan_a_min: float | None = None,
        dV_fan_a_max: float | None = None,
        T_dis_max: float | Literal["auto"] | None = "auto",
        x_suction_min: float | None = X_SUCTION_MIN_DEFAULT,
        # On/off cycling below the minimum continuous capacity in analyze_steady:
        # EN 14825 degradation coefficient Cc of a unit delivering to water
        # (None: no cycling, the row over-delivers at the minimum speed).
        Cd_cycling: CyclingDegradation | float | None = CC_WATER_DEFAULT,
    ):
        validate_ctor("ASHPB", locals())
        #: EN 14825 cycling degradation of ``analyze_steady`` (None: continuous at minimum speed).
        self.cycling: CyclingDegradation | None = resolve_cycling(Cd_cycling, "water")
        if dV_fan_a_ref is not None:
            dV_fan_a_rated = dV_fan_a_ref
        # Resolve deprecated mapping
        if V_cmp_ref is None:
            V_cmp_ref = V_disp_cmp if V_disp_cmp is not None else default_displacement(hp_capacity)
        if eta_cmp is None:
            eta_cmp = eta_cmp_electro_mech
        if UA_tank_hx is None:
            UA_tank_hx = UA_tank if UA_tank is not None else UA_cond_design
        if UA_ou_rated is None:
            UA_ou_rated = UA_evap_design
        if dV_fan_a_rated is None:
            dV_fan_a_rated = dV_ou_fan_a_design
        if dP_fan_rated is None:
            dP_fan_rated = dP_ou_fan_design if dP_ou_fan_design is not None else 60.0
        if A_cross is None:
            A_cross = A_cross_ou
        if eta_fan_rated is None:
            eta_fan_rated = eta_ou_fan_design if eta_ou_fan_design is not None else 0.6
        if vsd_coeffs is None:
            vsd_coeffs = vsd_coeffs_ou

        if hp_on_schedule is None:
            hp_on_schedule = [(0.0, 24.0)]
        if not vsd_coeffs:
            vsd_coeffs = SINGLE_ZONE_VAV_COEFFICIENTS.copy()

        # --- 1. Refrigerant / cycle / compressor ---
        self.ref: str = ref
        self.V_cmp_ref: float = V_cmp_ref

        self.dT_superheat: float = dT_superheat
        self.dT_subcool: float = dT_subcool
        self.dT_hx_min: float = dT_hx_min
        # Compressor pressure-ratio envelope (floor -> clamp, ceiling -> reject)
        self.PR_cycle_min: float = PR_cycle_min
        self.PR_cycle_max: float = PR_cycle_max
        # Compressor speed search bounds [rev/s]
        self.rps_min: float = rps_min
        # rps_max=None: searched up to RATED_SPEED_SEARCH_MAX for the rated
        # speed, then replaced by default_rps_max(rps_rated) (reference_state).
        self._rps_max_derived: bool = rps_max is None
        self.rps_max: float = RATED_SPEED_SEARCH_MAX if rps_max is None else rps_max
        # Discharge-temperature limit [degC] ("auto": refrigerant default; None: off)
        self.T_dis_max: float | None = resolve_discharge_temp_max(T_dis_max, ref)
        # Discharge-temperature control: lowest suction quality (None: no control)
        self.x_suction_min: float | None = resolve_x_suction_min(x_suction_min)
        #: Suction quality of the cycle being evaluated (< 1 only inside the control).
        self._x_suction: float = 1.0
        # Records the PR-envelope event of the most recent _calc_state call
        # (None | ("pr_below_min", pr, bound) | ("pr_above_max", pr, bound)).
        self._last_pr_event: tuple[str, float, float] | None = None
        # Records an active-cycle infeasibility that must survive the HP-off
        # fallback long enough for analyze_steady() to expose it to adapters.
        self._last_cycle_failure_reason: str | None = None
        # Whether the last outdoor-approach search found every cycle on the speed floor.
        self._solve_bound_at_speed_floor: bool = False
        self.hp_capacity: float = hp_capacity

        # --- 2. Heat exchanger UA ---
        # If not explicitly provided, scale the condenser UA with rated heating capacity
        # to give a 5 K condensing approach at the rated point. This is the common rule
        # used by the validation manuscript and the Panasonic rule set in equipment_presets.py.
        if UA_tank_hx is None:
            self.UA_tank_hx = hp_capacity / 5.0
        else:
            self.UA_tank_hx = UA_tank_hx

        # The condenser rejects the evaporator load plus compressor work, so the
        # evaporator load is approximately (1 - 1 / COP) times the condenser load.
        # Across the COP range considered by the validation manuscript, this is about 0.7.
        if UA_ou_rated is None:
            self.UA_ou_rated = self.UA_tank_hx * 0.7
        else:
            self.UA_ou_rated = UA_ou_rated

        self.n_ou: float = n_ou

        # --- 3. Outdoor unit fan ---
        # Default fan flow rate is scaled at 0.0002 m^3/s per W (or 720 CMH per kW),
        # representing an optimal ratio of airflow volume to thermal capacity.
        # This provides enough margin so that nominal optimization operates at ~80% fan ratio.
        if dV_fan_a_rated is None:
            self.dV_fan_a_rated = hp_capacity * 0.00015
        else:
            self.dV_fan_a_rated = dV_fan_a_rated

        self.dV_fan_a_ref = self.dV_fan_a_rated
        self.dV_fan_a_min, self.dV_fan_a_max = resolve_fan_flow_limits(
            self.dV_fan_a_ref,
            dV_fan_a_min,
            dV_fan_a_max,
            custom_curve=not is_generic_fan_curve(vsd_coeffs),
        )
        self.dP_fan_rated: float = dP_fan_rated
        self.eta_fan_rated: float = eta_fan_rated

        # Default coil face area assumes a nominal frontal air velocity of 2.0 m/s.
        # This velocity is selected specifically to maintain pressure drop profiles
        # within optimal ranges for typical plain fin-and-tube configurations.
        # Ref: Heat transfer and friction characteristics of plain fin-and-tube heat exchangers, part II (Wang et al., 2000, DOI: 10.1016/S0017-9310(99)00333-6)
        if A_cross is None:
            self.A_cross = self.dV_fan_a_rated / 2.0  # Reference face velocity 2.0 m/s; not a maximum
        else:
            self.A_cross = A_cross

        self.E_fan_rated: float = self.dV_fan_a_rated * self.dP_fan_rated / self.eta_fan_rated
        self.vsd_coeffs: dict = vsd_coeffs
        self.fan_params: dict = {
            "fan_min_flow_rate": self.dV_fan_a_min,
            "fan_max_flow_rate": self.dV_fan_a_max,
            "fan_ref_flow_rate": self.dV_fan_a_rated,
            "fan_rated_flow_rate": self.dV_fan_a_rated,
            "fan_ref_power": self.E_fan_rated,
            "fan_rated_power": self.E_fan_rated,
        }

        # --- Compressor reference state (rated speed, rated mass flow) ---
        efficiencies = self._initialize_reference_state(
            rps_rated=rps_rated,
            m_dot_ref_rated=m_dot_ref_rated,
            efficiencies={"eta_cmp_isen": eta_cmp_isen, "eta_cmp_vol": eta_cmp_vol, "eta_cmp": eta_cmp},
            rated_condition=rated_condition,
        )
        self.eta_cmp_isen: float | Callable = efficiencies["eta_cmp_isen"]
        self.eta_cmp_vol: float | Callable = efficiencies["eta_cmp_vol"]
        self.eta_cmp: float | Callable = efficiencies["eta_cmp"]

        # --- 4. Tank geometry and thermal props ---
        self.tank_physical: dict = {
            "r0": r0,
            "H": H,
            "x_shell": x_shell,
            "x_ins": x_ins,
            "k_shell": k_shell,
            "k_ins": k_ins,
            "h_o": h_o,
        }
        self.UA_tank_wall: float = calc_simple_tank_UA(
            **self.tank_physical,
        )
        self.T_sur_K: float = cu.C2K(T_sur)
        self.V_tank_full: float = math.pi * r0**2 * H
        self.C_tank: float = c_w * rho_w * self.V_tank_full

        self.dV_mix_w_out_max: float = dV_mix_w_out_max
        self.T_tank_w_upper_bound: float = T_tank_w_upper_bound
        self.T_tank_w_lower_bound: float = T_tank_w_lower_bound
        self.T_sup_w: float = T_sup_w
        self.T_sup_w_K: float = cu.C2K(T_sup_w)
        self.T_tank_w_in: float = T_sup_w
        self.T_mix_w_out: float = T_mix_w_out
        self.T_tank_w_in_K: float = cu.C2K(T_sup_w)
        self.T_mix_w_out_K: float = cu.C2K(T_mix_w_out)

        # --- 5. Tank water level management ---
        self.tank_always_full: bool = tank_always_full
        self.tank_level_lower_bound: float = tank_level_lower_bound
        self.tank_level_upper_bound: float = tank_level_upper_bound
        self.dV_tank_w_in_refill: float = dV_tank_w_in_refill
        self.prevent_simultaneous_flow: bool = prevent_simultaneous_flow

        # --- 6. HP operating schedule ---
        self.hp_on_schedule: list[tuple[float, float]] = hp_on_schedule
        # Validated by validate_ctor (finite, >= 0).
        self.min_on_time_s: float = float(min_on_time_s)
        self.min_off_time_s: float = float(min_off_time_s)

        # --- 7. Subsystems ---
        self.stc: SolarThermalCollector | None = stc
        self.pv: PhotovoltaicSystem | None = pv
        self._subsystems: dict[str, Any] = {}
        if stc is not None:
            self._subsystems["stc"] = stc
        if pv is not None:
            self._subsystems["pv"] = pv
        if uv is not None:
            self._subsystems["uv"] = uv

        # Flow-rate sync variables
        self.dV_tank_w_in: float = 0.0
        self.dV_tank_w_out: float = 0.0
        self.dV_mix_sup_w_in: float = 0.0
        self.dV_mix_w_out: float = 0.0

    def _rating_side(self, role: str, mode: str, T_in_C: float) -> HXSide:
        """Outdoor-air evaporator (source) and tank-immersed condenser (load)."""
        if role == "source":
            return HXSide("air", T_in_C, self.UA_ou_rated, self.dV_fan_a_ref)
        return HXSide("tank", T_in_C, self.UA_tank_hx)

    # =============================================================
    # Refrigerant cycle physics (ASHP-specific)
    # =============================================================

    @discharge_controlled(reject=False)
    @reject_invalid_efficiency
    def _calc_state(
        self,
        dT_ref_ou: float,
        T_tank_w: float,
        Q_ref_tank: float,
        T0: float,
        *,
        flow_state: dict,
        m_dot_w: float | None = None,
        hx_diag: dict | None = None,
    ) -> dict | None:
        """Evaluate refrigerant cycle at a given operating point.

        Parameters
        ----------
        dT_ref_ou : float
            Evaporator approach ΔT [K].
        T_tank_w : float
            Tank water temperature [°C].
        Q_ref_tank : float
            Target condenser heat rate [W].
        T0 : float
            Dead-state / outdoor-air temperature [°C].
        flow_state : dict
            Explicit mixing-valve / tank-flow context. Must contain:
            ``dV_mix_w_out``, ``dV_tank_w_out``, ``dV_tank_w_in``,
            ``dV_mix_sup_w_in``. Replaces former implicit ``self.dV_*`` reads.
        m_dot_w : float | None
            Circulating-water mass flow rate [kg/s]. ``None`` preserves the
            standalone fixed-UA approach calculation. The water specific heat
            is the shared constant ``c_w`` (``tmhp.constants``).
        hx_diag : dict | None
            When given, receives the outdoor-coil solution of an active
            cycle, including its signed capacity margins at the fan flow
            limits (``min_flow_capacity_margin_W``,
            ``max_flow_capacity_margin_W``) that the outdoor-approach search
            solves its band edges from. Left untouched when no cycle exists.

        Returns
        -------
        dict | None
            Cycle performance dictionary; ``None`` if infeasible.
        """
        if m_dot_w is not None and m_dot_w > 0:
            # Flow-dependent ε-NTU: circulating water passing a constant
            # condensing-temperature surface (same physics as the outdoor-side HX,
            # cf. enex_functions.calc_HX_perf_for_target_heat)
            C_w_tank = m_dot_w * c_w
            eps_tank_hx = 1.0 - math.exp(-self.UA_tank_hx / C_w_tank)
            K_tank_hx = C_w_tank * eps_tank_hx
        else:
            # Backward-compatible fallback (standalone runs without loop flow).
            # There is no water-side capacity rate, so no effectiveness to
            # define either -- the conductance carries the whole description.
            C_w_tank = float("nan")
            eps_tank_hx = float("nan")
            K_tank_hx = self.UA_tank_hx

        # Heat transferred per kelvin of tank-side approach. The approach that
        # goes into the cycle is the one implied by the *requested* duty; when
        # the compressor turns out to be outside its modulating band, the
        # closure below re-solves it against the heat actually delivered.
        dT_ref_tank = Q_ref_tank / K_tank_hx if Q_ref_tank > 0 else 0.0

        T_tank_w_K: float = cu.C2K(T_tank_w)
        T0_K: float = cu.C2K(T0)

        T_ou_sat_K: float = T0_K - dT_ref_ou
        T_tank_sat_K: float = T_tank_w_K + dT_ref_tank

        is_active: bool = Q_ref_tank > 0.0

        if not is_active:
            # Flow state (explicit parameter, no side-effect reads)
            dV_tank_w_out: float = flow_state["dV_tank_w_out [m3/s]"]
            dV_tank_w_in: float = flow_state["dV_tank_w_in [m3/s]"]
            dV_mix_sup_w_in: float = flow_state["dV_mix_sup_w_in [m3/s]"]
            dV_mix_w_out_val: float = flow_state["dV_mix_w_out [m3/s]"]

            if dV_mix_w_out_val == 0:
                T_mix_w_out_val: float = np.nan
                T_mix_w_out_val_K: float = np.nan
            else:
                mix: dict = calc_mixing_valve_temp(
                    T_tank_w_K,
                    self.T_sup_w_K,
                    self.T_mix_w_out_K,
                )
                T_mix_w_out_val = mix["T_mix_w_out"]
                T_mix_w_out_val_K = mix["T_mix_w_out_K"]

            # Energy balance: Q_tank_w_in + Q_ref_tank = Q_tank_w_out + Q_tank_loss + dU_tank/dt
            Q_tank_w_in: float = calc_energy_flow(G=c_w * rho_w * dV_tank_w_in, T=self.T_tank_w_in_K, T0=T0_K)
            Q_tank_w_out: float = calc_energy_flow(G=c_w * rho_w * dV_tank_w_out, T=T_tank_w_K, T0=T0_K)
            Q_mix_sup_w_in: float = calc_energy_flow(G=c_w * rho_w * dV_mix_sup_w_in, T=self.T_sup_w_K, T0=T0_K)
            Q_mix_w_out: float = calc_energy_flow(G=c_w * rho_w * dV_mix_w_out_val, T=T_mix_w_out_val_K, T0=T0_K)

            cs = calc_ref_state(
                T_evap_K=T_ou_sat_K,
                T_cond_K=T_tank_sat_K,
                refrigerant=self.ref,
                eta_cmp_isen=1.0,
                mode="heating",
                dT_superheat=self.dT_superheat,
                dT_subcool=0.0,
                is_active=False,
                rps=0.0,
            )

            result: dict = reportable_state(cs)
            result.update(
                {
                    "hp_is_on": False,
                    "converged": True,
                    "converged_rps": True,
                    "capacity_clamped": None,
                    "pr_clamped": False,
                    "fan_flow_min_limit": False,
                    "fan_flow_max_limit": False,
                    # Temperatures [°C]
                    "T_ou_a_in [°C]": T0,
                    "T_ou_a_mid [°C]": T0,
                    "T_ou_a_out [°C]": T0,
                    "T_tank_w [°C]": T_tank_w,
                    "T_sup_w [°C]": self.T_sup_w,
                    "T_tank_w_in [°C]": self.T_tank_w_in,
                    "T_mix_w_out [°C]": T_mix_w_out_val,
                    "T0 [°C]": T0,
                    # Volume flow rates [m3/s]
                    "dV_ou_a [m3/s]": 0.0,
                    "v_ou_a [m/s]": 0.0,
                    "dV_mix_w_out [m3/s]": (dV_mix_w_out_val if dV_mix_w_out_val > 0 else np.nan),
                    "dV_tank_w_out [m3/s]": (dV_tank_w_out if dV_tank_w_out > 0 else np.nan),
                    "dV_tank_w_in [m3/s]": (dV_tank_w_in if dV_tank_w_in > 0 else np.nan),
                    "dV_mix_sup_w_in [m3/s]": (dV_mix_sup_w_in if dV_mix_sup_w_in > 0 else np.nan),
                    "m_dot_ref [kg/s]": 0.0,
                    "cmp_rpm [rpm]": 0.0,
                    # Energy rates [W]
                    "E_ou_fan [W]": 0.0,
                    "Q_ref_ou [W]": 0.0,
                    "Q_ou_a [W]": 0.0,
                    "E_cmp [W]": 0.0,
                    "Q_ref_tank [W]": 0.0,
                    "Q_tank_w_in [W]": Q_tank_w_in,
                    "Q_tank_w_out [W]": Q_tank_w_out,
                    "Q_mix_sup_w_in [W]": Q_mix_sup_w_in,
                    "Q_mix_w_out [W]": Q_mix_w_out,
                    "E_tot [W]": 0.0,
                    # COP metrics
                    "cop_ref [-]": np.nan,
                    "cop_sys [-]": np.nan,
                }
            )
            return result

        # --- Active state calculations ---
        self._last_cycle_failure_reason = None

        def _cycle_unchecked(dT_tank: float, rps_fixed: float | None = None) -> dict | None:
            """Refrigerant cycle for one tank-side approach temperature.

            ``rps_fixed`` holds the compressor at a given speed instead of
            solving for the requested duty; the speed-floor closure below runs
            the machine at its bound and lets the approach move instead.
            Returns ``None`` when the cycle is infeasible.
            """
            # Low-lift feasibility is enforced downstream by the compressor
            # pressure-ratio floor (PR_cycle_min); a separate fixed minimum lift is
            # redundant and non-transferable across refrigerants/operating levels.
            actual_dT_subcool = min(self.dT_subcool, max(0.0, dT_tank - self.dT_hx_min))
            actual_dT_superheat = min(self.dT_superheat, max(0.0, dT_ref_ou - self.dT_hx_min))
            T_cond_K = T_tank_w_K + dT_tank

            cs_i = calc_ref_state(
                T_evap_K=T_ou_sat_K,
                T_cond_K=T_cond_K,
                refrigerant=self.ref,
                eta_cmp_isen=1.0,  # Temporary dummy value to get basic states
                mode="heating",
                dT_superheat=actual_dT_superheat,
                x_suction=self._x_suction,
                dT_subcool=actual_dT_subcool,
                is_active=True,
                rps=None,
            )

            ratio = cs_i["P_ref_cmp_out [Pa]"] / cs_i["P_ref_cmp_in [Pa]"] if cs_i["P_ref_cmp_in [Pa]"] > 0 else 1.0

            # Compressor pressure-ratio envelope guard (see compressor_envelope.py).
            # Ceiling -> reject; floor -> clamp the cycle onto PR_cycle_min by holding
            # the evaporator (outdoor) pressure and projecting the condensing
            # (tank-side) pressure, then refresh the state. Recorded for the
            # analyze_steady hint; no print here (runs inside the optimiser loop).
            self._last_pr_event = None
            pr_event = check_pr_envelope(ratio, self.PR_cycle_min, self.PR_cycle_max)
            pr_clamped_i = pr_event == "pr_below_min"
            if pr_event == "pr_above_max":
                self._last_pr_event = ("pr_above_max", ratio, self.PR_cycle_max)
                return None
            if pr_event == "pr_below_min":
                self._last_pr_event = ("pr_below_min", ratio, self.PR_cycle_min)
                P_evap_clamp = cs_i["P_ref_cmp_in [Pa]"]
                P_cond_clamp = self.PR_cycle_min * P_evap_clamp
                T_cond_K = props_si("T", "P", P_cond_clamp, "Q", 0, self.ref)
                cs_i = calc_ref_state(
                    T_evap_K=T_ou_sat_K,
                    T_cond_K=T_cond_K,
                    refrigerant=self.ref,
                    eta_cmp_isen=1.0,  # Temporary dummy value to get basic states
                    mode="heating",
                    dT_superheat=actual_dT_superheat,
                    x_suction=self._x_suction,
                    dT_subcool=actual_dT_subcool,
                    is_active=True,
                    rps=None,
                )
                ratio = (
                    cs_i["P_ref_cmp_out [Pa]"] / cs_i["P_ref_cmp_in [Pa]"]
                    if cs_i["P_ref_cmp_in [Pa]"] > 0
                    else self.PR_cycle_min
                )

            if T_cond_K <= T_tank_w_K:
                # Condensing temperature at/below tank/loop inlet temperature:
                # heat transfer to the circulating water is thermodynamically infeasible.
                self._last_cycle_failure_reason = "t_cond_below_t_in"
                return None

            P_cond = cs_i["P_ref_cmp_out [Pa]"]
            s_cmp_in = cs_i["s_ref_cmp_in [J/(kg·K)]"]
            h_cmp_in = cs_i["h_ref_cmp_in [J/kg]"]
            h_exp_in = cs_i["h_ref_exp_in [J/kg]"]

            # Compute isentropic enthalpy once before loop
            try:
                h_ref_cmp_out_isen = props_si("H", "P", P_cond, "S", s_cmp_in, self.ref)
            except ValueError:
                h_ref_cmp_out_isen = h_cmp_in

            def _residual_rps(rps):
                eta_vol_r = _eval_eff(self.eta_cmp_vol, ratio, rps)
                eta_isen_r = _eval_eff(self.eta_cmp_isen, ratio, rps)

                h_cmp_out = h_cmp_in + (h_ref_cmp_out_isen - h_cmp_in) / eta_isen_r
                dh_cond_local = h_cmp_out - h_exp_in

                m_dot = self.V_cmp_ref * cs_i["rho_ref_cmp_in [kg/m3]"] * eta_vol_r * rps
                return (m_dot * dh_cond_local) - Q_ref_tank

            if rps_fixed is None:
                rps_i, converged_i, clamped_i = solve_compressor_speed(_residual_rps, self.rps_min, self.rps_max)
            else:
                rps_i, converged_i, clamped_i = rps_fixed, True, None

            eta_vol_i = _eval_eff(self.eta_cmp_vol, ratio, rps_i)
            eta_isen_i = _eval_eff(self.eta_cmp_isen, ratio, rps_i)
            eta_em_i = _eval_eff(self.eta_cmp, ratio, rps_i)

            # Post-evaluate state with final isentropic efficiency
            cs_i = calc_ref_state(
                T_evap_K=T_ou_sat_K,
                T_cond_K=T_cond_K,
                refrigerant=self.ref,
                eta_cmp_isen=eta_isen_i,
                mode="heating",
                dT_superheat=actual_dT_superheat,
                x_suction=self._x_suction,
                dT_subcool=actual_dT_subcool,
                is_active=True,
                rps=rps_i,
            )

            m_dot_i = self.V_cmp_ref * cs_i["rho_ref_cmp_in [kg/m3]"] * eta_vol_i * rps_i
            return {
                "cs": cs_i,
                "rps": rps_i,
                "converged_rps": converged_i,
                "capacity_clamped": clamped_i,
                "pr_clamped": pr_clamped_i,
                "pr": ratio,
                "eta_vol": eta_vol_i,
                "eta_isen": eta_isen_i,
                "eta_em": eta_em_i,
                "m_dot_ref": m_dot_i,
                # The approach the cycle actually runs at: equal to the input
                # unless the pressure-ratio floor moved the condensing state.
                "dT_tank": T_cond_K - T_tank_w_K,
                "Q_tank": m_dot_i * (cs_i["h_ref_cmp_out [J/kg]"] - cs_i["h_ref_exp_in [J/kg]"]),
                "Q_ou": m_dot_i * (cs_i["h_ref_cmp_in [J/kg]"] - cs_i["h_ref_exp_out [J/kg]"]),
                "E_cmp": m_dot_i * (cs_i["h_ref_cmp_out [J/kg]"] - cs_i["h_ref_cmp_in [J/kg]"]) / eta_em_i,
            }

        def _cycle(dT_tank: float, rps_fixed: float | None = None) -> dict | None:
            """:func:`_cycle_unchecked`, with no saturation state counted as infeasible.

            A condensing temperature at or above the critical point (tank
            temperature plus approach, or an approach widened by the closure
            below) has no saturation state, and CoolProp raises. There is no
            condenser there, so the cycle is infeasible -- the same answer the
            GSHPB/WSHPB cycles give -- rather than an error escaping
            ``analyze_steady`` / ``step()``.
            """
            try:
                return _cycle_unchecked(dT_tank, rps_fixed)
            except ValueError as exc:
                if isinstance(exc, InvalidCompressorEfficiency):
                    raise
                return None

        def _close_tank_hx(state0: dict) -> dict | None:
            """Re-solve the approach against the heat the machine delivers.

            Outside the modulating band the compressor no longer meets the
            request, so an approach derived from the request describes a
            condenser transferring a different duty than the cycle produces.
            The speed is held at the bound it clamped onto and the approach
            becomes the unknown of

                Q_delivered(dT) = K_tank_hx * dT

            which is the same closure the outdoor coil already runs on. No
            correction factor enters; the root is the operating point at which
            the two descriptions of the condenser agree.
            """
            rps_bound = state0["rps"]

            def _residual(dT: float) -> float:
                st = _cycle(dT, rps_fixed=rps_bound)
                if st is None:
                    return float("nan")
                return float(st["Q_tank"] - K_tank_hx * st["dT_tank"])

            lo = hi = dT_ref_tank
            r0 = state0["Q_tank"] - K_tank_hx * state0["dT_tank"]
            if r0 > 0.0:
                # Over-delivery (speed floor): the approach has to widen.
                for _ in range(12):
                    hi = hi * 1.5 + 0.5
                    r_hi = _residual(hi)
                    if not math.isfinite(r_hi):
                        return None
                    if r_hi <= 0.0:
                        break
                else:
                    return None
            elif r0 < 0.0:
                # Shortfall (speed ceiling): the approach has to narrow.
                for _ in range(12):
                    lo = lo * 0.6
                    if lo < 1.0e-3:
                        return None
                    r_lo = _residual(lo)
                    if not math.isfinite(r_lo):
                        return None
                    if r_lo >= 0.0:
                        break
                else:
                    return None
            else:
                return state0

            try:
                dT_closed = brentq(_residual, lo, hi, xtol=1.0e-6, rtol=1.0e-12, maxiter=100)
            except (ValueError, RuntimeError):
                return None
            closed = _cycle(dT_closed, rps_fixed=rps_bound)
            if closed is None:
                return None
            closed["capacity_clamped"] = state0["capacity_clamped"]
            closed["converged_rps"] = state0["converged_rps"]
            return closed

        state = _cycle(dT_ref_tank)
        if state is None:
            return None

        if state["capacity_clamped"] is not None:
            saved_failure = self._last_cycle_failure_reason
            closed_state = _close_tank_hx(state)
            if closed_state is None:
                # The closure could not be bracketed. Keep the request-based
                # state; the non-zero `tank_hx_residual [W]` reported below is
                # what tells a caller the condenser balance is not closed.
                self._last_cycle_failure_reason = saved_failure
            else:
                state = closed_state

        cs = state["cs"]
        cmp_rps = state["rps"]
        converged_rps = state["converged_rps"]
        capacity_clamped = state["capacity_clamped"]
        pr_clamped = state["pr_clamped"]
        m_dot_ref = state["m_dot_ref"]
        dT_ref_tank = state["dT_tank"]
        Q_ref_tank_calc = state["Q_tank"]
        Q_ref_ou = state["Q_ou"]
        E_cmp = state["E_cmp"]
        # Residual of the tank-side closure: zero by construction wherever the
        # compressor meets the request, and driven to zero at a speed bound.
        tank_hx_residual = Q_ref_tank_calc - K_tank_hx * dT_ref_tank

        HX_perf_ou: dict = calc_HX_perf_for_target_heat(
            Q_ref_target=Q_ref_ou,
            T_ou_a_in_C=T0,
            T_ref_evap_sat_K=cs["T_ref_evap_sat_K"],
            T_ref_cond_sat_l_K=cs["T_ref_cond_sat_l_K"],
            A_cross=self.A_cross,
            UA_rated=self.UA_ou_rated,
            dV_fan_ref=self.dV_fan_a_ref,
            dV_fan_min=self.dV_fan_a_min,
            dV_fan_max=self.dV_fan_a_max,
            custom_fan_curve=not is_generic_fan_curve(self.vsd_coeffs),
            is_active=True,
            exponent=self.n_ou,
        )
        if hx_diag is not None:
            hx_diag.update(HX_perf_ou)

        if HX_perf_ou.get("converged", True) is False:
            return {
                "converged": False,
                "_hx_diag": HX_perf_ou,
                # The cycle exists; only the coil does not carry it. The
                # discharge-temperature control acts on it, so the coil margins
                # the search reads are those of the controlled cycle.
                CYCLE_KEY: reportable_state(cs),
                "converged_rps": bool(converged_rps),
                "capacity_clamped": capacity_clamped,
                "pr_clamped": pr_clamped,
                "fan_flow_min_limit": HX_perf_ou.get("min_limit", False),
                "fan_flow_max_limit": HX_perf_ou.get("max_limit", False),
            }

        dV_ou_a: float = HX_perf_ou["dV_fan"]
        v_ou_a: float = dV_ou_a / self.A_cross
        # The flags say where the fan runs, not which branch the coil solve took.
        # At an optimum on a fan limit the coil balance is a root at that limit,
        # and the airflow lands a few 1e-9 either side of it depending on the
        # platform; a relative 1e-6 band makes both report the limit.
        at_min_airflow = bool(HX_perf_ou.get("min_limit", False)) or dV_ou_a <= self.dV_fan_a_min * (1.0 + 1e-6)
        at_max_airflow = bool(HX_perf_ou.get("max_limit", False)) or dV_ou_a >= self.dV_fan_a_max * (1.0 - 1e-6)
        T_ou_a_mid: float = HX_perf_ou["T_ou_a_mid"]
        Q_ou_a: float = HX_perf_ou["Q_ou_air"]

        E_ou_fan: float = calc_fan_power_from_dV_fan(
            dV_fan=dV_ou_a,
            fan_params=self.fan_params,
            vsd_coeffs=self.vsd_coeffs,
            is_active=True,
        )

        T_ou_a_out: float = T_ou_a_mid + E_ou_fan / (c_a * rho_a * dV_ou_a)

        # --- Flow state (explicit parameter, no side-effect reads) ---
        # No type annotations here on purpose: the inactive branch above
        # (around L316) already binds these names; redeclaring with
        # `name: float = ...` triggers mypy [no-redef] even though the
        # earlier branch returns unconditionally.
        dV_tank_w_out = flow_state["dV_tank_w_out [m3/s]"]
        dV_tank_w_in = flow_state["dV_tank_w_in [m3/s]"]
        dV_mix_sup_w_in = flow_state["dV_mix_sup_w_in [m3/s]"]
        dV_mix_w_out_val = flow_state["dV_mix_w_out [m3/s]"]

        if dV_mix_w_out_val == 0:
            T_mix_w_out_val = np.nan
            T_mix_w_out_val_K = np.nan
        else:
            mix = calc_mixing_valve_temp(
                T_tank_w_K,
                self.T_sup_w_K,
                self.T_mix_w_out_K,
            )
            T_mix_w_out_val = mix["T_mix_w_out"]
            T_mix_w_out_val_K = mix["T_mix_w_out_K"]

        # Energy balance: Q_tank_w_in + Q_ref_tank = Q_tank_w_out + Q_tank_loss + dU_tank/dt
        Q_tank_w_in = calc_energy_flow(G=c_w * rho_w * dV_tank_w_in, T=self.T_tank_w_in_K, T0=T0_K)
        Q_tank_w_out = calc_energy_flow(G=c_w * rho_w * dV_tank_w_out, T=T_tank_w_K, T0=T0_K)
        Q_mix_sup_w_in = calc_energy_flow(G=c_w * rho_w * dV_mix_sup_w_in, T=self.T_sup_w_K, T0=T0_K)
        Q_mix_w_out = calc_energy_flow(G=c_w * rho_w * dV_mix_w_out_val, T=T_mix_w_out_val_K, T0=T0_K)

        result = reportable_state(cs)

        result.update(
            {
                "hp_is_on": True,
                "converged": bool(converged_rps),
                "converged_rps": bool(converged_rps),
                "capacity_clamped": capacity_clamped,
                "pr_clamped": pr_clamped,
                "Q_ref_tank_request [W]": Q_ref_tank,
                # Heat-exchanger closure diagnostics. `dT_ref_tank` is the
                # approach the cycle ran at, `tank_hx_residual` how far the
                # condenser is from carrying the delivered duty.
                "dT_ref_tank [K]": dT_ref_tank,
                "UA_tank_hx [W/K]": self.UA_tank_hx,
                "C_w_tank [W/K]": C_w_tank,
                "epsilon_tank_hx [-]": eps_tank_hx,
                "tank_hx_residual [W]": tank_hx_residual,
                "UA_ou [W/K]": HX_perf_ou.get("UA", np.nan),
                "NTU_ou [-]": (HX_perf_ou.get("UA", np.nan) / (c_a * rho_a * dV_ou_a) if dV_ou_a > 0 else np.nan),
                "epsilon_ou [-]": HX_perf_ou.get("epsilon", np.nan),
                "PLR_request [-]": (Q_ref_tank / self.hp_capacity if self.hp_capacity > 0 else np.nan),
                "PLR_delivered [-]": (Q_ref_tank_calc / self.hp_capacity if self.hp_capacity > 0 else np.nan),
                "fan_flow_min_limit": at_min_airflow,
                "fan_flow_max_limit": at_max_airflow,
                # Temperatures [°C]
                "T_ou_a_in [°C]": T0,
                "T_ou_a_mid [°C]": T_ou_a_mid,
                "T_ou_a_out [°C]": T_ou_a_out,
                "T_tank_w [°C]": T_tank_w,
                "T_sup_w [°C]": self.T_sup_w,
                "T_tank_w_in [°C]": self.T_tank_w_in,
                "T_mix_w_out [°C]": T_mix_w_out_val,
                "T0 [°C]": T0,
                # Volume flow rates [m3/s]
                "dV_ou_a [m3/s]": dV_ou_a,
                "v_ou_a [m/s]": v_ou_a,
                "dV_mix_w_out [m3/s]": (dV_mix_w_out_val if dV_mix_w_out_val > 0 else np.nan),
                "dV_tank_w_out [m3/s]": (dV_tank_w_out if dV_tank_w_out > 0 else np.nan),
                "dV_tank_w_in [m3/s]": (dV_tank_w_in if dV_tank_w_in > 0 else np.nan),
                "dV_mix_sup_w_in [m3/s]": (dV_mix_sup_w_in if dV_mix_sup_w_in > 0 else np.nan),
                "m_dot_ref [kg/s]": m_dot_ref,  # Mass flow rate [kg/s]
                "cmp_rpm [rpm]": cmp_rps * 60,
                "n_star [-]": cmp_rps / self.rps_rated,
                "pr_cmp [-]": state["pr"],
                "eta_cmp_vol [-]": state["eta_vol"],
                "eta_cmp_isen [-]": state["eta_isen"],
                "eta_cmp [-]": state["eta_em"],
                # Energy rates [W]
                "E_ou_fan [W]": E_ou_fan,
                "Q_ref_ou [W]": Q_ref_ou,
                "Q_ou_a [W]": Q_ou_a,
                "E_cmp [W]": E_cmp,
                "Q_ref_tank [W]": Q_ref_tank_calc,
                "Q_tank_w_in [W]": Q_tank_w_in,
                "Q_tank_w_out [W]": Q_tank_w_out,
                "Q_mix_sup_w_in [W]": Q_mix_sup_w_in,
                "Q_mix_w_out [W]": Q_mix_w_out,
                "E_tot [W]": E_cmp + E_ou_fan,
                # COP metrics (analogous to X_eff_ref / X_eff_sys)
                "cop_ref [-]": (Q_ref_tank_calc / E_cmp if E_cmp > 0 else np.nan),
                "cop_sys [-]": (Q_ref_tank_calc / (E_cmp + E_ou_fan) if (E_cmp + E_ou_fan) > 0 else np.nan),
            }
        )

        return result

    # -------------------------------------------------------------
    # Outdoor-approach search
    # -------------------------------------------------------------

    #: Outdoor (evaporator) approach search range [K].
    _OUTDOOR_APPROACH_BOUNDS: tuple[float, float] = (1.0, 20.0)
    #: Coarse outdoor-approach samples. They only locate the band the fan can
    #: serve; its edges are solved from the coil capacity margins at the fan
    #: flow limits, so a band narrower than the spacing is not missed.
    _OUTDOOR_GRID_POINTS = 5
    #: Tolerance of the first solve of the band edges [K]. Candidates start
    #: this far inside them; an edge is solved to ``_BAND_EDGE_XTOL_K`` (and
    #: the candidate stepped ``_BAND_EDGE_STEP_K`` off it) only when the best
    #: candidate sits on it.
    _BAND_COARSE_XTOL_K = 5e-2
    _BAND_EDGE_XTOL_K = 1e-8
    _BAND_EDGE_STEP_K = 2e-8
    #: Resolution of the edge between approaches with and without a cycle [K].
    _CYCLE_EDGE_TOL_K = 1e-3
    #: In-band candidates are no farther apart than the band width over this.
    #: The objective is not unimodal in the band -- the guarded efficiency fit
    #: has kinks (PR 7.15, 33.8 and 72.5 rev/s) and the speed floor another --
    #: so the minimiser starts from the best of these, not from the band.
    _BAND_SCAN_INTERVALS = 4
    #: Tolerance of the minimisation around the best candidate [K]. Below it
    #: the objective is flat to the inner solvers' noise.
    _BAND_XATOL_K = 1e-5
    #: Scan valleys polished: those within this fraction of the best, at most this many.
    _VALLEY_SPREAD = 1e-2
    _MAX_VALLEYS = 3
    #: Distance inside a band edge at which an edge minimum is checked [K].
    _EDGE_PROBE_K = 1e-3

    def _optimize_operation(
        self,
        T_tank_w: float,
        Q_ref_tank: float,
        T0: float,
        *,
        flow_state: dict,
        m_dot_w: float | None = None,
    ) -> OptimizeResult:
        """Find the least specific-energy operating point over the outdoor approach.

        The outdoor fan modulates between its flow limits, so only a band of
        outdoor approaches can carry the evaporator duty: below it the coil
        cannot absorb the duty even at the largest airflow, above it the coil
        absorbs more than the duty even at the smallest airflow. The band's
        edges are the roots of the coil capacity margins at those two flows
        (:func:`~tmhp._opt_utils.margin_feasible_interval`); coarse samples
        only locate it (:meth:`_search_outdoor`). Candidates in the band are
        ranked on :func:`~tmhp._opt_utils.specific_energy_objective`, and a
        solved cycle that is not an operating point
        (:func:`~tmhp.dynamic_context.unphysical_cycle_detail`) is never
        preferred to one that is.

        Parameters
        ----------
        T_tank_w : float
            Tank water temperature [°C].
        Q_ref_tank : float
            Target condenser heat rate [W].
        T0 : float
            Dead-state temperature [°C].
        flow_state : dict
            Explicit flow context passed through to ``_calc_state()``.
        m_dot_w : float | None
            Circulating-water mass flow rate [kg/s].

        Returns
        -------
        scipy.optimize.OptimizeResult
            ``x`` is the outdoor approach [K] of the best candidate and
            ``fun`` its objective. ``success`` is ``True`` once the search
            ran; whether ``x`` is an operating point is read from the cycle
            state there, as before. ``failure_detail`` is set when no
            approach lets the outdoor coil carry the duty:
            ``"outdoor_coil_short_at_max_airflow"`` (it absorbs too little
            even at the largest airflow at every approach the cycle reaches),
            ``"outdoor_coil_excess_at_min_airflow"`` (it absorbs too much even
            at the smallest airflow), or ``"outdoor_coil_no_band"`` (the two
            limits leave no approach between them); otherwise ``None``.
        """
        search = _OutdoorSearch(self, T_tank_w, Q_ref_tank, T0, flow_state, m_dot_w)
        self._search_outdoor(search)
        value, x = search.best()
        # Read by the duty reduction (`reduce_duty_within_envelope`): below
        # this request every approach is the same machine state.
        self._solve_bound_at_speed_floor = search.all_at_speed_floor()
        return OptimizeResult(
            x=x,
            fun=value,
            success=True,
            nfev=len(search.rows),
            failure_detail=search.coil_failure() if value >= _OBJ_OUTSIDE_ENVELOPE else None,
            # The cycle state the search evaluated at ``x`` (:meth:`_state_at_optimum`).
            solved=search.states[x],
        )

    def _state_at_optimum(
        self,
        opt: object,
        T_tank_w: float,
        Q_ref_tank: float,
        T0: float,
        *,
        flow_state: dict,
        m_dot_w: float | None = None,
    ) -> dict | None:
        """Cycle state at the best approach of :meth:`_optimize_operation`.

        The search has already evaluated that approach, so its state is taken
        from the search (with the pressure-ratio event and the cycle failure
        reason of that evaluation restored) instead of being solved a second
        time. A result without it (a substituted search) is evaluated here.
        """
        solved = getattr(opt, "solved", None)
        if solved is None:
            self._solve_bound_at_speed_floor = False
            return self._calc_state(
                dT_ref_ou=safe_float_attr(opt, "x", 5.0),
                T_tank_w=T_tank_w,
                Q_ref_tank=Q_ref_tank,
                T0=T0,
                flow_state=flow_state,
                m_dot_w=m_dot_w,
            )
        state, self._last_pr_event, self._last_cycle_failure_reason = solved
        return state if isinstance(state, dict) else None

    def _cycle_cell(self, search: "_OutdoorSearch", a: float, b: float) -> tuple[float, float] | None:
        """Shrink ``[a, b]`` to its part where a cycle exists (one end must have one)."""
        good_a, good_b = search.has_cycle(a), search.has_cycle(b)
        if good_a and good_b:
            return a, b
        if not (good_a or good_b):
            return None
        good, bad = (a, b) if good_a else (b, a)
        while abs(bad - good) > self._CYCLE_EDGE_TOL_K:
            mid = 0.5 * (good + bad)
            if search.has_cycle(mid):
                good = mid
            else:
                bad = mid
        return (a, good) if good_a else (good, b)

    def _coarse_bands(self, search: "_OutdoorSearch") -> list[tuple[float, float, float, float]]:
        """Outdoor-approach bands the fan can serve, edges solved coarsely.

        The sampled approaches are split into runs that have a refrigerant
        cycle (the pressure-ratio ceiling or a condensing temperature below
        the tank removes it at the ends of the range); each run is extended
        to where its cycle ends. A larger approach lowers the evaporating
        temperature, so the coil absorbs more heat at any airflow while the
        cycle hands it less (more of the condenser duty comes from compressor
        work): the signed margin at the maximum fan flow rises from negative
        (the coil is short) and the margin at the minimum flow rises through
        zero (the fan cannot turn down far enough). The band is the interval
        between the two roots. Returns ``(low, high, run_low, run_high)``
        per band, with ``low``/``high`` within ``_BAND_COARSE_XTOL_K`` of the
        roots unless they are the run ends.
        """
        samples = sorted(search.rows)
        runs: list[tuple[float, float]] = []
        i = 0
        while i < len(samples):
            if not search.has_cycle(samples[i]):
                i += 1
                continue
            j = i
            while j + 1 < len(samples) and search.has_cycle(samples[j + 1]):
                j += 1
            first, last = samples[i], samples[j]
            if i > 0:
                cell = self._cycle_cell(search, samples[i - 1], first)
                first = cell[0] if cell is not None else first
            if j + 1 < len(samples):
                cell = self._cycle_cell(search, last, samples[j + 1])
                last = cell[1] if cell is not None else last
            runs.append((first, last))
            i = j + 1
        bands = []
        for first, last in runs:
            band = self._band_edges(search, first, last, self._BAND_COARSE_XTOL_K)
            if band is not None:
                bands.append((band[0], band[1], first, last))
        return bands

    def _band_edges(
        self, search: "_OutdoorSearch", first: float, last: float, xtol: float
    ) -> tuple[float, float] | None:
        """:func:`margin_feasible_interval` on ``[first, last]``, brackets narrowed by the cache."""
        known = [x for x in search.rows if first < x < last and search.has_cycle(x)]
        with contextlib.suppress(_NoCycle, ValueError, RuntimeError, StopIteration):
            return margin_feasible_interval(
                search.margin_max_flow,
                search.margin_min_flow,
                first,
                last,
                xtol=xtol,
                samples=known,
            )
        return None

    def _minimize_in_band(self, search: "_OutdoorSearch", band: tuple[float, float, float, float]) -> None:
        """Scan a band, then polish its best valleys.

        Candidates are spread over the band no farther apart than
        ``1 / _BAND_SCAN_INTERVALS`` of its width (cached samples count). The
        scan's local minima within ``_VALLEY_SPREAD`` of the best are polished
        (:meth:`_polish_valley`), at most ``_MAX_VALLEYS`` of them: the
        objective has more than one valley, and the sampled values of two
        valleys can rank them the wrong way round.
        """
        lo_c, hi_c, first, last = band
        coarse = 1.01 * self._BAND_COARSE_XTOL_K
        # A coarse root is within `coarse` of the true edge; a run end is exact.
        lo = lo_c if lo_c <= first else lo_c + coarse
        hi = hi_c if hi_c >= last else hi_c - coarse
        if hi - lo <= 2.0 * coarse:
            precise = self._band_edges(search, first, last, self._BAND_EDGE_XTOL_K)
            if precise is not None:
                self._sample_precise_band(search, precise)
            return
        search(lo)
        search(hi)
        width = (hi - lo) / self._BAND_SCAN_INTERVALS
        points = sorted(x for x in search.rows if lo <= x <= hi)
        for a, b in zip(points, points[1:], strict=False):
            n = math.ceil((b - a) / width - 1e-9)
            for k in range(1, n):
                search(a + k * (b - a) / n)
        points = sorted(x for x in search.rows if lo <= x <= hi)
        values = [search(x) for x in points]
        valleys = [
            k
            for k in range(len(points))
            if (k == 0 or values[k] <= values[k - 1]) and (k + 1 == len(points) or values[k] <= values[k + 1])
        ]
        best = min(values[k] for k in valleys)
        if best >= _OBJ_OUTSIDE_ENVELOPE:
            return
        valleys = sorted(
            (k for k in valleys if values[k] <= best + self._VALLEY_SPREAD * abs(best)), key=values.__getitem__
        )
        for k in valleys[: self._MAX_VALLEYS]:
            self._polish_valley(search, points, k, first, last)

    def _polish_valley(self, search: "_OutdoorSearch", points: list[float], k: int, first: float, last: float) -> None:
        """Refine the valley of the band scan at ``points[k]``.

        A valley on a band edge first gets the edge solved precisely, plus a
        probe ``_EDGE_PROBE_K`` inside it. Within the valley's window (its
        scan neighbours, or the solved edge) the best candidate is then
        either on the window's end -- the edge is the minimum -- or bracketed
        by its neighbours, and a Brent minimisation starts from that triple.
        """
        x = points[k]
        lo_w = points[k - 1] if k > 0 else x
        hi_w = points[k + 1] if k + 1 < len(points) else x
        if k == 0:
            if x > first:
                solved = self._band_edges(search, first, x, self._BAND_EDGE_XTOL_K)
                if solved is not None and solved[0] + self._BAND_EDGE_STEP_K < x:
                    lo_w = solved[0] + self._BAND_EDGE_STEP_K
                    search(lo_w)
            search(min(lo_w + self._EDGE_PROBE_K, hi_w))
        if k + 1 == len(points):
            if x < last:
                solved = self._band_edges(search, x, last, self._BAND_EDGE_XTOL_K)
                if solved is not None and solved[1] - self._BAND_EDGE_STEP_K > x:
                    hi_w = solved[1] - self._BAND_EDGE_STEP_K
                    search(hi_w)
            search(max(hi_w - self._EDGE_PROBE_K, lo_w))
        window = sorted(p for p in search.rows if lo_w <= p <= hi_w)
        j = min(range(len(window)), key=lambda i: (search(window[i]), window[i]))
        if j == 0 or j + 1 == len(window):
            return
        a, b, c = window[j - 1], window[j], window[j + 1]
        try:
            minimize_scalar(
                search,
                bracket=(a, b, c),
                method="brent",
                options={"xtol": self._BAND_XATOL_K / max(abs(b), 1.0), "maxiter": 30},
            )
        except ValueError:
            # Ties with a neighbour: no strict bracketing triple.
            minimize_scalar(
                search,
                bounds=(a, c),
                method="bounded",
                options={"xatol": min(self._BAND_XATOL_K, (c - a) / 20.0), "maxiter": 30},
            )

    def _sample_precise_band(self, search: "_OutdoorSearch", band: tuple[float, float]) -> None:
        """Search a band whose edges are solved precisely: its inward-stepped edges, then the interior."""
        lo, hi = band
        step = self._BAND_EDGE_STEP_K
        if hi - lo <= 2.0 * step:
            # A single point: equal fan limits, or a band at solver resolution.
            for x in (0.5 * (lo + hi), lo, hi):
                if search.feasible(x):
                    return
            return
        lo, hi = lo + step, hi - step
        search(lo)
        search(hi)
        minimize_scalar(
            search,
            bounds=(lo, hi),
            method="bounded",
            options={"xatol": min(self._BAND_XATOL_K, (hi - lo) / 20.0), "maxiter": 30},
        )

    def _search_outdoor(self, search: "_OutdoorSearch") -> None:
        """Locate the outdoor-approach bands the fan can serve and minimise over them.

        Coarse samples locate the band; its edges come from the coil capacity
        margins (:meth:`_coarse_bands`), so a band that no sample falls into
        is still found. Each band is then scanned and minimised
        (:meth:`_minimize_in_band`).
        """
        lo_x, hi_x = self._OUTDOOR_APPROACH_BOUNDS
        for x in np.linspace(lo_x, hi_x, self._OUTDOOR_GRID_POINTS):
            search(float(x))
        for band in self._coarse_bands(search):
            self._minimize_in_band(search, band)

    # =============================================================
    # Steady-state analysis
    # =============================================================

    def _report_part_load(self, result: dict, Q_ref_tank: float) -> None:
        """Write the cycling keys of an ``analyze_steady`` row; average it if the unit cycles.

        A row on the compressor's speed floor that delivers more than the
        request becomes the mean over the on/off cycling period
        (:func:`tmhp.cycling.report_boiler_cycling`): the rates a dynamic step
        averages with its run time are multiplied by ``capacity_ratio [-]``,
        the electrical inputs by ``capacity_ratio / part_load_factor``.
        """
        report_boiler_cycling(
            result,
            self.cycling,
            Q_ref_tank,
            rate_keys=self._CYCLING_RATE_KEYS,
            power_keys=self._CYCLING_POWER_KEYS,
            source_heat_key=self._CYCLING_SOURCE_HEAT_KEY,
            hp_capacity=self.hp_capacity,
        )

    @steady_cycling(duty_arg="Q_ref_tank")
    @steady_envelope(duty_arg="Q_ref_tank", bindings=_CAPACITY_BINDINGS)
    def analyze_steady(
        self,
        T_tank_w: float,
        T0: float,
        Q_ref_tank: float,
        *,
        m_dot_w: float | None = None,
        return_dict: bool = True,
    ) -> dict | pd.DataFrame:
        """Run a steady-state performance snapshot.

        Evaluates the refrigerant cycle at a given operating point
        (T_tank_w, T0, Q_ref_tank) **without** solving the tank energy
        balance or tracking dynamic flows.

        Parameters
        ----------
        T_tank_w : float
            Tank water temperature [°C] — treated as a given input.
        T0 : float
            Dead-state / outdoor-air temperature [°C].
        Q_ref_tank : float
            Target condenser heat rate [W].
        m_dot_w : float | None
            Circulating-water mass flow rate [kg/s]. When positive, the
            condenser approach uses a constant-surface-temperature ε-NTU model
            with the shared water specific heat ``c_w`` (``tmhp.constants``).
        return_dict : bool
            If True return dict; else single-row DataFrame.

        Returns
        -------
        dict | pd.DataFrame
            Cycle state plus diagnostic flags.

            Three keys are useful for branching:

            - ``"converged"`` (bool) — True only when the HX optimisation and
              the SciPy optimiser both succeeded.
            - ``"failure_reason"`` (str) — one of ``"none"``,
              ``"cycle_invalid"``, ``"t_cond_below_t_in"``,
              ``"pr_above_max"``, ``"discharge_temp_above_max"`` (even
              under the discharge-temperature control and at a reduced duty
              the discharge exceeds ``T_dis_max``), ``"eta_isen_below_min"``
              (the shipped isentropic fit, extrapolated, is below 0.30),
              ``"hx_not_converged"``, or ``"optimizer_failed"``.
            - ``"failure_detail"`` (str, failures only) — on
              ``"cycle_invalid"``, which check of
              :func:`tmhp.dynamic_context.unphysical_cycle_detail` rejected a
              solved cycle; on ``"hx_not_converged"``, which fan limit the
              outdoor coil fails at every approach the cycle reaches
              (``"outdoor_coil_short_at_max_airflow"``,
              ``"outdoor_coil_excess_at_min_airflow"`` or
              ``"outdoor_coil_no_band"``).
            - ``"capacity_clamped"`` (str | None) — ``"min"`` when the
              requested duty was below what the compressor delivers at
              ``rps_min`` and the cycle was solved at that floor instead,
              ``"max"`` for the corresponding ceiling case, ``None`` when the
              request was met exactly. A clamped point is **converged**: the
              request simply lay outside the machine's deliverable band, which
              a real inverter unit answers by running at its limit and cycling.
              A ``"min"`` point is reported as that cycling mean (below); at a
              ``"max"`` point compare ``"Q_ref_tank [W]"`` against
              ``"Q_ref_tank_request [W]"`` for the shortfall. A ``"max"`` point carries
              ``"capacity_binding"``: ``"compressor"`` (``rps_max``), or the
              operating limit the request ran into when a smaller duty runs --
              ``"discharge_temperature"`` (``T_dis_max``),
              ``"pressure_ratio"`` (``PR_cycle_max``), ``"outdoor_coil"`` (the
              coil is short at the fan's largest airflow) or
              ``"compressor_efficiency"`` (the shipped fit's floor). The
              largest duty that runs is then returned, with ``"Q_max [W]"``
              and ``"Q_request [W]"``; see
              :func:`tmhp.compressor_envelope.reduce_duty_within_envelope`.
            - ``"runtime_fraction [-]"``, ``"capacity_ratio [-]"``,
              ``"part_load_factor [-]"``, ``"Q_min [W]"`` and
              ``"E_cycling_loss [W]"`` — on every row. A request below the
              duty at ``rps_min`` (``capacity_clamped == "min"``) is reported
              as the mean of on/off cycling: ``Q_ref_tank [W]`` equals the
              request (``Q_min [W]`` is the continuous duty), heat and
              refrigerant rates are the running machine's times
              ``capacity_ratio [-]`` -- the compressor runs that share of the
              time, ``runtime_fraction [-]`` -- and the electrical inputs
              carry the EN 14825 degradation for a unit delivering to water
              (``Cd_cycling``: the running values times
              ``capacity_ratio / part_load_factor``), so both COPs are the
              continuous ones times the part-load factor. Temperatures,
              pressures and speed are those of the running machine. With
              ``Cd_cycling=None`` the row is the continuous minimum-speed
              point and ``Q_ref_tank [W]`` exceeds the request.
            - ``"efficiency_extrapolated"`` (bool) — a default efficiency fit
              is evaluated outside its data at this point.
            - ``"discharge_control"`` (bool), ``"dT_ref_superheat [K]"``,
              ``"x_ref_cmp_in [-]"`` — whether the discharge-temperature
              control held ``T_dis_max`` and the suction state it used
              (superheat reduced first, then wet suction down to
              ``x_suction_min``); see :mod:`tmhp.compressor_envelope`.

            Every failure -- an infeasible cycle, a solved cycle that is not
            an operating point, an outdoor coil that cannot carry the duty,
            a search that did not settle -- returns the same off-mode state:
            ``hp_is_on = False``, ``converged = False``, ``E_cmp = 0``,
            ``Q_ref_tank = 0``, NaN COP, with ``failure_reason`` (and
            ``failure_detail`` where there is one). A request that fails on a
            limit a smaller duty cures is not a failure when such a duty
            runs (``capacity_clamped = "max"`` above); ``"cycle_invalid"``
            with no cycle at all (a condensing temperature at or above the
            critical point) is not reduced.
        """
        validate_steady(locals())
        import warnings

        # Empty flow state as steady state ignores dynamic withdrawal/refill
        flow_state = {
            "dV_mix_w_out [m3/s]": 0.0,
            "dV_tank_w_out [m3/s]": 0.0,
            "dV_tank_w_in [m3/s]": 0.0,
            "dV_mix_sup_w_in [m3/s]": 0.0,
            "alp": 0.0,
        }

        if Q_ref_tank <= 0:
            result = self._calc_state(
                dT_ref_ou=5.0,
                T_tank_w=T_tank_w,
                Q_ref_tank=0.0,
                T0=T0,
                flow_state=flow_state,
                m_dot_w=m_dot_w,
            )
        else:
            opt_result = self._optimize_operation(
                T_tank_w=T_tank_w,
                Q_ref_tank=Q_ref_tank,
                T0=T0,
                flow_state=flow_state,
                m_dot_w=m_dot_w,
            )
            result = None
            with contextlib.suppress(Exception):
                result = self._state_at_optimum(
                    opt_result, T_tank_w, Q_ref_tank, T0, flow_state=flow_state, m_dot_w=m_dot_w
                )

            # A solved cycle that is not an operating point (undefined
            # discharge state, speed-floor duty above the nameplate) is
            # rejected like an infeasible one; see unphysical_cycle_detail.
            invalid_detail: str | None = None
            envelope_reason: str | None = None
            if isinstance(result, dict) and result.get("hp_is_on") and result.get("converged", False):
                invalid_detail = unphysical_cycle_detail(result, self.hp_capacity)
                if invalid_detail is None:
                    envelope_reason = envelope_violation(result, self)
                if invalid_detail is not None or envelope_reason is not None:
                    result = None

            # Pressure-ratio envelope hint for the final operating point
            # (one message per call; per-probe events are silent). Floor ->
            # clamp (cycle still solved); ceiling -> reject (HP-off fallback).
            pr_event = self._last_pr_event
            cycle_failure_reason = self._last_cycle_failure_reason
            if pr_event is not None:
                kind, pr_val, bound = pr_event
                if kind == "pr_below_min":
                    print(
                        f"[PR guard] clamp 하한(below PR_cycle_min): "
                        f"PR={pr_val:.3f} -> {bound:.2f} "
                        f"(T_tank_w={T_tank_w:.1f}°C, T0={T0:.1f}°C, Q_ref_tank={Q_ref_tank:.0f}W)"
                    )
                else:  # pr_above_max
                    print(
                        f"[PR guard] reject 상한(above PR_cycle_max): "
                        f"PR={pr_val:.3f} > {bound:.2f} "
                        f"(T_tank_w={T_tank_w:.1f}°C, T0={T0:.1f}°C, Q_ref_tank={Q_ref_tank:.0f}W)"
                    )

            # Diagnose what (if anything) went wrong. failure_reason is a
            # *report*; the fallback trigger condition is preserved as
            # `result is None or not isinstance(result, dict)` to match
            # the historical behaviour of this branch.
            opt_success = bool(getattr(opt_result, "success", False))
            if result is None or not isinstance(result, dict):
                if invalid_detail is not None:
                    failure_reason = "cycle_invalid"
                elif envelope_reason is not None:
                    failure_reason = envelope_reason
                elif cycle_failure_reason is not None:
                    failure_reason = cycle_failure_reason
                elif pr_event is not None and pr_event[0] == "pr_above_max":
                    failure_reason = "pr_above_max"
                else:
                    failure_reason = "cycle_invalid"
            elif not result.get("converged", False):
                # `_hx_diag` marks the outdoor-coil branch; otherwise the speed
                # root find inside the cycle failed numerically.
                failure_reason = "hx_not_converged" if "_hx_diag" in result else "optimizer_failed"
            elif not opt_success:
                failure_reason = "optimizer_failed"
            else:
                failure_reason = "none"

            if failure_reason != "none":
                # No operating point: every failure reports the same off-mode
                # row (no heat, no power, NaN COP) with the reason, never a
                # half-filled active one.
                if failure_reason == "hx_not_converged":
                    # Which fan limit the outdoor coil fails at every approach.
                    invalid_detail = getattr(opt_result, "failure_detail", None)
                warnings.warn(
                    f"analyze_steady: fell back to HP-off state "
                    f"(reason={failure_reason!r}, "
                    + (f"detail={invalid_detail!r}, " if invalid_detail else "")
                    + f"T_tank_w={T_tank_w:.1f}°C, T0={T0:.1f}°C, "
                    f"Q_ref_tank={Q_ref_tank:.0f}W, "
                    f"opt_success={opt_success}, "
                    f"opt_x={safe_float_attr(opt_result, 'x', float('nan')):.2f}, "
                    f"opt_fun={safe_float_attr(opt_result, 'fun', float('nan')):.3g}). "
                    "Consider calibrating UA_rated or increasing the explicit fan-flow maximum.",
                    RuntimeWarning,
                    stacklevel=2,
                )
                try:
                    result = self._calc_state(
                        dT_ref_ou=5.0,
                        T_tank_w=T_tank_w,
                        Q_ref_tank=0.0,
                        T0=T0,
                        flow_state=flow_state,
                        m_dot_w=m_dot_w,
                    )
                except Exception:
                    # `dict[str, object]` so the later string-valued
                    # `failure_reason` assignment doesn't violate inferred
                    # `dict[str, bool | float]`.
                    result = {
                        "hp_is_on": False,
                        "converged": False,
                        "failure_reason": failure_reason,
                        "Q_ref_tank [W]": 0.0,
                        "Q_ref_ou [W]": 0.0,
                        "E_cmp [W]": 0.0,
                        "E_ou_fan [W]": 0.0,
                        "E_tot [W]": 0.0,
                        "T_tank_w [°C]": T_tank_w,
                        "T0 [°C]": T0,
                    }
                if isinstance(result, dict):
                    result["converged"] = False
                    result["failure_reason"] = failure_reason
                    if invalid_detail is not None:
                        result["failure_detail"] = invalid_detail
            else:
                assert isinstance(result, dict)
                result["failure_reason"] = failure_reason

        if result is None or not isinstance(result, dict):
            raise RuntimeError("Simulation failed to produce a valid result dictionary.")

        # Steady state doesn't have tank loss because we don't solve tank mass/energy balance
        result["Q_tank_loss [W]"] = 0.0
        result.setdefault("failure_reason", "none")  # zero load: a valid off result, not a failure
        result["tank_level [-]"] = 1.0  # steady-state: always_full

        if return_dict:
            return result
        return pd.DataFrame([result])

    # =============================================================
    # Private helpers for analyze_dynamic
    # =============================================================

    @staticmethod
    def _calc_tank_flow_context(
        dV_mix_w_out: float,
        T_tank_w_K: float,
        T_sup_w_K: float,
        T_mix_w_out_K: float,
        dV_tank_w_in_override: float | None = None,
    ) -> dict:
        """Compute mixing-valve / tank-flow context (no side-effects).

        Parameters
        ----------
        dV_mix_w_out : float
            Service-water draw-off volumetric flow rate [m³/s].
        T_tank_w_K : float
            Current tank water temperature [K].
        T_sup_w_K : float
            Mains supply temperature [K].
        T_mix_w_out_K : float
            Mixing-valve target outlet temperature [K].
        dV_tank_w_in_override : float | None
            If not None, overrides the symmetry assumption
            ``dV_tank_w_in = dV_tank_w_out`` (e.g. refill control).

        Returns
        -------
        dict
            Keys: ``dV_mix_w_out``, ``dV_tank_w_out``, ``dV_tank_w_in``,
            ``dV_mix_sup_w_in``.
        """
        mix_state = calc_mixing_valve_temp(T_tank_w_K, T_sup_w_K, T_mix_w_out_K)
        flows = calc_mixing_valve_flows(dV_mix_w_out, mix_state["alp"])
        dV_tank_w_out: float = flows["dV_hot_in"]
        dV_tank_w_in: float = dV_tank_w_out if dV_tank_w_in_override is None else dV_tank_w_in_override
        return {
            "dV_mix_w_out [m3/s]": dV_mix_w_out,
            "dV_tank_w_out [m3/s]": dV_tank_w_out,
            "dV_tank_w_in [m3/s]": dV_tank_w_in,
            "dV_mix_sup_w_in [m3/s]": flows["dV_cold_in"],
            "alp": mix_state["alp"],
        }

    def _thermostat_call(self, ctx: StepContext, hp_is_on_prev: bool) -> bool:
        """Thermostat call for heat at the start of the step.

        Hysteresis on the tank temperature with the operating schedule
        applied. The compressor itself runs only while the call stands and a
        cycle exists; :func:`tmhp.dynamic_context.resolve_step_operation`
        follows the call through the step.
        """
        return determine_heat_source_on_off(
            T_tank_w_C=cu.K2C(ctx.T_tank_w_K),
            T_lower=self.T_tank_w_lower_bound,
            T_upper=self.T_tank_w_upper_bound,
            is_on_prev=hp_is_on_prev,
            hour_of_day=ctx.hour_of_day,
            on_schedule=self.hp_on_schedule,
        )

    def _step_flow_state(self, ctx: StepContext, T_tank_w: float) -> dict:
        """Mixing-valve flows of the step evaluated at tank temperature ``T_tank_w`` [°C]."""
        # The step-start temperature keeps its exact Kelvin value.
        T_tank_w_K = ctx.T_tank_w_K if T_tank_w == cu.K2C(ctx.T_tank_w_K) else cu.C2K(T_tank_w)
        return self._calc_tank_flow_context(
            dV_mix_w_out=ctx.dV_mix_w_out,
            T_tank_w_K=T_tank_w_K,
            T_sup_w_K=self.T_sup_w_K,
            T_mix_w_out_K=self.T_mix_w_out_K,
        )

    def _off_cycle_result(self, ctx: StepContext, T_tank_w: float) -> dict:
        """Off-mode row (compressor stopped, nothing asked of it) at ``T_tank_w`` [°C]."""
        off_result = self._calc_state(5.0, T_tank_w, 0.0, ctx.T0, flow_state=self._step_flow_state(ctx, T_tank_w))
        hp_result = off_result if off_result is not None else {}
        hp_result["failure_reason"] = "none"
        return hp_result

    def _solve_cycle_at(self, ctx: StepContext, T_tank_w: float) -> dict:
        """Cycle delivering ``hp_capacity`` to a tank at ``T_tank_w`` [°C].

        A running result (``failure_reason='none'``), or an off-mode row (no
        heat, no power, NaN COP) carrying the reason the call could not run.
        When an operating limit refuses the nameplate duty and a smaller duty
        runs, the step runs at the largest such duty, as ``analyze_steady``
        does (:func:`reduce_duty_within_envelope`).
        """
        result = self._solve_cycle_at_duty(ctx, T_tank_w, self.hp_capacity)
        return reduce_duty_within_envelope(
            self,
            result,
            lambda q: self._solve_cycle_at_duty(ctx, T_tank_w, q),
            self.hp_capacity,
            bindings=_CAPACITY_BINDINGS,
        )

    def _solve_cycle_at_duty(self, ctx: StepContext, T_tank_w: float, Q_ref_tank: float) -> dict:
        """:meth:`_solve_cycle_at` for a requested duty ``Q_ref_tank`` [W]."""
        flow_state = self._step_flow_state(ctx, T_tank_w)
        opt = self._optimize_operation(
            T_tank_w,
            Q_ref_tank,
            ctx.T0,
            flow_state=flow_state,
        )
        solved = self._state_at_optimum(opt, T_tank_w, Q_ref_tank, ctx.T0, flow_state=flow_state)
        reason = self._active_step_failure(opt, solved)
        if reason is None:
            assert solved is not None
            hp_result = solved
            hp_result["failure_reason"] = "none"
            return hp_result
        # Called but not solved: the compressor does not run. Report an
        # off-mode row (no heat, no power, NaN COP) with the reason instead
        # of a half-filled active row.
        off_result = self._calc_state(5.0, T_tank_w, 0.0, ctx.T0, flow_state=flow_state)
        hp_result = off_result if off_result is not None else {"hp_is_on": False}
        hp_result.update({"converged": False, "failure_reason": reason})
        # The detail `analyze_steady` reports for the same failure; the duty
        # reduction reads it, so a step reduces exactly where the steady solve does.
        detail: str | None = None
        if reason == "hx_not_converged":
            detail = getattr(opt, "failure_detail", None)
        elif reason == "cycle_invalid" and solved is not None:
            detail = unphysical_cycle_detail(solved, self.hp_capacity)
        if detail is not None:
            hp_result["failure_detail"] = detail
        return hp_result

    def _active_step_failure(self, opt: object, solved: dict | None) -> str | None:
        """Why a called step has no operating point, or ``None`` if it has one.

        Uses the same vocabulary as ``analyze_steady``: the cycle's own guard
        (``t_cond_below_t_in``), the pressure-ratio ceiling, plain
        infeasibility, an outdoor coil that did not converge, a compressor
        speed or outer search that did not settle, and the operating-point
        check of :func:`unphysical_cycle_detail`.
        """
        if solved is None:
            if self._last_cycle_failure_reason is not None:
                return self._last_cycle_failure_reason
            event = self._last_pr_event
            return "pr_above_max" if event is not None and event[0] == "pr_above_max" else "cycle_invalid"
        if not solved.get("converged", False):
            # `_hx_diag` marks the outdoor-coil branch; otherwise the speed
            # root find inside the cycle failed numerically.
            return "hx_not_converged" if "_hx_diag" in solved else "optimizer_failed"
        if not bool(getattr(opt, "success", False)):
            return "optimizer_failed"
        if unphysical_cycle_detail(solved, self.hp_capacity) is not None:
            return "cycle_invalid"
        return envelope_violation(solved, self)

    def _assemble_core_results(
        self,
        ctx: StepContext,
        ctrl: ControlState,
        T_solved_K: float,
        level_solved: float,
        ier: int,
        flows: TankStepFlows | None = None,
    ) -> dict:
        """Build HP-core result dict at solved state.

        Subsystem results are appended separately by
        each subsystem's ``assemble_results()``. ``flows`` are the tank flows
        the solved balance of a level-varying tank carried
        (:func:`tmhp.dynamic_context.tank_step_flows`): its draw, make-up and
        the mixed water it delivered (less than the demand when the draw
        stopped at the lowest level, ``dV_mix_w_out_unmet [m3/s]``).
        """
        den: float = max(
            1e-6,
            T_solved_K - self.T_sup_w_K,
        )
        alp: float = min(
            1.0,
            max(
                0.0,
                (self.T_mix_w_out_K - self.T_sup_w_K) / den,
            ),
        )
        dV_tank_w_out: float = alp * ctx.dV_mix_w_out
        dV_tank_w_in: float = dV_tank_w_out if ctrl.dV_tank_w_in_ctrl is None else ctrl.dV_tank_w_in_ctrl
        dV_mix_w_out: float = ctx.dV_mix_w_out
        if flows is not None:
            dV_tank_w_out, dV_tank_w_in, dV_mix_w_out = flows.dV_tank_w_out, flows.dV_tank_w_in, flows.dV_mix_w_out

        self.dV_tank_w_out = dV_tank_w_out
        self.dV_tank_w_in = dV_tank_w_in
        self.dV_mix_w_out = dV_mix_w_out
        self.dV_mix_sup_w_in = (1 - alp) * dV_mix_w_out

        T_mix_w_out_val: float = (
            calc_mixing_valve_temp(
                T_solved_K,
                self.T_sup_w_K,
                self.T_mix_w_out_K,
            )["T_mix_w_out"]
            if dV_mix_w_out > 0
            else np.nan
        )

        r: dict = {}
        r.update(ctrl.result)
        r.update(
            {
                "hp_is_on": ctrl.is_on,
                "Q_tank_loss [W]": (self.UA_tank_wall * (T_solved_K - self.T_sur_K)),
                "T_tank_w [°C]": cu.K2C(T_solved_K),
                "T_mix_w_out [°C]": T_mix_w_out_val,
                "T_tank_w_in [°C]": cu.K2C(self.T_tank_w_in_K),
                "T_sup_w [°C]": cu.K2C(self.T_sup_w_K),
            }
        )
        # Tank-side flows at the solved (end-of-step) tank temperature, as the
        # implicit tank balance uses them. The cycle result carries them at the
        # temperature its cycle was solved at (the run's mean, or the step
        # start on an off step), which T_tank_w and the tank exergy do not use.
        rc = c_w * rho_w
        T_mix_K = cu.C2K(T_mix_w_out_val) if dV_mix_w_out > 0 else np.nan
        dV_mix_sup_w_in = (1 - alp) * dV_mix_w_out
        r.update(
            {
                "dV_mix_w_out [m3/s]": dV_mix_w_out if dV_mix_w_out > 0 else np.nan,
                "dV_tank_w_out [m3/s]": dV_tank_w_out if dV_tank_w_out > 0 else np.nan,
                "dV_tank_w_in [m3/s]": dV_tank_w_in if dV_tank_w_in > 0 else np.nan,
                "dV_mix_sup_w_in [m3/s]": dV_mix_sup_w_in if dV_mix_sup_w_in > 0 else np.nan,
                "Q_tank_w_in [W]": calc_energy_flow(G=rc * dV_tank_w_in, T=self.T_tank_w_in_K, T0=ctx.T0_K),
                "Q_tank_w_out [W]": calc_energy_flow(G=rc * dV_tank_w_out, T=T_solved_K, T0=ctx.T0_K),
                "Q_mix_sup_w_in [W]": calc_energy_flow(G=rc * dV_mix_sup_w_in, T=self.T_sup_w_K, T0=ctx.T0_K),
                "Q_mix_w_out [W]": calc_energy_flow(G=rc * dV_mix_w_out, T=T_mix_K, T0=ctx.T0_K),
            }
        )
        # Hot-water service: the heat the draw schedule asked for at the set
        # point and the part not delivered (water below the set point, or a
        # draw the tank could not supply).
        r.update(dhw_service_columns(ctx.dV_mix_w_out, dV_mix_w_out, T_mix_K, self.T_mix_w_out_K, self.T_sup_w_K))

        if not self.tank_always_full or (self.tank_always_full and self.prevent_simultaneous_flow):
            r["tank_level [-]"] = level_solved
            r["dV_mix_w_out_unmet [m3/s]"] = flows.dV_mix_w_out_unmet if flows is not None else 0.0

        # Diagnostics of the steady solve, not time-series quantities: keep them
        # on the analyze_steady dict and out of the dynamic frame (an object
        # column of None/str breaks numeric frame comparisons downstream, and
        # the heat-exchanger closure terms describe one operating point rather
        # than the tank trajectory).
        for key in (
            "capacity_clamped",
            "failure_detail",
            "_hx_diag",
            "dT_ref_tank [K]",
            "UA_tank_hx [W/K]",
            "C_w_tank [W/K]",
            "epsilon_tank_hx [-]",
            "tank_hx_residual [W]",
            "UA_ou [W/K]",
            "NTU_ou [-]",
            "epsilon_ou [-]",
            "PLR_request [-]",
            "PLR_delivered [-]",
        ):
            r.pop(key, None)

        return r

    # =============================================================
    # Template Method Hooks (override in scenario subclasses)
    # =============================================================

    def _get_activation_flags(
        self,
        hour_of_day: float,
    ) -> dict[str, bool]:
        """Return per-subsystem schedule activation flags for *hour_of_day*.

        Returns a dict mapping subsystem name → ``True`` if the
        subsystem should be active at this hour.

        Default: delegates to ``self.stc.is_preheat_on()`` when an
        STC is attached (backward-compat); returns ``{}`` otherwise.
        Scenario subclasses override to implement custom schedules.
        """
        if self.stc is not None:
            return {"stc": self.stc.is_preheat_on(hour_of_day)}
        return {}

    def _needs_solar_input(self) -> bool:
        """Return True if any subsystem requires solar irradiance (I_DN, I_dH).

        Default: checks if self.stc or self.pv exists (backward-compat).
        Scenario subclasses should override this if they don't attach
        components directly to self.stc/self.pv.
        """
        return self.stc is not None or self.pv is not None

    def _build_residual_fn(
        self,
        ctx: "StepContext",
        ctrl: "ControlState",
        dt_s: float,
        T_tank_w_in_K_n: float,
        T_sup_w_K_n: float,
        tank_level: float,
        sub_states: dict,
    ):  # -> Callable[[float], float]
        """Return the 1-D energy-balance residual function for *brentq*.

        Default implementation: passes *sub_states* as fixed values
        (backward-compatible, semi-implicit).

        Scenario subclasses override this to re-evaluate their
        subsystem physics at ``T_cand`` during every iteration
        of the nonlinear solver, achieving a fully implicit solve.

        Parameters
        ----------
        ctx : StepContext
            Current-step immutable context.
        ctrl : ControlState
            HP control decisions.
        dt_s : float
            Time-step size [s].
        T_tank_w_in_K_n : float
            Mains water inlet temperature [K] (fixed for this step).
        T_sup_w_K_n : float
            Mains supply temperature [K] (for mixing valve).
        tank_level : float
            Pre-computed next-step tank level approximation.
        sub_states : dict
            Subsystem states computed by ``_run_subsystems()``
            (frozen at ``T_tank_n``; override to unfreeze).

        Returns
        -------
        Callable[[float], float]
            ``f(T_cand_K) -> residual`` for use with ``root_scalar``.
        """

        def residual(T_cand_K: float) -> float:
            return tank_mass_energy_residual(
                [T_cand_K, tank_level],
                ctx,
                ctrl,
                dt_s,
                T_tank_w_in_K_n,
                T_sup_w_K_n,
                self.T_mix_w_out_K,
                self.C_tank,
                self.UA_tank_wall,
                self.V_tank_full,
                self._subsystems,
                sub_states,
                T_sur_K=self.T_sur_K,
            )[0]

        return residual

    def _run_subsystems(
        self,
        ctx: "StepContext",
        ctrl: "ControlState",
        dt: float,
        T_tank_w_in_K: float,
    ) -> dict[str, dict]:
        """Step all attached subsystems and return their state dicts.

        Default: iterates ``self._subsystems`` (backward-compat).
        Scenario subclasses override this to call specific subsystems
        without touching ``self._subsystems``.
        """
        sub_states: dict[str, dict] = {}
        for name, sub in self._subsystems.items():
            sub_states[name] = sub.step(ctx, ctrl, dt, T_tank_w_in_K)
        return sub_states

    def _augment_results(
        self,
        r: dict,
        ctx: "StepContext",
        ctrl: "ControlState",
        sub_states: dict[str, dict],
        T_solved_K: float,
    ) -> dict:
        """Append subsystem result columns to the step result dict.

        Default: iterates ``self._subsystems.assemble_results()`` (backward-compat).
        Scenario subclasses override to call specific subsystem assemblers.
        """
        for name, sub in self._subsystems.items():
            r.update(sub.assemble_results(ctx, ctrl, sub_states[name], T_solved_K))
        # E_tot is the whole electric draw: the lamp's step-average power joins
        # the compressor and the outdoor fan (the cycle assembled E_tot before
        # the subsystems reported).
        if "uv" in self._subsystems:
            add_uv_power_to_e_tot(r)
        return r

    def _postprocess(self, df: "pd.DataFrame") -> "pd.DataFrame":
        """Post-process the result DataFrame (exergy calculations).

        Default: delegates to ``self.postprocess_exergy()`` (backward-compat).
        Scenario subclasses override to append subsystem-specific exergy columns
        after calling ``super()._postprocess(df)``.
        """
        return self.postprocess_exergy(df)

    # =============================================================
    # Public single-timestep kernel (#165 P0) — analyze_dynamic loops over it
    # =============================================================

    def make_initial_state(self, T_tank_w_init_C: float, tank_level_init: float = 1.0) -> DynamicState:
        """Build the initial carried state for a ``step()``-driven run.

        A tank whose level varies (``tank_always_full=False`` or
        ``prevent_simultaneous_flow``) starts with at least
        :data:`~tmhp.dynamic_context.TANK_LEVEL_MIN` of water: an empty tank
        has no temperature to start from.
        """
        validate_initial_state(locals())
        level_varies = not self.tank_always_full or self.prevent_simultaneous_flow
        if level_varies and tank_level_init < TANK_LEVEL_MIN:
            raise ValueError(
                f"tank_level_init={tank_level_init!r}: a tank whose level varies starts with at least "
                f"TANK_LEVEL_MIN={TANK_LEVEL_MIN} of water (an empty tank has no temperature)"
            )
        return DynamicState(
            T_tank_w_K=cu.C2K(T_tank_w_init_C),
            tank_level=tank_level_init,
            is_refilling=False,
            hp_is_on_prev=False,
            dV_tank_w_out_prev=0.0,
        )

    def step(
        self,
        state: DynamicState,
        inputs: dict,
        dt_s: float,
    ) -> tuple[DynamicState, dict]:
        """Advance one timestep from *state* under *inputs*.

        Returns ``(new_state, result_row)``. Re-entrant: every cross-step
        quantity is read from ``state`` (never ``self``), so an external
        co-simulation master (FMI/EnergyPlus) can drive the model one exchange
        at a time and re-run it independently.

        The tank thermostat is followed inside the step
        (:func:`tmhp.dynamic_context.resolve_step_operation`): the compressor
        stops when the tank reaches the upper bound and starts when it falls
        to the lower bound, at any point of the step. The heat, power and
        refrigerant-flow rates in ``result_row`` are averages over ``dt_s``
        (the running cycle times ``runtime_fraction [-]``), so
        ``rate * dt_s`` is the step's energy; ``hp_is_on`` says the
        compressor ran at some point of the step, ``hp_starts [-]`` how often
        it started, and COP and state points describe the running cycle. The per-step environment is set
        on ``self`` afresh from ``inputs`` each call (transient scratch shared
        with the cycle/tank helpers).

        Parameters
        ----------
        state : DynamicState
            Carried state from ``make_initial_state`` or the previous ``step``.
        inputs : dict
            Per-step drivers: ``n`` (int), ``current_time_s`` [s], ``T0`` [°C],
            ``dV_mix_w_out`` [m³/s], ``T_sup_w`` [°C], ``T_sur`` [°C], and
            optional ``I_DN`` / ``I_dH`` [W/m²] (default 0).
        dt_s : float
            Timestep [s].
        """
        validate_step(inputs, dt_s)
        n = inputs["n"]
        t_s: float = inputs["current_time_s"]
        hr: float = t_s * cu.s2h
        hour_of_day: float = (t_s % (24 * cu.h2s)) * cu.s2h

        # Per-step mains water supply temperature
        T_sup_w_n: float = inputs["T_sup_w"]
        T_sup_w_K_n: float = cu.C2K(T_sup_w_n)
        T_tank_w_in_K_n: float = T_sup_w_K_n

        # Sync self fields for _calc_state compat (transient, set fresh per step)
        self.T_sup_w = T_sup_w_n
        self.T_sup_w_K = T_sup_w_K_n
        self.T_tank_w_in = T_sup_w_n
        self.T_tank_w_in_K = T_tank_w_in_K_n

        # Per-step surrounding temperature
        self.T_sur_K = cu.C2K(inputs["T_sur"])

        # Subsystem activation schedule — delegated to Hook
        activation_flags: dict[str, bool] = self._get_activation_flags(hour_of_day)

        ctx: StepContext = StepContext(
            n=n,
            current_time_s=t_s,
            current_hour=hr,
            hour_of_day=hour_of_day,
            T0=inputs["T0"],
            T0_K=cu.C2K(inputs["T0"]),
            activation_flags=activation_flags,
            T_tank_w_K=state.T_tank_w_K,
            tank_level=state.tank_level,
            dV_mix_w_out=inputs["dV_mix_w_out"],
            I_DN=inputs.get("I_DN", 0.0),
            I_dH=inputs.get("I_dH", 0.0),
            T_sup_w_K=T_sup_w_K_n,
        )

        # --- Phase A: control decisions ---
        # The thermostat call carries the hysteresis; the compressor runs (and
        # heats the tank) only when the called cycle was solved.
        call_start: bool = self._thermostat_call(ctx, state.hp_is_on_prev)
        T_start_C: float = cu.K2C(ctx.T_tank_w_K)

        # Draw expected over this step (mixing valve at the start temperature):
        # the refill decision looks at the level this step's draw leaves, not
        # the previous step's draw (the tank balance then limits both flows).
        alp_prev: float = min(
            1.0, max(0.0, (self.T_mix_w_out_K - T_sup_w_K_n) / max(1e-6, ctx.T_tank_w_K - T_sup_w_K_n))
        )
        dV_tank_w_out_prev = alp_prev * ctx.dV_mix_w_out
        dV_tank_w_in_ctrl, is_refilling = determine_tank_refill_flow(
            dt=dt_s,
            tank_level=ctx.tank_level,
            dV_tank_w_out=dV_tank_w_out_prev,
            V_tank_full=self.V_tank_full,
            tank_always_full=self.tank_always_full,
            prevent_simultaneous_flow=self.prevent_simultaneous_flow,
            tank_level_lower_bound=self.tank_level_lower_bound,
            tank_level_upper_bound=self.tank_level_upper_bound,
            dV_tank_w_in_refill=self.dV_tank_w_in_refill,
            is_refilling=state.is_refilling,
        )

        # Heat-source-free control state: subsystems do not depend on the
        # compressor, and the free tank balance is read from this residual.
        ctrl_free: ControlState = ControlState(is_on=False, Q_heat_source=0.0, dV_tank_w_in_ctrl=dV_tank_w_in_ctrl)

        # --- Phase A-2: subsystem step (via Hook) ---
        # Subsystem state of the previous step, read by the hooks (never
        # written: the next state is gathered from sub_states below).
        self._subsystem_state_in = state.subsystem_state
        sub_states: dict[str, dict] = self._run_subsystems(
            ctx,
            ctrl_free,
            dt_s,
            T_tank_w_in_K_n,
        )

        # Level estimate at the start temperature. An always-full tank keeps
        # its level; a level-varying tank's residual computes the end level
        # from the flows at T^{n+1} itself, and the step takes it from there.
        dV_tank_w_in_prev = dV_tank_w_out_prev if dV_tank_w_in_ctrl is None else dV_tank_w_in_ctrl
        level_next_approx = ctx.tank_level + (dV_tank_w_in_prev - dV_tank_w_out_prev) * dt_s / self.V_tank_full
        tank_level = max(TANK_LEVEL_MIN, min(1.0, level_next_approx))

        # --- Phase A-3: thermostat events inside the step ---
        # Bounds are reached part-way through a step: the compressor runs
        # only until the tank reaches the upper bound, and starts when it
        # reaches the lower bound, instead of holding its start-of-step state
        # for the whole step (#96 T6).
        run, cycle_result = resolve_step_operation(
            T_start_K=ctx.T_tank_w_K,
            call_start=call_start,
            running_prev=state.hp_running_prev,
            phase_age_s=state.hp_phase_age_s,
            scheduled=check_hp_schedule_active(ctx.hour_of_day, self.hp_on_schedule),
            T_lower=self.T_tank_w_lower_bound,
            T_upper=self.T_tank_w_upper_bound,
            dt=dt_s,
            C_curr=self.C_tank * max(TANK_LEVEL_MIN, ctx.tank_level),
            C_next=self.C_tank * max(TANK_LEVEL_MIN, tank_level),
            C_tank=self.C_tank,
            residual_free=self._build_residual_fn(
                ctx, ctrl_free, dt_s, T_tank_w_in_K_n, T_sup_w_K_n, tank_level, sub_states
            ),
            solve_cycle=lambda T_C, _fraction: self._solve_cycle_at(ctx, T_C),
            Q_request=self.hp_capacity,
            min_on_time_s=self.min_on_time_s,
            min_off_time_s=self.min_off_time_s,
        )
        hp_result = step_hp_result(
            run,
            cycle_result,
            lambda: self._off_cycle_result(ctx, T_start_C),
            self._RUNTIME_SCALED_KEYS,
        )
        hp_is_on = bool(hp_result.get("hp_is_on", False))
        Q_ref_cond = delivered_tank_heat(hp_result)

        ctrl: ControlState = ControlState(
            is_on=hp_is_on,
            Q_heat_source=Q_ref_cond,
            dV_tank_w_in_ctrl=dV_tank_w_in_ctrl,
            result=hp_result,
        )

        # --- Phase B: implicit solve (1D over T_next since mass is explicit) ---
        residual_1d = self._build_residual_fn(
            ctx,
            ctrl,
            dt_s,
            T_tank_w_in_K_n,
            T_sup_w_K_n,
            tank_level,
            sub_states,
        )

        from scipy.optimize import root_scalar

        try:
            res = root_scalar(residual_1d, bracket=[cu.C2K(0.0), cu.C2K(100.0)], method="brentq")
            solved = bool(res.converged) and math.isfinite(res.root)
        except (ValueError, RuntimeError):
            solved = False  # no root between 0 and 100 °C, or the search did not settle
        if solved:
            T_tank_w_K = float(res.root)
            ier = 1
        else:
            # Same residual, wider range (tmhp._tank_solve): heat pump, draw-off,
            # loss and subsystem heat stay in the balance, and a step that
            # cannot be solved is named in a warning.
            T_tank_w_K = recover_tank_temperature(
                residual_1d,
                T_start_K=ctx.T_tank_w_K,
                dt=dt_s,
                C_curr=self.C_tank * max(TANK_LEVEL_MIN, ctx.tank_level),
                C_next=self.C_tank * max(TANK_LEVEL_MIN, tank_level),
                C_tank=self.C_tank,
                who=type(self).__name__,
                step=n,
            )
            ier = 0
        warn_if_tank_boiling(T_tank_w_K)
        flows = None
        if dV_tank_w_in_ctrl is not None:
            # Level-varying tank: the flows and level the solved balance carried.
            flows = tank_step_flows(T_tank_w_K, ctx, ctrl, dt_s, T_sup_w_K_n, self.T_mix_w_out_K, self.V_tank_full)
            tank_level = flows.level_next

        # --- Phase C: core + subsystem results (via Hook) ---
        r: dict = self._assemble_core_results(
            ctx,
            ctrl,
            T_tank_w_K,
            tank_level,
            ier,
            flows=flows,
        )
        r = self._augment_results(r, ctx, ctrl, sub_states, T_tank_w_K)

        new_state = DynamicState(
            T_tank_w_K=T_tank_w_K,
            tank_level=tank_level,
            is_refilling=is_refilling,
            hp_is_on_prev=run.call_end,
            dV_tank_w_out_prev=self.dV_tank_w_out,
            hp_running_prev=run.running_end,
            hp_phase_age_s=run.phase_age_end_s,
            subsystem_state=collect_subsystem_state(state.subsystem_state, sub_states),
        )
        return new_state, r

    # =============================================================
    # Main dynamic simulation
    # =============================================================

    def analyze_dynamic(
        self,
        simulation_period_sec: int,
        dt_s: int,
        T_tank_w_init_C: float,
        dhw_usage_schedule,
        T0_schedule,
        I_DN_schedule=None,
        I_dH_schedule=None,
        T_sup_w_schedule=None,
        tank_level_init: float = 1.0,
        result_save_csv_path: str | None = None,
        T_sur_schedule=None,
    ) -> pd.DataFrame:
        """Run a time-stepping dynamic simulation.

        Loops over :meth:`step`: implicit tank balance per step, with the
        thermostat followed inside each step, so heat and power are step
        averages and the result converges as ``dt_s`` shrinks.

        Parameters
        ----------
        simulation_period_sec : int
            Total simulation duration [s].
        dt_s : int
            Time step size [s].
        T_tank_w_init_C : float
            Initial tank temperature [°C].
        dhw_usage_schedule : np.ndarray
            DHW volumetric flow rate per step [m³/s].
        T0_schedule : array-like
            Outdoor temperature per step [°C].
        I_DN_schedule : array-like | None
            Direct-normal irradiance per step [W/m²].
        I_dH_schedule : array-like | None
            Diffuse-horizontal irradiance [W/m²].
        T_sup_w_schedule : array-like | None
            Mains water supply temperature per step [°C].
            If ``None``, the constructor value ``T_sup_w``
            is used for every step (backward-compatible).
        tank_level_init : float
            Initial fractional tank level (0–1).
        result_save_csv_path : str | None
            Optional CSV output path.

        Returns
        -------
        pd.DataFrame
            Per-timestep result DataFrame.
        """
        validate_dynamic(locals())

        time: np.ndarray = np.arange(
            0,
            simulation_period_sec,
            dt_s,
        )
        tN: int = len(time)

        T0_schedule = np.array(T0_schedule)
        if len(T0_schedule) != tN:
            raise ValueError(
                f"T0_schedule length ({len(T0_schedule)}) != time length ({tN})",
            )
        if I_DN_schedule is not None and len(I_DN_schedule) != tN:
            raise ValueError(
                f"I_DN_schedule length ({len(I_DN_schedule)}) != tN ({tN})",
            )
        if I_dH_schedule is not None and len(I_dH_schedule) != tN:
            raise ValueError(
                f"I_dH_schedule length ({len(I_dH_schedule)}) != tN ({tN})",
            )

        # T_sup_w schedule: fallback to constructor constant
        if T_sup_w_schedule is not None:
            T_sup_w_arr: np.ndarray = np.array(
                T_sup_w_schedule,
                dtype=float,
            )
            if len(T_sup_w_arr) != tN:
                raise ValueError(
                    f"T_sup_w_schedule length ({len(T_sup_w_arr)}) != tN ({tN})",
                )
        else:
            T_sup_w_arr = np.full(tN, self.T_sup_w)

        # T_sur schedule: fallback to constructor constant
        if T_sur_schedule is not None:
            T_sur_arr: np.ndarray = np.array(
                T_sur_schedule,
                dtype=float,
            )
            if len(T_sur_arr) != tN:
                raise ValueError(
                    f"T_sur_schedule length ({len(T_sur_arr)}) != tN ({tN})",
                )
        else:
            T_sur_arr = np.full(tN, cu.K2C(self.T_sur_K))

        self.time: np.ndarray = time
        self.dt: int = dt_s

        # DHW schedule handling: direct m³/s flow array
        self.dhw_flow_m3s: np.ndarray = np.asarray(
            dhw_usage_schedule,
            dtype=float,
        )
        if len(self.dhw_flow_m3s) != tN:
            raise ValueError(
                f"dhw_usage_schedule length ({len(self.dhw_flow_m3s)}) != tN ({tN})",
            )

        results_data: list[dict] = []

        # STC/PV schedule flags — resolve I_DN/I_dH per step before step()
        _use_solar: bool = self._needs_solar_input()

        state: DynamicState = self.make_initial_state(T_tank_w_init_C, tank_level_init)

        for n in tqdm(range(tN), desc="ASHPB Simulating"):
            inputs: dict = {
                "n": n,
                "current_time_s": time[n],
                "T0": T0_schedule[n],
                "dV_mix_w_out": self.dhw_flow_m3s[n],
                "T_sup_w": T_sup_w_arr[n],
                "T_sur": T_sur_arr[n],
                "I_DN": (I_DN_schedule[n] if _use_solar and I_DN_schedule is not None else 0.0),
                "I_dH": (I_dH_schedule[n] if _use_solar and I_dH_schedule is not None else 0.0),
            }
            state, r = self.step(state, inputs, dt_s)
            results_data.append(r)

        results_df: pd.DataFrame = pd.DataFrame(results_data)
        results_df = self._postprocess(results_df)
        if result_save_csv_path:
            results_df.to_csv(
                result_save_csv_path,
                index=False,
            )
        warn_if_dhw_unmet(results_df, dt_s, type(self).__name__, cu.K2C(self.T_mix_w_out_K))
        return results_df

    # =============================================================
    # Exergy post-processing (ASHP-specific)
    # =============================================================

    def postprocess_exergy(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute ASHP-specific exergy variables.

        Owns the full HP exergy topology:

        1. Refrigerant state-point exergy (CoolProp)
        2. Electricity = exergy (compressor, OU fan, UV)
        3. Air exergy (outdoor unit)
        4. Heat exchanger Carnot exergy (condenser, evaporator)
        5. Water exergy (tank inlet/outlet, mixing valve)
        6. Heat loss exergy, tank stored exergy
        7. Subsystem exergy via ``calc_exergy()`` protocol
        8. Component-level exergy destruction
        9. Exergetic efficiency metrics

        Parameters
        ----------
        df : pd.DataFrame
            Result DataFrame from ``analyze_dynamic()``.

        Returns
        -------
        pd.DataFrame
            DataFrame with exergy columns appended.
        """
        from .thermodynamics import (
            calc_exergy_flow,
            calc_refrigerant_exergy,
            convert_electricity_to_exergy,
        )

        df = df.copy()
        T0_K = cu.C2K(df["T0 [°C]"])
        T_tank_K = cu.C2K(df["T_tank_w [°C]"])

        # ── 1. Refrigerant exergy (uses pre-computed h/s from calc_ref_state)
        df = calc_refrigerant_exergy(df, self.ref, T0_K)

        # ── 2. Electricity = exergy ────────────────────────
        df = convert_electricity_to_exergy(df)

        # ── 3. Air exergy (outdoor unit) ───────────────────
        if "dV_ou_a [m3/s]" in df.columns and "T_ou_a_in [°C]" in df.columns:
            G_a = c_a * rho_a * df["dV_ou_a [m3/s]"]
            Tin = cu.C2K(df["T_ou_a_in [°C]"])
            Tmid = cu.C2K(df["T_ou_a_mid [°C]"])
            Tout = cu.C2K(df["T_ou_a_out [°C]"]) if "T_ou_a_out [°C]" in df.columns else Tin
            df["X_a_ou_in [W]"] = calc_exergy_flow(G_a, Tin, T0_K)
            df["X_a_ou_out [W]"] = calc_exergy_flow(G_a, Tout, T0_K)
            df["X_a_ou_mid [W]"] = calc_exergy_flow(G_a, Tmid, T0_K)

        # ── 4. HX exergy (Carnot form) ─────────────────────
        Q_ref_tank = df["Q_ref_tank [W]"].fillna(0)
        Q_ref_ou = df["Q_ref_ou [W]"].fillna(0)
        df["X_ref_tank [W]"] = np.where(
            Q_ref_tank > 0,
            Q_ref_tank * (1 - T0_K / cu.C2K(df["T_ref_cond_sat_v [°C]"])),
            0.0,
        )
        df["X_ref_ou [W]"] = np.where(
            Q_ref_ou > 0,
            Q_ref_ou * (1 - T0_K / cu.C2K(df["T_ref_evap_sat [°C]"])),
            0.0,
        )

        # ── 5. Water exergy (inlet / outlet) ───────────────
        df["X_tank_w_in [W]"] = calc_exergy_flow(
            c_w * rho_w * df["dV_tank_w_in [m3/s]"].fillna(0),
            cu.C2K(df["T_tank_w_in [°C]"]),
            T0_K,
        )
        df["X_tank_w_out [W]"] = calc_exergy_flow(
            c_w * rho_w * df["dV_tank_w_out [m3/s]"].fillna(0),
            cu.C2K(df["T_tank_w [°C]"]),
            T0_K,
        )
        df["X_mix_w_out [W]"] = calc_exergy_flow(
            c_w * rho_w * df["dV_mix_w_out [m3/s]"].fillna(0),
            cu.C2K(df["T_mix_w_out [°C]"]),
            T0_K,
        )
        df["X_mix_sup_w_in [W]"] = calc_exergy_flow(
            c_w * rho_w * df["dV_mix_sup_w_in [m3/s]"].fillna(0),
            cu.C2K(df["T_sup_w [°C]"]),
            T0_K,
        )

        # ── 6. Heat loss exergy ────────────────────────────
        df["X_tank_loss [W]"] = df["Q_tank_loss [W]"] * (1 - T0_K / T_tank_K)

        # ── 7. Tank stored exergy ──────────────────────────
        tank_level = df["tank_level [-]"] if "tank_level [-]" in df.columns else 1.0
        C_tank_actual = self.C_tank * tank_level
        T_tank_K_prev = T_tank_K.shift(1)
        df["Xst_tank [W]"] = (1 - T0_K / T_tank_K) * C_tank_actual * (T_tank_K - T_tank_K_prev) / self.dt
        df.loc[df.index[0], "Xst_tank [W]"] = 0.0

        # ── 8. Removed Subsystem exergy (protocol) ─────────
        # Subsystems handle their own exergy via _postprocess hook.

        # ── 9. Total exergy input (system-level) ──────────
        X_tot = df["E_cmp [W]"] + df["E_ou_fan [W]"]
        if "X_uv [W]" in df.columns:
            X_tot = X_tot + df["X_uv [W]"].fillna(0)
        df["X_tot [W]"] = X_tot

        # ── 10. Component exergy destruction ───────────────
        # Xc = ΣX_in − ΣX_out ≥ 0 (2nd law)
        df["Xc_cmp [W]"] = df["X_cmp [W]"] + df["X_ref_cmp_in [W]"] - df["X_ref_cmp_out [W]"]
        ref_tank_active = Q_ref_tank > 0
        df["Xc_ref_tank [W]"] = np.where(
            ref_tank_active,
            df["X_ref_cmp_out [W]"] - df["X_ref_exp_in [W]"] - df["X_ref_tank [W]"],
            0.0,
        )
        df["Xc_exp [W]"] = df["X_ref_exp_in [W]"] - df["X_ref_exp_out [W]"]
        df["Xc_ref_ou [W]"] = (df["X_ref_exp_out [W]"] + df["X_a_ou_in [W]"]) - (
            df["X_ref_cmp_in [W]"] + df["X_a_ou_mid [W]"]
        )
        df["Xc_ou_fan [W]"] = df["X_ou_fan [W]"] + df["X_a_ou_mid [W]"] - df["X_a_ou_out [W]"]
        df["Xc_mix [W]"] = df["X_tank_w_out [W]"] + df["X_mix_sup_w_in [W]"] - df["X_mix_w_out [W]"]

        # 10g. Storage tank
        X_in_tank = df["X_ref_tank [W]"].fillna(0) + df["X_tank_w_in [W]"].fillna(0)
        if "X_uv [W]" in df.columns:
            X_in_tank = X_in_tank + df["X_uv [W]"].fillna(0)

        X_out_tank = df["Xst_tank [W]"]  # X_tank_loss is intentionally excluded here: it is treated as
        # part of the tank's exergy consumption rather than an outflow.
        if "X_tank_w_out [W]" in df.columns:
            X_out_tank = X_out_tank + df["X_tank_w_out [W]"].fillna(0)

        df["Xc_tank [W]"] = X_in_tank - X_out_tank

        # ── 11. Exergetic efficiency metrics ───────────────
        df["X_eff_ref [-]"] = df["X_ref_tank [W]"] / df["X_cmp [W]"].replace(0, np.nan)
        df["X_eff_sys [-]"] = df["X_ref_tank [W]"] / df["X_tot [W]"].replace(0, np.nan)

        df["X_eff_tank [-]"] = 1 - df["Xc_tank [W]"] / X_in_tank.replace(0, np.nan)

        X_in_mix = df["X_tank_w_out [W]"].fillna(0) + df["X_mix_sup_w_in [W]"].fillna(0)
        df["X_eff_mix [-]"] = 1 - df["Xc_mix [W]"] / X_in_mix.replace(0, np.nan)

        X_in_cmp = df["X_cmp [W]"] + df["X_ref_cmp_in [W]"]
        df["X_eff_cmp [-]"] = 1 - df["Xc_cmp [W]"] / X_in_cmp.replace(0, np.nan)

        df["X_eff_ref_tank [-]"] = 1 - df["Xc_ref_tank [W]"] / df["X_ref_cmp_out [W]"].replace(0, np.nan)

        df["X_eff_exp [-]"] = 1 - df["Xc_exp [W]"] / df["X_ref_exp_in [W]"].replace(0, np.nan)

        a_ou_in = df["X_a_ou_in [W]"].fillna(0) if "X_a_ou_in [W]" in df.columns else 0.0
        X_in_ref_ou = df["X_ref_exp_out [W]"] + a_ou_in
        df["X_eff_ref_ou [-]"] = 1 - df["Xc_ref_ou [W]"] / X_in_ref_ou.replace(0, np.nan)

        a_ou_mid = df["X_a_ou_mid [W]"].fillna(0) if "X_a_ou_mid [W]" in df.columns else 0.0
        X_in_ou_fan = df["X_ou_fan [W]"] + a_ou_mid
        df["X_eff_ou_fan [-]"] = 1 - df["Xc_ou_fan [W]"] / X_in_ou_fan.replace(0, np.nan)

        return df
