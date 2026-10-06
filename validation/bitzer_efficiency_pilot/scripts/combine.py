"""Concatenate raw/per_compressor/*.csv into raw/bitzer_raw_points.csv.

usage: combine.py PILOT_DIR [TAG]

Without TAG the pilot grid is combined (files carrying a ``_lowlift`` tag are left out, so the
pilot results stay reproducible).  With TAG=lowlift only the low-lift extension of the scroll
grid is combined, into raw/bitzer_raw_points_lowlift.csv (used by plr_cop/).
"""

import sys
from pathlib import Path

import pandas as pd

d = Path(sys.argv[1])
tag = sys.argv[2] if len(sys.argv) > 2 else ""
allf = sorted((d / "raw" / "per_compressor").glob("*.csv"))
files = [f for f in allf if (f"_{tag}" in f.stem)] if tag else [f for f in allf if "_lowlift" not in f.stem]
df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
df.to_csv(d / "raw" / (f"bitzer_raw_points_{tag}.csv" if tag else "bitzer_raw_points.csv"), index=False)
ok = df.BITZER_limit_status == 0
print(f"{len(files)} compressors, {len(df)} requests, {ok.sum()} inside envelope, {(~ok).sum()} refused")
print(
    df.groupby(["refrigerant", "compressor_id"])
    .BITZER_limit_status.apply(lambda s: f"{(s == 0).sum()}/{len(s)}")
    .to_string()
)
