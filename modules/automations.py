# -*- coding: utf-8 -*-
"""Workflow Manager (Automations) — AccuLynx parity.

Admin-configured rules that fire on milestone changes: create a task, a reminder,
or an email DRAFT (never auto-sent, per house rules). Hooked without touching
jobs.py/leads.py by wrapping db.add_activity: every stage-change activity
(kind='stage') is matched against active automations for that stage.

Audit #11 (AUDIT_2026-06-10.md):
  A. Stage detection used to regex-parse the English activity text
     ('Moved to X'/'Advanced to X'). Rewording the label silently broke
     every automation. NEW: we read the entity's current `stage` column
     directly — the source of truth the caller just set.
  B. Two `except: pass` blocks swallowed every automation failure with no
     trace. NEW: all exception paths log via the stdlib logger.
  C. Two stage activities for one transition = double-fire (the monkey-
     patched add_activity fires on each call). NEW: an `automation_fires`
     table with a UNIQUE constraint on
     (automation_id, entity_type, entity_id, stage_key, fire_date)
     gives us insert-first / catch-violation atomic dedupe per
     (entity, stage, day).
"""
import logging

from flask import Blueprint, render_template, request, redirect, url_for, flash

import db
import constants

bp = Blueprint("automations", __name__, url_prefix="/workflow")
_log = logging.getLogger(__name__)

ACTION_TYPES = ["create_task", "draft_email", "create_reminder"]

# Synthetic triggers that aren't pipeline stages (invoices carry no stage). Surfaced
# in the Workflow admin and fired explicitly from their own module.
_EXTRA_TRIGGERS = [("invoice_overdue", "Invoice overdue")]


# Audit #11.C: dedupe-fire table. UNIQUE constraint is what enforces "at most
# once per (entity, stage, day)" — the runtime code does insert-first /
# catch-violation, so the race is closed at the SQL layer (not a Python check).
try:
    db.execute(
        """CREATE TABLE IF NOT EXISTS automation_fires (
            id INTEGER PRIMARY KEY AUTOINCREMENT, created TEXT,
            automation_id INTEGER, entity_type TEXT, entity_id INTEGER,
            stage_key TEXT, fire_date TEXT,
            UNIQUE(automation_id, entity_type, entity_id, stage_key, fire_date))""")
except Exception:   # pragma: no cover — best-effort like the rest of module-load DDL
    pass

# ---------------------------------------------------------------------------
# Multi-step follow-up SEQUENCES (drip cadence)
# ---------------------------------------------------------------------------
# A sequence is an ordered set of steps; each step fires `offset_days` after the
# enrollment date. A step is an 'email' (draft by default; auto-send only when
# the step opts in) or an 'sms' (via comms.send_sms — fails closed when Twilio
# is unset). Enrollments track per-entity progress; the tick/runner finds due
# steps and fires them. Self-creating tables (house convention) — no db.py edit.
for _ddl in (
    """CREATE TABLE IF NOT EXISTS sequences (
        id INTEGER PRIMARY KEY AUTOINCREMENT, created TEXT,
        name TEXT, active INTEGER DEFAULT 1, department TEXT)""",
    """CREATE TABLE IF NOT EXISTS sequence_steps (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sequence_id INTEGER, step_no INTEGER DEFAULT 0,
        offset_days INTEGER DEFAULT 0,
        channel TEXT DEFAULT 'email',
        subject TEXT, body TEXT,
        auto_send INTEGER DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS sequence_enrollments (
        id INTEGER PRIMARY KEY AUTOINCREMENT, created TEXT,
        sequence_id INTEGER, entity_type TEXT, entity_id INTEGER,
        enrolled_date TEXT, status TEXT DEFAULT 'active',
        UNIQUE(sequence_id, entity_type, entity_id))""",
    # Per-step fire ledger — UNIQUE makes the runner idempotent (a step fires
    # at most once per enrollment, even if tick() runs repeatedly).
    """CREATE TABLE IF NOT EXISTS sequence_step_fires (
        id INTEGER PRIMARY KEY AUTOINCREMENT, created TEXT,
        enrollment_id INTEGER, step_id INTEGER, result TEXT,
        UNIQUE(enrollment_id, step_id))""",
):
    try:
        db.execute(_ddl)
    except Exception:   # pragma: no cover — best-effort module-load DDL
        pass

