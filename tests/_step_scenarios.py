"""Shared deterministic ASHPB dynamic scenarios for the P0 step()-kernel
regression suite (#165).

Both the golden generator (``python -m`` / direct run, pre-refactor) and the
regression tests import from here so the *exact same* schedules drive both
the legacy ``analyze_dynamic`` path and the new public ``step()`` path.

Golden artifacts are gzip-compressed CSV (``%.17g`` round-trips float64) so
they stay git-diffable without a pyarrow dependency.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from tmhp import AirSourceHeatPumpBoiler

PERIOD_S: int = 3 * 86400  # 3-day horizon (spec: >= 3 days)
DTS: tuple[int, ...] = (300, 600)  # spec: dt in {300, 600} s
SCHEDULE_NAMES: tuple[str, ...] = ("diurnal_2draw", "cold_heavydraw")
SCENARIOS: list[tuple[str, int]] = [(name, dt) for name in SCHEDULE_NAMES for dt in DTS]
DATA_DIR: Path = Path(__file__).parent / "data"


def make_model() -> AirSourceHeatPumpBoiler:
    """Fixed ASHPB configuration shared by golden generation and tests."""
    return AirSourceHeatPumpBoiler(ref="R32")


def scenario_kwargs(name: str, dt_s: int) -> dict:
    """Deterministic ``analyze_dynamic`` kwargs for a named schedule."""
    t = np.arange(0, PERIOD_S, dt_s)
    tN = len(t)
    hod = (t % 86400) / 3600.0
    if name == "diurnal_2draw":
        # Diurnal outdoor swing + morning/evening DHW draws (HP cycling).
        T0 = 5.0 + 5.0 * np.sin(2 * np.pi * (hod - 9) / 24)
        dhw = np.where((np.abs(hod - 7) < 0.5) | (np.abs(hod - 20) < 0.5), 5e-5, 0.0)
        Tsup = np.full(tN, 15.0)
        Tsur = np.full(tN, 20.0)
    elif name == "cold_heavydraw":
        # Constant cold source + long daytime draw active through end-of-day,
        # so the final step carries a non-zero dV_tank_w_out and the run
        # exercises the cross-step coupling path heavily (HP-on + refill).
        # (Note: with the default tank_always_full=True the legacy
        # self.dV_tank_w_out leak does not perturb the trajectory, so the
        # idempotency guard is GREEN both before and after the refactor.)
        T0 = np.full(tN, 0.0)
        dhw = np.where(hod >= 6.0, 3e-5, 0.0)
        Tsup = np.full(tN, 10.0)
        Tsur = np.full(tN, 18.0)
    else:  # pragma: no cover - guard
        raise ValueError(f"unknown schedule: {name}")
    return {
        "simulation_period_sec": PERIOD_S,
        "dt_s": dt_s,
        "T_tank_w_init_C": 55.0,
        "dhw_usage_schedule": dhw,
        "T0_schedule": T0,
        "T_sup_w_schedule": Tsup,
        "T_sur_schedule": Tsur,
    }


def golden_path(name: str, dt_s: int) -> Path:
    return DATA_DIR / f"golden_step_{name}_dt{dt_s}.csv.gz"


def run_step_driven(model: AirSourceHeatPumpBoiler, name: str, dt_s: int) -> pd.DataFrame:
    """Drive the public ``step()`` kernel manually over a scenario, mirroring
    ``analyze_dynamic``'s wrapper (array setup + ``_postprocess``).

    This is the FMU/EnergyPlus call pattern: one ``step()`` per exchange.
    """
    kw = scenario_kwargs(name, dt_s)
    time = np.arange(0, kw["simulation_period_sec"], dt_s)
    tN = len(time)
    dhw = np.asarray(kw["dhw_usage_schedule"], dtype=float)
    T0 = np.asarray(kw["T0_schedule"], dtype=float)
    Tsup = np.asarray(kw["T_sup_w_schedule"], dtype=float)
    Tsur = np.asarray(kw["T_sur_schedule"], dtype=float)

    # State that _postprocess relies on (analyze_dynamic sets these too).
    model.time = time
    model.dt = dt_s
    model.dhw_flow_m3s = dhw

    state = model.make_initial_state(kw["T_tank_w_init_C"], tank_level_init=1.0)
    rows: list[dict] = []
    for n in range(tN):
        inputs = {
            "n": n,
            "current_time_s": float(time[n]),
            "T0": float(T0[n]),
            "dV_mix_w_out": float(dhw[n]),
            "T_sup_w": float(Tsup[n]),
            "T_sur": float(Tsur[n]),
            "I_DN": 0.0,
            "I_dH": 0.0,
        }
        state, row = model.step(state, inputs, dt_s)
        rows.append(row)
    return model._postprocess(pd.DataFrame(rows))


def _generate_golden() -> None:
    """Capture pre-refactor analyze_dynamic output as committed golden."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for name, dt in SCENARIOS:
        df = make_model().analyze_dynamic(**scenario_kwargs(name, dt))
        out = golden_path(name, dt)
        df.to_csv(out, index=False, float_format="%.17g")
        print(f"wrote {out}  shape={df.shape}")


if __name__ == "__main__":
    _generate_golden()
