# -*- coding: utf-8 -*-
"""Branded Demo-Portal Generator — a SALES tool for selling the white-label CRM.

A salesperson enters a PROSPECT contractor's company name, logo, and brand colors;
this mints a shareable link to a self-contained, fully-branded DEMO homeowner
portal — a synthetic sample job pre-loaded with the live milestone tracker, the
Design Studio, and the Referral Game — so the prospect can "play with it on their
phone" BEFORE a sales meeting and experience the customer portal with THEIR brand.

Isolation: NOTHING here touches real jobs/leads/portal_tokens. The only persisted
row is a `demos` record holding the prospect's branding + a slug. The sample job,
design selections, and referral state are synthesized in-memory per slug (referral
counters live in a process-local dict — ephemeral, demo-only). The portal templates'
look is reproduced with the demo's brand overriding the `company` context, leaving
the real company untouched.

Coordinates with the live portal by REUSING its content constants (PHASE_INFO,
VALUE_STEPS, ROOF_COLORS/OPTIONS, REFERRAL_TIERS, SHARE_LEVELS) read-only — so the
demo stays in sync with the real portal without modifying portal.py.
"""
import os
import re
import time
import secrets

from flask import (Blueprint, render_template, request, redirect, url_for,
                   Response,
                   abort, flash, jsonify)

import config
import db
from modules import portal  # read-only reuse of portal content constants + helpers

bp = Blueprint("demo", __name__)

# --- schema (module-load convention, mirrors the rest of the app) ----------
try:
    db.execute("""CREATE TABLE IF NOT EXISTS demos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created TEXT, slug TEXT, company_name TEXT,
        logo_url TEXT, tagline TEXT, phone TEXT, website TEXT,
        color_masthead TEXT, color_primary TEXT, color_accent TEXT,
        sample_system TEXT DEFAULT 'shingle', created_by TEXT)""")
except Exception:
    pass
# Contractor leads captured by the public sales landing page (myroofportal.com root).
try:
    db.execute("""CREATE TABLE IF NOT EXISTS portal_leads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created TEXT, name TEXT, company TEXT, email TEXT, phone TEXT,
        source_host TEXT)""")
except Exception:
    pass
# Purchase offers captured by the "domain + software for sale" landing page
# (myroofportal.com root — the page that replaced the license-sales landing).
try:
    db.execute("""CREATE TABLE IF NOT EXISTS portal_offers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT, name TEXT, email TEXT, offer TEXT, message TEXT,
        source_host TEXT)""")
except Exception:
    pass
# Email addresses captured by the "see the live demo" gate on the sales home page.
try:
    db.execute("""CREATE TABLE IF NOT EXISTS demo_access_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created TEXT, email TEXT, slug TEXT, source_host TEXT)""")
except Exception:
    pass
# First-party funnel events for the sales page. The internal DB is the source of
# truth for traffic/conversion — ad blockers make third-party analytics a floor,
# not a measurement. GA4 is optional (GA4_MEASUREMENT_ID) and purely additive.
try:
    db.execute("""CREATE TABLE IF NOT EXISTS portal_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created TEXT, kind TEXT, path TEXT, referrer TEXT,
        ua TEXT, source_host TEXT)""")
except Exception:
    pass
# Whether the owner alert for each captured lead actually went out. Without this,
# a silent mailer failure is indistinguishable from "no leads yet".
for _t in ("portal_offers", "portal_leads", "demo_access_requests"):
    try:
        db._ensure_column(_t, "notified", "INTEGER DEFAULT 0")
    except Exception:
        pass
# Bot-farmed offers are kept but flagged, so a real offer is never dropped and
# the owner is not paged for crypto spam.
for _t, _c, _d in (("portal_offers", "spam", "INTEGER DEFAULT 0"),
                   ("portal_offers", "spam_reason", "TEXT")):
    try:
        db._ensure_column(_t, _c, _d)
    except Exception:
        pass
db._COLCACHE.clear()

# ---------------------------------------------------------------------------
# Lead alerts + first-party funnel tracking
# ---------------------------------------------------------------------------
def _notify_to():
    """Where sales alerts go. Override with PORTAL_NOTIFY_TO."""
    return (os.environ.get("PORTAL_NOTIFY_TO")
            or "daniel@collaborativeconceptsfl.com").strip()


def _notify(subject, body):
    """Best-effort owner alert. True only if the mail actually went out. Never
    raises and never blocks the visitor: the DB row is the durable record, this
    is the nudge on top of it."""
    try:
        from modules import gmail as _gm
        if not _gm.smtp_configured():
            return False
        return bool(_gm._smtp_send(_notify_to(), subject, body))
    except Exception:
        return False


