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
db._COLCACHE.clear()

# Ephemeral, process-local referral game state per demo slug (resets on restart —
# this is a throwaway sales demo, never persisted).
_REF_STATE = {}

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
    return render_template(
        "demo_portal.html", slug=slug, company=company, j=j,
        phases=portal.CUSTOMER_PHASES, checklist=_checklist(phase),
        value_steps=value_steps, value_done=value_done, value_total=len(value_steps),
        updates=_sample_updates(phase), referral=_referral_ctx(d, link),
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
