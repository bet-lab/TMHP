"""Low-speed compressor losses -- what the measurements actually support.

Background
----------
The starting assumption of this work was that a real inverter heat pump peaks
somewhere around 35 % load and falls away below it, and that TMHP was wrong to
show COP rising all the way down. Certified declared data says otherwise:
across 17,480 Heat Pump Keymark rows the declared COP rises monotonically from
test point A to D, and 99 % of machines report a higher COP at the lightest
load than at the next one up. The part-load penalty real equipment suffers
comes from the minimum modulation limit and from on/off cycling, neither of
which TMHP models -- not from the compressor efficiency correlations.

So the correlations must not be bent to manufacture a roll-over. What they
*should* carry is the low-speed loss that is actually measured, at the size it
is actually measured, and no more.

What is measured
----------------
Ossorio & Navarro-Peris (2023), doi:10.1016/j.applthermaleng.2023.120725
(CC BY) publish, as supplementary data, drive efficiency against output
frequency for three variable-speed compressor inverters over 15-110 Hz --
185 measured points. This is the only source in the collected evidence that
goes below 30 Hz, and it makes the low-speed electro-mechanical penalty a
measurement rather than an assertion.

Cuevas & Lebrun (2009), doi:10.1016/j.applthermaleng.2008.03.016, separate the
loss: at 35 Hz both the isentropic and the volumetric effectiveness degrade,
while at 75 Hz only the isentropic one does. The low-speed loss therefore has
an internal-leakage component and cannot be attributed to friction alone --
which is why TMHP splits it across ``eta_cmp_vol`` and ``eta_cmp`` rather than
loading it all onto one coefficient.

Speed convention
----------------
The published axis is drive output frequency in Hz. The compressors are
two-pole, so one hertz is one revolution per second and the fit is used
directly against ``rps``. A four-pole machine would need the factor of two;
this is stated wherever the coefficients appear.

Run
---
``uv run python -m validation.extraction.compressor_speed_losses``
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import curve_fit

from ._pdftext import EvidenceMissing, evidence_path

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_CSV = REPO_ROOT / "validation" / "data" / "inverter_efficiency_ossorio2023.csv"
OUT_JSON = REPO_ROOT / "validation" / "data" / "compressor_speed_loss_fit.json"

XLSX = "data/ossorio2023/1-s2.0-S1359431123007548-mmc2.xlsx"
SHEETS = ("Inverter A", "Inverter B", "Inverter C")

#: Speed at which the fitted shape is normalised to 1 [rev/s]. Chosen as the
#: middle of the measured range, not as a favourable point.
RPS_REF = 50.0


def saturating(f: np.ndarray, eta_max: float, f0: float) -> np.ndarray:
    """``eta = eta_max * f / (f + f0)`` -- a fixed loss against a rising output.

    The form is not chosen for goodness of fit. A drive carries switching and
    magnetising losses that are nearly independent of how fast it is turning,
    so the *fraction* lost scales as one over the speed and the efficiency
    saturates. ``f0`` is the frequency at which those fixed losses equal the
    delivered output.
    """
    return eta_max * f / (f + f0)


def load_measurements() -> pd.DataFrame:
    import openpyxl

    path = evidence_path(XLSX)
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    rows = []
    for sheet in SHEETS:
        ws = wb[sheet]
        for r in ws.iter_rows(min_row=2, values_only=True):
            if r[0] is None:
                continue
            try:
                freq, p_in, p_out, eta = (float(r[0]), float(r[1]), float(r[2]), float(r[3]))
            except (TypeError, ValueError):
                continue
            rows.append(
                {
                    "inverter": sheet.replace("Inverter ", ""),
                    "frequency_hz": freq,
                    "P_in_W": p_in,
                    "P_out_W": p_out,
                    "eta_drive": eta,
                }
            )
    df = pd.DataFrame(rows)
    # Verbatim check against the printed sheet before trusting the rest.
    a30 = df[(df.inverter == "A") & (df.frequency_hz == 30.0)]
    assert len(a30) == 26, f"Inverter A at 30 Hz: expected 26 points, got {len(a30)}"
    assert abs(a30.eta_drive.max() - 0.9133) < 5e-4, a30.eta_drive.max()
    return df


def main() -> None:
    print("Low-speed compressor losses -- Ossorio & Navarro-Peris (2023) drive data")
    try:
        df = load_measurements()
    except EvidenceMissing as exc:
        print(f"  [skip] {exc}")
        return
    df.to_csv(OUT_CSV, index=False)

    print(
        f"  {len(df)} measured points, {df.frequency_hz.min():.0f}-{df.frequency_hz.max():.0f} Hz, "
        f"{df.inverter.nunique()} drives"
    )
    print()
    print(
        f"  {'drive':<8}{'n':>5}{'eta_max':>10}{'f0 [Hz]':>10}{'RMSE':>10}"
        f"{'eta@15':>9}{'eta@30':>9}{'eta@50':>9}{'eta@90':>9}"
    )
    fits = {}
    for inv, g in df.groupby("inverter"):
        popt, _ = curve_fit(saturating, g.frequency_hz.to_numpy(), g.eta_drive.to_numpy(), p0=[0.98, 3.0], maxfev=20000)
        resid = g.eta_drive.to_numpy() - saturating(g.frequency_hz.to_numpy(), *popt)
        rmse = float(np.sqrt((resid**2).mean()))
        fits[inv] = {"eta_max": float(popt[0]), "f0_hz": float(popt[1]), "rmse": rmse, "n": int(len(g))}
        vals = [saturating(np.array([f]), *popt)[0] for f in (15, 30, 50, 90)]
        print(
            f"  {inv:<8}{len(g):>5}{popt[0]:>10.4f}{popt[1]:>10.3f}{rmse:>10.5f}"
            f"{vals[0]:>9.4f}{vals[1]:>9.4f}{vals[2]:>9.4f}{vals[3]:>9.4f}"
        )

    f0_all = float(np.median([v["f0_hz"] for v in fits.values()]))
    print()
    print(f"  median f0 across the three drives: {f0_all:.2f} Hz")
    print()
    print("  Normalised to the reference speed, the shape TMHP carries is")
    print(f"    s(rps) = [rps / (rps + {f0_all:.2f})] / [{RPS_REF:.0f} / ({RPS_REF:.0f} + {f0_all:.2f})]")
    print(f"  {'rps':>6}{'s(rps)':>10}{'penalty':>10}")
    ref = RPS_REF / (RPS_REF + f0_all)
    for rps in (10, 15, 20, 30, 40, 50, 70, 90, 110):
        s = (rps / (rps + f0_all)) / ref
        print(f"  {rps:>6}{s:>10.4f}{(s - 1.0) * 100:>9.1f}%")
    print()
    print("  That is the drive alone. Motor and mechanical friction add to it,")
    print("  so a total electro-mechanical efficiency should fall at least this")
    print("  steeply -- the fitted shape is a floor on the penalty, not a ceiling.")

    import json

    OUT_JSON.write_text(
        json.dumps(
            {
                "source": "Ossorio & Navarro-Peris 2023, doi:10.1016/j.applthermaleng.2023.120725, mmc2",
                "form": "eta = eta_max * f / (f + f0)",
                "speed_convention": "two-pole drives; 1 Hz = 1 rev/s",
                "per_drive": fits,
                "f0_hz_median": f0_all,
                "rps_ref": RPS_REF,
            },
            indent=2,
        )
        + "\n"
    )
    print(f"  wrote {OUT_CSV.relative_to(REPO_ROOT)}")
    print(f"  wrote {OUT_JSON.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
