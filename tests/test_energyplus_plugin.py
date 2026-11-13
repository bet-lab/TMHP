"""P2 EnergyPlus Python Plugin adapter tests (#165) — PlantComponent:UserDefined.

The plugin classes import ``pyenergyplus`` (bundled in an EnergyPlus install,
not pip-installable) and a full check needs EnergyPlus running the embedded
interpreter — that end-to-end co-simulation is exercised outside CI. Here we
anchor correctness to the public ``analyze_steady()`` seam (#165 P0), which is
exactly what the plugin calls, so the contract the adapter depends on is tested
dependency-light; the plugin-class structure is checked only when
``pyenergyplus`` is importable.
"""

from __future__ import annotations

import pytest

from tmhp import AirSourceHeatPumpBoiler

# Keys the plugin reads back from analyze_steady (must stay unit-suffixed).
_REQUIRED_KEYS = ("E_cmp [W]", "Q_ref_tank [W]", "cop_ref [-]", "cop_sys [-]")
CP_WATER = 4181.0  # J/(kg·K)
TOUT_MAX_REF = 95.0  # mirror the plugin's outlet clamp; fails if it drifts


def test_energyplus_model_kwargs_apply_validated_preset_without_pyenergyplus():
    """The pure configuration helper exposes the paper preset to the plugin."""
    from tmhp.integrations.energyplus_plugin import _ashpb_model_kwargs

    kwargs = _ashpb_model_kwargs(
        ref="R32",
        hp_capacity=9000.0,
        preset="validated_rule_set",
        V_cmp_disp_cc=42.0,
        dV_fan_a_rated=1.153,
    )

    assert kwargs["ref"] == "R32"
    assert kwargs["hp_capacity"] == 9000.0
    assert kwargs["V_cmp_ref"] == pytest.approx(4.2e-5)
    assert kwargs["UA_tank"] == pytest.approx(1800.0)
    assert kwargs["UA_ou_rated"] == pytest.approx(1260.0)
    assert kwargs["dV_fan_a_rated"] == pytest.approx(1.153)


def test_energyplus_model_kwargs_empty_preset_keeps_legacy_constructor():
    from tmhp.integrations.energyplus_plugin import _ashpb_model_kwargs

    assert _ashpb_model_kwargs(ref="R32", hp_capacity=15000.0) == {
        "ref": "R32",
        "hp_capacity": 15000.0,
    }


class _FakeExchange:
    def __init__(self, values, system_time_step=0.25):
        self.values = values
        self._system_time_step = system_time_step
        self.actuators = {}
        self.globals = {}

    def api_data_fully_ready(self, state):
        return True

    def get_internal_variable_value(self, state, handle):
        return self.values[handle]

    def get_variable_value(self, state, handle):
        return self.values[handle]

    def set_actuator_value(self, state, handle, value):
        self.actuators[handle] = value

    def set_global_value(self, state, handle, value):
        self.globals[handle] = value

    def system_time_step(self, state):
        return self._system_time_step


class _FakeRuntime:
    def __init__(self):
        self.severe = []
        self.warnings = []

    def issue_severe(self, state, msg):
        self.severe.append(msg)

    def issue_warning(self, state, msg):
        self.warnings.append(msg)


class _FakeApi:
    def __init__(self, values, system_time_step=0.25):
        self.exchange = _FakeExchange(values, system_time_step=system_time_step)
        self.runtime = _FakeRuntime()


def _surrogate_handles(e_cmp_j=8, e_cmp_w=9, e_cmp_legacy=-1):
    return {
        "t_in": 1,
        "mdot": 2,
        "cp": 3,
        "load": 4,
        "t_out_act": 5,
        "mdot_act": 6,
        "t0": 7,
        "e_cmp_j": e_cmp_j,
        "e_cmp_w": e_cmp_w,
        "e_cmp_legacy": e_cmp_legacy,
    }


def _steady():
    hp = AirSourceHeatPumpBoiler(ref="R32", hp_capacity=15000.0)
    return hp.analyze_steady(T_tank_w=54.0, T0=7.0, Q_ref_tank=10000.0)


