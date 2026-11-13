===========
Result keys
===========

``analyze_steady`` returns a flat ``dict`` and ``analyze_dynamic`` a
``pandas.DataFrame`` whose columns are the same keys, one row per time step.
This page lists every key the five released families return with their
default options: what it means, its unit and sign, and which family carries
it. The model pages explain the physics behind a group of keys; this page is
the index.

How to read a result
====================

- **The unit is in the key.** ``E_cmp [W]`` is in watts, ``T_tank_w [°C]`` in
  degrees Celsius, ``dV_ou_a [m3/s]`` in cubic metres per second. A key
  without a bracket is a flag (``bool``), a name (``str``) or a plain ratio.
- **Everything is a rate.** Heat and power are in watts, never joules. In a
  dynamic row they are means over the step, so energy is the column times
  ``dt_s``.
- **Location, not role.** Heat duties are named after the heat exchanger they
  cross (``Q_ref_iu``, ``Q_ref_ou``, ``Q_ref_tank``, ``Q_ref_ground``) and are
  never negative; ``mode`` says which of them is the condenser.
- **The request is signed, the result is not.** ``Q_r_iu < 0`` asks for
  heating and ``Q_r_iu > 0`` for cooling; ``Q_request [W]`` repeats it as
  given, ``Q_ref_iu [W]`` is the magnitude delivered.
- **Off is 0, undefined is NaN.** An off or failed row has ``hp_is_on ==
  False``, zero heat and power, and NaN for what does not exist without a
  running cycle (COP, refrigerant states, efficiencies).
  ``failure_reason`` separates "not asked to run" (``"none"``) from "could not
  run" (:doc:`failure-reason-semantics`).
- **A row can be a time average.** Below the minimum compressor speed a
  steady result is the mean of on/off operation, and a boiler's dynamic step
  is the mean over a step in which the thermostat may have switched the
  compressor. ``runtime_fraction [-]`` is below 1 on such rows: rates are
  means, temperatures, pressures and speed are those of the running machine.

In the *Where* column a family name alone means the key is in both
``analyze_steady`` and ``analyze_dynamic``; ``(s)`` marks steady only, ``(d)``
dynamic only; ``all`` is all five families in both. The families are ASHP
(:class:`~tmhp.AirSourceHeatPump`), GSHP
(:class:`~tmhp.GroundSourceHeatPump`), ASHPB
(:class:`~tmhp.AirSourceHeatPumpBoiler`), GSHPB
(:class:`~tmhp.GroundSourceHeatPumpBoiler`) and WSHPB
(:class:`~tmhp.WaterSourceHeatPumpBoiler`).

.. code-block:: python

   from tmhp import AirSourceHeatPump

   row = AirSourceHeatPump().analyze_steady(Q_r_iu=-9_000, T0=-10.0, T_a_room=20.0)

   row["failure_reason"]      # "none": the unit runs
   row["capacity_clamped"]    # "max": it delivers less than the 9 kW asked for
   row["capacity_binding"]    # "compressor": the speed ceiling stops it
   row["Q_ref_iu [W]"]        # heat delivered, about 4.0 kW
   row["Q_unmet [W]"]         # the rest of the request, about 5.0 kW

Status and flags
================

Keys without a unit bracket are flags or names. Read these first: they say whether the numbers of the row
are an operating point, a reduced one, a time average, or placeholders.

.. list-table::
    :header-rows: 1
    :widths: 30 24 46

    * - Key
      - Where
      - Meaning
    * - ``hp_is_on``
      - all
      - ``True`` when the compressor runs in this result (for a dynamic step: during any part of it).
    * - ``mode``
      - all
      - ``"heating"``, ``"cooling"`` or ``"off"``. The boilers only heat.
    * - ``converged``
      - all
      - The operating point closed: heat exchangers and compressor speed. ``False`` on a failed request, together with ``failure_reason``.
    * - ``converged_rps``
      - all
      - The compressor-speed solve met the duty inside ``rps_min`` … ``rps_max``.
    * - ``failure_reason``
      - all
      - ``"none"`` or why a requested point has no operating point; the row then holds off-state placeholders. Values: :doc:`failure-reason-semantics`.
    * - ``capacity_clamped``
      - ASHP, GSHP, ASHPB (s), GSHPB (s), WSHPB (s)
      - ``None`` when the request was met; ``"min"`` when it was below the minimum-speed duty (the row is then the on/off mean, see *Request, capacity and part load*); ``"max"`` when the unit delivers less than asked.
    * - ``capacity_binding``
      - ASHP, GSHP, ASHPB (s), GSHPB (s), WSHPB (s)
      - On a ``"max"`` row, the limit the next larger duty runs into: ``"compressor"`` (``rps_max``), ``"discharge_temperature"``, ``"condensing_temperature"``, ``"pressure_ratio"``, ``"compressor_efficiency"``, ``"cycle"`` (ASHP, GSHP: a coil at the edge of the approach search), ``"outdoor_coil"`` (ASHPB), ``"source_heat_exchanger"`` (GSHPB, WSHPB). ``None`` otherwise.
    * - ``efficiency_extrapolated``
      - ASHP, GSHP (s), ASHPB (s), GSHPB (s), WSHPB (s)
      - A default compressor-efficiency fit is read outside its data box (PR 1.50–7.15, 33.8–72.5 rev/s) at this point. Scalar and user efficiencies give ``False``.
    * - ``discharge_control``
      - all
      - The discharge-temperature control acted: suction superheat reduced, then wet suction, to hold ``T_dis_max``. See ``dT_ref_superheat [K]`` and ``x_ref_cmp_in [-]``.
    * - ``pr_clamped``
      - ASHPB
      - The pressure ratio was raised to the floor ``PR_cycle_min`` and the cycle solved there.
    * - ``pr_floor_active``
      - GSHP
      - Same condition as ``pr_clamped``, GSHP's name for it.
    * - ``indoor_heat_direction_valid``
      - ASHP, GSHP
      - The indoor air receives heat in heating and gives it up in cooling. ``False`` makes ``cop_sys [-]`` NaN.
    * - ``fan_flow_min_limit``
      - ASHPB
      - The outdoor fan runs at its smallest airflow (``dV_fan_a_min``).
    * - ``fan_flow_max_limit``
      - ASHPB
      - The outdoor fan runs at its largest airflow (``dV_fan_a_max``).
    * - ``ou_fan_flow_min_limit``
      - ASHP
      - The outdoor fan runs at its smallest airflow.
    * - ``ou_fan_flow_max_limit``
      - ASHP
      - The outdoor fan runs at its largest airflow.
    * - ``iu_fan_flow_min_limit``
      - ASHP, GSHP
      - The indoor fan runs at its smallest airflow.
    * - ``iu_fan_flow_max_limit``
      - ASHP, GSHP
      - The indoor fan runs at its largest airflow.

