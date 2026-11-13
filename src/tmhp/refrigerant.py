"""
Refrigerant cycle calculations and optimization.
"""

import math
import threading
from collections.abc import Callable, Mapping
from typing import Any

import CoolProp.CoolProp as CP
import numpy as np

from . import calc_util as cu

__all__ = [
    "calc_ref_state",
    "create_lmtd_constraints",
    "find_ref_loop_optimal_operation",
    "props_si",
]

# ---------------------------------------------------------------------------
# Fast property evaluation
# ---------------------------------------------------------------------------
#
# ``CP.PropsSI`` builds a new equation-of-state object on every call, and that
# construction -- not the flash -- is nearly all of its cost for the Helmholtz
# fluids (R32, R134a, R290: 0.1--0.6 ms per call, about 5 ms for the twenty
# calls of one cycle state). The low-level ``AbstractState`` runs the same
# flash routines on an object kept between calls: one cycle state in about
# 0.05 ms, with bit-identical values. The states are kept per thread (a
# CoolProp state is not thread-safe) and per fluid string.

_STATES = threading.local()

#: ``(name1, name2)`` of a ``PropsSI`` input pair -> (CoolProp input pair, inputs swapped).
_INPUT_PAIRS: dict[tuple[str, str], tuple[int, bool]] = {
    ("T", "Q"): (CP.QT_INPUTS, True),
    ("Q", "T"): (CP.QT_INPUTS, False),
    ("P", "Q"): (CP.PQ_INPUTS, False),
    ("Q", "P"): (CP.PQ_INPUTS, True),
    ("T", "P"): (CP.PT_INPUTS, True),
    ("P", "T"): (CP.PT_INPUTS, False),
    ("P", "S"): (CP.PSmass_INPUTS, False),
    ("S", "P"): (CP.PSmass_INPUTS, True),
    ("P", "H"): (CP.HmassP_INPUTS, True),
    ("H", "P"): (CP.HmassP_INPUTS, False),
}


def _fluid_state(refrigerant: str) -> Any:
    """This thread's low-level CoolProp state of ``refrigerant``, or ``None``.

    ``None`` for anything but a single (pure or pseudo-pure) fluid of the
    default Helmholtz backend -- a mixture string, a ``.mix`` file, another
    backend -- which then goes through ``CP.PropsSI`` as before.
    """
    cache: dict[str, Any] = _STATES.__dict__.setdefault("states", {})
    try:
        return cache[refrigerant]
    except KeyError:
        pass
    state = None
    if isinstance(refrigerant, str) and not any(mark in refrigerant for mark in ("[", "&", ".mix")):
        try:
            backend, fluid = CP.extract_backend(refrigerant)
            if backend in ("?", "HEOS"):
                candidate = CP.AbstractState("HEOS", fluid)
                if len(candidate.fluid_names()) == 1:
                    state = candidate
        except Exception:
            state = None
    cache[refrigerant] = state
    return state


def _read(state: Any, outputs: str) -> tuple[float, ...]:
    values = []
    for output in outputs:
        if output == "P":
            value = state.p()
        elif output == "T":
            value = state.T()
        elif output == "H":
            value = state.hmass()
        elif output == "S":
            value = state.smass()
        elif output == "D":
            value = state.rhomass()
        else:
            raise KeyError(output)
        if not math.isfinite(value):
            # ``PropsSI`` raises for a value it could not evaluate.
            raise ValueError(f"CoolProp output {output!r} is not finite")
        values.append(value)
    return tuple(values)


def _props(outputs: str, name1: str, value1: float, name2: str, value2: float, refrigerant: str) -> tuple[float, ...]:
    """Several outputs (letters of ``PTHSD``, mass based) of one state, e.g. ``_props("PHS", "T", T, "Q", 1, ref)``.

    One flash serves every output. Same values and the same ``ValueError`` on
    an invalid state as one ``CP.PropsSI`` call per output.
    """
    state = _fluid_state(refrigerant)
    pair = _INPUT_PAIRS.get((name1, name2))
    if state is None or pair is None:
        return tuple(float(CP.PropsSI(output, name1, value1, name2, value2, refrigerant)) for output in outputs)
    input_pair, swapped = pair
    if swapped:
        state.update(input_pair, value2, value1)
    else:
        state.update(input_pair, value1, value2)
    return _read(state, outputs)


