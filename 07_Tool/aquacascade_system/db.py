"""Database layer - dialect-portable.

SQLite is the local/dev backend and is the one VERIFIED here (tests +
live smoke). PostgreSQL is the production target for nationwide scale and
concurrency: the schema and a parameter translator are written for both,
selected by AQUA_DB_URL. The Postgres path is covered by Docker Compose
smoke tests; broader load/performance testing remains deployment work.
"""
import sqlite3
from contextlib import contextmanager
from .config import DB_PATH, DATA, DB_URL, DB_DIALECT
from .auth import hash_token, token_label

# ---- dialect-specific DDL fragments ----
if DB_DIALECT == "postgres":
    PK = "BIGSERIAL PRIMARY KEY"
    NOW = "now()"
    TS = "TIMESTAMPTZ"
else:
    PK = "INTEGER PRIMARY KEY AUTOINCREMENT"
    NOW = "(datetime('now'))"
    TS = "TEXT"

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS tenants(
  id {PK},
  name TEXT NOT NULL,
  kind TEXT NOT NULL,                       -- UTILITY | STATE | NATIONAL
  scope_key TEXT NOT NULL,                  -- pwsid | 2-letter state | *
  created_at {TS} DEFAULT {NOW});

CREATE TABLE IF NOT EXISTS users(
  id {PK},
  tenant_id BIGINT NOT NULL REFERENCES tenants(id),
  name TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'INSPECTOR',   -- INSPECTOR | REHAB | DISPATCHER | ADMIN
  token TEXT UNIQUE NOT NULL,
  active INTEGER DEFAULT 1);

CREATE TABLE IF NOT EXISTS water_systems(
  pwsid TEXT PRIMARY KEY, name TEXT, state TEXT,
  epa_region TEXT, model_rank INTEGER, p_lead_rich REAL);

CREATE TABLE IF NOT EXISTS service_lines(
  id {PK},
  external_line_id TEXT NOT NULL, pwsid TEXT, state TEXT,
  location TEXT, latitude REAL, longitude REAL, geometry TEXT,
  install_year TEXT, current_status TEXT,
  expected_service_life_years TEXT, replacement_year TEXT,
  diameter_in TEXT, length_ft TEXT, ownership_side TEXT,
  verification_method TEXT, evidence_source TEXT, confidence_score TEXT,
  imported_at {TS} DEFAULT {NOW},
  UNIQUE(pwsid, external_line_id));

CREATE TABLE IF NOT EXISTS work_orders(
  id {PK},
  line_id BIGINT NOT NULL REFERENCES service_lines(id),
  pwsid TEXT, state TEXT,
  type TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'OPEN',
  priority_rank INTEGER, p_lead_rich REAL,
  rehab_year INTEGER, due_year INTEGER,
  assigned_to BIGINT REFERENCES users(id),
  created_at {TS} DEFAULT {NOW},
  updated_at {TS} DEFAULT {NOW});

CREATE TABLE IF NOT EXISTS investigations(
  work_order_id BIGINT PRIMARY KEY REFERENCES work_orders(id),
  line_id BIGINT NOT NULL, pwsid TEXT, state TEXT,
  system_side_material TEXT, customer_side_material TEXT,
  overall_classification TEXT, basis_of_classification TEXT,
  install_year TEXT, investigation_method TEXT,
  investigation_date TEXT, inspector TEXT, investigation_cost TEXT,
  predicted_p TEXT, predicted_label TEXT, confirmed_status TEXT,
  disadvantaged_community_flag TEXT, notes TEXT, photo_ref TEXT,
  created_at {TS} DEFAULT {NOW});

CREATE TABLE IF NOT EXISTS rehab_plans(
  id {PK}, scope TEXT, params_json TEXT, summary_json TEXT,
  created_at {TS} DEFAULT {NOW});

CREATE TABLE IF NOT EXISTS rehab_plan_items(
  plan_id BIGINT REFERENCES rehab_plans(id),
  line_id BIGINT, pwsid TEXT, state TEXT, classification TEXT,
  year INTEGER, cost REAL, weight REAL, disadvantaged INTEGER);

CREATE TABLE IF NOT EXISTS events(
  id {PK}, ts {TS} DEFAULT {NOW}, kind TEXT, detail TEXT);

