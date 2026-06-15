# -*- coding: utf-8 -*-
"""Roof Reports — measured roof reports from the Roof Report Engine.

Enter an address (or pick a job) -> the engine geocodes, pulls the Google DSM,
clips to the county parcel, measures the roof, and renders a branded PDF. All
engine calls are server-side; the API key never reaches the browser.

Config (env, set in Vercel — the repo is public so no secrets live here):
    ROOF_ENGINE_URL      e.g. https://150-136-152-240.nip.io
    ROOF_ENGINE_API_KEY  the engine's X-API-Key
    ROOF_BRAND           brand id in the engine (default "seabreeze")

The engine runs reports asynchronously, so /new just starts the job and the
detail page polls /status — this keeps every request well under Vercel's limit.
"""
import json
import os
import ssl
import urllib.request

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, jsonify, Response)

import db

try:
    import certifi
    _SSL = ssl.create_default_context(cafile=certifi.where())
except Exception:  # pragma: no cover
    _SSL = ssl.create_default_context()

bp = Blueprint("roof_reports", __name__, url_prefix="/roof-reports")

ENGINE_URL = (os.environ.get("ROOF_ENGINE_URL") or "").rstrip("/")
ENGINE_KEY = os.environ.get("ROOF_ENGINE_API_KEY") or ""
BRAND = os.environ.get("ROOF_BRAND", "seabreeze")


def _configured():
    return bool(ENGINE_URL and ENGINE_KEY)


