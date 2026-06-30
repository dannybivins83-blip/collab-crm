# -*- coding: utf-8 -*-
"""Insurance claims / AOB / supplement workflow (AccuLynx parity).

Jobs already capture a few insurance columns, but there's no claim lifecycle:
no carrier/adjuster tracking, no supplement requests, no claim-status pipeline.

This blueprint adds a self-contained insurance claim per job:
  filed -> inspection -> approved -> supplementing -> paid -> closed
plus a child list of **supplements** (requested -> approved/denied) so a job's
final recoverable value = original estimate + approved supplements.

Tables are created here (CREATE TABLE IF NOT EXISTS) to match the auth.py /
commissions.py convention and avoid touching the shared SCHEMA string.
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash

import db
import theme

bp = Blueprint("claims", __name__, url_prefix="/claims")

STATUSES = ["filed", "inspection", "approved", "supplementing", "paid", "closed"]
SUPP_STATUSES = ["requested", "approved", "denied"]

db.execute("""CREATE TABLE IF NOT EXISTS claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created TEXT, updated TEXT, job_id INTEGER,
    carrier TEXT, claim_number TEXT, adjuster_name TEXT, adjuster_phone TEXT,
    deductible REAL DEFAULT 0, rcv REAL DEFAULT 0, acv REAL DEFAULT 0,
    status TEXT DEFAULT 'filed', date_filed TEXT, notes TEXT, department TEXT)""")

db.execute("""CREATE TABLE IF NOT EXISTS claim_supplements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created TEXT, claim_id INTEGER, description TEXT,
    amount_requested REAL DEFAULT 0, amount_approved REAL DEFAULT 0,
    status TEXT DEFAULT 'requested')""")


@bp.app_context_processor
def _inject():
    return {"job_claim": claim_for_job}


def claim_for_job(job_id):
    """Latest claim row for a job (or None) — used by job_detail to label its button."""
    rows = db.all_rows("claims", "job_id=?", (job_id,), "id DESC", 1)
    return rows[0] if rows else None


def _est_value(job):
    """Original estimate / contract value used as the supplement baseline."""
    if not job:
        return 0.0
    return theme.est_num(job.get("contract_value"))


def _supp_totals(claim_id):
    supps = db.all_rows("claim_supplements", "claim_id=?", (claim_id,), "id DESC")
    requested = sum(s.get("amount_requested") or 0 for s in supps)
    approved = sum(s.get("amount_approved") or 0 for s in supps
                   if s.get("status") == "approved")
    return supps, requested, approved


@bp.route("/")
def index():
    dept = theme.current_department()
    rows = db.all_rows("claims", "department=?", (dept,), "status, id DESC")
    jobs_map = {j["id"]: j for j in db.all_rows("jobs", "department=?", (dept,))}

    # Bulk-load supplement approved totals (avoid N+1).
    claim_ids = tuple(c["id"] for c in rows)
    appr_by_claim = {}
    if claim_ids:
        ph = ",".join("?" * len(claim_ids))
        for s in db.all_rows("claim_supplements", "claim_id IN (%s)" % ph, claim_ids):
            if s.get("status") == "approved":
                appr_by_claim[s["claim_id"]] = appr_by_claim.get(s["claim_id"], 0) + (s.get("amount_approved") or 0)

    status_f = request.args.get("status")
    q = (request.args.get("q") or "").strip().lower()
    for c in rows:
        c["_job"] = jobs_map.get(c["job_id"])
        c["_supp_approved"] = appr_by_claim.get(c["id"], 0)
        c["_total"] = _est_value(c["_job"]) + c["_supp_approved"]
    if status_f:
        rows = [c for c in rows if c["status"] == status_f]
    if q:
        rows = [c for c in rows if q in (
            (c.get("carrier") or "") + " " + (c.get("claim_number") or "") + " " +
            (c.get("adjuster_name") or "") + " " +
            ((c["_job"] or {}).get("name") or "") + " " +
            ((c["_job"] or {}).get("address") or "") + " " +
            (c.get("notes") or "")).lower()]

    return render_template("claims.html", view="list", rows=rows,
                           statuses=STATUSES, status_f=status_f, q=q)


@bp.route("/<int:cid>")
def detail(cid):
    c = db.get("claims", cid)
    if not c or c.get("department") != theme.current_department():
        flash("Claim not found.", "warn")
        return redirect(url_for("claims.index"))
    job = db.get("jobs", c["job_id"]) or {}
    supps, requested, approved = _supp_totals(cid)
    est = _est_value(job)
    return render_template("claims.html", view="detail", claim=c, job=job,
                           supps=supps, statuses=STATUSES, supp_statuses=SUPP_STATUSES,
                           est_value=est, supp_requested=requested,
                           supp_approved=approved, total=est + approved)


@bp.route("/for-job/<int:job_id>")
def for_job(job_id):
    """Entry point from job_detail: open the job's claim, creating one if needed."""
    job = db.get("jobs", job_id)
    if not job:
        flash("Job not found.", "warn")
        return redirect(url_for("claims.index"))
    existing = claim_for_job(job_id)
    if existing:
        return redirect(url_for("claims.detail", cid=existing["id"]))
    cid = db.insert("claims", {
        "job_id": job_id, "status": "filed", "date_filed": db.today(),
        "carrier": job.get("insurance_carrier") or "",
        "claim_number": job.get("claim_number") or "",
        "department": job.get("department"),
    })
    db.add_activity("job", job_id, "automation", "Insurance claim opened")
    return redirect(url_for("claims.detail", cid=cid))


