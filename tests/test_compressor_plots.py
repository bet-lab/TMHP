"""The fixed compressor figures (#256): map values, before/after rows, error scores and the model method.

The figures are checked through what a reader sees in them -- panel titles
(peak value and place, error scores), the points drawn on each panel -- and
through :func:`efficiency_grid`, whose values the map panels contour.
"""

import math

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pytest

from tmhp import AirSourceHeatPumpBoiler
from tmhp.compressor_efficiency import default_efficiencies, map_eta_ov, map_eta_v
from tmhp.compressor_plots import (
    OperatingPoints,
    StageScore,
    cop_errors,
    efficiency_grid,
    plot_calibration_stages,
    plot_compressor_map,
    plot_cop_parity,
)

#: A flat machine: eta_ov = 0.7 * 0.9 everywhere, so its peak is known.
FLAT = {"eta_cmp_isen": 0.7, "eta_cmp_vol": 0.9, "eta_cmp": 0.9}


@pytest.fixture(autouse=True)
def _close_figures():
    """Close every figure a test opens so pyplot does not accumulate them."""
    yield
    plt.close("all")


def _map_axes(fig):
    """The map panels of a map figure in row-major order (the colour bar left out)."""
    return [ax for ax in fig.axes if ax.get_label() != "<colorbar>"]


def test_grid_of_default_maps_equals_the_map_functions():
    """eta_ov of the defaults is map_eta_ov; eta_cmp_vol is map_eta_v with a value above one set to one."""
    prs = np.array([1.5, 3.0, 6.0])
    ns = np.array([30.0, 70.0, 110.0])
    for T_e in (-15.0, 10.0, 25.0):
        ov = efficiency_grid(None, "eta_ov", prs, ns, T_e)
        vol = efficiency_grid(None, "eta_cmp_vol", prs, ns, T_e)
        for j, n in enumerate(ns):
            for i, pr in enumerate(prs):
                assert ov[j, i] == pytest.approx(map_eta_ov(pr, n, T_e), rel=1e-12)
                assert vol[j, i] == pytest.approx(min(1.0, map_eta_v(pr, n, T_e)), rel=1e-12)


def test_grid_marks_an_infeasible_efficiency_as_nan():
    """A nonpositive efficiency is an infeasible point: NaN in the grid, not an error."""

    def eta_isen(pr, rps):
        return 0.7 if pr < 3.0 else -0.1

    grid = efficiency_grid({**FLAT, "eta_cmp_isen": eta_isen}, "eta_ov", [2.0, 4.0], [50.0], 0.0)
    assert grid[0, 0] == pytest.approx(0.63)
    assert math.isnan(grid[0, 1])


def test_unknown_quantity_and_incomplete_source_are_refused():
    """An unknown quantity is a ValueError; a mapping without all three efficiencies a TypeError."""
    with pytest.raises(ValueError, match="quantity"):
        efficiency_grid(None, "eta_x", [2.0], [50.0], 0.0)
    with pytest.raises(TypeError, match="eta_cmp"):
        plot_compressor_map({"eta_cmp_isen": 0.7}, None)


def test_identical_maps_give_identical_rows_and_a_changed_map_its_own_peak():
    """Row 1 is before, row 2 after: the same maps title both rows alike; a flat map shows its value as the peak."""
    temps = (-15.0, 0.0, 10.0)
    same = plot_compressor_map(None, default_efficiencies(), T_evap_C=temps, resolution=(30, 25))
    axes = _map_axes(same)
    assert len(axes) == 6
    titles = [ax.get_title(loc="left") for ax in axes]
    assert titles[:3] == titles[3:]
    assert [t.split(" · ")[0] for t in titles[:3]] == ["T_evap -15 °C", "T_evap 0 °C", "T_evap 10 °C"]

    changed = plot_compressor_map(None, FLAT, T_evap_C=temps, resolution=(30, 25))
    bottom = [ax.get_title(loc="left") for ax in _map_axes(changed)[3:]]
    assert all("peak 0.630" in t for t in bottom)
    assert [ax.get_title(loc="left") for ax in _map_axes(changed)[:3]] == titles[:3]


