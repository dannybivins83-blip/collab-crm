# -*- coding: utf-8 -*-
"""Global typeahead search — live suggestions across leads, jobs, and contacts
for the masthead search box (auto-populates as you type)."""
from flask import Blueprint, request, jsonify, url_for

import db
import theme

bp = Blueprint("search", __name__, url_prefix="/search")


def _sql_suggest(table, dept_col, dept, fields, like, limit=8):
    """SQL-side LIKE search across multiple columns. Returns matching rows."""
    where_parts = ["LOWER(%s) LIKE ?" % f for f in fields]
    params = [like] * len(fields)
    if dept_col:
        clause = "%s=? AND (%s)" % (dept_col, " OR ".join(where_parts))
        params = [dept] + params
    else:
        clause = " OR ".join(where_parts)
    return db.all_rows(table, clause, tuple(params), "id DESC", limit)


@bp.route("/suggest")
def suggest():
    """Return up to ~12 live matches (leads + jobs + contacts) for a query string.
    Uses SQL-side LIKE to avoid loading full tables into Python on every keystroke."""
    q = (request.args.get("q") or "").strip().lower()
    if len(q) < 2:
        return jsonify({"results": []})
    like = "%" + q + "%"
    dept = theme.current_department()
    out = []

    for l in _sql_suggest("leads", "department", dept, ("name", "rid", "phone", "email", "address"), like, 8):
        out.append({"type": "Lead", "name": l.get("name") or "(lead)",
                    "sub": " · ".join(p for p in (l.get("rid"), l.get("address")) if p),
                    "url": url_for("leads.detail", lead_id=l["id"])})
    for j in _sql_suggest("jobs", "department", dept, ("name", "rid", "phone", "email", "address"), like, 8):
        out.append({"type": "Job", "name": j.get("name") or "(job)",
                    "sub": " · ".join(p for p in (j.get("rid"), j.get("address")) if p),
                    "url": url_for("jobs.detail", job_id=j["id"])})
    for c in _sql_suggest("contacts", None, None, ("first_name", "last_name", "company", "phone", "email"), like, 8):
        nm = (" ".join(p for p in (c.get("first_name"), c.get("last_name")) if p).strip()
              or c.get("company") or "(contact)")
        out.append({"type": "Contact", "name": nm,
                    "sub": c.get("phone") or c.get("email") or "",
                    "url": url_for("contacts.detail", contact_id=c["id"])})

    # Leads/jobs first (most actionable), capped.
    return jsonify({"results": out[:12], "q": q})
