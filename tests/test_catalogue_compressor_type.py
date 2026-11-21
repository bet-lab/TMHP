"""The published compressor kind and injection: what a catalogue may say, and where it goes.

``compressor_type`` selects the library's type-specific default through one
mapping; ``injection`` is a reporting fact that never reaches the model.
"""

from __future__ import annotations

import pandas as pd
import pytest
from validation.parity import run
from validation.parity.spec import (
    ALLOWED_PUBLISHED_INPUTS,
    INJECTION_KINDS,
    MODEL_COMPRESSOR_TYPE,
    PUBLISHED_COMPRESSOR_TYPES,
    Catalog,
    OperatingPoint,
    load_all,
)

from tmhp.compressor_efficiency import COMPRESSOR_TYPES


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
    assert catalog.model_compressor_type == MODEL_COMPRESSOR_TYPE[kind]
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


def test_the_mapping_to_the_library_types():
    assert MODEL_COMPRESSOR_TYPE == {
        "rotary": "rotary_twin_inverter",
        "swing": "rotary_twin_inverter",
        "scroll": "scroll",
    }
    assert set(MODEL_COMPRESSOR_TYPE) == PUBLISHED_COMPRESSOR_TYPES
    assert set(MODEL_COMPRESSOR_TYPE.values()) <= set(COMPRESSOR_TYPES)


def test_unpublished_kind_falls_back_to_the_library_default():
    catalog = _catalog()
    assert catalog.model_compressor_type is None and catalog.injected is None
    assert run._build_model(catalog).compressor_type.compressor_type == COMPRESSOR_TYPES[0]


@pytest.mark.parametrize(
    ("slug", "expected"),
    [
        ("panasonic_wh_wxg09me8", "scroll"),
        ("samsung_ehs_mono_ht_r32_14kw", "scroll"),
        ("panasonic_wh_uq09he8", "rotary_twin_inverter"),
        ("daikin_rxm35a", "rotary_twin_inverter"),
    ],
)
def test_build_model_hands_the_mapped_type_to_the_model(slug, expected):
    (catalog,) = load_all(slug)
    assert run._build_model(catalog).compressor_type.compressor_type == expected


def test_every_catalogue_declares_both_inputs():
    catalogs = {c.slug: c for c in load_all()}
    assert len(catalogs) >= 17
    for slug, catalog in catalogs.items():
        assert {"compressor_type", "injection"} <= set(catalog.published_inputs), slug


@pytest.mark.parametrize("model_class", ["ASHPB", "ASHP"])
def test_injection_never_reaches_the_model_constructor(monkeypatch, model_class):
    seen: dict = {}

    def record(**kwargs):
        seen.update(kwargs)
        return object()

    monkeypatch.setattr(run, "AirSourceHeatPumpBoiler", record)
    monkeypatch.setattr(run, "AirSourceHeatPump", record)
    run._build_model(_catalog(model_class, compressor_type="scroll", injection="flash"))
    assert "injection" not in seen
    assert seen["compressor_type"] == "scroll"


def test_overrides_still_win_over_the_published_type(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(run, "AirSourceHeatPumpBoiler", lambda **kw: seen.update(kw))
    run._build_model(_catalog(compressor_type="scroll", injection="vapor"), {"compressor_type": "rotary_single_fixed"})
    assert seen["compressor_type"] == "rotary_single_fixed"


def test_unit_rows_carry_the_published_kind(monkeypatch):
    monkeypatch.setattr(run, "_build_model", lambda *args: object())
    monkeypatch.setattr(
        run, "_run_point", lambda *args, **kwargs: {"failure_reason": "none", "cop_sys [-]": 4.4, "E_tot [W]": 2000}
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
    """The summary prints a capacity score even with no air-to-water units."""
    capacity = {
        "points_capacity_short": 0,
        "capacity_ratio_mean": 1.0,
        "capacity_ratio_min": 1.0,
        "capacity_binding_counts": {},
        "points_capacity_ceiling": 4,
        "capacity_ceiling_headroom_mean": 1.5,
        "points_capacity_lower_bound": 0,
        "points_capacity_no_estimate": 0,
    }
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
                **capacity,
            }
        ]
    )
    strata = run.injection_strata(summary, [])
    assert strata.empty and list(strata.columns) == list(run.STRATA_COLUMNS)
    run._print_summary(
        summary,
        {"capacity_by_status_and_model_class": {"adopted": {"ASHP": capacity}}, "discharge_control_strata": []},
    )
    printed = capsys.readouterr().out
    assert "by injection" not in printed
    assert "adopted capacity: 0/4 short, mean ratio 1.000, min ratio 1.000, 4 ceilings (mean headroom 1.500)" in printed
