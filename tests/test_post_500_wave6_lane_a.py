# -*- coding: utf-8 -*-
"""Wave-6 fresh-DB regression guard for the POST/mutation 500 class on the
previously-unswept surfaces in modules/{worksheet,orders,commissions,
automations,comms}.py — form + JSON handlers that crashed on pathological input
the browser UI never sends but a hand-crafted / legacy / scripted POST can.

Each seeded case reproduces a 500 that occurred BEFORE the fix:

  worksheet.save  — a non-dict JSON body (list) -> ``data.get`` AttributeError;
                    a non-list ``lines`` (str) or non-dict line element ->
                    ``ln.get`` AttributeError; a non-numeric ``budget_cost`` ->
                    ``float('abc')`` ValueError.
  orders.save     — same shapes: non-dict body, non-list/str ``lines``, non-dict
                    line, non-numeric ``qty``/``cost``.
  commissions.save— a non-numeric ``rate_pct`` -> ``float('abc')`` ValueError.
  automations.new / .save / sequence_add_step — a non-numeric ``offset_days`` /
                    ``step_no`` -> ``int('abc')`` ValueError.
  automations.sequence_enroll — a ``target`` of 'lead:abc' -> ``int(eid)`` 500.
  comms.log / .draft / .sms   — a ``target`` of 'lead:abc' -> ``int(eid)`` 500.

The fix coerces money/int fields with safe helpers (junk -> 0), guards non-dict
bodies / non-list lines / non-dict elements, and gates ``int(eid)`` behind
``eid.isdigit()``. A well-formed save must still persist its lines — asserted too.

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
uid = db.insert("users", {"name": "PostGuard", "email": "pg6@test.local",
                          "role": "admin", "active": 1})
jid = db.insert("jobs", {"name": "R-1: Test", "stage": "approved",
                         "department": dept, "contract_value": "20000"})
oid = db.insert("orders", {"job_id": jid, "type": "Material", "po_number": "PO-M-0001",
                           "status": "draft", "department": dept})
cid = db.insert("commissions", {"job_id": jid, "rep": "Rep", "basis": "profit",
                                "rate_pct": 10, "status": "pre", "department": dept})
aid = db.insert("automations", {"name": "A", "trigger_stage": "", "action_type": "create_task",
                                "offset_days": 0, "active": 1})
sqid = db.insert("sequences", {"created": db.now(), "name": "S", "active": 1, "department": dept})

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

def F(d):
    d = dict(d); d["_csrf"] = T; return d

# (label, kind, path, form_dict, json_obj)
cases = [
    # worksheet.save — malformed JSON shapes + non-numeric money.
    ("ws.save body=list", "json", "/worksheet/%d/save" % jid, None, [1, 2, 3]),
    ("ws.save lines=str", "json", "/worksheet/%d/save" % jid, None, {"lines": "nope"}),
    ("ws.save budget junk", "json", "/worksheet/%d/save" % jid, None,
     {"lines": [{"description": "d", "budget_cost": "abc"}]}),
    ("ws.save line=notdict", "json", "/worksheet/%d/save" % jid, None, {"lines": ["x", 5]}),
    # orders.save — same shapes + non-numeric qty/cost.
    ("ord.save body=list", "json", "/orders/%d/save" % oid, None, [1, 2, 3]),
    ("ord.save qty junk", "json", "/orders/%d/save" % oid, None,
     {"lines": [{"description": "d", "qty": "abc"}]}),
    ("ord.save lines=str", "json", "/orders/%d/save" % oid, None, {"lines": "nope"}),
    ("ord.save line=notdict", "json", "/orders/%d/save" % oid, None, {"lines": ["x", 5]}),
    # commissions.save — non-numeric rate.
    ("comm.save rate junk", "form", "/commissions/%d/save" % cid, F({"rate_pct": "abc"}), None),
    # automations — non-numeric int fields + bad enroll target.
    ("auto.new offset junk", "form", "/workflow/new", F({"name": "x", "offset_days": "abc"}), None),
    ("auto.save offset junk", "form", "/workflow/%d/save" % aid, F({"name": "x", "offset_days": "abc"}), None),
    ("auto.step step_no junk", "form", "/workflow/sequences/%d/step" % sqid,
     F({"step_no": "abc", "offset_days": "z"}), None),
    ("auto.enroll eid junk", "form", "/workflow/sequences/%d/enroll" % sqid, F({"target": "lead:abc"}), None),
    # comms — bad int(eid) target.
    ("comms.log eid junk", "form", "/comms/log", F({"target": "lead:abc", "kind": "call", "text": "hi"}), None),
    ("comms.draft eid junk", "form", "/comms/draft", F({"target": "lead:abc", "subject": "s", "body": "b"}), None),
    ("comms.sms eid junk", "form", "/comms/sms", F({"target": "lead:abc", "to": "555", "body": "b"}), None),
    # Happy paths must still succeed (regression the other direction).
    ("ws.save good", "json", "/worksheet/%d/save" % jid, None,
     {"contract_value": "$25,000", "lines": [{"description": "Shingles", "category": "Material",
      "budget_cost": "1500.50", "qty": "30", "unit": "SQ", "unit_cost": "50"}]}),
    ("ord.save good", "json", "/orders/%d/save" % oid, None,
     {"vendor": "ABC", "lines": [{"description": "Nails", "unit": "BX", "qty": "5", "cost": "12.5"}]}),
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

# The two well-formed saves must have actually persisted their line.
wl = db.all_rows("worksheet_lines", order="id")
ol = db.all_rows("order_lines", order="id")
if not any((w.get("description") or "") == "Shingles" and abs((w.get("budget_cost") or 0) - 1500.5) < 0.001
           for w in wl):
    bad.append(("ws.save good did not persist line", "/worksheet/save", "PERSIST"))
if not any((o.get("description") or "") == "Nails" and abs((o.get("cost") or 0) - 12.5) < 0.001
           for o in ol):
    bad.append(("ord.save good did not persist line", "/orders/save", "PERSIST"))

if bad:
    for label, path, sc in bad:
        print("FAIL", sc, label, path)
        print(errors.get(label, "(no server-side traceback captured)"))
        print("-" * 60)
    sys.exit(1)

print("POST_500_WAVE6_OK count=%d" % len(cases))
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
    env["CRM_PORT"] = "5096"
    env["CRM_DEMO"] = "0"
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("RENDER", None)
    env.pop("CRM_ENV", None)
    return env


def test_wave6_post_routes_no_5xx_on_bad_input():
    tmpdb = os.path.join(tempfile.mkdtemp(prefix="post500_wave6_"), "crm.db")
    assert not os.path.exists(tmpdb), "temp DB should not pre-exist"
    code = _CHECK.replace("REPO_PLACEHOLDER", repr(REPO))
    r = subprocess.run([sys.executable, "-c", code], env=_fresh_env(tmpdb),
                       capture_output=True, text=True, cwd=REPO, timeout=240)
    assert r.returncode == 0, (
        "Wave-6 POST-route 500 guard failed:\nSTDOUT:\n%s\nSTDERR:\n%s"
        % (r.stdout, r.stderr))
    assert "POST_500_WAVE6_OK" in r.stdout, "unexpected output:\n%s" % r.stdout
