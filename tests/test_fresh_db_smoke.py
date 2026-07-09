# -*- coding: utf-8 -*-
"""Fresh-database boot smoke test.

Regression guard for the payments-style crash class: a module runs
`db._ensure_column("<table>", ...)` (or a raw ALTER TABLE) at IMPORT time, but the
table it targets is not created by `db.init_db()` before the blueprint that touches
it is imported. On an EXISTING prod DB the table already happens to exist so nothing
breaks; on a brand-new white-label deploy `import app` crashes on boot with
`sqlite3.OperationalError: no such table: <table>`. Prod's own DB masked the bug.

This boots the real app against a throwaway, empty SQLite DB in an isolated
subprocess (so it neither depends on nor mutates the dev database, and can't be
pinned by another test module's import-time DB path) and asserts:
  1. `import app` succeeds — every blueprint's import-time DDL ran against a DB
     that only `init_db()` had touched.
  2. The public /healthz probe returns 200 {"ok": true, "sha": ...} with no session
     (deploy-health checks must not need auth or RENDER_API_KEY).
"""
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Runs inside the subprocess: import the app on a fresh DB, then hit /healthz and
# /version unauthenticated. Any import-time "no such table" would abort before this
# prints, failing the test with the traceback on stderr.
_CHECK = r'''
import json, os, sys
sys.path.insert(0, REPO_PLACEHOLDER)
os.chdir(REPO_PLACEHOLDER)

import app as appmod          # <-- the boot under test (runs every import-time DDL)
import db

# init_db must have produced a usable schema (base table present, no partial state).
assert "id" in db._columns("company_settings"), "company_settings missing after init_db"

a = appmod.app
c = a.test_client()

for path in ("/healthz", "/version"):
    r = c.get(path)                      # NO session set -> must still be 200 (public)
    assert r.status_code == 200, "%s -> %d (expected 200; auth-exempt?)" % (path, r.status_code)
    body = json.loads(r.data)
    assert body.get("ok") is True, "%s ok!=True: %r" % (path, body)
    assert body.get("sha"), "%s missing sha: %r" % (path, body)

print("FRESH_DB_OK routes=%d sha=%s" % (
    len(list(a.url_map.iter_rules())), json.loads(c.get("/healthz").data)["sha"]))
sys.exit(0)
'''


def _fresh_env(tmpdb):
    env = dict(os.environ)
    env["DATABASE_URL"] = ""            # force SQLite even if a PG URL is in the ambient env
    env["POSTGRES_URL"] = ""
    env["POSTGRES_PRISMA_URL"] = ""
    env["CRM_DB_PATH"] = tmpdb          # throwaway, guaranteed-empty DB file
    env["CRM_DATA_DIR"] = os.path.dirname(tmpdb)
    env["CRM_NOBROWSER"] = "1"
    env["CRM_PORT"] = "5098"
    env.pop("RENDER", None)
    env.pop("CRM_ENV", None)
    return env


def test_fresh_db_import_and_healthz():
    tmpdb = os.path.join(tempfile.mkdtemp(prefix="fresh_db_smoke_"), "crm.db")
    assert not os.path.exists(tmpdb), "temp DB should not pre-exist"
    code = _CHECK.replace("REPO_PLACEHOLDER", repr(REPO))
    r = subprocess.run([sys.executable, "-c", code], env=_fresh_env(tmpdb),
                       capture_output=True, text=True, cwd=REPO, timeout=180)
    assert r.returncode == 0, (
        "fresh-DB boot smoke failed (import-time DDL ordering hazard?):\n"
        "STDOUT:\n%s\nSTDERR:\n%s" % (r.stdout, r.stderr))
    assert "FRESH_DB_OK" in r.stdout, "unexpected output:\n%s" % r.stdout
