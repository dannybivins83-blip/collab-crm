# -*- coding: utf-8 -*-
"""Toolbar utilities ported from the original job-manager board:
Export (CSV), Backup (JSON), Restore, Call Sheet (print), Checklist, Mass Email.
All brandable (sign-off from company settings) and draft-only (never auto-sends)."""
import csv
import io
import json

from flask import (Blueprint, render_template, request, redirect, url_for, flash,
                   Response, jsonify)

import db
import theme
import constants

bp = Blueprint("tools", __name__, url_prefix="/tools")

# Tables included in a full backup (data only; secrets are stripped below).
_BACKUP_TABLES = ["company_settings", "users", "contacts", "leads", "jobs", "activities",
                  "estimates", "estimate_sections", "estimate_lines", "documents", "photos",
                  "appointments", "invoices", "materials", "measurements", "templates",
                  "permits", "worksheets", "worksheet_lines", "orders", "order_lines",
                  "vendors", "commissions", "custom_fields", "custom_values", "automations"]
_SECRET_COLS = {"password_hash", "acculynx_api_key", "qbo_client_secret",
                "qbo_access_token", "qbo_refresh_token"}


def _existing_tables():
    conn = db.connect()
    if db.IS_PG:
        rows = conn.execute("SELECT table_name AS n FROM information_schema.tables "
                            "WHERE table_schema='public'").fetchall()
        names = {r["n"] for r in rows}
    else:
        rows = conn.execute("SELECT name AS n FROM sqlite_master WHERE type='table'").fetchall()
        names = {dict(r)["n"] for r in rows}
    conn.close()
    return names


# ---------------------------------------------------------------------------
# Backup / Restore
# ---------------------------------------------------------------------------

@bp.route("/backup")
def backup():
    have = _existing_tables()
    out = {"_meta": {"app": "whitelabel-crm", "exported": db.now()}}
    for t in _BACKUP_TABLES:
        if t not in have:
            continue
        rows = db.all_rows(t, order="id" if t != "company_settings" else "id")
        for r in rows:
            for c in list(r.keys()):
                if c in _SECRET_COLS:
                    r[c] = ""
        out[t] = rows
    data = json.dumps(out, indent=1, default=str)
    fname = "crm-backup-%s.json" % db.today()
    return Response(data, mimetype="application/json",
                    headers={"Content-Disposition": "attachment; filename=%s" % fname})


@bp.route("/restore", methods=["GET", "POST"])
def restore():
    if request.method == "POST":
        f = request.files.get("file")
        if not f or not f.filename:
            flash("Choose a backup .json file.", "error")
            return redirect(url_for("tools.restore"))
        try:
            payload = json.loads(f.read().decode("utf-8"))
        except Exception as e:
            flash("Could not read backup: %s" % e, "error")
            return redirect(url_for("tools.restore"))
        have = _existing_tables()
        restored = {}
        for t in _BACKUP_TABLES:
            if t not in payload or t not in have:
                continue
            rows = payload[t] or []
            db.execute("DELETE FROM %s" % t)
            for r in rows:
                r = {k: v for k, v in r.items() if k not in _SECRET_COLS}
                if r:
                    db.insert(t, r)
            restored[t] = len(rows)
        db._COLCACHE.clear()
        flash("Restored from backup: " + ", ".join("%s=%d" % (k, v) for k, v in restored.items() if v), "ok")
        return redirect(url_for("dashboard.home"))
    return render_template("tools_restore.html")


# ---------------------------------------------------------------------------
# Export CSV
# ---------------------------------------------------------------------------

_EXPORT_COLS = {
    "leads": ["rid", "name", "phone", "email", "address", "work_type", "rep", "source",
              "stage", "estimate", "last_contact", "next_follow"],
    "jobs": ["rid", "name", "phone", "email", "address", "city", "state", "zip", "work_type",
             "rep", "stage", "contract_value", "ahj", "system", "stage_since"],
    "contacts": ["first_name", "last_name", "company", "phone", "email", "address", "city",
                 "state", "zip", "source", "tags"],
}


