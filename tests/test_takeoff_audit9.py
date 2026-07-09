# -*- coding: utf-8 -*-
"""Regression tests for AUDIT_2026-06-10 #9 (`modules/takeoff.py`).

Two bugs were fixed:
  A. The measurement loop's upsert-by-job overwrote siblings in one envelope:
     an envelope carrying 2 measurements left exactly 1 row after ingest.
  B. The idempotency check was an O(n) scan + TOCTOU race: two concurrent
     POSTs with the same key both did the work and both inserted, producing
     duplicate jobs and duplicate measurements.

These tests boot the Flask app against a throwaway SQLite file, exercise
`/api/takeoff` via the test client with proper HMAC signing, and assert the
row counts the fix guarantees.

Run:
    cd whitelabel-crm
    DATABASE_URL="" CRM_NOBROWSER=1 CRM_PORT=5099 python tests/test_takeoff_audit9.py
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

# Force UTF-8 on stdout so arrow characters in the project's docstrings/messages
# don't crash test reporting on Windows cp1252 terminals.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Force a clean SQLite DB + dev mode before any project imports.
_TMP_DB = Path(tempfile.gettempdir()) / f"crm_test_audit9_{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = ""
os.environ["CRM_NOBROWSER"] = "1"
os.environ["CRM_DB_PATH"] = str(_TMP_DB)
os.environ["CRM_PORT"] = os.environ.get("CRM_PORT", "5099")
os.environ.setdefault("MEASURE_CRM_WEBHOOK_SECRET", "test-secret-audit9-" + uuid.uuid4().hex)

# Make project root importable.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# pylint: disable=wrong-import-position
import app as crm_app   # noqa: E402
import db               # noqa: E402

SECRET = os.environ["MEASURE_CRM_WEBHOOK_SECRET"].encode("utf-8")


def _sign(body: bytes) -> str:
    return hmac.new(SECRET, body, hashlib.sha256).hexdigest()


def _post(client, envelope: dict):
    body = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
    return client.post(
        "/api/takeoff",
        data=body,
        headers={"Content-Type": "application/json", "X-Signature": _sign(body)},
    )


def _base_envelope(idem: str, n_measurements: int = 1) -> dict:
    measurements = []
    for i in range(n_measurements):
        measurements.append({
            "measurement_name": f"M{i + 1}",
            "measurement_type": "STEEP_SLOPE",
            "total_sq": 10.0 + i,
            "predominant_pitch": "6:12",
            "steep_slope": {
                "area_sq": 10.0 + i,
                "ridges_lf": 5 + i,
                "hips_lf": 50 + i * 10,
                "valleys_lf": 0,
                "rakes_lf": 0,
                "eaves_lf": 80 + i * 10,
                "step_flashing_lf": 0,
            },
        })
    return {
        "schema_version": "estimator-takeoff/v1",
        "source": "estimator-agent-test",
        "idempotency_key": idem,
        "project": {
            "name": f"Audit9 Test {idem[:8]}",
            "address_line1": f"{idem[:6]} Test St",
            "city": "Boca Raton", "state": "FL", "zip": "33432",
        },
        "wind_design": {"wind_speed_mph_ultimate": 170, "exposure_category": "C",
                        "risk_category": "II", "asce_version": "ASCE 7-22"},
        "roof_system": {"primary_type": "STEEP_SLOPE_CEMENT_TILE",
                        "predominant_pitch": "6:12"},
        "measurements": measurements,
        "line_items": [],
        "submittal_components": [],
        "heads_up_items": [],
    }


def _measurements_for_job(job_id: int) -> list[dict]:
    return db.all_rows("measurements", where="job_id=?", params=(job_id,), order="id ASC")


def _takeoffs_for_key(key: str) -> list[dict]:
    return db.all_rows("takeoffs", where="idempotency_key=?", params=(key,), order="id ASC")


# ─────────────────────────────────────────────────────────────────────────────
# TEST A — Multi-measurement envelope persists each measurement (audit #9.1)
# ─────────────────────────────────────────────────────────────────────────────
def test_multi_measurement_envelope_persists_each():
    client = crm_app.app.test_client()
    env = _base_envelope(idem=str(uuid.uuid4()), n_measurements=2)
    r = _post(client, env)
    assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.get_data(as_text=True)}"
    body = r.get_json()
    assert body["ok"] is True, body
    assert len(body["measurement_ids"]) == 2, \
        f"expected 2 measurement ids returned, got {body['measurement_ids']}"
    rows = _measurements_for_job(body["job_id"])
    assert len(rows) == 2, f"expected 2 rows in measurements table, got {len(rows)}: {rows}"
    # The two rows should not be the same row.
    assert rows[0]["id"] != rows[1]["id"], "second measurement overwrote the first"
    # Squares should differ (10.0 vs 11.0 from the test envelope).
    sq = sorted(float(r["squares"] or 0) for r in rows)
    assert sq == [10.0, 11.0], f"unexpected squares {sq}"
    print("  [PASS] A: 2 measurements in envelope → 2 rows (no overwrite)")


# ─────────────────────────────────────────────────────────────────────────────
# TEST B — Same idempotency_key reposted = exactly one job/takeoff row, same result
# ─────────────────────────────────────────────────────────────────────────────
def test_same_idempotency_key_returns_original():
    client = crm_app.app.test_client()
    key = str(uuid.uuid4())
    env = _base_envelope(idem=key, n_measurements=1)
    r1 = _post(client, env)
    assert r1.status_code == 200, r1.get_data(as_text=True)
    body1 = r1.get_json()
    job_id_1 = body1["job_id"]
    # Re-POST identical envelope.
    r2 = _post(client, env)
    assert r2.status_code == 200, r2.get_data(as_text=True)
    body2 = r2.get_json()
    assert body2["job_id"] == job_id_1, \
        f"re-POST created a different job (was {job_id_1}, now {body2['job_id']})"
    assert body2["measurement_ids"] == body1["measurement_ids"], \
        f"re-POST returned different measurement ids: {body1} vs {body2}"
    # Exactly one takeoffs row for this key.
    rows = _takeoffs_for_key(key)
    assert len(rows) == 1, f"expected 1 takeoffs row for key, got {len(rows)}"
    print("  [PASS] B: same idempotency_key → original result returned, 1 takeoffs row")


# ─────────────────────────────────────────────────────────────────────────────
# TEST C — A POST that loses the idempotency race sees the winner's placeholder
#         and refuses to do duplicate work.
# ─────────────────────────────────────────────────────────────────────────────
# Simulating real concurrency against SQLite + Flask test_client triggers write-lock
# contention before the race even materializes (the DB lock saves us, but it's the
# wrong kind of save). Instead, we deterministically simulate "another request just
# claimed this key" by inserting a placeholder takeoffs row with empty result, then
# POSTing the same key. The route must NOT insert a duplicate job; it must poll
# briefly for a result; and when none arrives it must return 409 IDEMPOTENCY_BUSY.
def test_race_loser_blocks_on_existing_claim():
    client = crm_app.app.test_client()
    key = str(uuid.uuid4())
    # Pre-insert the claim row (this is what the winning concurrent request would do).
    db.insert("takeoffs", {"idempotency_key": key,
                           "schema_version": "estimator-takeoff/v1",
                           "source": "test-pre-claim", "result": ""})
    before_jobs = len(db.all_rows("jobs"))
    before_measurements = len(db.all_rows("measurements"))
    before_takeoffs = len(db.all_rows("takeoffs"))

    env = _base_envelope(idem=key, n_measurements=2)
    r = _post(client, env)
    # The route waits ~2s for a result then 409s. We don't care about exact code
    # so much as: no new job was inserted, no new takeoffs row was inserted.
    assert r.status_code == 409, \
        f"expected 409 IDEMPOTENCY_BUSY, got {r.status_code}: {r.get_data(as_text=True)}"
    body = r.get_json()
    assert body.get("ok") is False and body.get("error_code") == "IDEMPOTENCY_BUSY", body
    after_jobs = len(db.all_rows("jobs"))
    after_measurements = len(db.all_rows("measurements"))
    after_takeoffs = len(db.all_rows("takeoffs"))
    assert after_jobs == before_jobs, \
        f"race-loser created a duplicate job ({before_jobs} -> {after_jobs})"
    assert after_measurements == before_measurements, \
        f"race-loser created duplicate measurements ({before_measurements} -> {after_measurements})"
    assert after_takeoffs == before_takeoffs, \
        f"race-loser created a duplicate takeoffs row ({before_takeoffs} -> {after_takeoffs})"
    print("  [PASS] C: race-loser detected existing claim and did NOT duplicate work")


# ─────────────────────────────────────────────────────────────────────────────
# TEST D — UNIQUE index actually exists (won the race against pre-existing dups
#          OR the fallback non-unique index is in place). Either is acceptable
#          for the audit-#9 fix to function; the runtime code handles both.
# ─────────────────────────────────────────────────────────────────────────────
def test_idempotency_index_exists():
    # `db.all_rows()` enforces a TABLE_ALLOWLIST that (correctly) excludes the
    # `sqlite_master` system catalog, so read it through a raw connection here.
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type=? AND tbl_name=? ORDER BY name",
            ("index", "takeoffs")).fetchall()
    finally:
        conn.close()
    names = [(r["name"] or "") for r in rows]
    has_unique = any(n == "uq_takeoffs_idempotency_key" for n in names)
    has_fallback = any(n == "ix_takeoffs_idempotency_key" for n in names)
    assert has_unique or has_fallback, \
        f"neither uq_takeoffs_idempotency_key nor ix_takeoffs_idempotency_key found in {names}"
    print(f"  [PASS] D: idempotency index present "
          f"({'UNIQUE' if has_unique else 'non-unique fallback'})")


def main() -> int:
    print(f"Test DB: {_TMP_DB}")
    failures = 0
    for fn in (test_multi_measurement_envelope_persists_each,
               test_same_idempotency_key_returns_original,
               test_race_loser_blocks_on_existing_claim,
               test_idempotency_index_exists):
        name = fn.__name__
        try:
            fn()
        except AssertionError as e:
            failures += 1
            print(f"  [FAIL] {name}: {e}")
        except Exception as e:    # pragma: no cover
            failures += 1
            print(f"  [ERR ] {name}: {type(e).__name__}: {e}")
    # Cleanup.
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
