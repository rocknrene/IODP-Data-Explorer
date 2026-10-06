---
title: SOD Explorer
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: Scientific ocean drilling data visualization tool
---
 
 SOD Explorer

SOD (Scientific Ocean Drilling) Explorer is a web application and Python
package for retrieving,
depth-registering, and visualizing scientific ocean drilling data from the
Deep Sea Drilling Project (DSDP), the Ocean Drilling Program (ODP), and the
International Ocean Discovery Program (IODP).

Live application: <https://huggingface.co/spaces/rocknrene/IODP-Data-Explorer>

## Motivation

Sixty years of scientific ocean drilling data are distributed across
several archives with different access methods, vocabularies, and depth
conventions: the LIMS database of the JOIDES Resolution Science Operator
(through LORE), PANGAEA, the legacy DSDP database, and J-CORES for the
Chikyu. Comparing two measurements from the same hole, or the same
measurement from two expeditions, usually requires locating the right
archive, downloading and reformatting tables by hand, and pairing samples
by depth in a spreadsheet.

SOD Explorer selects the archive from the Leg or Expedition number,
retrieves the requested report, and pairs samples from two datasets by
depth within a stated tolerance, restricted to the same hole and depth
scale. Every dataset carries a machine-readable provenance record, and every
export includes the citations the source archives require.

## Features

**Legacy Data view** (one dataset, or two merged by depth)
- Retrieval by Leg/Expedition, Site, Hole, and report type from the
  appropriate archive (see *Data sources*), or file upload.
- A choice between a single dataset and two merged datasets; the dataset B
  and merge controls appear only in merge view.
- Depth-tolerance merge (see *Methods*).
- Depth tracks, a cross-plot with an autocorrelation-adjusted correlation
  test, a dual-axis overlay, and depth-window smoothing.
- Export as a ZIP archive with `data.csv`, `provenance.json`, and
  `CITATION.txt`.

**Shipboard view** (one uploaded file)
- Readers for CSV, TSV, Excel, LAS, and ZIP (including J-CORES bulk exports),
  with automatic detection of the header row below LIMS metadata rows.
- Multi-track depth log with an optional lithology track, sampling-gap
  shading, comment markers, and core-top ticks; one line per hole.
- Scatter, line, histogram, and a correlation matrix restricted to
  measurement columns (identifier columns such as Core, Section, and
  offsets are excluded).

Explanations of each control are behind the ⓘ button beside its section
label. In the Hole menu, `*` denotes an early DSDP hole drilled before hole
letters were assigned.

## Data sources

| Program / platform              | Source                                      | Access method                              |
|---------------------------------|---------------------------------------------|--------------------------------------------|
| DSDP (Glomar Challenger)        | DSDP Data Access application (shinylaurel.com) | Headless browser (Selenium)            |
| IODP (JOIDES Resolution), Exp. 317 onward | LORE (LIMS database)              | LORE JSON services                         |
| ODP; IODP Exp. 301 to 312 (JOIDES Resolution) | NOAA NCEI archive              | File upload (reader not yet implemented)   |
| IODP (Chikyu)                   | PANGAEA; otherwise J-CORES                  | PANGAEA search API; J-CORES by upload      |
| IODP (Mission-Specific Platform)| PANGAEA                                     | PANGAEA search API                         |

Program and platform assignments, and the primary archive for each, follow
the *Scientific Ocean Drilling Legacy Data Access: Quick Start Guide*
(Childress, 2026). The assignments are read from the reference table in
`sod_explorer/data/` (see its README for contents and provenance). Retrieved tables are always re-filtered to the requested Leg,
Site, and Hole, because the services do not filter reliably in every case.

## Methods

### Depth scales

Depth scales are inferred from column headers using the IODP depth-scale
terminology (IODP-MI, 2011). The legacy scales "mbsf" and "mcd" are treated
as CSF-A and CCSF-A, respectively. Axis labels use the column header, so the
scale is always visible; no scale is assumed.

### Depth-tolerance merge (`analysis.merge_by_depth`)

1. If both depth scales are recognized and differ, the merge is refused
   unless the user explicitly allows mixed scales.
2. Depths are converted to meters from the unit in each header (meters are
   assumed, with a warning, when no unit is stated).
3. Pairs are restricted to the same hole. If both datasets are on a
   composite scale (CCSF, mcd), pairs are restricted to the same site
   instead. Without Site/Hole columns, rows are paired on depth alone and a
   warning is recorded.
4. Each A sample is paired with the nearest B sample within the tolerance.
5. By default each B sample is used at most once (it is kept for the
   nearest A sample), so that one measurement is not counted several times
   in later statistics.

The merge report (pairs formed, duplicates removed, median depth offset,
warnings) is shown in the interface and stored in the export provenance.

### Correlation of downhole series (`analysis.correlate`)

Adjacent downhole samples are not independent, so a conventional Pearson
test understates p-values. SOD Explorer reports Pearson's r with a
t-test and a Fisher-z 95% confidence interval based on an effective sample
size

    n_eff = n (1 - r1x r1y) / (1 + r1x r1y)

