"""ASHPB with SolarThermalCollector — tank_circuit placement.

Phase 3 restructuring: all simulation orchestration logic
(activation probe, result assembly, exergy calculation) is
implemented directly in this class.  ``SolarThermalCollector``
is used purely as a **physics engine** (``calc_performance()``),
with no dependency on ``step()``, ``assemble_results()``, or
``calc_exergy()``.

.. note::
   See the project paper for the orchestration logic and energy/exergy
   boundaries used by hybrid (STC + HP, stratified tank) systems.

Usage
-----
::

    from enex_analysis import SolarThermalCollector
    from enex_analysis.ASHPB_STC_tank import ASHPB_STC_tank

    stc = SolarThermalCollector(A_stc=4.0)
    model = ASHPB_STC_tank(
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
from .air_source_heat_pump_boiler import AirSourceHeatPumpBoiler
from .constants import c_w, rho_w
from .subsystems import SolarThermalCollector

if TYPE_CHECKING:
    from .dynamic_context import ControlState, StepContext


class ASHPB_STC_tank(AirSourceHeatPumpBoiler):
    """ASHPB + SolarThermalCollector in *tank_circuit* placement.

    Physical configuration
    ----------------------
    The STC collector loop is connected directly to the storage tank.
    The STC draws water from the tank, heats it via solar energy, and
    returns it through the pump. The STC is activated only when the
    collector's thermal gain exceeds the pump electricity required to
    circulate the loop.

    Orchestration responsibility
    ----------------------------
    This class owns all simulation logic for the STC:

    - ``_run_subsystems``: activation probe + ``calc_performance()``
    - ``_augment_results``: result column assembly (re-evaluates at
      solved tank temperature for accuracy)
    - ``_postprocess``: STC exergy calculation and tank boundary
      correction (``X_tot``, ``Xc_tank``)

    Parameters
    ----------
    stc : SolarThermalCollector
        Pure physics engine.  No ``mode`` constraint required.
    **kwargs
        Forwarded to :class:`AirSourceHeatPumpBoiler`.

    Raises
    ------
    TypeError
        If *stc* is not a :class:`SolarThermalCollector` instance.
    """

    #: Boiling-protection ceiling [°C]. The STC loop must not circulate when the
    #: probed collector outlet would reach/exceed this temperature (liquid water
    #: boiling at 1 atm). Prevents non-physical >100 °C circulation and the
    #: associated phantom gain when the tank is near saturation.
    T_stc_max_C: float = 100.0

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
        """Return STC schedule flag: {"stc": bool}.

        The tank-circuit STC is governed purely by physical feasibility
        (collector gain beats pump power, and the outlet stays below boiling);
        it is not gated by a fixed clock window, so harvesting continues
        whenever the collector can usefully raise the tank.
        """
        return {"stc": True}

    def _collector_gain_exceeds_pump(self, stc_result: dict) -> bool:
        """Return whether collector heat gain justifies running the loop pump."""
        collector_gain_w = float(stc_result.get("Q_stc_w_out", 0.0)) - float(stc_result.get("Q_stc_w_in", 0.0))
        pump_power_w = max(0.0, float(self._stc.E_stc_pump))
        return bool(np.isfinite(collector_gain_w) and collector_gain_w > pump_power_w)

    def _build_residual_fn(
        self,
        ctx,
        ctrl,
        dt_s: float,
        T_tank_w_in_K_n: float,
        T_sup_w_K_n: float,
        tank_level: float,
        sub_states: dict,
    ):
        """Fully implicit residual: Q_STC is evaluated at T_cand each iteration.

        Physics
        -------
        Energy balance (see dynamic_context.tank_mass_energy_residual):

            C(L_{n+1})·T_{n+1} - C(L_n)·T_n
            = dt·[ Q_HP + Q_STC(T_{n+1}) + Q_flow(T_{n+1}) - Q_loss(T_{n+1}) ]

        ``Q_STC(T_{n+1})`` depends on tank inlet temperature which equals
        the (unknown) next-step tank temperature. This override re-evaluates
        ``SolarThermalCollector.calc_performance`` at every ``T_cand``
        candidate, making the solve truly implicit.

        ``stc_active`` is frozen at the value determined by ``_run_subsystems``
        (activation criterion uses ``T_tank_n``; this is intentional).
        """
        stc_active: bool = sub_states.get("stc", {}).get("stc_active", False)
        E_pump: float = self._stc.E_stc_pump if stc_active else 0.0

        def residual(T_cand_K: float) -> float:
            # Q_STC net = heat returned to tank − heat drawn from tank [W]
            # Re-evaluated at T_cand (implicit in T_{n+1})
            stc_r = self._stc.calc_performance(
                I_DN_stc=ctx.I_DN,
                I_dH_stc=ctx.I_dH,
                T_stc_w_in_K=T_cand_K,  # ← T_cand
                T0_K=ctx.T0_K,
                is_active=stc_active,
            )
            Q_stc_net: float = stc_r["Q_stc_w_out"] - stc_r["Q_stc_w_in"]

            # Mixing valve: α = (T_mix_set - T_sup) / (T_cand - T_sup)
            den: float = max(1e-6, T_cand_K - T_sup_w_K_n)
            alp: float = min(1.0, max(0.0, (self.T_mix_w_out_K - T_sup_w_K_n) / den))
            dV_out: float = alp * ctx.dV_mix_w_out
            dV_in: float = dV_out if ctrl.dV_tank_w_in_ctrl is None else ctrl.dV_tank_w_in_ctrl

            # Energy flows [W]
            Q_flow: float = c_w * rho_w * (dV_in * T_tank_w_in_K_n - dV_out * T_cand_K)
            Q_loss: float = self.UA_tank_wall * (T_cand_K - self.T_sur_K)

            # Do not circulate when collector heat gain cannot beat pump electricity.
            if Q_stc_net <= max(0.0, E_pump):
                Q_stc_net = 0.0
                current_E_pump = 0.0
            else:
                current_E_pump = E_pump

            C_curr: float = self.C_tank * max(0.001, ctx.tank_level)
            C_next: float = self.C_tank * max(0.001, tank_level)

            # Energy balance residual, scaled by C_tank to match base solver
            r: float = (
                C_next * T_cand_K
                - C_curr * ctx.T_tank_w_K
                - dt_s * (ctrl.Q_heat_source + current_E_pump + Q_stc_net + Q_flow - Q_loss)
            )
            return r / self.C_tank

        return residual

    def _run_subsystems(
        self,
        ctx: StepContext,
        ctrl: ControlState,
        dt: float,
        T_tank_w_in_K: float,
    ) -> dict[str, dict]:
        """Activation probe + STC performance for *tank_circuit* placement.

        Physics
        -------
        - STC inlet temperature = current tank water temperature (``ctx.T_tank_w_K``)
        - STC is activated only when collector heat gain exceeds pump
          electricity.  If not, a COP=1 electric heater would provide at
          least as much useful heat as circulating the collector loop.
        - No ``T_tank_w_in_override_K``: STC heats tank directly, not
          the mains supply

        Returns
        -------
        dict
            ``{"stc": state_dict}`` where *state_dict* contains:
            - ``stc_active`` (bool)
            - ``stc_result`` (dict from ``calc_performance()``)
            - ``T_tank_w_in_override_K`` (always ``None``)
            - ``E_subsystem`` (pump electricity [W])
            - ``Q_contribution`` (0.0; STC energy enters tank boundary
              via ``X_in_tank_add`` in ``_postprocess``, not here)
        """
        # Probe: calculate STC performance assuming active
        probe = self._stc.calc_performance(
            I_DN_stc=ctx.I_DN,
            I_dH_stc=ctx.I_dH,
            T_stc_w_in_K=ctx.T_tank_w_K,  # inlet = tank temperature
            T0_K=ctx.T0_K,
            is_active=True,
        )
        # Activation criteria: (1) collector heat gain must beat pump electricity,
        # and (2) boiling protection — the probed collector outlet (the outlet if
        # the loop were to circulate this step) must stay below the boiling
        # ceiling. If circulating would drive the outlet to/above T_stc_max_C,
        # the loop does not run and the recorded gain is zero.
        stc_active: bool = (
            bool(ctx.activation_flags.get("stc", False))
            and self._collector_gain_exceeds_pump(probe)
            and bool(probe["T_stc_w_out_K"] < self.T_stc_max_C + 273.15)
        )

        if stc_active:
            stc_result = probe
        else:
            stc_result = self._stc.calc_performance(
                I_DN_stc=ctx.I_DN,
                I_dH_stc=ctx.I_dH,
                T_stc_w_in_K=ctx.T_tank_w_K,
                T0_K=ctx.T0_K,
                is_active=False,
            )

        E_pump: float = self._stc.E_stc_pump if stc_active else 0.0

        return {
            "stc": {
                "stc_active": stc_active,
                "stc_result": stc_result,
                "T_tank_w_in_override_K": None,  # tank_circuit: no inlet override
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

        For *tank_circuit*, STC performance is **re-evaluated at the
        solved tank temperature** (``T_solved_K``) to obtain accurate
        post-solve reporting values.  This matches the original
        ``assemble_results()`` behaviour.

        Columns appended
        ----------------
        ``stc_active [-]``, ``I_DN_stc [W/m2]``, ``I_dH_stc [W/m2]``,
        ``I_sol_stc [W/m2]``, ``Q_sol_stc [W]``, ``S_sol_stc [W/K]``,
        ``X_sol_stc [W]``, ``Q_stc_w_out [W]``, ``Q_stc_pump_w_out [W]``,
        ``Q_stc_w_in [W]``, ``Q_l_stc [W]``,
        ``T_stc_w_out [°C]``, ``T_stc_pump_w_out [°C]``,
        ``T_stc_w_in [°C]``, ``T_stc [°C]``, ``E_stc_pump [W]``
        """
        state = sub_states["stc"]
        stc_active: bool = state["stc_active"]
        E_pump: float = state["E_subsystem"]

        # Re-evaluate at solved tank temperature for accurate reporting
        stc_result = self._stc.calc_performance(
            I_DN_stc=ctx.I_DN,
            I_dH_stc=ctx.I_dH,
            T_stc_w_in_K=T_solved_K,
            T0_K=ctx.T0_K,
            is_active=stc_active,
        )

        if stc_active and (
            not self._collector_gain_exceeds_pump(stc_result)
            or stc_result["T_stc_w_out_K"] >= self.T_stc_max_C + 273.15
        ):
            stc_active = False
            E_pump = 0.0
            stc_result = self._stc.calc_performance(
                I_DN_stc=ctx.I_DN,
                I_dH_stc=ctx.I_dH,
                T_stc_w_in_K=T_solved_K,
                T0_K=ctx.T0_K,
                is_active=False,
            )

        T_stc_w_out_K: float = stc_result["T_stc_w_out_K"]
        T_stc_pump_w_out_K: float = stc_result.get("T_stc_pump_w_out_K", T_stc_w_out_K)

        r["E_tot [W]"] = float(r.get("E_tot [W]", 0.0)) + E_pump

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
                "dV_stc [m3/s]": self._stc.dV_stc_w,
                "T_stc_w_out [°C]": (cu.K2C(T_stc_w_out_K) if not np.isnan(T_stc_w_out_K) else np.nan),
                "T_stc_pump_w_out [°C]": (cu.K2C(T_stc_pump_w_out_K) if not np.isnan(T_stc_pump_w_out_K) else np.nan),
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
        """ASHP core exergy → STC exergy → tank boundary correction.

        STC exergy topology (*tank_circuit*)
        -------------------------------------
        .. code-block:: text

            [Solar irradiance] → STC → [pump] → [tank in]
                                         ↑
                                   [tank out] (water drawn)

        Balance corrections applied to the ASHP base result:

        .. math::

            \\dot{X}_{\\mathrm{tot}} \\mathrel{+}=
                \\dot{X}_{\\mathrm{stc,pump}} \\quad (\\text{pump electricity})

            \\dot{X}_{\\mathrm{c,tank}} \\mathrel{+}=
                \\dot{X}_{\\mathrm{stc,pump,w,out}}
                - \\dot{X}_{\\mathrm{stc,w,in}}
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
        T_stc_pump_w_out_K = cu.C2K(df.get("T_stc_pump_w_out [°C]", df["T_stc_w_out [°C]"]))
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

        # 6. STC exergy destruction (2nd-law: Xc = ΣX_in - ΣX_out ≥ 0)
        is_stc_active = (
            df["stc_active [-]"].fillna(False).astype(bool)
            if "stc_active [-]" in df.columns
            else pd.Series(False, index=df.index)
        )
        if "X_sol_stc [W]" in df.columns:
            Xc_raw = (
                df["X_sol_stc [W]"].fillna(0)
                + df["X_stc_w_in [W]"].fillna(0)
                + E_pump
                - df["X_stc_pump_w_out [W]"].fillna(0)
                - df["X_l_stc [W]"].fillna(0)
            )
            df["Xc_stc [W]"] = np.where(is_stc_active, Xc_raw, 0.0)

        # 7. Tank boundary corrections (tank_circuit specific)
        # STC draws water from tank (X_out_tank_add = X_stc_w_in)
        # STC returns heated water to tank (X_in_tank_add = X_stc_pump_w_out)
        X_in_tank_add = df["X_stc_pump_w_out [W]"].fillna(0)
        X_out_tank_add = df["X_stc_w_in [W]"].fillna(0)

        df["X_tot [W]"] = df["X_tot [W]"].add(E_pump, fill_value=0)
        df["Xc_tank [W]"] = df["Xc_tank [W]"].add(X_in_tank_add - X_out_tank_add, fill_value=0)

        return df
