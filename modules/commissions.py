# -*- coding: utf-8 -*-
"""Commissions (AccuLynx parity).

A sold job gets a **pre-commission** (computed from worksheet profit or contract
value); on completion it can be **approved**, then **paid**. Surfaced on the rep
leaderboard, a /commissions list, and a per-rep summary.

Table is created here (CREATE TABLE IF NOT EXISTS) to match the auth.py /
acculynx_sync.py convention and avoid touching the shared SCHEMA string.
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash

import db
import theme

bp = Blueprint("commissions", __name__, url_prefix="/commissions")

BASES = ["profit", "contract_value"]
STATUSES = ["pre", "approved", "paid"]
DEFAULT_RATE = 10.0

db.execute("""CREATE TABLE IF NOT EXISTS commissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created TEXT, updated TEXT, job_id INTEGER, rep TEXT,
    basis TEXT DEFAULT 'profit', rate_pct REAL DEFAULT 10, amount REAL DEFAULT 0,
    status TEXT DEFAULT 'pre', notes TEXT, department TEXT)""")


@bp.app_context_processor
def _inject():
    return {"job_commission": for_job}


def _compute(job, basis, rate):
    if basis == "contract_value":
        base = theme.est_num(job.get("contract_value"))
    else:
        from modules import worksheet as ws
        base = ws.profit_analysis(job["id"])["gross_profit"]
    return round(max(base, 0) * (rate or 0) / 100.0, 2)


def for_job(job_id):
    """Ensure a pre-commission exists for a sold job, recompute, and return it."""
    job = db.get("jobs", job_id)
    if not job:
        return None
    rows = db.all_rows("commissions", "job_id=?", (job_id,), "id DESC")
    if rows:
        c = rows[0]
    else:
        cid = db.insert("commissions", {"job_id": job_id, "rep": job.get("rep") or "—",
                                        "basis": "profit", "rate_pct": DEFAULT_RATE, "status": "pre",
                                        "department": job.get("department")})
        c = db.get("commissions", cid)
    # keep amount current with the worksheet/contract while still 'pre'
    if c["status"] == "pre":
        amt = _compute(job, c["basis"], c["rate_pct"])
        if amt != c["amount"]:
            db.update("commissions", c["id"], amount=amt)
            c["amount"] = amt
    return c


def summary_by_rep():
    rows = db.all_rows("commissions", "department=?", (theme.current_department(),))
    reps = {}
    for c in rows:
        r = reps.setdefault(c.get("rep") or "—", {"pre": 0.0, "approved": 0.0, "paid": 0.0, "count": 0})
        r[c["status"]] = r.get(c["status"], 0) + (c["amount"] or 0)
        r["count"] += 1
    return sorted(reps.items(), key=lambda kv: -(kv[1]["approved"] + kv[1]["paid"] + kv[1]["pre"]))


@bp.route("/")
def index():
    dept     = theme.current_department()
    # 2 bulk queries replace the previous 2N-query per-job loop.
    dept_jobs  = db.all_rows("jobs", "department=?", (dept,))
    jobs_map   = {j["id"]: j for j in dept_jobs}
    existing   = db.all_rows("commissions", "department=?", (dept,), "status, id DESC")
    has_comm   = {c["job_id"] for c in existing}

    # Insert defaults for active jobs that have no commission yet.
    created_any = False
    for j in dept_jobs:
        if j["stage"] not in ("canceled",) and j["id"] not in has_comm:
            db.insert("commissions", {
                "job_id": j["id"], "rep": j.get("rep") or "—",
                "basis": "profit", "rate_pct": DEFAULT_RATE, "status": "pre",
                "department": j.get("department"),
            })
            created_any = True

    all_commissions = (
        db.all_rows("commissions", "department=?", (dept,), "status, id DESC")
        if created_any else existing
    )

    # Batch-load worksheet profit for all pre-commissions with basis="profit" to
    # avoid calling profit_analysis() per job (2 DB queries each = N+1 pattern).
    _profit_jids = tuple(
        c["job_id"] for c in all_commissions
        if c["status"] == "pre" and c.get("basis", "profit") == "profit"
        and c["job_id"] in jobs_map
    )
    _ws_profit = {}
    if _profit_jids:
        _ph = ",".join("?" * len(_profit_jids))
        _ws_rows = db.all_rows("worksheets", "job_id IN (%s)" % _ph, _profit_jids)
        if _ws_rows:
            _ws_ids = tuple(w["id"] for w in _ws_rows)
            _wl_rows = db.all_rows("worksheet_lines",
                                   "worksheet_id IN (%s)" % ",".join("?" * len(_ws_ids)),
                                   _ws_ids)
            _wl_by_ws = {}
            for wl in _wl_rows:
                _wl_by_ws.setdefault(wl["worksheet_id"], []).append(wl)
            for w in _ws_rows:
                lines = _wl_by_ws.get(w["id"], [])
                actual = sum(l.get("actual_cost") or 0 for l in lines)
                cv = w.get("contract_value") or 0
                _ws_profit[w["job_id"]] = cv - actual
        for jid in _profit_jids:
            if jid not in _ws_profit:
                _ws_profit[jid] = theme.est_num(jobs_map[jid].get("contract_value"))

    # Recompute pre-commission amounts in-place (uses jobs_map + _ws_profit — no per-job re-fetch).
    for c in all_commissions:
        if c["status"] == "pre" and c["job_id"] in jobs_map:
            if c.get("basis", "profit") == "profit":
                base = _ws_profit.get(c["job_id"], 0)
                amt = round(max(base, 0) * (c["rate_pct"] or 0) / 100.0, 2)
            else:
                amt = _compute(jobs_map[c["job_id"]], c["basis"], c["rate_pct"])
            if amt != c["amount"]:
                db.update("commissions", c["id"], amount=amt)
                c["amount"] = amt

    # Build summary inline — avoids summary_by_rep() re-querying the DB.
    reps_agg = {}
    for c in all_commissions:
        r = reps_agg.setdefault(c.get("rep") or "—",
                                {"pre": 0.0, "approved": 0.0, "paid": 0.0, "count": 0})
        r[c["status"]] = r.get(c["status"], 0) + (c["amount"] or 0)
        r["count"] += 1
    summary = sorted(reps_agg.items(),
                     key=lambda kv: -(kv[1]["approved"] + kv[1]["paid"] + kv[1]["pre"]))

    status_f = request.args.get("status")
    rep_f    = request.args.get("rep")
    q        = (request.args.get("q") or "").strip().lower()
    rows     = list(all_commissions)
    if status_f:
        rows = [c for c in rows if c["status"] == status_f]
    if rep_f:
        rows = [c for c in rows if (c.get("rep") or "") == rep_f]
    for c in rows:
        c["_job"] = jobs_map.get(c["job_id"])
    if q:
        rows = [c for c in rows if q in (
            (c.get("rep") or "") + " " + ((c["_job"] or {}).get("name") or "") +
            " " + ((c["_job"] or {}).get("address") or "") +
            " " + (c.get("notes") or "")).lower()]
    return render_template("commissions.html", rows=rows, summary=summary,
                           statuses=STATUSES, bases=BASES,
                           reps=sorted({c.get("rep") for c in all_commissions if c.get("rep")}),
                           status_f=status_f, rep_f=rep_f, q=q)


@bp.route("/<int:cid>/save", methods=["POST"])
def save(cid):
    c = db.get("commissions", cid)
    if not c:
        return redirect(url_for("commissions.index"))
    job = db.get("jobs", c["job_id"]) or {}
    basis = request.form.get("basis", c["basis"])
    # est_num coerces a non-numeric rate ("abc") -> 0.0 instead of float() 500'ing.
    rate = theme.est_num(request.form.get("rate_pct") or c["rate_pct"])
    db.update("commissions", cid, basis=basis, rate_pct=rate, rep=request.form.get("rep", c["rep"]),
              notes=request.form.get("notes", ""), amount=_compute(job, basis, rate))
    flash("Commission updated.", "ok")
    return redirect(url_for("commissions.index"))


@bp.route("/<int:cid>/status", methods=["POST"])
def status(cid):
    st = request.form.get("status")
    c = db.get("commissions", cid)
    if c and st in STATUSES:
        db.update("commissions", cid, status=st)
        if c.get("job_id"):
            db.add_activity("job", c["job_id"], "automation",
                            "Commission %s — %s for %s" % (st, theme.money(c["amount"]), c.get("rep")))
        flash("Commission marked %s." % st, "ok")
    return redirect(url_for("commissions.index"))
