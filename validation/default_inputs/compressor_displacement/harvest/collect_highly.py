"""Archive official Highly web tables through Chrome; snapshots are not original PDFs."""

import asyncio
import base64
import hashlib
import json
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "validation/evidence/default_inputs/expansion"
HERE = Path(__file__).parent


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    results = []
    async with async_playwright() as p:
        b = await p.chromium.connect_over_cdp("http://127.0.0.1:19122")
        for family in ["a", "d", "g", "l", "h", "th"]:
            url = f"https://www.highly.co.in/{family}-compressor-series.html"
            page = await b.contexts[0].new_page()
            try:
                response = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(500)
                html = (await page.content()).encode()
                tables = await page.locator("table").evaluate_all(
                    "(els)=>els.map(t=>Array.from(t.rows).map(r=>Array.from(r.cells).map(c=>c.innerText.trim())))"
                )
                html_path = OUT / f"HIGHLY-{family.upper()}-20261025.html"
                html_path.write_bytes(html)
                cdp = await page.context.new_cdp_session(page)
                pdf = await cdp.send(
                    "Page.printToPDF",
                    {
                        "printBackground": True,
                        "paperWidth": 8.27,
                        "paperHeight": 11.69,
                        "marginTop": 0.4,
                        "marginBottom": 0.4,
                        "marginLeft": 0.25,
                        "marginRight": 0.25,
                    },
                )
                pdf_path = OUT / f"HIGHLY-{family.upper()}-20261025-web-snapshot.pdf"
                pdf_path.write_bytes(base64.b64decode(pdf["data"]))
                body = await page.locator("body").inner_text()
                result = {
                    "source_id": f"HIGHLY-WEB-{family.upper()}-20261025",
                    "manufacturer": "Highly",
                    "url": url,
                    "accessed_at": datetime.now().astimezone().isoformat(),
                    "access_method": "chrome_cdp_dom_and_Page.printToPDF",
                    "evidence_type": "manufacturer_web_page_snapshot",
                    "html_path": str(html_path.relative_to(ROOT)),
                    "html_sha256": hashlib.sha256(html).hexdigest(),
                    "pdf_path": str(pdf_path.relative_to(ROOT)),
                    "pdf_sha256": hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
                    "http_status": response.status,
                    "body_text": body,
                    "tables": tables,
                }
                results.append(result)
                print(family, response.status, len(html), len(tables), flush=True)
            except Exception as e:
                results.append({"source_id": f"HIGHLY-WEB-{family.upper()}-20261025", "url": url, "error": str(e)})
                print(family, str(e), flush=True)
            finally:
                await page.close()
    (HERE / "highly_web_tables.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")


asyncio.run(main())
