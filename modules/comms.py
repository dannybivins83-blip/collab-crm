# -*- coding: utf-8 -*-
"""Communication log — email/SMS/call log per contact + draft emails for review.

Drafts are saved to the activity log only. Nothing is ever auto-sent.
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash

import db
import theme

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
    dept = theme.current_department()
    dept_leads_list = db.all_rows("leads", "department=?", (dept,), "name")
    dept_jobs_list = db.all_rows("jobs", "department=?", (dept,), "name")
    _dept_lead_ids = tuple(l["id"] for l in dept_leads_list)
    _dept_job_ids = tuple(j["id"] for j in dept_jobs_list)
    # Fetch dept-scoped comms via IN() to avoid cross-tenant contamination.
    _parts, _params = [], []
    if _dept_lead_ids:
        _parts.append("(entity_type='lead' AND entity_id IN (%s))" % ",".join("?" * len(_dept_lead_ids)))
        _params.extend(_dept_lead_ids)
    if _dept_job_ids:
        _parts.append("(entity_type='job' AND entity_id IN (%s))" % ",".join("?" * len(_dept_job_ids)))
        _params.extend(_dept_job_ids)
    _parts.append("entity_type NOT IN ('lead','job')")
    _conn = db.connect()
    try:
        _where = "kind IN ('call','email','sms','draft') AND (%s)" % " OR ".join(_parts)
        rows = [dict(r) for r in _conn.execute(
            "SELECT * FROM activities WHERE %s ORDER BY id DESC LIMIT 200" % _where,
            tuple(_params)).fetchall()]
    finally:
        _conn.close()
    # Batch name lookups — only for IDs actually in this result set.
    _lmap = {l["id"]: l.get("name") or "?" for l in dept_leads_list}
    _jmap = {j["id"]: j.get("name") or "?" for j in dept_jobs_list}
    _cmap = {}
    _con_ids = tuple({a["entity_id"] for a in rows if a.get("entity_type") == "contact"})
    if _con_ids:
        ph = ",".join("?" * len(_con_ids))
        for r in db.all_rows("contacts", "id IN (%s)" % ph, _con_ids):
            _cmap[r["id"]] = ("%s %s" % (r.get("first_name", ""), r.get("last_name", ""))).strip() or "?"
    for a in rows:
        et, eid = a.get("entity_type"), a.get("entity_id")
        if et == "lead":      a["_who"] = _lmap.get(eid, "?")
        elif et == "job":     a["_who"] = _jmap.get(eid, "?")
        elif et == "contact": a["_who"] = _cmap.get(eid, "?")
        else:                 a["_who"] = "?"
    return render_template("comms.html", logs=rows,
                           leads=dept_leads_list, jobs=dept_jobs_list,
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
