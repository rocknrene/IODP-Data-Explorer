"""Readers for user-supplied data files.

Supported formats are delimited text (CSV, TSV), Excel workbooks (XLSX,
XLS), Log ASCII Standard files (LAS), and ZIP archives that contain one or
more delimited-text members (for example, J-CORES bulk exports from the
Chikyu).

Delimited-text and Excel exports from LIMS, J-CORES, and legacy databases
often begin with free-text metadata rows above the column header. The
header row is located by scoring each of the first rows for vocabulary
typical of drilling-data headers (see :func:`detect_header_row`).

Every reader returns a :class:`pandas.DataFrame` and a metadata dictionary.
The metadata always contains the file name, detected format, SHA-256
digest of the raw bytes (recorded in the provenance of any derived
dataset), and the row and column counts.
"""

from __future__ import annotations

import base64
import hashlib
import io
import os
import re
import zipfile

import pandas as pd

from .columns import depth_scale, find_depth_column, find_identifier_column, normalize_header
from .reference import REFERENCE, UNLETTERED_HOLE


class ParseError(ValueError):
    """Raised when a file cannot be read as a data table."""


SUPPORTED_EXTENSIONS = (".csv", ".tsv", ".txt", ".xlsx", ".xls", ".las", ".zip")

# Vocabulary used to recognize a header row. A row is scored by the number
# of these substrings it contains.
HEADER_KEYWORDS = (
    "depth", "lith", "facies", "unit", "section", "sample", "core",
    "upper", "lower", "top", "bottom", "description", "interval", "formation",
)

# LIMS exports share a distinctive leading header
# ("Exp","Site","Hole","Core","Type","Sect","A/W","Offset (cm)",...).
# A row containing three or more of these substrings is taken to be a LIMS
# header without further scoring.
LIMS_HEADER_KEYWORDS = (
    "exp", "site", "hole", "core", "type", "sect", "offset", "depth csf",
    "depth mbsf", "depth (m", "csf-a", "mcd", "text id", "label id",
)

_N_SCAN_ROWS = 30
_TEXT_ENCODINGS = ("utf-8-sig", "cp1252", "latin-1")


# ---------------------------------------------------------------------------
# Header and metadata detection
# ---------------------------------------------------------------------------

def _keyword_score(text: str, keywords: tuple[str, ...]) -> int:
    """Number of keywords that occur in a text, ignoring case."""
    lower = text.lower()
    return sum(1 for kw in keywords if kw in lower)


def is_lims_header_row(line: str) -> bool:
    """True if a text line has the column vocabulary of a LIMS export header."""
    return _keyword_score(line, LIMS_HEADER_KEYWORDS) >= 3


def detect_header_row(lines: list[str], n_scan: int = _N_SCAN_ROWS) -> int:
    """Index of the row most likely to be the column header.

    The first row that matches the LIMS header vocabulary is returned
    immediately. Otherwise the row with the highest
    :data:`HEADER_KEYWORDS` score is returned, with ties resolved in favor
    of the earliest row; if no row scores above zero, row 0 is returned.
    """
    for i, line in enumerate(lines[:n_scan]):
        if is_lims_header_row(line):
            return i
    best_row, best_score = 0, 0
    for i, line in enumerate(lines[:n_scan]):
        score = _keyword_score(line, HEADER_KEYWORDS)
        if score > best_score:
            best_row, best_score = i, score
    return best_row


_SEPARATOR = r'["\s,:=]+'
_META_PATTERNS = {
    "expedition": re.compile(r"\b(?:expedition|leg)" + _SEPARATOR + r"(\d+[A-Z]?)\b", re.IGNORECASE),
    "site": re.compile(r"\bsite" + _SEPARATOR + r"([A-Z]?\d{1,5})\b", re.IGNORECASE),
    "hole": re.compile(r"\bhole" + _SEPARATOR + r"([A-Z])\b", re.IGNORECASE),
}


def extract_preamble_metadata(lines: list[str]) -> dict[str, str]:
    """Read Expedition, Site, and Hole from key-value rows above the header.

    Recognizes forms such as ``Expedition: 405``, ``"Site","U1480"``, and
    ``Hole = E``. Site identifiers may be numeric (DSDP, ODP: ``1172``) or
    carry a platform prefix (IODP: ``U1480``, ``C0019``, ``M0077``).
    """
    found: dict[str, str] = {}
    for line in lines:
        for key, pattern in _META_PATTERNS.items():
            if key in found:
                continue
            match = pattern.search(line)
            if match:
                found[key] = match.group(1).upper()
    return found


