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

# SSL context with a real CA bundle (certifi) — Windows Python's urllib otherwise
# can't verify the AccuLynx cert ("CERTIFICATE_VERIFY_FAILED"). Falls back to the
# system default if certifi isn't present.
import ssl as _ssl
try:
    import certifi as _certifi
    _SSL_CTX = _ssl.create_default_context(cafile=_certifi.where())
except Exception:
    _SSL_CTX = _ssl.create_default_context()


def _ensure_schema():
    for col in ("acculynx_api_key TEXT", "acculynx_api_base TEXT",
                "acculynx_last_sync TEXT", "acculynx_auto INTEGER DEFAULT 0",
                "acculynx_cursor INTEGER DEFAULT 0", "acculynx_group INTEGER DEFAULT 0"):
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
    with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as r:
        return json.loads(r.read().decode("utf-8"))


def _paginate(base, path, key, params=None, max_pages=40):
    """Collect items across pages (startIndex/pageSize)."""
    items = []
    params = dict(params or {})
    start = 0
    for _ in range(max_pages):
        params.update({"pageStartIndex": start, "pageSize": 25})
        data = _api_get(base, path, key, params)
        page = data.get("items") if isinstance(data, dict) else data
        if not page:
            break
        items.extend(page)
        if len(page) < 25:
            break
        start += 25
    return items


def _join_list(v):
    """Flatten an array field (e.g. tradeTypes) to a comma string."""
    if isinstance(v, list):
        out = []
        for it in v:
            out.append(_g(it, "name", "type", "value", "title") if isinstance(it, dict) else str(it or ""))
        return ", ".join([x for x in out if x])
    return str(v or "")


def _flatten_address(a):
    """AccuLynx locationAddress is an object; build a single display string."""
    if not isinstance(a, dict):
        return str(a or "")
    line1 = _g(a, "addressFirstLine", "address1", "street", "streetAddress", "line1", "addressLine1")
    line2 = _g(a, "addressSecondLine", "address2", "line2", "addressLine2")
    city = _g(a, "city", "cityText")
    state = _g(a, "state", "stateText", "stateCode", "province")
    zc = _g(a, "zip", "zipCode", "postalCode", "zipText", "postal")
    sz = ("%s %s" % (state, zc)).strip()
    return ", ".join([p for p in (line1, line2, city, sz) if p])


def _first_val(obj, list_keys, item_keys):
    """Pull the first usable value from array-of-objects fields like
    phoneNumbers:[{number,...}] / emailAddresses:[{address,...}]."""
    if not isinstance(obj, dict):
        return ""
    for lk in list_keys:
        arr = obj.get(lk)
        if isinstance(arr, list):
            for it in arr:
                if isinstance(it, dict):
                    v = _g(it, *item_keys)
                    if v:
                        return v
                elif it:
                    return str(it)
    return ""


def _pick_contact(job):
    """Choose the job's primary/customer contact wrapper from the contacts[] array."""
    cs = job.get("contacts")
    if not isinstance(cs, list) or not cs:
        return {}
    for c in cs:
        if not isinstance(c, dict):
            continue
        role = str(_g(c, "contactType", "type", "role", "contactRole")).lower()
        if c.get("isPrimary") or c.get("isPrimaryContact") or role in ("customer", "primary", "homeowner", "billing"):
            return c
    return cs[0] if isinstance(cs[0], dict) else {}


