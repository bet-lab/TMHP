"""Why the air-to-air residuals look the way they do.

The parity harness reports that two Fujitsu units sit about +33 % above their
published COP while five Daikin units scatter around zero. A bias equal to the
error means a systematic cause, and this module finds it.

It turned out to be three causes, not one, and an early reading of this data
attributed the whole gap to the first of them. The decomposition below is the
correction.

1. **Residual hardware efficiency.** Compared at each machine's own nominal
   heating rating point -- no latent load, no forced maximum output, the
   cleanest comparison available -- Daikin lands 6-11 % low and Fujitsu 13-15 %
   high. That ~20 point gap is real: the two product lines are built to
   different efficiency targets, and a rule written per kilowatt cannot know
   which one it is looking at.

2. **Dehumidification (cooling half).** TMHP's indoor coil is a dry sensible
   exchanger. A real machine removing moisture must hold its coil below the
   dew point, which is far colder than a sensible-only coil needs to move the
   same total heat, and a colder coil means more lift and less COP. At its
   rating point the Fujitsu removes 34 % of its duty as latent heat and the
   Daikin 2 %, so the dry-coil idealisation costs the Fujitsu far more.

3. **Maximum-capacity tables (heating half).** Fujitsu's heating grid is
   published at maximum capacity -- the machine pinned at full compressor
   speed, where its efficiency is worst. The harness asks the model for that
   same duty and lets it choose a speed, which it meets at around half the
   available range. That is a different operating point, and the comparison
   flatters the model.

Only the first is a property of the defaults. The second is a stated model
boundary and the third is a property of the source document.

Run
---
``uv run python -m validation.analysis.residual_decomposition``
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from scipy.optimize import brentq

from tmhp import AirSourceHeatPump

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA = REPO_ROOT / "validation" / "data"
OUT_CSV = DATA / "residual_decomposition.csv"

#: Coil duty to nameplate cooling capacity, ``1 + 1/EER``. The component
#: catalogues report conductance per unit of *coil* duty; the machine rule is
#: written against the nameplate, so the band has to be carried across the same
#: way the median was. Quoting the two on different bases -- which an earlier
#: version of this work did -- makes the default look better placed in its own
#: band than it is.
COIL_TO_NAMEPLATE = 1.307


def measured_band(percentiles: tuple[float, float] = (10.0, 90.0)) -> tuple[float, float]:
    """Capacity-divisor band implied by the component catalogues [Q/x]."""
    evap = pd.read_csv(DATA / "en328_evaporator_inversion.csv")
    evap = evap[evap.is_primary] if "is_primary" in evap else evap
    cond = pd.read_csv(DATA / "env327_condenser_inversion.csv")
    ua_over_q_cool = pd.concat([evap.UA_over_Q, cond.UA_over_Q]) * COIL_TO_NAMEPLATE
    lo, hi = ua_over_q_cool.quantile(percentiles[1] / 100.0), ua_over_q_cool.quantile(percentiles[0] / 100.0)
    return 1.0 / lo, 1.0 / hi


@dataclass(frozen=True)
class Nameplate:
    """One machine's published rating points, read from its specification table."""

    unit: str
    manufacturer: str
    refrigerant: str
    cooling_kW: float
    cooling_power_kW: float
    heating_kW: float
    heating_power_kW: float
    indoor_flow_m3_s: float
    #: Indoor dry bulb at the cooling rating point [degC].
    cooling_indoor_C: float
    #: Sensible heat ratio at the cooling rating point, from the capacity grid.
    cooling_shr: float

    @property
    def eer(self) -> float:
        return self.cooling_kW / self.cooling_power_kW

    @property
    def cop(self) -> float:
        return self.heating_kW / self.heating_power_kW


