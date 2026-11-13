# -*- coding: utf-8 -*-
"""Homeowner side of MyRoofPortal — compare roof quotes apples to apples.

The roofer-facing front door sells the portal. This is the other audience: the
homeowner holding three bids who cannot tell why one is $9,000 more than the
next. They are not the same roof, and the difference is never in the price — it
is in the twenty lines underneath it that only one of the three quotes bothered
to write down.

So the tool is not a price calculator. It is a SPEC SHEET: the homeowner marks,
per quote, which of these line items the contractor actually put in writing.
What falls out is (a) the real comparison, (b) a list of questions to ask each
contractor, generated from that quote's own gaps, and (c) a project tracker for
after they pick someone.

Deliberately NOT here: any claim about what a roof should cost, any ranking of
contractors, any "recommended" bid. We are not licensed to advise on their job
and we do not see it. Every judgement stays with the homeowner; this only makes
sure nothing is invisible when they make it.
"""
import json
import re
import secrets

from flask import (Blueprint, render_template, request, redirect, url_for,
                   abort, jsonify)

import db

bp = Blueprint("compare", __name__)

# --------------------------------------------------------------------------
# Storage. Separate from portal_leads/jobs: a homeowner comparing bids has no
# contractor and no job yet, and may never have one here.
# --------------------------------------------------------------------------
try:
    db.execute("""CREATE TABLE IF NOT EXISTS ho_projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created TEXT, token TEXT, email TEXT, name TEXT,
        address TEXT, city TEXT, state TEXT, zip TEXT,
        stage TEXT, notes TEXT, source_host TEXT, last_seen TEXT)""")
except Exception:
    pass
try:
    db.execute("""CREATE TABLE IF NOT EXISTS ho_quotes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER, created TEXT,
        contractor TEXT, phone TEXT, email TEXT, license TEXT,
        price TEXT, spec TEXT, notes TEXT)""")
except Exception:
    pass
db._COLCACHE.clear()


