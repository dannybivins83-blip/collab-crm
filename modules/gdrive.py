# -*- coding: utf-8 -*-
"""Google Drive storage backend for uploaded files (photos, documents, product
collateral, homeowner uploads). Lets files persist on serverless hosts (Vercel)
and live in the company's own Drive.

Activation (no code change needed):
  - GDRIVE_SA_JSON      = the Google service-account key JSON (raw or base64)
  - GDRIVE_FOLDER_ID    = the ID of a Drive folder shared with that service account
When unset, enabled() is False and the app uses local disk as before.

Uses google-auth (for the SA token) + requests (Drive REST) — no heavy client.
"""
import os
import json
import time
import base64
import mimetypes

import db

_TOKEN = {"value": None, "exp": 0}

# Tables whose files we mirror to Drive (each gets a drive_id column).
_FILE_TABLES = ("documents", "photos", "library_docs")
for _t in _FILE_TABLES:
    try:
        db.execute("ALTER TABLE %s ADD COLUMN drive_id TEXT" % _t)
    except Exception:
        pass
db._COLCACHE.clear()


def folder_id():
    return (os.environ.get("GDRIVE_FOLDER_ID") or "").strip()


def _sa_info():
    raw = (os.environ.get("GDRIVE_SA_JSON") or "").strip()
    if not raw:
        return None
    for attempt in (raw, ):
        try:
            return json.loads(attempt)
        except Exception:
            pass
    try:  # allow base64-encoded JSON (easier to paste as one env line)
        return json.loads(base64.b64decode(raw))
    except Exception:
        return None


def enabled():
    return bool(_sa_info() and folder_id())


def _token():
    if _TOKEN["value"] and time.time() < _TOKEN["exp"] - 60:
        return _TOKEN["value"]
    from google.oauth2 import service_account
    import google.auth.transport.requests as gr
    creds = service_account.Credentials.from_service_account_info(
        _sa_info(), scopes=["https://www.googleapis.com/auth/drive"])
    creds.refresh(gr.Request())
    _TOKEN["value"] = creds.token
    _TOKEN["exp"] = time.time() + 3300  # ~55 min
    return creds.token


def upload(name, data, mime="application/octet-stream"):
    """Upload bytes to the shared Drive folder. Returns the Drive file id or None."""
    if not enabled():
        return None
    try:
        import requests
        meta = {"name": name, "parents": [folder_id()]}
        files = {
            "metadata": ("metadata", json.dumps(meta), "application/json"),
            "file": (name, data, mime),
        }
        r = requests.post(
            "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id",
            headers={"Authorization": "Bearer " + _token()}, files=files, timeout=90)
        if r.ok:
            return r.json().get("id")
    except Exception:
        pass
    return None


def download(file_id):
    """Fetch a Drive file's bytes (for proxying). Returns bytes or None."""
    if not file_id or not enabled():
        return None
    try:
        import requests
        r = requests.get("https://www.googleapis.com/drive/v3/files/%s?alt=media" % file_id,
                         headers={"Authorization": "Bearer " + _token()}, timeout=90)
        if r.ok:
            return r.content
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Convenience used by upload routes + the /uploads serve fallback
# ---------------------------------------------------------------------------

def mirror(path, name=None):
    """Upload a just-saved local file to Drive; return its Drive id (or None)."""
    if not enabled() or not os.path.exists(path):
        return None
    name = name or os.path.basename(path)
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    try:
        with open(path, "rb") as fh:
            return upload(name, fh.read(), mime)
    except Exception:
        return None


def find_drive_id(filename):
    """Look up a file's Drive id by stored filename across the file tables."""
    base = os.path.basename(filename or "")
    if not base:
        return None
    for t in _FILE_TABLES:
        try:
            rows = db.all_rows(t, "filename=?", (base,))
        except Exception:
            rows = []
        if rows and rows[0].get("drive_id"):
            return rows[0]["drive_id"]
    return None


def serve_fallback(subpath):
    """If a local upload is missing (e.g. on serverless), fetch it from Drive.
    Returns (bytes, mimetype) or None."""
    did = find_drive_id(os.path.basename(subpath))
    if not did:
        return None
    data = download(did)
    if data is None:
        return None
    mime = mimetypes.guess_type(subpath)[0] or "application/octet-stream"
    return data, mime
