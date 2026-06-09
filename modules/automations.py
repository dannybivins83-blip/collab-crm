# -*- coding: utf-8 -*-
"""Workflow Manager (Automations) — AccuLynx parity.

Admin-configured rules that fire on milestone changes: create a task, a reminder,
or an email DRAFT (never auto-sent, per house rules). Hooked without touching
jobs.py/leads.py by wrapping db.add_activity: every stage-change activity
(kind='stage') is matched against active automations for that stage.
"""
import re

from flask import Blueprint, render_template, request, redirect, url_for, flash

import db
import constants

bp = Blueprint("automations", __name__, url_prefix="/workflow")

ACTION_TYPES = ["create_task", "draft_email", "create_reminder"]

# Synthetic triggers that aren't pipeline stages (invoices carry no stage). Surfaced
# in the Workflow admin and fired explicitly from their own module.
_EXTRA_TRIGGERS = [("invoice_overdue", "Invoice overdue")]

# stage display-name -> key (lead + job stage keys are disjoint, so one map is safe)
_STAGE_BY_NAME = {}
for _s in constants.LEAD_STAGES + constants.JOB_STAGES:
    _STAGE_BY_NAME[_s["name"].lower()] = _s["key"]


def _stage_key_from_text(text):
    """A stage-change activity reads e.g. 'Moved to Permit Approved' / 'Advanced to X'."""
    m = re.search(r"\b(?:to|in)\s+(.+?)\s*$", text or "")
    if not m:
        return None
    return _STAGE_BY_NAME.get(m.group(1).strip().lower())


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


def _maybe_fire(entity_type, entity_id, text):
    if entity_type not in ("job", "lead"):
        return
    key = _stage_key_from_text(text)
    if not key:
        return
    for auto in db.all_rows("automations", "trigger_stage=? AND active=1", (key,)):
        try:
            _execute(auto, entity_type, entity_id)
        except Exception:
            pass


def fire_invoice_overdue(inv, job=None):
    """Status-engine entry point for overdue invoices. Invoices have no pipeline stage,
    so the invoice layer calls this when one is first observed overdue. Fires any active
    'invoice_overdue' automations against the linked job (draft nudge — never sends)."""
    if not inv or not inv.get("job_id"):
        return
    for auto in db.all_rows("automations", "trigger_stage=? AND active=1", ("invoice_overdue",)):
        try:
            _execute(auto, "job", inv["job_id"])
        except Exception:
            pass


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
            _maybe_fire(entity_type, entity_id, text)
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
