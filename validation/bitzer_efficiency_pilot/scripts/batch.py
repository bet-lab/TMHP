"""Run extract.py for a list of compressors in parallel (one BITZER session each).

usage: batch.py OUT_DIR SPEC [SPEC ...]    SPEC = MODULE:SERIES:REFRIGERANT:MODEL:TYPE[:f1,f2,...]
Each compressor goes to its own CSV (OUT_DIR/<model>_<refrigerant>.csv) so reruns are idempotent.
The grid can be overridden with BZ_SST / BZ_SDT (comma lists); BZ_TAG is appended to the file name
(used for the low-lift extension of the scroll grid, see plr_cop/README.md).
"""

import os
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(__file__))
from extract import run  # noqa: E402

SST = [float(x) for x in os.environ.get("BZ_SST", "-15,-10,-5,0,5").split(",")]
SDT = [float(x) for x in os.environ.get("BZ_SDT", "35,40,45,50,55").split(",")]
TAG = os.environ.get("BZ_TAG", "")
F = [35, 40, 45, 50, 55, 60, 65, 70, 75]


def job(spec, out_dir):
    parts = spec.split(":")
    mod, ser, ref, mdl, typ = parts[:5]
    freqs = [float(x) for x in parts[5].split(",")] if len(parts) > 5 else F
    out = os.path.join(out_dir, f"{mdl}_{ref}{TAG}.csv")
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
    with ThreadPoolExecutor(max_workers=int(os.environ.get("BZ_WORKERS", "3"))) as ex:
        for msg in ex.map(lambda s: job(s, out_dir), sys.argv[2:]):
            print(msg, flush=True)
