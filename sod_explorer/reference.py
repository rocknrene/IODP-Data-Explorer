"""Static reference table of drilled Expedition/Site/Hole combinations.

The table (``data/exp_site_hole.csv``) lists every Leg or Expedition, Site,
and Hole in the reference compilation together with the scientific program
(DSDP, ODP, IODP) and drilling platform (Glomar Challenger, JOIDES
Resolution, Chikyu, or Mission-Specific Platform, MSP). It populates the
selection menus so that only combinations that were actually drilled can be
requested, and it determines which data source is queried for a request
(see :mod:`sod_explorer.sources.routing`).

Provenance of the table (compilation source and snapshot date) is recorded
in ``data/README.md``.

Conventions
-----------
* All identifiers are stored as strings. Expedition identifiers may carry a
  letter suffix (e.g. ``343T`` for a transit expedition, ``395C`` for a
  complementary expedition), so they are not purely numeric.
* A Hole value of ``*`` denotes an early DSDP hole drilled before hole
  letters were assigned; it is a placeholder, not a hole designation, and is
  never sent to a data service as a filter value.
* An Expedition may be listed with empty Site and Hole values when its
  platform is known but its holes have not yet been entered. It can then be
  selected and routed to the correct archive, with Site and Hole left blank.
"""

from __future__ import annotations

from importlib import resources

import pandas as pd

#: Hole placeholder for DSDP holes that predate hole lettering.
UNLETTERED_HOLE = "*"

_REFERENCE_COLUMNS = ["Exp", "Site", "Hole", "program", "vessel"]


def load_reference_table() -> pd.DataFrame:
    """Load the Expedition/Site/Hole reference table.

    Returns
    -------
    pandas.DataFrame
        Columns ``Exp``, ``Site``, ``Hole``, ``program``, ``vessel``, all of
        string dtype.
    """
    with resources.files("sod_explorer.data").joinpath("exp_site_hole.csv").open("rb") as fh:
        table = pd.read_csv(fh, dtype=str, keep_default_na=False)
    missing = set(_REFERENCE_COLUMNS) - set(table.columns)
    if missing:
        raise ValueError(f"Reference table is missing columns: {sorted(missing)}")
    return table[_REFERENCE_COLUMNS]


REFERENCE = load_reference_table()


def natural_sort_key(value: str) -> tuple:
    """Sort key that orders numeric identifiers numerically.

    Purely numeric identifiers sort first in numeric order (1, 2, ..., 400);
    identifiers with a letter suffix (``343T``) sort after them by their
    numeric prefix, then by suffix.
    """
    text = str(value)
    digits = ""
    for char in text:
        if char.isdigit():
            digits += char
        else:
            break
    suffix = text[len(digits):]
    return (int(digits) if digits else float("inf"), suffix)


ALL_EXPEDITIONS: list[str] = sorted(REFERENCE["Exp"].unique().tolist(), key=natural_sort_key)


def sites_for_expedition(expedition: str | None) -> list[str]:
    """Return the Sites drilled during an Expedition, in natural order."""
    if not expedition:
        return []
    subset = REFERENCE[(REFERENCE["Exp"] == str(expedition)) & (REFERENCE["Site"] != "")]
    return sorted(subset["Site"].unique().tolist(), key=natural_sort_key)


def holes_for_site(expedition: str | None, site: str | None) -> list[str]:
    """Return the Holes drilled at a Site during an Expedition."""
    if not expedition or not site:
        return []
    subset = REFERENCE[(REFERENCE["Exp"] == str(expedition)) & (REFERENCE["Site"] == str(site))
                       & (REFERENCE["Hole"] != "")]
    return sorted(subset["Hole"].unique().tolist())


def hole_display_label(hole: str) -> str:
    """Menu label for a Hole value.

    The unlettered-hole placeholder is shown as ``*`` alone; its meaning is
    explained in the documentation rather than in the menu.
    """
    return hole


def _single_value(expedition: str, column: str) -> str | None:
    """Value of a reference-table column for an Expedition, or ``None`` if absent or not unique."""
    values = REFERENCE.loc[REFERENCE["Exp"] == str(expedition), column].unique().tolist()
    return values[0] if len(values) == 1 else None


def platform_for_expedition(expedition: str) -> str | None:
    """Drilling platform for an Expedition, or ``None`` if absent or ambiguous."""
    return _single_value(expedition, "vessel")


def program_for_expedition(expedition: str) -> str | None:
    """Scientific program for an Expedition, or ``None`` if absent or ambiguous."""
    return _single_value(expedition, "program")


def program_from_number(expedition: str) -> str | None:
    """Infer the program from the Leg/Expedition number alone.

    Used only when an identifier is not in the reference table. Numbering
    ranges: DSDP Legs 1 to 96, ODP Legs 100 to 210, IODP Expeditions 301
    onward. Identifiers outside these ranges return ``None``.
    """
    digits = "".join(ch for ch in str(expedition).strip() if ch.isdigit())
    if not digits or digits != str(expedition).strip():
        return None
    number = int(digits)
    if 1 <= number <= 96:
        return "DSDP"
    if 100 <= number <= 210:
        return "ODP"
    if number >= 301:
        return "IODP"
    return None
