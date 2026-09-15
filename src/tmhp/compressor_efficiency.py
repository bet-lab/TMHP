r"""Default compressor efficiency correlations, and where each one comes from.

A variable-speed compressor loses work in three distinguishable places, and
TMHP keeps them separate because they act on different outputs:

``eta_cmp_isen``
    Isentropic efficiency -- sets the discharge enthalpy, hence the heating
    duty and the discharge temperature.
``eta_cmp_vol``
    Volumetric efficiency -- the fraction of the swept volume actually
    delivered, lost to re-expansion and internal leakage; sets the mass flow.
``eta_cmp``
    Electro-mechanical efficiency -- drive, motor and bearings; sets the
    electrical input for a given refrigerant-side work.

Where the numbers come from
---------------------------
The correlations are fitted to *standalone compressor* performance data and
then frozen (``validation/compressor_maps``): Copeland variable-speed scroll
AHRI-540 coefficient sets at two or three rated speeds per machine (Online
Product Information), the 48 calorimeter tests of Cuevas & Lebrun (2009) with
measured discharge temperature, the published efficiency functions of Guth &
Atakan (2023) for an R-290 scroll, and rated points of Highly R-290 rotaries.
Heat-pump catalogue COP is *not* used to fit them; it is used afterwards to
check the assembled model (``validation/parity``).  The archived version with
data list, fits and leave-one-compressor-out cross-validation is named by
:data:`COEFFICIENT_VERSION`.

Structure (what the data identify)
----------------------------------
``eta_vol = f(PR, n*)``, ``eta_isen = f(PR)``, ``eta_em = f(n*)``, with
``n* = rps / rps_rated`` the speed relative to the machine's rated speed.

* Power tables identify the *product* ``eta_isen * eta_em`` (the electrical-
  to-isentropic efficiency).  With no speed term in ``eta_isen`` and no lift
  term in ``eta_em`` the product is separable, ``g(PR) * s(n*)``, up to one
  scale factor.  That factor -- how much of the loss ends up in the
  refrigerant (``eta_isen``, via the discharge enthalpy) rather than leaving
  through the shell or the drive (``eta_em``) -- is fixed by the only rows
  with a measured discharge temperature under exactly TMHP's definition:
  Cuevas & Lebrun's inverter-fed tests near rated speed, :data:`ETA_EM_REF`.
* The speed penalty measured across the Copeland set grows with pressure
  ratio (the fitted interaction is significant); carrying it would need a
  speed term in ``eta_isen``.  It buys 0.15 pp of cross-validated error and
  is kept as a documented extension, not adopted -- the correlations only
  become more complex when the data demand it clearly.
* Speed is read relative to the machine's rated speed.  A drive and motor are
  sized for the speed the compressor is rated at; a curve measured on a
  machine rated near 70 rev/s must not put a 40 rev/s machine permanently on
  its falling side.

Low-load behaviour
------------------
Certified declared COP (Heat Pump Keymark, 18,106 rows) rises monotonically
toward the lightest EN 14825 test point; there is no low-load roll-over to
reproduce.  These correlations therefore carry the low-speed loss the
compressor data show -- a few points of volumetric and electro-mechanical
efficiency at a quarter of rated speed -- and nothing else.  Cycling and the
minimum-modulation limit are not modelled and are stated as limitations.

References
----------
Cuevas, C. & Lebrun, J. (2009). Testing and modelling of a variable speed
    scroll compressor. *Applied Thermal Engineering* 29(2-3), 469-478.
    doi:10.1016/j.applthermaleng.2008.03.016
Guth, T. & Atakan, B. (2023). Semi-empirical model of a variable speed scroll
    compressor for R-290. *International Journal of Refrigeration* 146,
    483-499. doi:10.1016/j.ijrefrig.2022.10.024
Ossorio, R. & Navarro-Peris, E. (2023). Testing of variable-speed scroll
    compressors and their inverters for the development of empirical
    correlations. *Applied Thermal Engineering* 230, 120725.
    doi:10.1016/j.applthermaleng.2023.120725
Copeland LP. Online Product Information, AHRI 540 performance coefficients
    of ZPV/XPV/YPV/ZHV/YHV variable-speed scroll compressors (accessed
    2026-09-15).
"""

