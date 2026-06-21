"""Flask application factory. Wires the REST API (/api/v1) and the
server-rendered UI on the same backend + SQLite database.

Run:  python -m aquacascade_system.app      (from 07_Tool/)
 or:  python 07_Tool/aquacascade_system/app.py
Open: http://127.0.0.1:5000
"""
import os
import sys
from pathlib import Path
from flask import Flask

if __package__ in (None, ""):                       # allow direct run
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from aquacascade_system.db import init_db
    from aquacascade_system.api import api
    from aquacascade_system.web import web
    from aquacascade_system.config import (
        get_api_key, get_secret_key, SESSION_COOKIE_SECURE,
        MAX_IMPORT_BYTES)
else:
    from .db import init_db
    from .api import api
    from .web import web
    from .config import (
        get_api_key, get_secret_key, SESSION_COOKIE_SECURE,
        MAX_IMPORT_BYTES)


def create_app():
    app = Flask(__name__, template_folder="templates",
                static_folder="static")
    app.secret_key = get_secret_key()
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=SESSION_COOKIE_SECURE,
        MAX_CONTENT_LENGTH=MAX_IMPORT_BYTES,
    )
    app.register_blueprint(api)
    app.register_blueprint(web)
    init_db()
    return app


app = create_app()

if __name__ == "__main__":
    print("AquaCascade System -> http://127.0.0.1:5000  (Ctrl+C)")
    if os.environ.get("AQUA_SHOW_SECRETS") == "1":
        print("API key (X-API-Key header):", get_api_key())
    else:
        # Touch the key so local first-run behavior still creates it.
        get_api_key()
        print("API key loaded from AQUA_API_KEY or local data/api_key.txt.")
    app.run(host="127.0.0.1", port=5000, debug=False)
