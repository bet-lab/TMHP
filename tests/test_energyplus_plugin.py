"""EnergyPlus Python Plugin adapter tests — PlantComponent:UserDefined.

The plugin classes import ``pyenergyplus`` (bundled in an EnergyPlus install,
not pip-installable) and a full check needs EnergyPlus running the embedded
interpreter. The EnergyPlus CI workflow runs the design-day plant in
``tests/energyplus`` using the stock Linux CLI. Here we
anchor correctness to the public ``analyze_steady()`` seam, which is
exactly what the plugin calls, so the contract the adapter depends on is tested
dependency-light; the plugin-class structure is checked only when
``pyenergyplus`` is importable.
"""

from __future__ import annotations

import atexit
import random
import re
from types import SimpleNamespace

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
        self.values = {10: 84.0, **values}
        self._system_time_step = system_time_step
        self.actuators = {}
        self.globals = {}
        self.failed_variable_handles = set()
        self.error_flag = False
        # Simulation clock read by the humidity tally.
        self.environment = 1
        self.year_value = 2026
        self.day = 1
        self.hour_value = 1
        self.zone_step = 1
        self.system_clock = 1.0
        self.warmup = False

    def api_data_fully_ready(self, state):
        return True

    def get_internal_variable_value(self, state, handle):
        return self.values[handle]

    def get_variable_value(self, state, handle):
        """Return zero and set the sticky flag on failed reads, as EnergyPlus does."""
        if handle in self.failed_variable_handles:
            self.error_flag = True
            return 0.0
        return self.values[handle]

    def api_error_flag(self, state):
        """Expose the error status until the caller resets it."""
        return self.error_flag

    def reset_api_error_flag(self, state):
        """Clear the sticky flag for later API operations."""
        self.error_flag = False

    def set_actuator_value(self, state, handle, value):
        self.actuators[handle] = value

    def set_global_value(self, state, handle, value):
        self.globals[handle] = value

    def system_time_step(self, state):
        return self._system_time_step

    def current_environment_num(self, state):
        return self.environment

    def year(self, state):
        return self.year_value

    def day_of_year(self, state):
        return self.day

    def hour(self, state):
        return self.hour_value

    def zone_time_step_number(self, state):
        return self.zone_step

    def current_time(self, state):
        """The system-step clock, which moves with HVAC substeps."""
        return self.system_clock

    def warmup_flag(self, state):
        return self.warmup


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
        "rh_out": 10,
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
        "RH_out": None,
    }
    assert calls[1]["m_dot_w"] == 2.5


@pytest.mark.parametrize(
    ("handle", "readings", "expected", "warnings"),
    [
        (10, (35.0, 60.0, 35.0), [0.35, 0.60], 0),
        (-1, (None,) * 3, [None], 1),
        (10, (None, float("nan"), float("inf"), -0.1, 110.1, 35.0), [None, 0.35], 1),
        # The EPW format allows up to 110 %, and EnergyPlus 24.2 passes such
        # readings on unclipped: 100-110 % is saturated air at 100 %.
        (10, (100.5, 110.0, 100.0, 110.1), [1.0, None], 1),
        # Interpolating between two 110 % hours can land one ULP above 110.
        (10, (110.00000000000001,), [1.0], 0),
    ],
    ids=["cache-by-humidity", "missing-handle", "invalid-readings", "above-saturation", "interpolated-110"],
)
def test_surrogate_outdoor_humidity(monkeypatch, capsys, handle, readings, expected, warnings):
    """Forward usable humidity, separate cache entries, and warn once on fallback."""
    plant = _real_surrogate(40.0, 2.0, 15000.0)
    plant.h["rh_out"] = handle
    calls = []

    def analyze_steady(**kwargs):
        """Echo the supplied model humidity in its result diagnostics."""
        calls.append(kwargs["RH_out"])
        return {
            "converged": True,
            "failure_reason": "none",
            "Q_ref_tank [W]": 15000.0,
            "E_tot [W]": 5000.0,
            "RH_out [-]": kwargs["RH_out"],
        }

    monkeypatch.setattr(plant.hp, "analyze_steady", analyze_steady)
    for percent in readings:
        plant.api.exchange.values[10] = percent
        assert plant.on_user_defined_component_model(object()) == 0
        assert plant.api.exchange.globals[9] == 5000.0

    assert calls == expected
    assert plant.api.runtime.severe == []
    assert len(plant.api.runtime.warnings) == warnings
    if warnings:
        assert "relative humidity" in plant.api.runtime.warnings[0]
    if 0.35 in expected:
        assert "RH_out=35.00%" in capsys.readouterr().out


