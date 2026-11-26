"""The fixed compressor figures (#256, #258): map values, before/after rows, error scores, style and the model method.

The figures are checked through what a reader sees in them -- panel titles
(peak value and place, error scores), row titles, tick labels, axis names,
colour bars, the points drawn on each panel -- through :func:`efficiency_grid`,
whose values the map panels contour, and through dartwork-mpl's own figure
check. The figma-plot drawing rules the figures follow (explicit axis specs,
decimals, units, colour bars) are checked the same way.
"""

import math

import matplotlib

matplotlib.use("Agg")

import dartwork_mpl as dm
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pytest
from matplotlib.markers import MarkerStyle

from tmhp import AirSourceHeatPumpBoiler
from tmhp.compressor_efficiency import default_efficiencies, map_eta_ov, map_eta_v
from tmhp.compressor_plots import (
    COOLING,
    FIGURE_STYLE,
    GREY,
    HEATING,
    OperatingPoints,
    StageScore,
    _axis_spec,
    _spec_ticks,
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
    """The map panels of a map figure in row-major order (the colour bars left out)."""
    return [ax for ax in fig.axes if not hasattr(ax, "_colorbar")]


def _bar_axes(fig):
    """The colour-bar axes of a figure, top row first."""
    return [ax for ax in fig.axes if hasattr(ax, "_colorbar")]


def _shown(labels):
    """Texts of the visible, non-empty tick labels."""
    return [t.get_text() for t in labels if t.get_visible() and t.get_text()]


def _spec_of(axis):
    """(min, max, interval) a linear axis shows: its limits and the spacing of its ticks, which must be even."""
    lo, hi = sorted(axis.axes.get_xlim() if axis.axis_name == "x" else axis.axes.get_ylim())
    ticks = np.asarray(axis.get_ticklocs(), dtype=float)
    steps = np.diff(ticks)
    np.testing.assert_allclose(steps, steps[0], rtol=1e-9)
    # both ends are ticks
    assert ticks[0] == pytest.approx(lo) and ticks[-1] == pytest.approx(hi)
    return (lo, hi, float(steps[0]))


def _titles(ax):
    """(left, right) title of a panel: its name and its numbers."""
    return ax.get_title(loc="left"), ax.get_title(loc="right")


def _stage_list():
    return [
        StageScore("Default", 0.151, 0.131, 0.149, 0.130, adopted=True),
        StageScore("κ_ov", 0.055, 0.035, 0.057, 0.037, adopted=True),
        StageScore("+λ_f", 0.055, 0.035, 0.059, 0.038, adopted=False),
        StageScore("+ΔN", 0.053, 0.036),
    ]


def _three_figures():
    """One of each figure, small enough to draw quickly."""
    ref = np.array([2.0, 3.0, 4.0, 5.0, 6.0])
    points = OperatingPoints(np.array([2.0, 3.0, 4.0]), np.array([40.0, 60.0, 80.0]), np.array([-10.0, 0.0, 8.0]))
    return {
        "map": plot_compressor_map(None, FLAT, points=points, resolution=(40, 30)),
        "stages": plot_calibration_stages(_stage_list(), title="Unit: COP error by stage"),
        "parity": plot_cop_parity(ref, ref * 0.85, ref * 1.02, mode=["heating", "cooling"] * 2 + ["heating"]),
    }


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
    titles = [_titles(ax) for ax in axes]
    assert titles[:3] == titles[3:]
    assert [left for left, _ in titles[:3]] == [
        "Evaporating \N{MINUS SIGN}15 °C",
        "Evaporating 0 °C",
        "Evaporating 10 °C",
    ]

    changed = plot_compressor_map(None, FLAT, T_evap_C=temps, resolution=(30, 25))
    bottom = [_titles(ax)[1] for ax in _map_axes(changed)[3:]]
    assert all(t.startswith("peak 63.0 %") for t in bottom)
    assert [_titles(ax) for ax in _map_axes(changed)[:3]] == titles[:3]


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
    left, right = (_titles(ax) for ax in fig.axes)
    assert left[0] == "Default maps"
    assert left[1].startswith("MAPE 20.0 %, bias -20.0 %")
    assert right[0] == "Calibrated"
    assert right[1].startswith("MAPE 10.0 %, bias +0.0 %")
    assert "1 of 3 points not solved" in right[1]
    with pytest.raises(ValueError, match="modes"):
        plot_cop_parity(ref, ref, ref, mode=["heating"])


def test_stage_figure_draws_fit_lines_in_percent_and_cv_markers_by_adoption():
    """Left panel RMSE, right MAPE, both in %: the line is the fit; CV markers are filled when adopted, open when not."""
    fig = plot_calibration_stages(_stage_list())
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
    top, bottom = (_titles(ax) for ax in _map_axes(fig))
    assert top == bottom
    assert [ax.get_figure(root=False).get_suptitle() for ax in _map_axes(fig)] == ["Default maps", "This model"]

    fig = flat_model.plot_compressor_map(before=default_model, T_evap_C=(0.0,), resolution=(20, 20))
    top_flat, bottom_flat = (_titles(ax) for ax in _map_axes(fig))
    assert top_flat == top
    assert bottom_flat[1].startswith("peak 63.0 %")


def test_parity_draws_only_the_points_it_scores():
    """A zero or negative model COP is unsolved: not drawn and not in the axis range, as it is not in the scores."""
    ref = np.array([3.0, 4.0, 5.0])
    fig = plot_cop_parity(ref, ref, [0.0, 4.0, -1.0])
    left, right = fig.axes
    drawn = [len(c.get_offsets()) for c in right.collections]
    assert sum(drawn) == 1
    assert "2 of 3 points not solved" in right.get_title(loc="right")
    assert right.get_ylim()[0] > 2.0


def test_figures_are_drawn_in_the_scientific_preset_and_leave_the_callers_style_alone():
    """Whatever the caller set, the figures use the preset's sizes; the caller's rcParams are as before the call."""
    with dm.style.context(FIGURE_STYLE):
        expected = {k: plt.rcParams[k] for k in ("xtick.labelsize", "axes.labelsize", "xtick.major.pad")}
    with plt.rc_context({"font.size": 13.0, "xtick.labelsize": 13.0, "ytick.labelsize": 13.0, "xtick.major.pad": 6.0}):
        before = dict(plt.rcParams)
        figs = _three_figures()
        assert dict(plt.rcParams) == before
        for name, fig in figs.items():
            # drawn here, outside the preset, as a caller's savefig would draw it
            fig.canvas.draw()
            for ax in fig.axes:
                for tick in ax.xaxis.get_major_ticks():
                    assert tick.label1.get_fontsize() == expected["xtick.labelsize"], name
                    assert tick.get_pad() == expected["xtick.major.pad"], name
                if ax.get_xlabel():
                    assert ax.xaxis.label.get_fontsize() == expected["axes.labelsize"], name


def test_map_rows_stand_apart_and_every_panel_has_its_own_tick_labels():
    """Map rows are separate blocks under their own titles; no panel shares an axis or lacks tick labels."""
    figs = _three_figures()
    rows = [ax.get_figure(root=False) for ax in _map_axes(figs["map"])]
    assert [r.get_suptitle() for r in dict.fromkeys(rows)] == ["Default maps", "Calibrated"]
    top, bottom = _map_axes(figs["map"])[:3], _map_axes(figs["map"])[3:]
    gap = min(ax.get_window_extent().y0 for ax in top) - max(ax.get_window_extent().y1 for ax in bottom)
    # the gap holds the top row's x tick labels and axis name and the bottom row's titles
    assert gap > 3 * top[0].xaxis.label.get_fontsize()
    for name in ("map", "parity"):
        fig = figs[name]
        fig.canvas.draw()
        for ax in _map_axes(fig):
            assert ax.get_shared_x_axes().get_siblings(ax) == [ax], name
            assert ax.get_shared_y_axes().get_siblings(ax) == [ax], name
            for labels in (ax.get_xticklabels(), ax.get_yticklabels()):
                assert [t for t in labels if t.get_visible() and t.get_text()], name


def test_figures_pass_the_dartwork_mpl_figure_check():
    """dartwork-mpl finds nothing to warn about: no overflow, clipping, overlap or unreadable text."""
    for name, fig in _three_figures().items():
        warnings = [w for w in dm.validate_figure(fig, quiet=True) if w.severity.name == "WARNING"]
        assert warnings == [], name


def test_map_colour_bars_have_no_triangles_and_show_only_their_two_end_values():
    """One upright bar per row, right of the row's last panel, its top level with the panel's; no triangular ends;
    the two end levels as its only tick labels, without tick lines, and the quantity named above it."""
    fig = _three_figures()["map"]
    fig.canvas.draw()
    bars, panels = _bar_axes(fig), _map_axes(fig)
    assert len(bars) == 2
    for bar_ax, last in zip(bars, (panels[2], panels[5]), strict=True):
        bar = bar_ax._colorbar
        assert bar.extend == "neither"
        assert bar.orientation == "vertical"
        assert bar_ax.get_ylim() == pytest.approx((0.30, 0.85))
        assert _shown(bar_ax.get_yticklabels()) == ["30", "85"]
        assert all(
            t.tick1line.get_markersize() == 0 and t.tick2line.get_markersize() == 0
            for t in bar_ax.yaxis.get_major_ticks()
        )
        box, panel = bar_ax.get_window_extent(), last.get_window_extent()
        assert box.x0 > panel.x1
        assert (box.y0, box.y1) == pytest.approx((panel.y0, panel.y1), abs=0.5)
        # the label: one upright line right of the end values, centred on the bar
        label = bar_ax.yaxis.label
        assert label.get_text() == "Overall efficiency (%)"
        lbox = label.get_window_extent()
        assert lbox.x0 > max(t.get_window_extent().x1 for t in bar_ax.get_yticklabels() if t.get_text())
        assert (lbox.y0 + lbox.y1) / 2 == pytest.approx((box.y0 + box.y1) / 2, abs=1.0)


def test_map_fills_values_beyond_the_levels_in_the_end_colours():
    """A map above the top level fills its panels with the top band, one below the bottom level with the bottom band:
    no blank panels where the efficiency lies beyond the fixed levels."""
    high = {"eta_cmp_isen": 1.0, "eta_cmp_vol": 0.9, "eta_cmp": 0.95}  # eta_ov 0.95 > 0.85
    low = {"eta_cmp_isen": 0.4, "eta_cmp_vol": 0.9, "eta_cmp": 0.5}  # eta_ov 0.20 < 0.30
    fig = plot_compressor_map(high, low, resolution=(20, 15))
    for i, ax in enumerate(_map_axes(fig)):
        (filled,) = [c for c in ax.collections if getattr(c, "filled", False)]
        drawn = [len(p.vertices) > 0 for p in filled.get_paths()]
        expected = [False] * (len(drawn) - 1) + [True] if i < 3 else [True] + [False] * (len(drawn) - 1)
        assert drawn == expected, i


def test_linear_axes_have_an_explicit_spec_with_ticks_at_both_ends():
    """Speed axis 15 to 120 every 15; error axes from 0 to a round value above the largest error; parity axes over
    the drawn values rounded outward; pressure ratio 1 to 12, a tick at every whole number."""
    figs = _three_figures()
    for ax in _map_axes(figs["map"]):
        assert ax.get_xscale() == "linear"
        assert _spec_of(ax.xaxis) == pytest.approx((1.0, 12.0, 1.0))
        assert _spec_of(ax.yaxis) == pytest.approx((15.0, 120.0, 15.0))
    rmse_ax, mape_ax = figs["stages"].axes
    assert _spec_of(rmse_ax.yaxis) == pytest.approx((0.0, 16.0, 2.0))  # largest RMSE 15.1 %
    assert _spec_of(mape_ax.yaxis) == pytest.approx((0.0, 14.0, 2.0))  # largest MAPE 13.1 %
    for ax in figs["parity"].axes:
        # drawn COPs 1.7 to 6.12, with 10 % room: 1.53 to 6.73
        assert _spec_of(ax.xaxis) == pytest.approx((1.0, 7.0, 1.0))
        assert _spec_of(ax.yaxis) == pytest.approx((1.0, 7.0, 1.0))


def test_axis_spec_is_whole_intervals_round_and_holds_the_range():
    """Every spec covers its range with 3 to 8 whole intervals of a 1, 2 or 5 mantissa, min and max on the interval."""
    assert _axis_spec(15.0, 120.0, steps=(1.0, 1.5, 2.0, 2.5, 3.0, 5.0)) == pytest.approx((15.0, 120.0, 15.0))
    assert _axis_spec(2.07, 8.5) == pytest.approx((2.0, 9.0, 1.0))
    rng = np.random.default_rng(1)
    for _ in range(300):
        lo = float(rng.uniform(-50.0, 50.0))
        hi = lo + float(10.0 ** rng.uniform(-3.0, 3.0))
        a, b, step = _axis_spec(lo, hi)
        n = (b - a) / step
        assert a <= lo and b >= hi
        assert n == pytest.approx(round(n), abs=1e-6) and 3 <= round(n) <= 8
        mantissa = step / 10.0 ** math.floor(math.log10(step) + 1e-9)
        assert round(mantissa, 6) in (1.0, 2.0, 5.0)
        assert a / step == pytest.approx(round(a / step), abs=1e-6)


def test_axis_spec_keeps_a_nonzero_interval_for_tiny_ranges():
    """Rounding is relative to the interval, so a range far below 1e-9 still gets ticks (PR #260 review)."""
    lo, hi, step = _axis_spec(0.0, 1.05e-10)
    assert step > 0.0 and hi >= 1.05e-10 and lo == 0.0
    assert len(_spec_ticks((lo, hi, step))) >= 4
    fig = plot_calibration_stages([StageScore("Default", 1e-12, 1e-12), StageScore("Fit", 1e-12, 1e-12)])
    fig.canvas.draw()
    shown = _shown(fig.axes[0].get_yticklabels())
    assert len(shown) == len(set(shown)) >= 4


def test_tick_labels_carry_only_the_decimals_their_interval_needs():
    """Whole intervals: no decimal point; efficiencies in percent (30, 85; contour labels every 10): no decimals."""
    figs = _three_figures()
    figs["map"].canvas.draw()
    panel = _map_axes(figs["map"])[0]
    assert _shown(panel.get_yticklabels()) == ["15", "30", "45", "60", "75", "90", "105", "120"]
    # pressure ratio: a tick at every whole number, a number on every second one, upright
    assert len(panel.get_xticks()) == 12
    assert _shown(panel.get_xticklabels()) == ["1", "3", "5", "7", "9", "11"]
    assert {t.get_rotation() for t in panel.get_xticklabels()} == {0.0}
    assert _shown(_bar_axes(figs["map"])[0].get_yticklabels()) == ["30", "85"]
    defaults = plot_compressor_map(None, None, T_evap_C=(0.0,), resolution=(40, 30))
    defaults.canvas.draw()
    line_labels = [t.get_text() for t in _map_axes(defaults)[0].texts]
    assert line_labels and all(t.isdigit() and int(t) % 10 == 0 for t in line_labels)
    figs["stages"].canvas.draw()
    assert _shown(figs["stages"].axes[0].get_yticklabels()) == ["0", "2", "4", "6", "8", "10", "12", "14", "16"]
    figs["parity"].canvas.draw()
    assert _shown(figs["parity"].axes[1].get_xticklabels()) == ["1", "2", "3", "4", "5", "6", "7"]


def test_axis_names_write_percent_in_parentheses_units_in_brackets_and_titles_in_words():
    """(%) for percent, [rev/s] for speed; panel titles name the evaporating temperature in words, not T_evap."""
    figs = _three_figures()
    rmse_ax, mape_ax = figs["stages"].axes
    assert rmse_ax.get_ylabel() == "COP RMSE, ln(model/catalogue) (%)"
    assert mape_ax.get_ylabel() == "COP MAPE (%)"
    for ax in _map_axes(figs["map"]):
        assert ax.get_ylabel() in ("Speed (N) [rev/s]", "")
        assert ax.get_xlabel() == r"Pressure ratio ($\mathregular{r_p}$)"
        assert ax.get_title(loc="left").startswith("Evaporating ")
    texts = [t.get_text() for fig in figs.values() for t in fig.findobj(matplotlib.text.Text)]
    assert not [t for t in texts if "[%]" in t or "T_evap" in t]


def test_operating_points_are_small_solid_black_dots_with_a_hairline_white_rim():
    """Points: half the former area (4.5 pt^2), filled black, a 0.3 pt white edge so overlapping dots stay apart."""
    for ax in _map_axes(_three_figures()["map"]):
        for c in (c for c in ax.collections if c.get_gid() == "operating-points"):
            assert list(c.get_sizes()) == [4.5]
            assert tuple(c.get_facecolor()[0]) == (0.0, 0.0, 0.0, 1.0)
            assert tuple(c.get_edgecolor()[0]) == (1.0, 1.0, 1.0, 1.0)
            assert list(c.get_linewidths()) == [0.3]


def test_parity_points_are_opaque_circles_red_for_heating_blue_for_cooling_with_a_white_rim():
    """Mode is told by colour, not shape: every point a circle, no transparency, heating red, cooling blue,
    anything else grey, each with a 0.3 pt white edge so overlapping points stay apart."""
    fig = plot_cop_parity([2.0, 3.0, 4.0], [2.1, 2.9, 4.2], [2.0, 3.1, 3.9], mode=["heating", "cooling", "defrost"])
    expected = {"heating": HEATING, "cooling": COOLING, "defrost": GREY}
    for ax in fig.axes:
        by_label = {c.get_label(): c for c in ax.collections}
        assert set(by_label) == set(expected)
        for label, c in by_label.items():
            assert c.get_paths()[0].vertices.shape == MarkerStyle("o").get_path().vertices.shape
            assert c.get_alpha() is None
            assert tuple(c.get_facecolor()[0]) == pytest.approx(matplotlib.colors.to_rgba(expected[label]))
            assert tuple(c.get_edgecolor()[0]) == (1.0, 1.0, 1.0, 1.0)
            assert list(c.get_linewidths()) == [0.3]


def test_legend_markers_are_drawn_at_the_size_of_the_markers_they_name():
    """No marker scaling: each legend marker has the size of the plotted marker of the same shape."""
    fig = _three_figures()["map"]
    drawn = {}
    for ax in _map_axes(fig):
        for c in ax.collections:
            if c.get_gid() in ("peak", "operating-points"):
                drawn["D" if c.get_gid() == "peak" else "o"] = math.sqrt(c.get_sizes()[0])
    (legend,) = fig.legends
    assert legend.markerscale == 1.0
    sizes = {h.get_marker(): h.get_markersize() for h in legend.legend_handles if h.get_marker() not in (None, "None")}
    assert sizes == pytest.approx(drawn)


def test_row_headings_outrank_panel_titles_and_no_figure_has_a_title_by_default():
    """The row heading is larger than the panel titles; none of the three figures has a figure title unless asked."""
    figs = _three_figures()
    for ax in _map_axes(figs["map"]):
        heading = ax.get_figure(root=False)._suptitle
        panel_title = ax._left_title
        assert heading.get_fontsize() > panel_title.get_fontsize()
    figs_no_title = {
        "map": plot_compressor_map(None, None, resolution=(20, 20)),
        "stages": plot_calibration_stages(_stage_list()),
        "parity": plot_cop_parity([2.0, 3.0], [2.1, 2.9], [2.0, 3.1]),
    }
    for name, fig in figs_no_title.items():
        assert fig._suptitle is None, name


def test_symbols_and_colour_bar_label_are_drawn_in_the_figures_own_font():
    """r_p is drawn in the font and weight of the text around it, also when saved after the preset is gone;
    the colour-bar label is one upright line in percent."""
    from matplotlib import font_manager
    from matplotlib.mathtext import MathTextParser

    fig = _three_figures()["map"]
    parser = MathTextParser("path")
    for text in (_map_axes(fig)[0]._right_title, _map_axes(fig)[0].xaxis.label):
        prop = text.get_fontproperties()
        *_, glyphs, _rects = parser.parse(text.get_text().splitlines()[-1], dpi=72, prop=prop)
        assert {str(g[0].fname) for g in glyphs} == {str(font_manager.findfont(prop))}
    bars = [ax for ax in fig.axes if hasattr(ax, "_colorbar")]
    for cax in bars:
        assert cax.get_ylabel() == "Overall efficiency (%)"
        assert cax.yaxis.label.get_rotation() == 90.0
