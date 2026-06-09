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
                "acculynx_cursor INTEGER DEFAULT 0", "acculynx_group INTEGER DEFAULT 0",
                "acculynx_rr_cursor INTEGER DEFAULT 0", "acculynx_rr_group INTEGER DEFAULT 0",
                "acculynx_doc_cursor INTEGER DEFAULT 0", "acculynx_doc_group INTEGER DEFAULT 0"):
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


def _name_of(v, *prefer):
    """Many AccuLynx fields are objects like {'id':5,'name':'New','abbreviation':'FL'}.
    Reduce any object/list to a plain display string so raw JSON is never stored."""
    if isinstance(v, dict):
        return _g(v, *(prefer or ("name", "title", "value", "type", "label", "abbreviation")))
    if isinstance(v, list):
        return _join_list(v)
    return "" if v in (None, "") else str(v)


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
    city = _name_of(_g(a, "city", "cityText"))
    state = _name_of(_g(a, "state", "stateText", "stateCode", "province"), "abbreviation", "name")
    zc = _name_of(_g(a, "zip", "zipCode", "postalCode", "zipText", "postal"))
    sz = ("%s %s" % (state, zc)).strip()
    return ", ".join([p for p in (line1, line2, city, sz) if p])


def _job_detail(base, guid, key):
    """GET a single job's full record. The /jobs LIST is thin (often just city);
    the DETAIL endpoint carries the structured locationAddress (street1, city,
    state{abbreviation}, zipCode), milestoneDate, workType. Best-effort: {} on fail."""
    if not guid:
        return {}
    try:
        d = _api_get(base, "/jobs/%s" % guid, key)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _address_fields(loc):
    """Structured address columns from an AccuLynx locationAddress object —
    {address(street), city, state(abbrev), zip}. Handles the nested state object
    (the thing that used to corrupt into \"{'id': 9\")."""
    if not isinstance(loc, dict):
        return {}
    line1 = _g(loc, "street1", "addressFirstLine", "address1", "street", "streetAddress", "line1")
    line2 = _g(loc, "street2", "addressSecondLine", "address2", "line2")
    street = ", ".join([p for p in (line1, line2) if p]).strip()
    city = _name_of(_g(loc, "city", "cityText"))
    state = _name_of(_g(loc, "state", "stateText", "stateCode", "province"), "abbreviation", "name")
    zc = _name_of(_g(loc, "zipCode", "zip", "postalCode", "zipText", "postal"))
    return {"address": street or city, "city": city if street else "",
            "state": state, "zip": zc}


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
                                "notes": "Synced from AccuLynx", "drive_id": _drive_mirror(path)})
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


# ---- job-name decoding -----------------------------------------------------
# Shop convention: "R-25179: Richard Reis (PBC) (T28) (SCOTT)" =
#   R-YYNNN (Reroof / year / sequence) | client | (AHJ) | (system + squares) | (salesperson)
_SYS_MAT = {"5V": "5V Metal", "T": "Tile", "S": "Shingle", "M": "Metal"}
_AHJ_MAP = {"PBC": "Palm Beach County", "BB": "Boynton Beach", "LWB": "Lake Worth Beach",
            "RPB": "Royal Palm Beach", "PBG": "Palm Beach Gardens", "WELL": "Wellington",
            "WPB": "West Palm Beach"}
_REP_MAP = {"SCOTT": "Scott", "DB": "Danny Bivins", "FF": "Francis Ferrer",
            "FERRER": "Francis Ferrer", "JHC": "Johnny Cagle", "JAC": "Jacin Carreiro", "MK": "MK"}
# Florida service-area cities -> matched as the AHJ when a name spells the jurisdiction
# out (e.g. "(Delray Beach)") instead of coding it. Longest names matched first.
_FL_CITIES = {
    "boca raton", "boynton beach", "delray beach", "lake worth beach", "lake worth",
    "west palm beach", "palm beach gardens", "royal palm beach", "north palm beach",
    "south palm beach", "palm beach", "wellington", "greenacres", "lantana", "jupiter",
    "juno beach", "lake park", "palm springs", "hypoluxo", "riviera beach", "highland beach",
    "ocean ridge", "manalapan", "atlantis", "haverhill", "loxahatchee groves", "loxahatchee",
    "gulf stream", "hobe sound", "tequesta", "lake clarke shores", "the acreage", "westlake",
    "pahokee", "belle glade", "south bay", "fort lauderdale", "hollywood", "pompano beach",
    "coral springs", "deerfield beach", "davie", "plantation", "sunrise", "tamarac", "margate",
    "coconut creek", "parkland", "lighthouse point", "lauderdale-by-the-sea", "lauderhill",
    "oakland park", "wilton manors", "pembroke pines", "miramar", "weston", "hallandale beach",
    "north lauderdale", "lauderdale lakes", "cooper city", "southwest ranches", "stuart",
    "palm city", "jensen beach", "port st. lucie", "port saint lucie", "fort pierce",
    "miami beach", "miami", "aventura", "hialeah", "homestead", "north miami", "key biscayne",
    "palmetto bay", "coral gables", "cape coral", "fort myers", "north fort myers", "estero",
    "bonita springs", "lehigh acres", "punta gorda", "port charlotte", "north port",
}
_FL_CITIES_SORTED = sorted(_FL_CITIES, key=len, reverse=True)

try:
    db.execute("ALTER TABLE jobs ADD COLUMN squares TEXT")
except Exception:
    pass
db._COLCACHE.clear()


def _parse_job_name(name):
    """Decode a SeaBreeze job name into structured parts. Format varies (parens or
    dashes), so each field is matched independently. Returns only what's found:
    {year, jobno, system, squares, ahj, rep}. System code = material letter + squares
    (e.g. T28 = Tile, 28 squares)."""
    n = name or ""
    out = {}
    m = re.search(r"\bR-?(\d{2})(\d{2,})\b", n)
    if m:
        out["year"] = "20" + m.group(1)
        out["jobno"] = "R-" + m.group(1) + m.group(2)
    sm = re.search(r"\b(5V|[TSM])\s?-?\s?(\d{1,3})\b", n)   # material + squares
    if sm:
        out["system"] = _SYS_MAT.get(sm.group(1).upper())
        out["squares"] = sm.group(2)
    toks = re.findall(r"[A-Za-z0-9]+", n.upper())
    for t in toks:                 # AHJ #1: known coded jurisdiction tag (PBC, BB, ...)
        if t in _AHJ_MAP:
            out["ahj"] = _AHJ_MAP[t]
            break
    if "ahj" not in out:           # AHJ #2: a full FL city name — prefer one inside parens
        parens = " | ".join(re.findall(r"\(([^)]*)\)", n))
        for src in (parens, n):
            sl = " " + re.sub(r"\s+", " ", re.sub(r"[^a-z. ]", " ", src.lower())) + " "
            hit = next((c for c in _FL_CITIES_SORTED if (" " + c + " ") in sl), None)
            if hit:
                out["ahj"] = hit.title()
                break
    for t in reversed(toks):       # rep: last known rep tag (salesperson is usually last)
        if t in _REP_MAP:
            out["rep"] = _REP_MAP[t]
            break
    return out


# ---- Reverse maps: compose a canonical SeaBreeze job name from structured parts ----
# Canonical format (documented convention):  R-YY###: Client (AHJ) (RoofCode+Sq) (Rep)
#   RoofCode = material letter + squares, e.g. T28 = Tile 28 sq.  L suffix = still a lead.
_AHJ_CODE = {v: k for k, v in _AHJ_MAP.items()}          # "Palm Beach County" -> "PBC"
_REP_CODE = {"Danny Bivins": "DB", "Scott": "SCOTT", "Francis Ferrer": "FF",
             "Johnny Cagle": "JHC", "Jacin Carreiro": "JAC", "MK": "MK"}


def _sys_letter(work_type="", system=""):
    """Material letter (S/T/M/5V) from a work type or roof-system string."""
    s = ("%s %s" % (work_type or "", system or "")).lower()
    if "tile" in s:
        return "T"
    if "shingle" in s:
        return "S"
    if "5v" in s or "5-v" in s:
        return "5V"
    if "metal" in s or "galvalume" in s:
        return "M"
    return ""


def _ahj_code(ahj=""):
    a = (ahj or "").strip()
    if not a:
        return ""
    if a in _AHJ_CODE:
        return _AHJ_CODE[a]
    # Unknown jurisdiction: initials of each word (Delray Beach -> DB-ish); cap at 4 chars.
    init = "".join(w[0] for w in re.findall(r"[A-Za-z]+", a)).upper()
    return init[:4] or a[:4].upper()


def _rep_code(rep=""):
    r = (rep or "").strip()
    if not r:
        return ""
    if r in _REP_CODE:
        return _REP_CODE[r]
    # Fall back to initials (first + last) for unknown reps.
    parts = re.findall(r"[A-Za-z]+", r)
    return ("".join(p[0] for p in parts).upper() or r[:3].upper())


def compose_job_name(client, ahj="", work_type="", system="", squares="",
                     rep="", rid="", is_lead=False):
    """Build a canonical name. squares optional (added to the roof code once known).
    is_lead=True appends an 'L' marker (pre-job lifecycle)."""
    client = (client or "").strip() or "New Customer"
    letter = _sys_letter(work_type, system)
    sq = re.sub(r"[^0-9.]", "", str(squares or "")).split(".")[0]
    roof = (letter + sq) if letter else ""
    bits = [client]
    a = _ahj_code(ahj)
    if a:
        bits.append("(%s)" % a)
    if roof:
        bits.append("(%s)" % roof)
    rc = _rep_code(rep)
    if rc:
        bits.append("(%s)" % rc)
    if is_lead:
        bits.append("L")
    name = " ".join(bits)
    return ("%s: %s" % (rid, name)) if rid else name


