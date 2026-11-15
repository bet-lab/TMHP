"""``capacity_binding`` names the limit active at the operating point returned.

An 8 kW R290 ASHPB at a 55 °C tank and -12 °C outdoor air delivers the same
point at its speed ceiling for every request above it. A large request used
to be reduced through the duty search and named the limit the search stopped
on (``"outdoor_coil"``: the outdoor coil refuses gross duties above about
33 kW there), while a smaller one reported ``"compressor"`` -- for one and the
same operating point. The name must follow the point, and only the name may
have moved: the rest of the result is the same for every request.
"""

from __future__ import annotations

import math
import warnings

import pytest

from tmhp import AirSourceHeatPumpBoiler
from tmhp.compressor_envelope import (
    BINDING_COMPRESSOR,
    BINDING_CYCLE,
    BINDING_PRESSURE_RATIO,
    _reduced_binding,
)

#: Keys that are the request itself, or a solver residual (converged to ~1e-5 W
#: on a ~9 kW duty whichever duty the search started from).
_REQUEST_KEYS = {"Q_request [W]", "PLR_request [-]"}


def _same(a, b) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        return (math.isnan(a) and math.isnan(b)) or math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-9)
    return a == b


@pytest.fixture(scope="module")
def clamped_rows() -> dict[float, dict]:
    model = AirSourceHeatPumpBoiler(hp_capacity=8000.0, ref="R290")
    rows = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        # 12 and 20 kW are met at the speed ceiling directly; 32 and 40 kW are
        # refused by the outdoor coil and reduced onto the same point.
        for request in (12000.0, 20000.0, 32000.0, 40000.0):
            rows[request] = model.analyze_steady(T_tank_w=55.0, T0=-12.0, Q_ref_tank=request)
    return rows


def test_every_request_runs_at_the_same_speed_ceiling_point(clamped_rows):
    for request, row in clamped_rows.items():
        assert row["failure_reason"] == "none", request
        assert row["capacity_clamped"] == "max", request
        assert row["Q_request [W]"] == request
        assert row["Q_ref_tank [W]"] < 12000.0


def test_binding_is_the_same_for_every_request(clamped_rows):
    assert {row["capacity_binding"] for row in clamped_rows.values()} == {BINDING_COMPRESSOR}


def test_only_the_request_differs(clamped_rows):
    rows = list(clamped_rows.values())
    base = rows[0]
    for row in rows[1:]:
        assert set(row) == set(base)
        diff = [
            key for key in base if key not in _REQUEST_KEYS and "residual" not in key and not _same(base[key], row[key])
        ]
        assert not diff, f"request {row['Q_request [W]']:.0f} W: {diff}"


class _Model:
    PR_cycle_max = 10.0


def _point(ratio: float, clamped: str | None, binding: str | None = None) -> dict:
    point = {"P_ref_cmp_in [Pa]": 1.0e5, "P_ref_cmp_out [Pa]": ratio * 1.0e5, "capacity_clamped": clamped}
    if binding is not None:
        point["capacity_binding"] = binding
    return point


@pytest.mark.parametrize(
    ("point", "above", "expected"),
    [
        # Nothing active at the point: the limit the next larger duty met.
        (_point(4.0, None), {"failure_reason": "discharge_temp_above_max"}, "discharge_temperature"),
        (_point(4.0, None), {"failure_reason": "hx_not_converged"}, "outdoor_coil"),
        # The point runs at rps_max: the speed ceiling, whatever refused above it.
        (_point(4.0, "max"), {"failure_reason": "hx_not_converged"}, BINDING_COMPRESSOR),
        (_point(4.0, "max"), {"failure_reason": "discharge_temp_above_max"}, BINDING_COMPRESSOR),
        # A family's own name for its ceiling is kept.
        (_point(4.0, "max", "source_fluid_freezing"), {"failure_reason": "hx_not_converged"}, "source_fluid_freezing"),
        # On the pressure-ratio ceiling first, also at rps_max.
        (_point(10.0, None), {"failure_reason": "hx_not_converged"}, BINDING_PRESSURE_RATIO),
        (_point(10.0, "max"), {"failure_reason": "hx_not_converged"}, BINDING_PRESSURE_RATIO),
        # No named limit above.
        (_point(4.0, None), {"failure_reason": "cycle_invalid"}, BINDING_CYCLE),
    ],
)
def test_precedence_of_active_limits(point, above, expected):
    bindings = {"hx_not_converged": "outdoor_coil"}
    assert _reduced_binding(_Model(), point, above, bindings) == expected
