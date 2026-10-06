"""Record how LORE serves its composite ("COMPLEX") reports.

Development aid, run by the ``live checks`` workflow. LORE's menu
(``/reference/NavHierarchyGet-LORE``) lists two kinds of report. Standard
reports are served directly by the header and display services. Composite
reports (Carbonates, Interstitial Water, Gas Elements) are assembled in the
browser from an *internal* report, according to a report definition
(``/reference/ReportDefinitionGet-LORE``).

For each composite report this module prints the report definition, the
column headers of the internal report, the number of tests returned for
one Hole under several forms of the search filter, and the first rows of
the internal report, so that the assembly can be reproduced and tested.
Output goes to standard output.
"""

from __future__ import annotations

import json
import time

import requests

HOST = "https://web.iodp.tamu.edu"
PAGE = f"{HOST}/LORE/"
HEADERS = {"User-Agent": "SOD-Explorer/2.0 (report discovery; github.com/rocknrene/IODP-Data-Explorer)",
           "Referer": PAGE, "X-Requested-With": "XMLHttpRequest"}
COMPOSITE_REPORTS = ("carbonates", "iw", "gaselements")
STANDARD_REPORTS = ("penetrate", "sranl", "pwcdiscrete")
EXPEDITION, SITE, HOLE = "362", "U1480", "E"
SCALE_ID = "11331"
ROWS_SHOWN = 8


def get_json(session: requests.Session, path: str, params: dict):
    time.sleep(0.5)
    response = session.get(f"{HOST}{path}", params=params, timeout=60)
    if response.status_code != 200:
        return f"HTTP {response.status_code}: {response.text[:200]!r}"
    try:
        return response.json()
    except ValueError:
        return f"not JSON: {response.text[:200]!r}"


def working_set(session: requests.Session, report: str, filters: list[str]):
    return get_json(session, "/limsM/AWorkingSetGet-LORE", {
        "username": "GUEST", "password": "guest", "report": report,
        "postretrieve": json.dumps({"scale_id": SCALE_ID}), "filters": json.dumps(filters)})


def show_rows(session: requests.Session, report: str, ids: list) -> None:
    rows = get_json(session, "/limsM/DisplayGet-LORE", {
        "name": report, "id": "[" + ",".join(str(i) for i in ids[:40]) + "]",
        "postretrieve": json.dumps({"scale_id": SCALE_ID}),
        "username": "GUEST", "password": "guest", "nolink": "true"})
    if not isinstance(rows, list):
        print(f"   rows: {rows}")
        return
    print(f"   {len(rows)} rows for the first {min(len(ids), 40)} tests; first {ROWS_SHOWN}:")
    for row in rows[:ROWS_SHOWN]:
        print(f"   {json.dumps(row)}")


def describe(session: requests.Session, report: str, extra_filters: list[str]) -> None:
    headers = get_json(session, "/reference/HeaderDisplayGet-LORE",
                       {"name": report, "scaleid": SCALE_ID, "splice": "test"})
    print(f"-- headers of {report}: {json.dumps(headers)}")
    location = [f"x_expedition in ('{EXPEDITION}')", f"x_site in ('{SITE}')", f"x_hole in ('{HOLE}')"]
    variants = {"location only": location}
    if extra_filters:
        variants["location + definition filters"] = location + extra_filters
        variants["location + definition filters without scale"] = (
            location + [f for f in extra_filters if "scale_id" not in f])
    shown = False
    for label, filters in variants.items():
        ids = working_set(session, report, filters)
        count = len(ids) if isinstance(ids, list) else ids
        print(f"   working set ({label}): {count}")
        if isinstance(ids, list) and ids and not shown:
            show_rows(session, report, ids)
            shown = True


def main() -> None:
    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        session.get(PAGE, timeout=60)
    except requests.RequestException as exc:
        print(f"LORE page not reachable: {exc}")

    print("== 1. Composite reports")
    for name in COMPOSITE_REPORTS:
        definition = get_json(session, "/reference/ReportDefinitionGet-LORE", {"name": name})
        if isinstance(definition, dict) and isinstance(definition.get("definition"), str):
            try:   # the definition is returned as a JSON string inside a JSON object
                definition = json.loads(definition["definition"])
            except ValueError:
                pass
        print(f"\n==== {name}\n-- definition:\n{json.dumps(definition, indent=1)}")
        if not isinstance(definition, dict):
            continue
        internal = definition.get("internalreport")
        if internal:
            describe(session, internal, list(definition.get("searchfilters") or []))
        if definition.get("reportservice"):
            print(f"-- report service: {definition['reportservice']}")

    print("\n== 2. Standard reports")
    for name in STANDARD_REPORTS:
        print(f"\n==== {name}")
        describe(session, name, [])


if __name__ == "__main__":
    main()
