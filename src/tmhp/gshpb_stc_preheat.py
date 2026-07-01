"""GSHPB with SolarThermalCollector — mains_preheat placement.

Phase 3 restructuring: all simulation orchestration logic
(activation probe, result assembly, exergy calculation) is
implemented directly in this class.  ``SolarThermalCollector``
is used purely as a **physics engine** (``calc_performance()``),
with no dependency on ``step()``, ``assemble_results()``, or
``calc_exergy()``.

Usage
-----
::

    from enex_analysis import SolarThermalCollector
    from enex_analysis.gshpb_stc_preheat import GSHPB_STC_preheat

    stc = SolarThermalCollector(A_stc=4.0)
    model = GSHPB_STC_preheat(
        stc=stc,
        ref="R134a",
        hp_capacity=15_000.0,
        T_tank_w_lower_bound=55.0,
        T_tank_w_upper_bound=65.0,
        T_mix_w_out=42.0,
    )
    df = model.analyze_dynamic(...)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from . import calc_util as cu
from .constants import c_w, rho_w
from .ground_source_heat_pump_boiler import GroundSourceHeatPumpBoiler
from .subsystems import SolarThermalCollector

if TYPE_CHECKING:
    from .dynamic_context import ControlState, StepContext


class GSHPB_STC_preheat(GroundSourceHeatPumpBoiler):
    """GSHPB + SolarThermalCollector in *mains_preheat* placement.

    Physical configuration
    ----------------------
    The STC preheats mains cold water before it enters the storage
    tank.  The raised inlet temperature (``T_tank_w_in_override_K``)
    reduces the thermal load on the heat pump compressor.

    Orchestration responsibility
    ----------------------------
    This class owns all simulation logic for the STC:

    - ``_run_subsystems``: activation probe + ``calc_performance()``
      → sets ``T_tank_w_in_override_K`` when active
    - ``_augment_results``: result column assembly (uses pump outlet
      temperature directly; no re-evaluation at solved tank temp)
    - ``_postprocess``: STC exergy calculation.  Tank boundary
      corrections are **not applied** because the preheated inlet
      temperature is already reflected in the core
      ``X_tank_w_in [W]`` column (``X_in_tank_add = 0``).

    Parameters
    ----------
    stc : SolarThermalCollector
        Pure physics engine.  No ``mode`` constraint required.
    **kwargs
        Forwarded to :class:`GroundSourceHeatPumpBoiler`.

    Raises
    ------
    TypeError
        If *stc* is not a :class:`SolarThermalCollector` instance.
    """

    def __init__(
        self,
        *,
        stc: SolarThermalCollector,
        **kwargs,
    ) -> None:
        if not isinstance(stc, SolarThermalCollector):
            raise TypeError(
                f"stc must be a SolarThermalCollector instance, got {type(stc)!r}",
            )
        # Do NOT pass stc to super().__init__ — keeps self._subsystems empty.
        super().__init__(**kwargs)
        self._stc: SolarThermalCollector = stc
        # Expose as self.stc so analyze_dynamic() enables I_DN/I_dH schedules.
        self.stc = stc

    # ------------------------------------------------------------------
    # Hook: subsystem step
    # ------------------------------------------------------------------

    def _needs_solar_input(self) -> bool:
        return True

    def _get_activation_flags(self, hour_of_day: float) -> dict[str, bool]:
        """Return STC schedule flag: {"stc": bool}."""
        return {"stc": self._stc.is_preheat_on(hour_of_day)}

    def _run_subsystems(
        self,
        ctx: StepContext,
        ctrl: ControlState,
        dt: float,
        T_tank_w_in_K: float,
    ) -> dict[str, dict]:
        """Activation probe + STC performance for *mains_preheat* placement.

        Physics
        -------
        - STC inlet temperature = mains cold water temperature
          (``T_tank_w_in_K``, **not** tank temperature)
        - STC is activated only when ``T_stc_pump_w_out > T_mains``
          (collector raises mains water temperature)
        - When active, sets ``T_tank_w_in_override_K`` so the base
          model uses the preheated mains temperature for the tank
          energy balance

        Key difference from *tank_circuit*
        ------------------------------------
        ``T_tank_w_in_override_K`` is ``None`` in tank_circuit and
        ``T_stc_pump_w_out_K`` (preheated mains supply) in preheat.

        Returns
        -------
        dict
            ``{"stc": state_dict}`` where *state_dict* contains:
            - ``stc_active`` (bool)
            - ``stc_result`` (dict from ``calc_performance()``)
            - ``T_tank_w_in_override_K`` (preheated temp or None)
            - ``E_subsystem`` (pump electricity [W])
            - ``Q_contribution`` (0.0)
        """
        dV_feed: float = ctrl.dV_tank_w_in_ctrl if ctrl.dV_tank_w_in_ctrl is not None else ctx.dV_mix_w_out

        stc_active: bool = False
        stc_result: dict = {}

        if ctx.activation_flags.get("stc", False) and dV_feed > 0:
            # Probe: STC heats mains water flowing in at dV_feed
            probe = self._stc.calc_performance(
                I_DN_stc=ctx.I_DN,
                I_dH_stc=ctx.I_dH,
                T_stc_w_in_K=T_tank_w_in_K,  # inlet = mains temperature
                T0_K=ctx.T0_K,
                dV_stc=dV_feed,
                is_active=True,
            )
            stc_active = probe["T_stc_w_out_K"] > T_tank_w_in_K

            if stc_active:
                stc_result = probe
            else:
                stc_result = self._stc.calc_performance(
                    I_DN_stc=ctx.I_DN,
                    I_dH_stc=ctx.I_dH,
                    T_stc_w_in_K=T_tank_w_in_K,
                    T0_K=ctx.T0_K,
                    dV_stc=dV_feed,
                    is_active=False,
                )
        else:
            # Preheat window inactive or no flow: evaluate for reporting only
            stc_result = self._stc.calc_performance(
                I_DN_stc=ctx.I_DN,
                I_dH_stc=ctx.I_dH,
                T_stc_w_in_K=T_tank_w_in_K,
                T0_K=ctx.T0_K,
                dV_stc=max(dV_feed, 1e-6),  # non-zero dV for physics validity
                is_active=False,
            )

        E_pump: float = self._stc.E_stc_pump if stc_active else 0.0

        # KEY: override mains temp when STC is active
        T_override: float | None = stc_result.get("T_stc_pump_w_out_K") if stc_active else None

        return {
            "stc": {
                "stc_active": stc_active,
                "stc_result": stc_result,
                "T_tank_w_in_override_K": T_override,
                "E_subsystem": E_pump,
                "Q_contribution": 0.0,
            }
        }

    # ------------------------------------------------------------------
    # Hook: result assembly
    # ------------------------------------------------------------------

    def _augment_results(
        self,
        r: dict,
        ctx: StepContext,
        ctrl: ControlState,
        sub_states: dict[str, dict],
        T_solved_K: float,
    ) -> dict:
        """Assemble STC result columns for the step result dict.

        For *mains_preheat*, the STC outlet temperature is the pump
        outlet temperature from the pre-solve step result.
        Unlike *tank_circuit*, there is **no re-evaluation** at
        the solved tank temperature because the STC operates on the
        mains supply, not the tank water.

        Columns appended
        ----------------
        ``stc_active [-]``, ``I_DN_stc [W/m2]``, ``I_dH_stc [W/m2]``,
        ``I_sol_stc [W/m2]``, ``Q_sol_stc [W]``, ``S_sol_stc [W/K]``,
        ``X_sol_stc [W]``, ``Q_stc_w_out [W]``, ``Q_stc_pump_w_out [W]``,
        ``Q_stc_w_in [W]``, ``Q_l_stc [W]``,
        ``T_stc_w_out [°C]``, ``T_stc_w_in [°C]``, ``T_stc [°C]``,
        ``E_stc_pump [W]``

        Note: ``T_stc_pump_w_out [°C]`` is **not** added for *mains_preheat*
        because the pump outlet is the effective mains supply temperature
        (already reflected in the core ``T_tank_w_in [°C]`` column).
        """
        state = sub_states["stc"]
        stc_active: bool = state["stc_active"]
        stc_result: dict = state["stc_result"]
        E_pump: float = state["E_subsystem"]

        # For mains_preheat: T_stc_w_out = pump outlet (mains supply T)
        T_stc_w_out_K: float = state["stc_result"].get("T_stc_pump_w_out_K", np.nan)

        r.update(
            {
                "stc_active [-]": stc_active,
                "I_DN_stc [W/m2]": ctx.I_DN,
                "I_dH_stc [W/m2]": ctx.I_dH,
                "I_sol_stc [W/m2]": stc_result.get("I_sol_stc", np.nan),
                "Q_sol_stc [W]": stc_result.get("Q_sol_stc", np.nan),
                "S_sol_stc [W/K]": stc_result.get("S_sol_stc", np.nan),
                "X_sol_stc [W]": stc_result.get("X_sol_stc", np.nan),
                "Q_stc_w_out [W]": stc_result.get("Q_stc_w_out", 0.0),
                "Q_stc_pump_w_out [W]": stc_result.get("Q_stc_pump_w_out", 0.0),
                "Q_stc_w_in [W]": stc_result.get("Q_stc_w_in", 0.0),
                "Q_l_stc [W]": stc_result.get("Q_l_stc", np.nan),
                "dV_stc [m3/s]": (ctrl.dV_tank_w_in_ctrl if ctrl.dV_tank_w_in_ctrl is not None else ctx.dV_mix_w_out),
                "T_stc_w_out [°C]": (cu.K2C(T_stc_w_out_K) if not np.isnan(T_stc_w_out_K) else np.nan),
                "T_stc_w_in [°C]": cu.K2C(T_solved_K),
                "T_stc [°C]": cu.K2C(stc_result.get("T_stc_K", np.nan)),
                "E_stc_pump [W]": E_pump,
            }
        )
        return r

    # ------------------------------------------------------------------
    # Hook: post-processing (exergy)
    # ------------------------------------------------------------------

    def _postprocess(self, df: pd.DataFrame) -> pd.DataFrame:
        """ASHP core exergy → STC exergy (no tank boundary correction).

        STC exergy topology (*mains_preheat*)
        -------------------------------------
        .. code-block:: text

            [Solar] → STC → [pump] → mains supply (→ tank inlet)
                                           ↑
                              T_tank_w_in already reflects preheated temp
                              → X_tank_w_in already accounts for STC gain

        Because the preheated mains temperature is baked into the core
        ``X_tank_w_in [W]`` column (via ``T_tank_w_in_override_K``),
        no additional ``Xc_tank`` correction is needed.

        Only ``X_tot [W]`` is corrected by pump electricity.

        .. math::

            \\dot{X}_{\\mathrm{tot}} \\mathrel{+}=
                \\dot{X}_{\\mathrm{stc,pump}}
        """
        from .thermodynamics import calc_exergy_flow

        # 1. Standard ASHP exergy
        df = super()._postprocess(df)

        # 2. Guard: STC columns must be present
        if "T_stc_w_in [°C]" not in df.columns or "T_stc_w_out [°C]" not in df.columns:
            return df

        T0_K = cu.C2K(df["T0 [°C]"])
        T_stc_w_in_K = cu.C2K(df["T_stc_w_in [°C]"])
        T_stc_w_out_K = cu.C2K(df["T_stc_w_out [°C]"])
        # For mains_preheat, pump_w_out = w_out (no separate pump column)
        T_stc_pump_w_out_K = T_stc_w_out_K
        T_stc_K = cu.C2K(df["T_stc [°C]"])

        # Heat capacity rate [W/K] from explicit flow rate
        G_stc = c_w * rho_w * df["dV_stc [m3/s]"].fillna(0)

        # 3. Water exergy flows
        df["X_stc_w_in [W]"] = calc_exergy_flow(G_stc, T_stc_w_in_K, T0_K)
        df["X_stc_w_out [W]"] = calc_exergy_flow(G_stc, T_stc_w_out_K, T0_K)
        df["X_stc_pump_w_out [W]"] = calc_exergy_flow(G_stc, T_stc_pump_w_out_K, T0_K)

        # 4. Pump electricity = exergy
        E_pump = df["E_stc_pump [W]"].fillna(0)
        df["X_stc_pump [W]"] = E_pump

        # 5. Heat loss exergy
        df["X_l_stc [W]"] = df["Q_l_stc [W]"].fillna(0) * (1 - T0_K / T_stc_K.replace(0, np.nan))

        # 6. STC exergy destruction
        is_stc_active = df["stc_active [-]"].fillna(False).astype(bool) if "stc_active [-]" in df.columns else False
        if "X_sol_stc [W]" in df.columns:
            Xc_raw = (
                df["X_sol_stc [W]"].fillna(0)
                + df["X_stc_w_in [W]"].fillna(0)
                + E_pump
                - df["X_stc_pump_w_out [W]"].fillna(0)
                - df["X_l_stc [W]"].fillna(0)
            )
            df["Xc_stc [W]"] = np.where(is_stc_active, Xc_raw, 0.0)

        # 7. Only X_tot correction (no Xc_tank correction for mains_preheat)
        # X_in_tank_add = 0, X_out_tank_add = 0:
        # the preheated inlet temp is already in X_tank_w_in [W].
        df["X_tot [W]"] = df["X_tot [W]"].add(E_pump, fill_value=0)

        return df
