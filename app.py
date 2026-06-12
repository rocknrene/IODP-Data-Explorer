# =============================================================================
# IODP Explorer -- Interactive Data Visualization Dashboard
# =============================================================================
#
# This app lets scientists explore International Ocean Discovery Program (IODP)
# core data. It has two main modes:
#
#   SHIPBOARD       Load a single data file and explore it with charts and stats.
#                   Designed for use during an expedition while coring is ongoing.
#
#   POST-EXPEDITION Load two datasets from different sources, merge them by depth,
#                   and compare them visually using four science-oriented chart modes.
#
# SUPPORTED FILE TYPES
#   .csv   comma-separated values (most common IODP export format)
#   .xlsx  Excel spreadsheets
#   .las   Log ASCII Standard, used in geophysical well logging
#
# DATA SOURCES IN POST-EXPEDITION MODE
#   Upload / J-CORES  drag-and-drop a local file
#                     (required for Chikyu/J-CORES/KCC/JAMSTEC data)
#   LIMS/LORE         live fetch from the JRSO database at Texas A&M
#                     (JOIDES Resolution expeditions 317 and later)
#   PANGAEA           live fetch from the European data portal at Bremen
#                     (Mission Specific Platform / ECORD expeditions)
#
# HOW TO RUN LOCALLY
#   pip install dash plotly pandas openpyxl lasio requests
#   python app.py
#   Open http://localhost:7860 in your browser.
#
# DEPLOYMENT
#   Hosted on Hugging Face Spaces. The server (gunicorn) imports this file
#   and calls the "server" object, which is a standard Flask app.
#   Port 7860 is the Hugging Face Spaces default.
#
# =============================================================================

# --- Standard library imports -------------------------------------------------
import io          # read files in memory (no temp files on disk)
import base64      # decode uploaded files (Dash sends them as base64 text)
import re          # regular expressions for pattern matching in text
import json        # build URL filter parameters for LIMS requests
import requests    # download data from the internet (LIMS, PANGAEA)

# --- Scientific computing -----------------------------------------------------
import numpy as np   # math and array operations
import pandas as pd  # DataFrames -- the main data structure throughout this app

# --- File format support ------------------------------------------------------
import lasio  # reads LAS (Log ASCII Standard) well log files

# --- Plotting -----------------------------------------------------------------
import plotly.graph_objects as go   # low-level Plotly (full control over charts)
import plotly.express as px         # high-level Plotly shortcuts
from plotly.subplots import make_subplots  # side-by-side chart panels

# --- Web app framework --------------------------------------------------------
from dash import Dash, dcc, html, Input, Output, State, dash_table
# Dash turns Python into an interactive browser app.
# dcc  = Dash Core Components: dropdowns, sliders, file uploaders, graphs
# html = HTML elements: divs, paragraphs, buttons
# Input/Output/State = wiring that connects components to callback functions
# dash_table = interactive sortable/filterable data tables

import flask  # the web server Dash runs on top of

# =============================================================================
# COLOR THEMES
# =============================================================================
# Styles throughout the app use these color dictionaries.
# "dark"  -- dark background, easy on screens and in low-light rooms.
# "light" -- white background, better for printing and bright offices.
# The active theme is swapped at runtime via a JavaScript clientside callback
# that updates CSS custom properties on the page root element.
#
THEMES = {
    "dark": dict(
        bg="#0d1117", panel="#161b22", border="#30363d",
        accent="#58a6ff", accent2="#3fb950", accent3="#d2a679",
        text="#e6edf3", muted="#8b949e", danger="#f85149", warn="#d29922",
    ),
    "light": dict(
        bg="#ffffff", panel="#f6f8fa", border="#d0d7de",
        accent="#0969da", accent2="#1a7f37", accent3="#953800",
        text="#1f2328", muted="#656d76", danger="#cf222e", warn="#9a6700",
    ),
}
# Default — overridden at runtime via dcc.Store
C = THEMES["dark"]
# =============================================================================
# SHARED STYLE VARIABLES
# =============================================================================
# Reusable style dictionaries applied to many components.
# In Dash, styles are Python dicts instead of separate CSS files.

# Base Plotly layout applied to every chart figure
PLOT_CFG = dict(
    paper_bgcolor=C["panel"], plot_bgcolor=C["bg"],
    font=dict(color=C["text"], family="monospace"),
    xaxis=dict(gridcolor=C["border"], zerolinecolor=C["border"]),
    yaxis=dict(gridcolor=C["border"], zerolinecolor=C["border"]),
    colorway=[C["accent"], C["accent2"], C["accent3"], "#bc8cff", "#ff7b72"],
    margin=dict(l=55, r=20, t=40, b=50),
)
# Card style -- a bordered box used to wrap sections of content
CARD = dict(background=C["panel"], border=f"1px solid {C['border']}",
            borderRadius="8px", padding="14px")
FONT = "monospace"
# Dropdown style
DD   = {"background": C["panel"], "color": C["text"],
        "border": f"1px solid {C['border']}", "borderRadius": "4px"}
# Section label style -- small uppercase text above sidebar controls
LBL  = {"color": C["muted"], "fontSize": "10px", "letterSpacing": "2px",
        "marginBottom": "4px", "marginTop": "12px", "fontFamily": FONT}
# Text input box style
INP  = {"width": "100%", "background": C["bg"], "color": C["text"],
        "border": f"1px solid {C['border']}", "borderRadius": "4px",
        "padding": "4px 8px", "fontSize": "11px", "fontFamily": FONT,
        "boxSizing": "border-box"}
# Button style factory -- call BTN(color) to get a style dict for a button
BTN  = lambda bg: {"backgroundColor": bg, "color": C["bg"], "border": "none",
                   "borderRadius": "4px", "padding": "6px 12px", "cursor": "pointer",
                   "fontSize": "11px", "marginTop": "6px", "width": "100%",
                   "fontWeight": "700"}

# =============================================================================
# LITHOLOGY COLOR MAP
# =============================================================================
# Maps sediment/rock type names to display colors for the lithology track.
# Colors follow conventions used in IODP core description publications.
LITHO_COLORS = {
    "clay": "#1D9E75", "silty clay": "#5DCAA5", "silt": "#888780",
    "sand": "#EF9F27", "mtd": "#D85A30", "mtd / chaotic": "#D85A30",
    "turbidite": "#378ADD", "hemipelagite": "#3fb950",
    "gravel": "#BA7517", "chalk": "#B5D4F4", "limestone": "#85B7EB",
    "basalt": "#444441", "ash": "#D3D1C7",
}
def litho_color(name):
    return LITHO_COLORS.get(str(name).lower().strip(), "#444441")

# =============================================================================
# DATA REPOSITORY CONFIGURATION
# =============================================================================
# URLs and report types for the three supported online data sources.

# LIMS/LORE -- JRSO Laboratory Information Management System
# Covers JOIDES Resolution (JR) expeditions 317 and later.
# Physical data repository: Gulf Coast Repository (GCR), Texas A&M University. — GCR (TAMU), JR expeditions 317+
LORE_BASE = "http://web.iodp.tamu.edu/LORE/"
LORE_REPORTS = {
    "gra":       "GRA Bulk Density",
    "mad":       "MAD (Moisture and Density)",
    "pwave":     "P-wave Velocity",
    "ngr":       "Natural Gamma Radiation",
    "thermcond": "Thermal Conductivity",
    "wrmsr":     "WRMSL (multi-sensor)",
    "shearstr":  "Vane Shear Strength",
    "xrf":       "Shore XRF Summary",
}

# PANGAEA -- the European data portal run by MARUM, University of Bremen.
# Covers Mission Specific Platform (MSP/ECORD) expeditions.
# Physical data repository: Bremen Core Repository (BCR).
# Direct tabular download: https://doi.pangaea.de/10.1594/PANGAEA.{id}?format=textfile
# Search API: https://www.pangaea.de/api/datasets/search?q=...&count=20
PANGAEA_ES      = "https://ws.pangaea.de/es/pangaea/panmd/_search"
PANGAEA_DOI_DL  = "https://doi.pangaea.de/10.1594/PANGAEA.{pid}?format=textfile"

# J-CORES (KCC/JAMSTEC) -- for Chikyu expeditions (343, 405, 319, etc.)
# Physical repository: Kochi Core Center (KCC), Kochi University / JAMSTEC.
# There is no public REST API for J-CORES, so those files must be uploaded manually. — upload only. Label this clearly in the UI.

# =============================================================================
# FILE PARSING HELPERS
# =============================================================================
# Keywords likely to appear in a real data header row.
# Used to automatically skip title/metadata rows that appear before the data
# in IODP supplementary tables (which often have DOI, title, and notes rows first).
HEADER_KEYWORDS = [
    "depth", "lith", "facies", "unit", "section", "sample", "core",
    "upper", "lower", "top", "bottom", "description", "interval", "formation",
]

def detect_header_row(raw_bytes, encoding="utf-8", n_scan=30):
    """
    Scan the first n_scan rows of a CSV and return the row index that most
    looks like a real data header (highest count of HEADER_KEYWORDS matches).
    IODP files often have title rows above the actual column names -- this skips them.
    """
    try:
        lines = raw_bytes.decode(encoding, errors="replace").splitlines()
    except Exception:
        return 0
    best_row, best_score = 0, 0
    for i, line in enumerate(lines[:n_scan]):
        score = sum(1 for kw in HEADER_KEYWORDS if kw in line.lower())
        if score > best_score:
            best_score, best_row = score, i
    return best_row

# ── File parsing ───────────────────────────────────────────────────────────────
def parse_upload(contents, filename):
    """
    Decode a file uploaded through the Dash upload widget and return a DataFrame.

    Dash sends uploaded files as base64-encoded strings (text), so we decode
    them back to bytes before reading. Supports CSV, Excel, and LAS formats.

    Returns (df, meta) where meta is a dict with file info.
    If parsing fails, df is None and meta contains an "error" key.
    """
    _, b64 = contents.split(",")
    raw    = base64.b64decode(b64)
    fname  = filename.lower()
    meta   = {"filename": filename}
    try:
        if fname.endswith(".csv"):
            df = None
            for enc in ["utf-8", "latin-1", "cp1252", "utf-16"]:
                try:
                    header_row = detect_header_row(raw, encoding=enc)
                    df = pd.read_csv(io.StringIO(raw.decode(enc)),
                                     header=header_row, skip_blank_lines=True)
                    df = df.dropna(axis=1, how="all").dropna(how="all").reset_index(drop=True)
                    break
                except Exception:
                    continue
            if df is None:
                return None, {"error": "Could not decode CSV"}
            meta["format"] = "CSV"
        elif fname.endswith((".xlsx", ".xls")):
            best_df, best_score = None, -1
            for skip in range(0, 20):
                try:
                    candidate = pd.read_excel(io.BytesIO(raw), skiprows=skip)
                    candidate = candidate.dropna(axis=1, how="all").dropna(how="all")
                    score = sum(1 for col in candidate.columns
                                for kw in HEADER_KEYWORDS if kw in str(col).lower())
                    if score > best_score:
                        best_score, best_df = score, candidate
                except Exception:
                    continue
            if best_df is None:
                return None, {"error": "Could not read Excel file"}
            df = best_df.reset_index(drop=True)
            meta["format"] = "Excel"
        elif fname.endswith(".las"):
            for enc in ["utf-8", "latin-1", "cp1252"]:
                try:
                    las = lasio.read(io.StringIO(raw.decode(enc)))
                    break
                except Exception:
                    continue
            df = las.df().reset_index()
            meta["format"] = "LAS"
            try:    meta["well"] = las.well.WELL.value
            except: meta["well"] = ""
        else:
            return None, {"error": "Unsupported file type: " + filename}
        meta.update(rows=len(df), cols=len(df.columns),
                    columns=list(df.columns),
                    numeric_cols=df.select_dtypes(include="number").columns.tolist())
        return df, meta
    except Exception as e:
        return None, {"error": str(e)}

