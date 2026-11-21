"""The study of the indoor coil's conductance with the wet coil (``validation/indoor_ua_wet_coil``, #220).

The study solves, from each catalogue cooling row's total capacity and sensible
heat ratio, the coil temperature and the conductance the model's wet coil needs
to print that row, runs the rows with that conductance, splits what is left of
the COP bias, and tests candidate rules (``validation/results/indoor_ua_wet_coil_once/``).
Checked here:

- the inversion: it is the inverse of the model's own coil;
- the committed coils and a sample of the model rows, rerun (``rel=1e-6``, as
  the other studies in ``tests/test_study_results_current.py``), with the dry
  and the heating pass equal to the shipped parity results;
- the tables and the manifest, recomputed from the committed rows;
- the record: made by the script as it is, from a commit, all of its files;
- what the study found, in the direction it is reported.
"""

from __future__ import annotations

import io
import json
import math
from pathlib import Path

import pandas as pd
import pytest
from validation.indoor_ua_wet_coil import simulate as study

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT = REPO_ROOT / "validation" / "results" / "indoor_ua_wet_coil_once"

HINT = "모델 동작이 바뀌었다. `python -m validation.indoor_ua_wet_coil.simulate` 로 이 연구를 다시 만들어라"

TABLES = (
    study.UA_POINTS_NAME,
    study.UA_UNITS_NAME,
    study.COOLING_NAME,
    study.BY_UNIT_NAME,
    study.BY_PR_NAME,
    study.BY_SPEED_NAME,
    study.HEATING_NAME,
    study.HEATING_UNITS_NAME,
    study.RULES_NAME,
    study.HELD_UA_POINTS_NAME,
    study.HELD_UA_UNITS_NAME,
    study.HELD_COOLING_NAME,
    study.HELD_RULES_NAME,
)


def _read(name: str) -> pd.DataFrame:
    return pd.read_csv(OUT / name, float_precision="round_trip")


@pytest.fixture(scope="module")
def committed() -> dict[str, pd.DataFrame]:
    return {name: _read(name) for name in TABLES}


def _mismatches(fresh: pd.DataFrame, old: pd.DataFrame) -> list[str]:
    fresh = pd.read_csv(io.StringIO(fresh.to_csv(index=False)), float_precision="round_trip")
    old = pd.read_csv(io.StringIO(old.to_csv(index=False)), float_precision="round_trip")
    assert list(fresh.columns) == list(old.columns) and len(fresh) == len(old), f"shape changed. {HINT}."
    out = []
    for i in range(len(fresh)):
        for column in fresh.columns:
            new, was = fresh.at[i, column], old.at[i, column]
            if pd.isna(new) and pd.isna(was):
                continue
            numbers = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (new, was))
            same = new == pytest.approx(was, rel=1e-6, abs=1e-9) if numbers else new == was
            if not same:
                out.append(f"row {i} {column}: committed {was!r}, now {new!r}")
    return out


# ---------------------------------------------------------------------------
# The inversion
# ---------------------------------------------------------------------------


