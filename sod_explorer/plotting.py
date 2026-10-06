"""Figure builders for the Legacy Data and Shipboard views.

All builders are pure functions of a table and display options and return
a :class:`plotly.graph_objects.Figure`.

Conventions
-----------
* Depth increases downward on every depth axis.
* Axis titles use the depth column header, which names the depth scale
  (for example "Depth CSF-A (m)"); no scale is assumed.
* When a table contains several Holes, each Hole is drawn as a separate
  line segment, so that lines never connect samples from different Holes.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .analysis import (
    comment_flagged,
    core_tops,
    correlate,
    correlation_matrix,
    depth_window_mean,
    detrend_by_depth,
    find_sampling_gaps,
)
from .columns import canonical_scale, depth_scale, hole_key_columns, measurement_columns
from .theme import FONT, lithology_color, palette, series_colors

MAX_HEATMAP_COLUMNS = 30


# ---------------------------------------------------------------------------
# Common layout
# ---------------------------------------------------------------------------

def base_layout(theme: str = "dark", **overrides) -> dict:
    """Plotly layout settings for a theme."""
    t = palette(theme)
    layout = dict(
        paper_bgcolor=t["panel"], plot_bgcolor=t["bg"],
        font=dict(color=t["text"], family=FONT),
        colorway=series_colors(theme),
        margin=dict(l=60, r=20, t=50, b=50),
    )
    layout.update(overrides)
    return layout


def _style_axes(fig: go.Figure, theme: str) -> go.Figure:
    """Apply the theme's grid and axis-line colors to every axis of a figure."""
    t = palette(theme)
    fig.update_xaxes(gridcolor=t["border"], zerolinecolor=t["border"], linecolor=t["border"])
    fig.update_yaxes(gridcolor=t["border"], zerolinecolor=t["border"], linecolor=t["border"])
    return fig


def empty_figure(message: str = "Upload a file to begin", theme: str = "dark",
                 color: str | None = None) -> go.Figure:
    """Blank figure displaying a message."""
    t = palette(theme)
    fig = go.Figure()
    fig.update_layout(**base_layout(theme), xaxis=dict(visible=False), yaxis=dict(visible=False),
                      annotations=[dict(text=message, xref="paper", yref="paper", x=0.5, y=0.5,
                                        showarrow=False, font=dict(color=color or t["muted"], size=14))])
    return fig


def hole_labels(df: pd.DataFrame) -> pd.Series | None:
    """Site+Hole label per row when a table spans several Holes, else ``None``."""
    keys = hole_key_columns(df)
    if not keys.complete:
        return None
    labels = df[keys.site].astype(str).str.strip() + df[keys.hole].astype(str).str.strip()
    return labels if labels.nunique() > 1 else None


def _segments(df: pd.DataFrame, labels: pd.Series | None):
    """Yield ``(hole_label, sub_table)`` pairs; one pair if holes are not distinguished."""
    if labels is None:
        yield "", df
        return
    for label in pd.unique(labels):
        yield str(label), df[labels == label]


# ---------------------------------------------------------------------------
# Shipboard view
# ---------------------------------------------------------------------------

def scatter_figure(df, x, y, color, theme="dark", invert_y=False) -> go.Figure:
    """Scatter plot of two columns, optionally colored by a third."""
    color = None if color in (None, "", "None") else color
    fig = px.scatter(df, x=x, y=y, color=color, opacity=0.75)
    fig.update_traces(marker=dict(size=5))
    fig.update_layout(**base_layout(theme))
    if invert_y:
        fig.update_yaxes(autorange="reversed")
    return _style_axes(fig, theme)


def line_figure(df, x, y, theme="dark", invert_y=False) -> go.Figure:
    """Line plot of y against x, sorted by x, with one line per Hole."""
    labels = hole_labels(df)
    fig = go.Figure()
    for hole, part in _segments(df, labels):
        part = part[[x, y]].dropna().sort_values(x)
        fig.add_trace(go.Scatter(x=part[x], y=part[y], mode="lines", name=hole or y,
                                 showlegend=bool(hole)))
    fig.update_layout(**base_layout(theme), xaxis_title=x, yaxis_title=y)
    if invert_y:
        fig.update_yaxes(autorange="reversed")
    return _style_axes(fig, theme)


