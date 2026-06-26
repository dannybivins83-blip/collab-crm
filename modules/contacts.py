# -*- coding: utf-8 -*-
"""Contacts & companies — list, detail with activity timeline, create/edit.

Also home to **General Contractor (GC) consolidation**: a GC is just a contacts
row flagged ``is_gc=1``. Repeat GCs that AccuLynx fragmented into one contact
per job get merged into a single GC record (all their jobs re-pointed to it via
``jobs.contact_id``), and new jobs can be spun up under that GC pre-filled.
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash

import db
import constants

bp = Blueprint("contacts", __name__, url_prefix="/contacts")

# A GC is a contacts row with is_gc=1 — added here (our lane) rather than in the
# shared db.py SCHEMA so we don't touch a coordinated hot file. Idempotent.
db._ensure_column("contacts", "is_gc", "INTEGER DEFAULT 0")

FIELDS = ["kind", "first_name", "last_name", "company", "email", "phone",
          "address", "city", "state", "zip", "source", "tags", "notes", "is_gc"]

# Tables that point at a contact via contact_id, plus the activity timeline which
# uses (entity_type='contact', entity_id). Used by the merge tool to re-home a
# duplicate's history onto the surviving GC record.
_CONTACT_FK_TABLES = ["leads", "jobs", "estimates", "appointments", "invoices", "materials"]


def _form_data():
    """Pull the editable contact fields from the POST form, coercing the is_gc
    checkbox to 0/1."""
    data = {f: request.form.get(f, "").strip() for f in FIELDS}
    data["is_gc"] = 1 if request.form.get("is_gc") in ("1", "on", "true") else 0
    return data


@bp.route("/")
def index():
    import re as _re
    q = request.args.get("q", "").strip()
    if q:
        like = "%" + q.lower() + "%"
        rows = db.all_rows(
            "contacts",
            "LOWER(first_name) LIKE ? OR LOWER(last_name) LIKE ? OR "
            "LOWER(company) LIKE ? OR LOWER(email) LIKE ? OR phone LIKE ? OR "
            "LOWER(address) LIKE ?",
            (like, like, like, like, like, like), "last_name, company")
        # also match digit-stripped phone (e.g. "5614567890" finds "(561) 456-7890")
        qd = _re.sub(r"\D", "", q)
        if qd and len(qd) >= 7:
            found_ids = {r["id"] for r in rows}
            for c in db.all_rows("contacts", order="last_name, company"):
                if c["id"] not in found_ids and qd in _re.sub(r"\D", "", c.get("phone") or ""):
                    rows.append(c)
    else:
        rows = db.all_rows("contacts", order="last_name, company")
    return render_template("contacts.html", contacts=rows, q=q)


# ---------------------------------------------------------------------------
# General Contractors (GC) view
# ---------------------------------------------------------------------------

def _gc_jobs(contact_id):
    """All jobs for a GC (shared contact_id) with profit pulled from the job's
    worksheet, plus rolled-up totals."""
    from modules import worksheet
    import theme
    jobs = db.all_rows("jobs", "contact_id=?", (contact_id,), "created DESC")
    total_value = total_profit = 0.0
    for j in jobs:
        pa = worksheet.profit_analysis(j["id"])
        j["_value"] = pa["contract_value"] or theme.est_num(j.get("contract_value"))
        j["_profit"] = pa["gross_profit"]
        j["_has_ws"] = pa["has_ws"]
        j["_stage"] = constants.job_stage(j["stage"])
        total_value += j["_value"]
        total_profit += j["_profit"]
    return jobs, {"count": len(jobs), "value": total_value, "profit": total_profit}


def _name_key(c):
    return ((c.get("first_name") or "").strip().lower(),
            (c.get("last_name") or "").strip().lower())


def _dupe_candidates(gc):
    """Other contacts that look like the same entity as this GC — same
    (first,last) name, or matching phone/email — so they can be merged in.
    Never includes other GCs (don't silently swallow a real GC)."""
    key = _name_key(gc)
    phone = (gc.get("phone") or "").strip()
    email = (gc.get("email") or "").strip().lower()
    out = []
    for c in db.all_rows("contacts", "id<>?", (gc["id"],)):
        if c.get("is_gc"):
            continue
        if (_name_key(c) == key and any(key)) \
           or (phone and (c.get("phone") or "").strip() == phone) \
           or (email and (c.get("email") or "").strip().lower() == email):
            c["_jobs"] = db.all_rows("jobs", "contact_id=?", (c["id"],))
            c["_leads"] = db.all_rows("leads", "contact_id=?", (c["id"],))
            out.append(c)
    return out


@bp.route("/gcs")
def gcs():
    """List of all General Contractors with job counts + totals."""
    import theme
    rows = db.all_rows("contacts", "is_gc=1", order="last_name, company")
    for g in rows:
        jobs = db.all_rows("jobs", "contact_id=?", (g["id"],))
        g["_job_count"] = len(jobs)
        g["_value"] = sum(theme.est_num(j.get("contract_value")) for j in jobs)
    return render_template("gc_list.html", gcs=rows)


@bp.route("/<int:contact_id>")
def detail(contact_id):
    c = db.get("contacts", contact_id)
    if not c:
        return redirect(url_for("contacts.index"))
    leads = db.all_rows("leads", "contact_id=?", (contact_id,))
    jobs = db.all_rows("jobs", "contact_id=?", (contact_id,))
    ctx = dict(c=c, leads=leads, jobs=jobs,
               activity=db.entity_activity("contact", contact_id),
               tags=[t.strip() for t in (c.get("tags") or "").split(",") if t.strip()])
    if c.get("is_gc"):
        gc_jobs, totals = _gc_jobs(contact_id)
        ctx.update(gc_jobs=gc_jobs, gc_totals=totals, dupes=_dupe_candidates(c))
        return render_template("gc_detail.html", **ctx)
    return render_template("contact_detail.html", **ctx)


@bp.route("/new", methods=["GET", "POST"])
def new():
    if request.method == "POST":
        data = _form_data()
        cid = db.insert("contacts", data)
        db.add_activity("contact", cid, "note",
                        "GC created" if data.get("is_gc") else "Contact created")
        flash("Contact created.", "ok")
        return redirect(url_for("contacts.detail", contact_id=cid))
    # ?gc=1 pre-checks the General Contractor box on the new-contact form.
    return render_template("contact_form.html",
                           contact={"is_gc": 1} if request.args.get("gc") else {},
                           mode="new")


@bp.route("/<int:contact_id>/edit", methods=["GET", "POST"])
def edit(contact_id):
    c = db.get("contacts", contact_id)
    if not c:
        return redirect(url_for("contacts.index"))
    if request.method == "POST":
        data = _form_data()
        db.update("contacts", contact_id, **data)
        flash("Contact updated.", "ok")
        return redirect(url_for("contacts.detail", contact_id=contact_id))
    return render_template("contact_form.html", contact=c, mode="edit")


@bp.route("/<int:contact_id>/make-gc", methods=["POST"])
def make_gc(contact_id):
    """Promote a plain contact to a General Contractor record (or demote)."""
    c = db.get("contacts", contact_id)
    if not c:
        return redirect(url_for("contacts.index"))
    to_gc = request.form.get("is_gc", "1") in ("1", "on", "true")
    db.update("contacts", contact_id, is_gc=1 if to_gc else 0)
    db.add_activity("contact", contact_id, "note",
                    "Marked as General Contractor" if to_gc else "Unmarked as General Contractor")
    flash("Marked as General Contractor." if to_gc else "Removed GC flag.", "ok")
    return redirect(url_for("contacts.detail", contact_id=contact_id))


# ---------------------------------------------------------------------------
# Merge / consolidate duplicates into one GC
# ---------------------------------------------------------------------------

def _merge_plan(survivor_id, dupe_ids):
    """Count what will move from each dupe onto the survivor (preview, no writes)."""
    moves = {t: 0 for t in _CONTACT_FK_TABLES}
    moves["activities"] = 0
    detail = []
    for did in dupe_ids:
        d = db.get("contacts", did)
        if not d:
            continue
        row = {"contact": d, "tables": {}}
        for t in _CONTACT_FK_TABLES:
            n = len(db.all_rows(t, "contact_id=?", (did,)))
            moves[t] += n
            row["tables"][t] = n
        acts = len(db.entity_activity("contact", did))
        moves["activities"] += acts
        row["tables"]["activities"] = acts
        detail.append(row)
    return moves, detail


def _do_merge(survivor_id, dupe_ids):
    """Re-point every dupe's jobs/leads/estimates/appointments/activities onto the
    survivor, then delete the now-empty dupe contact rows. No job history is lost —
    only the duplicate *contact* shells are removed.
    Wrapped in a single BEGIN IMMEDIATE transaction so a crash mid-merge can't
    leave FKs pointing at a deleted contact."""
    conn = db.begin_immediate()
    moved = {t: 0 for t in _CONTACT_FK_TABLES}
    moved["activities"] = 0
    try:
        for did in dupe_ids:
            if did == survivor_id:
                continue
            for t in _CONTACT_FK_TABLES:
                rows = conn.execute(
                    "SELECT id FROM %s WHERE contact_id=?" % t, (did,)).fetchall()
                for r in rows:
                    conn.execute(
                        "UPDATE %s SET contact_id=? WHERE id=?" % t,
                        (survivor_id, r["id"]))
                    moved[t] += 1
            acts = conn.execute(
                "SELECT id FROM activities WHERE entity_type='contact' AND entity_id=?",
                (did,)).fetchall()
            for a in acts:
                conn.execute(
                    "UPDATE activities SET entity_id=? WHERE id=?",
                    (survivor_id, a["id"]))
                moved["activities"] += 1
            conn.execute("DELETE FROM contacts WHERE id=?", (did,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return moved


@bp.route("/<int:contact_id>/merge", methods=["POST"])
def merge(contact_id):
    """Two-phase merge into this GC: first POST (preview=1) shows what will move;
    second POST (confirm=1) executes it."""
    survivor = db.get("contacts", contact_id)
    if not survivor:
        return redirect(url_for("contacts.index"))
    dupe_ids = [int(x) for x in request.form.getlist("dupe_ids") if x.isdigit()
                and int(x) != contact_id]
    if not dupe_ids:
        flash("Select at least one duplicate to merge.", "error")
        return redirect(url_for("contacts.detail", contact_id=contact_id))

    if request.form.get("confirm"):
        moved = _do_merge(contact_id, dupe_ids)
        summary = ", ".join("%d %s" % (n, t) for t, n in moved.items() if n)
        db.add_activity("contact", contact_id, "note",
                        "Merged %d duplicate contact(s) into this GC (%s)." % (
                            len(dupe_ids), summary or "no linked records"))
        flash("Merged %d duplicate(s) into this GC. Moved: %s." % (
            len(dupe_ids), summary or "nothing to move"), "ok")
        return redirect(url_for("contacts.detail", contact_id=contact_id))

    # Preview
    moves, detail = _merge_plan(contact_id, dupe_ids)
    return render_template("gc_merge_preview.html", survivor=survivor,
                           moves=moves, detail=detail, dupe_ids=dupe_ids)


@bp.route("/<int:contact_id>/note", methods=["POST"])
def note(contact_id):
    text = request.form.get("text", "").strip()
    if text:
        db.add_activity("contact", contact_id, request.form.get("kind", "note"), text)
    return redirect(url_for("contacts.detail", contact_id=contact_id))


@bp.route("/<int:contact_id>/delete", methods=["POST"])
def delete(contact_id):
    # Null out FK references in all tables that point at this contact.
    for _t in ("leads", "jobs", "estimates", "appointments"):
        db.execute("UPDATE %s SET contact_id=NULL WHERE contact_id=?" % _t, (contact_id,))
    db.execute("DELETE FROM activities WHERE entity_type='contact' AND entity_id=?", (contact_id,))
    db.delete("contacts", contact_id)
    flash("Contact deleted.", "ok")
    return redirect(url_for("contacts.index"))
