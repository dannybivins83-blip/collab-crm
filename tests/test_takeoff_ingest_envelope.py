# -*- coding: utf-8 -*-
"""End-to-end unit test for `takeoff._ingest_envelope` — NO AI, NO HTTP, NO HMAC.

Feeds a synthetic `estimator-takeoff/v1` envelope straight into the ingest
function (the same code path `/api/takeoff` and the async worker use to fan an
envelope out across the CRM) and asserts the rows it must create:

  * one job  (matched-or-created, header fields populated from project/wind/roof)
  * one measurement row PER measurement in the envelope (LF values mapped)
  * one estimate + one estimate_line PER line item, with the 30% default-margin
    price rule applied when the line carries no explicit sell price
  * one submittal_component row per component, plus a WARNING for an NOA whose
    expiration is within 6 months
  * a follow-up TASK activity for every HIGH-severity heads-up item

This exercises the whole non-AI ingest surface without ever calling Claude.

Run:
    cd whitelabel-crm
    DATABASE_URL="" CRM_NOBROWSER=1 CRM_PORT=5098 python tests/test_takeoff_ingest_envelope.py
"""
from __future__ import annotations

import datetime
import os
import sys
import tempfile
import uuid
from pathlib import Path

# UTF-8 stdout so the project's arrow chars don't crash reporting on Windows.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Clean throwaway SQLite DB + dev mode BEFORE any project import.
_TMP_DB = Path(tempfile.gettempdir()) / f"crm_test_ingest_{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = ""
os.environ["CRM_NOBROWSER"] = "1"
os.environ["CRM_DB_PATH"] = str(_TMP_DB)
os.environ["CRM_PORT"] = os.environ.get("CRM_PORT", "5098")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# pylint: disable=wrong-import-position
import app as crm_app                    # noqa: E402  (boots schema)
import db                                # noqa: E402
from modules import takeoff              # noqa: E402


def _synthetic_envelope() -> dict:
    today = datetime.date.today()
    expiring = (today + datetime.timedelta(days=30)).isoformat()      # within 6 months
    safe = (today + datetime.timedelta(days=3650)).isoformat()        # ~10 yrs out
    uniq = uuid.uuid4().hex[:8]
    return {
        "schema_version": "estimator-takeoff/v1",
        "source": "unit-test",
        "project": {
            "name": f"Ingest Test {uniq}",
            "address_line1": f"{uniq} Envelope Way",
            "city": "Lantana", "state": "FL", "zip": "33462",
            "county": "Palm Beach", "permit_jurisdiction": "PBC",
            "architect_firm": "Acme Architects", "engineer_firm": "Bolt Engineering",
            "plan_set_label": "Permit Set A",
        },
        "wind_design": {"wind_speed_mph_ultimate": 170, "exposure_category": "C",
                        "risk_category": "II", "asce_version": "ASCE 7-22"},
        "roof_system": {"primary_type": "STEEP_SLOPE_ASPHALT_SHINGLE",
                        "predominant_pitch": "6:12"},
        "measurements": [
            {"measurement_name": "Main", "total_sq": 24.0, "predominant_pitch": "6:12",
             "steep_slope": {"area_sq": 24.0, "ridges_lf": 40, "hips_lf": 20,
                             "valleys_lf": 15, "rakes_lf": 30, "eaves_lf": 90,
                             "step_flashing_lf": 12}},
            {"measurement_name": "Garage", "total_sq": 8.0, "predominant_pitch": "4:12",
             "steep_slope": {"area_sq": 8.0, "ridges_lf": 12, "hips_lf": 0,
                             "valleys_lf": 0, "rakes_lf": 16, "eaves_lf": 40,
                             "step_flashing_lf": 0}},
        ],
        "line_items": [
            # No sell price → 30% default margin: price = 100 / (1-0.30) = 142.86.
            {"section": "Tear-Off", "item": "Remove existing shingles",
             "unit": "SQ", "qty": 24, "unit_price_usd": 100.0},
            # Explicit sell price → used as-is (400.00), cost stays 250.
            {"section": "Install", "item": "Architectural shingles",
             "unit": "SQ", "qty": 24, "unit_price_usd": 250.0, "sell_price_usd": 400.0},
        ],
        "submittal_components": [
            {"ord": 1, "category": "Shingle", "component": "Field shingle",
             "manufacturer": "GAF", "product": "Timberline HDZ", "noa_number": "22-0101.5",
             "noa_expiration_date": safe, "status": "APPROVED"},
            {"ord": 2, "category": "Underlayment", "component": "Sync underlayment",
             "manufacturer": "GAF", "product": "FeltBuster", "noa_number": "19-0202.3",
             "noa_expiration_date": expiring, "status": "APPROVED"},
        ],
        "heads_up_items": [
            {"severity": "HIGH", "title": "Confirm re-roof scope",
             "body": "Plans distinguish existing vs new — verify with GC."},
            {"severity": "NOTE", "title": "Owner prefers charcoal", "body": "Color note."},
        ],
    }


