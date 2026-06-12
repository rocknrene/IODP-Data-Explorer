import io, base64, re
import numpy as np
import pandas as pd
import lasio
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
from dash import Dash, dcc, html, Input, Output, State, dash_table
import flask

# ── Color tokens (dark theme) ──────────────────────────────────────────────────
C = dict(
    bg="#0d1117",      # page background
    panel="#161b22",   # sidebar and card surfaces
    border="#30363d",  # dividers and input borders
    accent="#58a6ff",  # primary blue highlight
    accent2="#3fb950", # green highlight
    accent3="#d2a679", # amber highlight
    text="#e6edf3",    # primary text
    muted="#8b949e",   # secondary/label text
    danger="#f85149",  # QC flag red
    warn="#d29922",    # core-top tick amber
)

# Shared Plotly layout applied to every figure
PLOT_CFG = dict(
    paper_bgcolor=C["panel"], plot_bgcolor=C["bg"],
    font=dict(color=C["text"], family="monospace"),
    xaxis=dict(gridcolor=C["border"], zerolinecolor=C["border"]),
    yaxis=dict(gridcolor=C["border"], zerolinecolor=C["border"]),
    colorway=[C["accent"], C["accent2"], C["accent3"], "#bc8cff", "#ff7b72"],
    margin=dict(l=55, r=20, t=40, b=50),
)

# Reusable card style dict
CARD = dict(background=C["panel"], border=f"1px solid {C['border']}",
            borderRadius="8px", padding="14px")

FONT = "monospace"

# Dropdown style
DD = {"background": C["bg"], "color": C["text"],
      "border": f"1px solid {C['border']}", "borderRadius": "4px"}

# Sidebar section label style
LBL = {"color": C["muted"], "fontSize": "10px", "letterSpacing": "2px",
       "marginBottom": "4px", "marginTop": "12px", "fontFamily": FONT}

# Lithology name → fill color mapping (case-insensitive lookup via litho_color())
LITHO_COLORS = {
    "clay":           "#1D9E75",
    "silty clay":     "#5DCAA5",
    "silt":           "#888780",
    "sand":           "#EF9F27",
    "mtd":            "#D85A30",
    "mtd / chaotic":  "#D85A30",
    "turbidite":      "#378ADD",
    "hemipelagite":   "#3fb950",
    "gravel":         "#BA7517",
    "chalk":          "#B5D4F4",
    "limestone":      "#85B7EB",
    "basalt":         "#444441",
    "ash":            "#D3D1C7",
}

def litho_color(name):
    """Return the fill color for a given lithology name, defaulting to dark gray."""
    return LITHO_COLORS.get(str(name).lower().strip(), "#444441")


# ── Header row detection ───────────────────────────────────────────────────────

# Keywords likely to appear in a real data header row
HEADER_KEYWORDS = [
    "depth", "lith", "facies", "unit", "section",
    "sample", "core", "upper", "lower", "top", "bottom",
    "description", "interval", "formation",
]

def detect_header_row(raw_bytes, encoding="utf-8", n_scan=30):
    """Scan the first n_scan rows of a CSV and return the index of the row
    that most looks like a header (highest count of header keywords).
    Returns 0 if no clear header is found beyond the first row."""
    try:
        lines = raw_bytes.decode(encoding, errors="replace").splitlines()
    except Exception:
        return 0

    best_row   = 0
    best_score = 0

    for i, line in enumerate(lines[:n_scan]):
        line_lower = line.lower()
        score = sum(1 for kw in HEADER_KEYWORDS if kw in line_lower)
        if score > best_score:
            best_score = score
            best_row   = i

    return best_row


