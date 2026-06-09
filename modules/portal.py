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
# Leads get their own portal token + an "invited" flag (dedupe the auto-invite email).
for _lc in ("portal_token TEXT", "portal_invited TEXT"):
    try:
        db.execute("ALTER TABLE leads ADD COLUMN %s" % _lc)
    except Exception:
        pass
for _c in ("photo_app_url TEXT", "tutorials TEXT", "auto_portal_invite INTEGER DEFAULT 0",
           "portal_perks TEXT",            # homeowner perks / giveaways / events shown in the portal
           "portal_notify INTEGER DEFAULT 0"):  # kill-switch: email homeowner on each milestone
    try:
        db.execute("ALTER TABLE company_settings ADD COLUMN %s" % _c)
    except Exception:
        pass
# Milestone updates shown in the homeowner portal (one per phase reached) + unread flag.
try:
    db.execute("""CREATE TABLE IF NOT EXISTS portal_updates (
        id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER, phase INTEGER,
        title TEXT, created TEXT, seen INTEGER DEFAULT 0)""")
except Exception:
    pass
# Document e-signature + a per-job payment link (Stripe/Square/QBO/PayPal/etc.).
for _t, _c in (("documents", "needs_sign INTEGER DEFAULT 0"), ("documents", "signed_name TEXT"),
               ("documents", "signed_at TEXT"), ("documents", "signature TEXT"),
               ("jobs", "pay_url TEXT"), ("jobs", "sitecam_url TEXT")):
    try:
        db.execute("ALTER TABLE %s ADD COLUMN %s" % (_t, _c))
    except Exception:
        pass
db._COLCACHE.clear()

