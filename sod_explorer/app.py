"""Application factory for the SOD Explorer web interface."""

from __future__ import annotations

import flask
from dash import Dash

from .callbacks import register_callbacks
from .layout import INDEX_STRING, build_layout


def create_app(server: flask.Flask | None = None) -> Dash:
    """Create the Dash application.

    Parameters
    ----------
    server
        Existing Flask server to attach to. A new server is created if
        omitted.
    """
    server = server or flask.Flask(__name__)
    app = Dash(__name__, server=server, title="SOD Explorer")
    app.index_string = INDEX_STRING
    app.layout = build_layout()
    register_callbacks(app)
    return app
