"""P1 FMU adapter tests (#165) — FMI 2.0 co-sim slave wrapping step().

Requires the ``integrations`` extra (pythonfmu / fmpy); skipped otherwise so
the core test suite stays dependency-light::

    uv run --with pythonfmu --with fmpy python3 -m pytest tmhp/tests/test_fmu_adapter.py

Correctness is anchored to the public ``step()`` kernel (#165 P0): the slave
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

from tmhp import AirSourceHeatPumpBoiler
from tmhp.integrations._fmi_common import VARIABLE_DESCRIPTIONS
from tmhp.integrations.fmu import TmhpAshpbSlave, _finite

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


def _make_slave() -> TmhpAshpbSlave:
    slave = TmhpAshpbSlave(instance_name="tmhp_ashpb")
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
def test_fmu_slave_rejects_invalid_importer_inputs(field, value, step_size):
    """Invalid importer inputs fail the FMI step before the model advances."""
    slave = _make_slave()
    setattr(slave, field, value)

    assert slave.do_step(0.0, step_size) is False
    assert slave.converged is False
    assert slave.failure_reason == "invalid_input"
    assert slave.hp_is_on is False
    assert slave._n == 0


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


def test_fmu_builds_and_simulates(tmp_path):
    """Full PythonFMU build + fmpy round-trip: the FMU runs the prescribed
    schedule to completion, emits no NaN, and matches analyze_dynamic() on
    the FMI boundary outputs."""
    fmpy = pytest.importorskip("fmpy")
    from fmpy.validation import validate_fmu
    from pythonfmu.builder import FmuBuilder

    import tmhp.integrations.fmu as fmu_mod

    fmu_file = FmuBuilder.build_FMU(fmu_mod.__file__, dest=str(tmp_path))
    assert fmu_file is not None
    assert validate_fmu(str(fmu_file)) == []
    with ZipFile(fmu_file) as archive:
        root = ElementTree.fromstring(archive.read("modelDescription.xml"))
    descriptions = {
        scalar.attrib["name"]: scalar.attrib.get("description")
        for scalar in root.findall("./ModelVariables/ScalarVariable")
        if scalar.attrib["name"] in VARIABLE_DESCRIPTIONS
    }
    assert descriptions == VARIABLE_DESCRIPTIONS
    unit_names = {unit.attrib["name"] for unit in root.findall("./UnitDefinitions/Unit")}
    assert {"W", "degC", "m3/s", "1"} <= unit_names
    units = {}
    for scalar in root.findall("./ModelVariables/ScalarVariable"):
        real = scalar.find("Real")
        if real is not None:
            units[scalar.attrib["name"]] = real.attrib["unit"]
    assert units["T0"] == "degC"
    assert units["dhw_draw"] == "m3/s"
    assert units["E_cmp"] == "W"
    assert units["cop_sys"] == "1"

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
