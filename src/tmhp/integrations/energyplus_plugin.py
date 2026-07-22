"""EnergyPlus Python Plugin adapter for the tmhp ASHPB (#165 P2).

Surrogates :class:`~tmhp.AirSourceHeatPumpBoiler` inside EnergyPlus as a
``PlantComponent:UserDefined`` device. Each plant-solver call hands the plugin
the loop inlet temperature, mass flow, specific heat, and load request (plus the
site outdoor drybulb); the plugin solves the refrigerant cycle with the public
``analyze_steady()`` seam (#165 P0) and writes back the outlet-temperature and
mass-flow actuators, routing compressor electricity to a metered output.

Why ``analyze_steady`` (not ``step()``): EnergyPlus owns the storage-tank state
through ``WaterHeater:Mixed`` and asks the component for a *steady* answer each
timestep (given the inlet/outdoor conditions and the loop load, what is the
leaving temperature and the electric input). The dynamic ``step()`` kernel — and
its tank model — is instead what the FMU adapter (#165 P1,
:mod:`tmhp.integrations.fmu`) wraps. The two BES adapters therefore ride on
different seams of the same model.

Runtime: EnergyPlus runs Python plugins in its **embedded CPython**, so
``pyenergyplus`` is provided by the EnergyPlus install (not pip-installable) and
``tmhp`` + native deps (CoolProp/numpy/scipy) must be built for that
interpreter's ABI and pointed to via ``PythonPlugin:SearchPaths``. Verify with an
import-only smoke plugin first — a wrong-ABI CoolProp wheel fails silently.

Configuration (environment variables, all optional):

=====================================  ===========================================
``TMHP_ASHPB_REF``                     refrigerant (default ``R32``; e.g. R290, R410A)
``TMHP_ASHPB_CAPACITY``                nominal HP capacity in W (default ``15000``)
``TMHP_ASHPB_PRESET``                  named parameter preset (default empty = model defaults)
``TMHP_ASHPB_VDISP_CC``                compressor displacement in cm³ (required with preset)
``TMHP_ASHPB_FAN_M3S``                 rated outdoor fan flow in m³/s (required with preset)
``TMHP_UD_NAME``                       ``PlantComponent:UserDefined`` object name
                                       (default ``ASHPB_UserDefined``)
``TMHP_LOOP_DESIGN_VDOT``              design loop volume flow in m³/s (default ``0.003``)
``TMHP_EPLUS_ECMP_ENERGY_GLOBAL``      plugin global receiving timestep energy [J]
                                       (default ``tmhp_E_cmp_J``)
``TMHP_EPLUS_ECMP_POWER_GLOBAL``       optional plugin global receiving power [W]
                                       (default ``tmhp_E_cmp_W``)
``TMHP_PLUGIN_LOG``                    if set, append a per-call/tally log to this path
=====================================  ===========================================

IDF wiring (see the issue #180 / the validated demo for a full example): one
``PlantComponent:UserDefined`` named ``ASHPB_UserDefined`` with an init manager
bound to :class:`TmhpPlantInit` and a simulation manager bound to
:class:`TmhpPlantSurrogate`, a ``PythonPlugin:Variables`` global
``tmhp_E_cmp_J`` for summed energy metering, and optionally ``tmhp_E_cmp_W`` for
averaged power reporting. The legacy global ``tmhp_E_cmp`` is still accepted as
an energy sink for older IDFs.

API strings (actuator component type ``"Plant Connection 1"``; internal
variables ``"... for Plant Connection 1"``) are verified against EnergyPlus
24.2.0; reconfirm them for other releases.
"""

from __future__ import annotations

import math
import os
from typing import Any

try:
    from pyenergyplus.plugin import EnergyPlusPlugin
except ModuleNotFoundError:  # pragma: no cover - exercised where EnergyPlus is absent

    class EnergyPlusPlugin:  # type: ignore[no-redef]
        """Minimal stand-in so pure adapter helpers stay testable outside E+."""

        def __init__(self) -> None:
            self.api: Any = None


from tmhp import AirSourceHeatPumpBoiler
from tmhp.integrations import _fmi_common