# ── File parsing ───────────────────────────────────────────────────────────────
def parse_upload(contents, filename):
    """Decode a Dash upload (base64) into a DataFrame + metadata dict.
    Supports CSV, XLSX/XLS, and LAS formats.
    For CSV and Excel, automatically detects the true header row so that
    IODP supplementary tables with title/DOI preamble rows load correctly."""
    _, b64 = contents.split(",")
    raw    = base64.b64decode(b64)
    fname  = filename.lower()
    meta   = {"filename": filename}
    try:
        if fname.endswith(".csv"):
            df = None
            # Try common encodings; for each, detect the header row first
            for enc in ["utf-8", "latin-1", "cp1252", "utf-16"]:
                try:
                    header_row = detect_header_row(raw, encoding=enc)
                    df = pd.read_csv(
                        io.StringIO(raw.decode(enc)),
                        header=header_row,
                        skip_blank_lines=True,
                    )
                    # Drop columns and rows that are entirely NaN
                    # (common artifact of preamble rows in IODP tables)
                    df = df.dropna(axis=1, how="all").dropna(how="all")
                    df = df.reset_index(drop=True)
                    break
                except Exception:
                    continue
            if df is None:
                return None, {"error": "Could not decode CSV"}
            meta["format"] = "CSV"

        elif fname.endswith((".xlsx", ".xls")):
            # Scan skiprows 0–19 and score each candidate header
            best_df    = None
            best_score = -1
            for skip in range(0, 20):
                try:
                    candidate = pd.read_excel(io.BytesIO(raw), skiprows=skip)
                    candidate = candidate.dropna(axis=1, how="all").dropna(how="all")
                    score = sum(
                        1 for col in candidate.columns
                        for kw in HEADER_KEYWORDS
                        if kw in str(col).lower()
                    )
                    if score > best_score:
                        best_score = score
                        best_df    = candidate
                except Exception:
                    continue
            if best_df is None:
                return None, {"error": "Could not read Excel file"}
            df = best_df.reset_index(drop=True)
            meta["format"] = "Excel"

        elif fname.endswith(".las"):
            # Try common encodings for LAS files
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

        # Store column and row counts for downstream use
        meta.update(
            rows=len(df), cols=len(df.columns),
            columns=list(df.columns),
            numeric_cols=df.select_dtypes(include="number").columns.tolist(),
        )
        return df, meta

    except Exception as e:
        return None, {"error": str(e)}


def df2j(df):
    """Serialize a DataFrame to JSON for dcc.Store."""
    return df.to_json(date_format="iso", orient="split") if df is not None else None

def j2df(j):
    """Deserialize a dcc.Store JSON string back to a DataFrame."""
    return pd.read_json(j, orient="split") if j else None

def empty_fig(msg="Upload a file to begin", color=None):
    """Return a blank Plotly figure with a centered message."""
    fig = go.Figure()
    fig.update_layout(**PLOT_CFG, annotations=[dict(
        text=msg, xref="paper", yref="paper", x=0.5, y=0.5,
        showarrow=False, font=dict(color=color or C["muted"], size=15))])
    return fig


# ── Litho column alias resolution ──────────────────────────────────────────────

# Each tuple: (internal_name, [fragments that should match column names])
# Matching is case-insensitive substring search
LITHO_COLUMN_ALIASES = [
    ("top_mbsf", [
        "top depth csf",    # Top Depth CSF-A (m)
        "top depth",
        "upper depth",
        "depth top",
        "topdepth",
        "top_depth",
        "top_mbsf",
        "top (m",           # Top (mbsf)
        "top_csf",
    ]),
    ("bottom_mbsf", [
        "bottom depth csf", # Bottom Depth CSF-A (m)
        "bottom depth",
        "lower depth",
        "depth bottom",
        "bottomdepth",
        "bottom_depth",
        "bottom_mbsf",
        "bottom (m",        # Bottom (mbsf)
        "bot_csf",
        "bot depth",
    ]),
    ("lithology", [
        "lithofacies",
        "lithology",
        "lith. unit",
        "lith unit",
        "litho unit",
        "lithostratigraphic",
        "facies",
        "description",
        "sediment type",
        "rock type",
        "unit name",
    ]),
]


