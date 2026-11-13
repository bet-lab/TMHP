"""Source-loop fluid and its freezing limit (GSHP, GSHPB, WSHPB).

Before this limit the models returned running cycles whose loop *water* left
the evaporator below 0 °C (GSHPB at ``T_source = -10 °C``: ``T_bhe_f_in =
-11.7 °C``, COP 2.5). The tests cover

1. the fluid table (freezing point, operating limit, properties, parsing) and
   the defaults: 25 % ethylene glycol for the ground loops, water for WSHPB;
2. water: unchanged properties and unchanged results wherever the loop stays
   above its limit;
3. the limit in ``analyze_steady``: off below it, the largest duty on it;
4. an antifreeze mixture: sub-zero operation down to its own limit;
5. the limit in ``analyze_dynamic``: reduced or off steps, one warning per run.
"""

from __future__ import annotations

import math
import warnings

import numpy as np
import pandas as pd
import pytest

from tmhp import GroundSourceHeatPump, GroundSourceHeatPumpBoiler, WaterSourceHeatPumpBoiler
from tmhp.constants import c_w, k_w, mu_w, rho_w
from tmhp.source_fluid import (
    FREEZE_MARGIN_K,
    GROUND_LOOP_FLUID_DEFAULT,
    SOURCE_FLUID_FREEZING,
    SOURCE_FLUID_PRESETS,
    SourceFluid,
    freeze_violation,
    resolve_source_fluid,
    source_fluid_table,
)

SHORT = dict(t_max_s=86400.0, dt_s=600.0)
BOILERS = {"GSHPB": GroundSourceHeatPumpBoiler, "WSHPB": WaterSourceHeatPumpBoiler}
#: Water with no freezing limit to speak of: the model as it was before the limit.
UNLIMITED_WATER = SourceFluid("water without a limit", -60.0, rho_w, c_w, mu_w, k_w)
#: Tolerance on a temperature compared with the limit [K] (the limit's own is 0.01 K).
TOL_K = 0.02


def _quiet(fn, *args, **kwargs):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return fn(*args, **kwargs)


def _freezing_warnings(caught) -> list[str]:
    return [str(w.message) for w in caught if "source-loop liquid" in str(w.message)]


@pytest.fixture(scope="module")
def machines() -> dict[tuple[str, str], object]:
    """One default machine per family and fluid, built once."""
    built: dict[tuple[str, str], object] = {}
    for family, cls in BOILERS.items():
        built[family, "default"] = _quiet(cls, **SHORT)
        built[family, "water"] = _quiet(cls, source_fluid="water", **SHORT)
        built[family, "MPG-25%"] = _quiet(cls, source_fluid="MPG-25%", **SHORT)
        built[family, "unlimited"] = _quiet(cls, source_fluid=UNLIMITED_WATER, **SHORT)
    built["GSHP", "default"] = _quiet(GroundSourceHeatPump, t_max_s=3600.0)
    built["GSHP", "water"] = _quiet(GroundSourceHeatPump, source_fluid="water", t_max_s=3600.0)
    built["GSHP", "MPG-30%"] = _quiet(GroundSourceHeatPump, source_fluid="MPG-30%", t_max_s=3600.0)
    return built


# ---------------------------------------------------------------------------
# 1. the fluid table
# ---------------------------------------------------------------------------


def test_water_keeps_the_model_constants(machines):
    water = resolve_source_fluid("water")
    assert (water.T_freeze_C, water.T_min_C) == (0.0, FREEZE_MARGIN_K)
    assert (water.rho, water.cp, water.mu, water.k) == (rho_w, c_w, mu_w, k_w)
    assert water.is_water and resolve_source_fluid(None) is water and resolve_source_fluid(" Water ") is water
    for family in ("GSHPB", "WSHPB", "GSHP"):
        assert machines[family, "water"].source_fluid is water


def test_default_loop_liquid_is_glycol_for_the_ground_and_water_for_the_water_source(machines):
    """Ground loops default to 25 % ethylene glycol (freezes at -11.0 °C, limit -8.0 °C), WSHPB to water."""
    glycol = resolve_source_fluid(GROUND_LOOP_FLUID_DEFAULT)
    assert GROUND_LOOP_FLUID_DEFAULT == "MEG-25%" and resolve_source_fluid(None, GROUND_LOOP_FLUID_DEFAULT) is glycol
    assert glycol.T_freeze_C == pytest.approx(-11.0, abs=0.05) and glycol.T_min_C == pytest.approx(-8.0, abs=0.05)
    assert machines["GSHP", "default"].source_fluid is glycol
    assert machines["GSHPB", "default"].source_fluid is glycol
    assert machines["WSHPB", "default"].source_fluid is resolve_source_fluid("water")


