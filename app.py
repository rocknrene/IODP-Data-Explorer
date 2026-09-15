# =============================================================================
# IODP Explorer -- Interactive Data Visualization Dashboard
# =============================================================================
import io
import base64
import re
import json
import os
import time
import tempfile
import shutil
import zipfile
import xml.etree.ElementTree as ET
import requests

import numpy as np
import pandas as pd
import lasio
try:
    from scipy import stats as scipy_stats
    SCIPY_AVAILABLE = True
except ImportError:
    # scipy is optional — it only powers the p-value on the correlation
    # scatter's trendline. A missing optional stats package should degrade
    # that one feature, not prevent the whole app from booting.
    scipy_stats = None
    SCIPY_AVAILABLE = False

import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots

from dash import Dash, dcc, html, Input, Output, State, dash_table, ctx, ALL, no_update
import flask

# =============================================================================
# COLOR THEMES
# =============================================================================
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
C = THEMES["dark"]

def plot_cfg(theme="dark"):
    """Plotly figures are rendered as SVG and can't follow CSS variables the
    way regular HTML elements can — Plotly needs a literal color string at
    figure-build time. So charts get their palette by calling this with the
    current theme (from theme-store) rather than via the CSS-var trick used
    for the rest of the UI."""
    t = THEMES.get(theme, THEMES["dark"])
    return dict(
        paper_bgcolor=t["panel"], plot_bgcolor=t["bg"],
        font=dict(color=t["text"], family="monospace"),
        xaxis=dict(gridcolor=t["border"], zerolinecolor=t["border"]),
        yaxis=dict(gridcolor=t["border"], zerolinecolor=t["border"]),
        colorway=[t["accent"], t["accent2"], t["accent3"], "#bc8cff", "#ff7b72"],
        margin=dict(l=55, r=20, t=40, b=50),
    )
PLOT_CFG = plot_cfg("dark")  # default/back-compat for any stray direct references
CARD = dict(background="var(--panel)", border=f"1px solid var(--border)",
            borderRadius="8px", padding="14px")
FONT = "monospace"
DD   = {"background": "var(--panel)", "color": "var(--text)",
        "border": f"1px solid var(--border)", "borderRadius": "4px"}
LBL  = {"color": "var(--muted)", "fontSize": "10px", "letterSpacing": "2px",
        "marginBottom": "4px", "marginTop": "12px", "fontFamily": FONT}
INP  = {"width": "100%", "background": "var(--bg)", "color": "var(--text)",
        "border": f"1px solid var(--border)", "borderRadius": "4px",
        "padding": "4px 8px", "fontSize": "11px", "fontFamily": FONT,
        "boxSizing": "border-box"}
BTN  = lambda bg: {"backgroundColor": bg, "color": "var(--bg)", "border": "none",
                   "borderRadius": "4px", "padding": "6px 12px", "cursor": "pointer",
                   "fontSize": "11px", "marginTop": "6px", "width": "100%",
                   "fontWeight": "700"}

LITHO_COLORS = {
    "clay": "#1D9E75", "silty clay": "#5DCAA5", "silt": "#888780",
    "sand": "#EF9F27", "mtd": "#D85A30", "mtd / chaotic": "#D85A30",
    "turbidite": "#378ADD", "hemipelagite": "#3fb950",
    "gravel": "#BA7517", "chalk": "#B5D4F4", "limestone": "#85B7EB",
    "basalt": "#444441", "ash": "#D3D1C7",
}
def litho_color(name):
    return LITHO_COLORS.get(str(name).lower().strip(), "#444441")

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
# GRA Bulk Density runs on nearly every core, so it doubles as a stand-in
# report for previewing a Leg's sites/holes before a report is chosen.
PREVIEW_REPORT = "gra"

PANGAEA_ES      = "https://ws.pangaea.de/es/pangaea/panmd/_search"

HEADER_KEYWORDS = [
    "depth", "lith", "facies", "unit", "section", "sample", "core",
    "upper", "lower", "top", "bottom", "description", "interval", "formation",
]

# =============================================================================
# LIMS CSV exports have metadata rows like:
#   "Exp","Site","Hole","Core","Type","Sect","A/W","Offset (cm)","Depth CSF-A (m)",...
# The actual column header is identifiable by LIMS-specific keywords.
LIMS_HEADER_KEYWORDS = [
    "exp", "site", "hole", "core", "type", "sect", "offset", "depth csf",
    "depth mbsf", "depth (m", "csf-a", "mcd", "text id", "label id",
]

def is_lims_header_row(line):
    """Check if a CSV line looks like a LIMS data header row."""
    lower = line.lower()
    hits = sum(1 for kw in LIMS_HEADER_KEYWORDS if kw in lower)
    return hits >= 3

def extract_lims_metadata(raw_bytes, encoding="utf-8"):
    """
    Scan the first ~30 rows of a LIMS CSV for metadata values (expedition, site, hole)
    that appear in key:value style rows above the actual column header.
    Returns dict with whatever we find.
    """
    meta = {}
    try:
        lines = raw_bytes.decode(encoding, errors="replace").splitlines()
    except Exception:
        return meta
    for line in lines[:30]:
        stripped = line.strip().strip('"')
        # LIMS sometimes writes "Expedition: 400" or has it as a cell value
        m = re.search(r'expedition["\s,]*[:\s]+["\s]*(\d+)', line, re.IGNORECASE)
        if m and "expedition" not in meta:
            meta["expedition"] = m.group(1)
        m = re.search(r'\bsite["\s,]*[:\s]+["\s]*([A-Z]\d+)', line, re.IGNORECASE)
        if m and "site" not in meta:
            meta["site"] = m.group(1)
        m = re.search(r'\bhole["\s,]*[:\s]+["\s]*([A-Z])\b', line, re.IGNORECASE)
        if m and "hole" not in meta:
            meta["hole"] = m.group(1)
    return meta

def detect_header_row(raw_bytes, encoding="utf-8", n_scan=30):
    """
    Scan first n_scan rows and return the row index that looks most like a
    real data header. Prefers rows that match LIMS-style headers first,
    then falls back to generic HEADER_KEYWORDS scoring.
    """
    try:
        lines = raw_bytes.decode(encoding, errors="replace").splitlines()
    except Exception:
        return 0
    # LIMS-first pass
    for i, line in enumerate(lines[:n_scan]):
        if is_lims_header_row(line):
            return i
    # Generic fallback
    best_row, best_score = 0, 0
    for i, line in enumerate(lines[:n_scan]):
        score = sum(1 for kw in HEADER_KEYWORDS if kw in line.lower())
        if score > best_score:
            best_score, best_row = score, i
    return best_row

# =============================================================================
# FILE PARSING
# =============================================================================
def parse_upload(contents, filename):
    """
    Decode a Dash-uploaded file and return (DataFrame, meta).
    Now supports .csv, .tsv, .xlsx, .xls, .las
    """
    _, b64 = contents.split(",")
    raw    = base64.b64decode(b64)
    fname  = filename.lower()
    meta   = {"filename": filename}
    try:
        # Accepts TSV as well as CSV
        if fname.endswith(".csv") or fname.endswith(".tsv"):
            sep = "\t" if fname.endswith(".tsv") else ","
            df = None
            lims_meta = {}
            for enc in ["utf-8", "latin-1", "cp1252", "utf-16"]:
                try:
                    header_row = detect_header_row(raw, encoding=enc)
                    # Try to grab LIMS metadata from rows above header
                    if header_row > 0:
                        lims_meta = extract_lims_metadata(raw, encoding=enc)
                    df = pd.read_csv(io.StringIO(raw.decode(enc)),
                                     header=header_row, skip_blank_lines=True,
                                     sep=sep)
                    df = df.dropna(axis=1, how="all").dropna(how="all").reset_index(drop=True)
                    break
                except Exception:
                    continue
            if df is None:
                return None, {"error": f"Could not decode {'TSV' if fname.endswith('.tsv') else 'CSV'}"}
            meta["format"] = "TSV" if fname.endswith(".tsv") else "CSV"
            # Attach LIMS metadata if found
            if lims_meta:
                meta["lims_meta"] = lims_meta

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
            # Error message lists supported formats
            return None, {"error": f"Unsupported file type: {filename}\nSupported formats: .csv, .tsv, .xlsx, .xls, .las"}
        meta.update(rows=len(df), cols=len(df.columns),
                    columns=list(df.columns),
                    numeric_cols=df.select_dtypes(include="number").columns.tolist())
        return df, meta
    except Exception as e:
        return None, {"error": str(e)}

def df2j(df):
    return df.to_json(date_format="iso", orient="split") if df is not None else None

def j2df(j):
    return pd.read_json(io.StringIO(j), orient="split") if j else None

