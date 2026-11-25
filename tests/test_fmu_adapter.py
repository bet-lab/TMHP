"""FMU adapter tests — FMI 2.0 co-sim slave wrapping step().

Requires the ``integrations`` extra (pythonfmu / fmpy); skipped otherwise so
the core test suite stays dependency-light::

    uv run --with pythonfmu --with fmpy python3 -m pytest tmhp/tests/test_fmu_adapter.py

Correctness is anchored to the public ``step()`` kernel: the slave
must reproduce a raw ``step()``-driven trajectory exactly (it only maps keys
and sanitizes the FMI boundary), and — via the full PythonFMU build + fmpy
round-trip — must reproduce the public ``analyze_dynamic()`` reference while
letting no NaN cross the boundary.
"""

from __future__ import annotations

import math
from xml.etree import ElementTree
from zipfile import ZipFile

import numpy as np
import pytest

pytest.importorskip("pythonfmu")

from pythonfmu.enums import Fmi2Status

from tmhp import AirSourceHeatPumpBoiler
from tmhp.equipment_presets import resolve_preset
from tmhp.integrations._fmi_common import VARIABLE_DESCRIPTIONS
from tmhp.integrations.fmu import TmhpAshpbSlave, _finite

from ._fmu_build import build_fmu, simulation_obstacle

PERIOD_S = 3 * 86400
DT = 600
T_SUR = 20.0
T_SUP = 15.0
T_TANK_INIT = 55.0
HPCAP = 15000.0
PRESET_VDISP_CC = 42.0
PRESET_FAN_M3S = 1.153
_OUTPUTS = ("E_cmp", "E_tot", "Q_ref_tank", "cop_sys", "T_tank_w")
_ANALYZE_DYNAMIC_OUTPUTS = {
    "E_cmp": "E_cmp [W]",
    "E_tot": "E_tot [W]",
    "Q_ref_tank": "Q_ref_tank [W]",
    "cop_sys": "cop_sys [-]",
    "T_tank_w": "T_tank_w [°C]",
}
_LEGACY_VARIABLE_ORDER = (
    "ref",
    "hp_capacity",
    "T_tank_w_init",
    "T_sur",
    "T0",
    "dhw_draw",
    "T_sup_w",
    "E_cmp",
    "E_tot",
    "Q_ref_tank",
    "cop_sys",
    "T_tank_w",
    "hp_is_on",
    "converged",
    "failure_reason",
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, 0.0),
        (float("nan"), 0.0),
        (float("inf"), 0.0),
        (float("-inf"), 0.0),
        (2.5, 2.5),
    ],
)
def test_fmi_numeric_boundary_outputs_are_finite(raw, expected):
    """FMI numeric outputs never expose NaN or infinities."""
    assert _finite(raw) == expected


def _schedule():
    t = np.arange(0, PERIOD_S, DT)
    hod = (t % 86400) / 3600.0
    T0 = 5.0 + 5.0 * np.sin(2 * np.pi * (hod - 9) / 24)
    dhw = np.where((np.abs(hod - 7) < 0.5) | (np.abs(hod - 20) < 0.5), 5e-5, 0.0)
    return t, T0, dhw


def _raw_step_trajectory() -> list[dict]:
    """Drive the public step() kernel directly — the ground truth the slave
    must reproduce."""
    t, T0, dhw = _schedule()
    m = AirSourceHeatPumpBoiler(ref="R32", hp_capacity=HPCAP)
    st = m.make_initial_state(T_TANK_INIT, 1.0)
    rows: list[dict] = []
    for n in range(len(t)):
        inp = {
            "n": n,
            "current_time_s": float(t[n]),
            "T0": float(T0[n]),
            "dV_mix_w_out": float(dhw[n]),
            "T_sup_w": T_SUP,
            "T_sur": T_SUR,
            "I_DN": 0.0,
            "I_dH": 0.0,
        }
        st, res = m.step(st, inp, DT)
        rows.append(res)
    return rows


