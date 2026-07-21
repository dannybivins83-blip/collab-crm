# -*- coding: utf-8 -*-
"""Regression coverage for the takeoff -> measurements -> estimate -> documents chain.

Owner report (2026-06-15, re-raised 2026-07-20):
    "still issues it just needs to extract the data and put in measurements for
     the estimate and save the takeoff in docs"

Each test below pins one break that was reproduced against the real code paths.
NO AI and NO network: `anthropic` is replaced with a stub client that returns a
canned tool_use block, and the roof-engine HTTP call is monkeypatched, so the
genuine worker / apply logic runs end to end against a throwaway SQLite DB.

Covered:
  1. A lead that already has a JOB keeps ONE estimate — the pre-existing
     lead-linked draft gets the quantities, instead of a duplicate job-linked
     estimate being spawned while the real one stays at qty 0.
  2. The measurement row is reachable from BOTH measurements.for_lead() and
     measurements.for_job() after conversion.
  3. The job header mirrors area + slope from the takeoff.
  4. The takeoff itself is filed into documents (not just the uploaded plan set).
  5. A non-draft (sent/signed) estimate is never silently re-priced.
  6. roof_reports._measurement_from_result maps EVERY engine edge class,
     including `step` and `apron` -> step_flash_lf (previously dropped, which is
     the "squares only / missing edge types" complaint).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import types
import uuid
from pathlib import Path

import pytest

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_TMP_DB = Path(tempfile.gettempdir()) / f"crm_test_chain_{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = ""
os.environ["CRM_NOBROWSER"] = "1"
os.environ["CRM_DB_PATH"] = str(_TMP_DB)
os.environ.setdefault("CRM_PORT", "5096")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test-not-a-real-key")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as crm_app          # noqa: E402  (boots schema)
import config                  # noqa: E402
import db                      # noqa: E402
from modules import estimates as EST        # noqa: E402
from modules import measurements as MEAS    # noqa: E402
from modules import roof_reports as RR      # noqa: E402
from modules import takeoff as TK           # noqa: E402

APP = crm_app.app

EXTRACT = {
    "total_sq": 32.5, "ridge_lf": 48.0, "hip_lf": 62.0, "valley_lf": 24.0,
    "rake_lf": 90.0, "eave_lf": 130.0, "step_flash_lf": 18.0,
    "predominant_pitch": "5:12", "roof_system_type": "Shingle",
    "wind_speed_mph": 175, "asce_version": "ASCE 7-22",
    "plan_set_label": "Regression Plan Set",
}

_MIN_PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
            b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
            b"trailer<</Root 1 0 R>>\n%%EOF\n")


@pytest.fixture(autouse=True)
def _stub_anthropic(monkeypatch):
    """Replace the anthropic SDK with a client returning a fixed extraction."""
    class _Block:
        type = "tool_use"
        name = "extract_roof_measurements"
        input = dict(EXTRACT)

    class _Resp:
        content = [_Block()]

    class _Messages:
        def create(self, **_kw):
            return _Resp()

    class _Client:
        def __init__(self, *_a, **_k):
            self.messages = _Messages()

    mod = types.ModuleType("anthropic")
    mod.Anthropic = _Client
    monkeypatch.setitem(sys.modules, "anthropic", mod)


def _plans_file(name):
    os.makedirs(config.DOC_DIR, exist_ok=True)
    path = os.path.join(config.DOC_DIR, name)
    with open(path, "wb") as fh:
        fh.write(_MIN_PDF)
    return path


def _run_worker(lead_id, path, name="plans.pdf", doc_id=None):
    token = str(uuid.uuid4())
    with APP.app_context():
        db.execute(
            "INSERT INTO takeoff_jobs (token,lead_id,profile,status,progress,file_path,"
            "created,updated) VALUES (?,?,?,?,?,?,?,?)",
            (token, lead_id, "seabreeze", "queued", "Queued...", path, db.now(), db.now()))
    TK._run_takeoff_worker(token, path, name, lead_id, "seabreeze", APP, doc_id)
    with APP.app_context():
        row = db.all_rows("takeoff_jobs", "token=?", (token,))[0]
    assert row["status"] == "done", row.get("progress")
    return row


def _make_lead(name, **extra):
    with APP.app_context():
        fields = {"name": name, "address": "9 Regression St", "city": "Lantana",
                  "stage": "new", "department": "reroof",
                  "work_type": "Roofing - Shingle"}
        fields.update(extra)
        return db.insert("leads", fields)


# ---------------------------------------------------------------------------
# 1-4: converted lead — one estimate, dual-linked measurement, job header, docs
# ---------------------------------------------------------------------------
def test_converted_lead_updates_existing_estimate_and_files_takeoff():
    lead_id = _make_lead("Converted Owner", stage="won")
    with APP.app_context():
        job_id = db.insert("jobs", {
            "name": "Converted Owner", "lead_id": lead_id, "address": "9 Regression St",
            "city": "Lantana", "department": "reroof", "stage": "approved",
            "work_type": "Roofing - Shingle"})
        # The estimate the rep already has open: created at lead entry, so it is
        # LEAD-linked (job_id NULL) even though the lead is now a job.
        est_id = EST.build_estimate(lead_id=lead_id, work_type="Roofing - Shingle")

    _run_worker(lead_id, _plans_file("regress_converted.pdf"), "regress_converted.pdf")

    with APP.app_context():
        ests = db.all_rows("estimates", "lead_id=? OR job_id=?", (lead_id, job_id))
        # (1) exactly ONE estimate — the takeoff must not spawn a duplicate.
        assert len(ests) == 1, [(e["id"], e.get("lead_id"), e.get("job_id")) for e in ests]
        assert ests[0]["id"] == est_id

        # ...and it is the one that actually received the quantities.
        lines = db.all_rows("estimate_lines", "estimate_id=?", (est_id,))
        nonzero = [l for l in lines if float(l.get("qty") or 0) > 0]
        assert nonzero, "existing draft estimate was left at qty 0"

        # Edge-driven lines, not just squares: ridge+hip drives the hip/ridge line
        # and eave+rake drives drip edge. Both must be non-zero.
        by_desc = {(l.get("description") or "").lower(): float(l.get("qty") or 0)
                   for l in lines}
        assert any(q > 0 for d, q in by_desc.items() if "hip/ridge" in d or "ridge" in d), by_desc
        assert any(q > 0 for d, q in by_desc.items() if "drip" in d), by_desc
        assert any(q > 0 for d, q in by_desc.items() if "valley" in d), by_desc

        # (2) measurement reachable from BOTH sides of the conversion.
        ml, mj = MEAS.for_lead(lead_id), MEAS.for_job(job_id)
        assert ml is not None, "measurements.for_lead() found nothing after conversion"
        assert mj is not None, "measurements.for_job() found nothing"
        assert ml["id"] == mj["id"]
        for key, want in (("squares", 32.5), ("ridge_lf", 48.0), ("hip_lf", 62.0),
                          ("valley_lf", 24.0), ("rake_lf", 90.0), ("eave_lf", 130.0),
                          ("step_flash_lf", 18.0)):
            assert float(ml[key] or 0) == want, (key, ml[key])

        # (3) job header mirrors the headline numbers.
        job = db.get("jobs", job_id)
        assert job["area"] == "32.5", job["area"]
        assert job["slope"] == "5:12", job["slope"]

        # (4) the takeoff itself is filed into documents.
        docs = db.all_rows("documents", "lead_id=? OR job_id=?", (lead_id, job_id))
        takeoffs = [d for d in docs
                    if "takeoff-summary" in (d.get("original_name") or "")]
        assert takeoffs, [d.get("original_name") for d in docs]
        doc = takeoffs[0]
        assert doc["job_id"] == job_id, "takeoff doc not linked to the job"
        assert (doc.get("size") or 0) > 0
        assert os.path.exists(os.path.join(config.DOC_DIR, doc["filename"]))


def test_takeoff_summary_pdf_contains_every_edge_type():
    """The filed artifact must show all edge types, not just squares."""
    lead = {"name": "PDF Owner", "address": "1 Doc Way", "city": "Lantana",
            "mail_state": "FL", "mail_zip": "33462"}
    data = TK._takeoff_summary_pdf(dict(EXTRACT), ["verify me"], lead,
                                   "plans.pdf", "Roofing - Shingle")
    assert data and data.startswith(b"%PDF"), "expected a real PDF"
    from pypdf import PdfReader
    import io as _io
    text = PdfReader(_io.BytesIO(data)).pages[0].extract_text() or ""
    for token in ("32.5", "48.0", "62.0", "24.0", "90.0", "130.0", "18.0",
                  "Ridge", "Hip", "Valley", "Rake", "Eave", "Step flashing"):
        assert token in text, (token, text)
    # address shape: leads carry mail_state/mail_zip, not state/zip
    assert "FL" in text and "33462" in text, text


# ---------------------------------------------------------------------------
# 5: never re-price a locked estimate
# ---------------------------------------------------------------------------
def test_non_draft_estimate_is_not_repriced_and_not_duplicated():
    lead_id = _make_lead("Signed Owner")
    with APP.app_context():
        est_id = EST.build_estimate(lead_id=lead_id, work_type="Roofing - Shingle")
        db.update("estimates", est_id, status="signed")
        before = {l["id"]: float(l.get("qty") or 0)
                  for l in db.all_rows("estimate_lines", "estimate_id=?", (est_id,))}

    _run_worker(lead_id, _plans_file("regress_signed.pdf"), "regress_signed.pdf")

    with APP.app_context():
        after = {l["id"]: float(l.get("qty") or 0)
                 for l in db.all_rows("estimate_lines", "estimate_id=?", (est_id,))}
        assert after == before, "a signed estimate was silently re-priced"
        assert len(db.all_rows("estimates", "lead_id=?", (lead_id,))) == 1, \
            "a duplicate estimate was spawned alongside the signed one"


# ---------------------------------------------------------------------------
# 6: engine edge mapping — every class lands on a column
# ---------------------------------------------------------------------------
def test_measurement_from_result_maps_all_edge_classes():
    m = {
        "totals": {"squares": 41.2, "predominant_pitch": "6", "facet_count": 12},
        "edges": {
            "ridge": {"length_ft": 55.0}, "hip": {"length_ft": 70.5},
            "valley": {"length_ft": 33.0}, "rake": {"length_ft": 88.0},
            "eave": {"length_ft": 142.0}, "step": {"length_ft": 21.0},
            "apron": {"length_ft": 9.0},
        },
    }
    out = RR._measurement_from_result(m)
    assert out["squares"] == 41.2
    assert out["pitch"] == "6:12"          # bare number gets the :12 denominator
    assert out["facets"] == 12
    assert out["ridge_lf"] == 55.0
    assert out["hip_lf"] == 70.5
    assert out["valley_lf"] == 33.0
    assert out["rake_lf"] == 88.0
    assert out["eave_lf"] == 142.0
    # step + apron are the same roof-to-wall detail and share the column.
    assert out["step_flash_lf"] == 30.0, out


def test_measurement_from_result_omits_blank_edges():
    """Never clobber good data with zeros for edges the engine did not measure."""
    out = RR._measurement_from_result(
        {"totals": {"squares": 10}, "edges": {"ridge": {"length_ft": 0}}})
    assert out == {"squares": 10.0}, out


# ---------------------------------------------------------------------------
# roof_reports auto-chain: docs + measurements + estimate off a done report
# ---------------------------------------------------------------------------
def test_apply_to_client_chains_docs_measurements_estimate(monkeypatch):
    engine_json = {
        "totals": {"squares": 41.2, "predominant_pitch": "6", "facet_count": 12},
        "edges": {"ridge": {"length_ft": 55.0}, "hip": {"length_ft": 70.5},
                  "valley": {"length_ft": 33.0}, "rake": {"length_ft": 88.0},
                  "eave": {"length_ft": 142.0}, "step": {"length_ft": 21.0}},
        "building_confidence": "high",
    }
    monkeypatch.setattr(RR, "ENGINE_URL", "https://stub.invalid")
    monkeypatch.setattr(RR, "ENGINE_KEY", "stub")
    monkeypatch.setattr(RR, "_engine",
                        lambda path, method="GET", body=None, raw=False, timeout=45:
                        b"%PDF-1.4 stub\n%%EOF" if raw
                        else {"status": "done", "measurement": engine_json})

    lead_id = _make_lead("Aerial Owner")
    with APP.app_context():
        db._ensure_column("roof_reports", "lead_id", "INTEGER")
        rid = db.insert("roof_reports", {
            "lead_id": lead_id, "address": "9 Regression St", "engine_job": "eng-test",
            "status": "done", "api_result": json.dumps(engine_json)})
        summary = RR._apply_to_client(rid)

        assert "measurements auto-filled" in summary, summary
        assert "documents" in summary, summary

        meas = MEAS.for_lead(lead_id)
        assert meas is not None
        assert float(meas["step_flash_lf"] or 0) == 21.0, meas["step_flash_lf"]
        assert float(meas["hip_lf"] or 0) == 70.5

        ests = db.all_rows("estimates", "lead_id=?", (lead_id,))
        assert len(ests) == 1
        lines = db.all_rows("estimate_lines", "estimate_id=?", (ests[0]["id"],))
        assert [l for l in lines if float(l.get("qty") or 0) > 0]

        docs = db.all_rows("documents", "lead_id=?", (lead_id,))
        assert [d for d in docs if d.get("category") == "Roof Report"], docs

        # idempotent: a second call must not duplicate anything
        assert RR._apply_to_client(rid) == ""
        assert len(db.all_rows("estimates", "lead_id=?", (lead_id,))) == 1
        assert len(db.all_rows("documents", "lead_id=?", (lead_id,))) == len(docs)
