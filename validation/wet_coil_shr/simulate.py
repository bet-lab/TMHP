"""Catalogue cooling points with the dry and the wet indoor coil: sensible heat ratio and COP bias.

The parity harness (``validation/parity/run.py``) runs every cooling row with
the dry indoor coil: the whole published capacity is sensible heat in the
model, although the manufacturer's row removes moisture as well. Each
air-to-air cooling row records its indoor wet bulb and its sensible heat ratio
in the catalogue ``note`` (``"WB 19 C, SHR 0.74, wet coil"``), so the same rows
can be run with the room humidity the table was published at
(``AirSourceHeatPump.analyze_steady(RH_room=...)``, #205). This study does that
and asks two things:

1. **Does the wet coil reproduce the published sensible heat ratio?** Model
   against catalogue, row by row.
2. **What does it do to the COP bias?** By band of the published sensible
   heat ratio, dry against wet.

It writes its own files and touches nothing the harness ships
(``validation/results/<slug>.csv`` are the dry-coil results and stay so).

Units
-----
The five Daikin units (EN 14511, adopted) are the study: ``wet_coil_shr_once.csv``,
``shr_bands.csv``, ``shr_by_unit.csv`` and ``headline`` of the manifest. The
two Fujitsu units are held catalogues (AHRI 210/240; the manual does not say
what its input power includes, nor whether its capacities are net of the
indoor fan's heat), so they are run the same way and reported apart, in files
of their own: ``wet_coil_shr_held.csv``, ``shr_bands_held.csv``,
``shr_by_unit_held.csv`` and ``headline_held``. Nothing of theirs enters a
number of the adopted units.

Passes
------
``dry``      ``RH_room`` not given. The shipped path: its COP must equal the
             committed ``validation/results/<slug>.csv`` row for row; that
             gate runs before anything is written.
``wet``      ``RH_room`` from the row's indoor dry bulb and wet bulb, at one
             standard atmosphere.
``wet_ua50`` / ``wet_ua200``
             The wet pass with the indoor coil's rated conductance halved and
             doubled. A sensitivity, not a calibration: the default
             (``INDOOR_UA_PER_CAPACITY``, 0.16 W/K per W of nameplate) is a
             provisional rule that was not derived for a wet coil.

In these four everything else is the harness's: the model of each unit is
built by ``validation.parity.run._build_model`` (nameplate, refrigerant and
published airflow, the library defaults otherwise), the
request is the published total capacity, and a row is scored as there -- not
where the model falls more than 1 % short of the published duty, and not where
it meets it only as an on/off mean.

``dry_net`` / ``wet_net``
             The same two coils **with the net capacity matched**. The harness
             passes the published capacity as the request, which the model
             takes as the heat of the coil (``Q_ref_iu [W]``), before the
             indoor fan's heat; the room then receives that much less, and
             the catalogue's capacity is the net one. Here the request is
             raised until the net delivery (``Q_room_sensible [W]`` plus
             ``Q_iu_latent [W]``) is the published capacity
             (:data:`NET_MATCH_TOL`), and the 1 % shortfall test is applied to
             the net delivery. The COP is then the published capacity over
             the model's input. FTXM42A point 23: a request of 4 200 W
             delivers 4 177.7 W net at a COP of 4.237; matched, the request is
             4 222.3 W and the COP 4.229.

Boundaries of the comparison
----------------------------
- The published capacities are net of the indoor fan motor's heat (the Daikin
  data book's note), so the model's ratio is taken on the same boundary: net
  sensible heat (``Q_room_sensible [W]``) over net total heat (that plus
  ``Q_iu_latent [W]``). The coil's own ratio (``SHR_iu [-]``) is kept as
  ``shr_gross``.
- The catalogues print the sensible and the total capacity; the ratio in the
  catalogue files is their quotient rounded to two decimals. The Daikin table
  prints whole degrees of wet bulb, the Fujitsu table (SI block) one decimal.
- ``T_adp_catalog_C`` is the coil temperature at which the model's own coil
  line (room state to saturated air at the coil) has the published ratio:
  what the evaporating temperature would have to be for the model to print
  that ratio at that room state. **It takes the published ratio as the
  coil's own**, without the fan heat. ``T_adp_catalog_fan_C`` puts the
  published ratio on the coil's boundary first -- ``(SHC + E_fan) /
  (TC + E_fan)`` with the model's indoor fan power of the ``wet_net`` pass,
  the catalogues not printing one -- and is a few hundredths of a kelvin
  higher (FTXM42A point 23: 11.059 and 11.098 degC). Neither involves a cycle
  solve (nor the model's saturation limit on the leaving air, which none of
  the adopted rows reaches: ``rows_leaving_air_saturated`` of the headline).
  A straight coil line has a lowest ratio for a room state
  (:func:`apparatus_dew_point`); a published ratio below it has no such
  temperature (NaN).

Run (about a minute and a half)::

    PYTHONPATH=. uv run --extra integrations python -m validation.wet_coil_shr.simulate

``--out-dir`` writes the study somewhere else. Part of the study is run
through :func:`run_unit` (``point_ids``), as the tests do; the command line
runs all of it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path

import pandas as pd

#: The bytes this code was compiled from, when it runs through :func:`main`
#: (which reads the file once, compiles those bytes and runs the result with
#: this name set); ``None`` in a module the import system loaded, whose source
#: bytes are not known. The provenance record takes the script's blob from it.
_SOURCE: bytes | None = globals().get("_SOURCE")

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "validation" / "results" / "wet_coil_shr_once"
CSV_NAME = "wet_coil_shr_once.csv"
BANDS_NAME = "shr_bands.csv"
UNITS_NAME = "shr_by_unit.csv"
#: The same three files for the held units, reported apart.
HELD_CSV_NAME = "wet_coil_shr_held.csv"
HELD_BANDS_NAME = "shr_bands_held.csv"
HELD_UNITS_NAME = "shr_by_unit_held.csv"
MANIFEST_NAME = "run_manifest.json"
#: This script, relative to the repository root (the provenance record names it and its content).
SCRIPT = "validation/wet_coil_shr/simulate.py"

#: The adopted catalogues with cooling rows: the study.
UNITS = ("daikin_rxm20a", "daikin_rxm25a", "daikin_rxm35a", "daikin_rxm42a", "daikin_rxm50a")
#: Held catalogues with cooling rows (``status: hold``): run the same way, reported apart.
HELD_UNITS = ("fujitsu_asuh09lpas", "fujitsu_asuh12lpas")
#: Passes: name -> (wet coil?, scale of the indoor coil's rated conductance).
PASSES: dict[str, tuple[bool, float]] = {
    "dry": (False, 1.0),
    "wet": (True, 1.0),
    "wet_ua50": (True, 0.5),
    "wet_ua200": (True, 2.0),
}
#: Net-capacity-matched passes: name -> the pass whose coil they use.
NET_PASSES: dict[str, str] = {"dry_net": "dry", "wet_net": "wet"}
#: Relative difference between the net delivery and the published capacity at which the request stands:
#: 0.01 %, a fiftieth of what the catalogues' two decimals of a kilowatt leave open. The operating-point
#: search settles the indoor fan's power to a few hundredths of a watt (2e-5 of these capacities), so a
#: match much tighter than this would follow its noise from one request to the next without settling.
NET_MATCH_TOL = 1e-4
#: Requests tried to match the net capacity. The fan heat moves by about 1 % of a change of the request,
#: so the second request is usually the last; a row that does not get there is reported unmatched.
NET_MATCH_MAX_ITER = 6
#: Scoring reason of a net-matched pass: the net delivery did not reach the published capacity.
NET_UNMATCHED = "net_capacity_unmatched"
#: Bands of the published sensible heat ratio (the ones of docs/source/validation/index.rst).
BANDS: tuple[tuple[str, float, float], ...] = (
    ("below 0.75", 0.0, 0.75),
    ("0.75 to 0.95", 0.75, 0.95),
    ("0.95 and above", 0.95, 2.0),
)
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
    gets colder -- but not without end: the latent part is bounded by the
    water the air holds, the sensible part is not, so the ratio has a minimum
    (0.61 at a coil of -3 degC for room air at 27 degC dry bulb / 19 degC wet
    bulb) and rises again below it. The temperature returned is the one
    between the dew point and that minimum; a ratio of 1 or more, or one
    below the minimum, has none.
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


def _models(catalog) -> dict[str, object]:
    """One model per conductance scale, built as the parity harness builds the unit."""
    from validation.parity.run import _build_model

    reference = _build_model(catalog, None, "cooling")
    models = {1.0: reference}
    for _, scale in PASSES.values():
        if scale not in models:
            models[scale] = _build_model(catalog, {"UA_iu_rated": reference.UA_iu_rated * scale}, "cooling")
    return models


def _leaving_air_saturated(result: dict) -> bool:
    """Whether the coil's leaving air sits on the saturation curve (the model's limit on the coil line)."""
    from tmhp.defrost import saturation_humidity_ratio

    w_out = float(result.get("w_iu_a_out [kg/kg]", math.nan))
    if not math.isfinite(w_out):
        return False
    return w_out >= saturation_humidity_ratio(float(result["T_iu_a_mid [°C]"])) * (1.0 - 1e-9)


def run_point(catalog, point, model, name: str, rh: float | None) -> dict:
    """One catalogue row in one pass, scored as ``validation.parity.run.run_catalog`` scores it."""
    from validation.parity.run import CAPACITY_SHORT, CAPACITY_SHORT_TOL, CYCLING_MEAN, _cycling_mean, _result_number

    result = model.analyze_steady(
        Q_r_iu=point.q_kW * 1000.0,
        T0=point.t_source_C,
        T_a_room=point.t_sink_C,
        RH_room=rh,
        return_dict=True,
        verbose=False,
    )
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
    nan = math.nan
    net_sensible = float(result.get("Q_room_sensible [W]", nan))
    latent = float(result.get("Q_iu_latent [W]", nan))
    net_total = net_sensible + latent
    return {
        f"cop_{name}": cop if usable else nan,
        f"power_kW_{name}": float(result["E_tot [W]"]) / 1000.0 if usable else nan,
        f"T_evap_C_{name}": float(result.get("T_ref_evap_sat [°C]", nan)) if usable else nan,
        f"shr_gross_{name}": float(result.get("SHR_iu [-]", nan)) if usable else nan,
        f"shr_net_{name}": net_sensible / net_total if usable and net_total > 0.0 else nan,
        f"q_latent_W_{name}": latent if usable else nan,
        f"net_total_W_{name}": net_total if usable else nan,
        f"condensate_kg_h_{name}": float(result.get("m_dot_iu_condensate [kg/s]", nan)) * 3600.0 if usable else nan,
        f"leaving_air_saturated_{name}": _leaving_air_saturated(result) if usable else False,
        f"failure_{name}": failure,
        f"usable_{name}": usable,
    }


def run_point_net(point, model, name: str, rh: float | None) -> dict:
    """One catalogue row with the net delivery matched to the published capacity.

    The request (the coil's heat) is raised from the published capacity by
    what the net delivery -- ``Q_room_sensible [W]`` plus ``Q_iu_latent [W]``,
    the coil's heat less the indoor fan's -- is short of it, until the two
    agree to :data:`NET_MATCH_TOL`. A row is scored when the unit runs
    continuously there (not as an on/off mean) and the net delivery is within
    1 % of the published capacity, the harness's shortfall test moved to the
    net side.
    """
    from validation.parity.run import CAPACITY_SHORT, CAPACITY_SHORT_TOL, CYCLING_MEAN, _cycling_mean

    nan = math.nan
    target = point.q_kW * 1000.0
    request, net_total, result, failure = target, nan, {}, NET_UNMATCHED
    for _ in range(NET_MATCH_MAX_ITER):
        result = model.analyze_steady(
            Q_r_iu=request, T0=point.t_source_C, T_a_room=point.t_sink_C, RH_room=rh, return_dict=True, verbose=False
        )
        failure = result.get("failure_reason", "none")
        if failure != "none":
            break
        net_total = float(result["Q_room_sensible [W]"]) + float(result["Q_iu_latent [W]"])
        if _cycling_mean(result):
            failure = CYCLING_MEAN
            break
        if abs(net_total - target) <= NET_MATCH_TOL * target:
            break
        if result.get("capacity_clamped") == "max":
            # At its ceiling: a larger request delivers no more.
            failure = CAPACITY_SHORT if net_total < target * (1.0 - CAPACITY_SHORT_TOL) else NET_UNMATCHED
            break
        request += target - net_total
    else:
        failure = NET_UNMATCHED
    cop = float(result.get("cop_sys [-]", nan))
    usable = failure == "none" and math.isfinite(cop) and cop > 0.0
    net_sensible = float(result.get("Q_room_sensible [W]", nan))
    return {
        f"cop_{name}": cop if usable else nan,
        f"power_kW_{name}": float(result["E_tot [W]"]) / 1000.0 if usable else nan,
        f"T_evap_C_{name}": float(result.get("T_ref_evap_sat [°C]", nan)) if usable else nan,
        f"shr_net_{name}": net_sensible / net_total if usable and net_total > 0.0 else nan,
        f"q_request_W_{name}": request if usable else nan,
        f"net_total_W_{name}": net_total if usable else nan,
        f"fan_W_{name}": float(result.get("E_iu_fan [W]", nan)) if usable else nan,
        f"failure_{name}": failure,
        f"usable_{name}": usable,
    }


def cooling_point_ids(slug: str) -> list[int]:
    """Ids of the cooling rows of one catalogue: the rows this study runs."""
    from validation.parity.spec import load_all

    return [point.id for point in load_all(slug)[0].points if point.mode == "cooling"]


def catalogue_ratio_on_the_coil(shr: float, q_W: float, fan_W: float) -> float:
    """A published (net) sensible heat ratio on the coil's boundary: the fan heat added to both capacities."""
    return (shr * q_W + fan_W) / (q_W + fan_W)


def run_unit(
    slug: str,
    passes: tuple[str, ...] = tuple(PASSES),
    point_ids: tuple[int, ...] | None = None,
    net_passes: tuple[str, ...] = tuple(NET_PASSES),
):
    """All cooling rows of one catalogue (or ``point_ids`` of it): one row per catalogue row.

    ``passes`` are run with the published capacity as the request,
    ``net_passes`` with the net delivery matched to it.
    """
    from validation.parity.spec import load_all

    catalog = load_all(slug)[0]
    models = _models(catalog)
    rows = []
    for point in catalog.points:
        if point.mode != "cooling" or (point_ids is not None and point.id not in point_ids):
            continue
        parsed = parse_note(point.note)
        if parsed is None:
            raise SystemExit(f"{slug} point {point.id}: no indoor wet bulb and sensible heat ratio in {point.note!r}")
        wet_bulb, shr_catalog = parsed
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
            "rh_room": rh,
            "q_kW": point.q_kW,
            "cop_target": point.target_cop(),
            "shr_catalog": shr_catalog,
            "T_adp_catalog_C": apparatus_dew_point(point.t_sink_C, rh, shr_catalog),
        }
        for name in passes:
            wet, scale = PASSES[name]
            row.update(run_point(catalog, point, models[scale], name, rh if wet else None))
        for name in net_passes:
            wet, scale = PASSES[NET_PASSES[name]]
            row.update(run_point_net(point, models[scale], name, rh if wet else None))
        if "wet_net" in net_passes:
            # The published ratio on the coil's boundary, with the model's fan heat at the published net capacity.
            fan_W = row["fan_W_wet_net"]
            row["T_adp_catalog_fan_C"] = (
                apparatus_dew_point(
                    point.t_sink_C, rh, catalogue_ratio_on_the_coil(shr_catalog, point.q_kW * 1000.0, fan_W)
                )
                if math.isfinite(fan_W)
                else math.nan
            )
        rows.append(row)
    return pd.DataFrame(rows)