def test_analyze_steady_contract_keys():
    """The adapter reads unit-suffixed keys plus bare converged/failure_reason."""
    res = _steady()
    for k in _REQUIRED_KEYS:
        assert k in res, f"missing key {k!r}"
    for k in ("converged", "failure_reason"):
        assert k in res, f"missing bare key {k!r}"


def test_analyze_steady_converges_and_powers_in_watts():
    res = _steady()
    assert res["converged"] and res["failure_reason"] == "none"
    e_cmp = res["E_cmp [W]"]
    q = res["Q_ref_tank [W]"]
    assert 500.0 < e_cmp < 15000.0, f"E_cmp out of plausible W range: {e_cmp}"
    assert 1000.0 < q < 20000.0, f"Q out of plausible W range: {q}"


def test_plugin_derived_formulas_are_unit_consistent():
    """Reproduce the two derivations the plugin performs from analyze_steady:
    cop_ref == Q/E_cmp, and the loop outlet rise dT = Q/(mdot*cp) is a small
    °C increment (the EnergyPlus <-> tmhp boundary contract)."""
    res = _steady()
    q, e_cmp = res["Q_ref_tank [W]"], res["E_cmp [W]"]
    assert abs(res["cop_ref [-]"] - q / e_cmp) < 1e-6
    m_dot = 0.003 * 1000.0  # design loop flow [kg/s]
    dt = q / (m_dot * CP_WATER)
    assert 0.1 < dt < 10.0, f"outlet dT not a small °C rise: {dt}"
    assert 54.0 + dt < TOUT_MAX_REF


def test_surrogate_solve_passes_loop_flow_and_cp_and_caches_by_flow():
    """The ε-NTU solve receives loop properties and cannot reuse another flow's result."""
    from tmhp.integrations.energyplus_plugin import TmhpPlantSurrogate

    calls = []

    class _StubHp:
        def analyze_steady(self, **kwargs):
            calls.append(kwargs)
            return {"call": len(calls)}

    plant = TmhpPlantSurrogate()
    plant.hp = _StubHp()
    plant._cache.clear()

    first = plant._solve(50.0, 7.0, 8000.0, 1.25)
    cached = plant._solve(50.0, 7.0, 8000.0, 1.25)
    second_flow = plant._solve(50.0, 7.0, 8000.0, 2.5)

    assert first is cached
    assert second_flow is not first
    assert len(calls) == 2
    assert calls[0] == {
        "T_tank_w": 50.0,
        "T0": 7.0,
        "Q_ref_tank": 8000.0,
        "m_dot_w": 1.25,
    }
    assert calls[1]["m_dot_w"] == 2.5


def test_surrogate_uses_usable_diagnostic_cycle_numbers(monkeypatch):
    """EnergyPlus should use positive cycle outputs even when diagnostics warn."""
    from tmhp.integrations.energyplus_plugin import (
        LOOP_DESIGN_VDOT,
        RHO_WATER,
        TmhpPlantSurrogate,
    )

    plant = TmhpPlantSurrogate()
    plant._requested = True
    plant._need = False
    plant._tally_every = 10_000
    plant.h = _surrogate_handles()
    monkeypatch.setattr(
        plant,
        "_solve",
        lambda t_in, t0, q_target, m_dot: {
            "converged": False,
            "failure_reason": "hx_not_converged",
            "Q_ref_tank [W]": 6000.0,
            "E_cmp [W]": 2000.0,
        },
    )
    plant.api = _FakeApi({1: 50.0, 2: 0.0, 3: CP_WATER, 4: 8000.0, 7: 7.0})

    assert plant.on_user_defined_component_model(object()) == 0

    ex = plant.api.exchange
    assert ex.actuators[5] > 50.0
    assert ex.actuators[6] == pytest.approx(LOOP_DESIGN_VDOT * RHO_WATER)
    assert ex.globals[8] == pytest.approx(2000.0 * 3600.0 * 0.25)
    assert ex.globals[9] == pytest.approx(2000.0)