def _analyze_dynamic_reference():
    """Run the public batch API over the same schedule used by the FMU smoke."""
    t, T0, dhw = _schedule()
    model = AirSourceHeatPumpBoiler(ref="R32", hp_capacity=HPCAP)
    return model.analyze_dynamic(
        simulation_period_sec=PERIOD_S,
        dt_s=DT,
        T_tank_w_init_C=T_TANK_INIT,
        dhw_usage_schedule=dhw,
        T0_schedule=T0,
        T_sup_w_schedule=np.full(len(t), T_SUP),
        T_sur_schedule=np.full(len(t), T_SUR),
    )


def _failure_reason(row: dict) -> str:
    value = row.get("failure_reason", "none")
    if value is None:
        return "none"
    return str(value)


def _make_slave(*, tank_temperature: float = T_TANK_INIT) -> TmhpAshpbSlave:
    slave = TmhpAshpbSlave(instance_name="tmhp_ashpb")
    slave.ref = "R32"
    slave.hp_capacity = HPCAP
    slave.T_tank_w_init = tank_temperature
    slave.T_sur = T_SUR
    slave.exit_initialization_mode()
    return slave


def test_fmu_slave_default_parameters_remain_model_defaults():
    """An empty preset keeps the legacy two-argument model construction."""
    slave = _make_slave()
    standalone = AirSourceHeatPumpBoiler(ref="R32", hp_capacity=HPCAP)

    assert slave.preset == ""
    assert slave._hp is not None
    for name in ("V_cmp_ref", "UA_tank_hx", "UA_ou_rated", "dV_fan_a_rated"):
        assert getattr(slave._hp, name) == getattr(standalone, name)


@pytest.mark.parametrize(
    ("field", "value"),
    [("V_cmp_disp_cc", 0.0), ("V_cmp_disp_cc", -1.0), ("dV_fan_a_rated", 0.0)],
)
def test_fmu_slave_rejects_invalid_preset_parameters(field, value):
    slave = TmhpAshpbSlave(instance_name="tmhp_ashpb_invalid_preset")
    slave.preset = "validated_rule_set"
    slave.V_cmp_disp_cc = PRESET_VDISP_CC
    slave.dV_fan_a_rated = PRESET_FAN_M3S
    setattr(slave, field, value)

    with pytest.raises(ValueError, match=field):
        slave.exit_initialization_mode()


def test_fmu_slave_validated_preset_reproduces_standalone_step_kernel():
    """The FMI 2.0 preset trajectory equals the same standalone model."""
    t, T0, dhw = _schedule()
    slave = TmhpAshpbSlave(instance_name="tmhp_ashpb_validated")
    slave.ref = "R32"
    slave.hp_capacity = HPCAP
    slave.T_tank_w_init = T_TANK_INIT
    slave.T_sur = T_SUR
    slave.preset = "validated_rule_set"
    slave.V_cmp_disp_cc = PRESET_VDISP_CC
    slave.dV_fan_a_rated = PRESET_FAN_M3S
    slave.exit_initialization_mode()

    kwargs = resolve_preset("validated_rule_set")(
        "R32",
        HPCAP,
        V_cmp_disp_cc=PRESET_VDISP_CC,
        dV_fan_a_rated=PRESET_FAN_M3S,
    )
    model = AirSourceHeatPumpBoiler(ref="R32", hp_capacity=HPCAP, **kwargs)
    state = model.make_initial_state(T_TANK_INIT)

    for n in range(12):
        inputs = {
            "n": n,
            "current_time_s": float(t[n]),
            "T0": float(T0[n]),
            "dV_mix_w_out": float(dhw[n]),
            "T_sup_w": T_SUP,
            "T_sur": T_SUR,
            "I_DN": 0.0,
            "I_dH": 0.0,
        }
        state, expected = model.step(state, inputs, DT)
        slave.T0 = inputs["T0"]
        slave.dhw_draw = inputs["dV_mix_w_out"]
        slave.T_sup_w = T_SUP

        assert slave.do_step(float(t[n]), DT) is True
        for output, result_key in _ANALYZE_DYNAMIC_OUTPUTS.items():
            expected_value = _finite(expected.get(result_key))
            assert getattr(slave, output) == pytest.approx(expected_value, rel=1e-12, abs=1e-9)


_OBSERVABLE = (*_OUTPUTS, "hp_is_on", "converged", "failure_reason")
_INVALID_INPUTS = [
    ("T0", float("nan")),
    ("dhw_draw", -1.0e-5),
    ("T_sup_w", float("inf")),
    ("T_sur", float("-inf")),
    ("RH_out", float("nan")),
    ("RH_out", -0.1),
    ("RH_out", 1.5),
    ("RH_out", 35.0),
]


