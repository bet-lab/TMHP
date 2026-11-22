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
        "[outdoor RH final] pid 4242 run 1: zone timesteps with a heating dispatch, warmup days excluded: weather=20 clipped_to_100=4 default=0\n"
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
    log.write_text(
        "[TmhpSurrogate handles] rh_out=10\n"
        + entry
        + "[outdoor RH final] pid 4242 run 1: zone timesteps with a heating dispatch, warmup days excluded: weather=20 clipped_to_100=4 default=0\n"
    )

    with pytest.raises(ValueError, match="humidity"):
        run_smoke.check_humidity_log(log)


@pytest.mark.parametrize(
    "summary",
    [
        "",
        "[outdoor RH final] pid 4242 run 1: zone timesteps with a heating dispatch, warmup days excluded: "
        "weather=20 clipped_to_100=4 default=1\n",
        "[outdoor RH final] pid 4242 run 1: zone timesteps with a heating dispatch, warmup days excluded: "
        "weather=0 clipped_to_100=0 default=0\n",
        "[outdoor RH final] pid 4242 run 1: zone timesteps with a heating dispatch, warmup days excluded: "
        "weather=20 clipped_to_100=4 default=1\n"
        "[outdoor RH final] pid 4242 run 1: zone timesteps with a heating dispatch, warmup days excluded: "
        "weather=20 clipped_to_100=4 default=0\n",
    ],
    ids=["missing", "default-used", "nothing-counted", "two-summaries"],
)
def test_smoke_rejects_missing_or_wrong_humidity_summary(tmp_path, summary):
    """The run must end with humidity counts that never fell back to the default."""
    log = tmp_path / "plugin.log"
    log.write_text("[call    3] T0=  7.00C RH_out=100.00% Qreq=  8000.0W -> converged=True\n" + summary)

    with pytest.raises(ValueError, match="humidity summary"):
        run_smoke.check_humidity_log(log)
