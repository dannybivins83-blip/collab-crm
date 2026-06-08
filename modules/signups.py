# -*- coding: utf-8 -*-
"""Online Sign-Up Packages.

Each roof system (shingle/tile/metal/flat) has a sign-up packet template made of
clauses the homeowner must initial + a final signature. The rep sends the packet
for a job; it's pre-filled with the customer/job info, the matching company PDF is
attached for reference, and the homeowner completes it (initials + signature) in
their portal. Completion is recorded on the job (activity + a saved record).
"""
import io
import os
import re
import json
import base64
import textwrap

from flask import (Blueprint, render_template, request, redirect, url_for, abort, flash)

import config
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
    raw_items = template_for(p["system"])
    ctx = packet_context(job)
    items = [{**it, "body": _fill(it["body"], ctx)} for it in raw_items]
    responses = {}
    missing = []
    for it in raw_items:
        val = (request.form.get("item_%s" % it["key"]) or "").strip()
        responses[it["key"]] = val
        if it["type"] in ("initial", "sign") and not val:
            missing.append(it["title"])
    responses["signature_img"] = request.form.get("item_signature_img", "")  # drawn signature dataURL
    if missing:
        flash("Please initial/sign: %s" % ", ".join(missing), "error")
        return redirect(url_for("signups.portal_view", token=token, packet_id=packet_id))
    signed_at = db.now()
    db.update("signup_packets", packet_id, responses=json.dumps(responses),
              status="completed", signed_at=signed_at,
              customer_name=responses.get("signature") or p.get("customer_name"))
    p = db.get("signup_packets", packet_id)
    db.add_activity("job", job["id"], "automation",
                    "✅ Sign-up package COMPLETED & signed by homeowner (%s roof)" % p["system"])
    # Render the completed, signed packet to a real PDF and file it as the contract doc.
    try:
        fn, size, did = _generate_pdf(p, job, items, responses, ctx)
        db.insert("documents", {"job_id": job["id"], "category": "Contract",
                                "filename": fn, "original_name": "Signed Sign-Up Package (%s).pdf" % p["system"],
                                "size": size, "drive_id": did, "notes": "Completed & signed online via portal",
                                "signed_at": signed_at, "signed_name": responses.get("signature", "")})
    except Exception as e:
        db.insert("documents", {"job_id": job["id"], "category": "Contract", "filename": "",
                                "original_name": "Signed Sign-Up Package (%s)" % p["system"],
                                "notes": "Completed online (PDF render failed: %s)" % e,
                                "signed_at": signed_at, "signed_name": responses.get("signature", "")})
    db.update("jobs", job["id"], next_follow=db.today())
    flash("Thank you! Your sign-up package is complete and your signed copy is saved.", "ok")
    return redirect(url_for("portal.home", token=token))


def _generate_pdf(packet, job, items, responses, ctx):
    """Render the completed packet (clauses + initials + drawn signature) to a PDF,
    save it under job documents, and mirror to Drive. Returns (filename, size, drive_id)."""
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.lib.utils import ImageReader
    company = db.get_company()
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    W, H = letter
    state = {"y": H - 0.8 * inch}

    def line(txt, size=10, dy=14, bold=False, x=0.8 * inch):
        if state["y"] < 1.0 * inch:
            c.showPage()
            state["y"] = H - 0.8 * inch
        c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        c.drawString(x, state["y"], txt)
        state["y"] -= dy

    def wrap(txt, size=9, width=98):
        for ln in textwrap.wrap(txt, width):
            line(ln, size, 12)

    c.setFont("Helvetica-Bold", 15)
    c.drawString(0.8 * inch, state["y"], company.get("name", ""))
    state["y"] -= 18
    line("Roof Replacement Sign-Up Package — %s roof" % packet["system"].capitalize(), 11, 16, True)
    line("Homeowner: %s        Date: %s" % (ctx["name"], db.today()))
    line("Property: %s" % ctx["address"])
    line("Contract total: %s        License: %s" % (ctx["value"], company.get("license", "")))
    state["y"] -= 8
    for i, it in enumerate(items, 1):
        if it["type"] == "sign":
            continue
        line("%d. %s" % (i, it["title"]), 10.5, 14, True)
        wrap(it["body"])
        val = responses.get(it["key"], "")
        if it["type"] == "initial":
            line("        Initialed:  %s" % val.upper(), 10, 16, True)
        elif it["type"] == "field":
            line("        Selection:  %s" % val, 10, 16, True)
        state["y"] -= 4
    state["y"] -= 8
    line("Homeowner Signature:", 10.5, 16, True)
    img = responses.get("signature_img", "")
    if img and img.startswith("data:image"):
        try:
            data = base64.b64decode(img.split(",", 1)[1])
            c.drawImage(ImageReader(io.BytesIO(data)), 0.9 * inch, state["y"] - 48,
                        width=2.4 * inch, height=0.7 * inch, mask="auto")
            state["y"] -= 54
        except Exception:
            pass
    line("%s        Signed: %s" % (responses.get("signature", ""), (packet.get("signed_at") or "")[:19]), 10, 16)
    line("Electronically signed via the %s customer portal." % company.get("name", ""), 8, 12)
    c.showPage()
    c.save()
    blob = buf.getvalue()
    fn = "SignedSignUp_%s_%s_%d.pdf" % (
        re.sub(r"[^A-Za-z0-9]+", "_", ctx["name"] or "homeowner")[:30], packet["system"], packet["id"])
    path = os.path.join(config.DOC_DIR, fn)
    with open(path, "wb") as f:
        f.write(blob)
    did = None
    try:
        from modules import gdrive
        did = gdrive.mirror(path, fn)
    except Exception:
        pass
    return fn, len(blob), did
