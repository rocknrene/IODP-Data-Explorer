"""Entry point for deployment (Hugging Face Spaces, gunicorn, or ``python app.py``).

``server`` is the WSGI application, e.g. ``gunicorn app:server``.
"""

import os

from sod_explorer.app import create_app

app = create_app()
server = app.server

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 7860)), debug=False)
