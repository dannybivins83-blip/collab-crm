# -*- coding: utf-8 -*-
"""Communication log — email/SMS/call log per contact + draft emails for review.

Drafts are saved to the activity log only. Nothing is ever auto-sent.

SMS channel (send_sms): outbound text via Twilio when the three env vars
TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_FROM are all set; otherwise it
FAILS CLOSED — it logs an 'sms' activity (so the message is captured for review)
and returns False, never raising. No new dependency: the Twilio REST call is a
raw `requests` POST (requests is already vendored in via gmail.py's usage).
"""
import logging
import os

from flask import Blueprint, render_template, request, redirect, url_for, flash

import db
import theme

bp = Blueprint("comms", __name__, url_prefix="/comms")
_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SMS channel — Twilio if configured, else fail closed (log + return False)
# ---------------------------------------------------------------------------

def sms_configured():
    """True only when all three Twilio env vars are present."""
    return bool(os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
                and os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
                and os.environ.get("TWILIO_FROM", "").strip())


def send_sms(to, body, entity_type=None, entity_id=None):
    """Send an SMS via Twilio if configured; otherwise fail closed.

    Returns True only when Twilio accepts the message. When Twilio is NOT
    configured (or the send errors), we log an 'sms' activity against the
    given entity (if any) so the intended text is captured for review, and
    return False. This function NEVER raises — callers can rely on the bool.
    """
    to = (to or "").strip()
    body = (body or "").strip()
    if not to or not body:
        return False

    if not sms_configured():
        # Fail closed: record the intent in the activity log (today), no send.
        if entity_type and entity_id:
            try:
                db.add_activity(entity_type, int(entity_id), "sms",
                                "📱 SMS (not sent — Twilio not configured): " + body)
            except Exception:
                _log.exception("failed logging fail-closed sms activity")
        _log.info("send_sms: Twilio not configured; SMS to %s not sent (failed closed)", to)
        return False

    sid = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
    token = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
    from_ = os.environ.get("TWILIO_FROM", "").strip()
    try:
        import requests
        url = "https://api.twilio.com/2010-04-01/Accounts/%s/Messages.json" % sid
        r = requests.post(url, data={"To": to, "From": from_, "Body": body},
                          auth=(sid, token), timeout=20)
        ok = r.status_code in (200, 201)
        if ok:
            if entity_type and entity_id:
                try:
                    db.add_activity(entity_type, int(entity_id), "sms", "📱 SMS sent: " + body)
                except Exception:
                    _log.exception("sms sent but activity log failed")
            return True
        _log.error("send_sms: Twilio returned %s: %s", r.status_code, (r.text or "")[:300])
    except Exception:
        _log.exception("send_sms: Twilio request failed")
    # Any failure path: record intent + fail closed.
    if entity_type and entity_id:
        try:
            db.add_activity(entity_type, int(entity_id), "sms",
                            "📱 SMS (send failed): " + body)
        except Exception:
            _log.exception("failed logging sms send-failure activity")
    return False


def _name_for(et, eid):
    if et == "lead":
        r = db.get("leads", eid)
        return r["name"] if r else "?"
    if et == "job":
        r = db.get("jobs", eid)
        return r["name"] if r else "?"
    if et == "contact":
        r = db.get("contacts", eid)
        return "%s %s" % (r["first_name"], r["last_name"]) if r else "?"
    return "?"


