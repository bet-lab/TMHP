"""Reading figures off a documentation page, for the tests that keep quoted numbers the model's.

A model page quotes numbers computed from the default machines in tables
(``list-table``) and in sentences. These helpers find them and compare them
with a value: a quoted figure passes if it is a rounding of the value -- half
a unit of its last digit, plus a tenth for the last digits of a solve that
differ between platforms.
"""

from __future__ import annotations

import re

#: A figure as the pages write it: a typographic or ASCII minus, thousands separators, decimals.
#: A whole one: not the leading digits of a longer number.
NUMBER = r"([−-]?\d[\d,]*(?:\.\d+)?)(?![\d]|[.,]\d)"


def flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def value(cell: str) -> float:
    """A figure as the pages write it: a typographic minus, thousands separators."""
    return float(cell.replace("−", "-").replace(",", ""))


def decimals(cell: str) -> int:
    return len(cell.split(".")[1]) if "." in cell else 0


def quotes(cell: str, number: float, what: str) -> None:
    """``cell`` is ``number`` rounded to the digits it shows (0.6 of the last digit: see the module docstring)."""
    step = 10.0 ** -decimals(cell)
    assert abs(value(cell) - number) <= 0.6 * step, f"{what}: the page says {cell}, the model gives {number:.6g}"


def table(page: str, marker: str) -> dict[str, list[str]]:
    """Rows of the ``list-table`` titled ``marker``, or the first one after it: first cell -> the other cells.

    The first row is the header. A label that appears twice, or a row with
    another number of cells than the header, is an error: a second row under
    the same label would otherwise replace the first unseen.
    """
    start = page.index(marker)
    line_start = page.rfind("\n", 0, start) + 1
    titled = page.startswith(".. list-table::", line_start)
    lines = page[line_start if titled else page.index(".. list-table::", start) :].splitlines()[1:]
    rows: dict[str, list[str]] = {}
    label: str | None = None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("* - "):
            label = stripped[4:]
            assert label not in rows, f"{marker}: two rows labelled {label!r}"
            rows[label] = []
        elif stripped.startswith("- ") and label is not None:
            rows[label].append(stripped[2:])
        elif stripped and not line.startswith(" "):
            break  # the text after the table
    assert rows, marker
    width = len(next(iter(rows.values())))
    uneven = {label: len(cells) for label, cells in rows.items() if len(cells) != width}
    assert not uneven, f"{marker}: rows with another number of cells than the header ({width}): {uneven}"
    return rows


def sentence(page: str, template: str) -> list[str]:
    """The figures at the ``{}`` of ``template`` in the page (whitespace-insensitive).

    The sentence has to be on the page exactly once: with two matches the
    figures of the wrong one could be the ones compared.
    """
    pattern = NUMBER.join(re.escape(part) for part in flat(template).split("{}"))
    found = re.findall(pattern, flat(page))
    assert found, f"sentence not on the page: {template!r}"
    assert len(found) == 1, f"sentence on the page {len(found)} times: {template!r}"
    groups = found[0]
    return [groups] if isinstance(groups, str) else list(groups)
