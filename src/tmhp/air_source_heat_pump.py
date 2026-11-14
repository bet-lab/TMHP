"""Air source heat pump — physics-based cycle model with indoor unit.

Resolves a vapour-compression refrigerant cycle coupled to
an outdoor-air heat exchanger and an indoor-air heat exchanger.
Supports both **cooling** (``Q_r_iu > 0``) and **heating** (``Q_r_iu < 0``)
modes.  The indoor load ``Q_r_iu`` is imposed externally each timestep.

At each time step the model finds the minimum-power operating point
(compressor + indoor fan + outdoor fan) via bounded 2-D optimisation
over the evaporator and condenser approach temperature differences.

Architecture mirrors ``AirSourceHeatPumpBoiler`` — uses the same
shared utility functions (``calc_ref_state``, ``calc_HX_perf_for_target_heat``,
``calc_fan_power_from_dV_fan``) and the same ``postprocess_exergy()``
pattern, but replaces the tank energy balance with direct air-side
heat exchange at the indoor unit.
"""

import contextlib
import math
from collections.abc import Callable, Iterator, Mapping
from typing import Any, Literal

import numpy as np
import pandas as pd
from scipy.optimize import OptimizeResult, brentq, minimize, minimize_scalar
from tqdm import tqdm

from . import _opt_utils
from . import calc_util as cu
from ._boiler_results import fill_missing, report_condenser_dew_point
from ._opt_utils import efficiency_evaluator, specific_energy_objective
from ._validation import validate_ctor, validate_dynamic, validate_steady
from .air_properties import air_heat_capacity_rate
from .compressor_efficiency import reject_invalid_efficiency
from .compressor_envelope import (
    BINDING_CONDENSING,
    BINDING_CYCLE,
    BINDING_DISCHARGE,
    BINDING_EFFICIENCY,
    BINDING_PRESSURE_RATIO,
    CONDENSING_TEMP_ABOVE_MAX,
    CYCLE_KEY,
    DISCHARGE_TEMP_ABOVE_MAX,
    ETA_ISEN_BELOW_MIN,
    PR_ABOVE_MAX,
    PR_CYCLE_MAX_SINGLE_STAGE,
    RATED_SPEED_SEARCH_MAX,
    X_SUCTION_MIN_DEFAULT,
    check_pr_envelope,
    discharge_controlled,
    resolve_condensing_temp_max,
    resolve_discharge_temp_max,
    resolve_x_suction_min,
    steady_envelope,
)
from .compressor_speed import (
    CAPACITY_CLAMPED_MAX,
    CAPACITY_CLAMPED_MIN,
    default_displacement,
    solve_compressor_speed,
)
from .cycling import (
    CD_AIR_DEFAULT,
    CONTINUOUS,
    MIN_CAPACITY_KEY,
    OFF,
    CyclingDegradation,
    apply_cycling,
    continuous_keys,
    resolve_cycling,
)
from .defrost import (
    NO_FROST,
    DefrostModel,
    FrostState,
    apply_defrost,
    frost_free_keys,
    resolve_defrost,
)
from .enex_functions import (
    calc_energy_flow,
    calc_fan_power_from_dV_fan,
    calc_HX_perf_for_target_heat,
)
from .heat_exchanger import AirCoilSide, resolve_fan_flow_limits
from .hx_fan import INDOOR_AIRFLOW_PER_W, SINGLE_ZONE_VAV_COEFFICIENTS, is_generic_fan_curve
from .operating_limits import dynamic_notice, note_refusal, steady_notice
from .reference_state import HXSide, RatingCondition, ReferenceStateMixin
from .refrigerant import (
    _props,
    calc_ref_state,
    calc_ref_state_without_discharge,
    props_si,
    reportable_state,
    with_discharge_state,
)

# Objective bands for the operating-point search in ``_optimize_operation``.
#
# The search ranks candidate operating points lexicographically, best first:
#
#   1. points that meet the requested duty  -> ranked by total power [W],
#      which is O(1e2..1e5) and therefore always below the sentinels below;
#   2. points that are capacity limited     -> ranked by how much of the duty
#      they do deliver, so that a capacity-limited result is recognised as
#      one and never traded for a cheaper part-load point. The ceiling itself
#      is not read off this ranking: it is solved (`max_capacity`), because
#      the largest delivered heat is not where a unit runs flat out;
#   3. points the cycle cannot reach at all -> rejected.
#
# The bands must stay ordered and far enough apart that no power figure or
# shortfall can cross from one band into the next.

#: Objective floor for a capacity-limited operating point (valid operation).
OBJ_CAPACITY_LIMITED = 1.0e9
#: Objective value for an operating point the cycle cannot reach (invalid).
OBJ_INFEASIBLE = 1.0e12

#: A fan whose largest flow is at most this multiple of its smallest is
#: searched by :class:`_NarrowFanSearch`. At a fixed approach temperature a
#: coil's capacity grows roughly as flow^0.7, so a flow ratio of 2 leaves a band
#: of approach temperatures about +/-25 % wide -- at a 3 K approach that is
#: narrower than the spacing of the finest lattice the grid search falls back
#: on (1.3 K), and a constant-speed fan leaves a single point.
NARROW_FAN_FLOW_RATIO = 2.0


class _NarrowFanSearch:
    """Operating-point search for a fan with little or no flow range.

    A coil whose fan can modulate carries a given duty over a band of approach
    temperatures: a small approach at the largest airflow, a large one at the
    smallest. With a constant-speed fan the band is one point, and the unit
    meets a duty only where the refrigerant side and that one airflow agree.
    A grid or a simplex over the two approach temperatures cannot land on a set
    of zero width, so the band is solved instead, from the signed capacity
    margins of the coil at its two flow limits
    (:func:`~tmhp._opt_utils.margin_feasible_interval`) -- the way the
    ground-source and the water-heating models treat their fans.

    The two approaches are nested. The coil with the narrower fan is the
    *inner* one: for every outer approach its own approach is solved as the
    middle of its band (a single point for a constant-speed fan). The other
    coil's approach is the *outer* one: its band is solved from that coil's
    margins at the inner solution, and candidates inside it are ranked on the
    same three-band objective as the grid search
    (:func:`~tmhp._opt_utils.operating_point_objective`).

    With ``accept`` the search stops at the first candidate state it accepts
    and keeps it in ``accepted`` (for a caller that asks only whether some
    approach pair meets a duty exactly).
    """

    class _Accepted(Exception):
        """Raised from :meth:`entry` to leave the root finders at an accepted state."""

    #: Root tolerance on a band edge [K]. A constant-speed coil is converged
    #: only within 1e-5 of its duty, i.e. a few 1e-5 K of approach.
    _EDGE_XTOL_K = 1e-7
    #: Root tolerance on the outer band of a fan that can still modulate [K]:
    #: the interior is minimised afterwards, the edge only bounds it.
    _OUTER_BAND_XTOL_K = 1e-3
    #: Inward step from a solved band edge before the interior is minimised [K].
    _EDGE_STEP_K = 1e-4

    def __init__(
        self,
        model: "AirSourceHeatPump",
        Q_r_iu: float,
        T0: float,
        T_a_room: float,
        accept: Callable[[dict | None], bool] | None = None,
    ) -> None:
        self.model = model
        self.Q_r_iu, self.T0, self.T_a_room = Q_r_iu, T0, T_a_room
        self.accept = accept
        #: ``((dT_ref_evap, dT_ref_cond), state)`` of the accepted candidate.
        self.accepted: tuple[tuple[float, float], dict] | None = None
        self.heating = Q_r_iu < 0
        ratio_iu, ratio_ou = model._fan_flow_ratios()
        #: The narrower fan is solved, the other searched.
        self.inner_is_iu = ratio_iu <= ratio_ou
        self.inner_fixed = min(ratio_iu, ratio_ou) == 1.0
        self.outer_fixed = max(ratio_iu, ratio_ou) == 1.0
        #: (indoor approach, outdoor approach) -> (objective, state, coil margins)
        self.rows: dict[tuple[float, float], tuple[float, dict | None, tuple[float, float, float, float] | None]] = {}
        self._inner_points: dict[float, float | None] = {}
        self._inner_hint: float | None = None

    # -- candidates ---------------------------------------------------------

    def approaches(self, x_iu: float, x_ou: float) -> tuple[float, float]:
        """``(dT_ref_evap, dT_ref_cond)`` for an indoor/outdoor approach pair."""
        return (x_ou, x_iu) if self.heating else (x_iu, x_ou)

    def entry(self, x_iu: float, x_ou: float) -> tuple[float, dict | None, tuple[float, float, float, float] | None]:
        key = (float(x_iu), float(x_ou))
        if key not in self.rows:
            model = self.model
            de, dc = self.approaches(*key)
            perf = model._calc_state(
                dT_ref_evap=de, dT_ref_cond=dc, Q_r_iu=self.Q_r_iu, T0=self.T0, T_a_room=self.T_a_room
            )
            value = OBJ_INFEASIBLE
            if perf is not None and perf.get("converged", False):
                value = _opt_utils.operating_point_objective(
                    float(perf.get("E_tot [W]", math.nan)),
                    float(perf.get("Q_ref_iu [W]", math.nan)),
                    self.Q_r_iu,
                    perf.get("capacity_clamped"),
                ) * model._cycling_cost_factor(perf, self.Q_r_iu)
            self.rows[key] = (value, perf, model._coil_margins(perf))
            if self.accept is not None and perf is not None and self.accept(perf):
                self.accepted = (self.approaches(*key), perf)
                raise self._Accepted
        return self.rows[key]

    def _pair(self, outer: float, inner: float) -> tuple[float, float]:
        return (outer, inner) if not self.inner_is_iu else (inner, outer)

    def _side_margins(self, outer: float, inner: float, indoor: bool) -> tuple[float, float] | None:
        """(margin at the largest flow, margin at the smallest flow) of one coil [W]."""
        margins = self.entry(*self._pair(outer, inner))[2]
        if margins is None:
            return None
        iu_min, iu_max, ou_min, ou_max = margins
        return (iu_max, iu_min) if indoor else (ou_max, ou_min)

    # -- inner coil: solved -------------------------------------------------

    def inner(self, outer: float) -> float | None:
        """Inner approach at which the inner fan carries its duty, or ``None``."""
        outer = float(outer)
        if outer not in self._inner_points:
            lo, hi = self.model.dT_approach_bounds
            band = _opt_utils.margin_band_on_valid_range(
                lambda x: self._side_margins(outer, x, self.inner_is_iu),
                lo,
                hi,
                xtol=self._EDGE_XTOL_K,
                hint=self._inner_hint,
            )
            point = None
            if band is not None:
                # The middle of the band: the one point of a constant-speed
                # fan, mid-range airflow of a fan that can still modulate.
                point = 0.5 * (band[0] + band[1])
                self._inner_hint = point
            self._inner_points[outer] = point
        return self._inner_points[outer]

    def _outer_margins(self, outer: float) -> tuple[float, float] | None:
        inner = self.inner(outer)
        if inner is None:
            return None
        return self._side_margins(outer, inner, not self.inner_is_iu)

    def value(self, outer: float) -> float:
        inner = self.inner(outer)
        if inner is None:
            return OBJ_INFEASIBLE
        return self.entry(*self._pair(float(outer), inner))[0]

    # -- outer coil: band, then minimum -------------------------------------

    def run(self) -> None:
        with contextlib.suppress(self._Accepted):
            self._run()

    def _run(self) -> None:
        lo, hi = self.model.dT_approach_bounds
        band = _opt_utils.margin_band_on_valid_range(
            self._outer_margins,
            lo,
            hi,
            xtol=self._EDGE_XTOL_K if self.outer_fixed else self._OUTER_BAND_XTOL_K,
        )
        if band is None:
            return
        a, b = band
        step = self._EDGE_STEP_K
        if self.outer_fixed or b - a <= 2.0 * step:
            for x in (0.5 * (a + b), a, b):
                if self.value(x) < OBJ_INFEASIBLE:
                    return
            return
        a, b = a + step, b - step
        points = sorted({a, b, *(x for x in self._inner_points if a < x < b)})
        values = [self.value(x) for x in points]
        k = min(range(len(points)), key=values.__getitem__)
        left, right = points[max(k - 1, 0)], points[min(k + 1, len(points) - 1)]
        if values[k] < OBJ_INFEASIBLE and right > left:
            minimize_scalar(
                self.value,
                bounds=(left, right),
                method="bounded",
                options={"xatol": min(1e-2, (right - left) / 20.0), "maxiter": 25},
            )

    def best(self) -> tuple[float, tuple[float, float] | None, dict | None]:
        """Least objective found: ``(value, (dT_ref_evap, dT_ref_cond), state)``."""
        if not self.rows:
            return OBJ_INFEASIBLE, None, None
        key = min(self.rows, key=lambda k: self.rows[k][0])
        value, perf, _ = self.rows[key]
        return value, self.approaches(*key), perf


class _NotARow(Exception):
    """A steady result that :func:`_exergy_row` leaves to the frame form of the post-processing."""


def _boxed(value: Any) -> Any:
    """A cell of a one-row frame as ``DataFrame.iloc[0].to_dict()`` hands it back.

    Only the types a steady result holds are decided here; any other goes
    through the frame.
    """
    kind = type(value)
    if value is None or kind in (float, str, bool, int):
        return value
    if kind is np.float64:
        return float(value)
    if kind is np.bool_:
        return bool(value)
    if kind is np.int64:
        return int(value)
    raise _NotARow(kind)


