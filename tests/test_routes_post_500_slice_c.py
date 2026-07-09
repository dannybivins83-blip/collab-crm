# -*- coding: utf-8 -*-
"""Fresh-DB regression guard for POST/mutation 500s in the permits / measurements /
sitecam slice (WAVE 6 post-C). Each case reproduces a 500 that occurred BEFORE the fix:

  1. permits.contractor_profile_save — a NON-numeric hidden ``id`` ('abc', '1.5')
     reached ``int(pid)`` -> ValueError 500. Fix: coerce id-or-None, junk -> new insert.
  2. measurements.ingest — a valid-JSON but NON-object body (list / string / number /
     null) reached ``body.get(...)`` -> AttributeError 500. Fix: normalize to {}.
  3. measurements.ingest — ``job_id`` / ``lead_id`` sent as a dict/list bound straight
     into ``db.get`` -> sqlite InterfaceError 500. Fix: ``_as_id`` coercion (non-scalar
     -> None -> skip).
  4. sitecam.gallery_link — a non-object JSON body reached ``data.get(...)`` -> 500.
     Fix: normalize to {}.
  5. sitecam.gallery_link — ``rid`` as a list / ``address`` as an int crashed on
     ``.strip()`` / ``.split()`` in ``_match_job``. Fix: str()-coerce both.

The HMAC/secret-gated ingest + gallery routes are tested on the AUTH-PASSING path:
the harness pins the dev-fallback secret ('<tenant>-webhook-secret') by clearing the
env override, then signs / sends the shared secret so the request reaches the handler.
Boots the real app on a throwaway empty SQLite DB in an isolated subprocess.
"""
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHECK = r'''
import os, sys, json, hmac, hashlib, traceback
sys.path.insert(0, REPO_PLACEHOLDER)
os.chdir(REPO_PLACEHOLDER)

import app as appmod
import db, theme
from flask import got_request_exception

a = appmod.app
c = a.test_client()

dept = theme.departments(db.get_company())[0]
uid = db.insert("users", {"name": "PostGuardC", "email": "pgc@test.local",
                          "role": "admin", "active": 1})
jid = db.insert("jobs", {"name": "R-1: Test", "stage": "approved", "department": dept,
                         "contract_value": "20000"})

errors = {}
def _record(sender, exception, **extra):
    errors[getattr(_record, "cur", "?")] = "".join(
        traceback.format_exception(type(exception), exception, exception.__traceback__))
got_request_exception.connect(_record, a)

T = "tok"
with c.session_transaction() as s:
    s["user_id"] = uid; s["user_name"] = "PostGuardC"; s["user_role"] = "admin"
    s["department"] = dept; s["_csrf"] = T
HDR = {"X-CSRFToken": T}

# Dev-fallback shared secret (env override cleared by the harness -> deterministic).
SECRET = "seabreeze-webhook-secret"
def _sig(raw):
    return hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()

form_cases = [
    # permits.contractor_profile_save — junk hidden id must not 500.
    ("permits.cprofile id=abc", "/permits/contractor-profile/save", {"_csrf": T, "id": "abc"}),
    ("permits.cprofile id=1.5", "/permits/contractor-profile/save", {"_csrf": T, "id": "1.5"}),
]

# (label, path, raw_body, headers) — HMAC/secret-gated, auth-passing path.
raw_cases = []
for lbl, raw in [
    ("meas.ingest list-body", b"[1,2,3]"),
    ("meas.ingest str-body", b'"hello"'),
    ("meas.ingest num-body", b"123"),
    ("meas.ingest null-body", b"null"),
    ("meas.ingest job_id=dict", json.dumps({"job_id": {"x": 1}}).encode()),
    ("meas.ingest lead_id=list", json.dumps({"lead_id": [1, 2]}).encode()),
    ("meas.ingest good", json.dumps({"job_id": jid, "squares": "12.5"}).encode()),
]:
    raw_cases.append((lbl, "/measurements/ingest", raw,
                      {"X-Signature": _sig(raw), "Content-Type": "application/json"}))

_SC = {"x-sitecam-secret": SECRET, "Content-Type": "application/json"}
for lbl, raw in [
    ("sitecam list-body", b"[1,2,3]"),
    ("sitecam str-body", b'"hi"'),
    ("sitecam num-body", b"5"),
    ("sitecam rid=list", json.dumps({"url": "http://x", "rid": [1, 2]}).encode()),
    ("sitecam address=int", json.dumps({"url": "http://x", "address": 5}).encode()),
    ("sitecam zip=int", json.dumps({"url": "http://x", "address": "1 Main St", "zip": 33400}).encode()),
]:
    raw_cases.append((lbl, "/sitecam/gallery", raw, dict(_SC)))

bad = []
for label, path, form in form_cases:
    _record.cur = label
    try:
        sc = c.post(path, data=form).status_code
    except Exception:
        errors[label] = traceback.format_exc(); sc = "EXC"
    if sc == "EXC" or (isinstance(sc, int) and sc >= 500):
        bad.append((label, path, sc))

for label, path, raw, hdr in raw_cases:
    _record.cur = label
    try:
        sc = c.post(path, data=raw, headers=hdr).status_code
    except Exception:
        errors[label] = traceback.format_exc(); sc = "EXC"
    if sc == "EXC" or (isinstance(sc, int) and sc >= 500):
        bad.append((label, path, sc))

if bad:
    for label, path, sc in bad:
        print("FAIL", sc, label, path)
        print(errors.get(label, "(no server-side traceback captured)"))
        print("-" * 60)
    sys.exit(1)

print("POST_500_SLICE_C_OK count=%d" % (len(form_cases) + len(raw_cases)))
sys.exit(0)
'''


def _fresh_env(tmpdb):
    env = dict(os.environ)
    env["DATABASE_URL"] = ""            # force SQLite even if a PG URL is ambient
    env["POSTGRES_URL"] = ""
    env["POSTGRES_PRISMA_URL"] = ""
    # Pin the dev-fallback webhook secret by clearing any ambient override so the
    # harness's own HMAC/secret matches the handler's expectation (auth-passing path).
    env["MEASURE_CRM_WEBHOOK_SECRET"] = ""
    env["SEABREEZE_CRM_WEBHOOK_SECRET"] = ""
    env["CRM_DB_PATH"] = tmpdb          # throwaway, guaranteed-empty DB file
    env["CRM_DATA_DIR"] = os.path.dirname(tmpdb)
    env["CRM_NOBROWSER"] = "1"
    env["CRM_PORT"] = "5093"
    env["CRM_DEMO"] = "0"
    env.pop("RENDER", None)
    env.pop("CRM_ENV", None)
    return env


def test_post_slice_c_no_5xx_on_bad_input():
    tmpdb = os.path.join(tempfile.mkdtemp(prefix="post500c_smoke_"), "crm.db")
    assert not os.path.exists(tmpdb), "temp DB should not pre-exist"
    code = _CHECK.replace("REPO_PLACEHOLDER", repr(REPO))
    r = subprocess.run([sys.executable, "-c", code], env=_fresh_env(tmpdb),
                       capture_output=True, text=True, cwd=REPO, timeout=240)
    assert r.returncode == 0, (
        "POST-route 500 guard (slice C) failed:\nSTDOUT:\n%s\nSTDERR:\n%s"
        % (r.stdout, r.stderr))
    assert "POST_500_SLICE_C_OK" in r.stdout, "unexpected output:\n%s" % r.stdout
