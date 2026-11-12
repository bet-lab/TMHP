====================================
Air-source heat pump boiler (ASHPB)
====================================

.. |ashpb| raw:: html

   <span class="glossary" data-term="ashpb">ASHPB</span>

The ``ASHPB`` family pairs the shared refrigerant cycle with an
outdoor-coil source side and a DHW tank sink side. This is the
validated reference example used by the Getting Started flow.

Overview
========

|ashpb| solves the closed refrigerant cycle every step against an
outdoor coil (ε-NTU air-side) and a tank-coupled condenser. The
class is :class:`tmhp.AirSourceHeatPumpBoiler`. Three composed
variants extend it with subsystems:

- :class:`tmhp.ASHPB_STC_preheat` — STC heats the mains feed before it
  reaches the tank.
- :class:`tmhp.ASHPB_STC_tank` — STC charges a separate top node of a
  stratified tank.
- :class:`tmhp.ASHPB_PV_ESS` — PV generation + battery storage routes
  electricity to the compressor and auxiliaries before drawing grid.

.. figure:: ../_static/system_schematic.png
    :alt: Reference ASHPB instantiation with outdoor unit,
        refrigerant loop, hot-water tank, and mixing valve.
    :align: center
    :width: 100%

    Reference ASHPB instantiation. This physical wiring view is useful
    for the validated air-source DHW case, while the shared compressor /
    expander / heat-exchanger cycle core is reused by the other TMHP
    source and demand-side families.

Base usage
==========

.. code-block:: python

   from tmhp import AirSourceHeatPumpBoiler

   ashpb = AirSourceHeatPumpBoiler(ref="R32")

   # Steady-state snapshot
   result = ashpb.analyze_steady(
       T_tank_w=55.0,    # tank water [°C]
       T0=7.0,           # outdoor air [°C]
       Q_ref_tank=8_000, # target condenser duty [W]
   )

   # Time-stepping dynamic run — see Getting Started for full schedule
   # construction.
   # df = ashpb.analyze_dynamic(...)

Default compressor efficiencies
===============================

A bare ``AirSourceHeatPumpBoiler`` evaluates three independent
second-order polynomials from :mod:`tmhp.compressor_efficiency`
(coefficient set ``BITZER-three-C3-absolute-N-2026-10-06``, a scroll-compressor
fit to manufacturer calculation data):

.. math::

   \eta = b_0 + b_1\,PR + b_2\,N + b_3\,PR^2 + b_4\,N^2 + b_5\,PR\,N

Here :math:`PR` is the pressure ratio and :math:`N` is the physical shaft
speed in rev/s (``rps``); there is no relative-speed normalisation. The
same form is used for the volumetric (``eta_cmp_vol``), isentropic
(``eta_cmp_isen``) and effective electromechanical (``eta_cmp``)
efficiencies, each with its own coefficients, which are listed in
``validation/coefficients/BITZER-three-C3-absolute-N-2026-10-06/coefficients.csv``
and in the tuple constants of the module. The design notes are in
``docs/notes/compressor-bitzer-defaults.md``.

.. warning::

   The fit covers pressure ratio 1.50–7.15 and shaft speed
   33.8–72.5 rev/s (the convex hull of 3,899 scroll data points). The
   model's operating range (``rps_min`` = 15 rev/s, ``rps_max`` up to
   ``max(min(2 × rps_rated, 120), 1.5 × rps_rated)``, ``PR_cycle_max`` = 11) reaches outside
   it. Outside the box the guarded fit holds the boundary value (high PR,
   high speed) or continues the boundary slope downward (low PR, low speed),
   and steady results report ``efficiency_extrapolated``. At PR above 7 the
   fitted isentropic efficiency is 0.37–0.45, which drives the computed
   discharge temperature of R32 to 200–290 °C. The discharge-temperature
   control holds ``T_dis_max`` by lowering the suction superheat and then
   letting the suction go wet down to ``x_suction_min`` (0.9); points it
   cannot hold -- R32 above roughly 220 °C dry -- are refused (see
   :doc:`/developer/reference-operating-limits`).

