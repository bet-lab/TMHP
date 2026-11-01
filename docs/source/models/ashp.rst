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

Catalogue compressor fitting
============================

``fit`` extends the existing ``AirSourceHeatPump`` instance. The constructor,
``analyze_steady``, and other model classes retain their existing APIs. There is
no unified boundary-selection facade.
``AirSourceHeatPump`` now fills omitted compressor efficiencies with the
Ossorio 2024 R290 scroll research maps. Users do not need a map helper in the
constructor. Explicit scalar or callable efficiency inputs still override the
corresponding map individually, and the legacy ``eta_cmp_mech`` fallback remains
supported. GSHP and boiler classes, and the standalone BITZER factory functions,
retain their existing defaults.

Each input row contains **indoor dry-bulb temperature [°C], outdoor dry-bulb
temperature [°C], positive net delivered output [W], and system COP**. Unlike
``analyze_steady``'s signed coil duty, this output includes indoor fan heat.
``predict_catalogue`` and ``fit`` iteratively match the net output before
comparing COP, with the same compressor and two-fan electrical boundary.

.. code-block:: python

   from tmhp import AirSourceHeatPump

   # Illustrative observations, not a manufacturer catalogue or recommended setup.
   hp = AirSourceHeatPump(ref="R32", hp_capacity=3500.0)
   points = [(20.0, 7.0, 3000.0, 4.0), (20.0, 12.0, 3500.0, 4.5)]
   before = hp.predict_catalogue(points, mode="heating")
   report = hp.fit(
       points,
       mode="heating",
       parameters=("overall_efficiency", "volumetric_efficiency", "heat_loss_fraction"),
   )
   print(report.applied, report.reason, report.applied_scales)
   after = hp.predict_catalogue(points, mode="heating")

The three multipliers have different roles:

* ``overall_efficiency`` fits a single scale to all-row COP RMSE while the
  other scales stay fixed. This is the only COP free parameter.
* Optional ``volumetric_efficiency`` adjusts only when net capacity or the
  configured speed bounds fail, choosing the closest feasible grid value.
* Optional ``heat_loss_fraction`` adjusts only when discharge or effective-loss
  constraints fail, also choosing the closest feasible grid value.

The equations are :math:`\eta_v=\lambda_v\eta_{v,0}`,
:math:`\eta_{ov}=\lambda_{ov}\eta_{ov,0}`, and
:math:`f=\lambda_f f_0`, where
:math:`\eta_{ov,0}=\eta_{is,0}\eta_{em,0}` and
:math:`f_0=1-\eta_{em,0}`. Existing cycle inputs are reconstructed as
:math:`\eta_{is}=\eta_{ov}/(1-f)` and :math:`\eta_{em}=1-f`.
The multipliers act before the cycle is solved, never on completed COP values.

``lambda_bounds`` is optional. Its defaults are ``(0.5, 1.5)`` for overall and
volumetric efficiency, and ``(0.0, 2.0)`` for heat-loss fraction. These intervals
are numerical search settings supplied **before** fitting, not fitted lambda
values or published hardware limits. The actual applied results are
``report.applied_scales``; rejected candidate values are separately retained in
``report.candidate_scales``. Reproduction of a study that used narrower explicit
bounds must retain those bounds or rerun the study with the defaults.
Auxiliary search defaults to 21 evenly spaced values including
both interval endpoints and the current value. Its finite resolution is reported
through the chosen scale; global optimality or unique identification is not
claimed. This calibration adopts :math:`0\le f<1` and :math:`h_2\ge h_{2s}`
as model constraints, not universal laws for every nonadiabatic compressor.
Negative original effective loss is rejected rather than hidden by clipping or
a zero multiplier. A BITZER extrapolation with ``eta_cmp > 1`` therefore needs
baseline/domain review before this fitting scheme can accept it.

All points are re-evaluated after the auxiliary adjustments. If any point fails,
the final COP tolerance is missed, or RMSE worsens against an initially valid
set, the original efficiency callables and applied scales remain unchanged.
``report.candidate_scales`` retains the attempted values;
``report.applied_scales`` is what the model actually uses. ``hp.fit_report``
stores the last attempt, including failure. Repeated fits retain the original
baseline and absolute multipliers; they do not multiply fitted maps again.
Changing efficiency overrides manually after a successful fit requires a new
instance so the baseline cannot silently become stale.
Custom efficiency callables must be pure and deterministic; copying a model
does not isolate side effects in arbitrary user function closures.

If no valid COP candidate exists, the attempt returns
``cop_fit_no_feasible_candidate`` even if an auxiliary scale later restores
feasibility. Each stage records its status; feasibility repair alone does not
establish that COP fitting succeeded.

The report retains every requested row, per-stage results, net capacity error,
speed, pressure ratio, efficiencies, enthalpies, failure reasons and all-row COP
RMSE. Missing or invalid metrics are ``None`` rather than silently filtered rows.
``dataclasses.asdict(report)`` is suitable for strict JSON serialization.
Separate held-out catalogue conditions must be supplied to
``predict_catalogue`` after fitting; they must not influence coefficient
selection. COP and electrical power computed from the same output observation
are not independent validation quantities.

Wet-bulb temperatures must be converted using documented evidence or an explicit
case assumption before calling either method. Neither method supplies a latent
cooling, frost or defrost model. Compressor displacement, UA, airflow/fan models,
speed bounds and all other fixed specifications are never fitted. Only the ASHP
catalogue adapter is provided in this change; GSHP and boiler calls are not
implicitly reinterpreted as air-side conditions.

The ASHP baseline approximates an R290 scroll correlation in pressure ratio and
shaft speed [rev/s], with its source version retained in fitting reports. The
source speed range is 30–110 rev/s; the existing ASHP speed search defaults are
not automatically narrowed to that range, so extrapolation must be checked.
Using this map with R32, a rotary compressor, or another device is a modelling
assumption, not validation of that device. The helper
``tmhp.compressor_baseline.ossorio_r290_efficiencies()`` remains available for
explicit selection in other compatible classes. To reproduce the previous ASHP
BITZER behaviour, pass ``make_eta_vol``, ``make_eta_isen`` and ``make_eta_em`` from
``tmhp.compressor_efficiency`` as explicit efficiency overrides.
The reproducible Fujitsu case, source audit, exact catalogue tuples and
before/after plots are in ``validation/compressor_fitting/``. It uses the
published 8.0 kW cooling rating for ``hp_capacity`` (the default constructor
rating mode), with the 8.8 kW nominal heating output retained separately for
heating PLR. Four calibration and sixteen held-out rows are evaluated. The
earlier Panasonic study and its narrower search bounds remain in
``validation/compressor_fitting/legacy_panasonic/``.

API reference
=============

.. automodule:: tmhp.air_source_heat_pump
    :members:
    :undoc-members:
    :show-inheritance:
