# -*- coding: utf-8 -*-
"""Template Manager — DB-backed, editable estimate templates (AccuLynx-style)."""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify

import db
import constants

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
