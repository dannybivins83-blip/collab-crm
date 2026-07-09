# -*- coding: utf-8 -*-
"""Invoicing & payments — invoices against the draw schedule, balance tracking."""
from flask import Blueprint, render_template, request, redirect, url_for, flash

import db
import theme
import constants

bp = Blueprint("invoices", __name__, url_prefix="/invoices")

db._ensure_column("invoices", "reminded_at", "TEXT")
db._ensure_column("invoices", "overdue_fired_at", "TEXT")
# Partial-payment support: track cumulative amount applied to each invoice, and tie
# manually-recorded payments back to the invoice (the payments table is otherwise only
# populated by the AccuLynx sync — see acculynx_sync.py).
db._ensure_column("invoices", "amount_paid", "REAL")
db._ensure_column("payments", "invoice_id", "INTEGER")


def _est_contract_total(job_id):
    """Best contract total for a job's draw math: the signed estimate's computed total,
    else the job's stored contract_value. Returns a float (0.0 if nothing found)."""
    try:
        from modules import worksheet as ws
        from modules import estimates as est
        e = ws._signed_estimate(job_id)
        if e:
            secs = est._load_sections(e["id"])
            t = est.estimate_totals(e, secs).get("total") or 0
            if t:
                return float(t)
    except Exception:
        pass
    job = db.get("jobs", job_id) or {}
    return float(theme.est_num(job.get("contract_value")) or 0)


def _next_number():
    # Derive from the highest existing INV- number (not the row id) so deleting
    # an invoice can't make the next one reuse a number that still exists.
    # Serialized write txn prevents duplicate numbers under concurrent inserts (dual-engine).
    conn = db.begin_immediate(lock_table="invoices")
    try:
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
        # Use pre-loaded _job if callers already attached it; avoids N+1 DB lookup.
        job = inv.get("_job") or (db.get("jobs", inv["job_id"]) if inv.get("job_id") else None)
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
    dept = theme.current_department()
    _dept_jobs = db.all_rows("jobs", "department=?", (dept,))
    dept_job_ids = {j["id"] for j in _dept_jobs}
    job_map = {j["id"]: j for j in _dept_jobs}
    # Scope invoices to this dept at query time — avoids loading all paid/cross-dept rows.
    if dept_job_ids:
        _id_ph = ",".join("?" * len(dept_job_ids))
        rows = db.all_rows("invoices", "job_id IS NULL OR job_id IN (%s)" % _id_ph,
                           tuple(dept_job_ids), "id DESC")
    else:
        rows = db.all_rows("invoices", "job_id IS NULL", order="id DESC")
    for inv in rows:
        inv["_job"] = job_map.get(inv["job_id"])
        inv["_overdue"] = _is_overdue(inv)
        # Normalize the money field to a float so a legacy/imported string amount
        # can't 500 the per-row money() render or the totals below.
        inv["amount"] = theme.est_num(inv.get("amount"))
    sweep_overdue_automations(rows)
    # Totals are always across the full scoped set (before query filtering).
    total = sum(i["amount"] for i in rows)
    # Count partial payments toward collected, not just fully-paid invoices.
    paid = sum(theme.est_num(i.get("amount_paid")) if i.get("amount_paid")
               else (i["amount"] if i.get("status") == "paid" else 0)
               for i in rows)
    overdue = sum(i["amount"] for i in rows if i["_overdue"])
    overdue_n = sum(1 for i in rows if i["_overdue"])
    # Client-side search + status filter (after totals are computed).
    q = request.args.get("q", "").strip().lower()
    status_f = request.args.get("status", "").strip()
    if q:
        rows = [i for i in rows if
                q in (i.get("number") or "").lower() or
                q in ((i["_job"] or {}).get("name") or "").lower() or
                q in (i.get("notes") or "").lower()]
    if status_f == "overdue":
        rows = [i for i in rows if i["_overdue"]]
    elif status_f:
        rows = [i for i in rows if i.get("status") == status_f]
    return render_template("invoices.html", invoices=rows, total=total, paid=paid,
                           due=total - paid, overdue=overdue, overdue_n=overdue_n,
                           q=q, status_f=status_f)


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
    dept = theme.current_department()
    # Price each draw from the job's signed-estimate total so the amount auto-fills
    # instead of being hand-keyed (the DRAW_SCHEDULE percentages were previously unused).
    _total = _est_contract_total(int(job_id)) if str(job_id).isdigit() else 0
    priced_draws = [dict(d, amount=(round(_total * d["pct"], 2) if _total else None))
                    for d in constants.DRAW_SCHEDULE]
    return render_template("invoice_form.html", job=job,
                           jobs=db.all_rows("jobs", "department=?", (dept,), "name"),
                           draws=priced_draws, contract_total=_total)


