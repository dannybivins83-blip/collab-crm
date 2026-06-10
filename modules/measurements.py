# -*- coding: utf-8 -*-
"""Roof Measurements (RoofGraf / EagleView-style) — squares, pitch, linear footage.

Per the SeaBreeze workflow these come from a RoofGraf report Karla uploads. The
measurement record drives estimate quantities (squares → SQ lines, ridge/hip/
valley/eave/rake → the matching linear-foot lines).
"""
import os
import re
import time

from flask import Blueprint, request, redirect, url_for, flash, jsonify

import config
import db

bp = Blueprint("measurements", __name__, url_prefix="/measurements")

FIELDS = ["squares", "pitch", "stories", "ridge_lf", "hip_lf", "valley_lf",
          "rake_lf", "eave_lf", "step_flash_lf", "facets", "waste_pct", "source", "notes"]
NUMERIC = {"squares", "ridge_lf", "hip_lf", "valley_lf", "rake_lf", "eave_lf",
           "step_flash_lf", "facets", "waste_pct"}


def for_job(job_id):
    rows = db.all_rows("measurements", "job_id=?", (job_id,), "id DESC")
    return rows[0] if rows else None


def for_lead(lead_id):
    rows = db.all_rows("measurements", "lead_id=?", (lead_id,), "id DESC")
    return rows[0] if rows else None


def _coerce(form):
    data = {}
    for f in FIELDS:
        v = form.get(f, "")
        if f in NUMERIC:
            try:
                v = float(re.sub(r"[^0-9.]", "", str(v)) or 0)
            except Exception:
                v = 0
        data[f] = v
    return data


@bp.route("/job/<int:job_id>/save", methods=["POST"])
def save_job(job_id):
    data = _coerce(request.form)
    existing = for_job(job_id)
    if existing:
        db.update("measurements", existing["id"], **data)
        mid = existing["id"]
    else:
        data["job_id"] = job_id
        mid = db.insert("measurements", data)
    # Mirror the headline numbers onto the job for quick reference.
    db.update("jobs", job_id, area=str(data.get("squares") or ""), slope=data.get("pitch") or "")
    db.add_activity("job", job_id, "automation", "Roof measurements saved (%.0f sq, pitch %s)" % (
        data.get("squares") or 0, data.get("pitch") or "—"))
    if request.form.get("ajax"):
        return jsonify({"ok": True, "id": mid})
    flash("Measurements saved.", "ok")
    return redirect(url_for("jobs.detail", job_id=job_id) + "#meas")


@bp.route("/job/<int:job_id>/upload", methods=["POST"])
def upload_job(job_id):
    f = request.files.get("file")
    if not f or not f.filename:
        return redirect(url_for("jobs.detail", job_id=job_id))
    fn = "%d_%s" % (int(time.time() * 1000), re.sub(r"[^A-Za-z0-9._-]+", "_", f.filename))
    path = os.path.join(config.MEAS_DIR, fn)
    f.save(path)
    try:
        from modules import gdrive
        if gdrive.enabled():
            gdrive.mirror(path, fn)
    except Exception:
        pass
    existing = for_job(job_id)
    parsed = _try_parse(path)
    data = {"report_file": "measurements/" + fn, "source": "RoofGraf"}
    data.update({k: v for k, v in parsed.items() if v})
    if existing:
        db.update("measurements", existing["id"], **data)
    else:
        data["job_id"] = job_id
        db.insert("measurements", data)
    # Also file it under job documents for the record.
    db.insert("documents", {"job_id": job_id, "category": "Measurement",
                            "filename": fn, "original_name": f.filename,
                            "size": os.path.getsize(path), "notes": "RoofGraf report"})
    filled = [k for k in ("squares", "pitch", "ridge_lf", "hip_lf", "valley_lf",
                          "rake_lf", "eave_lf", "step_flash_lf", "facets") if parsed.get(k)]
    msg = "Measurement report uploaded"
    if filled:
        msg += " — auto-filled %d field%s (%.0f sq, pitch %s)" % (
            len(filled), "" if len(filled) == 1 else "s",
            parsed.get("squares") or 0, parsed.get("pitch") or "—")
    db.add_activity("job", job_id, "note", msg)
    flash(msg + ".", "ok")
    return redirect(url_for("jobs.detail", job_id=job_id) + "#meas")


