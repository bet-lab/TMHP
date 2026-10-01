"""Random operating-point sample of the air-to-air heat pump, one CSV per efficiency case.

The sensitivity study in ``validation/compressor_efficiency_sensitivity_simple``
holds the boundary air temperatures fixed (heating 7/20 °C, cooling 35/27 °C)
and varies only the requested PLR, so its conclusions rest on two operating
lines. This package instead draws operating points at random over a wide
envelope -- requested PLR 0.10-1.50 of the 3.5 kW nameplate, outdoor air
-20...40 °C, room air 15...30 °C, all independent uniform -- and records the
steady-state result of every point.

The same draw (fixed by ``SEED``) is solved once per case in ``CASE_ORDER``:
``BASE`` (all three compressor efficiencies held at the sensitivity study's
constants, η_v 0.90 / η_is 0.70 / η_em 0.90) plus the six single-efficiency
cases, imported from there so the two studies cannot drift apart. Each case
writes its own CSV named by ``CASE_SLUG`` (``config.py``), e.g.
``uniform_10k_points_default_cmp_eff.csv`` for BASE and
``uniform_10k_points_etais_nstar.csv`` for N-I (isentropic efficiency driven
by rotor speed) -- so the seven files line up row-for-row by ``point_id``.

The duty follows the boundary temperatures: outdoor colder than the room is
heating, outdoor warmer is cooling. Requesting the opposite sign makes the
refrigerant cycle infeasible and the model returns ``cycle_invalid``.

Exergy is not computed (``analyze_steady(postprocess=False)``): this archive
is about compressor operating points, not exergy.

``src/tmhp`` is untouched; the efficiencies are passed to
:class:`tmhp.AirSourceHeatPump` as ``(P_r, rps)`` callables.
"""
