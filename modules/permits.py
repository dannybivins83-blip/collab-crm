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
# packet_builder_handoff/ is now inside the repo (whitelabel-crm/packet_builder_handoff/)
# so Render can reach it.  Fall back to the legacy sibling-dir location for local
# installs that haven't pulled the new copy yet.
_BUILDER_DIR = os.path.normpath(os.path.join(
    config.HERE, "packet_builder_handoff", "permit_packet_builder"))
if not os.path.isdir(_BUILDER_DIR):
    # Legacy: sibling directory outside the repo root (pre-copy fallback)
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
                           systems=PERMIT_SYSTEMS, mode="new",
                           company=db.get_company_settings())


@bp.route("/<int:permit_id>")
def detail(permit_id):
    p = db.get("permits", permit_id)
    if not p:
        return redirect(url_for("permits.index"))
    sys_for_meta = request.args.get("sys") or p.get("system")
    if request.args.get("sys"):
        p["system"] = request.args.get("sys")  # reflect the picker selection
    from modules import ahj as ahj_mod
    return render_template("permit_detail.html", p=p,
                           job=db.get("jobs", p["job_id"]) if p.get("job_id") else None,
                           systems=PERMIT_SYSTEMS, status_list=PERMIT_STATUS,
                           meta=builder_meta(sys_for_meta),
                           portal=ahj_mod.ahj_portal(p.get("ahj")))


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
    # Contractor profile — use default profile for current tenant if set; else None (SB defaults).
    _contractor = None
    try:
        _cp_rows = db.all_rows("contractor_profiles", "is_default=1")
        _contractor = dict(_cp_rows[0]) if _cp_rows else None
    except Exception:
        pass
    # SAFETY (docs/PERMIT_SIGNATURE.md): the captured owner e-signature is deliberately NOT
    # forwarded into the permit packet. Permit forms — the Notice of Commencement and the
    # re-roof nailing affidavit — are NOTARIZED: the owner's signature on them IS the
    # notarized signature and must be wet-signed or RON-signed in the notary's presence.
    # Stamping a pre-captured signature there would be forgery of a notarized instrument.
    # Captured-signature auto-apply is limited to the estimate proposal and the
    # (non-notarized) sign-up package.

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
        b.build_packet(client, ahj, system, attachments, out_path, underlayment, product, contractor=_contractor)
    except Exception as e:
        flash("Build failed: %s" % e, "error")
        return redirect(url_for("permits.detail", permit_id=permit_id))

    db.update("permits", permit_id, packet_file="permits/" + fname)
    # Mirror to Google Drive so a packet built on the desktop is downloadable from
    # the cloud (Vercel can't host the 1.1 GB library, but it serves the finished PDF).
    try:
        from modules import gdrive
        if gdrive.enabled():
            gdrive.mirror(out_path, fname)
    except Exception:
        pass
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


# --- Portal account registration tracker (Consolidation #4) ------------------
# Tracks per-(platform/AHJ) contractor registration status so the team knows
# which portals are ready for auto-submit vs. need one-time registration first.

@bp.route("/portal-accounts")
def portal_accounts():
    from modules import ahj as ahj_mod
    accounts = db.all_rows("portal_accounts", order="platform, ahj")
    portals = ahj_mod._load_portals()
    # Build a set of known (platform, ahj) pairs from ahj_portals.json for the "add missing" list
    registered = {(a.get("platform", ""), k) for k, a in portals.items()
                  for row in accounts if row.get("platform") == a.get("platform") and row.get("ahj") == k}
    # All unique platforms and AHJs from portals.json that don't have an account record
    missing = [(k, p) for k, p in portals.items()
               if p.get("online") and (p.get("platform", ""), k) not in
               {(r.get("platform", ""), r.get("ahj", "")) for r in accounts}]
    return render_template("portal_accounts.html", accounts=accounts, missing=missing[:50])


@bp.route("/portal-accounts/seed", methods=["POST"])
def portal_accounts_seed():
    """Seed the portal_accounts table from ahj_portals.json for all online AHJs."""
    from modules import ahj as ahj_mod
    portals = ahj_mod._load_portals()
    existing = {(r.get("platform", ""), r.get("ahj", "")) for r in db.all_rows("portal_accounts")}
    added = 0
    for ahj_key, p in portals.items():
        if not p.get("online"):
            continue
        key = (p.get("platform", ""), ahj_key)
        if key in existing:
            continue
        db.insert("portal_accounts", {
            "created": db.now(), "updated": db.now(),
            "platform": p.get("platform", ""),
            "ahj": ahj_key, "city": p.get("city", ""), "county": p.get("county", ""),
            "registration_status": "pending",
        })
        added += 1
    flash("Seeded %d portal account records." % added, "ok")
    return redirect(url_for("permits.portal_accounts"))


@bp.route("/portal-accounts/<int:account_id>/update", methods=["POST"])
def portal_account_update(account_id):
    status = request.form.get("registration_status", "pending")
    notes = request.form.get("notes", "")
    username = request.form.get("username", "")
    db.update("portal_accounts", account_id,
              registration_status=status, notes=notes, username=username,
              updated=db.now(), last_checked=db.today())
    flash("Portal account updated.", "ok")
    return redirect(url_for("permits.portal_accounts"))


# --- Contractor profile management -------------------------------------------

@bp.route("/contractor-profile")
def contractor_profile():
    rows = db.all_rows("contractor_profiles")
    return render_template("contractor_profile.html", profiles=rows)


@bp.route("/contractor-profile/save", methods=["POST"])
def contractor_profile_save():
    pid = request.form.get("id", "").strip()
    data = {f: request.form.get(f, "").strip() for f in
            ["company_name", "license_number", "qualifier_name", "address", "city",
             "state", "zip", "phone", "email", "contact_person", "notary_county"]}
    data["is_default"] = 1 if request.form.get("is_default") else 0
    if data["is_default"]:
        db.execute("UPDATE contractor_profiles SET is_default=0")
    if pid:
        db.update("contractor_profiles", int(pid), **data)
        flash("Profile updated.", "ok")
    else:
        data["created"] = db.now()
        data["tenant_id"] = 1
        db.insert("contractor_profiles", data)
        flash("Profile saved.", "ok")
    return redirect(url_for("permits.contractor_profile"))
