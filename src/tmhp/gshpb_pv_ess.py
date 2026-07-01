"""GSHPB Scenario: Heat Pump driven by PV + ESS with Grid/Dump integration.

Energy routing (all logic lives here, subsystems are pure physics):

1. PV generation  → ``pv.calc_performance()``
2. DC routing:
   - PV surplus   → ``ess.charge()``; leftover → dump
   - PV deficit   → ``ess.discharge()``; leftover →  grid import
3. Inverter conversion loss applied to DC supply
4. Grid import covers any remaining AC shortfall

"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import calc_util as cu
from .ground_source_heat_pump_boiler import GroundSourceHeatPumpBoiler
from .subsystems import EnergyStorageSystem, PhotovoltaicSystem


class GSHPB_PV_ESS(GroundSourceHeatPumpBoiler):
    """GSHPB scenario where the heat pump is supplied by PV + ESS + Grid.

    The PV/ESS routing is resolved **synchronously** inside
    ``_augment_results`` after the HP electrical load is known.
    No 1-step lag: the PV energy is allocated to the exact
    HP load produced in the same timestep.

    Parameters
    ----------
    pv : PhotovoltaicSystem
        Pure-physics PV + charge-controller model.
    ess : EnergyStorageSystem
        Pure-physics battery model with ``charge()`` / ``discharge()``.
    eta_inv : float
        Inverter DC→AC efficiency [–].
    T_inv_K : float
        Inverter temperature for entropy calculation [K].
    **kwargs
        Forwarded to :class:`GroundSourceHeatPumpBoiler`.
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
        if not isinstance(pv, PhotovoltaicSystem):
            raise TypeError("pv must be a PhotovoltaicSystem instance")
        super().__init__(**kwargs)
        self._pv = pv
        self._ess = ess if ess is not None else EnergyStorageSystem()
        self._eta_inv = eta_inv
        self._T_inv_K = T_inv_K
        # expose pv so analyze_dynamic supplies I_DN / I_dH context
        self.pv = pv

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
        # Let base class run STC/UV. PV is called after HP load is known.
        self._current_dt = dt
        return super()._run_subsystems(ctx, ctrl, dt, T_tank_w_in_K)

    def _augment_results(
        self,
        r: dict,
        ctx,
        ctrl,
        sub_states: dict,
        T_solved_K: float,
    ) -> dict:
        """Resolve PV/ESS/Grid routing after HP load is known."""
        # 1. Base HP results
        hp_res = super()._augment_results(r, ctx, ctrl, sub_states, T_solved_K)

        # 2. HP AC load this step [W] (NaN-safe)
        e_cmp = float(hp_res.get("E_cmp [W]", 0.0))
        e_pmp = float(hp_res.get("E_pmp [W]", 0.0))
        E_hp_load = (0.0 if np.isnan(e_cmp) else e_cmp) + (0.0 if np.isnan(e_pmp) else e_pmp)

        # 3. PV generation (pure physics)
        dt = getattr(self, "_current_dt", 3600.0)
        T0_K = ctx.T0_K
        pv_r = self._pv.calc_performance(ctx.I_DN, ctx.I_dH, T0_K)
        E_ctrl_out: float = pv_r["E_ctrl_out"]  # DC from charge controller

        # 4. DC required at inverter input to satisfy HP AC load
        E_dc_req: float = E_hp_load / self._eta_inv if self._eta_inv > 0 else 0.0

        # 5. Route DC power  (3-way split: HP-off | PV surplus | PV deficit)
        if E_hp_load == 0.0:
            # ── HP off: route all PV to ESS, dump overflow ──────────────
            E_dc_to_inv = 0.0
            ess_r = self._ess.charge(E_ctrl_out, dt, T0_K)
            E_dump = max(0.0, E_ctrl_out - ess_r["E_ess_chg"])
            E_grid_import = 0.0
        elif E_ctrl_out >= E_dc_req:
            # ── HP on + PV surplus: supply HP, charge ESS with excess ───
            E_dc_to_inv = E_dc_req
            E_dc_excess = E_ctrl_out - E_dc_req
            ess_r = self._ess.charge(E_dc_excess, dt, T0_K)
            E_dump = max(0.0, E_dc_excess - ess_r["E_ess_chg"])
            E_grid_import = 0.0
        else:
            # ── HP on + PV deficit: discharge ESS, then fall back to grid
            E_dc_to_inv_from_pv = E_ctrl_out
            E_ess_needed = E_dc_req - E_dc_to_inv_from_pv
            ess_r = self._ess.discharge(E_ess_needed, dt, T0_K)
            E_dc_to_inv = E_dc_to_inv_from_pv + ess_r["E_ess_dis"]
            E_dump = 0.0
            # Remaining AC shortfall supplied by grid
            E_inv_out_available = self._eta_inv * E_dc_to_inv
            E_grid_import = max(0.0, E_hp_load - E_inv_out_available)

        # 6. Inverter physics
        E_inv_out = min(self._eta_inv * E_dc_to_inv + E_grid_import, E_hp_load)
        Q_l_inv = (1.0 - self._eta_inv) * E_dc_to_inv
        S_l_inv = Q_l_inv / self._T_inv_K
        X_l_inv = Q_l_inv - S_l_inv * T0_K
        X_c_inv = S_l_inv * T0_K

        # 7. Assemble result dict
        pv_cols = {
            "I_sol_pv [W/m2]": pv_r["I_sol_pv"],
            "T_pv [°C]": cu.K2C(pv_r["T_pv_K"]),
            "E_pv_out [W]": pv_r["E_pv_out"],
            "E_ctrl_out [W]": E_ctrl_out,
            "X_sol [W]": pv_r["X_sol"],
            "X_pv_out [W]": pv_r["X_pv_out"],
            "X_ctrl_out [W]": pv_r["X_ctrl_out"],
            "X_c_pv [W]": pv_r["X_c_pv"],
            "X_c_ctrl [W]": pv_r["X_c_ctrl"],
            "X_l_pv [W]": pv_r["X_l_pv"],
            "X_l_ctrl [W]": pv_r["X_l_ctrl"],
        }
        ess_cols = {
            "E_ess_chg [W]": ess_r["E_ess_chg"],
            "E_ess_dis [W]": ess_r["E_ess_dis"],
            "SOC_ess [-]": ess_r["SOC_ess"],
            "X_c_ess [W]": ess_r["X_c_ess"],
            "X_l_ess [W]": ess_r["X_l_ess"],
        }
        route_cols = {
            "E_inv_out [W]": E_inv_out,
            "E_grid_import [W]": E_grid_import,
            "E_dump [W]": E_dump,
            "X_c_inv [W]": X_c_inv,
            "X_l_inv [W]": X_l_inv,
        }
        hp_res.update(pv_cols)
        hp_res.update(ess_cols)
        hp_res.update(route_cols)
        return hp_res

    # ── post-processing ───────────────────────────────────────────

    def _postprocess(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df

        df = super()._postprocess(df)

        # System exergy input: Solar exergy + Grid (= pure electricity = exergy)
        df["X_tot_sys [W]"] = df["X_sol [W]"].fillna(0) + df["E_grid_import [W]"].fillna(0)
        if "X_uv [W]" in df.columns:
            df["X_tot_sys [W]"] += df["X_uv [W]"].fillna(0)

        # Total exergy destruction across system (all Xc_ components)
        df["Xc_sys_tot [W]"] = df.filter(regex=r"^Xc_").sum(axis=1)

        # Grid COP: useful heat / grid electricity drawn
        df["cop_grid [-]"] = df["Q_tank_w_out [W]"] / df["E_grid_import [W]"].replace(0, np.nan)

        # System exergy efficiency
        df["ex_eff_sys [-]"] = (df["Xst_tank [W]"] + df["X_tank_w_out [W]"]) / df["X_tot_sys [W]"].replace(0, np.nan)

        return df
