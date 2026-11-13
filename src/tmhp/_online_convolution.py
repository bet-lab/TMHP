"""History sum of a growing sequence against a fixed kernel, at a cost per step independent of its length.

Used by :class:`~tmhp.ground_coupling.AggregateGFunctionCoupler` for long
runs. For a sequence ``p`` that is appended to step by step and a kernel
``k`` known in advance, :meth:`OnlineHistory.history` returns

.. math::

    H_n = \\sum_{i<n} p_i\\, k_{n-i}

exactly (to round-off), without summing the whole history at every step.

After step ``t − 1`` is known, the block of the last ``s`` entries, where
``s`` is the largest power of two dividing ``t``, is convolved with ``k``
once and added to the next ``s`` steps. Every pair (entry, later step) is
covered by exactly one block (the one at the highest binary digit in which
the two indices differ), so ``H_n`` is complete once the entries before
``n`` have been folded in, and no later block changes it. Blocks are
convolved with the FFT, which makes a run of ``N`` steps ``O(N log² N)``
instead of ``N²/2``. This is the standard online (relaxed) convolution of
computer algebra with one fixed operand.

A single step can take long when a large block is due (once, half-way
through the run, a block of ``N/2`` entries); only the total matters for a
simulation.
"""

from __future__ import annotations

import numpy as np
from scipy import fft as _fft

# Blocks shorter than this are convolved directly (np.convolve beats an FFT there).
_DIRECT_BLOCK = 64


class OnlineHistory:
    """``Σ_{i<n} p[i]·k[n-i]`` for a sequence ``p`` whose entries before ``n`` are final.

    Parameters
    ----------
    kernel : np.ndarray
        ``k[0..N-1]``; ``k[0]`` multiplies a step's own entry and is not
        used here.
    """

    def __init__(self, kernel: np.ndarray) -> None:
        self.kernel = np.ascontiguousarray(kernel, dtype=float)
        self.n_steps = len(self.kernel)
        #: Number of leading entries of the sequence already folded in.
        self.folded = 0
        self._acc = np.zeros(self.n_steps + 1)
        self._kernel_fft: dict[int, tuple[int, np.ndarray]] = {}

    def history(self, p: np.ndarray, n: int) -> float:
        """``H_n`` for ``0 ≤ n < N``; folds in the entries ``p[folded:n]``, which must not change afterwards."""
        while self.folded < n:
            self.folded += 1
            self._fold(p, self.folded)
        return float(self._acc[n])

    def _fold(self, p: np.ndarray, t: int) -> None:
        """Add the contribution of ``p[t-s:t]`` to steps ``t .. t+s-1`` (``s`` = largest power of two dividing ``t``)."""
        n_steps = self.n_steps
        if t >= n_steps:
            return
        s = t & -t
        if s == 1:
            self._acc[t] += p[t - 1] * self.kernel[1]
            return
        src = p[t - s : t]
        if not src.any():
            return
        count = min(s, n_steps - t)
        # Lags 1 .. 2s-1 reach from the block to its targets; index s-1+j of the
        # full convolution belongs to step t+j.
        ker = self.kernel[1 : min(2 * s, n_steps)]
        if s < _DIRECT_BLOCK:
            self._acc[t : t + count] += np.convolve(src, ker)[s - 1 : s - 1 + count]
            return
        # A circular convolution of length >= len(ker) wraps only into indices
        # below s-1, which are not used.
        cached = self._kernel_fft.get(s)
        if cached is None:
            size = _fft.next_fast_len(len(ker), real=True)
            ker_f = _fft.rfft(ker, size)
            if 8 * s <= n_steps and len(ker) == 2 * s - 1:  # reused at least four times
                self._kernel_fft[s] = (size, ker_f)
        else:
            size, ker_f = cached
        out = _fft.irfft(_fft.rfft(src, size) * ker_f, size)
        self._acc[t : t + count] += out[s - 1 : s - 1 + count]
