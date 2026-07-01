"""tmhp — Thermodynamic Models for Heat Pumps: a general-purpose, physics-based heat pump modeling library.

When installed as a standalone package (``uv pip install -e .``),
import as::

    from tmhp import AirSourceHeatPumpBoiler

Support modules are imported for compatibility with existing ``tmhp.<name>``
lookups, while this package-level ``__all__`` keeps ``from tmhp import *``
focused on the model-oriented public facade.
"""

from .air_source_heat_pump import AirSourceHeatPump
from .air_source_heat_pump_boiler import AirSourceHeatPumpBoiler
from .ashpb_pv_ess import ASHPB_PV_ESS
from .ashpb_stc_preheat import ASHPB_STC_preheat
from .ashpb_stc_tank import ASHPB_STC_tank
from .calc_util import *  # noqa: F401, F403
from .compressor_envelope import check_pr_envelope
from .constants import *  # noqa: F401, F403
from .dhw import *  # noqa: F401, F403
from .dynamic_context import *  # noqa: F401, F403
from .enex_functions import *  # noqa: F401, F403
from .ground_source_heat_pump import GroundSourceHeatPump
from .ground_source_heat_pump_boiler import GroundSourceHeatPumpBoiler
from .gshp_empirical import GroundSourceHeatPumpEmpirical
from .gshpb_pv_ess import GSHPB_PV_ESS
from .gshpb_stc_ground import GSHPB_STC_ground
from .gshpb_stc_preheat import GSHPB_STC_preheat
from .gshpb_stc_routed import GSHPB_STC_routed, default_solar_router
from .gshpb_stc_tank import GSHPB_STC_tank
from .heat_transfer import *  # noqa: F401, F403
from .refrigerant import *  # noqa: F401, F403
from .subsystems import *  # noqa: F401, F403
from .thermodynamics import *  # noqa: F401, F403
from .visualization import *  # noqa: F401, F403
from .water_source_heat_pump_boiler import WaterSourceHeatPumpBoiler
from .weather import *  # noqa: F401, F403

__all__ = [
    "check_pr_envelope",
    "AirSourceHeatPump",
    "AirSourceHeatPumpBoiler",
    "ASHPB_PV_ESS",
    "ASHPB_STC_preheat",
    "ASHPB_STC_tank",
    "GroundSourceHeatPump",
    "GroundSourceHeatPumpBoiler",
    "GroundSourceHeatPumpEmpirical",
    "GSHPB_PV_ESS",
    "GSHPB_STC_ground",
    "GSHPB_STC_preheat",
    "GSHPB_STC_routed",
    "GSHPB_STC_tank",
    "WaterSourceHeatPumpBoiler",
    "default_solar_router",
]