@bp.route("/lead/<int:lead_id>/save", methods=["POST"])
def save_lead(lead_id):
    data = _coerce(request.form)
    existing = for_lead(lead_id)
    if existing:
        db.update("measurements", existing["id"], **data)
        mid = existing["id"]
    else:
        data["lead_id"] = lead_id
        mid = db.insert("measurements", data)
    db.add_activity("lead", lead_id, "automation", "Roof measurements saved (%.0f sq, pitch %s)" % (
        data.get("squares") or 0, data.get("pitch") or "—"))
    if request.form.get("ajax"):
        return jsonify({"ok": True, "id": mid})
    flash("Measurements saved.", "ok")
    return redirect(url_for("leads.detail", lead_id=lead_id) + "#meas")


@bp.route("/lead/<int:lead_id>/upload", methods=["POST"])
def upload_lead(lead_id):
    f = request.files.get("file")
    if not f or not f.filename:
        return redirect(url_for("leads.detail", lead_id=lead_id))
    fn = "%d_%s" % (int(time.time() * 1000), re.sub(r"[^A-Za-z0-9._-]+", "_", f.filename))
    path = os.path.join(config.MEAS_DIR, fn)
    f.save(path)
    try:
        from modules import gdrive
        if gdrive.enabled():
            gdrive.mirror(path, fn)
    except Exception:
        pass
    existing = for_lead(lead_id)
    parsed = _try_parse(path)
    data = {"report_file": "measurements/" + fn, "source": "RoofGraf"}
    data.update({k: v for k, v in parsed.items() if v})
    if existing:
        db.update("measurements", existing["id"], **data)
    else:
        data["lead_id"] = lead_id
        db.insert("measurements", data)
    db.insert("documents", {"lead_id": lead_id, "category": "Measurement",
                            "filename": fn, "original_name": f.filename,
                            "size": os.path.getsize(path), "notes": "RoofGraf report"})
    filled = [k for k in ("squares", "pitch", "ridge_lf", "hip_lf", "valley_lf",
                          "rake_lf", "eave_lf", "step_flash_lf", "facets") if parsed.get(k)]
    msg = "Measurement report uploaded"
    if filled:
        msg += " — auto-filled %d field%s (%.0f sq, pitch %s)" % (
            len(filled), "" if len(filled) == 1 else "s",
            parsed.get("squares") or 0, parsed.get("pitch") or "—")
    db.add_activity("lead", lead_id, "note", msg)
    flash(msg + ".", "ok")
    return redirect(url_for("leads.detail", lead_id=lead_id) + "#meas")


def _num(s):
    try:
        return float(re.sub(r"[^0-9.]", "", str(s)) or 0)
    except Exception:
        return 0.0


