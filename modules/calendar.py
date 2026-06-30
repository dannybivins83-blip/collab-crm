# -*- coding: utf-8 -*-
"""Calendar & scheduling — appointments / crew scheduling with reminders."""
import calendar as _calmod
from datetime import date

from flask import Blueprint, render_template, request, redirect, url_for, flash

import db
import theme

bp = Blueprint("calendar", __name__, url_prefix="/calendar")
KINDS = ["Inspection", "Estimate Appt", "Production / Crew", "Final Inspection", "Meeting", "Other"]


def _parse_month(s):
    """Parse a ?month=YYYY-MM string into a (year, month) date anchored on the 1st.
    Falls back to the current month on anything malformed (fail-soft)."""
    try:
        y, m = s.split("-")
        return date(int(y), int(m), 1)
    except Exception:
        t = date.today()
        return date(t.year, t.month, 1)


def _shift_month(d, delta):
    """Return the 1st of the month `delta` months away from date `d`."""
    idx = (d.year * 12 + (d.month - 1)) + delta
    return date(idx // 12, (idx % 12) + 1, 1)


@bp.route("/")
def index():
    dept = theme.current_department()
    dept_leads_list = db.all_rows("leads", "department=?", (dept,), "name")
    dept_jobs_list = db.all_rows("jobs", "department=?", (dept,), "name")
    jobs = {j["id"]: j for j in dept_jobs_list}
    leads = {l["id"]: l for l in dept_leads_list}
    # Scope appointments via SQL IN() — avoids full-table scan + cross-tenant bleed.
    # Unlinked appointments (no job/lead) remain visible to all depts by design.
    _jids = tuple(jobs.keys())
    _lids = tuple(leads.keys())
    _parts, _params = ["(job_id IS NULL AND lead_id IS NULL)"], []
    if _jids:
        _parts.append("job_id IN (%s)" % ",".join("?" * len(_jids)))
        _params.extend(_jids)
    if _lids:
        _parts.append("lead_id IN (%s)" % ",".join("?" * len(_lids)))
        _params.extend(_lids)
    dept_appts = db.all_rows("appointments", " OR ".join(_parts), tuple(_params), "start_at")
    for a in dept_appts:
        a["_who"] = (jobs.get(a["job_id"], {}) or leads.get(a["lead_id"], {}) or {}).get("name", "")
    # Aggregates for filter dropdowns (unfiltered dept set).
    assignees = sorted({a.get("assignee") for a in dept_appts if a.get("assignee")})
    kind_f = request.args.get("kind", "").strip()
    assignee_f = request.args.get("assignee", "").strip()
    appts = dept_appts
    if kind_f:
        appts = [a for a in appts if a.get("kind") == kind_f]
    if assignee_f:
        appts = [a for a in appts if (a.get("assignee") or "") == assignee_f]
    today = db.today()
    upcoming = [a for a in appts if (a.get("start_at") or "") >= today]
    past = [a for a in appts if (a.get("start_at") or "") < today]

    # --- Month-grid view ---------------------------------------------------
    # Build a 7-column (Sun..Sat) grid of weeks covering the visible month, with
    # each day's appointments bucketed by ISO date. start_at is a datetime-local
    # string (YYYY-MM-DDTHH:MM), so its first 10 chars are the ISO date.
    anchor = _parse_month(request.args.get("month", "").strip())
    prev_m = _shift_month(anchor, -1)
    next_m = _shift_month(anchor, 1)
    by_date = {}
    for a in appts:
        d = (a.get("start_at") or "")[:10]
        if d:
            by_date.setdefault(d, []).append(a)
    for lst in by_date.values():
        lst.sort(key=lambda x: x.get("start_at") or "")
    cal = _calmod.Calendar(firstweekday=6)  # 6 = Sunday
    weeks = []
    for week in cal.monthdatescalendar(anchor.year, anchor.month):
        row = []
        for d in week:
            iso = d.isoformat()
            row.append({"iso": iso, "day": d.day,
                        "in_month": (d.month == anchor.month),
                        "is_today": (iso == today),
                        "appts": by_date.get(iso, [])})
        weeks.append(row)
    weekday_labels = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]

    return render_template("calendar.html", upcoming=upcoming, past=past, kinds=KINDS,
                           leads=dept_leads_list, jobs=dept_jobs_list,
                           users=db.all_rows("users", order="name"),
                           assignees=assignees, kind_f=kind_f, assignee_f=assignee_f,
                           weeks=weeks, weekday_labels=weekday_labels,
                           month_label=anchor.strftime("%B %Y"),
                           cur_month=anchor.strftime("%Y-%m"),
                           prev_month=prev_m.strftime("%Y-%m"),
                           next_month=next_m.strftime("%Y-%m"))


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
