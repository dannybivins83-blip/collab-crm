# -*- coding: utf-8 -*-
"""Token-gated SQLite DB-restore — installs a prepared SQLite file as the live DB.

PURPOSE (one-vendor cutover): we are migrating the Neon/Postgres data onto Render's
SQLite host (`/data/crm.db`). A clean `migration/crm_export.db` is built off-host;
this endpoint is how that file lands on Render and atomically replaces the live DB.

SECURITY — this endpoint can OVERWRITE the entire database, so it is built
fail-closed:
  * Invisible (404) unless `DB_RESTORE_TOKEN` is set in the environment AND the
    request presents the exact same token in `X-Restore-Token` (constant-time
    compare). Any miss → 404, so the route looks like it doesn't exist.
  * SQLite hosts only. If the process is running on Postgres/Neon (`db.IS_PG`,
    i.e. Vercel) the tool refuses with 400 — restoring a SQLite file there makes
    no sense and could mislead.
  * The upload is validated (integrity_check, required tables, sane row count)
    BEFORE the live DB is touched. Any validation failure → 400 and the live DB
    is left completely untouched.
  * The swap is atomic (os.replace) and the prior DB is backed up first.

NOTE ON TAKING EFFECT: `db.connect()` opens a fresh sqlite3 connection per call
against the fixed `config.DB_PATH`, so a new `db.all_rows("jobs")` issued AFTER
the swap reads the new file immediately — no import-time connection is cached.
WAL sidecar files (`-wal`/`-shm`) from the OLD db are removed after the swap so a
stale write-ahead log can't bleed old rows into the new file; a Render redeploy /
restart additionally guarantees every worker is reading the new file.
"""
import hmac
import os
import sqlite3
import time

from flask import Blueprint, request, jsonify, abort, current_app

import config
import db

bp = Blueprint("dbadmin", __name__, url_prefix="/admin")

# Minimum jobs in an uploaded DB for it to be accepted — refuses a tiny/empty file
# that would silently wipe the real dataset. The real export carries ~1727 jobs.
MIN_JOBS = 1000


def _restore_token():
    """The armed token, or None when the endpoint is disabled (fail-closed)."""
    tok = (os.environ.get("DB_RESTORE_TOKEN") or "").strip()
    return tok or None


def _gate_or_404():
    """Fail-closed gate. Returns the token on success; aborts 404 on any miss so
    the endpoint is indistinguishable from a non-existent route when not armed."""
    armed = _restore_token()
    if not armed:
        abort(404)
    presented = request.headers.get("X-Restore-Token", "")
    # Constant-time compare; compare_digest needs equal-type str args.
    if not hmac.compare_digest(str(presented), str(armed)):
        abort(404)
    return armed


def _validate_sqlite(path):
    """Open `path` as SQLite and confirm it's a sane, complete CRM DB.
    Returns (ok: bool, reason: str|None, jobs_count: int)."""
    try:
        con = sqlite3.connect(path)
    except Exception as exc:
        return False, "cannot open uploaded file as sqlite: %s" % exc, 0
    try:
        try:
            chk = con.execute("PRAGMA integrity_check").fetchone()
        except Exception as exc:
            return False, "integrity_check failed: %s" % exc, 0
        if not chk or str(chk[0]).lower() != "ok":
            return False, "integrity_check not ok: %r" % (chk[0] if chk else None,), 0
        # Required tables present?
        names = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        for required in ("jobs", "contacts"):
            if required not in names:
                return False, "required table missing: %s" % required, 0
        try:
            jobs = con.execute("SELECT count(*) FROM jobs").fetchone()[0]
        except Exception as exc:
            return False, "cannot count jobs: %s" % exc, 0
        if jobs < MIN_JOBS:
            return False, "refusing: jobs count %d < minimum %d (tiny/empty file?)" % (
                jobs, MIN_JOBS), jobs
        return True, None, jobs
    finally:
        try:
            con.close()
        except Exception:
            pass