def empty_fig(msg="Upload a file to begin", color=None, theme="dark"):
    t = THEMES.get(theme, THEMES["dark"])
    fig = go.Figure()
    fig.update_layout(**plot_cfg(theme), annotations=[dict(
        text=msg, xref="paper", yref="paper", x=0.5, y=0.5,
        showarrow=False, font=dict(color=color or t["muted"], size=15))])
    return fig

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
    Auto-extract expedition/site info. Now also reads LIMS metadata if detected.
    """
    info = {}
    cols_lower = {c.lower(): c for c in df.columns}

    # Pull from LIMS-detected metadata first
    lims_meta = meta.get("lims_meta", {})
    if lims_meta.get("expedition"):
        info["expedition"] = lims_meta["expedition"]
    if lims_meta.get("site") and lims_meta.get("hole"):
        info["site_hole"] = lims_meta["site"] + lims_meta["hole"]
    elif lims_meta.get("site"):
        info["site_hole"] = lims_meta["site"]

    # Also try to read from column values (Exp, Site, Hole columns in LIMS data)
    for col_key, info_key in [("exp","expedition"), ("expedition","expedition")]:
        if col_key in cols_lower and info_key not in info:
            vals = df[cols_lower[col_key]].dropna().astype(str).unique()
            if len(vals):
                info[info_key] = vals[0]
    # Build site_hole from Site+Hole columns
    if "site_hole" not in info:
        site_val = hole_val = None
        for cand in ["site"]:
            if cand in cols_lower:
                v = df[cols_lower[cand]].dropna().astype(str).unique()
                if len(v): site_val = v[0]
        for cand in ["hole"]:
            if cand in cols_lower:
                v = df[cols_lower[cand]].dropna().astype(str).unique()
                if len(v): hole_val = v[0]
        if site_val:
            info["site_hole"] = site_val + (hole_val or "")

    # JCORES-style ID fallback
    if "site_hole" not in info:
        for cand in ["jcores_sampleid","sampleid","sample_id","sample"]:
            if cand in cols_lower:
                first = str(df[cols_lower[cand]].dropna().iloc[0]) if len(df) else ""
                m = re.match(r"([A-Z]\d{3,4}[A-Z]?)", first)
                if m:
                    info["site_hole"] = m.group(1)
                break

    for cand in ["topdepth_mbsf","topdepth_mbsf_mcsf-a","depth_mbsf","dept","depth",
                 "top_depth","top_mbsf","depth csf-a (m)","depth (m)"]:
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
    def field(label, value):
        return html.Div([
            html.Div(label, style={"color":"var(--muted)","fontSize":"9px",
                                   "letterSpacing":"1.5px","fontFamily":FONT}),
            html.Div(value, style={"color":"var(--text)","fontSize":"13px",
                                   "fontWeight":"600","fontFamily":FONT}),
        ], style={"marginRight":"20px"})
    site_hole  = manual.get("site_hole")  or info.get("site_hole","—")
    expedition = manual.get("expedition") or info.get("expedition","—")
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
                      style={"background":"var(--border)","padding":"3px 10px",
                             "borderRadius":"12px","fontSize":"11px","fontFamily":FONT}),
            html.Span(info.get("fmt",""),
                      style={"background":"var(--accent)","color":"var(--bg)","padding":"3px 10px",
                             "borderRadius":"12px","fontSize":"11px","fontWeight":"700"}),
            html.Span(f"{info.get('rows','')} rows",
                      style={"color":"var(--muted)","fontSize":"11px"}),
        ], style={"display":"flex","gap":"8px","alignItems":"center"}),
    ], style={"display":"flex","justifyContent":"space-between","alignItems":"center",
              "padding":"10px 20px","background":"var(--panel)",
              "borderBottom":f"1px solid var(--border)","flexWrap":"wrap","gap":"8px"})

# =============================================================================
# CORE-TOP, QC, AND GAP HELPERS
# =============================================================================
def extract_core_tops(df):
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
    for col in df.columns:
        if re.search(r"comment", col, re.IGNORECASE):
            return col
    return None

def find_recovery_gaps(df, depth_col, gap_threshold_m=5.0):
    if depth_col not in df.columns:
        return []
    depths = df[depth_col].dropna().sort_values().values
    return [(float(depths[i]), float(depths[i+1]))
            for i in range(len(depths)-1)
            if depths[i+1]-depths[i] > gap_threshold_m]

# =============================================================================
# ONLINE DATA FETCH HELPERS
# =============================================================================
def _find_col(df, keys):
    """Find the first column whose header matches one of the given keywords
    (case-insensitive, prefix match — e.g. 'exp' matches 'Exp' or 'Expedition')."""
    for c in df.columns:
        cl = c.lower().strip()
        if any(cl == k or cl.startswith(k) for k in keys):
            return c
    return None

def _restrict_to_request(df, expedition, site, hole):
    """LORE's filters aren't guaranteed to actually restrict the report — an
    unrecognized site/hole value can come back as an unfiltered (or only
    partially filtered) result instead of zero rows. Re-check the returned
    rows against what was actually requested so a typo'd or nonexistent
    site/hole doesn't get reported as a clean, on-target fetch."""
    exp_col = _find_col(df, ["exp", "leg"])
    if exp_col is not None and expedition:
        df = df[df[exp_col].astype(str).str.strip().str.lower()
                 == str(expedition).strip().lower()]
    site_col = _find_col(df, ["site"])
    if site_col is not None and site:
        df = df[df[site_col].astype(str).str.strip().str.lower()
                 == str(site).strip().lower()]
    hole_col = _find_col(df, ["hole"])
    if hole_col is not None and hole:
        df = df[df[hole_col].astype(str).str.strip().str.lower()
                 == str(hole).strip().lower()]
    return df

def fetch_lore(report_name, expedition, site="", hole=""):
    filters = [f"x_expedition in ('{expedition}')"]
    if site: filters.append(f"x_site in ('{site}')")
    if hole: filters.append(f"x_hole in ('{hole}')")
    url = (f"{LORE_BASE}?reportName={report_name}"
           f"&appl=LORE&action=download&format=csv"
           f"&filters={json.dumps(filters)}")
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        text = r.text
        df = pd.read_csv(io.StringIO(text))
        if df.empty:
            return None, "No data returned — check expedition/site/report combination"
        if len(df.columns) < 3:
            # Real LIMS/LORE physical-property exports carry several id/depth
            # columns (Exp, Site, Hole, Core, ... Depth, plus the measurement
            # itself). A single- or two-column result usually means the
            # response wasn't a real CSV at all — e.g. an HTML error or
            # login page that pandas still parsed as one text column —
            # not that the report legitimately has that little data.
            snippet = text.strip().splitlines()[0][:120] if text.strip() else "(empty response)"
            return None, f"Unexpected response ({len(df.columns)} column) — got: {snippet}"
        n_raw = len(df)
        df = _restrict_to_request(df, expedition, site, hole)
        if df.empty:
            requested = ", ".join(f"{k}={v}" for k, v in
                                   [("expedition",expedition),("site",site),("hole",hole)] if v)
            return None, (f"LORE returned {n_raw:,} rows but none matched {requested} — "
                          f"check that this site/hole exists for this expedition")
        return df, None
    except Exception as e:
        return None, str(e)

def fetch_pangaea_doi(pangaea_id):
    """Downloads a PANGAEA dataset's tab-separated data table. PANGAEA wraps
    its metadata (citation, parameters, license, etc.) in a C-style /* ... */
    block comment ahead of the real table — not // line comments — confirmed
    against the official pangaeapy client's own parsing logic. Requesting
    with an explicit tab-separated-values Accept header (rather than a
    ?format= query param) also matches that client and avoids an HTML
    landing page being returned instead of the raw table."""
    url = f"https://doi.pangaea.de/10.1594/PANGAEA.{pangaea_id}"
    try:
        r = requests.get(url, timeout=60,
                         headers={"Accept": "text/tab-separated-values"})
        r.raise_for_status()
        clean = re.sub(r"/\*(.*)\*/", "", r.text, count=1, flags=re.DOTALL).strip()
        df = pd.read_csv(io.StringIO(clean), sep="\t", skip_blank_lines=True,
                         on_bad_lines="skip")
        df = df.dropna(axis=1, how="all").dropna(how="all").reset_index(drop=True)
        if df.empty:
            return None, "Dataset is empty or could not be parsed"
        return df, None
    except Exception as e:
        return None, str(e)

_PANGAEA_META_NS = {"md": "http://www.pangaea.de/MetaData"}

def fetch_pangaea_title(pangaea_id, timeout=10):
    """Fetches a dataset's real title from its metadata XML — confirmed
    against the official pangaeapy client's own extraction logic (it reads
    ./md:citation/md:title from the same doi.pangaea.de URL used for data,
    just with a different Accept header). The ES search index's own 'title'
    field is unreliable/often empty, especially for older legacy datasets
    like the DSDP/ODP ones this app's PANGAEA fallback mostly surfaces —
    which is why the pick list was showing bare DOI URLs instead of titles."""
    try:
        r = requests.get(f"https://doi.pangaea.de/10.1594/PANGAEA.{pangaea_id}",
                         timeout=timeout,
                         headers={"Accept": "application/vnd.pangaea.metadata+xml"})
        r.raise_for_status()
        root = ET.fromstring(r.text.encode())
        title_el = root.find("./md:citation/md:title", _PANGAEA_META_NS)
        return title_el.text.strip() if title_el is not None and title_el.text else None
    except Exception:
        return None

def search_pangaea(query, count=10):
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
            pid   = uri.split(".")[-1] if uri else h.get("_id", "")
            title = src.get("title", uri)
            if pid:
                results.append({"label": f"{pid} — {str(title)[:60]}", "value": pid})
        # The ES 'title' field above frequently falls back to the bare URI —
        # fetch the real title for whichever results will actually be shown
        # (capped, since this is one extra request per result and the pick
        # list itself only ever displays the first 8).
        for r_item in results[:8]:
            real_title = fetch_pangaea_title(r_item["value"])
            if real_title:
                r_item["label"] = f"{r_item['value']} — {real_title[:70]}"
        return results, None
    except Exception as e:
        return [], str(e)

def search_pangaea_legacy(leg, project="DSDP", count=15):
    """Search PANGAEA for legacy DSDP/ODP shipboard datasets tied to a Leg
    number. PANGAEA's Elasticsearch endpoint is documented (and used by its
    own R client, pangaear) as a plain free-text 'q=' search — not a
    structured match against specific field names like campaign.label,
    which was this function's original approach and returned zero hits
    for every Leg since those field names were never confirmed to exist.
    Reuses search_pangaea()'s proven free-text query for the same reason."""
    if project == "DSDP":
        query = f'"Leg {leg}" DSDP'
    else:  # ODP shipboard party datasets
        query = f'"Leg {leg}" "Shipboard Scientific Party"'
    return search_pangaea(query, count=count)

DSDP_SHINYLAUREL_URL = "https://shinylaurel.com/shiny/DSDP_data_access/"

# shinylaurel.com's DSDP_data_access app organizes legacy DSDP data by its own
# category vocabulary (paleontology/lithology-style labels), not by LORE's
# physical-property report codes — so only report types with a confident,
# known match are wired up here. Extend this once the app's full category
# list (it's a long alphabetical button list) has been confirmed.
SHINYLAUREL_DSDP_TYPES = {
    "mad": "density and porosity",
}

