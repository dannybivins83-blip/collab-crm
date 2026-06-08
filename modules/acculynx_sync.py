# -*- coding: utf-8 -*-
"""AccuLynx API auto-sync — pulls jobs/leads/contacts via the AccuLynx REST API
and upserts them into the CRM. Truly unattended (no browser) once an API key is
set. Docs: https://apidocs.acculynx.com/  (Bearer auth, /jobs + /contacts).

Field shapes vary, so extraction is defensive. Configure the key on the Sync page
(Tools → Sync from AccuLynx), then run on demand or on a schedule.
"""
import os
import json
import re
import time
import urllib.request
import urllib.parse

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify

import config
import db
import constants

bp = Blueprint("sync", __name__, url_prefix="/sync")

DEFAULT_BASE = "https://api.acculynx.com/api/v2"


def _ensure_schema():
    for col in ("acculynx_api_key TEXT", "acculynx_api_base TEXT",
                "acculynx_last_sync TEXT", "acculynx_auto INTEGER DEFAULT 0"):
        try:
            db.execute("ALTER TABLE company_settings ADD COLUMN %s" % col)
        except Exception:
            pass
    db._COLCACHE.clear()


_ensure_schema()


# ---- milestone name -> CRM stage resolution -------------------------------
_LEAD_BY_NAME = {s["name"].lower(): s["key"] for s in constants.LEAD_STAGES}
_JOB_BY_NAME = {s["name"].lower(): s["key"] for s in constants.JOB_STAGES}


def _resolve_stage(milestone):
    """Return (kind, stage_key). kind = 'lead' | 'job'."""
    m = (milestone or "").strip().lower()
    if m in _LEAD_BY_NAME:
        return "lead", _LEAD_BY_NAME[m]
    if m in _JOB_BY_NAME:
        return "job", _JOB_BY_NAME[m]
    # bucket-level fallbacks
    if "prospect" in m or "negotiat" in m or "long term" in m:
        return "lead", "prospect"
    if "assigned" in m or "lead" in m:
        return "lead", "assigned"
    if "invoiced" in m:
        return "job", "invoiced"
    if "completed" in m:
        return "job", "completed"
    if "closed" in m:
        return "job", "closed"
    if "cancel" in m:
        return "job", "canceled"
    return "job", "approved"


def _g(obj, *keys, default=""):
    for k in keys:
        if isinstance(obj, dict) and obj.get(k) not in (None, ""):
            return obj.get(k)
    return default


def _api_get(base, path, key, params=None):
    url = base.rstrip("/") + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + key, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _paginate(base, path, key, params=None, max_pages=40):
    """Collect items across pages (startIndex/pageSize)."""
    items = []
    params = dict(params or {})
    start = 0
    for _ in range(max_pages):
        params.update({"startIndex": start, "pageSize": 25})
        data = _api_get(base, path, key, params)
        page = data.get("items") if isinstance(data, dict) else data
        if not page:
            break
        items.extend(page)
        if len(page) < 25:
            break
        start += 25
    return items


def _customer_name(job):
    c = job.get("customer") or job.get("contact") or {}
    nm = (_g(c, "fullName", "name") or
          (str(_g(c, "firstName")) + " " + str(_g(c, "lastName"))).strip() or
          _g(job, "name", "jobName", "displayName"))
    return (nm or "").strip()


def _safe_name(s):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s or "file")[:120]


def sync_messages(base, key, ajid, kind, crm_id):
    """Pull a job's message/comment thread into the CRM record's notes + activity."""
    try:
        data = _api_get(base, "/jobs/%s/messages" % ajid, key)
    except Exception:
        return 0
    msgs = data.get("items") if isinstance(data, dict) else data
    if not msgs:
        return 0
    lines = []
    for m in msgs:
        when = _g(m, "date", "createdOn", "sentOn", "timestamp")
        who = _g(m, "fromName", "author", "sender", "createdBy")
        if isinstance(who, dict):
            who = _g(who, "name", "fullName")
        subj = _g(m, "subject", "title")
        text = _g(m, "body", "message", "text", "note")
        text = re.sub(r"<[^>]+>", " ", str(text))  # strip any HTML
        snippet = ("%s — %s%s: %s" % (when, (who + " " if who else ""),
                   ("[" + subj + "] ") if subj else "", text)).strip()
        lines.append("- " + re.sub(r"\s+", " ", snippet)[:300])
    note = "AccuLynx messages (%d):\n%s" % (len(msgs), "\n".join(lines[:40]))
    db.update(kind + "s", crm_id, narrative=note)
    db.add_activity(kind, crm_id, "note", "AccuLynx notes synced (%d messages)" % len(msgs))
    return len(msgs)


