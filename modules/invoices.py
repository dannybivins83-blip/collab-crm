# -*- coding: utf-8 -*-
"""Invoicing & payments — invoices against the draw schedule, balance tracking."""
from flask import Blueprint, render_template, request, redirect, url_for, flash

import db
import theme
import constants

bp = Blueprint("invoices", __name__, url_prefix="/invoices")

# Track when a homeowner payment reminder was last drafted (module-load convention).
try:
    db.execute("ALTER TABLE invoices ADD COLUMN reminded_at TEXT")
except Exception:
    pass
# First time an invoice is observed overdue we let the workflow engine fire once.
try:
    db.execute("ALTER TABLE invoices ADD COLUMN overdue_fired_at TEXT")
except Exception:
    pass
db._COLCACHE.clear()


def _next_number():
    # Derive from the highest existing INV- number (not the row id) so deleting
    # an invoice can't make the next one reuse a number that still exists.
    # Wrapped in BEGIN IMMEDIATE to prevent duplicate numbers under concurrent inserts.
    import sqlite3
    conn = sqlite3.connect(db.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        mx = 0
        for r in conn.execute("SELECT number FROM invoices").fetchall():
            n = (r["number"] or "")
            if n.startswith("INV-"):
                try:
                    mx = max(mx, int(n[4:]))
                except Exception:
                    pass
        nxt = "INV-%04d" % (mx + 1)
        conn.commit()
        return nxt
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _is_overdue(inv):
    """Unpaid (or sent-but-unpaid) and past its due date."""
    if (inv.get("status") or "") == "paid":
        return False
    dd = inv.get("due_date") or ""
    return bool(dd) and dd < db.today()


def sweep_overdue_automations(rows):
    """First time an invoice is seen overdue, let the workflow engine fire its
    'invoice_overdue' rule (a draft nudge — never auto-sends). Idempotent per invoice
    via overdue_fired_at, so a dashboard/list refresh won't re-fire it."""
    from modules import automations
    for inv in rows:
        if inv.get("overdue_fired_at") or not _is_overdue(inv):
            continue
        job = db.get("jobs", inv["job_id"]) if inv.get("job_id") else None
        try:
            automations.fire_invoice_overdue(inv, job)
        except Exception:
            pass
        db.update("invoices", inv["id"], overdue_fired_at=db.now())


def _reminder_text(inv, job):
    """Build a friendly homeowner payment-reminder (subject, body) with a pay link."""
    company = db.get_company()
    name = (job or {}).get("name") or ""
    first = name.split(" ")[0] if name else "there"
    from modules import portal as portalmod
    pay_url = (inv.get("payment_link") or (job or {}).get("pay_url")
               or (portalmod.portal_link(job["id"]) if job else ""))
    amt = theme.money(inv.get("amount"))
    due = (" (due %s)" % inv["due_date"]) if inv.get("due_date") else ""
    cname = company.get("name") or "our team"
    lines = ["Hi %s," % first, "",
             "This is a friendly reminder from %s that invoice %s for %s%s is currently "
             "outstanding." % (cname, inv.get("number", ""), amt, due), ""]
    if pay_url:
        lines += ["You can pay securely online here:", pay_url, ""]
    else:
        lines += ["Please reply and we'll send you a secure payment link.", ""]
    lines += ["If you've already sent payment, thank you — please disregard this note.", ""]
    if company.get("phone"):
        lines += ["Questions? Call us at %s." % company["phone"], ""]
    lines += ["Thank you,", cname]
    if company.get("license"):
        lines.append("Lic. %s" % company["license"])
    subject = "Payment reminder — invoice %s (%s)" % (inv.get("number", ""), amt)
    return subject, "\n".join(lines)


def _draft_reminder(inv, job, email):
    """Draft the reminder in the current user's Gmail. Returns the draft id or None."""
    from flask import session
    from modules import gmail
    subject, body = _reminder_text(inv, job)
    did = gmail.create_draft(session.get("user_id"), email, subject, body)
    if did:
        db.update("invoices", inv["id"], reminded_at=db.now(), customer_email=email)
        if inv.get("job_id"):
            db.add_activity("job", inv["job_id"], "email",
                            "Payment reminder for %s drafted to %s (review & send in Gmail)"
                            % (inv.get("number", ""), email))
    return did


def _receipt_text(inv, job):
    """Build a payment-received thank-you (subject, body)."""
    company = db.get_company()
    name, _ = _customer(inv, job)
    first = name.split(" ")[0] if name else "there"
    amt = theme.money(inv.get("amount"))
    cname = company.get("name") or "our team"
    lines = ["Hi %s," % first, "",
             "Thank you! We've received your payment of %s for invoice %s."
             % (amt, inv.get("number", "")), "",
             "It was a pleasure working with you. If you have any questions or need "
             "anything in the future, don't hesitate to reach out.", ""]
    if company.get("phone"):
        lines += ["Phone: %s" % company["phone"], ""]
    lines += ["Thanks again,", cname]
    if company.get("license"):
        lines.append("Lic. %s" % company["license"])
    subject = "Payment received — invoice %s (%s)" % (inv.get("number", ""), amt)
    return subject, "\n".join(lines)


def _draft_receipt(inv, job, email):
    """Draft a payment-received thank-you in the current user's Gmail. Returns draft id or None."""
    from flask import session
    from modules import gmail
    subject, body = _receipt_text(inv, job)
    did = gmail.create_draft(session.get("user_id"), email, subject, body)
    if did:
        if inv.get("job_id"):
            db.add_activity("job", inv["job_id"], "email",
                            "Payment receipt for %s drafted to %s (review & send in Gmail)"
                            % (inv.get("number", ""), email))
    return did


@bp.route("/")
def index():
    rows = db.all_rows("invoices", order="id DESC")
    jobs = {j["id"]: j for j in db.all_rows("jobs")}
    for inv in rows:
        inv["_job"] = jobs.get(inv["job_id"])
        inv["_overdue"] = _is_overdue(inv)
    sweep_overdue_automations(rows)
    total = sum(i["amount"] or 0 for i in rows)
    paid = sum(i["amount"] or 0 for i in rows if i["status"] == "paid")
    overdue = sum(i["amount"] or 0 for i in rows if i["_overdue"])
    overdue_n = sum(1 for i in rows if i["_overdue"])
    return render_template("invoices.html", invoices=rows, total=total, paid=paid,
                           due=total - paid, overdue=overdue, overdue_n=overdue_n)


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
                           qbo_connected=qb.is_connected(), overdue=_is_overdue(inv),
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
    _inv = db.get("invoices", inv_id)
    if not _inv:
        return redirect(url_for("invoices.index"))
    if _inv.get("status") == "paid":
        flash("Invoice %s is already marked paid." % _inv.get("number", ""), "info")
        return redirect(url_for("invoices.detail", inv_id=inv_id))
    db.update("invoices", inv_id, status="paid", paid_date=db.today())
    inv = db.get("invoices", inv_id)
    job = db.get("jobs", inv["job_id"]) if inv.get("job_id") else None
    if inv.get("job_id"):
        db.add_activity("job", inv["job_id"], "automation", "Invoice %s marked paid" % inv["number"])
    _, email = _customer(inv, job)
    if email and _draft_receipt(inv, job, email):
        flash("Marked paid — receipt drafted in your Gmail to %s (review & send)." % email, "ok")
    else:
        flash("Marked paid.", "ok")
    return redirect(url_for("invoices.detail", inv_id=inv_id))


@bp.route("/<int:inv_id>/remind", methods=["POST"])
def remind(inv_id):
    """1-click: draft a friendly payment reminder (with pay link) to the homeowner.
    Drafts in the rep's Gmail — never auto-sends (house rule)."""
    inv = db.get("invoices", inv_id)
    if not inv:
        return redirect(url_for("invoices.index"))
    job = db.get("jobs", inv["job_id"]) if inv.get("job_id") else None
    _, email = _customer(inv, job)
    email = (request.form.get("email") or email or "").strip()
    if not email:
        flash("Add the customer's email first, then send the reminder.", "error")
        return redirect(url_for("invoices.detail", inv_id=inv_id))
    if _draft_reminder(inv, job, email):
        flash("Reminder drafted in your Gmail to %s — review and hit send." % email, "ok")
    else:
        flash("Connect your Gmail (📨 in the top bar) to draft reminders — or send %s the "
              "pay link manually." % email, "info")
    # Return to wherever the click came from (dashboard list or the invoice page).
    return redirect(request.referrer or url_for("invoices.detail", inv_id=inv_id))


@bp.route("/remind-overdue", methods=["POST"])
def remind_overdue():
    """1-click: draft reminders for every overdue invoice at once."""
    from flask import session
    from modules import gmail
    if not gmail.account_for_user(session.get("user_id")):
        flash("Connect your Gmail (📨 in the top bar) to draft reminders.", "info")
        return redirect(url_for("invoices.index"))
    _REMIND_COOLDOWN_DAYS = 5
    from datetime import date as _date, timedelta as _td
    _cutoff = (_date.today() - _td(days=_REMIND_COOLDOWN_DAYS)).isoformat()
    jobs = {j["id"]: j for j in db.all_rows("jobs")}
    drafted = skipped = already_sent = 0
    for inv in db.all_rows("invoices"):
        if not _is_overdue(inv):
            continue
        # Skip invoices reminded within the cooldown window to prevent duplicate floods.
        reminded = (inv.get("reminded_at") or "")[:10]
        if reminded >= _cutoff:
            already_sent += 1
            continue
        job = jobs.get(inv.get("job_id"))
        _, email = _customer(inv, job)
        if not email:
            skipped += 1
            continue
        if _draft_reminder(inv, job, email):
            drafted += 1
    msg = "Drafted %d payment reminder%s in your Gmail." % (drafted, "" if drafted == 1 else "s")
    if already_sent:
        msg += " %d skipped (reminded within last %d days)." % (already_sent, _REMIND_COOLDOWN_DAYS)
    if skipped:
        msg += " %d skipped (no customer email on file)." % skipped
    flash(msg, "ok" if drafted else "info")
    return redirect(url_for("invoices.index"))


@bp.route("/<int:inv_id>/delete", methods=["POST"])
def delete(inv_id):
    db.delete("invoices", inv_id)
    flash("Invoice deleted.", "ok")
    return redirect(url_for("invoices.index"))
