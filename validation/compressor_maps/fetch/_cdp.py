"""Minimal CDP bridge client for harvesting compressor performance documents.

The user's real Chrome is reachable over an SSH reverse tunnel (default
``http://127.0.0.1:9233``); we attach with Playwright's ``connect_over_cdp``,
open our own tab, and never touch the user's tabs.  Three operations:

    text   URL            -> page innerText (after settle) to stdout / --out
    links  URL [--match]  -> absolute hrefs (optionally regex-filtered), one per line
    pdf    URL --out F    -> bytes fetched *inside the page* (same-origin cookies)
                             and written here; refuses to keep non-PDF payloads
    search QUERY          -> DuckDuckGo html result links (title \t url)

Run with ``uv run --with playwright python3 -m validation.compressor_maps.fetch._cdp ...``.
The pattern mirrors browser-agent-playbook/skills/publisher-pdf-access/scripts/fetch_cdp.py.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote_plus, urljoin

try:
    from playwright.sync_api import TimeoutError as PWTimeoutError
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    print("playwright missing: run with  uv run --with playwright python3 ...", file=sys.stderr)
    raise

DEFAULT_CDP = "http://127.0.0.1:9233"

JS_FETCH = """async (url) => {
  const r = await fetch(url, {credentials: 'include'});
  if (!r.ok) return {status: r.status, b64: null};
  const buf = await r.arrayBuffer();
  const b = new Uint8Array(buf);
  let s = ''; const CH = 0x8000;
  for (let i = 0; i < b.length; i += CH) s += String.fromCharCode.apply(null, b.subarray(i, i + CH));
  return {status: r.status, type: r.headers.get('content-type'), b64: btoa(s)};
}"""


def connect(p, cdp_url: str, attempts: int = 3):
    last = None
    for _ in range(attempts):
        try:
            return p.chromium.connect_over_cdp(cdp_url, timeout=15000)
        except Exception as e:  # noqa: BLE001
            last = e
            if "setDownloadBehavior" in str(e) or "context management" in str(e):
                time.sleep(2)
                continue
            raise
    raise RuntimeError(f"CDP connect failed at {cdp_url}: {last}")


def _goto(page, url: str, settle_s: float) -> None:
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    with contextlib.suppress(PWTimeoutError):
        page.wait_for_load_state("networkidle", timeout=12000)
    page.wait_for_timeout(int(settle_s * 1000))


def op_text(page, url: str, settle_s: float) -> str:
    _goto(page, url, settle_s)
    title = page.title()
    body = page.evaluate("() => document.body ? document.body.innerText : ''")
    return f"# {title}\n# {page.url}\n\n{body}"


def op_links(page, url: str, settle_s: float, match: str | None) -> list[str]:
    _goto(page, url, settle_s)
    hrefs = page.evaluate(
        "() => Array.from(document.querySelectorAll('a[href]')).map(a => [a.innerText.trim().slice(0,120), a.href])"
    )
    out = []
    rx = re.compile(match, re.I) if match else None
    for text, href in hrefs:
        absu = urljoin(page.url, href)
        if rx and not (rx.search(absu) or rx.search(text)):
            continue
        out.append(f"{text}\t{absu}")
    return out


def op_pdf(page, url: str, out: Path, settle_s: float, referer: str | None) -> str:
    # Fetching directly from a blank page works for public PDFs; for publisher
    # pages we first navigate (to collect cookies) and then fetch the same URL.
    if referer:
        _goto(page, referer, settle_s)
    else:
        with contextlib.suppress(Exception):
            _goto(page, url, settle_s)
    r = page.evaluate(JS_FETCH, url)
    if not r or not r.get("b64"):
        return f"http_{r.get('status') if r else '?'}"
    data = base64.b64decode(r["b64"])
    if data[:4] != b"%PDF":
        return f"not_pdf(type={r.get('type')},{len(data)}B)"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return f"ok {len(data)}B -> {out}"


def op_search(page, query: str, settle_s: float, n: int) -> list[str]:
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    _goto(page, url, settle_s)
    rows = page.evaluate(
        "() => Array.from(document.querySelectorAll('a.result__a')).map(a => [a.innerText.trim(), a.href])"
    )
    out = []
    for text, href in rows[:n]:
        m = re.search(r"uddg=([^&]+)", href)
        real = m.group(1) if m else href
        from urllib.parse import unquote

        out.append(f"{text}\t{unquote(real)}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("op", choices=["text", "links", "pdf", "search"])
    ap.add_argument("target")
    ap.add_argument("--cdp-url", default=DEFAULT_CDP)
    ap.add_argument("--out")
    ap.add_argument("--match")
    ap.add_argument("--referer")
    ap.add_argument("--settle", type=float, default=2.5)
    ap.add_argument("-n", type=int, default=15)
    a = ap.parse_args()

    with sync_playwright() as p:
        browser = connect(p, a.cdp_url)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        try:
            if a.op == "text":
                s = op_text(page, a.target, a.settle)
                if a.out:
                    Path(a.out).write_text(s, encoding="utf-8")
                    print(f"ok {len(s)} chars -> {a.out}")
                else:
                    print(s)
            elif a.op == "links":
                print("\n".join(op_links(page, a.target, a.settle, a.match)))
            elif a.op == "pdf":
                if not a.out:
                    sys.exit("--out required")
                print(op_pdf(page, a.target, Path(a.out), a.settle, a.referer))
            elif a.op == "search":
                print("\n".join(op_search(page, a.target, a.settle, a.n)))
        finally:
            page.close()


if __name__ == "__main__":
    main()