def next_job_number(year=None):
    """Next R-YY### number, continuing the highest existing sequence for the year."""
    import db
    yy = (str(year) if year else db.today()[:4])[-2:]
    hi = 0
    for j in db.all_rows("jobs"):
        m = re.match(r"\s*R-?%s(\d{2,})" % re.escape(yy), (j.get("rid") or "") + " " + (j.get("name") or ""))
        if m:
            hi = max(hi, int(m.group(1)))
    return "R-%s%03d" % (yy, hi + 1)


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
    # Active pipeline the office works day-to-day: leads (Assigned) → prospects →
    # approved jobs → completed → invoiced. Ordered newest-first within each group.
    # Deliberately skips the huge historical buckets (Closed ~1,158 / Canceled ~6,831)
    # so a full pass stays ~800 records, not ~8,800.
    GROUPS = ["lead", "prospect", "approved", "completed", "invoiced"]
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

    # Index existing records by AccuLynx GUID (from external_url) first, name second,
    # so re-syncs UPDATE the same record instead of inserting duplicates.
    def _guid_of(u):
        m = re.search(r"/jobs/([0-9a-f-]{30,})", u or "")
        return m.group(1) if m else None
    _leads = db.all_rows("leads")
    _jobs = db.all_rows("jobs")
    exLg = {_guid_of(l.get("external_url")): l for l in _leads if _guid_of(l.get("external_url"))}
    exJg = {_guid_of(j.get("external_url")): j for j in _jobs if _guid_of(j.get("external_url"))}
    exL = {(l.get("name") or "").lower(): l for l in _leads}
    exJ = {(j.get("name") or "").lower(): j for j in _jobs}
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
            # The list payload's address is thin (often just city) and its state is a
            # nested object. Pull the DETAIL record for the structured full address.
            detail = _job_detail(base, jid, key)
            loc = _g(detail, "locationAddress", "address", default={}) or \
                  _g(job, "locationAddress", "address", "jobAddress", "siteAddress", default={})
            af = _address_fields(loc)
            # Job progress: the detailed AccuLynx milestone + its date.
            mdate = (_g(job, "milestoneDate", "milestone_date") or "")[:10]
            progress = ("Milestone: %s%s" % (milestone or grp, (" (as of %s)" % mdate) if mdate else "")).strip()

            if kind == "lead":
                cur = exLg.get(jid) or (exL.get(name.lower()) if name else None)
            else:
                cur = exJg.get(jid) or (exJ.get(name.lower()) if name else None)
            crm_kind = "lead" if kind == "lead" else "job"
            crm_id = None

            if cur:
                upd = {"stage": stage, "external_url": url, "todo": progress}
                if val:
                    upd[val_col] = val
                if af.get("address"):  # backfill/repair the full structured address
                    upd["address"] = af["address"]
                    if crm_kind == "job":
                        upd.update({"city": af.get("city") or None, "state": af.get("state") or None,
                                    "zip": af.get("zip") or None})
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
                addr = _flatten_address(loc)  # full display string from the DETAIL record
                rec = {
                    "name": name, "rid": _g(job, "jobNumber", "number", "refNumber"),
                    "phone": cb["phone"], "email": cb["email"], "address": addr,
                    "work_type": _name_of(_g(job, "workType", "tradeType", "trade")) or _join_list(job.get("tradeTypes")),
                    "source": _name_of(_g(job, "leadSource", "source")),
                    "rep": _name_of(_g(job, "salesRep", "assignedTo", "rep")) or "Danny Bivins",
                    "external_url": url, "department": "REROOF Department", "todo": progress,
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
                    jrow = {**rec, "contact_id": cid, "stage": stage, "stage_since": db.today(),
                            "address": af.get("address") or addr,
                            "city": af.get("city") or "", "state": af.get("state") or "",
                            "zip": af.get("zip") or "", "county": "Palm Beach County",
                            "narrative": "Synced from AccuLynx (%s)." % (milestone or stage)}
                    _pj = _parse_job_name(name)   # decode AHJ / system / squares / rep from the job name
                    for _c in ("system", "squares", "ahj", "rep"):
                        if _pj.get(_c):
                            jrow[_c] = _pj[_c]
                    # Fallback when the job name didn't encode them: derive the AHJ from the
                    # property address and the roof system from the work type (mirrors lead
                    # intake) so the permit builder defaults correctly. Strict resolvers only
                    # write a confident value (real library AHJ / explicitly-named material),
                    # leaving messy rows blank rather than guessing.
                    from modules import ahj as _ahj
                    if not jrow.get("ahj"):
                        _a = _ahj.resolve_ahj_strict(jrow.get("address", ""), jrow.get("city", ""), jrow.get("county", ""))
                        if _a:
                            jrow["ahj"] = _a
                    if not jrow.get("system"):
                        _s = _ahj.system_from_work_type_strict(jrow.get("work_type", ""))
                        if _s:
                            jrow["system"] = _s
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


def _guid_of_url(u):
    m = re.search(r"/jobs/([0-9a-f-]{30,})", u or "")
    return m.group(1) if m else None


def dedupe_records():
    """Collapse AccuLynx-synced duplicates left over from earlier imports.
    - Jobs: by AccuLynx GUID only (a customer can legitimately have several distinct
      jobs, so never collapse jobs by name).
    - Leads: by GUID, then by name. On a name collision we keep the record that
      carries the AccuLynx link (GUID) over a link-less old import.
    Keeps exactly one of each. Returns before/after counts."""
    before_l, before_j = len(db.all_rows("leads")), len(db.all_rows("jobs"))
    removed = {"jobs_guid": 0, "leads_guid": 0, "leads_name": 0}

    def _collapse(table, rows, keyfn):
        seen, dele = set(), []
        for r in rows:
            k = keyfn(r)
            if k is None:
                continue
            if k in seen:
                dele.append(r["id"])
            else:
                seen.add(k)
        for rid in dele:
            db.delete(table, rid)
        return len(dele)

    removed["jobs_guid"] = _collapse("jobs", db.all_rows("jobs", order="id ASC"),
                                     lambda r: _guid_of_url(r.get("external_url")))
    removed["leads_guid"] = _collapse("leads", db.all_rows("leads", order="id ASC"),
                                      lambda r: _guid_of_url(r.get("external_url")))
    # Name pass: GUID-bearing rows first so the linked record survives the collision.
    lead_rows = sorted(db.all_rows("leads"), key=lambda r: (0 if _guid_of_url(r.get("external_url")) else 1, r["id"]))
    removed["leads_name"] = _collapse("leads", lead_rows, lambda r: (r.get("name") or "").strip().lower() or None)

    after_l, after_j = len(db.all_rows("leads")), len(db.all_rows("jobs"))
    return {"before_leads": before_l, "before_jobs": before_j,
            "after_leads": after_l, "after_jobs": after_j, "removed": removed}


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


@bp.route("/log")
def log():
    """A list of WHAT was synced from AccuLynx — the trail each sync writes (records,
    billing, estimates, comms, documents), newest first, with the record it landed on."""
    acts = db.all_rows("activities", where="text LIKE ?", params=("%AccuLynx%",), order="id DESC")
    leads = {l["id"]: l for l in db.all_rows("leads")}
    jobs = {j["id"]: j for j in db.all_rows("jobs")}
    items = []
    for a in acts[:400]:
        rec = (jobs if a.get("entity_type") == "job" else leads).get(a.get("entity_id"), {})
        t = (a.get("text") or "")
        tl = t.lower()
        if "billing" in tl:
            cat = "💵 Billing"
        elif "estimate" in tl:
            cat = "📝 Estimate"
        elif "communication" in tl or "notes synced" in tl or "messages" in tl:
            cat = "💬 Comms"
        elif "document" in tl:
            cat = "📎 Document"
        else:
            cat = "🔄 Record"
        items.append({"when": (a.get("created") or "")[:16], "type": a.get("entity_type") or "",
                      "name": rec.get("name") or "(removed)", "rid": rec.get("rid") or "",
                      "cat": cat, "what": t})
    from collections import Counter
    tally = Counter(i["cat"] for i in items)
    return render_template("sync_log.html", items=items, tally=dict(tally),
                           total=len(acts), company=db.get_company())


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
    g = (rec.get("guid") or "").strip().lower()

    def _find(rows):
        # match by AccuLynx GUID first (robust — the API sync keeps the 'R-####:' name
        # prefix, the bookmarklet strips it, so name-only matching duplicates), then name.
        if g:
            hit = next((r for r in rows if g in (r.get("external_url") or "").lower()), None)
            if hit:
                return hit
        return next((r for r in rows if (r.get("name") or "").lower() == name.lower()), None)

    if bucket in ("lead", "assigned", "prospect", "negotiation", "long_term"):
        stage = "assigned" if bucket in ("lead", "assigned") else "prospect"
        cur = _find(db.all_rows("leads"))
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
    cur = _find(db.all_rows("jobs"))
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


ALLOWED_DOC_EXT = {"pdf", "jpg", "jpeg", "png", "gif", "tif", "tiff",
                   "doc", "docx", "xls", "xlsx", "txt", "heic", "webp"}


def _cors(payload, code=200):
    r = jsonify(payload)
    r.status_code = code
    r.headers["Access-Control-Allow-Origin"] = "*"
    r.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return r


def _drive_mirror(path):
    """Push a saved doc to Google Drive so it survives the cloud's ephemeral disk.
    Returns the Drive file id (stored as drive_id) or None. No-op if Drive is off."""
    try:
        from modules import gdrive
        if gdrive.enabled() and os.path.exists(path):
            return gdrive.mirror(path, os.path.basename(path))
    except Exception:
        pass
    return None


def _finalize_doc(guid, folder, name, src_path):
    """Match the synced job by AccuLynx GUID, dedup, and attach the saved file at
    src_path. Removes src_path on any rejection. Returns a CORS JSON response."""
    size = os.path.getsize(src_path) if os.path.exists(src_path) else 0
    def _drop():
        try:
            os.remove(src_path)
        except Exception:
            pass
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext and ext not in ALLOWED_DOC_EXT:
        _drop()
        return _cors({"ok": False, "reason": "ext_blocked:%s" % ext, "name": name})
    if size > 64 * 1024 * 1024:
        _drop()
        return _cors({"ok": False, "reason": "too_large", "size": size, "name": name})
    job = next((j for j in db.all_rows("jobs") if guid in (j.get("external_url") or "").lower()), None)
    if not job:
        _drop()
        return _cors({"ok": False, "reason": "no_job", "guid": guid})
    same = [d for d in db.all_rows("documents", where="job_id=?", params=(job["id"],))
            if (d.get("original_name") or "").lower() == name.lower()]
    # Skip only if a LIVE copy already exists; replace ghost (byte-less) records.
    if any(_doc_has_bytes(d) for d in same):
        _drop()
        return _cors({"ok": True, "skipped": "duplicate", "name": name, "job": job.get("name")})
    for d in same:
        db.delete("documents", d["id"])  # drop dead duplicate so this re-import is clean
    db.insert("documents", {"job_id": job["id"], "category": folder,
                            "filename": os.path.basename(src_path), "original_name": name,
                            "size": size, "notes": "Permit doc synced from AccuLynx",
                            "drive_id": _drive_mirror(src_path)})
    db.add_activity("job", job["id"], "note",
                    "Permit document synced from AccuLynx: %s (%s)" % (name, folder))
    return _cors({"ok": True, "added": True, "job": job.get("name"), "folder": folder, "name": name})


@bp.route("/doc-manifest")
def doc_manifest():
    """CORS-open: list the original filenames already attached to the synced job
    (matched by AccuLynx GUID), so the collector can skip them WITHOUT re-uploading
    the bytes. Makes re-runs and resumes cheap."""
    guid = (request.args.get("guid") or "").strip().lower()
    if not guid:
        return _cors({"ok": False, "reason": "missing guid"}, 400)
    job = next((j for j in db.all_rows("jobs") if guid in (j.get("external_url") or "").lower()), None)
    if not job:
        return _cors({"ok": False, "reason": "no_job", "guid": guid})
    # Only advertise docs we can actually serve (mirrored to Drive or present on
    # local disk). Byte-less ghost records — files lost to the cloud's ephemeral
    # disk before Drive mirroring existed — are omitted so the collector re-fetches
    # them and they get persisted to Drive this time.
    names = [(d.get("original_name") or "") for d in
             db.all_rows("documents", where="job_id=?", params=(job["id"],))
             if _doc_has_bytes(d)]
    return _cors({"ok": True, "job": job.get("name"), "names": names})


def _doc_has_bytes(d):
    """True if a document's file is retrievable: mirrored to Drive, or present on
    local disk (desktop). False for ghost records whose bytes are gone."""
    if d.get("drive_id"):
        return True
    fn = os.path.basename(d.get("filename") or "")
    return bool(fn) and os.path.exists(os.path.join(config.DOC_DIR, fn))


@bp.route("/doc-import", methods=["POST", "OPTIONS"])
def doc_import():
    """Attach a single permit/document file scraped from the AccuLynx tab to the
    matching synced job (matched by AccuLynx GUID in external_url). CORS-open like
    browser-import. Supports CHUNKED uploads (uploadId/chunkIndex/chunkTotal) so
    large scans clear Render's ingress, which drops big cross-origin bodies. Guarded:
    known-job only, allow-listed extensions, 64 MB cap, dedup by filename."""
    from flask import make_response
    if request.method == "OPTIONS":
        r = make_response("", 204)
        r.headers["Access-Control-Allow-Origin"] = "*"
        r.headers["Access-Control-Allow-Headers"] = "Content-Type"
        r.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        return r

    f = request.files.get("file")
    guid = (request.form.get("guid") or "").strip().lower()
    folder = (request.form.get("folder") or "AccuLynx").strip()[:60]
    name = (request.form.get("filename") or (f.filename if f else "") or "acculynx_doc").strip()
    upload_id = request.form.get("uploadId")

    if upload_id:  # chunked: append each part to a temp file, finalize on the last
        uid = re.sub(r"[^A-Za-z0-9_-]", "", upload_id)[:80]
        idx = int(request.form.get("chunkIndex") or 0)
        total = int(request.form.get("chunkTotal") or 1)
        if not uid or f is None:
            return _cors({"ok": False, "reason": "bad_chunk"}, 400)
        cdir = os.path.join(config.DOC_DIR, "_chunks")
        os.makedirs(cdir, exist_ok=True)
        part = os.path.join(cdir, uid + ".part")
        if idx == 0 and os.path.exists(part):
            os.remove(part)  # stale restart
        with open(part, "ab") as out:
            out.write(f.read())
        if os.path.getsize(part) > 64 * 1024 * 1024:
            os.remove(part)
            return _cors({"ok": False, "reason": "too_large"})
        if idx < total - 1:
            return _cors({"ok": True, "chunk": idx})
        safe = "%d_%s" % (int(time.time() * 1000), _safe_name(name))
        final = os.path.join(config.DOC_DIR, safe)
        os.replace(part, final)
        return _finalize_doc(guid, folder, name, final)

    # single-shot (small files)
    if not guid or not f:
        return _cors({"ok": False, "reason": "missing guid or file"}, 400)
    safe = "%d_%s" % (int(time.time() * 1000), _safe_name(name))
    path = os.path.join(config.DOC_DIR, safe)
    f.save(path)
    return _finalize_doc(guid, folder, name, path)


def _ws_cat(desc):
    d = (desc or "").lower()
    if "labor" in d or "install" in d:
        return "Labor"
    if "permit" in d:
        return "Permit"
    if "overhead" in d or "admin" in d:
        return "Overhead"
    if "dump" in d or "disposal" in d or "haul" in d:
        return "Other"
    return "Material"


@bp.route("/worksheet-import", methods=["POST", "OPTIONS"])
def worksheet_import():
    """CORS-open: receive a job's AccuLynx financial worksheet (price total + cost
    line items) and store it in the worksheets/worksheet_lines tables so the CRM's
    Profit Analysis shows real contract-vs-cost gross profit. Matches job by GUID;
    replaces the worksheet's lines each run (idempotent). Never 500s."""
    from flask import make_response
    if request.method == "OPTIONS":
        r = make_response("", 204)
        r.headers["Access-Control-Allow-Origin"] = "*"
        r.headers["Access-Control-Allow-Headers"] = "Content-Type"
        r.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        return r
    try:
        body = request.get_json(force=True, silent=True)
        if body is None:
            import json as _json
            body = _json.loads(request.get_data(as_text=True) or "{}")
        items = body if isinstance(body, list) else [body]
        results, agg = [], {"worksheets": 0, "lines": 0, "no_job": 0}
        for it in items:
            if not isinstance(it, dict):
                continue
            guid = (it.get("guid") or "").strip().lower()
            job = next((j for j in db.all_rows("jobs")
                        if guid and guid in (j.get("external_url") or "").lower()), None)
            if not job:
                agg["no_job"] += 1
                results.append({"ok": False, "reason": "no_job", "guid": guid})
                continue
            ws = db.all_rows("worksheets", where="job_id=?", params=(job["id"],))
            ws_id = ws[0]["id"] if ws else db.insert("worksheets", {"job_id": job["id"], "created": db.now()})
            cv = _money_num(it.get("contract_value")) or (ws[0].get("contract_value") if ws else 0)
            db.update("worksheets", ws_id, contract_value=cv, updated=db.now(),
                      notes="Synced from AccuLynx worksheet")
            db.execute("DELETE FROM worksheet_lines WHERE worksheet_id=?", (ws_id,))
            n = 0
            for i, ln in enumerate(it.get("lines") or []):
                cost = _money_num(ln.get("cost"))
                desc = (ln.get("description") or "").strip()
                if not desc and not cost:
                    continue
                db.insert("worksheet_lines", {"worksheet_id": ws_id, "sort": i,
                          "category": ln.get("category") or _ws_cat(desc),
                          "description": desc or "(line)", "budget_cost": cost, "actual_cost": cost,
                          "qty": _money_num(ln.get("qty")), "unit": (ln.get("unit") or "")[:8],
                          "unit_cost": _money_num(ln.get("unit_cost"))})
                n += 1
            agg["worksheets"] += 1
            agg["lines"] += n
            db.add_activity("job", job["id"], "note",
                            "AccuLynx worksheet synced: %d line(s), cost %s, price %s"
                            % (n, _money_str(_money_num(it.get("cost_total"))), _money_str(cv)))
            results.append({"ok": True, "job": job.get("name"), "lines": n})
        db.save_company({"acculynx_last_sync": db.now()})
        return _cors({"ok": True, "summary": agg, "results": results[:50]})
    except Exception as e:
        import traceback
        return _cors({"ok": False, "error": "%s: %s" % (type(e).__name__, e),
                      "trace": traceback.format_exc()[-300:]})


@bp.route("/job-guids")
def job_guids():
    """CORS-open: AccuLynx GUIDs of every synced job (feeds the worksheet collector
    so it doesn't have to scroll-scrape the virtualized job list)."""
    out = []
    for j in db.all_rows("jobs"):
        m = re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                      (j.get("external_url") or ""), re.I)
        if m:
            out.append(m.group(0).lower())
    return _cors({"ok": True, "guids": list(dict.fromkeys(out))})


