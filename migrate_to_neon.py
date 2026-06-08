# -*- coding: utf-8 -*-
"""One-time migration: copy the local SQLite CRM into Neon Postgres.

Usage (PowerShell):
    $env:DATABASE_URL = "postgresql://...-pooler...neon.tech/neondb?sslmode=require"
    python migrate_to_neon.py

Steps: (1) build the COMPLETE schema in Neon by importing the app (this runs every
module's CREATE TABLE + auth's password_hash column), (2) make sure Neon has every
column the local DB has, (3) wipe seeded sample rows, (4) copy every row from local
data/crm.db preserving ids, (5) reset id sequences. Reads local SQLite only; never
writes to it. Uses a DIRECT (non-pooled) connection for the migration itself.
"""
import os
import sqlite3
import sys

import config

PG_URL = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")
if not PG_URL:
    sys.exit("ERROR: set DATABASE_URL to your Neon connection string first.")

# A one-shot bulk migration is happiest on a DIRECT (non-pooled) connection.
if "-pooler" in PG_URL:
    os.environ["DATABASE_URL"] = PG_URL.replace("-pooler", "")
PG_URL = os.environ["DATABASE_URL"]

LOCAL = os.path.join(config.HERE, "data", "crm.db")
if not os.path.exists(LOCAL):
    sys.exit("ERROR: local SQLite not found at %s" % LOCAL)
print("Local source :", LOCAL)
print("Neon target  :", PG_URL.split("@")[-1].split("?")[0])

# 1) Build the FULL schema in Neon. Importing the app runs db.init_db() plus every
#    module's top-level CREATE TABLE and auth's password_hash ALTER — against Neon.
print("\n[1/5] Building full schema in Neon (importing app) …")
os.environ.setdefault("CRM_NOBROWSER", "1")
import db
assert db.IS_PG, "db did not switch to Postgres — check DATABASE_URL"
import app  # noqa: F401  (side effect: creates all tables in Neon)
from modules import auth as _auth
_auth._ensure_schema()  # ensure users.password_hash exists in Neon

import psycopg
from psycopg.rows import dict_row

slite = sqlite3.connect(LOCAL)
slite.row_factory = sqlite3.Row
tables = [r[0] for r in slite.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]

pg = psycopg.connect(PG_URL)
copied = {}
with pg.cursor(row_factory=dict_row) as cur:
    # 2) make sure Neon has every column SQLite has (covers runtime-added columns)
    print("[2/5] Reconciling columns …")
    for t in tables:
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name=%s", (t,))
        pgcols = {r["column_name"] for r in cur.fetchall()}
        if not pgcols:
            print("   (table %s missing in Neon — skipping)" % t)
            continue
        for row in slite.execute("PRAGMA table_info(%s)" % t):
            name, ctype = row[1], (row[2] or "TEXT")
            if name not in pgcols:
                try:
                    cur.execute('ALTER TABLE %s ADD COLUMN "%s" %s' % (t, name, ctype))
                except Exception as e:
                    print("   (add %s.%s failed: %s)" % (t, name, e))

    # 3) + 4) wipe seeds, copy real rows preserving ids
    print("[3/5] Wiping seed rows + [4/5] copying real data …")
    NUMERIC = {"integer", "bigint", "smallint", "numeric", "real", "double precision",
               "boolean"}
    for t in tables:
        cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name=%s", (t,))
        types = {r["column_name"]: r["data_type"] for r in cur.fetchall()}
        if not types:
            continue
        scols = [r[1] for r in slite.execute("PRAGMA table_info(%s)" % t)]
        cols = [c for c in scols if c in types]

        def coerce(c, v):
            # Postgres rejects '' for numeric/bool columns; SQLite stored it loosely.
            if v == "" and types.get(c) in NUMERIC:
                return None
            if types.get(c) == "boolean" and v in (0, 1, "0", "1"):
                return bool(int(v))
            return v

        rows = slite.execute("SELECT * FROM %s" % t).fetchall()
        cur.execute("DELETE FROM %s" % t)
        if rows and cols:
            collist = ",".join('"%s"' % c for c in cols)
            ph = ",".join(["%s"] * len(cols))
            for r in rows:
                cur.execute("INSERT INTO %s (%s) VALUES (%s)" % (t, collist, ph),
                            tuple(coerce(c, r[c]) for c in cols))
        copied[t] = len(rows)

    # 5) reset id sequences so new inserts don't collide
    print("[5/5] Resetting id sequences …")
    for t in tables:
        try:
            cur.execute("SELECT pg_get_serial_sequence(%s, 'id') AS seq", (t,))
            seq = cur.fetchone()["seq"]
            if seq:
                cur.execute("SELECT setval(%s, GREATEST((SELECT COALESCE(MAX(id),1) FROM " + t + "), 1))", (seq,))
        except Exception as e:
            print("   (seq %s skip: %s)" % (t, e))

pg.commit()
pg.close()

print("\n[done] Rows now in Neon:")
for t in sorted(copied):
    if copied[t]:
        print("  %-18s %d" % (t, copied[t]))
print("\nNext: add DATABASE_URL (the POOLED url) to the Vercel project and redeploy.")