def _try_parse(path):
    """Auto-extract the full RoofGraf report: squares, pitch, facets, and every
    linear-foot measurement (ridge/hip/valley/rake/eave/step-flashing). Matches the
    real RoofGraf 'Premium Roof Report' layout. Best-effort; user can correct any
    field. Returns {} for anything that isn't a recognizable roof report."""
    out = {}
    try:
        from pypdf import PdfReader
        reader = PdfReader(path)
        txt = "\n".join((pg.extract_text() or "") for pg in reader.pages[:8])
    except Exception:
        return out
    if "roofgraf" not in txt.lower() and "SQ before waste" not in txt \
            and "Total Length" not in txt:
        return out  # not a roof report — never guess

    # Squares / predominant pitch / facets come as a labelled block on the Drawing page:
    #   SQ before waste / Predominant pitch / Facets / Chimneys / Skylights / Other Pen.
    #   13.61 / 5 / 10 / 0 / 0 / 3
    if "SQ before waste" in txt:
        i = txt.find("SQ before waste")
        nums = re.findall(r"-?\d+\.?\d*", txt[i:i + 400])
        if nums:
            out["squares"] = _num(nums[0])
        if len(nums) > 1:
            out["pitch"] = "%d:12" % int(_num(nums[1]))
        if len(nums) > 2:
            out["facets"] = int(_num(nums[2]))

    # Edge & Facet table: each line type sits on its own line, value on the next line.
    #   Ridge / 12.67 ... Hip / 98.58 ... Eave / 156.58 ...
    def edge(field, label):
        m = re.search(r"(?:^|\n)\s*" + label + r"\b[^\S\r\n]*[\r\n]+\s*([\d][\d,]*\.?\d*)",
                      txt, re.I)
        if m:
            v = _num(m.group(1))
            if v:
                out[field] = v

    edge("ridge_lf", r"Ridge")
    edge("hip_lf", r"Hip")
    edge("valley_lf", r"Valley")
    edge("rake_lf", r"Rake")
    edge("eave_lf", r"Eave")
    edge("step_flash_lf", r"Step\s*Flashing")
    return out


# ---------------------------------------------------------------------------
# External ingest: the in-house roof-measurement app POSTs a finished report here
# (HMAC-signed with the shared MEASURE_CRM_WEBHOOK_SECRET — same convention as SSO).
# It matches a CRM lead/job by id, external ref, or address/name; stores the
# measurement (squares/pitch/LF), attaches the PDF, and auto-parses if not provided.
# Contract (JSON or multipart):
#   headers:  X-Signature: hex(hmac_sha256(secret, raw_request_body))
#   body:     {lead_id|job_id|external_ref|address|name, squares, pitch, ridge_lf,...,
#              report_url | pdf_base64 | (multipart file), filename}
# ---------------------------------------------------------------------------
def _ingest_secret():
    import os
    val = os.environ.get("MEASURE_CRM_WEBHOOK_SECRET", "").strip()
    if val:
        return val
    # Fail CLOSED in production: an unset secret must NOT derive the publicly-documented
    # `<tenant>-webhook-secret` fallback — that makes every signature forgeable (audit #2).
    # Returning "" makes _verify_sig reject all requests until the real secret is set.
    import config
    if config.IS_PROD:
        return ""
    try:
        from modules import sso
        return "%s-webhook-secret" % sso._tenant_key()   # dev fallback (matches SSO)
    except Exception:
        return "measure-webhook-secret"


def _verify_sig(raw):
    import hmac
    import hashlib
    secret = _ingest_secret()
    if not secret:
        return False   # no secret configured (prod, unset) -> reject all; fail closed
    sig = (request.headers.get("X-Signature") or "").strip().lower()
    if not sig:
        return False
    expect = hmac.new(secret.encode("utf-8"), raw or b"", hashlib.sha256).hexdigest()
    try:
        return hmac.compare_digest(sig, expect)
    except Exception:
        return False


def _match_record(b):
    if b.get("job_id"):
        j = db.get("jobs", b["job_id"])
        if j:
            return "job", j
    if b.get("lead_id"):
        l = db.get("leads", b["lead_id"])
        if l:
            return "lead", l
    ref = str(b.get("external_ref") or "").strip().lower()
    if ref:
        for j in db.all_rows("jobs"):
            if ref in (j.get("external_url") or "").lower():
                return "job", j
        for l in db.all_rows("leads"):
            if ref in (l.get("external_url") or "").lower():
                return "lead", l
    addr = str(b.get("address") or "").strip().lower()
    name = str(b.get("name") or "").strip().lower()
    if addr or name:
        for j in db.all_rows("jobs"):
            if (addr and addr in (j.get("address") or "").lower()) or (name and name == (j.get("name") or "").lower()):
                return "job", j
        for l in db.all_rows("leads"):
            if (addr and addr in (l.get("address") or "").lower()) or (name and name == (l.get("name") or "").lower()):
                return "lead", l
    return None, None


