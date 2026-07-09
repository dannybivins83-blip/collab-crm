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
for _c in ("portal_token TEXT", "portal_token_expires TEXT"):
    try:
        db.execute("ALTER TABLE jobs ADD COLUMN %s" % _c)
    except Exception:
        pass
# Leads get their own portal token + an "invited" flag (dedupe the auto-invite email).
for _lc in ("portal_token TEXT", "portal_invited TEXT", "portal_token_expires TEXT"):
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
# Two-way homeowner <-> company conversation thread (one row per message).
# direction: 'in' = from the homeowner, 'out' = a rep's reply. read_at marks when
# the *other* side has seen it (rep-read for 'in', homeowner-read for 'out').
# department mirrors the parent job/lead for multi-tenant isolation.
try:
    db.execute("""CREATE TABLE IF NOT EXISTS portal_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER, lead_id INTEGER,
        direction TEXT, body TEXT, author TEXT, created TEXT, read_at TEXT,
        department TEXT)""")
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

# ---------------------------------------------------------------------------
# Unified homeowner JOURNEY — one continuous, numbered, accountable roadmap that
# spans prospect -> approved job. Both the lead portal and the job dashboard render
# the SAME roadmap (templates/_journey.html); status is computed per record so the
# experience flows identically as a homeowner moves from lead to job. Steps the
# homeowner must DO are highlighted; overdue ones drive gated reminder drafts.
# ---------------------------------------------------------------------------
def journey_steps(kind, rec, token):
    """Return the 10-step roadmap for `rec` with per-step status.
    status: done | current | upcoming | na.  locked = not yet actionable (prospect)."""
    from modules import signups
    rid = rec["id"]
    is_job = (kind == "job")
    phase = _phase_index(rec.get("stage")) if is_job else -1
    if is_job:
        ests = db.all_rows("estimates", "job_id=?", (rid,))
        signed = any(e.get("status") == "signed" for e in ests)
        unsigned = any(e.get("status") != "signed" for e in ests)
        docs = db.all_rows("documents", "job_id=?", (rid,))
        permit_to_sign = any(d.get("category") == "Permit" and d.get("needs_sign")
                             and not d.get("signed_at") for d in docs)
        has_contract = any(d.get("category") == "Contract" for d in docs)
        has_payment = bool(db.all_rows("payments", "job_id=?", (rid,)))
        packet = signups.open_packet_for_job(rid)
    else:
        signed = unsigned = permit_to_sign = has_contract = has_payment = False
        packet = None
    ahj = rec.get("ahj")
    home = url_for("portal.home", token=token)

    def U(ep, **kw):
        try:
            return url_for(ep, token=token, **kw)
        except Exception:
            return home

    out = []

    def add(title, blurb, icon, status, cta_label, cta_url, locked=False):
        out.append({"title": title, "blurb": blurb, "icon": icon, "status": status,
                    "cta_label": cta_label, "cta_url": cta_url, "locked": locked})

    # 1 Design (available to everyone, prospect or job)
    add("Design your roof", "Pick your color, system & options — and request free samples.",
        "palette", "done" if (is_job or rec.get("design_photo")) else "current",
        "Open Design Studio", U("portal.design"))
    # 2 Roof School
    add("Learn about your roof", "Take the 2-minute Roof School tour and test your Roof IQ.",
        "cap", "done" if is_job else "current", "Open Roof School", U("portal.learn"))
    # 3 Inspection
    add("Confirm your inspection", "We measure your roof and finalize your estimate for you.",
        "calendar", "done" if is_job else "current", "Message us", home + "#message")
    # 4 Approve proposal
    s = "done" if signed else ("current" if is_job else "upcoming")
    add("Review & approve your proposal", "Look over your estimate and e-sign to get started.",
        "clipboard", s, "Review & sign", home + "#estimate", locked=not is_job)
    # 5 Sign-up package
    if not is_job:
        s = "upcoming"
    elif phase >= 1 or has_contract:
        s = "done"
    elif packet:
        s = "current"
    else:
        s = "upcoming"
    add("Complete your sign-up package", "Sign your welcome packet so we can open your project file.",
        "pencil", s, "Open sign-up package",
        U("signups.portal_view", packet_id=packet["id"]) if packet else home + "#estimate",
        locked=not is_job)
    # 6 Permit documents
    if not is_job:
        s = "upcoming"
    elif phase >= 2:
        s = "done"
    elif phase >= 1 or permit_to_sign:
        s = "current"
    else:
        s = "upcoming"
    add("Sign your permit documents", "Sign the permit forms so we can submit to the city.",
        "doc", s, "Review permit docs", home + "#estimate", locked=not is_job)
    # 7 HOA approval — only when there's an AHJ/HOA on file
    if ahj:
        if not is_job:
            s = "upcoming"
        elif phase >= 2:
            s = "done"
        elif phase >= 1:
            s = "current"
        else:
            s = "upcoming"
        add("HOA approval", "If your community requires it, sign the HOA application & upload approval.",
            "building", s, "Upload HOA docs", home + "#documents", locked=not is_job)
    # 8 Deposit
    if not is_job:
        s = "upcoming"
    elif has_payment:
        s = "done"
    elif phase >= 0:
        s = "current"
    else:
        s = "upcoming"
    add("Make your deposit", "Submit your deposit to lock in materials & your install date.",
        "card", s, "Pay online", U("portal.pay"), locked=not is_job)
    # 9 Watch install
    if is_job and phase > 3:
        s = "done"
    elif is_job and phase == 3:
        s = "current"
    else:
        s = "upcoming"
    add("Watch your install", "Track your new roof going on — live job-site photos.",
        "hammer", s, "View job photos", home + "#photos", locked=not is_job)
    # 10 Final walkthrough + review & refer
    s = "current" if (is_job and phase >= 5) else "upcoming"
    add("Final walkthrough, review & refer", "Confirm the nail sweep, leave a review, and share to earn rewards.",
        "trophy", s, "Share & refer", home + "#refGame", locked=not is_job)

    for i, st in enumerate(out, 1):
        st["n"] = i
    return out


def journey_progress(steps):
    done = sum(1 for s in steps if s["status"] == "done")
    return {"done": done, "total": len(steps),
            "pct": int(round(done / len(steps) * 100)) if steps else 0}


def current_step(steps):
    return next((s for s in steps if s["status"] == "current"), None)
