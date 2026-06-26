# -*- coding: utf-8 -*-
"""In-app notifications — alert the team when a homeowner acts in their portal.

House rule (honored everywhere in this app): nothing is ever auto-sent. So when a
homeowner signs, uploads, or messages from the portal, we don't email/SMS — we
raise an **in-app alert**: a bell in the masthead with an unread badge + a feed.
Each notification targets the job's rep (admins see all). Clicking it opens the
job and marks the alert read. No external provider, keys, or deliverability — it
just works on the live serverless host.
"""
from datetime import datetime

from flask import (Blueprint, render_template, redirect, url_for, request,
                   abort)

import db

bp = Blueprint("notifications", __name__, url_prefix="/notifications")

# Schema (module-load convention — same as signups/portal).
db.execute("""CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created TEXT, job_id INTEGER, rep TEXT,
    kind TEXT, icon TEXT, text TEXT, read INTEGER DEFAULT 0)""")
db._COLCACHE.clear()

ICONS = {"sign": "✍️", "approve": "✅", "upload": "📎", "photo": "📸",
         "message": "💬", "request": "📝", "pay": "💳"}


def notify(job_id, kind, text, rep=None, icon=None):
    """Create an in-app alert for the team, targeted at the job's rep.

    Safe to call from public portal routes — never raises into the request."""
    try:
        if rep is None:
            j = db.get("jobs", job_id) or {}
            rep = j.get("rep") or ""
        db.insert("notifications", {
            "created": db.now(), "job_id": job_id, "rep": rep or "",
            "kind": kind, "icon": icon or ICONS.get(kind, "🔔"),
            "text": text, "read": 0})
    except Exception:
        pass


def _visible_where(user):
    """Reps see alerts for their own jobs (+ untargeted); admins see everything."""
    if user and user.get("role") == "admin":
        return "1=1", ()
    name = (user or {}).get("name") or ""
    return "(rep = ? OR rep = '' OR rep IS NULL)", (name,)


def _ago(iso):
    try:
        d = datetime.strptime((iso or "")[:19], "%Y-%m-%d %H:%M:%S")
        secs = (datetime.now() - d).total_seconds()
    except Exception:
        return ""
    if secs < 60:
        return "just now"
    if secs < 3600:
        return "%dm ago" % (secs // 60)
    if secs < 86400:
        return "%dh ago" % (secs // 3600)
    if secs < 7 * 86400:
        return "%dd ago" % (secs // 86400)
    return (iso or "")[:10]


def recent(user, limit=12):
    where, params = _visible_where(user)
    rows = db.all_rows("notifications", where, params, "id DESC", limit)
    for r in rows:
        r["ago"] = _ago(r.get("created"))
    return rows


def unread_count(user):
    where, params = _visible_where(user)
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM notifications WHERE (%s) AND read=0" % where, params
        ).fetchone()
        return row[0] if row else 0
    finally:
        conn.close()


# --- routes ----------------------------------------------------------------

@bp.route("/")
def index():
    from modules.auth import current_user
    user = current_user()
    where, params = _visible_where(user)
    rows = db.all_rows("notifications", where, params, "id DESC", 200)
    for r in rows:
        r["ago"] = _ago(r.get("created"))
    # Batch job name lookup — avoids one db.get() per notification row.
    _jids = tuple({r["job_id"] for r in rows if r.get("job_id")})
    _jmap = {}
    if _jids:
        _ph = ",".join("?" * len(_jids))
        for j in db.all_rows("jobs", "id IN (%s)" % _ph, _jids):
            _jmap[j["id"]] = j.get("name") or "—"
    for r in rows:
        r["job_name"] = _jmap.get(r.get("job_id"), "—")
    return render_template("notifications.html", notes=rows)


@bp.route("/<int:note_id>/go")
def go(note_id):
    """Open the job behind an alert and mark that alert read."""
    n = db.get("notifications", note_id)
    if not n:
        abort(404)
    db.update("notifications", note_id, read=1)
    if n.get("job_id"):
        return redirect(url_for("jobs.detail", job_id=n["job_id"]))
    return redirect(url_for("notifications.index"))


@bp.route("/read-all", methods=["POST"])
def read_all():
    from modules.auth import current_user
    where, params = _visible_where(current_user())
    db.execute("UPDATE notifications SET read=1 WHERE (%s) AND read=0" % where, params)
    nxt = request.form.get("next") or request.referrer
    return redirect(nxt if (nxt and nxt.startswith("/")) else url_for("dashboard.home"))


def init_notifications(app):
    app.register_blueprint(bp)

    @app.context_processor
    def _inject_notifs():
        # Runs on every page render — must never raise, or it breaks the whole UI.
        from flask import session
        if not session.get("user_id"):
            return {"notif_unread": 0, "notif_recent": []}
        try:
            from modules.auth import current_user
            u = current_user()
            return {"notif_unread": unread_count(u), "notif_recent": recent(u, 12)}
        except Exception:
            return {"notif_unread": 0, "notif_recent": []}
