# -*- coding: utf-8 -*-
"""Jobs / production pipeline — board, detail, checklists, draw schedule, payments."""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, abort

import db
import theme
import constants
from theme import current_department

bp = Blueprint("jobs", __name__, url_prefix="/jobs")

EDITABLE = ["rid", "name", "phone", "email", "address", "city", "state", "zip",
            "work_type", "rep", "source", "contract_value", "narrative", "todo", "notes",
            "next_follow", "pcn", "legal", "county", "ahj", "system", "existing",
            "area", "slope", "mrh", "exposure", "external_url", "contact_id", "pay_url",
            "sitecam_url"]


def _require_job(job_id):
    """Fetch job and verify caller's department owns it. Aborts 404/403 as needed."""
    j = db.get("jobs", job_id)
    if not j:
        abort(404)
    from modules.auth import current_user as _cu
    u = _cu() or {}
    if u.get("role") != "admin" and j.get("department") != current_department():
        abort(403)
    return j


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
    # When AccuLynx billing is synced, show ITS exact Balance Due / Collected — the
    # job detail header computes balance = value*(1 - _paid_pct), so deriving _paid_pct
    # from the stored balance reproduces AccuLynx to the penny.
    val = theme.est_num(j.get("contract_value"))
    bal = j.get("balance")
    if bal not in (None, "") and val:
        j["_balance"] = theme.est_num(bal)
        j["_collected"] = max(0.0, val - j["_balance"])
        j["_paid_pct"] = j["_collected"] / val
        j["_billing_synced"] = True
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
    sort = request.args.get("sort", "date")
    if sort == "est":
        jobs.sort(key=lambda j: -theme.est_num(j.get("contract_value")))
    elif sort == "name":
        jobs.sort(key=lambda j: (j.get("name") or "").lower())
    elif sort == "clock":
        jobs.sort(key=lambda j: -j["_fs"]["days"])
    else:  # date — newest first
        jobs.sort(key=lambda j: -(j.get("id") or 0))
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
                                         (j.get("rid") or "") + (j.get("phone") or "") +
                                         (j.get("work_type") or "")).lower()]
    rep_f = request.args.get("rep")
    if rep_f:
        rows = [j for j in rows if (j.get("rep") or "") == rep_f]
    # Sort options for the bucket views.
    sort = request.args.get("sort", "date")
    if sort == "recent":
        rows.sort(key=lambda j: (j.get("stage_since") or j.get("created") or ""), reverse=True)
    elif sort == "days":
        rows.sort(key=lambda j: -j["_fs"]["days"])
    elif sort == "value":
        rows.sort(key=lambda j: -theme.est_num(j.get("contract_value")))
    elif sort == "name":
        rows.sort(key=lambda j: (j.get("name") or "").lower())
    elif sort == "rid":
        rows.sort(key=lambda j: (j.get("rid") or "").lower())
    else:  # date — newest first
        rows.sort(key=lambda j: -(j.get("id") or 0))
    reps = sorted({(j.get("rep") or "").strip() for j in jobs if (j.get("rep") or "").strip()})
    return render_template("jobs_list.html", rows=rows, counts=counts, stage_f=stage_f,
                           bucket=bucket, q=q, total=len(jobs), sort=sort, rep_f=rep_f, reps=reps,
                           stages=constants.JOB_STAGES, buckets=constants.BUCKETS)


@bp.route("/new", methods=["GET", "POST"])
def new():
    if request.method == "POST":
        data = {f: request.form.get(f, "").strip() for f in EDITABLE}
        data["stage"] = request.form.get("stage", constants.JOB_DEFAULT_STAGE)
        data["stage_since"] = db.today()
        data["department"] = current_department()
        # Auto-compose the canonical job number + name. The form sends the raw client
        # name in `client` (or name); we format it as R-YY###: Client (AHJ) (RoofCode) (Rep).
        from modules import acculynx_sync as S
        if not (data.get("rid") or "").strip():
            data["rid"] = S.next_job_number()
        _client = (request.form.get("client") or data.get("name") or "").strip()
        if request.form.get("auto_name") or not (data.get("name") or "").strip():
            data["name"] = S.compose_job_name(
                _client, ahj=data.get("ahj") or "", work_type=data.get("work_type") or "",
                system=data.get("system") or "", squares=data.get("area") or "",
                rep=data.get("rep") or "", rid=data.get("rid"))
        lid = db.insert("jobs", data)
        gc = db.get("contacts", data.get("contact_id")) if data.get("contact_id") else None
        if gc and gc.get("is_gc"):
            gname = ("%s %s" % (gc.get("first_name") or "", gc.get("last_name") or "")).strip() \
                or gc.get("company") or "GC"
            db.add_activity("job", lid, "stage", "Job created under GC %s" % gname)
            db.add_activity("contact", gc["id"], "note", "New job created under this GC: %s" % data.get("name"))
        else:
            db.add_activity("job", lid, "stage", "Job created")
        flash("Job created.", "ok")
        return redirect(url_for("jobs.detail", job_id=lid))
    return render_template("job_form.html", job=_prefill_from_gc(), gc_id=request.args.get("gc"),
                           contacts=db.all_rows("contacts", order="last_name"), mode="new")


