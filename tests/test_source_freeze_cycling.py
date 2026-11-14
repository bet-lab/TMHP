"""The source fluid's freezing limit on the compressor's speed floor.

:func:`tmhp.source_fluid.limit_duty_to_source_freeze` lowers the duty until
the loop liquid stays on its operating limit, and it stopped as soon as the
compressor was on its speed floor: "no smaller running state". That holds for
the running machine, but a unit that cycles meets a smaller duty with shorter
runs, and where the source answers the *mean* load -- a borehole wall in a
dynamic step -- the liquid of those runs is warmer. A ground-source heat pump
on a 7 °C water loop asked for 1.5 kW (below its 2 kW minimum-speed duty) ran
three steps by cycling and was then switched off in every other step, although
cycling at about half that duty keeps the liquid on the limit.

Where the source does not answer (a prescribed wall or source temperature, a
model that does not cycle) the liquid of a smaller duty is the same: one trial
shows it, and the unit stays off as before.
"""

import warnings

import numpy as np
import pytest

from tmhp import GroundSourceHeatPump
from tmhp.cycling import CAPACITY_RATIO_KEY, RUNTIME_FRACTION_KEY
from tmhp.source_fluid import (
    DETAIL_OUTLET,
    ENTERING_KEY,
    FREEZE_TOL_K,
    LEAVING_KEY,
    SOURCE_FLUID_FREEZING,
    limit_duty_to_source_freeze,
    resolve_source_fluid,
)

WATER = resolve_source_fluid("water")
LIMIT = WATER.limit_C  # 3 °C


class _Unit:
    """A unit whose minimum-speed duty is 2 kW; smaller duties are met by cycling (rows on the floor)."""

    source_fluid = WATER
    hp_capacity = 4000.0

    def __init__(self, leaving_on_floor):
        self.leaving_on_floor = leaving_on_floor  # leaving liquid of the running machine [°C] at a mean duty
        self.trials: list[float] = []

    def row(self, q: float) -> dict:
        floor = q < 2000.0
        leaving = self.leaving_on_floor(q) if floor else self.leaving_on_floor(2000.0) - (q - 2000.0) / 500.0
        return {
            "hp_is_on": True,
            "failure_reason": "none",
            "capacity_clamped": "min" if floor else None,
            "Q_ref_iu [W]": q,
            LEAVING_KEY: leaving,
            ENTERING_KEY: leaving + 1.5,
        }

    def solve(self, q: float) -> dict:
        self.trials.append(q)
        return self.row(q)

    def limited(self, request: float) -> dict:
        def off(detail: str) -> dict:
            return {"hp_is_on": False, "failure_reason": SOURCE_FLUID_FREEZING, "Q_ref_iu [W]": 0.0}

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return limit_duty_to_source_freeze(
                self, self.row(request), self.solve, request, off, delivered_key="Q_ref_iu [W]"
            )


def test_a_cycling_unit_is_reduced_to_the_mean_duty_that_keeps_the_limit():
    """The source answers the mean load: 1 K warmer per kW less. The limit is met at 1 kW."""
    unit = _Unit(lambda q: 2.0 + (2000.0 - q) / 1000.0)
    result = unit.limited(1500.0)  # the running machine leaves at 2.5 °C
    assert result["hp_is_on"] and result["failure_reason"] == "none"
    assert result["capacity_clamped"] == "max" and result["capacity_binding"] == SOURCE_FLUID_FREEZING
    assert LIMIT - FREEZE_TOL_K <= result[LEAVING_KEY] <= LIMIT + 0.05
    assert result["Q_ref_iu [W]"] == pytest.approx(1000.0, rel=5e-2)
    assert result["Q_max [W]"] == result["Q_ref_iu [W]"] and result["Q_request [W]"] == 1500.0
    assert len(unit.trials) <= 4


def test_a_request_above_the_speed_floor_is_reduced_into_the_cycling_range():
    unit = _Unit(lambda q: 2.0 + (2000.0 - q) / 1000.0)
    result = unit.limited(3000.0)  # continuous: leaves at 0 °C; at the speed floor still 2 °C
    assert result["failure_reason"] == "none" and result["capacity_binding"] == SOURCE_FLUID_FREEZING
    assert result["Q_ref_iu [W]"] == pytest.approx(1000.0, rel=5e-2)
    assert LIMIT - FREEZE_TOL_K <= result[LEAVING_KEY] <= LIMIT + 0.05


def test_a_source_that_does_not_answer_the_mean_load_is_refused_after_one_trial():
    """Fixed source temperature: every duty on the speed floor leaves the liquid at 2.5 °C."""
    unit = _Unit(lambda q: 2.5)
    result = unit.limited(1500.0)
    assert not result["hp_is_on"] and result["failure_reason"] == SOURCE_FLUID_FREEZING
    assert result["failure_detail"] == DETAIL_OUTLET
    assert len(unit.trials) == 1


