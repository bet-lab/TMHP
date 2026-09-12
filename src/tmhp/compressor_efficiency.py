r"""Default compressor efficiency correlations, and where each one comes from.

A variable-speed compressor loses work in three distinguishable places, and
TMHP keeps them separate because the evidence for how each behaves is
different:

``eta_cmp_isen``
    Isentropic efficiency -- the penalty for compressing irreversibly.
``eta_cmp_vol``
    Volumetric efficiency -- the fraction of the swept volume actually
    delivered, lost to re-expansion and internal leakage.
``eta_cmp``
    Electro-mechanical efficiency -- motor, bearings and inverter.

What the part-load evidence actually says
-----------------------------------------
It is tempting to give these correlations a low-speed cliff so that modelled
COP peaks at part load and falls away below it, matching the shape people
expect of a seasonal performance curve. That shape is not in the data. Across
17,480 Heat Pump Keymark declared rows the certified COP rises monotonically
from test point A to point D, and 99 % of air-to-water machines declare a
higher COP at the lightest load than at the next one up. Part-load COP rises
because the heat exchangers become large relative to the duty and the lift
falls; the penalty real equipment pays at low load comes from the minimum
modulation limit and from on/off cycling, and TMHP models neither. Cuevas &
Lebrun make the same point from the hardware side: they observe the low-speed
degradation, attribute it to oil starvation, and note that the manufacturer's
own answer is to switch to on/off operation below 35 Hz rather than keep
modulating.

So these correlations carry the low-speed loss that is measured, at the size it
is measured, and nothing else. TMHP's low-load COP is a continuous-operation
figure and is optimistic against a machine that cycles -- stated plainly rather
than compensated for with a fitted penalty.

Where the low-speed loss belongs
--------------------------------
Not all on one coefficient. Cuevas & Lebrun (2009) ran the controlled contrast:
at 35 Hz both the isentropic and the volumetric effectiveness degrade, while at
75 Hz only the isentropic one does. They read the first as internal leakage and
the second as mechanical loss. A single speed-dependent factor cannot express
that, which is why the volumetric and electro-mechanical correlations below
each carry their own speed term and the isentropic one carries none.

References
----------
Cuevas, C. & Lebrun, J. (2009). Testing and modelling of a variable speed
    scroll compressor. *Applied Thermal Engineering* 29(2-3), 469-478.
    doi:10.1016/j.applthermaleng.2008.03.016
Guth, T. & Atakan, B. (2023). Semi-empirical model of a variable speed scroll
    compressor for R-290. *International Journal of Refrigeration* 146,
    483-499. doi:10.1016/j.ijrefrig.2022.10.024
Ossorio, R. & Navarro-Peris, E. (2023). The role of the inverter in the
    performance of variable speed compressors. *Applied Thermal Engineering*
    233, 120725. doi:10.1016/j.applthermaleng.2023.120725
"""

from __future__ import annotations

__all__ = [
    "eta_isen_default",
    "eta_vol_default",
    "eta_em_default",
    "guth_eta_comp",
    "speed_shape_em",
    "RPS_REF",
    "RPS_FIT_MIN",
    "RPS_FIT_MAX",
]

#: Speed at which the speed-dependent factors are normalised to 1 [rev/s].
#: The middle of the measured range, not a favourable point.
RPS_REF = 50.0
#: Lower and upper speeds over which the published measurements extend
#: [rev/s]. Outside this the speed factors are held at their boundary value:
#: a polynomial fitted over 15-110 Hz says nothing about 5 Hz, and pretending
#: otherwise would put a fabricated cliff into exactly the region this module
#: is careful not to invent one in.
RPS_FIT_MIN = 15.0
RPS_FIT_MAX = 110.0


# ---------------------------------------------------------------------------
# Isentropic efficiency
# ---------------------------------------------------------------------------
#: ``eta_isen = A - B * PR``. The linear dependence on pressure ratio is the
#: standard semi-empirical form; these coefficients are the ones the TMHP
#: validation manuscript uses across its whole reference set.
ETA_ISEN_A = 0.90
ETA_ISEN_B = 0.02
#: Below this the linear form stops being meaningful.
ETA_ISEN_FLOOR = 0.25


def eta_isen_default(pressure_ratio: float) -> float:
    """Isentropic efficiency at a given pressure ratio.

    No speed term. Guth & Atakan do report one, but it runs the opposite way to
    intuition -- their isentropic efficiency is *higher* at low speed -- while
    Cuevas & Lebrun see a mild degradation at both ends of the range. The two
    disagree in sign, so neither is carried; the speed dependence that both
    sources agree on lives in the other two correlations.
    """
    return max(ETA_ISEN_FLOOR, ETA_ISEN_A - ETA_ISEN_B * pressure_ratio)


# ---------------------------------------------------------------------------
# Volumetric efficiency
# ---------------------------------------------------------------------------
#: Clearance re-expansion coefficient in ``1 - C * (PR - 1)``.
ETA_VOL_CLEARANCE = 0.020

#: Internal-leakage coefficient [rev/s] in ``- k * (1/rps - 1/RPS_REF)``.
#:
#: Leakage past the scrolls is driven by the pressure difference and is close
#: to independent of how fast the machine turns, while the swept mass flow is
#: proportional to speed -- so the *fraction* lost goes as one over the speed.
#: The magnitude is set from Cuevas & Lebrun, who measure roughly three
#: percentage points of volumetric effectiveness lost between 50 and 35 Hz:
#: ``k * (1/35 - 1/50) = 0.03``.
#:
#: Two honest caveats. It is one machine, and its authors read the effect as
#: oil starvation rather than as an intrinsic property of scroll compressors.
#: And below 35 rev/s the term is extrapolation: it predicts about 16 points of
#: loss at 15 rev/s, which is the right sign and a plausible size against
#: Ossorio's 15 Hz measurements, but it is not measured here.
ETA_VOL_LEAKAGE = 3.50
#: Lower bound -- a compressor delivering less than this is not modelled.
ETA_VOL_FLOOR = 0.20


