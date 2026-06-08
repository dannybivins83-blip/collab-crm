# -*- coding: utf-8 -*-
"""Vercel serverless entrypoint for the white-label CRM.

Vercel's Python runtime serves the WSGI `app` exposed here. The app's filesystem
is read-only except /tmp, so the SQLite DB + uploads live there (ephemeral — resets
on cold starts). This makes the live demo reachable; for persistent data, deploy to
a disk host (see DEPLOY.md) instead.
"""
import os
import sys

# Make the CRM package importable (api/ is one level below the app root).
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# Route all writable state to /tmp (the only writable dir on Vercel).
os.environ.setdefault("CRM_DB_PATH", "/tmp/crm/crm.db")
os.environ.setdefault("CRM_DATA_DIR", "/tmp/crm")
os.environ.setdefault("CRM_UPLOAD_DIR", "/tmp/crm/uploads")
os.environ.setdefault("CRM_NOBROWSER", "1")
os.environ.setdefault("CRM_SECRET", "collab-crm-demo-secret-change-me")

from app import app  # noqa: E402  (WSGI callable Vercel serves)