def _bias(frame: pd.DataFrame, name: str) -> tuple[int, float, float]:
    """(rows scored, mean COP bias [%], mean absolute COP error [%]) of one pass."""
    good = frame[frame[f"usable_{name}"]]
    if not len(good):
        return 0, math.nan, math.nan
    relative = (good[f"cop_{name}"] - good.cop_target) / good.cop_target * 100.0
    return int(len(good)), float(relative.mean()), float(relative.abs().mean())


def _shr_statistics(frame: pd.DataFrame, name: str) -> dict:
    good = frame[frame[f"usable_{name}"]]
    error = good[f"shr_net_{name}"] - good.shr_catalog
    return {
        f"shr_model_{name}": float(good[f"shr_net_{name}"].mean()) if len(good) else math.nan,
        f"shr_error_{name}": float(error.mean()) if len(good) else math.nan,
        f"shr_abs_error_{name}": float(error.abs().mean()) if len(good) else math.nan,
    }


def _wet(name: str) -> bool:
    """Whether a pass (net-matched or not) runs the wet coil."""
    return PASSES[NET_PASSES.get(name, name)][0]


def _passes_of(points: pd.DataFrame) -> tuple[str, ...]:
    """The passes a result frame holds, in the order of :data:`PASSES` then :data:`NET_PASSES`."""
    return tuple(name for name in (*PASSES, *NET_PASSES) if f"usable_{name}" in points.columns)


