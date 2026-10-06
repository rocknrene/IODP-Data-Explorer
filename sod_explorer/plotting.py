"""Page layout of the Dash application.

The page has two tabs:

Legacy Data
    Retrieval of one dataset, or of two datasets (A and B) that are merged
    by depth, from public archives or files, with comparison plots.
Shipboard
    Exploration of a single uploaded file: summary statistics, scatter,
    line, histogram, correlation matrix, and multi-track depth log with an
    optional lithology track.

Explanatory text is placed behind an information button (ⓘ) beside each
section label, so that the sidebars show only the controls by default.
"""

from __future__ import annotations

from dash import dcc, html

from . import __version__
from .reference import ALL_EXPEDITIONS
from .sources.catalog import menu_options
from .theme import (
    CARD,
    CHECK_INPUT,
    CHECK_LABEL,
    DROPDOWN,
    FONT,
    HINT,
    INPUT,
    LABEL,
    button,
    css_variables,
)

ACCEPTED_DATA = ".csv  .tsv  .txt  .xlsx  .xls  .las  .zip"
ACCEPTED_LITHOLOGY = ".csv  .tsv  .xlsx  .zip"


def _css_block(theme: str) -> str:
    """CSS custom-property declarations of a theme, for the ``:root`` rule."""
    return "; ".join(f"{k}:{v}" for k, v in css_variables(theme).items())


INDEX_STRING = """<!DOCTYPE html>
<html>
<head>
{%metas%}
<title>SOD Explorer</title>
{%favicon%}
{%css%}
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Didact+Gothic&display=swap" rel="stylesheet">
<style>
  :root { """ + _css_block("dark") + """ }
  html, body, #react-entry-point { height:auto !important; min-height:100%; overflow-y:auto !important; }
  body { background:var(--bg) !important; color:var(--text) !important;
         font-family:""" + FONT + """; }
  .Select-menu-outer,.VirtualizedSelectOption,.Select-option
    { background-color:var(--dd-bg)!important; color:var(--text)!important; }
  .Select-option:hover,.Select-option.is-focused
    { background-color:var(--dd-hover)!important; color:var(--accent)!important; }
  .Select-value-label,.Select-placeholder,.Select--single .Select-value { color:var(--text)!important; }
  .Select-control { background-color:var(--dd-bg)!important; border-color:var(--border)!important;
                    color:var(--text)!important; }
  .Select-input input { color:var(--text)!important; background:transparent!important; }
  .Select-value { background-color:var(--dd-hover)!important; border-color:var(--accent)!important;
                  color:var(--text)!important; }
  .Select-arrow { border-top-color:var(--muted)!important; }
  .dash-dropdown, .dash-dropdown-content, .dash-dropdown-search
    { background-color:var(--dd-bg)!important; color:var(--text)!important; border-color:var(--border)!important; }
  .dash-dropdown-value, .dash-dropdown-content .dash-options-list-option { color:var(--text)!important; }
  .dash-dropdown-placeholder { color:var(--muted)!important; }
  .dash-dropdown-content { max-height:min(420px, 70vh)!important; }
  .dash-dropdown-content .dash-options-list-option:hover,
  .dash-dropdown-content .dash-options-list-option[data-highlighted],
  .dash-dropdown-content .dash-options-list-option.selected
    { background-color:var(--dd-hover)!important; color:var(--accent)!important; }
  .dash-spreadsheet-container .dash-spreadsheet-inner th
    { background-color:var(--bg)!important; color:var(--accent)!important; }
  .dash-spreadsheet-container .dash-spreadsheet-inner td
    { background-color:var(--panel)!important; color:var(--text)!important; }
  .tab { background-color:var(--panel)!important; color:var(--muted)!important; }
  .tab--selected { background-color:var(--bg)!important; color:var(--text)!important; }
  * { transition: background-color 0.25s, color 0.25s, border-color 0.25s; }
  .dash-spreadsheet-container { scrollbar-width:auto; scrollbar-color:var(--muted) var(--bg); }
  .dash-spreadsheet-container::-webkit-scrollbar { height:12px; width:12px; }
  .dash-spreadsheet-container::-webkit-scrollbar-track { background:var(--bg); }
  .dash-spreadsheet-container::-webkit-scrollbar-thumb { background:var(--muted); border-radius:6px;
                                                         border:2px solid var(--bg); }
  /* On a phone the chart toolbar would cover the chart title, and pinch and drag replace it. */
  @media (max-width: 700px) { .js-plotly-plot .modebar-container { display:none !important; } }
  .info-summary { list-style:none; }
  .info-summary::-webkit-details-marker { display:none; }
  .info-icon { font-size:13px; letter-spacing:0; color:var(--accent); padding-left:8px; }
  details[open] > .info-summary .info-icon { color:var(--text); }
</style>
</head>
<body>
{%app_entry%}
<footer>{%config%}{%scripts%}{%renderer%}</footer>
<script>
  // When a menu opens, place the cursor in its search box, so that typing filters the list
  // even if a value is already selected (the menu otherwise puts the cursor on that value).
  document.addEventListener("click", function (event) {
    if (!event.target.closest || !event.target.closest(".dash-dropdown")) { return; }
    setTimeout(function () {
      var box = document.querySelector(".dash-dropdown-content input.dash-dropdown-search");
      if (box && document.activeElement !== box) { box.focus(); box.select(); }
    }, 60);
  });
</script>
</body>
</html>"""

