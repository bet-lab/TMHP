"""Ground source heat pump — physics-based cycle model with indoor unit.

Resolves a vapour-compression refrigerant cycle coupled to a borehole
heat exchanger (BHE) on the source side and an indoor-air heat exchanger
on the load side.  Supports both **cooling** (``Q_r_iu > 0``) and
**heating** (``Q_r_iu < 0``) modes.

At each time step the model finds the operating point of least electrical
input per unit of delivered heat (compressor + BHE pump + indoor fan). The
indoor approach temperature is searched (coarse grid, then a bounded
refinement); the ground-side approach follows from the ground heat-exchanger
balance at the fixed loop flow.

Borehole thermal response is tracked with pygfunction-based multi-borehole
g-functions, enabling robust long-term ground temperature drift modelling.
The effective borehole thermal resistance ``R_b*`` linking the borehole wall to
the circulating fluid is derived from the U-tube cross-section geometry unless it
is supplied explicitly.

Architecture mirrors ``GroundSourceHeatPumpBoiler`` for the BHE side
and ``AirSourceHeatPump`` for the indoor-unit side.
"""

from __future__ import annotations

import contextlib
import math
import warnings
from collections.abc import Callable, Mapping
from typing import Any, Literal

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar
from tqdm import tqdm

from . import calc_util as cu
from ._opt_utils import OBJ_INFEASIBLE, margin_feasible_interval, operating_point_objective
from ._validation import validate_ctor, validate_dynamic, validate_steady
from .compressor_efficiency import _eval_eff, reject_invalid_efficiency
from .compressor_envelope import (
    BINDING_DISCHARGE,
    DISCHARGE_TEMP_ABOVE_MAX,
    ETA_ISEN_BELOW_MIN,
    PR_CYCLE_MAX_SINGLE_STAGE,
    RATED_SPEED_SEARCH_MAX,
    X_SUCTION_MIN_DEFAULT,
    check_pr_envelope,
    discharge_controlled,
    resolve_discharge_temp_max,
    resolve_x_suction_min,
    steady_envelope,
)
from .compressor_speed import CAPACITY_CLAMPED_MAX, default_displacement, solve_compressor_speed
from .constants import c_a, c_w, k_w, mu_w, rho_a, rho_w
from .enex_functions import (
    calc_energy_flow,
    calc_exergy_flow,
    calc_fan_power_from_dV_fan,
    calc_HX_perf_for_target_heat,
)
from .g_function import g_step_mean, precompute_gfunction
from .ground_defaults import (
    BOREHOLE_DEFAULT_DEPTH,
    calc_rated_linear_load,
    default_borefield_layout,
    default_ground_flow_lpm,
    warn_if_borefield_undersized,
    warn_if_laminar_ground_flow,
)
from .ground_flow_control import (
    close_ground_temperature,
    select_ground_flow,
    solve_ground_approach,
)
from .ground_loop import (
    calc_bhe_fluid_temperatures,
    calc_borefield_linear_load,
    calc_borehole_count,
    calc_total_borehole_length,
    configure_ground_flow,
    ground_flow_state,
    ground_hx_UA,
    ground_result_diagnostics,
    resolve_ground_flow_rates,
)
from .heat_exchanger import (
    calc_ground_hx_UA_from_capacity,
    calc_phase_change_hx_effectiveness,
    resolve_fan_flow_limits,
)
from .hx_fan import SINGLE_ZONE_VAV_COEFFICIENTS, is_generic_fan_curve
from .pump import default_ground_pump_power
from .reference_state import HXSide, RatingCondition, ReferenceStateMixin
from .refrigerant import (
    calc_ref_state,
    reportable_state,
)


def _require_finite(**values: float | None) -> None:
    """Reject NaN/inf inputs with a named ValueError (``None`` means omitted)."""
    for name, value in values.items():
        if value is None:
            continue
        try:
            finite = bool(np.isfinite(float(value)))
        except (TypeError, ValueError):
            finite = False
        if not finite:
            raise ValueError(f"{name} must be a finite number, got {value!r}")


#: Default rated indoor airflow per watt of capacity [m³/s per W]: 400 cfm per
#: ton of refrigeration, the usual selection airflow of a water-to-air unit
#: (0.215 m³/s = 773 m³/h for 4 kW).
IU_AIRFLOW_PER_W = 5.37e-5

#: Borehole-wall temperature seen by a ground closure [K]: a fixed value, or a
#: function of the borefield load ``Q_bhe`` [W] of the point being evaluated
#: (a dynamic step, whose own load moves the wall within the step).
WallTemperature = float | Callable[[float], float]

#: Signed indoor-coil capacity margins at the fan flow limits (row keys).
_IU_MARGIN_AT_MAX_FLOW = "iu_hx_max_flow_margin [W]"
_IU_MARGIN_AT_MIN_FLOW = "iu_hx_min_flow_margin [W]"


class _IndoorSearch:
    """Cached candidate operating points over the indoor approach for one request.

    Each candidate closes the ground side (:meth:`GroundSourceHeatPump.
    _close_ground_side`) and is ranked on the three-band objective. Calling the
    instance returns that objective, so it can be handed to a scalar minimiser.
    """

    def __init__(
        self,
        model: GroundSourceHeatPump,
        Q_r_iu: float,
        T0: float,
        T_a_room: float,
        wall_K: WallTemperature | None,
        warm: dict[str, float] | None = None,
    ) -> None:
        self.model = model
        self.Q_r_iu, self.T0, self.T_a_room, self.wall_K = Q_r_iu, T0, T_a_room, wall_K
        self.rows: dict[float, tuple[float, str, dict | None]] = {}
        self.warm: dict[str, float] = dict(warm or {})
        self.pr_ceiling_seen = False
        self.envelope_reason: str | None = None

    def entry(self, load_approach: float) -> tuple[float, str, dict | None]:
        load_approach = float(load_approach)
        if load_approach not in self.rows:
            model = self.model
            status, row, g, T_out = model._close_ground_side(
                load_approach, self.Q_r_iu, self.T0, self.T_a_room, self.wall_K, self.warm
            )
            if status == "invalid" and model._last_pr_event is not None:
                self.pr_ceiling_seen = self.pr_ceiling_seen or model._last_pr_event[0] == "pr_above_max"
                if model._last_pr_event[0] in (DISCHARGE_TEMP_ABOVE_MAX, ETA_ISEN_BELOW_MIN):
                    self.envelope_reason = model._last_pr_event[0]
            value = OBJ_INFEASIBLE
            if row is not None and status in model._BALANCED:
                self.warm.update(g=g, T_out=T_out)
                row = {
                    **row,
                    "ground_approach [K]": g,
                    "indoor_approach [K]": load_approach,
                    "ground_balance": status,
                }
                if (
                    row.get("converged", False)
                    and row.get("converged_rps", True)
                    and row.get("indoor_heat_direction_valid", True)
                ):
                    value = operating_point_objective(
                        float(row["E_tot [W]"]),
                        float(row["Q_ref_iu [W]"]),
                        self.Q_r_iu,
                        row.get("capacity_clamped"),
                    )
            self.rows[load_approach] = (value, status, row)
        return self.rows[load_approach]

    def __call__(self, load_approach: float) -> float:
        return self.entry(load_approach)[0]

    def feasible(self, load_approach: float) -> bool:
        return self.entry(load_approach)[0] < OBJ_INFEASIBLE

    def balanced(self, load_approach: float) -> bool:
        _, status, row = self.entry(load_approach)
        return row is not None and status in self.model._BALANCED and _IU_MARGIN_AT_MAX_FLOW in row

    def margin(self, load_approach: float, key: str) -> float:
        if not self.balanced(load_approach):
            raise ValueError("No balanced cycle at this indoor approach")
        row = self.entry(load_approach)[2]
        assert row is not None
        return float(row[key])

    def best(self) -> tuple[float, str, dict | None]:
        return min(self.rows.values(), key=lambda item: item[0])


