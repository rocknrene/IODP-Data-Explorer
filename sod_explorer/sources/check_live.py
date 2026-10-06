"""Live checks of the data sources.

Run from a computer (or a GitHub Actions runner) with internet access::

    python -m sod_explorer.sources.check_live

The test suite replaces every network service with recorded responses, so
it cannot detect a change in a live service or a block on the network the
application is deployed to. This module makes one small real request to
each source and prints whether it succeeded:

* LORE: moisture and density for Expedition 362, Hole U1480E.
* PANGAEA: a dataset search for Expedition 343 (Chikyu), and a download of
  the first dataset found.
* DSDP Data Access application: density and porosity for Leg 29, Site 277
  (requires Chromium and chromedriver; see
  :mod:`sod_explorer.sources.shinylaurel`).

Each check is independent; a failure of one does not stop the others.
"""

from __future__ import annotations

import time
import traceback

from . import lore, pangaea, shinylaurel


def _lore() -> str:
    df, source = lore.fetch("mad", "362", "U1480", "E")
    return f"{len(df)} rows, {len(df.columns)} columns; reports {source['query']['lims_reports']}"


def _pangaea() -> str:
    results = pangaea.search('"Expedition 343" Chikyu', count=5)
    if not results:
        return "search returned no datasets (service reachable)"
    df, source = pangaea.fetch_dataset(results[0]["value"])
    return (f"{len(results)} datasets found; downloaded {results[0]['value']} "
            f"({len(df)} rows); citation present: {bool(source.get('citation'))}")


def _dsdp() -> str:
    df, source = shinylaurel.fetch("mad", "29", "277")
    return f"{len(df)} rows of {source['rows_downloaded']} downloaded; columns {list(df.columns)[:6]}"


CHECKS = (("LORE", _lore), ("PANGAEA", _pangaea), ("DSDP Data Access", _dsdp))


def main() -> int:
    """Run every check, print one line per source, and return the number that failed."""
    failures = 0
    for name, check in CHECKS:
        started = time.time()
        try:
            detail = check()
        except Exception as exc:  # report any failure of a live service, whatever its type
            failures += 1
            print(f"FAIL  {name} ({time.time() - started:.0f} s): {type(exc).__name__}: {exc}")
            traceback.print_exc(limit=1)
        else:
            print(f"PASS  {name} ({time.time() - started:.0f} s): {detail}")
    print(f"\n{failures} of {len(CHECKS)} source(s) failed.")
    return failures


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
