# -*- coding: utf-8 -*-
"""Invoicing & payments — invoices against the draw schedule, balance tracking."""
from flask import Blueprint, render_template, request, redirect, url_for, flash

import db
import theme
import constants

bp = Blueprint("invoices", __name__, url_prefix="/invoices")


def _next_number():
    rows = db.all_rows("invoices", order="id DESC")
    return "INV-%04d" % ((rows[0]["id"] + 1) if rows else 1)


@bp.route("/")
def index():
    rows = db.all_rows("invoices", order="id DESC")
    jobs = {j["id"]: j for j in db.all_rows("jobs")}
    for inv in rows:
        inv["_job"] = jobs.get(inv["job_id"])
    total = sum(i["amount"] or 0 for i in rows)
    paid = sum(i["amount"] or 0 for i in rows if i["status"] == "paid")
    return render_template("invoices.html", invoices=rows, total=total, paid=paid, due=total - paid)


@bp.route("/new", methods=["GET", "POST"])
def new():
    if request.method == "POST":
        job_id = request.form.get("job_id") or None
        amount = theme.est_num(request.form.get("amount"))
        data = {"job_id": job_id, "number": _next_number(),
                "draw_key": request.form.get("draw_key", ""), "amount": amount,
                "status": "unpaid", "due_date": request.form.get("due_date", ""),
                "notes": request.form.get("notes", "")}
        iid = db.insert("invoices", data)
        if job_id:
            db.add_activity("job", int(job_id), "automation", "Invoice %s created (%s)" % (data["number"], theme.money(amount)))
        flash("Invoice created.", "ok")
        return redirect(url_for("invoices.detail", inv_id=iid))
    job_id = request.args.get("job_id", "")
    job = db.get("jobs", job_id) if job_id else None
    return render_template("invoice_form.html", job=job, jobs=db.all_rows("jobs", order="name"),
                           draws=constants.DRAW_SCHEDULE)


@bp.route("/<int:inv_id>")
def detail(inv_id):
    inv = db.get("invoices", inv_id)
    if not inv:
        return redirect(url_for("invoices.index"))
    from modules import quickbooks as qb
    job = db.get("jobs", inv["job_id"]) if inv.get("job_id") else None
    return render_template("invoice_detail.html", inv=inv, job=job,
                           qbo_connected=qb.is_connected(),
                           default_email=inv.get("customer_email") or (job or {}).get("email") or "")


def _customer(inv, job):
    name = (job or {}).get("name") or "Customer"
    email = inv.get("customer_email") or (job or {}).get("email") or ""
    return name, email


@bp.route("/<int:inv_id>/push", methods=["POST"])
def push(inv_id):
    """Create this invoice in QuickBooks Online and pull its Pay-now link."""
    from modules import quickbooks as qb
    inv = db.get("invoices", inv_id)
    if not inv:
        return redirect(url_for("invoices.index"))
    if not qb.is_connected():
        flash("Connect QuickBooks first (Tools → QuickBooks).", "error")
        return redirect(url_for("invoices.detail", inv_id=inv_id))
    job = db.get("jobs", inv["job_id"]) if inv.get("job_id") else None
    name, email = _customer(inv, job)
    desc = "%s — %s" % (inv.get("number"), (inv.get("draw_key") or "Invoice"))
    ok, res = qb.create_invoice(inv, job, name, email, inv.get("amount"), desc)
    if not ok:
        flash("QuickBooks error: %s" % res, "error")
        return redirect(url_for("invoices.detail", inv_id=inv_id))
    link = qb.invoice_link(res) or ""
    db.update("invoices", inv_id, qbo_id=res, payment_link=link, customer_email=email)
    if inv.get("job_id"):
        db.add_activity("job", inv["job_id"], "automation",
                        "Invoice %s pushed to QuickBooks%s" % (inv["number"], " + pay link" if link else ""))
    flash("Pushed to QuickBooks%s." % (" and pulled the Pay-now link" if link else ""), "ok")
    return redirect(url_for("invoices.detail", inv_id=inv_id))


@bp.route("/<int:inv_id>/send", methods=["POST"])
def send(inv_id):
    """Have QuickBooks email the invoice + Pay-now link to the customer (confirmed)."""
    from modules import quickbooks as qb
    inv = db.get("invoices", inv_id)
    if not inv:
        return redirect(url_for("invoices.index"))
    email = request.form.get("email", "").strip() or inv.get("customer_email")
    if not email:
        flash("Add the customer email first.", "error")
        return redirect(url_for("invoices.detail", inv_id=inv_id))
    if not inv.get("qbo_id"):
        flash("Push the invoice to QuickBooks first.", "error")
        return redirect(url_for("invoices.detail", inv_id=inv_id))
    ok, res = qb.send_invoice(inv["qbo_id"], email)
    if not ok:
        flash("QuickBooks send failed: %s" % res, "error")
        return redirect(url_for("invoices.detail", inv_id=inv_id))
    db.update("invoices", inv_id, status=(inv["status"] if inv["status"] == "paid" else "sent"),
              sent_at=db.now(), customer_email=email)
    if inv.get("job_id"):
        db.add_activity("job", inv["job_id"], "email", "Invoice %s + payment link emailed to %s via QuickBooks" % (inv["number"], email))
    flash("Invoice + payment link sent to %s via QuickBooks." % email, "ok")
    return redirect(url_for("invoices.detail", inv_id=inv_id))


@bp.route("/<int:inv_id>/pay", methods=["POST"])
def pay(inv_id):
    db.update("invoices", inv_id, status="paid", paid_date=db.today())
    inv = db.get("invoices", inv_id)
    if inv.get("job_id"):
        db.add_activity("job", inv["job_id"], "automation", "Invoice %s marked paid" % inv["number"])
    flash("Marked paid.", "ok")
    return redirect(url_for("invoices.detail", inv_id=inv_id))


@bp.route("/<int:inv_id>/delete", methods=["POST"])
def delete(inv_id):
    db.delete("invoices", inv_id)
    flash("Invoice deleted.", "ok")
    return redirect(url_for("invoices.index"))
