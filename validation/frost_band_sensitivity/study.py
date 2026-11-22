"""Sensitivity of the frost correction to the frost-point band (#231).

``DefrostModel.frost_point_band_K`` (3 K) is the width of the band of frost
points above the coil temperature over which the fixed frosted-coil losses of
the EnergyPlus adjustment (12.5 % of the capacity, 4.6 % of the power) grow
from nothing to their full size. No measurement fixes it. This study says what
depends on it:

1. ``multipliers.csv`` -- the rated-load capacity, power and COP multipliers
   over outdoor dry bulb -30 ... 5 degC and relative humidity 30 ... 100 %,
   for a band of 0, 3 and 6 K and without one (``frost_point_band_K=None``:
   the fixed losses whatever the air can deposit, EnergyPlus' rule). The frost
   model alone, no cycle. Only the band changes between the four: the
   setting without a band keeps this model's transition between 2 and 5 degC
   and, in the season below, its load term, so it is not EnergyPlus.
2. ``seasonal.csv`` and ``seasonal_by_temperature.csv`` -- a heating season of
   the default 4 kW air-to-air unit against a 20 degC room in three cold
   climates of the United States (Denver, cool and dry; Helena, cold and dry;
   Fairbanks, subarctic), with the same four settings. The load is a straight
   line from nothing at 16 degC outdoor (the balance point EN 14825 uses) to
   the nameplate capacity at the site's 99.6 % heating design dry bulb; what
   the unit does not deliver is made up by resistance heat. Load, heat and
   backup are counted at the indoor coil (``Q_ref_iu``): the heat of the
   indoor fan is not credited. Each hour of the typical year is taken at its
   dry bulb and humidity, in bins of 1 K and 5 %; an hour whose dry bulb
   rounds to 16 degC carries no load and is left out (about 150 hours a
   site). ``electricity_vs_default_pct`` compares a band with 3 K at the same
   site and under the same reading of the humidity.

The weather
-----------
``weather_bins.csv`` is the count of hours per bin, from the NREL TMY3
EnergyPlus weather files of the three stations (the files themselves are not
in the repository; ``--epw`` rebuilds the table from them).

The frost model's relative humidity (``tmhp.defrost.humidity_ratio``,
CoolProp) is over ice below 0 degC. A weather file gives a dew point and a
relative humidity and does not say over which phase. The two columns do not
settle it: in most sub-zero hours they agree to a few points whether both
are read over water or both over ice, and in some hours under neither. The
readings are far apart in cold air -- at -20 degC a vapour pressure of 80 %
of saturation over water is 97 % of saturation over ice -- so the season is
computed for two input scenarios (column ``humidity``):

``dew_point``
    The dew point is one over liquid water at every temperature, the usual
    convention of meteorological observations. Its vapour pressure (Magnus
    form over water) is kept, and the relative humidity the model is given is
    that vapour pressure over the model's own saturation pressure at the dry
    bulb.
``relative_humidity``
    The relative-humidity column is given to the model as it is: read as over
    ice below 0 degC, which is also how EnergyPlus' psychrometric functions
    read the file.

Run from the repository root::

    uv run python -m validation.frost_band_sensitivity.study
    uv run python -m validation.frost_band_sensitivity.study --epw helena=PATH --epw denver=PATH --epw fairbanks=PATH
"""

from __future__ import annotations

import argparse
import math
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from tmhp import AirSourceHeatPump
from tmhp.defrost import P_ATM, DefrostModel

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "validation" / "results" / "frost_band_sensitivity"
WEATHER_BINS = Path(__file__).resolve().parent / "weather_bins.csv"

#: Settings compared: the label of each and its ``frost_point_band_K`` (``None``: no band, the fixed
#: losses whatever the air deposits; nothing else of the frost model changes).
BANDS: dict[str, float | None] = {"none": None, "0": 0.0, "3": 3.0, "6": 6.0}
#: The library default, which the others are compared with.
DEFAULT_BAND = "3"

#: Grid of the multiplier table: outdoor dry bulb [degC] and relative humidity [-].
T_OUT_GRID = tuple(float(t) for t in range(-30, 6))
RH_GRID = tuple(round(0.30 + 0.05 * i, 2) for i in range(15))

#: Room temperature of the seasonal case [degC] (EN 14511 heating rating condition).
T_ROOM = 20.0
#: Outdoor temperature at which the building needs no heat [degC] (EN 14825, 16 degC).
T_BALANCE = 16.0
#: Sites: name, climate zone of ASHRAE 169, and the 99.6 % heating design dry bulb [degC]
#: (ASHRAE Handbook 2009 climatic design data, as printed in the station's ``.stat`` file).
SITES: dict[str, dict] = {
    "denver": {"station": "Denver Intl AP, CO (WMO 725650)", "zone": "5B", "T_design": -17.4},
    "helena": {"station": "Helena Regional AP, MT (WMO 727720)", "zone": "6B", "T_design": -26.3},
    "fairbanks": {"station": "Fairbanks Intl AP, AK (WMO 702610)", "zone": "8", "T_design": -41.9},
}
#: Outdoor-temperature classes of the breakdown [degC]: lower edge (inclusive), upper edge.
T_CLASSES = ((-60.0, -20.0), (-20.0, -10.0), (-10.0, -5.0), (-5.0, 2.0), (2.0, 5.0), (5.0, T_BALANCE))