# --------------------------------------------------------------------------
# The spec sheet. This list IS the product — the UI, the gap analysis and the
# printed question list all read from it, so adding a row adds it everywhere.
#
# `ask` is written to be read aloud on the phone, in the homeowner's words, not
# ours. `why` says what it costs them to leave it unanswered, because a
# checklist nobody understands gets skipped.
# --------------------------------------------------------------------------
SPEC = [
    ("Tear-off and what's underneath", [
        dict(key="tearoff",
             label="Full tear-off to the deck",
             why="Going over the old shingles is cheaper now and hides rot you'll pay for later.",
             ask="Are you tearing the old roof off down to the wood, or laying over it? How many layers are on there now?"),
        dict(key="decking",
             label="Rotten decking — allowance in writing",
             why="Nobody knows what's under there until it's open. Without a written per-sheet price this becomes a blank cheque on the day.",
             ask="How many sheets of plywood are included, and what do you charge per sheet after that?"),
        dict(key="deck_fasten",
             label="Re-nailing the deck to current code",
             why="Many areas require the existing deck re-nailed before the new roof goes on. If it's not in the bid it becomes a change order.",
             ask="Does your price include re-nailing the deck to code, and is that line on the permit?"),
    ]),
    ("The layers that keep water out", [
        dict(key="underlay",
             label="Underlayment — synthetic, not felt",
             why="Felt and synthetic are not the same product or the same price. This is a common place for a cheap bid to save money quietly.",
             ask="What underlayment are you using — brand and product name? Synthetic or felt?"),
        dict(key="ice_water",
             label="Ice-and-water shield at valleys, eaves and penetrations",
             why="This is the membrane that saves you in wind-driven rain. It's also easy to leave off a bid without anyone noticing.",
             ask="Where exactly are you putting ice-and-water shield — valleys, eaves, around every pipe and skylight?"),
        dict(key="valleys",
             label="How the valleys are done",
             why="Open metal and closed-cut valleys are different work at different prices and different lifespans.",
             ask="Are the valleys open metal or closed cut? If metal, what gauge and finish?"),
        dict(key="flashing",
             label="NEW flashing — not re-used",
             why="Re-using old step and counter flashing on a new roof is the single most common source of a leak at a wall or chimney.",
             ask="Is all the step, counter and chimney flashing brand new, or are you re-using what's there?"),
        dict(key="boots",
             label="New pipe boots and vent collars",
             why="Cracked rubber boots outlive nothing. Old ones on a new roof will leak before the warranty is up.",
             ask="Are all the pipe boots and vent flashings being replaced with new?"),
        dict(key="drip_edge",
             label="New drip edge",
             why="Code in most places, and the edge detail is where a roof starts failing.",
             ask="Is new drip edge included on eaves and rakes, and what gauge?"),
    ]),
    ("The roof itself", [
        dict(key="shingle",
             label="Shingle — brand AND exact product line",
             why='"Architectural shingle" is not a product. Two lines from the same brand can differ by thousands over the roof.',
             ask="Which brand and which exact shingle line? Not the category — the product name."),
        dict(key="starter",
             label="Manufacturer starter strip",
             why="Cutting up field shingles for starter is faster and cheaper, and it can void the wind warranty.",
             ask="Are you using the manufacturer's starter strip, or cut shingles, along the eaves and rakes?"),
        dict(key="ridge_cap",
             label="Manufacturer ridge cap",
             why="Same story as starter — cut-up field shingles at the ridge is a warranty problem.",
             ask="Is the ridge cap the manufacturer's cap product, or cut field shingles?"),
        dict(key="vent",
             label="Ventilation — intake AND exhaust",
             why="Exhaust without intake does nothing and will cook the roof. It is also the most common cause of a denied manufacturer warranty claim.",
             ask="What ventilation am I getting, and is there enough intake at the soffits to feed it?"),
    ]),
    ("Paperwork, warranty and who's liable", [
        dict(key="permit",
             label="Permit pulled by the contractor, in their name",
             why="If you pull it yourself, you are the contractor as far as the county is concerned, and you own every defect.",
             ask="Are you pulling the permit in your company's name, and is the fee in this price?"),
        dict(key="license",
             label="License number on the quote",
             why="You can check it in two minutes. A quote without one is not a quote from a licensed contractor.",
             ask="What's your license number? I'd like to look it up."),
        dict(key="insurance",
             label="Liability AND workers' comp certificates",
             why="If someone is hurt on your roof and there's no comp, the claim can land on your homeowner's policy.",
             ask="Can you have your insurer send me certificates for general liability and workers' comp, with my address listed?"),
        dict(key="mfr_warranty",
             label="Manufacturer warranty — which one, and registered",
             why="The headline number on the brochure usually requires a certified installer and registration after the job. Otherwise you have the basic one.",
             ask="Which manufacturer warranty do I actually get, who registers it, and what do I need to do to keep it valid?"),
        dict(key="labor_warranty",
             label="Workmanship warranty — years, in writing, transferable",
             why="The manufacturer covers the shingle. Only the contractor covers the installation, and installation is what leaks.",
             ask="How many years is your workmanship warranty, is it in writing, and does it transfer if I sell the house?"),
    ]),
    ("Money and the day itself", [
        dict(key="payment",
             label="Payment schedule — deposit and draws",
             why="A large deposit before any material is delivered is the warning sign in this trade.",
             ask="What's the deposit, when are the other payments due, and what's the final payment tied to?"),
        dict(key="change_order",
             label="Change orders priced and signed BEFORE the work",
             why="This is the clause that stops a surprise invoice after the roof is already off.",
             ask="If you find something unexpected, do I get a written price before you do the work?"),
        dict(key="schedule",
             label="Start window and how many days",
             why="Your roof being open across a storm is a real cost. So is a crew that starts and disappears.",
             ask="When would you start, how many days, and what happens if weather stops you mid-job?"),
        dict(key="protect",
             label="Property protection, clean-up and magnet sweep",
             why="Nails in the driveway and a crushed AC unit are the two most common complaints after a re-roof.",
             ask="How do you protect the landscaping, the pool cage and the AC, and do you magnet-sweep when you're done?"),
    ]),
]

SPEC_INDEX = {i["key"]: i for _g, items in SPEC for i in items}
SPEC_KEYS = [i["key"] for _g, items in SPEC for i in items]

