"""Client for the NOAA NCEI archive of JOIDES Resolution core data.

NOAA's National Centers for Environmental Information (NCEI, formerly
NGDC) archive the shipboard core data of the Ocean Drilling Program (ODP,
Legs 101 to 210) as exported from the Janus database. The archive is the
primary shipboard data source for ODP listed in the *Scientific Ocean
Drilling Legacy Data Access: Quick Start Guide* (Childress, v1.0, 2026).

Archive layout
--------------
Data are stored by Leg and Hole, one tab-delimited text file per data
type::

    {base}/{leg}/{site}{hole}/{type}_{leg}_{site}{hole}.txt
    .../joides_resolution/204/1244c/carb_204_1244c.txt

Folder and file names are lower case. The first line of a file holds the
column names (``Leg``, ``Site``, ``Hole``, ``Core``, ``Type``, ``Section``,
``Top (cm)``, ``Bottom (cm)``, ``Depth (mbsf)``, then the measurements);
lines end with a trailing tab. Depths are in metres below seafloor (mbsf).
The layout was established from the archive's directory listings and from
the carbonate and moisture-and-density files of Leg 204, Hole 1244C
(R. Castillo, 2026-10-06). The archive also holds one file per data type
for all of ODP (``odp_all_{type}.txt.gz``, up to 177 MB); these are not
used.

Access
------
The archive is public. Its ``robots.txt`` excludes automated crawlers from
the data directories, so this client does not index or traverse the
archive: it requests only the file for a Hole that a user has asked for
(one request per Hole and data type), identifies itself in the
``User-Agent`` header, pauses between requests, and caches results so that
a file is not downloaded twice. The Holes of a Leg are taken from the
reference table (:mod:`sod_explorer.reference`), not from directory
listings.

Citation
--------
Ocean Drilling Program (2005): Archive of Core and Site/Hole Data and
Photographs from the Ocean Drilling Program (ODP). National Geophysical
Data Center, NOAA. doi:10.7289/V5W37T8C
"""

from __future__ import annotations

import io
import time
from functools import lru_cache

import pandas as pd
import requests

from ..provenance import utc_now
from ..reference import UNLETTERED_HOLE, holes_for_site, sites_for_expedition
from .catalog import get_report, report_label
from .common import USER_AGENT, SourceError, restrict_to_request, validate_identifier

NCEI_BASE = "https://www.ngdc.noaa.gov/mgg/geology/data/joides_resolution"
ODP_DOI = "10.7289/V5W37T8C"
ODP_CITATION = ("Ocean Drilling Program (2005): Archive of Core and Site/Hole Data and Photographs "
                "from the Ocean Drilling Program (ODP). National Geophysical Data Center, NOAA. "
                f"doi:{ODP_DOI}")
TIMEOUT_S = 60
PAUSE_S = 0.3            # pause between requests to the archive
MAX_HOLES = 60           # most Holes requested for one Leg-wide or Site-wide request
CACHE_SIZE = 256

#: Columns that every data file of the archive begins with.
_REQUIRED_COLUMNS = ("Leg", "Site", "Hole")


def file_url(code: str, leg: str, site: str, hole: str) -> str:
    """URL of the file of one data type for one Hole."""
    location = f"{site}{hole}".lower()
    return f"{NCEI_BASE}/{leg}/{location}/{code}_{leg}_{location}.txt"


