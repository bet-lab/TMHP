"""
Weather data processing and irradiance calculations.
"""

import warnings

import numpy as np
import pandas as pd
import pvlib

from . import calc_util as cu

__all__ = [
    "decompose_ghi_to_poa",
    "load_kma_T0_sol_hourly_csv",
    "load_kma_solar_csv",
]


def load_kma_solar_csv(csv_path: str, encoding: str = "euc-kr") -> pd.DataFrame:
    """Load a Korea Meteorological Administration (KMA, 기상청) 1-minute
    cumulative solar irradiance CSV.

    Parameters
    ----------
    csv_path : str
        Path to CSV file.
    encoding : str, optional
        File encoding. Default is 'euc-kr'.

    Returns
    -------
    pd.DataFrame
        DataFrame with datetime index and 'ghi' column [W/m2].
    """
    warnings.warn(
        "load_kma_solar_csv is deprecated. Use enex_analysis.external_api.kma_loader instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    df = pd.read_csv(csv_path, encoding=encoding)

    # 1. Parse the timestamp column. KMA exports label it '일시' or '시간';
    # both spellings are accepted.
    time_col = df.columns[df.columns.str.contains("일시|시간")][0]
    df["datetime"] = pd.to_datetime(df[time_col])

    # BUGFIX: KMA timestamps are KST but parsed as tz-naive. If passed to
    # pvlib as-is they would be interpreted as UTC, producing a 9-hour offset
    # that shows up as a sunrise/sunset DNI anomaly. Localise explicitly.
    if df["datetime"].dt.tz is None:
        df["datetime"] = df["datetime"].dt.tz_localize("Asia/Seoul")

    df.set_index("datetime", inplace=True)

    # 2. Parse the cumulative irradiance column (MJ/m² per 1-minute interval)
    # and convert it to an instantaneous W/m² rate.
    solar_col = df.columns[df.columns.str.contains("일사")][0]
    df["ghi"] = df[solar_col].diff().fillna(0) * 1e6 / 60
    df.loc[df["ghi"] < 0, "ghi"] = 0

    return df[["ghi"]]


def load_kma_T0_sol_hourly_csv(csv_path: str, encoding: str = "euc-kr") -> pd.DataFrame:
    """Load KMA hourly temperature and solar irradiance CSV.

    Parameters
    ----------
    csv_path : str
        Path to CSV file.
    encoding : str, optional
        File encoding. Default is 'euc-kr'.

    Returns
    -------
    pd.DataFrame
        DataFrame with datetime index, 'T0_K', and 'ghi' columns.
    """
    warnings.warn(
        "load_kma_T0_sol_hourly_csv is deprecated. Use enex_analysis.external_api.kma_loader instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    df = pd.read_csv(csv_path, encoding=encoding)

    # Match columns case-insensitively against a list of candidate substrings.
    # Korean keywords (KMA exports) and English keywords both work.
    def _find_col(patterns: list[str]) -> str:
        for p in patterns:
            match = df.columns[df.columns.str.lower().str.contains(p.lower())]
            if len(match) > 0:
                return str(match[0])
        raise ValueError(f"Column matching {patterns} not found.")

    time_col = _find_col(["일시", "시간", "time", "date"])
    temp_col = _find_col(["기온", "온도", "temp", "t0", "°C", "℃"])
    ghi_col = _find_col(["일사", "ghi", "irradiance", "mj", "solar"])

    df["datetime"] = pd.to_datetime(df[time_col])

    # BUGFIX: KMA timestamps are KST but parsed as tz-naive. If passed to
    # pvlib as-is they would be interpreted as UTC, producing a 9-hour offset
    # that shows up as a sunrise/sunset DNI anomaly. Localise explicitly.
    if df["datetime"].dt.tz is None:
        df["datetime"] = df["datetime"].dt.tz_localize("Asia/Seoul")

    df.set_index("datetime", inplace=True)

    # Convert temperature to Kelvin.
    df["T0_K"] = cu.C2K(df[temp_col])

    # Convert irradiance from MJ/m² per hour to W/m².
    df["ghi"] = df[ghi_col] * cu.MJ2J * cu.s2h
    df.loc[df["ghi"] < 0, "ghi"] = 0

    return df[["T0_K", "ghi"]]


def decompose_ghi_to_poa(
    ghi: np.ndarray,
    latitude: float,
    longitude: float,
    tilt: float,
    azimuth: float,
    altitude: float = 0,
    tz: str = "Asia/Seoul",
    decomposition: str = "erbs",
    transposition: str = "perez",
) -> pd.DataFrame:
    """Decompose GHI to POA (Plane of Array) total irradiance.

    Parameters
    ----------
    ghi : np.ndarray or pd.Series
        Global horizontal irradiance timeseries [W/m2]. Must have DatetimeIndex.
    latitude : float
        Location latitude.
    longitude : float
        Location longitude.
    tilt : float
        Surface tilt angle [deg].
    azimuth : float
        Surface azimuth [deg]. 180 is South.
    altitude : float, optional
        Location altitude [m]. Default is 0.
    tz : str, optional
        Timezone. Default is 'Asia/Seoul'.
    decomposition : str, optional
        DNI/DHI decomposition model ('erbs', 'dirint', etc). Default is 'erbs'.
    transposition : str, optional
        POA transposition model ('perez', 'isotropic', etc). Default is 'perez'.

    Returns
    -------
    pd.DataFrame
        DataFrame with 'poa_global', 'poa_direct', 'poa_diffuse'.
    """
    if not isinstance(ghi, pd.Series):
        raise ValueError("ghi must be a pandas Series with DatetimeIndex")

    times = ghi.index
    location = pvlib.location.Location(latitude, longitude, tz, altitude)

    # 1. Solar position.
    solar_position = location.get_solarposition(times)

    # 2. Decompose GHI into DNI and DHI.
    if decomposition.lower() == "erbs":
        dni_dhi = pvlib.irradiance.erbs(ghi, solar_position["zenith"], times.dayofyear)
    else:
        # Fall back to Erbs for any unrecognised decomposition model.
        dni_dhi = pvlib.irradiance.erbs(ghi, solar_position["zenith"], times.dayofyear)

    dni = dni_dhi["dni"]
    dhi = dni_dhi["dhi"]

    # 3. Transpose to plane-of-array (POA) irradiance.
    dni_extra = pvlib.irradiance.get_extra_radiation(times)
    airmass = location.get_airmass(times=times)
    poa = pvlib.irradiance.get_total_irradiance(
        surface_tilt=tilt,
        surface_azimuth=azimuth,
        solar_zenith=solar_position["zenith"],
        solar_azimuth=solar_position["azimuth"],
        dni=dni,
        ghi=ghi,
        dhi=dhi,
        dni_extra=dni_extra,
        airmass=airmass["airmass_absolute"],
        model=transposition.lower(),
    )

    return poa