def fetch_dsdp_shinylaurel(data_type_label, expedition, site="", hole="", timeout=45):
    """Drives shinylaurel.com's DSDP_data_access Shiny app the way a browser
    would, since the app has no stable download API — its download link is
    scoped to a single live session (see the app's rendered HTML: the href
    is 'session/<random-token>/download/...', good only for that one
    browser session). Selects the given data type under the app's
    'Data by Type' tab, downloads the resulting file, then filters it
    locally to the requested Leg/Site/Hole, since the app hands back every
    Leg for that data type in one table rather than letting you query by Leg.

    NOTE: this has not been exercised against the live site — this sandbox
    has no network path to shinylaurel.com and no Chromium binary to run
    Selenium at all, so this is built from the app's rendered HTML rather
    than a live test. Expect to need at least one round of fixes once this
    actually runs on the deployed Space.
    """
    try:
        from selenium import webdriver
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.chrome.options import Options
    except ImportError:
        return None, "Selenium isn't installed in this environment"

    tmp_dir = tempfile.mkdtemp(prefix="dsdp_dl_")
    chrome_opts = Options()
    chrome_opts.add_argument("--headless=new")
    chrome_opts.add_argument("--no-sandbox")
    chrome_opts.add_argument("--disable-dev-shm-usage")
    chrome_opts.binary_location = os.environ.get("CHROME_BIN", "/usr/bin/chromium")
    chrome_opts.add_experimental_option("prefs", {
        "download.default_directory": tmp_dir,
        "download.prompt_for_download": False,
        "safebrowsing.enabled": True,
    })
    driver_path = os.environ.get("CHROMEDRIVER_PATH", "/usr/bin/chromedriver")

    driver = None
    df = None
    try:
        driver = webdriver.Chrome(service=Service(driver_path), options=chrome_opts)
        # Headless Chrome needs downloads explicitly allowed via CDP —
        # the prefs dict above isn't always honored in headless mode alone.
        driver.execute_cdp_cmd("Page.setDownloadBehavior", {
            "behavior": "allow", "downloadPath": tmp_dir,
        })
        wait = WebDriverWait(driver, timeout)

        driver.get(DSDP_SHINYLAUREL_URL)
        wait.until(EC.element_to_be_clickable((By.LINK_TEXT, "Data by Type"))).click()

        type_btn_xpath = (
            "//div[@id='button_list_datapreview']"
            f"//button[normalize-space(text())='{data_type_label}']"
        )
        wait.until(EC.element_to_be_clickable((By.XPATH, type_btn_xpath))).click()

        # The per-type "Download this file" link — distinguished from the
        # id="download_data_zip" "Download all data" link, a separate
        # element on the same page.
        dl_xpath = ("//a[contains(@class,'shiny-download-link') "
                   "and @id!='download_data_zip']")
        dl_link = wait.until(EC.presence_of_element_located((By.XPATH, dl_xpath)))
        dl_link.click()

        deadline = time.time() + timeout
        downloaded = None
        while time.time() < deadline:
            files = [f for f in os.listdir(tmp_dir) if not f.endswith(".crdownload")]
            if files:
                downloaded = os.path.join(tmp_dir, files[0])
                break
            time.sleep(0.5)
        if not downloaded:
            return None, "Download didn't complete in time — the app may be slow, or its layout changed"

        if downloaded.lower().endswith(".zip"):
            with zipfile.ZipFile(downloaded) as z:
                data_files = [n for n in z.namelist() if n.lower().endswith((".csv",".txt",".tsv"))]
                if not data_files:
                    return None, "Downloaded zip had no CSV/TXT/TSV file inside"
                with z.open(data_files[0]) as f:
                    df = pd.read_csv(f, sep=None, engine="python")
        else:
            df = pd.read_csv(downloaded, sep=None, engine="python")
    except Exception as e:
        return None, f"DSDP (shinylaurel.com) fetch failed: {e}"
    finally:
        if driver is not None:
            driver.quit()
        shutil.rmtree(tmp_dir, ignore_errors=True)

    if df is None or df.empty:
        return None, "No data returned"
    n_raw = len(df)
    df = _restrict_to_request(df, expedition, site, hole)
    if df.empty:
        requested = ", ".join(f"{k}={v}" for k, v in
                              [("Leg",expedition),("site",site),("hole",hole)] if v)
        return None, f"Downloaded {n_raw:,} rows but none matched {requested}"
    return df, None

def find_depth_col(df):
    for c in df.columns:
        if any(k in c.lower() for k in ["depth","mbsf","mcsf","top_depth"]):
            return c
    return df.columns[0]

def depth_tolerance_merge(dfa, dfb, tol_cm=2):
    tol_m = tol_cm / 100.0
    a = dfa.copy().sort_values("depth_key").reset_index(drop=True)
    b = dfb.copy().sort_values("depth_key").reset_index(drop=True)
    return pd.merge_asof(a, b, on="depth_key", tolerance=tol_m,
                         direction="nearest", suffixes=("_A","_B"))

def get_expeditions_from_df(df):
    for c in df.columns:
        if "expedition" in c.lower() or c.lower() in ("exp","exp."):
            return sorted(df[c].dropna().astype(str).unique().tolist())
    return []

# =============================================================================
# CHART BUILDER (SHIPBOARD TAB)
# Y-axis inverted by default for depth plots
# Each curve/layer has its own legendgroup so toggles work independently
# =============================================================================
def make_chart(df, ctype, x, y, color, curves,
               litho_df=None, show_gaps=True, show_qc=True, show_core_tops=True,
               invert_y=True, theme="dark", gap_threshold_m=5.0):
    t = THEMES.get(theme, THEMES["dark"])
    cfg = plot_cfg(theme)
    if ctype == "scatter" and x and y:
        cc = None if color in (None,"None","") else color
        fig = (px.scatter(df, x=x, y=y, color=cc, opacity=0.75)
               .update_traces(marker=dict(size=5))
               .update_layout(**cfg))
        # Invert y-axis when depth is on Y
        if invert_y:
            fig.update_yaxes(autorange="reversed")
        return fig
    if ctype == "line" and x and y:
        fig = px.line(df, x=x, y=y).update_layout(**cfg)
        if invert_y:
            fig.update_yaxes(autorange="reversed")
        return fig
    if ctype == "histogram" and x:
        return (px.histogram(df, x=x, nbins=40,
                             color_discrete_sequence=[t["accent"]])
                .update_layout(**cfg))
    if ctype == "heatmap":
        nc = df.select_dtypes(include="number").columns.tolist()
        if len(nc) < 2:
            return empty_fig("Need 2+ numeric columns for heatmap")
        corr = df[nc].corr().round(2)
        return (px.imshow(corr, text_auto=True, aspect="auto",
                          color_continuous_scale="RdBu_r", zmin=-1, zmax=1)
                .update_layout(**cfg, height=420))

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
        pal       = [t["accent"],t["accent2"],t["accent3"],"#bc8cff","#ff7b72"]
        gaps      = find_recovery_gaps(df, x, gap_threshold_m) if show_gaps else []
        core_tops = extract_core_tops(df)       if show_core_tops else {}
        qc_col    = find_qc_col(df)             if show_qc        else None
        qc_depths = []
        if qc_col and qc_col in df.columns:
            qc_mask   = df[qc_col].fillna("").astype(str).str.strip() != ""
            qc_depths = df.loc[qc_mask, x].dropna().tolist()
        for i, col in enumerate(sel):
            mask = df[col].notna() & df[x].notna()
            # Each curve gets its own legendgroup so its toggle only affects itself
            # Using legendgroup + legendgrouptitle ensures clicking the legend
            # entry hides/shows only that trace (and its QC/gap companions).
            fig.add_trace(go.Scatter(
                x=df.loc[mask,col], y=df.loc[mask,x], mode="lines", name=col,
                line=dict(color=pal[i%len(pal)], width=1.5),
                legendgroup=col, showlegend=True),
                row=1, col=col_offset+i)
            for gap_top, gap_bot in gaps:
                fig.add_hrect(y0=gap_top, y1=gap_bot, fillcolor="#888780",
                              opacity=0.18, line_width=0, row=1, col=col_offset+i,
                              annotation_text="gap" if i==0 else "",
                              annotation_font=dict(size=8, color=t["muted"]),
                              annotation_position="top left")
            if qc_depths:
                qc_df = df.loc[df[x].isin(qc_depths) & df[col].notna()]
                if len(qc_df):
                    fig.add_trace(go.Scatter(
                        x=qc_df[col], y=qc_df[x], mode="markers",
                        name="QC flagged", showlegend=(i==0),
                        legendgroup="qc_flags",
                        marker=dict(symbol="circle-open", size=8,
                                    color=t["danger"], line_width=1.5),
                        hovertemplate="%{y:.2f} mbsf - QC flagged<extra></extra>"),
                        row=1, col=col_offset+i)
            if show_core_tops and i==0 and core_tops:
                x_min   = df[col].min()
                x_range = (df[col].max()-x_min) or 1
                tick_end = x_min + x_range*0.12
                for core_label, core_depth in core_tops.items():
                    fig.add_shape(type="line", x0=x_min, x1=tick_end,
                                  y0=core_depth, y1=core_depth,
                                  line=dict(color=t["warn"], width=0.8, dash="dot"),
                                  row=1, col=col_offset+i)
                    fig.add_annotation(x=tick_end, y=core_depth,
                                       text=core_label.split("-")[-1],
                                       showarrow=False,
                                       font=dict(size=7, color=t["warn"]),
                                       xanchor="left", yanchor="middle",
                                       row=1, col=col_offset+i)
        fig.update_yaxes(autorange="reversed", title_text=x, row=1, col=1)
        # Fixed, scroll-safe height. The depth log uses
        # a constrained height so the controls below it remain accessible.
        depthlog_cfg = {**cfg, "height": 560}
        depthlog_cfg.pop("xaxis",None); depthlog_cfg.pop("yaxis",None)
        return fig.update_layout(showlegend=True,
                                 legend=dict(x=1.01,y=1,font=dict(size=10)),
                                 **depthlog_cfg)
    return empty_fig("Select axes to plot")

# =============================================================================
# DASH APP INITIALIZATION
# =============================================================================
server = flask.Flask(__name__)
app    = Dash(__name__, server=server, suppress_callback_exceptions=True)

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
  html, body, #react-entry-point {
    height: auto !important;
    min-height: 100%;
    overflow-y: auto !important;
  }
  body { background:var(--bg) !important; color:var(--text) !important; }
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
  .dash-spreadsheet-container .dash-spreadsheet-inner th
    { background-color:var(--bg)!important; color:var(--accent)!important; }
  .dash-spreadsheet-container .dash-spreadsheet-inner td
    { background-color:var(--panel)!important; color:var(--text)!important; }
  .tab { background-color:var(--panel)!important; color:var(--muted)!important; }
  .tab--selected { background-color:var(--bg)!important; color:var(--text)!important; }
  * { transition: background-color 0.25s, color 0.25s, border-color 0.25s; }

  /* Data table scrolls independently of the page */
  .iodp-table-scroll {
    max-height: 320px;
    overflow-y: auto;
    overflow-x: auto;
  }

  /* Depth log layout — keeps the chart from pushing controls off screen */
  .depthlog-graph-container {
    overflow: visible;
  }
