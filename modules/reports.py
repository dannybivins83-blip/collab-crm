# -*- coding: utf-8 -*-
"""Reports & dashboards — pipeline value, win rate, revenue, leaderboard, aging."""
import os
import time

from flask import Blueprint, render_template, abort

import config
import db
import theme
import constants

bp = Blueprint("reports", __name__, url_prefix="/reports")


# --- Owner gate -------------------------------------------------------------
# The System Map is owner-only (just Danny), narrower than the existing "admin"
# role (Karla is admin-adjacent office). We add an is_owner flag on users and
# seed it ONCE onto the primary admin; everyone else gets 403 + no menu item.
def _ensure_owner_flag():
    try:
        db._ensure_column("users", "is_owner", "INTEGER DEFAULT 0")
        users = db.all_rows("users")
        if users and not any(u.get("is_owner") for u in users):
            admins = sorted((u for u in users if u.get("role") == "admin"),
                            key=lambda u: u["id"])
            target = admins[0] if admins else sorted(users, key=lambda u: u["id"])[0]
            db.update("users", target["id"], is_owner=1)
    except Exception:
        pass


_ensure_owner_flag()


def _require_owner():
    from modules.auth import current_user
    _ensure_owner_flag()  # re-seed if column missing or nobody flagged yet
    u = current_user()
    if not (u and u.get("is_owner")):
        abort(403)
    return u


@bp.route("/")
def index():
    dept = theme.current_department()
    leads = db.all_rows("leads", "department=?", (dept,))
    jobs = db.all_rows("jobs", "department=?", (dept,))
    dept_job_ids = {j["id"] for j in jobs}
    all_inv = db.all_rows("invoices")
    invoices = [i for i in all_inv if not i.get("job_id") or i["job_id"] in dept_job_ids]

    # Pipeline by lead stage.
    lead_rows = []
    for s in constants.LEAD_STAGES:
        items = [l for l in leads if l["stage"] == s["key"]]
        lead_rows.append({"name": s["name"], "count": len(items),
                          "value": sum(theme.est_num(l.get("estimate")) for l in items)})

    # Production by job stage.
    job_rows = []
    for s in constants.JOB_STAGES:
        items = [j for j in jobs if j["stage"] == s["key"]]
        job_rows.append({"name": s["name"], "count": len(items),
                         "value": sum(theme.est_num(j.get("contract_value")) for j in items)})

    won = [l for l in leads if l["stage"] == "won"]
    lost = [l for l in leads if l["stage"] == "lost"]
    decided = len(won) + len(lost)
    win_rate = round(100 * len(won) / decided) if decided else 0

    # Revenue: collected invoices + won contract value.
    revenue_collected = sum(i["amount"] or 0 for i in invoices if i["status"] == "paid")
    outstanding = sum(i["amount"] or 0 for i in invoices if i["status"] != "paid")

    # Leaderboard by rep (won value + active job value).
    board = {}
    for l in won:
        board.setdefault(l.get("rep") or "—", {"won": 0, "deals": 0})
        board[l.get("rep") or "—"]["won"] += theme.est_num(l.get("estimate"))
        board[l.get("rep") or "—"]["deals"] += 1
    leaderboard = sorted(board.items(), key=lambda kv: -kv[1]["won"])

    # Overdue follow-ups count by source.
    by_source = {}
    for l in leads:
        if l["stage"] in ("won", "lost"):
            continue
        sd = constants.lead_stage(l["stage"])
        fs = theme.follow_status(sd, l.get("last_contact") or l.get("created"), l.get("snooze_until"))
        src = l.get("source") or "—"
        by_source.setdefault(src, {"open": 0, "overdue": 0, "value": 0})
        by_source[src]["open"] += 1
        by_source[src]["value"] += theme.est_num(l.get("estimate"))
        if fs["level"] != "ok":
            by_source[src]["overdue"] += 1
    source_rows = sorted(by_source.items(), key=lambda kv: -kv[1]["value"])

    return render_template("reports.html",
                           lead_rows=lead_rows, job_rows=job_rows,
                           win_rate=win_rate, won=len(won), lost=len(lost),
                           pipeline_value=sum(r["value"] for r in lead_rows[:-2]),
                           job_value=sum(r["value"] for r in job_rows[:-1]),
                           revenue_collected=revenue_collected, outstanding=outstanding,
                           leaderboard=leaderboard, source_rows=source_rows)


