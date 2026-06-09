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


# ---------------------------------------------------------------------------
# SeaBreeze job-name encoding helpers (see reference_job_naming_convention):
#   R-26179: Richard Reis (PBC) (S17) (DB)
# The roof code's leading letter is the material; the AHJ gets a shop abbrev.
# Only DOCUMENTED abbreviations are emitted — unknown jurisdictions are left
# blank + flagged (never invented), so reports never carry a made-up code.
# ---------------------------------------------------------------------------

def roof_letter(work_type):
    """Leading letter(s) of the roof code from a work type (S/T/M/F, or 5V for
    5V-crimp metal). Order matters for combo types ("Shingle + Flat" -> S): the
    predominant material is named first, so tile/metal/shingle are tested before
    flat. Returns '' for non-roofing work types (Repair/Other) — the caller
    should flag rather than guess a letter."""
    low = (work_type or "").lower()
    if not low:
        return ""
    if "5v" in low or "5-v" in low:
        return "5V"
    if "tile" in low:
        return "T"
    if "metal" in low:
        return "M"
    if "shingle" in low:
        return "S"
    if "flat" in low or "tpo" in low or "mod" in low or "bur" in low or "hot-mop" in low:
        return "F"
    return ""


# Documented AHJ abbreviations only (confirmed in reference_job_naming_convention),
# keyed on the lower-cased jurisdiction name. The default county "Palm Beach
# County" -> PBC covers unincorporated addresses that fall back to the county.
AHJ_ABBREV = {
    "palm beach county": "PBC",
    "boynton beach": "BB",
    "lake worth beach": "LWB",
    "royal palm beach": "RPB",
}


def ahj_abbrev(resolved):
    """(abbrev, confident) for a resolved AHJ/jurisdiction label.

    confident is False when there's a jurisdiction but no DOCUMENTED abbreviation
    — the caller should leave the AHJ slot blank and flag it for Danny to confirm
    rather than inventing a code. An empty input is 'confident' (nothing to flag)."""
    if not resolved:
        return "", True
    norm = re.sub(r"[_\s]+", " ", str(resolved)).strip().lower()
    if norm in AHJ_ABBREV:
        return AHJ_ABBREV[norm], True
    return "", False