def test_surrogate_tallies_outdoor_humidity_timesteps(monkeypatch, capsys):
    """The tally counts zone timesteps solved with weather, clipped and default humidity.

    Plant iterations and HVAC system substeps re-call the plugin within one
    zone timestep, so a timestep is counted once (by environment, year, day,
    hour and zone step number); warmup days are left out. The counts appear in
    the periodic tally line and in a final summary.
    """
    plant = _real_surrogate(40.0, 2.0, 15000.0)
    plant._tally_every = 4
    ex = plant.api.exchange

    def analyze_steady(**kwargs):
        return {"converged": True, "failure_reason": "none", "Q_ref_tank [W]": 15000.0, "E_tot [W]": 5000.0}

    monkeypatch.setattr(plant.hp, "analyze_steady", analyze_steady)
    timesteps = [
        # (warmup, day, hour, zone step, reading in percent, system substeps)
        (True, 1, 0, 1, float("nan"), 3),
        (False, 1, 0, 1, 35.0, 2),
        (False, 1, 0, 2, 100.5, 3),
        (False, 1, 1, 1, 110.0, 1),
        (False, 1, 1, 2, float("nan"), 2),
        (False, 1, 2, 1, 110.1, 1),
        (False, 1, 3, 1, -1.0, 1),
        (False, 2, 0, 1, 100.0, 2),
        (False, 2, 0, 2, 35.0, 1),  # this zone timestep falls back on a later call
    ]
    for warmup, day, hour, step, percent, substeps in timesteps:
        ex.warmup, ex.day, ex.hour_value, ex.zone_step = warmup, day, hour, step
        ex.values[10] = percent
        for substep in range(substeps):
            ex.system_clock = hour + (step - 1 + (substep + 1) / substeps) / 4
            assert plant.on_user_defined_component_model(object()) == 0
    ex.values[10] = float("nan")  # a second call in the last zone timestep fails
    assert plant.on_user_defined_component_model(object()) == 0
    plant._log_humidity_summary()

    out = capsys.readouterr().out.splitlines()
    tally = [line for line in out if line.startswith("[tally")]
    assert len(tally) == 4  # dispatches 4, 8, 12 and 16 of 17 (the first 3 are warmup)
    assert tally[2].endswith(
        "outdoor_RH_timesteps={'weather': 1, 'clipped_to_100': 2, 'default': 2} "
        "(heating-dispatch zone timesteps, warmup excluded)"
    )
    assert out[-1] == (
        f"[outdoor RH final] {plant._run_id}: zone timesteps with a heating dispatch, warmup days excluded: "
        "weather=2 clipped_to_100=2 default=4"
    )


def test_surrogate_final_humidity_summaries_name_their_run(capsys):
    """Two runs in one process (EnergyPlus Python API) log summaries that can be told apart."""
    first = _real_surrogate(40.0, 2.0, 15000.0)
    second = _real_surrogate(40.0, 2.0, 15000.0)
    assert first._run_id != second._run_id
    assert re.fullmatch(r"pid \d+ run \d+", first._run_id)

    first._log_humidity_summary()
    second._log_humidity_summary()

    out = [line for line in capsys.readouterr().out.splitlines() if line.startswith("[outdoor RH final]")]
    assert [line.split(":")[0] for line in out] == [
        f"[outdoor RH final] {first._run_id}",
        f"[outdoor RH final] {second._run_id}",
    ]


def test_surrogate_uses_default_on_failed_humidity_read_and_recovers(capsys):
    """Meter the default on repeated API errors, then accept an unflagged zero."""
    plant = _real_surrogate(40.0, 2.0, 15000.0)
    ex = plant.api.exchange
    state = object()
    point = dict(T_tank_w=40.0, T0=2.0, Q_ref_tank=15000.0, m_dot_w=3.0)
    default_power = plant.hp.analyze_steady(**point)["E_tot [W]"]
    dry_power = plant.hp.analyze_steady(**point, RH_out=0.0)["E_tot [W]"]
    assert default_power != dry_power

    ex.failed_variable_handles.add(10)
    for _ in range(2):
        assert plant.on_user_defined_component_model(state) == 0
        assert ex.globals[9] == pytest.approx(default_power)
        assert not ex.api_error_flag(state)
    assert len(plant.api.runtime.warnings) == 1
    assert "RH_out=default" in capsys.readouterr().out

    ex.failed_variable_handles.clear()
    ex.values[10] = 0.0
    assert plant.on_user_defined_component_model(state) == 0
    assert ex.globals[9] == pytest.approx(dry_power)
    assert len(plant.api.runtime.warnings) == 1
    assert "RH_out=0.00%" in capsys.readouterr().out


def test_surrogate_humidity_does_not_clear_prior_api_error():
    """An error from another exchange operation remains visible to EnergyPlus."""
    plant = _real_surrogate(40.0, 2.0, 15000.0)
    ex = plant.api.exchange
    state = object()
    ex.error_flag = True
    ex.values[10] = 0.0
    default_power = plant.hp.analyze_steady(T_tank_w=40.0, T0=2.0, Q_ref_tank=15000.0, m_dot_w=3.0)["E_tot [W]"]

    assert plant.on_user_defined_component_model(state) == 0
    assert ex.globals[9] == pytest.approx(default_power)
    assert ex.api_error_flag(state)


