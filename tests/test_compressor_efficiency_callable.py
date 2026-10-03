"""Ground compressors must evaluate all three efficiencies at solved speed."""

import contextlib

import pytest

from tmhp import GroundSourceHeatPump, GroundSourceHeatPumpBoiler


@pytest.mark.parametrize("cls", [GroundSourceHeatPump, GroundSourceHeatPumpBoiler])
@pytest.mark.parametrize("kind", ["float", "pr", "pr_speed"])
def test_ground_efficiency_api_and_mass_work_balances(cls, kind):
    vals = (0.70, 1.0, 0.90)
    seen = [[], [], []]

    def model(i):
        if kind == "float":
            return vals[i]
        if kind == "pr":
            return lambda pr: vals[i]

        def eff(pr, rps):
            seen[i].append((pr, rps))
            return vals[i] - (0.03 if i == 1 else 0.01) * (1 - rps / 150)

        return eff

    unrated = pytest.warns(RuntimeWarning, match="m_dot_ref_rated not derived")
    with unrated if cls is GroundSourceHeatPumpBoiler else contextlib.nullcontext():
        hp = cls(
            ref="R410A",
            V_cmp_ref=1.2e-5,
            hp_capacity=8000,
            R_b=0.2,
            t_max_s=3600,
            eta_cmp_isen=model(0),
            eta_cmp_vol=model(1),
            eta_cmp=model(2),
            PR_cycle_max=8,
            rps_rated=40,  # 12 cm3/rev cannot rate 8 kW; the speed is fixed here
        )
    if cls is GroundSourceHeatPump:
        row = hp._calc_state(10, 10, 4000, 26, 26)
        duty = "Q_ref_iu [W]"
    else:
        row = hp._calc_state(8, 40, 4000, 26, flow_state={})
        duty = "Q_ref_tank [W]"
    assert row is not None and row["converged_rps"]
    assert row[duty] == pytest.approx(4000, abs=1e-6)
    pr = row["P_ref_cmp_out [Pa]"] / row["P_ref_cmp_in [Pa]"]
    speed = row["cmp_rpm [rpm]"] / 60
    expected = [
        v - (0.03 if i == 1 else 0.01) * (1 - speed / 150) if kind == "pr_speed" else v for i, v in enumerate(vals)
    ]
    assert [row[k] for k in ("eta_is [-]", "eta_v [-]", "eta_em [-]")] == pytest.approx(expected)
    mass = hp.V_cmp_ref * row["rho_ref_cmp_in [kg/m3]"] * expected[1] * speed
    assert row["m_dot_ref [kg/s]"] == pytest.approx(mass)
    gas = mass * (row["h_ref_cmp_out [J/kg]"] - row["h_ref_cmp_in [J/kg]"])
    assert row["E_cmp [W]"] == pytest.approx(gas / expected[2])
    if kind == "pr_speed":
        assert all((pr, speed) in calls for calls in seen)
        assert len({r for _, r in seen[0]}) > 2


@pytest.mark.parametrize("pr,speed", [(1.5, 15), (3, 60), (5, 100)])
def test_all_five_models_share_baseline_at_same_rated_speed(pr, speed):
    from tmhp import AirSourceHeatPump, AirSourceHeatPumpBoiler, WaterSourceHeatPumpBoiler
    from tmhp.compressor_efficiency import _eval_eff, make_eta_em, make_eta_isen, make_eta_vol

    expected = [f(60)(pr, speed) for f in (make_eta_isen, make_eta_vol, make_eta_em)]
    for cls in (
        AirSourceHeatPump,
        AirSourceHeatPumpBoiler,
        GroundSourceHeatPump,
        GroundSourceHeatPumpBoiler,
        WaterSourceHeatPumpBoiler,
    ):
        hp = cls(
            rps_rated=60,
            eta_cmp_isen=None,
            eta_cmp_vol=None,
            eta_cmp=None,
            **(
                {"t_max_s": 3600}
                if cls in (GroundSourceHeatPump, GroundSourceHeatPumpBoiler, WaterSourceHeatPumpBoiler)
                else {}
            ),
        )
        assert [
            _eval_eff(getattr(hp, k), pr, speed) for k in ("eta_cmp_isen", "eta_cmp_vol", "eta_cmp")
        ] == pytest.approx(expected)


def test_helper_does_not_mask_callable_body_type_error():
    from tmhp.compressor_efficiency import _eval_eff

    def broken(pr, rps):
        raise TypeError("failure inside user function")

    with pytest.raises(TypeError, match="inside user function"):
        _eval_eff(broken, 3, 40)


@pytest.mark.parametrize("eff", [0, -1, 1.01, float("nan"), float("inf")])
def test_evaluated_efficiency_validation(eff):
    from tmhp.compressor_efficiency import _eval_eff

    with pytest.raises(ValueError):
        _eval_eff(lambda pr, rps: eff, 3, 40)


def test_shipped_coefficients_are_unchanged():
    import json
    from pathlib import Path

    from tmhp.compressor_efficiency import coefficients

    frozen = json.loads((Path(__file__).parents[1] / "docs/audits/compressor-efficiency-baseline.json").read_text())
    assert coefficients() == frozen


def test_gshp_legacy_efficiency_aliases_and_new_precedence():
    with pytest.warns(DeprecationWarning):
        hp = GroundSourceHeatPump(eta_v=0.9, eta_em=0.8, eta_cmp_vol=1, eta_cmp=0.95, t_max_s=3600)
    assert hp.eta_cmp_vol == hp.eta_v == 1
    assert hp.eta_cmp == hp.eta_em == 0.95
