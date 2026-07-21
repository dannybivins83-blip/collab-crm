# -*- coding: utf-8 -*-
"""Regression coverage for the MILESTONE PIPELINE bucketing bugs.

Owner report (re-raised 2026-07-20): the milestone pipeline is "not populating
the correct jobs".

Each test pins one defect that was reproduced against the real routes:

  1. `constants.job_stage()` resolved an unknown stage to JOB_STAGES[0]
     ("Approved") for the badge, while `jobs.board` bucketed with a raw string
     `==` — so a job whose stage column held a legacy key or an AccuLynx display
     name got an Approved badge on its detail page but appeared in NO column on
     the board. The two now share one resolver (`normalize_job_stage`).
  2. `/jobs/list` built its sidebar counts from a raw `GROUP BY stage`, injecting
     keys the template never renders: those jobs counted toward "All" but had no
     row to click, so the counts did not add up.
  3. Clicking a milestone row whose count came from a non-canonical raw value
     returned zero rows (SQL filtered on the canonical key only).
  4. `jobs.advance` on a CLOSED job moved it to **Canceled** (`closed` is index
     19, `len-1` is 20, so `JOB_STAGES[idx+1]` is the Canceled entry).
  5. `_write_stage_history` never populated the `milestone` column.
  6. The unified `/pipeline` board's "Closed" column was structurally unreachable:
     the query excluded exactly the stages that map to it.
"""
from __future__ import annotations

import os
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_TMP_DB = Path(tempfile.gettempdir()) / f"crm_test_milestone_{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = ""
os.environ["CRM_NOBROWSER"] = "1"
os.environ["CRM_DB_PATH"] = str(_TMP_DB)
os.environ.setdefault("CRM_PORT", "5095")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as crm_app      # noqa: E402
import constants           # noqa: E402
import db                  # noqa: E402
import theme               # noqa: E402

APP = crm_app.app
APP.config["WTF_CSRF_ENABLED"] = False

# Token shared by the session fixture and every POST below.
CSRF = "milestone-test-csrf-token"


@pytest.fixture(scope="module")
def client():
    c = APP.test_client()
    with APP.app_context():
        uid = db.insert("users", {"name": "milestone-test",
                                  "email": "milestone@test.local",
                                  "role": "admin", "active": 1})
    with c.session_transaction() as s:
        s["user_id"] = uid
        s["user_name"] = "milestone-test"
        s["user_role"] = "admin"
        # auth._guard() enforces its own session `_csrf` token on every
        # state-changing request. Without it each POST silently redirects to the
        # dashboard — which would make the advance/stage tests below pass
        # vacuously against a route that never ran.
        s["_csrf"] = CSRF
    return c


@pytest.fixture(scope="module")
def dept(client):
    """The department the board actually scopes to."""
    with APP.test_request_context("/"):
        return theme.current_department()


def _job(dept, **kw):
    with APP.app_context():
        f = {"name": "MS Job", "address": "1 Milestone Way", "department": dept,
             "stage": "approved", "contract_value": "10,000.00"}
        f.update(kw)
        return db.insert("jobs", f)


# ---------------------------------------------------------------------------
# 1. the resolver itself
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw,expect", [
    ("permit_applied", "permit_applied"),      # canonical key
    ("PERMIT_APPLIED", "permit_applied"),      # case-insensitive
    ("Permit Applied For", "permit_applied"),  # AccuLynx display name
    ("permit_sub", "permit_applied"),          # legacy key
    ("started", "teardown_started"),           # legacy key
    ("Final Inspection Passed", "final_passed"),
    ("closed", "closed"),
    ("canceled", "canceled"),
    ("", "approved"),                          # unknown -> documented default
    ("Some Status We Never Heard Of", "approved"),
])
def test_normalize_job_stage(raw, expect):
    assert constants.normalize_job_stage(raw) == expect


@pytest.mark.parametrize("raw,expect", [
    ("prospect", "prospect"), ("Prospect", "prospect"),
    ("negotiating", "negotiation"),            # legacy key
    ("new", "assigned"),                       # legacy key
    ("Dead / Lost", "lost"),                   # display name
    ("nonsense", "assigned"),
])
def test_normalize_lead_stage(raw, expect):
    assert constants.normalize_lead_stage(raw) == expect


def test_job_stage_and_normalize_never_disagree():
    """The badge (job_stage) and the bucket (normalize) must be the same stage."""
    for raw in ("permit_applied", "Permit Applied For", "permit_sub", "closed",
                "", "bogus-status", "Roof Install Started"):
        assert constants.job_stage(raw)["key"] == constants.normalize_job_stage(raw)


# ---------------------------------------------------------------------------
# 2-3. board + list: a display-name stage must not vanish
# ---------------------------------------------------------------------------
def test_display_name_stage_lands_in_its_milestone_column(client, dept):
    """A job stored with the AccuLynx display name must appear under that
    milestone — not disappear from the board."""
    _job(dept, name="Display Name Stage Job", stage="Permit Applied For")

    r = client.get("/jobs/?stage=permit_applied")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Display Name Stage Job" in body, \
        "job with a display-name stage vanished from the milestone board"


