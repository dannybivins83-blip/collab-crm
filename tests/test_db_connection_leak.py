# -*- coding: utf-8 -*-
"""Regression test for db.py connection leak (issue diagnosed during audit #11).

The bug: every write helper in db.py opened `conn = connect()` and assumed
the happy path, calling `conn.commit(); conn.close()` only after the operation
succeeded. If the underlying `conn.execute(...)` raised — e.g. a UNIQUE
violation — the connection was never closed and lingered in the pool. On
SQLite this manifested as cascading "database is locked" on every subsequent
op until the garbage collector reaped the leaked connection. On Postgres the
connection would be permanently leaked from the pool.

`edda6d8` (Jun 12, 2026) wrapped the 6 main write helpers (insert/update/get/
all_rows/delete/execute) in try/finally. This commit extends the same fix to
every remaining connect() caller in db.py — init_db, _ensure_* DDL helpers,
_migrate_* helpers, _columns, _numeric_cols, get/save_integrations,
get/save_company, _ensure_column.

These tests force a write exception on the SAME path the audit-#11 dedupe
hits (insert that violates a UNIQUE constraint) and verify that:
  (a) the exception still propagates,
  (b) the very next op succeeds without a "database is locked" cascade.

Run:
    cd whitelabel-crm
    DATABASE_URL="" CRM_NOBROWSER=1 CRM_PORT=5097 python tests/test_db_connection_leak.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import uuid
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_TMP_DB = Path(tempfile.gettempdir()) / f"crm_test_dbleak_{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = ""
os.environ["CRM_NOBROWSER"] = "1"
os.environ["CRM_DB_PATH"] = str(_TMP_DB)
os.environ["CRM_PORT"] = os.environ.get("CRM_PORT", "5097")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# pylint: disable=wrong-import-position
import app as crm_app   # noqa: E402  -- triggers init_db() and module-load DDL
import db               # noqa: E402


# ----------------------------------------------------------------------
# TEST A — INSERT that raises a UNIQUE violation does NOT leak the conn;
#          the next op succeeds without a "database is locked" cascade.
# ----------------------------------------------------------------------
def test_insert_unique_violation_releases_connection():
    # Build a small table with a UNIQUE constraint we can violate at will.
    db.execute(
        "CREATE TABLE IF NOT EXISTS _leaktest ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE)")
    key = f"k_{uuid.uuid4().hex[:8]}"
    db.insert("_leaktest", {"key": key})
    raised = False
    try:
        db.insert("_leaktest", {"key": key})   # duplicate -> IntegrityError
    except Exception:
        raised = True
    assert raised, "expected IntegrityError on duplicate; none raised"
    # The leak would manifest here as "database is locked" — but the next
    # INSERT must succeed because the connection was released in finally:.
    db.insert("_leaktest", {"key": key + "_b"})
    rows = db.all_rows("_leaktest", order="id ASC")
    assert len(rows) == 2, f"expected 2 rows after recovery, got {len(rows)}"
    print("  [PASS] A: UNIQUE-violated INSERT released the connection; next INSERT succeeded")


# ----------------------------------------------------------------------
# TEST B — Same pattern hammered: many consecutive raises then a success.
#          If even ONE conn was leaked per failure, the pool would be
#          exhausted (SQLite) or stuck (Postgres).
# ----------------------------------------------------------------------
def test_repeated_failures_do_not_exhaust_connections():
    db.execute(
        "CREATE TABLE IF NOT EXISTS _leaktest2 ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE)")
    key = f"k_{uuid.uuid4().hex[:8]}"
    db.insert("_leaktest2", {"key": key})
    failures = 0
    for _ in range(25):
        try:
            db.insert("_leaktest2", {"key": key})  # always fails
        except Exception:
            failures += 1
    assert failures == 25, f"expected 25 IntegrityErrors, got {failures}"
    # If we leaked even ONE conn per failure, this final INSERT would lock up.
    db.insert("_leaktest2", {"key": key + "_z"})
    rows = db.all_rows("_leaktest2", order="id ASC")
    assert len(rows) == 2, f"expected 2 rows after 25 failures, got {len(rows)}"
    print("  [PASS] B: 25 consecutive UNIQUE failures + recovery; no connection exhaustion")


# ----------------------------------------------------------------------
# TEST C — Specifically exercise the audit-#11 dedupe path that diagnosed
#          this issue: automation_fires UNIQUE violation must not cascade.
# ----------------------------------------------------------------------
def test_automation_fires_unique_does_not_cascade():
    db.execute(
        "CREATE TABLE IF NOT EXISTS _af_test ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "automation_id INTEGER, entity_type TEXT, entity_id INTEGER, "
        "stage_key TEXT, fire_date TEXT, "
        "UNIQUE(automation_id, entity_type, entity_id, stage_key, fire_date))")
    base = {"automation_id": 1, "entity_type": "job", "entity_id": 99,
            "stage_key": "permit_applied", "fire_date": db.today()}
    db.insert("_af_test", dict(base))
    raised = False
    try:
        db.insert("_af_test", dict(base))
    except Exception:
        raised = True
    assert raised, "expected UNIQUE violation; none raised"
    # The very next call (different stage_key, would-be-distinct row) must
    # succeed. Pre-fix this was the "database is locked" cascade point.
    db.insert("_af_test", {**base, "stage_key": "permit_approved"})
    rows = db.all_rows("_af_test", order="id ASC")
    assert len(rows) == 2, f"expected 2 fire rows after recovery, got {len(rows)}"
    print("  [PASS] C: automation_fires UNIQUE violation -> next row inserts cleanly")


# ----------------------------------------------------------------------
# TEST D — Read helpers that take an exception path (table doesn't exist)
#          also release the connection cleanly.
# ----------------------------------------------------------------------
def test_read_helpers_release_on_error():
    raised = False
    try:
        db.all_rows("_does_not_exist", order="id ASC")
    except Exception:
        raised = True
    assert raised, "expected exception on missing table"
    # Next op on a real table must succeed.
    db.execute(
        "CREATE TABLE IF NOT EXISTS _read_recovery ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, val TEXT)")
    db.insert("_read_recovery", {"val": "ok"})
    rows = db.all_rows("_read_recovery")
    assert len(rows) == 1, f"expected 1 row, got {len(rows)}"
    print("  [PASS] D: all_rows() on missing table released the connection")


def main() -> int:
    print(f"Test DB: {_TMP_DB}")
    failures = 0
    for fn in (test_insert_unique_violation_releases_connection,
               test_repeated_failures_do_not_exhaust_connections,
               test_automation_fires_unique_does_not_cascade,
               test_read_helpers_release_on_error):
        name = fn.__name__
        try:
            fn()
        except AssertionError as e:
            failures += 1
            print(f"  [FAIL] {name}: {e}")
        except Exception as e:   # pragma: no cover
            failures += 1
            import traceback
            print(f"  [ERR ] {name}: {type(e).__name__}: {e}")
            traceback.print_exc()
    try:
        _TMP_DB.unlink(missing_ok=True)
    except Exception:
        pass
    print()
    if failures:
        print(f"{failures} FAILURE(S)")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