def _ensure_catalog_table():
    db.execute("CREATE TABLE IF NOT EXISTS material_catalog (id %s, name TEXT, unit TEXT, "
               "unit2 TEXT, category TEXT)" % ("SERIAL PRIMARY KEY" if getattr(db, "IS_PG", False)
                                               else "INTEGER PRIMARY KEY AUTOINCREMENT"))
    db._COLCACHE.pop("material_catalog", None)


@bp.route("/catalog-import", methods=["POST", "OPTIONS"])
def catalog_import():
    """CORS-open: receive the AccuLynx Company Library material catalog (name + units
    + Material/Labor) and store it as a reusable CRM material catalog. Replaces all."""
    from flask import make_response
    if request.method == "OPTIONS":
        r = make_response("", 204)
        r.headers["Access-Control-Allow-Origin"] = "*"
        r.headers["Access-Control-Allow-Headers"] = "Content-Type"
        r.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        return r
    try:
        _ensure_catalog_table()
        body = request.get_json(force=True, silent=True) or []
        db.execute("DELETE FROM material_catalog")
        n = 0
        for it in body:
            if not isinstance(it, dict):
                continue
            nm = (it.get("n") or it.get("name") or "").strip()
            if not nm:
                continue
            db.insert("material_catalog", {"name": nm[:120], "unit": (it.get("u") or "")[:8],
                      "unit2": (it.get("u2") or "")[:8], "category": it.get("c") or "Material"})
            n += 1
        return _cors({"ok": True, "imported": n})
    except Exception as e:
        import traceback
        return _cors({"ok": False, "error": "%s: %s" % (type(e).__name__, e),
                      "trace": traceback.format_exc()[-300:]})