TAB_STYLE = {"backgroundColor": "var(--panel)", "color": "var(--muted)",
             "border": "1px solid var(--border)", "borderBottom": "none",
             "fontFamily": FONT, "fontSize": "13px", "padding": "8px 20px"}
TAB_SELECTED = {**TAB_STYLE, "backgroundColor": "var(--bg)", "color": "var(--text)",
                "borderBottom": "1px solid var(--bg)", "fontWeight": "600"}
DIVIDER = {"borderColor": "var(--border)", "margin": "14px 0"}
CONTROL_ITEM = {"flex": "1 1 180px", "minWidth": "160px"}
CONTROL_LABEL = {**LABEL, "marginTop": "0"}
STATUS_BOX = {"flex": "1", "background": "var(--panel)", "border": "1px solid var(--border)",
              "borderRadius": "6px", "padding": "10px 14px", "fontSize": "12px",
              "color": "var(--muted)", "minWidth": "200px"}


def upload_dropzone(component_id: str, label: str, accepted: str, compact: bool = False,
                    accent: str = "var(--accent)") -> dcc.Upload:
    """File drop zone. ``compact`` selects the narrower style of the dataset panels."""
    caption = html.Div(f"Accepted: {accepted}",
                       style={"color": "var(--muted)", "fontSize": "9px", "marginTop": "2px"})
    if compact:
        return dcc.Upload(
            id=component_id, multiple=False,
            children=html.Div([html.Div(label, style={"color": "var(--muted)", "fontSize": "11px"}),
                               caption], style={"padding": "10px 0", "textAlign": "center"}),
            style={"border": "1px dashed var(--border)", "borderRadius": "6px",
                   "backgroundColor": "var(--bg)", "cursor": "pointer", "marginTop": "6px"})
    return dcc.Upload(
        id=component_id, multiple=False,
        children=html.Div([html.Div("↑", style={"fontSize": "22px", "color": accent}),
                           html.Div(label), caption],
                          style={"textAlign": "center", "color": "var(--text)", "fontSize": "12px"}),
        style={"border": "2px dashed var(--border)", "borderRadius": "8px", "padding": "12px",
               "cursor": "pointer", "marginBottom": "6px"})


def section_label(text: str, info: str | None = None, component_id: str | None = None,
                  **style) -> html.Component:
    """Sidebar section label, optionally with an information button (ⓘ).

    With ``info``, the label is the summary of an HTML ``<details>`` element:
    clicking it reveals the explanation below, and hovering over it shows the
    same text as a tooltip.
    """
    label_style = {**LABEL, **style}
    if info is None:
        return html.P(text, id=component_id, style=label_style) if component_id else html.P(text, style=label_style)
    summary = html.Summary(
        [html.Span(text, id=component_id) if component_id else html.Span(text),
         html.Span("ⓘ", className="info-icon")],
        className="info-summary", title=info,
        style={**label_style, "display": "flex", "justifyContent": "space-between",
               "alignItems": "center", "cursor": "pointer"})
    return html.Details([
        summary,
        html.Div(info, style={**HINT, "background": "var(--bg)", "border": "1px solid var(--border)",
                              "borderRadius": "4px", "padding": "6px 8px", "marginTop": "4px"}),
    ])