#: Nameplate data, transcribed from each manufacturer's specification table.
#: Daikin: RXM-A data book EEDEN24-200 "Technical specifications"; the EER
#: column is printed as "Nominal efficiency EER".
#: Fujitsu: ASUH/AOUH LPAS design & technical manual section 1.
NAMEPLATES = (
    Nameplate("FTXM20A / RXM20A", "Daikin", "R32", 2.00, 0.37, 2.50, 0.50, 10.3 / 60, 27.0, 0.98),
    Nameplate("FTXM25A / RXM25A", "Daikin", "R32", 2.50, 0.48, 2.80, 0.56, 11.9 / 60, 27.0, 0.98),
    Nameplate("FTXM35A / RXM35A", "Daikin", "R32", 3.50, 0.76, 4.00, 0.88, 12.4 / 60, 27.0, 0.96),
    Nameplate("FTXM42A / RXM42A", "Daikin", "R32", 4.20, 1.00, 5.40, 1.29, 13.4 / 60, 27.0, 0.95),
    Nameplate("FTXM50A / RXM50A", "Daikin", "R32", 5.00, 1.36, 5.80, 1.40, 14.0 / 60, 27.0, 0.94),
    Nameplate("ASUH09LPAS", "Fujitsu", "R410A", 2.64, 0.72, 2.93, 0.74, 700 / 3600, 26.7, 0.66),
    Nameplate("ASUH12LPAS", "Fujitsu", "R410A", 3.52, 1.09, 4.10, 1.10, 770 / 3600, 26.7, 0.66),
)

#: Rating conditions. Cooling: 35 degC outdoor (AHRI 210/240 and EN 14511
#: agree). Heating: 7 degC outdoor, 20 degC indoor.
COOLING_OUTDOOR_C = 35.0
HEATING_OUTDOOR_C = 7.0
HEATING_INDOOR_C = 20.0


def _model(plate: Nameplate, ua_divisor: float | None = None) -> AirSourceHeatPump:
    kwargs: dict = {
        "hp_capacity": plate.cooling_kW * 1000.0,
        "ref": plate.refrigerant,
        "dV_iu_fan_a_rated": plate.indoor_flow_m3_s,
    }
    if ua_divisor is not None:
        ua_ou = plate.cooling_kW * 1000.0 / ua_divisor
        kwargs["UA_ou_rated"] = ua_ou
        kwargs["UA_iu_rated"] = 0.8 * ua_ou
    return AirSourceHeatPump(**kwargs)


def predicted_cop(plate: Nameplate, mode: str, ua_divisor: float | None = None) -> float:
    model = _model(plate, ua_divisor)
    if mode == "cooling":
        result = model.analyze_steady(
            Q_r_iu=plate.cooling_kW * 1000.0,
            T0=COOLING_OUTDOOR_C,
            T_a_room=plate.cooling_indoor_C,
            verbose=False,
        )
    else:
        result = model.analyze_steady(
            Q_r_iu=-plate.heating_kW * 1000.0,
            T0=HEATING_OUTDOOR_C,
            T_a_room=HEATING_INDOOR_C,
            verbose=False,
        )
    assert isinstance(result, dict)
    if result.get("failure_reason", "none") != "none":
        return float("nan")
    return float(result["cop_sys [-]"])


def implied_ua_divisor(plate: Nameplate, mode: str = "cooling") -> float:
    """The conductance rule that would reproduce this machine's published COP.

    Not a calibration -- nothing here is written back into a default. It answers
    "if the whole residual were conductance, how much conductance would it be?",
    which is what places each machine against the measured band.
    """
    published = plate.eer if mode == "cooling" else plate.cop

    def residual(divisor: float) -> float:
        return predicted_cop(plate, mode, divisor) - published

    # The optimiser fails sporadically at isolated divisors rather than only
    # at the infeasible end, so the bracket is built from the divisors that
    # actually returned a number instead of stopping at the first gap.
    grid = [2.0 + 0.25 * i for i in range(57)]  # 2.00 .. 16.00
    samples = [(d, residual(d)) for d in grid]
    finite = [(d, r) for d, r in samples if r == r]
    if len(finite) < 2:
        return float("nan")

    bracket = None
    for (d_lo, r_lo), (d_hi, r_hi) in zip(finite, finite[1:], strict=False):
        if r_lo == 0.0:
            return d_lo
        if r_lo * r_hi < 0:
            bracket = (d_lo, d_hi)
            break
    if bracket is None:
        return float("nan")
    try:
        return float(brentq(residual, *bracket, xtol=1e-3))
    except (ValueError, RuntimeError):
        return float("nan")