def df2j(df):
    """Serialize a DataFrame to JSON for storage in dcc.Store.
    dcc.Store can only hold strings/dicts, not DataFrames directly."""
    return df.to_json(date_format="iso", orient="split") if df is not None else None

def j2df(j):
    """Deserialize a JSON string from dcc.Store back into a DataFrame."""
    return pd.read_json(io.StringIO(j), orient="split") if j else None

def empty_fig(msg="Upload a file to begin", color=None):
    """Return a blank Plotly figure with a centered message.
    Used as a placeholder before data is loaded."""
    fig = go.Figure()
    fig.update_layout(**PLOT_CFG, annotations=[dict(
        text=msg, xref="paper", yref="paper", x=0.5, y=0.5,
        showarrow=False, font=dict(color=color or C["muted"], size=15))])
    return fig

# =============================================================================
# LITHOLOGY COLUMN DETECTION
# =============================================================================
# IODP litho files use many different column name formats across expeditions.
# This section handles that variety by matching against known aliases.
# For each required internal column name, list all real-world name variations
LITHO_COLUMN_ALIASES = [
    ("top_mbsf",    ["top depth csf","top depth","upper depth","depth top",
                     "topdepth","top_depth","top_mbsf","top (m","top_csf"]),
    ("bottom_mbsf", ["bottom depth csf","bottom depth","lower depth","depth bottom",
                     "bottomdepth","bottom_depth","bottom_mbsf","bottom (m",
                     "bot_csf","bot depth"]),
    ("lithology",   ["lithofacies","lithology","lith. unit","lith unit","litho unit",
                     "lithostratigraphic","facies","description","sediment type",
                     "rock type","unit name"]),
]

def resolve_litho_columns(df):
    """
    Identify the top depth, bottom depth, and lithology columns in an uploaded
    litho file, even if the column names do not match the expected standard.
    Returns (renamed_df, None) on success, or (None, error_string) on failure.
    """
    cols_lower = {c.lower().strip(): c for c in df.columns}
    mapping = {}
    for internal_name, aliases in LITHO_COLUMN_ALIASES:
        matched = None
        for alias in aliases:
            for col_lower, col_original in cols_lower.items():
                if alias in col_lower:
                    matched = col_original
                    break
            if matched:
                break
        if matched:
            mapping[internal_name] = matched
    missing = [n for n in ("top_mbsf","bottom_mbsf","lithology") if n not in mapping]
    if missing:
        error = (f"Could not identify: {', '.join(missing)}.\n\nDetected columns:\n"
                 + "\n".join(f"  - {c}" for c in df.columns))
        return None, error
    df = df.rename(columns={v: k for k, v in mapping.items()})
    for col in ("top_mbsf","bottom_mbsf"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["top_mbsf","bottom_mbsf"])
    df = df[df["top_mbsf"] < df["bottom_mbsf"]].reset_index(drop=True)
    df["lithology"] = df["lithology"].fillna("unknown").astype(str).str.strip()
    return df, None

# =============================================================================
# SITE METADATA HELPERS
# =============================================================================
def infer_site_meta(df, meta):
    """
    Try to automatically extract expedition/site info from column contents.
    Looks for JCORES-style sample IDs (e.g. C0019J-6K-1) and depth columns.
    Returns a dict of whatever it can find; missing values are filled in later
    from the manual metadata fields the user can type into the sidebar.
    """
    info = {}
    cols_lower = {c.lower(): c for c in df.columns}
    for cand in ["jcores_sampleid","sampleid","sample_id","sample"]:
        if cand in cols_lower:
            first = str(df[cols_lower[cand]].dropna().iloc[0]) if len(df) else ""
            m = re.match(r"([A-Z]\d{3,4}[A-Z]?)", first)
            if m:
                info["site_hole"] = m.group(1)
            break
    for cand in ["topdepth_mbsf","topdepth_mbsf_mcsf-a","depth_mbsf","dept","depth","top_depth","top_mbsf"]:
        if cand in cols_lower:
            v = df[cols_lower[cand]].dropna()
            if len(v):
                info["depth_min"] = f"{v.min():.1f}"
                info["depth_max"] = f"{v.max():.1f}"
            break
    info["rows"] = meta.get("rows","")
    info["filename"] = meta.get("filename","")
    info["fmt"] = meta.get("format","")
    return info

def build_metadata_bar(info, manual):
    """
    Build the site metadata banner shown at the top of the Shipboard tab.
    Combines auto-detected values (info) with manual overrides (manual).
    """
    def field(label, value):
        return html.Div([
            html.Div(label, style={"color":C["muted"],"fontSize":"9px",
                                   "letterSpacing":"1.5px","fontFamily":FONT}),
            html.Div(value, style={"color":C["text"],"fontSize":"13px",
                                   "fontWeight":"600","fontFamily":FONT}),
        ], style={"marginRight":"20px"})
    site_hole  = manual.get("site_hole")  or info.get("site_hole","—")
    expedition = manual.get("expedition") or "—"
    lat  = manual.get("lat")  or "—"
    lon  = manual.get("lon")  or "—"
    water_d  = manual.get("water_depth") or "—"
    recovery = manual.get("recovery")    or "—"
    d_min = info.get("depth_min","—"); d_max = info.get("depth_max","—")
    depth_str = f"{d_min} - {d_max} mbsf" if d_min != "—" else "—"
    return html.Div([
        html.Div([
            field("EXPEDITION", expedition), field("SITE / HOLE", site_hole),
            field("LAT / LON", f"{lat}  {lon}"),
            field("WATER DEPTH", f"{water_d} m" if water_d != "—" else "—"),
            field("RECOVERY", f"{recovery}%" if recovery != "—" else "—"),
            field("DEPTH RANGE", depth_str),
        ], style={"display":"flex","alignItems":"center","flexWrap":"wrap"}),
        html.Div([
            html.Span(info.get("filename",""),
                      style={"background":C["border"],"padding":"3px 10px",
                             "borderRadius":"12px","fontSize":"11px","fontFamily":FONT}),
            html.Span(info.get("fmt",""),
                      style={"background":C["accent"],"color":C["bg"],"padding":"3px 10px",
                             "borderRadius":"12px","fontSize":"11px","fontWeight":"700"}),
            html.Span(f"{info.get('rows','')} rows",
                      style={"color":C["muted"],"fontSize":"11px"}),
        ], style={"display":"flex","gap":"8px","alignItems":"center"}),
    ], style={"display":"flex","justifyContent":"space-between","alignItems":"center",
              "padding":"10px 20px","background":C["panel"],
              "borderBottom":f"1px solid {C['border']}","flexWrap":"wrap","gap":"8px"})

# =============================================================================
# CORE-TOP, QC, AND GAP HELPERS
# =============================================================================
def extract_core_tops(df):
    """
    Parse JCORES sample IDs (e.g. C0019J-6K-1) to find the minimum depth
    (core top) for each core. Core-top ticks help scientists orient themselves
    in depth profiles -- they mark where each new core section begins.
    Returns a dict like {"C0019J-6K": 45.3, "C0019J-7K": 52.1, ...}
    """
    cols_lower = {c.lower(): c for c in df.columns}
    id_col, depth_col = None, None
    for cand in ["jcores_sampleid","sampleid","sample_id","sample"]:
        if cand in cols_lower:
            id_col = cols_lower[cand]; break
    for cand in ["topdepth_mbsf","topdepth_mbsf_mcsf-a","depth_mbsf","dept","depth"]:
        if cand in cols_lower:
            depth_col = cols_lower[cand]; break
    if id_col is None or depth_col is None:
        return {}
    core_tops = {}
    for sid, depth in zip(df[id_col], df[depth_col]):
        parts = str(sid).split("-")
        if len(parts) >= 3 and pd.notna(depth):
            key = f"{parts[0]}-{parts[1]}"
            d = float(depth)
            if key not in core_tops or d < core_tops[key]:
                core_tops[key] = d
    return core_tops

def find_qc_col(df):
    """Return the first column whose name contains the word "comment", or None.
    QC (quality control) comment columns flag measurements that may be unreliable."""
    for col in df.columns:
        if re.search(r"comment", col, re.IGNORECASE):
            return col
    return None

def find_recovery_gaps(df, depth_col, gap_threshold_m=5.0):
    """
    Find depth intervals where consecutive measurements are more than
    gap_threshold_m meters apart. These indicate intervals where the drill
    advanced but no core was recovered -- the core "fell out" of the barrel.
    Returns a list of (gap_top, gap_bottom) tuples in meters below seafloor (mbsf).
    """
    if depth_col not in df.columns:
        return []
    depths = df[depth_col].dropna().sort_values().values
    return [(float(depths[i]), float(depths[i+1]))
            for i in range(len(depths)-1)
            if depths[i+1]-depths[i] > gap_threshold_m]

# =============================================================================
# ONLINE DATA FETCH HELPERS
# =============================================================================
def fetch_lore(report_name, expedition, site="", hole=""):
    """
    Download data from the IODP LIMS/LORE database at Texas A&M University.
    Works for JOIDES Resolution expeditions 317 and later.

    The LORE URL accepts filter parameters that specify which expedition,
    site, hole, and measurement type to return. The response is a CSV file.

    Returns (DataFrame, None) on success, or (None, error_string) on failure.
    """
    """Fetch from LIMS/LORE (GCR/TAMU) — JR expeditions 317+."""
    filters = [f"x_expedition in ('{expedition}')"]
    if site: filters.append(f"x_site in ('{site}')")
    if hole: filters.append(f"x_hole in ('{hole}')")
    url = (f"{LORE_BASE}?reportName={report_name}"
           f"&appl=LORE&action=download&format=csv"
           f"&filters={json.dumps(filters)}")
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text))
        if df.empty:
            return None, "No data returned — check expedition/site/report combination"
        return df, None
    except Exception as e:
        return None, str(e)


def fetch_pangaea_doi(pangaea_id):
    """
    Download a single dataset from PANGAEA by its numeric dataset ID.

    The ID is the number at the end of a PANGAEA DOI:
        Full DOI: 10.1594/PANGAEA.938129
        Numeric ID: 938129

    PANGAEA text file exports include comment lines starting with "//"
    (metadata header) that we strip before parsing the tab-separated data.

    Returns (DataFrame, None) on success, or (None, error_string) on failure.
    """
    url = PANGAEA_DOI_DL.format(pid=pangaea_id)
    try:
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        text = r.text
        # PANGAEA textfile format has comment lines starting with //
        lines = [l for l in text.splitlines() if not l.startswith("//")]
        clean = "\n".join(lines)
        df = pd.read_csv(io.StringIO(clean), sep="\t", skip_blank_lines=True)
        df = df.dropna(axis=1, how="all").dropna(how="all").reset_index(drop=True)
        if df.empty:
            return None, "Dataset is empty or could not be parsed"
        return df, None
    except Exception as e:
        return None, str(e)


