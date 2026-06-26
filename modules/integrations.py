# -*- coding: utf-8 -*-
"""Integrations hub — one page listing every integration instance + live status.

Tenant-agnostic. Each integration's status is derived from CHEAP, SAFE checks only:
presence of env vars / config, an existing ``connected``-style helper, or a trivial
health ping. We NEVER read, print, or expose a secret VALUE — only booleans and a
status label leave this module.

Status vocabulary (one badge per row):
    connected      ✅  configured + (where checkable) reachable / authenticated
    degraded       ⚠️  configured but a health/auth check failed, or running on a
                       dev/fallback secret in a way that won't work in prod
    not_configured ⬜  required env/config absent — integration is dark
    down           🔴  configured + expected reachable, but a health ping failed hard

Add a new integration by appending one dict to the list built in ``_collect()``.
"""
import os

from flask import Blueprint, render_template

import config
import db

bp = Blueprint("integrations", __name__, url_prefix="/integrations")


# ---------------------------------------------------------------------------
# Status helpers
# ---------------------------------------------------------------------------
CONNECTED = "connected"
DEGRADED = "degraded"
NOT_CONFIGURED = "not_configured"
DOWN = "down"

_BADGE = {
    CONNECTED: ("✅", "Connected"),
    DEGRADED: ("⚠️", "Degraded"),
    NOT_CONFIGURED: ("⬜", "Not configured"),
    DOWN: ("\U0001f534", "Down"),
}


def _env_set(*names):
    """True only if EVERY named env var is present + non-empty. Never returns the value."""
    return all((os.environ.get(n) or "").strip() for n in names)


def _row(name, status, what, detail="", config_endpoint=None, config_label=None):
    icon, label = _BADGE.get(status, _BADGE[NOT_CONFIGURED])
    return {
        "name": name,
        "status": status,
        "badge_icon": icon,
        "badge_label": label,
        "what": what,
        "detail": detail,
        "config_endpoint": config_endpoint,
        "config_label": config_label or "Configure",
    }


def _ping_ok(url, timeout=4):
    """Trivial GET health ping. Returns (ok, http_status_or_None). Swallows all errors —
    a failed ping is just a status signal, never an exception that breaks the page."""
    import urllib.request
    import urllib.error
    try:
        import ssl
        try:
            import certifi
            ctx = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            ctx = ssl.create_default_context()
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return (200 <= r.status < 400), r.status
    except urllib.error.HTTPError as e:
        return False, getattr(e, "code", None)
    except Exception:
        return False, None


# ---------------------------------------------------------------------------
# Per-integration checks (each returns a _row())
# ---------------------------------------------------------------------------

def _acculynx_sync():
    """AccuLynx -> CRM bookmarklet bridge + API auto-sync."""
    try:
        from modules import acculynx_sync as ax
        has_secret = bool(ax._sync_secret())
    except Exception:
        has_secret = _env_set("CRM_SYNC_SECRET")
    last = ""
    try:
        last = (db.get_company() or {}).get("acculynx_last_sync") or ""
    except Exception:
        last = ""
    if has_secret:
        status = CONNECTED
        detail = ("Last sync: %s" % last) if last else "Secret set; no sync recorded yet"
    elif not config.IS_PROD:
        # Dev allows the bridge with no secret (fail-open locally) — flag it as degraded
        # so the operator knows prod will refuse until CRM_SYNC_SECRET is set.
        status = DEGRADED
        detail = "No CRM_SYNC_SECRET (dev fail-open; prod will reject)" + ((" · Last sync: %s" % last) if last else "")
    else:
        status = NOT_CONFIGURED
        detail = "CRM_SYNC_SECRET not set (bridge fails closed in prod)"
    return _row("AccuLynx Sync", status,
                "Imports jobs, leads & contacts from AccuLynx via the keyed bookmarklet bridge + API auto-sync.",
                detail, "sync.index", "Open Sync page")


