# -*- coding: utf-8 -*-
"""Import AccuLynx contacts into the CRM contacts table (resumable).

The /contacts list gives name/company/crossRef/address inline, but email & phone
come as sub-resource links — so we follow the PRIMARY email + phone per contact.
Dedupes by AccuLynx contact id (ext_id column). Run repeatedly; it resumes from a
cursor stored in company_settings.
"""
import sys
import os
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db
from modules import acculynx_sync as S

KEY = (db.get_company().get("acculynx_api_key") or "").strip()
BASE = S.DEFAULT_BASE
PAGE = 25

db._ensure_column("contacts", "ext_id", "TEXT")        # AccuLynx contact GUID (dedupe key)
db._ensure_column("contacts", "cross_ref", "TEXT")


def _g(o, *ks, default=""):
    for k in ks:
        v = (o or {}).get(k)
        if v not in (None, ""):
            return v
    return default


def _addr(a):
    if not isinstance(a, dict):
        return {}
    st = " ".join(p for p in (_g(a, "street1"), _g(a, "street2")) if p).strip()
    state = a.get("state")
    state = state.get("abbreviation") or state.get("name") if isinstance(state, dict) else (state or "")
    return {"address": st, "city": _g(a, "city"), "state": state, "zip": _g(a, "zipCode", "zip")}


def _first_value(arr, sub, value_keys):
    """Follow the first sub-resource link in arr and pull the value."""
    if not arr:
        return ""
    item = arr[0]
    cid = item.get("id")
    parent = item.get("_link", "")
    if not cid:
        return ""
    # the _link is the full sub-resource URL already
    try:
        import urllib.request, json
        req = urllib.request.Request(parent, headers={"Authorization": "Bearer " + KEY, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=20, context=S._SSL_CTX) as r:
            d = json.loads(r.read())
        return _g(d, *value_keys)
    except Exception:
        return ""


def _nk(fn, ln, em, ph):
    """Dedupe key — matches AccuLynx contacts to ones the job-sync already created."""
    return "%s|%s|%s" % ((fn or "").lower().strip(), (ln or "").lower().strip(),
                         (em or ph or "").lower().strip())


def _build_index():
    by_ext, by_name = {}, {}
    for c in db.all_rows("contacts"):
        if c.get("ext_id"):
            by_ext[c["ext_id"]] = c
        by_name[_nk(c.get("first_name"), c.get("last_name"), c.get("email"), c.get("phone"))] = c
    return by_ext, by_name


def run(limit=None, budget=420, verbose=False):
    by_ext, by_name = _build_index()
    cur = int(db.get_company().get("acculynx_contact_cursor") or 0)
    t0 = time.time()
    added = updated = skipped = 0
    samples = []
    done = False
    while True:
        if budget and time.time() - t0 > budget:
            break
        d = S._api_get(BASE, "/contacts", KEY, {"pageStartIndex": cur, "pageSize": PAGE})
        items = d.get("items") if isinstance(d, dict) else d
        if not items:
            done = True
            break
        for c in items:
            ext = c.get("id")
            email = _first_value(c.get("emailAddresses"), "email", ("address", "emailAddress", "value"))
            phraw = c.get("phoneNumbers")
            phone = _first_value(phraw, "phone", ("number", "phoneNumber", "value"))
            a = _addr(c.get("mailingAddress") or c.get("billingAddress"))
            rec = {"first_name": _g(c, "firstName"), "last_name": _g(c, "lastName"),
                   "company": _g(c, "companyName"), "cross_ref": _g(c, "crossReference"),
                   "email": email, "phone": phone, **a,
                   "source": "AccuLynx import", "ext_id": ext, "kind": "person"}
            nk = _nk(rec["first_name"], rec["last_name"], email, phone)
            match = by_ext.get(ext) or by_name.get(nk)
            if match:
                db.update("contacts", match["id"], **{k: v for k, v in rec.items() if v})
                updated += 1
            else:
                nid = db.insert("contacts", rec)
                rec["id"] = nid
                by_ext[ext] = rec
                by_name[nk] = rec
                added += 1
            if verbose and len(samples) < 8:
                samples.append("%s %s | %s | %s | %s %s" % (rec["first_name"], rec["last_name"],
                               rec["phone"] or "-", rec["email"] or "-", rec["address"], rec["state"]))
            if limit and (added + updated) >= limit:
                break
        cur += len(items)
        db.save_company({"acculynx_contact_cursor": cur})
        if len(items) < PAGE:
            done = True
            break
        if limit and (added + updated) >= limit:
            break
    db.save_company({"acculynx_contact_cursor": 0 if done else cur})
    return {"added": added, "updated": updated, "done": done, "cursor": cur, "samples": samples}


if __name__ == "__main__":
    lim = int(sys.argv[1]) if len(sys.argv) > 1 else None
    bud = int(sys.argv[2]) if len(sys.argv) > 2 else 420
    print(run(limit=lim, budget=bud, verbose=True))