def search_pangaea(query, count=10):
    """
    Search the PANGAEA database using its Elasticsearch API.
    Returns a list of result dicts with "label" and "value" keys, ready for
    use in a Dash Dropdown component.

    The Elasticsearch endpoint accepts a JSON query body and returns matching
    dataset records including their DOI (from which we extract the numeric ID).

    Returns (results_list, None) on success, or ([], error_string) on failure.
    """
    """Search PANGAEA via Elasticsearch and return list of {label, value} dicts."""
    body = {
        "query": {"query_string": {"query": query, "default_operator": "AND"}},
        "size": count,
        "_source": ["title", "URI"],
    }
    try:
        r = requests.post(PANGAEA_ES, json=body, timeout=20,
                          headers={"Content-Type": "application/json"})
        r.raise_for_status()
        hits = r.json().get("hits", {}).get("hits", [])
        results = []
        for h in hits:
            src   = h.get("_source", {})
            uri   = src.get("URI", "")
            # URI looks like "https://doi.pangaea.de/10.1594/PANGAEA.938129"
            pid   = uri.split(".")[-1] if uri else h.get("_id", "")
            title = src.get("title", uri)
            if pid:
                results.append({"label": f"{pid} — {str(title)[:60]}", "value": pid})
        return results, None
    except Exception as e:
        return [], str(e)


def find_depth_col(df):
    """
    Return the name of the column most likely to contain depth values (mbsf).
    Searches for common IODP depth column name patterns.
    Falls back to the first column if nothing matches.
    """
    for c in df.columns:
        if any(k in c.lower() for k in ["depth","mbsf","mcsf","top_depth"]):
            return c
    return df.columns[0]

def depth_tolerance_merge(dfa, dfb, tol_cm=2):
    """
    Merge two datasets by depth, treating measurements within tol_cm centimeters
    of each other as being at the same depth.

    This tolerance is necessary because two instruments measuring the same core
    will almost never sample at exactly the same depth points. For example,
    GRA bulk density might be measured every 2 cm while P-wave velocity is every
    5 cm -- a depth-tolerance join links them without requiring exact matches.

    Uses pandas merge_asof which matches each row in A to the nearest row in B
    within the tolerance window. Columns from both datasets appear side by side
    in the result, with _A and _B suffixes to distinguish them.

    Both input DataFrames must have a column named "depth_key" (in meters).
    """
    tol_m = tol_cm / 100.0
    a = dfa.copy().sort_values("depth_key").reset_index(drop=True)
    b = dfb.copy().sort_values("depth_key").reset_index(drop=True)
    return pd.merge_asof(a, b, on="depth_key", tolerance=tol_m,
                         direction="nearest", suffixes=("_A","_B"))

def get_expeditions_from_df(df):
    """
    Look for an expedition number column and return a sorted list of unique values.
    Used to populate the expedition filter checkboxes in the Post-Expedition tab.
    """
    for c in df.columns:
        if "expedition" in c.lower() or c.lower() in ("exp","exp."):
            return sorted(df[c].dropna().astype(str).unique().tolist())
    return []

# =============================================================================
# CHART BUILDER (SHIPBOARD TAB)
# =============================================================================
def make_chart(df, ctype, x, y, color, curves,
               litho_df=None, show_gaps=True, show_qc=True, show_core_tops=True):
    if ctype == "scatter" and x and y:
        cc = None if color in (None,"None","") else color
        return (px.scatter(df, x=x, y=y, color=cc, opacity=0.75)
                .update_traces(marker=dict(size=5))
                .update_layout(**PLOT_CFG))
    if ctype == "line" and x and y:
        return px.line(df, x=x, y=y).update_layout(**PLOT_CFG)
    if ctype == "histogram" and x:
        return (px.histogram(df, x=x, nbins=40,
                             color_discrete_sequence=[C["accent"]])
                .update_layout(**PLOT_CFG))
    if ctype == "heatmap":
        nc = df.select_dtypes(include="number").columns.tolist()
        if len(nc) < 2:
            return empty_fig("Need 2+ numeric columns for heatmap")
        corr = df[nc].corr().round(2)
        return (px.imshow(corr, text_auto=True, aspect="auto",
                          color_continuous_scale="RdBu_r", zmin=-1, zmax=1)
                .update_layout(**PLOT_CFG, height=420))
    if ctype == "depthlog" and x and curves:
        sel = [c for c in curves if c in df.columns]
        if not sel:
            return empty_fig("No valid curves selected")
        has_litho  = litho_df is not None and len(litho_df) > 0
        all_labels = (["Litho"] if has_litho else []) + sel
        raw_widths = [0.06 if l == "Litho" else 1.0 for l in all_labels]
        total      = sum(raw_widths)
        col_widths = [w/total for w in raw_widths]
        fig = make_subplots(rows=1, cols=len(all_labels), shared_yaxes=True,
                            subplot_titles=all_labels, column_widths=col_widths,
                            horizontal_spacing=0.01)
        col_offset = 1
        if has_litho:
            for _, row in litho_df.iterrows():
                fig.add_shape(type="rect", x0=0, x1=1,
                              y0=row["top_mbsf"], y1=row["bottom_mbsf"],
                              fillcolor=litho_color(row.get("lithology","")),
                              opacity=0.75, line_width=0, row=1, col=col_offset,
                              xref=f"x{col_offset if col_offset>1 else ''} domain",
                              yref="y")
                fig.add_annotation(
                    x=0.5, y=(row["top_mbsf"]+row["bottom_mbsf"])/2,
                    text=str(row.get("lithology",""))[:6], showarrow=False,
                    font=dict(size=7, color="#ffffff"), textangle=-90,
                    xref=f"x{col_offset if col_offset>1 else ''} domain",
                    yref="y", row=1, col=col_offset)
            fig.add_trace(go.Scatter(
                x=[0.5,0.5],
                y=[litho_df["top_mbsf"].min(), litho_df["bottom_mbsf"].max()],
                mode="markers", marker_opacity=0, showlegend=False, name=""),
                row=1, col=col_offset)
            col_offset += 1
        pal       = [C["accent"],C["accent2"],C["accent3"],"#bc8cff","#ff7b72"]
        gaps      = find_recovery_gaps(df, x)  if show_gaps      else []
        core_tops = extract_core_tops(df)       if show_core_tops else {}
        qc_col    = find_qc_col(df)             if show_qc        else None
        qc_depths = []
        if qc_col and qc_col in df.columns:
            qc_mask   = df[qc_col].fillna("").astype(str).str.strip() != ""
            qc_depths = df.loc[qc_mask, x].dropna().tolist()
        for i, col in enumerate(sel):
            mask = df[col].notna() & df[x].notna()
            fig.add_trace(go.Scatter(
                x=df.loc[mask,col], y=df.loc[mask,x], mode="lines", name=col,
                line=dict(color=pal[i%len(pal)], width=1.5)),
                row=1, col=col_offset+i)
            for gap_top, gap_bot in gaps:
                fig.add_hrect(y0=gap_top, y1=gap_bot, fillcolor="#888780",
                              opacity=0.18, line_width=0, row=1, col=col_offset+i,
                              annotation_text="gap" if i==0 else "",
                              annotation_font=dict(size=8, color=C["muted"]),
                              annotation_position="top left")
            if qc_depths:
                qc_df = df.loc[df[x].isin(qc_depths) & df[col].notna()]
                if len(qc_df):
                    fig.add_trace(go.Scatter(
                        x=qc_df[col], y=qc_df[x], mode="markers",
                        name="QC flagged" if i==0 else "", showlegend=(i==0),
                        marker=dict(symbol="circle-open", size=8,
                                    color=C["danger"], line_width=1.5),
                        hovertemplate="%{y:.2f} mbsf - QC flagged<extra></extra>"),
                        row=1, col=col_offset+i)
            if show_core_tops and i==0 and core_tops:
                x_min   = df[col].min()
                x_range = (df[col].max()-x_min) or 1
                tick_end = x_min + x_range*0.12
                for core_label, core_depth in core_tops.items():
                    fig.add_shape(type="line", x0=x_min, x1=tick_end,
                                  y0=core_depth, y1=core_depth,
                                  line=dict(color=C["warn"], width=0.8, dash="dot"),
                                  row=1, col=col_offset+i)
                    fig.add_annotation(x=tick_end, y=core_depth,
                                       text=core_label.split("-")[-1],
                                       showarrow=False,
                                       font=dict(size=7, color=C["warn"]),
                                       xanchor="left", yanchor="middle",
                                       row=1, col=col_offset+i)
        fig.update_yaxes(autorange="reversed", title_text=x, row=1, col=1)
        cfg = {**PLOT_CFG, "height":620}
        cfg.pop("xaxis",None); cfg.pop("yaxis",None)
        return fig.update_layout(showlegend=True,
                                 legend=dict(x=1.01,y=1,font=dict(size=10)), **cfg)
    return empty_fig("Select axes to plot")

# =============================================================================
# DASH APP INITIALIZATION
# =============================================================================

server = flask.Flask(__name__)  # the underlying Flask web server
app    = Dash(__name__, server=server, suppress_callback_exceptions=True)
# suppress_callback_exceptions=True is needed because tab content is generated
# dynamically -- Dash would otherwise error when callbacks reference components
# that haven't been rendered yet.

# Inject CSS into the page <head>.
# Sets CSS custom properties for theming and overrides Dash's internal dropdown
# styles (which ignore the normal "style" prop and require !important overrides).
app.index_string = """<!DOCTYPE html>
<html>
<head>
{%metas%}
<title>IODP Explorer</title>
{%favicon%}
{%css%}
<style>
  :root {
    --bg:#0d1117; --panel:#161b22; --border:#30363d;
    --accent:#58a6ff; --accent2:#3fb950; --accent3:#d2a679;
    --text:#e6edf3; --muted:#8b949e; --danger:#f85149; --warn:#d29922;
    --dd-bg:#21262d; --dd-hover:#30363d;
  }
  body { background:var(--bg) !important; color:var(--text) !important; }
  /* Dash dropdown internals */
  .Select-menu-outer,.VirtualizedSelectOption,.Select-option
    { background-color:var(--dd-bg)!important; color:var(--text)!important; }
  .Select-option:hover,.Select-option.is-focused
    { background-color:var(--dd-hover)!important; color:var(--accent)!important; }
  .Select-value-label,.Select-placeholder,.Select--single .Select-value
    { color:var(--text)!important; }
  .Select-control
    { background-color:var(--dd-bg)!important; border-color:var(--border)!important;
      color:var(--text)!important; }
  .Select-input input { color:var(--text)!important; background:transparent!important; }
  .Select-value
    { background-color:var(--dd-hover)!important; border-color:var(--accent)!important;
      color:var(--text)!important; }
  .Select-value-icon { color:var(--muted)!important; border-color:var(--accent)!important; }
  .Select-value-icon:hover
    { background-color:var(--accent)!important; color:var(--bg)!important; }
  .Select-arrow { border-top-color:var(--muted)!important; }
  .Select-clear { color:var(--muted)!important; }
  /* DataTable */
  .dash-spreadsheet-container .dash-spreadsheet-inner th
    { background-color:var(--bg)!important; color:var(--accent)!important; }
  .dash-spreadsheet-container .dash-spreadsheet-inner td
    { background-color:var(--panel)!important; color:var(--text)!important; }
  /* Tab bar */
  .tab { background-color:var(--panel)!important; color:var(--muted)!important; }
  .tab--selected { background-color:var(--bg)!important; color:var(--text)!important; }
  /* Smooth theme transition */
  * { transition: background-color 0.25s, color 0.25s, border-color 0.25s; }
</style>
</head>
<body>
{%app_entry%}
<footer>{%config%}{%scripts%}{%renderer%}</footer>
</body>
</html>"""

