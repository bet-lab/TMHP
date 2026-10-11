"""Catalogue power cannot be inferred from requested coil load / air COP."""

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