Request, capacity and part load
===============================

What was asked for, what was delivered, and how a request outside the modulation range was answered.
``runtime_fraction [-]`` is the one key that tells a time-averaged row from a continuous one.

.. list-table::
    :header-rows: 1
    :widths: 30 24 46

    * - Key
      - Where
      - Meaning
    * - ``Q_request [W]``
      - ASHP, GSHP, ASHPB (s)
      - The duty asked for, with the sign it was given (``Q_r_iu``: negative heating, positive cooling; boilers: positive).
    * - ``Q_unmet [W]``
      - ASHP, GSHP
      - Part of the request not delivered, ≥ 0. Sum it times the time step for a schedule's unmet energy.
    * - ``Q_max [W]``
      - ASHP, GSHP, ASHPB (s)
      - On a ``"max"`` row, the duty delivered (the ceiling). ``None`` or absent otherwise.
    * - ``Q_min [W]``
      - ASHP, GSHP, ASHPB (s), GSHPB (s), WSHPB (s)
      - On a ``"min"`` row, the duty of continuous operation at the minimum speed. NaN otherwise.
    * - ``Q_ref_tank_request [W]``
      - ASHPB
      - ASHPB: the ``Q_ref_tank`` argument. Compare with ``Q_ref_tank [W]`` on a ``"max"`` row.
    * - ``Q_tank_load [W]``
      - GSHPB, WSHPB
      - GSHPB, WSHPB: the tank heat the cycle was asked for (the ``Q_ref_tank`` argument).
    * - ``PLR_request [-]``
      - ASHPB (s)
      - Requested duty over ``hp_capacity``.
    * - ``PLR_delivered [-]``
      - ASHPB (s)
      - Delivered duty over ``hp_capacity``.
    * - ``capacity_ratio [-]``
      - ASHP, GSHP, ASHPB (s), GSHPB (s), WSHPB (s)
      - Request over the minimum-speed duty (the *CR* of EN 14825); 1 on a row that does not cycle, 0 when off.
    * - ``part_load_factor [-]``
      - ASHP, GSHP, ASHPB (s), GSHPB (s), WSHPB (s)
      - Mean COP over the continuous minimum-speed COP on a cycling row (EN 14825 degradation); 1 otherwise.
    * - ``runtime_fraction [-]``
      - all
      - Share of the time the compressor runs. Steady: 1, or below 1 on a ``"min"`` row. Boiler dynamic steps: the part of the step the thermostat kept the compressor on. Heat, flow and power of such a row are means over the whole period; temperatures, pressures and speed are those of the running machine.
    * - ``hp_starts [-]``
      - ASHPB (d), GSHPB (d), WSHPB (d)
      - Number of compressor starts inside the step (boiler dynamic steps).
    * - ``E_cycling_loss [W]``
      - ASHP, GSHP, ASHPB (s), GSHPB (s), WSHPB (s)
      - Mean electrical power drawn beyond ``capacity_ratio`` times the continuous power: the cost of on/off operation. 0 on a row that does not cycle.

Heat rates
==========

``Q_ref_*`` are refrigerant-side duties named by *where* the heat exchanger is, always ≥ 0: in heating the
indoor unit or tank is the condenser, in cooling the indoor unit is the evaporator. Stream keys
(``Q_a_*``, ``Q_tank_w_*``, ``Q_mix_*``) are enthalpy flows ``ṁ c (T − T0)`` counted from the outdoor
temperature of the row and can be negative; only their differences are heat rates.

.. list-table::
    :header-rows: 1
    :widths: 30 24 46

    * - Key
      - Where
      - Meaning
    * - ``Q_ref_iu [W]``
      - ASHP, GSHP
      - Indoor-coil duty: heat delivered in heating, heat removed in cooling (sensible, before indoor-fan heat).
    * - ``Q_ref_ou [W]``
      - ASHP, ASHPB
      - Outdoor-coil duty: heat absorbed from outdoor air in heating (and by the boiler), rejected to it in cooling.
    * - ``Q_ref_tank [W]``
      - ASHPB, GSHPB, WSHPB
      - Heat the condenser delivers to the tank water.
    * - ``Q_ref_ground [W]``
      - GSHP, GSHPB
      - Duty of the heat exchanger between refrigerant and ground-loop fluid: absorbed in heating, rejected in cooling.
    * - ``Q_ref_water [W]``
      - WSHPB
      - Duty of the evaporator on the source-water loop.
    * - ``Q_bhe [W]``
      - GSHP, GSHPB, WSHPB
      - Heat taken from the ground (or source water) by the loop: positive when extracted, negative when rejected in cooling. Differs from ``Q_ref_ground``/``Q_ref_water`` by the pump heat ``E_pmp``.
    * - ``Q_ou_a [W]``
      - ASHPB
      - Heat the outdoor air gives up across the coil (air-side counterpart of ``Q_ref_ou``).
    * - ``Q_a_iu_in [W]``
      - ASHP, GSHP
      - Enthalpy flow of the air entering the indoor unit, from ``T0``.
    * - ``Q_a_iu_out [W]``
      - ASHP, GSHP
      - Enthalpy flow of the air leaving the indoor unit (after the fan), from ``T0``. ``Q_a_iu_out − Q_a_iu_in`` is the heat the room receives (negative in cooling).
    * - ``Q_tank_loss [W]``
      - ASHPB, GSHPB, WSHPB
      - Heat lost through the tank wall to its surroundings (``T_sur``); 0 in ``analyze_steady``.
    * - ``Q_tank_w_in [W]``
      - ASHPB
      - Enthalpy flow of the make-up water entering the tank, from ``T0``.
    * - ``Q_tank_w_out [W]``
      - ASHPB, GSHPB (d), WSHPB (d)
      - Enthalpy flow of the hot water leaving the tank, from ``T0``.
    * - ``Q_mix_sup_w_in [W]``
      - ASHPB
      - Enthalpy flow of the mains water entering the mixing valve, from ``T0``.
    * - ``Q_mix_w_out [W]``
      - ASHPB
      - Enthalpy flow of the mixed water delivered to the tap, from ``T0``.
    * - ``Q_dhw_request [W]``
      - ASHPB (d), GSHPB (d), WSHPB (d)
      - Hot-water heat the step was asked for: scheduled draw at the set point ``T_mix_w_out``, counted from the mains temperature.
    * - ``Q_dhw_unmet [W]``
      - ASHPB (d), GSHPB (d), WSHPB (d)
      - Part of ``Q_dhw_request`` not delivered, ≥ 0 (water left below the set point, or a level-varying tank cut the draw).