# ---------------------------------------------------------------------------
# Shipboard tab
# ---------------------------------------------------------------------------

def _metadata_inputs():
    """Text inputs for the optional site metadata shown in the banner."""
    fields = [
        ("EXPEDITION", "meta-expedition", "e.g. 405"),
        ("SITE / HOLE", "meta-site-hole", "e.g. C0019J"),
        ("LATITUDE", "meta-lat", "decimal degrees, e.g. 37.95"),
        ("LONGITUDE", "meta-lon", "decimal degrees, e.g. 143.91"),
        ("WATER DEPTH (m)", "meta-water-depth", "e.g. 6897"),
        ("CORE RECOVERY (%)", "meta-recovery", "e.g. 68.4"),
    ]
    return [html.Div([html.Div(label, style={**LABEL, "marginTop": "6px"}),
                      dcc.Input(id=fid, type="text", placeholder=ph, debounce=True, style=INPUT)])
            for label, fid, ph in fields]


def shipboard_sidebar() -> html.Div:
    """Controls of the Shipboard tab: file uploads, site metadata, chart type, and chart options."""
    return html.Div([
        html.P("DATA FILE", style=LABEL),
        upload_dropzone("upload", "Drop file or click to upload", ACCEPTED_DATA),
        html.Div(id="upload-status", style={"fontSize": "10px", "marginBottom": "6px"}),

        section_label("LITHOLOGY TRACK (optional)",
                      "A separate table of intervals with top depth, bottom depth, and lithology "
                      "columns. It is drawn as a colored track beside the depth log and does not "
                      "replace the data file. Its depths must be on the same scale as the data."),
        upload_dropzone("upload-litho", "Drop lithology file or click", ACCEPTED_LITHOLOGY,
                        accent="var(--accent3)"),
        html.Div(id="litho-badge"),

        html.Hr(style=DIVIDER),
        section_label("SITE METADATA (optional)",
                      "Detected from the file where possible. Entries here override detected values "
                      "in the banner only; they are not written to exports."),
        *_metadata_inputs(),

        html.Hr(style=DIVIDER),
        html.P("CHART TYPE", style=LABEL),
        dcc.RadioItems(id="chart-type", value="depthlog", labelStyle=CHECK_LABEL, inputStyle=CHECK_INPUT,
                       options=[{"label": " Depth log", "value": "depthlog"},
                                {"label": " Scatter", "value": "scatter"},
                                {"label": " Line", "value": "line"},
                                {"label": " Histogram", "value": "histogram"},
                                {"label": " Correlation matrix", "value": "heatmap"}]),

        html.P("DEPTH COLUMN", id="depth-lbl", style=LABEL),
        dcc.Dropdown(id="depth-col", placeholder="Select depth column...", style=DROPDOWN),
        html.P("X AXIS", id="x-lbl", style=LABEL),
        dcc.Dropdown(id="x-col", placeholder="Select column...", style=DROPDOWN),
        html.P("Y AXIS", id="y-lbl", style=LABEL),
        dcc.Dropdown(id="y-col", placeholder="Select column...", style=DROPDOWN),
        html.P("COLOR BY", id="color-lbl", style=LABEL),
        dcc.Dropdown(id="color-col", placeholder="None", style=DROPDOWN),
        html.P("CURVES", id="curves-lbl", style=LABEL),
        dcc.Checklist(id="depth-curves", options=[], value=[], labelStyle=CHECK_LABEL,
                      inputStyle=CHECK_INPUT),

        html.Div(id="depthlog-options", children=[
            html.Hr(style=DIVIDER),
            section_label("DEPTH LOG OVERLAYS",
                          "Sampling gap: an interval wider than the threshold between consecutive "
                          "samples of the same hole; it marks missing measurements, not core recovery. "
                          "Samples with comments: rows whose comment field is not empty; comments record "
                          "any analyst note and do not necessarily indicate a bad measurement. Core top: "
                          "the shallowest sampled depth in each core."),
            dcc.Checklist(id="overlay-opts", value=["gaps", "flags", "core_tops"],
                          labelStyle=CHECK_LABEL, inputStyle=CHECK_INPUT,
                          options=[{"label": " Sampling gaps", "value": "gaps"},
                                   {"label": " Samples with comments", "value": "flags"},
                                   {"label": " Core tops (shallowest sample)", "value": "core_tops"}]),
            html.Div("Gap threshold (m)", style={**LABEL, "marginTop": "4px"}),
            dcc.Input(id="gap-threshold", type="number", value=5.0, min=0.01, step="any", style=INPUT),
        ]),

        html.Div(id="axis-options", children=[
            html.Hr(style=DIVIDER),
            html.P("AXIS OPTIONS", style=LABEL),
            dcc.Checklist(id="axis-opts", value=["invert_y"], labelStyle=CHECK_LABEL,
                          inputStyle=CHECK_INPUT,
                          options=[{"label": " Reverse Y axis (depth increasing downward)",
                                    "value": "invert_y"}]),
        ]),
    ], style={"width": "250px", "minWidth": "250px", "background": "var(--panel)",
              "borderRight": "1px solid var(--border)", "padding": "18px"})


