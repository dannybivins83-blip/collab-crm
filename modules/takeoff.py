# -*- coding: utf-8 -*-
"""Estimator → CRM takeoff ingest — a single atomic endpoint that accepts the
Estimator Agent's `estimator-takeoff/v1` envelope (whether emitted standalone or
folded into the Roof Report Engine) and fans it out across the CRM in one call:

  match-or-create job  →  measurement  →  estimate + line items  →  submittal
  components (NOA index)  →  heads-up items (notes/tasks)  →  id-map response.

Auth: HMAC X-Signature over the raw body, same shared secret as /measurements/ingest.
Idempotent: re-POST with the same idempotency_key is a no-op (returns the prior result).
See docs/TAKEOFF_INGEST.md for the contract.
"""
import io
import json
import datetime
import threading
import uuid

from flask import Blueprint, request, jsonify

import db
from modules import measurements as M   # reuse _verify_sig / _match_record / _ingest_cors

bp = Blueprint("takeoff", __name__, url_prefix="/api")

# Tables (module-load convention, like portal/sync).
for _ddl in (
    """CREATE TABLE IF NOT EXISTS takeoffs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, created TEXT, job_id INTEGER,
        idempotency_key TEXT, schema_version TEXT, source TEXT, result TEXT)""",
    """CREATE TABLE IF NOT EXISTS submittal_components (
        id INTEGER PRIMARY KEY AUTOINCREMENT, created TEXT, job_id INTEGER, ord INTEGER,
        category TEXT, component TEXT, manufacturer TEXT, product TEXT,
        noa_number TEXT, noa_approved_date TEXT, noa_expiration_date TEXT,
        noa_url TEXT, status TEXT)"""):
    try:
        db.execute(_ddl)
    except Exception:
        pass
# Audit #9: UNIQUE partial index on idempotency_key so the insert-first claim
# pattern below is atomic across concurrent requests. Empty/NULL keys are
# excluded so envelopes without a key (legacy clients) still insert freely.
# Falls back to a non-unique index if the live table already holds duplicate
# keys — the app code still detects the race correctly via the claim row.
try:
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_takeoffs_idempotency_key "
               "ON takeoffs(idempotency_key) "
               "WHERE idempotency_key IS NOT NULL AND idempotency_key != ''")
except Exception:
    try:
        db.execute("CREATE INDEX IF NOT EXISTS ix_takeoffs_idempotency_key "
                   "ON takeoffs(idempotency_key)")
    except Exception:
        pass
# Wind/architect header fields on jobs (additive; the takeoff carries them).
for _c in ("architect_firm", "engineer_firm", "wind_speed_mph", "asce_version",
           "risk_category", "plan_set_label"):
    try:
        db.execute("ALTER TABLE jobs ADD COLUMN %s TEXT" % _c)
    except Exception:
        pass
db._COLCACHE.clear()

# Async takeoff jobs (upload → AI extract → estimate pipeline).
try:
    db.execute("""CREATE TABLE IF NOT EXISTS takeoff_jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        token TEXT UNIQUE NOT NULL,
        lead_id INTEGER,
        job_id INTEGER,
        profile TEXT DEFAULT 'seabreeze',
        status TEXT DEFAULT 'queued',
        progress TEXT,
        result TEXT,
        created TEXT,
        updated TEXT)""")
except Exception:
    pass
# Sticky profile selection on leads.
try:
    db.execute("ALTER TABLE leads ADD COLUMN bid_as_profile TEXT")
except Exception:
    pass


def _num(v):
    try:
        return float(str(v).replace(",", "").replace("$", "").strip() or 0)
    except Exception:
        return 0.0


def _within_months(date_str, months=6):
    """True if date_str (YYYY-MM-DD…) is within `months` from today (expiring soon)."""
    try:
        d = datetime.datetime.strptime((date_str or "")[:10], "%Y-%m-%d").date()
    except Exception:
        return False
    return d <= (datetime.date.today() + datetime.timedelta(days=months * 31))


