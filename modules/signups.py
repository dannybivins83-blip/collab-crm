# -*- coding: utf-8 -*-
"""Online Sign-Up Packages.

Each roof system (shingle/tile/metal/flat) has a sign-up packet template made of
clauses the homeowner must initial + a final signature. The rep sends the packet
for a job; it's pre-filled with the customer/job info, the matching company PDF is
attached for reference, and the homeowner completes it (initials + signature) in
their portal. Completion is recorded on the job (activity + a saved record).
"""
import json

from flask import (Blueprint, render_template, request, redirect, url_for, abort, flash)

import db
import constants

bp = Blueprint("signups", __name__, url_prefix="/signups")

# --- schema (module-load convention) ---------------------------------------
db.execute("""CREATE TABLE IF NOT EXISTS signup_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT, system TEXT, items TEXT, updated TEXT)""")
db.execute("""CREATE TABLE IF NOT EXISTS signup_packets (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created TEXT, job_id INTEGER, system TEXT,
    status TEXT DEFAULT 'sent', customer_name TEXT, responses TEXT DEFAULT '{}',
    signed_at TEXT, library_doc TEXT)""")
db._COLCACHE.clear()

SYSTEMS = ["shingle", "tile", "metal", "flat"]

# Default clauses (each requires the homeowner's initials), then a final signature.
# {system} / {name} / {address} / {value} are filled in per job.
_BASE_ITEMS = [
    {"key": "scope", "type": "initial", "title": "Scope of Work",
     "body": "I authorize {company} to perform a complete {system} roof replacement at {address} "
             "per the agreed proposal, including tear-off to deck, code-required re-nail, underlayment, "
             "the selected {system} roof covering, flashing, and cleanup."},
    {"key": "color", "type": "field", "title": "Color / Material Selection",
     "body": "Please confirm your selected color / material. Final selections must be confirmed before material is ordered."},
    {"key": "payment", "type": "initial", "title": "Payment Schedule",
     "body": "I agree to the payment schedule: 25% deposit, 25% at tear-off, 40% at material delivery, "
             "10% at completion. Total contract: {value}."},
    {"key": "permit_hoa", "type": "initial", "title": "Permit & HOA",
     "body": "I authorize {company} to pull the building permit on my behalf and, if applicable, to submit "
             "for HOA / ARC approval. I understand work cannot begin until the permit is issued."},
    {"key": "access", "type": "initial", "title": "Property Access & Preparation",
     "body": "On install day I will move vehicles from the driveway, keep pets indoors, and secure wall "
             "hangings. I understand satellite dishes / solar may need to be detached and reset by others."},
    {"key": "warranty", "type": "initial", "title": "Warranty & Florida Building Code",
     "body": "Work will comply with the Florida Building Code and the manufacturer's installation requirements. "
             "Workmanship and manufacturer warranties are issued upon final payment."},
    {"key": "cancel", "type": "initial", "title": "Right to Cancel",
     "body": "I understand I have the right to cancel this agreement within 3 business days of signing."},
    {"key": "signature", "type": "sign", "title": "Homeowner Signature",
     "body": "By signing below I acknowledge I have read and agree to all of the above."},
]


def _seed():
    if db.all_rows("signup_templates"):
        return
    for s in SYSTEMS:
        db.insert("signup_templates", {"system": s, "items": json.dumps(_BASE_ITEMS), "updated": db.now()})


_seed()


def template_for(system):
    rows = db.all_rows("signup_templates", "system=?", ((system or "shingle").lower(),))
    items = db.load_json(rows[0]["items"], _BASE_ITEMS) if rows else _BASE_ITEMS
    return items


def _fill(text, ctx):
    for k, v in ctx.items():
        text = text.replace("{%s}" % k, str(v or ""))
    return text


def packet_context(job):
    company = db.get_company()
    return {
        "company": company.get("name", ""),
        "name": job.get("name", ""),
        "address": ("%s%s %s %s" % (job.get("address", ""), (", " + job.get("city")) if job.get("city") else "",
                                    job.get("state", ""), job.get("zip", ""))).strip(),
        "value": job.get("contract_value", "") or "as proposed",
        "system": (job.get("system") or "roof"),
    }