def _save_upload(dest_path):
    """Write the uploaded SQLite to `dest_path`. Prefers the multipart `dbfile`
    field; falls back to the raw request body. Returns (ok, reason)."""
    f = request.files.get("dbfile")
    if f is not None and f.filename:
        f.save(dest_path)
        return True, None
    # Raw-body fallback (e.g. `curl --data-binary @file`).
    raw = request.get_data(cache=False)
    if raw:
        with open(dest_path, "wb") as out:
            out.write(raw)
        return True, None
    return False, "no upload: provide multipart field 'dbfile' or a raw request body"


@bp.route("/db-restore", methods=["POST"])
def db_restore():
    # 1. Fail-closed gate (404 on any miss — invisible when not armed).
    _gate_or_404()

    # SQLite-only tool. On a Postgres/Neon host this doesn't apply.
    if db.IS_PG:
        return jsonify(ok=False, error="not applicable on Postgres host"), 400

    db_path = config.DB_PATH
    data_dir = os.path.dirname(db_path) or "."
    try:
        os.makedirs(data_dir, exist_ok=True)
    except Exception:
        pass

    # 2. Receive the upload into a temp file next to the live DB.
    tmp_path = "%s.incoming.%d.tmp" % (db_path, int(time.time()))
    ok, reason = _save_upload(tmp_path)
    if not ok:
        _safe_unlink(tmp_path)
        return jsonify(ok=False, error=reason), 400

    # 3. Validate BEFORE touching the live DB.
    valid, vreason, new_jobs = _validate_sqlite(tmp_path)
    if not valid:
        _safe_unlink(tmp_path)
        return jsonify(ok=False, error="validation failed: %s" % vreason), 400

    # Capture the old jobs count (best-effort; live DB may not exist yet on a
    # fresh host).
    old_jobs = None
    if os.path.exists(db_path):
        try:
            con = sqlite3.connect(db_path)
            try:
                old_jobs = con.execute("SELECT count(*) FROM jobs").fetchone()[0]
            finally:
                con.close()
        except Exception:
            old_jobs = None

    # 4. Back up the current DB, then atomically swap in the validated file.
    backup_path = None
    if os.path.exists(db_path):
        backup_path = "%s.bak.%d" % (db_path, int(time.time()))
        try:
            os.replace(db_path, backup_path)
        except Exception as exc:
            _safe_unlink(tmp_path)
            return jsonify(ok=False, error="backup failed, live DB untouched: %s" % exc), 500
    try:
        os.replace(tmp_path, db_path)
    except Exception as exc:
        # Swap failed — try to restore the backup so we don't leave the host
        # without a DB.
        if backup_path and os.path.exists(backup_path):
            try:
                os.replace(backup_path, db_path)
            except Exception:
                pass
        _safe_unlink(tmp_path)
        return jsonify(ok=False, error="swap failed: %s" % exc), 500

    # Drop any stale WAL/SHM sidecars from the OLD db so an old write-ahead log
    # can't merge old rows into the freshly-installed file.
    for sidecar in (db_path + "-wal", db_path + "-shm"):
        _safe_unlink(sidecar)

    # Bust db.py's column cache (table shape may differ from the prior DB).
    try:
        db._COLCACHE.clear()
        db._NUMCACHE.clear()
    except Exception:
        pass

    # 5. Confirm a fresh read reflects the new file.
    try:
        _cv = db.connect()
        try:
            confirmed = (_cv.execute("SELECT COUNT(*) FROM jobs").fetchone() or (0,))[0]
        finally:
            _cv.close()
    except Exception:
        confirmed = new_jobs

    try:
        current_app.logger.warning(
            "db-restore: installed new SQLite DB (old_jobs=%s new_jobs=%s backup=%s path=%s)",
            old_jobs, confirmed, os.path.basename(backup_path) if backup_path else None, db_path)
    except Exception:
        pass

    return jsonify(
        ok=True,
        old_jobs=old_jobs,
        new_jobs=confirmed,
        backup=backup_path,
        db_path=db_path,
    )


