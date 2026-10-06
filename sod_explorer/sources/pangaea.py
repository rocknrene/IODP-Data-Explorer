"""Client for PANGAEA (https://www.pangaea.de).

Search uses PANGAEA's public search endpoint (``/advanced/search.php``),
the same endpoint used by the PANGAEA website and by the ``pangaeapy``
client. Datasets are downloaded as tab-separated text from their DOI URL
with ``Accept: text/tab-separated-values``.

A PANGAEA tab-separated download begins with a metadata block enclosed in
``/* ... */``. The block contains, among other fields, the dataset
``Citation`` and ``License``; these are parsed into the provenance record
so that exports can cite the dataset DOI, as PANGAEA's licenses require.

Search results are screened before they are offered to the user:

* Results whose titles name the requested Leg/Expedition are ranked first
  (PANGAEA's relevance ranking can place a dataset from another Leg that
  shares more keywords above an exact match).
* If none of the results mentions the requested measurement type, no
  results are offered, rather than presenting unrelated datasets as
  candidates.
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET

import pandas as pd
import requests

from ..parsing import identifiers_from_sample_labels
from ..provenance import utc_now
from .common import USER_AGENT, SourceError

SEARCH_URL = "https://www.pangaea.de/advanced/search.php"
DOI_PREFIX = "10.1594/PANGAEA."
TIMEOUT_S = 60
TITLE_LOOKUPS = 8            # results for which the title is retrieved
_METADATA_NS = {"md": "http://www.pangaea.de/MetaData"}
_HEADERS = {"User-Agent": USER_AGENT}


def dataset_url(pangaea_id: str) -> str:
    """DOI URL of a PANGAEA dataset."""
    return f"https://doi.pangaea.de/{DOI_PREFIX}{pangaea_id}"


def parse_tab_download(text: str) -> tuple[pd.DataFrame, dict]:
    """Split a PANGAEA tab-separated download into table and header fields.

    Parameters
    ----------
    text
        Body of the download.

    Returns
    -------
    df : pandas.DataFrame
        The data table, with fully empty rows and columns removed.
    header : dict
        Fields of the ``/* ... */`` metadata block, keyed by field name
        (``"Citation"``, ``"License"``, and others). Continuation lines are
        appended to the preceding field.
    """
    header: dict[str, str] = {}
    body = text
    match = re.match(r"\s*/\*(.*?)\*/", text, flags=re.DOTALL)
    if match:
        body = text[match.end():]
        key = None
        for line in match.group(1).splitlines():
            if ":\t" in line:
                key, value = line.split(":\t", 1)
                key = key.strip()
                header[key] = value.strip()
            elif key and line.strip():
                header[key] += " " + line.strip()
    df = pd.read_csv(io.StringIO(body.strip()), sep="\t", skip_blank_lines=True)
    df = df.dropna(axis=1, how="all").dropna(how="all").reset_index(drop=True)
    return df, header


def fetch_dataset(pangaea_id: str) -> tuple[pd.DataFrame, dict]:
    """Download a PANGAEA dataset.

    Returns
    -------
    df : pandas.DataFrame
        The data table.
    source : dict
        Provenance source description including the dataset citation,
        DOI, and license.

    Raises
    ------
    SourceError
        If the download fails or yields an empty table.
    """
    pangaea_id = str(pangaea_id).strip()
    if not pangaea_id.isdigit():
        raise SourceError(f"Invalid PANGAEA identifier: {pangaea_id!r}")
    url = dataset_url(pangaea_id)
    try:
        response = requests.get(url, timeout=TIMEOUT_S,
                                headers={**_HEADERS, "Accept": "text/tab-separated-values"})
        response.raise_for_status()
        df, header = parse_tab_download(response.text)
    except requests.RequestException as exc:
        raise SourceError(f"Could not download PANGAEA dataset {pangaea_id}: {exc}") from exc
    except (pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        raise SourceError(f"PANGAEA dataset {pangaea_id} could not be parsed: {exc}") from exc
    if df.empty:
        raise SourceError(f"PANGAEA dataset {pangaea_id} is empty")
    df, label_column = identifiers_from_sample_labels(df)
    source = {
        "type": "pangaea",
        "name": f"PANGAEA dataset {pangaea_id}",
        "url": url,
        "doi": f"{DOI_PREFIX}{pangaea_id}",
        "query": {"pangaea_id": pangaea_id},
        "retrieved_utc": utc_now(),
        "citation": header.get("Citation"),
        "license": header.get("License"),
    }
    if label_column is not None:
        source["identifiers_parsed_from"] = label_column
    return df, source


def fetch_title(pangaea_id: str, timeout: float = 10) -> str | None:
    """Retrieve a dataset title from its metadata XML, or ``None`` on failure.

    The title is read from ``md:citation/md:title``. The search index does
    not return clean titles, particularly for legacy DSDP/ODP datasets.
    """
    try:
        response = requests.get(dataset_url(pangaea_id), timeout=timeout,
                                headers={**_HEADERS, "Accept": "application/vnd.pangaea.metadata+xml"})
        response.raise_for_status()
        element = ET.fromstring(response.content).find("./md:citation/md:title", _METADATA_NS)
    except (requests.RequestException, ET.ParseError):
        return None
    return element.text.strip() if element is not None and element.text else None


def search(query: str, count: int = 10) -> list[dict]:
    """Search PANGAEA and return candidate datasets.

    Returns
    -------
    list of dict
        Each with ``value`` (PANGAEA identifier) and ``label``
        (``"<id>: <title>"``). Titles are retrieved for the first
        :data:`TITLE_LOOKUPS` results; the others are labeled with their URI.

    Raises
    ------
    SourceError
        If the search request fails.
    """
    try:
        response = requests.get(SEARCH_URL, params={"q": query, "count": count, "offset": 0},
                                timeout=20, headers=_HEADERS)
        response.raise_for_status()
        hits = response.json().get("results", [])
    except (requests.RequestException, ValueError) as exc:
        raise SourceError(f"PANGAEA search failed: {exc}") from exc
    results = []
    for hit in hits:
        uri = hit.get("URI", "")
        pangaea_id = uri.rsplit(".", 1)[-1] if uri else ""
        if pangaea_id.isdigit():
            results.append({"value": pangaea_id, "label": f"{pangaea_id}: {uri}"})
    for item in results[:TITLE_LOOKUPS]:
        title = fetch_title(item["value"])
        if title:
            item["label"] = f"{item['value']}: {title[:90]}"
    return results


def leg_pattern(expedition: str) -> re.Pattern:
    """Pattern that matches "Leg 29", "Legs 29", "Hole 29", "Expedition 405", etc.

    The trailing word boundary allows the number to be followed by any
    non-alphanumeric character or by the end of the title.
    """
    return re.compile(rf"\b(?:Legs?|Holes?|Expeditions?|Exp\.?)\s*{re.escape(str(expedition).strip())}\b",
                      re.IGNORECASE)


def keyword_matches(label: str, keyword: str | None) -> bool:
    """Case-insensitive keyword test that tolerates a plural keyword.

    ``"diatoms"`` matches a title containing ``"Diatom stratigraphy"``.
    """
    if not keyword:
        return True
    label, keyword = label.lower(), keyword.lower()
    return keyword in label or (keyword.endswith("s") and keyword[:-1] in label)


def screen_results(results: list[dict], expedition: str, keyword: str | None) -> list[dict]:
    """Rank results by Leg match and drop them if none matches the keyword.

    Returns
    -------
    list of dict
        Results naming the requested Leg first; an empty list if a keyword
        was given and no result title contains it.
    """
    if keyword and not any(keyword_matches(r["label"], keyword) for r in results):
        return []
    pattern = leg_pattern(expedition)
    return sorted(results, key=lambda r: 0 if pattern.search(r["label"]) else 1)


def find_datasets(expedition: str, keyword: str | None, platform_term: str = "",
                  unit: str = "Expedition") -> list[dict]:
    """Search PANGAEA for datasets from a Leg or Expedition and screen the results.

    Parameters
    ----------
    expedition
        Leg or Expedition number.
    keyword
        Measurement-type search term (the ``pangaea_term`` of a
        :class:`~sod_explorer.sources.catalog.ReportType`).
    platform_term
        Additional search term, for example ``"Chikyu"`` or ``"DSDP"``.
    unit
        ``"Expedition"`` (IODP) or ``"Leg"`` (DSDP, ODP).
    """
    query = " ".join(part for part in (f'"{unit} {expedition}"', platform_term, keyword or "") if part)
    return screen_results(search(query), expedition, keyword)
