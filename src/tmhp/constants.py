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
    "k_a",
    "Pr_a",
    # Water properties
    "c_w",
    "rho_w",
    "mu_w",
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
rho_a = 1.225  # Density of air [kg/m³]
k_a = 0.0257  # Thermal conductivity of air [W/mK]
Pr_a = 0.71  # Prandtl number of air [-] (at typical ambient temperature)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Water Properties
# ═══════════════════════════════════════════════════════════════════════════════

c_w = 4186  # Specific heat capacity of water [J/kgK]
rho_w = 1000  # Density of water [kg/m³]
mu_w = 0.001  # Dynamic viscosity of water [Pa·s]
k_w = 0.606  # Thermal conductivity of water [W/mK]
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
