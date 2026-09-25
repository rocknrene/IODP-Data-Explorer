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
import gzip
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

# ── Static Exp/Site/Hole reference table ─────────────────────────────────────
# Every DSDP/ODP/IODP Leg-Site-Hole combination that has ever existed, plus
# which program (DSDP/ODP/IODP) drilled it. IODP concluded in 2024, so this
# list is now essentially fixed rather than something that goes stale.
# Embedded directly (gzip+base64) rather than shipped as a separate CSV file,
# so it can never go missing from a deploy the way requirements.txt has —
# there's only one file to push. Powers the Exp/Site/Hole dropdowns so a
# user can only ever pick a combination that's real, without waiting on a
# live network round trip to find that out.
_EXP_SITE_HOLE_BLOB = "H4sIAF0srGoC/42dTZPkNnqt9/4VXjtyQXwDSwJgVnVLGsmWboTDu1ko7IkrW44Z+8b1vzfJZGaru8DzcIvnvO85IEEmk2RWLf//P28//+W/fr29//7br7f//Ovv//rXP//77f/9+re//frb35mbuf3Drf/cf7q9/fb7v//5r3/f/u3Pv/3263/8669/XaFV0CnoNZzPYVCVQVVGVRlVZVKV6bzS3vJ55QZFZVGVRVWaSZUaA1R1trcqaZO0n1F3M2K17HQW1MvaIGmUNAGdJa2CZtm5KGonoLOkVdImqJG+RvqKU4W/WSepBzoLGmRtkLVR1kZZm2RtkrVZ1hags6RV0nZO3aR8nVgb4easpE5SL2mQNEqaJM2SFkX9JKncVt4CnQUVWzLevJdUHAsrjbI2AZ0lrYJmWZtlbZGpiuocJlUbJllrZK2RtVbWOqCzpGJbBbk2QpA0ys5ybQS5NoJcGyHLznLlBLlyglw5Qa+cojpHsa7SLRqgs6BW1lpZ62Stg9oqqJedvewcZG2QtVHWRlmbZK1YsfkWxZrcqaotsrao2jSp2jRBbRXUyM5Gdray1spaJ2u9pAHoqW+5JbFydqpqk6xNUFslbYJm6VuAilR5UrV5krVG1lqgqrOTtU7WqlsP0y0HwrPCUVcnjbPGReIyaWw0tho7jfVGLXqjFr3Vitpq5layxmJ9r9hMkyw3k0Gu+1uod8A98AA8Iq+SJ6hXm3+7oWSQz5JbqLdQ76DeAw/AI3KdL0F9gnra/uqs4W5G3U3auQFugTvgHnhAPkseoT5CfYL6hPVV8gz9C/IZuPR3sP/dpPs7WB/OQD2sHwfrx3nkM/AKvAHvwJdT7leu1vfGI/AEPAMvmqt7WQefJTdQb4E75NrfQ73H+vP1EVau9s+Dz5InqE9YX4E3yTP4F83VnbKDy/zqbtnOLXAH3CM/zxdXro7PjUfgCbn2z1BfNFd3nHZukMt86r7Tzh3y8/5p5Wr/bTwg1/0j1EeoT1CfgRfN1Z2dnav9l1dukc+SO6j3yHX/APUReEKu/TPUF83VHZKdw/7JFvl5/rJytX827pFXyQPUR+AJeAZeNJe3PTZukM/A5faRN04eXPcX+2/9bmbUrZWdB+Qz8Cp5hP4JeAb/Iuutuj9z8FlysX/W77ZW3X/ZuUc+Sx6gPiKfgVfgTfIE/gn8M9QX5LK/fJvJrtwAt8Ad8llyD/UBeESu/RPUJ6hX+297racgnxVX95d2boBb4A64Bx6AR+AJOGxfW5DL7avu39jt1SoD3CKfJXdQ76DeQ72H+gD1EXgCrvZfWHnRXN0/OfgsuYF6C9wB98ADcp0/Qn0CDttf3Z9Yj12r7k8cfJbcQL2Begv1DrgHHoBH4Al4Rj4DP78+SStX+2/l6v7Fzg1wC1xt/7xyj3yWPEB9BJ6Qz8Cr5Bn6F+TSX92/2LkBboE75Dqf2r9l5QF4BJ6AZ+Sz5EVzdX/i4LreQL2Begv1DrnuD/svi+NzvXay6v7GwXV9hPoEPCOfgVfgTXL1W4P1+5e6v3LwWXID9QbqLdQ74B54AB6BJ+AZuZ6/3j9umoAb4Gr72pU75LPkHuoD8Ag8Ac/Ai+bq/sPB5fzV/Ymdq+3rVu6BB+Sz5BHqE/IZeJVc7Z/thyUF+ay4/LXUg1N9lVzt3+2nLxa4A+6Bq/0fVx6BJ+Sz5BnqM9ZXyYvur+5/HFz6q/sjLq3cIp+BV+ANeJfcQT4H+TzUq/WVVx6BJ+AZeNFc3V/ZuQFugTvgHjhsPw/bz8P287D9PGy/ANsvWOTn66usXG2/B6f6KrmH/gF4RK7zJajPwAvy8190Tjen7o/s3AC3yLW/g3oPXP1Cd70+VfdHDj5LnqA+Ay/Ipb+6/7Gee5y6/7FzC1wcPzv3UB+AR+AJeEau8xeoL1hfFc+wf9T9kZ2r/bde36r7Hzt3wD3wADwCV/tvvb5V9x92XnS9un9wcNlf3T84ONVX4E1yC/4W/B3UO6hXf99g+yG62v8PruvV+og7n4Gfb9/1+lHdHzn4LHmG+oz1FXgDfnr9vV57OXV/ZuVe3Z85+Cy5gXqLXPd3UO+BB+BqfZWVJ+AZeNFc3b85+Cy5gXoD9RbqLdSrv44xrTwgn4FXySP0F+eHYFau/srGg1N9BX56fK7fTZirv/Px4Of53CV+nt+vXP0lkQefFVf3tw6u6w3UW+AOuEc+A6/Am+QB/BPwjFznh/2r3u8J2590Ufv3wam+Sm6gv4H+Fuot1Duod1hfgZ+vj7hytT4ffAZeJQ/QP0D/CPVq/aaVZ+Qz8Cp50f3l30J68Bm49Jd/TenBZ+Dn/dfrF/n3mB58Bq77O+jvkWv/APUR+Xn/9fpE3Z88uK7PUJ+hvuh6dX9z5+qvNa3XN/IvMj34LLmDeo9c9w9QH5Hr/gnqM/J/kbxAfYF86u8trVzdHz34DFzmj7B+1O/nVubV/deDn9ev16fq/uvOPfCAXPur9bVe/6r7rzvPyGfgVfKi+6v7sweX/ur+7c4tct1f7d/1+lHd3z34LHmA+oD1VfII/RPyGbj2z9C/aK7uD+/cID/Pv16fqvvDB6f6CrwB78AX4HfJHczPQX4H+T3097D9AtSr9b/9CUq1vh9c1yeoz8ALcumv7s/v3AC3yGfgVXIH/R30V+trvf5X7wcefJY8QH0EnoBn5DpfgfqC9RV4A94FD+r++MFn4Of58srV+t24RT4Dr8Cb5A78Hfir9V1Wrtb3g8/Adf8A/QP0F9cP67WX9N+5+kufD071DXgH/gb8HfgnyRPML8H8EswvwfzS+ef7wfX8MuTPkD9D/gL9i+6vng+t1/ZBPf/ZuUU+S+6g3kG9+gu7duUB+Qy8Sh6hf4T+Efs34B34AvwueYL5qfXtVl6Qz4qr5zsH1/UG6g3UW6i3WF8ld9DfI5+Bn/v7lavj48Fn4Lp/hP4R+kfs3yRP4J+R63xqfYdbUM+vDj4Dr5Ib6G+gv4V6i/Xn+bY/u6/W94PPknuoV+t/vT5Wz592HoEn4Bm5zlegvuh69XwpZf3+08qDen60c4tc93dQr/b/en2snv8cnOqr5AH6R+AJ+Qz8/K/Pr9dn6vnQwWfJC9QXXa+eHx1c5lfPl/J6/aaeLx18ltxBvcP6KrmH/h76q7/Fv14/qedTO0/AM/JZ8qLr1fOjnRvgFrgDrra/09+P8/r5rJ7v7Dwin4FX4E3yBP4J/DPUF+Syv/zPGuvnv/z/GBu3yGfgFXgD3iV3kM9BPgf5PPT30N9jfz3/AP4B/AP4B+G//QsjdXw9+Ay8Sp6gf4L+Cfpn6F+QS3/1fG3n6vhar//U87ODz8Cr5A76O+jvsH8D3oEvwO/A3yT3MH8P81fHb9737wyc6s+3X9HvT6w8qOd/B9f1Eeoj1lfg5//5Z72+VM8Pd56Rz8ArcJ2vaH/1/PHgM3CZTz2/PPgMXPe30N8h1/4e6j3Wn+dfr8/V88uDz8B1/wj9I/QXx8e67eX178bV89WDz8Cpf5M8g39BPgNX+aJ6vnrwGTj1b5Ib8Dfgb8DfQn8L/cXn/3psRvV89uAzcOrfJPfg78FfHd9+5RH5LHmC+gT1Geoz1BeoL1hfgTfF1fPDg8/AK3Dy78AXyQ3kN5DfQH4D+Q3kNyJ/2I+fDpzq74qr57s7V+eHuHKPfJY8QH2A+gj1Eeur5An6J+ifoT5DfYH6ouvV89WdG+AWufaH9WM9cvEfKadVsC2QH1f++cdPffn57//p17/9/tt//9dffv+PF6/A2yk3K4+i/4NX4NS/S57AP4F/hvqi+f4AU/MKXM5/f4CpuEWu/R3Ue+ABufZX6+f5A9wzvl7guAQ8I6/Am+QF+hfoX7B/B74AvyvuJ53fG+QV+Pn81g94r9bvg1fg1L8DXyR3kM8jP8+/XkB4dfw8ONU34B34AvwO/E3yCPOLML8E9er8vn5AenX8P7iuL1BfsL4B78AX4Hfgb8DfgX8C/vmUp1sM6vzy4FVyA/UWuEOu/T3UB+S6f4R6dXzklavj48F1fYb6jPVN8gL9C/Qvun+cdP846f7RQL2Begv1Fuod1Duo91Dvsb4B75IH8A/gH6E+Idf9YX1Htb7L8QKZ5k1w/fmy8lhk/5jE+jbr97NkkFfgDXiX3IK/Q67zeagPwMX6MuZ4gHDOY0qyPqYE9RnqxfowduVifRyc6hvwDnxRPE86X550vjzpfNlAfwP9DfS30N8h1/4e6j3W6/wB+kfk2j9BfYL6DPUZ6xvwDvx8/a7fT7M6vh68Am/Au+Jl0v5l0v5l0v7FQH8D/Q30t9DfQn8H9Q7qPdR7rNfzC9A/Itf+CerV8eVXro6vjRfJ0zQhr5IbqDdY3yS30N9Cfwf1Hvl5/7BytT42HpFX4E3yBP0T9E/QP0P/grwCl/5m0v3NpPsbA/UG6i3UO+AeufaH9WUi8vP+ceVq/Tw41TfJM/TP0D9j/w58kbxAPrV+0y1ZtT43bpBX4E1yC/0t9HdQ77Be5/PQ30N/D/0D9A/QP0D/CP0Tcu2foT5DfdH1DtanM8gr8PPtl1eu1ueDV+DUvwNfgN8ld5DfQX4H+R3k9+Dvwd+Dv0f/Bfgd+JvkAeYXYH7q+C0rV8fvg1fgun+C/gn6Z6jPWK/zFehfoH/R/dXz44PL/ur58s4tcAfcI6/A9fwD9A/QX6zP9doiqeefB6/Am+QZ+mfon6F/gf4F+hfdXz1fPLjsr54vrtdeST1fPHiV3EG9g3oP9R7qA9RH5BV4A94lT+CfwD+Bf4b+Gfqr9W1Xrtb3g1fF1fPLg+t6A/UG6+X81PPNg1fgur+D/g76O+jvob86ftzK1fHz4FTfgHfJ1fHpV56QV+BN8gz9M/TP0L9A/wL9i+6vnp8evALX/Q30t8Adcp3PQ72H+gD1Aev19oH1mxLyCrwB78AX4Kffb2xYuTo+Nl6QV8XV89WDU30D3iU34G+R63wO6j3wADwiP88XV67W54Pr+gz1Gesb8C550f7q+ePBK3CZTz1/PLjub6HeYn0D3oEvkjvIp87faeVqfT841TfJA/QP0D9CfYT6BPUJ68/nl/f5d+CL5Or4fPAKXOcr0L9Af3V9U25ZPb/duUFegWt/cXy6aeUOeQXegHfJPfh78PfgH6B/gP4B+kfoH6F/hP4J+ifoL45PZ1Yujq+DV+DUvwNfgN+Bn96/dhb6b1wc/wevwBvwDpzy3RVXz98PXoE34Of53crF+evgFXiT3EJ/C/0t9HfQ30F/D/UBue4foT5ivZ5/gv4J+ifsr9dPBv8M/kXXq+f/B5f9LaxvC+vbwvq1DnkFLre/hfWpnu8fnPrL/Wth/VtY/+r5vvMrT8ir5BnqM9QXqC9Y3xRX7wccvALX/Q30N9DfQr2Fegf1av2Hlav1/eC6PkB9gPoI9RHqE9QnqM9Qn6G+QH3R9er58sGpvkluoL+B/ur6Iq5crd8Hr8B1fwf9HfRX5/ekfx+z7vusnq8fnOob8C55AP8A/gH8I/SP0D9ifz2/BP4J/BP4J/DP4J/BP4N/Bv8C/gX8C/gX7a/eLzi49FfvFxyc6hvwDnyR3EI+C/ks5LOQz4G/A38H/g799faB81uA81tQ57eycnX+evAKnPp3ySP4R/CP4B/RfwF+B/4G/F3yBPNPMP8E808w/wz+Gfwz+Gf0X4DfgZ9ufz/p64f12imr92cOLuvV+zMH1/UG6g3WN+Bdcgv+Fvwt+DvoL86v67VnVu/PHJzqz/Ot37/V+zUHr8B1/wj9I/RPUJ+gPkN9QS77q/dbDn5e7/XznY2r91sOXoHr/hb6W+hvob+D/g76e6j3UB+gPmC9nl+E/gl4Rl6B63xF91fvt6zfzbJ6/+TgFXgD3iW34G/B30G9w/oGvANfgN8l95DfQ34P+T3k95A/QL6IvALX+RP0T9A/Yf/z7RP19eXG1ftHB6/Am+QF+hfoX7C/nJ96f+ng0l+9n3Rwqpf51ftLB6/AqX8HvkjuIJ86P6WVq/PDg1N9A96BL8DvwN8kDzC/APMLML8A8wswP/H3GX2G7195rVfnx/X7nXp/6+AVeAPeJc/gn8G/QH2R9UW9X7Vzcf4I08ot8gq8Ae+SO/B34O/A34G/B/8AXKzPYFaegGfkVfIC9QXrm+Lq/ZqDV+DUv0tuwN+AvwF/C/0t9LfQ30F/tb7tytX6fHCqb5IH6B+BJ+AZeQXegHfJC/gX8FfHh7sV9X7NwStw6t8lN+BvwN+Av0H/RXIL+Szkc1DvkVfg5/P3K1fHx4NX4NS/A18kj5AvQr4I+RL0T9A/Qf8M/TP0z9C/QP8C/Qv278DP91+4FfV+08Er8Aa8S27A34C/AX8L/S30d1Dvkev+Aeoj8IS8Am/A9f7L4J/Bv0B9wXqZ38P69ga59PewvrzFep0f1p/3yCvwBlzufw/rV70fFOLKE/CMvAJvwLvkBfwL+Kv1mW5FvT+zc4O8Sm6h3iGvwPX8PPT30D9AfUR+3j+vXK2/B6f6BrwDX4Dfgb8Bfwf+Cfhn4N8B/x74D8D/BPxH4D8B/8dTXlauzj8PXiUvUF+wvgHviqv3Pw5egUt/9X7IwXV/C/UW6xvw0+2zzq2o9z8OXoE34Nrfg78Hfw/+Hv0X4HfJA+SPyCvwBrwDX4Dr+SXInyB/gvwJ8ifInyB/hvwZ8mfIX6B/gf4F+3fgC3C5fdT7QQevwBtwmV+9P3TwCrwBJ3+5/dT7RwfX+eD8mhzWN+Ad+Pn8zMrV+ffBK/AGvEsewD+AfwD/AP4R/CP4R/CP6K/3T4J8Gbg6P9n9+VgF3hRX72cdvALX/Q30N9DfQH8L/S30t9i/S+7A34G/A38P/T3099hfzy+AfwD/CPUR6xtwnT+BPxw/mY6fhP7n5we3cnX8b7wgr4qr96fWa+Oi3o86eJXcQr0D7pFX4A14lzyAfwD/CPUR6xvwDnyRPEE+tf7DytX6fHBdX6C+yHozqRd0ngLoYKiDoQ6WOlju0EjQSXC+o+MmUEfaIagkaCToWuApQ2BBJQGEjGQRySKyBWyHRBkSZUiUIZOFOvLSKlCvJj0FlQSNBJ0ECwnuWmBoFoZmYWgWhmZhaBaWQloKaSmkIwtHFo4sPFl4svBsAZs6UIZAGSJ1iNyhkQBmkShDogyJMiTOAIs2U8hMITOFzBQST2KFMhTKUDiD3lCWTqSWTqSWTqSWTqSWToOWToOWToOWToOWznKWznJWneXyJlBnuV3gWVBJABkCWQSyCGQRySKSRSSLRBaJLBJZZLIoIFBvdz0FOqR6P+shUIsW/sBAhD8AEOEH9CtfAzgZYBdUEjQSdC3wlMFTBk8ZPGdYtCBQyEAhA4UMFDJwyDsJ3kjwrgWRtkOk7RBpOySySGSRqUPmDo0EnQQLCe4keCPBOwlO309J0yYQ58CnoJKgkaBLgXrJ8CmoJGgkwAyLFhgKaSikoZCWLCxZWLJwZCHO9slsAnGufggCCSIJEgkyCQoLKgmaFKg3D5+CSgKwMGRhyMJSB8sdGgk6CRYS3LXA0SzUorWbQC3aQ4AdmhYEsghkEdiik2DRgkghI4WMFDJSyEQZEmVIlCGTRSaLTBaFLApZFLBQ7x4+BZUEYGHIwpCFYYtOAr1o1TuOT0ElQSNBJwGGvJPgjQTvWuBoO9BpUL1t+RTAdlAnUrcJAgkiCRIJMgkKCNRba8nfnHoqu/K1gWFBJUEjQSfBQoI7Cd5I8E6CTyT4TILvSPA9CX4gwZ9I8CMJftICS+vB0nqwtB4srQdL68HSerC0HhxN09E0HU3T0TQdTdPRNNU5bHvWrV4qfAoqCZoWRLKIZBHZomtBogyJMiTKkMkik0VmC5hmoQyFMhTIoN5ifAq0hXpP8SnADhDSkoUlC0sWjiwcWTi20Ls709GtXll8CiADnR8ynR8yHf6ZDv9MB29O3KGR4HxTx1tS97vT9jhNvVz4FGCHpgWFLApZFLboUqDecXwKKgkaCTDDogWGQhoKaSikoZCGQloKaSmkpZCWQjrK4CiDowyOMywkuGuBp1l4moWnWYgnaml7tCm/RR2CSoJGAsgQKUOkDJEyJLLILKgkgAyFLIq2MOo916cAOzQSdC0wlMGyoJKgkQBCqvND2QSeBZUEjQSdBIsWBAoZWVBJ0EjQSQCzEIfeesm9CjILqhYU6lC4QyNBlwL1puxTUEnQSAAZDGWwLICQjjp4EgQWVBLAhopkkVhQSQAZ1Ko2m6CwoJKgSYF66fApqCRAi64FhjIYymCpg+UOMAtHFo4sHFl4svBk4dkC9kWgDOrQs5tAHVmHADs0EnQtSJQhUYZMHTJ3aCSAWRTKUChDoQwFMqhXJ5+CSgKdQb1b+RSAhaUOljtASEcWjiwcWXiyUIe/2wTq4N0FkQSJBJkEBQTqLbGHwJBA7W6/CRwLKgmaFniy8GThySKQRSCLQBaRLCJZRLJIZJHIIpFFJgt1rg76vxsdvAtujHpP7SmoJGgkOM8QV4F6ke0pqCRoJIAMhjIYymAog9oXaROos8MhqFrgqIMnQWABZIjUIVKHRB0SdcjUIXOHpgUFLNTLU0+BzqDejXoKoAOtqEgrKtKKip4F5xbb/Sz1ws5TgB2aFkSyiGSRqINak9utIvXS0FOAHZoWFLBQrx09BZUEjQRdCwxlMJTBUAZDGSxlsJTBUgZxG7ds91DUqywPgWdB1YJAHSIJEgkyC85Dbl/L1dsRTwF2aCToJFikQL1f8RRUEuiQ6gWMp6CSACwsWViysGzRSbCQ4E6C05fEyvZtU70E8hB4EgQWVC2I1EEdWdt3RfXuwlNQSdBI0EmwkOCuBYVmUWgWBWah3n54CioJwEIdm9v3NPXewFNQtcBRB8cdmhZ4svBkEahDoA6ROkTqkKhDog6ZOmTqoFb1+p3XqofZT0ElQSNBJ8FCgrsWGJqFoVkYmoWhWRiahaWQlkJaCqk+1OItT+JHsAf/JPh+LVhJcB4xbRHV6WMXeBZULQjUQR3beRMkFlQtyNShsKCSoEmBehj+FGgL9Sj7KcAOENKShSULRx0cdVBLrmyCwIKqBZE6ROqQqEPiDk0LMllksshkUciigIV6nP4UYAcdUj0tfwoqCdCia4GlDOfHxfaBuQrOj4tD4FlQSdBI0EmwkOB+KjCbIIhZHIJKgqYFkSwiWUS26CRYtCBRyEQhE4VMFDJRyEwhM4XMFDJTyEwhC4UsFLJQyEIhC4QUbw28BJUEjQSdBBDSUEhDIQ2FNBTSiJB2E1gR8hBUEjQtcGThyMKxRScBbAdPIT2F9BQykEUgi0AWkSwiWUSySGSRyCKxBezNTBkyZciUoZBFIYsCFn4CCz+BhZ/YQm9JbyiDoQyGMtAZxtMZxlu2OJ+m2wTqFHQIzjP4TaDOD4cAOzQSdBIsJLhrQaBZBJpFoFkEmkWgWUQKGSlkog6JOmTqkLkDbKhCFoUsClt0EiwkuJPgjQTvJPhEgs8k+I4E30tBmGBfhAn2RZhgX4QJ9kWYYF+ECfZFMDQLQ7MwNAtDszA0C8OzeCOBXlFBfeJsd4qDI4FnQdUCdaaNmyCyoJKgkeB8Z223goM6Tx6CSoKmBZksMllksihkUciigEWcwCJOYBENdVDH5nafNqpVfQiwQ9MCRxaOLBxbdC3wlMFTBk8ZAlkEsghsAdOMlCFShkgZElmoo3u7yxrVwbsLCgsqCZoUpAks0gQWaWKLrgWGMhjKYKmDY0ElAUzTk0VgQSVBI8HppjbbHW/xrtshOF/Vbrvl9fib85+Ewu0PzEnBPRoq+rnC3n6Ypi9Bfvj5p9egHQ260aAfD9bRYPvjoNvyWbUVDkVFRUNFR8UCCodJHSb12MNf6NFQQbMNmCNgjoA5IrpEdIkXXGi2CXMkzJGxR77Qo6Gio2JBxf1c4TdFUXM5FBUVDRUdFQsqeC5vqHjXiv0lDVLA9jAGe8izecAtFm/Ry5NU3Ezk6fRQVFA47OEu9Gio6KDwmMNjDo855KkwbYp4QVFR0UCR0CWhS7rg0lGxgCJj0oxJ5akwbwp5kjoUVSvsRD3shD0M9rAXFBUVDRUdFA5z+AuKigqRtDxv+Jwp1jPpdiUYvrpmfA3W0WAbDfbR4PJxMI6M0niwjgYH7nlUXsaDdTTYRoN9NPhxRl8+rP44aEaDdjToRoN+NBjGgx9nZEYb+cs58Y+Doy1vRtvTjLanHc3dmvHgR3c72iDWjQcH5aOtZMN4cFD+zVbavp1Z9eHzVFRUNFR0UETMETFHxBwRcyTMkTBHwhzpQo4FFBmTZkyaMWnGpBmTFkxaMGnBpAWTqqt9Y/GDw+0H0TcnkMdgHg1+cwJZP7b+eKuj/dtf/u///PcfxuvJeDsZ78Nxe9LfnYz70/FxnnCiDyf6eKKPH/ThmNdyMn4/GX87GX8/Gf80HLcneeyH7f8c/3b7x2O7tZPxsT6e6OOpfjkZvw/H04ft/xyvJ+PtZHycJ5/0zyf984f+6fZ/jAvqIvclqSxpJLFsZNnIXjHqJHGcxXEWx1k8G3k2Uhfe6zXUJpGXEw9JZEm6IqkswbiZjTIbFe5SrnRpLOkgiRNmieaKRMQt+4FcPhz4+/iXbwffjH/44FuvpVcrp9K8JJUljSSWjSwbWTZybOTYyLGRZyPPRp6NAhsFNorcRV3jr9+oNok6N7wkF7o0lnSSZM6SOUvmLOoqfLtzsx9f33zgHuMfLghf49/q3W6lTmYvSWVJY0kHiZ8wi58wy/52MEowi+EshrMYzmLZyLKRvWKEk3acxXEWx1nclSwLSTzH9RzXc1zPcT3HDRw3cNzAcQPHDSqu3yXy/P2QJJZklhSUBHlmCNvXb/f1rbrXYP04aEZKOxp0o0E/GgyjwTgaTKPBPBosg0E/mqYfzciPZuRHM/KjGfnRjPxoRn40Iz+akS/jwY/7KIymGUbTDHY8WEeD7ePgaIMEPx4c9BxtpTDaSmG0lcI3Wyke9xq++W5v9+9jUX5SPCWVJI67uCtdGks6SxaW3Fly/kTePr7gyE+KskvCFUllSWNJZ8lCkshxI8eNHDdy3Hgl7p0kiWeUeEaJZ5R4RolnlHhGmWeUeUaZZ5R5RplnlHlGhWdUeEaFZ1R4RgVnlCaMmyaMmyaMmyaMm6YrcWkHJMMzMjwjwzMyPCPDMzJXZvTGkvMXqtx+JyGpz8ZD4ljiWRKuSCpJIndJVyTCyOxf+z88B3qN15PxdjLeT8aXk/H7yfjbyfj7cNyf5Pcn+f1Jfn+S35/k9yf5/Un+cJIznOQMJznjSZ940ied6NOpvp2Mf7t97HEt+jYa/3jbd7uBud+Waifj/WR8GY7bkz72pI897XM/GX8bjn94vuX2L6BJfZS/JJUkhbuUK10aSPKERtlckYgsYZXoJ+jr99+tizw1PyWVJI67uCtdGks6SxaSeIjrivwgSdtitB8Psjz+gvgafz8Z/3Qy/vlk/LuT8e9H4+bE13x4KPwYt2Y8L2tO9PZE/+GettuvF7LctE9JZUljSWfJQpLIcSPHjRw3sVFio8RGmY0yG2U2KmxU2KhcMeosoT1dJoxbJoxbJoxbDBsZNlJfE/x+CVrUafmQ+CuSSpLAXQJ3idwlXumC2yWxUWKjzF0ydyncpVzpApP200RGfprIyE8TGxk2MmxkrhidH/Z+e1br5c8oXpLKksaSC1kWkmSOmzlu5riZ4+Yrce8kKTyjwjMqPKPCMyo4I/kTkUNirkgqS8SM7L681aX5S1JJ4riL4y6eu/grXXDSgY0CGwU2imwU2SiyUWKjxEaJjTIbZTbKbFTYqLBRQSP5S8yXpLIEjQwbGTaSx/TjDsaHF/Ve4/VkvJ2M95Pxb+60+OctB5H+KWkkMSTx8henL0llyQWjThLHWRxncZxFnh3DLglXJJUljSWdJQtL7ix5Y8k7Sz6x5DNLvmPJ9yz5gSV/YsmPLPnpXBL36wN5HD0llSWNJZ0lC0kcx3Uc13Fcz0aejTwbBTYKbBSuGHWW4A6IHDdy3MhxI8dNnCVxlsRZMhtlNircpVzpQnHdhEZuQiMnP6fT/uLS129yvQbraLCNBvtocPkwGKeBUZwGRtGMlGasbKPB70aD348Gf/g4aEfuduRuR+52sEGiHUWyo0hu5O5G7m7k7kbubrQ7/MjIj4z8yMiPjMKoZxj1DKOecVQeR+VppPz6Z/j++aTjh28ucp/jfzoZ/+mb8bIfSfKq/ympLGks6SxZSGI5ruW4luM6NnJs5LmL+owN+z0++TuVl6SypLGkkyRxlsRZEmdJV7Kcr5ew32KSP4h5SSpLGks6Sy7EPf/KEfYv2PLnMC9JZUkDifyty0tCRvJXKodEHdPB7RJ3RVJZ0ljSSeI5i+csgbuEK11wRpGNIhtFNkpslNgosVFmo8xG8pjeb2nIn328JBUk8pchLwl2MdzFcBfLXeyVLrTp5A/JD4m/IqksaSzpJAmcJXAWeTTudz3kb81fksqSC0adJQtJEsdNHFce0/sXOvm790NSrkgqSyiL/MX6S0JG8kftL8mFLo0lnSSWs1jOYjmLYyPHRu6KEU7acxbPWTxnkeeXx1e4r39F9RqsHwfLQJmm8WAdDbaPg2ZUbkblZlRuR+V2VG5v/zwa/JePg27U048Gw3iwjgYH7nFU/vV37/D8hvyPX39Dfo3/08n4zyfjv4zG7YcXxV/j9WS8nYz3k/HlZPx+Mv42HP/wonjYv6jKn5+9JJUljSSRjSIbJe6SrnRpLOksWUiSOW7muIW7FOwif5T0kmAXw13MlS6NJZ0ltAPk73Pifr9B/j4n7l/g5e9zXpLKksaSzpKFJXeWvLHknSSBt0vg7RJ4uwTeLoG3S+DtEnlGkWcUeUaRZxR5RonjJo6bOG5mo8xG+YoRbpfCWQpnKZylXMmysOTOkjeWvLPk/IWDuN+Uk7/PeUkqSxpLOksWltxZ8saSd5KoT7W431mSPyd6SSpLGks6SRxncZzFcRbPRp6NPBsFNgpsFK4Y4daNnCVylshZImdJnCVxlsxd8pUuOKPCRoWNyhUj2nTyRy8vSWWJyOL3751fP96NYfxD1dd4PRlvJ+P9ZPyb74Vx+x+E8kcMcb+xIX+j85JUlqCRZSPLRvaKUSeJ4yyOs3juEq5I0Chyl3ilC266xEaJjdIVI9xHmbPIs1TaJfIU9JRAlyB/UvSSiC55l8hD7Sm50KWxpJPEchbLWRx38VckaBS4S7jSpbFEbLrtJkKQb34fEndFUlnSWNJZspDEc1zPcT3HDWwUr0gqSxpLzjfddov9j/8j4/mpmsy+SFTGQ5KuSCpJMnfJV7o0khQ0kr9ReUkoi/wByksiuthdIrfuU3KhS2NJJ0nmLJmzFOwif7L3kggjv0vkDnhILEvcFUllSWNJZ8lCEs9xPcf1HFed4FI4FkNjSWfJwpI7S95Y8k4SdQH1kjSWdJbQpO2ERnZCI/kjg5dETDruEnmWekoudGkkyWyU2ShfMeokKZylYBb5C4JDYq5IKksaSzpLxMLcLyzly1IvSWVJY0lniYi7Xx3Jd7BfksqSxpLOkgtx7yx5I4njSTuetONJq32UzX4b6eu3SV6D9eNgGSjz12+T5P3yQL6p/pJUljSWdJIEzhKvSCpLGks6SxaSJI6bOG7iuInjZs6SOUvmLOoTIbtdoj4RXpLKksYSyiLfd39JKksaSy5kWUhiOK7huIbjWjaybGSvGOF2cZzFcRZ5rtsv1sMlSWVJY0knSeAsgbMEzhKuZFlYcmfJ+edt3q+h5e8jXpLKkkaSyEaRjeIVo06SxFkSZ0mcJV3JspAkc9zMcTPHzRw3c9zCcQvGlT/neEmwi+EuhrtY7mKvdBE7IO6XjF+/QfwarKPBNhrso8FlNHj/OGhH7nbkbkfuduTuRj3dqKcb9XTjnstocDAjP3L3I3c/cvcjdz9y9yP3MHIPI/cwcg8j9zjqGUc900iZxso2Ghy451HPPOqZRz3zqGcZ9SyjnmXUswx6lmnQs0yDnmUa9CzTqOfo2CyjY7OMjs0yOjbL6IgroyOujI64MjriyuiIK6MjroyOjjI6OspoJZevV3KZ9pOc+gx/SRpLOksWkiTuUjiL+kpT9scl8q3bQxKvSCpJEndRFwtl/8IoX9d8SSpLGks6SOT7jy8JZZFvC74k2MVyF/UxX/ZvPUFdRL0kC0vuJCmYRf5+7iVpIJEvN74klSUXjDpLFpaITReOb4NvLHkHifyh3iFRXztfkoUlMKMoXyZ5SSpLGks6S8SM9rv18kdOL0klSeQu6YqksqSRJLNRZqOCXeSvhl6SypLGkk4Sw1kMZ7HcxXIXx138FUllidh06fhb/3eWvLHknSWfWPJZS4J8v/olqSxpLOkkSZwlcZbEWTIbZTbKV4zEpPengfIt7ZeksqSBRL6C/ZKQkXxh+SW50AXjWjaybOS4i7vSBeN6NgpXJJUlmCWyUWSjxF3yFQka8RFQypUusF3ihEdAnPAIiPKt/rL/z4xJfvkpexd5HD0llSWnWbb/obBJxBHwRVJZcsGos2RhyZ0knmfkeUaBu8QrksqSxhLcdImzZCUxxwXBdyz5Xkuiel31i6SCRL3R+pSYKxJhZHeJVV2eEuziuIu/IqksaSQJbBSvSCpLGks6SxaSJI6bVFy3S+QR8JRc6NJIUtDITlcklSWNJZ0khrMYzmI4i2Ujd0VSWdJY0lmysOTOkjeSeJ6050mrb592+mU7fctXN79o1L9F2jXy8tubn/e/Lx8vaarW5KBeg/bul/E/ooj2F/6DIfmf8XdSqfxy+2wmeT32RVMvaNoFTb+gWVBjL2S2FzLbC5mvbEN3wctf6OMv9eHM4YJXuOAVLnjFC17xgle84JUueKULXumCV77glS94yZslUzue682oSZc09YKmXdB01OQLefKFPFe2T76Qp1zII79Nh3Y8A1kuaO6okTcyX5pPpIn6IcdTg/OSfz7xi4b7+At9/KU+6rHLwg/WHxr5FzW/aLiPudDHXOqjbo7+st+0lNvw0Ihz+P8C9forazoVAgA="