def test_coil_from_ratio_is_the_inverse_of_the_models_coil():
    """FTXM35A at its rating point: 3.5 kW net at a ratio of 0.82, 27 / 19 degC, 0.2067 m3/s, a 20.67 W fan."""
    from validation.wet_coil_shr.simulate import catalogue_ratio_on_the_coil, room_humidity

    rh = room_humidity(27.0, 19.0)
    q_W, fan_W, airflow = 3500.0, 20.67, 0.2067
    coil = study._coil_of_published(27.0, rh, q_W, airflow, 0.82, fan_W)
    assert coil["solve"] == "ok"
    assert coil["T_adp_C"] == pytest.approx(12.80, abs=0.01)
    assert coil["contact_factor"] == pytest.approx(0.833, abs=1e-3) and coil["UA_W_K"] == pytest.approx(437.4, abs=0.1)
    assert coil["conductance_W_K"] == pytest.approx(coil["contact_factor"] * coil["C_air_W_K"], rel=1e-12)
    # The model's coil, given that conductance at that airflow and coil temperature, carries the heat at the ratio.
    on_coil = catalogue_ratio_on_the_coil(0.82, q_W, fan_W)
    assert study.coil_closure(27.0, rh, q_W + fan_W, airflow, coil) == pytest.approx(on_coil, abs=1e-9)
    # A higher ratio is a warmer coil and, for the same heat, a larger conductance.
    warmer = study._coil_of_published(27.0, rh, q_W, airflow, 0.82 + study.SHR_ROUNDING, fan_W)
    assert warmer["T_adp_C"] > coil["T_adp_C"] and warmer["UA_W_K"] > coil["UA_W_K"]
    # The fan heat on both capacities raises the ratio on the coil a little, and the conductance with it.
    assert study._coil_of_published(27.0, rh, q_W, airflow, 0.82, 0.0)["UA_W_K"] < coil["UA_W_K"]
    # Rows with no such coil say why.
    assert study._coil_of_published(27.0, rh, q_W, airflow, 1.0, 0.0)["solve"] == "ratio_one"
    assert study._coil_of_published(27.0, rh, q_W, airflow, 0.55, 0.0)["solve"] == "below_coil_line"
    starved = study._coil_of_published(27.0, rh, q_W, airflow / 2.0, 0.82, fan_W)
    assert starved["solve"] == "contact_factor_one" and math.isnan(starved["UA_W_K"])
    assert math.isnan(study.coil_closure(27.0, rh, q_W + fan_W, airflow / 2.0, starved))


def test_rules_give_back_what_they_were_fitted_to():
    units = pd.DataFrame(
        {
            "slug": ["a", "b", "c"],
            "nominal_kW": [2.0, 3.0, 5.0],
            "airflow_m3_s": [0.15, 0.20, 0.25],
            "UA_W_K": [300.0, 400.0, 500.0],
        }
    )
    units["conductance_W_K"] = [
        study.conductance_at_rating(ua, v) for ua, v in zip(units.UA_W_K, units.airflow_m3_s, strict=True)
    ]
    # Proportional to the airflow, exactly: the airflow rules recover it, with an exponent of 1.
    assert study.fit_rule("airflow", units)["k"] == pytest.approx(2000.0, rel=1e-12)
    power = study.fit_rule("airflow_power", units)
    assert power["n"] == pytest.approx(1.0, abs=1e-12)
    assert study.rule_ua("airflow_power", power, 3000.0, 0.20) == pytest.approx(400.0, rel=1e-12)
    assert study.rule_ua("constant", study.fit_rule("constant", units), 3000.0, 0.20) == pytest.approx(
        (300.0 * 400.0 * 500.0) ** (1.0 / 3.0), rel=1e-12
    )
    # A conductance on the driving difference the airflow cannot carry has no coil.
    assert math.isnan(study.rule_ua("conductance", {"k": 1.0e4}, 3000.0, 0.20))
    conductance = study.rule_ua("conductance", {"k": 150.0}, 3000.0, 0.20)
    assert study.conductance_at_rating(conductance, 0.20) == pytest.approx(150.0, rel=1e-12)


# ---------------------------------------------------------------------------
# The committed rows, rerun
# ---------------------------------------------------------------------------


def test_committed_coils_are_those_of_the_catalogues(committed):
    for slug, name in (("daikin_rxm35a", study.UA_POINTS_NAME), ("fujitsu_asuh12lpas", study.HELD_UA_POINTS_NAME)):
        old = committed[name]
        mismatches = _mismatches(study.catalogue_coils(slug), old[old.slug == slug].reset_index(drop=True))
        assert not mismatches, f"{name}: {HINT}.\n  " + "\n  ".join(mismatches[:15])
    # The unit tables are those of the committed rows.
    assert not _mismatches(study.ua_by_unit(committed[study.UA_POINTS_NAME]), committed[study.UA_UNITS_NAME])
    assert not _mismatches(study.ua_by_unit(committed[study.HELD_UA_POINTS_NAME]), committed[study.HELD_UA_UNITS_NAME])