@bp.route("/")
def index():
    dept = theme.current_department()
    dept_leads_list = db.all_rows("leads", "department=?", (dept,), "name")
    dept_jobs_list = db.all_rows("jobs", "department=?", (dept,), "name")
    _dept_lead_ids = tuple(l["id"] for l in dept_leads_list)
    _dept_job_ids = tuple(j["id"] for j in dept_jobs_list)
    # Fetch dept-scoped comms via IN() to avoid cross-tenant contamination.
    _parts, _params = [], []
    if _dept_lead_ids:
        _parts.append("(entity_type='lead' AND entity_id IN (%s))" % ",".join("?" * len(_dept_lead_ids)))
        _params.extend(_dept_lead_ids)
    if _dept_job_ids:
        _parts.append("(entity_type='job' AND entity_id IN (%s))" % ",".join("?" * len(_dept_job_ids)))
        _params.extend(_dept_job_ids)
    _parts.append("entity_type NOT IN ('lead','job')")
    _conn = db.connect()
    try:
        _where = "kind IN ('call','email','sms','draft') AND (%s)" % " OR ".join(_parts)
        rows = [dict(r) for r in _conn.execute(
            "SELECT * FROM activities WHERE %s ORDER BY id DESC LIMIT 200" % _where,
            tuple(_params)).fetchall()]
    finally:
        _conn.close()
    # Batch name lookups — only for IDs actually in this result set.
    _lmap = {l["id"]: l.get("name") or "?" for l in dept_leads_list}
    _jmap = {j["id"]: j.get("name") or "?" for j in dept_jobs_list}
    _cmap = {}
    _con_ids = tuple({a["entity_id"] for a in rows if a.get("entity_type") == "contact"})
    if _con_ids:
        ph = ",".join("?" * len(_con_ids))
        for r in db.all_rows("contacts", "id IN (%s)" % ph, _con_ids):
            _cmap[r["id"]] = ("%s %s" % (r.get("first_name", ""), r.get("last_name", ""))).strip() or "?"
    for a in rows:
        et, eid = a.get("entity_type"), a.get("entity_id")
        if et == "lead":      a["_who"] = _lmap.get(eid, "?")
        elif et == "job":     a["_who"] = _jmap.get(eid, "?")
        elif et == "contact": a["_who"] = _cmap.get(eid, "?")
        else:                 a["_who"] = "?"
    # Search/filter (applied after _who enrichment).
    q = request.args.get("q", "").strip().lower()
    kind_f = request.args.get("kind", "").strip()
    if q:
        rows = [a for a in rows if q in (a.get("_who") or "").lower()
                or q in (a.get("text") or "").lower()]
    if kind_f:
        rows = [a for a in rows if a.get("kind") == kind_f]
    return render_template("comms.html", logs=rows,
                           leads=dept_leads_list, jobs=dept_jobs_list,
                           contacts=db.all_rows("contacts", order="last_name", limit=200),
                           q=q, kind_f=kind_f)


@bp.route("/log", methods=["POST"])
def log():
    target = request.form.get("target", "")
    et, _, eid = target.partition(":")
    kind = request.form.get("kind", "call")
    text = request.form.get("text", "").strip()
    if et and eid and text:
        db.add_activity(et, int(eid), kind, text)
        if kind in ("call", "email", "sms") and et == "lead":
            db.update("leads", int(eid), last_contact=db.today())
        flash("Logged.", "ok")
    return redirect(url_for("comms.index"))


@bp.route("/draft", methods=["POST"])
def draft():
    """Save a draft email for review — never sent."""
    target = request.form.get("target", "")
    et, _, eid = target.partition(":")
    subject = request.form.get("subject", "").strip()
    body = request.form.get("body", "").strip()
    if et and eid and (subject or body):
        db.add_activity(et, int(eid), "draft", "✉️ DRAFT — %s\n%s" % (subject, body))
        flash("Draft saved for review (not sent).", "ok")
    return redirect(url_for("comms.index"))


@bp.route("/sms", methods=["POST"])
def sms():
    """Send an SMS via Twilio (fail-closed when unconfigured)."""
    target = request.form.get("target", "")
    et, _, eid = target.partition(":")
    to = request.form.get("to", "").strip()
    body = request.form.get("body", "").strip()
    if et and eid and to and body:
        ok = send_sms(to, body, entity_type=et, entity_id=int(eid))
        if ok:
            if et == "lead":
                db.update("leads", int(eid), last_contact=db.today())
            flash("SMS sent.", "ok")
        elif sms_configured():
            flash("SMS failed to send — logged for review.", "err")
        else:
            flash("SMS not sent (texting not configured) — logged for review.", "err")
    return redirect(url_for("comms.index"))