TAB_STYLE = {"backgroundColor":C["panel"],"color":C["muted"],
             "border":f"1px solid {C['border']}","borderBottom":"none",
             "fontFamily":FONT,"fontSize":"13px","padding":"8px 20px"}
TAB_SEL   = {**TAB_STYLE,"backgroundColor":C["bg"],"color":C["text"],
             "borderBottom":f"1px solid {C['bg']}","fontWeight":"600"}

# =============================================================================
# SHIPBOARD SIDEBAR LAYOUT
# =============================================================================
# The left panel containing all controls for the Shipboard tab.
shipboard_sidebar = html.Div([
    html.P("DATA SOURCE", style=LBL),
    dcc.Upload(id="upload", multiple=False,
        children=html.Div([
            html.Div("↑", style={"fontSize":"26px","color":C["accent"]}),
            html.Div("Drop file or click"),
            html.Div(".csv .xlsx .las", style={"color":C["muted"],"fontSize":"10px"}),
        ], style={"textAlign":"center","color":C["text"],"fontSize":"12px"}),
        style={"border":f"2px dashed {C['border']}","borderRadius":"8px",
               "padding":"16px","cursor":"pointer","marginBottom":"10px"}),
    html.P("LITHO TRACK (optional)", style=LBL),
    html.Div("CSV or Excel: top depth, bottom depth, lithology columns",
             style={"color":C["muted"],"fontSize":"9px","marginBottom":"4px","fontFamily":FONT}),
    dcc.Upload(id="upload-litho", multiple=False,
        children=html.Div([
            html.Div("↑", style={"fontSize":"18px","color":C["accent3"]}),
            html.Div("Drop litho file"),
        ], style={"textAlign":"center","color":C["text"],"fontSize":"11px"}),
        style={"border":f"2px dashed {C['border']}","borderRadius":"8px",
               "padding":"10px","cursor":"pointer","marginBottom":"4px"}),
    html.Div(id="litho-badge"),
    html.Hr(style={"borderColor":C["border"],"margin":"14px 0"}),
    html.P("SITE METADATA (optional)", style=LBL),
    html.Div("Auto-detected where possible:",
             style={"color":C["muted"],"fontSize":"9px","marginBottom":"6px"}),
    *[html.Div([
        html.Div(label, style={**LBL,"marginTop":"6px"}),
        dcc.Input(id=fid, type="text", placeholder=ph, debounce=True, style=INP),
      ]) for label,fid,ph in [
        ("EXPEDITION","meta-expedition","e.g. IODP 405"),
        ("SITE / HOLE","meta-site-hole","e.g. C0019J"),
        ("LAT","meta-lat","e.g. 38.1N"),
        ("LON","meta-lon","e.g. 143.9E"),
        ("WATER DEPTH m","meta-water-depth","e.g. 6897"),
        ("RECOVERY %","meta-recovery","e.g. 68.4"),
    ]],
    html.Hr(style={"borderColor":C["border"],"margin":"14px 0"}),
    html.P("X AXIS (depth)", style=LBL),
    dcc.Dropdown(id="x-col", placeholder="Select column...", style=DD),
    html.P("Y AXIS", id="y-lbl", style=LBL),
    dcc.Dropdown(id="y-col", placeholder="Select column...", style=DD),
    html.P("COLOR BY", id="color-lbl", style=LBL),
    dcc.Dropdown(id="color-col", placeholder="None", style=DD),
    html.P("CURVES (depth log)", id="curves-lbl", style={**LBL,"display":"none"}),
    dcc.Checklist(id="depth-curves", options=[], value=[],
                  labelStyle={"display":"block","marginBottom":"5px",
                               "color":C["text"],"fontSize":"12px"},
                  inputStyle={"marginRight":"6px","accentColor":C["accent"]},
                  style={"display":"none"}),
    html.Hr(style={"borderColor":C["border"],"margin":"14px 0"}),
    html.P("DEPTH LOG OVERLAYS", style=LBL),
    dcc.Checklist(id="overlay-opts",
        options=[{"label":" Recovery gap hatching","value":"gaps"},
                 {"label":" QC flag markers","value":"qc"},
                 {"label":" Core-top tick marks","value":"core_tops"}],
        value=["gaps","qc","core_tops"],
        labelStyle={"display":"block","marginBottom":"6px",
                    "color":C["text"],"fontSize":"11px","fontFamily":FONT},
        inputStyle={"marginRight":"6px","accentColor":C["accent"]}),
    html.Hr(style={"borderColor":C["border"],"margin":"14px 0"}),
    html.P("CHART TYPE", style={**LBL,"marginTop":"18px"}),
    dcc.RadioItems(id="chart-type", value="scatter",
        options=[{"label":" Scatter","value":"scatter"},
                 {"label":" Line","value":"line"},
                 {"label":" Histogram","value":"histogram"},
                 {"label":" Depth Log","value":"depthlog"},
                 {"label":" Correlation Heatmap","value":"heatmap"}],
        labelStyle={"display":"block","marginBottom":"8px",
                    "color":C["text"],"fontSize":"12px","fontFamily":FONT},
        inputStyle={"marginRight":"7px","accentColor":C["accent"]}),
], style={"width":"240px","minWidth":"240px","background":C["panel"],
          "borderRight":f"1px solid {C['border']}","padding":"18px","overflowY":"auto"})


# ── Post-Expedition dataset fetch panel ────────────────────────────────────────
def dataset_panel(ds):
    """
    Build the Dataset A or B fetch panel in the Post-Expedition sidebar.
    Each panel offers three data source options:
      Upload    -- drag and drop a local file (required for J-CORES/KCC data)
      LIMS/LORE -- live fetch from the JRSO database
      PANGAEA   -- live fetch from the European IODP portal
    """
    accent = C["accent"] if ds == "a" else C["accent3"]
    label  = "DATASET A" if ds == "a" else "DATASET B"
    repo_hint = html.Div([
        html.Div([
            html.Span("LIMS/LORE", style={"color":C["accent2"],"fontWeight":"700"}),
            html.Span(" — JR expeditions 317+  (GCR/TAMU)",
                      style={"color":C["muted"]}),
        ], style={"fontSize":"9px","marginBottom":"2px","fontFamily":FONT}),
        html.Div([
            html.Span("PANGAEA", style={"color":C["accent3"],"fontWeight":"700"}),
            html.Span(" — MSP expeditions  (BCR/Bremen, ESO/ECORD)",
                      style={"color":C["muted"]}),
        ], style={"fontSize":"9px","marginBottom":"2px","fontFamily":FONT}),
        html.Div([
            html.Span("Upload", style={"color":C["accent"],"fontWeight":"700"}),
            html.Span(" — Chikyu/J-CORES (KCC/JAMSTEC) or any local file",
                      style={"color":C["muted"]}),
        ], style={"fontSize":"9px","fontFamily":FONT}),
    ], style={"background":C["bg"],"border":f"1px solid {C['border']}",
              "borderRadius":"4px","padding":"6px 8px","marginBottom":"8px"})

    return html.Div([
        html.P(label, style={**LBL, "color": accent, "marginTop":"0"}),
        repo_hint,
        dcc.RadioItems(id=f"pe-{ds}-source",
            options=[
                {"label": " Upload / J-CORES", "value": "upload"},
                {"label": " LIMS/LORE",         "value": "lims"},
                {"label": " PANGAEA",            "value": "pangaea"},
            ],
            value="upload",
            labelStyle={"display":"block","color":C["muted"],
                        "fontSize":"11px","marginBottom":"3px"},
            inputStyle={"marginRight":"6px","accentColor":accent},
        ),

        # Upload panel
        html.Div(id=f"pe-{ds}-upload-panel", children=[
            dcc.Upload(id=f"pe-{ds}-upload", multiple=False,
                children=html.Div("Drop file or click",
                    style={"color":C["muted"],"fontSize":"11px",
                           "textAlign":"center","padding":"10px 0"}),
                style={"border":f"1px dashed {C['border']}","borderRadius":"6px",
                       "backgroundColor":C["bg"],"cursor":"pointer","marginTop":"6px"}),
            html.Div(id=f"pe-{ds}-upload-status",
                     style={"fontSize":"10px","color":C["accent2"],"marginTop":"4px"}),
        ]),

        # LIMS panel
        html.Div(id=f"pe-{ds}-lims-panel", style={"display":"none"}, children=[
            html.P("Report type", style={**LBL,"marginTop":"6px"}),
            dcc.Dropdown(id=f"pe-{ds}-report",
                options=[{"label":v,"value":k} for k,v in LORE_REPORTS.items()],
                placeholder="select report...", style=DD),
            dcc.Input(id=f"pe-{ds}-lims-exp",  placeholder="Expedition (e.g. 344)",
                      style={**INP,"marginTop":"4px"}),
            dcc.Input(id=f"pe-{ds}-lims-site", placeholder="Site (e.g. U1381)",
                      style={**INP,"marginTop":"4px"}),
            dcc.Input(id=f"pe-{ds}-lims-hole", placeholder="Hole (e.g. C)",
                      style={**INP,"marginTop":"4px"}),
            html.Button(f"Fetch {ds.upper()} from LIMS", id=f"pe-fetch-{ds}-lims",
                        n_clicks=0, style=BTN(C["accent2"])),
            html.Div(id=f"pe-{ds}-lims-status",
                     style={"fontSize":"10px","color":C["accent2"],"marginTop":"4px"}),
        ]),

        # PANGAEA panel
        html.Div(id=f"pe-{ds}-pangaea-panel", style={"display":"none"}, children=[
            html.P("PANGAEA dataset ID", style={**LBL,"marginTop":"6px"}),
            html.Div("Enter the numeric ID from the DOI (e.g. 938129 from 10.1594/PANGAEA.938129)",
                     style={"color":C["muted"],"fontSize":"9px","marginBottom":"4px"}),
            dcc.Input(id=f"pe-{ds}-pangaea-id", placeholder="e.g. 938129",
                      style={**INP,"marginTop":"4px"}),
            html.Button(f"Fetch {ds.upper()} from PANGAEA", id=f"pe-fetch-{ds}-pangaea",
                        n_clicks=0, style=BTN(C["accent3"])),
            html.Hr(style={"borderColor":C["border"],"margin":"8px 0"}),
            html.P("Or search PANGAEA", style={**LBL,"marginTop":"0"}),
            dcc.Input(id=f"pe-{ds}-pangaea-query",
                      placeholder="e.g. IODP 386 physical properties",
                      style={**INP,"marginTop":"4px"}),
            html.Button("Search", id=f"pe-search-{ds}-pangaea",
                        n_clicks=0, style=BTN(C["border"])),
            html.Div(id=f"pe-{ds}-pangaea-results",
                     style={"marginTop":"6px"}),
            html.Div(id=f"pe-{ds}-pangaea-status",
                     style={"fontSize":"10px","color":C["accent2"],"marginTop":"4px"}),
        ]),
    ], style={"borderBottom":f"1px solid {C['border']}",
              "paddingBottom":"12px","marginBottom":"12px"})


