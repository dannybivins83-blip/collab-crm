# -*- coding: utf-8 -*-
"""Fresh-DB regression guards for the routes-b lane (permits/portal/roof_reports/
invoices) — the LIVE-500 hardening class: bad-input coercion + corrupt-JSON reads
that only bite on legacy/imported/hand-edited prod rows a clean dev DB never has.

Each seeded row reproduces a 500 that occurred BEFORE the fix:
  1. roof_reports.detail  — a NON-JSON api_result 500'd raw json.loads().
  2. invoices.detail      — a money STRING in the REAL amount column 500'd the
                            balance math AND the template's money() filter.
  3. invoices.index       — same string amount 500'd the list totals + per-row render.
  4. portal.home (job)    — the same string invoice amount 500'd money(inv.amount);
                            a non-dict jobs.payments blob 500'd theme.paid_pct.

Boots the real app on a throwaway empty SQLite DB in an isolated subprocess and
asserts every one of these GET routes responds without a 5xx.
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
from modules import portal as portalmod

a = appmod.app
a.config["WTF_CSRF_ENABLED"] = False
c = a.test_client()

uid = db.insert("users", {"name": "smoke", "email": "smoke@test.local", "role": "admin"})

# Job with a portal token whose billing carries a corrupt (non-dict) payments blob.
jid = db.insert("jobs", {"name": "R-1: Bob", "stage": "approved", "system": "shingle",
                         "work_type": "Reroof - Shingle", "contract_value": "$20,000",
                         "email": "b@t.test", "rep": "Danny"})
db.execute("UPDATE jobs SET payments=? WHERE id=?", ("[1, 2, 3]", jid))  # list, not dict

# roof report whose api_result is NOT valid JSON.
rr = db.insert("roof_reports", {"job_id": jid, "address": "1 Main St", "status": "done",
                                "engine_job": "e1"})
db.execute("UPDATE roof_reports SET api_result=? WHERE id=?", ("{not valid json", rr))

# Invoice + payment carrying a non-numeric money STRING in the REAL amount column.
inv = db.insert("invoices", {"job_id": jid, "number": "INV-1", "status": "unpaid"})
db.execute("UPDATE invoices SET amount=? WHERE id=?", ("N/A", inv))
pmt = db.insert("payments", {"job_id": jid, "invoice_id": inv})
db.execute("UPDATE payments SET amount=? WHERE id=?", ("bad", pmt))
# A NULL-job invoice with a string amount is ALWAYS in the index list (job_id IS NULL).
inv2 = db.insert("invoices", {"job_id": None, "number": "INV-2", "status": "unpaid"})
db.execute("UPDATE invoices SET amount=? WHERE id=?", ("$1,234.56", inv2))

tok = portalmod.ensure_token(jid)

with c.session_transaction() as s:
    s["user_id"] = uid; s["user_name"] = "smoke"; s["user_role"] = "admin"

checks = [
    ("/roof-reports/%d" % rr, (200, 302, 404)),
    ("/invoices/%d" % inv, (200,)),
    ("/invoices/", (200,)),
    ("/portal/%s" % tok, (200,)),
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


def _fresh_env(tmpdb):
    env = dict(os.environ)
    env["DATABASE_URL"] = ""            # force SQLite even if a PG URL is ambient
    env["POSTGRES_URL"] = ""
    env["POSTGRES_PRISMA_URL"] = ""
    env["CRM_DB_PATH"] = tmpdb          # throwaway, guaranteed-empty DB file
    env["CRM_DATA_DIR"] = os.path.dirname(tmpdb)
    env["CRM_NOBROWSER"] = "1"
    env["CRM_PORT"] = "5090"
    env.pop("RENDER", None)
    env.pop("CRM_ENV", None)
    return env


def test_routes_b_no_5xx_on_bad_data():
    tmpdb = os.path.join(tempfile.mkdtemp(prefix="routesb_smoke_"), "crm.db")
    code = _CHECK.replace("REPO_PLACEHOLDER", repr(REPO))
    r = subprocess.run([sys.executable, "-c", code], env=_fresh_env(tmpdb),
                       capture_output=True, text=True, cwd=REPO, timeout=180)
    assert r.returncode == 0, (
        "routes-b hardening smoke failed:\nSTDOUT:\n%s\nSTDERR:\n%s"
        % (r.stdout, r.stderr))
    assert "ALL_OK" in r.stdout