def _prefill_from_gc():
    """When New Job is opened as ?gc=<contact_id>, pre-fill the GC's name/company/
    phone/email/rep so the user only needs to add the new property + work type."""
    gid = request.args.get("gc")
    if not gid or not gid.isdigit():
        return {}
    g = db.get("contacts", int(gid))
    if not g or not g.get("is_gc"):
        return {}
    name = (((g.get("first_name") or "") + " " + (g.get("last_name") or "")).strip()
            or g.get("company") or "")
    return {"name": name, "phone": g.get("phone") or "", "email": g.get("email") or "",
            "contact_id": g["id"], "rep": g.get("rep") or "", "state": g.get("state") or "FL"}


@bp.route("/<int:job_id>")
def detail(job_id):
    j = _require_job(job_id)
    _decorate(j)
    # Auto-materialize the worksheet from the estimate so Profit Analysis fills in without
    # opening the worksheet + clicking Seed. Only when an estimate with line items exists
    # (get_or_create seeds new/placeholder worksheets, never clobbers a built-out one).
    try:
        from modules import worksheet as _ws
        if db.all_rows("estimates", "job_id=?", (job_id,)):
            _ws.get_or_create(job_id)
    except Exception:
        pass
    from modules import measurements as meas
    idx = constants.JOB_STAGE_INDEX.get(j["stage"], 0)
    next_stage = constants.JOB_STAGES[idx + 1] if idx < len(constants.JOB_STAGES) - 1 else None
    # Quick Estimate scoped to the job's tagged system (work type from sign-up), not the full catalog.
    _k = constants.template_for_work_type(j.get("work_type") or "")
    quick_templates = [t for t in db.all_rows("templates", order="name")
                       if constants.template_for_work_type(t.get("work_type") or "") == _k and _k != "blank"]
    try:
        job_expenses = db.all_rows("job_expenses", "job_id=?", (job_id,), "payment_date DESC")
    except Exception:
        job_expenses = []
    try:
        stage_history = db.all_rows("job_stage_history", "job_id=?", (job_id,), "started_at")
    except Exception:
        stage_history = []
    return render_template("job_detail.html", j=j, measurement=meas.for_job(job_id),
                           quick_templates=quick_templates,
                           meas_fields=meas.FIELDS,
                           activity=db.entity_activity("job", job_id),
                           estimates=db.all_rows("estimates", "job_id=?", (job_id,)),
                           documents=db.all_rows("documents", "job_id=?", (job_id,)),
                           photos=db.all_rows("photos", "job_id=?", (job_id,)),
                           permits=db.all_rows("permits", "job_id=?", (job_id,)),
                           invoices=db.all_rows("invoices", "job_id=?", (job_id,)),
                           materials=db.all_rows("materials", "job_id=?", (job_id,)),
                           job_expenses=job_expenses, stage_history=stage_history,
                           draws=constants.DRAW_SCHEDULE, buckets=constants.BUCKETS,
                           cur_bucket=j["_stage"].get("bucket"), next_stage=next_stage,
                           cur_bucket_index=next((i for i, b in enumerate(constants.BUCKETS)
                                                  if b["key"] == j["_stage"].get("bucket")), 0),
                           all_stages=constants.JOB_STAGES)


@bp.route("/<int:job_id>/edit", methods=["GET", "POST"])
def edit(job_id):
    j = _require_job(job_id)
    if request.method == "POST":
        data = {f: request.form.get(f, "").strip() for f in EDITABLE}
        db.update("jobs", job_id, **data)
        flash("Job updated.", "ok")
        return redirect(url_for("jobs.detail", job_id=job_id))
    return render_template("job_form.html", job=j, contacts=db.all_rows("contacts", order="last_name"),
                           mode="edit")