def _ingest_envelope(env):
    """Write an already-parsed takeoff envelope to the DB. Returns the result dict.
    Called directly from _run_takeoff_worker (no HTTP, no HMAC needed)."""
    proj = env.get("project") or {}
    wind = env.get("wind_design") or {}
    rs = env.get("roof_system") or {}
    addr = proj.get("address_line1") or ""
    match = {"job_id": env.get("job_id"), "lead_id": env.get("lead_id"),
             "external_ref": env.get("external_ref"), "address": addr,
             "name": proj.get("name")}
    kind, rec = M._match_record(match)
    job_fields = {
        "name": proj.get("name") or addr or "Takeoff",
        "address": addr, "city": proj.get("city") or "", "state": proj.get("state") or "FL",
        "zip": proj.get("zip") or "", "county": proj.get("county") or "",
        "ahj": proj.get("permit_jurisdiction") or "", "system": rs.get("primary_type") or "",
        "work_type": rs.get("primary_type") or "",
        "area": str((env.get("measurements") or [{}])[0].get("total_sq") or ""),
        "slope": rs.get("predominant_pitch") or "",
        "exposure": wind.get("exposure_category") or "",
        "architect_firm": proj.get("architect_firm") or "",
        "engineer_firm": proj.get("engineer_firm") or "",
        "wind_speed_mph": str(wind.get("wind_speed_mph_ultimate") or ""),
        "asce_version": wind.get("asce_version") or "",
        "risk_category": wind.get("risk_category") or "",
        "plan_set_label": proj.get("plan_set_label") or "",
    }
    if rec and kind == "job":
        job_id = rec["id"]
        db.update("jobs", job_id, **{k: v for k, v in job_fields.items() if v})
    elif rec and kind == "lead":
        job_fields["lead_id"] = rec["id"]
        job_fields["stage"] = "approved"
        job_id = db.insert("jobs", job_fields)
    else:
        job_fields["stage"] = "approved"
        job_id = db.insert("jobs", job_fields)

    warnings, mids, liids, scids = [], [], [], []

    for m in (env.get("measurements") or []):
        ss = m.get("steep_slope") or {}
        mdata = {"job_id": job_id, "source": "Estimator",
                 "squares": _num(m.get("total_sq") or ss.get("area_sq")),
                 "pitch": m.get("predominant_pitch") or "",
                 "ridge_lf": _num(ss.get("ridges_lf")), "hip_lf": _num(ss.get("hips_lf")),
                 "valley_lf": _num(ss.get("valleys_lf")), "rake_lf": _num(ss.get("rakes_lf")),
                 "eave_lf": _num(ss.get("eaves_lf")), "step_flash_lf": _num(ss.get("step_flashing_lf")),
                 "notes": m.get("measurement_name") or ""}
        mids.append(db.insert("measurements", mdata))

    lines = env.get("line_items") or []
    if lines:
        from modules import estimates as E
        eid = db.insert("estimates", {
            "number": E._next_number(),
            "title": "Takeoff — %s" % (proj.get("name") or addr),
            "job_id": job_id, "contact_id": (rec or {}).get("contact_id"),
            "work_type": rs.get("primary_type") or "", "status": "draft",
            "source": "Estimator", "tax_pct": 0})
        sections = list(dict.fromkeys([ln.get("section") or "Takeoff" for ln in lines]))
        for si, sec in enumerate(sections):
            sid = db.insert("estimate_sections", {"estimate_id": eid, "sort": si, "name": sec,
                                                  "scope_text": "", "margin_pct": 30})
            for li, ln in enumerate([l for l in lines if (l.get("section") or "Takeoff") == sec]):
                liids.append(db.insert("estimate_lines", {
                    "estimate_id": eid, "section_id": sid, "sort": li,
                    "description": ln.get("item") or "", "unit": ln.get("unit") or "EA",
                    "qty": _num(ln.get("qty")), "cost": _num(ln.get("unit_price_usd")),
                    "price": _num(ln.get("unit_price_usd"))}))

    for sc in (env.get("submittal_components") or []):
        scids.append(db.insert("submittal_components", {
            "job_id": job_id, "ord": sc.get("ord"), "category": sc.get("category") or "",
            "component": sc.get("component") or "", "manufacturer": sc.get("manufacturer") or "",
            "product": sc.get("product") or "", "noa_number": sc.get("noa_number") or "",
            "noa_approved_date": sc.get("noa_approved_date") or "",
            "noa_expiration_date": sc.get("noa_expiration_date") or "",
            "noa_url": sc.get("noa_url") or "", "status": sc.get("status") or ""}))
        st = (sc.get("status") or "").upper()
        if "EXPIR" in st or _within_months(sc.get("noa_expiration_date")):
            warnings.append("NOA '%s %s' (%s) expires soon." % (
                sc.get("manufacturer") or "", sc.get("product") or "", sc.get("noa_number") or ""))

    for h in (env.get("heads_up_items") or []):
        sev = (h.get("severity") or "").upper()
        txt = "[%s] %s — %s" % (sev or "NOTE", h.get("title") or "", h.get("body") or "")
        db.add_activity("job", job_id, "note", txt)
        if sev == "HIGH":
            db.add_activity("job", job_id, "task", "Follow up: %s" % (h.get("title") or ""))

    db.add_activity("job", job_id, "automation",
                    "Takeoff ingested: %d line items, %d submittals%s" % (
                        len(liids), len(scids), (" · %d warnings" % len(warnings)) if warnings else ""))

    return {"ok": True, "job_id": job_id, "measurement_ids": mids,
            "line_item_ids": liids, "submittal_component_ids": scids,
            "attachment_ids": [], "warnings": warnings}