@bp.route("/catalog")
def catalog_view():
    """Viewer for the imported material catalog (Tools → Material Catalog)."""
    try:
        items = db.all_rows("material_catalog", order="category, name")
    except Exception:
        items = []
    return render_template("catalog.html", items=items)


@bp.route("/run", methods=["POST"])
def run():
    deep = bool(request.form.get("deep"))  # OFF: AccuLynx API has no GET for messages/docs (404)
    import time as _t
    t0 = _t.time()
    agg = {"added_leads": 0, "added_jobs": 0, "updated": 0, "skipped": 0, "batch": 0}
    result = {"ok": True, "done": False}
    last_err = ""
    try:
        # Vercel serverless functions are killed at ~10s, so do ONE small batch (10
        # records) per click and return fast — never long enough to time out. The
        # cursor persists between clicks, so just click Sync again until it says
        # the full pass is complete.
        result = run_sync(deep=deep, batch=10)
        if result.get("ok"):
            for k in agg:
                agg[k] += result.get(k, 0)
            last_err = result.get("last_err") or last_err
    except Exception as e:  # never 500 — always show a readable error
        import traceback
        result = {"ok": False, "error": "%s: %s" % (type(e).__name__, e),
                  "trace": traceback.format_exc()[-400:]}
    if result.get("ok"):
        msg = "Synced %d records — +%d leads · +%d jobs · %d updated." % (
            agg["batch"], agg["added_leads"], agg["added_jobs"], agg["updated"])
        if agg["skipped"]:
            msg += " (%d skipped — %s)" % (agg["skipped"], last_err[:120])
        if result.get("done"):
            msg += " ✅ Full pass complete (all active milestones)."
        else:
            msg += " More remain — click Sync again to continue."
        flash(msg, "ok")
    else:
        flash("Sync failed: %s" % result.get("error"), "error")
    ref = request.referrer
    return redirect(ref if ref and "/sync" not in ref else url_for("sync.index"))


@bp.route("/dedupe", methods=["POST"])
def dedupe():
    """Remove leftover duplicate synced records (idempotent — safe to run anytime)."""
    try:
        r = dedupe_records()
    except Exception as e:
        flash("Dedupe failed: %s: %s" % (type(e).__name__, e), "error")
        return redirect(url_for("sync.index"))
    rm = r["removed"]
    total = rm["jobs_guid"] + rm["leads_guid"] + rm["leads_name"]
    if total:
        flash("Removed %d duplicate(s) — leads %d→%d, jobs %d→%d (jobs by GUID: %d · leads by GUID: %d, by name: %d)." % (
            total, r["before_leads"], r["after_leads"], r["before_jobs"], r["after_jobs"],
            rm["jobs_guid"], rm["leads_guid"], rm["leads_name"]), "ok")
    else:
        flash("No duplicates found — records are already clean.", "ok")
    return redirect(url_for("sync.index"))


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
    deep = bool(request.args.get("deep"))  # OFF: AccuLynx API has no GET for messages/docs (404)
    t0 = _t.time()
    batches = 0
    agg = {"added_leads": 0, "added_jobs": 0, "updated": 0}
    result = {"done": False}
    try:
        # A few small batches per cron run (resumable cursor), staying well under
        # Vercel's ~10s serverless cap so the function never gets killed mid-write.
        while _t.time() - t0 < 8:
            result = run_sync(deep=deep, batch=10)
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


# ===========================================================================
# Browser-bridge sync for Billing / Estimates / Communications.
#
# Same pattern as doc-import: a CORS-open POST endpoint receives records that a
# bookmarklet (running in the logged-in my.acculynx.com tab) fetched from the
# INTERNAL web API and POSTed here, keyed by AccuLynx job GUID. We match the CRM
# job/lead by that GUID (found in external_url), upsert, and dedup so re-runs are
# idempotent. Every endpoint is wrapped so it NEVER 500s — it returns readable
# JSON errors instead. Field extraction is DEFENSIVE (_g multi-candidate keys)
# because the exact internal field names aren't all confirmed live yet.
# ===========================================================================

def _ensure_billing_schema():
    """Tables/columns for synced billing + estimates. Additive, idempotent."""
    conn = db.connect()
    # Payments received against a job (AccuLynx "Payments"). Invoices reuse the
    # existing `invoices` table; payments get their own so balance math is exact.
    conn.execute("""CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created TEXT, job_id INTEGER,
        ext_id TEXT, amount REAL DEFAULT 0,
        method TEXT, reference TEXT, paid_date TEXT, notes TEXT,
        source TEXT DEFAULT 'AccuLynx')""")
    conn.commit()
    conn.close()
    # Invoices: tag synced ones with the AccuLynx invoice id so re-runs update,
    # not duplicate. (ext_id added defensively; invoices already has number/amount.)
    db._ensure_column("invoices", "ext_id", "TEXT")
    db._ensure_column("invoices", "source", "TEXT")
    db._ensure_column("invoices", "invoice_date", "TEXT")
    # Estimates: tag synced ones with the AccuLynx estimate id + a plain total so
    # we don't have to fabricate line items we may not have. amount_total is a
    # display string; the estimates module's section math is untouched.
    db._ensure_column("estimates", "ext_id", "TEXT")
    db._ensure_column("estimates", "source", "TEXT")
    db._ensure_column("estimates", "amount_total", "TEXT")
    db._ensure_column("estimates", "ext_status", "TEXT")
    db._ensure_column("estimates", "ext_date", "TEXT")
    db._COLCACHE.clear()


try:
    _ensure_billing_schema()
except Exception:
    pass


def _money_num(v):
    """Coerce any AccuLynx money value (number, '$1,234.56', or {'amount':..}) to a float."""
    if isinstance(v, dict):
        v = _g(v, "amount", "value", "total", "totalAmount", default="")
    if v in (None, ""):
        return 0.0
    try:
        return float(re.sub(r"[^0-9.\-]", "", str(v)) or 0)
    except Exception:
        return 0.0


def _money_str(n):
    """Format a float as '$12,345' (matches the existing contract_value style)."""
    try:
        n = float(n)
    except Exception:
        return ""
    return ("$" + format(int(round(n)), ",")) if n else ""


def _job_by_guid(guid):
    """Find the CRM JOB whose external_url contains this AccuLynx GUID."""
    guid = (guid or "").strip().lower()
    if not guid:
        return None
    return next((j for j in db.all_rows("jobs")
                 if guid in (j.get("external_url") or "").lower()), None)


def _lead_by_guid(guid):
    guid = (guid or "").strip().lower()
    if not guid:
        return None
    return next((l for l in db.all_rows("leads")
                 if guid in (l.get("external_url") or "").lower()), None)


def _record_by_guid(guid):
    """Return (kind, row) where kind is 'job' or 'lead', or (None, None)."""
    j = _job_by_guid(guid)
    if j:
        return "job", j
    l = _lead_by_guid(guid)
    if l:
        return "lead", l
    return None, None


def _date10(v):
    return (str(v or ""))[:10]


# ---- 1) BILLING / PAYMENTS -------------------------------------------------

