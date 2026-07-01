"""
Mollier diagram visualization functions.
"""

from functools import lru_cache
from typing import cast

import CoolProp.CoolProp as CP
import dartwork_mpl as dm
import matplotlib.axes as maxes
import matplotlib.figure as mfigure
import numpy as np

from . import calc_util as cu


def _draw_cycle_lines_and_annotations(
    ax: maxes.Axes,
    pts_x: dict[str, float],
    pts_y: dict[str, float],
    is_on: bool,
    color_sat_liq: str,
    color_sat_vap: str,
    line_color: str = "oc.gray5",
    color_on: str = "black",
    color_off: str = "oc.gray6",
    tol_atol: float = 0.1,
    tol_y_atol: float | None = None,
    point_labels: dict[str, str] | None = None,
) -> None:
    """Draw heat pump cycle lines, scatter markers, and aligned text annotations."""
    cycle_markerfacecolor = color_on if is_on else color_off
    cycle_markeredgecolor = color_on if is_on else color_off

    _tol_y = tol_y_atol if tol_y_atol is not None else tol_atol

    def points_are_close(x1, y1, x2, y2, tol=tol_atol):
        if np.isnan(x1) or np.isnan(y1) or np.isnan(x2) or np.isnan(y2):
            return False
        return abs(x1 - x2) < tol and abs(y1 - y2) < _tol_y

    def draw_segment(k1, k2):
        if not (np.isnan(pts_x[k1]) or np.isnan(pts_x[k2]) or np.isnan(pts_y[k1]) or np.isnan(pts_y[k2])):
            if points_are_close(pts_x[k1], pts_y[k1], pts_x[k2], pts_y[k2], tol=tol_atol):
                ax.plot(
                    pts_x[k1],
                    pts_y[k1],
                    marker="o",
                    linestyle="None",
                    markerfacecolor=cycle_markerfacecolor,
                    markeredgecolor=cycle_markeredgecolor,
                    markeredgewidth=dm.lw(0),
                    markersize=2.5,
                    zorder=1,
                )
            else:
                ax.plot(
                    [pts_x[k1], pts_x[k2]],
                    [pts_y[k1], pts_y[k2]],
                    color=line_color,
                    linewidth=dm.lw(0),
                    linestyle=":",
                    zorder=1,
                )

    draw_segment("4", "1_star")
    draw_segment("1_star", "1")
    draw_segment("1", "2")
    draw_segment("2", "2_star")
    draw_segment("2_star", "3_star")
    draw_segment("3_star", "3")
    draw_segment("3", "4")

    points_list = []
    if points_are_close(pts_x["1_star"], pts_y["1_star"], pts_x["1"], pts_y["1"], tol_atol):
        points_list.append((pts_x["1"], pts_y["1"], "1"))
    else:
        if not (np.isnan(pts_x["1_star"]) or np.isnan(pts_y["1_star"])):
            points_list.append((pts_x["1_star"], pts_y["1_star"], "1'"))
        if not (np.isnan(pts_x["1"]) or np.isnan(pts_y["1"])):
            points_list.append((pts_x["1"], pts_y["1"], "1"))

    if points_are_close(pts_x["2"], pts_y["2"], pts_x["2_star"], pts_y["2_star"], tol_atol):
        points_list.append((pts_x["2"], pts_y["2"], "2"))
    else:
        if not (np.isnan(pts_x["2"]) or np.isnan(pts_y["2"])):
            points_list.append((pts_x["2"], pts_y["2"], "2"))
        if not (np.isnan(pts_x["2_star"]) or np.isnan(pts_y["2_star"])):
            points_list.append((pts_x["2_star"], pts_y["2_star"], "2'"))

    if points_are_close(pts_x["3_star"], pts_y["3_star"], pts_x["3"], pts_y["3"], tol_atol):
        points_list.append((pts_x["3"], pts_y["3"], "3"))
    else:
        if not (np.isnan(pts_x["3_star"]) or np.isnan(pts_y["3_star"])):
            points_list.append((pts_x["3_star"], pts_y["3_star"], "3'"))
        if not (np.isnan(pts_x["3"]) or np.isnan(pts_y["3"])):
            points_list.append((pts_x["3"], pts_y["3"], "3"))

    if not (np.isnan(pts_x["4"]) or np.isnan(pts_y["4"])):
        points_list.append((pts_x["4"], pts_y["4"], "4"))

    text_cfg = {
        "1": (4, 0, "left", "center"),
        "2": (4, 0, "left", "center"),
        "3": (-4, 0, "right", "center"),
        "4": (-4, 0, "right", "center"),
    }

    if point_labels is None:
        point_labels = {"1": "cmp,in", "2": "cmp,out", "3": "exp,in", "4": "exp,out"}

    fig = ax.figure

    for x_val, y_val, key in points_list:
        if key in ["1'", "2'"]:
            ax.plot(
                x_val,
                y_val,
                marker="o",
                markersize=2.5,
                markerfacecolor="white",
                markeredgecolor=color_sat_vap,
                markeredgewidth=dm.lw(0),
                zorder=2,
            )
            continue
        elif key == "3'":
            ax.plot(
                x_val,
                y_val,
                marker="o",
                markersize=2.5,
                markerfacecolor="white",
                markeredgecolor=color_sat_liq,
                markeredgewidth=dm.lw(0),
                zorder=2,
            )
            continue

        ax.plot(
            x_val,
            y_val,
            marker="o",
            markersize=2.5,
            markerfacecolor=cycle_markerfacecolor,
            markeredgecolor=cycle_markeredgecolor,
            markeredgewidth=dm.lw(0),
            zorder=2,
        )

        dx, dy, ha, va = text_cfg.get(key, (0, 4, "center", "bottom"))
        text_str = str(point_labels.get(key, key))

        if fig is not None:
            cfig = cast(mfigure.Figure, fig)
            offset = dm.make_offset(dx, dy, cfig)
            ax.text(x_val, y_val, text_str, transform=ax.transData + offset, ha=ha, va=va, fontsize=dm.fs(-2))
        else:
            ax.annotate(
                text_str, (x_val, y_val), xytext=(dx, dy), textcoords="offset points", ha=ha, va=va, fontsize=dm.fs(-2)
            )