# ---------------------------------------------------------------------------
# Format-specific readers
# ---------------------------------------------------------------------------

def _decode_text(raw: bytes) -> tuple[str, str]:
    """Decode bytes to text, returning ``(text, encoding)``.

    UTF-16 is used only when a byte-order mark is present. Otherwise UTF-8
    is attempted first, then Windows-1252, then Latin-1 (which accepts any
    byte sequence and therefore always succeeds).
    """
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16"), "utf-16"
    for encoding in _TEXT_ENCODINGS:
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ParseError("Could not decode file as text")  # unreachable: latin-1 accepts all bytes


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    """Drop fully empty rows and columns and reset the index."""
    return df.dropna(axis=1, how="all").dropna(how="all").reset_index(drop=True)


def _sniff_delimiter(header_line: str) -> str:
    """Choose the delimiter that occurs most often in the header line.

    Candidates are comma, tab, semicolon, and vertical bar; a comma is
    returned if none occurs.
    """
    counts = {sep: header_line.count(sep) for sep in (",", "\t", ";", "|")}
    best = max(counts, key=counts.get)
    return best if counts[best] > 0 else ","


def read_delimited(raw: bytes, filename: str) -> tuple[pd.DataFrame, dict]:
    """Read CSV or TSV bytes, locating the header row automatically.

    The delimiter is a tab for ``.tsv`` files and is otherwise inferred
    from the header row (see :func:`_sniff_delimiter`). Rows above the
    header are excluded from the table and searched for Expedition, Site,
    and Hole values. Malformed data rows raise :class:`ParseError` rather
    than being dropped silently.
    """
    text, encoding = _decode_text(raw)
    lines = text.splitlines()
    if not lines:
        raise ParseError(f"{filename} is empty")
    header_row = detect_header_row(lines)
    sep = "\t" if filename.lower().endswith(".tsv") else _sniff_delimiter(lines[header_row])
    try:
        df = pd.read_csv(io.StringIO(text), skiprows=header_row, header=0, sep=sep,
                         skip_blank_lines=True)
    except (pd.errors.ParserError, pd.errors.EmptyDataError, ValueError) as exc:
        raise ParseError(f"Could not parse {filename} as delimited text: {exc}") from exc
    meta = {"encoding": encoding, "header_row": header_row, "delimiter": sep}
    if header_row > 0:
        preamble = extract_preamble_metadata(lines[:header_row])
        if preamble:
            meta["preamble"] = preamble
    return _clean(df), meta


def read_excel(raw: bytes, filename: str) -> tuple[pd.DataFrame, dict]:
    """Read the first worksheet of an Excel workbook.

    The sheet is read once without a header; the header row is then located
    among the first rows with :func:`detect_header_row`.
    """
    try:
        grid = pd.read_excel(io.BytesIO(raw), header=None, dtype=object)
    except Exception as exc:  # the Excel engines raise several unrelated types
        raise ParseError(f"Could not read {filename} as an Excel workbook: {exc}") from exc
    if grid.empty:
        raise ParseError(f"{filename} contains no data")
    lines = [",".join("" if pd.isna(v) else str(v) for v in row)
             for row in grid.head(_N_SCAN_ROWS).itertuples(index=False)]
    header_row = detect_header_row(lines)
    header = [str(v).strip() if not pd.isna(v) else f"column_{j}"
              for j, v in enumerate(grid.iloc[header_row])]
    body = grid.iloc[header_row + 1:].copy()
    body.columns = header
    body = body.apply(_to_numeric_if_complete)
    return _clean(body), {"header_row": header_row}


def _to_numeric_if_complete(series: pd.Series) -> pd.Series:
    """Convert a column to numeric only if every non-blank value parses."""
    converted = pd.to_numeric(series, errors="coerce")
    nonblank = series.notna() & (series.astype(str).str.strip() != "")
    if nonblank.any() and converted[nonblank].notna().all():
        return converted
    return series