post_sidebar = html.Div([
    html.P("POST-EXPEDITION", style={**LBL,"marginTop":"0","fontSize":"11px",
                                     "color":C["muted"],"letterSpacing":"3px"}),
    html.Div("Multi-dataset merge with depth tolerance matching.",
             style={"color":C["muted"],"fontSize":"9px","marginBottom":"12px"}),

    dataset_panel("a"),
    dataset_panel("b"),

    html.P("MERGE SETTINGS", style={**LBL,"marginTop":"0"}),
    html.Div("Depth tolerance (cm)", style={"color":C["muted"],"fontSize":"10px","marginBottom":"4px"}),
    dcc.Input(id="pe-tolerance", value="2", type="number", min=0, max=500, style=INP),
    html.Div("Depth col — A", style={"color":C["muted"],"fontSize":"10px","marginTop":"8px","marginBottom":"4px"}),
    dcc.Dropdown(id="pe-depth-a", options=[], placeholder="auto-detect", style=DD),
    html.Div("Depth col — B", style={"color":C["muted"],"fontSize":"10px","marginTop":"8px","marginBottom":"4px"}),
    dcc.Dropdown(id="pe-depth-b", options=[], placeholder="auto-detect", style=DD),
    html.Button("Merge datasets", id="pe-merge-btn", n_clicks=0,
                style={**BTN(C["accent2"]),"marginTop":"10px","fontSize":"12px"}),
    html.Hr(style={"borderColor":C["border"],"margin":"10px 0"}),
    html.P("CHART MODE", style=LBL),
    dcc.RadioItems(id="pe-chart-mode", value="tracks",
        options=[
            {"label": " Depth tracks  (each property its own lane)", "value": "tracks"},
            {"label": " Correlation scatter  (A vs B, color = depth)", "value": "scatter"},
            {"label": " Dual-axis overlay  (two scales, one depth axis)", "value": "dual"},
            {"label": " Rolling mean  (smoothed downhole trends)", "value": "rolling"},
        ],
        labelStyle={"display":"block","marginBottom":"6px",
                    "color":C["text"],"fontSize":"11px","fontFamily":FONT},
        inputStyle={"marginRight":"6px","accentColor":C["accent2"]},
    ),
    html.P("DEPTH COLUMN", style=LBL),
    dcc.Dropdown(id="pe-xaxis", options=[], value=None, style=DD),
    html.P("DATASET A  columns", style=LBL),
    dcc.Dropdown(id="pe-yaxis", options=[], value=None, multi=True, style=DD),
    html.P("DATASET B  columns", style=LBL),
    dcc.Dropdown(id="pe-ycols-b", options=[], value=None, multi=True, style=DD),
    html.Div(id="pe-rolling-ctrl", style={"display":"none"}, children=[
        html.P("Rolling window (rows)", style={**LBL,"marginTop":"8px"}),
        dcc.Input(id="pe-rolling-window", value="20", type="number",
                  min=2, max=500, style=INP),
    ]),
], style={"width":"260px","minWidth":"260px","background":C["panel"],
          "borderRight":f"1px solid {C['border']}","padding":"18px","overflowY":"auto"})

# =============================================================================
# APP LAYOUT
# =============================================================================
# Defines the full structure of the web page.
# Components with an "id" can be read from or updated by callback functions.
app.layout = html.Div([
    html.Div([
        html.Div([
            html.Div([
                html.Span("IODP",      style={"fontWeight":"700","color":C["accent"]}),
                html.Span(" Explorer", style={"fontWeight":"300","color":C["text"]}),
            ], style={"fontSize":"17px","fontFamily":FONT}),
            html.Div("International Ocean Discovery Program · Data Visualization Tool",
                     style={"color":C["muted"],"fontSize":"11px","fontFamily":FONT}),
        ]),
        html.Button(id="theme-toggle", n_clicks=0,
            children="☀ Light mode",
            style={"backgroundColor":"transparent","border":f"1px solid {C['border']}",
                   "borderRadius":"6px","color":C["muted"],"cursor":"pointer",
                   "fontSize":"11px","fontFamily":FONT,"padding":"5px 12px",
                   "transition":"all 0.2s"}),
    ], style={"display":"flex","justifyContent":"space-between","alignItems":"center",
              "padding":"8px 20px","background":C["panel"],
              "borderBottom":f"1px solid {C['border']}"}),
    # Theme toggle — injected into page background wrapper via clientside callback
    html.Div(id="theme-root", style={"display":"none"}),

    dcc.Tabs(id="main-tabs", value="shipboard", style={"fontFamily":FONT},
        children=[
            dcc.Tab(label="Shipboard",       value="shipboard", style=TAB_STYLE, selected_style=TAB_SEL),
            dcc.Tab(label="Post-Expedition", value="postexp",   style=TAB_STYLE, selected_style=TAB_SEL),
        ]),

    html.Div(id="tab-content"),

    # Stores
    dcc.Store(id="theme-store",      storage_type="local", data="dark"),
    dcc.Store(id="store-df",        storage_type="session"),
    dcc.Store(id="store-meta",      storage_type="session"),
    dcc.Store(id="store-litho",     storage_type="session"),
    dcc.Store(id="store-site-info", storage_type="session"),
    dcc.Store(id="pe-store-a"),
    dcc.Store(id="pe-store-b"),
    dcc.Store(id="pe-merged-store"),

], style={"height":"100vh","display":"flex","flexDirection":"column",
          "background":C["bg"],"color":C["text"],"overflow":"hidden","fontFamily":FONT})

# =============================================================================
# TAB ROUTING
# =============================================================================
@app.callback(Output("tab-content","children"), Input("main-tabs","value"))
def render_tab(tab):
    """Render the content area for the active tab.
    Content is built dynamically so both tabs can share the same component IDs.
    """
    if tab == "shipboard":
        return html.Div([
            html.Div(id="meta-banner",
                     children=html.Div("Upload a file to see site metadata.",
                         style={"color":C["muted"],"fontSize":"11px",
                                "padding":"10px 20px","fontFamily":FONT})),
            html.Div([
                shipboard_sidebar,
                html.Div([
                    html.Div(id="kpi-bar",
                             style={"display":"flex","gap":"10px","padding":"10px 18px",
                                    "borderBottom":f"1px solid {C['border']}","flexWrap":"wrap"}),
                    html.Div(dcc.Graph(id="main-chart", style={"height":"620px"},
                                       config={"displayModeBar":True,"scrollZoom":True}),
                             style={"padding":"10px 18px"}),
                    html.Div([
                        html.Div([
                            html.Span("DATA TABLE", style={"color":C["muted"],"fontSize":"10px","letterSpacing":"2px"}),
                            html.Span(id="row-count", style={"color":C["accent"],"fontSize":"11px","marginLeft":"12px"}),
                        ], style={"marginBottom":"8px"}),
                        html.Div(id="table-container"),
                    ], style={**CARD,"margin":"0 18px 18px 18px"}),
                ], style={"flex":"1","overflowY":"auto","minWidth":"0"}),
            ], style={"display":"flex","flex":"1","overflow":"hidden"}),
        ], style={"display":"flex","flexDirection":"column","flex":"1","overflow":"hidden"})

    else:
        return html.Div([
            post_sidebar,
            html.Div([
                # Status cards
                html.Div([
                    html.Div(id="pe-status-a",
                        style={"flex":"1","background":C["panel"],"border":f"1px solid {C['border']}",
                               "borderRadius":"6px","padding":"10px 14px","fontSize":"12px",
                               "color":C["muted"],"marginRight":"8px"}),
                    html.Div(id="pe-status-b",
                        style={"flex":"1","background":C["panel"],"border":f"1px solid {C['border']}",
                               "borderRadius":"6px","padding":"10px 14px","fontSize":"12px",
                               "color":C["muted"],"marginRight":"8px"}),
                    html.Div(id="pe-status-merged",
                        style={"flex":"1","background":C["panel"],"border":f"1px solid {C['border']}",
                               "borderRadius":"6px","padding":"10px 14px","fontSize":"12px",
                               "color":C["muted"]}),
                ], style={"display":"flex","marginBottom":"12px"}),

                # Expedition filter
                html.Div([
                    html.Div([
                        html.Span("EXPEDITIONS IN DATASETS",
                                  style={"color":C["muted"],"fontSize":"10px","letterSpacing":"2px"}),
                        html.Button("All / None", id="pe-exp-all-none", n_clicks=0,
                            style={"backgroundColor":C["border"],"color":C["text"],"border":"none",
                                   "borderRadius":"4px","padding":"3px 10px","cursor":"pointer",
                                   "fontSize":"10px","marginLeft":"12px"}),
                    ], style={"marginBottom":"8px"}),
                    dcc.Checklist(id="pe-exp-filter", options=[], value=[],
                        labelStyle={"display":"inline-block","margin":"3px 8px 3px 0",
                                    "color":C["muted"],"fontSize":"11px"}),
                ], style={**CARD,"marginBottom":"12px"}),

                # Merged expeditions + download
                html.Div([
                    html.Span("Expeditions in merged data: ",
                              style={"color":C["muted"],"fontSize":"11px","marginRight":"6px"}),
                    html.Span(id="pe-merged-expeditions",
                              style={"color":C["accent2"],"fontSize":"11px","fontFamily":FONT}),
                    html.Button("Download merged CSV", id="pe-download-btn", n_clicks=0,
                        style={"backgroundColor":C["panel"],"color":C["accent"],
                               "border":f"1px solid {C['border']}","borderRadius":"4px",
                               "padding":"4px 12px","cursor":"pointer",
                               "fontSize":"11px","marginLeft":"16px"}),
                    dcc.Download(id="pe-download"),
                ], style={"marginBottom":"12px"}),

                dcc.Graph(id="pe-chart", config={"displayModeBar":True,"scrollZoom":True}),
                html.Div(id="pe-table-container", style={"marginTop":"16px"}),
            ], style={"flex":"1","overflowY":"auto","padding":"16px","minWidth":"0"}),
        ], style={"display":"flex","flex":"1","overflow":"hidden"})



