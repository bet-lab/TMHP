"""
Physical constants for energy, entropy, and exergy analysis.

Categories:
    1. Air Properties
    2. Water Properties
    3. Physical Constants
    4. Thermodynamic Constants
    5. Solar Entropy Coefficients
    6. Natural Gas Properties
    7. Mathematical Constants
"""

import numpy as np

__all__ = [
    # Air properties
    "c_a",
    "rho_a",
    "R_a",
    "k_a",
    "Pr_a",
    # Water properties
    "c_w",
    "rho_w",
    "mu_w",
    "water_viscosity",
    "k_w",
    "Pr_w",
    "beta",
    # Physical / thermodynamic
    "g",
    "sigma",
    "T0_K",
    "P0_PA",
    # Solar entropy coefficients
    "k_D",
    "k_d",
    # Natural gas
    "ex_eff_NG",
    # Mathematical
    "SP",
]

# ═══════════════════════════════════════════════════════════════════════════════
# 1. Air Properties
# ═══════════════════════════════════════════════════════════════════════════════

c_a = 1005  # Specific heat capacity of air [J/kgK]
# Density of standard air [kg/m³]: dry air at 15 °C and 101 325 Pa. The air
# coils of the cycle models take the density at their own inlet temperature
# (``tmhp.air_properties.air_density``), which equals this value at 15 °C.
rho_a = 1.225
R_a = 287.055  # Specific gas constant of dry air [J/kgK]
k_a = 0.0257  # Thermal conductivity of air [W/mK]
Pr_a = 0.71  # Prandtl number of air [-] (at typical ambient temperature)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Water Properties
# ═══════════════════════════════════════════════════════════════════════════════

c_w = 4186  # Specific heat capacity of water [J/kgK]
rho_w = 1000  # Density of water [kg/m³]
# Dynamic viscosity of water [Pa·s], at about 20 °C (1.002 mPa s there). The
# source-side Reynolds numbers of the liquid-source models take the viscosity
# at the loop's design temperature instead (``water_viscosity``,
# ``tmhp.source_fluid.SourceFluid.viscosity``): 1.79 mPa s at 0 °C.
mu_w = 0.001
k_w = 0.606  # Thermal conductivity of water [W/mK]


def water_viscosity(T_C):
    """Dynamic viscosity of liquid water [Pa·s] at ``T_C`` [°C] and low pressure.

    Kestin, Sokolov and Wakeham (1978), *J. Phys. Chem. Ref. Data* 7(3), the
    two branches around ``mu(20 °C) = 1.002 mPa s``:

    - ``T_C <= 20``: ``log10(mu / mu20) = d / (T_C + 96) * (1.2378 - 1.303e-3 d
      + 3.06e-6 d**2 + 2.55e-8 d**3)``, ``d = 20 - T_C``;
    - ``T_C > 20``: ``log10(mu / mu20) = d / (T_C + 105) * (-1.3272 - 1.053e-3 d)``,
      ``d = T_C - 20``.

    Within 0.2 % of IAPWS 2008 (CoolProp ``Water``) from 0 to 100 °C; below
    0 °C it continues smoothly into supercooled water (2.15 mPa s at -5 °C).
    ``T_C`` may be a scalar or an array. The constant :data:`mu_w` is this
    function near 20 °C.

    Examples
    --------
    >>> round(water_viscosity(20.0) * 1e3, 3), round(water_viscosity(0.0) * 1e3, 3)
    (1.002, 1.793)
    """
    T = np.asarray(T_C, dtype=float)
    cold = 20.0 - T
    warm = T - 20.0
    log_ratio = np.where(
        T <= 20.0,
        cold / (T + 96.0) * (1.2378 - 1.303e-3 * cold + 3.06e-6 * cold**2 + 2.55e-8 * cold**3),
        warm / (T + 105.0) * (-1.3272 - 1.053e-3 * warm),
    )
    mu = 1.002e-3 * np.power(10.0, log_ratio)
    return float(mu) if mu.ndim == 0 else mu


beta = 2.07e-4  # Volumetric expansion coefficient of water [1/K] (at ~20 °C)
Pr_w = 6.9  # Prandtl number of water [-] (at ~20 °C)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Physical Constants
# ═══════════════════════════════════════════════════════════════════════════════

g = 9.81  # Gravitational acceleration [m/s²]


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Thermodynamic Constants
# ═══════════════════════════════════════════════════════════════════════════════

sigma = 5.67e-8  # Stefan-Boltzmann constant [W/m²K⁴]
T0_K = 293.15  # Default dead state temperature [K] (20°C)
P0_PA = 101325.0  # Default dead state pressure [Pa] (1 atm)


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Solar Entropy Coefficients
#    Reference: Petela, R. (2003) – Exergy of undiluted thermal radiation
# ═══════════════════════════════════════════════════════════════════════════════

k_D = 0.000462  # Direct solar entropy coefficient [-]
k_d = 0.0014  # Diffuse solar entropy coefficient [-]


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Natural Gas Properties
#    Reference: Shukuya, M. (2013) – Exergy: Theory and Applications in the
#               Built Environment, Springer.
# ═══════════════════════════════════════════════════════════════════════════════

ex_eff_NG = 0.93  # Chemical-exergy-to-HHV ratio of LNG [-]


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Mathematical Constants
# ═══════════════════════════════════════════════════════════════════════════════

SP = np.sqrt(np.pi)  # Square root of pi [-]
