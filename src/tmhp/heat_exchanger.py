"""Heat-exchanger component models, separate from fans and correlations."""

import math
import warnings
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from scipy.optimize import root_scalar

from . import calc_util as cu
from .air_properties import air_density
from .constants import P0_PA, c_a, rho_a
from .reference_state import (
    Fluid,  # noqa: F401 -- named by the annotations AirCoilSide inherits; the docs build resolves them here
    HXSide,
)


def calc_ground_hx_UA_from_capacity(rated_capacity: float, specific_UA: float = 0.18) -> float:
    """Default ground refrigerant/water HX rated UA [W/K] from capacity [W].

    ``UA_rated = specific_UA * rated_capacity`` is a TMHP reduced-order sizing
    assumption, not a fitted refrigerant correlation. ``specific_UA`` has units
    1/K. The default 0.18 represents approximate water flow 3 L/min per kW,
    cooling HX duty 1.2 times rated load and a 4 K leaving-water approach.
    For a phase-change HX, UA = Cw*log(1 + Q_HX/(Cw*approach_out)); scaling
    flow and duty with capacity gives a proportional sizing rule. Its rounded
    default coefficient must be calibrated when device data are available.

    Reference for refrigerant-side BPHE heat-transfer behaviour only:
    Longo, G.A. (2009), R410A condensation inside a commercial brazed plate
    heat exchanger, Experimental Thermal and Fluid Science 33(2), 284-291.
    DOI: 10.1016/j.expthermflusci.2008.09.004.
    The paper reports mass-flux dependence in forced-convection condensation;
    it does NOT propose UA = 0.18*capacity or determine this coefficient.
    """
    if not all(math.isfinite(v) and v > 0 for v in (rated_capacity, specific_UA)):
        raise ValueError("Rated capacity and ground specific UA must be positive and finite")
    value = specific_UA * rated_capacity
    if not math.isfinite(value):
        raise ValueError("Calculated ground rated UA must be finite")
    return value


def calc_UA_two_stream_scaled(
    UA_rated: float,
    m_dot_fluid: float,
    m_dot_fluid_rated: float,
    m_dot_ref: float,
    m_dot_ref_rated: float,
    fluid_fraction: float = 0.5,
    refrigerant_fraction: float = 0.3,
    constant_fraction: float = 0.2,
    fluid_exponent: float = 0.8,
    refrigerant_exponent: float = 0.8,
) -> float:
    """Scale rated UA through a series resistance network [W/K].

    Fractions and exponents are modelling assumptions, not fitted universal
    refrigerant correlations. All mass flows are positive; handle off states
    separately. Rated flows reproduce UA_rated exactly.
    """
    positive = (UA_rated, m_dot_fluid, m_dot_fluid_rated, m_dot_ref, m_dot_ref_rated)
    fractions = (fluid_fraction, refrigerant_fraction, constant_fraction)
    exponents = (fluid_exponent, refrigerant_exponent)
    if not all(math.isfinite(v) and v > 0 for v in positive):
        raise ValueError("UA and mass flows must be finite and positive")
    if not all(math.isfinite(v) and v >= 0 for v in (*fractions, *exponents)):
        raise ValueError("Resistance fractions and exponents must be finite and nonnegative")
    if not math.isclose(sum(fractions), 1.0, rel_tol=0, abs_tol=1e-10):
        raise ValueError("Resistance fractions must sum to 1")
    resistance_ratio = (
        fluid_fraction * (m_dot_fluid / m_dot_fluid_rated) ** (-fluid_exponent)
        + refrigerant_fraction * (m_dot_ref / m_dot_ref_rated) ** (-refrigerant_exponent)
        + constant_fraction
    )
    return float(UA_rated / resistance_ratio)


