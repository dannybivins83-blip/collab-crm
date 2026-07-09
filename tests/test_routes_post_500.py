# -*- coding: utf-8 -*-
"""Fresh-DB regression guard for the POST/form-submit 500 class — form + JSON
handlers that crashed on pathological input the browser UI never sends but a
hand-crafted / legacy / scripted POST can.

Each seeded case reproduces a 500 that occurred BEFORE the fix:

  1. invoices.new         — a NON-numeric ``job_id`` form value ('abc') reached
                            ``int(job_id)`` in the activity-log call -> ValueError 500
                            (the FK insert also silently took the junk string).
  2. estimates.save       — a non-numeric ``tax_pct`` -> ``float('abc')`` 500.
  3. estimates.save       — a non-numeric section ``margin_pct`` -> ``float`` 500.
  4. estimates.save       — a non-numeric line ``qty`` (and cost/price/waste) 500.
  5. estimates.save       — ``sections`` sent as a string (not a list): iterating
                            it yielded chars and ``sec.get`` 500'd (AttributeError);
                            likewise a non-dict top-level body / non-dict elements.

The fix coerces money fields with a local safe-float (junk -> 0.0), coerces the
invoice ``job_id`` to int-or-None, and skips any non-dict section/line element.

Boots the real app on a throwaway empty SQLite DB in an isolated subprocess and
asserts every one of these POST routes responds without a 5xx. The app enforces
a custom CSRF token (``session['_csrf']`` vs form ``_csrf`` / ``X-CSRFToken``),
so the harness sets the token exactly as the app expects rather than disabling it.
"""
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHECK = r'''
import os, sys, json, traceback
sys.path.insert(0, REPO_PLACEHOLDER)
os.chdir(REPO_PLACEHOLDER)

import app as appmod
import db, theme
from flask import got_request_exception

a = appmod.app
c = a.test_client()

dept = theme.departments(db.get_company())[0]
uid = db.insert("users", {"name": "PostGuard", "email": "pg@test.local",
                          "role": "admin", "active": 1})
jid = db.insert("jobs", {"name": "R-1: Test", "stage": "approved", "department": dept,
                         "contract_value": "20000"})
from modules import estimates as est_mod
eid = est_mod.build_estimate(job_id=jid, work_type="Shingle Reroof")

errors = {}
def _record(sender, exception, **extra):
    errors[getattr(_record, "cur", "?")] = "".join(
        traceback.format_exception(type(exception), exception, exception.__traceback__))
got_request_exception.connect(_record, a)

T = "tok"
with c.session_transaction() as s:
    s["user_id"] = uid; s["user_name"] = "PostGuard"; s["user_role"] = "admin"
    s["department"] = dept; s["_csrf"] = T
HDR = {"X-CSRFToken": T}

# (label, kind, path, form_dict, json_obj)
cases = [
    # invoices.new — non-numeric / out-of-range job_id must not 500 (int() crash).
    ("inv.new job_id=abc", "form", "/invoices/new", {"_csrf": T, "job_id": "abc", "amount": "100"}, None),
    ("inv.new job_id=1.5", "form", "/invoices/new", {"_csrf": T, "job_id": "1.5", "amount": "N/A"}, None),
    # estimates.save — non-numeric money fields + malformed JSON shapes.
    ("est.save tax junk", "json", "/estimates/%d/save" % eid, None, {"tax_pct": "abc", "sections": []}),
    ("est.save margin junk", "json", "/estimates/%d/save" % eid, None,
     {"sections": [{"name": "S", "margin_pct": "xx", "lines": []}]}),
    ("est.save line junk", "json", "/estimates/%d/save" % eid, None,
     {"sections": [{"name": "S", "lines": [{"description": "d", "qty": "abc",
                                            "cost": "z", "price": "p", "waste_pct": "w"}]}]}),
    ("est.save sections=str", "json", "/estimates/%d/save" % eid, None, {"sections": "nope"}),
    ("est.save body=list", "json", "/estimates/%d/save" % eid, None, [1, 2, 3]),
    ("est.save sec=notdict", "json", "/estimates/%d/save" % eid, None, {"sections": ["x", 5]}),
    ("est.save line=notdict", "json", "/estimates/%d/save" % eid, None,
     {"sections": [{"name": "S", "lines": ["a", 1]}]}),
]

bad = []
for label, kind, path, form, js in cases:
    _record.cur = label
    try:
        if kind == "json":
            r = c.post(path, data=json.dumps(js), content_type="application/json", headers=HDR)
        else:
            r = c.post(path, data=form)
        sc = r.status_code
    except Exception:
        errors[label] = traceback.format_exc()
        sc = "EXC"
    if sc == "EXC" or (isinstance(sc, int) and sc >= 500):
        bad.append((label, path, sc))

if bad:
    for label, path, sc in bad:
        print("FAIL", sc, label, path)
        print(errors.get(label, "(no server-side traceback captured)"))
        print("-" * 60)
    sys.exit(1)

print("POST_500_OK count=%d" % len(cases))
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
    env["CRM_PORT"] = "5094"
    env["CRM_DEMO"] = "0"
    env.pop("RENDER", None)
    env.pop("CRM_ENV", None)
    return env


def test_post_routes_no_5xx_on_bad_input():
    tmpdb = os.path.join(tempfile.mkdtemp(prefix="post500_smoke_"), "crm.db")
    assert not os.path.exists(tmpdb), "temp DB should not pre-exist"
    code = _CHECK.replace("REPO_PLACEHOLDER", repr(REPO))
    r = subprocess.run([sys.executable, "-c", code], env=_fresh_env(tmpdb),
                       capture_output=True, text=True, cwd=REPO, timeout=240)
    assert r.returncode == 0, (
        "POST-route 500 guard failed:\nSTDOUT:\n%s\nSTDERR:\n%s"
        % (r.stdout, r.stderr))
    assert "POST_500_OK" in r.stdout, "unexpected output:\n%s" % r.stdout
