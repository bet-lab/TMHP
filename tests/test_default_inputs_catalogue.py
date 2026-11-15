"""Catalogue default-input module (issue #86): values, evidence and refusals."""

import json
from pathlib import Path

import pytest

from tmhp.compressor_speed import RatedPoint, refrigerant_aware_displacement
from tmhp.default_inputs import DEFAULT_DISPLACEMENT_BASIS, estimate_displacement, estimate_max_airflow

ROOT = Path(__file__).resolve().parents[1]
AIRFLOW_ARCHIVE = ROOT / "validation/default_inputs/fan_airflow"
AIRFLOW_PACKAGE = ROOT / "src/tmhp/data/fan_airflow_regression.json"


def test_displacement_is_refrigerant_aware_displacement_at_the_catalogue_point():
    """Option A: rho_1 * dh at the basis's catalogue rating point, eta_vol * rps from the catalogue."""
    estimate = estimate_displacement(3500.0, "R32")
    expected = refrigerant_aware_displacement(
        3500.0,
        "R32",
        RatedPoint(T_evap_C=7.2, T_cond_C=54.4, rps=60.0, duty="cooling", basis="ARI"),
        dT_superheat=11.1,
        dT_subcool=8.3,
        eta_vol=0.887,
    )
    assert estimate.value == pytest.approx(expected, rel=2e-3)
    assert estimate.unit == "m3/rev" and estimate.key == DEFAULT_DISPLACEMENT_BASIS
    assert not estimate.extrapolated
    assert set(estimate.source_ids) == {"LG-ROTARY-2024", "SCI-ROTARY-2026"}


def test_displacement_follows_fluid_property_and_scales_with_capacity():
    r32, r290 = estimate_displacement(3500.0, "R32"), estimate_displacement(3500.0, "R290")
    assert r290.value / r32.value == pytest.approx(1.87, abs=0.05)  # rho_1 * dh of R290 is ~0.53 of R32
    assert estimate_displacement(7000.0, "R32").value == pytest.approx(2 * r32.value, rel=1e-9)
    assert estimate_displacement(500.0, "R32").extrapolated


def test_displacement_refuses_unknown_basis_and_bad_capacity():
    with pytest.raises(ValueError, match="Unknown displacement basis"):
        estimate_displacement(3500.0, "R32", basis="scroll_inverter")
    with pytest.raises(ValueError, match="positive and finite"):
        estimate_displacement(0.0, "R32")


def test_airflow_package_is_the_latest_release():
    package = json.loads(AIRFLOW_PACKAGE.read_text())
    release = json.loads((AIRFLOW_ARCHIVE / "releases.json").read_text())[-1]
    regression = json.loads((AIRFLOW_ARCHIVE / "regression.json").read_text())
    assert package["version"] == release["version"]
    assert package["data_sha256"] == release["data_sha256"] == regression["data_sha256"]
    for key, fit in regression["fits"].items():
        assert package["strata"][key]["selected_law"] == fit["selected_law"]
        assert package["strata"][key]["meets_shipping_gate"] == fit["meets_shipping_gate"]


def test_airflow_evaluates_the_packaged_law():
    package = json.loads(AIRFLOW_PACKAGE.read_text())
    law = package["strata"]["airflow|cooling|indoor|wall_mounted"]["selected_law"]
    estimate = estimate_max_airflow(3500.0, side="indoor", product="wall_mounted", mode="cooling")
    assert law["kind"] == "linear"
    assert estimate.value == pytest.approx(law["a"] * 3.5 + law["b"], rel=1e-12)
    assert estimate.unit == "m3/s"


def test_airflow_refuses_missing_or_ungated_strata():
    with pytest.raises(ValueError, match="No catalogue airflow stratum"):
        estimate_max_airflow(3500.0, side="indoor", product="cassette")
    with pytest.raises(ValueError, match="side must be"):
        estimate_max_airflow(3500.0, side="ceiling", product="wall_mounted")
    package = json.loads(AIRFLOW_PACKAGE.read_text())
    for key, entry in package["strata"].items():
        if not entry["meets_shipping_gate"]:
            _, mode, side, product = key.split("|")
            with pytest.raises(ValueError, match="shipping gate"):
                estimate_max_airflow(3500.0, side=side, product=product, mode=mode)


@pytest.mark.parametrize(
    ("side", "product", "mode", "capacity"),
    [("indoor", "water_to_air", "cooling", 10500.0), ("outdoor", "air_to_water", "heating", 8000.0)],
)
def test_new_product_class_strata_are_shipped(side, product, mode, capacity):
    """GSHP indoor and ASHPB outdoor classes have their own catalogue strata (#86 decision 4)."""
    estimate = estimate_max_airflow(capacity, side=side, product=product, mode=mode)
    assert 0.2 < estimate.value < 2.0
    assert not estimate.extrapolated
    assert len(estimate.source_ids) >= 2