def histogram_figure(df, x, theme="dark", bins=40) -> go.Figure:
    """Histogram of one column with a fixed number of bins."""
    fig = px.histogram(df, x=x, nbins=bins, color_discrete_sequence=[palette(theme)["accent"]])
    fig.update_layout(**base_layout(theme), yaxis_title="count")
    return _style_axes(fig, theme)


def heatmap_figure(df, theme="dark", min_pairs=10) -> go.Figure:
    """Pearson correlation matrix of the measurement columns.

    Depth and identifier columns (Core, Section, offsets, sample IDs) are
    excluded. Each coefficient uses the rows complete for that pair; pairs
    with fewer than ``min_pairs`` complete rows are left blank. The matrix is
    descriptive: coefficients are not tested for significance.
    """
    columns = measurement_columns(df)[:MAX_HEATMAP_COLUMNS]
    if len(columns) < 2:
        return empty_figure("At least two measurement columns are required for a correlation matrix",
                            theme)
    corr = correlation_matrix(df, columns, min_pairs=min_pairs).round(2)
    fig = px.imshow(corr, text_auto=True, aspect="auto", color_continuous_scale="RdBu_r",
                    zmin=-1, zmax=1)
    title = f"Pearson r (pairwise complete, n ≥ {min_pairs}; not tested for significance)"
    fig.update_layout(**base_layout(theme), height=480, title=dict(text=title, font=dict(size=11)))
    return fig


def _axis_refs(column: int) -> tuple[str, str]:
    """Plotly axis identifiers (``"x2"``, ``"y2"``) of a subplot column."""
    suffix = str(column) if column > 1 else ""
    return f"x{suffix}", f"y{suffix}"


def _lithology_track(lithology: pd.DataFrame, column: int) -> tuple[list[dict], list[dict]]:
    """Shapes and labels of the lithology track in a subplot column."""
    x_axis, y_axis = _axis_refs(column)
    shapes, labels = [], []
    for row in lithology.itertuples(index=False):
        shapes.append(dict(type="rect", xref=f"{x_axis} domain", yref=y_axis, x0=0, x1=1,
                           y0=row.top_depth, y1=row.bottom_depth, line_width=0, opacity=0.8,
                           fillcolor=lithology_color(row.lithology)))
        labels.append(dict(xref=f"{x_axis} domain", yref=y_axis, x=0.5,
                           y=(row.top_depth + row.bottom_depth) / 2, text=str(row.lithology)[:6],
                           showarrow=False, textangle=-90, font=dict(size=7, color="#ffffff")))
    return shapes, labels