def test_sample_rows_reproduce_and_the_shipped_passes_are_the_shipped_ones(committed):
    units, held_units = committed[study.UA_UNITS_NAME], committed[study.HELD_UA_UNITS_NAME]
    loo, for_held = study.leave_one_out(units), study.rules_for_held(units, held_units)
    mismatches = []
    # The rating point and the wettest row of the smallest and the largest unit; the rating point of a held unit.
    for slug, ids, table, of_units, rules in (
        ("daikin_rxm20a", (23, 36), study.COOLING_NAME, units, loo),
        ("daikin_rxm50a", (23, 36), study.COOLING_NAME, units, loo),
        ("fujitsu_asuh09lpas", (58,), study.HELD_COOLING_NAME, held_units, for_held),
    ):
        fresh = study.run_cooling(slug, study.cooling_conductances(slug, of_units, rules), point_ids=ids)
        old = committed[table]
        old = old[(old.slug == slug) & old.point_id.isin(ids)].reset_index(drop=True)
        mismatches += [f"{slug} {m}" for m in _mismatches(fresh, old)]
        assert study.parity_gate(fresh, "dry") <= 1e-6, slug
    # Two heating rows of the largest unit: the coldest, and 6 degC wet bulb outdoors at 20 degC indoors.
    fresh = study.run_heating("daikin_rxm50a", study.heating_conductances("daikin_rxm50a", units), point_ids=(37, 47))
    old = committed[study.HEATING_NAME]
    old = old[(old.slug == "daikin_rxm50a") & old.point_id.isin((37, 47))].reset_index(drop=True)
    mismatches += [f"heating {m}" for m in _mismatches(fresh, old)]
    assert study.parity_gate(fresh, "heat") <= 1e-6
    assert not mismatches, f"validation/results/indoor_ua_wet_coil_once: {HINT}.\n  " + "\n  ".join(mismatches[:15])


def test_tables_and_manifest_are_those_of_the_committed_rows(committed):
    points, held, heating = (committed[n] for n in (study.COOLING_NAME, study.HELD_COOLING_NAME, study.HEATING_NAME))
    units, held_units = committed[study.UA_UNITS_NAME], committed[study.HELD_UA_UNITS_NAME]
    assert len(points) == len(heating) == 180 and list(dict.fromkeys(points.slug)) == list(study.UNITS)
    assert len(held) == 144 and list(dict.fromkeys(held.slug)) == list(study.HELD_UNITS)
    assert set(points.status) == {"adopted"} and set(held.status) == {"hold"}
    assert study.parity_gate(points, "dry") <= 1e-6 and study.parity_gate(held, "dry") <= 1e-6
    assert study.parity_gate(heating, "heat") <= 1e-6
    # The wet pass is the wet pass of the study this one starts from.
    assert study.wet_study_difference(points, "wet_coil_shr_once.csv") <= 1e-6
    loo = study.with_model_results(study.leave_one_out(units), points)
    for_held = study.with_model_results(study.rules_for_held(units, held_units), held)
    for name, fresh in (
        (study.BY_UNIT_NAME, study.bias_by_unit(points)),
        (study.BY_PR_NAME, study.bias_by_pressure_ratio(points)),
        (study.BY_SPEED_NAME, study.bias_by_speed(points)),
        (study.HEATING_UNITS_NAME, study.heating_by_unit(heating)),
        (study.RULES_NAME, loo),
        (study.HELD_RULES_NAME, for_held),
    ):
        assert not _mismatches(fresh, committed[name]), name

    def strict(constant):
        raise AssertionError(f"{constant} in the manifest")

    manifest = json.loads((OUT / study.MANIFEST_NAME).read_text(encoding="utf-8"), parse_constant=strict)
    assert manifest["units"] == list(study.UNITS) and manifest["held_units"] == list(study.HELD_UNITS)
    assert manifest["cooling_rows"] == {**dict.fromkeys(study.UNITS, 36), **dict.fromkeys(study.HELD_UNITS, 72)}
    assert manifest["heating_rows"] == dict.fromkeys(study.UNITS, 36)
    assert manifest["shipped_passes_vs_committed_parity_max_rel_cop_difference"] <= 1e-6
    recomputed = {
        "headline": study.headline(units, points, heating),
        "headline_held": study.headline(held_units, held),
        "rules_leave_one_out": study.rule_summary(loo),
        "rules_held": study.rule_summary(for_held),
        "rules_fitted_on_the_adopted_units": {rule: study.fit_rule(rule, units) for rule in study.RULES},
    }
    for key, numbers in recomputed.items():
        fresh, stored = _flat(study._json_ready(numbers)), _flat(manifest[key])
        assert set(fresh) == set(stored), key
        for name, value in fresh.items():
            if value is None:
                assert stored[name] is None, (key, name)  # undefined: null, not NaN
            else:
                assert value == pytest.approx(stored[name], rel=1e-6, abs=1e-9), (key, name)


