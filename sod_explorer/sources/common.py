"""Shared types and helpers for the data-source clients."""

from __future__ import annotations

import re

import pandas as pd

from ..columns import find_identifier_column, normalize_identifier
from ..reference import UNLETTERED_HOLE

#: User-Agent sent to every service. Identifies the software and a contact
#: URL so that service operators can distinguish it from automated scraping.
USER_AGENT = ("SOD-Explorer/2.0 (scientific ocean drilling data viewer; "
              "https://huggingface.co/spaces/rocknrene/IODP-Data-Explorer)")

_IDENTIFIER = re.compile(r"^[A-Za-z0-9]{1,8}$")


class SourceError(RuntimeError):
    """Raised when a data source returns no usable data.

    The message is written for display to the user.
    """


def validate_identifier(value: str, name: str) -> str:
    """Check that a Leg/Site/Hole value is a short alphanumeric token.

    Values are interpolated into service query strings, so anything other
    than letters and digits is rejected.
    """
    value = (value or "").strip()
    if value and not _IDENTIFIER.match(value):
        raise SourceError(f"Invalid {name} identifier: {value!r}")
    return value


def restrict_to_request(df: pd.DataFrame, expedition: str | None, site: str | None,
                        hole: str | None) -> pd.DataFrame:
    """Keep only rows that match the requested Leg/Expedition, Site, and Hole.

    Services do not always filter reliably (for example, LORE can return
    rows from other holes when a filter value is not recognized, and
    bulk downloads can include other Legs), so every returned table is
    re-filtered here. A blank request value, or the unlettered-hole
    placeholder ``*``, does not filter. A request value is ignored if the
    table has no corresponding identifier column.
    """
    for kind, wanted in (("expedition", expedition), ("site", site), ("hole", hole)):
        if not wanted or str(wanted).strip() == UNLETTERED_HOLE:
            continue
        column = find_identifier_column(df, kind)
        if column is not None:
            df = df[df[column].map(normalize_identifier) == normalize_identifier(wanted)]
    return df.reset_index(drop=True)


def describe_request(expedition: str, site: str = "", hole: str = "") -> str:
    """Compact label such as ``"Exp 362 U1480E"``."""
    location = f"{site}{hole if hole != UNLETTERED_HOLE else ''}"
    return " ".join(part for part in (f"Exp {expedition}", location) if part.strip())
