"""Minimal Notion REST client and block builders (``NOTION_API_KEY`` from ``.env``).

The token is read once and never printed.  ``rt`` turns ``$...$`` spans into
inline equations so ``$P_r$``, ``$n^*$`` and ``$\\eta_v$`` render as maths.
"""

from __future__ import annotations

import json
import mimetypes
import os
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
API = "https://api.notion.com/v1"
VERSION = "2025-09-03"


def _token() -> str:
    env = os.environ.get("NOTION_API_KEY")
    if env:
        return env
    for parent in [HERE, *HERE.parents]:
        candidate = parent / ".env"
        if candidate.is_file():
            for line in candidate.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("NOTION_API_KEY"):
                    return line.partition("=")[2].strip().strip("'\"")
    raise SystemExit("NOTION_API_KEY not found (.env or environment)")


def call(
    method: str, path: str, body: dict | None = None, *, raw: bytes | None = None, ctype: str | None = None
) -> dict:
    headers = {"Authorization": f"Bearer {_token()}", "Notion-Version": VERSION}
    data = raw
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if ctype:
        headers["Content-Type"] = ctype
    req = urllib.request.Request(f"{API}{path}", data=data, method=method, headers=headers)
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            msg = exc.read().decode("utf-8", "replace")[:500]
            if exc.code in (409, 429, 500, 502, 503, 504):
                time.sleep(2.0 * (attempt + 1))
                continue
            raise SystemExit(f"{method} {path} failed: {exc.code} {msg}") from exc
    raise SystemExit(f"{method} {path} failed after retries")


def upload(path: Path) -> str:
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    created = call("POST", "/file_uploads", {"filename": path.name, "content_type": ctype})
    boundary = uuid.uuid4().hex
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'.encode(),
            f"Content-Type: {ctype}\r\n\r\n".encode(),
            path.read_bytes(),
            f"\r\n--{boundary}--\r\n".encode(),
        ]
    )
    sent = call(
        "POST", f"/file_uploads/{created['id']}/send", raw=body, ctype=f"multipart/form-data; boundary={boundary}"
    )
    if sent.get("status") != "uploaded":
        raise SystemExit(f"upload of {path.name} did not complete: {sent}")
    return created["id"]


def children_all(block_id: str) -> list[dict]:
    out: list[dict] = []
    cursor = None
    while True:
        suffix = f"&start_cursor={cursor}" if cursor else ""
        page = call("GET", f"/blocks/{block_id}/children?page_size=100{suffix}")
        out.extend(page.get("results", []))
        if not page.get("has_more"):
            return out
        cursor = page.get("next_cursor")


def append(page_id: str, blocks: list[dict]) -> None:
    for i in range(0, len(blocks), 90):
        call("PATCH", f"/blocks/{page_id}/children", {"children": blocks[i : i + 90]})


# ---------------------------------------------------------------------------
# block builders
# ---------------------------------------------------------------------------
def rt(text: str, *, code: bool = False, bold: bool = False) -> list[dict]:
    parts: list[dict] = []
    for i, chunk in enumerate(text.split("$")):
        if not chunk:
            continue
        if i % 2 == 1:
            parts.append({"type": "equation", "equation": {"expression": chunk}})
        else:
            parts.append(
                {"type": "text", "text": {"content": chunk[:1900]}, "annotations": {"code": code, "bold": bold}}
            )
    return parts


def h(level: int, text: str) -> dict:
    key = f"heading_{level}"
    return {"object": "block", "type": key, key: {"rich_text": rt(text)}}


def para(text: str) -> dict:
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": rt(text)}}


def bullet(text: str) -> dict:
    return {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": rt(text)}}


def equation(expr: str) -> dict:
    return {"object": "block", "type": "equation", "equation": {"expression": expr}}


def code(text: str, language: str = "plain text") -> dict:
    return {"object": "block", "type": "code", "code": {"rich_text": rt(text), "language": language}}


def callout(text: str, emoji: str = "ℹ️") -> dict:
    return {
        "object": "block",
        "type": "callout",
        "callout": {"rich_text": rt(text), "icon": {"type": "emoji", "emoji": emoji}},
    }


def table(header: list[str], rows: list[list[str]], *, code_cols: tuple[int, ...] = ()) -> dict:
    def row(cells: list[str], is_header: bool) -> dict:
        return {
            "object": "block",
            "type": "table_row",
            "table_row": {
                "cells": [
                    rt(str(c), code=(j in code_cols and not is_header), bold=is_header) for j, c in enumerate(cells)
                ]
            },
        }

    return {
        "object": "block",
        "type": "table",
        "table": {
            "table_width": len(header),
            "has_column_header": True,
            "has_row_header": False,
            "children": [row(header, True)] + [row(r, False) for r in rows],
        },
    }


def image(path: Path, caption: str) -> dict:
    fid = upload(path)
    return {
        "object": "block",
        "type": "image",
        "image": {"type": "file_upload", "file_upload": {"id": fid}, "caption": rt(caption)},
    }


def divider() -> dict:
    return {"object": "block", "type": "divider", "divider": {}}
