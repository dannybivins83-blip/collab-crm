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

CREATE TABLE IF NOT EXISTS roof_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created TEXT, updated TEXT,
    job_id INTEGER,
    address TEXT, city TEXT, state TEXT, zip TEXT,
    engine_job TEXT,                 -- the Roof Report Engine job id
    status TEXT DEFAULT 'queued',    -- queued | processing | done | failed
    squares TEXT, pitch TEXT, confidence TEXT,
    api_result TEXT                  -- full measurement JSON
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

CREATE TABLE IF NOT EXISTS job_expenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER, acculynx_job_name TEXT,
    payment_date TEXT, payment_type TEXT,
    amount REAL DEFAULT 0,
    to_method TEXT, check_ref TEXT,
    memo TEXT, job_value REAL DEFAULT 0, balance_due REAL DEFAULT 0,
    account_type TEXT, paid_in_full TEXT, rep TEXT
);

CREATE TABLE IF NOT EXISTS job_stage_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER, acculynx_job_name TEXT,
    status_name TEXT, milestone TEXT,
    started_at TEXT, ended_at TEXT,
    duration_days INTEGER DEFAULT 0,
    set_by TEXT, checklist_pct TEXT, checklist_done TEXT
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

CREATE TABLE IF NOT EXISTS team_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created TEXT,
    user_id INTEGER,
    user_name TEXT,
    body TEXT
);
"""


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
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
    # Hierarchical worksheet sync — section/group/scope structure from AccuLynx.
    for _c, _d in [("ws_section", "TEXT"), ("ws_group", "TEXT"),
                   ("item_type", "TEXT DEFAULT 'material'"), ("scope_letter", "TEXT"),
                   ("price", "REAL DEFAULT 0")]:
        _ensure_column("worksheet_lines", _c, _d)
    _ensure_column("company_settings", "labels", "TEXT")  # JSON map of phrase -> custom label
    _ensure_column("company_settings", "lead_notify_to", "TEXT")  # fallback email for new-lead rep notifications
    _ensure_change_requests_table()
    _ensure_library_table()
    _ensure_permit_api_tables()
    _seed_if_empty()
    _migrate_columns()  # idempotent; run unconditionally so renamed/added columns land
    _migrate_stages()
    _migrate_null_defaults()  # backfill NULLs on columns accessed with direct bracket notation
    _ensure_indexes()   # idempotent: CREATE INDEX IF NOT EXISTS for all WHERE-clause cols
    # Default departments for any existing company row that lacks them.
    co = get_company()
    if co and not (co.get("departments") or "").strip():
        save_company({"departments": "REROOF Department, Service Department, Warranties"})


def _ensure_indexes():
    """Create indexes for columns frequently used in WHERE clauses.

    Called unconditionally from init_db() — every statement uses CREATE INDEX IF
    NOT EXISTS so it is fully idempotent.  All indexes are on SQLite; the Postgres
    path gains them via the same DDL (Postgres also accepts IF NOT EXISTS).

    Index naming convention: idx_<table>_<col(s)>
    """
    _IDX_SQL = """
-- leads: filtered by department (list page), contact, stage (pipeline/smart-todos)
CREATE INDEX IF NOT EXISTS idx_leads_department    ON leads(department);
CREATE INDEX IF NOT EXISTS idx_leads_contact_id    ON leads(contact_id);
CREATE INDEX IF NOT EXISTS idx_leads_stage         ON leads(stage);
CREATE INDEX IF NOT EXISTS idx_leads_portal_token  ON leads(portal_token);

-- jobs: same high-traffic columns
CREATE INDEX IF NOT EXISTS idx_jobs_department     ON jobs(department);
CREATE INDEX IF NOT EXISTS idx_jobs_contact_id     ON jobs(contact_id);
CREATE INDEX IF NOT EXISTS idx_jobs_lead_id        ON jobs(lead_id);
CREATE INDEX IF NOT EXISTS idx_jobs_stage          ON jobs(stage);
CREATE INDEX IF NOT EXISTS idx_jobs_portal_token   ON jobs(portal_token);