def sync_documents(base, key, ajid, crm_job_id):
    """Download a job's documents via the API (authenticated) and attach them."""
    try:
        data = _api_get(base, "/jobs/%s/documents" % ajid, key)
    except Exception:
        return 0
    docs = data.get("items") if isinstance(data, dict) else data
    if not docs:
        return 0
    saved = 0
    for d in docs:
        url = _g(d, "downloadUrl", "url", "href", "fileUrl")
        name = _g(d, "fileName", "name", "title") or ("acculynx_doc_%d" % saved)
        category = _g(d, "category", "folder", "type") or "AccuLynx"
        if not url:
            continue
        fn = "%d_%s" % (int(time.time() * 1000), _safe_name(name))
        path = os.path.join(config.DOC_DIR, fn)
        try:
            req = urllib.request.Request(url, headers={"Authorization": "Bearer " + key})
            with urllib.request.urlopen(req, timeout=60) as r, open(path, "wb") as f:
                f.write(r.read())
        except Exception:
            continue
        db.insert("documents", {"job_id": crm_job_id, "category": category,
                                "filename": fn, "original_name": name,
                                "size": os.path.getsize(path) if os.path.exists(path) else 0,
                                "notes": "Synced from AccuLynx"})
        saved += 1
    if saved:
        db.add_activity("job", crm_job_id, "note", "AccuLynx documents synced (%d files)" % saved)
    return saved


def run_sync(deep=False):
    company = db.get_company()
    key = (company.get("acculynx_api_key") or "").strip()
    base = (company.get("acculynx_api_base") or DEFAULT_BASE).strip()
    if not key:
        return {"ok": False, "error": "No API key set."}
    try:
        jobs = _paginate(base, "/jobs", key)
    except Exception as e:
        return {"ok": False, "error": "API request failed: %s" % e}

    exL = {(l.get("name") or "").lower(): l for l in db.all_rows("leads")}
    exJ = {(j.get("name") or "").lower(): j for j in db.all_rows("jobs")}
    added_l = added_j = updated = notes_synced = docs_synced = 0

    for job in jobs:
        jid = _g(job, "jobId", "id", "uid")
        name = _customer_name(job)
        if not name:
            continue
        milestone = _g(job, "currentMilestone", "milestone", "milestoneName",
                       "currentMilestoneName", "status")
        if isinstance(milestone, dict):
            milestone = _g(milestone, "name", "title")
        kind, stage = _resolve_stage(milestone)
        addr = _g(job, "address", "jobAddress", "siteAddress")
        if isinstance(addr, dict):
            addr = ", ".join(str(addr.get(k, "")) for k in ("street", "city", "state", "zip") if addr.get(k))
        contact = job.get("customer") or job.get("contact") or {}
        rec = {
            "name": name, "rid": _g(job, "jobNumber", "number", "refNumber"),
            "phone": _g(contact, "phone", "primaryPhone", "mobile"),
            "email": _g(contact, "email", "primaryEmail"),
            "address": addr, "work_type": _g(job, "workType", "tradeType", "trade"),
            "source": _g(job, "leadSource", "source"), "rep": _g(job, "salesRep", "assignedTo", "rep") or "Danny Bivins",
            "external_url": "https://my.acculynx.com/jobs/%s" % jid if jid else "",
            "department": company.get("default_county") and "REROOF Department" or "REROOF Department",
        }
        crm_kind = crm_id = None
        if kind == "lead":
            cur = exL.get(name.lower())
            if cur:
                db.update("leads", cur["id"], stage=stage, external_url=rec["external_url"])
                crm_id = cur["id"]
                updated += 1
            else:
                cid = _ensure_contact(name, rec)
                crm_id = db.insert("leads", {**rec, "contact_id": cid, "stage": stage,
                                             "stage_since": db.today(), "last_contact": db.today(),
                                             "narrative": "Synced from AccuLynx (%s)." % (milestone or stage)})
                db.add_activity("lead", crm_id, "automation", "Synced from AccuLynx — %s" % (milestone or stage))
                added_l += 1
            crm_kind = "lead"
        else:
            cur = exJ.get(name.lower())
            if cur:
                db.update("jobs", cur["id"], stage=stage, external_url=rec["external_url"])
                crm_id = cur["id"]
                updated += 1
            else:
                cid = _ensure_contact(name, rec)
                parts = [p.strip() for p in (rec["address"] or "").split(",")]
                jrow = {**rec, "contact_id": cid, "stage": stage, "stage_since": db.today(),
                        "address": parts[0] if parts else rec["address"],
                        "city": parts[1] if len(parts) > 1 else "", "county": "Palm Beach County",
                        "narrative": "Synced from AccuLynx (%s)." % (milestone or stage)}
                crm_id = db.insert("jobs", jrow)
                db.add_activity("job", crm_id, "automation", "Synced from AccuLynx — %s" % (milestone or stage))
                added_j += 1
            crm_kind = "job"

        # Deep sync: pull this record's notes + documents via the API.
        if deep and crm_id and jid:
            notes_synced += sync_messages(base, key, jid, crm_kind, crm_id)
            if crm_kind == "job":
                docs_synced += sync_documents(base, key, jid, crm_id)

    db.save_company({"acculynx_last_sync": db.now()})
    return {"ok": True, "jobs_seen": len(jobs), "added_leads": added_l,
            "added_jobs": added_j, "updated": updated,
            "notes_synced": notes_synced, "docs_synced": docs_synced}


