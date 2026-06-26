# -*- coding: utf-8 -*-
"""Calendar & scheduling — appointments / crew scheduling with reminders."""
from flask import Blueprint, render_template, request, redirect, url_for, flash

import db
import theme

bp = Blueprint("calendar", __name__, url_prefix="/calendar")
KINDS = ["Inspection", "Estimate Appt", "Production / Crew", "Final Inspection", "Meeting", "Other"]


@bp.route("/")
def index():
    dept = theme.current_department()
    appts = db.all_rows("appointments", order="start_at")
    jobs = {j["id"]: j for j in db.all_rows("jobs", "department=?", (dept,))}
    leads = {l["id"]: l for l in db.all_rows("leads", "department=?", (dept,))}
    for a in appts:
        a["_who"] = (jobs.get(a["job_id"], {}) or leads.get(a["lead_id"], {}) or {}).get("name", "")
    upcoming = [a for a in appts if (a.get("start_at") or "") >= db.today()]
    past = [a for a in appts if (a.get("start_at") or "") < db.today()]
    return render_template("calendar.html", upcoming=upcoming, past=past, kinds=KINDS,
                           leads=db.all_rows("leads", "department=?", (dept,), "name"),
                           jobs=db.all_rows("jobs", "department=?", (dept,), "name"),
                           users=db.all_rows("users", order="name"))


@bp.route("/new", methods=["POST"])
def new():
    target = request.form.get("target", "")
    et, _, eid = target.partition(":")
    data = {"title": request.form.get("title", "").strip(), "kind": request.form.get("kind", "Other"),
            "start_at": request.form.get("start", ""), "end_at": request.form.get("end", ""),
            "assignee": request.form.get("assignee", ""), "location": request.form.get("location", ""),
            "notes": request.form.get("notes", ""), "reminder": request.form.get("reminder", "")}
    if et == "lead" and eid:
        data["lead_id"] = int(eid)
    elif et == "job" and eid:
        data["job_id"] = int(eid)
    db.insert("appointments", data)
    if data.get("lead_id"):
        db.add_activity("lead", data["lead_id"], "note", "Appointment: %s @ %s" % (data["title"], data["start_at"]))
    if data.get("job_id"):
        db.add_activity("job", data["job_id"], "note", "Appointment: %s @ %s" % (data["title"], data["start_at"]))
    flash("Appointment scheduled.", "ok")
    return redirect(url_for("calendar.index"))


@bp.route("/<int:appt_id>/delete", methods=["POST"])
def delete(appt_id):
    db.delete("appointments", appt_id)
    return redirect(url_for("calendar.index"))
