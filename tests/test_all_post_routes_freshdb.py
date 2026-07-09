# -*- coding: utf-8 -*-
"""Whole-app fresh-DB POST/mutation smoke net: EVERY POST route must not return
HTTP 5xx on empty or pathological input.

Sibling to ``test_all_get_routes_freshdb.py`` (which covers the GET surface).
The GET net is 500-clean; this net closes the much larger, almost-uncovered
POST surface (~208 rules in ``app.url_map``). It is the durable regression net
for the "form/JSON handler crashes on input the browser UI never sends but a
hand-crafted / legacy / scripted / retried POST can" bug class — e.g.:

  * a NON-numeric ``job_id`` reaching ``int(job_id)`` -> ValueError 500
    (invoices.new / estimates.save; fixed 2026-07-09 wave 5).
  * a top-level JSON **list** body reaching ``(get_json() or {}).get(...)``:
    the ``or {}`` only guards ``None``, so a list slips through and ``.get`` on a
    list is an ``AttributeError`` 500 — BEFORE any auth/CSRF/validation runs.
  * a records list whose elements are scalars (or a dict iterated as its keys)
    reaching ``rec.get("name")`` -> ``AttributeError`` 500.

Rather than chase these one route at a time, this test enumerates **every POST
rule** from ``app.url_map`` at run time (new routes are covered for free), boots
the real app against a throwaway empty SQLite DB in an isolated subprocess with a
seeded admin session + the app's own CSRF token (``session['_csrf']`` echoed via
the ``X-CSRFToken`` header, exactly as ``modules/auth.py`` expects — so handlers
are genuinely reached, not bounced at the CSRF gate), synthesizes path params
from freshly-seeded rows, and POSTs several bodies to each:

    empty | junk form | junk JSON dict | top-level JSON list | malformed JSON

asserting **none respond 5xx**. HMAC/signature/api-key-gated webhooks
(``/stripe/webhook``, ``/measurements/ingest``, ``/api/takeoff``, ...) reject a
missing/bad signature with 4xx, which passes the "no 5xx" bar naturally.

A full route->status manifest is printed so any failure is self-diagnosing.

QUARANTINE: a handful of endpoints in OTHER lanes' modules currently 5xx on a
malformed JSON body (see ``KNOWN_5XX`` below). They are documented + reported to
their owning agents (wave-6 ``still_open``); this net asserts **no NEW 5xx appear
outside that quarantine**, and reports any quarantined route that now passes so
the list can be trimmed once the owner fixes it. Per the wave-6 lane split, this
module writes NO app code — it only observes.

Runs in a subprocess (mirrors the sibling smoke tests) so it neither depends on
nor mutates the dev database.
"""
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHECK = r'''
import os, sys, re, json, traceback, socket
sys.path.insert(0, REPO_PLACEHOLDER)
os.chdir(REPO_PLACEHOLDER)
socket.setdefaulttimeout(8)   # bound any accidental outbound network call

import app as appmod
import db
from flask import got_request_exception

a = appmod.app
c = a.test_client()

# --- known-5xx quarantine (endpoints in OTHER lanes' modules) ----------------
# Each 5xxs on a malformed JSON body today; reported to the owning agent via the
# wave-6 still_open list. This net does NOT fix them (tests-only lane) — it hard-
# fails only on NEW 5xx OUTSIDE this set, and flags any that now pass.
KNOWN_5XX = {
    "leads.import_leads":         "leads.py: for rec in records -> rec.get() on non-dict record / dict-as-keys",
    "permit_api.submit_build":    "permit_api.py:_get_key_from_request (get_json() or {}).get() crashes on top-level JSON list (pre-auth)",
    "sync.browser_import":        "acculynx_sync.py:_upsert_record rec.get() on non-dict record",
    "sync.closed_import":         "acculynx_sync.py:closed_import (get_json() or {}).get() on top-level JSON list",
    "sync.estimate_collect":      "acculynx_sync.py:estimate_collect (get_json() or {}).get() on top-level JSON list",
    "sync.insurance_import":      "acculynx_sync.py:insurance_import (get_json() or {}).get() on top-level JSON list",
    "sync.orders_import":         "acculynx_sync.py:orders_import (get_json() or {}).get() on top-level JSON list",
    "sync.pipeline_batch":        "acculynx_sync.py:pipeline_batch (get_json() or {}).get() on top-level JSON list",
    "sync.roofreport_collect":    "acculynx_sync.py:roofreport_collect (get_json() or {}).get() on top-level JSON list",
}

# --- seed one row per table a mutation route reads/updates -------------------
# db.insert returns the new id (or None if a table's schema differs); path-param
# synthesis then points at rows that actually exist. Unmatched ids MUST still
# degrade to 404/redirect (never 5xx) -- that non-degradation is the bug we catch.
def _ins(table, **kw):
    try:
        return db.insert(table, kw)
    except Exception:
        return None

uid        = _ins("users", name="smoke", email="smoke@test.local", role="admin", active=1)
contact_id = _ins("contacts", first_name="Pat", last_name="Example", kind="person")
lead_id    = _ins("leads", name="Lead One", department="REROOF Department", address="1 Main St")
job_id     = _ins("jobs", name="R-1 Smoke Job", address="1 Main St", city="Boca Raton",
                  ahj="Boca Raton", system="shingle", department="REROOF Department",
                  stage="approved", contract_value="20000")
est_id     = _ins("estimates", number="E-1", title="Smoke Est", job_id=job_id,
                  lead_id=lead_id, contact_id=contact_id, status="draft")
inv_id     = _ins("invoices", job_id=job_id, number="INV-1", amount=100.0)
materials_id = _ins("materials", job_id=job_id, supplier="ABC", status="draft")
order_id   = _ins("orders", job_id=job_id, vendor="ABC", type="Material", status="draft")
permit_id  = _ins("permits", job_id=job_id, ahj="Boca Raton", system="shingle", status="prep")
rr_id      = _ins("roof_reports", job_id=job_id, address="1 Main St", status="queued")
claim_id   = _ins("claims", job_id=job_id, carrier="State Farm", status="filed")
sub_id     = _ins("subcontractors", name="Sub One", trade="roofing", status="active")
note_id    = _ins("notifications", created=db.now(), job_id=job_id, rep="", kind="sign",
                  icon="x", text="hi", read=0)
tmpl_id    = _ins("templates", tkey="smoke", name="Smoke Template", work_type="reroof")
doc_id     = _ins("documents", job_id=job_id, name="Doc One", filename="d.pdf", category="docs")
task_id    = _ins("tasks", title="Task One", job_id=job_id, status="open")
appt_id    = _ins("appointments", job_id=job_id, title="Appt", start=db.now())
comm_id    = _ins("commissions", job_id=job_id, rep="", amount=100.0, status="pending")
auto_id    = _ins("automations", name="A", trigger="stage", active=1)
insp_id    = _ins("inspections", job_id=job_id, kind="final", status="scheduled")

# --- capture server-side tracebacks for any 5xx (self-diagnosing failures) ----
errors = {}
_state = {"cur": "?"}
def _record(sender, exception, **extra):
    errors.setdefault(_state["cur"], "".join(
        traceback.format_exception(type(exception), exception, exception.__traceback__)))
got_request_exception.connect(_record, a)

T = "tok-postsmoke"
with c.session_transaction() as s:
    s["user_id"]   = uid or 1
    s["user_name"] = "smoke"
    s["user_role"] = "admin"          # clears the ADMIN_ONLY_PATHS gate
    s["department"] = "REROOF Department"
    s["_csrf"]     = T                 # the app's own CSRF secret for this session
HDR      = {"X-CSRFToken": T}          # echoed token -> passes the CSRF guard
HDR_JSON = {"X-CSRFToken": T, "Content-Type": "application/json"}

# Path-param synthesis. Keyed first by (endpoint, arg) where the same arg name
# means different tables, then by arg name -> seeded id, then by converter, then
# a tokenish default.
ARG_IDS = {
    "job_id": job_id, "lead_id": lead_id, "contact_id": contact_id,
    "est_id": est_id, "inv_id": inv_id, "permit_id": permit_id,
    "report_id": rr_id, "cid": claim_id, "sid": sub_id,
    "note_id": note_id, "tid": tmpl_id, "packet_id": 1,
    "doc_id": doc_id, "task_id": task_id, "appt_id": appt_id,
    "auto_id": auto_id, "seq_id": 1, "vendor_id": 1, "key_id": 1,
    "account_id": 1, "req_id": 1, "fid": 1, "insp_id": insp_id,
    "user_id": uid, "invoice_id": inv_id, "entity_id": job_id,
    "order_id": order_id,
}
ENDPOINT_ARGS = {
    ("materials.delete", "order_id"): materials_id,
    ("materials.save", "order_id"): materials_id,
    ("orders.delete", "order_id"): order_id,
    ("orders.save", "order_id"): order_id,
    ("orders.status", "order_id"): order_id,
    ("commissions.save", "cid"): comm_id,
    ("commissions.status", "cid"): comm_id,
    ("claims.save", "cid"): claim_id,
    ("claims.status", "cid"): claim_id,
    ("claims.add_supplement", "cid"): claim_id,
    ("customfields.source_delete", "sid"): 1,
    ("claims.supplement_status", "sid"): 1,
    ("subs.save", "sid"): sub_id,
}
TOKENISH = {"token", "slug", "code", "mid", "app_id", "old"}

def value_for(endpoint, name, conv):
    if (endpoint, name) in ENDPOINT_ARGS:
        v = ENDPOINT_ARGS[(endpoint, name)]
        return v if v is not None else 1
    if name in ARG_IDS and ARG_IDS[name] is not None:
        return ARG_IDS[name]
    if conv == "path":
        return "none.txt"
    if conv == "int":
        return 1
    if name in TOKENISH:
        return "smoketok"
    if name == "entity_type":
        return "lead"
    return "smoke"

_ph = re.compile(r"<(?:([^:>]+):)?([^>]+)>")
def build_url(rule_str, endpoint):
    return _ph.sub(lambda m: str(value_for(endpoint, m.group(2), m.group(1))), rule_str)

# Pathological bodies covering the common crash sites: non-numeric where int()/
# float() is expected, wrong-typed list fields, a top-level JSON list (not a
# dict), and a syntactically-malformed JSON string.
JUNK_FORM = {
    "_csrf": T, "job_id": "abc", "lead_id": "あ", "contact_id": "1.5",
    "amount": "xyz", "qty": "NaN", "cost": "--", "price": "z", "waste_pct": "w",
    "tax_pct": "abc", "margin_pct": "xx", "contract_value": "N/A", "id": "あ",
    "entity_id": "abc", "seq_id": "x", "count": "-", "page": "abc", "email": "",
    "name": "", "status": "", "stage": "", "value": "", "days": "abc",
    "pct": "??", "rank": "z", "amount_cents": "abc", "invoice_id": "abc",
}
JUNK_JSON = {
    "job_id": "abc", "lead_id": "あ", "amount": "xyz", "qty": "NaN",
    "cost": "z", "price": "p", "waste_pct": "w", "tax_pct": "abc",
    "margin_pct": "xx", "sections": "nope", "lines": "nope", "id": "あ",
    "items": "x", "records": 5, "rows": "y", "jobs": "z",
}

# (label, kind); kind drives how the body is encoded.
VARIANTS = [
    ("empty",     "empty"),      # zero-length body, CSRF header only
    ("junk_form", "form"),       # junk form fields (non-numeric where numeric expected)
    ("junk_json", "json"),       # junk JSON dict (wrong-typed values)
    ("json_list", "jsonlist"),   # top-level JSON *list* (not a dict)
    ("bad_json",  "badjson"),    # syntactically malformed JSON string
]

# Endpoints that could replace the throwaway DB — exercised, but LAST so a cascade
# cannot mask earlier routes.
LAST = {"dbadmin.db_restore", "tools.restore"}

rules = []
for r in a.url_map.iter_rules():
    if "POST" not in (r.methods or ()):
        continue
    rules.append(r)
rules.sort(key=lambda r: (r.endpoint in LAST, r.endpoint))

# per-endpoint: {variant_label: status}
manifest = {}
rule_of  = {}
for r in rules:
    ep = r.endpoint
    rule_of[ep] = str(r)
    url = build_url(str(r), ep)
    manifest.setdefault(ep, {})
    for vlabel, kind in VARIANTS:
        _state["cur"] = "%s|%s" % (ep, vlabel)
        try:
            if kind == "empty":
                resp = c.post(url, data=b"", headers=HDR)
            elif kind == "form":
                resp = c.post(url, data=JUNK_FORM, headers=HDR)
            elif kind == "json":
                resp = c.post(url, data=json.dumps(JUNK_JSON), headers=HDR_JSON)
            elif kind == "jsonlist":
                resp = c.post(url, data=json.dumps([1, 2, "あ"]), headers=HDR_JSON)
            elif kind == "badjson":
                resp = c.post(url, data="not json{[", headers=HDR_JSON)
            sc = resp.status_code
        except Exception:
            errors["%s|%s" % (ep, vlabel)] = traceback.format_exc()
            sc = "EXC"
        manifest[ep][vlabel] = sc

def _is_5xx(sc):
    return (isinstance(sc, int) and sc >= 500) or sc == "EXC"

# --- full route->status manifest (self-diagnosing) --------------------------
for ep in sorted(manifest):
    statuses = manifest[ep]
    worst = max((s for s in statuses.values() if isinstance(s, int)), default=0)
    tag = "5XX" if any(_is_5xx(s) for s in statuses.values()) else "ok "
    print("ROUTE %s %-42s %-52s %s" % (
        tag, ep, rule_of[ep],
        " ".join("%s=%s" % (k, statuses[k]) for k, _ in VARIANTS)))

bad_eps = {ep for ep, st in manifest.items() if any(_is_5xx(s) for s in st.values())}
unexpected = bad_eps - set(KNOWN_5XX)
resolved   = set(KNOWN_5XX) - bad_eps

print("=" * 72)
print("POST_ROUTES n=%d  bad_eps=%d  known_quarantine=%d  unexpected=%d  resolved=%d"
      % (len(rules), len(bad_eps), len(KNOWN_5XX), len(unexpected), len(resolved)))

# Guard against a silent collapse (blueprints failing to register) trivially
# "passing" with an empty net.
if len(rules) < 100:
    print("FAIL too few POST routes enumerated: %d (expected >=100)" % len(rules))
    sys.exit(1)

for ep in sorted(bad_eps):
    variants = [k for k, _ in VARIANTS if _is_5xx(manifest[ep].get(k))]
    mark = "QUARANTINED" if ep in KNOWN_5XX else "NEW-5XX"
    print("  %-11s %-40s variants=%s" % (mark, ep, variants))
    if ep in unexpected:
        key = "%s|%s" % (ep, variants[0])
        tb = errors.get(key, "")
        if tb:
            print("      TB: " + " || ".join(tb.strip().splitlines()[-3:]))

if resolved:
    print("NOTE: quarantined endpoints now returning <500 (trim KNOWN_5XX once the "
          "owning lane confirms the fix): %s" % sorted(resolved))

if unexpected:
    print("FAIL %d POST route(s) 5xx outside the documented quarantine: %s"
          % (len(unexpected), sorted(unexpected)))
    sys.exit(1)

print("ALL_OK %d POST routes, 0 NEW 5xx (quarantine=%d)" % (len(rules), len(KNOWN_5XX)))
sys.exit(0)
'''


