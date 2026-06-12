# -*- coding: utf-8 -*-
"""Dashboard — AccuLynx-style Current Pipeline (L/P/A/C/I) + activity feed."""
from flask import Blueprint, render_template

import db
import theme
import constants

bp = Blueprint("dashboard", __name__)


def _bucket_of(kind, stage):
    if kind == "lead":
        return constants.lead_stage(stage).get("bucket")
    return constants.job_stage(stage).get("bucket")


@bp.route("/")
def home():
    dept = theme.current_department()
    leads = db.all_rows("leads", "department=?", (dept,))
    jobs = db.all_rows("jobs", "department=?", (dept,))

    # Current Pipeline: count + $ value per top-level bucket (L/P/A/C/I).
    buckets = {b["key"]: {"def": b, "count": 0, "value": 0.0} for b in constants.BUCKETS}
    for l in leads:
        if l["stage"] == "lost":
            continue
        bk = _bucket_of("lead", l["stage"])
        if bk in buckets:
            buckets[bk]["count"] += 1
            buckets[bk]["value"] += theme.est_num(l.get("estimate"))
    for j in jobs:
        if j["stage"] == "canceled":
            continue
        bk = _bucket_of("job", j["stage"])
        if bk in buckets:
            buckets[bk]["count"] += 1
            buckets[bk]["value"] += theme.est_num(j.get("contract_value"))
    pipeline = [buckets[b["key"]] for b in constants.BUCKETS]
    active_jobs = len([j for j in jobs if j["stage"] not in constants.JOB_INACTIVE])

    # Overdue follow-ups across both pipelines.
    overdue = []
    for l in leads:
        if l["stage"] in constants.LEAD_INACTIVE:
            continue
        sd = constants.lead_stage(l["stage"])
        fs = theme.follow_status(sd, l.get("last_contact") or l.get("created"), l.get("snooze_until"))
        if fs["level"] != "ok":
            overdue.append(("lead", l, sd, fs))
    for j in jobs:
        if j["stage"] in constants.JOB_INACTIVE:
            continue
        sd = constants.job_stage(j["stage"])
        fs = theme.follow_status(sd, j.get("stage_since") or j.get("created"), j.get("snooze_until"))
        if fs["level"] != "ok":
            overdue.append(("job", j, sd, fs))
    overdue.sort(key=lambda x: (0 if x[3]["level"] == "hot" else 1, -x[3]["days"]))

    # Recent activity feed (newest across all entities).
    feed = db.all_rows("activities", order="id DESC")[:80]  # show ~15, scroll the rest
    for a in feed:
        a["_who"] = _activity_name(a)

    won = len([l for l in leads if l["stage"] == "won"])
    lost = len([l for l in leads if l["stage"] == "lost"])
    decided = won + lost
    win_rate = round(100 * won / decided) if decided else 0

    # Gross profit across active jobs with worksheets (SQLite + Postgres compatible).
    conn = db.connect()
    try:
        gp_row = conn.execute("""
            SELECT
                COALESCE(SUM(w.contract_value), 0)  AS total_contract,
                COALESCE(SUM(wl.budget_cost), 0)    AS total_cost
            FROM worksheets w
            JOIN jobs j ON j.id = w.job_id
            LEFT JOIN worksheet_lines wl ON wl.worksheet_id = w.id
            WHERE j.department = ?
              AND j.stage NOT IN ('closed','canceled')
        """, (dept,)).fetchone()
    finally:
        conn.close()
    gp_contract = float(gp_row[0]) if gp_row else 0.0
    gp_cost     = float(gp_row[1]) if gp_row else 0.0
    gp_dollars  = gp_contract - gp_cost
    gp_margin   = round(100 * gp_dollars / gp_contract, 1) if gp_contract else 0.0

    # Outstanding invoices (department-scoped) for the dashboard Send panel.
    from modules import quickbooks as qb
    from modules import invoices as invmod
    job_ids = {j["id"] for j in jobs}
    outstanding = [inv for inv in db.all_rows("invoices", order="id DESC")
                   if inv["status"] != "paid" and (inv.get("job_id") in job_ids or not inv.get("job_id"))]
    job_by_id = {j["id"]: j for j in jobs}
    for inv in outstanding:
        inv["_job"] = job_by_id.get(inv.get("job_id"))
        inv["_overdue"] = invmod._is_overdue(inv)
    invmod.sweep_overdue_automations(outstanding)
    outstanding_total = sum(i.get("amount") or 0 for i in outstanding)

    return render_template("dashboard.html", pipeline=pipeline, active_jobs=active_jobs,
                           overdue=overdue, feed=feed, win_rate=win_rate, won=won, lost=lost,
                           outstanding=outstanding, outstanding_total=outstanding_total,
                           qbo_connected=qb.is_connected(),
                           gp_dollars=gp_dollars, gp_margin=gp_margin,
                           gp_contract=gp_contract)


def _activity_name(a):
    et, eid = a.get("entity_type"), a.get("entity_id")
    if et == "lead":
        r = db.get("leads", eid)
        return r["name"] if r else ""
    if et == "job":
        r = db.get("jobs", eid)
        return r["name"] if r else ""
    if et == "contact":
        r = db.get("contacts", eid)
        return "%s %s" % (r["first_name"], r["last_name"]) if r else ""
    return ""
