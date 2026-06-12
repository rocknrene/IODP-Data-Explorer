import io, base64, re, json, requests, threading
import numpy as np
import pandas as pd
import lasio
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
from dash import Dash, dcc, html, Input, Output, State, dash_table
import flask

# ── Color tokens ───────────────────────────────────────────────────────────────
C = dict(
    bg="#0d1117", panel="#161b22", border="#30363d",
    accent="#58a6ff", accent2="#3fb950", accent3="#d2a679",
    text="#e6edf3", muted="#8b949e", danger="#f85149", warn="#d29922",
)
PLOT_CFG = dict(
    paper_bgcolor=C["panel"], plot_bgcolor=C["bg"],
    font=dict(color=C["text"], family="monospace"),
    xaxis=dict(gridcolor=C["border"], zerolinecolor=C["border"]),
    yaxis=dict(gridcolor=C["border"], zerolinecolor=C["border"]),
    colorway=[C["accent"], C["accent2"], C["accent3"], "#bc8cff", "#ff7b72"],
    margin=dict(l=55, r=20, t=40, b=50),
)
CARD = dict(background=C["panel"], border=f"1px solid {C['border']}",
            borderRadius="8px", padding="14px")
FONT = "monospace"
DD   = {"background": C["bg"], "color": C["text"],
        "border": f"1px solid {C['border']}", "borderRadius": "4px"}
LBL  = {"color": C["muted"], "fontSize": "10px", "letterSpacing": "2px",
        "marginBottom": "4px", "marginTop": "12px", "fontFamily": FONT}
INP  = {"width": "100%", "background": C["bg"], "color": C["text"],
        "border": f"1px solid {C['border']}", "borderRadius": "4px",
        "padding": "4px 8px", "fontSize": "11px", "fontFamily": FONT,
        "boxSizing": "border-box"}
BTN  = lambda bg: {"backgroundColor": bg, "color": C["bg"], "border": "none",
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

# ── Repository fetch config ────────────────────────────────────────────────────
# LIMS/LORE — GCR (TAMU), JR expeditions 317+
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

# PANGAEA — BCR (Bremen) / ESO/ECORD, mission-specific platform expeditions
# Direct tabular download: https://doi.pangaea.de/10.1594/PANGAEA.{id}?format=textfile
# Search API: https://www.pangaea.de/api/datasets/search?q=...&count=20
PANGAEA_SEARCH  = "https://www.pangaea.de/api/datasets/search"
PANGAEA_DOI_DL  = "https://doi.pangaea.de/10.1594/PANGAEA.{pid}?format=textfile"

# J-CORES (KCC/JAMSTEC) — Chikyu expeditions (343, 405, 319, etc.)
# No public REST API — upload only. Label this clearly in the UI.

# ── Header row detection ───────────────────────────────────────────────────────
HEADER_KEYWORDS = [
    "depth", "lith", "facies", "unit", "section", "sample", "core",
    "upper", "lower", "top", "bottom", "description", "interval", "formation",
]

def detect_header_row(raw_bytes, encoding="utf-8", n_scan=30):
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
    return df.to_json(date_format="iso", orient="split") if df is not None else None

def j2df(j):
    return pd.read_json(io.StringIO(j), orient="split") if j else None

def empty_fig(msg="Upload a file to begin", color=None):
    fig = go.Figure()
    fig.update_layout(**PLOT_CFG, annotations=[dict(
        text=msg, xref="paper", yref="paper", x=0.5, y=0.5,
        showarrow=False, font=dict(color=color or C["muted"], size=15))])
    return fig

# ── Litho column resolution ────────────────────────────────────────────────────
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

# ── Site metadata ──────────────────────────────────────────────────────────────
def infer_site_meta(df, meta):
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

# ── Core-top extraction ────────────────────────────────────────────────────────
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

# ── Repository fetch helpers ───────────────────────────────────────────────────
def fetch_lore(report_name, expedition, site="", hole=""):
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
    """Fetch a single PANGAEA dataset by numeric ID (e.g. 938129).
    Downloads the tab-delimited textfile export and parses it,
    skipping the comment header lines that start with //.
    Returns (DataFrame, error_str)."""
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
    """Search PANGAEA and return a list of (title, doi_id, url) tuples."""
    try:
        r = requests.get(PANGAEA_SEARCH,
                         params={"q": query, "count": count, "format": "json"},
                         timeout=20)
        r.raise_for_status()
        data = r.json()
        results = []
        for item in data.get("results", []):
            doi = item.get("doi", "")
            pid = doi.split(".")[-1] if doi else ""
            title = item.get("title", doi)
            results.append({"label": f"{pid} — {title[:60]}", "value": pid})
        return results, None
    except Exception as e:
        return [], str(e)


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

# ── Chart builder (unchanged from original) ───────────────────────────────────
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

# ── App ────────────────────────────────────────────────────────────────────────
server = flask.Flask(__name__)
app    = Dash(__name__, server=server, suppress_callback_exceptions=True)

TAB_STYLE = {"backgroundColor":C["panel"],"color":C["muted"],
             "border":f"1px solid {C['border']}","borderBottom":"none",
             "fontFamily":FONT,"fontSize":"13px","padding":"8px 20px"}
TAB_SEL   = {**TAB_STYLE,"backgroundColor":C["bg"],"color":C["text"],
             "borderBottom":f"1px solid {C['bg']}","fontWeight":"600"}

# ── Shipboard sidebar (original, unchanged) ────────────────────────────────────
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
    """Build the Dataset A or B fetch panel. ds = 'a' or 'b'."""
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
    html.P("X AXIS", style=LBL),
    dcc.Dropdown(id="pe-xaxis", options=[], value=None, style=DD),
    html.P("Y AXIS", style=LBL),
    dcc.Dropdown(id="pe-yaxis", options=[], value=None, multi=True, style=DD),
], style={"width":"260px","minWidth":"260px","background":C["panel"],
          "borderRight":f"1px solid {C['border']}","padding":"18px","overflowY":"auto"})