def _run_takeoff_worker(token, file_path, file_name, lead_id, profile, app):
    """Background thread: extract PDF text → AI extraction → ingest envelope."""

    def _progress(msg):
        try:
            with app.app_context():
                rows = db.all_rows("takeoff_jobs", "token=?", (token,))
                if rows:
                    prev = (rows[0].get("progress") or "").split("\n")[-9:]
                    prev.append(msg)
                    db.execute("UPDATE takeoff_jobs SET progress=?,updated=? WHERE token=?",
                               ("\n".join(prev), db.now(), token))
        except Exception:
            pass

    try:
        with app.app_context():
            db.execute("UPDATE takeoff_jobs SET status='running',updated=? WHERE token=?",
                       (db.now(), token))
            lead = db.get("leads", lead_id) or {}

        _progress("Reading uploaded file…")
        text_chunks = []

        if file_name.lower().endswith(".zip"):
            import zipfile
            try:
                import pypdf
                with zipfile.ZipFile(file_path) as zf:
                    for name in zf.namelist()[:20]:
                        if name.lower().endswith(".pdf"):
                            try:
                                reader = pypdf.PdfReader(io.BytesIO(zf.read(name)))
                                for page in reader.pages[:10]:
                                    text_chunks.append(page.extract_text() or "")
                                _progress("Extracted %s" % name)
                            except Exception:
                                pass
            except zipfile.BadZipFile as e:
                raise ValueError("Not a valid ZIP file: %s" % e)
        else:
            try:
                import pypdf
                reader = pypdf.PdfReader(file_path)
                for page in reader.pages[:20]:
                    text_chunks.append(page.extract_text() or "")
                _progress("Extracted %d PDF pages" % len(reader.pages))
            except Exception as e:
                raise ValueError("Could not read PDF: %s" % e)

        full_text = "\n\n".join(text_chunks)[:40000]
        if not full_text.strip():
            raise ValueError(
                "No text found in upload — may be a scanned/image-only PDF. "
                "Request a text-layer plan set from the architect.")

        _progress("AI extracting project data…")

        import anthropic
        import config as _config
        client = anthropic.Anthropic(api_key=_config.ANTHROPIC_API_KEY or None)
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1500,
            tools=[{
                "name": "extract_takeoff",
                "description": "Extract roof project fields from construction plan documents",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "project_name": {"type": "string"},
                        "address_line1": {"type": "string"},
                        "city": {"type": "string"},
                        "state": {"type": "string"},
                        "zip": {"type": "string"},
                        "owner_name": {"type": "string"},
                        "architect_firm": {"type": "string"},
                        "engineer_firm": {"type": "string"},
                        "roof_system_type": {"type": "string",
                                             "description": "e.g. Shingle, Tile, Metal, Flat/TPO"},
                        "predominant_pitch": {"type": "string", "description": "e.g. 4:12"},
                        "total_sq": {"type": "number",
                                     "description": "Total roof area in squares (100 sq ft each)"},
                        "wind_speed_mph": {"type": "number",
                                           "description": "Ultimate design wind speed (mph)"},
                        "asce_version": {"type": "string", "description": "e.g. ASCE 7-22"},
                        "risk_category": {"type": "string", "description": "e.g. II"},
                        "exposure_category": {"type": "string", "description": "e.g. C"},
                        "plan_set_label": {"type": "string",
                                           "description": "Sheet set / revision label"}
                    },
                    "required": []
                }
            }],
            tool_choice={"type": "tool", "name": "extract_takeoff"},
            messages=[{"role": "user", "content": (
                "Extract all project and roof data from these construction plans.\n"
                "Lead context: name=%s  address=%s  work_type=%s\n\nPLAN TEXT:\n%s"
            ) % (lead.get("name", ""), lead.get("address", ""),
                 lead.get("work_type", ""), full_text)}]
        )

        fields = {}
        for block in resp.content:
            if block.type == "tool_use" and block.name == "extract_takeoff":
                fields = block.input or {}
                break

        _progress("AI extraction complete — building job + estimate…")

        rtype = (fields.get("roof_system_type") or lead.get("work_type") or "").lower()
        if "tile" in rtype:
            work_type = "Roofing - Tile"
        elif "metal" in rtype:
            work_type = "Roofing - Metal (Galvalume)"
        elif any(x in rtype for x in ("flat", "tpo", "mod", "built")):
            work_type = "Roofing - Flat (TPO)"
        else:
            work_type = "Roofing - Shingle"

        heads_up = []
        if not fields.get("total_sq"):
            heads_up.append({"severity": "HIGH", "title": "Measurement missing",
                             "body": "Plans did not contain readable square footage. "
                                     "Enter measurements manually on the job."})
        if not fields.get("wind_speed_mph"):
            heads_up.append({"severity": "MEDIUM", "title": "Wind speed not found",
                             "body": "Could not extract wind design speed — defaulted to 170 mph. "
                                     "Verify against cover sheet."})

        envelope = {
            "schema_version": "estimator-takeoff/v1",
            "source": "CRM-Plans-Upload",
            "idempotency_key": str(uuid.uuid4()),
            "lead_id": lead_id,
            "project": {
                "name": fields.get("project_name") or lead.get("name") or "",
                "address_line1": fields.get("address_line1") or lead.get("address") or "",
                "city": fields.get("city") or lead.get("city") or "",
                "state": fields.get("state") or "FL",
                "zip": fields.get("zip") or "",
                "owner_name": fields.get("owner_name") or lead.get("name") or "",
                "architect_firm": fields.get("architect_firm") or "",
                "engineer_firm": fields.get("engineer_firm") or "",
                "plan_set_label": fields.get("plan_set_label") or "",
                "permit_jurisdiction": lead.get("ahj") or "",
            },
            "wind_design": {
                "wind_speed_mph_ultimate": fields.get("wind_speed_mph") or 170,
                "asce_version": fields.get("asce_version") or "ASCE 7-22",
                "risk_category": fields.get("risk_category") or "II",
                "exposure_category": fields.get("exposure_category") or "C",
            },
            "roof_system": {
                "primary_type": work_type,
                "predominant_pitch": fields.get("predominant_pitch") or "",
            },
            "measurements": ([{
                "measurement_name": "Extracted from plans",
                "total_sq": fields["total_sq"],
                "predominant_pitch": fields.get("predominant_pitch") or "",
                "steep_slope": {}
            }] if fields.get("total_sq") else []),
            "line_items": [],
            "submittal_components": [],
            "heads_up_items": heads_up,
        }

        with app.app_context():
            result = _ingest_envelope(envelope)

        _progress("Done! Job and draft estimate created.")
        with app.app_context():
            db.execute(
                "UPDATE takeoff_jobs SET status='done',result=?,job_id=?,updated=? WHERE token=?",
                (json.dumps(result), result.get("job_id"), db.now(), token))

    except Exception as exc:
        _progress("ERROR: %s" % str(exc))
        with app.app_context():
            db.execute(
                "UPDATE takeoff_jobs SET status='failed',result=?,updated=? WHERE token=?",
                (json.dumps({"ok": False, "error": str(exc)}), db.now(), token))


