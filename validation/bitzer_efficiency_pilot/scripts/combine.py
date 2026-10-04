"""Concatenate raw/per_compressor/*.csv into raw/bitzer_raw_points.csv.

usage: combine.py PILOT_DIR
"""

import sys
from pathlib import Path

import pandas as pd

d = Path(sys.argv[1])
files = sorted((d / "raw" / "per_compressor").glob("*.csv"))
df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
df.to_csv(d / "raw" / "bitzer_raw_points.csv", index=False)
ok = df.BITZER_limit_status == 0
print(f"{len(files)} compressors, {len(df)} requests, {ok.sum()} inside envelope, {(~ok).sum()} refused")
print(
    df.groupby(["refrigerant", "compressor_id"])
    .BITZER_limit_status.apply(lambda s: f"{(s == 0).sum()}/{len(s)}")
    .to_string()
)
