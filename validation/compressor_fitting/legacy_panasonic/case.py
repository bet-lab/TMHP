"""Frozen Panasonic catalogue assumptions for the public ASHP fitting example."""

from pathlib import Path

import pandas as pd

from tmhp import AirSourceHeatPump
from tmhp.compressor_baseline import ossorio_r290_efficiencies

ROOT = Path(__file__).resolve().parent
FLOW_IN = 12.40 / 60
FLOW_OUT = 35.6 / 60
EQUIPMENT_SETTINGS = dict(
    ref="R32",
    hp_capacity=3500.0,
    V_cmp_ref=10.30e-6,
    UA_iu_rated=306.3298,
    UA_ou_rated=766.8609,
    dV_iu_fan_a_rated=FLOW_IN,
    dV_ou_fan_a_rated=FLOW_OUT,
    dV_iu_fan_a_min=0.2 * FLOW_IN,
    dV_iu_fan_a_max=FLOW_IN,
    dV_ou_fan_a_min=0.2 * FLOW_OUT,
    dV_ou_fan_a_max=FLOW_OUT,
    dP_iu_fan_rated=44.9 * 0.6 / FLOW_IN,
    dP_ou_fan_rated=50 * 0.6 / FLOW_OUT,
    eta_iu_fan_rated=0.6,
    eta_ou_fan_rated=0.6,
    dT_superheat=5.0,
    dT_subcool=5.0,
    rps_min=30.0,
    rps_max=110.0,
    PR_cycle_min=1.5,
    PR_cycle_max=7.5,
    dT_approach_bounds=(1.0, 35.0),
)
CALIBRATION_BOUNDS = {
    "overall_efficiency": (0.65, 1.25),
    "volumetric_efficiency": (0.75, 1.1),
    "heat_loss_fraction": (0.0, 1.5),
}


def make_model():
    """Construct the uncalibrated ASHP; fixed assumptions are never fitted."""
    return AirSourceHeatPump(**EQUIPMENT_SETTINGS, **ossorio_r290_efficiencies())


def catalogue(split):
    """Read preserved source values; Tout DB = printed WB + 1 K is an assumption."""
    return pd.read_csv(ROOT / "catalogue_points.csv").query("split == @split").sort_values(["Tin_DB_C", "Tout_DB_C"])


def points(frame):
    """Expose the user's exact (Tin DB, Tout DB, net output, COP) tuple contract."""
    return list(frame[["Tin_DB_C", "Tout_DB_C", "Q_W", "COP"]].itertuples(index=False, name=None))