def test_surrogate_zeroes_true_off_mode_cycle_outputs(monkeypatch):
    """Off-mode placeholders must not heat the EnergyPlus loop."""
    from tmhp.integrations.energyplus_plugin import TmhpPlantSurrogate

    plant = TmhpPlantSurrogate()
    plant._requested = True
    plant._need = False
    plant._tally_every = 10_000
    plant.h = _surrogate_handles()
    monkeypatch.setattr(
        plant,
        "_solve",
        lambda t_in, t0, q_target, m_dot: {
            "converged": False,
            "failure_reason": "cycle_invalid",
            "Q_ref_tank [W]": 0.0,
            "E_cmp [W]": 0.0,
        },
    )
    plant.api = _FakeApi({1: 50.0, 2: 0.0, 3: CP_WATER, 4: 8000.0, 7: 7.0})

    assert plant.on_user_defined_component_model(object()) == 0

    ex = plant.api.exchange
    assert ex.actuators[5] == 50.0
    assert ex.globals[8] == 0.0
    assert ex.globals[9] == 0.0


def test_surrogate_reverse_transfer_guard_stops_flow_and_limits_warnings(monkeypatch):
    """A reverse-transfer result must not cool the tank through requested loop flow."""
    from tmhp.integrations.energyplus_plugin import GUARD_WARNING_LIMIT, TmhpPlantSurrogate

    plant = TmhpPlantSurrogate()
    plant._requested = True
    plant._need = False
    plant._tally_every = 10_000
    plant.h = _surrogate_handles()
    monkeypatch.setattr(
        plant,
        "_solve",
        lambda t_in, t0, q_target, m_dot: {
            "converged": False,
            "failure_reason": "t_cond_below_t_in",
            # Positive placeholders verify that the reason itself blocks use.
            "Q_ref_tank [W]": 6000.0,
            "E_cmp [W]": 2000.0,
        },
    )
    plant.api = _FakeApi({1: 50.0, 2: 1.0, 3: CP_WATER, 4: 8000.0, 7: 7.0})

    for _ in range(GUARD_WARNING_LIMIT + 1):
        assert plant.on_user_defined_component_model(object()) == 0

    ex = plant.api.exchange
    assert ex.actuators[5] == 50.0
    assert ex.actuators[6] == 0.0
    assert ex.globals[8] == 0.0
    assert ex.globals[9] == 0.0
    assert len(plant.api.runtime.warnings) == GUARD_WARNING_LIMIT
    assert "t_cond_below_t_in" in plant.api.runtime.warnings[0]
    assert "suppressed" in plant.api.runtime.warnings[-1]


def test_surrogate_rejects_invalid_energyplus_boundary_values(monkeypatch):
    """Invalid EnergyPlus numeric inputs must not enter analyze_steady()."""
    from tmhp.integrations.energyplus_plugin import TmhpPlantSurrogate

    plant = TmhpPlantSurrogate()
    plant._requested = True
    plant._need = False
    plant.h = _surrogate_handles()
    monkeypatch.setattr(
        plant,
        "_solve",
        lambda t_in, t0, q_target, m_dot: pytest.fail("_solve should not be called"),
    )
    plant.api = _FakeApi({1: 50.0, 2: 0.0, 3: None, 4: 8000.0, 7: 7.0})

    assert plant.on_user_defined_component_model(object()) == 1

    ex = plant.api.exchange
    assert ex.actuators[5] == 50.0
    assert ex.actuators[6] == 0.0
    assert ex.globals[8] == 0.0
    assert ex.globals[9] == 0.0
    assert plant.api.runtime.severe
    assert "cp=None" in plant.api.runtime.severe[0]


