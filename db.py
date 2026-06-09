# -*- coding: utf-8 -*-
"""SQLite layer for the white-label CRM: schema + generic CRUD helpers.

Patterned after SeaBreeze_Ops/db.py but generalized so every module shares the
same insert/update/get helpers. All brandable values live in `company_settings`.
"""
import os
import json
import sqlite3
from datetime import datetime

import config

DB_PATH = config.DB_PATH


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today():
    return datetime.now().strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Dual engine: SQLite locally, Postgres (Neon) when DATABASE_URL is set.
# A thin wrapper makes a psycopg connection behave like the sqlite3 API the rest
# of db.py uses (? placeholders, .execute/.executescript/.commit/.close, dict rows).
# ---------------------------------------------------------------------------
_PG_URL = (os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")
           or os.environ.get("POSTGRES_PRISMA_URL"))
# Some hosts/paste-ins wrap the value in quotes or inject a UTF-8 BOM / CRLF,
# which breaks psycopg's conninfo parser. A valid URL has no whitespace, so scrub
# every whitespace char (incl. BOM) and surrounding quotes.
if _PG_URL:
    import re as _re
    _PG_URL = _PG_URL.strip().strip('"').strip("'")
    _PG_URL = _PG_URL.replace("\\r", "").replace("\\n", "").replace("\\t", "")  # literal escapes
    _PG_URL = _re.sub(r"\s", "", _PG_URL).replace("﻿", "")                       # real ws + BOM
IS_PG = bool(_PG_URL)

if IS_PG:
    import psycopg
    from psycopg.rows import dict_row


def _pg_ddl(sql):
    """Translate the SQLite DDL we use into Postgres."""
    return sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")


def _split_sql(script):
    return [s.strip() for s in script.split(";") if s.strip()]


class _PgConn:
    """Adapt a psycopg connection to the sqlite3 calling convention db.py expects."""

    def __init__(self, raw):
        self._raw = raw

    def execute(self, sql, params=()):
        cur = self._raw.cursor(row_factory=dict_row)
        cur.execute(_pg_ddl(sql).replace("?", "%s"), tuple(params))
        return cur

    def executescript(self, script):
        cur = self._raw.cursor()
        for stmt in _split_sql(script):
            cur.execute(_pg_ddl(stmt))
        return cur

    def commit(self):
        self._raw.commit()

    def close(self):
        self._raw.close()


def connect():
    if IS_PG:
        return _PgConn(psycopg.connect(_PG_URL))
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    # WAL + a busy timeout let several gunicorn workers share one SQLite file safely
    # (concurrent readers, serialized writers) — what makes single-host hosting viable.
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=8000")
        conn.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS company_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    name TEXT, legal_name TEXT, tagline TEXT,
    license TEXT, qualifier TEXT,
    address TEXT, city TEXT, state TEXT, zip TEXT,
    phone TEXT, email TEXT, website TEXT,
    logo_path TEXT,
    color_primary TEXT DEFAULT '#2f6df6',
    color_accent  TEXT DEFAULT '#0f9e82',
    color_warn    TEXT DEFAULT '#d98200',
    color_danger  TEXT DEFAULT '#d93a36',
    default_county TEXT DEFAULT 'Palm Beach County',
    departments TEXT,
    terms TEXT, updated TEXT
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created TEXT, name TEXT, email TEXT, phone TEXT,
    role TEXT DEFAULT 'sales',   -- admin | sales | production | office
    active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created TEXT, updated TEXT,
    kind TEXT DEFAULT 'person',  -- person | company
    first_name TEXT, last_name TEXT, company TEXT,
    email TEXT, phone TEXT,
    address TEXT, city TEXT, state TEXT, zip TEXT,
    source TEXT, tags TEXT, notes TEXT,
    department TEXT
);

CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created TEXT, updated TEXT,
    contact_id INTEGER,
    rid TEXT, name TEXT, phone TEXT, email TEXT, address TEXT,
    work_type TEXT, rep TEXT, source TEXT,
    stage TEXT DEFAULT 'new',
    stage_since TEXT, last_contact TEXT, next_follow TEXT, snooze_until TEXT,
    estimate TEXT, narrative TEXT, todo TEXT, notes TEXT,
    checks TEXT DEFAULT '{}',
    external_url TEXT, department TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created TEXT, updated TEXT,
    contact_id INTEGER, lead_id INTEGER,
    rid TEXT, name TEXT, phone TEXT, email TEXT,
    address TEXT, city TEXT, state TEXT, zip TEXT,
    work_type TEXT, rep TEXT, source TEXT,
    stage TEXT DEFAULT 'approved', department TEXT,
    stage_since TEXT, next_follow TEXT, snooze_until TEXT,
    contract_value TEXT, narrative TEXT, todo TEXT, notes TEXT,
    checks TEXT DEFAULT '{}', payments TEXT DEFAULT '{}',
    pcn TEXT, legal TEXT, county TEXT, ahj TEXT, system TEXT,
    existing TEXT, area TEXT, slope TEXT, mrh TEXT, exposure TEXT,
    permit_file TEXT, external_url TEXT
);

CREATE TABLE IF NOT EXISTS activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created TEXT,
    entity_type TEXT,   -- lead | job | contact
    entity_id INTEGER,
    kind TEXT,          -- note | stage | task | call | email | sms | automation
    text TEXT, due TEXT, done INTEGER DEFAULT 0,
    assignee TEXT
);

CREATE TABLE IF NOT EXISTS estimates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created TEXT, updated TEXT,
    number TEXT, title TEXT,
    job_id INTEGER, lead_id INTEGER, contact_id INTEGER,
    work_type TEXT, template_key TEXT,
    status TEXT DEFAULT 'draft',  -- draft | sent | signed | declined
    markup_pct REAL DEFAULT 0, tax_pct REAL DEFAULT 0,
    notes TEXT, terms TEXT,
    signed_name TEXT, signed_at TEXT, signature TEXT,
    pdf_file TEXT
);

CREATE TABLE IF NOT EXISTS estimate_sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    estimate_id INTEGER, sort INTEGER DEFAULT 0,
    name TEXT, scope_text TEXT, margin_pct REAL DEFAULT 30
);

CREATE TABLE IF NOT EXISTS estimate_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    estimate_id INTEGER, section_id INTEGER, sort INTEGER DEFAULT 0,
    description TEXT, unit TEXT, qty REAL DEFAULT 0,
    waste_pct REAL DEFAULT 0, cost REAL DEFAULT 0, price REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created TEXT, job_id INTEGER, lead_id INTEGER,
    category TEXT, filename TEXT, original_name TEXT, size INTEGER, notes TEXT
);

CREATE TABLE IF NOT EXISTS photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created TEXT, job_id INTEGER, album TEXT, phase TEXT,
    caption TEXT, filename TEXT, original_name TEXT
);

CREATE TABLE IF NOT EXISTS appointments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created TEXT, title TEXT, kind TEXT,
    start_at TEXT, end_at TEXT,
    lead_id INTEGER, job_id INTEGER, contact_id INTEGER,
    assignee TEXT, location TEXT, notes TEXT, reminder TEXT
);

CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created TEXT, job_id INTEGER, number TEXT,
    status TEXT DEFAULT 'unpaid',  -- unpaid | partial | paid
    draw_key TEXT, amount REAL DEFAULT 0,
    due_date TEXT, paid_date TEXT, notes TEXT
);

CREATE TABLE IF NOT EXISTS materials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created TEXT, job_id INTEGER, supplier TEXT,
    status TEXT DEFAULT 'draft',  -- draft | ordered | delivered
    items TEXT DEFAULT '[]', notes TEXT,
    ordered_date TEXT, delivery_date TEXT
);

CREATE TABLE IF NOT EXISTS measurements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created TEXT, updated TEXT,
    job_id INTEGER, lead_id INTEGER,
    squares REAL DEFAULT 0, pitch TEXT, stories TEXT,
    ridge_lf REAL DEFAULT 0, hip_lf REAL DEFAULT 0, valley_lf REAL DEFAULT 0,
    rake_lf REAL DEFAULT 0, eave_lf REAL DEFAULT 0, step_flash_lf REAL DEFAULT 0,
    facets INTEGER DEFAULT 0, waste_pct REAL DEFAULT 15,
    source TEXT DEFAULT 'RoofGraf', report_file TEXT, notes TEXT
);

CREATE TABLE IF NOT EXISTS templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created TEXT, updated TEXT,
    tkey TEXT, name TEXT, work_type TEXT,
    scope_text TEXT, lines TEXT DEFAULT '[]',
    is_builtin INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS permits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created TEXT, job_id INTEGER,
    ahj TEXT, county TEXT, system TEXT,
    status TEXT DEFAULT 'prep',  -- prep | submitted | approved | closed
    permit_number TEXT, submitted_date TEXT, approved_date TEXT,
    packet_file TEXT, notes TEXT
);