def test_default_constructors_are_quiet():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        GroundSourceHeatPump(t_max_s=3600.0)
        GroundSourceHeatPumpBoiler(**SHORT)
        WaterSourceHeatPumpBoiler(**SHORT)


def test_default_machines_run_their_brine_rating_points(machines):
    """EN 14511 B0/W35 and B0/W55 (GSHPB) and a 0 °C borehole wall (GSHP) with nothing but the defaults."""
    gshpb = machines["GSHPB", "default"]
    assert gshpb.ref == "R290"  # (R410A, the default before: 3.847 and 2.404, with glycol as with water)
    for T_tank, cop_sys in ((35.0, 3.870), (55.0, 2.651)):
        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            r = gshpb.analyze_steady(T_tank_w=T_tank, T_source=0.0, Q_ref_tank=8000.0)
        assert r["failure_reason"] == "none" and r["capacity_clamped"] is None
        assert r["Q_ref_tank [W]"] == pytest.approx(8000.0)
        assert r["T_bhe_f_in [°C]"] == pytest.approx(-3.0 if T_tank == 35.0 else -2.5, abs=0.1)
        assert r["cop_sys [-]"] == pytest.approx(cop_sys, rel=2e-3)
    gshp = machines["GSHP", "default"]
    # 3.520 and 3.921 with a water loop before it had a freezing point: the glycol's
    # borehole resistance is 0.168 against 0.162 m K/W.
    for load, cop_sys in ((-4000.0, 3.508), (-2000.0, 3.915)):
        r = _quiet(gshp.analyze_steady, Q_r_iu=load, T0=0.0, T_a_room=20.0, T_bhe_wall=0.0)
        assert r["failure_reason"] == "none" and r["capacity_clamped"] is None
        assert r["cop_sys [-]"] == pytest.approx(cop_sys, rel=2e-3)
        assert gshp.source_fluid.T_min_C < r["T_bhe_f_in [°C]"] < 0.0


@pytest.mark.parametrize(
    ("name", "T_freeze"),
    # CoolProp incompressible mixtures by mass (Melinder 2010 fits): freezing points to 0.2 K.
    [("MPG-25%", -9.8), ("MPG-30%", -12.8), ("MEG-25%", -11.0), ("ethanol-20%", -11.1)],
)
def test_mixture_freezing_point_limit_and_properties(name, T_freeze):
    fluid = resolve_source_fluid(name)
    assert fluid.T_freeze_C == pytest.approx(T_freeze, abs=0.2)
    assert fluid.T_min_C == pytest.approx(fluid.T_freeze_C + FREEZE_MARGIN_K)
    assert not fluid.is_water
    # Less heat per litre and several times the viscosity of water at 0 °C.
    assert 0.90 < fluid.rho * fluid.cp / (rho_w * c_w) < 1.03
    assert 3.0 < fluid.mu / mu_w < 8.0
    assert fluid.k < k_w


def test_fluid_names_are_parsed_and_bad_ones_rejected():
    assert resolve_source_fluid("mpg-25%") is resolve_source_fluid("MPG-25%")
    assert resolve_source_fluid("MPG 25") == resolve_source_fluid("MPG-25%")
    stronger, weaker = resolve_source_fluid("MEG-35%"), resolve_source_fluid("MEG-20%")
    assert stronger.T_freeze_C < weaker.T_freeze_C < 0.0
    for bad in ("brine", "MPG", "MPG-2%", "MPG-80%", "R410A-25%", 25, ""):
        with pytest.raises(ValueError, match="source_fluid"):
            resolve_source_fluid(bad)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="source_fluid"):
        _quiet(GroundSourceHeatPumpBoiler, source_fluid="antifreeze", **SHORT)


def test_custom_fluid_carries_its_own_limit():
    brine = SourceFluid("site brine", -15.0, 1040.0, 3700.0, 0.006, 0.43, T_min_C=-10.0)
    assert brine.T_min_C == -10.0 and resolve_source_fluid(brine) is brine
    assert SourceFluid("x", -15.0, 1040.0, 3700.0, 0.006, 0.43).T_min_C == pytest.approx(-12.0)
    with pytest.raises(ValueError, match="T_min_C"):
        SourceFluid("x", -15.0, 1040.0, 3700.0, 0.006, 0.43, T_min_C=-16.0)
    with pytest.raises(ValueError, match="mu"):
        SourceFluid("x", -15.0, 1040.0, 3700.0, 0.0, 0.43)


