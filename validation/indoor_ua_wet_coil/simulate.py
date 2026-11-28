"""The indoor coil's conductance read from the catalogues' sensible heat ratio, on the present compressor map.

The air-to-air model's indoor coil defaults to ``UA_iu_rated = 0.16 W/K per W
of nameplate`` (``INDOOR_UA_PER_CAPACITY``, a rule kept provisionally). With
the wet indoor coil (``RH_room``, #205) the cooling rows of the adopted
catalogues have a COP bias of -3.1 % where the dry coil gives -12.8 %, and it
runs with the size of the unit, from -13.4 % (2.0 kW) to +12.6 % (5.0 kW)
(``validation/wet_coil_shr``). This study asks what the catalogue data say
that conductance is, what the model does with it in cooling and in heating,
where the bias that is left lies, and what a rule for it would move if it
were the default (#220). It is a diagnosis: it changes no default and adopts
nothing.

A first run of this study was made on the compressor model the library had
before #223 (semi-empirical efficiency forms by compressor type); its COP
figures do not describe the present model, whose efficiencies are one
measured map in pressure ratio, speed and evaporating temperature
(:mod:`tmhp.compressor_efficiency`). Step 1 involves no compressor and is
unchanged.

1. The conductance a row publishes
----------------------------------
The model's wet coil has one surface temperature, the evaporating temperature
``T_e``, and the air leaves a fraction ``eps = 1 - exp(-UA / C)`` of the way
along the straight line from the room state to saturated air at ``T_e``
(:func:`tmhp.heat_exchanger.calc_HX_perf_for_target_heat`). Its sensible heat
ratio therefore depends on the room state and on ``T_e`` alone -- not on
``UA``, not on the airflow -- and its total heat is ``eps C`` times the
driving difference at ``T_e``. A catalogue row that prints the total
capacity, the sensible heat ratio and the room's dry and wet bulb gives both,
with no compressor involved:

- the coil temperature at which the coil line has the published ratio (the
  apparatus dew point, ``T_adp_C``), which is the evaporating temperature the
  model must run at to print that ratio;
- the heat per kelvin of driving difference there, ``eps C``
  (``conductance_W_K``): sensible heat over ``T_room - T_adp``;
- with the published indoor airflow, the contact factor ``eps``
  (``contact_factor``; one minus the bypass factor) and
  ``UA = -C ln(1 - eps)`` (``UA_W_K``).

The published capacities are net of the indoor fan motor's heat, so the ratio
is put on the coil's boundary first (the fan heat added to the sensible and
to the total capacity). The catalogues print no fan power; the model's rated
indoor fan power at the published airflow is used (``E_iu_fan_rated``, 17 to
23 W on these units), and ``UA_no_fan_W_K`` is the same solve with the
published ratio taken as the coil's own. The published ratio has two
decimals: ``UA_lo_W_K`` and ``UA_hi_W_K`` are the solve at the ratio less and
plus 0.005 (:data:`SHR_ROUNDING`).

A row whose published ratio is 1 has a dry coil and no such temperature, and
one whose ratio is below the lowest a straight coil line can give at that
room state has none either (``solve`` says which); neither enters a unit's
value. A unit's value is the median over its solved rows
(``ua_by_unit.csv``), with the spread, the share of rows whose rounding band
holds that median, and the slope of ``ln UA`` on the outdoor and the indoor
dry bulb: a coil's conductance does not depend on the outdoor temperature, so
a slope there would mean the single-temperature coil line is not what the
table was made with.

2. Cooling with that conductance
--------------------------------
Every cooling row of the adopted units is run in these passes
(``cooling_passes.csv``), built and scored as the parity harness does
(``validation.parity.run._build_model``; the request is the published
capacity; a row whose delivered duty is more than ``CAPACITY_SHORT_TOL``
short of it, or met only as an on/off mean, is not scored):

``dry``                  no ``RH_room``: the shipped path. Its COP must equal
                         the committed ``validation/results/<slug>.csv`` row
                         for row; that gate runs before anything is written.
``wet``                  ``RH_room`` of the row, the shipped conductance: the
                         wet pass of ``validation/wet_coil_shr``.
``wet_ua_shr``           the unit's conductance of step 1, **the compressor
                         left as shipped**: the rated speed is the shipped
                         build's (``rps_rated`` passed), so only the coil
                         differs.
``wet_ua_shr_rerated``   the same conductance with ``UA_iu_rated`` alone
                         passed, as a user would. The model then also
                         re-solves its reference state -- the nameplate duty
                         on a *dry* coil of that conductance -- and its rated
                         speed with it. How far its rows are from those of
                         ``wet_ua_shr`` is in the headline
                         (``cop_``, ``T_evap_K_`` and
                         ``pr_max_abs_difference_rerated``).
``wet_ua_shr_x70`` / ``_x140`` / ``_x200``
                         ``wet_ua_shr`` with the conductance scaled: how far
                         a smaller or a larger coil moves the COP bias of a
                         unit and its sensible heat ratio, and where the bias
                         of a unit crosses zero if it does
                         (``UA_scale_for_zero_cop_bias``).
``wet_rule_<rule>``      the conductance a candidate rule gives the unit when
                         fitted **on the other four units** (step 5).

2b. A scan of the conductance
-----------------------------
How close to the catalogue can a different indoor conductance bring the
rows? The cooling rows with the dry coil, the cooling rows with the wet coil
and the heating rows are run at ten conductances from half to a hundred
times the shipped one (:data:`SCAN_SCALES`; ``conductance_scan.csv``), the
compressor as shipped. ``conductance_scan_summary.csv`` has the bias at each
scale and the bias with every row at the conductance of the scan that gives
it its highest COP (``which == "best"``). The second is the figure to read
for rows that run low: a row's COP is not always highest at the largest
conductance (the summary counts the rows where it is not, and by how much),
so the largest scale alone is not a bound. It is a bound over the ten
conductances run, not over every conductance.

3. Where the bias that is left lies
-----------------------------------
For ``dry``, ``wet`` and ``wet_ua_shr`` each row keeps the compressor's state
on the three axes of the efficiency map -- pressure ratio, speed,
evaporating temperature -- and whether it lies inside the map's data
(:func:`map_position`: the speeds 30 to 110 rev/s and the convex hull of the
measured points in pressure ratio and evaporating temperature). The bias is
split along each axis and by unit: ``bias_by_pressure_ratio.csv`` (the bands
of the first run, and outside / inside the map's data),
``bias_by_speed.csv`` (over the rated speed, over the unit's rating-row
speed, and in rev/s), ``bias_by_evaporating_temperature.csv``,
``bias_by_indoor_condition.csv`` (each indoor dry bulb with its wet bulb, by
unit) and ``bias_by_unit.csv``. ``eta_overall`` is the model's isentropic
times electromechanical efficiency (the map's ``eta_ov``);
``compressor_input_over_needed`` is the model's compressor input over the
one the published COP leaves for it at the model's delivery and fan powers
(above 1: the model's compressor draws too much).

4. Heating
----------
The indoor coil is the condenser in heating, and the heating grids print no
ratio, so the conductance cannot be solved there. The heating rows are run
with the shipped conductance (``heat``; gated on the committed results like
``dry``), with the conductance of step 1 (``heat_ua_shr``), with the shipped
one halved and doubled (``heat_ua_x50``, ``heat_ua_x200``) and with each
rule's (``heat_rule_<rule>``), the compressor as shipped
(``heating_passes.csv``, ``heating_by_unit.csv``).

5. Candidate rules
------------------
Five one- or two-parameter rules for the conductance of a wall-mounted unit
are fitted to the per-unit values of step 1 (least squares on the logarithm):

``nameplate``       ``UA = k * nameplate`` (the shipped form, ``k`` fitted)
``airflow``         ``UA = k * airflow`` (a contact factor common to the units)
``airflow_power``   ``UA = k * airflow ** n``
``constant``        ``UA = k``
``conductance``     ``eps C = k``: the heat per kelvin of driving difference
                    is the same for every unit, so ``UA = -C ln(1 - k / C)``
                    with ``C`` of the published airflow at 27 degC

Each is fitted on four units and tested on the fifth
(``rules_leave_one_out.csv``, with the shipped rule and the unit's own value
beside them): the conductance it predicts, the sensible heat ratio and COP
bias of the held-out unit's cooling rows run with it, and the COP bias of its
heating rows. The two held Fujitsu units (a different efficiency class; AHRI
210/240, boundaries of power and capacity not stated) are an out-of-family
check of the sensible heat ratio only: rules fitted on the five adopted
units, in files of their own (``ua_from_shr_held.csv``,
``ua_by_unit_held.csv``, ``cooling_passes_held.csv``, ``rules_held.csv``).
Nothing of theirs enters a number of the adopted units.

6. What a rule would move as the default
----------------------------------------
Each rule, fitted on the five adopted units, is put in place of the default:
``UA_iu_rated`` alone is overridden, so the reference state is re-solved as
it would be with a new default. The five units are run on the two paths of
the shipped parity results -- cooling with the dry coil, heating -- and on
the wet cooling path (``default_passes.csv``, one row per rule, path and
catalogue row). ``default_summary.csv`` has the COP bias and error of each
rule by path and unit beside those of the shipped conductance; the path
``parity`` is the dry cooling rows and the heating rows together, which is
what ``validation/results/<slug>.csv`` holds for these units. Nothing is
written to those files.

Run (about six minutes)::

    PYTHONPATH=. uv run --extra integrations python -m validation.indoor_ua_wet_coil.simulate

``--out-dir`` writes the study somewhere else. Part of the study is run
through :func:`run_cooling`, :func:`run_heating`, :func:`run_scan` and
:func:`run_defaults` (``point_ids``), as the tests do; the command line runs all of it.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import math
import re
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

#: The bytes this code was compiled from, when it runs through :func:`main`
#: (which reads the file once, compiles those bytes and runs the result with
#: this name set); ``None`` in a module the import system loaded, whose source
#: bytes are not known. The provenance record takes the script's blob from it.
_SOURCE: bytes | None = globals().get("_SOURCE")

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "validation" / "results" / "indoor_ua_wet_coil_once"
UA_POINTS_NAME = "ua_from_shr.csv"
UA_UNITS_NAME = "ua_by_unit.csv"
COOLING_NAME = "cooling_passes.csv"
BY_UNIT_NAME = "bias_by_unit.csv"
BY_PR_NAME = "bias_by_pressure_ratio.csv"
BY_SPEED_NAME = "bias_by_speed.csv"
BY_TEVAP_NAME = "bias_by_evaporating_temperature.csv"
BY_INDOOR_NAME = "bias_by_indoor_condition.csv"
SCAN_NAME = "conductance_scan.csv"
SCAN_SUMMARY_NAME = "conductance_scan_summary.csv"
DEFAULT_NAME = "default_passes.csv"
DEFAULT_SUMMARY_NAME = "default_summary.csv"
HEATING_NAME = "heating_passes.csv"
HEATING_UNITS_NAME = "heating_by_unit.csv"
RULES_NAME = "rules_leave_one_out.csv"
#: The files of the held units, reported apart.
HELD_UA_POINTS_NAME = "ua_from_shr_held.csv"
HELD_UA_UNITS_NAME = "ua_by_unit_held.csv"
HELD_COOLING_NAME = "cooling_passes_held.csv"
HELD_RULES_NAME = "rules_held.csv"
MANIFEST_NAME = "run_manifest.json"
#: This script, relative to the repository root (the provenance record names it and its content).
SCRIPT = "validation/indoor_ua_wet_coil/simulate.py"
#: How the committed study is made.
COMMAND = "python -m validation.indoor_ua_wet_coil.simulate"

#: The adopted catalogues with cooling rows: the study.
UNITS = ("daikin_rxm20a", "daikin_rxm25a", "daikin_rxm35a", "daikin_rxm42a", "daikin_rxm50a")
#: Held catalogues with cooling rows (``status: hold``): run the same way, reported apart.
HELD_UNITS = ("fujitsu_asuh09lpas", "fujitsu_asuh12lpas")
#: Half of the last printed digit of a published sensible heat ratio (two decimals).
SHR_ROUNDING = 0.005
#: Indoor dry bulb of the cooling rating point (ISO 5151 T1), where a unit's heat capacity rate is taken [degC].
T_RATING_INDOOR_C = 27.0
#: Scales of the unit's conductance of step 1 in the ``wet_ua_shr_x..`` passes.
UA_SCALES: dict[str, float] = {"wet_ua_shr_x70": 0.70, "wet_ua_shr_x140": 1.40, "wet_ua_shr_x200": 2.00}
#: Scales of the shipped conductance in the heating passes.
HEATING_SCALES: dict[str, float] = {"heat_ua_x50": 0.5, "heat_ua_x200": 2.0}
#: Scales of the shipped conductance in the scan of step 2b: from half of it to a hundred times it, at
#: which the coil's contact factor ``1 - exp(-UA / C)`` is 1 to every printed digit (``UA / C`` above 100
#: on these units).
SCAN_SCALES = (0.5, 0.7, 1.0, 1.4, 2.0, 3.0, 5.0, 10.0, 30.0, 100.0)
#: Candidate rules for the indoor conductance (module docstring, step 5).
RULES = ("nameplate", "airflow", "airflow_power", "constant", "conductance")
#: Passes whose rows carry the compressor's state, not the COP and the coil alone.
DETAILED = ("dry", "wet", "wet_ua_shr")
#: A pass that could not be built or run: the rule gives the unit no conductance.
NO_CONDUCTANCE = "no_conductance"
#: Edges of the bands of compressor speed over the rated speed (``n_star [-]``).
RELATIVE_SPEED_EDGES = (0.90, 0.95, 1.00, 1.05)
#: Edges of the bands of compressor speed over the speed of the unit's rating row in the same pass.
RATING_ROW_SPEED_EDGES = (0.95, 1.00, 1.05)
#: Edges of the bands of compressor speed [rev/s], the speed axis of the efficiency map.
SPEED_EDGES = (40.0, 44.0, 48.0)
#: Outdoor dry bulb of the cooling rating point (ISO 5151 T1) [degC]; the indoor one is :data:`T_RATING_INDOOR_C`.
T_RATING_OUTDOOR_C = 35.0
#: Pressure ratios where bands are cut above the cycle's floor. They are the bands of the first run of
#: this study (#220, on the compressor model before #223), kept so that the two can be read side by
#: side; 1.94 was the lowest pressure ratio of the efficiency data of that model and is no boundary of
#: the present map, whose data are told apart row by row (``outside_map_pr_``).
PR_EDGES = (1.7, 1.94, 2.3)
#: Edges of the bands of evaporating temperature [degC].
T_EVAP_EDGES = (8.0, 10.0, 12.0, 14.0, 16.0)


# ---------------------------------------------------------------------------
# 1. The conductance a row publishes
# ---------------------------------------------------------------------------

# The reading of a row and the coil line are written out here although the
# wet-coil study (``validation/wet_coil_shr/simulate.py``) has the same
# functions: that file is outside what a run of this study records (the model,
# the harness and the catalogues through ``source_state``, and this script's
# own bytes), so nothing this study computes with is taken from it. A test
# holds the two sets of functions to the same values.

P_ATM = 101325.0

_NOTE = re.compile(r"WB\s+(-?\d+(?:\.\d+)?)\s*C,\s*SHR\s+(\d+(?:\.\d+)?)")


def parse_note(note: str) -> tuple[float, float] | None:
    """``(indoor wet bulb [degC], sensible heat ratio)`` of a cooling row's note, or ``None``."""
    match = _NOTE.search(note)
    return (float(match.group(1)), float(match.group(2))) if match else None


