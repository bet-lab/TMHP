"""Actual model keyframes with explicitly interpolated illustrative transitions.

This is an explanatory parameter sweep, NOT the optimizer's evaluation history.
Every keyframe recalculates all catalogue rows and a fixed-condition PLR curve.
Intermediate video frames interpolate only between valid endpoint predictions;
invalid endpoint pairs remain gaps. Efficiency surfaces are evaluated analytically
at every displayed multiplier. Run again after replacing the frozen case/report.
Optional rendering dependency: imageio-ffmpeg (not a production dependency).
"""

import argparse
import hashlib
import json
import math
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path

import dartwork_mpl as dm
import matplotlib

matplotlib.use("Agg")
import case as case_config
import matplotlib.pyplot as plt
import numpy as np
from case import EQUIPMENT_SETTINGS, ROOT, catalogue, make_model, points
from matplotlib.colors import LinearSegmentedColormap, Normalize
from matplotlib.offsetbox import AnnotationBbox, HPacker, TextArea

from tmhp.calibration import EfficiencyScales, scaled_efficiencies
from tmhp.compressor_baseline import OssorioR290Map

OUTPUT = Path("/home/habin/papers/temp/261102 presentation")
FIELDS = ["lambda_eta_ov", "lambda_eta_v", "lambda_f_loss"]
QUANTITIES = ["eta_ov", "eta_v", "f_loss"]
LABELS = ["Overall efficiency", "Volumetric efficiency", "Heat loss fraction"]
PLRS = np.arange(0.4, 1.61, 0.1)


def spec(ax, dimension, bounds, label):
    """Explicit finite linear axis with endpoint ticks."""
    low, high, step = bounds
    assert low < high and step > 0 and np.isclose((high - low) / step, round((high - low) / step))
    getattr(ax, "set_" + dimension + "lim")(low, high)
    getattr(ax, "set_" + dimension + "ticks")(np.linspace(low, high, round((high - low) / step) + 1))
    getattr(ax, "set_" + dimension + "label")(label)


def compute_snapshot(task):
    """Re-evaluate true model predictions; workers have independent CoolProp state."""
    scales, rows, indoor, outdoor, rated = task
    hp = make_model()
    hp.eta_cmp_vol, hp.eta_cmp_isen, hp.eta_cmp = scaled_efficiencies(
        (hp.eta_cmp_vol, hp.eta_cmp_isen, hp.eta_cmp), EfficiencyScales(**scales)
    )
    cat = hp.predict_catalogue(rows, mode="heating")
    curve = hp.predict_catalogue([(indoor, outdoor, float(plr * rated), 1.0) for plr in PLRS], mode="heating")
    return {"scales": scales, "catalogue": [asdict(p) for p in cat], "curve": [asdict(p) for p in curve]}


def keyframes(final):
    """Three illustrative excursions per role, returning to the applied solution."""
    current = dict.fromkeys(FIELDS, 1.0)
    result = [dict(current)]
    segments = []
    for stage, field in enumerate(FIELDS):
        start = len(result) - 1
        center = final[field]
        # Modest sweeps: preserve intended constraints; invalid points are still retained.
        for value in [center * (1.005 if field == "lambda_eta_v" else 1.06), center * 0.96, center]:
            current = {**current, field: value}
            result.append(dict(current))
        segments.append({"stage": stage, "first": start, "last": len(result) - 1})
    return result, segments


def interpolate_rows(a, b, f):
    """Never bridge an invalid endpoint with an apparently valid point."""
    if f <= 1e-12:
        return [dict(p) for p in a]
    if f >= 1 - 1e-12:
        return [dict(p) for p in b]
    out = []
    for p, q in zip(a, b, strict=True):
        good = p["operating_valid"] and q["operating_valid"]
        physically_valid = p["valid"] and q["valid"]
        out.append(
            {
                k: (
                    p[k] + (q[k] - p[k]) * f
                    if good
                    and isinstance(p[k], (float, int))
                    and isinstance(q[k], (float, int))
                    and not isinstance(p[k], bool)
                    else p[k]
                )
                for k in p
            }
            | {"valid": physically_valid, "operating_valid": good}
        )
    return out


