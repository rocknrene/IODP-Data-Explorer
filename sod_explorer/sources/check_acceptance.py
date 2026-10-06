"""Acceptance checks of retrieval, merging, and charts against the live archives.

Run from a computer (or a GitHub Actions runner) with internet access::

    python -m sod_explorer.sources.check_acceptance
    python -m sod_explorer.sources.check_acceptance --all-types

The checks follow the rapid test plan prepared for GSA Connects 2026
(L. B. Childress, October 2026). Each check performs the operation the
application performs when a user clicks: the routed request, storage of the
table as the browser holds it, the depth merge, and construction of the
chart. The checks do not operate a browser; the interface itself is
exercised separately.

Checks
------
1. One Hole of DSDP, ODP, and IODP data.
2. An entire Leg or Expedition of DSDP, ODP, and IODP data.
3. Merges within and between programs. Datasets from one Hole are paired
   within the Hole; datasets from different Sites are paired on depth alone
   (the ``across_holes`` option of
   :func:`sod_explorer.analysis.merge_by_depth`).
4. Depth tracks and the cross-plot with correlation, for a single dataset
   and for a merged dataset.
5. With ``--all-types``, every report type for one Hole of each program
   (informative: a report type with no data for the Hole is listed as
   ``none`` and is not a failure).

The exit status is the number of failed checks in parts 1 to 4.
"""

from __future__ import annotations

import sys
import time
import traceback

import pandas as pd

from ..analysis import merge_by_depth
from ..callbacks import from_store, to_store
from ..columns import find_depth_column, measurement_columns
from ..plotting import correlation_figure, tracks_figure
from . import routing
from .catalog import REPORT_TYPES

#: One Hole of each program: (Leg/Expedition, Site, Hole).
HOLE = {"DSDP": ("29", "277", "*"), "ODP": ("204", "1244", "C"), "IODP": ("362", "U1480", "E")}
#: Two report types measured in that Hole, for merging within a program.
PAIR = {"DSDP": ("mad", "pwave_split"), "ODP": ("mad", "carbonates"), "IODP": ("mad", "carbonates")}
WITHIN_TOLERANCE_M = 0.5
ACROSS_TOLERANCE_M = 2.0

_tables: dict[tuple, pd.DataFrame] = {}
_results: list[tuple[str, bool]] = []


def load(report: str, expedition: str, site: str = "", hole: str = "") -> pd.DataFrame:
    """Routed request, passed through the browser store as in the application."""
    key = (report, expedition, site, hole)
    if key not in _tables:
        outcome = routing.fetch(report, expedition, site, hole)
        if outcome.df is None:
            raise RuntimeError(outcome.message)
        stored = to_store(outcome.df)
        table = from_store(stored)
        table.attrs["message"] = f"{outcome.message}; {len(stored) / 1e6:.2f} MB in the browser"
        _tables[key] = table
    return _tables[key]


def describe(table: pd.DataFrame) -> str:
    depth = find_depth_column(table)
    return (f"{table.attrs.get('message', '')}; {len(table)} rows; depth column {depth!r}; "
            f"{len(measurement_columns(table))} measurement columns")


def run(name: str, check, counted: bool = True) -> None:
    start = time.time()
    try:
        detail, ok = check(), True
    except Exception as exc:   # report every failure and continue with the other checks
        detail, ok = f"{type(exc).__name__}: {exc}", False
        if not isinstance(exc, RuntimeError):
            traceback.print_exc(file=sys.stdout)
    label = "PASS" if ok else ("FAIL" if counted else "none")
    print(f"{label}  {name} ({time.time() - start:.0f} s): {detail}", flush=True)
    if counted:
        _results.append((name, ok))


def one_hole(program: str) -> str:
    table = load("mad", *HOLE[program])
    if find_depth_column(table) is None:
        raise AssertionError(f"no depth column found among {list(table.columns)}")
    return describe(table)