def room_humidity(t_db_C: float, t_wb_C: float) -> float:
    """Relative humidity (0-1) of air at dry bulb ``t_db_C`` and wet bulb ``t_wb_C``, one standard atmosphere."""
    from CoolProp.HumidAirProp import HAPropsSI

    return float(HAPropsSI("R", "T", t_db_C + 273.15, "B", t_wb_C + 273.15, "P", P_ATM))


def coil_line_shr(t_db_C: float, w_in: float, t_coil_C: float) -> float:
    """Sensible heat ratio of the model's coil line from room air to saturated air at ``t_coil_C``."""
    from tmhp.heat_exchanger import wet_coil_latent_difference

    sensible = t_db_C - t_coil_C
    latent = wet_coil_latent_difference(w_in, t_coil_C + 273.15, P_ATM)
    return sensible / (sensible + latent)


def apparatus_dew_point(t_db_C: float, rh: float, shr: float) -> float:
    """Coil temperature [degC] at which :func:`coil_line_shr` equals ``shr``, or NaN when there is none.

    The ratio is 1 down to the dew point and falls from there as the coil
    gets colder, to a minimum (the latent part is bounded by the water the
    air holds, the sensible part is not) below which it rises again. The
    temperature returned is the one between the dew point and that minimum; a
    ratio of 1 or more, or one below the minimum, has none.
    """
    from CoolProp.HumidAirProp import HAPropsSI
    from scipy.optimize import brentq

    from tmhp.defrost import humidity_ratio

    if not shr < 1.0:
        return math.nan
    w_in = humidity_ratio(t_db_C, rh)
    dew_point = float(HAPropsSI("D", "T", t_db_C + 273.15, "R", rh, "P", P_ATM)) - 273.15
    warm, ratio_warm = dew_point, 1.0
    while warm > dew_point - 60.0:
        cold = warm - 0.25
        ratio_cold = coil_line_shr(t_db_C, w_in, cold)
        if ratio_cold <= shr:
            return float(brentq(lambda t: coil_line_shr(t_db_C, w_in, t) - shr, cold, warm, xtol=1e-9))
        if ratio_cold > ratio_warm:
            break  # past the minimum: no coil temperature has a ratio this low
        warm, ratio_warm = cold, ratio_cold
    return math.nan


def catalogue_ratio_on_the_coil(shr: float, q_W: float, fan_W: float) -> float:
    """A published (net) sensible heat ratio on the coil's boundary: the fan heat added to both capacities."""
    return (shr * q_W + fan_W) / (q_W + fan_W)


def coil_from_ratio(t_db_C: float, rh: float, q_coil_W: float, shr_coil: float, airflow_m3_s: float) -> dict:
    """The model's wet coil that gives total heat ``q_coil_W`` at sensible heat ratio ``shr_coil``.

    Room air at ``t_db_C`` [degC] and relative humidity ``rh`` (0-1), an
    airflow of ``airflow_m3_s`` at that state. Both capacities are on the
    coil's boundary (before the fan). Returns ``T_adp_C`` (the coil
    temperature whose coil line has that ratio), ``conductance_W_K`` (heat
    per kelvin of driving difference, ``eps C``), ``C_air_W_K``,
    ``contact_factor`` (``eps``), ``UA_W_K`` and ``solve``: ``"ok"``;
    ``"ratio_one"`` (a dry coil: any temperature above the dew point);
    ``"below_coil_line"`` (no straight coil line has a ratio this low at this
    room state); ``"contact_factor_one"`` (the airflow cannot carry that
    sensible heat to that coil temperature at any conductance).
    """
    from tmhp.air_properties import air_heat_capacity_rate

    nan = math.nan
    capacity_rate = float(air_heat_capacity_rate(airflow_m3_s, t_db_C))
    out = {"T_adp_C": nan, "conductance_W_K": nan, "C_air_W_K": capacity_rate, "contact_factor": nan, "UA_W_K": nan}
    coil = apparatus_dew_point(t_db_C, rh, shr_coil)
    if math.isnan(coil):
        return {**out, "solve": "ratio_one" if shr_coil >= 1.0 else "below_coil_line"}
    conductance = shr_coil * q_coil_W / (t_db_C - coil)
    contact = conductance / capacity_rate
    out.update({"T_adp_C": coil, "conductance_W_K": conductance, "contact_factor": contact})
    if not contact < 1.0:
        return {**out, "solve": "contact_factor_one"}
    return {**out, "UA_W_K": -capacity_rate * math.log1p(-contact), "solve": "ok"}


def coil_closure(t_db_C: float, rh: float, q_coil_W: float, airflow_m3_s: float, coil: dict) -> float:
    """Sensible heat ratio the model's own coil gives with a solved conductance, at the solved coil temperature.

    :func:`tmhp.heat_exchanger.calc_HX_perf_for_target_heat` asked for
    ``q_coil_W`` with ``UA_W_K`` at the fixed airflow and the refrigerant at
    ``T_adp_C``: what :func:`coil_from_ratio` inverted, the model's limit on a
    leaving state beyond saturation included. NaN for a row that was not
    solved.
    """
    from tmhp.defrost import humidity_ratio
    from tmhp.heat_exchanger import calc_HX_perf_for_target_heat

    if coil["solve"] != "ok":
        return math.nan
    result = calc_HX_perf_for_target_heat(
        Q_ref_target=q_coil_W,
        T_a_in_C=t_db_C,
        T_ref_sat_K=coil["T_adp_C"] + 273.15,
        # The face area the model gives its indoor coil (2 m/s at the rated airflow); it does not enter the heat.
        A_cross=airflow_m3_s / 2.0,
        UA_rated=coil["UA_W_K"],
        dV_fan_ref=airflow_m3_s,
        dV_fan_min=airflow_m3_s,
        dV_fan_max=airflow_m3_s,
        rho_air="inlet",
        w_a_in=humidity_ratio(t_db_C, rh),
    )
    total = float(result["Q_sensible"]) + float(result["Q_latent"])
    # The fixed airflow has to carry the published heat with that conductance: the other half of the inversion.
    if not (result["converged"] and abs(total - q_coil_W) <= 1e-6 * q_coil_W):
        return math.nan
    return float(result["Q_sensible"]) / total


def published_airflow(catalog, mode: str = "cooling") -> float:
    """Indoor airflow the catalogue publishes [m3/s], the one the harness builds the model with."""
    published = catalog.published_inputs
    if mode == "heating" and published.get("rated_indoor_air_flow_heating_m3_s"):
        return float(published["rated_indoor_air_flow_heating_m3_s"])
    return float(published["rated_indoor_air_flow_m3_s"])


def _coil_of_published(t_db_C: float, rh: float, q_W: float, airflow_m3_s: float, shr: float, fan_W: float) -> dict:
    """:func:`coil_from_ratio` of a published (net) capacity and ratio, put on the coil's boundary with ``fan_W``."""
    return coil_from_ratio(t_db_C, rh, q_W + fan_W, catalogue_ratio_on_the_coil(shr, q_W, fan_W), airflow_m3_s)


def catalogue_coils(slug: str) -> pd.DataFrame:
    """Step 1 for every cooling row of one catalogue: one row per catalogue row. No operating point is solved."""
    from validation.parity.run import _build_model
    from validation.parity.spec import load_all

    catalog = load_all(slug)[0]
    airflow = published_airflow(catalog)
    # The model's indoor fan power at the published airflow: the catalogues print none.
    fan_W = float(_build_model(catalog, None, "cooling").E_iu_fan_rated)
    rows = []
    for point in catalog.points:
        if point.mode != "cooling":
            continue
        parsed = parse_note(point.note)
        if parsed is None:
            raise SystemExit(f"{slug} point {point.id}: no indoor wet bulb and sensible heat ratio in {point.note!r}")
        wet_bulb, shr = parsed
        rh = room_humidity(point.t_sink_C, wet_bulb)
        q_W = point.q_kW * 1000.0
        solved = functools.partial(_coil_of_published, point.t_sink_C, rh, q_W, airflow)
        coil = solved(shr, fan_W)
        rows.append(
            {
                "slug": slug,
                "unit": catalog.name,
                "status": catalog.status,
                "nominal_kW": catalog.nominal_capacity_kW,
                "airflow_m3_s": airflow,
                "point_id": point.id,
                "t_source_C": point.t_source_C,
                "t_sink_C": point.t_sink_C,
                "t_wb_C": wet_bulb,
                "rh_room": rh,
                "q_kW": point.q_kW,
                "shr_catalog": shr,
                "fan_W": fan_W,
                "shr_on_coil": catalogue_ratio_on_the_coil(shr, q_W, fan_W),
                **coil,
                "UA_lo_W_K": solved(shr - SHR_ROUNDING, fan_W)["UA_W_K"],
                "UA_hi_W_K": solved(shr + SHR_ROUNDING, fan_W)["UA_W_K"],
                "UA_no_fan_W_K": solved(shr, 0.0)["UA_W_K"],
                "shr_closure": coil_closure(point.t_sink_C, rh, q_W + fan_W, airflow, coil),
            }
        )
    return pd.DataFrame(rows)