# db.insert/update/get/all_rows gate on db.TABLE_ALLOWLIST. Register our self-
# created tables at import so the data-layer accepts them WITHOUT editing db.py.
# (Mutating the in-memory set, not the file — fully reversible. The integrator
# should also add these to the canonical TABLE_ALLOWLIST literal in db.py; see
# wiring_snippets.)
try:
    db.TABLE_ALLOWLIST.update({
        "sequences", "sequence_steps", "sequence_enrollments", "sequence_step_fires"})
except Exception:   # pragma: no cover
    pass
db._COLCACHE.clear()


def _current_stage_key(entity_type, entity_id):
    """Audit #11.A: read the entity's CURRENT stage column from the DB. The
    caller just set it before logging the breadcrumb activity, so the DB is
    the source of truth — not the English text we were parsing before."""
    rec = db.get(entity_type + "s", entity_id) or {}
    return (rec.get("stage") or "").strip() or None


def _fill(template, entity_type, entity_id):
    rec = db.get(entity_type + "s", entity_id) or {}
    stage = ""
    if entity_type == "job":
        stage = constants.job_stage(rec.get("stage", "")).get("name", "")
    elif entity_type == "lead":
        stage = constants.lead_stage(rec.get("stage", "")).get("name", "")
    subs = {"{customer}": rec.get("name", ""), "{name}": rec.get("name", ""),
            "{address}": rec.get("address", ""), "{ahj}": rec.get("ahj", ""),
            "{rep}": rec.get("rep", ""), "{stage}": stage,
            "{company}": db.get_company().get("name", "")}
    out = template or ""
    for k, v in subs.items():
        out = out.replace(k, str(v or ""))
    return out


def _execute(auto, entity_type, entity_id):
    text = _fill(auto.get("template_text"), entity_type, entity_id)
    offset = int(auto.get("offset_days") or 0)
    due = None
    if offset >= 0:
        try:
            from datetime import datetime, timedelta
            due = (datetime.now() + timedelta(days=offset)).strftime("%Y-%m-%d")
        except Exception:
            due = db.today()
    action = auto.get("action_type")
    if action == "draft_email":
        db.add_activity(entity_type, entity_id, "draft", "✉️ DRAFT (auto): " + text)
    elif action == "create_reminder":
        db.add_activity(entity_type, entity_id, "task", "⏰ Reminder: " + text, due=due)
    else:  # create_task
        db.add_activity(entity_type, entity_id, "task", text, due=due)


def _claim_fire(auto_id, entity_type, entity_id, stage_key):
    """Audit #11.C dedupe. SELECT-then-INSERT: returns True if we just claimed
    a fire-slot for today; False if a prior fire already exists. The UNIQUE
    constraint on automation_fires is a defensive backstop for true cross-
    process races; in normal in-process flow the SELECT catches duplicates
    before INSERT, so we never hit the IntegrityError path (and never trigger
    db.insert's connection-leak-on-exception."""
    today = db.today()
    existing = db.all_rows(
        "automation_fires",
        where=("automation_id=? AND entity_type=? AND entity_id=? "
               "AND stage_key=? AND fire_date=?"),
        params=(auto_id, entity_type, entity_id, stage_key, today))
    if existing:
        _log.debug("automation %s already fired today for %s/%s stage=%s",
                   auto_id, entity_type, entity_id, stage_key)
        return False
    try:
        db.insert("automation_fires", {
            "automation_id": auto_id, "entity_type": entity_type,
            "entity_id": entity_id, "stage_key": stage_key, "fire_date": today})
        return True
    except Exception:
        # UNIQUE backstop fired (true cross-process race). Treat as dedup-skip.
        _log.debug("automation %s race-loser for %s/%s stage=%s",
                   auto_id, entity_type, entity_id, stage_key)
        return False


