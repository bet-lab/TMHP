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
Atakan (2023) for an R-290 scroll, rated points of Highly R-290 rotaries, and
the manufacturer performance maps of three inverter rotary compressors at
30-120 Hz published by Shao et al. (2004).
Heat-pump catalogue COP is *not* used to fit them; it is used afterwards to
check the assembled model (``validation/parity``).  The archived version with
data list, fits and leave-one-compressor-out cross-validation is named by
:data:`COEFFICIENT_VERSION`.

Structure (what the data identify)
----------------------------------
All three efficiencies depend on the pressure ratio ``PR`` and on the speed
relative to the machine's rated speed, ``n* = rps / rps_rated``::

    eta_vol  = 1 - A (PR-1) - B u - C (PR-1) u,           u = max(0, 1/n* - 1)
    eta_em   = ETA_EM_REF * s(n*) * m(PR)
    eta_isen = eta_oi / eta_em,   eta_oi = g(PR) [s(n*)] x(PR, n*) h(n*)

with

    g(PR)    = A_oi - B_oi PR - C_oi / PR        lift shape of the electrical-to-
                                                 isentropic product, peak near the
                                                 built-in volume ratio
    s(n*)    = n* (1 + n0) / (n* + n0)           drive + motor fixed losses
                                                 (saturating; Ossorio form)
    m(PR)    = (PR-1)/(PR-1+p0) * (2+p0)/2       motor load: torque, hence motor
                                                 efficiency, grows with lift
    x(PR,n*) = 1 - c (PR-1) u_L                  internal leakage: the leaked
                                                 fraction goes as the pressure
                                                 difference over the speed
    h(n*)    = 1 - d (n*^2 - 1)  (two-sided)     flow losses through the ports
             = 1 - d max(0, n*-1)^2 (one-sided)  grow with the square of speed

* Power tables identify eta_vol and the *product* ``eta_isen * eta_em``.  The
  product is fitted as ``g * s * x * h`` on 76 machines; the speed shape is
  identified from the change *within* each machine (fixed-effects estimator,
  rule R4 of ``validation/compressor_maps/cv.py``), because two thirds of the
  rows sit at rated speed and the between-machine level spread would
  otherwise swamp every speed term.
* The split of the product into ``eta_isen`` and ``eta_em`` is not identified
  by power; it is set from the only rows measured with a discharge
  temperature under TMHP's definition (Cuevas & Lebrun 2009, inverter-fed,
  n* 0.7-1.5, PR 1.5-5.6): ``s(n*)`` and ``m(PR)`` are fitted there and held
  fixed in the product fit, so whatever further speed dependence the 76
  machines demand -- leakage ``x`` and flow loss ``h`` -- lands in
  ``eta_isen`` by construction.  The drive-only efficiency measured by
  Ossorio & Navarro-Peris (2023) on three inverters bounds ``n0`` from below.
* A one-sided term (``max(0, ...)``) means the correlation does not award a
  bonus where the data are thin; which terms are one-sided is decided by the
  cross-validation and recorded in the coefficient archive.

Low-load behaviour
------------------
At fixed boundary temperatures the assembled heat-pump model gains from the
heat exchangers unloading as the load falls (lower lift) and loses from the
compressor's low-speed terms (drive fixed losses, internal leakage).  The
correlations carry only what the compressor data show; no COP shape is
prescribed.  Cycling and the minimum-modulation limit are not modelled and are
stated as limitations.

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
Shao, S., Shi, W., Li, X. & Chen, H. (2004). Performance representation of
    variable-speed compressor for inverter air conditioners based on
    experimental data. *International Journal of Refrigeration* 27(8),
    805-815. doi:10.1016/j.ijrefrig.2004.02.008
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
    "make_eta_isen",
    "make_eta_em",
    "speed_factor_em",
    "load_factor_em",
    "leakage_factor",
    "flow_factor",
    "RPS_REF",
    "ETA_VOL_A",
    "ETA_VOL_B",
    "ETA_VOL_C",
    "ETA_VOL_FLOOR",
    "ETA_OI_A",
    "ETA_OI_B",
    "ETA_OI_C",
    "ETA_LEAK_C",
    "ETA_LEAK_TWO_SIDED",
    "ETA_FLOW_D",
    "ETA_FLOW_TWO_SIDED",
    "ETA_OI_HAS_DRIVE",
    "ETA_EM_N0",
    "ETA_EM_P0",
    "ETA_EM_REF",
    "ETA_ISEN_FLOOR",
    "N_STAR_MAX",
]