_SPAM_WORDS = (
    "lamborghini", "usdt", "btc", "bitcoin", "jackpot", "promo code", "playstation",
    "telegra.ph", "graph.org", "freeurlredirect", "casino", "crypto", "withdrawal",
    "you have (1) message", "transfer401", "viagra", "seo services", "backlink",
)
_URL_RE = re.compile(r"https?://|www\.", re.I)


def _spam_score(name, email, offer, message):
    """Return (score, reasons). >=2 means almost certainly bot-farmed.

    Never used to drop a submission -- only to skip the owner alert and mark the
    row, so a false positive costs a notification, not a lead.
    """
    blob = " ".join((name, offer, message)).lower()
    reasons = []
    if any(w in blob for w in _SPAM_WORDS):
        reasons.append("spam-keyword")
    if _URL_RE.search(name) or _URL_RE.search(offer):
        reasons.append("url-in-name-or-offer")
    if name and offer and name.strip().lower() == offer.strip().lower():
        reasons.append("name-equals-offer")
    if _URL_RE.search(message or "") and len(message or "") < 200:
        reasons.append("link-only-message")
    # An offer field with no digit at all is not an offer.
    if offer and not re.search(r"\d", offer):
        reasons.append("offer-has-no-number")
    if len(name) > 60:
        reasons.append("name-too-long")
    if _looks_random(name) or _looks_random(offer):
        reasons.append("random-token")
    return len(reasons), reasons


def _looks_random(v):
    """True for keyboard-mash tokens like 'PLIcJslJMuPyYmvpcQU'. Deliberately
    narrow: a single word, long, with a burst of interior capitals -- a real
    person's name or company does not look like this."""
    v = (v or "").strip()
    if len(v) < 12 or " " in v:
        return False
    return sum(1 for ch in v[1:] if ch.isupper()) >= 4


def _log_event(kind, path=""):
    """Record a funnel event. Silent on failure — tracking must never break a page."""
    try:
        db.insert("portal_events", {
            "created": db.now(), "kind": (kind or "")[:40],
            "path": (path or request.path or "")[:200],
            "referrer": (request.referrer or "")[:300],
            "ua": (request.headers.get("User-Agent") or "")[:300],
            "source_host": (request.host or "")[:120]})
    except Exception:
        pass


# Canonical product-demo brand — a generic, believable roofing company so the public
# demo shows a real-looking roofer, not the operating tenant's name or the product name.
# Ensured on import: creates the `roof-portal` demo if missing, and upgrades the earlier
# auto-seeded placeholder ("Roof Portal") to this brand. Only ever touches the auto-seeded
# row (created_by='seed') — never a manually-created demo.
_DEMO_DEFAULT = {
    "company_name": "Summit Roofing Co.", "tagline": "Roofs done right — on time, every time.",
    "phone": "(555) 018-2440", "website": "https://summitroofing.example.com",
    "color_masthead": "#15201A", "color_primary": "#37B34A", "color_accent": "#2A8F3A",
    "sample_system": "shingle",
}


def _ensure_default_demo():
    try:
        rows = db.all_rows("demos", "slug=?", ("roof-portal",))
        if not rows:
            db.insert("demos", dict(_DEMO_DEFAULT, created=db.now(), slug="roof-portal",
                                    logo_url="", created_by="seed"))
        elif rows[0].get("created_by") == "seed" and (rows[0].get("company_name") or "") in ("", "Roof Portal", "KLR Roofing"):
            db.update("demos", rows[0]["id"], company_name=_DEMO_DEFAULT["company_name"],
                      tagline=_DEMO_DEFAULT["tagline"], phone=_DEMO_DEFAULT["phone"],
                      website=_DEMO_DEFAULT["website"])
    except Exception:
        pass


_ensure_default_demo()

# Dedicated demo/sales domains (e.g. myroofportal.com). A bare visit to one of
# these hosts' "/" serves the contractor-facing SALES LANDING PAGE (app.py wires
# the before_request); the homeowner demo lives at /demo/<DEMO_SLUG> behind it.
# Env-driven so this stays white-label, not hardcoded to one tenant.
DEMO_HOSTS = {h.strip().lower() for h in
              os.environ.get("CRM_DEMO_HOSTS", "myroofportal.com,www.myroofportal.com").split(",")
              if h.strip()}
DEMO_SLUG = os.environ.get("CRM_DEMO_SLUG", "roof-portal")

# Ephemeral, process-local referral game state per demo slug (resets on restart —
# this is a throwaway sales demo, never persisted).
_REF_STATE = {}


