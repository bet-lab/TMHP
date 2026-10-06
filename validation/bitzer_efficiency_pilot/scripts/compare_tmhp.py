"""Compare BITZER speed ratios with the current TMHP default correlations (read-only).

TMHP electrical-to-isentropic product = eta_oi_product(PR, n*) * eta_em(PR, n*); eta_em carries
the drive term s(n*). BITZER power excludes the external FI, so the TMHP product is shown both
with and without s(n*).
usage: compare_tmhp.py EFFICIENCY_POINTS.csv OUT.csv
"""

import sys

import pandas as pd

from tmhp import compressor_efficiency as ce

df = pd.read_csv(sys.argv[1])
df = df[df.valid_bitzer == "yes"].copy()
eta_v = ce.make_eta_vol(1.0)


def rel(f, pr, ns):
    return f(pr, ns) / f(pr, 1.0)


def prod_nodrive(p, n):
    return ce.eta_oi_product(p, n) * ce.load_factor_em(p)


def prod_drive(p, n):
    return ce.eta_oi_product(p, n) * ce.speed_factor_em(n) * ce.load_factor_em(p)


rows = []
for (typ, ref), g in df.groupby(["compressor_type", "refrigerant"]):
    pr = g.pressure_ratio.median()
    for ns in sorted(g.normalized_speed.round(3).unique()):
        rows.append(
            dict(
                group=f"{typ}_{ref}",
                PR_median=round(pr, 2),
                normalized_speed=ns,
                tmhp_eta_v_rel=rel(eta_v, pr, ns),
                tmhp_product_rel_without_drive=rel(prod_nodrive, pr, ns),
                tmhp_product_rel_with_drive=rel(prod_drive, pr, ns),
            )
        )
out = pd.DataFrame(rows)
out.to_csv(sys.argv[2], index=False)
print(out.round(3).to_string())