# Where a homeowner actually is, start to finish. The checklist per stage is the
# point: "what should I have in my hand right now."
STAGES = [
    dict(key="comparing", label="Comparing quotes",
         todo=["Get at least three written quotes",
               "Fill in the spec sheet for each one",
               "Ask every open question below and write the answer down",
               "Look up each license number"]),
    dict(key="chosen", label="Contractor chosen",
         todo=["Get the final quote re-issued with every answer written into it",
               "Get the insurance certificates sent directly from the insurer",
               "Read the change-order clause before signing"]),
    dict(key="signed", label="Contract signed",
         todo=["Keep a signed copy with the spec sheet attached",
               "Confirm the deposit amount matches the contract",
               "Get the start window in writing"]),
    dict(key="permit", label="Permit applied for",
         todo=["Confirm the permit is in the contractor's name",
               "Note the permit number",
               "Check it's issued before anyone starts"]),
    dict(key="scheduled", label="Materials ordered and scheduled",
         todo=["Confirm the shingle line and colour on the delivery ticket",
               "Move cars, patio furniture and anything on the walls",
               "Agree where the dumpster goes"]),
    dict(key="install", label="Install under way",
         todo=["Photograph the bare deck before the new roof covers it",
               "Photograph any wood they replace, before it's covered",
               "Get any change order in writing before you agree to it"]),
    dict(key="inspection", label="Final inspection",
         todo=["Confirm the county inspection passed",
               "Walk the property for nails and damage before final payment",
               "Hold final payment until the inspection is signed off"]),
    dict(key="done", label="Done — paperwork in hand",
         todo=["Manufacturer warranty registered, with the certificate emailed to you",
               "Written workmanship warranty",
               "Final invoice marked paid in full",
               "Closed permit"]),
]
STAGE_INDEX = {s["key"]: s for s in STAGES}
STAGE_KEYS = [s["key"] for s in STAGES]

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _tok():
    return secrets.token_urlsafe(12)


def _spec_of(q):
    """A quote's answers as {key: {"v": yes|no|?, "d": detail}}. Fail-soft: a
    corrupt blob must never 500 the page the homeowner is working in."""
    try:
        d = json.loads(q.get("spec") or "{}")
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _val(spec, key):
    v = (spec.get(key) or {})
    return (v.get("v") or "?") if isinstance(v, dict) else "?"


def gaps_for(q):
    """Every spec line this quote does NOT put in writing, as questions to ask.
    'no' and 'not addressed' both land here: the homeowner needs to raise both,
    they just mean different things once they do."""
    spec = _spec_of(q)
    out = []
    for key in SPEC_KEYS:
        v = _val(spec, key)
        if v != "yes":
            item = SPEC_INDEX[key]
            out.append(dict(item, state=v))
    return out


def disagreements(quotes):
    """Spec lines the quotes do not agree on — the actual reason the prices
    differ. Shown above the per-quote gaps because it is the whole question the
    homeowner came here with."""
    out = []
    for key in SPEC_KEYS:
        vals = {}
        for q in quotes:
            vals[_val(_spec_of(q), key)] = True
        if "yes" in vals and len(vals) > 1:
            out.append(SPEC_INDEX[key])
    return out


def _project_by_token(token):
    rows = db.all_rows("ho_projects", "token=?", (token,), "id DESC", 1)
    if not rows:
        abort(404)
    return rows[0]


def _quotes_for(pid):
    return db.all_rows("ho_quotes", "project_id=?", (pid,), "id ASC")


def _log(kind, path=""):
    try:
        db.insert("portal_events", {
            "created": db.now(), "kind": kind, "path": path or request.path,
            "referrer": (request.referrer or "")[:300],
            "ua": (request.headers.get("User-Agent") or "")[:300],
            "source_host": (request.host or "")[:120]})
    except Exception:
        pass


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------
@bp.route("/compare", endpoint="landing")
def landing():
    """The homeowner front door.

    On a demo host (myroofportal.com) this IS "/" — app.py renders this view in
    place for the bare domain. /compare then 301s to "/" so the same page is not
    indexed at two URLs. On the CRM host "/" is the dashboard, so /compare stays
    a real page there and must not redirect.
    """
    try:
        from modules.demos import _on_demo_host
        if _on_demo_host() and request.path.rstrip("/") == "/compare":
            return redirect("/", code=301)
    except Exception:
        pass
    _log("compare_view")
    return render_template("compare_landing.html", spec=SPEC,
                           spec_count=len(SPEC_KEYS), stages=STAGES)