@bp.route("/<int:inv_id>")
def detail(inv_id):
    inv = db.get("invoices", inv_id)
    if not inv:
        return redirect(url_for("invoices.index"))
    from modules import quickbooks as qb
    job = db.get("jobs", inv["job_id"]) if inv.get("job_id") else None
    pays = db.all_rows("payments", "invoice_id=?", (inv_id,), "id DESC")
    # Normalize money fields to floats up front — a legacy/imported/hand-edited row
    # can carry a money STRING ('$5,000', 'N/A') in the REAL column, which 500s both
    # the balance math AND the template's money() filter. est_num is the project's
    # money parser (already used this way in portal.home).
    inv["amount"] = theme.est_num(inv.get("amount"))
    for _p in pays:
        _p["amount"] = theme.est_num(_p.get("amount"))
    paid_sum = sum(_p["amount"] for _p in pays)
    if not paid_sum and inv.get("status") == "paid":
        paid_sum = inv["amount"]
    balance = max(inv["amount"] - paid_sum, 0)
    return render_template("invoice_detail.html", inv=inv, job=job,
                           qbo_connected=qb.is_connected(), overdue=_is_overdue(inv),
                           payments=pays, paid_sum=paid_sum, balance=balance,
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


@bp.route("/generate-draws/<int:job_id>", methods=["POST"])
def generate_draws(job_id):
    """One-click: create an invoice for each draw in the schedule not yet billed,
    priced from the job's signed-estimate total × each draw %. Idempotent by draw label."""
    job = db.get("jobs", job_id)
    if not job:
        flash("Job not found.", "error")
        return redirect(url_for("invoices.index"))
    total = _est_contract_total(job_id)
    if total <= 0:
        flash("No signed-estimate total or contract value on this job yet — set one first.", "error")
        return redirect(url_for("jobs.detail", job_id=job_id))
    existing = {(i.get("draw_key") or "") for i in db.all_rows("invoices", "job_id=?", (job_id,))}
    made = 0
    for d in constants.DRAW_SCHEDULE:
        if d["label"] in existing:
            continue
        db.insert("invoices", {"job_id": job_id, "number": _next_number(),
                               "draw_key": d["label"], "amount": round(total * d["pct"], 2),
                               "status": "unpaid", "notes": ""})
        made += 1
    if made:
        db.add_activity("job", job_id, "automation",
                        "%d draw invoice(s) generated from estimate total %s" % (made, theme.money(total)))
        flash("Generated %d draw invoice(s) from %s." % (made, theme.money(total)), "ok")
    else:
        flash("All draws are already invoiced for this job.", "info")
    return redirect(url_for("jobs.detail", job_id=job_id))


@bp.route("/<int:inv_id>/record-payment", methods=["POST"])
def record_payment(inv_id):
    """Record a (possibly partial) payment: writes a real payments row and marks the
    invoice paid only once cumulative payments cover the amount, else 'partial'."""
    inv = db.get("invoices", inv_id)
    if not inv:
        return redirect(url_for("invoices.index"))
    amt = theme.est_num(request.form.get("amount"))
    if not amt or amt <= 0:
        flash("Enter a payment amount.", "error")
        return redirect(url_for("invoices.detail", inv_id=inv_id))
    db.insert("payments", {
        "job_id": inv.get("job_id"), "invoice_id": inv_id, "amount": round(amt, 2),
        "method": request.form.get("method", ""), "reference": request.form.get("reference", ""),
        "paid_date": request.form.get("paid_date") or db.today(),
        "notes": request.form.get("notes", ""), "source": "manual", "created": db.now()})
    paid = sum(theme.est_num(p.get("amount")) for p in db.all_rows("payments", "invoice_id=?", (inv_id,)))
    inv_amt = theme.est_num(inv.get("amount"))
    fields = {"amount_paid": round(paid, 2)}
    if inv_amt > 0 and paid + 0.005 >= inv_amt:
        fields["status"] = "paid"
        fields["paid_date"] = request.form.get("paid_date") or db.today()
    elif inv.get("status") != "paid":
        fields["status"] = "partial"
    db.update("invoices", inv_id, **fields)
    if inv.get("job_id"):
        db.add_activity("job", inv["job_id"], "automation",
                        "Payment %s recorded on %s (balance %s)" % (
                            theme.money(amt), inv.get("number"), theme.money(max(inv_amt - paid, 0))))
    flash("Payment of %s recorded.%s" % (
        theme.money(amt),
        "" if fields.get("status") != "paid" else " Invoice fully paid."), "ok")
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
    dept = theme.current_department()
    _dept_jobs = db.all_rows("jobs", "department=?", (dept,))
    dept_job_ids = {j["id"] for j in _dept_jobs}
    jobs = {j["id"]: j for j in _dept_jobs}
    if dept_job_ids:
        _id_ph = ",".join("?" * len(dept_job_ids))
        _inv_rows = db.all_rows("invoices", "job_id IS NULL OR job_id IN (%s)" % _id_ph, tuple(dept_job_ids))
    else:
        _inv_rows = db.all_rows("invoices", "job_id IS NULL")
    drafted = skipped = already_sent = 0
    for inv in _inv_rows:
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