def _observable(slave: TmhpAshpbSlave) -> dict[str, object]:
    """Every output value an FMI 2.0 importer can read back after a step."""
    return {name: getattr(slave, name) for name in _OBSERVABLE}


def _running_slave() -> TmhpAshpbSlave:
    """A slave after one accepted 60 s step with the compressor running."""
    slave = _make_slave(tank_temperature=40.0)
    slave.T0 = 2.0
    assert slave.do_step(0.0, 60.0) is True
    assert slave.hp_is_on is True
    assert slave.E_tot > 0.0
    slave.log_queue.clear()
    return slave


@pytest.mark.parametrize(
    ("field", "value", "step_size"),
    [*((field, value, 60.0) for field, value in _INVALID_INPUTS), ("T0", 2.0, 0.0), ("T0", 2.0, float("nan"))],
)
def test_fmu_slave_discards_rejected_step_without_observable_change(field, value, step_size):
    """A rejected step returns ``False`` (``fmi2Discard``) and leaves the FMU as it was.

    PythonFMU maps ``False`` to ``fmi2Discard`` and an exception to
    ``fmi2Fatal``; it has no ``fmi2Error`` path, so illegal step sizes are
    discarded as well. The state, the step counter and every output stay put,
    and the log names the rejected value.
    """
    slave = _running_slave()
    before = _observable(slave)
    state, n = slave._state, slave._n
    setattr(slave, field, value)

    assert slave.do_step(60.0, step_size) is False

    assert _observable(slave) == before
    assert slave._state is state
    assert slave._n == n
    assert len(slave.log_queue) == 1
    assert slave.log_queue[0].status == Fmi2Status.discard
    assert (field if step_size == 60.0 else "communicationStepSize") in slave.log_queue[0].msg


@pytest.mark.parametrize("rh_out", [None, 0.0, 0.35, 1.0])
def test_fmu_slave_outdoor_humidity_matches_step_kernel(rh_out: float | None) -> None:
    """Check the unconnected and connected inputs against the public step kernel."""
    slave = _make_slave(tank_temperature=40.0)
    slave.T0 = 2.0
    model = AirSourceHeatPumpBoiler(ref="R32", hp_capacity=HPCAP)
    state = model.make_initial_state(slave.T_tank_w_init, 1.0)
    inputs = {
        "n": 0,
        "current_time_s": 0.0,
        "T0": slave.T0,
        "dV_mix_w_out": 0.0,
        "T_sup_w": T_SUP,
        "T_sur": T_SUR,
        "I_DN": 0.0,
        "I_dH": 0.0,
    }
    if rh_out is not None:
        inputs["RH_out"] = slave.RH_out = rh_out

    _, expected = model.step(state, inputs, DT)

    assert slave.do_step(0.0, DT) is True
    assert slave.E_tot > 0.0
    for output, result_key in _ANALYZE_DYNAMIC_OUTPUTS.items():
        assert getattr(slave, output) == _finite(expected[result_key])


def test_fmu_slave_reproduces_step_kernel():
    """do_step() over the schedule reproduces the raw step() trajectory and
    lets no NaN cross the FMI boundary."""
    t, T0, dhw = _schedule()
    ref = _raw_step_trajectory()
    slave = _make_slave()

    for n in range(len(t)):
        slave.T0 = float(T0[n])
        slave.dhw_draw = float(dhw[n])
        slave.T_sup_w = T_SUP
        assert slave.do_step(float(t[n]), DT) is True

        for nm in _OUTPUTS:
            assert not math.isnan(getattr(slave, nm)), f"{nm} NaN at step {n}"

        assert slave.E_cmp == pytest.approx(ref[n]["E_cmp [W]"], rel=1e-12, abs=1e-9)
        assert slave.E_tot == pytest.approx(ref[n]["E_tot [W]"], rel=1e-12, abs=1e-9)
        assert slave.Q_ref_tank == pytest.approx(ref[n]["Q_ref_tank [W]"], rel=1e-12, abs=1e-9)
        assert slave.T_tank_w == pytest.approx(ref[n]["T_tank_w [°C]"], rel=1e-12, abs=1e-9)
        assert slave.hp_is_on == bool(ref[n]["hp_is_on"])
        assert slave.converged == bool(ref[n].get("converged", True))
        assert slave.failure_reason == _failure_reason(ref[n])
        # cop_sys is NaN-sanitized to 0.0 at the boundary when the cycle is off
        ref_cop = ref[n].get("cop_sys [-]", float("nan"))
        if not (ref_cop is None or math.isnan(float(ref_cop))):
            assert slave.cop_sys == pytest.approx(float(ref_cop), rel=1e-12, abs=1e-9)
        else:
            assert slave.cop_sys == 0.0


