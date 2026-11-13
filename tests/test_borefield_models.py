"""FieldGroundResponse on the ground-source models: model_kwargs, attach, dynamic response."""

from __future__ import annotations

import warnings

import numpy as np
import pytest

from tmhp import GroundSourceHeatPump, GroundSourceHeatPumpBoiler
from tmhp.borefield import BoreField, FieldGroundResponse
from tmhp.borehole import BoreholePipe
from tmhp.borehole_capacity import DynamicGFunction, enable_borehole_dynamics
from tmhp.constants import c_w, k_w, mu_w, rho_w
from tmhp.ground_coupling import AggregateGFunctionCoupler
from tmhp.source_fluid import SourceFluid

K_S, C_S, RHO_S = 2.0, 800.0, 2000.0
ALPHA_S = K_S / (C_S * RHO_S)
T_MAX = 30 * 86400.0

#: Water at its 20 °C viscosity everywhere, the water FieldGroundResponse assumes (mu_f = mu_w).
WATER_20C = SourceFluid("water", 0.0, rho_w, c_w, mu_w, k_w)


def _flow(lpm: float) -> float:
    return lpm / 60000.0 * rho_w


def _response(field: BoreField, lpm: float, pipe: BoreholePipe | None = None, **kwargs) -> FieldGroundResponse:
    return FieldGroundResponse(field, pipe, k_s=K_S, alpha_s=ALPHA_S, m_flow_total=_flow(lpm), t_max_s=T_MAX, **kwargs)


def _boiler(**kwargs) -> GroundSourceHeatPumpBoiler:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return GroundSourceHeatPumpBoiler(dt_s=600.0, t_max_s=T_MAX, **kwargs)


def _run(model: GroundSourceHeatPumpBoiler, steps: int = 18):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return model.analyze_dynamic(steps * 600.0, 600.0, 52.0, np.full(steps, 4.0 / 60000.0), np.full(steps, 5.0))


@pytest.fixture(scope="module")
def irregular() -> FieldGroundResponse:
    field = BoreField.from_coordinates([(0.0, 0.0), (6.0, 0.0), (3.0, 5.0)], H=[150.0, 200.0, 100.0], D=1.0)
    return _response(field, 60.0)


def test_rectangle_through_attach_reproduces_the_default_model():
    """A 2 x 2 rectangle set up as a field gives the run the rectangular arguments give."""
    # FieldGroundResponse evaluates the borehole resistance for water at mu_w (20 °C):
    # compare like with like (source_fluid="water" takes it at 0 °C).
    reference = _boiler(N_1=2, N_2=2, B=6.0, H_b=150.0, ground_flow_ref_lpm=48.0, source_fluid=WATER_20C)
    response = _response(BoreField.rectangle(2, 2, 6.0, 150.0), 48.0)
    assert response.boundary_condition == "UBWT"
    assert response.R_b == pytest.approx(reference.R_b, rel=1e-9)
    kwargs = response.model_kwargs()
    assert (kwargs["N_1"], kwargs["N_2"], kwargs["H_b"]) == (4, 1, 150.0)
    assert kwargs["rho_s"] * kwargs["c_s"] == pytest.approx(RHO_S * C_S)
    # The models' default is the dynamic borehole, and attach keeps the model's choice.
    model = _boiler(**kwargs, source_fluid=WATER_20C)
    g = response.attach(model)
    assert isinstance(g, DynamicGFunction) and g.field is response.g
    assert model.borehole_dynamics == reference.borehole_dynamics == "transit"
    out_ref, out = _run(reference), _run(model)
    for column in ("T_bhe [°C]", "T_bhe_f_out [°C]", "Q_bhe [W]", "E_cmp [W]"):
        np.testing.assert_allclose(out[column].to_numpy(), out_ref[column].to_numpy(), rtol=1e-6, atol=1e-6)
    # The same with the steady borehole on both sides.
    reference = _boiler(
        N_1=2, N_2=2, B=6.0, H_b=150.0, ground_flow_ref_lpm=48.0, borehole_dynamics=False, source_fluid=WATER_20C
    )
    model = _boiler(**kwargs, borehole_dynamics=False, source_fluid=WATER_20C)
    assert response.attach(model) is response.g and model.borehole_dynamics == "off"
    out_ref, out = _run(reference), _run(model)
    for column in ("T_bhe [°C]", "T_bhe_f_out [°C]", "Q_bhe [W]", "E_cmp [W]"):
        np.testing.assert_allclose(out[column].to_numpy(), out_ref[column].to_numpy(), rtol=1e-6, atol=1e-6)


