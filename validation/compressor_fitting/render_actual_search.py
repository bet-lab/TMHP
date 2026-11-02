"""Replay actual public fitting evaluations without inventing intermediate candidates.

Input is run_search_trace.py's fit_report.json. Every displayed state is a recorded
fit call; repeated frames control presentation timing, not simulated solver time.
The four calibration catalogue rows are shown; holdout observations never enter
this video's fitting objective. Undefined RMSE is displayed separately from the
finite numerical penalty returned to the optimizer.
"""

import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path

import dartwork_mpl as dm
import imageio_ffmpeg
import matplotlib

matplotlib.use("Agg")
import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
import numpy as np
from case import ROOT
from matplotlib.colors import LinearSegmentedColormap, Normalize
from render_fitting_video import reference_video_style_spec, spec, tracked_label_plan

from tmhp.compressor_baseline import OssorioR290Map

OUTPUT = Path("/home/habin/papers/temp/261102 presentation")
PALETTE = ["#E6F1FE", "#D1E5FD", "#B1D2FC", "#7BB2FA", "#4C8FEC"]
DECISIONS = {
    "objective_returned": "COP objective evaluated",
    "retained": "Current value retained",
    "selected": "Selected for this stage",
    "rejected": "Rejected by constraints",
    "applied": "Final parameters applied",
    "evaluated": "Baseline evaluated",
    "not_applied": "Parameters not applied",
}
COLORS = dict(catalogue="#188179", search="#AE5214", invalid="#BD3D3D", ink="#111923", context="#68717D")
PHASES = {
    "baseline": "Baseline",
    "cop_grid": "COP fitting · coarse grid",
    "cop_optimizer": "COP fitting · bounded search",
    "cop_optimizer_result": "COP fitting · returned optimum",
    "cop_selection": "COP fitting · select candidate",
    "volumetric_check": "Speed feasibility · check current value",
    "volumetric_repair": "Speed feasibility · test candidate",
    "volumetric_selection": "Speed feasibility · stage result",
    "loss_check": "Discharge feasibility · check current value",
    "loss_repair": "Discharge feasibility · test candidate",
    "loss_selection": "Discharge feasibility · stage result",
    "final": "Apply fitting result",
}
KEYS = {"eta_ov": "lambda_eta_ov", "eta_v": "lambda_eta_v", "f_loss": "lambda_f_loss"}
TITLES = {"eta_ov": "Overall efficiency", "eta_v": "Volumetric efficiency", "f_loss": "Heat loss fraction"}
LEVELS = {
    "eta_ov": np.arange(26, 105, 6),
    "eta_v": np.array([90, 93, 96, 99]),
    "f_loss": np.array([6, 8, 10, 13, 16, 19, 22, 25]),
}
BOUNDS = {"eta_ov": (60, 90), "eta_v": (85, 100), "f_loss": (4, 19)}


def style_helper():
    """Load the same presentation helper used by the approved reference video."""
    path = Path("/home/habin/papers/.agents/assets/presentation-figure.py")
    module_spec = importlib.util.spec_from_file_location("reference_presentation", path)
    helper = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(helper)
    helper.start_presentation_figure("Paper")
    return helper


def quantity(event):
    """Select the parameter actually inspected by the recorded fitting phase."""
    phase = event["phase"]
    if phase.startswith("volumetric"):
        return "eta_v"
    if phase.startswith("loss") or phase == "final":
        return "f_loss"
    return "eta_ov"


def limits(values, interval=1.0, include_zero=False):
    """Choose explicit endpoint-inclusive linear ticks covering finite observations."""
    finite = [float(v) for v in values if v is not None and math.isfinite(v)]
    low = 0 if include_zero else math.floor(min(finite, default=0) / interval) * interval
    high = math.ceil(max(finite, default=1) / interval) * interval
    return low, max(high, low + interval), interval


def load_trace(path):
    """Refuse unordered or nonfinite traces instead of inferring search history."""
    payload = json.loads(path.read_text())
    report = payload.get("report", payload)
    events = report["trace"]
    assert events and [e["sequence"] for e in events] == list(range(1, len(events) + 1))
    assert report["applied"] and events[-1]["decision"] == "applied"
    assert len({e["candidate_id"] for e in events}) == report["evaluations"]
    return report, events