def _group_row(frame: pd.DataFrame, passes: tuple[str, ...]) -> dict:
    row: dict = {"points": int(len(frame)), "shr_catalog": float(frame.shr_catalog.mean())}
    for name in passes:
        scored, bias, mape = _bias(frame, name)
        row.update({f"scored_{name}": scored, f"cop_bias_pct_{name}": bias, f"cop_MAPE_pct_{name}": mape})
        if _wet(name):
            row.update(_shr_statistics(frame, name))
    return row


def by_band(points: pd.DataFrame, passes: tuple[str, ...] | None = None) -> pd.DataFrame:
    """COP bias and model sensible heat ratio by band of the published ratio, plus all rows."""
    passes = _passes_of(points) if passes is None else passes
    rows = []
    for label, low, high in BANDS:
        frame = points[(points.shr_catalog >= low) & (points.shr_catalog < high)]
        rows.append({"shr_band": label, **_group_row(frame, passes)})
    rows.append({"shr_band": "all", **_group_row(points, passes)})
    return pd.DataFrame(rows)


def by_unit(points: pd.DataFrame, passes: tuple[str, ...] | None = None) -> pd.DataFrame:
    passes = _passes_of(points) if passes is None else passes
    rows = [
        {"slug": slug, "unit": frame.unit.iat[0], **_group_row(frame, passes)}
        for slug, frame in points.groupby("slug", sort=False)
    ]
    return pd.DataFrame(rows)