def _maybe_fire(entity_type, entity_id):
    if entity_type not in ("job", "lead"):
        return
    key = _current_stage_key(entity_type, entity_id)
    if not key:
        _log.debug("no current stage on %s %s; skipping automations", entity_type, entity_id)
        return
    for auto in db.all_rows("automations", "trigger_stage=? AND active=1", (key,)):
        if not _claim_fire(auto["id"], entity_type, entity_id, key):
            continue
        try:
            _execute(auto, entity_type, entity_id)
        except Exception:
            _log.exception("automation %s failed for %s %s (stage=%s)",
                           auto.get("id"), entity_type, entity_id, key)


def fire_invoice_overdue(inv, job=None):
    """Status-engine entry point for overdue invoices. Invoices have no pipeline stage,
    so the invoice layer calls this when one is first observed overdue. Fires any active
    'invoice_overdue' automations against the linked job (draft nudge — never sends).
    Dedupe key uses the invoice id (the entity actually transitioning) so the same
    invoice can't fire twice in one day even if the overdue scan re-runs."""
    if not inv or not inv.get("job_id"):
        return
    inv_id = inv.get("id")
    for auto in db.all_rows("automations", "trigger_stage=? AND active=1", ("invoice_overdue",)):
        # Dedupe scope: (automation, invoice, "invoice_overdue", today). Stored
        # under entity_type='invoice' so it doesn't collide with job-stage fires.
        if not _claim_fire(auto["id"], "invoice", inv_id, "invoice_overdue"):
            continue
        try:
            _execute(auto, "job", inv["job_id"])
        except Exception:
            _log.exception("invoice_overdue automation %s failed for inv=%s job=%s",
                           auto.get("id"), inv_id, inv.get("job_id"))


def _ensure_invoice_overdue_default():
    """Idempotently ensure the 'invoice overdue -> draft reminder' rule exists, even on
    DBs seeded before this rule shipped (defaults only seed on a fresh, empty table)."""
    if db.all_rows("automations", "trigger_stage=?", ("invoice_overdue",)):
        return
    db.insert("automations", {
        "name": "Invoice overdue — draft customer reminder",
        "trigger_stage": "invoice_overdue", "action_type": "draft_email",
        "template_text": ("Hi {customer}, a friendly reminder from {company} that your "
                          "invoice is past due. Please use the secure pay link we sent, "
                          "or reply with any questions."),
        "offset_days": 0, "active": 1})


def _seed_defaults():
    if db.all_rows("automations"):
        _ensure_invoice_overdue_default()
        return
    defaults = [
        ("Permit submitted — track it", "permit_applied", "create_task",
         "Track permit with the county for {customer}; update customer on timeline.", 2),
        ("Permit submitted — notify customer", "permit_applied", "draft_email",
         "Hi {customer}, your roofing permit for {address} has been submitted to {ahj}. "
         "We'll let you know as soon as it's approved. — {company}", 0),
        ("Permit approved — schedule", "permit_approved", "create_task",
         "Notify {customer} of start date; schedule crew, order material, install permit box.", 1),
        ("Pre-con — order material", "precon_needed", "create_task",
         "Run pre-con walkthrough, order material, set crew & start date, put up yard sign.", 1),
        ("Final passed — close out", "final_passed", "create_task",
         "Send final invoice & warranty; request review and 3 referrals from {customer}.", 1),
        ("Lead → Prospect follow-up", "prospect", "create_reminder",
         "Follow up with {customer} on the estimate — answer questions, ask for the sale.", 3),
    ]
    for name, stg, act, tmpl, off in defaults:
        db.insert("automations", {"name": name, "trigger_stage": stg, "action_type": act,
                                  "template_text": tmpl, "offset_days": off, "active": 1})
    _ensure_invoice_overdue_default()