def _log_slopes(frame: pd.DataFrame) -> tuple[float, float, float, float]:
    """Slopes of ``ln UA`` on the outdoor and the indoor dry bulb [%/K] and their standard errors (least squares)."""
    nan = math.nan
    if len(frame) < 4 or frame.t_source_C.nunique() < 2 or frame.t_sink_C.nunique() < 2:
        return nan, nan, nan, nan
    design = np.column_stack([np.ones(len(frame)), frame.t_source_C, frame.t_sink_C])
    target = np.log(frame.UA_W_K.to_numpy(dtype=float))
    solution, *_ = np.linalg.lstsq(design, target, rcond=None)
    residual = target - design @ solution
    variance = float(residual @ residual) / (len(frame) - 3)
    errors = np.sqrt(np.diag(variance * np.linalg.inv(design.T @ design)))
    return 100.0 * solution[1], 100.0 * errors[1], 100.0 * solution[2], 100.0 * errors[2]


def conductance_at_rating(ua_W_K: float, airflow_m3_s: float) -> float:
    """``eps C`` of a coil of conductance ``ua_W_K`` at ``airflow_m3_s`` of 27 degC air [W/K]."""
    from tmhp.air_properties import air_heat_capacity_rate

    capacity_rate = float(air_heat_capacity_rate(airflow_m3_s, T_RATING_INDOOR_C))
    return capacity_rate * -math.expm1(-ua_W_K / capacity_rate)


def ua_by_unit(coils: pd.DataFrame) -> pd.DataFrame:
    """The units' conductances: the median of the solved rows, its spread and what it is per watt and per airflow."""
    from tmhp.air_source_heat_pump import INDOOR_UA_PER_CAPACITY

    rows = []
    for slug, frame in coils.groupby("slug", sort=False):
        good = frame[frame.solve == "ok"]
        nominal_W = float(frame.nominal_kW.iat[0]) * 1000.0
        airflow = float(frame.airflow_m3_s.iat[0])
        ua = float(good.UA_W_K.median()) if len(good) else math.nan
        low = good.UA_lo_W_K.fillna(0.0)
        high = good.UA_hi_W_K.fillna(math.inf)
        # The band of a row whose ratio plus half a digit is 1 has no upper end; its half-width is not taken.
        half_width = ((good.UA_hi_W_K - good.UA_lo_W_K) / 2.0 / good.UA_W_K).dropna()
        slope_out, slope_out_se, slope_in, slope_in_se = _log_slopes(good)
        shipped = INDOOR_UA_PER_CAPACITY * nominal_W
        rows.append(
            {
                "slug": slug,
                "unit": frame.unit.iat[0],
                "nominal_kW": nominal_W / 1000.0,
                "airflow_m3_s": airflow,
                "rows": int(len(frame)),
                "rows_solved": int(len(good)),
                "rows_ratio_one": int((frame.solve == "ratio_one").sum()),
                "rows_below_coil_line": int((frame.solve == "below_coil_line").sum()),
                "rows_contact_factor_one": int((frame.solve == "contact_factor_one").sum()),
                "UA_W_K": ua,
                "UA_q25_W_K": float(good.UA_W_K.quantile(0.25)) if len(good) else math.nan,
                "UA_q75_W_K": float(good.UA_W_K.quantile(0.75)) if len(good) else math.nan,
                "UA_min_W_K": float(good.UA_W_K.min()) if len(good) else math.nan,
                "UA_max_W_K": float(good.UA_W_K.max()) if len(good) else math.nan,
                "UA_no_fan_W_K": float(good.UA_no_fan_W_K.median()) if len(good) else math.nan,
                "UA_per_W_nameplate": ua / nominal_W,
                "UA_per_m3_s": ua / airflow,
                "contact_factor": float(good.contact_factor.median()) if len(good) else math.nan,
                "conductance_W_K": conductance_at_rating(ua, airflow) if math.isfinite(ua) else math.nan,
                "UA_shipped_W_K": shipped,
                "UA_shipped_over_solved": shipped / ua,
                "conductance_shipped_W_K": conductance_at_rating(shipped, airflow),
                "rows_rounding_band_holds_UA": int(((low <= ua) & (ua <= high)).sum()),
                "rounding_half_width_pct": float(half_width.mean() * 100.0) if len(half_width) else math.nan,
                "UA_spread_pct": float(np.log(good.UA_W_K).std() * 100.0) if len(good) > 1 else math.nan,
                "UA_slope_pct_per_K_outdoor": slope_out,
                "UA_slope_se_pct_per_K_outdoor": slope_out_se,
                "UA_slope_pct_per_K_indoor": slope_in,
                "UA_slope_se_pct_per_K_indoor": slope_in_se,
                "shr_closure_abs_error_max": float((good.shr_closure - good.shr_on_coil).abs().max())
                if len(good)
                else math.nan,
            }
        )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 5. Candidate rules
# ---------------------------------------------------------------------------


def fit_rule(rule: str, units: pd.DataFrame) -> dict:
    """Parameters of one rule fitted to the units' conductances (least squares on the logarithm)."""
    log_ua = np.log(units.UA_W_K.to_numpy(dtype=float))
    nominal_W = units.nominal_kW.to_numpy(dtype=float) * 1000.0
    airflow = units.airflow_m3_s.to_numpy(dtype=float)
    if rule == "nameplate":
        return {"k": float(np.exp(np.mean(log_ua - np.log(nominal_W))))}
    if rule == "airflow":
        return {"k": float(np.exp(np.mean(log_ua - np.log(airflow))))}
    if rule == "airflow_power":
        exponent, intercept = np.polyfit(np.log(airflow), log_ua, 1)
        return {"k": float(np.exp(intercept)), "n": float(exponent)}
    if rule == "constant":
        return {"k": float(np.exp(np.mean(log_ua)))}
    if rule == "conductance":
        return {"k": float(np.exp(np.mean(np.log(units.conductance_W_K.to_numpy(dtype=float)))))}
    raise ValueError(f"no rule {rule!r}")


def rule_ua(rule: str, parameters: dict, nominal_W: float, airflow_m3_s: float) -> float:
    """Conductance [W/K] a fitted rule gives a unit; NaN where it gives none."""
    from tmhp.air_properties import air_heat_capacity_rate

    k = parameters["k"]
    if rule == "nameplate":
        return k * nominal_W
    if rule == "airflow":
        return k * airflow_m3_s
    if rule == "airflow_power":
        return k * airflow_m3_s ** parameters["n"]
    if rule == "constant":
        return k
    if rule == "conductance":
        capacity_rate = float(air_heat_capacity_rate(airflow_m3_s, T_RATING_INDOOR_C))
        # The airflow carries at most its heat capacity rate per kelvin: no coil gives more.
        return -capacity_rate * math.log1p(-k / capacity_rate) if k < capacity_rate else math.nan
    raise ValueError(f"no rule {rule!r}")


def _rule_rows(unit, fitted_on: pd.DataFrame) -> list[dict]:
    """The conductance each rule, fitted on ``fitted_on``, gives ``unit``; the shipped rule and the unit's own beside."""
    candidates: dict[str, tuple[dict, float]] = {"shipped": ({}, unit.UA_shipped_W_K), "own": ({}, unit.UA_W_K)}
    for rule in RULES:
        parameters = fit_rule(rule, fitted_on)
        candidates[rule] = (parameters, rule_ua(rule, parameters, unit.nominal_kW * 1000.0, unit.airflow_m3_s))
    rows = []
    for rule, (parameters, ua) in candidates.items():
        conductance = conductance_at_rating(ua, unit.airflow_m3_s) if math.isfinite(ua) else math.nan
        rows.append(
            {
                "rule": rule,
                "slug": unit.slug,
                "unit": unit.unit,
                "k": parameters.get("k", math.nan),
                "n": parameters.get("n", math.nan),
                "UA_rule_W_K": ua,
                "UA_solved_W_K": unit.UA_W_K,
                "UA_error_pct": (ua / unit.UA_W_K - 1.0) * 100.0,
                "conductance_rule_W_K": conductance,
                "conductance_solved_W_K": unit.conductance_W_K,
                "conductance_error_pct": (conductance / unit.conductance_W_K - 1.0) * 100.0,
            }
        )
    return rows


def leave_one_out(units: pd.DataFrame) -> pd.DataFrame:
    """Each rule fitted without one unit, and the conductance it gives that unit."""
    return pd.DataFrame(
        [row for unit in units.itertuples() for row in _rule_rows(unit, units[units.slug != unit.slug])]
    )


def rules_for_held(units: pd.DataFrame, held_units: pd.DataFrame) -> pd.DataFrame:
    """Each rule fitted on all the adopted units, and the conductance it gives each held unit."""
    return pd.DataFrame([row for unit in held_units.itertuples() for row in _rule_rows(unit, units)])


# ---------------------------------------------------------------------------
# 2. to 4. The model with a conductance
# ---------------------------------------------------------------------------


def _pass_name(rule: str) -> str:
    return f"wet_rule_{rule}"


def _heating_pass_name(rule: str) -> str:
    return f"heat_rule_{rule}"


def cooling_conductances(slug: str, units: pd.DataFrame, rules: pd.DataFrame) -> dict[str, tuple[bool, float, bool]]:
    """Passes of one unit's cooling rows: name -> (wet coil?, ``UA_iu_rated`` [W/K], compressor as shipped?).

    ``units`` holds the unit's own conductance (``UA_W_K``) and the shipped
    one, ``rules`` the conductance each rule gives it (:func:`leave_one_out`
    for an adopted unit, :func:`rules_for_held` for a held one). An adopted
    unit runs every pass; a held one the dry and the wet pass, its own
    conductance and the rules.
    """
    unit = units.set_index("slug").loc[slug]
    shipped, own = float(unit.UA_shipped_W_K), float(unit.UA_W_K)
    passes: dict[str, tuple[bool, float, bool]] = {
        "dry": (False, shipped, False),
        "wet": (True, shipped, False),
        "wet_ua_shr": (True, own, True),
    }
    if slug in UNITS:
        passes["wet_ua_shr_rerated"] = (True, own, False)
        passes.update({name: (True, own * scale, True) for name, scale in UA_SCALES.items()})
    of_unit = rules[rules.slug == slug].set_index("rule")
    passes.update({_pass_name(rule): (True, float(of_unit.UA_rule_W_K[rule]), True) for rule in RULES})
    return passes


def _model(catalog, mode: str, shipped, ua: float, as_shipped_compressor: bool):
    """The harness's model of a unit with ``UA_iu_rated = ua``; ``shipped`` itself for the shipped conductance.

    With ``as_shipped_compressor`` the rated speed of the shipped build is
    passed as well, so the compressor is the same machine (the same rated
    speed and, derived from it, the same highest speed) and only the coil
    differs. The model then only reports a reference mass flow
    it does not use here, and warns where the nameplate duty has no dry
    rating point with that coil; the warning is not an error of the pass.
    Without it the reference state is re-solved with the new coil, and a
    coil too small for a dry rating point is refused by the model
    (``None``).
    """
    from tmhp.reference_state import ReferenceStateError
    from validation.parity.run import _build_model

    if not (math.isfinite(ua) and ua > 0.0):
        return None
    if ua == shipped.UA_iu_rated and not as_shipped_compressor:
        return shipped
    overrides = {"UA_iu_rated": ua}
    if as_shipped_compressor:
        overrides["rps_rated"] = shipped.rps_rated
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="m_dot_ref_rated not derived", category=RuntimeWarning)
            return _build_model(catalog, overrides, mode)
    except ReferenceStateError:
        return None


def _scored(point, result: dict) -> tuple[str, bool, float]:
    """(failure, usable, COP) of one result, scored as ``validation.parity.run.run_catalog`` scores it."""
    from validation.parity.run import CAPACITY_SHORT, CAPACITY_SHORT_TOL, CYCLING_MEAN, _cycling_mean, _result_number

    failure = result.get("failure_reason", "none")
    # The parity harness's own test of a duty short of the published one (``run_catalog``).
    delivered = abs(_result_number(result, "Q_ref_iu [W]"))
    requested = point.q_kW * 1000.0
    if failure == "none" and math.isfinite(delivered) and delivered < requested * (1.0 - CAPACITY_SHORT_TOL):
        failure = CAPACITY_SHORT
    elif failure == "none" and _cycling_mean(result):
        failure = CYCLING_MEAN
    cop = float(result.get("cop_sys [-]", math.nan))
    usable = failure == "none" and math.isfinite(cop) and cop > 0.0
    return failure, usable, cop if usable else math.nan


