"""End-of-step temperature of the single-node (lumped) boiler tank.

Every boiler closes its tank balance with an implicit residual
``r(T) = [C_next·T − C_curr·T_start − dt·(Q_hp + P(T))] / C_tank`` [K]
(:func:`tmhp.dynamic_context.tank_mass_energy_residual`; ``P`` is the draw-off
enthalpy, the envelope loss and the subsystem heat). The functions here are
what the boilers do when their first solve of ``r(T) = 0`` does not return a
root, so that a failed solve neither goes unnoticed nor drops part of ``P``:

1. the same equation is solved by bracketing over 0-200 °C
   (:func:`tmhp.dynamic_context._solve_tank_end`), which finds the root whenever
   one exists there;
2. if none exists (the balance puts the water below freezing or above 200 °C,
   or the residual is not a number), the step is taken explicitly with the
   **whole** heat rate at the start temperature -- heat pump, draw-off, loss and
   subsystems, read back from the residual itself -- and a
   :class:`TankSolveWarning` names the step. A step that cannot even be taken
   explicitly (non-finite heat rate) raises.
"""

from __future__ import annotations

import math
import warnings
from collections.abc import Callable

import numpy as np

from ._opt_utils import ignore_minpack_progress_warning
from .dynamic_context import _solve_tank_end, tank_free_heat_rate

__all__ = ["TANK_RESIDUAL_TOL_K", "TankSolveWarning", "recover_tank_temperature", "solve_lumped_tank"]

#: Largest residual [K of the full tank] at which a returned temperature counts
#: as a root. 1e-6 K of a 100 L tank is 0.4 J; MINPACK's own flag is not used
#: because it reports "not making good progress" (``ier = 5``) on roots it has
#: found to 1e-13 K when the mixing-valve term has a kink there.
TANK_RESIDUAL_TOL_K: float = 1.0e-6


class TankSolveWarning(RuntimeWarning):
    """The implicit tank balance had no root; the step was taken explicitly."""


def _residual_value(residual: Callable, T_K: float) -> float:
    return float(np.atleast_1d(residual(T_K))[0])


def recover_tank_temperature(
    residual: Callable,
    *,
    T_start_K: float,
    dt: float,
    C_curr: float,
    C_next: float,
    C_tank: float,
    who: str,
    step: int,
) -> float:
    """Tank temperature [K] at the end of a step whose first implicit solve failed.

    ``residual`` is the boiler's residual with the step's heat-source rate in
    it. See the module docstring for the two stages.
    """
    T_K = _solve_tank_end(lambda T: _residual_value(residual, T), 0.0, dt, C_tank)
    if T_K is not None and math.isfinite(T_K):
        return T_K
    try:
        rate = tank_free_heat_rate(
            lambda T: _residual_value(residual, T),
            T_start_K,
            T_start_K=T_start_K,
            C_curr=C_curr,
            C_next=C_next,
            C_tank=C_tank,
            dt=dt,
            Q_heat_source=0.0,
        )
        T_K = (C_curr * T_start_K + dt * rate) / C_next
    except (ArithmeticError, ValueError) as exc:
        raise RuntimeError(f"{who}: tank energy balance of step {step} cannot be evaluated ({exc})") from exc
    if not math.isfinite(T_K):
        raise RuntimeError(
            f"{who}: tank energy balance of step {step} is not a number (start {T_start_K - 273.15:.2f} °C); "
            "check the step's inputs and subsystem states"
        )
    warnings.warn(
        f"{who}: the tank energy balance of step {step} has no solution between 0 and 200 °C "
        f"(start {T_start_K - 273.15:.2f} °C); the step was taken explicitly with the heat rate at the start "
        f"temperature ({rate:.0f} W: heat pump, draw-off, loss and subsystems) and ends at {T_K - 273.15:.2f} °C. "
        "Liquid water does not exist there: the result of this step is outside the tank model.",
        TankSolveWarning,
        stacklevel=3,
    )
    return float(T_K)


def solve_lumped_tank(
    residual: Callable,
    T_guess_K: float,
    *,
    T_start_K: float,
    dt: float,
    C_curr: float,
    C_next: float,
    C_tank: float,
    who: str,
    step: int,
) -> float:
    """End-of-step tank temperature [K]: ``fsolve`` from ``T_guess_K``, checked.

    The ``fsolve`` result is kept when it is a root (finite, residual within
    :data:`TANK_RESIDUAL_TOL_K`); otherwise :func:`recover_tank_temperature`
    takes over. No exception is swallowed into a different energy balance.
    """
    from scipy.optimize import fsolve

    T_K = math.nan
    try:
        with ignore_minpack_progress_warning():
            T_K = float(np.atleast_1d(fsolve(residual, x0=[T_guess_K]))[0])
        ok = math.isfinite(T_K) and abs(_residual_value(residual, T_K)) <= TANK_RESIDUAL_TOL_K
    except (ArithmeticError, ValueError, RuntimeError):
        ok = False
    if ok:
        return T_K
    return recover_tank_temperature(
        residual, T_start_K=T_start_K, dt=dt, C_curr=C_curr, C_next=C_next, C_tank=C_tank, who=who, step=step
    )