def props_si(output: str, name1: str, value1: float, name2: str, value2: float, refrigerant: str) -> float:
    """``CoolProp.PropsSI`` for one of ``P``, ``T``, ``H``, ``S``, ``D`` (mass based), without its per-call set-up.

    A drop-in for the cycle solvers' inner loops: the same value and the same
    ``ValueError`` on an invalid state as ``CP.PropsSI(output, name1, value1,
    name2, value2, refrigerant)``, evaluated on a CoolProp state kept between
    calls. Input pairs ``(T, Q)``, ``(P, Q)``, ``(T, P)``, ``(P, S)`` and
    ``(P, H)`` of a single Helmholtz-backend fluid take the fast path; anything
    else is passed to ``CP.PropsSI``.
    """
    if output not in _FAST_OUTPUTS:
        return float(CP.PropsSI(output, name1, value1, name2, value2, refrigerant))
    return _props(output, name1, value1, name2, value2, refrigerant)[0]


_FAST_OUTPUTS = frozenset("PTHSD")


def calc_ref_state(
    T_evap_K: float,  # evaporating temperature [K] (treated as saturation T)
    T_cond_K: float,  # condensing temperature [K] (treated as saturation T)
    refrigerant: str,  # refrigerant name
    eta_cmp_isen: float | Callable,  # compressor isentropic efficiency (scalar or callable)
    mode: str = "heating",  # operating mode ('heating' or 'cooling')
    dT_superheat: float = 0.0,  # [K] evaporator outlet superheat (State 1* → 1)
    dT_subcool: float = 0.0,  # [K] condenser outlet subcool (State 3* → 3)
    is_active: bool = True,  # active flag (returns nan-filled dict when False)
    rps: float | None = None,  # compressor speed [rps]
    x_suction: float = 1.0,  # [-] compressor-inlet vapour quality (< 1: wet suction, superheat ignored)
) -> dict[str, Any]:
    """Compute the four thermodynamic state points of the refrigerant cycle.

    The vapour-compression cycle has four canonical state points:

    - State 1 (``cmp_in``):  compressor inlet  — low-pressure superheated vapour
      at the evaporator outlet.
    - State 2 (``cmp_out``): compressor outlet — high-pressure superheated vapour
      at the condenser inlet.
    - State 3 (``exp_in``):  expansion-valve inlet  — high-pressure subcooled
      liquid at the condenser outlet.
    - State 4 (``exp_out``): expansion-valve outlet — low-pressure two-phase
      mixture at the evaporator inlet.

    Keys in the returned dict are always assigned by the physical compressor /
    expander ports; the ``mode`` argument is preserved verbatim under the
    ``"mode"`` key but does not change the key naming.

    Wet suction
    -----------
    ``x_suction < 1`` puts State 1 inside the two-phase dome at the evaporating
    pressure (the evaporator outlet is two-phase, so the evaporator duty is
    ``m_dot (h_1 - h_4)`` with the wet ``h_1``). The suction density is the
    homogeneous two-phase density ``1 / (x v_g + (1 - x) v_l)`` -- it is what
    sets the swept mass flow -- and the isentropic discharge state is the
    isentropic compression of that two-phase state. Used by the
    discharge-temperature control of :mod:`tmhp.compressor_envelope`.

    Note
    ----
    Whether a given heat exchanger acts as the evaporator or the condenser in
    heating versus cooling mode is decided by the caller (``_calc_state``),
    which chooses ``T_evap_K`` and ``T_cond_K`` accordingly before calling
    this function.
    """

    # When inactive, short-circuit with a dict of NaNs so downstream code can
    # still index by key without special-casing missing fields.
    if not is_active:
        return {
            "P_ref_cmp_in [Pa]": np.nan,
            "P_ref_cmp_out [Pa]": np.nan,
            "P_ref_exp_in [Pa]": np.nan,
            "P_ref_exp_out [Pa]": np.nan,
            "P_ref_evap_sat [Pa]": np.nan,
            "P_ref_cond_sat_l [Pa]": np.nan,
            "P_ref_cond_sat_v [Pa]": np.nan,
            "T_ref_cmp_in_K": np.nan,
            "T_ref_cmp_out_K": np.nan,
            "T_ref_exp_in_K": np.nan,
            "T_ref_exp_out_K": np.nan,
            "T_ref_evap_sat_K": np.nan,
            "T_ref_cond_sat_v_K": np.nan,
            "T_ref_cond_sat_l_K": np.nan,
            "T_ref_cmp_in [°C]": np.nan,
            "T_ref_cmp_out [°C]": np.nan,
            "T_ref_exp_in [°C]": np.nan,
            "T_ref_exp_out [°C]": np.nan,
            "T_ref_evap_sat [°C]": np.nan,
            "T_ref_cond_sat_v [°C]": np.nan,
            "T_ref_cond_sat_l [°C]": np.nan,
            "h_ref_cmp_in [J/kg]": np.nan,
            "h_ref_cmp_out [J/kg]": np.nan,
            "h_ref_cond_sat_v [J/kg]": np.nan,
            "h_ref_exp_in [J/kg]": np.nan,
            "h_ref_exp_out [J/kg]": np.nan,
            "h_ref_evap_sat [J/kg]": np.nan,
            "h_ref_cond_sat_l [J/kg]": np.nan,
            "s_ref_cmp_in [J/(kg·K)]": np.nan,
            "s_ref_cmp_out [J/(kg·K)]": np.nan,
            "s_ref_cond_sat_v [J/(kg·K)]": np.nan,
            "s_ref_exp_in [J/(kg·K)]": np.nan,
            "s_ref_exp_out [J/(kg·K)]": np.nan,
            "s_ref_evap_sat [J/(kg·K)]": np.nan,
            "s_ref_cond_sat_l [J/(kg·K)]": np.nan,
            "rho_ref_cmp_in [kg/m3]": np.nan,
            "mode": mode,
        }

    state, h2_isen = calc_ref_state_without_discharge(
        T_evap_K,
        T_cond_K,
        refrigerant,
        mode=mode,
        dT_superheat=dT_superheat,
        dT_subcool=dT_subcool,
        x_suction=x_suction,
    )

    if callable(eta_cmp_isen):
        import inspect

        P_evap, P_cond = state["P_ref_cmp_in [Pa]"], state["P_ref_cmp_out [Pa]"]
        sig = inspect.signature(eta_cmp_isen)
        if len(sig.parameters) == 2 and rps is not None:
            val_eta_cmp_isen = eta_cmp_isen(P_cond / P_evap, rps)
        else:
            val_eta_cmp_isen = eta_cmp_isen(P_cond / P_evap)
    else:
        val_eta_cmp_isen = eta_cmp_isen

    return _set_discharge_state(state, h2_isen, val_eta_cmp_isen, refrigerant)