def test_surrogate_rejects_invalid_system_timestep(monkeypatch):
    """EnergyPlus timestep must be finite and positive before J integration."""
    from tmhp.integrations.energyplus_plugin import TmhpPlantSurrogate

    plant = TmhpPlantSurrogate()
    plant._requested = True
    plant._need = False
    plant.h = _surrogate_handles()
    monkeypatch.setattr(
        plant,
        "_solve",
        lambda t_in, t0, q_target, m_dot: pytest.fail("_solve should not be called"),
    )
    plant.api = _FakeApi(
        {1: 50.0, 2: 0.0, 3: CP_WATER, 4: 8000.0, 7: 7.0},
        system_time_step=0.0,
    )

    assert plant.on_user_defined_component_model(object()) == 1

    ex = plant.api.exchange
    assert ex.actuators[5] == 50.0
    assert ex.actuators[6] == 0.0
    assert ex.globals[8] == 0.0
    assert ex.globals[9] == 0.0
    assert "system timestep" in plant.api.runtime.severe[0]


def test_surrogate_accepts_legacy_energy_global_without_new_name():
    """Older IDFs may still expose only tmhp_E_cmp for timestep joules."""
    from tmhp.integrations.energyplus_plugin import TmhpPlantSurrogate

    plant = TmhpPlantSurrogate()
    plant.h = _surrogate_handles(e_cmp_j=-1, e_cmp_w=-1, e_cmp_legacy=8)
    plant.api = _FakeApi({})

    assert plant._valid(object()) is True
    assert plant.api.runtime.severe == []


def test_surrogate_uses_legacy_energy_global_when_new_name_missing(monkeypatch):
    """Legacy tmhp_E_cmp receives timestep joules when tmhp_E_cmp_J is absent."""
    from tmhp.integrations.energyplus_plugin import (
        LOOP_DESIGN_VDOT,
        RHO_WATER,
        TmhpPlantSurrogate,
    )

    plant = TmhpPlantSurrogate()
    plant._requested = True
    plant._need = False
    plant._tally_every = 10_000
    plant.h = _surrogate_handles(e_cmp_j=-1, e_cmp_w=-1, e_cmp_legacy=8)
    monkeypatch.setattr(
        plant,
        "_solve",
        lambda t_in, t0, q_target, m_dot: {
            "converged": True,
            "failure_reason": "none",
            "Q_ref_tank [W]": 6000.0,
            "E_cmp [W]": 2000.0,
        },
    )
    plant.api = _FakeApi({1: 50.0, 2: 0.0, 3: CP_WATER, 4: 8000.0, 7: 7.0})

    assert plant.on_user_defined_component_model(object()) == 0

    ex = plant.api.exchange
    assert ex.actuators[5] > 50.0
    assert ex.actuators[6] == pytest.approx(LOOP_DESIGN_VDOT * RHO_WATER)
    assert ex.globals[8] == pytest.approx(2000.0 * 3600.0 * 0.25)
    assert 9 not in ex.globals


def _run_surrogate(monkeypatch, result, *, t_in=50.0, mdot=0.0, load=8000.0, t0=7.0, legacy=False):
    """One plugin call with a stubbed cycle result; returns (surrogate, fake exchange)."""
    from tmhp.integrations.energyplus_plugin import TmhpPlantSurrogate

    plant = TmhpPlantSurrogate()
    plant._requested = True
    plant._need = False
    plant._tally_every = 10_000
    plant.h = _surrogate_handles(e_cmp_j=-1 if legacy else 8, e_cmp_w=9, e_cmp_legacy=8 if legacy else -1)
    monkeypatch.setattr(plant, "_solve", lambda *a: dict(result))
    plant.api = _FakeApi({1: t_in, 2: mdot, 3: CP_WATER, 4: load, 7: t0})
    assert plant.on_user_defined_component_model(object()) == 0
    return plant, plant.api.exchange


_OK = {"converged": True, "failure_reason": "none"}


def test_surrogate_meters_system_total_including_outdoor_fan(monkeypatch):
    """The metered power is E_tot (compressor + outdoor fan), not the compressor alone."""
    res = {**_OK, "Q_ref_tank [W]": 6000.0, "E_cmp [W]": 2000.0, "E_ou_fan [W]": 150.0, "E_tot [W]": 2150.0}
    _, ex = _run_surrogate(monkeypatch, res)
    assert ex.globals[9] == pytest.approx(2150.0)
    assert ex.globals[8] == pytest.approx(2150.0 * 3600.0 * 0.25)


