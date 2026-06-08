# -*- coding: utf-8 -*-
"""One-time migration: copy the local SQLite CRM into Neon Postgres.

Usage (PowerShell):
    $env:DATABASE_URL = "postgresql://user:pass@...-pooler.neon.tech/neondb?sslmode=require"
    python migrate_to_neon.py

It (1) creates the full schema in Neon, (2) wipes the seeded sample rows, then
(3) copies every row from local data/crm.db into Neon preserving primary-key ids,
and (4) resets the id sequences. Safe to re-run (idempotent: wipes + recopies).
The local SQLite file is only read, never modified.
"""
import os
import sqlite3
import sys

import config

PG_URL = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")
if not PG_URL:
    sys.exit("ERROR: set DATABASE_URL to your Neon connection string first.")

# Importing db with DATABASE_URL set puts it in Postgres mode.
import db
assert db.IS_PG, "db did not detect Postgres — check DATABASE_URL"
import psycopg
from psycopg.rows import dict_row

SQLITE_PATH = config.DB_PATH if not os.environ.get("CRM_DB_PATH") else config.DB_PATH
# Always read the local on-disk SQLite (ignore any DB_PATH override pointing elsewhere).
LOCAL = os.path.join(config.HERE, "data", "crm.db")
if not os.path.exists(LOCAL):
    sys.exit("ERROR: local SQLite not found at %s" % LOCAL)

print("Local source :", LOCAL)
print("Neon target  :", PG_URL.split("@")[-1])

# 1) Build the schema (+ seed) in Neon.
print("\n[1/4] Creating schema in Neon …")
db.init_db()

# 2/3) Copy table-by-table.
slite = sqlite3.connect(LOCAL)
slite.row_factory = sqlite3.Row
tables = [r[0] for r in slite.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]

pg = psycopg.connect(PG_URL)
copied = {}
with pg.cursor(row_factory=dict_row) as cur:
    for t in tables:
        # columns common to both engines
        scols = [r[1] for r in slite.execute("PRAGMA table_info(%s)" % t)]
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name=%s", (t,))
        pgcols = {r["column_name"] for r in cur.fetchall()}
        cols = [c for c in scols if c in pgcols]
        if not cols:
            continue
        rows = slite.execute("SELECT * FROM %s" % t).fetchall()
        cur.execute("DELETE FROM %s" % t)  # remove seeded sample rows
        if rows:
            ph = ",".join(["%s"] * len(cols))
            collist = ",".join(cols)
            for r in rows:
                cur.execute("INSERT INTO %s (%s) VALUES (%s)" % (t, collist, ph),
                            tuple(r[c] for c in cols))
        copied[t] = len(rows)
    # 4) reset id sequences
    for t in tables:
        try:
            cur.execute("SELECT pg_get_serial_sequence(%s, 'id')", (t,))
            seq = cur.fetchone()["pg_get_serial_sequence"]
            if seq:
                cur.execute("SELECT setval(%s, GREATEST((SELECT COALESCE(MAX(id),1) FROM %s), 1))"
                            % ("%s", t), (seq,))
        except Exception as e:
            print("  (seq skip %s: %s)" % (t, e))
pg.commit()
pg.close()

print("\n[done] Rows copied into Neon:")
for t in sorted(copied):
    if copied[t]:
        print("  %-18s %d" % (t, copied[t]))
print("\nNeon now holds your real CRM. Set DATABASE_URL on the Vercel project and redeploy.")
