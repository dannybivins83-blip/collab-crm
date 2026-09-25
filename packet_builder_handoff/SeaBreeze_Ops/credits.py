# -*- coding: utf-8 -*-
"""Prepaid credits for the permit packet builder. One credit costs one US dollar.

Money is taken by Stripe Checkout, which is hosted by Stripe: no card number ever
reaches this app, so none of this code is in PCI scope. We only ever create a
session and then ask Stripe whether it was paid.

This app binds to 127.0.0.1, so a Stripe webhook cannot reach it. The balance is
therefore credited when the browser comes back from Checkout, and the payment is
confirmed by calling Stripe rather than by trusting anything in that URL. Crediting
is keyed on the Stripe session id, so a refresh, a back-button or a replayed link
cannot add the same purchase twice. A webhook route is included as well, for the
day this runs somewhere Stripe can reach.

The secret key is read from the environment and is never written to the ledger,
the logs or the page.
"""
import io
import json
import os
import threading

import requests

API = "https://api.stripe.com/v1"
DOLLARS_PER_CREDIT = 1           # one credit, one dollar
MAX_PURCHASE = 2000              # sanity ceiling on a single top-up, in credits
_LOCK = threading.Lock()


def _store_path():
    """Ledger lives outside the repo by default - it is a financial record."""
    override = os.environ.get("PACKET_BUILDER_CREDITS_FILE")
    if override:
        return override
    base = (os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME")
            or os.path.expanduser("~"))
    d = os.path.join(base, "PermitBuilder")
    try:
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, "credits.json")
    except Exception:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "credits.json")


def secret_key():
    return (os.environ.get("STRIPE_SECRET_KEY") or "").strip()


def configured():
    return bool(secret_key())


def live_mode():
    return secret_key().startswith("sk_live")


def _load():
    p = _store_path()
    if not os.path.exists(p):
        return {"balance": 0, "entries": []}
    try:
        with io.open(p, encoding="utf-8") as f:
            d = json.load(f)
        d.setdefault("balance", 0)
        d.setdefault("entries", [])
        return d
    except Exception:
        # Never lose money to a corrupt file: keep it and start a fresh ledger.
        try:
            os.replace(p, p + ".corrupt")
        except Exception:
            pass
        return {"balance": 0, "entries": []}


def _save(d):
    p = _store_path()
    tmp = p + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)
    os.replace(tmp, p)


def balance():
    return _load()["balance"]


def history(limit=25):
    return list(reversed(_load()["entries"]))[:limit]


def _entry(d, kind, credits, note, ref=""):
    d["entries"].append({"kind": kind, "credits": credits, "note": note,
                         "ref": ref, "balance_after": d["balance"]})


def debit(credits, note, ref=""):
    """Record usage. Deliberately allows a negative balance: this meters what is
    owed, it does not stand between the operator and a job that needs doing."""
    with _LOCK:
        d = _load()
        d["balance"] -= int(credits)
        _entry(d, "debit", -int(credits), note, ref)
        _save(d)
        return d["balance"]


def _credit_locked(d, credits, note, ref):
    d["balance"] += int(credits)
    _entry(d, "credit", int(credits), note, ref)


def apply_payment(session_id, credits, note="Credit purchase"):
    """Idempotent by Stripe session id. Returns (applied, balance)."""
    with _LOCK:
        d = _load()
        if any(e.get("ref") == session_id and e.get("kind") == "credit"
               for e in d["entries"]):
            return False, d["balance"]
        _credit_locked(d, credits, note, session_id)
        _save(d)
        return True, d["balance"]


# --- Stripe -----------------------------------------------------------------

def _auth():
    key = secret_key()
    if not key:
        raise RuntimeError("STRIPE_SECRET_KEY is not set")
    return {"Authorization": "Bearer " + key}


def create_checkout(credits, success_url, cancel_url):
    """Hosted Stripe Checkout for `credits` x $1. Returns the URL to send the
    browser to. Raises RuntimeError with a readable message on failure."""
    credits = int(credits)
    if credits < 1 or credits > MAX_PURCHASE:
        raise RuntimeError("Choose between 1 and %d credits." % MAX_PURCHASE)
    data = {
        "mode": "payment",
        "success_url": success_url,
        "cancel_url": cancel_url,
        "client_reference_id": "permit-builder-credits",
        "line_items[0][quantity]": credits,
        "line_items[0][price_data][currency]": "usd",
        "line_items[0][price_data][unit_amount]": DOLLARS_PER_CREDIT * 100,
        "line_items[0][price_data][product_data][name]": "Permit Packet Builder credit",
        "line_items[0][price_data][product_data][description]":
            "Prepaid usage credit. One credit = one US dollar.",
        "metadata[credits]": credits,
    }
    r = requests.post(API + "/checkout/sessions", data=data, headers=_auth(), timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(_stripe_error(r))
    return r.json()["url"]


def confirm_session(session_id):
    """Ask Stripe whether this session was actually paid. Never trust the browser.
    Returns (paid, credits, amount_total_cents)."""
    r = requests.get(API + "/checkout/sessions/" + session_id, headers=_auth(), timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(_stripe_error(r))
    s = r.json()
    paid = s.get("payment_status") == "paid"
    # Trust the amount Stripe reports, not the metadata that came back with it.
    cents = int(s.get("amount_total") or 0)
    credits = cents // (DOLLARS_PER_CREDIT * 100)
    return paid, credits, cents


def _stripe_error(r):
    try:
        msg = r.json().get("error", {}).get("message") or r.text[:200]
    except Exception:
        msg = r.text[:200]
    return "Stripe said: %s" % msg
