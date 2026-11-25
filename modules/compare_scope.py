# -*- coding: utf-8 -*-
"""Which roof each criterion applies to, and which questions lead the sheet.

The scoping itself is NOT decided here. Every one of the 104 generated
criteria already carries its own `types` tuple -- the handoff authored it --
and this module simply reads it. The bug it fixes was that `spec_for` only
ever looked at the GROUP's types, and only the metal group is type-specific,
so all 68 non-metal criteria were shown on all four roofs regardless of the
types the criterion itself declared.

What that did to a tile homeowner: six of their twenty-five headline
questions were about somebody else's roof -- shingle seal activation, shingle
nail counts, the shingle 160-mph wind upgrade, flat-roof leak response. No
tile proposal states any of them, so each came back "Not stated" and pushed
the rows that actually differ between three quotes down the page.

`compare_criteria.py` is generated and must not be hand-edited, so the small
amount of real judgement -- which questions lead -- lives here.
"""

TILE, SHINGLE, METAL, FLAT = "tile", "shingle", "metal", "flat"

# The landing page now shows a homeowner six photographs and tells them what
# to ask about each one. Those questions have to be answerable on the first
# screen of their sheet rather than three clicks into "show everything", and
# scoping the criteria correctly emptied some roofs' lead rows of anything
# about their own materials. Each roof therefore tops up its own lead set.
# A key listed for a roof it does not apply to is simply never reached.
FEATURED_EXTRA = {
    TILE:    ("vet_13", "pg_14", "pg_01", "pg_10", "vet_21"),
    FLAT:    ("vet_13", "pg_01", "pg_10", "vet_21"),
    METAL:   ("vet_13", "pg_14", "vet_21"),
    SHINGLE: ("ct_01", "ct_04", "ct_05", "ct_07", "ct_11", "ct_12", "vet_21"),
}


def applies(item, roof_type):
    """Whether a criterion belongs on this roof, per its own authored types.

    A criterion with no types declared applies everywhere, which is the safe
    default: asking a question that does not apply is better than hiding one
    that does.
    """
    types = item.get("types")
    return not types or roof_type in types


def is_featured(item, roof_type):
    """Featured as generated, plus this roof's own lead questions."""
    return bool(item.get("featured")) or \
        item["key"] in FEATURED_EXTRA.get(roof_type, ())
