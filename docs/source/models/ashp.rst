================================================
Air-source heat pump (ASHP — space conditioning)
================================================

.. |ashp| raw:: html

   <span class="glossary" data-term="ashp">ASHP</span>

|ashp| conditions a building zone (heating + cooling) rather than
charging a DHW tank. The refrigerant cycle and outdoor-coil source
side are shared with the air-source boiler family; what differs is
the demand side — a zone energy balance instead of a tank.

Overview
========

The class is :class:`tmhp.AirSourceHeatPump`. Use it when the heat
pump's job is space conditioning rather than DHW production.

Base usage
==========

.. code-block:: python

   from tmhp import AirSourceHeatPump

   ashp = AirSourceHeatPump(ref="R32")

   # See API reference below for the full constructor and
   # analyze_steady / analyze_dynamic signatures.

Source-side mechanics
=====================

Outdoor coil with variable-speed fan and an ε-NTU air-side heat
exchanger — the shared air-source environmental-side model.

Sink-side mechanics
===================

A zone temperature / load proxy stands in for the building. The
caller supplies indoor-unit load as ``Q_r_iu``: positive values
select cooling, negative values select heating, and zero values
represent off operation. There is no tank energy balance.

Part load: cycling and unmet load
=================================

The compressor modulates between ``rps_min`` and ``rps_max``. A request outside
what the unit delivers over that range is still answered, and the row says how.

**Below the minimum capacity** the compressor cycles on and off, and the row is
the mean over the cycling period (``capacity_clamped == "min"``):

- ``Q_ref_iu [W]`` equals the request. ``Q_min [W]`` is what the unit delivers
  running continuously at its minimum speed, and ``capacity_ratio [-]`` is the
  request divided by it (the *CR* of EN 14825).
- The electrical input carries the EN 14825 degradation for a unit delivering
  to air: ``COP = COP_min · (1 − Cd · (1 − CR))`` with ``Cd = 0.25``, the value
  the standard prescribes when a unit has not been tested for it.
  ``part_load_factor [-]`` is that bracket. It applies to the refrigeration
  machine — compressor and outdoor fan — so ``cop_ref [-]`` is the continuous
  value times it, and ``cop_sys [-]`` falls slightly less (about one percent)
  because the indoor fan is not degraded.
- ``runtime_fraction [-]`` is the share of the time the compressor runs,
  ``CR / part_load_factor``. Compressor and outdoor-fan power (``E_cmp``,
  ``E_ou_fan``) are the running values times that fraction. Heat and flow
  rates (``Q_ref_iu``, ``Q_ref_ou``, ``Q_a_iu_in``, ``Q_a_iu_out``,
  ``m_dot_ref``, ``dV_iu_a``, ``dV_ou_a``) are the running values times *CR*,
  and so is the indoor fan's power (``E_iu_fan``): it runs while heat is
  delivered. The refrigerant cycle and both air streams thus stay in balance
  with the delivered heat, and the room still receives the coil duty plus the
  indoor fan's heat. Temperatures, pressures, compressor speed and
  efficiencies are those of the running machine.
- ``E_cycling_loss [W]`` is the mean electrical power the compressor and the
  outdoor fan draw beyond *CR* times their continuous power — the cost of the
  starts.

.. list-table:: EN 14825 degradation with ``Cd = 0.25``
   :header-rows: 1
   :widths: 22 24 26 28

   * - ``capacity_ratio``
     - ``part_load_factor``
     - ``runtime_fraction``
     - Cycling loss / continuous power
   * - 0.1
     - 0.775
     - 0.129
     - 0.029
   * - 0.2
     - 0.800
     - 0.250
     - 0.050
   * - 0.4
     - 0.850
     - 0.471
     - 0.071
   * - 0.6
     - 0.900
     - 0.667
     - 0.067
   * - 0.8
     - 0.950
     - 0.842
     - 0.042
   * - 1.0
     - 1.000
     - 1.000
     - 0.000

A request of a tenth of the minimum capacity is thus met at 77.5 % of the
continuous minimum-speed COP, with the compressor running 12.9 % of the time.
Without this model every request below the floor was answered with the minimum
capacity itself — about half the nameplate for the default unit in mild
weather — whatever was asked for. ``Cd_cycling`` sets the coefficient: a number
replaces 0.25, ``0`` is loss-free cycling, and ``None`` restores the earlier
behaviour (continuous operation at the minimum speed, ``Q_ref_iu [W]`` above
the request, ``runtime_fraction [-]`` of 1). The operating-point search weighs
the degradation, so the minimum-speed point may sit a few watts from the one
found with ``Cd_cycling=None``.

**Above the maximum capacity** the unit runs flat out
(``capacity_clamped == "max"``): ``Q_ref_iu [W]`` is the ceiling,
``Q_max [W]`` repeats it, ``capacity_binding`` names what set it and
``Q_unmet [W]`` is the part of the request that is not delivered.
``Q_request [W]`` (signed, as asked) and ``Q_unmet [W]`` are on every row, so a
schedule's unmet energy is the sum of ``Q_unmet [W]`` times the time step.

What the cycling model does not contain: the indoor fan is taken to run only
while heat is delivered, no standby or crankcase power is added while the unit is off,
and a start is not resolved in time — the degradation is the standard's
seasonal-average figure, not a transient simulation.

COP boundary
============

``Q_r_iu`` is the coil requirement before fan heat, not net room delivery.
It retains its sign convention and governs compressor speed, rated capacity,
part-load ratio and the operating-point search. ``cop_ref [-]`` remains
``Q_ref_iu [W] / E_cmp [W]``.

The space-conditioning system metric is
``abs(Q_a_iu_out [W] - Q_a_iu_in [W]) / E_tot [W]``. Both air energy
flows use the actual indoor airflow and the same outdoor reference temperature.
The final outlet already includes indoor fan heat: net cooling equals coil
duty minus indoor fan power, while net heating equals coil duty plus that
power. Do not add or subtract it a second time. The denominator includes
compressor, indoor fan and outdoor fan electricity.

``indoor_heat_direction_valid`` identifies delivery in the requested mode.
Opposite-direction transfer or zero airflow during active operation gives
NaN system COP; off operation has zero air energy flows and NaN COP.
This reporting diagnostic does not alter the coil-load solver.

This dry-coil model reports sensible cooling, not total latent-plus-sensible
cooling or a certified rating. Catalogue comparisons must use the catalogue's
own capacity and electrical-input boundary. Earlier results through commit
``c5d673e`` used ``Q_ref_iu / E_tot`` and must be labelled as coil-duty COP.
For a seasonal performance factor, sum actual mode-specific air delivery
times timestep duration and divide by summed system electricity times duration;
an arithmetic mean of hourly COP is a different statistic.

API reference
=============

.. automodule:: tmhp.air_source_heat_pump
    :members:
    :undoc-members:
    :show-inheritance:
