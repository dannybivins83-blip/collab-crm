# -*- coding: utf-8 -*-
"""AHJ (Authority Having Jurisdiction) resolver + roof-type → system mapping.

When a lead is entered we resolve the permit office (AHJ) from the property
address by matching the municipality against the SeaBreeze permit library's 70
PBC/Broward municipalities. Incorporated city -> that city's building dept;
otherwise fall back to the county. The roof work type maps to a permit system
(shingle/tile/metal/flat) used to auto-create the permit.
"""
import re


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