def _ensure_default_demo():
    """Self-heal the default sales demo (the one wired to the demo domain) so its
    branding matches config on every deploy. Env-driven so it stays white-label:
    CRM_DEMO_SLUG picks the row, CRM_DEMO_SEED_NAME the display name. Creates it if
    missing; refreshes branding ONLY while it still carries the old placeholder name
    (never clobbers an intentionally-edited demo)."""
    slug = (os.environ.get("CRM_DEMO_SLUG") or "roof-portal").strip()
    if not slug:
        return
    # Never let a stale env var brand the public demo with an operating tenant's name.
    _env_name = (os.environ.get("CRM_DEMO_SEED_NAME") or "").strip()
    if _env_name.lower() in ("", "klr roofing", "klr roofing corp", "seabreeze", "seabreeze roofing"):
        _env_name = ""
    name = _env_name or "Summit Roofing Co."
    brand = {"company_name": name,
             "tagline": "Roofs done right — on time, every time.",
             "website": "https://summitroofing.example.com", "phone": "(555) 018-2440",
             "color_masthead": "#15201A", "color_primary": "#37B34A",
             "color_accent": "#2A8F3A", "sample_system": "shingle"}
    try:
        rows = db.all_rows("demos", "slug=?", (slug,))
        if rows:
            r0 = rows[0]
            # This is THE default demo (slug=CRM_DEMO_SLUG, wired to the demo domain), so it
            # always self-heals to the configured brand — regardless of created_by. (The
            # created_by guard only matters for per-prospect demos, which have other slugs.)
            stale_name = (r0.get("company_name") or "") in ("", "Roof Portal", "Your Roofing Co.", "KLR Roofing")
            stale_color = (r0.get("color_primary") or "") != brand["color_primary"] \
                or (r0.get("color_masthead") or "") != brand["color_masthead"]
            if stale_name or stale_color:
                db.update("demos", r0["id"], **brand)
        else:
            db.insert("demos", dict(brand, slug=slug, created=db.now(), created_by="seed"))
    except Exception:
        pass


_ensure_default_demo()

