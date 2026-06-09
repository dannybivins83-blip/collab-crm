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


def _refresh_name(kind, entity_id, squares):
    """Recompose the SeaBreeze standardized name now that squares are known
    (RoofCode picks up the squares -> e.g. S17). Best-effort; never blocks a save."""
    try:
        import theme
        if kind == "lead":
            theme.refresh_lead_name(entity_id, squares=squares)
        else:
            theme.refresh_job_name(entity_id, squares=squares)
    except Exception:
        pass


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
    _refresh_name("job", job_id, data.get("squares"))
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
    _refresh_name("job", job_id, parsed.get("squares"))
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
    _refresh_name("lead", lead_id, data.get("squares"))
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
    _refresh_name("lead", lead_id, parsed.get("squares"))
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
