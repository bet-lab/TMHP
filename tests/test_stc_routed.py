"""GSHPB_STC_routed — per-timestep solar routing to the ground OR tank.

A single model routes solar heat to either the borehole field or the storage
tank each step, exclusively (never both). With no sun it reduces to the base
GSHPB byte-identically; a forced router reproduces the dedicated ground/tank
scenarios; the default greedy router never charges both at once.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

import numpy as np
import pytest

pytest.importorskip("pygfunction")

from tmhp import (  # noqa: E402
    GroundSourceHeatPumpBoiler,
    GSHPB_STC_routed,
    default_solar_router,
)
from tmhp.subsystems import SolarThermalCollector  # noqa: E402

_tN = 24
_BHE_COLS = ["T_bhe [°C]", "T_bhe_f [°C]", "T_bhe_f_in [°C]", "T_bhe_f_out [°C]"]


def _base_gshpb() -> GroundSourceHeatPumpBoiler:
    return GroundSourceHeatPumpBoiler(
        ref="R32",
        N_1=2,
        N_2=1,
        H_b=100.0,
        dt_s=3600.0,
        t_max_s=200 * 3600,
    )


def _routed_gshpb(
    *,
    stc: SolarThermalCollector | None = None,
    solar_router: Callable[..., str] | None = None,
    T_tank_w_lower_bound: float = 55.0,
) -> GSHPB_STC_routed:
    return GSHPB_STC_routed(
        stc=stc or SolarThermalCollector(A_stc=6.0),
        solar_router=solar_router,
        T_tank_w_lower_bound=T_tank_w_lower_bound,
        ref="R32",
        N_1=2,
        N_2=1,
        H_b=100.0,
        dt_s=3600.0,
        t_max_s=200 * 3600,
    )


def _schedules(sun: bool):
    dhw = np.zeros(_tN)
    dhw[[3, 4, 9, 10, 15, 16]] = 6.0e-5
    T0 = np.full(_tN, 15.0)
    I_DN = np.zeros(_tN)
    I_dH = np.zeros(_tN)
    if sun:
        day = np.arange(7, 18)
        I_DN[day] = 800.0
        I_dH[day] = 120.0
    return dhw, T0, I_DN, I_dH


def _run(model: GroundSourceHeatPumpBoiler, sun: bool):
    dhw, T0, I_DN, I_dH = _schedules(sun)
    return model.analyze_dynamic(
        simulation_period_sec=_tN * 3600.0,
        dt_s=3600.0,
        T_tank_w_init_C=56.0,
        dhw_usage_schedule=dhw,
        T0_schedule=T0,
        I_DN_schedule=I_DN,
        I_dH_schedule=I_dH,
    )


def _stc():
    return SolarThermalCollector(A_stc=6.0)


def test_default_router_is_greedy():
    assert default_solar_router(T_tank_w=50.0, T_tank_lower=60.0) == "tank"
    assert default_solar_router(T_tank_w=62.0, T_tank_lower=60.0) == "ground"
    # extra state is accepted and ignored
    assert default_solar_router(T_tank_w=62.0, T_tank_lower=60.0, hour_of_day=12, T_bhe=14.0, T0=15.0) == "ground"


def test_construction_type_check():
    with pytest.raises(TypeError):
        _routed_gshpb(stc=cast(SolarThermalCollector, object()))
    m = _routed_gshpb(stc=_stc())
    assert m._stc is m.stc


def test_zero_sun_matches_base():
    """No irradiance ⇒ routed model is a no-op vs the base GSHPB (byte-identical)."""
    base = _run(_base_gshpb(), sun=False)
    rt = _run(_routed_gshpb(stc=_stc()), sun=False)
    for col in [*_BHE_COLS, "T_tank_w [°C]", "cop_sys [-]"]:
        np.testing.assert_array_equal(np.nan_to_num(rt[col].to_numpy()), np.nan_to_num(base[col].to_numpy()))


def test_force_ground_route_charges_ground_only():
    """A router pinned to 'ground' reproduces ground charging; tank gets no solar."""
    model = _routed_gshpb(stc=_stc(), solar_router=lambda **_: "ground")
    df = _run(model, sun=True)
    assert (df["Q_solar_ground [W]"].to_numpy() > 0.0).any()  # ground charged
    assert np.allclose(df["Q_solar_tank [W]"].to_numpy(), 0.0)  # tank never charged
    # ground warms vs no-sun baseline
    base = _run(_routed_gshpb(stc=_stc(), solar_router=lambda **_: "ground"), sun=False)
    assert df["T_bhe [°C]"].mean() > base["T_bhe [°C]"].mean() + 0.5


def test_force_tank_route_charges_tank_only():
    """A router pinned to 'tank' charges the tank; the ground load is untouched."""
    model = _routed_gshpb(stc=_stc(), solar_router=lambda **_: "tank")
    df = _run(model, sun=True)
    assert (df["Q_solar_tank [W]"].to_numpy() > 0.0).any()  # tank charged on some steps
    assert np.allclose(df["Q_solar_ground [W]"].to_numpy(), 0.0)  # ground never charged
    assert (df["solar_route [-]"] == "tank").any()


def test_routing_is_exclusive_never_both():
    """Across the day the default router never charges ground and tank at once."""
    df = _run(_routed_gshpb(stc=_stc(), T_tank_w_lower_bound=60.0), sun=True)
    both = (df["Q_solar_tank [W]"].to_numpy() > 0.0) & (df["Q_solar_ground [W]"].to_numpy() > 0.0)
    assert not both.any()
    # and the day actually exercises both destinations (otherwise the test is vacuous)
    routes = set(df["solar_route [-]"])
    assert {"ground", "tank"} <= routes


def test_invalid_route_raises():
    model = _routed_gshpb(stc=_stc(), solar_router=lambda **_: "bogus")
    with pytest.raises(ValueError, match="expected one of"):
        _run(model, sun=True)
