# -*- coding: utf-8 -*-
"""SiteCam — embedded field-photo platform (CompanyCam-style).

SiteCam is the company's own app (photos.seabreezeroofing.com / sitecam-web on
Render). It allows iframe embedding (no X-Frame-Options / frame-ancestors), so we
host it full-screen inside the CRM. The URL is a white-label company setting.

It also receives per-project **Client Gallery** links from SiteCam: when a crew
shares a project's read-only gallery, SiteCam POSTs the /g/<token> URL here and we
attach it to the matching job (`jobs.sitecam_url`) so the homeowner portal shows a
"View your project photos" button automatically. See `gallery_link()` below.
"""
import hmac
import re
from urllib.parse import urlparse

from flask import Blueprint, render_template, request, jsonify

import db

bp = Blueprint("sitecam", __name__, url_prefix="/sitecam")

DEFAULT_URL = "https://sitecam-web.onrender.com/"


def url():
    return (db.get_company().get("sitecam_url") or DEFAULT_URL).strip()


def _origin(u):
    """scheme://host for postMessage targeting (locks the SSO handoff to SiteCam)."""
    p = urlparse(u)
    return ("%s://%s" % (p.scheme, p.netloc)) if p.scheme and p.netloc else "*"


@bp.route("/")
def index():
    u = url()
    return render_template("sitecam.html", sitecam_url=u, sitecam_origin=_origin(u))


# ---------------------------------------------------------------------------
# Inbound: SiteCam → CRM gallery-link sync.
# Shares the SSO/webhook secret (SEABREEZE_CRM_WEBHOOK_SECRET) so no new secret
# is provisioned. Matching is deliberately conservative — a wrong match would
# leak another homeowner's photos — so address matches need street # AND zip.
# ---------------------------------------------------------------------------

def _sync_secret():
    """The shared SiteCam secret (reuses the SSO connected-app registry)."""
    from modules import sso
    secret, _is_dev = sso._secret(sso._apps()["sitecam"])
    return secret


def _street_num(s):
    m = re.match(r"\s*(\d+)", s or "")
    return m.group(1) if m else ""


def _zip5(s):
    m = re.search(r"(\d{5})(?:-\d{4})?", s or "")
    return m.group(1) if m else ""


def _match_job(data):
    """Find the CRM job a SiteCam project belongs to. Confident matches only:
    1) crm_job_id == jobs.id, 2) rid (R-number) exact, 3) street# AND zip both equal."""
    jobs = db.all_rows("jobs")
    jid = str(data.get("crm_job_id") or "").strip()
    if jid:
        for j in jobs:
            if str(j["id"]) == jid:
                return j, "crm_job_id"
    rid = re.sub(r"[^a-z0-9]", "", (data.get("rid") or "").lower())
    if rid:
        for j in jobs:
            if re.sub(r"[^a-z0-9]", "", (j.get("rid") or "").lower()) == rid:
                return j, "rid"
    addr = data.get("address") or ""
    snum = _street_num(addr.split(",")[0])
    szip = (data.get("zip") or "")[:5] or _zip5(addr)
    if snum and szip:
        for j in jobs:
            if _street_num(j.get("address")) == snum and (j.get("zip") or "")[:5] == szip:
                return j, "address"
    return None, None


@bp.route("/gallery", methods=["POST"])
def gallery_link():
    """SiteCam posts a project's Client Gallery link; we attach it to the job.
    Auth: shared secret in `x-sitecam-secret` (or Authorization: Bearer)."""
    sent = (request.headers.get("x-sitecam-secret")
            or request.headers.get("authorization", "").replace("Bearer ", "")).strip()
    secret = _sync_secret()
    if not sent or not secret or not hmac.compare_digest(sent, secret):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    gurl = (data.get("url") or "").strip()
    if not gurl:
        return jsonify({"ok": False, "error": "missing url"}), 400
    job, how = _match_job(data)
    if not job:
        return jsonify({"ok": False, "matched": False,
                        "error": "no confident job match"}), 200
    if (job.get("sitecam_url") or "") != gurl:
        db.update("jobs", job["id"], sitecam_url=gurl)
        db.add_activity("job", job["id"], "automation",
                        "📸 SiteCam project gallery linked to the homeowner portal")
    return jsonify({"ok": True, "matched": True, "job_id": job["id"], "by": how})