def _exergy_row(result: dict, ref: str) -> dict:
    """The exergy variables of one steady result: ``AirSourceHeatPump.postprocess_exergy`` on scalars.

    Every line is the corresponding line of ``postprocess_exergy`` and of the
    helpers it calls (``calc_refrigerant_exergy``, ``convert_electricity_to_exergy``,
    ``calc_exergy_flow``), with the operations in the same order, so a one-row
    frame and this give the same bits. Arithmetic is on ``np.float64``: a zero
    divisor gives ``inf``/``nan`` as in a frame, not ``ZeroDivisionError``.
    Raises :class:`_NotARow` where the frame would not behave as written
    (a number held as anything but a float, a string where a frame holds a
    number); a missing key raises ``KeyError`` where the frame does.
    """
    row = {key: _boxed(value) for key, value in result.items()}
    if "T0 [°C]" not in row or "h_ref_cmp_in [J/kg]" not in row:
        raise _NotARow("no refrigerant state: the frame returns the row unchanged")

    def num(key: str) -> np.float64:
        value = row[key]
        if type(value) is not float and type(value) is not np.float64:
            raise _NotARow(key)
        return np.float64(value)

    def filled(key: str) -> np.float64:
        """``fillna(0)`` of a number column."""
        value = num(key)
        return np.float64(0.0) if value != value else value

    def divided(numerator: np.float64, denominator: np.float64) -> np.float64:
        """``numerator / denominator.replace(0, nan)``."""
        return numerator / (np.float64(np.nan) if denominator == 0 else denominator)

    def exergy_flow(G: np.float64, T: np.float64, T0: np.float64) -> np.float64:
        """``calc_exergy_flow`` on a one-row series: zero where a temperature is not positive."""
        invalid = T <= 0 or T0 <= 0
        T_safe = np.float64(1.0) if T <= 0 else T
        T0_safe = np.float64(1.0) if T0 <= 0 else T0
        # np.log of a one-element array: the loop a frame's column goes through.
        log = np.log(np.array([T_safe / T0_safe]))[0]
        return np.float64(0.0) if invalid else G * ((T_safe - T0_safe) - T0_safe * log)

    with np.errstate(all="ignore"):
        T0_K = cu.C2K(num("T0 [°C]"))

        # ── 1. Refrigerant exergy ────────────────────────
        h0 = s0 = np.float64(np.nan)
        if T0_K == T0_K:
            try:
                h0, s0 = (np.float64(v) for v in _props("HS", "T", float(T0_K), "P", 101325, ref))
            except Exception:
                h0 = s0 = np.float64(np.nan)
        m_dot = num("m_dot_ref [kg/s]") if "m_dot_ref [kg/s]" in row else np.float64(np.nan)
        for name in ("cmp_in", "cmp_out", "exp_in", "exp_out"):
            h_key, s_key = f"h_ref_{name} [J/kg]", f"s_ref_{name} [J/(kg·K)]"
            if h_key not in row or s_key not in row:
                continue
            x_val = (num(h_key) - h0) - T0_K * (num(s_key) - s0)
            row[f"x_ref_{name} [J/kg]"] = x_val
            row[f"X_ref_{name} [W]"] = m_dot * x_val

        # ── 2. Electricity = exergy ─────────────────────
        for key in list(row):
            if key.startswith("E_") and key.endswith(" [W]"):
                row["X_" + key[2:]] = row[key]
        if "E_iu_fan [W]" in row:
            row["X_iu_fan [W]"] = row["E_iu_fan [W]"]

        # ── 3. Air exergy (outdoor unit, indoor unit) ───
        for side in ("ou", "iu"):
            if f"dV_{side}_a [m3/s]" in row and f"T_{side}_a_in [°C]" in row:
                T_in_C = num(f"T_{side}_a_in [°C]")
                G = air_heat_capacity_rate(np.array([filled(f"dV_{side}_a [m3/s]")]), np.array([T_in_C]))[0]
                T_in, T_mid = cu.C2K(T_in_C), cu.C2K(num(f"T_{side}_a_mid [°C]"))
                T_out = cu.C2K(num(f"T_{side}_a_out [°C]")) if f"T_{side}_a_out [°C]" in row else T_in
                row[f"X_a_{side}_in [W]"] = exergy_flow(G, T_in, T0_K)
                row[f"X_a_{side}_out [W]"] = exergy_flow(G, T_out, T0_K)
                row[f"X_a_{side}_mid [W]"] = exergy_flow(G, T_mid, T0_K)

        # ── 4. HX Carnot exergy (mode-aware IU/OU) ─────
        mode = row.get("mode")
        if mode is not None and type(mode) is not str:
            raise _NotARow("mode")
        # The bubble point, as in ``postprocess_exergy`` (``T_ref_cond_sat_v`` is a zeotrope's dew point).
        if "T_ref_cond_sat_l [°C]" in row and "T_ref_evap_sat [°C]" in row and "mode" in row:
            heating = mode == "heating"
            cond, evap = num("T_ref_cond_sat_l [°C]"), num("T_ref_evap_sat [°C]")
            T_iu_sat_K = cu.C2K(cond if heating else evap)
            T_ou_sat_K = cu.C2K(evap if heating else cond)
            row["X_ref_iu [W]"] = num("Q_ref_iu [W]") * (1 - T0_K / T_iu_sat_K)
            row["X_ref_ou [W]"] = num("Q_ref_ou [W]") * (1 - T0_K / T_ou_sat_K)

        # ── 5. Total exergy input ───────────────────────
        X_tot = num("E_cmp [W]") + filled("E_ou_fan [W]") + filled("E_iu_fan [W]")
        row["X_tot [W]"] = X_tot

        # ── 6. Component exergy destruction (IU/OU naming) ──
        def air(key: str) -> np.float64:
            return filled(key) if key in row else np.float64(0.0)

        X_a_ou_in, X_a_ou_mid, X_a_ou_out = air("X_a_ou_in [W]"), air("X_a_ou_mid [W]"), air("X_a_ou_out [W]")
        X_a_iu_in, X_a_iu_mid, X_a_iu_out = air("X_a_iu_in [W]"), air("X_a_iu_mid [W]"), air("X_a_iu_out [W]")

        if "X_cmp [W]" not in row:
            return {key: _boxed(value) for key, value in row.items()}

        heating, cooling = row["mode"] == "heating", row["mode"] == "cooling"
        X_ref_cmp_in, X_ref_cmp_out = num("X_ref_cmp_in [W]"), num("X_ref_cmp_out [W]")
        X_ref_exp_in, X_ref_exp_out = num("X_ref_exp_in [W]"), num("X_ref_exp_out [W]")

        # ── 6a. Compressor ──
        row["X_in_cmp [W]"] = X_in_cmp = num("X_cmp [W]") + X_ref_cmp_in
        row["X_out_cmp [W]"] = X_ref_cmp_out
        row["Xc_cmp [W]"] = Xc_cmp = X_in_cmp - X_ref_cmp_out

        # ── 6b. Expansion valve ──
        row["X_in_exp [W]"] = X_ref_exp_in
        row["X_out_exp [W]"] = X_ref_exp_out
        row["Xc_exp [W]"] = Xc_exp = X_ref_exp_in - X_ref_exp_out

        # ── 6c / 6d. Indoor and outdoor unit HX (a mode that is neither leaves zeros) ──
        X_in_iu = X_out_iu = X_in_ou = X_out_ou = np.float64(0.0)
        if heating:
            X_in_iu, X_out_iu = X_ref_cmp_out + X_a_iu_in, X_ref_exp_in + X_a_iu_mid
            X_in_ou, X_out_ou = X_ref_exp_out + X_a_ou_in, X_ref_cmp_in + X_a_ou_mid
        elif cooling:
            X_in_iu, X_out_iu = X_ref_exp_out + X_a_iu_in, X_ref_cmp_in + X_a_iu_mid
            X_in_ou, X_out_ou = X_ref_cmp_out + X_a_ou_in, X_ref_exp_in + X_a_ou_mid
        row["X_in_iu_hx [W]"], row["X_out_iu_hx [W]"] = X_in_iu, X_out_iu
        row["Xc_iu_hx [W]"] = Xc_iu_hx = X_in_iu - X_out_iu
        row["X_in_ou_hx [W]"], row["X_out_ou_hx [W]"] = X_in_ou, X_out_ou
        row["Xc_ou_hx [W]"] = Xc_ou_hx = X_in_ou - X_out_ou

        # ── 6e / 6f. Outdoor and indoor fan ──
        X_in_ou_fan = filled("X_ou_fan [W]") + X_a_ou_mid
        row["X_in_ou_fan [W]"], row["X_out_ou_fan [W]"] = X_in_ou_fan, X_a_ou_out
        row["Xc_ou_fan [W]"] = Xc_ou_fan = X_in_ou_fan - X_a_ou_out
        X_in_iu_fan = filled("X_iu_fan [W]") + X_a_iu_mid
        row["X_in_iu_fan [W]"], row["X_out_iu_fan [W]"] = X_in_iu_fan, X_a_iu_out
        row["Xc_iu_fan [W]"] = Xc_iu_fan = X_in_iu_fan - X_a_iu_out

        # ── 7. Exergetic efficiency metrics ─────────────
        row["X_eff_sys [-]"] = divided(X_a_iu_out - X_a_iu_in, X_tot)
        row["X_eff_cmp [-]"] = 1 - divided(Xc_cmp, X_in_cmp)
        row["X_eff_exp [-]"] = 1 - divided(Xc_exp, X_ref_exp_in)
        row["X_eff_iu_hx [-]"] = 1 - divided(Xc_iu_hx, X_in_iu)
        row["X_eff_ou_hx [-]"] = 1 - divided(Xc_ou_hx, X_in_ou)
        row["X_eff_ou_fan [-]"] = 1 - divided(Xc_ou_fan, X_in_ou_fan)
        row["X_eff_iu_fan [-]"] = 1 - divided(Xc_iu_fan, X_in_iu_fan)

    return {key: _boxed(value) for key, value in row.items()}


