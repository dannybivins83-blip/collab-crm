# -*- coding: utf-8 -*-
"""One-time data migration: Neon Postgres  ->  a single SQLite file (crm.db).

This is the only step in the Render+SQLite consolidation that needs Neon reachable.
It (1) builds a fresh SQLite DB with the app's FULL schema by importing the app in
SQLite mode, then (2) copies every table from Neon into it, preserving primary keys so
all job_id/lead_id foreign references stay valid.

Run it when Neon is reachable (i.e. after the transfer quota resets or the plan is
upgraded briefly):

    # NEON URL can come from the env (DATABASE_URL) or be pasted as arg 1.
    python scripts/neon_to_sqlite.py [NEON_URL] [TARGET_SQLITE_PATH]

Default target: ./data/crm.db  (upload that file to Render's /data disk, or restore it).
"""
import os
import sys
import json
import sqlite3
import subprocess

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    neon_url = (sys.argv[1] if len(sys.argv) > 1 else "") or os.environ.get("DATABASE_URL", "")
    target = (sys.argv[2] if len(sys.argv) > 2 else "") or os.path.join(HERE, "data", "crm.db")
    if not neon_url:
        sys.exit("Provide the Neon URL as arg 1 or in DATABASE_URL.")
    target = os.path.abspath(target)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    if os.path.exists(target):
        sys.exit("Target %s already exists — move it aside first (won't overwrite)." % target)

    # 1) Build the full SQLite schema by importing the app in SQLite mode (no DATABASE_URL).
    print("Building SQLite schema at %s ..." % target)
    env = dict(os.environ)
    # Force SQLite: set to EMPTY (not unset) so config.py's .env loader can't re-inject
    # a DATABASE_URL via setdefault. Empty string -> db.IS_PG is False -> SQLite.
    env["DATABASE_URL"] = ""
    env["POSTGRES_URL"] = ""
    env["CRM_DB_PATH"] = target
    env["CRM_NOBROWSER"] = "1"
    r = subprocess.run([sys.executable, "-c", "import app; print('schema-ok')"],
                       cwd=HERE, env=env, capture_output=True, text=True)
    if "schema-ok" not in (r.stdout or ""):
        sys.exit("Schema build failed:\n%s\n%s" % (r.stdout, r.stderr[-1500:]))

    # 2) Copy every table from Neon into the SQLite file (intersection of columns).
    import psycopg
    neon = psycopg.connect(neon_url)
    sl = sqlite3.connect(target)
    tables = [r[0] for r in neon.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name").fetchall()]
    total = 0
    for t in tables:
        try:
            sl_cols = {row[1] for row in sl.execute("PRAGMA table_info(%s)" % t).fetchall()}
            if not sl_cols:
                print("  skip %-22s (no such table in SQLite schema)" % t)
                continue
            cur = neon.execute("SELECT * FROM %s" % t)
            pg_cols = [d.name for d in cur.description]
            # Add any Neon column the SQLite table is missing (e.g. lazily-added columns
            # like design_selection) so NO data is dropped. SQLite is dynamically typed.
            for c in pg_cols:
                if c not in sl_cols:
                    try:
                        sl.execute("ALTER TABLE %s ADD COLUMN %s" % (t, c))
                        sl_cols.add(c)
                    except Exception:
                        pass
            use = [c for c in pg_cols if c in sl_cols]
            rows = cur.fetchall()
            sl.execute("DELETE FROM %s" % t)            # clear any seed rows
            if rows:
                idx = [pg_cols.index(c) for c in use]
                ph = ",".join("?" * len(use))
                data = []
                for row in rows:
                    vals = []
                    for i in idx:
                        v = row[i]
                        # SQLite can't store dict/list/bool natively -> normalize.
                        if isinstance(v, (dict, list)):
                            v = json.dumps(v)
                        elif isinstance(v, bool):
                            v = 1 if v else 0
                        vals.append(v)
                    data.append(vals)
                sl.executemany("INSERT INTO %s (%s) VALUES (%s)" % (t, ",".join(use), ph), data)
            sl.commit()
            total += len(rows)
            print("  %-22s %6d rows" % (t, len(rows)))
        except Exception as e:
            print("  ERROR on %s: %s" % (t, e))
    sl.close()
    neon.close()
    print("\nDone. %d rows -> %s" % (total, target))
    print("Next: upload this file to Render's /data disk as crm.db (Shell or scp), then")
    print("the service runs entirely on SQLite — no Neon, no Vercel.")


if __name__ == "__main__":
    main()