class GroundSourceHeatPump(ReferenceStateMixin):
    """Ground source heat pump with BHE and indoor-unit air heat exchange.

    The refrigerant cycle is resolved via CoolProp. The operating point
    minimises total electrical input (``E_cmp + E_pmp + E_iu_fan``) per unit
    of delivered heat over the indoor approach temperature, with the
    ground-side approach closed by the ground heat-exchanger balance.

    When the requested duty exceeds what the unit can deliver, the result is
    the delivered ceiling with ``capacity_clamped == "max"`` and
    ``Q_ref_iu [W]`` below the request; the difference is unmet load, not a
    solver failure. A request below the minimum-speed duty runs at that speed
    and reports ``capacity_clamped == "min"`` with the larger delivery.

    **Defaults that follow the capacity.** With only ``hp_capacity`` (and
    ``ref``) given, the ground side and the indoor airflow are sized from it,
    so the rating-point COP is the same at every capacity:

    - loop flow ``ground_flow_ref_lpm``:
      ``0.75 * hp_capacity / (rho c * 3 K)``
      (:func:`~tmhp.ground_defaults.default_ground_flow_lpm`; 14.3 L/min for
      4 kW), shared equally by the parallel boreholes;
    - pump power ``E_pmp``: ASHRAE pumping grade B, 75 W per ton of capacity
      (:func:`~tmhp.pump.default_ground_pump_power`; 85 W for 4 kW);
    - borefield: when none of ``N_1``, ``N_2`` and ``H_b`` is given, the
      smallest near-square field of 100 m boreholes that keeps the rated
      source-side heat (``0.75 * hp_capacity``) within 50 W/m
      (:func:`~tmhp.ground_defaults.default_borefield_layout`; one borehole up to
      6.67 kW, 3 x 2 for 30 kW). Giving any of the three fixes the field as
      given (the others default to 1, 1 and 100 m);
    - rated indoor airflow ``dV_iu_fan_a_rated``: 5.37e-5 m³/s per W
      (400 cfm per ton; 773 m³/h for 4 kW). With the default coil
      (``UA_iu_rated = 0.08 * hp_capacity``) the rated duty needs an indoor
      approach of 21.6 K, which is why ``indoor_approach_max_K`` defaults to
      25 K.

    A supplied value always replaces its default. Two sizing slips are
    reported with a ``UserWarning`` instead of being solved silently: a
    U-tube that is laminar at the reference flow (``Re < 2300``; the model
    attribute ``Re_bhe_ref``) and a caller-sized borefield above 80 W/m at the
    rated source-side heat (``q_bhe_rated``).

    **Dynamic steps.** :meth:`analyze_dynamic` closes the ground-fluid
    temperature inside each step against the borehole wall, including the
    wall's response to that step's own load. The refrigerant cycle, the
    fluid temperatures and the wall temperature of one row therefore belong
    to the same step.
    """

    _RATING_FAMILY = "GSHP"
    _RATING_DEFAULT_MODE = "cooling"

    def __init__(
        self,
        # 1. Refrigerant / cycle / compressor -----------
        ref: str = "R32",
        V_cmp_ref: float | None = None,
        eta_cmp_isen: float | Callable | None = None,
        dT_superheat: float = 5.0,
        dT_subcool: float = 5.0,
        # 2. Heat exchanger UA ---------------------------
        UA_cond: float | None = None,
        UA_evap: float | None = None,
        # 3. Indoor unit fan -----------------------------
        dV_iu_fan_a_rated: float | None = None,
        dP_iu_fan_rated: float | None = None,
        A_cross_iu: float | None = None,
        eta_iu_fan_rated: float | None = None,
        vsd_coeffs_iu: dict | None = None,
        # 4. BHE (Borehole Heat Exchanger) ---------------
        N_1: int | None = None,
        N_2: int | None = None,
        B: float = 6.0,
        D_b: float = 0,
        H_b: float | None = None,
        r_b: float = 0.08,
        R_b: float | None = None,
        k_g: float = 1.5,
        k_p: float = 0.4,
        r_out: float = 0.016,
        r_in: float = 0.013,
        D_s: float = 0.025,
        boundary_condition: str = "uniform_temperature",
        dV_b_f_lpm: float | None = None,
        k_s: float = 2.0,
        c_s: float = 800,
        rho_s: float = 2000,
        Ts: float = 16.0,
        E_pmp: float | None = None,
        # 5. System capacity / room ----------------------
        hp_capacity: float = 4000.0,
        T_a_room: float = 27.0,
        # 6. Cycle guard ---------------------------------
        dT_hx_min: float = 0.5,
        # Compressor pressure-ratio envelope (PR = P_cond / P_evap); the
        # ceiling is the single-stage scroll limit, as for ASHP (see compressor_envelope).
        PR_cycle_min: float = 1.5,
        PR_cycle_max: float = PR_CYCLE_MAX_SINGLE_STAGE,
        # 7. Simulation scope ----------------------------
        t_max_s: float = 8760 * 3600,
        dt_s: float = 3600,
        # Deprecated:
        V_disp_cmp: float | None = None,
        UA_cond_design: float | None = None,
        UA_evap_design: float | None = None,
        dV_iu_fan_a_design: float | None = None,
        dP_iu_fan_design: float | None = None,
        eta_iu_fan_design: float | None = None,
        *,
        UA_ground_rated: float | None = None,
        ground_hx_ua_per_capacity: float = 0.18,
        UA_iu_rated: float | None = None,
        indoor_approach_min_K: float = 1.0,
        indoor_approach_max_K: float = 25.0,
        eta_cmp_vol: float | Callable | None = None,
        eta_cmp: float | Callable | None = None,
        rps_rated: float | None = None,
        eta_v: float | None = None,
        eta_em: float | None = None,
        ground_flow_ref_lpm: float | None = None,
        ground_flow_constant_lpm: float | None = None,
        ground_flow_min_lpm: float | None = None,
        ground_flow_max_lpm: float | None = None,
        ground_flow_control: str = "constant",
        variable_ground_flow: bool = False,
        variable_ground_hx_UA: bool = False,
        variable_Rb: bool = False,
        hydraulic_pump: bool = False,
        pump_efficiency: float = 0.6,
        pump_map: Mapping[str, Any] | None = None,
        pipe_inner_diameter: float | None = None,
        pipe_roughness: float = 1e-6,
        dp_common: float | None = None,
        dp_aux_ref: float = 0.0,
        dp_aux_exponent: float = 2.0,
        ground_flow_min_ratio: float | None = None,
        ground_flow_max_ratio: float | None = None,
        m_dot_ref_rated: float | None = None,
        rated_condition: Mapping[str, Any] | RatingCondition | None = None,
        ground_hx_fluid_fraction: float = 0.5,
        ground_hx_refrigerant_fraction: float = 0.3,
        ground_hx_constant_fraction: float = 0.2,
        ground_hx_fluid_exponent: float = 0.8,
        ground_hx_refrigerant_exponent: float = 0.8,
        rps_min: float = 15.0,
        rps_max: float | None = None,
        dV_iu_fan_a_ref: float | None = None,
        dV_iu_fan_a_min: float | None = None,
        dV_iu_fan_a_max: float | None = None,
        T_dis_max: float | Literal["auto"] | None = "auto",
        x_suction_min: float | None = X_SUCTION_MIN_DEFAULT,
    ):
        validate_ctor("GSHP", locals())
        # Ground side sized from the capacity when the caller gives none of it
        # (see tmhp.ground_defaults): flow, pump power and the borefield.
        borefield_defaulted = N_1 is None and N_2 is None and H_b is None
        if H_b is None:
            H_b = BOREHOLE_DEFAULT_DEPTH
        if borefield_defaulted:
            N_1, N_2 = default_borefield_layout(hp_capacity, H_b)
        else:
            N_1 = 1 if N_1 is None else N_1
            N_2 = 1 if N_2 is None else N_2
        if E_pmp is None:
            E_pmp = default_ground_pump_power(hp_capacity)
        ground_rates = resolve_ground_flow_rates(
            default_ref_lpm=default_ground_flow_lpm(hp_capacity),
            legacy_ref_lpm=dV_b_f_lpm,
            min_ratio=ground_flow_min_ratio,
            max_ratio=ground_flow_max_ratio,
            ref_lpm=ground_flow_ref_lpm,
            constant_lpm=ground_flow_constant_lpm,
            min_lpm=ground_flow_min_lpm,
            max_lpm=ground_flow_max_lpm,
        )
        self.ground_flow_ref_lpm = ground_rates["ref_lpm"]
        self.ground_flow_constant_lpm = ground_rates["constant_lpm"]
        self.ground_flow_min_lpm = ground_rates["min_lpm"]
        self.ground_flow_max_lpm = ground_rates["max_lpm"]
        dV_b_f_lpm = self.ground_flow_ref_lpm
        if dV_iu_fan_a_ref is not None:
            dV_iu_fan_a_rated = dV_iu_fan_a_ref
        if (
            not all(np.isfinite(v) for v in (indoor_approach_min_K, indoor_approach_max_K))
            or not 0 < indoor_approach_min_K < indoor_approach_max_K
        ):
            raise ValueError("Require finite 0 < indoor_approach_min_K < indoor_approach_max_K")
        self.indoor_approach_min_K = indoor_approach_min_K
        self.indoor_approach_max_K = indoor_approach_max_K
        # Resolve deprecated mapping
        if V_cmp_ref is None:
            V_cmp_ref = V_disp_cmp if V_disp_cmp is not None else default_displacement(hp_capacity)
        if UA_cond is None:
            UA_cond = UA_cond_design
        if UA_evap is None:
            UA_evap = UA_evap_design
        if dV_iu_fan_a_rated is None:
            dV_iu_fan_a_rated = dV_iu_fan_a_design
        if dP_iu_fan_rated is None:
            dP_iu_fan_rated = dP_iu_fan_design if dP_iu_fan_design is not None else 60.0
        if eta_iu_fan_rated is None:
            eta_iu_fan_rated = eta_iu_fan_design if eta_iu_fan_design is not None else 0.6

        if not vsd_coeffs_iu:
            vsd_coeffs_iu = SINGLE_ZONE_VAV_COEFFICIENTS.copy()

        # --- 1. Refrigerant / cycle / compressor ---
        self.ref: str = ref
        self.V_cmp_ref: float = V_cmp_ref
        # Validation-only speed for scalar/legacy inputs (the rated speed may not
        # be known yet; scalars do not depend on it).
        rps_check = rps_rated if rps_rated is not None else rps_min
        for name, legacy in (("eta_v", eta_v), ("eta_em", eta_em)):
            if legacy is not None:
                _eval_eff(legacy, 3.0, rps_check)
                warnings.warn(
                    f"{name} is deprecated; use eta_cmp_vol / eta_cmp. Explicit new inputs take precedence.",
                    DeprecationWarning,
                    stacklevel=2,
                )
        if eta_cmp_vol is None:
            eta_cmp_vol = eta_v
        if eta_cmp is None:
            eta_cmp = eta_em
        for efficiency in (eta_cmp_isen, eta_cmp_vol, eta_cmp):
            if efficiency is not None and not callable(efficiency):
                _eval_eff(efficiency, 3.0, rps_check)
        self.dT_superheat: float = dT_superheat
        self.dT_subcool: float = dT_subcool
        self.dT_hx_min: float = dT_hx_min
        # Compressor pressure-ratio envelope (floor -> clamp, ceiling -> reject)
        self.PR_cycle_min: float = PR_cycle_min
        self.PR_cycle_max: float = PR_cycle_max
        self._last_pr_event: tuple[str, float, float] | None = None
        self.hp_capacity: float = hp_capacity
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

        # --- 2. Physical heat exchangers and compressor efficiencies ---
        # Water HX sizing is independent of the air HX and of cycle mode.
        self.UA_ground_rated = (
            calc_ground_hx_UA_from_capacity(hp_capacity, ground_hx_ua_per_capacity)
            if UA_ground_rated is None
            else UA_ground_rated
        )
        self.ground_hx_ua_per_capacity = ground_hx_ua_per_capacity
        # Preserve the former cooling indoor-air default, independently of
        # ground-HX sizing. This is an air-HX assumption, not the BPHE rule.
        self.UA_iu_rated = 0.08 * hp_capacity if UA_iu_rated is None else UA_iu_rated
        if not all(np.isfinite(v) and v > 0 for v in (self.UA_ground_rated, self.UA_iu_rated)):
            raise ValueError("Physical heat exchanger rated UA values must be positive and finite")
        legacy_ua = UA_cond is not None or UA_evap is not None
        self._legacy_ground_ua = legacy_ua and UA_ground_rated is None
        self._legacy_indoor_ua = legacy_ua and UA_iu_rated is None
        if legacy_ua:
            warnings.warn(
                "UA_cond/UA_evap (including *_design) are deprecated cycle-role inputs; "
                "use UA_ground_rated and UA_iu_rated for mode-independent physical heat exchangers. "
                "Explicit physical UA inputs take precedence.",
                DeprecationWarning,
                stacklevel=2,
            )
            # Compatibility only: retain explicitly requested old role mapping.
            self.UA_cond = hp_capacity / 10.0 if UA_cond is None else UA_cond
            self.UA_evap = 0.8 * self.UA_cond if UA_evap is None else UA_evap
        else:
            # Read-compatible cooling-role aliases; no cycle computation uses
            # these aliases unless the caller explicitly used the legacy API.
            self.UA_cond, self.UA_evap = self.UA_ground_rated, self.UA_iu_rated

        # --- 3. Indoor unit fan ---
        if dV_iu_fan_a_rated is None:
            self.dV_iu_fan_a_rated = hp_capacity * IU_AIRFLOW_PER_W
        else:
            self.dV_iu_fan_a_rated = dV_iu_fan_a_rated

        self.dV_iu_fan_a_ref = self.dV_iu_fan_a_rated
        self.dV_iu_fan_a_min, self.dV_iu_fan_a_max = resolve_fan_flow_limits(
            self.dV_iu_fan_a_ref,
            dV_iu_fan_a_min,
            dV_iu_fan_a_max,
            custom_curve=not is_generic_fan_curve(vsd_coeffs_iu),
        )
        self.dP_iu_fan_rated: float = dP_iu_fan_rated
        self.eta_iu_fan_rated: float = eta_iu_fan_rated

        if A_cross_iu is None:
            self.A_cross_iu = self.dV_iu_fan_a_rated / 2.0
        else:
            self.A_cross_iu = A_cross_iu

        self.E_iu_fan_rated: float = self.dV_iu_fan_a_rated * self.dP_iu_fan_rated / self.eta_iu_fan_rated
        self.vsd_coeffs_iu: dict = vsd_coeffs_iu
        self.fan_params_iu: dict = {
            "fan_min_flow_rate": self.dV_iu_fan_a_min,
            "fan_max_flow_rate": self.dV_iu_fan_a_max,
            "fan_ref_flow_rate": self.dV_iu_fan_a_rated,
            "fan_rated_flow_rate": self.dV_iu_fan_a_rated,
            "fan_ref_power": self.E_iu_fan_rated,
            "fan_rated_power": self.E_iu_fan_rated,
        }

        # --- 4. BHE ---
        self.n_boreholes = calc_borehole_count(N_1, N_2)
        self.total_borehole_length = calc_total_borehole_length(self.n_boreholes, H_b)
        self.N_1 = N_1
        self.N_2 = N_2
        self.B = B
        self.D_b = D_b
        self.H_b = H_b
        self.r_b = r_b
        self.k_s = k_s
        self.c_s = c_s
        self.rho_s = rho_s
        self.alp_s = k_s / (c_s * rho_s)
        self.E_pmp: float = E_pmp
        self.dV_b_f_m3s: float = dV_b_f_lpm / 60000
        #: Specific borehole load at the rated source-side heat [W/m]. A field
        #: the caller sized is reported when it exceeds 80 W/m.
        self.q_bhe_rated: float = (
            calc_rated_linear_load(hp_capacity, self.n_boreholes, H_b)
            if borefield_defaulted
            else warn_if_borefield_undersized(hp_capacity, self.n_boreholes, H_b, stacklevel=3)
        )
        #: U-tube Reynolds number at the reference flow (laminar below 2300: warned).
        pipe_diameter = pipe_inner_diameter if pipe_inner_diameter is not None else 2 * r_in
        self.Re_bhe_ref: float = warn_if_laminar_ground_flow(
            self.dV_b_f_m3s, self.n_boreholes, pipe_diameter, rho=rho_w, mu=mu_w, stacklevel=3
        )
        if self.ground_flow_constant_lpm != self.ground_flow_ref_lpm:
            warn_if_laminar_ground_flow(
                self.ground_flow_constant_lpm / 60000,
                self.n_boreholes,
                pipe_diameter,
                rho=rho_w,
                mu=mu_w,
                label="constant setpoint",
                stacklevel=3,
            )

        # Effective borehole thermal resistance R_b* [mK/W].
        # Mirrors GroundSourceHeatPumpBoiler: when R_b is not given explicitly,
        # derive it from the borehole cross-section (multipole method) and then
        # apply the axial short-circuit correction, so T_bhe_f = T_bhe - q_b*R_b*
        # reflects the actual U-tube geometry instead of a fixed literature value.
        if R_b is None:
            from .borehole import (
                calc_effective_borehole_thermal_resistance,
                calc_local_borehole_thermal_resistance,
            )

            n_boreholes = max(1, self.N_1 * self.N_2)
            m_flow_pipe = self.dV_b_f_m3s * rho_w / n_boreholes

            R_b_local, R_a = calc_local_borehole_thermal_resistance(
                k_s=self.k_s,
                k_g=k_g,
                k_p=k_p,
                r_b=self.r_b,
                r_out=r_out,
                r_in=r_in,
                D_s=D_s,
                m_flow_pipe=m_flow_pipe,
                rho_f=rho_w,
                mu_f=mu_w,
                cp_f=c_w,
                k_f=k_w,
            )
            self.R_b_local: float = R_b_local
            self.R_a: float = R_a
            self.R_b = calc_effective_borehole_thermal_resistance(
                R_b=R_b_local,
                R_a=R_a,
                H=self.H_b,
                m_flow_pipe=m_flow_pipe,
                cp_f=c_w,
                boundary_condition=boundary_condition,
            )
        else:
            self.R_b = R_b

        if not all(np.isfinite(v) and v > 0 for v in (self.UA_cond, self.UA_evap)):
            raise ValueError("Heat exchanger UA values must be positive and finite")
        if not 0 < self.rps_min < self.rps_max or not np.isfinite(self.rps_max):
            raise ValueError("Require finite 0 < rps_min < rps_max")
        # Compressor reference state at rated (fixed) UA, before variable
        # ground-HX UA is configured with the m_dot_ref_rated it produces.
        efficiencies = self._initialize_reference_state(
            rps_rated=rps_rated,
            m_dot_ref_rated=m_dot_ref_rated,
            efficiencies={
                "eta_cmp_isen": eta_cmp_isen,
                "eta_cmp_vol": eta_cmp_vol,
                "eta_cmp": eta_cmp,
            },
            rated_condition=rated_condition,
        )
        self.eta_cmp_isen = efficiencies["eta_cmp_isen"]
        self.eta_cmp_vol = efficiencies["eta_cmp_vol"]
        self.eta_cmp = efficiencies["eta_cmp"]
        self.eta_v, self.eta_em = (
            self.eta_cmp_vol,
            self.eta_cmp,
        )  # read-compatible aliases
        self._ground_settings = configure_ground_flow(
            control=ground_flow_control,
            variable_ground_flow=variable_ground_flow,
            variable_UA=variable_ground_hx_UA,
            variable_Rb=variable_Rb,
            hydraulic_pump=hydraulic_pump,
            volume_flow_ref=self.dV_b_f_m3s,
            volume_flow_constant=self.ground_flow_constant_lpm / 60000,
            volume_flow_min=self.ground_flow_min_lpm / 60000,
            volume_flow_max=self.ground_flow_max_lpm / 60000,
            n_boreholes=self.n_boreholes,
            H_b=self.H_b,
            R_b=self.R_b,
            R_b_supplied=R_b is not None,
            pump_power=self.E_pmp,
            pump_efficiency=pump_efficiency,
            pump_map=dict(pump_map) if pump_map is not None else None,
            pipe_inner_diameter=pipe_inner_diameter if pipe_inner_diameter is not None else 2 * r_in,
            pipe_roughness=pipe_roughness,
            dp_common=dp_common,
            dp_aux_ref=dp_aux_ref,
            dp_aux_exponent=dp_aux_exponent,
            m_dot_ref_rated=self.m_dot_ref_rated,
            ua_fractions=(
                ground_hx_fluid_fraction,
                ground_hx_refrigerant_fraction,
                ground_hx_constant_fraction,
            ),
            ua_exponents=(ground_hx_fluid_exponent, ground_hx_refrigerant_exponent),
            boundary_condition=boundary_condition,
            geometry=dict(
                k_s=k_s,
                k_g=k_g,
                k_p=k_p,
                r_b=r_b,
                r_out=r_out,
                r_in=r_in,
                D_s=D_s,
                rho_f=rho_w,
                mu_f=mu_w,
                k_f=k_w,
            ),
        )
        self.ground_flow_control = self._ground_settings["control"]

        self.Ts: float = Ts
        self.Ts_K: float = cu.C2K(Ts)

        # --- 5. Room temperature ---
        self.T_a_room: float = T_a_room

        # --- Precompute g-function ---
        self.dt_s: float = dt_s
        self._gfunc_interp = precompute_gfunction(
            N_1=N_1,
            N_2=N_2,
            B=B,
            H_b=H_b,
            D_b=D_b,
            r_b=r_b,
            alpha_s=self.alp_s,
            k_s=k_s,
            t_max_s=t_max_s,
            dt_s=dt_s,
        )

        # --- Simulation state ---
        self.time: np.ndarray = np.array([])
        self.dt: float = dt_s
        self.T_bhe_f: float = Ts
        self.T_bhe: float = Ts
        self.T_bhe_f_in: float = Ts
        self.T_bhe_f_in_K: float = self.Ts_K
        self.T_bhe_f_out: float = Ts
        self.T_bhe_f_out_K: float = self.Ts_K
        self.Q_bhe: float = 0.0

    # =============================================================

    # =============================================================
    # Refrigerant cycle physics
    # =============================================================

    @discharge_controlled(reject=True)
    @reject_invalid_efficiency
    def _calc_state(
        self,
        dT_ref_evap: float,
        dT_ref_cond: float,
        Q_r_iu: float,
        T0: float,
        T_a_room: float,
        *,
        ground_flow_ratio: float = 1.0,
        source_temperature_K: float | None = None,
        speed_clamp_is_failure: bool | None = None,
    ) -> dict | None:
        """Evaluate refrigerant cycle at a given operating point.

        Parameters
        ----------
        dT_ref_evap, dT_ref_cond : float
            Approach ΔT [K].
        Q_r_iu : float
            Indoor thermal load [W]. >0 cooling, <0 heating, 0 off.
        T0 : float
            Dead-state / ambient temperature [°C].
        T_a_room : float
            Room air temperature [°C].
        source_temperature_K : float | None
            Ground-fluid temperature leaving the borefield [K]; the model state
            ``T_bhe_f_out_K`` when omitted.
        speed_clamp_is_failure : bool | None
            Whether a compressor speed clamp marks the state as failed. The
            default keeps the coupled-solver rule (failed when the coupled loop
            is active or a source temperature is given); the standard solver
            passes ``False`` and reports the clamp as ``capacity_clamped``.
        """
        self._last_pr_event = None
        loop = ground_flow_state(self._ground_settings, ground_flow_ratio)
        T_a_room_K = cu.C2K(T_a_room)
        T_bhe_f_out_K = float(getattr(self, "T_bhe_f_out_K", self.Ts_K))

        if source_temperature_K is not None:
            T_bhe_f_out_K = source_temperature_K

        is_active = Q_r_iu != 0.0
        m_dot_cp_b = loop["dV"] * rho_w * c_w

        if Q_r_iu < 0:
            # Heating: BHE = evaporator (absorb from ground), IU = condenser (heat room)
            mode = "heating"
            T_source_K = T_bhe_f_out_K + (loop["E_pmp"] / m_dot_cp_b)
            T_evap_sat_K = T_source_K - dT_ref_evap
            T_cond_sat_K = T_a_room_K + dT_ref_cond
            Q_ref_iu = abs(Q_r_iu)
        elif Q_r_iu > 0:
            # Cooling: IU = evaporator (cool room), BHE = condenser (reject to ground)
            mode = "cooling"
            T_source_K = T_bhe_f_out_K + (loop["E_pmp"] / m_dot_cp_b)
            T_evap_sat_K = T_a_room_K - dT_ref_evap
            T_cond_sat_K = T_source_K + dT_ref_cond
            Q_ref_iu = Q_r_iu
        else:
            mode = "off"
            T_evap_sat_K = self.Ts_K
            T_cond_sat_K = self.Ts_K
            Q_ref_iu = 0.0

        # Low-lift feasibility is enforced downstream by the compressor
        # pressure-ratio floor (PR_cycle_min); a separate fixed minimum lift is
        # redundant and non-transferable across refrigerants/operating levels.

        actual_dT_subcool: float = min(self.dT_subcool, max(0.0, dT_ref_cond - self.dT_hx_min))
        actual_dT_superheat: float = min(self.dT_superheat, max(0.0, dT_ref_evap - self.dT_hx_min))

        # Always mode="heating" for calc_ref_state (avoids key swap)
        cycle_states = calc_ref_state(
            T_evap_K=T_evap_sat_K,
            T_cond_K=T_cond_sat_K,
            refrigerant=self.ref,
            eta_cmp_isen=1.0,
            mode=mode,
            dT_superheat=actual_dT_superheat,
            x_suction=self._x_suction,
            dT_subcool=actual_dT_subcool,
            is_active=is_active,
        )

        # Compressor pressure-ratio envelope guard (PR = P_cond / P_evap), the
        # physically primary lift limit. Ceiling -> reject (outside the
        # single-stage envelope); floor -> clamp the cycle onto PR_cycle_min by
        # holding P_evap and projecting P_cond, then refresh the cycle state.
        self._last_pr_event = None
        if is_active:
            P_evap = cycle_states["P_ref_cmp_in [Pa]"]
            P_cond = cycle_states["P_ref_cmp_out [Pa]"]
            ratio_P_cmp = P_cond / P_evap if P_evap > 0 else 1.0
            pr_event = check_pr_envelope(ratio_P_cmp, self.PR_cycle_min, self.PR_cycle_max)
            if pr_event == "pr_above_max":
                self._last_pr_event = ("pr_above_max", ratio_P_cmp, self.PR_cycle_max)
                return None
            if pr_event == "pr_below_min":
                self._last_pr_event = ("pr_below_min", ratio_P_cmp, self.PR_cycle_min)
                import CoolProp.CoolProp as CP

                P_cond = self.PR_cycle_min * P_evap
                T_cond_sat_K = CP.PropsSI("T", "P", P_cond, "Q", 0, self.ref)
                # Projection changes the condenser's physical approach. Its
                # available subcooling must follow that projected temperature,
                # not the pre-projection trial approach used by the HX search.
                sink_K = cu.C2K(T_a_room) if mode == "heating" else T_source_K
                actual_dT_subcool = min(self.dT_subcool, max(0.0, T_cond_sat_K - sink_K - self.dT_hx_min))
                cycle_states = calc_ref_state(
                    T_evap_K=T_evap_sat_K,
                    T_cond_K=T_cond_sat_K,
                    refrigerant=self.ref,
                    eta_cmp_isen=1.0,
                    mode=mode,
                    dT_superheat=actual_dT_superheat,
                    x_suction=self._x_suction,
                    dT_subcool=actual_dT_subcool,
                    is_active=is_active,
                )

        if is_active:
            # Diagnostic temperatures follow the same cycle and caller-supplied
            # room temperature as the indoor HX, including pressure-ratio clamps.
            indoor_sat_K = T_cond_sat_K if mode == "heating" else T_evap_sat_K
            ground_sat_K = T_evap_sat_K if mode == "heating" else T_cond_sat_K
            self.T_r_iu = cu.K2C(indoor_sat_K)
            self.dT_r_iu = self.T_r_iu - T_a_room
            self.dT_r_ghx = ground_sat_K - T_bhe_f_out_K

        cmp_rps = 0.0
        converged_rps = True
        capacity_clamped = None
        val_eta_isen = val_eta_vol = val_eta_em = np.nan
        ratio_P_cmp = np.nan
        if is_active:
            # Unit isentropic state supplies pressure, suction density and work.
            # Each speed candidate evaluates the efficiencies before predicting duty.
            P_evap = cycle_states["P_ref_cmp_in [Pa]"]
            P_cond = cycle_states["P_ref_cmp_out [Pa]"]
            ratio_P_cmp = P_cond / P_evap
            rho_suction = cycle_states["rho_ref_cmp_in [kg/m3]"]
            h_in = cycle_states["h_ref_cmp_in [J/kg]"]
            dh_isen = cycle_states["h_ref_cmp_out [J/kg]"] - h_in
            h_liquid = cycle_states["h_ref_exp_in [J/kg]"]
            h_expansion = cycle_states["h_ref_exp_out [J/kg]"]

            def residual(rps: float) -> float:
                eta_vol = _eval_eff(self.eta_cmp_vol, ratio_P_cmp, rps)
                eta_isen = _eval_eff(self.eta_cmp_isen, ratio_P_cmp, rps)
                # Validate the drive model on every candidate as well. It acts
                # on input power only, not on the refrigerant heat duty.
                _eval_eff(self.eta_cmp, ratio_P_cmp, rps)
                m_dot = self.V_cmp_ref * rho_suction * eta_vol * rps
                h_out = h_in + dh_isen / eta_isen
                dh = h_in - h_expansion if mode == "cooling" else h_out - h_liquid
                return float(m_dot * dh - Q_ref_iu)

            cmp_rps, converged_rps, capacity_clamped = solve_compressor_speed(residual, self.rps_min, self.rps_max)
            val_eta_isen = _eval_eff(self.eta_cmp_isen, ratio_P_cmp, cmp_rps)
            val_eta_vol = _eval_eff(self.eta_cmp_vol, ratio_P_cmp, cmp_rps)
            val_eta_em = _eval_eff(self.eta_cmp, ratio_P_cmp, cmp_rps)
            cycle_states = calc_ref_state(
                T_evap_K=T_evap_sat_K,
                T_cond_K=T_cond_sat_K,
                refrigerant=self.ref,
                eta_cmp_isen=val_eta_isen,
                mode=mode,
                dT_superheat=actual_dT_superheat,
                x_suction=self._x_suction,
                dT_subcool=actual_dT_subcool,
                is_active=True,
            )
            m_dot_ref = self.V_cmp_ref * rho_suction * val_eta_vol * cmp_rps
        else:
            m_dot_ref = 0.0

        h_cmp_out = cycle_states["h_ref_cmp_out [J/kg]"]
        h_cmp_in = cycle_states["h_ref_cmp_in [J/kg]"]
        h_exp_in = cycle_states["h_ref_exp_in [J/kg]"]
        h_exp_out = cycle_states["h_ref_exp_out [J/kg]"]
        Q_ref_cond = m_dot_ref * (h_cmp_out - h_exp_in) if is_active else 0.0
        Q_ref_evap = m_dot_ref * (h_cmp_in - h_exp_out) if is_active else 0.0
        E_cmp_ref = m_dot_ref * (h_cmp_out - h_cmp_in) if is_active else 0.0
        E_cmp = E_cmp_ref / val_eta_em if is_active else 0.0
        E_cmp_loss = E_cmp - E_cmp_ref

        if is_active and E_cmp <= 0:
            return None

        # ── BHE energy balance ──
        if mode == "heating":
            Q_bhe = Q_ref_evap - loop["E_pmp"]
            T_bhe_f_in_K = T_source_K - Q_ref_evap / m_dot_cp_b
        elif mode == "cooling":
            Q_bhe = -(Q_ref_cond + loop["E_pmp"])  # negative = heat into ground
            T_bhe_f_in_K = T_source_K + Q_ref_cond / m_dot_cp_b
        else:
            Q_bhe = 0.0
            T_bhe_f_in_K = self.T_bhe_f_in_K

        Q_bhe_unit = calc_borefield_linear_load(Q_bhe, self.n_boreholes, self.H_b) if is_active else 0.0
        T_bhe_f = (cu.K2C(T_bhe_f_in_K) + cu.K2C(T_bhe_f_out_K)) / 2
        T_bhe = T_bhe_f + Q_bhe_unit * loop["R_b"]

        UA_ground_rated, UA_iu_rated = self._rated_hx_UAs(mode)
        # ── Indoor unit HX ──
        if mode == "cooling":
            iu_hx = calc_HX_perf_for_target_heat(
                Q_ref_target=Q_ref_evap,
                T_a_in_C=T_a_room,
                T_ref_sat_K=T_evap_sat_K,
                A_cross=self.A_cross_iu,
                UA_rated=UA_iu_rated,
                dV_fan_ref=self.dV_iu_fan_a_ref,
                dV_fan_min=self.dV_iu_fan_a_min,
                dV_fan_max=self.dV_iu_fan_a_max,
                custom_fan_curve=not is_generic_fan_curve(self.vsd_coeffs_iu),
                is_active=is_active,
            )
        elif mode == "heating":
            iu_hx = calc_HX_perf_for_target_heat(
                Q_ref_target=Q_ref_cond,
                T_a_in_C=T_a_room,
                T_ref_sat_K=T_cond_sat_K,
                A_cross=self.A_cross_iu,
                UA_rated=UA_iu_rated,
                dV_fan_ref=self.dV_iu_fan_a_ref,
                dV_fan_min=self.dV_iu_fan_a_min,
                dV_fan_max=self.dV_iu_fan_a_max,
                custom_fan_curve=not is_generic_fan_curve(self.vsd_coeffs_iu),
                is_active=is_active,
            )
        else:
            iu_hx = {
                "dV_fan": 0.0,
                "T_a_mid_C": T_a_room,
                "converged": True,
                "min_limit": False,
                "max_limit": False,
            }

        dV_iu_a = iu_hx["dV_fan"]
        T_iu_a_mid = iu_hx["T_a_mid_C"]
        E_iu_fan = calc_fan_power_from_dV_fan(
            dV_fan=dV_iu_a,
            fan_params=self.fan_params_iu,
            vsd_coeffs=self.vsd_coeffs_iu,
            is_active=is_active,
        )
        if not is_active:
            E_iu_fan = 0.0
        T_iu_a_out = T_iu_a_mid + E_iu_fan / (c_a * rho_a * dV_iu_a) if is_active and dV_iu_a > 0 else T_a_room
        v_iu_a = dV_iu_a / self.A_cross_iu if is_active else 0.0

        UA_ground_actual = ground_hx_UA(self._ground_settings, UA_ground_rated, ground_flow_ratio, m_dot_ref)
        Q_ground_available = 0.0
        # BHE NTU check (heating: evaporator constraint)
        if mode == "heating" and is_active:
            eps = calc_phase_change_hx_effectiveness(UA_ground_actual, loop["dV"] * rho_w, c_w)
            T_source_K_local = T_bhe_f_out_K + (loop["E_pmp"] / m_dot_cp_b)
            Q_evap_max = eps * m_dot_cp_b * (T_source_K_local - T_evap_sat_K)
            Q_ground_available = Q_evap_max
            err_Q_evap = Q_ref_evap - Q_evap_max
        elif mode == "cooling" and is_active:
            eps = calc_phase_change_hx_effectiveness(UA_ground_actual, loop["dV"] * rho_w, c_w)
            T_source_K_local = T_bhe_f_out_K + (loop["E_pmp"] / m_dot_cp_b)
            Q_cond_max = eps * m_dot_cp_b * (T_cond_sat_K - T_source_K_local)
            Q_ground_available = Q_cond_max
            err_Q_evap = Q_ref_cond - Q_cond_max
        else:
            err_Q_evap = 0.0

        # Total electrical input
        E_pmp_active = loop["E_pmp"] if is_active else 0.0
        E_tot = E_cmp + E_pmp_active + E_iu_fan
        # Air-stream energy uses the actual flow and final fan-heated outlet.
        # It does not replace the coil duty used by the compressor/load solver.
        G_iu_a = c_a * rho_a * dV_iu_a
        T0_K = cu.C2K(T0)
        Q_a_iu_in = calc_energy_flow(G_iu_a, T_a_room_K, T0_K)
        Q_a_iu_out = calc_energy_flow(G_iu_a, cu.C2K(T_iu_a_out), T0_K)
        Q_air = Q_a_iu_out - Q_a_iu_in
        heat_direction_valid = bool(
            not is_active or (dV_iu_a > 0 and (Q_air <= 0 if mode == "cooling" else Q_air >= 0))
        )

        result = reportable_state(cycle_states)
        result.update(
            {
                "hp_is_on": is_active,
                "mode": mode,
                "converged": bool(iu_hx.get("converged", True)),
                "Q_iu_HX_available [W]": iu_hx.get("Q_air", 0.0),
                "Q_iu_ref_required [W]": Q_ref_evap if mode == "cooling" else Q_ref_cond,
                "iu_hx_capacity_margin [W]": iu_hx.get("capacity_margin_W", 0.0),
                "iu_hx_min_flow_margin [W]": iu_hx.get("min_flow_capacity_margin_W", 0.0),
                "iu_hx_max_flow_margin [W]": iu_hx.get("max_flow_capacity_margin_W", 0.0),
                "converged_rps": converged_rps,
                "capacity_clamped": capacity_clamped,
                "iu_fan_flow_min_limit": iu_hx.get("min_limit", False),
                "iu_fan_flow_max_limit": iu_hx.get("max_limit", False),
                "err_Q_evap [W]": err_Q_evap,
                # Temperatures [°C]
                "T_iu_a_in [°C]": T_a_room,
                "T_iu_a_mid [°C]": T_iu_a_mid,
                "T_iu_a_out [°C]": T_iu_a_out,
                "T_a_room [°C]": T_a_room,
                "T0 [°C]": T0,
                "Ts [°C]": self.Ts,
                "T_bhe [°C]": T_bhe,
                "T_bhe_f [°C]": T_bhe_f,
                "T_bhe_f_in [°C]": cu.K2C(T_bhe_f_in_K),
                "T_bhe_f_out [°C]": cu.K2C(T_bhe_f_out_K),
                # Volume flow rates
                "dV_iu_a [m3/s]": dV_iu_a,
                "v_iu_a [m/s]": v_iu_a,
                "dV_bhe_f [m3/s]": loop["dV"] if is_active else 0.0,
                "m_dot_ref [kg/s]": m_dot_ref,
                "cmp_rpm [rpm]": cmp_rps * 60,
                "cmp_rps [rev/s]": cmp_rps,
                "n_star [-]": cmp_rps / self.rps_rated,
                "pressure_ratio": ratio_P_cmp,
                "pr_floor_active": self._last_pr_event is not None and self._last_pr_event[0] == "pr_below_min",
                # Energy rates [W]
                "E_iu_fan [W]": E_iu_fan,
                "E_pmp [W]": E_pmp_active,
                # Heat duties by physical location (mode-mapped): in heating the indoor
                # unit is the condenser and the ground loop the evaporator; in cooling
                # the roles swap. Reported by location so the labels are mode-independent
                # and the consumer never sees the cond/evap bookkeeping (the
                # refrigerant-perspective cond/evap remain only in the refrigerant-state
                # keys T/P/h/s_ref_*_sat and in refrigerant.py).
                "Q_ref_iu [W]": Q_ref_cond if mode == "heating" else Q_ref_evap,
                "Q_a_iu_in [W]": Q_a_iu_in,
                "Q_a_iu_out [W]": Q_a_iu_out,
                "indoor_heat_direction_valid": heat_direction_valid,
                "Q_ref_ground [W]": Q_ref_evap if mode == "heating" else Q_ref_cond,
                "Q_bhe [W]": Q_bhe,
                "E_cmp [W]": E_cmp,
                # Electro-mechanical losses are external to the refrigerant
                # cycle, as in the other TMHP compressor models. Condenser
                # duty includes refrigerant work, not those external losses.
                "E_cmp_ref [W]": E_cmp_ref,
                "E_cmp_loss [W]": E_cmp_loss,
                "eta_is [-]": val_eta_isen,
                "eta_v [-]": val_eta_vol,
                "eta_em [-]": val_eta_em,
                "UA_ground_rated [W/K]": UA_ground_rated,
                "UA_iu_rated [W/K]": UA_iu_rated,
                "E_tot [W]": E_tot,
                # Refrigerant COP uses coil duty; system COP uses final air delivery.
                "cop_ref [-]": (
                    (Q_ref_cond if mode == "heating" else Q_ref_evap) / E_cmp if (is_active and E_cmp > 0) else np.nan
                ),
                "cop_sys [-]": (abs(Q_air) / E_tot if (is_active and E_tot > 0 and heat_direction_valid) else np.nan),
            }
        )
        ground_result_diagnostics(
            result,
            self._ground_settings,
            loop,
            ground_flow_ratio,
            UA_ground_actual,
            Q_ground_available,
            result["Q_ref_ground [W]"],
        )
        if speed_clamp_is_failure is None:
            speed_clamp_is_failure = bool(self._ground_settings["active"] or source_temperature_K is not None)
        if speed_clamp_is_failure and is_active and (capacity_clamped is not None or not converged_rps):
            result["converged_rps"] = False
            result["failure_reason"] = "compressor_min_speed" if capacity_clamped == "min" else "compressor_max_speed"
        return result

    def _rating_side(self, role: str, mode: str, T_in_C: float) -> HXSide:
        """Ground loop is the source side, indoor coil the load side, in either mode."""
        UA_ground, UA_iu = self._rated_hx_UAs(mode)
        if role == "source":
            return HXSide("water", T_in_C, UA_ground, self.dV_b_f_m3s)
        return HXSide("air", T_in_C, UA_iu, self.dV_iu_fan_a_ref)

    def _rated_hx_UAs(self, mode: str) -> tuple[float, float]:
        """Return ground/IU physical UA; old explicit role inputs use an adapter.

        Defaults and physical inputs remain identical in cooling and heating.
        Only deprecated role-based inputs preserve the old mode swap.
        """
        ground, indoor = self.UA_ground_rated, self.UA_iu_rated
        if self._legacy_ground_ua:
            ground = self.UA_evap if mode == "heating" else self.UA_cond
        if self._legacy_indoor_ua:
            indoor = self.UA_cond if mode == "heating" else self.UA_evap
        return ground, indoor

    # =============================================================
    # 2D Optimisation
    # =============================================================

    def _solve_ground_flow_point(
        self,
        ratio: float,
        Q_r_iu: float,
        T0: float,
        T_a_room: float,
        wall_K: float | Callable[[float], float],
    ) -> dict:
        """At fixed flow, close the ground HX and optimize the indoor approach."""
        from scipy.optimize import brentq, minimize_scalar

        cache: dict[float, dict] = {}
        minimum_ground_cache: dict[float, dict | None] = {}

        def evaluate_ground(load_approach: float, ground_approach: float) -> dict | None:
            evap, cond = (ground_approach, load_approach) if Q_r_iu < 0 else (load_approach, ground_approach)
            return close_ground_temperature(
                lambda temperature: self._calc_state(
                    evap,
                    cond,
                    Q_r_iu,
                    T0,
                    T_a_room,
                    ground_flow_ratio=ratio,
                    source_temperature_K=temperature,
                ),
                wall_K,
                self.n_boreholes,
                self.H_b,
            )

        def evaluate_load_approach(load_approach: float) -> dict:
            if load_approach not in cache:
                cache[load_approach] = solve_ground_approach(
                    lambda ground: evaluate_ground(load_approach, ground),
                    "Q_ref_iu [W]",
                    abs(Q_r_iu),
                )
                if cache[load_approach].get("failure_reason") == "cycle_invalid" and self._last_pr_event is not None:
                    cache[load_approach]["failure_reason"] = (
                        self._last_pr_event[0]
                        if self._last_pr_event[0] in (DISCHARGE_TEMP_ABOVE_MAX, ETA_ISEN_BELOW_MIN)
                        else "pressure_ratio_limit"
                    )
            return cache[load_approach]

        def objective(x: float) -> float:
            row = evaluate_load_approach(float(x))
            return float(row["E_tot [W]"]) if row.get("converged", False) else 1e30

        grid = np.linspace(self.indoor_approach_min_K, self.indoor_approach_max_K, 7)
        powers = [objective(float(x)) for x in grid]

        # PR-floor projection makes the ground-HX duty residual flat in ground
        # approach, creating a narrow feasible boundary in indoor approach.
        # Resolve that physical equality explicitly: a bounded smooth search
        # can otherwise miss it and return a higher-power point just above PRmin.
        def minimum_ground_residual(load_approach: float) -> float:
            if load_approach not in minimum_ground_cache:
                minimum_ground_cache[load_approach] = evaluate_ground(load_approach, 1.0)
            row = minimum_ground_cache[load_approach]
            if row is None:
                raise ValueError("Invalid cycle at minimum ground approach")
            return float(row["Q_ref_required [W]"] - row["Q_HX_available [W]"])

        previous = None
        for x in grid:
            x = float(x)
            try:
                residual = minimum_ground_residual(x)
            except ValueError:
                previous = None
                continue
            if previous is not None and previous[1] * residual <= 0:
                with contextlib.suppress(ValueError, RuntimeError):
                    root = float(brentq(minimum_ground_residual, previous[0], x, xtol=1e-10))
                    row = minimum_ground_cache[root]
                    if row is not None and row.get("pr_floor_active", False):
                        objective(root)
            previous = (x, residual)
        # A clamped fan can satisfy both HX duties only on the airflow
        # boundary. Solve its signed capacity margin, avoiding the zero
        # plateau of the already solved indoor duty.
        for margin_key in ("iu_hx_min_flow_margin [W]", "iu_hx_max_flow_margin [W]"):
            previous_fan = None
            for x in grid:
                x = float(x)
                margin = evaluate_load_approach(x).get(margin_key, np.nan)
                if not np.isfinite(margin):
                    previous_fan = None
                    continue
                if previous_fan is not None and previous_fan[1] * margin < 0:
                    with contextlib.suppress(ValueError, RuntimeError):
                        root = float(
                            brentq(
                                lambda approach, key=margin_key: evaluate_load_approach(float(approach))[key],
                                previous_fan[0],
                                x,
                                xtol=1e-10,
                            )
                        )
                        objective(root)
                previous_fan = (x, margin)
        feasible = [i for i, power in enumerate(powers) if power < 1e30]
        if not feasible:
            boundary = [r for r in cache.values() if r.get("converged", False)]
            if boundary:
                return min(boundary, key=lambda r: r["E_tot [W]"])
            rows = list(cache.values())
            return next(
                (r for r in rows if r["failure_reason"] == "load_hx_capacity_insufficient"),
                next(
                    (r for r in rows if r["failure_reason"] == "ground_hx_capacity_insufficient"),
                    rows[0],
                ),
            )
        best = min(feasible, key=lambda i: powers[i])
        optimum = minimize_scalar(
            objective,
            bounds=(
                float(grid[max(0, best - 1)]),
                float(grid[min(len(grid) - 1, best + 1)]),
            ),
            method="bounded",
            options={"xatol": 1e-3, "maxiter": 30},
        )
        objective(float(optimum.x))
        return min(
            (r for r in cache.values() if r.get("converged", False)),
            key=lambda r: r["E_tot [W]"],
        )

    def _solve_ground_flow(
        self,
        Q_r_iu: float,
        T0: float,
        T_a_room: float,
        *,
        ground_flow_ratio: float | None = None,
        T_bhe_wall: float | None = None,
        wall_response: Callable[[float], float] | None = None,
    ) -> dict:
        if Q_r_iu == 0:
            result = self._calc_state(5, 5, 0, T0, T_a_room)
            assert result is not None
            result.update({"failure_reason": "none", "hx_feasible": True})
            result["E_iu_fan [W]"] = result["E_tot [W]"] = 0.0
            return result
        wall_K = (
            wall_response if wall_response is not None else cu.C2K(self.T_bhe if T_bhe_wall is None else T_bhe_wall)
        )
        selected = select_ground_flow(
            lambda ratio: self._solve_ground_flow_point(ratio, Q_r_iu, T0, T_a_room, wall_K),
            self._ground_settings,
            ground_flow_ratio,
        )
        if "h_ref_cmp_in [J/kg]" not in selected:
            off = self._calc_state(5, 5, 0, T0, T_a_room) or {}
            off.update(selected)
            selected = off
        return selected

    # =============================================================
    # Standard (fixed ground flow) operating-point solver
    # =============================================================

    #: Ground-side approach search range [K]; the coupled solver uses the same.
    _GROUND_APPROACH_BOUNDS: tuple[float, float] = (1.0, 20.0)
    #: Coarse indoor-approach samples. They only locate the feasible band; its
    #: edges are then solved from the fan capacity margins, so a band narrower
    #: than the spacing (narrow or fixed fan range) is not missed.
    _INDOOR_GRID_POINTS = 7
    #: Root tolerance for band edges and the inward step taken off them [K].
    _BAND_EDGE_XTOL_K = 1e-7
    _BAND_EDGE_STEP_K = 1e-6
    #: Resolution of the edge between ground-balanced and unbalanced approaches [K].
    _BALANCED_EDGE_TOL_K = 1e-3
    _GROUND_CLOSURE_MAX_ITER = 24
    #: Width [K] to which the approach at the compressor envelope edge is resolved.
    _ENVELOPE_APPROACH_TOL_K = 1e-2
    #: Ground-fluid temperature closure tolerance [K] (as the coupled solver).
    _FLUID_TEMPERATURE_TOL_K = 1e-5
    #: Ground-side closures that describe a physical operating state. Spare
    #: ground-HX capacity is accepted in two places only: at the smallest
    #: approach searched, and in cooling at the compressor pressure-ratio
    #: floor, where the condensing pressure is held up (head-pressure control:
    #: the condenser runs partly flooded) instead of following the ground loop.
    _BALANCED = frozenset({"equality", "slack_bound", "slack_floor"})

    def _ground_conductance(self, mode: str) -> tuple[dict, float]:
        """Loop state and ε·ṁ·c_p of the ground HX at the fixed reference flow [W/K]."""
        loop = ground_flow_state(self._ground_settings, 1.0)
        m_dot = loop["dV"] * rho_w
        UA_ground, _ = self._rated_hx_UAs(mode)
        return loop, float(calc_phase_change_hx_effectiveness(UA_ground, m_dot, c_w) * m_dot * c_w)

    def _close_ground_side(
        self,
        load_approach: float,
        Q_r_iu: float,
        T0: float,
        T_a_room: float,
        wall_K: WallTemperature | None,
        warm: dict,
    ) -> tuple[str, dict | None, float, float]:
        """Close the ground-side heat balance at one indoor approach.

        With a fixed loop flow and heat-exchanger UA the ground-side saturation
        temperature is not a free control: the evaporator (heating) or the
        condenser (cooling) settles where the refrigerant duty equals the duty
        the ground HX can carry, ``ε·ṁc_p·ΔT``. Treating it as an independent
        search variable with an inequality (the former solver) let the optimiser
        rest on any point with spare HX capacity. Here a Newton/secant iteration
        on the approach closes the equality.

        When ``wall_K`` is given, the fluid temperature leaving the borefield is
        closed at the same time from the borehole relation used by the coupled
        solver, ``T_out = T_wall - q' R_b* + Q_bhe / (2 ṁ c_p)``
        (:func:`calc_bhe_fluid_temperatures`). ``wall_K`` may be a function of
        the borefield load ``Q_bhe`` [W]: a dynamic step passes the wall
        response to the step's own load, so the fluid temperature of the row
        belongs to the same step as its cycle (#96 G1). Otherwise the fluid
        leaves the borefield at the model state ``T_bhe_f_out_K``.

        Returns ``(status, row, ground_approach, T_out_K)``. ``status`` is
        ``"equality"`` (duty balance closed), ``"slack_bound"`` (spare ground HX
        capacity already at the smallest approach), ``"slack_floor"`` (cooling
        at the pressure-ratio floor: the condensing pressure is held at the
        floor and the condenser has spare capacity, see :attr:`_BALANCED`),
        ``"ground_limit"`` (duty
        exceeds the ground HX at the largest approach), ``"invalid"`` or
        ``"not_closed"``.
        """
        heating = Q_r_iu < 0
        lo, hi = self._GROUND_APPROACH_BOUNDS
        loop, conductance = self._ground_conductance("heating" if heating else "cooling")
        # Ground duty is the indoor duty minus (heating) or plus (cooling) the
        # compressor work; a COP near 4 is a starting guess only.
        g = float(warm.get("g", abs(Q_r_iu) * (0.75 if heating else 1.25) / conductance))
        g = min(max(g, lo), hi)
        if "T_out" in warm:
            T_out = float(warm["T_out"])
        elif wall_K is None:
            T_out = float(self.T_bhe_f_out_K)
        else:
            # Start the fluid closure from the guessed borefield load, not from
            # the wall temperature itself.
            q_ground = g * conductance
            Q_bhe_guess = q_ground - loop["E_pmp"] if heating else -(q_ground + loop["E_pmp"])
            T_out = calc_bhe_fluid_temperatures(
                wall_K(Q_bhe_guess) if callable(wall_K) else wall_K,
                Q_bhe_guess,
                self.n_boreholes,
                self.H_b,
                loop["R_b"],
                loop["dV"] * rho_w,
                c_w,
            )[2]
        # The compressor envelope (pressure-ratio ceiling) limits the absolute
        # ground-side saturation temperature, not the approach: as the fluid
        # temperature closes, the approach at that limit moves with it.
        pump_rise = float(loop["E_pmp"]) / (float(loop["dV"]) * rho_w * c_w)

        def saturation(approach: float, fluid: float) -> float:
            source = fluid + pump_rise
            return source - approach if heating else source + approach

        def approach_at(sat_K: float, fluid: float) -> float:
            source = fluid + pump_rise
            return source - sat_K if heating else sat_K - source

        previous: tuple[float, float] | None = None
        # Previous (fluid temperature, closure error) pair for the secant step.
        previous_fluid: tuple[float, float] | None = None
        # Last valid evaluation (approach, residual, row, fluid temperature) and
        # the nearest invalid saturation temperature seen (outside the envelope).
        last: tuple[float, float, dict, float] | None = None
        bad_sat: float | None = None
        row: dict | None = None
        for _ in range(self._GROUND_CLOSURE_MAX_ITER):
            evap, cond = (g, load_approach) if heating else (load_approach, g)
            try:
                row = self._calc_state(
                    evap,
                    cond,
                    Q_r_iu,
                    T0,
                    T_a_room,
                    source_temperature_K=T_out,
                    speed_clamp_is_failure=False,
                )
            except (ValueError, OverflowError, ZeroDivisionError):
                row = None
            if row is None:
                # A larger approach raises the lift into the pressure-ratio
                # ceiling: retry at the smallest approach, or bisect between the
                # last valid saturation temperature and this one.
                sat = saturation(g, T_out)
                if bad_sat is not None:
                    # Keep the invalid temperature closest to the valid side.
                    sat = max(bad_sat, sat) if heating else min(bad_sat, sat)
                bad_sat = sat
                if last is None:
                    if g <= lo + 1e-9:
                        return "invalid", None, g, T_out
                    g = lo
                    continue
                last_sat = saturation(last[0], last[3])
                if abs(last_sat - bad_sat) < self._ENVELOPE_APPROACH_TOL_K:
                    if last[1] > 0:
                        # The duty needs more ground approach than the compressor
                        # envelope allows: a ground-side capacity limit.
                        return "ground_limit", last[2], last[0], last[3]
                    return "invalid", None, g, T_out
                g = max(lo, approach_at(0.5 * (last_sat + bad_sat), T_out))
                continue
            residual = float(row["err_Q_evap [W]"])
            tolerance = max(0.01, 1e-5 * abs(float(row["Q_ref_ground [W]"])))
            if wall_K is None:
                T_next = T_out
            else:
                _, _, T_next = calc_bhe_fluid_temperatures(
                    wall_K(float(row["Q_bhe [W]"])) if callable(wall_K) else wall_K,
                    float(row["Q_bhe [W]"]),
                    self.n_boreholes,
                    self.H_b,
                    float(row["R_b_eff [mK/W]"]),
                    float(row["dV_bhe_f [m3/s]"]) * rho_w,
                    c_w,
                )
            closed = abs(T_next - T_out) <= self._FLUID_TEMPERATURE_TOL_K
            upper = hi if bad_sat is None else min(hi, approach_at(bad_sat, T_out))
            if closed:
                if abs(residual) <= tolerance:
                    return "equality", row, g, T_out
                if residual < 0 and g <= lo + 1e-12:
                    return "slack_bound", row, g, T_out
                if residual < 0 and not heating and row.get("pr_floor_active", False):
                    return "slack_floor", row, g, T_out
                if residual > 0 and (g >= hi - 1e-12 or upper - g < self._ENVELOPE_APPROACH_TOL_K):
                    return "ground_limit", row, g, T_out
            # At the pressure-ratio floor (cooling) the condensing temperature is
            # held above the trial approach, so the residual belongs to that
            # held approach and does not move with ``g``. Stepping from the
            # trial value would creep by residual / conductance per iteration
            # and never leave the floor.
            g_eval = g
            if not heating and row.get("pr_floor_active", False):
                g_eval = max(g, approach_at(cu.C2K(float(row["T_ref_cond_sat_l [°C]"])), T_out))
            # dR/dg = dQ/dg - ε·ṁc_p; the duty term is small, so the conductance
            # is a safe first slope and a secant estimate refines it.
            slope = conductance
            if previous is not None and abs(g_eval - previous[0]) > 1e-9:
                estimate = (previous[1] - residual) / (g_eval - previous[0])
                if estimate > 0.05 * conductance:
                    slope = estimate
            previous, last = (g_eval, residual), (g_eval, residual, row, T_out)
            # Fluid closure: plain substitution oscillates when the duty rises
            # steeply with the source temperature (loop gain near -0.5 at the
            # compressor speed ceiling); a secant step on the closure error
            # removes that, as in the coupled solver's temperature closure.
            error = T_next - T_out
            step = error
            if previous_fluid is not None and abs(error - previous_fluid[1]) > 1e-12:
                secant = -error * (T_out - previous_fluid[0]) / (error - previous_fluid[1])
                if math.isfinite(secant) and abs(secant) < 30.0:
                    step = secant
            previous_fluid = (T_out, error)
            T_out = T_out + step
            # Never step onto an approach already known to leave the envelope.
            upper = hi if bad_sat is None else min(hi, approach_at(bad_sat, T_out))
            target = g_eval + residual / slope
            if target >= upper:
                target = 0.5 * (g_eval + upper) if upper < hi else hi
            g = max(target, lo)
        return "not_closed", row, g, T_out

    def _solve_operating_point(
        self,
        Q_r_iu: float,
        T0: float,
        T_a_room: float,
        *,
        wall_K: WallTemperature | None = None,
    ) -> dict:
        """Operating point at a fixed ground flow, or an explicit failure.

        The indoor approach is the free control (the fan modulates); the
        ground-side approach follows from the ground HX balance
        (:meth:`_close_ground_side`). The indoor approaches the fan can serve
        form one band, found from the signed coil capacity margins at the fan
        flow limits (:meth:`_search_indoor`), and candidates in it are ranked
        on the three-band objective of
        :func:`~tmhp._opt_utils.operating_point_objective`: specific energy
        below the capacity ceiling (raw power would pick the starved coil at
        the compressor's minimum speed), largest delivery at ``rps_max``.

        A request above what the unit can deliver returns the delivered ceiling
        with ``capacity_clamped == "max"``. A point that cannot be solved
        returns the off state with ``converged=False`` and the reason: no heat,
        no power, NaN COP.
        """
        search = _IndoorSearch(self, Q_r_iu, T0, T_a_room, wall_K)
        self._search_indoor(search)
        rows = search.rows
        value, _, row = search.best()
        if value < OBJ_INFEASIBLE and row is not None:
            result = dict(row)
            result.update({"converged": True, "failure_reason": "none", "Q_request [W]": Q_r_iu})
            if result.get("capacity_clamped") == CAPACITY_CLAMPED_MAX:
                result.update({"Q_max [W]": abs(float(result["Q_ref_iu [W]"])), "capacity_binding": "compressor"})
            return result

        statuses = {status for _, status, _ in rows.values()}
        indoor_limited = any(
            r is not None and status in self._BALANCED and not r.get("converged", False)
            for _, status, r in rows.values()
        )
        # Only a coil that cannot carry the duty even at its largest airflow is
        # a capacity limit; one that over-delivers at its smallest airflow at
        # every approach cannot turn down, and a smaller duty will not help.
        indoor_short = any(
            r is not None and status in self._BALANCED and float(r.get(_IU_MARGIN_AT_MAX_FLOW, 0.0)) < 0.0
            for _, status, r in rows.values()
        )
        if indoor_short or "ground_limit" in statuses:
            ceiling = self._capacity_limited_operating_point(Q_r_iu, T0, T_a_room, wall_K)
            if ceiling is not None:
                return ceiling
        if indoor_limited:
            reason = "hx_not_converged"
        elif "ground_limit" in statuses:
            reason = "ground_hx_capacity_insufficient"
        elif search.pr_ceiling_seen:
            reason = "pr_above_max"
        elif search.envelope_reason is not None:
            reason = search.envelope_reason
        else:
            reason = "cycle_invalid"
        return self._failed_operating_point(reason, Q_r_iu, T0, T_a_room)

    def _balanced_cell(self, search: _IndoorSearch, a: float, b: float) -> tuple[float, float] | None:
        """Shrink ``[a, b]`` to its ground-balanced part (one end must be balanced)."""
        good_a, good_b = search.balanced(a), search.balanced(b)
        if good_a and good_b:
            return a, b
        if not (good_a or good_b):
            return None
        good, bad = (a, b) if good_a else (b, a)
        while abs(bad - good) > self._BALANCED_EDGE_TOL_K:
            mid = 0.5 * (good + bad)
            if search.balanced(mid):
                good = mid
            else:
                bad = mid
        return (a, good) if good_a else (good, b)

    def _fan_band(self, search: _IndoorSearch, a: float, b: float) -> tuple[float, float] | None:
        """Indoor approaches in ``[a, b]`` at which the fan can carry the duty.

        At a larger approach the coil carries more heat at any airflow, so the
        signed capacity margin at the maximum fan flow rises from negative
        (cannot carry the duty) and the margin at the minimum flow rises
        through zero (cannot turn down far enough). The band between the two
        roots is solved, not sampled.
        """
        # Both margins rise with the approach: a band needs the minimum-flow
        # margin <= 0 at the left end and the maximum-flow margin >= 0 at the
        # right end. Check what the balanced ends already tell before paying
        # for the ground-balance edge.
        if search.balanced(a) and search.margin(a, _IU_MARGIN_AT_MIN_FLOW) > 0.0:
            return None
        if search.balanced(b) and search.margin(b, _IU_MARGIN_AT_MAX_FLOW) < 0.0:
            return None
        cell = self._balanced_cell(search, a, b)
        if cell is None:
            return None
        with contextlib.suppress(ValueError, RuntimeError):
            return margin_feasible_interval(
                lambda x: search.margin(x, _IU_MARGIN_AT_MAX_FLOW),
                lambda x: search.margin(x, _IU_MARGIN_AT_MIN_FLOW),
                cell[0],
                cell[1],
                xtol=self._BAND_EDGE_XTOL_K,
            )
        return None

    def _sample_band(
        self,
        search: _IndoorSearch,
        band: tuple[float, float],
        accept: Callable[[float], bool] | None,
    ) -> None:
        """Search a fan band: its inward-stepped edges, then the interior.

        Without ``accept`` the interior is minimised; with it the band is only
        probed (edges and middle) for an accepted point.
        """
        lo, hi = band
        step = self._BAND_EDGE_STEP_K
        check = accept if accept is not None else search.feasible
        if hi - lo <= 2.0 * step:
            # A single point: equal fan limits, or a band at solver resolution.
            for x in (0.5 * (lo + hi), lo, hi):
                if check(x):
                    return
            return
        lo, hi = lo + step, hi - step
        if accept is not None:
            for x in (lo, hi, 0.5 * (lo + hi)):
                if accept(x):
                    return
            return
        search(lo)
        search(hi)
        minimize_scalar(
            search,
            bounds=(lo, hi),
            method="bounded",
            options={"xatol": min(1e-2, (hi - lo) / 20.0), "maxiter": 25},
        )

    def _search_indoor(
        self,
        search: _IndoorSearch,
        *,
        seed: float | None = None,
        accept: Callable[[float], bool] | None = None,
    ) -> None:
        """Locate the feasible indoor-approach band and search it.

        Coarse samples locate the band. Its edges come from the fan capacity
        margins (:meth:`_fan_band`), so an empty sample set is never taken as
        proof that no operating point exists. Without ``accept`` the band
        around the best sample is searched for the least objective; with it
        the search stops at the first accepted point.
        """
        lo_i, hi_i = self.indoor_approach_min_K, self.indoor_approach_max_K
        if accept is not None and seed is not None and accept(seed):
            return
        for x in np.linspace(lo_i, hi_i, self._INDOOR_GRID_POINTS):
            if accept is not None and accept(float(x)):
                return
            search(float(x))
        samples = sorted(x for x in search.rows if lo_i <= x <= hi_i)
        feasible = [x for x in samples if search.feasible(x)]
        if feasible:
            best = min(feasible, key=search)
            k = samples.index(best)
            left = samples[k - 1] if k > 0 else best
            right = samples[k + 1] if k + 1 < len(samples) else best
            lo: float = left
            hi: float = right
            if not search.feasible(left):
                band = self._fan_band(search, left, best)
                lo = band[0] if band is not None else best
            if not search.feasible(right):
                band = self._fan_band(search, best, right)
                hi = band[1] if band is not None else best
            if hi > lo:
                self._sample_band(search, (lo, hi), accept)
            if accept is None:
                return
        for a, b in zip(samples, samples[1:], strict=False):
            if accept is None and (search.feasible(a) or search.feasible(b)):
                continue
            band = self._fan_band(search, a, b)
            if band is None:
                continue
            self._sample_band(search, band, accept)
            if accept is None and search.best()[0] < OBJ_INFEASIBLE:
                return
            if accept is not None and any(accept(x) for x in search.rows if band[0] <= x <= band[1]):
                return

    def _duty_deliverable(
        self,
        Q_r_iu: float,
        T0: float,
        T_a_room: float,
        wall_K: WallTemperature | None,
        seed: float | None,
        warm: dict[str, float] | None = None,
    ) -> tuple[float | None, dict | None]:
        """An indoor approach at which this duty is met exactly, if one exists."""
        search = _IndoorSearch(self, Q_r_iu, T0, T_a_room, wall_K, warm)

        def exact(load_approach: float) -> bool:
            value, _, row = search.entry(load_approach)
            return (
                value < OBJ_INFEASIBLE
                and row is not None
                and row.get("capacity_clamped") is None
                and abs(float(row["Q_ref_iu [W]"]) - abs(Q_r_iu)) <= 1e-6 * abs(Q_r_iu)
            )

        self._search_indoor(search, seed=seed, accept=exact)
        for x in search.rows:
            if exact(x):
                return x, search.entry(x)[2]
        return None, None

    @staticmethod
    def _warm_from(row: dict | None) -> dict[str, float]:
        """Ground-closure warm start taken from a solved row."""
        if row is None:
            return {}
        return {"g": float(row["ground_approach [K]"]), "T_out": cu.C2K(float(row["T_bhe_f_out [°C]"]))}

    def _capacity_limited_operating_point(
        self,
        Q_r_iu: float,
        T0: float,
        T_a_room: float,
        wall_K: WallTemperature | None,
        rel_tol: float = 5e-3,
    ) -> dict | None:
        """Largest deliverable duty below the request, reported as a ceiling.

        A ladder of trial duties brackets the ceiling and a bisection closes on
        it. Each duty is tested by the fan-band search (:meth:`_search_indoor`),
        so a narrow fan range does not turn into an artificial ceiling. Returns
        ``None`` when no duty below the request can be delivered exactly.
        """
        sign = -1.0 if Q_r_iu < 0 else 1.0
        request = abs(Q_r_iu)
        rungs = [
            f * self.hp_capacity
            for f in (0.1, 0.2, 0.35, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0)
            if f * self.hp_capacity < request
        ]
        lo_q, lo_x, lo_row = 0.0, None, None
        hi_q = request
        for q in rungs:
            x, row = self._duty_deliverable(sign * q, T0, T_a_room, wall_K, lo_x, self._warm_from(lo_row))
            if row is not None:
                lo_q, lo_x, lo_row = q, x, row
            elif lo_row is not None:
                hi_q = q
                break
        if lo_row is None:
            return None
        while hi_q - lo_q > rel_tol * hi_q:
            mid = 0.5 * (lo_q + hi_q)
            x, row = self._duty_deliverable(sign * mid, T0, T_a_room, wall_K, lo_x, self._warm_from(lo_row))
            if row is not None:
                lo_q, lo_x, lo_row = mid, x, row
            else:
                hi_q = mid
        assert lo_x is not None
        # Just past the ceiling, did the compressor or a heat exchanger stop it?
        _, probe, _, _ = self._close_ground_side(lo_x, sign * min(request, 1.05 * lo_q), T0, T_a_room, wall_K, {})
        binding = (
            "compressor" if probe is not None and probe.get("capacity_clamped") == CAPACITY_CLAMPED_MAX else "cycle"
        )
        if binding == "cycle" and self.T_dis_max is not None:
            # Refused under the discharge limit, deliverable without it?
            limit, self.T_dis_max = self.T_dis_max, None
            try:
                x_free, _ = self._duty_deliverable(sign * hi_q, T0, T_a_room, wall_K, lo_x, self._warm_from(lo_row))
            finally:
                self.T_dis_max = limit
            if x_free is not None:
                binding = BINDING_DISCHARGE
        result = dict(lo_row)
        result.update(
            {
                "converged": True,
                "failure_reason": "none",
                "capacity_clamped": CAPACITY_CLAMPED_MAX,
                "Q_request [W]": Q_r_iu,
                "Q_max [W]": lo_q,
                "capacity_binding": binding,
            }
        )
        return result

    def _failed_operating_point(self, reason: str, Q_r_iu: float, T0: float, T_a_room: float) -> dict:
        """Off state carrying the failure: no heat, no power, NaN COP."""
        result = self._calc_state(5.0, 5.0, 0.0, T0, T_a_room)
        if result is None:
            result = {
                "hp_is_on": False,
                "mode": "off",
                "Q_ref_iu [W]": 0.0,
                "Q_ref_ground [W]": 0.0,
                "E_cmp [W]": 0.0,
                "E_tot [W]": 0.0,
                "cop_ref [-]": np.nan,
                "cop_sys [-]": np.nan,
                "T0 [°C]": T0,
                "T_a_room [°C]": T_a_room,
            }
        result.update({"converged": False, "failure_reason": reason, "Q_request [W]": Q_r_iu})
        return result

    # =============================================================
    # BHE g-function superposition
    # =============================================================

    # Step-mean responses of the run in progress, set by analyze_dynamic:
    # (time grid, g-function, step length, ḡ for every lag in reverse order).
    _bhe_step_mean: tuple[np.ndarray, Any, float, np.ndarray] | None = None

    def _bhe_history_rise(self, n: int, time_arr: np.ndarray, pulses: np.ndarray, upto: int) -> float:
        """``Σ_{i<upto} p_i · ḡ(t_n − t_i)`` [K]: wall response at step ``n`` to the first ``upto`` pulses.

        Inside :meth:`analyze_dynamic` the step-mean response of every lag is
        tabulated once and this is one contiguous dot product (as in
        :class:`~tmhp.ground_coupling.AggregateGFunctionCoupler`). Called on
        its own, with another time grid, step or g-function, it evaluates the
        g-function for the recorded pulses.
        """
        if upto <= 0:
            return 0.0
        table = self._bhe_step_mean
        if table is not None and table[0] is time_arr and table[1] is self._gfunc_interp and table[2] == self.dt:
            rev = table[3]
            start = len(rev) - 1 - n  # ḡ[n-i] for i = 0..upto-1 is rev[start + i]
            return float(np.dot(pulses[:upto], rev[start : start + upto]))
        idx = np.flatnonzero(pulses[:upto])
        if len(idx) == 0:
            return 0.0
        return float(np.dot(pulses[idx], g_step_mean(self._gfunc_interp, time_arr[n] - time_arr[idx], self.dt)))

    def _compute_bhe_superposition(
        self,
        n: int,
        time_arr: np.ndarray,
        Q_bhe_unit_pulse: np.ndarray,
        Q_bhe_unit_old: float,
        hp_result: dict,
        hp_is_on: bool,
    ) -> float:
        """Temporal superposition for BHE — from GSHPB."""
        Q_bhe_unit = (
            calc_borefield_linear_load(hp_result.get("Q_bhe [W]", 0.0), self.n_boreholes, self.H_b) if hp_is_on else 0.0
        )

        if abs(Q_bhe_unit - Q_bhe_unit_old) > 1e-6:
            Q_bhe_unit_pulse[n] = Q_bhe_unit - Q_bhe_unit_old
            Q_bhe_unit_old = Q_bhe_unit

        # Step-mean response over [t_n, t_n + dt], own pulse included (#96 G4).
        dT_bhe = self._bhe_history_rise(n, time_arr, Q_bhe_unit_pulse, n + 1)

        self.T_bhe = self.Ts - dT_bhe
        T_bhe_K = cu.C2K(self.T_bhe)
        T_bhe_f_K = T_bhe_K - Q_bhe_unit * hp_result.get("R_b_eff [mK/W]", self.R_b)
        self.T_bhe_f = cu.K2C(T_bhe_f_K)
        self.Q_bhe = Q_bhe_unit * self.total_borehole_length
        m_cp_b = c_w * rho_w * hp_result.get("dV_bhe_f [m3/s]", self.dV_b_f_m3s)

        dT_half = float((self.Q_bhe / m_cp_b) / 2) if m_cp_b > 0 else 0.0
        self.T_bhe_f_in_K = T_bhe_f_K - dT_half
        self.T_bhe_f_in = cu.K2C(self.T_bhe_f_in_K)
        self.T_bhe_f_out_K = T_bhe_f_K + dT_half
        self.T_bhe_f_out = cu.K2C(self.T_bhe_f_out_K)

        hp_result["T_bhe [°C]"] = self.T_bhe
        hp_result["T_bhe_f [°C]"] = self.T_bhe_f
        hp_result["T_bhe_f_in [°C]"] = self.T_bhe_f_in
        hp_result["T_bhe_f_out [°C]"] = self.T_bhe_f_out

        return Q_bhe_unit_old

    # =============================================================
    # Steady-state analysis
    # =============================================================

    @steady_envelope()
    def analyze_steady(
        self,
        Q_r_iu: float,
        T0: float,
        T_a_room: float | None = None,
        *,
        return_dict: bool = True,
        ground_flow_ratio: float | None = None,
        ground_flow_lpm: float | None = None,
        T_bhe_wall: float | None = None,
    ) -> dict | pd.DataFrame:
        """Run a steady-state performance snapshot.

        Parameters
        ----------
        Q_r_iu : float
            Indoor thermal load [W]. >0 cooling, <0 heating, 0 off.
        T0 : float
            Dead-state temperature [°C] (exergy reference only).
        T_a_room : float | None
            Room air temperature [°C]. Uses the constructor default if None.
        T_bhe_wall : float | None
            Borehole wall temperature [°C]. The fluid temperature leaving the
            borefield is then closed against it through ``R_b*`` and the loop
            flow, ``T_out = T_wall - q' R_b* + Q_bhe / (2 ṁ c_p)``, the same
            relation the coupled ground-flow solver uses. When omitted, the
            fluid leaves the borefield at the model state ``T_bhe_f_out``
            (the constructor ``Ts`` before any dynamic run).
        ground_flow_lpm, ground_flow_ratio : float | None
            Prescribed ground flow; selects the coupled ground-flow solver.

        Returns
        -------
        dict | pd.DataFrame
            Cycle state plus diagnostic flags. Notable keys:

            - ``"converged"`` (bool) — True only when an operating point was
              found that closes the indoor and ground heat balances.
            - ``"failure_reason"`` (str) — ``"none"`` or why no operating point
              exists: ``"hx_not_converged"`` (the indoor coil cannot carry the
              duty within its fan range), ``"ground_hx_capacity_insufficient"``,
              ``"pr_above_max"``, ``"discharge_temp_above_max"`` (no duty keeps
              the discharge temperature within ``T_dis_max``) or
              ``"cycle_invalid"`` (coupled solver: see
              :func:`~tmhp.ground_flow_control.failed_ground_point`).
            - ``"capacity_clamped"`` — ``"max"`` when the request exceeds the
              deliverable duty (``Q_ref_iu [W]`` is the ceiling, ``Q_max [W]``
              and ``capacity_binding`` say what bound it: ``"compressor"``,
              ``"cycle"`` or ``"discharge_temperature"``), ``"min"`` when the
              request is below the minimum-speed duty.

            Every non-``"none"`` reason returns the HP-off state: heat and
            power are 0 and the COP keys are NaN. Those numbers describe no
            operation and must not be used as performance.

        Raises
        ------
        ValueError
            For a non-finite load, temperature or flow input.
        """
        validate_steady(locals())
        if T_a_room is None:
            T_a_room = self.T_a_room
        _require_finite(
            Q_r_iu=Q_r_iu,
            T0=T0,
            T_a_room=T_a_room,
            T_bhe_wall=T_bhe_wall,
            ground_flow_ratio=ground_flow_ratio,
            ground_flow_lpm=ground_flow_lpm,
        )

        if ground_flow_ratio is not None and ground_flow_lpm is not None:
            raise ValueError("Supply ground_flow_lpm or deprecated ground_flow_ratio, not both")
        if ground_flow_lpm is not None:
            ground_flow_ratio = ground_flow_lpm / self.ground_flow_ref_lpm
        elif ground_flow_ratio is not None:
            warnings.warn(
                "ground_flow_ratio is deprecated; use ground_flow_lpm (ratio is always to reference).",
                DeprecationWarning,
                stacklevel=2,
            )
        if self._ground_settings["active"] or ground_flow_ratio is not None:
            result = self._solve_ground_flow(
                Q_r_iu,
                T0,
                T_a_room,
                ground_flow_ratio=ground_flow_ratio,
                T_bhe_wall=T_bhe_wall,
            )
            return result if return_dict else pd.DataFrame([result])

        if Q_r_iu == 0:
            result = self._calc_state(5.0, 5.0, 0.0, T0, T_a_room)
            if result is None:
                result = {
                    "hp_is_on": False,
                    "converged": False,
                    "failure_reason": "cycle_invalid",
                    "Q_ref_iu [W]": 0.0,
                    "Q_ref_ground [W]": 0.0,
                    "T0 [°C]": T0,
                    "T_a_room [°C]": T_a_room,
                }
            else:
                result["failure_reason"] = "none"
        else:
            wall_K = None if T_bhe_wall is None else cu.C2K(T_bhe_wall)
            result = self._solve_operating_point(Q_r_iu, T0, T_a_room, wall_K=wall_K)
            if result["failure_reason"] != "none":
                warnings.warn(
                    f"analyze_steady: no operating point; returning the HP-off state "
                    f"(reason={result['failure_reason']!r}, Q_r_iu={Q_r_iu:.0f}W, "
                    f"T0={T0:.1f}°C, T_a_room={T_a_room:.1f}°C). "
                    "Consider calibrating UA_rated or increasing the explicit fan-flow maximum.",
                    RuntimeWarning,
                    stacklevel=2,
                )
            if T_bhe_wall is not None:
                result["T_bhe_wall [°C]"] = T_bhe_wall

        if return_dict:
            return result
        return pd.DataFrame([result])

    # =============================================================
    # Dynamic simulation
    # =============================================================

    def analyze_dynamic(
        self,
        simulation_period_sec: int,
        dt_s: int,
        Q_r_iu_schedule,
        T0_schedule,
        T_a_room_schedule=None,
        result_save_csv_path: str | None = None,
    ) -> pd.DataFrame:
        """Time-stepping dynamic simulation with BHE superposition.

        Step ``n`` stands for the interval ``[t_n, t_n + dt]``. The borehole
        wall of the step is the step mean of its response to all loads so
        far, the step's own included, and the ground fluid is closed against
        that wall inside the step (``T_out = T_wall - q' R_b* + Q_bhe /
        (2 m c_p)``), so the evaporating temperature of a heating row is
        below the ``T_bhe_f_out [°C]`` of the same row (above it for the
        condensing temperature of a cooling row). Before this closure the
        cycle used the fluid temperature left by the previous step: the first
        step after an off period ran on undisturbed ground, which understated
        the compressor energy of intermittent operation by 4-7 % in heating
        and 11-21 % in cooling (20 min on / 40 min off and 1 h on / 2 h off
        at the default sizing; under 0.5 % in continuous operation).
        """
        validate_dynamic(locals())
        time = np.arange(0, simulation_period_sec, dt_s)
        tN = len(time)

        T0_schedule = np.array(T0_schedule, dtype=float)
        Q_r_iu_schedule = np.array(Q_r_iu_schedule, dtype=float)

        if len(T0_schedule) != tN:
            raise ValueError(f"T0_schedule length ({len(T0_schedule)}) != tN ({tN})")
        if len(Q_r_iu_schedule) != tN:
            raise ValueError(f"Q_r_iu_schedule length ({len(Q_r_iu_schedule)}) != tN ({tN})")

        if T_a_room_schedule is not None:
            T_a_room_arr = np.array(T_a_room_schedule, dtype=float)
        else:
            T_a_room_arr = np.full(tN, self.T_a_room)
        for name, values in (
            ("Q_r_iu_schedule", Q_r_iu_schedule),
            ("T0_schedule", T0_schedule),
            ("T_a_room_schedule", T_a_room_arr),
        ):
            if not np.all(np.isfinite(values)):
                raise ValueError(f"{name} must contain only finite values")

        self.time = time
        self.dt = dt_s

        # Reset BHE state
        self.T_bhe_f = self.Ts
        self.T_bhe = self.Ts
        self.T_bhe_f_in = self.Ts
        self.T_bhe_f_in_K = self.Ts_K
        self.T_bhe_f_out = self.Ts
        self.T_bhe_f_out_K = self.Ts_K
        self.Q_bhe = 0.0

        Q_bhe_unit_pulse = np.zeros(tN)
        Q_bhe_unit_old = 0.0
        # Step-mean response for every lag of the run, once (the grid is uniform):
        # each step's superposition is then a dot product, not a g-function evaluation per pulse.
        gbar = np.asarray(g_step_mean(self._gfunc_interp, time - time[0], dt_s), dtype=float) if tN else np.zeros(0)
        self._bhe_step_mean = (time, self._gfunc_interp, dt_s, np.ascontiguousarray(gbar[::-1]))
        g_own_step = float(gbar[0]) if tN else 0.0

        results_data: list[dict] = []

        for n in tqdm(range(tN), desc="GSHP Simulating"):
            t_s = time[n]
            hr = t_s * cu.s2h
            Q_r_iu_n = Q_r_iu_schedule[n]
            T0_n = T0_schedule[n]
            T_a_room_n = T_a_room_arr[n]

            hp_result: dict | None
            if Q_r_iu_n == 0 and not self._ground_settings["active"]:
                hp_result = self._calc_state(5.0, 5.0, 0.0, T0_n, T_a_room_n)
                if hp_result is not None:
                    hp_result["failure_reason"] = "none"
            else:
                # Borehole wall of this step as a function of the step's own
                # load: the response to the earlier pulses is fixed, and a
                # candidate load adds its own step-mean response. Evaluating
                # it never records a pulse.
                # The history does not depend on the candidate: one dot
                # product with the tabulated step-mean response per step.
                history_rise = self._bhe_history_rise(n, time, Q_bhe_unit_pulse, n)

                def wall_response(q_total, history=history_rise, previous_q=Q_bhe_unit_old):
                    rise = history
                    delta = q_total / self.total_borehole_length - previous_q
                    if abs(delta) > 1e-6:
                        rise += delta * g_own_step
                    return self.Ts_K - rise

                if self._ground_settings["active"]:
                    hp_result = self._solve_ground_flow(Q_r_iu_n, T0_n, T_a_room_n, wall_response=wall_response)
                else:
                    # The ground fluid is closed against that wall inside the
                    # step, so the cycle and the fluid temperature of one row
                    # belong to the same step (#96 G1). A failed step comes
                    # back as the off state with its reason.
                    hp_result = self._solve_operating_point(Q_r_iu_n, T0_n, T_a_room_n, wall_K=wall_response)

            if hp_result is None:
                # Off-mode cycle itself failed — fall back to an inert row so
                # downstream BHE superposition / DataFrame assembly don't see
                # a None.
                hp_result = {
                    "hp_is_on": False,
                    "converged": False,
                    "failure_reason": "cycle_invalid",
                    "Q_bhe [W]": 0.0,
                }

            assert hp_result is not None
            hp_is_on = bool(hp_result.get("hp_is_on", False))

            # BHE superposition
            Q_bhe_unit_old = self._compute_bhe_superposition(
                n,
                time,
                Q_bhe_unit_pulse,
                Q_bhe_unit_old,
                hp_result,
                hp_is_on,
            )

            hp_result["time [s]"] = t_s
            hp_result["time [h]"] = hr
            results_data.append(hp_result)

        self._bhe_step_mean = None
        results_df = pd.DataFrame(results_data)
        results_df = self.postprocess_exergy(results_df)
        if result_save_csv_path:
            results_df.to_csv(result_save_csv_path, index=False)
        return results_df

    # =============================================================
    # Exergy post-processing
    # =============================================================

    def postprocess_exergy(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute GSHP-specific exergy: 6 subsystems × (X_in, Xc, X_out)."""
        from .enex_functions import (
            calc_refrigerant_exergy,
            convert_electricity_to_exergy,
        )

        df = df.copy()
        if "T0 [°C]" not in df.columns:
            return df

        T0_K = cu.C2K(df["T0 [°C]"])

        # ── 1. Refrigerant exergy ──
        if "h_ref_cmp_in [J/kg]" not in df.columns:
            return df
        df = calc_refrigerant_exergy(df, self.ref, T0_K)

        # ── 2. Electricity = exergy ──
        df = convert_electricity_to_exergy(df)
        if "E_iu_fan [W]" in df.columns:
            df["X_iu_fan [W]"] = df["E_iu_fan [W]"]
        if "E_pmp [W]" in df.columns:
            df["X_pmp [W]"] = df["E_pmp [W]"]

        # ── 3a. Indoor unit air exergy ──
        if "dV_iu_a [m3/s]" in df.columns and "T_iu_a_in [°C]" in df.columns:
            G_a_iu = c_a * rho_a * df["dV_iu_a [m3/s]"].fillna(0)
            Tin_iu = cu.C2K(df["T_iu_a_in [°C]"])
            Tmid_iu = cu.C2K(df["T_iu_a_mid [°C]"])
            Tout_iu = cu.C2K(df["T_iu_a_out [°C]"]) if "T_iu_a_out [°C]" in df.columns else Tin_iu
            df["X_a_iu_in [W]"] = calc_exergy_flow(G_a_iu, Tin_iu, T0_K)
            df["X_a_iu_out [W]"] = calc_exergy_flow(G_a_iu, Tout_iu, T0_K)
            df["X_a_iu_mid [W]"] = calc_exergy_flow(G_a_iu, Tmid_iu, T0_K)

        # ── 3b. BHE fluid exergy ──
        if "dV_bhe_f [m3/s]" in df.columns and "T_bhe_f_in [°C]" in df.columns:
            G_b = c_w * rho_w * df["dV_bhe_f [m3/s]"].fillna(0)
            T_bhe_f_in_K = cu.C2K(df["T_bhe_f_in [°C]"])
            T_bhe_f_out_K = cu.C2K(df["T_bhe_f_out [°C]"])
            df["X_bhe_f_in [W]"] = calc_exergy_flow(G_b, T_bhe_f_in_K, T0_K)
            df["X_bhe_f_out [W]"] = calc_exergy_flow(G_b, T_bhe_f_out_K, T0_K)
            # Evaporator inlet = BHE outlet + pump work
            T_evap_in_K = T_bhe_f_out_K + df["E_pmp [W]"].fillna(0) / G_b.replace(0, np.nan)
            T_evap_in_K = T_evap_in_K.fillna(T_bhe_f_out_K)
            df["X_evap_in [W]"] = calc_exergy_flow(G_b, T_evap_in_K, T0_K)

        # ── 4. Carnot exergy (by physical location, mode-aware) ──
        # The Carnot factor for each location uses the saturation temperature of
        # the refrigerant role it plays: heating -> IU=condenser, ground=evaporator;
        # cooling -> roles swap. Output exergy is labelled by location
        # (X_ref_iu / X_ref_ground); refrigerant-state saturation keys stay cond/evap.
        if {"T_ref_cond_sat_v [°C]", "T_ref_evap_sat [°C]", "mode"} <= set(df.columns):
            is_heating = df["mode"] == "heating"
            T_iu_sat_K = cu.C2K(df["T_ref_cond_sat_v [°C]"].where(is_heating, df["T_ref_evap_sat [°C]"]))
            T_ground_sat_K = cu.C2K(df["T_ref_evap_sat [°C]"].where(is_heating, df["T_ref_cond_sat_v [°C]"]))
            df["X_ref_iu [W]"] = df["Q_ref_iu [W]"] * (1 - T0_K / T_iu_sat_K)
            df["X_ref_ground [W]"] = df["Q_ref_ground [W]"] * (1 - T0_K / T_ground_sat_K)

        # ── 5. Total exergy input ──
        X_tot = df["E_cmp [W]"] + df["E_pmp [W]"].fillna(0) + df["E_iu_fan [W]"].fillna(0)
        df["X_tot [W]"] = X_tot

        # ── 6. Component exergy destruction (X_in, Xc, X_out) ──
        X_a_iu_in = df.get("X_a_iu_in [W]", pd.Series(0.0, index=df.index)).fillna(0)
        X_a_iu_mid = df.get("X_a_iu_mid [W]", pd.Series(0.0, index=df.index)).fillna(0)
        X_a_iu_out = df.get("X_a_iu_out [W]", pd.Series(0.0, index=df.index)).fillna(0)
        X_bhe_f_in = df.get("X_bhe_f_in [W]", pd.Series(0.0, index=df.index)).fillna(0)
        X_bhe_f_out = df.get("X_bhe_f_out [W]", pd.Series(0.0, index=df.index)).fillna(0)
        X_evap_in = df.get("X_evap_in [W]", pd.Series(0.0, index=df.index)).fillna(0)

        if "X_cmp [W]" not in df.columns:
            return df

        is_heating = df["mode"] == "heating"
        is_cooling = df["mode"] == "cooling"

        # 6a. Compressor
        df["X_in_cmp [W]"] = df["X_cmp [W]"] + df["X_ref_cmp_in [W]"]
        df["X_out_cmp [W]"] = df["X_ref_cmp_out [W]"]
        df["Xc_cmp [W]"] = df["X_in_cmp [W]"] - df["X_out_cmp [W]"]

        # 6b. Expansion valve
        df["X_in_exp [W]"] = df["X_ref_exp_in [W]"]
        df["X_out_exp [W]"] = df["X_ref_exp_out [W]"]
        df["Xc_exp [W]"] = df["X_in_exp [W]"] - df["X_out_exp [W]"]

        # 6c. Indoor Unit HX (mode-aware)
        X_in_iu_hx = pd.Series(0.0, index=df.index)
        X_out_iu_hx = pd.Series(0.0, index=df.index)
        # Heating: IU = condenser
        X_in_iu_hx[is_heating] = df.loc[is_heating, "X_ref_cmp_out [W]"] + X_a_iu_in[is_heating]
        X_out_iu_hx[is_heating] = df.loc[is_heating, "X_ref_exp_in [W]"] + X_a_iu_mid[is_heating]
        # Cooling: IU = evaporator
        X_in_iu_hx[is_cooling] = df.loc[is_cooling, "X_ref_exp_out [W]"] + X_a_iu_in[is_cooling]
        X_out_iu_hx[is_cooling] = df.loc[is_cooling, "X_ref_cmp_in [W]"] + X_a_iu_mid[is_cooling]
        df["X_in_iu_hx [W]"] = X_in_iu_hx
        df["X_out_iu_hx [W]"] = X_out_iu_hx
        df["Xc_iu_hx [W]"] = X_in_iu_hx - X_out_iu_hx

        # 6d. BHE HX (mode-aware)
        X_in_bhe_hx = pd.Series(0.0, index=df.index)
        X_out_bhe_hx = pd.Series(0.0, index=df.index)
        # Heating: BHE = evaporator → ref(exp_out→cmp_in), fluid(evap_in→bhe_f_in)
        X_in_bhe_hx[is_heating] = df.loc[is_heating, "X_ref_exp_out [W]"] + X_evap_in[is_heating]
        X_out_bhe_hx[is_heating] = df.loc[is_heating, "X_ref_cmp_in [W]"] + X_bhe_f_in[is_heating]
        # Cooling: BHE = condenser → ref(cmp_out→exp_in), fluid(evap_in→bhe_f_in)
        X_in_bhe_hx[is_cooling] = df.loc[is_cooling, "X_ref_cmp_out [W]"] + X_evap_in[is_cooling]
        X_out_bhe_hx[is_cooling] = df.loc[is_cooling, "X_ref_exp_in [W]"] + X_bhe_f_in[is_cooling]
        df["X_in_bhe_hx [W]"] = X_in_bhe_hx
        df["X_out_bhe_hx [W]"] = X_out_bhe_hx
        df["Xc_bhe_hx [W]"] = X_in_bhe_hx - X_out_bhe_hx

        # 6e. Pump
        df["X_in_pmp [W]"] = df["X_pmp [W]"].fillna(0) + X_bhe_f_out
        df["X_out_pmp [W]"] = X_evap_in
        df["Xc_pmp [W]"] = df["X_in_pmp [W]"] - df["X_out_pmp [W]"]

        # 6f. Indoor fan
        df["X_in_iu_fan [W]"] = df["X_iu_fan [W]"].fillna(0) + X_a_iu_mid
        df["X_out_iu_fan [W]"] = X_a_iu_out
        df["Xc_iu_fan [W]"] = df["X_in_iu_fan [W]"] - df["X_out_iu_fan [W]"]

        # ── 7. Efficiencies ──
        df["X_eff_sys [-]"] = (X_a_iu_out - X_a_iu_in) / df["X_tot [W]"].replace(0, np.nan)
        df["X_eff_cmp [-]"] = 1 - df["Xc_cmp [W]"] / df["X_in_cmp [W]"].replace(0, np.nan)
        df["X_eff_exp [-]"] = 1 - df["Xc_exp [W]"] / df["X_in_exp [W]"].replace(0, np.nan)
        df["X_eff_iu_hx [-]"] = 1 - df["Xc_iu_hx [W]"] / df["X_in_iu_hx [W]"].replace(0, np.nan)
        df["X_eff_bhe_hx [-]"] = 1 - df["Xc_bhe_hx [W]"] / df["X_in_bhe_hx [W]"].replace(0, np.nan)
        df["X_eff_pmp [-]"] = 1 - df["Xc_pmp [W]"] / df["X_in_pmp [W]"].replace(0, np.nan)
        df["X_eff_iu_fan [-]"] = 1 - df["Xc_iu_fan [W]"] / df["X_in_iu_fan [W]"].replace(0, np.nan)

        return df
