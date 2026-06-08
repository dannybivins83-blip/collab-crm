# -*- coding: utf-8 -*-
"""Real per-user authentication — login/logout, password hashing, role gating.

Self-contained: adds its own `password_hash` column, seeds default passwords,
registers a before-request guard, and exposes `current_user` to templates.
"""
from flask import (Blueprint, render_template, request, redirect, url_for,
                   session, flash)
from werkzeug.security import generate_password_hash, check_password_hash

import os
import db

bp = Blueprint("auth", __name__)

# Seed/default password. Override in production via the CRM_DEFAULT_PASSWORD env var
# so the live (real-data) instance never uses the publicly-known default.
DEFAULT_PASSWORD = os.environ.get("CRM_DEFAULT_PASSWORD", "seabreeze2026")
# Endpoints reachable without being logged in.
PUBLIC = {"auth.login", "static", "uploads", "favicon", "leads.import_leads", "sync.browser_import", "sync.cron",
          "portal.home", "portal.message", "portal.sign", "portal.pay",
          "portal.upload_doc", "portal.upload_photo"}
# Endpoints only admins may hit (prefix match on the path).
ADMIN_ONLY_PATHS = ("/settings", "/orders/vendors", "/workflow")


def _ensure_schema():
    try:
        db.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
    except Exception:
        pass
    db._COLCACHE.clear()
    for u in db.all_rows("users"):
        if not u.get("password_hash"):
            db.update("users", u["id"], password_hash=generate_password_hash(DEFAULT_PASSWORD))


def current_user():
    uid = session.get("user_id")
    return db.get("users", uid) if uid else None


@bp.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id"):
        return redirect(url_for("dashboard.home"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        pw = request.form.get("password", "")
        user = next((u for u in db.all_rows("users")
                     if (u.get("email") or "").lower() == email and u.get("active", 1)), None)
        if user and user.get("password_hash") and check_password_hash(user["password_hash"], pw):
            session["user_id"] = user["id"]
            session["user_name"] = user["name"]
            session["user_role"] = user.get("role", "sales")
            nxt = request.args.get("next")
            return redirect(nxt if nxt and nxt.startswith("/") else url_for("dashboard.home"))
        flash("Invalid email or password.", "error")
    return render_template("login.html")


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/account", methods=["GET", "POST"])
def account():
    u = current_user()
    if not u:
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        cur = request.form.get("current", "")
        new = request.form.get("new", "")
        if not check_password_hash(u.get("password_hash") or "", cur):
            flash("Current password is incorrect.", "error")
        elif len(new) < 6:
            flash("New password must be at least 6 characters.", "error")
        else:
            db.update("users", u["id"], password_hash=generate_password_hash(new))
            flash("Password updated.", "ok")
        return redirect(url_for("auth.account"))
    return render_template("account.html", u=u)


def set_password(user_id, password):
    db.update("users", user_id, password_hash=generate_password_hash(password or DEFAULT_PASSWORD))


def init_auth(app):
    _ensure_schema()
    app.register_blueprint(bp)

    @app.before_request
    def _guard():
        if request.endpoint in PUBLIC:
            return
        if not session.get("user_id"):
            return redirect(url_for("auth.login", next=request.path))
        # Admin-only areas.
        if session.get("user_role") != "admin" and request.path.startswith(ADMIN_ONLY_PATHS):
            flash("Admins only.", "error")
            return redirect(url_for("dashboard.home"))

    @app.context_processor
    def _inject_user():
        return {"current_user": current_user()}