def test_documented_table_lists_every_preset():
    rows = source_fluid_table()
    assert [row["source_fluid"] for row in rows] == list(SOURCE_FLUID_PRESETS)
    assert all(row["T_min [°C]"] == pytest.approx(row["T_freeze [°C]"] + FREEZE_MARGIN_K) for row in rows)


def test_model_page_table_states_the_values_of_the_code():
    """docs/source/models/gshpb.rst lists freezing point and limit to 0.1 K."""
    from pathlib import Path

    page = (Path(__file__).resolve().parents[1] / "docs/source/models/gshpb.rst").read_text(encoding="utf-8")
    assert '    * - ``"MEG-25%"`` (default of GSHP and GSHPB)\n      - -11.0 °C\n      - -8.0 °C\n' in page
    for row in source_fluid_table(("MPG-20%", "MPG-25%", "MPG-30%", "MEG-30%", "ethanol-20%")):
        listed = (
            f'    * - ``"{row["source_fluid"]}"``\n'
            f"      - {row['T_freeze [°C]']:.1f} °C\n"
            f"      - {row['T_min [°C]']:.1f} °C\n"
        )
        assert listed in page, row["source_fluid"]
    assert '    * - ``"water"`` (default of WSHPB)\n      - 0.0 °C\n      - 3.0 °C\n' in page


# ---------------------------------------------------------------------------
# 2. default fluid: nothing changes while the loop stays above its limit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("family", list(BOILERS))
def test_points_above_the_limit_are_unchanged(machines, family):
    """A warm loop never meets the limit: the result is that of the model without one."""
    case = dict(T_tank_w=45.0, T_source=10.0, Q_ref_tank=8000.0, T0=10.0)
    limited = _quiet(machines[family, "water"].analyze_steady, **case)
    free = _quiet(machines[family, "unlimited"].analyze_steady, **case)
    assert limited["failure_reason"] == "none" and limited["capacity_clamped"] is None
    assert limited["T_bhe_f_in [°C]"] > FREEZE_MARGIN_K
    for key in ("Q_ref_tank [W]", "E_cmp [W]", "cop_ref [-]", "T_ref_evap_sat [°C]", "T_bhe_f_in [°C]"):
        assert limited[key] == free[key], key


# ---------------------------------------------------------------------------
# 3. the limit in analyze_steady
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("family", list(BOILERS))
@pytest.mark.parametrize("T_source", [-10.0, 0.0, 2.0])
def test_water_entering_below_its_limit_does_not_run(machines, family, T_source):
    """The defect: sub-zero water circulating through a running evaporator."""
    case = dict(T_tank_w=45.0, T_source=T_source, Q_ref_tank=6000.0)
    was = _quiet(machines[family, "unlimited"].analyze_steady, **case)
    assert was["hp_is_on"] and was["T_bhe_f_in [°C]"] < T_source  # what the model used to return
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        r = machines[family, "water"].analyze_steady(**case)
    assert r["failure_reason"] == SOURCE_FLUID_FREEZING and r["failure_detail"] == "source_inlet_below_limit"
    assert r["hp_is_on"] is False and r["converged"] is False
    assert r["Q_ref_tank [W]"] == 0.0 and r["E_cmp [W]"] == 0.0 and math.isnan(r["cop_ref [-]"])
    # The keys every steady result carries are there, in their off values.
    assert r["efficiency_extrapolated"] is False and r["discharge_control"] is False
    assert math.isnan(r["x_ref_cmp_in [-]"]) and math.isnan(r["dT_ref_superheat [K]"])
    (message,) = _freezing_warnings(caught)
    assert "3.0 °C" in message and "MPG-25%" in message


