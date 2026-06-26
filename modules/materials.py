# -*- coding: utf-8 -*-
"""Material orders — per-job supplier order sheets."""
from flask import Blueprint, render_template, request, redirect, url_for, flash

import db

bp = Blueprint("materials", __name__, url_prefix="/materials")
STATUS = ["draft", "ordered", "delivered"]


def _parse_items(raw):
    """Each line: 'qty | unit | item' OR free text -> list of dicts."""
    items = []
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 3:
            items.append({"qty": parts[0], "unit": parts[1], "item": parts[2]})
        else:
            items.append({"qty": "", "unit": "", "item": line})
    return items


def _items_to_text(items):
    out = []
    for it in items:
        if it.get("qty") or it.get("unit"):
            out.append("%s | %s | %s" % (it.get("qty", ""), it.get("unit", ""), it.get("item", "")))
        else:
            out.append(it.get("item", ""))
    return "\n".join(out)


@bp.route("/")
def index():
    import theme as _theme
    dept = _theme.current_department()
    dept_job_ids = {j["id"] for j in db.all_rows("jobs", "department=?", (dept,))}
    all_mats = db.all_rows("materials", order="id DESC")
    rows = [m for m in all_mats if not m.get("job_id") or m["job_id"] in dept_job_ids]
    job_map = {j["id"]: j for j in db.all_rows("jobs", "department=?", (dept,))}
    for m in rows:
        m["_job"] = job_map.get(m["job_id"])
        m["_items"] = db.load_json(m.get("items"), [])
    return render_template("materials.html", orders=rows)


@bp.route("/new", methods=["GET", "POST"])
def new():
    if request.method == "POST":
        items = _parse_items(request.form.get("items_raw", ""))
        data = {"job_id": request.form.get("job_id") or None,
                "supplier": request.form.get("supplier", ""), "status": request.form.get("status", "draft"),
                "items": db.dump_json(items), "notes": request.form.get("notes", ""),
                "ordered_date": request.form.get("ordered_date", ""),
                "delivery_date": request.form.get("delivery_date", "")}
        mid = db.insert("materials", data)
        if data["job_id"]:
            db.add_activity("job", int(data["job_id"]), "automation", "Material order created (%s)" % data["supplier"])
        flash("Material order created.", "ok")
        return redirect(url_for("materials.detail", order_id=mid))
    job_id = request.args.get("job_id", "")
    return render_template("material_form.html", job=db.get("jobs", job_id) if job_id else None,
                           jobs=db.all_rows("jobs", order="name"), status_list=STATUS)


@bp.route("/<int:order_id>")
def detail(order_id):
    m = db.get("materials", order_id)
    if not m:
        return redirect(url_for("materials.index"))
    m["_items"] = db.load_json(m.get("items"), [])
    m["_items_text"] = _items_to_text(m["_items"])
    return render_template("material_detail.html", m=m,
                           job=db.get("jobs", m["job_id"]) if m.get("job_id") else None, status_list=STATUS)


@bp.route("/<int:order_id>/save", methods=["POST"])
def save(order_id):
    items = _parse_items(request.form.get("items_raw", ""))
    db.update("materials", order_id, supplier=request.form.get("supplier", ""),
              status=request.form.get("status", "draft"), items=db.dump_json(items),
              notes=request.form.get("notes", ""),
              ordered_date=request.form.get("ordered_date", ""),
              delivery_date=request.form.get("delivery_date", ""))
    flash("Order saved.", "ok")
    return redirect(url_for("materials.detail", order_id=order_id))


@bp.route("/<int:order_id>/delete", methods=["POST"])
def delete(order_id):
    db.delete("materials", order_id)
    flash("Order deleted.", "ok")
    return redirect(url_for("materials.index"))
