# -*- coding: utf-8 -*-
"""Contacts & companies — list, detail with activity timeline, create/edit."""
from flask import Blueprint, render_template, request, redirect, url_for, flash

import db
import constants

bp = Blueprint("contacts", __name__, url_prefix="/contacts")

FIELDS = ["kind", "first_name", "last_name", "company", "email", "phone",
          "address", "city", "state", "zip", "source", "tags", "notes"]


@bp.route("/")
def index():
    q = request.args.get("q", "").strip()
    if q:
        like = "%" + q + "%"
        rows = db.all_rows("contacts",
                           "first_name LIKE ? OR last_name LIKE ? OR company LIKE ? OR email LIKE ? OR phone LIKE ? OR address LIKE ?",
                           (like, like, like, like, like, like), "last_name, company")
    else:
        rows = db.all_rows("contacts", order="last_name, company")
    return render_template("contacts.html", contacts=rows, q=q)


@bp.route("/new", methods=["GET", "POST"])
def new():
    if request.method == "POST":
        data = {f: request.form.get(f, "").strip() for f in FIELDS}
        cid = db.insert("contacts", data)
        db.add_activity("contact", cid, "note", "Contact created")
        flash("Contact created.", "ok")
        return redirect(url_for("contacts.detail", contact_id=cid))
    return render_template("contact_form.html", contact={}, mode="new")


@bp.route("/<int:contact_id>")
def detail(contact_id):
    c = db.get("contacts", contact_id)
    if not c:
        return redirect(url_for("contacts.index"))
    leads = db.all_rows("leads", "contact_id=?", (contact_id,))
    jobs = db.all_rows("jobs", "contact_id=?", (contact_id,))
    return render_template("contact_detail.html", c=c, leads=leads, jobs=jobs,
                           activity=db.entity_activity("contact", contact_id),
                           tags=[t.strip() for t in (c.get("tags") or "").split(",") if t.strip()])


@bp.route("/<int:contact_id>/edit", methods=["GET", "POST"])
def edit(contact_id):
    c = db.get("contacts", contact_id)
    if not c:
        return redirect(url_for("contacts.index"))
    if request.method == "POST":
        data = {f: request.form.get(f, "").strip() for f in FIELDS}
        db.update("contacts", contact_id, **data)
        flash("Contact updated.", "ok")
        return redirect(url_for("contacts.detail", contact_id=contact_id))
    return render_template("contact_form.html", contact=c, mode="edit")


@bp.route("/<int:contact_id>/note", methods=["POST"])
def note(contact_id):
    text = request.form.get("text", "").strip()
    if text:
        db.add_activity("contact", contact_id, request.form.get("kind", "note"), text)
    return redirect(url_for("contacts.detail", contact_id=contact_id))


@bp.route("/<int:contact_id>/delete", methods=["POST"])
def delete(contact_id):
    db.delete("contacts", contact_id)
    flash("Contact deleted.", "ok")
    return redirect(url_for("contacts.index"))
