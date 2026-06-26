# -*- coding: utf-8 -*-
"""QXO (Beacon Building Products) — native materials integration.

Lets the CRM price + order roofing materials directly against QXO's partner API:
live pricing into estimates, a job BOM → a QXO material order, delivery tracking
(with photos) on the job board, and invoice reconciliation.

────────────────────────────────────────────────────────────────────────────
STATUS: DARK SCAFFOLD (env-gated, ships disabled).
QXO's "custom API" (https://www.qxo.com/customapi) is a *gated partner program* —
the endpoint paths, auth method, and request/response shapes are NOT public; they
arrive only after the API partner application is approved
(https://go.qxo.com/qxoapi, requires an existing QXO/Beacon account).

So everything below is wired but inert: with no credentials configured every call
returns {"ok": False, "reason": "qxo_not_configured"} and nothing touches the
network. The pieces marked  # TODO(spec):  are PLACEHOLDERS to confirm against the
real partner docs — do not treat the paths/auth here as verified. Once you have the
spec + keys, filling them in is a small, localized change; the wire-ins and the
SKU-map table already exist.
────────────────────────────────────────────────────────────────────────────

Activation (no code change once the spec is confirmed): set env vars
  - QXO_API_BASE            e.g. https://api.qxo.com   # TODO(spec): real base
  - QXO_API_KEY             partner API key/bearer       (key-auth shape), OR
  - QXO_CLIENT_ID / QXO_CLIENT_SECRET                    (OAuth2 client-credentials)
Secrets live only in the gitignored .env — never in code, URLs, or logs.
"""
import os
import time

from flask import Blueprint, jsonify, request, session

import db

bp = Blueprint("qxo", __name__, url_prefix="/qxo")

# Logical API → path. PLACEHOLDERS — confirm every one against the partner spec.
PATHS = {
    "pricing": "/v1/pricing",            # TODO(spec)
    "product": "/v1/products",           # TODO(spec)
    "order": "/v1/orders",               # TODO(spec)
    "delivery": "/v1/deliveries",        # TODO(spec)
    "invoice": "/v1/invoices",           # TODO(spec)
    "account": "/v1/account",            # TODO(spec)
}

# Cross-reference: a CRM material ↔ a QXO product id. This is the join that makes
# BOMs orderable and estimates priceable. Created idempotently (mirrors the
# gmail_accounts pattern).
try:
    db.execute("""CREATE TABLE IF NOT EXISTS qxo_products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        material_id INTEGER,
        qxo_product_id TEXT,
        qxo_sku TEXT,
        description TEXT,
        uom TEXT,
        last_price REAL DEFAULT 0,
        last_priced_at REAL DEFAULT 0,
        updated TEXT)""")
except Exception:
    pass
db._COLCACHE.clear()


# ---------------------------------------------------------------------------
# Config / auth
# ---------------------------------------------------------------------------

def _cfg():
    return {
        "base": os.environ.get("QXO_API_BASE", "").strip().rstrip("/"),
        "api_key": os.environ.get("QXO_API_KEY", "").strip(),
        "client_id": os.environ.get("QXO_CLIENT_ID", "").strip(),
        "client_secret": os.environ.get("QXO_CLIENT_SECRET", "").strip(),
    }


def configured():
    """True only when we have a base URL AND some credential. Until then the whole
    integration is dark and every call short-circuits."""
    c = _cfg()
    return bool(c["base"] and (c["api_key"] or (c["client_id"] and c["client_secret"])))


_token_cache = {"value": "", "exp": 0.0}


def _oauth_token():
    """Fetch/cache an OAuth2 client-credentials token. STUB — only used if QXO
    turns out to use OAuth rather than a static key. # TODO(spec): real token URL,
    grant params, and response field names."""
    c = _cfg()
    if not (c["client_id"] and c["client_secret"]):
        return ""
    if _token_cache["value"] and time.time() < _token_cache["exp"] - 60:
        return _token_cache["value"]
    # TODO(spec): POST to QXO's token endpoint with client_credentials and cache it.
    return ""


def _auth_headers():
    c = _cfg()
    if c["api_key"]:
        # TODO(spec): confirm header name/scheme (Bearer vs x-api-key, etc.).
        return {"Authorization": "Bearer " + c["api_key"]}
    tok = _oauth_token()
    return {"Authorization": "Bearer " + tok} if tok else {}


