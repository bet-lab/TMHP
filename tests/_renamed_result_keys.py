"""Result keys that were renamed or removed (#96, round 5): old name -> new name.

The families return only the new names -- no alias, no old key next to the new
one. ``None`` means the key was removed without a one-key replacement
(``cmp_rps [rev/s]`` is ``cmp_rpm [rpm] / 60``). The table is the one on
``docs/source/concepts/result-keys.rst`` ("Renamed keys");
``tests/test_result_key_names.py`` and ``tests/test_docs_alignment.py`` hold the
results, the repository and that page to it.
"""

from __future__ import annotations

RENAMED_RESULT_KEYS: dict[str, str | None] = {
    "eta_is [-]": "eta_cmp_isen [-]",
    "eta_v [-]": "eta_cmp_vol [-]",
    "eta_em [-]": "eta_cmp [-]",
    "pressure_ratio": "pr_cmp [-]",
    "pr_clamped": "pr_floor_active",
    "Q_ref_tank_request [W]": "Q_request [W]",
    "Q_tank_load [W]": "Q_request [W]",
    "ground_flow_ref_ratio": "ground_flow_ratio",
    "fan_flow_min_limit": "ou_fan_flow_min_limit",
    "fan_flow_max_limit": "ou_fan_flow_max_limit",
    "cmp_rps [rev/s]": None,
    "T_cond [°C]": "T_ref_cond_sat_l [°C]",
    "err_Q_evap [W]": "source_hx_residual [W]",
    "err_Q_ground [W]": "source_hx_residual [W]",
    "err_Q_water [W]": "source_hx_residual [W]",
}