def test_irregular_field_drives_the_boiler(irregular):
    model = _boiler(**irregular.model_kwargs())
    assert model.n_boreholes == 3 and model.total_borehole_length == pytest.approx(450.0)
    assert model.R_b == irregular.R_b
    assert irregular.attach(model, borehole_dynamics=False) is irregular.g
    assert irregular.boundary_condition == "MIFT"
    out = _run(model)
    # The result columns are the field response for the loads the model reports.
    q_line = out["Q_bhe [W]"].to_numpy() / 450.0
    pulses = np.diff(np.concatenate(([0.0], q_line)))
    kernel = np.asarray(irregular.g.step_mean(np.arange(len(q_line)) * 600.0, 600.0))
    rise = np.array([np.dot(pulses[: n + 1], kernel[n::-1]) for n in range(len(q_line))])
    np.testing.assert_allclose(out["T_bhe [°C]"].to_numpy(), model.Ts - rise, atol=1e-9)
    np.testing.assert_allclose(out["T_bhe_f [°C]"].to_numpy(), model.Ts - rise - q_line * irregular.R_b, atol=1e-9)
    assert out["hp_is_on"].any()


def test_attach_with_borehole_dynamics(irregular):
    model = _boiler(**irregular.model_kwargs())
    g = irregular.attach(model, borehole_dynamics=True)
    assert isinstance(g, DynamicGFunction) and g.field is irregular.g
    assert model._gfunc_interp is g and model._ground_coupler._g is g
    assert g.section.steady_resistance == pytest.approx(irregular.R_b, rel=1e-12)
    # Three parallel boreholes of mean depth 150 m sharing 60 L/min: 20 L/min each.
    assert g.transit_time == pytest.approx(g.section.C_f * 150.0 / (_flow(20.0) * 4186.0), rel=2e-3)
    assert model.borehole_dynamics == "transit"
    steady = _boiler(**irregular.model_kwargs())
    irregular.attach(steady, borehole_dynamics=False)
    assert steady.borehole_dynamics == "off"
    # A model constructed with the steady borehole keeps it through attach.
    kept = _boiler(**irregular.model_kwargs(), borehole_dynamics=False)
    assert irregular.attach(kept) is irregular.g and kept.borehole_dynamics == "off"
    out_s, out_d = _run(steady), _run(model)
    on = out_s["hp_is_on"].astype(bool).to_numpy()
    first = int(np.flatnonzero(on)[0])
    assert out_d["T_bhe_f_out [°C]"].iloc[first] - out_s["T_bhe_f_out [°C]"].iloc[first] > 0.3


def test_enable_borehole_dynamics_does_not_override_an_attached_field():
    """The model-level switch assumes the model's single U-tube rectangle; an attached field is not that."""
    pair = BoreField.rectangle(2, 1, 6.0, 150.0)
    response = _response(pair, 24.0, BoreholePipe(kind="double_u_parallel", D_s=0.04), connectivity=[-1, 0])
    for dynamics in (False, True):
        model = _boiler(**response.model_kwargs())
        g = response.attach(model, borehole_dynamics=dynamics)
        with pytest.raises(ValueError, match="attach\\(model, borehole_dynamics=True\\)"):
            enable_borehole_dynamics(model)
        assert model._gfunc_interp is g


def test_attach_refuses_a_model_of_another_field(irregular):
    with pytest.raises(ValueError, match="borehole count"):
        irregular.attach(_boiler())
    kwargs = irregular.model_kwargs()
    with pytest.raises(ValueError, match="loop flow"):
        irregular.attach(_boiler(**{**kwargs, "ground_flow_ref_lpm": 30.0}))
    with pytest.raises(ValueError, match="R_b"):
        irregular.attach(_boiler(**{**kwargs, "R_b": 0.2}))
    with pytest.raises(ValueError, match="alpha_s"):
        irregular.attach(_boiler(**{**kwargs, "c_s": 900.0}))
    custom = AggregateGFunctionCoupler(irregular.g)

    class _Other:
        def reset(self, n_steps, time_arr):
            custom.reset(n_steps, time_arr)

        def wall_temperature_rise(self, n, time_arr, q_unit):
            return custom.wall_temperature_rise(n, time_arr, q_unit)

    with pytest.raises(ValueError, match="injected ground_coupler"):
        irregular.attach(_boiler(ground_coupler=_Other(), **kwargs))
    with pytest.raises(TypeError, match="does not expose a borehole field"):
        irregular.attach(object())
    with pytest.raises(ValueError, match="c_s"):
        irregular.model_kwargs(c_s=0.0)


