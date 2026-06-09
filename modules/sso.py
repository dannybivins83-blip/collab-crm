# -*- coding: utf-8 -*-
"""Unified single sign-on (SSO) bus — the CRM is the identity provider.

When a user is logged into the CRM (by Google or password), they are
automatically authenticated into every *connected app* — SiteCam today, a
roof-measurement app tomorrow — with no separate login.

How it works
------------
The CRM mints a short-lived, single-use, HMAC-signed *assertion* describing the
logged-in user (email, name, role) and the CRM's tenant. The assertion is handed
to the embedded app over an origin-checked ``window.postMessage`` (see
``templates/sitecam.html``). The connected app verifies the signature with the
shared per-tenant secret, checks expiry + nonce (no replay), finds-or-provisions
the user in that tenant, and issues its own session — all without the user ever
seeing a second login.

Assertion format (compact, JWS-like)::

    base64url(claims_json) + "." + hex( HMAC-SHA256(base64url(claims_json), secret) )

Both sides sign/verify the *exact* base64url string, so there is no JSON
canonicalization to get wrong. See ``docs/SSO.md`` for the full contract.

Trust
-----
The signing secret is the SAME value as the connected app's per-tenant CRM
webhook secret (e.g. SiteCam's ``SEABREEZE_CRM_WEBHOOK_SECRET``). It is read from
an env var named in the registry below — never hardcoded, never sent to the
browser. The browser only ever receives a freshly-minted, ~90-second assertion.

Adding a connected app
----------------------
Add one entry to ``CONNECTED_APPS`` (id, base URL env, shared-secret env, tenant)
and implement the same verify-steps on that app's ``/auth/sso`` endpoint. Nothing
else in the CRM changes.
"""
import os
import time
import json
import hmac
import base64
import hashlib
import secrets
from urllib.parse import urlparse

from flask import Blueprint, jsonify, session, abort

import db

bp = Blueprint("sso", __name__, url_prefix="/sso")

# Assertions are valid for a short window only. Hard ceiling is 120s; we use 90s
# to leave clock-skew headroom while keeping the replay surface tiny.
ASSERTION_TTL = 90
ASSERTION_VERSION = 1


def _tenant_key():
    """This CRM's tenant key in the connected-app ecosystem.

    The SeaBreeze CRM maps to the ``seabreeze`` tenant. Override per-deployment
    with the SSO_TENANT_KEY env var (e.g. a La Gala CRM would set ``lagala``)."""
    return (os.environ.get("SSO_TENANT_KEY") or "seabreeze").strip().lower()


# ---------------------------------------------------------------------------
# Connected-apps registry — the single place new SSO targets are declared.
# ---------------------------------------------------------------------------
# Each app:
#   id          stable identifier used in the /sso/token/<id> URL
#   name        human label
#   base_url    the app's web origin; env override lets prod/stage differ
#   secret_env  env var holding the shared HMAC secret (== the app's per-tenant
#               CRM webhook secret). NEVER hardcode the secret itself.
#   sso_path    the app's SSO verify endpoint (for documentation / future use)
#   embed       True if the app is embedded as an iframe and receives the
#               assertion via postMessage
def _apps():
    return {
        "sitecam": {
            "id": "sitecam",
            "name": "SiteCam",
            "base_url": (os.environ.get("SITECAM_URL")
                         or "https://sitecam-web.onrender.com").strip().rstrip("/"),
            "secret_env": "SEABREEZE_CRM_WEBHOOK_SECRET",
            "sso_path": "/api/auth/sso",
            "embed": True,
        },
        # ── Future: roof-measurement app (design only — see docs/SSO.md) ──
        # Uncomment + set MEASURE_URL + MEASURE_CRM_WEBHOOK_SECRET to enable.
        # "measure": {
        #     "id": "measure",
        #     "name": "Roof Measurement",
        #     "base_url": (os.environ.get("MEASURE_URL") or "").strip().rstrip("/"),
        #     "secret_env": "MEASURE_CRM_WEBHOOK_SECRET",
        #     "sso_path": "/auth/sso",
        #     "embed": True,
        # },
    }


def app_origin(app):
    """The browser origin (scheme://host[:port]) for postMessage targeting."""
    p = urlparse(app["base_url"])
    if not p.scheme or not p.netloc:
        return ""
    return "%s://%s" % (p.scheme, p.netloc)


def _secret(app):
    """The shared HMAC secret for an app. Reads the registry env var; falls back to
    the connected app's documented dev default (``<tenant>-webhook-secret``, which
    the SiteCam seed also uses when unset) so local dev works with no config. In
    production BOTH sides must set the same real secret in their env."""
    val = os.environ.get(app["secret_env"], "").strip()
    if val:
        return val, False
    return "%s-webhook-secret" % _tenant_key(), True   # dev fallback


def _b64url(raw_bytes):
    return base64.urlsafe_b64encode(raw_bytes).rstrip(b"=").decode("ascii")


def mint_assertion(app, user):
    """Build a signed SSO assertion for ``user`` targeting connected ``app``.

    Returns ``(assertion, claims)``. ``user`` is a CRM users-table row dict."""
    secret, is_dev = _secret(app)
    now = int(time.time())
    claims = {
        "v": ASSERTION_VERSION,
        "iss": "crm",                       # this CRM is the identity provider
        "app": app["id"],                   # intended audience (one app)
        "tenant": _tenant_key(),            # tenant the user belongs to
        "email": (user.get("email") or "").strip().lower(),
        "name": user.get("name") or "",
        "role": user.get("role") or "sales",  # CRM role; app maps to its own
        "iat": now,
        "exp": now + ASSERTION_TTL,
        "nonce": secrets.token_urlsafe(18),   # single-use; app rejects replays
    }
    payload = _b64url(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(secret.encode("utf-8"), payload.encode("ascii"),
                   hashlib.sha256).hexdigest()
    return payload + "." + sig, claims, is_dev


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@bp.route("/apps")
def apps():
    """List embeddable connected apps + their origins (for the parent page's
    postMessage target). No secrets. Login required (not in auth.PUBLIC)."""
    out = []
    for a in _apps().values():
        if not a.get("base_url"):
            continue
        out.append({"id": a["id"], "name": a["name"],
                    "origin": app_origin(a), "embed": bool(a.get("embed"))})
    return jsonify({"apps": out})


@bp.route("/token/<app_id>")
def token(app_id):
    """Mint a fresh SSO assertion for the logged-in user + the named app.

    The parent page fetches this (same-origin, credentialed) and postMessages the
    assertion to the embedded app. Requires a CRM session — the before-request
    guard enforces login because this endpoint is NOT in ``auth.PUBLIC``."""
    uid = session.get("user_id")
    if not uid:
        abort(401)
    app = _apps().get(app_id)
    if not app or not app.get("base_url"):
        abort(404, "Unknown connected app.")
    user = db.get("users", uid)
    if not user:
        abort(401)
    assertion, claims, is_dev = mint_assertion(app, user)
    resp = {
        "app": app["id"],
        "origin": app_origin(app),
        "assertion": assertion,
        "expires_in": ASSERTION_TTL,
        "exp": claims["exp"],
    }
    if is_dev:
        # Surfaced (not secret) so it's obvious the prod secret isn't set yet.
        resp["dev_secret"] = True
    return jsonify(resp)
