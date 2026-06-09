# -*- coding: utf-8 -*-
"""Global typeahead search — live suggestions across leads, jobs, and contacts
for the masthead search box (auto-populates as you type)."""
from flask import Blueprint, request, jsonify, url_for

import db

bp = Blueprint("search", __name__, url_prefix="/search")


def _match(row, fields, q):
    hay = " ".join(str(row.get(f) or "") for f in fields).lower()
    return q in hay


@bp.route("/suggest")
def suggest():
    """Return up to ~12 live matches (leads + jobs + contacts) for a query string."""
    q = (request.args.get("q") or "").strip().lower()
    if len(q) < 2:
        return jsonify({"results": []})
    out = []
    for l in db.all_rows("leads", order="id DESC"):
        if _match(l, ("name", "rid", "phone", "email", "address"), q):
            out.append({"type": "Lead", "name": l.get("name") or "(lead)",
                        "sub": " · ".join(p for p in (l.get("rid"), l.get("address")) if p),
                        "url": url_for("leads.detail", lead_id=l["id"])})
    for j in db.all_rows("jobs", order="id DESC"):
        if _match(j, ("name", "rid", "phone", "email", "address"), q):
            out.append({"type": "Job", "name": j.get("name") or "(job)",
                        "sub": " · ".join(p for p in (j.get("rid"), j.get("address")) if p),
                        "url": url_for("jobs.detail", job_id=j["id"])})
    for c in db.all_rows("contacts", order="id DESC"):
        if _match(c, ("first_name", "last_name", "company", "phone", "email"), q):
            nm = (" ".join(p for p in (c.get("first_name"), c.get("last_name")) if p).strip()
                  or c.get("company") or "(contact)")
            out.append({"type": "Contact", "name": nm,
                        "sub": c.get("phone") or c.get("email") or "",
                        "url": url_for("contacts.detail", contact_id=c["id"])})
        if len(out) > 60:
            break
    # Leads/jobs first (most actionable), capped.
    return jsonify({"results": out[:12], "q": q})