def _ingest_cors(payload, code=200):
    from flask import make_response
    import json as _json
    r = make_response(_json.dumps(payload), code)
    r.headers["Content-Type"] = "application/json"
    r.headers["Access-Control-Allow-Origin"] = "*"
    r.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Signature"
    r.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    return r


@bp.route("/ingest", methods=["POST", "OPTIONS"])
def ingest():
    if request.method == "OPTIONS":
        return _ingest_cors({"ok": True})
    raw = request.get_data() or b""
    if not _verify_sig(raw):
        return _ingest_cors({"ok": False, "reason": "bad_signature"}, 401)

    pdf_bytes, filename, fields, body = None, "roof-report.pdf", {}, {}
    f = request.files.get("file")
    if f and f.filename:                       # multipart
        pdf_bytes = f.read()
        filename = f.filename
        body = {k: request.form.get(k) for k in request.form}
    else:                                       # JSON
        import json as _json
        try:
            body = _json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            body = {}
        filename = body.get("filename") or filename
        if body.get("pdf_base64"):
            import base64
            try:
                pdf_bytes = base64.b64decode(body["pdf_base64"])
            except Exception:
                pdf_bytes = None
        elif body.get("report_url"):
            import urllib.request
            try:
                with urllib.request.urlopen(body["report_url"], timeout=30) as resp:
                    pdf_bytes = resp.read()
            except Exception:
                pdf_bytes = None

    kind, rec = _match_record(body)
    if not rec:
        return _ingest_cors({"ok": False, "reason": "no_match",
                             "hint": "send lead_id/job_id, external_ref, or address/name"}, 404)

    # Measurement fields explicitly provided by the app.
    for fld in FIELDS:
        if body.get(fld) not in (None, ""):
            v = body.get(fld)
            if fld in NUMERIC:
                try:
                    v = float(re.sub(r"[^0-9.]", "", str(v)) or 0)
                except Exception:
                    v = 0
            fields[fld] = v
    fields.setdefault("source", "Measurement App")

    # Save the PDF (if any), auto-parse to fill gaps, attach as a document.
    report_rel, parsed = None, {}
    if pdf_bytes:
        fn = "%d_%s" % (int(time.time() * 1000), re.sub(r"[^A-Za-z0-9._-]+", "_", filename))
        os.makedirs(config.MEAS_DIR, exist_ok=True)
        path = os.path.join(config.MEAS_DIR, fn)
        with open(path, "wb") as out:
            out.write(pdf_bytes)
        try:
            from modules import gdrive
            if gdrive.enabled():
                gdrive.mirror(path, fn)
        except Exception:
            pass
        report_rel = "measurements/" + fn
        try:
            parsed = _try_parse(path)
        except Exception:
            parsed = {}
        db.insert("documents", {("job_id" if kind == "job" else "lead_id"): rec["id"],
                                "category": "Measurement", "filename": fn,
                                "original_name": filename, "size": len(pdf_bytes),
                                "notes": "Roof report (measurement app)"})

    data = dict(fields)
    for k, v in (parsed or {}).items():        # parsed fills only what the app didn't send
        if v and not data.get(k):
            data[k] = v
    if report_rel:
        data["report_file"] = report_rel
    existing = for_job(rec["id"]) if kind == "job" else for_lead(rec["id"])
    if existing:
        db.update("measurements", existing["id"], **data)
        mid = existing["id"]
    else:
        data[("job_id" if kind == "job" else "lead_id")] = rec["id"]
        mid = db.insert("measurements", data)
    db.add_activity(kind, rec["id"], "note",
                    "Roof report received from the measurement app (%.0f sq, pitch %s)."
                    % (float(data.get("squares") or 0), data.get("pitch") or "—"))
    return _ingest_cors({"ok": True, "matched": kind, "record": rec.get("name"),
                         "id": mid, "squares": data.get("squares")})
