# -*- coding: utf-8 -*-
"""Jobs / production pipeline — board, detail, checklists, draw schedule, payments."""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, abort

import db
import theme
import constants
from theme import current_department

bp = Blueprint("jobs", __name__, url_prefix="/jobs")

# SQLite caps one statement at SQLITE_LIMIT_VARIABLE_NUMBER (32766) bound params; an
# IN (?,?,…) over more job ids than that 500s with "too many SQL variables". 900 stays
# safe on both engines (Postgres' limit is 65535).
_SQL_IN_BATCH = 900

EDITABLE = ["rid", "name", "phone", "email", "address", "city", "state", "zip",
            "work_type", "rep", "foreman", "crew", "source", "contract_value", "narrative",
            "todo", "notes", "next_follow", "pcn", "legal", "county", "ahj", "system",
            "existing", "area", "slope", "mrh", "exposure", "external_url", "contact_id",
            "pay_url", "sitecam_url"]

# Production crew/foreman fields — jobs natively carry only the sales 'rep'.
# Schema-flexible columns, added the same way other optional job columns are.
db._ensure_column("jobs", "foreman", "TEXT")
db._ensure_column("jobs", "crew", "TEXT")

# Structured inspection records (rough/final/etc). Self-creates at import time per
# house convention; scoped by department like other job-child reads.
db.execute("""CREATE TABLE IF NOT EXISTS inspections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER, type TEXT, scheduled_date TEXT, result TEXT DEFAULT 'pending',
    inspector TEXT, notes TEXT, created TEXT, department TEXT)""")


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
        _sf = constants.normalize_job_stage(stage_f)
        jobs = [j for j in jobs if j["_stage"]["key"] == _sf]
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
        # Bucket by the NORMALIZED stage (j["_stage"], set by _decorate) rather than a
        # raw string ==. A job whose stage column holds a legacy key or an AccuLynx
        # display name matched no column here and vanished from the milestone board
        # entirely — while _decorate had already given it an Approved badge. Same
        # resolver on both sides means what the board shows and what the badge says
        # can no longer disagree.
        items = [j for j in jobs if j["_stage"]["key"] == s["key"]]
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
    import re as _re
    dept      = current_department()
    stage_f   = request.args.get("stage")
    bucket    = request.args.get("bucket")
    q         = (request.args.get("q") or "").strip().lower()
    rep_f     = request.args.get("rep")
    overdue_f = request.args.get("overdue") == "1"
    sort      = request.args.get("sort", "date")
    page      = max(1, request.args.get("page", default=1, type=int) or 1)
    PER_PAGE  = 50

    # Aggregate queries: counts + reps + total without loading every row.
    _conn = db.connect()
    try:
        _sc = _conn.execute(
            "SELECT stage, COUNT(*) n FROM jobs WHERE department=? GROUP BY stage",
            (dept,)).fetchall()
        _rr = _conn.execute(
            "SELECT DISTINCT rep FROM jobs WHERE department=?"
            " AND rep IS NOT NULL AND rep != '' ORDER BY rep",
            (dept,)).fetchall()
        _trow = _conn.execute(
            "SELECT COUNT(*) n FROM jobs WHERE department=?",
            (dept,)).fetchone()
        total = _trow["n"] if _trow else 0
    finally:
        _conn.close()
    counts = {s["key"]: 0 for s in constants.JOB_STAGES}
    # Fold each RAW stage value in the table onto its milestone key and SUM.
    # The old `counts.update({r["stage"]: ...})` both (a) injected unrecognized keys
    # that the sidebar template never renders — so those jobs counted toward the
    # "All" total but had no row to click, making the numbers not add up — and
    # (b) overwrote rather than accumulated when two raw spellings mapped to one
    # milestone. Keep the raw->key map so the SQL filter below can match all of them.
    _stage_variants = {}
    for r in _sc:
        _key = constants.normalize_job_stage(r["stage"])
        counts[_key] = counts.get(_key, 0) + r["n"]
        _stage_variants.setdefault(_key, []).append(r["stage"])
    reps = [r["rep"] for r in _rr]

    # Build WHERE clause — push all SQL-safe filters to the DB.
    _w, _p = ["department=?"], [dept]
    if stage_f:
        # Match every RAW spelling in the table that normalizes to this milestone,
        # not just the canonical key — otherwise clicking a sidebar row whose count
        # came from a legacy/display-name value returned zero rows.
        _sf = constants.normalize_job_stage(stage_f)
        _variants = _stage_variants.get(_sf) or [stage_f]
        _w.append("stage IN (%s)" % ",".join("?" * len(_variants)))
        _p.extend(_variants)
    elif bucket:
        # Push bucket→stages so LIMIT/OFFSET is applied to the filtered set.
        _bkeys = [s["key"] for s in constants.JOB_STAGES if s.get("bucket") == bucket]
        _bstages = [raw for k in _bkeys for raw in _stage_variants.get(k, [k])]
        if _bstages:
            _w.append("stage IN (%s)" % ",".join("?" * len(_bstages)))
            _p.extend(_bstages)
        else:
            # Unknown bucket → no stage maps to it → no rows. Without this, an empty
            # _bstages left the WHERE unfiltered AND the paginated path skips the Python
            # bucket filter, so a bogus ?bucket= leaked EVERY dept row instead of none.
            _w.append("1=0")
        bucket = None  # SQL now owns the bucket filter; clear so Python doesn't re-filter
    if rep_f:
        _w.append("rep=?"); _p.append(rep_f)
    if q:
        _qd = _re.sub(r"\D", "", q)
        _like = "%" + q + "%"
        _qparts = ["LOWER(name) LIKE ?", "LOWER(address) LIKE ?", "LOWER(rid) LIKE ?",
                   "LOWER(work_type) LIKE ?", "LOWER(email) LIKE ?"]
        _qp = [_like] * len(_qparts)
        if _qd and len(_qd) >= 7:
            _qparts.append(
                "REPLACE(REPLACE(REPLACE(REPLACE(phone,' ',''),'(',''),')',''),'-','') LIKE ?")
            _qp.append("%" + _qd + "%")
        _w.append("(%s)" % " OR ".join(_qparts))
        _p.extend(_qp)

    _where = " AND ".join(_w)
    _params = tuple(_p)

    # Hardcoded SQL sort expressions (safe — no user data, allowlisted here).
    _SORT_SQL = {
        "date":   "id DESC",
        "name":   "LOWER(COALESCE(name,'')) ASC, id DESC",
        "value":  "CAST(REPLACE(REPLACE(COALESCE(contract_value,'0'),'$',''),',','') AS REAL) DESC, id DESC",
        "rid":    "LOWER(COALESCE(rid,'')) ASC, id DESC",
        "recent": "COALESCE(stage_since, created) DESC, id DESC",
    }
    # 'days' sort and overdue filter both require decorated fields — can't paginate.
    can_paginate = not overdue_f and sort in _SORT_SQL

    if can_paginate:
        _order_sql = _SORT_SQL[sort]
        _conn2 = db.connect()
        try:
            _mrow = _conn2.execute(
                "SELECT COUNT(*) n FROM jobs WHERE " + _where, _params
            ).fetchone()
            matching_n = _mrow["n"] if _mrow else 0
            _page_rows = [dict(r) for r in _conn2.execute(
                "SELECT * FROM jobs WHERE %s ORDER BY %s LIMIT %d OFFSET %d" % (
                    _where, _order_sql, PER_PAGE, (page - 1) * PER_PAGE),
                _params).fetchall()]
        finally:
            _conn2.close()
        jobs = [_decorate(j) for j in _page_rows]
        total_pages = max(1, (matching_n + PER_PAGE - 1) // PER_PAGE)
        rows = jobs  # bucket was already pushed to SQL; no Python bucket filter needed
    else:
        # 'days' sort or overdue_f: load all matching records.
        jobs = [_decorate(j) for j in db.all_rows("jobs", _where, _params)]
        if bucket:
            jobs = [j for j in jobs if j["_stage"].get("bucket") == bucket]
        if overdue_f:
            jobs = [j for j in jobs if j["_fs"]["level"] != "ok" and j["stage"] not in constants.JOB_INACTIVE]
        if sort == "days":
            jobs.sort(key=lambda j: -j["_fs"]["days"])
        elif sort == "recent":
            jobs.sort(key=lambda j: (j.get("stage_since") or j.get("created") or ""), reverse=True)
        elif sort == "value":
            jobs.sort(key=lambda j: -theme.est_num(j.get("contract_value")))
        elif sort == "name":
            jobs.sort(key=lambda j: (j.get("name") or "").lower())
        elif sort == "rid":
            jobs.sort(key=lambda j: (j.get("rid") or "").lower())
        rows = jobs
        matching_n = len(rows)
        page = 1
        total_pages = 1

    # Batch-load profit analysis for visible rows only (50 rows on the paginated view;
    # on the 'days'/overdue path `rows` is every matching job, so the id list is chunked
    # under SQLite's 32766-bound-var cap — a >32k-job dept would otherwise 500 here with
    # "too many SQL variables").
    if rows:
        _ids = [j["id"] for j in rows]
        _ws_agg = []
        _conn3 = db.connect()
        try:
            for _i in range(0, len(_ids), _SQL_IN_BATCH):
                _chunk = _ids[_i:_i + _SQL_IN_BATCH]
                _ph = ",".join("?" * len(_chunk))
                _ws_agg.extend(_conn3.execute(
                    "SELECT w.job_id, w.contract_value, "
                    "COALESCE(SUM(wl.actual_cost),0) AS actual_cost, "
                    "COALESCE(SUM(wl.budget_cost),0) AS budget_cost "
                    "FROM worksheets w "
                    "LEFT JOIN worksheet_lines wl ON wl.worksheet_id=w.id "
                    "WHERE w.job_id IN (%s) GROUP BY w.job_id, w.id ORDER BY w.id DESC" % _ph,
                    tuple(_chunk)).fetchall())
        finally:
            _conn3.close()
        _ws_by_job = {}
        for _ws in _ws_agg:
            if _ws["job_id"] not in _ws_by_job:
                _ws_by_job[_ws["job_id"]] = dict(_ws)
        for j in rows:
            _ws = _ws_by_job.get(j["id"])
            if _ws:
                _cv = _ws["contract_value"] or 0
                _gp = _cv - _ws["actual_cost"]
                j["_pa"] = {"has_ws": True, "gross_profit": _gp,
                            "gross_pct": (_gp / _cv * 100.0) if _cv else 0}
            else:
                j["_pa"] = {"has_ws": False}
    return render_template("jobs_list.html", rows=rows, counts=counts, stage_f=stage_f,
                           bucket=bucket, q=q, total=total, sort=sort, rep_f=rep_f, reps=reps,
                           stages=constants.JOB_STAGES, buckets=constants.BUCKETS,
                           overdue_f=overdue_f, page=page, total_pages=total_pages,
                           matching_n=matching_n, per_page=PER_PAGE)


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
    # Load estimates once; reuse for auto-materialize check and the render call below.
    _job_estimates = db.all_rows("estimates", "job_id=?", (job_id,))
    # Auto-materialize the worksheet from the estimate so Profit Analysis fills in without
    # opening the worksheet + clicking Seed. Only when an estimate with line items exists
    # (get_or_create seeds new/placeholder worksheets, never clobbers a built-out one).
    try:
        from modules import worksheet as _ws
        if _job_estimates:
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
    # Inspections — scoped by the job's own department (child read).
    try:
        inspections = db.all_rows("inspections", "job_id=? AND department=?",
                                  (job_id, j.get("department")), "scheduled_date DESC, id DESC")
    except Exception:
        inspections = []
    # Client portal two-way message thread — so a rep can see what the homeowner
    # said before replying, instead of replying blind.
    try:
        from modules import portal as _portal
        portal_thread = _portal.thread_messages(job_id)
    except Exception:
        portal_thread = []
    return render_template("job_detail.html", j=j, measurement=meas.for_job(job_id),
                           portal_thread=portal_thread,
                           quick_templates=quick_templates,
                           meas_fields=meas.FIELDS,
                           activity=db.entity_activity("job", job_id),
                           estimates=_job_estimates,
                           documents=db.all_rows("documents", "job_id=?", (job_id,)),
                           photos=db.all_rows("photos", "job_id=?", (job_id,)),
                           permits=db.all_rows("permits", "job_id=?", (job_id,)),
                           invoices=db.all_rows("invoices", "job_id=?", (job_id,)),
                           materials=db.all_rows("materials", "job_id=?", (job_id,)),
                           job_expenses=job_expenses, stage_history=stage_history,
                           inspections=inspections,
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
        # Write BOTH columns. `milestone` (the stable key) was never populated here,
        # so anything reading job_stage_history.milestone got nothing but the
        # human-facing status_name — which is not safe to match on.
        "milestone": constants.normalize_job_stage(stage),
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
        # Delegate to constants.next_step() instead of re-deriving the index here.
        # The local math had two defects: `closed` is index 19 and len-1 is 20, so
        # advancing a CLOSED job moved it to JOB_STAGES[20] == "canceled"; and an
        # unrecognized stage fell back to index 0, advancing the job to
        # "finance_ntp" regardless of where it actually was. next_step() already
        # guards both ("never auto-advance into Canceled") and returns None at a
        # terminal, so a terminal job is now simply a no-op.
        step = constants.next_step("job", constants.normalize_job_stage(j["stage"]))
        if step and step.get("action") == "job_stage":
            nxt = step["stage"]
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
    # A hand-crafted / scripted POST can send a JSON body that is a list, string,
    # number or bool — get_json returns that non-dict verbatim, and a truthy non-dict
    # skips the ``or {}`` fallback, so ``.get`` 500'd (AttributeError). Coerce to {}.
    _body = request.get_json(silent=True)
    if not isinstance(_body, dict):
        _body = {}
    stage = _body.get("stage") or request.form.get("stage")
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


@bp.route("/<int:job_id>/inspection", methods=["POST"])
def add_inspection(job_id):
    """Add a structured inspection record (rough/final/etc.) for this job."""
    j = _require_job(job_id)
    itype = request.form.get("type", "").strip()
    if itype:
        db.insert("inspections", {
            "job_id": job_id,
            "type": itype,
            "scheduled_date": request.form.get("scheduled_date", "").strip(),
            "result": request.form.get("result", "pending").strip() or "pending",
            "inspector": request.form.get("inspector", "").strip(),
            "notes": request.form.get("notes", "").strip(),
            "created": db.today(),
            "department": j.get("department") or current_department(),
        })
        db.add_activity("job", job_id, "note", "Inspection added: %s" % itype)
        flash("Inspection added.", "ok")
    return redirect(url_for("jobs.detail", job_id=job_id))


@bp.route("/<int:job_id>/inspection/<int:insp_id>", methods=["POST"])
def update_inspection(job_id, insp_id):
    """Update an existing inspection (e.g. record the result) for this job."""
    _require_job(job_id)
    insp = db.get("inspections", insp_id)
    if not insp or insp.get("job_id") != job_id:
        abort(404)
    data = {}
    for f in ("type", "scheduled_date", "result", "inspector", "notes"):
        if f in request.form:
            data[f] = request.form.get(f, "").strip()
    if data:
        db.update("inspections", insp_id, **data)
        db.add_activity("job", job_id, "note",
                        "Inspection updated: %s" % (data.get("type") or insp.get("type") or ""))
    flash("Inspection updated.", "ok")
    return redirect(url_for("jobs.detail", job_id=job_id))


@bp.route("/<int:job_id>/snooze", methods=["POST"])
def snooze(job_id):
    _require_job(job_id)
    raw = request.form.get("days", "30")
    days = int(raw) if raw.lstrip("-").isdigit() else 30
    if days <= 0:
        db.update("jobs", job_id, snooze_until="")
        db.add_activity("job", job_id, "note", "Cleared follow-up snooze.")
        flash("Snooze cleared — job will appear in overdue list again.", "ok")
    else:
        import datetime as _dt
        days = min(days, 365)
        until = (_dt.date.today() + _dt.timedelta(days=days)).isoformat()
        db.update("jobs", job_id, snooze_until=until)
        db.add_activity("job", job_id, "note", "Snoozed follow-up for %d days (until %s)." % (days, until))
        flash("Snoozed for %d days — won't appear overdue until %s." % (days, until), "ok")
    return redirect(url_for("jobs.detail", job_id=job_id))


@bp.route("/<int:job_id>/delete", methods=["POST"])
def delete(job_id):
    _require_job(job_id)
    # Cascade-delete child records to prevent FK orphans.
    # All deletes run in a single serialized transaction to prevent partial deletes
    # leaving orphan rows if the process is interrupted mid-way (dual-engine).
    conn = db.begin_immediate()
    try:
        # Subquery DELETEs replace N+1 fetch-then-loop patterns.
        conn.execute("DELETE FROM estimate_sections WHERE estimate_id IN (SELECT id FROM estimates WHERE job_id=?)", (job_id,))
        conn.execute("DELETE FROM estimate_lines WHERE estimate_id IN (SELECT id FROM estimates WHERE job_id=?)", (job_id,))
        conn.execute("DELETE FROM estimates WHERE job_id=?", (job_id,))
        conn.execute("DELETE FROM worksheet_lines WHERE worksheet_id IN (SELECT id FROM worksheets WHERE job_id=?)", (job_id,))
        conn.execute("DELETE FROM worksheets WHERE job_id=?", (job_id,))
        conn.execute("DELETE FROM order_lines WHERE order_id IN (SELECT id FROM orders WHERE job_id=?)", (job_id,))
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
