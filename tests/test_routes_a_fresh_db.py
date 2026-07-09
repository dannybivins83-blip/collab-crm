# -*- coding: utf-8 -*-
"""Fresh-DB + pathological-data route guard for the routes-a slice
(``modules/{contacts,leads,jobs,estimates}.py``).

Regression guard for the LIVE-500 crash class that hit this slice in production:

  * **contacts** 500'd on every load, and **roof-reports** 500'd on a brand-new
    tenant DB — both because a GET route filtered / iterated on state that a
    fresh white-label deploy (empty tables, no company settings, records with
    NULL columns) did not satisfy. Prod's own aged DB masked the defect.

The bug *classes* this locks down for every GET route the four blueprints
expose (list / board / detail / new / edit / index / print / pdf, plus the
sort / filter / pagination / search query-params):

  1. **fresh-DB missing column** — a column added lazily by ``_ensure_column``
     (``contacts.is_gc``, ``leads.phone2``, ``jobs.foreman`` / ``crew``) that a
     WHERE/ORDER clause references. Asserted present right after ``init_db``.
  2. **unguarded int()/type coercion on query params** — ``?page=abc``,
     ``?page=-5``, ``?page=0`` must not 500.
  3. **None / empty-data crashes** — rows with NULL name / stage / money /
     dates, an unknown stage key, an orphan estimate with no parent and an
     estimate whose section/line columns are NULL, all decorated on the board.

Boots the real app on a throwaway empty SQLite DB in an isolated subprocess
(so it neither depends on nor mutates the dev database) and asserts every
exercised route responds without a 5xx, printing the captured server-side
traceback for any that do.
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

a = appmod.app
a.config["WTF_CSRF_ENABLED"] = False

# ---- (1) fresh-DB missing-column guard -----------------------------------
# These columns are added by _ensure_column at *module import* time (not the
# base db.py SCHEMA). They MUST exist after `import app`, because GET routes in
# this slice filter/search on them (contacts.gcs -> WHERE is_gc=1;
# leads.list phone search -> REPLACE(phone2, ...)). A future edit that moves an
# _ensure_column into a request handler would resurrect the roof-reports bug.
for _tbl, _col in [("contacts", "is_gc"), ("leads", "phone2"),
                   ("leads", "company"), ("jobs", "foreman"), ("jobs", "crew")]:
    assert _col in db._columns(_tbl), (
        "%s.%s missing after boot -- fresh-DB GET route will 500" % (_tbl, _col))

errors = {}
def _record(sender, exception, **extra):
    errors[getattr(_record, "cur", "?")] = "".join(
        traceback.format_exception(type(exception), exception, exception.__traceback__))
got_request_exception.connect(_record, a)

c = a.test_client()

# The board/list routes scope to the masthead department; seed into the real
# default so the pathological rows are actually loaded + decorated (not filtered
# out and silently skipped).
dept = theme.departments(db.get_company())[0]

uid = db.insert("users", {"name": "Guard Admin", "email": "guard@test.local",
                          "role": "admin", "active": 1})

# ---- (3) pathological rows: NULLs, unknown/None stages, junk money --------
cid = db.insert("contacts", {"kind": "person", "department": dept})  # all-NULL names
gcid = db.insert("contacts", {"kind": "company", "first_name": "Sam", "last_name": "GC",
                              "company": "SamCo", "is_gc": 1, "phone": "5619998888",
                              "email": "sam@co.com", "department": dept})
# a duplicate that contacts._dupe_candidates should surface on the GC detail page
dup = db.insert("contacts", {"first_name": "Sam", "last_name": "GC", "phone": "5619998888",
                             "email": "sam@co.com", "department": dept})

lid = db.insert("leads", {"name": None, "stage": "bogus_stage", "department": dept,
                          "estimate": "($500)", "contact_id": cid, "phone": "5610001111",
                          "phone2": "5610002222", "last_contact": None})
lid2 = db.insert("leads", {"name": None, "stage": None, "department": dept, "estimate": None})
db.insert("leads", {"contact_id": dup, "name": "Dupe Lead", "stage": "new", "department": dept})

jid = db.insert("jobs", {"name": None, "stage": "bogus_stage", "department": dept,
                         "contract_value": None, "contact_id": gcid, "balance": "5000"})
jid2 = db.insert("jobs", {"name": None, "stage": None, "department": dept,
                          "contract_value": "xyz"})
# a job under the GC with a worksheet, to exercise the profit-JOIN path
gjob = db.insert("jobs", {"contact_id": gcid, "name": "GC Job", "stage": "approved",
                          "department": dept, "contract_value": "40000"})
wsid = db.insert("worksheets", {"job_id": gjob, "contract_value": 40000})
db.insert("worksheet_lines", {"worksheet_id": wsid, "actual_cost": 12000, "budget_cost": 10000})

# estimate with real sections/lines on a dept lead (populated totals path)
from modules import estimates as est_mod
eid = est_mod.build_estimate(lead_id=lid, work_type="Shingle Reroof")
# an ORPHAN estimate (no parent job/lead) with a NULL-column section+line
eid_orphan = db.insert("estimates", {"number": None, "title": None, "status": None,
                                     "margin_pct": None, "tax_pct": None})
sid = db.insert("estimate_sections", {"estimate_id": eid_orphan, "sort": 0, "name": None,
                                      "scope_text": None, "margin_pct": None})
db.insert("estimate_lines", {"estimate_id": eid_orphan, "section_id": sid, "description": None,
                             "unit": None, "qty": None, "cost": None, "price": None, "waste_pct": None})

with c.session_transaction() as s:
    s["user_id"] = uid
    s["user_name"] = "Guard Admin"
    s["user_role"] = "admin"
    s["department"] = dept

paths = [
    # contacts — list/search/pagination (incl. (2) bad int coercion), gcs, detail, forms
    "/contacts/", "/contacts/?q=sam", "/contacts/?q=5619998888",
    "/contacts/?page=0", "/contacts/?page=abc", "/contacts/?page=-5", "/contacts/?page=999",
    "/contacts/gcs", "/contacts/gcs?q=sam",
    "/contacts/%d" % cid, "/contacts/%d" % gcid, "/contacts/%d/edit" % cid,
    "/contacts/new", "/contacts/new?gc=1", "/contacts/999999",
    # leads — board (all sorts), list (all sorts + filters + bad page), detail, forms
    "/leads/", "/leads/?show_lost=1", "/leads/?sort=est", "/leads/?sort=name", "/leads/?sort=clock",
    "/leads/list", "/leads/list?sort=days", "/leads/list?sort=value", "/leads/list?sort=name",
    "/leads/list?sort=rid", "/leads/list?sort=date", "/leads/list?overdue=1",
    "/leads/list?stage=new", "/leads/list?bucket=open", "/leads/list?q=dupe",
    "/leads/list?q=5610001111", "/leads/list?page=0", "/leads/list?page=abc",
    "/leads/list?rep=Nobody",
    "/leads/new", "/leads/%d" % lid, "/leads/%d" % lid2, "/leads/%d/edit" % lid, "/leads/999999",
    # jobs — board (all sorts), list (all sorts + filters + bad page), detail, forms
    "/jobs/", "/jobs/?bucket=approved", "/jobs/?stage=approved", "/jobs/?sort=est",
    "/jobs/?sort=name", "/jobs/?sort=clock",
    "/jobs/list", "/jobs/list?sort=value", "/jobs/list?sort=name", "/jobs/list?sort=rid",
    "/jobs/list?sort=recent", "/jobs/list?sort=days", "/jobs/list?overdue=1",
    "/jobs/list?stage=approved", "/jobs/list?bucket=approved", "/jobs/list?q=gc",
    "/jobs/list?page=0", "/jobs/list?page=abc", "/jobs/list?rep=Nobody",
    "/jobs/new", "/jobs/new?gc=%d" % gcid, "/jobs/%d" % jid, "/jobs/%d" % jid2,
    "/jobs/%d/edit" % jid, "/jobs/999999",
    # estimates — index/search/status, new (incl. bad int lead_id), detail/print/pdf,
    # orphan estimate detail/print/pdf (NULL section+line), missing id
    "/estimates/", "/estimates/?q=reroof", "/estimates/?status=draft",
    "/estimates/new", "/estimates/new?lead_id=%d" % lid, "/estimates/new?job_id=%d" % jid,
    "/estimates/new?lead_id=abc",
    "/estimates/%d" % eid, "/estimates/%d/print" % eid, "/estimates/%d/pdf" % eid,
    "/estimates/%d" % eid_orphan, "/estimates/%d/print" % eid_orphan,
    "/estimates/%d/pdf" % eid_orphan, "/estimates/999999",
]

bad = []
for p in paths:
    _record.cur = p
    try:
        r = c.get(p)
        sc = r.status_code
    except Exception:
        errors[p] = traceback.format_exc()
        sc = "EXC"
    if sc == "EXC" or (isinstance(sc, int) and sc >= 500):
        bad.append((p, sc))

if bad:
    for p, sc in bad:
        print("FAIL", sc, p)
        print(errors.get(p, "(no server-side traceback captured)"))
        print("-" * 60)
    sys.exit(1)

print("ROUTES_A_OK count=%d" % len(paths))
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
    env["CRM_PORT"] = "5099"
    env["CRM_DEMO"] = "0"               # do not seed demo data — keep the DB fresh
    env.pop("RENDER", None)
    env.pop("CRM_ENV", None)
    return env


def test_routes_a_fresh_db_no_5xx():
    tmpdb = os.path.join(tempfile.mkdtemp(prefix="routes_a_smoke_"), "crm.db")
    assert not os.path.exists(tmpdb), "temp DB should not pre-exist"
    code = _CHECK.replace("REPO_PLACEHOLDER", repr(REPO))
    r = subprocess.run([sys.executable, "-c", code], env=_fresh_env(tmpdb),
                       capture_output=True, text=True, cwd=REPO, timeout=240)
    assert r.returncode == 0, (
        "routes-a fresh-DB route guard failed:\nSTDOUT:\n%s\nSTDERR:\n%s"
        % (r.stdout, r.stderr))
    assert "ROUTES_A_OK" in r.stdout, "unexpected output:\n%s" % r.stdout