def for_job(job_id):
    return db.all_rows("signup_packets", "job_id=?", (job_id,), "id DESC")


def open_packet_for_job(job_id):
    """The most recent not-yet-completed packet for a job (for the portal prompt)."""
    return next((p for p in for_job(job_id) if p.get("status") != "completed"), None)


# --- CRM: create / send a packet -------------------------------------------

@bp.route("/job/<int:job_id>/create", methods=["POST"])
def create(job_id):
    job = db.get("jobs", job_id)
    if not job:
        abort(404)
    system = (request.form.get("system") or job.get("system") or "shingle").lower()
    # Attach the matching company sign-up PDF (reference) from the library, if present.
    libdoc = None
    for d in db.all_rows("library_docs", "category=?", ("Sign-Up Packages",)):
        n = (d.get("original_name") or "").lower()
        if system in n and "sign" in n:
            libdoc = d.get("filename")
            break
    pid = db.insert("signup_packets", {"created": db.now(), "job_id": job_id, "system": system,
                                       "status": "sent", "customer_name": job.get("name", ""),
                                       "responses": "{}", "library_doc": libdoc})
    db.add_activity("job", job_id, "automation", "Sign-up package sent (%s) — awaiting homeowner completion" % system)
    db.update("jobs", job_id, next_follow=db.today())
    flash("Sign-up package created — it now appears in the homeowner portal.", "ok")
    return redirect(request.referrer or url_for("jobs.detail", job_id=job_id))


# --- Portal: complete a packet (public, token-gated) -----------------------

def _job_by_token(token):
    rows = db.all_rows("jobs", "portal_token=?", (token,)) if token else []
    return rows[0] if rows else None


@bp.route("/portal/<token>/<int:packet_id>")
def portal_view(token, packet_id):
    job = _job_by_token(token)
    p = db.get("signup_packets", packet_id)
    if not job or not p or p.get("job_id") != job["id"]:
        abort(404)
    ctx = packet_context(job)
    items = []
    for it in template_for(p["system"]):
        items.append({**it, "body": _fill(it["body"], ctx)})
    return render_template("signup_packet.html", token=token, packet=p, job=job, items=items,
                           ctx=ctx, responses=db.load_json(p.get("responses"), {}))


@bp.route("/portal/<token>/<int:packet_id>/complete", methods=["POST"])
def portal_complete(token, packet_id):
    job = _job_by_token(token)
    p = db.get("signup_packets", packet_id)
    if not job or not p or p.get("job_id") != job["id"]:
        abort(404)
    items = template_for(p["system"])
    responses = {}
    missing = []
    for it in items:
        val = (request.form.get("item_%s" % it["key"]) or "").strip()
        responses[it["key"]] = val
        if it["type"] in ("initial", "sign") and not val:
            missing.append(it["title"])
    if missing:
        flash("Please initial/sign: %s" % ", ".join(missing), "error")
        return redirect(url_for("signups.portal_view", token=token, packet_id=packet_id))
    db.update("signup_packets", packet_id, responses=json.dumps(responses),
              status="completed", signed_at=db.now(),
              customer_name=request.form.get("item_signature", "") or p.get("customer_name"))
    db.add_activity("job", job["id"], "automation",
                    "✅ Sign-up package COMPLETED & signed by homeowner (%s roof)" % p["system"])
    # Also record a job document marker so it shows in the Documents list.
    db.insert("documents", {"job_id": job["id"], "category": "Contract",
                            "filename": "", "original_name": "Signed Sign-Up Package (%s)" % p["system"],
                            "notes": "Completed online via portal", "signed_at": db.now(),
                            "signed_name": responses.get("signature", "")})
    db.update("jobs", job["id"], next_follow=db.today())
    flash("Thank you! Your sign-up package is complete.", "ok")
    return redirect(url_for("portal.home", token=token))
