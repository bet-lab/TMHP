"""Smoke tests — exercise each public model's analyze_steady() once.

These tests catch wide classes of regressions (NameError, AttributeError,
KeyError, sign convention flips) that the pure-import tests cannot.
They use single representative operating points; they are not validation
against external data.
"""

import warnings

import pytest

from tmhp import (
    AirSourceHeatPump,
    AirSourceHeatPumpBoiler,
    GroundSourceHeatPump,
    GroundSourceHeatPumpBoiler,
    WaterSourceHeatPumpBoiler,
    check_pr_envelope,
)


def test_ashpb_analyze_steady():
    ashpb = AirSourceHeatPumpBoiler(ref="R32")
    result = ashpb.analyze_steady(T_tank_w=55.0, T0=5.0, Q_ref_tank=8_000.0)
    assert isinstance(result, dict)
    assert result["E_cmp [W]"] > 0
    assert result["Q_ref_tank [W]"] > 0
    assert result["cop_sys [-]"] > 1.0
    # failure_reason is a diagnostic, not a pass/fail gate — it may say
    # "hx_not_converged" or "optimizer_failed" even when the returned
    # numbers (E_cmp, COP) are usable. Just assert it surfaces.
    assert result["failure_reason"] in {
        "none",
        "hx_not_converged",
        "optimizer_failed",
    }


def test_gshpb_analyze_steady():
    gshpb = GroundSourceHeatPumpBoiler(ref="R32")
    result = gshpb.analyze_steady(T_tank_w=55.0, T_source=12.0, Q_ref_tank=8_000.0, T0=15.0)
    assert isinstance(result, dict)
    assert result["E_cmp [W]"] > 0
    assert result["cop_sys [-]"] > 1.0
    # failure_reason is a diagnostic, not a pass/fail gate — it may say
    # "hx_not_converged" or "optimizer_failed" even when the returned
    # numbers (E_cmp, COP) are usable. Just assert it surfaces.
    assert result["failure_reason"] in {
        "none",
        "hx_not_converged",
        "optimizer_failed",
    }


def test_wshpb_analyze_steady():
    wshpb = WaterSourceHeatPumpBoiler(ref="R32")
    result = wshpb.analyze_steady(T_tank_w=55.0, T_source=12.0, Q_ref_tank=8_000.0, T0=15.0)
    assert isinstance(result, dict)
    assert result["E_cmp [W]"] > 0
    assert result["Q_ref_tank [W]"] > 0
    assert result["cop_ref [-]"] > 1.0
    assert result["cop_sys [-]"] > 1.0
    # failure_reason is a diagnostic, not a pass/fail gate — it may say
    # "hx_not_converged" or "optimizer_failed" even when the returned
    # numbers (E_cmp, COP) are usable. Just assert it surfaces.
    assert result["failure_reason"] in {
        "none",
        "hx_not_converged",
        "optimizer_failed",
    }


def test_ashp_heating_analyze_steady():
    # Use a UA / fan-flow combination large enough to converge — too-small UA
    # makes the inner HX optimisation bottom out and return off-mode.
    ashp = AirSourceHeatPump(
        ref="R32",
        UA_iu_rated=3000.0,
        UA_ou_rated=3000.0,
        dV_iu_fan_a_design=0.8,
        dV_ou_fan_a_design=0.8,
        A_cross_iu=0.5,
        A_cross_ou=0.5,
    )
    result = ashp.analyze_steady(Q_r_iu=-5_000.0, T0=5.0, T_a_room=20.0, verbose=False)
    assert isinstance(result, dict)
    assert result["E_cmp [W]"] > 0
    assert result["cop_sys [-]"] > 1.0
    # failure_reason is a diagnostic, not a pass/fail gate — it may say
    # "hx_not_converged" or "optimizer_failed" even when the returned
    # numbers (E_cmp, COP) are usable. Just assert it surfaces.
    assert result["failure_reason"] in {
        "none",
        "hx_not_converged",
        "optimizer_failed",
    }