@pytest.mark.parametrize("family", list(BOILERS))
def test_duty_is_reduced_onto_the_limit(machines, family):
    """Water entering at 5 °C leaves at 2 °C at full duty: the unit turns down instead."""
    machine = machines[family, "water"]
    case = dict(T_tank_w=45.0, T_source=5.0, Q_ref_tank=8000.0)
    free = _quiet(machines[family, "unlimited"].analyze_steady, **case)
    assert free["T_bhe_f_in [°C]"] < FREEZE_MARGIN_K - 0.5
    r = _quiet(machine.analyze_steady, **case)
    assert r["failure_reason"] == "none" and r["hp_is_on"]
    assert r["capacity_clamped"] == "max" and r["capacity_binding"] == SOURCE_FLUID_FREEZING
    # On the limit, within the band the reduction closes to.
    assert FREEZE_MARGIN_K - TOL_K <= r["T_bhe_f_in [°C]"] <= FREEZE_MARGIN_K + 0.05
    assert r["Q_request [W]"] == 8000.0 and r["Q_tank_load [W]"] == 8000.0
    assert r["Q_max [W]"] == pytest.approx(r["Q_ref_tank [W]"])
    # 2 K of the 3 K loop difference is left: about two thirds of the duty.
    assert 0.55 * 8000.0 < r["Q_ref_tank [W]"] < 0.80 * 8000.0
    assert freeze_violation(r, machine.source_fluid) is None
    # A request the limit already allows is met exactly.
    below = _quiet(machine.analyze_steady, **{**case, "Q_ref_tank": 0.9 * r["Q_ref_tank [W]"]})
    assert below["capacity_clamped"] is None and below["Q_ref_tank [W]"] == pytest.approx(0.9 * r["Q_ref_tank [W]"])
    # The one-row frame carries the same reduced point.
    frame = _quiet(machine.analyze_steady, **case, return_dict=False)
    assert isinstance(frame, pd.DataFrame) and frame.loc[0, "capacity_binding"] == SOURCE_FLUID_FREEZING


def test_gshp_heating_stops_on_a_freezing_borehole_and_cooling_is_untouched(machines):
    gshp = machines["GSHP", "water"]
    cold = _quiet(gshp.analyze_steady, Q_r_iu=-4000.0, T0=0.0, T_a_room=21.0, T_bhe_wall=0.0)
    assert cold["failure_reason"] == SOURCE_FLUID_FREEZING and cold["hp_is_on"] is False
    assert cold["Q_ref_iu [W]"] == 0.0 and math.isnan(cold["cop_sys [-]"])
    mild = _quiet(gshp.analyze_steady, Q_r_iu=-4000.0, T0=0.0, T_a_room=21.0, T_bhe_wall=10.0)
    assert mild["failure_reason"] == "none" and mild["T_bhe_f_in [°C]"] >= FREEZE_MARGIN_K - TOL_K
    cooling = _quiet(gshp.analyze_steady, Q_r_iu=4000.0, T0=30.0, T_a_room=27.0, T_bhe_wall=15.0)
    assert cooling["failure_reason"] == "none" and cooling["capacity_clamped"] is None
    assert cooling["T_bhe_f_in [°C]"] > cooling["T_bhe_f_out [°C]"] > FREEZE_MARGIN_K


def test_gshp_duty_is_reduced_onto_the_limit(machines):
    gshp = machines["GSHP", "water"]
    r = _quiet(gshp.analyze_steady, Q_r_iu=-4000.0, T0=0.0, T_a_room=21.0, T_bhe_wall=8.0)
    assert r["failure_reason"] == "none" and r["capacity_binding"] == SOURCE_FLUID_FREEZING
    assert r["capacity_clamped"] == "max" and r["Q_request [W]"] == -4000.0
    assert FREEZE_MARGIN_K - TOL_K <= r["T_bhe_f_in [°C]"] <= FREEZE_MARGIN_K + 0.05
    assert 0.3 * 4000.0 < r["Q_ref_iu [W]"] < 4000.0
    assert r["Q_max [W]"] == pytest.approx(r["Q_ref_iu [W]"])


class _Loop:
    """A source loop reduced to its temperatures: enough to drive the duty search.

    The liquid enters at ``T_unloaded - drop * q`` and leaves ``rise * q``
    colder; below ``q_floor`` the machine has no operating point (speed floor).
    """

    source_fluid = resolve_source_fluid("water")

    def __init__(self, T_unloaded, drop=0.0, rise=3.0 / 8000.0, q_floor=0.0):
        self.T_unloaded, self.drop, self.rise, self.q_floor = T_unloaded, drop, rise, q_floor
        self.solves = 0

    def solve(self, q):
        self.solves += 1
        if q < self.q_floor:
            return {"hp_is_on": False, "failure_reason": "cycle_invalid"}
        entering = self.T_unloaded - self.drop * q
        return {
            "hp_is_on": True,
            "failure_reason": "none",
            "capacity_clamped": None,
            "Q_ref_tank [W]": q,
            "T_bhe_f_out [°C]": entering,
            "T_bhe_f_in [°C]": entering - self.rise * q,
        }

    def limited(self, request=8000.0):
        from tmhp.source_fluid import limit_duty_to_source_freeze

        first = self.solve(request)
        self.solves = 0
        off = lambda detail: {"hp_is_on": False, "failure_reason": SOURCE_FLUID_FREEZING, "Q_ref_tank [W]": 0.0}  # noqa: E731
        return _quiet(limit_duty_to_source_freeze, self, first, self.solve, request, off)