def _load_exp_site_hole_ref():
    try:
        raw = gzip.decompress(base64.b64decode(_EXP_SITE_HOLE_BLOB))
        df = pd.read_csv(io.BytesIO(raw), dtype=str)
        return df
    except Exception:
        return pd.DataFrame(columns=["Exp","Site","Hole","program","vessel"])

EXP_SITE_HOLE_REF = _load_exp_site_hole_ref()

def _exp_sort_key(exp):
    """Numeric-aware sort so Leg/Exp options read 1, 2, 3 ... 400, not the
    lexicographic 1, 10, 100, 2, 200 ... a plain string sort would give."""
    try:
        return (0, float(exp))
    except (TypeError, ValueError):
        return (1, str(exp))

ALL_EXPEDITIONS = sorted(EXP_SITE_HOLE_REF["Exp"].dropna().unique().tolist(), key=_exp_sort_key)

def sites_for_exp(exp):
    if not exp:
        return []
    sub = EXP_SITE_HOLE_REF[EXP_SITE_HOLE_REF["Exp"] == str(exp)]
    return sorted(sub["Site"].dropna().unique().tolist(), key=_exp_sort_key)

def holes_for_exp_site(exp, site):
    if not exp or not site:
        return []
    sub = EXP_SITE_HOLE_REF[(EXP_SITE_HOLE_REF["Exp"] == str(exp)) &
                            (EXP_SITE_HOLE_REF["Site"] == str(site))]
    holes = sorted(sub["Hole"].dropna().unique().tolist())
    return holes