def _columns(name: str, point, result: dict | None, failure: str, usable: bool, cop: float, detailed: bool) -> dict:
    """The columns of one pass of one cooling row."""
    nan = math.nan

    def value(key: str) -> float:
        return float(result.get(key, nan)) if usable and result is not None else nan

    net_sensible, latent = value("Q_room_sensible [W]"), value("Q_iu_latent [W]")
    net_total = net_sensible + latent
    row = {
        f"cop_{name}": cop,
        f"T_evap_C_{name}": value("T_ref_evap_sat [°C]"),
        f"pr_{name}": value("pr_cmp [-]"),
        f"shr_net_{name}": net_sensible / net_total if usable and net_total > 0.0 else nan,
        f"failure_{name}": failure,
        f"usable_{name}": usable,
    }
    if detailed:
        fans = value("E_iu_fan [W]") + value("E_ou_fan [W]")
        # What the published COP leaves for the compressor at the model's delivery and fan powers.
        needed = net_total / point.target_cop() - fans
        pr, rps, t_evap = value("pr_cmp [-]"), value("cmp_rpm [rpm]") / 60.0, value("T_ref_evap_sat [°C]")
        row.update(
            {
                f"power_kW_{name}": value("E_tot [W]") / 1000.0,
                f"rps_{name}": rps,
                f"n_star_{name}": value("n_star [-]"),
                f"T_cond_C_{name}": value("T_ref_cond_sat_l [°C]"),
                f"airflow_m3_s_{name}": value("dV_iu_a [m3/s]"),
                f"eta_overall_{name}": value("eta_cmp_isen [-]") * value("eta_cmp [-]"),
                f"compressor_input_over_needed_{name}": value("E_cmp [W]") / needed if usable and needed > 0.0 else nan,
                **map_position(name, pr, rps, t_evap),
                f"efficiency_extrapolated_{name}": bool(result.get("efficiency_extrapolated", False))
                if usable and result is not None
                else False,
            }
        )
    return row


def map_pressure_ratio_range(t_evap_C: float) -> tuple[float, float]:
    """Lowest and highest pressure ratio of the efficiency map's data at one evaporating temperature.

    The map's data domain in (pressure ratio, evaporating temperature) is the
    convex hull of its 134 measured points
    (``tmhp.compressor_efficiency.DOMAIN``); this is the chord of that hull
    at ``t_evap_C``. NaN, NaN at an evaporating temperature the data do not
    reach.
    """
    from tmhp.compressor_efficiency import DOMAIN

    vertices = DOMAIN.hull_PR_Te.vertices
    crossings = []
    for (pr_a, te_a), (pr_b, te_b) in zip(vertices, vertices[1:] + vertices[:1], strict=True):
        if te_a != te_b and min(te_a, te_b) <= t_evap_C <= max(te_a, te_b):
            crossings.append(pr_a + (pr_b - pr_a) * (t_evap_C - te_a) / (te_b - te_a))
    return (min(crossings), max(crossings)) if crossings else (math.nan, math.nan)


def efficiency_map_data() -> dict:
    """The data domain of the default efficiency map, as the run found it in the library."""
    from tmhp.compressor_efficiency import COEFFICIENT_VERSION, DOMAIN

    return {
        "coefficient_version": COEFFICIENT_VERSION,
        "speed_range_rev_s": list(DOMAIN.rps_range),
        "hull_pressure_ratio_T_evap_C": [list(vertex) for vertex in DOMAIN.hull_PR_Te.vertices],
        # The chord of the hull at the evaporating temperatures the cooling rows run at.
        "pressure_ratio_range_at_T_evap_C": {
            f"{t:g}": list(map_pressure_ratio_range(t)) for t in (5.0, 10.0, 15.0, 20.0)
        },
    }


def map_position(name: str, pr: float, rps: float, t_evap_C: float) -> dict:
    """Where one operating point lies against the data of the default efficiency map.

    ``map_pr_min_``: the lowest pressure ratio of the map's data at the row's
    evaporating temperature. ``outside_map_speed_``: the speed is outside the
    30-110 rev/s of the data. ``outside_map_pr_``: the (pressure ratio,
    evaporating temperature) pair is outside the hull of the data. A row that
    was not scored is in neither.
    """
    from tmhp.compressor_efficiency import DOMAIN

    if not all(math.isfinite(v) for v in (pr, rps, t_evap_C)):
        return {f"map_pr_min_{name}": math.nan, f"outside_map_speed_{name}": False, f"outside_map_pr_{name}": False}
    low, high = DOMAIN.rps_range
    return {
        f"map_pr_min_{name}": map_pressure_ratio_range(t_evap_C)[0],
        f"outside_map_speed_{name}": not low <= rps <= high,
        f"outside_map_pr_{name}": not DOMAIN.hull_PR_Te.contains(pr, t_evap_C),
    }


def run_cooling(
    slug: str,
    conductances: dict[str, tuple[bool, float, bool]],
    point_ids: tuple[int, ...] | None = None,
) -> pd.DataFrame:
    """The cooling rows of one catalogue (or ``point_ids`` of it) in the passes of ``conductances``."""
    from validation.parity.run import _build_model
    from validation.parity.spec import load_all

    catalog = load_all(slug)[0]
    shipped = _build_model(catalog, None, "cooling")
    models = {name: _model(catalog, "cooling", shipped, ua, pinned) for name, (_, ua, pinned) in conductances.items()}
    # The rated speed and the (dry) rating point's evaporating temperature: of the shipped build, which the
    # passes that leave the compressor as shipped keep, and of the one pass that re-solves its reference state.
    rated = {"rps_rated": float(shipped.rps_rated), "T_evap_rated_C": float(shipped.reference_state.T_evap_sat_C)}
    rerated = models.get("wet_ua_shr_rerated")
    if "wet_ua_shr_rerated" in conductances:
        rated["rps_rated_wet_ua_shr_rerated"] = float(rerated.rps_rated) if rerated is not None else math.nan
        rated["T_evap_rated_C_wet_ua_shr_rerated"] = (
            float(rerated.reference_state.T_evap_sat_C) if rerated is not None else math.nan
        )
    rows = []
    for point in catalog.points:
        if point.mode != "cooling" or (point_ids is not None and point.id not in point_ids):
            continue
        parsed = parse_note(point.note)
        if parsed is None:
            raise SystemExit(f"{slug} point {point.id}: no indoor wet bulb and sensible heat ratio in {point.note!r}")
        wet_bulb, shr = parsed
        rh = room_humidity(point.t_sink_C, wet_bulb)
        row = {
            "slug": slug,
            "unit": catalog.name,
            "status": catalog.status,
            "nominal_kW": catalog.nominal_capacity_kW,
            "point_id": point.id,
            "t_source_C": point.t_source_C,
            "t_sink_C": point.t_sink_C,
            "t_wb_C": wet_bulb,
            "q_kW": point.q_kW,
            "cop_target": point.target_cop(),
            "shr_catalog": shr,
            **rated,
            "pr_floor": float(shipped.PR_cycle_min),
        }
        for name, (wet, ua, _) in conductances.items():
            row[f"UA_W_K_{name}"] = ua
            model = models[name]
            if model is None:
                row.update(_columns(name, point, None, NO_CONDUCTANCE, False, math.nan, name in DETAILED))
                continue
            result = model.analyze_steady(
                Q_r_iu=point.q_kW * 1000.0,
                T0=point.t_source_C,
                T_a_room=point.t_sink_C,
                RH_room=rh if wet else None,
                return_dict=True,
                verbose=False,
            )
            row.update(_columns(name, point, result, *_scored(point, result), name in DETAILED))
        rows.append(row)
    return pd.DataFrame(rows)


def heating_conductances(slug: str, units: pd.DataFrame, rules: pd.DataFrame | None = None) -> dict[str, float]:
    """Heating passes of one unit: name -> ``UA_iu_rated`` [W/K] (``heat``: the shipped model as it is).

    With ``rules`` (:func:`leave_one_out`) the conductance each rule gives
    the unit when fitted on the other four is run as well
    (``heat_rule_<rule>``).
    """
    unit = units.set_index("slug").loc[slug]
    shipped = float(unit.UA_shipped_W_K)
    passes = {
        "heat": shipped,
        "heat_ua_shr": float(unit.UA_W_K),
        **{name: shipped * scale for name, scale in HEATING_SCALES.items()},
    }
    if rules is not None:
        of_unit = rules[rules.slug == slug].set_index("rule")
        passes.update({_heating_pass_name(rule): float(of_unit.UA_rule_W_K[rule]) for rule in RULES})
    return passes


def run_heating(slug: str, conductances: dict[str, float], point_ids: tuple[int, ...] | None = None) -> pd.DataFrame:
    """The heating rows of one catalogue (or ``point_ids`` of it) with the indoor conductances of ``conductances``."""
    from validation.parity.run import _build_model, _run_point
    from validation.parity.spec import load_all

    catalog = load_all(slug)[0]
    shipped = _build_model(catalog, None, "heating")
    models = {
        name: shipped if name == "heat" else _model(catalog, "heating", shipped, ua, True)
        for name, ua in conductances.items()
    }
    rows = []
    for point in catalog.points:
        if point.mode != "heating" or (point_ids is not None and point.id not in point_ids):
            continue
        row = {
            "slug": slug,
            "unit": catalog.name,
            "nominal_kW": catalog.nominal_capacity_kW,
            "point_id": point.id,
            "t_source_C": point.t_source_C,
            "t_sink_C": point.t_sink_C,
            "q_kW": point.q_kW,
            "cop_target": point.target_cop(),
        }
        for name, ua in conductances.items():
            row[f"UA_W_K_{name}"] = ua
            model = models[name]
            if model is None:
                row.update({f"cop_{name}": math.nan, f"failure_{name}": NO_CONDUCTANCE, f"usable_{name}": False})
                row.update({f"T_cond_C_{name}": math.nan, f"airflow_m3_s_{name}": math.nan})
                continue
            result = _run_point(model, catalog, point)
            failure, usable, cop = _scored(point, result)
            row.update(
                {
                    f"cop_{name}": cop,
                    f"T_cond_C_{name}": float(result.get("T_ref_cond_sat_l [°C]", math.nan)) if usable else math.nan,
                    f"airflow_m3_s_{name}": float(result.get("dV_iu_a [m3/s]", math.nan)) if usable else math.nan,
                    f"failure_{name}": failure,
                    f"usable_{name}": usable,
                }
            )
        rows.append(row)
    return pd.DataFrame(rows)


#: Paths of the scan and of the default passes: name -> (rows of which mode, wet coil?). ``dry`` and ``heat`` are the
#: paths of the shipped parity results, ``wet`` the wet pass of ``validation/wet_coil_shr``.
DEFAULT_PATHS: dict[str, tuple[str, bool]] = {
    "dry": ("cooling", False),
    "wet": ("cooling", True),
    "heat": ("heating", False),
}


# ---------------------------------------------------------------------------
# 2b. A scan of the indoor conductance
# ---------------------------------------------------------------------------


def _solve_row(model, catalog, point, wet: bool) -> dict:
    """One catalogue row with ``model``: the heating row as the harness runs it, the cooling row dry or wet."""
    from validation.parity.run import _run_point

    if point.mode == "heating":
        return _run_point(model, catalog, point)
    humidity = None
    if wet:
        parsed = parse_note(point.note)
        if parsed is None:
            raise SystemExit(f"{catalog.slug} point {point.id}: no indoor wet bulb in {point.note!r}")
        humidity = room_humidity(point.t_sink_C, parsed[0])
    return model.analyze_steady(
        Q_r_iu=point.q_kW * 1000.0,
        T0=point.t_source_C,
        T_a_room=point.t_sink_C,
        RH_room=humidity,
        return_dict=True,
        verbose=False,
    )


def run_scan(slug: str, point_ids: dict[str, tuple[int, ...]] | None = None) -> pd.DataFrame:
    """One catalogue at every conductance of :data:`SCAN_SCALES`: one row per (path, scale, catalogue row).

    The paths are those of :data:`DEFAULT_PATHS` (cooling dry, cooling wet,
    heating). The conductance is the shipped one times the scale, the
    compressor left as shipped (``rps_rated`` of the shipped build passed).
    ``point_ids`` limits the rows by mode.
    """
    from validation.parity.run import _build_model
    from validation.parity.spec import load_all

    catalog = load_all(slug)[0]
    rows = []
    for path, (mode, wet) in DEFAULT_PATHS.items():
        shipped = _build_model(catalog, None, mode)
        for scale in SCAN_SCALES:
            ua = float(shipped.UA_iu_rated) * scale
            model = _model(catalog, mode, shipped, ua, True)
            for point in catalog.points:
                if point.mode != mode or (point_ids is not None and point.id not in point_ids.get(mode, ())):
                    continue
                result = _solve_row(model, catalog, point, wet)
                failure, usable, cop = _scored(point, result)
                saturation = "T_ref_cond_sat_l [°C]" if mode == "heating" else "T_ref_evap_sat [°C]"
                rows.append(
                    {
                        "path": path,
                        "slug": slug,
                        "unit": catalog.name,
                        "nominal_kW": catalog.nominal_capacity_kW,
                        "point_id": point.id,
                        "t_source_C": point.t_source_C,
                        "t_sink_C": point.t_sink_C,
                        "q_kW": point.q_kW,
                        "cop_target": point.target_cop(),
                        "scale": scale,
                        "UA_W_K": ua,
                        "cop": cop,
                        # The indoor coil's refrigerant temperature: evaporating in cooling, condensing in heating.
                        "T_sat_indoor_C": float(result.get(saturation, math.nan)) if usable else math.nan,
                        "pr": float(result.get("pr_cmp [-]", math.nan)) if usable else math.nan,
                        "failure": failure,
                        "usable": usable,
                    }
                )
    return pd.DataFrame(rows)


