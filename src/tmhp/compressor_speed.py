"""Compressor speed-envelope guard (capacity based).

A variable-speed compressor can only deliver duties inside the band spanned by
its speed range. Below ``rps_min`` the machine still turns at ``rps_min``; it
does not stop modulating and it does not fail. Asking such a machine for less
than that minimum is therefore an ordinary operating situation -- a real
inverter unit answers it by running at its floor and cycling on/off -- not a
numerical failure.

The speed search itself is a one-dimensional root find on

    residual(rps) = Q_delivered(rps) - Q_request

which is monotonically increasing in ``rps``. When the requested duty lies
outside the deliverable band the residual keeps the same sign across the whole
interval, so a bracketing solver such as :func:`scipy.optimize.brentq` has no
root to find and raises. That exception means *the solution is outside the
interval*, which is a statement about the machine, not about the solver.

:func:`solve_compressor_speed` separates the two cases. Out-of-band requests are
**clamped** onto the nearest speed bound and reported as converged, tagged with
:data:`CAPACITY_CLAMPED_MIN` / :data:`CAPACITY_CLAMPED_MAX` so callers can tell
that the delivered duty differs from the requested one. Only a genuine solver
breakdown inside a valid bracket is reported as non-converged.

This mirrors the floor/ceiling split already used for the pressure-ratio
envelope in :mod:`tmhp.compressor_envelope`, and is shared by every heat-pump
model that solves for compressor speed so the semantics stay consistent.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import NamedTuple

import CoolProp.CoolProp as CP
from scipy.optimize import brentq

__all__ = [
    "solve_compressor_speed",
    "default_displacement",
    "RatedPoint",
    "RATED_POINT_AIR_TO_WATER",
    "RATED_POINT_AIR_TO_AIR",
    "CAPACITY_CLAMPED_MIN",
    "CAPACITY_CLAMPED_MAX",
    "DISPLACEMENT_PER_W",
]

#: Clamp code: request below the duty deliverable at ``rps_min``.
CAPACITY_CLAMPED_MIN = "min"
#: Clamp code: request above the duty deliverable at ``rps_max``.
CAPACITY_CLAMPED_MAX = "max"

#: Legacy fixed displacement per watt of nominal capacity [m^3/rev per W].
#:
#: Retained only so that code pinning the previous behaviour can reproduce it.
#: It takes no account of the working fluid, and the volumetric refrigerating
#: capacity of the fluids in common use spans a factor of 2.7 -- so a single
#: constant is roughly 50 % too large for R32 and 40 % too small for R134a.
#: :func:`default_displacement` supersedes it.
DISPLACEMENT_PER_W = 42.0e-6 / 9000.0


class RatedPoint(NamedTuple):
    """The condition at which a nameplate capacity is declared.

    A compressor is sized so that it delivers the nameplate capacity at the
    nameplate condition while running at its rated speed. Both halves of that
    sentence have to be declared before a displacement can be derived, and
    which duty the nameplate refers to differs by equipment class: an
    air-to-water unit is sold on its heating output, an air-to-air unit on its
    cooling output.
    """

    #: Saturated evaporating temperature at the rating point [degC].
    T_evap_C: float
    #: Saturated condensing temperature at the rating point [degC].
    T_cond_C: float
    #: Compressor speed at the rating point [rev/s].
    rps: float
    #: Which duty the nameplate capacity denotes: ``"heating"`` (condenser) or
    #: ``"cooling"`` (evaporator).
    duty: str
    #: Human-readable provenance, quoted in documentation.
    basis: str


#: Air-to-water rating point, EN 14511 A7/W35: outdoor air 7 degC, leaving
#: water 35 degC. The saturation temperatures below are that condition seen
#: from the refrigerant side with ordinary approach temperatures.
#:
#: The rated speed is not a guess. Inverting this relation against the nine
#: Panasonic Aquarea units whose compressor displacement is published by the
#: manufacturer's compressor division (R32 42.0 / 65.0, R290 81.0, R410A 42.4 /
#: 65.0 cm^3/rev) gives a median rated speed of 42 rev/s. At a fixed capacity
#: the three refrigerants agree to within 5 % -- the physics carries the fluid
#: dependence on its own. The residual scatter is the manufacturers' practice
#: of sharing one compressor across adjacent capacity steps (the 9 and 12 kW
#: units of every one of those three lines use the same machine), not an error
#: in the rule.
RATED_POINT_AIR_TO_WATER = RatedPoint(T_evap_C=1.0, T_cond_C=40.0, rps=40.0, duty="heating", basis="EN 14511 A7/W35")

#: Air-to-air rating point, ISO 5151 T1 cooling: outdoor 35 degC, indoor 27/19
#: degC. Rated speed from the Daikin SL-series service manual, which publishes
#: rated compressor frequency in place of displacement (52 rev/s cooling for
#: the 2.5 kW class, 72 for the 3.5 kW class).
#:
#: This is the weaker of the two rated points: two published speeds, no
#: published displacement to check the result against. Treated accordingly in
#: the documentation.
#: How far below the critical temperature a rating point is pulled when the
#: working fluid has no saturation state at the declared condition [K].
CRITICAL_MARGIN_K = 5.0

RATED_POINT_AIR_TO_AIR = RatedPoint(T_evap_C=10.0, T_cond_C=50.0, rps=60.0, duty="cooling", basis="ISO 5151 T1")


def default_displacement(
    hp_capacity: float,
    refrigerant: str = "R32",
    rated_point: RatedPoint = RATED_POINT_AIR_TO_WATER,
    *,
    dT_superheat: float = 5.0,
    dT_subcool: float = 5.0,
    eta_vol: float = 0.90,
    eta_isen: float = 0.70,
) -> float:
    r"""Swept compressor displacement implied by a nameplate capacity [m^3/rev].

    The machine has to move enough refrigerant to carry the nameplate duty at
    the nameplate condition:

    .. math::

        \dot m = \frac{\dot Q_{\mathrm{nom}}}{\Delta h},
        \qquad
        V_{\mathrm{disp}}
            = \frac{\dot m}{\rho_{1}\, \eta_{\mathrm{vol}}\, n_{\mathrm{rated}}}

    where :math:`\rho_1` is the suction density and :math:`\Delta h` is the
    enthalpy change across whichever heat exchanger the nameplate refers to --
    the evaporator for a cooling rating, the condenser for a heating rating.
    Both come from CoolProp at the declared rating point, so the dependence on
    the working fluid is pure fluid property and not a tabulated coefficient.
    That dependence is large: at a common rating point the displacement needed
    per kilowatt runs from about 3.1 cm^3/rev for R32 to 8.4 for R1234yf.

    The quantity carrying the most uncertainty is the rated speed, to which the
    result is exactly inversely proportional. It is declared per equipment
    class in :data:`RATED_POINT_AIR_TO_WATER` / :data:`RATED_POINT_AIR_TO_AIR`
    with its provenance, and a caller who knows the machine should pass
    ``V_cmp_ref`` explicitly instead.

    Displacement and ``rps_min`` jointly fix the lowest duty a machine can
    deliver, so an oversized displacement shows up as a unit that cannot
    modulate rather than as an error at the rating point: the speed solver
    absorbs a displacement error into speed, and the steady-state COP moves
    only through the speed dependence of the efficiencies.

    Parameters
    ----------
    hp_capacity
        Nameplate capacity [W], interpreted as the duty named by
        ``rated_point.duty``.
    refrigerant
        CoolProp fluid name, or a mixture specification such as
        ``"HEOS::R32[0.829248]&R1234yf[0.170752]"`` for a blend CoolProp does
        not know by name.
    rated_point
        The declared rating condition and rated speed.
    dT_superheat, dT_subcool
        Suction superheat and liquid subcooling at the rating point [K].
    eta_vol, eta_isen
        Volumetric and isentropic efficiency at the rating point. Only the
        product ``eta_vol`` and, through the discharge enthalpy, ``eta_isen``
        enter; both are representative values, not fitted.
    """
    T_evap_K = rated_point.T_evap_C + 273.15
    T_cond_K = rated_point.T_cond_C + 273.15

    # A rating point is declared for the subcritical equipment classes TMHP
    # models. A fluid whose critical temperature is below the declared
    # condensing temperature -- carbon dioxide above all -- has no saturation
    # state there, so the rating point is pulled below the critical point
    # rather than letting the property call fail. The displacement that comes
    # out is still the right order for such a fluid, because its volumetric
    # capacity is what dominates; a transcritical gas cooler is not modelled
    # and a caller sizing one should pass `V_cmp_ref` directly.
    T_crit_K = CP.PropsSI("Tcrit", refrigerant)
    if T_cond_K > T_crit_K - CRITICAL_MARGIN_K:
        T_cond_K = T_crit_K - CRITICAL_MARGIN_K
    if T_evap_K > T_cond_K - 1.0:
        T_evap_K = T_cond_K - 1.0

    p_evap = CP.PropsSI("P", "T", T_evap_K, "Q", 1, refrigerant)
    p_cond = CP.PropsSI("P", "T", T_cond_K, "Q", 1, refrigerant)

    T_suction_K = min(T_evap_K + dT_superheat, T_cond_K - 0.5)
    h_suction = CP.PropsSI("H", "T", T_suction_K, "P", p_evap, refrigerant)
    s_suction = CP.PropsSI("S", "T", T_suction_K, "P", p_evap, refrigerant)
    rho_suction = CP.PropsSI("D", "T", T_suction_K, "P", p_evap, refrigerant)

    h_liquid = CP.PropsSI("H", "T", max(T_cond_K - dT_subcool, T_evap_K + 0.5), "P", p_cond, refrigerant)
    h_discharge_isen = CP.PropsSI("H", "P", p_cond, "S", s_suction, refrigerant)

    dh_evaporator = h_suction - h_liquid
    dh_compression = (h_discharge_isen - h_suction) / eta_isen

    if rated_point.duty == "cooling":
        dh_nameplate = dh_evaporator
    elif rated_point.duty == "heating":
        dh_nameplate = dh_evaporator + dh_compression
    else:  # pragma: no cover -- guarded by the RatedPoint contract
        raise ValueError(f"rated_point.duty must be 'heating' or 'cooling', got {rated_point.duty!r}")

    mass_flow = hp_capacity / dh_nameplate
    return float(mass_flow / (rho_suction * eta_vol * rated_point.rps))


def solve_compressor_speed(
    residual: Callable[[float], float],
    rps_min: float,
    rps_max: float,
) -> tuple[float, bool, str | None]:
    """Solve for the compressor speed that meets a requested duty.

    Parameters
    ----------
    residual
        ``residual(rps) = Q_delivered(rps) - Q_request`` [W]. Must be
        monotonically increasing in ``rps``.
    rps_min, rps_max
        Compressor speed search bounds [rev/s].

    Returns
    -------
    tuple[float, bool, str | None]
        ``(rps, converged, capacity_clamped)``.

        ``capacity_clamped`` is :data:`CAPACITY_CLAMPED_MIN` when the request
        was below the duty available at ``rps_min`` (the machine delivers more
        than asked), :data:`CAPACITY_CLAMPED_MAX` when it was above the duty
        available at ``rps_max`` (the machine delivers less than asked), and
        ``None`` when the request was met exactly. ``converged`` is ``False``
        only when the request lies inside the deliverable band yet the root
        find still failed, which indicates a numerical problem.
    """
    res_min = residual(rps_min)
    if res_min > 0.0:
        return rps_min, True, CAPACITY_CLAMPED_MIN
    if res_min == 0.0:
        return rps_min, True, None

    res_max = residual(rps_max)
    if res_max < 0.0:
        return rps_max, True, CAPACITY_CLAMPED_MAX
    if res_max == 0.0:
        return rps_max, True, None

    try:
        return brentq(residual, rps_min, rps_max), True, None
    except ValueError:
        # The residual changes sign across the interval, so a root exists and
        # the solver should have found it. Report the failure honestly.
        rps = rps_min if abs(res_min) < abs(res_max) else rps_max
        return rps, False, None