def _model(band: float | None) -> DefrostModel:
    return DefrostModel(frost_point_band_K=band)


def multipliers() -> pd.DataFrame:
    """Rated-load multipliers of the frost adjustment over the grid, for each band."""
    rows = []
    for label, band in BANDS.items():
        model = _model(band)
        for T_out in T_OUT_GRID:
            for RH in RH_GRID:
                frost = model.frost_state(T_out, RH)
                rows.append(
                    {
                        "band": label,
                        "T_out_C": T_out,
                        "RH": RH,
                        "frost_onset": frost.frost_onset if frost.active else 0.0,
                        "defrost_fraction": frost.defrost_fraction,
                        "capacity_multiplier": frost.capacity_multiplier,
                        "power_multiplier": frost.power_multiplier,
                        "cop_multiplier": frost.capacity_multiplier / (frost.power_multiplier + frost.defrost_fraction),
                    }
                )
    return pd.DataFrame(rows)


def _vapour_pressure_over_water(T_C: np.ndarray) -> np.ndarray:
    """Saturation vapour pressure over liquid water [Pa]: the Magnus form of WMO-No. 8 (2018), Annex 4.B."""
    return 611.2 * np.exp(17.62 * T_C / (243.12 + T_C))


def _model_saturation_pressure(T_C: float) -> float:
    """Partial pressure of water vapour in saturated air at ``T_C`` as the frost model has it [Pa] (ice below 0 degC)."""
    from CoolProp.HumidAirProp import HAPropsSI

    return float(HAPropsSI("P_w", "T", T_C + 273.15, "P", P_ATM, "R", 1.0))


#: Readings of a weather file's humidity (module docstring).
HUMIDITY_READINGS = ("dew_point", "relative_humidity")


def weather_bins_from_epw(site: str, path: Path) -> pd.DataFrame:
    """Hours of a typical year below the balance point, by dry bulb (1 K) and model relative humidity (5 %).

    One table per reading of the file's humidity (:data:`HUMIDITY_READINGS`).
    """
    hours = pd.read_csv(path, skiprows=8, header=None, usecols=[6, 7, 8], names=["T_db", "T_dp", "RH_pct"])
    # Missing values of the format: 99.9 (dry bulb, dew point) and 999 (relative humidity).
    missing = (hours["T_db"] >= 99.0) | (hours["T_dp"] >= 99.0) | (hours["RH_pct"] > 110.0)
    if missing.any():
        raise ValueError(
            f"{path.name}: {int(missing.sum())} hour(s) without a dry bulb, dew point or relative humidity"
        )
    hours["T_out_C"] = hours["T_db"].round()
    hours = hours[hours["T_out_C"] < T_BALANCE].copy()
    saturation = {T: _model_saturation_pressure(T) for T in sorted(hours["T_db"].unique())}
    vapour = _vapour_pressure_over_water(np.minimum(hours["T_dp"], hours["T_db"]).to_numpy())
    readings = {
        "dew_point": vapour / hours["T_db"].map(saturation).to_numpy(),
        "relative_humidity": hours["RH_pct"].to_numpy() / 100.0,
    }
    tables = []
    for reading in HUMIDITY_READINGS:
        hours["RH"] = np.clip(np.round(readings[reading] / 0.05) * 0.05, 0.05, 1.0).round(2)
        bins = hours.groupby(["T_out_C", "RH"]).size().rename("hours").reset_index()
        bins.insert(0, "humidity", reading)
        bins.insert(0, "site", site)
        tables.append(bins)
    return pd.concat(tables, ignore_index=True)


def _class_of(T_out: float) -> str:
    for low, high in T_CLASSES:
        if low <= T_out < high:
            return f"{low:g} to {high:g}" if low > -60.0 else f"below {high:g}"
    raise ValueError(T_out)