def _flat(numbers: dict, prefix: str = "") -> dict:
    out: dict = {}
    for key, value in numbers.items():
        if isinstance(value, dict):
            out.update(_flat(value, f"{prefix}{key}."))
        else:
            out[f"{prefix}{key}"] = value
    return out


def test_record_vouches_for_the_script_that_made_the_files():
    """The record names a commit that holds the script as it is now, run with nothing uncommitted."""
    from validation.parity.run import _git

    record = json.loads((OUT / "provenance.json").read_text(encoding="utf-8"))
    assert record["dirty"] is False and record["script"] == study.SCRIPT
    assert record["command"] == study.COMMAND  # the whole study, into its own folder
    assert record["study_script_uncommitted"] is False, f"made with an uncommitted script. {HINT}."
    names = (*TABLES, study.MANIFEST_NAME)
    assert record["outputs"] == sorted(f"validation/results/indoor_ua_wet_coil_once/{name}" for name in names)
    assert sorted(p.name for p in OUT.iterdir()) == sorted((*names, "provenance.json"))
    blob = _git("hash-object", study.SCRIPT)
    if not blob:
        pytest.skip("git is not available")
    assert record["study_script_blob"] == blob, f"the study script changed after the files were made. {HINT}."
    # Where the recorded commit is in this clone (not in a shallow one), it holds that same script.
    at_commit = _git("rev-parse", f"{record['git_head']}:{study.SCRIPT}")
    assert at_commit in ("", blob), "the record's commit holds another version of the study script"


def test_a_module_loaded_by_the_import_system_records_nothing(tmp_path):
    """Its source bytes are not known, so ``run`` called on it directly refuses before computing."""
    assert study._SOURCE is None
    out = tmp_path / "out"
    with pytest.raises(SystemExit, match="has to be run through main"):
        study.run(["--out-dir", str(out)])
    assert not out.exists()


def test_the_command_line_runs_the_whole_study_only(capsys):
    with pytest.raises(SystemExit):
        study.main(["--unit", "daikin_rxm20a"])
    assert "unrecognized arguments" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# What the study found
# ---------------------------------------------------------------------------


