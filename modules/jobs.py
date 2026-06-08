# -*- coding: utf-8 -*-
"""Jobs / production pipeline — board, detail, checklists, draw schedule, payments."""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify

import db
import theme
import constants
from theme import current_department

bp = Blueprint("jobs", __name__, url_prefix="/jobs")

EDITABLE = ["rid", "name", "phone", "email", "address", "city", "state", "zip",
            "work_type", "rep", "source", "contract_value", "narrative", "todo", "notes",
            "next_follow", "pcn", "legal", "county", "ahj", "system", "existing",
            "area", "slope", "mrh", "exposure", "external_url", "contact_id"]


def _decorate(j):
    sd = constants.job_stage(j["stage"])
    fs = theme.follow_status(sd, j.get("stage_since") or j.get("created"), j.get("snooze_until"))
    checks = db.load_json(j.get("checks"), {})
    payments = db.load_json(j.get("payments"), {})
    done = sum(1 for i in range(len(sd["checklist"])) if checks.get("%s:%d" % (sd["key"], i)))
    j["_stage"] = sd
    j["_fs"] = fs
    j["_checks"] = checks
    j["_payments"] = payments
    j["_done"] = done
    j["_total"] = len(sd["checklist"])
    j["_pct"] = round(100 * done / len(sd["checklist"])) if sd["checklist"] else 0
    j["_paid_pct"] = theme.paid_pct(payments)
    return j


@bp.route("/")
def board():
    jobs = [_decorate(j) for j in db.all_rows("jobs", "department=?", (current_department(),))]
    # Optional filters from the nav: ?bucket=approved or ?stage=closed
    bucket = request.args.get("bucket")
    stage_f = request.args.get("stage")
    if bucket:
        jobs = [j for j in jobs if j["_stage"].get("bucket") == bucket]
    if stage_f:
        jobs = [j for j in jobs if j["stage"] == stage_f]
    sort = request.args.get("sort", "clock")
    if sort == "est":
        jobs.sort(key=lambda j: -theme.est_num(j.get("contract_value")))
    elif sort == "name":
        jobs.sort(key=lambda j: (j.get("name") or "").lower())
    else:
        jobs.sort(key=lambda j: -j["_fs"]["days"])
    cols = []
    grand = 0
    overdue = 0
    for s in constants.JOB_STAGES:
        items = [j for j in jobs if j["stage"] == s["key"]]
        tot = sum(theme.est_num(j.get("contract_value")) for j in items)
        if s["key"] not in constants.JOB_INACTIVE:
            grand += tot
            overdue += sum(1 for j in items if j["_fs"]["level"] == "hot")
        # Keep the board readable: show a column if it has cards, or matches an active filter.
        show = bool(items) or (bucket and s.get("bucket") == bucket) or (stage_f == s["key"])
        if show:
            cols.append({"stage": s, "cards": items, "total": tot})
    return render_template("jobs_board.html", cols=cols, grand=grand, overdue=overdue, sort=sort,
                           bucket=bucket, stage_f=stage_f, buckets=constants.BUCKETS)


@bp.route("/list")
def list_view():
    """AccuLynx jobs list with the full milestone-pipeline filter sidebar."""
    jobs = [_decorate(j) for j in db.all_rows("jobs", "department=?", (current_department(),))]
    stage_f = request.args.get("stage")
    bucket = request.args.get("bucket")
    q = (request.args.get("q") or "").strip().lower()
    counts = {s["key"]: 0 for s in constants.JOB_STAGES}
    for j in jobs:
        counts[j["stage"]] = counts.get(j["stage"], 0) + 1
    rows = jobs
    if stage_f:
        rows = [j for j in rows if j["stage"] == stage_f]
    if bucket:
        rows = [j for j in rows if j["_stage"].get("bucket") == bucket]
    if q:
        rows = [j for j in rows if q in ((j.get("name") or "") + (j.get("address") or "") +
                                         (j.get("rid") or "")).lower()]
    rows.sort(key=lambda j: -j["_fs"]["days"])
    return render_template("jobs_list.html", rows=rows, counts=counts, stage_f=stage_f,
                           bucket=bucket, q=q, total=len(jobs),
                           stages=constants.JOB_STAGES, buckets=constants.BUCKETS)


@bp.route("/new", methods=["GET", "POST"])
def new():
    if request.method == "POST":
        data = {f: request.form.get(f, "").strip() for f in EDITABLE}
        data["stage"] = request.form.get("stage", constants.JOB_DEFAULT_STAGE)
        data["stage_since"] = db.today()
        data["department"] = current_department()
        lid = db.insert("jobs", data)
        db.add_activity("job", lid, "stage", "Job created")
        flash("Job created.", "ok")
        return redirect(url_for("jobs.detail", job_id=lid))
    return render_template("job_form.html", job={}, contacts=db.all_rows("contacts", order="last_name"),
                           mode="new")


