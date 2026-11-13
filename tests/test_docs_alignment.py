"""Guard documentation claims that mirror public model boundaries."""

import re
import warnings
from pathlib import Path

import numpy as np
import pytest

from tmhp import (
    AirSourceHeatPump,
    AirSourceHeatPumpBoiler,
    GroundSourceHeatPump,
    GroundSourceHeatPumpBoiler,
    WaterSourceHeatPumpBoiler,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


def test_model_family_matrix_matches_public_time_boundaries() -> None:
    text = _read("docs/source/models/index.rst")

    assert "AirSourceHeatPumpBoiler" in text
    assert "``analyze_steady()``, ``analyze_dynamic()``, ``step()``" in text
    assert "point-state" in text and "``step()`` is intentionally unavailable" in text
    assert "WaterSourceHeatPumpBoiler" in text
    assert "``Q_r_iu > 0`` cooling, ``Q_r_iu < 0`` heating" in text

    assert "step" in AirSourceHeatPumpBoiler.__dict__
    assert "step" in GroundSourceHeatPumpBoiler.__dict__
    assert "step" not in WaterSourceHeatPumpBoiler.__dict__
    assert "step" not in AirSourceHeatPump.__dict__
    assert "step" not in GroundSourceHeatPump.__dict__


def test_docs_do_not_claim_unreleased_water_source_space_conditioning() -> None:
    docs = "\n".join(
        [
            _read("README.md"),
            _read("docs/source/index.rst"),
            _read("docs/source/models/index.rst"),
            _read("docs/source/concepts/cycle-architecture.rst"),
            _read("docs/source/_static/source_sink_matrix.svg"),
        ]
    )

    assert "Water-source space-conditioning is not a released public API" in docs
    assert "air, water, and ground source sides paired with DHW" not in docs
    assert "Every model in TMHP is the same closed refrigerant cycle" not in docs


# --- Result-key dictionary (docs/source/concepts/result-keys.rst) ---------------
#
# Every key a released family returns with its default options has to be named
# on that page. The check is one-directional and deliberately light: one default
# machine per family, a handful of steady points (running, off, below the minimum
# speed, above the maximum) and three dynamic steps. A key that only a rarer path
# produces is caught by the collection script the page was written from, not here.

RESULT_KEYS_PAGE = "docs/source/concepts/result-keys.rst"


def _documented_result_keys() -> set[str]:
    return set(re.findall(r"``([^`]+)``", _read(RESULT_KEYS_PAGE)))


def _space_results(model) -> tuple[set[str], set[str]]:
    capacity = model.hp_capacity
    steady: set[str] = set()
    for load in (-0.5 * capacity, -0.03 * capacity, -2.5 * capacity, 0.5 * capacity, 0.0):
        steady |= set(
            model.analyze_steady(Q_r_iu=load, T0=2.0 if load <= 0 else 35.0, T_a_room=20.0 if load <= 0 else 27.0)
        )
    frame = model.analyze_dynamic(
        simulation_period_sec=1800,
        dt_s=600,
        Q_r_iu_schedule=np.array([-0.5 * capacity, 0.0, -0.03 * capacity]),
        T0_schedule=np.full(3, 2.0),
    )
    return steady, set(frame.columns)


def _boiler_results(model, source: dict) -> tuple[set[str], set[str]]:
    capacity = model.hp_capacity
    steady: set[str] = set()
    for load in (0.6 * capacity, 0.03 * capacity, 2.5 * capacity, 0.0):
        steady |= set(model.analyze_steady(T_tank_w=45.0, Q_ref_tank=load, **source))
    frame = model.analyze_dynamic(
        simulation_period_sec=1800,
        dt_s=600,
        T_tank_w_init_C=50.0,
        dhw_usage_schedule=np.array([1.0e-4, 0.0, 0.0]),
        T0_schedule=np.full(3, 7.0),
    )
    return steady, set(frame.columns)


_RESULT_KEY_CASES = {
    "ASHP": lambda: _space_results(AirSourceHeatPump()),
    "GSHP": lambda: _space_results(GroundSourceHeatPump()),
    "ASHPB": lambda: _boiler_results(AirSourceHeatPumpBoiler(), {"T0": 7.0}),
    "GSHPB": lambda: _boiler_results(GroundSourceHeatPumpBoiler(), {"T_source": 10.0}),
    "WSHPB": lambda: _boiler_results(WaterSourceHeatPumpBoiler(), {"T_source": 10.0}),
}


@pytest.mark.parametrize("family", sorted(_RESULT_KEY_CASES))
def test_every_result_key_is_in_the_result_key_dictionary(family: str) -> None:
    documented = _documented_result_keys()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        steady, dynamic = _RESULT_KEY_CASES[family]()

    assert len(steady) > 50 and len(dynamic) > 50, "the probe returned too little to mean anything"
    missing = sorted((steady | dynamic) - documented)
    assert not missing, (
        f"{family} returns result keys that {RESULT_KEYS_PAGE} does not name: {missing}. "
        "Add them to the page (meaning, unit, sign, families)."
    )


def test_result_key_dictionary_names_the_keys_callers_branch_on() -> None:
    documented = _documented_result_keys()
    for key in (
        "failure_reason",
        "capacity_clamped",
        "capacity_binding",
        "runtime_fraction [-]",
        "Q_request [W]",
        "Q_unmet [W]",
        "Q_dhw_request [W]",
        "Q_dhw_unmet [W]",
        "E_cycling_loss [W]",
        "failure_detail",
    ):
        assert key in documented, key
