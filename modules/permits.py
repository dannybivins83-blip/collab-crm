# -*- coding: utf-8 -*-
"""Permits module — per-job permit tracking + the SeaBreeze permit-packet builder.

Folds in the existing build.py engine (SeaBreeze_Permit_Library) so a fully
pre-filled county permit packet PDF can be generated straight from a job: pick
AHJ + system + underlayment + product, attach the RoofGraf report, click Build.
"""
import os
import re
import sys
import time

from flask import Blueprint, render_template, request, redirect, url_for, flash

import config
import db

bp = Blueprint("permits", __name__, url_prefix="/permits")

PERMIT_SYSTEMS = ["shingle", "tile", "metal", "flat"]
PERMIT_STATUS = ["prep", "submitted", "approved", "closed"]
FIELDS = ["job_id", "ahj", "county", "system", "permit_number", "submitted_date",
          "approved_date", "notes"]

# Make the permit-packet builder importable (engine + SeaBreeze_Permit_Library).
_BUILDER_DIR = os.path.normpath(os.path.join(
    config.HERE, "..", "packet_builder_handoff", "permit_packet_builder"))
if _BUILDER_DIR not in sys.path and os.path.isdir(_BUILDER_DIR):
    sys.path.insert(0, _BUILDER_DIR)

# System key map: our lowercase -> build.py's capitalized.
_SYS_MAP = {"shingle": "Shingle", "tile": "Tile", "metal": "Metal", "flat": "Flat"}


def _build():
    """Import the build engine lazily; None if unavailable."""
    try:
        import build
        return build
    except Exception:
        return None


def _builder_available():
    return _build() is not None


def builder_meta(system_lower=None):
    """AHJ list + system/underlayment/product options for the wizard."""
    b = _build()
    if not b:
        return {"available": False, "ahjs": [], "systems": PERMIT_SYSTEMS, "uls": [], "products": []}
    sysname = _SYS_MAP.get(system_lower or "", "")
    ahjs = [(a, a.replace("_", " ")) for a in b.list_ahjs()]
    return {
        "available": True,
        "ahjs": ahjs,
        "systems": list(b.SYSTEMS.keys()),
        "uls": b.ul_choices(sysname) if sysname else [],
        "products": b.prod_choices(sysname) if sysname else [],
    }


@bp.route("/")
def index():
    rows = db.all_rows("permits", order="id DESC")
    jobs = {j["id"]: j for j in db.all_rows("jobs")}
    for p in rows:
        p["_job"] = jobs.get(p["job_id"])
    return render_template("permits.html", permits=rows, status_list=PERMIT_STATUS)


@bp.route("/new", methods=["GET", "POST"])
def new():
    if request.method == "POST":
        data = {f: request.form.get(f, "").strip() for f in FIELDS}
        data["status"] = request.form.get("status", "prep")
        pid = db.insert("permits", data)
        if data.get("job_id"):
            db.add_activity("job", int(data["job_id"]), "automation",
                            "Permit record created (%s · %s)" % (data.get("ahj"), data.get("system")))
        flash("Permit created.", "ok")
        return redirect(url_for("permits.detail", permit_id=pid))
    job_id = request.args.get("job_id", "")
    job = db.get("jobs", job_id) if job_id else None
    pre = {}
    if job:
        pre = {"job_id": job["id"], "ahj": job.get("ahj"), "county": job.get("county"),
               "system": job.get("system")}
    return render_template("permit_form.html", permit=pre, jobs=db.all_rows("jobs", order="name"),
                           systems=PERMIT_SYSTEMS, mode="new")


@bp.route("/<int:permit_id>")
def detail(permit_id):
    p = db.get("permits", permit_id)
    if not p:
        return redirect(url_for("permits.index"))
    sys_for_meta = request.args.get("sys") or p.get("system")
    if request.args.get("sys"):
        p["system"] = request.args.get("sys")  # reflect the picker selection
    return render_template("permit_detail.html", p=p,
                           job=db.get("jobs", p["job_id"]) if p.get("job_id") else None,
                           systems=PERMIT_SYSTEMS, status_list=PERMIT_STATUS,
                           meta=builder_meta(sys_for_meta))


