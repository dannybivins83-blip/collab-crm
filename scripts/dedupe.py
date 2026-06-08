# -*- coding: utf-8 -*-
"""Surgical de-dupe of AccuLynx-synced records in the live (Neon) DB.
- Jobs: collapse duplicates that share the same AccuLynx GUID (keep lowest id).
        (Never dedupe jobs by name — a customer can have several distinct jobs.)
- Leads: collapse duplicate GUIDs, then duplicate names (keep lowest id).
Prints what it removes; keeps exactly one of each."""
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
for line in open(os.path.join(HERE, ".env.production"), encoding="utf-8-sig"):
    line = line.strip()
    if line.startswith("DATABASE_URL="):
        u = line.split("=", 1)[1].strip().strip('"').strip("'")
        os.environ["DATABASE_URL"] = re.sub(r"\s", "", u.replace("\\r", "").replace("\\n", "")).replace("﻿", "")
        break
os.environ["CRM_NOBROWSER"] = "1"
import db


def guid(u):
    m = re.search(r"/jobs/([0-9a-f-]{30,})", u or "")
    return m.group(1) if m else None


def dedupe(table, keys_fn, label):
    rows = db.all_rows(table, order="id ASC")
    seen, to_delete = set(), []
    for r in rows:
        k = keys_fn(r)
        if k is None:
            continue
        if k in seen:
            to_delete.append(r["id"])
        else:
            seen.add(k)
    print("%s: %d rows -> removing %d %s duplicates" % (table, len(rows), len(to_delete), label))
    for rid in to_delete:
        db.delete(table, rid)
    return len(to_delete)


print("BEFORE — leads:%d jobs:%d" % (len(db.all_rows("leads")), len(db.all_rows("jobs"))))

# Jobs: by GUID only.
dedupe("jobs", lambda r: guid(r.get("external_url")), "GUID")

# Leads: by GUID, then by name.
dedupe("leads", lambda r: guid(r.get("external_url")), "GUID")
dedupe("leads", lambda r: (r.get("name") or "").strip().lower() or None, "name")

print("AFTER  — leads:%d jobs:%d" % (len(db.all_rows("leads")), len(db.all_rows("jobs"))))