</style>
</head>
<body>
{%app_entry%}
<footer>{%config%}{%scripts%}{%renderer%}</footer>
</body>
</html>"""

TAB_STYLE = {"backgroundColor":"var(--panel)","color":"var(--muted)",
             "border":f"1px solid var(--border)","borderBottom":"none",
             "fontFamily":FONT,"fontSize":"13px","padding":"8px 20px"}
TAB_SEL   = {**TAB_STYLE,"backgroundColor":"var(--bg)","color":"var(--text)",
             "borderBottom":f"1px solid var(--bg)","fontWeight":"600"}

# =============================================================================
# SHIPBOARD SIDEBAR LAYOUT
# =============================================================================
shipboard_sidebar = html.Div([
    html.P("DATA SOURCE", style=LBL),
    # Upload box lists all accepted formats including .tsv
    dcc.Upload(id="upload", multiple=False,
        children=html.Div([
            html.Div("↑", style={"fontSize":"26px","color":"var(--accent)"}),
            html.Div("Drop file or click to upload"),
            html.Div("Accepted: .csv  .tsv  .xlsx  .las",
                     style={"color":"var(--muted)","fontSize":"10px","marginTop":"3px"}),
        ], style={"textAlign":"center","color":"var(--text)","fontSize":"12px"}),
        style={"border":f"2px dashed var(--border)","borderRadius":"8px",
               "padding":"16px","cursor":"pointer","marginBottom":"10px"}),

    # Litho upload layers on top of the main data — it doesn't replace it
    html.P("LITHO TRACK (optional, layers on chart)", style=LBL),
    html.Div("Upload a separate CSV/XLSX with top depth, bottom depth, and lithology columns. "
             "This adds a color-coded lithology lane to depth log view — it does not replace your main data file.",
             style={"color":"var(--muted)","fontSize":"9px","marginBottom":"4px","fontFamily":FONT,
                    "lineHeight":"1.4"}),
    html.Div("Accepted: .csv  .tsv  .xlsx",
             style={"color":"var(--muted)","fontSize":"9px","marginBottom":"6px","fontFamily":FONT}),
    dcc.Upload(id="upload-litho", multiple=False,
        children=html.Div([
            html.Div("↑", style={"fontSize":"18px","color":"var(--accent3)"}),
            html.Div("Drop litho file or click"),
        ], style={"textAlign":"center","color":"var(--text)","fontSize":"11px"}),
        style={"border":f"2px dashed var(--border)","borderRadius":"8px",
               "padding":"10px","cursor":"pointer","marginBottom":"4px"}),
    html.Div(id="litho-badge"),

    html.Hr(style={"borderColor":"var(--border)","margin":"14px 0"}),
    html.P("SITE METADATA (optional)", style=LBL),
    html.Div("Auto-detected from LIMS CSV where possible. Fill in any missing fields:",
             style={"color":"var(--muted)","fontSize":"9px","marginBottom":"6px"}),
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

    html.Hr(style={"borderColor":"var(--border)","margin":"14px 0"}),
    html.P("X AXIS (depth)", style=LBL),
    dcc.Dropdown(id="x-col", placeholder="Select column...", style=DD),
    html.P("Y AXIS", id="y-lbl", style=LBL),
    dcc.Dropdown(id="y-col", placeholder="Select column...", style=DD),
    html.P("COLOR BY", id="color-lbl", style=LBL),
    dcc.Dropdown(id="color-col", placeholder="None", style=DD),
    html.P("CURVES (depth log)", id="curves-lbl", style={**LBL,"display":"none"}),
    dcc.Checklist(id="depth-curves", options=[], value=[],
                  labelStyle={"display":"block","marginBottom":"5px",
                               "color":"var(--text)","fontSize":"12px"},
                  inputStyle={"marginRight":"6px","accentColor":"var(--accent)"},
                  style={"display":"none"}),

    html.Hr(style={"borderColor":"var(--border)","margin":"14px 0"}),
    html.P("DEPTH LOG OVERLAYS", style=LBL),
    dcc.Checklist(id="overlay-opts",
        options=[{"label":" Recovery gap hatching","value":"gaps"},
                 {"label":" QC flag markers","value":"qc"},
                 {"label":" Core-top tick marks","value":"core_tops"}],
        value=["gaps","qc","core_tops"],
        labelStyle={"display":"block","marginBottom":"6px",
                    "color":"var(--text)","fontSize":"11px","fontFamily":FONT},
        inputStyle={"marginRight":"6px","accentColor":"var(--accent)"}),
    html.Div("QC flag markers: a heuristic, not a validated QC method — it "
             "flags any row where a column named *_Comment or *_comment is "
             "non-empty, on the assumption that such fields were used to "
             "note sample issues. Confirm that assumption holds for your file.",
             style={"color":"var(--muted)","fontSize":"9px","marginTop":"2px",
                    "marginBottom":"6px","lineHeight":"1.4"}),
    html.Div("Gap threshold (m) — flags any two consecutive depth samples "
             "farther apart than this as a recovery gap. 5 m is a reasonable "
             "default for typical physical-property sampling spacing, not a "
             "fixed rule — tighten it for closely-sampled data.",
             style={"color":"var(--muted)","fontSize":"9px","marginTop":"6px",
                    "marginBottom":"4px","lineHeight":"1.4"}),
    dcc.Input(id="gap-threshold", type="number", value=5.0, min=0.1, step=0.1,
              style=INP),

    # Y-axis invert toggle
    html.Hr(style={"borderColor":"var(--border)","margin":"14px 0"}),
    html.P("AXIS OPTIONS", style=LBL),
    dcc.Checklist(id="axis-opts",
        options=[{"label":" Invert Y-axis (depth: 0 at top)","value":"invert_y"}],
        value=["invert_y"],
        labelStyle={"display":"block","marginBottom":"6px",
                    "color":"var(--text)","fontSize":"11px","fontFamily":FONT},
        inputStyle={"marginRight":"6px","accentColor":"var(--accent)"}),

    html.Hr(style={"borderColor":"var(--border)","margin":"14px 0"}),
    html.P("CHART TYPE", style={**LBL,"marginTop":"18px"}),
    dcc.RadioItems(id="chart-type", value="scatter",
        options=[{"label":" Scatter","value":"scatter"},
                 {"label":" Line","value":"line"},
                 {"label":" Histogram","value":"histogram"},
                 {"label":" Depth Log","value":"depthlog"},
                 {"label":" Correlation Heatmap","value":"heatmap"}],
        labelStyle={"display":"block","marginBottom":"8px",
                    "color":"var(--text)","fontSize":"12px","fontFamily":FONT},
        inputStyle={"marginRight":"7px","accentColor":"var(--accent)"}),
], style={"width":"240px","minWidth":"240px","background":"var(--panel)",
          "borderRight":f"1px solid var(--border)","padding":"18px"})


# ── Post-Expedition dataset fetch panel ──────────────────────────────────────
def dataset_panel(ds):
    accent = "var(--accent)" if ds == "a" else "var(--accent3)"
    label  = "DATASET A" if ds == "a" else "DATASET B"
    repo_hint = html.Div([
        html.Div("Enter a Leg/Expedition number — no need to know which archive it "
                 "lives in. Database mode checks LIMS/LORE first (JR expeditions, "
                 "317+), then falls back to PANGAEA (DSDP, ODP, and MSP expeditions).",
                 style={"color":"var(--muted)","fontSize":"9px","lineHeight":"1.4"}),
        html.Div([
            html.Span("Note: ", style={"color":"var(--accent)","fontWeight":"700"}),
            html.Span("Chikyu/J-CORES data (KCC/JAMSTEC) has no public API — use "
                      "Local file upload for those expeditions.", style={"color":"var(--muted)"}),
        ], style={"fontSize":"9px","marginTop":"4px","fontFamily":FONT}),
    ], style={"background":"var(--bg)","border":f"1px solid var(--border)",
              "borderRadius":"4px","padding":"6px 8px","marginBottom":"8px"})

    return html.Div([
        html.P(label, style={**LBL, "color": accent, "marginTop":"0"}),
        repo_hint,
        dcc.RadioItems(id=f"pe-{ds}-source",
            options=[
                {"label": " Database",           "value": "database"},
                {"label": " Local file upload",  "value": "upload"},
            ],
            value="database",
            labelStyle={"display":"block","color":"var(--muted)",
                        "fontSize":"11px","marginBottom":"3px"},
            inputStyle={"marginRight":"6px","accentColor":accent},
        ),
        html.Div(id=f"pe-{ds}-upload-panel", style={"display":"none"}, children=[
            dcc.Upload(id=f"pe-{ds}-upload", multiple=False,
                children=html.Div([
                    html.Div("Drop file or click to upload",
                             style={"color":"var(--muted)","fontSize":"11px","textAlign":"center"}),
                    html.Div("Accepted: .csv  .tsv  .xlsx  .las",
                             style={"color":"var(--muted)","fontSize":"9px","textAlign":"center","marginTop":"2px"}),
                ], style={"padding":"10px 0"}),
                style={"border":f"1px dashed var(--border)","borderRadius":"6px",
                       "backgroundColor":"var(--bg)","cursor":"pointer","marginTop":"6px"}),
            html.Div(id=f"pe-{ds}-upload-status",
                     style={"fontSize":"10px","color":"var(--accent2)","marginTop":"4px"}),
        ]),
        html.Div(id=f"pe-{ds}-database-panel", children=[
            dcc.Input(id=f"pe-{ds}-lims-exp",  placeholder="Leg/Expedition (e.g. 344 or 118)",
                      debounce=True, style={**INP,"marginTop":"6px"}),
            html.Div("Site and Hole below populate as soon as this Leg is found in "
                     "LIMS/LORE — pick from the list, don't type a guess.",
                     style={"color":"var(--muted)","fontSize":"9px","marginTop":"6px","lineHeight":"1.4"}),
            dcc.Dropdown(id=f"pe-{ds}-lims-site", placeholder="Site — optional (all sites if blank)",
                         style={**DD,"marginTop":"4px"}),
            dcc.Dropdown(id=f"pe-{ds}-lims-hole", placeholder="Hole — optional (all holes if blank)",
                         style={**DD,"marginTop":"4px"}),
            html.P("Report type (required to Fetch)", style={**LBL,"marginTop":"10px"}),
            dcc.Dropdown(id=f"pe-{ds}-report",
                options=[{"label":v,"value":k} for k,v in LORE_REPORTS.items()],
                placeholder="select report...", style=DD),
            html.Button(f"Fetch {ds.upper()}", id=f"pe-fetch-{ds}-database",
                        n_clicks=0, style=BTN(accent)),
            html.Div(id=f"pe-{ds}-db-status",
                     style={"fontSize":"10px","color":"var(--accent2)","marginTop":"4px"}),
            html.Div(id=f"pe-{ds}-db-results", style={"marginTop":"6px"}),
        ]),
    ], style={"borderBottom":f"1px solid var(--border)",
              "paddingBottom":"12px","marginBottom":"12px"})


post_sidebar = html.Div([
    html.P("POST-EXPEDITION", style={**LBL,"marginTop":"0","fontSize":"11px",
                                     "color":"var(--muted)","letterSpacing":"3px"}),
    html.Div("Multi-dataset merge with depth tolerance matching.",
             style={"color":"var(--muted)","fontSize":"9px","marginBottom":"12px"}),
    html.P("VIEW MODE", style={**LBL,"marginTop":"0"}),
    html.Div("Explore a single dataset on its own, or merge A + B by depth first. "
             "Set this before fetching if you only want one dataset — it's easy to "
             "miss below a long Database/PANGAEA panel otherwise.",
             style={"color":"var(--muted)","fontSize":"9px","marginBottom":"6px","lineHeight":"1.4"}),
    dcc.RadioItems(id="pe-view-mode", value="merged",
        options=[
            {"label": " Merged (A + B)",   "value": "merged"},
            {"label": " Dataset A only",   "value": "a"},
            {"label": " Dataset B only",   "value": "b"},
        ],
        labelStyle={"display":"block","marginBottom":"6px",
                    "color":"var(--text)","fontSize":"11px","fontFamily":FONT},
        inputStyle={"marginRight":"6px","accentColor":"var(--accent)"}),
    html.Hr(style={"borderColor":"var(--border)","margin":"10px 0"}),
    dataset_panel("a"),
    dataset_panel("b"),
    html.P("MERGE SETTINGS", style={**LBL,"marginTop":"0"}),
    html.Div("Depth tolerance (cm)", style={"color":"var(--muted)","fontSize":"10px","marginBottom":"4px"}),
    dcc.Input(id="pe-tolerance", value="2", type="number", min=0, max=500, style=INP),
    html.Div("Depth col — A", style={"color":"var(--muted)","fontSize":"10px","marginTop":"8px","marginBottom":"4px"}),
    dcc.Dropdown(id="pe-depth-a", options=[], placeholder="auto-detect", style=DD),
    html.Div("Depth col — B", style={"color":"var(--muted)","fontSize":"10px","marginTop":"8px","marginBottom":"4px"}),
    dcc.Dropdown(id="pe-depth-b", options=[], placeholder="auto-detect", style=DD),
    html.Button("Merge datasets", id="pe-merge-btn", n_clicks=0,
                style={**BTN("var(--accent2)"),"marginTop":"10px","fontSize":"12px"}),
    html.Hr(style={"borderColor":"var(--border)","margin":"10px 0"}),
    html.P("CHART MODE", style=LBL),
    dcc.RadioItems(id="pe-chart-mode", value="tracks",
        options=[
            {"label": " Depth tracks  (each property its own lane)", "value": "tracks"},
            {"label": " Correlation scatter  (A vs B, color = depth)", "value": "scatter"},
            {"label": " Dual-axis overlay  (two scales, one depth axis)", "value": "dual"},
            {"label": " Rolling mean  (smoothed downhole trends)", "value": "rolling"},
        ],
        labelStyle={"display":"block","marginBottom":"6px",
                    "color":"var(--text)","fontSize":"11px","fontFamily":FONT},
        inputStyle={"marginRight":"6px","accentColor":"var(--accent2)"},
    ),
    html.P("DEPTH COLUMN", style=LBL),
    dcc.Dropdown(id="pe-xaxis", options=[], value=None, style=DD),
    html.P("DATASET A  columns", id="pe-yaxis-lbl", style=LBL),
    dcc.Dropdown(id="pe-yaxis", options=[], value=None, multi=True, style=DD),
    html.Div(id="pe-ycols-b-container", children=[
        html.P("DATASET B  columns", style=LBL),
        dcc.Dropdown(id="pe-ycols-b", options=[], value=None, multi=True, style=DD),
    ]),
    html.Div(id="pe-rolling-ctrl", style={"display":"none"}, children=[
        html.P("Rolling window (rows)", style={**LBL,"marginTop":"8px"}),
        html.Div("Centered simple moving average over this many rows (not a "
                 "depth interval — row spacing varies with sample density). "
                 "Points within half a window of either end use a smaller, "
                 "asymmetric window rather than being dropped.",
                 style={"color":"var(--muted)","fontSize":"9px","marginBottom":"4px",
                        "lineHeight":"1.4"}),
        dcc.Input(id="pe-rolling-window", value="20", type="number",
                  min=2, max=500, style=INP),
    ]),
], style={"width":"260px","minWidth":"260px","background":"var(--panel)",
          "borderRight":f"1px solid var(--border)","padding":"18px"})

# =============================================================================
# APP LAYOUT
# =============================================================================
app.layout = html.Div([
    html.Div([
        html.Div([
            html.Div([
                html.Span("IODP",      style={"fontWeight":"700","color":"var(--accent)"}),
                html.Span(" Explorer", style={"fontWeight":"300","color":"var(--text)"}),
            ], style={"fontSize":"17px","fontFamily":FONT}),
            html.Div("International Ocean Discovery Program · Data Visualization Tool",
                     style={"color":"var(--muted)","fontSize":"11px","fontFamily":FONT}),
        ]),
        html.Button(id="theme-toggle", n_clicks=0,
            children="☀ Light mode",
            style={"backgroundColor":"transparent","border":f"1px solid var(--border)",
                   "borderRadius":"6px","color":"var(--muted)","cursor":"pointer",
                   "fontSize":"11px","fontFamily":FONT,"padding":"5px 12px",
                   "transition":"all 0.2s"}),
    ], style={"display":"flex","justifyContent":"space-between","alignItems":"center",
              "padding":"8px 20px","background":"var(--panel)",
              "borderBottom":f"1px solid var(--border)"}),
    html.Div(id="theme-root", style={"display":"none"}),

    dcc.Tabs(id="main-tabs", value="shipboard", style={"fontFamily":FONT},
        children=[
            dcc.Tab(label="Shipboard",       value="shipboard", style=TAB_STYLE, selected_style=TAB_SEL),
            dcc.Tab(label="Post-Expedition", value="postexp",   style=TAB_STYLE, selected_style=TAB_SEL),
        ]),

    html.Div(id="tab-content"),

    dcc.Store(id="theme-store",      storage_type="local", data="dark"),
    dcc.Store(id="store-df",        storage_type="session"),
    dcc.Store(id="store-meta",      storage_type="session"),
    dcc.Store(id="store-litho",     storage_type="session"),
    dcc.Store(id="store-site-info", storage_type="session"),
    dcc.Store(id="pe-store-a"),
    dcc.Store(id="pe-store-b"),
    dcc.Store(id="pe-a-lims-raw"),
    dcc.Store(id="pe-b-lims-raw"),
    dcc.Store(id="pe-merged-store"),
    dcc.Store(id="pe-active-store"),

], style={"minHeight":"100vh","display":"flex","flexDirection":"column",
          "background":"var(--bg)","color":"var(--text)","fontFamily":FONT})

# =============================================================================
# TAB ROUTING
# =============================================================================
@app.callback(Output("tab-content","children"), Input("main-tabs","value"))
def render_tab(tab):
    if tab == "shipboard":
        return html.Div([
            html.Div(id="meta-banner",
                     children=html.Div("Upload a file to see site metadata.",
                         style={"color":"var(--muted)","fontSize":"11px",
                                "padding":"10px 20px","fontFamily":FONT})),
            html.Div([
                shipboard_sidebar,
                html.Div([
                    html.Div(id="kpi-bar",
                             style={"display":"flex","gap":"10px","padding":"10px 18px",
                                    "borderBottom":f"1px solid var(--border)","flexWrap":"wrap"}),
                    # Graph wrapped in constrained div to prevent depth log overflow
                   html.Div(
                        dcc.Graph(id="main-chart",
                                  config={"displayModeBar":True,"scrollZoom":True}),
                        style={"padding":"10px 18px"},
                        id="chart-wrapper",
                    ),
                    html.Div([
                        html.Div([
                            html.Span("DATA TABLE", style={"color":"var(--muted)","fontSize":"10px","letterSpacing":"2px"}),
                            html.Span(id="row-count", style={"color":"var(--accent)","fontSize":"11px","marginLeft":"12px"}),
                            html.Span(" — values shown are measured per sample",
                                      style={"color":"var(--muted)","fontSize":"9px","marginLeft":"8px"}),
                        ], style={"marginBottom":"8px"}),
                        html.Div(id="table-container",
                                 className="iodp-table-scroll"),
                    ], style={**CARD,"margin":"0 18px 18px 18px"}),
                ], style={"flex":"1","minWidth":"320px"}),
            ], style={"display":"flex","flexWrap":"wrap","flex":"1"}),
        ], style={"display":"flex","flexDirection":"column","flex":"1"})

    else:
        return html.Div([
            post_sidebar,
            html.Div([
                html.Div([
                    html.Div(id="pe-status-a",
                        style={"flex":"1","background":"var(--panel)","border":f"1px solid var(--border)",
                               "borderRadius":"6px","padding":"10px 14px","fontSize":"12px",
                               "color":"var(--muted)","marginRight":"8px"}),
                    html.Div(id="pe-status-b",
                        style={"flex":"1","background":"var(--panel)","border":f"1px solid var(--border)",
                               "borderRadius":"6px","padding":"10px 14px","fontSize":"12px",
                               "color":"var(--muted)","marginRight":"8px"}),
                    html.Div(id="pe-status-merged",
                        style={"flex":"1","background":"var(--panel)","border":f"1px solid var(--border)",
                               "borderRadius":"6px","padding":"10px 14px","fontSize":"12px",
                               "color":"var(--muted)"}),
                ], style={"display":"flex","marginBottom":"12px"}),
                html.Div([
                    html.Div([
                        html.Span("FILTER BY EXPEDITION",
                                  style={"color":"var(--text)","fontSize":"11px","letterSpacing":"1px",
                                         "fontWeight":"600"}),
                        html.Span(" — check/uncheck to show only those expeditions in the chart and table",
                                  style={"color":"var(--muted)","fontSize":"10px","marginLeft":"6px"}),
                        html.Button("All / None", id="pe-exp-all-none", n_clicks=0,
                            style={"backgroundColor":"var(--border)","color":"var(--text)","border":"none",
                                   "borderRadius":"4px","padding":"3px 10px","cursor":"pointer",
                                   "fontSize":"10px","marginLeft":"12px"}),
                    ], style={"marginBottom":"8px","display":"flex","alignItems":"center","flexWrap":"wrap"}),
                    html.Div(id="pe-exp-filter-hint",
                             children="No expedition column detected in data yet.",
                             style={"color":"var(--muted)","fontSize":"10px","fontStyle":"italic",
                                    "display":"none"}),
                    dcc.Checklist(id="pe-exp-filter", options=[], value=[],
                        labelStyle={"display":"inline-block","margin":"3px 8px 3px 0",
                                    "color":"var(--muted)","fontSize":"11px"}),
                ], style={**CARD,"marginBottom":"12px"}),

                html.Div([
                    html.Span("Expeditions in view: ",
                              style={"color":"var(--muted)","fontSize":"11px","marginRight":"6px"}),
                    html.Span(id="pe-merged-expeditions",
                              style={"color":"var(--accent2)","fontSize":"11px","fontFamily":FONT}),
                    html.Button("Download CSV", id="pe-download-btn", n_clicks=0,
                        style={"backgroundColor":"var(--panel)","color":"var(--accent)",
                               "border":f"1px solid var(--border)","borderRadius":"4px",
                               "padding":"4px 12px","cursor":"pointer",
                               "fontSize":"11px","marginLeft":"16px"}),
                    dcc.Download(id="pe-download"),
                ], style={"marginBottom":"12px"}),

                dcc.Graph(id="pe-chart", config={"displayModeBar":True,"scrollZoom":True}),
                # Post-expedition table also scrolls independently
                html.Div(id="pe-table-container",
                         className="iodp-table-scroll",
                         style={"marginTop":"16px","maxHeight":"320px","overflowY":"auto"}),
            ], style={"flex":"1","padding":"16px","minWidth":"320px"}),
        ], style={"display":"flex","flexWrap":"wrap","flex":"1"})


# =============================================================================
# CALLBACKS
# =============================================================================
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
    if not contents: return None, {}, {}
    df, meta = parse_upload(contents, filename)
    if "error" in meta: return None, meta, {}
    return df2j(df), meta, infer_site_meta(df, meta)

@app.callback(
    Output("store-litho","data"), Output("litho-badge","children"),
    Input("upload-litho","contents"), State("upload-litho","filename"),
)
def load_litho(contents, filename):
    if not contents:
        return None, html.Div("No litho file loaded.", style={"color":"var(--muted)","fontSize":"10px"})
    df_litho, meta = parse_upload(contents, filename)
    if "error" in meta or df_litho is None:
        return None, html.Div(f"Error: {meta.get('error','Unknown')}",
                               style={"color":"var(--danger)","fontSize":"10px"})
    df_litho, error = resolve_litho_columns(df_litho)
    if error:
        return None, html.Div([
            html.Div("Could not identify lithology columns.",
                     style={"color":"var(--danger)","fontSize":"11px","fontWeight":"700","marginBottom":"6px"}),
            html.Pre(error, style={"color":"var(--muted)","fontSize":"9px","fontFamily":FONT,
                                   "whiteSpace":"pre-wrap","maxHeight":"160px","overflowY":"auto",
                                   "background":"var(--bg)","padding":"8px","borderRadius":"4px",
                                   "border":f"1px solid var(--border)"}),
        ])
    n_units     = len(df_litho)
    depth_range = f"{df_litho['top_mbsf'].min():.1f} - {df_litho['bottom_mbsf'].max():.1f} mbsf"
    return df2j(df_litho), html.Div([
        html.Span(f"✓ {filename}",
                  style={"background":"var(--border)","padding":"3px 8px","borderRadius":"10px",
                         "fontSize":"10px","color":"var(--accent2)","fontFamily":FONT}),
        html.Span(f"{n_units} units · {depth_range}",
                  style={"color":"var(--muted)","fontSize":"10px","fontFamily":FONT,"marginLeft":"6px"}),
        html.Div("Litho track will appear in Depth Log view.",
                 style={"color":"var(--accent3)","fontSize":"9px","marginTop":"3px","fontFamily":FONT}),
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
                        style={"color":"var(--muted)","fontSize":"11px","padding":"10px 20px","fontFamily":FONT})
    manual = {"expedition":expedition or "","site_hole":site_hole or "",
              "lat":lat or "","lon":lon or "","water_depth":water_depth or "","recovery":recovery or ""}
    return build_metadata_bar(site_info, manual)

@app.callback(
    Output("x-col","options"), Output("y-col","options"),
    Output("color-col","options"), Output("depth-curves","options"),
    Input("store-meta","data"),
)
def set_options(meta):
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
    if not jdf:
        return [html.Span("Upload a file to begin.",style={"color":"var(--muted)","fontSize":"12px"})]
    df = j2df(jdf); cards = []
    for col in meta.get("numeric_cols",[])[:6]:
        v = df[col].dropna()
        if not len(v): continue
        cards.append(html.Div([
            html.Div(col, style={"color":"var(--muted)","fontSize":"9px","letterSpacing":"1px"}),
            html.Div(f"{v.mean():.3g}", style={"color":"var(--text)","fontSize":"17px","fontWeight":"700"}),
            html.Div(f"min {v.min():.3g}  max {v.max():.3g}",style={"color":"var(--muted)","fontSize":"9px"}),
        ], style={**CARD,"minWidth":"110px","padding":"8px 12px"}))
    return cards

@app.callback(
    Output("main-chart","figure"),
    Input("store-df","data"), Input("store-litho","data"),
    Input("chart-type","value"), Input("x-col","value"),
    Input("y-col","value"), Input("color-col","value"),
    Input("depth-curves","value"), Input("overlay-opts","value"),
    Input("axis-opts","value"), Input("theme-store","data"),
    Input("gap-threshold","value"),
)
def update_chart(jdf, jlitho, ctype, x, y, color, curves, overlays, axis_opts, theme, gap_threshold):
    """Rebuild chart on any control change; passes invert_y from axis-opts."""
    if not jdf: return empty_fig(theme=theme)
    overlays  = overlays  or []
    axis_opts = axis_opts or []
    invert_y  = "invert_y" in axis_opts
    try:
        return make_chart(j2df(jdf), ctype, x, y, color, curves or [],
                          litho_df=j2df(jlitho) if jlitho else None,
                          show_gaps=("gaps" in overlays),
                          show_qc=("qc" in overlays),
                          show_core_tops=("core_tops" in overlays),
                          invert_y=invert_y, theme=theme,
                          gap_threshold_m=float(gap_threshold) if gap_threshold else 5.0)
    except Exception as e:
        t = THEMES.get(theme, THEMES["dark"])
        return empty_fig("Error: "+str(e), t["danger"], theme=theme)

@app.callback(
    Output("table-container","children"), Output("row-count","children"),
    Input("store-df","data"),
)
def update_table(jdf):
    """Table rendered inside the iodp-table-scroll div for independent scrolling."""
    if not jdf:
        return html.Div("No data loaded.",style={"color":"var(--muted)"}), ""
    df = j2df(jdf); preview = df.head(200)
    tbl = dash_table.DataTable(
        data=preview.to_dict("records"),
        columns=[{"name":c,"id":c} for c in preview.columns],
        page_size=10, sort_action="native", filter_action="native",
        style_table={"overflowX":"auto","minWidth":"100%"},
        style_header={"backgroundColor":"var(--bg)","color":"var(--accent)",
                      "fontWeight":"700","fontSize":"10px","border":f"1px solid var(--border)"},
        style_cell={"backgroundColor":"var(--panel)","color":"var(--text)","fontSize":"11px",
                    "padding":"7px 11px","border":f"1px solid var(--border)",
                    "fontFamily":FONT,"maxWidth":"160px","overflow":"hidden","textOverflow":"ellipsis"},
        style_data_conditional=[
            {"if":{"row_index":"odd"},"backgroundColor":"var(--bg)"},
            *[{"if":{"filter_query":f'{{{col}}} != ""',"column_id":col},"color":"var(--warn)"}
              for col in preview.columns if re.search(r"comment",col,re.IGNORECASE)],
        ],
        fixed_rows={"headers": True},
    )
    return tbl, f"showing first 200 of {len(df):,} rows"

# =============================================================================
# POST-EXPEDITION TAB CALLBACKS
# =============================================================================
for _ds in ["a","b"]:
    @app.callback(
        Output(f"pe-{_ds}-upload-panel","style"),
        Output(f"pe-{_ds}-database-panel","style"),
        Input(f"pe-{_ds}-source","value"),
    )
    def pe_toggle(src, ds=_ds):
        up = {}  if src=="upload"   else {"display":"none"}
        db = {}  if src=="database" else {"display":"none"}
        return up, db

@app.callback(
    Output("pe-store-a","data"), Output("pe-a-upload-status","children"),
    Input("pe-a-upload","contents"), State("pe-a-upload","filename"),
    prevent_initial_call=True,
)
def pe_load_a_upload(contents, filename):
    df, meta = parse_upload(contents, filename)
    if "error" in meta: return None, f"Error: {meta['error']}"
    return df2j(df), f"✓ {filename}  ({len(df):,} rows)"

@app.callback(
    Output("pe-store-b","data"), Output("pe-b-upload-status","children"),
    Input("pe-b-upload","contents"), State("pe-b-upload","filename"),
    prevent_initial_call=True,
)
def pe_load_b_upload(contents, filename):
    df, meta = parse_upload(contents, filename)
    if "error" in meta: return None, f"Error: {meta['error']}"
    return df2j(df), f"✓ {filename}  ({len(df):,} rows)"

def _pangaea_pick_list(ds, results):
    """Clickable list of PANGAEA matches — each button fetches that dataset."""
    return html.Div([
        html.Div("Pick a match to load it:",
                 style={"color":"var(--muted)","fontSize":"9px","marginBottom":"4px"}),
        *[html.Button(r["label"],
            id={"type":"pe-db-pick", "ds":ds, "pid":r["value"]},
            n_clicks=0,
            style={"display":"block","width":"100%","textAlign":"left",
                   "background":"none","border":"none",
                   "borderBottom":f"1px solid var(--border)",
                   "color":"var(--accent3)","cursor":"pointer",
                   "fontSize":"10px","padding":"4px 0","fontFamily":FONT})
          for r in results[:8]]
    ])

for _ds in ["a","b"]:
    @app.callback(
        Output(f"pe-{_ds}-lims-raw","data"),
        Output(f"pe-{_ds}-lims-site","options"), Output(f"pe-{_ds}-lims-site","value"),
        Output(f"pe-{_ds}-lims-hole","options",allow_duplicate=True),
        Output(f"pe-{_ds}-lims-hole","value",allow_duplicate=True),
        Output(f"pe-{_ds}-db-status","children"),
        Input(f"pe-{_ds}-lims-exp","value"), Input(f"pe-{_ds}-report","value"),
        prevent_initial_call=True,
    )
    def pe_lookup(exp, report, ds=_ds):
        """Pulls a Leg's report once (no site/hole filter) so Site and Hole
        can be offered as dropdowns built only from what's really there —
        instead of free-typed values that may not exist for this Leg. Runs
        as soon as a Leg is entered, using PREVIEW_REPORT as a stand-in
        until an actual report is chosen; re-runs against the real report
        once one is picked, in case its site/hole coverage differs."""
        if not exp:
            return None, [], None, [], None, ""
        exp = str(exp).strip()
        probe = report or PREVIEW_REPORT
        df, err = fetch_lore(probe, exp, "", "")
        if err:
            return None, [], None, [], None, f"LIMS/LORE: {err[:100]}"
        site_col = _find_col(df, ["site"])
        sites = sorted(df[site_col].dropna().astype(str).unique().tolist()) if site_col else []
        hole_col = _find_col(df, ["hole"])
        holes = sorted(df[hole_col].dropna().astype(str).unique().tolist()) if hole_col else []
        preview_note = "" if report else f"  — preview via {LORE_REPORTS[PREVIEW_REPORT]}, pick a report to fetch"
        status = f"Found {len(df):,} rows in LIMS/LORE for Leg {exp}  ({len(sites)} site(s)){preview_note}"
        return (df2j(df),
                [{"label":s,"value":s} for s in sites], None,
                [{"label":h,"value":h} for h in holes], None,
                status)

    @app.callback(
        Output(f"pe-{_ds}-lims-hole","options",allow_duplicate=True),
        Output(f"pe-{_ds}-lims-hole","value",allow_duplicate=True),
        Input(f"pe-{_ds}-lims-site","value"),
        State(f"pe-{_ds}-lims-raw","data"),
        prevent_initial_call=True,
    )
    def pe_hole_opts(site, raw, ds=_ds):
        """Narrows the Hole dropdown to holes that actually exist for the
        chosen Site, so an impossible Site+Hole combo can't be selected."""
        if not raw:
            return [], None
        df = j2df(raw)
        hole_col = _find_col(df, ["hole"])
        if not hole_col:
            return [], None
        if site:
            site_col = _find_col(df, ["site"])
            if site_col:
                df = df[df[site_col].astype(str) == str(site)]
        holes = sorted(df[hole_col].dropna().astype(str).unique().tolist())
        return [{"label":h,"value":h} for h in holes], None

    @app.callback(
        Output(f"pe-store-{_ds}","data",allow_duplicate=True),
        Output(f"pe-{_ds}-db-status","children",allow_duplicate=True),
        Output(f"pe-{_ds}-db-results","children"),
        Input(f"pe-fetch-{_ds}-database","n_clicks"),
        State(f"pe-{_ds}-report","value"), State(f"pe-{_ds}-lims-exp","value"),
        State(f"pe-{_ds}-lims-site","value"), State(f"pe-{_ds}-lims-hole","value"),
        State(f"pe-{_ds}-lims-raw","data"),
        prevent_initial_call=True,
    )
    def pe_database_fetch(n, report, exp, site, hole, raw, ds=_ds):
        if not n or not exp:
            return None, "Enter a Leg/Expedition number", ""
        exp = str(exp).strip()

        if not report:
            return None, "Pick a report type, then Fetch — Site/Hole above are just a preview", ""

        # 1) Leg + report already looked up in LIMS/LORE — filter the cached
        #    raw fetch by whatever Site/Hole was picked (both real values,
        #    since the dropdowns only ever offer what's actually in the data).
        if raw:
            df = j2df(raw)
            df = _restrict_to_request(df, exp, site, hole)
            if not df.empty:
                picked = ", ".join(f"{k}={v}" for k, v in [("site",site),("hole",hole)] if v)
                status = (f"✓ LIMS/LORE  {LORE_REPORTS.get(report,report)}  Leg {exp}"
                          f"{'  ('+picked+')' if picked else ''}  ({len(df):,} rows)")
                return df2j(df), status, ""
            return None, f"No rows matched site={site or 'any'}, hole={hole or 'any'} for Leg {exp}", ""

        # 2) No LIMS/LORE data for this leg — fall back to PANGAEA (DSDP, then ODP)
        for project in ("DSDP", "ODP"):
            results, err = search_pangaea_legacy(exp, project)
            if err:
                continue
            if not results:
                continue
            if len(results) == 1:
                pid = results[0]["value"]
                df, ferr = fetch_pangaea_doi(pid)
                if not ferr and df is not None and not df.empty:
                    status = f"✓ PANGAEA {project}  {pid}  Leg {exp}  ({len(df):,} rows)"
                    return df2j(df), status, ""
            status = (f"Not found in LIMS/LORE — {len(results)} PANGAEA {project} "
                      f"match(es) for Leg {exp}:")
            return None, status, _pangaea_pick_list(ds, results)

        # 3) PANGAEA has nothing either — try shinylaurel.com's DSDP archive,
        #    but only for report types with a known matching category there
        dsdp_type = SHINYLAUREL_DSDP_TYPES.get(report)
        if dsdp_type:
            df, err = fetch_dsdp_shinylaurel(dsdp_type, exp, site, hole)
            if not err and df is not None and not df.empty:
                status = f"✓ DSDP (shinylaurel.com)  {dsdp_type}  Leg {exp}  ({len(df):,} rows)"
                return df2j(df), status, ""

        return (None,
                f"No data found in LIMS/LORE, PANGAEA, or DSDP archive for Leg {exp}. Try Local file upload.",
                "")

    @app.callback(
        Output(f"pe-store-{_ds}","data",allow_duplicate=True),
        Output(f"pe-{_ds}-db-status","children",allow_duplicate=True),
        Input({"type":"pe-db-pick","ds":_ds,"pid":ALL},"n_clicks"),
        prevent_initial_call=True,
    )
    def pe_database_pick(n_clicks_list, ds=_ds):
        trig = ctx.triggered_id
        if not trig or not any(n_clicks_list):
            return None, ""
        pid = trig["pid"]
        df, err = fetch_pangaea_doi(pid)
        if err:
            return None, f"PANGAEA error: {err[:80]}"
        return df2j(df), f"✓ PANGAEA {pid}  ({len(df):,} rows)"