@lru_cache(maxsize=8)
def _get_saturation_curves(refrigerant: str):
    T_min = CP.PropsSI("Tmin", refrigerant)
    T_crit = CP.PropsSI("Tcrit", refrigerant)
    temps_K = np.linspace(T_min + 1, T_crit - 0.5, 10000)
    temps = [cu.K2C(T) for T in temps_K]
    h_liq = [CP.PropsSI("H", "T", T, "Q", 0, refrigerant) / 1000 for T in temps_K]
    h_vap = [CP.PropsSI("H", "T", T, "Q", 1, refrigerant) / 1000 for T in temps_K]
    p_sat = [CP.PropsSI("P", "T", T, "Q", 0, refrigerant) / 1000 for T in temps_K]
    s_liq = [CP.PropsSI("S", "T", T, "Q", 0, refrigerant) / 1000 for T in temps_K]
    s_vap = [CP.PropsSI("S", "T", T, "Q", 1, refrigerant) / 1000 for T in temps_K]
    try:
        h_crit = CP.PropsSI("H", "T", T_crit, "Q", 0, refrigerant) / 1000
        p_crit = CP.PropsSI("P", "T", T_crit, "Q", 0, refrigerant) / 1000
        s_crit = CP.PropsSI("S", "T", T_crit, "Q", 0, refrigerant) / 1000
        temps.append(cu.K2C(T_crit))
        h_liq.append(h_crit)
        h_vap.append(h_crit)
        p_sat.append(p_crit)
        s_liq.append(s_crit)
        s_vap.append(s_crit)
    except Exception:
        pass
    return temps, h_liq, h_vap, p_sat, s_liq, s_vap


