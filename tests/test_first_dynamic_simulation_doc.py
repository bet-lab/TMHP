"""The cut-off temperatures quoted in ``getting-started/first-dynamic-simulation.rst`` (#228).

The page quotes the *envelope stop* of the boilers (``T_cond_max`` less the tank
heat-exchanger approach), where a run with ``min_on_time_s=1800`` stops, and the thermostat
band the figure's R32 machine runs in. Each number is computed here from the models and has to
appear in the page at the printed precision, so a change of ``T_cond_max``, of the heat-exchanger
conductance or of the in-step stop moves the page and the test together.
"""

from __future__ import annotations

import re
import warnings
from pathlib import Path

import numpy as np
import pytest

import tmhp
from tmhp.compressor_envelope import condensing_tank_cutout

PAGE = Path(__file__).resolve().parents[1] / "docs/source/getting-started/first-dynamic-simulation.rst"

STEPS_S = (60.0, 300.0, 600.0)
#: the page's run: 2 h from 50 degC, no draw, 7 degC source, 30 min minimum run time
HOURS, T_INIT_C, T_SOURCE_C, MIN_ON_S = 2, 50.0, 7.0, 1800.0
#: how far below the envelope stop the tank temperature of a run may end, as quoted in the page [K]
PEAK_TOL_K = 0.03


def _quiet(factory, *args, **kwargs):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return factory(*args, **kwargs)


def _text() -> str:
    """The page with its line wrapping and markup quotes removed."""
    return re.sub(r"\s+", " ", PAGE.read_text(encoding="utf-8")).replace("``", "")


def _r32_ashpb(**kwargs):
    return tmhp.AirSourceHeatPumpBoiler(ref="R32", **kwargs)


FAMILIES = {
    "GSHPB": tmhp.GroundSourceHeatPumpBoiler,
    "WSHPB": tmhp.WaterSourceHeatPumpBoiler,
    "ASHPB R32": _r32_ashpb,
}


def test_the_quoted_envelope_stops_are_the_models():
    """Each stop is tied to its refrigerant's limit in the page's own sentence, so swapped values fail."""
    text = _text()
    models = {name: _quiet(f) for name, f in FAMILIES.items()}
    for name, model in models.items():
        k = float(model.hp_capacity) / float(model.K_tank_hx)
        assert f"{k:.2f} K" in text, f"{name}: heat-exchanger approach {k:.2f} K is not on the page"
    r290, r32 = models["GSHPB"], models["ASHPB R32"]
    assert models["WSHPB"].ref == r290.ref == "R290" and r32.ref == "R32"
    stop290, stop32 = condensing_tank_cutout(r290), condensing_tank_cutout(r32)
    assert condensing_tank_cutout(models["WSHPB"]) == pytest.approx(stop290)
    assert f"{stop290:.2f} °C for the default R290 boilers (T_cond_max {float(r290.T_cond_max):g} °C)" in text
    assert f"{stop32:.2f} °C for an R32 ASHPB ({float(r32.T_cond_max):g} °C)" in text
    # the run sentence pairs them again
    assert f"the default R290 GSHPB and WSHPB at {stop290:.2f} °C, an R32 ASHPB at {stop32:.2f} °C" in text
    # the R290 default upper set point lies below the stop, so it is not capped
    for name in ("GSHPB", "WSHPB"):
        requested = models[name].T_tank_w_setpoints_requested[1]
        assert float(models[name].T_tank_w_upper_bound) == float(requested) < stop290, name
        assert f"the default {float(requested):g} °C of the R290 GSHPB and WSHPB lies below it" in text
    assert 'With the default condensing_limit_control="stop", a requested upper set point' in text


@pytest.mark.parametrize("name", list(FAMILIES))
@pytest.mark.parametrize("dt", STEPS_S)
def test_a_minimum_run_time_stops_at_the_quoted_envelope_stop(name, dt):
    """2 h from 50 degC with ``min_on_time_s=1800``: one condensing-limit stop, at the cut-out, whatever the step."""
    model = _quiet(FAMILIES[name], min_on_time_s=MIN_ON_S)
    stop = condensing_tank_cutout(model)
    n = int(HOURS * 3600 / dt)
    df = _quiet(model.analyze_dynamic, n * dt, dt, T_INIT_C, np.zeros(n), np.full(n, T_SOURCE_C))
    assert (df["failure_reason"] == "none").all()
    assert (df["hp_stop_reason"] == "condensing_limit").sum() == 1
    # The run ends at the envelope stop, from below and within the tolerance the page quotes (the 65 degC
    # set point of GSHPB/WSHPB lies below the stop, so their minimum run carries the tank up to it).
    peak = float(df["T_tank_w [°C]"].max())
    assert stop - PEAK_TOL_K <= peak <= stop + 1e-6, (name, dt, peak, stop)
    # the step that stops the compressor ends at the stop too
    at_stop = float(df.loc[df["hp_stop_reason"] == "condensing_limit", "T_tank_w [°C]"].iloc[0])
    assert stop - PEAK_TOL_K <= at_stop <= stop + 1e-6, (name, dt, at_stop, stop)


def test_the_page_quotes_the_run_it_was_measured_on():
    text = _text()
    assert "min_on_time_s=1800" in text
    assert "60, 300 and 600 s steps" in text
    assert f"a {HOURS} h run from {T_INIT_C:g} °C with no draw and a {T_SOURCE_C:g} °C source" in text
    assert f"within {PEAK_TOL_K:.2f} K below the stop" in text


def test_the_figure_caption_band_is_the_capped_band_of_the_r32_machine():
    """The 24 h figure builds ``AirSourceHeatPumpBoiler(ref="R32")``: the 60-65 degC default band is capped."""
    model = _quiet(_r32_ashpb)
    lower, upper = model.T_tank_w_setpoints_requested
    text = _text()
    capped = f"{model.T_tank_w_lower_bound:.0f}–{model.T_tank_w_upper_bound:.0f} °C"
    assert f"caps the default {lower:g}–{upper:g} °C thermostat band at {capped}" in text