def _contact_basics(job, base, key, fetch=True):
    """name/phone/email for the job's primary contact. The /jobs response embeds
    only a contact ref (id + _link), so fetch /contacts/{id} for phone/email when
    asked. Never raises — missing pieces just come back blank."""
    out = {"name": "", "phone": "", "email": ""}
    wrap = _pick_contact(job)
    inner = wrap.get("contact") if isinstance(wrap.get("contact"), dict) else wrap
    if not isinstance(inner, dict):
        return out
    first = _g(inner, "firstName", "givenName")
    last = _g(inner, "lastName", "familyName", "surname")
    company = _g(inner, "companyName", "company", "businessName")
    out["phone"] = (_first_val(inner, ("phoneNumbers", "phones"), ("number", "phoneNumber", "value"))
                    or _g(inner, "phone", "primaryPhone", "mobile", "cellPhone"))
    out["email"] = (_first_val(inner, ("emailAddresses", "emails"), ("address", "emailAddress", "value"))
                    or _g(inner, "email", "primaryEmail"))
    cid = _g(inner, "id")
    if fetch and cid and (not out["phone"] or not out["email"] or not (first or last or company)):
        try:
            d = _api_get(base, "/contacts/%s" % cid, key)
            first = first or _g(d, "firstName", "givenName")
            last = last or _g(d, "lastName", "familyName", "surname")
            company = company or _g(d, "companyName", "company", "businessName")
            out["phone"] = out["phone"] or _first_val(d, ("phoneNumbers", "phones"), ("number", "phoneNumber", "value")) or _g(d, "phone", "primaryPhone", "mobile")
            out["email"] = out["email"] or _first_val(d, ("emailAddresses", "emails"), ("address", "emailAddress", "value")) or _g(d, "email", "primaryEmail")
        except Exception:
            pass
    out["name"] = (("%s %s" % (first, last)).strip() or company or "").strip()
    return out


def _safe_name(s):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s or "file")[:120]


def sync_messages(base, key, ajid, kind, crm_id):
    """Pull a job's message/comment thread into the CRM record's notes + activity.
    Paginates so jobs with long threads aren't truncated to the first page."""
    try:
        msgs = _paginate(base, "/jobs/%s/messages" % ajid, key)
    except Exception:
        return 0
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
    """Download a job's documents via the API (authenticated) and attach them.
    Paginates, and skips files already pulled (dedup by name) so re-syncs are cheap
    and don't pile up duplicate copies."""
    try:
        docs = _paginate(base, "/jobs/%s/documents" % ajid, key)
    except Exception:
        return 0
    if not docs:
        return 0
    have = {(r.get("original_name") or "").lower()
            for r in db.all_rows("documents", where="job_id=?", params=(crm_job_id,))}
    saved = 0
    for d in docs:
        url = _g(d, "downloadUrl", "url", "href", "fileUrl")
        name = _g(d, "fileName", "name", "title") or ("acculynx_doc_%d" % saved)
        category = _g(d, "category", "folder", "type") or "AccuLynx"
        if not url or name.lower() in have:  # skip missing-url or already-synced files
            continue
        fn = "%d_%s" % (int(time.time() * 1000), _safe_name(name))
        path = os.path.join(config.DOC_DIR, fn)
        try:
            req = urllib.request.Request(url, headers={"Authorization": "Bearer " + key})
            with urllib.request.urlopen(req, timeout=60, context=_SSL_CTX) as r, open(path, "wb") as f:
                f.write(r.read())
        except Exception:
            continue
        db.insert("documents", {"job_id": crm_job_id, "category": category,
                                "filename": fn, "original_name": name,
                                "size": os.path.getsize(path) if os.path.exists(path) else 0,
                                "notes": "Synced from AccuLynx"})
        have.add(name.lower())  # guard against in-call repeats if the API ignores paging
        saved += 1
    if saved:
        db.add_activity("job", crm_job_id, "note", "AccuLynx documents synced (%d files)" % saved)
    return saved


def _money_val(job):
    """Pull a dollar value from an AccuLynx job and format as '$12,345'."""
    v = _g(job, "jobValue", "value", "contractValue", "estimateValue", "totalValue",
           "totalContractValue", "jobTotal", "totalJobValue", "estimateTotal", "amount", default="")
    if isinstance(v, dict):
        v = _g(v, "amount", "value", "total", default="")
    if v in (None, ""):
        return ""
    try:
        n = float(re.sub(r"[^0-9.]", "", str(v)))
        return ("$" + format(int(round(n)), ",")) if n else ""
    except Exception:
        return str(v)


