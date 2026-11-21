"""How the parity harness scores a row: the electrical input it reads, and the rows it does not score."""

import math
from types import SimpleNamespace

import pytest
from validation.parity import run
from validation.parity.spec import Catalog, OperatingPoint


@pytest.fixture
def capacity_model(monkeypatch):
    """A ceiling probe at the cooling condition shared by these scoring tests."""

    def max_capacity(*, T0, T_a_room, mode):
        """Return a ceiling above the published 3 kW cooling duty."""
        assert (T0, T_a_room, mode) == (30, 26, "cooling")
        return {"Q_max [W]": 4_500.0}

    monkeypatch.setattr(run, "_build_model", lambda *args: SimpleNamespace(max_capacity=max_capacity))


def test_catalogue_power_uses_actual_electrical_input(monkeypatch, capacity_model):
    """Use the model's electrical input at the catalogue duty for the COP score."""
    catalog = Catalog(
        "fixture", "fixture", "fixture", "ASHP", "R32", 3.0, (OperatingPoint(1, 30, 26, 3, cop=5, mode="cooling"),)
    )
    monkeypatch.setattr(
        run, "_run_point", lambda *args: {"failure_reason": "none", "cop_sys [-]": 4.9, "E_tot [W]": 600}
    )
    row = run.run_catalog(catalog).iloc[0]
    assert row["power_pred_kW"] == pytest.approx(0.6)
    assert row["power_pred_kW"] != pytest.approx(3 / 4.9)
    assert row["cop_basis"] == "final_indoor_air_sensible"
    assert row["q_max_pred_kW"] == 4.5
    assert row["capacity_headroom"] == 1.5


@pytest.mark.parametrize(
    ("clamped", "delivered_W", "reason", "scored"),
    [
        (None, 3000.0, "none", True),
        ("max", 2990.0, "none", True),  # within 1 % of the published duty
        ("max", 2900.0, "capacity_short", False),
        # The request met as the mean of on/off cycling: the duty matches, the quantity does not.
        ("min", 3000.0, "cycling_mean", False),
    ],
)
def test_a_row_that_is_not_the_catalogue_operating_point_is_not_scored(
    monkeypatch, capacity_model, clamped, delivered_W, reason, scored
):
    """Keep the COP scoring decision based on the solve at the catalogue duty."""
    catalog = Catalog(
        "fixture", "fixture", "fixture", "ASHP", "R32", 3.0, (OperatingPoint(1, 30, 26, 3, cop=5, mode="cooling"),)
    )
    row = {
        "failure_reason": "none",
        "cop_sys [-]": 4.9,
        "E_tot [W]": 600,
        "Q_ref_iu [W]": delivered_W,
        "capacity_clamped": clamped,
    }
    monkeypatch.setattr(run, "_run_point", lambda *args: row)
    out = run.run_catalog(catalog).iloc[0]
    assert out["failure_reason"] == reason and bool(out["usable"]) is scored
    assert out["cop_pred"] == pytest.approx(4.9) if scored else math.isnan(out["cop_pred"])
