"""The published compressor kind and injection: what a catalogue may say, and where it goes.

Both are reporting facts that never reach the model: the library has one
default efficiency map for every compressor (#217), and the single-stage model
has no injection.
"""

from __future__ import annotations

import pandas as pd
import pytest
from validation.parity import run
from validation.parity.spec import (
    ALLOWED_PUBLISHED_INPUTS,
    INJECTION_KINDS,
    PUBLISHED_COMPRESSOR_TYPES,
    Catalog,
    OperatingPoint,
    load_all,
)


def _catalog(model_class: str = "ASHPB", **published) -> Catalog:
    return Catalog(
        "fixture",
        "fixture",
        "fixture",
        model_class,
        "R32",
        9.0,
        (OperatingPoint(1, 7, 35, 9.0, cop=4.5),),
        published_inputs=published,
    )


def test_both_inputs_are_allowed_published_inputs():
    assert {"compressor_type", "injection"} <= ALLOWED_PUBLISHED_INPUTS
    assert {"rotary", "scroll", "swing"} == PUBLISHED_COMPRESSOR_TYPES
    assert {"none", "vapor", "flash"} == INJECTION_KINDS


@pytest.mark.parametrize("kind", sorted(PUBLISHED_COMPRESSOR_TYPES))
@pytest.mark.parametrize("injection", sorted(INJECTION_KINDS))
def test_allowed_values_load(kind, injection):
    catalog = _catalog(compressor_type=kind, injection=injection)
    assert catalog.published_inputs["compressor_type"] == kind
    assert catalog.injected is (injection != "none")


@pytest.mark.parametrize(
    "published",
    [
        {"compressor_type": "reciprocating"},
        {"compressor_type": "rotary_twin_inverter"},  # the library's name is not a published kind
        {"injection": "vapour"},
        {"injection": True},
    ],
)
def test_unknown_values_are_rejected(published):
    with pytest.raises(ValueError):
        _catalog(**published)


def test_unpublished_kind_reports_nothing():
    catalog = _catalog()
    assert "compressor_type" not in catalog.published_inputs and catalog.injected is None


def test_every_catalogue_declares_both_inputs():
    catalogs = {c.slug: c for c in load_all()}
    assert len(catalogs) >= 17
    for slug, catalog in catalogs.items():
        assert {"compressor_type", "injection"} <= set(catalog.published_inputs), slug


@pytest.mark.parametrize("model_class", ["ASHPB", "ASHP"])
def test_neither_input_reaches_the_model_constructor(monkeypatch, model_class):
    seen: dict = {}

    def record(**kwargs):
        seen.update(kwargs)
        return object()

    monkeypatch.setattr(run, "AirSourceHeatPumpBoiler", record)
    monkeypatch.setattr(run, "AirSourceHeatPump", record)
    run._build_model(_catalog(model_class, compressor_type="scroll", injection="flash"))
    assert "injection" not in seen
    assert "compressor_type" not in seen


def test_unit_rows_carry_the_published_kind(monkeypatch):
    monkeypatch.setattr(run, "_build_model", lambda *args: object())
    monkeypatch.setattr(
        run, "_run_point", lambda *args: {"failure_reason": "none", "cop_sys [-]": 4.4, "E_tot [W]": 2000}
    )
    row = run.run_catalog(_catalog(compressor_type="scroll", injection="vapor")).iloc[0]
    assert (row["compressor_type"], row["injection"]) == ("scroll", "vapor")
    bare = run.run_catalog(_catalog()).iloc[0]
    assert (bare["compressor_type"], bare["injection"]) == ("", "")
    summary = run._summarise(run.run_catalog(_catalog(compressor_type="rotary", injection="none")))
    assert (summary["compressor_type"], summary["injection"]) == ("rotary", "none")


def test_injection_strata_split_the_pooled_headline():
    def unit(slug, injection, points, usable, mape, bias, model_class="ASHPB", status="adopted"):
        published = {"compressor_type": "rotary", "injection": injection} if injection else {}
        catalog = Catalog(
            slug,
            slug,
            "fixture",
            model_class,
            "R32",
            9.0,
            (OperatingPoint(1, 7, 35, 9.0, cop=4.5),),
            published_inputs=published,
            rating_standard="EN 14511",
            status=status,
            hold_reason="fixture" if status == "hold" else "",
        )
        row = {
            "slug": slug,
            "model_class": model_class,
            "rating_standard": "EN 14511",
            "status": status,
            "points": points,
            "points_usable": usable,
            "cop_MAPE_pct": mape,
            "cop_bias_pct": bias,
        }
        return catalog, row

    units = [
        unit("a", "none", 10, 8, 4.0, -2.0),
        unit("b", "none", 10, 2, 9.0, 1.0),
        unit("c", "vapor", 6, 5, 12.0, -10.0),
        unit("d", "flash", 4, 3, 20.0, -6.0),
        unit("held", "none", 9, 9, 50.0, 50.0, status="hold"),
        unit("air", "none", 9, 9, 50.0, 50.0, model_class="ASHP"),
    ]
    catalogs = [c for c, _ in units]
    summary = pd.DataFrame([r for _, r in units])
    strata = run.injection_strata(summary, catalogs).set_index("stratum")
    pooled = strata.loc[run.STRATUM_POOLED]
    none = strata.loc[run.STRATUM_NO_INJECTION]
    inj = strata.loc[run.STRATUM_INJECTION]
    assert (pooled.units, pooled.points, pooled.points_usable) == (4, 30, 18)
    assert (none.units, none.points, none.points_usable) == (2, 20, 10)
    assert (inj.units, inj.points, inj.points_usable) == (2, 10, 8)
    assert none.cop_MAPE_pct == pytest.approx((4.0 * 8 + 9.0 * 2) / 10)
    assert inj.cop_bias_pct == pytest.approx((-10.0 * 5 - 6.0 * 3) / 8)
    # The strata add up to the pooled figure, point for point.
    assert pooled.cop_MAPE_pct == pytest.approx(
        (none.cop_MAPE_pct * none.points_usable + inj.cop_MAPE_pct * inj.points_usable) / pooled.points_usable
    )
    assert run.STRATUM_UNPUBLISHED not in strata.index


def test_strata_and_summary_print_without_air_to_water_units(capsys):
    summary = pd.DataFrame(
        [
            {
                "slug": "air",
                "unit": "air",
                "model_class": "ASHP",
                "refrigerant": "R32",
                "rating_standard": "EN 14511",
                "status": "adopted",
                "points": 4,
                "points_usable": 4,
                "cop_MAE": 0.1,
                "cop_MAPE_pct": 5.0,
                "cop_bias_pct": -1.0,
            }
        ]
    )
    strata = run.injection_strata(summary, [])
    assert strata.empty and list(strata.columns) == list(run.STRATA_COLUMNS)
    run._print_summary(summary)
    assert "by injection" not in capsys.readouterr().out