# --- Configuration (read once at import) ------------------------------------
REF = os.environ.get("TMHP_ASHPB_REF", "R32")
HP_CAPACITY = float(os.environ.get("TMHP_ASHPB_CAPACITY", "15000"))
PRESET = os.environ.get("TMHP_ASHPB_PRESET", "")
V_CMP_DISP_CC = float(os.environ.get("TMHP_ASHPB_VDISP_CC", "0"))
DV_FAN_A_RATED = float(os.environ.get("TMHP_ASHPB_FAN_M3S", "0"))
UD_NAME = os.environ.get("TMHP_UD_NAME", "ASHPB_UserDefined")
LOOP_DESIGN_VDOT = float(os.environ.get("TMHP_LOOP_DESIGN_VDOT", "0.003"))  # m³/s
ECMP_ENERGY_GLOBAL = os.environ.get("TMHP_EPLUS_ECMP_ENERGY_GLOBAL", "tmhp_E_cmp_J")
ECMP_POWER_GLOBAL = os.environ.get("TMHP_EPLUS_ECMP_POWER_GLOBAL", "tmhp_E_cmp_W")
ECMP_LEGACY_GLOBAL = "tmhp_E_cmp"
_LOG = os.environ.get("TMHP_PLUGIN_LOG")  # None -> stdout only

MDOT_FLOOR = 0.05  # kg/s — below this the inlet flow is treated as "off"
TOUT_MAX = 95.0  # °C — clamp inside the liquid-water property range
RHO_WATER = 1000.0  # kg/m³ — sizing only
GUARD_WARNING_LIMIT = 5  # repeated warnings per failure reason before suppression


def _ashpb_model_kwargs(
    *,
    ref: str,
    hp_capacity: float,
    preset: str = "",
    V_cmp_disp_cc: float = 0.0,
    dV_fan_a_rated: float = 0.0,
) -> dict[str, Any]:
    """Return constructor kwargs without requiring an EnergyPlus runtime."""
    return {
        "ref": ref,
        "hp_capacity": hp_capacity,
        **_fmi_common.preset_kwargs(
            preset,
            ref=ref,
            hp_capacity=hp_capacity,
            V_cmp_disp_cc=V_cmp_disp_cc,
            dV_fan_a_rated=dV_fan_a_rated,
        ),
    }


def _normalize_failure_reason(value: object) -> str | None:
    """Normalize model diagnostics for adapter tally keys."""
    if value is None or isinstance(value, str):
        return value
    return str(value)


