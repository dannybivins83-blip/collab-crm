# -*- coding: utf-8 -*-
"""Orders + Order Manager (AccuLynx parity).

Material + Labor purchase orders generated from a job's estimate, queued across
jobs in the Order Manager. Vendors are admin-managed in Settings.
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, abort

import db
import theme

bp = Blueprint("orders", __name__, url_prefix="/orders")


def _require_order(order_id):
    """Fetch order and verify caller's department owns it. Aborts 404/403 as needed."""
    o = db.get("orders", order_id)
    if not o:
        abort(404)
    from modules.auth import current_user as _cu
    from theme import current_department
    u = _cu() or {}
    if u.get("role") != "admin" and o.get("department") != current_department():
        abort(403)
    return o

TYPES = ["Material", "Labor"]
STATUSES = ["draft", "ordered", "delivered", "received"]


@bp.app_context_processor
def _inject():
    return {"job_orders": lambda job_id: db.all_rows("orders", "job_id=?", (job_id,), "id DESC")}


def lines_for(order_id):
    return db.all_rows("order_lines", "order_id=?", (order_id,), "sort, id")


def order_total(order_id):
    return sum((l.get("qty") or 0) * (l.get("cost") or 0) for l in lines_for(order_id))


def _next_po(otype):
    conn = db.connect()
    try:
        row = conn.execute("SELECT MAX(id) AS mx FROM orders").fetchone()
        n = ((row["mx"] or 0) + 1) if row else 1
    finally:
        conn.close()
    return "PO-%s-%04d" % (otype[0].upper(), n)


# ---- Order Manager (cross-job queue) --------------------------------------

@bp.route("/")
def index():
    dept = theme.current_department()
    all_orders = db.all_rows("orders", "department=?", (dept,), "id DESC")
    # Aggregates from the full unfiltered set (so counts + vendor list are always complete).
    counts = {s: sum(1 for o in all_orders if o["status"] == s) for s in STATUSES}
    vendors = sorted({o.get("vendor") for o in all_orders if o.get("vendor")})
    total = len(all_orders)
    status_f = request.args.get("status")
    type_f = request.args.get("type")
    vendor_f = request.args.get("vendor")
    q = (request.args.get("q") or "").strip().lower()
    rows = all_orders
    if status_f:
        rows = [o for o in rows if o["status"] == status_f]
    if type_f:
        rows = [o for o in rows if o["type"] == type_f]
    if vendor_f:
        rows = [o for o in rows if (o.get("vendor") or "") == vendor_f]
    jobs = {j["id"]: j for j in db.all_rows("jobs", "department=?", (dept,))}
    # Batch-load order lines to avoid N+1 (was one query per row via order_total).
    if rows:
        _ids = tuple(o["id"] for o in rows)
        _ph = ",".join("?" * len(_ids))
        _lines_by_order = {}
        for _ln in db.all_rows("order_lines", "order_id IN (%s)" % _ph, _ids):
            _lines_by_order.setdefault(_ln["order_id"], []).append(_ln)
    else:
        _lines_by_order = {}
    for o in rows:
        o["_job"] = jobs.get(o["job_id"])
        _lns = _lines_by_order.get(o["id"], [])
        o["_total"] = sum((l.get("qty") or 0) * (l.get("cost") or 0) for l in _lns)
    if q:
        rows = [o for o in rows if q in ((o.get("po_number") or "") + " " +
                                         ((o["_job"] or {}).get("name") or "") + " " +
                                         (o.get("vendor") or "") + " " +
                                         (o.get("notes") or "")).lower()]
    return render_template("orders_index.html", orders=rows, counts=counts, statuses=STATUSES,
                           types=TYPES, vendors=vendors, status_f=status_f, type_f=type_f,
                           vendor_f=vendor_f, total=total, q=q)


# ---- generate from estimate -----------------------------------------------