def run_sync(deep=False, batch=50):
    company = db.get_company()
    key = (company.get("acculynx_api_key") or "").strip()
    base = (company.get("acculynx_api_base") or DEFAULT_BASE).strip()
    if not key:
        return {"ok": False, "error": "No API key set."}
    # Walk AccuLynx milestone groups in priority order (leads/prospects first, the
    # huge historical closed/invoiced buckets last) using the DOCUMENTED params:
    #   milestones=<group>  recordStartIndex=<n>  pageSize<=25  sortBy=MilestoneDate
    # Cursor = (group index, recordStartIndex within that group), resumable.
    BATCH = int(batch or 50)
    PAGE = 25  # AccuLynx caps pageSize at 25
    GROUPS = ["lead", "prospect", "approved", "completed", "invoiced", "closed", "cancelled", "dead"]
    g = int(company.get("acculynx_group") or 0)
    start = int(company.get("acculynx_cursor") or 0)
    window = []
    try:
        while len(window) < BATCH and g < len(GROUPS):
            data = _api_get(base, "/jobs", key, {
                "milestones": GROUPS[g], "pageStartIndex": start, "pageSize": PAGE,
                "sortBy": "MilestoneDate", "sortOrder": "Descending"})
            items = data.get("items") if isinstance(data, dict) else (data or [])
            if items:
                for it in items:           # tag with the milestone group we filtered on
                    if isinstance(it, dict):
                        it["__group"] = GROUPS[g]
                window.extend(items)
                start += len(items)
                if len(items) < PAGE:   # this milestone group is fully consumed
                    g += 1
                    start = 0
            else:                       # empty group — move on
                g += 1
                start = 0
    except Exception as e:
        return {"ok": False, "error": "API request failed: %s" % e}

    done = g >= len(GROUPS)
    cur_group = GROUPS[g] if not done else "all"

    exL = {(l.get("name") or "").lower(): l for l in db.all_rows("leads")}
    exJ = {(j.get("name") or "").lower(): j for j in db.all_rows("jobs")}
    added_l = added_j = updated = notes_synced = docs_synced = skipped = 0
    last_err = ""

    for job in window:
        try:
            jid = _g(job, "id", "jobId", "uid")
            name = (_g(job, "jobName", "name", "displayName") or "").strip()
            milestone = _g(job, "currentMilestone", "milestone", "milestoneName",
                           "currentMilestoneName", "status")
            if isinstance(milestone, dict):
                milestone = _g(milestone, "name", "title", "milestoneName")
            # Trust the milestone GROUP we filtered the API on (reliable), not the
            # free-text currentMilestone (shape varies and often won't parse).
            _GROUP_MAP = {"lead": ("lead", "assigned"), "prospect": ("lead", "prospect"),
                          "approved": ("job", "approved"), "completed": ("job", "completed"),
                          "invoiced": ("job", "invoiced"), "closed": ("job", "closed"),
                          "cancelled": ("job", "canceled"), "dead": ("lead", "lost")}
            grp = job.get("__group")
            if grp in _GROUP_MAP:
                kind, stage = _GROUP_MAP[grp]
            else:
                kind, stage = _resolve_stage(milestone)
            url = "https://my.acculynx.com/jobs/%s" % jid if jid else ""
            val = _money_val(job)
            val_col = "estimate" if kind == "lead" else "contract_value"

            cur = (exL if kind == "lead" else exJ).get(name.lower()) if name else None
            crm_kind = "lead" if kind == "lead" else "job"
            crm_id = None

            if cur:
                upd = {"stage": stage, "external_url": url}
                if val:
                    upd[val_col] = val
                db.update(crm_kind + "s", cur["id"], **upd)
                crm_id = cur["id"]
                updated += 1
            else:
                # fetch=False: use only the embedded contact (no per-record /contacts
                # API call) so a 50-record batch never approaches the function timeout.
                cb = _contact_basics(job, base, key, fetch=False)
                name = name or cb["name"]
                if not name:
                    continue
                addr = _flatten_address(_g(job, "locationAddress", "address", "jobAddress", "siteAddress", default={}))
                rec = {
                    "name": name, "rid": _g(job, "jobNumber", "number", "refNumber"),
                    "phone": cb["phone"], "email": cb["email"], "address": addr,
                    "work_type": _g(job, "workType", "tradeType", "trade") or _join_list(job.get("tradeTypes")),
                    "source": _g(job, "leadSource", "source"),
                    "rep": _g(job, "salesRep", "assignedTo", "rep") or "Danny Bivins",
                    "external_url": url, "department": "REROOF Department",
                }
                if val:
                    rec[val_col] = val
                cid = _ensure_contact(name, rec)
                if kind == "lead":
                    crm_id = db.insert("leads", {**rec, "contact_id": cid, "stage": stage,
                                                 "stage_since": db.today(), "last_contact": db.today(),
                                                 "narrative": "Synced from AccuLynx (%s)." % (milestone or stage)})
                    db.add_activity("lead", crm_id, "automation", "Synced from AccuLynx — %s" % (milestone or stage))
                    added_l += 1
                else:
                    parts = [p.strip() for p in (addr or "").split(",")]
                    jrow = {**rec, "contact_id": cid, "stage": stage, "stage_since": db.today(),
                            "address": parts[0] if parts else addr,
                            "city": parts[1] if len(parts) > 1 else "", "county": "Palm Beach County",
                            "narrative": "Synced from AccuLynx (%s)." % (milestone or stage)}
                    crm_id = db.insert("jobs", jrow)
                    db.add_activity("job", crm_id, "automation", "Synced from AccuLynx — %s" % (milestone or stage))
                    added_j += 1

            # Deep sync: pull this record's notes + documents via the API.
            if deep and crm_id and jid:
                notes_synced += sync_messages(base, key, jid, crm_kind, crm_id)
                if crm_kind == "job":
                    docs_synced += sync_documents(base, key, jid, crm_id)
        except Exception as e:
            skipped += 1
            last_err = "%s: %s" % (type(e).__name__, e)
            continue

    db.save_company({"acculynx_last_sync": db.now(),
                     "acculynx_group": 0 if done else g,
                     "acculynx_cursor": 0 if done else start})
    return {"ok": True, "batch": len(window), "group": cur_group, "done": done,
            "added_leads": added_l, "added_jobs": added_j, "updated": updated,
            "skipped": skipped, "last_err": last_err,
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
    try:
        result = run_sync(deep=deep)
    except Exception as e:  # never 500 — always show a readable error
        import traceback
        result = {"ok": False, "error": "%s: %s" % (type(e).__name__, e),
                  "trace": traceback.format_exc()[-400:]}
    if result.get("ok"):
        msg = "Batch synced — milestone '%s' · %d records · +%d leads · +%d jobs · %d updated." % (
            result.get("group", "?"), result.get("batch", 0),
            result.get("added_leads", 0), result.get("added_jobs", 0), result.get("updated", 0))
        if result.get("skipped"):
            msg += " (%d skipped — %s)" % (result.get("skipped"), result.get("last_err", "")[:120])
        if result.get("done"):
            msg += " ✅ Full pass complete (all milestones)."
        else:
            msg += " Click Sync again to continue."
        if deep:
            msg += " Notes: %d · Documents: %d." % (result.get("notes_synced", 0), result.get("docs_synced", 0))
        flash(msg, "ok")
    else:
        flash("Sync failed: %s" % result.get("error"), "error")
    ref = request.referrer
    return redirect(ref if ref and "/sync" not in ref else url_for("sync.index"))


@bp.route("/cron")
def cron():
    """Unattended daily sync, called by Vercel Cron. Protected by CRON_SECRET:
    Vercel automatically sends `Authorization: Bearer <CRON_SECRET>` when that env
    var is set. No-op (200) if no API key is configured yet — safe to pre-wire."""
    secret = os.environ.get("CRON_SECRET")
    if secret:
        auth = request.headers.get("Authorization", "")
        if auth != "Bearer " + secret and request.args.get("key") != secret:
            return jsonify({"ok": False, "error": "unauthorized"}), 401
    company = db.get_company()
    if not (company.get("acculynx_api_key") or "").strip():
        return jsonify({"ok": True, "skipped": "no AccuLynx API key configured yet"})
    import time as _t
    deep = bool(request.args.get("deep"))
    t0 = _t.time()
    batches = 0
    agg = {"added_leads": 0, "added_jobs": 0, "updated": 0}
    result = {"done": False}
    try:
        # Walk several 50-record batches per cron run (leads-first, resumable),
        # stopping before the serverless time budget so it never hard-times-out.
        while _t.time() - t0 < 45:
            result = run_sync(deep=deep)
            if not result.get("ok"):
                return jsonify(result), 200
            batches += 1
            for k in agg:
                agg[k] += result.get(k, 0)
            if result.get("done"):
                break
    except Exception as e:
        return jsonify({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}), 200
    return jsonify({"ok": True, "batches": batches, "done": result.get("done"),
                    "jobs_seen": result.get("jobs_seen"), **agg})


@bp.route("/test")
def test():
    """Diagnostic: the server fetches a couple jobs with its stored key and shows
    the HTTP status + raw field names, so the mapping can be matched to AccuLynx's
    actual response. (The server uses its own key — no key is handled by the UI.)"""
    company = db.get_company()
    key = (company.get("acculynx_api_key") or "").strip()
    base = (company.get("acculynx_api_base") or DEFAULT_BASE).strip()
    diag = {"base": base, "key_set": bool(key)}
    if not key:
        diag["error"] = "No API key saved."
        return render_template("sync_test.html", diag=diag)
    import json as _json
    # Try the configured base, plus a couple common AccuLynx hosts, until one returns JSON.
    candidates = [base, "https://api.acculynx.com/api/v2", "https://api.acculynx.com/v2",
                  "https://api.acculynx.com"]
    seen = []
    for b in dict.fromkeys(candidates):
        url = b.rstrip("/") + "/jobs?startIndex=0&pageSize=2"
        try:
            req = urllib.request.Request(url, headers={"Authorization": "Bearer " + key,
                                                       "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=20) as r:
                status = r.getcode()
                raw = r.read().decode("utf-8", "replace")
            try:
                data = _json.loads(raw)
                items = data.get("items") if isinstance(data, dict) else data
                first = (items or [None])[0] if isinstance(items, list) else data
                diag["working_base"] = b
                diag["status"] = status
                diag["top_keys"] = list(data.keys()) if isinstance(data, dict) else "(list)"
                diag["job_keys"] = list(first.keys()) if isinstance(first, dict) else str(first)[:200]
                diag["sample"] = _json.dumps(first, indent=1)[:1800] if isinstance(first, dict) else str(first)[:500]
                if isinstance(first, dict):
                    # Show exactly how the sync now maps this record — so we can confirm
                    # name/address/phone/email/stage all resolve before running it for real.
                    ms = _g(first, "currentMilestone", "milestone", "status")
                    if isinstance(ms, dict):
                        ms = _g(ms, "name", "title")
                    k, st = _resolve_stage(ms)
                    cb = _contact_basics(first, b, key)
                    diag["mapped"] = _json.dumps({
                        "name": (_g(first, "jobName", "name") or cb["name"]),
                        "milestone": ms, "resolved_as": "%s / %s" % (k, st),
                        "rid": _g(first, "jobNumber", "number"),
                        "address": _flatten_address(_g(first, "locationAddress", "address", default={})),
                        "work_type": _g(first, "workType") or _join_list(first.get("tradeTypes")),
                        "source": _g(first, "leadSource", "source"),
                        "phone": cb["phone"], "email": cb["email"],
                    }, indent=1)
                    diag["address_raw"] = _json.dumps(_g(first, "locationAddress", "address", default={}), indent=1)[:600]
                    diag["contact_raw"] = _json.dumps(_pick_contact(first), indent=1)[:900]
                return render_template("sync_test.html", diag=diag)
            except Exception:
                seen.append("%s → HTTP %s, non-JSON: %s" % (b, status, raw[:120]))
        except Exception as e:
            seen.append("%s → %s: %s" % (b, type(e).__name__, str(e)[:120]))
    diag["error"] = "No base URL returned usable JSON."
    diag["attempts"] = seen
    return render_template("sync_test.html", diag=diag)
