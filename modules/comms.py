# -*- coding: utf-8 -*-
"""Communication log — email/SMS/call log per contact + draft emails for review.

Drafts are saved to the activity log only. Nothing is ever auto-sent.
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash

import db

bp = Blueprint("comms", __name__, url_prefix="/comms")


def _name_for(et, eid):
    if et == "lead":
        r = db.get("leads", eid)
        return r["name"] if r else "?"
    if et == "job":
        r = db.get("jobs", eid)
        return r["name"] if r else "?"
    if et == "contact":
        r = db.get("contacts", eid)
        return "%s %s" % (r["first_name"], r["last_name"]) if r else "?"
    return "?"


@bp.route("/")
def index():
    rows = db.all_rows("activities", "kind IN ('call','email','sms','draft')", order="id DESC")
    for a in rows:
        a["_who"] = _name_for(a["entity_type"], a["entity_id"])
    return render_template("comms.html", logs=rows,
                           leads=db.all_rows("leads", order="name"),
                           jobs=db.all_rows("jobs", order="name"),
                           contacts=db.all_rows("contacts", order="last_name"))


@bp.route("/log", methods=["POST"])
def log():
    target = request.form.get("target", "")
    et, _, eid = target.partition(":")
    kind = request.form.get("kind", "call")
    text = request.form.get("text", "").strip()
    if et and eid and text:
        db.add_activity(et, int(eid), kind, text)
        if kind in ("call", "email", "sms") and et == "lead":
            db.update("leads", int(eid), last_contact=db.today())
        flash("Logged.", "ok")
    return redirect(url_for("comms.index"))


@bp.route("/draft", methods=["POST"])
def draft():
    """Save a draft email for review — never sent."""
    target = request.form.get("target", "")
    et, _, eid = target.partition(":")
    subject = request.form.get("subject", "").strip()
    body = request.form.get("body", "").strip()
    if et and eid and (subject or body):
        db.add_activity(et, int(eid), "draft", "✉️ DRAFT — %s\n%s" % (subject, body))
        flash("Draft saved for review (not sent).", "ok")
    return redirect(url_for("comms.index"))
