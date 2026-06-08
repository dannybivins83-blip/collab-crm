# -*- coding: utf-8 -*-
"""Reports & dashboards — pipeline value, win rate, revenue, leaderboard, aging."""
from flask import Blueprint, render_template

import db
import theme
import constants

bp = Blueprint("reports", __name__, url_prefix="/reports")


@bp.route("/")
def index():
    leads = db.all_rows("leads")
    jobs = db.all_rows("jobs")
    invoices = db.all_rows("invoices")

    # Pipeline by lead stage.
    lead_rows = []
    for s in constants.LEAD_STAGES:
        items = [l for l in leads if l["stage"] == s["key"]]
        lead_rows.append({"name": s["name"], "count": len(items),
                          "value": sum(theme.est_num(l.get("estimate")) for l in items)})

    # Production by job stage.
    job_rows = []
    for s in constants.JOB_STAGES:
        items = [j for j in jobs if j["stage"] == s["key"]]
        job_rows.append({"name": s["name"], "count": len(items),
                         "value": sum(theme.est_num(j.get("contract_value")) for j in items)})

    won = [l for l in leads if l["stage"] == "won"]
    lost = [l for l in leads if l["stage"] == "lost"]
    decided = len(won) + len(lost)
    win_rate = round(100 * len(won) / decided) if decided else 0

    # Revenue: collected invoices + won contract value.
    revenue_collected = sum(i["amount"] or 0 for i in invoices if i["status"] == "paid")
    outstanding = sum(i["amount"] or 0 for i in invoices if i["status"] != "paid")

    # Leaderboard by rep (won value + active job value).
    board = {}
    for l in won:
        board.setdefault(l.get("rep") or "—", {"won": 0, "deals": 0})
        board[l.get("rep") or "—"]["won"] += theme.est_num(l.get("estimate"))
        board[l.get("rep") or "—"]["deals"] += 1
    leaderboard = sorted(board.items(), key=lambda kv: -kv[1]["won"])

    # Overdue follow-ups count by source.
    by_source = {}
    for l in leads:
        if l["stage"] in ("won", "lost"):
            continue
        sd = constants.lead_stage(l["stage"])
        fs = theme.follow_status(sd, l.get("last_contact") or l.get("created"), l.get("snooze_until"))
        src = l.get("source") or "—"
        by_source.setdefault(src, {"open": 0, "overdue": 0, "value": 0})
        by_source[src]["open"] += 1
        by_source[src]["value"] += theme.est_num(l.get("estimate"))
        if fs["level"] != "ok":
            by_source[src]["overdue"] += 1
    source_rows = sorted(by_source.items(), key=lambda kv: -kv[1]["value"])

    return render_template("reports.html",
                           lead_rows=lead_rows, job_rows=job_rows,
                           win_rate=win_rate, won=len(won), lost=len(lost),
                           pipeline_value=sum(r["value"] for r in lead_rows[:-2]),
                           job_value=sum(r["value"] for r in job_rows[:-1]),
                           revenue_collected=revenue_collected, outstanding=outstanding,
                           leaderboard=leaderboard, source_rows=source_rows)
