"""Attachable subsystems for heat-pump boiler models.

Given physical input state each subsystem returns a standardised output dict.
All simulation orchestration (activation logic, result assembly, exergy
calculation) is the responsibility of the scenario class that uses the
subsystem. The solar and UV subsystems are stateless pure-physics engines
(the collector-loop controller's latch and lock-out are run state of the
scenario classes, carried in ``DynamicState.subsystem_state``);
``EnergyStorageSystem`` additionally carries battery state (state of charge),
so it is stateful across time steps. A scenario class must call
:meth:`EnergyStorageSystem.reset` at the start of every run so that two runs
of the same model with the same inputs give the same result.

Subsystem catalogue
-------------------
- ``SolarThermalCollector`` — flat-plate / evacuated-tube STC physics engine
  (``calc_performance``, ``is_preheat_on``); stateless
- ``PhotovoltaicSystem`` — PV array + inverter chain; stateless
- ``EnergyStorageSystem`` — battery storage; stateful (tracks state of charge,
  ``reset()`` restores ``SOC_init``)
- ``route_pv_ess`` — one-step PV → inverter / ESS / dump / grid routing shared by
  ``ASHPB_PV_ESS`` and ``GSHPB_PV_ESS`` (importable, not star-exported)
- ``UVLamp`` — UV disinfection lamp; stateless
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field, fields
from typing import TYPE_CHECKING

import numpy as np

from . import calc_util as cu
from ._validation import check_efficiency, check_finite, check_temperature, validate_ctor
from .constants import c_w, k_a, k_D, k_d, rho_w
from .thermodynamics import calc_energy_flow
from .weather import (
    DEFAULT_LATITUDE,
    DEFAULT_LONGITUDE,
    DEFAULT_UTC_OFFSET_H,
    ashrae_iam,
    diffuse_iam,
    plane_of_array_irradiance,
    solar_position,
    start_day_of_year,
)

if TYPE_CHECKING:
    import pandas as pd

    from .dynamic_context import ControlState, StepContext, SubsystemExergy

__all__ = [
    "EnergyStorageSystem",
    "PhotovoltaicSystem",
    "SolarThermalCollector",
    "UVLamp",
]

#: Grid import below this is numerical noise, not a physical draw [W].
#: 1 mW sits twelve orders of magnitude above the double-precision round-off of a
#: kW-scale power balance (~1e-12 W) and far below any meter resolution, so a
#: ratio taken against a larger import is meaningful and one against a smaller
#: import is not.
GRID_IMPORT_FLOOR_W: float = 1e-3


# ------------------------------------------------------------------
# Default subsystem step() return — STC absent/inactive
# ------------------------------------------------------------------

STC_OFF_STEP: dict = {
    "stc_active": False,
    "stc_result": {},
    "T_stc_w_out_K": np.nan,
    "T_stc_pump_w_out_K": np.nan,
    "Q_stc_w_out": 0.0,
    "Q_stc_pump_w_out": 0.0,
    "Q_stc_w_in": 0.0,
    "E_stc_pump": 0.0,
    "Q_contribution": 0.0,
    "E_subsystem": 0.0,
    "T_tank_w_in_override_K": None,
    "freeze": False,
    "lockout": False,
}

# Backward-compatible alias
STC_OFF: dict = STC_OFF_STEP


#: ISO 9806 collector test flow, 0.02 kg/(s·m²) of collector, as a volume flow
#: per m² [m³/(s·m²)] (about 1.2 L/(min·m²)). The default collector-loop flow
#: is this times ``A_stc``: the efficiency coefficients are measured at it.
STC_SPECIFIC_FLOW_M3S_PER_M2: float = 0.02 / rho_w

#: Construction parameters of the former lumped collector model and their old
#: defaults. They are accepted with a ``DeprecationWarning`` and converted to the
#: linear loss coefficient ``a1_stc`` (``a2_stc = 0``).
_LEGACY_CONSTRUCTION_DEFAULTS: dict[str, float] = {
    "h_o_stc": 15.0,
    "h_r_stc": 2.0,
    "k_ins_stc": 0.03,
    "x_air_stc": 0.01,
    "x_ins_stc": 0.05,
}


def _construction_loss_coefficient(h_o: float, h_r: float, k_ins: float, x_air: float, x_ins: float) -> float:
    """Overall loss coefficient [W/(m²·K)] of the former two-path construction model.

    Top: radiation ‖ air gap, then outside convection; bottom: insulation, then
    outside convection; the two paths in parallel.
    """
    R_air = x_air / k_a
    R_ins = x_ins / k_ins
    R_o = 1.0 / h_o
    R_r = 1.0 / h_r if h_r > 0 else np.inf
    R1 = (R_r * R_air) / (R_r + R_air) + R_o if np.isfinite(R_r) else R_air + R_o
    R2 = R_ins + R_o
    return 1.0 / R1 + 1.0 / R2


class _SunFacingSurface:
    """Site, clock and plane-of-array transposition shared by collector and PV array.

    Subclasses are dataclasses that declare ``latitude``, ``longitude``,
    ``utc_offset_h``, ``start_time``, ``albedo`` and ``sky_model`` and return
    their tilt and azimuth from :meth:`_surface`.
    """

    latitude: float
    longitude: float
    utc_offset_h: float
    start_time: object
    albedo: float
    sky_model: str

    def _surface(self) -> tuple[float, float]:  # pragma: no cover - abstract
        raise NotImplementedError

    def _beam_b0(self) -> float:  # pragma: no cover - abstract
        """ASHRAE incidence-angle coefficient ``b0`` of the beam on this surface."""
        raise NotImplementedError

    def _check_site(self) -> None:
        if self.sky_model not in ("isotropic", "haydavies", "perez"):
            raise ValueError(f"sky_model must be 'isotropic', 'haydavies' or 'perez', got {self.sky_model!r}")
        start_day_of_year(self.start_time, self.utc_offset_h)  # parses, or raises

    def plane_irradiance(
        self,
        I_DN: float,
        I_dH: float,
        time_s: float,
        dt_s: float | None = None,
    ) -> dict[str, float]:
        """Irradiance on this surface [W/m²] for one step.

        ``I_DN`` / ``I_dH`` are the step's direct-normal and diffuse-horizontal
        irradiance; ``time_s`` is the time after ``start_time`` [s] at which
        the sun position is taken. With ``dt_s`` the step ``[time_s,
        time_s + dt_s)`` is sampled at the centres of sub-intervals of at
        most 10 min and the components are averaged over the sunlit samples
        (the step average of a measured DNI includes the dark part of a
        sunrise/sunset step as zero, so its sunlit part carries it).
        ``beam_iam`` is the beam times its incidence-angle modifier, taken
        sample by sample before averaging: the modifier is nonlinear and
        clipped, so applying it to the mean angle would lose the beam of a
        step in which the sun grazes the surface or passes behind it.

        A DNI above 1 W/m² while the sun is below the horizon for the whole
        step contradicts the site or clock (``latitude``, ``longitude``,
        ``utc_offset_h``, ``start_time``): the beam is dropped and a
        ``UserWarning`` says so.

        Returns the keys of :func:`tmhp.weather.plane_of_array_irradiance`.
        """
        doy0 = start_day_of_year(self.start_time, self.utc_offset_h)
        tilt, surf_az = self._surface()
        b0 = self._beam_b0()
        if dt_s is None or dt_s <= 0:
            times = [float(time_s)]
        else:
            n_sub = int(min(12, max(1, np.ceil(dt_s / 600.0))))
            times = [float(time_s) + (k + 0.5) * dt_s / n_sub for k in range(n_sub)]
        samples = []
        for t in times:
            doy = doy0 + t / 86400.0
            zen, az = solar_position(
                doy, latitude=self.latitude, longitude=self.longitude, utc_offset_h=self.utc_offset_h
            )
            sample = plane_of_array_irradiance(
                I_DN,
                I_dH,
                zenith=zen,
                azimuth=az,
                tilt=tilt,
                surface_azimuth=surf_az,
                albedo=self.albedo,
                sky_model=self.sky_model,
                day_of_year=doy,
            )
            sample["beam_iam"] = ashrae_iam(sample["aoi"], b0) * sample["beam"]
            samples.append(sample)
        sunlit = [s for s in samples if s["zenith"] < 90.0]
        if not sunlit:
            if I_DN > 1.0:
                warnings.warn(
                    "direct-normal irradiance given while the sun is below the horizon at this site and "
                    "time: the beam is dropped. Check latitude/longitude/utc_offset_h/start_time of the "
                    "collector or PV array against the irradiance schedule.",
                    UserWarning,
                    stacklevel=2,
                )
            return samples[len(samples) // 2]
        keys = ("beam", "beam_iam", "sky", "ground", "total", "aoi", "ghi", "zenith", "dni_used")
        return {k: float(np.mean([s[k] for s in sunlit])) for k in keys}


@dataclass
class SolarThermalCollector(_SunFacingSurface):
    """Glazed solar thermal collector (STC) — pure physics engine.

    Bundles the collector (area, orientation, ISO 9806 efficiency), its loop
    pump and controller settings, and the site and clock used for the sun
    position. This class is a **stateless physics calculator**: given the
    irradiance, the inlet and ambient temperatures and the flow it returns a
    performance dict. Activation and routing are the scenario classes' job.

    Thermal model
    -------------
    Quasi-steady collector efficiency of ISO 9806 / EN 12975 on the mean
    fluid temperature ``T_m = (T_in + T_out)/2``::

        Q_u = A·[η0·(K_b·G_b + K_d·G_d) − a1·(T_m − T_a) − a2·(T_m − T_a)·|T_m − T_a|]
        Q_u = ṁ·c_p·(T_out − T_in)

    solved in closed form for ``T_out``. ``G_b`` / ``G_d`` are the beam and
    diffuse (sky + ground-reflected) irradiance **on the collector plane**,
    transposed from ``I_DN`` / ``I_dH`` with the sun position of the step
    (:meth:`plane_irradiance`); ``K_b = 1 − b0·(1/cos θ − 1)`` is the ASHRAE
    incidence-angle modifier and ``K_d`` the diffuse one (Brandemuehl–Beckman
    equivalent angles of ``b0`` unless ``K_d_stc`` is given). ``T_a`` is the
    outdoor temperature ``T0``. ``Q_sol_stc = A·η0·(K_b·G_b + K_d·G_d)`` is
    the heat absorbed (after optical losses) and the balance
    ``Q_sol_stc = (Q_stc_w_out − Q_stc_w_in) + Q_l_stc`` closes exactly. The
    absolute-value form of the quadratic term keeps the loss odd in
    ``T_m − T_a``.

    The defaults describe a representative single-glazed selective flat-plate
    collector (Solar Keymark range η0 0.75–0.82, a1 3–4 W/(m²·K),
    a2 0.01–0.02 W/(m²·K²), b0 ≈ 0.1); its stagnation temperature at
    1000 W/m² and 30 °C ambient is about 170 °C. Take ``eta0_stc``,
    ``a1_stc``, ``a2_stc`` and ``b0_stc`` (or ``K_d_stc``) from a collector
    datasheet together with the area they refer to.

    Parameters
    ----------
    A_stc : float
        Collector area [m²] that the efficiency coefficients refer to
        (aperture or gross, as on the datasheet). Absorption, losses and
        the default flow scale with it.
    stc_tilt : float
        Tilt from horizontal [°].
    stc_azimuth : float
        Azimuth of the collector normal, clockwise from north [°] (180 = south).
    A_stc_pipe : float | None
        Deprecated alias of ``A_stc`` (it used to be the only area in the
        heat balance).
    alpha_stc, h_o_stc, h_r_stc, k_ins_stc, x_air_stc, x_ins_stc : float | None
        Deprecated construction parameters of the former lumped model.
        ``alpha_stc`` becomes ``eta0_stc``; the others give the linear loss
        coefficient ``a1_stc`` of that model (with ``a2_stc = 0``).
    preheat_start_hour, preheat_end_hour : float
        Clock window [h] in which the collector may run, used by the preheat,
        ground and routed scenarios (the tank-loop scenarios are governed by
        their controller only).
    dV_stc_w : float | None
        Collector-loop flow [m³/s] of the pumped loops. ``None`` (default):
        the ISO 9806 test flow, 0.02 kg/(s·m²) × ``A_stc``. The mains preheat
        scenario runs the collector at the tank feed instead.
    E_stc_pump : float
        Collector-loop pump power [W] while it runs.
    eta0_stc : float
        Zero-loss efficiency η0 [-] (normal incidence).
    a1_stc : float
        First-order heat-loss coefficient [W/(m²·K)].
    a2_stc : float
        Second-order heat-loss coefficient [W/(m²·K²)].
    b0_stc : float
        ASHRAE incidence-angle-modifier coefficient [-].
    K_d_stc : float | None
        Diffuse incidence-angle modifier [-]; ``None`` derives it from ``b0_stc``.
    dT_stc_on, dT_stc_off : float
        Differential controller [K]: a stopped pump starts when the idle
        collector is ``dT_stc_on`` above the sink (and the run would hold),
        a running pump runs on while its outlet stays ``dT_stc_off`` above it.
    T_stc_tank_max : float
        Storage high limit [°C]: the collector loop stops charging the tank at
        this temperature (within the step) and starts again once the tank is
        2 K below it.
    T_stc_boil : float
        Boiling protection [°C]: the loop does not run when the collector
        outlet would reach this temperature.
    latitude, longitude : float
        Site [°], north and east positive. Default Seoul.
    utc_offset_h : float
        Local standard time offset from UTC [h] of the run's clock. Default 9 (KST).
    start_time : str | datetime | None
        Local standard time of simulation second 0 (anything
        :class:`pandas.Timestamp` accepts; an aware timestamp is converted).
        ``None``: 00:00 on 20 March (the equinox, a year-average sun path).
        The boilers count their hour of day from second 0, so a run starting
        at local midnight keeps their schedules and the sun in step.
    albedo : float
        Ground reflectance [-].
    sky_model : str
        Sky-diffuse transposition: ``"perez"`` (default), ``"haydavies"`` or
        ``"isotropic"``.
    T_stc_freeze : float | None
        Freeze protection [°C]. The collector carries water (tank or mains
        water), so it is a drain-back system: while the idle collector
        (:meth:`stagnation_temperature_K`) is below this temperature the
        loop does not run and the collector is drained
        (``stc_freeze_protected [-]``); nor does a pumped loop run while the
        sink it would draw from is below 0 °C. Default 4 °C; ``None``
        switches the protection off (a loop filled with antifreeze).
    T_stc_lockout : float | None
        Collector lock-out after stagnation [°C]. A pumped loop that a
        protection holds off (storage limit, boiling protection, clock
        window) while its idle collector is at or above this temperature is
        not started until the collector has cooled 10 K below it
        (``stc_stagnation_lockout [-]``): cold water must not be pumped into
        an absorber far above its boiling point. Default 130 °C; ``None``
        switches the lock-out off.
    """

    # Collector
    A_stc: float = 2.0
    stc_tilt: float = 35.0
    stc_azimuth: float = 180.0
    A_stc_pipe: float | None = None
    alpha_stc: float | None = None
    h_o_stc: float | None = None
    h_r_stc: float | None = None
    k_ins_stc: float | None = None
    x_air_stc: float | None = None
    x_ins_stc: float | None = None

    # Pump / schedule
    preheat_start_hour: float = 6.0
    preheat_end_hour: float = 18.0
    dV_stc_w: float | None = None
    E_stc_pump: float = 50.0

    # ISO 9806 efficiency
    eta0_stc: float = 0.78
    a1_stc: float = 3.6
    a2_stc: float = 0.015
    b0_stc: float = 0.10
    K_d_stc: float | None = None

    # Loop controller
    dT_stc_on: float = 6.0
    dT_stc_off: float = 2.0
    T_stc_tank_max: float = 85.0
    T_stc_boil: float = 100.0

    # Site and clock
    latitude: float = DEFAULT_LATITUDE
    longitude: float = DEFAULT_LONGITUDE
    utc_offset_h: float = DEFAULT_UTC_OFFSET_H
    start_time: object = None
    albedo: float = 0.2
    sky_model: str = "perez"

    # Collector protections (added last: the positional order of the fields above is unchanged)
    T_stc_freeze: float | None = 4.0
    T_stc_lockout: float | None = 130.0

    def __post_init__(self) -> None:
        validate_ctor("STC", vars(self))
        self._check_site()
        self._check_protection()
        self._apply_legacy_parameters()

    def _check_protection(self) -> None:
        """Freeze limit in the environment range, lock-out above the boiling protection."""
        if self.T_stc_freeze is not None:
            check_temperature("T_stc_freeze", self.T_stc_freeze, "env")
        if self.T_stc_lockout is not None:
            x = check_finite("T_stc_lockout", self.T_stc_lockout)
            if x <= self.T_stc_boil:
                raise ValueError(
                    f"T_stc_lockout ({x:g} °C) must be above T_stc_boil ({self.T_stc_boil:g} °C): a running "
                    "loop is already stopped at T_stc_boil; the lock-out is for the idle (stagnating) collector"
                )

    def _apply_legacy_parameters(self) -> None:
        """Map the deprecated parameters onto the current ones (once)."""
        defaults = {f.name: f.default for f in fields(self)}
        if self.A_stc_pipe is not None:
            warnings.warn(
                "A_stc_pipe is deprecated: the collector area is A_stc (it is now used for absorption and loss).",
                DeprecationWarning,
                stacklevel=4,
            )
            if self.A_stc != defaults["A_stc"] and self.A_stc != self.A_stc_pipe:
                raise ValueError(f"give the collector area once: A_stc={self.A_stc}, A_stc_pipe={self.A_stc_pipe}")
            self.A_stc = float(self.A_stc_pipe)
            self.A_stc_pipe = None
        if self.alpha_stc is not None:
            warnings.warn(
                "alpha_stc is deprecated: the collector uses the ISO 9806 zero-loss efficiency eta0_stc "
                "(absorptance x cover transmittance x efficiency factor); alpha_stc is taken as eta0_stc.",
                DeprecationWarning,
                stacklevel=4,
            )
            if self.eta0_stc != defaults["eta0_stc"] and self.eta0_stc != self.alpha_stc:
                raise ValueError("give either eta0_stc or the deprecated alpha_stc, not both")
            self.eta0_stc = float(self.alpha_stc)
            self.alpha_stc = None
        given = {k: getattr(self, k) for k in _LEGACY_CONSTRUCTION_DEFAULTS if getattr(self, k) is not None}
        if given:
            warnings.warn(
                f"{sorted(given)} are deprecated: the collector uses the ISO 9806 loss coefficients "
                "a1_stc / a2_stc; the construction is converted to a1_stc (a2_stc = 0).",
                DeprecationWarning,
                stacklevel=4,
            )
            if self.a1_stc != defaults["a1_stc"] or self.a2_stc != defaults["a2_stc"]:
                raise ValueError("give either a1_stc/a2_stc or the deprecated construction parameters, not both")
            c = {**_LEGACY_CONSTRUCTION_DEFAULTS, **given}
            self.a1_stc = _construction_loss_coefficient(
                c["h_o_stc"], c["h_r_stc"], c["k_ins_stc"], c["x_air_stc"], c["x_ins_stc"]
            )
            self.a2_stc = 0.0
            for k in given:
                setattr(self, k, None)

    def _surface(self) -> tuple[float, float]:
        return self.stc_tilt, self.stc_azimuth

    def _beam_b0(self) -> float:
        return float(self.b0_stc)

    # ----------------------------------------------------------
    # Physics helpers
    # ----------------------------------------------------------

    @property
    def loop_flow(self) -> float:
        """Collector-loop flow of the pumped loops [m³/s] (``dV_stc_w`` or the ISO test flow)."""
        if self.dV_stc_w is not None:
            return float(self.dV_stc_w)
        return self.A_stc * STC_SPECIFIC_FLOW_M3S_PER_M2

    def calc_overall_heat_transfer_coeff(self) -> float:
        """Linear heat-loss coefficient ``a1_stc`` [W/(m²·K)] (kept for the former API)."""
        return float(self.a1_stc)

    def incidence_modifiers(self, aoi_deg: float) -> tuple[float, float, float]:
        """Beam, sky-diffuse and ground-diffuse incidence-angle modifiers [-]."""
        K_b = ashrae_iam(aoi_deg, self.b0_stc)
        if self.K_d_stc is not None:
            return K_b, float(self.K_d_stc), float(self.K_d_stc)
        K_sky, K_gnd = diffuse_iam(self.stc_tilt, self.b0_stc)
        return K_b, K_sky, K_gnd

    def absorbed_irradiance(self, plane: dict) -> float:
        """Irradiance the absorber turns into heat, ``η0·(K_b·G_b + K_d·G_d)`` [W/m²]."""
        K_b, K_sky, K_gnd = self.incidence_modifiers(plane["aoi"])
        # A plane from plane_irradiance carries the beam already modified sample by sample.
        G_b = plane["beam_iam"] if "beam_iam" in plane else K_b * plane["beam"]
        return float(self.eta0_stc * (G_b + K_sky * plane["sky"] + K_gnd * plane["ground"]))

    def stagnation_temperature_K(self, plane: dict, T0_K: float) -> float:
        """Absorber temperature with no flow [K] (all absorbed heat lost)."""
        S = self.absorbed_irradiance(plane)
        if self.a2_stc > 0:
            y = 2.0 * S / (self.a1_stc + np.sqrt(self.a1_stc**2 + 4.0 * self.a2_stc * S))
        elif self.a1_stc > 0:
            y = S / self.a1_stc
        else:
            y = np.inf
        return float(T0_K + y)

    def _resolve_plane(self, I_DN: float, I_dH: float, time_s: float | None, plane: dict | None) -> dict:
        if plane is not None:
            return plane
        if time_s is None:
            raise TypeError(
                "SolarThermalCollector.calc_performance needs the time to place the sun: pass time_s "
                "(seconds after start_time) or plane=collector.plane_irradiance(I_DN, I_dH, time_s)."
            )
        return self.plane_irradiance(I_DN, I_dH, time_s)

    # ----------------------------------------------------------
    # Main performance calculation
    # ----------------------------------------------------------

    def calc_performance(
        self,
        I_DN_stc: float,
        I_dH_stc: float,
        T_stc_w_in_K: float,
        T0_K: float,
        dV_stc: float | None = None,
        is_active: bool = True,
        *,
        time_s: float | None = None,
        plane: dict | None = None,
    ) -> dict:
        """Compute STC thermal performance for one timestep.

        Parameters
        ----------
        I_DN_stc : float
            Direct-normal irradiance [W/m²].
        I_dH_stc : float
            Diffuse-horizontal irradiance [W/m²].
        T_stc_w_in_K : float
            Inlet water temperature [K].
        T0_K : float
            Outdoor (ambient and dead-state) temperature [K].
        dV_stc : float | None
            Flow through the collector [m³/s]; defaults to :attr:`loop_flow`.
        is_active : bool
            If ``False`` (no flow), the flow terms are NaN; the plane
            irradiance and the stagnation temperature are still reported.
        time_s : float | None
            Time after ``start_time`` [s] at which the sun is placed.
        plane : dict | None
            Precomputed :meth:`plane_irradiance` (takes precedence over
            ``time_s``; the scenario classes compute it once per step).

        Returns
        -------
        dict
            ``I_sol_stc`` (irradiance on the collector plane [W/m²]),
            ``Q_sol_stc`` (absorbed), ``S_sol_stc``, ``X_sol_stc``,
            ``Q_stc_w_in``, ``Q_stc_w_out``, ``Q_stc_pump_w_out``,
            ``T_stc_w_out_K``, ``T_stc_pump_w_out_K``, ``T_stc_w_in_K``,
            ``T_stc_K`` (mean fluid temperature), ``Q_l_stc``, ``eta_stc``,
            ``aoi_stc``, ``T_stc_stag_K`` and the plane components
            ``G_stc_beam`` / ``G_stc_sky`` / ``G_stc_ground``.
        """
        if dV_stc is None:
            dV_stc = self.loop_flow
        p = self._resolve_plane(I_DN_stc, I_dH_stc, time_s, plane)
        G_T = float(p["total"])
        common = {
            "I_sol_stc": G_T,
            "aoi_stc": float(p["aoi"]),
            "G_stc_beam": float(p["beam"]),
            "G_stc_sky": float(p["sky"]),
            "G_stc_ground": float(p["ground"]),
            "T_stc_stag_K": self.stagnation_temperature_K(p, T0_K),
        }

        if not is_active or dV_stc <= 0:
            return {
                **common,
                "Q_sol_stc": np.nan,
                "S_sol_stc": np.nan,
                "X_sol_stc": np.nan,
                "Q_stc_w_in": np.nan,
                "Q_stc_w_out": np.nan,
                "Q_stc_pump_w_out": np.nan,
                "ksi_stc": np.nan,
                "T_stc_pump_w_out_K": T_stc_w_in_K,
                "T_stc_w_out_K": T_stc_w_in_K,
                "T_stc_w_in_K": T_stc_w_in_K,
                "T_stc_K": np.nan,
                "Q_l_stc": np.nan,
                "eta_stc": np.nan,
            }

        A = self.A_stc
        S = self.absorbed_irradiance(p)
        Q_sol_stc = A * S
        # Radiation entropy of the incident beam and diffuse parts, carried in
        # proportion to the absorbed share (as the PV array does with alp_pv).
        s_in = k_D * p["beam"] ** 0.9 + k_d * (p["sky"] + p["ground"]) ** 0.9
        S_sol_stc = Q_sol_stc * s_in / G_T if G_T > 0 else 0.0
        X_sol_stc = Q_sol_stc - S_sol_stc * T0_K

        G_stc = c_w * rho_w * dV_stc
        x = T_stc_w_in_K - T0_K
        b = 2.0 * G_stc + A * self.a1_stc
        c = A * S + 2.0 * G_stc * x
        # Mean-temperature form: A·a2·y|y| + b·y − c = 0 with y = T_m − T_a.
        y = 2.0 * c / (b + np.sqrt(b * b + 4.0 * A * self.a2_stc * abs(c)))
        T_out_K = 2.0 * (T0_K + y) - T_stc_w_in_K
        Q_l_stc = A * (self.a1_stc * y + self.a2_stc * y * abs(y))

        T_stc_pump_w_out_K = T_out_K + self.E_stc_pump / G_stc
        Q_stc_w_in = calc_energy_flow(G_stc, T_stc_w_in_K, T0_K)
        Q_stc_w_out = calc_energy_flow(G_stc, T_out_K, T0_K)
        Q_stc_pump_w_out = calc_energy_flow(G_stc, T_stc_pump_w_out_K, T0_K)

        return {
            **common,
            "Q_sol_stc": Q_sol_stc,
            "S_sol_stc": S_sol_stc,
            "X_sol_stc": X_sol_stc,
            "Q_stc_w_in": Q_stc_w_in,
            "Q_stc_w_out": Q_stc_w_out,
            "Q_stc_pump_w_out": Q_stc_pump_w_out,
            "ksi_stc": np.nan,
            "T_stc_pump_w_out_K": T_stc_pump_w_out_K,
            "T_stc_w_out_K": T_out_K,
            "T_stc_w_in_K": T_stc_w_in_K,
            "T_stc_K": T0_K + y,
            "Q_l_stc": Q_l_stc,
            "eta_stc": (Q_stc_w_out - Q_stc_w_in) / (A * G_T) if G_T > 0 else np.nan,
        }

    # ----------------------------------------------------------
    # Schedule helper
    # ----------------------------------------------------------

    def is_preheat_on(self, hour_of_day: float) -> bool:
        """Check whether *hour_of_day* falls in the window.

        Parameters
        ----------
        hour_of_day : float
            Hour within the day (0–24).

        Returns
        -------
        bool
        """
        return self.preheat_start_hour <= hour_of_day < self.preheat_end_hour


# ------------------------------------------------------------------
# Photovoltaic System


@dataclass
class PhotovoltaicSystem(_SunFacingSurface):
    """Photovoltaic System (PV + Charge Controller) — pure physics engine.

    Computes PV energy generation from irradiance inputs.  This class
    is a **stateless physics calculator**: given physical inputs it
    returns a performance dict.  All routing logic (ESS charge/discharge,
    Grid import, dump) is the responsibility of the scenario class.

    The array sees the irradiance on its own plane (``A_pv``, ``pv_tilt``,
    ``pv_azimuth``), transposed from ``I_DN`` / ``I_dH`` with the sun position
    of the step (:meth:`plane_irradiance`; site and clock as for
    :class:`SolarThermalCollector`), less the reflection at the module glass
    (ASHRAE incidence-angle modifier ``b0_pv``, 0.05 as pvlib's default).

    The public API:

    - :meth:`calc_performance` — single-timestep PV generation
    """

    # Panel physics
    A_pv: float = 5.0
    alp_pv: float = 0.9
    pv_tilt: float = 35.0
    pv_azimuth: float = 180.0
    h_o: float = 15.0

    # Efficiencies
    eta_pv: float = 0.15
    beta_pv: float = 0.0045
    T_ref_pv_K: float = 298.15
    eta_ctrl: float = 0.95
    T_ctrl_K: float = 308.15

    # Optics, site and clock
    b0_pv: float = 0.05
    latitude: float = DEFAULT_LATITUDE
    longitude: float = DEFAULT_LONGITUDE
    utc_offset_h: float = DEFAULT_UTC_OFFSET_H
    start_time: object = None
    albedo: float = 0.2
    sky_model: str = "perez"

    def __post_init__(self) -> None:
        validate_ctor("PV", vars(self))
        self._check_site()

    def _surface(self) -> tuple[float, float]:
        return self.pv_tilt, self.pv_azimuth

    def _beam_b0(self) -> float:
        return float(self.b0_pv)

    def calc_performance(
        self,
        I_DN: float,
        I_dH: float,
        T0_K: float,
        *,
        time_s: float | None = None,
        plane: dict | None = None,
    ) -> dict:
        """Compute PV generation for one timestep.

        Parameters
        ----------
        I_DN : float
            Direct-normal irradiance [W/m²].
        I_dH : float
            Diffuse-horizontal irradiance [W/m²].
        T0_K : float
            Dead-state (ambient) temperature [K].
        time_s : float | None
            Time after ``start_time`` [s] at which the sun is placed.
        plane : dict | None
            Precomputed :meth:`plane_irradiance` (takes precedence).

        Returns
        -------
        dict
            Keys: ``I_sol_pv`` (irradiance on the array plane), ``I_eff_pv``
            (after the incidence-angle modifier), ``T_pv_K``,
            ``eta_pv``, ``E_pv_out``, ``E_ctrl_out``,
            ``Q_l_pv``, ``Q_l_ctrl``, ``X_sol``, ``X_pv_out``,
            ``X_ctrl_out``, ``X_c_pv``, ``X_c_ctrl``,
            ``X_l_pv``, ``X_l_ctrl``.
        """
        if plane is None:
            if time_s is None:
                raise TypeError(
                    "PhotovoltaicSystem.calc_performance needs the time to place the sun: pass time_s "
                    "(seconds after start_time) or plane=pv.plane_irradiance(I_DN, I_dH, time_s)."
                )
            plane = self.plane_irradiance(I_DN, I_dH, time_s)
        T0 = max(1e-3, T0_K)
        K_sky, K_gnd = diffuse_iam(self.pv_tilt, self.b0_pv)
        # A plane from plane_irradiance carries the beam already modified sample by sample.
        G_b = plane["beam_iam"] if "beam_iam" in plane else ashrae_iam(plane["aoi"], self.b0_pv) * plane["beam"]
        G_d = K_sky * plane["sky"] + K_gnd * plane["ground"]
        I_sol = G_b + G_d

        # ── PV Cell ──────────────────────────────────────────────
        T_pv_K_approx = T0 + (I_sol * (self.alp_pv - self.eta_pv)) / (2.0 * self.h_o)

        eta_pv_actual = self.eta_pv * (1.0 - self.beta_pv * (T_pv_K_approx - self.T_ref_pv_K))
        eta_pv_actual = max(0.0, min(self.alp_pv, eta_pv_actual))

        T_pv_K = T0 + (I_sol * (self.alp_pv - eta_pv_actual)) / (2.0 * self.h_o)
        E_pv_out = self.A_pv * eta_pv_actual * I_sol
        Q_l_pv = 2.0 * self.A_pv * self.h_o * (T_pv_K - T0)

        s_sol = k_D * (G_b**0.9) + k_d * (G_d**0.9)
        S_sol = self.A_pv * self.alp_pv * s_sol
        S_l_pv = (1.0 / T_pv_K) * Q_l_pv if T_pv_K > 0 else 0.0
        S_g_pv = S_l_pv - S_sol  # S_pv_out = 0 (electricity)

        X_sol = self.A_pv * self.alp_pv * (I_sol - s_sol * T0)
        X_pv_out = E_pv_out
        X_l_pv = (1.0 - T0 / T_pv_K) * Q_l_pv if T_pv_K > 0 else 0.0
        X_c_pv = S_g_pv * T0

        # ── Charge Controller ─────────────────────────────────────
        E_ctrl_out = self.eta_ctrl * E_pv_out
        Q_l_ctrl = (1.0 - self.eta_ctrl) * E_pv_out
        S_l_ctrl = Q_l_ctrl / self.T_ctrl_K
        S_g_ctrl = S_l_ctrl  # S_ctrl_out=0, S_in=0 (electricity)

        X_ctrl_out = E_ctrl_out
        X_l_ctrl = Q_l_ctrl - S_l_ctrl * T0
        X_c_ctrl = S_g_ctrl * T0

        return {
            "I_sol_pv": float(plane["total"]),
            "I_eff_pv": I_sol,
            "T_pv_K": T_pv_K,
            "eta_pv": eta_pv_actual,
            "E_pv_out": E_pv_out,
            "E_ctrl_out": E_ctrl_out,
            "Q_l_pv": Q_l_pv,
            "Q_l_ctrl": Q_l_ctrl,
            "X_sol": X_sol,
            "X_pv_out": X_pv_out,
            "X_ctrl_out": X_ctrl_out,
            "X_c_pv": X_c_pv,
            "X_c_ctrl": X_c_ctrl,
            "X_l_pv": X_l_pv,
            "X_l_ctrl": X_l_ctrl,
        }


# ------------------------------------------------------------------
# Energy Storage System
# ------------------------------------------------------------------


@dataclass
class EnergyStorageSystem:
    """Energy Storage System (Battery) — pure physics engine.

    Accepts charging / discharging *requests* (DC power, [W]) and
    enforces capacity and SOC limits.  Internal losses and exergy
    destruction are computed from round-trip efficiencies.

    State (``SOC_ess``) is updated in-place at each call to
    :meth:`charge` or :meth:`discharge`.  Routing decisions (which
    request to fulfil and in what order) are entirely the
    responsibility of the scenario class.

    The state is part of the *run*, not of the model: the scenario classes
    call :meth:`reset` when a run starts, so a second ``analyze_dynamic`` on
    the same model starts from ``SOC_init`` again.  Do not share one instance
    between models that are stepped at the same time.

    Energy bookkeeping per call (``E`` are DC powers at the battery
    terminals, ``C = C_ess_max``)::

        charge:     C·ΔSOC/dt =  η_chg · E_ess_chg
        discharge:  C·ΔSOC/dt = -E_ess_dis / η_dis
        Q_l_ess     = (1 - η_chg)·E_ess_chg + (1/η_dis - 1)·E_ess_dis

    so ``E_ess_chg - E_ess_dis = C·ΔSOC/dt + Q_l_ess``.  A state of charge
    outside ``[SOC_min, SOC_max]`` is never moved *toward* the band by a
    call (a battery that starts below ``SOC_min`` cannot discharge, but it is
    not topped up for free either).

    Parameters
    ----------
    C_ess_max : float
        Rated energy capacity [J].  Default 3.6 MJ (= 1 kWh).
    SOC_init : float
        Initial state of charge [–].
    SOC_min : float
        Minimum allowable SOC [–] (depth-of-discharge guard).
    SOC_max : float
        Maximum allowable SOC [–].
    eta_ess_chg : float
        Charging efficiency (electricity-in to stored) [–].
    eta_ess_dis : float
        Discharging efficiency (stored to electricity-out) [–].
    T_ess_K : float
        Representative battery temperature used for entropy calc [K].
    """

    C_ess_max: float = 3.6e6
    SOC_init: float = 0.0
    SOC_min: float = 0.10
    SOC_max: float = 1.00
    eta_ess_chg: float = 0.90
    eta_ess_dis: float = 0.90
    T_ess_K: float = 313.15

    SOC_ess: float = field(init=False)

    def __post_init__(self) -> None:
        validate_ctor("ESS", vars(self))
        self.SOC_ess = self.SOC_init

    # ── helpers ──────────────────────────────────────────────────

    def _max_chg_power(self, dt: float) -> float:
        """Maximum charging power limited by remaining capacity [W]."""
        headroom = (self.SOC_max - self.SOC_ess) * self.C_ess_max
        return max(0.0, headroom / (self.eta_ess_chg * dt)) if dt > 0 else 0.0

    def _max_dis_power(self, dt: float) -> float:
        """Maximum discharge power limited by SOC_min [W]."""
        available = (self.SOC_ess - self.SOC_min) * self.C_ess_max
        return max(0.0, available * self.eta_ess_dis / dt) if dt > 0 else 0.0

    def _exergy(self, E_chg: float, E_dis: float, T0_K: float) -> dict:
        T0 = max(1e-3, T0_K)
        Q_l = (1.0 - self.eta_ess_chg) * E_chg + (1.0 / self.eta_ess_dis - 1.0) * E_dis
        S_l = Q_l / self.T_ess_K
        X_l_ess = Q_l - S_l * T0
        X_c_ess = S_l * T0
        return {"Q_l_ess": Q_l, "X_l_ess": X_l_ess, "X_c_ess": X_c_ess}

    # ── public API ───────────────────────────────────────────────

    def reset(self) -> None:
        """Restore the state of charge to ``SOC_init`` (start of a run)."""
        self.SOC_ess = self.SOC_init

    def charge(self, E_req_chg: float, dt: float, T0_K: float) -> dict:
        """Request to charge the ESS with *E_req_chg* [W] for *dt* [s].

        Returns a dict with keys ``E_ess_chg``, ``E_ess_dis``, ``SOC_ess``
        plus ``Q_l_ess`` (charging loss [W]) and exergy keys.
        """
        E_chg = min(max(E_req_chg, 0.0), self._max_chg_power(dt))
        soc = self.SOC_ess + E_chg * self.eta_ess_chg * dt / self.C_ess_max
        # Round-off guard only: never pull a state that is already above
        # SOC_max down (``E_chg`` is 0 there), and never exceed it by round-off.
        self.SOC_ess = min(soc, max(self.SOC_max, self.SOC_ess))
        return {
            "E_ess_chg": E_chg,
            "E_ess_dis": 0.0,
            "SOC_ess": self.SOC_ess,
            **self._exergy(E_chg, 0.0, T0_K),
        }

    def discharge(self, E_req_dis: float, dt: float, T0_K: float) -> dict:
        """Request to discharge *E_req_dis* [W] from the ESS for *dt* [s].

        Returns a dict with keys ``E_ess_dis`` (actual), ``E_ess_chg``,
        ``SOC_ess`` plus ``Q_l_ess`` (discharging loss [W]) and exergy keys.
        """
        E_dis = min(max(E_req_dis, 0.0), self._max_dis_power(dt))
        soc = self.SOC_ess - E_dis / self.eta_ess_dis * dt / self.C_ess_max
        # Round-off guard only: a state below SOC_min stays where it is
        # (E_dis is 0 there) instead of being raised to SOC_min for free.
        self.SOC_ess = max(soc, min(self.SOC_min, self.SOC_ess))
        return {
            "E_ess_chg": 0.0,
            "E_ess_dis": E_dis,
            "SOC_ess": self.SOC_ess,
            **self._exergy(0.0, E_dis, T0_K),
        }


# ------------------------------------------------------------------
# PV → inverter / ESS / dump / grid routing (one step)
# ------------------------------------------------------------------


def route_pv_ess(
    pv_result: dict,
    ess: EnergyStorageSystem,
    E_load: float,
    eta_inv: float,
    dt: float,
    T0_K: float,
) -> dict:
    """Route one step of PV power to the load, the battery, the dump and the grid.

    Shared by :class:`~tmhp.ASHPB_PV_ESS` and :class:`~tmhp.GSHPB_PV_ESS`.
    All routing logic lives here; the PV, ESS and inverter are physics only.

    The system has no grid export: DC power that neither the load nor the
    battery can take is **dumped** (``E_dump``).

    Parameters
    ----------
    pv_result : dict
        Output of :meth:`PhotovoltaicSystem.calc_performance`.
    ess : EnergyStorageSystem
        Battery; its state of charge is advanced by this call.
    E_load : float
        AC electric load of the system this step [W] (``E_tot``).
    eta_inv : float
        Inverter DC→AC efficiency, ``0 < eta_inv <= 1``.
    dt : float
        Time step [s].
    T0_K : float
        Dead-state temperature [K].

    Returns
    -------
    dict
        Powers [W] — DC unless noted:
        ``E_pv_to_inv`` (PV power sent to the inverter),
        ``E_ess_chg`` / ``E_ess_dis`` (battery terminals),
        ``E_dc_to_inv`` (``E_pv_to_inv + E_ess_dis``),
        ``E_inv_out`` (AC out of the inverter, ``eta_inv * E_dc_to_inv``),
        ``E_grid_import`` (AC), ``E_dump``,
        losses ``Q_l_ctrl``, ``Q_l_inv``, ``Q_l_ess`` and ``ess_result``
        (the raw dict of the battery call).

    Notes
    -----
    Two balances close every step (to round-off)::

        E_pv_out  = Q_l_ctrl + E_pv_to_inv + E_ess_chg + E_dump
        E_load    = E_inv_out + E_grid_import
        E_ess_chg - E_ess_dis = C_ess_max·ΔSOC/dt + Q_l_ess

    and together::

        E_pv_out + E_grid_import = E_load + Q_l_ctrl + Q_l_inv + Q_l_ess
                                   + C_ess_max·ΔSOC/dt + E_dump

    ``eta_ctrl`` (charge controller) acts between ``E_pv_out`` and
    ``E_ctrl_out``; ``eta_inv`` acts on the DC power that reaches the inverter
    (PV direct plus battery discharge); ``eta_ess_chg`` / ``eta_ess_dis`` act
    inside the battery.
    """
    eta_inv = check_efficiency("eta_inv", eta_inv)
    E_ctrl_out: float = pv_result["E_ctrl_out"]
    E_load = max(float(E_load), 0.0)

    if E_load == 0.0:
        # Load off: all controller output goes to the battery, the rest is dumped.
        ess_r = ess.charge(E_ctrl_out, dt, T0_K)
        E_pv_to_inv = 0.0
        E_dis = 0.0
        E_dump = max(0.0, E_ctrl_out - ess_r["E_ess_chg"])
        E_dc_short = 0.0
    else:
        E_dc_req = E_load / eta_inv
        if E_ctrl_out >= E_dc_req:
            # PV covers the load; the excess charges the battery, the rest is dumped.
            E_pv_to_inv = E_dc_req
            E_dc_excess = E_ctrl_out - E_dc_req
            ess_r = ess.charge(E_dc_excess, dt, T0_K)
            E_dis = 0.0
            E_dump = max(0.0, E_dc_excess - ess_r["E_ess_chg"])
            E_dc_short = 0.0
        else:
            # PV deficit: the battery discharges, the grid supplies what is left.
            E_pv_to_inv = E_ctrl_out
            E_ess_needed = E_dc_req - E_ctrl_out
            ess_r = ess.discharge(E_ess_needed, dt, T0_K)
            E_dis = ess_r["E_ess_dis"]
            E_dump = 0.0
            # Exactly 0.0 when the battery delivers the whole request (no
            # round-off residue that a ratio could blow up).
            E_dc_short = E_ess_needed - E_dis

    E_dc_to_inv = E_pv_to_inv + E_dis
    E_inv_out = eta_inv * E_dc_to_inv
    E_grid_import = eta_inv * E_dc_short
    return {
        "E_pv_to_inv": E_pv_to_inv,
        "E_ess_chg": ess_r["E_ess_chg"],
        "E_ess_dis": E_dis,
        "E_dc_to_inv": E_dc_to_inv,
        "E_inv_out": E_inv_out,
        "E_grid_import": E_grid_import,
        "E_dump": E_dump,
        "Q_l_ctrl": pv_result["Q_l_ctrl"],
        "Q_l_inv": (1.0 - eta_inv) * E_dc_to_inv,
        "Q_l_ess": ess_r["Q_l_ess"],
        "ess_result": ess_r,
    }


def stc_result_columns(stc_result: dict, T_stc_w_in_K: float, dV_stc: float, fraction: float = 1.0) -> dict:
    """Energy columns of one collector evaluation, named as in ``GSHPB_STC_tank``.

    ``fraction`` is the share of the step the loop circulated
    (``stc_runtime_fraction [-]``); the flow terms are step averages
    (``fraction`` × running value) and the absorbed heat is the whole step's,
    so the collector balance ``Q_sol_stc = (Q_stc_w_out - Q_stc_w_in) + Q_l_stc``
    closes with the idle part of the step counted as loss (a stagnating
    collector loses all it absorbs). Temperatures are the running values.
    The flow terms are NaN while the collector is inactive.
    """
    f = float(fraction)
    T_out = stc_result.get("T_stc_w_out_K", np.nan)
    T_pump_out = stc_result.get("T_stc_pump_w_out_K", T_out)
    Q_sol = stc_result.get("Q_sol_stc", np.nan)
    Q_in = stc_result.get("Q_stc_w_in", np.nan)
    Q_out = stc_result.get("Q_stc_w_out", np.nan)
    T_stag = stc_result.get("T_stc_stag_K", np.nan)
    return {
        "I_sol_stc [W/m2]": stc_result.get("I_sol_stc", np.nan),
        "aoi_stc [deg]": stc_result.get("aoi_stc", np.nan),
        "Q_sol_stc [W]": Q_sol,
        "S_sol_stc [W/K]": stc_result.get("S_sol_stc", np.nan),
        "X_sol_stc [W]": stc_result.get("X_sol_stc", np.nan),
        "Q_stc_w_out [W]": f * Q_out,
        "Q_stc_pump_w_out [W]": f * stc_result.get("Q_stc_pump_w_out", np.nan),
        "Q_stc_w_in [W]": f * Q_in,
        "Q_l_stc [W]": Q_sol - f * (Q_out - Q_in),
        "eta_stc [-]": f * stc_result.get("eta_stc", np.nan),
        "stc_runtime_fraction [-]": f if np.isfinite(Q_sol) else 0.0,
        "dV_stc [m3/s]": f * dV_stc,
        "T_stc_w_out [°C]": cu.K2C(T_out) if not np.isnan(T_out) else np.nan,
        "T_stc_pump_w_out [°C]": cu.K2C(T_pump_out) if not np.isnan(T_pump_out) else np.nan,
        "T_stc_w_in [°C]": cu.K2C(T_stc_w_in_K),
        "T_stc [°C]": cu.K2C(stc_result.get("T_stc_K", np.nan)),
        "T_stc_stag [°C]": cu.K2C(T_stag) if np.isfinite(T_stag) else np.nan,
    }


def warn_if_uv_ignored(model: object) -> None:
    """Warn when a scenario that never steps its subsystems was given a UV lamp.

    The STC scenario classes drive their own collector and do not step the
    subsystems registered through the ``uv=`` argument of the base model, so the
    lamp neither draws power nor heats the tank there.  Say so instead of
    silently dropping it.
    """
    if "uv" in getattr(model, "_subsystems", {}):
        warnings.warn(
            f"{type(model).__name__} does not simulate the 'uv' lamp: its power and heat are "
            "not part of the results (E_tot excludes it). Use ASHPB_PV_ESS / GSHPB_PV_ESS or the "
            "base boiler classes for a UV lamp.",
            UserWarning,
            stacklevel=3,
        )


def pv_ess_result_columns(
    pv_result: dict,
    route: dict,
    eta_inv: float,
    T_inv_K: float,
    T0_K: float,
) -> dict:
    """Result-row columns of one PV/ESS/inverter routing step.

    Column meaning (all [W] unless noted):

    - ``E_pv_out`` PV array DC output; ``E_ctrl_out`` after the charge
      controller (``Q_l_ctrl`` is the difference).
    - ``E_pv_to_inv`` DC PV power sent to the inverter (self-consumption path).
    - ``E_ess_chg`` / ``E_ess_dis`` DC power into / out of the battery terminals;
      ``Q_l_ess`` battery charge + discharge loss; ``SOC_ess`` [-].
    - ``E_inv_out`` AC power out of the inverter (``eta_inv`` × DC in);
      ``Q_l_inv`` inverter loss; ``E_grid_import`` AC power from the grid.
      ``E_inv_out + E_grid_import`` is the AC load, i.e. ``E_tot``.
    - ``E_dump`` DC power neither the load nor the battery could take.  The model
      has no grid export: surplus PV is discarded, never sold.
    """
    ess_r = route["ess_result"]
    S_l_inv = route["Q_l_inv"] / T_inv_K
    return {
        "I_sol_pv [W/m2]": pv_result["I_sol_pv"],
        "T_pv [°C]": cu.K2C(pv_result["T_pv_K"]),
        "eta_pv [-]": pv_result.get("eta_pv", np.nan),
        "E_pv_out [W]": pv_result["E_pv_out"],
        "E_ctrl_out [W]": pv_result["E_ctrl_out"],
        "E_pv_to_inv [W]": route["E_pv_to_inv"],
        "X_sol [W]": pv_result["X_sol"],
        "X_pv_out [W]": pv_result["X_pv_out"],
        "X_ctrl_out [W]": pv_result["X_ctrl_out"],
        "X_c_pv [W]": pv_result["X_c_pv"],
        "X_c_ctrl [W]": pv_result["X_c_ctrl"],
        "X_l_pv [W]": pv_result["X_l_pv"],
        "X_l_ctrl [W]": pv_result["X_l_ctrl"],
        "Q_l_ctrl [W]": route["Q_l_ctrl"],
        "E_ess_chg [W]": route["E_ess_chg"],
        "E_ess_dis [W]": route["E_ess_dis"],
        "SOC_ess [-]": ess_r["SOC_ess"],
        "Q_l_ess [W]": route["Q_l_ess"],
        "X_c_ess [W]": ess_r["X_c_ess"],
        "X_l_ess [W]": ess_r["X_l_ess"],
        "E_inv_out [W]": route["E_inv_out"],
        "E_grid_import [W]": route["E_grid_import"],
        "E_dump [W]": route["E_dump"],
        "Q_l_inv [W]": route["Q_l_inv"],
        "X_c_inv [W]": S_l_inv * T0_K,
        "X_l_inv [W]": route["Q_l_inv"] - S_l_inv * T0_K,
    }


def add_uv_power_to_e_tot(row: dict) -> float:
    """Add the UV lamp power of a result row to its ``E_tot [W]``; return ``E_tot``.

    The UV subsystem reports ``E_uv [W]`` through ``assemble_results`` but the
    cycle assembly that sets ``E_tot [W]`` runs before it, so the lamp is added
    here.  ``E_tot`` is the electricity the system draws:
    compressor + fans/pumps + solar-loop pump + UV lamp.
    """
    e_tot = row.get("E_tot [W]", 0.0)
    e_tot = 0.0 if e_tot is None or np.isnan(e_tot) else float(e_tot)
    e_uv = row.get("E_uv [W]", 0.0)
    if e_uv is not None and not np.isnan(e_uv):
        e_tot += float(e_uv)
    row["E_tot [W]"] = e_tot
    return e_tot


def pv_ess_postprocess(df: pd.DataFrame) -> pd.DataFrame:
    """Add the system-level exergy and grid-COP columns of the PV + ESS scenarios.

    ``cop_grid [-]`` is useful heat per grid electricity.  It is NaN while the
    grid import is at or below :data:`GRID_IMPORT_FLOOR_W`: with (almost) no import
    the ratio is arbitrarily large and carries no information (an infinite COP is
    not a physical quantity), so it is reported as undefined rather than as a
    number like 4e11.
    """
    # System exergy input: Solar exergy + Grid (= pure electricity = exergy)
    df["X_tot_sys [W]"] = df["X_sol [W]"].fillna(0) + df["E_grid_import [W]"].fillna(0)
    if "X_uv [W]" in df.columns:
        df["X_tot_sys [W]"] += df["X_uv [W]"].fillna(0)

    # Total exergy destruction across system (all Xc_ components)
    df["Xc_sys_tot [W]"] = df.filter(regex=r"^Xc_").sum(axis=1)

    grid = df["E_grid_import [W]"]
    df["cop_grid [-]"] = df["Q_tank_w_out [W]"] / grid.where(grid > GRID_IMPORT_FLOOR_W)

    # System exergy efficiency
    df["ex_eff_sys [-]"] = (df["Xst_tank [W]"] + df["X_tank_w_out [W]"]) / df["X_tot_sys [W]"].replace(0, np.nan)
    return df


# ------------------------------------------------------------------
# UV Disinfection Lamp
# ------------------------------------------------------------------


@dataclass
class UVLamp:
    """UV disinfection lamp subsystem.

    The lamp switches on periodically (``num_switching`` times per
    ``period_sec``, each for ``exposure_sec``, evenly spaced). Each step
    reports the lamp's **step-average** power, the switching schedule
    integrated over the step (:func:`tmhp.uv_treatment.calc_uv_lamp_energy`),
    so the energy does not depend on the time step.

    Heat: ``heat_to_water_fraction`` of the lamp power ends up in the tank
    water. A low-pressure mercury lamp in a quartz sleeve inside the tank
    turns about a third of its input into 254 nm UV-C and the rest into heat
    and visible light; the water and the tank wall absorb all of it. Its
    ballast (electronic, about 90 % efficient) sits in the control box
    outside the tank, so the default is 0.9. Set 1.0 for a lamp and ballast
    both inside the tank boundary.

    Step protocol: ``E_subsystem`` is the lamp's electric power; the tank
    balance adds it as heat, and ``Q_contribution = -(1 - fraction)·E_uv`` takes
    off the part that leaves the tank boundary, so the tank gains
    ``fraction·E_uv`` once.

    Parameters
    ----------
    lamp_watts : float
        Rated lamp power [W].
    exposure_sec : float
        Duration of each on-cycle [s].
    num_switching : int
        Number of on-cycles per period.
    period_sec : float
        Switching period [s] (default 3 h = 10 800 s).
    heat_to_water_fraction : float
        Share of the lamp's electric power that heats the tank water [-].
    """

    lamp_watts: float = 0.0
    exposure_sec: float = 0.0
    num_switching: int = 1
    period_sec: float = 3 * cu.h2s
    heat_to_water_fraction: float = 0.9

    def __post_init__(self) -> None:
        validate_ctor("UV", vars(self))
        if self.num_switching * self.exposure_sec > self.period_sec:
            raise ValueError(
                f"num_switching * exposure_sec must not exceed period_sec, got "
                f"{self.num_switching} x {self.exposure_sec} s > {self.period_sec} s"
            )

    def step(
        self,
        ctx: StepContext,
        ctrl: ControlState,
        dt: float,
        T_tank_w_in_K: float,
    ) -> dict:
        """Step-average lamp power and the heat it gives the tank."""
        from .uv_treatment import calc_uv_lamp_energy

        E_uv: float = (
            calc_uv_lamp_energy(
                ctx.current_time_s,
                dt,
                self.period_sec,
                self.num_switching,
                self.exposure_sec,
                self.lamp_watts,
            )
            / dt
            if dt > 0
            else 0.0
        )
        return {
            "Q_contribution": -(1.0 - self.heat_to_water_fraction) * E_uv,
            "E_subsystem": E_uv,
            "T_tank_w_in_override_K": None,
            "E_uv": E_uv,
        }

    def assemble_results(
        self,
        ctx: StepContext,
        ctrl: ControlState,
        step_state: dict,
        T_solved_K: float,
    ) -> dict:
        """Report the step-average UV power and its heat to the tank."""
        E_uv: float = step_state.get("E_uv", 0.0)
        if E_uv > 0 or self.lamp_watts > 0:
            return {"E_uv [W]": E_uv, "Q_uv_tank [W]": self.heat_to_water_fraction * E_uv}
        return {}

    def calc_exergy(
        self,
        df: pd.DataFrame,
        T0_K: pd.Series,
    ) -> SubsystemExergy | None:
        """UV exergy = electricity (handled by E→X conversion).

        No additional post-processing needed.
        Returns ``None``.
        """
        return None