@bp.route("/export")
def export():
    entity = request.args.get("entity", "leads")
    cols = _EXPORT_COLS.get(entity)
    if not cols:
        return redirect(url_for("dashboard.home"))
    rows = db.all_rows(entity, order="name" if entity != "contacts" else "last_name")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([c.replace("_", " ").title() for c in cols])
    for r in rows:
        w.writerow([r.get(c, "") for c in cols])
    fname = "%s-%s.csv" % (entity, db.today())
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=%s" % fname})


# ---------------------------------------------------------------------------
# Call Sheet (print) — everything due/overdue across leads + jobs
# ---------------------------------------------------------------------------

# Stage-action label shown on each card (top-right pill), by pipeline bucket.
_ACTION_BY_BUCKET = {"lead": "Sign-Up Docs", "prospect": "Sign-Up Docs",
                     "approved": "Job Docs", "completed": "Closeout + Final Pay",
                     "invoiced": "Final Payment / Lien Release"}
# Fallback checklist when a stage doesn't define one (mirrors the SeaBreeze docs sheet).
_DEFAULT_CHECKLIST = {
    "approved": ["Signed proposal", "Color/material selection confirmed", "Insurance/scope docs",
                 "Scan ALL signed docs into job folder", "Email copies of signed docs to client",
                 "Photos uploaded (SiteCam)", "RoofGraf uploaded to job documents"],
    "completed": ["Final inspection passed", "Punch list cleared", "Final photos uploaded",
                  "Warranty issued to homeowner"],
    "invoiced": ["Final invoice sent", "Payment link sent", "Lien release prepared",
                 "Review requested"],
}


def _card(kind, r, sd, fs):
    """Build one rich call-sheet card: contact + where-it-stands + checklist + the
    30/30/30/10 draw schedule with amounts + paid %. Mirrors the SeaBreeze job sheet."""
    checks = db.load_json(r.get("checks"), {})
    labels = sd.get("checklist") or _DEFAULT_CHECKLIST.get(sd["bucket"], [])
    checklist = [{"label": lbl, "done": bool(checks.get("%s:%d" % (sd["key"], i)))}
                 for i, lbl in enumerate(labels)]
    is_lead = kind == "Lead"
    value = theme.est_num(r.get("estimate") if is_lead else r.get("contract_value"))
    payments = {} if is_lead else db.load_json(r.get("payments"), {})
    draws = []
    for d in constants.DRAW_SCHEDULE:
        amt = round(value * d["pct"]) if value else 0
        draws.append({"pct": int(d["pct"] * 100), "amount": amt,
                      "paid": bool(payments.get(d["key"]))})
    return {
        "kind": kind, "r": r, "stage": sd["name"], "fs": fs,
        "action": _ACTION_BY_BUCKET.get(sd["bucket"], "Follow up"),
        "narrative": (r.get("narrative") or "").strip(),
        "todo": (r.get("todo") or "").strip() or sd["name"],
        "checklist": checklist,
        "value": value, "value_str": theme.money(value) if value else "",
        "draws": draws,
        "paid_pct": int(theme.paid_pct(payments) * 100) if not is_lead else 0,
    }