# Customer-facing "what to expect" — per phase: what WE do, your typical timeframe,
# and a thorough checklist of what YOU (the homeowner) can do at that stage.
PHASE_INFO = [
    {"name": "Approved", "tf": "1–3 days",
     "desc": "We finalize your contract, color & material selections, and paperwork so your project is ready to move.",
     "you": ["Review & sign your Sign-Up Package (button above)",
             "Confirm your shingle/tile/metal color & product choices",
             "Finish any financing paperwork if you're financing"]},
    {"name": "Permitting", "tf": "1–4 weeks",
     "desc": "We prepare and submit your building permit — plus HOA approval if your community requires it — then wait for the city to issue it.",
     "you": ["Sign the HOA application if you're in an HOA community",
             "Upload any HOA or insurance documents we request",
             "Otherwise sit tight — issue time is up to the city/HOA"]},
    {"name": "Scheduling", "tf": "3–7 days",
     "desc": "Your permit is in hand. We order your materials, schedule the crew, and lock in your install date with you.",
     "you": ["Confirm the install date we propose",
             "Plan to move vehicles out of the driveway that morning",
             "Arrange to keep kids & pets indoors during the work"]},
    {"name": "Installation", "tf": "1–3 days",
     "desc": "Tear-off, dry-in, and your new roof goes on. Most homes are finished in 1–2 days.",
     "you": ["Move vehicles away from the house & driveway",
             "Take down fragile wall hangings — tear-off causes vibration",
             "Keep pets indoors; expect noise starting early in the day",
             "Stay clear of the work area for your safety"]},
    {"name": "Final Inspection", "tf": "1–2 weeks",
     "desc": "The city inspects the finished roof and we close out your permit. We coordinate everything with the inspector.",
     "you": ["Nothing required — we schedule the inspection for you",
             "Just make sure the inspector can access your property if asked"]},
    {"name": "Complete", "tf": "—",
     "desc": "Final walkthrough, warranty registration, and you're all set. Thank you for trusting us with your home!",
     "you": ["Walk the property with us & confirm the magnet nail-sweep",
             "Make your final payment",
             "Keep your warranty — we register it with the manufacturer for you"]},
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
CUSTOMER_PHASES = [p["name"] for p in PHASE_INFO]

# Homeowner-facing "value" checklist — the SeaBreeze team playbook translated into
# what the CUSTOMER gets, grouped by the production phase it's completed in. Items
# auto-check as the project advances, so the homeowner sees the value rack up.
# phase: -1 = pre-sale (always done by the time they're in the portal), 0..5 = phases.
VALUE_STEPS = [
    (-1, "Responded fast and got you on the schedule"),
    (-1, "Measured your roof precisely with aerial RoofGraf technology"),
    (-1, "Built your detailed, itemized estimate for the right system"),
    (-1, "Walked you through your options and answered every question"),
    (0, "Collected your signed agreement and sent you copies of all paperwork"),
    (0, "Confirmed your color, material & product selections"),
    (0, "Opened your project file and assigned your crew"),
    (1, "Prepared your building permit packet (NOC, PCN, legal description)"),
    (1, "Attached engineered wind-load & product-approval docs (PE-sealed where required)"),
    (1, "Submitted your permit and tracked it through the city"),
    (1, "Handled HOA / architectural approval — colors, samples & follow-ups (if applicable)"),
    (2, "Took off your exact materials from the roof measurements"),
    (2, "Ordered your premium materials and confirmed delivery"),
    (2, "Verified your colors match your approved selections"),
    (2, "Locked in your install date with you"),
    (3, "Staged materials, permit box & yard sign before the crew arrived"),
    (3, "Tore off the old roof and inspected the deck"),
    (3, "Installed your new roof system to Florida Building Code"),
    (3, "Cleaned up daily and swept for nails"),
    (4, "Completed our punch-list walkthrough"),
    (4, "Passed your final city inspection"),
    (5, "Delivered your warranty documents"),
    (5, "Made sure you're 100% happy and asked how we did"),
]
# Roof "Design Studio" — curated color palettes + engagement options per system, so a
# lead can mock up their roof (system + color + choices) and request samples online.
ROOF_COLORS = {
    "shingle": [
        {"name": "Charcoal", "hex": "#36393d"}, {"name": "Weathered Wood", "hex": "#6c5b46"},
        {"name": "Pewter Gray", "hex": "#7e828a"}, {"name": "Barkwood", "hex": "#5b4a3a"},
        {"name": "Hickory", "hex": "#8a6e4b"}, {"name": "Slate", "hex": "#495663"},
        {"name": "Shakewood", "hex": "#9c7d57"}, {"name": "Hunter Green", "hex": "#2e4031"},
        {"name": "Driftwood", "hex": "#8b8175"}, {"name": "Patriot Red", "hex": "#6e2e2b"},
    ],
    "tile": [
        {"name": "Terracotta", "hex": "#b14b2c"}, {"name": "Sandcastle", "hex": "#c8a979"},
        {"name": "Charcoal Blend", "hex": "#41434a"}, {"name": "Espresso", "hex": "#4b3a2f"},
        {"name": "Slate Blend", "hex": "#5a6470"}, {"name": "Sierra Madre", "hex": "#8a5a3c"},
        {"name": "Capistrano", "hex": "#a9603c"}, {"name": "Sahara", "hex": "#c79a5e"},
    ],
    "metal": [
        {"name": "Galvalume", "hex": "#b8bcc0"}, {"name": "Charcoal", "hex": "#3a3d42"},
        {"name": "Slate Gray", "hex": "#5d666f"}, {"name": "Forest Green", "hex": "#2c4733"},
        {"name": "Regal Blue", "hex": "#28465f"}, {"name": "Copper Penny", "hex": "#a9622f"},
        {"name": "Bone White", "hex": "#ece7da"}, {"name": "Matte Black", "hex": "#232427"},
        {"name": "Burgundy", "hex": "#5e2730"},
    ],
    "flat": [
        {"name": "Energy White", "hex": "#f0f1ee"}, {"name": "Light Gray", "hex": "#c9cdcf"},
        {"name": "Tan", "hex": "#c9b79a"},
    ],
}
ROOF_OPTIONS = {
    "common": [
        {"name": "Seamless gutters & downspouts", "ic": "🌧️"},
        {"name": "Hurricane-rated skylights", "ic": "☀️"},
        {"name": "Ridge vent / attic ventilation upgrade", "ic": "🌬️"},
        {"name": "Premium peel-&-stick underlayment", "ic": "🛡️"},
        {"name": "Extended workmanship warranty", "ic": "📜"},
    ],
    "tile": [{"name": "Premium / Designer tile profile", "ic": "✨"},
             {"name": "Color-coat (slurry) finish", "ic": "🎨"},
             {"name": "Copper valley & drip (coastal)", "ic": "🟫"}],
    "shingle": [{"name": "Designer / architectural upgrade", "ic": "✨"}],
    "metal": [{"name": "Standing-seam clip & coating upgrade", "ic": "✨"}],
}

# Referral GAME — personal link, send tracking, level badges + a reward ladder.
for _rt in ("jobs", "leads"):
    for _rc in ("referral_code TEXT", "referral_clicks INTEGER DEFAULT 0",
                "referral_shares INTEGER DEFAULT 0", "referral_signed INTEGER DEFAULT 0",
                "referral_msg TEXT"):
        try:
            db.execute("ALTER TABLE %s ADD COLUMN %s" % (_rt, _rc))
        except Exception:
            pass
db._COLCACHE.clear()

REFERRAL_TIERS = [   # real rewards, unlocked by SIGNED referrals
    {"n": 1, "reward": "$50 Visa gift card", "ic": "💳"},
    {"n": 2, "reward": "$150 + entry in our annual giveaway", "ic": "🎟️"},
    {"n": 3, "reward": "$300 + customer-appreciation party invite", "ic": "🎉"},
    {"n": 5, "reward": "$500 + a year of free gutter cleaning", "ic": "🏆"},
    {"n": 10, "reward": "Roof Royalty — grand prize + free maintenance", "ic": "👑"},
]
SHARE_LEVELS = [     # fun badge levels, by number of times they SEND their link
    {"n": 0, "name": "Newcomer", "ic": "🌱"}, {"n": 1, "name": "Spreader", "ic": "📣"},
    {"n": 3, "name": "Connector", "ic": "🔥"}, {"n": 5, "name": "Influencer", "ic": "⭐"},
    {"n": 10, "name": "Referral Champion", "ic": "👑"},
]


def _client_first(name):
    nm = re.sub(r"^\s*[A-Za-z]?-?\d{3,}\s*[:\-]\s*", "", (name or ""))
    nm = re.sub(r"\s*\([^)]*\)", "", nm)
    return (re.sub(r"\s+L\s*$", "", nm).strip(" -·,").split(" ") or ["there"])[0] or "there"


def ensure_referral_code(kind, rec):
    code = rec.get("referral_code")
    if not code:
        code = re.sub(r"[^a-zA-Z0-9]", "", secrets.token_urlsafe(6))[:7] or secrets.token_hex(3)
        db.update(kind + "s", rec["id"], referral_code=code)
    return code


def _share_level(shares):
    lvl, nxt = SHARE_LEVELS[0], None
    for l in SHARE_LEVELS:
        if shares >= l["n"]:
            lvl = l
        elif nxt is None:
            nxt = l
    return lvl, nxt


def referral_ctx(kind, rec):
    """Everything the portal referral game needs for a job/lead."""
    code = ensure_referral_code(kind, rec)
    shares = int(rec.get("referral_shares") or 0)
    signed = int(rec.get("referral_signed") or 0)
    lvl, nxt = _share_level(shares)
    return {
        "code": code, "link": url_for("portal.referral_land", code=code, _external=True),
        "shares": shares, "clicks": int(rec.get("referral_clicks") or 0), "signed": signed,
        "level": lvl, "next_level": nxt,
        "tiers": [dict(t, unlocked=signed >= t["n"]) for t in REFERRAL_TIERS],
        "next_tier": next((t for t in REFERRAL_TIERS if signed < t["n"]), None),
        "msg": rec.get("referral_msg") or "", "first": _client_first(rec.get("name")),
    }


# Roof Education game content + HOA seminar request.
ROOF_EDU = [
    {"key": "shingle", "name": "Asphalt Shingle", "ic": "🏠", "life": "15–30 yrs", "cost": "$", "hex": "#6c5b46",
     "blurb": "America's most popular roof — affordable, tons of colors, quick to install.",
     "pros": ["Lowest upfront cost", "Huge color range", "Fast install & repair"],
     "cons": ["Shorter lifespan", "Less wind-rated than tile or metal"]},
    {"key": "tile", "name": "Concrete / Clay Tile", "ic": "🧱", "life": "50+ yrs", "cost": "$$$", "hex": "#b14b2c",
     "blurb": "The classic Florida & HOA look — beautiful, durable, fire & wind resistant.",
     "pros": ["50+ year lifespan", "Hurricane & fire resistant", "Timeless curb appeal", "HOA-favored"],
     "cons": ["Higher upfront cost", "Heavy — needs proper structure"]},
    {"key": "metal", "name": "Standing-Seam Metal", "ic": "⬜", "life": "40–70 yrs", "cost": "$$$", "hex": "#5d666f",
     "blurb": "Modern, energy-efficient, hurricane-built — reflects heat & sheds water fast.",
     "pros": ["40–70 year lifespan", "Energy efficient (cooler home)", "Hurricane-rated", "Low maintenance"],
     "cons": ["Higher upfront cost", "Fewer crews install it well"]},
    {"key": "flat", "name": "Flat / Low-Slope (TPO)", "ic": "🟦", "life": "20–30 yrs", "cost": "$$", "hex": "#aeb4b8",
     "blurb": "For lanais, additions & low-slope sections — a seamless waterproof membrane.",
     "pros": ["Built for low-slope areas", "Reflective / energy saving", "Seamless & watertight"],
     "cons": ["Not for steep roofs", "Needs periodic inspection"]},
]
GLOSSARY = [
    {"t": "Underlayment", "d": "The waterproof layer under your tiles/shingles — your roof's real raincoat."},
    {"t": "Ridge", "d": "The peak where two roof slopes meet at the very top."},
    {"t": "Valley", "d": "The V-shaped channel where two slopes meet — where water runs off."},
    {"t": "Eave", "d": "The lower roof edge that overhangs the wall (where gutters mount)."},
    {"t": "Drip edge", "d": "Metal edging that guides water into the gutter, away from the fascia."},
    {"t": "Flashing", "d": "Metal that seals joints (chimneys, walls, valleys) against leaks."},
    {"t": "Fascia", "d": "The board along the roof edge that gutters attach to."},
    {"t": "Decking", "d": "The wood base your roof is built on — re-nailed to code on a re-roof."},
]
ROOF_QUIZ = [
    {"q": "Which roof typically lasts longest in Florida?", "a": ["Asphalt shingle", "Concrete/clay tile", "3-tab shingle"],
     "c": 1, "why": "Concrete & clay tile commonly last 50+ years and shrug off wind and sun."},
    {"q": "What does underlayment do?", "a": ["Adds color", "Waterproofs under the tile", "Holds the gutters"],
     "c": 1, "why": "Underlayment is the waterproof barrier — your roof's real defense against leaks."},
    {"q": "Which system is most energy-efficient?", "a": ["Standing-seam metal", "Dark 3-tab shingle", "None"],
     "c": 0, "why": "Metal reflects heat, keeping your attic and home cooler."},
    {"q": "Why re-nail the decking on a re-roof?", "a": ["For looks", "To meet FL code & wind uplift", "It's optional"],
     "c": 1, "why": "Re-nailing to current code dramatically improves hurricane wind-uplift resistance."},
    {"q": "Where does a valley send water?", "a": ["Up the roof", "Off the roof where slopes meet", "Into the attic"],
     "c": 1, "why": "Valleys are the V-channels that carry water off where two slopes meet."},
]

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


def ensure_lead_token(lead_id):
    """Return the lead's portal token, generating + saving one if needed."""
    l = db.get("leads", lead_id)
    if not l:
        return None
    tok = l.get("portal_token")
    if not tok:
        tok = secrets.token_urlsafe(12)
        db.update("leads", lead_id, portal_token=tok)
    return tok


def _lead_by_token(token):
    if not token:
        return None
    rows = db.all_rows("leads", "portal_token=?", (token,))
    return rows[0] if rows else None


def lead_portal_link(lead_id):
    """Shareable homeowner-portal URL for a lead (pre-job welcome view)."""
    tok = ensure_lead_token(lead_id)
    return url_for("portal.home", token=tok, _external=True) if tok else ""


def _phase_index(stage):
    return _STAGE_TO_PHASE.get(stage, 0)


# Celebratory per-phase message for the homeowner milestone email.
_PHASE_NOTIFY = {
    "Approved": "You're officially approved — your project is a GO! 🎉",
    "Permitting": "We've submitted your building permit. We're on it with the city. 📝",
    "Scheduling": "Your permit is in hand — we're locking in your install date! 📅",
    "Installation": "It's happening — our crew is building your new roof! Watch it on your Roof Cam. 🔨",
    "Final Inspection": "Your roof passed install — final inspection is up next. 🔎",
    "Complete": "All done — your new roof is complete! Enjoy it. 🏡",
}


def on_phase_advance(job_id, old_phase, new_phase, uid=None):
    """Record a portal update for each phase newly reached (drives the portal feed +
    confetti) and, when the homeowner-notify kill-switch is ON, email the customer.
    Idempotent per (job, phase). Safe to call on every stage change."""
    if new_phase is None or old_phase is None or new_phase <= old_phase:
        return
    job = db.get("jobs", job_id)
    if not job:
        return
    for ph in range(old_phase + 1, new_phase + 1):
        title = CUSTOMER_PHASES[ph] if 0 <= ph < len(CUSTOMER_PHASES) else "Project update"
        if db.all_rows("portal_updates", "job_id=? AND phase=?", (job_id, ph)):
            continue  # dedupe — already recorded this milestone
        db.insert("portal_updates", {"job_id": job_id, "phase": ph, "title": title,
                                     "created": db.now(), "seen": 0})
    # Email the homeowner (gated by the kill-switch + an email on file + a sender).
    comp = db.get_company()
    email = (job.get("email") or "").strip()
    if str(comp.get("portal_notify") or "0") == "1" and email and uid:
        try:
            from modules import gmail
            pname = CUSTOMER_PHASES[new_phase] if 0 <= new_phase < len(CUSTOMER_PHASES) else "your project"
            msg = _PHASE_NOTIFY.get(pname, "Your roof project just hit a new milestone.")
            body = ("%s\n\nSee your live progress and everything we've checked off for you:\n%s\n\n— %s"
                    % (msg, portal_link(job_id), comp.get("name") or "Your roofing team"))
            gmail.send_message(uid, email, "🎉 Roof update — %s" % pname, body)
            db.add_activity("job", job_id, "automation",
                            "Homeowner emailed milestone update: %s" % pname)
        except Exception:
            pass


def _decorate(j):
    payments = db.load_json(j.get("payments"), {})
    j["_payments"] = payments
    j["_paid_pct"] = theme.paid_pct(payments)
    j["_value"] = theme.est_num(j.get("contract_value"))
    j["_balance"] = j["_value"] * (1 - j["_paid_pct"])
    j["_phase"] = _phase_index(j.get("stage"))
    j["_stage_name"] = constants.job_stage(j.get("stage")).get("name", "")
    # Clean customer display name: job names are stored as
    # "R-26061: Belinda Souza (PBC) (S17) (Danny)" — strip the job-number prefix
    # and the trailing (AHJ)(code)(rep) tags so the portal greets the real person.
    nm = j.get("name") or ""
    nm = re.sub(r"^\s*[A-Za-z]?-?\d{3,}\s*[:\-]\s*", "", nm)
    nm = re.sub(r"\s*\([^)]*\)", "", nm).strip(" -·,")
    j["_client"] = nm
    j["_first"] = nm.split(" ")[0] if nm else ""
    return j


@bp.route("/<token>")
def home(token):
    j = _job_by_token(token)
    if not j:
        # Pre-job: a lead's lightweight welcome portal.
        l = _lead_by_token(token)
        if l:
            nm = re.sub(r"^\s*[A-Za-z]?-?\d{3,}\s*[:\-]\s*", "", (l.get("name") or ""))
            nm = re.sub(r"\s*\([^)]*\)", "", nm)
            nm = re.sub(r"\s+L\s*$", "", nm).strip(" -·,")
            rep = next((u for u in db.all_rows("users") if u.get("name") == l.get("rep")), None)
            return render_template("lead_portal.html", l=l, client=nm or "there",
                                   rep=rep, company=db.get_company(), token=token)
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
    payments = db.all_rows("payments", "job_id=?", (j["id"],), "id DESC")
    # When real billing was synced from AccuLynx (invoices/payments exist), drive the
    # Balance Due + Paid% from the ACTUAL numbers instead of the generic draw schedule.
    paid_real = sum(theme.est_num(p.get("amount")) for p in payments)
    inv_total = sum(theme.est_num(i.get("amount")) for i in invoices)
    j["_has_billing"] = bool(invoices or payments)
    if j["_has_billing"]:
        base = j["_value"] or inv_total
        if paid_real or invoices:
            j["_balance"] = max(0.0, base - paid_real) if base else j["_balance"]
            j["_paid_pct"] = (paid_real / base) if base else j["_paid_pct"]
    j["_paid_real"] = paid_real
    activity = [a for a in db.entity_activity("job", j["id"])
                if a.get("kind") in ("stage", "note", "automation")][:10]
    rep = next((u for u in db.all_rows("users") if u.get("name") == j.get("rep")), None)
    company = db.get_company()
    # Build the what's-next checklist: each phase with done/current flag, timeframe,
    # and the homeowner's own to-do items for that stage.
    checklist = []
    for i, p in enumerate(PHASE_INFO):
        checklist.append({"name": p["name"], "desc": p["desc"], "timeframe": p["tf"],
                          "you": p.get("you", []),
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
    from modules import signups
    signup_packet = signups.open_packet_for_job(j["id"])
    # Homeowner value checklist — done if its phase is already behind us, in-progress at
    # the current phase, upcoming if ahead. Shows the customer the work they're paying for.
    cur_phase = j.get("_phase", 0)
    value_steps = [{"text": t, "phase": ph,
                    "done": ph < cur_phase, "current": ph == cur_phase}
                   for ph, t in VALUE_STEPS]
    value_done = sum(1 for v in value_steps if v["done"])
    # Milestone update feed + confetti: newest first; if any are unseen, celebrate the
    # latest, then mark them all seen so the party only fires once per new milestone.
    updates = db.all_rows("portal_updates", "job_id=?", (j["id"],), "id DESC")
    unseen = [u for u in updates if not u.get("seen")]
    celebrate = unseen[0]["title"] if unseen else ""
    for u in unseen:
        db.update("portal_updates", u["id"], seen=1)
    return render_template("portal_dashboard.html", j=j, token=token,
                           referral=referral_ctx("job", j),
                           value_steps=value_steps, value_done=value_done,
                           value_total=len(value_steps),
                           updates=updates, celebrate=celebrate,
                           phases=CUSTOMER_PHASES, estimates=estimates, photos=photos,
                           documents=documents, docs_to_sign=docs_to_sign, invoices=invoices,
                           payments=payments,
                           activity=activity, pay_url=j.get("pay_url"), signup_packet=signup_packet,
                           rep=rep, draws=constants.DRAW_SCHEDULE,
                           checklist=checklist, contract=contract,
                           tutorials=_tutorials(company), product_docs=product_docs, sysk=sysk,
                           photo_app_url=company.get("photo_app_url"))


def _record_by_any_token(token):
    """Resolve a portal token to (kind, record) for a job or a lead."""
    j = _job_by_token(token)
    if j:
        return ("job", j)
    l = _lead_by_token(token)
    if l:
        return ("lead", l)
    return (None, None)


@bp.route("/<token>/design")
def design(token):
    """Roof Design Studio — the homeowner mocks up their roof: system + color + options,
    with a live recoloring preview, then requests samples. Works for a lead or a job."""
    kind, rec = _record_by_any_token(token)
    if not rec:
        abort(404)
    from modules import ahj as ahj_mod
    sysk = (rec.get("system") or ahj_mod.work_type_to_system(rec.get("work_type", "")) or "shingle").lower()
    if sysk not in ROOF_COLORS:
        sysk = "shingle"
    return render_template("design_studio.html", token=token, rec=rec, kind=kind,
                           company=db.get_company(), colors=ROOF_COLORS, options=ROOF_OPTIONS,
                           start_system=sysk)


@bp.route("/<token>/design/request", methods=["POST"])
def design_request(token):
    """Log the homeowner's roof selections + (optional) sample request to the record so
    the rep sees it, and confirm back to the customer."""
    kind, rec = _record_by_any_token(token)
    if not rec:
        abort(404)
    system = (request.form.get("system") or "").strip()[:40]
    color = (request.form.get("color") or "").strip()[:60]
    opts = (request.form.get("options") or "").strip()[:300]
    wants = request.form.get("samples")
    parts = ["System: %s" % system if system else "", "Color: %s" % color if color else "",
             "Options: %s" % opts if opts else ""]
    summary = " · ".join(p for p in parts if p)
    note = "🎨 Roof design selections from the portal — %s%s" % (
        summary or "(started a design)", "  ·  ✉️ SAMPLES REQUESTED" if wants else "")
    db.add_activity(kind, rec["id"], "note", note)
    # Stash the latest selection on the record so the rep / presentation can reuse it.
    try:
        db._ensure_column("leads" if kind == "lead" else "jobs", "design_selection", "TEXT")
        db.update("leads" if kind == "lead" else "jobs", rec["id"],
                  design_selection=db.dump_json({"system": system, "color": color,
                                                 "options": opts, "samples": bool(wants)}))
    except Exception:
        pass
    flash("Your selections are saved!%s Your project contact will follow up." %
          (" We'll bring your samples." if wants else ""), "ok")
    return redirect(url_for("portal.design", token=token))


@bp.route("/r/<code>")
def referral_land(code):
    """Public landing for a customer's personal referral link. Counts the click and
    shows a branded 'your neighbor referred you' page with a quote CTA."""
    rec = kind = None
    for k in ("jobs", "leads"):
        rows = db.all_rows(k, "referral_code=?", (code,))
        if rows:
            rec, kind = rows[0], k[:-1]
            break
    if not rec:
        abort(404)
    db.update(kind + "s", rec["id"], referral_clicks=int(rec.get("referral_clicks") or 0) + 1)
    return render_template("referral_landing.html", company=db.get_company(),
                           referrer=_client_first(rec.get("name")))


@bp.route("/<token>/refer/share", methods=["POST"])
def refer_share(token):
    """Count a 'send' of the referral link (the game metric) and return the new level."""
    kind, rec = _record_by_any_token(token)
    if not rec:
        return jsonify({"ok": False}), 404
    n = int(rec.get("referral_shares") or 0) + 1
    db.update(kind + "s", rec["id"], referral_shares=n)
    lvl, nxt = _share_level(n)
    return jsonify({"ok": True, "shares": n, "level": lvl["name"], "icon": lvl["ic"],
                    "leveledUp": bool(nxt is None or False) or (lvl["n"] == n),
                    "next": (nxt["name"] if nxt else None), "nextAt": (nxt["n"] if nxt else None)})


@bp.route("/<token>/refer/msg", methods=["POST"])
def refer_msg(token):
    """Save the homeowner's customized referral message."""
    kind, rec = _record_by_any_token(token)
    if not rec:
        return jsonify({"ok": False}), 404
    db.update(kind + "s", rec["id"], referral_msg=(request.form.get("msg") or "")[:400])
    return jsonify({"ok": True})


@bp.route("/<token>/learn")
def learn(token):
    """Interactive roof-education game — system explainers, a quiz (Roof IQ), and a
    glossary. Keeps homeowners engaged and educated from the lead stage on."""
    kind, rec = _record_by_any_token(token)
    if not rec:
        abort(404)
    return render_template("learn.html", token=token, company=db.get_company(),
                           systems=ROOF_EDU, glossary=GLOSSARY, quiz=ROOF_QUIZ)


@bp.route("/<token>/seminar", methods=["GET", "POST"])
def seminar(token):
    """HOA / community lunch-and-learn request: a resident or board member organizes a
    Q&A seminar (we bring food + knowledge + manufacturer reps). Logs to the record."""
    kind, rec = _record_by_any_token(token)
    if not rec:
        abort(404)
    if request.method == "POST":
        f = request.form
        systems = ", ".join(f.getlist("systems")) or "—"
        fields = [
            ("Organizer", f.get("organizer")), ("Role", f.get("role")),
            ("Community / HOA", f.get("community")), ("Est. attendees", f.get("attendees")),
            ("Preferred date(s)", f.get("dates")), ("Systems of interest", systems),
            ("Required manufacturer", f.get("manufacturer")),
            ("Required color/profile", f.get("color")),
            ("Topics / questions", f.get("topics")),
        ]
        body = "  ·  ".join("%s: %s" % (k, v) for k, v in fields if (v or "").strip())
        db.add_activity(kind, rec["id"], "note",
                        "🍽️ HOA Q&A SEMINAR REQUEST (lunch-&-learn) — %s%s" % (
                            body, "  ·  ⭐ wants manufacturer reps present" if f.get("manufacturer") else ""))
        try:
            db._ensure_column("leads" if kind == "lead" else "jobs", "seminar_request", "TEXT")
            db.update("leads" if kind == "lead" else "jobs", rec["id"],
                      seminar_request=db.dump_json({k: f.get(k) for k in
                                                    ("organizer", "role", "community", "attendees",
                                                     "dates", "manufacturer", "color", "topics")}))
        except Exception:
            pass
        flash("Seminar request sent! We'll reach out to schedule your lunch-and-learn — "
              "food and expert answers on us. 🍽️", "ok")
        return redirect(url_for("portal.seminar", token=token))
    return render_template("seminar.html", token=token, company=db.get_company(), systems=ROOF_EDU)


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
        from modules import notifications
        notifications.notify(j["id"], "upload", "Homeowner uploaded a document (%s): %s" % (cat, f.filename))
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
        from modules import notifications
        notifications.notify(j["id"], "photo", "Homeowner uploaded a photo: %s" % f.filename)
        db.update("jobs", j["id"], next_follow=db.today())
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
        from modules import notifications
        notifications.notify(j["id"], "request" if kind == "request" else "message",
                             "%s: %s" % (label, text[:140]))
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
    from modules import notifications
    notifications.notify(j["id"], "approve", "%s approved & signed proposal %s" % (name, e.get("number", "")))
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
    from modules import notifications
    notifications.notify(j["id"], "sign", "%s e-signed: %s" % (name, d.get("original_name", "a document")))
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