def depth_log_figure(df, depth, curves, lithology=None, lithology_scale=None,
                     show_gaps=True, show_flags=True, show_core_tops=True,
                     gap_threshold_m=5.0, theme="dark") -> go.Figure:
    """Multi-track depth log with optional lithology and overlays.

    Parameters
    ----------
    df
        Data table.
    depth
        Depth column (shared vertical axis).
    curves
        Columns drawn as separate tracks.
    lithology
        Optional interval table with ``top_depth``, ``bottom_depth``,
        ``lithology``.
    lithology_scale
        Depth scale of the lithology table. A mismatch with the data depth
        scale is reported in the figure title.
    show_gaps
        Shade sampling gaps wider than ``gap_threshold_m`` (per Hole).
    show_flags
        Mark samples with a non-empty comment field.
    show_core_tops
        Mark the shallowest sampled depth of each core.
    """
    t = palette(theme)
    curves = [c for c in curves if c in df.columns]
    if not curves:
        return empty_figure("Select one or more curves", theme)
    has_lithology = lithology is not None and len(lithology) > 0
    titles = (["Lithology"] if has_lithology else []) + curves
    widths = [0.07 if name == "Lithology" else 1.0 for name in titles]
    fig = make_subplots(rows=1, cols=len(titles), shared_yaxes=True, subplot_titles=titles,
                        column_widths=[w / sum(widths) for w in widths], horizontal_spacing=0.012)
    # Shapes and annotations are collected and assigned in one layout update;
    # adding them one at a time is quadratic in their number.
    shapes: list[dict] = []
    annotations: list[dict] = list(fig.layout.annotations)
    first = 1
    if has_lithology:
        litho_shapes, litho_labels = _lithology_track(lithology, 1)
        shapes += litho_shapes
        annotations += litho_labels
        # Invisible trace so the shared depth axis spans the lithology intervals.
        fig.add_trace(go.Scatter(x=[0.5, 0.5],
                                 y=[lithology["top_depth"].min(), lithology["bottom_depth"].max()],
                                 mode="markers", marker_opacity=0, showlegend=False, hoverinfo="skip"),
                      row=1, col=1)
        fig.update_xaxes(showticklabels=False, showgrid=False, row=1, col=1)
        first = 2

    colors = series_colors(theme)
    labels = hole_labels(df)
    gaps = find_sampling_gaps(df, depth, gap_threshold_m) if show_gaps else []
    flagged = comment_flagged(df) if show_flags else pd.Series(False, index=df.index)
    tops = core_tops(df, depth) if show_core_tops else []

    for i, curve in enumerate(curves):
        column = first + i
        x_axis, y_axis = _axis_refs(column)
        color = colors[i % len(colors)]
        for j, (hole, part) in enumerate(_segments(df, labels)):
            part = part[[depth, curve]].dropna().sort_values(depth)
            fig.add_trace(go.Scatter(
                x=part[curve], y=part[depth], mode="lines", name=curve, legendgroup=curve,
                showlegend=(j == 0), line=dict(color=color, width=1.4),
                hovertemplate=(f"{hole} " if hole else "") + "%{x:.4g} at %{y:.2f}<extra>" + curve + "</extra>",
            ), row=1, col=column)
        for hole, top, bottom in gaps:
            shapes.append(dict(type="rect", xref=f"{x_axis} domain", yref=y_axis, x0=0, x1=1,
                               y0=top, y1=bottom, fillcolor="#888780", opacity=0.15, line_width=0,
                               layer="below"))
            if i == 0:
                annotations.append(dict(xref=f"{x_axis} domain", yref=y_axis, x=0, y=top,
                                        text=f"gap {hole}".strip(), showarrow=False, xanchor="left",
                                        yanchor="top", font=dict(size=8, color=t["muted"])))
        marked = df.loc[flagged & df[curve].notna() & df[depth].notna(), [depth, curve]]
        if len(marked):
            fig.add_trace(go.Scatter(
                x=marked[curve], y=marked[depth], mode="markers", name="Comment on sample",
                legendgroup="comment_flags", showlegend=(i == 0),
                marker=dict(symbol="circle-open", size=8, color=t["danger"], line_width=1.5),
                hovertemplate="%{y:.2f}: sample has a comment<extra></extra>"), row=1, col=column)
        if i == 0 and tops:
            values = df[curve].dropna()
            if len(values):
                x0 = float(values.min())
                x1 = x0 + 0.12 * ((float(values.max()) - x0) or 1.0)
                for label, top in tops:
                    shapes.append(dict(type="line", xref=x_axis, yref=y_axis, x0=x0, x1=x1, y0=top, y1=top,
                                       line=dict(color=t["warn"], width=0.8, dash="dot")))
                    annotations.append(dict(xref=x_axis, yref=y_axis, x=x1, y=top, text=label,
                                            showarrow=False, xanchor="left",
                                            font=dict(size=7, color=t["warn"])))
    fig.update_layout(shapes=shapes, annotations=annotations)

    title = None
    if has_lithology and lithology_scale:
        data_scale = depth_scale(depth)
        if data_scale and canonical_scale(data_scale) != canonical_scale(lithology_scale):
            title = (f"Warning: lithology depths are on {lithology_scale}, "
                     f"data depths are on {data_scale}")
    fig.update_yaxes(autorange="reversed", title_text=depth, row=1, col=1)
    fig.update_layout(**base_layout(theme), height=580, showlegend=True,
                      legend=dict(x=1.01, y=1, font=dict(size=10)),
                      title=dict(text=title, font=dict(size=11, color=t["warn"])) if title else None)
    return _style_axes(fig, theme)