def scan_summary(scan: pd.DataFrame) -> pd.DataFrame:
    """COP bias at every conductance of the scan, and with each row at the conductance that suits it best.

    One row per (path, unit or all units, scale), and one with ``which ==
    "best"`` per (path, unit): every row is given the highest COP that any
    conductance of the scan scores for it, and the bias and error of those
    are taken. That is the closest to the catalogue the scan's conductances
    can bring rows that run low -- not the value at the largest
    conductance, which need not be a row's highest. On the ``best`` rows,
    ``rows_highest_at_the_largest_scale`` counts the rows whose COP at the
    largest scale is within 1e-6 of their highest,
    ``largest_scale_below_highest_pct_max`` is how far below it the others
    are at most, ``rows_cop_never_falls`` counts the rows whose COP does not
    fall (by more than 1e-6) from one scale to the next, and
    ``best_minus_scale_5_and_above_pct_points_max`` is how far the bias of
    the ``best`` row is from the bias at any scale from 5 up.
    """
    rows = []
    for path, of_path in scan.groupby("path", sort=False):
        groups = [(slug, frame) for slug, frame in of_path.groupby("slug", sort=False)]
        for slug, frame in [*groups, ("all", of_path)]:
            cop = frame.assign(cop=frame.cop.where(frame.usable)).pivot(
                index=["slug", "point_id"], columns="scale", values="cop"
            )
            target = frame.drop_duplicates(["slug", "point_id"]).set_index(["slug", "point_id"]).cop_target
            target = target.reindex(cop.index)
            bias_at = {}
            for scale in cop.columns:
                relative = ((cop[scale] - target) / target * 100.0).dropna()
                bias_at[float(scale)] = _mean(relative)
                rows.append(
                    {
                        "path": path,
                        "slug": slug,
                        "which": "scale",
                        "scale": float(scale),
                        "points": int(len(cop)),
                        "scored": int(len(relative)),
                        "cop_bias_pct": _mean(relative),
                        "cop_MAPE_pct": _mean(relative.abs()),
                    }
                )
            highest = cop.max(axis=1)
            relative = ((highest - target) / target * 100.0).dropna()
            largest = cop[cop.columns.max()]
            steps = cop.ffill(axis=1).diff(axis=1).iloc[:, 1:].div(highest, axis=0)
            top = [bias for scale, bias in bias_at.items() if scale >= 5.0]
            rows.append(
                {
                    "path": path,
                    "slug": slug,
                    "which": "best",
                    "scale": math.nan,
                    "points": int(len(cop)),
                    "scored": int(len(relative)),
                    "cop_bias_pct": _mean(relative),
                    "cop_MAPE_pct": _mean(relative.abs()),
                    "rows_highest_at_the_largest_scale": int((largest >= highest * (1.0 - 1e-6)).sum()),
                    "largest_scale_below_highest_pct_max": float(((1.0 - largest / highest) * 100.0).max()),
                    "rows_cop_never_falls": int((steps.fillna(0.0) >= -1e-6).all(axis=1).sum()),
                    "best_minus_scale_5_and_above_pct_points_max": float(max(_mean(relative) - bias for bias in top)),
                }
            )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 6. What a rule would move as the default
# ---------------------------------------------------------------------------


def default_conductances(units: pd.DataFrame) -> pd.DataFrame:
    """The conductance each rule, fitted on all of ``units``, gives each of them: one row per (rule, unit)."""
    rows = []
    for rule in RULES:
        parameters = fit_rule(rule, units)
        for unit in units.itertuples():
            rows.append(
                {
                    "rule": rule,
                    "slug": unit.slug,
                    "k": parameters.get("k", math.nan),
                    "n": parameters.get("n", math.nan),
                    "UA_rule_W_K": rule_ua(rule, parameters, unit.nominal_kW * 1000.0, unit.airflow_m3_s),
                    "UA_shipped_W_K": unit.UA_shipped_W_K,
                }
            )
    return pd.DataFrame(rows)


def run_defaults(
    slug: str, conductances: pd.DataFrame, point_ids: dict[str, tuple[int, ...]] | None = None
) -> pd.DataFrame:
    """One catalogue with each rule's conductance in place of the default: one row per (rule, path, catalogue row).

    ``conductances`` is :func:`default_conductances`. The model is built as
    the parity harness builds it with ``UA_iu_rated`` alone overridden, which
    is what a new default would do: the reference state, and with it the
    rated speed, is re-solved with the new coil. ``point_ids`` limits the
    rows by mode (``{"cooling": (...), "heating": (...)}``).
    """
    from validation.parity.run import _build_model
    from validation.parity.spec import load_all

    catalog = load_all(slug)[0]
    of_unit = conductances[conductances.slug == slug].set_index("rule")
    rows = []
    for rule in RULES:
        ua = float(of_unit.UA_rule_W_K[rule])
        models = {}
        for mode in ("cooling", "heating"):
            models[mode] = _model(catalog, mode, _build_model(catalog, None, mode), ua, False)
        for path, (mode, wet) in DEFAULT_PATHS.items():
            model = models[mode]
            for point in catalog.points:
                if point.mode != mode or (point_ids is not None and point.id not in point_ids.get(mode, ())):
                    continue
                row = {
                    "rule": rule,
                    "path": path,
                    "slug": slug,
                    "unit": catalog.name,
                    "nominal_kW": catalog.nominal_capacity_kW,
                    "point_id": point.id,
                    "t_source_C": point.t_source_C,
                    "t_sink_C": point.t_sink_C,
                    "q_kW": point.q_kW,
                    "cop_target": point.target_cop(),
                    "UA_W_K": ua,
                    "rps_rated": float(model.rps_rated) if model is not None else math.nan,
                }
                if model is None:
                    rows.append({**row, "cop": math.nan, "shr_net": math.nan, "failure": NO_CONDUCTANCE})
                    rows[-1]["usable"] = False
                    continue
                result = _solve_row(model, catalog, point, wet)
                failure, usable, cop = _scored(point, result)
                net_sensible = float(result.get("Q_room_sensible [W]", math.nan))
                net_total = net_sensible + float(result.get("Q_iu_latent [W]", math.nan))
                rows.append(
                    {
                        **row,
                        "cop": cop,
                        "shr_net": net_sensible / net_total if usable and wet and net_total > 0.0 else math.nan,
                        "failure": failure,
                        "usable": usable,
                    }
                )
    return pd.DataFrame(rows)


#: The shipped pass each default path is compared with: path -> (frame, pass).
SHIPPED_PASS = {"dry": ("cooling", "dry"), "wet": ("cooling", "wet"), "heat": ("heating", "heat")}


def default_summary(defaults: pd.DataFrame, points: pd.DataFrame, heating: pd.DataFrame) -> pd.DataFrame:
    """What each rule moves as the default: COP bias and error by path and unit, the shipped conductance beside.

    One row per (rule, path, unit) and per (rule, path) over all units, plus
    the path ``parity``: the dry cooling rows and the heating rows together,
    which is what the shipped per-unit results hold for these units.
    ``*_shipped`` are the same rows with the shipped conductance (the ``dry``,
    ``wet`` and ``heat`` passes); ``*_common`` are taken on the rows both
    score.
    """
    shipped_frames = {"cooling": points, "heating": heating}
    rows = []
    for rule, of_rule in defaults.groupby("rule", sort=False):
        parts = {}
        for path, frame in of_rule.groupby("path", sort=False):
            source, name = SHIPPED_PASS[path]
            before = shipped_frames[source][["slug", "point_id", f"cop_{name}", f"usable_{name}"]]
            before = before.rename(columns={f"cop_{name}": "cop_shipped", f"usable_{name}": "usable_shipped"})
            parts[path] = frame.merge(before, on=["slug", "point_id"], how="left", validate="one_to_one")
        parts["parity"] = pd.concat([parts["dry"], parts["heat"]], ignore_index=True)
        for path, frame in parts.items():
            groups = [(slug, part) for slug, part in frame.groupby("slug", sort=False)]
            for slug, part in [*groups, ("all", frame)]:
                part = part.assign(usable_shipped=part.usable_shipped.astype(bool))
                now, was = part[part.usable], part[part.usable_shipped]
                after = (now.cop - now.cop_target) / now.cop_target * 100.0
                before_rel = (was.cop_shipped - was.cop_target) / was.cop_target * 100.0
                both = part.usable & part.usable_shipped
                rows.append(
                    {
                        "rule": rule,
                        "path": path,
                        "slug": slug,
                        "points": int(len(part)),
                        "UA_W_K": float(part.UA_W_K.iat[0]) if slug != "all" else math.nan,
                        "rps_rated": float(part.rps_rated.iat[0]) if slug != "all" and path != "parity" else math.nan,
                        "scored": int(part.usable.sum()),
                        "cop_bias_pct": _mean(after),
                        "cop_MAPE_pct": _mean(after.abs()),
                        "scored_shipped": int(len(was)),
                        "cop_bias_pct_shipped": _mean(before_rel),
                        "cop_MAPE_pct_shipped": _mean(before_rel.abs()),
                        "rows_common": int(both.sum()),
                        "cop_bias_pct_common": _mean(after[both[part.usable]]),
                        "cop_bias_pct_shipped_common": _mean(before_rel[both[part.usable_shipped]]),
                        "cop_change_pct_mean_common": _mean((part.cop[both] / part.cop_shipped[both] - 1.0) * 100.0),
                    }
                )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------


def passes_of(frame: pd.DataFrame) -> tuple[str, ...]:
    """The passes a result frame holds, in its column order."""
    return tuple(column[len("usable_") :] for column in frame.columns if column.startswith("usable_"))


def _relative(frame: pd.DataFrame, name: str) -> pd.Series:
    """COP error of one pass against the catalogue [%], on the rows it scored."""
    good = frame[frame[f"usable_{name}"]]
    return (good[f"cop_{name}"] - good.cop_target) / good.cop_target * 100.0


def _bias(frame: pd.DataFrame, name: str) -> tuple[int, float, float]:
    """(rows scored, mean COP bias [%], mean absolute COP error [%]) of one pass."""
    relative = _relative(frame, name)
    if not len(relative):
        return 0, math.nan, math.nan
    return int(len(relative)), float(relative.mean()), float(relative.abs().mean())


def _mean(series: pd.Series) -> float:
    return float(series.mean()) if len(series) else math.nan


def zero_bias_point(frame: pd.DataFrame) -> tuple[float, float]:
    """Where a unit's mean COP bias is zero: (scale of its conductance of step 1, change of evaporating temperature [K]).

    Read from ``wet_ua_shr`` (scale 1) and the scaled passes on the rows all
    of them scored, by linear interpolation on the logarithm of the scale
    between the two scales that bracket zero bias; the evaporating
    temperature is interpolated the same way and given relative to
    ``wet_ua_shr``, the coil temperature the published ratio implies. NaN
    where the bias does not change sign over the scales run.
    """
    names = {"wet_ua_shr": 1.0, **UA_SCALES}
    if any(f"usable_{name}" not in frame.columns for name in names):
        return math.nan, math.nan
    common = frame[np.logical_and.reduce([frame[f"usable_{name}"] for name in names])]
    if not len(common):
        return math.nan, math.nan
    reference = float(common.T_evap_C_wet_ua_shr.mean())
    points = sorted(
        (scale, _bias(common, name)[1], float(common[f"T_evap_C_{name}"].mean()) - reference)
        for name, scale in names.items()
    )
    for (low, bias_low, shift_low), (high, bias_high, shift_high) in zip(points, points[1:], strict=False):
        if bias_low <= 0.0 <= bias_high and bias_low < bias_high:
            weight = -bias_low / (bias_high - bias_low)
            scale = math.exp(math.log(low) + weight * (math.log(high) - math.log(low)))
            return float(scale), float(shift_low + weight * (shift_high - shift_low))
    return math.nan, math.nan


