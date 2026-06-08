# -*- coding: utf-8 -*-
"""Homeowner Portal — a secure, branded, login-free customer dashboard per job.

Each job gets a unique magic-link token (jobs.portal_token). The homeowner opens
/portal/<token> to: see project status + what's next, approve/e-sign their
proposal, view photos & documents, see their payment schedule + pay, and message
the company. Every action writes back to the CRM job (activity log, e-signature).
Read-mostly + a few safe write actions — no CRM login, no access to other jobs.
"""
import os
import re
import time
import secrets

from flask import (Blueprint, render_template, request, redirect, url_for,
                   abort, flash, jsonify)

import config
import db
import theme
import constants

bp = Blueprint("portal", __name__, url_prefix="/portal")

# Ensure the token column + portal config columns exist (module-load convention).
try:
    db.execute("ALTER TABLE jobs ADD COLUMN portal_token TEXT")
except Exception:
    pass
for _c in ("photo_app_url TEXT", "tutorials TEXT"):
    try:
        db.execute("ALTER TABLE company_settings ADD COLUMN %s" % _c)
    except Exception:
        pass
# Document e-signature + a per-job payment link (Stripe/Square/QBO/PayPal/etc.).
for _t, _c in (("documents", "needs_sign INTEGER DEFAULT 0"), ("documents", "signed_name TEXT"),
               ("documents", "signed_at TEXT"), ("documents", "signature TEXT"),
               ("jobs", "pay_url TEXT")):
    try:
        db.execute("ALTER TABLE %s ADD COLUMN %s" % (_t, _c))
    except Exception:
        pass
db._COLCACHE.clear()

# Customer-facing "what to expect" — description + typical timeframe per phase.
PHASE_INFO = [
    ("Approved", "We finalize your contract, color/material selections, and paperwork.", "1–3 days"),
    ("Permitting", "We submit your permit (and HOA approval if needed) to the city and wait for it to be issued.", "1–4 weeks"),
    ("Scheduling", "We order your materials, schedule the crew, and confirm your install date with you.", "3–7 days"),
    ("Installation", "Tear-off, dry-in, and your new roof goes on. Most homes are completed in 1–2 days.", "1–3 days"),
    ("Final Inspection", "The city inspects the finished roof and we close out the permit.", "1–2 weeks"),
    ("Complete", "Final walkthrough, warranty registration, and you're all set. Thank you!", "—"),
]