from __future__ import annotations

from collections.abc import Callable

__all__ = [
    "COEFFICIENT_VERSION",
    "coefficients",
    "eta_isen_default",
    "eta_vol_default",
    "eta_em_default",
    "eta_oi_product",
    "make_eta_vol",
    "make_eta_em",
    "speed_factor_em",
    "RPS_REF",
    "ETA_VOL_A",
    "ETA_VOL_B",
    "ETA_VOL_FLOOR",
    "ETA_OI_A",
    "ETA_OI_B",
    "ETA_OI_C",
    "ETA_EM_N0",
    "ETA_EM_REF",
    "ETA_ISEN_FLOOR",
]

#: Rated speed assumed when a caller has none to give [rev/s].  The module-
#: level defaults :data:`eta_vol_default` and :data:`eta_em_default` are bound
#: to it; the heat-pump models bind their own rated speed instead
#: (:data:`tmhp.compressor_speed.RATED_POINT_AIR_TO_WATER`, 40 rev/s, and
#: :data:`tmhp.compressor_speed.RATED_POINT_AIR_TO_AIR`, 60 rev/s).
RPS_REF = 50.0

# --- BEGIN GENERATED coefficients v2026-09-15 ---
#: Coefficient version; the archive with data list, fits and cross-validation
#: lives in ``validation/coefficients/v2026-09-15/``.
COEFFICIENT_VERSION = "v2026-09-15"
#: Volumetric efficiency ``1 - A (PR - 1) - B max(0, 1/n* - 1)`` -- 73 machines,
#: 113 speed records, LOCO MAPE 5.41 % (legacy 5.71 %).
ETA_VOL_A = 0.02051
ETA_VOL_B = 0.01774
#: Electrical-to-isentropic product ``(A - B PR - C/PR) * n*(1+n0)/(n*+n0)`` --
#: LOCO MAPE 8.96 % (legacy 9.96 %).
ETA_OI_A = 1.17406
ETA_OI_B = 0.08346
ETA_OI_C = 0.68157
ETA_EM_N0 = 0.03304
#: Electro-mechanical efficiency at rated speed: the measured split of the
#: product (Cuevas & Lebrun 2009, inverter-fed, n* ~ 1; p10-p90 0.926-0.942).
ETA_EM_REF = 0.9360
# --- END GENERATED coefficients ---

#: Below these the correlations are extrapolating past the compressor data
#: (PR up to 8, n* down to 0.27) and are held rather than followed.
ETA_VOL_FLOOR = 0.50
ETA_ISEN_FLOOR = 0.30


def coefficients() -> dict[str, float | str]:
    """The frozen coefficient set, for provenance columns in validation output."""
    return {
        "version": COEFFICIENT_VERSION,
        "ETA_VOL_A": ETA_VOL_A,
        "ETA_VOL_B": ETA_VOL_B,
        "ETA_OI_A": ETA_OI_A,
        "ETA_OI_B": ETA_OI_B,
        "ETA_OI_C": ETA_OI_C,
        "ETA_EM_N0": ETA_EM_N0,
        "ETA_EM_REF": ETA_EM_REF,
        "ETA_VOL_FLOOR": ETA_VOL_FLOOR,
        "ETA_ISEN_FLOOR": ETA_ISEN_FLOOR,
    }


