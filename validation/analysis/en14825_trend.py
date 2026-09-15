r"""Does the default compressor coefficient set produce an EN 14825 trend?

The part-load question has a certified answer. EN 14825 lowers the required
duty and the flow temperature together across four test points, Heat Pump
Keymark publishes the measured COP at each of them, and
``validation.extraction.keymark_declared`` has already read 18,106 of those
certificates off the register. So the modelled trajectory can be scored rather
than eyeballed.

Three criteria, each a property of the certified population rather than a
threshold chosen here:

``C1 shape``
    COP rises at every step from A to D. The certified fraction that does so is
    computed below from the declared rows, not quoted.
``C2 level``
    Every modelled point falls inside the certified p10-p90 at its own test
    point. A model can have the right shape at the wrong level.
``C3 gradient``
    The ratio ``COP(D) / COP(A)`` -- how much the machine gains between the
    coldest and the mildest point -- falls inside the certified p10-p90 of the
    same ratio. This is the criterion that a flat or an exaggerated trend
    fails while still passing C1 and C2.

The ablation is the point of the module
---------------------------------------
Passing on its own proves little if every coefficient set passes. Four
compressor descriptions are therefore run through the identical trajectory:

``ideal``
    All three efficiencies pinned at 1.0 -- what TMHP's ASHP did before this
    work.
``constant``
    The three correlations frozen at their rated-point values, so the machine
    has realistic losses but no speed dependence at all.
``legacy-v1``
    The correlations TMHP shipped before the standalone-compressor refit
    (Guth shape, Cuevas leakage term, ``0.90 - 0.02 PR``), frozen under
    ``validation/coefficients/v1-legacy/``.
``defaults``
    What TMHP ships now (``compressor_efficiency.COEFFICIENT_VERSION``).

Run
---
``uv run python -m validation.analysis.en14825_trend``
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from tmhp import AirSourceHeatPumpBoiler
from tmhp.compressor_efficiency import ETA_EM_REF, eta_isen_default, make_eta_vol
from tmhp.compressor_speed import RATED_POINT_AIR_TO_WATER

# The pre-refit correlations (Guth shape, Cuevas leakage term, 0.90 - 0.02 PR) are frozen
# verbatim under validation/coefficients/v1-legacy/ so the ablation can still run them.
_LEGACY_PATH = Path(__file__).resolve().parents[1] / "coefficients" / "v1-legacy" / "legacy_forms.py"


def _load_legacy():
    import importlib.util

    spec = importlib.util.spec_from_file_location("tmhp_legacy_v1_forms", _LEGACY_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


REPO_ROOT = Path(__file__).resolve().parents[2]
DATA = REPO_ROOT / "validation" / "data"
DECLARED_CSV = DATA / "keymark_en14825_declared.csv"
OUT_POINTS = DATA / "en14825_trend_points.csv"
OUT_VERDICT = DATA / "en14825_trend_verdict.csv"
OUT_PARITY = DATA / "coefficient_ablation_parity.csv"

#: EN 14825 average climate. Part load is fixed by the standard's own bin
#: rule, ``(T_j - 16) / (T_design - 16)`` with ``T_design = -10 degC``, so
#: these four fractions are not a modelling choice.
EN14825_POINTS = {
    "low": (
        ("A", -7.0, 34.0, 0.88),
        ("B", 2.0, 30.0, 0.54),
        ("C", 7.0, 27.0, 0.35),
        ("D", 12.0, 24.0, 0.15),
    ),
    "medium": (
        ("A", -7.0, 52.0, 0.88),
        ("B", 2.0, 42.0, 0.54),
        ("C", 7.0, 36.0, 0.35),
        ("D", 12.0, 30.0, 0.15),
    ),
}

#: Temperature drop from the declared leaving-water temperature to the bulk
#: tank node the model is asked to hold.
SINK_OFFSET_K = 2.5

#: A machine is sized above the design load it is certified against; the ratio
#: is a property of the installation, not of the model, so it is swept rather
#: than fixed. 0.55-0.75 spans ordinary residential sizing practice.
OVERSIZING = (0.55, 0.667, 0.75)

#: Sizes and working fluids the trajectory is run for. The criteria have to
#: hold across the set, not at one lucky configuration.
CONFIGURATIONS = (
    (9000.0, "R32"),
    (9000.0, "R410A"),
    (9000.0, "R290"),
    (12000.0, "R32"),
    (16000.0, "R32"),
)


# ---------------------------------------------------------------------------
# Certified reference
# ---------------------------------------------------------------------------
def certified_reference(application: str) -> pd.DataFrame:
    """Per-point percentiles and the A->D gradient, from the declared rows."""
    df = pd.read_csv(DECLARED_CSV)
    df = df[df.application == application].dropna(subset=["cop_A", "cop_B", "cop_C", "cop_D"])
    rows = []
    for label in ("A", "B", "C", "D"):
        cop = df[f"cop_{label}"]
        rows.append(
            {
                "application": application,
                "point": label,
                "n": int(cop.size),
                "p10": float(cop.quantile(0.10)),
                "p50": float(cop.quantile(0.50)),
                "p90": float(cop.quantile(0.90)),
            }
        )
    gradient = df.cop_D / df.cop_A
    rows.append(
        {
            "application": application,
            "point": "D/A",
            "n": int(gradient.size),
            "p10": float(gradient.quantile(0.10)),
            "p50": float(gradient.quantile(0.50)),
            "p90": float(gradient.quantile(0.90)),
        }
    )
    return pd.DataFrame(rows)


def certified_monotonic_fraction(application: str) -> tuple[float, int]:
    """Fraction of certified machines whose declared COP rises at every step."""
    df = pd.read_csv(DECLARED_CSV)
    df = df[df.application == application].dropna(subset=["cop_A", "cop_B", "cop_C", "cop_D"])
    rising = (df.cop_B > df.cop_A) & (df.cop_C > df.cop_B) & (df.cop_D > df.cop_C)
    return float(rising.mean()), int(rising.size)


def percentile_of(value: float, series: pd.Series) -> float:
    return float((series < value).mean() * 100.0)


# ---------------------------------------------------------------------------
# Compressor descriptions under test
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Variant:
    key: str
    label: str
    note: str
    kwargs: dict


def _rated_constants() -> dict:
    """The three correlations evaluated once, at the air-to-water rated point."""
    rated = RATED_POINT_AIR_TO_WATER
    # A representative lift for A7/W35; the frozen variant is meant to be the
    # same machine without speed dependence, not a different machine.
    pressure_ratio = 2.7
    return {
        "eta_cmp_isen": eta_isen_default(pressure_ratio),
        "eta_cmp_vol": make_eta_vol(rated.rps)(pressure_ratio, rated.rps),
        "eta_cmp": ETA_EM_REF,
    }


def variants() -> tuple[Variant, ...]:
    frozen = _rated_constants()
    legacy = _load_legacy()
    return (
        Variant(
            "ideal",
            "ideal compressor",
            "eta_isen = eta_vol = eta_em = 1.0 (TMHP's ASHP before this work)",
            {"eta_cmp_isen": 1.0, "eta_cmp_vol": 1.0, "eta_cmp": 1.0},
        ),
        Variant(
            "constant",
            "frozen at rated point",
            f"eta_isen {frozen['eta_cmp_isen']:.3f}, eta_vol {frozen['eta_cmp_vol']:.3f}, "
            f"eta_em {frozen['eta_cmp']:.2f}; no speed dependence",
            dict(frozen),
        ),
        Variant(
            "legacy-v1",
            "pre-refit defaults (v1)",
            "0.90 - 0.02 PR; 1 - 0.020(PR-1) - 3.5(1/rps - 1/50); 0.80 x Guth shape in relative speed",
            {
                "eta_cmp_isen": legacy.eta_isen_default,
                "eta_cmp_vol": legacy.eta_vol_default,
                "eta_cmp": legacy.make_eta_em(RATED_POINT_AIR_TO_WATER.rps),
            },
        ),
        Variant(
            "defaults",
            "TMHP defaults",
            "eta_isen_default, make_eta_vol(rps_rated), make_eta_em(rps_rated) -- compressor_efficiency COEFFICIENT_VERSION",
            {},
        ),
    )


# ---------------------------------------------------------------------------
# Trajectory
# ---------------------------------------------------------------------------
def trajectory(
    variant: Variant, capacity_w: float, refrigerant: str, application: str, oversizing: float
) -> list[dict]:
    model = AirSourceHeatPumpBoiler(hp_capacity=capacity_w, ref=refrigerant, **variant.kwargs)
    design_load = capacity_w * oversizing
    rows = []
    for label, t_outdoor, lwt, load_ratio in EN14825_POINTS[application]:
        result = model.analyze_steady(
            T_tank_w=lwt - SINK_OFFSET_K,
            T0=t_outdoor,
            Q_ref_tank=design_load * load_ratio,
            return_dict=True,
        )
        assert isinstance(result, dict)
        ok = result.get("failure_reason", "none") == "none"
        rows.append(
            {
                "variant": variant.key,
                "application": application,
                "capacity_kW": capacity_w / 1000.0,
                "refrigerant": refrigerant,
                "oversizing": oversizing,
                "point": label,
                "plr": load_ratio,
                "t_outdoor_C": t_outdoor,
                "lwt_C": lwt,
                "rps": float(result["cmp_rpm [rpm]"]) / 60.0,
                "clamped": result.get("capacity_clamped") is not None,
                "cop": float(result["cop_sys [-]"]) if ok else float("nan"),
                "failure_reason": result.get("failure_reason", "none"),
            }
        )
    return rows


def score(points: pd.DataFrame, reference: pd.DataFrame) -> dict:
    """C1/C2/C3 for one (variant, configuration, application, oversizing)."""
    ordered = points.set_index("point").loc[["A", "B", "C", "D"]]
    cop = ordered.cop
    ref = reference.set_index("point")

    c1 = bool(cop.is_monotonic_increasing and cop.notna().all())
    inside = [bool(ref.p10[p] <= cop[p] <= ref.p90[p]) for p in ("A", "B", "C", "D")]
    c2 = all(inside)
    gradient = float(cop["D"] / cop["A"]) if cop["A"] > 0 else float("nan")
    c3 = bool(ref.p10["D/A"] <= gradient <= ref.p90["D/A"])

    return {
        "C1_shape": c1,
        "C2_level": c2,
        "C3_gradient": c3,
        "points_inside": sum(inside),
        "gradient_DA": gradient,
        "verdict": "OK" if (c1 and c2 and c3) else "NG",
    }


def parity_under(variant: Variant) -> dict:
    """Score the whole catalogue set with one compressor description.

    The EN 14825 criteria test the *shape* of the part-load curve against a
    certified population. They cannot say whether a coefficient set is right at
    a given machine, because the population is thousands of machines wide. The
    catalogue parity set answers that half, so both are reported together and
    a coefficient set has to survive both.
    """
    from validation.parity.run import run_catalog
    from validation.parity.spec import load_all

    frames = [run_catalog(catalog, variant.kwargs) for catalog in load_all()]
    df = pd.concat(frames, ignore_index=True)
    usable = df[df.usable]
    water = usable[usable.model_class == "ASHPB"]
    return {
        "variant": variant.key,
        "points": int(len(usable)),
        "MAPE_all_pct": float(usable.abs_pct_error.mean()),
        "MAPE_air_to_water_pct": float(water.abs_pct_error.mean()),
        "bias_air_to_water_pct": float(((water.cop_pred - water.cop_target) / water.cop_target).mean() * 100.0),
        "within10_pct": float((usable.abs_pct_error <= 10.0).mean() * 100.0),
    }


def main() -> None:
    all_points: list[dict] = []
    verdicts: list[dict] = []
    declared = pd.read_csv(DECLARED_CSV)

    for application in ("low", "medium"):
        reference = certified_reference(application)
        fraction, n = certified_monotonic_fraction(application)
        print(f"\n=== EN 14825 average climate, {application}-temperature application ===")
        print(f"  certified reference: {n:,} records, {fraction * 100:.1f} % rise monotonically A->D")
        print(reference.to_string(index=False, float_format=lambda v: f"{v:.2f}"))

        pop = declared[declared.application == application]
        for variant in variants():
            for capacity_w, refrigerant in CONFIGURATIONS:
                for oversizing in OVERSIZING:
                    rows = trajectory(variant, capacity_w, refrigerant, application, oversizing)
                    all_points.extend(rows)
                    frame = pd.DataFrame(rows)
                    result = score(frame, reference)
                    ordered = frame.set_index("point").loc[["A", "B", "C", "D"]]
                    verdicts.append(
                        {
                            "variant": variant.key,
                            "application": application,
                            "capacity_kW": capacity_w / 1000.0,
                            "refrigerant": refrigerant,
                            "oversizing": oversizing,
                            **result,
                            **{
                                f"pct_{p}": percentile_of(float(ordered.cop[p]), pop[f"cop_{p}"])
                                for p in ("A", "B", "C", "D")
                            },
                        }
                    )

    points_df = pd.DataFrame(all_points)
    verdict_df = pd.DataFrame(verdicts)
    points_df.to_csv(OUT_POINTS, index=False)
    verdict_df.to_csv(OUT_VERDICT, index=False)

    print("\n\n=== verdict by compressor description (all configurations) ===")
    summary = (
        verdict_df.groupby(["variant", "application"])
        .agg(
            runs=("verdict", "size"),
            ok=("verdict", lambda s: int((s == "OK").sum())),
            shape=("C1_shape", "sum"),
            level=("C2_level", "sum"),
            gradient=("C3_gradient", "sum"),
            median_gradient=("gradient_DA", "median"),
        )
        .reset_index()
    )
    print(summary.to_string(index=False, float_format=lambda v: f"{v:.2f}"))

    print("\n=== TMHP defaults, per configuration ===")
    shipped = verdict_df[verdict_df.variant == "defaults"]
    print(
        shipped[
            [
                "application",
                "capacity_kW",
                "refrigerant",
                "oversizing",
                "points_inside",
                "gradient_DA",
                "pct_A",
                "pct_D",
                "verdict",
            ]
        ].to_string(index=False, float_format=lambda v: f"{v:.2f}")
    )

    print("\n=== the same four descriptions against the catalogue parity set ===")
    parity = pd.DataFrame([parity_under(v) for v in variants()])
    parity.to_csv(OUT_PARITY, index=False)
    print(parity.to_string(index=False, float_format=lambda v: f"{v:.2f}"))

    print(f"\nwrote {OUT_POINTS.relative_to(REPO_ROOT)}")
    print(f"wrote {OUT_VERDICT.relative_to(REPO_ROOT)}")
    print(f"wrote {OUT_PARITY.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