@app.callback(
    Output("pe-depth-a","options"), Output("pe-depth-a","value"),
    Output("pe-depth-b","options"), Output("pe-depth-b","value"),
    Input("pe-store-a","data"), Input("pe-store-b","data"),
)
def pe_depth_opts(da, db):
    def ov(d):
        if not d: return [],None
        df = j2df(d)
        return [{"label":c,"value":c} for c in df.columns], find_depth_col(df)
    oa,va = ov(da); ob,vb = ov(db)
    return oa,va,ob,vb

@app.callback(
    Output("pe-status-a","children"), Output("pe-status-b","children"),
    Input("pe-store-a","data"), Input("pe-store-b","data"),
)
def pe_status_cards(da, db):
    def card(d, label, color):
        if not d: return [html.Span(f"{label}: ",style={"color":color}), "no data loaded"]
        df = j2df(d)
        return [html.Span(f"{label}  ",style={"color":color,"fontWeight":"600"}),
                html.Span(f"{len(df):,} rows x {len(df.columns)} cols")]
    return card(da,"Dataset A","var(--accent)"), card(db,"Dataset B","var(--accent3)")

@app.callback(
    Output("pe-exp-filter","options"), Output("pe-exp-filter","value"),
    Input("pe-store-a","data"), Input("pe-store-b","data"), Input("pe-merged-store","data"),
)
def pe_exp_opts(da, db, dm):
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
    all_vals = [o["value"] for o in opts]
    return [] if set(current)==set(all_vals) else all_vals

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
        status = [html.Span("Merged  ",style={"color":"var(--accent2)","fontWeight":"600"}),
                  html.Span(f"{len(merged):,} rows, {n_match:,} depth matches (tol={tol_cm} cm)")]
        return df2j(merged), status
    except Exception as e:
        return None, f"Merge error: {str(e)[:80]}"

