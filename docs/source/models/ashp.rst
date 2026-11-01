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
no unified boundary-selection facade. The default BITZER maps are unchanged;
select a documented alternative through the existing efficiency overrides when
its operating domain and effective-loss definition are appropriate.

Each input row contains **indoor dry-bulb temperature [°C], outdoor dry-bulb
temperature [°C], positive net delivered output [W], and system COP**. Unlike
``analyze_steady``'s signed coil duty, this output includes indoor fan heat.
``predict_catalogue`` and ``fit`` iteratively match the net output before
comparing COP, with the same compressor and two-fan electrical boundary.

.. code-block:: python

   from tmhp import AirSourceHeatPump

   # Illustrative observations, not a manufacturer catalogue or recommended setup.
   hp = AirSourceHeatPump(
       ref="R32", hp_capacity=3500.0,
       eta_cmp_vol=0.90, eta_cmp_isen=0.70, eta_cmp=0.90,
   )
   points = [(20.0, 7.0, 3000.0, 4.0), (20.0, 12.0, 3500.0, 4.5)]
   before = hp.predict_catalogue(points, mode="heating")
   report = hp.fit(
       points,
       mode="heating",
       parameters=("overall_efficiency", "volumetric_efficiency", "heat_loss_fraction"),
       lambda_bounds={
           "overall_efficiency": (0.5, 1.5),
           "volumetric_efficiency": (0.5, 1.5),
           "heat_loss_fraction": (0.0, 2.0),
       },
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

The default search intervals shown above are numerical settings, not published
hardware limits. Auxiliary search defaults to 21 evenly spaced values including
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

An explicitly selected research baseline is available through
``tmhp.compressor_baseline.ossorio_r290_efficiencies()``. Pass its returned mapping
as constructor keyword overrides. It approximates an R290 scroll correlation in
pressure ratio and shaft speed [rev/s], with the original source version retained
in fitting reports. It does not replace the default BITZER maps and is not a
validated substitute for an arbitrary compressor technology or refrigerant.
The reproducible Panasonic case, source audit, exact catalogue tuples and
before/after plots are in ``validation/compressor_fitting/``. In that case COP
RMSE improves slightly on calibration rows while power RMSE increases; the
held-out rows are reported separately.

API reference
=============

.. automodule:: tmhp.air_source_heat_pump
    :members:
    :undoc-members:
    :show-inheritance:
