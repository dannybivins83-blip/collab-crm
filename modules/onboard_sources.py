# -*- coding: utf-8 -*-
"""External data sources for one-click homeowner onboarding.

CompanyCam  — the roofer's field-photo app: real jobs (name + address) and the
              crew's job photos. Token: env COMPANYCAM_API_TOKEN (per-tenant).
JobNimbus   — the roofer's CRM: customers/jobs WITH contact info (email/phone).
              Key: env JOBNIMBUS_API_KEY (per-tenant).

Both are OPTIONAL and read their token from the environment; every call fails
soft (returns [] / {}), so the onboarding UI degrades gracefully to a "connect"
state when a token is missing. Nothing here writes to either service.
"""
import os
import re

try:
    import requests
except Exception:  # pragma: no cover
    requests = None

CC_BASE = "https://api.companycam.com/v2"
JN_BASE = "https://app.jobnimbus.com/api1"
_TIMEOUT = 15


# ---------------------------------------------------------------------------
# CompanyCam
# ---------------------------------------------------------------------------
def cc_token():
    return (os.environ.get("COMPANYCAM_API_TOKEN") or "").strip()


def cc_enabled():
    return bool(cc_token() and requests)


def _cc_get(path, params=None):
    if not cc_enabled():
        return None
    try:
        r = requests.get(CC_BASE + path,
                         headers={"Authorization": "Bearer " + cc_token(),
                                  "Accept": "application/json"},
                         params=params or {}, timeout=_TIMEOUT)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def cc_list_projects(page=1, per_page=50):
    """Return a normalized list of CompanyCam projects (newest first)."""
    data = _cc_get("/projects", {"page": page, "per_page": per_page})
    out = []
    for p in (data or []):
        addr = p.get("address") or {}
        out.append({
            "id": str(p.get("id") or ""),
            "name": (p.get("name") or "").strip(),
            "address": (addr.get("street_address_1") or "").strip(),
            "city": (addr.get("city") or "").strip(),
            "state": (addr.get("state") or "").strip(),
            "zip": (addr.get("postal_code") or "").strip(),
            "photo_count": p.get("photo_count") or 0,
            "feature_image": _first_uri(p.get("feature_image") or []),
        })
    return out


def cc_project_photos(project_id, per_page=24):
    """Return [{url, thumb, captured}] for a project's photos (newest first)."""
    data = _cc_get("/projects/%s/photos" % project_id, {"per_page": per_page})
    return [ph for ph in (_norm_photo(x) for x in (data or [])) if ph]


def _first_uri(uris, want=("web", "thumbnail", "original")):
    by = {u.get("type"): (u.get("url") or u.get("uri")) for u in (uris or [])}
    for t in want:
        if by.get(t):
            return by[t]
    return ""


def _norm_photo(ph):
    uris = ph.get("uris") or []
    by = {u.get("type"): (u.get("url") or u.get("uri")) for u in uris}
    url = by.get("web") or by.get("original") or by.get("thumbnail")
    if not url:
        return None
    return {"url": url, "thumb": by.get("thumbnail") or url,
            "captured": (ph.get("captured_at") or "")[:10]}


# System guessed from a CompanyCam project name like "Case Residence; Tile Re-Roof".
_SYS_WORDS = [("tile", "tile"), ("metal", "metal"), ("standing", "metal"),
              ("gaco", "flat"), ("flat", "flat"), ("modified", "flat"),
              ("tpo", "flat"), ("shingle", "shingle"), ("gutter", "shingle")]


def guess_system(project_name):
    n = (project_name or "").lower()
    for frag, sysk in _SYS_WORDS:
        if frag in n:
            return sysk
    return "shingle"


def clean_person_name(project_name):
    """'Case Residence; Tile Re-Roof' -> 'Case Residence'."""
    base = re.split(r"[;|]", project_name or "", 1)[0].strip()
    return base or (project_name or "").strip()


# ---------------------------------------------------------------------------
# JobNimbus (contact enrichment by address — best effort)
# ---------------------------------------------------------------------------
def jn_key():
    return (os.environ.get("JOBNIMBUS_API_KEY") or "").strip()


def jn_enabled():
    return bool(jn_key() and requests)


def _jn_get(path, params=None):
    if not jn_enabled():
        return None
    try:
        r = requests.get(JN_BASE + path,
                         headers={"Authorization": "Bearer " + jn_key(),
                                  "Accept": "application/json"},
                         params=params or {}, timeout=_TIMEOUT)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def _norm_addr(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def jn_contact_for_address(street, city=""):
    """Best-effort: find a JobNimbus contact whose address matches `street`.
    Returns {email, phone, name} or {} — never raises."""
    if not jn_enabled() or not street:
        return {}
    data = _jn_get("/contacts", {"size": 100})
    rows = (data or {}).get("results") or (data or {}).get("contacts") or []
    target = _norm_addr(street)
    for c in rows:
        addr = c.get("address_line1") or c.get("address1") or c.get("address") or ""
        if target and _norm_addr(addr) == target:
            return {
                "email": (c.get("email") or "").strip(),
                "phone": (c.get("mobile_phone") or c.get("home_phone")
                          or c.get("phone") or c.get("work_phone") or "").strip(),
                "name": (c.get("display_name") or c.get("name") or "").strip(),
            }
    return {}