def hole_display_label(hole):
    # "*" is this lab's own convention for legacy DSDP holes drilled before
    # hole letters were standardized — it does not mean the letter was
    # forgotten, it means there genuinely isn't one.
    return "* (no hole letter — pre-dates hole lettering)" if hole == "*" else hole

def vessel_for_exp(exp):
    """Which vessel drilled this Leg/Expedition — Glomar Challenger (DSDP),
    JOIDES Resolution (spans ODP and modern IODP), MSP (IODP-era Mission
    Specific Platform), or Chikyu (JAMSTEC). This is the precise signal for
    which data source is even worth trying: searching PANGAEA's DSDP/ODP
    archive for a modern JOIDES Resolution IODP expedition, for example,
    will never find real data — it just returns loosely-relevant garbage,
    since that Leg was never DSDP or ODP to begin with."""
    sub = EXP_SITE_HOLE_REF[EXP_SITE_HOLE_REF["Exp"] == str(exp)]
    vessels = sub["vessel"].dropna().unique().tolist()
    return vessels[0] if len(vessels) == 1 else None

def program_for_exp(exp):
    sub = EXP_SITE_HOLE_REF[EXP_SITE_HOLE_REF["Exp"] == str(exp)]
    programs = sub["program"].dropna().unique().tolist()
    return programs[0] if len(programs) == 1 else None

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
# DSDP-native data types with no LIMS/LORE equivalent — DSDP predates LORE
# entirely, and most of these (paleontology, lithology, core description)
# were never part of LORE's physical-property report vocabulary in the
# first place. Keyed by the exact category string shinylaurel.com's own
# dropdown uses, confirmed directly from that app's page source, so no
# separate label mapping is needed for the Selenium/shinylaurel path.
DSDP_NATIVE_REPORTS = {k: k.title().replace("Ucr", "UCR") for k in [
    "age assignments", "algae", "alternating field demagnetization",
    "ammonite", "aptychi", "archaeomonads", "benthic foraminifera",
    "bryozoans", "calcispherulides", "carbonate and carbon",
    "core description - hard rock", "core description - screen, sediments",
    "core description - visual, sediments", "crinoids", "depth and recovery",
    "diatoms", "dinoflagellates", "ebridians and actiniscaceae",
    "fish debris", "grain size distribution", "interstitial water",
    "major element analyses - hard rock",
    "minor and trace element analyses - hard rock", "nannofossils",
    "ostracods", "phytolitharia", "planktonic foraminifera", "pollen",
    "radiolarians", "rhyncollites", "rock magnetic measurements",
    "rock magnetic measurements - curie", "silicoflagellates",
    "site summary", "smear slides", "spinner magnetometer",
    "spinner magnetometer (long-core)", "trace fossils",
    "x-ray diffraction - other labs, bulk", "x-ray diffraction - other labs, clay",
    "x-ray diffraction - other labs, silt", "x-ray diffraction - UCR, bulk",
    "x-ray diffraction - UCR, clay", "x-ray diffraction - UCR, silt",
]}
# The combined list the Report type dropdown actually offers.
ALL_REPORT_TYPES = {**LORE_REPORTS, **DSDP_NATIVE_REPORTS}