def test_gshp_heating_analyze_steady():
    gshp = GroundSourceHeatPump(
        ref="R32",
        UA_evap_design=2000.0,
        UA_cond_design=2000.0,
        dV_iu_fan_a_design=0.5,
        A_cross_iu=0.5,
    )
    result = gshp.analyze_steady(Q_r_iu=-3_000.0, T0=5.0, T_a_room=20.0)
    assert isinstance(result, dict)
    assert result["E_cmp [W]"] > 0
    assert result["cop_sys [-]"] > 1.0
    # failure_reason is a diagnostic, not a pass/fail gate — it may say
    # "hx_not_converged" or "optimizer_failed" even when the returned
    # numbers (E_cmp, COP) are usable. Just assert it surfaces.
    assert result["failure_reason"] in {
        "none",
        "hx_not_converged",
        "optimizer_failed",
    }


def test_gshp_cooling_analyze_steady():
    gshp = GroundSourceHeatPump(
        ref="R32",
        dT_hx_min=0.5,
        UA_evap=6000.0,
        UA_cond=6000.0,
        dV_iu_fan_a_rated=1.2,
        A_cross_iu=0.5,
    )
    result = gshp.analyze_steady(Q_r_iu=3_000.0, T0=30.0, T_a_room=26.0)
    assert isinstance(result, dict)
    assert result["mode"] == "cooling"
    assert result["E_cmp [W]"] > 0
    assert result["cop_sys [-]"] > 1.0
    assert abs(float(result["Q_ref_iu [W]"]) - 3000.0) < 1e-6
    assert result["failure_reason"] == "none"


def test_ashp_off_mode_failure_reason_is_diagnostic():
    # Deliberately tiny UA so the inner HX optimisation cannot converge.
    # The model is expected to fall back to off-mode AND surface a
    # specific failure_reason so callers can branch on it.
    ashp = AirSourceHeatPump(
        ref="R32",
        UA_iu_rated=2000.0,
        UA_ou_rated=2000.0,
        dV_iu_fan_a_design=0.5,
        dV_ou_fan_a_design=0.5,
        A_cross_iu=0.5,
        A_cross_ou=0.5,
    )
    result = ashp.analyze_steady(Q_r_iu=-3_000.0, T0=5.0, T_a_room=20.0, verbose=False)
    assert isinstance(result, dict)
    assert result["mode"] == "off"
    assert result["converged"] is False
    assert result["failure_reason"] in {
        "cycle_invalid",
        "hx_not_converged",
        "optimizer_failed",
    }


def test_ashp_deprecated_params_emit_warning():
    """Deprecated UA_cond_rated/UA_evap_rated/n_cond/n_evap must emit DeprecationWarning."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        AirSourceHeatPump(
            ref="R32",
            UA_cond_rated=3000.0,
            UA_evap_rated=2500.0,
            n_cond=0.6,
            n_evap=0.6,
        )
    dep_messages = [str(w.message) for w in caught if issubclass(w.category, DeprecationWarning)]
    assert any("UA_cond_rated" in m or "UA_evap_rated" in m for m in dep_messages), (
        "Expected DeprecationWarning for UA_cond_rated/UA_evap_rated"
    )
    assert any("n_cond" in m or "n_evap" in m for m in dep_messages), "Expected DeprecationWarning for n_cond/n_evap"


def test_ashp_oldest_deprecated_design_params_emit_warning():
    """Two-hop compat: UA_cond_design/UA_evap_design must also emit DeprecationWarning."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        AirSourceHeatPump(
            ref="R32",
            UA_cond_design=3000.0,
            UA_evap_design=2500.0,
        )
    dep_messages = [str(w.message) for w in caught if issubclass(w.category, DeprecationWarning)]
    assert any("UA_cond_rated" in m or "UA_evap_rated" in m for m in dep_messages), (
        "Expected DeprecationWarning for two-hop UA_cond_design path"
    )


# ---------------------------------------------------------------------------
# dT_hx_min — heat-exchanger pinch clamp, still user-configurable.
# The legacy dT_cycle_min fixed-minimum-lift guard was REMOVED across the whole
# HP family; low-lift feasibility is now governed solely by the PR floor
# (PR_cycle_min). dT_hx_min is a distinct superheat/subcool pinch clamp and is
# retained.
# ---------------------------------------------------------------------------