-- activities: the timeline query filters by (entity_type, entity_id) and kind/done
CREATE INDEX IF NOT EXISTS idx_activities_entity   ON activities(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_activities_kind_done ON activities(kind, done);

-- estimates: loaded per-job and per-lead constantly
CREATE INDEX IF NOT EXISTS idx_estimates_job_id    ON estimates(job_id);
CREATE INDEX IF NOT EXISTS idx_estimates_lead_id   ON estimates(lead_id);

-- estimate_sections/lines: always fetched by parent estimate
CREATE INDEX IF NOT EXISTS idx_est_sections_est_id ON estimate_sections(estimate_id);
CREATE INDEX IF NOT EXISTS idx_est_lines_est_id    ON estimate_lines(estimate_id);
CREATE INDEX IF NOT EXISTS idx_est_lines_section_id ON estimate_lines(section_id);

-- documents: loaded per-job and per-lead on every job/lead detail page
CREATE INDEX IF NOT EXISTS idx_documents_job_id    ON documents(job_id);
CREATE INDEX IF NOT EXISTS idx_documents_lead_id   ON documents(lead_id);

-- photos: per-job on every job detail + portal page
CREATE INDEX IF NOT EXISTS idx_photos_job_id       ON photos(job_id);

-- invoices: per-job (list, QBO sync, portal)
CREATE INDEX IF NOT EXISTS idx_invoices_job_id     ON invoices(job_id);

-- materials: per-job
CREATE INDEX IF NOT EXISTS idx_materials_job_id    ON materials(job_id);

-- permits: per-job
CREATE INDEX IF NOT EXISTS idx_permits_job_id      ON permits(job_id);

-- worksheets / worksheet_lines: per-job and per-worksheet
CREATE INDEX IF NOT EXISTS idx_worksheets_job_id       ON worksheets(job_id);
CREATE INDEX IF NOT EXISTS idx_worksheet_lines_ws_id   ON worksheet_lines(worksheet_id);

-- orders / order_lines: per-job, per-department
CREATE INDEX IF NOT EXISTS idx_orders_job_id       ON orders(job_id);
CREATE INDEX IF NOT EXISTS idx_orders_department   ON orders(department);
CREATE INDEX IF NOT EXISTS idx_order_lines_order_id ON order_lines(order_id);

-- measurements: per-job and per-lead (roof-report sync, takeoff)
CREATE INDEX IF NOT EXISTS idx_measurements_job_id  ON measurements(job_id);
CREATE INDEX IF NOT EXISTS idx_measurements_lead_id ON measurements(lead_id);

-- roof_reports: per-job
CREATE INDEX IF NOT EXISTS idx_roof_reports_job_id  ON roof_reports(job_id);

-- appointments: per-lead, per-job, per-contact
CREATE INDEX IF NOT EXISTS idx_appointments_lead_id    ON appointments(lead_id);
CREATE INDEX IF NOT EXISTS idx_appointments_job_id     ON appointments(job_id);
CREATE INDEX IF NOT EXISTS idx_appointments_contact_id ON appointments(contact_id);

-- notifications: per job + read flag (notifications has job_id/rep/read columns, no entity_type/entity_id)
CREATE INDEX IF NOT EXISTS idx_notifications_job_id ON notifications(job_id);
CREATE INDEX IF NOT EXISTS idx_notifications_read   ON notifications(read);

-- automations: trigger lookup (run on every stage change)
CREATE INDEX IF NOT EXISTS idx_automations_trigger  ON automations(trigger_stage, active);

-- contacts: GC filter
CREATE INDEX IF NOT EXISTS idx_contacts_is_gc       ON contacts(is_gc);

-- custom_fields / custom_values: entity lookups
CREATE INDEX IF NOT EXISTS idx_custom_fields_entity      ON custom_fields(entity);
CREATE INDEX IF NOT EXISTS idx_custom_values_entity      ON custom_values(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_custom_values_field_id    ON custom_values(field_id);

-- gmail_accounts: per-user
CREATE INDEX IF NOT EXISTS idx_gmail_accounts_user_id ON gmail_accounts(user_id);

-- commissions: per-job, per-department
CREATE INDEX IF NOT EXISTS idx_commissions_job_id    ON commissions(job_id);
CREATE INDEX IF NOT EXISTS idx_commissions_dept      ON commissions(department);

-- takeoff_jobs: lookup by token and by lead
CREATE INDEX IF NOT EXISTS idx_takeoff_jobs_token    ON takeoff_jobs(token);
CREATE INDEX IF NOT EXISTS idx_takeoff_jobs_lead_id  ON takeoff_jobs(lead_id);

-- library_docs: per-category (used for permit/signup package lookups)
CREATE INDEX IF NOT EXISTS idx_library_docs_category ON library_docs(category);

-- demos: slug lookup
CREATE INDEX IF NOT EXISTS idx_demos_slug            ON demos(slug);

-- signup_packets / signup_templates: per-job, per-system
CREATE INDEX IF NOT EXISTS idx_signup_packets_job_id    ON signup_packets(job_id);
CREATE INDEX IF NOT EXISTS idx_signup_templates_system  ON signup_templates(system);

-- payments: per-job
CREATE INDEX IF NOT EXISTS idx_payments_job_id       ON payments(job_id);

-- qxo_products: per-material
CREATE INDEX IF NOT EXISTS idx_qxo_products_material_id ON qxo_products(material_id);

-- portal_updates: per-job, composite (job_id, phase) for duplicate check
CREATE INDEX IF NOT EXISTS idx_portal_updates_job_id    ON portal_updates(job_id);
CREATE INDEX IF NOT EXISTS idx_portal_updates_job_phase ON portal_updates(job_id, phase);
"""
    # Run each CREATE INDEX statement in its own connection so a Postgres
    # transaction-abort on one failure does not poison all subsequent statements.
    for stmt in _split_sql(_IDX_SQL):
        conn = connect()
        try:
            conn.execute(stmt)
            conn.commit()
        except Exception:
            pass  # table/column doesn't exist yet on this schema variant — skip
        finally:
            conn.close()


def _ensure_library_table():
    """Company Document Library — reusable docs (warranties, brochures, licenses,
    permit packages, sign-up packages, cheat sheets) with category + AHJ/system tags."""
    conn = connect()
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS library_docs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created TEXT, filename TEXT, original_name TEXT,
            category TEXT, ahj TEXT, system TEXT, tags TEXT,
            size INTEGER, notes TEXT)""")
        conn.commit()
    finally:
        conn.close()


def _ensure_permit_api_tables():
    """White-label permit builder API tables:
    contractor_profiles — per-tenant contractor branding (replaces hardcoded SB constants)
    permit_api_keys — API keys for external software integrations
    permit_build_jobs — async build job tracking (status, result path, webhook)
    portal_accounts — per-(platform/AHJ) contractor registration status tracker
    """
    conn = connect()
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS contractor_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created TEXT, tenant_id INTEGER DEFAULT 1,
            company_name TEXT, license_number TEXT, qualifier_name TEXT,
            address TEXT, city TEXT, state TEXT DEFAULT 'FL', zip TEXT,
            phone TEXT, email TEXT, contact_person TEXT, notary_county TEXT,
            logo_path TEXT, is_default INTEGER DEFAULT 0)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS permit_api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT, tenant_id INTEGER DEFAULT 1,
            key_hash TEXT UNIQUE, label TEXT,
            rate_limit_per_day INTEGER DEFAULT 100,
            active INTEGER DEFAULT 1)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS permit_build_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT, job_id TEXT UNIQUE,
            api_key_id INTEGER, status TEXT DEFAULT 'queued',
            result_path TEXT, webhook_url TEXT, notes TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS portal_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created TEXT, updated TEXT,
            platform TEXT, ahj TEXT, city TEXT, county TEXT,
            username TEXT, registration_status TEXT DEFAULT 'pending',
            notes TEXT, last_checked TEXT)""")
        conn.commit()
    finally:
        conn.close()


def _ensure_change_requests_table():
    """Log of white-label customization requests typed in the Customize box."""
    conn = connect()
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS change_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created TEXT, requested_by TEXT, raw_text TEXT,
            status TEXT DEFAULT 'queued',   -- applied | partial | queued
            result TEXT)""")
        conn.commit()
    finally:
        conn.close()