def test_duty_search_closes_in_a_few_solves_and_knows_when_to_stop():
    # Entering at 5 °C whatever the duty: the limit is met at 2 K of the 3 K rise.
    loop = _Loop(5.0)
    r = loop.limited()
    assert r["capacity_binding"] == SOURCE_FLUID_FREEZING and loop.solves <= 2
    assert r["Q_ref_tank [W]"] == pytest.approx(8000.0 * 2.0 / 3.0, rel=0.02)
    # A borehole behind the loop: it arrives at 2 °C at full duty, warmer at a smaller one.
    loop = _Loop(6.0, drop=4.0 / 8000.0)
    r = loop.limited()
    assert r["capacity_binding"] == SOURCE_FLUID_FREEZING and loop.solves <= 4
    assert FREEZE_MARGIN_K - TOL_K <= r["T_bhe_f_in [°C]"] <= FREEZE_MARGIN_K + 0.05
    assert r["Q_ref_tank [W]"] == pytest.approx(8000.0 * 3.0 / 7.0, rel=0.02)
    # A duty the speed floor refuses bounds the search from below; duties above it still run.
    loop = _Loop(6.0, drop=4.0 / 8000.0, q_floor=3000.0)
    r = loop.limited()
    assert r["capacity_binding"] == SOURCE_FLUID_FREEZING and r["Q_ref_tank [W]"] >= 3000.0
    # ... and when the limit needs less than the floor, nothing runs.
    loop = _Loop(6.0, drop=4.0 / 8000.0, q_floor=4000.0)
    assert loop.limited()["failure_reason"] == SOURCE_FLUID_FREEZING and loop.solves <= 12
    # The liquid arrives below the limit whatever the duty: one solve settles it.
    loop = _Loop(2.0)
    assert loop.limited()["failure_reason"] == SOURCE_FLUID_FREEZING and loop.solves == 1
    # Heat rejected to the loop cannot be cured by rejecting less: no solve at all.
    loop = _Loop(2.0, rise=-3.0 / 8000.0)
    assert loop.limited()["failure_reason"] == SOURCE_FLUID_FREEZING and loop.solves == 0
    # Above the limit nothing is touched.
    loop = _Loop(12.0)
    assert loop.limited()["capacity_clamped"] is None and loop.solves == 0


def test_wall_coupled_boiler_runs_the_duty_its_borehole_allows():
    """Coupled ground-flow path: a 6 °C wall returns 2.7 °C liquid at 8 kW, 4.4 °C at half the duty."""
    gshpb = _quiet(GroundSourceHeatPumpBoiler, hydraulic_pump=True, source_fluid="water", **SHORT)
    r = _quiet(gshpb.analyze_steady, T_tank_w=45.0, T_source=6.0, Q_ref_tank=8000.0, T_bhe_wall=6.0)
    assert r["failure_reason"] == "none" and r["capacity_binding"] == SOURCE_FLUID_FREEZING
    assert FREEZE_MARGIN_K - TOL_K <= r["T_bhe_f_in [°C]"] <= FREEZE_MARGIN_K + 0.05
    assert 0.3 * 8000.0 < r["Q_ref_tank [W]"] < 0.7 * 8000.0