@pytest.fixture(scope="module")
def fmu_file(tmp_path_factory):
    """The FMI 2.0 archive PythonFMU builds from the adapter (built once per module)."""
    pytest.importorskip("fmpy")
    from pythonfmu.builder import FmuBuilder

    import tmhp.integrations.fmu as fmu_mod

    return build_fmu(FmuBuilder, fmu_mod.__file__, tmp_path_factory.mktemp("fmu2"))


def test_fmu_builds_and_validates(fmu_file):
    """PythonFMU build: the archive passes fmpy's validation and declares the documented interface."""
    from fmpy.validation import validate_fmu

    assert validate_fmu(str(fmu_file)) == []
    with ZipFile(fmu_file) as archive:
        root = ElementTree.fromstring(archive.read("modelDescription.xml"))
    variable_names = [scalar.attrib["name"] for scalar in root.findall("./ModelVariables/ScalarVariable")]
    assert variable_names[: len(_LEGACY_VARIABLE_ORDER)] == list(_LEGACY_VARIABLE_ORDER)
    assert variable_names[-4:] == ["preset", "V_cmp_disp_cc", "dV_fan_a_rated", "RH_out"]
    humidity = root.find("./ModelVariables/ScalarVariable[@name='RH_out']")
    assert humidity is not None
    assert humidity.attrib["causality"] == "input"
    assert humidity.attrib["variability"] == "continuous"
    real = humidity.find("Real")
    assert real is not None
    assert float(real.attrib["start"]) == -1.0
    descriptions = {
        scalar.attrib["name"]: scalar.attrib.get("description")
        for scalar in root.findall("./ModelVariables/ScalarVariable")
        if scalar.attrib["name"] in VARIABLE_DESCRIPTIONS
    }
    assert descriptions == VARIABLE_DESCRIPTIONS
    unit_names = {unit.attrib["name"] for unit in root.findall("./UnitDefinitions/Unit")}
    assert {"W", "degC", "m3/s", "cm3", "1"} <= unit_names
    units = {}
    for scalar in root.findall("./ModelVariables/ScalarVariable"):
        real = scalar.find("Real")
        if real is not None:
            units[scalar.attrib["name"]] = real.attrib["unit"]
    assert units["T0"] == "degC"
    assert units["RH_out"] == "1"
    assert units["dhw_draw"] == "m3/s"
    assert units["E_cmp"] == "W"
    assert units["cop_sys"] == "1"
    assert units["V_cmp_disp_cc"] == "cm3"
    assert units["dV_fan_a_rated"] == "m3/s"


