"""Property evaluation on a CoolProp state kept between calls (:mod:`tmhp.refrigerant`).

``CP.PropsSI`` builds a new equation-of-state object per call; the cycle
solvers evaluate thousands of states per operating point, so that set-up was
nearly all of their run time. The fast path has to return the very same
numbers -- the goldens and the committed validation results are compared at
1e-6 -- and raise the same ``ValueError`` on a state CoolProp cannot evaluate.
"""

from __future__ import annotations

import math
import threading

import CoolProp.CoolProp as CP
import pytest

from tmhp import refrigerant
from tmhp.refrigerant import calc_ref_state, props_si

PURE = ("R32", "R134a", "R410A", "R290", "HEOS::R32")
MIXTURE = "R32[0.5]&R125[0.5]"


def _reference_state(ref, T_evap, T_cond, superheat, subcool, x, eta):
    """The four state points written with one ``CP.PropsSI`` call per property."""
    P1 = CP.PropsSI("P", "T", T_evap, "Q", 1, ref)
    P2 = CP.PropsSI("P", "T", T_cond, "Q", 0, ref)
    if x < 1.0:
        h1, s1, rho1 = (CP.PropsSI(k, "P", P1, "Q", x, ref) for k in "HSD")
    elif abs(superheat) < 1e-6:
        h1, s1, rho1 = (CP.PropsSI(k, "T", T_evap, "Q", 1, ref) for k in "HSD")
    else:
        h1, s1, rho1 = (CP.PropsSI(k, "T", T_evap + superheat, "P", P1, ref) for k in "HSD")
    h2 = h1 + (CP.PropsSI("H", "P", P2, "S", s1, ref) - h1) / eta
    if abs(subcool) < 1e-6:
        h3, s3 = (CP.PropsSI(k, "T", T_cond, "Q", 0, ref) for k in "HS")
    else:
        h3, s3 = (CP.PropsSI(k, "T", T_cond - subcool, "P", P2, ref) for k in "HS")
    return {
        "P_ref_cmp_in [Pa]": P1,
        "P_ref_cmp_out [Pa]": P2,
        "h_ref_cmp_in [J/kg]": h1,
        "s_ref_cmp_in [J/(kg·K)]": s1,
        "rho_ref_cmp_in [kg/m3]": rho1,
        "h_ref_cmp_out [J/kg]": h2,
        "T_ref_cmp_out_K": CP.PropsSI("T", "P", P2, "H", h2, ref),
        "s_ref_cmp_out [J/(kg·K)]": CP.PropsSI("S", "P", P2, "H", h2, ref),
        "h_ref_cond_sat_v [J/kg]": CP.PropsSI("H", "P", P2, "Q", 1, ref),
        "s_ref_cond_sat_v [J/(kg·K)]": CP.PropsSI("S", "P", P2, "Q", 1, ref),
        "h_ref_exp_in [J/kg]": h3,
        "s_ref_exp_in [J/(kg·K)]": s3,
        "T_ref_exp_out_K": CP.PropsSI("T", "P", P1, "H", h3, ref),
        "s_ref_exp_out [J/(kg·K)]": CP.PropsSI("S", "P", P1, "H", h3, ref),
    }


SUCTION = ((0.0, 1.0), (5.0, 1.0), (1e-7, 1.0), (0.0, 0.92), (5.0, 0.97))


@pytest.mark.parametrize("ref", PURE)
def test_cycle_state_is_bit_identical_to_propssi(ref):
    for T_evap, T_cond in ((253.15, 318.15), (268.15, 338.15), (283.15, 303.15)):
        for superheat, x in SUCTION:
            for subcool, eta in ((0.0, 1.0), (3.0, 0.55)):
                state = calc_ref_state(
                    T_evap, T_cond, ref, eta, dT_superheat=superheat, dT_subcool=subcool, x_suction=x, rps=40.0
                )
                for key, value in _reference_state(ref, T_evap, T_cond, superheat, subcool, x, eta).items():
                    assert state[key] == value, (key, T_evap, T_cond, superheat, x, subcool, eta)