def solve_secondary_flow_for_target_heat(
    Q_target: float,
    UA: float | Callable[[float], float],
    cp: float,
    T_fluid_in: float,
    T_ref_sat: float,
    m_dot_min: float,
    m_dot_max: float,
) -> dict:
    """Bracket a secondary-fluid mass flow [kg/s] for a positive duty [W].

    A callable UA receives the candidate mass flow. Feasibility requires a
    continuous capacity curve with a root inside the supplied flow interval.
    """
    if (
        not all(math.isfinite(v) for v in (Q_target, m_dot_min, m_dot_max))
        or Q_target < 0
        or not 0 < m_dot_min < m_dot_max
    ):
        raise ValueError("Invalid target heat or secondary-flow bounds")

    def residual(m_dot: float) -> float:
        conductance = UA(m_dot) if callable(UA) else UA
        return calc_phase_change_hx_capacity(conductance, m_dot, cp, T_fluid_in, T_ref_sat) - Q_target

    if Q_target == 0:
        return {"m_dot": 0.0, "Q_HX": 0.0, "converged": True}
    low, high = residual(m_dot_min), residual(m_dot_max)
    if low * high > 0:
        return {"m_dot": math.nan, "Q_HX": math.nan, "converged": False}
    root = root_scalar(residual, bracket=(m_dot_min, m_dot_max), method="brentq", xtol=1e-12)
    return {"m_dot": root.root, "Q_HX": residual(root.root) + Q_target, "converged": bool(root.converged)}


def calc_phase_change_hx_effectiveness(UA: float, m_dot: float, cp: float) -> float:
    """Effectiveness of a constant-temperature refrigerant / single-phase HX.

    UA [W/K], m_dot [kg/s], cp [J/(kg K)]. Zero UA or flow gives zero duty.
    """
    if not all(math.isfinite(v) for v in (UA, m_dot, cp)) or UA < 0 or m_dot < 0 or cp <= 0:
        raise ValueError("UA and m_dot must be nonnegative; cp must be positive (all finite)")
    return 1.0 - math.exp(-UA / (m_dot * cp)) if m_dot > 0 else 0.0


def calc_phase_change_hx_capacity(UA: float, m_dot: float, cp: float, T_fluid_in: float, T_ref_sat: float) -> float:
    """Available heat-transfer magnitude [W]; temperatures must use the same units."""
    if not all(math.isfinite(v) for v in (T_fluid_in, T_ref_sat)):
        raise ValueError("Temperatures must be finite")
    return calc_phase_change_hx_effectiveness(UA, m_dot, cp) * m_dot * cp * abs(T_fluid_in - T_ref_sat)


def resolve_fan_flow_limits(
    reference: float, minimum: float | None = None, maximum: float | None = None, *, custom_curve: bool = False
) -> tuple[float, float]:
    """Validate independent air-flow reference and solver bounds [m3/s].

    The generic ASHRAE correlation is limited to 15--100% of reference.
    Custom curves may supply independent limits outside that validity range.
    Reference is always a normalization point, not an implicit hardware limit.

    ``minimum == maximum`` describes a constant-speed (single-stage) fan: the
    airflow is fixed and the coil duty can only change through the approach
    temperature. ``minimum > maximum`` stays an input error.
    """
    minimum = 0.15 * reference if minimum is None else minimum
    maximum = reference if maximum is None else maximum
    if (
        not all(math.isfinite(v) for v in (reference, minimum, maximum))
        or reference <= 0
        or not 0 <= minimum <= maximum
        or maximum <= 0
    ):
        raise ValueError("Fan reference must be positive and finite; require finite 0 <= min <= max and max > 0")
    if not custom_curve:
        minimum = max(minimum, 0.15 * reference)
        maximum = min(maximum, reference)
        if minimum > maximum:
            raise ValueError("Generic ASHRAE fan limits must overlap 0.15--1.0 of reference")
    return minimum, maximum


def resolve_air_density(rho_air: float | str | None, T_a_in_C: float, P: float = P0_PA) -> float:
    """Density of the air an air coil's fan moves [kg/m³].

    ``None`` is standard air (``constants.rho_a``, dry air at 15 °C and one
    atmosphere), ``"inlet"`` dry air at the coil inlet temperature ``T_a_in_C``
    [°C] and pressure ``P`` [Pa], and a number is taken as given.
    """
    if rho_air is None:
        return float(rho_a)
    if isinstance(rho_air, str):
        if rho_air != "inlet":
            raise ValueError("rho_air must be None, 'inlet' or a positive density [kg/m3]")
        return float(air_density(T_a_in_C, P))
    if not (math.isfinite(rho_air) and rho_air > 0):
        raise ValueError("rho_air must be None, 'inlet' or a positive density [kg/m3]")
    return float(rho_air)


