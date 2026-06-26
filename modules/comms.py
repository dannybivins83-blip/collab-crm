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
    rows = db.all_rows("activities", "kind IN ('call','email','sms','draft')", order="id DESC", limit=200)
    # Batch-load referenced entities to avoid N+1 db.get() calls (one per activity row).
    _lead_ids = {a["entity_id"] for a in rows if a.get("entity_type") == "lead"}
    _job_ids  = {a["entity_id"] for a in rows if a.get("entity_type") == "job"}
    _con_ids  = {a["entity_id"] for a in rows if a.get("entity_type") == "contact"}
    _lmap = {}; _jmap = {}; _cmap = {}
    if _lead_ids:
        ph = ",".join("?" * len(_lead_ids))
        for r in db.all_rows("leads", "id IN (%s)" % ph, tuple(_lead_ids)):
            _lmap[r["id"]] = r.get("name") or "?"
    if _job_ids:
        ph = ",".join("?" * len(_job_ids))
        for r in db.all_rows("jobs", "id IN (%s)" % ph, tuple(_job_ids)):
            _jmap[r["id"]] = r.get("name") or "?"
    if _con_ids:
        ph = ",".join("?" * len(_con_ids))
        for r in db.all_rows("contacts", "id IN (%s)" % ph, tuple(_con_ids)):
            _cmap[r["id"]] = ("%s %s" % (r.get("first_name", ""), r.get("last_name", ""))).strip() or "?"
    for a in rows:
        et, eid = a.get("entity_type"), a.get("entity_id")
        if et == "lead":      a["_who"] = _lmap.get(eid, "?")
        elif et == "job":     a["_who"] = _jmap.get(eid, "?")
        elif et == "contact": a["_who"] = _cmap.get(eid, "?")
        else:                 a["_who"] = "?"
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
