# -*- coding: utf-8 -*-
"""Estimates — AccuLynx-style: Estimate → Sections (narrative scope) → cost lines,
with the Cost / Price / Profit-Margin model. Price = Cost / (1 - margin)."""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify

import db
import theme
import constants

bp = Blueprint("estimates", __name__, url_prefix="/estimates")


# ---- money math (margin model, mirrors AccuLynx) --------------------------

def line_cost(l):
    return (l.get("qty") or 0) * (1 + (l.get("waste_pct") or 0) / 100.0) * (l.get("cost") or 0)


def _margin_price(cost, margin_pct):
    m = (margin_pct or 0) / 100.0
    if m >= 0.99:
        m = 0.99
    return cost / (1 - m) if (1 - m) else cost


def line_price(l, margin_pct):
    """Use the stored per-line price if set (manual override); else derive from margin."""
    if l.get("price"):
        return l["price"]
    return _margin_price(line_cost(l), margin_pct)


def estimate_totals(est, sections):
    cost = sum(s["_cost"] for s in sections)
    subtotal = sum(s["_price"] for s in sections)
    tax = subtotal * (est.get("tax_pct") or 0) / 100.0
    total = subtotal + tax
    net = total - cost
    margin = (net / total * 100.0) if total else 0
    return {"cost": cost, "subtotal": subtotal, "tax": tax, "total": total,
            "net": net, "margin": margin}


def _load_sections(est_id):
    sections = db.all_rows("estimate_sections", "estimate_id=?", (est_id,), "sort, id")
    for s in sections:
        lines = db.all_rows("estimate_lines", "section_id=?", (s["id"],), "sort, id")
        for l in lines:
            l["_cost"] = line_cost(l)
            l["_price"] = line_price(l, s.get("margin_pct"))
        s["_lines"] = lines
        s["_cost"] = sum(l["_cost"] for l in lines)
        s["_price"] = sum(l["_price"] for l in lines)
    return sections


def _draws(total):
    return [{"label": p["label"], "amount": (total * p["pct"] if p["pct"] else None)}
            for p in constants.DRAW_SCHEDULE]


def _next_number():
    rows = db.all_rows("estimates", order="id DESC")
    return "EST-%04d" % ((rows[0]["id"] + 1) if rows else 1)


# ---- routes ---------------------------------------------------------------

@bp.route("/")
def index():
    rows = db.all_rows("estimates", order="id DESC")
    for e in rows:
        e["_total"] = estimate_totals(e, _load_sections(e["id"]))["total"]
    return render_template("estimates.html", estimates=rows)


def _resolve_template(template_id, work_type):
    """Return (name, work_type, scope_text, lines[{description,unit,qty,cost}]) from the
    DB templates table, or fall back to the code defaults."""
    row = None
    if template_id:
        row = db.get("templates", template_id)
    if not row:
        # best-fit by work type
        wt = (work_type or "").strip()
        matches = db.all_rows("templates", "work_type=?", (wt,)) if wt else []
        row = matches[0] if matches else None
    if row:
        return (row["name"], row["work_type"], row.get("scope_text", ""),
                db.load_json(row.get("lines"), []))
    key = constants.template_for_work_type(work_type)
    tpl = constants.ESTIMATE_TEMPLATES.get(key, constants.ESTIMATE_TEMPLATES["blank"])
    lines = [{"description": l["desc"], "unit": l["unit"], "qty": l["qty"], "cost": l["price"]}
             for l in tpl["lines"]]
    return (tpl["name"], work_type, constants.scope_for_template(key), lines)


@bp.route("/new", methods=["GET", "POST"])
def new():
    if request.method == "POST":
        work_type = request.form.get("work_type", "")
        template_id = request.form.get("template_id") or None
        name, wt, scope, lines = _resolve_template(template_id, work_type)
        est = {
            "number": _next_number(),
            "title": request.form.get("title") or name,
            "job_id": request.form.get("job_id") or None,
            "lead_id": request.form.get("lead_id") or None,
            "contact_id": request.form.get("contact_id") or None,
            "work_type": work_type, "template_key": template_id or "", "status": "draft",
            "margin_pct": 30, "tax_pct": 0,
            "terms": db.get_company().get("terms", ""),
        }
        eid = db.insert("estimates", est)
        sid = db.insert("estimate_sections", {
            "estimate_id": eid, "sort": 0,
            "name": name, "scope_text": scope, "margin_pct": 30})
        for i, line in enumerate(lines):
            db.insert("estimate_lines", {
                "estimate_id": eid, "section_id": sid, "sort": i,
                "description": line.get("description", ""), "unit": line.get("unit", "EA"),
                "qty": line.get("qty", 0), "waste_pct": 0, "cost": line.get("cost", 0)})
        flash("Estimate created from %s." % name, "ok")
        return redirect(url_for("estimates.detail", est_id=eid))
    pre = {}
    if request.args.get("lead_id"):
        l = db.get("leads", request.args["lead_id"])
        if l:
            pre = {"lead_id": l["id"], "contact_id": l.get("contact_id"),
                   "work_type": l.get("work_type"), "title": l.get("name")}
    elif request.args.get("job_id"):
        j = db.get("jobs", request.args["job_id"])
        if j:
            pre = {"job_id": j["id"], "contact_id": j.get("contact_id"),
                   "work_type": j.get("work_type"), "title": j.get("name")}
    return render_template("estimate_new.html", pre=pre,
                           templates=db.all_rows("templates", order="name"))


