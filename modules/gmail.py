# -*- coding: utf-8 -*-
"""Gmail integration — a real in-dashboard inbox via the Gmail API.

Each CRM user connects their own Google account once (3-legged OAuth). We store a
refresh token per user and use it to render their inbox, read messages, and draft
replies right inside the dashboard — no leaving the CRM, no iframe (Google blocks
embedding mail.google.com).

House rule honored: composing/replying creates a DRAFT in the user's Gmail — we
never auto-send. The user opens the draft in Gmail to send it.

Activation (no code change): set two env vars from a Google Cloud OAuth client
(type = Web application):
  - GOOGLE_OAUTH_CLIENT_ID
  - GOOGLE_OAUTH_CLIENT_SECRET
and add each host's /gmail/callback to the client's Authorized redirect URIs.
"""
import os
import time
import json
import base64
import secrets
from email.mime.text import MIMEText
from email.utils import parseaddr, formataddr

from flask import (Blueprint, request, redirect, url_for, session, jsonify,
                   abort)

import db

bp = Blueprint("gmail", __name__, url_prefix="/gmail")

# Single scope: gmail.modify covers read, mark-as-read, and creating drafts
# (everything except permanent delete). userinfo.email tells us which account.
SCOPES = " ".join([
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/gmail.modify",
])
# Scopes for "Sign in with Google" (CRM login / SSO). Identity only — these are
# NON-sensitive, so an External OAuth app can be published with no Google review
# and refresh tokens never expire. Any Google account can sign in (we still only
# admit emails that match a provisioned CRM user). The Gmail inbox is a separate,
# opt-in connect (/gmail/connect) because gmail.modify is a restricted scope.
LOGIN_SCOPES = " ".join([
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
])
_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN = "https://oauth2.googleapis.com/token"
_USERINFO = "https://openidconnect.googleapis.com/v1/userinfo"
_API = "https://gmail.googleapis.com/gmail/v1/users/me"

# Per-user Gmail credentials. Kept out of the backup export (tokens are secrets).
try:
    db.execute("""CREATE TABLE IF NOT EXISTS gmail_accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, email TEXT,
        access_token TEXT, refresh_token TEXT, expiry REAL DEFAULT 0,
        connected_at TEXT)""")
except Exception:
    pass
db._COLCACHE.clear()


# ---------------------------------------------------------------------------
# Config / credentials
# ---------------------------------------------------------------------------

def _cfg():
    return (os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "").strip(),
            os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "").strip())


def configured():
    cid, sec = _cfg()
    return bool(cid and sec)


def _redirect_uri():
    """Build this host's callback URL. Forces https off-localhost so it matches the
    redirect URI registered in the Google console (Vercel terminates TLS upstream)."""
    host = request.host
    scheme = "http" if host.startswith(("127.0.0.1", "localhost")) else "https"
    return "%s://%s/gmail/callback" % (scheme, host)


def _current_uid():
    return session.get("user_id")


def account_for_user(uid):
    if not uid:
        return None
    rows = db.all_rows("gmail_accounts", "user_id=?", (uid,), "id DESC")
    return rows[0] if rows else None


def save_grant(uid, tok, email):
    """Upsert a user's Gmail credentials from an OAuth token response. Shared by the
    Gmail /callback and the 'Sign in with Google' login flow so one consent connects
    the inbox widget too. Keeps the prior refresh token if Google omits a new one."""
    if not uid:
        return
    fields = {"email": email or "",
              "access_token": tok.get("access_token", ""),
              "expiry": time.time() + int(tok.get("expires_in", 3600))}
    if tok.get("refresh_token"):
        fields["refresh_token"] = tok["refresh_token"]
    existing = account_for_user(uid)
    if existing:
        db.update("gmail_accounts", existing["id"], **fields)
    else:
        fields.update({"user_id": uid,
                       "refresh_token": tok.get("refresh_token", ""),
                       "connected_at": db.now()})
        db.insert("gmail_accounts", fields)


def exchange_code(code, redirect_uri):
    """Trade an auth code for tokens. Returns the token dict (or {})."""
    cid, sec = _cfg()
    try:
        import requests
        r = requests.post(_TOKEN, data={
            "code": code, "client_id": cid, "client_secret": sec,
            "redirect_uri": redirect_uri, "grant_type": "authorization_code"}, timeout=30)
        return r.json() if r.ok else {}
    except Exception:
        return {}


