"""Heat pump Coefficient of Performance (COP) models.

Provides empirical COP correlations for:
- Air source heat pumps (cooling and heating modes)
- Ground source heat pumps (modified Carnot model)
"""

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


def calc_GSHP_COP(T_a_iu_in_K, T_f_out_K, dV_a_ratio, mode):
    """Calculate COP for a Ground Source Heat Pump.

    Uses the EnergyPlus ``Coil:*:WaterToAir:EquationFit`` model
    (Equation-Fit Method).

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
        Load-side air volumetric flow ratio: V_a / V_a_ref  [-]
        V_a_ref = Q_rated / (rho_a * c_a * 10K)  [m³/s]
    mode : str
        "cooling" or "heating"

    Units
    -----
    Temperatures : K
    Flow rates   : m³/s
    Heat/Power   : W

    Returns
    -------
    float
        COP (dimensionless). Polynomial model is unconditionally stable—
        no ValueError on temperature inversion.
    """
    from CoolProp.HumidAirProp import HAPropsSI

    # ------------------------------------------------------------------
    # TCH072_GLHP coefficients  (EnergyPlus WaterToAirHeatPumps.idf)
    # Curve:QuadLinear — [C1, C2(w), C3(x), C4(y), C5(z)]
    # w = T_wb(clg) / T_a_iu_in(htg) / T_ref
    # x = T_f_out (= T_w,in) / T_ref
    # y = V_a / V_a_ref
    # z = V_w / V_w_ref
    # T_ref = 283 K  (EnergyPlus Eng. Ref.)
    # ------------------------------------------------------------------
    _COEFFS = {
        "CLG_CAPFT": [-4.34283983721389, 7.95908762160623, -2.81781371834254, 0.107825995086305, -9.96686960975482e-3],
        "CLG_PWRFT": [-6.07162009581271, 0.751024641964602, 5.74078006772573, 0.248287540581677, 3.53588794377984e-3],
        "HTG_CAPFT": [-4.71139387014587, -1.80505473458221, 7.61053388144077, 0.260726814057021, -2.22820319308267e-2],
        "HTG_PWRFT": [-5.12615627602834, 4.59939579763495, 1.45764440263458, -4.61795048261089e-2, 4.3384414360512e-3],
    }
    _T_REF = 283  # [K] EnergyPlus fixed reference temperature
    _COP_REF_CLG = 14.35 / 3.412  # EER 14.35 → COP ≈ 4.207
    _COP_REF_HTG = 3.42  # Rated heating COP from IDF
    _V_W_RATIO = 1.0  # Ground-loop water flow ratio (constant-speed pump)

    def _quad_linear(coeffs, x2, x3, x4, x5=1.0):
        c = coeffs
        return c[0] + c[1] * x2 + c[2] * x3 + c[3] * x4 + c[4] * x5

    if mode == "cooling":
        # T_a_iu_in_K (dry-bulb) → convert to wet-bulb for cooling curve (RH=50%)
        T_wb_K = HAPropsSI("Twb", "T", T_a_iu_in_K, "RH", 0.5, "P", 101325)

        cap_ratio = _quad_linear(
            _COEFFS["CLG_CAPFT"],
            T_wb_K / _T_REF,  # w = T_wb / T_ref
            T_f_out_K / _T_REF,  # x = T_f_out (T_w,in) / T_ref
            dV_a_ratio,  # y = V_a / V_a_ref
            _V_W_RATIO,  # z = V_w / V_w_ref
        )
        pwr_ratio = _quad_linear(
            _COEFFS["CLG_PWRFT"],
            T_wb_K / _T_REF,
            T_f_out_K / _T_REF,
            dV_a_ratio,
            _V_W_RATIO,
        )
        COP = _COP_REF_CLG * cap_ratio / pwr_ratio

    elif mode == "heating":
        # T_a_iu_in_K (dry-bulb) used directly for heating curve
        cap_ratio = _quad_linear(
            _COEFFS["HTG_CAPFT"],
            T_a_iu_in_K / _T_REF,  # w = T_a_iu_in / T_ref
            T_f_out_K / _T_REF,  # x = T_f_out (T_w,in) / T_ref
            dV_a_ratio,
            _V_W_RATIO,
        )
        pwr_ratio = _quad_linear(
            _COEFFS["HTG_PWRFT"],
            T_a_iu_in_K / _T_REF,
            T_f_out_K / _T_REF,
            dV_a_ratio,
            _V_W_RATIO,
        )
        COP = _COP_REF_HTG * cap_ratio / pwr_ratio

    else:
        COP = 0.0

    return COP