def resolve_litho_columns(df):
    """Attempt to map uploaded columns to the three internal names
    (top_mbsf, bottom_mbsf, lithology) using flexible alias matching.

    Returns:
        (renamed_df, error_string)
        On success: (df, None)
        On failure: (None, descriptive error string)
    """
    cols_lower = {c.lower().strip(): c for c in df.columns}
    mapping    = {}  # internal_name → original column name

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

    # Check that all three required columns were resolved
    missing = [name for name in ("top_mbsf", "bottom_mbsf", "lithology")
               if name not in mapping]

    if missing:
        detected = list(df.columns)
        error = (
            f"Could not identify the following required columns: "
            f"{', '.join(missing)}.\n\n"
            f"Detected columns in your file:\n"
            + "\n".join(f"  • {c}" for c in detected)
            + "\n\nExpected something like:\n"
              "  • Top Depth CSF-A (m) / Top Depth / Upper Depth / top_mbsf\n"
              "  • Bottom Depth CSF-A (m) / Bottom Depth / Lower Depth / bottom_mbsf\n"
              "  • Lithology / Lithofacies / Facies / Description"
        )
        return None, error

    # Rename to internal schema
    rename_map = {v: k for k, v in mapping.items()}
    df = df.rename(columns=rename_map)

    # Convert depth columns to numeric; coerce anything non-numeric to NaN
    for col in ("top_mbsf", "bottom_mbsf"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Drop rows with missing depths or inverted/zero-thickness intervals
    df = df.dropna(subset=["top_mbsf", "bottom_mbsf"])
    df = df[df["top_mbsf"] < df["bottom_mbsf"]]
    df = df.reset_index(drop=True)

    # Ensure lithology is stored as a clean string
    df["lithology"] = df["lithology"].fillna("unknown").astype(str).str.strip()

    return df, None


# ── Site metadata inference ────────────────────────────────────────────────────
def infer_site_meta(df, meta):
    """Best-effort extraction of IODP site info from column contents."""
    info       = {}
    cols_lower = {c.lower(): c for c in df.columns}

    # Try to parse site/hole from the first JCORES sample ID (e.g. C0019J-6K-1)
    for cand in ["jcores_sampleid", "sampleid", "sample_id", "sample"]:
        if cand in cols_lower:
            first = str(df[cols_lower[cand]].dropna().iloc[0]) if len(df) else ""
            m = re.match(r"([A-Z]\d{3,4}[A-Z]?)", first)
            if m:
                info["site_hole"] = m.group(1)
            break

    # Find depth range from the first recognized depth column
    for cand in ["topdepth_mbsf", "topdepth_mbsf_mcsf-a", "depth_mbsf",
                 "dept", "depth", "top_depth", "top_mbsf"]:
        if cand in cols_lower:
            v = df[cols_lower[cand]].dropna()
            if len(v):
                info["depth_min"] = f"{v.min():.1f}"
                info["depth_max"] = f"{v.max():.1f}"
            break

    info["rows"]     = meta.get("rows", "")
    info["filename"] = meta.get("filename", "")
    info["fmt"]      = meta.get("format", "")
    return info


def build_metadata_bar(info, manual):
    """Render the site metadata banner row."""
    def field(label, value):
        return html.Div([
            html.Div(label, style={"color": C["muted"], "fontSize": "9px",
                                   "letterSpacing": "1.5px", "fontFamily": FONT}),
            html.Div(value, style={"color": C["text"], "fontSize": "13px",
                                   "fontWeight": "600", "fontFamily": FONT}),
        ], style={"marginRight": "20px"})

    site_hole  = manual.get("site_hole")   or info.get("site_hole", "—")
    expedition = manual.get("expedition")  or "—"
    lat        = manual.get("lat")         or "—"
    lon        = manual.get("lon")         or "—"
    water_d    = manual.get("water_depth") or "—"
    recovery   = manual.get("recovery")    or "—"
    d_min      = info.get("depth_min", "—")
    d_max      = info.get("depth_max", "—")
    depth_str  = f"{d_min} – {d_max} mbsf" if d_min != "—" else "—"

    return html.Div([
        html.Div([
            field("EXPEDITION",  expedition),
            field("SITE / HOLE", site_hole),
            field("LAT / LON",   f"{lat}  {lon}"),
            field("WATER DEPTH", f"{water_d} m" if water_d != "—" else "—"),
            field("RECOVERY",    f"{recovery}%" if recovery != "—" else "—"),
            field("DEPTH RANGE", depth_str),
        ], style={"display": "flex", "alignItems": "center", "flexWrap": "wrap"}),
        html.Div([
            html.Span(info.get("filename", ""),
                      style={"background": C["border"], "padding": "3px 10px",
                             "borderRadius": "12px", "fontSize": "11px",
                             "fontFamily": FONT}),
            html.Span(info.get("fmt", ""),
                      style={"background": C["accent"], "color": C["bg"],
                             "padding": "3px 10px", "borderRadius": "12px",
                             "fontSize": "11px", "fontWeight": "700"}),
            html.Span(f"{info.get('rows', '')} rows",
                      style={"color": C["muted"], "fontSize": "11px"}),
        ], style={"display": "flex", "gap": "8px", "alignItems": "center"}),
    ], style={
        "display": "flex", "justifyContent": "space-between",
        "alignItems": "center", "padding": "10px 20px",
        "background": C["panel"], "borderBottom": f"1px solid {C['border']}",
        "flexWrap": "wrap", "gap": "8px",
    })


# ── Core-top extraction ────────────────────────────────────────────────────────
def extract_core_tops(df):
    """Parse JCORES-style sample IDs to find the shallowest depth per core."""
    cols_lower = {c.lower(): c for c in df.columns}
    id_col, depth_col = None, None

    for cand in ["jcores_sampleid", "sampleid", "sample_id", "sample"]:
        if cand in cols_lower:
            id_col = cols_lower[cand]; break

    for cand in ["topdepth_mbsf", "topdepth_mbsf_mcsf-a", "depth_mbsf", "dept", "depth"]:
        if cand in cols_lower:
            depth_col = cols_lower[cand]; break

    if id_col is None or depth_col is None:
        return {}

    core_tops = {}
    for sid, depth in zip(df[id_col], df[depth_col]):
        parts = str(sid).split("-")
        if len(parts) >= 3 and pd.notna(depth):
            key = f"{parts[0]}-{parts[1]}"
            d   = float(depth)
            if key not in core_tops or d < core_tops[key]:
                core_tops[key] = d
    return core_tops


# ── QC helper ──────────────────────────────────────────────────────────────────
def find_qc_col(df):
    """Return the first column whose name contains 'comment', or None."""
    for col in df.columns:
        if re.search(r"comment", col, re.IGNORECASE):
            return col
    return None


# ── Recovery gap detection ─────────────────────────────────────────────────────
def find_recovery_gaps(df, depth_col, gap_threshold_m=5.0):
    """Return (top, bottom) pairs where consecutive depths exceed the threshold."""
    if depth_col not in df.columns:
        return []
    depths = df[depth_col].dropna().sort_values().values
    return [
        (float(depths[i]), float(depths[i+1]))
        for i in range(len(depths) - 1)
        if depths[i+1] - depths[i] > gap_threshold_m
    ]


# ── Chart builder ──────────────────────────────────────────────────────────────
def make_chart(df, ctype, x, y, color, curves,
               litho_df=None, show_gaps=True, show_qc=True, show_core_tops=True):
    """Build and return a Plotly figure for the selected chart type."""

    if ctype == "scatter" and x and y:
        cc = None if color in (None, "None", "") else color
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
        n_cols     = len(all_labels)
        raw_widths = [0.06 if l == "Litho" else 1.0 for l in all_labels]
        total      = sum(raw_widths)
        col_widths = [w / total for w in raw_widths]

        fig = make_subplots(
            rows=1, cols=n_cols, shared_yaxes=True,
            subplot_titles=all_labels,
            column_widths=col_widths,
            horizontal_spacing=0.01,
        )

        col_offset = 1

        # Litho track
        if has_litho:
            for _, row in litho_df.iterrows():
                fig.add_shape(
                    type="rect", x0=0, x1=1,
                    y0=row["top_mbsf"], y1=row["bottom_mbsf"],
                    fillcolor=litho_color(row.get("lithology", "")),
                    opacity=0.75, line_width=0,
                    row=1, col=col_offset,
                    xref=f"x{col_offset if col_offset > 1 else ''} domain",
                    yref="y",
                )
                fig.add_annotation(
                    x=0.5, y=(row["top_mbsf"] + row["bottom_mbsf"]) / 2,
                    text=str(row.get("lithology", ""))[:6],
                    showarrow=False, font=dict(size=7, color="#ffffff"),
                    textangle=-90,
                    xref=f"x{col_offset if col_offset > 1 else ''} domain",
                    yref="y", row=1, col=col_offset,
                )
            fig.add_trace(
                go.Scatter(
                    x=[0.5, 0.5],
                    y=[litho_df["top_mbsf"].min(), litho_df["bottom_mbsf"].max()],
                    mode="markers", marker_opacity=0, showlegend=False, name="",
                ),
                row=1, col=col_offset,
            )
            col_offset += 1

        # Data log tracks
        pal       = [C["accent"], C["accent2"], C["accent3"], "#bc8cff", "#ff7b72"]
        gaps      = find_recovery_gaps(df, x) if show_gaps      else []
        core_tops = extract_core_tops(df)      if show_core_tops else {}
        qc_col    = find_qc_col(df)            if show_qc        else None
        qc_depths = []
        if qc_col and qc_col in df.columns:
            qc_mask   = df[qc_col].fillna("").astype(str).str.strip() != ""
            qc_depths = df.loc[qc_mask, x].dropna().tolist()

        for i, col in enumerate(sel):
            mask = df[col].notna() & df[x].notna()

            # Main log line
            fig.add_trace(
                go.Scatter(
                    x=df.loc[mask, col], y=df.loc[mask, x],
                    mode="lines", name=col,
                    line=dict(color=pal[i % len(pal)], width=1.5),
                ),
                row=1, col=col_offset + i,
            )

            # Recovery gap hatching
            for gap_top, gap_bot in gaps:
                fig.add_hrect(
                    y0=gap_top, y1=gap_bot,
                    fillcolor="#888780", opacity=0.18, line_width=0,
                    row=1, col=col_offset + i,
                    annotation_text="gap" if i == 0 else "",
                    annotation_font=dict(size=8, color=C["muted"]),
                    annotation_position="top left",
                )

            # QC flag markers
            if qc_depths:
                qc_df = df.loc[df[x].isin(qc_depths) & df[col].notna()]
                if len(qc_df):
                    fig.add_trace(
                        go.Scatter(
                            x=qc_df[col], y=qc_df[x],
                            mode="markers",
                            name="QC flagged" if i == 0 else "",
                            showlegend=(i == 0),
                            marker=dict(symbol="circle-open", size=8,
                                        color=C["danger"], line_width=1.5),
                            hovertemplate="%{y:.2f} mbsf — QC flagged<extra></extra>",
                        ),
                        row=1, col=col_offset + i,
                    )

            # Core-top tick marks on first data track only
            if show_core_tops and i == 0 and core_tops:
                x_min    = df[col].min()
                x_range  = (df[col].max() - x_min) or 1
                tick_end = x_min + x_range * 0.12
                for core_label, core_depth in core_tops.items():
                    fig.add_shape(
                        type="line",
                        x0=x_min, x1=tick_end, y0=core_depth, y1=core_depth,
                        line=dict(color=C["warn"], width=0.8, dash="dot"),
                        row=1, col=col_offset + i,
                    )
                    fig.add_annotation(
                        x=tick_end, y=core_depth,
                        text=core_label.split("-")[-1],
                        showarrow=False, font=dict(size=7, color=C["warn"]),
                        xanchor="left", yanchor="middle",
                        row=1, col=col_offset + i,
                    )

        fig.update_yaxes(autorange="reversed", title_text=x, row=1, col=1)
        cfg = {**PLOT_CFG, "height": 620}
        cfg.pop("xaxis", None)
        cfg.pop("yaxis", None)
        return fig.update_layout(
            showlegend=True,
            legend=dict(x=1.01, y=1, font=dict(size=10)),
            **cfg,
        )

    return empty_fig("Select axes to plot")


# ── Dash app ───────────────────────────────────────────────────────────────────
server = flask.Flask(__name__)  # expose Flask server for Hugging Face
app    = Dash(__name__, server=server, suppress_callback_exceptions=True)

sidebar = html.Div([
    # Primary data file upload
    html.P("DATA SOURCE", style=LBL),
    dcc.Upload(
        id="upload", multiple=False,
        children=html.Div([
            html.Div("↑", style={"fontSize": "26px", "color": C["accent"]}),
            html.Div("Drop file or click"),
            html.Div(".csv .xlsx .las", style={"color": C["muted"], "fontSize": "10px"}),
        ], style={"textAlign": "center", "color": C["text"], "fontSize": "12px"}),
        style={"border": f"2px dashed {C['border']}", "borderRadius": "8px",
               "padding": "16px", "cursor": "pointer", "marginBottom": "10px"},
    ),

    # Optional litho track upload
    html.P("LITHO TRACK (optional)", style=LBL),
    html.Div("CSV or Excel: top depth, bottom depth, lithology columns",
             style={"color": C["muted"], "fontSize": "9px",
                    "marginBottom": "4px", "fontFamily": FONT}),
    dcc.Upload(
        id="upload-litho", multiple=False,
        children=html.Div([
            html.Div("↑", style={"fontSize": "18px", "color": C["accent3"]}),
            html.Div("Drop litho file"),
        ], style={"textAlign": "center", "color": C["text"], "fontSize": "11px"}),
        style={"border": f"2px dashed {C['border']}", "borderRadius": "8px",
               "padding": "10px", "cursor": "pointer", "marginBottom": "4px"},
    ),
    html.Div(id="litho-badge"),

    html.Hr(style={"borderColor": C["border"], "margin": "14px 0"}),

    # Manual site metadata override fields
    html.P("SITE METADATA (optional)", style=LBL),
    html.Div("Auto-detected where possible:",
             style={"color": C["muted"], "fontSize": "9px", "marginBottom": "6px"}),
    *[
        html.Div([
            html.Div(label, style={**LBL, "marginTop": "6px"}),
            dcc.Input(id=fid, type="text", placeholder=ph, debounce=True,
                      style={"width": "100%", "background": C["bg"],
                             "color": C["text"], "border": f"1px solid {C['border']}",
                             "borderRadius": "4px", "padding": "4px 8px",
                             "fontSize": "11px", "fontFamily": FONT}),
        ])
        for label, fid, ph in [
            ("EXPEDITION",    "meta-expedition",  "e.g. IODP 405"),
            ("SITE / HOLE",   "meta-site-hole",   "e.g. C0019J"),
            ("LAT",           "meta-lat",          "e.g. 38.1°N"),
            ("LON",           "meta-lon",          "e.g. 143.9°E"),
            ("WATER DEPTH m", "meta-water-depth",  "e.g. 6897"),
            ("RECOVERY %",    "meta-recovery",     "e.g. 68.4"),
        ]
    ],

    html.Hr(style={"borderColor": C["border"], "margin": "14px 0"}),

    # Axis selectors
    html.P("X AXIS (depth)", style=LBL),
    dcc.Dropdown(id="x-col", placeholder="Select column...", style=DD),

    html.P("Y AXIS", id="y-lbl", style=LBL),
    dcc.Dropdown(id="y-col", placeholder="Select column...", style=DD),

    html.P("COLOR BY", id="color-lbl", style=LBL),
    dcc.Dropdown(id="color-col", placeholder="None", style=DD),

    # Curve checklist — only visible in depth log mode
    html.P("CURVES (depth log)", id="curves-lbl", style={**LBL, "display": "none"}),
    dcc.Checklist(id="depth-curves", options=[], value=[],
                  labelStyle={"display": "block", "marginBottom": "5px",
                               "color": C["text"], "fontSize": "12px"},
                  inputStyle={"marginRight": "6px", "accentColor": C["accent"]},
                  style={"display": "none"}),

    html.Hr(style={"borderColor": C["border"], "margin": "14px 0"}),

    # Depth log overlay toggles
    html.P("DEPTH LOG OVERLAYS", style=LBL),
    dcc.Checklist(
        id="overlay-opts",
        options=[
            {"label": " Recovery gap hatching", "value": "gaps"},
            {"label": " QC flag markers",        "value": "qc"},
            {"label": " Core-top tick marks",    "value": "core_tops"},
        ],
        value=["gaps", "qc", "core_tops"],
        labelStyle={"display": "block", "marginBottom": "6px",
                    "color": C["text"], "fontSize": "11px", "fontFamily": FONT},
        inputStyle={"marginRight": "6px", "accentColor": C["accent"]},
    ),

    html.Hr(style={"borderColor": C["border"], "margin": "14px 0"}),

    # Chart type selector
    html.P("CHART TYPE", style={**LBL, "marginTop": "18px"}),
    dcc.RadioItems(
        id="chart-type", value="scatter",
        options=[
            {"label": " Scatter",             "value": "scatter"},
            {"label": " Line",                "value": "line"},
            {"label": " Histogram",           "value": "histogram"},
            {"label": " Depth Log",           "value": "depthlog"},
            {"label": " Correlation Heatmap", "value": "heatmap"},
        ],
        labelStyle={"display": "block", "marginBottom": "8px",
                    "color": C["text"], "fontSize": "12px", "fontFamily": FONT},
        inputStyle={"marginRight": "7px", "accentColor": C["accent"]},
    ),
], style={"width": "240px", "minWidth": "240px", "background": C["panel"],
          "borderRight": f"1px solid {C['border']}", "padding": "18px",
          "overflowY": "auto"})


app.layout = html.Div([
    # Top wordmark bar
    html.Div([
        html.Div([
            html.Span("IODP",      style={"fontWeight": "700", "color": C["accent"]}),
            html.Span(" Explorer", style={"fontWeight": "300", "color": C["text"]}),
        ], style={"fontSize": "17px", "fontFamily": FONT}),
        html.Div("International Ocean Discovery Program · Data Visualization Tool",
                 style={"color": C["muted"], "fontSize": "11px", "fontFamily": FONT}),
    ], style={"display": "flex", "justifyContent": "space-between",
              "alignItems": "center", "padding": "8px 20px",
              "background": C["panel"], "borderBottom": f"1px solid {C['border']}"}),

    # Site metadata banner
    html.Div(id="meta-banner",
             children=html.Div("Upload a file to see site metadata.",
                               style={"color": C["muted"], "fontSize": "11px",
                                      "padding": "10px 20px", "fontFamily": FONT})),

    # Main content
    html.Div([
        sidebar,
        html.Div([
            html.Div(id="kpi-bar",
                     style={"display": "flex", "gap": "10px", "padding": "10px 18px",
                            "borderBottom": f"1px solid {C['border']}", "flexWrap": "wrap"}),
            html.Div(dcc.Graph(id="main-chart", style={"height": "620px"},
                               config={"displayModeBar": True, "scrollZoom": True}),
                     style={"padding": "10px 18px"}),
            html.Div([
                html.Div([
                    html.Span("DATA TABLE",
                              style={"color": C["muted"], "fontSize": "10px",
                                     "letterSpacing": "2px"}),
                    html.Span(id="row-count",
                              style={"color": C["accent"], "fontSize": "11px",
                                     "marginLeft": "12px"}),
                ], style={"marginBottom": "8px"}),
                html.Div(id="table-container"),
            ], style={**CARD, "margin": "0 18px 18px 18px"}),
        ], style={"flex": "1", "overflowY": "auto", "minWidth": "0"}),
    ], style={"display": "flex", "flex": "1", "overflow": "hidden"}),

    # Per-session data stores — each browser tab gets isolated data
    dcc.Store(id="store-df",        storage_type="session"),
    dcc.Store(id="store-meta",      storage_type="session"),
    dcc.Store(id="store-litho",     storage_type="session"),
    dcc.Store(id="store-site-info", storage_type="session"),

], style={"height": "100vh", "display": "flex", "flexDirection": "column",
          "background": C["bg"], "color": C["text"],
          "overflow": "hidden", "fontFamily": FONT})


# ── Callbacks ──────────────────────────────────────────────────────────────────

@app.callback(
    Output("store-df",        "data"),
    Output("store-meta",      "data"),
    Output("store-site-info", "data"),
    Input("upload", "contents"),
    State("upload", "filename"),
)
def load_file(contents, filename):
    """Parse the primary uploaded file and infer site metadata."""
    if not contents:
        return None, {}, {}
    df, meta = parse_upload(contents, filename)
    if "error" in meta:
        return None, meta, {}
    return df2j(df), meta, infer_site_meta(df, meta)


@app.callback(
    Output("store-litho", "data"),
    Output("litho-badge", "children"),
    Input("upload-litho", "contents"),
    State("upload-litho", "filename"),
)
def load_litho(contents, filename):
    """Parse the optional litho upload with flexible header and column detection."""
    if not contents:
        return None, html.Div("No litho file loaded.",
                               style={"color": C["muted"], "fontSize": "10px"})

    df_litho, meta = parse_upload(contents, filename)

    if "error" in meta or df_litho is None:
        return None, html.Div(
            f"Error loading file: {meta.get('error', 'Unknown error')}",
            style={"color": C["danger"], "fontSize": "10px", "fontFamily": FONT},
        )

    # Flexible column resolution with alias matching
    df_litho, error = resolve_litho_columns(df_litho)

    if error:
        # Show the full informative error in a scrollable box
        return None, html.Div([
            html.Div("Could not identify lithology columns.",
                     style={"color": C["danger"], "fontSize": "11px",
                            "fontWeight": "700", "marginBottom": "6px",
                            "fontFamily": FONT}),
            html.Pre(error,
                     style={"color": C["muted"], "fontSize": "9px",
                            "fontFamily": FONT, "whiteSpace": "pre-wrap",
                            "maxHeight": "160px", "overflowY": "auto",
                            "background": C["bg"], "padding": "8px",
                            "borderRadius": "4px",
                            "border": f"1px solid {C['border']}"}),
        ])

    # Success badge showing unit count and depth range
    n_units     = len(df_litho)
    depth_range = (
        f"{df_litho['top_mbsf'].min():.1f} – "
        f"{df_litho['bottom_mbsf'].max():.1f} mbsf"
    )
    return df2j(df_litho), html.Div([
        html.Span(
            f"✓ {filename}",
            style={"background": C["border"], "padding": "3px 8px",
                   "borderRadius": "10px", "fontSize": "10px",
                   "color": C["accent2"], "fontFamily": FONT},
        ),
        html.Span(
            f"{n_units} units · {depth_range}",
            style={"color": C["muted"], "fontSize": "10px",
                   "fontFamily": FONT, "marginLeft": "6px"},
        ),
    ])


@app.callback(
    Output("meta-banner",     "children"),
    Input("store-site-info",  "data"),
    Input("meta-expedition",  "value"),
    Input("meta-site-hole",   "value"),
    Input("meta-lat",         "value"),
    Input("meta-lon",         "value"),
    Input("meta-water-depth", "value"),
    Input("meta-recovery",    "value"),
)
def update_meta_banner(site_info, expedition, site_hole, lat, lon, water_depth, recovery):
    """Re-render the metadata banner whenever values change."""
    if not site_info:
        return html.Div("Upload a file to see site metadata.",
                        style={"color": C["muted"], "fontSize": "11px",
                               "padding": "10px 20px", "fontFamily": FONT})
    manual = {
        "expedition":  expedition  or "",
        "site_hole":   site_hole   or "",
        "lat":         lat         or "",
        "lon":         lon         or "",
        "water_depth": water_depth or "",
        "recovery":    recovery    or "",
    }
    return build_metadata_bar(site_info, manual)


@app.callback(
    Output("x-col",        "options"),
    Output("y-col",        "options"),
    Output("color-col",    "options"),
    Output("depth-curves", "options"),
    Input("store-meta",    "data"),
)
def set_options(meta):
    """Populate all dropdowns from the uploaded file's columns."""
    if not meta or "columns" not in meta:
        return [], [], [], []
    cols = meta["columns"]
    num  = meta["numeric_cols"]
    col_opts   = [{"label": c, "value": c} for c in cols]
    color_opts = [{"label": "None", "value": "None"}] + col_opts
    num_opts   = [{"label": c, "value": c} for c in num]
    return col_opts, col_opts, color_opts, num_opts


@app.callback(
    Output("x-col",        "value"),
    Output("y-col",        "value"),
    Output("color-col",    "value"),
    Output("depth-curves", "value"),
    Input("store-meta",    "data"),
    prevent_initial_call=True,
)
def set_defaults(meta):
    """Auto-select sensible default columns on file load."""
    if not meta or "columns" not in meta:
        return None, None, "None", []
    cols = meta["columns"]
    num  = meta["numeric_cols"]
    x_val = cols[0] if cols else None
    y_val = cols[1] if len(cols) > 1 else (cols[0] if cols else None)
    curve_candidates = [c for c in num
                        if not re.search(r"comment", c, re.IGNORECASE)]
    curve_vals = curve_candidates[1:4] if len(curve_candidates) > 1 else curve_candidates[:1]
    return x_val, y_val, "None", curve_vals


@app.callback(
    Output("y-lbl",        "style"), Output("y-col",       "style"),
    Output("color-lbl",    "style"), Output("color-col",   "style"),
    Output("curves-lbl",   "style"), Output("depth-curves","style"),
    Input("chart-type",    "value"),
)
def toggle_controls(ctype):
    """Show/hide sidebar controls depending on the selected chart type."""
    show    = {**LBL};     hide    = {**LBL, "display": "none"}
    show_dd = {**DD};      hide_dd = {**DD,  "display": "none"}
    if ctype in ("histogram", "heatmap"):
        return hide, hide_dd, hide, hide_dd, hide, {"display": "none"}
    if ctype == "depthlog":
        return hide, hide_dd, hide, hide_dd, show, {}
    if ctype == "scatter":
        return show, show_dd, show, show_dd, hide, {"display": "none"}
    return show, show_dd, hide, hide_dd, hide, {"display": "none"}


@app.callback(
    Output("kpi-bar",   "children"),
    Input("store-df",   "data"),
    Input("store-meta", "data"),
)
def update_kpis(jdf, meta):
    """Render summary stat cards for the first 6 numeric columns."""
    if not jdf:
        return [html.Span("Upload a file to begin.",
                          style={"color": C["muted"], "fontSize": "12px"})]
    df = j2df(jdf)
    cards = []
    for col in meta.get("numeric_cols", [])[:6]:
        v = df[col].dropna()
        if not len(v): continue
        cards.append(html.Div([
            html.Div(col, style={"color": C["muted"], "fontSize": "9px",
                                  "letterSpacing": "1px"}),
            html.Div(f"{v.mean():.3g}", style={"color": C["text"], "fontSize": "17px",
                                                "fontWeight": "700"}),
            html.Div(f"min {v.min():.3g}  max {v.max():.3g}",
                     style={"color": C["muted"], "fontSize": "9px"}),
        ], style={**CARD, "minWidth": "110px", "padding": "8px 12px"}))
    return cards


@app.callback(
    Output("main-chart",  "figure"),
    Input("store-df",     "data"),
    Input("store-litho",  "data"),
    Input("chart-type",   "value"),
    Input("x-col",        "value"),
    Input("y-col",        "value"),
    Input("color-col",    "value"),
    Input("depth-curves", "value"),
    Input("overlay-opts", "value"),
)
def update_chart(jdf, jlitho, ctype, x, y, color, curves, overlays):
    """Rebuild the main chart whenever any input changes."""
    if not jdf:
        return empty_fig()
    overlays = overlays or []
    try:
        return make_chart(
            j2df(jdf), ctype, x, y, color, curves or [],
            litho_df=      j2df(jlitho) if jlitho else None,
            show_gaps=     ("gaps"      in overlays),
            show_qc=       ("qc"        in overlays),
            show_core_tops=("core_tops" in overlays),
        )
    except Exception as e:
        return empty_fig("Error: " + str(e), C["danger"])


@app.callback(
    Output("table-container", "children"),
    Output("row-count",        "children"),
    Input("store-df",          "data"),
)
def update_table(jdf):
    """Render a paginated, sortable, filterable data table."""
    if not jdf:
        return html.Div("No data loaded.", style={"color": C["muted"]}), ""
    df      = j2df(jdf)
    preview = df.head(200)
    tbl = dash_table.DataTable(
        data=preview.to_dict("records"),
        columns=[{"name": c, "id": c} for c in preview.columns],
        page_size=10, sort_action="native", filter_action="native",
        style_table={"overflowX": "auto"},
        style_header={"backgroundColor": C["bg"], "color": C["accent"],
                      "fontWeight": "700", "fontSize": "10px",
                      "border": f"1px solid {C['border']}"},
        style_cell={"backgroundColor": C["panel"], "color": C["text"],
                    "fontSize": "11px", "padding": "7px 11px",
                    "border": f"1px solid {C['border']}", "fontFamily": FONT,
                    "maxWidth": "160px", "overflow": "hidden",
                    "textOverflow": "ellipsis"},
        style_data_conditional=[
            {"if": {"row_index": "odd"}, "backgroundColor": C["bg"]},
            *[{"if": {"filter_query": f'{{{col}}} != ""', "column_id": col},
               "color": C["warn"]}
              for col in preview.columns
              if re.search(r"comment", col, re.IGNORECASE)],
        ],
    )
    return tbl, f"showing first 200 of {len(df):,} rows"


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Hugging Face Spaces expects the app on port 7860
    app.run(host="0.0.0.0", port=7860, debug=False)
