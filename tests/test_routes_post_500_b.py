# -*- coding: utf-8 -*-
"""Fresh-DB regression guard for the POST/form-submit 500 class in the
leads / jobs / contacts blueprints (wave-6 lane post-B).

Each seeded case reproduces a 500 that occurred BEFORE the fix:

  1. leads.move   — a JSON body that is NOT a dict (list / string / number / bool):
                    ``(request.get_json(silent=True) or {}).get("stage")`` — a truthy
                    non-dict skips the ``or {}`` fallback, so ``.get`` 500'd
                    (AttributeError: 'list'/'str'/'int'/'bool' object has no attribute 'get').
  2. jobs.move    — identical non-dict-JSON-body 500.
  3. contacts.merge — ``_CONTACT_FK_TABLES`` listed ``invoices``/``materials``, which
                    carry only ``job_id`` (no ``contact_id`` column), so a perfectly
                    valid GC merge (real survivor + real dupe) 500'd on both the preview
                    and the confirm path with ``sqlite3.OperationalError: no such
                    column: contact_id``.

The fixes coerce a non-dict JSON body to ``{}`` in both ``move`` handlers, and skip
any FK table whose ``contact_id`` column doesn't exist in the tenant's schema.

Also smoke-tests the rest of this lane's POST surface (new/edit/stage/snooze/check/
note/field/assign/convert/delete/inspection/pay/make-gc) on pathological input so a
regression anywhere in the slice trips this guard.

Boots the real app on a throwaway empty SQLite DB in an isolated subprocess and
asserts every one of these POST routes responds without a 5xx. The app enforces a
custom CSRF token (``session['_csrf']`` vs form ``_csrf`` / ``X-CSRFToken``), so the
harness sets the token exactly as the app expects rather than disabling it.
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
uid = db.insert("users", {"name": "PostGuardB", "email": "pgb@test.local",
                          "role": "admin", "active": 1})
lid = db.insert("leads", {"name": "L-1 Test", "stage": "prospect", "department": dept,
                          "checks": "{}"})
jid = db.insert("jobs", {"name": "R-1: Test", "stage": "approved", "department": dept,
                         "contract_value": "20000"})
cid = db.insert("contacts", {"first_name": "Con", "last_name": "Tact", "department": dept})
cid2 = db.insert("contacts", {"first_name": "Dup", "last_name": "Two", "department": dept})
insp_id = db.insert("inspections", {"job_id": jid, "type": "rough", "department": dept})

errors = {}
def _record(sender, exception, **extra):
    errors[getattr(_record, "cur", "?")] = "".join(
        traceback.format_exception(type(exception), exception, exception.__traceback__))
got_request_exception.connect(_record, a)

T = "tok"
with c.session_transaction() as s:
    s["user_id"] = uid; s["user_name"] = "PostGuardB"; s["user_role"] = "admin"
    s["department"] = dept; s["_csrf"] = T
HDR = {"X-CSRFToken": T}

L = "/leads/%d" % lid
J = "/jobs/%d" % jid
C = "/contacts/%d" % cid
MISSING = object()

# (label, kind, path, form_dict_or_None, json_obj)
cases = [
    # --- leads.move / jobs.move: non-dict JSON body must not 500 ---
    ("leads.move body=list", "json", L + "/move", None, [1, 2, 3]),
    ("leads.move body=str", "json", L + "/move", None, "nope"),
    ("leads.move body=num", "json", L + "/move", None, 5),
    ("leads.move body=true", "json", L + "/move", None, True),
    ("leads.move body=dict", "json", L + "/move", None, {"stage": "prospect"}),
    ("jobs.move body=list", "json", J + "/move", None, [1, 2, 3]),
    ("jobs.move body=str", "json", J + "/move", None, "nope"),
    ("jobs.move body=num", "json", J + "/move", None, 5),
    ("jobs.move body=true", "json", J + "/move", None, True),
    ("jobs.move body=dict", "json", J + "/move", None, {"stage": "approved"}),

    # --- contacts.merge: valid merge must not 500 on the invoices/materials schema ---
    ("contacts.merge preview", "form", C + "/merge", {"_csrf": T, "dupe_ids": str(cid2)}, MISSING),
    ("contacts.merge confirm", "form", C + "/merge", {"_csrf": T, "dupe_ids": str(cid2), "confirm": "1"}, MISSING),
    ("contacts.merge junk dupes", "form", C + "/merge", {"_csrf": T, "dupe_ids": "abc"}, MISSING),

    # --- broad slice smoke on pathological input ---
    ("leads.new empty", "form", "/leads/new", {"_csrf": T}, MISSING),
    ("leads.new junk", "form", "/leads/new", {"_csrf": T, "name": "X", "contact_id": "abc", "rank": "zz", "work_type": "Shingle Reroof", "area": "notnum"}, MISSING),
    ("leads.edit junk", "form", L + "/edit", {"_csrf": T, "rank": "abc", "contact_id": "xx"}, MISSING),
    ("leads.stage junk", "form", L + "/stage", {"_csrf": T, "stage": "nope", "ajax": "1"}, MISSING),
    ("leads.snooze days=abc", "form", L + "/snooze", {"_csrf": T, "days": "abc"}, MISSING),
    ("leads.snooze days=huge", "form", L + "/snooze", {"_csrf": T, "days": "999999999999999999999999"}, MISSING),
    ("leads.check nokey", "form", L + "/check", {"_csrf": T, "ajax": "1"}, MISSING),
    ("leads.note empty", "form", L + "/note", {"_csrf": T}, MISSING),
    ("leads.field junk", "form", L + "/field", {"_csrf": T, "field": "notreal", "value": "x"}, MISSING),
    ("leads.assign empty", "form", L + "/assign", {"_csrf": T}, MISSING),
    ("leads.convert", "form", L + "/convert", {"_csrf": T}, MISSING),

    ("jobs.new empty", "form", "/jobs/new", {"_csrf": T}, MISSING),
    ("jobs.new junk", "form", "/jobs/new", {"_csrf": T, "client": "X", "contact_id": "abc", "area": "notnum", "auto_name": "1"}, MISSING),
    ("jobs.edit empty", "form", J + "/edit", {"_csrf": T}, MISSING),
    ("jobs.stage junk", "form", J + "/stage", {"_csrf": T, "stage": "nope", "ajax": "1"}, MISSING),
    ("jobs.advance", "form", J + "/advance", {"_csrf": T}, MISSING),
    ("jobs.check nokey", "form", J + "/check", {"_csrf": T}, MISSING),
    ("jobs.pay woodAmt", "form", J + "/pay", {"_csrf": T, "key": "woodAmt", "value": "junk"}, MISSING),
    ("jobs.inspection empty", "form", J + "/inspection", {"_csrf": T}, MISSING),
    ("jobs.update_inspection", "form", "%s/inspection/%d" % (J, insp_id), {"_csrf": T, "result": "pass"}, MISSING),
    ("jobs.snooze days=abc", "form", J + "/snooze", {"_csrf": T, "days": "abc"}, MISSING),

    ("contacts.new junk", "form", "/contacts/new", {"_csrf": T, "is_gc": "maybe", "first_name": "A"}, MISSING),
    ("contacts.make-gc junk", "form", C + "/make-gc", {"_csrf": T, "is_gc": "maybe"}, MISSING),
    ("contacts.note empty", "form", C + "/note", {"_csrf": T}, MISSING),
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

print("POST_500_B_OK count=%d" % len(cases))
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
    env["CRM_PORT"] = "5098"
    env["CRM_DEMO"] = "0"
    env.pop("RENDER", None)
    env.pop("CRM_ENV", None)
    return env


def test_leads_jobs_contacts_post_routes_no_5xx_on_bad_input():
    tmpdb = os.path.join(tempfile.mkdtemp(prefix="post500b_smoke_"), "crm.db")
    assert not os.path.exists(tmpdb), "temp DB should not pre-exist"
    code = _CHECK.replace("REPO_PLACEHOLDER", repr(REPO))
    r = subprocess.run([sys.executable, "-c", code], env=_fresh_env(tmpdb),
                       capture_output=True, text=True, cwd=REPO, timeout=240)
    assert r.returncode == 0, (
        "POST-route 500 guard (leads/jobs/contacts) failed:\nSTDOUT:\n%s\nSTDERR:\n%s"
        % (r.stdout, r.stderr))
    assert "POST_500_B_OK" in r.stdout, "unexpected output:\n%s" % r.stdout