Electrical power and COP
========================

All ``E_*`` are electrical input, ≥ 0, and 0 on an off row. COPs are NaN on an off or failed row, never 0.

.. list-table::
    :header-rows: 1
    :widths: 30 24 46

    * - Key
      - Where
      - Meaning
    * - ``E_cmp [W]``
      - all
      - Compressor electrical input (refrigerant work over the electro-mechanical efficiency; on a cycling or frosting row, the mean including that loss).
    * - ``E_cmp_ref [W]``
      - GSHP
      - Work the compressor does on the refrigerant, ``ṁ (h_cmp_out − h_cmp_in)``.
    * - ``E_cmp_loss [W]``
      - GSHP
      - ``E_cmp − E_cmp_ref``: electro-mechanical loss, plus the on/off loss on a cycling row.
    * - ``E_ou_fan [W]``
      - ASHP, ASHPB
      - Outdoor-fan electrical input.
    * - ``E_iu_fan [W]``
      - ASHP, GSHP
      - Indoor-fan electrical input. Its heat is in the air leaving the indoor unit.
    * - ``E_pmp [W]``
      - GSHP, GSHPB, WSHPB
      - Source-loop pump electrical input. It warms the loop fluid.
    * - ``E_cmp_plus_pmp [W]``
      - GSHP, GSHPB
      - ``E_cmp + E_pmp``.
    * - ``E_tot [W]``
      - all
      - Whole electrical input of the row: compressor plus the family's fans or pump (and subsystem loads in composed variants).
    * - ``cop_ref [-]``
      - all
      - Refrigerant-side COP: the delivered duty (``Q_ref_iu`` or ``Q_ref_tank``) over ``E_cmp``.
    * - ``cop_sys [-]``
      - all
      - System COP. Boilers: ``Q_ref_tank / E_tot``. ASHP, GSHP: heat the room air receives or gives up, ``|Q_a_iu_out − Q_a_iu_in|``, over ``E_tot`` (indoor-fan heat counted once, on the air).

Frost and defrost (ASHP)
========================

Present on every ASHP row; neutral (0 or 1) in cooling and above the frosting limit. See :doc:`/models/ashp`.

.. list-table::
    :header-rows: 1
    :widths: 30 24 46

    * - Key
      - Where
      - Meaning
    * - ``defrost_fraction [-]``
      - ASHP
      - Fraction of the compressor's runtime spent defrosting.
    * - ``frost_capacity_factor [-]``
      - ASHP
      - Heating-capacity multiplier of the frosted coil, defrost time included; 1 without frost.
    * - ``frost_power_factor [-]``
      - ASHP
      - Input-power multiplier of the frosted coil; 1 without frost.
    * - ``E_defrost [W]``
      - ASHP
      - Mean compressor power spent defrosting (part of ``E_cmp``).
    * - ``Q_defrost [W]``
      - ASHP
      - Mean heat a reverse-cycle defrost takes from the room.
    * - ``Q_ref_iu_frost_free [W]``
      - ASHP
      - Indoor-coil duty of the frost-free cycle the row was solved at.
    * - ``E_tot_frost_free [W]``
      - ASHP
      - Electrical input of that frost-free cycle.
    * - ``RH_out [-]``
      - ASHP
      - Outdoor relative humidity the frost correction used (fraction); NaN where no correction applies.

Compressor and cycle
====================

Values of the running machine. On an off row speeds and flows are 0 and efficiencies NaN.

.. list-table::
    :header-rows: 1
    :widths: 30 24 46

    * - Key
      - Where
      - Meaning
    * - ``cmp_rpm [rpm]``
      - all
      - Compressor speed.
    * - ``cmp_rps [rev/s]``
      - GSHP, GSHPB
      - Compressor speed in revolutions per second.
    * - ``n_star [-]``
      - all
      - Compressor speed over the rated speed ``rps_rated``.
    * - ``pr_cmp [-]``
      - ASHP, ASHPB, GSHPB, WSHPB
      - Compressor pressure ratio, discharge over suction pressure.
    * - ``pressure_ratio``
      - GSHP
      - The same ratio under GSHP's name (dimensionless, no bracket).
    * - ``eta_cmp_isen [-]``
      - ASHP, ASHPB, GSHPB, WSHPB
      - Isentropic efficiency at this point.
    * - ``eta_cmp_vol [-]``
      - ASHP, ASHPB, GSHPB, WSHPB
      - Volumetric efficiency at this point.
    * - ``eta_cmp [-]``
      - ASHP, ASHPB, GSHPB, WSHPB
      - Electro-mechanical efficiency at this point.
    * - ``eta_is [-]``
      - GSHP, GSHPB
      - Isentropic efficiency, ground-source name (same value as ``eta_cmp_isen`` where both exist).
    * - ``eta_v [-]``
      - GSHP, GSHPB
      - Volumetric efficiency, ground-source name.
    * - ``eta_em [-]``
      - GSHP, GSHPB
      - Electro-mechanical efficiency, ground-source name.
    * - ``m_dot_ref [kg/s]``
      - all
      - Refrigerant mass flow (a mean on a cycling row).
    * - ``rho_ref_cmp_in [kg/m3]``
      - all
      - Refrigerant density at the compressor suction.
    * - ``dT_ref_superheat [K]``
      - all
      - Suction superheat the cycle ran at: the set value, capped by the evaporator approach, or reduced by the discharge control. 0 with wet suction.
    * - ``x_ref_cmp_in [-]``
      - all
      - Suction vapour quality: 1 for dry suction, below 1 (down to ``x_suction_min``) when the discharge control uses wet suction.
    * - ``T_cond [°C]``
      - GSHPB, WSHPB
      - Condensing temperature (bubble point); on an off row the tank temperature.

