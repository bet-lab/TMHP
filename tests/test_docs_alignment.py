"""Guard documentation claims that mirror public model boundaries."""

from pathlib import Path

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