def test_surrogate_adds_fan_when_result_has_no_e_tot(monkeypatch):
    res = {**_OK, "Q_ref_tank [W]": 6000.0, "E_cmp [W]": 2000.0, "E_ou_fan [W]": 150.0}
    _, ex = _run_surrogate(monkeypatch, res)
    assert ex.globals[9] == pytest.approx(2150.0)


def test_surrogate_does_not_deliver_more_heat_than_requested(monkeypatch):
    """Minimum-speed clamp: the cycle gives 6 kW, EnergyPlus asked for 1.5 kW -> cycling average."""
    from tmhp.integrations.energyplus_plugin import LOOP_DESIGN_VDOT, RHO_WATER

    res = {
        **_OK,
        "capacity_clamped": "min",
        "Q_ref_tank [W]": 6000.0,
        "E_cmp [W]": 1900.0,
        "E_ou_fan [W]": 100.0,
        "E_tot [W]": 2000.0,
    }
    _, ex = _run_surrogate(monkeypatch, res, load=1500.0)
    m_dot = LOOP_DESIGN_VDOT * RHO_WATER
    q_loop = m_dot * CP_WATER * (ex.actuators[5] - 50.0)
    assert q_loop == pytest.approx(1500.0)
    # metered for the same duty fraction (1500 / 6000) of the minimum-speed state
    assert ex.globals[9] == pytest.approx(2000.0 * 1500.0 / 6000.0)
    assert ex.globals[8] == pytest.approx(2000.0 * 0.25 * 3600.0 * 1500.0 / 6000.0)


def test_surrogate_does_not_meter_power_for_heat_cut_at_outlet_limit(monkeypatch):
    """Heat that would push the outlet above TOUT_MAX is neither delivered nor metered."""
    from tmhp.integrations.energyplus_plugin import LOOP_DESIGN_VDOT, RHO_WATER, TOUT_MAX

    res = {**_OK, "Q_ref_tank [W]": 6000.0, "E_cmp [W]": 1900.0, "E_tot [W]": 2000.0}
    _, ex = _run_surrogate(monkeypatch, res, t_in=TOUT_MAX - 0.1)
    m_dot = LOOP_DESIGN_VDOT * RHO_WATER
    q_loop = m_dot * CP_WATER * (ex.actuators[5] - (TOUT_MAX - 0.1))
    assert ex.actuators[5] == pytest.approx(TOUT_MAX)
    assert q_loop < 6000.0
    assert ex.globals[9] == pytest.approx(2000.0 * q_loop / 6000.0)


def test_surrogate_metered_power_and_loop_heat_share_one_operating_state(monkeypatch):
    """E_meter / q_loop equals the solved state's E_tot / Q for any request."""
    from tmhp.integrations.energyplus_plugin import LOOP_DESIGN_VDOT, RHO_WATER

    res = {**_OK, "Q_ref_tank [W]": 6000.0, "E_cmp [W]": 1900.0, "E_tot [W]": 2000.0}
    m_dot = LOOP_DESIGN_VDOT * RHO_WATER
    for load in (500.0, 3000.0, 6000.0, 9000.0):
        _, ex = _run_surrogate(monkeypatch, res, load=load)
        q_loop = m_dot * CP_WATER * (ex.actuators[5] - 50.0)
        assert q_loop <= min(load, 6000.0) * (1 + 1e-12)
        assert ex.globals[9] / q_loop == pytest.approx(2000.0 / 6000.0)


