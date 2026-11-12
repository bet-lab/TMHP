"""
Weather data processing and irradiance calculations.

Irradiance convention of the dynamic models
-------------------------------------------
The ``I_DN_schedule`` / ``I_dH_schedule`` inputs of the boiler models are the
**direct-normal** irradiance (DNI, beam on a surface facing the sun) and the
**diffuse-horizontal** irradiance (DHI), step averages in W/m². A collector or
PV array turns them into the irradiance on its own plane with
:func:`plane_of_array_irradiance`, from the sun position at the step midpoint
(:func:`solar_position`, site and clock from the collector's ``latitude``,
``longitude``, ``utc_offset_h`` and ``start_time``). Measured global-horizontal
irradiance (GHI, e.g. KMA data) is split into DNI and DHI with
:func:`decompose_ghi_to_poa`, whose result carries ``dni`` and ``dhi`` columns.
"""

import math
import warnings
from typing import Any

import numpy as np
import pandas as pd
import pvlib

from . import calc_util as cu

__all__ = [
    "decompose_ghi_to_poa",
    "load_kma_T0_sol_hourly_csv",
    "load_kma_solar_csv",
    "plane_of_array_irradiance",
    "solar_position",
]

#: Default site: Seoul (37.57 °N, 126.98 °E), Korea Standard Time (UTC+9).
DEFAULT_LATITUDE: float = 37.57
DEFAULT_LONGITUDE: float = 126.98
DEFAULT_UTC_OFFSET_H: float = 9.0
#: Default clock of simulation second 0: local midnight starting 20 March
#: (day 79, the March equinox), i.e. a year-average sun path.
DEFAULT_START_DAY_OF_YEAR: float = 79.0

_SKY_MODELS = ("isotropic", "haydavies", "perez")


def start_day_of_year(start_time: Any, utc_offset_h: float) -> float:
    """Fractional day of year (1 = 1 January 00:00) of a run's second 0.

    ``start_time`` is anything :class:`pandas.Timestamp` accepts. A naive
    timestamp is local standard time at ``utc_offset_h``; an aware one is
    converted to it. ``None`` gives :data:`DEFAULT_START_DAY_OF_YEAR`.
    """
    if start_time is None:
        return DEFAULT_START_DAY_OF_YEAR
    ts = pd.Timestamp(start_time)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC").tz_localize(None) + pd.Timedelta(hours=utc_offset_h)
    return float(ts.dayofyear) + float(ts.hour + ts.minute / 60.0 + ts.second / 3600.0) / 24.0


def solar_position(
    day_of_year: float,
    *,
    latitude: float = DEFAULT_LATITUDE,
    longitude: float = DEFAULT_LONGITUDE,
    utc_offset_h: float = DEFAULT_UTC_OFFSET_H,
) -> tuple[float, float]:
    """Solar zenith and azimuth [deg] at a fractional day of year (local standard time).

    ``day_of_year`` counts from 1 at 1 January 00:00 local standard time
    (``utc_offset_h`` hours ahead of UTC); its fraction is the clock time.
    Spencer (1971) declination and equation of time with the analytical
    zenith/azimuth (Duffie & Beckman, *Solar Engineering of Thermal
    Processes*, 4th ed., §1.6): within 0.3° (zenith) and 0.4° (azimuth) of
    NREL SPA over a year at Seoul, without atmospheric refraction. Azimuth is clockwise from north
    (180 = south), as in pvlib.
    """
    day = (float(day_of_year) - 1.0) % 365.0
    clock_h = (day - math.floor(day)) * 24.0
    gamma = 2.0 * math.pi * math.floor(day) / 365.0
    decl = (
        0.006918
        - 0.399912 * math.cos(gamma)
        + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma)
        + 0.000907 * math.sin(2 * gamma)
        - 0.002697 * math.cos(3 * gamma)
        + 0.00148 * math.sin(3 * gamma)
    )
    eot_min = 229.18 * (
        0.000075
        + 0.001868 * math.cos(gamma)
        - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma)
        - 0.040849 * math.sin(2 * gamma)
    )
    solar_h = clock_h + (4.0 * (longitude - 15.0 * utc_offset_h) + eot_min) / 60.0
    omega = math.radians(15.0 * (solar_h - 12.0))
    phi = math.radians(latitude)
    cos_z = math.sin(phi) * math.sin(decl) + math.cos(phi) * math.cos(decl) * math.cos(omega)
    cos_z = min(1.0, max(-1.0, cos_z))
    zenith = math.acos(cos_z)
    sin_z = math.sin(zenith)
    if sin_z < 1e-9:
        return math.degrees(zenith), 180.0
    sin_az = -math.cos(decl) * math.sin(omega) / sin_z
    cos_az = (math.sin(decl) * math.cos(phi) - math.cos(decl) * math.sin(phi) * math.cos(omega)) / sin_z
    azimuth = math.degrees(math.atan2(sin_az, cos_az)) % 360.0
    return math.degrees(zenith), azimuth


