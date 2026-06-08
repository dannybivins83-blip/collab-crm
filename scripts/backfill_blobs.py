# -*- coding: utf-8 -*-
"""Backfill existing small upload files into the Neon blob store so they download
on the live (serverless) site. Files larger than gdrive.MAX_BLOB are skipped
(they need a disk host / CDN)."""
import os
import re
import sys
import mimetypes

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
for line in open(os.path.join(HERE, ".env.production"), encoding="utf-8-sig"):
    line = line.strip()
    if line.startswith("DATABASE_URL="):
        u = line.split("=", 1)[1].strip().strip('"').strip("'")
        os.environ["DATABASE_URL"] = re.sub(r"\s", "", u.replace("\\r", "").replace("\\n", "")).replace("﻿", "")
        break
os.environ["CRM_NOBROWSER"] = "1"

import config
import db
from modules import gdrive

assert getattr(db, "IS_PG", False), "Not connected to Postgres — aborting."
print("Blob store target: Neon  |  MAX_BLOB = %d bytes" % gdrive.MAX_BLOB)

stored = skipped_big = total_bytes = 0
for root, _dirs, files in os.walk(config.UPLOAD_DIR):
    for fn in files:
        if fn.startswith("."):
            continue
        p = os.path.join(root, fn)
        try:
            sz = os.path.getsize(p)
        except Exception:
            continue
        if sz > gdrive.MAX_BLOB:
            skipped_big += 1
            continue
        with open(p, "rb") as fh:
            data = fh.read()
        mime = mimetypes.guess_type(fn)[0] or "application/octet-stream"
        if gdrive.blob_put(fn, data, mime):
            stored += 1
            total_bytes += sz

print("stored: %d small files (%.1f MB) | skipped (too big for serverless): %d"
      % (stored, total_bytes / 1e6, skipped_big))