def test_surrogate_with_real_model_at_minimum_speed_clamp(monkeypatch):
    """Real ASHPB at a clamped point: loop heat = request, metered power includes the fan.

    ``analyze_steady`` reports such a point as the mean over the on/off
    cycling period (heat = request, electrical input with the EN 14825
    degradation), and the plugin meters that row as it is.
    """
    from tmhp.integrations.energyplus_plugin import TmhpPlantSurrogate

    plant = TmhpPlantSurrogate()
    plant._requested = True
    plant._need = False
    plant._tally_every = 10_000
    plant.h = _surrogate_handles()
    plant.api = _FakeApi({1: 30.0, 2: 3.0, 3: CP_WATER, 4: 1500.0, 7: 7.0})
    full = plant.hp.analyze_steady(T_tank_w=30.0, T0=7.0, Q_ref_tank=1500.0, m_dot_w=3.0)
    assert full["capacity_clamped"] == "min" and full["Q_min [W]"] > 1500.0  # scenario precondition
    assert full["Q_ref_tank [W]"] == pytest.approx(1500.0)
    assert full["E_ou_fan [W]"] > 0.0

    assert plant.on_user_defined_component_model(object()) == 0

    ex = plant.api.exchange
    q_loop = 3.0 * CP_WATER * (ex.actuators[5] - 30.0)
    assert q_loop == pytest.approx(1500.0)
    assert ex.globals[9] == pytest.approx(full["E_tot [W]"])
    assert ex.globals[9] > full["E_cmp [W]"]
    # More than the running machine's input for the share of the time it runs:
    # the row carries the cycling degradation.
    running_input = (full["E_tot [W]"] - full["E_cycling_loss [W]"]) / full["capacity_ratio [-]"]
    assert ex.globals[9] > running_input * 1500.0 / full["Q_min [W]"]


def test_plugin_classes_when_energyplus_available():
    """When pyenergyplus is importable, the two managers are EnergyPlusPlugins
    exposing the user-defined-component callback."""
    pytest.importorskip("pyenergyplus")
    from pyenergyplus.plugin import EnergyPlusPlugin

    from tmhp.integrations.energyplus_plugin import TmhpPlantInit, TmhpPlantSurrogate

    for cls in (TmhpPlantInit, TmhpPlantSurrogate):
        assert issubclass(cls, EnergyPlusPlugin)
        assert hasattr(cls, "on_user_defined_component_model")


def _real_surrogate(t_in, t0, load, mdot=3.0):
    from tmhp.integrations.energyplus_plugin import TmhpPlantSurrogate

    plant = TmhpPlantSurrogate()
    plant._requested = True
    plant._need = False
    plant._tally_every = 10_000
    plant.h = _surrogate_handles()
    plant.api = _FakeApi({1: t_in, 2: mdot, 3: CP_WATER, 4: load, 7: t0})
    return plant


def test_surrogate_with_real_model_at_the_discharge_temperature_ceiling():
    """R32 at A-2 with a 60 degC loop: the discharge-temperature control holds 150 degC
    only at a reduced duty (``capacity_clamped='max'``). The loop gets that duty,
    not the request, and the machine is metered for all of it."""
    plant = _real_surrogate(60.0, -2.0, 15000.0)
    full = plant.hp.analyze_steady(T_tank_w=60.0, T0=-2.0, Q_ref_tank=15000.0, m_dot_w=3.0)
    # scenario precondition
    assert full["capacity_clamped"] == "max" and full["capacity_binding"] == "discharge_temperature"
    assert full["discharge_control"] is True and full["Q_ref_tank [W]"] < 15000.0

    assert plant.on_user_defined_component_model(object()) == 0

    ex = plant.api.exchange
    q_loop = 3.0 * CP_WATER * (ex.actuators[5] - 60.0)
    assert q_loop == pytest.approx(full["Q_ref_tank [W]"])
    assert ex.globals[9] == pytest.approx(full["E_tot [W]"])


def test_surrogate_with_real_model_outside_the_envelope_delivers_nothing():
    """R32 at A-10 with a 50 degC loop: outside the envelope even under control."""
    plant = _real_surrogate(50.0, -10.0, 15000.0)
    off = plant.hp.analyze_steady(T_tank_w=50.0, T0=-10.0, Q_ref_tank=15000.0, m_dot_w=3.0)
    assert off["failure_reason"] == "discharge_temp_above_max"  # scenario precondition

    assert plant.on_user_defined_component_model(object()) == 0

    ex = plant.api.exchange
    assert ex.actuators[5] == pytest.approx(50.0)
    assert ex.globals[9] == pytest.approx(0.0)