def userinfo_email(access_token):
    """Resolve the Google account email for an access token. ('' on failure.)"""
    try:
        import requests
        r = requests.get(_USERINFO, headers={"Authorization": "Bearer " + access_token}, timeout=20)
        return (r.json() or {}).get("email", "") if r.ok else ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Token handling
# ---------------------------------------------------------------------------

def _access_token(uid):
    """Return a valid access token for this user, refreshing if expired. None if
    the user hasn't connected or the refresh failed (they must reconnect)."""
    acct = account_for_user(uid)
    if not acct:
        return None
    if acct.get("access_token") and time.time() < (acct.get("expiry") or 0) - 60:
        return acct["access_token"]
    rt = acct.get("refresh_token")
    if not rt:
        return None
    cid, sec = _cfg()
    try:
        import requests
        r = requests.post(_TOKEN, data={
            "client_id": cid, "client_secret": sec,
            "refresh_token": rt, "grant_type": "refresh_token"}, timeout=30)
        if not r.ok:
            return None
        tok = r.json()
        db.update("gmail_accounts", acct["id"],
                  access_token=tok.get("access_token", ""),
                  expiry=time.time() + int(tok.get("expires_in", 3600)))
        return tok.get("access_token")
    except Exception:
        return None


def _api_get(uid, path, params=None):
    tok = _access_token(uid)
    if not tok:
        return None
    try:
        import requests
        r = requests.get(_API + path, params=params or {},
                         headers={"Authorization": "Bearer " + tok}, timeout=30)
        if r.ok:
            return r.json()
    except Exception:
        pass
    return None


def _api_post(uid, path, body):
    tok = _access_token(uid)
    if not tok:
        return None
    try:
        import requests
        r = requests.post(_API + path, json=body,
                          headers={"Authorization": "Bearer " + tok}, timeout=30)
        if r.ok:
            return r.json()
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# OAuth flow
# ---------------------------------------------------------------------------

@bp.route("/connect")
def connect():
    if not configured():
        abort(503, "Gmail is not configured: set GOOGLE_OAUTH_CLIENT_ID + GOOGLE_OAUTH_CLIENT_SECRET.")
    cid, _ = _cfg()
    state = secrets.token_urlsafe(24)
    session["gmail_oauth_state"] = state
    from urllib.parse import urlencode
    qs = urlencode({
        "client_id": cid,
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",      # get a refresh token
        "include_granted_scopes": "true",
        "prompt": "consent",           # force refresh-token issuance on reconnect
        "state": state,
    })
    return redirect(_AUTH + "?" + qs)


@bp.route("/callback")
def callback():
    if request.args.get("state") != session.pop("gmail_oauth_state", None):
        abort(400, "OAuth state mismatch — please try connecting again.")
    code = request.args.get("code")
    if not code:
        return redirect(url_for("dashboard.home"))
    tok = exchange_code(code, _redirect_uri())
    if not tok.get("access_token"):
        return redirect(url_for("dashboard.home") + "?gmail=error")
    email = userinfo_email(tok["access_token"])
    save_grant(_current_uid(), tok, email)
    # If we were auto-triggered right after login, return the user to where they
    # were headed (set by auth._after_login_redirect); else the dashboard.
    after = session.pop("gmail_after", "")
    base = after if after.startswith("/") else url_for("dashboard.home")
    sep = "&" if "?" in base else "?"
    return redirect(base + sep + "gmail=connected")


@bp.route("/disconnect", methods=["POST"])
def disconnect():
    acct = account_for_user(_current_uid())
    if acct:
        # Best-effort token revoke, then forget it locally.
        try:
            import requests
            requests.post("https://oauth2.googleapis.com/revoke",
                          params={"token": acct.get("refresh_token") or acct.get("access_token")},
                          timeout=15)
        except Exception:
            pass
        db.delete("gmail_accounts", acct["id"])
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Inbox / read / draft  (consumed by the dashboard widget via fetch())
# ---------------------------------------------------------------------------

@bp.route("/status")
def status():
    acct = account_for_user(_current_uid())
    return jsonify({"configured": configured(),
                    "connected": bool(acct),
                    "email": acct.get("email", "") if acct else ""})


def _hdr(headers, name):
    for h in headers or []:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