An explicitly supplied float or callable for the corresponding constructor
argument overrides these defaults. The earlier linear correlations
(``1.0 - 0.020 * (r_p - 1.0)`` and similar) survive only as
``tmhp.compressor_efficiency_legacy`` and in the
``equipment_presets`` module; they are not the default.

Source-side mechanics
=====================

ASHPB models its outdoor coil as a variable-speed fan coupled to an
ε-NTU heat exchanger. Fan electrical power follows an ASHRAE
90.1-style cubic-with-speed curve; coil ε is recomputed each step
from the resolved refrigerant mass flow.

Sink-side mechanics
===================

The sink is a single-node DHW tank (with optional stratification on
the ``_STC_tank`` variant). The tank energy balance is solved
implicitly per step with ``fsolve``, jointly with the refrigerant
cycle.

Composed variants
=================

.. tab-set::
   :class: composition-tabs

   .. tab-item:: Base

      The standalone ASHPB: outdoor coil ε-NTU evaporator, R32 cycle,
      DHW tank charge.

      .. code-block:: python

         from tmhp import AirSourceHeatPumpBoiler

         ashpb = AirSourceHeatPumpBoiler(ref="R32")
         result = ashpb.analyze_steady(
             T_tank_w=55.0,
             T0=7.0,
             Q_ref_tank=8_000,
         )

   .. tab-item:: + STC preheat

      Adds a flat-plate STC that preheats mains water entering the
      tank. Reduces the tank-charge duty the heat pump has to deliver.

      .. code-block:: python

         from tmhp import ASHPB_STC_preheat
         from tmhp.subsystems import SolarThermalCollector

         stc = SolarThermalCollector(A_stc=4.0, stc_tilt=35.0, stc_azimuth=180.0)
         model = ASHPB_STC_preheat(stc=stc, ref="R32")

   .. tab-item:: + STC stratified

      STC charges a separate top node of a stratified tank; the heat
      pump charges the bottom. Top-of-tank water is drawn first.

      .. code-block:: python

         from tmhp import ASHPB_STC_tank
         from tmhp.subsystems import SolarThermalCollector

         stc = SolarThermalCollector(A_stc=4.0, stc_tilt=35.0, stc_azimuth=180.0)
         model = ASHPB_STC_tank(stc=stc, ref="R32")

   .. tab-item:: + PV / ESS

      Photovoltaic generation + ESS preferentially feeds the
      compressor and auxiliaries.

      .. code-block:: python

         from tmhp import ASHPB_PV_ESS
         from tmhp.subsystems import EnergyStorageSystem, PhotovoltaicSystem

         model = ASHPB_PV_ESS(
             pv=PhotovoltaicSystem(),
             ess=EnergyStorageSystem(),
             ref="R32",
         )

STC preheat
-----------

A :class:`~tmhp.subsystems.SolarThermalCollector` heats the cold mains
water before it reaches the DHW tank, so the heat pump sees pre-heated
water during the preheat window.

.. code-block:: python

   import numpy as np

   from tmhp import ASHPB_STC_preheat
   from tmhp.subsystems import SolarThermalCollector

   stc = SolarThermalCollector(
       A_stc=4.0,             # 4 m² collector area
       stc_tilt=35.0,
       stc_azimuth=180.0,
   )

   model = ASHPB_STC_preheat(stc=stc, ref="R32")

Drive it the same way as base ASHPB, adding irradiance schedules
(``I_DN_schedule``, ``I_dH_schedule``) — both in W/m² per step:

.. code-block:: python

   dt_s          = 60
   n_steps       = 24 * 3600 // dt_s
   hour_of_day   = np.arange(n_steps) / 60.0

   # Crude clear-sky irradiance: bell from 06:00 to 18:00.
   day_window    = (hour_of_day >= 6.0) & (hour_of_day <= 18.0)
   sun_shape     = np.sin(np.pi * (hour_of_day - 6.0) / 12.0)
   I_DN          = np.where(day_window, 800.0 * sun_shape, 0.0)
   I_dH          = np.where(day_window, 100.0 * sun_shape, 0.0)

   T0  = np.full(n_steps, 5.0)
   dhw = np.zeros(n_steps)

   df = model.analyze_dynamic(
       simulation_period_sec = n_steps * dt_s,
       dt_s                  = dt_s,
       T_tank_w_init_C       = 50.0,
       dhw_usage_schedule    = dhw,
       T0_schedule           = T0,
       I_DN_schedule         = I_DN,
       I_dH_schedule         = I_dH,
   )

