"""Convert the developer handoff's comparison_rules.json into the live SPEC.

Run once; it writes modules/compare_criteria.py. Re-run it if the handoff data
is revised — never hand-edit the generated file.
"""
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
SRC, OUT = sys.argv[1], sys.argv[2]

# 38 source categories is right for a rules file and wrong for a form, so they
# roll up into the seven groups a homeowner actually thinks in. Order is the
# order of the job: who they are, then the roof from the deck up, then money.
GROUPS = [
    ("The company and the paperwork", [
        "business_identity", "insurance_and_license", "installer_qualifications",
        "crew_and_supervision", "supplier_and_references", "documentation",
        "permit_and_inspection", "performance_evidence"]),
    ("Tear-off, deck and substrate", [
        "tear_off", "reroofing", "deck_and_substrate", "deck_preparation", "primer"]),
    ("The layers that keep water out", [
        "waterproofing_details", "slope_and_underlayment", "moisture_and_ventilation",
        "attic_and_moisture", "material_compatibility"]),
    ("How the roof is fastened and assembled", [
        "fastening", "attachment", "materials_and_assembly", "installation_details",
        "installation_quality", "system_identification", "system_accessories",
        "roof_attachments"]),
    ("Metal systems", ["metal_system"]),
    ("Warranty", [
        "warranty", "warranty_and_service", "wind_warranty", "warranty_closeout",
        "maintenance"]),
    ("Money, schedule and the day itself", [
        "price_and_inclusions", "payments_and_releases", "change_orders",
        "schedule_and_exposure", "schedule_and_protection", "site_quality"]),
]

# A handful of caution lines were written to compare one source document's pages
# against another's. The site does not show documents, so those sentences would
# point at nothing. Rewritten to say the thing that actually matters to a
# homeowner, with no reference to any packet or page.
WARN_REWRITE = {
    "PG-21": ("Published laboratory figures are not a statement about how this roof "
              "was installed. A product passing a test says nothing about a torn "
              "membrane, a missed nail or an open seam on your house."),
    "CT-26": ("An impact rating belongs to the shingle, not to your roof or your "
              "policy. No rating guarantees an insurance discount — ask your "
              "insurer directly."),
    "PG-20": ("Product literature is not an inspection report. It describes how the "
              "material should behave, not what happened on your deck."),
}

# Any remaining sentence that cites a page, a packet or a brand of document is
# scaffolding from the source material, not something a homeowner needs.
_DOC_REF = re.compile(
    r"(?:^|(?<=[.;]))[^.;]*\b(?:p\.?\s?\d|pp\.?\s?\d|page\s+\d|packet|KLR)\b[^.;]*[.;]?",
    re.I)


# comparison_rule is addressed to the comparison ENGINE ("Record the seller...",
# "Compare documented active qualification...", "Split each cost into its own
# comparison field"). That is machine direction, not homeowner guidance, and on
# the page it reads like leaked internals. A line keeps its `why` only if it
# explains something to the reader.
# This started as six verbs and missed most of the field. "Mark absent documents
# Not provided, mismatches Needs clarification..." sailed through and rendered
# as if it were advice to a homeowner, on the front page and in the list of
# disagreements. The two filters share one vocabulary now so a sentence aimed at
# the engine cannot reach a person through either field.
_ENGINE_VERBS = (
    r"record|compare|split|flag|treat|preserve|capture|list|map|store|tag|"
    r"normali[sz]e|score|rank|mark|show|apply|use|keep|assign|select|prefer|"
    r"distinguish|separate|convert|rewrite|substitute|infer|confuse|penali[sz]e|"
    r"do not|don't|never|avoid|require|report|return|output|set|add|omit|include")
_ENGINE_VOICE = re.compile(r"^(" + _ENGINE_VERBS + r")\b", re.I)


# The warning field carries the same problem as comparison_rule: a good half of
# it is direction aimed at the comparison engine ("Preserve the short user
# wording", "Do not penalize a qualified business..."). Rendered in red beside
# a homeowner's question it reads as a warning about their roof, which it is
# not. These openers are instructions, not cautions.
_ENGINE_WARN = re.compile(r"^(" + _ENGINE_VERBS + r")\b", re.I)


def caution_only(text):
    """Keep the sentences that warn a person; drop the ones that direct the tool."""
    if not text:
        return ""
    keep = [x.strip() for x in re.split(r"(?<=[.;])\s+", text)
            if x.strip() and not _ENGINE_WARN.match(x.strip())]
    out = " ".join(keep).strip()
    return out if len(out) > 25 else ""


def homeowner_voice(text):
    """Drop sentences written at the tool; keep sentences written at a person."""
    if not text:
        return ""
    keep = [s.strip() for s in re.split(r"(?<=[.;])\s+", text)
            if s.strip() and not _ENGINE_VOICE.match(s.strip())]
    out = " ".join(keep).strip()
    return out if len(out) > 25 else ""


