"""Air-side energy balance of every air coil, within one result row.

For each coil an air stream crosses,

    m_air * c_a * (T_out - T_in) = heat from the refrigerant + fan power

with one air mass flow ``m_air = rho * dV`` for the coil duty, the fan heat and
the reported energy flows. The check is relative 1e-6 of the coil duty: the
model closes it by construction, so a larger residual means two different air
densities (or specific heats) were mixed inside a row.

Density per family
------------------
Every air coil takes dry air at its inlet temperature
(``tmhp.air_properties.air_density``): ASHP (both coils), ASHPB (outdoor coil)
and the GSHP indoor coil. A coil still on standard air (``constants.rho_a``)
leaves a residual of ``rho(T_in) / rho_a - 1`` here (GSHP at 18-26 °C room
air: -1.0 to -3.7 %).

Rows are continuous operation (``Cd_cycling=None``, ``defrost=None``): a row
averaged over on/off cycling or frost averages the outdoor fan power with the
runtime fraction and the airflow with the capacity ratio, so its outdoor side
closes only up to that share of the fan power (indoor side:
``test_indoor_balance_survives_cycling``).
"""

from __future__ import annotations

import math
import warnings
from collections.abc import Callable
from dataclasses import dataclass

import pytest

from tmhp.air_properties import air_density, air_heat_capacity_rate
from tmhp.constants import c_a

from ._families import FAMILIES, Result, SteadyCase, is_reported_running, num

BALANCE_RTOL = 1e-6


@dataclass(frozen=True)
class AirCoil:
    """Result keys of one air coil and the density its model uses."""

    name: str
    dV_key: str
    T_in_key: str
    T_mid_key: str
    T_out_key: str
    Q_ref_key: str
    E_fan_key: str
    density: Callable[[float], float]
    # True when the refrigerant heats the air on this coil in heating mode.
    heated_in_heating: bool


OUTDOOR = AirCoil(
    "outdoor", "dV_ou_a [m3/s]", "T_ou_a_in [°C]", "T_ou_a_mid [°C]", "T_ou_a_out [°C]", "Q_ref_ou [W]",
    "E_ou_fan [W]", air_density, heated_in_heating=False,
)  # fmt: skip
INDOOR = AirCoil(
    "indoor", "dV_iu_a [m3/s]", "T_iu_a_in [°C]", "T_iu_a_mid [°C]", "T_iu_a_out [°C]", "Q_ref_iu [W]",
    "E_iu_fan [W]", air_density, heated_in_heating=True,
)  # fmt: skip

COILS: dict[str, tuple[AirCoil, ...]] = {
    "ASHPB": (OUTDOOR,),
    "ASHP-heat": (OUTDOOR, INDOOR),
    "ASHP-cool": (OUTDOOR, INDOOR),
    "GSHP-heat": (INDOOR,),
    "GSHP-cool": (INDOOR,),
}

# family -> (T_source, T_demand, part load of hp_capacity); see _families for the meaning per family.
POINTS: dict[str, tuple[tuple[float, float, float], ...]] = {
    "ASHPB": ((-20.0, 35.0, 0.5), (-7.0, 50.0, 0.5), (7.0, 45.0, 0.75), (25.0, 50.0, 0.5)),
    "ASHP-heat": ((-20.0, 20.0, 0.5), (-7.0, 20.0, 0.5), (7.0, 20.0, 0.75), (15.0, 22.0, 0.5)),
    "ASHP-cool": ((27.0, 25.0, 0.5), (27.0, 35.0, 0.75), (24.0, 43.0, 0.5)),
    "GSHP-heat": ((5.0, 18.0, 0.5), (5.0, 24.0, 0.75)),
    "GSHP-cool": ((26.0, 20.0, 0.5), (22.0, 30.0, 0.75)),
}
REFRIGERANT = "R32"


def _continuous_kwargs(family: str) -> dict:
    kwargs: dict = {"Cd_cycling": None}
    if family.startswith("ASHP-"):
        kwargs["defrost"] = None
    return kwargs


def air_balance_residuals(family: str, r: Result) -> list[tuple[str, float, float]]:
    """``(coil, coil-only residual, coil-plus-fan residual)``, each relative to the coil duty."""
    heating = FAMILIES[family].mode == "heating"
    out = []
    for coil in COILS[family]:
        dV, T_in = num(r, coil.dV_key), num(r, coil.T_in_key)
        T_mid, T_out = num(r, coil.T_mid_key), num(r, coil.T_out_key)
        Q_ref, E_fan = num(r, coil.Q_ref_key), num(r, coil.E_fan_key)
        to_air = Q_ref if coil.heated_in_heating == heating else -Q_ref
        C_air = c_a * coil.density(T_in) * dV
        coil_only = (C_air * (T_mid - T_in) - to_air) / Q_ref
        with_fan = (C_air * (T_out - T_in) - (to_air + E_fan)) / Q_ref
        out.append((coil.name, coil_only, with_fan))
    return out


