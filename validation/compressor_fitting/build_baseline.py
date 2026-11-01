"""Reconstruct the published R290 fits, then audit a PR-speed surrogate.

Run from the repository root. These are correlation-generated points, not
133 measured observations. The loss expression follows the author's thesis
Eq. 3.22, correcting the omitted complement in journal Eq. 22.
"""

import json
from pathlib import Path

import CoolProp.CoolProp as CP
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent


def features(pr, speed):
    """Return the six specified quadratic terms in absolute rev/s."""
    return np.column_stack([np.ones_like(pr), pr, speed, pr**2, speed**2, pr * speed])


def reconstruct():
    """Evaluate journal Eqs. 10/17 and thesis Eq. 3.22 on a fixed grid."""
    published = json.loads((ROOT / "published_coefficients.json").read_text())
    mass = published["eq10_mass_flow"]["Table3"]["scroll-R290"]
    work = published["eq17_specific_consumption"]["Table4"]["scroll-R290"]
    loss = published["eq22_electromechanical_loss"]["Table5"]["scroll-R290"]
    rows = []
    for te in range(-10, 16, 5):
        for tc in range(20, 71, 5):
            if tc - te < 15:
                continue
            pe = CP.PropsSI("P", "T", te + 273.15, "Q", 1, "Propane")
            pc = CP.PropsSI("P", "T", tc + 273.15, "Q", 1, "Propane")
            h1 = CP.PropsSI("H", "P", pe, "T", te + 283.15, "Propane")
            s1 = CP.PropsSI("S", "P", pe, "T", te + 283.15, "Propane")
            rho = CP.PropsSI("D", "P", pe, "T", te + 283.15, "Propane")
            h2s = CP.PropsSI("H", "P", pc, "S", s1, "Propane")
            for speed in range(30, 111, 10):

                def factor(k, speed=speed):
                    """Apply the speed parabola normalized at 70 rev/s."""
                    return k["k4"] * ((speed - k["k5"]) / 70) ** 2 + 1 - k["k4"] * (1 - k["k5"] / 70) ** 2

                p, q = pe / 1e5, pc / 1e5
                mdot = (
                    (mass["k0"] + mass["k1"] * p + mass["k2"] * q + mass["k3"] * p * q)
                    * (speed / 70)
                    * factor(mass)
                    / 1000
                )
                specific = (work["k0"] + work["k1"] * (q - work["k2"]) / (p - work["k3"])) * factor(work) * 1000
                power = mdot * specific
                pr = pc / pe
                f = loss["k0"] + loss["k1"] * pr + loss["k2"] * pr / (speed / 70) + loss["k3"] / power
                rows.append(
                    dict(
                        Te_C=te,
                        Tc_C=tc,
                        N_rps=speed,
                        PR=pr,
                        mdot_kg_s=mdot,
                        Pel_W=power,
                        eta_v=mdot / (rho * 46e-6 * speed),
                        eta_ov=(h2s - h1) / specific,
                        f_loss=f,
                    )
                )
    return pd.DataFrame(rows)


def main():
    """Save generated points and the exact surrogate coefficients/audit."""
    points = reconstruct()
    matrix = features(points.PR.to_numpy(), points.N_rps.to_numpy())
    scale = np.linalg.norm(matrix, axis=0)
    fits = {}
    for target in ("eta_v", "eta_ov", "f_loss"):
        co = np.linalg.lstsq(matrix / scale, points[target], rcond=None)[0] / scale
        residual = matrix @ co - points[target]
        fits[target] = dict(
            coefficients=co.tolist(),
            rmse=float(np.sqrt(np.mean(residual**2))),
            max_abs_error=float(np.max(abs(residual))),
        )
    payload = dict(
        version="Ossorio2024-R290-PRN-quadratic-2026-11-01",
        generated_points=len(points),
        terms=["1", "PR", "N", "PR^2", "N^2", "PR*N"],
        speed_unit="rev/s",
        refrigerant="R290",
        displacement_m3_rev=46e-6,
        superheat_K=10,
        source="https://doi.org/10.1016/j.applthermaleng.2024.122666",
        source_kind="published-correlations, then quadratic surrogate; not measured rows",
        loss_definition="1-eta_em; author thesis Eq.3.22; journal Eq.22 omits complement",
        grid=dict(Te_C=[-10, 15, 5], Tc_C=[20, 70, 5], N_rps=[30, 110, 10], min_lift_K=15),
        domain=dict(PR=[float(points.PR.min()), float(points.PR.max())], N_rps=[30, 110]),
        fits=fits,
    )
    points.to_csv(ROOT / "baseline_generated_points.csv", index=False)
    (ROOT / "baseline_surrogate.json").write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