#: Rated speed assumed when a caller has none to give [rev/s].  The module-
#: level defaults are bound to it; the heat-pump models bind their own rated
#: speed instead (:data:`tmhp.compressor_speed.RATED_POINT_AIR_TO_WATER`,
#: 40 rev/s, and :data:`tmhp.compressor_speed.RATED_POINT_AIR_TO_AIR`, 60 rev/s).
RPS_REF = 50.0

# --- BEGIN GENERATED coefficients v2026-09-24 ---
#: Coefficient version; the archive with data list, fits and cross-validation
#: lives in ``validation/coefficients/v2026-09-24/`` (fe estimator).
COEFFICIENT_VERSION = "v2026-09-24"
#: Volumetric efficiency ``1 - A (PR-1) - B u - C (PR-1) u``, ``u = max(0, 1/n* - 1)`` --
#: family V2, 76 machines, 131 speed records,
#: LOCO MAPE 5.95 % (legacy 5.88 %), speed transfer 2.88 %.
ETA_VOL_A = 0.02596
ETA_VOL_B = 0.02233
ETA_VOL_C = 0.00000
#: Lift shape of the electrical-to-isentropic product ``A - B PR - C/PR`` --
#: family I2xE0xL1, LOCO MAPE 11.72 % (legacy 12.21 %), speed transfer 6.86 %.
ETA_OI_A = 1.16595
ETA_OI_B = 0.08318
ETA_OI_C = 0.70174
#: Leakage interaction ``1 - c (PR-1) u_L``; two-sided means ``u_L = 1/n* - 1``.
ETA_LEAK_C = 0.02365
ETA_LEAK_TWO_SIDED = False
#: Flow-loss speed factor ``1 - d (n*^2 - 1)`` (two-sided) or ``1 - d max(0, n*-1)^2``.
ETA_FLOW_D = 0.00000
ETA_FLOW_TWO_SIDED = False
#: Whether the fitted product carries the drive factor (family I2xE0xL1).
ETA_OI_HAS_DRIVE = False
#: Drive + motor: ``s(n*) = n*(1+n0)/(n*+n0)`` with ``n0`` from the drive anchor
#: (total, Cuevas & Lebrun 2009: 0.0754; drive-only floor, Ossorio &
#: Navarro-Peris 2023: 0.0256); motor load ``m(PR)`` with ``p0``;
#: level at PR 3, n* 1 (Cuevas & Lebrun 2009 discharge-temperature split, 29 rows).
ETA_EM_N0 = 0.02556
ETA_EM_P0 = 0.02695
ETA_EM_REF = 0.9411
# --- END GENERATED coefficients ---

#: Below these the correlations are extrapolating past the compressor data
#: (PR up to 8, n* down to 0.27) and are held rather than followed.
ETA_VOL_FLOOR = 0.50
ETA_ISEN_FLOOR = 0.30
#: The speed terms rest on data up to twice rated speed; past that the factors
#: are held at their ``n* = N_STAR_MAX`` value rather than extrapolated.
N_STAR_MAX = 2.0
#: Motor-load factor is normalised at this pressure ratio.
PR_REF_EM = 3.0


def coefficients() -> dict[str, float | str | bool]:
    """The frozen coefficient set, for provenance columns in validation output."""
    return {
        "version": COEFFICIENT_VERSION,
        "ETA_VOL_A": ETA_VOL_A,
        "ETA_VOL_B": ETA_VOL_B,
        "ETA_VOL_C": ETA_VOL_C,
        "ETA_OI_A": ETA_OI_A,
        "ETA_OI_B": ETA_OI_B,
        "ETA_OI_C": ETA_OI_C,
        "ETA_LEAK_C": ETA_LEAK_C,
        "ETA_LEAK_TWO_SIDED": ETA_LEAK_TWO_SIDED,
        "ETA_FLOW_D": ETA_FLOW_D,
        "ETA_FLOW_TWO_SIDED": ETA_FLOW_TWO_SIDED,
        "ETA_OI_HAS_DRIVE": ETA_OI_HAS_DRIVE,
        "ETA_EM_N0": ETA_EM_N0,
        "ETA_EM_P0": ETA_EM_P0,
        "ETA_EM_REF": ETA_EM_REF,
        "ETA_VOL_FLOOR": ETA_VOL_FLOOR,
        "ETA_ISEN_FLOOR": ETA_ISEN_FLOOR,
        "N_STAR_MAX": N_STAR_MAX,
    }


