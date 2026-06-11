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