def test_list_sidebar_counts_add_up(client, dept):
    """Every counted job must be reachable from a milestone row: the per-stage
    counts must sum to the same total the header shows."""
    _job(dept, name="Counted Legacy", stage="permit_sub")     # legacy key
    _job(dept, name="Counted Display", stage="Tear Off Started")

    with APP.test_request_context("/jobs/list"):
        _conn = db.connect()
        try:
            rows = _conn.execute(
                "SELECT stage, COUNT(*) n FROM jobs WHERE department=? GROUP BY stage",
                (dept,)).fetchall()
            total = _conn.execute(
                "SELECT COUNT(*) n FROM jobs WHERE department=?", (dept,)).fetchone()["n"]
        finally:
            _conn.close()

    counts = {s["key"]: 0 for s in constants.JOB_STAGES}
    for r in rows:
        counts[constants.normalize_job_stage(r["stage"])] += r["n"]

    assert sum(counts.values()) == total, (counts, total)
    # and no count is stranded under a key the sidebar cannot render
    assert set(counts) == {s["key"] for s in constants.JOB_STAGES}


def test_list_filter_matches_raw_variants(client, dept):
    """Clicking a milestone must return the rows counted under it, including
    those stored with a legacy/display-name spelling."""
    _job(dept, name="Variant Filter Job", stage="Roof Install Started")
    r = client.get("/jobs/list?stage=install_started")
    assert r.status_code == 200
    assert "Variant Filter Job" in r.get_data(as_text=True), \
        "milestone filter missed a row stored with a non-canonical stage value"


# ---------------------------------------------------------------------------
# 4. advance must never roll a terminal job into Canceled
# ---------------------------------------------------------------------------
def test_advance_on_closed_job_does_not_cancel_it(client, dept):
    jid = _job(dept, name="Closed Job", stage="closed")
    r = client.post("/jobs/%d/advance" % jid, data={"_csrf": CSRF},
                    follow_redirects=False)
    assert r.status_code < 500
    with APP.app_context():
        assert db.get("jobs", jid)["stage"] == "closed", \
            "advancing a CLOSED job moved it to Canceled"


def test_advance_on_canceled_job_is_a_noop(client, dept):
    jid = _job(dept, name="Canceled Job", stage="canceled")
    client.post("/jobs/%d/advance" % jid, data={"_csrf": CSRF},
                follow_redirects=False)
    with APP.app_context():
        assert db.get("jobs", jid)["stage"] == "canceled"


def test_advance_still_moves_a_mid_pipeline_job_forward(client, dept):
    jid = _job(dept, name="Mid Job", stage="permit_applied")
    client.post("/jobs/%d/advance" % jid, data={"_csrf": CSRF},
                follow_redirects=False)
    with APP.app_context():
        assert db.get("jobs", jid)["stage"] == "permit_approved"


def test_advance_from_invoiced_marks_closed(client, dept):
    jid = _job(dept, name="Invoiced Job", stage="invoiced")
    client.post("/jobs/%d/advance" % jid, data={"_csrf": CSRF},
                follow_redirects=False)
    with APP.app_context():
        assert db.get("jobs", jid)["stage"] == "closed"


# ---------------------------------------------------------------------------
# 5. stage history records the milestone key, not just the display name
# ---------------------------------------------------------------------------
def test_stage_history_writes_milestone_key(client, dept):
    jid = _job(dept, name="History Job", stage="approved")
    r = client.post("/jobs/%d/stage" % jid, data={"stage": "permit_applied", "_csrf": CSRF})
    assert r.status_code < 500
    with APP.app_context():
        rows = db.all_rows("job_stage_history", "job_id=?", (jid,), "id DESC")
    assert rows, "no stage history row written"
    assert rows[0]["milestone"] == "permit_applied", rows[0]
    assert rows[0]["status_name"] == "Permit Applied For", rows[0]


# ---------------------------------------------------------------------------
# 6. the unified pipeline board's Closed column
# ---------------------------------------------------------------------------
def test_pipeline_closed_column_populates(client, dept):
    _job(dept, name="Pipeline Closed Job", stage="closed")
    with APP.app_context():
        db.insert("leads", {"name": "Pipeline Lost Lead", "department": dept,
                            "stage": "lost", "address": "2 Milestone Way"})

    r = client.get("/pipeline/")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Pipeline Closed Job" in body, \
        "closed job never reaches the pipeline board's Closed column"
    assert "Pipeline Lost Lead" in body, \
        "lost lead never reaches the pipeline board's Closed column"


def test_pipeline_does_not_double_count_won_leads(client, dept):
    """A won lead is represented by its job — it must not also occupy a column."""
    with APP.app_context():
        db.insert("leads", {"name": "Won Lead Not On Board", "department": dept,
                            "stage": "won", "address": "3 Milestone Way"})
    r = client.get("/pipeline/")
    assert r.status_code == 200
    assert "Won Lead Not On Board" not in r.get_data(as_text=True)