def test_the_published_ratio_fixes_one_conductance_per_unit(committed):
    units = committed[study.UA_UNITS_NAME].set_index("slug")
    coils = committed[study.UA_POINTS_NAME]
    # 172 of the 180 rows publish a ratio below 1 and all of those have a coil; the model's coil gives it back.
    assert units.rows_solved.sum() == 172 and units.rows_ratio_one.sum() == 8
    assert units.rows_below_coil_line.sum() == units.rows_contact_factor_one.sum() == 0
    assert units.shr_closure_abs_error_max.max() < 1e-9
    # One value per unit, to the two decimals of the ratio: the spread is that of the rounding, most rows'
    # rounding band holds the unit's value, and the value does not move with the outdoor temperature.
    assert (units.UA_spread_pct < units.rounding_half_width_pct).all() and units.UA_spread_pct.max() < 3.5
    assert units.rows_rounding_band_holds_UA.sum() == 156
    assert (units.UA_slope_pct_per_K_outdoor.abs() < 2.0 * units.UA_slope_se_pct_per_K_outdoor).all()
    assert units.UA_slope_pct_per_K_outdoor.abs().max() < 0.1
    # ... and for the largest unit falls a little with the indoor temperature (5 % over the 12 K of the table).
    assert units.UA_slope_pct_per_K_indoor["daikin_rxm50a"] == pytest.approx(-0.39, abs=0.01)
    # The values, 2.0 to 5.0 kW: not proportional to the nameplate, nor to the airflow.
    assert [round(v) for v in units.UA_W_K] == [382, 444, 432, 359, 337]
    assert [round(v, 3) for v in units.UA_per_W_nameplate] == [0.191, 0.178, 0.124, 0.086, 0.067]
    assert units.UA_per_m3_s.iloc[0] / units.UA_per_m3_s.iloc[-1] > 1.5
    assert [round(v, 2) for v in units.UA_shipped_over_solved] == [0.84, 0.90, 1.30, 1.87, 2.37]
    # The heat per kelvin of driving difference is within 2 % of 198 W/K for the four larger units.
    assert [round(v) for v in units.conductance_W_K] == [172, 199, 203, 196, 195]
    # The fan heat is a small part of it.
    assert ((units.UA_W_K / units.UA_no_fan_W_K - 1.0).between(0.02, 0.03)).all()
    assert coils.fan_W.between(17.0, 23.5).all()


