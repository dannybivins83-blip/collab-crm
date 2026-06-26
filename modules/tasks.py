# -*- coding: utf-8 -*-
"""Tasks & follow-ups — assignable tasks with due dates across leads/jobs."""
from flask import Blueprint, render_template, request, redirect, url_for, flash

import db
import theme

bp = Blueprint("tasks", __name__, url_prefix="/tasks")


@bp.route("/")
def index():
    dept = theme.current_department()
    dept_leads = db.all_rows("leads", "department=?", (dept,), "name")
    dept_jobs = db.all_rows("jobs", "department=?", (dept,), "name")
    dept_lead_ids = {l["id"] for l in dept_leads}
    dept_job_ids = {j["id"] for j in dept_jobs}
    all_tasks = db.open_tasks()
    today = db.today()
    # Scope to this dept — contacts are cross-tenant so always shown.
    tasks = [t for t in all_tasks if
             (t.get("entity_type") == "lead" and t.get("entity_id") in dept_lead_ids) or
             (t.get("entity_type") == "job" and t.get("entity_id") in dept_job_ids) or
             t.get("entity_type") not in ("lead", "job")]
    # Batch-load entity names to avoid one db.get() per task row.
    _t_lead_ids = tuple({t["entity_id"] for t in tasks if t.get("entity_type") == "lead"})
    _t_job_ids = tuple({t["entity_id"] for t in tasks if t.get("entity_type") == "job"})
    _t_con_ids = tuple({t["entity_id"] for t in tasks if t.get("entity_type") == "contact"})
    _lmap, _jmap, _cmap = {}, {}, {}
    if _t_lead_ids:
        ph = ",".join("?" * len(_t_lead_ids))
        for r in db.all_rows("leads", "id IN (%s)" % ph, _t_lead_ids):
            _lmap[r["id"]] = r
    if _t_job_ids:
        ph = ",".join("?" * len(_t_job_ids))
        for r in db.all_rows("jobs", "id IN (%s)" % ph, _t_job_ids):
            _jmap[r["id"]] = r
    if _t_con_ids:
        ph = ",".join("?" * len(_t_con_ids))
        for r in db.all_rows("contacts", "id IN (%s)" % ph, _t_con_ids):
            _cmap[r["id"]] = r
    for t in tasks:
        et, eid = t.get("entity_type"), t.get("entity_id")
        t["_overdue"] = bool(t.get("due")) and t["due"] <= today
        if et == "lead":
            r = _lmap.get(eid)
            t["_link"] = ("lead", r["name"] if r else "?",
                          url_for("leads.detail", lead_id=eid) if r else "#")
        elif et == "job":
            r = _jmap.get(eid)
            t["_link"] = ("job", r["name"] if r else "?",
                          url_for("jobs.detail", job_id=eid) if r else "#")
        elif et == "contact":
            r = _cmap.get(eid)
            t["_link"] = ("contact",
                          ("%s %s" % (r["first_name"], r["last_name"])).strip() if r else "?",
                          url_for("contacts.detail", contact_id=eid) if r else "#")
        else:
            t["_link"] = (et or "—", "", "#")
    return render_template("tasks.html", tasks=tasks,
                           leads=dept_leads, jobs=dept_jobs,
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