def bin_results(weather: pd.DataFrame, site: str, band: str) -> pd.DataFrame:
    """The default unit at each weather bin of ``weather`` (rows of one site), with the band ``band`` of :data:`BANDS`.

    One row per bin: its hours, the heat the load line asks for, what the
    heat pump delivers and draws, and what resistance heat makes up [kWh].
    """
    design = SITES[site]["T_design"]
    rows = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        unit = AirSourceHeatPump(defrost=_model(BANDS[band]))
        for T_out, RH, hours in weather[["T_out_C", "RH", "hours"]].itertuples(index=False):
            load = unit.hp_capacity * (T_BALANCE - T_out) / (T_BALANCE - design)
            row = unit.analyze_steady(
                Q_r_iu=-load, T0=float(T_out), T_a_room=T_ROOM, RH_out=float(RH), return_dict=True
            )
            running = bool(row.get("hp_is_on", False)) and row.get("failure_reason", "none") == "none"
            heat = abs(float(row["Q_ref_iu [W]"])) if running else 0.0
            rows.append(
                {
                    "site": site,
                    "band": band,
                    "class": _class_of(float(T_out)),
                    "hours": hours,
                    "load_kWh": load * hours / 1000.0,
                    "hp_heat_kWh": heat * hours / 1000.0,
                    "hp_electricity_kWh": (float(row["E_tot [W]"]) if running else 0.0) * hours / 1000.0,
                    "backup_kWh": max(load - heat, 0.0) * hours / 1000.0,
                    "frosting_hours": hours if running and float(row["frost_capacity_factor [-]"]) < 1.0 else 0,
                }
            )
    return pd.DataFrame(rows)


def totals(detail: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """Sums of :func:`bin_results` over ``keys``, with the seasonal performance factors."""
    out = detail.groupby(keys, sort=False)[
        ["hours", "load_kWh", "hp_heat_kWh", "hp_electricity_kWh", "backup_kWh", "frosting_hours"]
    ].sum()
    out["scop_hp"] = out["hp_heat_kWh"] / out["hp_electricity_kWh"]
    out["electricity_kWh"] = out["hp_electricity_kWh"] + out["backup_kWh"]
    out["scop_system"] = out["load_kWh"] / out["electricity_kWh"]
    return out.reset_index()


def seasonal(bins: pd.DataFrame, site: str, humidity: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Heating season of the default unit at ``site`` for each band: totals, and by outdoor-temperature class."""
    weather = bins[(bins["site"] == site) & (bins["humidity"] == humidity)]
    detail = pd.concat([bin_results(weather, site, band) for band in BANDS], ignore_index=True)
    detail.insert(1, "humidity", humidity)
    season = totals(detail, ["site", "humidity", "band"])
    reference = float(season.loc[season["band"] == DEFAULT_BAND, "electricity_kWh"].iloc[0])
    season["electricity_vs_default_pct"] = 100.0 * (season["electricity_kWh"] / reference - 1.0)
    return season, totals(detail, ["site", "humidity", "band", "class"])


def run(bins: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Every table of the study from the weather bins."""
    seasons, classes = zip(
        *(seasonal(bins, site, humidity) for site in SITES for humidity in HUMIDITY_READINGS), strict=True
    )
    return {
        "multipliers.csv": multipliers(),
        "seasonal.csv": pd.concat(seasons, ignore_index=True),
        "seasonal_by_temperature.csv": pd.concat(classes, ignore_index=True),
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--epw",
        action="append",
        default=[],
        metavar="SITE=PATH",
        help="rebuild weather_bins.csv from this TMY3 EnergyPlus weather file (all sites must be given)",
    )
    args = parser.parse_args(argv)
    if args.epw:
        given = dict(item.split("=", 1) for item in args.epw)
        if set(given) != set(SITES):
            parser.error(f"--epw needs every site of {sorted(SITES)}, got {sorted(given)}")
        bins = pd.concat([weather_bins_from_epw(site, Path(given[site])) for site in SITES], ignore_index=True)
        bins.to_csv(WEATHER_BINS, index=False)
    bins = pd.read_csv(WEATHER_BINS)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tables = run(bins)
    for name, table in tables.items():
        table.to_csv(OUT_DIR / name, index=False)

    from validation import study_provenance

    study_provenance.write(
        OUT_DIR / "provenance.json",
        "validation/frost_band_sensitivity/study.py",
        [OUT_DIR / name for name in tables],
        {
            "model": "AirSourceHeatPump(defrost=DefrostModel(frost_point_band_K=band)), every other argument the default",
            "bands": {label: band for label, band in BANDS.items()},
            "T_room_C": T_ROOM,
            "T_balance_C": T_BALANCE,
            "load_line": "hp_capacity * (T_balance - T_out) / (T_balance - T_design)",
            "sites": SITES,
            "weather": "validation/frost_band_sensitivity/weather_bins.csv (NREL TMY3, hours per 1 K and 5 % bin)",
            "humidity_readings": list(HUMIDITY_READINGS),
        },
        "how much the rated-load frost multipliers and a heating season in a cold climate depend on the frost-point band",
        command="python -m validation.frost_band_sensitivity.study",
    )
    season = tables["seasonal.csv"]
    print(season.to_string(index=False, float_format=lambda v: f"{v:.3f}" if not math.isnan(v) else "nan"))


if __name__ == "__main__":
    main()
