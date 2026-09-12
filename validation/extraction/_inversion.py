"""Shared identities for inverting a coil catalogue entry into a conductance.

The whole evidence chain rests on one fact: when the refrigerant side of a coil
changes phase its capacity rate is unbounded, the capacity-rate ratio ``C_r``
vanishes, and the LMTD and effectiveness-NTU descriptions of the same coil
become *the same equation*. A catalogue that declares duty, air volume flow and
a rating temperature difference therefore pins the conductance exactly. Nothing
is fitted; the result is either right or the catalogue was misread.

Reporting convention
--------------------
Conductance is reported three ways, all exactly interconvertible:

``UA``
    W/K, the physical conductance.
``UA/Q``
    W/K per watt of coil duty -- the capacity-normalised form a default rule
    needs.
``equivalent LMTD``
    ``Q / UA`` in kelvin. This is the number to compare across sources,
    because unlike "approach temperature" it does not depend on where along
    the coil the difference is measured.

The outlet-end approach temperature is *not* used as a cross-source metric: it
differs from the LMTD by ``eps / (NTU * exp(-NTU))`` and so two catalogues that
describe identical hardware report different approaches whenever their NTU
differs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = [
    "AIR_CP",
    "AIR_RHO",
    "air_capacity_rate",
    "ua_from_declared_rating",
    "equivalent_lmtd",
    "lmtd_over_outlet_approach",
    "CoilInversion",
    "invert",
]

#: Moist-air specific heat at the rating conditions used by EN 328 / ENV 327
#: [J/(kg K)]. Dry-air value; the rating points are declared on a dry basis.
AIR_CP = 1005.0
#: Air density at 0 degC, 101.325 kPa [kg/m^3]. Component catalogues declare
#: volumetric flow at the coil inlet, so the inlet-state density is the one
#: that converts it to a capacity rate.
AIR_RHO = 1.2922


def air_capacity_rate(volume_flow_m3_s: float, rho: float = AIR_RHO, cp: float = AIR_CP) -> float:
    """Air-side capacity rate ``C = rho * V * cp`` [W/K]."""
    return rho * volume_flow_m3_s * cp


def ua_from_declared_rating(duty_w: float, c_air: float, dt1_k: float) -> float:
    """Conductance implied by a declared (duty, air flow, DT1) triple [W/K].

    ``DT1`` is the rating standards' temperature difference: entering air minus
    refrigerant saturation temperature. With ``C_r = 0`` the effectiveness is
    ``eps = Q / (C_air * DT1)`` and ``NTU = -ln(1 - eps)``, so

    .. math::

        UA = -\\ln\\left(1 - \\frac{Q}{C_{air}\\,\\Delta T_1}\\right) C_{air}

    Raises
    ------
    ValueError
        If the declared duty exceeds ``C_air * DT1``. That is thermodynamically
        impossible (effectiveness above one) and always means a misread column,
        not an unusual coil -- which is exactly why this inversion is a useful
        guard against table-column drift in a multi-manufacturer scrape.
    """
    eps = duty_w / (c_air * dt1_k)
    if not 0.0 < eps < 1.0:
        raise ValueError(
            f"effectiveness {eps:.4f} outside (0, 1): duty {duty_w:.1f} W, "
            f"C_air {c_air:.1f} W/K, DT1 {dt1_k:.2f} K -- check column mapping"
        )
    return -math.log(1.0 - eps) * c_air


def equivalent_lmtd(duty_w: float, ua_w_k: float) -> float:
    """Logarithmic mean temperature difference implied by duty and UA [K]."""
    return duty_w / ua_w_k


def lmtd_over_outlet_approach(ntu: float) -> float:
    """Ratio LMTD / outlet-end approach for a phase-change coil.

    Exact for ``C_r = 0``: ``LMTD / dT_out = eps / (NTU * exp(-NTU))``. Quoted
    whenever an outlet approach is reported so the reader can convert.
    """
    eps = 1.0 - math.exp(-ntu)
    return eps / (ntu * math.exp(-ntu))


@dataclass(frozen=True)
class CoilInversion:
    """One catalogue row reduced to convention-free conductance measures."""

    duty_w: float
    volume_flow_m3_s: float
    dt1_k: float
    c_air_w_k: float
    eps: float
    ntu: float
    ua_w_k: float
    lmtd_k: float
    ua_over_q: float
    flow_m3h_per_kw: float


def invert(duty_w: float, volume_flow_m3_s: float, dt1_k: float, *, rho: float = AIR_RHO) -> CoilInversion:
    """Reduce one declared catalogue row to the reporting set above."""
    c_air = air_capacity_rate(volume_flow_m3_s, rho=rho)
    ua = ua_from_declared_rating(duty_w, c_air, dt1_k)
    eps = duty_w / (c_air * dt1_k)
    ntu = ua / c_air
    return CoilInversion(
        duty_w=duty_w,
        volume_flow_m3_s=volume_flow_m3_s,
        dt1_k=dt1_k,
        c_air_w_k=c_air,
        eps=eps,
        ntu=ntu,
        ua_w_k=ua,
        lmtd_k=equivalent_lmtd(duty_w, ua),
        ua_over_q=ua / duty_w,
        flow_m3h_per_kw=volume_flow_m3_s * 3600.0 / (duty_w / 1000.0),
    )