where r1x and r1y are the lag-1 autocorrelations of the two series ordered
by depth within each hole (Bretherton et al., 1999). In 2,000 simulated
pairs of independent AR(1) series (phi = 0.85, n = 200), the unadjusted
Pearson test reported p < 0.05 for 43% of pairs and the adjusted test for
4.6%, close to the nominal 5%; the test suite repeats a smaller version of
this check. The approximation assumes AR(1) behavior and roughly uniform
sample spacing.

Two properties that both change monotonically with depth (for example,
through compaction) correlate strongly through the shared trend; their
lag-1 autocorrelations then approach 1, n_eff approaches its lower bound
of 2, and the interface reports that significance cannot be assessed. The
cross-plot offers an option to remove a linear depth trend from each
property (per hole) and correlate the residuals, which tests whether the
deviations from the two trends co-vary.

### Depth-window smoothing (`analysis.depth_window_mean`)

A centered mean over a fixed depth interval, computed separately for each
hole. Because the window is defined in meters rather than rows, its physical
length does not change with sampling density and it does not extend across
gaps wider than the window.

### Sampling gaps, core tops, comment markers

- *Sampling gaps* are intervals wider than a threshold between consecutive
  samples of one hole. They mark missing measurements, not core recovery.
- *Core tops* are the shallowest sampled depth in each core, an
  approximation of the curated core top.
- *Comment markers* flag rows with a non-empty comment field; they are a
  screening aid, not a quality classification.

## Provenance and citation of data

Each dataset in the application carries a provenance record with the
source, request parameters, retrieval time (UTC), source file SHA-256 (for
uploads), depth column and scale, and processing steps. Merged datasets
include the records of both inputs and the merge report. The schema
identifier is `sod-explorer-provenance/1`.

`CITATION.txt` in each export lists the PANGAEA dataset citations (with DOI)
and citation guidance for LIMS and DSDP data (the expedition *Proceedings*
and *Initial Reports* volumes).

## Installation

Python 3.11 or later.

```bash
pip install .            # library and application
pip install ".[dsdp]"    # adds Selenium for DSDP retrieval (also requires Chromium and chromedriver)
```

For an exact reproduction of the tested environment:

```bash
pip install -r requirements.txt
```

## Running

```bash
python app.py                      # http://localhost:7860
gunicorn app:server -b 0.0.0.0:7860
```

DSDP retrieval reads the Chromium and chromedriver locations from the
`CHROME_BIN` and `CHROMEDRIVER_PATH` environment variables (defaults
`/usr/bin/chromium` and `/usr/bin/chromedriver`).

## Using the library

```python
from sod_explorer.sources import routing
from sod_explorer.analysis import merge_by_depth, correlate

a = routing.fetch("mad", "362", "U1480", "E").df
b = routing.fetch("pwave", "362", "U1480", "E").df
merged, report = merge_by_depth(a, b, "Depth CSF-A (m)", "Depth CSF-A (m)", tolerance_m=0.02)
print(report.matched, report.warnings)
```

## Testing

```bash
pip install ".[test]"
pytest
```

Network services are replaced by recorded or synthetic responses, so the
suite runs offline. Continuous integration runs the suite and `ruff` on
Python 3.11 to 3.13 (`.github/workflows/tests.yml`).

## Package layout

```
sod_explorer/
  reference.py     Expedition/Site/Hole reference table and program lookup
  columns.py       Depth, identifier, and measurement column classification
  parsing.py       File readers
  analysis.py      Merge, correlation, smoothing, gaps, core tops
  provenance.py    Provenance records and export archives
  sources/         LORE, PANGAEA, DSDP clients and routing rules
  plotting.py      Figure builders
  layout.py        Page layout
  callbacks.py     Dash callbacks
  app.py           Application factory
app.py             Deployment entry point
tests/             Test suite and fixtures
```

## Known limitations

- ODP Legs and IODP Expeditions 301 to 312 are not served by LORE; a reader
  for the NOAA NCEI JOIDES Resolution archive is planned. Until then these
  data must be uploaded as files.
- Sites and holes for Expeditions 380, 389, and 405 are not yet in the
  reference table; these expeditions can be selected, but only with Site and
  Hole left blank.
- DSDP retrieval automates a third-party web application and will fail if
  its page layout changes.
- The merge pairs nearest samples; it does not interpolate.
- The effective-sample-size correction is an approximation (see *Methods*).

## How to cite

See `CITATION.cff`. Please also cite the source datasets listed in the
`CITATION.txt` file of each export.

## References

Bretherton, C. S., Widmann, M., Dymnikov, V. P., Wallace, J. M., & Bladé, I.
(1999). The effective number of spatial degrees of freedom of a
time-varying field. *Journal of Climate*, 12(7), 1990-2009.

Childress, L. B. (2026). *Scientific Ocean Drilling Legacy Data Access: Quick
Start Guide*, version 1.0. Gulf Coast Repository, Texas A&M University.

IODP-MI (2011). *IODP Depth Scales Terminology*, version 2.0.

## Acknowledgments

Developed during an internship at the IODP Gulf Coast Repository, Texas A&M
University.

## License

MIT (see `LICENSE`).
