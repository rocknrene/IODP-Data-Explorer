---
title: IODP Explorer
emoji: 🌊
colorFrom: blue
colorTo: green
sdk: docker
pinned: false
license: mit
short_description: Interactive IODP data visualization tool
---

# IODP Explorer

An interactive dashboard for exploring data from the International Ocean Discovery Program.

Upload CSV, Excel, or LAS files directly from the IODP data archive and visualize them as scatter plots, depth logs, histograms, and correlation heatmaps — no coding required.

## Features

- Depth log viewer with shared depth axis
- Automatic site metadata detection from JCORES sample IDs
- Optional lithology track with flexible column name detection
- Recovery gap hatching, QC flag markers, and core-top tick marks
- Correlation heatmap across all numeric columns
- Sortable, filterable data table

## Supported file formats

- `.csv` — auto-detects encoding and header row
- `.xlsx` / `.xls` — auto-detects header row, handles preamble rows
- `.las` — standard well log format