@bp.route("/callsheet")
def callsheet():
    dept = theme.current_department()
    bucket = request.args.get("bucket", "")     # ""=all buckets, else L/P/A/C/I key
    stage_key = request.args.get("stage", "")   # specific stage within the bucket
    due = request.args.get("due", "due")        # all | due | overdue

    def keep(fs):
        if due == "all":
            return True
        if due == "overdue":
            return fs["level"] == "hot"
        return fs["level"] != "ok"              # due = due-soon + overdue

    items = []
    for l in db.all_rows("leads", "department=?", (dept,)):
        if l["stage"] in constants.LEAD_INACTIVE:
            continue
        sd = constants.lead_stage(l["stage"])
        if bucket and sd["bucket"] != bucket:
            continue
        if stage_key and l["stage"] != stage_key:
            continue
        fs = theme.follow_status(sd, l.get("last_contact") or l.get("created"), l.get("snooze_until"))
        if keep(fs):
            items.append(_card("Lead", l, sd, fs))
    for j in db.all_rows("jobs", "department=?", (dept,)):
        if j["stage"] in constants.JOB_INACTIVE:
            continue
        sd = constants.job_stage(j["stage"])
        if bucket and sd["bucket"] != bucket:
            continue
        if stage_key and j["stage"] != stage_key:
            continue
        fs = theme.follow_status(sd, j.get("stage_since") or j.get("created"), j.get("snooze_until"))
        if keep(fs):
            items.append(_card("Job", j, sd, fs))
    items.sort(key=lambda x: (0 if x["fs"]["level"] == "hot" else 1, -x["fs"]["days"]))

    # Per-bucket headcounts for the big buttons + the active set's stage choices.
    counts = _bucket_counts(dept, due)
    stage_opts = []
    if bucket:
        for s in constants.LEAD_STAGES:
            if s["bucket"] == bucket and s["key"] not in constants.LEAD_INACTIVE:
                stage_opts.append({"key": s["key"], "name": s["name"]})
        for s in constants.JOB_STAGES:
            if s["bucket"] == bucket and s["key"] not in constants.JOB_INACTIVE:
                stage_opts.append({"key": s["key"], "name": s["name"]})
    return render_template("tools_callsheet.html", items=items, buckets=constants.BUCKETS,
                           counts=counts, sel_bucket=bucket, sel_stage=stage_key,
                           sel_due=due, stage_opts=stage_opts)


def _bucket_counts(dept, due):
    """How many calls each bucket would yield under the current due filter — shown
    as a badge on each big button so the rep sees where the work is."""
    def keep(fs):
        if due == "all":
            return True
        if due == "overdue":
            return fs["level"] == "hot"
        return fs["level"] != "ok"
    counts = {b["key"]: 0 for b in constants.BUCKETS}
    counts["_all"] = 0
    for l in db.all_rows("leads", "department=?", (dept,)):
        if l["stage"] in constants.LEAD_INACTIVE:
            continue
        sd = constants.lead_stage(l["stage"])
        fs = theme.follow_status(sd, l.get("last_contact") or l.get("created"), l.get("snooze_until"))
        if keep(fs):
            counts[sd["bucket"]] = counts.get(sd["bucket"], 0) + 1
            counts["_all"] += 1
    for j in db.all_rows("jobs", "department=?", (dept,)):
        if j["stage"] in constants.JOB_INACTIVE:
            continue
        sd = constants.job_stage(j["stage"])
        fs = theme.follow_status(sd, j.get("stage_since") or j.get("created"), j.get("snooze_until"))
        if keep(fs):
            counts[sd["bucket"]] = counts.get(sd["bucket"], 0) + 1
            counts["_all"] += 1
    return counts


# ---------------------------------------------------------------------------
# Checklist — team procedural reference
# ---------------------------------------------------------------------------

@bp.route("/checklist")
def checklist():
    return render_template("tools_checklist.html")


@bp.route("/drive-backfill", methods=["POST"])
def drive_backfill():
    """Push all existing local uploads to Google Drive so the cloud can serve them.
    Run once from the DESKTOP app (where the files live) after Drive is configured."""
    from modules import gdrive
    if not gdrive.enabled():
        flash("Google Drive isn't configured yet (set GDRIVE_SA_JSON + GDRIVE_FOLDER_ID).", "error")
        return redirect(url_for("settings.index"))
    res = gdrive.backfill_local()
    if res.get("ok"):
        flash("Drive backfill complete — pushed %d files (%d already there)." % (res["pushed"], res["skipped"]), "ok")
    else:
        flash("Backfill failed: %s" % res.get("error"), "error")
    return redirect(url_for("settings.index"))


