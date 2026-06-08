# -*- coding: utf-8 -*-
"""Backfill: upload existing local files (library product sheets, photos, job
documents) to Google Drive and store their drive_id, so they download/display on
the live serverless site. Run AFTER setting GDRIVE_SA_JSON + GDRIVE_FOLDER_ID.

Usage:
    set GDRIVE_SA_JSON / GDRIVE_FOLDER_ID (and DATABASE_URL for the live Neon DB)
    python scripts/push_files_to_drive.py
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

# Load DATABASE_URL from .env.production if present (so it targets the live DB).
envf = os.path.join(HERE, ".env.production")
if os.path.exists(envf) and not os.environ.get("DATABASE_URL"):
    for line in open(envf, encoding="utf-8-sig"):
        line = line.strip()
        if line.startswith("DATABASE_URL="):
            u = line.split("=", 1)[1].strip().strip('"').strip("'")
            os.environ["DATABASE_URL"] = re.sub(r"\s", "", u.replace("\\r", "").replace("\\n", "")).replace("﻿", "")
            break
os.environ["CRM_NOBROWSER"] = "1"

import config
import db
from modules import gdrive

if not gdrive.enabled():
    sys.exit("GDRIVE_SA_JSON + GDRIVE_FOLDER_ID not set — configure them first.")

PLAN = [("library_docs", os.path.join(config.UPLOAD_DIR, "library")),
        ("documents", config.DOC_DIR),
        ("photos", config.PHOTO_DIR)]

for table, folder in PLAN:
    rows = db.all_rows(table)
    done = skip = miss = 0
    for r in rows:
        if r.get("drive_id"):
            skip += 1
            continue
        path = os.path.join(folder, r.get("filename") or "")
        if not r.get("filename") or not os.path.exists(path):
            miss += 1
            continue
        did = gdrive.mirror(path, r["filename"])
        if did:
            db.update(table, r["id"], drive_id=did)
            done += 1
    print("%-14s uploaded=%d already=%d missing-local=%d" % (table, done, skip, miss))

print("Done. New uploads auto-mirror; the live site now serves these from Drive.")