def read_las(raw: bytes, filename: str) -> tuple[pd.DataFrame, dict]:
    """Read a Log ASCII Standard (LAS) well-log file with :mod:`lasio`."""
    import lasio

    text, encoding = _decode_text(raw)
    try:
        las = lasio.read(io.StringIO(text))
    except Exception as exc:  # lasio raises format-specific exception types
        raise ParseError(f"Could not read {filename} as a LAS file: {exc}") from exc
    df = las.df().reset_index()
    meta = {"encoding": encoding}
    index_curve = las.curves[0] if len(las.curves) else None
    if index_curve is not None:
        meta["index_curve"] = index_curve.mnemonic
        meta["index_unit"] = index_curve.unit or ""
    try:
        meta["well"] = str(las.well["WELL"].value)
    except (KeyError, AttributeError):
        meta["well"] = ""
    return df, meta


def read_zip(raw: bytes, filename: str) -> tuple[pd.DataFrame, dict]:
    """Read the most table-like delimited-text member of a ZIP archive.

    Each CSV/TSV member is parsed and scored by the number of
    :data:`HEADER_KEYWORDS` in its column names. Members whose names begin
    with ``bulk`` (the J-CORES bulk-export convention) receive one
    additional point. The highest-scoring member is returned; the names of
    the others are recorded in the metadata.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise ParseError(f"{filename} is not a valid ZIP archive") from exc
    members = [n for n in archive.namelist()
               if n.lower().endswith((".csv", ".tsv", ".txt")) and "__MACOSX" not in n]
    if not members:
        listed = ", ".join(archive.namelist()[:10])
        raise ParseError(f"No CSV/TSV member in {filename} (contains: {listed})")

    best = None
    for name in members:
        try:
            df, meta = read_delimited(archive.read(name), name)
        except ParseError:
            continue
        if df.empty:
            continue
        score = sum(_keyword_score(str(c), HEADER_KEYWORDS) for c in df.columns)
        if os.path.basename(name).lower().startswith("bulk"):
            score += 1
        if best is None or score > best[0]:
            best = (score, name, df, meta)
    if best is None:
        raise ParseError(f"None of the {len(members)} CSV/TSV members of {filename} could be parsed")

    _, name, df, meta = best
    meta["zip_member"] = name
    others = [n for n in members if n != name]
    if others:
        meta["zip_other_members"] = others[:10]
    return df, meta


# ---------------------------------------------------------------------------
# Identifiers from sample labels
# ---------------------------------------------------------------------------

# "<leg>-<site><hole>[-<core><type>]...", e.g. "177-1090E-8H-3,130", "29-280A",
# "362-U1480E-1H-1". Hole and core type are optional (early DSDP labels).
_LABEL_WITH_LEG = re.compile(r"^\s*(\d{1,3}[A-Z]?)-([A-Z]?\d{1,4})([A-Z])?(?:-(\d{1,3})([A-Z])?)?(?![A-Za-z0-9])")
# "<site><hole>-<core><type>...", e.g. "C0019J-1K-1" (J-CORES), "U1480E-1H-1".
_LABEL_WITHOUT_LEG = re.compile(r"^\s*([A-Z]?\d{1,4})([A-Z])?-(\d{1,3})([A-Z])?(?![A-Za-z0-9])")
_LABEL_HEADERS = ("sample label", "sample id", "sample_id", "sampleid", "jcores_sampleid",
                  "event label", "event", "label", "sample")
_MIN_VALID_FRACTION = 0.9


def identifiers_from_sample_labels(df: pd.DataFrame) -> tuple[pd.DataFrame, str | None]:
    """Add Expedition, Site, Hole, and Core columns parsed from sample labels.

    Tables from PANGAEA and J-CORES identify samples by a label such as
    ``177-1090E-8H-3,130`` (Leg 177, Site 1090, Hole E, Core 8, type H)
    rather than by separate columns. Without Site and Hole columns, samples
    cannot be grouped by hole for merging, smoothing, or gap detection.

    A label column is accepted only if at least 90% of its values parse
    and the parsed Sites exist in the reference table (with the parsed Leg,
    when the label includes one). This guards against misreading labels of
    another form. Tables that already have Site and Hole columns are
    returned unchanged.

    Returns
    -------
    df : pandas.DataFrame
        The table, with ``Exp`` (if present in the labels), ``Site``,
        ``Hole``, ``Core``, and ``Type`` columns inserted first when a label
        column was accepted. An absent hole letter is recorded as ``*``.
    source_column : str or None
        Name of the label column used, or ``None`` if nothing was added.
    """
    if find_identifier_column(df, "site") is not None and find_identifier_column(df, "hole") is not None:
        return df, None
    known_sites = set(REFERENCE["Site"])
    known_leg_sites = set(zip(REFERENCE["Exp"], REFERENCE["Site"], strict=True))
    candidates = [c for c in df.columns
                  if normalize_header(c, strip_merge_suffix=False) in _LABEL_HEADERS
                  and not pd.api.types.is_numeric_dtype(df[c])]
    for column in candidates:
        labels = df[column].dropna().astype(str)
        if labels.empty:
            continue
        for pattern, names in ((_LABEL_WITH_LEG, ["Exp", "Site", "Hole", "Core", "Type"]),
                               (_LABEL_WITHOUT_LEG, ["Site", "Hole", "Core", "Type"])):
            parts = labels.str.extract(pattern)
            parts.columns = names
            parsed = parts["Site"].notna()
            if "Exp" in names:
                pairs = pd.Series(list(zip(parts["Exp"], parts["Site"], strict=True)), index=parts.index)
                valid = parsed & pairs.isin(known_leg_sites)
            else:
                valid = parsed & parts["Site"].isin(known_sites)
            if valid.mean() < _MIN_VALID_FRACTION:
                continue
            parts = parts.where(valid).reindex(df.index)
            parts["Hole"] = parts["Hole"].where(parts["Hole"].notna() | parts["Site"].isna(), UNLETTERED_HOLE)
            parts["Core"] = pd.to_numeric(parts["Core"], errors="coerce")
            parts = parts.dropna(axis=1, how="all")
            result = df.copy()
            for position, name in enumerate(n for n in names if n in parts.columns and n not in df.columns):
                result.insert(position, name, parts[name])
            return result, str(column)
    return df, None


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

_READERS = {
    ".csv": ("CSV", read_delimited),
    ".txt": ("Text", read_delimited),
    ".tsv": ("TSV", read_delimited),
    ".xlsx": ("Excel", read_excel),
    ".xls": ("Excel", read_excel),
    ".las": ("LAS", read_las),
    ".zip": ("ZIP", read_zip),
}


def parse_file(raw: bytes, filename: str) -> tuple[pd.DataFrame, dict]:
    """Parse a data file into a table and a metadata dictionary.

    Parameters
    ----------
    raw
        File contents.
    filename
        Original file name; the extension selects the reader.

    Raises
    ------
    ParseError
        If the extension is unsupported or the contents cannot be parsed.
    """
    extension = os.path.splitext(filename.lower())[1]
    if extension not in _READERS:
        raise ParseError(f"Unsupported file type '{extension}'. "
                         f"Supported: {', '.join(SUPPORTED_EXTENSIONS)}")
    fmt, reader = _READERS[extension]
    df, meta = reader(raw, filename)
    if df.empty:
        raise ParseError(f"{filename} contains no data rows")
    df.columns = [str(c).strip() for c in df.columns]
    df, label_column = identifiers_from_sample_labels(df)
    if label_column is not None:
        meta["identifiers_parsed_from"] = label_column
    meta.update(
        filename=filename,
        format=fmt,
        sha256=hashlib.sha256(raw).hexdigest(),
        rows=int(len(df)),
        cols=int(len(df.columns)),
    )
    return df, meta


def decode_upload(contents: str) -> bytes:
    """Decode the ``data:<mime>;base64,<payload>`` string from ``dcc.Upload``."""
    try:
        _, payload = contents.split(",", 1)
        return base64.b64decode(payload)
    except (ValueError, TypeError) as exc:
        raise ParseError("Upload payload is not valid base64 data") from exc


# ---------------------------------------------------------------------------
# Lithology tables
# ---------------------------------------------------------------------------

_LITHOLOGY_ALIASES = {
    "top_depth": ("top depth", "upper depth", "depth top", "topdepth", "top_depth",
                  "top_mbsf", "top (m", "top_csf", "top"),
    "bottom_depth": ("bottom depth", "lower depth", "depth bottom", "bottomdepth",
                     "bottom_depth", "bottom_mbsf", "bottom (m", "bot_csf", "bot depth",
                     "bottom"),
    "lithology": ("lithofacies", "lithology", "lith. unit", "lith unit", "litho unit",
                  "lithostratigraphic", "facies", "sediment type", "rock type",
                  "unit name", "description"),
}


def resolve_lithology_columns(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Standardize a lithology interval table.

    Identifies the top-depth, bottom-depth, and lithology columns by header
    aliases and returns a table with columns ``top_depth``,
    ``bottom_depth``, and ``lithology``. Intervals with missing depths or
    with top not shallower than bottom are removed.

    Returns
    -------
    table : pandas.DataFrame
        The standardized interval table.
    info : dict
        Source column names, the inferred depth scale of the top-depth
        column, and the number of intervals removed.

    Raises
    ------
    ParseError
        If any of the three required columns cannot be identified.
    """
    mapping: dict[str, str] = {}
    lowered = [(str(c).lower().strip(), c) for c in df.columns]
    for target, aliases in _LITHOLOGY_ALIASES.items():
        for alias in aliases:
            match = next((orig for low, orig in lowered
                          if alias in low and orig not in mapping.values()), None)
            if match is not None:
                mapping[target] = match
                break
    missing = [t for t in _LITHOLOGY_ALIASES if t not in mapping]
    if missing:
        detected = "\n".join(f"  - {c}" for c in df.columns)
        raise ParseError(f"Could not identify: {', '.join(missing)}.\n\nDetected columns:\n{detected}")

    table = df[[mapping["top_depth"], mapping["bottom_depth"], mapping["lithology"]]].copy()
    table.columns = ["top_depth", "bottom_depth", "lithology"]
    for column in ("top_depth", "bottom_depth"):
        table[column] = pd.to_numeric(table[column], errors="coerce")
    n_input = len(table)
    table = table.dropna(subset=["top_depth", "bottom_depth"])
    table = table[table["top_depth"] < table["bottom_depth"]].reset_index(drop=True)
    table["lithology"] = table["lithology"].fillna("unknown").astype(str).str.strip()
    info = {
        "source_columns": mapping,
        "depth_scale": depth_scale(mapping["top_depth"]),
        "intervals_removed": int(n_input - len(table)),
    }
    return table, info


