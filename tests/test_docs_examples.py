"""Regression tests for copy-runnable documentation examples."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _rst_python_blocks(path: Path) -> list[str]:
    """Extract Python code blocks from an RST document."""
    lines = path.read_text(encoding="utf-8").splitlines()
    blocks: list[str] = []
    index = 0

    while index < len(lines):
        if lines[index].strip() != ".. code-block:: python":
            index += 1
            continue

        index += 1
        while index < len(lines) and not lines[index].strip():
            index += 1

        block_lines: list[str] = []
        while index < len(lines):
            line = lines[index]
            if not line.startswith("   ") and line.strip():
                break
            block_lines.append(line[3:] if line.startswith("   ") else "")
            index += 1

        blocks.append("\n".join(block_lines).strip())

    return blocks


def _markdown_python_blocks(path: Path) -> list[str]:
    """Extract fenced Python code blocks from a Markdown document."""
    blocks: list[str] = []
    block_lines: list[str] = []
    in_python_block = False

    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() == "```python":
            in_python_block = True
            block_lines = []
            continue

        if in_python_block and line.strip() == "```":
            blocks.append("\n".join(block_lines).strip())
            in_python_block = False
            continue

        if in_python_block:
            block_lines.append(line)

    return blocks


def _find_block(blocks: list[str], marker: str) -> str:
    """Return the single documentation code block containing marker."""
    matches = [block for block in blocks if marker in block]
    assert len(matches) == 1
    return matches[0]


def test_readme_refrigerant_swap_example_is_copy_runnable() -> None:
    blocks = _markdown_python_blocks(REPO_ROOT / "README.md")
    code = _find_block(blocks, 'ashpb_r744 = AirSourceHeatPumpBoiler(ref="R744")')

    namespace: dict[str, object] = {}
    exec(code, namespace)

    assert namespace["ashpb_r290"].ref == "R290"
    assert namespace["ashpb_r744"].ref == "R744"
    assert namespace["ashpb_r410"].ref == "R410A"


def test_quickstart_refrigerant_swap_example_is_copy_runnable() -> None:
    path = REPO_ROOT / "docs/source/getting-started/quickstart.rst"
    code = _find_block(
        _rst_python_blocks(path),
        'AirSourceHeatPumpBoiler(ref="R744")',
    )

    exec(code, {})


def test_coolprop_refrigerant_list_example_is_copy_runnable() -> None:
    path = REPO_ROOT / "docs/source/concepts/refrigerant-and-coolprop.rst"
    code = _find_block(
        _rst_python_blocks(path),
        'AirSourceHeatPumpBoiler(ref="R600a")',
    )

    exec(code, {})