# Search terms likely to appear in a PANGAEA dataset's own title/citation for
# each measurement type — without these, a PANGAEA search only narrows by
# Leg and project, and returns whatever dataset types exist for that Leg
# (core photos, XRD protocols, geochemistry, ...) regardless of which
# physical property was actually requested.
PANGAEA_REPORT_KEYWORDS = {
    "gra":       "GRA bulk density",
    "mad":       "moisture density MAD",
    "pwave":     "P-wave velocity",
    "ngr":       "natural gamma radiation",
    "thermcond": "thermal conductivity",
    "wrmsr":     "multisensor track MSCL",
    "shearstr":  "shear strength",
    "xrf":       "XRF",
    # DSDP-native categories reuse their own label text as the search
    # keyword — a reasonable default since PANGAEA search is free-text
    # anyway, though not individually curated the way the LORE keys above are.
    **{k: k for k in DSDP_NATIVE_REPORTS},
}

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
def _parse_tabular_bytes(raw, fname):
    """Parses CSV/TSV bytes — multi-encoding with LIMS metadata detection.
    Used directly for .csv/.tsv uploads, and for whichever CSV/TSV member
    gets selected out of a .zip upload."""
    sep = "\t" if fname.lower().endswith(".tsv") else ","
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
    return df, lims_meta

