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

# Neon/Postgres-backed blob store for SMALL files so they persist + download on
# serverless (Vercel) with no external account. Files above MAX_BLOB (Vercel's
# ~4.5 MB response cap) are skipped — those need a disk host (Render) or a CDN.
MAX_BLOB = 4_000_000
try:
    db.execute("CREATE TABLE IF NOT EXISTS file_blobs (filename TEXT PRIMARY KEY, mime TEXT, data %s)"
               % ("BYTEA" if getattr(db, "IS_PG", False) else "BLOB"))
except Exception:
    pass
db._COLCACHE.clear()


def blob_put(filename, data, mime="application/octet-stream"):
    """Store small file bytes in Postgres/SQLite. Returns True if stored."""
    if not filename or data is None or len(data) > MAX_BLOB:
        return False
    fn = os.path.basename(filename)
    try:
        db.execute("DELETE FROM file_blobs WHERE filename=?", (fn,))
        db.execute("INSERT INTO file_blobs (filename, mime, data) VALUES (?,?,?)",
                   (fn, mime or "application/octet-stream", data))
        return True
    except Exception:
        return False


def blob_get(filename):
    """Return (bytes, mime) for a stored blob, or None."""
    fn = os.path.basename(filename or "")
    if not fn:
        return None
    try:
        conn = db.connect()
        row = conn.execute("SELECT mime, data FROM file_blobs WHERE filename=?", (fn,)).fetchone()
        conn.close()
    except Exception:
        return None
    if not row:
        return None
    d = row["data"]
    if isinstance(d, memoryview):
        d = bytes(d)
    return bytes(d), row["mime"]


def folder_id():
    v = (os.environ.get("GDRIVE_FOLDER_ID") or "").strip().lstrip("﻿")
    return "".join(v.split()).encode("ascii", "ignore").decode()


def _sa_info():
    raw = (os.environ.get("GDRIVE_SA_JSON") or "").strip().lstrip("﻿")
    if not raw:
        return None
    try:  # raw JSON
        return json.loads(raw)
    except Exception:
        pass
    try:  # base64-encoded JSON — sanitize any BOM/whitespace/non-ASCII the host added
        cleaned = "".join(raw.split()).encode("ascii", "ignore").decode()
        return json.loads(base64.b64decode(cleaned))
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
            "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id&supportsAllDrives=true",
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
        r = requests.get("https://www.googleapis.com/drive/v3/files/%s?alt=media&supportsAllDrives=true" % file_id,
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
    """Persist a just-saved file so it survives/serves on serverless. Prefer Google
    Drive (scales); fall back to the Neon blob store ONLY when Drive isn't configured,
    so we never fill the 512 MB Postgres cap with file bytes. Returns the Drive id."""
    if not os.path.exists(path):
        return None
    name = name or os.path.basename(path)
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except Exception:
        return None
    if enabled():
        did = upload(name, data, mime)   # Drive (scales) — no Neon blob copy when this works
        if did:
            return did
    blob_put(name, data, mime)           # fallback ONLY: Drive off, or the upload failed
    return None


def _drive_id_by_name(name):
    """Search the shared folder for a file by exact name; return its id or None.
    Lets us find any mirrored file without bookkeeping a drive_id column."""
    if not enabled() or not name:
        return None
    try:
        import requests
        q = "name = '%s' and '%s' in parents and trashed = false" % (
            name.replace("'", "\\'"), folder_id())
        r = requests.get("https://www.googleapis.com/drive/v3/files", params={
            "q": q, "fields": "files(id)", "pageSize": 1,
            "supportsAllDrives": "true", "includeItemsFromAllDrives": "true",
            "corpora": "allDrives"},
            headers={"Authorization": "Bearer " + _token()}, timeout=30)
        if r.ok:
            files = r.json().get("files", [])
            if files:
                return files[0]["id"]
    except Exception:
        pass
    return None


def find_drive_id(filename):
    """Look up a file's Drive id: stored drive_id column first, then a live name
    search of the shared folder (covers backfilled / un-bookkept files)."""
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
    return _drive_id_by_name(base)


def backfill_local():
    """Upload every existing local file under uploads/ to Drive (idempotent: skips
    names already present). Run once from the desktop app after Drive is configured."""
    import config
    if not enabled():
        return {"ok": False, "error": "Drive not configured"}
    pushed = skipped = 0
    for root, _dirs, files in os.walk(config.UPLOAD_DIR):
        for fn in files:
            if fn.startswith("."):
                continue
            if _drive_id_by_name(fn):
                skipped += 1
                continue
            if mirror(os.path.join(root, fn), fn):
                pushed += 1
    return {"ok": True, "pushed": pushed, "skipped": skipped}


def serve_fallback(subpath):
    """If a local upload is missing, serve it from: R2 (preferred) → blob store → Drive.
    Returns (bytes, mimetype) or None."""
    base = os.path.basename(subpath)
    # 1. R2 (fast, no SA JSON needed)
    try:
        from modules import r2 as _r2
        if _r2.enabled():
            result = _r2.serve_fallback(subpath)
            if result:
                return result
    except Exception:
        pass
    # 2. SQLite/Neon blob store (small files, legacy)
    b = blob_get(base)
    if b:
        return b
    # 3. Google Drive (legacy SA-key path)
    did = find_drive_id(base)
    if did:
        data = download(did)
        if data is not None:
            return data, mimetypes.guess_type(subpath)[0] or "application/octet-stream"
    return None