def calc_ref_state_without_discharge(
    T_evap_K: float,
    T_cond_K: float,
    refrigerant: str,
    *,
    mode: str = "heating",
    dT_superheat: float = 0.0,
    dT_subcool: float = 0.0,
    x_suction: float = 1.0,
) -> tuple[dict[str, Any], float]:
    """The cycle state of :func:`calc_ref_state` before the compressor's efficiency is known.

    A cycle solver first needs the pressures, the suction state and the
    isentropic discharge enthalpy to find the compressor speed, and only then
    knows the isentropic efficiency that fixes the discharge state. Everything
    but that discharge state is independent of the efficiency, so it is
    evaluated once here and :func:`with_discharge_state` adds the discharge
    state -- instead of two full :func:`calc_ref_state` calls, which repeat
    every flash (the isentropic one and the discharge one are the two
    expensive ones of the eight).

    Returns ``(state, h_isen)``: the dict of :func:`calc_ref_state` with the
    three discharge entries (``h_ref_cmp_out``, ``T_ref_cmp_out``,
    ``s_ref_cmp_out``) still NaN, and the isentropic discharge enthalpy
    [J/kg]. Raises ``ValueError`` where :func:`calc_ref_state` does.
    """
    # Step 1: saturation temperatures and pressures, with the saturation-state
    # enthalpy / entropy at the evaporator and condenser.
    T_ref_evap_sat_K = T_evap_K
    T_ref_cond_sat_l_K = T_cond_K

    dry_saturated = not x_suction < 1.0 and abs(dT_superheat) < 1e-6
    if dry_saturated:
        P_evap, h_ref_evap_sat, s_ref_evap_sat, rho_ref_cmp_in = _props(
            "PHSD", "T", T_ref_evap_sat_K, "Q", 1, refrigerant
        )
    else:
        P_evap, h_ref_evap_sat, s_ref_evap_sat = _props("PHS", "T", T_ref_evap_sat_K, "Q", 1, refrigerant)
    P_cond, h_ref_cond_sat_l, s_ref_cond_sat_l = _props("PHS", "T", T_ref_cond_sat_l_K, "Q", 0, refrigerant)

    # Step 2: State 1 — actual superheated vapour at the compressor inlet.
    T_ref_cmp_in_K = T_ref_evap_sat_K + dT_superheat

    if x_suction < 1.0:
        # Wet suction (see "Wet suction" above): saturation temperature, no superheat.
        T_ref_cmp_in_K = T_ref_evap_sat_K
        h_ref_cmp_in, s_ref_cmp_in, rho_ref_cmp_in = _props("HSD", "P", P_evap, "Q", x_suction, refrigerant)
    elif dry_saturated:
        h_ref_cmp_in = h_ref_evap_sat
        s_ref_cmp_in = s_ref_evap_sat
    else:
        h_ref_cmp_in, s_ref_cmp_in, rho_ref_cmp_in = _props("HSD", "T", T_ref_cmp_in_K, "P", P_evap, refrigerant)

    # Step 3: State 2 — high-pressure superheated vapour at the compressor
    # outlet. Only its isentropic enthalpy here; `_set_discharge_state` adds
    # the state once the isentropic efficiency is known.
    (h2_isen,) = _props("H", "P", P_cond, "S", s_ref_cmp_in, refrigerant)

    # Step 3.5: State 2* — point where the high-pressure stream first reaches
    # the condenser saturation vapour line.
    T_ref_cond_sat_v_K = T_ref_cond_sat_l_K
    P_ref_cond_sat_v = P_cond
    h_ref_cond_sat_v, s_ref_cond_sat_v = _props("HS", "P", P_cond, "Q", 1, refrigerant)

    # Step 4: State 3 — actual subcooled liquid at the expansion-valve inlet.
    T_ref_exp_in_K = T_ref_cond_sat_l_K - dT_subcool

    if abs(dT_subcool) < 1e-6:
        h_ref_exp_in = h_ref_cond_sat_l
        s_ref_exp_in = s_ref_cond_sat_l
    else:
        h_ref_exp_in, s_ref_exp_in = _props("HS", "T", T_ref_exp_in_K, "P", P_cond, refrigerant)

    # Step 5: State 4 — two-phase mixture at the expansion-valve outlet
    # (isenthalpic expansion: h_4 = h_3).
    h_ref_exp_out = h_ref_exp_in
    T_ref_exp_out_K, s_ref_exp_out = _props("TS", "P", P_evap, "H", h_ref_exp_out, refrigerant)

    # The discharge entries keep their place in the key order (it is the
    # column order of every result frame).
    result = {
        "P_ref_cmp_in [Pa]": P_evap,
        "P_ref_cmp_out [Pa]": P_cond,
        "P_ref_exp_in [Pa]": P_cond,
        "P_ref_exp_out [Pa]": P_evap,
        "P_ref_evap_sat [Pa]": P_evap,
        "P_ref_cond_sat_l [Pa]": P_cond,
        "P_ref_cond_sat_v [Pa]": P_ref_cond_sat_v,
        "T_ref_cmp_in_K": T_ref_cmp_in_K,
        "T_ref_cmp_out_K": np.nan,
        "T_ref_exp_in_K": T_ref_exp_in_K,
        "T_ref_exp_out_K": T_ref_exp_out_K,
        "T_ref_evap_sat_K": T_ref_evap_sat_K,
        "T_ref_cond_sat_v_K": T_ref_cond_sat_v_K,
        "T_ref_cond_sat_l_K": T_ref_cond_sat_l_K,
        "T_ref_cmp_in [°C]": cu.K2C(T_ref_cmp_in_K),
        "T_ref_cmp_out [°C]": np.nan,
        "T_ref_exp_in [°C]": cu.K2C(T_ref_exp_in_K),
        "T_ref_exp_out [°C]": cu.K2C(T_ref_exp_out_K),
        "T_ref_evap_sat [°C]": cu.K2C(T_ref_evap_sat_K),
        "T_ref_cond_sat_l [°C]": cu.K2C(T_ref_cond_sat_l_K),
        "T_ref_cond_sat_v [°C]": cu.K2C(T_ref_cond_sat_v_K),
        "h_ref_cmp_in [J/kg]": h_ref_cmp_in,
        "h_ref_cmp_out [J/kg]": np.nan,
        "h_ref_cond_sat_v [J/kg]": h_ref_cond_sat_v,
        "h_ref_exp_in [J/kg]": h_ref_exp_in,
        "h_ref_exp_out [J/kg]": h_ref_exp_out,
        "h_ref_evap_sat [J/kg]": h_ref_evap_sat,
        "h_ref_cond_sat_l [J/kg]": h_ref_cond_sat_l,
        "s_ref_cmp_in [J/(kg·K)]": s_ref_cmp_in,
        "s_ref_cmp_out [J/(kg·K)]": np.nan,
        "s_ref_cond_sat_v [J/(kg·K)]": s_ref_cond_sat_v,
        "s_ref_exp_in [J/(kg·K)]": s_ref_exp_in,
        "s_ref_exp_out [J/(kg·K)]": s_ref_exp_out,
        "s_ref_evap_sat [J/(kg·K)]": s_ref_evap_sat,
        "s_ref_cond_sat_l [J/(kg·K)]": s_ref_cond_sat_l,
        "rho_ref_cmp_in [kg/m3]": rho_ref_cmp_in,
        "mode": mode,
    }

    return result, h2_isen


