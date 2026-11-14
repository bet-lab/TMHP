"""Shared helpers of the FMU adapter tests: build an FMU, and say whether fmpy can run it here."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

#: fmpy platforms PythonFMU and PythonFMU3 ship an export library for. On
#: these the built FMU must run; anywhere else it can only be built and validated.
PLATFORMS_WITH_BINARIES = frozenset({"linux64", "win64"})


def build_fmu(builder: Any, script: str, dest: Path) -> Path:
    """Build ``script`` into an FMU under ``dest`` and leave ``sys.path`` as it was.

    ``FmuBuilder.build_FMU`` inserts the script's folder at the front of
    ``sys.path`` and does not remove it. For the tmhp adapters that folder is
    ``tmhp/integrations``, where ``geolink.py`` then answers ``import geolink``
    for the rest of the process.
    """
    saved = list(sys.path)
    try:
        fmu_file = builder.build_FMU(script, dest=str(dest))
    finally:
        sys.path[:] = saved
    assert fmu_file is not None
    return Path(fmu_file)


def simulation_obstacle(fmu_file: Path) -> str | None:
    """Why fmpy cannot simulate ``fmu_file`` on this machine, or ``None`` if it can.

    A PythonFMU archive carries the export library of the platforms the
    package ships (Linux and Windows, x86-64); fmpy refuses an FMU without a
    binary for the platform it runs on (macOS, for one). The function fails
    instead of reporting an obstacle on a platform the packages do ship, so
    that a broken build cannot pass as "not supported here".
    """
    import fmpy
    from fmpy.util import can_simulate

    platforms = fmpy.supported_platforms(str(fmu_file))
    runnable, _ = can_simulate(platforms)
    if runnable:
        return None
    assert fmpy.platform not in PLATFORMS_WITH_BINARIES, (
        f"the FMU built on {fmpy.platform} carries no binary for it (found {sorted(platforms)})"
    )
    return (
        f"the FMU has binaries for {sorted(platforms)} only; fmpy cannot run it on {fmpy.platform} "
        "(PythonFMU ships no export library for this platform)"
    )
