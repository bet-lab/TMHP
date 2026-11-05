"""ASHP research baseline; other heat-pump classes retain their existing defaults.

The coefficients approximate the Ossorio 2024 R290 correlations on 567
generated points (Te -10..15 C, Tc 20..70 C, SH 10 K, N 30..110 rev/s).
The source model also depends on absolute pressures. Reducing it to PR,N is
an approximation: eta_v/eta_ov/f_loss RMSE is 0.00924/0.04053/0.00291.
See validation/compressor_fitting for generation and source audits.
"""

from collections.abc import Callable
from dataclasses import dataclass

__all__ = ["ossorio_r290_efficiencies", "OssorioR290Map", "BASELINE_VERSION"]

BASELINE_VERSION = "Ossorio2024-R290-PRN-quadratic-2026-11-01"
_COEFFICIENTS = {
    "eta_v": (
        0.965185902006016,
        -0.01607945554820537,
        0.0010871137352265598,
        0.0004879907092585861,
        -5.133283989055647e-06,
        -4.585054815608074e-06,
    ),
    "eta_ov": (
        0.12758677733633833,
        0.19531157021061987,
        0.0031522829259373014,
        -0.021664672500858607,
        -2.1748796144427422e-05,
        4.842102797710244e-06,
    ),
    "f_loss": (
        0.11095951233858059,
        0.018950229614289157,
        -0.0008047999721903226,
        -0.00015064700293870024,
        6.753743999445844e-06,
        -0.0001642311748040922,
    ),
}


@dataclass(frozen=True)
class OssorioR290Map:
    """Quadratic research map with an explicit absolute-speed convention.

    ``quantity`` accepts eta_v, eta_ov, f_loss, eta_is or eta_em. It never clips
    or holds a boundary; callers must check feasibility and extrapolation.
    ``eta_em`` uses the complement verified against author thesis Eq. 3.22.
    """

    quantity: str
    source_version: str = BASELINE_VERSION

    def __post_init__(self) -> None:
        """Reject unknown physical quantities at construction."""
        if self.quantity not in ("eta_v", "eta_ov", "f_loss", "eta_is", "eta_em"):
            raise ValueError("Unknown Ossorio map quantity")

    def __call__(self, pressure_ratio: float, rps: float) -> float:
        """Evaluate at PR and actual rev/s, preserving raw extrapolated values."""

        def polynomial(name: str) -> float:
            """Evaluate the documented six-term surrogate without clipping."""
            b0, b1, b2, b3, b4, b5 = _COEFFICIENTS[name]
            return (
                b0 + b1 * pressure_ratio + b2 * rps + b3 * pressure_ratio**2 + b4 * rps**2 + b5 * pressure_ratio * rps
            )

        if self.quantity == "eta_em":
            return 1 - polynomial("f_loss")
        if self.quantity == "eta_is":
            return polynomial("eta_ov") / (1 - polynomial("f_loss"))
        return polynomial(self.quantity)


def ossorio_r290_efficiencies() -> dict[str, Callable[[float, float], float]]:
    """Return the ASHP defaults as explicit overrides for compatible TMHP classes.

    AirSourceHeatPump selects these maps when efficiency inputs are omitted.
    Other classes can opt in through the existing constructor overrides. This
    R290 scroll surrogate is not validated for every refrigerant/technology;
    using it for another compressor remains an explicit modelling assumption.
    """
    return {
        "eta_cmp_vol": OssorioR290Map("eta_v"),
        "eta_cmp_isen": OssorioR290Map("eta_is"),
        "eta_cmp": OssorioR290Map("eta_em"),
    }