def test_mixture_string_still_goes_through_propssi():
    """Anything but a single Helmholtz fluid keeps the ``PropsSI`` route, one call per output."""
    assert refrigerant._fluid_state(MIXTURE) is None
    assert refrigerant._fluid_state("R407C.mix") is None
    expected = tuple(CP.PropsSI(key, "T", 270.0, "Q", 1, MIXTURE) for key in "PHS")
    assert refrigerant._props("PHS", "T", 270.0, "Q", 1, MIXTURE) == expected
    assert props_si("P", "T", 270.0, "Q", 1, MIXTURE) == expected[0]


def test_pure_fluid_cycle_state_does_not_call_propssi(monkeypatch):
    """The cost this path removes: no per-call CoolProp set-up for a single HEOS fluid."""
    calls = []

    def counted(*args):
        calls.append(args)
        return CP.PropsSI(*args)

    monkeypatch.setattr(refrigerant.CP, "PropsSI", counted, raising=True)
    try:
        calc_ref_state(268.15, 338.15, "R32", 0.6, dT_superheat=5.0, dT_subcool=3.0)
        calc_ref_state(268.15, 338.15, "R32", 0.6, x_suction=0.95)
        assert props_si("H", "P", 2.0e6, "S", 2100.0, "R32") > 0.0
    finally:
        monkeypatch.undo()
    assert calls == []


@pytest.mark.parametrize(
    "args",
    [
        ("P", "T", 270.0, "Q", 1, "R32"),
        ("T", "P", 2.5e6, "Q", 0, "R32"),
        ("H", "P", 8.0e5, "Q", 0.93, "R134a"),
        ("D", "T", 275.0, "P", 2.0e5, "R134a"),
        ("S", "T", 320.0, "P", 2.5e6, "R290"),
        ("H", "P", 2.5e6, "S", 2150.0, "R32"),
        ("T", "P", 3.0e6, "H", 5.6e5, "R32"),
        ("T", "H", 5.6e5, "P", 3.0e6, "R32"),  # inputs in either order
        ("H", "Q", 1, "T", 270.0, "R410A"),
        ("C", "T", 300.0, "P", 1.0e5, "R32"),  # an output outside the fast set
        ("H", "T", 300.0, "D", 30.0, "R32"),  # an input pair outside the fast set
    ],
)
def test_props_si_is_coolprop_propssi(args):
    assert props_si(*args) == CP.PropsSI(*args)


@pytest.mark.parametrize("args", [("P", "T", 400.0, "Q", 1, "R32"), ("H", "P", 3.0e6, "S", 1.0e9, "R32")])
def test_invalid_state_raises_value_error_like_propssi(args):
    with pytest.raises(ValueError):
        CP.PropsSI(*args)
    with pytest.raises(ValueError):
        props_si(*args)


def test_discharge_beyond_the_equation_of_state_is_nan_not_an_error():
    # eta 0.02 puts the discharge enthalpy far above the fluid's range (as before the fast path).
    state = calc_ref_state(253.15, 338.15, "R32", 0.02)
    assert math.isnan(state["T_ref_cmp_out_K"]) and math.isnan(state["s_ref_cmp_out [J/(kg·K)]"])
    assert math.isfinite(state["h_ref_cmp_out [J/kg]"])


def test_each_thread_keeps_its_own_coolprop_state():
    """A CoolProp state object is not thread-safe: threads must not share one."""
    mine = refrigerant._fluid_state("R32")
    assert mine is not None and refrigerant._fluid_state("R32") is mine
    seen = []
    worker = threading.Thread(target=lambda: seen.append(refrigerant._fluid_state("R32")))
    worker.start()
    worker.join()
    assert seen[0] is not None and seen[0] is not mine
