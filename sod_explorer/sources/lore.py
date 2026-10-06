"""Client for the LIMS Online Report Environment (LORE).

LORE (https://web.iodp.tamu.edu/LORE/) is the public report interface to
the Laboratory Information Management System (LIMS) database of the
JOIDES Resolution Science Operator. The LORE web page is a template whose
tables are populated by three JSON services on the same host; this client
calls the same services:

1. ``/reference/HeaderDisplayGet-LORE``: column headers of the report. This
   call also establishes which LORE report name is valid (see
   :func:`resolve_report_name`).
2. ``/limsM/AWorkingSetGet-LORE``: identifiers of the tests that match the
   Expedition, Site, and Hole filters.
3. ``/limsM/DisplayGet-LORE``: result rows, requested in batches of test
   identifiers.

Public access uses LORE's guest credentials (``GUEST``/``guest``). Requests
are issued sequentially with a fixed pause, identify the client in the
User-Agent header, and back off exponentially on HTTP 429, 502, 503, and
504 responses (HTTP 500 is not retried, because LORE may answer an
unrecognized report name with it). Successful responses are cached in memory for the lifetime of
the process.

Depths are requested on LORE's default depth scale set (scale identifier
11331), which reports both CSF-A and CSF-B.
"""

from __future__ import annotations

import json
import time
from functools import lru_cache
from html import unescape

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..provenance import utc_now
from .catalog import LoreReport, get_report, report_label
from .common import USER_AGENT, SourceError, describe_request, restrict_to_request, validate_identifier

LORE_HOST = "https://web.iodp.tamu.edu"
LORE_PAGE = f"{LORE_HOST}/LORE/"
LORE_USER, LORE_PASSWORD = "GUEST", "guest"
LORE_SCALE_ID = "11331"
BATCH_SIZE = 200          # test identifiers per DisplayGet request
PAUSE_S = 0.5             # pause after each request
TIMEOUT_S = 60            # per-request timeout
CACHE_SIZE = 64           # number of distinct requests cached

_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": LORE_PAGE,
}


def _session() -> requests.Session:
    """HTTP session with retry and exponential backoff (2, 4, 8, 16 s).

    The LORE page is requested once so that any session cookies it sets
    accompany the data requests; failure of that request is not fatal.
    """
    session = requests.Session()
    session.headers.update(_HEADERS)
    retry = Retry(total=4, backoff_factor=2, status_forcelist=(429, 502, 503, 504),
                  allowed_methods=("GET",), respect_retry_after_header=True)
    session.mount("https://", HTTPAdapter(max_retries=retry))
    try:
        session.get(LORE_PAGE, timeout=TIMEOUT_S)
    except requests.RequestException:
        pass
    return session


def _get_json(session: requests.Session, path: str, params: dict):
    """Issue one GET request to a LORE service and decode the JSON body."""
    response = session.get(f"{LORE_HOST}{path}", params=params, timeout=TIMEOUT_S)
    time.sleep(PAUSE_S)
    if response.status_code == 403:
        raise SourceError("LORE refused the request (HTTP 403). Try again in a few "
                          "minutes, or use Local file upload.")
    response.raise_for_status()
    return response.json()


def rows_to_frame(rows: list[list], headers: list[str]) -> pd.DataFrame:
    """Convert LORE's list-of-lists rows to a table.

    Rows shorter than the widest row are padded with blanks; columns beyond
    the supplied headers are named ``col_<i>``. A column is converted to
    numeric only if every non-blank value parses as a number, so that
    identifier columns with mixed content are preserved as text.
    """
    width = max(len(r) for r in rows)
    names = list(headers[:width]) + [f"col_{i}" for i in range(len(headers), width)]
    df = pd.DataFrame([list(r) + [""] * (width - len(r)) for r in rows], columns=names)
    for column in df.columns:
        text = df[column].astype(str).str.strip()
        nonblank = text.ne("")
        converted = pd.to_numeric(df[column].where(nonblank), errors="coerce")
        if nonblank.any() and converted[nonblank].notna().all():
            df[column] = converted
    return df


def resolve_report_name(session: requests.Session, report: LoreReport) -> tuple[str, list[str]]:
    """Find the LORE report name for a LIMS report and return its column headers.

    Each candidate name is submitted to LORE's header service in order; the
    first for which LORE returns a non-empty header list is used. A name
    listed in :data:`~sod_explorer.sources.catalog.VERIFIED_LORE_NAMES`
    is accepted on the same evidence, so a verified name and a candidate are
    treated identically at run time.

    Returns
    -------
    name : str
        The LORE report name.
    headers : list of str
        Column headers of the report, HTML entities decoded.

    Raises
    ------
    SourceError
        If LORE recognizes none of the candidates.
    """
    for name in report.candidates:
        try:
            response = _get_json(session, "/reference/HeaderDisplayGet-LORE", {
                "name": name, "scaleid": LORE_SCALE_ID, "splice": "test",
            })
        except (requests.HTTPError, ValueError):
            continue  # LORE does not recognize this name; try the next candidate
        headers = response.get("headers") if isinstance(response, dict) else None
        if headers:
            return name, [unescape(h) for h in headers]
    tried = ", ".join(report.candidates)
    raise SourceError(f"LORE did not recognize a report name for {report.code} (tried: {tried})")