# Roof "Design Studio" — curated color palettes + engagement options per system, so a
# lead can mock up their roof (system + color + choices) and request samples online.
# Each color entry: {name, hex, mfr}  — mfr groups the picker by manufacturer.
ROOF_COLORS = {
    "shingle": [
        {"name": "Charcoal",        "hex": "#36393d", "mfr": "GAF Timberline HDZ"},
        {"name": "Weathered Wood",  "hex": "#6c5b46", "mfr": "GAF Timberline HDZ"},
        {"name": "Pewter Gray",     "hex": "#7e828a", "mfr": "GAF Timberline HDZ"},
        {"name": "Barkwood",        "hex": "#5b4a3a", "mfr": "GAF Timberline HDZ"},
        {"name": "Hickory",         "hex": "#8a6e4b", "mfr": "GAF Timberline HDZ"},
        {"name": "Hunter Green",    "hex": "#2e4031", "mfr": "GAF Timberline HDZ"},
        {"name": "Driftwood",       "hex": "#8b8175", "mfr": "Owens Corning Duration"},
        {"name": "Patriot Red",     "hex": "#6e2e2b", "mfr": "Owens Corning Duration"},
        {"name": "Shakewood",       "hex": "#9c7d57", "mfr": "Owens Corning Duration"},
        {"name": "Slate",           "hex": "#495663", "mfr": "Owens Corning Duration"},
    ],
    "tile": [
        {"name": "Terracotta",      "hex": "#b14b2c", "mfr": "Westlake Royal"},
        {"name": "Sandcastle",      "hex": "#c8a979", "mfr": "Westlake Royal"},
        {"name": "Sierra Madre",    "hex": "#8a5a3c", "mfr": "Westlake Royal"},
        {"name": "Capistrano",      "hex": "#a9603c", "mfr": "Westlake Royal"},
        {"name": "Charcoal Blend",  "hex": "#41434a", "mfr": "Boral"},
        {"name": "Espresso",        "hex": "#4b3a2f", "mfr": "Boral"},
        {"name": "Slate Blend",     "hex": "#5a6470", "mfr": "Boral"},
        {"name": "Sahara",          "hex": "#c79a5e", "mfr": "Boral"},
    ],
    "metal": [
        {"name": "Galvalume",       "hex": "#b8bcc0", "mfr": "ABC Supply / McElroy"},
        {"name": "Charcoal",        "hex": "#3a3d42", "mfr": "ABC Supply / McElroy"},
        {"name": "Slate Gray",      "hex": "#5d666f", "mfr": "ABC Supply / McElroy"},
        {"name": "Matte Black",     "hex": "#232427", "mfr": "ABC Supply / McElroy"},
        {"name": "Forest Green",    "hex": "#2c4733", "mfr": "Fabral"},
        {"name": "Regal Blue",      "hex": "#28465f", "mfr": "Fabral"},
        {"name": "Copper Penny",    "hex": "#a9622f", "mfr": "Fabral"},
        {"name": "Bone White",      "hex": "#ece7da", "mfr": "Fabral"},
        {"name": "Burgundy",        "hex": "#5e2730", "mfr": "Fabral"},
    ],
    "flat": [
        {"name": "Energy White",    "hex": "#f0f1ee", "mfr": "Firestone"},
        {"name": "Light Gray",      "hex": "#c9cdcf", "mfr": "Firestone"},
        {"name": "Tan",             "hex": "#c9b79a", "mfr": "GAF EverGuard"},
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
     "img": "https://upload.wikimedia.org/wikipedia/commons/thumb/8/8e/Asphalt_shingles.jpg/640px-Asphalt_shingles.jpg",
     "blurb": "America's most popular roof — affordable, tons of colors, quick to install.",
     "detail": "Architectural (dimensional) shingles are layered fiberglass mats coated in asphalt and mineral granules. Modern Florida shingles like GAF Timberline HDZ carry high wind ratings (130+ mph with proper nailing) and come in dozens of colors. Great value for steep-slope homes.",
     "layers": ["Architectural shingles (the visible roof)", "Secondary Water Barrier — self-adhered underlayment (SWB)", "Re-nailed plywood deck to FL code"],
     "swb": "Your SWB (Secondary Water Barrier) is a self-adhered, peel-&-stick underlayment bonded directly to the deck. It's required by Florida code: even if wind tears shingles off in a storm, the SWB keeps water out of your home. It's the layer that actually waterproofs your roof.",
     "includes": ["6-nail high-wind nailing pattern", "Ridge-vent attic ventilation", "New pipe boots, drip edge & valley metal", "Manufacturer + workmanship warranty"],
     "fl": "Florida code requires the SWB + a re-nailed deck on every re-roof — that's where most of your wind protection comes from, under the shingles.",
     "pros": ["Lowest upfront cost", "Huge color range", "Fast install & repair", "130+ mph rated when nailed right"],
     "cons": ["Shorter lifespan (15–30 yrs)", "Granule loss in harsh sun over time"]},
    {"key": "tile", "name": "Concrete / Clay Tile", "ic": "🧱", "life": "50+ yrs", "cost": "$$$", "hex": "#b14b2c",
     "img": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/2a/Roof_tiles_-_Barcelona.jpg/640px-Roof_tiles_-_Barcelona.jpg",
     "blurb": "The classic Florida & HOA look — beautiful, durable, fire & wind resistant.",
     "detail": "Concrete or clay tiles set in foam adhesive or mortar. The tiles themselves shrug off sun, salt air, and 150+ mph wind for 50+ years — but the real waterproofing is the membrane underneath. The look most Florida HOAs require, and it boosts resale value.",
     "layers": ["Concrete/clay tile (the visible roof)", "Secondary Water Barrier — Polyglass / self-adhered (SWB)", "Battens / foam-set system", "Re-nailed deck to FL code"],
     "swb": "With tile, the SWB matters even more: tile sheds most water, but the self-adhered Secondary Water Barrier beneath is what truly keeps your home dry. We use a premium 2-ply self-adhered system (e.g., Polyglass TU) so your roof is watertight for decades.",
     "includes": ["Tile uplift test & engineering (per code)", "Copper or galvanized valley metal", "Hip & ridge set + pointed/weather-blocked", "50-yr tile + workmanship warranty"],
     "fl": "Tile is the gold standard for FL HOAs and coastal homes — fire-rated, hurricane-rated, and it pairs with the strongest SWB systems we install.",
     "pros": ["50+ year lifespan", "Hurricane & fire resistant", "Timeless curb appeal", "HOA-favored, adds resale value"],
     "cons": ["Higher upfront cost", "Heavy — needs proper structure"]},
    {"key": "metal", "name": "Standing-Seam Metal", "ic": "⬜", "life": "40–70 yrs", "cost": "$$$", "hex": "#5d666f",
     "img": "https://upload.wikimedia.org/wikipedia/commons/thumb/5/52/Standing_seam_metal_roof.jpg/640px-Standing_seam_metal_roof.jpg",
     "blurb": "Modern, energy-efficient, hurricane-built — reflects heat & sheds water fast.",
     "detail": "Interlocking vertical metal panels with hidden fasteners (the 'standing seam'). No exposed screws to leak. Reflective coatings bounce the Florida sun so your attic and AC bills drop. Lasts 40–70 years — often the last roof you'll buy.",
     "layers": ["Standing-seam metal panels (hidden clips)", "Secondary Water Barrier — high-temp self-adhered (SWB)", "Re-nailed deck to FL code"],
     "swb": "Under metal we use a high-temperature Secondary Water Barrier rated for the heat metal builds up. It's the self-adhered waterproof layer on the deck — your backup if any water ever gets past a seam.",
     "includes": ["Concealed-clip standing seam (no exposed screws)", "Reflective 'cool roof' coating", "Custom flashing & trim", "30–50 yr finish + workmanship warranty"],
     "fl": "Metal is a top pick for coastal & energy-conscious FL homes — hurricane-rated, salt-tolerant with the right coating, and the coolest-running roof we offer.",
     "pros": ["40–70 year lifespan", "Energy efficient (cooler home)", "Hurricane-rated, no exposed fasteners", "Very low maintenance"],
     "cons": ["Higher upfront cost", "Fewer crews install it well — workmanship matters"]},
    {"key": "flat", "name": "Flat / Low-Slope (TPO)", "ic": "🟦", "life": "20–30 yrs", "cost": "$$", "hex": "#8a9097",
     "img": "https://upload.wikimedia.org/wikipedia/commons/thumb/3/3d/Flat_roof_with_membrane.jpg/640px-Flat_roof_with_membrane.jpg",
     "blurb": "For lanais, additions & low-slope sections — a seamless waterproof membrane.",
     "detail": "A single-ply TPO (or modified-bitumen) membrane heat-welded into one continuous, seamless waterproof surface. Used on flat or low-slope areas — lanais, additions, porches — where tile/shingle can't shed water. White TPO reflects heat to keep those rooms cooler.",
     "layers": ["Heat-welded TPO membrane (seamless)", "Insulation / cover board", "Self-adhered base (SWB)", "Re-secured deck"],
     "swb": "On a flat roof the membrane IS the waterproofing — but it's installed over a self-adhered base layer (the Secondary Water Barrier), and every seam is heat-welded into one continuous sheet so there are no gaps for water.",
     "includes": ["Heat-welded seams (no glue joints)", "Tapered insulation for drainage", "Reflective white 'cool roof' surface", "Manufacturer + workmanship warranty"],
     "fl": "Essential for FL low-slope sections — a reflective TPO membrane handles ponding rain and afternoon storms that would defeat a sloped system.",
     "pros": ["Built for low-slope areas", "Reflective / energy saving", "Seamless & watertight"],
     "cons": ["Not for steep roofs", "Needs periodic inspection"]},
]
GLOSSARY = [
    {"t": "Secondary Water Barrier (SWB)", "d": "A self-adhered, peel-&-stick membrane bonded to your deck UNDER the tile/shingle/metal. It's the layer that actually waterproofs your home — required by Florida code. Even if the surface is damaged in a storm, the SWB keeps water out."},
    {"t": "Underlayment", "d": "Another name for the waterproof layer under your roofing — on FL re-roofs this is the self-adhered Secondary Water Barrier (SWB)."},
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
    {"q": "What's the FIRST thing we do on install day?", "a": ["Tear off the old roof", "Protect your landscaping & set up", "Paint the fascia"],
     "c": 1, "why": "We protect your pool, plants & AC and stage materials before a single shingle moves."},
    {"q": "What is a 'dry-in'?", "a": ["A dry day to work", "The waterproof underlayment layer + inspection", "The final cleanup"],
     "c": 1, "why": "Dry-in is your waterproof barrier going on — it gets its own city inspection before the roof goes on."},
    {"q": "How do we make sure no nails are left in your yard?", "a": ["We rake", "We run magnets (magnet sweep)", "We don't"],
     "c": 1, "why": "We run powerful magnets over the yard and driveway daily to catch every nail."},
]

