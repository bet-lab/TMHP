"""Per-run state reset for the ground-source boiler scenario classes.

``GroundSourceHeatPumpBoiler.analyze_dynamic`` starts a run by restoring the
borehole temperatures in °C (``T_bhe``, ``T_bhe_f``, ``T_bhe_f_in``,
``T_bhe_f_out``) but not their Kelvin twin ``T_bhe_f_out_K``, which the first
step's cycle state reads.  The value left behind by the previous run therefore
changes the first row of the next run: the same model with the same inputs
gives a different table the second time.

The proper fix is one line in ``ground_source_heat_pump_boiler.py``
(``self.T_bhe_f_out_K = self.Ts_K`` next to the other resets).  Until that file
carries it, the scenario classes derived from it start every run through
:class:`GroundSourceHeatPumpBoilerRun`, which restores the value first.  The
reset is idempotent, so it stays harmless once the base class does it too.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from .ground_source_heat_pump_boiler import GroundSourceHeatPumpBoiler


class GroundSourceHeatPumpBoilerRun(GroundSourceHeatPumpBoiler):
    """``GroundSourceHeatPumpBoiler`` whose runs do not depend on earlier runs.

    Scenario subclasses extend :meth:`_reset_run_state` (calling ``super()``) to
    restore their own per-run state, e.g. a battery's state of charge.
    """

    def _reset_run_state(self) -> None:
        """Restore every attribute a run reads before it writes it."""
        self.T_bhe_f_out_K = self.Ts_K

    def analyze_dynamic(self, *args: Any, **kwargs: Any) -> pd.DataFrame:
        self._reset_run_state()
        return super().analyze_dynamic(*args, **kwargs)