# ---------------------------------------------------------------------------
# Legacy Data view
# ---------------------------------------------------------------------------

def _track_colors(theme, columns_a, columns_b):
    """Pair each selected column with a color: blue-violet hues for dataset A,
    warm hues for dataset B."""
    t = palette(theme)
    colors_a = [t["accent"], "#bc8cff", "#ff7b72"]
    colors_b = [t["accent3"], t["accent2"], "#f0883e"]
    return ([(c, colors_a[i % len(colors_a)]) for i, c in enumerate(columns_a)]
            + [(c, colors_b[i % len(colors_b)]) for i, c in enumerate(columns_b)])


def tracks_figure(df, depth, columns_a, columns_b, theme="dark") -> go.Figure:
    """One depth track per selected column, sharing a depth axis."""
    items = _track_colors(theme, columns_a, columns_b)
    fig = make_subplots(rows=1, cols=len(items), shared_yaxes=True, horizontal_spacing=0.03)
    labels = hole_labels(df)
    for i, (column, color) in enumerate(items):
        for hole, part in _segments(df, labels):
            part = part[[depth, column]].dropna().sort_values(depth)
            fig.add_trace(go.Scatter(x=part[column], y=part[depth], mode="lines", name=column,
                                     line=dict(color=color, width=1.4),
                                     hovertemplate=(f"{hole} " if hole else "")
                                     + "%{x:.4g} at %{y:.2f}<extra>" + column + "</extra>"),
                          row=1, col=i + 1)
        fig.update_xaxes(title_text=column, title_font=dict(size=10), row=1, col=i + 1)
    fig.update_yaxes(title_text=depth, autorange="reversed", row=1, col=1)
    fig.update_layout(**base_layout(theme), height=600, showlegend=False)
    return _style_axes(fig, theme)


def correlation_figure(df, depth, column_x, column_y, theme="dark", detrend=False) -> go.Figure:
    """Cross-plot of two columns colored by depth, with OLS fit and statistics.

    Statistics are computed by :func:`sod_explorer.analysis.correlate`,
    which adjusts the significance test for serial correlation. With
    ``detrend``, the linear depth trend of each column is removed (within
    each Hole) and the residuals are plotted and correlated.
    """
    t = palette(theme)
    sub = df[[depth, column_x, column_y]].copy()
    labels = hole_labels(df)
    groups = labels if labels is not None else pd.Series("", index=df.index)
    sub["_hole"] = groups.values
    sub = sub.dropna(subset=[depth, column_x, column_y])
    if len(sub) < 3:
        return empty_figure("Fewer than three depth-matched pairs; nothing to correlate", theme)
    x_label, y_label = column_x, column_y
    if detrend:
        sub[column_x] = detrend_by_depth(sub[column_x], sub[depth], sub["_hole"])
        sub[column_y] = detrend_by_depth(sub[column_y], sub[depth], sub["_hole"])
        sub = sub.dropna(subset=[column_x, column_y])
        x_label, y_label = f"{column_x}, residual from depth trend", f"{column_y}, residual from depth trend"
        if len(sub) < 3:
            return empty_figure("Too few samples per hole to remove a depth trend", theme)
    fig = go.Figure(go.Scatter(
        x=sub[column_x], y=sub[column_y], mode="markers", name="samples",
        marker=dict(color=sub[depth], colorscale="Viridis_r", size=5, opacity=0.75, showscale=True,
                    colorbar=dict(title=dict(text=depth, font=dict(color=t["muted"], size=10)),
                                  tickfont=dict(color=t["muted"]))),
        hovertemplate=(f"{x_label}: %{{x:.4g}}<br>{y_label}: %{{y:.4g}}"
                       "<br>depth: %{marker.color:.2f}<extra></extra>"),
    ))
    try:
        # Values are already detrended above, so correlate() is not asked to detrend again.
        result = correlate(sub[column_x], sub[column_y], sub[depth],
                           groups=sub["_hole"] if labels is not None else None)
        result.detrended = detrend
    except ValueError as exc:
        fig.update_layout(title=dict(text=str(exc), font=dict(size=11)))
    else:
        x_range = np.linspace(sub[column_x].min(), sub[column_x].max(), 200)
        fig.add_trace(go.Scatter(x=x_range, y=result.slope * x_range + result.intercept, mode="lines",
                                 name="OLS fit", line=dict(color=t["danger"], width=1.5, dash="dash")))
        fig.update_layout(title=dict(text=result.summary()
                                     + "<br><sup>p and CI use n_eff adjusted for lag-1 autocorrelation "
                                       "(Bretherton et al., 1999)</sup>", font=dict(size=11)))
    fig.update_layout(**base_layout(theme), height=600, xaxis_title=x_label, yaxis_title=y_label,
                      showlegend=True, legend=dict(orientation="h", y=-0.15))
    return _style_axes(fig, theme)


