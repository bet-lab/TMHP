"""Level 4 -- certification-condition part-load trend (EN 14825 / Heat Pump Keymark).

EN 14825 points A-D lower the load *and* move the outdoor and sink
temperatures together, so a trajectory through them is ``COP = f(PLR, T_out)``,
not a fixed-boundary modulation test (that is ``validation.fixed_boundary_plr``).
This package groups the two certification-trend products:

``air_to_water``  the 18,106-row Keymark air-to-water baseline, p10-p90 band,
                  four-variant coefficient ablation -- implemented in
                  ``validation.analysis.en14825_trend`` (kept in place because
                  the documentation and figure scripts point there).
``air_to_air``    the 19-model Keymark air-to-air sample (38 declared rows),
                  scored on shape and gradient only -- implemented in
                  ``validation.analysis.ashp_trend``.

The wrappers here run the underlying module and stamp the coefficient
version into the outputs.

Run:  uv run python -m validation.en14825_seasonal_trend.air_to_water   (~40 min incl. ablation)
      uv run python -m validation.en14825_seasonal_trend.air_to_air     (~5 min)
"""
