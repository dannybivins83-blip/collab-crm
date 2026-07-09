# -*- coding: utf-8 -*-
"""Base-schema completeness guard (the roof_reports.lead_id crash class).

`db.init_db()` runs `_ensure_indexes()` — a batch of ``CREATE INDEX IF NOT EXISTS``
statements — near the END of init, but BEFORE any blueprint in modules/ is imported.
Each blueprint may lazily ``db._ensure_column(...)`` (or raw ALTER) the columns it
uses. If an indexed column is only added that way, then at index-creation time the
column does not yet exist, the ``CREATE INDEX`` silently fails (it is wrapped in
``except: pass`` so one bad statement can't poison the rest), and the index is NEVER
created on that fresh tenant DB. Worse, if a LIST/INDEX-time query filters on such a
column, the page 500s outright (that was the roof_reports.lead_id bug, commit 59ed64e).

Prod's long-lived DB masks both symptoms because a past request/boot already ALTERed
the column in — so only brand-new white-label deploys are hit.

This test boots ONLY `db` (never `import app`, which would import every blueprint and
mask the bug by adding the columns) on a throwaway empty SQLite DB in an isolated
subprocess, runs `db.init_db()`, and asserts:

  1. The specific columns that regressed (leads.portal_token, jobs.portal_token,
     contacts.is_gc) are declared in the BASE schema — present after a bare init_db.
  2. Their indexes were actually created (not swallowed).
  3. GENERIC GUARD: every ``CREATE INDEX ... ON <table>(...)`` that db.py declares for a
     table that init_db() itself creates was actually created. This catches ANY future
     indexed column that someone adds only via a blueprint-level _ensure_column.
"""
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHECK = r'''
import os, re, sys, sqlite3
sys.path.insert(0, REPO_PLACEHOLDER)
os.chdir(REPO_PLACEHOLDER)

import db                    # NOTE: do NOT import app — blueprints would add the columns
                            # and hide exactly the fresh-tenant regression we are guarding.
db.init_db()

conn = sqlite3.connect(os.environ["CRM_DB_PATH"])

def columns(table):
    return {r[1] for r in conn.execute("PRAGMA table_info(%s)" % table)}

existing_tables = {r[0] for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table'")}
existing_indexes = {r[0] for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='index'")}

failures = []

# (1) explicit column regressions -------------------------------------------------
for table, col in (("leads", "portal_token"),
                   ("jobs", "portal_token"),
                   ("contacts", "is_gc")):
    if col not in columns(table):
        failures.append("%s.%s missing from base schema after init_db "
                        "(index-swallow / list-500 hazard)" % (table, col))

# (2) explicit index regressions --------------------------------------------------
for idx in ("idx_leads_portal_token", "idx_jobs_portal_token", "idx_contacts_is_gc"):
    if idx not in existing_indexes:
        failures.append("%s not created by init_db — its column is absent at "
                        "index-create time (swallowed)" % idx)

# (3) GENERIC guard: any index db.py declares for a table init_db() creates must exist
src = open(db.__file__, "r", encoding="utf-8").read()
declared = re.findall(
    r"CREATE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+(\w+)\s+ON\s+(\w+)\s*\(", src, re.I)
for idx_name, table in declared:
    if table not in existing_tables:
        continue  # table is created by its own blueprint module, not init_db — out of scope
    if idx_name not in existing_indexes:
        failures.append(
            "index %s ON %s declared in db.py but NOT created by init_db — a column it "
            "references is missing from the base CREATE TABLE (add it there, additive)"
            % (idx_name, table))

conn.close()

if failures:
    for f in failures:
        print("FAIL:", f)
    sys.exit(1)
print("SCHEMA_COMPLETE declared_indexes=%d" % len(declared))
sys.exit(0)
'''


def test_base_schema_declares_indexed_columns():
    tmpdb = os.path.join(tempfile.mkdtemp(prefix="schema_complete_"), "crm.db")
    assert not os.path.exists(tmpdb), "temp DB should not pre-exist"
    env = dict(os.environ)
    env["DATABASE_URL"] = ""            # force SQLite even if a PG URL is ambient
    env["POSTGRES_URL"] = ""
    env["POSTGRES_PRISMA_URL"] = ""
    env["CRM_DB_PATH"] = tmpdb          # throwaway, guaranteed-empty DB file
    env["CRM_DATA_DIR"] = os.path.dirname(tmpdb)
    env["CRM_NOBROWSER"] = "1"
    env["CRM_PORT"] = "5093"
    env.pop("RENDER", None)
    env.pop("CRM_ENV", None)

    code = _CHECK.replace("REPO_PLACEHOLDER", repr(REPO))
    r = subprocess.run([sys.executable, "-c", code], env=env,
                       capture_output=True, text=True, cwd=REPO, timeout=180)
    assert r.returncode == 0, (
        "base-schema completeness check failed (index swallowed on fresh tenant?):\n"
        "STDOUT:\n%s\nSTDERR:\n%s" % (r.stdout, r.stderr))
    assert "SCHEMA_COMPLETE" in r.stdout, "unexpected output:\n%s" % r.stdout