def _set_discharge_state(state: dict[str, Any], h_isen: float, eta_cmp_isen: float, refrigerant: str) -> dict[str, Any]:
    """Fill the discharge state (State 2) of ``state`` in place and return it."""
    h_ref_cmp_in = state["h_ref_cmp_in [J/kg]"]
    h_ref_cmp_out = h_ref_cmp_in + (h_isen - h_ref_cmp_in) / eta_cmp_isen
    try:
        T_ref_cmp_out_K, s_ref_cmp_out = _props("TS", "P", state["P_ref_cmp_out [Pa]"], "H", h_ref_cmp_out, refrigerant)
    except ValueError:
        # H is too high — it exceeds CoolProp's Tmax for this fluid
        # (e.g. 435 K for R32). Do NOT clip h_ref_cmp_out, since that would
        # silently break the energy balance; just record T and s as NaN.
        T_ref_cmp_out_K = np.nan
        s_ref_cmp_out = np.nan
    state["T_ref_cmp_out_K"] = T_ref_cmp_out_K
    state["T_ref_cmp_out [°C]"] = cu.K2C(T_ref_cmp_out_K)
    state["h_ref_cmp_out [J/kg]"] = h_ref_cmp_out
    state["s_ref_cmp_out [J/(kg·K)]"] = s_ref_cmp_out
    return state