# ---------------------------------------------------------------------------
# Volumetric efficiency
# ---------------------------------------------------------------------------
def make_eta_vol(rps_rated: float) -> Callable[[float, float], float]:
    """Volumetric efficiency correlation for a machine rated at ``rps_rated``.

    Returns ``eta(pressure_ratio, rps)`` with

    ``eta_vol = 1 - ETA_VOL_A (PR - 1) - ETA_VOL_B max(0, 1/n* - 1)``.

    The first term is clearance re-expansion and pressure-driven leakage
    growing with lift; the second is the extra leakage fraction at low speed
    (leakage is set by the pressure difference and barely by speed, the swept
    flow is proportional to speed, so the *fraction* lost goes as ``1/n*``).
    It is one-sided: no bonus above rated speed, where the data thin out and
    an error would flatter the model.  The delivered duty ``rps * eta_vol``
    stays monotonic in speed, which the speed search relies on.
    """

    def eta_vol(pressure_ratio: float, rps: float) -> float:
        eta = 1.0 - ETA_VOL_A * (pressure_ratio - 1.0)
        if rps > 0.0:
            n_star = rps / rps_rated
            eta -= ETA_VOL_B * max(0.0, 1.0 / n_star - 1.0)
        return max(ETA_VOL_FLOOR, eta)

    return eta_vol


# ---------------------------------------------------------------------------
# Electrical-to-isentropic product, and its split
# ---------------------------------------------------------------------------
def speed_factor_em(n_star: float) -> float:
    """Speed factor of the electro-mechanical efficiency, 1 at rated speed.

    ``s(n*) = n* (1 + n0) / (n* + n0)``: the saturating shape of a drive whose
    fixed switching and magnetising losses weigh more as the delivered power
    falls -- the form Ossorio & Navarro-Peris fit to 185 inverter
    measurements, here with ``n0`` fitted on the whole compressor set.
    """
    n = max(n_star, 1e-6)
    return n * (1.0 + ETA_EM_N0) / (n + ETA_EM_N0)


def eta_oi_product(pressure_ratio: float, n_star: float) -> float:
    """Electrical-to-isentropic efficiency ``eta_isen * eta_em`` at ``(PR, n*)``.

    ``g(PR) = A - B PR - C/PR`` peaks near the built-in volume ratio of a
    scroll (``PR = sqrt(C/B)``, about 2.9 here) and falls on both sides:
    under-compression below it, over-compression and leakage above.
    """
    g = ETA_OI_A - ETA_OI_B * pressure_ratio - ETA_OI_C / max(pressure_ratio, 1.0)
    return max(ETA_ISEN_FLOOR * ETA_EM_REF, g) * speed_factor_em(n_star)


def eta_isen_default(pressure_ratio: float) -> float:
    """Isentropic efficiency at a given pressure ratio; no speed term.

    ``eta_isen = g(PR) / ETA_EM_REF`` -- the fitted product divided by the
    measured electro-mechanical level at rated speed, so that
    ``eta_isen(PR) * eta_em(rps_rated)`` reproduces the fitted product.
    Below :data:`ETA_ISEN_FLOOR` the correlation is extrapolating (the data
    end near ``PR = 8``) and is held.
    """
    g = ETA_OI_A - ETA_OI_B * pressure_ratio - ETA_OI_C / max(pressure_ratio, 1.0)
    return max(ETA_ISEN_FLOOR, g / ETA_EM_REF)


def make_eta_em(rps_rated: float) -> Callable[[float, float], float]:
    """Electro-mechanical efficiency correlation for a machine rated at ``rps_rated``.

    Returns ``eta(pressure_ratio, rps) = ETA_EM_REF * s(rps / rps_rated)``.
    The pressure ratio is accepted for signature compatibility with the other
    correlations and not used: the fitted product is separable, and the lift
    dependence lives in :func:`eta_isen_default`.
    """

    def eta_em(pressure_ratio: float, rps: float) -> float:
        del pressure_ratio
        return ETA_EM_REF * speed_factor_em(rps / rps_rated)

    return eta_em


#: Correlations for a machine rated at :data:`RPS_REF`, for callers that have
#: no rated speed to hand.
eta_vol_default = make_eta_vol(RPS_REF)
eta_em_default = make_eta_em(RPS_REF)
