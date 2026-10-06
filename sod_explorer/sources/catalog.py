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
* the ODP report name (archived in Janus and at NOAA NCEI; not yet
  retrievable by this software);
* the IODP LIMS reports, as candidate LORE report names (see below);
* a search term for PANGAEA (Chikyu and Mission-Specific Platform data).

LORE report names
-----------------
LORE identifies a report by a short internal name. The names of six
reports (``gra``, ``mad``, ``pwl``, ``ngr``, ``tcon``, ``avs``) were
verified against LORE's header service; each equals the lower-case LIMS
analysis code. The remaining names could not be verified when this catalog
was written and are listed as ordered *candidates*, the analysis code
first. The LORE client requests the header of each candidate in turn and
uses the first that LORE recognizes
(:func:`sod_explorer.sources.lore.resolve_report_name`). Run
``python -m sod_explorer.sources.check_lore`` to list which candidates
resolve, and move the confirmed names into :data:`VERIFIED_LORE_NAMES`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: LORE report names confirmed against LORE's header service.
VERIFIED_LORE_NAMES = frozenset({"gra", "mad", "pwl", "ngr", "tcon", "avs"})


@dataclass(frozen=True)
class LoreReport:
    """One LIMS report as served by LORE.

    Attributes
    ----------
    code
        LIMS analysis code shown to the user (for example ``"PWC"``).
    candidates
        LORE report names to try, in order.
    """

    code: str
    candidates: tuple[str, ...]


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


def _lore(code: str, *extra: str) -> LoreReport:
    """LORE report whose first candidate name is the lower-case analysis code."""
    return LoreReport(code, (code.lower(), *extra))


REPORT_TYPES: tuple[ReportType, ...] = (
    # Downhole
    ReportType("downhole_temperature", "Downhole temperature", "Downhole",
               odp_report="Downhole temperature",
               lore_reports=(LoreReport("APCT-3/SET", ("apct", "apct3", "dhtemp", "downholetemp", "set")),),
               pangaea_term="downhole temperature"),
    # Geochemistry
    ReportType("carbonates", "Carbonates", "Geochemistry",
               dsdp_category="carbonate and carbon", odp_report="Carbonates (CARB)",
               lore_reports=(_lore("CARB"),), pangaea_term="carbonate"),
    ReportType("gas_elements", "Gas elements", "Geochemistry",
               odp_report="Gas Elements (GAS)",
               lore_reports=(LoreReport("GE", ("ge", "gaselements", "gas")),),
               pangaea_term="headspace gas"),
    ReportType("interstitial_water", "Interstitial water", "Geochemistry",
               dsdp_category="interstitial water", odp_report="Interstitial Water (IW)",
               lore_reports=(LoreReport("IW", ("iw", "iwreport", "interstitialwater")),),
               pangaea_term="interstitial water"),
    ReportType("source_rock", "Source rock analysis", "Geochemistry",
               dsdp_pangaea_term="Rock-Eval pyrolysis", odp_report="Rock Eval (RE/REVAL)",
               lore_reports=(_lore("SRA"),), pangaea_term="Rock-Eval pyrolysis"),
    # Physical properties
    ReportType("penetrometer", "Compressional strength (penetrometer)", "Physical properties",
               dsdp_pangaea_term="penetrometer", odp_report="Shear Strength (PEN)",
               lore_reports=(_lore("PEN"),), pangaea_term="penetrometer"),
    ReportType("gra", "Gamma ray attenuation bulk density", "Physical properties",
               dsdp_category="gamma ray attenuation", odp_report="Bulk density (GRA)",
               lore_reports=(_lore("GRA"),), pangaea_term="GRA bulk density"),
    ReportType("mad", "Moisture and density", "Physical properties",
               dsdp_category="density and porosity", odp_report="Moisture and Density (MAD)",
               lore_reports=(_lore("MAD"),), pangaea_term="moisture density"),
    ReportType("ngr", "Natural gamma radiation", "Physical properties",
               odp_report="Natural Gamma Radiation (NGR)",
               lore_reports=(_lore("NGR"),), pangaea_term="natural gamma radiation"),
    ReportType("pwave_split", "P-wave velocity (split-core)", "Physical properties",
               dsdp_category="sonic velocity", odp_report="P-Wave Velocity (PWS, Split-Core System)",
               lore_reports=(_lore("PWC"), _lore("PWB")), pangaea_term="P-wave velocity"),
    ReportType("pwave_logger", "P-wave velocity (logger)", "Physical properties",
               dsdp_category="sonic velocity", odp_report="P-Wave Velocity (PWL, Whole-Core System)",
               lore_reports=(_lore("PWL"),), pangaea_term="P-wave velocity"),
    ReportType("color_reflectance", "Color reflectance", "Physical properties",
               odp_report="Color Reflectance (RSC)",
               lore_reports=(_lore("RSC"),), pangaea_term="color reflectance"),
    ReportType("rgb", "RGB", "Physical properties",
               odp_report="Digital Imaging RGB Channels (RGB)",
               lore_reports=(_lore("RGB"),), pangaea_term="RGB"),
    ReportType("shear_vane", "Shear strength (vane)", "Physical properties",
               dsdp_category="vane shear", odp_report="Shear Strength (AVS)",
               lore_reports=(_lore("AVS"),), pangaea_term="shear strength"),
    ReportType("shear_torvane", "Shear strength (torvane)", "Physical properties",
               dsdp_category="vane shear", odp_report="Shear Strength (TOR)",
               lore_reports=(_lore("TOR"),), pangaea_term="shear strength"),
    ReportType("thermal_conductivity", "Thermal conductivity", "Physical properties",
               dsdp_pangaea_term="thermal conductivity", odp_report="Thermal Conductivity (TCON)",
               lore_reports=(_lore("TCON"),), pangaea_term="thermal conductivity"),
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