def with_discharge_state(
    state: Mapping[str, Any], h_isen: float, eta_cmp_isen: float, refrigerant: str
) -> dict[str, Any]:
    """The cycle state :func:`calc_ref_state` returns for ``eta_cmp_isen``, from its efficiency-free part.

    ``state`` and ``h_isen`` are the pair of
    :func:`calc_ref_state_without_discharge`. The result equals
    ``calc_ref_state(..., eta_cmp_isen=eta_cmp_isen)`` for the same arguments
    (same values, same key order); ``state`` is left as it is, so several
    efficiencies can be tried on one.
    """
    return _set_discharge_state(dict(state), h_isen, eta_cmp_isen, refrigerant)


#: Solver-internal absolute-temperature entries of a cycle-state dict.
#: Kelvin is the solver's working unit; the reporting boundary publishes the
#: ``[°C]`` duplicates of these same state points instead.
_INTERNAL_STATE_KEYS: frozenset[str] = frozenset(
    {
        "T_ref_cmp_in_K",
        "T_ref_cmp_out_K",
        "T_ref_exp_in_K",
        "T_ref_exp_out_K",
        "T_ref_evap_sat_K",
        "T_ref_cond_sat_v_K",
        "T_ref_cond_sat_l_K",
    }
)


def reportable_state(cs: Mapping[str, Any]) -> dict[str, Any]:
    """Copy a cycle state without its solver-internal Kelvin entries.

    Models seed each result row from the cycle state returned by
    ``calc_ref_state``. That dict carries every state-point temperature twice:
    once in Kelvin for the solver and once in ``[°C]`` for reporting. Only the
    ``[°C]`` form belongs in the result frame and the exported CSV.

    Parameters
    ----------
    cs : Mapping
        Cycle state as returned by :func:`calc_ref_state`.

    Returns
    -------
    dict
        Shallow copy of ``cs`` with the seven ``T_ref_*_K`` entries removed;
        every other key keeps its original order and value.
    """
    return {k: v for k, v in cs.items() if k not in _INTERNAL_STATE_KEYS}


