"""``operating_range()``: standard conditions solved and tabulated (``tmhp.standard_conditions``).

The contract, not the outcome at any one condition: every family returns the
same columns, each row is what a direct ``analyze_steady`` call with the
row's inputs returns, a point that does not run says so with its
``failure_reason`` and the limit in the words of the warnings, and the call
neither warns nor changes the model. Which conditions a default machine runs
at is deliberately not asserted here: it moves with the on/off and envelope
rules, and the gate that asserts it sits on top of this method.
"""

from __future__ import annotations

import contextlib
import copy
import io
import math
import warnings
from collections.abc import Callable, Mapping
from typing import Any

import pandas as pd
import pytest

import tmhp
from tmhp import OperatingLimitWarning, StandardCondition
from tmhp.operating_limits import reset_notices
from tmhp.standard_conditions import (
    AIR_TO_AIR,
    AIR_TO_WATER,
    BRINE_TO_AIR,
    BRINE_TO_WATER,
    COLUMNS,
    LEAVING_WATER_TO_TANK_K,
    WATER_TO_WATER,
)


def _quiet(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    with (
        warnings.catch_warnings(),
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        warnings.simplefilter("ignore")
        return fn(*args, **kwargs)


def _notices(fn: Callable[[], Any], action: str) -> tuple[Any, list[warnings.WarningMessage]]:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter(action)
        result = fn()
    return result, [w for w in caught if issubclass(w.category, OperatingLimitWarning)]


# family -> (constructor, default conditions, delivered key)
FAMILIES: dict[str, tuple[Callable[[], Any], tuple[StandardCondition, ...], str]] = {
    "ASHPB": (lambda: tmhp.AirSourceHeatPumpBoiler(ref="R32"), AIR_TO_WATER, "Q_ref_tank [W]"),
    "GSHPB": (tmhp.GroundSourceHeatPumpBoiler, BRINE_TO_WATER, "Q_ref_tank [W]"),
    "WSHPB": (tmhp.WaterSourceHeatPumpBoiler, WATER_TO_WATER, "Q_ref_tank [W]"),
    "ASHP": (tmhp.AirSourceHeatPump, AIR_TO_AIR, "Q_ref_iu [W]"),
    "GSHP": (tmhp.GroundSourceHeatPump, BRINE_TO_AIR, "Q_ref_iu [W]"),
}


@pytest.fixture(scope="module")
def tables() -> dict[str, tuple[Any, pd.DataFrame]]:
    """Each family's default machine and its default table, solved once."""
    out = {}
    for name, (build, _, _) in FAMILIES.items():
        model = _quiet(build)
        out[name] = (model, model.operating_range())
    return out


def _direct(model: Any, row: Mapping[str, Any]) -> dict:
    """The row a caller gets from ``analyze_steady`` with the table row's inputs."""
    target = model
    if isinstance(model, tmhp.GroundSourceHeatPump):  # the documented copy with the brine at the condition
        target = copy.copy(model)
        target.T_bhe_f_out = row["T_source_side [°C]"]
        target.T_bhe_f_out_K = row["T_source_side [°C]"] + 273.15
    return _quiet(target.analyze_steady, **row["inputs"])


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
        return True
    return bool(a == b)


def _label(value: Any) -> Any:
    return None if value is None or (isinstance(value, float) and math.isnan(value)) else value


# ---------------------------------------------------------------------------
# 1. one table shape for every family
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("family", list(FAMILIES))
def test_every_family_returns_the_same_columns_one_row_per_condition(tables, family):
    _, conditions, _ = FAMILIES[family]
    model, table = tables[family]
    assert isinstance(table, pd.DataFrame)
    assert tuple(table.columns) == COLUMNS
    assert list(table["condition"]) == [c.name for c in conditions]
    assert table["runs"].map(type).eq(bool).all() and table["meets_request"].map(type).eq(bool).all()
    # The request is the nameplate duty, signed as the model signs it.
    for condition, request in zip(conditions, table["Q_request [W]"], strict=True):
        sign = -1.0 if condition.mode == "heating" and family in ("ASHP", "GSHP") else 1.0
        assert request == pytest.approx(sign * model.hp_capacity)
    # A label that is absent is None in every row (not NaN in some).
    for column in ("capacity_clamped", "capacity_binding", "failure_detail", "limit"):
        assert all(value is None or isinstance(value, str) for value in table[column])


def test_the_method_is_on_every_family_and_its_variants():
    for cls in (
        tmhp.AirSourceHeatPumpBoiler,
        tmhp.GroundSourceHeatPumpBoiler,
        tmhp.WaterSourceHeatPumpBoiler,
        tmhp.AirSourceHeatPump,
        tmhp.GroundSourceHeatPump,
        tmhp.ASHPB_PV_ESS,
        tmhp.GSHPB_STC_tank,
    ):
        assert callable(cls.operating_range)
    assert tmhp.StandardCondition is StandardCondition and "StandardCondition" in tmhp.__all__


# ---------------------------------------------------------------------------
# 2. each row is the direct analyze_steady call
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("family", list(FAMILIES))
def test_each_row_is_the_direct_steady_call_with_its_inputs(tables, family):
    _, _, delivered_key = FAMILIES[family]
    model, table = tables[family]
    for _, row in table.iterrows():
        direct = _direct(model, row)
        reason = direct["failure_reason"]
        assert row["failure_reason"] == reason
        assert row["runs"] == (bool(direct["hp_is_on"]) and reason == "none")
        assert _same(row["Q_request [W]"], float(direct["Q_request [W]"]))
        assert _same(row["Q_delivered [W]"], float(direct[delivered_key]))
        for key in ("Q_max [W]", "Q_min [W]", "runtime_fraction [-]", "cop_sys [-]"):
            assert _same(row[key], float(direct.get(key, math.nan))), (row["condition"], key)
        for key in ("capacity_clamped", "capacity_binding", "failure_detail"):
            assert row[key] == _label(direct.get(key)), (row["condition"], key)
        shortfall = abs(row["Q_delivered [W]"]) < abs(row["Q_request [W]"]) * (1.0 - 1e-3)
        assert row["meets_request"] == (row["runs"] and not shortfall)


def test_boiler_inputs_name_the_tank_temperature_below_the_leaving_water(tables):
    _, table = tables["ASHPB"]
    for _, row in table.iterrows():
        assert row["T_tank_w [°C]"] == pytest.approx(row["T_load_side [°C]"] - LEAVING_WATER_TO_TANK_K)
        assert row["inputs"]["T_tank_w"] == row["T_tank_w [°C]"]
        assert row["inputs"]["T0"] == row["T_source_side [°C]"]
    # A tank solved at the named temperature: sink_offset_K=0.
    model, _ = tables["ASHPB"]
    named = model.operating_range([AIR_TO_WATER[0]], sink_offset_K=0.0)
    assert named.loc[0, "T_tank_w [°C]"] == AIR_TO_WATER[0].load_side_C
    # The space heat pumps have no tank.
    _, space = tables["ASHP"]
    assert space["T_tank_w [°C]"].isna().all()


def test_the_standard_wet_bulb_becomes_the_outdoor_humidity(tables):
    _, table = tables["ASHPB"]
    a7 = table.set_index("condition").loc["A7/W35"]
    assert a7["RH_out [-]"] == pytest.approx(0.868, abs=0.002)  # 7 °C dry bulb, 6 °C wet bulb
    assert a7["inputs"]["RH_out"] == a7["RH_out [-]"]
    no_wet_bulb = table.set_index("condition").loc["A-15/W55"]
    assert math.isnan(no_wet_bulb["RH_out [-]"]) and "RH_out" not in no_wet_bulb["inputs"]
    _, space = tables["ASHP"]
    cooling = space.set_index("condition").loc["A35/27"]
    assert cooling["mode"] == "cooling" and "RH_out" not in cooling["inputs"]


def test_part_load_and_own_conditions():
    model = _quiet(tmhp.WaterSourceHeatPumpBoiler)
    mine = StandardCondition("W12/W40", "heating", 12.0, 40.0, basis="a user's point")
    table = model.operating_range([mine, WATER_TO_WATER[0].at_part_load(0.25)])
    assert list(table["condition"]) == ["W12/W40", "W10/W35"]
    assert list(table["part_load [-]"]) == [1.0, 0.25]
    assert table.loc[1, "Q_request [W]"] == pytest.approx(0.25 * model.hp_capacity)
    for _, row in table.iterrows():
        direct = _direct(model, row)
        assert _same(row["cop_sys [-]"], float(direct["cop_sys [-]"]))
        assert _same(row["runtime_fraction [-]"], float(direct["runtime_fraction [-]"]))


# ---------------------------------------------------------------------------
# 3. a point that does not run
# ---------------------------------------------------------------------------

# A discharge limit no duty or speed meets at this lift, and a source liquid
# below its operating limit: points that stay refused whatever the duty rules.
REFUSED = [
    (
        lambda: tmhp.AirSourceHeatPumpBoiler(ref="R32", T_dis_max=100.0),
        StandardCondition("A-15/W55", "heating", -15.0, 55.0),
        "discharge_temp_above_max",
        "T_dis_max = 100 °C",
    ),
    (
        lambda: tmhp.GroundSourceHeatPumpBoiler(source_fluid="water"),
        StandardCondition("B-2/W52.5", "heating", -2.0, 52.5),
        "source_fluid_freezing",
        "for water",
    ),
]


@pytest.mark.parametrize(("build", "condition", "reason", "number"), REFUSED)
def test_a_point_that_does_not_run_is_named_with_its_limit(build, condition, reason, number):
    model = _quiet(build)
    row = model.operating_range([condition]).iloc[0]
    assert not row["runs"] and not row["meets_request"]
    assert row["failure_reason"] == reason
    assert row["Q_delivered [W]"] == 0.0 and math.isnan(row["cop_sys [-]"])
    assert f"failure_reason={reason!r}" in row["limit"] and number in row["limit"]
    # The limit is the warning a direct call gives, word for word.
    fresh = _quiet(build)
    _, caught = _notices(lambda: fresh.analyze_steady(**row["inputs"]), "always")
    assert [str(w.message) for w in caught] == [row["limit"]]


def test_the_default_r32_boiler_at_a7_w55_tank_55_is_reported_as_the_direct_call():
    # The point the 6th-round gate is about. Its outcome is not fixed here (the
    # on/off rule for refused continuous duties is about to change it); only
    # that the table reports exactly what analyze_steady does.
    model = _quiet(tmhp.AirSourceHeatPumpBoiler, ref="R32")
    a7_w55 = next(c for c in AIR_TO_WATER if c.name == "A-7/W55")
    for offset in (0.0, LEAVING_WATER_TO_TANK_K):
        row = model.operating_range([a7_w55], sink_offset_K=offset).iloc[0]
        expected = {"T_tank_w": 55.0 - offset, "T0": -7.0, "Q_ref_tank": model.hp_capacity, "RH_out": row["RH_out [-]"]}
        assert row["inputs"] == expected
        direct = _direct(model, row)
        assert row["failure_reason"] == direct["failure_reason"]
        assert row["runs"] == (bool(direct["hp_is_on"]) and direct["failure_reason"] == "none")


# ---------------------------------------------------------------------------
# 4. no warning, no change to the model
# ---------------------------------------------------------------------------


def _refusing_boiler() -> Any:
    return _quiet(tmhp.AirSourceHeatPumpBoiler, ref="R32", T_dis_max=100.0)


def test_no_operating_limit_warning_even_under_the_error_filter():
    model = _refusing_boiler()
    table, caught = _notices(model.operating_range, "error")
    assert caught == [] and (~table["runs"]).any()


def test_the_once_per_model_notice_is_still_due_after_the_table():
    point = {"T_tank_w": 52.5, "T0": -15.0, "Q_ref_tank": 15000.0}
    model = _refusing_boiler()
    reference = _refusing_boiler()
    _notices(model.operating_range, "default")
    _, after = _notices(lambda: model.analyze_steady(**point), "default")
    _, untouched = _notices(lambda: reference.analyze_steady(**point), "default")
    assert len(after) == 1 and [str(w.message) for w in after] == [str(w.message) for w in untouched]


@pytest.mark.parametrize("family", list(FAMILIES))
def test_steady_results_are_the_same_before_and_after_the_table(family):
    build, _, _ = FAMILIES[family]
    model = _quiet(build)
    probe = model.operating_range()  # the inputs to probe with
    before = [_direct(model, row) for _, row in probe.iterrows()]
    attributes = set(vars(model))
    notified = copy.copy(vars(model).get("_operating_limit_notified"))
    source = getattr(model, "T_bhe_f_out", None)
    _quiet(model.operating_range)
    after = [_direct(model, row) for _, row in probe.iterrows()]
    for first, second in zip(before, after, strict=True):
        assert first.keys() == second.keys()
        for key, value in first.items():
            other = second[key]
            if isinstance(value, float) or isinstance(other, float):
                assert _same(float(value), float(other)), key
            elif not isinstance(value, (dict, list, pd.DataFrame)):
                assert value == other, key
    assert set(vars(model)) == attributes
    assert vars(model).get("_operating_limit_notified") == notified
    assert getattr(model, "T_bhe_f_out", None) == source  # GSHP: solved on a copy


def test_reset_notices_is_not_needed_after_the_table():
    model = _refusing_boiler()
    _quiet(model.operating_range)
    reset_notices(model)  # a no-op here: the table recorded nothing
    assert "_operating_limit_notified" not in vars(model)


# ---------------------------------------------------------------------------
# 5. arguments
# ---------------------------------------------------------------------------


def test_bad_arguments_are_refused():
    boiler = _quiet(tmhp.WaterSourceHeatPumpBoiler)
    with pytest.raises(ValueError, match="sink_offset_K"):
        boiler.operating_range(sink_offset_K=-1.0)
    with pytest.raises(ValueError, match="heats water only"):
        boiler.operating_range([AIR_TO_AIR[-1]])
    with pytest.raises(TypeError, match="StandardCondition"):
        boiler.operating_range([("W10/W35", 10.0, 35.0)])
    with pytest.raises(ValueError, match="mode"):
        StandardCondition("X", "drying", 10.0, 35.0)
    with pytest.raises(ValueError, match="part_load"):
        StandardCondition("X", "heating", 10.0, 35.0, part_load=0.0)
    with pytest.raises(ValueError, match="wet_bulb"):
        StandardCondition("X", "heating", 2.0, 35.0, outdoor_wet_bulb_C=3.0)
    with pytest.raises(ValueError, match="source_side_C"):
        StandardCondition("X", "heating", math.nan, 35.0)


def test_default_tables_name_their_basis():
    for table in (AIR_TO_WATER, AIR_TO_AIR, BRINE_TO_WATER, WATER_TO_WATER, BRINE_TO_AIR):
        names = [c.name for c in table]
        assert len(set(names)) == len(names)
        assert all(c.basis and c.part_load == 1.0 for c in table)
    assert {c.mode for c in AIR_TO_WATER + BRINE_TO_WATER + WATER_TO_WATER} == {"heating"}
    assert "A-7/W55" in [c.name for c in AIR_TO_WATER]


def test_the_documentation_examples_run():
    from tests.test_docs_examples import REPO_ROOT, _rst_python_blocks

    page = REPO_ROOT / "docs/source/concepts/failure-reason-semantics.rst"
    blocks = [block for block in _rst_python_blocks(page) if "operating_range(" in block]
    assert len(blocks) == 3
    for block in blocks:
        _quiet(exec, compile(block, str(page), "exec"), {})