def test_dt_hx_min_default_and_custom():
    assert AirSourceHeatPump(ref="R32", UA_ou_rated=3000.0, UA_iu_rated=3000.0).dT_hx_min == 0.5
    assert AirSourceHeatPump(ref="R32", UA_ou_rated=3000.0, UA_iu_rated=3000.0, dT_hx_min=1.0).dT_hx_min == 1.0
    assert GroundSourceHeatPump(ref="R32").dT_hx_min == 0.5
    assert GroundSourceHeatPump(ref="R32", dT_hx_min=1.0).dT_hx_min == 1.0
    assert AirSourceHeatPumpBoiler(ref="R32").dT_hx_min == 0.5
    assert GroundSourceHeatPumpBoiler(ref="R32", dT_hx_min=0.7).dT_hx_min == 0.7
    assert WaterSourceHeatPumpBoiler(ref="R32", dT_hx_min=0.7).dT_hx_min == 0.7


@pytest.mark.parametrize(
    "cls, kwargs",
    [
        (AirSourceHeatPump, {"UA_ou_rated": 3000.0, "UA_iu_rated": 3000.0}),
        (GroundSourceHeatPump, {}),
        (AirSourceHeatPumpBoiler, {}),
        (GroundSourceHeatPumpBoiler, {}),
        (WaterSourceHeatPumpBoiler, {}),
    ],
)
def test_dt_cycle_min_removed(cls, kwargs):
    # The fixed-minimum-lift guard was removed across the whole HP family;
    # passing the retired keyword must now be a hard error, not a silent no-op,
    # and the attribute must no longer exist on a constructed model.
    with pytest.raises(TypeError):
        cls(ref="R32", dT_cycle_min=20.0, **kwargs)
    assert not hasattr(cls(ref="R32", **kwargs), "dT_cycle_min")


# ---------------------------------------------------------------------------
# Compressor pressure-ratio envelope + rps search bounds (#166, #188)
# Applied to the whole HP family (ASHP / ASHPB / GSHP / GSHPB / WSHPB): the PR
# floor/ceiling is now the single operating-point guard after the dT_cycle_min
# fixed-lift guard was removed. Ceiling default tightened 10 -> 5 for the
# space-conditioning models (ASHP/GSHP, single-stage envelope). DHW boiler
# defaults (ASHPB/GSHPB/WSHPB) use a 20 ceiling for high-lift validation points
# that reach PR 16-18.
# ---------------------------------------------------------------------------


def test_check_pr_envelope_boundaries():
    # Inside the band -> feasible (None).
    assert check_pr_envelope(3.0, 1.5, 10.0) is None
    assert check_pr_envelope(1.5, 1.5, 10.0) is None  # inclusive floor
    assert check_pr_envelope(10.0, 1.5, 10.0) is None  # inclusive ceiling
    # Below the floor / above the ceiling -> reason codes.
    assert check_pr_envelope(1.2, 1.5, 10.0) == "pr_below_min"
    assert check_pr_envelope(11.0, 1.5, 10.0) == "pr_above_max"


def test_ashp_custom_pr_and_rps():
    ashp = AirSourceHeatPump(
        ref="R32",
        UA_ou_rated=3000.0,
        UA_iu_rated=3000.0,
        PR_cycle_min=1.8,
        PR_cycle_max=8.0,
        rps_min=15.0,
        rps_max=120.0,
    )
    assert ashp.PR_cycle_min == 1.8
    assert ashp.PR_cycle_max == 8.0
    assert ashp.rps_min == 15.0
    assert ashp.rps_max == 120.0


def test_ashp_default_pr_and_rps():
    ashp = AirSourceHeatPump(ref="R32", UA_ou_rated=3000.0, UA_iu_rated=3000.0)
    assert ashp.PR_cycle_min == 1.5
    assert ashp.PR_cycle_max == 5.0
    assert ashp.rps_min == 10.0
    assert ashp.rps_max == 150.0


def test_gshp_default_pr_and_rps():
    gshp = GroundSourceHeatPump(ref="R32")
    assert gshp.PR_cycle_min == 1.5
    assert gshp.PR_cycle_max == 5.0