# =============================================================================
# CALLBACKS
# =============================================================================
# Callbacks are Python functions that run automatically when a component changes.
# Each callback declares:
#   Output  -- which component property to update
#   Input   -- which component change triggers this function
#   State   -- additional values to read without triggering (like form fields)
#
# =============================================================================
# THEME CALLBACKS
# =============================================================================
# clientside_callback runs as JavaScript in the browser (no server round-trip).
# This makes the theme switch instant rather than waiting for a server response.
app.clientside_callback(
    """
    function(n, stored) {
        const theme = (n % 2 === 1) ? "light" : "dark";
        const dark = {
            "--bg":"#0d1117","--panel":"#161b22","--border":"#30363d",
            "--accent":"#58a6ff","--accent2":"#3fb950","--accent3":"#d2a679",
            "--text":"#e6edf3","--muted":"#8b949e","--danger":"#f85149","--warn":"#d29922",
            "--dd-bg":"#21262d","--dd-hover":"#30363d"
        };
        const light = {
            "--bg":"#ffffff","--panel":"#f6f8fa","--border":"#d0d7de",
            "--accent":"#0969da","--accent2":"#1a7f37","--accent3":"#953800",
            "--text":"#1f2328","--muted":"#656d76","--danger":"#cf222e","--warn":"#9a6700",
            "--dd-bg":"#ffffff","--dd-hover":"#eaf0f7"
        };
        const vars = theme === "light" ? light : dark;
        const root = document.documentElement;
        Object.entries(vars).forEach(([k, v]) => root.style.setProperty(k, v));
        // Also update body background directly for full coverage
        document.body.style.backgroundColor = vars["--bg"];
        document.body.style.color = vars["--text"];
        return theme;
    }
    """,
    Output("theme-store", "data"),
    Input("theme-toggle", "n_clicks"),
    State("theme-store", "data"),
)

@app.callback(
    Output("theme-toggle", "children"),
    Output("theme-toggle", "style"),
    Input("theme-store", "data"),
)
def update_toggle_btn(theme):
    """Update the toggle button label and border color to match the active theme."""
    if theme == "light":
        return "🌙 Dark mode", {
            "backgroundColor": "transparent",
            "border": "1px solid #d0d7de",
            "borderRadius": "6px", "color": "#656d76",
            "cursor": "pointer", "fontSize": "11px",
            "fontFamily": FONT, "padding": "5px 12px",
        }
    return "☀ Light mode", {
        "backgroundColor": "transparent",
        "border": "1px solid #30363d",
        "borderRadius": "6px", "color": "#8b949e",
        "cursor": "pointer", "fontSize": "11px",
        "fontFamily": FONT, "padding": "5px 12px",
    }

# =============================================================================
# SHIPBOARD TAB CALLBACKS
# =============================================================================
@app.callback(
    Output("store-df","data"), Output("store-meta","data"), Output("store-site-info","data"),
    Input("upload","contents"), State("upload","filename"),
)
def load_file(contents, filename):
    """Parse the uploaded data file and store it. Triggers all downstream callbacks."""
    if not contents: return None, {}, {}
    df, meta = parse_upload(contents, filename)
    if "error" in meta: return None, meta, {}
    return df2j(df), meta, infer_site_meta(df, meta)

@app.callback(
    Output("store-litho","data"), Output("litho-badge","children"),
    Input("upload-litho","contents"), State("upload-litho","filename"),
)
def load_litho(contents, filename):
    """Parse the optional lithology file and validate its required columns."""
    if not contents:
        return None, html.Div("No litho file loaded.", style={"color":C["muted"],"fontSize":"10px"})
    df_litho, meta = parse_upload(contents, filename)
    if "error" in meta or df_litho is None:
        return None, html.Div(f"Error: {meta.get('error','Unknown')}",
                               style={"color":C["danger"],"fontSize":"10px"})
    df_litho, error = resolve_litho_columns(df_litho)
    if error:
        return None, html.Div([
            html.Div("Could not identify lithology columns.",
                     style={"color":C["danger"],"fontSize":"11px","fontWeight":"700","marginBottom":"6px"}),
            html.Pre(error, style={"color":C["muted"],"fontSize":"9px","fontFamily":FONT,
                                   "whiteSpace":"pre-wrap","maxHeight":"160px","overflowY":"auto",
                                   "background":C["bg"],"padding":"8px","borderRadius":"4px",
                                   "border":f"1px solid {C['border']}"}),
        ])
    n_units     = len(df_litho)
    depth_range = f"{df_litho['top_mbsf'].min():.1f} - {df_litho['bottom_mbsf'].max():.1f} mbsf"
    return df2j(df_litho), html.Div([
        html.Span(f"✓ {filename}",
                  style={"background":C["border"],"padding":"3px 8px","borderRadius":"10px",
                         "fontSize":"10px","color":C["accent2"],"fontFamily":FONT}),
        html.Span(f"{n_units} units · {depth_range}",
                  style={"color":C["muted"],"fontSize":"10px","fontFamily":FONT,"marginLeft":"6px"}),
    ])

@app.callback(
    Output("meta-banner","children"),
    Input("store-site-info","data"),
    Input("meta-expedition","value"), Input("meta-site-hole","value"),
    Input("meta-lat","value"), Input("meta-lon","value"),
    Input("meta-water-depth","value"), Input("meta-recovery","value"),
)
def update_meta_banner(site_info, expedition, site_hole, lat, lon, water_depth, recovery):
    if not site_info:
        return html.Div("Upload a file to see site metadata.",
                        style={"color":C["muted"],"fontSize":"11px","padding":"10px 20px","fontFamily":FONT})
    manual = {"expedition":expedition or "","site_hole":site_hole or "",
              "lat":lat or "","lon":lon or "","water_depth":water_depth or "","recovery":recovery or ""}
    return build_metadata_bar(site_info, manual)

@app.callback(
    Output("x-col","options"), Output("y-col","options"),
    Output("color-col","options"), Output("depth-curves","options"),
    Input("store-meta","data"),
)
def set_options(meta):
    """Populate axis dropdowns and curve checklist from the uploaded file columns."""
    if not meta or "columns" not in meta: return [],[],[],[]
    cols = meta["columns"]; num = meta["numeric_cols"]
    col_opts   = [{"label":c,"value":c} for c in cols]
    color_opts = [{"label":"None","value":"None"}] + col_opts
    num_opts   = [{"label":c,"value":c} for c in num]
    return col_opts, col_opts, color_opts, num_opts

@app.callback(
    Output("x-col","value"), Output("y-col","value"),
    Output("color-col","value"), Output("depth-curves","value"),
    Input("store-meta","data"), prevent_initial_call=True,
)
def set_defaults(meta):
    """Auto-select sensible default axis values when a new file is loaded."""
    if not meta or "columns" not in meta: return None,None,"None",[]
    cols = meta["columns"]; num = meta["numeric_cols"]
    x_val = cols[0] if cols else None
    y_val = cols[1] if len(cols)>1 else (cols[0] if cols else None)
    curve_candidates = [c for c in num if not re.search(r"comment",c,re.IGNORECASE)]
    curve_vals = curve_candidates[1:4] if len(curve_candidates)>1 else curve_candidates[:1]
    return x_val, y_val, "None", curve_vals

@app.callback(
    Output("y-lbl","style"), Output("y-col","style"),
    Output("color-lbl","style"), Output("color-col","style"),
    Output("curves-lbl","style"), Output("depth-curves","style"),
    Input("chart-type","value"),
)
def toggle_controls(ctype):
    """Show or hide sidebar controls based on the selected chart type.
    The depth log mode shows curve checkboxes; scatter mode shows x/y dropdowns.
    """
    show=dict(LBL); hide=dict(LBL,display="none")
    show_dd=dict(DD); hide_dd=dict(DD,display="none")
    if ctype in ("histogram","heatmap"):
        return hide,hide_dd,hide,hide_dd,hide,{"display":"none"}
    if ctype == "depthlog":
        return hide,hide_dd,hide,hide_dd,show,{}
    if ctype == "scatter":
        return show,show_dd,show,show_dd,hide,{"display":"none"}
    return show,show_dd,hide,hide_dd,hide,{"display":"none"}

@app.callback(Output("kpi-bar","children"),
              Input("store-df","data"), Input("store-meta","data"))
def update_kpis(jdf, meta):
    """Build the row of summary statistic cards shown above the chart."""
    if not jdf:
        return [html.Span("Upload a file to begin.",style={"color":C["muted"],"fontSize":"12px"})]
    df = j2df(jdf); cards = []
    for col in meta.get("numeric_cols",[])[:6]:
        v = df[col].dropna()
        if not len(v): continue
        cards.append(html.Div([
            html.Div(col, style={"color":C["muted"],"fontSize":"9px","letterSpacing":"1px"}),
            html.Div(f"{v.mean():.3g}", style={"color":C["text"],"fontSize":"17px","fontWeight":"700"}),
            html.Div(f"min {v.min():.3g}  max {v.max():.3g}",style={"color":C["muted"],"fontSize":"9px"}),
        ], style={**CARD,"minWidth":"110px","padding":"8px 12px"}))
    return cards

@app.callback(
    Output("main-chart","figure"),
    Input("store-df","data"), Input("store-litho","data"),
    Input("chart-type","value"), Input("x-col","value"),
    Input("y-col","value"), Input("color-col","value"),
    Input("depth-curves","value"), Input("overlay-opts","value"),
)
def update_chart(jdf, jlitho, ctype, x, y, color, curves, overlays):
    """Rebuild the main chart whenever any axis, chart type, or overlay option changes."""
    if not jdf: return empty_fig()
    overlays = overlays or []
    try:
        return make_chart(j2df(jdf), ctype, x, y, color, curves or [],
                          litho_df=j2df(jlitho) if jlitho else None,
                          show_gaps=("gaps" in overlays),
                          show_qc=("qc" in overlays),
                          show_core_tops=("core_tops" in overlays))
    except Exception as e:
        return empty_fig("Error: "+str(e), C["danger"])

@app.callback(
    Output("table-container","children"), Output("row-count","children"),
    Input("store-df","data"),
)
def update_table(jdf):
    """Render a paginated, sortable, filterable preview of the data table."""
    if not jdf:
        return html.Div("No data loaded.",style={"color":C["muted"]}), ""
    df = j2df(jdf); preview = df.head(200)
    tbl = dash_table.DataTable(
        data=preview.to_dict("records"),
        columns=[{"name":c,"id":c} for c in preview.columns],
        page_size=10, sort_action="native", filter_action="native",
        style_table={"overflowX":"auto"},
        style_header={"backgroundColor":C["bg"],"color":C["accent"],
                      "fontWeight":"700","fontSize":"10px","border":f"1px solid {C['border']}"},
        style_cell={"backgroundColor":C["panel"],"color":C["text"],"fontSize":"11px",
                    "padding":"7px 11px","border":f"1px solid {C['border']}",
                    "fontFamily":FONT,"maxWidth":"160px","overflow":"hidden","textOverflow":"ellipsis"},
        style_data_conditional=[
            {"if":{"row_index":"odd"},"backgroundColor":C["bg"]},
            *[{"if":{"filter_query":f'{{{col}}} != ""',"column_id":col},"color":C["warn"]}
              for col in preview.columns if re.search(r"comment",col,re.IGNORECASE)],
        ],
    )
    return tbl, f"showing first 200 of {len(df):,} rows"

# =============================================================================
# POST-EXPEDITION TAB CALLBACKS
# =============================================================================
for _ds in ["a","b"]:
    @app.callback(
        Output(f"pe-{_ds}-upload-panel","style"),
        Output(f"pe-{_ds}-lims-panel","style"),
        Output(f"pe-{_ds}-pangaea-panel","style"),
        Input(f"pe-{_ds}-source","value"),
    )
    def pe_toggle(src, ds=_ds):
        up  = {}  if src=="upload"  else {"display":"none"}
        lm  = {}  if src=="lims"    else {"display":"none"}
        pg  = {}  if src=="pangaea" else {"display":"none"}
        return up, lm, pg