Air side
========

``T0`` is the outdoor (dead-state) temperature of the row.

.. list-table::
    :header-rows: 1
    :widths: 30 24 46

    * - Key
      - Where
      - Meaning
    * - ``T0 [°C]``
      - all
      - Outdoor air temperature given for the row; also the reference temperature of enthalpy and exergy flows.
    * - ``T_a_room [°C]``
      - ASHP, GSHP
      - Room air temperature given for the row.
    * - ``T_ou_a_in [°C]``
      - ASHP, ASHPB
      - Air entering the outdoor coil (equals ``T0``).
    * - ``T_ou_a_mid [°C]``
      - ASHP, ASHPB
      - Air leaving the outdoor coil, before the fan.
    * - ``T_ou_a_out [°C]``
      - ASHP, ASHPB
      - Air leaving the outdoor unit, fan heat included.
    * - ``T_iu_a_in [°C]``
      - ASHP, GSHP
      - Air entering the indoor coil (equals ``T_a_room``).
    * - ``T_iu_a_mid [°C]``
      - ASHP, GSHP
      - Air leaving the indoor coil, before the fan.
    * - ``T_iu_a_out [°C]``
      - ASHP, GSHP
      - Supply air, fan heat included.
    * - ``dV_ou_a [m3/s]``
      - ASHP, ASHPB
      - Outdoor airflow (a mean on a cycling row).
    * - ``dV_iu_a [m3/s]``
      - ASHP, GSHP
      - Indoor airflow (a mean on a cycling row).
    * - ``v_ou_a [m/s]``
      - ASHP, ASHPB
      - Face velocity at the outdoor coil.
    * - ``v_iu_a [m/s]``
      - ASHP, GSHP
      - Face velocity at the indoor coil.
    * - ``UA_ou [W/K]``
      - ASHPB (s)
      - Outdoor-coil conductance at this airflow.
    * - ``NTU_ou [-]``
      - ASHPB (s)
      - Outdoor-coil number of transfer units at this airflow.
    * - ``epsilon_ou [-]``
      - ASHPB (s)
      - Outdoor-coil effectiveness.
    * - ``UA_iu_rated [W/K]``
      - GSHP
      - Rated indoor-coil conductance of the machine (echo of the model value).
    * - ``indoor_approach [K]``
      - GSHP
      - Temperature difference between room air and refrigerant saturation the indoor coil ran at.
    * - ``Q_iu_HX_available [W]``
      - GSHP
      - Heat the indoor coil transfers at the solved approach (running machine).
    * - ``Q_iu_ref_required [W]``
      - GSHP
      - Refrigerant duty the indoor coil has to carry (running machine).
    * - ``iu_hx_capacity_margin [W]``
      - GSHP
      - Available minus required indoor-coil heat at the solved airflow; 0 when balanced.
    * - ``iu_hx_min_flow_margin [W]``
      - GSHP
      - The same margin at the smallest indoor airflow.
    * - ``iu_hx_max_flow_margin [W]``
      - GSHP
      - The same margin at the largest indoor airflow.

Ground and source-water loop
============================

The loop fluid is named from the borehole's side: ``T_bhe_f_in`` *enters the borehole*, i.e. leaves the heat
pump (the coldest fluid in heating); ``T_bhe_f_out`` leaves the borehole and enters the heat pump. WSHPB
reuses these names for its source-water coil.

