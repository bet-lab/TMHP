"""Check the smoke runner's humidity assertion without the EnergyPlus CLI."""

import pytest
from tests.energyplus import run_smoke


def test_smoke_accepts_model_humidity_from_the_saturated_design_day(tmp_path):
    """A saturated model reading is accepted even with unrelated log messages."""
    log = tmp_path / "plugin.log"
    log.write_text(
        "[TmhpSurrogate handles] rh_out=10\n"
        "[call    3] T0=  7.00C RH_out=100.00% Qreq=  8000.0W -> converged=True\n"
        "[call    4] T0=  7.00C RH_out=99.99% Qreq=  8000.0W -> converged=True\n"
    )

    run_smoke.check_humidity_log(log)


@pytest.mark.parametrize(
    "entry",
    [
        "",
        "[call    4] T0=  7.00C Qreq=  8000.0W\n",
        "[call    4] RH_out=default Qreq=  8000.0W\n",
        "[call    4] RH_out=84.00% Qreq=  8000.0W\n",
        "[call    4] RH_out=101.00% Qreq=  8000.0W\n",
        "[call    4] RH_out=100.00 Qreq=  8000.0W\n",
        "[call    4] RH_out=100.00% Qreq=  8000.0W\n[call    5] RH_out=default Qreq=  8000.0W\n",
    ],
    ids=["no-calls", "missing", "default", "model-default-number", "out-of-range", "no-unit", "mixed"],
)
def test_smoke_rejects_missing_or_incorrect_model_humidity(tmp_path, entry):
    """Every logged solve must report percent humidity from the design day."""
    log = tmp_path / "plugin.log"
    log.write_text("[TmhpSurrogate handles] rh_out=10\n" + entry)

    with pytest.raises(ValueError, match="humidity"):
        run_smoke.check_humidity_log(log)