def test_points_go_to_the_column_of_the_nearest_evaporating_temperature():
    """Each operating point is drawn once per row, on the column whose T_evap is nearest to its own."""
    points = OperatingPoints(
        pressure_ratio=np.array([2.0, 3.0, 4.0, 5.0]),
        rps=np.array([40.0, 50.0, 60.0, 70.0]),
        T_evap_C=np.array([-20.0, -1.0, 4.0, 30.0]),
    )
    fig = plot_compressor_map(None, None, T_evap_C=(-15.0, 0.0, 10.0), points=points, resolution=(20, 20))
    counts = [
        sum(len(c.get_offsets()) for c in ax.collections if c.get_gid() == "operating-points") for ax in _map_axes(fig)
    ]
    # -20 -> the -15 column; -1 and 4 -> the 0 column; 30 -> the 10 column; the same in both rows
    assert counts == [1, 2, 1, 1, 2, 1]


def test_points_from_result_rows_use_the_pressure_ratio_and_drop_unsolved_rows():
    """Result rows give r_p from pr_cmp (or the pressures), N from cmp_rpm / 60; a NaN row is dropped."""
    rows = pd.DataFrame(
        {
            "P_ref_cmp_in [Pa]": [5.0e5, 4.0e5, math.nan],
            "P_ref_cmp_out [Pa]": [15.0e5, 20.0e5, math.nan],
            "cmp_rpm [rpm]": [3000.0, 4200.0, math.nan],
            "T_ref_evap_sat [°C]": [0.0, -5.0, math.nan],
        }
    )
    pts = OperatingPoints.from_results(rows)
    np.testing.assert_allclose(pts.pressure_ratio, [3.0, 5.0])
    np.testing.assert_allclose(pts.rps, [50.0, 70.0])
    np.testing.assert_allclose(pts.T_evap_C, [0.0, -5.0])
    with_pr = OperatingPoints.from_results({**rows.to_dict("list"), "pr_cmp [-]": [2.5, 4.5, math.nan]})
    np.testing.assert_allclose(with_pr.pressure_ratio, [2.5, 4.5])


def test_cop_errors_on_known_ratios_and_unsolved_points():
    """Ratios 1.1 and 0.9: MAPE 10 %, bias 0, RMSE from the logs; a NaN model value counts as unsolved."""
    e = cop_errors([2.0, 4.0, 3.0], [2.2, 3.6, math.nan])
    assert (e.n, e.n_failed) == (2, 1)
    assert e.mape == pytest.approx(0.10)
    assert e.bias == pytest.approx(0.0, abs=1e-12)
    assert e.rmse == pytest.approx(math.sqrt((math.log(1.1) ** 2 + math.log(0.9) ** 2) / 2))
    with pytest.raises(ValueError, match="reference"):
        cop_errors([0.0], [1.0])


def test_cop_errors_do_not_depend_on_the_cop_scale():
    """Scores are of ratios: scaling catalogue and model COP together leaves them unchanged (seeded cases)."""
    rng = np.random.default_rng(0)
    for _ in range(50):
        ref = rng.uniform(1.5, 8.0, size=12)
        mod = ref * rng.uniform(0.7, 1.3, size=12)
        k = rng.uniform(0.2, 5.0)
        a, b = cop_errors(ref, mod), cop_errors(k * ref, k * mod)
        assert (a.rmse, a.mape, a.bias) == pytest.approx((b.rmse, b.mape, b.bias), rel=1e-9)


