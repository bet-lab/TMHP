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
from matplotlib import patheffects
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


def tracked_label_plan(segments, previous, frame, seed):
    """Project a tracked anchor onto the current physical iso-line."""
    pieces = [np.asarray(q) for q in segments if len(q) >= 2]
    normalized = [(q - [2, 30]) / [5, 80] for q in pieces]
    if previous is not None and previous["frame"] == frame - 1:
        target = np.asarray(previous["anchor"])
    else:
        # Prefer the branch facing the fixed global minimum; it survives while
        # the lower iso-value moves towards the boundary during scalar sweeps.
        target = np.asarray(seed)
    # Preserve branch identity before preferring interior placement. Otherwise a
    # tiny boundary branch can jump to a distant, unrelated visible branch.
    distances = [np.min(np.linalg.norm(q - target, axis=1)) for q in normalized]
    normalized = [normalized[int(np.argmin(distances))]]

    def candidates(interior):
        out = []
        for q in normalized:
            mask = np.all((q >= 0.045) & (q <= 0.955), axis=1) if interior else np.ones(len(q), dtype=bool)
            out.extend(q[mask])
            a, b = q[:-1], q[1:]
            keep = mask[:-1] & mask[1:]
            a, b = a[keep], b[keep]
            if len(a):
                v = b - a
                den = np.sum(v * v, axis=1)
                u = np.clip(np.sum((target - a) * v, axis=1) / np.maximum(den, 1e-20), 0, 1)
                out.extend(a + u[:, None] * v)
        return np.asarray(out)

    interior_points = candidates(True)
    all_points = candidates(False)
    boundary = not len(interior_points)
    if len(interior_points):
        interior_distance = np.min(np.linalg.norm(interior_points - target, axis=1))
        nearest_distance = np.min(np.linalg.norm(all_points - target, axis=1))
        # Preserve a tracked boundary branch rather than jump a label far along
        # the curve only to regain an interior text box.
        boundary = interior_distance > nearest_distance + 0.025
    points = all_points if boundary else interior_points
    anchor = points[np.argmin(np.linalg.norm(points - target, axis=1))]
    text = np.clip(anchor, 0.045, 0.955)
    delta = float(np.linalg.norm(text - previous["text"])) if previous and previous["frame"] == frame - 1 else None
    return {
        "frame": frame,
        "anchor": anchor.tolist(),
        "text": text.tolist(),
        "text_offset_axes": float(np.linalg.norm(text - anchor)),
        "leader_reason": "Tracked boundary iso-line has no nearby interior label anchor; offset text with leader"
        if boundary
        else None,
        "consecutive_displacement_axes": delta,
    }