def whole_leg(program: str) -> str:
    table = load("mad", HOLE[program][0])
    if find_depth_column(table) is None:
        raise AssertionError(f"no depth column found among {list(table.columns)}")
    return describe(table)


def merged(program_a: str, program_b: str) -> tuple[pd.DataFrame, str]:
    same = program_a == program_b
    report_a, report_b = PAIR[program_a] if same else ("mad", "mad")
    a, b = load(report_a, *HOLE[program_a]), load(report_b, *HOLE[program_b])
    depth_a, depth_b = find_depth_column(a), find_depth_column(b)
    table, report = merge_by_depth(
        a, b, depth_a, depth_b, tolerance_m=WITHIN_TOLERANCE_M if same else ACROSS_TOLERANCE_M,
        allow_mixed_scales=False, one_to_one=True, across_holes=not same)
    if report.matched == 0:
        raise AssertionError(f"no pairs formed; warnings: {report.warnings}")
    table = from_store(to_store(table))
    return table, (f"{report_a} with {report_b}: {report.matched} of {report.rows_a_with_depth} A samples "
                   f"paired (depths {depth_a!r} and {depth_b!r}; scales {report.depth_scale_a} and "
                   f"{report.depth_scale_b}; grouping {report.grouping})")


def charts_single() -> str:
    table = load("mad", *HOLE["IODP"])
    depth, columns = find_depth_column(table), measurement_columns(table)[:2]
    tracks = tracks_figure(table, depth, columns, [], "dark")
    cross = correlation_figure(table, depth, columns[0], columns[1], "light")
    title = cross.layout.title.text or ""
    return f"tracks: {len(tracks.data)} traces; cross-plot: {len(cross.data)} traces; title {title!r}"


def charts_merged() -> str:
    table, _ = merged("IODP", "IODP")
    depth = find_depth_column(table) or table.columns[0]
    columns = measurement_columns(table)
    columns_a = [c for c in columns if c.endswith("_A")][:1]
    columns_b = [c for c in columns if c.endswith("_B")][:1]
    tracks = tracks_figure(table, depth, columns_a, columns_b, "dark")
    cross = correlation_figure(table, depth, columns_a[0], columns_b[0], "dark", detrend=True)
    return (f"depth {depth!r}; {columns_a[0]!r} against {columns_b[0]!r}; tracks: {len(tracks.data)} "
            f"traces; cross-plot title {cross.layout.title.text!r}")


def main() -> None:
    if "--all-types" in sys.argv:
        print("== 5. Every report type, one Hole of each program (informative)")
        for program in HOLE:
            for report in REPORT_TYPES:
                run(f"{program} {report.label}", lambda p=program, r=report: describe(load(r.key, *HOLE[p])),
                    counted=False)
        return

    print("== 1. One Hole")
    for program in HOLE:
        run(f"{program} one Hole {HOLE[program]}", lambda p=program: one_hole(p))
    print("\n== 2. Entire Leg or Expedition")
    for program in HOLE:
        run(f"{program} entire Leg/Expedition {HOLE[program][0]}", lambda p=program: whole_leg(p))
    print("\n== 3. Merges")
    for a, b in (("DSDP", "DSDP"), ("ODP", "ODP"), ("IODP", "IODP"),
                 ("DSDP", "IODP"), ("ODP", "IODP"), ("DSDP", "ODP")):
        run(f"merge {a} with {b}", lambda a=a, b=b: merged(a, b)[1])
    print("\n== 4. Charts")
    run("depth tracks and cross-plot, single dataset", charts_single)
    run("depth tracks and cross-plot, merged dataset", charts_merged)

    failures = [name for name, ok in _results if not ok]
    print(f"\n{len(failures)} of {len(_results)} check(s) failed"
          + (": " + "; ".join(failures) if failures else "."))
    sys.exit(len(failures))


if __name__ == "__main__":
    main()