CREATE TABLE IF NOT EXISTS system_inventory(
  pwsid TEXT PRIMARY KEY,
  pws_name TEXT,
  state TEXT,
  source_quarter TEXT,
  lead_count INTEGER DEFAULT 0,
  grr_count INTEGER DEFAULT 0,
  unknown_count INTEGER DEFAULT 0,
  nonlead_count INTEGER DEFAULT 0,
  total_count INTEGER DEFAULT 0,
  report_status TEXT,
  pws_type TEXT,
  activity_status TEXT,
  primacy_agency TEXT,
  epa_region TEXT,
  population_served INTEGER,
  latitude REAL,
  longitude REAL,
  inventory_source TEXT,
  updated_at {TS} DEFAULT {NOW});

CREATE INDEX IF NOT EXISTS ix_sl_state ON service_lines(state);
CREATE INDEX IF NOT EXISTS ix_sl_pws ON service_lines(pwsid);
CREATE INDEX IF NOT EXISTS ix_sl_geo ON service_lines(latitude,longitude);
CREATE INDEX IF NOT EXISTS ix_wo_scope
  ON work_orders(state, status, type, priority_rank);
CREATE INDEX IF NOT EXISTS ix_ws_state ON water_systems(state);
CREATE INDEX IF NOT EXISTS ix_inv_state ON system_inventory(state);
CREATE INDEX IF NOT EXISTS ix_inv_geo ON system_inventory(latitude,longitude);
"""

MIGRATIONS = (
    ("001_initial_schema", SCHEMA),
    ("002_user_token_hashes", None),
    ("003_service_line_geo", None),
    ("004_service_line_geometry", None),
    ("005_system_inventory", None),
    ("006_service_line_lifecycle", None),
)


class Conn:
    """Thin wrapper giving one API over sqlite3 / psycopg. SQL uses '?'
    placeholders; translated to '%s' for Postgres."""

    def __init__(self):
        if DB_DIALECT == "postgres":
            import psycopg                       # lazy; prod only
            from psycopg.rows import dict_row
            self._c = psycopg.connect(DB_URL, row_factory=dict_row)
            self._pg = True
        else:
            DATA.mkdir(exist_ok=True)
            self._c = sqlite3.connect(DB_PATH, timeout=30)
            self._c.row_factory = sqlite3.Row
            self._c.execute("PRAGMA journal_mode=WAL")
            self._c.execute("PRAGMA foreign_keys=ON")
            self._pg = False

    def execute(self, sql, params=()):
        if self._pg:
            sql = sql.replace("?", "%s")
        cur = self._c.execute(sql, params)
        return cur

    def insert_id(self, sql, params=()):
        """Insert and return the new row id, portably."""
        if self._pg:
            if "returning" not in sql.lower():
                sql = sql.rstrip().rstrip(";") + " RETURNING id"
            cur = self.execute(sql, params)
            return cur.fetchone()["id"]
        cur = self.execute(sql, params)
        return cur.lastrowid

    def executescript(self, script):
        if self._pg:
            for stmt in script.split(";"):
                stmt = stmt.strip()
                if stmt:
                    self._c.execute(stmt)
        else:
            self._c.executescript(script)

    def commit(self):
        self._c.commit()

    def rollback(self):
        self._c.rollback()

    def close(self):
        self._c.close()


@contextmanager
def tx():
    con = Conn()
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def migrate_db():
    """Apply idempotent, version-recorded schema migrations."""
    with tx() as con:
        con.execute(f"""CREATE TABLE IF NOT EXISTS schema_migrations(
            version TEXT PRIMARY KEY,
            applied_at {TS} DEFAULT CURRENT_TIMESTAMP)""")
        rows = con.execute("SELECT version FROM schema_migrations").fetchall()
        applied = {r["version"] for r in rows}
        for version, script in MIGRATIONS:
            if version in applied:
                continue
            if version == "002_user_token_hashes":
                _migration_002_user_token_hashes(con)
            elif version == "003_service_line_geo":
                _migration_003_service_line_geo(con)
            elif version == "004_service_line_geometry":
                _migration_004_service_line_geometry(con)
            elif version == "005_system_inventory":
                _migration_005_system_inventory(con)
            elif version == "006_service_line_lifecycle":
                _migration_006_service_line_lifecycle(con)
            else:
                con.executescript(script)
            con.execute("INSERT INTO schema_migrations(version) VALUES(?)",
                        (version,))


def _column_names(con, table):
    if DB_DIALECT == "postgres":
        rows = con.execute("""SELECT column_name name
            FROM information_schema.columns
            WHERE table_name=?""", (table,)).fetchall()
        return {r["name"] for r in rows}
    rows = con.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}


def _add_column(con, table, column, ddl):
    if column not in _column_names(con, table):
        con.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def _migration_002_user_token_hashes(con):
    _add_column(con, "users", "token_hash", "token_hash TEXT")
    _add_column(con, "users", "token_label", "token_label TEXT")
    _add_column(con, "users", "token_issued_at", f"token_issued_at {TS}")
    _add_column(con, "users", "token_last_used_at",
                f"token_last_used_at {TS}")
    con.execute("""CREATE UNIQUE INDEX IF NOT EXISTS ix_users_token_hash
        ON users(token_hash) WHERE token_hash IS NOT NULL""")
    rows = con.execute("""SELECT id,token FROM users
        WHERE token_hash IS NULL OR token_hash=''""").fetchall()
    for r in rows:
        con.execute("""UPDATE users SET token_hash=?,token_label=?,
            token_issued_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (hash_token(r["token"]), token_label(r["token"]),
                     r["id"]))


