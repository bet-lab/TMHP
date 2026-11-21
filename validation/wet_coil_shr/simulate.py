"""Catalogue cooling points with the dry and the wet indoor coil: sensible heat ratio and COP bias.

The parity harness (``validation/parity/run.py``) runs every cooling row with
the dry indoor coil: the whole published capacity is sensible heat in the
model, although the manufacturer's row removes moisture as well. Each Daikin
cooling row records its indoor wet bulb and its sensible heat ratio in the
catalogue ``note`` (``"WB 19 C, SHR 0.74, wet coil"``), so the same rows can be
run with the room humidity the table was published at
(``AirSourceHeatPump.analyze_steady(RH_room=...)``, #205). This study does that
and asks two things:

1. **Does the wet coil reproduce the published sensible heat ratio?** Model
   against catalogue, row by row.
2. **What does it do to the COP bias?** By band of the published sensible
   heat ratio, dry against wet.

It writes its own files and touches nothing the harness ships
(``validation/results/<slug>.csv`` are the dry-coil results and stay so).

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

Everything else is the harness's: the model of each unit is built by
``validation.parity.run._build_model`` (nameplate, refrigerant, published
airflow and compressor type, the library defaults otherwise), the request is
the published total capacity, and a row is scored as there -- not where the
model falls more than 1 % short of the published duty, and not where it meets
it only as an on/off mean.

Boundaries of the comparison
----------------------------
- The published capacity is net of the indoor fan motor's heat (the data
  book's note), so the model's ratio is taken on the same boundary: net
  sensible heat (``Q_room_sensible [W]``) over net total heat (that plus
  ``Q_iu_latent [W]``). The coil's own ratio (``SHR_iu [-]``) is kept as
  ``shr_gross``.
- The catalogue prints two decimals of the ratio and whole degrees of wet bulb.
- ``T_adp_catalog_C`` is the coil temperature at which the model's own coil
  line (room state to saturated air at the coil) has the published ratio:
  what the evaporating temperature would have to be for the model to print
  that ratio at that room state. It involves no cycle solve. A straight coil
  line has a lowest ratio for a room state (:func:`apparatus_dew_point`); a
  published ratio below it has no such temperature (NaN).
- The two Fujitsu units (held, AHRI 210/240) record a sensible heat ratio but
  no indoor wet bulb in their catalogue files, so their 144 cooling rows
  cannot be run wet and are not in this study.

Run::

    PYTHONPATH=. uv run --extra integrations python -m validation.wet_coil_shr.simulate
"""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "validation" / "results" / "wet_coil_shr_once"
CSV_NAME = "wet_coil_shr_once.csv"
BANDS_NAME = "shr_bands.csv"
UNITS_NAME = "shr_by_unit.csv"

#: Catalogues whose cooling rows record the indoor wet bulb.
UNITS = ("daikin_rxm20a", "daikin_rxm25a", "daikin_rxm35a", "daikin_rxm42a", "daikin_rxm50a")
#: Catalogues with a sensible heat ratio but no indoor wet bulb on their cooling rows (not run).
UNITS_WITHOUT_WET_BULB = ("fujitsu_asuh09lpas", "fujitsu_asuh12lpas")
#: Passes: name -> (wet coil?, scale of the indoor coil's rated conductance).
PASSES: dict[str, tuple[bool, float]] = {
    "dry": (False, 1.0),
    "wet": (True, 1.0),
    "wet_ua50": (True, 0.5),
    "wet_ua200": (True, 2.0),
}
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


def run_point(catalog, point, model, name: str, rh: float | None) -> dict:
    """One catalogue row in one pass, scored as ``validation.parity.run.run_catalog`` scores it."""
    from validation.parity.run import CAPACITY_SHORT, CYCLING_MEAN, _capacity_short, _cycling_mean

    result = model.analyze_steady(
        Q_r_iu=point.q_kW * 1000.0,
        T0=point.t_source_C,
        T_a_room=point.t_sink_C,
        RH_room=rh,
        return_dict=True,
        verbose=False,
    )
    failure = result.get("failure_reason", "none")
    if failure == "none" and _capacity_short(catalog, point, result):
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
        f"condensate_kg_h_{name}": float(result.get("m_dot_iu_condensate [kg/s]", nan)) * 3600.0 if usable else nan,
        f"failure_{name}": failure,
        f"usable_{name}": usable,
    }


def run_unit(slug: str, passes: tuple[str, ...] = tuple(PASSES), point_ids: tuple[int, ...] | None = None):
    """All cooling rows of one catalogue (or ``point_ids`` of it) in ``passes``: one row per catalogue row."""
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


def _group_row(frame: pd.DataFrame, passes: tuple[str, ...]) -> dict:
    row: dict = {"points": int(len(frame)), "shr_catalog": float(frame.shr_catalog.mean())}
    for name in passes:
        scored, bias, mape = _bias(frame, name)
        row.update({f"scored_{name}": scored, f"cop_bias_pct_{name}": bias, f"cop_MAPE_pct_{name}": mape})
        if PASSES[name][0]:
            row.update(_shr_statistics(frame, name))
    return row


def by_band(points: pd.DataFrame, passes: tuple[str, ...] = tuple(PASSES)) -> pd.DataFrame:
    """COP bias and model sensible heat ratio by band of the published ratio, plus all rows."""
    rows = []
    for label, low, high in BANDS:
        frame = points[(points.shr_catalog >= low) & (points.shr_catalog < high)]
        rows.append({"shr_band": label, **_group_row(frame, passes)})
    rows.append({"shr_band": "all", **_group_row(points, passes)})
    return pd.DataFrame(rows)