def parse_upload(contents, filename):
    """
    Decode a Dash-uploaded file and return (DataFrame, meta).
    Supports .csv, .tsv, .xlsx, .xls, .las, .zip
    """
    _, b64 = contents.split(",")
    raw    = base64.b64decode(b64)
    fname  = filename.lower()
    meta   = {"filename": filename}
    try:
        # Accepts TSV as well as CSV
        if fname.endswith(".csv") or fname.endswith(".tsv"):
            df, lims_meta = _parse_tabular_bytes(raw, fname)
            if df is None:
                return None, {"error": f"Could not decode {'TSV' if fname.endswith('.tsv') else 'CSV'}"}
            meta["format"] = "TSV" if fname.endswith(".tsv") else "CSV"
            # Attach LIMS metadata if found
            if lims_meta:
                meta["lims_meta"] = lims_meta

        elif fname.endswith(".zip"):
            # Some archives (JAMSTEC's Chikyu J-CORES "Bulk Export" downloads
            # in particular) hand back a .zip containing a bulk-*.csv plus
            # attachment files, rather than a bare CSV/TSV. Pick whichever
            # CSV/TSV member inside looks most like real tabular data.
            try:
                zf = zipfile.ZipFile(io.BytesIO(raw))
            except Exception:
                return None, {"error": f"Could not open {filename} — not a valid zip file"}
            candidates = [n for n in zf.namelist()
                         if n.lower().endswith((".csv",".tsv")) and "__MACOSX" not in n]
            if not candidates:
                listed = ", ".join(zf.namelist()[:10])
                return None, {"error": f"No CSV/TSV file found inside {filename} — contains: {listed}"}
            best_df, best_score, best_name, best_lims_meta = None, -1, None, {}
            for name in candidates:
                try:
                    cand_df, cand_lims_meta = _parse_tabular_bytes(zf.read(name), name)
                    if cand_df is None or cand_df.empty:
                        continue
                    score = sum(1 for col in cand_df.columns
                                for kw in HEADER_KEYWORDS if kw in str(col).lower())
                    # JAMSTEC's own "bulk.csv" / "bulk-<field>.csv" naming
                    # convention is a strong signal for the main data table.
                    if os.path.basename(name).lower().startswith("bulk"):
                        score += 1
                    if score > best_score:
                        best_score, best_df, best_name, best_lims_meta = score, cand_df, name, cand_lims_meta
                except Exception:
                    continue
            if best_df is None:
                return None, {"error": f"Found {len(candidates)} CSV/TSV file(s) in {filename}, but none could be parsed"}
            df = best_df
            meta["format"] = "ZIP"
            meta["zip_member"] = best_name
            other = [n for n in candidates if n != best_name]
            if other:
                meta["zip_other_members"] = other[:10]
            if best_lims_meta:
                meta["lims_meta"] = best_lims_meta

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
            return None, {"error": f"Unsupported file type: {filename}\nSupported formats: .csv, .tsv, .xlsx, .xls, .las, .zip"}
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
    """Searches PANGAEA via its own documented search API — the same
    endpoint PANGAEA's own website search box calls — confirmed against
    the official pangaeapy client's PanQuery class and test suite. This
    replaces querying the raw internal Elasticsearch cluster directly
    (ws.pangaea.de/es/...), which is undocumented; that approach's exact
    field names and query behavior were never confirmed and turned out to
    only work by coincidence for specific queries, not reliably across
    arbitrary Legs."""
    try:
        r = requests.get("https://www.pangaea.de/advanced/search.php",
                         params={"q": query, "count": count, "offset": 0},
                         timeout=20)
        r.raise_for_status()
        hits = r.json().get("results", [])
        results = []
        for h in hits:
            uri = h.get("URI", "")
            pid = uri.split(".")[-1] if uri else ""
            if pid:
                results.append({"label": f"{pid} — {uri}", "value": pid})
        # The search result itself only carries a display HTML snippet, not
        # a clean title -- fetch the real title for whichever results will
        # actually be shown (capped, since this is one extra request per
        # result and the pick list itself only ever displays the first 8).
        for r_item in results[:8]:
            real_title = fetch_pangaea_title(r_item["value"])
            if real_title:
                r_item["label"] = f"{r_item['value']} — {real_title[:70]}"
        return results, None
    except Exception as e:
        return [], str(e)

def _leg_match_pattern(leg):
    leg_str = str(leg).strip()
    # \b (a zero-width word boundary) after the number correctly matches
    # whether it's followed by a space, a hyphen, a comma, or nothing at
    # all (end of string/title) — the previous version required one of a
    # specific set of characters immediately after the number, which
    # silently failed to match titles where the Leg number was the very
    # last thing in the string. Leg/Hole/Expedition also now accept a
    # trailing "s" (e.g. "DSDP Legs 1 and 4"), which the singular-only
    # version missed.
    return re.compile(rf'\b(?:Legs?|Holes?|Expeditions?)\s*{re.escape(leg_str)}\b', re.IGNORECASE)

def _matches_leg(result, pattern):
    return bool(pattern.search(result.get("label","")))

def _rank_pangaea_by_leg_match(results, leg):
    """Re-sorts PANGAEA search results so entries that actually name this
    Leg/Hole outrank ones that only loosely match on keywords. PANGAEA's
    own relevance ranking doesn't reliably do this — a dataset from a
    completely different Leg that happens to share more keywords (e.g.
    "bulk density") can rank above an exact match for the requested Leg
    that uses more specific terminology (e.g. "GRAPE ... Hole 1-4")."""
    pattern = _leg_match_pattern(leg)
    return sorted(results, key=lambda r: 0 if _matches_leg(r, pattern) else 1)

def _kw_matches(label_lower, kw_lower):
    """Tolerates simple singular/plural mismatches (e.g. a PANGAEA title
    saying "Diatom stratigraphy" when the keyword is "diatoms") — an
    exact-substring check alone would wrongly treat that as a non-match."""
    if not kw_lower:
        return True
    if kw_lower in label_lower:
        return True
    if kw_lower.endswith("s") and kw_lower[:-1] in label_lower:
        return True
    return False

def search_pangaea_legacy(leg, project="DSDP", report_keyword=None, count=15):
    """Search PANGAEA for legacy DSDP/ODP shipboard datasets tied to a Leg
    number. PANGAEA's Elasticsearch endpoint is documented (and used by its
    own R client, pangaear) as a plain free-text 'q=' search — not a
    structured match against specific field names like campaign.label,
    which was this function's original approach and returned zero hits
    for every Leg since those field names were never confirmed to exist.
    Reuses search_pangaea()'s proven free-text query for the same reason.

    Runs two queries — the Leg number alone, and the Leg number plus the
    report keyword — and merges their results, rather than trusting either
    single query's top results to contain the right answer. Combining the
    Leg and keyword into one query lets datasets from an entirely different
    Leg dominate the results if that Leg simply has more keyword-matching
    data (e.g. more "P-wave velocity" datasets than the requested Leg has).
    Searching the Leg alone avoids that, but has the opposite failure mode:
    if the real matching-type dataset for this Leg doesn't happen to rank
    in PANGAEA's own top results for the bare Leg query, it's never
    fetched at all, and no amount of re-sorting afterward can recover a
    result that was never retrieved. Querying both ways and merging means
    a real match only has to surface in *either* query's top results, not
    both — then results confirmed on both the Leg and the report keyword
    are ranked first, Leg-confirmed-only next, keyword-only last."""
    base_query = (f'"Leg {leg}" DSDP' if project == "DSDP"
                 else f'"Leg {leg}" "Shipboard Scientific Party"')
    broad_results, err1 = search_pangaea(base_query, count=30)

    kw_results, err2 = ([], None)
    if report_keyword:
        kw_results, err2 = search_pangaea(f"{base_query} {report_keyword}", count=30)

    if err1 and err2:
        return [], err1
    if err1:
        broad_results = []
    if err2:
        kw_results = []

    # Deduplicate by dataset id, preferring the label from the bare-Leg
    # query (its title isn't shaped by which keyword was searched for).
    merged = {}
    for r in broad_results + kw_results:
        pid = r.get("value")
        if pid and pid not in merged:
            merged[pid] = r
    if not merged:
        return [], None

    pattern = _leg_match_pattern(leg)
    kw_lower = report_keyword.lower() if report_keyword else None
    def score(r):
        leg_ok = _matches_leg(r, pattern)
        kw_ok = _kw_matches(r.get("label","").lower(), kw_lower)
        if leg_ok and kw_ok: return 0
        if leg_ok:           return 1
        if kw_ok:            return 2
        return 3
    ranked = sorted(merged.values(), key=score)

    if report_keyword and not any(_kw_matches(r.get("label","").lower(), kw_lower) for r in ranked):
        # Nothing found even loosely confirms the requested measurement
        # type — showing the merely Leg-confirmed results anyway would
        # present unrelated data (e.g. carbon analyses, manganese deposits)
        # as if they were candidates for what was actually asked for. Say
        # plainly that nothing was found rather than offering a guess.
        return [], None

    return ranked[:count], None

