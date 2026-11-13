"""Traceability of the compressor displacement archive (issue #86, V3).

The packaged relation must be the latest archive release, every archived row must reproduce its own
effective speed eta_vol * rps = Q_nom / (V_cmp_ref * rho_1 * dh), and the archive's fluid-property
convention must be the one tmhp.compressor_speed uses, so V_cmp_ref = Q_nom / (rho_1 * dh * eta_vol * rps)
means the same thing in both places.
"""

import json
import math
from pathlib import Path

import pytest

from tmhp.compressor_speed import RatedPoint, refrigerant_aware_displacement

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "validation/default_inputs/compressor_displacement"
PACKAGED = ROOT / "src/tmhp/data/compressor_displacement_regression.json"


def _load(name: str):
    return json.loads((ARCHIVE / name).read_text())


def test_package_is_the_latest_release():
    packaged = json.loads(PACKAGED.read_text())
    release = _load("releases.json")[-1]
    regression = _load("regression.json")
    assert packaged["version"] == release["version"]
    assert packaged["data_sha256"] == release["data_sha256"] == regression["data_sha256"]
    for basis, fit in regression["normalized_fits"].items():
        shipped = packaged["bases"][basis]
        assert shipped["eta_vol_rps_per_f"] == fit["selected_law"]["eta_vol_rps_per_f"]
        assert shipped["n"] == fit["n"]
        assert shipped["meets_shipping_gate"] == fit["meets_shipping_gate"]


def test_every_row_reproduces_its_effective_speed():
    for row in _load("measurements.json"):
        q_from_row = row["eta_vol_rps"] * row["value"] * 1e-6 * row["rho_1_kg_m3"] * row["dh_J_kg"]
        assert math.isclose(q_from_row, row["capacity_W"], rel_tol=1e-9), row["record_id"]
        assert math.isclose(row["eta_vol_rps_per_f"] * row["frequency_Hz"], row["eta_vol_rps"], rel_tol=1e-12)


def test_inverter_twin_basis_meets_the_shipping_gate():
    basis = json.loads(PACKAGED.read_text())["bases"]["rotary_twin_inverter_60rps"]
    assert basis["meets_shipping_gate"]
    assert basis["n_families"] >= 3 and len(basis["manufacturers"]) >= 2
    assert 0.80 < basis["eta_vol_rps_per_f"] < 0.95


@pytest.mark.parametrize("record_id", ["lg:ARI60:DST134MB", "sci:ARI60:SVB130FBBMT", "sci:ASRET60:SNB110FGYMT"])
def test_archive_property_convention_matches_compressor_speed(record_id):
    """refrigerant_aware_displacement with the row's rating point and eta_vol*rps returns the printed V_cmp_ref."""
    row = next(r for r in _load("measurements.json") if r["record_id"] == record_id)
    rated_point = RatedPoint(
        T_evap_C=row["T_evap_C"],
        T_cond_C=row["T_cond_C"],
        rps=row["frequency_Hz"],
        duty="cooling",
        basis=row["protocol"],
    )
    V_cmp_ref = refrigerant_aware_displacement(
        row["capacity_W"],
        row["refrigerant_normalized"],
        rated_point,
        dT_superheat=row["T_suction_C"] - row["T_evap_C"],
        dT_subcool=row["T_cond_C"] - row["T_liquid_C"],
        eta_vol=row["eta_vol_rps_per_f"],
    )
    assert V_cmp_ref * 1e6 == pytest.approx(row["value"], rel=1e-6)
