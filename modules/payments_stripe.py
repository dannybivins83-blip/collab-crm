# -*- coding: utf-8 -*-
"""Native online card payments via Stripe Checkout (ready-for-keys).

Activation (no code change): set on the host's env —
  STRIPE_SECRET_KEY        (sk_live_… for real money, sk_test_… for sandbox)
  STRIPE_PUBLISHABLE_KEY   (pk_…)
  STRIPE_WEBHOOK_SECRET    (whsec_…, from the Stripe dashboard webhook pointing at /stripe/webhook)

Fail-closed: with no STRIPE_SECRET_KEY the 'Pay with card' button never renders and
the routes 400 — so a tenant without Stripe sees nothing broken. No `stripe` library
dependency: we call the REST API with `requests` (already used elsewhere) and verify
the webhook signature with hmac/hashlib.

Flow: a rep clicks 'Stripe pay link' on an invoice → we create a Checkout Session for
the amount and store its URL on invoices.payment_link (which the client portal's
'Pay now' button already uses). When the customer pays, Stripe calls /stripe/webhook;
we verify the signature, mark the invoice paid, and write a payments-ledger row.
"""
import hashlib
import hmac
import os
import time

from flask import (Blueprint, request, redirect, url_for, flash, jsonify, abort)

import db

bp = Blueprint("stripe_pay", __name__, url_prefix="/stripe")

API = "https://api.stripe.com/v1"

db._ensure_column("invoices", "stripe_session_id", "TEXT")


def _sk():
    return (os.environ.get("STRIPE_SECRET_KEY", "") or "").strip()


def configured():
    return bool(_sk())


def pub_key():
    return (os.environ.get("STRIPE_PUBLISHABLE_KEY", "") or "").strip()


def _post(path, data):
    """POST form-encoded to the Stripe API. Returns (ok, json_or_error)."""
    try:
        import requests
        r = requests.post(API + path, data=data, auth=(_sk(), ""), timeout=25)
        body = r.json() if r.content else {}
        if r.status_code >= 400:
            return False, (body.get("error", {}) or {}).get("message", "stripe error %d" % r.status_code)
        return True, body
    except Exception as exc:
        return False, str(exc)


def create_checkout(invoice, success_url, cancel_url):
    """Create a Stripe Checkout Session for an invoice. Returns (ok, url_or_error)."""
    amount = invoice.get("amount") or 0
    cents = int(round(float(amount) * 100))
    if cents <= 0:
        return False, "invoice amount must be > 0"
    company = db.get_company() or {}
    label = "%s — %s" % (company.get("name") or "Invoice", invoice.get("number") or "")
    data = {
        "mode": "payment",
        "success_url": success_url,
        "cancel_url": cancel_url,
        "client_reference_id": str(invoice.get("id")),
        "metadata[invoice_id]": str(invoice.get("id")),
        "line_items[0][quantity]": "1",
        "line_items[0][price_data][currency]": "usd",
        "line_items[0][price_data][unit_amount]": str(cents),
        "line_items[0][price_data][product_data][name]": label[:120],
    }
    email = invoice.get("customer_email")
    if email:
        data["customer_email"] = email
    ok, res = _post("/checkout/sessions", data)
    if not ok:
        return False, res
    return True, (res.get("url"), res.get("id"))


@bp.route("/checkout/<int:invoice_id>", methods=["POST"])
def checkout(invoice_id):
    """Rep action: create a Stripe pay link for an invoice + store it as payment_link."""
    if not configured():
        flash("Stripe isn't configured. Add STRIPE_SECRET_KEY to enable card payments.", "error")
        return redirect(url_for("invoices.detail", inv_id=invoice_id))
    inv = db.get("invoices", invoice_id)
    if not inv:
        return redirect(url_for("invoices.index"))
    base = request.host_url.rstrip("/")
    ok, res = create_checkout(
        inv,
        success_url=base + url_for("stripe_pay.thanks") + "?inv=%d" % invoice_id,
        cancel_url=base + url_for("invoices.detail", inv_id=invoice_id))
    if not ok:
        flash("Stripe error: %s" % res, "error")
        return redirect(url_for("invoices.detail", inv_id=invoice_id))
    url, sid = res
    db.update("invoices", invoice_id, payment_link=url, stripe_session_id=sid)
    if inv.get("job_id"):
        db.add_activity("job", inv["job_id"], "automation",
                        "Stripe pay link created for invoice %s" % inv.get("number"))
    flash("Stripe pay link created — the client portal 'Pay now' button now uses it.", "ok")
    return redirect(url_for("invoices.detail", inv_id=invoice_id))


@bp.route("/thanks")
def thanks():
    return ("<h2 style='font-family:sans-serif'>Thank you — your payment was received.</h2>"
            "<p style='font-family:sans-serif'>You can close this window.</p>")


def _verify_sig(raw, header):
    """Verify a Stripe webhook signature (t=…,v1=…) against STRIPE_WEBHOOK_SECRET."""
    secret = (os.environ.get("STRIPE_WEBHOOK_SECRET", "") or "").strip()
    if not secret or not header:
        return False
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
    t, v1 = parts.get("t"), parts.get("v1")
    if not t or not v1:
        return False
    # Reject stale timestamps (>5 min) to blunt replay.
    try:
        if abs(time.time() - int(t)) > 300:
            return False
    except ValueError:
        return False
    signed = ("%s.%s" % (t, raw.decode("utf-8", "replace"))).encode()
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, v1)


@bp.route("/webhook", methods=["POST"])
def webhook():
    """Stripe → us. Verify signature, then on checkout.session.completed mark the
    invoice paid and write a payments-ledger row (idempotent by session id)."""
    raw = request.get_data()
    if not _verify_sig(raw, request.headers.get("Stripe-Signature", "")):
        abort(400)
    import json as _json
    try:
        event = _json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        abort(400)
    if event.get("type") != "checkout.session.completed":
        return jsonify(ok=True, ignored=event.get("type"))
    sess = (event.get("data", {}) or {}).get("object", {}) or {}
    inv_id = (sess.get("metadata", {}) or {}).get("invoice_id") or sess.get("client_reference_id")
    if not inv_id:
        return jsonify(ok=True, note="no invoice id")
    try:
        inv_id = int(inv_id)
    except (TypeError, ValueError):
        return jsonify(ok=True, note="bad invoice id")
    inv = db.get("invoices", inv_id)
    if not inv:
        return jsonify(ok=True, note="invoice gone")
    sid = sess.get("id")
    # Idempotency: skip if we already recorded a payment for this session.
    if sid and db.all_rows("payments", "invoice_id=? AND reference=?", (inv_id, sid), limit=1):
        return jsonify(ok=True, note="already recorded")
    amt = (sess.get("amount_total") or 0) / 100.0
    db.insert("payments", {"job_id": inv.get("job_id"), "invoice_id": inv_id,
                           "amount": round(amt, 2), "method": "Stripe (card)",
                           "reference": sid or "", "paid_date": db.today(),
                           "source": "Stripe", "created": db.now()})
    db.update("invoices", inv_id, status="paid", paid_date=db.today(),
              amount_paid=inv.get("amount"))
    if inv.get("job_id"):
        db.add_activity("job", inv["job_id"], "automation",
                        "Invoice %s paid online via Stripe" % inv.get("number"))
    return jsonify(ok=True, marked_paid=inv_id)