# ---------------------------------------------------------------------------
# Sequence engine — enroll, tick/runner, fire one step
# ---------------------------------------------------------------------------

def _days_between(start_ymd, end_ymd):
    """Whole days from start->end (both 'YYYY-MM-DD'). Tolerant: bad input ⇒ 0."""
    try:
        from datetime import datetime
        a = datetime.strptime((start_ymd or "")[:10], "%Y-%m-%d")
        b = datetime.strptime((end_ymd or "")[:10], "%Y-%m-%d")
        return (b - a).days
    except Exception:
        return 0


def enroll(sequence_id, entity_type, entity_id, enrolled_date=None):
    """Enroll an entity into a sequence (idempotent on (seq,type,id)). Returns the
    enrollment id, or None on bad input. Re-enrolling an existing pair is a no-op
    that returns the existing id (so callers can enroll freely)."""
    if entity_type not in ("job", "lead", "contact"):
        return None
    existing = db.all_rows(
        "sequence_enrollments",
        where="sequence_id=? AND entity_type=? AND entity_id=?",
        params=(sequence_id, entity_type, entity_id))
    if existing:
        return existing[0]["id"]
    try:
        return db.insert("sequence_enrollments", {
            "created": db.now(), "sequence_id": sequence_id,
            "entity_type": entity_type, "entity_id": entity_id,
            "enrolled_date": (enrolled_date or db.today()), "status": "active"})
    except Exception:
        # UNIQUE backstop (cross-process race) — fetch + return the winner's id.
        again = db.all_rows(
            "sequence_enrollments",
            where="sequence_id=? AND entity_type=? AND entity_id=?",
            params=(sequence_id, entity_type, entity_id))
        return again[0]["id"] if again else None


def _recipient(entity_type, entity_id, channel):
    """Email address (email channel) or phone (sms channel) for the entity."""
    rec = db.get(entity_type + "s", entity_id) if entity_type in ("job", "lead") else None
    if entity_type == "contact":
        rec = db.get("contacts", entity_id)
    rec = rec or {}
    if channel == "sms":
        return (rec.get("phone") or "").strip()
    return (rec.get("email") or "").strip()


def _fire_step(enr, step):
    """Fire one sequence step for one enrollment. Returns a short result string.
    Email default = DRAFT (house rule); auto-send only when step.auto_send is set
    AND a send path succeeds. SMS goes through comms.send_sms (fails closed)."""
    et, eid = enr["entity_type"], enr["entity_id"]
    channel = (step.get("channel") or "email").strip()
    subject = _fill(step.get("subject"), et, eid)
    body = _fill(step.get("body"), et, eid)
    to = _recipient(et, eid, channel)

    if channel == "sms":
        try:
            from modules import comms
        except Exception:
            import comms  # pragma: no cover — alt import path
        ok = comms.send_sms(to, body, entity_type=et, entity_id=eid)
        return "sms_sent" if ok else "sms_failed_closed"

    # email channel
    if int(step.get("auto_send") or 0) and to:
        try:
            try:
                from modules import gmail
            except Exception:
                import gmail  # pragma: no cover — alt import path
            # uid=None: send_message falls back to SMTP if no user OAuth (fail-closed
            # if neither configured ⇒ returns None ⇒ we draft instead).
            res = gmail.send_message(None, to, subject, body)
            if res:
                db.add_activity(et, eid, "email", "✉️ Sent (sequence): %s\n%s" % (subject, body))
                return "email_sent"
        except Exception:
            _log.exception("sequence auto-send failed for %s %s; falling back to draft", et, eid)
    # Default / fallback: draft only (never auto-sent).
    db.add_activity(et, eid, "draft", "✉️ DRAFT (sequence) — %s\n%s" % (subject, body))
    return "email_drafted"