def ashrae_iam(aoi_deg: float, b0: float) -> float:
    """ASHRAE incidence-angle modifier ``1 - b0 (1/cos θ - 1)``, clipped to [0, 1]."""
    if aoi_deg >= 90.0:
        return 0.0
    return min(1.0, max(0.0, 1.0 - b0 * (1.0 / math.cos(math.radians(aoi_deg)) - 1.0)))


def diffuse_iam(tilt_deg: float, b0: float) -> tuple[float, float]:
    """Sky and ground diffuse incidence-angle modifiers of a tilted surface.

    Equivalent incidence angles of Brandemuehl & Beckman (1980) (Duffie &
    Beckman eq. 5.4.1–5.4.2) applied to :func:`ashrae_iam`.
    """
    b = tilt_deg
    theta_sky = 59.7 - 0.1388 * b + 0.001497 * b * b
    theta_gnd = 90.0 - 0.5788 * b + 0.002693 * b * b
    return ashrae_iam(theta_sky, b0), ashrae_iam(theta_gnd, b0)


def plane_of_array_irradiance(
    dni: float,
    dhi: float,
    *,
    zenith: float,
    azimuth: float,
    tilt: float,
    surface_azimuth: float,
    albedo: float = 0.2,
    sky_model: str = "perez",
    day_of_year: float = DEFAULT_START_DAY_OF_YEAR,
) -> dict[str, float]:
    """Irradiance on a tilted plane [W/m²] from DNI and DHI and the sun position.

    Beam ``DNI·cos θ`` (zero with the sun below the horizon or behind the
    plane), sky diffuse from ``sky_model`` (``"perez"`` (default, as
    :func:`decompose_ghi_to_poa`), ``"haydavies"`` or ``"isotropic"``, via
    pvlib) and ground-reflected ``albedo·GHI·(1 − cos β)/2`` with
    ``GHI = DNI·cos z + DHI``. With the sun below the horizon the beam is zero
    and the sky diffuse is isotropic.

    Returns ``beam``, ``sky``, ``ground``, ``total``, ``aoi`` [deg],
    ``ghi``, ``zenith`` and ``dni_used`` (the DNI after the horizon check).
    """
    if sky_model not in _SKY_MODELS:
        raise ValueError(f"sky_model must be one of {_SKY_MODELS}, got {sky_model!r}")
    dni = max(0.0, float(dni))
    dhi = max(0.0, float(dhi))
    beta = math.radians(tilt)
    up = zenith < 90.0
    dni_used = dni if up else 0.0
    cos_z = max(0.0, math.cos(math.radians(zenith)))
    ghi = dni_used * cos_z + dhi
    cos_aoi = math.cos(math.radians(zenith)) * math.cos(beta) + math.sin(math.radians(zenith)) * math.sin(
        beta
    ) * math.cos(math.radians(azimuth - surface_azimuth))
    cos_aoi = min(1.0, max(-1.0, cos_aoi))
    aoi = math.degrees(math.acos(cos_aoi))
    beam = dni_used * max(0.0, cos_aoi) if up else 0.0
    if up and sky_model != "isotropic" and dhi > 0.0:
        sky = float(
            pvlib.irradiance.get_sky_diffuse(
                tilt,
                surface_azimuth,
                zenith,
                azimuth,
                dni_used,
                ghi,
                dhi,
                dni_extra=float(pvlib.irradiance.get_extra_radiation(day_of_year)),
                airmass=float(pvlib.atmosphere.get_relative_airmass(zenith)),
                model=sky_model,
            )
        )
        if not math.isfinite(sky):
            sky = dhi * (1.0 + math.cos(beta)) / 2.0
    else:
        sky = dhi * (1.0 + math.cos(beta)) / 2.0
    ground = albedo * ghi * (1.0 - math.cos(beta)) / 2.0
    return {
        "beam": beam,
        "sky": max(0.0, sky),
        "ground": ground,
        "total": beam + max(0.0, sky) + ground,
        "aoi": aoi,
        "ghi": ghi,
        "zenith": zenith,
        "dni_used": dni_used,
    }


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
        DNI/DHI decomposition model: ``'erbs'`` (default), ``'disc'``,
        ``'dirint'`` or ``'boland'`` (pvlib). Any other name raises
        ``ValueError``.
    transposition : str, optional
        POA transposition model ('perez', 'isotropic', etc). Default is 'perez'.

    Returns
    -------
    pd.DataFrame
        DataFrame with 'poa_global', 'poa_direct', 'poa_diffuse' (and pvlib's
        sky/ground components) plus the decomposed ``'dni'`` and ``'dhi'``,
        which are the ``I_DN_schedule`` / ``I_dH_schedule`` inputs of the
        dynamic models.
    """
    if not isinstance(ghi, pd.Series):
        raise ValueError("ghi must be a pandas Series with DatetimeIndex")

    times = ghi.index
    location = pvlib.location.Location(latitude, longitude, tz, altitude)

    # 1. Solar position.
    solar_position = location.get_solarposition(times)
    zenith = solar_position["zenith"]

    # 2. Decompose GHI into DNI and DHI.
    method = decomposition.lower()
    if method == "erbs":
        dni_dhi = pvlib.irradiance.erbs(ghi, zenith, times.dayofyear)
        dni, dhi = dni_dhi["dni"], dni_dhi["dhi"]
    elif method in ("disc", "dirint"):
        if method == "disc":
            dni = pvlib.irradiance.disc(ghi, zenith, times)["dni"]
        else:
            dni = pvlib.irradiance.dirint(ghi, zenith, times)
        dni = dni.fillna(0.0)
        dhi = (ghi - dni * np.cos(np.radians(zenith)).clip(lower=0.0)).clip(lower=0.0)
    elif method == "boland":
        dni_dhi = pvlib.irradiance.boland(ghi, zenith, times)
        dni, dhi = dni_dhi["dni"], dni_dhi["dhi"]
    else:
        raise ValueError(f"decomposition must be 'erbs', 'disc', 'dirint' or 'boland', got {decomposition!r}")

    # 3. Transpose to plane-of-array (POA) irradiance.
    dni_extra = pvlib.irradiance.get_extra_radiation(times)
    airmass = location.get_airmass(times=times)
    poa = pvlib.irradiance.get_total_irradiance(
        surface_tilt=tilt,
        surface_azimuth=azimuth,
        solar_zenith=zenith,
        solar_azimuth=solar_position["azimuth"],
        dni=dni,
        ghi=ghi,
        dhi=dhi,
        dni_extra=dni_extra,
        airmass=airmass["airmass_absolute"],
        model=transposition.lower(),
    )
    poa = pd.DataFrame(poa)
    poa["dni"] = dni
    poa["dhi"] = dhi
    return poa