@app.callback(
    Output("pe-view-mode","value"),
    Input("pe-store-a","data"), Input("pe-store-b","data"),
    prevent_initial_call=True,
)
def pe_view_mode_auto(da, db):
    """Switches View Mode to whichever single dataset is actually loaded, so
    loading just Dataset A shows it immediately instead of sitting on
    'Merged' (which stays empty until both A and B exist and Merge is
    clicked). Once both datasets have data, this stops touching the
    setting — that's the point where 'Merged' vs 'A only' vs 'B only'
    becomes a real, user-made choice rather than an obvious default."""
    if da and not db:
        return "a"
    if db and not da:
        return "b"
    return no_update

@app.callback(
    Output("pe-active-store","data"),
    Input("pe-view-mode","value"),
    Input("pe-merged-store","data"), Input("pe-store-a","data"), Input("pe-store-b","data"),
)
def pe_active_data(mode, dm, da, db):
    """Selects the dataframe to drive the chart/table/download — either
    dataset on its own, or the merged result, per the View Mode setting."""
    if mode == "a": return da
    if mode == "b": return db
    return dm

@app.callback(
    Output("pe-ycols-b-container","style"), Output("pe-yaxis-lbl","children"),
    Input("pe-view-mode","value"),
)
def pe_view_mode_ui(mode):
    if mode == "merged":
        return {}, "DATASET A  columns"
    label = "DATASET A  columns" if mode == "a" else "DATASET B  columns"
    return {"display":"none"}, label

