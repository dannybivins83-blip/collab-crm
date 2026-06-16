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
        file_path TEXT,
        created TEXT,
        updated TEXT)""")
except Exception:
    pass
# file_path column (added for retry-without-reupload support).
try:
    db.execute("ALTER TABLE takeoff_jobs ADD COLUMN file_path TEXT")
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
        lead_id_val = rec["id"]
        # Check if this lead already has a job — update it rather than spawning a duplicate.
        existing_jobs = db.all_rows("jobs", "lead_id=?", (lead_id_val,))
        if existing_jobs:
            job_id = existing_jobs[0]["id"]
            db.update("jobs", job_id, **{k: v for k, v in job_fields.items() if v})
        else:
            job_fields["lead_id"] = lead_id_val
            job_fields["name"] = rec.get("name") or job_fields["name"]
            job_fields["address"] = rec.get("address") or job_fields["address"]
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


def _run_takeoff_worker(token, file_path, file_name, lead_id, profile, app, doc_id=None):
    """Background thread: send PDFs to Claude Vision → extract full measurement set → update lead."""

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

        import base64 as _b64
        import anthropic
        import config as _config

        _progress("Loading PDF(s)…")
        content_blocks = []
        MAX_PDF_BYTES = 25 * 1024 * 1024  # 25 MB per PDF (API limit is 32 MB)

        if file_name.lower().endswith(".zip"):
            import zipfile
            try:
                with zipfile.ZipFile(file_path) as zf:
                    all_names = zf.namelist()
            except zipfile.BadZipFile as e:
                raise ValueError("Not a valid ZIP file: %s" % e)
            # Prioritise roof-plan sheets; take up to 5 PDFs total
            def _pdf_rank(n):
                nl = n.lower()
                if "roof" in nl:
                    return 0
                if "cover" in nl or "sheet" in nl or "index" in nl:
                    return 1
                return 2
            pdf_names = sorted(
                [n for n in all_names if n.lower().endswith(".pdf")],
                key=_pdf_rank)[:5]
            if not pdf_names:
                raise ValueError("ZIP contains no PDF files.")
            for pname in pdf_names:
                with zipfile.ZipFile(file_path) as zf:
                    pdf_bytes = zf.read(pname)
                if len(pdf_bytes) > MAX_PDF_BYTES:
                    _progress("Skipping %s — %dMB exceeds limit" % (
                        pname, len(pdf_bytes) // 1048576))
                    continue
                content_blocks.append({
                    "type": "document",
                    "source": {"type": "base64", "media_type": "application/pdf",
                               "data": _b64.standard_b64encode(pdf_bytes).decode()},
                    "title": pname,
                })
                _progress("Loaded %s (%dKB)" % (pname, len(pdf_bytes) // 1024))
        else:
            with open(file_path, "rb") as fh:
                pdf_bytes = fh.read()
            if len(pdf_bytes) > MAX_PDF_BYTES:
                raise ValueError("PDF too large (%dMB). Max 25MB per file." % (
                    len(pdf_bytes) // 1048576))
            content_blocks.append({
                "type": "document",
                "source": {"type": "base64", "media_type": "application/pdf",
                           "data": _b64.standard_b64encode(pdf_bytes).decode()},
            })
            _progress("Loaded PDF (%dKB)" % (len(pdf_bytes) // 1024))

        if not content_blocks:
            raise ValueError("No readable PDFs found in the upload (all exceeded size limit).")

        content_blocks.append({
            "type": "text",
            "text": (
                "You are a roofing estimator reading a plan set or measurement report.\n"
                "Lead: name=%(name)s  address=%(addr)s  work_type=%(wt)s\n\n"
                "Extract every roof measurement from these documents.\n"
                "Look for: Roof Plan sheet, Measurement Summary, Take-Off Summary, "
                "Area Schedule, Roof Framing Plan, or any table with ridge/hip/valley/rake/eave LF.\n\n"
                "UNIT RULES:\n"
                "- Squares = 100 sq ft. If document shows sq ft, divide by 100.\n"
                "- If document shows squares directly, use as-is.\n"
                "- Linear feet (LF) for ridge, hip, valley, rake, eave, flashing.\n"
                "- If a value is shown in a table or dimension callout, use it even if approximate.\n"
                "- Leave a field null only if genuinely absent — do not guess."
            ) % {"name": lead.get("name",""), "addr": lead.get("address",""),
                 "wt": lead.get("work_type","")}
        })

        _progress("Sending to Claude Vision for measurement extraction…")

        client = anthropic.Anthropic(api_key=_config.ANTHROPIC_API_KEY or None)
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2000,
            tools=[{
                "name": "extract_roof_measurements",
                "description": "Extract complete roof measurement set from plan documents",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "total_sq":      {"type": "number", "description": "Steep-slope area in SQUARES (sq ft ÷ 100)"},
                        "flat_sq":       {"type": "number", "description": "Flat/low-slope area in squares"},
                        "ridge_lf":      {"type": "number", "description": "Ridge in linear feet"},
                        "hip_lf":        {"type": "number", "description": "Hip edges in linear feet"},
                        "valley_lf":     {"type": "number", "description": "Valleys in linear feet"},
                        "rake_lf":       {"type": "number", "description": "Rake edges in linear feet"},
                        "eave_lf":       {"type": "number", "description": "Eaves / drip edge in linear feet"},
                        "step_flash_lf": {"type": "number", "description": "Step flashing in linear feet"},
                        "predominant_pitch": {"type": "string", "description": "e.g. 4:12"},
                        "roof_system_type":  {"type": "string", "description": "Shingle / Tile / Metal / Flat/TPO"},
                        "project_name":  {"type": "string"},
                        "address_line1": {"type": "string"},
                        "city":          {"type": "string"},
                        "state":         {"type": "string"},
                        "zip":           {"type": "string"},
                        "owner_name":    {"type": "string"},
                        "architect_firm":{"type": "string"},
                        "engineer_firm": {"type": "string"},
                        "wind_speed_mph":{"type": "number", "description": "Ultimate design wind speed mph"},
                        "asce_version":  {"type": "string", "description": "e.g. ASCE 7-22"},
                        "risk_category": {"type": "string", "description": "e.g. II"},
                        "exposure_category": {"type": "string", "description": "e.g. C"},
                        "plan_set_label":{"type": "string"},
                    },
                    "required": []
                }
            }],
            tool_choice={"type": "tool", "name": "extract_roof_measurements"},
            messages=[{"role": "user", "content": content_blocks}]
        )

        fields = {}
        for block in resp.content:
            if block.type == "tool_use" and block.name == "extract_roof_measurements":
                fields = block.input or {}
                break

        _progress("AI extraction complete — updating lead…")

        rtype = (fields.get("roof_system_type") or lead.get("work_type") or "").lower()
        if "tile" in rtype:
            work_type = "Roofing - Tile"
        elif "metal" in rtype:
            work_type = "Roofing - Metal (Galvalume)"
        elif any(x in rtype for x in ("flat", "tpo", "mod", "built")):
            work_type = "Roofing - Flat (TPO)"
        else:
            work_type = lead.get("work_type") or "Roofing - Shingle"

        total_sq = fields.get("total_sq")
        warnings = []
        if not total_sq:
            warnings.append("Square footage not found in plans — enter measurements manually. "
                            "Plans may be image-based (scanned) with no text layer.")
        if not fields.get("wind_speed_mph"):
            warnings.append("Wind speed not found — verify against cover sheet.")

        with app.app_context():
            # Update the lead with non-invasive fields only — never rename or convert.
            lead_upd = {}
            if total_sq and not lead.get("area"):
                lead_upd["area"] = str(total_sq)
            if work_type and not lead.get("work_type"):
                lead_upd["work_type"] = work_type
            if fields.get("architect_firm") and not lead.get("architect_firm"):
                lead_upd["architect_firm"] = fields["architect_firm"]
            if fields.get("engineer_firm") and not lead.get("engineer_firm"):
                lead_upd["engineer_firm"] = fields["engineer_firm"]
            if lead_upd:
                db.update("leads", lead_id, **lead_upd)

            # Check if this lead has already been converted to a job.
            target_job_id = None
            existing_jobs = db.all_rows("jobs", "lead_id=?", (lead_id,))
            if existing_jobs:
                target_job_id = existing_jobs[0]["id"]
                job_upd = {k: v for k, v in {
                    "wind_speed_mph": str(fields["wind_speed_mph"]) if fields.get("wind_speed_mph") else "",
                    "asce_version": fields.get("asce_version") or "",
                    "risk_category": fields.get("risk_category") or "",
                    "exposure": fields.get("exposure_category") or "",
                    "architect_firm": fields.get("architect_firm") or "",
                    "engineer_firm": fields.get("engineer_firm") or "",
                    "plan_set_label": fields.get("plan_set_label") or "",
                }.items() if v}
                if job_upd:
                    db.update("jobs", target_job_id, **job_upd)
                if doc_id:
                    try:
                        db.execute("UPDATE documents SET job_id=? WHERE id=?",
                                   (target_job_id, doc_id))
                    except Exception:
                        pass

            # Insert measurement row with all extracted LF values.
            meas_id = None
            if total_sq or any(fields.get(k) for k in (
                    "ridge_lf","hip_lf","valley_lf","rake_lf","eave_lf","step_flash_lf")):
                mdata = {
                    "source": "Plans Upload",
                    "squares": total_sq or 0,
                    "pitch": fields.get("predominant_pitch") or "",
                    "notes": fields.get("plan_set_label") or file_name,
                    "ridge_lf":      fields.get("ridge_lf") or 0,
                    "hip_lf":        fields.get("hip_lf") or 0,
                    "valley_lf":     fields.get("valley_lf") or 0,
                    "rake_lf":       fields.get("rake_lf") or 0,
                    "eave_lf":       fields.get("eave_lf") or 0,
                    "step_flash_lf": fields.get("step_flash_lf") or 0,
                }
                if target_job_id:
                    mdata["job_id"] = target_job_id
                else:
                    try:
                        db.execute("ALTER TABLE measurements ADD COLUMN lead_id INTEGER")
                        db._COLCACHE.clear()
                    except Exception:
                        pass
                    mdata["lead_id"] = lead_id
                try:
                    meas_id = db.insert("measurements", mdata)
                except Exception:
                    pass

            # Activity note on the lead.
            note_parts = ["Plans uploaded: %s" % (fields.get("plan_set_label") or file_name)]
            note_parts.append("System: %s" % work_type)
            if fields.get("wind_speed_mph"):
                note_parts.append("Wind: %s mph %s" % (
                    fields["wind_speed_mph"], fields.get("asce_version") or ""))
            meas_summary = []
            if total_sq:
                meas_summary.append("%.2f sq" % total_sq)
            for lbl, key in [("ridge","ridge_lf"),("hip","hip_lf"),("valley","valley_lf"),
                              ("rake","rake_lf"),("eave","eave_lf"),("step flash","step_flash_lf")]:
                if fields.get(key):
                    meas_summary.append("%s %.0f LF" % (lbl, fields[key]))
            if meas_summary:
                note_parts.append(" · ".join(meas_summary))
            else:
                note_parts.append("⚠ no measurements found")
            if warnings:
                note_parts += warnings
            db.add_activity("lead", lead_id, "note", " | ".join(note_parts))

            lf_summary = {k: fields.get(k) for k in
                          ("ridge_lf","hip_lf","valley_lf","rake_lf","eave_lf","step_flash_lf")
                          if fields.get(k)}
            result = {
                "ok": True,
                "lead_id": lead_id,
                "job_id": target_job_id,
                "measurement_id": meas_id,
                "squares": total_sq,
                "lf": lf_summary,
                "work_type": work_type,
                "warnings": warnings,
            }

        meas_line = ("%.2f sq" % total_sq) if total_sq else "⚠ sq not found"
        if lf_summary:
            meas_line += " · " + " · ".join(
                "%s %.0fLF" % (k.replace("_lf",""), v) for k, v in lf_summary.items())
        _progress("Done! %s" % meas_line)
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