# The physical install journey — used to walk the homeowner through "what happens," each
# step illustrated with REAL photos pulled from similar completed jobs (the field photos).
PROCESS_STEPS = [
    {"key": "prep", "bucket": "Tear-off", "name": "Prep & Protection", "ic": "🚧",
     "blurb": "We protect your landscaping, pool & AC, set up the dumpster, and stage materials and the permit box."},
    {"key": "teardown", "bucket": "Tear-off", "name": "Tear-Off", "ic": "🔨",
     "blurb": "The old roof comes off down to the wood deck so we can see — and fix — everything underneath."},
    {"key": "deck", "bucket": "Tear-off", "name": "Deck Inspection & Re-Nail", "ic": "🪵",
     "blurb": "We inspect the decking, replace any rotten wood, and re-nail to current Florida code for wind uplift."},
    {"key": "dryin", "bucket": "Installation", "name": "Dry-In (Waterproofing)", "ic": "🛡️",
     "blurb": "Your self-adhered underlayment goes on — the real waterproof barrier — and passes a dry-in inspection."},
    {"key": "install", "bucket": "Installation", "name": "Roof Install", "ic": "🏠",
     "blurb": "Your new system goes on — set to manufacturer spec and Florida Building Code."},
    {"key": "details", "bucket": "Installation", "name": "Flashing & Details", "ic": "⚙️",
     "blurb": "Valleys, pipe flashings, ridge & hip, drip edge — the details that keep water out for decades."},
    {"key": "cleanup", "bucket": "Finished", "name": "Cleanup & Magnet Sweep", "ic": "🧲",
     "blurb": "We clean daily and run magnets over your yard and driveway to catch every last nail."},
    {"key": "final", "bucket": "Finished", "name": "Final Inspection & Warranty", "ic": "✅",
     "blurb": "The city inspects, we close your permit, and register your manufacturer warranty."},
]


def _photo_bucket(phase):
    p = (phase or "").lower()
    if any(k in p for k in ("before", "tear", "old", "remove", "demo")):
        return "Tear-off"
    if any(k in p for k in ("after", "final", "complete", "done", "finish")):
        return "Finished"
    return "Installation"


# SiteCam is the standalone photo source of truth. The portal pulls real, R2-hosted
# field photos by roof system from SiteCam's public showcase endpoint, so the
# "recent jobs like yours" galleries always show working images — no local file syncing,
# no AccuLynx. City/area only on the SiteCam side (no homeowner names/addresses).
_SC_API = os.environ.get("SITECAM_API_URL", "https://sitecam-api.onrender.com").rstrip("/")
_SC_TENANT = os.environ.get("SITECAM_TENANT", "seabreeze")