@bp.route("/<int:permit_id>/save", methods=["POST"])
def save(permit_id):
    data = {f: request.form.get(f, "").strip() for f in FIELDS if f != "job_id"}
    data["status"] = request.form.get("status", "prep")
    db.update("permits", permit_id, **data)
    flash("Permit updated.", "ok")
    return redirect(url_for("permits.detail", permit_id=permit_id))


@bp.route("/<int:permit_id>/build", methods=["POST"])
def build_packet(permit_id):
    """Generate a permit packet PDF via the SeaBreeze build engine."""
    p = db.get("permits", permit_id)
    if not p:
        return redirect(url_for("permits.index"))
    ahj = request.form.get("ahj", "").strip()
    system_lower = request.form.get("system", "").strip()
    underlayment = request.form.get("underlayment", "").strip() or None
    product = request.form.get("product", "").strip() or None
    # Persist the chosen AHJ/system back onto the permit.
    db.update("permits", permit_id, ahj=ahj, system=system_lower)

    b = _build()
    if not b:
        flash("Permit builder engine (build.py) not available on this host.", "error")
        return redirect(url_for("permits.detail", permit_id=permit_id))
    system = _SYS_MAP.get(system_lower)
    if not ahj or system not in b.SYSTEMS:
        flash("Pick an AHJ and a valid system before building.", "error")
        return redirect(url_for("permits.detail", permit_id=permit_id))

    job = db.get("jobs", p["job_id"]) if p.get("job_id") else {}
    job = job or {}
    client = {"owner": job.get("name", ""), "address": job.get("address", ""),
              "city": job.get("city", ""), "zip": job.get("zip", ""),
              "phone": job.get("phone", ""), "pcn": job.get("pcn", ""),
              "legal": job.get("legal", ""), "existing": job.get("existing", ""),
              "area": job.get("area", ""), "slope": job.get("slope", ""),
              "mrh": job.get("mrh", ""), "exposure": job.get("exposure", ""),
              "value": job.get("contract_value", "")}

    # Optional RoofGraf attachment.
    attachments = []
    f = request.files.get("attachment")
    if f and f.filename:
        ap = os.path.join(config.PERMIT_DIR, "att_%d_%s" % (
            int(time.time() * 1000), re.sub(r"[^A-Za-z0-9._-]+", "_", f.filename)))
        f.save(ap)
        attachments.append(ap)
        # Pull squares/pitch from the report if the job lacks them.
        if not client.get("area") or not client.get("slope"):
            meas = b.parse_roofgraf(ap) or {}
            client["area"] = client.get("area") or meas.get("area", "")
            client["slope"] = client.get("slope") or meas.get("pitch", "")

    safe = re.sub(r"[^A-Za-z0-9]+", "_", client["owner"] or "client").strip("_") or "client"
    fname = "Permit_%d_%s_%s_%s.pdf" % (permit_id, safe, ahj, system_lower)
    out_path = os.path.join(config.PERMIT_DIR, fname)
    try:
        b.build_packet(client, ahj, system, attachments, out_path, underlayment, product)
    except Exception as e:
        flash("Build failed: %s" % e, "error")
        return redirect(url_for("permits.detail", permit_id=permit_id))

    db.update("permits", permit_id, packet_file="permits/" + fname)
    if p.get("job_id"):
        db.insert("documents", {"job_id": p["job_id"], "category": "Permit",
                                "filename": fname, "original_name": fname,
                                "size": os.path.getsize(out_path) if os.path.exists(out_path) else 0,
                                "notes": "Permit packet (%s, %s)" % (ahj.replace("_", " "), system)})
        db.add_activity("job", p["job_id"], "automation",
                        "Permit packet built: %s (%s)" % (ahj.replace("_", " "), system))
    flash("Permit packet built: %s" % fname, "ok")
    return redirect(url_for("permits.detail", permit_id=permit_id))


@bp.route("/<int:permit_id>/delete", methods=["POST"])
def delete(permit_id):
    db.delete("permits", permit_id)
    flash("Permit deleted.", "ok")
    return redirect(url_for("permits.index"))