def shipboard_tab() -> html.Div:
    """Shipboard tab: metadata banner, sidebar, summary cards, chart, and data table."""
    return html.Div([
        html.Div(id="meta-banner"),
        html.Div([
            shipboard_sidebar(),
            html.Div([
                html.Div(id="kpi-bar", style={"display": "flex", "gap": "10px", "padding": "10px 18px",
                                              "borderBottom": "1px solid var(--border)",
                                              "flexWrap": "wrap"}),
                html.Div(dcc.Graph(id="main-chart", config={"displayModeBar": True, "scrollZoom": True}),
                         style={"padding": "10px 18px"}),
                html.Div([
                    html.Div([
                        html.Span("DATA TABLE", style={"color": "var(--muted)", "fontSize": "10px",
                                                       "letterSpacing": "2px"}),
                        html.Span(id="row-count", style={"color": "var(--accent)", "fontSize": "11px",
                                                         "marginLeft": "12px"}),
                    ], style={"marginBottom": "8px"}),
                    html.Div(id="table-container"),
                ], style={**CARD, "margin": "0 18px 18px 18px"}),
            ], style={"flex": "1", "minWidth": "320px"}),
        ], style={"display": "flex", "flexWrap": "wrap", "flex": "1"}),
    ], style={"display": "flex", "flexDirection": "column", "flex": "1"})


# ---------------------------------------------------------------------------
# Legacy Data tab
# ---------------------------------------------------------------------------

_SOURCE_NOTE = (
    "Select a Leg or Expedition; the archive is chosen automatically. DSDP Legs are retrieved "
    "from the DSDP Data Access application (or PANGAEA for a few data types); JOIDES Resolution "
    "expeditions from LORE (LIMS data, Expedition 317 onward); Chikyu and Mission-Specific "
    "Platform expeditions from PANGAEA. ODP Legs are retrieved from the NOAA NCEI archive, one "
    "Hole at a time; IODP Expeditions 301 to 312 may require file upload. Report types use one "
    "generic name across programs; a "
    "type with no DSDP equivalent returns no DSDP data."
)


def _report_options() -> list[dict]:
    """Report-type options with the category headings styled as small labels."""
    heading = {"color": "var(--accent)", "fontSize": "10px", "letterSpacing": "2px", "fontWeight": "700"}
    return [{**o, "label": html.Span(o["label"], style=heading), "search": o["label"]}
            if o.get("disabled") else o for o in menu_options()]


