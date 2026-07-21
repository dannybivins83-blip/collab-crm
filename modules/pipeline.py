# -*- coding: utf-8 -*-
"""Unified pipeline board — one view of the whole customer journey across BOTH
the lead and job modules: Lead -> Prospect -> Approved -> Completed -> Invoiced
-> Closed. Leads fill the first two columns; jobs fill the rest. Read-only board
(click a card to open the record); large columns are capped with a 'view all' link."""
from flask import Blueprint, render_template, url_for

import db
import theme
import constants
from theme import current_department

bp = Blueprint("pipeline", __name__, url_prefix="/pipeline")

CAP = 40  # max cards shown per column (closed/invoiced can be huge)


def _card(kind, r):
    stage = r.get("stage") or ""
    sd = constants.lead_stage(stage) if kind == "lead" else constants.job_stage(stage)
    val = theme.est_num(r.get("estimate") if kind == "lead" else r.get("contract_value"))
    clock = r.get("last_contact") or r.get("created") if kind == "lead" else r.get("stage_since") or r.get("created")
    fs = theme.follow_status(sd, clock, r.get("snooze_until"))
    # Current-stage checklist as quick-toggle tasks.
    checks = db.load_json(r.get("checks"), {})
    items = sd.get("checklist", [])
    tasks = [{"label": it, "key": "%s:%d" % (sd["key"], i),
              "done": bool(checks.get("%s:%d" % (sd["key"], i)))}
             for i, it in enumerate(items)]
    ns = constants.next_step(kind, stage)
    if kind == "lead":
        href = url_for("leads.detail", lead_id=r["id"])
        check_url = url_for("leads.check", lead_id=r["id"])
        advance_url = (url_for("leads.convert", lead_id=r["id"])
                       if (ns and ns["action"] == "convert")
                       else url_for("leads.set_stage", lead_id=r["id"]))
    else:
        href = url_for("jobs.detail", job_id=r["id"])
        check_url = url_for("jobs.check", job_id=r["id"])
        advance_url = url_for("jobs.set_stage", job_id=r["id"])
    return {
        "kind": kind, "id": r["id"], "name": r.get("name") or "—",
        "rid": r.get("rid") or "", "stage_name": sd.get("name", stage),
        "color": sd.get("color", "#8aa"), "value": val, "rep": r.get("rep") or "",
        "address": (r.get("address") or ""), "since": r.get("stage_since") or r.get("created") or "",
        "href": href, "tasks": tasks, "next": ns, "check_url": check_url,
        "advance_url": advance_url, "done_count": sum(1 for t in tasks if t["done"]),
        "fs": fs, "snooze_until": r.get("snooze_until") or "",
        "todo": (r.get("todo") or "").strip(),
    }


@bp.route("/")
def board():
    dept = current_department()
    cols = {s["key"]: {"def": s, "items": [], "value": 0.0, "count": 0}
            for s in constants.LIFECYCLE}
    # ACTIVE records — loaded in full; these are the working columns.
    # A `won` lead is excluded because the job record already represents it in the
    # Approved column (including both would double-count the same customer).
    for l in db.all_rows("leads", "department=? AND stage NOT IN ('won','lost')", (dept,)):
        step = constants.lifecycle_step("lead", l.get("stage") or "")
        c = cols[step]
        c["count"] += 1
        card = _card("lead", l)
        c["value"] += card["value"]
        c["items"].append(card)
    for j in db.all_rows("jobs", "department=? AND stage NOT IN ('closed','canceled')", (dept,)):
        step = constants.lifecycle_step("job", j.get("stage") or "")
        c = cols[step]
        c["count"] += 1
        card = _card("job", j)
        c["value"] += card["value"]
        c["items"].append(card)

    # TERMINAL records → the 'Closed' column. These queries used to be excluded
    # outright, but leads `lost` and jobs `closed`/`canceled` are the ONLY stages
    # constants.lifecycle_step() maps to "closed" — so the 6th column rendered
    # "No records" permanently, no matter the data. They are included now, but
    # BOUNDED: the true count/value come from SQL aggregates while only CAP rows
    # are materialized into cards, so a table with thousands of archived jobs
    # can't turn this page into a full-table scan.
    _closed = cols["closed"]
    for table, where, params, value_col in (
            ("leads", "department=? AND stage='lost'", (dept,), "estimate"),
            ("jobs", "department=? AND stage IN ('closed','canceled')", (dept,),
             "contract_value")):
        _conn = db.connect()
        try:
            _agg = _conn.execute(
                "SELECT COUNT(*) n, COALESCE(SUM(CAST(REPLACE(REPLACE(COALESCE(%s,'0'),"
                "'$',''),',','') AS REAL)),0) v FROM %s WHERE %s" % (value_col, table, where),
                params).fetchone()
        finally:
            _conn.close()
        _closed["count"] += (_agg["n"] if _agg else 0) or 0
        _closed["value"] += (_agg["v"] if _agg else 0) or 0.0
        kind = "lead" if table == "leads" else "job"
        # `id DESC` (newest first) picks WHICH CAP rows to materialize; the exact
        # display order is re-sorted by stage_since in Python below. db.all_rows
        # allowlists ORDER BY to bare column names, so no expression here.
        for r in db.all_rows(table, where, params, "id DESC", limit=CAP):
            _closed["items"].append(_card(kind, r))
    # newest first within each column, then cap for render
    columns = []
    for s in constants.LIFECYCLE:
        c = cols[s["key"]]
        c["items"].sort(key=lambda x: x["since"], reverse=True)
        c["shown"] = c["items"][:CAP]
        c["more"] = max(0, c["count"] - CAP)
        columns.append(c)
    totals = {"count": sum(c["count"] for c in columns),
              "value": sum(c["value"] for c in columns)}
    return render_template("pipeline_board.html", columns=columns, totals=totals)