REF_LIMITS: dict[str, dict[str, dict[str, float]]] = {
    "R410A": {
        "th": {"xmin": 0.0, "xmax": 700.0, "ymin": -40.0, "ymax": 140.0},
        "ph": {"xmin": 0.0, "xmax": 700.0, "ymin": 100.0, "ymax": 10000.0},
        "ts": {"xmin": 0.0, "xmax": 3.0, "ymin": -40.0, "ymax": 140.0},
    },
    "R134a": {
        "th": {"xmin": 0.0, "xmax": 600.0, "ymin": -40.0, "ymax": 120.0},
        "ph": {"xmin": 0.0, "xmax": 600.0, "ymin": 100.0, "ymax": 10000.0},
        "ts": {"xmin": 0.0, "xmax": 2.5, "ymin": -40.0, "ymax": 120.0},
    },
    "R32": {
        "th": {"xmin": 0.0, "xmax": 800.0, "ymin": -40.0, "ymax": 200.0},
        "ph": {"xmin": 0.0, "xmax": 800.0, "ymin": 100.0, "ymax": 10000.0},
        "ts": {"xmin": 0.0, "xmax": 3.0, "ymin": -40.0, "ymax": 200.0},
    },
    "R290": {
        "th": {"xmin": 0.0, "xmax": 850.0, "ymin": -40.0, "ymax": 140.0},
        "ph": {"xmin": 0.0, "xmax": 850.0, "ymin": 100.0, "ymax": 10000.0},
        "ts": {"xmin": 0.0, "xmax": 3.0, "ymin": -40.0, "ymax": 140.0},
    },
}