def shipped_dry_gate(points: pd.DataFrame, results_dir: Path = REPO_ROOT / "validation" / "results") -> float:
    """Largest relative difference between the dry pass and the committed parity results (COP, row for row)."""
    worst = 0.0
    for slug, frame in points.groupby("slug", sort=False):
        shipped = pd.read_csv(results_dir / f"{slug}.csv", float_precision="round_trip").set_index("point_id")
        for row in frame.itertuples():
            committed = shipped.at[row.point_id, "cop_pred"]
            if pd.isna(committed) or pd.isna(row.cop_dry):
                if not (pd.isna(committed) and pd.isna(row.cop_dry)):
                    return math.inf
                continue
            worst = max(worst, abs(row.cop_dry - committed) / committed)
    return worst


def _mean(series: pd.Series) -> float:
    return float(series.mean()) if len(series) else math.nan


def _correlation(a: pd.Series, b: pd.Series) -> float:
    """Pearson correlation of two columns over the rows where both are numbers; NaN where it is not defined."""
    both = pd.concat([a, b], axis=1).dropna()
    if len(both) < 2 or both.iloc[:, 0].nunique() < 2 or both.iloc[:, 1].nunique() < 2:
        return math.nan  # fewer than two rows, or a column that does not vary
    return float(both.iloc[:, 0].corr(both.iloc[:, 1]))