.. list-table::
    :header-rows: 1
    :widths: 30 24 46

    * - Key
      - Where
      - Meaning
    * - ``Ts [°C]``
      - GSHP, GSHPB, WSHPB
      - Undisturbed ground temperature (WSHPB: source-water temperature) of the model or step.
    * - ``T_bhe [°C]``
      - GSHP, GSHPB, WSHPB
      - Borehole-wall temperature. Dynamic: the ground response to the load history. ``analyze_steady`` without ``T_bhe_wall``: the wall temperature that returns fluid at the given source temperature, back-calculated from the load, not a ground response. WSHPB: the source-water temperature.
    * - ``T_bhe_f [°C]``
      - GSHP, GSHPB, WSHPB
      - Mean loop-fluid temperature, ``(T_bhe_f_in + T_bhe_f_out) / 2``.
    * - ``T_bhe_f_in [°C]``
      - GSHP, GSHPB, WSHPB
      - Fluid entering the borehole = leaving the heat pump. Step mean in a dynamic boiler row.
    * - ``T_bhe_f_out [°C]``
      - GSHP, GSHPB, WSHPB
      - Fluid leaving the borehole = entering the heat pump.
    * - ``T_bhe_f_running [°C]``
      - GSHPB (d), WSHPB (d)
      - Mean loop-fluid temperature while the compressor runs (NaN on a step without a run).
    * - ``T_bhe_f_in_running [°C]``
      - GSHPB (d), WSHPB (d)
      - Fluid leaving the heat pump while the compressor runs: the coldest loop temperature of the step.
    * - ``T_bhe_f_out_running [°C]``
      - GSHPB (d), WSHPB (d)
      - Fluid entering the heat pump while the compressor runs: what the step's cycle was solved against.
    * - ``T_bhe_wall [°C]``
      - GSHP (s)
      - Echo of the ``T_bhe_wall`` argument; present only when it was given.
    * - ``dV_bhe_f [m3/s]``
      - GSHP, GSHPB, WSHPB
      - Loop volume flow (total, all boreholes).
    * - ``ground_flow [m3/s]``
      - GSHP, GSHPB
      - Loop volume flow as the ground-flow controller set it.
    * - ``ground_flow_ratio``
      - GSHP, GSHPB
      - Loop flow over the reference flow ``ground_flow_ref_lpm`` (0 when off).
    * - ``ground_flow_ref_ratio``
      - GSHP, GSHPB
      - The same ratio (kept under both names).
    * - ``ground_flow_control``
      - GSHP, GSHPB
      - ``"constant"`` or ``"optimal_power"``: how the loop flow was chosen.
    * - ``ground_flow_at_min``
      - GSHP, GSHPB
      - The loop flow sits on its lower limit.
    * - ``ground_flow_at_max``
      - GSHP, GSHPB
      - The loop flow sits on its upper limit.
    * - ``m_dot_borehole [kg/s]``
      - GSHP, GSHPB
      - Mass flow through one borehole (a mean on a cycling row).
    * - ``R_b_eff [mK/W]``
      - GSHP, GSHPB
      - Borehole thermal resistance used at this flow.
    * - ``UA_ground [W/K]``
      - GSHP, GSHPB
      - Conductance of the fluid-to-refrigerant heat exchanger at this flow.
    * - ``UA_ground_rated [W/K]``
      - GSHP
      - Its rated value (echo of the model value).
    * - ``ground_approach [K]``
      - GSHP
      - Temperature difference between the loop fluid and the refrigerant saturation the ground-side heat exchanger ran at.
    * - ``ground_balance``
      - GSHP
      - ``"equality"`` when the ground-side heat exchanger carries exactly the duty; ``"slack_floor"`` when the pressure-ratio floor leaves it oversized.
    * - ``Q_HX_available [W]``
      - GSHP, GSHPB
      - Heat the ground-side heat exchanger transfers at the solved approach (running machine).
    * - ``Q_ref_required [W]``
      - GSHP, GSHPB
      - Refrigerant duty it has to carry (running machine).
    * - ``hx_capacity_margin [W]``
      - GSHP, GSHPB
      - ``Q_HX_available − Q_ref_required``; 0 when balanced.
    * - ``hx_capacity_ratio``
      - GSHP, GSHPB
      - ``Q_HX_available / Q_ref_required``; NaN when off.
    * - ``hx_feasible``
      - GSHP, GSHPB
      - The ground-side heat exchanger balance closed (margin within tolerance).
    * - ``err_Q_evap [W]``
      - GSHP
      - Residual of the evaporator balance (GSHP).
    * - ``err_Q_ground [W]``
      - GSHPB
      - Residual of the ground heat-exchanger balance (GSHPB).
    * - ``err_Q_water [W]``
      - WSHPB
      - Residual of the source-water heat-exchanger balance (WSHPB).
    * - ``pump_speed_ratio``
      - GSHP, GSHPB
      - Pump speed over its reference speed; NaN unless the hydraulic pump model is on.
    * - ``pump_efficiency``
      - GSHP, GSHPB
      - Pump efficiency used; NaN when off.
    * - ``ground_pressure_drop [Pa]``
      - GSHP, GSHPB
      - Loop pressure drop; NaN unless the hydraulic pump model is on.
    * - ``ground_pressure_drop_bhe [Pa]``
      - GSHP, GSHPB
      - Its borehole part.
    * - ``ground_pressure_drop_aux [Pa]``
      - GSHP, GSHPB
      - Its part outside the boreholes (``dp_aux_ref``).
    * - ``ground_pressure_drop_total [Pa]``
      - GSHP, GSHPB
      - Same value as ``ground_pressure_drop [Pa]``.
    * - ``approach_solver_success``
      - GSHP, GSHPB
      - The coupled ground-flow solve found the ground approach (``False`` when that solver is not used).
    * - ``approach_at_bound``
      - GSHP, GSHPB
      - The solved ground approach sits on a bound of its search band.
    * - ``flow_optimizer_success``
      - GSHP, GSHPB
      - The flow optimiser of ``ground_flow_control="optimal_power"`` succeeded (``False`` otherwise).
    * - ``flow_optimizer_nfev``
      - GSHP, GSHPB
      - Number of flow candidates it evaluated.
    * - ``flow_bound_active``
      - GSHP, GSHPB
      - The optimal flow sits on a flow limit.

Tank and hot water
==================

Boiler families. Flows are step means in a dynamic row; a flow that is zero is reported as NaN by ASHPB and
as 0 by GSHPB and WSHPB.

.. list-table::
    :header-rows: 1
    :widths: 30 24 46

    * - Key
      - Where
      - Meaning
    * - ``T_tank_w [°C]``
      - ASHPB, GSHPB, WSHPB
      - Tank water temperature: the argument in ``analyze_steady``, the end-of-step temperature in a dynamic row.
    * - ``T_tank_w_in [°C]``
      - ASHPB, GSHPB, WSHPB
      - Temperature of the make-up water entering the tank.
    * - ``T_sup_w [°C]``
      - ASHPB
      - Mains water temperature.
    * - ``T_mix_w_out [°C]``
      - ASHPB, GSHPB, WSHPB
      - Temperature of the water delivered by the mixing valve; NaN when nothing is drawn (ASHPB).
    * - ``dV_mix_w_out [m3/s]``
      - ASHPB, GSHPB, WSHPB
      - Mixed hot water delivered to the tap.
    * - ``dV_mix_sup_w_in [m3/s]``
      - ASHPB, GSHPB, WSHPB
      - Mains water entering the mixing valve.
    * - ``dV_tank_w_out [m3/s]``
      - ASHPB, GSHPB, WSHPB
      - Hot water leaving the tank.
    * - ``dV_tank_w_in [m3/s]``
      - ASHPB, GSHPB, WSHPB
      - Make-up water entering the tank.
    * - ``tank_level [-]``
      - ASHPB (s), GSHPB, WSHPB
      - Tank fill level, 1 = full.
    * - ``dT_ref_tank [K]``
      - ASHPB (s), GSHPB, WSHPB
      - Condensing approach the cycle ran at: condensing temperature minus tank temperature.
    * - ``tank_hx_residual [W]``
      - ASHPB (s), GSHPB, WSHPB
      - Imbalance left in the tank heat-exchanger closure; 0 unless it could not be bracketed.
    * - ``UA_tank_hx [W/K]``
      - ASHPB (s)
      - Tank heat-exchanger conductance (echo of the model value).
    * - ``C_w_tank [W/K]``
      - ASHPB (s)
      - Heat-capacity flow of the water pumped through the condenser; NaN for a coil immersed in the tank (no ``m_dot_w``).
    * - ``epsilon_tank_hx [-]``
      - ASHPB (s)
      - Effectiveness of the condenser against that water flow; NaN for an immersed coil.