def clean(text):
    """Strip document scaffolding from a sentence and tidy what is left."""
    if not text:
        return ""
    out = _DOC_REF.sub(" ", text)
    out = re.sub(r"\s+", " ", out).strip(" ;,")
    if out and not out.endswith("."):
        out += "."
    return "" if len(out) < 12 else out

# The source packets are written around the two manufacturers that contractor
# sells. A homeowner comparing three quotes may be looking at three different
# brands, so a brand-locked question is useless to them and quietly steers
# toward one supplier. The QUESTION is right; the brand lock is not.
BRANDS = [
    (r"\bWhich exact Polyglass\b", "Which exact"),
    (r"\bExact Polyglass warranty\b", "Exact manufacturer warranty"),
    (r"\bWhich Polyglass warranty\b", "Which manufacturer warranty"),
    (r"\bas Polyglass requires\b", "as though the manufacturer requires"),
    (r"\bor require a CertainTeed brand\b", "or require one particular brand"),
    (r"\bor CertainTeed alone\b", "or a brand name alone"),
    (r"\bUnlimited-wind Integrity Roof System components\b",
     "Enhanced wind-warranty system components"),
    (r"\bIntegrity Roof System\b", "enhanced wind-warranty system"),
    (r"\bPolystick\b", "the self-adhered underlayment"),
    (r"\bPolyanchor\b", "the anchor sheet"),
    (r"\bPolyglass\b", "the manufacturer"),
    (r"\bCertainTeed\b", "the manufacturer"),
]


def debrand(text):
    if not text:
        return text
    for pat, rep in BRANDS:
        text = re.sub(pat, rep, text)
    return re.sub(r"\s+", " ", text).strip()


def clean_label(v):
    return re.sub(r"\s+", " ", (v or "")).strip()


crit = json.load(open(SRC, encoding="utf-8"))["criteria"]
by_cat = {}
for c in crit:
    by_cat.setdefault(c["category"], []).append(c)

placed, groups_out = set(), []
for name, cats in GROUPS:
    items, types = [], set()
    for cat in cats:
        for c in by_cat.get(cat, []):
            placed.add(c["id"])
            key = re.sub(r"[^a-z0-9]+", "_", c["id"].lower()).strip("_")
            rts = [t.lower() for t in (c.get("roof_types") or [])]
            types.update(rts)
            why = (c.get("comparison_rule") or "").strip()
            warn = WARN_REWRITE.get(c["id"])
            if warn is None:
                warn = clean(c.get("warning_if_any"))
            items.append({
                "key": key,
                "label": debrand(clean_label(c.get("label"))),
                "ask": debrand((c.get("neutral_public_question") or c.get("question") or "").strip()),
                "why": homeowner_voice(debrand(clean(why))),
                "warn": caution_only(debrand(warn)),
                "evidence": [debrand(e.strip()) for e in (c.get("evidence_to_request") or []) if e.strip()],
                "types": tuple(sorted(rts)) or ("tile", "shingle", "metal", "flat"),
                "featured": bool(c.get("featured")),
            })
    if items:
        # A group applies to a roof if ANY of its lines do.
        groups_out.append({"name": name,
                           "types": tuple(sorted(types)) or ("tile", "shingle", "metal", "flat"),
                           "items": items})

missing = [c["id"] for c in crit if c["id"] not in placed]
if missing:
    print("!! %d criteria fell outside the group map: %s" % (len(missing), missing[:10]))
    sys.exit(1)

total = sum(len(g["items"]) for g in groups_out)
assert total == len(crit), "lost criteria: %d of %d" % (total, len(crit))

header = '''# -*- coding: utf-8 -*-
"""Comparison criteria — GENERATED, do not hand-edit.

Built from the MyRoofPortal developer handoff (2026-11-18) by
docs/handoff/build_criteria.py from the developer handoff's rule set.
{n} criteria.

The site uses the KNOWLEDGE from those sources and does not display them: no
file names, no page numbers, no contractor or packet names reach the screen.
Source and authority fields are therefore absent from this data entirely rather
than merely unrendered — a field that exists is one that gets rendered by
accident eventually. The provenance lives in docs/handoff/ for whoever
maintains this.
"""

SPEC = {spec}
'''


def fmt(groups):
    out = ["["]
    for g in groups:
        out.append('    dict(name=%r, types=%r, items=[' % (g["name"], g["types"]))
        for i in g["items"]:
            out.append("        dict(")
            for k in ("key", "label", "ask", "why", "warn", "evidence", "types",
                      "featured"):
                out.append("            %s=%r," % (k, i[k]))
            out.append("        ),")
        out.append("    ]),")
    out.append("]")
    return "\n".join(out)


with open(OUT, "w", encoding="utf-8", newline="") as fh:
    fh.write(header.format(n=len(crit), spec=fmt(groups_out)))

print("wrote %s" % OUT)
print("groups: %d, criteria: %d" % (len(groups_out), total))
for g in groups_out:
    f = sum(1 for i in g["items"] if i["featured"])
    print("  %-42s %3d items (%d featured)  types=%s" % (g["name"], len(g["items"]), f, ",".join(g["types"])))