def dataset_panel(ds: str) -> html.Div:
    """Retrieval controls for dataset ``"a"`` or ``"b"``."""
    accent = "var(--accent)" if ds == "a" else "var(--accent3)"
    return html.Div([
        section_label(f"DATASET {ds.upper()}", _SOURCE_NOTE, component_id=f"pe-{ds}-label",
                      color=accent, marginTop="0"),
        dcc.RadioItems(id=f"pe-{ds}-source", value="database",
                       options=[{"label": " Public archive", "value": "database"},
                                {"label": " Local file upload", "value": "upload"}],
                       labelStyle={"display": "block", "color": "var(--muted)", "fontSize": "11px",
                                   "marginBottom": "3px"},
                       inputStyle={"marginRight": "6px", "accentColor": accent}),
        html.Div(id=f"pe-{ds}-upload-panel", style={"display": "none"}, children=[
            upload_dropzone(f"pe-{ds}-upload", "Drop file or click to upload", ACCEPTED_DATA, compact=True),
            html.Div(id=f"pe-{ds}-upload-status", style={"fontSize": "10px", "marginTop": "4px"}),
        ]),
        html.Div(id=f"pe-{ds}-database-panel", children=[
            dcc.Dropdown(id=f"pe-{ds}-exp", options=[{"label": e, "value": e} for e in ALL_EXPEDITIONS],
                         placeholder="Leg / Expedition", style=DROPDOWN),
            dcc.Dropdown(id=f"pe-{ds}-site", placeholder="Site (optional; all if blank)",
                         style={**DROPDOWN, "marginTop": "4px"}),
            dcc.Dropdown(id=f"pe-{ds}-hole", placeholder="Hole (optional; all if blank)",
                         style={**DROPDOWN, "marginTop": "4px"}),
            html.P("REPORT TYPE", style={**LABEL, "marginTop": "10px"}),
            dcc.Dropdown(id=f"pe-{ds}-report", placeholder="Select report...", style=DROPDOWN,
                         options=_report_options()),
            html.Button(f"Fetch {ds.upper()}", id=f"pe-{ds}-fetch", n_clicks=0, style=button(accent)),
            dcc.Loading(html.Div(id=f"pe-{ds}-db-status", style={"fontSize": "10px", "marginTop": "4px"}),
                        type="dot", color="var(--accent)"),
            html.Div(id=f"pe-{ds}-db-results", style={"marginTop": "6px"}),
        ]),
    ], style={"borderBottom": "1px solid var(--border)", "paddingBottom": "12px", "marginBottom": "12px"})


_VIEW_NOTE = (
    "Single dataset: load one dataset from an archive or a file and plot it. Merge two datasets: "
    "load datasets A and B, pair their samples by depth, and compare them."
)
_MERGE_NOTE = (
    "Each sample of A is paired with the nearest sample of B in the same hole (the same site when "
    "both depth columns are on a composite scale) within the tolerance. Choose the depth column of "
    "each dataset above the chart before merging."
)
_CHART_NOTE = (
    "The cross-plot and dual-axis overlay compare two columns: the first two selected columns in "
    "single-dataset view, or the first column of A and of B in merged view."
)
_DETREND_NOTE = (
    "Properties that both change with depth (for example, through compaction) correlate through "
    "the shared trend. Removing each trend, per hole, tests whether the deviations co-vary."
)
_WINDOW_NOTE = (
    "Centered mean over a fixed depth interval, computed separately for each hole. Windows with "
    "fewer than three samples are left blank."
)