# ── App layout ─────────────────────────────────────────────────────────────────
app.layout = html.Div([
    html.Div([
        html.Div([
            html.Span("IODP",      style={"fontWeight":"700","color":C["accent"]}),
            html.Span(" Explorer", style={"fontWeight":"300","color":C["text"]}),
        ], style={"fontSize":"17px","fontFamily":FONT}),
        html.Div("International Ocean Discovery Program · Data Visualization Tool",
                 style={"color":C["muted"],"fontSize":"11px","fontFamily":FONT}),
    ], style={"display":"flex","justifyContent":"space-between","alignItems":"center",
              "padding":"8px 20px","background":C["panel"],
              "borderBottom":f"1px solid {C['border']}"}),

    dcc.Tabs(id="main-tabs", value="shipboard", style={"fontFamily":FONT},
        children=[
            dcc.Tab(label="Shipboard",       value="shipboard", style=TAB_STYLE, selected_style=TAB_SEL),
            dcc.Tab(label="Post-Expedition", value="postexp",   style=TAB_STYLE, selected_style=TAB_SEL),
        ]),

    html.Div(id="tab-content"),

    # Stores
    dcc.Store(id="store-df",        storage_type="session"),
    dcc.Store(id="store-meta",      storage_type="session"),
    dcc.Store(id="store-litho",     storage_type="session"),
    dcc.Store(id="store-site-info", storage_type="session"),
    dcc.Store(id="pe-store-a"),
    dcc.Store(id="pe-store-b"),
    dcc.Store(id="pe-merged-store"),

], style={"height":"100vh","display":"flex","flexDirection":"column",
          "background":C["bg"],"color":C["text"],"overflow":"hidden","fontFamily":FONT})

# ── Tab routing ────────────────────────────────────────────────────────────────
@app.callback(Output("tab-content","children"), Input("main-tabs","value"))
def render_tab(tab):
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


# ── Shipboard callbacks (original, unchanged) ──────────────────────────────────
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

# ── Post-Expedition: source panel toggles ─────────────────────────────────────
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
    Input("pe-merged-store","data"),
)
def pe_axis_opts(dm):
    if not dm: return [],[]
    df = j2df(dm)
    opts = [{"label":c,"value":c} for c in df.columns]
    return opts, opts

@app.callback(
    Output("pe-xaxis","value"), Output("pe-yaxis","value"),
    Input("pe-xaxis","options"), prevent_initial_call=True,
)
def pe_axis_defaults(opts):
    if not opts: return None,None
    cols = [o["value"] for o in opts]
    depth = next((c for c in cols if "depth" in c.lower()),cols[0])
    others = [c for c in cols if c not in (depth,"depth_key")]
    return depth, others[:4]

# ── Post-Expedition: merged expeditions readout ───────────────────────────────
@app.callback(
    Output("pe-merged-expeditions","children"),
    Input("pe-merged-store","data"), Input("pe-exp-filter","value"),
)
def pe_merged_exp_readout(dm, selected):
    if not dm: return "n/a"
    df = j2df(dm)
    exps = get_expeditions_from_df(df)
    filtered = [e for e in exps if e in (selected or [])]
    return ", ".join(filtered) if filtered else "n/a"

# ── Post-Expedition: chart ────────────────────────────────────────────────────
@app.callback(
    Output("pe-chart","figure"),
    Input("pe-merged-store","data"), Input("pe-exp-filter","value"),
    Input("pe-xaxis","value"), Input("pe-yaxis","value"),
)
def pe_chart(dm, selected, xcol, ycols):
    if not dm or not xcol: return empty_fig("Merge two datasets to visualise")
    df = j2df(dm)
    exp_col = next((c for c in df.columns if "expedition" in c.lower()),None)
    if exp_col and selected:
        df = df[df[exp_col].astype(str).isin(selected)]
    ycols = ycols or []
    if isinstance(ycols,str): ycols=[ycols]
    if not ycols: return empty_fig("Select Y axis columns")
    fig = make_subplots(rows=1, cols=len(ycols), shared_yaxes=True, horizontal_spacing=0.02)
    colors=[C["accent"],C["accent2"],C["accent3"],"#bc8cff","#ff7b72"]
    for i,yc in enumerate(ycols):
        if yc not in df.columns: continue
        fig.add_trace(go.Scatter(x=df[yc], y=df[xcol], mode="lines", name=yc,
                                  line=dict(color=colors[i%len(colors)],width=1.5)),
                      row=1, col=i+1)
        fig.update_xaxes(title_text=yc, gridcolor=C["border"], linecolor=C["border"],
                         row=1, col=i+1)
    cfg={**PLOT_CFG,"height":580}
    cfg.pop("xaxis",None); cfg.pop("yaxis",None)
    fig.update_yaxes(title_text=xcol, autorange="reversed",
                     gridcolor=C["border"], linecolor=C["border"])
    fig.update_layout(showlegend=True, **cfg)
    return fig

# ── Post-Expedition: table ────────────────────────────────────────────────────
@app.callback(
    Output("pe-table-container","children"),
    Input("pe-merged-store","data"), Input("pe-exp-filter","value"),
)
def pe_table(dm, selected):
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
    if not dm: return None
    df = j2df(dm)
    exp_col = next((c for c in df.columns if "expedition" in c.lower()),None)
    if exp_col and selected:
        df = df[df[exp_col].astype(str).isin(selected)]
    return dcc.send_data_frame(df.to_csv, "iodp_merged.csv", index=False)

# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7860, debug=False)
