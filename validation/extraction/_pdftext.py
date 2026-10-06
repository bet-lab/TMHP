"""Layout-preserving text access to the archived evidence PDFs.

The evidence tree is untracked (publisher PDFs are not redistributable), so
every parser here starts by checking the file is present and matches the
checksum recorded in ``validation/registry/sources.yaml``. A reader who obtains
the same document from the manufacturer gets the same bytes and therefore the
same numbers; a reader without it gets a clear message rather than a silent
empty result.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = REPO_ROOT / "validation" / "evidence"


class EvidenceMissing(FileNotFoundError):
    """Raised when an evidence document is not present locally."""


def evidence_path(relative: str) -> Path:
    path = EVIDENCE / relative
    if not path.exists():
        raise EvidenceMissing(
            f"{relative} not found under validation/evidence/.\n"
            "The evidence tree is deliberately untracked. See "
            "validation/registry/sources.yaml for the document identity and "
            "where to obtain it."
        )
    return path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def layout_text(relative: str, first: int | None = None, last: int | None = None) -> str:
    """``pdftotext -layout`` output for an evidence document."""
    path = evidence_path(relative)
    cmd = ["pdftotext", "-layout"]
    if first is not None:
        cmd += ["-f", str(first)]
    if last is not None:
        cmd += ["-l", str(last)]
    cmd += [str(path), "-"]
    return subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=120).stdout


def assembled_rows(relative: str, page_index: int, y_tol: float = 3.0) -> list[str]:
    """Words of one page regrouped into visual rows.

    ``pdftotext -layout`` breaks a row whenever a cell wraps, which silently
    splits a model name from its numbers. Grouping extracted words by their
    vertical position and sorting by horizontal position keeps each printed row
    intact, which is what a table scrape across six manufacturers needs.
    """
    import pdfplumber  # imported lazily: only the scrapers need it

    with pdfplumber.open(evidence_path(relative)) as pdf:
        page = pdf.pages[page_index]
        buckets: dict[int, list[dict]] = {}
        for word in page.extract_words(use_text_flow=False, keep_blank_chars=False):
            buckets.setdefault(round(word["top"] / y_tol), []).append(word)
    return [" ".join(w["text"] for w in sorted(ws, key=lambda x: x["x0"])) for _, ws in sorted(buckets.items())]


def number(token: str) -> float:
    """Parse a catalogue number written with either decimal separator."""
    cleaned = token.strip().replace("*", "").replace(" ", "")
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    else:
        cleaned = cleaned.replace(",", ".")
    return float(cleaned)


def text_lines(relative: str, page_index: int) -> list[str]:
    """``pdfplumber.extract_text`` lines of one page.

    Preferred over :func:`assembled_rows` for tables whose cells are already on
    a single baseline; the y-bucketing in ``assembled_rows`` can drop words when
    a table mixes font sizes, which GEA Searle's selection tables do.
    """
    import pdfplumber

    with pdfplumber.open(evidence_path(relative)) as pdf:
        return (pdf.pages[page_index].extract_text() or "").split("\n")
