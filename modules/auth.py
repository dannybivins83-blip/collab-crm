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
import time
import db

bp = Blueprint("auth", __name__)

# Brute-force protection: max 5 failed attempts per IP in a 60-second window.
_login_attempts: dict = {}  # ip -> [timestamp, ...]
_MAX_ATTEMPTS = 5
_WINDOW_SECS = 60


def _is_rate_limited(ip: str) -> bool:
    now = time.monotonic()
    hits = [t for t in _login_attempts.get(ip, []) if now - t < _WINDOW_SECS]
    _login_attempts[ip] = hits
    return len(hits) >= _MAX_ATTEMPTS


def _record_failure(ip: str) -> None:
    now = time.monotonic()
    hits = [t for t in _login_attempts.get(ip, []) if now - t < _WINDOW_SECS]
    hits.append(now)
    _login_attempts[ip] = hits

# No hardcoded default password (Fix 2, audit #critical-2).
# Users without a password_hash cannot log in; an admin must set their password.
# On first-run with no admin account, CRM_DEFAULT_PASSWORD env var is required.
# This constant is set at import time from the env var so settings.py can reference it.
DEFAULT_PASSWORD = os.environ.get("CRM_DEFAULT_PASSWORD", "").strip()
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
          # sync.job_guids removed: secured via _KEY_REQUIRED (CRM_SYNC_SECRET).
          "sync.catalog_import",
          "sync.insurance_import", "sync.orders_import",
          "sync.reconcile_financials", "sync.expenses_import", "sync.closed_import",
          "sync.roofreport_collect", "sync.pipeline_batch", "sync.estimate_collect",
          "portal.home", "portal.invite", "portal.portal_login",
          "portal.message", "portal.sign", "portal.sign_doc", "portal.pay",
          "portal.upload_doc", "portal.upload_photo", "portal.design", "portal.design_request",
          "portal.referral_land", "portal.refer_share", "portal.refer_msg",
          "portal.learn", "portal.seminar", "portal.design_photo", "portal.proposal",
          "portal.portal_file",
          "signups.portal_view", "signups.portal_complete",
          "measurements.ingest", "takeoff.create",
          # Permit REST API — key-gated via X-Permit-API-Key; must bypass the
          # session guard so external callers reach the route handlers.
          "permit_api.list_ahjs", "permit_api.submit_build",
          "permit_api.build_status", "permit_api.build_download",
          # Self-contained iframe embed widget — does its own api_key validation,
          # so it must bypass the session guard to load on a contractor's site.
          "permits.widget_embed",
          # Branded demo portal (shareable, login-free sales tool). The generator
          # UI (demo.generator/create/delete) is intentionally NOT public.
          "demo.portal", "demo.design", "demo.design_request",
          "demo.refer_share", "demo.refer_msg",
          # Token-gated DB-restore + CSV imports: NOT session-auth'd. Their own
          # X-Restore-Token check is the gate (404 when unarmed/wrong). Must bypass
          # the login redirect so the gate returns 404, not a 302 to /login.
          "dbadmin.db_restore",
          "dbadmin.import_job_expenses",
          "dbadmin.import_workflow_status",
          "sitecam.gallery_link"}
# Endpoints only admins may hit (prefix match on the path).
ADMIN_ONLY_PATHS = ("/settings", "/orders/vendors", "/workflow", "/quickbooks")


def _ensure_schema():
    import logging as _logging
    try:
        db.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
    except Exception:
        pass
    db._COLCACHE.clear()
    # Seed NULL-hash users from CRM_DEFAULT_PASSWORD on first run.
    # An admin MUST change this password via /account after first login.
    for u in db.all_rows("users"):
        if not u.get("password_hash"):
            if DEFAULT_PASSWORD:
                try:
                    set_password(u["id"], DEFAULT_PASSWORD)
                    _logging.info(
                        "CRM: seeded password for user id=%s (%s) from CRM_DEFAULT_PASSWORD.",
                        u.get("id"), u.get("email") or u.get("name"),
                    )
                except Exception as _e:
                    _logging.error("CRM: failed to seed password for user id=%s: %s", u.get("id"), _e)
            else:
                _logging.warning(
                    "CRM: user id=%s (%s) has no password_hash — set CRM_DEFAULT_PASSWORD env var "
                    "or have an admin set their password.", u.get("id"), u.get("email") or u.get("name")
                )


def current_user():
    uid = session.get("user_id")
    return db.get("users", uid) if uid else None


