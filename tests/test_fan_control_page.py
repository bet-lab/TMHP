"""The fan-flow section of the air-to-air model page states what the least-power search is worth.

The model has no fan controller: it runs each fan at the flow of least total
power. The page (``docs/source/models/ashp.rst``, "Fan flows") and the scope
table quote the COP with the fans held instead; these tests keep those
figures the model's.
"""

from __future__ import annotations

import re
import warnings
from pathlib import Path

import pytest

from tmhp import AirSourceHeatPump

DOCS = Path(__file__).resolve().parents[1] / "docs" / "source"
#: (label on the page, load [W] (cooling positive), outdoor [degC], room [degC])
CASES = (
    ("Cooling 2 kW", 2000.0, 35.0, 27.0),
    ("Heating 2 kW", -2000.0, 7.0, 20.0),
    ("Cooling 4 kW", 4000.0, 35.0, 27.0),
    ("Heating 4 kW", -4000.0, 7.0, 20.0),
)


HALF = "Indoor fan held at 50 %"
DELIVERED = "delivered at nameplate request, indoor fan at 50 % [kW]"


def _build(**kwargs) -> AirSourceHeatPump:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return AirSourceHeatPump(**kwargs)


@pytest.fixture(scope="module")
def cop() -> dict[str, list[float]]:
    base = _build()
    indoor, outdoor = base.dV_iu_fan_a_max, base.dV_ou_fan_a_max
    models = {
        "Least power (the model)": base,
        "Indoor fan held at 70 % of its largest flow": _build(
            dV_iu_fan_a_min=0.7 * indoor, dV_iu_fan_a_max=0.7 * indoor
        ),
        "Indoor fan held at 50 %": _build(dV_iu_fan_a_min=0.5 * indoor, dV_iu_fan_a_max=0.5 * indoor),
        "Both fans held at their largest flow": _build(
            dV_iu_fan_a_min=indoor, dV_iu_fan_a_max=indoor, dV_ou_fan_a_min=outdoor, dV_ou_fan_a_max=outdoor
        ),
    }
    table: dict[str, list[float]] = {}
    for name, model in models.items():
        rows = [
            model.analyze_steady(Q_r_iu=load, T0=T0, T_a_room=room, return_dict=True, verbose=False)
            for _, load, T0, room in CASES
        ]
        assert all(row["failure_reason"] == "none" for row in rows)
        if name == HALF:
            # Half the indoor flow does not carry the nameplate: the page says what it delivers instead.
            assert [row["Q_unmet [W]"] > 0.0 for row in rows] == [False, False, True, True]
            table[DELIVERED] = [abs(float(row["Q_ref_iu [W]"])) / 1000.0 for row in rows[2:]]
        else:
            assert all(row["Q_unmet [W]"] == 0.0 for row in rows), name
        table[name] = [float(row["cop_sys [-]"]) for row in rows]
    return table


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def test_the_table_is_what_the_model_gives(cop) -> None:
    page = (DOCS / "models" / "ashp.rst").read_text()
    header = "".join(f"     - {label}\n" for label, *_ in CASES)
    assert "   * - Fan flows\n" + header in page
    delivered = cop.pop(DELIVERED)
    for name, values in cop.items():
        row = f"   * - {name}\n" + "".join(f"     - {value:.2f}\n" for value in values)
        assert row in page, f"ashp.rst, 'System COP by fan flow': {name} should read {[f'{v:.2f}' for v in values]}"
    assert (
        f"its coil takes {delivered[0]:.2f} kW in cooling and gives {delivered[1]:.2f} kW in heating (refrigerant side)"
    ) in _flat(page)
    short = [round((1.0 - value / 4.0) * 100.0) for value in delivered]
    scope = _flat((DOCS / "concepts" / "scope-and-limits.rst").read_text())
    assert f"which the unit then falls {min(short)} to {max(short)} % short of" in scope
    cop[DELIVERED] = delivered