def plot_th_diagram(
    ax: maxes.Axes,
    result: dict[str, float],
    refrigerant: str,
    T_cond_bound: dict[str, float | str] | None = None,
    T_evap_bound: dict[str, float | str] | None = None,
    fontsize: float | None = None,
    tick_pad: float | None = None,
    sat_liquid_label: str = "Sat. liquid",
    sat_vapor_label: str = "Sat. vapor",
    ref_cycle_label: str = "Ref. cycle",
    point_labels: dict[str, str] | None = None,
) -> None:
    """Plot T-h diagram on given axis."""
    color1, color2, color3, color4, line_color = "oc.blue5", "oc.red5", "black", "oc.gray6", "oc.gray5"
    limits = REF_LIMITS.get(refrigerant, {"th": {"xmin": 200.0, "xmax": 750.0, "ymin": -40.0, "ymax": 160.0}})["th"]

    temps, h_liq, h_vap, _, _, _ = _get_saturation_curves(refrigerant)

    h1_star = result.get("h_ref_evap_sat [J/kg]", result.get("h1_star [J/kg]", np.nan)) * cu.J2kJ
    h1 = result.get("h_ref_cmp_in [J/kg]", result.get("h1 [J/kg]", np.nan)) * cu.J2kJ
    h2 = result.get("h_ref_cmp_out [J/kg]", result.get("h2 [J/kg]", np.nan)) * cu.J2kJ
    h2_star = result.get("h_ref_cond_sat_v [J/kg]", result.get("h2_star [J/kg]", np.nan)) * cu.J2kJ
    h3_star = result.get("h_ref_cond_sat_l [J/kg]", result.get("h3_star [J/kg]", np.nan)) * cu.J2kJ
    h3 = result.get("h_ref_exp_in [J/kg]", result.get("h3 [J/kg]", np.nan)) * cu.J2kJ
    h4 = result.get("h_ref_exp_out [J/kg]", result.get("h4 [J/kg]", np.nan)) * cu.J2kJ
    T1_star = result.get("T_ref_evap_sat [°C]", result.get("T1_star [°C]", np.nan))
    T1 = result.get("T_ref_cmp_in [°C]", result.get("T1 [°C]", np.nan))
    T2 = result.get("T_ref_cmp_out [°C]", result.get("T2 [°C]", np.nan))
    T2_star = result.get("T_ref_cond_sat_v [°C]", result.get("T2_star [°C]", np.nan))
    T3_star = result.get("T_ref_cond_sat_l [°C]", result.get("T3_star [°C]", np.nan))
    T3 = result.get("T_ref_exp_in [°C]", result.get("T3 [°C]", np.nan))
    T4 = result.get("T_ref_exp_out [°C]", result.get("T4 [°C]", np.nan))

    if np.isnan(h1_star) and not np.isnan(h1):
        h1_star, T1_star = h1, T1
    if np.isnan(h3_star) and not np.isnan(h3):
        h3_star, T3_star = h3, T3

    ax.plot(h_liq, temps, color=color1, label=sat_liquid_label, linewidth=dm.lw(0))
    ax.plot(h_vap, temps, color=color2, label=sat_vapor_label, linewidth=dm.lw(0))

    pts_x = {"1_star": h1_star, "1": h1, "2": h2, "2_star": h2_star, "3_star": h3_star, "3": h3, "4": h4}
    pts_y = {"1_star": T1_star, "1": T1, "2": T2, "2_star": T2_star, "3_star": T3_star, "3": T3, "4": T4}
    is_on = bool(result.get("hp_is_on", result.get("is_on", False)))
    # T-h chart: x-axis = enthalpy [kJ/kg], y-axis = temperature [°C].
    # The two axes use different units, so tol_atol and tol_y_atol are
    # specified independently:
    # - tol_atol  = 0.5 kJ/kg — narrow enthalpy difference inside the
    #               saturation dome.
    # - tol_y_atol = 0.5 °C   — temperature resolution that lets the SH/SC
    #               (superheat / subcool) segments stay visible.
    _draw_cycle_lines_and_annotations(
        ax,
        pts_x,
        pts_y,
        is_on,
        color1,
        color2,
        line_color,
        color3,
        color4,
        tol_atol=0.5,
        tol_y_atol=0.5,
        point_labels=point_labels,
    )

    trans = ax.get_yaxis_transform()

    if T_cond_bound is not None:
        val = T_cond_bound.get("val")
        label = T_cond_bound.get("label", "Cond_bound")
        if val is not None and not (isinstance(val, float) and np.isnan(val)):
            val = float(val)  # type: ignore
            dy = float(T_cond_bound.get("dy", 2.0))
            va = T_cond_bound.get("va", "bottom")
            ax.axhline(y=val, color="oc.red5", linestyle=":", linewidth=dm.lw(0))
            offset = dm.make_offset(4, float(dy), cast(mfigure.Figure, ax.figure)) if ax.figure else None
            transform = trans + offset if offset else trans
            ax.text(
                0.0,
                val,
                f"{label}: {val:.1f}°C",
                color="oc.red5",
                ha="left",
                va=va,
                transform=transform,
                fontsize=dm.fs(-2),
            )

    if T_evap_bound is not None:
        val = T_evap_bound.get("val")
        label = T_evap_bound.get("label", "Evap_bound")
        if val is not None and not (isinstance(val, float) and np.isnan(val)):
            val = float(val)  # type: ignore
            dy = float(T_evap_bound.get("dy", -2.0))
            va = T_evap_bound.get("va", "top")
            ax.axhline(y=val, color="oc.orange5", linestyle=":", linewidth=dm.lw(0))
            offset = dm.make_offset(4, float(dy), cast(mfigure.Figure, ax.figure)) if ax.figure else None
            transform = trans + offset if offset else trans
            ax.text(
                0.0,
                val,
                f"{label}: {val:.1f}°C",
                color="oc.orange5",
                ha="left",
                va=va,
                transform=transform,
                fontsize=dm.fs(-2),
            )

    ax.set_xlabel("Enthalpy [kJ/kg]")
    ax.set_ylabel("Temperature [°C]")
    ax.set_xlim(limits["xmin"], limits["xmax"])
    ax.set_ylim(limits["ymin"], limits["ymax"])

    import matplotlib.ticker as ticker

    if refrigerant == "R32":
        ax.yaxis.set_major_locator(ticker.MultipleLocator(40))

    legend_handles = [
        ax.plot([], [], color=color1, linewidth=dm.lw(0), label=sat_liquid_label)[0],
        ax.plot([], [], color=color2, linewidth=dm.lw(0), label=sat_vapor_label)[0],
        ax.plot(
            [],
            [],
            color=line_color,
            linewidth=dm.lw(0),
            marker="o",
            linestyle=":",
            markersize=2.5,
            markerfacecolor=color3,
            markeredgecolor=color3,
            markeredgewidth=dm.lw(0),
            label=ref_cycle_label,
        )[0],
    ]
    ax.legend(
        handles=legend_handles,
        loc="upper center",
        handlelength=1.5,
        labelspacing=0.5,
        columnspacing=2,
        ncol=3,
        frameon=False,
    )


