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
    """Persist a just-uploaded file to R2 (preferred) or Drive (fallback).
    Returns the storage key/id on success, or None."""
    try:
        from modules import r2, gdrive
        if r2.enabled():
            key = r2.mirror(path, fn)
            if key:
                return key
        return gdrive.mirror(path, fn)
    except Exception:
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


@bp.route("/doc/<int:doc_id>/update", methods=["POST"])
def update_doc(doc_id):
    d = db.get("documents", doc_id)
    if not d:
        flash("Document not found.", "err")
        return redirect(request.referrer or "/")
    name = (request.form.get("original_name") or "").strip() or d.get("original_name", "")
    cat = (request.form.get("category") or "").strip() or d.get("category", "Other")
    db.update("documents", doc_id, original_name=name, category=cat)
    if d.get("job_id"):
        db.add_activity("job", d["job_id"], "note",
                        "Document updated: %s → %s (%s)" % (d.get("original_name", ""), name, cat))
    flash("Document updated.", "ok")
    return redirect(request.referrer or url_for("jobs.detail", job_id=d.get("job_id") or 0))


@bp.route("/doc/<int:doc_id>/delete", methods=["POST"])
def delete_doc(doc_id):
    d = db.get("documents", doc_id)
    if not d:
        flash("Document not found.", "err")
        return redirect(request.referrer or "/")
    job_id = d.get("job_id")
    # Remove file from disk if present
    try:
        fpath = os.path.join(config.DOC_DIR, d.get("filename", ""))
        if d.get("filename") and os.path.exists(fpath):
            os.remove(fpath)
    except Exception:
        pass
    db.delete("documents", doc_id)
    if job_id:
        db.add_activity("job", job_id, "note", "Document deleted: %s" % d.get("original_name", ""))
    flash("Document deleted.", "ok")
    return redirect(request.referrer or url_for("jobs.detail", job_id=job_id or 0))


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
