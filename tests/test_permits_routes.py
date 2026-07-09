# -*- coding: utf-8 -*-
"""Smoke tests for the permit-lane GET routes.

Guards the regression class where a view references a name that was only
imported in another function's local scope (e.g. `_theme` used in permits.new()
while `import theme as _theme` lived inside permits.index()), which 500s only
when the route is actually hit — invisible to import/compile checks.

Runs in an isolated subprocess against a fresh temp SQLite DB so it neither
depends on nor mutates the dev database, and can't be contaminated by the DB
path other test modules pin at import time.
"""
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Self-contained checker: boots the real app on a throwaway DB, seeds a user +
# job + permit, then asserts every permit GET route responds without a 5xx.
_CHECK = r'''
import os, sys
sys.path.insert(0, REPO_PLACEHOLDER)
os.chdir(REPO_PLACEHOLDER)
import app as appmod
import db

a = appmod.app
a.config["WTF_CSRF_ENABLED"] = False
c = a.test_client()

uid = db.insert("users", {"name": "smoke", "email": "smoke@test.local",
                          "role": "admin"})
jid = db.insert("jobs", {"name": "Smoke Job", "address": "1 Main St",
                         "city": "Boca Raton", "ahj": "Boca Raton",
                         "system": "shingle"})
pid = db.insert("permits", {"job_id": jid, "ahj": "Boca Raton",
                            "system": "shingle", "status": "prep"})

with c.session_transaction() as s:
    s["user_id"] = uid
    s["user_name"] = "smoke"
    s["user_role"] = "admin"

checks = [
    ("/permits/", (200,)),
    ("/permits/?q=boca&status=prep", (200,)),
    ("/permits/new", (200,)),
    ("/permits/new?job_id=%d" % jid, (200,)),
    ("/permits/%d" % pid, (200,)),
    ("/permits/%d?sys=tile" % pid, (200,)),
    ("/permits/portal-accounts", (200,)),
    ("/permits/contractor-profile", (200,)),
    ("/permits/widget/embed", (400, 401)),   # missing api_key -> 400
]

bad = []
for path, ok in checks:
    r = c.get(path)
    if r.status_code not in ok:
        bad.append((path, r.status_code, ok))

if bad:
    for b in bad:
        print("FAIL", b)
    sys.exit(1)
print("ALL_OK", len(checks))
sys.exit(0)
'''


def test_permit_routes_no_5xx():
    tmpdb = os.path.join(tempfile.mkdtemp(prefix="permit_smoke_"), "crm.db")
    env = dict(os.environ)
    env["DATABASE_URL"] = ""            # force SQLite
    env["CRM_DB_PATH"] = tmpdb          # throwaway DB
    env["CRM_NOBROWSER"] = "1"
    env["CRM_PORT"] = "5099"
    env.pop("RENDER", None)
    env.pop("CRM_ENV", None)

    code = _CHECK.replace("REPO_PLACEHOLDER", repr(REPO))
    r = subprocess.run([sys.executable, "-c", code], env=env,
                       capture_output=True, text=True, cwd=REPO, timeout=180)
    assert r.returncode == 0, (
        "permit route smoke failed:\nSTDOUT:\n%s\nSTDERR:\n%s"
        % (r.stdout, r.stderr))
    assert "ALL_OK" in r.stdout
