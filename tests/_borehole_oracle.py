"""Independent time-domain oracle for the plug-flow borehole loop.

:func:`tmhp.borehole_capacity.loop_outlet_response` is a Laplace-domain
series inverted numerically. This oracle marches the same physics in time:
the fluid path is cut into upwind segments, each exchanging heat with its own
finite-volume radial chain (fluid node, pipe node, grout, ground), and the
load is added between outlet and inlet. It shares only the radial grid
builder with the module; the advection, the loop closure and the time
integration are separate. Upwinding is first order, so the agreement
improves linearly with the number of segments.
"""

from __future__ import annotations

import numpy as np
from scipy.linalg import solve_banded


def plug_flow_outlet(
    section, H: float, m_cp: float, t_end: float, *, n_axial: int, dt: float, r_far: float = 1.2
) -> tuple[np.ndarray, np.ndarray]:
    """Outlet temperature rise [m.K/W] of the loop for a unit load per metre, at every step end."""
    grid = section._grid(r_far, 60, 12, capacity=True)
    cap, k = grid.capacity, grid.conductance
    n = cap.size
    diag = np.zeros(n)
    diag[:-1] += k
    diag[1:] += k
    diag[-1] += grid.far_conductance
    advection = m_cp / (H / n_axial)
    ab = np.zeros((3, n))
    ab[0, 1:] = -k
    ab[2, :-1] = -k
    ab[1] = cap / dt + diag
    ab[1, 0] += advection
    temp = np.zeros((n_axial, n))
    times, outlet = [], []
    t = 0.0
    while t < t_end - 1e-9:
        upstream = temp[-1, 0] + H / m_cp  # outlet of the previous step, plus the load
        for j in range(n_axial):
            rhs = cap / dt * temp[j]
            rhs[0] += advection * upstream
            temp[j] = solve_banded((1, 1), ab, rhs, check_finite=False)
            upstream = temp[j, 0]
        t += dt
        times.append(t)
        outlet.append(temp[-1, 0])
    return np.asarray(times), np.asarray(outlet)
