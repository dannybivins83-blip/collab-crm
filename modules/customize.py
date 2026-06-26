# -*- coding: utf-8 -*-
"""Customize — white-label buyer types plain-English changes to their own CRM.

Safe changes (company profile text, theme colors, and UI label renames) are
applied instantly. Anything else is logged as a change request for review.
Nothing here can alter code or structure — only the brandable config knobs.
"""
import re
import json

from flask import Blueprint, render_template, request, redirect, url_for, flash

import db

bp = Blueprint("customize", __name__, url_prefix="/customize")

# phrase (in the request) -> company_settings column
FIELD_SYNONYMS = [
    (("legal name",), "legal_name"),
    (("company name", "business name", "company"), "name"),
    (("tagline", "slogan", "motto"), "tagline"),
    (("license number", "license #", "license", "cert", "certification"), "license"),
    (("qualifier", "cert holder"), "qualifier"),
    (("phone number", "phone", "telephone"), "phone"),
    (("email address", "email"), "email"),
    (("website", "url", "web site"), "website"),
    (("street address", "address"), "address"),
    (("city",), "city"),
    (("state",), "state"),
    (("zip code", "zip", "postal"), "zip"),
    (("default county", "county"), "default_county"),
    (("proposal terms", "terms", "payment terms"), "terms"),
]

# color target phrase -> company_settings color column
COLOR_TARGETS = [
    (("masthead", "top bar", "topbar", "header bar", "navy bar", "banner"), "color_masthead"),
    (("primary", "nav", "navigation", "link", "main", "button"), "color_primary"),
    (("accent", "success", "secondary"), "color_accent"),
    (("warn", "warning", "caution"), "color_warn"),
    (("danger", "error", "alert"), "color_danger"),
]

COLOR_NAMES = {
    "navy": "#24476C", "dark blue": "#1f3e60", "blue": "#4680BF", "royal blue": "#2f6df6",
    "light blue": "#29ABE2", "sky blue": "#29ABE2", "teal": "#0f9e82", "green": "#8CC63F",
    "forest green": "#2e7d32", "lime": "#8CC63F", "red": "#E25050", "crimson": "#c0152f",
    "maroon": "#7a1f1f", "orange": "#F78300", "gold": "#F2C000", "yellow": "#F2C000",
    "purple": "#7d3cff", "violet": "#7d3cff", "pink": "#e0559a", "black": "#1d2a44",
    "white": "#ffffff", "gray": "#64748b", "grey": "#64748b", "slate": "#475569",
    "brown": "#6d4c2f",
}


def _color_value(text):
    text = text.strip().strip("\"'.").lower()
    hexm = re.search(r"#?([0-9a-f]{6}|[0-9a-f]{3})\b", text)
    if hexm and (text.startswith("#") or re.fullmatch(r"#?[0-9a-f]{3,6}", text)):
        return "#" + hexm.group(1)
    for name in sorted(COLOR_NAMES, key=len, reverse=True):
        if name in text:
            return COLOR_NAMES[name]
    return None


def _value_after(clause):
    """Grab the value after to / = / : / as."""
    m = re.search(r"\b(?:to|=|:|as|into)\b\s*(.+)$", clause, re.I)
    return (m.group(1).strip().strip("\"'") if m else "").strip()


def parse_and_apply(text, who="admin"):
    """Returns (applied[list of str], queued[list of str])."""
    applied, queued = [], []
    company_updates = {}
    labels = db.load_json(db.get_company().get("labels"), {})
    label_changed = False

    clauses = [c.strip() for c in re.split(r"[\n;]+", text) if c.strip()]
    for clause in clauses:
        low = clause.lower()

        # 1) Color change
        if re.search(r"colou?r|theme", low):
            target = "color_primary"
            for phrases, col in COLOR_TARGETS:
                if any(p in low for p in phrases):
                    target = col
                    break
            val = _color_value(_value_after(clause) or low)
            if val:
                company_updates[target] = val
                applied.append("%s → %s" % (target.replace("color_", "").replace("_", " ").title() + " color", val))
                continue

        # 2) Rename / relabel  (rename X to Y / call X Y / label X as Y)
        rn = re.search(r"\b(?:rename|relabel|label|call)\b\s+(?:the\s+)?[\"']?(.+?)[\"']?\s+(?:to|as|->|→)\s+[\"']?(.+?)[\"']?$", clause, re.I)
        if rn and not any(s in low for grp, _f in FIELD_SYNONYMS for s in grp):
            old, new = rn.group(1).strip(), rn.group(2).strip().strip("\"'")
            labels[old] = new
            label_changed = True
            applied.append('Renamed "%s" → "%s"' % (old, new))
            continue

        # 3) Named company field
        matched = False
        for phrases, field in FIELD_SYNONYMS:
            if any(re.search(r"\b" + re.escape(p) + r"\b", low) for p in phrases):
                val = _value_after(clause)
                if val:
                    company_updates[field] = val
                    applied.append("%s → %s" % (field.replace("_", " ").title(), val[:60]))
                    matched = True
                break
        if matched:
            continue

        # 4) Unrecognized → queue for review
        queued.append(clause)

    if company_updates:
        db.save_company(company_updates)
    if label_changed:
        db.save_company({"labels": json.dumps(labels)})

    status = "applied" if applied and not queued else ("partial" if applied else "queued")
    db.insert("change_requests", {"created": db.now(), "requested_by": who,
                                  "raw_text": text, "status": status,
                                  "result": "; ".join(applied) + (" | QUEUED: " + "; ".join(queued) if queued else "")})
    return applied, queued


@bp.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        text = request.form.get("changes", "").strip()
        if not text:
            flash("Type the changes you want first.", "error")
            return redirect(url_for("customize.index"))
        who = "admin"
        try:
            from flask import g
            who = getattr(g, "user", {}).get("name", "admin") if getattr(g, "user", None) else "admin"
        except Exception:
            pass
        applied, queued = parse_and_apply(text, who)
        if applied:
            flash("Applied %d change%s: %s" % (len(applied), "" if len(applied) == 1 else "s", "; ".join(applied)), "ok")
        if queued:
            flash("Logged %d request%s for review (not auto-applied): %s" % (
                len(queued), "" if len(queued) == 1 else "s", "; ".join(queued)), "info")
        return redirect(url_for("customize.index"))

    company = db.get_company()
    labels = db.load_json(company.get("labels"), {})
    return render_template("customize.html", company=company, labels=labels,
                           requests=db.all_rows("change_requests", "", (), "id DESC", 20))


@bp.route("/label/<path:old>/delete", methods=["POST"])
def del_label(old):
    company = db.get_company()
    labels = db.load_json(company.get("labels"), {})
    labels.pop(old, None)
    db.save_company({"labels": json.dumps(labels)})
    flash('Removed rename for "%s".' % old, "ok")
    return redirect(url_for("customize.index"))


@bp.route("/request/<int:req_id>/apply", methods=["POST"])
def mark_applied(req_id):
    db.update("change_requests", req_id, status="applied")
    flash("Marked applied.", "ok")
    return redirect(url_for("customize.index"))