@bp.route("/<int:job_id>")
def detail(job_id):
    j = db.get("jobs", job_id)
    if not j:
        return redirect(url_for("jobs.board"))
    _decorate(j)
    from modules import measurements as meas
    idx = constants.JOB_STAGE_INDEX.get(j["stage"], 0)
    next_stage = constants.JOB_STAGES[idx + 1] if idx < len(constants.JOB_STAGES) - 1 else None
    return render_template("job_detail.html", j=j, measurement=meas.for_job(job_id),
                           meas_fields=meas.FIELDS,
                           activity=db.entity_activity("job", job_id),
                           estimates=db.all_rows("estimates", "job_id=?", (job_id,)),
                           documents=db.all_rows("documents", "job_id=?", (job_id,)),
                           photos=db.all_rows("photos", "job_id=?", (job_id,)),
                           permits=db.all_rows("permits", "job_id=?", (job_id,)),
                           invoices=db.all_rows("invoices", "job_id=?", (job_id,)),
                           materials=db.all_rows("materials", "job_id=?", (job_id,)),
                           draws=constants.DRAW_SCHEDULE, buckets=constants.BUCKETS,
                           cur_bucket=j["_stage"].get("bucket"), next_stage=next_stage,
                           cur_bucket_index=next((i for i, b in enumerate(constants.BUCKETS)
                                                  if b["key"] == j["_stage"].get("bucket")), 0),
                           all_stages=constants.JOB_STAGES)


@bp.route("/<int:job_id>/edit", methods=["GET", "POST"])
def edit(job_id):
    j = db.get("jobs", job_id)
    if not j:
        return redirect(url_for("jobs.board"))
    if request.method == "POST":
        data = {f: request.form.get(f, "").strip() for f in EDITABLE}
        db.update("jobs", job_id, **data)
        flash("Job updated.", "ok")
        return redirect(url_for("jobs.detail", job_id=job_id))
    return render_template("job_form.html", job=j, contacts=db.all_rows("contacts", order="last_name"),
                           mode="edit")


@bp.route("/<int:job_id>/stage", methods=["POST"])
def set_stage(job_id):
    stage = request.form.get("stage")
    if stage in constants.JOB_STAGE_INDEX:
        db.update("jobs", job_id, stage=stage, stage_since=db.today())
        db.add_activity("job", job_id, "stage", "Moved to %s" % constants.job_stage(stage)["name"])
        flash("Stage updated.", "ok")
    return redirect(url_for("jobs.detail", job_id=job_id))


@bp.route("/<int:job_id>/advance", methods=["POST"])
def advance(job_id):
    """Move the job to the next milestone (AccuLynx 'Advance Job')."""
    j = db.get("jobs", job_id)
    if j:
        idx = constants.JOB_STAGE_INDEX.get(j["stage"], 0)
        if idx < len(constants.JOB_STAGES) - 1:
            nxt = constants.JOB_STAGES[idx + 1]["key"]
            db.update("jobs", job_id, stage=nxt, stage_since=db.today())
            db.add_activity("job", job_id, "stage", "Advanced to %s" % constants.job_stage(nxt)["name"])
            flash("Advanced to %s." % constants.job_stage(nxt)["name"], "ok")
    return redirect(url_for("jobs.detail", job_id=job_id))


@bp.route("/<int:job_id>/move", methods=["POST"])
def move(job_id):
    stage = (request.get_json(silent=True) or {}).get("stage") or request.form.get("stage")
    if stage in constants.JOB_STAGE_INDEX:
        db.update("jobs", job_id, stage=stage, stage_since=db.today())
        db.add_activity("job", job_id, "stage", "Moved to %s" % constants.job_stage(stage)["name"])
        return jsonify({"ok": True})
    return jsonify({"ok": False}), 400


@bp.route("/<int:job_id>/check", methods=["POST"])
def check(job_id):
    j = db.get("jobs", job_id)
    checks = db.load_json(j.get("checks"), {})
    key = request.form.get("key")
    checks[key] = not checks.get(key)
    db.update("jobs", job_id, checks=db.dump_json(checks))
    return redirect(url_for("jobs.detail", job_id=job_id))


@bp.route("/<int:job_id>/pay", methods=["POST"])
def pay(job_id):
    j = db.get("jobs", job_id)
    payments = db.load_json(j.get("payments"), {})
    key = request.form.get("key")
    if key == "woodAmt":
        payments["woodAmt"] = request.form.get("value", "")
    else:
        payments[key] = not payments.get(key)
    db.update("jobs", job_id, payments=db.dump_json(payments))
    return redirect(url_for("jobs.detail", job_id=job_id))


@bp.route("/<int:job_id>/note", methods=["POST"])
def note(job_id):
    text = request.form.get("text", "").strip()
    kind = request.form.get("kind", "note")
    if text:
        db.add_activity("job", job_id, kind, text)
    return redirect(url_for("jobs.detail", job_id=job_id))


@bp.route("/<int:job_id>/delete", methods=["POST"])
def delete(job_id):
    db.delete("jobs", job_id)
    flash("Job deleted.", "ok")
    return redirect(url_for("jobs.board"))
