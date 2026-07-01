"""Tests for scripts/data/gen_refrigerant_data.py."""

import json

import CoolProp.CoolProp as CP
from scripts.data import gen_refrigerant_data as gen


def _patch_single_cycle_grid(monkeypatch) -> list[tuple[str, list]]:
    axes = [
        ("refrigerant", ["R32"]),
        ("T_source", [10.0]),
        ("T_sink", [45.0]),
        ("dT_subcool", [3.0]),
        ("dT_superheat", [5.0]),
        ("Q_cond", [14000.0]),
        ("UA_cond", [2500.0]),
        ("UA_evap", [2000.0]),
    ]
    monkeypatch.setattr(gen, "REFRIGERANTS", ["R32"])
    monkeypatch.setattr(gen, "T_SOURCES_C", [10.0])
    monkeypatch.setattr(gen, "T_SINKS_C", [45.0])
    monkeypatch.setattr(gen, "DT_SUBCOOL_K", [3.0])
    monkeypatch.setattr(gen, "DT_SUPERHEAT_K", [5.0])
    monkeypatch.setattr(gen, "Q_COND_W", [14000.0])
    monkeypatch.setattr(gen, "UA_COND_WK", [2500.0])
    monkeypatch.setattr(gen, "UA_EVAP_WK", [2000.0])
    monkeypatch.setattr(gen, "PARAM_AXES", axes)
    monkeypatch.setattr(gen, "SAT_CURVE_POINTS", 64)
    return axes


def test_supported_refrigerants() -> None:
    assert gen.REFRIGERANTS == ["R410A", "R134a", "R32", "R290"]


def test_ci_profile_uses_reduced_cycle_grid() -> None:
    full_axes = dict(gen.profile_param_axes("full"))
    ci_axes = dict(gen.profile_param_axes("ci"))

    assert ci_axes["refrigerant"] == full_axes["refrigerant"]
    assert len(ci_axes["T_source"]) < len(full_axes["T_source"])
    assert len(ci_axes["T_sink"]) < len(full_axes["T_sink"])
    assert ci_axes["dT_subcool"] == [3.0]
    assert ci_axes["dT_superheat"] == [5.0]


def test_saturation_curves_are_monotonic_in_pressure(monkeypatch) -> None:
    monkeypatch.setattr(gen, "SAT_CURVE_POINTS", 64)

    curves = gen.build_saturation_curves("R32")
    pressures_kpa = curves["p_sat"]

    assert pressures_kpa == sorted(pressures_kpa)
    assert len(curves["T"]) == len(pressures_kpa)
    assert curves["h_liq"][0] < curves["h_vap"][0]


def test_solve_cycle_returns_seven_display_points() -> None:
    result = gen.solve_cycle(
        "R32",
        t_source_c=10.0,
        t_sink_c=45.0,
        dt_subcool=3.0,
        dt_superheat=5.0,
        q_cond_w=14000.0,
        ua_cond=2500.0,
        ua_evap=2000.0,
        t_crit_k=CP.PropsSI("Tcrit", "R32"),
        t_min_k=CP.PropsSI("Tmin", "R32"),
    )

    assert result is not None
    assert len(result) == 7
    assert all(len(point) == 4 for point in result)


def test_writes_cycle_widget_payload(tmp_path, monkeypatch) -> None:
    axes = _patch_single_cycle_grid(monkeypatch)

    out_path = tmp_path / "cycle_data.json"
    gen.main(out_path)

    payload = json.loads(out_path.read_text())
    assert set(payload) == {"meta", "params", "limits", "saturation", "states"}
    assert payload["meta"]["key_axes"] == [name for name, _ in axes]
    assert payload["meta"]["point_order"] == [
        "1s",
        "1",
        "2",
        "2s",
        "3s",
        "3",
        "4",
    ]
    assert payload["meta"]["n_total"] == 1
    assert payload["meta"]["profile"] == "full"
    assert payload["params"]["refrigerant"] == ["R32"]
    assert payload["saturation"]["R32"]["p_sat"]


def test_writes_cycle_widget_payload_to_current_directory(tmp_path, monkeypatch) -> None:
    _patch_single_cycle_grid(monkeypatch)
    monkeypatch.chdir(tmp_path)

    gen.main("cycle_data.json")

    assert (tmp_path / "cycle_data.json").is_file()


def test_cli_accepts_optional_output_path(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["gen_refrigerant_data", "out/cycle_data.json"])

    args = gen._parse_args()

    assert args.out_path == "out/cycle_data.json"


def test_cli_accepts_data_profile(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["gen_refrigerant_data", "--profile", "ci"])

    args = gen._parse_args()

    assert args.profile == "ci"