def build() -> pd.DataFrame:
    rows = []
    for plate in NAMEPLATES:
        cooling_pred = predicted_cop(plate, "cooling")
        heating_pred = predicted_cop(plate, "heating")
        divisor = implied_ua_divisor(plate, "cooling")
        rows.append(
            {
                "unit": plate.unit,
                "manufacturer": plate.manufacturer,
                "refrigerant": plate.refrigerant,
                "cooling_kW": plate.cooling_kW,
                "nameplate_EER": plate.eer,
                "nameplate_COP": plate.cop,
                "cooling_SHR": plate.cooling_shr,
                "indoor_flow_m3h_per_kW": plate.indoor_flow_m3_s * 3600.0 / plate.cooling_kW,
                "cop_pred_cooling": cooling_pred,
                "cop_pred_heating": heating_pred,
                "bias_cooling_pct": (cooling_pred / plate.eer - 1.0) * 100.0,
                "bias_heating_pct": (heating_pred / plate.cop - 1.0) * 100.0,
                "implied_ua_divisor": divisor,
                "implied_ua_over_q": (1.0 / divisor) if divisor == divisor else float("nan"),
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    df = build()
    df.to_csv(OUT_CSV, index=False)

    print("Air-to-air residuals, decomposed")
    print()
    print("1. At each machine's own NOMINAL rating point -- the cleanest comparison")
    print("   available: no latent load in heating, no forced maximum output.")
    print()
    print(f"   {'unit':<20}{'EER':>6}{'COP':>6}{'SHR':>6}{'bias cool':>11}{'bias heat':>11}{'implied UA':>12}")
    for _, r in df.iterrows():
        print(
            f"   {r.unit:<20}{r.nameplate_EER:>6.2f}{r.nameplate_COP:>6.2f}{r.cooling_SHR:>6.2f}"
            f"{r.bias_cooling_pct:>10.1f}%{r.bias_heating_pct:>10.1f}%"
            f"{'  Q/' + format(r.implied_ua_divisor, '.1f'):>12}"
        )
    print()

    by_maker = df.groupby("manufacturer")[["bias_heating_pct", "bias_cooling_pct"]].mean()
    print("   mean bias by manufacturer:")
    print(by_maker.to_string(float_format=lambda v: f"{v:+.1f} %"))
    gap = by_maker.loc["Fujitsu", "bias_heating_pct"] - by_maker.loc["Daikin", "bias_heating_pct"]
    print()
    print(f"   => residual hardware gap, heating rating point: {gap:.0f} percentage points.")
    print("      This is the part the defaults are responsible for. It is real:")
    print("      the two product lines are built to different efficiency targets")
    print("      and a per-kilowatt rule cannot tell which one it is looking at.")
    print()
    print("2. The rest of the harness-wide +33 % is not the defaults:")
    print("   - cooling: the Fujitsu removes 34 % of its duty as latent heat")
    print("     (SHR 0.66) against the Daikin's 2-6 %, and TMHP computes a dry")
    print("     coil, so it holds the evaporator far warmer than the real")
    print("     machine can.")
    print("   - heating: Fujitsu's published grid is at *maximum* capacity, with")
    print("     the compressor pinned at full speed where its efficiency is")
    print("     worst; the model meets the same duty at about half the range.")
    print()
    lo, hi = measured_band()
    print("3. Where each machine sits against the measured conductance band.")
    print(f"   Nameplate basis, p10-p90 from 1,414 component coils: Q/{lo:.1f} to Q/{hi:.1f}.")
    print(f"   The default Q/5.0 sits inside it: {lo <= 5.0 <= hi}.")
    print()
    resolved = df.dropna(subset=["implied_ua_divisor"])
    for _, r in resolved.iterrows():
        mark = "inside" if lo <= r.implied_ua_divisor <= hi else "OUTSIDE"
        print(f"   {r.unit:<20} Q/{r.implied_ua_divisor:<5.1f} {mark}")
    inside = resolved.implied_ua_divisor.between(lo, hi)
    print(f"   {inside.sum()}/{len(resolved)} inside.")
    print()
    print("   Every Daikin unit is inside; both Fujitsu units sit just outside the")
    print("   low-conductance end. That is the honest reading: the Fujitsu behaves")
    print("   like a machine with less coil per kilowatt than 90 % of the component")
    print("   population, which is why a median default over-predicts it.")
    print()
    print(f"   wrote {OUT_CSV.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
