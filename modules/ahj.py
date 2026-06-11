# -*- coding: utf-8 -*-
"""AHJ (Authority Having Jurisdiction) resolver + roof-type → system mapping.

When a lead is entered we resolve the permit office (AHJ) from the property
address by matching the municipality against the SeaBreeze permit library's 70
PBC/Broward municipalities. Incorporated city -> that city's building dept;
otherwise fall back to the county. The roof work type maps to a permit system
(shingle/tile/metal/flat) used to auto-create the permit.
"""
import os
import json
import re

# Per-AHJ online permitting portal data (login URL, platform, online-submission status,
# contractor registration, NOC recording) researched for all 70 PBC/Broward municipalities.
# Lets the permit page render a clickable "Submit to <city> portal" link.
_PORTALS = None


def _load_portals():
    global _PORTALS
    if _PORTALS is None:
        try:
            import config
            path = os.path.join(config.DATA_DIR, "ahj_portals.json")
            with open(path, "r", encoding="utf-8") as fh:
                _PORTALS = (json.load(fh) or {}).get("ahjs", {})
        except Exception:
            _PORTALS = {}
    return _PORTALS


def ahj_portal(ahj_key):
    """Return the permitting-portal info dict for an AHJ key (e.g. 'Royal_Palm_Beach'),
    or {} if unknown. Keys: city, county, platform, portal_url, login_url, register_url,
    online, upload, notes, phone, email, confidence."""
    if not ahj_key:
        return {}
    return _load_portals().get(ahj_key, {})


def _build():
    try:
        import build
        return build
    except Exception:
        return None


def _ahj_keys():
    b = _build()
    try:
        return b.list_ahjs() if b else []
    except Exception:
        return []


def city_from(address, city):
    if city and city.strip():
        return city.strip()
    if address:
        parts = [p.strip() for p in address.split(",")]
        if len(parts) >= 2:
            # "street, city, FL zip" -> city is parts[1]
            return parts[1]
    return ""


def resolve_ahj(address="", city="", county=""):
    """Return the best AHJ (permit office) label for an address."""
    cand = city_from(address, city)
    keys = _ahj_keys()
    if cand and keys:
        norm = re.sub(r"\s+", "_", cand.strip()).lower()
        for k in keys:                       # exact municipality match
            if k.lower() == norm:
                return k
        for k in keys:                       # contains (e.g. "Palm Beach Gardens")
            if norm and norm in k.lower():
                return k
        for k in keys:                       # first token match (e.g. "Boynton")
            if cand.split()[0].lower() in k.lower():
                return k
    # Fall back to the city as-typed, else the county building dept.
    return cand or (county or "")


def resolve_ahj_strict(address="", city="", county=""):
    """Like resolve_ahj, but return a real permit-library AHJ key ONLY — '' when there's
    no confident municipality match. Use where a wrong/garbage AHJ (e.g. a 'FL 33414'
    city field) is worse than leaving it blank, such as bulk backfills."""
    a = resolve_ahj(address, city, county)
    return a if a in set(_ahj_keys()) else ""


def system_from_work_type_strict(work_type):
    """Roof system from the work type ONLY when a roofing material is explicitly named
    (else '') — avoids defaulting generic work types like 'New'/'Retail' to shingle."""
    low = (work_type or "").lower()
    if any(k in low for k in ("tile", "metal", "flat", "tpo", "mod", "bur", "shingle")):
        return work_type_to_system(work_type)
    return ""


def work_type_to_system(work_type):
    """Map a roof work type to a permit system key (shingle/tile/metal/flat)."""
    low = (work_type or "").lower()
    if "tile" in low:
        return "tile"
    if "metal" in low:
        return "metal"
    if "flat" in low or "tpo" in low or "mod" in low or "bur" in low:
        return "flat"
    if "shingle" in low:
        return "shingle"
    if "repair" in low:
        return ""  # repairs usually don't need a re-roof permit packet
    return "shingle"