def render(source, target, fps=24):
    """Encode actual evaluation states and audit every visible map and panel title."""
    import contourpy

    report, events = load_trace(source)
    helper = style_helper()
    presentation_style = reference_video_style_spec()
    dm.style.use("presentation")
    plt.rcParams.update({"font.family": "Inter", "text.color": COLORS["ink"], "axes.labelcolor": COLORS["ink"]})
    cmap = LinearSegmentedColormap.from_list("reference_magnitude_light", PALETTE, N=1024)
    pr, speed = np.meshgrid(np.linspace(2, 7, 121), np.linspace(30, 110, 101))
    parity = limits([v for e in events for p in e["points"] for v in (p["COP_catalogue"], p["COP_model"])])
    errors = [e["objective_rmse"] for e in events if e["objective_rmse"] is not None]
    rmse_step = 0.5 if max(errors, default=1) <= 3 else 1.0
    error_bounds = limits(errors, rmse_step, include_zero=True)
    width, height = 1920, 800
    fig, axes = plt.subplots(1, 3, figsize=(1000 / 96, (1000 * height / width) / 96), dpi=width * 96 / 1000)
    fig.set_size_inches((width + 0.01) / fig.dpi, (height + 0.01) / fig.dpi)
    fig.subplots_adjust(left=0.055, right=0.94, bottom=0.24, top=0.80, wspace=0.40)
    fig.patch.set_facecolor((249 / 255, 248 / 255, 246 / 255))
    target.parent.mkdir(parents=True, exist_ok=True)
    pending = target.with_name(target.stem + "__pending.mp4")
    writer = imageio_ffmpeg.write_frames(
        str(pending), (width, height), fps=fps, codec="libx264", pix_fmt_out="yuv420p", quality=8, macro_block_size=1
    )
    writer.send(None)
    audit = []
    previous = {}
    frame_total = 0
    bar = None
    try:
        for index, event in enumerate(events):
            for ax in axes:
                ax.clear()
                ax.set_box_aspect(1)
            key = quantity(event)
            field_key = KEYS[key]
            scales = event["scales"]
            ax = axes[0]
            ax.plot(parity[:2], parity[:2], color=COLORS["context"], lw=0.8)
            for valid, marker in [(True, "o"), (False, "x")]:
                rows = [p for p in event["points"] if p["valid"] == valid and p["COP_model"] is not None]
                if rows:
                    ax.scatter(
                        [p["COP_catalogue"] for p in rows],
                        [p["COP_model"] for p in rows],
                        marker=marker,
                        s=22,
                        color=COLORS["catalogue"] if valid else COLORS["invalid"],
                        zorder=4,
                    )
            spec(ax, "x", parity, "Catalogue heating COP")
            spec(ax, "y", parity, "Predicted heating COP")
            rms = event["objective_rmse"]
            status = f"COP RMSE = {rms:.3f}" if rms is not None else "COP RMSE = unavailable"
            ax.text(
                0.04,
                0.96,
                f"{status}\nValid = {sum(p['valid'] for p in event['points'])}/{len(event['points'])}",
                transform=ax.transAxes,
                va="top",
                fontsize=dm.fs(-1),
            )
            if event["constraint_violations"]:
                reasons = ", ".join(v.replace("_", " ").capitalize() for v in event["constraint_violations"])
                ax.text(
                    0.04,
                    0.04,
                    reasons.replace(", ", "\n"),
                    transform=ax.transAxes,
                    fontsize=dm.fs(-3),
                    color=COLORS["invalid"],
                )
            ax = axes[1]
            zoomed = event["phase"] in {"cop_optimizer", "cop_optimizer_result", "cop_selection"}
            x_bounds = (0, 2, 0.5) if key == "f_loss" else (1.25, 1.5, 0.05) if zoomed else (0.5, 1.5, 0.25)
            y_bounds = (0.30, 0.45, 0.05) if zoomed else (0.33, 0.37, 0.01) if key == "f_loss" else error_bounds
            same = [e for e in events[: index + 1] if quantity(e) == key and not e["cache_hit"]]
            plotted = [
                e
                for e in same
                if e["objective_rmse"] is not None
                and x_bounds[0] <= e["scales"][field_key] <= x_bounds[1]
                and y_bounds[0] <= e["objective_rmse"] <= y_bounds[1]
            ]
            if plotted:
                ax.scatter(
                    [e["scales"][field_key] for e in plotted],
                    [e["objective_rmse"] for e in plotted],
                    color=COLORS["context"],
                    s=14,
                    label="Evaluated (zoom)" if zoomed else "Evaluated",
                )
            if rms is not None:
                ax.scatter(
                    [scales[field_key]],
                    [rms],
                    marker="D",
                    color=COLORS["search"],
                    s=36,
                    zorder=6,
                    label="Current candidate",
                )
            else:
                ax.text(
                    0.04,
                    0.96,
                    f"λ = {scales[field_key]:.5f}\nRMSE unavailable",
                    transform=ax.transAxes,
                    va="top",
                    color=COLORS["invalid"],
                    fontsize=dm.fs(-1),
                )
            spec(ax, "x", x_bounds, "Scale factor λ")
            spec(ax, "y", y_bounds, "Calibration COP RMSE")
            if rms is not None:
                ax.text(
                    0.04,
                    0.96,
                    f"λ = {scales[field_key]:.6f}\nRMSE = {rms:.6f}",
                    transform=ax.transAxes,
                    va="top",
                    fontsize=dm.fs(-2),
                )
            assert x_bounds[0] <= scales[field_key] <= x_bounds[1]
            assert rms is None or y_bounds[0] <= rms <= y_bounds[1]
            ax.legend(loc="upper right", frameon=False, fontsize=dm.fs(-2))
            ax.text(
                0.04,
                0.04,
                DECISIONS[event["decision"]] + ("\nCached result" if event["cache_hit"] else ""),
                transform=ax.transAxes,
                fontsize=dm.fs(-2),
            )
            ax = axes[2]
            z = OssorioR290Map(key)(pr, speed) * scales[field_key] * 100
            lo, hi = BOUNDS[key]
            image = ax.pcolormesh(pr, speed, z, cmap=cmap, norm=Normalize(lo, hi), shading="gouraud")
            contour = ax.contour(pr, speed, z, levels=LEVELS[key], colors=COLORS["ink"], linewidths=0.4)
            generator = contourpy.contour_generator(x=pr, y=speed, z=z, line_type="Separate")
            labels = []
            label_records = []
            seed_index = np.unravel_index(np.argmin(z), z.shape)
            seed = ((pr[seed_index] - 2) / 5, (speed[seed_index] - 30) / 80)
            for level in LEVELS[key]:
                segments = generator.lines(float(level))
                if not any(len(p) >= 2 for p in segments):
                    continue
                identity = (key, int(level))
                plan = tracked_label_plan(segments, previous.get(identity), index, seed)
                previous[identity] = plan
                anchor = np.asarray(plan["anchor"]) * [5, 80] + [2, 30]
                labels.append(float(level))
                anchor_error = abs(float(OssorioR290Map(key)(*anchor) * scales[field_key] * 100 - level))
                assert anchor_error < 0.02, (event["sequence"], level, anchor_error)
                label_records.append(
                    dict(
                        level=float(level),
                        anchor=anchor.tolist(),
                        physical_error_pct_points=anchor_error,
                        text_offset_axes=plan["text_offset_axes"],
                        leader_reason=plan["leader_reason"],
                    )
                )
                text = ax.annotate(
                    f"{level:g}",
                    xy=anchor,
                    xytext=plan["text"],
                    textcoords="axes fraction",
                    ha="center",
                    va="center",
                    fontsize=dm.fs(-3),
                    color=COLORS["ink"],
                    arrowprops={"arrowstyle": "-", "color": COLORS["ink"], "lw": 0.3}
                    if plan["leader_reason"]
                    else None,
                )
                text.set_path_effects([pe.withStroke(linewidth=1.2, foreground="white")])
            visible = sum(any(len(segment) >= 2 for segment in group) for group in contour.allsegs)
            assert visible == len(labels) and 2 <= visible <= 5, (event["sequence"], key, labels)
            spec(ax, "x", (2, 7, 1), "Pressure ratio")
            spec(ax, "y", (30, 110, 20), "Shaft speed [rev/s]")
            for valid, marker in [(True, "o"), (False, "x")]:
                rows = [
                    p
                    for p in event["points"]
                    if p["valid"] == valid and p["pressure_ratio"] is not None and p["speed_rps"] is not None
                ]
                for point in rows:
                    assert 2 <= point["pressure_ratio"] <= 7 and 30 <= point["speed_rps"] <= 110
                if rows:
                    ax.scatter(
                        [p["pressure_ratio"] for p in rows],
                        [p["speed_rps"] for p in rows],
                        marker=marker,
                        s=22,
                        color=COLORS["catalogue"] if valid else COLORS["invalid"],
                        zorder=5,
                    )
            helper.set_panel_titles(
                axes,
                [
                    f"Catalogue parity · {len(event['points'])} fitting rows",
                    "Actual λ search · zoom" if zoomed else "Actual λ search",
                    f"{TITLES[key]} (λ = {scales[field_key]:.2f})",
                ],
            )
            if bar is not None:
                bar.ax.remove()
            fig.canvas.draw()
            bar = helper.add_slide_colorbar(fig, ax, image, minimum=lo, maximum=hi, label="(%)")
            assert axes[0].title.get_fontsize() == presentation_style["title_pt"]
            ax_box, cb_box = ax.get_position(), bar.ax.get_position()
            assert abs(cb_box.height / ax_box.height - 0.5) < 1e-6
            assert abs(cb_box.width / ax_box.width - 0.015) < 1e-6
            phase = PHASES.get(event["phase"], event["phase"])
            fig.suptitle(phase, y=0.98, fontsize=dm.fs(2), color=COLORS["ink"])
            for artist in list(fig.texts):
                if artist is not fig._suptitle:
                    artist.remove()
            fig.text(
                0.5,
                0.125,
                f"Call {event['sequence']}/{len(events)} · Unique candidate {event['candidate_id']}/{report['evaluations']} · λov {scales['lambda_eta_ov']:.5f} · λv {scales['lambda_eta_v']:.3f} · λloss {scales['lambda_f_loss']:.3f}",
                ha="center",
                fontsize=dm.fs(-2),
                color=COLORS["context"],
            )
            fig.text(
                0.5,
                0.065,
                "Fujitsu ASYG30LMTA/AOYG30LMTA · In 20 °C / Out 7, 10, 15, 20 °C · COP = TC/IP",
                ha="center",
                fontsize=dm.fs(-2),
                color=COLORS["context"],
            )
            fig.text(
                0.5,
                0.025,
                "Actual evaluation order · time compressed · discrete recorded candidates · 16 holdout rows excluded from fitting",
                ha="center",
                fontsize=dm.fs(-2),
                color=COLORS["context"],
            )
            fig.canvas.draw()
            titles = [a.title.get_window_extent(fig.canvas.get_renderer()).y0 for a in axes]
            assert max(titles) - min(titles) < 1
            rgba = np.asarray(fig.canvas.buffer_rgba())
            assert rgba.shape == (height, width, 4)
            repeats = round(fps * (2.5 if index == len(events) - 1 else 0.45 if not event["cache_hit"] else 0.30))
            for _ in range(repeats):
                writer.send(rgba[:, :, :3].tobytes())
            frame_total += repeats
            record = dict(
                sequence=event["sequence"],
                candidate_id=event["candidate_id"],
                cache_hit=event["cache_hit"],
                phase=event["phase"],
                scales=scales,
                visible_contours=labels,
                label_records=label_records,
                x_bounds=x_bounds,
                y_bounds=y_bounds,
                zoomed=zoomed,
                first_frame=frame_total - repeats,
                frame_count=repeats,
            )
            audit.append(record)
            if index in (0, len(events) // 2, len(events) - 1):
                suffix = "" if index == len(events) - 1 else "_initial" if index == 0 else "_middle"
                fig.savefig(
                    target.with_name(target.stem + suffix).with_suffix(".png"),
                    dpi=fig.dpi,
                    facecolor=fig.get_facecolor(),
                )
            print(f"Rendered actual call {index + 1}/{len(events)}", flush=True)
    finally:
        writer.close()
        plt.close(fig)
    reader = imageio_ffmpeg.read_frames(str(pending), pix_fmt="rgb24")
    metadata = next(reader)
    decoded = sum(1 for _ in reader)
    assert decoded == frame_total and tuple(metadata["size"]) == (width, height)
    pending.replace(target)
    manifest = dict(
        source=str(source),
        source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        presentation_style=presentation_style,
        actual_search=True,
        interpolated_candidates=False,
        fps=fps,
        frames=decoded,
        duration_s=decoded / fps,
        size_px=[width, height],
        calls=len(events),
        unique_candidates=report["evaluations"],
        palette=PALETTE,
        semantic_colors=COLORS,
        parity_axes=parity,
        objective_axes=error_bounds,
        normalization=BOUNDS,
        final_scales=report["applied_scales"],
        final_rmse=report["final_cop_rmse"],
        events=audit,
    )
    target.with_suffix(".json").write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n")
    print(json.dumps({k: v for k, v in manifest.items() if k != "events"}), flush=True)


def main():
    """Replay the saved actual trace; fitting must be run explicitly beforehand."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "search_trace" / "fit_report.json")
    parser.add_argument("--output", type=Path, default=OUTPUT / "TMHP_catalogue_actual_search.mp4")
    args = parser.parse_args()
    render(args.source, args.output)


if __name__ == "__main__":
    main()
