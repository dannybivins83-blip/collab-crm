# -*- coding: utf-8 -*-
"""Regression tests for the two IMPORTER root causes behind
"not populating the correct jobs in the milestone pipeline".

1. modules/acculynx_sync.run_sync — the existing-record lookup only searched the
   table matching the pass's kind, so the SAME AccuLynx GUID arriving under a
   lead/prospect group after it was already stored as a job got INSERTED AGAIN as a
   lead. The record then appeared twice on the unified board (Prospect + Approved).

2. scripts/import_closed — hardcoded `stage = "closed"|"canceled"` with no kind
   resolution (so dead LEADS were written into `jobs`) plus a hardcoded
   "REROOF Department" and a name-only dedupe fallback.

No network: `_api_get` / `_job_detail` are monkeypatched with a synthetic AccuLynx
payload, and the import_closed path is exercised through `import_items()` which takes
already-fetched records.

Run:
    cd whitelabel-crm
    DATABASE_URL="" CRM_NOBROWSER=1 python -m pytest tests/test_pipeline_import_regression.py -q
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

# Throwaway SQLite DB + dev mode BEFORE any project import (never touch a real mirror).
_TMP_DB = Path(tempfile.gettempdir()) / f"crm_test_pipeimport_{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = ""
os.environ["CRM_NOBROWSER"] = "1"
os.environ["CRM_DB_PATH"] = str(_TMP_DB)
os.environ["CRM_PORT"] = os.environ.get("CRM_PORT", "5099")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

# pylint: disable=wrong-import-position
import app as crm_app                       # noqa: E402,F401  (boots the schema)
import db                                   # noqa: E402
from modules import acculynx_sync as S      # noqa: E402
import import_closed                        # noqa: E402  (scripts/ is on sys.path)


GUID_A = "aaaaaaaa-1111-4222-8333-444444444444"
GUID_B = "bbbbbbbb-1111-4222-8333-444444444444"


# --------------------------------------------------------------------------- helpers
def _record(guid, name, milestone, value="$12,345"):
    """A thin AccuLynx /jobs list item."""
    return {
        "id": guid,
        "jobName": name,
        "jobNumber": "12345",
        "currentMilestone": {"name": milestone},
        "jobValue": value,
        "workType": {"name": "Reroof"},
        "leadSource": {"name": "Referral"},
        "salesRep": {"name": "Scott"},
        "locationAddress": {"addressFirstLine": "1 Test St", "city": "Lantana",
                            "state": {"abbreviation": "FL"}, "zipCode": "33462"},
        "contact": {"id": "c1", "firstName": name.split(" ")[0],
                    "lastName": " ".join(name.split(" ")[1:]) or "X",
                    "phone": "5615550000", "email": "t@example.com"},
    }


def _install_fake_api(monkeypatch, pages):
    """pages: {group_name: [items...]}. Every other group answers empty."""
    def fake_api_get(base, path, key, params=None):
        params = params or {}
        if path == "/jobs":
            grp = params.get("milestones")
            start = int(params.get("pageStartIndex") or 0)
            items = pages.get(grp, [])
            return {"items": items[start:start + int(params.get("pageSize") or 25)]}
        return {}
    monkeypatch.setattr(S, "_api_get", fake_api_get)
    monkeypatch.setattr(S, "_job_detail", lambda base, guid, key: {})


def _reset_cursor():
    db.save_company({"acculynx_group": 0, "acculynx_cursor": 0})


def _setup_company():
    db.save_company({"acculynx_api_key": "test-key-not-a-secret",
                     "departments": "SOLAR Department, SERVICE Department"})


def _wipe():
    for t in ("leads", "jobs", "contacts", "activities"):
        try:
            db.execute("DELETE FROM %s" % t)
        except Exception:
            pass


# --------------------------------------------------------------------- FIX 1 tests
def test_same_guid_job_then_lead_group_stays_one_job(monkeypatch):
    """A GUID stored as a JOB, later reported under the `prospect` group, must NOT
    be inserted a second time as a lead. Exactly one record; it stays a job."""
    _wipe()
    _setup_company()
    rec = _record(GUID_A, "Belinda Souza", "Approved")

    # Pass 1 — AccuLynx reports it in the approved group → creates a job.
    _install_fake_api(monkeypatch, {"approved": [rec]})
    _reset_cursor()
    r1 = S.run_sync(deep=False, batch=5)
    assert r1.get("ok"), r1
    jobs = db.all_rows("jobs")
    assert len(jobs) == 1, f"expected 1 job after pass 1, got {len(jobs)}"
    job_id = jobs[0]["id"]

    # Pass 2 — the SAME GUID now comes back under the prospect (lead) group.
    _install_fake_api(monkeypatch, {"prospect": [_record(GUID_A, "Belinda Souza", "Prospect")]})
    _reset_cursor()
    r2 = S.run_sync(deep=False, batch=5)
    assert r2.get("ok"), r2

    leads = db.all_rows("leads")
    jobs = db.all_rows("jobs")
    assert len(leads) == 0, f"duplicate lead created for a GUID already stored as a job: {leads}"
    assert len(jobs) == 1, f"expected still 1 job, got {len(jobs)}"
    assert jobs[0]["id"] == job_id
    # Job outranks lead — a lead stage key must never be written onto the job row.
    assert jobs[0]["stage"] in {s["key"] for s in __import__("constants").JOB_STAGES}, \
        jobs[0]["stage"]


def test_same_guid_lead_then_job_group_leaves_one_board_entry(monkeypatch):
    """The reverse order: stored as a lead first, then reported as an approved job.
    The job is created and the lead is retired ('won') so the same AccuLynx record
    cannot occupy two pipeline columns."""
    _wipe()
    _setup_company()

    _install_fake_api(monkeypatch, {"lead": [_record(GUID_B, "Josh Gutierrez", "Assigned")]})
    _reset_cursor()
    assert S.run_sync(deep=False, batch=5).get("ok")
    assert len(db.all_rows("leads")) == 1
    assert len(db.all_rows("jobs")) == 0

    _install_fake_api(monkeypatch, {"approved": [_record(GUID_B, "Josh Gutierrez", "Approved")]})
    _reset_cursor()
    assert S.run_sync(deep=False, batch=5).get("ok")

    leads = db.all_rows("leads")
    jobs = db.all_rows("jobs")
    assert len(jobs) == 1, f"expected 1 job, got {len(jobs)}"
    assert len(leads) == 1, "the lead row should be reused, not duplicated"
    # pipeline.py lists leads WHERE stage NOT IN ('won','lost') — so 'won' takes the
    # retired lead off the board, leaving exactly one visible entry for this GUID.
    assert leads[0]["stage"] == "won", leads[0]["stage"]


def test_distinct_guids_still_create_distinct_records(monkeypatch):
    """The cross-kind guard must not collapse unrelated records."""
    _wipe()
    _setup_company()
    _install_fake_api(monkeypatch, {
        "lead": [_record("cccccccc-1111-4222-8333-444444444444", "Eli Zamir", "Assigned")],
        "approved": [_record("dddddddd-1111-4222-8333-444444444444", "Ryan Winsted", "Approved")],
    })
    _reset_cursor()
    assert S.run_sync(deep=False, batch=10).get("ok")
    assert len(db.all_rows("leads")) == 1
    assert len(db.all_rows("jobs")) == 1


# --------------------------------------------------------------------- FIX 2 tests
def test_canceled_lead_lands_in_leads_not_jobs():
    """A dead AccuLynx LEAD in the 'cancelled' bucket must be written to `leads`."""
    _wipe()
    _setup_company()
    dead_lead = _record("eeeeeeee-1111-4222-8333-444444444444", "Dead Prospect", "Prospect")
    res = import_closed.import_items([dead_lead], "cancelled")
    assert res["added_leads"] == 1, res
    assert res["added_jobs"] == 0, res
    leads = db.all_rows("leads")
    assert len(leads) == 1 and len(db.all_rows("jobs")) == 0
    assert leads[0]["stage"] == "lost", leads[0]["stage"]


def test_canceled_job_still_lands_in_jobs():
    """A genuine canceled JOB must still be written to `jobs` (no regression)."""
    _wipe()
    _setup_company()
    canceled_job = _record("ffffffff-1111-4222-8333-444444444444", "Real Job", "Tear Off Started")
    res = import_closed.import_items([canceled_job], "cancelled")
    assert res["added_jobs"] == 1 and res["added_leads"] == 0, res
    jobs = db.all_rows("jobs")
    assert len(jobs) == 1 and jobs[0]["stage"] == "canceled"


def test_department_comes_from_department_for_not_hardcoded():
    """Department must come from _department_for()/company settings, never the
    hardcoded literal 'REROOF Department'."""
    _wipe()
    db.save_company({"acculynx_api_key": "test-key-not-a-secret",
                     "departments": "SOLAR Department, SERVICE Department"})
    company = db.get_company()
    rec = _record("11111111-1111-4222-8333-444444444444", "Dept Test", "Tear Off Started")
    import_closed.import_items([rec], "cancelled", company=company)
    job = db.all_rows("jobs")[0]
    assert job["department"] == S._department_for("Reroof", company) == "SOLAR Department", \
        job["department"]

    # A service work type routes to the configured SERVICE department.
    _wipe()
    svc = _record("22222222-1111-4222-8333-444444444444", "Svc Test", "Tear Off Started")
    svc["workType"] = {"name": "Service Repair"}
    import_closed.import_items([svc], "cancelled", company=company)
    assert db.all_rows("jobs")[0]["department"] == "SERVICE Department"


def test_import_closed_dedupes_by_guid_not_name():
    """Same GUID twice → one row. Same NAME but different GUIDs → two rows."""
    _wipe()
    _setup_company()
    g = "33333333-1111-4222-8333-444444444444"
    a = _record(g, "Same Name", "Tear Off Started")
    res = import_closed.import_items([a, _record(g, "Same Name", "Tear Off Started")], "cancelled")
    assert res["added_jobs"] == 1 and res["updated"] == 1, res
    assert len(db.all_rows("jobs")) == 1

    # Different GUID, identical name → a distinct record (name must not merge them).
    res2 = import_closed.import_items(
        [_record("44444444-1111-4222-8333-444444444444", "Same Name", "Tear Off Started")],
        "cancelled")
    assert res2["added_jobs"] == 1, res2
    assert len(db.all_rows("jobs")) == 2


def test_import_closed_never_recreates_a_job_as_a_lead():
    """Cross-kind guard in the backfill path: a GUID already stored as a job must
    not be re-inserted as a dead lead."""
    _wipe()
    _setup_company()
    g = "55555555-1111-4222-8333-444444444444"
    import_closed.import_items([_record(g, "Promoted Rec", "Tear Off Started")], "cancelled")
    assert len(db.all_rows("jobs")) == 1
    # Now AccuLynx reports the same GUID with a lead milestone.
    import_closed.import_items([_record(g, "Promoted Rec", "Prospect")], "cancelled")
    assert len(db.all_rows("leads")) == 0, "job was re-created as a lead"
    assert len(db.all_rows("jobs")) == 1


# --------------------------------------------------------- repair script (dry run)
def test_repair_script_is_dry_run_by_default():
    """The repair script must report without writing unless --apply is passed."""
    _wipe()
    _setup_company()
    import repair_misfiled_canceled_jobs as R
    db.insert("jobs", {"name": "Misfiled Dead Lead", "stage": "canceled",
                       "narrative": R.NARRATIVE_SIGNATURE})
    db.insert("jobs", {"name": "Real Canceled Job", "stage": "canceled",
                       "narrative": R.NARRATIVE_SIGNATURE, "contract_value": "$40,000"})
    res = R.repair(apply=False)
    assert res["candidates"] == 1, res
    assert res["skipped"] == 1, res          # the one with money is left alone
    assert res["moved"] == 0
    assert len(db.all_rows("leads")) == 0, "dry run wrote to the database"
    assert len(db.all_rows("jobs")) == 2


def teardown_module(module):    # noqa: D103
    try:
        _TMP_DB.unlink(missing_ok=True)
    except Exception:
        pass
