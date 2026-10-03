"""Harvest Copeland Online Product Information (OPI) AHRI-540 coefficient sets for
variable-speed scroll compressors, one record per (model, speed variant).

Why OPI: it is the only public source found that publishes *compressor-level*
performance at several rotational speeds for the same machine -- each
variable-speed model (ZPV/YPV/XPV/ZHV/YHV) is listed once per rated speed
(V1/V2/V4 ...), and ``Compressor/GetCCoefficients`` returns the ten AHRI 540
polynomial coefficients for capacity, power, current and mass flow together
with the rating conventions (dew-point, 20 degF constant superheat,
15 degF subcooling, evaporating/condensing bounds, RPM in the comment).

Usage (needs the Chrome bridge):
    uv run --with playwright python3 -m validation.compressor_maps.fetch.copeland_opi \
        --patterns 'ZPV*' 'YPV*' 'XPV*' 'ZHV*' 'YHV*' --out validation/evidence/compressor_maps/copeland/opi

Writes  <out>/<MODEL>__<id>.json   (raw GetCCoefficients ResponseItem + CSummary text)
        <out>/index.csv            (one row per record: id, model, app, rpm, refrigerant, ...)
Nothing here is parsed into efficiencies; see ``parse.copeland_opi``.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from validation.compressor_maps.fetch._cdp import DEFAULT_CDP, connect

BASE = "https://webapps.copeland.com/online-product-information"
ROUTER = BASE + "/Home/OPICustomerRouterJSP?prodtype=cs&feedsource=EX-OPI&UNIT=E&part={part}"

ROWS_JS = """() => Array.from(document.querySelectorAll('#matchresult tbody tr')).map(tr => {
  const sp = tr.querySelector('span[onclick]');
  const m = sp ? /getModelDetails\\("(\\d+)","([^"]+)"/.exec(sp.getAttribute('onclick')) : null;
  const tds = Array.from(tr.querySelectorAll('td')).map(t => t.innerText.trim());
  return {id: m ? m[1] : null, model: m ? m[2] : tds[0], cells: tds};
})"""


def list_rows(page, part: str) -> list[dict]:
    page.goto(ROUTER.format(part=part), wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(6000)
    # show all entries
    try:
        page.select_option("select[name='matchresult_length']", "100", timeout=5000)
        page.wait_for_timeout(1500)
    except Exception:
        pass
    rows: list[dict] = []
    seen = set()
    for _ in range(40):  # paginate
        for r in page.evaluate(ROWS_JS):
            if r["id"] and r["id"] not in seen:
                seen.add(r["id"])
                rows.append(r)
        nxt = page.locator("#matchresult_next")
        if not nxt.count() or "disabled" in (nxt.first.get_attribute("class") or ""):
            break
        try:
            nxt.first.click(timeout=3000)
            page.wait_for_timeout(1200)
        except Exception:
            break
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--patterns", nargs="+", default=["ZPV*", "YPV*", "XPV*", "ZHV*", "YHV*"])
    ap.add_argument("--out", default="validation/evidence/compressor_maps/copeland/opi")
    ap.add_argument("--cdp-url", default=DEFAULT_CDP)
    ap.add_argument("--summary", action="store_true", help="also store CSummary text (slower)")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    index_path = out / "index.csv"
    done = set()
    if index_path.exists():
        with index_path.open() as fh:
            done = {r["id"] for r in csv.DictReader(fh)}
    fields = [
        "id",
        "model",
        "product_type",
        "status",
        "app",
        "capacity_btuh",
        "eer",
        "rpm",
        "phase_voltage",
        "ref",
        "pattern",
        "fetched",
    ]
    new_index = not index_path.exists()
    with sync_playwright() as p, index_path.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        if new_index:
            w.writeheader()
        b = connect(p, a.cdp_url)
        ctx = b.contexts[0]
        page = ctx.new_page()
        try:
            for part in a.patterns:
                rows = list_rows(page, part)
                print(f"[{part}] {len(rows)} rows", file=sys.stderr)
                for r in rows:
                    if r["id"] in done:
                        continue
                    c = r["cells"]
                    rec = {
                        "id": r["id"],
                        "model": r["model"],
                        "product_type": c[1] if len(c) > 1 else "",
                        "status": c[2] if len(c) > 2 else "",
                        "app": c[3] if len(c) > 3 else "",
                        "capacity_btuh": c[4] if len(c) > 4 else "",
                        "eer": c[5] if len(c) > 5 else "",
                        "rpm": c[6] if len(c) > 6 else "",
                        "phase_voltage": c[7] if len(c) > 7 else "",
                        "ref": c[8] if len(c) > 8 else "",
                        "pattern": part,
                        "fetched": time.strftime("%Y-%m-%d"),
                    }
                    try:
                        resp = page.request.post(BASE + "/Compressor/GetCCoefficients", data={"id": r["id"]})
                        js = resp.json()
                    except Exception as e:  # noqa: BLE001
                        print("  coeff err", r["id"], str(e)[:80], file=sys.stderr)
                        continue
                    payload = {"index": rec, "coefficients": js}
                    if a.summary:
                        try:
                            s = page.request.post(
                                BASE + "/Compressor/CSummary", form={"ModelID": r["id"], "Metric": "E"}
                            )
                            payload["summary_html"] = s.text()
                        except Exception:  # noqa: BLE001
                            pass
                    (out / f"{r['model']}__{r['id']}.json").write_text(json.dumps(payload, indent=1))
                    w.writerow(rec)
                    fh.flush()
                    done.add(r["id"])
                    time.sleep(0.4)
        finally:
            page.close()
    print(f"index: {index_path} ({len(done)} records)")


if __name__ == "__main__":
    main()