def tick(now_date=None):
    """Runner: find due steps across all active enrollments and fire them once.
    A step is DUE when (today - enrolled_date) >= step.offset_days and it has not
    already fired for that enrollment. Returns a summary dict. Safe to call
    repeatedly (per-step UNIQUE ledger makes each fire idempotent)."""
    today = now_date or db.today()
    fired, skipped = 0, 0
    enrollments = db.all_rows("sequence_enrollments", "status=?", ("active",))
    for enr in enrollments:
        seq = db.get("sequences", enr["sequence_id"])
        if not seq or not seq.get("active"):
            continue
        elapsed = _days_between(enr.get("enrolled_date"), today)
        steps = db.all_rows("sequence_steps", "sequence_id=?",
                            (enr["sequence_id"],), order="offset_days, step_no, id")
        all_done = True
        for step in steps:
            if elapsed < int(step.get("offset_days") or 0):
                all_done = False
                continue  # not due yet
            # Claim the (enrollment, step) slot — insert-first / catch-violation.
            already = db.all_rows(
                "sequence_step_fires",
                where="enrollment_id=? AND step_id=?",
                params=(enr["id"], step["id"]))
            if already:
                continue
            try:
                fid = db.insert("sequence_step_fires", {
                    "created": db.now(), "enrollment_id": enr["id"],
                    "step_id": step["id"], "result": "pending"})
            except Exception:
                # UNIQUE backstop (race) — someone else claimed it.
                continue
            try:
                result = _fire_step(enr, step)
            except Exception:
                _log.exception("sequence step %s failed for enrollment %s",
                               step.get("id"), enr.get("id"))
                result = "error"
            try:
                db.update("sequence_step_fires", fid, result=result)
            except Exception:
                _log.exception("failed recording sequence fire result")
            fired += 1
        if all_done and steps:
            # All steps are past-due and accounted for — mark the enrollment done.
            done_count = len(db.all_rows(
                "sequence_step_fires", "enrollment_id=?", (enr["id"],)))
            if done_count >= len(steps):
                try:
                    db.update("sequence_enrollments", enr["id"], status="done")
                except Exception:
                    _log.exception("failed marking enrollment %s done", enr.get("id"))
        skipped += 1
    return {"fired": fired, "enrollments_scanned": skipped}


def init_automations(app):
    _seed_defaults()
    # Wrap db.add_activity so stage-change activities trigger automations.
    if getattr(db.add_activity, "_automated", False):
        return
    _orig = db.add_activity

    def _wrapped(entity_type, entity_id, kind, text, due=None, assignee=None):
        aid = _orig(entity_type, entity_id, kind, text, due=due, assignee=assignee)
        if kind == "stage":
            # Audit #11.A: do NOT pass the prose text — _maybe_fire now reads
            # the entity's current stage column from the DB.
            try:
                _maybe_fire(entity_type, entity_id)
            except Exception:
                _log.exception("automation dispatch crashed for %s %s", entity_type, entity_id)
        return aid
    _wrapped._automated = True
    db.add_activity = _wrapped
    app.register_blueprint(bp)


# ---- admin UI -------------------------------------------------------------

def _stage_choices():
    return ([("lead", s["key"], s["name"]) for s in constants.LEAD_STAGES] +
            [("job", s["key"], s["name"]) for s in constants.JOB_STAGES] +
            [("invoice", k, n) for k, n in _EXTRA_TRIGGERS])


@bp.route("/")
def index():
    rows = db.all_rows("automations", order="trigger_stage, id")
    name_of = {s["key"]: s["name"] for s in constants.LEAD_STAGES + constants.JOB_STAGES}
    name_of.update(dict(_EXTRA_TRIGGERS))
    for a in rows:
        a["_stage_name"] = name_of.get(a["trigger_stage"], a["trigger_stage"])
    return render_template("workflow.html", autos=rows, stages=_stage_choices(),
                           actions=ACTION_TYPES)