def test_surrogate_humidity_bin_yields_the_same_power_in_either_order(monkeypatch):
    """Readings in one 0.01 percentage-point bin use its center in either order."""
    rng = random.Random(201)
    plant = _real_surrogate(40.0, 2.0, 15000.0)
    ex = plant.api.exchange
    state = object()

    def analyze_steady(*, RH_out, **kwargs):
        """Encode the model input in the metered output."""
        return {"converged": True, "Q_ref_tank [W]": 15000.0, "E_tot [W]": RH_out * 10_000}

    monkeypatch.setattr(plant.hp, "analyze_steady", analyze_steady)
    for bin_index in rng.sample(range(1, 10_000), 10):
        center = bin_index / 100
        a = center - rng.uniform(0.0001, 0.0049)
        b = center + rng.uniform(0.0001, 0.0049)
        for order in ((a, b), (b, a)):
            plant._cache.clear()
            for percent in order:
                ex.values[10] = percent
                assert plant.on_user_defined_component_model(state) == 0
                assert ex.globals[9] == pytest.approx(bin_index), (bin_index, order)


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
        lambda t_in, t0, q_target, m_dot, *, rh_out: {
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
        lambda t_in, t0, q_target, m_dot, *, rh_out: {
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
        lambda t_in, t0, q_target, m_dot, *, rh_out: {
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


@pytest.mark.parametrize(
    ("declared_names", "expected_outputs"),
    [
        (["PLANT ENERGY"], {0: 9000.0}),
        (["PLANT ENERGY", "PLANT POWER"], {0: 9000.0, 1: 10.0}),
        (["TMHP_E_CMP"], {0: 9000.0}),
        (["TMHP_E_CMP", "PLANT POWER", "PLANT ENERGY"], {0: 9000.0, 1: 10.0, 2: 9000.0}),
        (["PLANT POWER"], {}),
    ],
    ids=["energy-only", "energy-and-power", "legacy-only", "all-three", "missing-energy"],
)
def test_surrogate_initializes_and_meters_only_declared_globals(monkeypatch, declared_names, expected_outputs):
    """EnergyPlus aborts on undeclared global lookups, even for an optional output."""
    from tmhp.integrations import energyplus_plugin as plugin

    monkeypatch.setattr(plugin, "ECMP_ENERGY_GLOBAL", "Plant Energy")
    monkeypatch.setattr(plugin, "ECMP_POWER_GLOBAL", "Plant Power")
    plant = plugin.TmhpPlantSurrogate()
    plant.api = _FakeApi({1: 50.0, 2: 7.0})
    ex = plant.api.exchange
    requested = []
    ex.request_variable = lambda state, name, key: requested.append((name, key))
    ex.get_internal_variable_handle = lambda *args: 1
    ex.get_actuator_handle = lambda *args: 3
    weather_handles = {
        ("Site Outdoor Air Drybulb Temperature", "Environment"): 2,
        ("Site Outdoor Air Relative Humidity", "Environment"): 10,
    }

    def get_variable_handle(state, name, key):
        assert (name, key) in requested
        return weather_handles[name, key]

    ex.get_variable_handle = get_variable_handle
    global_handles = {name: handle for handle, name in enumerate(declared_names)}
    points = [SimpleNamespace(what="PluginGlobalVariable", name="", key=name) for name in declared_names]
    points.append(SimpleNamespace(what="OutputVariable", name="Unrelated", key="PLANT ENERGY"))
    ex.get_api_data = lambda state: points
    ex.get_global_handle = lambda state, name: global_handles[name.upper()]
    result = {"converged": True, "failure_reason": "none", "Q_ref_tank [W]": 50.0, "E_cmp [W]": 10.0}
    monkeypatch.setattr(plant, "_solve", lambda *args, rh_out: result)

    state = object()
    assert plant.on_user_defined_component_model(state) == 0  # Request both outdoor-air variables.
    assert requested == list(weather_handles)
    if expected_outputs:
        assert plant.on_user_defined_component_model(state) == 0
        assert plant.api.runtime.severe == []
    else:
        assert plant.on_user_defined_component_model(state) == 1
        assert "energy global" in plant.api.runtime.severe[0]
    assert ex.globals == expected_outputs


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
        lambda t_in, t0, q_target, m_dot, *, rh_out: {
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
    monkeypatch.setattr(plant, "_solve", lambda *args, rh_out: dict(result))
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
    from tmhp.integrations.energyplus_plugin import _log_humidity_summary

    atexit.unregister(_log_humidity_summary)
    return plant


@pytest.mark.earlier_defaults  # tests/conftest.py
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


@pytest.mark.earlier_defaults  # tests/conftest.py
def test_surrogate_with_real_model_outside_the_envelope_delivers_nothing():
    """R32 at A-10 with a 50 degC loop: outside the envelope even under control."""
    plant = _real_surrogate(50.0, -10.0, 15000.0)
    off = plant.hp.analyze_steady(T_tank_w=50.0, T0=-10.0, Q_ref_tank=15000.0, m_dot_w=3.0)
    assert off["failure_reason"] == "discharge_temp_above_max"  # scenario precondition

    assert plant.on_user_defined_component_model(object()) == 0

    ex = plant.api.exchange
    assert ex.actuators[5] == pytest.approx(50.0)
    assert ex.globals[9] == pytest.approx(0.0)
