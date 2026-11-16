"""Compressor efficiency maps of earlier releases, recorded outside the library for reproduction (#164).

The library ships one set of efficiency models: the compressor-type-specific
semi-empirical models of :mod:`tmhp.compressor_efficiency`
(``COEFFICIENT_VERSION``). The maps it shipped before are not supported
defaults and are not part of the package. They are kept here, number for
number, for two uses only:

* validation studies that compare the present models against an earlier map
  (a contrast arm labelled with that map's release name);
* tests whose scenario -- a limit reached at a stated duty, a number recorded
  from an earlier commit -- was recorded on a machine built with an earlier
  map (``tests/conftest.py``, marker ``earlier_defaults``).

==========================================  ==========================================  =====================
release                                     module                                      fit archive
==========================================  ==========================================  =====================
``BITZER-three-C3-absolute-N-2026-10-06``   :mod:`.bitzer_quadratic_2026_10_06`         the folder of that name
``v2026-09-24``                             :mod:`.v2026_09_24`                         the folder of that name
==========================================  ==========================================  =====================

Each module keeps the names its release had (``make_eta_vol``,
``make_eta_isen``, ``make_eta_em``, ...). The models are plain user-style
callables ``model(PR, rps)``; a heat-pump model takes them as ``eta_cmp_isen``
/ ``eta_cmp_vol`` / ``eta_cmp`` like any efficiency a caller supplies::

    from validation.coefficients.recorded_maps import recorded_efficiencies

    hp = AirSourceHeatPump(**recorded_efficiencies("BITZER-three-C3-absolute-N-2026-10-06", rps_rated=60.0))
"""

from __future__ import annotations

from collections.abc import Callable

from . import bitzer_quadratic_2026_10_06, v2026_09_24

__all__ = ["RELEASES", "bitzer_quadratic_2026_10_06", "recorded_efficiencies", "v2026_09_24"]

#: Release name -> the module that records its map.
RELEASES = {
    bitzer_quadratic_2026_10_06.COEFFICIENT_VERSION: bitzer_quadratic_2026_10_06,
    v2026_09_24.COEFFICIENT_VERSION: v2026_09_24,
}


def recorded_efficiencies(release: str, rps_rated: float) -> dict[str, Callable]:
    """The three efficiency models of ``release`` for a machine rated at ``rps_rated`` [rev/s].

    Keys are the constructor arguments ``eta_cmp_isen``, ``eta_cmp_vol`` and
    ``eta_cmp``. The 2026-09-24 correlations are written in speed relative to
    ``rps_rated``; the quadratic map uses the absolute speed and reads
    ``rps_rated`` only when a model is called without a speed.
    """
    try:
        module = RELEASES[release]
    except KeyError:
        raise ValueError(f"no recorded map {release!r}; recorded: {sorted(RELEASES)}") from None
    return {
        "eta_cmp_isen": module.make_eta_isen(rps_rated),
        "eta_cmp_vol": module.make_eta_vol(rps_rated),
        "eta_cmp": module.make_eta_em(rps_rated),
    }
