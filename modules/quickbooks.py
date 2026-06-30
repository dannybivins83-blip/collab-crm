# -*- coding: utf-8 -*-
"""QuickBooks Online integration (ready-for-keys).

Wire-up: paste your Intuit app's Client ID + Secret in Settings → QuickBooks,
register the shown Redirect URI in the Intuit developer portal, click Connect,
approve on Intuit's consent screen. After that the app can push invoices to QBO,
pull the QuickBooks Payments "Pay now" link, and have QBO email the invoice +
link to the customer (rep/admin clicks Send on the dashboard).

No financial credentials are ever entered by the assistant — the OAuth consent
is performed by the signed-in user on Intuit's own screen.
"""
import json
import secrets
import time
import base64
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from flask import Blueprint, request, redirect, url_for, flash, session

import db

bp = Blueprint("quickbooks", __name__, url_prefix="/quickbooks")

AUTH_URL = "https://appcenter.intuit.com/connect/oauth2"
TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
SCOPE = "com.intuit.quickbooks.accounting"


def _api_base(env):
    return ("https://sandbox-quickbooks.api.intuit.com" if env == "sandbox"
            else "https://quickbooks.api.intuit.com")


def cfg():
    return db.get_integrations()


def is_configured():
    c = cfg()
    return bool(c.get("qbo_client_id") and c.get("qbo_client_secret"))


def is_connected():
    c = cfg()
    return bool(c.get("qbo_realm_id") and c.get("qbo_refresh_token"))


def _redirect_uri():
    # Must exactly match a Redirect URI registered in the Intuit app.
    saved = cfg().get("qbo_redirect_uri")
    if saved:
        return saved
    return request.url_root.rstrip("/") + url_for("quickbooks.callback")


# ---------------------------------------------------------------------------
# OAuth
# ---------------------------------------------------------------------------

@bp.route("/connect")
def connect():
    if not is_configured():
        flash("Add your QuickBooks Client ID + Secret first.", "error")
        return redirect(url_for("quickbooks.settings"))
    ruri = _redirect_uri()
    db.save_integrations({"qbo_redirect_uri": ruri})
    state = secrets.token_urlsafe(32)
    session["qbo_state"] = state
    params = {"client_id": cfg()["qbo_client_id"], "response_type": "code",
              "scope": SCOPE, "redirect_uri": ruri, "state": state}
    return redirect(AUTH_URL + "?" + urllib.parse.urlencode(params))


@bp.route("/callback")
def callback():
    if request.args.get("state") != session.pop("qbo_state", None):
        flash("QuickBooks: state mismatch, please retry Connect.", "error")
        return redirect(url_for("quickbooks.settings"))
    code = request.args.get("code")
    realm = request.args.get("realmId")
    if not code:
        flash("QuickBooks authorization was cancelled.", "error")
        return redirect(url_for("quickbooks.settings"))
    tok = _exchange_token({"grant_type": "authorization_code", "code": code,
                           "redirect_uri": _redirect_uri()})
    if not tok:
        flash("QuickBooks token exchange failed.", "error")
        return redirect(url_for("quickbooks.settings"))
    db.save_integrations({
        "qbo_realm_id": realm,
        "qbo_access_token": tok["access_token"],
        "qbo_refresh_token": tok["refresh_token"],
        "qbo_token_expiry": (datetime.now() + timedelta(seconds=tok.get("expires_in", 3600))).strftime("%Y-%m-%d %H:%M:%S"),
        "qbo_connected_at": db.now()})
    flash("QuickBooks connected ✔", "ok")
    return redirect(url_for("quickbooks.settings"))


@bp.route("/disconnect", methods=["POST"])
def disconnect():
    db.save_integrations({"qbo_realm_id": "", "qbo_access_token": "", "qbo_refresh_token": "",
                          "qbo_token_expiry": "", "qbo_connected_at": ""})
    flash("QuickBooks disconnected.", "ok")
    return redirect(url_for("quickbooks.settings"))