# ---------------------------------------------------------------------------
# Site summary
# ---------------------------------------------------------------------------

def _first_value(df: pd.DataFrame, column: str | None) -> str | None:
    """First distinct non-missing value of a column as text, or ``None``."""
    if column is None:
        return None
    values = df[column].dropna().astype(str).unique()
    return values[0] if len(values) else None


def _distinct_count(df: pd.DataFrame, column: str | None) -> int:
    """Number of distinct values in a column; zero if the column is absent."""
    return int(df[column].nunique()) if column is not None else 0


def summarize_site(df: pd.DataFrame, meta: dict) -> dict:
    """Summarize the Expedition, Site/Hole, and depth coverage of a table.

    Values are taken from identifier columns when present, then from the
    preamble metadata, then from J-CORES-style sample identifiers
    (``C0019J-...``). When a table spans several Expeditions or Holes, the
    count is reported rather than an arbitrary single value.
    """
    info: dict = {
        "filename": meta.get("filename", ""),
        "format": meta.get("format", ""),
        "rows": meta.get("rows", len(df)),
    }
    exp_col = find_identifier_column(df, "expedition")
    site_col = find_identifier_column(df, "site")
    hole_col = find_identifier_column(df, "hole")

    if _distinct_count(df, exp_col) > 1:
        info["expedition"] = f"{_distinct_count(df, exp_col)} expeditions"
    else:
        info["expedition"] = _first_value(df, exp_col) or meta.get("preamble", {}).get("expedition")

    n_holes = (df[[site_col, hole_col]].drop_duplicates().shape[0]
               if site_col and hole_col else 0)
    if n_holes > 1:
        info["site_hole"] = f"{n_holes} holes"
    else:
        site = _first_value(df, site_col) or meta.get("preamble", {}).get("site")
        hole = _first_value(df, hole_col) or meta.get("preamble", {}).get("hole")
        if site:
            info["site_hole"] = site + (hole or "")

    if "site_hole" not in info:
        id_col = next((c for c in df.columns
                       if str(c).lower() in ("jcores_sampleid", "sampleid", "sample_id", "sample")), None)
        first = _first_value(df, id_col)
        match = re.match(r"([A-Z]\d{3,5}[A-Z]?)", first or "")
        if match:
            info["site_hole"] = match.group(1)

    depth_col = find_depth_column(df)
    if depth_col is not None:
        depths = df[depth_col].dropna()
        if len(depths):
            info.update(depth_column=depth_col, depth_scale=depth_scale(depth_col),
                        depth_min=float(depths.min()), depth_max=float(depths.max()))
    return info
