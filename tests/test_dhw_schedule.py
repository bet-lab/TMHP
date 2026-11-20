"""Annex 42 draw schedules preserve timing and daily volume across time grids."""

import numpy as np
import pytest

from tmhp.dhw import (
    build_dhw_usage_ratio,
    calc_total_water_use_from_schedule,
    make_dhw_schedule_from_Annex_42_profile,
)


def test_source_pulse_is_split_by_time_overlap():
    """A one-minute pulse crossing a target boundary contributes to both bins."""
    flow = np.zeros(1440)
    flow[1] = 12.0
    entries = make_dhw_schedule_from_Annex_42_profile(flow, 1, 90)

    assert entries == [
        ("00:00", "00:01:30", pytest.approx(1 / 3)),
        ("00:01:30", "00:03", pytest.approx(1 / 3)),
    ]
    assert build_dhw_usage_ratio(entries, np.array([0, 89, 90, 179, 180])) == pytest.approx([1 / 3] * 4 + [0])


@pytest.mark.parametrize("target_seconds", [30, 61, 300, 700])
def test_resampled_daily_volume_matches_profile(target_seconds):
    """The whole day, including a partial final interval, delivers the input litres."""
    rng = np.random.default_rng(42)
    flow = rng.uniform(0, 15, 288)
    entries = make_dhw_schedule_from_Annex_42_profile(flow, 5, target_seconds)

    volume = calc_total_water_use_from_schedule(entries, np.max(flow) / 60_000, info=False)
    assert volume * 1000 == pytest.approx(5 * np.sum(flow))


def test_daily_volume_counts_seconds_in_schedule_times():
    """A draw crossing a minute boundary retains the actual duration in seconds."""
    volume = calc_total_water_use_from_schedule([("01:00:45", "01:01:15", 0.5)], 0.001, info=False)
    assert volume == pytest.approx(0.015)


def test_numpy_integer_sample_step_preserves_long_intervals():
    """Converting twelve-hour samples to seconds must not overflow a narrow dtype."""
    entries = make_dhw_schedule_from_Annex_42_profile(np.array([0.0, 10.0]), np.int16(720), 43200)
    assert entries == [("12:00", "24:00", 1.0)]
    assert calc_total_water_use_from_schedule(entries, 10 / 60000, info=False) == pytest.approx(7.2)


@pytest.mark.parametrize("step", [0, -10, 0.5, np.nan, np.inf])
def test_invalid_target_step_rejected(step):
    """Reject steps that cannot be expressed as a positive duration in seconds."""
    with pytest.raises(ValueError, match="simulation_time_step"):
        make_dhw_schedule_from_Annex_42_profile(np.ones(24), 60, step)


@pytest.mark.parametrize("invalid", [-1.0, np.nan, np.inf])
def test_invalid_flow_rejected(invalid):
    """A missing or invalid rate must not silently erase the day's draws."""
    profile = np.ones(24)
    profile[12] = invalid
    with pytest.raises(ValueError, match="flow rates"):
        make_dhw_schedule_from_Annex_42_profile(profile, 60, 1800)
