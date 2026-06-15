# -*- coding: utf-8 -*-
"""White-label roofing/construction CRM — Flask entry point.

Run:  python app.py   (opens http://127.0.0.1:5050)

A self-hostable, offline, re-brandable AccuLynx-style CRM. The default skin is
SeaBreeze Roofing, but every brand element is loaded from the company_settings
table so the app can be resold to any contractor (see Settings → Company).
"""
import os
import socket
import threading
import webbrowser

from flask import Flask, redirect, url_for, send_from_directory, abort

import config
import db
import theme

from datetime import timedelta

app = Flask(__name__)
app.secret_key = config.SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024  # 64 MB uploads
app.config["TEMPLATES_AUTO_RELOAD"] = True  # pick up template edits without a restart
# Session/CSRF hardening (audit #7): SameSite=Lax stops the session cookie riding
# cross-site form POSTs (the main CSRF vector); HttpOnly hides it from JS; Secure only
# in prod so local http dev still keeps its session.
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SECURE"] = config.IS_PROD
# Permanent sessions: cookie carries an explicit 30-day expiry instead of being a
# browser-session cookie (which Incognito drops aggressively between navigations).
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)
app.jinja_env.auto_reload = True

db.init_db()
theme.register(app)
from modules import gdrive  # noqa: E402 — ensures drive_id columns + enables Drive storage

# Blueprints (one per module).
from modules.dashboard import bp as dashboard_bp
from modules.settings import bp as settings_bp
from modules.contacts import bp as contacts_bp
from modules.leads import bp as leads_bp
from modules.jobs import bp as jobs_bp
from modules.estimates import bp as estimates_bp
from modules.permits import bp as permits_bp
from modules.files import bp as files_bp
from modules.tasks import bp as tasks_bp
from modules.calendar import bp as calendar_bp
from modules.invoices import bp as invoices_bp
from modules.materials import bp as materials_bp
from modules.reports import bp as reports_bp
from modules.comms import bp as comms_bp
from modules.templates_mgr import bp as templates_bp
from modules.measurements import bp as measurements_bp
from modules.acculynx_sync import bp as sync_bp
from modules.quickbooks import bp as quickbooks_bp
from modules.worksheet import bp as worksheet_bp
from modules.customize import bp as customize_bp
from modules.orders import bp as orders_bp
from modules.library import bp as library_bp
from modules.commissions import bp as commissions_bp
from modules.customfields import bp as customfields_bp
from modules.portal import bp as portal_bp
from modules.tools import bp as tools_bp
from modules.signups import bp as signups_bp
from modules.gmail import bp as gmail_bp
from modules.sitecam import bp as sitecam_bp
from modules.sso import bp as sso_bp
from modules.search import bp as search_bp
from modules.leads_dedupe import bp as leads_dedupe_bp
from modules.pipeline import bp as pipeline_bp
from modules.roof_reports import bp as roof_reports_bp
from modules.takeoff import bp as takeoff_bp
from modules.demos import bp as demos_bp
from modules.smart_todos import bp as smart_todos_bp
from modules.qxo import bp as qxo_bp  # native QXO materials integration (dark until QXO_API_* set)
from modules.integrations import bp as integrations_bp  # white-label integrations status hub
from modules.dbadmin import bp as dbadmin_bp  # token-gated SQLite DB-restore (dark until DB_RESTORE_TOKEN set)
from modules.permit_api import bp as permit_api_bp  # white-label permit builder REST API

for _bp in (dashboard_bp, settings_bp, contacts_bp, leads_bp, jobs_bp, estimates_bp,
            permits_bp, files_bp, tasks_bp, calendar_bp, invoices_bp, materials_bp,
            reports_bp, comms_bp, templates_bp, measurements_bp, sync_bp, quickbooks_bp,
            worksheet_bp, customize_bp, orders_bp, library_bp, commissions_bp, customfields_bp,
            portal_bp, tools_bp, signups_bp, gmail_bp, sitecam_bp, sso_bp, search_bp,
            leads_dedupe_bp, pipeline_bp, roof_reports_bp, demos_bp, smart_todos_bp, takeoff_bp,
            qxo_bp, integrations_bp, dbadmin_bp, permit_api_bp):
    app.register_blueprint(_bp)

# Real per-user login (registers its own blueprint + before-request guard).
from modules.auth import init_auth
init_auth(app)

# In-app notifications (masthead bell) — alerts the team on homeowner portal actions.
from modules.notifications import init_notifications
init_notifications(app)

# Workflow Manager: registers blueprint + wraps db.add_activity to fire on stage changes.
from modules.automations import init_automations
init_automations(app)

# Background AccuLynx auto-sync (runs only when enabled + an API key is set).
from modules.acculynx_sync import start_auto_sync
start_auto_sync(app)


# Extensions we'll let render inline; everything else (e.g. .html/.svg/.js) downloads
# instead of executing — closes the stored-XSS surface from served-back uploads (audit #12).
_INLINE_OK = {"png", "jpg", "jpeg", "gif", "webp", "bmp", "ico", "pdf"}


def _safe_upload_resp(resp, subpath):
    ext = subpath.rsplit(".", 1)[-1].lower() if "." in subpath else ""
    resp.headers["X-Content-Type-Options"] = "nosniff"
    if ext not in _INLINE_OK:
        resp.headers["Content-Disposition"] = "attachment"
    return resp


_UPLOADS_SENSITIVE = ("docs/", "estimates/", "permits/", "photos/", "jobs/")


@app.route("/uploads/<path:subpath>")
def uploads(subpath):
    """Serve uploaded files (photos, docs, logos, estimate/permit PDFs).
    Falls back to Google Drive when the local copy is missing (serverless hosts).
    Sensitive subdirs (docs/estimates/permits/photos) require an active session."""
    if any(subpath.startswith(p) for p in _UPLOADS_SENSITIVE):
        from flask import session as _sess
        if not _sess.get("user_id"):
            abort(403)
    full = os.path.normpath(os.path.join(config.UPLOAD_DIR, subpath))
    if full.startswith(config.UPLOAD_DIR) and os.path.exists(full):
        return _safe_upload_resp(
            send_from_directory(os.path.dirname(full), os.path.basename(full)), subpath)
    # Local miss → try Google Drive (if configured + this file was mirrored).
    try:
        from modules import gdrive
        got = gdrive.serve_fallback(subpath)
        if got:
            from flask import Response
            return _safe_upload_resp(Response(got[0], mimetype=got[1]), subpath)
    except Exception:
        pass
    abort(404)


@app.route("/favicon.ico")
def favicon():
    return ("", 204)




def _free_port(preferred):
    for p in range(preferred, preferred + 25):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    return preferred


PORT = _free_port(config.PORT)


def _open_browser():
    if os.environ.get("CRM_NOBROWSER"):
        return
    try:
        webbrowser.open("http://127.0.0.1:%d" % PORT)
    except Exception:
        pass


if __name__ == "__main__":
    if os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        print("\n  White-label CRM running at  http://127.0.0.1:%d\n" % PORT)
        threading.Timer(1.2, _open_browser).start()
    app.run(host="127.0.0.1", port=PORT, debug=bool(os.environ.get("CRM_DEBUG")))