def bias_by_unit(points: pd.DataFrame) -> pd.DataFrame:
    """COP bias and sensible heat ratio of every pass, by unit and over all rows."""
    names = passes_of(points)
    rows = []
    groups = [(slug, frame) for slug, frame in points.groupby("slug", sort=False)]
    for slug, frame in [*groups, ("all", points)]:
        row: dict = {
            "slug": slug,
            "unit": frame.unit.iat[0] if slug != "all" else "all",
            "points": int(len(frame)),
            "shr_catalog": float(frame.shr_catalog.mean()),
        }
        for name in names:
            scored, bias, mape = _bias(frame, name)
            good = frame[frame[f"usable_{name}"]]
            row.update(
                {
                    f"UA_W_K_{name}": float(frame[f"UA_W_K_{name}"].iat[0]) if slug != "all" else math.nan,
                    f"scored_{name}": scored,
                    f"cop_bias_pct_{name}": bias,
                    f"cop_MAPE_pct_{name}": mape,
                    f"T_evap_C_{name}": _mean(good[f"T_evap_C_{name}"]),
                }
            )
            if not name.startswith("dry"):
                error = good[f"shr_net_{name}"] - good.shr_catalog
                row.update({f"shr_error_{name}": _mean(error), f"shr_abs_error_{name}": _mean(error.abs())})
            if name in DETAILED:
                outside = good[f"outside_map_pr_{name}"] | good[f"outside_map_speed_{name}"]
                row.update(
                    {
                        f"pr_{name}": _mean(good[f"pr_{name}"]),
                        f"rps_{name}": _mean(good[f"rps_{name}"]),
                        f"n_star_{name}": _mean(good[f"n_star_{name}"]),
                        f"eta_overall_{name}": _mean(good[f"eta_overall_{name}"]),
                        f"compressor_input_over_needed_{name}": _mean(good[f"compressor_input_over_needed_{name}"]),
                        f"rows_outside_map_{name}": int(outside.sum()),
                        f"cop_bias_pct_inside_map_{name}": _bias(good[~outside], name)[1],
                        f"cop_bias_pct_outside_map_{name}": _bias(good[outside], name)[1],
                    }
                )
        row["UA_scale_for_zero_cop_bias"], row["T_evap_shift_for_zero_cop_bias_K"] = zero_bias_point(frame)
        if slug != "all":
            for column in (c for c in frame.columns if c.startswith(("rps_rated", "T_evap_rated_C"))):
                row[column] = float(frame[column].iat[0])
        rows.append(row)
    return pd.DataFrame(rows)


def _banded(points: pd.DataFrame, name: str, bands: list[tuple[str, pd.Series]], label: str) -> list[dict]:
    """Rows of a band table for one pass: each band, and all rows."""
    good = points[points[f"usable_{name}"]]
    relative = _relative(points, name)
    out = []
    for band, mask in [*bands, ("all", pd.Series(True, index=good.index))]:
        inside = good[mask.reindex(good.index, fill_value=False)]
        part = relative.loc[inside.index]
        out.append(
            {
                "pass": name,
                label: band,
                "rows": int(len(inside)),
                "cop_bias_pct": _mean(part),
                "cop_MAPE_pct": _mean(part.abs()),
                # What the band adds to the mean bias of the pass over all its rows [percentage points].
                "share_of_bias_pct_points": float(part.sum() / len(good)) if len(good) else math.nan,
                "rows_outside_map": int((inside[f"outside_map_pr_{name}"] | inside[f"outside_map_speed_{name}"]).sum()),
                "t_source_C": _mean(inside.t_source_C),
                "t_sink_C": _mean(inside.t_sink_C),
                "nominal_kW": _mean(inside.nominal_kW),
                "pr": _mean(inside[f"pr_{name}"]),
                "rps": _mean(inside[f"rps_{name}"]),
                "n_star": _mean(inside[f"n_star_{name}"]),
                "T_evap_C": _mean(inside[f"T_evap_C_{name}"]),
                "eta_overall": _mean(inside[f"eta_overall_{name}"]),
                "compressor_input_over_needed": _mean(inside[f"compressor_input_over_needed_{name}"]),
            }
        )
    return out


def _edge_bands(values: pd.Series, edges: tuple[float, ...]) -> list[tuple[str, pd.Series]]:
    """Bands of ``values`` cut at ``edges``: below the first, between neighbours, the last and above."""
    cuts = (-math.inf, *edges, math.inf)
    bands = []
    for low, high in zip(cuts, cuts[1:], strict=False):
        label = (
            f"below {high:g}"
            if low == -math.inf
            else f"{low:g} and above"
            if high == math.inf
            else f"{low:g} to {high:g}"
        )
        bands.append((label, (values >= low) & (values < high)))
    return bands


def _detailed(points: pd.DataFrame) -> tuple[str, ...]:
    return tuple(name for name in DETAILED if f"usable_{name}" in points.columns)


def bias_by_pressure_ratio(points: pd.DataFrame) -> pd.DataFrame:
    """COP bias of the detailed passes by band of the model's pressure ratio.

    The bands are cut at the pressure-ratio floor of the cycle
    (``PR_cycle_min``; a row within 1e-6 of it is on it) and at
    :data:`PR_EDGES`. Two more pairs of bands follow: below and from 1.94 (the
    split of the first run of this study), and outside and inside the data of
    the efficiency map (the convex hull of its measured points in pressure
    ratio and evaporating temperature, and its speed range).
    """
    rows: list[dict] = []
    for name in _detailed(points):
        pr, floor = points[f"pr_{name}"], points.pr_floor
        on_floor = pr <= floor * (1.0 + 1e-6)
        first, *rest = _edge_bands(pr, PR_EDGES)
        outside = points[f"outside_map_pr_{name}"] | points[f"outside_map_speed_{name}"]
        bands = [
            ("on the floor", on_floor),
            (f"floor to {PR_EDGES[0]:g}", first[1] & ~on_floor),
            *rest,
            (f"below {PR_EDGES[1]:g}", pr < PR_EDGES[1]),
            (f"{PR_EDGES[1]:g} and above", pr >= PR_EDGES[1]),
            ("outside the map's data", outside),
            ("inside the map's data", ~outside),
        ]
        rows += _banded(points, name, bands, "pressure_ratio_band")
    return pd.DataFrame(rows)


def speed_over_rating_row(points: pd.DataFrame, name: str) -> pd.Series:
    """Compressor speed of each row over the speed of its unit's rating row in the same pass.

    The rating row is the unit's row at 35 degC outdoors whose indoor dry
    bulb is nearest 27 degC (ISO 5151 T1; 26.7 degC in the Fujitsu tables).
    The catalogue files give the Daikin capacity tables as "at nominal
    operating frequency". Read as one compressor frequency for every row, a
    model that followed the table's capacity at a fixed speed would have 1 in
    every row; the catalogues print no frequency to check that reading with.
    NaN for a unit whose rating row is not in ``points`` or was not scored.
    """
    ratio = pd.Series(math.nan, index=points.index)
    for _, frame in points.groupby("slug", sort=False):
        at_rating = frame[frame.t_source_C == T_RATING_OUTDOOR_C]
        if not len(at_rating):
            continue
        row = (at_rating.t_sink_C - T_RATING_INDOOR_C).abs().idxmin()
        ratio.loc[frame.index] = frame[f"rps_{name}"] / points.at[row, f"rps_{name}"]
    return ratio


def bias_by_speed(points: pd.DataFrame) -> pd.DataFrame:
    """COP bias of the detailed passes by band of the compressor speed, on three scales.

    ``over rated``: over the model's rated speed (``n_star [-]``; the rated
    speed is that of the shipped build, solved at the nameplate duty on a dry
    coil). ``over the rating row``: over the speed of the unit's rating row in
    the same pass (:func:`speed_over_rating_row`). ``rev/s``: the speed
    itself, which is the axis of the efficiency map.
    """
    rows: list[dict] = []
    for name in _detailed(points):
        scales = (
            ("over rated", points[f"n_star_{name}"], RELATIVE_SPEED_EDGES),
            ("over the rating row", speed_over_rating_row(points, name), RATING_ROW_SPEED_EDGES),
            ("rev/s", points[f"rps_{name}"], SPEED_EDGES),
        )
        for scale, values, edges in scales:
            for row in _banded(points, name, _edge_bands(values, edges), "speed_band"):
                band = row.pop("speed_band")
                rows.append({"pass": row.pop("pass"), "speed_scale": scale, "speed_band": band, **row})
    return pd.DataFrame(rows)


def bias_by_evaporating_temperature(points: pd.DataFrame) -> pd.DataFrame:
    """COP bias of the detailed passes by band of the model's evaporating temperature [degC]."""
    rows: list[dict] = []
    for name in _detailed(points):
        rows += _banded(
            points, name, _edge_bands(points[f"T_evap_C_{name}"], T_EVAP_EDGES), "evaporating_temperature_band_C"
        )
    return pd.DataFrame(rows)


def bias_by_indoor_condition(points: pd.DataFrame) -> pd.DataFrame:
    """COP bias of the detailed passes by indoor dry bulb (each with its one wet bulb), by unit and over all units."""
    rows: list[dict] = []
    groups = [(slug, frame) for slug, frame in points.groupby("slug", sort=False)]
    for name in _detailed(points):
        for slug, frame in [*groups, ("all", points)]:
            for t_sink, part in frame.groupby("t_sink_C"):
                good = part[part[f"usable_{name}"]]
                relative = _relative(part, name)
                rows.append(
                    {
                        "pass": name,
                        "slug": slug,
                        "t_sink_C": float(t_sink),
                        "t_wb_C": _mean(part.t_wb_C),
                        "rows": int(len(good)),
                        "shr_catalog": _mean(good.shr_catalog),
                        "cop_bias_pct": _mean(relative),
                        "cop_MAPE_pct": _mean(relative.abs()),
                        "cop_target": _mean(good.cop_target),
                        "cop_model": _mean(good[f"cop_{name}"]),
                        "T_evap_C": _mean(good[f"T_evap_C_{name}"]),
                        "pr": _mean(good[f"pr_{name}"]),
                        "rps": _mean(good[f"rps_{name}"]),
                    }
                )
    return pd.DataFrame(rows)


def heating_by_unit(points: pd.DataFrame) -> pd.DataFrame:
    """Heating COP bias of each pass by unit and over all rows, and on the rows every pass scored."""
    names = passes_of(points)
    rows = []
    groups = [(slug, frame) for slug, frame in points.groupby("slug", sort=False)]
    for slug, frame in [*groups, ("all", points)]:
        common = frame[np.logical_and.reduce([frame[f"usable_{name}"] for name in names])]
        row: dict = {
            "slug": slug,
            "unit": frame.unit.iat[0] if slug != "all" else "all",
            "points": int(len(frame)),
            "rows_scored_by_every_pass": int(len(common)),
        }
        for name in names:
            scored, bias, mape = _bias(frame, name)
            good = frame[frame[f"usable_{name}"]]
            row.update(
                {
                    f"UA_W_K_{name}": float(frame[f"UA_W_K_{name}"].iat[0]) if slug != "all" else math.nan,
                    f"scored_{name}": scored,
                    f"cop_bias_pct_{name}": bias,
                    f"cop_MAPE_pct_{name}": mape,
                    f"cop_bias_pct_common_{name}": _bias(common, name)[1],
                    f"T_cond_C_{name}": _mean(good[f"T_cond_C_{name}"]),
                }
            )
        rows.append(row)
    return pd.DataFrame(rows)


def with_model_results(rules: pd.DataFrame, points: pd.DataFrame, heating: pd.DataFrame | None = None) -> pd.DataFrame:
    """A rule table with what the model gives each unit at the rule's conductance.

    Cooling (wet coil): scored rows, sensible heat ratio and COP bias. With
    ``heating`` (the frame of :func:`run_heating` with the rules' passes):
    scored rows and COP bias of the unit's heating rows as well.
    """
    pass_of = {"shipped": "wet", "own": "wet_ua_shr", **{rule: _pass_name(rule) for rule in RULES}}
    heating_pass_of = {"shipped": "heat", "own": "heat_ua_shr", **{rule: _heating_pass_name(rule) for rule in RULES}}
    rows = []
    for row in rules.itertuples(index=False):
        name = pass_of[row.rule]
        frame = points[points.slug == row.slug]
        good = frame[frame[f"usable_{name}"]]
        error = good[f"shr_net_{name}"] - good.shr_catalog
        scored, bias, mape = _bias(frame, name)
        out = {
            **row._asdict(),
            "points": int(len(frame)),
            "scored": scored,
            "shr_error": _mean(error),
            "shr_abs_error": _mean(error.abs()),
            "T_evap_C": _mean(good[f"T_evap_C_{name}"]),
            "cop_bias_pct": bias,
            "cop_MAPE_pct": mape,
        }
        if heating is not None:
            scored, bias, mape = _bias(heating[heating.slug == row.slug], heating_pass_of[row.rule])
            out.update(
                {
                    "heating_points": int((heating.slug == row.slug).sum()),
                    "heating_scored": scored,
                    "heating_cop_bias_pct": bias,
                    "heating_cop_MAPE_pct": mape,
                }
            )
        rows.append(out)
    return pd.DataFrame(rows)