def _apply_billing(guid, payload):
    """Upsert one job's billing block. payload may carry:
       value/jobValue/contractValue, balance/balanceDue, arAge,
       invoices:[{id,number,amount,date,status}],
       payments:[{id,amount,date,method,reference}].
    Returns a result dict. Never raises (caller wraps too, belt-and-suspenders)."""
    kind, rec = _record_by_guid(guid)
    if not rec:
        return {"ok": False, "reason": "no_job", "guid": guid}

    out = {"ok": True, "guid": guid, "record": rec.get("name"), "kind": kind,
           "value_set": False, "invoices_added": 0, "invoices_updated": 0,
           "payments_added": 0, "payments_updated": 0}

    # --- job/contract value -> the column the dashboard SUMS -----------------
    val = _money_num(_g(payload, "value", "jobValue", "contractValue", "totalValue",
                        "totalContractValue", "jobTotal", "totalJobValue",
                        "estimateTotal", "contractTotal", "amount", default=""))
    if val:
        col = "contract_value" if kind == "job" else "estimate"
        db.update(kind + "s", rec["id"], **{col: _money_str(val)})
        out["value_set"] = True
        out["value"] = _money_str(val)

    balance = _money_num(_g(payload, "balance", "balanceDue", "balance_due",
                            "arBalance", "amountDue", default=""))

    # Billing only attaches to JOBS for the invoices/payments tables (job_id FK).
    job_id = rec["id"] if kind == "job" else None

    if job_id:
        # --- invoices (reuse the invoices table, dedup by ext_id or number) --
        existing = db.all_rows("invoices", where="job_id=?", params=(job_id,))
        by_ext = {(i.get("ext_id") or ""): i for i in existing if i.get("ext_id")}
        by_num = {(i.get("number") or "").lower(): i for i in existing if i.get("number")}
        invs = _g(payload, "invoices", "Invoices", default=[]) or []
        for inv in (invs if isinstance(invs, list) else []):
            if not isinstance(inv, dict):
                continue
            ext = str(_g(inv, "id", "invoiceId", "guid", "Id", default="")).strip()
            num = str(_g(inv, "number", "invoiceNumber", "Number", "name", default="")).strip()
            amt = _money_num(_g(inv, "amount", "total", "totalAmount", "amountDue", "Amount", default=""))
            idate = _date10(_g(inv, "date", "invoiceDate", "createdOn", "issuedDate", "Date", default=""))
            stat = (_name_of(_g(inv, "status", "state", "Status", default="")) or "").lower() or "unpaid"
            stat = {"open": "unpaid", "draft": "unpaid", "issued": "sent",
                    "paid": "paid", "partial": "partial", "partiallypaid": "partial"}.get(
                        re.sub(r"[^a-z]", "", stat), stat if stat in ("unpaid", "sent", "partial", "paid") else "unpaid")
            cur = (by_ext.get(ext) if ext else None) or (by_num.get(num.lower()) if num else None)
            data = {"job_id": job_id, "number": num or ("AX-" + ext[:8] if ext else ""),
                    "amount": amt, "status": stat, "invoice_date": idate,
                    "ext_id": ext, "source": "AccuLynx"}
            if cur:
                db.update("invoices", cur["id"], **data)
                out["invoices_updated"] += 1
            else:
                iid = db.insert("invoices", data)
                if ext:
                    by_ext[ext] = {"id": iid, "ext_id": ext}
                if num:
                    by_num[num.lower()] = {"id": iid, "number": num}
                out["invoices_added"] += 1

        # --- payments (own table, dedup by ext_id; else by amount+date) ------
        ex_pay = db.all_rows("payments", where="job_id=?", params=(job_id,))
        pe_ext = {(p.get("ext_id") or ""): p for p in ex_pay if p.get("ext_id")}
        pe_ad = {((p.get("paid_date") or "") + "|" + str(p.get("amount") or 0)): p for p in ex_pay}
        pays = _g(payload, "payments", "Payments", default=[]) or []
        for pay in (pays if isinstance(pays, list) else []):
            if not isinstance(pay, dict):
                continue
            ext = str(_g(pay, "id", "paymentId", "guid", "Id", default="")).strip()
            amt = _money_num(_g(pay, "amount", "total", "Amount", default=""))
            pdate = _date10(_g(pay, "date", "paidOn", "paymentDate", "createdOn", "Date", default=""))
            meth = _name_of(_g(pay, "method", "type", "paymentType", "Method", default=""))
            ref = _name_of(_g(pay, "reference", "checkNumber", "memo", "Reference", default=""))
            akey = pdate + "|" + str(amt)
            cur = (pe_ext.get(ext) if ext else None) or pe_ad.get(akey)
            data = {"job_id": job_id, "amount": amt, "paid_date": pdate,
                    "method": meth, "reference": ref, "ext_id": ext, "source": "AccuLynx"}
            if cur:
                db.update("payments", cur["id"], **data)
                out["payments_updated"] += 1
            else:
                pid = db.insert("payments", data)
                if ext:
                    pe_ext[ext] = {"id": pid, "ext_id": ext}
                pe_ad[akey] = {"id": pid}
                out["payments_added"] += 1

    # Single deduped activity summarizing the billing pull.
    if job_id and (out["value_set"] or out["invoices_added"] or out["payments_added"]
                   or out["invoices_updated"] or out["payments_updated"]):
        bits = []
        if out.get("value"):
            bits.append("value " + out["value"])
        if balance:
            bits.append("balance " + _money_str(balance))
        if out["invoices_added"] or out["invoices_updated"]:
            bits.append("%d invoice(s)" % (out["invoices_added"] + out["invoices_updated"]))
        if out["payments_added"] or out["payments_updated"]:
            bits.append("%d payment(s)" % (out["payments_added"] + out["payments_updated"]))
        summary = "AccuLynx billing synced - " + ", ".join(bits)
        recent = db.all_rows("activities", where="entity_type='job' AND entity_id=? AND text=?",
                             params=(job_id, summary))
        if not recent:
            db.add_activity("job", job_id, "note", summary)
    return out


@bp.route("/billing-import", methods=["POST", "OPTIONS"])
def billing_import():
    """CORS-open: receive a job's billing block (value/invoices/payments) scraped
    from the AccuLynx tab and upsert it. Accepts either a single object keyed by
    `guid`, or a list of such objects. Never 500s."""
    from flask import make_response
    if request.method == "OPTIONS":
        r = make_response("", 204)
        r.headers["Access-Control-Allow-Origin"] = "*"
        r.headers["Access-Control-Allow-Headers"] = "Content-Type"
        r.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        return r
    try:
        body = request.get_json(force=True, silent=True)
        if body is None:
            import json as _json
            body = _json.loads(request.get_data(as_text=True) or "{}")
        items = body if isinstance(body, list) else [body]
        results, agg = [], {"records": 0, "no_job": 0, "value_set": 0,
                            "invoices": 0, "payments": 0}
        for it in items:
            if not isinstance(it, dict):
                continue
            guid = (it.get("guid") or it.get("jobGuid") or it.get("id") or "").strip()
            res = _apply_billing(guid, it)
            results.append(res)
            if not res.get("ok"):
                agg["no_job"] += 1
                continue
            agg["records"] += 1
            agg["value_set"] += 1 if res.get("value_set") else 0
            agg["invoices"] += res.get("invoices_added", 0) + res.get("invoices_updated", 0)
            agg["payments"] += res.get("payments_added", 0) + res.get("payments_updated", 0)
        db.save_company({"acculynx_last_sync": db.now()})
        return _cors({"ok": True, "summary": agg, "results": results[:50]})
    except Exception as e:
        import traceback
        return _cors({"ok": False, "error": "%s: %s" % (type(e).__name__, e),
                      "trace": traceback.format_exc()[-300:]})


# ---- 2) ESTIMATES ----------------------------------------------------------

def _next_estimate_number():
    rows = db.all_rows("estimates", order="id DESC")
    return "EST-%04d" % ((rows[0]["id"] + 1) if rows else 1)


def _apply_estimate(guid, est):
    """Upsert one synced estimate (header-level: name/total/status/date) into the
    estimates table, linked to the matching job/lead. Dedup by ext_id."""
    kind, rec = _record_by_guid(guid)
    if not rec:
        return {"ok": False, "reason": "no_job", "guid": guid}
    ext = str(_g(est, "id", "estimateId", "guid", "Id", default="")).strip()
    name = (_name_of(_g(est, "name", "title", "estimateName", "Name", default=""))
            or rec.get("name") or "AccuLynx Estimate")
    total = _money_num(_g(est, "total", "totalPrice", "amount", "totalAmount",
                          "price", "grandTotal", "Total", default=""))
    status = (_name_of(_g(est, "status", "state", "Status", default="")) or "").lower()
    status = {"approved": "signed", "accepted": "signed", "signed": "signed",
              "sent": "sent", "declined": "declined", "rejected": "declined",
              "draft": "draft", "open": "draft"}.get(re.sub(r"[^a-z]", "", status), "draft")
    edate = _date10(_g(est, "date", "createdOn", "estimateDate", "issuedDate", "Date", default=""))
    num = str(_g(est, "number", "estimateNumber", "Number", default="")).strip()

    link_col = "job_id" if kind == "job" else "lead_id"
    existing = db.all_rows("estimates", where=link_col + "=?", params=(rec["id"],))
    cur = None
    if ext:
        cur = next((e for e in existing if (e.get("ext_id") or "") == ext), None)
    if not cur:
        cur = next((e for e in existing if (e.get("title") or "").lower() == name.lower()
                    and (e.get("source") == "AccuLynx")), None)

    data = {link_col: rec["id"], "contact_id": rec.get("contact_id"),
            "title": name, "number": num or (cur or {}).get("number") or _next_estimate_number(),
            "work_type": rec.get("work_type", ""), "status": status,
            "ext_id": ext, "source": "AccuLynx", "amount_total": _money_str(total),
            "ext_status": status, "ext_date": edate}
    if cur:
        db.update("estimates", cur["id"], **{k: v for k, v in data.items() if k != "number"})
        return {"ok": True, "guid": guid, "action": "updated", "estimate": name,
                "total": _money_str(total), "record": rec.get("name")}
    eid = db.insert("estimates", data)
    db.add_activity(kind, rec["id"], "note",
                    "AccuLynx estimate synced: %s (%s, %s)" % (name, _money_str(total) or "$0", status))
    return {"ok": True, "guid": guid, "action": "added", "estimate": name,
            "total": _money_str(total), "id": eid, "record": rec.get("name")}