def sitecam_showcase_photos(sysk, limit=6, per=8):
    """Flat list of full https R2 photo URLs for a roof system, newest job first.
    Best-effort: returns [] on any error/timeout so the portal page never breaks."""
    if not sysk:
        return []
    import json
    import urllib.parse
    import urllib.request
    try:
        qs = urllib.parse.urlencode(
            {"tenant": _SC_TENANT, "system": sysk, "limit": limit, "photosPerJob": per}
        )
        with urllib.request.urlopen(f"{_SC_API}/api/public/showcase?{qs}", timeout=4) as r:
            jobs = json.loads(r.read().decode())
        return [p["url"] for j in jobs for p in (j.get("photos") or []) if p.get("url")]
    except Exception:
        return []


# TTL caches for photo queries that scan ALL jobs — these are called on every portal
# /learn page load. Cache for 5 min to avoid N+1 on large job tables.
_PHOTO_CACHE_TTL = 300  # seconds
_photo_cache = {}  # key → (expires_at, value)


def _cache_get(key):
    entry = _photo_cache.get(key)
    if entry and time.time() < entry[0]:
        return entry[1]
    return None


def _cache_set(key, value):
    _photo_cache[key] = (time.time() + _PHOTO_CACHE_TTL, value)


def similar_job_photos(system, exclude_id, dept=None, cap=24):
    """Real field photos from OTHER jobs of the same roof system. Prefers SiteCam's
    R2-hosted photos (the standalone source); falls back to locally-synced photos.
    Anonymized — photos only, no names/addresses."""
    cache_key = ("similar", system, exclude_id, dept, cap)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    urls = sitecam_showcase_photos(system, limit=8, per=6)[:cap]
    if urls:
        result = {"Recent installs": urls}
        _cache_set(cache_key, result)
        return result
    from modules import ahj as ahj_mod
    groups = {"Tear-off": [], "Installation": [], "Finished": []}
    target_sys = (system or "").lower()
    # Collect matching job IDs in one pass (no DB calls inside loop).
    # Scope to the record's own department so cross-company tenants stay isolated.
    _j_where = "department=?" if dept else None
    _j_params = (dept,) if dept else ()
    matching_ids = [
        j["id"] for j in db.all_rows("jobs", _j_where, _j_params)
        if j["id"] != exclude_id
        and (j.get("system") or ahj_mod.work_type_to_system(j.get("work_type", "")) or "").lower() == target_sys
    ]
    if matching_ids:
        # Batch-load photos for ALL matching jobs in ONE query (was N+1).
        ph_rows = db.all_rows("photos",
                              "job_id IN (%s)" % ",".join("?" * len(matching_ids)),
                              tuple(matching_ids), "id DESC")
        n = 0
        for ph in ph_rows:
            b = _photo_bucket(ph.get("phase"))
            if len(groups[b]) < 8:
                groups[b].append(ph.get("filename"))
                n += 1
            if n >= cap:
                break
    result = {k: v for k, v in groups.items() if v}
    _cache_set(cache_key, result)
    return result


def one_photo_per_system(dept=None):
    """A single representative REAL field photo per roof system (for the Roof School
    cards). Prefers a SiteCam R2 photo; falls back to a locally-synced filename."""
    cache_key = ("one_photo_per_system", dept)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    from modules import ahj as ahj_mod
    import concurrent.futures
    systems = ("shingle", "tile", "metal", "flat")
    out = {}
    # Fetch all 4 systems in parallel so cold-SiteCam doesn't serialize 4 × 4s timeouts.
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as _pool:
        futures = {_pool.submit(sitecam_showcase_photos, s, 1, 1): s for s in systems}
        for fut, s in futures.items():
            try:
                ph = fut.result(timeout=5)
                if ph:
                    out[s] = ph[0]
            except Exception:
                pass
    needed = set(systems) - set(out.keys())
    if needed:
        # Collect one matching job_id per needed system — scope to dept for multi-tenant safety.
        _j_where = "department=?" if dept else None
        _j_params = (dept,) if dept else ()
        jobs_by_system = {}
        for j in db.all_rows("jobs", _j_where, _j_params, "id ASC"):
            s = (j.get("system") or ahj_mod.work_type_to_system(j.get("work_type", "")) or "").lower()
            if s in needed and s not in jobs_by_system:
                jobs_by_system[s] = j["id"]
            if len(jobs_by_system) == len(needed):
                break
        if jobs_by_system:
            # Batch-load photos for all candidate jobs in ONE query (was N+1).
            cand_ids = tuple(jobs_by_system.values())
            ph_rows = db.all_rows("photos",
                                  "job_id IN (%s)" % ",".join("?" * len(cand_ids)),
                                  cand_ids, "id DESC")
            photo_by_job = {}
            for ph in ph_rows:
                jid = ph.get("job_id")
                if jid not in photo_by_job:
                    photo_by_job[jid] = ph.get("filename")
            for s, jid in jobs_by_system.items():
                if jid in photo_by_job and s not in out:
                    out[s] = photo_by_job[jid]
    _cache_set(cache_key, out)
    return out


# Brand/product keywords used to match a Document-Library data sheet to a roof system.
_SYS_KW = {
    "shingle": ("shingle", "gaf", "timberline", "owens", "trudefinition", "duration", "landmark", "hdz"),
    "tile": ("tile", "westlake", "eagle", "saxony", "barcelona", "villa", "crown", "tu_plus", "tu-plus"),
    "metal": ("metal", "dynamic", "galvalume", "standing", "seam", "englert", "dmc", "dm-", "dm_", "ss_metal"),
    "flat": ("flat", "tpo", "modbit", "mod_bit", "mod-bit", "polyglass_sa", "hot-mop", "hotmop", "bur"),
}


def _doc_systems(name):
    n = (name or "").lower()
    return {s for s, kws in _SYS_KW.items() if any(k in n for k in kws)}


def product_docs_for(sysk):
    """Real company product data sheets / color charts / warranties relevant to a system
    (system-specific first, then generic). Pulled from the Document Library."""
    cats = ("Product & Color Charts", "Warranties")
    # SQL-filter by category first (was a full-table scan); Python handles keyword matching.
    cat_docs = db.all_rows("library_docs",
                           "category IN (%s)" % ",".join("?" * len(cats)),
                           cats)
    docs = [d for d in cat_docs
            if (sysk in _doc_systems(d.get("original_name"))) or not _doc_systems(d.get("original_name"))]
    docs.sort(key=lambda d: 0 if _doc_systems(d.get("original_name")) else 1)
    return docs[:16]


