# -*- coding: utf-8 -*-
"""Real per-user authentication — login/logout, password hashing, role gating.

Self-contained: adds its own `password_hash` column, seeds default passwords,
registers a before-request guard, and exposes `current_user` to templates.
"""
from flask import (Blueprint, render_template, request, redirect, url_for,
                   session, flash)
from werkzeug.security import generate_password_hash, check_password_hash

import os
import secrets
import db

bp = Blueprint("auth", __name__)

# Seed/default password. Override in production via the CRM_DEFAULT_PASSWORD env var
# so the live (real-data) instance never uses the publicly-known default.
DEFAULT_PASSWORD = os.environ.get("CRM_DEFAULT_PASSWORD", "seabreeze2026")
# Endpoints reachable without being logged in.
PUBLIC = {"auth.login", "auth.google_login", "auth.google_callback",
          "static", "uploads", "favicon", "leads.import_leads",
          "leads.intake", "leads.intake_email", "leads.intake_ringcentral",
          "sync.browser_import", "sync.cron",
          "sync.doc_import", "sync.doc_manifest", "sync.doc_batch",
          "sync.roofreport_import", "sync.roofreport_manifest", "sync.roofreport_batch",
          "sync.photo_import", "sync.photo_batch",
          "sync.billing_import", "sync.billing_manifest", "sync.bill_batch",
          "sync.estimate_import", "sync.comm_import", "sync.worksheet_import",
          "sync.job_guids", "sync.catalog_import", "sync.debug_probe",
          "portal.home", "portal.message", "portal.sign", "portal.sign_doc", "portal.pay",
          "portal.upload_doc", "portal.upload_photo", "portal.design", "portal.design_request",
          "portal.referral_land", "portal.refer_share", "portal.refer_msg",
          "portal.learn", "portal.seminar", "portal.design_photo",
          "signups.portal_view", "signups.portal_complete",
          "measurements.ingest",
          "sitecam.gallery_link"}
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


def _after_login_redirect(nxt):
    """Land the user where they were headed — but on first login, seamlessly
    connect their Gmail inbox if it isn't connected yet (Part 2 of unified SSO).

    CRM login itself stays identity-only (non-sensitive Google scopes, no app
    review). The restricted gmail.modify consent is triggered ONCE per session so
    the inbox widget is ready without a manual "Connect Gmail" click. A session
    flag prevents a declined consent from looping."""
    target = nxt if (nxt or "").startswith("/") else url_for("dashboard.home")
    try:
        from modules import gmail
        if (gmail.configured()
                and not session.get("gmail_autoprompted")
                and not gmail.account_for_user(session.get("user_id"))):
            session["gmail_autoprompted"] = True
            session["gmail_after"] = target   # where to land after consent
            return redirect(url_for("gmail.connect"))
    except Exception:
        pass
    return redirect(target)


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
            return _after_login_redirect(request.args.get("next"))
        flash("Invalid email or password.", "error")
    from modules import gmail
    return render_template("login.html", google_enabled=gmail.configured())


# ---------------------------------------------------------------------------
# Single sign-on — "Sign in with Google". One Google consent both signs the user
# into the CRM (matched by email) AND connects their Gmail inbox widget. Shares
# the same OAuth client as the Gmail integration.
# ---------------------------------------------------------------------------

def _google_redirect_uri():
    host = request.host
    scheme = "http" if host.startswith(("127.0.0.1", "localhost")) else "https"
    return "%s://%s/auth/google/callback" % (scheme, host)


@bp.route("/auth/google/login")
def google_login():
    from modules import gmail
    if not gmail.configured():
        flash("Google sign-in isn't configured yet.", "error")
        return redirect(url_for("auth.login"))
    from urllib.parse import urlencode
    state = secrets.token_urlsafe(24)
    session["google_login_state"] = state
    nxt = request.args.get("next", "")
    if nxt.startswith("/"):
        session["google_login_next"] = nxt
    cid, _ = gmail._cfg()
    qs = urlencode({
        "client_id": cid, "redirect_uri": _google_redirect_uri(),
        "response_type": "code", "scope": gmail.LOGIN_SCOPES,
        "access_type": "offline", "include_granted_scopes": "true",
        "prompt": "consent", "state": state,
    })
    return redirect(gmail._AUTH + "?" + qs)


@bp.route("/auth/google/callback")
def google_callback():
    from modules import gmail
    if request.args.get("state") != session.pop("google_login_state", None):
        flash("Sign-in expired — please try again.", "error")
        return redirect(url_for("auth.login"))
    code = request.args.get("code")
    if not code:
        return redirect(url_for("auth.login"))
    tok = gmail.exchange_code(code, _google_redirect_uri())
    if not tok.get("access_token"):
        flash("Google sign-in failed. Please try again.", "error")
        return redirect(url_for("auth.login"))
    email = gmail.userinfo_email(tok["access_token"]).lower()
    user = next((u for u in db.all_rows("users")
                 if (u.get("email") or "").lower() == email and u.get("active", 1)), None)
    if not user:
        flash("No CRM account for %s. Ask an admin to add you first." % (email or "that account"), "error")
        return redirect(url_for("auth.login"))
    session["user_id"] = user["id"]
    session["user_name"] = user["name"]
    session["user_role"] = user.get("role", "sales")
    # Login grants identity only; the Gmail inbox is a separate restricted-scope
    # connect. _after_login_redirect auto-triggers it once so it's seamless.
    return _after_login_redirect(session.pop("google_login_next", ""))


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
