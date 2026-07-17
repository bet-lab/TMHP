"""
Visualization and summary output functions (Facade for backward compatibility).
"""

from .mollier_diagram import plot_ph_diagram, plot_th_diagram, plot_ts_diagram
from .simulation_summary import print_simulation_summary

__all__ = [
    "print_simulation_summary",
    "plot_ph_diagram",
    "plot_th_diagram",
    "plot_ts_diagram",
]