def _notify_phase(job_id, old_stage, new_stage):
    """Fire the homeowner milestone update (portal feed + optional email) when a stage
    change moves the job into a new customer-facing phase. Never raises."""
    try:
        from flask import session
        from modules import portal
        op, np = portal._phase_index(old_stage or ""), portal._phase_index(new_stage or "")
        if np > op:
            portal.on_phase_advance(job_id, op, np, session.get("user_id"))
    except Exception:
        pass


def _incomplete_on_stage(job, stage_key):
    """How many checklist items are still unchecked on `stage_key` (0 = complete/none).
    Audit #8: checklists are advisory — we WARN on a forward move that leaves items
    unchecked (e.g. skipping permits/inspections), but never block (owner's call)."""
    sd = constants.job_stage(stage_key)
    checklist = sd.get("checklist") or []
    if not checklist:
        return 0
    checks = db.load_json(job.get("checks"), {})
    return sum(1 for i in range(len(checklist))
               if not checks.get("%s:%d" % (sd["key"], i)))


def _skip_warning(job, from_stage, to_stage):
    """A friendly 'you left N items unchecked' string for a FORWARD move, else ''."""
    fi = constants.JOB_STAGE_INDEX.get(from_stage, 0)
    ti = constants.JOB_STAGE_INDEX.get(to_stage, 0)
    if ti <= fi:
        return ""  # backward / same — nothing to warn about
    n = _incomplete_on_stage(job, from_stage)
    if not n:
        return ""
    return "%d checklist item%s on %s %s still unchecked." % (
        n, "" if n == 1 else "s", constants.job_stage(from_stage)["name"],
        "was" if n == 1 else "were")


def _write_stage_history(job_id, stage):
    """Write a job_stage_history row for CRM-native stage transitions.
    Closes the previous open row (no ended_at) and opens a new one.
    Keeps the history panel populated for jobs never synced from AccuLynx."""
    from flask import session as _sess
    u = db.get("users", _sess.get("user_id")) or {}
    today = db.today()
    # Close the most recent open entry for this job.
    try:
        db.execute(
            "UPDATE job_stage_history SET ended_at=? WHERE job_id=? AND (ended_at IS NULL OR ended_at='')",
            (today, job_id),
        )
    except Exception:
        pass
    db.insert("job_stage_history", {
        "job_id": job_id,
        "status_name": constants.job_stage(stage)["name"],
        "started_at": today,
        "set_by": u.get("name") or u.get("email") or "CRM",
    })


@bp.route("/<int:job_id>/stage", methods=["POST"])
def set_stage(job_id):
    job = _require_job(job_id)
    stage = request.form.get("stage")
    if stage in constants.JOB_STAGE_INDEX:
        _old = job.get("stage")
        warn = _skip_warning(job, _old, stage)
        db.update("jobs", job_id, stage=stage, stage_since=db.today())
        db.add_activity("job", job_id, "stage", "Moved to %s" % constants.job_stage(stage)["name"])
        _write_stage_history(job_id, stage)
        _notify_phase(job_id, _old, stage)
        if request.form.get("ajax"):
            return jsonify({"ok": True, "stage": stage, "warning": warn})
        flash(("Stage updated. ⚠️ " + warn) if warn else "Stage updated.",
              "info" if warn else "ok")
    elif request.form.get("ajax"):
        return jsonify({"ok": False}), 400
    return redirect(url_for("jobs.detail", job_id=job_id))


@bp.route("/<int:job_id>/advance", methods=["POST"])
def advance(job_id):
    """Move the job to the next milestone (AccuLynx 'Advance Job')."""
    j = _require_job(job_id)
    if j:
        idx = constants.JOB_STAGE_INDEX.get(j["stage"], 0)
        if idx < len(constants.JOB_STAGES) - 1:
            nxt = constants.JOB_STAGES[idx + 1]["key"]
            warn = _skip_warning(j, j["stage"], nxt)
            db.update("jobs", job_id, stage=nxt, stage_since=db.today())
            db.add_activity("job", job_id, "stage", "Advanced to %s" % constants.job_stage(nxt)["name"])
            _write_stage_history(job_id, nxt)
            _notify_phase(job_id, j["stage"], nxt)
            name = constants.job_stage(nxt)["name"]
            flash(("Advanced to %s. ⚠️ %s" % (name, warn)) if warn
                  else "Advanced to %s." % name, "info" if warn else "ok")
    return redirect(url_for("jobs.detail", job_id=job_id))


