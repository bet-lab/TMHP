"""Heat pump Coefficient of Performance (COP) models.

Provides empirical COP correlations for:
- Air source heat pumps (cooling and heating modes)
- Ground source heat pumps (EnergyPlus water-to-air EquationFit curves)
"""

import math

from . import calc_util as cu


def calc_ASHP_cooling_COP(T_a_int_out, T_a_ext_in, Q_r_int, Q_r_max, COP_ref):
    """
    Calculate the Coefficient of Performance (COP) for an Air Source Heat Pump (ASHP) in cooling mode.

    Reference: https://publications.ibpsa.org/proceedings/bs/2023/papers/bs2023_1118.pdf

    Parameters
    ----------
    T_a_int_out : float
        Indoor air temperature [K]
    T_a_ext_in : float
        Outdoor air temperature [K]
    Q_r_int : float
        Indoor heat load [W]
    Q_r_max : float
        Maximum cooling capacity [W]
    COP_ref : float
        Reference COP at standard conditions

    Returns
    -------
    float
        COP value

    Note
    ----
    COP is calculated based on:
    - PLR: Part Load Ratio
    - EIR: Energy input to cooling output ratio
    """
    PLR = Q_r_int / Q_r_max
    if PLR < 0.2:
        PLR = 0.2
    if PLR > 1.0:
        PLR = 1.0
    EIR_by_T = 0.38 + 0.02 * cu.K2C(T_a_int_out) + 0.01 * cu.K2C(T_a_ext_in)
    EIR_by_PLR = 0.22 + 0.50 * PLR + 0.26 * PLR**2
    COP = PLR * COP_ref / (EIR_by_T * EIR_by_PLR)
    return COP


def calc_ASHP_heating_COP(T0, Q_r_int, Q_r_max):
    """
    Calculate the Coefficient of Performance (COP) for an Air Source Heat Pump (ASHP) in heating mode.

    Reference: https://www.mdpi.com/2071-1050/15/3/1880

    Parameters
    ----------
    T0 : float
        Environmental temperature [K]
    Q_r_int : float
        Indoor heat load [W]
    Q_r_max : float
        Maximum heating capacity [W]

    Returns
    -------
    float
        COP value

    Note
    ----
    COP is calculated based on PLR (Part Load Ratio).
    """
    PLR = Q_r_int / Q_r_max
    if PLR < 0.2:
        PLR = 0.2
    if PLR > 1.0:
        PLR = 1.0
    COP = -7.46 * (PLR - 0.0047 * cu.K2C(T0) - 0.477) ** 2 + 0.0941 * cu.K2C(T0) + 4.34
    return COP


# ---------------------------------------------------------------------------
# Water-to-air heat pump, EnergyPlus EquationFit (ClimateMaster TCH072_GLHP)
# ---------------------------------------------------------------------------
# Source: EnergyPlus ``datasets/WaterToAirHeatPumps.idf`` (NREL/EnergyPlus,
# last update July 2022), objects ``TCH072_GLHP_{CLG,HTG}_{CAPFT,PWRFT}``,
# "performance curve based on 15% methanol antifreeze solution". Rated:
#   cooling 70.29 kBtu/h, EER 14.35 at 77 °F EWT, 80.6/66.2 °F entering air;
#   heating 56.14 kBtu/h, COP 3.42 at 32 °F EWT, 68/59 °F entering air.
# Curve:QuadLinear  ratio = C1 + C2 w + C3 x + C4 y + C5 z with
#   w = T_wb / T_ref (cooling) or T_db / T_ref (heating) of the entering air,
#   x = T_w,in / T_ref (entering source water), T_ref = 283 K,
#   y = V_air / V_air,rated, z = V_w / V_w,rated.
# The curves give the *full-load* capacity and power at those conditions;
# EnergyPlus meets a smaller load by cycling the coil (run-time fraction), so
# y is the air-flow ratio while the compressor runs, not a part-load ratio.

GSHP_EQFIT_COEFFS = {
    "CLG_CAPFT": (-4.34283983721389, 7.95908762160623, -2.81781371834254, 0.107825995086305, -9.96686960975482e-3),
    "CLG_PWRFT": (-6.07162009581271, 0.751024641964602, 5.74078006772573, 0.248287540581677, 3.53588794377984e-3),
    "HTG_CAPFT": (-4.71139387014587, -1.80505473458221, 7.61053388144077, 0.260726814057021, -2.22820319308267e-2),
    "HTG_PWRFT": (-5.12615627602834, 4.59939579763495, 1.45764440263458, -4.61795048261089e-2, 4.3384414360512e-3),
}
GSHP_EQFIT_T_REF_K = 283.0
GSHP_EQFIT_COP_REF = {"cooling": 14.35 / 3.412, "heating": 3.42}

#: Fitted domain of each curve variable, copied from the IDF ``Minimum/Maximum
#: Value of w, x, y, z`` fields (dimensionless, ×283 K for temperatures).
#: In °C: cooling entering wet-bulb 9.85–23.73, cooling EWT 9.85–48.72;
#: heating entering dry-bulb 9.85–26.51, heating EWT −1.25–32.06.
GSHP_EQFIT_DOMAIN = {
    "cooling": {"w": (1.0, 1.04905), "x": (1.0, 1.13734), "y": (0.75, 1.25), "z": (0.5, 1.0)},
    "heating": {"w": (1.0, 1.05886), "x": (0.96076, 1.07848), "y": (0.75, 1.25), "z": (0.5, 1.0)},
}


