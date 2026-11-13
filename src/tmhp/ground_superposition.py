"""Exact fast temporal superposition, forced on for any run length.

:class:`~tmhp.ground_coupling.AggregateGFunctionCoupler` (the default ground
coupler) sums the load history with one dot product per step, whose cost
grows with the step index, and switches by itself to an online FFT
convolution for long runs (from
:attr:`~tmhp.ground_coupling.AggregateGFunctionCoupler.FAST_MIN_STEPS`
steps on). **Nothing has to be enabled for a long run to be fast.**

The two give the same values (to round-off): the online convolution is not a
load-aggregation scheme, nothing is averaged or truncated. See
:mod:`tmhp._online_convolution` for the method.

This module keeps :class:`FastGFunctionCoupler`, the same coupler with the
online convolution used from the first step whatever the run length. It is
useful for tests and for timing; a simulation does not need it.
"""

from __future__ import annotations

from typing import Any

from .ground_coupling import AggregateGFunctionCoupler

__all__ = ["FastGFunctionCoupler", "enable_fast_superposition"]


class FastGFunctionCoupler(AggregateGFunctionCoupler):
    """:class:`~tmhp.ground_coupling.AggregateGFunctionCoupler` with the online convolution at any run length.

    Same contract, parameters and values as the default coupler, which
    already does this for runs of
    :attr:`~tmhp.ground_coupling.AggregateGFunctionCoupler.FAST_MIN_STEPS`
    steps or more. Below that length the dot product of the default coupler
    is faster.
    """

    FAST_MIN_STEPS = 0


def enable_fast_superposition(model: Any) -> FastGFunctionCoupler:
    """Switch a ground-source model to :class:`FastGFunctionCoupler`, in place.

    Not needed for speed: the default coupler accelerates long runs by
    itself. Works on :class:`~tmhp.GroundSourceHeatPumpBoiler` and its solar
    variants; the g-function, pulse tolerance and fixed step of the model's
    coupler are kept, so the results do not change. Functions that rebuild
    the model's coupler afterwards
    (:func:`~tmhp.borehole_capacity.enable_borehole_dynamics`, attaching a
    :class:`~tmhp.borefield.FieldGroundResponse`) put the default coupler
    back.

    Returns
    -------
    FastGFunctionCoupler
        The coupler now used by ``model``.
    """
    if not hasattr(model, "_ground_coupler"):
        raise TypeError(
            f"{type(model).__name__} has no ground coupler (only the ground-source heat-pump boilers delegate "
            "their temporal superposition)"
        )
    coupler = model._ground_coupler
    if isinstance(coupler, FastGFunctionCoupler):
        return coupler
    if not isinstance(coupler, AggregateGFunctionCoupler):
        raise ValueError("the model uses an injected ground_coupler; build a FastGFunctionCoupler on its g-function")
    fast = FastGFunctionCoupler(coupler._g, pulse_tol=coupler._pulse_tol, dt_s=coupler._dt_fixed)
    model._ground_coupler = fast
    return fast