# NCEI/NGDC's internal file-name codes for each DSDP "by data type" flat
# file, and which report type each is the real-world equivalent of.
# Confirmed directly from Laurel's own team's published reference data
# (shinylaurel/LIMS2_dsdp_NGDClinks_dt.csv on GitHub — the file their own
# LIMS2 app uses to link to NGDC), not guessed. The four LORE-equivalent
# codes: "grape" is literally the historic instrument name GRA is short
# for; "density" is DSDP-era MAD; "sonic" is DSDP-era P-wave velocity;
# "vane" is vane shear strength. Natural Gamma Radiation, Thermal
# Conductivity, WRMSL (multi-sensor), and Shore XRF Summary have no code
# in that reference file — those instruments largely postdate DSDP, so the
# absence is a real fact about what DSDP measured, not a gap in this
# mapping. The remaining entries map DSDP-native categories (matched by
# their exact shinylaurel.com dropdown label) to that same reference
# file's codes. Two categories are left unmapped rather than guessed:
# "rock magnetic measurements" has both an "hr_mag" and a separate,
# more specific "dsed_mag" code with no clear way to tell which the UI
# category corresponds to, and "spinner magnetometer (long-core)" has no
# distinct code from plain "spinner magnetometer" in the reference file.
NGDC_DSDP_FILE_CODES = {
    "gra":      "grape",
    "mad":      "density",
    "pwave":    "sonic",
    "shearstr": "vane",
    "age assignments":                            "ageprof",
    "algae":                                      "algae",
    "alternating field demagnetization":          "afd_mag",
    "ammonite":                                   "ammonite",
    "aptychi":                                    "aptychi",
    "archaeomonads":                              "archaeom",
    "benthic foraminifera":                       "b_forams",
    "bryozoans":                                  "bryozoan",
    "calcispherulides":                           "cspherul",
    "carbonate and carbon":                       "carbon",
    "core description - hard rock":               "hr_desc",
    "core description - screen, sediments":       "screen",
    "core description - visual, sediments":       "vistxt",
    "crinoids":                                   "crinoids",
    "depth and recovery":                         "coredep",
    "diatoms":                                    "diatoms",
    "dinoflagellates":                            "dinoflag",
    "ebridians and actiniscaceae":                "ebri_act",
    "fish debris":                                "fish_deb",
    "grain size distribution":                    "grain",
    "interstitial water":                         "water",
    "major element analyses - hard rock":         "hr_major",
    "minor and trace element analyses - hard rock": "hr_minor",
    "nannofossils":                               "nannos",
    "ostracods":                                  "ostracod",
    "phytolitharia":                              "phyliths",
    "planktonic foraminifera":                    "p_forams",
    "pollen":                                     "pollen",
    "radiolarians":                               "radiolar",
    "rhyncollites":                               "rhyncoll",
    "rock magnetic measurements - curie":         "hr_mag_curie",
    "silicoflagellates":                          "siliflag",
    "site summary":                               "sitesum",
    "smear slides":                               "smear",
    "spinner magnetometer":                       "spin_mag",
    "trace fossils":                              "trfossil",
    "x-ray diffraction - other labs, bulk":       "xrw_bulk",
    "x-ray diffraction - other labs, clay":       "xrw_clay",
    "x-ray diffraction - other labs, silt":       "xrw_silt",
    "x-ray diffraction - UCR, bulk":              "xrd_bulk",
    "x-ray diffraction - UCR, clay":              "xrd_clay",
    "x-ray diffraction - UCR, silt":              "xrd_silt",
}

NGDC_DSDP_BASE_CANDIDATES = [
    # NCEI's own current metadata landing page for this dataset
    # (doi:10.7289/V54M92G2) explicitly links this path as "Data files and
    # documentation" — "Web version of data... Data file access by leg,
    # site/hole, data type, and geographic area." This is the more
    # authoritative, currently-live-linked path.
    "https://www.ngdc.noaa.gov/mgg/geology/dsdp/all_dsdp_data_by_type/",
    # The path Laurel's own team's published reference data
    # (LIMS2_dsdp_NGDClinks_dt.csv) uses instead — kept as a second
    # attempt in case one path has moved and the other hasn't.
    "https://www.ngdc.noaa.gov/mgg/geology/data/glomar_challenger/all_dsdp_data_by_type/",
]

def fetch_dsdp_ngdc(file_code, expedition, site="", hole="", timeout=30):
    """Downloads a DSDP 'by data type' flat file directly from NCEI/NGDC's
    static archive — a plain file per data type covering every DSDP Leg,
    no Shiny app or browser session involved. Tried before the
    Selenium-driven shinylaurel.com fetch since a plain HTTP request is
    far simpler and faster when it works.

    NOTE: shinylaurel.com's own page states this NCEI hosting was
    "unavailable for download" as of July 2026, so this may currently be
    down entirely regardless of which URL is used — if both candidates
    fail, the caller falls back to shinylaurel.com. The exact column
    delimiter of these files hasn't been confirmed either, so several
    common ones are tried in turn."""
    errors = []
    df = None
    for base in NGDC_DSDP_BASE_CANDIDATES:
        url = f"{base}{file_code}.txt"
        try:
            r = requests.get(url, timeout=timeout,
                             headers={"User-Agent": "Mozilla/5.0 (research script)"})
            r.raise_for_status()
            text = r.text
            candidate_df = None
            for sep in ["\t", r"\s{2,}", ","]:
                try:
                    candidate = pd.read_csv(io.StringIO(text), sep=sep, engine="python")
                    if candidate.shape[1] > 1:
                        candidate_df = candidate
                        break
                except Exception:
                    continue
            if candidate_df is None or candidate_df.empty:
                errors.append(f"{base}: downloaded but couldn't parse its column layout")
                continue
            df = candidate_df
            break
        except Exception as e:
            errors.append(f"{base}: {e}")
            continue
    else:
        return None, "; ".join(errors)

    n_raw = len(df)
    df = _restrict_to_request(df, expedition, site, hole)
    if df.empty:
        requested = ", ".join(f"{k}={v}" for k, v in
                              [("Leg",expedition),("site",site),("hole",hole)] if v)
        return None, f"Downloaded {n_raw:,} rows but none matched {requested}"
    return df, None

DSDP_SHINYLAUREL_URL = "https://shinylaurel.com/shiny/DSDP_data_access/"

# shinylaurel.com's DSDP_data_access app organizes legacy DSDP data by its
# own category vocabulary (paleontology/lithology/method-based labels), not
# by LORE's physical-property report codes. The four LORE-equivalent keys
# map to their real-world shinylaurel.com equivalent:
#   - "gamma ray attenuation" IS GRA — the same measurement LORE calls
#     GRA Bulk Density, just under DSDP-era terminology.
#   - "sonic velocity" is DSDP/ODP-era terminology for P-wave velocity.
#   - "vane shear" is the direct equivalent of LORE's Vane Shear Strength.
#   - "density and porosity" is the closest match for MAD (Moisture and
#     Density), though MAD also reports moisture content that this
#     category may not fully cover.
# Natural Gamma Radiation, Thermal Conductivity, WRMSL (multi-sensor), and
# Shore XRF Summary have no confident match (X-ray diffraction categories
# are XRD — mineralogy — not XRF's elemental geochemistry, a different
# technique) and are deliberately left out rather than guessed at; those
# report types still fall through to PANGAEA for DSDP Legs. Every
# DSDP-native report key already IS the exact shinylaurel.com dropdown
# value (confirmed from that app's page source), so those just map to
# themselves.
SHINYLAUREL_DSDP_TYPES = {
    "gra":      "gamma ray attenuation",
    "mad":      "density and porosity",
    "pwave":    "sonic velocity",
    "shearstr": "vane shear",
    **{k: k for k in DSDP_NATIVE_REPORTS},
}

