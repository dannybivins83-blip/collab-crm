# -*- coding: utf-8 -*-
"""White-label Permit Builder REST API — Phase 2+3
POST   /api/v1/permits/build              async packet build (JSON in, job_id out)
GET    /api/v1/permits/build/<id>/status  poll build status
GET    /api/v1/permits/build/<id>/download stream finished PDF
GET    /api/v1/permits/ahjs               list supported AHJs + systems
POST   /api/v1/permits/keys/new           generate API key (auth'd with session or master key)

Authentication: X-Permit-API-Key header OR api_key in JSON body.
Keys stored as SHA-256 hashes in permit_api_keys table; raw key shown once on creation.
Async execution via daemon threading.Thread; state in permit_build_jobs table + in-memory dict.
"""
import os
import re
import sys
import time
import uuid
import hashlib
import secrets
import threading
import urllib.request

from flask import Blueprint, request, jsonify, send_file, g, redirect, url_for, flash

import config
import db

bp = Blueprint("permit_api", __name__, url_prefix="/api/v1/permits")

_JOBS = {}          # job_id -> {status, result_path, error, created}
_JOBS_LOCK = threading.Lock()

_SYS_MAP = {"shingle": "Shingle", "tile": "Tile", "metal": "Metal", "flat": "Flat"}


# --- Auth helpers -------------------------------------------------------------