def test_how_much_of_the_optimism_is_the_coil(committed):
    points = committed[study.COOLING_NAME]
    numbers = study.headline(committed[study.UA_UNITS_NAME], points, committed[study.HEATING_NAME])
    by_unit = committed[study.BY_UNIT_NAME].set_index("slug")
    assert numbers["cop_bias_pct_dry"] == pytest.approx(-1.78, abs=0.01)
    assert numbers["cop_bias_pct_wet"] == pytest.approx(12.83, abs=0.01)
    # With the conductance of the published ratio the ratio is reproduced and a third of the optimism goes.
    assert numbers["cop_bias_pct_wet_ua_shr"] == pytest.approx(8.32, abs=0.01)
    assert numbers["cop_bias_pct_points_taken_by_UA_of_shr"] == pytest.approx(4.52, abs=0.01)
    assert numbers["shr_abs_error_wet"] == pytest.approx(0.047, abs=1e-3)
    assert numbers["shr_abs_error_wet_ua_shr"] == pytest.approx(0.006, abs=1e-3)
    assert [round(v, 1) for v in by_unit.cop_bias_pct_wet[list(study.UNITS)]] == [3.1, 4.0, 10.5, 18.2, 28.4]
    assert [round(v, 1) for v in by_unit.cop_bias_pct_wet_ua_shr[list(study.UNITS)]] == [4.6, 4.8, 8.0, 9.9, 14.3]
    # What is left sits below the efficiency data's lowest pressure ratio, at speeds inside the data.
    assert numbers["rows_below_efficiency_data"] == numbers["rows_flagged_extrapolated"] == 70
    assert numbers["rows_on_floor"] == 11 and numbers["rows_below_efficiency_data_speed"] == 0
    assert 39.0 < numbers["rps_min"] < numbers["rps_max"] < 55.0
    assert numbers["cop_bias_pct_points_left_below_efficiency_data"] == pytest.approx(7.27, abs=0.01)
    assert numbers["cop_bias_pct_points_left_on_floor"] == pytest.approx(2.12, abs=0.01)
    assert numbers["cop_bias_pct_points_left_inside_efficiency_data"] == pytest.approx(1.05, abs=0.01)
    assert numbers["cop_bias_pct_on_floor"] > 30.0 > numbers["cop_bias_pct_below_efficiency_data"] > 15.0
    assert numbers["cop_bias_pct_inside_efficiency_data"] == pytest.approx(1.72, abs=0.01)
    # The model's efficiency is as high below the data as inside; the published power asks for a fifth more input.
    assert numbers["eta_overall_below_efficiency_data"] > numbers["eta_overall_inside_efficiency_data"] > 0.68
    assert numbers["compressor_input_over_needed_on_floor"] == pytest.approx(0.72, abs=0.01)
    assert numbers["compressor_input_over_needed_below_efficiency_data"] == pytest.approx(0.83, abs=0.01)
    assert numbers["compressor_input_over_needed_inside_efficiency_data"] == pytest.approx(0.99, abs=0.01)
    # The dry coil has the same slope with the pressure ratio; its small bias is two parts that cancel.
    bands = committed[study.BY_PR_NAME].set_index(["pass", "pressure_ratio_band"])
    assert bands.cop_bias_pct["dry", "below the efficiency data"] == pytest.approx(6.1, abs=0.05)
    assert bands.cop_bias_pct["dry", "inside the efficiency data"] == pytest.approx(-4.2, abs=0.05)
    assert bands.rows["dry", "below the efficiency data"] == 42 < bands.rows["wet", "below the efficiency data"] == 76
    # Inside the data the bias still runs with the size of the unit, as it does with the dry coil.
    inside = by_unit.cop_bias_pct_inside_efficiency_data_wet_ua_shr[list(study.UNITS)]
    assert inside.is_monotonic_increasing and inside.iloc[0] < -7.0 and inside.iloc[-1] > 10.0
    assert by_unit.cop_bias_pct_inside_efficiency_data_dry[list(study.UNITS)].is_monotonic_increasing
    # The COP alone would have 0.62 to 0.68 of that conductance, every unit, where the ratio is 0.05 to 0.06 low.
    assert by_unit.UA_scale_for_zero_cop_bias[list(study.UNITS)].between(0.615, 0.68).all()
    assert by_unit.T_evap_shift_for_zero_cop_bias_K[list(study.UNITS)].between(-4.0, -1.0).all()
    assert numbers["shr_error_wet_ua_shr_x70"] == pytest.approx(-0.054, abs=1e-3)
    # Passing the conductance alone also moves the rated speed: the rating point is solved on a dry coil.
    assert by_unit.rps_rated["daikin_rxm50a"] == pytest.approx(54.1, abs=0.05)
    assert by_unit.rps_rated_wet_ua_shr_rerated["daikin_rxm50a"] == pytest.approx(68.9, abs=0.05)
    assert by_unit.T_evap_rated_C_wet_ua_shr_rerated["daikin_rxm50a"] == pytest.approx(1.3, abs=0.05)
    assert numbers["cop_bias_pct_wet_ua_shr_rerated"] == pytest.approx(7.75, abs=0.01)


def test_heating_does_not_agree_with_the_conductance_of_the_ratio(committed):
    numbers = study.headline(
        committed[study.UA_UNITS_NAME], committed[study.COOLING_NAME], committed[study.HEATING_NAME]
    )
    by_unit = committed[study.HEATING_UNITS_NAME].set_index("slug")
    assert numbers["cop_bias_pct_heat"] == pytest.approx(-8.38, abs=0.01) and numbers["scored_heat"] == 180
    # A larger conductance moves the heating bias towards zero, a smaller one away from it ...
    assert numbers["cop_bias_pct_common_heat_ua_x50"] < numbers["cop_bias_pct_common_heat"] < 0.0
    assert numbers["cop_bias_pct_common_heat"] < numbers["cop_bias_pct_common_heat_ua_x200"] < 0.0
    # ... so the conductance of the ratio helps the two units it enlarges and hurts the three it reduces,
    # and seven rows of the two largest fall short of the published capacity with it.
    assert numbers["cop_bias_pct_heat_ua_shr"] == pytest.approx(-11.61, abs=0.01)
    assert numbers["scored_heat_ua_shr"] == 173
    change = by_unit.cop_bias_pct_heat_ua_shr - by_unit.cop_bias_pct_heat
    assert (change[["daikin_rxm20a", "daikin_rxm25a"]] > 0.0).all()
    assert (change[["daikin_rxm35a", "daikin_rxm42a", "daikin_rxm50a"]] < 0.0).all()