def legacy_sidebar() -> html.Div:
    """Controls of the Legacy Data tab: view, dataset panels, merge settings, and chart mode."""
    return html.Div([
        section_label("VIEW", _VIEW_NOTE, marginTop="0"),
        dcc.RadioItems(id="pe-view-mode", value="a", labelStyle=CHECK_LABEL, inputStyle=CHECK_INPUT,
                       options=[{"label": " Single dataset", "value": "a"},
                                {"label": " Merge two datasets", "value": "merged"}]),
        html.Hr(style={**DIVIDER, "margin": "10px 0"}),
        dataset_panel("a"),
        html.Div(id="pe-merge-section", style={"display": "none"}, children=[
            dataset_panel("b"),
            section_label("DEPTH MERGE", _MERGE_NOTE, marginTop="0"),
            html.Div("Tolerance (cm)", style={**LABEL, "marginTop": "4px"}),
            dcc.Input(id="pe-tolerance", value=2, type="number", min=0, max=500, step="any", style=INPUT),
            dcc.Checklist(id="pe-merge-opts", value=["one_to_one"],
                          labelStyle={**CHECK_LABEL, "marginTop": "8px"}, inputStyle=CHECK_INPUT,
                          options=[{"label": " Use each B sample at most once", "value": "one_to_one"},
                                   {"label": " Allow different depth scales", "value": "mixed_scales"},
                                   {"label": " Pair across different Sites/Holes (depth only)",
                                    "value": "across_holes"}]),
            html.Button("Merge datasets", id="pe-merge-btn", n_clicks=0,
                        style={**button("var(--accent2)"), "fontSize": "12px"}),
        ]),

        html.Hr(style={**DIVIDER, "margin": "10px 0"}),
        section_label("CHART", _CHART_NOTE),
        dcc.RadioItems(id="pe-chart-mode", value="tracks", labelStyle=CHECK_LABEL, inputStyle=CHECK_INPUT,
                       options=[{"label": " Depth tracks", "value": "tracks"},
                                {"label": " Cross-plot with correlation", "value": "scatter"},
                                {"label": " Dual-axis overlay", "value": "dual"},
                                {"label": " Depth-window mean", "value": "rolling"}]),
        html.Div(id="pe-detrend-ctrl", style={"display": "none"}, children=[
            section_label("TREND", _DETREND_NOTE, marginTop="8px"),
            dcc.Checklist(id="pe-detrend", value=[], labelStyle=CHECK_LABEL, inputStyle=CHECK_INPUT,
                          options=[{"label": " Remove linear depth trend first", "value": "detrend"}]),
        ]),
        html.Div(id="pe-rolling-ctrl", style={"display": "none"}, children=[
            section_label("WINDOW (m)", _WINDOW_NOTE, marginTop="8px"),
            dcc.Input(id="pe-rolling-window", value=3, type="number", min=0.01, step="any", style=INPUT),
        ]),
    ], style={"width": "270px", "minWidth": "270px", "background": "var(--panel)",
              "borderRight": "1px solid var(--border)", "padding": "18px"})


def legacy_tab() -> html.Div:
    """Legacy Data tab: sidebar, dataset status, Expedition filter, column pickers, chart, and table."""
    return html.Div([
        legacy_sidebar(),
        html.Div([
            html.Div([html.Div(id="pe-status-a", style={**STATUS_BOX, "marginRight": "8px"}),
                      html.Div(id="pe-status-b", style={**STATUS_BOX, "marginRight": "8px", "display": "none"}),
                      html.Div(id="pe-status-merged", style={**STATUS_BOX, "display": "none"})],
                     style={"display": "flex", "flexWrap": "wrap", "gap": "4px", "marginBottom": "12px"}),
            html.Div([
                html.Div([
                    html.Span("EXPEDITIONS SHOWN", style={"color": "var(--text)", "fontSize": "11px",
                                                         "letterSpacing": "1px", "fontWeight": "600"}),
                    html.Button("All / None", id="pe-exp-all-none", n_clicks=0,
                                style={"backgroundColor": "var(--border)", "color": "var(--text)",
                                       "border": "none", "borderRadius": "4px", "padding": "3px 10px",
                                       "cursor": "pointer", "fontSize": "10px", "marginLeft": "12px"}),
                    html.Button("Download data + provenance (ZIP)", id="pe-download-btn", n_clicks=0,
                                style={"backgroundColor": "var(--panel)", "color": "var(--accent)",
                                       "border": "1px solid var(--border)", "borderRadius": "4px",
                                       "padding": "3px 12px", "cursor": "pointer", "fontSize": "10px",
                                       "marginLeft": "auto"}),
                    dcc.Download(id="pe-download"),
                ], style={"display": "flex", "alignItems": "center", "marginBottom": "8px"}),
                dcc.Checklist(id="pe-exp-filter", options=[], value=[],
                              labelStyle={"display": "inline-block", "margin": "3px 8px 3px 0",
                                          "color": "var(--muted)", "fontSize": "11px"}),
                html.Div(id="pe-exp-hint", style={**HINT, "fontStyle": "italic"}),
            ], style={**CARD, "marginBottom": "12px"}),
            html.Div([
                html.Div([html.P("MERGE DEPTH, A", style=CONTROL_LABEL),
                          dcc.Dropdown(id="pe-depth-a", options=[], placeholder="auto-detect",
                                       style=DROPDOWN)], id="pe-depth-a-container",
                         style={**CONTROL_ITEM, "display": "none"}),
                html.Div([html.P("MERGE DEPTH, B", style=CONTROL_LABEL),
                          dcc.Dropdown(id="pe-depth-b", options=[], placeholder="auto-detect",
                                       style=DROPDOWN)], id="pe-depth-b-container",
                         style={**CONTROL_ITEM, "display": "none"}),
                html.Div([html.P("DEPTH AXIS", style=CONTROL_LABEL),
                          dcc.Dropdown(id="pe-xaxis", options=[], style=DROPDOWN)], style=CONTROL_ITEM),
                html.Div([html.P("COLUMNS", id="pe-yaxis-lbl", style=CONTROL_LABEL),
                          dcc.Dropdown(id="pe-yaxis", options=[], multi=True, style=DROPDOWN)],
                         style={**CONTROL_ITEM, "flex": "2 1 240px"}),
                html.Div(id="pe-ycols-b-container", children=[
                    html.P("DATASET B COLUMNS", style=CONTROL_LABEL),
                    dcc.Dropdown(id="pe-ycols-b", options=[], multi=True, style=DROPDOWN)],
                    style={"display": "none"}),
            ], style={**CARD, "display": "flex", "flexWrap": "wrap", "gap": "12px",
                      "alignItems": "flex-end", "marginBottom": "12px"}),
            dcc.Graph(id="pe-chart", config={"displayModeBar": True, "scrollZoom": True}),
            html.Div(id="pe-table-container", style={"marginTop": "16px"}),
        ], style={"flex": "1", "padding": "16px", "minWidth": "320px"}),
    ], style={"display": "flex", "flexWrap": "wrap", "flex": "1"})


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

