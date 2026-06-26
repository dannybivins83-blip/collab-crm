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
     "body": "I agree to the payment schedule: 30% deposit (permit, material order & admin), "
             "30% at job start (mobilization), 30% at 2 of 3 inspections passed, and 10% at final "
             "inspection / completion. Total contract: {value}."},
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


# System-specific selections — the real content from the company confirmation forms
# (e.g. Metal Roof Confirmation: panel brand/style, color). Replaces the generic
# "color" clause with a richer spec block per system. 'choice' renders as options.
SYSTEM_EXTRAS = {
    "metal": [
        {"key": "panel_brand", "type": "field", "title": "Panel Brand", "default": "Dynamic Metals",
         "body": "Metal roof panel brand."},
        {"key": "panel_style", "type": "choice", "title": "Panel Style", "options": ["Flat Panel", "Striations", "Beads"],
         "body": "Select your standing-seam panel profile."},
        {"key": "panel_gauge", "type": "field", "title": "Panel Gauge", "default": "24 ga",
         "body": "Panel gauge."},
        {"key": "color", "type": "field", "title": "Color Selection", "body": "Final metal color (Galvalume or standard color)."},
    ],
    "tile": [
        {"key": "tile_mfr", "type": "field", "title": "Tile Manufacturer", "default": "Westlake Royal",
         "body": "Tile manufacturer (e.g. Westlake Royal, Eagle)."},
        {"key": "tile_profile", "type": "choice", "title": "Tile Profile", "options": ["Flat", "Barcelona / S-Tile", "Villa"],
         "body": "Select your tile profile."},
        {"key": "color", "type": "field", "title": "Color Selection", "body": "Final tile color / blend."},
    ],
    "shingle": [
        {"key": "shingle_mfr", "type": "field", "title": "Shingle Manufacturer", "default": "Owens Corning",
         "body": "Shingle manufacturer (e.g. Owens Corning, GAF)."},
        {"key": "shingle_line", "type": "field", "title": "Shingle Line", "default": "TruDefinition Duration",
         "body": "Shingle product line."},
        {"key": "color", "type": "field", "title": "Color Selection", "body": "Final shingle color."},
    ],
    "flat": [
        {"key": "flat_type", "type": "choice", "title": "Flat Roof System", "options": ["TPO", "3-ply SA Mod-Bit", "Hot-Mop BUR"],
         "body": "Select your flat roof system."},
        {"key": "color", "type": "field", "title": "Color Selection", "body": "Final color (if applicable)."},
    ],
}


def _prefill_pdf(src_path, ctx):
    """Stamp the customer header onto a flat company PDF by locating its labels
    (Customer Name / Address / Job Site Address / Phone / Date) and writing the
    value just to the right. Best-effort + generalized (no per-form coordinates).
    Returns the filled PDF bytes, or None on failure."""
    try:
        import io
        from pypdf import PdfReader, PdfWriter
        from reportlab.pdfgen import canvas
        from reportlab.pdfbase.pdfmetrics import stringWidth
        reader = PdfReader(src_path)
        writer = PdfWriter()
        # (label substring, value) — most specific first so "Job Site Address" wins over "Address".
        targets = [
            ("job site address", ctx.get("address", "")),
            ("customer name", ctx.get("name", "")),
            ("property owner", ctx.get("name", "")),
            ("phone", ctx.get("phone", "")),
            ("address", ctx.get("address", "")),
            ("name", ctx.get("name", "")),
            ("date", db.today()),
        ]
        any_placed = False
        for pi, page in enumerate(reader.pages):
            chunks = []

            def vis(text, cm, tm, fd, fs, _c=chunks):
                t = (text or "").strip()
                if t:
                    try:
                        _c.append((t, float(tm[4]), float(tm[5]), float(fs or 10)))
                    except Exception:
                        pass
            if pi < 3:  # header fields live on the first pages
                try:
                    page.extract_text(visitor_text=vis)
                except Exception:
                    pass
            mb = page.mediabox
            w, h = float(mb.width), float(mb.height)
            buf = io.BytesIO()
            cv = canvas.Canvas(buf, pagesize=(w, h))
            cv.setFont("Helvetica", 10)
            used_chunk, placed_field = set(), set()
            for needle, val in targets:
                if not val or needle in placed_field:
                    continue
                for ci, (txt, x, y, fs) in enumerate(chunks):
                    if ci in used_chunk:
                        continue
                    if needle in txt.lower():
                        vx = x + stringWidth(txt, "Helvetica", min(fs, 12)) + 8
                        cv.setFont("Helvetica", min(max(fs, 8), 11))
                        cv.drawString(vx, y, str(val))
                        used_chunk.add(ci)
                        placed_field.add(needle)
                        any_placed = True
                        break
            cv.save()
            buf.seek(0)
            overlay = PdfReader(buf).pages[0]
            page.merge_page(overlay)
            writer.add_page(page)
        if not any_placed:
            return None
        out = io.BytesIO()
        writer.write(out)
        return out.getvalue()
    except Exception:
        return None


def _seed():
    if db.all_rows("signup_templates"):
        return
    for s in SYSTEMS:
        db.insert("signup_templates", {"system": s, "items": json.dumps(_BASE_ITEMS), "updated": db.now()})


_seed()