# ── Post-Expedition: upload load ──────────────────────────────────────────────
@app.callback(
    Output("pe-store-a","data"), Output("pe-a-upload-status","children"),
    Input("pe-a-upload","contents"), State("pe-a-upload","filename"),
    prevent_initial_call=True,
)
def pe_load_a_upload(contents, filename):
    """Parse a locally uploaded file for Dataset A."""
    df, meta = parse_upload(contents, filename)
    if "error" in meta: return None, f"Error: {meta['error']}"
    return df2j(df), f"✓ {filename}  ({len(df):,} rows)"

@app.callback(
    Output("pe-store-b","data"), Output("pe-b-upload-status","children"),
    Input("pe-b-upload","contents"), State("pe-b-upload","filename"),
    prevent_initial_call=True,
)
def pe_load_b_upload(contents, filename):
    """Parse a locally uploaded file for Dataset B."""
    df, meta = parse_upload(contents, filename)
    if "error" in meta: return None, f"Error: {meta['error']}"
    return df2j(df), f"✓ {filename}  ({len(df):,} rows)"

# ── Post-Expedition: LIMS fetch ───────────────────────────────────────────────
@app.callback(
    Output("pe-store-a","data",allow_duplicate=True),
    Output("pe-a-lims-status","children"),
    Input("pe-fetch-a-lims","n_clicks"),
    State("pe-a-report","value"), State("pe-a-lims-exp","value"),
    State("pe-a-lims-site","value"), State("pe-a-lims-hole","value"),
    prevent_initial_call=True,
)
def pe_lims_a(n, report, exp, site, hole):
    """Fetch Dataset A from the LIMS/LORE database."""
    if not n or not report or not exp: return None, "Select report and expedition"
    df, err = fetch_lore(report, exp, site or "", hole or "")
    if err: return None, f"LIMS error: {err[:80]}"
    return df2j(df), f"✓ LIMS {LORE_REPORTS.get(report,report)} Exp {exp}  ({len(df):,} rows)"

@app.callback(
    Output("pe-store-b","data",allow_duplicate=True),
    Output("pe-b-lims-status","children"),
    Input("pe-fetch-b-lims","n_clicks"),
    State("pe-b-report","value"), State("pe-b-lims-exp","value"),
    State("pe-b-lims-site","value"), State("pe-b-lims-hole","value"),
    prevent_initial_call=True,
)
def pe_lims_b(n, report, exp, site, hole):
    """Fetch Dataset B from the LIMS/LORE database."""
    if not n or not report or not exp: return None, "Select report and expedition"
    df, err = fetch_lore(report, exp, site or "", hole or "")
    if err: return None, f"LIMS error: {err[:80]}"
    return df2j(df), f"✓ LIMS {LORE_REPORTS.get(report,report)} Exp {exp}  ({len(df):,} rows)"

# ── Post-Expedition: PANGAEA fetch ────────────────────────────────────────────
@app.callback(
    Output("pe-store-a","data",allow_duplicate=True),
    Output("pe-a-pangaea-status","children"),
    Input("pe-fetch-a-pangaea","n_clicks"),
    State("pe-a-pangaea-id","value"),
    prevent_initial_call=True,
)
def pe_pangaea_a(n, pid):
    """Fetch Dataset A from PANGAEA by numeric dataset ID."""
    if not n or not pid: return None, "Enter a PANGAEA dataset ID"
    df, err = fetch_pangaea_doi(pid.strip())
    if err: return None, f"PANGAEA error: {err[:80]}"
    return df2j(df), f"✓ PANGAEA {pid}  ({len(df):,} rows)"

@app.callback(
    Output("pe-store-b","data",allow_duplicate=True),
    Output("pe-b-pangaea-status","children"),
    Input("pe-fetch-b-pangaea","n_clicks"),
    State("pe-b-pangaea-id","value"),
    prevent_initial_call=True,
)
def pe_pangaea_b(n, pid):
    """Fetch Dataset B from PANGAEA by numeric dataset ID."""
    if not n or not pid: return None, "Enter a PANGAEA dataset ID"
    df, err = fetch_pangaea_doi(pid.strip())
    if err: return None, f"PANGAEA error: {err[:80]}"
    return df2j(df), f"✓ PANGAEA {pid}  ({len(df):,} rows)"

# ── Post-Expedition: PANGAEA search ──────────────────────────────────────────
@app.callback(
    Output("pe-a-pangaea-results","children"),
    Input("pe-search-a-pangaea","n_clicks"),
    State("pe-a-pangaea-query","value"),
    prevent_initial_call=True,
)
def pe_pangaea_search_a(n, query):
    """Search PANGAEA and display results so the user can copy an ID for Dataset A."""
    if not n or not query: return ""
    results, err = search_pangaea(query)
    if err: return html.Div(f"Search error: {err[:60]}", style={"color":C["danger"],"fontSize":"10px"})
    if not results: return html.Div("No results.", style={"color":C["muted"],"fontSize":"10px"})
    return html.Div([
        html.Div("Click an ID to copy it into the field above:",
                 style={"color":C["muted"],"fontSize":"9px","marginBottom":"4px"}),
        *[html.Div(r["label"], style={
            "fontSize":"10px","color":C["accent3"],"cursor":"pointer",
            "padding":"3px 0","borderBottom":f"1px solid {C['border']}",
            "fontFamily":FONT,
          }) for r in results[:8]]
    ])

@app.callback(
    Output("pe-b-pangaea-results","children"),
    Input("pe-search-b-pangaea","n_clicks"),
    State("pe-b-pangaea-query","value"),
    prevent_initial_call=True,
)
def pe_pangaea_search_b(n, query):
    """Search PANGAEA and display results so the user can copy an ID for Dataset B."""
    if not n or not query: return ""
    results, err = search_pangaea(query)
    if err: return html.Div(f"Search error: {err[:60]}", style={"color":C["danger"],"fontSize":"10px"})
    if not results: return html.Div("No results.", style={"color":C["muted"],"fontSize":"10px"})
    return html.Div([
        html.Div("Click an ID to copy it into the field above:",
                 style={"color":C["muted"],"fontSize":"9px","marginBottom":"4px"}),
        *[html.Div(r["label"], style={
            "fontSize":"10px","color":C["accent3"],"cursor":"pointer",
            "padding":"3px 0","borderBottom":f"1px solid {C['border']}",
            "fontFamily":FONT,
          }) for r in results[:8]]
    ])

# ── Post-Expedition: depth col dropdowns ─────────────────────────────────────
@app.callback(
    Output("pe-depth-a","options"), Output("pe-depth-a","value"),
    Output("pe-depth-b","options"), Output("pe-depth-b","value"),
    Input("pe-store-a","data"), Input("pe-store-b","data"),
)
def pe_depth_opts(da, db):
    """Populate depth column dropdowns from the columns available in each dataset."""
    def ov(d):
        if not d: return [],None
        df = j2df(d)
        return [{"label":c,"value":c} for c in df.columns], find_depth_col(df)
    oa,va = ov(da); ob,vb = ov(db)
    return oa,va,ob,vb

# ── Post-Expedition: status cards ─────────────────────────────────────────────
@app.callback(
    Output("pe-status-a","children"), Output("pe-status-b","children"),
    Input("pe-store-a","data"), Input("pe-store-b","data"),
)
def pe_status_cards(da, db):
    """Update the status cards showing row/column counts for each loaded dataset."""
    def card(d, label, color):
        if not d: return [html.Span(f"{label}: ",style={"color":color}), "no data loaded"]
        df = j2df(d)
        return [html.Span(f"{label}  ",style={"color":color,"fontWeight":"600"}),
                html.Span(f"{len(df):,} rows x {len(df.columns)} cols")]
    return card(da,"Dataset A",C["accent"]), card(db,"Dataset B",C["accent3"])

# ── Post-Expedition: expedition filter ───────────────────────────────────────
@app.callback(
    Output("pe-exp-filter","options"), Output("pe-exp-filter","value"),
    Input("pe-store-a","data"), Input("pe-store-b","data"), Input("pe-merged-store","data"),
)
def pe_exp_opts(da, db, dm):
    """Populate expedition filter checkboxes from any expedition column in the data."""
    exps = set()
    for d in [da,db,dm]:
        if d:
            df = j2df(d); exps.update(get_expeditions_from_df(df))
    opts = [{"label":f" {e}","value":e} for e in sorted(exps)]
    return opts, [o["value"] for o in opts]

@app.callback(
    Output("pe-exp-filter","value",allow_duplicate=True),
    Input("pe-exp-all-none","n_clicks"),
    State("pe-exp-filter","options"), State("pe-exp-filter","value"),
    prevent_initial_call=True,
)
def pe_exp_toggle(n, opts, current):
    """Toggle all expedition checkboxes on or off with the All/None button."""
    all_vals = [o["value"] for o in opts]
    return [] if set(current)==set(all_vals) else all_vals

# ── Post-Expedition: merge ────────────────────────────────────────────────────
@app.callback(
    Output("pe-merged-store","data"), Output("pe-status-merged","children"),
    Input("pe-merge-btn","n_clicks"),
    State("pe-store-a","data"), State("pe-store-b","data"),
    State("pe-depth-a","value"), State("pe-depth-b","value"),
    State("pe-tolerance","value"),
    prevent_initial_call=True,
)
def pe_merge(n, da, db, dca, dcb, tol):
    if not da or not db: return None, "Load both datasets first"
    dfa = j2df(da); dfb = j2df(db)
    dca = dca or find_depth_col(dfa); dcb = dcb or find_depth_col(dfb)
    tol_cm = float(tol) if tol else 2.0
    try:
        dfa2 = dfa.rename(columns={dca:"depth_key"})
        dfb2 = dfb.rename(columns={dcb:"depth_key"})
        merged = depth_tolerance_merge(dfa2, dfb2, tol_cm)
        n_match = merged["depth_key"].notna().sum()
        status = [html.Span("Merged  ",style={"color":C["accent2"],"fontWeight":"600"}),
                  html.Span(f"{len(merged):,} rows, {n_match:,} depth matches (tol={tol_cm} cm)")]
        return df2j(merged), status
    except Exception as e:
        return None, f"Merge error: {str(e)[:80]}"

# ── Post-Expedition: axis dropdowns ──────────────────────────────────────────
@app.callback(
    Output("pe-xaxis","options"), Output("pe-yaxis","options"),
    Output("pe-ycols-b","options"),
    Input("pe-merged-store","data"),
)
def pe_axis_opts(dm):
    """Populate axis dropdowns from the merged dataset columns."""
    if not dm: return [],[],[]
    df = j2df(dm)
    opts = [{"label":c,"value":c} for c in df.columns]
    return opts, opts, opts