def plot_ph_diagram(
    ax: maxes.Axes,
    result: dict[str, float],
    refrigerant: str,
    fontsize: float | None = None,
    tick_pad: float | None = None,
    sat_liquid_label: str = "Sat. liquid",
    sat_vapor_label: str = "Sat. vapor",
    ref_cycle_label: str = "Ref. cycle",
    point_labels: dict[str, str] | None = None,
) -> None:
    """Plot P-h diagram on given axis."""
    color1, color2, color3, color4, line_color = "oc.blue5", "oc.red5", "black", "oc.gray6", "oc.gray4"
    limits = REF_LIMITS.get(refrigerant, {"ph": {"xmin": 200.0, "xmax": 750.0, "ymin": 100.0, "ymax": 10000.0}})["ph"]

    temps, h_liq, h_vap, _, _, _ = _get_saturation_curves(refrigerant)
    p_sat = [CP.PropsSI("P", "T", cu.C2K(T), "Q", 0, refrigerant) / 1000 for T in temps]

    P1_star = (result.get("P_ref_evap_sat [Pa]") or result.get("P1_star [Pa]", np.nan)) * cu.Pa2kPa
    P1 = (result.get("P_ref_cmp_in [Pa]") or result.get("P1 [Pa]", np.nan)) * cu.Pa2kPa
    P2 = (result.get("P_ref_cmp_out [Pa]") or result.get("P2 [Pa]", np.nan)) * cu.Pa2kPa
    P2_star = (result.get("P_ref_cond_sat_v [Pa]") or result.get("P2_star [Pa]", np.nan)) * cu.Pa2kPa
    P3_star = (result.get("P_ref_cond_sat_l [Pa]") or result.get("P3_star [Pa]", np.nan)) * cu.Pa2kPa
    P3 = (result.get("P_ref_exp_in [Pa]") or result.get("P3 [Pa]", np.nan)) * cu.Pa2kPa
    P4 = (result.get("P_ref_exp_out [Pa]") or result.get("P4 [Pa]", np.nan)) * cu.Pa2kPa

    h1_star = (result.get("h_ref_evap_sat [J/kg]") or result.get("h1_star [J/kg]", np.nan)) * cu.J2kJ
    h1 = (result.get("h_ref_cmp_in [J/kg]") or result.get("h1 [J/kg]", np.nan)) * cu.J2kJ
    h2 = (result.get("h_ref_cmp_out [J/kg]") or result.get("h2 [J/kg]", np.nan)) * cu.J2kJ
    h2_star = (result.get("h_ref_cond_sat_v [J/kg]") or result.get("h2_star [J/kg]", np.nan)) * cu.J2kJ
    h3_star = (result.get("h_ref_cond_sat_l [J/kg]") or result.get("h3_star [J/kg]", np.nan)) * cu.J2kJ
    h3 = (result.get("h_ref_exp_in [J/kg]") or result.get("h3 [J/kg]", np.nan)) * cu.J2kJ
    h4 = (result.get("h_ref_exp_out [J/kg]") or result.get("h4 [J/kg]", np.nan)) * cu.J2kJ

    if np.isnan(h1_star) and not np.isnan(h1):
        h1_star, P1_star = h1, P1
    if np.isnan(h3_star) and not np.isnan(h3):
        h3_star, P3_star = h3, P3

    ax.plot(h_liq, p_sat, color=color1, label=sat_liquid_label, linewidth=dm.lw(0))
    ax.plot(h_vap, p_sat, color=color2, label=sat_vapor_label, linewidth=dm.lw(0))

    pts_x = {"1_star": h1_star, "1": h1, "2": h2, "2_star": h2_star, "3_star": h3_star, "3": h3, "4": h4}
    pts_y = {"1_star": P1_star, "1": P1, "2": P2, "2_star": P2_star, "3_star": P3_star, "3": P3, "4": P4}
    is_on = bool(result.get("hp_is_on", result.get("is_on", False)))
    _draw_cycle_lines_and_annotations(
        ax,
        pts_x,
        pts_y,
        is_on,
        color1,
        color2,
        line_color,
        color3,
        color4,
        tol_atol=0.5,
        tol_y_atol=None,
        point_labels=point_labels,
    )

    ax.set_xlabel("Enthalpy [kJ/kg]")
    ax.set_ylabel("Pressure [kPa]")
    ax.set_xlim(limits["xmin"], limits["xmax"])
    ax.set_ylim(limits["ymin"], limits["ymax"])
    ax.set_yscale("log")

    legend_handles = [
        ax.plot([], [], color=color1, linewidth=dm.lw(0), label=sat_liquid_label)[0],
        ax.plot([], [], color=color2, linewidth=dm.lw(0), label=sat_vapor_label)[0],
        ax.plot(
            [],
            [],
            color=line_color,
            linewidth=dm.lw(0),
            marker="o",
            linestyle=":",
            markersize=2.5,
            markerfacecolor=color3,
            markeredgecolor=color3,
            markeredgewidth=dm.lw(0),
            label=ref_cycle_label,
        )[0],
    ]
    ax.legend(
        handles=legend_handles,
        loc="upper center",
        handlelength=1.5,
        labelspacing=0.5,
        columnspacing=2,
        ncol=3,
        frameon=False,
    )