def test_all_post_routes_no_5xx_on_fresh_db():
    tmpdb = os.path.join(tempfile.mkdtemp(prefix="allpost_smoke_"), "crm.db")
    assert not os.path.exists(tmpdb), "temp DB should not pre-exist"
    env = dict(os.environ)
    env["DATABASE_URL"] = ""            # force SQLite even if a PG URL is ambient
    env["POSTGRES_URL"] = ""
    env["POSTGRES_PRISMA_URL"] = ""
    env["CRM_DB_PATH"] = tmpdb          # throwaway, guaranteed-empty DB file
    env["CRM_DATA_DIR"] = os.path.dirname(tmpdb)
    env["CRM_NOBROWSER"] = "1"
    env["CRM_DEMO"] = "0"
    env["CRM_PORT"] = "5096"
    env.pop("RENDER", None)
    env.pop("CRM_ENV", None)

    code = _CHECK.replace("REPO_PLACEHOLDER", repr(REPO))
    r = subprocess.run([sys.executable, "-c", code], env=env,
                       capture_output=True, text=True, cwd=REPO, timeout=420)
    assert r.returncode == 0, (
        "all-POST-route fresh-DB smoke failed (a NEW route returned 5xx on bad "
        "input):\nSTDOUT:\n%s\nSTDERR:\n%s" % (r.stdout, r.stderr))
    assert "ALL_OK" in r.stdout, "unexpected output:\n%s" % r.stdout