# A sensible default brand if a field is left blank (keeps the demo looking finished).
_DEF_MASTHEAD = "#24476C"
_DEF_PRIMARY = "#4680BF"
_DEF_ACCENT = "#8CC63F"
_SYS_WORKTYPE = {"shingle": "Roofing - Architectural Shingle", "tile": "Roofing - Concrete Tile",
                 "metal": "Roofing - Standing-Seam Metal", "flat": "Roofing - Flat / TPO"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slugify(name):
    base = re.sub(r"[^a-z0-9]+", "-", (name or "demo").lower()).strip("-")[:40] or "demo"
    slug = base
    # Ensure uniqueness; append a short random suffix on collision.
    while db.all_rows("demos", "slug=?", (slug,)):
        slug = "%s-%s" % (base, secrets.token_hex(2))
    return slug


def _get_demo(slug):
    rows = db.all_rows("demos", "slug=?", (slug,))
    return rows[0] if rows else None


def _norm_color(v, fallback):
    v = (v or "").strip()
    if not v:
        return fallback
    if not v.startswith("#"):
        v = "#" + v
    return v if re.match(r"^#[0-9a-fA-F]{3,8}$", v) else fallback


def _logo_src(logo_url):
    """A demo logo is either an external http(s) URL or an uploaded file stored as
    a relative 'branding/<file>' path served by the app's /uploads route."""
    if not logo_url:
        return ""
    if logo_url.startswith(("http://", "https://", "//")):
        return logo_url
    return url_for("uploads", subpath=logo_url)


def _demo_company(d):
    """A `company`-shaped dict carrying the demo's branding. Passed to the portal
    templates to OVERRIDE the real company context — the real row is never touched."""
    name = d.get("company_name") or "Your Roofing Co."
    return {
        "name": name, "legal_name": name,
        "tagline": d.get("tagline") or "Quality roofing, done right.",
        "phone": d.get("phone") or "(555) 123-4567",
        "email": "hello@%s" % (re.sub(r"[^a-z0-9]", "", name.lower())[:18] or "roofing") + ".com",
        "website": d.get("website") or "",
        "license": "DEMO-0000", "qualifier": name,
        "logo_path": d.get("logo_url") or "", "logo_src": _logo_src(d.get("logo_url")),
        "color_masthead": _norm_color(d.get("color_masthead"), _DEF_MASTHEAD),
        "color_primary": _norm_color(d.get("color_primary"), _DEF_PRIMARY),
        "color_accent": _norm_color(d.get("color_accent"), _DEF_ACCENT),
        "color_warn": "#F78300", "color_danger": "#E25050",
        "portal_perks": "", "tutorials": "",
    }


def _sample_job(d):
    """Synthetic in-memory homeowner job — never inserted. Decorated with the same
    portal._decorate() the real portal uses, so the tracker/value math match."""
    system = (d.get("sample_system") or "shingle").lower()
    j = {
        "id": 0, "name": "Jordan & Taylor Rivera", "first": "Jordan",
        "address": "1428 Coastal Breeze Dr", "city": "Boca Raton", "state": "FL", "zip": "33431",
        "work_type": _SYS_WORKTYPE.get(system, _SYS_WORKTYPE["shingle"]),
        "system": system, "rep": "Sam Carter",
        # Mid-project so the tracker shows completed + current + upcoming milestones
        # (a livelier "play with it" demo than sitting at step 1).
        "stage": "install_started",
        "contract_value": "$24,800",
        "payments": db.dump_json({"p1": True, "p2": True}),
    }
    portal._decorate(j)
    return j


def _sample_updates(phase):
    """A few milestone updates for the feed, for every phase reached so far."""
    out = []
    for ph in range(0, min(phase, len(portal.CUSTOMER_PHASES) - 1) + 1):
        out.append({"title": portal.CUSTOMER_PHASES[ph], "created": ""})
    return list(reversed(out))


def _value_steps(phase):
    steps = [{"text": t, "phase": ph, "done": ph < phase, "current": ph == phase}
             for ph, t in portal.VALUE_STEPS]
    done = sum(1 for v in steps if v["done"])
    return steps, done


def _checklist(phase):
    out = []
    for i, p in enumerate(portal.PHASE_INFO):
        out.append({"name": p["name"], "desc": p["desc"], "timeframe": p["tf"],
                    "you": p.get("you", []), "done": i < phase, "current": i == phase})
    return out


def _referral_ctx(d, link):
    """Referral-game context (mirrors portal.referral_ctx) without any DB write —
    counters come from the ephemeral per-slug state."""
    st = _REF_STATE.setdefault(d["slug"], {"shares": 0, "signed": 2})
    shares, signed = st["shares"], st["signed"]
    lvl, nxt = portal._share_level(shares)
    return {
        "code": d["slug"], "link": link, "shares": shares, "clicks": 0, "signed": signed,
        "level": lvl, "next_level": nxt,
        "tiers": [dict(t, unlocked=signed >= t["n"]) for t in portal.REFERRAL_TIERS],
        "next_tier": next((t for t in portal.REFERRAL_TIERS if signed < t["n"]), None),
        "msg": "", "first": "Jordan",
    }


# ---------------------------------------------------------------------------
# Generator UI (login-gated — a sales tool for the CRM operator)
# ---------------------------------------------------------------------------

@bp.route("/demos")
def generator():
    demos = db.all_rows("demos", order="id DESC")
    for x in demos:
        x["_link"] = url_for("demo.portal", slug=x["slug"], _external=True)
        x["_logo_src"] = _logo_src(x.get("logo_url"))
    try:
        access = db.all_rows("demo_access_requests", order="id DESC")[:200]
    except Exception:
        access = []
    return render_template("demo_generator.html", demos=demos, access=access,
                           defaults={"masthead": _DEF_MASTHEAD, "primary": _DEF_PRIMARY,
                                     "accent": _DEF_ACCENT})


@bp.route("/demos/create", methods=["POST"])
def create():
    f = request.form
    name = (f.get("company_name") or "").strip()
    if not name:
        flash("Enter the prospect's company name.", "error")
        return redirect(url_for("demo.generator"))
    logo_url = (f.get("logo_url") or "").strip()
    # Optional logo file upload (takes precedence over a pasted URL).
    up = request.files.get("logo_file")
    if up and up.filename:
        fn = "demo_%d_%s" % (int(time.time() * 1000),
                             re.sub(r"[^A-Za-z0-9._-]+", "_", up.filename))
        os.makedirs(config.LOGO_DIR, exist_ok=True)
        up.save(os.path.join(config.LOGO_DIR, fn))
        logo_url = "branding/%s" % fn
    slug = _slugify(name)
    from modules.auth import current_user
    who = (current_user() or {}).get("name", "")
    db.insert("demos", {
        "created": db.now(), "slug": slug, "company_name": name, "logo_url": logo_url,
        "tagline": (f.get("tagline") or "").strip(), "phone": (f.get("phone") or "").strip(),
        "website": (f.get("website") or "").strip(),
        "color_masthead": _norm_color(f.get("color_masthead"), _DEF_MASTHEAD),
        "color_primary": _norm_color(f.get("color_primary"), _DEF_PRIMARY),
        "color_accent": _norm_color(f.get("color_accent"), _DEF_ACCENT),
        "sample_system": (f.get("sample_system") or "shingle").strip(), "created_by": who})
    flash("Demo portal created for %s — copy the link and text it to your prospect." % name, "ok")
    return redirect(url_for("demo.generator") + "#demo-" + slug)


@bp.route("/demos/<slug>/delete", methods=["POST"])
def delete(slug):
    d = _get_demo(slug)
    if d:
        db.delete("demos", d["id"])
        _REF_STATE.pop(slug, None)
        flash("Demo deleted.", "ok")
    return redirect(url_for("demo.generator"))


# ---------------------------------------------------------------------------
# Shared portal content — identical on the demo AND the real homeowner portal
# (portal.py imports these so the live portal mirrors the demo exactly).
# ---------------------------------------------------------------------------
RECENT_WORK = [
    {"img": "demo/demo-metal-finished.jpg", "system": "Standing-Seam Metal"},
    {"img": "demo/demo-tile-finished.jpg", "system": "Concrete Tile"},
    {"img": "demo/demo-shingle-6-finished.jpg", "system": "Architectural Shingle"},
    {"img": "demo/demo-metal-2.jpg", "system": "Metal — Clean Lines"},
    {"img": "demo/demo-shingle-3-deck.jpg", "system": "New Deck & Dry-In"},
    {"img": "demo/demo-shingle-2-tearoff.jpg", "system": "Tear-Off Day"},
]
ADDON_CATS = [
    {"key": "gutters", "name": "Gutters & Drainage", "tag": "Protect your new roof", "icon": "droplet", "items": [
        {"name": "Seamless Aluminum Gutters", "price": "$1,850", "unit": "whole home", "blurb": "5\" K-style, color-matched to your roof", "badge": "Popular", "img": "demo/addons/01-seamless-aluminum-gutters.webp"},
        {"name": "Leaf-Guard Gutter Protection", "price": "$690", "unit": "", "blurb": "Never clean your gutters again", "badge": "", "img": "demo/addons/02-leaf-guard-gutter-protection.webp"},
        {"name": "Downspout Extensions", "price": "$120", "unit": "", "blurb": "Move water away from your foundation", "badge": "", "img": "demo/addons/03-downspout-extensions.webp"},
        {"name": "Copper Half-Round Upgrade", "price": "$4,200", "unit": "", "blurb": "Premium coastal look", "badge": "Premium", "img": "demo/addons/04-copper-half-round-upgrade.webp"},
    ]},
    {"key": "maint", "name": "Maintenance Plans", "tag": "Keep your warranty valid", "icon": "shield", "items": [
        {"name": "Annual Roof Checkup", "price": "$199", "unit": "/yr", "blurb": "Yearly inspection + tune-up", "badge": "Best value", "img": "demo/addons/05-annual-roof-checkup.webp"},
        {"name": "Premium Care Plan", "price": "$349", "unit": "/yr", "blurb": "2 visits/yr + priority scheduling", "badge": "Popular", "img": "demo/addons/06-premium-care-plan.webp"},
        {"name": "Gutter Cleaning (2x/yr)", "price": "$149", "unit": "/yr", "blurb": "Spring & fall clean-outs", "badge": "", "img": "demo/addons/07-gutter-cleaning-2x-year.webp"},
        {"name": "Storm Response Membership", "price": "$99", "unit": "/yr", "blurb": "Priority post-storm inspection", "badge": "", "img": "demo/addons/08-storm-response-membership.webp"},
    ]},
    {"key": "inspect", "name": "Inspections", "tag": "Save on insurance", "icon": "search", "items": [
        {"name": "Wind Mitigation Inspection", "price": "$125", "unit": "", "blurb": "Can lower your insurance premium", "badge": "Insurance discount", "img": "demo/addons/09-wind-mitigation-inspection.webp"},
        {"name": "Drone Roof Inspection", "price": "$99", "unit": "", "blurb": "HD aerial photos + report", "badge": "", "img": "demo/addons/10-drone-roof-inspection.webp"},
        {"name": "Post-Storm Damage Inspection", "price": "Free", "unit": "", "blurb": "After any named storm", "badge": "Free", "img": "demo/addons/11-post-storm-damage-inspection.webp"},
        {"name": "4-Point Home Inspection", "price": "$150", "unit": "", "blurb": "For insurance or closing", "badge": "", "img": "demo/addons/12-four-point-home-inspection.webp"},
    ]},
    {"key": "upgrades", "name": "Roof Upgrades", "tag": "Add before install day", "icon": "star", "items": [
        {"name": "Ridge Vent + Attic Ventilation", "price": "$650", "unit": "", "blurb": "Cooler attic, longer roof life", "badge": "Recommended", "img": "demo/addons/13-ridge-vent-attic-ventilation.webp"},
        {"name": "Hurricane-Rated Skylights", "price": "$1,200", "unit": "each", "blurb": "Natural light, impact-rated", "badge": "", "img": "demo/addons/14-hurricane-rated-skylights.webp"},
        {"name": "Peel-&-Stick Underlayment Upgrade", "price": "$900", "unit": "", "blurb": "Max secondary water barrier", "badge": "Popular", "img": "demo/addons/15-peel-stick-underlayment-upgrade.webp"},
        {"name": "Extended 25-Yr Workmanship Warranty", "price": "$450", "unit": "", "blurb": "Double your coverage", "badge": "", "img": "demo/addons/16-extended-25yr-workmanship-warranty.webp"},
    ]},
]


# ---------------------------------------------------------------------------
# Public demo portal (login-free — this is the shareable link)
# ---------------------------------------------------------------------------

# Endpoint is set to "portal" (templates/url_for use `demo.portal`); the function
# keeps a distinct name so it doesn't shadow the imported `portal` module.
@bp.route("/demo/<slug>", endpoint="portal")
def portal_view(slug):
    d = _get_demo(slug)
    if not d:
        abort(404)
    _log_event("demo_view", "/demo/%s" % slug)
    company = _demo_company(d)
    j = _sample_job(d)
    phase = j["_phase"]
    value_steps, value_done = _value_steps(phase)
    link = url_for("demo.portal", slug=slug, _external=True)
    # Synthetic showcase data so the demo demonstrates the SiteCam feed, QuickBooks
    # billing, the document center + e-sign, and Roof School — no real records touched.
    # Curated install-sequence job photos (generic demo set — served from static/demo/).
    demo_photos = [
        {"cap": "Before — your existing roof", "sub": "Day 1", "at": "7:12 AM",
         "img": "demo/demo-shingle-1-before.jpg", "hex": "#6b7280"},
        {"cap": "Tear-off complete", "sub": "Stripped to the deck", "at": "9:40 AM",
         "img": "demo/demo-shingle-2-tearoff.jpg", "hex": "#8a6e4b"},
        {"cap": "New shingles going on", "sub": "6-nail high-wind pattern", "at": "3:05 PM",
         "img": "demo/demo-shingle-5-install.jpg", "hex": "#374151"},
        {"cap": "Your new roof", "sub": "Cleaned up & magnet-swept", "at": "Finished",
         "img": "demo/demo-shingle-6-finished.jpg", "hex": "#2c4733"},
        {"cap": "Deck re-nailed to code", "sub": "Every sheet secured", "at": "11:05 AM",
         "img": "demo/demo-shingle-3-deck.jpg", "hex": "#5b4a3a"},
        {"cap": "Peel-&-stick underlayment", "sub": "Secondary water barrier down", "at": "1:20 PM",
         "img": "demo/demo-shingle-4-dryin.jpg", "hex": "#1f6f8b"},
    ]
    # Recent-work gallery — real finished roofs across systems (marketing showcase).
    recent_work = RECENT_WORK
    demo_invoices = [
        {"label": "Deposit (draw 1)", "amount": "$7,440", "status": "Paid", "paid": True},
        {"label": "Materials draw", "amount": "$9,920", "status": "Due now", "paid": False},
        {"label": "Completion draw", "amount": "$7,440", "status": "Upcoming", "paid": False},
    ]
    demo_docs = [
        {"name": "Roofing Agreement", "kind": "Contract", "status": "signed"},
        {"name": "Notice of Commencement", "kind": "NOC", "status": "sign"},
        {"name": "Palm Beach County Permit", "kind": "Permit", "status": "sign"},
        {"name": "25-Year Workmanship Warranty", "kind": "Warranty", "status": "ready"},
    ]
    # Instacart-style add-on marketplace — browse & "Add" upgrades to your project.
    addon_cats = ADDON_CATS
    return render_template(
        "demo_portal.html", slug=slug, company=company, j=j, addon_cats=addon_cats,
        phases=portal.CUSTOMER_PHASES, checklist=_checklist(phase),
        value_steps=value_steps, value_done=value_done, value_total=len(value_steps),
        updates=_sample_updates(phase), referral=_referral_ctx(d, link),
        roof_edu=portal.ROOF_EDU, demo_photos=demo_photos, recent_work=recent_work,
        demo_pm_photo='portal_demo/pm-headshot.jpg',
        demo_logo='',
        demo_invoices=demo_invoices, demo_docs=demo_docs,
        demo_sitecam_url=(d.get("sitecam_url") or os.environ.get("CRM_DEMO_SITECAM_URL") or "").strip(),
        demo_meta=d)


@bp.route("/demo/<slug>/design")
def design(slug):
    d = _get_demo(slug)
    if not d:
        abort(404)
    sysk = (d.get("sample_system") or "shingle").lower()
    if sysk not in portal.ROOF_COLORS:
        sysk = "shingle"
    return render_template("demo_design.html", slug=slug, company=_demo_company(d),
                           colors=portal.ROOF_COLORS, options=portal.ROOF_OPTIONS,
                           start_system=sysk)


@bp.route("/demo/<slug>/design/request", methods=["POST"])
def design_request(slug):
    if not _get_demo(slug):
        abort(404)
    # Demo: acknowledge, but write nothing.
    flash("Nice choices! In the real portal this saves your selections and your "
          "project contact follows up with samples. (This is a demo.)", "ok")
    return redirect(url_for("demo.design", slug=slug))


@bp.route("/demo/<slug>/refer/share", methods=["POST"])
def refer_share(slug):
    if not _get_demo(slug):
        return jsonify({"ok": False}), 404
    st = _REF_STATE.setdefault(slug, {"shares": 0, "signed": 2})
    st["shares"] += 1
    lvl, nxt = portal._share_level(st["shares"])
    return jsonify({"ok": True, "shares": st["shares"], "level": lvl["name"], "icon": lvl["ic"],
                    "leveledUp": lvl["n"] == st["shares"],
                    "next": (nxt["name"] if nxt else None), "nextAt": (nxt["n"] if nxt else None)})


@bp.route("/demo/<slug>/refer/msg", methods=["POST"])
def refer_msg(slug):
    # Demo: accept and discard.
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Contractor-facing SALES landing page (login-free — served at the root of the
# demo domains, e.g. myroofportal.com, via app.py's _demo_host_root hook; also
# reachable at /portal-sales on any host for previewing).
# ---------------------------------------------------------------------------

def _on_demo_host():
    host = (request.host or "").split(":")[0].lower()
    return host in DEMO_HOSTS


@bp.route("/", endpoint="roofer_landing")
def roofer_landing_view():
    """The roofer-facing pitch. This is what myroofportal.com/ serves.

    The cold-email campaign sells licences to roofing contractors, so the front
    door has to speak to them: anyone who types the domain instead of clicking
    the demo link used to land on the acquisition page and be told the product
    was for sale. Buyers now get /acquire, linked from the footer band.
    """
    _log_event("page_view_server", "/")
    return render_template(
        "portal_roofer_landing.html",
        demo_url="/demo/%s" % DEMO_SLUG,
        lead_action=url_for("demo.landing_lead"),
        acquire_url=url_for("demo.acquire"),
        thanks=(request.args.get("thanks") == "1"),
        err=(request.args.get("err") == "1"))


@bp.route("/acquire", endpoint="acquire")
@bp.route("/portal-sales", endpoint="landing")
def landing_view():
    """Domain + software FOR-SALE page (replaced the license-sales landing on
    2026-08-11 — the old page stays reachable at /portal-sales/licensing)."""
    # Server-side view log: survives ad blockers and JS being off, so the funnel
    # denominator is real. The client beacon adds CTA clicks on top.
    _log_event("page_view_server")
    return render_template(
        "portal_sale_landing.html",
        demo_url="/demo/%s" % DEMO_SLUG,
        # Keep form + links relative so the visitor's URL stays myroofportal.com.
        offer_action=url_for("demo.landing_offer"),
        demo_request_action=url_for("demo.demo_request"),
        demo_err=(request.args.get("demoerr") == "1"),
        thanks=(request.args.get("thanks") == "1"),
        ga4_id=(os.environ.get("GA4_MEASUREMENT_ID") or "").strip(),
        err=(request.args.get("err") == "1"))


@bp.route("/portal-sales/offer", methods=["POST"], endpoint="landing_offer")
def landing_offer():
    f = request.form
    name = (f.get("name") or "").strip()[:120]
    email = (f.get("email") or "").strip()[:200]
    offer = (f.get("offer") or "").strip()[:60]
    message = (f.get("message") or "").strip()[:2000]
    base = "/" if _on_demo_host() else url_for("demo.landing")
    if not (name and email and offer):
        return redirect(base + "?err=1#offer")
    score, reasons = _spam_score(name, email, offer, message)
    is_spam = score >= 2
    sent = False
    if not is_spam:
        body = "\n".join([
            "A purchase offer came in on %s" % (request.host or "myroofportal.com"),
            "", "Name:    %s" % name, "Email:   %s" % email,
            "Offer:   %s" % offer, "", "Message:", (message or "(none)"),
            "", "Reply straight to %s — full list at /portal-sales/inbox" % email])
        sent = _notify("MyRoofPortal OFFER: %s - %s" % (offer, name), body)
    row = {
        "created_at": db.now(), "name": name, "email": email,
        "offer": offer, "message": message,
        "source_host": (request.host or "")[:120],
        "notified": 1 if sent else 0}
    try:
        row["spam"] = 1 if is_spam else 0
        row["spam_reason"] = (",".join(reasons))[:200]
        db.insert("portal_offers", row)
    except Exception:
        row.pop("spam", None)
        row.pop("spam_reason", None)
        db.insert("portal_offers", row)
    _log_event("offer_spam" if is_spam else "offer_submit")
    return redirect(base + "?thanks=1#offer")


@bp.route("/portal-sales/demo-request", methods=["POST"], endpoint="demo_request")
def demo_request():
    """Email gate for the live demo: capture the visitor's email, then open the
    demo. Low-friction — email only, no approval step."""
    email = (request.form.get("email") or "").strip()[:200]
    base = "/" if _on_demo_host() else url_for("demo.landing")
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return redirect(base + "?demoerr=1#see-demo")
    sent = _notify("MyRoofPortal demo opened: %s" % email,
                   "\n".join(["%s just opened the live demo on %s."
                              % (email, (request.host or "myroofportal.com")),
                              "", "Full list: /portal-sales/inbox"]))
    db.insert("demo_access_requests", {
        "created": db.now(), "email": email, "slug": DEMO_SLUG,
        "source_host": (request.host or "")[:120],
        "notified": 1 if sent else 0})
    _log_event("demo_gate")
    return redirect(url_for("demo.portal", slug=DEMO_SLUG))


@bp.route("/portal-sales/inbox", endpoint="sales_inbox")
def sales_inbox():
    """Everything the public sales pages captured, in one place. Login-gated
    (the endpoint is deliberately absent from auth.PUBLIC). This page exists so a
    captured lead can never be invisible, even if the mailer is down."""
    def _rows(t, order):
        try:
            return db.all_rows(t, order=order)[:300]
        except Exception:
            return []
    offers = _rows("portal_offers", "id DESC")
    leads = _rows("portal_leads", "id DESC")
    access = _rows("demo_access_requests", "id DESC")
    events = _rows("portal_events", "id DESC")
    counts = {}
    for e in events:
        counts[e.get("kind") or "?"] = counts.get(e.get("kind") or "?", 0) + 1
    from modules import gmail as _gm
    try:
        mail_ok = _gm.smtp_configured()
    except Exception:
        mail_ok = False
    return render_template("portal_sales_inbox.html",
                           offers=offers, leads=leads, access=access,
                           events=events[:100], counts=counts,
                           mail_ok=mail_ok, notify_to=_notify_to())


@bp.route("/portal-sales/ev", methods=["POST"], endpoint="track_event")
def track_event():
    """First-party funnel beacon. Public by design (anonymous visitors) and
    records nothing a visitor did not already send in the request."""
    kind = (request.form.get("k") or request.args.get("k") or "").strip()[:40]
    if kind:
        _log_event(kind, request.form.get("p") or "")
    return ("", 204)


@bp.route("/robots.txt", endpoint="robots")
def robots():
    """Let the sale page be indexed; keep the internal surfaces out of search."""
    host = (request.host or "myroofportal.com").split(":")[0]
    body = "\n".join([
        "User-agent: *",
        "Allow: /$",
        "Allow: /demo/",
        "Disallow: /portal-sales/inbox",
        "Disallow: /demos",
        "Disallow: /jobs",
        "Disallow: /settings",
        "Disallow: /login",
        "Sitemap: https://%s/sitemap.xml" % host,
        ""])
    return Response(body, mimetype="text/plain")


@bp.route("/sitemap.xml", endpoint="sitemap")
def sitemap():
    host = (request.host or "myroofportal.com").split(":")[0]
    urls = ["https://%s/" % host,
            "https://%s/demo/%s" % (host, DEMO_SLUG),
            "https://%s/portal-sales/licensing" % host]
    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in urls:
        parts.append("  <url><loc>%s</loc><changefreq>weekly</changefreq></url>" % u)
    parts.append("</urlset>")
    return Response("\n".join(parts), mimetype="application/xml")


@bp.route("/portal-sales/licensing", endpoint="landing_licensing")
def landing_licensing_view():
    """The previous license-sales landing page, kept reachable for reference."""
    return render_template(
        "portal_landing.html",
        demo_url="/demo/%s" % DEMO_SLUG,
        lead_action=url_for("demo.landing_lead"),
        thanks=(request.args.get("thanks") == "1"),
        err=(request.args.get("err") == "1"))


@bp.route("/portal-sales/lead", methods=["POST"], endpoint="landing_lead")
def landing_lead():
    f = request.form
    name = (f.get("name") or "").strip()[:120]
    company = (f.get("company") or "").strip()[:160]
    email = (f.get("email") or "").strip()[:200]
    phone = (f.get("phone") or "").strip()[:40]
    # The lead form is on the roofer front page; /licensing still carries a copy.
    base = "/" if _on_demo_host() else url_for("demo.landing_licensing")
    # Require a name plus at least one way to reach them.
    if not name or not (email or phone):
        return redirect(base + "?err=1#get-started")
    sent = _notify("MyRoofPortal lead: %s (%s)" % (name, company or "no company"),
                   "\n".join(["New contractor lead on %s"
                              % (request.host or "myroofportal.com"), "",
                              "Name:    %s" % name, "Company: %s" % (company or "-"),
                              "Email:   %s" % (email or "-"),
                              "Phone:   %s" % (phone or "-"), "",
                              "Full list: /portal-sales/inbox"]))
    db.insert("portal_leads", {
        "created": db.now(), "name": name, "company": company,
        "email": email, "phone": phone,
        "source_host": (request.host or "")[:120],
        "notified": 1 if sent else 0})
    _log_event("lead_submit")
    return redirect(base + "?thanks=1#get-started")