def _engine(path, method="GET", body=None, raw=False, timeout=45):
    """Call the Roof Report Engine. Returns parsed JSON, or raw bytes if raw=True."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"X-API-Key": ENGINE_KEY}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(ENGINE_URL + path, data=data, headers=headers,
                                 method=method)
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL) as r:
        payload = r.read()
    return payload if raw else json.loads(payload.decode("utf-8"))


def _job_address(job):
    return ", ".join(x for x in (job.get("address"), job.get("city"),
                                 job.get("state"), job.get("zip")) if x)


@bp.route("/")
def index():
    reports = db.all_rows("roof_reports", order="created DESC")
    leads = db.all_rows("leads", order="name ASC")
    jobs = db.all_rows("jobs", order="name ASC")
    return render_template("roof_reports_index.html", reports=reports,
                           configured=_configured(), leads=leads, jobs=jobs)


@bp.route("/new", methods=["GET", "POST"])
def new():
    if not _configured():
        flash("Roof engine not configured — set ROOF_ENGINE_URL and "
              "ROOF_ENGINE_API_KEY in the environment.", "error")
        return redirect(url_for("roof_reports.index"))

    if request.method == "POST":
        job_id = request.form.get("job_id") or None
        lead_id = request.form.get("lead_id") or None
        if job_id:
            job = db.get("jobs", int(job_id))
            address = _job_address(job) if job else ""
        elif lead_id:
            lead = db.get("leads", int(lead_id))
            if lead:
                address = ", ".join(x for x in (
                    lead.get("address", ""), lead.get("city", ""),
                    lead.get("state", "FL"), lead.get("zip", "")) if x)
            else:
                address = ""
        else:
            address = ", ".join(
                x for x in (request.form.get("address", "").strip(),
                            request.form.get("city", "").strip(),
                            request.form.get("state", "").strip(),
                            request.form.get("zip", "").strip()) if x)
        if not address:
            flash("Enter an address or pick a job/lead with one.", "error")
            return redirect(url_for("roof_reports.new"))
        try:
            ej = _engine("/reports", "POST", {"address": address, "brand_id": BRAND})
        except Exception as e:  # noqa: BLE001
            flash(f"Could not reach the roof engine: {e}", "error")
            return redirect(url_for("roof_reports.new"))
        db._ensure_column("roof_reports", "lead_id", "INTEGER")
        rid = db.insert("roof_reports", {
            "job_id": job_id, "lead_id": lead_id, "address": address,
            "engine_job": ej.get("id"), "status": ej.get("status", "queued"),
            "api_result": "",
        })
        if lead_id:
            db.add_activity("lead", int(lead_id), "note",
                            f"Aerial roof report ordered for {address} (report #{rid}).")
        return redirect(url_for("roof_reports.detail", report_id=rid))

    pre = {k: request.args.get(k, "") for k in ("address", "city", "zip", "lead_id", "job_id")}
    pre["state"] = request.args.get("state", "FL")
    return render_template("roof_reports_new.html",
                           jobs=db.all_rows("jobs", order="created DESC"),
                           leads=db.all_rows("leads", "stage NOT IN ('lost','canceled')",
                                             order="created DESC"),
                           pre=pre)


@bp.route("/<int:report_id>")
def detail(report_id):
    rr = db.get("roof_reports", report_id)
    if not rr:
        return redirect(url_for("roof_reports.index"))
    rr["result"] = json.loads(rr.get("api_result") or "{}")
    # Keyless link: the engine API key must NOT reach the browser, so the
    # "Trace from plans" link points at a CRM route that redirects server-side.
    takeoff_url = url_for("roof_reports.takeoff", report_id=report_id) if _configured() else "#"
    return render_template("roof_reports_detail.html", rr=rr, takeoff_url=takeoff_url)


@bp.route("/<int:report_id>/takeoff")
def takeoff(report_id):
    """Redirect to the engine takeoff tool, injecting the API key server-side so
    it never sits in the report page source. (Engine should move to a short-lived
    token so the key never lands in the browser at all — flagged to roofengine.)"""
    if not _configured():
        return redirect(url_for("roof_reports.detail", report_id=report_id))
    return redirect("%s/takeoff?api_key=%s" % (ENGINE_URL, ENGINE_KEY))


@bp.route("/<int:report_id>/status")
def status(report_id):
    """Polled by the detail page until the engine finishes; persists the result."""
    rr = db.get("roof_reports", report_id)
    if not rr:
        return jsonify({"ok": False, "error": "not found", "status": "missing"}), 404
    try:
        ej = _engine(f"/reports/{rr['engine_job']}")
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "status": rr.get("status") or "processing", "error": str(e)})
    st = ej.get("status")
    m = ej.get("measurement") or {}
    if st == "done":
        t = m.get("totals", {})
        db.update("roof_reports", report_id, status="done",
                  squares=str(t.get("squares", "")),
                  pitch=str(t.get("predominant_pitch", "")),
                  confidence=m.get("building_confidence", ""),
                  api_result=json.dumps(m))
    elif st == "failed":
        db.update("roof_reports", report_id, status="failed")
    return jsonify({"status": st, "measurement": m, "error": ej.get("error")})


@bp.route("/<int:report_id>/pdf")
def pdf(report_id):
    rr = db.get("roof_reports", report_id)
    if not rr:
        return redirect(url_for("roof_reports.index"))
    try:
        data = _engine(f"/reports/{rr['engine_job']}/pdf", raw=True)
    except Exception as e:  # noqa: BLE001
        flash(f"PDF not ready yet: {e}", "error")
        return redirect(url_for("roof_reports.detail", report_id=report_id))
    return Response(data, mimetype="application/pdf", headers={
        "Content-Disposition": f"inline; filename=roof-report-{report_id}.pdf"})


@bp.route("/<int:report_id>/assign", methods=["POST"])
def assign(report_id):
    """Link a roof report to a lead or job."""
    rr = db.get("roof_reports", report_id)
    if not rr:
        return jsonify({"ok": False, "error": "not found"}), 404
    kind = request.form.get("kind")
    target_id = request.form.get("target_id") or ""
    if kind not in ("lead", "job") or not target_id.isdigit():
        flash("Pick a lead or job to assign this report to.", "error")
        return redirect(url_for("roof_reports.index"))
    tid = int(target_id)
    if kind == "job":
        db._ensure_column("roof_reports", "job_id", "INTEGER")
        db.update("roof_reports", report_id, job_id=tid, lead_id=None)
        db.add_activity("job", tid, "note",
                        f"Roof report (ID {report_id}, {rr.get('address','')}) assigned.")
    else:
        db._ensure_column("roof_reports", "lead_id", "INTEGER")
        db.update("roof_reports", report_id, lead_id=tid, job_id=None)
        db.add_activity("lead", tid, "note",
                        f"Roof report (ID {report_id}, {rr.get('address','')}) assigned.")
    flash("Report assigned.", "ok")
    return redirect(url_for("roof_reports.index"))
