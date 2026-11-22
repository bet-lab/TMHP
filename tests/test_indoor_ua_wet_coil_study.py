"""The study of the indoor coil's conductance with the wet coil (``validation/indoor_ua_wet_coil``, #220).

The study solves, from each catalogue cooling row's total capacity and sensible
heat ratio, the coil temperature and the conductance the model's wet coil needs
to print that row, runs the cooling and the heating rows with that conductance,
splits what is left of the COP bias along the axes of the efficiency map, tests
candidate rules, and runs each rule as the default
(``validation/results/indoor_ua_wet_coil_once/``). Checked here:

- the inversion: it is the inverse of the model's own coil;
- the committed coils and a sample of the model rows, rerun (``rel=1e-6``, as
  the other studies in ``tests/test_study_results_current.py``; saturation
  temperatures as ``tests/test_wet_coil_shr_study.py`` holds them), with the
  dry and the heating pass equal to the shipped parity results and the wet
  pass equal to the wet-coil study's;
- the tables and the manifest, recomputed from the committed rows;
- the record: made by the script as it is, from a commit, all of its files;
- what the study found, in the direction it is reported;
- the figures ``docs/source/validation/index.rst`` quotes from it.
"""

from __future__ import annotations

import io
import json
import math
import re
from pathlib import Path

import pandas as pd
import pytest
from validation.indoor_ua_wet_coil import simulate as study

REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS = REPO_ROOT / "validation" / "results"
OUT = RESULTS / "indoor_ua_wet_coil_once"

HINT = "모델 동작이 바뀌었다. `python -m validation.indoor_ua_wet_coil.simulate` 로 이 연구를 다시 만들어라"

