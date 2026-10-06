"""Color themes and shared component styles for the user interface.

HTML components take their colors from CSS custom properties (``var(--bg)``
and so on), which the theme toggle rewrites in the browser. Plotly figures
are rendered to SVG with literal colors, so figure builders obtain the
palette from :data:`THEMES` for the active theme instead.
"""

from __future__ import annotations

THEMES = {
    "dark": dict(
        bg="#0d1117", panel="#161b22", border="#30363d",
        accent="#58a6ff", accent2="#3fb950", accent3="#d2a679",
        text="#e6edf3", muted="#8b949e", danger="#f85149", warn="#d29922",
        dd_bg="#21262d", dd_hover="#30363d",
    ),
    "light": dict(
        bg="#ffffff", panel="#f6f8fa", border="#d0d7de",
        accent="#0969da", accent2="#1a7f37", accent3="#953800",
        text="#1f2328", muted="#656d76", danger="#cf222e", warn="#9a6700",
        dd_bg="#ffffff", dd_hover="#eaf0f7",
    ),
}

#: Additional series colors used after the three theme accents.
EXTRA_SERIES_COLORS = ("#bc8cff", "#ff7b72", "#f0883e")

#: Fill colors for the lithology track, keyed by lower-case lithology name.
LITHOLOGY_COLORS = {
    "clay": "#1D9E75", "silty clay": "#5DCAA5", "silt": "#888780",
    "sand": "#EF9F27", "mtd": "#D85A30", "mtd / chaotic": "#D85A30",
    "turbidite": "#378ADD", "hemipelagite": "#3fb950",
    "gravel": "#BA7517", "chalk": "#B5D4F4", "limestone": "#85B7EB",
    "basalt": "#444441", "ash": "#D3D1C7",
}
LITHOLOGY_DEFAULT_COLOR = "#444441"

#: Interface font. Century Gothic is a commercial typeface and is used when
#: installed on the viewer's computer (it ships with Microsoft Office). URW
#: Gothic is a metric-compatible free clone common on Linux; Didact Gothic, a
#: free geometric sans-serif loaded from Google Fonts, is the web fallback.
FONT = "'Century Gothic', CenturyGothic, 'URW Gothic', 'URW Gothic L', 'Didact Gothic', AppleGothic, sans-serif"

#: Font for data tables, where fixed-width digits keep numeric columns aligned.
DATA_FONT = "'Menlo', 'Consolas', 'DejaVu Sans Mono', monospace"


def palette(theme: str) -> dict:
    """Color set for a theme name, defaulting to the dark theme."""
    return THEMES.get(theme, THEMES["dark"])


def series_colors(theme: str) -> list[str]:
    """Ordered series colors for a theme."""
    t = palette(theme)
    return [t["accent"], t["accent2"], t["accent3"], *EXTRA_SERIES_COLORS]


def lithology_color(name: object) -> str:
    """Fill color for a lithology name."""
    return LITHOLOGY_COLORS.get(str(name).lower().strip(), LITHOLOGY_DEFAULT_COLOR)


def css_variables(theme: str) -> dict:
    """CSS custom-property assignments for a theme."""
    return {f"--{k.replace('_', '-')}": v for k, v in palette(theme).items()}


# Component styles (CSS variables, so they follow the active theme).
CARD = dict(background="var(--panel)", border="1px solid var(--border)",
            borderRadius="8px", padding="14px")
DROPDOWN = {"background": "var(--panel)", "color": "var(--text)",
            "border": "1px solid var(--border)", "borderRadius": "4px"}
LABEL = {"color": "var(--muted)", "fontSize": "10px", "letterSpacing": "2px",
         "marginBottom": "4px", "marginTop": "12px", "fontFamily": FONT}
HINT = {"color": "var(--muted)", "fontSize": "9px", "lineHeight": "1.4",
        "marginBottom": "4px", "fontFamily": FONT}
INPUT = {"width": "100%", "background": "var(--bg)", "color": "var(--text)",
         "border": "1px solid var(--border)", "borderRadius": "4px",
         "padding": "4px 8px", "fontSize": "11px", "fontFamily": FONT,
         "boxSizing": "border-box"}
CHECK_LABEL = {"display": "block", "marginBottom": "6px", "color": "var(--text)",
               "fontSize": "11px", "fontFamily": FONT}
CHECK_INPUT = {"marginRight": "6px", "accentColor": "var(--accent)"}


def button(background: str) -> dict:
    """Style of a full-width action button."""
    return {"backgroundColor": background, "color": "var(--bg)", "border": "none",
            "borderRadius": "4px", "padding": "6px 12px", "cursor": "pointer",
            "fontSize": "11px", "marginTop": "6px", "width": "100%", "fontWeight": "700"}