def test_steady_limits_apply_in_order_cycling_mean_then_freezing_then_envelope(machines):
    """``analyze_steady``: on/off mean > source-fluid limit > compressor envelope (condensing limit included)."""
    water = machines["GSHPB", "water"]
    limit, T_cond_max = FREEZE_MARGIN_K, water.T_cond_max
    assert (water.ref, T_cond_max) == ("R290", 75.0)
    # Tank at 72 °C: the condensing limit allows 4.8 kW (3 K of approach), less
    # than the freezing limit would (about 5.5 kW from a 5 °C source). The
    # envelope reduces, and the point it returns is inside the freezing limit.
    r = _quiet(water.analyze_steady, T_tank_w=72.0, T_source=5.0, Q_ref_tank=8000.0)
    assert r["failure_reason"] == "none" and r["capacity_binding"] == "condensing_temperature"
    assert r["T_ref_cond_sat_l [°C]"] == pytest.approx(T_cond_max, abs=0.05)
    assert r["T_bhe_f_in [°C]"] > limit + 0.5
    # Tank at 68 °C: the condensing limit would allow the full 8 kW; the
    # freezing limit binds, and the reduced point is inside the envelope.
    r = _quiet(water.analyze_steady, T_tank_w=68.0, T_source=5.0, Q_ref_tank=8000.0)
    assert r["failure_reason"] == "none" and r["capacity_binding"] == SOURCE_FLUID_FREEZING
    assert limit - TOL_K <= r["T_bhe_f_in [°C]"] <= limit + 0.05
    assert r["T_ref_cond_sat_l [°C]"] < T_cond_max - 1.0 and r["Q_ref_tank [W]"] < 8000.0
    # A request below the minimum-speed duty is the mean of on/off operation of
    # a machine whose running loop is above the limit ...
    r = _quiet(water.analyze_steady, T_tank_w=45.0, T_source=5.0, Q_ref_tank=400.0)
    assert r["failure_reason"] == "none" and r["capacity_clamped"] == "min"
    assert r["Q_ref_tank [W]"] == pytest.approx(400.0) and r["Q_min [W]"] > 1500.0
    assert 0.0 < r["runtime_fraction [-]"] < 1.0 and r["T_bhe_f_in [°C]"] >= limit - TOL_K
    # ... and off when the running machine, already on its speed floor, would
    # chill the loop below it: switching on and off does not warm the liquid.
    r = _quiet(water.analyze_steady, T_tank_w=45.0, T_source=3.5, Q_ref_tank=400.0)
    assert r["failure_reason"] == SOURCE_FLUID_FREEZING and r["hp_is_on"] is False
    assert r["Q_ref_tank [W]"] == 0.0 and r["runtime_fraction [-]"] == 0.0


# ---------------------------------------------------------------------------
# 4. antifreeze
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("family", list(BOILERS))
def test_antifreeze_runs_the_brine_point_and_stops_at_its_own_limit(machines, family):
    brine = machines[family, "MPG-25%"]
    fluid = brine.source_fluid
    # Default flow keeps the 3 K loop difference with the mixture's heat capacity.
    water = machines[family, "water"]
    assert brine.dV_b_f_m3s / water.dV_b_f_m3s == pytest.approx(rho_w * c_w / (fluid.rho * fluid.cp))
    # B0/W35: liquid in at 0 °C, out at -3 °C, full duty.
    rated = _quiet(brine.analyze_steady, T_tank_w=35.0, T_source=0.0, Q_ref_tank=8000.0)
    assert rated["failure_reason"] == "none" and rated["capacity_clamped"] is None
    assert rated["T_bhe_f_in [°C]"] == pytest.approx(-3.0, abs=0.3)
    assert 3.5 < rated["cop_ref [-]"] < 5.0
    # Reduced once the liquid would leave below freezing point + 3 K ...
    reduced = _quiet(brine.analyze_steady, T_tank_w=35.0, T_source=-5.0, Q_ref_tank=8000.0)
    assert reduced["capacity_binding"] == SOURCE_FLUID_FREEZING
    assert fluid.T_min_C - TOL_K <= reduced["T_bhe_f_in [°C]"] <= fluid.T_min_C + 0.05
    # ... and off when it already arrives below it.
    off = _quiet(brine.analyze_steady, T_tank_w=35.0, T_source=-10.0, Q_ref_tank=8000.0)
    assert off["failure_reason"] == SOURCE_FLUID_FREEZING and off["hp_is_on"] is False


def test_gshp_antifreeze_runs_against_a_freezing_borehole_wall(machines):
    brine = machines["GSHP", "MPG-30%"]
    r = _quiet(brine.analyze_steady, Q_r_iu=-4000.0, T0=0.0, T_a_room=21.0, T_bhe_wall=0.0)
    assert r["failure_reason"] == "none" and r["hp_is_on"]
    assert brine.source_fluid.T_min_C - TOL_K <= r["T_bhe_f_in [°C]"] < 0.0


def test_ground_loop_options_that_assume_water_are_named_for_a_chosen_fluid():
    for chosen in ("MPG-25%", "MEG-25%"):
        with pytest.warns(UserWarning, match="viscosity of water"):
            GroundSourceHeatPumpBoiler(source_fluid=chosen, hydraulic_pump=True, **SHORT)
    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)
        GroundSourceHeatPumpBoiler(source_fluid="MPG-25%", **SHORT)  # nothing to report without them
        # A model left on its default liquid does not warn about it (documented instead).
        GroundSourceHeatPumpBoiler(hydraulic_pump=True, **SHORT)
        GroundSourceHeatPumpBoiler(source_fluid="water", hydraulic_pump=True, **SHORT)


# ---------------------------------------------------------------------------
# 5. the limit in analyze_dynamic
# ---------------------------------------------------------------------------


