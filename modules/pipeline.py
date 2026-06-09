# -*- coding: utf-8 -*-
"""Unified pipeline board — one view of the whole customer journey across BOTH
the lead and job modules: Lead -> Prospect -> Approved -> Completed -> Invoiced
-> Closed. Leads fill the first two columns; jobs fill the rest. Read-only board
(click a card to open the record); large columns are capped with a 'view all' link."""
from flask import Blueprint, render_template, url_for

import db
import theme
import constants

bp = Blueprint("pipeline", __name__, url_prefix="/pipeline")

CAP = 40  # max cards shown per column (closed/invoiced can be huge)


def _card(kind, r):
    stage = r.get("stage") or ""
    sd = constants.lead_stage(stage) if kind == "lead" else constants.job_stage(stage)
    val = theme.est_num(r.get("estimate") if kind == "lead" else r.get("contract_value"))
    if kind == "lead":
        href = url_for("leads.detail", lead_id=r["id"])
    else:
        href = url_for("jobs.detail", job_id=r["id"])
    return {
        "kind": kind, "id": r["id"], "name": r.get("name") or "—",
        "rid": r.get("rid") or "", "stage_name": sd.get("name", stage),
        "color": sd.get("color", "#8aa"), "value": val, "rep": r.get("rep") or "",
        "address": (r.get("address") or ""), "since": r.get("stage_since") or r.get("created") or "",
        "href": href,
    }


@bp.route("/")
def board():
    cols = {s["key"]: {"def": s, "items": [], "value": 0.0, "count": 0}
            for s in constants.LIFECYCLE}
    for l in db.all_rows("leads"):
        step = constants.lifecycle_step("lead", l.get("stage") or "")
        c = cols[step]
        c["count"] += 1
        card = _card("lead", l)
        c["value"] += card["value"]
        c["items"].append(card)
    for j in db.all_rows("jobs"):
        step = constants.lifecycle_step("job", j.get("stage") or "")
        c = cols[step]
        c["count"] += 1
        card = _card("job", j)
        c["value"] += card["value"]
        c["items"].append(card)
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