def latest_system_job_photos(sysk, dept=None, cap=18):
    """The newest documented job of this system — returns its photos (newest first) so
    Roof School can show a real, recently-documented job of the homeowner's exact system.
    Prefers SiteCam's R2 photos; falls back to locally-synced job photos."""
    sc = sitecam_showcase_photos(sysk, limit=1, per=cap)
    if sc:
        return {"sitecam_url": None, "photos": [{"filename": u, "caption": ""} for u in sc]}
    from modules import ahj as ahj_mod
    sysk_lower = (sysk or "").lower()
    # SQL ORDER BY id DESC eliminates the Python sort over all jobs; dept filter for multi-tenant.
    _j_where = "department=?" if dept else None
    _j_params = (dept,) if dept else ()
    for j in db.all_rows("jobs", _j_where, _j_params, "id DESC"):
        s = (j.get("system") or ahj_mod.work_type_to_system(j.get("work_type", "")) or "").lower()
        if s != sysk_lower:
            continue
        ph = db.all_rows("photos", "job_id=?", (j["id"],), "id DESC")
        if ph:
            return {"sitecam_url": j.get("sitecam_url"), "photos": ph[:cap]}
    return None


# Deep component education — each roofing feature: what it is, the TYPES, the BRANDS we
# install, and why it matters. doc_kw links it to a real data sheet in the library.
FEATURES = [
    {"key": "swb", "ic": "🛡️", "name": "Underlayment / Secondary Water Barrier (SWB)",
     "what": "The self-adhered, peel-&-stick membrane bonded to your wood deck — the layer that actually keeps water out of your home. Required by Florida code on every re-roof.",
     "types": ["Synthetic felt — basic, mechanically fastened (budget tier)",
               "2-ply self-adhered (peel & stick) — what we install on most homes",
               "Tile-grade high-bond self-adhered — for foam-set tile"],
     "brands": ["Polyglass Polystick TU Plus", "Polyglass Polystick MTS / IR-Xe", "GAF FeltBuster / Deck-Armor"],
     "why": "Even if shingles or tiles blow off in a hurricane, the SWB keeps your home dry. It's the most important waterproofing layer — and the #1 place cheap roofs cut corners.",
     "doc_kw": ["polyglass", "polystick", "underlay", "tu plus", "tu_plus", "felt", "ir-xe", "mts"]},
    {"key": "ridge", "ic": "🌬️", "name": "Ridge Vents & Attic Ventilation",
     "what": "Vents at the peak of your roof that let superheated attic air escape while cooler air is pulled in at the eaves.",
     "types": ["Shingle-over ridge vent — hidden, runs the whole ridge",
               "Off-ridge / box vents", "Tile ridge vents (for tile roofs)", "Solar-powered attic fans"],
     "brands": ["GAF Cobra ridge vent", "O'Hagin (tile vents)", "Lomanco", "Solar attic fans"],
     "why": "Good ventilation drops attic temps 20–40°F — lower AC bills, no trapped moisture or mold, and a roof that lasts years longer. An unvented attic literally cooks your roof from underneath.",
     "doc_kw": ["vent", "cobra", "ridge", "ventilation", "ohagin", "o'hagin", "lomanco"]},
    {"key": "deck", "ic": "🪵", "name": "Decking & Re-Nailing",
     "what": "The plywood base your whole roof is built on. On a re-roof we re-nail the entire deck to current Florida wind code and replace any rotten wood.",
     "types": ["Re-nail existing deck to FBC (8d ring-shank pattern)", "Replace rotten/soft plywood sheets",
               "New 5/8\" CDX where required"],
     "brands": ["8d ring-shank nails (code pattern)", "CDX structural plywood"],
     "why": "This is where most of your wind-uplift strength comes from — before a single shingle or tile goes on. We document the bare deck and the re-nail in SiteCam so you can see it was done right.",
     "doc_kw": ["deck", "nail", "plywood", "cdx"]},
    {"key": "valley", "ic": "💧", "name": "Valley Metal",
     "what": "The metal-lined V-channels where two roof slopes meet — they carry the most water on your whole roof.",
     "types": ["Open W-valley metal", "Closed-cut valley", "Coastal: copper valley"],
     "brands": ["26-ga galvanized valley metal", "16\" galvanized", "Copper (salt-air homes)"],
     "why": "Valleys move more water than anywhere else — done cheap, they're the first place a roof leaks. We use heavy-gauge (or copper near saltwater) and seal them to the SWB.",
     "doc_kw": ["valley", "galvanized", "copper"]},
    {"key": "flashing", "ic": "⚙️", "name": "Flashing & Penetrations",
     "what": "The metal that seals every joint and penetration — pipes, vents, skylights, walls, chimneys — against leaks.",
     "types": ["Lead pipe boots / stacks", "Goosenecks (exhaust)", "Step & counter flashing (walls)", "Skylight flashing kits"],
     "brands": ["Lead stack flashings", "Galvanized step flashing", "Manufacturer skylight kits"],
     "why": "Most roof leaks start at a penetration, not the field. We replace every boot and flashing on a re-roof — old ones are usually the weak point.",
     "doc_kw": ["flash", "lead", "boot", "skylight", "gooseneck"]},
    {"key": "drip", "ic": "📐", "name": "Drip Edge & Trim",
     "what": "Metal edging along the eaves and rakes that guides water into your gutters and protects the fascia board.",
     "types": ["3\"x3\" galvanized drip edge", "Aluminum drip (color-matched)", "Eave & rake trim"],
     "brands": ["3\"x3\" galvanized — white/brown", "Color-matched aluminum"],
     "why": "Without proper drip edge, water wicks back under the roof and rots your fascia and soffit. It's a small part that protects the whole edge of your home.",
     "doc_kw": ["drip", "edge", "fascia", "trim"]},
]


_STAGE_TO_PHASE = {
    # pre-approval (-1 = Proposal phase visible in portal tracker)
    "prospect": -1, "new_lead": -1, "lead": -1, "contacted": -1,
    "estimate_sent": -1, "proposal": -1,
    # active pipeline
    "approved": 0, "finance_ntp": 0, "documentation": 0,
    "permit_applied": 1, "permit_approved": 1,
    "precon_needed": 2, "precon_complete": 2, "ready_teardown": 2,
    "teardown_started": 3, "teardown_complete": 3, "install_started": 3,
    "install_complete": 3, "punch_needed": 3, "punch_complete": 3,
    "final_needed": 4, "final_scheduled": 4, "final_passed": 4,
    "completed": 5, "invoiced": 5, "closed": 5,
}


