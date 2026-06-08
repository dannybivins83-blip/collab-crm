# -*- coding: utf-8 -*-
"""Tasks & follow-ups — assignable tasks with due dates across leads/jobs."""
from flask import Blueprint, render_template, request, redirect, url_for, flash

import db

bp = Blueprint("tasks", __name__, url_prefix="/tasks")


def _label(t):
    et, eid = t.get("entity_type"), t.get("entity_id")
    if et == "lead":
        r = db.get("leads", eid)
        return ("lead", r["name"], url_for("leads.detail", lead_id=eid)) if r else (et, "?", "#")
    if et == "job":
        r = db.get("jobs", eid)
        return ("job", r["name"], url_for("jobs.detail", job_id=eid)) if r else (et, "?", "#")
    if et == "contact":
        r = db.get("contacts", eid)
        return ("contact", "%s %s" % (r["first_name"], r["last_name"]), url_for("contacts.detail", contact_id=eid)) if r else (et, "?", "#")
    return (et or "—", "", "#")


@bp.route("/")
def index():
    tasks = db.open_tasks()
    today = db.today()
    for t in tasks:
        t["_link"] = _label(t)
        t["_overdue"] = bool(t.get("due")) and t["due"] <= today
    return render_template("tasks.html", tasks=tasks,
                           leads=db.all_rows("leads", order="name"),
                           jobs=db.all_rows("jobs", order="name"),
                           users=db.all_rows("users", order="name"))


@bp.route("/new", methods=["POST"])
def new():
    target = request.form.get("target", "")  # "lead:3" / "job:5"
    et, _, eid = target.partition(":")
    if et and eid:
        db.add_activity(et, int(eid), "task", request.form.get("text", "").strip(),
                        due=request.form.get("due") or None,
                        assignee=request.form.get("assignee") or None)
        flash("Task added.", "ok")
    return redirect(url_for("tasks.index"))


@bp.route("/<int:task_id>/done", methods=["POST"])
def done(task_id):
    db.update("activities", task_id, done=1)
    flash("Task completed.", "ok")
    return redirect(url_for("tasks.index"))