#: Columns of ``carbonates_internal`` that identify a sample.
_CARBONATE_SAMPLE_COLUMNS = ("Exp", "Site", "Hole", "Core", "Type", "Sect", "A/W", "Text ID",
                             "Top offset on sect (cm)", "Bot offset on sect (cm)",
                             "Depth CSF-A (m)", "Depth CSF-B (m)", "Sample comments")
#: CHNS result columns of ``carbonates_internal`` and their names in LORE's Carbonates report.
_CHNS_COLUMNS = {
    "carbon_percent": "Total carbon (wt%)",
    "hydrogen_percent": "Hydrogen (wt%)",
    "nitrogen_percent": "Nitrogen (wt%)",
    "sulfur_percent": "Sulfur (wt%)",
    "carbon_organic_percent": "Organic carbon (wt%), CHNS with treated sample (wt%)",
    "treatment_method": "Sample treatment method (CHNS organic carbon)",
}
#: Mass ratio CaCO3 / C used by LORE to convert inorganic carbon to calcium carbonate.
CARBONATE_FACTOR = 8.333


def assemble_carbonates(raw: pd.DataFrame) -> pd.DataFrame:
    """Assemble LORE's Carbonates report from the rows of ``carbonates_internal``.

    The internal report holds one row per test: a coulometric test (COUL)
    reports inorganic carbon, and an elemental-analyzer test (CHNS) reports
    total carbon, hydrogen, nitrogen, and sulfur, all in the same result
    columns, distinguished by the ``Analysis`` column. Following LORE's
    report definition (``/reference/ReportDefinitionGet-LORE?name=carbonates``),
    the tests of a sample are placed on one row and two quantities are
    computed:

    * calcium carbonate (wt%) = inorganic carbon (wt%) x 8.333;
    * organic carbon by difference (wt%) = total carbon - inorganic carbon.

    Replicate tests of a sample are kept on separate rows (first COUL with
    first CHNS, second with second, and so on); a ``Replicate`` column is
    added when any sample has more than one. If the table does not have
    the expected columns, it is returned unchanged.
    """
    if "Analysis" not in raw.columns or "carbon_percent" not in raw.columns:
        return raw
    keys = [c for c in _CARBONATE_SAMPLE_COLUMNS if c in raw.columns]
    analysis = raw["Analysis"].astype(str).str.strip().str.upper()
    blank = "\x00"   # groupby drops missing keys, so blanks are given a placeholder

    def part(code: str, columns: dict[str, str]) -> pd.DataFrame:
        present = {c: n for c, n in columns.items() if c in raw.columns}
        rows = raw.loc[analysis == code, keys + list(present)].rename(columns=present)
        rows[keys] = rows[keys].astype(object).where(rows[keys].notna(), blank)
        rows["Replicate"] = rows.groupby(keys, sort=False).cumcount() + 1
        return rows

    coul = part("COUL", {"carbon_percent": "Inorganic carbon (wt%)"})
    chns = part("CHNS", _CHNS_COLUMNS)
    if coul.empty and chns.empty:
        return raw
    table = coul.merge(chns, on=keys + ["Replicate"], how="outer", sort=False)
    table[keys] = table[keys].where(table[keys] != blank)
    for column in ("Inorganic carbon (wt%)", "Total carbon (wt%)"):
        if column not in table.columns:
            table[column] = float("nan")
        table[column] = pd.to_numeric(table[column], errors="coerce")
    table["Calcium carbonate (wt%)"] = (table["Inorganic carbon (wt%)"] * CARBONATE_FACTOR).round(3)
    table["Organic carbon (wt%) by difference (CHNS-COUL)"] = (
        table["Total carbon (wt%)"] - table["Inorganic carbon (wt%)"]).round(2)

    measured = ["Inorganic carbon (wt%)", "Calcium carbonate (wt%)", "Total carbon (wt%)",
                "Organic carbon (wt%) by difference (CHNS-COUL)"]
    measured += [n for n in _CHNS_COLUMNS.values() if n in table.columns and n not in measured]
    identifiers = [k for k in keys if k != "Sample comments"]
    order = identifiers + (["Replicate"] if table["Replicate"].max() > 1 else []) + measured
    order += ["Sample comments"] if "Sample comments" in keys else []
    for column in identifiers:
        converted = pd.to_numeric(table[column], errors="coerce")
        if converted.notna().sum() == table[column].notna().sum():
            table[column] = converted
    sort_by = [c for c in ("Site", "Hole", "Depth CSF-A (m)") if c in table.columns]
    return table[order].sort_values(sort_by, kind="stable").reset_index(drop=True)