def _tutorials(company):
    """Parse the company's tutorials config ('Title | URL' per line)."""
    out = []
    for line in (company.get("tutorials") or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if "|" in line:
            t, u = line.split("|", 1)
            out.append((t.strip(), u.strip()))
        else:
            out.append((line, ""))
    return out

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
    all_docs = db.all_rows("documents", "job_id=?", (j["id"],), "id DESC")
    # Documents the company has requested the homeowner to e-sign (not yet signed).
    docs_to_sign = [d for d in all_docs if d.get("needs_sign") and not d.get("signed_at")]
    # Only show customer-appropriate documents (contracts, permits, warranties, COI).
    show_cats = {"Contract", "Permit", "Warranty", "COI", "NOA", "Measurement", "HOA"}
    documents = [d for d in all_docs if (d.get("category") or "") in show_cats]
    invoices = db.all_rows("invoices", "job_id=?", (j["id"],), "id DESC")
    activity = [a for a in db.entity_activity("job", j["id"])
                if a.get("kind") in ("stage", "note", "automation")][:10]
    rep = next((u for u in db.all_rows("users") if u.get("name") == j.get("rep")), None)
    company = db.get_company()
    # Build the what's-next checklist: each phase with done/current flag + timeframe.
    checklist = []
    for i, (name, desc, tf) in enumerate(PHASE_INFO):
        checklist.append({"name": name, "desc": desc, "timeframe": tf,
                          "done": i < j["_phase"], "current": i == j["_phase"]})
    contract = next((d for d in documents if (d.get("category") or "") == "Contract"), None)
    # Product collateral from the company Document Library, matched to this roof's
    # system (data sheets, color charts, warranties) — shingle/tile/metal/flat.
    from modules import ahj as ahj_mod
    sysk = (j.get("system") or ahj_mod.work_type_to_system(j.get("work_type", "")) or "").lower()
    prod_cats = ("Product & Color Charts", "Warranties")
    lib = db.all_rows("library_docs")
    # Detect a doc's roof system by brand/product keyword so a metal homeowner
    # never sees tile catalogs, etc. Docs with no system keyword are generic
    # (underlayment, skylights, sample warranties) and show for everyone.
    SYS_KW = {
        "shingle": ("shingle", "gaf", "timberline", "owens", "trudefinition", "duration", "landmark", "hdz", "ir-xe"),
        "tile": ("tile", "westlake", "eagle", "saxony", "barcelona", "villa", "crown", "tu_plus", "tu-plus"),
        "metal": ("metal", "dynamic", "galvalume", "standing", "seam", "englert", "dmc", "dm-", "dm_", "_mts", "ss_metal"),
        "flat": ("flat", "tpo", "modbit", "mod_bit", "mod-bit", "polyglass_sa", "hot-mop", "hotmop", "built-up", "bur"),
    }

    def _doc_systems(name):
        n = (name or "").lower()
        return {s for s, kws in SYS_KW.items() if any(k in n for k in kws)}

    def _relevant(d):
        if d.get("category") not in prod_cats:
            return False
        ds = _doc_systems(d.get("original_name"))
        return (sysk in ds) if ds else True  # match system, or generic (no system keyword)
    product_docs = [d for d in lib if _relevant(d)]
    # system-specific first, generic after
    product_docs.sort(key=lambda d: 0 if _doc_systems(d.get("original_name")) else 1)
    product_docs = product_docs[:16]
    return render_template("portal_dashboard.html", j=j, token=token,
                           phases=CUSTOMER_PHASES, estimates=estimates, photos=photos,
                           documents=documents, docs_to_sign=docs_to_sign, invoices=invoices,
                           activity=activity, pay_url=j.get("pay_url"),
                           rep=rep, draws=constants.DRAW_SCHEDULE,
                           checklist=checklist, contract=contract,
                           tutorials=_tutorials(company), product_docs=product_docs, sysk=sysk,
                           photo_app_url=company.get("photo_app_url"))


@bp.route("/<token>/upload-doc", methods=["POST"])
def upload_doc(token):
    j = _job_by_token(token)
    if not j:
        abort(404)
    f = request.files.get("file")
    if f and f.filename:
        fn = "%d_%s" % (int(time.time() * 1000), re.sub(r"[^A-Za-z0-9._-]+", "_", f.filename))
        path = os.path.join(config.DOC_DIR, fn)
        f.save(path)
        cat = request.form.get("category", "HOA")
        from modules import gdrive
        db.insert("documents", {"job_id": j["id"], "category": cat, "filename": fn,
                                "original_name": f.filename, "drive_id": gdrive.mirror(path, fn),
                                "size": os.path.getsize(path),
                                "notes": "Uploaded by homeowner"})
        db.add_activity("job", j["id"], "note", "Homeowner uploaded a document (%s): %s" % (cat, f.filename))
        db.update("jobs", j["id"], next_follow=db.today())
        flash("Thanks — your document was uploaded.", "ok")
    return redirect(url_for("portal.home", token=token) + "#documents")


@bp.route("/<token>/upload-photo", methods=["POST"])
def upload_photo(token):
    j = _job_by_token(token)
    if not j:
        abort(404)
    f = request.files.get("file")
    if f and f.filename:
        fn = "%d_%s" % (int(time.time() * 1000), re.sub(r"[^A-Za-z0-9._-]+", "_", f.filename))
        path = os.path.join(config.PHOTO_DIR, fn)
        f.save(path)
        from modules import gdrive
        db.insert("photos", {"job_id": j["id"], "album": "Homeowner", "phase": "homeowner",
                             "caption": request.form.get("caption", ""), "filename": fn,
                             "original_name": f.filename, "drive_id": gdrive.mirror(path, fn)})
        db.add_activity("job", j["id"], "note", "Homeowner uploaded a photo: %s" % f.filename)
        flash("Thanks — your photo was uploaded.", "ok")
    return redirect(url_for("portal.home", token=token) + "#photos")


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


@bp.route("/<token>/sign-doc/<int:doc_id>", methods=["POST"])
def sign_doc(token, doc_id):
    """Homeowner e-signs a document the company requested (contract, change order…)."""
    j = _job_by_token(token)
    if not j:
        abort(404)
    d = db.get("documents", doc_id)
    if not d or d.get("job_id") != j["id"]:
        abort(404)
    name = (request.form.get("signed_name") or "").strip()
    sig = request.form.get("signature") or ""
    if not name:
        flash("Please type your name to sign.", "error")
        return redirect(url_for("portal.home", token=token) + "#sign")
    db.update("documents", doc_id, signed_name=name, signed_at=db.now(),
              signature=sig, needs_sign=0)
    db.add_activity("job", j["id"], "automation",
                    "✅ Document e-signed by homeowner: %s (%s)" % (d.get("original_name", ""), name))
    db.update("jobs", j["id"], next_follow=db.today())
    flash("Thank you — your signature was recorded.", "ok")
    return redirect(url_for("portal.home", token=token) + "#sign")


@bp.route("/<token>/pay")
@bp.route("/<token>/pay/<int:inv_id>")
def pay(token, inv_id=None):
    """Send the homeowner to a payment link: the invoice's QuickBooks link if it
    has one, otherwise the job's payment URL (Stripe/Square/QBO/PayPal, etc.)."""
    j = _job_by_token(token)
    if not j:
        abort(404)
    link = None
    if inv_id:
        inv = db.get("invoices", inv_id)
        if inv and inv.get("job_id") == j["id"]:
            link = inv.get("payment_link")
    link = link or j.get("pay_url")
    if link:
        db.add_activity("job", j["id"], "note", "Homeowner opened the payment link.")
        return redirect(link)
    flash("Online payment isn't set up yet — please contact us to pay.", "info")
    return redirect(url_for("portal.home", token=token) + "#payments")