def fetch_dsdp_shinylaurel(data_type_label, expedition, site="", hole="", timeout=45):
    """Drives shinylaurel.com's DSDP_data_access Shiny app the way a browser
    would, since the app has no stable download API. Uses the app's
    "Data by Site" tab, which — confirmed directly from the page's HTML
    source — exposes real <select> dropdowns for Leg (#var1), Site (#var2,
    multi-select, populated dynamically once Leg is chosen), and Data type
    (#var_data, all 43 categories enumerated in the initial page). This
    lets the query be scoped to the actual requested Leg + Site up front,
    rather than downloading every Leg for a data type and filtering
    locally (the app's other tab, "Data by Type", only supports the
    latter). The download link (#download_site_data) starts disabled —
    confirmed via the page's shinyjs enable/disable handlers — and the
    server enables it with a real href once a full Leg + Site + Data type
    selection has been made.

    NOTE: this has not been exercised against the live site — this sandbox
    has no network path to shinylaurel.com and no Chromium binary to run
    Selenium at all, so this is built from the app's actual rendered HTML
    (not from guessing) but still untested end-to-end. Expect to need at
    least one round of fixes once this actually runs on the deployed Space.
    """
    try:
        from selenium import webdriver
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait, Select
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
        wait.until(EC.element_to_be_clickable(
            (By.CSS_SELECTOR, "a[data-value='bysite']"))).click()

        # Leg (#var1) — a plain single-select, DSDP Legs 1-96
        leg_select = Select(wait.until(EC.presence_of_element_located((By.ID, "var1"))))
        leg_select.select_by_value(str(expedition))

        # Site (#var2) starts as a single "placeholder1" option until the
        # server responds to the Leg selection — wait for it to actually
        # refresh before touching it.
        def _sites_loaded(d):
            opts = Select(d.find_element(By.ID, "var2")).options
            return len(opts) >= 1 and opts[0].get_attribute("value") != "placeholder1"
        wait.until(_sites_loaded)

        if site:
            site_select = Select(driver.find_element(By.ID, "var2"))
            # #var2 is a multi-select — select_by_value() ADDS to whatever
            # is already selected rather than replacing it, and the app's
            # default selection state after a Leg change isn't known, so
            # clear it explicitly first to guarantee only the requested
            # site ends up selected.
            site_select.deselect_all()
            try:
                site_select.select_by_value(site)
            except Exception:
                pass  # requested site not in the live list — leave default selection
        else:
            # No site requested — explicitly select every available site,
            # rather than relying on whatever the app's own default
            # selection happens to be.
            site_select = Select(driver.find_element(By.ID, "var2"))
            for opt in site_select.options:
                val = opt.get_attribute("value")
                if val:
                    site_select.select_by_value(val)

        # Data type (#var_data) — values are the exact category strings
        # confirmed from the page source (e.g. "gamma ray attenuation").
        data_select = Select(driver.find_element(By.ID, "var_data"))
        data_select.select_by_value(data_type_label)

        # The download link is disabled until the server has a complete,
        # valid selection to build a file from.
        def _download_enabled(d):
            link = d.find_element(By.ID, "download_site_data")
            return "disabled" not in (link.get_attribute("class") or "")
        wait.until(_download_enabled)

        driver.find_element(By.ID, "download_site_data").click()

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
    # Site/Leg were already scoped in the request itself, but this stays as
    # a safety net (and still applies Hole filtering, which the site's own
    # form doesn't offer) in case the site's download includes more than
    # was actually asked for.
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
def _chart_scatter(df, x, y, color, cfg, invert_y):
    cc = None if color in (None,"None","") else color
    fig = (px.scatter(df, x=x, y=y, color=cc, opacity=0.75)
           .update_traces(marker=dict(size=5))
           .update_layout(**cfg))
    # Invert y-axis when depth is on Y
    if invert_y:
        fig.update_yaxes(autorange="reversed")
    return fig

def _chart_line(df, x, y, cfg, invert_y):
    fig = px.line(df, x=x, y=y).update_layout(**cfg)
    if invert_y:
        fig.update_yaxes(autorange="reversed")
    return fig

def _chart_histogram(df, x, cfg, t):
    return (px.histogram(df, x=x, nbins=40,
                         color_discrete_sequence=[t["accent"]])
            .update_layout(**cfg))

def _chart_heatmap(df, cfg):
    nc = df.select_dtypes(include="number").columns.tolist()
    if len(nc) < 2:
        return empty_fig("Need 2+ numeric columns for heatmap")
    corr = df[nc].corr().round(2)
    return (px.imshow(corr, text_auto=True, aspect="auto",
                      color_continuous_scale="RdBu_r", zmin=-1, zmax=1)
            .update_layout(**cfg, height=420))

def _chart_depthlog(df, x, curves, litho_df, show_gaps, show_qc, show_core_tops,
                    cfg, t, gap_threshold_m):
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

def make_chart(df, ctype, x, y, color, curves,
               litho_df=None, show_gaps=True, show_qc=True, show_core_tops=True,
               invert_y=True, theme="dark", gap_threshold_m=5.0):
    """Dispatches to the builder for the selected chart type. Each chart
    type has its own function (_chart_scatter, _chart_line, etc.) rather
    than living as a branch inside one large function, so each builder has
    a single, obvious job and can be read, tested, or changed on its own
    without touching the others."""
    t = THEMES.get(theme, THEMES["dark"])
    cfg = plot_cfg(theme)
    if ctype == "scatter" and x and y:
        return _chart_scatter(df, x, y, color, cfg, invert_y)
    if ctype == "line" and x and y:
        return _chart_line(df, x, y, cfg, invert_y)
    if ctype == "histogram" and x:
        return _chart_histogram(df, x, cfg, t)
    if ctype == "heatmap":
        return _chart_heatmap(df, cfg)
    if ctype == "depthlog" and x and curves:
        return _chart_depthlog(df, x, curves, litho_df, show_gaps, show_qc,
                               show_core_tops, cfg, t, gap_threshold_m)
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
def upload_dropzone(comp_id, label, accepted, icon_size="18px",
                    icon_color="var(--accent)", padding="10px",
                    margin_bottom="4px", font_size="11px", compact=False):
    """Shared dcc.Upload dropzone markup — used for the main data upload,
    the litho overlay upload, and each Post-Expedition dataset's upload,
    which previously each hand-wrote this same structure with only the
    icon size/color, label text, and accepted-formats caption differing.
    compact=True drops the icon and uses the tighter border/background
    style the Post-Expedition dataset panels use, to fit their narrower
    sidebar column."""
    if compact:
        return dcc.Upload(id=comp_id, multiple=False,
            children=html.Div([
                html.Div(label, style={"color":"var(--muted)","fontSize":"11px","textAlign":"center"}),
                html.Div(f"Accepted: {accepted}",
                         style={"color":"var(--muted)","fontSize":"9px","textAlign":"center","marginTop":"2px"}),
            ], style={"padding":"10px 0"}),
            style={"border":"1px dashed var(--border)","borderRadius":"6px",
                   "backgroundColor":"var(--bg)","cursor":"pointer","marginTop":"6px"})
    return dcc.Upload(id=comp_id, multiple=False,
        children=html.Div([
            html.Div("↑", style={"fontSize":icon_size,"color":icon_color}),
            html.Div(label),
            html.Div(f"Accepted: {accepted}",
                     style={"color":"var(--muted)","fontSize":"9px","marginTop":"2px"}),
        ], style={"textAlign":"center","color":"var(--text)","fontSize":font_size}),
        style={"border":"2px dashed var(--border)","borderRadius":"8px",
               "padding":padding,"cursor":"pointer","marginBottom":margin_bottom})