TABLES = (
    study.UA_POINTS_NAME,
    study.UA_UNITS_NAME,
    study.COOLING_NAME,
    study.BY_UNIT_NAME,
    study.BY_PR_NAME,
    study.BY_SPEED_NAME,
    study.BY_TEVAP_NAME,
    study.BY_INDOOR_NAME,
    study.HEATING_NAME,
    study.HEATING_UNITS_NAME,
    study.RULES_NAME,
    study.DEFAULT_NAME,
    study.DEFAULT_SUMMARY_NAME,
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


#: Saturation temperatures of a rerun row [K]: a Celsius value has no relative scale, so they are compared
#: in kelvin, to the tolerance ``tests/test_wet_coil_shr_study.py`` holds its rerun rows to
#: (``RERUN_T_EVAP_ABS_K``; the files are written on macOS, and a rerun on Linux differs by up to
#: 2.5e-5 K there).
RERUN_T_SAT_ABS_K = 1e-4


def _mismatches(fresh: pd.DataFrame, old: pd.DataFrame, *, rerun: bool = False) -> list[str]:
    """Cells of ``fresh`` that differ from ``old``: ``rel=1e-6`` for numbers, everything else exactly.

    ``rerun``: the rows were solved again, on a platform that need not be the one the files were written
    on; the evaporating and condensing temperatures are then held to :data:`RERUN_T_SAT_ABS_K`.
    """
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
            saturation = rerun and column.startswith(("T_evap_C_", "T_cond_C_"))
            abs_ = RERUN_T_SAT_ABS_K if saturation else 1e-9
            same = new == pytest.approx(was, rel=1e-6, abs=abs_) if numbers else new == was
            if not same:
                out.append(f"row {i} {column}: committed {was!r}, now {new!r}")
    return out


# ---------------------------------------------------------------------------
# The inversion
# ---------------------------------------------------------------------------


def test_coil_from_ratio_is_the_inverse_of_the_models_coil():
    """FTXM35A at its rating point: 3.5 kW net at a ratio of 0.82, 27 / 19 degC, 0.2067 m3/s, a 20.67 W fan."""
    rh = study.room_humidity(27.0, 19.0)
    q_W, fan_W, airflow = 3500.0, 20.67, 0.2067
    coil = study._coil_of_published(27.0, rh, q_W, airflow, 0.82, fan_W)
    assert coil["solve"] == "ok"
    assert coil["T_adp_C"] == pytest.approx(12.80, abs=0.01)
    assert coil["contact_factor"] == pytest.approx(0.833, abs=1e-3) and coil["UA_W_K"] == pytest.approx(437.4, abs=0.1)
    assert coil["conductance_W_K"] == pytest.approx(coil["contact_factor"] * coil["C_air_W_K"], rel=1e-12)
    # The model's coil, given that conductance at that airflow and coil temperature, carries the heat at the ratio.
    on_coil = study.catalogue_ratio_on_the_coil(0.82, q_W, fan_W)
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


def test_the_study_computes_with_nothing_outside_its_record():
    """What a run records is the model, the harness, the catalogues and this script: it imports nothing else that computes.

    The reading of a row and the coil line are also in the wet-coil study; this study has its own, and the
    two give the same values.
    """
    import re

    from validation.wet_coil_shr import simulate as wet_study

    source = (REPO_ROOT / study.SCRIPT).read_text(encoding="utf-8")
    imported = set(re.findall(r"^\s*(?:from|import)\s+(validation[\w.]*)", source, flags=re.M))
    # The harness (within the recorded source state) and the helper that writes the record.
    assert imported == {"validation", "validation.parity.run", "validation.parity.spec"}, imported
    assert re.findall(r"^\s*from validation import (\w+)", source, flags=re.M) == ["study_provenance"]
    for note in ("WB 19 C, SHR 0.74, wet coil", "WB 19.4 C, SHR 0.66, wet coil", "outdoor WB"):
        assert study.parse_note(note) == wet_study.parse_note(note)
    for t_db, t_wb, shr in ((27.0, 19.0, 0.74), (20.0, 14.0, 0.93), (32.0, 24.0, 0.59), (26.7, 19.4, 0.66)):
        rh = study.room_humidity(t_db, t_wb)
        assert rh == wet_study.room_humidity(t_db, t_wb)
        assert study.apparatus_dew_point(t_db, rh, shr) == wet_study.apparatus_dew_point(t_db, rh, shr)
    assert math.isnan(study.apparatus_dew_point(27.0, study.room_humidity(27.0, 19.0), 0.55))
    assert study.catalogue_ratio_on_the_coil(0.74, 4200.0, 22.0) == wet_study.catalogue_ratio_on_the_coil(
        0.74, 4200.0, 22.0
    )


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


def test_map_position_is_the_data_domain_of_the_efficiency_map():
    """The chord of the map's (pressure ratio, evaporating temperature) hull, and the flag the model itself sets."""
    from tmhp.compressor_efficiency import DOMAIN, MapEfficiency, efficiency_extrapolated

    low, high = study.map_pressure_ratio_range(10.0)
    assert low == pytest.approx(1.631, abs=1e-3) and high == pytest.approx(5.07, abs=0.01)
    assert DOMAIN.hull_PR_Te.contains(low * 1.001, 10.0) and not DOMAIN.hull_PR_Te.contains(low * 0.999, 10.0)
    assert all(math.isnan(v) for v in study.map_pressure_ratio_range(40.0))  # no data that warm
    model = MapEfficiency("eta_cmp_isen")
    for pr, rps, t_evap in (
        (1.5, 42.0, 17.0),
        (1.9, 42.0, 14.0),
        (2.4, 48.0, 9.0),
        (2.4, 25.0, 9.0),
        (1.55, 45.0, 19.9),
    ):
        where = study.map_position("x", pr, rps, t_evap)
        assert (where["outside_map_pr_x"] or where["outside_map_speed_x"]) == efficiency_extrapolated(
            model, pr, rps, t_evap
        ), (pr, rps, t_evap)
    assert study.map_position("x", 1.5, 42.0, 17.0)["outside_map_pr_x"]  # the cycle's floor at a warm evaporator
    assert not study.map_position("x", 1.5, 42.0, 17.0)["outside_map_speed_x"]
    assert study.map_position("x", 2.4, 25.0, 9.0) == {
        "map_pr_min_x": pytest.approx(study.map_pressure_ratio_range(9.0)[0]),
        "outside_map_speed_x": True,
        "outside_map_pr_x": False,
    }
    # A row that was not scored is in neither.
    assert study.map_position("x", math.nan, math.nan, math.nan)["outside_map_pr_x"] is False


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
        mismatches += [f"{slug} {m}" for m in _mismatches(fresh, old, rerun=True)]
        assert study.parity_gate(fresh, "dry") <= 1e-6, slug
    # Two heating rows of the largest unit: the coldest, and 6 degC wet bulb outdoors at 20 degC indoors.
    fresh = study.run_heating(
        "daikin_rxm50a", study.heating_conductances("daikin_rxm50a", units, loo), point_ids=(37, 47)
    )
    old = committed[study.HEATING_NAME]
    old = old[(old.slug == "daikin_rxm50a") & old.point_id.isin((37, 47))].reset_index(drop=True)
    mismatches += [f"heating {m}" for m in _mismatches(fresh, old, rerun=True)]
    assert study.parity_gate(fresh, "heat") <= 1e-6
    # The rules as the default, the rating row and one heating row of the largest unit.
    as_default = study.default_conductances(units)
    fresh = study.run_defaults("daikin_rxm50a", as_default, point_ids={"cooling": (23,), "heating": (47,)})
    old = committed[study.DEFAULT_NAME]
    old = old[(old.slug == "daikin_rxm50a") & old.point_id.isin((23, 47))].reset_index(drop=True)
    mismatches += [f"default {m}" for m in _mismatches(fresh, old, rerun=True)]
    assert not mismatches, f"validation/results/indoor_ua_wet_coil_once: {HINT}.\n  " + "\n  ".join(mismatches[:15])


def test_tables_and_manifest_are_those_of_the_committed_rows(committed):
    points, held, heating = (committed[n] for n in (study.COOLING_NAME, study.HELD_COOLING_NAME, study.HEATING_NAME))
    units, held_units = committed[study.UA_UNITS_NAME], committed[study.HELD_UA_UNITS_NAME]
    assert len(points) == len(heating) == 180 and list(dict.fromkeys(points.slug)) == list(study.UNITS)
    assert len(held) == 144 and list(dict.fromkeys(held.slug)) == list(study.HELD_UNITS)
    assert set(points.status) == {"adopted"} and set(held.status) == {"hold"}
    assert study.parity_gate(points, "dry") <= 1e-6 and study.parity_gate(held, "dry") <= 1e-6
    assert study.parity_gate(heating, "heat") <= 1e-6
    # The wet pass is the wet pass of the study this one starts from, row for row.
    wet_study = pd.read_csv(RESULTS / "wet_coil_shr_once" / "wet_coil_shr_once.csv", float_precision="round_trip")
    both = points.merge(wet_study[["slug", "point_id", "cop_wet"]], on=["slug", "point_id"], suffixes=("", "_other"))
    assert len(both) == 180 and ((both.cop_wet - both.cop_wet_other).abs() <= 1e-6 * both.cop_wet_other).all()
    loo = study.with_model_results(study.leave_one_out(units), points, heating)
    for_held = study.with_model_results(study.rules_for_held(units, held_units), held)
    defaults = committed[study.DEFAULT_NAME]
    assert len(defaults) == len(study.RULES) * len(study.DEFAULT_PATHS) * 180
    # The default passes run the conductances the rules give when fitted on the five units.
    as_default = study.default_conductances(units).set_index(["rule", "slug"]).sort_index().UA_rule_W_K
    run_with = defaults.groupby(["rule", "slug"]).UA_W_K.agg(["min", "max"])
    assert (run_with["min"] == run_with["max"]).all()
    assert run_with["min"].to_dict() == pytest.approx(as_default.to_dict(), rel=1e-12)
    for name, fresh in (
        (study.BY_UNIT_NAME, study.bias_by_unit(points)),
        (study.BY_PR_NAME, study.bias_by_pressure_ratio(points)),
        (study.BY_SPEED_NAME, study.bias_by_speed(points)),
        (study.BY_TEVAP_NAME, study.bias_by_evaporating_temperature(points)),
        (study.BY_INDOOR_NAME, study.bias_by_indoor_condition(points)),
        (study.HEATING_UNITS_NAME, study.heating_by_unit(heating)),
        (study.RULES_NAME, loo),
        (study.DEFAULT_SUMMARY_NAME, study.default_summary(defaults, points, heating)),
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
    assert manifest["efficiency_map"] == study._json_ready(study.efficiency_map_data())
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

SMALL, LARGE = "daikin_rxm20a", "daikin_rxm50a"


def _numbers(committed) -> dict:
    return study.headline(committed[study.UA_UNITS_NAME], committed[study.COOLING_NAME], committed[study.HEATING_NAME])


def test_the_published_ratio_fixes_one_conductance_per_unit(committed):
    """Step 1 involves no compressor: these are the values of the first run of the study, to the digit."""
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
    assert units.UA_slope_pct_per_K_indoor[LARGE] == pytest.approx(-0.39, abs=0.01)
    # The values, 2.0 to 5.0 kW: not proportional to the nameplate, nor to the airflow.
    assert [round(v) for v in units.UA_W_K] == [382, 444, 432, 359, 337]
    assert [round(v, 3) for v in units.UA_per_W_nameplate] == [0.191, 0.178, 0.124, 0.086, 0.067]
    assert units.UA_per_m3_s.iloc[0] / units.UA_per_m3_s.iloc[-1] > 1.5
    assert [round(v, 2) for v in units.UA_shipped_over_solved] == [0.84, 0.90, 1.30, 1.87, 2.37]
    # The heat per kelvin of driving difference is within 2 % of 198 W/K for the four larger units.
    assert [round(v) for v in units.conductance_W_K] == [172, 199, 203, 196, 195]
    # The fan heat (the model's rated indoor fan power) is a small part of it.
    assert ((units.UA_W_K / units.UA_no_fan_W_K - 1.0).between(0.02, 0.03)).all()
    assert coils.fan_W.between(17.0, 23.5).all()


def test_what_the_conductance_of_the_ratio_does_in_cooling(committed):
    numbers = _numbers(committed)
    by_unit = committed[study.BY_UNIT_NAME].set_index("slug")
    slugs = list(study.UNITS)
    # The shipped passes: the dry coil of the parity results and the wet pass of the wet-coil study.
    assert numbers["cop_bias_pct_dry"] == pytest.approx(-12.85, abs=0.01)
    assert numbers["cop_bias_pct_wet"] == pytest.approx(-3.11, abs=0.01)
    assert numbers["scored_dry"] == numbers["scored_wet"] == numbers["scored_wet_ua_shr"] == 180
    # With the conductance of the published ratio the ratio is reproduced; the COP is lower, not closer on
    # average, and closer row by row.
    assert numbers["shr_abs_error_wet"] == pytest.approx(0.047, abs=1e-3)
    assert numbers["shr_abs_error_wet_ua_shr"] == pytest.approx(0.005, abs=1e-3)
    assert numbers["cop_bias_pct_wet_ua_shr"] == pytest.approx(-6.21, abs=0.01)
    assert numbers["cop_bias_pct_points_moved_by_UA_of_shr"] == pytest.approx(-3.10, abs=0.01)
    assert numbers["cop_MAPE_pct_wet_ua_shr"] == pytest.approx(8.22, abs=0.01)
    assert numbers["cop_MAPE_pct_wet_ua_shr"] < numbers["cop_MAPE_pct_wet"]
    # The run of the bias with the size of the unit closes by two fifths, not wholly.
    assert [round(v, 1) for v in by_unit.cop_bias_pct_wet[slugs]] == [-13.4, -12.2, -5.1, 2.5, 12.6]
    assert [round(v, 1) for v in by_unit.cop_bias_pct_wet_ua_shr[slugs]] == [-12.5, -11.6, -6.7, -3.1, 2.9]
    assert by_unit.cop_bias_pct_wet_ua_shr[slugs].is_monotonic_increasing
    assert numbers["cop_bias_pct_spread_of_units_wet"] == pytest.approx(26.08, abs=0.01)
    assert numbers["cop_bias_pct_spread_of_units_wet_ua_shr"] == pytest.approx(15.33, abs=0.01)
    assert numbers["cop_bias_spread_of_units_closed_by_UA_of_shr_pct"] == pytest.approx(41.2, abs=0.1)
    # It moves the two smaller units by a point: their conductance rises by a fifth and a tenth.
    assert (by_unit.cop_bias_pct_wet_ua_shr[slugs[:2]] - by_unit.cop_bias_pct_wet[slugs[:2]]).between(0.4, 1.1).all()
    assert by_unit.cop_bias_pct_wet_ua_shr_x200[slugs[:2]].between(-10.0, -9.0).all()
    assert by_unit.shr_error_wet_ua_shr_x200[slugs[:2]].between(0.03, 0.07).all()
    # The limit of a large conductance (a hundred times the shipped one: a contact factor of 1 to 1e-12)
    # leaves them 9 % low: no larger indoor conductance brings them to the catalogue. The bias rises with
    # the conductance over every scale run, for every unit, so the limit is the most it reaches.
    points = committed[study.COOLING_NAME]
    assert (points.UA_W_K_wet_ua_limit == study.LIMIT_SCALE * points.UA_W_K_wet).all()
    coils = committed[study.UA_POINTS_NAME]
    assert (points.UA_W_K_wet_ua_limit.to_numpy() / coils.C_air_W_K.to_numpy()).min() > 100.0
    assert numbers["scored_wet_ua_limit"] == numbers["scored_dry_ua_limit"] == 180
    assert [round(v, 1) for v in by_unit.cop_bias_pct_wet_ua_limit[slugs]] == [-9.3, -9.0, -3.1, 4.4, 14.2]
    ladder = ["wet_ua_shr_x70", "wet_ua_shr", "wet_ua_shr_x140", "wet_ua_shr_x200", "wet_ua_limit"]
    for slug in slugs:
        assert pd.Series([by_unit.at[slug, f"cop_bias_pct_{name}"] for name in ladder]).is_monotonic_increasing, slug
    # The dry coil of the parity path at the same limit: low for every unit.
    assert [round(v, 1) for v in by_unit.cop_bias_pct_dry_ua_limit[slugs]] == [-9.6, -9.9, -10.5, -6.6, -1.7]
    assert numbers["cop_bias_pct_dry_ua_limit"] == pytest.approx(-7.67, abs=0.01)
    assert numbers["cop_bias_pct_wet_ua_shr_x70"] < numbers["cop_bias_pct_wet_ua_shr"] < 0.0
    assert numbers["cop_bias_pct_wet_ua_shr"] < numbers["cop_bias_pct_wet_ua_shr_x140"] < 0.0
    assert numbers["shr_error_wet_ua_shr_x70"] == pytest.approx(-0.054, abs=1e-3)
    # Only the two larger units cross zero within the scales run: at 1.3 and 0.9 times their conductance.
    assert by_unit.UA_scale_for_zero_cop_bias[slugs[:3]].isna().all()
    assert by_unit.UA_scale_for_zero_cop_bias["daikin_rxm42a"] == pytest.approx(1.33, abs=0.01)
    assert by_unit.UA_scale_for_zero_cop_bias[LARGE] == pytest.approx(0.89, abs=0.01)
    # Passing the conductance alone re-solves the rated speed (the rating point is solved on a dry coil) and
    # leaves every operating point where it was: the efficiency map takes the speed in rev/s.
    assert by_unit.rps_rated[LARGE] == pytest.approx(51.5, abs=0.05)
    assert by_unit.rps_rated_wet_ua_shr_rerated[LARGE] == pytest.approx(64.1, abs=0.05)
    assert by_unit.T_evap_rated_C_wet_ua_shr_rerated[LARGE] == pytest.approx(1.3, abs=0.05)
    assert numbers["cop_max_abs_difference_rerated"] <= 1e-9
    assert numbers["T_evap_K_max_abs_difference_rerated"] <= 1e-9 and numbers["pr_max_abs_difference_rerated"] <= 1e-9
    assert numbers["cop_bias_pct_wet_ua_shr_rerated"] == pytest.approx(numbers["cop_bias_pct_wet_ua_shr"], abs=1e-9)


def test_where_the_bias_that_is_left_lies(committed):
    numbers = _numbers(committed)
    by_unit = committed[study.BY_UNIT_NAME].set_index("slug")
    slugs = list(study.UNITS)
    # Against the data of the efficiency map: every speed inside, 13 rows below its lowest pressure ratio at
    # their evaporating temperature (9 of them on the cycle's floor), and the model's own flag says the same.
    assert numbers["rows_outside_map_speed"] == 0 and 38.0 < numbers["rps_min"] < numbers["rps_max"] < 53.0
    assert numbers["rows_outside_map"] == numbers["rows_outside_map_pressure_ratio"] == 13
    assert numbers["rows_flagged_extrapolated"] == 13 and numbers["rows_on_floor"] == 9
    points = committed[study.COOLING_NAME]
    outside = points[points.outside_map_pr_wet_ua_shr]
    assert (outside.pr_wet_ua_shr < outside.map_pr_min_wet_ua_shr).all()
    # Those rows are not where the bias is: less low than the rows inside, 0.2 of the 6.2 points.
    assert numbers["cop_bias_pct_outside_map"] == pytest.approx(-3.15, abs=0.01)
    assert numbers["cop_bias_pct_inside_map"] == pytest.approx(-6.45, abs=0.01)
    assert numbers["cop_bias_pct_points_outside_map"] == pytest.approx(-0.23, abs=0.01)
    assert numbers["cop_bias_pct_points_inside_map"] == pytest.approx(-5.99, abs=0.01)
    # Along the pressure ratio the bias is level: no band above zero, none below -7.5 %, the floor included.
    bands = committed[study.BY_PR_NAME].set_index(["pass", "pressure_ratio_band"]).sort_index()
    five = ["on the floor", "floor to 1.7", "1.7 to 1.94", "1.94 to 2.3", "2.3 and above"]
    assert bands.rows["wet_ua_shr"][five].tolist() == [9, 18, 37, 61, 55]
    assert bands.cop_bias_pct["wet_ua_shr"][five].between(-7.5, -2.5).all()
    assert bands.cop_bias_pct["wet_ua_shr", "below 1.94"] == pytest.approx(-5.2, abs=0.05)
    assert bands.cop_bias_pct["wet_ua_shr", "1.94 and above"] == pytest.approx(-6.8, abs=0.05)
    assert abs(numbers["bias_pr_correlation"]) < 0.1 and abs(numbers["bias_T_evap_correlation"]) < 0.1
    # The model's efficiency falls towards a low pressure ratio, and the input the published COP leaves for
    # the compressor follows it: the model draws 3 to 10 % more in every band.
    assert bands.eta_overall["wet_ua_shr"][five].is_monotonic_increasing
    assert bands.eta_overall["wet_ua_shr", "on the floor"] == pytest.approx(0.489, abs=1e-3)
    assert bands.eta_overall["wet_ua_shr", "2.3 and above"] == pytest.approx(0.647, abs=1e-3)
    assert bands.compressor_input_over_needed["wet_ua_shr"][five].between(1.03, 1.10).all()
    # Along the speed in rev/s the low rows are the slow ones, which are the small units' ...
    speed = committed[study.BY_SPEED_NAME].set_index(["pass", "speed_scale", "speed_band"]).sort_index()
    by_speed = speed.loc[("wet_ua_shr", "rev/s")]
    assert by_speed.cop_bias_pct[["below 40", "40 to 44"]].max() < -9.0 < -3.5 < by_speed.cop_bias_pct["44 to 48"]
    assert by_speed.nominal_kW["below 40"] < 2.5 < 4.5 < by_speed.nominal_kW["48 and above"]
    # ... and along the speed over the rated one the sign is the other way (rows above it are the low ones).
    relative = speed.loc[("wet_ua_shr", "over rated")].cop_bias_pct
    assert relative[["below 0.9", "0.9 to 0.95"]].min() > -1.0 > -6.0 > relative["0.95 to 1"] > relative["1 to 1.05"]
    assert numbers["bias_speed_correlation"] > 0.4 and numbers["bias_relative_speed_correlation"] < -0.4
    # The tables are at one compressor frequency; the model's speed stays within 20 % of its rating row's.
    assert 0.80 < numbers["speed_over_rating_row_min"] < numbers["speed_over_rating_row_max"] < 1.12
    # Along the evaporating temperature it is not ordered.
    by_te = (
        committed[study.BY_TEVAP_NAME].set_index(["pass", "evaporating_temperature_band_C"]).sort_index().cop_bias_pct
    )
    six = by_te["wet_ua_shr"][["below 8", "8 to 10", "10 to 12", "12 to 14", "14 to 16", "16 and above"]]
    assert not six.is_monotonic_increasing and not six.is_monotonic_decreasing
    assert six.between(-9.0, -3.5).all()
    # What it follows is the unit, and within a unit the indoor condition: low in a dry room, at the
    # catalogue in the wettest one, where the dry coil's rows are level.
    indoor = committed[study.BY_INDOOR_NAME].set_index(["pass", "slug", "t_sink_C"]).sort_index().cop_bias_pct
    all_units = indoor["wet_ua_shr", "all"]
    assert all_units.is_monotonic_increasing
    assert all_units[20.0] == pytest.approx(-11.6, abs=0.05) and all_units[32.0] == pytest.approx(0.2, abs=0.05)
    assert indoor["dry", "all"].between(-15.0, -10.8).all()
    for slug in slugs:
        assert indoor["wet_ua_shr", slug].is_monotonic_increasing, slug
        assert indoor["wet_ua_shr", slug][32.0] - indoor["wet_ua_shr", slug][20.0] > 10.0, slug
    assert numbers["bias_shr_correlation"] == pytest.approx(-0.89, abs=0.01)
    assert numbers["bias_variance_explained_by_unit"] == pytest.approx(0.61, abs=0.01)
    assert numbers["bias_variance_explained_by_unit_and_indoor_condition"] == pytest.approx(0.91, abs=0.01)
    assert numbers["bias_variance_explained_by_map_axes"] == pytest.approx(0.64, abs=0.01)
    # By unit the compressor's input is 17 % above what the published COP leaves for it (2.0 kW) to 3 % below
    # (5.0 kW), at efficiencies of the map within 0.05 of each other.
    needed = by_unit.compressor_input_over_needed_wet_ua_shr[slugs]
    assert needed.is_monotonic_decreasing and needed.iloc[0] == pytest.approx(1.17, abs=0.01)
    assert needed.iloc[-1] == pytest.approx(0.97, abs=0.01)
    assert by_unit.eta_overall_wet_ua_shr[slugs].between(0.57, 0.63).all()
    # The dry coil's bias runs with the size of the unit as well: the run is not the wet coil's alone.
    assert by_unit.cop_bias_pct_dry[slugs].is_monotonic_increasing
    assert numbers["cop_bias_pct_spread_of_units_dry"] == pytest.approx(11.86, abs=0.01)


def test_heating_does_not_agree_with_the_conductance_of_the_ratio(committed):
    numbers = _numbers(committed)
    by_unit = committed[study.HEATING_UNITS_NAME].set_index("slug")
    assert numbers["cop_bias_pct_heat"] == pytest.approx(-11.45, abs=0.01) and numbers["scored_heat"] == 180
    # A larger conductance moves the heating bias towards zero, a smaller one away from it ...
    assert numbers["cop_bias_pct_common_heat_ua_x50"] < numbers["cop_bias_pct_common_heat"] < 0.0
    assert numbers["cop_bias_pct_common_heat"] < numbers["cop_bias_pct_common_heat_ua_x200"] < 0.0
    assert numbers["cop_bias_pct_heat_ua_x200"] == pytest.approx(-8.58, abs=0.01)
    # ... and not to it: at the limit of a large conductance every unit is still 7.5 to 9 % low, on all rows.
    assert numbers["cop_bias_pct_heat_ua_limit"] == pytest.approx(-8.15, abs=0.01)
    assert numbers["scored_heat_ua_limit"] == 180
    assert by_unit.cop_bias_pct_heat_ua_limit[list(study.UNITS)].between(-9.01, -7.5).all()
    assert numbers["cop_bias_pct_heat_ua_x200"] < numbers["cop_bias_pct_heat_ua_limit"]
    # ... so the conductance of the ratio helps the two units it enlarges and hurts the three it reduces,
    # and seven rows of the two largest fall short of the published capacity with it.
    assert numbers["cop_bias_pct_heat_ua_shr"] == pytest.approx(-14.61, abs=0.01)
    assert numbers["scored_heat_ua_shr"] == 173
    assert by_unit.scored_heat_ua_shr[["daikin_rxm42a", LARGE]].tolist() == [35, 30]
    change = by_unit.cop_bias_pct_heat_ua_shr - by_unit.cop_bias_pct_heat
    assert (change[[SMALL, "daikin_rxm25a"]] > 0.0).all()
    assert (change[["daikin_rxm35a", "daikin_rxm42a", LARGE]] < 0.0).all()
    # The heating bias ran less low for the larger units; with the conductance of the ratio it runs lower.
    assert [round(v, 1) for v in by_unit.cop_bias_pct_heat[list(study.UNITS)]] == [-13.9, -13.0, -10.7, -10.1, -9.6]
    assert [round(v, 1) for v in by_unit.cop_bias_pct_heat_ua_shr[list(study.UNITS)]] == [
        -12.4,
        -12.2,
        -13.0,
        -17.3,
        -19.0,
    ]


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
    assert loo["constant"]["shr_abs_error_mean"] == pytest.approx(0.019, abs=1e-3)
    assert loo["conductance"]["shr_abs_error_mean"] == pytest.approx(0.020, abs=1e-3)
    assert loo["shipped"]["shr_abs_error_mean"] == pytest.approx(0.047, abs=1e-3)
    assert loo["nameplate"]["shr_abs_error_mean"] == pytest.approx(0.070, abs=1e-3)
    assert loo["own"]["shr_abs_error_mean"] < 0.01 < loo["constant"]["shr_abs_error_mean"]
    for rule in ("airflow", "airflow_power", "constant", "conductance"):
        assert loo[rule]["shr_abs_error_mean"] < loo["shipped"]["shr_abs_error_mean"], rule
    # Its failure cases: the constant is 19 % high for the largest unit, the constant heat per kelvin doubles
    # the conductance of the smallest.
    rules = committed[study.RULES_NAME].set_index(["rule", "slug"]).sort_index()
    assert rules.UA_error_pct["constant", LARGE] == pytest.approx(19.4, abs=0.1)
    assert rules.UA_error_pct["conductance", SMALL] == pytest.approx(98.9, abs=0.1)
    assert rules.n["airflow_power", SMALL] < -1.5 < rules.n["airflow_power", LARGE] < 0.0
    # Cooling COP: every rule but the nameplate one narrows the run of the bias with the size of the unit,
    # every rule lowers the mean, and none brings it nearer the catalogue than the shipped rule has it.
    for rule in ("airflow", "airflow_power", "constant", "conductance"):
        assert loo[rule]["cop_bias_pct_spread_of_units"] < loo["shipped"]["cop_bias_pct_spread_of_units"], rule
    assert loo["nameplate"]["cop_bias_pct_spread_of_units"] > loo["shipped"]["cop_bias_pct_spread_of_units"]
    assert loo["constant"]["cop_bias_pct_spread_of_units"] == pytest.approx(18.5, abs=0.05)
    assert loo["constant"]["cop_bias_pct_mean_of_units"] == pytest.approx(-5.71, abs=0.01)
    assert loo["constant"]["cop_MAPE_pct_mean_of_units"] == pytest.approx(8.85, abs=0.01)
    for rule in study.RULES:
        assert loo[rule]["rows_scored"] == 180
        assert loo[rule]["cop_bias_pct_mean_of_units"] < loo["shipped"]["cop_bias_pct_mean_of_units"] < 0.0, rule
    # Heating: every rule is about three points lower than the shipped one, and some rows fall short.
    assert loo["shipped"]["heating_cop_bias_pct_mean_of_units"] == pytest.approx(-11.45, abs=0.01)
    for rule in study.RULES:
        assert -15.0 < loo[rule]["heating_cop_bias_pct_mean_of_units"] < -14.2, rule
    assert loo["constant"]["heating_cop_bias_pct_mean_of_units"] == pytest.approx(-14.57, abs=0.01)
    assert loo["constant"]["heating_rows_scored"] == 179 and loo["conductance"]["heating_rows_scored"] == 170
    # Out of family the rules do not hold: the two held units' tables imply 153 and 226 W/K, the rules 315 to
    # 420 W/K, and the ratio is 0.11 to 0.12 off with any of them (0.13 with the shipped rule, 0.05 at best).
    held_units = committed[study.HELD_UA_UNITS_NAME].set_index("slug")
    assert [round(v) for v in held_units.UA_W_K] == [153, 226]
    assert held_units.rows_below_coil_line.sum() == 20 and held_units.UA_spread_pct.min() > 20.0
    held_rules = committed[study.HELD_RULES_NAME]
    of_rules = held_rules[held_rules.rule.isin(study.RULES)]
    assert of_rules.UA_rule_W_K.between(314.0, 420.0).all()
    for rule in study.RULES:
        assert 0.10 < held[rule]["shr_abs_error_mean"] < held["shipped"]["shr_abs_error_mean"], rule
    assert held["shipped"]["shr_abs_error_mean"] == pytest.approx(0.133, abs=1e-3)
    assert held["own"]["shr_abs_error_mean"] == pytest.approx(0.053, abs=1e-3)


def test_no_rule_improves_the_shipped_parity_results_as_the_default(committed):
    summary = committed[study.DEFAULT_SUMMARY_NAME].set_index(["rule", "path", "slug"]).sort_index()
    # The shipped figures of the two parity paths of these units, and of the two together.
    before = summary.xs(("constant", "all"), level=("rule", "slug"))
    assert before.cop_bias_pct_shipped["dry"] == pytest.approx(-12.85, abs=0.01)
    assert before.cop_bias_pct_shipped["heat"] == pytest.approx(-11.45, abs=0.01)
    assert before.cop_bias_pct_shipped["parity"] == pytest.approx(-12.15, abs=0.01)
    assert before.cop_MAPE_pct_shipped["parity"] == pytest.approx(12.61, abs=0.01)
    assert before.scored_shipped["parity"] == before.points["parity"] == 360
    # ... which are those of the committed per-unit results.
    shipped = pd.concat([pd.read_csv(RESULTS / f"{slug}.csv") for slug in study.UNITS])
    relative = (shipped.cop_pred - shipped.cop_target) / shipped.cop_target * 100.0
    assert len(shipped) == 360 and shipped.usable.all()
    assert before.cop_bias_pct_shipped["parity"] == pytest.approx(relative.mean(), rel=1e-9)
    assert before.cop_MAPE_pct_shipped["parity"] == pytest.approx(relative.abs().mean(), rel=1e-9)
    # Every rule lowers both paths, on the rows it still scores, and raises the error of the two together.
    for rule in study.RULES:
        of_rule = summary.xs((rule, "all"), level=("rule", "slug"))
        for path in ("dry", "heat", "parity"):
            assert of_rule.cop_bias_pct[path] < of_rule.cop_bias_pct_shipped[path] - 2.5, (rule, path)
            assert of_rule.cop_bias_pct_common[path] < of_rule.cop_bias_pct_shipped_common[path] - 2.5, (rule, path)
            assert of_rule.cop_MAPE_pct[path] > of_rule.cop_MAPE_pct_shipped[path] + 2.0, (rule, path)
        assert -17.0 < of_rule.cop_bias_pct["parity"] < -15.5, rule
        assert of_rule.scored["dry"] <= 180 and of_rule.scored["wet"] == 180
    one = summary.xs(("constant", "all"), level=("rule", "slug"))
    assert one.cop_bias_pct["dry"] == pytest.approx(-18.11, abs=0.01) and one.scored["dry"] == 179
    assert one.cop_bias_pct["heat"] == pytest.approx(-14.60, abs=0.01) and one.scored["heat"] == 179
    assert one.cop_bias_pct["parity"] == pytest.approx(-16.35, abs=0.01) and one.scored["parity"] == 358
    assert one.cop_MAPE_pct["parity"] == pytest.approx(16.73, abs=0.01)
    # On the wet cooling path a rule lowers the bias as well, and the error with it for all but the nameplate rule.
    assert one.cop_bias_pct["wet"] == pytest.approx(-5.79, abs=0.01)
    assert one.cop_MAPE_pct["wet"] == pytest.approx(8.71, abs=0.01)
    assert one.cop_MAPE_pct["wet"] < one.cop_MAPE_pct_shipped["wet"]
    # As the default the reference state is re-solved: the largest unit's rated speed rises from 51 to 60 rev/s.
    assert summary.rps_rated["constant", "dry", LARGE] == pytest.approx(60.5, abs=0.05)
    # At the limit of a large indoor conductance both parity paths are still low, for every unit: the rules
    # lower them from a level no indoor conductance brings to the catalogue.
    by_unit = committed[study.BY_UNIT_NAME].set_index("slug")
    heat = committed[study.HEATING_UNITS_NAME].set_index("slug")
    assert (by_unit.cop_bias_pct_dry_ua_limit < -1.5).all() and (heat.cop_bias_pct_heat_ua_limit < -7.0).all()
    assert by_unit.cop_bias_pct_dry_ua_limit["all"] == pytest.approx(-7.67, abs=0.01)
    assert heat.cop_bias_pct_heat_ua_limit["all"] == pytest.approx(-8.15, abs=0.01)
    # By unit, one conductance moves the smallest unit up and the three larger ones down, the largest by 11 points.
    change = summary.cop_bias_pct["constant", "parity"] - summary.cop_bias_pct_shipped["constant", "parity"]
    assert change[SMALL] == pytest.approx(1.98, abs=0.01) and change[LARGE] == pytest.approx(-10.6, abs=0.05)
    assert (change[["daikin_rxm35a", "daikin_rxm42a", LARGE]] < -4.0).all()


# ---------------------------------------------------------------------------
# docs/source/validation/index.rst, "Indoor coil conductance"
# ---------------------------------------------------------------------------


def _signed(value: float, digits: int = 1) -> str:
    return f"{value:+.{digits}f}".replace("-", "−")


def _section() -> str:
    text = (REPO_ROOT / "docs" / "source" / "validation" / "index.rst").read_text(encoding="utf-8")
    match = re.search(r"^Indoor coil conductance\n=+\n(.*?)(?=^[^\n]+\n=+\n)", text, flags=re.M | re.S)
    assert match, "index.rst has no 'Indoor coil conductance' section"
    return match.group(1)


def test_docs_table_matches_the_study(committed):
    block = _section().split(".. list-table::")[1]
    body = block.split("\n\n", 2)[1]  # after the options, up to the first blank line that ends the table
    cells = [c.strip() for c in re.findall(r"^\s+(?:\* -|-) (.*)$", body, flags=re.M)]
    rows = [cells[i : i + 7] for i in range(7, len(cells), 7)]
    units = committed[study.UA_UNITS_NAME]
    assert [row[0] for row in rows] == list(units.unit)
    for row, unit in zip(rows, units.itertuples(), strict=True):
        expected = [
            unit.unit,
            f"{unit.nominal_kW:.1f} kW",
            f"{unit.airflow_m3_s:.3f} m³/s",
            f"{unit.rows_solved} of {unit.rows}",
            f"{unit.UA_W_K:.0f} W/K",
            f"{unit.UA_shipped_W_K:.0f} W/K",
            f"{unit.UA_shipped_over_solved:.2f}",
        ]
        assert row == expected, (row, expected)


def test_docs_prose_quotes_the_study(committed):
    flat = " ".join(_section().split())
    numbers = _numbers(committed)
    units = committed[study.UA_UNITS_NAME].set_index("slug")
    by_unit = committed[study.BY_UNIT_NAME].set_index("slug")
    heat = committed[study.HEATING_UNITS_NAME].set_index("slug")
    indoor = committed[study.BY_INDOOR_NAME].set_index(["pass", "slug", "t_sink_C"]).sort_index().cop_bias_pct
    bands = committed[study.BY_PR_NAME].set_index(["pass", "pressure_ratio_band"]).sort_index().loc["wet_ua_shr"]
    summary = committed[study.DEFAULT_SUMMARY_NAME].set_index(["rule", "path", "slug"]).sort_index()
    one = summary.xs(("constant", "all"), level=("rule", "slug"))
    manifest = json.loads((OUT / study.MANIFEST_NAME).read_text(encoding="utf-8"))
    loo, held, fitted = (
        manifest["rules_leave_one_out"],
        manifest["rules_held"],
        manifest["rules_fitted_on_the_adopted_units"],
    )
    held_units = committed[study.HELD_UA_UNITS_NAME]
    held_rules = committed[study.HELD_RULES_NAME]
    of_rules = held_rules[held_rules.rule.isin(study.RULES)].UA_rule_W_K
    coils = committed[study.UA_POINTS_NAME]
    five = ["on the floor", "floor to 1.7", "1.7 to 1.94", "1.94 to 2.3", "2.3 and above"]
    parity = [summary.cop_bias_pct[rule, "parity", "all"] for rule in study.RULES]
    small, large = by_unit.loc[SMALL], by_unit.loc[LARGE]
    second = by_unit.loc["daikin_rxm25a"]
    # "low for every unit", and "every rule lowers both parity paths".
    assert (by_unit.cop_bias_pct_dry_ua_limit[list(study.UNITS)] < 0.0).all()
    assert (heat.cop_bias_pct_heat_ua_limit[list(study.UNITS)] < 0.0).all()
    for rule in study.RULES:
        for path in ("dry", "heat"):
            assert summary.cop_bias_pct[rule, path, "all"] < summary.cop_bias_pct_shipped[rule, path, "all"], (
                rule,
                path,
            )
    # Rows of the pass with the solved conductance whose indoor airflow is below the published one.
    cooling = committed[study.COOLING_NAME].merge(
        units.airflow_m3_s.rename("published"), left_on="slug", right_index=True
    )
    low_airflow = int((cooling.airflow_m3_s_wet_ua_shr < 0.999 * cooling.published).sum())
    assert low_airflow == numbers["rows_indoor_airflow_below_published"]
    for sentence in (
        # What was measured.
        f"({coils.fan_W.min():.0f} to {coils.fan_W.max():.0f} W)",
        f"{numbers['rows_solved']} of the {numbers['cooling_rows']} rows publish a ratio below 1 and are solved; the "
        f"other {numbers['rows_ratio_one']} publish 1.00",
        f"its rows spread by {units.UA_spread_pct.min():.1f} to {units.UA_spread_pct.max():.1f} %, less than the "
        f"{units.rounding_half_width_pct.min():.1f} to {units.rounding_half_width_pct.max():.1f} % that half of the "
        f"ratio's last printed digit leaves",
        f"within {units.UA_slope_pct_per_K_outdoor.abs().max():.2f} % per kelvin",
        f"{units.UA_W_K.min():.0f} to {units.UA_W_K.max():.0f} W/K over 2.0 to 5.0 kW, where the shipped rule gives "
        f"{units.UA_shipped_W_K.min():.0f} to {units.UA_shipped_W_K.max():.0f} W/K",
        f"{units.UA_shipped_over_solved[SMALL]:.2f} of the solved value for the smallest unit and "
        f"{units.UA_shipped_over_solved[LARGE]:.2f} times it for the largest",
        # Cooling.
        f"falls from {numbers['shr_abs_error_wet']:.3f} to {numbers['shr_abs_error_wet_ua_shr']:.3f}",
        f"The COP bias goes from {_signed(numbers['cop_bias_pct_wet'])} % to "
        f"{_signed(numbers['cop_bias_pct_wet_ua_shr'])} % and the mean absolute error from "
        f"{numbers['cop_MAPE_pct_wet']:.1f} % to {numbers['cop_MAPE_pct_wet_ua_shr']:.1f} %",
        f"from {_signed(small.cop_bias_pct_wet)} % (2.0 kW) to {_signed(large.cop_bias_pct_wet)} % (5.0 kW) with the "
        f"shipped conductance and from {_signed(small.cop_bias_pct_wet_ua_shr)} % to "
        f"{_signed(large.cop_bias_pct_wet_ua_shr)} % with the solved one",
        f"{numbers['cop_bias_pct_spread_of_units_wet']:.1f} to {numbers['cop_bias_pct_spread_of_units_wet_ua_shr']:.1f} "
        f"points, {numbers['cop_bias_spread_of_units_closed_by_UA_of_shr_pct']:.0f} % of it",
        f"with a hundred times the shipped conductance, a contact factor of 1, their bias is "
        f"{_signed(small.cop_bias_pct_wet_ua_limit)} % and {_signed(second.cop_bias_pct_wet_ua_limit)} %",
        f"from {by_unit.rps_rated[LARGE]:.1f} to {by_unit.rps_rated_wet_ua_shr_rerated[LARGE]:.1f} rev/s",
        # Where the rest lies.
        f"{numbers['rows_outside_map']} of the {numbers['scored_wet_ua_shr']} rows lie outside the data of the "
        f"efficiency map",
        f"{numbers['rows_on_floor']} of them on the cycle's pressure-ratio floor",
        f"Their bias is {_signed(numbers['cop_bias_pct_outside_map'])} % against "
        f"{_signed(numbers['cop_bias_pct_inside_map'])} % for the rows inside, and they carry "
        f"{-numbers['cop_bias_pct_points_outside_map']:.1f} of the {-numbers['cop_bias_pct_wet_ua_shr']:.1f} points",
        f"between {_signed(bands.cop_bias_pct[five].max())} % and {_signed(bands.cop_bias_pct[five].min())} %",
        f"from {_signed(indoor['wet_ua_shr', 'all'][20.0])} % at 20 °C dry bulb / 14 °C wet bulb to "
        f"{_signed(indoor['wet_ua_shr', 'all'][32.0])} % at 32 °C / 24 °C",
        f"between {_signed(indoor['dry', 'all'].min())} % and {_signed(indoor['dry', 'all'].max())} %",
        f"accounts for {numbers['bias_variance_explained_by_unit_and_indoor_condition'] * 100:.0f} % of the variance "
        f"of the rows' bias, and a linear fit on the three axes of the map for "
        f"{numbers['bias_variance_explained_by_map_axes'] * 100:.0f} %",
        f"{(small.compressor_input_over_needed_wet_ua_shr - 1) * 100:.0f} % more than the published COP leaves for it "
        f"for the 2.0 kW unit and {(1 - large.compressor_input_over_needed_wet_ua_shr) * 100:.0f} % less for the "
        f"5.0 kW unit",
        # Heating.
        f"from {_signed(numbers['cop_bias_pct_heat'])} % to {_signed(numbers['cop_bias_pct_heat_ua_shr'])} %",
        f"{numbers['heating_rows'] - numbers['scored_heat_ua_shr']} rows of the two largest units fall short of the "
        f"published capacity",
        f"from {_signed(heat.cop_bias_pct_heat[SMALL])} % (2.0 kW) to {_signed(heat.cop_bias_pct_heat[LARGE])} % "
        f"(5.0 kW) with the shipped conductance, and from {_signed(heat.cop_bias_pct_heat_ua_shr[SMALL])} % to "
        f"{_signed(heat.cop_bias_pct_heat_ua_shr[LARGE])} % with the solved one",
        f"hundred times the shipped conductance the bias is {_signed(numbers['cop_bias_pct_heat_ua_limit'])} %, between "
        f"{_signed(heat.cop_bias_pct_heat_ua_limit[list(study.UNITS)].min())} % and "
        f"{_signed(heat.cop_bias_pct_heat_ua_limit[list(study.UNITS)].max())} % by unit",
        # Rules.
        f"within {loo['constant']['shr_abs_error_mean']:.3f} and {loo['conductance']['shr_abs_error_mean']:.3f} on "
        f"average, where the shipped rule has {loo['shipped']['shr_abs_error_mean']:.3f} and a refitted nameplate rule "
        f"{loo['nameplate']['shr_abs_error_mean']:.3f}; one conductance for every size is "
        f"{fitted['constant']['k']:.0f} W/K when fitted on all five",
        f"{held_units.UA_W_K.iloc[0]:.0f} and {held_units.UA_W_K.iloc[1]:.0f} W/K, the rules give "
        f"{of_rules.min():.0f} to {of_rules.max():.0f} W/K",
        f"{min(held[r]['shr_abs_error_mean'] for r in study.RULES):.2f} to "
        f"{max(held[r]['shr_abs_error_mean'] for r in study.RULES):.2f} off with any rule",
        # As the default.
        f"the cooling rows with the dry coil go from {_signed(one.cop_bias_pct_shipped['dry'])} % to "
        f"{_signed(one.cop_bias_pct['dry'])} % and the heating rows from {_signed(one.cop_bias_pct_shipped['heat'])} % "
        f"to {_signed(one.cop_bias_pct['heat'])} %",
        f"the bias goes from {_signed(one.cop_bias_pct_shipped['parity'])} % to {_signed(one.cop_bias_pct['parity'])} % "
        f"and the mean absolute error from {one.cop_MAPE_pct_shipped['parity']:.1f} % to "
        f"{one.cop_MAPE_pct['parity']:.1f} %, with {one.scored['parity']} of the {one.points['parity']} rows scored",
        f"between {_signed(max(parity))} % and {_signed(min(parity))} %",
        f"from {_signed(one.cop_bias_pct_shipped['wet'])} % to {_signed(one.cop_bias_pct['wet'])} % "
        f"(mean absolute error {one.cop_MAPE_pct_shipped['wet']:.1f} % to {one.cop_MAPE_pct['wet']:.1f} %)",
        f"the dry-coil cooling rows are still {_signed(numbers['cop_bias_pct_dry_ua_limit'])} % and the heating rows "
        f"{_signed(numbers['cop_bias_pct_heat_ua_limit'])} %, low for every unit",
        # Limits.
        f"on {low_airflow} of the {numbers['cooling_rows']} rows the model runs its indoor fan below the published "
        f"airflow",
    ):
        assert sentence in flat, f"index.rst lacks {sentence!r}"