def _n_star(rps: float | None, rps_rated: float) -> float:
    """Relative speed, clipped to the range the correlations are evaluated on; ``None`` means rated."""
    if rps is None or rps <= 0.0:
        return 1.0
    return min(max(rps / rps_rated, 1e-6), N_STAR_MAX)


# ---------------------------------------------------------------------------
# Volumetric efficiency
# ---------------------------------------------------------------------------
def make_eta_vol(rps_rated: float) -> Callable[[float, float], float]:
    """Volumetric efficiency correlation for a machine rated at ``rps_rated``.

    Returns ``eta(pressure_ratio, rps)`` with

    ``eta_vol = 1 - A (PR-1) - B u - C (PR-1) u``, ``u = max(0, 1/n* - 1)``.

    The first term is clearance re-expansion and pressure-driven leakage
    growing with lift; the speed terms are the extra leakage *fraction* at low
    speed -- leakage is set by the pressure difference and barely by speed,
    the swept flow is proportional to speed, so the fraction lost goes as
    ``1/n*`` (``B``) and, where the data ask for it, as ``(PR-1)/n*`` (``C``).
    One-sided: no bonus above rated speed.  The delivered duty
    ``rps * eta_vol`` stays monotonic in speed, which the speed search relies on.
    """

    def eta_vol(pressure_ratio: float, rps: float) -> float:
        eta = 1.0 - ETA_VOL_A * (pressure_ratio - 1.0)
        if rps is not None and rps > 0.0:
            u = max(0.0, rps_rated / rps - 1.0)
            eta -= (ETA_VOL_B + ETA_VOL_C * (pressure_ratio - 1.0)) * u
        return max(ETA_VOL_FLOOR, eta)

    return eta_vol


# ---------------------------------------------------------------------------
# Factors shared by the product and its split
# ---------------------------------------------------------------------------
def speed_factor_em(n_star: float) -> float:
    """Drive + motor speed factor ``s(n*) = n*(1+n0)/(n*+n0)``, 1 at rated speed.

    The saturating shape of a drive whose fixed switching, magnetising and
    friction losses weigh more as the delivered power falls -- the form
    Ossorio & Navarro-Peris (2023) fit to 185 inverter measurements.  ``n0``
    is fitted on the total electro-mechanical efficiency measured by Cuevas &
    Lebrun (2009) and is at least the drive-only value of the three Ossorio
    inverters.
    """
    n = min(max(n_star, 1e-6), N_STAR_MAX)
    return n * (1.0 + ETA_EM_N0) / (n + ETA_EM_N0)


def load_factor_em(pressure_ratio: float) -> float:
    """Motor-load factor ``m(PR) = (PR-1)/(PR-1+p0) * (PR_ref-1+p0)/(PR_ref-1)``, 1 at PR 3.

    Motor efficiency falls at light load; at a given speed the torque grows
    with lift, so the factor rises with pressure ratio and saturates.  Zero
    ``p0`` switches it off.
    """
    if ETA_EM_P0 <= 0.0:
        return 1.0
    x = max(pressure_ratio - 1.0, 0.05)
    return x / (x + ETA_EM_P0) * (PR_REF_EM - 1.0 + ETA_EM_P0) / (PR_REF_EM - 1.0)


def leakage_factor(pressure_ratio: float, n_star: float) -> float:
    """Leakage interaction ``x = 1 - c (PR-1) u_L`` on the isentropic efficiency.

    The gas leaking back across the flanks and tips at low speed has already
    been compressed once, so the work spent on it is lost; the leaked fraction
    grows with the pressure difference and with ``1/n*``.  One-sided
    (``u_L = max(0, 1/n*-1)``) unless the archive says two-sided.
    """
    n = min(max(n_star, 1e-6), N_STAR_MAX)
    u = 1.0 / n - 1.0
    if not ETA_LEAK_TWO_SIDED:
        u = max(0.0, u)
    return max(0.0, 1.0 - ETA_LEAK_C * (pressure_ratio - 1.0) * u)


