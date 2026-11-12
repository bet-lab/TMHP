"""ASHPB Scenario: Heat Pump driven by PV + ESS with Grid/Dump integration.

Energy routing (all logic lives in :func:`tmhp.subsystems.route_pv_ess`,
the subsystems are pure physics):

1. PV generation  → ``pv.calc_performance()`` (array output ``E_pv_out``, then the
   charge controller: ``E_ctrl_out = eta_ctrl · E_pv_out``)
2. DC routing of ``E_ctrl_out``:
   - PV surplus   → ``ess.charge()``; leftover → dump
   - PV deficit   → ``ess.discharge()``; leftover →  grid import
3. Inverter conversion loss applied to the DC supply (PV direct + battery discharge)
4. Grid import covers any remaining AC shortfall

There is **no grid export**: DC power that neither the load nor the battery can
take is discarded and reported as ``E_dump [W]``.

Per-step balances (every term is a result column, [W])::

    E_pv_out  = Q_l_ctrl + E_pv_to_inv + E_ess_chg + E_dump
    E_tot     = E_inv_out + E_grid_import            (AC load)
    E_inv_out = eta_inv · (E_pv_to_inv + E_ess_dis),  Q_l_inv = (1-eta_inv)·(...)
    E_ess_chg - E_ess_dis = C_ess_max·ΔSOC_ess/dt + Q_l_ess

``E_tot`` is the whole electric draw: compressor + outdoor fan + UV lamp.

.. note::
   See the project paper for the orchestration logic and energy/exergy
   boundaries used by hybrid (PV + ESS + HP) systems.
"""

from __future__ import annotations

import pandas as pd

from ._validation import validate_ctor
from .air_source_heat_pump_boiler import AirSourceHeatPumpBoiler
from .dynamic_context import DynamicState
from .subsystems import (
    EnergyStorageSystem,
    PhotovoltaicSystem,
    add_uv_power_to_e_tot,
    pv_ess_postprocess,
    pv_ess_result_columns,
    route_pv_ess,
)


class ASHPB_PV_ESS(AirSourceHeatPumpBoiler):
    """ASHPB scenario where the heat pump is supplied by PV + ESS + Grid.

    The PV/ESS routing is resolved **synchronously** inside
    ``_augment_results`` after the HP compressor load is known.
    No 1-step lag: the PV energy is allocated to the exact
    HP load produced in the same timestep.

    The battery state of charge belongs to a run: it is restored to
    ``ess.SOC_init`` when a run starts (``make_initial_state``, which both
    ``analyze_dynamic`` and an external ``step()`` driver call), so repeating a
    run on the same model reproduces it.

    Parameters
    ----------
    pv : PhotovoltaicSystem
        Pure-physics PV + charge-controller model.
    ess : EnergyStorageSystem
        Pure-physics battery model with ``charge()`` / ``discharge()``.
    eta_inv : float
        Inverter DC→AC efficiency [–], ``0 < eta_inv <= 1``.
    T_inv_K : float
        Inverter temperature for entropy calculation [K].
    **kwargs
        Forwarded to :class:`AirSourceHeatPumpBoiler`.
    """

    def __init__(
        self,
        *,
        pv: PhotovoltaicSystem,
        ess: EnergyStorageSystem | None = None,
        eta_inv: float = 0.95,
        T_inv_K: float = 313.15,
        **kwargs,
    ) -> None:
        validate_ctor("PV_ESS", locals())
        if not isinstance(pv, PhotovoltaicSystem):
            raise TypeError("pv must be a PhotovoltaicSystem instance")
        super().__init__(**kwargs)
        self._pv = pv
        self._ess = ess if ess is not None else EnergyStorageSystem()
        self._eta_inv = eta_inv
        self._T_inv_K = T_inv_K
        # expose pv so analyze_dynamic supplies I_DN / I_dH context
        self.pv = pv

    # ── per-run state ─────────────────────────────────────────────

    def make_initial_state(self, T_tank_w_init_C: float, tank_level_init: float = 1.0) -> DynamicState:
        """Start of a run: tank state as the base class, battery back at ``SOC_init``."""
        self._ess.reset()
        return super().make_initial_state(T_tank_w_init_C, tank_level_init)

    # ── hook overrides ────────────────────────────────────────────

    def _needs_solar_input(self) -> bool:
        return True

    def _get_activation_flags(self, hour_of_day: float) -> dict[str, bool]:
        # PV is passive — no schedule activation needed.
        return {}

    def _run_subsystems(
        self,
        ctx,
        ctrl,
        dt,
        T_tank_w_in_K,
    ) -> dict[str, dict]:
        # PV is called after the HP load is known; only attached subsystems
        # (e.g. a UV lamp) are stepped here.
        self._current_dt = dt
        return super()._run_subsystems(ctx, ctrl, dt, T_tank_w_in_K)

    def _augment_results(
        self,
        base_result: dict,
        ctx,
        ctrl,
        sub_states: dict,
        T_solved_K: float,
    ) -> dict:
        """Resolve PV/ESS/Grid routing after HP load is known."""
        # 1. Base HP results (+ attached subsystems such as UV)
        hp_res = super()._augment_results(base_result, ctx, ctrl, sub_states, T_solved_K)

        # 2. Electric load this step [W]: compressor + outdoor fan + UV lamp (NaN-safe)
        E_load = add_uv_power_to_e_tot(hp_res)

        # 3. PV generation (pure physics)
        dt = getattr(self, "_current_dt", 3600.0)
        T0_K = ctx.T0_K
        pv_r = self._pv.calc_performance(ctx.I_DN, ctx.I_dH, T0_K)

        # 4. Route the DC power and settle the AC side
        route = route_pv_ess(pv_r, self._ess, E_load, self._eta_inv, dt, T0_K)

        # 5. Result columns
        hp_res.update(pv_ess_result_columns(pv_r, route, self._eta_inv, self._T_inv_K, T0_K))
        return hp_res

    # ── post-processing ───────────────────────────────────────────

    def _postprocess(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df

        df = super()._postprocess(df)
        return pv_ess_postprocess(df)