def create_lmtd_constraints() -> tuple[Any, Any]:
    """Create LMTD-based constraint functions for cycle optimization.

    Optimization requires that the heat transfer calculated by LMTD matches
    the heat transferred by the refrigerant cycle.

    Returns
    -------
    tuple[Any, Any]
        Tuple of constraint functions (constraint_tank, constraint_hx).
    """

    def constraint_tank(perf: dict[str, Any]) -> float:
        """Condenser constraint: Q_LMTD_cond - Q_ref_cond = 0"""
        if perf is None or "Q_cond" not in perf or "Q_cond_LMTD" not in perf:
            return 1e6
        return float(perf["Q_cond_LMTD"] - perf["Q_cond"])

    def constraint_hx(perf: dict[str, Any]) -> float:
        """Evaporator constraint: Q_LMTD_evap - Q_ref_evap = 0"""
        if perf is None or "Q_evap" not in perf or "Q_evap_LMTD" not in perf:
            return 1e6
        return float(perf["Q_evap_LMTD"] - perf["Q_evap"])

    return constraint_tank, constraint_hx


def find_ref_loop_optimal_operation(
    simulator_func: Any,
    refrigerant: str,
    load_W: float,
    initial_guess: list[float],
    bounds: list[tuple[float, float]],
    constraint_funcs: list[Any] | None = None,
) -> dict[str, Any] | None:
    """Find the optimal operation point for the refrigerant loop.

    Minimizes compressor power while satisfying target load and LMTD constraints.

    Parameters
    ----------
    simulator_func : callable
        Function that takes `[dT_ref_HX, dT_ref_tank]` and returns a perf dict.
    refrigerant : str
        Refrigerant name.
    load_W : float
        Target heat load [W].
    initial_guess : list[float]
        Initial guess for `[dT_evap, dT_cond]`.
    bounds : list[tuple[float, float]]
        Bounds for `[dT_evap, dT_cond]`.
    constraint_funcs : list[callable], optional
        List of constraint functions. Each takes `perf` and returns a value
        that should be 0.

    Returns
    -------
    dict[str, Any] | None
        Optimal performance dictionary, or None if optimization fails.
    """
    from scipy.optimize import minimize

    def objective(x: np.ndarray) -> float:
        perf = simulator_func(x)
        if perf is None or "W_comp" not in perf:
            return 1e6

        # Add penalty if load is not met
        load_diff = abs(perf.get("Q_cond", 0) - load_W)
        penalty = (load_diff / load_W) ** 2 * 1e5 if load_W > 0 else 0

        return float(perf["W_comp"] + penalty)

    constraints = []
    if constraint_funcs:
        for cf in constraint_funcs:

            def make_constraint(c_func: Any) -> Any:
                def constraint(x: np.ndarray) -> float:
                    perf = simulator_func(x)
                    return float(c_func(perf))

                return constraint

            constraints.append({"type": "eq", "fun": make_constraint(cf)})

    try:
        res = minimize(
            objective,
            initial_guess,
            bounds=bounds,
            constraints=constraints,
            method="SLSQP",
            options={"disp": False, "ftol": 1e-4, "maxiter": 50},
        )
        if res.success:
            return simulator_func(res.x)  # type: ignore[no-any-return]
    except Exception:
        pass

    return None