def flow_factor(n_star: float) -> float:
    """Flow-loss speed factor ``h(n*)``: ``1 - d (n*^2-1)`` two-sided, ``1 - d max(0, n*-1)^2`` one-sided.

    Pressure losses through suction and discharge ports grow with the square
    of speed; below rated speed the compression is slightly better, above it
    worse.  Held at its ``n* = N_STAR_MAX`` value beyond the data.
    """
    n = min(max(n_star, 1e-6), N_STAR_MAX)
    if ETA_FLOW_TWO_SIDED:
        return max(0.0, 1.0 - ETA_FLOW_D * (n * n - 1.0))
    over = max(0.0, n - 1.0)
    return max(0.0, 1.0 - ETA_FLOW_D * over * over)


def _g(pressure_ratio: float) -> float:
    return ETA_OI_A - ETA_OI_B * pressure_ratio - ETA_OI_C / max(pressure_ratio, 1.0)


def eta_oi_product(pressure_ratio: float, n_star: float) -> float:
    """Electrical-to-isentropic efficiency ``eta_isen * eta_em`` at ``(PR, n*)``.

    ``g(PR) = A - B PR - C/PR`` peaks near the built-in volume ratio of a
    scroll (``PR = sqrt(C/B)``) and falls on both sides: under-compression
    below it, over-compression and leakage above.  Multiplied by the drive
    factor, the leakage interaction and the flow-loss factor.
    """
    g = max(ETA_ISEN_FLOOR * ETA_EM_REF, _g(pressure_ratio))
    s = speed_factor_em(n_star) if ETA_OI_HAS_DRIVE else 1.0
    return g * s * leakage_factor(pressure_ratio, n_star) * flow_factor(n_star)


# ---------------------------------------------------------------------------
# The split: isentropic and electro-mechanical efficiency
# ---------------------------------------------------------------------------
def make_eta_isen(rps_rated: float) -> Callable[[float, float | None], float]:
    """Isentropic efficiency correlation for a machine rated at ``rps_rated``.

    Returns ``eta(pressure_ratio, rps=None)`` with

    ``eta_isen = eta_oi(PR, n*) / eta_em(PR, n*)``

    -- the fitted product with the drive and motor factors divided out, so that
    ``eta_isen * eta_em`` reproduces the product at every ``(PR, n*)``.  When the
    fitted product carries no drive factor (:data:`ETA_OI_HAS_DRIVE` false) this
    gives the compression a small low-speed bonus, ``1 / s(n*)``, where the
    drive loses: the flow-loss reading of the within-machine data.  With
    ``rps`` omitted the rated speed is assumed.  Below :data:`ETA_ISEN_FLOOR`
    the correlation is extrapolating and is held.
    """

    def eta_isen(pressure_ratio: float, rps: float | None = None) -> float:
        n = _n_star(rps, rps_rated)
        g = _g(pressure_ratio) * leakage_factor(pressure_ratio, n) * flow_factor(n)
        if ETA_OI_HAS_DRIVE:
            g *= speed_factor_em(n)
        em = ETA_EM_REF * speed_factor_em(n) * load_factor_em(pressure_ratio)
        return max(ETA_ISEN_FLOOR, g / em)

    return eta_isen


def make_eta_em(rps_rated: float) -> Callable[[float, float], float]:
    """Electro-mechanical efficiency correlation for a machine rated at ``rps_rated``.

    Returns ``eta(pressure_ratio, rps) = ETA_EM_REF s(rps / rps_rated) m(PR)``.
    """

    def eta_em(pressure_ratio: float, rps: float) -> float:
        return ETA_EM_REF * speed_factor_em(_n_star(rps, rps_rated)) * load_factor_em(pressure_ratio)

    return eta_em


#: Correlations for a machine rated at :data:`RPS_REF`, for callers that have
#: no rated speed to hand.
eta_vol_default = make_eta_vol(RPS_REF)
eta_isen_default = make_eta_isen(RPS_REF)
eta_em_default = make_eta_em(RPS_REF)