def _cases():
    for family, points in POINTS.items():
        for T_source, T_demand, plr in points:
            yield pytest.param(
                family,
                T_source,
                T_demand,
                plr,
                id=f"{family}-src{T_source:g}-dem{T_demand:g}-plr{plr:g}",
                marks=pytest.mark.xdist_group(f"air-balance-{family.split('-')[0]}"),
            )


@pytest.mark.parametrize(("family", "T_source", "T_demand", "plr"), list(_cases()))
def test_air_side_balance_closes_within_a_row(family, T_source, T_demand, plr):
    fam = FAMILIES[family]
    model = fam.build(REFRIGERANT, **_continuous_kwargs(family))
    case = SteadyCase(T_source=T_source, T_demand=T_demand, load_W=plr * float(model.hp_capacity))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        r = fam.call(model, case)
    # With cycling off a row at the minimum speed is that running state, not a mean.
    assert is_reported_running(r), r.get("failure_reason")
    for name, coil_only, with_fan in air_balance_residuals(family, r):
        assert math.isfinite(coil_only) and math.isfinite(with_fan), (name, coil_only, with_fan)
        assert abs(coil_only) <= BALANCE_RTOL, f"{name} coil: air enthalpy vs refrigerant heat off by {coil_only:.3e}"
        assert abs(with_fan) <= BALANCE_RTOL, f"{name} coil + fan: air enthalpy off by {with_fan:.3e}"


@pytest.mark.parametrize(
    ("family", "T_source", "T_demand"), [("ASHP-heat", -7.0, 20.0), ("GSHP-heat", 5.0, 20.0), ("GSHP-cool", 26.0, 20.0)]
)
def test_reported_air_energy_flows_use_the_coil_mass_flow(family, T_source, T_demand):
    """``Q_a_iu_out - Q_a_iu_in`` is the indoor coil duty plus the indoor fan power (ASHP, GSHP).

    In cooling the refrigerant takes the duty from the air: the air leaves with
    the fan power minus the coil duty.
    """
    fam = FAMILIES[family]
    model = fam.build(REFRIGERANT, **_continuous_kwargs(family))
    case = SteadyCase(T_source=T_source, T_demand=T_demand, load_W=0.5 * float(model.hp_capacity))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        r = fam.call(model, case)
    assert is_reported_running(r), r.get("failure_reason")
    delivered = num(r, "Q_a_iu_out [W]") - num(r, "Q_a_iu_in [W]")
    sign = 1.0 if fam.mode == "heating" else -1.0
    expected = sign * num(r, "Q_ref_iu [W]") + num(r, "E_iu_fan [W]")
    assert delivered == pytest.approx(expected, rel=BALANCE_RTOL)
    C_air = air_heat_capacity_rate(num(r, "dV_iu_a [m3/s]"), num(r, "T_iu_a_in [°C]"))
    assert delivered == pytest.approx(C_air * (num(r, "T_iu_a_out [°C]") - num(r, "T_iu_a_in [°C]")), rel=BALANCE_RTOL)


def test_indoor_balance_survives_cycling():
    """A row averaged over on/off cycling keeps the indoor-side balance (default model)."""
    fam = FAMILIES["ASHP-cool"]
    model = fam.build(REFRIGERANT)
    case = SteadyCase(T_source=27.0, T_demand=30.0, load_W=0.05 * float(model.hp_capacity))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        r = fam.call(model, case)
    assert r.get("capacity_clamped") == "min"
    residuals = {name: with_fan for name, _, with_fan in air_balance_residuals("ASHP-cool", r)}
    assert abs(residuals["indoor"]) <= BALANCE_RTOL


def test_density_changes_the_coil_at_a_fixed_volume_flow():
    """Colder inlet air carries more heat capacity through the same fan volume flow.

    The same coil, duty and approach need less airflow in cold air than in
    warm air, in the ratio of the densities when the coil is far from
    saturation (NTU fixed by ``UA / C``): checked on the coil solver itself.
    """
    from tmhp.heat_exchanger import calc_HX_perf_for_target_heat

    def coil(T_in_C: float, rho_air):
        return calc_HX_perf_for_target_heat(
            Q_ref_target=3000.0,
            T_a_in_C=T_in_C,
            T_ref_sat_K=T_in_C + 273.15 - 8.0,
            A_cross=0.4,
            UA_rated=800.0,
            dV_fan_ref=0.8,
            exponent=0.65,
            rho_air=rho_air,
        )

    cold, warm = coil(-25.0, "inlet"), coil(35.0, "inlet")
    assert cold["C_air"] == pytest.approx(c_a * air_density(-25.0) * cold["dV_fan"], rel=1e-12)
    assert cold["dV_fan"] < warm["dV_fan"]
    # Standard air (the default) does not see the inlet temperature at all.
    assert coil(-25.0, None)["dV_fan"] == pytest.approx(coil(35.0, None)["dV_fan"], rel=1e-9)
    assert coil(15.0, "inlet")["dV_fan"] == pytest.approx(coil(15.0, None)["dV_fan"], rel=1e-4)
    with pytest.raises(ValueError):
        coil(15.0, "outlet")
    with pytest.raises(ValueError):
        coil(15.0, -1.0)
