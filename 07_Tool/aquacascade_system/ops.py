"""Operational helpers for database backup/restore."""
import sqlite3
from contextlib import closing
from pathlib import Path

from .config import DB_DIALECT, DB_PATH, DATA
from .db import migrate_db


def _require_sqlite():
    if DB_DIALECT != "sqlite":
        raise NotImplementedError(
            "AQUA_DB_URL is PostgreSQL; use pg_dump/pg_restore for backups")


def _validate_sqlite(path):
    with closing(sqlite3.connect(path)) as con:
        ok = con.execute("PRAGMA quick_check").fetchone()[0]
        if ok.lower() != "ok":
            raise ValueError(f"SQLite quick_check failed: {ok}")
        con.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()


def backup_sqlite(output_path):
    """Create a consistent SQLite backup using the sqlite backup API."""
    _require_sqlite()
    migrate_db()
    out = Path(output_path).expanduser().resolve()
    if out == DB_PATH.resolve():
        raise ValueError("backup path cannot be the live database path")
    out.parent.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(exist_ok=True)
    with closing(sqlite3.connect(DB_PATH)) as src, \
            closing(sqlite3.connect(out)) as dst:
        src.backup(dst)
    _validate_sqlite(out)
    return {"path": str(out), "bytes": out.stat().st_size}


def restore_sqlite(input_path):
    """Restore the live SQLite DB from a validated SQLite backup."""
    _require_sqlite()
    src_path = Path(input_path).expanduser().resolve()
    if not src_path.exists():
        raise FileNotFoundError(str(src_path))
    if src_path == DB_PATH.resolve():
        raise ValueError("restore path cannot be the live database path")
    _validate_sqlite(src_path)
    DATA.mkdir(exist_ok=True)
    with closing(sqlite3.connect(src_path)) as src, \
            closing(sqlite3.connect(DB_PATH)) as dst:
        src.backup(dst)
    for suffix in ("-wal", "-shm"):
        p = Path(str(DB_PATH) + suffix)
        if p.exists():
            p.unlink()
    migrate_db()
    return {"path": str(DB_PATH), "restored_from": str(src_path)}
