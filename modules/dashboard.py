# -*- coding: utf-8 -*-
"""Dashboard — AccuLynx-style Current Pipeline (L/P/A/C/I) + activity feed."""
import time as _time

from flask import Blueprint, render_template, redirect, url_for

import db
import theme
import constants

bp = Blueprint("dashboard", __name__)

# Throttle sweep_overdue_automations to once per 5 min across all dashboard loads
# to avoid running automation rules on every GET. idempotency is already enforced
# per-invoice via overdue_fired_at, but we avoid the scan overhead entirely here.
_OVERDUE_SWEEP_INTERVAL = 300
_last_overdue_sweep = 0.0


def _bucket_of(kind, stage):
    if kind == "lead":
        return constants.lead_stage(stage).get("bucket")
    return constants.job_stage(stage).get("bucket")


@bp.route("/dashboard")
def dashboard_alias():
    return redirect(url_for("dashboard.home"), 301)


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
    overdue_lead_ct = sum(1 for k, _, _, _ in overdue if k == "lead")
    overdue_job_ct  = sum(1 for k, _, _, _ in overdue if k == "job")

    # Recent activity feed — scoped to this dept's leads + jobs.
    # Build the IN lists from already-loaded dept data so no extra query is needed.
    _lead_ids = tuple(l["id"] for l in leads)
    _job_ids  = tuple(j["id"] for j in jobs)
    _parts, _params = [], []
    if _lead_ids:
        _parts.append("(entity_type='lead' AND entity_id IN (%s))" % ",".join("?"*len(_lead_ids)))
        _params.extend(_lead_ids)
    if _job_ids:
        _parts.append("(entity_type='job' AND entity_id IN (%s))" % ",".join("?"*len(_job_ids)))
        _params.extend(_job_ids)
    _parts.append("entity_type NOT IN ('lead','job')")
    _feed_conn = db.connect()
    try:
        _feed_where = " OR ".join(_parts)
        feed = [dict(r) for r in _feed_conn.execute(
            "SELECT * FROM activities WHERE %s ORDER BY id DESC LIMIT 80" % _feed_where,
            tuple(_params)).fetchall()]
    finally:
        _feed_conn.close()
    _name_cache = {}
    for _l in leads:
        _name_cache[("lead", _l["id"])] = _l.get("name") or ""
    for _j in jobs:
        _name_cache[("job", _j["id"])] = _j.get("name") or ""
    contact_ids = {a["entity_id"] for a in feed if a.get("entity_type") == "contact"}
    if contact_ids:
        id_ph = ",".join("?" * len(contact_ids))
        for _c in db.all_rows("contacts", "id IN (%s)" % id_ph, tuple(contact_ids)):
            _name_cache[("contact", _c["id"])] = (
                "%s %s" % (_c.get("first_name", ""), _c.get("last_name", ""))).strip()
    for a in feed:
        a["_who"] = _name_cache.get((a.get("entity_type"), a.get("entity_id")), "")

    won = len([l for l in leads if l["stage"] == "won"])
    lost = len([l for l in leads if l["stage"] == "lost"])
    decided = won + lost
    win_rate = round(100 * won / decided) if decided else 0

    # Gross profit across active jobs with worksheets (SQLite + Postgres compatible).
    # Fix 4 (audit #critical-4): use a subquery to sum worksheet_lines costs per
    # worksheet BEFORE joining to jobs, eliminating the Cartesian product that
    # multiplied contract_value by the number of worksheet lines per job.
    conn = db.connect()
    try:
        gp_row = conn.execute("""
            SELECT
                COALESCE(SUM(w.contract_value), 0)  AS total_contract,
                COALESCE(SUM(wl_totals.total_cost), 0) AS total_cost
            FROM worksheets w
            JOIN jobs j ON j.id = w.job_id
            LEFT JOIN (
                SELECT worksheet_id, SUM(actual_cost) AS total_cost
                FROM worksheet_lines
                GROUP BY worksheet_id
            ) wl_totals ON wl_totals.worksheet_id = w.id
            WHERE j.department = ?
              AND j.stage NOT IN ('closed','canceled')
        """, (dept,)).fetchone()
    finally:
        conn.close()
    gp_contract = float(gp_row["total_contract"]) if gp_row else 0.0
    gp_cost     = float(gp_row["total_cost"])     if gp_row else 0.0
    gp_dollars  = gp_contract - gp_cost
    gp_margin   = round(100 * gp_dollars / gp_contract, 1) if gp_contract else 0.0

    # Outstanding invoices (department-scoped) for the dashboard Send panel.
    from modules import quickbooks as qb
    from modules import invoices as invmod
    job_ids = {j["id"] for j in jobs}
    if job_ids:
        _id_ph = ",".join("?" * len(job_ids))
        outstanding = db.all_rows(
            "invoices",
            "status != 'paid' AND (job_id IS NULL OR job_id IN (%s))" % _id_ph,
            tuple(job_ids), "id DESC")
    else:
        outstanding = db.all_rows("invoices", "status != 'paid' AND job_id IS NULL", order="id DESC")
    job_by_id = {j["id"]: j for j in jobs}
    for inv in outstanding:
        inv["_job"] = job_by_id.get(inv.get("job_id"))
        inv["_overdue"] = invmod._is_overdue(inv)
    global _last_overdue_sweep
    now = _time.time()
    if now - _last_overdue_sweep >= _OVERDUE_SWEEP_INTERVAL:
        invmod.sweep_overdue_automations(outstanding)
        _last_overdue_sweep = now
    outstanding_total = sum(i.get("amount") or 0 for i in outstanding)

    # SiteCam card: order chips by photo recency — jobs whose SiteCam gallery was
    # touched most recently come first (and the top one renders "selected"), the
    # rest stay alphabetical. Photo recency = latest "📸 SiteCam" automation entry;
    # jobs.sitecam_url marks which jobs have a gallery at all.
    _active = [j for j in jobs if j["stage"] not in constants.JOB_INACTIVE]
    _photo_ts = {}
    for a in db.all_rows("activities",
                         "entity_type='job' AND text LIKE '%SiteCam%'",
                         order="created DESC", limit=500):
        _photo_ts.setdefault(a.get("entity_id"), a.get("created") or "")
    for j in _active:
        j["_photo_ts"] = _photo_ts.get(j["id"]) or ""
        j["_has_photos"] = bool(j.get("sitecam_url") or j["_photo_ts"])
    active_job_list = sorted(
        _active,
        key=lambda j: (j["_photo_ts"], ""),  # newest photo activity first…
        reverse=True
    )
    # …but keep the no-photo tail alphabetical instead of reverse-alpha.
    _with = [j for j in active_job_list if j["_photo_ts"]]
    _without = sorted([j for j in active_job_list if not j["_photo_ts"]],
                      key=lambda j: (j.get("name") or "").lower())
    active_job_list = _with + _without
    return render_template("dashboard.html", pipeline=pipeline, active_jobs=active_jobs,
                           overdue=overdue, overdue_lead_ct=overdue_lead_ct,
                           overdue_job_ct=overdue_job_ct,
                           feed=feed, win_rate=win_rate, won=won, lost=lost,
                           outstanding=outstanding, outstanding_total=outstanding_total,
                           qbo_connected=qb.is_connected(),
                           gp_dollars=gp_dollars, gp_margin=gp_margin,
                           gp_contract=gp_contract,
                           active_job_list=active_job_list)