class AirSourceHeatPump(ReferenceStateMixin):
    """Air source heat pump with indoor-unit air heat exchange.

    The refrigerant cycle is resolved via CoolProp with
    user-specified superheat / subcool margins.  A bounded
    2-D optimiser minimises total electrical input
    (``E_cmp + E_iu_fan + E_ou_fan``) over the evaporator
    and condenser approach temperatures.

    When the requested duty has no operating point -- the compressor runs
    out of speed, or the compressor envelope or a coil binds first -- the unit
    runs at its ceiling: flat out (``rps_max``, both fans at their largest
    airflow), or at the largest duty the envelope admits (:meth:`max_capacity`).
    Such an hour is reported with ``capacity_clamped == "max"``, a
    ``Q_ref_iu [W]`` below the request and ``capacity_binding`` naming the
    limit; the difference is unmet load (``Q_unmet [W]``), not a solver failure.

    When the request is below what the unit delivers at its minimum compressor
    speed, the compressor cycles on and off. Such an hour is reported with
    ``capacity_clamped == "min"`` as the mean over the cycling period: the
    delivered heat equals the request, the electrical input carries the EN
    14825 degradation (``Cd_cycling``), and ``runtime_fraction [-]`` is the
    share of the time the compressor runs (:mod:`tmhp.cycling`). The same
    holds where the envelope ends continuous operation above the minimum
    speed: at a large lift the shipped isentropic fit falls below its floor
    (``eta_isen_below_min``) towards low speed, and a request below the
    smallest duty that stays inside is met by cycling at that duty
    (``Q_min [W]``), not answered with the unit switched off.
    """

    #: Heat and flow rates averaged with the capacity ratio while cycling.
    _CYCLING_RATE_KEYS: tuple[str, ...] = (
        "Q_ref_iu [W]",
        "Q_ref_ou [W]",
        "Q_a_iu_in [W]",
        "Q_a_iu_out [W]",
        "m_dot_ref [kg/s]",
        "dV_iu_a [m3/s]",
        "dV_ou_a [m3/s]",
    )
    #: Electrical powers averaged with the runtime fraction while cycling. The
    #: indoor fan is not among them: it moves the delivered heat and is
    #: averaged with it (``E_iu_fan [W]`` times the capacity ratio).
    _CYCLING_POWER_KEYS: tuple[str, ...] = ("E_cmp [W]", "E_ou_fan [W]")

    #: Heat and flow rates averaged with the delivered-heat factor under frost.
    _FROST_RATE_KEYS: tuple[str, ...] = (
        "Q_ref_iu [W]",
        "Q_ref_ou [W]",
        "Q_a_iu_in [W]",
        "Q_a_iu_out [W]",
        "m_dot_ref [kg/s]",
        "dV_iu_a [m3/s]",
        "dV_ou_a [m3/s]",
    )
    #: Delivered heat and electrical input of the frost-free cycle a row was solved at [W].
    _FROST_FREE_HEAT_KEY = "Q_ref_iu_frost_free [W]"
    _FROST_FREE_POWER_KEY = "E_tot_frost_free [W]"
    #: EnergyPlus / DOE-2.1E frost adjustment in heating (None: frost-free
    #: steady state). Set by the constructor.
    defrost: DefrostModel | None = None

    _RATING_FAMILY = "ASHP"
    _RATING_DEFAULT_MODE = "cooling"

    def __init__(
        self,
        # 1. Refrigerant / cycle / compressor -----------
        ref: str = "R32",
        V_cmp_ref: float | None = None,
        eta_cmp_isen: float | Callable | None = None,
        eta_cmp_vol: float | Callable | None = None,
        eta_cmp: float | Callable | None = None,
        dT_superheat: float = 3.0,
        dT_subcool: float = 3.0,
        # 2. Heat exchanger UA ---------------------------
        UA_ou_rated: float | None = None,
        UA_iu_rated: float | None = None,
        n_ou: float = 0.65,
        n_iu: float = 0.65,
        # 3. Outdoor unit fan ----------------------------
        dV_ou_fan_a_rated: float | None = None,
        dP_ou_fan_rated: float | None = None,
        A_cross_ou: float | None = None,
        eta_ou_fan_rated: float | None = None,
        # 4. Indoor unit fan -----------------------
        dV_iu_fan_a_rated: float | None = None,
        dP_iu_fan_rated: float | None = None,
        A_cross_iu: float | None = None,
        eta_iu_fan_rated: float | None = None,
        # 5. System capacity / room ----------------------
        hp_capacity: float = 4000.0,
        T_a_room: float = 27.0,
        # 6. Cycle guard ---------------------------------
        dT_hx_min: float = 0.5,
        # Compressor pressure-ratio envelope (PR = P_cond / P_evap)
        PR_cycle_min: float = 1.5,
        # 5.0 rejected ordinary cold-weather heating: a split unit lifting from
        # -15 degC outdoor air to a 27 degC room runs at a pressure ratio near
        # 7, and Daikin publishes performance down to -20 degC. The guard is a
        # sanity bound, not a performance model, so it is set at the AC-scroll
        # self-unload limit cited in `compressor_envelope` (~11:1) rather than
        # below the equipment's own published envelope. Every family uses it.
        PR_cycle_max: float = PR_CYCLE_MAX_SINGLE_STAGE,
        # Compressor speed bounds [rev/s]; rps_max=None -> default_rps_max(rps_rated)
        rps_min: float = 15.0,
        rps_max: float | None = None,
        # Approach-temperature search bounds [K]: how far the refrigerant
        # saturation temperature may stand from the inlet air on either side.
        # The upper bound is a modelling limit, not a component limit, and it
        # becomes the binding constraint on :meth:`max_capacity` at mild
        # conditions -- widen it there rather than reading a bound as a ceiling.
        dT_approach_bounds: tuple[float, float] = (1.0, 20.0),
        # ASHRAE 90.1-2022 VSD coefficients
        vsd_coeffs_ou: dict | None = None,
        vsd_coeffs_iu: dict | None = None,
        # Deprecated:
        V_disp_cmp: float | None = None,
        eta_cmp_mech: float | Callable | None = None,
        UA_cond_rated: float | None = None,
        UA_evap_rated: float | None = None,
        n_cond: float | None = None,
        n_evap: float | None = None,
        UA_cond_design: float | None = None,
        UA_evap_design: float | None = None,
        dV_ou_fan_a_design: float | None = None,
        dP_ou_fan_design: float | None = None,
        eta_ou_fan_design: float | None = None,
        dV_iu_fan_a_design: float | None = None,
        dP_iu_fan_design: float | None = None,
        eta_iu_fan_design: float | None = None,
        *,
        rps_rated: float | None = None,
        m_dot_ref_rated: float | None = None,
        rated_condition: Mapping[str, Any] | RatingCondition | None = None,
        dV_ou_fan_a_ref: float | None = None,
        dV_ou_fan_a_min: float | None = None,
        dV_ou_fan_a_max: float | None = None,
        dV_iu_fan_a_ref: float | None = None,
        dV_iu_fan_a_min: float | None = None,
        dV_iu_fan_a_max: float | None = None,
        # On/off cycling below the minimum continuous capacity: EN 14825
        # degradation coefficient Cd (None: no cycling, the unit over-delivers).
        Cd_cycling: CyclingDegradation | float | None = CD_AIR_DEFAULT,
        # Frost on the outdoor coil and its defrost, heating only (None / False: off).
        defrost: DefrostModel | bool | None = True,
        T_dis_max: float | Literal["auto"] | None = "auto",
        x_suction_min: float | None = X_SUCTION_MIN_DEFAULT,
        T_cond_max: float | Literal["auto"] | None = "auto",
    ):
        validate_ctor("ASHP", locals())
        import warnings

        if dV_ou_fan_a_ref is not None:
            dV_ou_fan_a_rated = dV_ou_fan_a_ref
        if dV_iu_fan_a_ref is not None:
            dV_iu_fan_a_rated = dV_iu_fan_a_ref
        # Resolve deprecated mapping
        if V_cmp_ref is None:
            V_cmp_ref = V_disp_cmp if V_disp_cmp is not None else default_displacement(hp_capacity)
        if eta_cmp is None:
            eta_cmp = eta_cmp_mech
        # UA_cond/evap_design → UA_cond/evap_rated (oldest names, two hops)
        if UA_cond_rated is None:
            UA_cond_rated = UA_cond_design
        if UA_evap_rated is None:
            UA_evap_rated = UA_evap_design
        # UA_cond/evap_rated → UA_ou/iu_rated (physical-unit rename, issue #183)
        if UA_cond_rated is not None or UA_evap_rated is not None:
            warnings.warn(
                "UA_cond_rated/UA_evap_rated are deprecated and will be removed in a future"
                " release. Use UA_ou_rated/UA_iu_rated (physical unit identity).",
                DeprecationWarning,
                stacklevel=2,
            )
            if UA_ou_rated is None:
                UA_ou_rated = UA_cond_rated
            if UA_iu_rated is None:
                UA_iu_rated = UA_evap_rated
        if n_cond is not None or n_evap is not None:
            warnings.warn(
                "n_cond/n_evap are deprecated and will be removed in a future release. Use n_ou/n_iu.",
                DeprecationWarning,
                stacklevel=2,
            )
            if n_cond is not None:
                n_ou = n_cond
            if n_evap is not None:
                n_iu = n_evap
        if dV_ou_fan_a_rated is None:
            dV_ou_fan_a_rated = dV_ou_fan_a_design
        if dP_ou_fan_rated is None:
            dP_ou_fan_rated = dP_ou_fan_design if dP_ou_fan_design is not None else 60.0
        if eta_ou_fan_rated is None:
            eta_ou_fan_rated = eta_ou_fan_design if eta_ou_fan_design is not None else 0.6
        if dV_iu_fan_a_rated is None:
            dV_iu_fan_a_rated = dV_iu_fan_a_design
        if dP_iu_fan_rated is None:
            dP_iu_fan_rated = dP_iu_fan_design if dP_iu_fan_design is not None else 60.0
        if eta_iu_fan_rated is None:
            eta_iu_fan_rated = eta_iu_fan_design if eta_iu_fan_design is not None else 0.6

        if not vsd_coeffs_ou:
            vsd_coeffs_ou = SINGLE_ZONE_VAV_COEFFICIENTS.copy()
        if not vsd_coeffs_iu:
            vsd_coeffs_iu = SINGLE_ZONE_VAV_COEFFICIENTS.copy()

        # --- 1. Refrigerant / cycle / compressor ---
        self.ref: str = ref
        import CoolProp.CoolProp as CP

        #: Critical temperature of the refrigerant [K]. The cycle this model
        #: solves is subcritical, so it bounds every saturation temperature the
        #: operating-point search may propose.
        self._T_crit_K: float = float(CP.PropsSI("Tcrit", ref))
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
        # Maximum condensing temperature [degC] ("auto": refrigerant default; None: off)
        self.T_cond_max: float | None = resolve_condensing_temp_max(T_cond_max, ref)
        # Discharge-temperature control: lowest suction quality (None: no control)
        self.x_suction_min: float | None = resolve_x_suction_min(x_suction_min)
        #: Suction quality of the cycle being evaluated (< 1 only inside the control).
        self._x_suction: float = 1.0
        self.dT_approach_bounds: tuple[float, float] = (
            float(dT_approach_bounds[0]),
            float(dT_approach_bounds[1]),
        )
        # Records the PR-envelope event of the most recent _calc_state call
        # (None | ("pr_below_min", pr, bound) | ("pr_above_max", pr, bound)).
        self._last_pr_event: tuple[str, float, float] | None = None
        self.hp_capacity: float = hp_capacity

        # --- 2. Heat exchanger UA ---
        # Outdoor-coil conductance per watt of nameplate cooling capacity.
        #
        # Derived, not chosen. Air-coil catalogues rate an entire range at one
        # declared condition, so inverting them gives conductance per unit duty
        # that is comparable across capacities by construction -- and for a
        # coil whose refrigerant side changes phase that inversion is an
        # identity, with no parameter fitted. Two rating standards written by
        # different committees for different applications (EN 328 SC2 for unit
        # coolers, 352 models across four manufacturers; ENV 327 for air-cooled
        # condensers, 1,062 models across two) place their populations in the
        # same band of UA per unit duty, 0.11-0.23 W/K per W, although the air
        # flow per kilowatt they adopt differs by a factor of 2.4.
        #
        # Converting the condenser band to the nameplate basis needs the factor
        # `1 + 1/EER`, because the nameplate is the indoor coil's cooling duty
        # while the outdoor coil rejects that duty plus the compressor work
        # (1.29-1.35 across the reference units). The result is
        # UA/Q_cool = 0.19, i.e. `hp_capacity / 5`.
        #
        # An independent route -- computing the conductance straight from the
        # outdoor-coil geometry that Trane prints for twelve Precedent packaged
        # heat pumps, through the Wang/Chi/Chang (2000) plain-fin correlation
        # and Schmidt fin efficiency -- gives Q/6.8 with a declared
        # refrigerant-side film coefficient of 2500 W/(m^2 K) and Q/4.3 with
        # that resistance removed, bracketing the value above. The two routes
        # share neither inputs nor method.
        #
        # See validation/extraction/ua_transfer_law.py to regenerate all of it.
        # Established for dry, round-tube-plate-fin coils; frosted operation,
        # microchannel coils and residential mini-splits are outside the
        # evidence and stated as such in the documentation.
        if UA_ou_rated is None:
            self.UA_ou_rated = hp_capacity / 5.0
        else:
            self.UA_ou_rated = UA_ou_rated

        # Both faces are air coils sharing the same equivalent approach, so
        # their conductance ratio is their duty ratio, `1 / (1 + 1/EER)`. That
        # is 0.73-0.78 across the twelve reference units and 0.78-0.80 at the
        # EER of current equipment, so 0.8 is derived rather than assumed.
        # In cooling the indoor coil runs wet, which makes a sensible-basis UA
        # an underestimate; the ratio rises above 0.8 when the latent load is
        # large.
        if UA_iu_rated is None:
            self.UA_iu_rated = self.UA_ou_rated * 0.8
        else:
            self.UA_iu_rated = UA_iu_rated

        self.n_ou: float = n_ou
        self.n_iu: float = n_iu

        # --- 3. Outdoor unit fan ---
        if dV_ou_fan_a_rated is None:
            self.dV_ou_fan_a_rated = hp_capacity * 0.0002
        else:
            self.dV_ou_fan_a_rated = dV_ou_fan_a_rated

        self.dV_ou_fan_a_ref = self.dV_ou_fan_a_rated
        self.dV_ou_fan_a_min, self.dV_ou_fan_a_max = resolve_fan_flow_limits(
            self.dV_ou_fan_a_ref,
            dV_ou_fan_a_min,
            dV_ou_fan_a_max,
            custom_curve=not is_generic_fan_curve(vsd_coeffs_ou),
        )
        self.dP_ou_fan_rated: float = dP_ou_fan_rated
        self.eta_ou_fan_rated: float = eta_ou_fan_rated

        if A_cross_ou is None:
            self.A_cross_ou = self.dV_ou_fan_a_rated / 2.0
        else:
            self.A_cross_ou = A_cross_ou

        self.E_ou_fan_rated: float = self.dV_ou_fan_a_rated * self.dP_ou_fan_rated / self.eta_ou_fan_rated
        self.vsd_coeffs_ou: dict = vsd_coeffs_ou
        self.fan_params_ou: dict = {
            "fan_min_flow_rate": self.dV_ou_fan_a_min,
            "fan_max_flow_rate": self.dV_ou_fan_a_max,
            "fan_ref_flow_rate": self.dV_ou_fan_a_rated,
            "fan_rated_flow_rate": self.dV_ou_fan_a_rated,
            "fan_ref_power": self.E_ou_fan_rated,
            "fan_rated_power": self.E_ou_fan_rated,
        }

        # --- 4. Indoor unit fan ---
        # The indoor airflow sets the condensing (heating) and evaporating
        # (cooling) temperatures through the coil's approach, so it decides
        # the supply-air temperature and most of the lift. 400 cfm/ton is the
        # airflow unitary equipment is rated at; see ``INDOOR_AIRFLOW_PER_W``.
        if dV_iu_fan_a_rated is None:
            self.dV_iu_fan_a_rated = hp_capacity * INDOOR_AIRFLOW_PER_W
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

        # --- 5. Room temperature ---
        self.T_a_room: float = T_a_room

        # --- 5b. Part load below the minimum continuous capacity ---
        #: EN 14825 cycling degradation (None: continuous at minimum speed).
        self.cycling: CyclingDegradation | None = resolve_cycling(Cd_cycling, "air")

        # --- 5c. Frost on the outdoor coil and its defrost (heating) ---
        self.defrost = resolve_defrost(defrost)

        # --- 6. Compressor reference state (rated speed, rated mass flow) ---
        efficiencies = self._initialize_reference_state(
            rps_rated=rps_rated,
            m_dot_ref_rated=m_dot_ref_rated,
            efficiencies={"eta_cmp_isen": eta_cmp_isen, "eta_cmp_vol": eta_cmp_vol, "eta_cmp": eta_cmp},
            rated_condition=rated_condition,
        )
        self.eta_cmp_isen: float | Callable = efficiencies["eta_cmp_isen"]
        self.eta_cmp_vol: float | Callable = efficiencies["eta_cmp_vol"]
        self.eta_cmp: float | Callable = efficiencies["eta_cmp"]

    def _rating_side(self, role: str, mode: str, T_in_C: float) -> HXSide:
        """Outdoor coil is the source side, indoor coil the load side, in either mode."""
        if role == "source":
            return AirCoilSide("air", T_in_C, self.UA_ou_rated, self.dV_ou_fan_a_ref)
        return AirCoilSide("air", T_in_C, self.UA_iu_rated, self.dV_iu_fan_a_ref)

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
    ) -> dict | None:
        """Evaluate refrigerant cycle at a given operating point.

        Parameters
        ----------
        dT_ref_evap : float
            Evaporator approach ΔT [K].
        dT_ref_cond : float
            Condenser approach ΔT [K].
        Q_r_iu : float
            Indoor thermal load [W].
            Positive = cooling (indoor unit is evaporator).
            Negative = heating (indoor unit is condenser).
        T0 : float
            Dead-state / outdoor-air temperature [°C].
        T_a_room : float
            Room air temperature [°C].

        Returns
        -------
        dict | None
            Cycle performance dictionary; ``None`` if infeasible.
        """
        T0_K: float = cu.C2K(T0)
        T_a_room_K: float = cu.C2K(T_a_room)
        is_active: bool = Q_r_iu != 0.0

        if not is_active:
            cs: dict = calc_ref_state(
                T_evap_K=T0_K,
                T_cond_K=T0_K,
                refrigerant=self.ref,
                eta_cmp_isen=1.0,
                mode="off",
                dT_superheat=self.dT_superheat,
                dT_subcool=self.dT_subcool,
                is_active=False,
            )
            result = reportable_state(cs)
            result.update(
                {
                    "hp_is_on": False,
                    "converged": True,
                    "converged_rps": True,
                    "capacity_clamped": None,
                    "ou_fan_flow_min_limit": False,
                    "ou_fan_flow_max_limit": False,
                    "iu_fan_flow_min_limit": False,
                    "iu_fan_flow_max_limit": False,
                    # Temperatures [°C]
                    "T_ou_a_in [°C]": T0,
                    "T_ou_a_mid [°C]": T0,
                    "T_ou_a_out [°C]": T0,
                    "T_iu_a_in [°C]": T_a_room,
                    "T_iu_a_mid [°C]": T_a_room,
                    "T_iu_a_out [°C]": T_a_room,
                    "T_a_room [°C]": T_a_room,
                    "T0 [°C]": T0,
                    # Volume flow rates [m3/s]
                    "dV_ou_a [m3/s]": 0.0,
                    "v_ou_a [m/s]": 0.0,
                    "dV_iu_a [m3/s]": 0.0,
                    "v_iu_a [m/s]": 0.0,
                    "m_dot_ref [kg/s]": 0.0,
                    "cmp_rpm [rpm]": 0.0,
                    # Energy rates [W]
                    "E_iu_fan [W]": 0.0,
                    "E_ou_fan [W]": 0.0,
                    # Heat duties by physical location (mode-independent labels)
                    "Q_ref_iu [W]": 0.0,
                    "Q_a_iu_in [W]": 0.0,
                    "Q_a_iu_out [W]": 0.0,
                    "indoor_heat_direction_valid": True,
                    "Q_ref_ou [W]": 0.0,
                    "E_cmp [W]": 0.0,
                    "E_tot [W]": 0.0,
                    # COP metrics
                    "cop_ref [-]": np.nan,
                    "cop_sys [-]": np.nan,
                }
            )
            return result

        if Q_r_iu > 0:
            # Cooling mode: indoor = evaporator, outdoor = condenser
            mode = "cooling"
            T_evap_sat_K = T_a_room_K - dT_ref_evap  # evap below room
            T_cond_sat_K = T0_K + dT_ref_cond  # cond above outdoor
        else:
            # Heating mode: indoor = condenser, outdoor = evaporator
            mode = "heating"
            T_evap_sat_K = T0_K - dT_ref_evap  # evap below outdoor
            T_cond_sat_K = T_a_room_K + dT_ref_cond  # cond above room

        # A saturation temperature at or above the critical point has no
        # saturation pressure, so the subcritical cycle this model solves does
        # not exist there. Reject it as an infeasible candidate rather than
        # letting the property call raise: the search probes approach
        # temperatures freely, and a wide enough upper bound will reach the
        # critical temperature on a hot day (R410A: 71.3 degC).
        if T_cond_sat_K >= self._T_crit_K or T_evap_sat_K >= self._T_crit_K:
            return None

        # Low-lift feasibility is enforced downstream by the compressor
        # pressure-ratio floor (PR_cycle_min); a separate fixed minimum lift is
        # redundant and non-transferable across refrigerants/operating levels.

        actual_dT_subcool: float = min(self.dT_subcool, max(0.0, dT_ref_cond - self.dT_hx_min))
        actual_dT_superheat: float = min(self.dT_superheat, max(0.0, dT_ref_evap - self.dT_hx_min))

        # Same name (`cs`) is annotated up in the inactive branch (~L206);
        # re-annotating here triggers mypy [no-redef] even though the
        # inactive branch returns unconditionally. Use plain assignment.
        #
        # The cycle without its discharge state: pressures, suction state and
        # isentropic discharge enthalpy are what the speed solve needs, and
        # the discharge state follows from the efficiency at the solved speed.
        try:
            cs, h_ref_cmp_out_isen = calc_ref_state_without_discharge(
                T_evap_sat_K,
                T_cond_sat_K,
                self.ref,
                mode=mode,
                dT_superheat=actual_dT_superheat,
                x_suction=self._x_suction,
                dT_subcool=actual_dT_subcool,
            )
        except ValueError:
            # The fluid has no state at this candidate (a flash CoolProp cannot
            # solve): no cycle here. The search treats it as infeasible instead
            # of the whole solve raising.
            return None

        h_cmp_in: float = cs["h_ref_cmp_in [J/kg]"]
        h_exp_in: float = cs["h_ref_exp_in [J/kg]"]
        h_exp_out: float = cs["h_ref_exp_out [J/kg]"]
        rho_in: float = cs["rho_ref_cmp_in [kg/m3]"]
        P_evap = cs["P_ref_cmp_in [Pa]"]
        P_cond = cs["P_ref_cmp_out [Pa]"]

        ratio_P_cmp = P_cond / P_evap if P_evap > 0 else 1.0

        # Compressor pressure-ratio envelope guard. PR is the physically primary
        # limit (a fixed temperature lift maps to PR non-linearly per refrigerant
        # and operating level). Ceiling -> reject (outside single-stage envelope);
        # floor -> clamp the cycle onto PR_cycle_min (continuous low-lift
        # transition) by holding P_evap and projecting P_cond. Both events are
        # recorded on self._last_pr_event for the analyze_steady hint; no print
        # here (this runs inside the optimiser loop).
        self._last_pr_event = None
        pr_event = check_pr_envelope(ratio_P_cmp, self.PR_cycle_min, self.PR_cycle_max)
        if pr_event == "pr_above_max":
            self._last_pr_event = ("pr_above_max", ratio_P_cmp, self.PR_cycle_max)
            return None
        if pr_event == "pr_below_min":
            self._last_pr_event = ("pr_below_min", ratio_P_cmp, self.PR_cycle_min)
            # Clamp: hold P_evap, project P_cond = PR_cycle_min * P_evap, invert
            # the saturation curve for the constrained condensing temperature,
            # then refresh the cycle state at the clamped condition.
            P_cond = self.PR_cycle_min * P_evap
            try:
                T_cond_sat_K = props_si("T", "P", P_cond, "Q", 0, self.ref)
                cs, h_ref_cmp_out_isen = calc_ref_state_without_discharge(
                    T_evap_sat_K,
                    T_cond_sat_K,
                    self.ref,
                    mode=mode,
                    dT_superheat=actual_dT_superheat,
                    x_suction=self._x_suction,
                    dT_subcool=actual_dT_subcool,
                )
            except ValueError:
                # The floor pressure has no saturation state (above the
                # critical pressure): no cycle at this candidate.
                return None
            h_cmp_in = cs["h_ref_cmp_in [J/kg]"]
            h_exp_in = cs["h_ref_exp_in [J/kg]"]
            h_exp_out = cs["h_ref_exp_out [J/kg]"]
            rho_in = cs["rho_ref_cmp_in [kg/m3]"]
            P_evap = cs["P_ref_cmp_in [Pa]"]
            P_cond = cs["P_ref_cmp_out [Pa]"]
            ratio_P_cmp = P_cond / P_evap if P_evap > 0 else self.PR_cycle_min

        # The three efficiencies as functions of (PR, rps): `_eval_eff` with the
        # call form of each model decided once, not on every speed candidate.
        eta_vol_at = efficiency_evaluator(self.eta_cmp_vol)
        eta_isen_at = efficiency_evaluator(self.eta_cmp_isen)
        eta_em_at = efficiency_evaluator(self.eta_cmp)

        def _residual_rps(rps):
            val_eta_vol = eta_vol_at(ratio_P_cmp, rps)
            val_eta_isen = eta_isen_at(ratio_P_cmp, rps)
            h_cmp_out_local = h_cmp_in + (h_ref_cmp_out_isen - h_cmp_in) / val_eta_isen

            dh_cond_local = h_cmp_out_local - h_exp_in
            dh_evap_local = h_cmp_in - h_exp_out

            m_dot = self.V_cmp_ref * rho_in * val_eta_vol * rps
            if mode == "cooling":
                return (m_dot * dh_evap_local) - abs(Q_r_iu)
            else:
                return (m_dot * dh_cond_local) - abs(Q_r_iu)

        cmp_rps, converged_rps, capacity_clamped = solve_compressor_speed(_residual_rps, self.rps_min, self.rps_max)

        val_eta_vol = eta_vol_at(ratio_P_cmp, cmp_rps)
        val_eta_isen = eta_isen_at(ratio_P_cmp, cmp_rps)
        val_eta_electro_mech = eta_em_at(ratio_P_cmp, cmp_rps)

        cs = with_discharge_state(cs, h_ref_cmp_out_isen, val_eta_isen, self.ref)

        h_cmp_out_final = cs["h_ref_cmp_out [J/kg]"]
        m_dot_ref = self.V_cmp_ref * rho_in * val_eta_vol * cmp_rps
        Q_ref_cond = m_dot_ref * (h_cmp_out_final - h_exp_in)
        Q_ref_evap = m_dot_ref * (h_cmp_in - h_exp_out)
        # Map refrigerant-role duties to physical-location duties for output.
        # Heating: IU = condenser, OU = evaporator; cooling: roles swap.
        Q_ref_iu = Q_ref_cond if mode == "heating" else Q_ref_evap
        Q_ref_ou = Q_ref_evap if mode == "heating" else Q_ref_cond
        E_cmp = (m_dot_ref * (h_cmp_out_final - h_cmp_in)) / val_eta_electro_mech

        # Reject negative compressor power (unphysical)
        if E_cmp <= 0:
            return None

        # ── Outdoor unit HX ──
        # The outdoor coil is always parameterised by UA_ou_rated regardless of mode.
        # In cooling it acts as condenser; in heating as evaporator — but the physical
        # geometry (and therefore UA) is unchanged.
        if mode == "cooling":
            # Outdoor = condenser → ref rejects heat → air is heated
            ou_hx = calc_HX_perf_for_target_heat(
                Q_ref_target=Q_ref_cond,
                T_a_in_C=T0,
                T_ref_sat_K=T_cond_sat_K,
                A_cross=self.A_cross_ou,
                UA_rated=self.UA_ou_rated,
                dV_fan_ref=self.dV_ou_fan_a_ref,
                dV_fan_min=self.dV_ou_fan_a_min,
                dV_fan_max=self.dV_ou_fan_a_max,
                custom_fan_curve=not is_generic_fan_curve(self.vsd_coeffs_ou),
                is_active=True,
                exponent=self.n_ou,
                rho_air="inlet",
            )
        else:
            # Outdoor = evaporator → ref absorbs heat → air is cooled
            ou_hx = calc_HX_perf_for_target_heat(
                Q_ref_target=Q_ref_evap,
                T_a_in_C=T0,
                T_ref_sat_K=T_evap_sat_K,
                A_cross=self.A_cross_ou,
                UA_rated=self.UA_ou_rated,
                dV_fan_ref=self.dV_ou_fan_a_ref,
                dV_fan_min=self.dV_ou_fan_a_min,
                dV_fan_max=self.dV_ou_fan_a_max,
                custom_fan_curve=not is_generic_fan_curve(self.vsd_coeffs_ou),
                is_active=True,
                exponent=self.n_ou,
                rho_air="inlet",
            )

        dV_ou_a: float = ou_hx["dV_fan"]
        T_ou_a_mid: float = ou_hx["T_a_mid_C"]
        E_ou_fan: float = calc_fan_power_from_dV_fan(
            dV_fan=dV_ou_a,
            fan_params=self.fan_params_ou,
            vsd_coeffs=self.vsd_coeffs_ou,
            is_active=True,
        )
        # The fan heat warms the same air mass the coil exchanged heat with:
        # the fan's volume flow at the density of the air drawn in.
        C_ou_a: float = air_heat_capacity_rate(dV_ou_a, T0)
        T_ou_a_out: float = T_ou_a_mid + E_ou_fan / C_ou_a if dV_ou_a > 0 else T0
        v_ou_a: float = dV_ou_a / self.A_cross_ou

        # ── Indoor unit HX ──
        # The indoor coil is always parameterised by UA_iu_rated regardless of mode.
        if mode == "cooling":
            # Indoor = evaporator → ref absorbs heat → air is cooled
            iu_hx = calc_HX_perf_for_target_heat(
                Q_ref_target=Q_ref_evap,
                T_a_in_C=T_a_room,
                T_ref_sat_K=T_evap_sat_K,
                A_cross=self.A_cross_iu,
                UA_rated=self.UA_iu_rated,
                dV_fan_ref=self.dV_iu_fan_a_ref,
                dV_fan_min=self.dV_iu_fan_a_min,
                dV_fan_max=self.dV_iu_fan_a_max,
                custom_fan_curve=not is_generic_fan_curve(self.vsd_coeffs_iu),
                is_active=True,
                exponent=self.n_iu,
                rho_air="inlet",
            )
        else:
            # Indoor = condenser → ref rejects heat → air is heated
            iu_hx = calc_HX_perf_for_target_heat(
                Q_ref_target=Q_ref_cond,
                T_a_in_C=T_a_room,
                T_ref_sat_K=T_cond_sat_K,
                A_cross=self.A_cross_iu,
                UA_rated=self.UA_iu_rated,
                dV_fan_ref=self.dV_iu_fan_a_ref,
                dV_fan_min=self.dV_iu_fan_a_min,
                dV_fan_max=self.dV_iu_fan_a_max,
                custom_fan_curve=not is_generic_fan_curve(self.vsd_coeffs_iu),
                is_active=True,
                exponent=self.n_iu,
                rho_air="inlet",
            )

        dV_iu_a: float = iu_hx["dV_fan"]
        T_iu_a_mid: float = iu_hx["T_a_mid_C"]
        E_iu_fan: float = calc_fan_power_from_dV_fan(
            dV_fan=dV_iu_a,
            fan_params=self.fan_params_iu,
            vsd_coeffs=self.vsd_coeffs_iu,
            is_active=True,
        )
        C_iu_a: float = air_heat_capacity_rate(dV_iu_a, T_a_room)
        T_iu_a_out: float = T_iu_a_mid + E_iu_fan / C_iu_a if dV_iu_a > 0 else T_a_room
        v_iu_a: float = dV_iu_a / self.A_cross_iu

        # --- Check convergence for both HXs ---
        if not (ou_hx.get("converged", True) and iu_hx.get("converged", True)):
            return {
                "converged": False,
                "_ou_diag": ou_hx,
                "_iu_diag": iu_hx,
                # The cycle exists; only a coil does not carry it. The
                # discharge-temperature control acts on it, so the coil margins
                # the searches read are those of the controlled cycle -- the
                # same cycle a candidate whose coils close is evaluated at.
                CYCLE_KEY: reportable_state(cs),
                "converged_rps": bool(converged_rps),
                "capacity_clamped": capacity_clamped,
                "ou_fan_flow_min_limit": ou_hx.get("min_limit", False),
                "ou_fan_flow_max_limit": ou_hx.get("max_limit", False),
                "iu_fan_flow_min_limit": iu_hx.get("min_limit", False),
                "iu_fan_flow_max_limit": iu_hx.get("max_limit", False),
            }

        # Check overall convergence
        is_converged = ou_hx.get("converged", True) and iu_hx.get("converged", True) and converged_rps
        E_tot: float = E_cmp + E_ou_fan + E_iu_fan
        # Final outlet already includes fan heat. Keep the coil-load residual
        # and operating-point objective unchanged; only the reported COP changes.
        G_iu_a = C_iu_a
        Q_a_iu_in = calc_energy_flow(G_iu_a, T_a_room_K, T0_K)
        Q_a_iu_out = calc_energy_flow(G_iu_a, cu.C2K(T_iu_a_out), T0_K)
        Q_air = Q_a_iu_out - Q_a_iu_in
        heat_direction_valid = bool(dV_iu_a > 0 and (Q_air <= 0 if mode == "cooling" else Q_air >= 0))

        # Same name (`result`) is annotated up in the inactive branch (~L216);
        # plain assignment to avoid the mypy [no-redef] false positive.
        result = reportable_state(cs)
        result.update(
            {
                "hp_is_on": True,
                "mode": mode,
                "converged": bool(is_converged),
                "converged_rps": bool(converged_rps),
                "capacity_clamped": capacity_clamped,
                "ou_fan_flow_min_limit": ou_hx.get("min_limit", False),
                "ou_fan_flow_max_limit": ou_hx.get("max_limit", False),
                "iu_fan_flow_min_limit": iu_hx.get("min_limit", False),
                "iu_fan_flow_max_limit": iu_hx.get("max_limit", False),
                # Temperatures [°C]
                "T_ou_a_in [°C]": T0,
                "T_ou_a_mid [°C]": T_ou_a_mid,
                "T_ou_a_out [°C]": T_ou_a_out,
                "T_iu_a_in [°C]": T_a_room,
                "T_iu_a_mid [°C]": T_iu_a_mid,
                "T_iu_a_out [°C]": T_iu_a_out,
                "T_a_room [°C]": T_a_room,
                "T0 [°C]": T0,
                # Volume flow rates [m3/s]
                "dV_ou_a [m3/s]": dV_ou_a,
                "v_ou_a [m/s]": v_ou_a,
                "dV_iu_a [m3/s]": dV_iu_a,
                "v_iu_a [m/s]": v_iu_a,
                "m_dot_ref [kg/s]": m_dot_ref,
                "cmp_rpm [rpm]": cmp_rps * 60,
                "n_star [-]": cmp_rps / self.rps_rated,
                "pr_cmp [-]": ratio_P_cmp,
                "eta_cmp_vol [-]": val_eta_vol,
                "eta_cmp_isen [-]": val_eta_isen,
                "eta_cmp [-]": val_eta_electro_mech,
                # Energy rates [W]
                "E_iu_fan [W]": E_iu_fan,
                "E_ou_fan [W]": E_ou_fan,
                # Heat duties by physical location (mode-mapped): in heating the
                # indoor unit is the condenser and the outdoor unit the evaporator;
                # in cooling the roles swap. Reported by location so the labels are
                # mode-independent and the consumer never sees the cond/evap
                # bookkeeping (the refrigerant-perspective cond/evap remain only in
                # the refrigerant-state keys T/P/h/s_ref_*_sat and in refrigerant.py).
                "Q_ref_iu [W]": Q_ref_iu,
                "Q_a_iu_in [W]": Q_a_iu_in,
                "Q_a_iu_out [W]": Q_a_iu_out,
                "indoor_heat_direction_valid": heat_direction_valid,
                "Q_ref_ou [W]": Q_ref_ou,
                "E_cmp [W]": E_cmp,
                "E_tot [W]": E_tot,
                # Refrigerant COP uses coil duty; system COP uses final air delivery.
                "cop_ref [-]": (Q_ref_iu / E_cmp if E_cmp > 0 else np.nan),
                "cop_sys [-]": (abs(Q_air) / E_tot if E_tot > 0 and heat_direction_valid else np.nan),
            }
        )
        return result

    def _optimize_operation(
        self,
        Q_r_iu: float,
        T0: float,
        T_a_room: float,
    ):
        """Find the operating point the unit would run at (2-D bounded search).

        Normally that is the point of minimum total electric power that meets
        the requested duty. The search starts from the best of a coarse set of
        approach pairs and, where that leaves it above the cost of the
        least-lift point (:meth:`_largest_airflow_point`) or finds nothing,
        from the least-lift point as well. When the unit is short of the
        request even there -- flat out -- the result is that point, in the
        capacity-limited band (:data:`OBJ_CAPACITY_LIMITED`); the ceiling is
        then the caller's to solve (:meth:`_capacity_limited_operating_point`).
        ``least_lift_reason`` on an infeasible result says why the least-lift
        point of the request is not an operating point.

        Parameters
        ----------
        Q_r_iu : float
            Indoor thermal load [W].
        T0 : float
            Dead-state temperature [°C].
        T_a_room : float
            Room air temperature [°C].

        Returns
        -------
        scipy.optimize.OptimizeResult
        """

        def _objective(params) -> float:
            dT_ref_evap, dT_ref_cond = params
            perf: dict | None = self._calc_state(
                dT_ref_evap=dT_ref_evap,
                dT_ref_cond=dT_ref_cond,
                Q_r_iu=Q_r_iu,
                T0=T0,
                T_a_room=T_a_room,
            )
            if perf is None or not perf.get("converged", False):
                return OBJ_INFEASIBLE

            E_tot: float = float(perf.get("E_tot [W]", OBJ_INFEASIBLE))
            if E_tot <= 0 or np.isnan(E_tot):
                return OBJ_INFEASIBLE

            delivered: float = abs(float(perf.get("Q_ref_iu [W]", 0.0)))

            if perf.get("capacity_clamped") == CAPACITY_CLAMPED_MAX:
                # The compressor is already at ``rps_max`` and still short of the
                # request. Ranking these points by power would trade delivered
                # capacity away for a few watts of fan saving, which is not what
                # a real unit does: once it cannot meet the load it runs flat
                # out. Rank by shortfall instead, so the reported operating point
                # is the machine's maximum capacity at this condition -- the
                # quantity a capacity-limited hour actually needs to report.
                shortfall: float = max(abs(Q_r_iu) - delivered, 0.0)
                return OBJ_CAPACITY_LIMITED + shortfall

            # Everything below the capacity ceiling is ranked by specific energy
            # rather than raw ``E_tot`` (see `_opt_utils`). At the *lower* clamp
            # -- the compressor speed floor -- every candidate over-delivers, and
            # raw power picks whichever starves the coil hardest: it delivers
            # least, draws least, and looks best. Cost per unit of heat actually
            # delivered does not have that hole. A room cannot store the surplus,
            # but the choice *among* floor candidates is still made on that cost;
            # capping the credit at the request would reduce the ranking to
            # ``E_tot`` and pick the starved coil again. The over-delivery is
            # reported (``capacity_clamped == "min"``, ``Q_ref_iu`` > request) so
            # the caller can treat it as cycling capacity.
            #
            # ``penalty=OBJ_INFEASIBLE`` keeps this function inside the same
            # three-band scale the rest of the search reads: ordinary points come
            # back in watts, a capacity-limited point at ``OBJ_CAPACITY_LIMITED``,
            # and anything the helper rejects at or above ``OBJ_INFEASIBLE``, so
            # the lattice fallback and the warm-start guard still fire. Leaving
            # the helper's own default here would return 1e6 for an unreachable
            # point, which both guards would read as a perfectly good solution.
            #
            # A minimum-speed candidate is met by cycling, so its cost is the
            # mean input for the *requested* heat: the specific energy divided
            # by the part-load factor at this candidate's capacity ratio.
            return specific_energy_objective(
                E_tot,
                delivered,
                abs(Q_r_iu),
                math.inf,
                penalty=OBJ_INFEASIBLE,
            ) * self._cycling_cost_factor(perf, Q_r_iu)

        # Phase 0: a constant-speed fan. The approaches it can serve are a
        # single point, which no grid or simplex lands on, so the point is
        # solved from the coil capacity margins (:class:`_NarrowFanSearch`).
        # Nothing is left for the phases below to find or to refine.
        if self._has_constant_speed_fan():
            search = _NarrowFanSearch(self, Q_r_iu, T0, T_a_room)
            search.run()
            value, x, _ = search.best()
            lo_b, hi_b = self.dT_approach_bounds
            middle = 0.5 * (lo_b + hi_b)
            return OptimizeResult(
                x=np.array(x if x is not None else (middle, middle), dtype=float),
                fun=value,
                success=value < OBJ_INFEASIBLE,
                nfev=len(search.rows),
            )

        # Phase 1 seed: the least-lift point (both coils at their largest
        # airflow). It is the widest place of the feasible set -- whatever the
        # envelope or the speed range leaves of it contains this point -- so a
        # feasible set thinner than any fixed candidate spacing (about 1 K
        # wide for the default unit at -25 degC) is still entered. A unit that is
        # short of the request even here is flat out: there is nothing to
        # search, and the ceiling is the caller's (:meth:`max_capacity`).
        seed: tuple[float, float] | None = None
        seed_reason: str | None = None
        with contextlib.suppress(Exception):
            seed, seed_state, seed_reason = self._largest_airflow_point(
                Q_r_iu, T0, T_a_room, rtol=self._LEAST_LIFT_SEED_RTOL
            )
            flat_out = seed_state is not None and seed_state.get("capacity_clamped") == CAPACITY_CLAMPED_MAX
            if seed is not None and seed_state is not None and flat_out:
                shortfall = max(abs(Q_r_iu) - abs(float(seed_state["Q_ref_iu [W]"])), 0.0)
                return OptimizeResult(
                    x=np.array(seed, dtype=float), fun=OBJ_CAPACITY_LIMITED + shortfall, success=True, nfev=0
                )

        # Phase 1: coarse grid pre-scan to find a converging starting point.
        # A single fixed x0=(15,15) fails silently when the entire search space
        # is a penalty region — Nelder-Mead cannot escape because all objective
        # evaluations return the same sentinel. Scanning a coarse grid first
        # finds a valid basin (if one exists) so Phase 2 refines from there.
        _candidates = [
            (3.0, 3.0),
            (5.0, 5.0),
            (8.0, 8.0),
            (12.0, 12.0),
            (15.0, 15.0),
            (3.0, 8.0),
            (8.0, 3.0),
            (5.0, 12.0),
            (12.0, 5.0),
            (10.0, 10.0),
        ]
        best_x0 = [15.0, 15.0]
        best_val = OBJ_INFEASIBLE
        for cand in _candidates:
            val = _objective(cand)
            if val < best_val:
                best_val = val
                best_x0 = list(cand)

        # Phase 1a: a fan with a narrow flow range, paid only when the pre-scan
        # found nothing. The band of approaches it can serve may be thinner
        # than the spacing of the lattices below, so it is solved rather than
        # sampled; the point found seeds Phase 2.
        if best_val >= OBJ_INFEASIBLE and self._has_narrow_fan():
            search = _NarrowFanSearch(self, Q_r_iu, T0, T_a_room)
            search.run()
            value, x, _ = search.best()
            if value < OBJ_INFEASIBLE and x is not None:
                best_val, best_x0 = value, list(x)

        # Phase 1b: fallback lattice, paid only when Phase 1a found nothing.
        #
        # The candidates above cluster at small approach temperatures, which is
        # where a comfortably-met load sits. A load near the machine's ceiling is
        # feasible only in a narrow band at large approaches on both sides
        # (measured for a 18 kW R410A unit at 2 degC: dT_evap 13-18 K with
        # dT_cond 15-20 K, a few percent of the domain). The reason is the fan:
        # at a small approach the coil must move the heat with a temperature
        # difference it does not have, so the airflow search runs into its rated
        # ceiling and the state is rejected. Every candidate above lies outside
        # that band, so without this sweep a near-ceiling hour finds no feasible
        # point, falls back to HP-off and reports zero output -- understating
        # capacity exactly where capacity is the quantity of interest.
        # A second, finer lattice runs only if the first finds nothing: with the
        # discharge-temperature limit the feasible band can shrink to a few
        # kelvin between the coil's airflow limit (small approaches) and
        # T_dis_max (large ones) -- 2 x 2.5 K for Daikin RXM42A at -15 degC.
        # Not paid either when the least-lift point is outside the envelope
        # or beyond a coil: every other approach pair asks more of both.
        beyond_reach = seed_reason in self._ENVELOPE_BINDING or seed_reason == self._AIR_SIDE
        for rungs in (8, 16):
            if best_val < OBJ_INFEASIBLE or beyond_reach:
                break
            lo_b, hi_b = self.dT_approach_bounds
            lattice = tuple(float(v) for v in np.linspace(lo_b, hi_b, rungs))
            for de in lattice:
                for dc in lattice:
                    val = _objective((de, dc))
                    if val < best_val:
                        best_val = val
                        best_x0 = [de, dc]

        # Phase 2: Nelder-Mead refinement from the best candidate found above.
        def refine(x0) -> OptimizeResult:
            return minimize(
                _objective,
                x0=x0,
                bounds=[self.dT_approach_bounds, self.dT_approach_bounds],
                method="Nelder-Mead",
                options={"maxiter": 200, "xatol": 1e-3, "fatol": 1e-1},
            )

        seed_val = _objective(seed) if seed is not None else OBJ_INFEASIBLE
        if best_val >= OBJ_INFEASIBLE:
            # No candidate above runs; the least-lift point does if anything does.
            result = refine(list(seed)) if seed is not None and seed_val < OBJ_INFEASIBLE else refine(best_x0)
            # Why the least-lift point does not run is why the request does not.
            result["least_lift_reason"] = seed_reason
            return result
        result = refine(best_x0)
        if seed is not None and seed_val < float(result.fun):
            # The refinement stopped above the least-lift point's own cost: it
            # sits on a flat stretch of the objective (the discharge-temperature
            # control makes the input nearly independent of the outdoor
            # approach) or in another basin. Refine from the least-lift point
            # too and take it when it is materially cheaper -- by more than the
            # two refinements differ when they end in the same place.
            from_seed = refine(list(seed))
            if float(from_seed.fun) < (1.0 - self._SEED_GAIN) * float(result.fun):
                result = from_seed
        return result

    # =============================================================
    # Part load: cycling below the minimum capacity, unmet load above the maximum
    # =============================================================

    def _cycling_cost_factor(self, perf: dict, Q_r_iu: float) -> float:
        """Mean electrical input of a candidate relative to its continuous input (>= 1).

        1.0 unless the candidate sits on the compressor's speed floor and
        cycling is modelled; there ``1 / PLF`` at the candidate's capacity
        ratio (:class:`~tmhp.cycling.CyclingDegradation`).
        """
        if self.cycling is None or perf.get("capacity_clamped") != CAPACITY_CLAMPED_MIN:
            return 1.0
        delivered = abs(float(perf.get("Q_ref_iu [W]", math.nan)))
        if not (math.isfinite(delivered) and delivered > 0.0):
            return 1.0
        return 1.0 / self.cycling.part_load_factor(min(1.0, abs(Q_r_iu) / delivered))

    #: Keys every row carries whatever path the point took, with the value on
    #: a row they do not apply to (``None``: a name, NaN: a number) -- the
    #: convention of the boilers' rows (:mod:`tmhp._boiler_results`). A
    #: dynamic frame is the union of its rows, so without them its columns
    #: depend on what the run happened to do.
    _ROW_DEFAULTS: Mapping[str, Any] = {
        "capacity_binding": None,
        "Q_max [W]": math.nan,
        "n_star [-]": math.nan,
        "pr_cmp [-]": math.nan,
        "eta_cmp_isen [-]": math.nan,
        "eta_cmp_vol [-]": math.nan,
        "eta_cmp [-]": math.nan,
    }

    def _complete_row(self, result: dict) -> dict:
        """Give a finished row the keys every row has, and its condenser dew point (in place).

        Adds what the row's path did not write (:attr:`_ROW_DEFAULTS`: the
        ceiling keys of a row that is not at a ceiling, the compressor keys
        of an off or failed row). ``T_ref_cond_sat_v [°C]`` becomes the dew
        point of the condensing pressure for a zeotropic blend
        (:func:`~tmhp._boiler_results.report_condenser_dew_point`); for a pure
        refrigerant nothing changes. No cycle or control quantity is touched.
        """
        fill_missing(result, self._ROW_DEFAULTS)
        return report_condenser_dew_point(result, self.ref)

    def _report_part_load(self, result: dict, Q_r_iu: float) -> None:
        """Write the part-load keys of a solved row and average a cycling one (in place).

        Every row gets ``Q_request [W]`` (signed, as asked), ``Q_unmet [W]``
        (how far the delivered heat falls short of the request, >= 0) and the
        cycling keys of :mod:`tmhp.cycling`. A row on the compressor's speed
        floor is averaged over the cycling period; one at its ceiling gains
        ``Q_max [W]`` and ``capacity_binding`` when the search did not already
        set them.
        """
        request = abs(float(Q_r_iu))
        running = bool(result.get("hp_is_on", False)) and result.get("failure_reason", "none") == "none"
        delivered = abs(float(result.get("Q_ref_iu [W]", 0.0))) if running else 0.0
        clamp = result.get("capacity_clamped") if running else None
        if clamp == CAPACITY_CLAMPED_MIN and self.cycling is not None and 0.0 < request < delivered:
            state = self.cycling.state(request, delivered)
            apply_cycling(
                result,
                state,
                delivered_key="Q_ref_iu [W]",
                rate_keys=self._CYCLING_RATE_KEYS,
                power_keys=self._CYCLING_POWER_KEYS,
                delivery_power_keys=("E_iu_fan [W]",),
            )
            result["cop_ref [-]"] = result["Q_ref_iu [W]"] / result["E_cmp [W]"]
            if math.isfinite(float(result["cop_sys [-]"])):
                result["cop_sys [-]"] = abs(result["Q_a_iu_out [W]"] - result["Q_a_iu_in [W]"]) / result["E_tot [W]"]
            delivered = abs(float(result["Q_ref_iu [W]"]))
        else:
            continuous_keys(
                result,
                CONTINUOUS if running else OFF,
                delivered if clamp == CAPACITY_CLAMPED_MIN else math.nan,
            )
        result["Q_request [W]"] = float(Q_r_iu)
        unmet = max(request - delivered, 0.0)
        result["Q_unmet [W]"] = unmet if unmet > 1e-9 * request else 0.0
        if clamp == CAPACITY_CLAMPED_MAX:
            result.setdefault("Q_max [W]", delivered)
            result.setdefault("capacity_binding", "compressor")

    # =============================================================
    # Fans with little or no flow range
    # =============================================================

    def _fan_flow_ratios(self) -> tuple[float, float]:
        """Largest over smallest airflow of the indoor and the outdoor fan (1.0: constant speed)."""

        def ratio(low: float, high: float) -> float:
            return high / low if low > 0 else math.inf

        return (
            ratio(self.dV_iu_fan_a_min, self.dV_iu_fan_a_max),
            ratio(self.dV_ou_fan_a_min, self.dV_ou_fan_a_max),
        )

    def _has_narrow_fan(self) -> bool:
        """Whether either fan's flow range can be too narrow for the grid search."""
        return min(self._fan_flow_ratios()) <= NARROW_FAN_FLOW_RATIO

    def _has_constant_speed_fan(self) -> bool:
        """Whether either fan runs at one airflow only (``min == max``)."""
        return min(self._fan_flow_ratios()) == 1.0

    def _coil_margins(self, perf: dict | None) -> tuple[float, float, float, float] | None:
        """Signed capacity margins of both coils at their fan flow limits [W].

        ``(indoor at smallest flow, indoor at largest flow, outdoor at smallest
        flow, outdoor at largest flow)`` for a state of :meth:`_calc_state`:
        coil capacity at that airflow minus the refrigerant-side duty the
        cycle asks of it. A coil carries its duty at some airflow within the
        limits when the first of its pair is ``<= 0`` and the second ``>= 0``.
        ``None`` when the cycle itself does not exist.
        """
        if perf is None:
            return None
        if "_iu_diag" in perf:
            # A coil did not close: the coil solver reports the margins.
            values = tuple(
                float(perf[diag].get(key, math.nan))
                for diag in ("_iu_diag", "_ou_diag")
                for key in ("min_flow_capacity_margin_W", "max_flow_capacity_margin_W")
            )
        else:
            # Both coils closed, so each temperature difference follows from
            # the duty and the solved airflow.
            def limits(
                duty: float,
                flow: float,
                T_in: float,
                UA_rated: float,
                reference: float,
                n: float,
                low: float,
                high: float,
            ):
                def per_kelvin(dV: float) -> float:
                    C_air = float(air_heat_capacity_rate(dV, T_in))
                    return C_air * (1.0 - math.exp(-UA_rated * (dV / reference) ** n / C_air))

                if not (duty > 0.0 and flow > 0.0):
                    return math.nan, math.nan
                dT = duty / per_kelvin(flow)
                return per_kelvin(low) * dT - duty, per_kelvin(high) * dT - duty

            values = (
                *limits(
                    abs(float(perf.get("Q_ref_iu [W]", math.nan))),
                    float(perf.get("dV_iu_a [m3/s]", math.nan)),
                    float(perf.get("T_iu_a_in [°C]", math.nan)),
                    self.UA_iu_rated,
                    self.dV_iu_fan_a_ref,
                    self.n_iu,
                    self.dV_iu_fan_a_min,
                    self.dV_iu_fan_a_max,
                ),
                *limits(
                    abs(float(perf.get("Q_ref_ou [W]", math.nan))),
                    float(perf.get("dV_ou_a [m3/s]", math.nan)),
                    float(perf.get("T_ou_a_in [°C]", math.nan)),
                    self.UA_ou_rated,
                    self.dV_ou_fan_a_ref,
                    self.n_ou,
                    self.dV_ou_fan_a_min,
                    self.dV_ou_fan_a_max,
                ),
            )
        if not all(math.isfinite(v) for v in values):
            return None
        return values  # type: ignore[return-value]

    # =============================================================
    # Frost on the outdoor coil and its defrost
    # =============================================================

    def _frost_state(self, Q_r_iu: float, T0: float, RH_out: float | None, *, full_load: bool = False) -> FrostState:
        """Frost correction for this request (:data:`~tmhp.defrost.NO_FROST` outside heating).

        With a load-weighted frost model the coil is estimated at the load
        ratio of the request (:meth:`~tmhp.defrost.DefrostModel.frost_state_at_duty`);
        ``full_load`` asks for the rated-load state instead (the capacity
        ceiling, where ``Q_r_iu`` only carries the mode). Without load
        weighting both are ``frost_state(T0, RH_out)``.
        """
        if self.defrost is None or not Q_r_iu < 0:
            return NO_FROST
        if full_load:
            return self.defrost.frost_state(T0, RH_out)
        return self.defrost.frost_state_at_duty(T0, RH_out, abs(Q_r_iu), self.hp_capacity)

    def _frost_state_of_running(self, result: dict, frost: FrostState, T0: float) -> FrostState:
        """Frost state of the machine a frost-free row runs, given the state of its request.

        The same state, unless the frost model is load-weighted and the row
        sits on the compressor's speed floor: the machine then runs at
        ``Q_min [W]``, more than the duty the load ratio was taken from, and
        the coil is loaded by what the running machine absorbs. A request too
        small to frost by its own ratio is frosted there if ``Q_min`` is.
        """
        model = self.defrost
        running = bool(result.get("hp_is_on", False)) and result.get("failure_reason", "none") == "none"
        if model is None or not model.load_weighted or not running:
            return frost
        if result.get("capacity_clamped") != CAPACITY_CLAMPED_MIN:
            return frost
        q_min = abs(float(result.get(MIN_CAPACITY_KEY, math.nan)))
        if not (math.isfinite(q_min) and q_min > 0.0):
            return frost
        ratio = model.load_ratio(q_min, self.hp_capacity, T0)
        if ratio <= frost.load_ratio:
            return frost
        return model.frost_state(T0, frost.RH_out, load_ratio=ratio)

    def _write_frost_free(self, result: dict, frost: FrostState) -> None:
        """Frost keys of a row the correction left alone (in place)."""
        frost_free_keys(result, frost)
        result[self._FROST_FREE_HEAT_KEY] = float(result.get("Q_ref_iu [W]", 0.0))
        result[self._FROST_FREE_POWER_KEY] = float(result.get("E_tot [W]", 0.0))

    def _average_over_frost(self, result: dict, frost: FrostState, T0: float, runtime_share: float = 1.0) -> float:
        """Turn a running frost-free row into the mean over frosting and defrost (in place).

        Returns the mean delivered heat [W]: the frost-free heat times the
        capacity multiplier, less what a reverse-cycle defrost takes from the
        room. The indoor fan moves the delivered heat and is averaged with it.
        ``runtime_share`` is the fraction of the time the compressor runs: a
        cycling unit defrosts only while it runs, so the heat its defrosts
        take from the room is that share of the continuous-operation figure.
        """
        model = self.defrost
        assert model is not None
        q_defrost = runtime_share * model.defrost_load(frost, T0, self.hp_capacity)
        heat_frost_free = abs(float(result["Q_ref_iu [W]"]))
        power_frost_free = float(result["E_tot [W]"])
        delivered = max(frost.capacity_multiplier * heat_frost_free - q_defrost, 0.0)
        apply_defrost(
            result,
            frost,
            heat_factor=delivered / heat_frost_free,
            Q_defrost=q_defrost,
            rate_keys=self._FROST_RATE_KEYS,
            outdoor_power_keys=("E_ou_fan [W]",),
            delivery_power_keys=("E_iu_fan [W]",),
        )
        result[self._FROST_FREE_HEAT_KEY] = heat_frost_free
        result[self._FROST_FREE_POWER_KEY] = power_frost_free
        result["cop_ref [-]"] = result["Q_ref_iu [W]"] / result["E_cmp [W]"] if result["E_cmp [W]"] > 0 else np.nan
        if math.isfinite(float(result.get("cop_sys [-]", np.nan))) and result["E_tot [W]"] > 0:
            result["cop_sys [-]"] = abs(result["Q_a_iu_out [W]"] - result["Q_a_iu_in [W]"]) / result["E_tot [W]"]
        return delivered

    def _analyze_steady_frosted(
        self,
        frost: FrostState,
        Q_r_iu: float,
        T0: float,
        T_a_room: float,
        *,
        postprocess: bool,
        verbose: bool,
    ) -> dict:
        """Heating with a frosting outdoor coil: the mean over frosting and defrost.

        The frost-free cycle is solved at the duty whose mean is the request,
        ``(|Q_r_iu| + Q_defrost) / capacity multiplier``, with everything that
        applies to a frost-free solve (speed limits, cycling, the capacity
        ceiling), and the row is then averaged (:func:`~tmhp.defrost.apply_defrost`).
        """
        model = self.defrost
        assert model is not None
        q_defrost = model.defrost_load(frost, T0, self.hp_capacity)
        gross = model.gross_duty(frost, Q_r_iu, q_defrost)

        def frost_free(duty: float) -> dict:
            self.defrost = None
            try:
                solved = self.analyze_steady(
                    Q_r_iu=-duty, T0=T0, T_a_room=T_a_room, return_dict=True, postprocess=False, verbose=verbose
                )
            finally:
                self.defrost = model
            assert isinstance(solved, dict)
            return solved

        result = frost_free(gross)
        # On its speed floor the machine runs at Q_min, more than the duty the
        # load ratio was taken from (`_frost_state_of_running`): the frost is
        # that of Q_min, and the row is solved again with it.
        on_floor = self._frost_state_of_running(result, frost, T0)
        if on_floor is not frost:
            frost = on_floor
            q_defrost = model.defrost_load(frost, T0, self.hp_capacity)
            gross = model.gross_duty(frost, Q_r_iu, q_defrost)
            result = frost_free(gross)
        request = abs(float(Q_r_iu))
        running = bool(result.get("hp_is_on", False)) and result.get("failure_reason", "none") == "none"

        # Below the minimum continuous capacity the unit cycles, and it
        # defrosts only while it runs: the defrost heat loss is the runtime
        # fraction of the continuous-operation figure, not all of it. The
        # frost-free duty g then satisfies
        #     capacity multiplier * g - Q_defrost * runtime(g / Q_min) = request,
        # a scalar balance (no cycle solve) once Q_min is known. Without it a
        # vanishing request would still pay the whole defrost loss.
        runtime_share = 1.0
        cycling = self.cycling
        if (
            running
            and cycling is not None
            and q_defrost > 0.0
            and result.get("capacity_clamped") == CAPACITY_CLAMPED_MIN
        ):
            multiplier = frost.capacity_multiplier
            low = request / multiplier
            q_min_used = math.nan
            # Q_min moves a little with the duty (the fans' optimum follows the
            # cycling cost), so the balance is re-solved with the Q_min of the
            # row it produced until the two agree; two passes settle it.
            for _ in range(4):
                q_min = float(result.get(MIN_CAPACITY_KEY, math.nan))
                if not (running and math.isfinite(q_min) and q_min > 0.0):
                    break
                if math.isfinite(q_min_used) and abs(q_min - q_min_used) <= 1e-9 * q_min:
                    break

                def balance(duty: float, q_min: float = q_min) -> float:
                    return multiplier * duty - q_defrost * cycling.runtime_fraction(duty / q_min) - request

                if not (low < gross and balance(low) < 0.0 < balance(gross)):
                    break
                duty = float(brentq(balance, low, gross, xtol=1e-12 * max(gross, 1.0), rtol=1e-13))
                q_min_used = q_min
                runtime_share = cycling.runtime_fraction(duty / q_min)
                result = frost_free(duty)
                running = bool(result.get("hp_is_on", False)) and result.get("failure_reason", "none") == "none"

        delivered = 0.0
        if running:
            delivered = self._average_over_frost(result, frost, T0, runtime_share)
            if result.get("capacity_clamped") == CAPACITY_CLAMPED_MAX and "Q_max [W]" in result:
                result["Q_max [W]"] = delivered
        else:
            self._write_frost_free(result, frost)
        # The request and its shortfall are the caller's, not the frost-free solve's.
        if "Q_request [W]" in result:
            result["Q_request [W]"] = float(Q_r_iu)
        if "Q_unmet [W]" in result:
            unmet = max(request - delivered, 0.0)
            result["Q_unmet [W]"] = unmet if unmet > 1e-9 * request else 0.0

        if postprocess:
            result = self._exergy_of_row(result)
        return result

    # =============================================================
    # Capacity envelope
    # =============================================================

    #: A request of this many nameplates pins the compressor at its speed ceiling.
    _FLAT_OUT_FACTOR = 1.0e3
    #: Relative step at which the approaches of :meth:`_largest_airflow_point` are converged.
    _LEAST_LIFT_RTOL = 1e-9
    #: Least gain in specific energy for which the refinement from the least-lift
    #: point replaces the one from the pre-scan (the refinements stop at 0.1 W,
    #: about 2e-4 of a typical input).
    _SEED_GAIN = 1e-3
    #: Starting approaches of :meth:`_largest_airflow_point`, as fractions of the bounds' span.
    _LEAST_LIFT_STARTS: tuple[float, ...] = (0.0, 0.1, 0.2, 0.35, 0.55)
    #: The same for the least-lift point used as a starting point of the search.
    _LEAST_LIFT_SEED_RTOL = 1e-6
    #: Relative step from the converged approaches into the fans' flow range.
    _LEAST_LIFT_INSIDE = 1e-7
    #: Iterations of :meth:`_largest_airflow_point` (it contracts by 0.1 to 0.5 a step).
    _LEAST_LIFT_MAX_ITER = 80
    #: Smallest fraction a step of :meth:`_largest_airflow_point` is cut to before
    #: the point is taken to lie outside the compressor envelope.
    _LEAST_LIFT_MIN_STEP = 1.0 / 16.0
    #: ``capacity_binding`` of a ceiling set by a compressor-envelope limit
    #: (the names the water-heating models use).
    _ENVELOPE_BINDING: Mapping[str, str] = {
        PR_ABOVE_MAX: BINDING_PRESSURE_RATIO,
        CONDENSING_TEMP_ABOVE_MAX: BINDING_CONDENSING,
        DISCHARGE_TEMP_ABOVE_MAX: BINDING_DISCHARGE,
        ETA_ISEN_BELOW_MIN: BINDING_EFFICIENCY,
    }
    #: Why :meth:`_largest_airflow_point` has no state although the cycle exists:
    #: a coil cannot carry its duty inside the approach bounds.
    _AIR_SIDE = "air_side"
    #: Fractions of a duty tried, from the top, when that duty has no operating point.
    _CEILING_LADDER: tuple[float, ...] = (0.8, 0.6, 0.45, 0.3, 0.2, 0.12, 0.06)
    #: Relative width at which the bisection for a refused request stops.
    _REDUCTION_REL_TOL = 1e-3

    def _largest_airflow_approaches(self, x_iu: float, x_ou: float, perf: dict) -> tuple[float, float] | None:
        """Approaches at which each coil would carry this state's duty at its largest airflow [K].

        ``(indoor, outdoor)``, kept inside the approach bounds. At a fixed
        airflow a coil's capacity is proportional to its temperature
        difference, so the approach that closes the coil at its largest
        airflow is the present one times duty over capacity there. ``perf`` is
        a state of :meth:`_calc_state` whose cycle exists; its coils need not
        have closed. ``None`` when a duty or a capacity is not a positive
        number.
        """
        if "_iu_diag" in perf:
            pairs = []
            for diag in (perf["_iu_diag"], perf["_ou_diag"]):
                margin = float(diag.get("max_flow_capacity_margin_W", math.nan))
                duty = float(diag.get("Q_air", math.nan)) - float(diag.get("capacity_margin_W", math.nan))
                pairs.append((duty, margin))
        else:
            # Both coils closed, so each temperature difference follows from
            # the duty and the solved airflow (as in :meth:`_coil_margins`,
            # which also needs the smallest airflow -- zero for a fan that
            # can stop).
            def at_largest_airflow(
                duty: float, flow: float, T_in: float, UA_rated: float, reference: float, n: float, high: float
            ):
                def per_kelvin(dV: float) -> float:
                    C_air = float(air_heat_capacity_rate(dV, T_in))
                    return C_air * (1.0 - math.exp(-UA_rated * (dV / reference) ** n / C_air))

                if not (duty > 0.0 and flow > 0.0):
                    return math.nan, math.nan
                return duty, per_kelvin(high) * duty / per_kelvin(flow) - duty

            pairs = [
                at_largest_airflow(
                    abs(float(perf.get("Q_ref_iu [W]", math.nan))),
                    float(perf.get("dV_iu_a [m3/s]", math.nan)),
                    float(perf.get("T_iu_a_in [°C]", math.nan)),
                    self.UA_iu_rated,
                    self.dV_iu_fan_a_ref,
                    self.n_iu,
                    self.dV_iu_fan_a_max,
                ),
                at_largest_airflow(
                    abs(float(perf.get("Q_ref_ou [W]", math.nan))),
                    float(perf.get("dV_ou_a [m3/s]", math.nan)),
                    float(perf.get("T_ou_a_in [°C]", math.nan)),
                    self.UA_ou_rated,
                    self.dV_ou_fan_a_ref,
                    self.n_ou,
                    self.dV_ou_fan_a_max,
                ),
            ]
        lo, hi = self.dT_approach_bounds
        target = []
        for x, (duty, margin) in zip((x_iu, x_ou), pairs, strict=True):
            capacity = duty + margin
            if not (math.isfinite(duty) and math.isfinite(capacity) and duty > 0.0 and capacity > 0.0):
                return None
            target.append(min(max(x * duty / capacity, lo), hi))
        return target[0], target[1]

    def _largest_airflow_point(
        self,
        Q_r_iu: float,
        T0: float,
        T_a_room: float,
        start: tuple[float, float] | None = None,
        rtol: float | None = None,
    ) -> tuple[tuple[float, float] | None, dict | None, str | None]:
        """The operating point with both coils at their largest airflow: the least lift for a duty.

        A coil carries a duty with the smallest temperature difference at its
        largest airflow, so this is the point of the highest evaporating and
        the lowest condensing temperature at which the unit delivers
        ``Q_r_iu`` -- the smallest pressure ratio, the coolest discharge and
        the slowest compressor of all the approach pairs that deliver it. If
        this point is outside the compressor envelope, or needs more speed
        than ``rps_max``, so is every other. That makes it the test of whether
        a duty is deliverable at all, the state of a unit running flat out
        (a request the compressor cannot reach: speed at ``rps_max``, both
        fans at full flow), and a starting point for the least-power search
        that is inside the feasible set whenever there is one.

        Solved as a fixed point: each approach is replaced by the one at which
        its coil carries the cycle's duty at its largest airflow
        (:meth:`_largest_airflow_approaches`) until both stand still. An
        approach the bounds do not admit stays on the bound; on the lower one
        the fan simply runs below its largest airflow. A step into a state the
        cycle does not have is halved, and the point is outside the envelope
        when the cut reaches :attr:`_LEAST_LIFT_MIN_STEP`.

        ``start`` is ``(dT_ref_evap, dT_ref_cond)`` of a neighbouring solution.
        ``rtol`` is the relative step at which the approaches are converged
        (:attr:`_LEAST_LIFT_RTOL` by default; a search that only wants a
        starting point passes a looser one).

        Returns ``((dT_ref_evap, dT_ref_cond), state, reason)``. ``state`` is
        the closed state of :meth:`_calc_state` (``capacity_clamped`` tells
        whether the compressor met the duty) and ``reason`` is ``None``; or
        ``state`` is ``None`` and ``reason`` says why: the envelope limit the
        cycle ran into (``"pr_above_max"``, ``"discharge_temp_above_max"``,
        ``"eta_isen_below_min"``), :attr:`_AIR_SIDE` when a coil cannot carry
        its duty inside the approach bounds, or ``"cycle_invalid"``.
        """
        lo, hi = self.dT_approach_bounds
        heating = Q_r_iu < 0
        rtol = self._LEAST_LIFT_RTOL if rtol is None else rtol
        # Far enough inside to be clear of what the convergence left open.
        step_inside = max(self._LEAST_LIFT_INSIDE, 100.0 * rtol)

        def approaches(x: tuple[float, float]) -> tuple[float, float]:
            return (x[1], x[0]) if heating else (x[0], x[1])

        def evaluate(x: tuple[float, float]) -> dict | None:
            de, dc = approaches(x)
            return self._calc_state(dT_ref_evap=de, dT_ref_cond=dc, Q_r_iu=Q_r_iu, T0=T0, T_a_room=T_a_room)

        def missing() -> str:
            event = self._last_pr_event
            return event[0] if event is not None and event[0] != "pr_below_min" else "cycle_invalid"

        # Where to start: a neighbouring solution, else the smallest approaches
        # (the least lift the search admits). A fluid can lack a state at one
        # candidate (a flash CoolProp cannot solve), so a few larger approaches
        # are tried before the cycle is taken not to exist.
        starts: list[tuple[float, float]] = []
        if start is not None:
            starts.append(approaches((min(max(start[0], lo), hi), min(max(start[1], lo), hi))))
        starts.extend((lo + f * (hi - lo),) * 2 for f in self._LEAST_LIFT_STARTS)
        x: tuple[float, float] = starts[0]
        perf = None
        first_reason: str | None = None
        for x in starts:
            perf = evaluate(x)
            if perf is not None:
                break
            first_reason = first_reason or missing()
        if perf is None:
            return None, None, first_reason

        for _ in range(self._LEAST_LIFT_MAX_ITER):
            target = self._largest_airflow_approaches(x[0], x[1], perf)
            if target is None:
                return None, None, "cycle_invalid"
            if all(abs(t - v) <= rtol * v for t, v in zip(target, x, strict=True)):
                break
            step = 1.0
            while True:
                trial = (x[0] + step * (target[0] - x[0]), x[1] + step * (target[1] - x[1]))
                trial_perf = evaluate(trial)
                if trial_perf is not None:
                    break
                reason = missing()
                step *= 0.5
                if step < self._LEAST_LIFT_MIN_STEP:
                    return None, None, reason
            x, perf = trial, trial_perf
        else:
            return None, None, "cycle_invalid"

        # The fixed point puts each coil exactly on its largest airflow, where
        # the coil solve closes or not by the sign of a rounding error (a fan
        # with its own power curve is closed with no tolerance). A step inside
        # -- far below any tolerance of the cycle -- makes the closure definite.
        inside = (min(x[0] * (1.0 + step_inside), hi), min(x[1] * (1.0 + step_inside), hi))
        closed = evaluate(inside)
        if closed is None:
            return None, None, missing()
        if not closed.get("converged", False):
            # Short of its duty at its largest airflow: the coil is beyond its
            # reach inside the approach bounds, and so is every approach pair.
            # A coil that still carries too much at its *smallest* airflow is
            # another matter -- the pressure-ratio floor has lifted the
            # condensing temperature off the approach -- and says nothing
            # about the other pairs.
            short = any(
                not diag.get("converged", True) and float(diag.get("max_flow_capacity_margin_W", 0.0)) < 0.0
                for diag in (closed.get("_iu_diag", {}), closed.get("_ou_diag", {}))
            )
            return approaches(inside), None, self._AIR_SIDE if short else "cycle_invalid"
        return approaches(inside), closed, None

    def _indoor_coil_limit(self, T_a_room: float) -> float:
        """Largest duty the indoor coil carries inside the approach bounds [W].

        Its largest airflow, of room air at ``T_a_room`` [°C], at the widest
        approach the search admits. The
        request is the indoor coil's duty in either mode, so no larger request
        has an operating point, whatever the compressor can do. The value is
        a hair inside the limit (2e-7 of it), so that the coil closes there by
        more than a rounding error (see :meth:`_largest_airflow_point`).
        """
        dV = self.dV_iu_fan_a_max
        C_air = float(air_heat_capacity_rate(dV, T_a_room))
        UA = self.UA_iu_rated * (dV / self.dV_iu_fan_a_ref) ** self.n_iu
        limit = C_air * (1.0 - math.exp(-UA / C_air)) * self.dT_approach_bounds[1]
        return limit / (1.0 + 2.0 * self._LEAST_LIFT_INSIDE)

    def _largest_deliverable_duty(
        self, sign: float, q_top: float, T0: float, T_a_room: float, rel_tol: float
    ) -> tuple[tuple[float, float] | None, dict | None, str | None]:
        """Largest duty not above ``q_top`` whose least-lift point is an operating point.

        A unit asked for more than a limit allows does not stop; its
        controller holds the compressor back until the limit holds. ``q_top``
        itself is tried first; otherwise a ladder of smaller duties finds one
        that runs and a bisection closes on the limit above it (to
        ``rel_tol`` of the duty). The limits are not monotone in the duty --
        at low speed the efficiency falls and the discharge runs hotter -- so
        this is the boundary nearest ``q_top``, as in
        :func:`~tmhp.compressor_envelope.reduce_duty_within_envelope`.

        Returns ``((dT_ref_evap, dT_ref_cond), state, reason)``. ``reason`` is
        ``None`` when ``q_top`` is deliverable, else why the next larger duty
        is not (see :meth:`_largest_airflow_point`; ``"compressor"`` for the
        speed range). ``(None, None, reason)`` when no duty runs.
        """

        def trial(q: float, start: tuple[float, float] | None = None):
            x, perf, reason = self._largest_airflow_point(sign * q, T0, T_a_room, start=start)
            if perf is not None and perf.get("capacity_clamped") == CAPACITY_CLAMPED_MAX:
                return x, None, "compressor"
            return x, perf, reason

        x, perf, reason_hi = trial(q_top)
        if x is not None and perf is not None:
            return x, perf, None
        q_hi = q_top
        q_lo = 0.0
        x_lo: tuple[float, float] | None = None
        perf_lo: dict | None = None
        for fraction in self._CEILING_LADDER:
            q = fraction * q_top
            x, perf, reason = trial(q)
            if x is not None and perf is not None:
                q_lo, x_lo, perf_lo = q, x, perf
                break
            q_hi, reason_hi = q, reason
        if x_lo is None or perf_lo is None:
            return None, None, reason_hi
        while q_hi - q_lo > rel_tol * q_hi:
            mid = 0.5 * (q_lo + q_hi)
            x, perf, reason = trial(mid, start=x_lo)
            if x is not None and perf is not None:
                q_lo, x_lo, perf_lo = mid, x, perf
            else:
                q_hi, reason_hi = mid, reason
        return x_lo, perf_lo, reason_hi

    def _binding_of(self, reason: str | None) -> str:
        """``capacity_binding`` for the reason the next larger duty has no operating point.

        ``"cycle_invalid"`` -- the next larger duty has no cycle the model can
        represent, in practice a condensing temperature at the critical point
        -- is not a binding a unit runs on: :meth:`analyze_steady` does not
        settle there (as the water-heating models do not; see
        :mod:`tmhp.compressor_envelope`), and only :meth:`max_capacity`
        reports it.
        """
        if reason == "compressor":
            return "compressor"
        if reason == self._AIR_SIDE:
            return BINDING_CYCLE
        return self._ENVELOPE_BINDING.get(reason or "", "cycle_invalid")

    def max_capacity(
        self,
        T0: float,
        T_a_room: float | None = None,
        *,
        mode: str = "heating",
        grid_points: int | None = None,
        rel_tol: float = 1e-3,
        RH_out: float | None = None,
    ) -> dict:
        """Largest duty the unit can actually deliver at this condition.

        Nameplate capacity is a label attached to one rating point, not a
        ceiling. A real unit delivers more than nameplate when the outdoor air
        is mild and less when it is severe, because the limit is set by
        whichever of the compressor speed range, the compressor envelope and
        the air-side heat exchangers binds first. Clipping a load schedule at
        nameplate therefore errs in both directions at once. This returns the
        ceiling the model itself implies, so a schedule can be clipped against
        the machine rather than against its label and the shortfall reported as
        a result.

        The ceiling is the unit running flat out: the compressor at
        ``rps_max`` and both fans at their largest airflow, which is the least
        lift at which that speed's duty is delivered
        (:meth:`_largest_airflow_point`). Where that point is outside the
        compressor envelope (pressure ratio, discharge temperature even under
        its control, the efficiency floor) or beyond what a coil carries
        inside the approach bounds, the ceiling is the largest duty whose
        least-lift point is an operating point -- a unit's controller holds
        the compressor back to it -- found by bisection on the duty.

        The ceiling is not the largest heat any approach pair delivers at
        ``rps_max``. An adiabatic compressor turns its losses into delivered
        heat, so starving the outdoor coil of air (a lower evaporating
        pressure, a higher pressure ratio, a lower isentropic efficiency) can
        raise the heating duty a little while the COP collapses; no unit runs
        its fans that way.

        .. warning::

            The answer is only as trustworthy as the compressor correlations at
            the speeds it reaches. Those correlations are fits, and a validation
            carried out at part load does not cover the top of the speed range.
            Check that the returned ``cop_sys [-]`` is credible before using the
            ceiling, and consider lowering ``rps_max`` to the highest speed the
            validation actually reached.

        Parameters
        ----------
        T0 : float
            Outdoor air temperature [°C].
        T_a_room : float | None
            Room air temperature [°C]. Uses the constructor default if None.
        mode : str
            ``"heating"`` or ``"cooling"``.
        grid_points : int | None
            Unused. The ceiling was once swept on a grid of approach
            temperatures of this resolution; it is solved now. Accepted so
            that existing calls keep running.
        rel_tol : float
            Relative width at which the bisection on the duty stops when the
            unit cannot run flat out. A flat-out ceiling and one set by the
            indoor coil are exact.
        RH_out : float | None
            Outdoor relative humidity as a fraction (0-1) for the frost
            correction in heating. None: the frost model's default.

        Returns
        -------
        dict
            ``Q_max [W]`` (0.0 when the unit cannot run at all here), the
            approach temperatures there, the electrical input and system COP at
            that point, and ``binding`` naming what stopped it:
            ``"compressor"`` (the speed range), ``"pressure_ratio"``,
            ``"discharge_temperature"`` or ``"compressor_efficiency"`` (the
            compressor envelope: the next larger duty exceeds ``PR_cycle_max``,
            ``T_dis_max`` under its control, or falls below the efficiency
            floor), ``"cycle"`` (a coil at its largest airflow and the widest
            approach the search admits), ``"cycle_invalid"`` (the next larger
            duty has no cycle the model can represent -- a condensing
            temperature at the critical point; :meth:`analyze_steady` does not
            settle on that edge) and ``"infeasible"`` when no duty has an
            operating point.

            In heating with a frosting outdoor coil the ceiling, the input and
            the COP are the mean over frosting and defrost -- what
            :meth:`analyze_steady` delivers there -- and
            ``Q_max_frost_free [W]`` is the frost-free ceiling they come from
            (``defrost_fraction [-]`` > 0). Otherwise the two are equal.
        """
        if mode not in ("heating", "cooling"):
            raise ValueError(f"mode must be 'heating' or 'cooling', got {mode!r}")
        if not (math.isfinite(rel_tol) and rel_tol > 0.0):
            raise ValueError(f"rel_tol must be a positive number, got {rel_tol!r}")
        if T_a_room is None:
            T_a_room = self.T_a_room

        empty = {
            "Q_max [W]": 0.0,
            "dT_ref_evap [K]": float("nan"),
            "dT_ref_cond [K]": float("nan"),
            "E_tot [W]": float("nan"),
            "cop_sys [-]": float("nan"),
            "binding": "infeasible",
            "mode": mode,
            "T0 [°C]": T0,
            "T_a_room [°C]": T_a_room,
            "Q_max_frost_free [W]": 0.0,
            "defrost_fraction [-]": 0.0,
        }

        sign = 1.0 if mode == "cooling" else -1.0
        binding = "compressor"
        lo_x, lo_perf, _ = self._largest_airflow_point(sign * self._FLAT_OUT_FACTOR * self.hp_capacity, T0, T_a_room)
        if lo_x is None or lo_perf is None:
            # Not flat out: no request above the indoor coil's own limit has an
            # operating point, so the ceiling is that limit (known exactly) or
            # the largest duty below it the envelope admits.
            lo_x, lo_perf, reason = self._largest_deliverable_duty(
                sign, self._indoor_coil_limit(T_a_room), T0, T_a_room, rel_tol
            )
            # The limit itself runs: the indoor coil at its largest airflow
            # and the widest approach is what stops the unit.
            binding = BINDING_CYCLE if reason is None else self._binding_of(reason)
        if lo_x is None or lo_perf is None:
            return empty
        lo_q = abs(float(lo_perf["Q_ref_iu [W]"]))

        ceiling = {
            "Q_max [W]": lo_q,
            "dT_ref_evap [K]": lo_x[0],
            "dT_ref_cond [K]": lo_x[1],
            "E_tot [W]": float(lo_perf.get("E_tot [W]", float("nan"))),
            "cop_sys [-]": float(lo_perf.get("cop_sys [-]", float("nan"))),
            "binding": binding,
            "mode": mode,
            "T0 [°C]": T0,
            "T_a_room [°C]": T_a_room,
            "Q_max_frost_free [W]": lo_q,
            "defrost_fraction [-]": 0.0,
        }
        frost = self._frost_state(sign, T0, RH_out, full_load=True)
        if frost.active:
            # The ceiling analyze_steady delivers: the mean over frosting and defrost.
            mean = dict(lo_perf)
            ceiling["Q_max [W]"] = self._average_over_frost(mean, frost, T0)
            ceiling["E_tot [W]"] = float(mean["E_tot [W]"])
            ceiling["cop_sys [-]"] = float(mean["cop_sys [-]"])
            ceiling["defrost_fraction [-]"] = frost.defrost_fraction
        return ceiling

    def _least_power_at_ceiling(self, result: dict, Q_r_iu: float, T0: float, T_a_room: float) -> dict:
        """Least-power operating point at the ceiling duty, when a coil sets the ceiling.

        The ceiling comes from the least-lift point, where both fans run at
        their largest airflow. Where a coil sets the ceiling the compressor is
        clear of its own limits and the other fan has a range to choose from:
        the indoor coil at its largest airflow and widest approach fixes the
        duty, while the outdoor fan can still trade its own power against the
        compressor's (default 4 kW unit at 7 degC: 1.3 % of the input). The
        ceiling duty is therefore solved the ordinary way, so that the row is
        the one a request arriving at the ceiling from below ends at. At the
        speed ceiling or on an envelope limit the least-lift point is the only
        one that delivers the duty, and this is not called.
        """
        delivered = abs(float(result.get("Q_ref_iu [W]", 0.0)))
        power = float(result.get("E_tot [W]", math.nan))
        if not (delivered > 0.0 and math.isfinite(power) and power > 0.0):
            return result
        duty = math.copysign(delivered, Q_r_iu)
        with contextlib.suppress(Exception):
            opt = self._optimize_operation(Q_r_iu=duty, T0=T0, T_a_room=T_a_room)
            if not (bool(getattr(opt, "success", False)) and float(opt.fun) < OBJ_CAPACITY_LIMITED):
                return result
            state = self._calc_state(dT_ref_evap=opt.x[0], dT_ref_cond=opt.x[1], Q_r_iu=duty, T0=T0, T_a_room=T_a_room)
            if state is None or not state.get("converged", False) or state.get("capacity_clamped") is not None:
                return result
            heat, input_power = abs(float(state["Q_ref_iu [W]"])), float(state["E_tot [W]"])
            if abs(heat - delivered) <= 1e-9 * delivered and input_power < power:
                state.update(
                    {
                        "capacity_clamped": CAPACITY_CLAMPED_MAX,
                        "Q_request [W]": Q_r_iu,
                        "Q_max [W]": heat,
                        "capacity_binding": result.get("capacity_binding", BINDING_CYCLE),
                    }
                )
                return state
        return result

    #: ``binding`` values of :meth:`max_capacity` that name a ceiling.
    _CEILING_BINDINGS = frozenset(
        {
            "compressor",
            BINDING_CYCLE,
            BINDING_CONDENSING,
            BINDING_DISCHARGE,
            BINDING_PRESSURE_RATIO,
            BINDING_EFFICIENCY,
        }
    )

    def _capacity_limited_operating_point(self, Q_r_iu: float, T0: float, T_a_room: float) -> dict | None:
        """The state a unit runs at when the request itself has no operating point.

        Above the ceiling (:meth:`max_capacity`) that is the ceiling. A
        request below the ceiling can still be refused -- the envelope is not
        monotone in the duty: at mid speed the efficiency is lower and the
        discharge hotter than flat out -- and the unit then runs at the
        largest duty below the request that has an operating point
        (:meth:`_largest_deliverable_duty`). Either way the state is
        energy-balanced, with ``capacity_clamped == "max"``, ``Q_max [W]`` and
        ``capacity_binding``. ``None`` when the request needs no such state
        or none exists.
        """
        mode = "cooling" if Q_r_iu > 0 else "heating"
        request = abs(Q_r_iu)
        ceiling = self.max_capacity(T0=T0, T_a_room=T_a_room, mode=mode)
        # Anything else ("infeasible", a caller's own label) is not a ceiling.
        if ceiling.get("binding") not in self._CEILING_BINDINGS:
            return None
        # The frost-free ceiling: this state is a cycle state, averaged later.
        q_ceiling = float(ceiling.get("Q_max_frost_free [W]", ceiling["Q_max [W]"]))
        if not math.isfinite(q_ceiling) or not q_ceiling > 0:
            return None
        if q_ceiling < request:
            x = (ceiling["dT_ref_evap [K]"], ceiling["dT_ref_cond [K]"])
            binding = ceiling["binding"]
        else:
            x_below, below, reason = self._largest_deliverable_duty(
                math.copysign(1.0, Q_r_iu), request, T0, T_a_room, self._REDUCTION_REL_TOL
            )
            if x_below is None or below is None or reason is None:
                return None
            x, binding = x_below, self._binding_of(reason)
            if binding not in self._CEILING_BINDINGS:
                return None
            q_ceiling = abs(float(below["Q_ref_iu [W]"]))
            if not 0 < q_ceiling < request:
                return None
        state = self._calc_state(
            dT_ref_evap=x[0],
            dT_ref_cond=x[1],
            Q_r_iu=math.copysign(q_ceiling, Q_r_iu),
            T0=T0,
            T_a_room=T_a_room,
        )
        if state is None or not state.get("converged", False):
            return None
        delivered = float(state["Q_ref_iu [W]"])
        other = float(state["Q_ref_ou [W]"])
        work = float(state["E_cmp [W]"]) * float(state["eta_cmp [-]"])
        cycle_work = (other - delivered) if mode == "cooling" else (delivered - other)
        residuals = (abs(delivered - q_ceiling), abs(cycle_work - work))
        if not all(math.isfinite(value) and value <= 1e-9 * q_ceiling for value in residuals):
            return None
        state.update(
            {
                "capacity_clamped": CAPACITY_CLAMPED_MAX,
                "Q_request [W]": Q_r_iu,
                "Q_max [W]": q_ceiling,
                "capacity_binding": binding,
            }
        )
        return state

    #: Duties tried, as fractions of the nameplate and from the bottom, to find
    #: one that runs above a request the efficiency floor refuses.
    _FLOOR_LADDER: tuple[float, ...] = (0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.6, 0.8, 1.0, 1.5, 2.0)
    #: Relative width at which the bisection for the smallest continuous duty stops.
    _FLOOR_REL_TOL = 5e-3

    def _smallest_continuous_operating_point(self, Q_r_iu: float, T0: float, T_a_room: float) -> dict | None:
        """The smallest duty above a request the efficiency floor refuses, as a continuous state.

        The compressor's speed floor is not the only lower end of continuous
        operation. Where the shipped isentropic fit, extrapolated towards low
        speed, falls below :data:`~tmhp.compressor_envelope.ETA_ISEN_MIN`
        before ``rps_min`` is reached (a large lift: a low-pressure refrigerant
        in severe cold), the envelope refuses every duty below the one at
        which the fit crosses the floor -- while larger duties run. A unit
        does not stay off there: it runs at the smallest duty the envelope
        admits and switches off when the room has had enough, exactly as it
        does below its minimum speed.

        A ladder of duties above the request finds one with an operating
        point and a bisection closes on the smallest (to
        :attr:`_FLOOR_REL_TOL`); every trial is the ordinary search
        (:meth:`_optimize_operation`), so a request at or above the duty
        returned is one :meth:`analyze_steady` solves in continuous
        operation. The state is returned with ``capacity_clamped == "min"``:
        it delivers more than the request and is averaged over the cycling
        period like a minimum-speed row (:meth:`_report_part_load`). ``None``
        when a duty above the request is refused for another reason first, or
        none runs.
        """
        sign, request = math.copysign(1.0, Q_r_iu), abs(float(Q_r_iu))
        # The floor belongs to the condition, not to the request: inside one
        # frost balance or one dynamic run (the model does not change there)
        # it is searched once per condition and reused for smaller requests.
        memo, key = self._floor_memo, (sign, float(T0), float(T_a_room))
        if memo is not None and key in memo and request < abs(float(memo[key]["Q_ref_iu [W]"])):
            return dict(memo[key])

        def trial(q: float) -> tuple[dict | None, str | None]:
            opt = self._optimize_operation(Q_r_iu=sign * q, T0=T0, T_a_room=T_a_room)
            if not (bool(getattr(opt, "success", False)) and float(opt.fun) < OBJ_CAPACITY_LIMITED):
                return None, getattr(opt, "least_lift_reason", None)
            state = self._calc_state(
                dT_ref_evap=opt.x[0], dT_ref_cond=opt.x[1], Q_r_iu=sign * q, T0=T0, T_a_room=T_a_room
            )
            if state is None or not state.get("converged", False) or state.get("capacity_clamped") is not None:
                return None, None
            return state, None

        q_lo, q_hi, running = request, math.nan, None
        for fraction in self._FLOOR_LADDER:
            q = fraction * self.hp_capacity
            if not q > request * (1.0 + self._FLOOR_REL_TOL):
                continue
            state, reason = trial(q)
            if state is not None:
                q_hi, running = q, state
                break
            if reason != ETA_ISEN_BELOW_MIN:
                return None  # another limit is in the way: not a lower end of continuous operation
            q_lo = q
        if running is None:
            return None
        while q_hi - q_lo > self._FLOOR_REL_TOL * q_hi:
            mid = 0.5 * (q_lo + q_hi)
            state, _ = trial(mid)
            if state is not None:
                q_hi, running = mid, state
            else:
                q_lo = mid
        floor = dict(running)
        floor["capacity_clamped"] = CAPACITY_CLAMPED_MIN
        if memo is not None:
            memo[key] = dict(floor)
        return floor

    #: Smallest continuous states found in the frost balance or dynamic run in
    #: progress, by (mode sign, T0, T_a_room); ``None`` outside one.
    _floor_memo: dict[tuple[float, float, float], dict] | None = None

    @contextlib.contextmanager
    def _remembering_floors(self) -> Iterator[None]:
        """Reuse :meth:`_smallest_continuous_operating_point` results while the block runs."""
        outer = self._floor_memo
        if outer is None:
            self._floor_memo = {}
        try:
            yield
        finally:
            self._floor_memo = outer

    # =============================================================
    # Steady-state analysis
    # =============================================================

    @steady_notice(delivered_key="Q_ref_iu [W]")
    @steady_envelope()
    def analyze_steady(
        self,
        Q_r_iu: float,
        T0: float,
        T_a_room: float | None = None,
        *,
        return_dict: bool = True,
        postprocess: bool = True,
        verbose: bool = True,
        RH_out: float | None = None,
    ) -> dict | pd.DataFrame:
        """Run a steady-state performance snapshot.

        Parameters
        ----------
        Q_r_iu : float
            Indoor thermal load [W]. >0 cooling, <0 heating, 0 off.
        T0 : float
            Dead-state / outdoor-air temperature [°C].
        T_a_room : float | None
            Room air temperature [°C]. Uses constructor default if None.
        return_dict : bool
            If True return dict; else single-row DataFrame.
        postprocess : bool
            If True, apply postprocess_exergy to the output.
        verbose : bool
            If False, the :class:`~tmhp.OperatingLimitWarning` of this call
            is not issued (the row is the same).
        RH_out : float | None
            Outdoor relative humidity as a fraction (0-1), used by the frost
            correction in heating. None: the frost model's default (0.84).

        Returns
        -------
        dict | pd.DataFrame
            Cycle state plus diagnostic flags.

            Two keys are useful for branching:

            - ``"converged"`` (bool) — True only when the inner HX optimisation
              and the SciPy optimiser both succeeded.
            - ``"failure_reason"`` (str) — one of ``"none"``, ``"cycle_invalid"``
              (the refrigerant cycle itself was infeasible), ``"pr_above_max"``,
              ``"discharge_temp_above_max"``, ``"condensing_temp_above_max"``,
              ``"eta_isen_below_min"`` (the
              compressor envelope refuses the request where it is easiest to
              meet -- at its least-lift point -- and no smaller duty runs
              either; a request that a smaller duty meets returns that duty
              with ``capacity_clamped="max"`` and ``capacity_binding`` naming
              the limit: ``"discharge_temperature"``,
              ``"condensing_temperature"`` for ``T_cond_max``,
              ``"compressor_efficiency"``), ``"hx_not_converged"`` (cycle OK
              but a coil cannot carry the duty inside the approach bounds), or
              ``"optimizer_failed"`` (SciPy reported ``success=False``).

            ASHP triggers an off-mode fallback for any of the non-``"none"``
            reasons — ``E_cmp [W]`` will be 0 and the COP keys will be NaN
            in that case. Treat ``failure_reason != "none"`` as "do not
            trust the numbers".

            Part load is reported on every row: ``Q_request [W]`` (signed,
            as asked), ``Q_unmet [W]`` (request not delivered, >= 0),
            ``runtime_fraction [-]``, ``capacity_ratio [-]``,
            ``part_load_factor [-]``, ``Q_min [W]`` and
            ``E_cycling_loss [W]``. A request below the minimum continuous
            capacity (``capacity_clamped == "min"``) is the mean over an
            on/off cycling period: ``Q_ref_iu [W]`` equals the request,
            heat and flow rates are the running values times
            ``capacity_ratio [-]`` (the indoor fan's power with them),
            compressor and outdoor-fan power the running values times
            ``runtime_fraction [-]``, and the COP keys carry the EN 14825
            degradation; temperatures, pressures and speed are those of
            the running machine (:mod:`tmhp.cycling`). A request above
            the ceiling (``capacity_clamped == "max"``) adds ``Q_max [W]``
            and ``capacity_binding`` (``"compressor"``,
            ``"discharge_temperature"``, ``"pressure_ratio"``,
            ``"compressor_efficiency"`` or ``"cycle"``; see
            :meth:`max_capacity`).

            Heating below the frost model's outdoor limit (5 degC; the
            correction grows to its full size at 2 degC) is
            the mean over frosting and defrost (:mod:`tmhp.defrost`):
            ``defrost_fraction [-]`` is the share of the runtime spent
            defrosting, ``frost_capacity_factor [-]`` and
            ``frost_power_factor [-]`` the multipliers of the frosted coil,
            ``E_defrost [W]`` and ``Q_defrost [W]`` the mean power and room
            heat the defrost takes, and ``Q_ref_iu_frost_free [W]`` /
            ``E_tot_frost_free [W]`` the frost-free cycle the row was solved
            at. Without frost those two repeat ``Q_ref_iu [W]`` and
            ``E_tot [W]``.

        Warns
        -----
        OperatingLimitWarning
            Once, when the compressor was asked to run and the result is the
            off state (``failure_reason`` other than ``"none"``) or a running
            point that delivers less than the request (``capacity_clamped ==
            "max"``): the request, what is delivered, the limit in words and
            what removes it. Issued once per model and limit unless the
            ``"always"`` filter is set (:mod:`tmhp.operating_limits`).
        """
        validate_steady(locals())

        if T_a_room is None:
            T_a_room = self.T_a_room

        # Heating with a frosting outdoor coil is the mean over frosting and
        # defrost: solved frost-free at a larger duty, then averaged.
        frost = self._frost_state(Q_r_iu, T0, RH_out)
        if frost.active:
            # The frost balance solves the frost-free cycle at several duties.
            with self._remembering_floors():
                return self._analyze_steady_frosted(
                    frost, Q_r_iu, T0, T_a_room, postprocess=postprocess, verbose=verbose
                )

        if Q_r_iu == 0:
            result: dict | None = self._calc_state(
                dT_ref_evap=5.0,
                dT_ref_cond=5.0,
                Q_r_iu=0.0,
                T0=T0,
                T_a_room=T_a_room,
            )
            if result is not None:
                result["failure_reason"] = "none"
        else:
            opt_result = self._optimize_operation(
                Q_r_iu=Q_r_iu,
                T0=T0,
                T_a_room=T_a_room,
            )
            result = None
            with contextlib.suppress(Exception):
                result = self._calc_state(
                    dT_ref_evap=opt_result.x[0],
                    dT_ref_cond=opt_result.x[1],
                    Q_r_iu=Q_r_iu,
                    T0=T0,
                    T_a_room=T_a_room,
                )

            # Pressure-ratio envelope event of the final operating point.
            # Floor -> the ratio is held on it and the cycle is solved;
            # ceiling -> rejected (the off state below). Nothing is printed.
            pr_event = self._last_pr_event

            # opt_success=True at the infeasible sentinel is a false success:
            # the optimiser converged but never escaped the penalty region. A
            # capacity-limited optimum sits in its own band below that sentinel
            # and counts as success — the machine ran flat out and delivered
            # less than asked, which is operation, not failure.
            opt_fun = float(getattr(opt_result, "fun", OBJ_INFEASIBLE))
            opt_success = bool(getattr(opt_result, "success", False)) and opt_fun < OBJ_INFEASIBLE
            # Why the least-lift point of this request is not an operating
            # point, when the search found none: the limit that refuses the
            # request where it is easiest to meet. The probe the search ends on
            # is an arbitrary approach pair, and the limit it happens to break
            # (the pressure ratio at 15 K + 15 K, say) need not be the one in
            # the way.
            least_lift_reason = getattr(opt_result, "least_lift_reason", None)
            if (result is None or not result.get("converged", False)) and least_lift_reason is not None:
                failure_reason = "hx_not_converged" if least_lift_reason == self._AIR_SIDE else least_lift_reason
            elif result is None:
                # Distinguish a pressure-ratio ceiling rejection from a generic
                # invalid cycle so downstream consumers see the specific cause.
                failure_reason = (
                    pr_event[0] if pr_event is not None and pr_event[0] != "pr_below_min" else "cycle_invalid"
                )
            elif not result.get("converged", False):
                failure_reason = "hx_not_converged"
            elif not opt_success:
                failure_reason = "optimizer_failed"
            else:
                failure_reason = "none"

            unsolved = failure_reason != "none" and (result is None or not result.get("converged", False))
            limited = (
                failure_reason == "none"
                and result is not None
                and result.get("capacity_clamped") == CAPACITY_CLAMPED_MAX
            )
            if unsolved or limited:
                # The request has no operating point of its own: the compressor
                # runs out of speed, or the envelope or a coil binds first. The
                # unit then runs at its ceiling (:meth:`max_capacity`), which is
                # solved rather than searched -- ranking capacity-limited
                # candidates by delivered heat walks into states that starve
                # the outdoor coil (see :meth:`max_capacity`). A capacity-limited
                # search result stands only if the ceiling cannot be solved.
                # Match the existing numerical off-state fallback if the
                # envelope search itself encounters an invalid cycle.
                with contextlib.suppress(Exception):
                    ceiling_state = self._capacity_limited_operating_point(Q_r_iu, T0, T_a_room)
                    if ceiling_state is not None:
                        result = ceiling_state
                        failure_reason = "none"

            if failure_reason == ETA_ISEN_BELOW_MIN:
                # Refused for the efficiency floor at low speed while larger
                # duties run: the unit's smallest continuous duty lies above
                # the request, and the request is met by cycling at it.
                with contextlib.suppress(Exception):
                    floor_state = self._smallest_continuous_operating_point(Q_r_iu, T0, T_a_room)
                    if floor_state is not None:
                        result = floor_state
                        failure_reason = "none"

            if failure_reason != "none":
                # Not a warning here: this solve may be the frost-free trial of
                # a frosted request. The call that owns the result words it
                # (tmhp.operating_limits.steady_notice); the value that broke
                # the limit is known when the search ended on that probe.
                ended_on_it = least_lift_reason is None and pr_event is not None and pr_event[0] == failure_reason
                note_refusal(failure_reason, Q_r_iu, pr_event[1] if ended_on_it and pr_event is not None else None)
                result = self._calc_state(
                    dT_ref_evap=5.0,
                    dT_ref_cond=5.0,
                    Q_r_iu=0.0,
                    T0=T0,
                    T_a_room=T_a_room,
                )
                if result is not None:
                    result["converged"] = False
                    result["failure_reason"] = failure_reason
            else:
                # By construction (failure_reason == "none") result is a dict.
                assert result is not None
                # Only a ceiling set by a coil leaves a choice of operating
                # point: at the speed ceiling and on an envelope limit the
                # least-lift point is the only one that delivers the duty.
                if (
                    result.get("capacity_clamped") == CAPACITY_CLAMPED_MAX
                    and result.get("capacity_binding", BINDING_CYCLE) == BINDING_CYCLE
                ):
                    result = self._least_power_at_ceiling(result, Q_r_iu, T0, T_a_room)
                result["converged"] = True
                result["failure_reason"] = "none"

        if result is None:
            result = {}

        if result:
            self._report_part_load(result, Q_r_iu)
            # A request the frost model finds too small to frost may be met by
            # a machine that does: on its speed floor the unit runs at more
            # than the request (a load-weighted frost model; otherwise unchanged).
            running_frost = self._frost_state_of_running(result, frost, T0)
            if running_frost.active and not frost.active:
                with self._remembering_floors():
                    return self._analyze_steady_frosted(
                        running_frost, Q_r_iu, T0, T_a_room, postprocess=postprocess, verbose=verbose
                    )

        if result:
            self._write_frost_free(result, frost)
            self._complete_row(result)

        if postprocess and result:
            result = self._exergy_of_row(result)

        if return_dict:
            return result
        return pd.DataFrame([result]) if result else pd.DataFrame()

    # =============================================================
    # Dynamic simulation
    # =============================================================

    @dynamic_notice
    def analyze_dynamic(
        self,
        simulation_period_sec: int,
        dt_s: int,
        Q_r_iu_schedule,
        T0_schedule,
        T_a_room_schedule=None,
        result_save_csv_path: str | None = None,
        RH_out_schedule=None,
    ) -> pd.DataFrame:
        """Run a time-stepping dynamic simulation.

        Parameters
        ----------
        simulation_period_sec : int
            Total simulation duration [s].
        dt_s : int
            Time step size [s].
        Q_r_iu_schedule : array-like
            Indoor thermal load per step [W].
        T0_schedule : array-like
            Outdoor temperature per step [°C].
        T_a_room_schedule : array-like | None
            Room air temperature per step [°C].
            If None, uses constructor default.
        result_save_csv_path : str | None
            Optional CSV output path.
        RH_out_schedule : array-like | None
            Outdoor relative humidity per step, as a fraction (0-1), for the
            frost correction in heating. If None, the frost model's default.

        Returns
        -------
        pd.DataFrame
            Per-timestep result DataFrame.

        Warns
        -----
        OperatingLimitWarning
            Once, at the end of a run in which the compressor was called but
            did not run on some step, or ran below the request: the steps by
            limit and what they left unserved. The totals are in
            ``df.attrs["operating_limits"]``
            (:func:`tmhp.operating_limits.summarize_operating_limits`).
        """
        validate_dynamic(locals())
        time: np.ndarray = np.arange(0, simulation_period_sec, dt_s)
        tN: int = len(time)

        T0_schedule = np.array(T0_schedule)
        Q_r_iu_schedule = np.array(Q_r_iu_schedule, dtype=float)

        if len(T0_schedule) != tN:
            raise ValueError(f"T0_schedule length ({len(T0_schedule)}) != time length ({tN})")
        if len(Q_r_iu_schedule) != tN:
            raise ValueError(f"Q_r_iu_schedule length ({len(Q_r_iu_schedule)}) != time length ({tN})")

        if T_a_room_schedule is not None:
            T_a_room_arr = np.array(T_a_room_schedule, dtype=float)
            if len(T_a_room_arr) != tN:
                raise ValueError(f"T_a_room_schedule length ({len(T_a_room_arr)}) != tN ({tN})")
        else:
            T_a_room_arr = np.full(tN, self.T_a_room)

        RH_out_arr = None
        if RH_out_schedule is not None:
            RH_out_arr = np.array(RH_out_schedule, dtype=float)
            if len(RH_out_arr) != tN:
                raise ValueError(f"RH_out_schedule length ({len(RH_out_arr)}) != tN ({tN})")

        self.time = time
        self.dt = dt_s

        results_data: list[dict] = []

        # The model does not change during a run: the smallest continuous
        # duty of a condition (the efficiency floor) is searched once.
        with self._remembering_floors():
            for n in tqdm(range(tN), desc="ASHP Simulating"):
                t_s: float = time[n]
                hr: float = t_s * cu.s2h

                Q_r_iu_n: float = Q_r_iu_schedule[n]
                T0_n: float = T0_schedule[n]
                T_a_room_n: float = T_a_room_arr[n]

                # Use analyze_steady for robust calculation and fallback handling
                hp_result = self.analyze_steady(
                    Q_r_iu=Q_r_iu_n,
                    T0=T0_n,
                    T_a_room=T_a_room_n,
                    return_dict=True,
                    postprocess=False,  # Exergy postprocessing applied in bulk at the end
                    verbose=False,  # Suppress warnings during long dynamic loops
                    RH_out=None if RH_out_arr is None else float(RH_out_arr[n]),
                )

                # Add time columns
                hp_result["time [s]"] = t_s
                hp_result["time [h]"] = hr

                results_data.append(hp_result)

        results_df: pd.DataFrame = pd.DataFrame(results_data)
        results_df = self.postprocess_exergy(results_df)
        if result_save_csv_path:
            results_df.to_csv(result_save_csv_path, index=False)
        return results_df

    # =============================================================
    # Exergy post-processing
    # =============================================================

    def _exergy_of_row(self, result: dict) -> dict:
        """``result`` with its exergy variables, as :meth:`postprocess_exergy` gives them for a one-row frame.

        A steady point is one row, and filling some fifty columns of a
        one-row frame one assignment at a time costs more than the cycle
        solve's last step. The row is computed here from the same formulas on
        scalars (:func:`_exergy_row`); the keys, their order, the types and the
        values are those of ``postprocess_exergy(pd.DataFrame([result])).iloc[0].to_dict()``,
        to the last bit. A row that is not of the usual shape -- a value of a
        type a frame would treat differently, a missing column the frame
        would raise on or skip the exergy for -- goes through the frame.
        """
        try:
            return _exergy_row(result, self.ref)
        except (_NotARow, KeyError):
            processed: dict = self.postprocess_exergy(pd.DataFrame([result])).iloc[0].to_dict()
            return processed

    def postprocess_exergy(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute ASHP-specific exergy variables.

        Mirrors ``AirSourceHeatPumpBoiler.postprocess_exergy()``
        with adaptations for indoor-unit air exchange.

        Pipeline:

        1. Refrigerant state-point exergy (CoolProp)
        2. Electricity = exergy (compressor, IU fan, OU fan)
        3. Air exergy (outdoor unit + indoor unit)
        4. HX Carnot exergy (condenser, evaporator)
        5. Component-level exergy destruction
        6. Exergetic efficiency metrics
        """
        from .enex_functions import (
            calc_exergy_flow,
            calc_refrigerant_exergy,
            convert_electricity_to_exergy,
        )

        df = df.copy()

        # Guard: if T0 [°C] is missing (very defensive), skip
        if "T0 [°C]" not in df.columns:
            return df

        T0_K = cu.C2K(df["T0 [°C]"])

        # ── 1. Refrigerant exergy ────────────────────────
        if "h_ref_cmp_in [J/kg]" in df.columns:
            df = calc_refrigerant_exergy(df, self.ref, T0_K)
        else:
            return df  # OFF-only DataFrame, skip exergy

        # ── 2. Electricity = exergy ─────────────────────
        df = convert_electricity_to_exergy(df)
        # Add indoor fan exergy (electricity = exergy)
        if "E_iu_fan [W]" in df.columns:
            df["X_iu_fan [W]"] = df["E_iu_fan [W]"]

        # ── 3. Air exergy (outdoor unit) ────────────────
        if "dV_ou_a [m3/s]" in df.columns and "T_ou_a_in [°C]" in df.columns:
            # Same air mass as the cycle solve: density at the coil inlet.
            G_a_ou = air_heat_capacity_rate(df["dV_ou_a [m3/s]"].fillna(0), df["T_ou_a_in [°C]"])
            Tin_ou = cu.C2K(df["T_ou_a_in [°C]"])
            Tmid_ou = cu.C2K(df["T_ou_a_mid [°C]"])
            Tout_ou = cu.C2K(df["T_ou_a_out [°C]"]) if "T_ou_a_out [°C]" in df.columns else Tin_ou
            df["X_a_ou_in [W]"] = calc_exergy_flow(G_a_ou, Tin_ou, T0_K)
            df["X_a_ou_out [W]"] = calc_exergy_flow(G_a_ou, Tout_ou, T0_K)
            df["X_a_ou_mid [W]"] = calc_exergy_flow(G_a_ou, Tmid_ou, T0_K)

        # ── 3b. Air exergy (indoor unit) ────────────────
        if "dV_iu_a [m3/s]" in df.columns and "T_iu_a_in [°C]" in df.columns:
            G_a_iu = air_heat_capacity_rate(df["dV_iu_a [m3/s]"].fillna(0), df["T_iu_a_in [°C]"])
            Tin_iu = cu.C2K(df["T_iu_a_in [°C]"])
            Tmid_iu = cu.C2K(df["T_iu_a_mid [°C]"])
            Tout_iu = cu.C2K(df["T_iu_a_out [°C]"]) if "T_iu_a_out [°C]" in df.columns else Tin_iu
            df["X_a_iu_in [W]"] = calc_exergy_flow(G_a_iu, Tin_iu, T0_K)
            df["X_a_iu_out [W]"] = calc_exergy_flow(G_a_iu, Tout_iu, T0_K)
            df["X_a_iu_mid [W]"] = calc_exergy_flow(G_a_iu, Tmid_iu, T0_K)

        # ── 4. HX Carnot exergy (mode-aware IU/OU) ─────
        # calc_ref_state always uses mode="heating" internally:
        #   cmp_out → condenser inlet (high-pressure superheated)
        #   exp_in  → condenser outlet (high-pressure subcooled)
        #   exp_out → evaporator inlet (low-pressure two-phase)
        #   cmp_in  → evaporator outlet (low-pressure superheated)
        #
        # Mapping to physical units:
        #   Heating: IU = condenser, OU = evaporator
        #   Cooling: IU = evaporator, OU = condenser
        # The Carnot factor for each location uses the saturation temperature of
        # the refrigerant role it plays in the current mode. Output exergy is
        # labelled by location (X_ref_iu/X_ref_ou); the refrigerant-state
        # saturation keys remain cond/evap (refrigerant-intrinsic).
        # The condenser's factor uses the temperature the cycle condenses at,
        # the bubble point ``T_ref_cond_sat_l`` -- the value
        # ``T_ref_cond_sat_v`` repeated before it reported a zeotrope's dew point.
        if {"T_ref_cond_sat_l [°C]", "T_ref_evap_sat [°C]", "mode"} <= set(df.columns):
            is_heating = df["mode"] == "heating"
            T_iu_sat_K = cu.C2K(df["T_ref_cond_sat_l [°C]"].where(is_heating, df["T_ref_evap_sat [°C]"]))
            T_ou_sat_K = cu.C2K(df["T_ref_evap_sat [°C]"].where(is_heating, df["T_ref_cond_sat_l [°C]"]))
            df["X_ref_iu [W]"] = df["Q_ref_iu [W]"] * (1 - T0_K / T_iu_sat_K)
            df["X_ref_ou [W]"] = df["Q_ref_ou [W]"] * (1 - T0_K / T_ou_sat_K)

        # ── 5. Total exergy input ───────────────────────
        X_tot = df["E_cmp [W]"] + df["E_ou_fan [W]"].fillna(0) + df["E_iu_fan [W]"].fillna(0)
        df["X_tot [W]"] = X_tot

        # ── 6. Component exergy destruction (IU/OU naming) ──
        # Air exergy helper Series
        X_a_ou_in = df.get("X_a_ou_in [W]", pd.Series(0.0, index=df.index)).fillna(0)
        X_a_ou_mid = df.get("X_a_ou_mid [W]", pd.Series(0.0, index=df.index)).fillna(0)
        X_a_ou_out = df.get("X_a_ou_out [W]", pd.Series(0.0, index=df.index)).fillna(0)
        X_a_iu_in = df.get("X_a_iu_in [W]", pd.Series(0.0, index=df.index)).fillna(0)
        X_a_iu_mid = df.get("X_a_iu_mid [W]", pd.Series(0.0, index=df.index)).fillna(0)
        X_a_iu_out = df.get("X_a_iu_out [W]", pd.Series(0.0, index=df.index)).fillna(0)

        if "X_cmp [W]" not in df.columns:
            return df

        # Mode masks
        is_heating = df["mode"] == "heating"
        is_cooling = df["mode"] == "cooling"

        # ── 6a. Compressor (X_in, Xc, X_out) ──
        df["X_in_cmp [W]"] = df["X_cmp [W]"] + df["X_ref_cmp_in [W]"]
        df["X_out_cmp [W]"] = df["X_ref_cmp_out [W]"]
        df["Xc_cmp [W]"] = df["X_in_cmp [W]"] - df["X_out_cmp [W]"]

        # ── 6b. Expansion valve (X_in, Xc, X_out) ──
        df["X_in_exp [W]"] = df["X_ref_exp_in [W]"]
        df["X_out_exp [W]"] = df["X_ref_exp_out [W]"]
        df["Xc_exp [W]"] = df["X_in_exp [W]"] - df["X_out_exp [W]"]

        # ── 6c. Indoor Unit HX (mode-aware: X_in, Xc, X_out) ──
        # Heating: IU = condenser → ref enters from cmp_out, exits to exp_in
        # Cooling: IU = evaporator → ref enters from exp_out, exits to cmp_in
        X_in_iu_hx = pd.Series(0.0, index=df.index)
        X_out_iu_hx = pd.Series(0.0, index=df.index)
        X_in_iu_hx[is_heating] = df.loc[is_heating, "X_ref_cmp_out [W]"] + X_a_iu_in[is_heating]
        X_out_iu_hx[is_heating] = df.loc[is_heating, "X_ref_exp_in [W]"] + X_a_iu_mid[is_heating]
        X_in_iu_hx[is_cooling] = df.loc[is_cooling, "X_ref_exp_out [W]"] + X_a_iu_in[is_cooling]
        X_out_iu_hx[is_cooling] = df.loc[is_cooling, "X_ref_cmp_in [W]"] + X_a_iu_mid[is_cooling]
        df["X_in_iu_hx [W]"] = X_in_iu_hx
        df["X_out_iu_hx [W]"] = X_out_iu_hx
        df["Xc_iu_hx [W]"] = X_in_iu_hx - X_out_iu_hx

        # ── 6d. Outdoor Unit HX (mode-aware: X_in, Xc, X_out) ──
        # Heating: OU = evaporator → ref enters from exp_out, exits to cmp_in
        # Cooling: OU = condenser → ref enters from cmp_out, exits to exp_in
        X_in_ou_hx = pd.Series(0.0, index=df.index)
        X_out_ou_hx = pd.Series(0.0, index=df.index)
        X_in_ou_hx[is_heating] = df.loc[is_heating, "X_ref_exp_out [W]"] + X_a_ou_in[is_heating]
        X_out_ou_hx[is_heating] = df.loc[is_heating, "X_ref_cmp_in [W]"] + X_a_ou_mid[is_heating]
        X_in_ou_hx[is_cooling] = df.loc[is_cooling, "X_ref_cmp_out [W]"] + X_a_ou_in[is_cooling]
        X_out_ou_hx[is_cooling] = df.loc[is_cooling, "X_ref_exp_in [W]"] + X_a_ou_mid[is_cooling]
        df["X_in_ou_hx [W]"] = X_in_ou_hx
        df["X_out_ou_hx [W]"] = X_out_ou_hx
        df["Xc_ou_hx [W]"] = X_in_ou_hx - X_out_ou_hx

        # ── 6e. Outdoor fan (X_in, Xc, X_out) ──
        df["X_in_ou_fan [W]"] = df["X_ou_fan [W]"].fillna(0) + X_a_ou_mid
        df["X_out_ou_fan [W]"] = X_a_ou_out
        df["Xc_ou_fan [W]"] = df["X_in_ou_fan [W]"] - df["X_out_ou_fan [W]"]

        # ── 6f. Indoor fan (X_in, Xc, X_out) ──
        df["X_in_iu_fan [W]"] = df["X_iu_fan [W]"].fillna(0) + X_a_iu_mid
        df["X_out_iu_fan [W]"] = X_a_iu_out
        df["Xc_iu_fan [W]"] = df["X_in_iu_fan [W]"] - df["X_out_iu_fan [W]"]

        # ── 7. Exergetic efficiency metrics ─────────────
        # System exergetic efficiency
        df["X_eff_sys [-]"] = (X_a_iu_out - X_a_iu_in) / df["X_tot [W]"].replace(0, np.nan)

        # Compressor exergetic efficiency
        df["X_eff_cmp [-]"] = 1 - df["Xc_cmp [W]"] / df["X_in_cmp [W]"].replace(0, np.nan)

        # Expansion valve exergetic efficiency
        df["X_eff_exp [-]"] = 1 - df["Xc_exp [W]"] / df["X_in_exp [W]"].replace(0, np.nan)

        # Indoor unit HX exergetic efficiency
        df["X_eff_iu_hx [-]"] = 1 - df["Xc_iu_hx [W]"] / df["X_in_iu_hx [W]"].replace(0, np.nan)

        # Outdoor unit HX exergetic efficiency
        df["X_eff_ou_hx [-]"] = 1 - df["Xc_ou_hx [W]"] / df["X_in_ou_hx [W]"].replace(0, np.nan)

        # Outdoor fan exergetic efficiency
        df["X_eff_ou_fan [-]"] = 1 - df["Xc_ou_fan [W]"] / df["X_in_ou_fan [W]"].replace(0, np.nan)

        # Indoor fan exergetic efficiency
        df["X_eff_iu_fan [-]"] = 1 - df["Xc_iu_fan [W]"] / df["X_in_iu_fan [W]"].replace(0, np.nan)

        return df
