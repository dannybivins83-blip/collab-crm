# -*- coding: utf-8 -*-
"""Whole-app fresh-DB smoke net: EVERY GET route must not return HTTP 5xx.

This is the durable regression net for the "live-500" bug class that has
repeatedly bitten this CRM on brand-new white-label deploys, where prod's own
aged database masked the defect:

  * templates/contacts.html shipped with a curly-quote ``{% extends %}`` -> the
    Contacts list 500'd on every load (fixed 2026-07-09).
  * roof_reports.index filtered on a ``lead_id`` column that the BASE schema did
    not declare (only lazily added at request time) -> ``/roof-reports/`` 500'd on
    any fresh tenant DB (fixed 2026-07-09).
  * a view referencing a name imported only inside another function's local scope
    -> 500 only when that specific route is actually hit (invisible to
    import/compile checks).

Rather than chase these one route at a time, this test enumerates **every GET
rule** from ``app.url_map`` at run time (so newly-added routes are covered for
free), boots the real app against a throwaway, empty SQLite DB in an isolated
subprocess with a seeded admin session, requests each route once, and asserts
**none respond 5xx**. It synthesizes path params from freshly-seeded rows
(``/jobs/<id>`` -> a real seeded job) so detail templates actually render; routes
whose params can't map to a real row still must degrade to 404/redirect, never
500. A full route->status manifest is printed so any failure is self-diagnosing.

Runs in a subprocess (mirrors the sibling smoke tests) so it neither depends on
nor mutates the dev database, and can't be pinned to another test module's
import-time DB path.
"""
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Self-contained checker: boots the real app on a throwaway DB, seeds one row per
# table the detail routes read from, then GETs every GET rule in the url_map and
# fails on any 5xx (or an exception escaping the request).
_CHECK = r'''
import os, sys, re
sys.path.insert(0, REPO_PLACEHOLDER)
os.chdir(REPO_PLACEHOLDER)
import app as appmod
import db

a = appmod.app
a.config["WTF_CSRF_ENABLED"] = False
c = a.test_client()

# --- seed one row per table a detail/print/pdf route reads from -------------
# db.insert returns the new id; we capture each so path-param synthesis points at
# rows that actually exist (independent of whatever demo data init_db seeds).
uid = db.insert("users", {"name": "smoke", "email": "smoke@test.local", "role": "admin"})
contact_id = db.insert("contacts", {"first_name": "Pat", "last_name": "Example", "kind": "person"})
lead_id = db.insert("leads", {"name": "Lead One", "department": "REROOF Department", "address": "1 Main St"})
job_id = db.insert("jobs", {"name": "Smoke Job", "address": "1 Main St", "city": "Boca Raton",
                            "ahj": "Boca Raton", "system": "shingle", "department": "REROOF Department"})
est_id = db.insert("estimates", {"number": "E-1", "title": "Smoke Est", "job_id": job_id,
                                 "lead_id": lead_id, "contact_id": contact_id, "status": "draft"})
inv_id = db.insert("invoices", {"job_id": job_id, "number": "INV-1", "amount": 100.0})
materials_id = db.insert("materials", {"job_id": job_id, "supplier": "ABC", "status": "draft"})
order_id = db.insert("orders", {"job_id": job_id, "vendor": "ABC", "type": "Material", "status": "draft"})
permit_id = db.insert("permits", {"job_id": job_id, "ahj": "Boca Raton", "system": "shingle", "status": "prep"})
rr_id = db.insert("roof_reports", {"job_id": job_id, "address": "1 Main St", "status": "queued"})
claim_id = db.insert("claims", {"job_id": job_id, "carrier": "State Farm", "status": "filed"})
sub_id = db.insert("subcontractors", {"name": "Sub One", "trade": "roofing", "status": "active"})
note_id = db.insert("notifications", {"created": db.now(), "job_id": job_id, "rep": "",
                                      "kind": "sign", "icon": "x", "text": "hi", "read": 0})
tmpl_id = db.insert("templates", {"tkey": "smoke", "name": "Smoke Template", "work_type": "reroof"})

with c.session_transaction() as s:
    s["user_id"] = uid
    s["user_name"] = "smoke"
    s["user_role"] = "admin"

# Path-param synthesis. Keyed first by (endpoint, arg) for the cases where the same
# arg name means different tables (materials.detail vs orders.detail both use
# "order_id"), then by arg name -> seeded id, then by converter, then a tokenish
# default. A route whose synthesized param maps to no row MUST still degrade to
# 404/redirect (never 5xx) -- that non-degradation is exactly a bug worth catching.
ARG_IDS = {
    "job_id": job_id, "lead_id": lead_id, "contact_id": contact_id,
    "est_id": est_id, "inv_id": inv_id, "permit_id": permit_id,
    "report_id": rr_id, "cid": claim_id, "sid": sub_id,
    "note_id": note_id, "tid": tmpl_id, "packet_id": 1,
}
ENDPOINT_ARGS = {
    ("materials.detail", "order_id"): materials_id,
    ("orders.detail", "order_id"): order_id,
    ("orders.print_view", "order_id"): order_id,
    ("permit_api.build_download", "job_id"): str(job_id),
    ("permit_api.build_status", "job_id"): str(job_id),
    ("portal.portal_file", "subpath"): "none.pdf",
    ("uploads", "subpath"): "branding/none.png",   # non-sensitive prefix -> 404, not 403
    ("static", "filename"): "none-smoke.js",
}
TOKENISH = {"token", "slug", "code", "mid", "app_id"}

def value_for(endpoint, name, conv):
    if (endpoint, name) in ENDPOINT_ARGS:
        return ENDPOINT_ARGS[(endpoint, name)]
    if name in ARG_IDS:
        return ARG_IDS[name]
    if conv == "path":
        return "none.txt"
    if conv == "int":
        return 1
    if name in TOKENISH:
        return "smoketok"
    return "smoke"

_ph = re.compile(r"<(?:([^:>]+):)?([^>]+)>")
def build_url(rule_str, endpoint):
    return _ph.sub(lambda m: str(value_for(endpoint, m.group(2), m.group(1))), rule_str)

manifest = []
for r in a.url_map.iter_rules():
    if "GET" not in (r.methods or ()):
        continue
    url = build_url(str(r), r.endpoint)
    try:
        code = c.get(url).status_code
    except Exception as e:
        code = "EXC:" + type(e).__name__
    manifest.append((r.endpoint, str(r), url, code))

manifest.sort(key=lambda m: m[0])
for ep, rule, url, code in manifest:
    print("ROUTE %s %s -> %s" % (code, ep, url))

bad = [m for m in manifest
       if (isinstance(m[3], int) and m[3] >= 500) or (isinstance(m[3], str) and m[3].startswith("EXC"))]

# Guard against a silent collapse (blueprints failing to register) trivially
# "passing" with an empty net.
if len(manifest) < 100:
    print("FAIL too few GET routes enumerated: %d (expected >=100)" % len(manifest))
    sys.exit(1)

if bad:
    for ep, rule, url, code in bad:
        print("FAIL 5xx %s %s -> %s" % (code, ep, url))
    sys.exit(1)

print("ALL_OK %d GET routes, 0 5xx" % len(manifest))
sys.exit(0)
'''