# ===========================================================================
# OWNER-ONLY SYSTEM MAP  (/reports/system-map)
# A living map of the CRM: lead-onboarding flow + live disk tree + live DB
# census. Numbers are queried at request time (never hardcoded). Cached 60s.
# ===========================================================================
def _rows(sql, params=()):
    """Run a read-only SELECT and return a list of plain dicts (SQLite + PG)."""
    conn = db.connect()
    try:
        cur = conn.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _human(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024
    return "%.1f TB" % n


def _folder_stat(path):
    files, size = 0, 0
    for root, _dirs, fnames in os.walk(path):
        for f in fnames:
            try:
                size += os.path.getsize(os.path.join(root, f))
                files += 1
            except OSError:
                pass
    return files, size


def _disk_tree():
    """Walk the data dir (databases, blue) + upload subfolders (file folders,
    amber). Per-node file count + bytes."""
    out = []
    if not db.IS_PG and os.path.exists(config.DB_PATH):
        out.append({"kind": "db", "name": "data/crm.db", "files": 1,
                    "bytes": os.path.getsize(config.DB_PATH),
                    "human": _human(os.path.getsize(config.DB_PATH))})
    elif db.IS_PG:
        out.append({"kind": "db", "name": "Postgres (Neon) — remote", "files": 0,
                    "bytes": 0, "human": "remote"})
    try:
        for entry in sorted(os.scandir(config.UPLOAD_DIR), key=lambda e: e.name):
            if entry.is_dir():
                files, size = _folder_stat(entry.path)
                out.append({"kind": "files", "name": "uploads/" + entry.name,
                            "files": files, "bytes": size, "human": _human(size)})
    except FileNotFoundError:
        pass
    return out


# table -> display group
_CENSUS_GROUPS = [
    ("Pipeline", ["leads", "jobs", "contacts", "estimates", "estimate_sections",
                  "estimate_lines", "permits", "measurements"]),
    ("Money", ["invoices", "payments", "commissions", "orders", "order_lines"]),
    ("Automation", ["activities", "automations", "notifications", "tasks"]),
    ("Content", ["documents", "photos", "library", "templates"]),
]


def _table_names():
    if db.IS_PG:
        rows = _rows("SELECT tablename AS name FROM pg_tables WHERE schemaname='public'")
    else:
        rows = _rows("SELECT name FROM sqlite_master WHERE type='table' "
                     "AND name NOT LIKE 'sqlite_%'")
    return {r["name"] for r in rows}


def _count(table):
    try:
        r = _rows("SELECT COUNT(*) AS n FROM " + table)  # table from catalog, not user input
        return int(r[0]["n"]) if r else 0
    except Exception:
        return None


def _breakdown(table, col):
    try:
        rows = _rows("SELECT %s AS k, COUNT(*) AS n FROM %s GROUP BY %s ORDER BY n DESC"
                     % (col, table, col))
        return [(r["k"] or "-", int(r["n"])) for r in rows]
    except Exception:
        return []


def _db_census():
    present = _table_names()
    groups = []
    for gname, tables in _CENSUS_GROUPS:
        rows = []
        for t in tables:
            if t in present:
                rows.append({"table": t, "count": _count(t)})
        if rows:
            groups.append({"name": gname, "rows": rows})
    # ungrouped leftovers
    grouped = {t for _g, ts in _CENSUS_GROUPS for t in ts}
    other = sorted(present - grouped)
    if other:
        groups.append({"name": "Other",
                       "rows": [{"table": t, "count": _count(t)} for t in other]})

    # --- red flags ---------------------------------------------------------
    flags = []
    inv = _count("invoices") if "invoices" in present else None
    if inv == 0:
        flags.append("invoices table is EMPTY (0 rows) - billing not migrated/linked.")
    if "payments" in present and _count("payments") == 0:
        flags.append("payments table is EMPTY (0 rows) - collections not rolled up.")
    # documents rows vs actual files on disk
    doc_rows = _count("documents") if "documents" in present else None
    doc_dir = os.path.join(config.UPLOAD_DIR, "documents")
    disk_docs = _folder_stat(doc_dir)[0] if os.path.isdir(doc_dir) else 0
    if doc_rows is not None and disk_docs and doc_rows != disk_docs:
        flags.append("documents: %d DB rows vs %d files on disk - %d unregistered."
                     % (doc_rows, disk_docs, disk_docs - doc_rows))

    breakdowns = {}
    if "leads" in present:
        breakdowns["leads by stage"] = _breakdown("leads", "stage")
    if "jobs" in present:
        breakdowns["jobs by stage"] = _breakdown("jobs", "stage")
    if "estimates" in present:
        breakdowns["estimates by status"] = _breakdown("estimates", "status")
    return {"groups": groups, "flags": flags, "breakdowns": breakdowns,
            "doc_rows": doc_rows, "disk_docs": disk_docs}


_SM_CACHE = {"at": 0.0, "data": None}


@bp.route("/system-map")
def system_map():
    _require_owner()
    now = time.time()
    if not _SM_CACHE["data"] or (now - _SM_CACHE["at"]) > 60:
        _SM_CACHE["data"] = {"disk": _disk_tree(), "census": _db_census()}
        _SM_CACHE["at"] = now
    d = _SM_CACHE["data"]
    return render_template("reports_system_map.html",
                           disk=d["disk"], census=d["census"])
