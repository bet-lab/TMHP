"""How the parity harness scores a row: the electrical input it reads, and the rows it does not score."""

import math

import pytest
from validation.parity import run
from validation.parity.spec import Catalog, OperatingPoint


def test_catalogue_power_uses_actual_electrical_input(monkeypatch):
    catalog = Catalog(
        "fixture", "fixture", "fixture", "ASHP", "R32", 3.0, (OperatingPoint(1, 30, 26, 3, cop=5, mode="cooling"),)
    )
    monkeypatch.setattr(run, "_build_model", lambda *args: object())
    monkeypatch.setattr(
        run, "_run_point", lambda *args: {"failure_reason": "none", "cop_sys [-]": 4.9, "E_tot [W]": 600}
    )
    row = run.run_catalog(catalog).iloc[0]
    assert row["power_pred_kW"] == pytest.approx(0.6)
    assert row["power_pred_kW"] != pytest.approx(3 / 4.9)
    assert row["cop_basis"] == "final_indoor_air_sensible"


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
    monkeypatch, clamped, delivered_W, reason, scored
):
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
    monkeypatch.setattr(run, "_build_model", lambda *args: object())
    monkeypatch.setattr(run, "_run_point", lambda *args: row)
    out = run.run_catalog(catalog).iloc[0]
    assert out["failure_reason"] == reason and bool(out["usable"]) is scored
    assert out["cop_pred"] == pytest.approx(4.9) if scored else math.isnan(out["cop_pred"])