def test_parity_titles_carry_the_before_and_after_scores():
    """Left panel scores the before values, right the after values, with the unsolved count when there is one."""
    ref = np.array([2.0, 4.0, 3.0])
    fig = plot_cop_parity(ref, ref * 0.8, [2.2, 3.6, math.nan], mode=["heating", "cooling", "heating"])
    left, right = (ax.get_title(loc="left") for ax in fig.axes)
    assert left.startswith("Default maps: MAPE 20.0 %, bias -20.0 %")
    assert right.startswith("Calibrated: MAPE 10.0 %, bias +0.0 %")
    assert "1 of 3 points not solved" in right
    with pytest.raises(ValueError, match="modes"):
        plot_cop_parity(ref, ref, ref, mode=["heating"])


def test_stage_figure_draws_fit_lines_in_percent_and_cv_markers_by_adoption():
    """Left panel RMSE, right MAPE, both in %: the line is the fit; CV markers are filled when adopted, open when not."""
    stages = [
        StageScore("Default", 0.151, 0.131, 0.149, 0.130, adopted=True),
        StageScore("κ_ov", 0.055, 0.035, 0.057, 0.037, adopted=True),
        StageScore("+λ_f", 0.055, 0.035, 0.059, 0.038, adopted=False),
        StageScore("+ΔN", 0.053, 0.036),
    ]
    fig = plot_calibration_stages(stages)
    rmse_ax, mape_ax = fig.axes
    np.testing.assert_allclose(rmse_ax.lines[0].get_ydata(), [15.1, 5.5, 5.5, 5.3])
    np.testing.assert_allclose(mape_ax.lines[0].get_ydata(), [13.1, 3.5, 3.5, 3.6])
    assert [t.get_text() for t in rmse_ax.get_xticklabels()] == ["Default", "κ_ov", "+λ_f", "+ΔN"]
    markers = mape_ax.collections
    assert [float(c.get_offsets()[0][1]) for c in markers] == pytest.approx([13.0, 3.7, 3.8])
    filled = [c.get_facecolor()[0][3] > 0 and tuple(c.get_facecolor()[0][:3]) != (1.0, 1.0, 1.0) for c in markers]
    assert filled == [True, True, False]


def test_stage_score_refuses_a_negative_or_nonfinite_error():
    """A stage error must be finite and non-negative; a stage needs a label."""
    with pytest.raises(ValueError, match="rmse_fit"):
        StageScore("κ_ov", -0.1, 0.0)
    with pytest.raises(ValueError, match="mape_cv"):
        StageScore("κ_ov", 0.1, 0.1, mape_cv=math.nan)
    with pytest.raises(ValueError, match="label"):
        StageScore("", 0.1, 0.1)


def test_model_method_draws_its_own_maps_against_the_defaults_or_another_model():
    """model.plot_compressor_map(): row 2 is the model's efficiencies, row 1 the defaults or the model given."""
    default_model = AirSourceHeatPumpBoiler(hp_capacity=9000.0, ref="R290")
    flat_model = AirSourceHeatPumpBoiler(hp_capacity=9000.0, ref="R290", **FLAT)

    fig = default_model.plot_compressor_map(T_evap_C=(0.0,), resolution=(20, 20))
    top, bottom = (ax.get_title(loc="left") for ax in _map_axes(fig))
    assert top == bottom
    assert fig.axes[0].get_ylabel().startswith("Default maps")
    assert fig.axes[1].get_ylabel().startswith("This model")

    fig = flat_model.plot_compressor_map(before=default_model, T_evap_C=(0.0,), resolution=(20, 20))
    top_flat, bottom_flat = (ax.get_title(loc="left") for ax in _map_axes(fig))
    assert top_flat == top
    assert "peak 0.630" in bottom_flat


def test_parity_draws_only_the_points_it_scores():
    """A zero or negative model COP is unsolved: not drawn and not in the axis range, as it is not in the scores."""
    ref = np.array([3.0, 4.0, 5.0])
    fig = plot_cop_parity(ref, ref, [0.0, 4.0, -1.0])
    left, right = fig.axes
    drawn = [len(c.get_offsets()) for c in right.collections]
    assert sum(drawn) == 1
    assert "2 of 3 points not solved" in right.get_title(loc="left")
    assert right.get_ylim()[0] > 2.0