def _safe_unlink(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


@bp.route("/reconcile-docs", methods=["GET", "POST"])
def reconcile_docs():
    """Admin-only: scan DOC_DIR for files with no documents table row and fix them.

    - Files < 100 bytes (corrupt stubs from failed chunked uploads) are deleted.
    - Real files get a stub documents row (job_id=NULL, category="Unassigned") so
      they appear in the UI and can be manually linked.

    Returns a JSON summary of deleted, registered, and already-ok counts.
    Requires: logged-in admin session. GET /admin/reconcile-docs for a dry-run count.
    """
    from flask import session, jsonify as _json
    if session.get("user_role") != "admin":
        return jsonify({"ok": False, "error": "admin only"}), 403
    if db.IS_PG:
        return jsonify({"ok": False, "error": "not applicable on Postgres host"}), 400

    doc_dir = config.DOC_DIR
    if not os.path.exists(doc_dir):
        return jsonify({"ok": True, "deleted": 0, "registered": 0, "already_ok": 0,
                      "note": "DOC_DIR does not exist"})

    # Build set of filenames already in DB.
    conn = db.connect()
    try:
        db_filenames = {r[0] for r in conn.execute(
            "SELECT filename FROM documents WHERE filename IS NOT NULL").fetchall()}
    finally:
        conn.close()

    STUB_MAX = 100  # bytes — anything smaller is a failed chunk leftover
    deleted = registered = already_ok = 0
    dry = request.method == "GET"

    for fname in os.listdir(doc_dir):
        fpath = os.path.join(doc_dir, fname)
        if not os.path.isfile(fpath) or fname.startswith("_"):
            continue
        if fname in db_filenames:
            already_ok += 1
            continue
        sz = os.path.getsize(fpath)
        if sz < STUB_MAX:
            if not dry:
                _safe_unlink(fpath)
            deleted += 1
        else:
            if not dry:
                db.insert("documents", {
                    "job_id": None, "lead_id": None,
                    "category": "Unassigned",
                    "filename": fname,
                    "original_name": fname,
                    "size": sz,
                    "notes": "Orphaned — reconciled by admin tool",
                })
            registered += 1

    return jsonify({"ok": True, "dry_run": dry,
                  "deleted_stubs": deleted,
                  "registered_orphans": registered,
                  "already_ok": already_ok})


# ---------------------------------------------------------------------------
# AccuLynx CSV imports — gated by DB_RESTORE_TOKEN (same as db-restore)
# POST multipart/form-data with field "file" containing the CSV.
# ---------------------------------------------------------------------------

def _parse_amount(val):
    try:
        return float(str(val).replace(",", "").replace("$", "").strip() or 0)
    except (ValueError, TypeError):
        return 0.0


def _job_map_cached():
    """job name → id dict, built once per request."""
    jobs = db.all_rows("jobs")
    return {(j.get("name") or "").strip(): j["id"] for j in jobs if j.get("name")}


@bp.route("/import-job-expenses", methods=["POST"])
def import_job_expenses():
    """Upload AccuLynx Job Expenses CSV and populate job_expenses table.

    curl -X POST https://crm.../admin/import-job-expenses \
         -H "X-Restore-Token: $DB_RESTORE_TOKEN" \
         -F "file=@Job_Expenses_Report.csv"
    """
    _gate_or_404()
    import csv, io
    f = request.files.get("file")
    if not f:
        return jsonify({"ok": False, "error": "no file"}), 400
    text = f.read().decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    job_map = _job_map_cached()
    db.execute("DELETE FROM job_expenses")
    added = 0
    unmatched = 0
    for row in reader:
        job_name = (row.get("Job Name") or "").strip()
        job_id = job_map.get(job_name)
        if not job_id:
            unmatched += 1
        db.insert("job_expenses", {
            "job_id": job_id,
            "acculynx_job_name": job_name,
            "payment_date": (row.get("Payment Date") or "").strip(),
            "payment_type": (row.get("Payment Type") or "").strip(),
            "amount": _parse_amount(row.get("Payment Amount")),
            "to_method": (row.get("To/Method") or "").strip(),
            "check_ref": (row.get("Check Number/Reference") or "").strip(),
            "memo": (row.get("Memo/Notes") or "").strip(),
            "job_value": _parse_amount(row.get("Job Value")),
            "balance_due": _parse_amount(row.get("Balance Due")),
            "account_type": (row.get("Account Type") or "").strip(),
            "paid_in_full": (row.get("Paid in Full") or "").strip(),
            "rep": (row.get("Company Representative") or "").strip(),
        })
        added += 1
    return jsonify({"ok": True, "imported": added, "unmatched": unmatched})


@bp.route("/import-workflow-status", methods=["POST"])
def import_workflow_status():
    """Upload AccuLynx Workflow Status CSV and populate job_stage_history table.

    curl -X POST https://crm.../admin/import-workflow-status \
         -H "X-Restore-Token: $DB_RESTORE_TOKEN" \
         -F "file=@Workflow_Status_Report.csv"
    """
    _gate_or_404()
    import csv, io
    f = request.files.get("file")
    if not f:
        return jsonify({"ok": False, "error": "no file"}), 400
    text = f.read().decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    job_map = _job_map_cached()
    db.execute("DELETE FROM job_stage_history")
    added = 0
    unmatched = 0
    for row in reader:
        job_name = (row.get("Job Name") or "").strip()
        job_id = job_map.get(job_name)
        if not job_id:
            unmatched += 1
        try:
            dur = int(str(row.get("Status Duration") or "0").strip())
        except (ValueError, TypeError):
            dur = 0
        db.insert("job_stage_history", {
            "job_id": job_id,
            "acculynx_job_name": job_name,
            "status_name": (row.get("Status Name") or "").strip(),
            "milestone": (row.get("Status Milestone") or "").strip(),
            "started_at": (row.get("Status Start Date") or "").strip(),
            "ended_at": (row.get("Status End Date") or "").strip(),
            "duration_days": dur,
            "set_by": (row.get("Set By") or "").strip(),
            "checklist_pct": (row.get("Checklist Percentage Completed") or "").strip(),
            "checklist_done": (row.get("Checklist Completed") or "").strip(),
        })
        added += 1
    return jsonify({"ok": True, "imported": added, "unmatched": unmatched})


@bp.route("/link-estimates-to-job", methods=["POST"])
def link_estimates_to_job():
    """Set job_id on estimates that belong to a lead but are missing a job link.

    Accepts: X-Restore-Token header (token-gated) OR active admin session.
    Body JSON: {"lead_id": N, "job_id": M}
    """
    from flask import session as _sess
    armed = _restore_token()
    presented = request.headers.get("X-Restore-Token", "")
    token_ok = armed and hmac.compare_digest(str(presented), str(armed))
    admin_ok = _sess.get("user_role") == "admin"
    if not token_ok and not admin_ok:
        abort(403)
    data = request.get_json(silent=True) or {}
    lead_id = data.get("lead_id")
    job_id = data.get("job_id")
    if not lead_id or not job_id:
        return jsonify(ok=False, error="lead_id and job_id required"), 400
    db.execute("UPDATE estimates SET job_id=? WHERE lead_id=? AND (job_id IS NULL OR job_id=0)",
               (job_id, lead_id))
    # Also fix any doubled RID prefix in the job name (R-XXXXX: R-XXXXX: Client → R-XXXXX: Client).
    job = db.get("jobs", job_id)
    if job:
        rid = (job.get("rid") or "").strip()
        name = (job.get("name") or "").strip()
        if rid and name.startswith(rid + ": "):
            db.update("jobs", job_id, name=name[len(rid) + 2:])
    return jsonify(ok=True, lead_id=lead_id, job_id=job_id)
