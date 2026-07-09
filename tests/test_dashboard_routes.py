# -*- coding: utf-8 -*-
"""Fresh-DB smoke test for the Dashboard home route (`/`).

Regression guard for the money-string crash class: `dashboard.home` summed
outstanding-invoice amounts in Python (`sum(i.get("amount") or 0 ...)`) and the
template renders `money(inv.amount)` per row. SQLite's REAL affinity does NOT
convert a comma-formatted string like "1,200.50" to a number — it is stored as
TEXT verbatim (exactly how AccuLynx-imported / formatted amounts land). That made
the home page 500 on EVERY load with either a `TypeError: int + str` (the sum) or
a `ValueError: could not convert string to float` (the template's money()).

The fix coerces each outstanding invoice amount through `theme.est_num()` in the
handler, so both the sum and the template render survive money-string amounts.

Boots the real app on a throwaway empty SQLite DB in an isolated subprocess and
asserts `/` (and the `/dashboard` alias) respond without a 5xx — both on an empty
DB and with a TEXT (comma-formatted) invoice amount present.
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

uid = db.insert("users", {"name": "smoke", "email": "smoke@test.local",
                          "role": "admin", "active": 1})
with c.session_transaction() as s:
    s["user_id"] = uid
    s["user_name"] = "smoke"
    s["user_role"] = "admin"

def hit(paths):
    bad = []
    for p in paths:
        r = c.get(p)
        if r.status_code >= 500:
            bad.append((p, r.status_code))
    return bad

# 1) Empty DB — home + alias must render.
bad = hit(["/", "/dashboard"])

# 2) Seed a job with an outstanding invoice whose amount is a NON-numeric TEXT
#    value. A raw INSERT guarantees the comma-formatted string lands as TEXT under
#    SQLite's REAL affinity (the exact prod condition), not silently coerced.
jid = db.insert("jobs", {"name": "Dash Job", "address": "1 Main St",
                         "stage": "production", "department": "",
                         "contract_value": "$25,000.00"})
conn = db.connect()
try:
    conn.execute("INSERT INTO invoices (job_id, number, amount, status) VALUES (?,?,?,?)",
                 (jid, "INV-1", "1,200.50", "sent"))
    conn.execute("INSERT INTO invoices (job_id, number, amount, status) VALUES (?,?,?,?)",
                 (None, "INV-ORPHAN", "3,000.00", "unpaid"))
    conn.commit()
finally:
    conn.close()

bad += hit(["/", "/dashboard"])

if bad:
    for b in bad:
        print("FAIL", b)
    sys.exit(1)
print("ALL_OK")
sys.exit(0)
'''


def test_dashboard_home_no_5xx_with_text_amounts():
    tmpdb = os.path.join(tempfile.mkdtemp(prefix="dash_smoke_"), "crm.db")
    env = dict(os.environ)
    env["DATABASE_URL"] = ""            # force SQLite
    env["POSTGRES_URL"] = ""
    env["POSTGRES_PRISMA_URL"] = ""
    env["CRM_DB_PATH"] = tmpdb          # throwaway DB
    env["CRM_DATA_DIR"] = os.path.dirname(tmpdb)
    env["CRM_NOBROWSER"] = "1"
    env["CRM_PORT"] = "5093"
    env.pop("RENDER", None)
    env.pop("CRM_ENV", None)

    code = _CHECK.replace("REPO_PLACEHOLDER", repr(REPO))
    r = subprocess.run([sys.executable, "-c", code], env=env,
                       capture_output=True, text=True, cwd=REPO, timeout=180)
    assert r.returncode == 0, (
        "dashboard route smoke failed:\nSTDOUT:\n%s\nSTDERR:\n%s"
        % (r.stdout, r.stderr))
    assert "ALL_OK" in r.stdout