def plot_ts_diagram(
    ax: maxes.Axes,
    result: dict[str, float],
    refrigerant: str,
    T_cond_bound: dict[str, float | str] | None = None,
    T_evap_bound: dict[str, float | str] | None = None,
    sat_liquid_label: str = "Sat. liquid",
    sat_vapor_label: str = "Sat. vapor",
    ref_cycle_label: str = "Ref. cycle",
    point_labels: dict[str, str] | None = None,
) -> None:
    """Plot T-s diagram on given axis with super heating/cooling considered."""
    color1, color2, color3, color4, line_color = "oc.blue5", "oc.red5", "black", "oc.gray6", "oc.gray5"
    limits = REF_LIMITS.get(refrigerant, {"ts": {"xmin": 1.0, "xmax": 3.0, "ymin": -40.0, "ymax": 160.0}})["ts"]

    temps, _, _, _, s_liq, s_vap = _get_saturation_curves(refrigerant)

    ax.plot(s_liq, temps, color=color1, label=sat_liquid_label, linewidth=dm.lw(0))
    ax.plot(s_vap, temps, color=color2, label=sat_vapor_label, linewidth=dm.lw(0))

    s1_star = (result.get("s_ref_evap_sat [J/(kg·K)]") or result.get("s1_star [J/(kg·K)]", np.nan)) / 1000
    s1 = (result.get("s_ref_cmp_in [J/(kg·K)]") or result.get("s1 [J/(kg·K)]", np.nan)) / 1000
    s2 = (result.get("s_ref_cmp_out [J/(kg·K)]") or result.get("s2 [J/(kg·K)]", np.nan)) / 1000
    s2_star = (result.get("s_ref_cond_sat_v [J/(kg·K)]") or result.get("s2_star [J/(kg·K)]", np.nan)) / 1000
    s3_star = (result.get("s_ref_cond_sat_l [J/(kg·K)]") or result.get("s3_star [J/(kg·K)]", np.nan)) / 1000
    s3 = (result.get("s_ref_exp_in [J/(kg·K)]") or result.get("s3 [J/(kg·K)]", np.nan)) / 1000
    s4 = (result.get("s_ref_exp_out [J/(kg·K)]") or result.get("s4 [J/(kg·K)]", np.nan)) / 1000

    T1_star = result.get("T_ref_evap_sat [°C]") or result.get("T1_star [°C]", np.nan)
    T1 = result.get("T_ref_cmp_in [°C]") or result.get("T1 [°C]", np.nan)
    T2 = result.get("T_ref_cmp_out [°C]") or result.get("T2 [°C]", np.nan)
    T2_star = result.get("T_ref_cond_sat_v [°C]") or result.get("T2_star [°C]", np.nan)
    T3_star = result.get("T_ref_cond_sat_l [°C]") or result.get("T3_star [°C]", np.nan)
    T3 = result.get("T_ref_exp_in [°C]") or result.get("T3 [°C]", np.nan)
    T4 = result.get("T_ref_exp_out [°C]") or result.get("T4 [°C]", np.nan)

    if np.isnan(s1_star) and not np.isnan(s1):
        s1_star, T1_star = s1, T1
    if np.isnan(s3_star) and not np.isnan(s3):
        s3_star, T3_star = s3, T3

    pts_x = {"1_star": s1_star, "1": s1, "2": s2, "2_star": s2_star, "3_star": s3_star, "3": s3, "4": s4}
    pts_y = {"1_star": T1_star, "1": T1, "2": T2, "2_star": T2_star, "3_star": T3_star, "3": T3, "4": T4}
    is_on = bool(result.get("hp_is_on", result.get("is_on", False)))
    _draw_cycle_lines_and_annotations(
        ax,
        pts_x,
        pts_y,
        is_on,
        color1,
        color2,
        line_color,
        color3,
        color4,
        tol_atol=0.05,
        tol_y_atol=0.5,
        point_labels=point_labels,
    )

    trans = ax.get_yaxis_transform()

    if T_cond_bound is not None:
        val = T_cond_bound.get("val")
        label = T_cond_bound.get("label", "Cond_bound")
        if val is not None and not (isinstance(val, float) and np.isnan(val)):
            val = float(val)  # type: ignore
            dy = float(T_cond_bound.get("dy", 2.0))
            va = T_cond_bound.get("va", "bottom")
            ax.axhline(y=val, color="oc.red5", linestyle=":", linewidth=dm.lw(0))
            offset = dm.make_offset(4, float(dy), cast(mfigure.Figure, ax.figure)) if ax.figure else None
            transform = trans + offset if offset else trans
            ax.text(
                0.0,
                val,
                f"{label}: {val:.1f}°C",
                color="oc.red5",
                ha="left",
                va=va,
                transform=transform,
                fontsize=dm.fs(-2),
            )

    if T_evap_bound is not None:
        val = T_evap_bound.get("val")
        label = T_evap_bound.get("label", "Evap_bound")
        if val is not None and not (isinstance(val, float) and np.isnan(val)):
            val = float(val)  # type: ignore
            dy = float(T_evap_bound.get("dy", -2.0))
            va = T_evap_bound.get("va", "top")
            ax.axhline(y=val, color="oc.orange5", linestyle=":", linewidth=dm.lw(0))
            offset = dm.make_offset(4, float(dy), cast(mfigure.Figure, ax.figure)) if ax.figure else None
            transform = trans + offset if offset else trans
            ax.text(
                0.0,
                val,
                f"{label}: {val:.1f}°C",
                color="oc.orange5",
                ha="left",
                va=va,
                transform=transform,
                fontsize=dm.fs(-2),
            )

    ax.set_xlabel("Entropy [kJ/(kg·K)]")
    ax.set_ylabel("Temperature [°C]")
    ax.set_xlim(limits["xmin"], limits["xmax"])
    ax.set_ylim(limits["ymin"], limits["ymax"])

    import matplotlib.ticker as ticker

    ax.xaxis.set_major_locator(ticker.MultipleLocator(0.5))
    if refrigerant == "R32":
        ax.yaxis.set_major_locator(ticker.MultipleLocator(40))

    legend_handles = [
        ax.plot([], [], color=color1, linewidth=dm.lw(0), label=sat_liquid_label)[0],
        ax.plot([], [], color=color2, linewidth=dm.lw(0), label=sat_vapor_label)[0],
        ax.plot(
            [],
            [],
            color=line_color,
            linewidth=dm.lw(0),
            marker="o",
            linestyle=":",
            markersize=2.5,
            markerfacecolor=color3,
            markeredgecolor=color3,
            markeredgewidth=dm.lw(0),
            label=ref_cycle_label,
        )[0],
    ]
    ax.legend(
        handles=legend_handles,
        loc="upper center",
        handlelength=1.5,
        labelspacing=0.5,
        columnspacing=2,
        ncol=3,
        frameon=False,
    )