def test_all_get_routes_no_5xx_on_fresh_db():
    tmpdb = os.path.join(tempfile.mkdtemp(prefix="allroute_smoke_"), "crm.db")
    assert not os.path.exists(tmpdb), "temp DB should not pre-exist"
    env = dict(os.environ)
    env["DATABASE_URL"] = ""            # force SQLite even if a PG URL is ambient
    env["POSTGRES_URL"] = ""
    env["POSTGRES_PRISMA_URL"] = ""
    env["CRM_DB_PATH"] = tmpdb          # throwaway, guaranteed-empty DB file
    env["CRM_DATA_DIR"] = os.path.dirname(tmpdb)
    env["CRM_NOBROWSER"] = "1"
    env["CRM_PORT"] = "5091"
    env.pop("RENDER", None)
    env.pop("CRM_ENV", None)

    code = _CHECK.replace("REPO_PLACEHOLDER", repr(REPO))
    r = subprocess.run([sys.executable, "-c", code], env=env,
                       capture_output=True, text=True, cwd=REPO, timeout=300)
    assert r.returncode == 0, (
        "all-GET-route fresh-DB smoke failed (a route returned 5xx):\n"
        "STDOUT:\n%s\nSTDERR:\n%s" % (r.stdout, r.stderr))
    assert "ALL_OK" in r.stdout, "unexpected output:\n%s" % r.stdout
