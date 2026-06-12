# -*- coding: utf-8 -*-
"""Template Manager — DB-backed, editable estimate templates (AccuLynx-style)."""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify

import db
import constants
import json

bp = Blueprint("templates", __name__, url_prefix="/templates")


@bp.route("/")
def index():
    rows = db.all_rows("templates", order="name")
    for t in rows:
        t["_lines"] = db.load_json(t.get("lines"), [])
    return render_template("templates.html", templates=rows)


@bp.route("/refresh-builtins", methods=["POST"])
def refresh_builtins():
    """Re-sync the built-in estimate templates from constants.py (AccuLynx-mirror
    line items, costs + quantity formulas). Only touches is_builtin=1 rows; custom
    templates are never modified. Run after the code templates change."""
    updated, inserted = db.sync_builtin_templates()
    flash("Built-in templates refreshed from the latest spec: %d updated, %d added."
          % (updated, inserted), "ok")
    return redirect(url_for("templates.index"))


@bp.route("/new", methods=["POST"])
def new():
    tid = db.insert("templates", {"tkey": "custom_%d" % (db.all_rows("templates", order="id DESC")[0]["id"] + 1 if db.all_rows("templates") else 1),
                                  "name": request.form.get("name", "New Template"),
                                  "work_type": request.form.get("work_type", ""),
                                  "scope_text": "", "lines": "[]", "is_builtin": 0})
    return redirect(url_for("templates.edit", tid=tid))


@bp.route("/<int:tid>")
def edit(tid):
    t = db.get("templates", tid)
    if not t:
        return redirect(url_for("templates.index"))
    t["_lines"] = db.load_json(t.get("lines"), [])
    return render_template("template_edit.html", t=t, work_types=constants.WORK_TYPES)


@bp.route("/<int:tid>/save", methods=["POST"])
def save(tid):
    data = request.get_json(silent=True) or {}
    db.update("templates", tid,
              name=data.get("name", ""), work_type=data.get("work_type", ""),
              scope_text=data.get("scope_text", ""),
              lines=db.dump_json(data.get("lines", [])))
    return jsonify({"ok": True})


@bp.route("/<int:tid>/delete", methods=["POST"])
def delete(tid):
    db.delete("templates", tid)
    flash("Template deleted.", "ok")
    return redirect(url_for("templates.index"))


@bp.route("/sync-acculynx", methods=["POST"])
def sync_acculynx():
    """Pull AccuLynx estimate templates via API and import any that aren't already in the DB.
    AccuLynx v2 API endpoint: GET /estimate-templates
    Each AccuLynx template is mapped: name, work_type, lineItems → lines.
    """
    try:
        from modules.acculynx_sync import _api_get
    except ImportError:
        flash("acculynx_sync not available.", "error")
        return redirect(url_for("templates.index"))

    company = db.get_company()
    api_key = (company.get("acculynx_api_key") or "").strip()
    api_base = (company.get("acculynx_api_base") or "https://api.acculynx.com/api/v2").strip()

    if not api_key:
        flash("AccuLynx API key not configured — go to Settings → Integrations.", "warn")
        return redirect(url_for("templates.index"))

    # Try known AccuLynx template endpoints in order.
    raw = None
    for path in ("/estimate-templates", "/estimateTemplates", "/Templates"):
        try:
            raw = _api_get(api_base, path, api_key)
            break
        except Exception:
            continue

    if raw is None:
        flash(
            "AccuLynx API did not return templates (endpoint not found). "
            "Template line items can be entered manually or via the built-in spec (↻ Refresh built-ins).",
            "warn"
        )
        return redirect(url_for("templates.index"))

    items = raw if isinstance(raw, list) else raw.get("items", raw.get("value", []))
    if not items:
        flash("AccuLynx returned 0 templates.", "warn")
        return redirect(url_for("templates.index"))

    imported = 0
    for alx in items:
        name = (alx.get("name") or alx.get("templateName") or "").strip()
        if not name:
            continue
        work_type = (alx.get("workType") or alx.get("work_type") or "").strip()
        # Map AccuLynx line items — field names vary by API version.
        raw_lines = alx.get("lineItems") or alx.get("lines") or alx.get("items") or []
        lines = []
        for li in raw_lines:
            desc = (li.get("description") or li.get("name") or li.get("desc") or "").strip()
            if not desc:
                continue
            lines.append({
                "description": desc,
                "unit": (li.get("unitOfMeasure") or li.get("unit") or "EA").strip(),
                "qty": float(li.get("quantity") or li.get("qty") or 0),
                "cost": float(li.get("unitPrice") or li.get("price") or li.get("cost") or 0),
            })
        # Only insert if no template with that name already exists.
        existing = [t for t in db.all_rows("templates") if t.get("name", "").strip() == name]
        if existing:
            continue
        db.insert("templates", {
            "tkey": "alx_" + name.lower().replace(" ", "_")[:30],
            "name": name,
            "work_type": work_type,
            "scope_text": "",
            "lines": db.dump_json(lines),
            "is_builtin": 0,
        })
        imported += 1

    flash("Imported %d AccuLynx template(s). %d already existed (skipped)." % (imported, len(items) - imported), "ok")
    return redirect(url_for("templates.index"))