def parse_data_file(text: str) -> pd.DataFrame:
    """Parse a tab-delimited data file of the archive.

    Column names are stripped of surrounding blanks, and the empty column
    produced by the trailing tab of each line is removed.

    Raises
    ------
    SourceError
        If the text is not a data file (for example, an HTML error page).
    """
    try:
        df = pd.read_csv(io.StringIO(text), sep="\t", dtype={"Site": str, "Hole": str})
    except (pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        raise SourceError(f"The NCEI file could not be parsed: {exc}") from exc
    df.columns = [str(c).strip() for c in df.columns]
    df = df.loc[:, [c for c in df.columns if not (c.startswith("Unnamed") and df[c].isna().all())]]
    if any(c not in df.columns for c in _REQUIRED_COLUMNS):
        raise SourceError("The NCEI response is not a data file (no Leg, Site, and Hole columns)")
    return df


@lru_cache(maxsize=CACHE_SIZE)
def _fetch_file(code: str, leg: str, site: str, hole: str) -> pd.DataFrame | None:
    """Download and parse one file; ``None`` if the archive has no such file.

    Results, including absences, are cached. Network failures raise and are
    therefore not cached.
    """
    url = file_url(code, leg, site, hole)
    try:
        response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_S)
    except requests.RequestException as exc:
        raise SourceError(f"Could not reach the NCEI archive: {exc}") from exc
    finally:
        time.sleep(PAUSE_S)
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise SourceError(f"The NCEI archive answered HTTP {response.status_code} for {url}")
    response.encoding = response.encoding or "utf-8"
    return parse_data_file(response.text)


def _requested_holes(leg: str, site: str, hole: str) -> list[tuple[str, str]]:
    """(Site, Hole) pairs to request, from the request or the reference table."""
    if site and hole:
        return [(site, hole)]
    sites = [site] if site else sites_for_expedition(leg)
    pairs = [(s, h) for s in sites for h in holes_for_site(leg, s) if h != UNLETTERED_HOLE]
    if not pairs:
        raise SourceError(f"The reference table lists no Holes for Leg {leg}"
                          + (f", Site {site}" if site else "") + "; select a Site and Hole.")
    if len(pairs) > MAX_HOLES:
        raise SourceError(f"Leg {leg} has {len(pairs)} Holes; select a Site to limit the request.")
    return pairs


def fetch(report_key: str, expedition: str, site: str = "", hole: str = "") -> tuple[pd.DataFrame, dict]:
    """Retrieve a report type for an ODP Leg from the NCEI archive.

    If no Hole is given, every Hole of the Site (or of the Leg) listed in
    the reference table is requested. A report type stored as several data
    types (split-core P-wave velocity: ``pws1``, ``pws2``, ``pws3``) is
    returned as one table with a leading ``NCEI data type`` column.

    Returns
    -------
    df : pandas.DataFrame
        Rows restricted to the requested Leg, Site, and Hole.
    source : dict
        Provenance source description, including the URL of every file used.

    Raises
    ------
    SourceError
        If the report type has no ODP data type, the archive is
        unreachable, or it holds no file for the request.
    """
    report_type = get_report(report_key)
    if report_type is None or not report_type.ncei_codes:
        raise SourceError(f"{report_label(report_key)} was not measured during ODP "
                          "(the NCEI archive has no such data type)")
    leg = validate_identifier(str(expedition or ""), "Leg")
    site = validate_identifier(site, "Site").upper()
    hole = validate_identifier("" if hole == UNLETTERED_HOLE else hole, "Hole").upper()

    tables, files = [], []
    for hole_site, hole_letter in _requested_holes(leg, site, hole):
        for code in report_type.ncei_codes:
            table = _fetch_file(code, leg, hole_site, hole_letter)
            if table is None or table.empty:
                continue
            table = table.copy()
            if len(report_type.ncei_codes) > 1:
                table.insert(0, "NCEI data type", code)
            tables.append(table)
            files.append({"url": file_url(code, leg, hole_site, hole_letter), "rows": int(len(table))})
    where = f"Leg {leg}" + (f", {site}{hole}" if site else "")
    if not tables:
        raise SourceError(f"The NCEI archive has no {report_label(report_key)} file for {where}")
    df = pd.concat(tables, ignore_index=True) if len(tables) > 1 else tables[0]
    df = restrict_to_request(df, leg, site, hole)
    if df.empty:
        raise SourceError(f"The NCEI files hold no {report_label(report_key)} rows for {where}")

    source = {
        "type": "ncei",
        "name": "NOAA NCEI archive of JOIDES Resolution core data",
        "url": f"{NCEI_BASE}/{leg}/",
        "doi": ODP_DOI,
        "query": {"report": report_key, "ncei_data_types": list(report_type.ncei_codes),
                  "leg": leg, "site": site, "hole": hole, "files": files},
        "retrieved_utc": utc_now(),
        "citation_note": ODP_CITATION,
    }
    return df, source