@bp.route("/estimate-import", methods=["POST", "OPTIONS"])
def estimate_import():
    """CORS-open: receive estimates scraped from the AccuLynx tab and upsert them.
    Accepts {guid, estimates:[...]} or {guid, ...singleEstimate} or a list. Never 500s."""
    from flask import make_response
    if request.method == "OPTIONS":
        r = make_response("", 204)
        r.headers["Access-Control-Allow-Origin"] = "*"
        r.headers["Access-Control-Allow-Headers"] = "Content-Type"
        r.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        return r
    try:
        body = request.get_json(force=True, silent=True)
        if body is None:
            import json as _json
            body = _json.loads(request.get_data(as_text=True) or "{}")
        items = body if isinstance(body, list) else [body]
        results, agg = [], {"added": 0, "updated": 0, "no_job": 0}
        for it in items:
            if not isinstance(it, dict):
                continue
            guid = (it.get("guid") or it.get("jobGuid") or "").strip()
            ests = it.get("estimates")
            if not isinstance(ests, list):
                ests = [it]
            for est in ests:
                if not isinstance(est, dict):
                    continue
                res = _apply_estimate(guid, est)
                results.append(res)
                if not res.get("ok"):
                    agg["no_job"] += 1
                elif res.get("action") == "added":
                    agg["added"] += 1
                else:
                    agg["updated"] += 1
        db.save_company({"acculynx_last_sync": db.now()})
        return _cors({"ok": True, "summary": agg, "results": results[:50]})
    except Exception as e:
        import traceback
        return _cors({"ok": False, "error": "%s: %s" % (type(e).__name__, e),
                      "trace": traceback.format_exc()[-300:]})


# ---- 3) COMMUNICATIONS (notes / messages) ----------------------------------

_COMM_CAP = 200  # high volume: cap stored messages per job per run


def _apply_comms(guid, payload):
    """Store a job's messages/notes as job activities, deduped by text prefix.
    Caps at _COMM_CAP per run and reports what was capped."""
    kind, rec = _record_by_guid(guid)
    if not rec:
        return {"ok": False, "reason": "no_job", "guid": guid}
    msgs = _g(payload, "messages", "comms", "notes", "items", "Messages", default=[]) or []
    if not isinstance(msgs, list):
        msgs = []
    total = len(msgs)
    capped = max(0, total - _COMM_CAP)
    msgs = msgs[:_COMM_CAP]

    have = {(a.get("text") or "")[:140]
            for a in db.all_rows("activities",
                                 where="entity_type=? AND entity_id=?",
                                 params=(kind, rec["id"]))}
    added = 0
    summary_lines = []
    for m in msgs:
        if not isinstance(m, dict):
            continue
        when = _date10(_g(m, "date", "createdOn", "sentOn", "timestamp", "Date", default=""))
        who = _name_of(_g(m, "fromName", "author", "sender", "createdBy", "userName", "From", default=""))
        mtype = (_name_of(_g(m, "type", "messageType", "channel", "Type", default="")) or "note").lower()
        kindmap = {"email": "email", "sms": "sms", "text": "sms", "call": "call",
                   "phone": "call", "note": "note", "comment": "note", "message": "note"}
        akind = next((v for k, v in kindmap.items() if k in mtype), "note")
        subj = _name_of(_g(m, "subject", "title", "Subject", default=""))
        text = _g(m, "body", "message", "text", "note", "content", "Body", default="")
        text = re.sub(r"<[^>]+>", " ", str(text))
        text = re.sub(r"\s+", " ", text).strip()
        if not (text or subj):
            continue
        line = ("MSG %s%s%s: %s" % (
            when + " " if when else "",
            (who + " ") if who else "",
            ("[" + subj + "]") if subj else "",
            text))[:600]
        if line[:140] in have:
            continue
        db.add_activity(kind, rec["id"], akind, line)
        have.add(line[:140])
        added += 1
        if len(summary_lines) < 30:
            summary_lines.append("- " + line[:200])

    if summary_lines:
        note = "AccuLynx communications (%d shown%s):\n%s" % (
            len(summary_lines), (", %d capped" % capped) if capped else "",
            "\n".join(summary_lines))
        try:
            db.update(kind + "s", rec["id"], narrative=note)
        except Exception:
            pass
    return {"ok": True, "guid": guid, "record": rec.get("name"), "kind": kind,
            "added": added, "scanned": total, "capped": capped}


@bp.route("/comm-import", methods=["POST", "OPTIONS"])
def comm_import():
    """CORS-open: receive a job's messages/notes scraped from AccuLynx and store
    them as activities + narrative. High-volume safe (caps per job). Never 500s."""
    from flask import make_response
    if request.method == "OPTIONS":
        r = make_response("", 204)
        r.headers["Access-Control-Allow-Origin"] = "*"
        r.headers["Access-Control-Allow-Headers"] = "Content-Type"
        r.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        return r
    try:
        body = request.get_json(force=True, silent=True)
        if body is None:
            import json as _json
            body = _json.loads(request.get_data(as_text=True) or "{}")
        items = body if isinstance(body, list) else [body]
        results, agg = [], {"records": 0, "added": 0, "scanned": 0,
                            "capped": 0, "no_job": 0}
        for it in items:
            if not isinstance(it, dict):
                continue
            guid = (it.get("guid") or it.get("jobGuid") or "").strip()
            res = _apply_comms(guid, it)
            results.append(res)
            if not res.get("ok"):
                agg["no_job"] += 1
                continue
            agg["records"] += 1
            agg["added"] += res.get("added", 0)
            agg["scanned"] += res.get("scanned", 0)
            agg["capped"] += res.get("capped", 0)
        db.save_company({"acculynx_last_sync": db.now()})
        return _cors({"ok": True, "summary": agg, "results": results[:50]})
    except Exception as e:
        import traceback
        return _cors({"ok": False, "error": "%s: %s" % (type(e).__name__, e),
                      "trace": traceback.format_exc()[-300:]})


# ---- shared manifest: which GUIDs already have billing -----

@bp.route("/billing-manifest")
def billing_manifest():
    """CORS-open: report whether the synced job already has a value/invoices, so the
    collector can skip cheaply on re-runs."""
    guid = (request.args.get("guid") or "").strip().lower()
    if not guid:
        return _cors({"ok": False, "reason": "missing guid"}, 400)
    kind, rec = _record_by_guid(guid)
    if not rec:
        return _cors({"ok": False, "reason": "no_job", "guid": guid})
    job_id = rec["id"] if kind == "job" else None
    invs = db.all_rows("invoices", where="job_id=?", params=(job_id,)) if job_id else []
    pays = db.all_rows("payments", where="job_id=?", params=(job_id,)) if job_id else []
    valcol = "contract_value" if kind == "job" else "estimate"
    return _cors({"ok": True, "record": rec.get("name"), "kind": kind,
                  "has_value": bool((rec.get(valcol) or "").strip()),
                  "invoices": len(invs), "payments": len(pays)})


# ---- diagnostic: confirm internal endpoints + field mapping live -----------

@bp.route("/internal-test")
def internal_test():
    """Diagnostic page: shows the candidate INTERNAL API endpoints for billing/
    estimates/messages and exactly how the server-side mapping resolves a sample
    object you paste in. The live fetch is same-origin (browser session only) so a
    probe bookmarklet on the page does it; the server can't call my.acculynx.com."""
    sample = request.args.get("sample", "")
    which = request.args.get("which", "billing")
    mapped = None
    if sample:
        import json as _json
        try:
            obj = _json.loads(sample)
        except Exception as e:
            mapped = {"error": "Invalid JSON: %s" % e}
        if mapped is None:
            if which == "estimate":
                mapped = {
                    "name": _name_of(_g(obj, "name", "title", "estimateName", "Name", default="")),
                    "total": _money_str(_money_num(_g(obj, "total", "totalPrice", "amount",
                              "totalAmount", "price", "grandTotal", "Total", default=""))),
                    "status_raw": _g(obj, "status", "state", "Status", default=""),
                    "date": _date10(_g(obj, "date", "createdOn", "estimateDate", "Date", default="")),
                    "keys": list(obj.keys()) if isinstance(obj, dict) else "(not an object)",
                }
            elif which == "comm":
                mapped = {
                    "author": _name_of(_g(obj, "fromName", "author", "sender", "createdBy", "From", default="")),
                    "type": _g(obj, "type", "messageType", "channel", "Type", default=""),
                    "date": _date10(_g(obj, "date", "createdOn", "sentOn", "Date", default="")),
                    "body": re.sub(r"<[^>]+>", " ", str(_g(obj, "body", "message", "text", "note", "Body", default="")))[:200],
                    "keys": list(obj.keys()) if isinstance(obj, dict) else "(not an object)",
                }
            else:
                mapped = {
                    "value": _money_str(_money_num(_g(obj, "value", "jobValue", "contractValue",
                              "totalValue", "jobTotal", "amount", default=""))),
                    "balance": _money_str(_money_num(_g(obj, "balance", "balanceDue", "amountDue", default=""))),
                    "invoices_seen": len(_g(obj, "invoices", "Invoices", default=[]) or []),
                    "payments_seen": len(_g(obj, "payments", "Payments", default=[]) or []),
                    "keys": list(obj.keys()) if isinstance(obj, dict) else "(not an object)",
                }
    return render_template("sync_internal_test.html", which=which, sample=sample,
                           mapped=mapped, host=request.host_url)


