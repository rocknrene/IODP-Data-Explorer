"""Column classification for scientific ocean drilling tables.

Data tables from LIMS/LORE, PANGAEA, J-CORES, and legacy DSDP files mix
three kinds of columns:

* **Depth columns**, which locate a sample on a named depth scale.
* **Identifier columns**, which locate a sample in the curation hierarchy
  (Expedition, Site, Hole, Core, Section, offset) or carry bookkeeping
  fields (sample IDs, timestamps, instrument names, comments). Several of
  these are numeric but carry no physical meaning as measurements.
* **Measurement columns**: numeric physical, chemical, or other observations.

PANGAEA tables carry no Site or Hole columns; the curation hierarchy is
encoded in a sample label instead ("177-1090E-8H-3,130").
:func:`sod_explorer.parsing.identifiers_from_sample_labels` expands such
labels into identifier columns.

Statistics and default plot selections use measurement columns only, so
that curation indices (for example Core number) are never correlated
against physical properties.

Depth scales follow the IODP depth-scale terminology (IODP-MI, 2011,
*IODP Depth Scales Terminology*, version 2.0), in which the legacy scales
"mbsf" and "mcd" correspond to CSF-A and CCSF-A, respectively.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd

# ---------------------------------------------------------------------------
# Header normalization
# ---------------------------------------------------------------------------

_MERGE_SUFFIX = re.compile(r"_(a|b)$", re.IGNORECASE)
_UNITS = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]\s*")


def normalize_header(name: object, strip_merge_suffix: bool = True) -> str:
    """Lowercase a column header and remove bracketed units.

    ``"Depth CSF-A (m)_A"`` becomes ``"depth csf-a"`` when
    ``strip_merge_suffix`` is true.
    """
    text = str(name).strip()
    if strip_merge_suffix:
        text = _MERGE_SUFFIX.sub("", text)
    text = _UNITS.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip().lower()


# ---------------------------------------------------------------------------
# Identifier columns
# ---------------------------------------------------------------------------

_IDENTIFIER_PATTERNS: dict[str, re.Pattern] = {
    "expedition": re.compile(r"^(exp|exp\.|expedition|leg)$"),
    "site": re.compile(r"^site$"),
    "hole": re.compile(r"^hole$"),
    "core": re.compile(r"^core$"),
    "section": re.compile(r"^(sect|sect\.|section)$"),
}

_OTHER_IDENTIFIER_PATTERNS = [
    re.compile(p)
    for p in (
        r"^type$",
        r"^core type$",
        r"^a/w$",
        r"^(archive|working)( ?/ ?(archive|working))?$",
        r"^half$",
        r"offset",
        r"^(text|label|sample|test|specimen|result) ?(id|no\.?|number)$",
        r"^(jcores_)?sample_?id$",
        r"^sample$",
        r"^id$",
        r"timestamp",
        r"^(date|time)",
        r"^instrument",
        r"^(analyst|user|comments?)$",
        r"comment",
        r"^(sample|interval)( ?(top|bottom|label|name))?$",
    )
]


def identifier_kind(column: object) -> str | None:
    """Return the curation level a column identifies, if any.

    Returns one of ``"expedition"``, ``"site"``, ``"hole"``, ``"core"``,
    ``"section"``, ``"other"`` (a bookkeeping identifier), or ``None`` for
    a column that is not an identifier.
    """
    header = normalize_header(column)
    for kind, pattern in _IDENTIFIER_PATTERNS.items():
        if pattern.match(header):
            return kind
    if any(p.search(header) for p in _OTHER_IDENTIFIER_PATTERNS):
        return "other"
    return None


def find_identifier_column(df: pd.DataFrame, kind: str) -> str | None:
    """Return the first column that identifies the given curation level.

    Parameters
    ----------
    df
        Input table.
    kind
        ``"expedition"``, ``"site"``, ``"hole"``, ``"core"``, or ``"section"``.
    """
    for column in df.columns:
        if identifier_kind(column) == kind:
            return column
    return None


# ---------------------------------------------------------------------------
# Depth columns and depth scales
# ---------------------------------------------------------------------------

# Ordered from most to least specific so that, for example, "CCSF-A" is not
# reported as "CSF-A".
_SCALE_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("CCSF-A", re.compile(r"\bccsf-?a\b")),
    ("CCSF-D", re.compile(r"\bccsf-?d\b")),
    ("CCSF", re.compile(r"\bccsf\b")),
    ("CSF-A", re.compile(r"\bcsf-?a\b")),
    ("CSF-B", re.compile(r"\bcsf-?b\b")),
    ("WMSF", re.compile(r"\bwmsf\b")),
    ("WSF", re.compile(r"\bwsf\b")),
    ("LSF", re.compile(r"\blsf\b")),
    ("DSF", re.compile(r"\bdsf\b")),
    ("rmcd", re.compile(r"\brmcd\b")),
    ("amcd", re.compile(r"\bamcd\b")),
    ("mcd", re.compile(r"\bmcd\b")),
    ("mbsf", re.compile(r"mbsf")),
    # PANGAEA parameter "DEPTH, sediment/rock" (short name "Depth sed"): depth
    # below seafloor with no named scale, treated here as the legacy mbsf.
    ("mbsf", re.compile(r"\bdepth sed\b|depth, sediment")),
]

#: Legacy scale names mapped to their IODP (2011) equivalents.
_EQUIVALENT_SCALES = {"mbsf": "CSF-A", "mcd": "CCSF-A"}

#: Scales that register all holes at a site to a common composite depth.
COMPOSITE_SCALES = frozenset({"CCSF", "CCSF-A", "CCSF-D", "mcd", "rmcd", "amcd"})

_DEPTH_WORD = re.compile(r"depth|mbsf|mcd|\bcsf|\bccsf|\bwsf|\bwmsf|\blsf|\bdsf|^dept$")
_NOT_DEPTH = re.compile(r"offset|water depth|depth below rig|\bdrf\b|\bmsl\b")

# Preference when several depth columns are present. CSF-A is the standard
# LIMS reporting scale, so it is chosen first; top depths are preferred over
# bottom depths for interval data.
_SCALE_PRIORITY = {
    "CSF-A": 100, "mbsf": 95, "CCSF-A": 90, "CCSF-D": 88, "CCSF": 85, "mcd": 84,
    "rmcd": 83, "amcd": 82, "CSF-B": 70, "WMSF": 60, "WSF": 58, "LSF": 56, "DSF": 50,
}


def _header_text(column: object) -> str:
    """Lower-case header with any merge suffix removed but units retained.

    Scale names are often written inside the unit brackets ("Depth (mcd)"),
    so scale detection must not discard bracketed text.
    """
    return _MERGE_SUFFIX.sub("", str(column).strip()).lower()


def depth_scale(column: object) -> str | None:
    """Infer the depth scale named in a column header.

    Returns the scale label as written in the IODP terminology (for example
    ``"CSF-A"``) or the legacy label (``"mbsf"``, ``"mcd"``), or ``None``
    if the header names no recognized scale.
    """
    header = _header_text(column)
    for label, pattern in _SCALE_PATTERNS:
        if pattern.search(header):
            return label
    return None


def canonical_scale(scale: str | None) -> str | None:
    """Map a legacy scale label to its IODP (2011) equivalent."""
    if scale is None:
        return None
    return _EQUIVALENT_SCALES.get(scale, scale)


def is_composite_scale(scale: str | None) -> bool:
    """True if the scale registers all holes at a site to one composite depth."""
    return scale in COMPOSITE_SCALES


def depth_unit_factor(column: object) -> tuple[float, bool]:
    """Conversion factor from a depth column's unit to meters.

    Returns
    -------
    factor : float
        Multiplier that converts the column's values to meters.
    assumed : bool
        True if the header states no unit and meters were assumed.
    """
    text = str(column).lower()
    if re.search(r"[\(\[]\s*cm\s*[\)\]]|_cm\b", text):
        return 0.01, False
    if re.search(r"[\(\[]\s*mm\s*[\)\]]|_mm\b", text):
        return 0.001, False
    if re.search(r"[\(\[]\s*m\s*[\)\]]|_m\b|mbsf|\bmcd\b", text):
        return 1.0, False
    return 1.0, True


def is_depth_column(column: object) -> bool:
    """True if a header denotes a depth below seafloor (not an offset)."""
    header = _header_text(column)
    return bool(_DEPTH_WORD.search(header)) and not _NOT_DEPTH.search(header)


def find_depth_column(df: pd.DataFrame, numeric_only: bool = True) -> str | None:
    """Select the most appropriate depth column in a table.

    Candidates are ranked by depth scale (CSF-A first; see
    ``_SCALE_PRIORITY``), then adjusted by header wording:

    * top depths are preferred over bottom depths for interval data;
    * per-sample depths ("sample depth") are preferred;
    * core-level depths ("top of core depth") are penalized, since they
      are shared by every sample of a core;
    * depths within a section ("top interval depth (cm)" in DSDP tables)
      and depths stated in centimeters are penalized, since sub-seafloor
      depths are reported in meters.

    Returns ``None`` when no column is recognizable as a depth, rather than
    falling back to an arbitrary column.
    """
    best, best_score = None, -1e9
    for column in df.columns:
        if not is_depth_column(column):
            continue
        if numeric_only and not pd.api.types.is_numeric_dtype(df[column]):
            continue
        header = normalize_header(column)
        score = _SCALE_PRIORITY.get(depth_scale(column) or "", 40)
        if "top" in header:
            score += 5
        if "bottom" in header or "bot" in header.split():
            score -= 20
        if "sample" in header:
            score += 3
        if "core" in header.split() or "of core" in header:
            score -= 15
        if "interval" in header:
            score -= 30
        factor, _ = depth_unit_factor(column)
        if factor < 1.0:
            score -= 30
        if score > best_score:
            best, best_score = column, score
    return best


def depth_columns(df: pd.DataFrame) -> list[str]:
    """All numeric columns recognizable as depths, in table order."""
    return [c for c in df.columns if is_depth_column(c) and pd.api.types.is_numeric_dtype(df[c])]


def measurement_columns(df: pd.DataFrame) -> list[str]:
    """Numeric columns that hold observations rather than depths or identifiers."""
    return [
        c for c in df.columns
        if pd.api.types.is_numeric_dtype(df[c])
        and not pd.api.types.is_bool_dtype(df[c])
        and identifier_kind(c) is None
        and not is_depth_column(c)
    ]


# ---------------------------------------------------------------------------
# Hole grouping
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HoleKeyColumns:
    """Identifier columns that locate rows at the Expedition/Site/Hole level."""

    expedition: str | None
    site: str | None
    hole: str | None

    @property
    def complete(self) -> bool:
        """True if Site and Hole are both present (Expedition is optional)."""
        return self.site is not None and self.hole is not None


def hole_key_columns(df: pd.DataFrame) -> HoleKeyColumns:
    """Locate the Expedition, Site, and Hole columns of a table."""
    return HoleKeyColumns(
        expedition=find_identifier_column(df, "expedition"),
        site=find_identifier_column(df, "site"),
        hole=find_identifier_column(df, "hole"),
    )


def normalize_identifier(value: object) -> str:
    """Normalize a Leg/Site/Hole value for comparison.

    Removes surrounding whitespace, ignores case, and maps float
    representations of integers (``"29.0"``, produced when pandas reads an
    integer column containing blanks) to the integer form (``"29"``).
    """
    text = str(value).strip().lower()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text


def group_key(df: pd.DataFrame, level: str = "hole",
              include_expedition: bool = True) -> pd.Series | None:
    """Build a normalized grouping key at the Site or Hole level.

    Parameters
    ----------
    df
        Input table.
    level
        ``"hole"`` for Expedition/Site/Hole groups, ``"site"`` for
        Expedition/Site groups.
    include_expedition
        Prefix the key with the Expedition when that column exists. Set to
        false when the key must match a table that lacks an Expedition
        column.

    Returns
    -------
    pandas.Series or None
        String key per row, or ``None`` if the required identifier columns
        are absent.
    """
    keys = hole_key_columns(df)
    required = [keys.site] + ([keys.hole] if level == "hole" else [])
    if any(c is None for c in required):
        return None
    use_expedition = include_expedition and keys.expedition is not None
    parts = ([keys.expedition] if use_expedition else []) + required
    key = df[parts[0]].map(normalize_identifier)
    for column in parts[1:]:
        key = key + "|" + df[column].map(normalize_identifier)
    return key