def test_candidate_rules_out_of_sample(committed):
    manifest = json.loads((OUT / study.MANIFEST_NAME).read_text(encoding="utf-8"))
    loo, held = manifest["rules_leave_one_out"], manifest["rules_held"]
    fitted = manifest["rules_fitted_on_the_adopted_units"]
    # Fitted to all five: 0.119 W/K per W, 389 W/K, 193 W/K per kelvin of driving difference -- and an
    # exponent on the airflow that is negative.
    assert fitted["nameplate"]["k"] == pytest.approx(0.119, abs=1e-3)
    assert fitted["constant"]["k"] == pytest.approx(388.8, abs=0.1)
    assert fitted["conductance"]["k"] == pytest.approx(192.6, abs=0.1)
    assert fitted["airflow_power"]["n"] == pytest.approx(-0.45, abs=0.01)
    # Leaving one unit out: a rule proportional to the nameplate is no better than the shipped one, and
    # one conductance for every size reproduces the ratio best.
    assert loo["nameplate"]["conductance_error_rms_pct"] > loo["shipped"]["conductance_error_rms_pct"] > 19.0
    assert loo["constant"]["conductance_error_rms_pct"] == pytest.approx(5.7, abs=0.05)
    assert loo["constant"]["shr_abs_error_mean"] == pytest.approx(0.020, abs=1e-3)
    assert loo["conductance"]["shr_abs_error_mean"] == pytest.approx(0.020, abs=1e-3)
    assert loo["shipped"]["shr_abs_error_mean"] == pytest.approx(0.047, abs=1e-3)
    assert loo["own"]["shr_abs_error_mean"] < 0.01 < loo["constant"]["shr_abs_error_mean"]
    for rule in ("airflow", "airflow_power", "constant", "conductance"):
        assert loo[rule]["shr_abs_error_mean"] < loo["shipped"]["shr_abs_error_mean"], rule
    # Its failure cases: the constant is 19 % high for the largest unit, the constant heat per kelvin doubles
    # the conductance of the smallest.
    rules = committed[study.RULES_NAME].set_index(["rule", "slug"])
    assert rules.UA_error_pct["constant", "daikin_rxm50a"] == pytest.approx(19.4, abs=0.1)
    assert rules.UA_error_pct["conductance", "daikin_rxm20a"] == pytest.approx(98.9, abs=0.1)
    assert rules.n["airflow_power", "daikin_rxm20a"] < -1.5 < rules.n["airflow_power", "daikin_rxm50a"] < 0.0
    # No rule takes the COP bias away: with the unit's own conductance it is +8.3 %.
    assert min(loo[rule]["cop_bias_pct_mean_of_units"] for rule in study.RULES) > 8.0
    # Out of family the rules do not hold: the two held units' tables imply 153 and 226 W/K, the rules 360 to
    # 420 W/K, and the ratio is 0.11 to 0.12 off with any of them (0.13 with the shipped rule, 0.05 at best).
    held_units = committed[study.HELD_UA_UNITS_NAME].set_index("slug")
    assert [round(v) for v in held_units.UA_W_K] == [153, 226]
    assert held_units.rows_below_coil_line.sum() == 20 and held_units.UA_spread_pct.min() > 20.0
    held_rules = committed[study.HELD_RULES_NAME]
    assert held_rules[held_rules.rule.isin(("constant", "conductance"))].UA_rule_W_K.between(360.0, 420.0).all()
    for rule in study.RULES:
        assert 0.10 < held[rule]["shr_abs_error_mean"] < held["shipped"]["shr_abs_error_mean"], rule
    assert held["own"]["shr_abs_error_mean"] == pytest.approx(0.052, abs=1e-3)
