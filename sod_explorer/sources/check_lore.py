"""Check which candidate LORE report names LORE recognizes.

Run from a computer with internet access::

    python -m sod_explorer.sources.check_lore

For every LIMS report in the catalog, each candidate name is submitted to
LORE's header service and the outcome is printed. Names that resolve can be
added to :data:`sod_explorer.sources.catalog.VERIFIED_LORE_NAMES`. For a
report with no resolving candidate, open that report in LORE with the
browser's developer tools on the Network tab; the request to
``HeaderDisplayGet-LORE`` carries the report name in its ``name`` parameter.
Add that name to the report's candidate list in the catalog.
"""

from __future__ import annotations

from . import lore
from .catalog import REPORT_TYPES, VERIFIED_LORE_NAMES
from .common import SourceError


def main() -> int:
    """Print the resolution of every LIMS report; return the number unresolved."""
    session = lore._session()
    unresolved = 0
    for report_type in REPORT_TYPES:
        for lims_report in report_type.lore_reports:
            try:
                name, headers = lore.resolve_report_name(session, lims_report)
            except SourceError as exc:
                unresolved += 1
                print(f"UNRESOLVED  {report_type.label} [{lims_report.code}]: {exc}")
                continue
            status = "verified  " if name in VERIFIED_LORE_NAMES else "RESOLVED  "
            print(f"{status}  {report_type.label} [{lims_report.code}] -> '{name}' ({len(headers)} columns)")
    print(f"\n{unresolved} report(s) unresolved.")
    return unresolved


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