Time
====

Only the space-conditioning frames carry a time column. A boiler frame has one row per step in order:
row ``i`` covers ``i·dt_s`` to ``(i+1)·dt_s``.

.. list-table::
    :header-rows: 1
    :widths: 30 24 46

    * - Key
      - Where
      - Meaning
    * - ``time [s]``
      - ASHP (d), GSHP (d)
      - Start of the step, seconds from the start of the run.
    * - ``time [h]``
      - ASHP (d), GSHP (d)
      - The same in hours.

Refrigerant state points
========================

Every family reports the same seven points of the cycle, in ``analyze_steady`` and in every dynamic row.
An off or failed row carries NaN. For a zeotropic blend the condenser's dew and bubble points differ by
the glide; GSHPB and WSHPB report the bubble-point temperature under both ``T_ref_cond_sat_v`` and
``T_ref_cond_sat_l``.

.. list-table::
    :header-rows: 1
    :widths: 24 19 19 19 19

    * - Point
      - Temperature
      - Pressure
      - Specific enthalpy
      - Specific entropy
    * - Compressor suction
      - ``T_ref_cmp_in [°C]``
      - ``P_ref_cmp_in [Pa]``
      - ``h_ref_cmp_in [J/kg]``
      - ``s_ref_cmp_in [J/(kg·K)]``
    * - Compressor discharge
      - ``T_ref_cmp_out [°C]``
      - ``P_ref_cmp_out [Pa]``
      - ``h_ref_cmp_out [J/kg]``
      - ``s_ref_cmp_out [J/(kg·K)]``
    * - Expansion-valve inlet (subcooled liquid)
      - ``T_ref_exp_in [°C]``
      - ``P_ref_exp_in [Pa]``
      - ``h_ref_exp_in [J/kg]``
      - ``s_ref_exp_in [J/(kg·K)]``
    * - Expansion-valve outlet (evaporator inlet)
      - ``T_ref_exp_out [°C]``
      - ``P_ref_exp_out [Pa]``
      - ``h_ref_exp_out [J/kg]``
      - ``s_ref_exp_out [J/(kg·K)]``
    * - Saturated vapour at the evaporating pressure
      - ``T_ref_evap_sat [°C]``
      - ``P_ref_evap_sat [Pa]``
      - ``h_ref_evap_sat [J/kg]``
      - ``s_ref_evap_sat [J/(kg·K)]``
    * - Saturated vapour at the condensing pressure (dew point)
      - ``T_ref_cond_sat_v [°C]``
      - ``P_ref_cond_sat_v [Pa]``
      - ``h_ref_cond_sat_v [J/kg]``
      - ``s_ref_cond_sat_v [J/(kg·K)]``
    * - Saturated liquid at the condensing pressure (bubble point)
      - ``T_ref_cond_sat_l [°C]``
      - ``P_ref_cond_sat_l [Pa]``
      - ``h_ref_cond_sat_l [J/kg]``
      - ``s_ref_cond_sat_l [J/(kg·K)]``

Exergy
======

Exergy columns are added by post-processing, with the row's ``T0 [°C]`` as the dead state.
``analyze_dynamic`` adds them in every family; ``analyze_steady`` adds them for ASHP only
(``postprocess=True``, the default). ``X_*`` is an exergy flow or the exergy of a heat or power,
``Xc_*`` the exergy consumed in a component, ``X_in_*``/``X_out_*`` what enters and leaves it, and
``X_eff_*`` its exergy efficiency ``1 − Xc / X_in`` (system and cycle: product over input). Rows
without a running cycle carry NaN or 0.