def _roof_engine():
    """Roof Report Engine — measured roof reports."""
    has_env = _env_set("ROOF_ENGINE_URL", "ROOF_ENGINE_API_KEY")
    if not has_env:
        return _row("Roof Engine", NOT_CONFIGURED,
                    "Generates measured, branded roof-report PDFs from an address (DSM + parcel).",
                    "ROOF_ENGINE_URL / ROOF_ENGINE_API_KEY not set.",
                    "roof_reports.index", "Open Roof Reports")
    url = (os.environ.get("ROOF_ENGINE_URL") or "").rstrip("/")
    ok, code = _ping_ok(url + "/healthz")
    if ok:
        # Engine is up. A live key may be mismatched (known 401 on authed routes) — we
        # can't auth-probe safely without spending, so reflect "Connected (key unverified)".
        status, detail = CONNECTED, "Engine /healthz reachable (auth key not probed here)"
    elif code in (401, 403):
        status, detail = DEGRADED, "Env set but engine returned %s — likely an API-key mismatch" % code
    else:
        status, detail = DEGRADED, ("Engine /healthz unreachable (HTTP %s)" % code) if code else "Engine /healthz unreachable"
    return _row("Roof Engine", status,
                "Generates measured, branded roof-report PDFs from an address (DSM + parcel).",
                detail, "roof_reports.index", "Open Roof Reports")


def _measure_takeoff():
    """Measurement/Takeoff ingest — HMAC-signed POST seam (/api/takeoff + /measurements/ingest)."""
    try:
        from modules import measurements as mm
        secret = mm._ingest_secret()
    except Exception:
        secret = "1" if _env_set("MEASURE_CRM_WEBHOOK_SECRET") else ""
    has_real = _env_set("MEASURE_CRM_WEBHOOK_SECRET")
    if has_real:
        status = CONNECTED
        detail = "MEASURE_CRM_WEBHOOK_SECRET set; HMAC ingest live"
    elif secret and not config.IS_PROD:
        status = DEGRADED
        detail = "Running on the dev fallback secret — set MEASURE_CRM_WEBHOOK_SECRET for prod"
    else:
        status = NOT_CONFIGURED
        detail = "MEASURE_CRM_WEBHOOK_SECRET not set (ingest fails closed in prod)"
    return _row("Measurement / Takeoff Ingest", status,
                "Receives HMAC-signed roof measurements & takeoffs from the engine/estimator at /api/takeoff and /measurements/ingest.",
                detail, None)


def _sitecam_sso():
    """SiteCam embed + Unified SSO (CRM is the identity provider)."""
    try:
        from modules import sso
        app = sso._apps().get("sitecam")
        secret, is_dev = sso._secret(app) if app else ("", True)
    except Exception:
        secret, is_dev = ("", True)
    has_real = _env_set("SEABREEZE_CRM_WEBHOOK_SECRET")
    if has_real:
        status = CONNECTED
        detail = "Shared SSO secret set; assertions signed with the real key"
    elif secret and not config.IS_PROD:
        status = DEGRADED
        detail = "SSO on the dev fallback secret — set SEABREEZE_CRM_WEBHOOK_SECRET for prod"
    else:
        status = NOT_CONFIGURED
        detail = "SSO secret not set (assertions refused in prod until configured)"
    return _row("SiteCam + SSO", status,
                "Embeds the field-photo app and silently signs the user in via an HMAC SSO assertion (CRM is the IdP).",
                detail, "sitecam.index", "Open Site Photos")


def _gmail():
    """Gmail — per-user OAuth inbox, draft-only."""
    try:
        from modules import gmail
        configured = gmail.configured()
    except Exception:
        configured = _env_set("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET")
    connected = 0
    try:
        _ic = db.connect()
        try:
            connected = (_ic.execute(
                "SELECT COUNT(*) FROM gmail_accounts WHERE refresh_token!=''"
            ).fetchone() or (0,))[0]
        finally:
            _ic.close()
    except Exception:
        connected = 0
    if not configured:
        status, detail = NOT_CONFIGURED, "GOOGLE_OAUTH_CLIENT_ID / SECRET not set"
    elif connected:
        status, detail = CONNECTED, "%d user account%s connected" % (connected, "" if connected == 1 else "s")
    else:
        status, detail = DEGRADED, "OAuth app configured; no user has connected an inbox yet"
    return _row("Gmail", status,
                "In-dashboard inbox via per-user Google OAuth. Composes DRAFTS only — never auto-sends.",
                detail, "gmail.inbox", "Open Gmail")