def plan_labels(data, pr, speed, fixed_levels, frame_count):
    """Audit all physical labels before encoding, including natural lifetimes."""
    import contourpy

    previous = {}
    previous_visible = set()
    previous_key = None
    events = []
    plans = []
    statistics = {key: [] for key in QUANTITIES}
    for frame in range(frame_count):
        progress = frame / (frame_count - 1) * 3
        stage = min(int(progress), 2)
        local = min(progress - stage, 1) * 3
        left = min(int(local), 2) + 3 * stage
        fraction = min(local - int(local), 1)
        fraction = 0.5 - 0.5 * np.cos(np.pi * fraction)
        if frame == frame_count - 1:
            left = 8
            fraction = 1
        a, b = data["snapshots"][left : left + 2]
        key = QUANTITIES[stage]
        factor = a["scales"][FIELDS[stage]] + (b["scales"][FIELDS[stage]] - a["scales"][FIELDS[stage]]) * fraction
        field = OssorioR290Map(key)(pr, speed) * factor * 100
        generator = contourpy.contour_generator(x=pr, y=speed, z=field, line_type="Separate")
        labels = {}
        for level in fixed_levels[key]:
            pieces = generator.lines(float(level))
            if not any(len(q) >= 2 for q in pieces):
                continue
            identity = (key, int(level))
            seed = np.unravel_index(np.argmin(field), field.shape)
            seed = ((pr[seed] - 2) / 5, (speed[seed] - 30) / 80)
            entry = tracked_label_plan(pieces, previous.get(identity), frame, seed)
            point = np.asarray(entry["anchor"]) * [5, 80] + [2, 30]
            entry.update(quantity=key, iso_value=int(level), lambda_scale=float(factor))
            entry["physical_anchor_error_pct_points"] = abs(float(OssorioR290Map(key)(*point) * factor * 100 - level))
            assert entry["physical_anchor_error_pct_points"] < 0.02, entry
            previous[identity] = entry
            labels[int(level)] = entry
        assert 2 <= len(labels) <= 5, (frame, key, len(labels))
        current_visible = set(labels)
        if previous_key != key:
            events.append(
                {
                    "frame": frame,
                    "time_s": frame / 24,
                    "quantity": key,
                    "event": "stage_transition",
                    "visible_values": sorted(current_visible),
                    "reason": "Displayed physical quantity changes",
                }
            )
        else:
            for value in sorted(current_visible - previous_visible):
                events.append(
                    {
                        "frame": frame,
                        "time_s": frame / 24,
                        "quantity": key,
                        "event": "birth",
                        "iso_value": value,
                        "lambda_scale": float(factor),
                        "field_range": [float(field.min()), float(field.max())],
                        "reason": "Fixed physical iso-value enters the displayed field; a drawable contour segment exists",
                    }
                )
            for value in sorted(previous_visible - current_visible):
                events.append(
                    {
                        "frame": frame,
                        "time_s": frame / 24,
                        "quantity": key,
                        "event": "death",
                        "iso_value": value,
                        "lambda_scale": float(factor),
                        "field_range": [float(field.min()), float(field.max())],
                        "reason": "Fixed physical iso-value leaves the displayed field; no drawable contour segment remains",
                    }
                )
        previous_visible = current_visible
        previous_key = key
        statistics[key].append(len(labels))
        plans.append(labels)
    audit = {
        "per_frame": plans,
        "visible_counts": {key: {"min": min(v), "max": max(v), "frames": len(v)} for key, v in statistics.items()},
    }
    entries = [v for frame in plans for v in frame.values()]
    audit["max_anchor_error_pct_points"] = max(v["physical_anchor_error_pct_points"] for v in entries)
    audit["max_text_offset_axes"] = max(v["text_offset_axes"] for v in entries)
    audit["max_consecutive_displacement_axes"] = max(v["consecutive_displacement_axes"] or 0 for v in entries)
    audit["lifetime_events"] = events
    audit["artificial_label_deletions"] = 0
    audit["leader_instances"] = sum(bool(v["leader_reason"]) for v in entries)
    return plans, audit