@dataclass(frozen=True)
class AirCoilSide(HXSide):
    """Air side of a coil at a rating point, with the density of its inlet air.

    :class:`~tmhp.reference_state.HXSide` rates an air stream as standard air
    (``constants.rho_a``). The air-source models' own coils take the density
    of the air they draw in, so their rating sides carry that density
    (``air_density(T_in_C)`` by default) and the reference state is solved
    with the heat capacity rate the cycle uses at the same point.
    ``volume_flow`` stays the fan's volume flow at the inlet state.
    """

    density: float | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.fluid != "air":
            raise ValueError("AirCoilSide is an air stream")
        if self.density is None:
            object.__setattr__(self, "density", float(air_density(self.T_in_C)))
        elif not (math.isfinite(self.density) and self.density > 0):
            raise ValueError("Rating air density must be positive and finite")

    @property
    def conductance(self) -> float:
        """Duty per kelvin of approach, ``eps C`` with ``C = c_a * density * volume_flow`` [W/K]."""
        assert self.volume_flow is not None and self.density is not None
        C = c_a * self.density * self.volume_flow
        return C * (1.0 - math.exp(-self.UA / C))


def calc_UA_from_dV_fan(
    dV_fan: float,
    dV_fan_rated: float | None = None,
    A_cross: float | None = None,
    UA: float | None = None,
    exponent: float = 0.71,
    *,
    dV_fan_ref: float | None = None,
) -> float:
    """Calculate velocity-dependent UA via lumped scaling (Wang et al., 2000).

    Parameters
    ----------
    dV_fan : float
        Current fan flow rate [m³/s].
    dV_fan_rated : float
        Rated fan flow rate [m³/s].
    A_cross : float
        Heat exchanger cross-sectional area [m²].
    UA : float
        Rated UA value [W/K].
    exponent : float
        Exponent for velocity scaling. Default is 0.71 for a 1-row configuration.

    Returns
    -------
    float
        Scaled UA value [W/K].

    Notes
    -----
    Instead of the Dittus-Boelter tube-side exponent (0.8), this uses
    a simplified lumped exponent (default 0.71). This derivation assumes a 1-row
    plain fin-and-tube configuration (N=1) where the Colburn j-factor
    is proportional to Re^-0.29, leading to h ∝ V^0.71. Multi-row coils may
    use exponents between 0.5 and 0.8 depending on configuration.
    Reference: Wang et al. (2000), DOI: 10.1016/S0017-9310(99)00333-6
    """
    reference = dV_fan_ref if dV_fan_ref is not None else dV_fan_rated
    if reference is None or UA is None or A_cross is None:
        raise ValueError("Fan reference, UA and cross-sectional area are required")
    if (
        not all(math.isfinite(v) for v in (reference, dV_fan, A_cross, UA, exponent))
        or reference <= 0
        or A_cross <= 0
        or dV_fan < 0
        or UA <= 0
        or exponent < 0
    ):
        raise ValueError("Invalid fan/UA scaling input")
    return float(UA * (dV_fan / reference) ** exponent)


#: Tolerances and iteration limit of ``scipy.optimize.bisect`` (its defaults).
_BISECT_XTOL = 2e-12
_BISECT_RTOL = 4 * np.finfo(float).eps
_BISECT_MAXITER = 100


def _bisect_fan_flow(
    dV_low: float,
    dV_high: float,
    err_low: float,
    Q_ref_target: float,
    UA_rated: float,
    dV_fan_ref: float,
    exponent: float,
    C_air_per_flow: float,
    dT: float,
) -> tuple[float, bool]:
    """Airflow at which an air coil carries ``Q_ref_target``: ``(dV, converged)``.

    The bisection of ``scipy.optimize.bisect`` (same steps, same stopping
    rule, so the same root to the last bit) on the duty margin of
    :func:`calc_HX_perf_for_target_heat`, with the margin written out instead
    of called back from SciPy: about forty evaluations per coil, two coils per
    cycle evaluation, cost as much as all the property flashes of that
    evaluation. The bracket is validated by the caller: ``err_low < 0`` at
    ``dV_low`` and a positive margin at ``dV_high``.
    """
    exp = np.exp  # the margin's own exponential: `math.exp` may differ in the last bit
    x_low = dV_low
    step = dV_high - dV_low
    for _ in range(_BISECT_MAXITER):
        step *= 0.5
        x_mid = x_low + step
        if x_mid <= 0:
            err_mid = -Q_ref_target
        else:
            C_air = C_air_per_flow * x_mid
            err_mid = (
                C_air * (1 - float(exp(-(UA_rated * (x_mid / dV_fan_ref) ** exponent) / C_air))) * dT - Q_ref_target
            )
        if err_mid != err_mid:
            # SciPy stops on a margin that is not a number; ``root_scalar``
            # reports the point as an unconverged root.
            return x_mid, False
        if err_mid * err_low >= 0:
            x_low = x_mid
        if err_mid == 0 or abs(step) < _BISECT_XTOL + _BISECT_RTOL * abs(x_mid):
            return x_mid, True
    return x_low, False