def headline(points: pd.DataFrame) -> dict:
    """The numbers quoted in docs/source/validation/index.rst (*Cooling and latent load*).

    Of the adopted units, or of the held ones: whichever rows ``points``
    holds. A statistic with no row to take it from is NaN (``null`` in the
    manifest).
    """
    wet = points[points.usable_wet]
    both = points[points.usable_wet & points.usable_dry]
    error = wet.shr_net_wet - wet.shr_catalog
    # Rows whose published ratio the coil line gives at some coil temperature: a ratio below 1
    # (a published ratio of 1 is a dry coil, with no such temperature) and not below the line's minimum.
    reachable = wet[wet.T_adp_catalog_C.notna()]
    coil_gap = reachable.T_evap_C_wet - reachable.T_adp_catalog_C
    near_dry = wet[wet.shr_catalog >= 0.95]
    target_W = wet.q_kW * 1000.0
    net_short = target_W - wet.net_total_W_wet
    out = {
        "cooling_rows": int(len(points)),
        "rows_scored_dry": int(points.usable_dry.sum()),
        "rows_scored_wet": int(points.usable_wet.sum()),
        "rows_model_coil_dry_in_wet_pass": int((wet.q_latent_W_wet == 0.0).sum()),
        "rows_leaving_air_saturated": int(wet.leaving_air_saturated_wet.sum()),
        "shr_catalog_mean": _mean(wet.shr_catalog),
        "shr_model_net_mean": _mean(wet.shr_net_wet),
        "shr_model_gross_mean": _mean(wet.shr_gross_wet),
        "shr_error_mean": _mean(error),
        "shr_abs_error_mean": _mean(error.abs()),
        "shr_error_min": float(error.min()) if len(error) else math.nan,
        "shr_error_max": float(error.max()) if len(error) else math.nan,
        "shr_correlation": _correlation(wet.shr_net_wet, wet.shr_catalog),
        "rows_published_ratio_one": int((points.shr_catalog >= 1.0).sum()),
        "rows_published_ratio_at_least_095": int(len(near_dry)),
        "rows_published_ratio_at_least_095_with_latent_heat": int((near_dry.q_latent_W_wet > 0.0).sum()),
        "rows_published_ratio_below_coil_line_minimum": int(
            (points.T_adp_catalog_C.isna() & (points.shr_catalog < 1.0)).sum()
        ),
        "rows_with_catalogue_adp": int(len(reachable)),
        "model_coil_above_catalogue_adp_K_mean": _mean(coil_gap),
        "model_coil_above_catalogue_adp_K_min": float(coil_gap.min()) if len(coil_gap) else math.nan,
        "model_coil_above_catalogue_adp_K_max": float(coil_gap.max()) if len(coil_gap) else math.nan,
        "T_evap_rise_wet_minus_dry_K_mean": _mean(both.T_evap_C_wet - both.T_evap_C_dry),
        "power_change_wet_vs_dry_pct_mean": _mean((both.power_kW_wet / both.power_kW_dry - 1.0) * 100.0),
        "rows_wet_draws_more_than_dry": int((both.power_kW_wet > both.power_kW_dry).sum()),
        "bias_shr_correlation_dry": _correlation(
            (points.cop_dry - points.cop_target) / points.cop_target, points.shr_catalog
        ),
        "bias_shr_correlation_wet": _correlation(
            (points.cop_wet - points.cop_target) / points.cop_target, points.shr_catalog
        ),
        # The wet pass against the net capacity it was not matched to.
        "net_delivery_short_of_published_W_mean_wet": _mean(net_short),
        "net_delivery_short_of_published_pct_mean_wet": _mean(net_short / target_W * 100.0),
        "rows_net_delivery_short_by_more_than_1pct_wet": int((net_short > 0.01 * target_W).sum()),
    }
    for name in _passes_of(points):
        scored, bias, mape = _bias(points, name)
        out[f"cop_bias_pct_{name}"] = bias
        out[f"cop_MAPE_pct_{name}"] = mape
        out[f"scored_{name}"] = scored
    if "usable_wet_net" in points.columns:
        matched = points[points.usable_wet_net]
        error_net = matched.shr_net_wet_net - matched.shr_catalog
        on_coil = matched[matched.T_adp_catalog_fan_C.notna()]
        gap_fan = on_coil.T_evap_C_wet_net - on_coil.T_adp_catalog_fan_C
        both_net = points[points.usable_wet_net & points.usable_wet]
        out.update(
            {
                "request_above_published_W_mean_wet_net": _mean(matched.q_request_W_wet_net - matched.q_kW * 1000.0),
                "shr_model_net_mean_wet_net": _mean(matched.shr_net_wet_net),
                "shr_error_mean_wet_net": _mean(error_net),
                "shr_abs_error_mean_wet_net": _mean(error_net.abs()),
                "rows_with_catalogue_adp_fan": int(len(on_coil)),
                "model_coil_above_catalogue_adp_fan_K_mean_wet_net": _mean(gap_fan),
                "catalogue_adp_fan_minus_adp_K_mean": _mean(on_coil.T_adp_catalog_fan_C - on_coil.T_adp_catalog_C),
                "cop_change_net_matched_vs_request_pct_mean_wet": _mean(
                    (both_net.cop_wet_net / both_net.cop_wet - 1.0) * 100.0
                ),
            }
        )
    return out


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
            "the study has to be run through main() (python -m validation.wet_coil_shr.simulate), which runs "
            "the bytes it records; this module was loaded by the import system. Nothing written"
        )
    header = b"blob %d\0" % len(_SOURCE)
    return {"study_script_blob": hashlib.sha1(header + _SOURCE).hexdigest()}  # noqa: S324 -- git's blob id