# ---------------------------------------------------------------------------
# Mass Email — one draft per client by stage. Draft only (Gmail compose / copy).
# ---------------------------------------------------------------------------
LEAD_EMAIL = {
    "assigned": {"su": "Thanks for reaching out to {company}",
                 "need": "Thanks for your interest! I'd love to set up a free, no-obligation roof inspection at your convenience. What day this week works best for you?"},
    "prospect": {"su": "Let's get your roof inspection scheduled",
                 "need": "Following up to get your free roof inspection on the calendar — or, if we've already been out, to walk you through your estimate. Just let me know a good time."},
    "negotiation": {"su": "Checking in on your roof project",
                    "need": "Just checking in on your roof. Happy to answer any final questions and we offer financing if that helps — I'd love to earn your business."},
    "long_term": {"su": "Still here when you need us",
                  "need": "No rush on timing — whenever you'd like a fresh look at your roof, we're just a call away."},
    "won": {"su": "Welcome to {company}!",
            "need": "Welcome aboard, and thank you for choosing us! Our team will reach out with next steps and scheduling."},
    "lost": {"su": "Still here when you need us",
             "need": "If anything changes down the road, we'd be glad to take another look at your roof."},
}
JOB_EMAIL = {
    "approved": {"su": "Welcome — next steps for your roof",
                 "need": "Now that your project is approved, our next step is your sign-up documents and color/material selection so we can submit for your permit."},
    "documentation": {"su": "A couple items needed to move your roof forward",
                      "need": "To keep things moving we still need your signed sign-up documents and color/material selection. As soon as we have those we'll submit your permit package."},
    "permit_applied": {"su": "Your roofing permit has been submitted",
                       "need": "Good news — your permit package has been submitted to the building department. We're monitoring it and will reach out the moment it's approved."},
    "permit_approved": {"su": "Your permit is approved — let's schedule your roof!",
                        "need": "Your permit is approved! Next we'll confirm a start date, order materials, and set up the crew."},
    "precon_needed": {"su": "Scheduling your roof installation",
                      "need": "We're in pre-construction — coordinating your crew, materials, and start date. We'll confirm the schedule shortly."},
    "teardown_started": {"su": "Your roof installation is underway",
                         "need": "Your installation has started. We'll keep you posted through tear-off, dry-in, install, and inspection."},
    "final_needed": {"su": "Almost done — final inspection on your roof",
                     "need": "Your roof is installed and we're wrapping up with the final inspection and clean-up. We'll let you know the moment it passes."},
    "completed": {"su": "Thank you from {company}",
                  "need": "Thank you for trusting us with your roof — your project is complete! We'll send your warranty info, and we'd be grateful for a quick review and any referrals."},
}


def _draft(rec, kind):
    company = db.get_company()
    cname = company.get("name", "our team")
    tbl = LEAD_EMAIL if kind == "lead" else JOB_EMAIL
    t = tbl.get(rec["stage"], {"su": "Following up on your roof",
                               "need": "Just following up on your roofing project — let me know if I can help."})
    first = (rec.get("name") or "there").split(" ")[0]
    su = t["su"].format(company=cname)
    phone = company.get("phone", "")
    body = ("Hi %s,\n\n%s\n\nIf you have any questions, just reply or call %s.\n\nThank you,\n%s\n%s\n%s"
            % (first, t["need"], phone, rec.get("rep") or company.get("qualifier", ""), cname, phone))
    return su, body


@bp.route("/dev-note", methods=["POST"])
def dev_note():
    import json as _json, datetime as _dt
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    body_text = (data.get("body") or "").strip()
    page = (data.get("page") or "").strip()
    if not title and not body_text:
        return jsonify({"error": "empty note"}), 400
    line = "[%s] %s%s%s\n" % (
        _dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        (title + " -- ") if title else "",
        body_text,
        (" (on " + page + ")") if page else "",
    )
    try:
        import os
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "dev_notes.txt")
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"ok": True})