.. list-table::
    :header-rows: 1
    :widths: 30 24 46

    * - Key
      - Where
      - Meaning
    * - ``X_a_iu_in [W]``
      - ASHP, GSHP (d)
      - Flow exergy of the air entering the indoor coil.
    * - ``X_a_iu_mid [W]``
      - ASHP, GSHP (d)
      - Flow exergy of the air leaving the indoor coil, before the fan.
    * - ``X_a_iu_out [W]``
      - ASHP, GSHP (d)
      - Flow exergy of the supply air.
    * - ``X_a_ou_in [W]``
      - ASHP, ASHPB (d)
      - Flow exergy of the air entering the outdoor coil.
    * - ``X_a_ou_mid [W]``
      - ASHP, ASHPB (d)
      - Flow exergy of the air leaving the outdoor coil, before the fan.
    * - ``X_a_ou_out [W]``
      - ASHP, ASHPB (d)
      - Flow exergy of the air leaving the outdoor unit.
    * - ``X_bhe_f_in [W]``
      - GSHP (d)
      - Flow exergy of the loop fluid entering the borehole.
    * - ``X_bhe_f_out [W]``
      - GSHP (d)
      - Flow exergy of the loop fluid leaving the borehole.
    * - ``X_bhe_in [W]``
      - GSHPB (d), WSHPB (d)
      - Flow exergy of the loop fluid entering the borehole (boiler name).
    * - ``X_bhe_out [W]``
      - GSHPB (d), WSHPB (d)
      - Flow exergy of the loop fluid leaving the borehole (boiler name).
    * - ``X_cmp [W]``
      - ASHP, GSHP (d), ASHPB (d), GSHPB (d), WSHPB (d)
      - Exergy of ``E_cmp [W]`` (electricity is pure exergy: the same number).
    * - ``X_cmp_loss [W]``
      - GSHP (d)
      - Exergy of ``E_cmp_loss [W]`` (electricity is pure exergy: the same number).
    * - ``X_cmp_plus_pmp [W]``
      - GSHP (d), GSHPB (d)
      - Exergy of ``E_cmp_plus_pmp [W]`` (electricity is pure exergy: the same number).
    * - ``X_cmp_ref [W]``
      - GSHP (d)
      - Exergy of ``E_cmp_ref [W]`` (electricity is pure exergy: the same number).
    * - ``X_cycling_loss [W]``
      - ASHP, GSHP (d)
      - Exergy of ``E_cycling_loss [W]`` (electricity is pure exergy: the same number).
    * - ``X_defrost [W]``
      - ASHP
      - Exergy of ``E_defrost [W]`` (electricity is pure exergy: the same number).
    * - ``X_eff_bhe_hx [-]``
      - GSHP (d)
      - Exergy efficiency of the ground-side heat exchanger.
    * - ``X_eff_cmp [-]``
      - ASHP, GSHP (d), ASHPB (d)
      - Exergy efficiency of the compressor.
    * - ``X_eff_exp [-]``
      - ASHP, GSHP (d), ASHPB (d)
      - Exergy efficiency of the expansion valve.
    * - ``X_eff_iu_fan [-]``
      - ASHP, GSHP (d)
      - Exergy efficiency of the indoor fan.
    * - ``X_eff_iu_hx [-]``
      - ASHP, GSHP (d)
      - Exergy efficiency of the indoor coil.
    * - ``X_eff_mix [-]``
      - ASHPB (d)
      - Exergy efficiency of the mixing valve.
    * - ``X_eff_ou_fan [-]``
      - ASHP, ASHPB (d)
      - Exergy efficiency of the outdoor fan.
    * - ``X_eff_ou_hx [-]``
      - ASHP
      - Exergy efficiency of the outdoor coil.
    * - ``X_eff_pmp [-]``
      - GSHP (d)
      - Exergy efficiency of the source-loop pump.
    * - ``X_eff_ref [-]``
      - ASHPB (d), GSHPB (d), WSHPB (d)
      - Exergy efficiency of the refrigerant cycle.
    * - ``X_eff_ref_ou [-]``
      - ASHPB (d)
      - Exergy efficiency of the outdoor coil (evaporator).
    * - ``X_eff_ref_tank [-]``
      - ASHPB (d)
      - Exergy efficiency of the tank heat exchanger (condenser).
    * - ``X_eff_sys [-]``
      - ASHP, GSHP (d), ASHPB (d), GSHPB (d), WSHPB (d)
      - Exergy efficiency of the whole system.
    * - ``X_eff_tank [-]``
      - ASHPB (d)
      - Exergy efficiency of the tank.
    * - ``X_evap_in [W]``
      - GSHP (d)
      - Flow exergy of the loop fluid entering the heat pump, after the pump.
    * - ``X_in_bhe_hx [W]``
      - GSHP (d)
      - Exergy entering the ground-side heat exchanger.
    * - ``X_in_cmp [W]``
      - ASHP, GSHP (d)
      - Exergy entering the compressor.
    * - ``X_in_exp [W]``
      - ASHP, GSHP (d)
      - Exergy entering the expansion valve.
    * - ``X_in_iu_fan [W]``
      - ASHP, GSHP (d)
      - Exergy entering the indoor fan.
    * - ``X_in_iu_hx [W]``
      - ASHP, GSHP (d)
      - Exergy entering the indoor coil.
    * - ``X_in_ou_fan [W]``
      - ASHP
      - Exergy entering the outdoor fan.
    * - ``X_in_ou_hx [W]``
      - ASHP
      - Exergy entering the outdoor coil.
    * - ``X_in_pmp [W]``
      - GSHP (d)
      - Exergy entering the source-loop pump.
    * - ``X_iu_fan [W]``
      - ASHP, GSHP (d)
      - Exergy of ``E_iu_fan [W]`` (electricity is pure exergy: the same number).
    * - ``X_mix_sup_w_in [W]``
      - ASHPB (d), GSHPB (d), WSHPB (d)
      - Flow exergy of the mains water entering the mixing valve.
    * - ``X_mix_w_out [W]``
      - ASHPB (d), GSHPB (d), WSHPB (d)
      - Flow exergy of the mixed water delivered.
    * - ``X_ou_fan [W]``
      - ASHP, ASHPB (d)
      - Exergy of ``E_ou_fan [W]`` (electricity is pure exergy: the same number).
    * - ``X_out_bhe_hx [W]``
      - GSHP (d)
      - Exergy leaving the ground-side heat exchanger.
    * - ``X_out_cmp [W]``
      - ASHP, GSHP (d)
      - Exergy leaving the compressor.
    * - ``X_out_exp [W]``
      - ASHP, GSHP (d)
      - Exergy leaving the expansion valve.
    * - ``X_out_iu_fan [W]``
      - ASHP, GSHP (d)
      - Exergy leaving the indoor fan.
    * - ``X_out_iu_hx [W]``
      - ASHP, GSHP (d)
      - Exergy leaving the indoor coil.
    * - ``X_out_ou_fan [W]``
      - ASHP
      - Exergy leaving the outdoor fan.
    * - ``X_out_ou_hx [W]``
      - ASHP
      - Exergy leaving the outdoor coil.
    * - ``X_out_pmp [W]``
      - GSHP (d)
      - Exergy leaving the source-loop pump.
    * - ``X_pmp [W]``
      - GSHP (d), GSHPB (d), WSHPB (d)
      - Exergy of ``E_pmp [W]`` (electricity is pure exergy: the same number).
    * - ``X_ref_cmp_in [W]``
      - ASHP, GSHP (d), ASHPB (d), GSHPB (d), WSHPB (d)
      - Flow exergy of the refrigerant at the compressor suction.
    * - ``X_ref_cmp_out [W]``
      - ASHP, GSHP (d), ASHPB (d), GSHPB (d), WSHPB (d)
      - Flow exergy of the refrigerant at the compressor discharge.
    * - ``X_ref_exp_in [W]``
      - ASHP, GSHP (d), ASHPB (d), GSHPB (d), WSHPB (d)
      - Flow exergy of the refrigerant at the expansion-valve inlet.
    * - ``X_ref_exp_out [W]``
      - ASHP, GSHP (d), ASHPB (d), GSHPB (d), WSHPB (d)
      - Flow exergy of the refrigerant at the expansion-valve outlet.
    * - ``X_ref_ground [W]``
      - GSHP (d), GSHPB (d)
      - Exergy of the heat ``Q_ref_ground [W]`` at the refrigerant's saturation temperature.
    * - ``X_ref_iu [W]``
      - ASHP, GSHP (d)
      - Exergy of the heat ``Q_ref_iu [W]`` at the refrigerant's saturation temperature.
    * - ``X_ref_ou [W]``
      - ASHP, ASHPB (d)
      - Exergy of the heat ``Q_ref_ou [W]`` at the refrigerant's saturation temperature.
    * - ``X_ref_tank [W]``
      - ASHPB (d), GSHPB (d), WSHPB (d)
      - Exergy of the heat ``Q_ref_tank [W]`` at the refrigerant's saturation temperature.
    * - ``X_ref_water [W]``
      - WSHPB (d)
      - Exergy of the heat ``Q_ref_water [W]`` at the refrigerant's saturation temperature.
    * - ``X_tank_loss [W]``
      - ASHPB (d), GSHPB (d), WSHPB (d)
      - Exergy of the tank's heat loss, at the tank temperature.
    * - ``X_tank_w_in [W]``
      - ASHPB (d), GSHPB (d), WSHPB (d)
      - Flow exergy of the make-up water entering the tank.
    * - ``X_tank_w_out [W]``
      - ASHPB (d), GSHPB (d), WSHPB (d)
      - Flow exergy of the hot water leaving the tank.
    * - ``X_tot [W]``
      - ASHP, GSHP (d), ASHPB (d), GSHPB (d), WSHPB (d)
      - Exergy of ``E_tot [W]`` (electricity is pure exergy: the same number).
    * - ``X_tot_frost_free [W]``
      - ASHP
      - Exergy of ``E_tot_frost_free [W]`` (electricity is pure exergy: the same number).
    * - ``Xc_bhe_hx [W]``
      - GSHP (d)
      - Exergy consumed (destroyed) in the ground-side heat exchanger.
    * - ``Xc_cmp [W]``
      - ASHP, GSHP (d), ASHPB (d), GSHPB (d), WSHPB (d)
      - Exergy consumed (destroyed) in the compressor.
    * - ``Xc_exp [W]``
      - ASHP, GSHP (d), ASHPB (d), GSHPB (d), WSHPB (d)
      - Exergy consumed (destroyed) in the expansion valve.
    * - ``Xc_ground [W]``
      - GSHPB (d)
      - Exergy consumed (destroyed) in the ground loop.
    * - ``Xc_iu_fan [W]``
      - ASHP, GSHP (d)
      - Exergy consumed (destroyed) in the indoor fan.
    * - ``Xc_iu_hx [W]``
      - ASHP, GSHP (d)
      - Exergy consumed (destroyed) in the indoor coil.
    * - ``Xc_mix [W]``
      - ASHPB (d), GSHPB (d), WSHPB (d)
      - Exergy consumed (destroyed) in the mixing valve.
    * - ``Xc_ou_fan [W]``
      - ASHP, ASHPB (d)
      - Exergy consumed (destroyed) in the outdoor fan.
    * - ``Xc_ou_hx [W]``
      - ASHP
      - Exergy consumed (destroyed) in the outdoor coil.
    * - ``Xc_pmp [W]``
      - GSHP (d), GSHPB (d), WSHPB (d)
      - Exergy consumed (destroyed) in the source-loop pump.
    * - ``Xc_ref_ou [W]``
      - ASHPB (d)
      - Exergy consumed (destroyed) in the outdoor coil (evaporator).
    * - ``Xc_ref_tank [W]``
      - ASHPB (d), GSHPB (d), WSHPB (d)
      - Exergy consumed (destroyed) in the tank heat exchanger (condenser).
    * - ``Xc_tank [W]``
      - ASHPB (d), GSHPB (d), WSHPB (d)
      - Exergy consumed (destroyed) in the tank.
    * - ``Xc_water [W]``
      - WSHPB (d)
      - Exergy consumed (destroyed) in the source-water loop.
    * - ``Xst_tank [W]``
      - ASHPB (d), GSHPB (d), WSHPB (d)
      - Rate at which exergy is stored in the tank water.
    * - ``x_ref_cmp_in [J/kg]``
      - ASHP, GSHP (d), ASHPB (d), GSHPB (d), WSHPB (d)
      - Specific flow exergy of the refrigerant at the compressor suction.
    * - ``x_ref_cmp_out [J/kg]``
      - ASHP, GSHP (d), ASHPB (d), GSHPB (d), WSHPB (d)
      - Specific flow exergy of the refrigerant at the compressor discharge.
    * - ``x_ref_exp_in [J/kg]``
      - ASHP, GSHP (d), ASHPB (d), GSHPB (d), WSHPB (d)
      - Specific flow exergy of the refrigerant at the expansion-valve inlet.
    * - ``x_ref_exp_out [J/kg]``
      - ASHP, GSHP (d), ASHPB (d), GSHPB (d), WSHPB (d)
      - Specific flow exergy of the refrigerant at the expansion-valve outlet.

