# -*- coding: utf-8 -*-
"""Cloudflare R2 storage backend — drop-in replacement / companion to gdrive.py.

Activation (no code change needed):
  - R2_ACCOUNT_ID           = Cloudflare account ID (32-char hex, from R2 dashboard)
  - R2_ACCESS_KEY_ID        = R2 API token access key
  - R2_SECRET_ACCESS_KEY    = R2 API token secret
  - R2_BUCKET_NAME          = bucket name (e.g. "crm-files")

When unset, enabled() is False and callers fall back to Drive / local disk.

R2 is S3-compatible; we use boto3 with the Cloudflare endpoint.
Public access is off — all reads go through the server (download() proxies bytes).
"""
import os
import mimetypes

_client = None


def _cfg():
    return {
        "account_id":   (os.environ.get("R2_ACCOUNT_ID")         or "").strip(),
        "access_key":   (os.environ.get("R2_ACCESS_KEY_ID")      or "").strip(),
        "secret_key":   (os.environ.get("R2_SECRET_ACCESS_KEY")  or "").strip(),
        "bucket":       (os.environ.get("R2_BUCKET_NAME")        or "").strip(),
    }


def enabled():
    c = _cfg()
    return bool(c["account_id"] and c["access_key"] and c["secret_key"] and c["bucket"])


def _client_():
    global _client
    if _client is not None:
        return _client
    import boto3
    c = _cfg()
    _client = boto3.client(
        "s3",
        endpoint_url="https://%s.r2.cloudflarestorage.com" % c["account_id"],
        aws_access_key_id=c["access_key"],
        aws_secret_access_key=c["secret_key"],
        region_name="auto",
    )
    return _client


def upload(name, data, mime="application/octet-stream"):
    """Upload bytes to R2. Returns the object key (name) on success, else None."""
    if not enabled():
        return None
    try:
        _client_().put_object(
            Bucket=_cfg()["bucket"],
            Key=name,
            Body=data,
            ContentType=mime or "application/octet-stream",
        )
        return name
    except Exception:
        return None


def download(key):
    """Fetch an R2 object's bytes. Returns bytes or None."""
    if not enabled() or not key:
        return None
    try:
        resp = _client_().get_object(Bucket=_cfg()["bucket"], Key=key)
        return resp["Body"].read()
    except Exception:
        return None


def mirror(path, name=None):
    """Upload a local file to R2. Returns the object key or None."""
    if not os.path.exists(path):
        return None
    name = name or os.path.basename(path)
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except Exception:
        return None
    return upload(name, data, mime)


def serve_fallback(subpath):
    """Return (bytes, mimetype) for a file keyed by basename, or None."""
    key = os.path.basename(subpath)
    if not key:
        return None
    data = download(key)
    if data is None:
        return None
    mime = mimetypes.guess_type(subpath)[0] or "application/octet-stream"
    return data, mime


def delete(key):
    """Delete an object from R2. Best-effort, never raises."""
    if not enabled() or not key:
        return
    try:
        _client_().delete_object(Bucket=_cfg()["bucket"], Key=key)
    except Exception:
        pass