def eta_vol_default(pressure_ratio: float, rps: float | None = None) -> float:
    """Volumetric efficiency at a pressure ratio and, optionally, a speed.

    Called with ``rps`` omitted the speed term vanishes and the result is the
    pressure-ratio-only form used by the published validation set.

    The speed term is deliberately one-sided: it penalises speeds below
    :data:`RPS_REF` and gives no bonus above it. A bonus would be an
    extrapolation in the direction where the evidence is weakest and where an
    error flatters the model.
    """
    eta = 1.0 - ETA_VOL_CLEARANCE * (pressure_ratio - 1.0)
    if rps is not None and rps > 0.0:
        eta -= ETA_VOL_LEAKAGE * max(0.0, 1.0 / rps - 1.0 / RPS_REF)
    return max(ETA_VOL_FLOOR, eta)


# ---------------------------------------------------------------------------
# Electro-mechanical efficiency
# ---------------------------------------------------------------------------
#: Guth & Atakan (2023) Table A.3, scroll / R-290: coefficient matrices of
#: their Eq. (22) for the combined mechanical, electrical and frequency-
#: converter efficiency. Evaporating temperature in degrees Celsius, speed in
#: revolutions per minute.
_GUTH_A: tuple[tuple[tuple[float, ...], ...], ...] = (
    (
        (-3.66418494e-10, 1.72087897e-09, 1.04857472e-08),
        (2.81656414e-06, -1.09398298e-05, -8.80890652e-05),
        (-5.94673980e-03, 1.41172509e-02, 1.85009090e-01),
    ),
    (
        (2.22336271e-09, -1.14775474e-08, -5.65025114e-08),
        (-1.65831030e-05, 7.13478801e-05, 4.76082956e-04),
        (3.38019071e-02, -8.72990757e-02, -9.85248276e-01),
    ),
    (
        (-3.31242445e-09, 1.88339750e-08, 6.44687148e-08),
        (2.37737242e-05, -1.14880656e-04, -5.43870648e-04),
        (-4.59804512e-02, 1.36903699e-01, 1.92659163e00),
    ),
)

#: Pressure ratio at which the Guth shape is sampled to get a speed-only
#: factor. Their correlation is a polynomial in pressure ratio fitted over a
#: limited range and diverges outside it, so the shape is taken at a single
#: representative lift rather than evaluated at the running pressure ratio.
GUTH_PR_SHAPE = 2.5
#: Evaporating temperature at which the shape is sampled [degC].
GUTH_TEVAP_SHAPE = 0.0

#: Electro-mechanical efficiency at :data:`RPS_REF`. Guth's own machine sits at
#: 0.80-0.83 over the usable range, and the value the TMHP validation
#: manuscript declares is 0.80, so the two agree without adjustment.
ETA_EM_REF = 0.80


def guth_eta_comp(pressure_ratio: float, rpm: float, t_evap_c: float) -> float:
    """Guth & Atakan (2023) Eq. (22) combined electro-mechanical efficiency.

    Reproduced as published so the shape below is traceable rather than
    asserted. Valid over their test envelope; the polynomial in pressure ratio
    leaves physical values above roughly ``PR = 4``, which is why
    :func:`speed_shape_em` samples it at a fixed lift.
    """
    t_vec = (t_evap_c**2, t_evap_c, 1.0)
    n_vec = (rpm**2, rpm, 1.0)
    total = 0.0
    for i, matrix in enumerate(_GUTH_A):
        inner = 0.0
        for row, n_component in zip(matrix, n_vec, strict=True):
            inner += sum(a * t for a, t in zip(row, t_vec, strict=True)) * n_component
        total += pressure_ratio ** (2 - i) * inner
    return total


def speed_shape_em(rps: float) -> float:
    """Speed dependence of electro-mechanical efficiency, normalised at 50 rev/s.

    Taken from :func:`guth_eta_comp`, which peaks near 70 rev/s and falls away
    on both sides -- fixed friction and magnetising losses dominate at low
    speed, flow and windage losses at high speed.

    Cross-checked independently: Ossorio & Navarro-Peris measure drive
    efficiency alone for three inverters over 15-110 Hz, and fitting
    ``eta = eta_max * f / (f + f0)`` to those 185 points gives a 6-11 % drop
    from 50 to 15 rev/s. The shape here gives about 8 %, which is consistent
    and correctly larger than the drive-only figure, since it also carries the
    motor and the bearings.
    """
    clamped = min(max(rps, RPS_FIT_MIN), RPS_FIT_MAX)
    numerator = guth_eta_comp(GUTH_PR_SHAPE, clamped * 60.0, GUTH_TEVAP_SHAPE)
    denominator = guth_eta_comp(GUTH_PR_SHAPE, RPS_REF * 60.0, GUTH_TEVAP_SHAPE)
    return numerator / denominator


def eta_em_default(pressure_ratio: float, rps: float) -> float:
    """Electro-mechanical efficiency at a pressure ratio and speed.

    The pressure ratio is accepted for signature compatibility with the other
    correlations and is not used: the published speed dependence is taken at a
    fixed representative lift, for the reason given on :data:`GUTH_PR_SHAPE`.
    """
    del pressure_ratio
    return ETA_EM_REF * speed_shape_em(rps)