# ---------------------------------------------------------------------------
# HTTP core — real once base+auth+paths are filled; inert until configured.
# ---------------------------------------------------------------------------

def _request(method, logical, path_suffix="", params=None, json_body=None):
    """Generic call. Returns (data, None) on success or (None, reason) on any
    failure / when not configured. Never raises into callers."""
    if not configured():
        return None, "qxo_not_configured"
    c = _cfg()
    url = c["base"] + PATHS.get(logical, "/" + logical) + (path_suffix or "")
    try:
        import requests
        headers = {"Accept": "application/json"}
        headers.update(_auth_headers())
        r = requests.request(method, url, headers=headers, params=params or {},
                             json=json_body, timeout=30)
        if r.status_code >= 400:
            return None, "qxo_http_%d" % r.status_code
        return (r.json() if r.content else {}), None
    except Exception as e:
        return None, "qxo_error:%s" % type(e).__name__


# ---------------------------------------------------------------------------
# Public API — one function per QXO API. Shapes are PLACEHOLDERS (# TODO(spec)).
# ---------------------------------------------------------------------------

def price_lookup(product_ids):
    """Real-time price for one or more QXO product ids. → {ok, prices:[...]}."""
    data, err = _request("GET", "pricing", params={"ids": ",".join(map(str, product_ids))})
    if err:
        return {"ok": False, "reason": err}
    return {"ok": True, "prices": data}   # TODO(spec): normalize to {product_id, price, uom}


def product_search(query):
    """Catalog search / product details. → {ok, products:[...]}"""
    data, err = _request("GET", "product", params={"q": query})
    if err:
        return {"ok": False, "reason": err}
    return {"ok": True, "products": data}


def place_order(po):
    """Place a material order. `po` is the CRM-built BOM payload. → {ok, order_id}.
    # TODO(spec): map our BOM lines (qxo_product_id, qty, uom, ship-to) to QXO's
    order body; capture and store the returned QXO order id on the job."""
    data, err = _request("POST", "order", json_body=po)
    if err:
        return {"ok": False, "reason": err}
    return {"ok": True, "order": data}


def track_delivery(order_id):
    """Delivery status + photos for a placed order. → {ok, status, photos:[...]}"""
    data, err = _request("GET", "delivery", path_suffix="/" + str(order_id))
    if err:
        return {"ok": False, "reason": err}
    return {"ok": True, "delivery": data}


def get_invoice(invoice_id):
    data, err = _request("GET", "invoice", path_suffix="/" + str(invoice_id))
    if err:
        return {"ok": False, "reason": err}
    return {"ok": True, "invoice": data}


def get_account():
    data, err = _request("GET", "account")
    if err:
        return {"ok": False, "reason": err}
    return {"ok": True, "account": data}


# ---------------------------------------------------------------------------
# SKU map helpers — CRM material ↔ QXO product
# ---------------------------------------------------------------------------

def map_sku(material_id, qxo_product_id, qxo_sku="", description="", uom=""):
    rows = db.all_rows("qxo_products", "material_id=?", (material_id,), "id DESC")
    fields = {"qxo_product_id": str(qxo_product_id), "qxo_sku": qxo_sku,
              "description": description, "uom": uom, "updated": db.now()}
    if rows:
        db.update("qxo_products", rows[0]["id"], **fields)
        return rows[0]["id"]
    fields["material_id"] = material_id
    return db.insert("qxo_products", fields)


def qxo_id_for_material(material_id):
    rows = db.all_rows("qxo_products", "material_id=?", (material_id,), "id DESC")
    return rows[0]["qxo_product_id"] if rows else None


def _count_mapped_skus():
    _c = db.connect()
    try:
        return (_c.execute("SELECT COUNT(*) FROM qxo_products").fetchone() or (0,))[0]
    finally:
        _c.close()


# ---------------------------------------------------------------------------
# Routes (minimal) — status only for now; ordering/pricing wire-ins come with the
# real spec so they aren't built against guessed shapes.
# ---------------------------------------------------------------------------

@bp.route("/status")
def status():
    """Is QXO configured + (best-effort) reachable. Login enforced by the global
    before-request guard (this route is not in auth.PUBLIC)."""
    if not session.get("user_id"):
        return jsonify({"configured": False}), 401
    reachable = None
    if configured() and request.args.get("ping") == "1":
        _, err = _request("GET", "account")
        reachable = err is None
    return jsonify({"configured": configured(), "reachable": reachable,
                    "mapped_skus": _count_mapped_skus()})
