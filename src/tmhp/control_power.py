"""Fixed control power: what a running heat pump draws besides its compressor, fans and pumps.

A real unit also feeds a control board, sensors, a display, communications and
the standby losses of its drive. That power does not follow the load: it is
there, at about the same level, whenever the unit is in operation. The
refrigerant cycle knows nothing of it, so the models take it as an input,
``E_ctrl`` [W], and report it as ``E_ctrl [W]``:

* it is part of ``E_tot [W]`` and therefore of ``cop_sys [-]``; ``cop_ref
  [-]`` (heat over the compressor input) does not see it;
* it enters the operating-point search with the rest of ``E_tot``. Where the
  compressor modulates, every candidate delivers the request and a constant
  does not move the optimum; on the speed floor the candidates deliver
  different amounts and the constant is weighed against the heat each one
  delivers;
* its heat is credited to no stream (the electronics sit in the outdoor unit
  or in a plant room, outside the air and water the models balance), so the
  exergy it carries is destroyed (``Xc_ctrl [W]``);
* ``E_ctrl=0`` is the machine without it.

The system boundary is that of the rating standards: the effective power input
of EN 14511-3 is the compressor and defrost, "all control and safety devices"
and the proportional share of the fans and pumps, so a catalogue COP has this
power in its denominator whatever the data sheet calls it.

When the compressor is off
--------------------------
``E_ctrl`` is the power of the unit *in operation*. What the unit draws while
the compressor is off is accounted where the off time is:

* **On/off mean of a steady row** (:mod:`tmhp.cycling`). ``E_ctrl`` belongs to
  the machine's electrical inputs, which the mean multiplies by the power
  fraction ``CR / PLF``. For a unit delivering to water that fraction is ``CR
  + (1 - Cc)(1 - CR)``: the EN 14825 degradation already charges the off
  periods with ``1 - Cc`` of the *whole* running input, ``E_ctrl`` included,
  and nothing is added on top of it -- a standby power counted here and again
  through ``Cc`` would be counted twice. A unit whose off-state draw is known
  to be its control power alone has ``Cc = 1 - E_ctrl / E_tot`` at the minimum
  speed (``Cd_cycling``). For a unit delivering to air the power fraction is
  the run-time fraction: the standard charges the start, not the off period,
  and ``E_ctrl`` is drawn for the time the compressor runs.
* **Frost and defrost.** The controls stay on through a defrost: the mean over
  frosting and defrost leaves ``E_ctrl [W]`` as it is.
* **A boiler's dynamic step.** The row is the mean over a step in which the
  thermostat may have switched the compressor; ``E_ctrl [W]`` is ``E_ctrl``
  times ``runtime_fraction [-]``, like every other power of the running
  machine.
* **Off rows.** A unit that is not asked to run is switched off, not on
  standby: ``E_ctrl [W]`` is 0, as every power of an off row is. The standby
  energy of the hours without a request (the ``P_SB`` / ``P_OFF`` /
  ``P_TO`` hours of an EN 14825 seasonal figure) is not in the results.

Default values
--------------
The defaults are medians of what Heat Pump Keymark certificates declare
(``validation/data/auxiliary_power/``, extracted by
``validation.auxiliary_power.pto_proxy``; levels in
``validation/data/rotary_speed_evidence/fixed_power_levels.csv``):

* units delivering to **water** -- 15 W, the median of the two off/standby
  declarations (``P_offsb_a_W``, ``P_offsb_b_W``) pooled over 8,676 air-to-water
  certificates;
* units delivering to **air** -- 12 W, the median thermostat-off power
  (``P_TO_W``: compressor off, controls live) of 19 air-to-air certificates
  (their off/standby declarations are about 1 W, the ecodesign standby cap,
  not a controls-live power).

Both are compressor-off declarations read as the power of the electronics; no
certificate states the electronics' draw of a running unit separately. The
brine- and water-source families have no declared population in the
repository and take the value of the air-source family with the same sink
(the electronics of a hydronic unit and of an air-delivering unit, whatever the
source): carried over, not measured.
"""

from __future__ import annotations

__all__ = [
    "CONTROL_POWER_SINK_AIR_W",
    "CONTROL_POWER_SINK_WATER_W",
    "CONTROL_POWER_KEY",
]

#: Default fixed control power of a unit delivering to water [W]: median of the off/standby declarations of
#: 8,676 Heat Pump Keymark air-to-water certificates.
CONTROL_POWER_SINK_WATER_W = 15.0
#: Default fixed control power of a unit delivering to air [W]: median thermostat-off power of 19 Heat Pump
#: Keymark air-to-air certificates.
CONTROL_POWER_SINK_AIR_W = 12.0
#: Fixed control power of the row [W]: ``E_ctrl`` of the running unit, averaged like its other electrical inputs.
CONTROL_POWER_KEY = "E_ctrl [W]"