_TOKEN_TTL_DAYS = 365   # full project lifespan
_TOKEN_SLIDE_DAYS = 90  # extend by this much when within 60 days of expiry


def _token_expires_str(days=_TOKEN_TTL_DAYS):
    import datetime
    return (datetime.date.today() + datetime.timedelta(days=days)).isoformat()


def _token_is_expired(expires_str):
    """True if expires_str is set AND today is past it."""
    if not expires_str:
        return False  # legacy tokens (NULL expiry) remain valid
    import datetime
    try:
        return datetime.date.today().isoformat() > expires_str[:10]
    except Exception:
        return False


def _maybe_slide(table, row_id, expires_str):
    """Extend the token TTL if it expires within 60 days."""
    if not expires_str:
        return
    import datetime
    threshold = (datetime.date.today() + datetime.timedelta(days=60)).isoformat()
    if expires_str[:10] <= threshold:
        db.update(table, row_id, portal_token_expires=_token_expires_str())


def ensure_token(job_id):
    """Return the job's portal token, generating + saving one if needed."""
    j = db.get("jobs", job_id)
    if not j:
        return None
    tok = j.get("portal_token")
    if not tok:
        tok = secrets.token_urlsafe(12)
        db.update("jobs", job_id, portal_token=tok,
                  portal_token_expires=_token_expires_str())
    return tok


def _job_by_token(token):
    if not token:
        return None
    rows = db.all_rows("jobs", "portal_token=?", (token,))
    if not rows:
        return None
    j = rows[0]
    if _token_is_expired(j.get("portal_token_expires")):
        return None
    _maybe_slide("jobs", j["id"], j.get("portal_token_expires"))
    return j


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
        db.update("leads", lead_id, portal_token=tok,
                  portal_token_expires=_token_expires_str())
    return tok


def _lead_by_token(token):
    if not token:
        return None
    rows = db.all_rows("leads", "portal_token=?", (token,))
    if not rows:
        return None
    l = rows[0]
    if _token_is_expired(l.get("portal_token_expires")):
        return None
    _maybe_slide("leads", l["id"], l.get("portal_token_expires"))
    return l


def lead_portal_link(lead_id):
    """Shareable homeowner-portal URL for a lead (pre-job welcome view)."""
    tok = ensure_lead_token(lead_id)
    return url_for("portal.home", token=tok, _external=True) if tok else ""


def _phase_index(stage):
    # Default to -1 (Proposal) — safer than showing "Approved" for
    # jobs that haven't been approved yet.
    return _STAGE_TO_PHASE.get(stage, -1)


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


def _token_is_expired_in_db(token):
    """True if the token exists in jobs or leads but is past its expiry date."""
    for table in ("jobs", "leads"):
        rows = db.all_rows(table, "portal_token=?", (token,))
        if rows and _token_is_expired(rows[0].get("portal_token_expires")):
            return True
    return False


# ---------------------------------------------------------------------------
# Servable-file guard — never show the homeowner a link to a file that 404s.
# A subset of legacy rows reference uploads whose bytes were lost in the old
# serverless (ephemeral-disk) window and were never mirrored to Drive. The
# /uploads route falls back to Drive, so "servable" = local file exists OR Drive
# has it. Result is cached per-process (Drive lookups are a network call); a new
# upload writes to the persistent disk so os.path.exists() catches it with no
# cache or Drive round-trip.
# ---------------------------------------------------------------------------
_SERVE_CACHE = {}


def _servable(subpath):
    if not subpath:
        return False
    subpath = str(subpath).lstrip("/")
    local = os.path.join(config.UPLOAD_DIR, *subpath.split("/"))
    if os.path.exists(local):
        return True
    base = os.path.basename(subpath)
    if base in _SERVE_CACHE:
        return _SERVE_CACHE[base]
    ok = False
    try:
        from modules import gdrive
        if gdrive.enabled():
            ok = bool(gdrive.find_drive_id(base))
    except Exception:
        ok = False
    _SERVE_CACHE[base] = ok
    return ok


def _doc_servable(d, folder):
    return _servable("%s/%s" % (folder, d.get("filename") or ""))


# Extensions safe to render inline; everything else downloads instead of executing,
# closing the stored-XSS surface from homeowner/staff-uploaded files served back
# through the portal (mirrors app.py's /uploads hardening, audit #12).
_PORTAL_INLINE_OK = {"png", "jpg", "jpeg", "gif", "webp", "bmp", "ico", "pdf"}


def _safe_portal_resp(resp, subpath):
    """Add anti-sniff + attachment-for-non-inline headers to a served-file response."""
    ext = subpath.rsplit(".", 1)[-1].lower() if "." in subpath else ""
    resp.headers["X-Content-Type-Options"] = "nosniff"
    if ext not in _PORTAL_INLINE_OK:
        resp.headers["Content-Disposition"] = "attachment"
    return resp


@bp.route("/<token>/file/<path:subpath>")
def portal_file(token, subpath):
    """Serve a document/photo to the homeowner, scoped to THEIR job/lead ONLY.

    The /uploads/* route is staff-session-gated, so the portal can't link there
    directly. This route validates the magic-link token, confirms the requested
    file actually belongs to that job/lead (no guessing other customers' files),
    then streams it (with the same path-traversal containment as /uploads)."""
    from flask import send_from_directory, Response
    job = _job_by_token(token)
    lead = None if job else _lead_by_token(token)
    if not (job or lead):
        abort(404)
    # Normalize FIRST, enforce UPLOAD_DIR containment, gate on the resolved path.
    full = os.path.normpath(os.path.join(config.UPLOAD_DIR, subpath))
    _root = config.UPLOAD_DIR.rstrip(os.sep)
    _pref = _root + os.sep
    if full != _root and not full.startswith(_pref):
        abort(404)
    rel = full[len(_pref):].replace(os.sep, "/") if full.startswith(_pref) else ""
    fname = os.path.basename(rel)
    owned = False
    if rel.startswith("library/"):
        owned = True  # shared product/warranty literature — shown to every portal visitor
    elif rel.startswith("documents/"):
        if job:
            owned = bool(db.all_rows("documents", "filename=? AND job_id=?", (fname, job["id"])))
        if not owned and lead:
            owned = bool(db.all_rows("documents", "filename=? AND lead_id=?", (fname, lead["id"])))
    elif rel.startswith("photos/"):
        if job:
            owned = bool(db.all_rows("photos", "filename=? AND job_id=?", (fname, job["id"])))
    if not owned:
        abort(403)
    if os.path.exists(full):
        return _safe_portal_resp(
            send_from_directory(os.path.dirname(full), os.path.basename(full)), rel)
    # Legacy files lost from local disk → R2/blob/Google Drive fallback. serve_fallback
    # returns a (bytes, mimetype) tuple; it MUST be wrapped in a Response — returning the
    # raw tuple makes Flask read the mimetype as the HTTP status, producing an invalid
    # status line that a WSGI proxy (gunicorn/Render) turns into a 502 on the download.
    try:
        from modules import gdrive
        got = gdrive.serve_fallback(rel)
        if got is not None:
            return _safe_portal_resp(Response(got[0], mimetype=got[1]), rel)
    except Exception:
        pass
    abort(404)