def test_air_to_water_outdoor_airflow_is_shipped_with_spread_and_missed_criteria():
    """Owner decision 2026-11-15: the 6-manufacturer stratum ships as a central value with the manufacturer spread
    stated; the missed pre-set criteria stay in the reason."""
    estimate = estimate_max_airflow(8000.0, side="outdoor", product="air_to_water", mode="heating")
    package = json.loads(AIRFLOW_PACKAGE.read_text())
    entry = package["strata"]["airflow|heating|outdoor|air_to_water"]
    assert estimate.status == "shipped"
    assert len(entry["manufacturers"]) == 6
    assert estimate.assumptions[-1].startswith("Note: Shipped by owner decision")
    assert "manufacturer hold-out MAPE" in estimate.assumptions[-1]
    spread = estimate.assumptions[-2]
    assert spread.startswith("Spread between manufacturers")
    assert f"MAPE {entry['nested_manufacturer_cv']['MAPE_pct']:.1f} %" in spread
    biases = entry["manufacturer_holdout_bias_pct"].values()
    assert f"{min(biases):+.0f} to {max(biases):+.0f} %" in spread
    assert "takes precedence" in spread
    assert estimate_displacement(3500.0, "R32").status == "shipped"


def test_airflow_spread_line_is_stated_for_every_multi_maker_stratum():
    package = json.loads(AIRFLOW_PACKAGE.read_text())
    for key, entry in package["strata"].items():
        if not entry["meets_shipping_gate"]:
            continue
        _, mode, side, product = key.split("|")
        estimate = estimate_max_airflow(8000.0, side=side, product=product, mode=mode)
        has_spread = any(a.startswith("Spread between manufacturers") for a in estimate.assumptions)
        assert has_spread == bool(entry["manufacturer_holdout_bias_pct"])


def test_airflow_basis_reads_rated_words_anywhere_in_the_label():
    """'Standard air capacity' (Toshiba) and 'Air Flow Rate Rated' are rated labels, not maxima (2026-11-15)."""
    from validation.default_inputs.fan_airflow.build import airflow_basis

    assert airflow_basis("Standard air capacity") == "rated"
    assert airflow_basis("Rated") == "rated"
    assert airflow_basis("Heating High") == "explicit_max"
    assert airflow_basis("Fan, air flow, maximum") == "explicit_max"
    assert airflow_basis("High / Mid / Low") == "single_value"
    assert airflow_basis("") == "single_value"


def test_air_to_water_fit_excludes_panasonic_and_unaccepted_third_party_sources():
    """Panasonic stays out (it is the out-of-sample check); third-party-hosted PDFs are recorded but not fitted
    until the owner accepts the source (sources.json owner_accepted)."""
    regression = json.loads((AIRFLOW_ARCHIVE / "regression.json").read_text())
    fit = regression["fits"]["airflow|heating|outdoor|air_to_water"]
    sources = {s["source_id"]: s for s in json.loads((AIRFLOW_ARCHIVE / "sources.json").read_text())}
    assert "Panasonic" not in fit["manufacturers"]
    for source_id in fit["source_ids"]:
        source = sources[source_id]
        assert not source["evidence_type"].endswith("_third_party_host") or source.get("owner_accepted")
    rows = json.loads((AIRFLOW_ARCHIVE / "measurements.json").read_text())
    held = [r for r in rows if sources[r["source_id"]]["evidence_type"].endswith("_third_party_host")]
    unaccepted = [r for r in held if not sources[r["source_id"]].get("owner_accepted")]
    assert all(not r["eligible"] and "owner acceptance" in r["exclusion_reason"] for r in unaccepted)
    # Daikin accepted after a match on Daikin's own domain; LG rests on LG's own ErP fiches (2026-11-15).
    assert {"Daikin", "LG"} <= set(fit["manufacturers"])
    lg = sources["LG_GR_ERP_THERMAV_R32_MONOBLOC"]
    assert lg["role"] == "observations" and not lg["evidence_type"].endswith("_third_party_host")
    assert sources["LG_THERMAV_R32_MONOBLOC_PDB_5BPM5-01D"]["role"] == "cross_check"
    for source in sources.values():
        if source.get("owner_accepted"):
            assert source["owner_acceptance"].startswith("Owner decision")


def test_third_party_hosted_rows_are_not_fitted_until_owner_accepted():
    """The acceptance gate itself: same row, unaccepted vs accepted third-party source."""
    import csv

    from validation.default_inputs.fan_airflow.build import normalize

    path = AIRFLOW_ARCHIVE / "observations/DAIKIN_EEDEN22_EBLA09-16D.csv"
    obs = dict(next(csv.DictReader(path.open())), source_id="X")
    source = {"url": "u", "evidence_path": "e", "sha256": None, "evidence_type": "manufacturer_databook_pdf_third_party_host"}
    row = normalize(obs, source)
    assert not row["eligible"] and "owner acceptance" in row["exclusion_reason"]
    assert normalize(obs, {**source, "owner_accepted": True})["eligible"]
