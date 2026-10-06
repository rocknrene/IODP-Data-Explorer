"""Report types offered in the interface and their names in each archive.

The catalog follows the cross-program data-type table compiled by
L. B. Childress (Gulf Coast Repository, Texas A&M University; "SOD Data
Types", 2026), which aligns the data types of DSDP, ODP, and IODP under a
common generic name and data category and identifies the types to be
offered in this version of SOD Explorer. Types that the table marks as
excluded (biostratigraphy, core description, operations, X-ray methods)
or as undecided (paleomagnetism; major, minor, and trace elements) are not
offered.

Each :class:`ReportType` records, for one generic name,

* the DSDP source: a category of the DSDP Data Access application, or a
  PANGAEA search term where the table lists PANGAEA as the DSDP source, or
  nothing where DSDP did not make the measurement;
* the ODP report name, and the code of the data type in the file names of
  the NOAA NCEI archive (:mod:`sod_explorer.sources.ncei`); RGB has no
  ODP data type;
* the IODP LIMS reports, as candidate LORE report names (see below);
* a search term for PANGAEA (Chikyu and Mission-Specific Platform data).

LORE report names
-----------------
LORE identifies a report by a short internal name, listed by its menu
service (``/reference/NavHierarchyGet-LORE``). The names in
:data:`VERIFIED_LORE_NAMES` have been confirmed against LORE; most equal
the lower-case LIMS analysis code (exceptions: downhole temperature is
``dhtemp``, the penetrometer report ``penetrate``, and source rock analysis
``sranl``).

LORE assembles three reports in the browser from an *internal* report and
a report definition: Carbonates, Interstitial Water, and Gas Elements.
Each is assembled by this software from its internal report:
Carbonates by :func:`sod_explorer.sources.lore.assemble_carbonates`, and
Interstitial Water and Gas Elements by applying LORE's own report
definition (:func:`sod_explorer.sources.lore.assemble_composite`). Run ``python -m
sod_explorer.sources.check_lore`` to list which names resolve.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: LORE report names confirmed against LORE's header service.
VERIFIED_LORE_NAMES = frozenset({
    "gra", "mad", "pwl", "ngr", "tcon", "avs",          # verified in the original application
    "dhtemp", "pwc", "pwb", "rsc", "rgb", "tor",        # verified 2026-10-06 (live checks workflow)
    "penetrate", "sranl", "carbonates_internal",        # read from LORE's menu service, 2026-10-06
    "iw_internal", "gaselements_internal",
})


@dataclass(frozen=True)
class LoreReport:
    """One LIMS report as served by LORE.

    Attributes
    ----------
    code
        LIMS analysis code shown to the user (for example ``"PWC"``).
    candidates
        LORE report names to try, in order.
    transform
        For a composite report, the assembly applied to the rows of its
        internal report (see :mod:`sod_explorer.sources.lore`).
    """

    code: str
    candidates: tuple[str, ...]
    transform: str | None = None   # name of the assembly applied to a composite report


@dataclass(frozen=True)
class ReportType:
    """A data type under its generic cross-program name."""

    key: str
    label: str
    category: str
    dsdp_category: str | None = None      # DSDP Data Access application category
    dsdp_pangaea_term: str | None = None  # PANGAEA search term where DSDP data are on PANGAEA
    odp_report: str | None = None
    lore_reports: tuple[LoreReport, ...] = field(default_factory=tuple)
    pangaea_term: str | None = None
    ncei_codes: tuple[str, ...] = ()      # data-type codes in NCEI file names (ODP)


def _lore(code: str, *extra: str) -> LoreReport:
    """LORE report whose first candidate name is the lower-case analysis code."""
    return LoreReport(code, (code.lower(), *extra))


REPORT_TYPES: tuple[ReportType, ...] = (
    # Downhole
    ReportType("downhole_temperature", "Downhole temperature", "Downhole",
               odp_report="Downhole temperature",
               lore_reports=(LoreReport("APCT-3/SET", ("dhtemp",)),),
               pangaea_term="downhole temperature", ncei_codes=("temp",)),
    # Geochemistry
    ReportType("carbonates", "Carbonates", "Geochemistry",
               dsdp_category="carbonate and carbon", odp_report="Carbonates (CARB)",
               lore_reports=(LoreReport("CARB", ("carbonates_internal",), transform="carbonates"),),
               pangaea_term="carbonate", ncei_codes=("carb",)),
    ReportType("gas_elements", "Gas elements", "Geochemistry",
               odp_report="Gas Elements (GAS)",
               lore_reports=(LoreReport("GE", ("gaselements_internal",), transform="definition:gaselements"),),
               pangaea_term="headspace gas", ncei_codes=("gas",)),
    ReportType("interstitial_water", "Interstitial water", "Geochemistry",
               dsdp_category="interstitial water", odp_report="Interstitial Water (IW)",
               lore_reports=(LoreReport("IW", ("iw_internal",), transform="definition:iw"),),
               pangaea_term="interstitial water", ncei_codes=("iw",)),
    ReportType("source_rock", "Source rock analysis", "Geochemistry",
               dsdp_pangaea_term="Rock-Eval pyrolysis", odp_report="Rock Eval (RE/REVAL)",
               lore_reports=(LoreReport("SRA", ("sranl",)),), pangaea_term="Rock-Eval pyrolysis", ncei_codes=("re",)),
    # Physical properties
    ReportType("penetrometer", "Compressional strength (penetrometer)", "Physical properties",
               dsdp_pangaea_term="penetrometer", odp_report="Shear Strength (PEN)",
               lore_reports=(LoreReport("PEN", ("penetrate",)),), pangaea_term="penetrometer", ncei_codes=("pen",)),
    ReportType("gra", "Gamma ray attenuation bulk density", "Physical properties",
               dsdp_category="gamma ray attenuation", odp_report="Bulk density (GRA)",
               lore_reports=(_lore("GRA"),), pangaea_term="GRA bulk density", ncei_codes=("gra",)),
    ReportType("mad", "Moisture and density", "Physical properties",
               dsdp_category="density and porosity", odp_report="Moisture and Density (MAD)",
               lore_reports=(_lore("MAD"),), pangaea_term="moisture density", ncei_codes=("mad",)),
    ReportType("ngr", "Natural gamma radiation", "Physical properties",
               odp_report="Natural Gamma Radiation (NGR)",
               lore_reports=(_lore("NGR"),), pangaea_term="natural gamma radiation", ncei_codes=("ngr",)),
    ReportType("pwave_split", "P-wave velocity (split-core)", "Physical properties",
               dsdp_category="sonic velocity", odp_report="P-Wave Velocity (PWS, Split-Core System)",
               lore_reports=(_lore("PWC"), _lore("PWB")), pangaea_term="P-wave velocity",
               ncei_codes=("pws1", "pws2", "pws3")),
    ReportType("pwave_logger", "P-wave velocity (logger)", "Physical properties",
               dsdp_category="sonic velocity", odp_report="P-Wave Velocity (PWL, Whole-Core System)",
               lore_reports=(_lore("PWL"),), pangaea_term="P-wave velocity", ncei_codes=("pwl",)),
    ReportType("color_reflectance", "Color reflectance", "Physical properties",
               odp_report="Color Reflectance (RSC)",
               lore_reports=(_lore("RSC"),), pangaea_term="color reflectance", ncei_codes=("rsc",)),
    ReportType("rgb", "RGB", "Physical properties",
               odp_report="Digital Imaging RGB Channels (RGB)",
               lore_reports=(_lore("RGB"),), pangaea_term="RGB"),
    ReportType("shear_vane", "Shear strength (vane)", "Physical properties",
               dsdp_category="vane shear", odp_report="Shear Strength (AVS)",
               lore_reports=(_lore("AVS"),), pangaea_term="shear strength", ncei_codes=("avs",)),
    ReportType("shear_torvane", "Shear strength (torvane)", "Physical properties",
               dsdp_category="vane shear", odp_report="Shear Strength (TOR)",
               lore_reports=(_lore("TOR"),), pangaea_term="shear strength", ncei_codes=("tor",)),
    ReportType("thermal_conductivity", "Thermal conductivity", "Physical properties",
               dsdp_pangaea_term="thermal conductivity", odp_report="Thermal Conductivity (TCON)",
               lore_reports=(_lore("TCON"),), pangaea_term="thermal conductivity", ncei_codes=("tcon",)),
)

REPORTS: dict[str, ReportType] = {r.key: r for r in REPORT_TYPES}

#: Data categories in menu order.
CATEGORIES: tuple[str, ...] = tuple(dict.fromkeys(r.category for r in REPORT_TYPES))


def get_report(key: str) -> ReportType | None:
    """Report type for a key, or ``None`` if the key is unknown."""
    return REPORTS.get(key)


def report_label(key: str) -> str:
    """Display label for a report key."""
    report = REPORTS.get(key)
    return report.label if report else str(key)


def menu_options() -> list[dict]:
    """Dropdown options grouped by data category.

    Category names are included as disabled entries so that they act as
    headings within the menu.
    """
    options: list[dict] = []
    for category in CATEGORIES:
        options.append({"label": category.upper(), "value": f"__{category}", "disabled": True})
        options += [{"label": r.label, "value": r.key} for r in REPORT_TYPES if r.category == category]
    return options