@bp.route("/delegate", methods=["POST"])
def delegate():
    """Write a Danny→agent command to the OVERLORD bus.
    Body: {message, url, page_title, context, lane, priority}"""
    import json as _json, datetime as _dt, os as _os
    from modules.auth import current_user as _cu
    if not _cu():
        return jsonify({"ok": False, "error": "not logged in"}), 401
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"ok": False, "error": "empty message"}), 400
    url = (data.get("url") or "").strip()[:200]
    page_title = (data.get("page_title") or "").strip()[:120]
    context = (data.get("context") or "").strip()[:600]
    screenshot_path = (data.get("screenshot_path") or "").strip()[:300]
    priority = (data.get("priority") or "P1").strip().upper()
    if priority not in ("P0", "P1", "P2", "P3"):
        priority = "P1"
    lane = (data.get("lane") or "crm-coord").strip()
    KNOWN_LANES = {"crm-coord", "crm-ui", "security", "sitecam", "roofengine",
                   "takeoff", "permit", "appconnect", "overlord"}
    if lane not in KNOWN_LANES:
        lane = "crm-coord"
    now = _dt.datetime.now()
    ts = now.strftime("%Y-%m-%dT%H%M")
    task_id = "%s__danny__dispatch" % ts
    frontmatter = ("---\nto: %s\nfrom: danny\ncreated: %s\nsubject: %s\n"
                   "status: new\npriority: %s\nneeds_ack: true\n"
                   "action_required: OPEN-AND-EXTRACT-TODOS\n---\n") % (
        lane, now.isoformat(), message[:80].replace("\n", " "), priority)
    body = "\U0001F6A9 FLAG — OPEN & ANALYZE FOR TO-DOS: Read in full, pull every action item into your task list, then ack. \U0001F6A9\n\n"
    body += "## Instruction\n%s\n\n" % message
    if page_title or url:
        body += "**Page:** %s\n**URL:** %s\n\n" % (page_title or "(untitled)", url or "(none)")
    if context:
        body += "**Selected text:**\n> %s\n\n" % context
    if screenshot_path:
        body += "**Screenshot:** `%s`\n\n" % screenshot_path
    body += "**Priority:** %s | **From:** Danny (CRM dispatch widget %s)\n" % (priority, now.strftime("%Y-%m-%d %H:%M"))
    body += "\n---\nTO: %s\nFROM: danny\nSTATUS: new\nSUMMARY: in-CRM dispatch\nNEEDS: nothing" % lane
    try:
        crm_root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
        inbox_dir = _os.path.normpath(_os.path.join(crm_root, "..", "_OVERLORD", "bus", "inbox", lane))
        _os.makedirs(inbox_dir, exist_ok=True)
        fpath = _os.path.join(inbox_dir, "%s__danny__dispatch.md" % ts)
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(frontmatter + body)
    except Exception as e:
        return jsonify({"ok": False, "error": "bus write failed: %s" % e}), 500
    try:
        log_path = _os.path.join(crm_root, "dispatch_log.jsonl")
        entry = _json.dumps({"ts": now.isoformat(), "task_id": task_id,
                              "message": message[:120], "lane": lane,
                              "priority": priority, "url": url}) + "\n"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception:
        pass
    return jsonify({"ok": True, "task_id": task_id, "lane": lane, "ts": now.isoformat()})


@bp.route("/delegate-log")
def delegate_log():
    """Return last 10 dispatched tasks for the floating widget history."""
    import json as _json, os as _os
    from modules.auth import current_user as _cu
    if not _cu():
        return jsonify({"ok": False}), 401
    log_path = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "dispatch_log.jsonl")
    entries = []
    try:
        with open(log_path, encoding="utf-8") as f:
            for line in f:
                try:
                    entries.append(_json.loads(line.strip()))
                except Exception:
                    pass
    except FileNotFoundError:
        pass
    return jsonify({"ok": True, "entries": entries[-10:][::-1]})


