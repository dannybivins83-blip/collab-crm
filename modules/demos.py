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
db._COLCACHE.clear()

# Canonical product-demo brand — a generic, believable roofing company so the public
# demo shows a real-looking roofer, not the operating tenant's name or the product name.
# Ensured on import: creates the `roof-portal` demo if missing, and upgrades the earlier
# auto-seeded placeholder ("Roof Portal") to this brand. Only ever touches the auto-seeded
# row (created_by='seed') — never a manually-created demo.
_DEMO_DEFAULT = {
    "company_name": "KLR Roofing", "tagline": "Roofs done right — on time, every time.",
    "phone": "(555) 018-2440", "website": "https://klrroofing.com",
    "color_masthead": "#15201A", "color_primary": "#37B34A", "color_accent": "#2A8F3A",
    "sample_system": "shingle",
}


def _ensure_default_demo():
    try:
        rows = db.all_rows("demos", "slug=?", ("roof-portal",))
        if not rows:
            db.insert("demos", dict(_DEMO_DEFAULT, created=db.now(), slug="roof-portal",
                                    logo_url="", created_by="seed"))
        elif rows[0].get("created_by") == "seed" and (rows[0].get("company_name") or "") in ("", "Roof Portal", "Summit Roofing Co."):
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
    name = (os.environ.get("CRM_DEMO_SEED_NAME") or "KLR Roofing").strip()
    brand = {"company_name": name,
             "tagline": "Roofs done right — on time, every time.",
             "website": "https://klrroofing.com", "phone": "(555) 018-2440",
             "color_masthead": "#15201A", "color_primary": "#37B34A",
             "color_accent": "#2A8F3A", "sample_system": "shingle"}
    try:
        rows = db.all_rows("demos", "slug=?", (slug,))
        if rows:
            r0 = rows[0]
            stale_name = (r0.get("company_name") or "") in ("", "Roof Portal", "Your Roofing Co.", "Summit Roofing Co.")
            stale_color = r0.get("created_by") == "seed" and (r0.get("color_primary") or "") != brand["color_primary"]
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
    return render_template("demo_generator.html", demos=demos,
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
# Public demo portal (login-free — this is the shareable link)
# ---------------------------------------------------------------------------

# Endpoint is set to "portal" (templates/url_for use `demo.portal`); the function
# keeps a distinct name so it doesn't shadow the imported `portal` module.
@bp.route("/demo/<slug>", endpoint="portal")
def portal_view(slug):
    d = _get_demo(slug)
    if not d:
        abort(404)
    company = _demo_company(d)
    j = _sample_job(d)
    phase = j["_phase"]
    value_steps, value_done = _value_steps(phase)
    link = url_for("demo.portal", slug=slug, _external=True)
    # Synthetic showcase data so the demo demonstrates the SiteCam feed, QuickBooks
    # billing, the document center + e-sign, and Roof School — no real records touched.
    demo_photos = [
        {"cap": "Tear-off complete", "sub": "Old roof removed, deck exposed", "hex": "#6b7280",
         "img": "portal_demo/job-1.jpg", "at": "7:18 AM"},
        {"cap": "Deck re-nail", "sub": "Every sheet re-nailed to code", "hex": "#8a6e4b",
         "img": "portal_demo/job-2.jpg", "at": "9:03 AM"},
        {"cap": "Peel-&-stick underlayment", "sub": "Secondary water barrier down", "hex": "#1f6f8b",
         "img": "portal_demo/job-3.jpg", "at": "10:47 AM"},
        {"cap": "Shingles going on", "sub": "6-nail high-wind pattern", "hex": "#374151",
         "img": "portal_demo/job-4.jpg", "at": "12:15 PM"},
        {"cap": "Ridge vent + caps", "sub": "Attic ventilation installed", "hex": "#4b5563"},
        {"cap": "Final walkthrough", "sub": "Magnet nail-sweep done", "hex": "#2c4733"},
    ]
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
    addon_cats = [
        {"key": "gutters", "name": "Gutters & Drainage", "tag": "Protect your new roof", "icon": "droplet", "items": [
            {"name": "Seamless Aluminum Gutters", "price": "$1,850", "unit": "whole home", "blurb": "5\" K-style, color-matched to your roof", "badge": "Popular"},
            {"name": "Leaf-Guard Gutter Protection", "price": "$690", "unit": "", "blurb": "Never clean your gutters again", "badge": ""},
            {"name": "Downspout Extensions", "price": "$120", "unit": "", "blurb": "Move water away from your foundation", "badge": ""},
            {"name": "Copper Half-Round Upgrade", "price": "$4,200", "unit": "", "blurb": "Premium coastal look", "badge": "Premium"},
        ]},
        {"key": "maint", "name": "Maintenance Plans", "tag": "Keep your warranty valid", "icon": "shield", "items": [
            {"name": "Annual Roof Checkup", "price": "$199", "unit": "/yr", "blurb": "Yearly inspection + tune-up", "badge": "Best value"},
            {"name": "Premium Care Plan", "price": "$349", "unit": "/yr", "blurb": "2 visits/yr + priority scheduling", "badge": "Popular"},
            {"name": "Gutter Cleaning (2x/yr)", "price": "$149", "unit": "/yr", "blurb": "Spring & fall clean-outs", "badge": ""},
            {"name": "Storm Response Membership", "price": "$99", "unit": "/yr", "blurb": "Priority post-storm inspection", "badge": ""},
        ]},
        {"key": "inspect", "name": "Inspections", "tag": "Save on insurance", "icon": "search", "items": [
            {"name": "Wind Mitigation Inspection", "price": "$125", "unit": "", "blurb": "Can lower your insurance premium", "badge": "Insurance discount"},
            {"name": "Drone Roof Inspection", "price": "$99", "unit": "", "blurb": "HD aerial photos + report", "badge": ""},
            {"name": "Post-Storm Damage Inspection", "price": "Free", "unit": "", "blurb": "After any named storm", "badge": "Free"},
            {"name": "4-Point Home Inspection", "price": "$150", "unit": "", "blurb": "For insurance or closing", "badge": ""},
        ]},
        {"key": "upgrades", "name": "Roof Upgrades", "tag": "Add before install day", "icon": "star", "items": [
            {"name": "Ridge Vent + Attic Ventilation", "price": "$650", "unit": "", "blurb": "Cooler attic, longer roof life", "badge": "Recommended"},
            {"name": "Hurricane-Rated Skylights", "price": "$1,200", "unit": "each", "blurb": "Natural light, impact-rated", "badge": ""},
            {"name": "Peel-&-Stick Underlayment Upgrade", "price": "$900", "unit": "", "blurb": "Max secondary water barrier", "badge": "Popular"},
            {"name": "Extended 25-Yr Workmanship Warranty", "price": "$450", "unit": "", "blurb": "Double your coverage", "badge": ""},
        ]},
    ]
    return render_template(
        "demo_portal.html", slug=slug, company=company, j=j, addon_cats=addon_cats,
        phases=portal.CUSTOMER_PHASES, checklist=_checklist(phase),
        value_steps=value_steps, value_done=value_done, value_total=len(value_steps),
        updates=_sample_updates(phase), referral=_referral_ctx(d, link),
        roof_edu=portal.ROOF_EDU, demo_photos=demo_photos,
        demo_pm_photo='portal_demo/pm-headshot.jpg',
        demo_logo='portal_sale/brand/summit-roofing-demo-logo.svg',
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


@bp.route("/portal-sales", endpoint="landing")
def landing_view():
    """Domain + software FOR-SALE page (replaced the license-sales landing on
    2026-08-11 — the old page stays reachable at /portal-sales/licensing)."""
    return render_template(
        "portal_sale_landing.html",
        demo_url="/demo/%s" % DEMO_SLUG,
        # Keep form + links relative so the visitor's URL stays myroofportal.com.
        offer_action=url_for("demo.landing_offer"),
        thanks=(request.args.get("thanks") == "1"),
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
    db.insert("portal_offers", {
        "created_at": db.now(), "name": name, "email": email,
        "offer": offer, "message": message,
        "source_host": (request.host or "")[:120]})
    return redirect(base + "?thanks=1#offer")


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
    # The license page (the only one carrying this form) lives at /licensing now.
    base = url_for("demo.landing_licensing")
    # Require a name plus at least one way to reach them.
    if not name or not (email or phone):
        return redirect(base + "?err=1#get-started")
    db.insert("portal_leads", {
        "created": db.now(), "name": name, "company": company,
        "email": email, "phone": phone,
        "source_host": (request.host or "")[:120]})
    return redirect(base + "?thanks=1#get-started")
