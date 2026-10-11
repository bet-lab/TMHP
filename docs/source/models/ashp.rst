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
