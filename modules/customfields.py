# -*- coding: utf-8 -*-
"""Phase 5 — Config + intelligence: Custom Fields, editable Lead Sources /
Contact Types, and Lead Rank (1–4).

Replaces the earlier stub. Keeps the `/customfields` prefix and the existing
`custom_fields.field_type` column. Tables/columns are ensured here (the
auth.py/acculynx_sync.py convention). Custom fields render on lead/job detail via
Jinja globals + a save endpoint — no edits to the lead/job view handlers.
Config routes are admin-only (checked inline); value-editing is open to any user.
"""
from flask import (Blueprint, render_template, request, redirect, url_for, flash, jsonify)

import db
import constants
from modules.auth import current_user

bp = Blueprint("customfields", __name__, url_prefix="/customfields")

FIELD_TYPES = ["text", "number", "date", "select", "checkbox"]
DEFAULT_CONTACT_TYPES = ["Customer", "General Contact", "Gutters", "Material Supplier",
                         "Public Adjuster", "Subcontractor", "Real Estate Agent", "Insurance"]

db.execute("""CREATE TABLE IF NOT EXISTS custom_fields (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created TEXT,
    entity TEXT, label TEXT, field_type TEXT DEFAULT 'text', sort INTEGER DEFAULT 0)""")
db._ensure_column("custom_fields", "options", "TEXT")
db.execute("""CREATE TABLE IF NOT EXISTS custom_values (
    id INTEGER PRIMARY KEY AUTOINCREMENT, field_id INTEGER,
    entity_type TEXT, entity_id INTEGER, value TEXT)""")
db.execute("CREATE TABLE IF NOT EXISTS lead_sources (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, sort INTEGER DEFAULT 0)")
db.execute("CREATE TABLE IF NOT EXISTS contact_types (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, sort INTEGER DEFAULT 0)")
db._ensure_column("leads", "rank", "INTEGER DEFAULT 0")


def _seed():
    if not db.all_rows("lead_sources"):
        for i, s in enumerate(constants.LEAD_SOURCES):
            db.insert("lead_sources", {"name": s, "sort": i})
    if not db.all_rows("contact_types"):
        for i, s in enumerate(DEFAULT_CONTACT_TYPES):
            db.insert("contact_types", {"name": s, "sort": i})


_seed()


def _is_admin():
    u = current_user()
    return bool(u and u.get("role") == "admin")


# ---- helpers exposed to templates -----------------------------------------

def fields_for(entity):
    return db.all_rows("custom_fields", "entity=?", (entity,), "sort, id")


def values_map(entity_type, entity_id):
    rows = db.all_rows("custom_values", "entity_type=? AND entity_id=?", (entity_type, entity_id))
    return {r["field_id"]: r["value"] for r in rows}


def lead_source_names():
    return [r["name"] for r in db.all_rows("lead_sources", order="sort, name")]


def contact_type_names():
    return [r["name"] for r in db.all_rows("contact_types", order="sort, name")]


@bp.app_context_processor
def _inject():
    return {"custom_fields": fields_for, "custom_values": values_map,
            "db_lead_sources": lead_source_names, "db_contact_types": contact_type_names,
            "field_opts": lambda f: [o.strip() for o in (f.get("options") or "").split(",") if o.strip()]}


# ---- value editing + lead rank (any logged-in user) -----------------------

@bp.route("/values/<entity_type>/<int:entity_id>", methods=["POST"])
def save_values(entity_type, entity_id):
    for f in fields_for(entity_type):
        val = request.form.get("cf_%d" % f["id"], "")
        existing = db.all_rows("custom_values", "field_id=? AND entity_type=? AND entity_id=?",
                               (f["id"], entity_type, entity_id))
        if existing:
            db.update("custom_values", existing[0]["id"], value=val)
        else:
            db.insert("custom_values", {"field_id": f["id"], "entity_type": entity_type,
                                        "entity_id": entity_id, "value": val})
    flash("Custom fields saved.", "ok")
    return redirect(request.referrer or url_for("dashboard.home"))


@bp.route("/rank/<int:lead_id>", methods=["POST"])
def set_rank(lead_id):
    try:
        rank = max(0, min(4, int(request.form.get("rank") or 0)))
    except Exception:
        rank = 0
    db.update("leads", lead_id, rank=rank)
    if request.form.get("ajax"):
        return jsonify({"ok": True, "rank": rank})
    return redirect(request.referrer or url_for("leads.list_view"))


# ---- admin config (admin-only) --------------------------------------------

@bp.route("/")
def index():
    if not _is_admin():
        flash("Admins only.", "error")
        return redirect(url_for("dashboard.home"))
    return render_template("config.html",
                           lead_fields=fields_for("lead"), job_fields=fields_for("job"),
                           contact_fields=fields_for("contact"),
                           sources=db.all_rows("lead_sources", order="sort, name"),
                           ctypes=db.all_rows("contact_types", order="sort, name"),
                           field_types=FIELD_TYPES)


@bp.route("/add", methods=["POST"])
def add():
    if not _is_admin():
        return redirect(url_for("dashboard.home"))
    if request.form.get("label", "").strip():
        db.insert("custom_fields", {"created": db.now(), "entity": request.form.get("entity", "lead"),
                                    "label": request.form.get("label").strip(),
                                    "field_type": request.form.get("field_type", "text"),
                                    "options": request.form.get("options", "").strip()})
        flash("Custom field added.", "ok")
    return redirect(url_for("customfields.index"))


@bp.route("/<int:fid>/delete", methods=["POST"])
def delete(fid):
    if not _is_admin():
        return redirect(url_for("dashboard.home"))
    db.delete("custom_fields", fid)
    db.execute("DELETE FROM custom_values WHERE field_id=?", (fid,))
    flash("Removed.", "ok")
    return redirect(url_for("customfields.index"))


@bp.route("/source/new", methods=["POST"])
def source_new():
    if _is_admin() and request.form.get("name", "").strip():
        db.insert("lead_sources", {"name": request.form.get("name").strip(), "sort": 99})
    return redirect(url_for("customfields.index"))


@bp.route("/source/<int:sid>/delete", methods=["POST"])
def source_delete(sid):
    if _is_admin():
        db.delete("lead_sources", sid)
    return redirect(url_for("customfields.index"))


@bp.route("/ctype/new", methods=["POST"])
def ctype_new():
    if _is_admin() and request.form.get("name", "").strip():
        db.insert("contact_types", {"name": request.form.get("name").strip(), "sort": 99})
    return redirect(url_for("customfields.index"))


@bp.route("/ctype/<int:cid>/delete", methods=["POST"])
def ctype_delete(cid):
    if _is_admin():
        db.delete("contact_types", cid)
    return redirect(url_for("customfields.index"))