def calc_HX_perf_for_target_heat(
    Q_ref_target,
    T_a_in_C=None,
    T_ref_sat_K=None,
    A_cross=None,
    UA_rated=None,
    dV_fan_rated=None,
    is_active=True,
    exponent=0.71,
    # Legacy parameters for backward compatibility
    T_ou_a_in_C=None,
    T_ref_evap_sat_K=None,
    T_ref_cond_sat_l_K=None,
    UA_design=None,
    dV_fan_design=None,
    *,
    dV_fan_ref=None,
    dV_fan_min=None,
    dV_fan_max=None,
    custom_fan_curve=False,
    rho_air=None,
):
    """Numerically solve for the air-side flow rate of an ε-NTU heat exchanger.

    Given a target heat transfer duty, find the airflow that delivers it,
    accounting for the velocity dependence of UA via the Wang et al. (2000)
    fin-and-tube correlation (UA ∝ velocity^0.71).

    The air heat capacity rate is ``c_a * rho_air * dV_fan``. ``rho_air``
    [kg/m³] is the density of the air the fan moves; ``"inlet"`` takes dry air
    at the inlet temperature ``T_a_in_C`` and one standard atmosphere
    (:func:`tmhp.air_properties.air_density`), and ``None`` (default) keeps
    standard air, ``constants.rho_a`` (15 °C). UA follows the volume flow in
    either case.

    Parameters
    ----------
    Q_ref_target : float
        Target heat transfer rate [W] (always positive).
    T_a_in_C : float, optional
        Air-side inlet temperature [°C].
    T_ref_sat_K : float, optional
        Refrigerant saturation temperature [K] on the constant-temperature side.
    A_cross : float
        Heat-exchanger cross-sectional area [m²].
    UA_rated : float
        Rated UA [W/K].
    dV_fan_rated : float
        Rated fan volumetric flow rate [m³/s].

    is_active : bool
        Active flag.
    exponent : float
        UA scaling exponent (default: 0.71).

    # Legacy aliases (optional)
    T_ou_a_in_C : float, optional
        Backward-compat alias for ``T_a_in_C``.
    T_ref_evap_sat_K : float, optional
        Backward-compat alias for ``T_ref_sat_K``.
    T_ref_cond_sat_l_K : float, optional
        Unused; kept only to preserve the older function signature.

    Returns
    -------
    dict
        Dictionary with the following keys:

        - ``dV_fan`` — required air-side flow rate [m³/s]
        - ``UA`` — overall heat-transfer coefficient at the solution point [W/K]
        - ``T_a_mid_C`` — air temperature between the heat exchanger and the fan [°C]
        - ``Q_air`` — heat-transfer rate at the operating point [W]
        - ``epsilon`` — effectiveness at the operating point [–]
        - ``C_air`` — air heat capacity rate at the operating point [W/K]
        - ``converged`` — whether the solver converged

        All numeric values are ``np.nan`` when ``is_active=False``.
    """
    # Backward-compat: accept legacy parameter names.
    if T_a_in_C is None:
        T_a_in_C = T_ou_a_in_C
    if T_ref_sat_K is None:
        T_ref_sat_K = T_ref_evap_sat_K
    if UA_rated is None:
        UA_rated = UA_design
    if dV_fan_rated is None:
        dV_fan_rated = dV_fan_design
    if dV_fan_design is not None:
        warnings.warn(
            "dV_fan_design is deprecated; use dV_fan_ref with independent min/max.", DeprecationWarning, stacklevel=2
        )
    if dV_fan_ref is None:
        dV_fan_ref = dV_fan_rated

    if not is_active or T_a_in_C is None or T_ref_sat_K is None:
        return {
            "converged": True,
            "dV_fan": np.nan,
            "UA": np.nan,
            "T_a_mid_C": np.nan,
            "Q_air": np.nan,
            "epsilon": np.nan,
            "C_air": np.nan,
            # Legacy keys
            "T_ou_a_mid": np.nan,
            "Q_ou_air": np.nan,
        }

    T_a_in_K = cu.C2K(T_a_in_C)
    C_air_per_flow = c_a * resolve_air_density(rho_air, T_a_in_C)
    if dV_fan_ref is None:
        raise ValueError("Fan reference flow is required")
    dV_min, dV_max = resolve_fan_flow_limits(dV_fan_ref, dV_fan_min, dV_fan_max, custom_curve=custom_fan_curve)

    if abs(Q_ref_target) < 1e-6:
        return {
            "converged": True,
            "dV_fan": 0.0,
            "UA": 0.0,
            "T_a_mid_C": T_a_in_C,
            "Q_air": 0.0,
            "epsilon": 0.0,
            "C_air": 0.0,
            # Legacy keys
            "T_ou_a_mid": T_a_in_C,
            "Q_ou_air": 0.0,
        }

    def _error_function(dV_fan):
        if dV_fan <= 0:
            return -Q_ref_target
        UA = calc_UA_from_dV_fan(dV_fan, dV_fan_ref, A_cross, UA_rated, exponent)
        C_air = C_air_per_flow * dV_fan
        epsilon = 1 - np.exp(-UA / C_air)
        # Heat transfer Q = C_air * epsilon * abs(T_air_in - T_ref_sat)
        Q_air = C_air * epsilon * abs(T_a_in_K - T_ref_sat_K)
        return Q_air - Q_ref_target

    # Clamp the flow demand, but do not mark an unmatched HX duty feasible.
    # With dV_min == dV_max (constant-speed fan) err_min == err_max, so one of the
    # two clamp branches below always applies and the bisection bracket
    # [dV_min, dV_max] is never reached.
    err_min, err_max = _error_function(dV_min), _error_function(dV_max)
    min_limit, max_limit = bool(err_min >= 0), bool(err_max <= 0)
    # A custom curve normally requires an exact clamp match (a mismatch is a limit hit,
    # and the flow bisection below finds the exact root when one exists). A
    # constant-speed fan has no flow to bisect: the cycle solver closes the duty
    # through the approach temperature, so the coil is converged within the same
    # tolerance as the generic curve.
    tolerance = 0.0 if custom_fan_curve and dV_min != dV_max else max(0.01, 1e-5 * abs(Q_ref_target))
    if min_limit:
        dV_sol, converged = dV_min, bool(abs(err_min) <= tolerance)
    elif max_limit:
        dV_sol, converged = dV_max, bool(abs(err_max) <= tolerance)
    elif err_min < 0 < err_max:
        dV_sol, converged = _bisect_fan_flow(
            float(dV_min),
            float(dV_max),
            err_min,
            Q_ref_target,
            UA_rated,
            dV_fan_ref,
            exponent,
            C_air_per_flow,
            abs(T_a_in_K - T_ref_sat_K),
        )
    else:
        # A margin that is not a number: left to SciPy, which reports the
        # coil as not converged.
        sol = root_scalar(_error_function, bracket=[dV_min, dV_max], method="bisect")
        dV_sol, converged = sol.root, sol.converged

    # Final calculations at solved point
    UA_sol = calc_UA_from_dV_fan(dV_sol, dV_fan_ref, A_cross, UA_rated, exponent)
    C_air_sol = C_air_per_flow * dV_sol
    eps_sol = 1 - np.exp(-UA_sol / C_air_sol) if dV_sol > 0 else 0.0

    # Exit air temperature (before fan heat if any)
    T_a_mid_K = T_a_in_K - (T_a_in_K - T_ref_sat_K) * eps_sol
    T_a_mid_C = cu.K2C(T_a_mid_K)
    Q_air_sol = C_air_sol * abs(T_a_in_K - T_a_mid_K)

    if dV_min == dV_max:
        # A constant-speed fan sits at both limits; the direction of the duty
        # mismatch is in capacity_margin_W / min_flow_capacity_margin_W.
        min_limit = max_limit = True

    return {
        "converged": converged,
        "dV_fan": dV_sol,
        "UA": UA_sol,
        "T_a_mid_C": T_a_mid_C,
        "Q_air": Q_air_sol,
        "epsilon": eps_sol,
        "C_air": C_air_sol,
        "min_limit": min_limit,
        "max_limit": max_limit,
        "fan_flow_ratio_to_ref": dV_sol / dV_fan_ref,
        "capacity_margin_W": Q_air_sol - Q_ref_target,
        "min_flow_capacity_margin_W": err_min,
        "max_flow_capacity_margin_W": err_max,
        # Legacy keys
        "T_ou_a_mid": T_a_mid_C,
        "Q_ou_air": Q_air_sol,
    }