@bp.route("/compare/start", methods=["POST"], endpoint="start")
def start():
    email = (request.form.get("email") or "").strip()[:200]
    if not _EMAIL_RE.match(email):
        return redirect(url_for("compare.landing") + "?err=1#start")
    name = (request.form.get("name") or "").strip()[:120]
    # One workspace per e-mail: coming back with the same address returns the
    # project instead of silently starting an empty second one.
    prior = db.all_rows("ho_projects", "email=?", (email,), "id DESC", 1)
    if prior:
        db.update("ho_projects", prior[0]["id"], last_seen=db.now())
        _log("compare_return")
        return redirect(url_for("compare.project", token=prior[0]["token"]))
    token = _tok()
    db.insert("ho_projects", {
        "created": db.now(), "token": token, "email": email, "name": name,
        "address": (request.form.get("address") or "").strip()[:200],
        "city": "", "state": "", "zip": "", "stage": "comparing", "notes": "",
        "source_host": (request.host or "")[:120], "last_seen": db.now()})
    _log("compare_start")
    return redirect(url_for("compare.project", token=token))


@bp.route("/c/<token>", endpoint="project")
def project(token):
    p = _project_by_token(token)
    quotes = _quotes_for(p["id"])
    try:
        db.update("ho_projects", p["id"], last_seen=db.now())
    except Exception:
        pass
    _log("compare_project", "/c/<token>")
    view = []
    for q in quotes:
        view.append(dict(q, _spec=_spec_of(q), _gaps=gaps_for(q)))
    return render_template(
        "compare_project.html", p=p, quotes=view, spec=SPEC,
        spec_keys=SPEC_KEYS, stages=STAGES,
        stage=STAGE_INDEX.get(p.get("stage") or "comparing", STAGES[0]),
        stage_keys=STAGE_KEYS,
        disagree=disagreements(quotes))


@bp.route("/c/<token>/quote", methods=["POST"], endpoint="save_quote")
def save_quote(token):
    p = _project_by_token(token)
    qid = (request.form.get("qid") or "").strip()
    spec = {}
    for key in SPEC_KEYS:
        v = (request.form.get("v_" + key) or "?").strip()
        if v not in ("yes", "no", "?"):
            v = "?"
        d = (request.form.get("d_" + key) or "").strip()[:300]
        if v != "?" or d:
            spec[key] = {"v": v, "d": d}
    row = {
        "contractor": (request.form.get("contractor") or "").strip()[:160],
        "phone": (request.form.get("phone") or "").strip()[:40],
        "email": (request.form.get("email") or "").strip()[:200],
        "license": (request.form.get("license") or "").strip()[:80],
        "price": (request.form.get("price") or "").strip()[:40],
        "notes": (request.form.get("notes") or "").strip()[:2000],
        "spec": json.dumps(spec),
    }
    if not row["contractor"]:
        return redirect(url_for("compare.project", token=token) + "?err=name")
    if qid.isdigit():
        existing = db.get("ho_quotes", int(qid))
        if not existing or existing.get("project_id") != p["id"]:
            abort(404)
        db.update("ho_quotes", int(qid), **row)
    else:
        row["project_id"] = p["id"]
        row["created"] = db.now()
        db.insert("ho_quotes", row)
    _log("compare_quote_saved", "/c/<token>/quote")
    return redirect(url_for("compare.project", token=token) + "#quotes")


@bp.route("/c/<token>/quote/<int:qid>/delete", methods=["POST"], endpoint="del_quote")
def del_quote(token, qid):
    p = _project_by_token(token)
    q = db.get("ho_quotes", qid)
    if not q or q.get("project_id") != p["id"]:
        abort(404)
    db.delete("ho_quotes", qid)
    return redirect(url_for("compare.project", token=token) + "#quotes")


@bp.route("/c/<token>/stage", methods=["POST"], endpoint="set_stage")
def set_stage(token):
    p = _project_by_token(token)
    s = (request.form.get("stage") or "").strip()
    if s in STAGE_INDEX:
        db.update("ho_projects", p["id"], stage=s)
        _log("compare_stage_" + s, "/c/<token>/stage")
    return redirect(url_for("compare.project", token=token) + "#track")


@bp.route("/c/<token>/note", methods=["POST"], endpoint="save_note")
def save_note(token):
    p = _project_by_token(token)
    db.update("ho_projects", p["id"],
              notes=(request.form.get("notes") or "").strip()[:8000])
    return redirect(url_for("compare.project", token=token) + "#notes")