Keys that appear only with an option
====================================

These are not in a default result. Composed variants (solar thermal, PV and
storage, UV lamp) add their own columns, described on :doc:`/models/ashpb`
and :doc:`/models/gshpb`.

.. list-table::
    :header-rows: 1
    :widths: 30 24 46

    * - Key
      - Appears when
      - Meaning
    * - ``failure_detail``
      - a boiler ``analyze_steady`` fails with ``cycle_invalid``, or ASHPB
        with ``hx_not_converged``
      - Which check rejected the point; see :doc:`failure-reason-semantics`.
    * - ``dV_mix_w_out_unmet [m3/s]``
      - the tank level varies (``tank_always_full=False``), dynamic
      - Scheduled hot-water flow the tank could not supply at its lowest
        level.
    * - ``T_tank_w_top [°C]``, ``T_tank_w_bottom [°C]``, ``T_tank_w_out [°C]``
      - GSHPB with ``tank_model="stratified"`` or ``"hybrid"``, dynamic
      - Top-node, bottom-node and draw-off temperatures of the multi-node
        tank.
    * - ``ground_temperature_residual [K]``, ``candidate_ground_flow_ratio``,
        ``flow_optimizer_method``
      - GSHP, GSHPB with the hydraulic pump model or
        ``ground_flow_control="optimal_power"``
      - Diagnostics of the coupled ground-flow solve (:doc:`/models/ground-flow`).

Checking a result in code
=========================

.. code-block:: python

   ok = (df["failure_reason"] == "none") & df["converged"]
   running = df["hp_is_on"]
   reduced = df["capacity_clamped"] == "max"
   electricity_kWh = (df["E_tot [W]"] * dt_s).sum() / 3.6e6

A new key added to a model has to be added here as well:
``tests/test_docs_alignment.py`` runs each family and fails when a result
carries a key this page does not name.