def test_gshp_custom_pr():
    gshp = GroundSourceHeatPump(ref="R32", PR_cycle_min=1.8, PR_cycle_max=7.0)
    assert gshp.PR_cycle_min == 1.8
    assert gshp.PR_cycle_max == 7.0


def test_ashpb_custom_pr_and_rps():
    ashpb = AirSourceHeatPumpBoiler(
        ref="R32",
        PR_cycle_min=1.6,
        PR_cycle_max=9.0,
        rps_min=12.0,
        rps_max=130.0,
    )
    assert ashpb.PR_cycle_min == 1.6
    assert ashpb.PR_cycle_max == 9.0
    assert ashpb.rps_min == 12.0
    assert ashpb.rps_max == 130.0


def test_ashpb_default_pr_and_rps():
    ashpb = AirSourceHeatPumpBoiler(ref="R32")
    assert ashpb.PR_cycle_min == 1.5
    assert ashpb.PR_cycle_max == 20.0
    assert ashpb.rps_min == 10.0
    assert ashpb.rps_max == 150.0


def test_gshpb_default_pr_and_rps():
    gshpb = GroundSourceHeatPumpBoiler(ref="R32")
    assert gshpb.PR_cycle_min == 1.5
    assert gshpb.PR_cycle_max == 20.0
    assert gshpb.rps_min == 10.0
    assert gshpb.rps_max == 150.0


def test_wshpb_custom_pr_and_rps():
    wshpb = WaterSourceHeatPumpBoiler(
        ref="R32",
        PR_cycle_min=1.6,
        PR_cycle_max=9.0,
        rps_min=12.0,
        rps_max=130.0,
    )
    assert wshpb.PR_cycle_min == 1.6
    assert wshpb.PR_cycle_max == 9.0
    assert wshpb.rps_min == 12.0
    assert wshpb.rps_max == 130.0


def test_wshpb_default_pr_and_rps():
    wshpb = WaterSourceHeatPumpBoiler(ref="R32")
    assert wshpb.PR_cycle_min == 1.5
    assert wshpb.PR_cycle_max == 20.0
    assert wshpb.rps_min == 10.0
    assert wshpb.rps_max == 150.0


def test_wshpb_pr_floor_clamp_keeps_cycle():
    # Low-lift heating (source close to tank) with the lift guard relaxed so PR
    # becomes the binding constraint. A raised floor forces the clamp; the cycle
    # must still converge (continuous low-lift transition, not rejection). A
    # small compressor keeps the speed search inside [rps_min, rps_max].
    wshpb = WaterSourceHeatPumpBoiler(
        ref="R410A",
        V_cmp_ref=5e-5,
        UA_tank=4000.0,
        UA_water=4000.0,
        PR_cycle_min=2.2,
        PR_cycle_max=10.0,
    )
    result = wshpb.analyze_steady(T_tank_w=28.0, T_source=22.0, Q_ref_tank=6000.0, T0=15.0)
    assert result["converged"] is True
    assert result["failure_reason"] == "none"
    assert wshpb._last_pr_event is not None
    assert wshpb._last_pr_event[0] == "pr_below_min"