To quantify the contribution, run the same schedules through a base
``AirSourceHeatPumpBoiler`` and difference the daily compressor
energy:

.. code-block:: python

   from tmhp import AirSourceHeatPumpBoiler

   base = AirSourceHeatPumpBoiler(ref="R32").analyze_dynamic(
       simulation_period_sec = n_steps * dt_s,
       dt_s                  = dt_s,
       T_tank_w_init_C       = 50.0,
       dhw_usage_schedule    = dhw,
       T0_schedule           = T0,
   )

   def daily_kwh(s, dt_s=dt_s):
       return float(s.sum()) * dt_s / 3.6e6

   saving = daily_kwh(base["E_cmp [W]"]) - daily_kwh(df["E_cmp [W]"])
   print(f"STC preheat saving: {saving:.2f} kWh/day")

.. autoclass:: tmhp.ASHPB_STC_preheat
    :no-index:
    :members:
    :show-inheritance:

STC with stratified tank
------------------------

.. autoclass:: tmhp.ASHPB_STC_tank
    :members:
    :show-inheritance:
    :no-index:

PV + ESS
--------

:class:`~tmhp.subsystems.PhotovoltaicSystem` generation feeds the
compressor and auxiliaries; an :class:`~tmhp.subsystems.EnergyStorageSystem`
buffers midday surplus for evening load; grid import covers
whatever the two cannot supply.

.. code-block:: python

   from tmhp import ASHPB_PV_ESS
   from tmhp.subsystems import EnergyStorageSystem, PhotovoltaicSystem

   model = ASHPB_PV_ESS(
       pv  = PhotovoltaicSystem(),
       ess = EnergyStorageSystem(),
       ref = "R32",
   )

   # Pass I_DN_schedule + I_dH_schedule to analyze_dynamic exactly
   # as you would for ASHPB_STC_preheat.

.. figure:: ../_static/pv_ess_energy_balance.svg
    :alt: Two-panel daily energy balance for the PV + ESS scenario.
        Panel (a) is the timeseries of PV generation, HP load, and
        grid import. Panel (b) is the stacked-bar daily ledger of
        where PV ended up and where HP load came from.
    :align: center
    :width: 100%

    24-hour ``ASHPB_PV_ESS`` run with a clear-sky irradiance profile
    and default ``PhotovoltaicSystem`` / ``EnergyStorageSystem`` sizes.
    Panel (b) makes the sizing tradeoff readable: shrinking the ESS
    column on the left would push more of the right-hand bar from
    "PV" to "Grid". Generated by
    ``scripts/visualization/pv_ess_energy_balance.py``.

.. autoclass:: tmhp.ASHPB_PV_ESS
    :no-index:
    :members:
    :show-inheritance:

API reference
=============

.. automodule:: tmhp.air_source_heat_pump_boiler
    :members:
    :undoc-members:
    :show-inheritance:

.. automodule:: tmhp.ashpb_stc_preheat
    :members:
    :undoc-members:
    :show-inheritance:

.. automodule:: tmhp.ashpb_stc_tank
    :members:
    :undoc-members:
    :show-inheritance:

.. automodule:: tmhp.ashpb_pv_ess
    :members:
    :undoc-members:
    :show-inheritance:

Validation
==========

ASHPB runs in the shared catalogue parity harness together with the
air-to-air ASHP: 17 manufacturer units are registered and 15 are adopted
into the headline figures (the Samsung EHS Mono HT Quiet R32 14 kW unit is
one of them). The earlier 15-point Samsung comparison used a dedicated
parameter set and is superseded. See :doc:`../validation/index` for the
parity plot, per-unit results and the held catalogues.
