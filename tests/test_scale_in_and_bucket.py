# -*- coding: utf-8 -*-
"""Regression guards for the WAVE-5 latent-scale + bucket-correctness bugs in the
``modules/{estimates,leads,jobs}.py`` list/index routes.

Two distinct defect classes are locked down, both reproduced before/after the fix:

1. **"too many SQL variables" 500 (scale).** ``estimates.index`` (and the jobs.list
   profit JOIN) built ``... IN (?,?,…)`` with **one bound param per row**. SQLite caps a
   statement at ``SQLITE_LIMIT_VARIABLE_NUMBER`` = 32766, so a department that grew past
   ~32k estimates / sections / jobs 500'd with *"too many SQL variables"* — a latent crash
   an aged prod DB walks into silently. Guarded two ways:
     * the ``estimates._in_batches`` chunker returns **every** row across multiple batches
       (correctness), and
     * a single IN over 50 000 ids **raises** the var-limit error while the chunker over
       the *same* ids does **not** (the exact before/after).

2. **Bogus ``?bucket=`` leaked ALL rows (correctness).** ``leads.list`` / ``jobs.list``
   mapped a bucket to its stage keys; when the bucket matched no stage, ``_bstages`` was
   empty, the ``stage IN (…)`` filter was **skipped**, and on the paginated code path
   (which has no Python bucket fallback) the route returned **every** department row
   instead of none. Now an unknown bucket yields **zero** rows on both paths, while a
   valid bucket still returns exactly its stages.

Boots the real app on a throwaway empty SQLite DB in an isolated subprocess (mirrors
tests/test_routes_a_fresh_db.py) so it neither depends on nor mutates the dev database.
"""
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHECK = r'''
import os, sys, traceback
sys.path.insert(0, REPO_PLACEHOLDER)
os.chdir(REPO_PLACEHOLDER)

import app as appmod
import db, theme, constants
from flask import got_request_exception
from modules import estimates as est_mod

a = appmod.app
a.config["WTF_CSRF_ENABLED"] = False

errors = {}
def _record(sender, exception, **extra):
    errors[getattr(_record, "cur", "?")] = "".join(
        traceback.format_exception(type(exception), exception, exception.__traceback__))
got_request_exception.connect(_record, a)

c = a.test_client()
dept  = theme.departments(db.get_company())[0]
other = "OTHER Department"
fail = []

# ==========================================================================
# (1a) _in_batches correctness — every row returned across multiple batches.
# ==========================================================================
conn = db.connect()
try:
    e_chunk = db.insert("estimates", {"number": "EST-CH", "title": "CHUNK",
                                      "status": "draft", "margin_pct": 30, "tax_pct": 0})
    made = [db.insert("estimate_sections",
                      {"estimate_id": e_chunk, "sort": i, "name": "S%d" % i,
                       "margin_pct": 30, "scope_text": ""})
            for i in range(25)]
    _orig = est_mod._SQL_IN_BATCH
    est_mod._SQL_IN_BATCH = 7          # 25 ids -> batches 7,7,7,4 (spans 4 queries)
    got = est_mod._in_batches(
        conn, "SELECT * FROM estimate_sections WHERE id IN ", made, " ORDER BY id")
    est_mod._SQL_IN_BATCH = _orig
    if len(got) != 25 or sorted(r["id"] for r in got) != sorted(made):
        fail.append("_in_batches lost rows across batches: got %d of 25" % len(got))
    if est_mod._in_batches(conn, "SELECT * FROM estimate_sections WHERE id IN ", []) != []:
        fail.append("_in_batches on empty ids should return [] and run no query")

    # ==================================================================
    # (1b) var-limit before/after: the OLD single-IN raises, chunker does not.
    # ==================================================================
    # 50000 ids (> SQLITE_LIMIT_VARIABLE_NUMBER = 32766) chosen well above any real row
    # id so the chunked query legitimately matches nothing (isolates the var-limit test).
    big = list(range(1_000_000, 1_050_000))
    _ph = ",".join("?" * len(big))
    raised = ""
    try:
        conn.execute("SELECT * FROM estimate_sections WHERE id IN (%s)" % _ph, big).fetchall()
    except Exception as ex:
        raised = str(ex)
    if "too many SQL variables" not in raised:
        fail.append("expected the un-chunked IN over 50k ids to raise the var-limit "
                    "error, got: %r" % raised)
    try:
        if est_mod._in_batches(conn, "SELECT * FROM estimate_sections WHERE id IN ", big) != []:
            fail.append("chunked _in_batches over 50k ids should match nothing here")
    except Exception as ex:
        fail.append("chunked _in_batches over 50k ids must NOT raise, but did: %s" % ex)
finally:
    conn.close()

# ==========================================================================
# (1c) estimates.index route — subquery dept-scoping is correct (2 dept-A + orphan
#      shown, dept-B hidden) and the page renders (no 500) with real sections/lines.
# ==========================================================================
uid = db.insert("users", {"name": "Scale Admin", "email": "scale@test.local",
                          "role": "admin", "active": 1})
la = db.insert("leads", {"name": "SCALEPROBE_DEPTA", "stage": "assigned", "department": dept})
est_mod.build_estimate(lead_id=la, work_type="Shingle Reroof")   # real sections + lines
db.insert("estimates", {"number": "EST-ORPH", "title": "SCALEPROBE_ORPHAN",
                        "status": "draft", "margin_pct": 30, "tax_pct": 0})  # null parent
lb = db.insert("leads", {"name": "SCALEPROBE_DEPTB", "stage": "assigned", "department": other})
est_mod.build_estimate(lead_id=lb, work_type="Shingle Reroof")   # other dept -> excluded

# ==========================================================================
# (2) bucket seed rows for leads + jobs (distinct names -> presence == inclusion).
# ==========================================================================
db.insert("leads", {"name": "BKTPROBE_PROSPECT", "stage": "prospect", "department": dept})
db.insert("leads", {"name": "BKTPROBE_ASSIGNED", "stage": "assigned", "department": dept})
db.insert("jobs", {"name": "BKTPROBE_APPROVED", "stage": "approved", "department": dept})
db.insert("jobs", {"name": "BKTPROBE_COMPLETED", "stage": "completed", "department": dept})

with c.session_transaction() as s:
    s["user_id"] = uid
    s["user_name"] = "Scale Admin"
    s["user_role"] = "admin"
    s["department"] = dept


def body(path):
    _record.cur = path
    r = c.get(path)
    if r.status_code >= 500:
        fail.append("%s -> HTTP %s\n%s" % (path, r.status_code,
                                           errors.get(path, "(no traceback)")))
        return ""
    return r.get_data(as_text=True)


def want(path, present, absent):
    txt = body(path)
    for tok in present:
        if tok not in txt:
            fail.append("%s: expected %r in response but it was MISSING" % (path, tok))
    for tok in absent:
        if tok in txt:
            fail.append("%s: %r LEAKED into response (should be filtered out)" % (path, tok))


# estimates.index: dept-A estimate + orphan visible; dept-B estimate scoped out.
want("/estimates/", ["SCALEPROBE_DEPTA", "SCALEPROBE_ORPHAN"], ["SCALEPROBE_DEPTB"])

# leads.list bucket — paginated path (sort=date) is where the leak lived.
want("/leads/list?bucket=zzz_nope&sort=date", [], ["BKTPROBE_PROSPECT", "BKTPROBE_ASSIGNED"])
want("/leads/list?bucket=zzz_nope", [], ["BKTPROBE_PROSPECT", "BKTPROBE_ASSIGNED"])  # days path
want("/leads/list?bucket=prospect&sort=date", ["BKTPROBE_PROSPECT"], ["BKTPROBE_ASSIGNED"])
want("/leads/list?bucket=prospect", ["BKTPROBE_PROSPECT"], ["BKTPROBE_ASSIGNED"])   # days path

# jobs.list bucket — default sort=date is the paginated (leaky) path.
want("/jobs/list?bucket=zzz_nope", [], ["BKTPROBE_APPROVED", "BKTPROBE_COMPLETED"])
want("/jobs/list?bucket=approved", ["BKTPROBE_APPROVED"], ["BKTPROBE_COMPLETED"])

if fail:
    for f in fail:
        print("FAIL:", f)
        print("-" * 60)
    sys.exit(1)
print("SCALE_IN_AND_BUCKET_OK")
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
    env["CRM_DEMO"] = "0"               # do not seed demo data — keep the DB fresh
    env.pop("RENDER", None)
    env.pop("CRM_ENV", None)
    return env


def test_scale_in_chunking_and_bucket_correctness():
    tmpdb = os.path.join(tempfile.mkdtemp(prefix="scale_bucket_"), "crm.db")
    assert not os.path.exists(tmpdb), "temp DB should not pre-exist"
    code = _CHECK.replace("REPO_PLACEHOLDER", repr(REPO))
    r = subprocess.run([sys.executable, "-c", code], env=_fresh_env(tmpdb),
                       capture_output=True, text=True, cwd=REPO, timeout=240)
    assert r.returncode == 0, (
        "scale-IN + bucket guard failed:\nSTDOUT:\n%s\nSTDERR:\n%s"
        % (r.stdout, r.stderr))
    assert "SCALE_IN_AND_BUCKET_OK" in r.stdout, "unexpected output:\n%s" % r.stdout