@bp.route("/generate/<int:job_id>", methods=["POST"])
def generate(job_id):
    """Create a Material PO (material lines) + Labor PO (labor lines) from the
    job's estimate. Categorizes each estimate line by description."""
    from modules import estimates as est
    from modules import worksheet as ws
    from modules.auth import current_user as _cu
    from theme import current_department
    job = db.get("jobs", job_id)
    if not job:
        return redirect(url_for("jobs.board"))
    u = _cu() or {}
    if u.get("role") != "admin" and job.get("department") != current_department():
        abort(403)
    rows = db.all_rows("estimates", "job_id=?", (job_id,), "id DESC")
    e = next((x for x in rows if x.get("status") == "signed"), rows[0] if rows else None)
    if not e:
        flash("No estimate on this job to generate orders from.", "error")
        return redirect(url_for("jobs.detail", job_id=job_id))
    sections = est._load_sections(e["id"])
    buckets = {"Material": [], "Labor": []}
    for s in sections:
        for ln in s["_lines"]:
            cat = ws._category_for(ln.get("description"), ln.get("unit"))
            kind = "Labor" if cat == "Labor" else "Material"  # Permit/Overhead/Other -> Material PO
            buckets[kind].append({"description": ln.get("description", ""), "unit": ln.get("unit", "EA"),
                                  "qty": ln.get("qty", 0), "cost": ln.get("cost", 0)})
    made = []
    for otype, lines in buckets.items():
        if not lines:
            continue
        oid = db.insert("orders", {"job_id": job_id, "type": otype, "po_number": _next_po(otype),
                                   "status": "draft", "department": job.get("department"),
                                   "notes": "Generated from %s" % e.get("number", "estimate")})
        for i, ln in enumerate(lines):
            db.insert("order_lines", {"order_id": oid, "sort": i, **ln})
        made.append(otype)
        db.add_activity("job", job_id, "automation", "%s order generated from %s" % (otype, e.get("number")))
    if made:
        flash("Generated %s order(s) from %s." % (" + ".join(made), e.get("number")), "ok")
    else:
        flash("Nothing to order from this estimate.", "error")
    return redirect(url_for("jobs.detail", job_id=job_id))


# ---- order detail ----------------------------------------------------------

@bp.route("/<int:order_id>")
def detail(order_id):
    o = _require_order(order_id)
    return render_template("orders_detail.html", o=o, lines=lines_for(order_id),
                           job=db.get("jobs", o["job_id"]) if o.get("job_id") else None,
                           total=order_total(order_id), statuses=STATUSES,
                           vendors=db.all_rows("vendors", order="name"))


@bp.route("/<int:order_id>/save", methods=["POST"])
def save(order_id):
    _require_order(order_id)
    # Defensive against a hand-crafted / malformed JSON body: a non-dict top-level
    # body, a non-list ``lines``, non-dict line elements, or non-numeric qty/cost
    # must never 500 the save (AttributeError / float("abc") ValueError).
    # theme.est_num() coerces any junk -> 0.0 without raising.
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}
    db.update("orders", order_id, vendor=data.get("vendor", ""), po_number=data.get("po_number", ""),
              notes=data.get("notes", ""))
    db.execute("DELETE FROM order_lines WHERE order_id=?", (order_id,))
    _lines = data.get("lines")
    if not isinstance(_lines, list):
        _lines = []
    for i, ln in enumerate(_lines):
        if not isinstance(ln, dict):
            continue
        if not (str(ln.get("description") or "")).strip():
            continue
        db.insert("order_lines", {"order_id": order_id, "sort": i,
                                  "description": ln.get("description", ""), "unit": ln.get("unit", "EA"),
                                  "qty": theme.est_num(ln.get("qty")), "cost": theme.est_num(ln.get("cost"))})
    return jsonify({"ok": True, "total": order_total(order_id)})


@bp.route("/<int:order_id>/status", methods=["POST"])
def status(order_id):
    st = request.form.get("status")
    o = _require_order(order_id)
    if st not in STATUSES:
        return redirect(url_for("orders.detail", order_id=order_id))
    fields = {"status": st}
    if st == "ordered" and not o.get("ordered_date"):
        fields["ordered_date"] = db.today()
    if st in ("delivered", "received") and not o.get("delivery_date"):
        fields["delivery_date"] = db.today()
    db.update("orders", order_id, **fields)
    if o.get("job_id"):
        db.add_activity("job", o["job_id"], "automation",
                        "%s order %s marked %s" % (o["type"], o.get("po_number") or "", st))
    flash("Order marked %s." % st, "ok")
    return redirect(url_for("orders.detail", order_id=order_id))


@bp.route("/<int:order_id>/print")
def print_view(order_id):
    o = _require_order(order_id)
    return render_template("order_print.html", o=o, lines=lines_for(order_id),
                           job=db.get("jobs", o["job_id"]) if o.get("job_id") else None,
                           total=order_total(order_id))


@bp.route("/<int:order_id>/delete", methods=["POST"])
def delete(order_id):
    _require_order(order_id)
    db.delete("orders", order_id)
    db.execute("DELETE FROM order_lines WHERE order_id=?", (order_id,))
    flash("Order deleted.", "ok")
    return redirect(url_for("orders.index"))


# ---- vendors (admin-managed) ----------------------------------------------

@bp.route("/vendors", methods=["GET", "POST"])
def vendors():
    if request.method == "POST":
        db.insert("vendors", {f: request.form.get(f, "").strip()
                              for f in ("name", "type", "phone", "email", "address")})
        flash("Vendor added.", "ok")
        return redirect(url_for("orders.vendors"))
    return render_template("vendors.html", vendors=db.all_rows("vendors", order="name"), types=TYPES)


@bp.route("/vendors/<int:vendor_id>/delete", methods=["POST"])
def vendor_delete(vendor_id):
    db.delete("vendors", vendor_id)
    flash("Vendor removed.", "ok")
    return redirect(url_for("orders.vendors"))
