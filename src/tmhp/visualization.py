"""
Visualization and summary output functions (Facade for backward compatibility).
"""

from .mollier_diagram import plot_ph_diagram, plot_th_diagram, plot_ts_diagram

# Expose internal functions if they are used elsewhere
from .simulation_summary import (
    _print_compressor_stats,
    _print_convergence_status,
    _print_fan_stats,
    _print_heat_exchange_stats,
    print_simulation_summary,
)

__all__ = [
    "print_simulation_summary",
    "plot_ph_diagram",
    "plot_th_diagram",
    "plot_ts_diagram",
    "_print_compressor_stats",
    "_print_convergence_status",
    "_print_fan_stats",
    "_print_heat_exchange_stats",
]
