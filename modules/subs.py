# -*- coding: utf-8 -*-
"""Subcontractors (internal admin directory).

A simple roster of subcontractors / crews: trade, contact info, COI expiry,
W-9 on file, active/inactive status. The list page flags Certificate-of-
Insurance dates that are expired or expiring soon so the office never lets a
crew on a roof with lapsed coverage.

Table is created here (CREATE TABLE IF NOT EXISTS) to match the commissions.py /
acculynx_sync.py convention and avoid touching the shared SCHEMA string.
"""
from datetime import datetime, timedelta

from flask import Blueprint, render_template, request, redirect, url_for, flash

import db
import theme

bp = Blueprint("subs", __name__, url_prefix="/subs")

TRADES = ["Roofing", "Gutters", "Framing", "Solar", "Painting", "Demolition",
          "Cleanup", "Other"]
STATUSES = ["active", "inactive"]
COI_WARN_DAYS = 30

db.execute("""CREATE TABLE IF NOT EXISTS subcontractors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT, trade TEXT, phone TEXT, email TEXT, address TEXT,
    coi_expiry TEXT, w9_on_file INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active', notes TEXT,
    created TEXT, department TEXT)""")


def _coi_state(coi_expiry):
    """Return one of 'ok' / 'expiring' / 'expired' / '' for a COI date string."""
    if not coi_expiry:
        return ""
    try:
        exp = datetime.strptime(coi_expiry[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return ""
    today = datetime.now().date()
    if exp < today:
        return "expired"
    if exp <= today + timedelta(days=COI_WARN_DAYS):
        return "expiring"
    return "ok"


@bp.route("/")
def index():
    dept = theme.current_department()
    rows = db.all_rows("subcontractors", "department=?", (dept,), "status, name")

    status_f = request.args.get("status")
    trade_f  = request.args.get("trade")
    q        = (request.args.get("q") or "").strip().lower()
    if status_f:
        rows = [s for s in rows if s["status"] == status_f]
    if trade_f:
        rows = [s for s in rows if (s.get("trade") or "") == trade_f]
    if q:
        rows = [s for s in rows if q in (
            (s.get("name") or "") + " " + (s.get("trade") or "") + " " +
            (s.get("phone") or "") + " " + (s.get("email") or "") + " " +
            (s.get("address") or "") + " " + (s.get("notes") or "")).lower()]

    for s in rows:
        s["_coi"] = _coi_state(s.get("coi_expiry"))

    return render_template("subs.html", rows=rows, trades=TRADES, statuses=STATUSES,
                           status_f=status_f, trade_f=trade_f, q=q,
                           coi_warn_days=COI_WARN_DAYS)


@bp.route("/new", methods=["POST"])
def new():
    name = (request.form.get("name") or "").strip()
    if not name:
        flash("A name is required.", "err")
        return redirect(url_for("subs.index"))
    db.insert("subcontractors", {
        "name": name,
        "trade": request.form.get("trade") or "",
        "phone": request.form.get("phone") or "",
        "email": request.form.get("email") or "",
        "address": request.form.get("address") or "",
        "coi_expiry": request.form.get("coi_expiry") or "",
        "w9_on_file": 1 if request.form.get("w9_on_file") else 0,
        "status": request.form.get("status") or "active",
        "notes": request.form.get("notes") or "",
        "created": db.now(),
        "department": theme.current_department(),
    })
    flash("Subcontractor added.", "ok")
    return redirect(url_for("subs.index"))


@bp.route("/<int:sid>/save", methods=["POST"])
def save(sid):
    s = db.get("subcontractors", sid)
    if not s:
        return redirect(url_for("subs.index"))
    db.update("subcontractors", sid,
              name=(request.form.get("name") or s["name"]).strip(),
              trade=request.form.get("trade", s.get("trade") or ""),
              phone=request.form.get("phone", s.get("phone") or ""),
              email=request.form.get("email", s.get("email") or ""),
              address=request.form.get("address", s.get("address") or ""),
              coi_expiry=request.form.get("coi_expiry", s.get("coi_expiry") or ""),
              w9_on_file=1 if request.form.get("w9_on_file") else 0,
              status=request.form.get("status", s.get("status") or "active"),
              notes=request.form.get("notes", s.get("notes") or ""))
    flash("Subcontractor updated.", "ok")
    return redirect(url_for("subs.detail", sid=sid))


@bp.route("/<int:sid>")
def detail(sid):
    s = db.get("subcontractors", sid)
    if not s or s.get("department") not in (None, theme.current_department()):
        flash("Subcontractor not found.", "err")
        return redirect(url_for("subs.index"))
    s["_coi"] = _coi_state(s.get("coi_expiry"))
    return render_template("subs.html", rows=None, sub=s, trades=TRADES,
                           statuses=STATUSES, coi_warn_days=COI_WARN_DAYS)