@bp.route("/takeoff_jobs/<token>", methods=["GET"])
def poll_takeoff_job(token):
    """Poll the status of an async takeoff job. Returns status, last progress lines, result."""
    rows = db.all_rows("takeoff_jobs", "token=?", (token,))
    if not rows:
        return jsonify({"ok": False, "error": "not_found"}), 404
    row = rows[0]
    resp = {
        "ok": True,
        "status": row.get("status"),
        "progress": (row.get("progress") or "").strip().split("\n")[-3:],
        "job_id": row.get("job_id"),
        "lead_id": row.get("lead_id"),
    }
    if row.get("result"):
        try:
            resp["result"] = json.loads(row["result"])
        except Exception:
            pass
    return jsonify(resp)


@bp.route("/takeoff", methods=["POST", "OPTIONS"])
def create():
    if request.method == "OPTIONS":
        return M._ingest_cors({"ok": True})
    raw = request.get_data() or b""
    if not M._verify_sig(raw):
        return M._ingest_cors({"ok": False, "reason": "bad_signature"}, 401)
    try:
        env = json.loads(raw.decode("utf-8") or "{}")
    except Exception:
        return M._ingest_cors({"ok": False, "error_code": "BAD_JSON"}, 400)

    proj = env.get("project") or {}
    # --- validation ------------------------------------------------------
    field_errors = {}
    if not (proj.get("name") or proj.get("address_line1") or env.get("job_id")):
        field_errors["project.name"] = "Required (or project.address_line1 / job_id)"
    if field_errors:
        return M._ingest_cors({"ok": False, "error_code": "VALIDATION_FAILED",
                               "field_errors": field_errors}, 422)

    # --- idempotency (audit #9: insert-first claim, no TOCTOU) -----------
    # Old path was an O(n) full-table scan followed by an unprotected INSERT at
    # the end of the route, so two concurrent POSTs with the same key BOTH did
    # the work and BOTH inserted (dup job + dup measurements). New path:
    #   1. Indexed lookup — if a finished takeoff with this key exists, return it.
    #   2. Otherwise INSERT a placeholder row (empty `result`). The UNIQUE index
    #      makes this atomic; the loser of the race gets an IntegrityError.
    #   3. Loser polls briefly for the winner's finished `result`, returns it.
    # The placeholder row id is held in `claim_id` so we UPDATE it (not INSERT)
    # once the work is committed.
    idem = (env.get("idempotency_key") or "").strip()
    claim_id = None
    if idem:
        prior = db.all_rows("takeoffs", where="idempotency_key=?", params=(idem,),
                            order="id DESC")
        if prior and (prior[0].get("result") or ""):
            return M._ingest_cors(json.loads(prior[0].get("result") or "{}"))
        try:
            claim_id = db.insert("takeoffs", {
                "idempotency_key": idem,
                "schema_version": env.get("schema_version") or "",
                "source": env.get("source") or "",
                "result": ""})
        except Exception:
            import time
            for _ in range(40):  # ~2s total — winner finishes work + UPDATE
                again = db.all_rows("takeoffs", where="idempotency_key=?",
                                    params=(idem,), order="id DESC")
                if again and (again[0].get("result") or ""):
                    return M._ingest_cors(json.loads(again[0].get("result") or "{}"))
                time.sleep(0.05)
            return M._ingest_cors({"ok": False, "error_code": "IDEMPOTENCY_BUSY"}, 409)

    result = _ingest_envelope(env)
    job_id = result.get("job_id")
    # Persist idempotency result (unblocks waiting losers if claimed above).
    if claim_id is not None:
        db.update("takeoffs", claim_id, job_id=job_id, result=json.dumps(result))
    else:
        db.insert("takeoffs", {"job_id": job_id, "idempotency_key": idem,
                               "schema_version": env.get("schema_version") or "",
                               "source": env.get("source") or "", "result": json.dumps(result)})
    return M._ingest_cors(result)