def test_attach_refuses_what_the_models_cannot_represent(irregular):
    """Non-water heat-capacity rate and pump hydraulics of a non-parallel field (the models assume both)."""
    field = BoreField.rectangle(2, 1, 6.0, 150.0)
    glycol = FieldGroundResponse(
        field, k_s=K_S, alpha_s=ALPHA_S, m_flow_total=_flow(48.0) * 1.03, t_max_s=T_MAX, rho_f=rho_w * 1.03, cp_f=3900.0
    )
    with pytest.raises(ValueError, match="loop fluid as water"):
        glycol.attach(_boiler(**glycol.model_kwargs()))
    # Viscosity and conductivity only change R_b, which the model takes from the field.
    viscous = FieldGroundResponse(
        field, k_s=K_S, alpha_s=ALPHA_S, m_flow_total=_flow(48.0), t_max_s=T_MAX, mu_f=2.0e-3, k_f=0.5
    )
    viscous.attach(_boiler(**viscous.model_kwargs()))
    # ... unless the model also computes the pump power, which uses water's viscosity.
    with pytest.raises(ValueError, match="viscosity of water"):
        viscous.attach(_boiler(hydraulic_pump=True, **viscous.model_kwargs()))
    # Pump hydraulics: fine for equal parallel single U-tubes, refused otherwise.
    parallel = _response(field, 48.0)
    parallel.attach(_boiler(hydraulic_pump=True, **parallel.model_kwargs()))
    series = _response(field, 24.0, connectivity=[-1, 0])
    with pytest.raises(ValueError, match="hydraulic_pump"):
        series.attach(_boiler(hydraulic_pump=True, **series.model_kwargs()))
    with pytest.raises(ValueError, match="hydraulic_pump"):
        irregular.attach(_boiler(hydraulic_pump=True, **irregular.model_kwargs()))
    double = _response(field, 48.0, BoreholePipe(kind="double_u_parallel", D_s=0.04))
    with pytest.raises(ValueError, match="hydraulic_pump"):
        double.attach(_boiler(hydraulic_pump=True, **double.model_kwargs()))
    series.attach(_boiler(**series.model_kwargs()))  # with a given pump power it is accepted


def test_space_conditioning_model_takes_a_field(irregular):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = GroundSourceHeatPump(**irregular.model_kwargs())
    g = irregular.attach(model)
    assert isinstance(g, DynamicGFunction) and g.field is irregular.g and model._gfunc_interp is g
    assert irregular.attach(model, borehole_dynamics=False) is irregular.g
    assert model._gfunc_interp is irregular.g and model.borehole_dynamics == "off"


def test_dynamic_response_follows_pipe_and_circuits():
    pair = BoreField.rectangle(2, 1, 6.0, 150.0)
    parallel = _response(pair, 48.0).dynamic_gfunction()
    series = _response(pair, 24.0, connectivity=[-1, 0]).dynamic_gfunction()
    # Two boreholes in series at the same flow per borehole: twice the way through the ground.
    assert series.transit_time == pytest.approx(2.0 * parallel.transit_time, rel=0.02)
    double = _response(pair, 48.0, BoreholePipe(kind="double_u_parallel", D_s=0.04)).dynamic_gfunction()
    assert double.section.C_f == pytest.approx(2.0 * parallel.section.C_f)
    assert double.section.r_eq == pytest.approx(2.0 * 0.016)
    coaxial = _response(BoreField.rectangle(1, 1, 6.0, 150.0), 24.0, BoreholePipe.coaxial()).dynamic_gfunction()
    assert coaxial.section.r_eq == pytest.approx(0.055)
    lumped = _response(pair, 48.0).dynamic_gfunction(transit=False, rhoc_g=3.0e6, rhoc_p=2.0e6)
    assert lumped.transit_time is None and lumped.section.rhoc_g == 3.0e6
