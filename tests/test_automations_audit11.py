# -*- coding: utf-8 -*-
"""Regression tests for AUDIT_2026-06-10 #11 (`modules/automations.py`).

Three bugs were fixed:
  A. Stage detection used to regex-parse the English activity text. Rewording
     "Moved to X" silently broke every automation. NEW: read the entity's
     current `stage` column from the DB.
  B. Two `except: pass` blocks silently swallowed automation failures. NEW:
     log via logging.exception.
  C. Two stage activities for one transition fired the automation twice
     (duplicate task / duplicate draft email). NEW: `automation_fires` table
     with UNIQUE (automation_id, entity_type, entity_id, stage_key, fire_date)
     + insert-first-catch-violation = atomic dedupe per (entity, stage, day).

These tests boot the Flask app against a throwaway SQLite file and exercise
the monkey-patched `db.add_activity` directly. No HTTP layer needed — the bug
is purely in the wrapper + dedupe paths.

Run:
    cd whitelabel-crm
    DATABASE_URL="" CRM_NOBROWSER=1 CRM_PORT=5099 python tests/test_automations_audit11.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import uuid
from pathlib import Path

# Force UTF-8 stdout so any arrow characters in tracebacks don't crash cp1252.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Throwaway SQLite + dev mode before any project imports.
_TMP_DB = Path(tempfile.gettempdir()) / f"crm_test_audit11_{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = ""
os.environ["CRM_NOBROWSER"] = "1"
os.environ["CRM_DB_PATH"] = str(_TMP_DB)
os.environ["CRM_PORT"] = os.environ.get("CRM_PORT", "5098")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# pylint: disable=wrong-import-position
import app as crm_app   # noqa: E402  — wires init_automations() which wraps add_activity
import db               # noqa: E402
from modules import automations as A   # noqa: E402


# Important: _seed_defaults() ran at app import. Multiple pre-seeded automations
# can match the same trigger_stage (e.g. "permit_applied" has both a create_task
# AND a draft_email rule). Each correctly fires ONCE (dedup is per-auto_id), so
# without disabling them the tests would see N tasks for N seeded rules — not a
# dedup bug, just noise. Wipe to a clean slate before each test.
def _reset_automations():
    db.execute("UPDATE automations SET active=0")
    db.execute("DELETE FROM automation_fires")


def _new_job(stage="lead_new"):
    return db.insert("jobs", {"name": f"AuditJob {uuid.uuid4().hex[:6]}",
                              "address": "123 Test St", "stage": stage})


def _new_lead(stage="lead_new"):
    return db.insert("leads", {"name": f"AuditLead {uuid.uuid4().hex[:6]}",
                               "address": "456 Test Ave", "stage": stage})


def _automation(stage_key, name=None, action="create_task", template="Do {name}"):
    return db.insert("automations", {
        "name": name or f"Auto {stage_key} {uuid.uuid4().hex[:4]}",
        "trigger_stage": stage_key, "action_type": action,
        "template_text": template, "offset_days": 0, "active": 1})


def _tasks_for(entity_type, entity_id):
    return db.all_rows("activities",
                       where="entity_type=? AND entity_id=? AND kind=?",
                       params=(entity_type, entity_id, "task"))


def _drafts_for(entity_type, entity_id):
    return db.all_rows("activities",
                       where="entity_type=? AND entity_id=? AND kind=?",
                       params=(entity_type, entity_id, "draft"))


def _fires_for(automation_id, entity_type, entity_id, stage_key):
    return db.all_rows(
        "automation_fires",
        where="automation_id=? AND entity_type=? AND entity_id=? AND stage_key=?",
        params=(automation_id, entity_type, entity_id, stage_key))


# ─────────────────────────────────────────────────────────────────────────────
# TEST A — Stage detection reads DB.stage, NOT the English activity text.
#         Reword "Moved to X" however you want; the automation still fires.
# ─────────────────────────────────────────────────────────────────────────────
def test_stage_detection_reads_db_not_prose():
    _reset_automations()
    # Pick any valid job stage key from the constants.
    import constants
    stage_key = constants.JOB_STAGES[2]["key"]  # arbitrary mid-pipeline stage
    auto_id = _automation(stage_key, action="create_task", template="Stage-task for {name}")
    job_id = _new_job(stage=stage_key)
    # The wrapper sees a stage-change activity with intentionally USELESS prose
    # — what the old regex-parser tried to read is gibberish here.
    db.add_activity("job", job_id, "stage", "🎉 advanced because reasons 🎉")
    tasks = _tasks_for("job", job_id)
    assert len(tasks) == 1, f"expected 1 auto-task, got {len(tasks)}: {tasks}"
    fires = _fires_for(auto_id, "job", job_id, stage_key)
    assert len(fires) == 1, f"expected 1 fire-record, got {len(fires)}"
    print("  [PASS] A: stage detected from DB column, not from activity prose")


# ─────────────────────────────────────────────────────────────────────────────
# TEST B — Same (entity, stage, day) fires AT MOST once (audit #11.C dedupe).
# ─────────────────────────────────────────────────────────────────────────────
def test_dedupe_per_entity_stage_day():
    _reset_automations()
    import constants
    stage_key = constants.JOB_STAGES[3]["key"]
    auto_id = _automation(stage_key, action="create_task", template="x")
    job_id = _new_job(stage=stage_key)
    # Two stage activities for one transition (the audit's double-fire shape).
    db.add_activity("job", job_id, "stage", "Moved to whatever")
    db.add_activity("job", job_id, "stage", "Moved to whatever (again)")
    tasks = _tasks_for("job", job_id)
    assert len(tasks) == 1, f"expected dedup → 1 auto-task, got {len(tasks)}"
    fires = _fires_for(auto_id, "job", job_id, stage_key)
    assert len(fires) == 1, f"expected 1 fire-record after dedup, got {len(fires)}"
    print("  [PASS] B: 2 stage activities → 1 auto-task (dedup held)")


# ─────────────────────────────────────────────────────────────────────────────
# TEST C — Non-stage activities never trigger automations (kind != 'stage').
# ─────────────────────────────────────────────────────────────────────────────
def test_non_stage_activity_does_not_fire():
    _reset_automations()
    import constants
    stage_key = constants.JOB_STAGES[4]["key"]
    _automation(stage_key, action="create_task", template="t")
    job_id = _new_job(stage=stage_key)
    db.add_activity("job", job_id, "note", "Just a comment, not a stage change")
    db.add_activity("job", job_id, "task", "Pre-existing task, not from automation")
    auto_tasks = _tasks_for("job", job_id)
    # The hand-added task is the only one — and there's no auto-task from notes.
    assert len(auto_tasks) == 1, f"expected only the manual task, got {len(auto_tasks)}"
    print("  [PASS] C: non-stage activities don't trigger automations")


# ─────────────────────────────────────────────────────────────────────────────
# TEST D — Failure in _execute is LOGGED, not silently swallowed (audit #11.B).
#         We force a failure by stubbing db.get to raise; the wrapper must not
#         crash the originating add_activity call.
# ─────────────────────────────────────────────────────────────────────────────
def test_execute_failure_is_logged_not_swallowed(capsys=None):
    _reset_automations()
    import logging
    import constants
    stage_key = constants.JOB_STAGES[5]["key"]
    _automation(stage_key, action="create_task", template="x")
    job_id = _new_job(stage=stage_key)

    # Force _execute to blow up by monkey-patching _fill (used inside _execute).
    orig_fill = A._fill
    def boom(*a, **kw):
        raise RuntimeError("simulated automation failure (test D)")
    A._fill = boom

    # Capture logs.
    log_records = []
    class _Capture(logging.Handler):
        def emit(self, record):
            log_records.append(record)
    handler = _Capture(level=logging.DEBUG)
    A._log.addHandler(handler)
    A._log.setLevel(logging.DEBUG)

    try:
        # add_activity itself must NOT raise — the underlying activity row
        # must still be inserted even if the automation chain blows up.
        aid = db.add_activity("job", job_id, "stage", "Moved to whatever")
        assert aid is not None, "add_activity returned None — wrapper crashed the trunk"
        # The exception was logged, not swallowed.
        msgs = " | ".join(r.getMessage() for r in log_records)
        assert any("automation" in r.getMessage() and "failed" in r.getMessage()
                   for r in log_records), \
            f"expected a 'automation … failed' log line, got: {msgs}"
    finally:
        A._fill = orig_fill
        A._log.removeHandler(handler)

    print("  [PASS] D: automation failure is logged, add_activity trunk still succeeds")


# ─────────────────────────────────────────────────────────────────────────────
# TEST E — fire_invoice_overdue dedupes per (invoice, day).
# ─────────────────────────────────────────────────────────────────────────────
def test_invoice_overdue_dedupe():
    _reset_automations()
    auto_id = _automation("invoice_overdue", action="draft_email",
                          template="Hi {customer}, your invoice is overdue.")
    job_id = _new_job(stage="lead_new")
    inv_id = db.insert("invoices", {"job_id": job_id, "number": "INV-9001"})
    inv = db.get("invoices", inv_id)
    # Fire twice — second call should be a no-op via the (auto, inv, "invoice_overdue", today) UNIQUE.
    A.fire_invoice_overdue(inv)
    A.fire_invoice_overdue(inv)
    drafts = _drafts_for("job", job_id)
    assert len(drafts) == 1, f"expected 1 draft after dedupe, got {len(drafts)}"
    fires = _fires_for(auto_id, "invoice", inv_id, "invoice_overdue")
    assert len(fires) == 1, f"expected 1 fire-record after dedupe, got {len(fires)}"
    print("  [PASS] E: invoice_overdue fires once per (invoice, day)")


# ─────────────────────────────────────────────────────────────────────────────
# TEST F — automation_fires UNIQUE index actually exists.
# ─────────────────────────────────────────────────────────────────────────────
def test_automation_fires_unique_constraint():
    _reset_automations()
    job_id = _new_job(stage="lead_new")
    base = {"automation_id": 9999, "entity_type": "job", "entity_id": job_id,
            "stage_key": "unique_test_key", "fire_date": db.today()}
    db.insert("automation_fires", dict(base))
    raised = False
    try:
        db.insert("automation_fires", dict(base))
    except Exception:
        raised = True
    assert raised, "expected UNIQUE violation on duplicate (auto, entity, stage, day) — none raised"
    print("  [PASS] F: automation_fires UNIQUE constraint enforced at SQL layer")


def main() -> int:
    print(f"Test DB: {_TMP_DB}")
    failures = 0
    for fn in (test_stage_detection_reads_db_not_prose,
               test_dedupe_per_entity_stage_day,
               test_non_stage_activity_does_not_fire,
               test_execute_failure_is_logged_not_swallowed,
               test_invoice_overdue_dedupe,
               test_automation_fires_unique_constraint):
        name = fn.__name__
        try:
            fn()
        except AssertionError as e:
            failures += 1
            print(f"  [FAIL] {name}: {e}")
        except Exception as e:  # pragma: no cover
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