def render(data, target, seconds=15, fps=24):
    """Continuous color normalization, independent square axes and compact bar."""
    import imageio_ffmpeg

    dm.style.use("presentation")
    tokens = json.loads((ROOT / "plot_tokens.json").read_text())["roles"]
    ink = tokens["text.label"]["value"]
    gray = tokens["text.auxiliary"]["value"]
    reference_stops = ["#E6F1FE", "#D1E5FD", "#B1D2FC", "#7BB2FA", "#4C8FEC"]
    cmap = LinearSegmentedColormap.from_list("reference_magnitude_light", reference_stops, N=1024)
    catalogue_teal, plr_blue = "#188179", "#144DA6"
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
        "eta_ov": np.array([55, 65, 70, 80, 90]),
        "eta_v": np.array([90, 93, 96, 99]),
        "f_loss": np.array([6, 8, 11, 14, 17]),
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
    frame_count = round(seconds * fps)
    label_plans, label_audit = plan_labels(data, pr, n, fixed_contours, frame_count)
    print("Label geometry precheck:", {k: v for k, v in label_audit.items() if k != "per_frame"}, flush=True)
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
        fraction = 0.5 - 0.5 * np.cos(np.pi * fraction)
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
            (cat[:n_calibration], "o", catalogue_teal, catalogue_teal, "Calibration"),
            (cat[n_calibration:], "s", "none", catalogue_teal, "Check"),
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
        ax.plot(data["PLR"], ys, color=plr_blue, marker="o", markersize=3)
        valid_curve = [(plr, point) for plr, point in zip(data["PLR"], curve, strict=True) if point["valid"]]
        if valid_curve:
            maximum_plr, maximum_point = max(valid_curve, key=lambda pair: pair[0])
            ax.scatter(
                [maximum_plr],
                [maximum_point["COP_model"]],
                s=22,
                facecolors="white",
                edgecolors=plr_blue,
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
        assert 2 <= visible_contours <= 5, (frame, key, visible_contours)
        visible_counts[key].append(visible_contours)
        label_artists = []
        assert len(label_plans[frame]) == visible_contours
        for level, entry in label_plans[frame].items():
            point = np.asarray(entry["anchor"]) * [5, 80] + [2, 30]
            if entry["leader_reason"]:
                artist = ax.annotate(
                    str(level),
                    xy=point,
                    xycoords="data",
                    xytext=entry["text"],
                    textcoords="axes fraction",
                    ha="center",
                    va="center",
                    fontsize=7,
                    color=ink,
                    arrowprops={"arrowstyle": "-", "color": ink, "lw": 0.35, "shrinkA": 3, "shrinkB": 0},
                    annotation_clip=False,
                )
                label_artists.append(artist)
            else:
                artist = ax.text(
                    *point,
                    str(level),
                    ha="center",
                    va="center",
                    fontsize=7,
                    color=ink,
                    path_effects=[patheffects.withStroke(linewidth=1.5, foreground=(1, 1, 1, 0.65))],
                )
                label_artists.append(artist)
        assert len(label_artists) == visible_contours, (frame, len(label_artists), visible_contours)

        ax.plot(
            [p["pressure_ratio"] if p["valid"] else np.nan for p in curve],
            [p["speed_rps"] if p["valid"] else np.nan for p in curve],
            color=plr_blue,
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
                edgecolors=plr_blue,
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
        for label in label_artists:
            bounds = label.get_window_extent(fig.canvas.get_renderer())
            assert (
                bounds.x0 >= panel_bbox.x0 - 0.5
                and bounds.x1 <= panel_bbox.x1 + 0.5
                and bounds.y0 >= panel_bbox.y0 - 0.5
                and bounds.y1 <= panel_bbox.y1 + 0.5
            ), (frame, bounds, panel_bbox)
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
        if frame in (0, frame_count // 3, frame_count // 2, frame_count - 1):
            suffix = (
                ""
                if frame == frame_count - 1
                else ("_initial" if frame == 0 else ("_middle" if frame == frame_count // 3 else "_rejected"))
            )
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
            "source": "Exact reference lambda_figures.py Paper magnitude.stop_1..5",
            "stops": reference_stops,
            "continuous": True,
        },
        "semantic_colors": {"catalogue": catalogue_teal, "PLR_curve_and_trajectory": plr_blue, "invalid": "#c93c37"},
        "label_continuity_audit": label_audit,
        "contour_labels": "fixed physical integer iso-values across every frame; two to five visible",
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
    pending = args.output.with_name(args.output.stem + "__pending" + args.output.suffix)
    manifest = render(data, pending, args.seconds)
    from PIL import Image

    images = []
    for suffix in ("_initial", "_middle", "_rejected", ""):
        source = pending.with_name(pending.stem + suffix).with_suffix(".png")
        destination = args.output.with_name(args.output.stem + suffix).with_suffix(".png")
        source.replace(destination)
        images.append(Image.open(destination).convert("RGB"))
    sheet = Image.new("RGB", (images[0].width * 2, images[0].height * 2))
    for i, picture in enumerate(images):
        sheet.paste(picture, ((i % 2) * picture.width, (i // 2) * picture.height))
    sheet.save(args.output.with_name(args.output.stem + "_contact").with_suffix(".png"))
    report_path = args.output.with_suffix(".json")
    temporary_report = report_path.with_name(report_path.name + ".pending")
    temporary_report.write_text(
        json.dumps(
            {
                "source_signature": signature,
                "disclosure": data["disclosure"],
                "time_interpolation": "cosine ease between actual model keyframes",
                **manifest,
            },
            indent=2,
        )
        + "\n"
    )
    temporary_report.replace(report_path)
    pending.replace(args.output)
    print(args.output, flush=True)


if __name__ == "__main__":
    main()