def test_ingest_envelope_end_to_end():
    env = _synthetic_envelope()
    with crm_app.app.app_context():
        result = takeoff._ingest_envelope(env)

    # --- top-level result ---------------------------------------------------
    assert result.get("ok") is True, result
    job_id = result.get("job_id")
    assert job_id, f"no job_id returned: {result}"

    # --- job row ------------------------------------------------------------
    job = db.get("jobs", job_id)
    assert job is not None, "job row not created"
    assert job["name"] == env["project"]["name"], job["name"]
    assert job["address"] == env["project"]["address_line1"], job["address"]
    assert (job.get("system") or "") == "STEEP_SLOPE_ASPHALT_SHINGLE", job.get("system")
    assert (job.get("wind_speed_mph") or "") == "170", job.get("wind_speed_mph")
    assert (job.get("architect_firm") or "") == "Acme Architects", job.get("architect_firm")

    # --- measurements: one row per envelope measurement ---------------------
    assert len(result["measurement_ids"]) == 2, result["measurement_ids"]
    mrows = db.all_rows("measurements", where="job_id=?", params=(job_id,), order="id ASC")
    assert len(mrows) == 2, f"expected 2 measurement rows, got {len(mrows)}"
    sq = sorted(float(r["squares"] or 0) for r in mrows)
    assert sq == [8.0, 24.0], f"unexpected squares {sq}"
    main = next(r for r in mrows if float(r["squares"] or 0) == 24.0)
    assert float(main["ridge_lf"] or 0) == 40, main["ridge_lf"]
    assert float(main["eave_lf"] or 0) == 90, main["eave_lf"]
    assert float(main["step_flash_lf"] or 0) == 12, main["step_flash_lf"]

    # --- estimate + line items ---------------------------------------------
    ests = db.all_rows("estimates", where="job_id=?", params=(job_id,), order="id ASC")
    assert len(ests) == 1, f"expected 1 estimate, got {len(ests)}"
    est_id = ests[0]["id"]
    assert len(result["line_item_ids"]) == 2, result["line_item_ids"]
    lrows = db.all_rows("estimate_lines", where="estimate_id=?", params=(est_id,), order="id ASC")
    assert len(lrows) == 2, f"expected 2 estimate_lines, got {len(lrows)}"
    # Two distinct sections in the envelope → two estimate_sections.
    secs = db.all_rows("estimate_sections", where="estimate_id=?", params=(est_id,))
    assert len(secs) == 2, f"expected 2 sections, got {len(secs)}"
    # Default-margin line (no sell price): cost 100 → price 142.86.
    default_line = next(r for r in lrows if float(r["cost"] or 0) == 100.0)
    assert abs(float(default_line["price"]) - 142.86) < 0.01, default_line["price"]
    # Explicit sell-price line: price honored, cost preserved.
    explicit_line = next(r for r in lrows if float(r["cost"] or 0) == 250.0)
    assert abs(float(explicit_line["price"]) - 400.0) < 0.01, explicit_line["price"]

    # --- submittal components + expiring-NOA warning ------------------------
    assert len(result["submittal_component_ids"]) == 2, result["submittal_component_ids"]
    scrows = db.all_rows("submittal_components", where="job_id=?", params=(job_id,))
    assert len(scrows) == 2, f"expected 2 submittal_components, got {len(scrows)}"
    warnings = result.get("warnings") or []
    assert any("expires soon" in w.lower() for w in warnings), \
        f"expected an NOA-expiring warning, got {warnings}"

    # --- heads-up: HIGH severity spawns a follow-up TASK --------------------
    tasks = db.all_rows("activities",
                        where="entity_type=? AND entity_id=? AND kind=?",
                        params=("job", job_id, "task"))
    assert len(tasks) >= 1, "HIGH-severity heads-up item did not create a task activity"
    assert any("Confirm re-roof scope" in (t.get("text") or "") for t in tasks), \
        [t.get("text") for t in tasks]

    print("  [PASS] envelope → job + 2 measurements + estimate(2 lines) + "
          "2 submittals + expiring-NOA warning + HIGH-severity task")


def test_no_match_creates_fresh_job_each_time():
    """Two envelopes with different addresses must create two distinct jobs
    (no accidental cross-attach through _match_record)."""
    with crm_app.app.app_context():
        r1 = takeoff._ingest_envelope(_synthetic_envelope())
        r2 = takeoff._ingest_envelope(_synthetic_envelope())
    assert r1["job_id"] and r2["job_id"], (r1, r2)
    assert r1["job_id"] != r2["job_id"], "distinct-address envelopes collapsed into one job"
    print("  [PASS] distinct-address envelopes → distinct jobs")


def main() -> int:
    print(f"Test DB: {_TMP_DB}")
    failures = 0
    for fn in (test_ingest_envelope_end_to_end,
               test_no_match_creates_fresh_job_each_time):
        name = fn.__name__
        try:
            fn()
        except AssertionError as e:
            failures += 1
            print(f"  [FAIL] {name}: {e}")
        except Exception as e:    # pragma: no cover
            import traceback
            failures += 1
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
