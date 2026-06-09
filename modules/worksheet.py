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


def _signed_estimate(job_id):
    """The job's signed estimate, else the most recent one."""
    rows = db.all_rows("estimates", "job_id=?", (job_id,), "id DESC")
    signed = [e for e in rows if e.get("status") == "signed"]
    return (signed or rows or [None])[0]


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
        if s.get("optional"):
            continue                              # Upgrades & Options menu — priced
                                                  # for the customer, not job budget
        for ln in s["_lines"]:
            cost = est.line_cost(ln)              # extended: qty × (1+waste) × unit cost
            if not cost:
                continue                          # line not turned on (qty 0) — skip
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

@bp.route("/<int:job_id>")
def view(job_id):
    job = db.get("jobs", job_id)
    if not job:
        return redirect(url_for("jobs.board"))
    ws = get_or_create(job_id)
    return render_template("worksheet.html", job=job, ws=ws, lines=lines_for(ws["id"]),
                           categories=CATEGORIES, profit=profit_analysis(job_id),
                           draws=constants.DRAW_SCHEDULE)


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