def rule_summary(rules: pd.DataFrame) -> dict:
    """Each rule over the units it was tested on.

    Root-mean-square errors of the conductance, the mean ratio error, and of
    the units' COP biases the mean, the lowest, the highest and their spread
    (highest minus lowest: how far the bias runs with the size of the unit);
    the same for heating where the table has it.
    """
    out = {}
    for rule, frame in rules.groupby("rule", sort=False):
        ua, conductance = frame.UA_error_pct.dropna(), frame.conductance_error_pct.dropna()
        bias = frame.cop_bias_pct.dropna()
        out[rule] = {
            "units": int(len(frame)),
            "units_with_a_conductance": int(frame.UA_rule_W_K.notna().sum()),
            "UA_error_rms_pct": float(np.sqrt((ua**2).mean())) if len(ua) else math.nan,
            "conductance_error_rms_pct": float(np.sqrt((conductance**2).mean())) if len(conductance) else math.nan,
            "conductance_error_abs_max_pct": float(conductance.abs().max()) if len(conductance) else math.nan,
            "shr_abs_error_mean": _mean(frame.shr_abs_error.dropna()),
            "shr_error_mean": _mean(frame.shr_error.dropna()),
            "rows_scored": int(frame.scored.sum()),
            "cop_bias_pct_mean_of_units": _mean(bias),
            "cop_bias_pct_min_of_units": float(bias.min()) if len(bias) else math.nan,
            "cop_bias_pct_max_of_units": float(bias.max()) if len(bias) else math.nan,
            "cop_bias_pct_spread_of_units": float(bias.max() - bias.min()) if len(bias) else math.nan,
            "cop_MAPE_pct_mean_of_units": _mean(frame.cop_MAPE_pct.dropna()),
        }
        if "heating_cop_bias_pct" in frame.columns:
            heat = frame.heating_cop_bias_pct.dropna()
            out[rule].update(
                {
                    "heating_rows": int(frame.heating_points.sum()),
                    "heating_rows_scored": int(frame.heating_scored.sum()),
                    "heating_cop_bias_pct_mean_of_units": _mean(heat),
                    "heating_cop_bias_pct_min_of_units": float(heat.min()) if len(heat) else math.nan,
                    "heating_cop_bias_pct_max_of_units": float(heat.max()) if len(heat) else math.nan,
                    "heating_cop_bias_pct_spread_of_units": float(heat.max() - heat.min()) if len(heat) else math.nan,
                    "heating_cop_MAPE_pct_mean_of_units": _mean(frame.heating_cop_MAPE_pct.dropna()),
                }
            )
    return out


def parity_gate(points: pd.DataFrame, name: str, results_dir: Path = REPO_ROOT / "validation" / "results") -> float:
    """Largest relative difference between one pass and the committed parity results (COP, row for row)."""
    worst = 0.0
    for slug, frame in points.groupby("slug", sort=False):
        shipped = pd.read_csv(results_dir / f"{slug}.csv", float_precision="round_trip").set_index("point_id")
        for row in frame.itertuples():
            committed, computed = shipped.at[row.point_id, "cop_pred"], getattr(row, f"cop_{name}")
            if pd.isna(committed) or pd.isna(computed):
                if not (pd.isna(committed) and pd.isna(computed)):
                    return math.inf
                continue
            worst = max(worst, abs(computed - committed) / committed)
    return worst


def _explained(target: pd.Series, columns: list[np.ndarray]) -> float:
    """Share of the variance of ``target`` a least-squares fit on ``columns`` (plus a constant) leaves explained."""
    values = target.to_numpy(dtype=float)
    if len(values) < 3 or float(np.var(values)) == 0.0:
        return math.nan
    design = np.column_stack([np.ones(len(values)), *columns])
    solution, *_ = np.linalg.lstsq(design, values, rcond=None)
    residual = values - design @ solution
    return float(1.0 - np.var(residual) / np.var(values))


def _unit_spread(frame: pd.DataFrame, name: str) -> float:
    """Highest minus lowest of the units' mean COP biases of one pass [percentage points]."""
    biases = [_bias(part, name)[1] for _, part in frame.groupby("slug", sort=False)]
    biases = [b for b in biases if math.isfinite(b)]
    return float(max(biases) - min(biases)) if biases else math.nan


def headline(units: pd.DataFrame, points: pd.DataFrame, heating: pd.DataFrame | None = None) -> dict:
    """The numbers the study answers with, from the unit table, the cooling rows and the heating rows.

    Of the adopted units, or of the held ones: whichever rows the frames
    hold. A statistic with no row to take it from is NaN (``null`` in the
    manifest).
    """
    names = passes_of(points)
    out: dict = {
        "cooling_rows": int(len(points)),
        "rows_solved": int(units.rows_solved.sum()),
        "rows_ratio_one": int(units.rows_ratio_one.sum()),
        "rows_below_coil_line": int(units.rows_below_coil_line.sum()),
        "rows_contact_factor_one": int(units.rows_contact_factor_one.sum()),
        "rows_rounding_band_holds_UA": int(units.rows_rounding_band_holds_UA.sum()),
        "shr_closure_abs_error_max": float(units.shr_closure_abs_error_max.max()),
        "UA_W_K": dict(zip(units.slug, units.UA_W_K, strict=True)),
        "UA_shipped_W_K": dict(zip(units.slug, units.UA_shipped_W_K, strict=True)),
        "UA_per_W_nameplate": dict(zip(units.slug, units.UA_per_W_nameplate, strict=True)),
        "UA_per_m3_s": dict(zip(units.slug, units.UA_per_m3_s, strict=True)),
        "contact_factor": dict(zip(units.slug, units.contact_factor, strict=True)),
        "conductance_W_K": dict(zip(units.slug, units.conductance_W_K, strict=True)),
    }
    for name in names:
        scored, bias, mape = _bias(points, name)
        good = points[points[f"usable_{name}"]]
        out.update(
            {
                f"scored_{name}": scored,
                f"cop_bias_pct_{name}": bias,
                f"cop_MAPE_pct_{name}": mape,
                # How far the bias runs with the size of the unit: highest minus lowest unit mean.
                f"cop_bias_pct_spread_of_units_{name}": _unit_spread(points, name),
            }
        )
        if not name.startswith("dry"):
            error = good[f"shr_net_{name}"] - good.shr_catalog
            out.update({f"shr_error_{name}": _mean(error), f"shr_abs_error_{name}": _mean(error.abs())})
    if "usable_wet_ua_shr" in points.columns and "usable_wet" in points.columns:
        name = "wet_ua_shr"
        good = points[points[f"usable_{name}"]]
        relative = _relative(points, name)
        pr, rps, t_evap = good[f"pr_{name}"], good[f"rps_{name}"], good[f"T_evap_C_{name}"]
        on_floor = pr <= good.pr_floor * (1.0 + 1e-6)
        outside_pr, outside_speed = good[f"outside_map_pr_{name}"], good[f"outside_map_speed_{name}"]
        outside = outside_pr | outside_speed
        both = points[points.usable_wet & points.usable_wet_ua_shr]
        solved = good[good.shr_catalog < 1.0]
        axes = [np.log(pr.to_numpy(dtype=float)), rps.to_numpy(dtype=float), t_evap.to_numpy(dtype=float)]
        of_unit = [(good.slug == slug).to_numpy(dtype=float) for slug in list(dict.fromkeys(good.slug))[1:]]
        indoor = [(good.t_sink_C == t).to_numpy(dtype=float) for t in sorted(set(good.t_sink_C))[1:]]
        out.update(
            {
                # What the conductance of the sensible heat ratio moves of the wet coil's bias, and of its run with size.
                "cop_bias_pct_points_moved_by_UA_of_shr": out[f"cop_bias_pct_{name}"] - out["cop_bias_pct_wet"],
                "cop_bias_spread_of_units_closed_by_UA_of_shr_pct": (
                    1.0 - out[f"cop_bias_pct_spread_of_units_{name}"] / out["cop_bias_pct_spread_of_units_wet"]
                )
                * 100.0,
                "T_evap_change_UA_of_shr_vs_shipped_K_mean": _mean(both.T_evap_C_wet_ua_shr - both.T_evap_C_wet),
                # The same conductance with the reference state re-solved (``UA_iu_rated`` alone): how far the
                # rows' COP, evaporating temperature and pressure ratio are from those of the pinned pass.
                **{
                    f"{key}_max_abs_difference_rerated": float(
                        (good[f"{column}_{name}"] - good[f"{column}_{name}_rerated"]).abs().max()
                    )
                    if f"{column}_{name}_rerated" in good.columns
                    else math.nan
                    for key, column in (("cop", "cop"), ("T_evap_K", "T_evap_C"), ("pr", "pr"))
                },
                # Where the rows are against the data of the efficiency map.
                "rows_on_floor": int(on_floor.sum()),
                "rows_outside_map": int(outside.sum()),
                "rows_outside_map_pressure_ratio": int(outside_pr.sum()),
                "rows_outside_map_speed": int(outside_speed.sum()),
                "rows_flagged_extrapolated": int(good[f"efficiency_extrapolated_{name}"].sum()),
                "cop_bias_pct_on_floor": _mean(relative[on_floor]),
                "cop_bias_pct_outside_map": _mean(relative[outside]),
                "cop_bias_pct_inside_map": _mean(relative[~outside]),
                "cop_bias_pct_points_outside_map": float(relative[outside].sum() / len(good)),
                "cop_bias_pct_points_inside_map": float(relative[~outside].sum() / len(good)),
                "rps_min": float(rps.min()) if len(rps) else math.nan,
                "rps_max": float(rps.max()) if len(rps) else math.nan,
                "n_star_min": float(good[f"n_star_{name}"].min()) if len(good) else math.nan,
                "n_star_max": float(good[f"n_star_{name}"].max()) if len(good) else math.nan,
                "speed_over_rating_row_min": float(speed_over_rating_row(good, name).min()),
                "speed_over_rating_row_max": float(speed_over_rating_row(good, name).max()),
                "pr_min": float(pr.min()) if len(pr) else math.nan,
                "pr_max": float(pr.max()) if len(pr) else math.nan,
                "T_evap_C_min": float(t_evap.min()) if len(t_evap) else math.nan,
                "T_evap_C_max": float(t_evap.max()) if len(t_evap) else math.nan,
                "eta_overall_mean": _mean(good[f"eta_overall_{name}"]),
                "compressor_input_over_needed_mean": _mean(good[f"compressor_input_over_needed_{name}"]),
                "rows_indoor_airflow_below_published": int(
                    (
                        good[f"airflow_m3_s_{name}"]
                        < 0.999 * good[f"airflow_m3_s_{name}"].groupby(good.slug).transform("max")
                    ).sum()
                ),
                # What the bias of a row goes with.
                "bias_pr_correlation": float(relative.corr(pr)) if len(good) > 2 else math.nan,
                "bias_speed_correlation": float(relative.corr(rps)) if len(good) > 2 else math.nan,
                "bias_relative_speed_correlation": float(relative.corr(good[f"n_star_{name}"]))
                if len(good) > 2
                else math.nan,
                "bias_T_evap_correlation": float(relative.corr(t_evap)) if len(good) > 2 else math.nan,
                "bias_outdoor_temperature_correlation": float(relative.corr(good.t_source_C))
                if len(good) > 2
                else math.nan,
                "bias_indoor_temperature_correlation": float(relative.corr(good.t_sink_C))
                if len(good) > 2
                else math.nan,
                "bias_shr_correlation": float(relative.corr(good.shr_catalog)) if len(good) > 2 else math.nan,
                # Share of the variance of the rows' bias a least-squares fit explains: on the three axes of
                # the efficiency map (ln PR, speed, evaporating temperature), on the unit, on the unit and the
                # indoor condition, and on the unit and the three axes.
                "bias_variance_explained_by_map_axes": _explained(relative, axes),
                "bias_variance_explained_by_unit": _explained(relative, of_unit) if of_unit else math.nan,
                "bias_variance_explained_by_unit_and_indoor_condition": _explained(relative, [*of_unit, *indoor])
                if of_unit
                else math.nan,
                "bias_variance_explained_by_unit_and_map_axes": _explained(relative, [*of_unit, *axes])
                if of_unit
                else math.nan,
                "shr_abs_error_rows_with_ratio_below_one": _mean(
                    (solved[f"shr_net_{name}"] - solved.shr_catalog).abs()
                ),
            }
        )
    if heating is not None:
        heat_names = passes_of(heating)
        common = heating[np.logical_and.reduce([heating[f"usable_{name}"] for name in heat_names])]
        out["heating_rows"] = int(len(heating))
        out["heating_rows_scored_by_every_pass"] = int(len(common))
        for name in heat_names:
            scored, bias, mape = _bias(heating, name)
            out.update(
                {
                    f"scored_{name}": scored,
                    f"cop_bias_pct_{name}": bias,
                    f"cop_MAPE_pct_{name}": mape,
                    f"cop_bias_pct_common_{name}": _bias(common, name)[1],
                    f"cop_bias_pct_spread_of_units_{name}": _unit_spread(heating, name),
                }
            )
    return out


