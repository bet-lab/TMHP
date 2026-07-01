"""FMI 3.0 co-simulation adapter tests for the ASHPB ``step()`` boundary."""

from __future__ import annotations

import math
from xml.etree import ElementTree
from zipfile import ZipFile

import numpy as np
import pytest

pytest.importorskip("pythonfmu3")

from pythonfmu3 import Fmi3Status

from tmhp import AirSourceHeatPumpBoiler
from tmhp.integrations.fmu3 import TmhpAshpbFmi3Slave, _finite

PERIOD_S = 3 * 86400
DT = 600
T_SUR = 20.0
T_SUP = 15.0
T_TANK_INIT = 55.0
HPCAP = 15000.0
_OUTPUTS = ("E_cmp", "E_tot", "Q_ref_tank", "cop_sys", "T_tank_w")
_ANALYZE_DYNAMIC_OUTPUTS = {
    "E_cmp": "E_cmp [W]",
    "E_tot": "E_tot [W]",
    "Q_ref_tank": "Q_ref_tank [W]",
    "cop_sys": "cop_sys [-]",
    "T_tank_w": "T_tank_w [°C]",
}


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
def test_fmi3_numeric_boundary_outputs_are_finite(raw: float | None, expected: float) -> None:
    """FMI 3.0 numeric outputs never expose NaN or infinities."""
    assert _finite(raw) == expected


def _schedule() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return a deterministic three-day weather and DHW schedule."""
    t = np.arange(0, PERIOD_S, DT)
    hod = (t % 86400) / 3600.0
    T0 = 5.0 + 5.0 * np.sin(2 * np.pi * (hod - 9) / 24)
    dhw = np.where((np.abs(hod - 7) < 0.5) | (np.abs(hod - 20) < 0.5), 5e-5, 0.0)
    return t, T0, dhw


def _raw_step_trajectory() -> list[dict]:
    """Drive the public ``step()`` kernel directly."""
    t, T0, dhw = _schedule()
    model = AirSourceHeatPumpBoiler(ref="R32", hp_capacity=HPCAP)
    state = model.make_initial_state(T_TANK_INIT, 1.0)
    rows: list[dict] = []
    for n in range(len(t)):
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
        state, res = model.step(state, inputs, DT)
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
    """Return the normalized failure reason for a step result row."""
    value = row.get("failure_reason", "none")
    if value is None:
        return "none"
    return str(value)


def _make_slave() -> TmhpAshpbFmi3Slave:
    """Create an initialized FMI 3.0 slave with deterministic parameters."""
    slave = TmhpAshpbFmi3Slave(instance_name="tmhp_ashpb_fmi3")
    slave.ref = "R32"
    slave.hp_capacity = HPCAP
    slave.T_tank_w_init = T_TANK_INIT
    slave.T_sur = T_SUR
    slave.exit_initialization_mode()
    return slave


@pytest.mark.parametrize(
    ("field", "value", "step_size"),
    [
        ("T0", float("nan"), DT),
        ("dhw_draw", -1.0e-5, DT),
        ("T_sup_w", float("inf"), DT),
        ("T_sur", float("-inf"), DT),
        ("T0", 7.0, 0.0),
    ],
)
def test_fmu3_slave_rejects_invalid_importer_inputs(
    field: str,
    value: float,
    step_size: float,
) -> None:
    """Invalid importer inputs discard the FMI 3.0 step before state advance."""
    slave = _make_slave()
    setattr(slave, field, value)

    result = slave.do_step(0.0, step_size)

    assert result.status == Fmi3Status.discard
    assert result.earlyReturn is True
    assert slave.converged is False
    assert slave.failure_reason == "invalid_input"
    assert slave.hp_is_on is False
    assert slave._n == 0


def test_fmu3_slave_reproduces_step_kernel() -> None:
    """``do_step()`` reproduces the raw ``step()`` trajectory."""
    t, T0, dhw = _schedule()
    ref = _raw_step_trajectory()
    slave = _make_slave()

    for n in range(len(t)):
        slave.T0 = float(T0[n])
        slave.dhw_draw = float(dhw[n])
        slave.T_sup_w = T_SUP
        result = slave.do_step(float(t[n]), DT)

        assert result.status == Fmi3Status.ok
        assert result.earlyReturn is False
        for name in _OUTPUTS:
            assert not math.isnan(getattr(slave, name)), f"{name} NaN at step {n}"

        assert slave.E_cmp == pytest.approx(ref[n]["E_cmp [W]"], rel=1e-12, abs=1e-9)
        assert slave.E_tot == pytest.approx(ref[n]["E_tot [W]"], rel=1e-12, abs=1e-9)
        assert slave.Q_ref_tank == pytest.approx(ref[n]["Q_ref_tank [W]"], rel=1e-12, abs=1e-9)
        assert slave.T_tank_w == pytest.approx(ref[n]["T_tank_w [°C]"], rel=1e-12, abs=1e-9)
        assert slave.hp_is_on == bool(ref[n]["hp_is_on"])
        assert slave.converged == bool(ref[n].get("converged", True))
        assert slave.failure_reason == _failure_reason(ref[n])
        ref_cop = ref[n].get("cop_sys [-]", float("nan"))
        if not (ref_cop is None or math.isnan(float(ref_cop))):
            assert slave.cop_sys == pytest.approx(float(ref_cop), rel=1e-12, abs=1e-9)
        else:
            assert slave.cop_sys == 0.0


def test_fmu3_builds_and_simulates(tmp_path) -> None:
    """PythonFMU3 build + FMPy round-trip validates the FMI 3.0 artifact."""
    fmpy = pytest.importorskip("fmpy")
    from fmpy.validation import validate_fmu
    from pythonfmu3.builder import FmuBuilder

    import tmhp.integrations.fmu3 as fmu3_mod

    fmu_file = FmuBuilder.build_FMU(fmu3_mod.__file__, dest=str(tmp_path))
    assert fmu_file is not None
    assert validate_fmu(str(fmu_file)) == []
    with ZipFile(fmu_file) as archive:
        root = ElementTree.fromstring(archive.read("modelDescription.xml"))
    assert root.attrib["fmiVersion"] == "3.0"
    unit_names = {
        unit.attrib["name"]
        for unit in root.findall("./UnitDefinitions/Unit")
    }
    assert {"W", "s", "degC", "m3/s", "1"} <= unit_names
    units = {}
    for variable in root.findall("./ModelVariables/*"):
        if "unit" in variable.attrib:
            units[variable.attrib["name"]] = variable.attrib["unit"]
    assert units["T0"] == "degC"
    assert units["dhw_draw"] == "m3/s"
    assert units["E_cmp"] == "W"
    assert units["cop_sys"] == "1"

    t, T0, dhw = _schedule()
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

    for name in _ANALYZE_DYNAMIC_OUTPUTS:
        values = np.asarray(result[name], dtype=float)
        assert not np.isnan(values).any(), f"{name} NaN crossed FMI boundary"

    ref = _analyze_dynamic_reference()
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