def _run_wshpb(machine, T_source_w):
    n = len(T_source_w)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        # 300 s steps from a 30 °C tank: the thermostat calls for heat on every step.
        df = machine.analyze_dynamic(
            n * 300.0, 300.0, 30.0, np.zeros(n), np.full(n, 5.0), T_source_w_schedule=np.asarray(T_source_w)
        )
    return df, _freezing_warnings(caught)


def test_dynamic_steps_are_reduced_or_off_and_warn_once(machines):
    machine = machines["WSHPB", "water"]
    # Source water: mild, then 5 °C (the coil returns the loop colder still), then freezing.
    df, messages = _run_wshpb(machine, [12.0, 12.0, 5.0, 5.0, 1.0, 1.0])
    reasons = df["failure_reason"].tolist()
    assert reasons[:2] == ["none", "none"] and bool(df["hp_is_on"].iloc[0])
    assert reasons[4:] == [SOURCE_FLUID_FREEZING] * 2 and not df["hp_is_on"].iloc[4:].any()
    assert (df["Q_ref_tank [W]"].iloc[4:] == 0.0).all() and (df["E_cmp [W]"].iloc[4:] == 0.0).all()
    # Every running step keeps the loop on or above the limit while the compressor runs.
    running = df[df["hp_is_on"]]
    assert (running["T_bhe_f_in_running [°C]"] >= FREEZE_MARGIN_K - TOL_K).all()
    cold = df.iloc[2:4]
    assert cold["hp_is_on"].all() and (cold["capacity_binding"] == SOURCE_FLUID_FREEZING).all()
    assert (cold["Q_ref_tank [W]"] < 0.9 * df["Q_ref_tank [W]"].iloc[0]).all()
    assert len(messages) == 1 and "analyze_dynamic" in messages[0]
    # A second run reports again, once.
    _, again = _run_wshpb(machine, [1.0, 1.0])
    assert len(again) == 1


def test_dynamic_run_above_the_limit_is_unchanged(machines):
    limited, messages = _run_wshpb(machines["WSHPB", "water"], [12.0] * 4)
    free, _ = _run_wshpb(machines["WSHPB", "unlimited"], [12.0] * 4)
    assert messages == []
    pd.testing.assert_frame_equal(limited, free)


@pytest.mark.parametrize("borehole_dynamics", [None, False])
def test_gshpb_dynamic_ground_near_freezing(borehole_dynamics):
    """Ground at 6.5 °C: full extraction chills the water below 3 °C, so the steps turn down.

    With the dynamic borehole (the default) the fluid cools over the first
    steps, so the first one still runs at full duty; the steady borehole
    drops by ``q' R_b`` at once. The limit is read on the running fluid of
    the step in both.
    """
    kwargs = dict(Ts=6.5, borehole_dynamics=borehole_dynamics, **SHORT)
    water = dict(source_fluid="water", **kwargs)
    n = 3
    args = (n * 600.0, 600.0, 30.0, np.zeros(n), np.full(n, 5.0))
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        df = _quiet(GroundSourceHeatPumpBoiler, **water).analyze_dynamic(*args)
        messages = _freezing_warnings(caught)
    free = _quiet(_quiet(GroundSourceHeatPumpBoiler, source_fluid=UNLIMITED_WATER, **kwargs).analyze_dynamic, *args)
    # The defect: water leaving the evaporator below its limit in a running step.
    assert free["hp_is_on"].all() and (free["T_bhe_f_in_running [°C]"] < FREEZE_MARGIN_K - 0.3).any()
    assert len(messages) == 1
    assert df["hp_is_on"].all() and (df["failure_reason"] == "none").all()
    assert (df["T_bhe_f_in_running [°C]"] >= FREEZE_MARGIN_K - TOL_K).all()
    reduced = df["capacity_binding"] == SOURCE_FLUID_FREEZING
    assert reduced.iloc[1:].all()
    assert (df.loc[reduced, "T_bhe_f_in_running [°C]"] <= FREEZE_MARGIN_K + 0.05).all()
    assert (df.loc[reduced, "Q_ref_tank [W]"] < 0.95 * free.loc[reduced, "Q_ref_tank [W]"]).all()
    # A step the limit does not touch is the step of the model without it.
    assert (df.loc[~reduced, "Q_ref_tank [W]"] == free.loc[~reduced, "Q_ref_tank [W]"]).all()