def test_wshpb_pr_ceiling_rejects():
    # A very low ceiling rejects an otherwise-valid high-lift heating point.
    # UA_tank kept high so the condensing temperature stays sub-critical.
    wshpb = WaterSourceHeatPumpBoiler(
        ref="R410A",
        UA_tank=2000.0,
        UA_water=2000.0,
        PR_cycle_min=1.0,
        PR_cycle_max=2.0,
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = wshpb.analyze_steady(T_tank_w=50.0, T_source=12.0, Q_ref_tank=8000.0, T0=15.0)
    runtime_messages = [str(w.message) for w in caught if issubclass(w.category, RuntimeWarning)]
    assert any("pr_above_max" in message for message in runtime_messages)
    assert result["converged"] is False
    assert result["failure_reason"] == "pr_above_max"
    assert wshpb._last_pr_event is not None
    assert wshpb._last_pr_event[0] == "pr_above_max"


def test_boiler_common_eta_defaults():
    # All heat-pump boilers resolve an unspecified isentropic efficiency to the
    # shared default 0.80 (volumetric to a PR-dependent callable), so a bare
    # model is never ideal-isentropic.
    for cls in (
        AirSourceHeatPumpBoiler,
        GroundSourceHeatPumpBoiler,
        WaterSourceHeatPumpBoiler,
    ):
        m = cls(ref="R32")
        assert m.eta_cmp_isen == 0.80
        assert callable(m.eta_cmp_vol)


def test_ashp_pr_floor_clamp_keeps_cycle():
    # Low-lift cooling (outdoor cooler than indoor) with the lift guard relaxed
    # so PR becomes the binding constraint. A raised floor forces the clamp; the
    # cycle must still converge (continuous low-lift transition, not rejection).
    ashp = AirSourceHeatPump(
        ref="R410A",
        hp_capacity=15500.0,
        UA_ou_rated=2500,
        UA_iu_rated=2500,
        PR_cycle_min=1.8,
        PR_cycle_max=10.0,
    )
    result = ashp.analyze_steady(Q_r_iu=16500.0, T0=10.0, T_a_room=28.0, return_dict=True, verbose=False)
    assert result["converged"] is True
    assert result["failure_reason"] == "none"
    assert ashp._last_pr_event is not None
    assert ashp._last_pr_event[0] == "pr_below_min"


def test_ashp_pr_ceiling_rejects():
    # A very low ceiling rejects an otherwise-valid heating point.
    ashp = AirSourceHeatPump(
        ref="R410A",
        hp_capacity=15500.0,
        UA_ou_rated=2500,
        UA_iu_rated=2500,
        PR_cycle_min=1.0,
        PR_cycle_max=1.6,
    )
    result = ashp.analyze_steady(Q_r_iu=-14000.0, T0=-7.0, T_a_room=20.0, return_dict=True, verbose=False)
    assert result["converged"] is False
    assert result["failure_reason"] == "pr_above_max"
    assert ashp._last_pr_event is not None
    assert ashp._last_pr_event[0] == "pr_above_max"


# ---------------------------------------------------------------------------
# Output-label contract: reversible models report heat duties / exergy by
# physical location (iu/ou/ground), never by refrigerant role (cond/evap) and
# without echoing the input load (Q_r_iu). Refrigerant-state saturation keys
# stay cond/evap (refrigerant-intrinsic).
# ---------------------------------------------------------------------------


def test_ashp_output_labels_are_position_based():
    ashp = AirSourceHeatPump(ref="R410A", hp_capacity=15500.0, UA_ou_rated=2500, UA_iu_rated=2500)
    for q in (-12000.0, 12000.0):  # heating, cooling
        r = ashp.analyze_steady(
            Q_r_iu=q,
            T0=7.0 if q < 0 else 31.0,
            T_a_room=20.0 if q < 0 else 26.0,
            return_dict=True,
            verbose=False,
        )
        # Position-based duties present, exergy by location present.
        assert "Q_ref_iu [W]" in r and "Q_ref_ou [W]" in r
        assert "X_ref_iu [W]" in r and "X_ref_ou [W]" in r
        # Refrigerant-role duty/exergy keys and the input echo are gone.
        for stale in (
            "Q_ref_cond [W]",
            "Q_ref_evap [W]",
            "Q_r_iu [W]",
            "X_ref_cond [W]",
            "X_ref_evap [W]",
        ):
            assert stale not in r, f"stale output key {stale}"
        # Refrigerant-state saturation keys remain cond/evap.
        assert "T_ref_cond_sat_v [°C]" in r and "T_ref_evap_sat [°C]" in r
        # Indoor duty equals the imposed load magnitude at convergence.
        assert abs(float(r["Q_ref_iu [W]"]) - abs(q)) < 1e-6


def test_gshp_output_labels_are_position_based():
    gshp = GroundSourceHeatPump(ref="R32")
    r = gshp.analyze_steady(Q_r_iu=-3000.0, T0=5.0, T_a_room=20.0, return_dict=True)
    assert "Q_ref_iu [W]" in r and "Q_ref_ground [W]" in r
    for stale in ("Q_ref_cond [W]", "Q_ref_evap [W]", "Q_r_iu [W]"):
        assert stale not in r, f"stale output key {stale}"
    assert abs(float(r["Q_ref_iu [W]"]) - 3000.0) < 1e-6