def _after_login_redirect(nxt):
    """Land the user where they were headed — but on first login, seamlessly
    connect their Gmail inbox if it isn't connected yet (Part 2 of unified SSO).

    CRM login itself stays identity-only (non-sensitive Google scopes, no app
    review). The restricted gmail.modify consent is triggered ONCE per session so
    the inbox widget is ready without a manual "Connect Gmail" click. A session
    flag prevents a declined consent from looping.

    When the user was interrupted mid-task (?next= set), skip the Gmail autoprompt
    and take them directly back — the OAuth round-trip adds friction and can drop
    the session in Incognito/strict-cookie environments."""
    from urllib.parse import urlparse as _urlparse
    _p = _urlparse(nxt or "")
    if _p.netloc or _p.scheme or not (nxt or "").startswith("/"):
        nxt = url_for("dashboard.home")
    target = nxt
    # Only autoprompt on plain dashboard landings, not on redirect-back flows
    if not nxt:
        try:
            from modules import gmail
            if (gmail.configured()
                    and not session.get("gmail_autoprompted")
                    and not gmail.account_for_user(session.get("user_id"))):
                session["gmail_autoprompted"] = True
                session["gmail_after"] = target
                return redirect(url_for("gmail.connect"))
        except Exception:
            pass
    return redirect(target)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id"):
        return redirect(url_for("dashboard.home"))
    # Ensure a pre-auth CSRF token exists in the session for the login form.
    # This defends against login-CSRF (attacker logs victim into attacker account).
    if "_login_csrf" not in session:
        session["_login_csrf"] = secrets.token_hex(24)
    if request.method == "POST":
        # Validate login-form CSRF token (pre-auth defense-in-depth).
        presented = request.form.get("_csrf", "")
        expected = session.get("_login_csrf", "")
        if not presented or not expected or not secrets.compare_digest(
                str(expected), str(presented)):
            flash("Request expired — please try again.", "error")
            session["_login_csrf"] = secrets.token_hex(24)
            from modules import gmail
            return render_template("login.html", google_enabled=gmail.configured())
        ip = (request.headers.get("X-Forwarded-For", request.remote_addr or "unknown")
              .split(",")[0].strip())
        if _is_rate_limited(ip):
            flash("Too many failed attempts — please wait 60 seconds.", "error")
        else:
            email = request.form.get("email", "").strip().lower()
            pw = request.form.get("password", "")
            _candidates = db.all_rows("users", "LOWER(email)=? AND (active IS NULL OR active=1)", (email,), limit=1)
            user = _candidates[0] if _candidates else None
            if user and user.get("password_hash") and check_password_hash(user["password_hash"], pw):
                _login_attempts.pop(ip, None)
                # Fix 5 (audit #critical-5): session fixation — clear any attacker-
                # seeded session data before writing the authenticated user identity.
                # Preserve only the post-login redirect target (not user-controlled).
                _nxt = request.args.get("next")
                session.clear()
                session.permanent = True
                session["user_id"] = user["id"]
                session["user_name"] = user["name"]
                session["user_role"] = user.get("role", "sales")
                # Re-generate CSRF token for the new authenticated session.
                session["_csrf"] = secrets.token_hex(24)
                return _after_login_redirect(_nxt)
            _record_failure(ip)
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
    _candidates = db.all_rows("users", "LOWER(email)=? AND (active IS NULL OR active=1)", (email,), limit=1)
    user = _candidates[0] if _candidates else None
    if not user:
        flash("No CRM account for %s. Ask an admin to add you first." % (email or "that account"), "error")
        return redirect(url_for("auth.login"))
    # Fix 5 (audit #critical-5): session fixation — clear before writing user identity.
    # Preserve the post-login redirect; the Google state nonce was already pop()'d above.
    _nxt = session.pop("google_login_next", "")
    session.clear()
    session.permanent = True
    session["user_id"] = user["id"]
    session["user_name"] = user["name"]
    session["user_role"] = user.get("role", "sales")
    # Re-generate CSRF token for the new authenticated session.
    session["_csrf"] = secrets.token_hex(24)
    # Login grants identity only; the Gmail inbox is a separate restricted-scope
    # connect. _after_login_redirect auto-triggers it once so it's seamless.
    return _after_login_redirect(_nxt)


@bp.route("/logout", methods=["POST"])
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
    # Fix 2: never fall back to a default. Reject falsy passwords explicitly.
    if not password:
        raise ValueError("set_password: password must not be empty or None")
    db.update("users", user_id, password_hash=generate_password_hash(password))


def _get_csrf_token():
    """Return the per-session CSRF token, generating one if needed."""
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(24)
    return session["_csrf"]


# Endpoints exempt from CSRF validation (cross-origin bookmarklets / HMAC-signed APIs /
# public portal magic-link routes / the DB-restore which has its own X-Restore-Token gate).
_CSRF_EXEMPT = PUBLIC | {
    "sync.index",       # sync dashboard page (GET-only in practice)
    "dbadmin.reconcile_docs",      # token-gated admin tool, supports GET
    "dbadmin.import_job_expenses", # token-gated CSV import (X-Restore-Token)
    "dbadmin.import_workflow_status",
    "dbadmin.link_estimates_to_job",  # token-gated OR admin session
    # auth.logout is now POST-only and CSRF-validated; removed from exempt list.
    # Permit API key management — these are session-authenticated but called from the
    # Settings page via JS fetch without a CSRF token in the request body/header.
    # They perform their own session-user gate inside the handler.
    "permit_api.new_key",
    "permit_api.revoke_key",
    # File-upload endpoints — protected by session auth + SameSite=Lax; no form _csrf field.
    "leads.run_takeoff",
    "leads.retry_takeoff",
    "leads.parse_image",
}


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
        # CSRF validation on all state-changing requests for authenticated sessions.
        # SameSite=Lax (app.py) is the primary guard; tokens add defense-in-depth.
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            if request.endpoint not in _CSRF_EXEMPT:
                expected = session.get("_csrf")
                presented = (request.form.get("_csrf")
                             or request.headers.get("X-CSRFToken")
                             or request.headers.get("X-CSRF-Token"))
                if not expected or not presented or not secrets.compare_digest(
                        str(expected), str(presented)):
                    flash("Request expired or invalid — please try again.", "error")
                    # Fixed route, never request.referrer (attacker-controllable → open redirect).
                    return redirect(url_for("dashboard.home"))

    @app.context_processor
    def _inject_user():
        tok = _get_csrf_token()
        return {"current_user": current_user(), "csrf_token": tok}