def render(data, target, seconds=15, fps=24):
    """Continuous color normalization, independent square axes and compact bar."""
    import imageio_ffmpeg

    dm.style.use("presentation")
    tokens = json.loads((ROOT / "plot_tokens.json").read_text())["roles"]
    blue = tokens["magnitude.stop_6"]["value"]
    ink = tokens["text.label"]["value"]
    gray = tokens["text.auxiliary"]["value"]
    # ColorBrewer Blues9 anchors, continuously interpolated; the installed
    # dartwork-mpl 0.4 does not ship a colormap named blues9.
    brewer = LinearSegmentedColormap.from_list(
        "ColorBrewerBlues9",
        ["#f7fbff", "#deebf7", "#c6dbef", "#9ecae1", "#6baed6", "#4292c6", "#2171b5", "#08519c", "#08306b"],
        N=4096,
    )
    cmap = LinearSegmentedColormap.from_list("Blues9_lightened_0_0.8", brewer(np.linspace(0, 0.8, 1024)), N=1024)
    plt.rcParams.update(
        {
            "font.family": tokens["font.latin"]["value"],
            "svg.fonttype": "none",
            "text.color": ink,
            "axes.labelcolor": ink,
        }
    )
    width, height = 1920, 800
    fig, axes = plt.subplots(1, 3, figsize=(1000 / 96, (1000 * height / width) / 96), dpi=width * 96 / 1000)
    # Epsilon prevents floating-point truncation from producing a 1919 px buffer.
    fig.set_size_inches((width + 0.01) / fig.dpi, (height + 0.01) / fig.dpi)
    fig.subplots_adjust(left=0.055, right=0.94, bottom=0.20, top=0.82, wspace=0.40)
    fig.patch.set_facecolor("#f8f7f6")
    pr, n = np.meshgrid(np.linspace(2, 7, 160), np.linspace(30, 110, 160))
    # Fixed normalizations cover every displayed state for each physical quantity.
    limits = []
    for i, key in enumerate(QUANTITIES):
        values = np.stack([OssorioR290Map(key)(pr, n) * s["scales"][FIELDS[i]] * 100 for s in data["snapshots"]])
        step = 5.0 if key != "f_loss" else 1.0
        lo = math.floor(values.min() / step) * step
        hi = math.ceil(values.max() / step) * step
        limits.append((lo, hi, step))
    # Explicit presentation choice: saturate colors outside 60–90%, retaining
    # the unmodified physical surface and iso-values beyond the color limits.
    limits[0] = (60.0, 90.0, 5.0)
    fixed_contours = {
        "eta_ov": np.arange(45, 106, 5),
        "eta_v": np.arange(85, 102, 2),
        "f_loss": np.array([5, 6, 7, 8, 9, 11, 13, 15, 17, 19]),
    }
    visible_counts = {key: [] for key in QUANTITIES}
    all_cop = [
        value
        for snapshot in data["snapshots"]
        for row in snapshot["catalogue"]
        if row["operating_valid"]
        for value in [row["COP_model"], row["COP_catalogue"]]
    ]
    cop_bounds = (math.floor(min(all_cop)), math.ceil(max(all_cop)), 1)
    curve_top = max(
        row["COP_model"] for snapshot in data["snapshots"] for row in snapshot["curve"] if row["operating_valid"]
    )
    curve_bounds = (0, max(2, math.ceil(curve_top / 2) * 2), 2)
    writer = imageio_ffmpeg.write_frames(
        str(target), (width, height), fps=fps, codec="libx264", quality=8, pix_fmt_out="yuv420p", macro_block_size=1
    )
    writer.send(None)
    frame_count = round(seconds * fps)
    barax = None
    for frame in range(frame_count):
        progress = frame / (frame_count - 1) * 3
        stage = min(int(progress), 2)
        local = min(progress - stage, 1.0) * 3
        left = min(int(local), 2) + 3 * stage
        fraction = min(local - int(local), 1.0)
        if frame == frame_count - 1:
            left = 8
            fraction = 1.0
        a, b = data["snapshots"][left : left + 2]
        scales = {k: a["scales"][k] + (b["scales"][k] - a["scales"][k]) * fraction for k in FIELDS}
        cat = interpolate_rows(a["catalogue"], b["catalogue"], fraction)
        curve = interpolate_rows(a["curve"], b["curve"], fraction)
        for ax in axes:
            ax.clear()
            ax.set_box_aspect(1)
            ax.patch.set_alpha(0)
        ax = axes[0]
        valid = [p for p in cat if p["valid"]]
        ax.plot(cop_bounds[:2], cop_bounds[:2], color=gray, lw=0.8)
        n_calibration = data.get("calibration_count", len(cat))
        for group, marker, fill, color, label in [
            (cat[:n_calibration], "o", blue, blue, "Calibration"),
            (cat[n_calibration:], "s", "none", ink, "Check"),
        ]:
            selected = [p for p in group if p["valid"]]
            if selected:
                ax.scatter(
                    [p["COP_catalogue"] for p in selected],
                    [p["COP_model"] for p in selected],
                    s=18,
                    marker=marker,
                    facecolors=fill,
                    edgecolors=color,
                    label=label,
                    clip_on=False,
                )
        rejected = [p for p in cat if p["operating_valid"] and not p["valid"]]
        if rejected:
            ax.scatter(
                [p["COP_catalogue"] for p in rejected],
                [p["COP_model"] for p in rejected],
                marker="x",
                s=22,
                color="#c93c37",
                label="Rejected / unverified",
                clip_on=False,
            )
            ax.text(0.04, 0.80, "Rejected / unverified state", transform=ax.transAxes, color="#c93c37", fontsize=8)
        ax.legend(loc="lower right", frameon=False, fontsize=8)

        spec(ax, "x", cop_bounds, "Catalogue heating COP")
        spec(ax, "y", cop_bounds, "Predicted heating COP")
        rmse = (
            np.sqrt(np.mean([(p["COP_model"] - p["COP_catalogue"]) ** 2 for p in valid]))
            if len(valid) == len(cat)
            else np.nan
        )
        rmse_label = f"{rmse:.3f}" if np.isfinite(rmse) else "—"
        ax.text(
            0.04,
            0.96,
            f"All-row RMSE = {rmse_label}\nValid = {len(valid)}/{len(cat)}",
            transform=ax.transAxes,
            va="top",
            fontsize=10,
        )
        ax = axes[1]
        ys = [p["COP_model"] if p["valid"] else np.nan for p in curve]
        ax.plot(data["PLR"], ys, color=blue, marker="o", markersize=3)
        valid_curve = [(plr, point) for plr, point in zip(data["PLR"], curve, strict=True) if point["valid"]]
        if valid_curve:
            maximum_plr, maximum_point = max(valid_curve, key=lambda pair: pair[0])
            ax.scatter(
                [maximum_plr],
                [maximum_point["COP_model"]],
                s=22,
                facecolors="white",
                edgecolors=blue,
                linewidths=0.8,
                zorder=6,
                clip_on=False,
            )

        bad_curve = [
            (plr, p) for plr, p in zip(data["PLR"], curve, strict=True) if p["operating_valid"] and not p["valid"]
        ]
        if bad_curve:
            ax.scatter(
                [plr for plr, p in bad_curve],
                [p["COP_model"] for plr, p in bad_curve],
                color="#c93c37",
                marker="x",
                s=16,
                clip_on=False,
            )

        spec(ax, "x", (0.4, 1.6, 0.2), "Part-load ratio")
        spec(ax, "y", curve_bounds, "Heating COP")
        ax.text(
            0.04,
            0.05,
            f"Valid PLR = {sum(p['valid'] for p in curve)}/{len(curve)}",
            transform=ax.transAxes,
            fontsize=8,
            color=gray,
        )
        ax = axes[2]
        key = QUANTITIES[stage]
        lo, hi, step = limits[stage]
        z = OssorioR290Map(key)(pr, n) * scales[FIELDS[stage]] * 100
        im = ax.pcolormesh(pr, n, z, cmap=cmap, norm=Normalize(lo, hi), shading="gouraud", rasterized=False)
        # These exact physical iso-values are fixed for the entire video.
        # Levels outside the current field are naturally absent, never renumbered.
        levels = fixed_contours[key]
        contours = ax.contour(pr, n, z, levels=levels, colors=ink, linewidths=0.4)
        visible_contours = sum(any(len(segment) >= 2 for segment in group) for group in contours.allsegs)
        assert 3 <= visible_contours <= 8, (frame, key, visible_contours)
        visible_counts[key].append(visible_contours)
        ax.clabel(contours, fontsize=7, fmt="%d")
        ax.plot(
            [p["pressure_ratio"] if p["valid"] else np.nan for p in curve],
            [p["speed_rps"] if p["valid"] else np.nan for p in curve],
            color=ink,
            marker="o",
            markersize=3,
            lw=1.1,
        )
        valid_curve = [(plr, point) for plr, point in zip(data["PLR"], curve, strict=True) if point["valid"]]
        if valid_curve:
            maximum_point = max(valid_curve, key=lambda pair: pair[0])[1]
            ax.scatter(
                [maximum_point["pressure_ratio"]],
                [maximum_point["speed_rps"]],
                s=22,
                facecolors="white",
                edgecolors=ink,
                linewidths=0.8,
                zorder=6,
                clip_on=False,
            )
        if bad_curve:
            ax.scatter(
                [p["pressure_ratio"] for plr, p in bad_curve],
                [p["speed_rps"] for plr, p in bad_curve],
                color="#c93c37",
                marker="x",
                s=16,
                clip_on=False,
            )
        spec(ax, "x", (2, 7, 1), "Pressure ratio")
        spec(ax, "y", (30, 110, 20), "Shaft speed [rev/s]")
        delta = b["scales"][FIELDS[stage]] - a["scales"][FIELDS[stage]]
        # All three title boxes share a bottom anchor and baseline font.
        common = {"fontsize": 10, "color": ink}
        triangle = TextArea(
            "▲" if delta > 0 else "▼", textprops={"fontsize": 10, "color": "#238636" if delta > 0 else "#c93c37"}
        )
        active_title = HPacker(
            children=[
                TextArea(f"{LABELS[stage]} (λ = {scales[FIELDS[stage]]:.2f}", textprops=common),
                triangle,
                TextArea(")", textprops=common),
            ],
            align="baseline",
            pad=0,
            sep=2,
        )
        titles = []
        for title_ax, title_box in zip(
            axes,
            [
                TextArea("Catalogue parity", textprops=common),
                TextArea(f"Indoor {data['indoor']:g} °C / Outdoor {data['outdoor']:g} °C", textprops=common),
                active_title,
            ],
            strict=True,
        ):
            title_ax.set_title("")
            title_artist = AnnotationBbox(
                title_box,
                (0.5, 1.04),
                xycoords="axes fraction",
                box_alignment=(0.5, 0),
                frameon=False,
                pad=0,
                annotation_clip=False,
            )
            title_ax.add_artist(title_artist)
            titles.append(title_artist)
        if barax is not None:
            barax.remove()
        fig.canvas.draw()
        title_bottoms = [title.get_window_extent(fig.canvas.get_renderer()).y0 for title in titles]
        assert max(title_bottoms) - min(title_bottoms) < 1, title_bottoms
        panel_bbox = ax.get_window_extent()
        for label in list(contours.labelTexts):
            bounds = label.get_window_extent(fig.canvas.get_renderer())
            if (
                bounds.x0 < panel_bbox.x0 + 2
                or bounds.x1 > panel_bbox.x1 - 2
                or bounds.y0 < panel_bbox.y0 + 2
                or bounds.y1 > panel_bbox.y1 - 2
            ):
                label.remove()
        box = ax.get_position()
        barax = fig.add_axes([box.x1 + (6 / 72 * fig.dpi / width), box.y0, box.width * 0.015, box.height * 0.5])
        cb = fig.colorbar(im, cax=barax, ticks=[lo, hi], extend="neither")
        cb.ax.tick_params(length=0, labelsize=8)
        cb.ax.set_title("(%)", fontsize=8)
        fig.suptitle(f"Stage {stage + 1}/3 · {LABELS[stage]}", y=0.98, fontsize=15)
        # Explicit disclosure avoids presenting oscillation as an optimization trace.
        for old in list(fig.texts)[1:]:
            old.remove()
        fig.text(
            0.5,
            0.085,
            data.get("catalogue_label", "Compressor calibration example"),
            ha="center",
            fontsize=8,
            color=gray,
        )
        fig.text(
            0.5,
            0.04,
            "Illustrative sweep · computed keyframes; transitions interpolated, feasibility unverified · not optimizer history",
            ha="center",
            fontsize=9,
            color=gray,
        )
        fig.canvas.draw()
        if frame in (0, frame_count // 2, frame_count - 1):
            suffix = "" if frame == frame_count - 1 else ("_initial" if frame == 0 else "_rejected")
            fig.savefig(
                target.with_name(target.stem + suffix).with_suffix(".png"), dpi=fig.dpi, facecolor=fig.get_facecolor()
            )
        pixels = np.asarray(fig.canvas.buffer_rgba())[:, :, :3].copy()
        assert pixels.shape == (height, width, 3), pixels.shape
        writer.send(pixels.tobytes())
    writer.close()
    encoded_frames, encoded_seconds = imageio_ffmpeg.count_frames_and_secs(str(target))
    assert encoded_frames == frame_count, (encoded_frames, frame_count)
    assert abs(encoded_seconds - seconds) < 1 / fps + 1e-6
    plt.close(fig)
    return {
        "frames": frame_count,
        "fps": fps,
        "duration_s": seconds,
        "size_px": [width, height],
        "normalization": dict(zip(QUANTITIES, limits, strict=True)),
        "colormap": {
            "source": "ColorBrewer Blues9 nine anchors",
            "continuous": True,
            "display_fraction": [0, 0.8],
            "dartwork_native": False,
        },
        "contour_labels": "fixed physical integer iso-values across every frame; three to eight visible",
        "visible_contour_counts": {
            key: {"min": min(values), "max": max(values), "frames": len(values)}
            for key, values in visible_counts.items()
        },
        "explicit_color_limit_override": {
            "eta_ov": [60, 90],
            "intent": "colors saturate outside 60–90%; physical surface and iso-lines remain unchanged",
        },
        "fixed_contour_levels": {key: levels.tolist() for key, levels in fixed_contours.items()},
        "titles": "shared baseline; inline colored direction triangle; lambda .2f",
        "PLR_markers": "maximum valid PLR is a white-filled circle on both COP curve and map trajectory; other valid markers filled; rejected/unverified points remain red crosses",
        "linear_axes": {
            "parity_x_and_y": cop_bounds,
            "PLR_x": (0.4, 1.6, 0.2),
            "PLR_COP_y": curve_bounds,
            "heatmap_x": (2, 7, 1),
            "heatmap_y": (30, 110, 20),
            "colorbars": {key: (lo, hi, hi - lo) for key, (lo, hi, step) in zip(QUANTITIES, limits, strict=True)},
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--seconds", type=float, default=15)
    parser.add_argument("--indoor", type=float, default=20)
    parser.add_argument("--outdoor", type=float, default=7)
    parser.add_argument("--output", type=Path, default=OUTPUT / "TMHP_catalogue_fitting_stages.mp4")
    args = parser.parse_args()
    report = json.loads((ROOT / "fit_report.json").read_text())
    assert report["applied"]
    final = report["applied_scales"]
    scales, segments = keyframes(final)
    # Show every selected catalogue row with its calibration/check identity retained.
    calibration_rows = points(catalogue("calibration"))
    rows = calibration_rows + points(catalogue("holdout"))
    recorded = [
        (p["T_indoor_C"], p["T_outdoor_C"], p["Q_requested_W"], p["COP_catalogue"]) for p in report["initial_points"]
    ]
    if len(calibration_rows) != len(recorded) or not np.allclose(calibration_rows, recorded, rtol=0, atol=1e-9):
        raise RuntimeError("fit_report.json belongs to a different catalogue case; rerun fitting first")
    rated = getattr(case_config, "HEATING_REFERENCE_W", EQUIPMENT_SETTINGS["hp_capacity"])
    repository = ROOT.parents[1]
    code_hashes = {
        relative: hashlib.sha256((repository / relative).read_bytes()).hexdigest()
        for relative in (
            "src/tmhp/calibration.py",
            "src/tmhp/air_source_heat_pump.py",
            "src/tmhp/compressor_baseline.py",
            "validation/compressor_fitting/case.py",
        )
    }
    signature = hashlib.sha256(
        json.dumps(
            [code_hashes, EQUIPMENT_SETTINGS, rows, final, scales, PLRS.tolist(), args.indoor, args.outdoor],
            sort_keys=True,
        ).encode()
    ).hexdigest()
    cache = ROOT / "video_keyframes.json"
    if cache.exists() and (data := json.loads(cache.read_text()))["signature"] == signature:
        print("Reuse verified model keyframes", flush=True)
    else:
        tasks = [(s, rows, args.indoor, args.outdoor, rated) for s in scales]
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            snapshots = []
            for i, snapshot in enumerate(pool.map(compute_snapshot, tasks)):
                snapshots.append(snapshot)
                print(f"Model keyframe {i + 1}/{len(tasks)}", flush=True)
        data = {
            "signature": signature,
            "code_hashes": code_hashes,
            "equipment": EQUIPMENT_SETTINGS,
            "catalogue_rows": rows,
            "catalogue_label": getattr(case_config, "ASSUMPTIONS", ["Compressor calibration example"])[0].split(":")[0],
            "calibration_count": len(calibration_rows),
            "versions": {
                name: version(name) for name in ("CoolProp", "numpy", "matplotlib", "dartwork-mpl", "imageio-ffmpeg")
            },
            "indoor": args.indoor,
            "outdoor": args.outdoor,
            "rated_heat_W": rated,
            "PLR": PLRS.tolist(),
            "segments": segments,
            "actual_fit_stage_keyframe_indices": [3, 6, 9],
            "snapshots": snapshots,
            "disclosure": "Illustrative oscillatory parameter sweep, not optimizer history. Actual model at each keyframe; numerical predictions interpolated in display frames and intermediate feasibility is unverified. Only endpoint-valid pairs are shown as valid transitions; rejected/unverified points use red crosses. Curve validity gaps are retained.",
        }
        cache.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")
    data.setdefault(
        "catalogue_label", getattr(case_config, "ASSUMPTIONS", ["Compressor calibration example"])[0].split(":")[0]
    )
    data.setdefault("actual_fit_stage_keyframe_indices", [3, 6, 9])
    data["disclosure"] = (
        "Illustrative oscillatory parameter sweep, not optimizer history. Actual model at each keyframe; numerical predictions interpolated in display frames and intermediate feasibility is unverified. Only endpoint-valid pairs are shown as valid transitions; rejected/unverified points use red crosses. Curve validity gaps are retained."
    )
    cache.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    manifest = render(data, args.output, args.seconds)
    args.output.with_suffix(".json").write_text(
        json.dumps({"source_signature": signature, "disclosure": data["disclosure"], **manifest}, indent=2) + "\n"
    )
    print(args.output, flush=True)


if __name__ == "__main__":
    main()
