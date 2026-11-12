"""Collector, PV, UV lamp and mains-temperature physics (#96 T2, T3, T8, T9, T11).

Regression tests for the fixes; the strict defect tests live in
``tests/invariants/test_known_defects.py`` and the scenario-class energy
accounting in ``tests/test_subsystem_energy_accounting.py``.
"""

from __future__ import annotations

import math
import warnings

import numpy as np
import pandas as pd
import pvlib
import pytest

import tmhp
from tmhp import calc_util as cu
from tmhp.constants import c_w, rho_w
from tmhp.subsystems import PhotovoltaicSystem, SolarThermalCollector, UVLamp
from tmhp.weather import (
    ashrae_iam,
    decompose_ghi_to_poa,
    plane_of_array_irradiance,
    solar_position,
    start_day_of_year,
)

SEOUL = pvlib.location.Location(37.57, 126.98, "Asia/Seoul")


def _clear_sky(day: str, dt_s: int = 600):
    t = pd.date_range(day, periods=86400 // dt_s, freq=f"{dt_s}s", tz="Asia/Seoul")
    mid = t + pd.Timedelta(seconds=dt_s / 2)
    return mid, SEOUL.get_clearsky(mid, model="ineichen")


# ---------------------------------------------------------------------------
# sun position and plane-of-array irradiance
# ---------------------------------------------------------------------------


def test_solar_position_matches_pvlib_spa():
    times = pd.date_range("2026-01-01", periods=365 * 4, freq="6h", tz="Asia/Seoul") + pd.Timedelta(minutes=17)
    sp = SEOUL.get_solarposition(times)
    for ts, (_, ref) in zip(times, sp.iterrows(), strict=True):
        if ref["zenith"] > 85.0:
            continue
        z, a = solar_position(start_day_of_year(ts.tz_localize(None), 9.0))
        assert z == pytest.approx(ref["zenith"], abs=0.4)
        assert ((a - ref["azimuth"] + 180.0) % 360.0) - 180.0 == pytest.approx(0.0, abs=0.5)


def test_start_time_accepts_aware_timestamps():
    naive = start_day_of_year("2026-06-21 09:00", 9.0)
    aware = start_day_of_year(pd.Timestamp("2026-06-21 00:00", tz="UTC"), 9.0)
    assert naive == pytest.approx(aware)
    assert start_day_of_year(None, 9.0) == pytest.approx(79.0)


@pytest.mark.parametrize("day", ["2026-03-20", "2026-06-21", "2026-12-21"])
@pytest.mark.parametrize("tilt,azimuth", [(0.0, 180.0), (35.0, 180.0), (90.0, 180.0), (35.0, 90.0)])
def test_collector_plane_irradiance_matches_pvlib_on_clear_days(day, tilt, azimuth):
    """Daily plane irradiance within 3 % of pvlib (SPA + Perez) for Seoul clear days."""
    mid, cs = _clear_sky(day)
    sp = SEOUL.get_solarposition(mid)
    ref = pvlib.irradiance.get_total_irradiance(
        tilt,
        azimuth,
        sp.apparent_zenith,
        sp.azimuth,
        cs.dni,
        cs.ghi,
        cs.dhi,
        dni_extra=pvlib.irradiance.get_extra_radiation(mid),
        airmass=SEOUL.get_airmass(mid).airmass_relative,
        model="perez",
        albedo=0.2,
    )["poa_global"].fillna(0.0)
    stc = SolarThermalCollector(stc_tilt=tilt, stc_azimuth=azimuth, start_time=day)
    ours = [
        stc.plane_irradiance(dn, dh, i * 600.0, 600.0)["total"]
        for i, (dn, dh) in enumerate(zip(cs.dni, cs.dhi, strict=True))
    ]
    assert sum(ours) == pytest.approx(float(ref.sum()), rel=0.03)


def test_dni_plus_dhi_overstates_a_tilted_collector():
    """The former model's DNI + DHI is 1.3-1.5x the 35° south plane on a clear equinox day."""
    mid, cs = _clear_sky("2026-03-20")
    stc = SolarThermalCollector(start_time="2026-03-20")
    plane = sum(
        stc.plane_irradiance(dn, dh, i * 600.0, 600.0)["total"]
        for i, (dn, dh) in enumerate(zip(cs.dni, cs.dhi, strict=True))
    )
    assert 1.25 < float((cs.dni + cs.dhi).sum()) / plane < 1.6


def test_beam_is_dropped_with_the_sun_below_the_horizon_and_a_warning_says_so():
    stc = SolarThermalCollector()  # equinox, Seoul: midnight is dark
    with pytest.warns(UserWarning, match="below the horizon"):
        p = stc.plane_irradiance(800.0, 100.0, 0.0, 600.0)
    assert p["beam"] == 0.0
    beta = math.radians(35.0)
    # isotropic sky plus the ground's reflection of the (diffuse-only) horizontal irradiance
    assert p["total"] == pytest.approx(100.0 * (1 + math.cos(beta)) / 2.0 + 0.2 * 100.0 * (1 - math.cos(beta)) / 2.0)


def test_sunrise_step_uses_its_sunlit_part():
    """An hourly step whose midpoint is before sunrise still sees the beam of its sunlit part."""
    stc = SolarThermalCollector(stc_tilt=90.0, stc_azimuth=90.0)  # east wall
    sunrise_hour = 6.0  # step 06:00-07:00 at the equinox; the sun rises at about 06:39
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        p = stc.plane_irradiance(300.0, 20.0, sunrise_hour * 3600.0, 3600.0)
    assert p["beam"] > 100.0


def test_unknown_sky_model_is_rejected():
    with pytest.raises(ValueError, match="sky_model"):
        SolarThermalCollector(sky_model="klucher")
    with pytest.raises(ValueError, match="sky_model"):
        plane_of_array_irradiance(1.0, 1.0, zenith=30, azimuth=180, tilt=0, surface_azimuth=180, sky_model="x")


def test_decompose_ghi_honours_the_decomposition_and_returns_dni_dhi():
    times = pd.date_range("2026-03-20 06:00", "2026-03-20 18:00", freq="h", tz="Asia/Seoul")
    ghi = SEOUL.get_clearsky(times)["ghi"]
    erbs = decompose_ghi_to_poa(ghi, 37.57, 126.98, 35.0, 180.0, decomposition="erbs")
    disc = decompose_ghi_to_poa(ghi, 37.57, 126.98, 35.0, 180.0, decomposition="disc")
    assert {"dni", "dhi", "poa_global"} <= set(erbs.columns)
    assert not np.allclose(erbs["dni"], disc["dni"])
    with pytest.raises(ValueError, match="decomposition"):
        decompose_ghi_to_poa(ghi, 37.57, 126.98, 35.0, 180.0, decomposition="bogus")


# ---------------------------------------------------------------------------
# collector (ISO 9806) and its parameters
# ---------------------------------------------------------------------------

NOON = 12.5 * 3600.0  # about solar noon at the default site and start day


def _gain(stc: SolarThermalCollector, T_in_C: float = 30.0, T0_C: float = 20.0, **kw) -> float:
    r = stc.calc_performance(800.0, 100.0, cu.C2K(T_in_C), cu.C2K(T0_C), time_s=NOON, **kw)
    return r["Q_stc_w_out"] - r["Q_stc_w_in"]


def test_area_tilt_and_azimuth_change_the_gain():
    base = _gain(SolarThermalCollector())
    assert _gain(SolarThermalCollector(A_stc=4.0)) == pytest.approx(2.0 * base, rel=1e-9)
    assert _gain(SolarThermalCollector(stc_tilt=90.0)) < 0.8 * base
    assert _gain(SolarThermalCollector(stc_azimuth=0.0)) < 0.5 * base


def test_calc_performance_needs_the_time():
    with pytest.raises(TypeError, match="time_s"):
        SolarThermalCollector().calc_performance(800.0, 100.0, 300.0, 293.15)


def test_efficiency_follows_iso_9806_on_the_mean_temperature():
    """Q_u = A·[S − a1·ΔT_m − a2·ΔT_m|ΔT_m|] and Q_u = G·(T_out − T_in) hold together."""
    stc = SolarThermalCollector(A_stc=3.0)
    T_in, T0 = cu.C2K(50.0), cu.C2K(15.0)
    r = stc.calc_performance(700.0, 150.0, T_in, T0, time_s=NOON)
    plane = stc.plane_irradiance(700.0, 150.0, NOON)
    S = stc.absorbed_irradiance(plane)
    dTm = 0.5 * (T_in + r["T_stc_w_out_K"]) - T0
    q_iso = stc.A_stc * (S - stc.a1_stc * dTm - stc.a2_stc * dTm * abs(dTm))
    G = c_w * rho_w * stc.loop_flow
    assert r["Q_stc_w_out"] - r["Q_stc_w_in"] == pytest.approx(q_iso, rel=1e-9)
    assert r["Q_stc_w_out"] - r["Q_stc_w_in"] == pytest.approx(G * (r["T_stc_w_out_K"] - T_in), rel=1e-9)
    assert r["Q_sol_stc"] == pytest.approx(r["Q_stc_w_out"] - r["Q_stc_w_in"] + r["Q_l_stc"], rel=1e-12)


def test_efficiency_at_ambient_mean_temperature_is_eta0_times_the_incidence_modifier():
    stc = SolarThermalCollector()
    plane = {"beam": 1000.0, "sky": 0.0, "ground": 0.0, "total": 1000.0, "aoi": 0.0}
    T0 = cu.C2K(20.0)
    # inlet chosen so that the mean fluid temperature equals the ambient (no thermal loss):
    # G·(T_out − T_in) = A·S with T_in = T0 − A·S/(2G)
    S = stc.absorbed_irradiance(plane)
    G = c_w * rho_w * stc.loop_flow
    r = stc.calc_performance(0.0, 0.0, T0 - stc.A_stc * S / (2.0 * G), T0, plane=plane)
    assert r["T_stc_K"] == pytest.approx(T0, abs=1e-6)
    assert r["eta_stc"] == pytest.approx(stc.eta0_stc, rel=1e-6)


def test_default_collector_is_a_realistic_flat_plate():
    stc = SolarThermalCollector()
    assert 0.75 <= stc.eta0_stc <= 0.82
    # ISO 9806 test flow, about 1.2 L/(min·m²), scales with the area
    assert stc.loop_flow * 60000.0 / stc.A_stc == pytest.approx(1.2, rel=0.01)
    assert SolarThermalCollector(A_stc=4.0).loop_flow == pytest.approx(2.0 * stc.loop_flow)
    plane = {"beam": 1000.0, "sky": 0.0, "ground": 0.0, "total": 1000.0, "aoi": 0.0}
    assert 150.0 <= cu.K2C(stc.stagnation_temperature_K(plane, cu.C2K(30.0))) <= 200.0
    # efficiency at a typical operating point (ΔT_m/G = 0.05 m²K/W) in the usual 0.5-0.65 band
    T0 = cu.C2K(20.0)
    r = stc.calc_performance(0.0, 0.0, T0 + 50.0, T0, plane=plane)
    assert 0.45 < r["eta_stc"] < 0.65


def test_incidence_angle_modifier_reduces_oblique_beam():
    stc = SolarThermalCollector()
    normal = {"beam": 800.0, "sky": 0.0, "ground": 0.0, "total": 800.0, "aoi": 0.0}
    oblique = {**normal, "aoi": 60.0}
    assert stc.absorbed_irradiance(oblique) == pytest.approx(
        stc.absorbed_irradiance(normal) * (1 - stc.b0_stc), rel=1e-12
    )


def test_deprecated_area_and_construction_parameters_are_mapped_with_a_warning():
    with pytest.warns(DeprecationWarning, match="A_stc_pipe"):
        stc = SolarThermalCollector(A_stc_pipe=3.0)
    assert stc.A_stc == 3.0 and stc.A_stc_pipe is None
    with pytest.raises(ValueError, match="once"), warnings.catch_warnings():
        warnings.simplefilter("ignore")
        SolarThermalCollector(A_stc=4.0, A_stc_pipe=3.0)
    with pytest.warns(DeprecationWarning, match="alpha_stc"):
        stc = SolarThermalCollector(alpha_stc=0.9)
    assert stc.eta0_stc == 0.9
    with pytest.warns(DeprecationWarning, match="h_o_stc"):
        stc = SolarThermalCollector(h_o_stc=15.0)
    # the former two-path construction at its defaults: about 4.1 W/(m²·K), linear
    assert stc.a1_stc == pytest.approx(4.08, abs=0.01) and stc.a2_stc == 0.0


def test_pv_geometry_changes_the_output():
    def out(**kw):
        return PhotovoltaicSystem(**kw).calc_performance(800.0, 100.0, 293.15, time_s=NOON)["E_pv_out"]

    base = out()
    assert out(A_pv=10.0) == pytest.approx(2.0 * base, rel=1e-9)
    assert out(pv_tilt=90.0) < 0.8 * base
    assert out(pv_azimuth=0.0) < 0.5 * base


def test_incidence_modifier_is_applied_before_averaging_the_step():
    """A step in which the sun passes behind the surface keeps the beam it had
    while in front: the modifier is applied to each sub-sample, not to the mean
    angle (an east-facing wall, 12:00-13:00 at the equinox: the mean incidence
    angle is ~90°, whose modifier is 0, while the first samples still see the sun)."""
    pv = PhotovoltaicSystem(pv_tilt=90.0, pv_azimuth=90.0, albedo=0.0, start_time="2026-03-20")
    t0, dt = 12 * 3600.0, 3600.0
    plane = pv.plane_irradiance(800.0, 0.0, t0, dt)
    sub = [pv.plane_irradiance(800.0, 0.0, t0 + (k + 0.5) * dt / 6) for k in range(6)]
    sunlit = [s for s in sub if s["zenith"] < 90.0]
    expected = float(np.mean([ashrae_iam(s["aoi"], pv.b0_pv) * s["beam"] for s in sunlit]))
    assert expected > 5.0  # some of the hour has the sun in front of the wall
    assert plane["beam_iam"] == pytest.approx(expected, rel=1e-12)
    assert ashrae_iam(plane["aoi"], pv.b0_pv) * plane["beam"] < 0.5 * expected  # what the mean angle would give
    # the collector and the PV array both use the per-sample value
    stc = SolarThermalCollector(stc_tilt=90.0, stc_azimuth=90.0, albedo=0.0, start_time="2026-03-20")
    p_stc = stc.plane_irradiance(800.0, 0.0, t0, dt)
    assert stc.absorbed_irradiance(p_stc) == pytest.approx(stc.eta0_stc * p_stc["beam_iam"], rel=1e-12)
    out = pv.calc_performance(800.0, 0.0, 293.15, plane=plane)
    assert out["E_pv_out"] > 0.0
    # a single instant is unchanged: modifier of that angle times that beam
    one = pv.plane_irradiance(800.0, 0.0, 9 * 3600.0)
    assert one["beam_iam"] == pytest.approx(ashrae_iam(one["aoi"], pv.b0_pv) * one["beam"], rel=1e-15)


# ---------------------------------------------------------------------------
# scenario classes: preheat (T8), tank-loop high limit (T9), second law
# ---------------------------------------------------------------------------

DT = 600
N = 144
T_SUP = 12.0


def _day(name: str, hp_off: bool = False, **stc_kw):
    mid, cs = _clear_sky("2026-03-20", DT)
    hod = np.arange(N) * DT / 3600.0
    draw = (np.where((hod >= 7) & (hod < 8), 6.0, 0.0) + np.where((hod >= 12) & (hod < 13), 5.0, 0.0)) / 60000.0
    stc = SolarThermalCollector(A_stc=4.0, start_time="2026-03-20", **stc_kw)
    cls = getattr(tmhp, name)
    ground = dict(N_1=2, N_2=1, H_b=100.0, dt_s=float(DT), t_max_s=48 * 3600.0)
    hp = {"hp_on_schedule": [(0.0, 0.0)]} if hp_off else {}
    m = cls(stc=stc, **hp) if name.startswith("ASHPB") else cls(stc=stc, **ground, **hp)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df = m.analyze_dynamic(
            N * DT,
            DT,
            55.0,
            draw,
            np.full(N, 12.0),
            I_DN_schedule=cs.dni.to_numpy(),
            I_dH_schedule=cs.dhi.to_numpy(),
            T_sup_w_schedule=np.full(N, T_SUP),
            T_sur_schedule=np.full(N, 20.0),
        )
    return m, df


@pytest.fixture(scope="module", params=["ASHPB_STC_preheat", "GSHPB_STC_preheat"])
def preheat_day(request):
    return request.param, *_day(request.param)


def test_preheat_collector_gain_enters_the_tank_through_its_inflow(preheat_day):
    name, m, df = preheat_day
    active = df["stc_active [-]"].astype(bool).to_numpy()
    assert active.any()
    gain = (df["Q_stc_w_out [W]"] - df["Q_stc_w_in [W]"]).fillna(0.0).to_numpy()
    inflow = (
        c_w * rho_w * df["dV_tank_w_in [m3/s]"].fillna(0.0).to_numpy() * (df["T_tank_w_in [°C]"].to_numpy() - T_SUP)
    )
    np.testing.assert_allclose(inflow[active], gain[active], rtol=1e-6, atol=1e-6)
    np.testing.assert_allclose(df["Q_solar_tank [W]"].to_numpy(), np.where(active, gain, 0.0), rtol=1e-9, atol=1e-9)
    # the collector inlet is the mains, its flow the tank's make-up flow
    np.testing.assert_allclose(df.loc[active, "T_stc_w_in [°C]"], T_SUP)
    np.testing.assert_allclose(df.loc[active, "dV_stc [m3/s]"], df.loc[active, "dV_tank_w_in [m3/s]"], rtol=1e-12)
    assert (df["E_stc_pump [W]"] == 0.0).all()


def test_preheat_mixer_cold_side_exergy_is_at_the_mains_temperature(preheat_day):
    """Only the tank make-up passes through the collector; the mixing valve's cold
    side is unheated mains water, so its exergy is evaluated at the mains
    temperature in both families (GSHPB's base model uses the tank-inlet column)."""
    from tmhp.thermodynamics import calc_exergy_flow

    _, _, df = preheat_day
    cold = df["dV_mix_sup_w_in [m3/s]"].fillna(0.0)
    heated = df["stc_active [-]"].astype(bool) & (cold > 0.0) & (df["T_tank_w_in [°C]"] > T_SUP + 1.0)
    assert heated.any()  # preheated make-up while the valve draws cold water
    T0_K = cu.C2K(df["T0 [°C]"])
    at_mains = calc_exergy_flow(c_w * rho_w * cold, cu.C2K(T_SUP), T0_K)
    np.testing.assert_allclose(df["X_mix_sup_w_in [W]"], at_mains, rtol=1e-12, atol=1e-12)
    xc = df["X_tank_w_out [W]"].fillna(0.0) + at_mains - df["X_mix_w_out [W]"].fillna(0.0)
    known = df["Xc_mix [W]"].notna()  # ASHPB leaves the idle valve as NaN
    assert known[heated].all()
    np.testing.assert_allclose(df.loc[known, "Xc_mix [W]"], xc[known], rtol=1e-9, atol=1e-9)


@pytest.fixture(scope="module", params=["ASHPB_STC_tank", "GSHPB_STC_tank", "GSHPB_STC_ground", "GSHPB_STC_routed"])
def pumped_day(request):
    return request.param, *_day(request.param)


def test_no_collector_heat_flows_into_a_warmer_sink(pumped_day):
    """Second law: heat enters the tank or the ground only from a warmer collector outlet."""
    _, _, df = pumped_day
    carried = (df["Q_stc_w_out [W]"] - df["Q_stc_w_in [W]"]).fillna(0.0).to_numpy()
    into = carried > 0.0
    assert into.any()
    assert (df["T_stc_w_out [°C]"].to_numpy()[into] > df["T_stc_w_in [°C]"].to_numpy()[into]).all()
    assert (carried >= 0.0).all()


def test_pumped_loop_runs_on_the_differential_controller(pumped_day):
    name, m, df = pumped_day
    on = df["stc_active [-]"].astype(bool).to_numpy()
    rise = (df["T_stc_w_out [°C]"] - df["T_stc_w_in [°C]"]).to_numpy()
    # At the step start the running outlet was dT_stc_off above the sink; the solved step
    # can end a little lower (tank warmed during the step), never at or below the sink.
    assert (rise[on] > 0.0).all()
    assert (df.loc[on, "E_stc_pump [W]"] > 0).all() and (df.loc[~on, "E_stc_pump [W]"] == 0).all()
    assert not on[:30].any(), "loop ran before sunrise"


@pytest.mark.parametrize("name", ["ASHPB_STC_tank", "GSHPB_STC_tank"])
def test_tank_loop_stops_at_the_storage_limit_within_the_step(name):
    from tmhp._stc_scenarios import TANK_LIMIT_RAMP_K

    m, df = _day(name, hp_off=True, T_stc_tank_max=62.0)
    T = df["T_tank_w [°C]"].to_numpy()
    share = df["stc_runtime_fraction [-]"].to_numpy()
    assert T.max() > 61.5, "the limit is not exercised"
    assert T.max() <= 62.0 + TANK_LIMIT_RAMP_K + 1e-9
    partial = (share > 0.0) & (share < 1.0)
    assert partial.any(), "no step stopped part-way at the limit"
    # pump power and collector flow are the running values times the share
    np.testing.assert_allclose(df["E_stc_pump [W]"], share * m._stc.E_stc_pump, rtol=1e-12)
    np.testing.assert_allclose(df["dV_stc [m3/s]"], share * m._stc.loop_flow, rtol=1e-12)


@pytest.mark.parametrize("name", ["ASHPB_STC_tank", "GSHPB_STC_tank"])
def test_tank_loop_heat_does_not_depend_on_the_time_step(name):
    """Solar heat into the tank over 3 sunny hours at dt 60 s and 600 s agree within 3 %."""
    out = {}
    for dt in (60, 600):
        n = 3 * 3600 // dt
        stc = SolarThermalCollector(A_stc=4.0, start_time="2026-03-20 10:00", T_stc_tank_max=70.0)
        cls = getattr(tmhp, name)
        kw = {} if name.startswith("ASHPB") else dict(N_1=2, N_2=1, H_b=100.0, dt_s=float(dt), t_max_s=6 * 3600.0)
        m = cls(stc=stc, hp_on_schedule=[(0.0, 0.0)], **kw)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            df = m.analyze_dynamic(
                n * dt,
                dt,
                60.0,
                np.zeros(n),
                np.full(n, 15.0),
                I_DN_schedule=np.full(n, 850.0),
                I_dH_schedule=np.full(n, 110.0),
                T_sup_w_schedule=np.full(n, T_SUP),
            )
        out[dt] = (float(df["Q_solar_tank [W]"].sum() * dt), float(df["T_tank_w [°C]"].max()))
    assert out[600][0] == pytest.approx(out[60][0], rel=0.03)
    assert max(out[60][1], out[600][1]) <= 70.0 + 0.05 + 1e-9


# ---------------------------------------------------------------------------
# UV lamp (T2 and step sampling)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("dt", [60, 600, 3600, 7200])
def test_uv_lamp_energy_is_the_schedule_integral_for_any_step(dt):
    from tmhp.uv_treatment import calc_uv_lamp_energy

    period, n_on, on_s = 3 * 3600.0, 2, 12.0
    total = sum(calc_uv_lamp_energy(t, dt, period, n_on, on_s, 1000.0) for t in np.arange(0.0, 6 * 3600.0, dt))
    assert total == pytest.approx(1000.0 * 2 * n_on * on_s, rel=1e-12)


def test_uv_lamp_energy_matches_the_instantaneous_schedule():
    from tmhp.uv_treatment import calc_uv_lamp_energy, calc_uv_lamp_power

    args = (3 * 3600.0, 3, 900.0, 40.0)
    fine = sum(calc_uv_lamp_power(t, *args) for t in np.arange(0.0, 3 * 3600.0, 1.0))
    assert calc_uv_lamp_energy(0.0, 3 * 3600.0, *args) == pytest.approx(fine, rel=1e-9)


@pytest.mark.parametrize("dt", [60, 600, 3600])
def test_uv_lamp_heats_the_tank_once_with_its_water_share(dt):
    n = 3 * 3600 // dt
    lamp = UVLamp(lamp_watts=1000.0, exposure_sec=12.0, num_switching=1, period_sec=3 * 3600.0)
    m = tmhp.AirSourceHeatPumpBoiler(uv=lamp, hp_on_schedule=[(0.0, 0.0)])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df = m.analyze_dynamic(n * dt, dt, 50.0, np.zeros(n), np.full(n, 20.0), T_sup_w_schedule=np.full(n, 15.0))
    lamp_J = float(df["E_uv [W]"].sum() * dt)
    assert lamp_J == pytest.approx(12_000.0, rel=1e-9)
    gain_J = m.C_tank * (df["T_tank_w [°C]"].iloc[-1] - 50.0) + float(df["Q_tank_loss [W]"].sum() * dt)
    assert gain_J == pytest.approx(lamp.heat_to_water_fraction * lamp_J, rel=1e-3)
    np.testing.assert_allclose(df["Q_uv_tank [W]"], lamp.heat_to_water_fraction * df["E_uv [W]"])


def test_uv_lamp_schedule_must_fit_its_period():
    with pytest.raises(ValueError, match="period_sec"):
        UVLamp(lamp_watts=10.0, exposure_sec=4000.0, num_switching=3, period_sec=3 * 3600.0)
    with pytest.raises(ValueError, match="heat_to_water_fraction"):
        UVLamp(lamp_watts=10.0, heat_to_water_fraction=1.2)


def test_single_count_uv_workaround_is_gone():
    import tmhp.subsystems as subs

    assert not hasattr(subs, "single_count_uv_heat")


# ---------------------------------------------------------------------------
# mains water temperature (T11)
# ---------------------------------------------------------------------------

MONTHLY = np.array([-2.4, 0.4, 5.7, 12.5, 17.8, 22.2, 24.9, 25.7, 21.2, 14.8, 7.2, 0.4])


def test_mains_temperature_matches_energyplus_values():
    """Values of EnergyPlus' WaterMainsTempFromCorrelation for a Seoul-like climate."""
    from tmhp.dhw import calc_cold_water_temp

    df = pd.DataFrame({"T_avg": MONTHLY})
    # Hand evaluation in °F: Tavg 54.95 °F, ΔT 50.94 °F, ratio 0.5095, lag 24.05 d.
    assert calc_cold_water_temp(df, "2026-01-15") == pytest.approx(9.38, abs=0.01)
    assert calc_cold_water_temp(df, "2026-08-20") == pytest.approx(22.87, abs=0.01)


def test_mains_temperature_southern_hemisphere_and_freezing_floor():
    from tmhp.dhw import calc_cold_water_temp

    df = pd.DataFrame({"T_avg": MONTHLY})
    north = calc_cold_water_temp(df, "2026-01-15")
    south = calc_cold_water_temp(df, "2026-01-15", latitude=-33.9)
    mean = (MONTHLY.mean() * 1.8 + 32 + 6 - 32) / 1.8
    assert south - mean == pytest.approx(mean - north, abs=1e-9)
    arctic = pd.DataFrame({"T_avg": MONTHLY - 25.0})
    assert calc_cold_water_temp(arctic, "2026-02-15") == 0.0


@pytest.mark.parametrize("name", ["ASHPB_STC_tank", "GSHPB_STC_tank"])
def test_boiling_protection_holds_within_the_step(name):
    """The step-start check admits the loop; as the tank warms in the step the outlet would
    pass T_stc_boil, so the loop stops part-way instead (the outlet never exceeds it)."""
    from tmhp._stc_scenarios import TANK_LIMIT_RAMP_K

    dt, n = 600, 18
    # A slow loop (1.6 L/min on 4 m²): the outlet runs ~15 K above the tank.
    stc = SolarThermalCollector(
        A_stc=4.0, dV_stc_w=2.7e-5, start_time="2026-06-21 10:00", T_stc_tank_max=95.0, T_stc_boil=97.0
    )
    cls = getattr(tmhp, name)
    kw = {} if name.startswith("ASHPB") else dict(N_1=2, N_2=1, H_b=100.0, dt_s=float(dt), t_max_s=6 * 3600.0)
    m = cls(stc=stc, hp_on_schedule=[(0.0, 0.0)], **kw)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df = m.analyze_dynamic(
            n * dt,
            dt,
            78.0,
            np.zeros(n),
            np.full(n, 25.0),
            I_DN_schedule=np.full(n, 850.0),
            I_dH_schedule=np.full(n, 110.0),
            T_sup_w_schedule=np.full(n, T_SUP),
        )
    on = df["stc_active [-]"].astype(bool).to_numpy()
    share = df["stc_runtime_fraction [-]"].to_numpy()
    T_out = df["T_stc_pump_w_out [°C]"].to_numpy()
    assert on.any()
    assert T_out[on].max() <= 97.0 + TANK_LIMIT_RAMP_K + 1e-9
    assert ((share[on] > 0.0) & (share[on] <= 1.0)).all()
    assert df["T_tank_w [°C]"].max() < 95.0 + TANK_LIMIT_RAMP_K + 1e-9
    # Directly: an active loop whose end-of-step outlet would pass T_stc_boil (tank still
    # below its limit) runs only a share of the step, and none once 0.05 K over.
    from tmhp.dynamic_context import StepContext

    ctx = StepContext(
        n=0, current_time_s=2 * 3600.0, current_hour=2.0, hour_of_day=12.0, T0=25.0, T0_K=cu.C2K(25.0),
        activation_flags={"stc": True}, T_tank_w_K=cu.C2K(80.0), tank_level=1.0, dV_mix_w_out=0.0,
        I_DN=850.0, I_dH=110.0, T_sup_w_K=cu.C2K(T_SUP),
    )  # fmt: skip
    state = m._tank_loop_state(ctx, dt)
    assert state["stc_active"]
    outlet = {T: cu.K2C(m._tank_loop_heat(ctx, state, cu.C2K(T))[2]["T_stc_pump_w_out_K"]) for T in (81.0, 84.0)}
    assert outlet[81.0] < 97.0 < outlet[84.0]
    assert m._tank_loop_heat(ctx, state, cu.C2K(81.0))[1] == 1.0
    assert m._tank_loop_heat(ctx, state, cu.C2K(84.0))[1] == 0.0


@pytest.mark.parametrize("name", ["ASHPB_STC_preheat", "GSHPB_STC_preheat"])
def test_preheat_outlet_limit_holds_at_the_end_of_step_flow(name):
    """The preheated feed never exceeds T_stc_tank_max, whatever flow the solved tank draws."""
    from tmhp._stc_scenarios import TANK_LIMIT_RAMP_K

    m, df = _day(name, T_stc_tank_max=30.0)
    T_in = df["T_tank_w_in [°C]"].to_numpy()
    assert T_in.max() <= 30.0 + TANK_LIMIT_RAMP_K + 1e-9
    plane = m._stc.plane_irradiance(800.0, 100.0, 12.5 * 3600.0)
    ctx = type(
        "Ctx",
        (),
        {"T_sup_w_K": cu.C2K(T_SUP), "dV_mix_w_out": 0.3 / 60000.0, "I_DN": 800.0, "I_dH": 100.0, "T0_K": cu.C2K(12.0)},
    )()
    ctrl = type("Ctrl", (), {"dV_tank_w_in_ctrl": None})()
    flow, T_pre, r, share = m._preheat(ctx, ctrl, plane, cu.C2K(60.0))
    assert cu.K2C(r["T_stc_w_out_K"]) > 30.0 + TANK_LIMIT_RAMP_K  # a slow draw would pass the limit
    assert share == 0.0 and T_pre == pytest.approx(cu.C2K(T_SUP))