@bp.route("/dispatch-screenshot", methods=["POST"])
def dispatch_screenshot():
    """Save a base64 PNG screenshot sent from the dispatch widget.
    Body: {image: 'data:image/png;base64,...', task_id}
    Saves to _OVERLORD/bus/attachments/ and returns the file path."""
    import base64 as _b64, os as _os, datetime as _dt, re as _re
    from modules.auth import current_user as _cu
    if not _cu():
        return jsonify({"ok": False, "error": "not logged in"}), 401
    data = request.get_json(silent=True) or {}
    img_data = (data.get("image") or "").strip()
    if not img_data.startswith("data:image/"):
        return jsonify({"ok": False, "error": "invalid image"}), 400
    # Strip data URL header
    try:
        _, b64 = img_data.split(",", 1)
        raw = _b64.b64decode(b64)
    except Exception as e:
        return jsonify({"ok": False, "error": "decode failed: %s" % e}), 400
    if len(raw) > 8 * 1024 * 1024:  # 8 MB cap
        return jsonify({"ok": False, "error": "image too large"}), 400
    ts = _dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    task_id = _re.sub(r"[^A-Za-z0-9_-]", "", (data.get("task_id") or ts))[:40]
    fname = "%s__%s.png" % (ts, task_id)
    crm_root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    attach_dir = _os.path.normpath(_os.path.join(crm_root, "..", "_OVERLORD", "bus", "attachments"))
    try:
        _os.makedirs(attach_dir, exist_ok=True)
        fpath = _os.path.join(attach_dir, fname)
        with open(fpath, "wb") as f:
            f.write(raw)
    except Exception as e:
        return jsonify({"ok": False, "error": "save failed: %s" % e}), 500
    return jsonify({"ok": True, "path": fpath, "filename": fname, "size_kb": len(raw) // 1024})


@bp.route("/team-messages", methods=["GET", "POST"])
def team_messages():
    from modules.auth import current_user as _cu
    u = _cu()
    if not u:
        return jsonify({"ok": False, "error": "not logged in"}), 401
    conn = db.connect()
    conn.execute("""CREATE TABLE IF NOT EXISTS team_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created TEXT, user_id INTEGER, user_name TEXT, body TEXT)""")
    conn.commit()
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        body = (data.get("body") or "").strip()
        if not body:
            conn.close()
            return jsonify({"ok": False, "error": "empty message"}), 400
        conn.execute(
            "INSERT INTO team_messages (created, user_id, user_name, body) VALUES (?,?,?,?)",
            (db.now(), u.get("id"), u.get("name") or u.get("email") or "Unknown", body))
        conn.commit()
        conn.close()
        return jsonify({"ok": True})
    rows = conn.execute(
        "SELECT id, created, user_name, body FROM team_messages ORDER BY id DESC LIMIT 60"
    ).fetchall()
    conn.close()
    msgs = [dict(r) for r in rows]
    msgs.reverse()
    return jsonify({"ok": True, "messages": msgs})


@bp.route("/team-messages/<int:msg_id>", methods=["DELETE"])
def delete_team_message(msg_id):
    from modules.auth import current_user as _cu
    u = _cu()
    if not u:
        return jsonify({"ok": False}), 401
    conn = db.connect()
    row = conn.execute("SELECT user_id FROM team_messages WHERE id=?", (msg_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"ok": False, "error": "not found"}), 404
    row = dict(row)
    if row["user_id"] != u.get("id") and u.get("role") != "admin" and not u.get("is_owner"):
        conn.close()
        return jsonify({"ok": False, "error": "forbidden"}), 403
    conn.execute("DELETE FROM team_messages WHERE id=?", (msg_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@bp.route("/mass-email")
def mass_email():
    kind = request.args.get("kind", "lead")
    stage_f = request.args.get("stage", "")
    due_only = request.args.get("due", "1") == "1"
    dept = theme.current_department()
    table = "leads" if kind == "lead" else "jobs"
    stages = constants.LEAD_STAGES if kind == "lead" else constants.JOB_STAGES
    clock = (lambda r: r.get("last_contact") or r.get("created")) if kind == "lead" \
        else (lambda r: r.get("stage_since") or r.get("created"))
    sdef = constants.lead_stage if kind == "lead" else constants.job_stage
    recips = []
    for r in db.all_rows(table, "department=?", (dept,)):
        if "@" not in (r.get("email") or ""):
            continue
        if stage_f and r["stage"] != stage_f:
            continue
        fs = theme.follow_status(sdef(r["stage"]), clock(r), r.get("snooze_until"))
        if due_only and fs["level"] == "ok":
            continue
        su, body = _draft(r, kind)
        recips.append({"r": r, "fs": fs, "stage_name": sdef(r["stage"])["name"], "su": su, "body": body})
    recips.sort(key=lambda x: (0 if x["fs"]["level"] == "hot" else 1, -x["fs"]["days"]))
    return render_template("tools_massemail.html", recips=recips, kind=kind, stage_f=stage_f,
                           due_only=due_only, stages=stages)