def test_fmu_simulates_like_analyze_dynamic(fmu_file):
    """fmpy round-trip: the FMU runs the prescribed schedule to completion,
    emits no NaN, and matches analyze_dynamic() on the FMI boundary outputs.

    Skipped where fmpy cannot run the archive (no export library for the
    platform, e.g. macOS); Linux and Windows must run it."""
    import fmpy

    obstacle = simulation_obstacle(fmu_file)
    if obstacle is not None:
        pytest.skip(obstacle)

    t, T0, dhw = _schedule()
    # Prescribed inputs as (time, value) signals for fmpy.
    input_dtype = np.dtype([("time", "f8"), ("T0", "f8"), ("dhw_draw", "f8"), ("T_sup_w", "f8")])
    signals = np.zeros(len(t), dtype=input_dtype)
    signals["time"] = t
    signals["T0"] = T0
    signals["dhw_draw"] = dhw
    signals["T_sup_w"] = T_SUP

    result = fmpy.simulate_fmu(
        str(fmu_file),
        start_time=0.0,
        stop_time=PERIOD_S - DT,
        output_interval=DT,
        input=signals,
        output=list(_ANALYZE_DYNAMIC_OUTPUTS),
        start_values={
            "ref": "R32",
            "hp_capacity": HPCAP,
            "T_tank_w_init": T_TANK_INIT,
            "T_sur": T_SUR,
        },
    )

    for nm in _ANALYZE_DYNAMIC_OUTPUTS:
        assert not np.isnan(np.asarray(result[nm], dtype=float)).any(), f"{nm} NaN crossed FMI boundary"

    ref = _analyze_dynamic_reference()
    # fmpy records the initial value at t=0 (before the first do_step), so the
    # post-step trajectory is result[name][1:]; align it with analyze_dynamic().
    for out_name, ref_col in _ANALYZE_DYNAMIC_OUTPUTS.items():
        sim_after = np.asarray(result[out_name], dtype=float)[1:]
        ref_values = ref[ref_col].to_numpy(dtype=float)
        if out_name == "cop_sys":
            ref_values = np.where(np.isnan(ref_values), 0.0, ref_values)
        k = min(len(ref_values), len(sim_after))
        assert k >= len(ref_values) - 1, (
            f"FMU produced too few steps for {out_name} ({len(sim_after)} vs {len(ref_values)})"
        )
        np.testing.assert_allclose(
            sim_after[:k],
            ref_values[:k],
            rtol=1e-9,
            atol=1e-9,
            err_msg=out_name,
        )


def test_fmu_importer_receives_discard_for_a_rejected_step(fmu_file, capfd, tmp_path):
    """The built FMI 2.0 FMU discards a rejected step and keeps its outputs.

    Drives the archive through FMPy's C binding with FMPy's default logger, so
    the status code, the status queries, the outputs and the log line are the
    ones a real importer receives. Skipped where fmpy cannot run the archive
    (macOS); Linux and Windows must run it.
    """
    import fmpy
    from fmpy import read_model_description
    from fmpy.fmi1 import FMICallException
    from fmpy.fmi2 import FMU2Slave, fmi2Discard, fmi2LastSuccessfulTime, fmi2Terminated

    obstacle = simulation_obstacle(fmu_file)
    if obstacle is not None:
        pytest.skip(obstacle)

    md = read_model_description(str(fmu_file))
    vr = {variable.name: variable.valueReference for variable in md.modelVariables}
    fmu = FMU2Slave(
        guid=md.guid,
        unzipDirectory=fmpy.extract(str(fmu_file), unzipdir=tmp_path / "fmu2"),
        modelIdentifier=md.coSimulation.modelIdentifier,
        instanceName="tmhp_fmi2_rejected_step",
    )
    fmu.instantiate(loggingOn=True)  # PythonFMU forwards log messages only with loggingOn
    try:
        fmu.setReal([vr["T_tank_w_init"]], [40.0])
        fmu.setupExperiment(startTime=0.0)
        fmu.enterInitializationMode()
        fmu.setReal([vr["T0"]], [2.0])
        fmu.exitInitializationMode()
        fmu.doStep(currentCommunicationPoint=0.0, communicationStepSize=60.0)

        def read() -> tuple[list[float], list[bool], bytes]:
            return (
                fmu.getReal([vr[name] for name in _OUTPUTS]),
                fmu.getBoolean([vr["hp_is_on"], vr["converged"]]),
                fmu.getString([vr["failure_reason"]])[0],
            )

        before = read()
        assert before[1] == [True, True]
        capfd.readouterr()

        fmu.setReal([vr["RH_out"]], [1.5])
        with pytest.raises(FMICallException) as rejected:
            fmu.doStep(currentCommunicationPoint=60.0, communicationStepSize=60.0)
        assert rejected.value.status == fmi2Discard
        assert read() == before
        # PythonFMU reports the discarded step as the end of the run.
        assert fmu.getBooleanStatus(fmi2Terminated) is True
        assert fmu.getRealStatus(fmi2LastSuccessfulTime) == 60.0
        log = [line for line in capfd.readouterr().out.splitlines() if "rejected" in line]
        assert len(log) == 1
        assert "RH_out" in log[0]
    finally:
        fmu.freeInstance()
