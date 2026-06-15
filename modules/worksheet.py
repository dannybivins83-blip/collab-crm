# -*- coding: utf-8 -*-
"""Worksheet + Profit Analysis (AccuLynx parity).

A Worksheet sets a job's contract value and tracks budget vs actual cost per
category, rolling up to gross profit + variance. Seeds from the job's signed
estimate (estimate section line costs -> budget lines).
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify

import db
import theme
import constants
from modules import estimates as est

bp = Blueprint("worksheet", __name__, url_prefix="/worksheet")

CATEGORIES = ["Material", "Labor", "Wood Replacement", "Permit", "Overhead", "Other"]


@bp.app_context_processor
def _inject():
    # Lets any template surface job profit without each view passing it.
    return {"job_profit": profit_analysis}


def _category_for(desc, unit):
    d = (desc or "").lower()
    if "permit" in d or "inspection" in d:
        return "Permit"
    if "dumpster" in d or "disposal" in d or "overhead" in d:
        return "Overhead"
    if any(w in d for w in ("tear off", "tear-off", "re-nail", "re nail", "renail",
                            "install", "labor", "dry-in", "dry in", "cleanup", "magnet")):
        return "Labor"
    return "Material"


def for_job(job_id):
    rows = db.all_rows("worksheets", "job_id=?", (job_id,), "id DESC")
    return rows[0] if rows else None


def lines_for(ws_id):
    return db.all_rows("worksheet_lines", "worksheet_id=?", (ws_id,), "sort, id")


def _est_line_count(eid):
    try:
        return len(db.all_rows("estimate_lines", "estimate_id=?", (eid,)))
    except Exception:
        return 0


def _signed_estimate(job_id):
    """The estimate to seed the worksheet from: the signed one, else the estimate with
    the MOST line items (a populated estimate beats an empty synced header), else the
    most recent. Picking by line count avoids seeding from a 0-line estimate."""
    rows = db.all_rows("estimates", "job_id=?", (job_id,), "id DESC")
    if not rows:
        return None
    signed = [e for e in rows if e.get("status") == "signed"]
    if signed:
        # among signed, still prefer the one that actually has lines
        signed.sort(key=lambda e: _est_line_count(e["id"]), reverse=True)
        return signed[0]
    rows_by_lines = sorted(rows, key=lambda e: _est_line_count(e["id"]), reverse=True)
    return rows_by_lines[0]


def seed_from_estimate(ws_id, job_id):
    """Build the worksheet from the job's estimate line items (the estimate is built
    from the per-system estimate template). Each estimate line becomes a worksheet
    line with its Qty | Unit | Unit Cost | Cost. Then append a Wood Replacement line
    at $0.01 — a placeholder the supervisor edits on tear-off day with the actual
    decking/fascia replaced. Skips the optional 'Upgrades & Options' section and any
    line not turned on (zero cost)."""
    e = _signed_estimate(job_id)
    if not e:
        return 0
    sections = est._load_sections(e["id"])
    totals = est.estimate_totals(e, sections)
    db.execute("DELETE FROM worksheet_lines WHERE worksheet_id=?", (ws_id,))
    i = 0
    for s in sections:
        for ln in s["_lines"]:
            cost = est.line_cost(ln)              # extended: qty × (1+waste) × unit cost
            if not cost:
                continue                          # line not turned on (qty 0) — skip
                                                  # (so un-selected upgrades drop out,
                                                  #  but accepted ones flow through)
            base_qty = ln.get("qty") or 0
            eff_qty = round(base_qty * (1 + (ln.get("waste_pct") or 0) / 100.0), 2)
            db.insert("worksheet_lines", {
                "worksheet_id": ws_id, "sort": i,
                "category": _category_for(ln.get("description"), ln.get("unit")),
                "description": ln.get("description", ""),
                "qty": eff_qty, "unit": ln.get("unit") or "",
                "unit_cost": ln.get("cost") or 0,
                "budget_cost": cost, "actual_cost": cost})
            i += 1
    # Wood Replacement placeholder — supervisor enters sheets of plywood / LF of
    # fascia at unit price on the day of tear-off (Qty × Unit Cost auto-fills Cost).
    db.insert("worksheet_lines", {
        "worksheet_id": ws_id, "sort": i, "category": "Wood Replacement",
        "description": "Wood replacement (decking / fascia — enter actuals at tear-off)",
        "qty": 0, "unit": "EA", "unit_cost": 0.01,
        "budget_cost": 0.01, "actual_cost": 0.01})
    db.update("worksheets", ws_id, contract_value=totals["total"], seeded_from_estimate=e["id"])
    return i + 1


def get_or_create(job_id):
    ws = for_job(job_id)
    if ws:
        # Auto-(re)seed if the worksheet is still empty or only the $0.01 placeholder
        # (e.g. it was seeded earlier when the estimate had no line items) AND the
        # estimate now has real lines. Never touches a worksheet that's been built out.
        real = [l for l in lines_for(ws["id"]) if (l.get("budget_cost") or 0) > 1]
        if not real:
            e = _signed_estimate(job_id)
            if e and _est_line_count(e["id"]):
                seed_from_estimate(ws["id"], job_id)
                return for_job(job_id)
        return ws
    job = db.get("jobs", job_id) or {}
    wid = db.insert("worksheets", {"job_id": job_id,
                                   "contract_value": theme.est_num(job.get("contract_value"))})
    seed_from_estimate(wid, job_id)
    return db.get("worksheets", wid)


def profit_analysis(job_id):
    """Return the profit rollup for a job (0s if no worksheet yet)."""
    ws = for_job(job_id)
    if not ws:
        job = db.get("jobs", job_id) or {}
        cv = theme.est_num(job.get("contract_value"))
        return {"has_ws": False, "contract_value": cv, "budget_cost": 0, "actual_cost": 0,
                "gross_profit": cv, "gross_pct": 100.0 if cv else 0, "variance": 0,
                "budget_profit": cv}
    lines = lines_for(ws["id"])
    budget = sum(l.get("budget_cost") or 0 for l in lines)
    actual = sum(l.get("actual_cost") or 0 for l in lines)
    cv = ws.get("contract_value") or 0
    gp = cv - actual
    return {"has_ws": True, "contract_value": cv, "budget_cost": budget, "actual_cost": actual,
            "gross_profit": gp, "gross_pct": (gp / cv * 100.0) if cv else 0,
            "variance": budget - actual, "budget_profit": cv - budget}


# ---- routes ---------------------------------------------------------------

def _build_synced_sections(ws_lines):
    """Rebuild AccuLynx hierarchical view from flat worksheet_lines rows.
    Returns list of sections, each with scope_items[] and groups[{name, price, items[]}]."""
    sections = {}
    section_order = []
    for ln in sorted(ws_lines, key=lambda x: x.get("sort") or 0):
        sec = ln.get("ws_section") or ""
        grp = ln.get("ws_group") or ""
        itype = ln.get("item_type") or "material"
        if sec not in sections:
            sections[sec] = {"title": sec, "scope_items": [], "groups": {}, "_gorder": []}
            section_order.append(sec)
        s = sections[sec]
        if itype == "scope":
            s["scope_items"].append({"letter": ln.get("scope_letter") or "", "text": ln.get("description") or ""})
        elif itype == "group_header":
            if grp not in s["groups"]:
                s["groups"][grp] = {"name": grp, "price": 0, "lines": []}
                s["_gorder"].append(grp)
            s["groups"][grp]["price"] = ln.get("price") or ln.get("budget_cost") or 0
        else:  # material
            if grp not in s["groups"]:
                s["groups"][grp] = {"name": grp, "price": 0, "lines": []}
                s["_gorder"].append(grp)
            s["groups"][grp]["lines"].append(ln)
    result = []
    for sec_key in section_order:
        s = sections[sec_key]
        groups = [s["groups"][g] for g in s["_gorder"]]
        if not groups and not s["scope_items"] and sec_key == "":
            # Ungrouped flat lines — put them in one unnamed group
            flat = [ln for ln in ws_lines if not (ln.get("ws_section") or "") and
                    (ln.get("item_type") or "material") == "material"]
            if flat:
                groups = [{"name": "", "price": 0, "lines": flat}]
        result.append({"title": s["title"], "scope_items": s["scope_items"], "groups": groups})
    return result


@bp.route("/<int:job_id>")
def view(job_id):
    job = db.get("jobs", job_id)
    if not job:
        return redirect(url_for("jobs.board"))
    ws = get_or_create(job_id)
    all_lines = lines_for(ws["id"])
    synced_sections = _build_synced_sections(all_lines)
    # Only show the AccuLynx hierarchical view when there are synced (hierarchical) lines.
    has_synced = any(ln.get("ws_section") or ln.get("ws_group") or ln.get("item_type") not in (None, "", "material")
                     for ln in all_lines)
    try:
        catalog = db.all_rows("material_catalog", order="name")
    except Exception:
        catalog = []
    return render_template("worksheet.html", job=job, ws=ws, lines=all_lines,
                           synced_sections=synced_sections if has_synced else [],
                           categories=CATEGORIES, profit=profit_analysis(job_id),
                           draws=constants.DRAW_SCHEDULE, catalog=catalog)


@bp.route("/<int:job_id>/seed", methods=["POST"])
def seed(job_id):
    ws = get_or_create(job_id)
    n = seed_from_estimate(ws["id"], job_id)
    flash("Worksheet seeded from estimate (%d lines)." % n if n else
          "No estimate found to seed from.", "ok" if n else "error")
    return redirect(url_for("worksheet.view", job_id=job_id))


@bp.route("/<int:job_id>/save", methods=["POST"])
def save(job_id):
    ws = get_or_create(job_id)
    data = request.get_json(silent=True) or {}
    db.update("worksheets", ws["id"],
              contract_value=theme.est_num(data.get("contract_value")),
              notes=data.get("notes", ""))
    db.execute("DELETE FROM worksheet_lines WHERE worksheet_id=?", (ws["id"],))
    for i, ln in enumerate(data.get("lines", [])):
        if not (ln.get("description") or "").strip():
            continue
        db.insert("worksheet_lines", {
            "worksheet_id": ws["id"], "sort": i,
            "category": ln.get("category", "Material"),
            "description": ln.get("description", ""),
            "budget_cost": float(ln.get("budget_cost") or 0),
            "actual_cost": float(ln.get("actual_cost") or 0),
            "qty": float(ln.get("qty") or 0), "unit": (ln.get("unit") or "")[:8],
            "unit_cost": float(ln.get("unit_cost") or 0)})
    # Mirror the contract value back onto the job for the boards.
    db.update("jobs", job_id, contract_value=theme.money(theme.est_num(data.get("contract_value"))))
    db.add_activity("job", job_id, "automation", "Worksheet updated — profit %s" % (
        theme.money(profit_analysis(job_id)["gross_profit"])))
    return jsonify({"ok": True, "profit": profit_analysis(job_id)})