def test_a_liquid_below_the_limit_at_every_duty_is_refused():
    """The source answers, but even the shortest run leaves below the limit (2.9 °C at zero duty)."""
    unit = _Unit(lambda q: 2.9 - q / 4000.0)
    result = unit.limited(1500.0)
    assert not result["hp_is_on"] and result["failure_reason"] == SOURCE_FLUID_FREEZING


# ---------------------------------------------------------------------------
# GroundSourceHeatPump
# ---------------------------------------------------------------------------

REQUEST, STEPS, DT = 1500.0, 6, 3600


@pytest.fixture(scope="module")
def cold_loop_run():
    """Water loop in 7 °C ground, steady borehole: six 1 h steps asked for 1.5 kW of heating."""
    model = GroundSourceHeatPump(source_fluid="water", Ts=7.0, borehole_dynamics=False, t_max_s=STEPS * DT, dt_s=DT)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df = model.analyze_dynamic(STEPS * DT, DT, np.full(STEPS, -REQUEST), np.zeros(STEPS), np.full(STEPS, 20.0))
    return model, df


def test_dynamic_steps_on_the_speed_floor_cycle_on_the_freezing_limit(cold_loop_run):
    _, df = cold_loop_run
    # Every step runs; before, steps 3 and 5 were off while the unit cycled in the others.
    assert df["hp_is_on"].all() and (df["failure_reason"] == "none").all()
    limited = (df["capacity_binding"] == SOURCE_FLUID_FREEZING).to_numpy()
    assert limited.sum() >= 2 and not limited[0]
    assert (df.loc[~limited, "capacity_clamped"] == "min").all()
    assert df.loc[~limited, "Q_ref_iu [W]"].to_numpy() == pytest.approx(REQUEST, rel=1e-9)
    rows = df[limited]
    assert (rows["capacity_clamped"] == "max").all()
    # The limited steps are on/off means too: shorter runs, less heat than asked for.
    assert ((rows[CAPACITY_RATIO_KEY] > 0.0) & (rows[CAPACITY_RATIO_KEY] < 1.0)).all()
    assert ((rows[RUNTIME_FRACTION_KEY] > 0.0) & (rows[RUNTIME_FRACTION_KEY] < 1.0)).all()
    delivered = rows["Q_ref_iu [W]"].to_numpy()
    assert (delivered < REQUEST).all() and (delivered > 0.1 * REQUEST - 1e-9).all()
    assert rows["Q_max [W]"].to_numpy() == pytest.approx(delivered, rel=1e-12)
    assert rows["Q_unmet [W]"].to_numpy() == pytest.approx(REQUEST - delivered, rel=1e-9)
    assert (rows["Q_request [W]"] == -REQUEST).all()
    # The running machine's liquid sits on the limit, within the search's band.
    leaving = df[LEAVING_KEY].to_numpy()
    assert (leaving >= LIMIT - FREEZE_TOL_K).all()
    assert (leaving[limited] <= LIMIT + 0.05).all()


def test_the_ground_takes_the_mean_load_of_a_limited_step(cold_loop_run):
    _, df = cold_loop_run
    mean_load = (df["Q_ref_ground [W]"] - df["E_pmp [W]"]).to_numpy()
    assert df["Q_bhe [W]"].to_numpy() == pytest.approx(mean_load, rel=1e-12)
    # Less heat is taken in a limited step than in the unlimited ones before it.
    limited = (df["capacity_binding"] == SOURCE_FLUID_FREEZING).to_numpy()
    assert df.loc[limited, "Q_bhe [W]"].max() < df.loc[~limited, "Q_bhe [W]"].min()


def test_steady_point_on_a_prescribed_wall_stays_off():
    """The wall does not answer the load: the running machine is as cold at any smaller duty."""
    model = GroundSourceHeatPump(source_fluid="water")
    for request in (-1500.0, -300.0):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            row = model.analyze_steady(Q_r_iu=request, T0=0.0, T_a_room=20.0, T_bhe_wall=6.0)
        assert not row["hp_is_on"] and row["failure_reason"] == SOURCE_FLUID_FREEZING
        assert row["failure_detail"] == DETAIL_OUTLET
        assert row["Q_ref_iu [W]"] == 0.0 and row[RUNTIME_FRACTION_KEY] == 0.0
    # Half a kelvin warmer the minimum-speed machine keeps the limit, and the unit cycles.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        row = model.analyze_steady(Q_r_iu=-300.0, T0=0.0, T_a_room=20.0, T_bhe_wall=6.5)
    assert row["hp_is_on"] and row["capacity_clamped"] == "min" and row[LEAVING_KEY] >= LIMIT
