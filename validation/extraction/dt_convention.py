"""L1 -- prove that catalogue inversion is an identity, not a fit.

Claim under test
----------------
For a coil whose refrigerant side changes phase, the LMTD statement
``Q = UA * LMTD`` and the effectiveness-NTU statement
``Q = C_air * eps * DT1`` are the same equation. If that is true, a catalogue
that declares duty, air flow and rating temperature difference determines the
conductance uniquely and there is no parameter left to choose -- the inversion
cannot be tuned to make a result look better.

What this script checks
-----------------------
1. Over NTU 0.2 to 5.0 the ratio of the two statements is 1 to within
   floating-point noise.
2. ``UA/Q`` computed on an LMTD basis equals ``UA/Q`` computed on the
   inlet-end (DT1) basis.
3. The outlet-end approach temperature is the *only* quantity that differs
   between conventions, and it converts exactly through
   ``LMTD / dT_out = eps / (NTU exp(-NTU))``.

Run
---
``uv run python -m validation.extraction.dt_convention``
"""

from __future__ import annotations

import math

from ._inversion import AIR_CP, AIR_RHO, lmtd_over_outlet_approach

NTU_GRID = [0.2 + 0.02 * i for i in range(241)]  # 0.20 .. 5.00


def check_identity(ntu: float, dt1_k: float = 8.0, c_air: float = 1000.0) -> dict[str, float]:
    """Compare the two descriptions of the same phase-change coil."""
    eps = 1.0 - math.exp(-ntu)
    ua = ntu * c_air
    duty_entu = c_air * eps * dt1_k

    # LMTD for a constant-temperature sink: inlet difference DT1, outlet
    # difference DT1 * exp(-NTU).
    dt_out = dt1_k * math.exp(-ntu)
    lmtd = (dt1_k - dt_out) / math.log(dt1_k / dt_out)
    duty_lmtd = ua * lmtd

    return {
        "ntu": ntu,
        "eps": eps,
        "ratio": duty_lmtd / duty_entu,
        "ua_over_q_lmtd": ua / duty_lmtd,
        "ua_over_q_entu": ua / duty_entu,
        "lmtd_k": lmtd,
        "dt_out_k": dt_out,
        "lmtd_over_dt_out": lmtd / dt_out,
        "closed_form_ratio": lmtd_over_outlet_approach(ntu),
    }


def main() -> None:
    worst_ratio = 0.0
    worst_uaq = 0.0
    worst_conv = 0.0
    for ntu in NTU_GRID:
        r = check_identity(ntu)
        worst_ratio = max(worst_ratio, abs(r["ratio"] - 1.0))
        worst_uaq = max(worst_uaq, abs(r["ua_over_q_lmtd"] / r["ua_over_q_entu"] - 1.0))
        worst_conv = max(worst_conv, abs(r["lmtd_over_dt_out"] / r["closed_form_ratio"] - 1.0))

    print("L1 -- catalogue inversion is an identity")
    print(f"  NTU range checked            : {NTU_GRID[0]:.2f} .. {NTU_GRID[-1]:.2f} ({len(NTU_GRID)} points)")
    print(f"  air properties declared      : rho = {AIR_RHO} kg/m3, cp = {AIR_CP} J/(kg K)")
    print(f"  max |Q_LMTD / Q_eNTU - 1|    : {worst_ratio:.3e}")
    print(f"  max |UA/Q ratio - 1|         : {worst_uaq:.3e}")
    print(f"  max closed-form conv. error  : {worst_conv:.3e}")
    print()
    print("  NTU    eps      LMTD/DT1   dT_out/DT1   LMTD/dT_out")
    for ntu in (0.2, 0.5, 1.0, 2.0, 3.0, 5.0):
        r = check_identity(ntu)
        print(
            f"  {ntu:4.1f}  {r['eps']:.4f}   {r['lmtd_k'] / 8.0:.4f}     "
            f"{r['dt_out_k'] / 8.0:.4f}       {r['lmtd_over_dt_out']:.3f}"
        )
    print()
    print("  Conclusion: the conductance follows in closed form from the declared")
    print("  triple. Zero free parameters -- the reporting convention changes the")
    print("  name of the temperature difference, never the conductance.")

    assert worst_ratio < 1e-12, worst_ratio
    assert worst_uaq < 1e-12, worst_uaq
    assert worst_conv < 1e-12, worst_conv


if __name__ == "__main__":
    main()
