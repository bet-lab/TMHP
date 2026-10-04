"""Run extract.py for a list of compressors in parallel (one BITZER session each).

usage: batch.py OUT_DIR SPEC [SPEC ...]    SPEC = MODULE:SERIES:REFRIGERANT:MODEL:TYPE[:f1,f2,...]
Each compressor goes to its own CSV (OUT_DIR/<model>_<refrigerant>.csv) so reruns are idempotent.
"""

import os
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(__file__))
from extract import run  # noqa: E402

SST = [-15, -10, -5, 0, 5]
SDT = [35, 40, 45, 50, 55]
F = [35, 40, 45, 50, 55, 60, 65, 70, 75]


def job(spec, out_dir):
    parts = spec.split(":")
    mod, ser, ref, mdl, typ = parts[:5]
    freqs = [float(x) for x in parts[5].split(",")] if len(parts) > 5 else F
    out = os.path.join(out_dir, f"{mdl}_{ref}.csv")
    if os.path.exists(out):
        return f"{mdl} {ref} skipped (exists)"
    tmp = out + ".part"
    if os.path.exists(tmp):
        os.remove(tmp)
    rows = run(mod, ser, ref, mdl, tmp, SST, SDT, freqs, {"type": typ})
    os.rename(tmp, out)
    return f"{mdl} {ref} {len(rows)} rows, ok {sum(1 for r in rows if r['BITZER_limit_status'] == 0)}"


if __name__ == "__main__":
    out_dir = sys.argv[1]
    os.makedirs(out_dir, exist_ok=True)
    with ThreadPoolExecutor(max_workers=3) as ex:
        for msg in ex.map(lambda s: job(s, out_dir), sys.argv[2:]):
            print(msg, flush=True)