@bp.route("/settings", methods=["GET", "POST"])
def settings():
    from flask import render_template
    if request.method == "POST":
        db.save_integrations({
            "qbo_client_id": request.form.get("qbo_client_id", "").strip(),
            "qbo_client_secret": request.form.get("qbo_client_secret", "").strip(),
            "qbo_environment": request.form.get("qbo_environment", "production")})
        flash("QuickBooks settings saved.", "ok")
        return redirect(url_for("quickbooks.settings"))
    return render_template("quickbooks.html", c=cfg(), connected=is_connected(),
                           configured=is_configured(),
                           redirect_uri=(cfg().get("qbo_redirect_uri") or
                                         (request.url_root.rstrip("/") + url_for("quickbooks.callback"))))


# ---------------------------------------------------------------------------
# Token plumbing
# ---------------------------------------------------------------------------

def _basic_auth():
    c = cfg()
    raw = "%s:%s" % (c.get("qbo_client_id", ""), c.get("qbo_client_secret", ""))
    return "Basic " + base64.b64encode(raw.encode()).decode()


def _exchange_token(form):
    try:
        data = urllib.parse.urlencode(form).encode()
        req = urllib.request.Request(TOKEN_URL, data=data, headers={
            "Authorization": _basic_auth(), "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def _valid_access_token():
    c = cfg()
    exp = c.get("qbo_token_expiry")
    if c.get("qbo_access_token") and exp:
        try:
            if datetime.now() < datetime.strptime(exp, "%Y-%m-%d %H:%M:%S") - timedelta(seconds=60):
                return c["qbo_access_token"]
        except Exception:
            pass
    # refresh
    tok = _exchange_token({"grant_type": "refresh_token", "refresh_token": c.get("qbo_refresh_token", "")})
    if not tok:
        return None
    db.save_integrations({
        "qbo_access_token": tok["access_token"],
        "qbo_refresh_token": tok.get("refresh_token", c.get("qbo_refresh_token")),
        "qbo_token_expiry": (datetime.now() + timedelta(seconds=tok.get("expires_in", 3600))).strftime("%Y-%m-%d %H:%M:%S")})
    return tok["access_token"]


def _api(method, path, body=None, params=None):
    """Call the QBO REST API. Returns (ok, data_or_error)."""
    if not is_connected():
        return False, "QuickBooks not connected"
    token = _valid_access_token()
    if not token:
        return False, "Could not obtain a QuickBooks access token (reconnect?)"
    c = cfg()
    url = "%s/v3/company/%s/%s" % (_api_base(c.get("qbo_environment", "production")), c["qbo_realm_id"], path)
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    try:
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=25) as r:
            return True, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return False, e.read().decode()[:500]
        except Exception:
            return False, str(e)
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------------------
# Business operations
# ---------------------------------------------------------------------------

def _query(sql):
    return _api("GET", "query", params={"query": sql, "minorversion": "65"})


def ensure_customer(name, email=None):
    safe = (name or "Customer").replace("'", "")
    ok, data = _query("select * from Customer where DisplayName = '%s'" % safe)
    if ok:
        rows = (data.get("QueryResponse", {}) or {}).get("Customer", [])
        if rows:
            return rows[0]["Id"]
    body = {"DisplayName": name}
    if email:
        body["PrimaryEmailAddr"] = {"Address": email}
    ok, data = _api("POST", "customer", body=body, params={"minorversion": "65"})
    if ok:
        return data.get("Customer", {}).get("Id")
    return None


def _default_item():
    ok, data = _query("select * from Item where Type = 'Service' maxresults 1")
    if ok:
        rows = (data.get("QueryResponse", {}) or {}).get("Item", [])
        if rows:
            return rows[0]["Id"]
    return "1"


def create_invoice(inv, job, contact_name, email, amount, description):
    """Create a QBO invoice for our invoice row. Returns (ok, qbo_id_or_error)."""
    cust = ensure_customer(contact_name, email)
    if not cust:
        return False, "Could not create/find the QuickBooks customer"
    item = _default_item()
    body = {
        "CustomerRef": {"value": cust},
        "AllowOnlineCreditCardPayment": True,
        "AllowOnlineACHPayment": True,
        "Line": [{
            "Amount": round(float(amount or 0), 2),
            "DetailType": "SalesItemLineDetail",
            "Description": description or inv.get("number", ""),
            "SalesItemLineDetail": {"ItemRef": {"value": item}},
        }],
    }
    if email:
        body["BillEmail"] = {"Address": email}
    if inv.get("due_date"):
        body["DueDate"] = inv["due_date"]
    ok, data = _api("POST", "invoice", body=body, params={"minorversion": "65"})
    if not ok:
        return False, data
    return True, data.get("Invoice", {}).get("Id")


def invoice_link(qbo_id):
    """Fetch the shareable QuickBooks Payments 'Pay now' link for an invoice."""
    ok, data = _api("GET", "invoice/%s" % qbo_id, params={"include": "invoiceLink", "minorversion": "65"})
    if ok:
        return data.get("Invoice", {}).get("InvoiceLink")
    return None


def send_invoice(qbo_id, email):
    """Have QuickBooks email the invoice (with the Pay-now link) to the customer."""
    params = {"minorversion": "65"}
    if email:
        params["sendTo"] = email
    return _api("POST", "invoice/%s/send" % qbo_id, body=None, params=params)


def invoice_balance(qbo_id):
    """Current open balance for a QBO invoice (0 once the customer has paid). None on error."""
    ok, data = _api("GET", "invoice/%s" % qbo_id, params={"minorversion": "65"})
    if not ok:
        return None
    return (data.get("Invoice", {}) or {}).get("Balance")


def sync_payments():
    """Close the loop on QuickBooks Payments: for every CRM invoice we pushed to QBO that
    isn't already marked paid, read its QBO balance and — when QuickBooks shows it cleared
    (customer paid via the Pay-now link) — mark the CRM invoice paid and write a payments
    row. Returns (checked, marked_paid)."""
    if not is_connected():
        return 0, 0
    rows = db.all_rows("invoices", "qbo_id IS NOT NULL AND qbo_id!='' AND (status IS NULL OR status!='paid')")
    checked = marked = 0
    for inv in rows:
        checked += 1
        bal = invoice_balance(inv.get("qbo_id"))
        if bal is None:
            continue
        try:
            bal = float(bal)
        except (TypeError, ValueError):
            continue
        if bal <= 0.005:
            db.update("invoices", inv["id"], status="paid", paid_date=db.today(),
                      amount_paid=inv.get("amount"))
            # Write a payments-ledger row if this invoice has none yet (so the ledger
            # reflects QBO-collected money, not just AccuLynx-imported history).
            if not db.all_rows("payments", "invoice_id=?", (inv["id"],), limit=1):
                db.insert("payments", {"job_id": inv.get("job_id"), "invoice_id": inv["id"],
                                       "amount": inv.get("amount") or 0, "method": "QuickBooks",
                                       "paid_date": db.today(), "source": "QuickBooks",
                                       "created": db.now()})
            if inv.get("job_id"):
                db.add_activity("job", inv["job_id"], "automation",
                                "Invoice %s marked paid — QuickBooks balance cleared" % inv.get("number"))
            marked += 1
    return checked, marked


@bp.route("/sync-payments", methods=["POST"])
def sync_payments_route():
    """Admin button: pull QBO balances and auto-mark cleared invoices paid."""
    if not is_connected():
        flash("Connect QuickBooks first.", "error")
        return redirect(url_for("quickbooks.settings"))
    checked, marked = sync_payments()
    flash("Checked %d QuickBooks invoice(s); marked %d paid." % (checked, marked), "ok")
    return redirect(url_for("quickbooks.settings"))
