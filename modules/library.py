# -*- coding: utf-8 -*-
"""Company Document Library — reusable SeaBreeze docs (warranties, product/color
charts, per-AHJ permit packages, licenses/COI, sign-up packages, cheat sheets).
Browse/search, attach to a job, or upload more. Contextual helpers surface the
right docs by AHJ (permit packages) and system (sign-up packages / warranties)."""
import os
import re
import time
import shutil

from flask import Blueprint, render_template, request, redirect, url_for, flash

import config
import db
import theme as _theme

bp = Blueprint("library", __name__, url_prefix="/library")

LIB_DIR = os.path.join(config.UPLOAD_DIR, "library")
os.makedirs(LIB_DIR, exist_ok=True)

CATEGORY_ORDER = [
    "Licenses & Insurance", "Warranties", "Permit Packages", "Sign-Up Packages",
    "Product & Color Charts", "Order Forms & Cheat Sheets", "HOA Forms", "NOC Forms",
    "Lien & Legal Forms", "Sample Proposals", "My Safe Florida Home",
    "Inspection Reports", "Company / Misc",
]


def _pretty(name):
    base = os.path.splitext(name)[0]
    return re.sub(r"[_\-]+", " ", base).strip().title()


@bp.route("/")
def index():
    q = (request.args.get("q") or "").strip().lower()
    cat = request.args.get("cat") or ""
    all_docs = db.all_rows("library_docs", order="original_name")
    for r in all_docs:
        r["_pretty"] = _pretty(r["original_name"])
        r["_ext"] = os.path.splitext(r["original_name"])[1].lstrip(".").upper()
    # Compute counts + total from the unfiltered set before applying search params.
    counts = {}
    for r in all_docs:
        c = r.get("category") or ""
        counts[c] = counts.get(c, 0) + 1
    total = len(all_docs)
    rows = all_docs
    if q:
        rows = [r for r in rows if q in (r["original_name"] + (r.get("ahj") or "") +
                                         (r.get("system") or "") + (r.get("category") or "")).lower()]
    if cat:
        rows = [r for r in rows if r["category"] == cat]
    groups = []
    for c in CATEGORY_ORDER:
        items = [r for r in rows if r["category"] == c]
        if items:
            groups.append((c, items))
    # any uncategorized leftovers
    known = set(CATEGORY_ORDER)
    extra = [r for r in rows if r["category"] not in known]
    if extra:
        groups.append(("Other", extra))
    dept = _theme.current_department()
    return render_template("library.html", groups=groups, counts=counts, q=q, cat=cat,
                           total=total,
                           jobs=db.all_rows("jobs", "department=?", (dept,), "name"))


@bp.route("/upload", methods=["POST"])
def upload():
    f = request.files.get("file")
    if not f or not f.filename:
        return redirect(url_for("library.index"))
    fn = re.sub(r"[^A-Za-z0-9._-]+", "_", f.filename)
    dest = os.path.join(LIB_DIR, fn)
    if os.path.exists(dest):
        fn = "%d_%s" % (int(time.time()), fn)
        dest = os.path.join(LIB_DIR, fn)
    f.save(dest)
    from modules import gdrive
    db.insert("library_docs", {"created": db.now(), "filename": fn, "original_name": f.filename,
                               "drive_id": gdrive.mirror(dest, fn),
                               "category": request.form.get("category", "Company / Misc"),
                               "ahj": request.form.get("ahj", ""), "system": request.form.get("system", ""),
                               "size": os.path.getsize(dest), "notes": request.form.get("notes", "")})
    flash("Added to library.", "ok")
    return redirect(url_for("library.index"))


@bp.route("/<int:doc_id>/attach", methods=["POST"])
def attach(doc_id):
    """Copy a library doc onto a job's Documents."""
    d = db.get("library_docs", doc_id)
    job_id = request.form.get("job_id")
    if not d or not job_id:
        return redirect(url_for("library.index"))
    src = os.path.join(LIB_DIR, d["filename"])
    newfn = "%d_%s" % (int(time.time() * 1000), d["filename"])
    shutil.copy2(src, os.path.join(config.DOC_DIR, newfn))
    db.insert("documents", {"job_id": int(job_id), "category": d["category"],
                            "filename": newfn, "original_name": d["original_name"],
                            "size": d.get("size") or 0, "notes": "From company library"})
    db.add_activity("job", int(job_id), "automation", "Attached library doc: %s" % d["original_name"])
    flash("Attached to job.", "ok")
    return redirect(request.referrer or url_for("library.index"))


@bp.route("/<int:doc_id>/delete", methods=["POST"])
def delete(doc_id):
    db.delete("library_docs", doc_id)
    flash("Removed from library.", "ok")
    return redirect(url_for("library.index"))


# ---------------------------------------------------------------------------
# Contextual helpers (used by other modules)
# ---------------------------------------------------------------------------

def for_ahj(ahj):
    """Permit packages whose AHJ tag matches (loosely)."""
    if not ahj:
        return []
    a = ahj.lower().replace("_", " ")
    out = []
    for r in db.all_rows("library_docs", "category=?", ("Permit Packages",)):
        tag = (r.get("ahj") or "").lower().replace("_", " ")
        if tag and (tag in a or a in tag):
            out.append(r)
    return out


def signups_for_system(system):
    rows = db.all_rows("library_docs", "category=?", ("Sign-Up Packages",))
    if system:
        sys_rows = [r for r in rows if system.lower() in (r.get("system") or "").lower()]
        return sys_rows or rows
    return rows