def _migration_003_service_line_geo(con):
    _add_column(con, "service_lines", "latitude", "latitude REAL")
    _add_column(con, "service_lines", "longitude", "longitude REAL")
    con.execute("""CREATE INDEX IF NOT EXISTS ix_sl_geo
        ON service_lines(latitude,longitude)""")


def _migration_004_service_line_geometry(con):
    _add_column(con, "service_lines", "geometry", "geometry TEXT")


def _migration_005_system_inventory(con):
    con.execute(f"""CREATE TABLE IF NOT EXISTS system_inventory(
      pwsid TEXT PRIMARY KEY,
      pws_name TEXT,
      state TEXT,
      source_quarter TEXT,
      lead_count INTEGER DEFAULT 0,
      grr_count INTEGER DEFAULT 0,
      unknown_count INTEGER DEFAULT 0,
      nonlead_count INTEGER DEFAULT 0,
      total_count INTEGER DEFAULT 0,
      report_status TEXT,
      pws_type TEXT,
      activity_status TEXT,
      primacy_agency TEXT,
      epa_region TEXT,
      population_served INTEGER,
      latitude REAL,
      longitude REAL,
      inventory_source TEXT,
      updated_at {TS} DEFAULT {NOW})""")
    con.execute("""CREATE INDEX IF NOT EXISTS ix_inv_state
        ON system_inventory(state)""")
    con.execute("""CREATE INDEX IF NOT EXISTS ix_inv_geo
        ON system_inventory(latitude,longitude)""")


def _migration_006_service_line_lifecycle(con):
    _add_column(con, "service_lines", "expected_service_life_years",
                "expected_service_life_years TEXT")
    _add_column(con, "service_lines", "replacement_year",
                "replacement_year TEXT")
    _add_column(con, "service_lines", "diameter_in", "diameter_in TEXT")
    _add_column(con, "service_lines", "length_ft", "length_ft TEXT")
    _add_column(con, "service_lines", "ownership_side",
                "ownership_side TEXT")
    _add_column(con, "service_lines", "verification_method",
                "verification_method TEXT")
    _add_column(con, "service_lines", "evidence_source",
                "evidence_source TEXT")
    _add_column(con, "service_lines", "confidence_score",
                "confidence_score TEXT")


def seed_db():
    """Seed the local demo/admin identities idempotently."""
    with tx() as con:
        n = con.execute("SELECT COUNT(*) c FROM tenants").fetchone()["c"]
        if n == 0:
            con.execute("INSERT INTO tenants(name,kind,scope_key) "
                        "VALUES(?,?,?)",
                        ("National (EPA/Program view)", "NATIONAL", "*"))
            tid = con.execute(
                "SELECT id FROM tenants WHERE kind='NATIONAL'"
                ).fetchone()["id"]
            for nm, role, tok in (
                ("National Admin", "ADMIN", "admin-national-001"),
                ("Dispatcher 1", "DISPATCHER", "disp-demo-001"),
                ("Inspector 1", "INSPECTOR", "insp-demo-001"),
                ("Replacement Crew 1", "REHAB", "rehab-demo-001"),
                ("Field Tech 1", "INSPECTOR", "tech-demo-001"),
            ):
                con.execute("""INSERT INTO users(tenant_id,name,role,token,
                    token_hash,token_label,token_issued_at)
                    VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
                            (tid, nm, role, tok, hash_token(tok),
                             token_label(tok)))


def init_db():
    migrate_db()
    seed_db()


def db_info():
    with tx() as con:
        rows = con.execute("""SELECT version, applied_at
            FROM schema_migrations ORDER BY version""").fetchall()
        return {"dialect": DB_DIALECT, "path": str(DB_PATH),
                "migrations": [dict(r) for r in rows]}


def log_event(con, kind, detail=""):
    con.execute("INSERT INTO events(kind,detail) VALUES(?,?)",
                (kind, str(detail)[:500]))