@bp.route("/invite/<token>")
def invite(token):
    """Alias: legacy invite-link format /portal/invite/<token> → redirect to home."""
    return redirect(url_for("portal.home", token=token), 301)


@bp.route("/login")
@bp.route("/login/<token>")
def portal_login(token=None):
    """Alias: /portal/login[/<token>] → redirect to portal home (or main login).
    Invite emails that used /portal/login/... still land on the correct portal."""
    if token:
        return redirect(url_for("portal.home", token=token), 301)
    return redirect(url_for("auth.login"), 302)


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
            _rep_rows = db.all_rows("users", "name=?", (l.get("rep") or "",), limit=1)
            rep = _rep_rows[0] if _rep_rows else None
            jr = journey_steps("lead", l, token)
            return render_template("lead_portal.html", l=l, client=nm or "there",
                                   rep=rep, company=db.get_company(), token=token,
                                   journey=jr, progress=journey_progress(jr))
        if _token_is_expired_in_db(token):
            company = db.get_company()
            return render_template("portal_expired.html", company=company), 410
        abort(404)
    _decorate(j)
    estimates = db.all_rows("estimates", "job_id=?", (j["id"],), "id DESC")
    photos = [p for p in db.all_rows("photos", "job_id=?", (j["id"],), "id DESC")
              if _doc_servable(p, "photos")]
    all_docs = db.all_rows("documents", "job_id=?", (j["id"],), "id DESC")
    # Documents the company has requested the homeowner to e-sign (not yet signed).
    docs_to_sign = [d for d in all_docs if d.get("needs_sign") and not d.get("signed_at")
                    and _doc_servable(d, "documents")]
    # Only show customer-appropriate documents (contracts, permits, warranties, COI).
    show_cats = {"Contract", "Permit", "Warranty", "COI", "NOA", "Measurement", "HOA"}
    documents = [d for d in all_docs if (d.get("category") or "") in show_cats
                 and _doc_servable(d, "documents")]
    invoices = db.all_rows("invoices", "job_id=?", (j["id"],), "id DESC")
    payments = db.all_rows("payments", "job_id=?", (j["id"],), "id DESC")
    # When real billing was synced from AccuLynx (invoices/payments exist), drive the
    # Balance Due + Paid% from the ACTUAL numbers instead of the generic draw schedule.
    paid_real = sum(theme.est_num(p.get("amount")) for p in payments)
    inv_total = sum(theme.est_num(i.get("amount")) for i in invoices)
    j["_has_billing"] = bool(invoices or payments or (j.get("balance") not in (None, "")))
    base = j["_value"] or inv_total
    if j.get("balance") not in (None, "") and base:
        # AccuLynx's exact Balance Due wins (Collected = value - balance).
        j["_balance"] = theme.est_num(j.get("balance"))
        paid_real = max(paid_real, base - j["_balance"])
        j["_paid_pct"] = max(0.0, min(1.0, (base - j["_balance"]) / base))
    elif j["_has_billing"] and (paid_real or invoices):
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
    product_docs = [d for d in lib if _relevant(d) and _doc_servable(d, "library")]
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
    thread = thread_messages(j["id"])
    return render_template("portal_dashboard.html", j=j, token=token,
                           thread=thread,
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
                           photo_app_url=company.get("photo_app_url"),
                           journey=journey_steps("job", j, token),
                           progress=journey_progress(journey_steps("job", j, token)))


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
    dphoto = rec.get("design_photo")
    if dphoto and not _servable(dphoto if "/" in str(dphoto) else "photos/%s" % dphoto):
        dphoto = None  # original upload lost (legacy ephemeral-disk) — don't show a broken image
    return render_template("design_studio.html", token=token, rec=rec, kind=kind,
                           company=db.get_company(), colors=ROOF_COLORS, options=ROOF_OPTIONS,
                           start_system=sysk, design_photo=dphoto)


@bp.route("/<token>/design/photo", methods=["POST"])
def design_photo(token):
    """Homeowner uploads a photo of their own roof/house in the Design Studio — stored on
    the record so the rep can use it (and, later, visualize the chosen color on it)."""
    kind, rec = _record_by_any_token(token)
    if not rec:
        abort(404)
    f = request.files.get("photo")
    if f and f.filename:
        ext = (f.filename.rsplit(".", 1)[-1] or "jpg").lower()[:5]
        if ext in ("jpg", "jpeg", "png", "heic", "webp", "gif"):
            os.makedirs(config.PHOTO_DIR, exist_ok=True)
            fn = "myroof_%s_%d.%s" % (kind, int(time.time() * 1000), ext)
            f.save(os.path.join(config.PHOTO_DIR, fn))
            try:
                db._ensure_column("leads" if kind == "lead" else "jobs", "design_photo", "TEXT")
                db.update("leads" if kind == "lead" else "jobs", rec["id"], design_photo=fn)
            except Exception:
                pass
            # Jobs also get it in the photo gallery so it shows up project-wide.
            if kind == "job":
                try:
                    db.insert("photos", {"job_id": rec["id"], "album": "Homeowner", "phase": "design",
                                         "caption": "Homeowner's roof photo (Design Studio)",
                                         "filename": fn, "original_name": f.filename})
                except Exception:
                    pass
            db.add_activity(kind, rec["id"], "note", "🏠 Homeowner uploaded a photo of their roof in the Design Studio.")
            flash("Thanks! Your roof photo is uploaded — your project contact can see it now.", "ok")
        else:
            flash("Please upload an image (JPG, PNG, HEIC).", "error")
    return redirect(url_for("portal.design", token=token))


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
    """Interactive roof-education game — system explainers, the install process walked
    through with REAL photos from similar jobs, a Roof IQ quiz, and a glossary."""
    kind, rec = _record_by_any_token(token)
    if not rec:
        abort(404)
    from modules import ahj as ahj_mod
    sysk = (rec.get("system") or ahj_mod.work_type_to_system(rec.get("work_type", "")) or "shingle").lower()
    my_system = next((s for s in ROOF_EDU if s["key"] == sysk), ROOF_EDU[0])
    import concurrent.futures as _cf
    excl = rec["id"] if kind == "job" else -1
    rec_dept = rec.get("department")
    # Fire SiteCam calls + one_photo_per_system in parallel — cold SiteCam can take up to 4s each.
    with _cf.ThreadPoolExecutor(max_workers=3) as _pool:
        _gf = _pool.submit(similar_job_photos, sysk, excl, rec_dept)
        _lf = _pool.submit(latest_system_job_photos, sysk, rec_dept)
        _pf = _pool.submit(one_photo_per_system, rec_dept)
        try:
            gallery = _gf.result(timeout=6)
        except Exception:
            gallery = {}
        try:
            sitecam_job = _lf.result(timeout=6)
        except Exception:
            sitecam_job = None
        try:
            system_photos = _pf.result(timeout=6)
        except Exception:
            system_photos = {}
    # Annotate features with a matching product data sheet (if one exists in the library).
    pdocs = product_docs_for(sysk)
    feats = []
    for f in FEATURES:
        doc = next((d for d in pdocs if any(k in (d.get("original_name") or "").lower()
                                            for k in f.get("doc_kw", []))), None)
        feats.append(dict(f, doc=doc))
    return render_template("learn.html", token=token, company=db.get_company(),
                           systems=ROOF_EDU, glossary=GLOSSARY, quiz=ROOF_QUIZ,
                           process=PROCESS_STEPS, gallery=gallery, my_system=my_system, sysk=sysk,
                           system_photos=system_photos, features=feats,
                           product_docs=pdocs, sitecam_job=sitecam_job)


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