@bp.route("/inbox")
def inbox():
    uid = _current_uid()
    q = request.args.get("q", "")
    params = {"maxResults": 15, "labelIds": "INBOX"}
    if q:
        params["q"] = q
        params.pop("labelIds", None)
    listing = _api_get(uid, "/messages", params)
    if listing is None:
        return jsonify({"ok": False, "connected": bool(account_for_user(uid))}), 200
    out = []
    for m in listing.get("messages", [])[:15]:
        full = _api_get(uid, "/messages/" + m["id"],
                        {"format": "metadata",
                         "metadataHeaders": ["From", "Subject", "Date"]})
        if not full:
            continue
        hs = full.get("payload", {}).get("headers", [])
        frm = _hdr(hs, "From")
        name, addr = parseaddr(frm)
        out.append({
            "id": m["id"], "threadId": full.get("threadId"),
            "from": addr or frm, "fromName": name or addr or frm,
            "subject": _hdr(hs, "Subject") or "(no subject)",
            "date": _hdr(hs, "Date"),
            "snippet": full.get("snippet", ""),
            "unread": "UNREAD" in (full.get("labelIds") or []),
        })
    return jsonify({"ok": True, "messages": out})


def _b64url_decode(s):
    if not s:
        return b""
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _extract_body(payload):
    """Walk a Gmail payload; prefer text/html, fall back to text/plain."""
    html = text = ""
    def walk(p):
        nonlocal html, text
        mt = p.get("mimeType", "")
        body = p.get("body", {})
        data = body.get("data")
        if mt == "text/html" and data and not html:
            html = _b64url_decode(data).decode("utf-8", "replace")
        elif mt == "text/plain" and data and not text:
            text = _b64url_decode(data).decode("utf-8", "replace")
        for part in p.get("parts", []) or []:
            walk(part)
    walk(payload or {})
    return html, text


@bp.route("/message/<mid>")
def message(mid):
    uid = _current_uid()
    full = _api_get(uid, "/messages/" + mid, {"format": "full"})
    if not full:
        return jsonify({"ok": False}), 200
    hs = full.get("payload", {}).get("headers", [])
    html, text = _extract_body(full.get("payload", {}))
    # Mark as read (drop the UNREAD label).
    _api_post(uid, "/messages/" + mid + "/modify", {"removeLabelIds": ["UNREAD"]})
    return jsonify({
        "ok": True, "id": mid, "threadId": full.get("threadId"),
        "from": _hdr(hs, "From"), "to": _hdr(hs, "To"),
        "subject": _hdr(hs, "Subject"), "date": _hdr(hs, "Date"),
        "messageId": _hdr(hs, "Message-ID"),
        "html": html, "text": text,
    })


def create_draft(uid, to, subject, body, in_reply_to=None, references=None, thread_id=None):
    """Create a Gmail DRAFT for user `uid` (never auto-send — house rule). Returns the
    draft id, or None if the user's Gmail isn't connected / the API call failed.
    Callable from other modules (e.g. invoice pay reminders), not just the HTTP route."""
    if not account_for_user(uid):
        return None
    msg = MIMEText(body or "", "plain", "utf-8")
    msg["To"] = (to or "").strip()
    msg["Subject"] = subject or ""
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = references or in_reply_to
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    resource = {"message": {"raw": raw}}
    if thread_id:
        resource["message"]["threadId"] = thread_id
    res = _api_post(uid, "/drafts", resource)
    return res.get("id") if res else None


def send_message(uid, to, subject, body):
    """SEND an email immediately from user `uid`'s connected Gmail (gmail.modify grant
    permits send). Returns the sent message id, or None if not connected / failed.
    Used only where the user has explicitly enabled an auto-send feature."""
    if not (to or "").strip() or not account_for_user(uid):
        return None
    msg = MIMEText(body or "", "plain", "utf-8")
    msg["To"] = (to or "").strip()
    msg["Subject"] = subject or ""
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    res = _api_post(uid, "/messages/send", {"raw": raw})
    return res.get("id") if res else None


@bp.route("/draft", methods=["POST"])
def draft():
    """Create a Gmail DRAFT (never auto-send — house rule). Supports a fresh compose
    or a reply (pass threadId + in_reply_to + references to keep the thread)."""
    uid = _current_uid()
    if not account_for_user(uid):
        return jsonify({"ok": False, "error": "not connected"}), 200
    data = request.get_json(silent=True) or {}
    did = create_draft(uid, data.get("to"), data.get("subject"), data.get("body"),
                       in_reply_to=data.get("in_reply_to"), references=data.get("references"),
                       thread_id=data.get("threadId"))
    if did:
        return jsonify({"ok": True, "draft_id": did})
    return jsonify({"ok": False, "error": "draft failed"}), 200
