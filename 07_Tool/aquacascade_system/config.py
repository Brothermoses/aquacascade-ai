"""Central configuration for the AquaCascade app.

The app is multi-tenant and uses a deployment API key plus per-user bearer
tokens. New user tokens are stored only as hashes; production SSO/OAuth can
sit in front of the same role/scope model.
"""
import os
import secrets
from pathlib import Path

PKG = Path(__file__).resolve().parent
ROOT = PKG.parents[1]                              # project root
DATA = PKG / "data"
DB_PATH = DATA / "aquacascade.db"

# Database backend. SQLite is the local/dev DB (verified here). For a
# nationwide production deployment set AQUA_DB_URL to a PostgreSQL DSN
# (postgresql://user:pw@host/db); the code is written dialect-portable.
DB_URL = os.environ.get("AQUA_DB_URL", "")          # "" -> local SQLite
DB_DIALECT = "postgres" if DB_URL.startswith(
    ("postgres://", "postgresql://")) else "sqlite"

# runtime/security mode
APP_ENV = os.environ.get("AQUA_ENV", "local").lower()
DEMO_MODE = os.environ.get("AQUA_DEMO_MODE", "1").lower() not in (
    "0", "false", "no")
ALLOW_QUERY_TOKENS = os.environ.get("AQUA_ALLOW_QUERY_TOKENS", "0").lower() \
    in ("1", "true", "yes")
SESSION_COOKIE_SECURE = os.environ.get("AQUA_COOKIE_SECURE", "0").lower() \
    in ("1", "true", "yes")

# triage model output (system-level priority) produced by 05_Modeling.
# The production calibrated script writes triage_production_ranking.csv;
# fall back to the older uncalibrated ranking only for old local workspaces.
PRODUCTION_RANKING_CSV = ROOT / "05_Modeling" / "triage_production_ranking.csv"
LEGACY_RANKING_CSV = ROOT / "05_Modeling" / "unknown_triage_ranking.csv"
_ranking_env = os.environ.get("AQUA_RANKING_CSV")
RANKING_CSV = Path(_ranking_env) if _ranking_env else (
    PRODUCTION_RANKING_CSV if PRODUCTION_RANKING_CSV.exists()
    else LEGACY_RANKING_CSV)

# planning assumptions (explicit, parametrised — never measured savings)
DEFAULT_REPLACE_COST = 4700.0
DEFAULT_INVESTIGATE_COST = 200.0
DEFAULT_HORIZON_YEARS = 10
EQUITY_MULTIPLIER = 2.0

# Post-LCRI asset lifecycle: once lead is gone the system stays useful
# by flagging non-lead pipe that has passed its expected service life.
# Explicit ASSUMPTION (engineering rule-of-thumb), not a measured
# failure model — stated honestly in the UI/Guide.
ASSET_SERVICE_LIFE_YEARS = 75            # default expected service life

# Interactive map tiles. The default is the public OpenStreetMap standard
# raster tile service for normal interactive viewing. Production operators
# can point this at their own OSM-derived provider or self-hosted tiles.
MAP_TILE_URL = os.environ.get(
    "AQUA_MAP_TILE_URL",
    "https://tile.openstreetmap.org/{z}/{x}/{y}.png")
MAP_TILE_ATTRIBUTION = os.environ.get(
    "AQUA_MAP_TILE_ATTRIBUTION",
    '&copy; <a href="https://www.openstreetmap.org/copyright">'
    'OpenStreetMap</a> contributors')

# role -> work-order types that role may see/work
ROLE_TYPES = {
    "INSPECTOR": ("INSPECTION",),
    "REHAB": ("REHAB", "RENEWAL"),
    "DISPATCHER": ("INSPECTION", "REHAB", "RENEWAL"),
    "ADMIN": ("INSPECTION", "REHAB", "RENEWAL"),
}

# --- API key: persisted to data/api_key.txt so it is stable across runs
_KEY_FILE = DATA / "api_key.txt"
_SECRET_FILE = DATA / "secret_key.txt"

# Keep imports bounded. This is deliberately conservative for a local tool;
# operators can raise it for larger utility files.
MAX_IMPORT_BYTES = int(os.environ.get("AQUA_MAX_IMPORT_BYTES", 25 * 1024 * 1024))


def get_api_key() -> str:
    env = os.environ.get("AQUA_API_KEY")
    if env:
        return env
    DATA.mkdir(exist_ok=True)
    if _KEY_FILE.exists():
        return _KEY_FILE.read_text().strip()
    k = "aqua_" + secrets.token_hex(16)
    _KEY_FILE.write_text(k)
    return k


def get_secret_key() -> str:
    """Flask session signing secret.

    Production deployments should set AQUA_SECRET_KEY. For local/offline
    operation we persist a generated key so sessions survive restarts without
    keeping a hardcoded secret in source.
    """
    env = os.environ.get("AQUA_SECRET_KEY")
    if env:
        return env
    DATA.mkdir(exist_ok=True)
    if _SECRET_FILE.exists():
        return _SECRET_FILE.read_text().strip()
    k = "aqua_session_" + secrets.token_urlsafe(32)
    _SECRET_FILE.write_text(k)
    return k
