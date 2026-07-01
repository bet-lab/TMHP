"""Pre-compute a multi-dimensional grid of vapour-compression cycle states.

This generator sweeps an 8-parameter grid (refrigerant, source/sink
temperatures, subcool, superheat, condenser load, and the two heat-exchanger
``UA`` values), runs a damped fixed-point solver to establish the dynamic
evaporating temperature, and serialises every reachable cycle state to a single
compact JSON asset consumed by the client-side SVG widget.

The condensing temperature is fixed directly by the load and condenser ``UA``::

    T_cond = T_sink + Q_cond / UA_cond

The evaporating temperature is coupled to the cycle through the evaporator duty
and is solved iteratively::

    Q_evap = m_ref (h1 - h4)          (cycle energy balance)
    T_evap = T_source - Q_evap / UA_evap

Combinations that are physically unreachable (e.g. ``T_cond`` above the
refrigerant critical temperature, or a non-converging / inverted cycle) are
omitted from the output; the widget renders an "unavailable" notice for those.

Output
------
``docs/source/_static/widgets/cycle_data.json`` — a single JSON file with:

* ``meta``        — grid metadata, point ordering, units
* ``params``      — axis values for each parameter
* ``limits``      — per-refrigerant diagram axis limits (from ``REF_LIMITS``)
* ``saturation``  — per-refrigerant saturation dome curves
* ``states``      — dict of ``"i0_i1_..._i7" -> [[h,T,P,s], ...]`` (7 points)
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os

import CoolProp.CoolProp as CP
import numpy as np
from tqdm import tqdm

import tmhp.calc_util as cu
from tmhp.mollier_diagram import REF_LIMITS
from tmhp.refrigerant import calc_ref_state

# ── Parameter grid ──────────────────────────────────────────────────────────
REFRIGERANTS = ["R410A", "R134a", "R32", "R290"]
T_SOURCES_C = [float(t) for t in range(-10, 31)]
T_SINKS_C = [float(t) for t in range(40, 66)]
DT_SUBCOOL_K = [float(t) for t in range(1, 6)]
DT_SUPERHEAT_K = [float(t) for t in range(1, 6)]
Q_COND_W = [14000.0]
UA_COND_WK = [2500.0]
UA_EVAP_WK = [2000.0]


def ETA_CMP_ISEN(r_p: float) -> float:
    return max(0.2, 0.9 - 0.02 * r_p)


SAT_CURVE_POINTS = 10000
CI_SAT_CURVE_POINTS = 512
DATA_PROFILES = ("full", "ci")

# Ordered axis lists; the per-state key encodes the index into each axis.
PARAM_AXES = [
    ("refrigerant", REFRIGERANTS),
    ("T_source", T_SOURCES_C),
    ("T_sink", T_SINKS_C),
    ("dT_subcool", DT_SUBCOOL_K),
    ("dT_superheat", DT_SUPERHEAT_K),
    ("Q_cond", Q_COND_W),
    ("UA_cond", UA_COND_WK),
    ("UA_evap", UA_EVAP_WK),
]

CI_PARAM_AXES = [
    ("refrigerant", REFRIGERANTS),
    ("T_source", [-10.0, 0.0, 10.0, 20.0, 30.0]),
    ("T_sink", [40.0, 50.0, 65.0]),
    ("dT_subcool", [3.0]),
    ("dT_superheat", [5.0]),
    ("Q_cond", Q_COND_W),
    ("UA_cond", UA_COND_WK),
    ("UA_evap", UA_EVAP_WK),
]

# Display-unit accessors: (h [kJ/kg], T [°C], P [kPa], s [kJ/(kg·K)]).
_POINT_SPEC = [
    ("1s", "h_ref_evap_sat [J/kg]", "T_ref_evap_sat [°C]", "P_ref_evap_sat [Pa]", "s_ref_evap_sat [J/(kg·K)]"),
    ("1", "h_ref_cmp_in [J/kg]", "T_ref_cmp_in [°C]", "P_ref_cmp_in [Pa]", "s_ref_cmp_in [J/(kg·K)]"),
    ("2", "h_ref_cmp_out [J/kg]", "T_ref_cmp_out [°C]", "P_ref_cmp_out [Pa]", "s_ref_cmp_out [J/(kg·K)]"),
    ("2s", "h_ref_cond_sat_v [J/kg]", "T_ref_cond_sat_v [°C]", "P_ref_cond_sat_v [Pa]", "s_ref_cond_sat_v [J/(kg·K)]"),
    ("3s", "h_ref_cond_sat_l [J/kg]", "T_ref_cond_sat_l [°C]", "P_ref_cond_sat_l [Pa]", "s_ref_cond_sat_l [J/(kg·K)]"),
    ("3", "h_ref_exp_in [J/kg]", "T_ref_exp_in [°C]", "P_ref_exp_in [Pa]", "s_ref_exp_in [J/(kg·K)]"),
    ("4", "h_ref_exp_out [J/kg]", "T_ref_exp_out [°C]", "P_ref_exp_out [Pa]", "s_ref_exp_out [J/(kg·K)]"),
]


def profile_param_axes(profile: str) -> list[tuple[str, list[float] | list[str]]]:
    """Return the parameter grid for a docs data generation profile."""
    if profile == "full":
        return PARAM_AXES
    if profile == "ci":
        return CI_PARAM_AXES
    msg = f"unknown docs data profile {profile!r}; expected one of {DATA_PROFILES}"
    raise ValueError(msg)


def _profile_saturation_points(profile: str) -> int:
    if profile == "full":
        return SAT_CURVE_POINTS
    if profile == "ci":
        return CI_SAT_CURVE_POINTS
    msg = f"unknown docs data profile {profile!r}; expected one of {DATA_PROFILES}"
    raise ValueError(msg)


def build_saturation_curves(refrigerant: str, *, points: int | None = None) -> dict[str, list[float]]:
    """Return down-sampled saturation dome curves in display units."""
    t_min = CP.PropsSI("Tmin", refrigerant)
    t_crit = CP.PropsSI("Tcrit", refrigerant)
    temps_k = np.linspace(t_min + 1.0, t_crit - 0.5, points or SAT_CURVE_POINTS)

    temp_c, h_liq, h_vap, p_sat, s_liq, s_vap = [], [], [], [], [], []
    for t_k in temps_k:
        temp_c.append(round(cu.K2C(t_k), 1))
        h_liq.append(round(CP.PropsSI("H", "T", t_k, "Q", 0, refrigerant) * cu.J2kJ, 1))
        h_vap.append(round(CP.PropsSI("H", "T", t_k, "Q", 1, refrigerant) * cu.J2kJ, 1))
        p_sat.append(round(CP.PropsSI("P", "T", t_k, "Q", 0, refrigerant) * cu.Pa2kPa, 1))
        s_liq.append(round(CP.PropsSI("S", "T", t_k, "Q", 0, refrigerant) * cu.J2kJ, 3))
        s_vap.append(round(CP.PropsSI("S", "T", t_k, "Q", 1, refrigerant) * cu.J2kJ, 3))

    try:
        h_crit = CP.PropsSI("H", "T", t_crit, "Q", 0, refrigerant) * cu.J2kJ
        p_crit = CP.PropsSI("P", "T", t_crit, "Q", 0, refrigerant) * cu.Pa2kPa
        s_crit = CP.PropsSI("S", "T", t_crit, "Q", 0, refrigerant) * cu.J2kJ
        temp_c.append(round(cu.K2C(t_crit), 1))
        h_liq.append(round(h_crit, 1))
        h_vap.append(round(h_crit, 1))
        p_sat.append(round(p_crit, 1))
        s_liq.append(round(s_crit, 3))
        s_vap.append(round(s_crit, 3))
    except Exception:
        pass

    return {"T": temp_c, "h_liq": h_liq, "h_vap": h_vap, "p_sat": p_sat, "s_liq": s_liq, "s_vap": s_vap}


def solve_cycle(
    refrigerant: str,
    t_source_c: float,
    t_sink_c: float,
    dt_subcool: float,
    dt_superheat: float,
    q_cond_w: float,
    ua_cond: float,
    ua_evap: float,
    t_crit_k: float,
    t_min_k: float,
) -> list[list[float]] | None:
    """Solve one cycle; return its 7 packed state points or ``None`` if invalid.

    Each point is ``[h (kJ/kg), T (°C), P (kPa), s (kJ/(kg·K))]`` in the order
    given by :data:`_POINT_SPEC`.
    """
    t_cond_c = t_sink_c + q_cond_w / ua_cond
    t_cond_k = cu.C2K(t_cond_c)
    # The condenser must operate below the critical point (with a small margin)
    # for the two-phase saturation states to exist.
    if t_cond_k > t_crit_k - 1.0:
        return None

    # Damped fixed-point iteration on the evaporating temperature.
    t_evap_c = t_source_c - q_cond_w / ua_evap  # conservative initial guess
    res = None
    converged = False
    for _ in range(60):
        t_evap_k = cu.C2K(t_evap_c)
        if t_evap_k <= t_min_k + 1.0 or t_evap_k >= t_cond_k - 0.5:
            return None

        res = calc_ref_state(
            T_evap_K=t_evap_k,
            T_cond_K=t_cond_k,
            refrigerant=refrigerant,
            eta_cmp_isen=ETA_CMP_ISEN,
            mode="heating",
            dT_superheat=dt_superheat,
            dT_subcool=dt_subcool,
            is_active=True,
        )

        h1 = res["h_ref_cmp_in [J/kg]"]
        h2 = res["h_ref_cmp_out [J/kg]"]
        h3 = res["h_ref_exp_in [J/kg]"]
        h4 = res["h_ref_exp_out [J/kg]"]
        if any(np.isnan(v) for v in (h1, h2, h3, h4)) or np.isnan(res["T_ref_cmp_out_K"]):
            return None

        delta_cond = h2 - h3
        if delta_cond <= 0.0:
            return None

        m_ref = q_cond_w / delta_cond
        q_evap = m_ref * (h1 - h4)
        if q_evap <= 0.0:
            return None

        t_evap_new_c = t_source_c - q_evap / ua_evap
        if abs(t_evap_new_c - t_evap_c) < 1e-3:
            t_evap_c = t_evap_new_c
            converged = True
            break
        t_evap_c = 0.5 * t_evap_c + 0.5 * t_evap_new_c  # under-relaxation

    if not converged or res is None:
        return None

    # Final recompute at the converged evaporating temperature.
    t_evap_k = cu.C2K(t_evap_c)
    if t_evap_k <= t_min_k + 1.0 or t_evap_k >= t_cond_k - 0.5:
        return None
    res = calc_ref_state(
        T_evap_K=t_evap_k,
        T_cond_K=t_cond_k,
        refrigerant=refrigerant,
        eta_cmp_isen=ETA_CMP_ISEN,
        mode="heating",
        dT_superheat=dt_superheat,
        dT_subcool=dt_subcool,
        is_active=True,
    )

    pts = []
    for _name, h_key, t_key, p_key, s_key in _POINT_SPEC:
        h_val = res[h_key]
        t_val = res[t_key]
        p_val = res[p_key]
        s_val = res[s_key]
        if any(np.isnan(v) for v in (h_val, t_val, p_val, s_val)):
            return None
        pts.append(
            [
                round(h_val * cu.J2kJ, 1),
                round(t_val, 1),
                round(p_val * cu.Pa2kPa, 1),
                round(s_val * cu.J2kJ, 3),
            ]
        )

    return pts


def worker(task: tuple) -> tuple[str, list[list[float]] | None]:
    """Worker function for multiprocessing."""
    ref, t_source, t_sink, dt_sub, dt_sup, q_cond, ua_cond, ua_evap, crit_val, tmin_val, key = task
    res = solve_cycle(ref, t_source, t_sink, dt_sub, dt_sup, q_cond, ua_cond, ua_evap, crit_val, tmin_val)
    return key, res


def _process_pool_context():
    """Return a non-fork multiprocessing context when the platform supports it."""
    methods = mp.get_all_start_methods()
    if "forkserver" in methods:
        return mp.get_context("forkserver")
    if "spawn" in methods:
        return mp.get_context("spawn")
    return mp.get_context()


def main(out_path: str | os.PathLike[str] | None = None, *, profile: str = "full") -> None:
    """Build the cycle-widget JSON payload.

    Parameters
    ----------
    out_path:
        Optional output path. When omitted, the generated asset is written to
        ``docs/source/_static/widgets/cycle_data.json`` for the Sphinx build.
    profile:
        ``"full"`` preserves the deployed high-resolution grid. ``"ci"`` keeps
        every refrigerant but uses a coarser grid for fast PR docs checks.
    """
    if out_path is None:
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        out_dir = os.path.join(repo_root, "docs", "source", "_static", "widgets")
        out_path_str = os.path.join(out_dir, "cycle_data.json")
    else:
        out_path_str = os.fspath(out_path)
        out_dir = os.path.dirname(out_path_str)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    param_axes = profile_param_axes(profile)
    axis = dict(param_axes)
    refrigerants = axis["refrigerant"]
    saturation_points = _profile_saturation_points(profile)

    saturation = {ref: build_saturation_curves(ref, points=saturation_points) for ref in refrigerants}

    crit = {ref: CP.PropsSI("Tcrit", ref) for ref in refrigerants}
    tmin = {ref: CP.PropsSI("Tmin", ref) for ref in refrigerants}

    tasks = []
    for i0, ref in enumerate(axis["refrigerant"]):
        for i1, t_source in enumerate(axis["T_source"]):
            for i2, t_sink in enumerate(axis["T_sink"]):
                for i3, dt_sub in enumerate(axis["dT_subcool"]):
                    for i4, dt_sup in enumerate(axis["dT_superheat"]):
                        for i5, q_cond in enumerate(axis["Q_cond"]):
                            for i6, ua_cond in enumerate(axis["UA_cond"]):
                                for i7, ua_evap in enumerate(axis["UA_evap"]):
                                    tasks.append(
                                        (
                                            ref,
                                            t_source,
                                            t_sink,
                                            dt_sub,
                                            dt_sup,
                                            q_cond,
                                            ua_cond,
                                            ua_evap,
                                            crit[ref],
                                            tmin[ref],
                                            f"{i0}_{i1}_{i2}_{i3}_{i4}_{i5}_{i6}_{i7}",
                                        )
                                    )

    states: dict[str, list[list[float]]] = {}
    total = len(tasks)

    from concurrent.futures import ProcessPoolExecutor

    max_workers = min(32, os.cpu_count() or 4)

    with ProcessPoolExecutor(max_workers=max_workers, mp_context=_process_pool_context()) as executor:
        results = executor.map(worker, tasks, chunksize=100)
        with tqdm(total=total, desc="Solving cycle grid") as pbar:
            for key, packed in results:
                if packed is not None:
                    states[key] = packed
                pbar.update(1)

    data = {
        "meta": {
            "profile": profile,
            "eta_cmp_isen": "max(0.2, 0.9 - 0.02 * r_p)",
            "point_order": [name for name, *_ in _POINT_SPEC],
            "point_labels": {
                "1s": "1'",
                "1": "1",
                "2": "2",
                "2s": "2'",
                "3s": "3'",
                "3": "3",
                "4": "4",
            },
            "value_order": ["h", "T", "P", "s"],
            "units": {"h": "kJ/kg", "T": "°C", "P": "kPa", "s": "kJ/(kg·K)"},
            "state_format": "states[key] is a list of 7 points; each point is [h, T, P, s]",
            "key_axes": [name for name, _ in param_axes],
            "saturation_curve_points": saturation_points,
            "n_valid": len(states),
            "n_total": total,
        },
        "params": {name: values for name, values in param_axes},
        "limits": REF_LIMITS,
        "saturation": saturation,
        "states": states,
    }

    with open(out_path_str, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))

    size_mb = os.path.getsize(out_path_str) / (1024 * 1024)
    print(f"Wrote {out_path_str}")
    print(f"Valid states: {len(states)} / {total}  ({100 * len(states) / total:.1f}%)")
    print(f"File size: {size_mb:.2f} MB")


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the data generator."""
    parser = argparse.ArgumentParser(
        description="Generate the JSON payload used by cycle_widget.html.",
    )
    parser.add_argument(
        "--profile",
        choices=DATA_PROFILES,
        default=os.environ.get("TMHP_DOCS_DATA_PROFILE", "full"),
        help="docs data profile: full for deployed docs, ci for fast PR checks",
    )
    parser.add_argument(
        "out_path",
        nargs="?",
        help=("optional output path; defaults to docs/source/_static/widgets/cycle_data.json"),
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    main(args.out_path, profile=args.profile)