def run_state() -> dict:
    """What a run computes with: the model and harness state, and this script's own content.

    :func:`validation.parity.run.source_state` (commit, tree hashes, whether
    the model, the harness or the catalogues had uncommitted edits) covers
    ``validation.parity.run.SOURCE_PATHS``, which this script is not part of.
    Its own git blob is added (:func:`script_identity`: the source of the
    running code), and whether that blob is the script of the recorded commit
    (``study_script_uncommitted`` is true when it is not), so that the record
    vouches for the script that made the files as well.
    """
    from validation.parity.run import _git, source_state

    state = source_state()
    identity = script_identity()
    committed = _git("rev-parse", f"{state['git_head']}:{SCRIPT}")
    return {**state, **identity, "study_script_uncommitted": identity["study_script_blob"] != committed}


#: How the committed study is made.
COMMAND = "python -m validation.wet_coil_shr.simulate"


def _json_ready(value):
    """``value`` with every number JSON has no word for (NaN, infinity) as ``None``."""
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def main(argv: list[str] | None = None) -> None:
    """Run the study with code compiled from one read of this file, whose bytes are what the record names.

    The module the interpreter loaded is not what runs the study: its source
    bytes are not known (the import system may have used a bytecode cache, or
    the file may have changed since). The file is read once here; those bytes
    are compiled and executed as a fresh copy of this script with
    :data:`_SOURCE` set to them, and that copy's :func:`run` does the work.
    The blob in the provenance record is therefore the blob of exactly the
    source that computed the results.
    """
    path = Path(__file__)
    source = path.read_bytes()
    script: dict = {"__name__": "validation.wet_coil_shr.simulate", "__file__": str(path), "_SOURCE": source}
    exec(compile(source, str(path), "exec", dont_inherit=True), script)  # noqa: S102 -- this script, run from the bytes read
    script["run"](argv)