# ===========================================================================
# ROOF-REPORT SYNC — pull RoofGraf / roof-report PDFs from AccuLynx job
# DOCUMENTS into the CRM and auto-parse them into measurements (squares, pitch,
# ridge/hip/valley/eave/rake) so they feed estimates.
#
# Same browser-bridge pattern as the permit doc-import: the AccuLynx public API
# can't serve documents (404), so a collector running in the logged-in
# my.acculynx.com tab fetches each job's `/api/v4/job-documents/{guid}/
# job-document-folders`, picks the roof-report file, downloads it, and POSTs the
# bytes here (chunked for big aerial PDFs). We match the CRM job/lead by AccuLynx
# GUID (in external_url), attach the file under Measurements, run it through
# measurements._try_parse, and upsert the measurement record.
#
# "20 at a time": the SERVER walks the active pipeline via the API (GUIDs only —
# no documents) with its own resumable cursor (acculynx_rr_group/_rr_cursor,
# independent of the milestone sync's cursor) and hands the collector the next 20
# GUIDs per click. The collector skips any that already have a roof report
# (idempotent) and reports what it pulled vs. skipped — no silent caps.
# ===========================================================================

# How the collector decides which document is the roof report. Confirmed live on
# SeaBreeze jobs: reports sit in the dedicated "Roof Report" document folder with
# a filename like "Roof-Report-{guid}.pdf"; manually-uploaded ones may instead say
# "RoofGraf"/"EagleView". Separators vary (space, hyphen, underscore), so the
# patterns allow [\s_-] between words. Folder OR file match counts; permit/NOC/
# photo folders don't match.
ROOFREPORT_FOLDER_RE = r"roof[\s_-]*report|measurement|roof[\s_-]*graf|eagleview|roof[\s_-]*measure|aerial"
ROOFREPORT_FILE_RE = r"roof[\s_-]*report|roof[\s_-]*graf|eagleview|roof[\s_-]*measure|premium[\s_-]*roof|measurement"

# Pipeline the office works day-to-day — leads (Assigned) + prospects carry roof
# reports too (Karla uploads at intake), so they're included. Walked newest-first
# with a dedicated cursor. "closed" is the huge historical bucket, so it's walked
# LAST and CAPPED to the most-recent N (see _RR_GROUP_CAP) — the rest of Closed +
# all of Canceled stay skipped.
_RR_GROUPS = ["lead", "prospect", "approved", "completed", "invoiced", "closed"]
_RR_GROUP_CAP = {"closed": 500}   # only the last 500 closed jobs


def _norm_name(s):
    """Normalize a person/job name for fallback matching: strip an 'R-####:'
    prefix and any (PBC)(T28)(SCOTT) tags, fold accents (José→jose), lowercase,
    collapse to alphanumerics + single spaces."""
    import unicodedata
    s = re.sub(r"^\s*R-?\d+\s*:?\s*", "", str(s or ""), flags=re.I)
    s = re.sub(r"\([^)]*\)", " ", s)               # drop (PBC) (T28) (SCOTT) tags
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", s.lower())).strip()


def _roofreport_record(guid, scan_name="", scan_addr=""):
    """Find the CRM job/lead this roof report belongs to. GUID first (robust);
    then an exact normalized-name fallback so records not yet carrying an AccuLynx
    GUID (e.g. a hand-entered lead) still match. Returns (kind, row, how)."""
    kind, rec = _record_by_guid(guid)
    if rec:
        return kind, rec, "guid"
    nm = _norm_name(scan_name)
    if nm and len(nm) >= 4:
        for k, rows in (("job", db.all_rows("jobs")), ("lead", db.all_rows("leads"))):
            hit = next((r for r in rows if _norm_name(r.get("name")) == nm), None)
            if hit:
                return k, hit, "name"
    return None, None, "none"


def _measurement_of(kind, rec_id):
    col = "job_id" if kind == "job" else "lead_id"
    rows = db.all_rows("measurements", where=col + "=?", params=(rec_id,), order="id DESC")
    return rows[0] if rows else None


def _has_roof_report(kind, rec):
    """True if this job/lead already has a parsed roof report on file — used to
    skip on re-runs (idempotent). A measurement row carrying a report_file counts;
    so does a Roof Report document with retrievable bytes."""
    m = _measurement_of(kind, rec["id"])
    if m and (m.get("report_file") or "").strip():
        return True
    col = "job_id" if kind == "job" else "lead_id"
    docs = db.all_rows("documents", where=col + "=?", params=(rec["id"],))
    return any((d.get("category") or "").lower() in ("roof report", "measurement")
               and _doc_has_bytes(d) for d in docs)


def _finalize_roofreport(guid, folder, name, src_path, scan_name="", scan_addr=""):
    """Attach a roof-report PDF (already saved at src_path under MEAS_DIR) to the
    matching CRM job/lead, mirror it to Drive, parse it into a measurement, and
    file it under the record's Documents. Removes src_path on any rejection."""
    size = os.path.getsize(src_path) if os.path.exists(src_path) else 0

    def _drop():
        try:
            os.remove(src_path)
        except Exception:
            pass

    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext and ext not in ALLOWED_DOC_EXT:
        _drop()
        return _cors({"ok": False, "reason": "ext_blocked:%s" % ext, "name": name})
    if size > 64 * 1024 * 1024:
        _drop()
        return _cors({"ok": False, "reason": "too_large", "size": size, "name": name})

    kind, rec, how = _roofreport_record(guid, scan_name, scan_addr)
    if not rec:
        _drop()
        return _cors({"ok": False, "reason": "no_record", "guid": guid, "scan_name": scan_name})

    if _has_roof_report(kind, rec):
        _drop()
        return _cors({"ok": True, "skipped": "already_has_report", "record": rec.get("name"),
                      "kind": kind, "name": name})

    base_fn = os.path.basename(src_path)
    drive_id = _drive_mirror(src_path)

    # --- parse the report into measurement fields ---------------------------
    try:
        from modules import measurements as _meas
        parsed = _meas._try_parse(src_path)
    except Exception:
        parsed = {}

    link_col = "job_id" if kind == "job" else "lead_id"
    mdata = {"report_file": "measurements/" + base_fn, "source": "RoofGraf"}
    mdata.update({k: v for k, v in parsed.items() if v})
    existing = _measurement_of(kind, rec["id"])
    if existing:
        db.update("measurements", existing["id"], **mdata)
        mid = existing["id"]
    else:
        mdata[link_col] = rec["id"]
        mid = db.insert("measurements", mdata)

    # Mirror the headline numbers onto a JOB for quick reference (matches the
    # manual measurement save). Leads carry the measurement row only.
    if kind == "job" and parsed.get("squares"):
        try:
            db.update("jobs", rec["id"], area=str(parsed.get("squares") or ""),
                      slope=parsed.get("pitch") or "")
        except Exception:
            pass

    # --- file it under the record's Documents (dedup by name) ---------------
    same = [d for d in db.all_rows("documents", where=link_col + "=?", params=(rec["id"],))
            if (d.get("original_name") or "").lower() == name.lower()]
    for d in same:
        if not _doc_has_bytes(d):
            db.delete("documents", d["id"])
    if not any(_doc_has_bytes(d) for d in same):
        db.insert("documents", {link_col: rec["id"], "category": "Roof Report",
                                "filename": base_fn, "original_name": name, "size": size,
                                "notes": "RoofGraf report synced from AccuLynx (%s)" % folder,
                                "drive_id": drive_id})

    filled = [k for k in ("squares", "pitch", "ridge_lf", "hip_lf", "valley_lf",
                          "rake_lf", "eave_lf", "step_flash_lf", "facets") if parsed.get(k)]
    msg = "Roof report synced from AccuLynx: %s" % name
    if filled:
        msg += " — auto-filled %d field%s (%.0f sq, pitch %s)" % (
            len(filled), "" if len(filled) == 1 else "s",
            parsed.get("squares") or 0, parsed.get("pitch") or "-")
    else:
        msg += " — attached (auto-parse found no measurements; enter them manually)"
    db.add_activity(kind, rec["id"], "automation", msg)

    return _cors({"ok": True, "added": True, "kind": kind, "record": rec.get("name"),
                  "matched_by": how, "name": name, "measurement_id": mid,
                  "filled": filled, "squares": parsed.get("squares") or 0,
                  "pitch": parsed.get("pitch") or "", "parsed": bool(filled)})


