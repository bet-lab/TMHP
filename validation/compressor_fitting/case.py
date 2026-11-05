"""Frozen Fujitsu 30-class heating catalogue study with explicit fixed assumptions."""

from pathlib import Path

import pandas as pd

from tmhp import AirSourceHeatPump

ROOT = Path(__file__).resolve().parent
FLOW_IN = 1380.0 / 3600.0
FLOW_OUT = 3600.0 / 3600.0
HEATING_REFERENCE_W = 8800.0
EQUIPMENT_SETTINGS = dict(
    ref="R410A",
    hp_capacity=8000.0,  # Constructor's default rating mode is cooling, not heating.
    dV_iu_fan_a_rated=FLOW_IN,
    dV_ou_fan_a_rated=FLOW_OUT,
    rps_min=30.0,
    rps_max=110.0,
    PR_cycle_max=7.5,
    dT_approach_bounds=(1.0, 35.0),
)
ASSUMPTIONS = [
    "Fujitsu ASYG30LMTA/AOYG30LMTA R410A: maximum-capacity calculated catalogue table, not measured nominal data",
    "hp_capacity=8000 W is published nominal cooling capacity; published nominal heating 8800 W defines display PLR only",
    "Indoor/outdoor DB are published; no wet-bulb conversion is used",
    "Manufacturer reference airflows are 1380/3600 m3/h; Vdisp, UA and fan curves retain TMHP capacity defaults",
    "Derived default Vdisp=37.333333 cm3/rev, UA indoor/outdoor=640/800 W/K; not manufacturer specifications",
    "Default fan dp=60 Pa and efficiency=0.6 give 38.333333/100 W reference inputs; outdoor 100 W is not the published motor-output value",
    "Published indoor fan input 60 W is retained as evidence, not substituted for the generic model fan curve",
    "Source-map speed 30–110 rev/s and PR 1.5–7.5 are research bounds, not certified equipment envelope",
    "Approach 1–35 K is a frozen numerical assumption; all other unspecified settings remain constructor defaults",
    "R290 scroll map transferred to R410A target; these lambdas are not independently identified device efficiencies",
    "Study uses outdoor DB 7/10/15/20 C and all indoor DB 16/18/20/22/24 C; indoor 20 C is calibration, the other 16 rows are holdout",
    "Outdoor 24 C group was excluded before COP fitting because one of its five baseline solves failed; all five remain solver-edge diagnostics",
    "All 25 warm candidates are preserved: corrected-rating baseline has 24 valid and one optimizer failure",
    "Outdoor DB 7 C does not certify frost-free operation; the model contains no frost/defrost physics",
    "Other 25 cold source rows remain diagnostic_cold; no failed rows are filtered from reported split metrics",
]


def make_model():
    """Construct the ASHP with built-in maps; fixed inputs are never fitted."""
    return AirSourceHeatPump(**EQUIPMENT_SETTINGS)


def catalogue(split):
    """Read all preserved source groups, with a partition frozen before COP fitting."""
    return pd.read_csv(ROOT / "catalogue_points.csv").query("split == @split").sort_values(["Tin_DB_C", "Tout_DB_C"])


def points(frame):
    """Return indoor DB, outdoor DB, net output [W], and system COP tuples."""
    return list(frame[["Tin_DB_C", "Tout_DB_C", "Q_W", "COP"]].itertuples(index=False, name=None))