def _finite_float_or_none(value: Any) -> float | None:
    """Parse a finite float from an EnergyPlus boundary value."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _is_finite(value: Any) -> bool:
    """Return whether *value* can safely cross the EnergyPlus boundary."""
    return _finite_float_or_none(value) is not None


def _is_positive_finite(value: Any) -> bool:
    """Return whether *value* is a usable positive finite number."""
    out = _finite_float_or_none(value)
    return out is not None and out > 0.0


def _has_usable_cycle_output(res: dict[str, Any]) -> bool:
    """Whether a steady result can actuate the EnergyPlus plant component."""
    return _is_positive_finite(res.get("E_cmp [W]")) and _is_positive_finite(res.get("Q_ref_tank [W]"))


def _issue_severe(api: Any, state: Any, msg: str) -> None:
    """Report an EnergyPlus severe error when the runtime API is available."""
    runtime = getattr(api, "runtime", None)
    issue_severe = getattr(runtime, "issue_severe", None)
    if callable(issue_severe):
        issue_severe(state, msg)


def _issue_warning(api: Any, state: Any, msg: str) -> None:
    """Report an EnergyPlus warning when the runtime API is available."""
    runtime = getattr(api, "runtime", None)
    issue_warning = getattr(runtime, "issue_warning", None)
    if callable(issue_warning):
        issue_warning(state, msg)
    else:
        _log(msg)


def _set_global_if_valid(ex: Any, state: Any, handle: int, value: float) -> None:
    """Write an EnergyPlus plugin global only when it was declared in the IDF."""
    if handle != -1:
        ex.set_global_value(state, handle, value)


def _log(msg: str) -> None:
    print(msg, flush=True)
    if _LOG:
        try:
            with open(_LOG, "a") as f:
                f.write(msg + "\n")
        except OSError:
            pass


class TmhpPlantInit(EnergyPlusPlugin):
    """One-shot sizing actuators so the plant loop dispatches load to the ASHPB.

    Bind to the ``PlantComponent:UserDefined`` *initialization* program-calling
    manager (E+ runs it once before plant sizing).
    """

    def __init__(self) -> None:
        super().__init__()
        self._need = True
        self.h: dict[str, int] = {}

    def _get_handles(self, state: Any) -> None:
        ac = self.api.exchange.get_actuator_handle
        self.h = dict(
            design_vdot=ac(state, "Plant Connection 1", "Design Volume Flow Rate", UD_NAME),
            mdot_min=ac(state, "Plant Connection 1", "Minimum Mass Flow Rate", UD_NAME),
            mdot_max=ac(state, "Plant Connection 1", "Maximum Mass Flow Rate", UD_NAME),
            cap_min=ac(state, "Plant Connection 1", "Minimum Loading Capacity", UD_NAME),
            cap_max=ac(state, "Plant Connection 1", "Maximum Loading Capacity", UD_NAME),
            cap_opt=ac(state, "Plant Connection 1", "Optimal Loading Capacity", UD_NAME),
        )
        self._need = False

    def _valid(self, state: Any) -> bool:
        ok = True
        for k, v in self.h.items():
            if v == -1:
                ok = False
                self.api.runtime.issue_severe(state, f"[TmhpInit] handle not found: {k}")
        return ok

    def on_user_defined_component_model(self, state: Any) -> int:
        if not self.api.exchange.api_data_fully_ready(state):
            return 0
        if self._need:
            self._get_handles(state)
            _log("[TmhpInit handles] " + ", ".join(f"{k}={v}" for k, v in self.h.items()))
            if not self._valid(state):
                return 1
        ex = self.api.exchange
        ex.set_actuator_value(state, self.h["design_vdot"], LOOP_DESIGN_VDOT)
        ex.set_actuator_value(state, self.h["mdot_min"], 0.0)
        ex.set_actuator_value(state, self.h["mdot_max"], LOOP_DESIGN_VDOT * RHO_WATER)
        ex.set_actuator_value(state, self.h["cap_min"], 0.0)
        ex.set_actuator_value(state, self.h["cap_max"], HP_CAPACITY)
        ex.set_actuator_value(state, self.h["cap_opt"], 0.9 * HP_CAPACITY)
        return 0


class TmhpPlantSurrogate(EnergyPlusPlugin):
    """Per-call ASHPB driver: read loop inlet + outdoor, solve, actuate.

    Bind to the ``PlantComponent:UserDefined`` *simulation* program-calling
    manager. Exposes compressor electricity through the plugin global
    ``tmhp_E_cmp_J`` (declare it in ``PythonPlugin:Variables`` and meter it as
    summed joules). ``tmhp_E_cmp_W`` can be declared for averaged power output.
    """

    def __init__(self) -> None:
        super().__init__()
        model_kwargs = _ashpb_model_kwargs(
            ref=REF,
            hp_capacity=HP_CAPACITY,
            preset=PRESET,
            V_cmp_disp_cc=V_CMP_DISP_CC,
            dV_fan_a_rated=DV_FAN_A_RATED,
        )
        self.hp = AirSourceHeatPumpBoiler(**model_kwargs)
        self._need = True
        self._requested = False
        self.h: dict[str, int] = {}
        self._ncall = 0
        self._nlog = 0
        # analyze_steady is a deterministic function of (T_in, T0, Q, mdot);
        # the plant solver re-calls with identical inputs many times per timestep,
        # so memoize on rounded inputs to avoid recomputing the CoolProp-heavy cycle.
        self._cache: dict[tuple[float, float, float, float], dict[str, Any]] = {}
        # Convergence tally over dispatched calls (cold-climate high-lift hours
        # may trip the pressure-ratio guard); dumped periodically to the log.
        self._ndispatch = 0
        self._nconv = 0
        self._reasons: dict[str | None, int] = {}
        self._tally_every = 5000
        self._warning_counts: dict[str, int] = {}

    def _solve(self, t_in: float, t0: float, q_target: float, m_dot: float) -> dict[str, Any]:
        key = (round(t_in, 1), round(t0, 1), round(q_target, 0), round(m_dot, 4))
        res = self._cache.get(key)
        if res is None:
            res = self.hp.analyze_steady(
                T_tank_w=t_in,
                T0=t0,
                Q_ref_tank=q_target,
                m_dot_w=m_dot,
            )
            self._cache[key] = res
        return res

    def _warn_guard_trip(self, state: Any, reason: str, t_in: float, t0: float, q_target: float) -> None:
        """Issue a bounded number of warnings for a thermodynamic guard."""
        count = self._warning_counts.get(reason, 0) + 1
        self._warning_counts[reason] = count
        if count > GUARD_WARNING_LIMIT:
            return
        suffix = " Further warnings for this reason are suppressed." if count == GUARD_WARNING_LIMIT else ""
        _issue_warning(
            self.api,
            state,
            f"[TmhpSurrogate] {reason}: condenser temperature is at/below loop inlet; "
            f"forcing zero heat, compressor power, and water flow "
            f"(T_in={t_in:.2f}C, T0={t0:.2f}C, Qtarget={q_target:.1f}W).{suffix}",
        )

    def _get_handles(self, state: Any) -> None:
        ex = self.api.exchange
        iv, ac = ex.get_internal_variable_handle, ex.get_actuator_handle
        self.h = dict(
            t_in=iv(state, "Inlet Temperature for Plant Connection 1", UD_NAME),
            mdot=iv(state, "Inlet Mass Flow Rate for Plant Connection 1", UD_NAME),
            cp=iv(state, "Inlet Specific Heat for Plant Connection 1", UD_NAME),
            load=iv(state, "Load Request for Plant Connection 1", UD_NAME),
            t_out_act=ac(state, "Plant Connection 1", "Outlet Temperature", UD_NAME),
            mdot_act=ac(state, "Plant Connection 1", "Mass Flow Rate", UD_NAME),
            t0=ex.get_variable_handle(state, "Site Outdoor Air Drybulb Temperature", "Environment"),
            e_cmp_j=ex.get_global_handle(state, ECMP_ENERGY_GLOBAL),
            e_cmp_w=ex.get_global_handle(state, ECMP_POWER_GLOBAL),
            e_cmp_legacy=ex.get_global_handle(state, ECMP_LEGACY_GLOBAL),
        )
        self._need = False

    def _valid(self, state: Any) -> bool:
        ok = True
        for k, v in self.h.items():
            if k in {"e_cmp_j", "e_cmp_w", "e_cmp_legacy"}:
                continue
            if v == -1:
                ok = False
                _issue_severe(self.api, state, f"[TmhpSurrogate] handle not found: {k}")
        if self.h.get("e_cmp_j", -1) == -1 and self.h.get("e_cmp_legacy", -1) == -1:
            ok = False
            _issue_severe(
                self.api,
                state,
                "[TmhpSurrogate] handle not found: energy global "
                f"{ECMP_ENERGY_GLOBAL!r} (or legacy {ECMP_LEGACY_GLOBAL!r})",
            )
        return ok

    def _set_electric_outputs(self, state: Any, e_cmp_w: float, e_cmp_j: float) -> None:
        """Write EnergyPlus global outputs with explicit W/J semantics."""
        ex = self.api.exchange
        _set_global_if_valid(ex, state, self.h.get("e_cmp_j", -1), e_cmp_j)
        _set_global_if_valid(ex, state, self.h.get("e_cmp_legacy", -1), e_cmp_j)
        _set_global_if_valid(ex, state, self.h.get("e_cmp_w", -1), e_cmp_w)

    def on_user_defined_component_model(self, state: Any) -> int:
        ex = self.api.exchange
        if not ex.api_data_fully_ready(state):
            return 0
        # Request the outdoor-air variable on the first pass; its handle is only
        # resolvable afterwards.
        if not self._requested:
            ex.request_variable(state, "Site Outdoor Air Drybulb Temperature", "Environment")
            self._requested = True
            return 0
        if self._need:
            self._get_handles(state)
            _log("[TmhpSurrogate handles] " + ", ".join(f"{k}={v}" for k, v in self.h.items()))
            if not self._valid(state):
                return 1

        self._ncall += 1
        t_in_raw = ex.get_internal_variable_value(state, self.h["t_in"])
        mdot_raw = ex.get_internal_variable_value(state, self.h["mdot"])
        cp_raw = ex.get_internal_variable_value(state, self.h["cp"])
        q_req_raw = ex.get_internal_variable_value(state, self.h["load"])
        t0_raw = ex.get_variable_value(state, self.h["t0"])
        t_in = _finite_float_or_none(t_in_raw)
        mdot = _finite_float_or_none(mdot_raw)
        cp = _finite_float_or_none(cp_raw)
        q_req = _finite_float_or_none(q_req_raw)  # loop heating load request [W]
        t0 = _finite_float_or_none(t0_raw)

        if t_in is None or mdot is None or mdot < 0.0 or cp is None or cp <= 0.0 or q_req is None or t0 is None:
            _issue_severe(
                self.api,
                state,
                "[TmhpSurrogate] invalid EnergyPlus boundary values: "
                f"T_in={t_in_raw!r}, mdot={mdot_raw!r}, cp={cp_raw!r}, "
                f"Qreq={q_req_raw!r}, T0={t0_raw!r}",
            )
            safe_t_in = 0.0 if t_in is None else t_in
            ex.set_actuator_value(state, self.h["t_out_act"], safe_t_in)
            ex.set_actuator_value(state, self.h["mdot_act"], 0.0)
            self._set_electric_outputs(state, e_cmp_w=0.0, e_cmp_j=0.0)
            return 1

        # No load requested -> component off (request no flow).
        if q_req < 1.0:
            ex.set_actuator_value(state, self.h["t_out_act"], t_in)
            ex.set_actuator_value(state, self.h["mdot_act"], 0.0)
            self._set_electric_outputs(state, e_cmp_w=0.0, e_cmp_j=0.0)
            return 0

        # Heating requested. The loop hands mdot=0 until the component *requests*
        # flow, so set the mass-flow actuator to design flow when inlet flow is
        # zero (mirrors the E+ PlantComponent:UserDefined chiller example).
        m_dot = mdot if mdot > MDOT_FLOOR else LOOP_DESIGN_VDOT * RHO_WATER
        ex.set_actuator_value(state, self.h["mdot_act"], m_dot)
        timestep_raw = ex.system_time_step(state)
        timestep_h = _finite_float_or_none(timestep_raw)
        if timestep_h is None or timestep_h <= 0.0:
            _issue_severe(
                self.api,
                state,
                f"[TmhpSurrogate] invalid EnergyPlus system timestep: {timestep_raw!r}",
            )
            ex.set_actuator_value(state, self.h["t_out_act"], t_in)
            ex.set_actuator_value(state, self.h["mdot_act"], 0.0)
            self._set_electric_outputs(state, e_cmp_w=0.0, e_cmp_j=0.0)
            return 1

        q_target = min(q_req, HP_CAPACITY)
        res = self._solve(t_in, t0, q_target, m_dot)
        converged = bool(res.get("converged"))
        reason = _normalize_failure_reason(res.get("failure_reason"))

        self._ndispatch += 1
        if converged and reason == "none":
            self._nconv += 1
        else:
            self._reasons[reason] = self._reasons.get(reason, 0) + 1
        if self._ndispatch % self._tally_every == 0:
            _log(
                f"[tally @ dispatch {self._ndispatch}] converged={self._nconv} "
                f"({100.0 * self._nconv / self._ndispatch:.1f}%) guard_trips={dict(self._reasons)}"
            )

        guard_trip = reason == "t_cond_below_t_in"
        if not guard_trip and _has_usable_cycle_output(res):
            q = float(res["Q_ref_tank [W]"])
            e_cmp = float(res["E_cmp [W]"])
            t_out = min(t_in + q / (m_dot * cp), TOUT_MAX)
            energy_j = e_cmp * 3600.0 * timestep_h
        else:
            q = e_cmp = energy_j = 0.0
            t_out = t_in

        if guard_trip:
            self._warn_guard_trip(state, reason, t_in, t0, q_target)

        ex.set_actuator_value(state, self.h["t_out_act"], t_out)
        ex.set_actuator_value(state, self.h["mdot_act"], 0.0 if guard_trip else m_dot)
        self._set_electric_outputs(state, e_cmp_w=e_cmp, e_cmp_j=energy_j)

        if self._nlog < 40:
            self._nlog += 1
            _log(
                f"[call {self._ncall:4d}] T_in={t_in:6.2f}C T0={t0:6.2f}C mdot={mdot:6.3f}kg/s "
                f"Qreq={q_req:8.1f}W -> converged={converged} reason={reason} "
                f"E_cmp={e_cmp:8.1f}W Q={q:8.1f}W T_out={t_out:6.2f}C"
            )
        return 0