# ---------------------------------------------------------------------------
# What a run records
# ---------------------------------------------------------------------------


def script_identity() -> dict:
    """Git blob of the source this code was compiled from.

    The hash ``git hash-object`` gives a file with the bytes in
    :data:`_SOURCE`. Those are the bytes :func:`main` read, compiled and ran,
    so the blob is that of the source that produced the results by
    construction; the file on disk is not read here. ``SystemExit`` in a
    module the import system loaded: what it was compiled from is not known
    (it may come from a bytecode cache), so nothing is recorded for it.
    """
    if _SOURCE is None:
        raise SystemExit(
            "the study has to be run through main() (python -m validation.indoor_ua_wet_coil.simulate), which runs "
            "the bytes it records; this module was loaded by the import system. Nothing written"
        )
    header = b"blob %d\0" % len(_SOURCE)
    return {"study_script_blob": hashlib.sha1(header + _SOURCE).hexdigest()}  # noqa: S324 -- git's blob id


def run_state() -> dict:
    """What a run computes with: the model and harness state, and this script's own content.

    :func:`validation.parity.run.source_state` (commit, tree hashes, whether
    the model, the harness or the catalogues had uncommitted edits) covers
    ``validation.parity.run.SOURCE_PATHS``, which this script is not part of.
    Its own git blob is added (:func:`script_identity`), and whether that
    blob is the script of the recorded commit (``study_script_uncommitted``
    is true when it is not).
    """
    from validation.parity.run import _git, source_state

    state = source_state()
    identity = script_identity()
    committed = _git("rev-parse", f"{state['git_head']}:{SCRIPT}")
    return {**state, **identity, "study_script_uncommitted": identity["study_script_blob"] != committed}


def _json_ready(value):
    """``value`` with every number JSON has no word for (NaN, infinity) as ``None``."""
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, (float, np.floating)):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, np.integer):
        return int(value)
    return value


def cooling_point_ids(slug: str) -> list[int]:
    """Ids of the cooling rows of one catalogue."""
    from validation.parity.spec import load_all

    return [point.id for point in load_all(slug)[0].points if point.mode == "cooling"]


def heating_point_ids(slug: str) -> list[int]:
    """Ids of the heating rows of one catalogue."""
    from validation.parity.spec import load_all

    return [point.id for point in load_all(slug)[0].points if point.mode == "heating"]


def main(argv: list[str] | None = None) -> None:
    """Run the study with code compiled from one read of this file, whose bytes are what the record names.

    The module the interpreter loaded is not what runs the study: its source
    bytes are not known (the import system may have used a bytecode cache, or
    the file may have changed since). The file is read once here; those bytes
    are compiled and executed as a fresh copy of this script with
    :data:`_SOURCE` set to them, and that copy's :func:`run` does the work.
    """
    path = Path(__file__)
    source = path.read_bytes()
    script: dict = {"__name__": "validation.indoor_ua_wet_coil.simulate", "__file__": str(path), "_SOURCE": source}
    exec(compile(source, str(path), "exec", dont_inherit=True), script)  # noqa: S102 -- this script, run from the bytes read
    script["run"](argv)


def run(argv: list[str] | None = None) -> None:
    """The study: solve the coils, run the passes, check the gates, write the files and the record."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR, help="where to write the study's files")
    args = parser.parse_args(argv)
    out_dir: Path = args.out_dir.resolve()

    # Captured before any catalogue is read or evaluated (as the parity
    # harness does): the record describes the code and the catalogues the
    # numbers were computed with, not what is on disk when the run ends.
    state = run_state()

    coils = pd.concat([catalogue_coils(slug) for slug in UNITS], ignore_index=True)
    held_coils = pd.concat([catalogue_coils(slug) for slug in HELD_UNITS], ignore_index=True)
    units, held_units = ua_by_unit(coils), ua_by_unit(held_coils)
    if not units.UA_W_K.notna().all():
        raise SystemExit("an adopted unit has no cooling row whose conductance can be solved; nothing written")
    loo, for_held = leave_one_out(units), rules_for_held(units, held_units)
    as_default = default_conductances(units)

    points = pd.concat([run_cooling(slug, cooling_conductances(slug, units, loo)) for slug in UNITS], ignore_index=True)
    held = pd.concat(
        [run_cooling(slug, cooling_conductances(slug, held_units, for_held)) for slug in HELD_UNITS],
        ignore_index=True,
    )
    heating = pd.concat(
        [run_heating(slug, heating_conductances(slug, units, loo)) for slug in UNITS], ignore_index=True
    )
    scan = pd.concat([run_scan(slug) for slug in UNITS], ignore_index=True)
    defaults = pd.concat([run_defaults(slug, as_default) for slug in UNITS], ignore_index=True)

    for label, frame, slugs, ids in (
        ("adopted cooling", points, UNITS, cooling_point_ids),
        ("held cooling", held, HELD_UNITS, cooling_point_ids),
        ("adopted heating", heating, UNITS, heating_point_ids),
    ):
        expected = {slug: ids(slug) for slug in slugs}
        computed = {slug: [int(i) for i in rows.point_id] for slug, rows in frame.groupby("slug", sort=False)}
        if computed != expected:
            raise SystemExit(f"the {label} rows computed are not the rows of the catalogues; nothing written")
    for (rule, path, slug), rows in defaults.groupby(["rule", "path", "slug"], sort=False):
        ids = cooling_point_ids(slug) if DEFAULT_PATHS[path][0] == "cooling" else heating_point_ids(slug)
        if [int(i) for i in rows.point_id] != ids:
            raise SystemExit(f"the rows of {rule} / {path} / {slug} are not the rows of the catalogue; nothing written")
    for (path, scale, slug), rows in scan.groupby(["path", "scale", "slug"], sort=False):
        ids = cooling_point_ids(slug) if DEFAULT_PATHS[path][0] == "cooling" else heating_point_ids(slug)
        if [int(i) for i in rows.point_id] != ids:
            raise SystemExit(
                f"the rows of the scan at {path} / {scale:g} / {slug} are not the catalogue's; nothing written"
            )
    worst = max(parity_gate(points, "dry"), parity_gate(held, "dry"), parity_gate(heating, "heat"))
    print(f"shipped passes against the committed parity results: largest relative COP difference {worst:.3e}")
    if not worst <= 1e-6:
        raise SystemExit("GATE FAILED: the dry or the heating pass is not the shipped parity path; nothing written")
    if run_state() != state:
        raise SystemExit("the model, the harness or a catalogue changed while the study ran; nothing written")

    loo, for_held = with_model_results(loo, points, heating), with_model_results(for_held, held)
    tables = {
        UA_POINTS_NAME: coils,
        UA_UNITS_NAME: units,
        COOLING_NAME: points,
        BY_UNIT_NAME: bias_by_unit(points),
        BY_PR_NAME: bias_by_pressure_ratio(points),
        BY_SPEED_NAME: bias_by_speed(points),
        BY_TEVAP_NAME: bias_by_evaporating_temperature(points),
        BY_INDOOR_NAME: bias_by_indoor_condition(points),
        HEATING_NAME: heating,
        HEATING_UNITS_NAME: heating_by_unit(heating),
        RULES_NAME: loo,
        SCAN_NAME: scan,
        SCAN_SUMMARY_NAME: scan_summary(scan),
        DEFAULT_NAME: defaults,
        DEFAULT_SUMMARY_NAME: default_summary(defaults, points, heating),
        HELD_UA_POINTS_NAME: held_coils,
        HELD_UA_UNITS_NAME: held_units,
        HELD_COOLING_NAME: held,
        HELD_RULES_NAME: for_held,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, frame in tables.items():
        frame.to_csv(out_dir / name, index=False)
        written.append(out_dir / name)

    numbers, numbers_held = headline(units, points, heating), headline(held_units, held)
    manifest = {
        "units": list(UNITS),
        "held_units": list(HELD_UNITS),
        "cooling_rows": {
            slug: int(n) for slug, n in pd.concat([points, held]).groupby("slug", sort=False).size().items()
        },
        "heating_rows": {slug: int(n) for slug, n in heating.groupby("slug", sort=False).size().items()},
        "shr_rounding": SHR_ROUNDING,
        "ua_scales": UA_SCALES,
        "heating_scales": HEATING_SCALES,
        "scan_scales": list(SCAN_SCALES),
        "pressure_ratio_edges": list(PR_EDGES),
        "relative_speed_edges": list(RELATIVE_SPEED_EDGES),
        "rating_row_speed_edges": list(RATING_ROW_SPEED_EDGES),
        "speed_edges_rev_s": list(SPEED_EDGES),
        "evaporating_temperature_edges_C": list(T_EVAP_EDGES),
        "efficiency_map": efficiency_map_data(),
        "rules": list(RULES),
        "rules_fitted_on_the_adopted_units": {rule: fit_rule(rule, units) for rule in RULES},
        "rules_leave_one_out": rule_summary(loo),
        "rules_held": rule_summary(for_held),
        "shipped_passes_vs_committed_parity_max_rel_cop_difference": worst,
        "headline": numbers,
        "headline_held": numbers_held,
    }
    # Undefined statistics (a mean over no rows) are null: NaN is not a JSON number.
    (out_dir / MANIFEST_NAME).write_text(
        json.dumps(_json_ready(manifest), indent=1, allow_nan=False) + "\n", encoding="utf-8"
    )
    written.append(out_dir / MANIFEST_NAME)

    from validation import study_provenance

    study_provenance.write(
        out_dir / "provenance.json",
        SCRIPT,
        # The files this run wrote, not whatever else the folder holds.
        written,
        {
            "model": "validation.parity.run._build_model(catalog, overrides, mode): the shipped parity construction",
            "overrides": "UA_iu_rated of the pass (of the scale, in the scan); with it rps_rated of the shipped build, "
            "except in wet_ua_shr_rerated and the default passes (UA_iu_rated alone) and in dry, wet and heat (none)",
            "scan_scales": list(SCAN_SCALES),
            "RH_room": "from the row's indoor dry bulb and wet bulb, one standard atmosphere (wet passes); None (dry)",
            "request": "the published capacity, as the parity harness passes it",
            "ua_scales": UA_SCALES,
            "heating_scales": HEATING_SCALES,
            "rules": list(RULES),
            "units": list(UNITS),
            "held_units": list(HELD_UNITS),
        },
        "the indoor coil's conductance read from the catalogues' sensible heat ratio, what it does to the cooling "
        "and heating COP bias on the present compressor map, and what a rule for it would move as the default",
        command=COMMAND if out_dir == OUT_DIR.resolve() else f"{COMMAND} --out-dir {out_dir}",
        # The state of the start of the run (unchanged since, checked above), in place of
        # the one the record helper reads when it writes.
        extra=state,
    )

    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 40)
    for name in (
        UA_UNITS_NAME,
        BY_PR_NAME,
        BY_SPEED_NAME,
        BY_TEVAP_NAME,
        HEATING_UNITS_NAME,
        RULES_NAME,
        SCAN_SUMMARY_NAME,
    ):
        print(name)
        print(tables[name].to_string(index=False, float_format=lambda v: f"{v:.3f}"))
    print(json.dumps(_json_ready({"headline": numbers, "headline_held": numbers_held}), indent=1))
    print(f"wrote {len(written)} files and the record to {out_dir}")


if __name__ == "__main__":
    main()