def _ensure_integrations_table():
    """Single-row table holding QuickBooks Online OAuth config + tokens."""
    conn = connect()
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS integrations (
            id INTEGER PRIMARY KEY CHECK (id=1),
            qbo_client_id TEXT, qbo_client_secret TEXT, qbo_environment TEXT DEFAULT 'production',
            qbo_realm_id TEXT, qbo_access_token TEXT, qbo_refresh_token TEXT,
            qbo_token_expiry TEXT, qbo_connected_at TEXT, qbo_redirect_uri TEXT,
            updated TEXT)""")
        conn.execute("INSERT INTO integrations (id) VALUES (1) ON CONFLICT (id) DO NOTHING"
                     if IS_PG else "INSERT OR IGNORE INTO integrations (id) VALUES (1)")
        conn.commit()
    finally:
        conn.close()


def get_integrations():
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM integrations WHERE id=1").fetchone()
    finally:
        conn.close()
    return dict(row) if row else {}


def save_integrations(data):
    cols = _columns("integrations")
    data = {k: v for k, v in data.items() if k in cols and k != "id"}
    if not data:
        return
    data["updated"] = now()
    conn = connect()
    try:
        conn.execute("UPDATE integrations SET %s WHERE id=1" % ",".join("%s=?" % k for k in data),
                     list(data.values()))
        conn.commit()
    finally:
        conn.close()


def _ensure_column(table, col, decl):
    _COLCACHE.pop(table, None); _NUMCACHE.pop(table, None)
    if col in _columns(table):
        return
    conn = connect()
    try:
        conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, col, _pg_ddl(decl) if IS_PG else decl))
        conn.commit()
    finally:
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
    try:
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
    finally:
        conn.close()
    _COLCACHE.clear(); _NUMCACHE.clear()


def _migrate_stages():
    """Remap any legacy stage keys to the AccuLynx milestone model."""
    import constants
    conn = connect()
    try:
        for old, new in constants._OLD_JOB_STAGE_MAP.items():
            conn.execute("UPDATE jobs SET stage=? WHERE stage=?", (new, old))
        for old, new in constants._OLD_LEAD_STAGE_MAP.items():
            conn.execute("UPDATE leads SET stage=? WHERE stage=?", (new, old))
        conn.commit()
    finally:
        conn.close()


def _migrate_null_defaults():
    """Backfill NULL values for columns that the application code accesses via direct
    bracket notation (e.g. ``row["stage"]``), which raises or returns None when the
    column is NULL, breaking board/list/dashboard views.

    All affected columns have a schema DEFAULT — this migration ensures existing rows
    that were inserted before the DEFAULT was present, or were inserted with an explicit
    NULL, satisfy the non-null invariant that the rest of the code assumes.

    Safe to run unconditionally: the UPDATE is a no-op when every row already has a
    value.
    """
    _BACKFILLS = [
        # table,               column,   fallback value
        ("leads",              "stage",  "new"),
        ("jobs",               "stage",  "approved"),
        ("invoices",           "status", "unpaid"),
        ("commissions",        "status", "pre"),
        ("commissions",        "basis",  "profit"),
        ("activities",         "kind",   "note"),
        ("activities",         "done",   "0"),
        ("estimate_sections",  "margin_pct", "30"),
        ("estimate_lines",     "qty",    "0"),
        ("estimate_lines",     "cost",   "0"),
        ("estimate_lines",     "price",  "0"),
        ("worksheet_lines",    "budget_cost", "0"),
        ("worksheet_lines",    "actual_cost", "0"),
        ("worksheets",         "contract_value", "0"),
    ]
    conn = connect()
    try:
        for table, col, val in _BACKFILLS:
            try:
                conn.execute(
                    "UPDATE %s SET %s=? WHERE %s IS NULL" % (table, col, col),
                    (val,))
            except Exception:
                pass  # table or column may not exist yet on very old schemas
        # Fix estimate_lines rows with NULL section_id.
        # Step 1: For estimates that have sections, assign orphan lines to section 0.
        try:
            conn.execute("""
                UPDATE estimate_lines
                SET section_id = (
                    SELECT id FROM estimate_sections
                    WHERE estimate_sections.estimate_id = estimate_lines.estimate_id
                    ORDER BY sort, id LIMIT 1
                )
                WHERE section_id IS NULL
                  AND estimate_id IS NOT NULL
                  AND EXISTS (
                    SELECT 1 FROM estimate_sections
                    WHERE estimate_sections.estimate_id = estimate_lines.estimate_id
                  )
            """)
        except Exception:
            pass
        # Step 2: For estimates that have NO sections at all, create a default section
        # and assign the orphan lines to it (these are pre-sections legacy estimates).
        try:
            orphan_est_ids = [
                dict(r)["estimate_id"]
                for r in conn.execute("""
                    SELECT DISTINCT el.estimate_id
                    FROM estimate_lines el
                    WHERE el.section_id IS NULL
                      AND el.estimate_id IS NOT NULL
                      AND NOT EXISTS (
                        SELECT 1 FROM estimate_sections es
                        WHERE es.estimate_id = el.estimate_id
                      )
                """).fetchall()
            ]
        except Exception:
            orphan_est_ids = []
        for eid in orphan_est_ids:
            try:
                # _insert_section: create a default section for this estimate
                conn.execute(
                    "INSERT INTO estimate_sections (estimate_id, sort, name, scope_text, margin_pct)"
                    " VALUES (?, 0, 'Scope of Work', '', 30)",
                    (eid,))
                # Get the new section id (RETURNING not available in old SQLite; use lastrowid approach)
                sid_row = conn.execute(
                    "SELECT id FROM estimate_sections WHERE estimate_id=? ORDER BY id DESC LIMIT 1",
                    (eid,)).fetchone()
                if sid_row:
                    sid = dict(sid_row)["id"]
                    conn.execute(
                        "UPDATE estimate_lines SET section_id=? WHERE estimate_id=? AND section_id IS NULL",
                        (sid, eid))
            except Exception:
                pass
        conn.commit()
    finally:
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
    try:
        if IS_PG:
            cur = conn.execute(sql + " RETURNING id", vals)
            rid = cur.fetchone()["id"]
        else:
            cur = conn.execute(sql, vals)
            rid = cur.lastrowid
        conn.commit()
    finally:
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
    try:
        conn.execute("UPDATE %s SET %s WHERE id=?" % (table, ",".join("%s=?" % k for k in fields)),
                     [_adapt(v) for v in fields.values()] + [row_id])
        conn.commit()
    finally:
        conn.close()


def get(table, row_id):
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM %s WHERE id=?" % table, (row_id,)).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def all_rows(table, where="", params=(), order="id DESC"):
    conn = connect()
    sql = "SELECT * FROM %s" % table
    if where:
        sql += " WHERE " + where
    if order:
        sql += " ORDER BY " + order
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def delete(table, row_id):
    conn = connect()
    try:
        conn.execute("DELETE FROM %s WHERE id=?" % table, (row_id,))
        conn.commit()
    finally:
        conn.close()


def execute(sql, params=()):
    """Run an arbitrary write statement (DELETE/UPDATE) and commit."""
    conn = connect()
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


_COLCACHE = {}


def _columns(table):
    if table not in _COLCACHE:
        conn = connect()
        try:
            if IS_PG:
                cols = [r["column_name"] for r in conn.execute(
                    "SELECT column_name FROM information_schema.columns WHERE table_name=?",
                    (table,)).fetchall()]
            else:
                cols = [r[1] for r in conn.execute("PRAGMA table_info(%s)" % table).fetchall()]
        finally:
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
        try:
            rows = conn.execute(
                "SELECT column_name, data_type FROM information_schema.columns WHERE table_name=?",
                (table,)).fetchall()
        finally:
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
    try:
        row = conn.execute("SELECT * FROM company_settings WHERE id=1").fetchone()
    finally:
        conn.close()
    return dict(row) if row else {}


def save_company(data):
    cols = _columns("company_settings")
    data = {k: v for k, v in data.items() if k in cols and k != "id"}
    data["updated"] = now()
    conn = connect()
    try:
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
    finally:
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
