"""GSHPB_STC_ground — solar thermal collector charging the ground loop.

Solar heat charges the ground (injection) *instead of* the tank (exclusive
routing). With no sun the scenario reduces to the base GSHPB byte-identically;
with sun the borehole field is warmed.
"""

from __future__ import annotations

from typing import cast

import numpy as np
import pytest

pytest.importorskip("pygfunction")

from tmhp import GroundSourceHeatPumpBoiler, GSHPB_STC_ground  # noqa: E402
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


def _ground_gshpb(
    stc: SolarThermalCollector | None = None,
) -> GSHPB_STC_ground:
    return GSHPB_STC_ground(
        stc=stc or SolarThermalCollector(A_stc=6.0),
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
        day = np.arange(7, 18)  # daytime hours within the preheat window
        I_DN[day] = 800.0
        I_dH[day] = 120.0
    return dhw, T0, I_DN, I_dH


def _run(model, sun: bool):
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


def test_construction_type_check():
    with pytest.raises(TypeError):
        _ground_gshpb(stc=cast(SolarThermalCollector, object()))
    m = _ground_gshpb()
    assert m._stc is m.stc


def test_zero_sun_matches_base_bhe():
    """No irradiance ⇒ the ground-STC scenario is a no-op vs the base GSHPB."""
    base = _run(_base_gshpb(), sun=False)
    grd = _run(_ground_gshpb(), sun=False)
    for col in _BHE_COLS:
        np.testing.assert_array_equal(grd[col].to_numpy(), base[col].to_numpy())


def test_solar_charges_and_warms_ground():
    """Daytime irradiance injects heat into the ground, raising T_bhe."""
    model = _ground_gshpb()
    df_sun = _run(model, sun=True)
    # Solar heat was actually injected on some daytime steps.
    q_sol = df_sun["Q_solar_ground [W]"].to_numpy()
    assert np.any(q_sol > 0.0)
    assert df_sun["stc_active [-]"].to_numpy().any()

    # Compared with the no-sun baseline, the ground (and its fluid) is warmer.
    base = _run(_ground_gshpb(), sun=False)
    T_sun = df_sun["T_bhe [°C]"].to_numpy()
    T_base = base["T_bhe [°C]"].to_numpy()
    assert np.all(np.isfinite(T_sun))
    assert np.all(T_sun >= T_base - 1e-9)  # solar injection never cools the ground
    assert T_sun.mean() > T_base.mean() + 0.5  # net warming
