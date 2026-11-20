"""Run EnergyPlus 24.2 with the installed TMHP wheel and check its hourly SQLite outputs."""

import math
import sqlite3
import subprocess
import sys
import sysconfig
from pathlib import Path
from tempfile import TemporaryDirectory


def hourly_values(db: sqlite3.Connection, key: str, units: str, aggregation: str) -> list[float]:
    """Require one finite, nonnegative plugin observation in each design-day hour."""
    rows = db.execute(
        """
        SELECT t.Hour, d.Value
          FROM ReportData AS d
          JOIN ReportDataDictionary AS v USING (ReportDataDictionaryIndex)
          JOIN Time AS t USING (TimeIndex)
         WHERE v.Name = 'PythonPlugin:OutputVariable' AND v.KeyValue = ?
           AND v.Units = ? AND v.Type = ? AND v.ReportingFrequency = 'Hourly'
           AND t.Interval = 60
         ORDER BY t.Hour
        """,
        (key, units, aggregation),
    ).fetchall()
    if [hour for hour, _ in rows] != list(range(1, 25)):
        raise ValueError(f"Expected hours 1–24 for {key} [{units}] ({aggregation}), got {rows}")
    values = [value for _, value in rows]
    if any(value is None or not math.isfinite(value) or value < 0 for value in values):
        raise ValueError(f"Invalid {key} [{units}] observations: {values}")
    return values


def check_results(database: Path) -> None:
    """Compare both metered channels and the stock 24.2 design-day reference."""
    with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as db:
        energies = hourly_values(db, "TMHP Energy", "J", "Sum")
        powers = hourly_values(db, "TMHP Power", "W", "Avg")
    for hour, (energy, power) in enumerate(zip(energies, powers, strict=True), 1):
        if not math.isclose(energy, power * 3600, rel_tol=1e-9, abs_tol=1e-5):
            raise ValueError(f"Hour {hour}: {energy} J does not equal {power} W × 3600 s")

    total, peak = math.fsum(energies), max(powers)
    # Stock 24.2.0-e7ecb2d53b on Linux x86-64 and macOS ARM64 agreed within
    # 0.001 J/day and 1e-6 W. Allow 0.01% for differences in native libraries.
    if not math.isclose(total, 172_377_180.49, rel_tol=1e-4) or not math.isclose(peak, 2457.35060, rel_tol=1e-4):
        raise ValueError(f"Design-day output outside reference tolerance: {total} J, {peak} W peak")
    print(f"24 hourly J/W pairs verified; total {total:.3f} J, peak {peak:.6f} W")


def run(energyplus: Path, output: Path) -> None:
    """Render the wheel path for embedded Python and run the official CLI."""
    fixture = Path(__file__).with_name("plant.idf").read_text()
    model = output / "plant.idf"
    model.write_text(fixture.replace("@SITE_PACKAGES@", sysconfig.get_path("purelib")))
    result = subprocess.run([energyplus, "-D", "-d", output, model], cwd=output)
    error_file = output / "eplusout.err"
    if error_file.exists():
        print(error_file.read_text(), flush=True)
    result.check_returncode()
    summary = (output / "eplusout.end").read_text()
    if "Completed Successfully-- 0 Warning; 0 Severe Errors;" not in summary:
        raise ValueError(summary)
    check_results(output / "eplusout.sql")


if __name__ == "__main__":
    with TemporaryDirectory(prefix="tmhp-energyplus-") as directory:
        run(Path(sys.argv[1]).resolve(), Path(directory).resolve())