@app.callback(
    Output("pe-xaxis","options"), Output("pe-yaxis","options"),
    Output("pe-ycols-b","options"),
    Input("pe-active-store","data"),
)
def pe_axis_opts(da):
    """Only offers numeric columns as chart axes. Plotting a depth track,
    scatter, dual-axis, or rolling-mean chart from a text/categorical field
    (sample comments, measurement units, lithology codes, etc.) produces a
    meaningless chart — Plotly just assigns each unique string an arbitrary
    integer position, which can look like a real depth trend but isn't one."""
    if not da: return [],[],[]
    df = j2df(da)
    numeric_cols = df.select_dtypes(include="number").columns.tolist()
    opts = [{"label":c,"value":c} for c in numeric_cols]
    return opts, opts, opts

@app.callback(
    Output("pe-xaxis","value"), Output("pe-yaxis","value"),
    Output("pe-ycols-b","value"),
    Input("pe-xaxis","options"), State("pe-view-mode","value"),
    prevent_initial_call=True,
)
def pe_axis_defaults(opts, mode):
    if not opts: return None, None, None
    cols = [o["value"] for o in opts]
    depth = next((c for c in cols if "depth" in c.lower()), cols[0])
    others = [c for c in cols if c not in (depth,"depth_key")]
    if mode == "merged":
        a_cols = [c for c in cols if c.endswith("_A") and c != depth]
        b_cols = [c for c in cols if c.endswith("_B") and c != depth]
        y_a = a_cols[:3] if a_cols else others[:2]
        y_b = b_cols[:3] if b_cols else others[2:4]
    else:
        # single-dataset view — Dataset B column selector is hidden, keep it empty
        y_a = others[:3]
        y_b = []
    return depth, y_a, y_b