def build_layout() -> html.Div:
    """Complete page layout. Both tabs are rendered once and shown or hidden."""
    header = html.Div([
        html.Div([
            html.Div([html.Span("SOD", style={"fontWeight": "700", "color": "var(--accent)"}),
                      html.Span(" Explorer", style={"fontWeight": "300", "color": "var(--text)"}),
                      html.Span(f"  v{__version__}", style={"color": "var(--muted)", "fontSize": "10px"})],
                     style={"fontSize": "17px", "fontFamily": FONT}),
            html.Div("Scientific Ocean Drilling · Data Visualization Tool",
                     style={"color": "var(--muted)", "fontSize": "11px", "fontFamily": FONT}),
        ]),
        html.Button(id="theme-toggle", n_clicks=0, children="Light mode",
                    style={"backgroundColor": "transparent", "border": "1px solid var(--border)",
                           "borderRadius": "6px", "color": "var(--muted)", "cursor": "pointer",
                           "fontSize": "11px", "fontFamily": FONT, "padding": "5px 12px"}),
    ], style={"display": "flex", "justifyContent": "space-between", "alignItems": "center",
              "padding": "8px 20px", "background": "var(--panel)", "borderBottom": "1px solid var(--border)"})

    return html.Div([
        header,
        dcc.Tabs(id="main-tabs", value="legacy", style={"fontFamily": FONT}, children=[
            dcc.Tab(label="Legacy Data", value="legacy", style=TAB_STYLE, selected_style=TAB_SELECTED),
            dcc.Tab(label="Shipboard", value="shipboard", style=TAB_STYLE, selected_style=TAB_SELECTED),
        ]),
        html.Div(legacy_tab(), id="legacy-container"),
        html.Div(shipboard_tab(), id="shipboard-container", style={"display": "none"}),

        dcc.Store(id="theme-store", storage_type="local", data="dark"),
        dcc.Store(id="store-df"), dcc.Store(id="store-meta"), dcc.Store(id="store-litho"),
        dcc.Store(id="store-site-info"),
        dcc.Store(id="pe-store-a"), dcc.Store(id="pe-store-b"), dcc.Store(id="pe-merged-store"),
        dcc.Store(id="pe-prov-a"), dcc.Store(id="pe-prov-b"), dcc.Store(id="pe-prov-merged"),
    ], style={"minHeight": "100vh", "display": "flex", "flexDirection": "column",
              "background": "var(--bg)", "color": "var(--text)", "fontFamily": FONT})