@lru_cache(maxsize=CACHE_SIZE)
def _fetch_cached(report: LoreReport, expedition: str, site: str, hole: str) -> tuple[pd.DataFrame, str]:
    """Retrieve one LIMS report from LORE. Raises :class:`SourceError` on failure.

    Only successful calls are cached: :func:`functools.lru_cache` does not
    store calls that raise, so a failed request is retried on the next call.

    Returns
    -------
    df : pandas.DataFrame
        Report rows restricted to the request.
    name : str
        The LORE report name that was used.
    """
    filters = [f"x_expedition in ('{expedition}')"]
    if site:
        filters.append(f"x_site in ('{site}')")
    if hole:
        filters.append(f"x_hole in ('{hole}')")
    post = json.dumps({"scale_id": LORE_SCALE_ID})
    where = describe_request(expedition, site, hole)

    try:
        session = _session()
        name, headers = resolve_report_name(session, report)
        test_ids = _get_json(session, "/limsM/AWorkingSetGet-LORE", {
            "username": LORE_USER, "password": LORE_PASSWORD, "report": name,
            "postretrieve": post, "filters": json.dumps(filters),
        })
        if not isinstance(test_ids, list) or not test_ids:
            raise SourceError(f"LORE has no {report.code} data for {where}")
        rows: list[list] = []
        for start in range(0, len(test_ids), BATCH_SIZE):
            batch = test_ids[start:start + BATCH_SIZE]
            chunk = _get_json(session, "/limsM/DisplayGet-LORE", {
                "name": name, "id": "[" + ",".join(str(x) for x in batch) + "]",
                "postretrieve": post, "username": LORE_USER, "password": LORE_PASSWORD,
                "nolink": "true",
            })
            if isinstance(chunk, list):
                rows.extend(chunk)
    except requests.RequestException as exc:
        raise SourceError(f"Could not reach LORE: {exc}") from exc
    except ValueError as exc:
        raise SourceError("LORE returned a response that is not valid JSON") from exc

    if not rows:
        raise SourceError(f"LORE returned no {report.code} rows for {where}")
    df = restrict_to_request(rows_to_frame(rows, headers), expedition, site, hole)
    if df.empty:
        raise SourceError(f"LORE returned {report.code} rows, but none for {where}")
    if report.transform == "carbonates":
        df = assemble_carbonates(df)
    return df, name


def fetch(report_key: str, expedition: str, site: str = "", hole: str = "") -> tuple[pd.DataFrame, dict]:
    """Retrieve a report type from LORE.

    A report type that corresponds to several LIMS reports (split-core
    P-wave velocity is measured with the caliper, PWC, and bayonet, PWB,
    systems) is retrieved report by report and returned as one table with a
    leading ``LIMS report`` column that names the report of each row. LIMS
    reports with no data for the request are omitted.

    Parameters
    ----------
    report_key
        Report key from :data:`~sod_explorer.sources.catalog.REPORTS`.
    expedition, site, hole
        Request filters. Site and Hole are optional.

    Returns
    -------
    df : pandas.DataFrame
        Report rows restricted to the requested Expedition, Site, and Hole.
    source : dict
        Provenance source description.

    Raises
    ------
    SourceError
        If the report type has no LIMS report, LORE is unreachable, or no
        LIMS report returns rows.
    """
    report_type = get_report(report_key)
    if report_type is None or not report_type.lore_reports:
        raise SourceError(f"LIMS has no report for {report_label(report_key)}")
    expedition = validate_identifier(str(expedition or ""), "Expedition")
    site = validate_identifier(site, "Site").upper()
    hole = validate_identifier(hole, "Hole").upper()

    tables, used, errors = [], [], []
    for lore_report in report_type.lore_reports:
        try:
            table, name = _fetch_cached(lore_report, expedition, site, hole)
        except SourceError as exc:
            errors.append(str(exc))
            continue
        table = table.copy()
        if len(report_type.lore_reports) > 1:
            table.insert(0, "LIMS report", lore_report.code)
        tables.append(table)
        used.append({"code": lore_report.code, "lore_report": name, "rows": int(len(table))})
    if not tables:
        raise SourceError("; ".join(errors))
    df = pd.concat(tables, ignore_index=True) if len(tables) > 1 else tables[0]

    source = {
        "type": "lore",
        "name": "LIMS Online Report Environment (LORE)",
        "url": LORE_PAGE,
        "query": {"report": report_key, "lims_reports": used, "expedition": expedition,
                  "site": site, "hole": hole, "scale_id": LORE_SCALE_ID},
        "retrieved_utc": utc_now(),
        "citation_note": (f"IODP LIMS data for Expedition {expedition}, retrieved from LORE "
                          f"({LORE_PAGE}). Cite the Proceedings of the International Ocean "
                          f"Discovery Program volume for Expedition {expedition}."),
    }
    if errors:
        source["query"]["lims_reports_without_data"] = errors
    return df, source