@bp.route("/roofreport-manifest")
def roofreport_manifest():
    """CORS-open: does this AccuLynx GUID's matching CRM job/lead already have a
    roof report? Lets the collector skip it WITHOUT downloading the PDF."""
    guid = (request.args.get("guid") or "").strip().lower()
    name = (request.args.get("name") or "").strip()
    if not guid and not name:
        return _cors({"ok": False, "reason": "missing guid"}, 400)
    kind, rec, how = _roofreport_record(guid, name)
    if not rec:
        return _cors({"ok": True, "in_crm": False, "has_report": False, "guid": guid})
    return _cors({"ok": True, "in_crm": True, "kind": kind, "record": rec.get("name"),
                  "matched_by": how, "has_report": _has_roof_report(kind, rec)})


@bp.route("/roofreport-import", methods=["POST", "OPTIONS"])
def roofreport_import():
    """CORS-open: receive one roof-report PDF scraped from the AccuLynx tab and
    attach + parse it. Matches the CRM job/lead by AccuLynx GUID (then name).
    Supports CHUNKED uploads (uploadId/chunkIndex/chunkTotal) for big aerial PDFs,
    same as doc-import. Finalizes into MEAS_DIR so the measurement 'View report'
    link resolves. Guarded: allow-listed extensions, 64 MB cap, idempotent."""
    from flask import make_response
    if request.method == "OPTIONS":
        r = make_response("", 204)
        r.headers["Access-Control-Allow-Origin"] = "*"
        r.headers["Access-Control-Allow-Headers"] = "Content-Type"
        r.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        return r

    f = request.files.get("file")
    guid = (request.form.get("guid") or "").strip().lower()
    folder = (request.form.get("folder") or "Measurements").strip()[:60]
    name = (request.form.get("filename") or (f.filename if f else "") or "roof_report.pdf").strip()
    scan_name = (request.form.get("name") or "").strip()
    scan_addr = (request.form.get("address") or "").strip()
    upload_id = request.form.get("uploadId")

    if upload_id:  # chunked: append each part, finalize into MEAS_DIR on the last
        uid = re.sub(r"[^A-Za-z0-9_-]", "", upload_id)[:80]
        idx = int(request.form.get("chunkIndex") or 0)
        total = int(request.form.get("chunkTotal") or 1)
        if not uid or f is None:
            return _cors({"ok": False, "reason": "bad_chunk"}, 400)
        cdir = os.path.join(config.MEAS_DIR, "_chunks")
        os.makedirs(cdir, exist_ok=True)
        part = os.path.join(cdir, uid + ".part")
        if idx == 0 and os.path.exists(part):
            os.remove(part)
        with open(part, "ab") as out:
            out.write(f.read())
        if os.path.getsize(part) > 64 * 1024 * 1024:
            os.remove(part)
            return _cors({"ok": False, "reason": "too_large"})
        if idx < total - 1:
            return _cors({"ok": True, "chunk": idx})
        safe = "%d_%s" % (int(time.time() * 1000), _safe_name(name))
        final = os.path.join(config.MEAS_DIR, safe)
        os.replace(part, final)
        return _finalize_roofreport(guid, folder, name, final, scan_name, scan_addr)

    # single-shot (small files)
    if (not guid and not scan_name) or not f:
        return _cors({"ok": False, "reason": "missing guid or file"}, 400)
    safe = "%d_%s" % (int(time.time() * 1000), _safe_name(name))
    path = os.path.join(config.MEAS_DIR, safe)
    f.save(path)
    return _finalize_roofreport(guid, folder, name, path, scan_name, scan_addr)


def _rr_next_batch(base, key, n):
    return _pipeline_next_batch(base, key, n, "acculynx_rr_group", "acculynx_rr_cursor")


def _pipeline_next_batch(base, key, n, gkey, ckey):
    """Walk the active AccuLynx pipeline (GUIDs only) and return the next `n` jobs
    after the saved cursor (gkey/ckey), advancing it. Each item: {guid, name}.
    Resumable across clicks; wraps the cursor to 0 when the pipeline is exhausted."""
    company = db.get_company()
    g = int(company.get(gkey) or 0)
    start = int(company.get(ckey) or 0)
    out = []
    PAGE = 25
    while len(out) < n and g < len(_RR_GROUPS):
        grp = _RR_GROUPS[g]
        cap = _RR_GROUP_CAP.get(grp)               # None = walk the whole group
        if cap is not None and start >= cap:       # capped group fully walked
            g += 1
            start = 0
            continue
        page_size = PAGE if cap is None else max(1, min(PAGE, cap - start))
        data = _api_get(base, "/jobs", key, {
            "milestones": grp, "pageStartIndex": start, "pageSize": page_size,
            "sortBy": "MilestoneDate", "sortOrder": "Descending"})
        items = data.get("items") if isinstance(data, dict) else (data or [])
        if not items:
            g += 1
            start = 0
            continue
        for it in items:
            if len(out) >= n:
                break
            jid = _g(it, "id", "jobId", "uid")
            nm = (_g(it, "jobName", "name", "displayName") or "").strip()
            if jid:
                out.append({"guid": str(jid).lower(), "name": nm, "group": grp})
            start += 1
            if cap is not None and start >= cap:   # hit the cap mid-page
                break
        reached_cap = cap is not None and start >= cap
        if (len(items) < page_size or reached_cap) and len(out) < n:  # group done
            g += 1
            start = 0
    done = g >= len(_RR_GROUPS)
    db.save_company({gkey: 0 if done else g, ckey: 0 if done else start})
    return out, (_RR_GROUPS[g] if not done else "all"), done


@bp.route("/roofreport-batch")
def roofreport_batch():
    """CORS-open: hand the collector the NEXT batch of job GUIDs to pull roof
    reports for, walking the pipeline with a resumable server cursor (20 per
    click by default). Annotates each with whether the matching CRM record already
    has a report, so the collector can skip cheaply. `?reset=1` restarts the walk."""
    company = db.get_company()
    key = (company.get("acculynx_api_key") or "").strip()
    base = (company.get("acculynx_api_base") or DEFAULT_BASE).strip()
    if not key:
        return _cors({"ok": False, "reason": "no_api_key",
                      "error": "Set an AccuLynx API key on the Sync page first."}, 400)
    if request.args.get("reset"):
        db.save_company({"acculynx_rr_group": 0, "acculynx_rr_cursor": 0})
    try:
        n = max(1, min(50, int(request.args.get("n") or 20)))
    except Exception:
        n = 20
    try:
        batch, group, done = _rr_next_batch(base, key, n)
    except Exception as e:
        return _cors({"ok": False, "reason": "api_failed",
                      "error": "%s: %s" % (type(e).__name__, e)})
    have = 0
    for it in batch:
        kind, rec, how = _roofreport_record(it["guid"], it.get("name"))
        it["in_crm"] = bool(rec)
        it["have"] = bool(rec and _has_roof_report(kind, rec))
        if it["have"]:
            have += 1
    return _cors({"ok": True, "batch": batch, "count": len(batch), "group": group,
                  "done": done, "already_have": have})


@bp.route("/roofreport-reset", methods=["POST"])
def roofreport_reset():
    """Reset the roof-report sync cursor to the top of the pipeline."""
    db.save_company({"acculynx_rr_group": 0, "acculynx_rr_cursor": 0})
    flash("Roof-report sync cursor reset — the next batch starts at the top of the pipeline.", "ok")
    return redirect(url_for("sync.index"))


@bp.route("/doc-batch")
def doc_batch():
    """CORS-open: hand the collector the NEXT batch of job GUIDs to pull ALL documents
    for (every folder), walking the pipeline with its OWN resumable cursor (separate
    from the roof-report walk). Annotates each with how many docs the CRM already has
    for that job, so the bookmarklet can show progress. `?reset=1` restarts the walk."""
    company = db.get_company()
    key = (company.get("acculynx_api_key") or "").strip()
    base = (company.get("acculynx_api_base") or DEFAULT_BASE).strip()
    if not key:
        return _cors({"ok": False, "reason": "no_api_key",
                      "error": "Set an AccuLynx API key on the Sync page first."}, 400)
    if request.args.get("reset"):
        db.save_company({"acculynx_doc_group": 0, "acculynx_doc_cursor": 0})
    try:
        n = max(1, min(50, int(request.args.get("n") or 20)))
    except Exception:
        n = 20
    try:
        batch, group, done = _pipeline_next_batch(base, key, n,
                                                   "acculynx_doc_group", "acculynx_doc_cursor")
    except Exception as e:
        return _cors({"ok": False, "reason": "api_failed",
                      "error": "%s: %s" % (type(e).__name__, e)})
    # Annotate each job with the matching CRM job id + how many docs already stored.
    for it in batch:
        kind, rec, how = _roofreport_record(it["guid"], it.get("name"))
        it["in_crm"] = bool(rec)
        if rec and kind == "job":
            try:
                it["have"] = len(db.all_rows("documents", where="job_id=?", params=(rec["id"],)))
            except Exception:
                it["have"] = 0
        else:
            it["have"] = 0
    return _cors({"ok": True, "batch": batch, "count": len(batch), "group": group, "done": done})


@bp.route("/doc-reset", methods=["POST"])
def doc_reset():
    """Reset the all-documents sync cursor to the top of the pipeline."""
    db.save_company({"acculynx_doc_group": 0, "acculynx_doc_cursor": 0})
    flash("Document sync cursor reset — the next batch starts at the top of the pipeline.", "ok")
    return redirect(url_for("sync.index"))