@bp.route("/new", methods=["POST"])
def new():
    db.insert("automations", {
        "name": request.form.get("name", "").strip(),
        "trigger_stage": request.form.get("trigger_stage", ""),
        "action_type": request.form.get("action_type", "create_task"),
        "template_text": request.form.get("template_text", "").strip(),
        "offset_days": int(request.form.get("offset_days") or 0), "active": 1})
    flash("Automation created.", "ok")
    return redirect(url_for("automations.index"))


@bp.route("/<int:auto_id>/save", methods=["POST"])
def save(auto_id):
    db.update("automations", auto_id,
              name=request.form.get("name", "").strip(),
              trigger_stage=request.form.get("trigger_stage", ""),
              action_type=request.form.get("action_type", "create_task"),
              template_text=request.form.get("template_text", "").strip(),
              offset_days=int(request.form.get("offset_days") or 0))
    flash("Automation saved.", "ok")
    return redirect(url_for("automations.index"))


@bp.route("/<int:auto_id>/toggle", methods=["POST"])
def toggle(auto_id):
    a = db.get("automations", auto_id)
    if a:
        db.update("automations", auto_id, active=0 if a.get("active") else 1)
    return redirect(url_for("automations.index"))


@bp.route("/<int:auto_id>/delete", methods=["POST"])
def delete(auto_id):
    db.delete("automations", auto_id)
    flash("Automation deleted.", "ok")
    return redirect(url_for("automations.index"))


# ---- sequence routes ------------------------------------------------------

@bp.route("/sequences/run", methods=["POST", "GET"])
def sequences_run():
    """Trigger the sequence runner. Returns JSON so it can be hit by a cron/curl
    or the admin UI. Idempotent — safe to call on any cadence."""
    from flask import jsonify
    summary = tick()
    if request.method == "GET" and "text/html" in (request.headers.get("Accept") or ""):
        flash("Sequence runner fired %d step(s)." % summary["fired"], "ok")
        return redirect(url_for("automations.index"))
    return jsonify({"ok": True, **summary})


@bp.route("/sequences/new", methods=["POST"])
def sequence_new():
    import theme
    db.insert("sequences", {
        "created": db.now(),
        "name": request.form.get("name", "").strip() or "Untitled sequence",
        "active": 1, "department": theme.current_department()})
    flash("Sequence created.", "ok")
    return redirect(url_for("automations.index"))


@bp.route("/sequences/<int:seq_id>/step", methods=["POST"])
def sequence_add_step(seq_id):
    channel = request.form.get("channel", "email").strip()
    if channel not in ("email", "sms"):
        channel = "email"
    db.insert("sequence_steps", {
        "sequence_id": seq_id,
        "step_no": int(request.form.get("step_no") or 0),
        "offset_days": int(request.form.get("offset_days") or 0),
        "channel": channel,
        "subject": request.form.get("subject", "").strip(),
        "body": request.form.get("body", "").strip(),
        "auto_send": 1 if request.form.get("auto_send") else 0})
    flash("Step added.", "ok")
    return redirect(url_for("automations.index"))


@bp.route("/sequences/<int:seq_id>/enroll", methods=["POST"])
def sequence_enroll(seq_id):
    target = request.form.get("target", "")
    et, _, eid = target.partition(":")
    if et and eid:
        eid_i = enroll(seq_id, et, int(eid))
        flash("Enrolled." if eid_i else "Could not enroll.", "ok" if eid_i else "err")
    return redirect(url_for("automations.index"))


@bp.route("/sequences/<int:seq_id>/toggle", methods=["POST"])
def sequence_toggle(seq_id):
    s = db.get("sequences", seq_id)
    if s:
        db.update("sequences", seq_id, active=0 if s.get("active") else 1)
    return redirect(url_for("automations.index"))