def template_for(system):
    system = (system or "shingle").lower()
    rows = db.all_rows("signup_templates", "system=?", (system,))
    items = db.load_json(rows[0]["items"], _BASE_ITEMS) if rows else list(_BASE_ITEMS)
    extras = SYSTEM_EXTRAS.get(system, [])
    if not extras:
        return items
    # Replace the generic "color" clause with the system spec block (brand/style/color);
    # skip any extra keys already present so an edited template never double-lists them.
    extra_keys = {e["key"] for e in extras}
    out, injected = [], False
    for it in items:
        if it.get("key") == "color" and not injected:
            out.extend(extras)
            injected = True
        elif it.get("key") in extra_keys:
            continue
        else:
            out.append(it)
    if not injected:
        out = [out[0]] + extras + out[1:] if out else extras
    return out


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
    # Load Sign-Up Packages once; reuse for both the reference-doc lookup and PDF prefill.
    _signup_docs = db.all_rows("library_docs", "category=?", ("Sign-Up Packages",))
    libdoc = None
    for d in _signup_docs:
        n = (d.get("original_name") or "").lower()
        if system in n and "sign" in n:
            libdoc = d.get("filename")
            break
    pid = db.insert("signup_packets", {"created": db.now(), "job_id": job_id, "system": system,
                                       "status": "sent", "customer_name": job.get("name", ""),
                                       "responses": "{}", "library_doc": libdoc})
    # Part B: stamp the customer header onto the ACTUAL company forms (sign-up package
    # + any matching confirmation form) and attach the pre-filled copies to the job.
    ctx = packet_context(job)
    libdir = os.path.join(config.UPLOAD_DIR, "library")
    prefilled = 0
    for d in _signup_docs:
        n = (d.get("original_name") or "").lower()
        if not (system in n and ("sign" in n or "confirmation" in n)):
            continue
        if not d["filename"].lower().endswith(".pdf"):
            continue
        src = os.path.join(libdir, d["filename"])
        if not os.path.exists(src):
            continue
        data = _prefill_pdf(src, ctx)
        if not data:
            continue
        fn = "Prefilled_%d_%s" % (pid, d["filename"])
        path = os.path.join(config.DOC_DIR, fn)
        with open(path, "wb") as f:
            f.write(data)
        did = None
        try:
            from modules import gdrive
            did = gdrive.mirror(path, fn)
            if len(data) <= getattr(gdrive, "MAX_BLOB", 4000000):
                gdrive.blob_put(fn, data, "application/pdf")  # serve live from Neon
        except Exception:
            pass
        db.insert("documents", {"job_id": job_id, "category": "Contract", "filename": fn,
                                "original_name": "Pre-filled: %s" % d["original_name"],
                                "size": len(data), "drive_id": did,
                                "notes": "Auto-filled company form (customer header)"})
        prefilled += 1
    # If the customer already e-signed the estimate AND authorized applying that signature
    # to the sign-up documents, auto-complete this packet with it (one signature everywhere).
    auto_signed = False
    if job.get("sign_consent") and job.get("signature"):
        import re as _re
        raw_items = template_for(system)
        items2 = [{**it, "body": _fill(it["body"], ctx)} for it in raw_items]
        nm = job.get("signed_name") or job.get("name") or ""
        initials = "".join(w[0] for w in _re.findall(r"[A-Za-z]+", nm)).upper()[:4]
        responses = {}
        for it in raw_items:
            if it["type"] == "initial":
                responses[it["key"]] = initials
            elif it["type"] == "sign":
                responses[it["key"]] = nm
        responses["signature"] = nm
        responses["signature_img"] = job["signature"]
        responses["auto_applied"] = "authorized estimate e-signature"
        signed_at = job.get("signed_at") or db.now()
        db.update("signup_packets", pid, responses=json.dumps(responses),
                  status="completed", signed_at=signed_at, customer_name=nm)
        p2 = db.get("signup_packets", pid)
        try:
            fn, size, did = _generate_pdf(p2, job, items2, responses, ctx)
            db.insert("documents", {"job_id": job_id, "category": "Contract", "filename": fn,
                      "original_name": "Signed Sign-Up Package (%s).pdf" % system, "size": size,
                      "drive_id": did, "signed_at": signed_at, "signed_name": nm,
                      "notes": "Auto-signed from the customer's authorized estimate e-signature"})
        except Exception:
            pass
        auto_signed = True

    if auto_signed:
        db.add_activity("job", job_id, "automation",
                        "Sign-up package auto-signed (%s) using the customer's authorized e-signature." % system)
    else:
        db.add_activity("job", job_id, "automation",
                        "Sign-up package sent (%s) — %d company form(s) pre-filled, awaiting homeowner" % (system, prefilled))
    db.update("jobs", job_id, next_follow=db.today())
    flash(("Sign-up package auto-signed from the customer's authorized e-signature." if auto_signed
           else "Sign-up package created%s — it's in the homeowner portal." % (
               " (%d company form(s) pre-filled)" % prefilled if prefilled else "")), "ok")
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
    from modules import notifications
    notifications.notify(job["id"], "sign", "%s completed & signed the %s sign-up package"
                         % (responses.get("signature") or p.get("customer_name") or "Homeowner", p["system"]))
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
        elif it["type"] in ("field", "choice"):
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
        if len(blob) <= getattr(gdrive, "MAX_BLOB", 4000000):
            gdrive.blob_put(fn, blob, "application/pdf")  # serve live from Neon
    except Exception:
        pass
    return fn, len(blob), did
