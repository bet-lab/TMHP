"""Per-source bookkeeping: what each source is, which family it feeds and on which power basis.

One row per source. ``power_basis`` says where the electrical power of the source was measured:

* ``terminal``        compressor terminals / inverter output (no drive loss in the power)
* ``inverter_input``  grid side of the inverter (drive loss included)
* ``line_fed``        fixed-speed motor on the mains (no inverter at all)
* ``both``            both measured (the terminal value is used)

``basis_status`` is ``stated`` when the source says so, ``assumed`` when it does not; an assumed basis is carried
into the uncertainty statement of the archive. ``origin`` is ``measured`` (calorimeter rows), ``polynomial``
(a published fit evaluated on a grid), ``software`` (manufacturer selection software) or ``catalogue`` (one printed
rating point per machine).

``role`` is what the rows do in the fit: ``shape`` (within-machine variation identifies the shape and the row's
unit gets a fitted level), ``level`` (one rating point per machine: only its level is read, after the shape is
fixed), ``check`` (compared with the model, never fitted) or ``excluded``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    source_id: str
    group: str  # leave-one-source-out group: BITZER / Copeland / AREP / literature / rating
    family: str  # scroll / rotary / none
    origin: str  # measured / polynomial / software / catalogue
    power_basis: str
    basis_status: str  # stated / assumed
    role: str
    evidence: str


SOURCES: tuple[Source, ...] = (
    Source(
        "bitzer",
        "BITZER",
        "scroll",
        "software",
        "terminal",
        "stated",
        "shape",
        "BITZER web software, ESC module, External FI: 'Current (280V)' at the FI output voltage and EST-420 treats "
        "FI losses as an extra on top (validation/bitzer_efficiency_pilot/metadata/extraction_conditions.md). Scroll "
        "results carry no 'Power consumption at frequency inverter inlet' note (the VARISPEED reciprocating rows do).",
    ),
    Source(
        "copeland_opi",
        "Copeland",
        "scroll",
        "polynomial",
        "inverter_input",
        "stated",
        "shape",
        "AHRI-540 coefficient records, Rt_Comment 'rating using EV... drive': power measured at the drive input "
        "(parse/copeland_opi.py docstring; P_includes_inverter=True on every row). Drive model not named.",
    ),
    Source(
        "arep_rpt11_ornl2013",
        "AREP",
        "scroll",
        "measured",
        "line_fed",
        "stated",
        "shape",
        "ORNL calorimeter, Yokogawa PR300 compressor power, line-fed 60 Hz fixed-speed motor (parse/arep.py).",
    ),
    Source(
        "ornl_tm_2013_414",
        "AREP",
        "scroll",
        "measured",
        "line_fed",
        "stated",
        "shape",
        "Same ZP21K5E scroll as AREP #11, separate test campaign; line-fed 60 Hz (parse/arep.py).",
    ),
    Source(
        "arep_rpt21_ornl2013",
        "AREP",
        "scroll",
        "measured",
        "line_fed",
        "stated",
        "shape",
        "ORNL calorimeter, 3.11 in3/rev scroll, R-404A, line-fed 60 Hz (parse/arep.py).",
    ),
    Source(
        "arep_rpt39_emerson2014",
        "AREP",
        "scroll",
        "measured",
        "line_fed",
        "stated",
        "shape",
        "Emerson calorimeter, ZP31K5E fixed speed, 'power = compressor input' wattmeter (parse/arep_rowlist.py).",
    ),
    Source(
        "arep_rpt58_emerson2016",
        "AREP",
        "scroll",
        "measured",
        "line_fed",
        "stated",
        "excluded",
        "Gas-cycle stand; the report prints the entering gas temperature equal to the dew point although it states "
        "20 F superheat, and Table 1 row 7 prints 12.8 psia for a 40 F dew point (printed_flag in "
        "parse/arep_rowlist.py): the suction state is not defined by the source.",
    ),
    Source(
        "cuevas_lebrun_2009",
        "literature",
        "scroll",
        "measured",
        "line_fed",
        "stated",
        "shape",
        "Table 2: 50 Hz from the network (no inverter); Table 3: inverter-fed, power at the inverter supply "
        "(parse/cuevas2009.py). Table 3 rows are converted with the inverter model (inverter_input basis).",
    ),
    Source(
        "guth_atakan_2023",
        "literature",
        "scroll",
        "polynomial",
        "inverter_input",
        "assumed",
        "shape",
        "Published efficiency functions; eta_comp is motor + mechanical and 'the frequency-converter loss is small "
        "and not separated' (parse/guth2023.py): read as power at the converter input.",
    ),
    Source(
        "dardenne_2015_ijr",
        "literature",
        "none",
        "measured",
        "inverter_input",
        "assumed",
        "excluded",
        "Vapour-injection scroll: out of scope for the single-stage forms (brief and DESIGN 9.1).",
    ),
    Source(
        "tello_oquendo_2017_ijr",
        "literature",
        "none",
        "measured",
        "line_fed",
        "assumed",
        "excluded",
        "Vapour-injection scroll: out of scope for the single-stage forms (brief and DESIGN 9.1).",
    ),
    Source(
        "arep_rpt26_hitachi2013",
        "AREP",
        "rotary",
        "measured",
        "line_fed",
        "stated",
        "shape",
        "Shanghai Hitachi 28 cc fixed-speed rotary, 2.5 hp single-phase 50 Hz motor, 'power = compressor input' "
        "(parse/arep26_rotary.py). Four measured standard points per refrigerant plus the report's polynomial at "
        "its tested conditions. The compressor carried a liquid-injection port for R-32 (use at map points not "
        "stated).",
    ),
    Source(
        "shao_2004",
        "literature",
        "rotary",
        "polynomial",
        "terminal",
        "assumed",
        "shape",
        "Manufacturer-curve polynomials of three inverter rotaries; 'whether the inverter is included is not "
        "stated' (parse/shao2004.py). Read as compressor input (manufacturer compressor curves); the alternative is "
        "reported as a sensitivity.",
    ),
    Source(
        "ossorio_2019_cnit",
        "literature",
        "rotary",
        "measured",
        "both",
        "stated",
        "shape",
        "E1 grid side and E2 inverter-compressor line both measured; the printed inverter efficiency E2/E1 converts "
        "the tabulated E1 to the terminal basis (parse/ossorio2019.py).",
    ),
    Source(
        "highly_catalogue_2024",
        "rating",
        "rotary",
        "catalogue",
        "terminal",
        "assumed",
        "check",
        "Highly R290 inverter rotary catalogue rating points: power basis not printed; compared with the model, "
        "not fitted.",
    ),
    Source(
        "rating_lg_sci_twin",
        "rating",
        "rotary",
        "catalogue",
        "terminal",
        "assumed",
        "level",
        "LG and Mitsubishi SCI inverter twin-rotary rating points at 60 Hz: basis not printed. Read as excluding the "
        "inverter: they sit at 0.96-1.00 of the BITZER level while the GMCC points, whose specification states "
        "'Rated Inverter Input Power', sit at 0.91-0.94 -- the gap is an inverter efficiency (DESIGN 10.3).",
    ),
    Source(
        "rating_fixed_single",
        "rating",
        "rotary",
        "catalogue",
        "line_fed",
        "assumed",
        "level",
        "LG and Panasonic fixed-speed single rotaries at 50 Hz: induction motor on the mains, so no inverter; the "
        "measurement point (terminals) is implied by the fixed-speed rating, not printed.",
    ),
)

BY_ID = {s.source_id: s for s in SOURCES}

LOSO_GROUPS = {
    "scroll": ("BITZER", "Copeland", "AREP", "literature"),
    # rotary: per source, because the 'literature' group holds both speed-shape sources
    "rotary": ("shao_2004", "ossorio_2019_cnit", "arep_rpt26_hitachi2013"),
}

# Physical machine for sources whose model names differ between reports of the same compressor.
MACHINE_ALIAS = {
    ("arep_rpt11_ornl2013", "Copeland ZP21K5E-PFV"): "Copeland ZP21K5E",
    ("ornl_tm_2013_414", "Copeland ZP21K5E-PFV"): "Copeland ZP21K5E",
    ("arep_rpt39_emerson2014", "Copeland ZP31K5E-PFV"): "Copeland ZP31K5E",
    ("arep_rpt58_emerson2016", "Copeland ZP31K5E-PFV"): "Copeland ZP31K5E",
}