def by_unit(points: pd.DataFrame, passes: tuple[str, ...] = tuple(PASSES)) -> pd.DataFrame:
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


def headline(points: pd.DataFrame) -> dict:
    """The numbers quoted in docs/source/validation/index.rst (*Cooling and latent load*)."""
    wet = points[points.usable_wet]
    both = points[points.usable_wet & points.usable_dry]
    error = wet.shr_net_wet - wet.shr_catalog
    # Rows whose published ratio the coil line gives at some coil temperature: a ratio below 1
    # (a published ratio of 1 is a dry coil, with no such temperature) and not below the line's minimum.
    reachable = wet[wet.T_adp_catalog_C.notna()]
    coil_gap = reachable.T_evap_C_wet - reachable.T_adp_catalog_C
    out = {
        "cooling_rows": int(len(points)),
        "rows_scored_dry": int(points.usable_dry.sum()),
        "rows_scored_wet": int(points.usable_wet.sum()),
        "rows_model_coil_dry_in_wet_pass": int((wet.q_latent_W_wet == 0.0).sum()),
        "shr_catalog_mean": float(wet.shr_catalog.mean()),
        "shr_model_net_mean": float(wet.shr_net_wet.mean()),
        "shr_model_gross_mean": float(wet.shr_gross_wet.mean()),
        "shr_error_mean": float(error.mean()),
        "shr_abs_error_mean": float(error.abs().mean()),
        "shr_error_min": float(error.min()),
        "shr_error_max": float(error.max()),
        "shr_correlation": float(wet.shr_net_wet.corr(wet.shr_catalog)),
        "rows_published_ratio_one": int((points.shr_catalog >= 1.0).sum()),
        "rows_published_ratio_below_coil_line_minimum": int(
            (points.T_adp_catalog_C.isna() & (points.shr_catalog < 1.0)).sum()
        ),
        "rows_with_catalogue_adp": int(len(reachable)),
        "model_coil_above_catalogue_adp_K_mean": float(coil_gap.mean()),
        "model_coil_above_catalogue_adp_K_min": float(coil_gap.min()),
        "model_coil_above_catalogue_adp_K_max": float(coil_gap.max()),
        "T_evap_rise_wet_minus_dry_K_mean": float((both.T_evap_C_wet - both.T_evap_C_dry).mean()),
        "power_change_wet_vs_dry_pct_mean": float(((both.power_kW_wet / both.power_kW_dry - 1.0) * 100.0).mean()),
        "bias_shr_correlation_dry": float(
            ((points.cop_dry - points.cop_target) / points.cop_target).corr(points.shr_catalog)
        ),
        "bias_shr_correlation_wet": float(
            ((points.cop_wet - points.cop_target) / points.cop_target).corr(points.shr_catalog)
        ),
    }
    for name in PASSES:
        scored, bias, mape = _bias(points, name)
        out[f"cop_bias_pct_{name}"] = bias
        out[f"cop_MAPE_pct_{name}"] = mape
        out[f"scored_{name}"] = scored
    return out


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR, help="where to write the study's files")
    args = parser.parse_args(argv)
    out_dir: Path = args.out_dir.resolve()

    points = pd.concat([run_unit(slug) for slug in UNITS], ignore_index=True)

    worst = shipped_dry_gate(points)
    print(f"dry pass against the committed parity results: largest relative COP difference {worst:.3e}")
    if not worst <= 1e-6:
        raise SystemExit("GATE FAILED: the dry pass is not the shipped parity path; nothing written")

    out_dir.mkdir(parents=True, exist_ok=True)
    bands, units, numbers = by_band(points), by_unit(points), headline(points)
    points.to_csv(out_dir / CSV_NAME, index=False)
    bands.to_csv(out_dir / BANDS_NAME, index=False)
    units.to_csv(out_dir / UNITS_NAME, index=False)
    manifest = {
        "units": list(UNITS),
        "units_not_run_no_indoor_wet_bulb": list(UNITS_WITHOUT_WET_BULB),
        "passes": {name: {"wet_coil": wet, "UA_iu_scale": scale} for name, (wet, scale) in PASSES.items()},
        "shr_bands": [list(band) for band in BANDS],
        "dry_pass_vs_committed_parity_max_rel_cop_difference": worst,
        "headline": numbers,
    }
    (out_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")

    from validation import study_provenance

    study_provenance.write(
        out_dir / "provenance.json",
        "validation/wet_coil_shr/simulate.py",
        sorted(p for p in out_dir.iterdir() if p.suffix in (".csv", ".json") and p.name != "provenance.json"),
        {
            "model": "validation.parity.run._build_model(catalog, None, 'cooling'): the shipped parity construction",
            "passes": manifest["passes"],
            "RH_room": "from the row's indoor dry bulb and wet bulb, one standard atmosphere (wet passes); None (dry)",
            "units": list(UNITS),
        },
        "sensible heat ratio of the wet indoor coil against the catalogue's, and the COP bias dry against wet",
        command="python -m validation.wet_coil_shr.simulate",
    )

    pd.set_option("display.width", 200)
    print(bands.to_string(index=False, float_format=lambda v: f"{v:.3f}"))
    print(units.to_string(index=False, float_format=lambda v: f"{v:.3f}"))
    print(json.dumps(numbers, indent=1))
    print(f"wrote {out_dir / CSV_NAME}")


if __name__ == "__main__":
    main()