def run(argv: list[str] | None = None) -> None:
    """The study: run the passes on every unit, check the gate, write the files and the record."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR, help="where to write the study's files")
    args = parser.parse_args(argv)
    out_dir: Path = args.out_dir.resolve()

    # Captured before any catalogue is read or evaluated (as the parity
    # harness does): the record describes the code and the catalogues the
    # numbers were computed with, not what is on disk when the run ends.
    state = run_state()

    points = pd.concat([run_unit(slug) for slug in UNITS], ignore_index=True)
    held = pd.concat([run_unit(slug) for slug in HELD_UNITS], ignore_index=True)

    for label, frame, slugs in (("adopted", points, UNITS), ("held", held, HELD_UNITS)):
        expected = {slug: cooling_point_ids(slug) for slug in slugs}
        computed = {slug: [int(i) for i in rows.point_id] for slug, rows in frame.groupby("slug", sort=False)}
        if computed != expected:
            raise SystemExit(f"the {label} rows computed are not the cooling rows of the catalogues; nothing written")
    worst = max(shipped_dry_gate(points), shipped_dry_gate(held))
    print(f"dry pass against the committed parity results: largest relative COP difference {worst:.3e}")
    if not worst <= 1e-6:
        raise SystemExit("GATE FAILED: the dry pass is not the shipped parity path; nothing written")
    if run_state() != state:
        raise SystemExit("the model, the harness or a catalogue changed while the study ran; nothing written")

    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    def write_csv(frame: pd.DataFrame, name: str) -> None:
        frame.to_csv(out_dir / name, index=False)
        written.append(out_dir / name)

    tables = {
        CSV_NAME: points,
        BANDS_NAME: by_band(points),
        UNITS_NAME: by_unit(points),
        HELD_CSV_NAME: held,
        HELD_BANDS_NAME: by_band(held),
        HELD_UNITS_NAME: by_unit(held),
    }
    for name, frame in tables.items():
        write_csv(frame, name)
    numbers, numbers_held = headline(points), headline(held)
    manifest = {
        "units": list(UNITS),
        "held_units": list(HELD_UNITS),
        "cooling_rows": {
            slug: int(n) for slug, n in pd.concat([points, held]).groupby("slug", sort=False).size().items()
        },
        "passes": {name: {"wet_coil": wet, "UA_iu_scale": scale} for name, (wet, scale) in PASSES.items()},
        "net_matched_passes": {
            name: {"coil_of": base, "net_match_rel_tol": NET_MATCH_TOL} for name, base in NET_PASSES.items()
        },
        "shr_bands": [list(band) for band in BANDS],
        "dry_pass_vs_committed_parity_max_rel_cop_difference": worst,
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
            "model": "validation.parity.run._build_model(catalog, None, 'cooling'): the shipped parity construction",
            "passes": manifest["passes"],
            "net_matched_passes": manifest["net_matched_passes"],
            "RH_room": "from the row's indoor dry bulb and wet bulb, one standard atmosphere (wet passes); None (dry)",
            "units": list(UNITS),
            "held_units": list(HELD_UNITS),
        },
        "sensible heat ratio of the wet indoor coil against the catalogue's, and the COP bias dry against wet",
        command=COMMAND if out_dir == OUT_DIR.resolve() else f"{COMMAND} --out-dir {out_dir}",
        # The state of the start of the run (unchanged since, checked above), in place of
        # the one the record helper reads when it writes.
        extra=state,
    )

    pd.set_option("display.width", 200)
    for name in (BANDS_NAME, UNITS_NAME, HELD_BANDS_NAME, HELD_UNITS_NAME):
        print(name)
        print(tables[name].to_string(index=False, float_format=lambda v: f"{v:.3f}"))
    print(json.dumps(_json_ready({"headline": numbers, "headline_held": numbers_held}), indent=1))
    print(f"wrote {out_dir / CSV_NAME} and {len(written)} other file(s)")


if __name__ == "__main__":
    main()