shipboard_sidebar = html.Div([
    html.P("DATA SOURCE", style=LBL),
    # Upload box lists all accepted formats including .tsv
    upload_dropzone("upload", "Drop file or click to upload",
                    ".csv  .tsv  .xlsx  .las  .zip",
                    icon_size="26px", padding="16px", margin_bottom="10px", font_size="12px"),

    # Litho upload layers on top of the main data — it doesn't replace it
    html.P("LITHO TRACK (optional, layers on chart)", style=LBL),
    html.Div("Upload a separate CSV/XLSX with top depth, bottom depth, and lithology columns. "
             "This adds a color-coded lithology lane to depth log view — it does not replace your main data file.",
             style={"color":"var(--muted)","fontSize":"9px","marginBottom":"4px","fontFamily":FONT,
                    "lineHeight":"1.4"}),
    upload_dropzone("upload-litho", "Drop litho file or click",
                    ".csv  .tsv  .xlsx  .zip",
                    icon_color="var(--accent3)"),
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
        html.Div("Pick a Leg/Expedition — no need to know which archive it "
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
            upload_dropzone(f"pe-{ds}-upload", "Drop file or click to upload",
                            ".csv  .tsv  .xlsx  .las  .zip", compact=True),
            html.Div(id=f"pe-{ds}-upload-status",
                     style={"fontSize":"10px","color":"var(--accent2)","marginTop":"4px"}),
        ]),
        html.Div(id=f"pe-{ds}-database-panel", children=[
            dcc.Dropdown(id=f"pe-{ds}-lims-exp",
                options=[{"label":e,"value":e} for e in ALL_EXPEDITIONS],
                placeholder="Leg/Expedition — pick from the list", style=DD),
            html.Div("Site and Hole populate from every known Leg/Site/Hole "
                     "combination — not from a live lookup, so they're instant "
                     "and don't depend on the data actually being in LIMS/LORE yet.",
                     style={"color":"var(--muted)","fontSize":"9px","marginTop":"6px","lineHeight":"1.4"}),
            dcc.Dropdown(id=f"pe-{ds}-lims-site", placeholder="Site — optional (all sites if blank)",
                         style={**DD,"marginTop":"4px"}),
            dcc.Dropdown(id=f"pe-{ds}-lims-hole", placeholder="Hole — optional (all holes if blank)",
                         style={**DD,"marginTop":"4px"}),
            html.P("Report type (required to Fetch)", style={**LBL,"marginTop":"10px"}),
            dcc.Dropdown(id=f"pe-{ds}-report",
                options=[{"label":v,"value":k} for k,v in ALL_REPORT_TYPES.items()],
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

def _upload_status_msg(filename, df, meta):
    msg = f"✓ {filename}  ({len(df):,} rows)"
    if meta.get("zip_member"):
        msg += f"  — using {meta['zip_member']} from the zip"
        if meta.get("zip_other_members"):
            msg += f" ({len(meta['zip_other_members'])} other CSV/TSV file(s) in the zip were not used)"
    return msg

for _ds in ["a","b"]:
    @app.callback(
        Output(f"pe-store-{_ds}","data"), Output(f"pe-{_ds}-upload-status","children"),
        Input(f"pe-{_ds}-upload","contents"), State(f"pe-{_ds}-upload","filename"),
        prevent_initial_call=True,
    )
    def pe_load_upload(contents, filename):
        df, meta = parse_upload(contents, filename)
        if "error" in meta: return None, f"Error: {meta['error']}"
        return df2j(df), _upload_status_msg(filename, df, meta)

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
        Output(f"pe-{_ds}-lims-site","options"), Output(f"pe-{_ds}-lims-site","value"),
        Output(f"pe-{_ds}-lims-hole","options",allow_duplicate=True),
        Output(f"pe-{_ds}-lims-hole","value",allow_duplicate=True),
        Input(f"pe-{_ds}-lims-exp","value"),
        prevent_initial_call=True,
    )
    def pe_site_opts(exp, ds=_ds):
        """Populates Site (and clears Hole, since it depends on Site) from
        the static reference table — instant, and independent of whether
        this Leg is actually reachable in LIMS/LORE or PANGAEA yet."""
        sites = sites_for_exp(exp)
        return [{"label":s,"value":s} for s in sites], None, [], None

    @app.callback(
        Output(f"pe-{_ds}-lims-hole","options",allow_duplicate=True),
        Output(f"pe-{_ds}-lims-hole","value",allow_duplicate=True),
        Input(f"pe-{_ds}-lims-site","value"),
        State(f"pe-{_ds}-lims-exp","value"),
        prevent_initial_call=True,
    )
    def pe_hole_opts(site, exp, ds=_ds):
        """Narrows Hole to whatever holes the reference table has for this
        Leg + Site — including '*' for legacy DSDP holes drilled before hole
        lettering existed, shown with a plain-language label."""
        holes = holes_for_exp_site(exp, site)
        return [{"label":hole_display_label(h),"value":h} for h in holes], None

    @app.callback(
        Output(f"pe-store-{_ds}","data",allow_duplicate=True),
        Output(f"pe-{_ds}-db-status","children",allow_duplicate=True),
        Output(f"pe-{_ds}-db-results","children"),
        Input(f"pe-fetch-{_ds}-database","n_clicks"),
        State(f"pe-{_ds}-report","value"), State(f"pe-{_ds}-lims-exp","value"),
        State(f"pe-{_ds}-lims-site","value"), State(f"pe-{_ds}-lims-hole","value"),
        prevent_initial_call=True,
    )
    def pe_database_fetch(n, report, exp, site, hole, ds=_ds):
        if not n or not exp:
            return None, "Enter a Leg/Expedition number", ""
        exp = str(exp).strip()

        if not report:
            return None, "Pick a report type, then Fetch", ""

        # "*" in the reference table means "this legacy DSDP hole predates
        # hole lettering" (this lab's own convention) — it isn't a real hole
        # code LIMS would recognize, so it must not be sent as a filter value.
        lore_hole = "" if hole == "*" else (hole or "")

        vessel  = vessel_for_exp(exp)
        program = program_for_exp(exp)

        # Route by which vessel actually drilled this Leg, rather than
        # trying every source for every Leg regardless of plausibility.
        # Searching PANGAEA's DSDP/ODP archive for a modern JOIDES
        # Resolution IODP expedition, for example, doesn't fail cleanly —
        # it returns loosely-relevant but wrong results (the search API
        # falls back to fuzzy relevance ranking rather than an empty
        # result when the exact Leg was never DSDP or ODP to begin with).
        try_lore   = vessel is None or (vessel == "JOIDES Resolution" and program == "IODP")
        try_dsdp   = vessel is None or vessel == "Glomar Challenger"
        try_odp    = vessel is None or (vessel == "JOIDES Resolution" and program == "ODP")
        try_msp    = vessel == "MSP"
        is_chikyu  = vessel == "Chikyu"
        report_keyword = PANGAEA_REPORT_KEYWORDS.get(report)

        if is_chikyu:
            # An official SOD data-access guide confirms Chikyu data is
            # also archived on PANGAEA (using a facet filter this app can
            # only approximate as free text, since that faceted PANGAEA
            # URL is itself a JS-rendered page a plain request can't read) —
            # worth trying before concluding there's no path at all.
            results, err = search_pangaea(
                f'"Expedition {exp}" Chikyu' + (f" {report_keyword}" if report_keyword else "")
            )
            if results:
                results = _rank_pangaea_by_leg_match(results, exp)
                if report_keyword and not any(
                    _kw_matches(r.get("label","").lower(), report_keyword.lower()) for r in results
                ):
                    results = []
            if not err and results:
                if len(results) == 1:
                    pid = results[0]["value"]
                    df, ferr = fetch_pangaea_doi(pid)
                    if not ferr and df is not None and not df.empty:
                        status = f"✓ PANGAEA (Chikyu)  {pid}  Expedition {exp}  ({len(df):,} rows)"
                        return df2j(df), status, ""
                status = (f"{len(results)} PANGAEA match(es) for Expedition {exp} (Chikyu):")
                return None, status, _pangaea_pick_list(ds, results)
            return (None,
                    f"Leg {exp} was drilled by Chikyu (JAMSTEC) — no PANGAEA match found for "
                    f"this report type either. Download the bulk export zip from JAMSTEC's "
                    f"data site and use Local file upload (zip is supported).",
                    "")

        # 1) LIMS/LORE (JR expeditions, 317+). NOTE: LORE's public interface
        # is a client-side single-page app — the page HTML it returns is an
        # empty template with the real data fetched by its own internal
        # JavaScript, not present in a plain HTTP response. A direct request
        # like this one currently cannot retrieve real LORE data; it's left
        # in place (and fails safely via the response-shape check in
        # fetch_lore) so this fallback chain is ready to work again the
        # moment that's fixed, likely via browser automation.
        if try_lore:
            df, err = fetch_lore(report, exp, site or "", lore_hole)
            if not err and df is not None and not df.empty:
                status = f"✓ LIMS/LORE  {ALL_REPORT_TYPES.get(report,report)}  Leg {exp}  ({len(df):,} rows)"
                return df2j(df), status, ""

        # 2) For DSDP-era Legs: try NGDC's direct static file first (a
        # plain HTTP request — fast and simple when it works), then
        # shinylaurel.com's Selenium-driven site as a fallback if NGDC's
        # hosting is down (its own site suggests it may be, as of last
        # check). PANGAEA is the fallback for both, not the primary DSDP
        # source, since DSDP data specifically should come from these two
        # sources when they cover the requested report type. Their actual
        # error messages are kept (not discarded) so that if this ends up
        # falling through to PANGAEA anyway, the status says why instead
        # of silently hiding that NGDC/shinylaurel were ever tried.
        ngdc_code    = NGDC_DSDP_FILE_CODES.get(report)
        dsdp_type    = SHINYLAUREL_DSDP_TYPES.get(report)
        dsdp_notes   = []
        if try_dsdp and ngdc_code:
            df, err = fetch_dsdp_ngdc(ngdc_code, exp, site, hole)
            if not err and df is not None and not df.empty:
                status = f"✓ DSDP (NGDC)  {ngdc_code}  Leg {exp}  ({len(df):,} rows)"
                return df2j(df), status, ""
            dsdp_notes.append(f"NGDC ({ngdc_code}): {err or 'no data returned'}")
        if try_dsdp and dsdp_type:
            df, err = fetch_dsdp_shinylaurel(dsdp_type, exp, site, hole)
            if not err and df is not None and not df.empty:
                status = f"✓ DSDP (shinylaurel.com)  {dsdp_type}  Leg {exp}  ({len(df):,} rows)"
                return df2j(df), status, ""
            dsdp_notes.append(f"shinylaurel.com ({dsdp_type}): {err or 'no data returned'}")
        dsdp_note_str = ("  [" + "; ".join(dsdp_notes) + "]") if dsdp_notes else ""

        # 3) PANGAEA — ODP always (shinylaurel doesn't cover ODP), DSDP only
        #    as a fallback when shinylaurel has no mapping for this report
        #    type or came up empty. report_keyword narrows toward the
        #    requested measurement type (without it, this returns every
        #    dataset type that Leg has — core photos, XRD protocols, etc.)
        projects_to_try = []
        if try_dsdp: projects_to_try.append("DSDP")
        if try_odp:  projects_to_try.append("ODP")
        for project in projects_to_try:
            results, err = search_pangaea_legacy(exp, project, report_keyword=report_keyword)
            if err or not results:
                continue
            if len(results) == 1:
                pid = results[0]["value"]
                df, ferr = fetch_pangaea_doi(pid)
                if not ferr and df is not None and not df.empty:
                    status = f"✓ PANGAEA {project}  {pid}  Leg {exp}  ({len(df):,} rows){dsdp_note_str}"
                    return df2j(df), status, ""
            status = (f"Not found in LIMS/LORE — {len(results)} PANGAEA {project} "
                      f"match(es) for Leg {exp} ({ALL_REPORT_TYPES.get(report,report)}){dsdp_note_str}:")
            return None, status, _pangaea_pick_list(ds, results)

        # 3b) MSP (Mission Specific Platform) expeditions are IODP-era but
        # not JR-operated, and are archived on PANGAEA without a DSDP/ODP
        # project tag — search by Leg/Expedition number alone instead.
        if try_msp:
            msp_query = f'"Expedition {exp}"'
            if report_keyword:
                msp_query += f" {report_keyword}"
            results, err = search_pangaea(msp_query)
            if results:
                results = _rank_pangaea_by_leg_match(results, exp)
                if report_keyword and not any(
                    _kw_matches(r.get("label","").lower(), report_keyword.lower()) for r in results
                ):
                    # Same honesty check as search_pangaea_legacy — nothing
                    # here actually confirms the requested measurement type.
                    results = []
            if not err and results:
                if len(results) == 1:
                    pid = results[0]["value"]
                    df, ferr = fetch_pangaea_doi(pid)
                    if not ferr and df is not None and not df.empty:
                        status = f"✓ PANGAEA (MSP)  {pid}  Expedition {exp}  ({len(df):,} rows)"
                        return df2j(df), status, ""
                status = (f"Not found in LIMS/LORE — {len(results)} PANGAEA "
                          f"match(es) for Expedition {exp}:")
                return None, status, _pangaea_pick_list(ds, results)

        source_note = ""
        if vessel == "JOIDES Resolution" and program == "IODP":
            source_note = " (LIMS/LORE is the right source for this Leg, but can't be reached automatically yet)"
        return (None,
                f"No data found for Leg {exp}{source_note}{dsdp_note_str}. Try Local file upload.",
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