@app.callback(
    Output("pe-merged-expeditions","children"),
    Input("pe-active-store","data"), Input("pe-exp-filter","value"),
)
def pe_merged_exp_readout(da, selected):
    if not da: return "n/a"
    df = j2df(da)
    exps = get_expeditions_from_df(df)
    filtered = [e for e in exps if e in (selected or [])]
    return ", ".join(filtered) if filtered else "n/a"

@app.callback(
    Output("pe-rolling-ctrl","style"),
    Input("pe-chart-mode","value"),
)
def pe_rolling_toggle(mode):
    return {} if mode == "rolling" else {"display":"none"}

@app.callback(
    Output("pe-chart","figure"),
    Input("pe-active-store","data"), Input("pe-exp-filter","value"),
    Input("pe-xaxis","value"), Input("pe-yaxis","value"),
    Input("pe-ycols-b","value"), Input("pe-chart-mode","value"),
    Input("pe-rolling-window","value"), Input("theme-store","data"),
)
def pe_chart(da, selected, xcol, ycols_a, ycols_b, mode, rwin, theme="dark"):
    t = THEMES.get(theme, THEMES["dark"])
    if not da or not xcol: return empty_fig("Load a dataset (or merge A + B) to visualize", theme=theme)
    df = j2df(da)
    exp_col = next((c for c in df.columns if "expedition" in c.lower()), None)
    if exp_col and selected:
        df = df[df[exp_col].astype(str).isin(selected)]
    df = df.dropna(subset=[xcol]).sort_values(xcol).reset_index(drop=True)

    ycols_a = [ycols_a] if isinstance(ycols_a, str) else (ycols_a or [])
    ycols_b = [ycols_b] if isinstance(ycols_b, str) else (ycols_b or [])
    ycols_a = [c for c in ycols_a if c in df.columns]
    ycols_b = [c for c in ycols_b if c in df.columns]
    all_cols = ycols_a + ycols_b
    if not all_cols: return empty_fig("Select columns for Dataset A and/or B", theme=theme)

    colors_a = [t["accent"], "#bc8cff", "#ff7b72"]
    colors_b = [t["accent3"], t["accent2"], "#f0883e"]
    cfg_base = {**plot_cfg(theme), "height": 600}
    cfg_base.pop("xaxis", None); cfg_base.pop("yaxis", None)
    axis_kw  = dict(gridcolor=t["border"], linecolor=t["border"])

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
        fig.update_yaxes(title_text=xcol + " (mbsf)", autorange="reversed", **axis_kw)
        fig.update_layout(showlegend=False, **cfg_base)
        return fig

    if mode == "scatter":
        if not ycols_a or not ycols_b:
            return empty_fig("Select at least one column from each dataset", theme=theme)
        xa = ycols_a[0]; xb = ycols_b[0]
        sub = df[[xcol, xa, xb]].dropna()
        fig = go.Figure(go.Scatter(
            x=sub[xa], y=sub[xb], mode="markers",
            marker=dict(color=sub[xcol], colorscale="Viridis_r", size=5, opacity=0.75,
                        colorbar=dict(title=dict(text=xcol + " mbsf",
                                                 font=dict(color=t["muted"])),
                                      tickfont=dict(color=t["muted"])),
                        showscale=True),
            hovertemplate=f"{xa}: %{{x:.3g}}<br>{xb}: %{{y:.3g}}<br>depth: %{{marker.color:.1f}} mbsf<extra></extra>",
        ))
        try:
            m, b = np.polyfit(sub[xa].values, sub[xb].values, 1)
            x_r = np.linspace(sub[xa].min(), sub[xa].max(), 200)
            n = len(sub)
            if SCIPY_AVAILABLE:
                # Pearson r together with its own p-value and n, rather than
                # a bare r — a correlation coefficient alone doesn't say
                # whether it's likely real or an artifact of a small/
                # scattered sample.
                r, p = scipy_stats.pearsonr(sub[xa].values, sub[xb].values)
                sig = "significant" if p < 0.05 else "not significant"
                label = f"r={r:.3f}  r²={r**2:.3f}  n={n}  p={p:.2g} ({sig} at α=0.05)"
            else:
                r = np.corrcoef(sub[xa].values, sub[xb].values)[0, 1]
                label = f"r={r:.3f}  r²={r**2:.3f}  n={n}  (p-value needs scipy — not installed)"
            fig.add_trace(go.Scatter(x=x_r, y=m*x_r+b, mode="lines",
                                     name=label,
                                     line=dict(color=t["danger"], width=1.5, dash="dash")))
        except Exception:
            pass
        fig.update_layout(**{**plot_cfg(theme), "height":600,
                             "xaxis": dict(title=xa,**axis_kw),
                             "yaxis": dict(title=xb,**axis_kw), "showlegend":True})
        return fig

    if mode == "dual":
        if not ycols_a or not ycols_b:
            return empty_fig("Select at least one column from each dataset", theme=theme)
        ya = ycols_a[0]; yb = ycols_b[0]
        fig = go.Figure()
        sub_a = df[[xcol,ya]].dropna(); sub_b = df[[xcol,yb]].dropna()
        fig.add_trace(go.Scatter(x=sub_a[xcol], y=sub_a[ya], mode="lines", name=ya,
                                 line=dict(color=t["accent"],width=1.5), yaxis="y1"))
        fig.add_trace(go.Scatter(x=sub_b[xcol], y=sub_b[yb], mode="lines", name=yb,
                                 line=dict(color=t["accent3"],width=1.5,dash="dot"), yaxis="y2"))
        layout = {**plot_cfg(theme),"height":600,
            "xaxis": dict(title=xcol+" (mbsf)",**axis_kw),
            "yaxis": dict(title=ya, color=t["accent"],**axis_kw),
            "yaxis2": dict(title=yb, color=t["accent3"], overlaying="y", side="right",
                           gridcolor="rgba(0,0,0,0)", linecolor=t["border"]),
            "showlegend":True,
            "legend":dict(bgcolor=t["panel"],bordercolor=t["border"],borderwidth=1),
        }
        fig.update_layout(**layout)
        return fig

    if mode == "rolling":
        window = max(2, int(rwin or 20))
        n_cols = len(all_cols)
        fig = make_subplots(rows=1, cols=n_cols, shared_yaxes=True, horizontal_spacing=0.03)
        for i, (yc, color) in enumerate(
            [(c, colors_a[j % len(colors_a)]) for j, c in enumerate(ycols_a)] +
            [(c, colors_b[j % len(colors_b)]) for j, c in enumerate(ycols_b)]
        ):
            sub = df[[xcol,yc]].dropna()
            rolled = sub[yc].rolling(window,center=True,min_periods=1).mean()
            fig.add_trace(go.Scatter(x=sub[yc],y=sub[xcol],mode="lines",
                                     name=yc+" (raw)", line=dict(color=color,width=0.6),
                                     opacity=0.35, showlegend=False), row=1,col=i+1)
            fig.add_trace(go.Scatter(x=rolled,y=sub[xcol],mode="lines",
                                     name=f"{yc}  (n={window})",
                                     line=dict(color=color,width=2.5)), row=1,col=i+1)
            fig.update_xaxes(title_text=yc,title_font=dict(size=10),**axis_kw,row=1,col=i+1)
        fig.update_yaxes(title_text=xcol+" (mbsf)",autorange="reversed",**axis_kw)
        fig.update_layout(showlegend=True,
                          legend=dict(bgcolor=t["panel"],bordercolor=t["border"],
                                      borderwidth=1,font=dict(size=10)), **cfg_base)
        return fig

    return empty_fig("Select a chart mode", theme=theme)

@app.callback(
    Output("pe-table-container","children"),
    Input("pe-active-store","data"), Input("pe-exp-filter","value"),
)
def pe_table(da, selected):
    if not da: return ""
    df = j2df(da)
    exp_col = next((c for c in df.columns if "expedition" in c.lower()),None)
    if exp_col and selected:
        df = df[df[exp_col].astype(str).isin(selected)]
    preview = df.head(200)
    return dash_table.DataTable(
        data=preview.to_dict("records"),
        columns=[{"name":c,"id":c} for c in preview.columns],
        page_size=10, sort_action="native", filter_action="native",
        style_table={"overflowX":"auto","minWidth":"100%"},
        style_header={"backgroundColor":"var(--bg)","color":"var(--accent)",
                      "fontWeight":"700","fontSize":"10px","border":f"1px solid var(--border)"},
        style_cell={"backgroundColor":"var(--panel)","color":"var(--text)","fontSize":"11px",
                    "padding":"7px 11px","border":f"1px solid var(--border)",
                    "fontFamily":FONT,"maxWidth":"160px","overflow":"hidden","textOverflow":"ellipsis"},
        style_data_conditional=[{"if":{"row_index":"odd"},"backgroundColor":"var(--bg)"}],
        fixed_rows={"headers": True},
    )

@app.callback(
    Output("pe-download","data"),
    Input("pe-download-btn","n_clicks"),
    State("pe-active-store","data"), State("pe-exp-filter","value"),
    prevent_initial_call=True,
)
def pe_download(n, da, selected):
    if not da: return None
    df = j2df(da)
    exp_col = next((c for c in df.columns if "expedition" in c.lower()),None)
    if exp_col and selected:
        df = df[df[exp_col].astype(str).isin(selected)]
    return dcc.send_data_frame(df.to_csv, "iodp_export.csv", index=False)

# =============================================================================
# ENTRY POINT
# =============================================================================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7860, debug=False)
