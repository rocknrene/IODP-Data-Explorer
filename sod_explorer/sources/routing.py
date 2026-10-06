"""Selection of a data source from the drilling program and platform.

The rules follow the primary shipboard data sources listed in the
*Scientific Ocean Drilling Legacy Data Access: Quick Start Guide*
(Childress, v1.0, September 2026). For DSDP, the guide lists NOAA NCEI as
the primary archive and the DSDP Data Access application as its
alternative; the NCEI files are not currently downloadable, so the
alternative is used.

Routing rules
-------------
============================  ====================================================
Program / platform            Source
============================  ====================================================
DSDP (Glomar Challenger)      DSDP Data Access application; PANGAEA for the data
                              types whose DSDP source is PANGAEA (see catalog)
ODP, IODP (JOIDES Resolution) LORE
IODP (Chikyu)                 PANGAEA; otherwise J-CORES export via file upload
IODP (Mission-Specific)       PANGAEA
============================  ====================================================

The platform is taken from the reference table
(:mod:`sod_explorer.reference`). If a Leg is absent from the table, the
program is inferred from its number: DSDP and ODP numbers are routed as
above (ODP used only the JOIDES Resolution), and IODP numbers, whose
platform cannot be inferred, are tried against LORE and then PANGAEA.

LORE serves the LIMS database, which the JOIDES Resolution Science
Operator adopted in 2009 (Expedition 317 onward in this table's
numbering). Data from ODP Legs and from IODP expeditions 301 to 312
predate LIMS and are archived by NOAA NCEI; a reader for that archive is
not yet implemented, so such requests are directed to file upload when
LORE returns no data.

When PANGAEA returns exactly one screened candidate, it is downloaded
directly; when it returns several, they are offered to the user.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from ..reference import (
    UNLETTERED_HOLE,
    platform_for_expedition,
    program_for_expedition,
    program_from_number,
)
from . import lore, pangaea, shinylaurel
from .catalog import get_report, report_label
from .common import SourceError

#: First IODP expedition with data in the LIMS database.
FIRST_LIMS_EXPEDITION = 317


@dataclass
class Route:
    """Program and platform of a Leg/Expedition and the source to query."""

    expedition: str
    program: str | None
    platform: str | None
    strategy: str   # "dsdp", "jr", "chikyu", "msp", "iodp_unknown_platform", or "unknown"


@dataclass
class FetchOutcome:
    """Result of a routed request.

    Exactly one of three states holds: ``df`` is set (data retrieved),
    ``candidates`` is non-empty (the user must choose a PANGAEA dataset), or
    both are empty (nothing found; ``message`` explains why).
    """

    message: str
    df: pd.DataFrame | None = None
    source: dict | None = None
    candidates: list[dict] = field(default_factory=list)


def resolve_route(expedition: str) -> Route:
    """Determine the program, platform, and source strategy for a Leg."""
    expedition = str(expedition).strip()
    platform = platform_for_expedition(expedition)
    program = program_for_expedition(expedition) or program_from_number(expedition)
    if platform == "Glomar Challenger" or (platform is None and program == "DSDP"):
        strategy = "dsdp"
    elif platform == "JOIDES Resolution" or (platform is None and program == "ODP"):
        strategy = "jr"
    elif platform is None and program == "IODP":
        strategy = "iodp_unknown_platform"
    elif platform == "Chikyu":
        strategy = "chikyu"
    elif platform == "MSP":
        strategy = "msp"
    else:
        strategy = "unknown"
    return Route(expedition, program, platform, strategy)


def _predates_lims(expedition: str) -> bool:
    """True if a JOIDES Resolution Leg/Expedition number precedes the first LIMS expedition."""
    digits = "".join(ch for ch in expedition if ch.isdigit())
    return bool(digits) and int(digits) < FIRST_LIMS_EXPEDITION


def _from_pangaea(route: Route, report_key: str, platform_term: str, label: str,
                  keyword: str | None = None, unit: str = "Expedition") -> FetchOutcome:
    """Search PANGAEA for a report type; load a single match, or offer several to the user."""
    report_type = get_report(report_key)
    keyword = keyword or (report_type.pangaea_term if report_type else None)
    candidates = pangaea.find_datasets(route.expedition, keyword, platform_term, unit=unit)
    if not candidates:
        return FetchOutcome(f"No PANGAEA dataset matching {report_label(report_key)} was found for "
                            f"{unit} {route.expedition} ({label}).")
    if len(candidates) == 1:
        df, source = pangaea.fetch_dataset(candidates[0]["value"])
        return FetchOutcome(f"PANGAEA dataset {candidates[0]['value']}, {unit} {route.expedition} "
                            f"({len(df):,} rows)", df=df, source=source)
    return FetchOutcome(f"{len(candidates)} PANGAEA datasets match {unit} {route.expedition} "
                        f"({label}). Select one:", candidates=candidates)


def _from_dsdp(route: Route, report_key: str, site: str, hole: str) -> FetchOutcome:
    """Retrieve DSDP data from the source the catalog lists for the report type."""
    report_type = get_report(report_key)
    if report_type is not None and report_type.dsdp_category:
        df, source = shinylaurel.fetch(report_key, route.expedition, site, hole)
        return FetchOutcome(f"DSDP {source['query']['category']}, Leg {route.expedition} "
                            f"({len(df):,} rows)", df=df, source=source)
    if report_type is not None and report_type.dsdp_pangaea_term:
        return _from_pangaea(route, report_key, "DSDP", "DSDP", keyword=report_type.dsdp_pangaea_term,
                             unit="Leg")
    return FetchOutcome(f"{report_label(report_key)} has no DSDP equivalent: the data-type table "
                        "lists no DSDP data type for it.")


def fetch(report_key: str, expedition: str, site: str = "", hole: str = "") -> FetchOutcome:
    """Retrieve a report for a Leg/Expedition from the appropriate source.

    Parameters
    ----------
    report_key
        Report key (see :mod:`sod_explorer.sources.catalog`).
    expedition
        Leg or Expedition identifier.
    site, hole
        Optional Site and Hole. The unlettered-hole placeholder ``*`` is
        not sent to services as a filter.

    Returns
    -------
    FetchOutcome
    """
    route = resolve_route(expedition)
    site = site or ""
    service_hole = "" if hole == UNLETTERED_HOLE else (hole or "")
    label = report_label(report_key)
    report_type = get_report(report_key)

    try:
        if route.strategy == "dsdp":
            return _from_dsdp(route, report_key, site, hole or "")

        if route.strategy == "jr":
            try:
                df, source = lore.fetch(report_key, route.expedition, site, service_hole)
            except SourceError as exc:
                if _predates_lims(route.expedition):
                    raise SourceError(
                        f"{exc}. {route.program} Leg {route.expedition} predates the LIMS database "
                        "served by LORE; these data are archived in Janus and at NOAA NCEI"
                        + (f" as '{report_type.odp_report}'" if report_type and report_type.odp_report else "")
                        + ". Use Local file upload.") from exc
                raise
            word = "Leg" if route.program == "ODP" else "Expedition"
            return FetchOutcome(f"LORE {label}, {word} {route.expedition} ({len(df):,} rows)",
                                df=df, source=source)

        if route.strategy == "chikyu":
            outcome = _from_pangaea(route, report_key, "Chikyu", "Chikyu")
            if outcome.df is None and not outcome.candidates:
                outcome.message += (" Chikyu data are distributed through J-CORES; download the "
                                    "bulk export from JAMSTEC and use Local file upload.")
            return outcome

        if route.strategy == "msp":
            return _from_pangaea(route, report_key, "", "Mission-Specific Platform")

        if route.strategy == "iodp_unknown_platform":
            try:
                df, source = lore.fetch(report_key, route.expedition, site, service_hole)
                return FetchOutcome(f"LORE {label}, Expedition {route.expedition} ({len(df):,} rows)",
                                    df=df, source=source)
            except SourceError as exc:
                outcome = _from_pangaea(route, report_key, "", "platform not in reference table")
                if outcome.df is None and not outcome.candidates:
                    outcome.message = (f"Expedition {route.expedition} is not in the reference table. "
                                       f"LORE: {exc}. PANGAEA: {outcome.message} Use Local file upload.")
                return outcome

    except SourceError as exc:
        return FetchOutcome(str(exc))

    return FetchOutcome(f"Leg/Expedition {expedition} is not in the reference table and its "
                        "program could not be determined. Use Local file upload.")