@bp.route("/<token>/proposal/<int:est_id>")
def proposal(token, est_id):
    """Token-gated, login-free render of the homeowner's own proposal (the estimate print
    view). Only serves estimates that belong to this token's record — so it's safe to embed
    in the portal without exposing other customers' estimates."""
    kind, rec = _record_by_any_token(token)
    if not rec:
        abort(404)
    e = db.get("estimates", est_id)
    link = "job_id" if kind == "job" else "lead_id"
    if not e or e.get(link) != rec["id"]:
        abort(404)
    from modules import estimates as est_mod
    sections = est_mod._load_sections(est_id)
    totals = est_mod.estimate_totals(e, sections)
    return render_template("estimate_print.html", e=e, sections=sections, totals=totals,
                           draws=est_mod._draws(totals["total"]))


@bp.route("/<token>/upload-doc", methods=["POST"])
def upload_doc(token):
    j = _job_by_token(token)
    if not j:
        abort(404)
    _DOC_ALLOWED = {
        "pdf", "doc", "docx", "xls", "xlsx", "csv", "txt", "rtf",
        "jpg", "jpeg", "png", "gif", "webp", "heic",
        "zip", "tar", "gz",
    }
    f = request.files.get("file")
    if f and f.filename:
        ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
        if ext not in _DOC_ALLOWED:
            flash("File type not allowed. Upload PDF, Word, Excel, image, or ZIP files only.", "err")
            return redirect(url_for("portal.home", token=token) + "#documents")
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


def thread_messages(job_id):
    """The full two-way conversation for a job, oldest-first (chat order)."""
    return db.all_rows("portal_messages", "job_id=?", (job_id,), "id ASC")


def _client_label(j):
    """Friendly homeowner name for the thread (falls back to 'Homeowner')."""
    nm = re.sub(r"^\s*[A-Za-z]?-?\d{3,}\s*[:\-]\s*", "", (j.get("name") or ""))
    nm = re.sub(r"\s*\([^)]*\)", "", nm).strip(" -·,")
    return nm or "Homeowner"


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
        # Persist as an inbound message in the two-way thread.
        try:
            db.insert("portal_messages", {
                "job_id": j["id"], "lead_id": None, "direction": "in",
                "body": text, "author": _client_label(j), "created": db.now(),
                "read_at": None, "department": j.get("department")})
        except Exception:
            pass
        from modules import notifications
        notifications.notify(j["id"], "request" if kind == "request" else "message",
                             "%s: %s" % (label, text[:140]))
        # surface it as a follow-up for the rep
        db.update("jobs", j["id"], next_follow=db.today())
        flash("Thanks! Your message was sent to our team.", "ok")
    return redirect(url_for("portal.home", token=token) + "#message")


@bp.route("/job/<int:job_id>/reply", methods=["POST"])
def reply(job_id):
    """Rep replies to the homeowner's portal thread (authenticated staff only).

    NOT a public/magic-link route — it resolves the job by its CRM id under the
    logged-in session (the app-wide before_request guard enforces login + CSRF).
    Inserts an 'out' message and marks any unread inbound messages as read."""
    from modules.auth import current_user
    user = current_user()
    if not user:
        abort(403)
    j = db.get("jobs", job_id)
    if not j:
        abort(404)
    body = (request.form.get("body") or request.form.get("text") or "").strip()
    if body:
        try:
            db.insert("portal_messages", {
                "job_id": j["id"], "lead_id": None, "direction": "out",
                "body": body, "author": user.get("name") or "Our team",
                "created": db.now(), "read_at": None,
                "department": j.get("department")})
            # The rep has now seen the homeowner's pending messages.
            db.execute("UPDATE portal_messages SET read_at=? "
                       "WHERE job_id=? AND direction='in' AND (read_at IS NULL OR read_at='')",
                       (db.now(), j["id"]))
        except Exception:
            pass
        db.add_activity("job", j["id"], "note",
                        "Replied to homeowner in portal: %s" % body[:200])
        flash("Reply sent to the homeowner's portal.", "ok")
    nxt = request.form.get("next") or request.referrer
    if nxt and nxt.startswith("/"):
        return redirect(nxt)
    return redirect(url_for("jobs.detail", job_id=j["id"]) + "#message")


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
        return redirect(url_for("portal.home", token=token) + "#estimate")
    db.update("documents", doc_id, signed_name=name, signed_at=db.now(),
              signature=sig, needs_sign=0)
    db.add_activity("job", j["id"], "automation",
                    "✅ Document e-signed by homeowner: %s (%s)" % (d.get("original_name", ""), name))
    from modules import notifications
    notifications.notify(j["id"], "sign", "%s e-signed: %s" % (name, d.get("original_name", "a document")))
    db.update("jobs", j["id"], next_follow=db.today())
    flash("Thank you — your signature was recorded.", "ok")
    return redirect(url_for("portal.home", token=token) + "#estimate")


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
