# -*- coding: utf-8 -*-
"""Sync the LOCAL SQLite DB down from the LIVE CRM before running locally.

Pulls a consistent snapshot from the live host's token-gated /admin/db-download
(mirror of /admin/db-restore; 404 unless DB_RESTORE_TOKEN is armed and presented),
validates it, then atomically swaps it in as data/crm.db with a .bak of the old
file kept beside it.

Used two ways:
  * `python scripts/sync_live_db.py`      — manual refresh
  * imported by scripts/run_local.py      — auto-refresh on every desktop launch
    (skip with CRM_NO_SYNC=1; failure never blocks the launch — the app just
    runs on the existing local copy).

Token comes from secrets/keys.local.env (DB_RESTORE_TOKEN) or the environment.
Values are never printed. Override the host with CRM_LIVE_URL if needed.
"""
import os
import sqlite3
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_LIVE = "https://collab-crm-bwsl.onrender.com"
MIN_JOBS = 1000  # same floor as dbadmin: refuse a tiny/empty snapshot


def _load_token():
    tok = (os.environ.get("DB_RESTORE_TOKEN") or "").strip()
    if tok:
        return tok
    sys.path.insert(0, HERE)
    try:
        from run_local import load_secrets
        kv = load_secrets(os.path.join(ROOT, "secrets", "keys.local.env"))
        return (kv.get("DB_RESTORE_TOKEN") or "").strip() or None
    except Exception:
        return None


def _jobs_count(path):
    try:
        con = sqlite3.connect(path)
        try:
            return con.execute("SELECT count(*) FROM jobs").fetchone()[0]
        finally:
            con.close()
    except Exception:
        return None


def _validate(path):
    """(ok, reason, jobs) — integrity + required tables + sane row count."""
    try:
        con = sqlite3.connect(path)
    except Exception as exc:
        return False, "not sqlite: %s" % exc, 0
    try:
        chk = con.execute("PRAGMA integrity_check").fetchone()
        if not chk or str(chk[0]).lower() != "ok":
            return False, "integrity_check failed", 0
        names = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        for required in ("jobs", "contacts"):
            if required not in names:
                return False, "missing table %s" % required, 0
        jobs = con.execute("SELECT count(*) FROM jobs").fetchone()[0]
        if jobs < MIN_JOBS:
            return False, "jobs %d < %d (refusing tiny snapshot)" % (jobs, MIN_JOBS), jobs
        return True, None, jobs
    except Exception as exc:
        return False, str(exc), 0
    finally:
        con.close()


def sync(timeout=90, quiet=False):
    """Refresh data/crm.db from the live host. Returns (ok, message)."""
    def say(msg):
        if not quiet:
            print("  db-sync: %s" % msg)

    token = _load_token()
    if not token:
        return False, "DB_RESTORE_TOKEN not available (env or secrets/keys.local.env)"
    live = (os.environ.get("CRM_LIVE_URL") or DEFAULT_LIVE).rstrip("/")

    db_path = os.path.join(ROOT, "data", "crm.db")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    tmp = "%s.sync.%d.tmp" % (db_path, int(time.time()))

    try:
        req = urllib.request.Request(live + "/admin/db-download",
                                     headers={"X-Restore-Token": token,
                                              "User-Agent": "crm-local-sync"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            with open(tmp, "wb") as out:
                while True:
                    chunk = r.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
    except Exception as exc:
        _unlink(tmp)
        return False, "download failed: %s" % exc

    valid, reason, new_jobs = _validate(tmp)
    if not valid:
        _unlink(tmp)
        return False, "snapshot rejected: %s" % reason

    old_jobs = _jobs_count(db_path) if os.path.exists(db_path) else None
    if os.path.exists(db_path):
        bak = db_path + ".bak"
        try:
            os.replace(db_path, bak)
        except Exception as exc:
            _unlink(tmp)
            return False, "could not back up current local DB: %s" % exc
    try:
        os.replace(tmp, db_path)
    except Exception as exc:
        _unlink(tmp)
        return False, "swap failed: %s" % exc
    # Drop stale WAL/SHM sidecars from the OLD local db.
    for sidecar in (db_path + "-wal", db_path + "-shm"):
        _unlink(sidecar)

    say("local data/crm.db refreshed from live (jobs %s -> %s)" % (old_jobs, new_jobs))
    return True, "synced: jobs %s -> %s" % (old_jobs, new_jobs)


def _unlink(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


if __name__ == "__main__":
    ok, msg = sync()
    print(("OK  " if ok else "FAIL ") + msg)
    sys.exit(0 if ok else 1)