@app.callback(
    Output("pe-xaxis","value"), Output("pe-yaxis","value"),
    Output("pe-ycols-b","value"),
    Input("pe-xaxis","options"), prevent_initial_call=True,
)
def pe_axis_defaults(opts):
    if not opts: return None, None, None
    cols = [o["value"] for o in opts]
    depth = next((c for c in cols if "depth" in c.lower()), cols[0])
    # Split cols into _A suffix (dataset A) and _B suffix (dataset B)
    a_cols = [c for c in cols if c.endswith("_A") and c != depth]
    b_cols = [c for c in cols if c.endswith("_B") and c != depth]
    # Fall back to first few non-depth cols if no suffix pattern
    others = [c for c in cols if c not in (depth,"depth_key")]
    y_a = a_cols[:3] if a_cols else others[:2]
    y_b = b_cols[:3] if b_cols else others[2:4]
    return depth, y_a, y_b

# ── Post-Expedition: merged expeditions readout ───────────────────────────────
@app.callback(
    Output("pe-merged-expeditions","children"),
    Input("pe-merged-store","data"), Input("pe-exp-filter","value"),
)
def pe_merged_exp_readout(dm, selected):
    """Show which expedition numbers are in the currently filtered merged data."""
    if not dm: return "n/a"
    df = j2df(dm)
    exps = get_expeditions_from_df(df)
    filtered = [e for e in exps if e in (selected or [])]
    return ", ".join(filtered) if filtered else "n/a"


# ── Post-Expedition: show rolling window control only in rolling mode ─────────
@app.callback(
    Output("pe-rolling-ctrl","style"),
    Input("pe-chart-mode","value"),
)
def pe_rolling_toggle(mode):
    """Show the rolling window size control only when rolling mean mode is active."""
    return {} if mode == "rolling" else {"display":"none"}

# ── Post-Expedition: chart ────────────────────────────────────────────────────
@app.callback(
    Output("pe-chart","figure"),
    Input("pe-merged-store","data"), Input("pe-exp-filter","value"),
    Input("pe-xaxis","value"), Input("pe-yaxis","value"),
    Input("pe-ycols-b","value"), Input("pe-chart-mode","value"),
    Input("pe-rolling-window","value"),
)
def pe_chart(dm, selected, xcol, ycols_a, ycols_b, mode, rwin):
    if not dm or not xcol: return empty_fig("Merge two datasets to visualize")
    df = j2df(dm)
    exp_col = next((c for c in df.columns if "expedition" in c.lower()), None)
    if exp_col and selected:
        df = df[df[exp_col].astype(str).isin(selected)]
    df = df.dropna(subset=[xcol]).sort_values(xcol).reset_index(drop=True)

    ycols_a = [ycols_a] if isinstance(ycols_a, str) else (ycols_a or [])
    ycols_b = [ycols_b] if isinstance(ycols_b, str) else (ycols_b or [])
    ycols_a = [c for c in ycols_a if c in df.columns]
    ycols_b = [c for c in ycols_b if c in df.columns]
    all_cols = ycols_a + ycols_b
    if not all_cols: return empty_fig("Select columns for Dataset A and/or B")

    colors_a = [C["accent"], "#bc8cff", "#ff7b72"]
    colors_b = [C["accent3"], C["accent2"], "#f0883e"]
    cfg_base = {**PLOT_CFG, "height": 600}
    cfg_base.pop("xaxis", None); cfg_base.pop("yaxis", None)
    axis_kw  = dict(gridcolor=C["border"], linecolor=C["border"])

    # ── MODE 1: Depth tracks — independent x-axis per column ─────────────────
    if mode == "tracks":
        n_cols = len(all_cols)
        fig = make_subplots(rows=1, cols=n_cols, shared_yaxes=True,
                            horizontal_spacing=0.03)
        for i, (yc, color) in enumerate(
            [(c, colors_a[j % len(colors_a)]) for j, c in enumerate(ycols_a)] +
            [(c, colors_b[j % len(colors_b)]) for j, c in enumerate(ycols_b)]
        ):
            sub = df[[xcol, yc]].dropna()
            fig.add_trace(go.Scatter(
                x=sub[yc], y=sub[xcol], mode="lines", name=yc,
                line=dict(color=color, width=1.5),
            ), row=1, col=i + 1)
            fig.update_xaxes(title_text=yc, title_font=dict(size=10),
                             **axis_kw, row=1, col=i + 1)
        fig.update_yaxes(title_text=xcol + " (mbsf)", autorange="reversed",
                         **axis_kw)
        fig.update_layout(showlegend=False, **cfg_base)
        return fig

    # ── MODE 2: Correlation scatter — col_a vs col_b, colored by depth ──────
    if mode == "scatter":
        if not ycols_a or not ycols_b:
            return empty_fig("Select at least one column from each dataset")
        xa = ycols_a[0]; xb = ycols_b[0]
        sub = df[[xcol, xa, xb]].dropna()
        fig = go.Figure(go.Scatter(
            x=sub[xa], y=sub[xb],
            mode="markers",
            marker=dict(
                color=sub[xcol],
                colorscale="Viridis_r",
                size=5, opacity=0.75,
                colorbar=dict(title=xcol + " mbsf",
                              tickfont=dict(color=C["muted"]),
                              titlefont=dict(color=C["muted"])),
                showscale=True,
            ),
            hovertemplate=f"{xa}: %{{x:.3g}}<br>{xb}: %{{y:.3g}}<br>depth: %{{marker.color:.1f}} mbsf<extra></extra>",
        ))
        # regression line
        try:
            m, b = np.polyfit(sub[xa].values, sub[xb].values, 1)
            x_r = np.linspace(sub[xa].min(), sub[xa].max(), 200)
            r = np.corrcoef(sub[xa].values, sub[xb].values)[0, 1]
            fig.add_trace(go.Scatter(
                x=x_r, y=m * x_r + b, mode="lines", name=f"r={r:.3f}",
                line=dict(color=C["danger"], width=1.5, dash="dash"),
            ))
        except Exception:
            pass
        fig.update_layout(**{**PLOT_CFG, "height": 600},
                          xaxis=dict(title=xa, **axis_kw),
                          yaxis=dict(title=xb, **axis_kw),
                          showlegend=True)
        return fig

    # ── MODE 3: Dual-axis overlay — two y-scales, shared depth axis ──────────
    if mode == "dual":
        if not ycols_a or not ycols_b:
            return empty_fig("Select at least one column from each dataset")
        ya = ycols_a[0]; yb = ycols_b[0]
        fig = go.Figure()
        sub_a = df[[xcol, ya]].dropna()
        sub_b = df[[xcol, yb]].dropna()
        fig.add_trace(go.Scatter(
            x=sub_a[xcol], y=sub_a[ya], mode="lines", name=ya,
            line=dict(color=C["accent"], width=1.5), yaxis="y1",
        ))
        fig.add_trace(go.Scatter(
            x=sub_b[xcol], y=sub_b[yb], mode="lines", name=yb,
            line=dict(color=C["accent3"], width=1.5, dash="dot"), yaxis="y2",
        ))
        layout = {**PLOT_CFG, "height": 600,
            "xaxis":  dict(title=xcol + " (mbsf)", **axis_kw),
            "yaxis":  dict(title=ya, color=C["accent"], **axis_kw),
            "yaxis2": dict(title=yb, color=C["accent3"],
                           overlaying="y", side="right",
                           gridcolor="rgba(0,0,0,0)", linecolor=C["border"]),
            "showlegend": True,
            "legend": dict(bgcolor=C["panel"], bordercolor=C["border"], borderwidth=1),
        }
        fig.update_layout(**layout)
        return fig

    # ── MODE 4: Rolling mean comparison ───────────────────────────────────────
    if mode == "rolling":
        window = max(2, int(rwin or 20))
        n_cols = len(all_cols)
        fig = make_subplots(rows=1, cols=n_cols, shared_yaxes=True,
                            horizontal_spacing=0.03)
        for i, (yc, color) in enumerate(
            [(c, colors_a[j % len(colors_a)]) for j, c in enumerate(ycols_a)] +
            [(c, colors_b[j % len(colors_b)]) for j, c in enumerate(ycols_b)]
        ):
            sub = df[[xcol, yc]].dropna()
            rolled = sub[yc].rolling(window, center=True, min_periods=1).mean()
            # raw (faint)
            fig.add_trace(go.Scatter(
                x=sub[yc], y=sub[xcol], mode="lines", name=yc + " (raw)",
                line=dict(color=color, width=0.6), opacity=0.35,
                showlegend=False,
            ), row=1, col=i + 1)
            # smoothed (bold)
            fig.add_trace(go.Scatter(
                x=rolled, y=sub[xcol], mode="lines",
                name=f"{yc}  (n={window})",
                line=dict(color=color, width=2.5),
            ), row=1, col=i + 1)
            fig.update_xaxes(title_text=yc, title_font=dict(size=10),
                             **axis_kw, row=1, col=i + 1)
        fig.update_yaxes(title_text=xcol + " (mbsf)", autorange="reversed",
                         **axis_kw)
        fig.update_layout(showlegend=True,
                          legend=dict(bgcolor=C["panel"], bordercolor=C["border"],
                                      borderwidth=1, font=dict(size=10)),
                          **cfg_base)
        return fig

    return empty_fig("Select a chart mode")

# ── Post-Expedition: table ────────────────────────────────────────────────────
@app.callback(
    Output("pe-table-container","children"),
    Input("pe-merged-store","data"), Input("pe-exp-filter","value"),
)
def pe_table(dm, selected):
    """Render a preview table of the merged data, filtered by selected expeditions."""
    if not dm: return ""
    df = j2df(dm)
    exp_col = next((c for c in df.columns if "expedition" in c.lower()),None)
    if exp_col and selected:
        df = df[df[exp_col].astype(str).isin(selected)]
    preview = df.head(200)
    return dash_table.DataTable(
        data=preview.to_dict("records"),
        columns=[{"name":c,"id":c} for c in preview.columns],
        page_size=10, sort_action="native", filter_action="native",
        style_table={"overflowX":"auto"},
        style_header={"backgroundColor":C["bg"],"color":C["accent"],
                      "fontWeight":"700","fontSize":"10px","border":f"1px solid {C['border']}"},
        style_cell={"backgroundColor":C["panel"],"color":C["text"],"fontSize":"11px",
                    "padding":"7px 11px","border":f"1px solid {C['border']}",
                    "fontFamily":FONT,"maxWidth":"160px","overflow":"hidden","textOverflow":"ellipsis"},
        style_data_conditional=[{"if":{"row_index":"odd"},"backgroundColor":C["bg"]}],
    )

# ── Post-Expedition: download ─────────────────────────────────────────────────
@app.callback(
    Output("pe-download","data"),
    Input("pe-download-btn","n_clicks"),
    State("pe-merged-store","data"), State("pe-exp-filter","value"),
    prevent_initial_call=True,
)
def pe_download(n, dm, selected):
    """Trigger a CSV file download of the merged dataset when the button is clicked."""
    if not dm: return None
    df = j2df(dm)
    exp_col = next((c for c in df.columns if "expedition" in c.lower()),None)
    if exp_col and selected:
        df = df[df[exp_col].astype(str).isin(selected)]
    return dcc.send_data_frame(df.to_csv, "iodp_merged.csv", index=False)

# =============================================================================
# ENTRY POINT
# =============================================================================
# This block only runs when you execute the file directly: python app.py
# It does not run when gunicorn or another server imports the file.
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7860, debug=False)
