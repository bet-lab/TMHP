"""Regression tests for copy-runnable documentation examples."""

import math
import re
import warnings
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
    code = _find_block(blocks, 'ashpb_r134a = AirSourceHeatPumpBoiler(ref="R134a")')
    # R744 is transcritical at DHW temperatures; the cycle does not support it (#96).
    assert "R744" not in code

    namespace: dict[str, object] = {}
    exec(code, namespace)

    assert namespace["ashpb_r290"].ref == "R290"
    assert namespace["ashpb_r410"].ref == "R410A"
    assert namespace["ashpb_r134a"].ref == "R134a"


def test_quickstart_refrigerant_swap_example_is_copy_runnable() -> None:
    path = REPO_ROOT / "docs/source/getting-started/quickstart.rst"
    code = _find_block(
        _rst_python_blocks(path),
        'AirSourceHeatPumpBoiler(ref="R290")',
    )
    assert "R744" not in code

    exec(code, {})


def test_coolprop_refrigerant_list_example_is_copy_runnable() -> None:
    path = REPO_ROOT / "docs/source/concepts/refrigerant-and-coolprop.rst"
    code = _find_block(
        _rst_python_blocks(path),
        'AirSourceHeatPumpBoiler(ref="R600a"',
    )

    exec(code, {})


def test_minimal_input_examples_run_and_match_the_page() -> None:
    """Capacity, refrigerant and family are enough; the page's numbers are the model's."""
    path = REPO_ROOT / "docs/source/getting-started/minimal-input.rst"
    blocks = _rst_python_blocks(path)
    text = path.read_text(encoding="utf-8")

    five = _find_block(blocks, "wshpb = WaterSourceHeatPumpBoiler(hp_capacity=9_000")
    namespace: dict[str, object] = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        exec(five, namespace)
    cops = []
    for name in ("a2a", "g2a", "a2w", "g2w", "w2w"):
        row = namespace[name]
        assert row["failure_reason"] == "none" and row["hp_is_on"] and row["capacity_clamped"] is None, name
        cops.append(row["cop_sys [-]"])
    quoted = "COP of " + ", ".join(f"{cop:.2f}" for cop in cops[:-1]) + f" and {cops[-1]:.2f}"
    assert quoted in " ".join(text.split()), quoted

    # "What follows the refrigerant": the span of rated speeds of each default machine over seven fluids.
    from tmhp import (
        AirSourceHeatPump,
        AirSourceHeatPumpBoiler,
        GroundSourceHeatPump,
        GroundSourceHeatPumpBoiler,
        WaterSourceHeatPumpBoiler,
    )

    fluids = ("R32", "R290", "R410A", "R134a", "R600a", "R1234yf", "R1234ze(E)")
    spans = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for cls in (
            GroundSourceHeatPump,
            AirSourceHeatPump,
            WaterSourceHeatPumpBoiler,
            AirSourceHeatPumpBoiler,
            GroundSourceHeatPumpBoiler,
        ):
            speeds = [cls(ref=ref).rps_rated for ref in fluids]
            spans.append(f"{min(speeds):.0f}–{max(speeds):.0f}")
    quoted = (
        f"{spans[0]} rev/s for the default GSHP, {spans[1]} ASHP, {spans[2]} WSHPB, {spans[3]} ASHPB and "
        f"{spans[4]} GSHPB"
    )
    assert quoted in " ".join(text.split()), quoted

    sizing = _find_block(blocks, "gshp = GroundSourceHeatPump(hp_capacity=30_000)")
    namespace = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        exec(sizing, namespace)
    gshp = namespace["gshp"]
    assert (gshp.N_1, gshp.N_2, gshp.H_b) == (3, 2, 100.0)
    assert math.isclose(gshp.ground_flow_ref_lpm, 115.3, abs_tol=0.05)
    assert math.isclose(gshp.E_pmp, 640.0, abs_tol=0.5)
    # "5845 W/K (condensing; 4927 W/K evaporating)", the approach-temperature defaults.
    assert math.isclose(gshp.UA_ground_rated, 5845.0, abs_tol=0.5)
    assert math.isclose(gshp.UA_ground_evap_rated, 4927.0, abs_tol=0.5)


def test_result_keys_example_is_copy_runnable() -> None:
    path = REPO_ROOT / "docs/source/concepts/result-keys.rst"
    code = _find_block(_rst_python_blocks(path), "row = AirSourceHeatPump().analyze_steady(Q_r_iu=-9_000")

    namespace: dict[str, object] = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        exec(code, namespace)
    row = namespace["row"]
    assert row["failure_reason"] == "none"
    assert row["capacity_clamped"] == "max"
    assert row["capacity_binding"] == "compressor"
    # The comments quote the delivered and the unmet heat to 0.1 kW; they are the model's.
    for key, words in (("Q_ref_iu [W]", "heat delivered"), ("Q_unmet [W]", "the rest of the request")):
        quoted = re.search(rf"# {words}, about (\d+\.\d) kW", code)
        assert quoted, words
        assert quoted.group(1) == f"{row[key] / 1000.0:.1f}", (key, quoted.group(1), row[key])
    assert math.isclose(row["Q_ref_iu [W]"] + row["Q_unmet [W]"], 9000.0, rel_tol=1e-9)
