---
title: IODP Explorer
emoji: 🌊
colorFrom: blue
colorTo: green
sdk: docker
pinned: false
license: mit
short_description: IODP data visualization tool
---

# IODP Explorer

[![DOI](https://zenodo.org/badge/1267426154.svg)](https://doi.org/10.5281/zenodo.20669063)

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

## Citation

If IODP Explorer is used in your research, please cite:

> Castillo, R. (2025). *IODP Explorer: Interactive Data Visualization Dashboard for IODP Core and Downhole Data* (v1.0.0). Zenodo. https://doi.org/10.5281/zenodo.20669063

## Live App

Available at: https://huggingface.co/spaces/rocknrene/IODP-Data-Explorer
