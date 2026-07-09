# -*- coding: utf-8 -*-
"""Fresh-DB smoke test for the Roof Reports index route.

Regression guard for the fresh-white-label-deploy crash class: the base
`roof_reports` schema in db.py did not declare a `lead_id` column (it was only
added lazily via `_ensure_column` inside new()/_apply_to_client at request time,
and the `idx_roof_reports_lead_id` index-create is silently swallowed when the
column is absent). But `roof_reports.index()` filters on `lead_id IS NULL`
unconditionally, so `/roof-reports/` 500'd on any brand-new tenant DB. Prod's
old DB already had the column (added by a past request) which masked the bug.

Boots the real app on a throwaway empty SQLite DB in an isolated subprocess and
asserts the roof-reports GET routes respond without a 5xx.
"""
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHECK = r'''
import os, sys
sys.path.insert(0, REPO_PLACEHOLDER)
os.chdir(REPO_PLACEHOLDER)
import app as appmod
import db

a = appmod.app
a.config["WTF_CSRF_ENABLED"] = False
c = a.test_client()

# lead_id must be part of the BASE schema after init_db (not lazily added).
assert "lead_id" in db._columns("roof_reports"), (
    "roof_reports.lead_id missing after init_db — fresh-DB /roof-reports/ will 500")

uid = db.insert("users", {"name": "smoke", "email": "smoke@test.local", "role": "admin"})
jid = db.insert("jobs", {"name": "Smoke Job", "address": "1 Main St",
                         "city": "Boca Raton", "ahj": "Boca Raton", "system": "shingle"})
rid = db.insert("roof_reports", {"job_id": jid, "address": "1 Main St", "status": "queued"})

with c.session_transaction() as s:
    s["user_id"] = uid
    s["user_name"] = "smoke"
    s["user_role"] = "admin"

checks = [
    ("/roof-reports/", (200,)),
    ("/roof-reports/?q=boca&status=queued", (200,)),
    ("/roof-reports/%d" % rid, (200, 302, 404)),
]
bad = []
for path, ok in checks:
    r = c.get(path)
    if r.status_code >= 500 or r.status_code not in ok:
        bad.append((path, r.status_code, ok))

if bad:
    for b in bad:
        print("FAIL", b)
    sys.exit(1)
print("ALL_OK", len(checks))
sys.exit(0)
'''


def test_roof_reports_routes_no_5xx():
    tmpdb = os.path.join(tempfile.mkdtemp(prefix="roofrep_smoke_"), "crm.db")
    env = dict(os.environ)
    env["DATABASE_URL"] = ""            # force SQLite
    env["POSTGRES_URL"] = ""
    env["POSTGRES_PRISMA_URL"] = ""
    env["CRM_DB_PATH"] = tmpdb          # throwaway DB
    env["CRM_DATA_DIR"] = os.path.dirname(tmpdb)
    env["CRM_NOBROWSER"] = "1"
    env["CRM_PORT"] = "5094"
    env.pop("RENDER", None)
    env.pop("CRM_ENV", None)

    code = _CHECK.replace("REPO_PLACEHOLDER", repr(REPO))
    r = subprocess.run([sys.executable, "-c", code], env=env,
                       capture_output=True, text=True, cwd=REPO, timeout=180)
    assert r.returncode == 0, (
        "roof-reports route smoke failed:\nSTDOUT:\n%s\nSTDERR:\n%s"
        % (r.stdout, r.stderr))
    assert "ALL_OK" in r.stdout
