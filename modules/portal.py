# -*- coding: utf-8 -*-
"""Homeowner Portal — a secure, branded, login-free customer dashboard per job.

Each job gets a unique magic-link token (jobs.portal_token). The homeowner opens
/portal/<token> to: see project status + what's next, approve/e-sign their
proposal, view photos & documents, see their payment schedule + pay, and message
the company. Every action writes back to the CRM job (activity log, e-signature).
Read-mostly + a few safe write actions — no CRM login, no access to other jobs.
"""
import secrets

from flask import (Blueprint, render_template, request, redirect, url_for,
                   abort, flash, jsonify)

import db
import theme
import constants

bp = Blueprint("portal", __name__, url_prefix="/portal")

# Ensure the token column exists (module-load convention used across the app).
try:
    db.execute("ALTER TABLE jobs ADD COLUMN portal_token TEXT")
except Exception:
    pass
db._COLCACHE.clear()

# Map the detailed production milestone -> a friendly customer-facing phase.
CUSTOMER_PHASES = ["Approved", "Permitting", "Scheduling", "Installation",
                   "Final Inspection", "Complete"]
_STAGE_TO_PHASE = {
    "approved": 0, "finance_ntp": 0, "documentation": 0,
    "permit_applied": 1, "permit_approved": 1,
    "precon_needed": 2, "precon_complete": 2, "ready_teardown": 2,
    "teardown_started": 3, "teardown_complete": 3, "install_started": 3,
    "install_complete": 3, "punch_needed": 3, "punch_complete": 3,
    "final_needed": 4, "final_scheduled": 4, "final_passed": 4,
    "completed": 5, "invoiced": 5, "closed": 5,
}


def ensure_token(job_id):
    """Return the job's portal token, generating + saving one if needed."""
    j = db.get("jobs", job_id)
    if not j:
        return None
    tok = j.get("portal_token")
    if not tok:
        tok = secrets.token_urlsafe(12)
        db.update("jobs", job_id, portal_token=tok)
    return tok


def _job_by_token(token):
    if not token:
        return None
    rows = db.all_rows("jobs", "portal_token=?", (token,))
    return rows[0] if rows else None


@bp.app_template_global()
def portal_link(job_id):
    """App-wide Jinja helper: the shareable homeowner-portal URL for a job."""
    tok = ensure_token(job_id)
    return url_for("portal.home", token=tok, _external=True) if tok else ""


def _phase_index(stage):
    return _STAGE_TO_PHASE.get(stage, 0)


def _decorate(j):
    payments = db.load_json(j.get("payments"), {})
    j["_payments"] = payments
    j["_paid_pct"] = theme.paid_pct(payments)
    j["_value"] = theme.est_num(j.get("contract_value"))
    j["_balance"] = j["_value"] * (1 - j["_paid_pct"])
    j["_phase"] = _phase_index(j.get("stage"))
    j["_stage_name"] = constants.job_stage(j.get("stage")).get("name", "")
    return j


@bp.route("/<token>")
def home(token):
    j = _job_by_token(token)
    if not j:
        abort(404)
    _decorate(j)
    estimates = db.all_rows("estimates", "job_id=?", (j["id"],), "id DESC")
    photos = db.all_rows("photos", "job_id=?", (j["id"],), "id DESC")
    documents = db.all_rows("documents", "job_id=?", (j["id"],), "id DESC")
    # Only show customer-appropriate documents (contracts, permits, warranties, COI).
    show_cats = {"Contract", "Permit", "Warranty", "COI", "NOA", "Measurement", "HOA"}
    documents = [d for d in documents if (d.get("category") or "") in show_cats]
    invoices = db.all_rows("invoices", "job_id=?", (j["id"],), "id DESC")
    activity = [a for a in db.entity_activity("job", j["id"])
                if a.get("kind") in ("stage", "note", "automation")][:10]
    rep = next((u for u in db.all_rows("users") if u.get("name") == j.get("rep")), None)
    return render_template("portal_dashboard.html", j=j, token=token,
                           phases=CUSTOMER_PHASES, estimates=estimates, photos=photos,
                           documents=documents, invoices=invoices, activity=activity,
                           rep=rep, draws=constants.DRAW_SCHEDULE)


@bp.route("/<token>/message", methods=["POST"])
def message(token):
    j = _job_by_token(token)
    if not j:
        abort(404)
    text = (request.form.get("text") or "").strip()
    kind = request.form.get("kind", "message")
    if text:
        label = "Homeowner change request" if kind == "request" else "Homeowner message"
        db.add_activity("job", j["id"], "note", "%s: %s" % (label, text))
        # surface it as a follow-up for the rep
        db.update("jobs", j["id"], next_follow=db.today())
        flash("Thanks! Your message was sent to our team.", "ok")
    return redirect(url_for("portal.home", token=token) + "#message")


@bp.route("/<token>/sign/<int:est_id>", methods=["POST"])
def sign(token, est_id):
    j = _job_by_token(token)
    if not j:
        abort(404)
    e = db.get("estimates", est_id)
    if not e or e.get("job_id") != j["id"]:
        abort(404)
    name = (request.form.get("signed_name") or "").strip()
    sig = request.form.get("signature") or ""
    if not name:
        flash("Please type your name to approve.", "error")
        return redirect(url_for("portal.home", token=token) + "#estimate")
    db.update("estimates", est_id, status="signed", signed_name=name,
              signed_at=db.now(), signature=sig)
    db.add_activity("job", j["id"], "automation",
                    "✅ Proposal %s approved & e-signed by homeowner (%s)" % (e.get("number", ""), name))
    db.update("jobs", j["id"], next_follow=db.today())
    flash("Thank you! Your proposal is approved and signed.", "ok")
    return redirect(url_for("portal.home", token=token) + "#estimate")


@bp.route("/<token>/pay/<int:inv_id>")
def pay(token, inv_id):
    """Send the homeowner to the QuickBooks pay link if one exists."""
    j = _job_by_token(token)
    if not j:
        abort(404)
    inv = db.get("invoices", inv_id)
    if inv and inv.get("job_id") == j["id"] and inv.get("payment_link"):
        db.add_activity("job", j["id"], "note", "Homeowner opened payment link for invoice %s" % inv.get("number", ""))
        return redirect(inv["payment_link"])
    flash("Online payment isn't set up for this invoice yet — please contact us.", "info")
    return redirect(url_for("portal.home", token=token) + "#payments")