def _hash_key(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def _validate_key(raw):
    if not raw:
        return None
    h = _hash_key(raw)
    rows = db.all_rows("permit_api_keys", "key_hash=? AND active=1", (h,))
    return rows[0] if rows else None


def _get_key_from_request():
    return (request.headers.get("X-Permit-API-Key")
            or (request.get_json(silent=True) or {}).get("api_key")
            or request.args.get("api_key", ""))


def _require_key():
    row = _validate_key(_get_key_from_request())
    if not row:
        return None, jsonify({"ok": False, "error": "Invalid or missing API key"}), 401
    return row, None, None


# --- Background builder -------------------------------------------------------

def _builder():
    # Prefer the in-repo copy (packet_builder_handoff/ inside whitelabel-crm/).
    # Fall back to the legacy sibling-dir location for local installs that haven't
    # pulled the new copy yet.
    _builder_dir = os.path.normpath(os.path.join(
        config.HERE, "packet_builder_handoff", "permit_packet_builder"))
    if not os.path.isdir(_builder_dir):
        _builder_dir = os.path.normpath(os.path.join(
            config.HERE, "..", "packet_builder_handoff", "permit_packet_builder"))
    if _builder_dir not in sys.path and os.path.isdir(_builder_dir):
        sys.path.insert(0, _builder_dir)
    try:
        import build
        return build
    except Exception:
        return None


def _fire_webhook(url, payload):
    try:
        import json as _json
        data = _json.dumps(payload).encode()
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass


def _bg_build(job_id, client, ahj, system, underlayment, product, contractor, webhook_url, out_path):
    try:
        b = _builder()
        if not b:
            raise RuntimeError("Build engine (build.py) not available on this host")
        b.build_packet(client, ahj, system, [], out_path, underlayment, product, contractor=contractor)
        with _JOBS_LOCK:
            _JOBS[job_id].update({"status": "complete", "result_path": out_path})
        try:
            db.execute("UPDATE permit_build_jobs SET status=?, result_path=? WHERE job_id=?",
                       ("complete", out_path, job_id))
        except Exception:
            pass
        if webhook_url:
            _fire_webhook(webhook_url, {
                "event": "permit.built", "job_id": job_id,
                "download_url": "/api/v1/permits/build/%s/download" % job_id,
                "ahj": ahj, "system": system.lower(),
            })
    except Exception as e:
        with _JOBS_LOCK:
            _JOBS[job_id].update({"status": "error", "error": str(e)})
        try:
            db.execute("UPDATE permit_build_jobs SET status=?, notes=? WHERE job_id=?",
                       ("error", str(e)[:500], job_id))
        except Exception:
            pass


# --- API endpoints ------------------------------------------------------------

@bp.route("/ahjs")
def list_ahjs():
    key_row, err, code = _require_key()
    if err:
        return err, code
    b = _builder()
    if not b:
        return jsonify({"ok": False, "error": "Builder engine not available"}), 503
    try:
        ahjs = b.list_ahjs()
        systems = list(b.SYSTEMS.keys())
        return jsonify({"ok": True, "ahjs": ahjs, "systems": systems})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/build", methods=["POST"])
def submit_build():
    key_row, err, code = _require_key()
    if err:
        return err, code

    body = request.get_json(silent=True) or {}
    job_data = body.get("job") or {}
    contractor = body.get("contractor") or None
    webhook_url = body.get("webhook_url") or None

    ahj = re.sub(r'[^A-Za-z0-9 _-]', '', (job_data.get("ahj") or "").strip())[:64]
    system_lower = re.sub(r'[^A-Za-z0-9 _-]', '', (job_data.get("system") or "").lower().strip())[:32]
    system = _SYS_MAP.get(system_lower)
    if not ahj or not system:
        return jsonify({"ok": False, "error": "job.ahj and job.system (shingle/tile/metal/flat) are required"}), 400

    client = {
        "owner":  job_data.get("owner", ""),
        "address": job_data.get("address", ""),
        "city":   job_data.get("city", ""),
        "zip":    job_data.get("zip", ""),
        "phone":  job_data.get("phone", ""),
        "pcn":    job_data.get("pcn", ""),
        "legal":  job_data.get("legal", ""),
        "area":   str(job_data.get("area", "") or ""),
        "slope":  str(job_data.get("slope", "") or ""),
        "mrh":    str(job_data.get("mrh", "") or ""),
        "value":  str(job_data.get("contract_value", "") or ""),
        "existing": job_data.get("existing", ""),
    }
    underlayment = job_data.get("underlayment") or None
    product = job_data.get("product") or None

    # If no contractor supplied in the request, use the default profile from DB (if any).
    if contractor is None:
        try:
            cp = db.all_rows("contractor_profiles", "is_default=1")
            contractor = dict(cp[0]) if cp else None
        except Exception:
            pass

    job_id = "pb_" + uuid.uuid4().hex[:12]
    safe = re.sub(r"[^A-Za-z0-9]+", "_", client["owner"] or "client")[:20] or "client"
    fname = "API_%s_%s_%s_%s.pdf" % (job_id, safe, ahj, system_lower)
    out_path = os.path.join(config.PERMIT_DIR, fname)

    try:
        db.execute(
            "INSERT INTO permit_build_jobs (job_id, api_key_id, status, webhook_url, created_at)"
            " VALUES (?,?,?,?,?)",
            (job_id, key_row["id"], "queued", webhook_url or "", db.now()))
    except Exception:
        pass

    with _JOBS_LOCK:
        _JOBS[job_id] = {"status": "queued", "result_path": None, "error": None,
                         "fname": fname, "created": time.time()}

    t = threading.Thread(
        target=_bg_build, daemon=True,
        args=(job_id, client, ahj, system, underlayment, product, contractor, webhook_url, out_path))
    t.start()

    return jsonify({
        "ok": True, "job_id": job_id, "status": "queued",
        "poll_url": "/api/v1/permits/build/%s/status" % job_id,
        "download_url": "/api/v1/permits/build/%s/download" % job_id,
        "estimated_seconds": 15,
    })


@bp.route("/build/<job_id>/status")
def build_status(job_id):
    key_row, err, code = _require_key()
    if err:
        return err, code
    with _JOBS_LOCK:
        job = dict(_JOBS.get(job_id) or {})
    if not job:
        rows = db.all_rows("permit_build_jobs", "job_id=?", (job_id,))
        if not rows:
            return jsonify({"ok": False, "error": "Job not found"}), 404
        job = {"status": rows[0]["status"], "result_path": rows[0].get("result_path"),
               "error": rows[0].get("notes")}
    resp = {"ok": True, "job_id": job_id, "status": job.get("status", "unknown")}
    if job.get("status") == "complete":
        resp["download_url"] = "/api/v1/permits/build/%s/download" % job_id
    elif job.get("status") == "error":
        resp["error"] = job.get("error") or "Build failed"
    return jsonify(resp)


@bp.route("/build/<job_id>/download")
def build_download(job_id):
    key_row, err, code = _require_key()
    if err:
        return err, code
    with _JOBS_LOCK:
        job = dict(_JOBS.get(job_id) or {})
    if not job:
        rows = db.all_rows("permit_build_jobs", "job_id=?", (job_id,))
        if not rows:
            return jsonify({"ok": False, "error": "Job not found"}), 404
        job = {"status": rows[0]["status"],
               "result_path": rows[0].get("result_path") or ""}
    if job.get("status") != "complete" or not job.get("result_path"):
        return jsonify({"ok": False, "error": "Not ready", "status": job.get("status")}), 202
    path = job["result_path"]
    # Path traversal guard: resolved path must stay inside PERMIT_DIR.
    full = os.path.realpath(path)
    permit_root = os.path.realpath(config.PERMIT_DIR)
    if not full.startswith(permit_root + os.sep) and full != permit_root:
        return jsonify({"ok": False, "error": "Invalid file path"}), 403
    if not os.path.exists(full):
        return jsonify({"ok": False, "error": "File not found on server"}), 404
    return send_file(full, as_attachment=True, download_name=os.path.basename(full))


# --- API key management (called from Settings or directly) --------------------

@bp.route("/keys/new", methods=["POST"])
def new_key():
    """Generate a new API key. Requires an active session (owner/admin) or master key."""
    from modules.auth import current_user as _current_user
    u = _current_user()
    if not u:
        return jsonify({"ok": False, "error": "Login required"}), 401
    label = (request.form.get("label") or request.get_json(silent=True, force=True) or {}).get("label", "default")
    if isinstance(label, dict):
        label = label.get("label", "default")
    raw = "pk_live_" + secrets.token_urlsafe(32)
    hashed = _hash_key(raw)
    tenant_id = u.get("id", 1)
    try:
        db.execute(
            "INSERT INTO permit_api_keys (created_at, tenant_id, key_hash, label, active)"
            " VALUES (?,?,?,?,1)",
            (db.now(), tenant_id, hashed, str(label)[:80]))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    # Return the raw key once — never stored in plaintext.
    return jsonify({"ok": True, "key": raw, "label": label,
                    "note": "Save this key — it will not be shown again."})


@bp.route("/keys/<int:key_id>/revoke", methods=["POST"])
def revoke_key(key_id):
    from modules.auth import current_user as _current_user
    u = _current_user()
    if not u:
        return jsonify({"ok": False, "error": "Login required"}), 401
    db.execute("UPDATE permit_api_keys SET active=0 WHERE id=?", (key_id,))
    flash("API key revoked.", "ok")
    return redirect(request.referrer or url_for("settings.index"))


# --- Public helper (used by permits.py settings page) ------------------------

def list_keys_for_tenant(tenant_id=1):
    return db.all_rows("permit_api_keys", "tenant_id=?", (tenant_id,))