def dual_axis_figure(df, depth, column_a, column_b, theme="dark") -> go.Figure:
    """Two columns against depth (horizontal) on independent vertical axes."""
    t = palette(theme)
    labels = hole_labels(df)
    fig = go.Figure()
    for column, color, axis, dash in ((column_a, t["accent"], "y", "solid"),
                                      (column_b, t["accent3"], "y2", "dot")):
        for j, (_hole, part) in enumerate(_segments(df, labels)):
            part = part[[depth, column]].dropna().sort_values(depth)
            fig.add_trace(go.Scatter(x=part[depth], y=part[column], mode="lines", name=column,
                                     legendgroup=column, showlegend=(j == 0), yaxis=axis,
                                     line=dict(color=color, width=1.4, dash=dash)))
    fig.update_layout(**base_layout(theme), height=600,
                      xaxis=dict(title=depth), yaxis=dict(title=column_a, color=t["accent"]),
                      yaxis2=dict(title=column_b, color=t["accent3"], overlaying="y", side="right",
                                  showgrid=False),
                      legend=dict(bgcolor=t["panel"], bordercolor=t["border"], borderwidth=1))
    return _style_axes(fig, theme)


def smoothed_figure(df, depth, columns_a, columns_b, window_m, min_count=3, theme="dark") -> go.Figure:
    """Raw and depth-window-averaged tracks.

    Each column is smoothed with a centered mean over ``window_m`` of depth,
    computed separately for each Hole (see
    :func:`sod_explorer.analysis.depth_window_mean`). Windows with fewer
    than ``min_count`` samples are left blank.
    """
    t = palette(theme)
    items = _track_colors(theme, columns_a, columns_b)
    labels = hole_labels(df)
    fig = make_subplots(rows=1, cols=len(items), shared_yaxes=True, horizontal_spacing=0.03)
    for i, (column, color) in enumerate(items):
        for j, (_hole, part) in enumerate(_segments(df, labels)):
            part = part[[depth, column]].dropna().sort_values(depth)
            smooth = depth_window_mean(part[depth], part[column], window_m, min_count=min_count)
            fig.add_trace(go.Scatter(x=part[column], y=part[depth], mode="lines", showlegend=False,
                                     line=dict(color=color, width=0.6), opacity=0.35,
                                     hoverinfo="skip"), row=1, col=i + 1)
            fig.add_trace(go.Scatter(x=smooth, y=part[depth], mode="lines",
                                     name=f"{column} ({window_m:g} m mean)", legendgroup=column,
                                     showlegend=(j == 0), line=dict(color=color, width=2.2)),
                          row=1, col=i + 1)
        fig.update_xaxes(title_text=column, title_font=dict(size=10), row=1, col=i + 1)
    fig.update_yaxes(title_text=depth, autorange="reversed", row=1, col=1)
    fig.update_layout(**base_layout(theme), height=600, showlegend=True,
                      legend=dict(bgcolor=t["panel"], bordercolor=t["border"], borderwidth=1,
                                  font=dict(size=10)))
    return _style_axes(fig, theme)