def _ensure_contact(name, rec):
    parts = [p.strip() for p in (rec.get("address") or "").split(",")]
    for c in db.all_rows("contacts"):
        if (str(c.get("first_name", "")) + " " + str(c.get("last_name", ""))).strip().lower() == name.lower():
            return c["id"]
    return db.insert("contacts", {
        "kind": "person", "first_name": name.split(" ")[0],
        "last_name": " ".join(name.split(" ")[1:]), "email": rec.get("email", ""),
        "phone": rec.get("phone", ""), "address": parts[0] if parts else "",
        "city": parts[1] if len(parts) > 1 else "", "state": "FL",
        "source": rec.get("source", ""), "tags": "AccuLynx sync"})


# ---- routes ---------------------------------------------------------------

@bp.route("/")
def index():
    return render_template("sync.html", company=db.get_company(), default_base=DEFAULT_BASE)


@bp.route("/save", methods=["POST"])
def save():
    db.save_company({
        "acculynx_api_key": request.form.get("acculynx_api_key", "").strip(),
        "acculynx_api_base": request.form.get("acculynx_api_base", "").strip() or DEFAULT_BASE,
        "acculynx_auto": 1 if request.form.get("acculynx_auto") else 0,
    })
    flash("AccuLynx API settings saved.", "ok")
    return redirect(url_for("sync.index"))


_AUTO_STARTED = [False]


def start_auto_sync(app, interval_hours=4):
    """Background daemon that re-runs the API sync periodically when the
    'Enable scheduled auto-sync' box is on and an API key is set. Unattended."""
    if _AUTO_STARTED[0]:
        return
    _AUTO_STARTED[0] = True
    import threading
    import time as _time

    def _loop():
        while True:
            _time.sleep(max(1, interval_hours) * 3600)
            try:
                with app.app_context():
                    co = db.get_company()
                    if co.get("acculynx_auto") and (co.get("acculynx_api_key") or "").strip():
                        run_sync(deep=False)
            except Exception:
                pass
    threading.Thread(target=_loop, daemon=True, name="acculynx-auto-sync").start()