def _gdrive():
    """Google Drive — file storage backend."""
    try:
        from modules import gdrive
        on = gdrive.enabled()
    except Exception:
        on = _env_set("GDRIVE_SA_JSON", "GDRIVE_FOLDER_ID")
    if on:
        status, detail = CONNECTED, "Service account + shared folder configured"
    elif _env_set("GDRIVE_SA_JSON") or _env_set("GDRIVE_FOLDER_ID"):
        status, detail = DEGRADED, "Only one of GDRIVE_SA_JSON / GDRIVE_FOLDER_ID set (need both)"
    else:
        status, detail = NOT_CONFIGURED, "GDRIVE_SA_JSON / GDRIVE_FOLDER_ID not set (using local disk)"
    return _row("Google Drive", status,
                "Persists uploaded files (photos, docs) to the company's Drive so they survive serverless hosts.",
                detail, "settings.index", "Open Settings")


def _quickbooks():
    """QuickBooks Online — invoicing + payments."""
    try:
        from modules import quickbooks as qb
        configured = qb.is_configured()
        connected = qb.is_connected()
    except Exception:
        configured = connected = False
    if connected:
        status, detail = CONNECTED, "OAuth connected; can push invoices + payment links"
    elif configured:
        status, detail = DEGRADED, "Client ID/Secret saved but not connected — click Connect on the QuickBooks page"
    else:
        status, detail = NOT_CONFIGURED, "QuickBooks Client ID/Secret not entered"
    return _row("QuickBooks Online", status,
                "Pushes CRM invoices to QBO with online payment links and customer emails.",
                detail, "quickbooks.settings", "Open QuickBooks")


def _qxo():
    """QXO / Beacon materials — native pricing + ordering (dark scaffold)."""
    try:
        from modules import qxo
        on = qxo.configured()
    except Exception:
        on = _env_set("QXO_API_BASE") and (_env_set("QXO_API_KEY") or _env_set("QXO_CLIENT_ID", "QXO_CLIENT_SECRET"))
    if on:
        status, detail = CONNECTED, "QXO_API_* configured"
    else:
        status, detail = NOT_CONFIGURED, "QXO_API_* not set (gated partner program; integration is dark)"
    return _row("QXO / Materials", status,
                "Live materials pricing + ordering against the QXO (Beacon) partner API.",
                detail, None)


def _collect():
    """Build every integration row. Each check is defensive — one failing check must
    not break the page, so we guard every call."""
    checks = (_acculynx_sync, _roof_engine, _measure_takeoff, _sitecam_sso,
              _gmail, _gdrive, _quickbooks, _qxo)
    rows = []
    for fn in checks:
        try:
            rows.append(fn())
        except Exception as e:
            rows.append(_row(fn.__name__.strip("_").replace("_", " ").title(), DEGRADED,
                             "Status check could not complete.", "Check error: %s" % type(e).__name__))
    return rows


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

@bp.route("/")
def index():
    rows = _collect()
    counts = {CONNECTED: 0, DEGRADED: 0, NOT_CONFIGURED: 0, DOWN: 0}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    summary = [
        ("✅", "Connected", counts[CONNECTED]),
        ("⚠️", "Degraded", counts[DEGRADED]),
        ("⬜", "Not configured", counts[NOT_CONFIGURED]),
        ("\U0001f534", "Down", counts[DOWN]),
    ]
    return render_template("integrations.html", rows=rows, summary=summary, total=len(rows))
