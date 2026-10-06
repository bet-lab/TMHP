"""Monkeypatch that adds a refrigerant-side resistance to the coil UA.

The production model scales the coil UA with **airflow only**
(``hx_fan.calc_UA_from_dV_fan``: ``UA = UA_rated (v/v_rated)^n``).  This module
replaces that law, at runtime and only inside a sensitivity script, with a
two-resistance series model (plan Sec. 7-8)::

    R_total,rated = 1 / UA_rated
    R_ref,rated   = f_ref       * R_total,rated
    R_other,rated = (1 - f_ref) * R_total,rated

    R_other = R_other,rated * x_air^(-n)      x_air = dV_fan / dV_fan_rated
    R_ref   = R_ref,rated   * x_ref^(-m)      x_ref = m_dot_ref / m_dot_ref,rated

    UA      = 1 / (R_other + R_ref)

``f_ref = 0`` collapses to ``1 / (R_total,rated x_air^-n) = UA_rated x_air^n``,
i.e. the production law **exactly** -- that identity is the regression check
in :func:`selftest`.

The refrigerant mass flow is not an argument of the UA call.  It is computed
in ``AirSourceHeatPump._calc_state`` a few lines above the coil solve, so the
coil entry point is wrapped by a shim that lifts ``m_dot_ref`` out of its
caller's frame and parks it in module state.  The shim *raises* when the local
is missing rather than falling back to a neutral value: a silent failure here
would look exactly like "the effect is small".

Nothing in ``src/`` is modified; the patch is installed per process by
:func:`install` and the state is set per run by :func:`set_law`.
"""

from __future__ import annotations

import sys

import tmhp.air_source_heat_pump as _ashp
import tmhp.enex_functions as _ef

__all__ = ["install", "set_law", "state", "selftest"]

#: Live parameters of the resistance law plus the captured refrigerant flow.
state: dict = {
    "f_ref": 0.0,
    "m_exp": 0.6,
    "m_dot_rated": None,  # None -> refrigerant term disabled (baseline pass 1)
    "m_dot_cur": None,
    "ua_calls": 0,
    "x_ref_min": float("inf"),
}

_orig_ua = _ef.calc_UA_from_dV_fan
_orig_hx = _ashp.calc_HX_perf_for_target_heat
_installed = False


def _ua_series(dV_fan, dV_fan_rated, A_cross, UA, exponent=0.71):
    """Two-resistance UA law; identical to the original when ``f_ref == 0``."""
    v = dV_fan / A_cross if A_cross > 0 else 0.0
    v_rated = dV_fan_rated / A_cross if A_cross > 0 else 0.0
    x_air = (v / v_rated) if v_rated > 0 else 0.0

    f = float(state["f_ref"])
    m_dot_rated = state["m_dot_rated"]
    state["ua_calls"] += 1
    if f <= 0.0 or m_dot_rated is None:
        # Baseline: keep the production expression verbatim.
        return float(UA * x_air**exponent)

    m_dot_cur = state["m_dot_cur"]
    if m_dot_cur is None:
        raise RuntimeError("refrigerant UA patch: m_dot_ref was never captured before a UA evaluation")

    x_ref = m_dot_cur / m_dot_rated
    if not (x_ref > 1e-9):
        x_ref = 1e-9
    if x_ref < state["x_ref_min"]:
        state["x_ref_min"] = x_ref
    if not (x_air > 1e-12):
        x_air = 1e-12

    r_total_rated = 1.0 / UA
    r_other = (1.0 - f) * r_total_rated * x_air ** (-exponent)
    r_ref = f * r_total_rated * x_ref ** (-float(state["m_exp"]))
    return float(1.0 / (r_other + r_ref))


def _hx_shim(*args, **kwargs):
    """Capture ``m_dot_ref`` from the calling frame, then delegate."""
    caller = sys._getframe(1)
    if "m_dot_ref" not in caller.f_locals:
        raise RuntimeError(
            "refrigerant UA patch: expected local 'm_dot_ref' in caller "
            f"{caller.f_code.co_qualname} ({caller.f_code.co_filename}:{caller.f_lineno}); "
            "the hook point in AirSourceHeatPump._calc_state has moved"
        )
    state["m_dot_cur"] = float(caller.f_locals["m_dot_ref"])
    return _orig_hx(*args, **kwargs)


def install() -> None:
    """Install both patches in this process (idempotent)."""
    global _installed
    if _installed:
        return
    _ef.calc_UA_from_dV_fan = _ua_series  # type: ignore[assignment]
    _ashp.calc_HX_perf_for_target_heat = _hx_shim  # type: ignore[assignment]
    _installed = True


def set_law(f_ref: float, m_exp: float, m_dot_rated: float | None) -> None:
    """Set the resistance-law parameters for the next solve."""
    state["f_ref"] = float(f_ref)
    state["m_exp"] = float(m_exp)
    state["m_dot_rated"] = None if m_dot_rated is None else float(m_dot_rated)
    state["m_dot_cur"] = None
    state["ua_calls"] = 0
    state["x_ref_min"] = float("inf")


def selftest() -> None:
    """Assert the f_ref = 0 identity against the untouched production law."""
    cases = [
        # dV_fan, dV_rated, A_cross, UA_rated, exponent
        (0.50, 0.50, 0.30, 400.0, 0.71),
        (0.10, 0.50, 0.30, 400.0, 0.71),
        (0.025, 0.50, 0.30, 1200.0, 0.71),
        (0.33, 0.47, 0.21, 812.5, 0.65),
    ]
    set_law(0.0, 0.6, 1.0)
    for args in cases:
        a = _orig_ua(*args)
        b = _ua_series(*args)
        assert a == b, f"f_ref=0 must reproduce the production law exactly: {a!r} != {b!r} for {args}"
    # At a fixed airflow the refrigerant term must bite as x_ref falls.
    set_law(0.2, 0.6, 1.0)
    state["m_dot_cur"] = 1.0
    at_rated_flow = _ua_series(0.2, 0.5, 0.3, 400.0, 0.71)
    state["m_dot_cur"] = 0.4
    at_low_flow = _ua_series(0.2, 0.5, 0.3, 400.0, 0.71)
    assert at_low_flow < at_rated_flow, "series law failed to reduce UA at reduced refrigerant flow"
    # ...and at the rated point (x_air = x_ref = 1) the law returns UA_rated
    # exactly, whatever f_ref is.
    state["m_dot_cur"] = 1.0
    assert abs(_ua_series(0.5, 0.5, 0.3, 400.0, 0.71) - 400.0) < 1e-9
    set_law(0.0, 0.6, None)