def _upsert_record(rec):
    """Upsert one scraped record (from the browser bookmarklet) by bucket. Returns 'added'|'updated'|None."""
    name = (rec.get("name") or "").strip()
    if not name:
        return None
    bucket = rec.get("bucket") or "prospect"
    url = "https://my.acculynx.com/jobs/%s" % rec.get("guid") if rec.get("guid") else ""
    parts = [p.strip() for p in (rec.get("address") or "").split(",")]
    company = db.get_company()
    dept = "REROOF Department"
    base = {"name": name, "rid": rec.get("rid", ""), "phone": rec.get("phone", ""),
            "email": rec.get("email", ""), "work_type": rec.get("work_type", ""),
            "source": rec.get("source", ""), "rep": rec.get("rep") or "Danny Bivins",
            "external_url": url, "department": dept}
    if bucket in ("lead", "assigned", "prospect", "negotiation", "long_term"):
        stage = "assigned" if bucket in ("lead", "assigned") else "prospect"
        cur = next((l for l in db.all_rows("leads") if (l.get("name") or "").lower() == name.lower()), None)
        if cur:
            db.update("leads", cur["id"], stage=stage, external_url=url, phone=base["phone"] or cur.get("phone"))
            return "updated"
        cid = _ensure_contact(name, {**base, "address": rec.get("address", "")})
        db.insert("leads", {**base, "address": rec.get("address", ""), "contact_id": cid, "stage": stage,
                            "stage_since": db.today(), "last_contact": db.today(),
                            "narrative": "Imported from AccuLynx (browser) — %s." % bucket})
        return "added"
    stage = {"approved": "approved", "completed": "completed", "invoiced": "invoiced",
             "closed": "closed"}.get(bucket, "approved")
    cur = next((j for j in db.all_rows("jobs") if (j.get("name") or "").lower() == name.lower()), None)
    if cur:
        db.update("jobs", cur["id"], stage=stage, external_url=url)
        return "updated"
    cid = _ensure_contact(name, {**base, "address": rec.get("address", "")})
    db.insert("jobs", {**base, "contact_id": cid, "stage": stage, "stage_since": db.today(),
                       "address": parts[0] if parts else "", "city": parts[1] if len(parts) > 1 else "",
                       "county": company.get("default_county", ""),
                       "narrative": "Imported from AccuLynx (browser) — %s." % bucket})
    return "added"


@bp.route("/browser-import", methods=["POST", "OPTIONS"])
def browser_import():
    """Receives scraped AccuLynx records from the browser bookmarklet. CORS-open
    (the request comes from the my.acculynx.com tab, not a CRM session)."""
    from flask import make_response
    if request.method == "OPTIONS":
        r = make_response("", 204)
    else:
        import json as _json
        recs = request.get_json(force=True, silent=True)
        if recs is None:
            try:
                recs = _json.loads(request.get_data(as_text=True) or "[]")
            except Exception:
                recs = []
        added = updated = 0
        for rec in (recs or []):
            res = _upsert_record(rec)
            if res == "added":
                added += 1
            elif res == "updated":
                updated += 1
        db.save_company({"acculynx_last_sync": db.now()})
        r = jsonify({"ok": True, "added": added, "updated": updated, "scanned": len(recs or [])})
    r.headers["Access-Control-Allow-Origin"] = "*"
    r.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return r


@bp.route("/run", methods=["POST"])
def run():
    deep = bool(request.form.get("deep"))
    result = run_sync(deep=deep)
    if result.get("ok"):
        msg = "Sync complete — %d jobs seen · +%d leads · +%d jobs · %d updated." % (
            result["jobs_seen"], result["added_leads"], result["added_jobs"], result["updated"])
        if deep:
            msg += " Notes synced: %d · Documents: %d." % (result["notes_synced"], result["docs_synced"])
        flash(msg, "ok")
    else:
        flash("Sync failed: %s" % result.get("error"), "error")
    # Return to where it was triggered (e.g. the dashboard Sync button).
    ref = request.referrer
    return redirect(ref if ref and "/sync" not in ref else url_for("sync.index"))