class EquationFitRangeError(ValueError):
    """An EquationFit curve was asked to evaluate outside its fitted domain."""

    def __init__(self, variable: str, value: float, bounds: tuple[float, float], mode: str) -> None:
        self.variable = variable
        self.value = value
        self.bounds = bounds
        self.mode = mode
        lo, hi = bounds
        super().__init__(
            f"GSHP EquationFit ({mode}): {variable}={value:.5f} outside the fitted range [{lo}, {hi}]"
            + (f" (= {value * GSHP_EQFIT_T_REF_K - 273.15:.2f} °C)" if variable in ("w", "x") else "")
        )


def gshp_equation_fit_ratios(T_a_iu_in_K, T_f_out_K, dV_a_ratio, mode, dV_w_ratio=1.0):
    """Capacity and power ratios of the TCH072_GLHP EquationFit curves.

    Raises :class:`EquationFitRangeError` (a ``ValueError``) when any curve
    variable lies outside the fitted domain :data:`GSHP_EQFIT_DOMAIN`; the
    curves are never extrapolated. Inside the domain both ratios are positive
    (checked at the domain corners: capacity 0.51–1.93, power 0.67–1.50).

    Returns
    -------
    tuple[float, float]
        ``(capacity_ratio, power_ratio)`` relative to the rated point.
    """
    if mode not in GSHP_EQFIT_DOMAIN:
        raise ValueError(f"mode must be 'cooling' or 'heating', got {mode!r}")
    values = (T_a_iu_in_K, T_f_out_K, dV_a_ratio, dV_w_ratio)
    if not all(math.isfinite(float(v)) for v in values):
        raise ValueError("GSHP EquationFit inputs must be finite")
    if mode == "cooling":
        from CoolProp.HumidAirProp import HAPropsSI

        # Entering-air wet-bulb at 50 % relative humidity.
        w = HAPropsSI("Twb", "T", T_a_iu_in_K, "RH", 0.5, "P", 101325) / GSHP_EQFIT_T_REF_K
        prefix = "CLG"
    else:
        w = T_a_iu_in_K / GSHP_EQFIT_T_REF_K
        prefix = "HTG"
    x = T_f_out_K / GSHP_EQFIT_T_REF_K
    point = {"w": w, "x": x, "y": float(dV_a_ratio), "z": float(dV_w_ratio)}
    for name, value in point.items():
        lo, hi = GSHP_EQFIT_DOMAIN[mode][name]
        if not lo - 1e-12 <= value <= hi + 1e-12:
            raise EquationFitRangeError(name, value, (lo, hi), mode)

    def _quad_linear(c):
        return c[0] + c[1] * w + c[2] * x + c[3] * point["y"] + c[4] * point["z"]

    return _quad_linear(GSHP_EQFIT_COEFFS[f"{prefix}_CAPFT"]), _quad_linear(GSHP_EQFIT_COEFFS[f"{prefix}_PWRFT"])


def calc_GSHP_COP(T_a_iu_in_K, T_f_out_K, dV_a_ratio, mode):
    """Calculate COP for a Ground Source Heat Pump.

    Uses the EnergyPlus ``Coil:*:WaterToAir:EquationFit`` model
    (Equation-Fit Method), ``COP = COP_ref · CAP_ratio / PWR_ratio``.

    References
    ----------
    EnergyPlus Engineering Reference, Ch. 16.5.10.2 (Eq. 16.412–16.418,
    Tang 2005). Dataset: TCH072_GLHP (ClimateMaster 6-ton, Ground Loop
    HP), source: ``EnergyPlus/datasets/WaterToAirHeatPumps.idf``.

    Rated conditions (from IDF comment, 15% methanol antifreeze):

    - Cooling: 70.29 kBtu/h @ 77 °F (25 °C) entering water, EER = 14.35
    - Heating: 56.14 kBtu/h @ 32 °F (0 °C) entering water, COP = 3.42

    Parameters
    ----------
    T_a_iu_in_K : float
        Indoor air inlet dry-bulb temperature [K]  (= T_a_room).
        For cooling, internally converted to wet-bulb (T_wb) via CoolProp
        assuming RH = 50%.
    T_f_out_K : float
        Source-side inlet water temperature [K] — water returning from the
        borehole *to* the heat pump (``T_w,in`` in EnergyPlus notation).
    dV_a_ratio : float
        Air flow ratio while the compressor runs, ``V_a / V_a,rated`` [-]
        (fitted range 0.75–1.25). It is **not** a part-load ratio: the curve
        describes full-load operation and a part load is met by cycling.
    mode : str
        "cooling" or "heating"

    Returns
    -------
    float
        Full-load COP (dimensionless), always positive inside the fitted
        domain; 0.0 for any other ``mode``.

    Raises
    ------
    EquationFitRangeError
        (a ``ValueError``) when the entering-air temperature, the entering
        water temperature or the air-flow ratio is outside the fitted domain
        :data:`GSHP_EQFIT_DOMAIN`. The curves are not extrapolated.
    """
    if mode not in ("cooling", "heating"):
        return 0.0
    cap_ratio, pwr_ratio = gshp_equation_fit_ratios(T_a_iu_in_K, T_f_out_K, dV_a_ratio, mode)
    return GSHP_EQFIT_COP_REF[mode] * cap_ratio / pwr_ratio
