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

# How much weight a line carries. 32 of the 104 come from the contractor's own
# education packet; presenting that as neutral authority would be dishonest, so
# the provenance travels with the criterion and the page shows it.
AUTHORITY = {
    "manufacturer_original": "manufacturer",
    "manufacturer_linked_synthesis": "manufacturer-linked",
    "mixed_manufacturer_original_and_KLR_explanation": "manufacturer-linked",
    "user_provided_contractor_education": "contractor-education",
    "user_provided_marketing_template": "contractor-education",
}

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
            items.append({
                "key": key,
                "label": (c.get("label") or "").strip(),
                "ask": (c.get("neutral_public_question") or c.get("question") or "").strip(),
                "why": why,
                "warn": (c.get("warning_if_any") or "").strip(),
                "evidence": [e.strip() for e in (c.get("evidence_to_request") or []) if e.strip()],
                "types": tuple(sorted(rts)) or ("tile", "shingle", "metal", "flat"),
                "authority": AUTHORITY.get(c.get("authority_type"), "contractor-education"),
                "source": "%s p.%s" % (
                    os.path.splitext(os.path.basename(c.get("source_file") or ""))[0],
                    ",".join(str(p) for p in (c.get("source_pages") or [])) or "?"),
                "featured": bool(c.get("featured")),
                "id": c["id"],
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
scratchpad/build_criteria.py. {n} criteria, every one carrying the source file
and page it came from.

Each line also carries `authority`:
  manufacturer        - from a manufacturer's own document
  manufacturer-linked - synthesised from manufacturer documents
  contractor-education - from a contractor's own homeowner-education material

That distinction is load-bearing. A third of these lines come from a
contractor's own packet, and a homeowner comparing bids must be able to see
which questions come from a manufacturer and which come from someone who sells
roofs. The page shows it; do not flatten it away.
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
                      "authority", "source", "featured", "id"):
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