def test_the_search_runs_the_indoor_fan_flat_out_and_the_losses_quoted_are_the_models(cop) -> None:
    base = _build()
    outdoor = {}
    for label, load, T0, room in CASES:
        row = base.analyze_steady(Q_r_iu=load, T0=T0, T_a_room=room, return_dict=True, verbose=False)
        assert row["dV_iu_a [m3/s]"] == pytest.approx(base.dV_iu_fan_a_max, rel=1e-4)
        outdoor[label] = float(row["dV_ou_a [m3/s]"])
    # 'the search slows it with the load ... (0.61 to 0.47 m3/s from nameplate to half load in cooling)'
    assert outdoor["Cooling 2 kW"] < outdoor["Cooling 4 kW"] and outdoor["Heating 2 kW"] < outdoor["Heating 4 kW"]
    assert (
        f"({outdoor['Cooling 4 kW']:.2f} to {outdoor['Cooling 2 kW']:.2f} m³/s from nameplate to half load in cooling)"
    ) in _flat((DOCS / "models" / "ashp.rst").read_text())
    assert f"{base.dV_iu_fan_a_max:.3f} m³/s" in (DOCS / "models" / "ashp.rst").read_text()
    least, half, full = (cop[k] for k in ("Least power (the model)", HALF, "Both fans held at their largest flow"))
    lower_half = [round((1.0 - b / a) * 100.0) for a, b in zip(least, half, strict=True)]
    lower_full = [round((1.0 - b / a) * 100.0) for a, b in zip(least, full, strict=True)]
    page = _flat((DOCS / "models" / "ashp.rst").read_text())
    assert (
        f"the COP is {lower_half[0]} % and {lower_half[1]} % lower at half load and "
        f"{lower_half[2]} % and {lower_half[3]} % lower at nameplate load"
    ) in page
    assert f"costs {lower_full[0]} % and {lower_full[1]} % at half load" in page
    # ... "and under 0.3 % at nameplate load" (0.27 % in heating; in cooling the search is at full flow itself).
    assert "and under 0.3 % at nameplate load" in page
    assert all(abs(1.0 - b / a) < 0.003 for a, b in zip(least[2:], full[2:], strict=True))
    scope = _flat((DOCS / "concepts" / "scope-and-limits.rst").read_text())
    assert (
        f"the COP is {min(lower_half[:2])} to {max(lower_half[:2])} % lower at half load and "
        f"{min(lower_half[2:])} to {max(lower_half[2:])} % lower at nameplate load"
    ) in scope


#: Tolerance of the operating-point search on the total input [W] (``fatol`` of its refinement).
SEARCH_TOLERANCE_W = 0.1


def test_holding_a_fan_does_not_beat_the_search_beyond_its_tolerance() -> None:
    """'The search stops within 0.1 W of that input.' At the same coil duty a held fan never draws less than that."""
    assert "The search stops within 0.1 W of that input" in _flat((DOCS / "models" / "ashp.rst").read_text())
    base = _build()
    indoor, outdoor = base.dV_iu_fan_a_max, base.dV_ou_fan_a_max
    held = {
        "indoor 70 %": _build(dV_iu_fan_a_min=0.7 * indoor, dV_iu_fan_a_max=0.7 * indoor),
        "indoor 50 %": _build(dV_iu_fan_a_min=0.5 * indoor, dV_iu_fan_a_max=0.5 * indoor),
        "outdoor largest": _build(dV_ou_fan_a_min=outdoor, dV_ou_fan_a_max=outdoor),
        "outdoor 60 %": _build(dV_ou_fan_a_min=0.6 * outdoor, dV_ou_fan_a_max=0.6 * outdoor),
        "both largest": _build(
            dV_iu_fan_a_min=indoor, dV_iu_fan_a_max=indoor, dV_ou_fan_a_min=outdoor, dV_ou_fan_a_max=outdoor
        ),
    }
    compared = 0
    for _, load, T0, room in CASES:
        searched = base.analyze_steady(Q_r_iu=load, T0=T0, T_a_room=room, return_dict=True, verbose=False)
        assert searched["Q_unmet [W]"] == 0.0
        for name, model in held.items():
            row = model.analyze_steady(Q_r_iu=load, T0=T0, T_a_room=room, return_dict=True, verbose=False)
            if row["failure_reason"] != "none" or row["Q_unmet [W]"] > 0.0:
                continue  # a held fan that does not carry the duty is not a cheaper way to carry it
            assert row["Q_ref_iu [W]"] == pytest.approx(searched["Q_ref_iu [W]"], rel=1e-9)
            assert row["E_tot [W]"] >= searched["E_tot [W]"] - SEARCH_TOLERANCE_W, (name, load)
            compared += 1
    assert compared >= 16  # every held setting but half the indoor flow at nameplate load
