# -*- coding: utf-8 -*-
"""File uploads — CompanyCam-style photos + per-job documents."""
import os
import re
import time

from flask import Blueprint, request, redirect, url_for, flash

import config
import db

bp = Blueprint("files", __name__, url_prefix="/files")


def _mirror(path, fn):
    """Best-effort: push a just-uploaded file to Google Drive so it persists +
    serves on the cloud (where local disk is ephemeral). No-op if Drive is off."""
    try:
        from modules import gdrive
        if gdrive.enabled():
            return gdrive.mirror(path, fn)
    except Exception:
        pass
    return None


def _safe(name):
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", name or "file")
    return "%d_%s" % (int(time.time() * 1000), base)


@bp.route("/photo/<int:job_id>", methods=["POST"])
def upload_photo(job_id):
    f = request.files.get("file")
    if f and f.filename:
        fn = _safe(f.filename)
        ppath = os.path.join(config.PHOTO_DIR, fn)
        f.save(ppath)
        db.insert("photos", {"job_id": job_id, "album": request.form.get("album", "Job"),
                             "phase": request.form.get("phase", "during"),
                             "caption": request.form.get("caption", ""),
                             "filename": fn, "original_name": f.filename,
                             "drive_id": _mirror(ppath, fn)})
        db.add_activity("job", job_id, "note", "Photo uploaded: %s" % (request.form.get("caption") or f.filename))
        flash("Photo uploaded.", "ok")
    return redirect(url_for("jobs.detail", job_id=job_id))


@bp.route("/doc/<int:job_id>", methods=["POST"])
def upload_doc(job_id):
    f = request.files.get("file")
    if f and f.filename:
        fn = _safe(f.filename)
        path = os.path.join(config.DOC_DIR, fn)
        f.save(path)
        db.insert("documents", {"job_id": job_id, "category": request.form.get("category", "Other"),
                                "filename": fn, "original_name": f.filename,
                                "size": os.path.getsize(path), "notes": request.form.get("notes", ""),
                                "drive_id": _mirror(path, fn)})
        db.add_activity("job", job_id, "note", "Document uploaded: %s (%s)" % (f.filename, request.form.get("category")))
        flash("Document uploaded.", "ok")
    return redirect(url_for("jobs.detail", job_id=job_id))


@bp.route("/doc/<int:doc_id>/request-sign", methods=["POST"])
def request_sign(doc_id):
    """Flag a document so the homeowner is asked to e-sign it in their portal."""
    d = db.get("documents", doc_id)
    if d:
        db.update("documents", doc_id, needs_sign=1, signed_at="", signed_name="")
        if d.get("job_id"):
            db.add_activity("job", d["job_id"], "note", "Requested homeowner signature: %s" % d.get("original_name", ""))
        flash("Signature requested — it now shows in the homeowner portal.", "ok")
    return redirect(request.referrer or url_for("jobs.detail", job_id=d.get("job_id") if d else 0))