@bp.route("/<int:cid>/save", methods=["POST"])
def save(cid):
    c = db.get("claims", cid)
    if not c or c.get("department") != theme.current_department():
        return redirect(url_for("claims.index"))
    f = request.form
    db.update("claims", cid,
              carrier=f.get("carrier", c.get("carrier") or ""),
              claim_number=f.get("claim_number", c.get("claim_number") or ""),
              adjuster_name=f.get("adjuster_name", c.get("adjuster_name") or ""),
              adjuster_phone=f.get("adjuster_phone", c.get("adjuster_phone") or ""),
              deductible=theme.est_num(f.get("deductible")),
              rcv=theme.est_num(f.get("rcv")),
              acv=theme.est_num(f.get("acv")),
              date_filed=f.get("date_filed", c.get("date_filed") or ""),
              notes=f.get("notes", c.get("notes") or ""),
              updated=db.now())
    flash("Claim updated.", "ok")
    return redirect(url_for("claims.detail", cid=cid))


@bp.route("/<int:cid>/status", methods=["POST"])
def status(cid):
    c = db.get("claims", cid)
    st = request.form.get("status")
    if c and c.get("department") == theme.current_department() and st in STATUSES:
        db.update("claims", cid, status=st, updated=db.now())
        if c.get("job_id"):
            db.add_activity("job", c["job_id"], "automation", "Claim status — %s" % st)
        flash("Claim marked %s." % st, "ok")
    return redirect(url_for("claims.detail", cid=cid))


@bp.route("/<int:cid>/supplement", methods=["POST"])
def add_supplement(cid):
    c = db.get("claims", cid)
    if not c or c.get("department") != theme.current_department():
        return redirect(url_for("claims.index"))
    desc = (request.form.get("description") or "").strip()
    if desc:
        db.insert("claim_supplements", {
            "claim_id": cid, "description": desc,
            "amount_requested": theme.est_num(request.form.get("amount_requested")),
            "amount_approved": 0, "status": "requested",
        })
        if c.get("job_id"):
            db.add_activity("job", c["job_id"], "automation", "Supplement requested — %s" % desc)
        flash("Supplement added.", "ok")
    return redirect(url_for("claims.detail", cid=cid))


@bp.route("/supplement/<int:sid>/status", methods=["POST"])
def supplement_status(sid):
    s = db.get("claim_supplements", sid)
    if not s:
        return redirect(url_for("claims.index"))
    claim = db.get("claims", s["claim_id"])
    if not claim or claim.get("department") != theme.current_department():
        return redirect(url_for("claims.index"))
    st = request.form.get("status")
    if st in SUPP_STATUSES:
        fields = {"status": st}
        if st == "approved":
            amt = request.form.get("amount_approved")
            fields["amount_approved"] = (theme.est_num(amt) if amt
                                         else (s.get("amount_requested") or 0))
        elif st == "denied":
            fields["amount_approved"] = 0
        db.update("claim_supplements", sid, **fields)
        flash("Supplement %s." % st, "ok")
    return redirect(url_for("claims.detail", cid=s["claim_id"]))