@bp.route("/<int:job_id>/move", methods=["POST"])
def move(job_id):
    job = _require_job(job_id)
    stage = (request.get_json(silent=True) or {}).get("stage") or request.form.get("stage")
    if stage in constants.JOB_STAGE_INDEX:
        warn = _skip_warning(job, job.get("stage"), stage)
        db.update("jobs", job_id, stage=stage, stage_since=db.today())
        db.add_activity("job", job_id, "stage", "Moved to %s" % constants.job_stage(stage)["name"])
        _write_stage_history(job_id, stage)
        return jsonify({"ok": True, "warning": warn})
    return jsonify({"ok": False}), 400


@bp.route("/<int:job_id>/check", methods=["POST"])
def check(job_id):
    j = _require_job(job_id)
    checks = db.load_json(j.get("checks"), {})
    key = request.form.get("key")
    checks[key] = not checks.get(key)
    db.update("jobs", job_id, checks=db.dump_json(checks))
    if request.form.get("ajax"):
        return jsonify({"ok": True, "checked": checks[key]})
    return redirect(url_for("jobs.detail", job_id=job_id))


@bp.route("/<int:job_id>/pay", methods=["POST"])
def pay(job_id):
    j = _require_job(job_id)
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
    _require_job(job_id)
    text = request.form.get("text", "").strip()
    kind = request.form.get("kind", "note")
    if text:
        db.add_activity("job", job_id, kind, text)
    return redirect(url_for("jobs.detail", job_id=job_id))


@bp.route("/<int:job_id>/delete", methods=["POST"])
def delete(job_id):
    _require_job(job_id)
    # Cascade-delete child records to prevent FK orphans.
    # All deletes run in a single BEGIN IMMEDIATE transaction to prevent partial
    # deletes leaving orphan rows if the process is interrupted mid-way.
    import sqlite3
    conn = sqlite3.connect(db.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        eids = [r["id"] for r in conn.execute(
            "SELECT id FROM estimates WHERE job_id=?", (job_id,)).fetchall()]
        for eid in eids:
            conn.execute("DELETE FROM estimate_sections WHERE estimate_id=?", (eid,))
            conn.execute("DELETE FROM estimate_lines WHERE estimate_id=?", (eid,))
        conn.execute("DELETE FROM estimates WHERE job_id=?", (job_id,))
        wsids = [r["id"] for r in conn.execute(
            "SELECT id FROM worksheets WHERE job_id=?", (job_id,)).fetchall()]
        for wsid in wsids:
            conn.execute("DELETE FROM worksheet_lines WHERE worksheet_id=?", (wsid,))
        conn.execute("DELETE FROM worksheets WHERE job_id=?", (job_id,))
        oids = [r["id"] for r in conn.execute(
            "SELECT id FROM orders WHERE job_id=?", (job_id,)).fetchall()]
        for oid in oids:
            conn.execute("DELETE FROM order_lines WHERE order_id=?", (oid,))
        conn.execute("DELETE FROM orders WHERE job_id=?", (job_id,))
        conn.execute("DELETE FROM permits WHERE job_id=?", (job_id,))
        conn.execute("DELETE FROM invoices WHERE job_id=?", (job_id,))
        conn.execute("DELETE FROM materials WHERE job_id=?", (job_id,))
        conn.execute("DELETE FROM measurements WHERE job_id=?", (job_id,))
        conn.execute("DELETE FROM documents WHERE job_id=?", (job_id,))
        conn.execute("DELETE FROM photos WHERE job_id=?", (job_id,))
        conn.execute("DELETE FROM appointments WHERE job_id=?", (job_id,))
        conn.execute("DELETE FROM activities WHERE entity_type='job' AND entity_id=?", (job_id,))
        # Cascade tables identified in 30-agent audit (job_expenses, job_stage_history,
        # commissions, notifications, payments, roof_reports, custom_values, portal_updates).
        conn.execute("DELETE FROM job_expenses WHERE job_id=?", (job_id,))
        conn.execute("DELETE FROM job_stage_history WHERE job_id=?", (job_id,))
        conn.execute("DELETE FROM commissions WHERE job_id=?", (job_id,))
        conn.execute("DELETE FROM notifications WHERE job_id=?", (job_id,))
        conn.execute("DELETE FROM payments WHERE job_id=?", (job_id,))
        conn.execute("DELETE FROM roof_reports WHERE job_id=?", (job_id,))
        conn.execute("DELETE FROM custom_values WHERE entity_type='job' AND entity_id=?", (job_id,))
        conn.execute("DELETE FROM portal_updates WHERE job_id=?", (job_id,))
        conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    flash("Job deleted.", "ok")
    return redirect(url_for("jobs.board"))