CREATE TABLE IF NOT EXISTS worksheets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created TEXT, updated TEXT,
    job_id INTEGER, contract_value REAL DEFAULT 0,
    seeded_from_estimate INTEGER, notes TEXT
);

CREATE TABLE IF NOT EXISTS worksheet_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    worksheet_id INTEGER, sort INTEGER DEFAULT 0,
    category TEXT DEFAULT 'Material',  -- Material | Labor | Permit | Overhead | Other
    description TEXT, budget_cost REAL DEFAULT 0, actual_cost REAL DEFAULT 0,
    qty REAL DEFAULT 0, unit TEXT, unit_cost REAL DEFAULT 0  -- AccuLynx-style breakdown
);

CREATE TABLE IF NOT EXISTS vendors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created TEXT, name TEXT, type TEXT, phone TEXT, email TEXT, address TEXT
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created TEXT, updated TEXT, job_id INTEGER,
    type TEXT DEFAULT 'Material',  -- Material | Labor
    vendor TEXT, po_number TEXT,
    status TEXT DEFAULT 'draft',  -- draft | ordered | delivered | received
    ordered_date TEXT, delivery_date TEXT, notes TEXT, department TEXT
);

CREATE TABLE IF NOT EXISTS order_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER, sort INTEGER DEFAULT 0,
    description TEXT, unit TEXT, qty REAL DEFAULT 0, cost REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS automations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created TEXT, name TEXT,
    trigger_stage TEXT,
    action_type TEXT DEFAULT 'create_task',  -- create_task | draft_email | create_reminder
    template_text TEXT, offset_days INTEGER DEFAULT 0,
    active INTEGER DEFAULT 1
);
"""


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = connect()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    _ensure_column("company_settings", "departments", "TEXT")
    _ensure_column("company_settings", "color_masthead", "TEXT DEFAULT '#24476C'")
    _ensure_column("company_settings", "brand_short", "TEXT")  # masthead short name (e.g. SRSMI); falls back to full name
    _ensure_column("company_settings", "gmail_url", "TEXT")  # team inbox quick-link
    _ensure_column("company_settings", "sitecam_url", "TEXT")  # embedded field-photo app (SiteCam)
    # Department scoping (REROOF vs Service vs Warranties).
    for _t in ("leads", "jobs", "contacts"):
        _ensure_column(_t, "department", "TEXT")
    # Permit info carried on the lead from entry (AHJ auto-resolved from address).
    for _c in ("ahj", "county", "system", "city"):
        _ensure_column("leads", _c, "TEXT")
    # Reusable customer signature (captured once at e-sign; applied to proposal,
    # sign-up docs, and permit packet) — stored on both leads and jobs.
    for _t in ("leads", "jobs"):
        for _c in ("signature", "signed_name", "signed_at", "sign_consent"):
            _ensure_column(_t, _c, "TEXT")
    # Backfill: any pre-existing record with no department belongs to REROOF.
    for _t in ("leads", "jobs", "contacts"):
        execute("UPDATE %s SET department='REROOF Department' WHERE department IS NULL OR department=''" % _t)
    # Invoicing + QuickBooks fields.
    for _c, _d in [("qbo_id", "TEXT"), ("payment_link", "TEXT"), ("customer_email", "TEXT"),
                   ("sent_at", "TEXT"), ("department", "TEXT")]:
        _ensure_column("invoices", _c, _d)
    _ensure_integrations_table()
    # AccuLynx-style worksheet line breakdown (Qty | Unit | Unit Cost | Cost).
    for _c, _d in [("qty", "REAL DEFAULT 0"), ("unit", "TEXT"), ("unit_cost", "REAL DEFAULT 0")]:
        _ensure_column("worksheet_lines", _c, _d)
    _ensure_column("company_settings", "labels", "TEXT")  # JSON map of phrase -> custom label
    _ensure_change_requests_table()
    _ensure_library_table()
    _seed_if_empty()
    _migrate_columns()  # idempotent; run unconditionally so renamed/added columns land
    _migrate_stages()
    # Default departments for any existing company row that lacks them.
    co = get_company()
    if co and not (co.get("departments") or "").strip():
        save_company({"departments": "REROOF Department, Service Department, Warranties"})


def _ensure_library_table():
    """Company Document Library — reusable docs (warranties, brochures, licenses,
    permit packages, sign-up packages, cheat sheets) with category + AHJ/system tags."""
    conn = connect()
    conn.execute("""CREATE TABLE IF NOT EXISTS library_docs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created TEXT, filename TEXT, original_name TEXT,
        category TEXT, ahj TEXT, system TEXT, tags TEXT,
        size INTEGER, notes TEXT)""")
    conn.commit()
    conn.close()


def _ensure_change_requests_table():
    """Log of white-label customization requests typed in the Customize box."""
    conn = connect()
    conn.execute("""CREATE TABLE IF NOT EXISTS change_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created TEXT, requested_by TEXT, raw_text TEXT,
        status TEXT DEFAULT 'queued',   -- applied | partial | queued
        result TEXT)""")
    conn.commit()
    conn.close()


def _ensure_integrations_table():
    """Single-row table holding QuickBooks Online OAuth config + tokens."""
    conn = connect()
    conn.execute("""CREATE TABLE IF NOT EXISTS integrations (
        id INTEGER PRIMARY KEY CHECK (id=1),
        qbo_client_id TEXT, qbo_client_secret TEXT, qbo_environment TEXT DEFAULT 'production',
        qbo_realm_id TEXT, qbo_access_token TEXT, qbo_refresh_token TEXT,
        qbo_token_expiry TEXT, qbo_connected_at TEXT, qbo_redirect_uri TEXT,
        updated TEXT)""")
    conn.execute("INSERT INTO integrations (id) VALUES (1) ON CONFLICT (id) DO NOTHING"
                 if IS_PG else "INSERT OR IGNORE INTO integrations (id) VALUES (1)")
    conn.commit()
    conn.close()


def get_integrations():
    conn = connect()
    row = conn.execute("SELECT * FROM integrations WHERE id=1").fetchone()
    conn.close()
    return dict(row) if row else {}


def save_integrations(data):
    cols = _columns("integrations")
    data = {k: v for k, v in data.items() if k in cols and k != "id"}
    if not data:
        return
    data["updated"] = now()
    conn = connect()
    conn.execute("UPDATE integrations SET %s WHERE id=1" % ",".join("%s=?" % k for k in data),
                 list(data.values()))
    conn.commit()
    conn.close()


def _ensure_column(table, col, decl):
    _COLCACHE.pop(table, None); _NUMCACHE.pop(table, None)
    if col in _columns(table):
        return
    conn = connect()
    conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, col, _pg_ddl(decl) if IS_PG else decl))
    conn.commit()
    conn.close()
    _COLCACHE.pop(table, None); _NUMCACHE.pop(table, None)
    _migrate_columns()


def _migrate_columns():
    """Add columns introduced after the first schema (idempotent)."""
    adds = [
        ("estimates", "margin_pct REAL DEFAULT 30"),
        ("estimate_lines", "section_id INTEGER"),
        ("estimate_lines", "waste_pct REAL DEFAULT 0"),
        ("estimate_lines", "cost REAL DEFAULT 0"),
        ("estimate_lines", "qrule TEXT"),  # JSON measurement->qty formula (AccuLynx mirror)
        ("jobs", "balance TEXT"),     # AccuLynx Balance Due, stored verbatim
        ("jobs", "collected TEXT"),   # AccuLynx Collected (value - balance), stored verbatim
        ("company_settings", "color_masthead TEXT DEFAULT '#24476C'"),
        # Appointments shipped originally with `start`/`end` columns; the app now
        # reads `start_at`/`end_at`. Add the new columns on legacy DBs and backfill.
        ("appointments", "start_at TEXT"),
        ("appointments", "end_at TEXT"),
    ]
    conn = connect()
    for table, coldef in adds:
        try:
            conn.execute("ALTER TABLE %s ADD COLUMN %s" % (table, coldef))
        except Exception:
            pass  # already exists
    # Backfill renamed appointment columns from the legacy start/end (reserved
    # words, so quoted) where present and the new columns are still empty.
    legacy = {"start": "start_at", "end": "end_at"}
    for old, new in legacy.items():
        try:
            conn.execute('UPDATE appointments SET %s="%s" '
                         'WHERE (%s IS NULL OR %s="") AND "%s" IS NOT NULL'
                         % (new, old, new, new, old))
        except Exception:
            pass  # legacy column doesn't exist (fresh schema) — nothing to copy
    conn.commit()
    conn.close()
    _COLCACHE.clear(); _NUMCACHE.clear()


def _migrate_stages():
    """Remap any legacy stage keys to the AccuLynx milestone model."""
    import constants
    conn = connect()
    for old, new in constants._OLD_JOB_STAGE_MAP.items():
        conn.execute("UPDATE jobs SET stage=? WHERE stage=?", (new, old))
    for old, new in constants._OLD_LEAD_STAGE_MAP.items():
        conn.execute("UPDATE leads SET stage=? WHERE stage=?", (new, old))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------

def _adapt(v):
    """JSON-encode dict/list values so they fit a TEXT column on both SQLite and
    Postgres (psycopg can't adapt a raw dict/list to a placeholder)."""
    if isinstance(v, (dict, list)):
        return json.dumps(v)
    return v


def insert(table, data):
    """Insert a dict; auto-stamps created/updated/stage_since when columns exist."""
    cols_present = _columns(table)
    data = dict(data)
    if "created" in cols_present and "created" not in data:
        data["created"] = now()
    if "updated" in cols_present and "updated" not in data:
        data["updated"] = now()
    data = {k: v for k, v in data.items() if k in cols_present}
    _coerce_numeric_blanks(table, data)
    keys = list(data.keys())
    conn = connect()
    sql = "INSERT INTO %s (%s) VALUES (%s)" % (table, ",".join(keys), ",".join("?" * len(keys)))
    vals = [_adapt(data[k]) for k in keys]
    if IS_PG:
        cur = conn.execute(sql + " RETURNING id", vals)
        rid = cur.fetchone()["id"]
    else:
        cur = conn.execute(sql, vals)
        rid = cur.lastrowid
    conn.commit()
    conn.close()
    return rid


def update(table, row_id, **fields):
    cols_present = _columns(table)
    if "updated" in cols_present:
        fields["updated"] = now()
    fields = {k: v for k, v in fields.items() if k in cols_present}
    _coerce_numeric_blanks(table, fields)
    if not fields:
        return
    conn = connect()
    conn.execute("UPDATE %s SET %s WHERE id=?" % (table, ",".join("%s=?" % k for k in fields)),
                 [_adapt(v) for v in fields.values()] + [row_id])
    conn.commit()
    conn.close()


def get(table, row_id):
    conn = connect()
    row = conn.execute("SELECT * FROM %s WHERE id=?" % table, (row_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def all_rows(table, where="", params=(), order="id DESC"):
    conn = connect()
    sql = "SELECT * FROM %s" % table
    if where:
        sql += " WHERE " + where
    if order:
        sql += " ORDER BY " + order
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete(table, row_id):
    conn = connect()
    conn.execute("DELETE FROM %s WHERE id=?" % table, (row_id,))
    conn.commit()
    conn.close()


def execute(sql, params=()):
    """Run an arbitrary write statement (DELETE/UPDATE) and commit."""
    conn = connect()
    conn.execute(sql, params)
    conn.commit()
    conn.close()


_COLCACHE = {}


def _columns(table):
    if table not in _COLCACHE:
        conn = connect()
        if IS_PG:
            cols = [r["column_name"] for r in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name=?",
                (table,)).fetchall()]
        else:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(%s)" % table).fetchall()]
        conn.close()
        _COLCACHE[table] = cols
    return _COLCACHE[table]


_NUMCACHE = {}
_PG_NUMERIC_TYPES = {"integer", "bigint", "smallint", "numeric", "real",
                     "double precision", "decimal"}


def _numeric_cols(table):
    """Set of numeric-typed columns (Postgres only). Used to turn a blank form
    value ("") into NULL — Postgres rejects '' for an integer/numeric column, while
    SQLite silently accepts it. Empty set on SQLite (no coercion needed)."""
    if not IS_PG:
        return set()
    if table not in _NUMCACHE:
        conn = connect()
        rows = conn.execute(
            "SELECT column_name, data_type FROM information_schema.columns WHERE table_name=?",
            (table,)).fetchall()
        conn.close()
        _NUMCACHE[table] = {r["column_name"] for r in rows
                            if r["data_type"] in _PG_NUMERIC_TYPES}
    return _NUMCACHE[table]


def _coerce_numeric_blanks(table, data):
    """In-place: blank ('' / whitespace) values for numeric columns become None."""
    if not IS_PG:
        return data
    nums = _numeric_cols(table)
    for k in list(data.keys()):
        v = data[k]
        if k in nums and isinstance(v, str) and not v.strip():
            data[k] = None
    return data


# ---------------------------------------------------------------------------
# JSON field helpers (checks / payments / items stored as TEXT)
# ---------------------------------------------------------------------------

def load_json(value, default):
    try:
        return json.loads(value) if value else default
    except Exception:
        return default


def dump_json(value):
    return json.dumps(value)


# ---------------------------------------------------------------------------
# Activity timeline
# ---------------------------------------------------------------------------

def add_activity(entity_type, entity_id, kind, text, due=None, assignee=None):
    return insert("activities", {
        "created": now(), "entity_type": entity_type, "entity_id": entity_id,
        "kind": kind, "text": text, "due": due, "done": 0, "assignee": assignee})


def entity_activity(entity_type, entity_id):
    return all_rows("activities", "entity_type=? AND entity_id=?",
                    (entity_type, entity_id), "id DESC")


def open_tasks(entity_type=None, entity_id=None):
    where = "kind='task' AND done=0"
    params = []
    if entity_type:
        where += " AND entity_type=?"
        params.append(entity_type)
    if entity_id:
        where += " AND entity_id=?"
        params.append(entity_id)
    return all_rows("activities", where, tuple(params), "due IS NULL, due ASC")


# ---------------------------------------------------------------------------
# Company settings (single brandable row)
# ---------------------------------------------------------------------------

def get_company():
    conn = connect()
    row = conn.execute("SELECT * FROM company_settings WHERE id=1").fetchone()
    conn.close()
    return dict(row) if row else {}


def save_company(data):
    cols = _columns("company_settings")
    data = {k: v for k, v in data.items() if k in cols and k != "id"}
    data["updated"] = now()
    conn = connect()
    exists = conn.execute("SELECT 1 FROM company_settings WHERE id=1").fetchone()
    if exists:
        conn.execute("UPDATE company_settings SET %s WHERE id=1" % ",".join("%s=?" % k for k in data),
                     list(data.values()))
    else:
        data["id"] = 1
        keys = list(data.keys())
        conn.execute("INSERT INTO company_settings (%s) VALUES (%s)" % (
            ",".join(keys), ",".join("?" * len(keys))), [data[k] for k in keys])
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Seed: default SeaBreeze brand + an admin user + a little sample data
# ---------------------------------------------------------------------------

def _seed_if_empty():
    if not get_company():
        save_company({
            "name": "SeaBreeze Roofing & Sheet Metal, Inc.",
            "legal_name": "SeaBreeze Roofing & Sheet Metal, Inc.",
            "tagline": "Florida Roofing Done Right",
            "license": "CCC1328689", "qualifier": "Jacintho Carreiro",
            "address": "2600 High Ridge Rd", "city": "Boynton Beach",
            "state": "FL", "zip": "33426",
            "phone": "(561) 555-0100", "email": "office@seabreezeroofing.com",
            "website": "seabreezeroofing.com",
            "color_primary": "#4680BF", "color_accent": "#8CC63F",
            "color_warn": "#F78300", "color_danger": "#E25050",  # AccuLynx-matched (live-verified)
            "default_county": "Palm Beach County",
            "departments": "REROOF Department, Service Department, Warranties",
            "terms": ("Payment schedule: 30% deposit (covers permit cost, material order & "
                      "admin); 30% at job start (mobilization); 30% upon passing 2 of 3 "
                      "inspections; 10% at final inspection. Draws 2-3 are performance-based. "
                      "All work per Florida Building Code."),
        })
    if not all_rows("users"):
        insert("users", {"name": "Danny Bivins", "email": "owner@seabreezeroofing.com",
                         "role": "admin", "active": 1})
        insert("users", {"name": "Karla", "email": "office@seabreezeroofing.com",
                         "role": "office", "active": 1})
    if not all_rows("leads") and not all_rows("jobs"):
        _seed_samples()
    _seed_templates()


def _seed_templates():
    """Seed the editable templates table from the code defaults (once)."""
    if all_rows("templates"):
        return
    import constants
    for key, tpl in constants.ESTIMATE_TEMPLATES.items():
        lines = [{"description": l["desc"], "unit": l["unit"], "qty": l.get("qty", 0),
                  "cost": l["price"], "q": l.get("q")} for l in tpl["lines"]]
        insert("templates", {
            "tkey": key, "name": tpl["name"],
            "work_type": (tpl["work_types"][0] if tpl.get("work_types") else ""),
            "scope_text": constants.scope_for_template(key),
            "lines": dump_json(lines), "is_builtin": 1})


def sync_builtin_templates():
    """Re-sync the editable templates table from the code defaults in constants.

    The estimate builder (_resolve_template) prefers DB template rows over the
    code defaults, so changing constants.ESTIMATE_TEMPLATES alone does NOT change
    live estimates — the existing builtin rows shadow the code. This refreshes the
    builtin rows (name / work_type / scope / lines) in place, matched by tkey.

    Only is_builtin=1 rows are touched — user-created custom templates and the
    user's own edits to custom rows are never modified. This is NOT called on
    startup (that would clobber legitimate UI edits to builtins); run it
    explicitly after intentionally changing the code templates. Returns
    (updated, inserted) counts.
    """
    import constants
    existing = {r["tkey"]: r for r in all_rows("templates") if r.get("is_builtin")}
    updated = inserted = 0
    for key, tpl in constants.ESTIMATE_TEMPLATES.items():
        lines = [{"description": l["desc"], "unit": l["unit"], "qty": l.get("qty", 0),
                  "cost": l["price"], "q": l.get("q")} for l in tpl["lines"]]
        payload = {
            "name": tpl["name"],
            "work_type": (tpl["work_types"][0] if tpl.get("work_types") else ""),
            "scope_text": constants.scope_for_template(key),
            "lines": dump_json(lines)}
        row = existing.get(key)
        if row:
            update("templates", row["id"], **payload)
            updated += 1
        else:
            insert("templates", {"tkey": key, "is_builtin": 1, **payload})
            inserted += 1
    return updated, inserted


def _seed_samples():
    t = today()
    c1 = insert("contacts", {"kind": "person", "first_name": "Maria", "last_name": "Gonzalez",
                             "email": "maria.g@example.com", "phone": "(561) 555-2841",
                             "address": "812 SW 3rd Ave", "city": "Boynton Beach", "state": "FL",
                             "zip": "33426", "source": "Referral", "tags": "shingle, hot lead"})
    insert("leads", {"contact_id": c1, "rid": "L-1001", "name": "Maria Gonzalez",
                     "phone": "(561) 555-2841", "email": "maria.g@example.com",
                     "address": "812 SW 3rd Ave, Boynton Beach, FL 33426",
                     "work_type": "Roofing - Shingle", "rep": "Danny Bivins", "source": "Referral",
                     "stage": "quoted", "stage_since": t, "last_contact": t,
                     "estimate": "$14,500", "narrative": "Shingle re-roof, sent estimate, deciding."})
    c2 = insert("contacts", {"kind": "person", "first_name": "Robert", "last_name": "Klein",
                             "email": "rklein@example.com", "phone": "(561) 555-9920",
                             "address": "44 Ocean Ridge Blvd", "city": "Ocean Ridge", "state": "FL",
                             "zip": "33435", "source": "Website", "tags": "tile"})
    insert("leads", {"contact_id": c2, "rid": "L-1002", "name": "Robert Klein",
                     "phone": "(561) 555-9920", "email": "rklein@example.com",
                     "address": "44 Ocean Ridge Blvd, Ocean Ridge, FL 33435",
                     "work_type": "Roofing - Tile", "rep": "Danny Bivins", "source": "Website",
                     "stage": "new", "stage_since": t, "last_contact": t})
    c3 = insert("contacts", {"kind": "person", "first_name": "The", "last_name": "Petersons",
                             "email": "peterson@example.com", "phone": "(561) 555-3377",
                             "address": "1190 NW 8th St", "city": "Delray Beach", "state": "FL",
                             "zip": "33444", "source": "Repeat Customer", "tags": "metal"})
    insert("jobs", {"contact_id": c3, "rid": "J-2001", "name": "Peterson Residence",
                    "phone": "(561) 555-3377", "email": "peterson@example.com",
                    "address": "1190 NW 8th St", "city": "Delray Beach", "state": "FL", "zip": "33444",
                    "work_type": "Roofing - Metal (Galvalume)", "rep": "Danny Bivins",
                    "source": "Repeat Customer", "stage": "permit_sub", "stage_since": t,
                    "contract_value": "$38,900", "county": "Palm Beach County",
                    "ahj": "Delray Beach", "system": "metal",
                    "payments": dump_json({"p1": True})})
