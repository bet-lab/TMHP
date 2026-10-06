"""Level 3 -- fixed-boundary part-load sweep (compressor modulation test).

Hold both boundary temperatures, lower the requested duty from nameplate to
about 12 %, and record the internal chain the strategy document asks for:

    N (n*) -> m_dot -> T_evap, T_cond -> PR -> eta_vol, eta_isen, eta_em -> W_cmp -> COP

Nothing here is scored against a target COP curve: the strategy is explicit
that the part-load trend must *emerge* from the physics and is checked for
internal consistency (lift falls with speed, PR falls, specific work falls),
not bent toward a shape.  Certification-condition trends, where the outdoor
temperature moves with the load, are a different product:
``validation.en14825_seasonal_trend``.

Run:  uv run python -m validation.fixed_boundary_plr.sweep
      uv run python -m validation.fixed_boundary_plr.figure
"""
