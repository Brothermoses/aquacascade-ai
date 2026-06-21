"""Production-ish WSGI entrypoint using Waitress.

Run from 07_Tool:
  python -m aquacascade_system.serve
"""
import os

from waitress import serve

from .app import app


def main():
    host = os.environ.get("AQUA_HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", os.environ.get("AQUA_PORT", "5000")))
    threads = int(os.environ.get("AQUA_THREADS", "4"))
    serve(app, host=host, port=port, threads=threads)


if __name__ == "__main__":
    main()