@bp.route("/<int:est_id>")
def detail(est_id):
    e = db.get("estimates", est_id)
    if not e:
        return redirect(url_for("estimates.index"))
    sections = _load_sections(est_id)
    totals = estimate_totals(e, sections)
    from modules import measurements as meas
    measurement = None
    if e.get("job_id"):
        measurement = meas.for_job(e["job_id"])
    if not measurement and e.get("lead_id"):
        measurement = meas.for_lead(e["lead_id"])
    return render_template("estimate_detail.html", e=e, sections=sections, totals=totals,
                           draws=_draws(totals["total"]), measurement=measurement,
                           scope_templates=constants.SCOPE_TEMPLATES,
                           job=db.get("jobs", e["job_id"]) if e.get("job_id") else None,
                           lead=db.get("leads", e["lead_id"]) if e.get("lead_id") else None)


@bp.route("/<int:est_id>/save", methods=["POST"])
def save(est_id):
    data = request.get_json(silent=True) or {}
    db.update("estimates", est_id,
              title=data.get("title", ""),
              tax_pct=float(data.get("tax_pct") or 0),
              notes=data.get("notes", ""),
              terms=data.get("terms", ""))
    db.execute("DELETE FROM estimate_sections WHERE estimate_id=?", (est_id,))
    db.execute("DELETE FROM estimate_lines WHERE estimate_id=?", (est_id,))
    for si, sec in enumerate(data.get("sections", [])):
        sid = db.insert("estimate_sections", {
            "estimate_id": est_id, "sort": si, "name": sec.get("name", ""),
            "scope_text": sec.get("scope_text", ""),
            "margin_pct": float(sec.get("margin_pct") or 0)})
        for li, ln in enumerate(sec.get("lines", [])):
            if not (ln.get("description") or "").strip():
                continue
            db.insert("estimate_lines", {
                "estimate_id": est_id, "section_id": sid, "sort": li,
                "description": ln.get("description", ""), "unit": ln.get("unit", "EA"),
                "qty": float(ln.get("qty") or 0), "waste_pct": float(ln.get("waste_pct") or 0),
                "cost": float(ln.get("cost") or 0), "price": float(ln.get("price") or 0)})
    return jsonify({"ok": True})


@bp.route("/<int:est_id>/status", methods=["POST"])
def status(est_id):
    st = request.form.get("status")
    if st in ("draft", "sent", "signed", "declined"):
        db.update("estimates", est_id, status=st)
        flash("Marked %s." % st, "ok")
    return redirect(url_for("estimates.detail", est_id=est_id))


@bp.route("/<int:est_id>/sign", methods=["POST"])
def sign(est_id):
    db.update("estimates", est_id, status="signed",
              signed_name=request.form.get("signed_name", ""),
              signed_at=db.now(), signature=request.form.get("signature", ""))
    e = db.get("estimates", est_id)
    for et, eid in (("job", e.get("job_id")), ("lead", e.get("lead_id"))):
        if eid:
            db.add_activity(et, eid, "automation", "Estimate %s signed by %s" % (e["number"], e.get("signed_name")))
    return jsonify({"ok": True})


@bp.route("/<int:est_id>/print")
def print_view(est_id):
    e = db.get("estimates", est_id)
    if not e:
        return redirect(url_for("estimates.index"))
    sections = _load_sections(est_id)
    totals = estimate_totals(e, sections)
    return render_template("estimate_print.html", e=e, sections=sections, totals=totals,
                           draws=_draws(totals["total"]))


@bp.route("/<int:est_id>/delete", methods=["POST"])
def delete(est_id):
    db.delete("estimates", est_id)
    db.execute("DELETE FROM estimate_sections WHERE estimate_id=?", (est_id,))
    db.execute("DELETE FROM estimate_lines WHERE estimate_id=?", (est_id,))
    flash("Estimate deleted.", "ok")
    return redirect(url_for("estimates.index"))