@pytest.mark.parametrize("borehole_dynamics", [None, False], ids=["dynamic-borehole", "steady-borehole"])
def test_default_glycol_rows_report_the_fluid_the_limit_was_checked_with(borehole_dynamics):
    """Cold ground on the default glycol (limit -8.0 °C): the steps turn down onto the limit.

    (-3 °C for the steady borehole, whose fluid drops by ``q' R_b`` at once;
    -4.5 °C for the dynamic one, whose fluid cools over the first steps.)

    The reported running fluid is the one the step's cycle was solved and the
    limit was checked with: the reduced steps report the limit itself (they
    used to report it 0.08 K off, the loop difference being split with the
    heat capacity of water), the loop temperature difference carries the row's
    ground heat with the glycol's own heat capacity, and the evaporator
    balance closes on the reported entering fluid.
    """
    Ts = -3.0 if borehole_dynamics is False else -4.5
    model = _quiet(GroundSourceHeatPumpBoiler, Ts=Ts, borehole_dynamics=borehole_dynamics, **SHORT)
    fluid, n = model.source_fluid, 4
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        df = model.analyze_dynamic(n * 600.0, 600.0, 30.0, np.zeros(n), np.full(n, 5.0))
    assert len(_freezing_warnings(caught)) == 1 and "MEG-25%" in _freezing_warnings(caught)[0]
    reduced = (df["capacity_binding"] == SOURCE_FLUID_FREEZING).to_numpy()
    assert df["hp_is_on"].all() and reduced.sum() >= 2
    leaving, entering = df["T_bhe_f_in_running [°C]"].to_numpy(), df["T_bhe_f_out_running [°C]"].to_numpy()
    # Decision value = reported value: the limit the search closed on, within its own band.
    assert (leaving >= fluid.T_min_C - TOL_K).all()
    assert (leaving[reduced] <= fluid.T_min_C + 0.035).all()
    m_cp = df["dV_bhe_f [m3/s]"].to_numpy() * fluid.rho * fluid.cp
    q_run = (df["Q_bhe [W]"] / df["runtime_fraction [-]"]).to_numpy()
    np.testing.assert_allclose((entering - leaving) * m_cp, q_run, rtol=1e-6)
    # The cycle of the row was solved on that entering fluid (pump heat included).
    eps = -np.expm1(-model.UA_ground / m_cp)
    pump = (df["E_pmp [W]"] / df["runtime_fraction [-]"]).to_numpy()
    q_hx = eps * m_cp * (entering + pump / m_cp - df["T_ref_evap_sat [°C]"].to_numpy())
    np.testing.assert_allclose(q_hx, q_run + pump, rtol=2e-3)


def test_gshp_default_glycol_rows_report_the_fluid_of_their_cycle():
    """GSHP on the default glycol: reported loop difference and ground-side saturation belong to one fluid."""
    gshp = _quiet(GroundSourceHeatPump, t_max_s=7200.0, dt_s=600.0)
    fluid, n = gshp.source_fluid, 3
    df = _quiet(gshp.analyze_dynamic, n * 600, 600, np.full(n, -4000.0), np.zeros(n), np.full(n, 20.0))
    assert df["hp_is_on"].all() and (df["failure_reason"] == "none").all()
    m_cp = df["dV_bhe_f [m3/s]"] * fluid.rho * fluid.cp
    np.testing.assert_allclose((df["T_bhe_f_out [°C]"] - df["T_bhe_f_in [°C]"]) * m_cp, df["Q_bhe [W]"], rtol=1e-6)
    source = df["T_bhe_f_out [°C]"] + df["E_pmp [W]"] / m_cp
    np.testing.assert_allclose(df["T_ref_evap_sat [°C]"], source - df["ground_approach [K]"], atol=1e-3)
    # Water's heat capacity would put the leaving liquid 0.2 K off (7 % of the 3 K loop difference).
    off = (df["T_bhe_f_out [°C]"] - df["Q_bhe [W]"] / (df["dV_bhe_f [m3/s]"] * rho_w * c_w)) - df["T_bhe_f_in [°C]"]
    assert (off.abs() > 0.1).all()


def test_gshp_dynamic_reports_the_limit_once():
    gshp = _quiet(GroundSourceHeatPump, Ts=2.0, t_max_s=7200.0, dt_s=600.0, source_fluid="water")
    n = 3
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        df = gshp.analyze_dynamic(n * 600, 600, np.full(n, -3000.0), np.full(n, 0.0), np.full(n, 20.0))
    assert (df["failure_reason"] == SOURCE_FLUID_FREEZING).all() and not df["hp_is_on"].any()
    assert (df["Q_ref_iu [W]"] == 0.0).all()
    assert len(_freezing_warnings(caught)) == 1
