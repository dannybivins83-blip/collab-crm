# -*- coding: utf-8 -*-
"""One-time import of the SeaBreeze company documents into the CRM Document Library.
Copies files into uploads/library/ and inserts categorized + AHJ/system-tagged rows.
Idempotent: skips files already imported (by original name)."""
import os
import shutil
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import config  # noqa
import db  # noqa

SRC = r"C:\Users\kjburnz\Downloads\_seabreeze_docs"
LIB_DIR = os.path.join(config.UPLOAD_DIR, "library")
os.makedirs(LIB_DIR, exist_ok=True)


def categorize(n):
    n = n.lower()
    def has(*ks):
        return any(k in n for k in ks)
    if has("_btr_", "_gl_", "_wc_", "workers_comp", "wc_1.", "gl_2.", "_coi", "coi_", "auto_coi",
           "certificate", "certified_cert", "license", "w9", "gl_cert", "wc_cert", "incorporated", "references"):
        return "Licenses & Insurance"
    if has("warranty", "wty"):
        return "Warranties"
    if has("msfh", "my-safe-florida", "renewpace", "authorized-improvements", "grant-invoice",
           "contractor-manual", "contractor-outreach", "residential_handbook", "florida_handbook"):
        return "My Safe Florida Home"
    if has("hoa"):
        return "HOA Forms"
    if has("noc"):
        return "NOC Forms"
    if has("permit", "building_permit_application", "panel_specification", "wind_mitigation", "interior_insp_waiver"):
        return "Permit Packages"
    if has("sign_up", "sign-up", "signup", "terms_and_conditions", "homeowners_ack", "owners_ack",
           "owners_acknoleg", "special_notes", "roof_covering_confirmation", "notarized_closing", "confirmation"):
        return "Sign-Up Packages"
    if has("order_form", "cheat", "quantity", "wood_pricing", "price_list", "pricing", "spreadsheet"):
        return "Order Forms & Cheat Sheets"
    if has("release_of_lien", "_lien", "lien_", "final_payment_affidavit", "waiver", "letterhead",
           "employee_change", "job_application", "new_hire", "spli", "damage_release"):
        return "Lien & Legal Forms"
    if has("brochure", "color", "palette", "display", "catalog", "beauty_book", "polystick", "dura_guard",
           "timberline", "eagle", "dynamic", "westlake", "saxony", "barcelona", "crown", "englert",
           "landmark", "maxim", "solatube", "skylight", "reflectivity", "panel_stickers", "foam_pa",
           "efflorescence", "noa", "tu_plus", "ir-xe", "shingles", "not_made_in_china", "title"):
        return "Product & Color Charts"
    if has("proposal", "quote", "estimate", "_dd", "_sa", "thomas-estimate"):
        return "Sample Proposals"
    if has("inspection_report", "inspection"):
        return "Inspection Reports"
    return "Company / Misc"


AHJ_MAP = [
    ("royal_palm_beach", "Royal_Palm_Beach"), ("north_palm_beach", "North_Palm_Beach"),
    ("palm_beach_county", "Palm Beach County"), ("lake_worth", "Lake_Worth_Beach"),
    ("deerfield", "Deerfield_Beach"), ("riviera", "Riviera_Beach"), ("boca", "Boca_Raton"),
    ("delray", "Delray_Beach"), ("hypoluxo", "Hypoluxo"), ("haverhill", "Haverhill"),
    ("loxahatchee", "Loxahatchee_Groves"), ("lox_groves", "Loxahatchee_Groves"),
    ("broward", "Broward County"), ("martin", "Martin County"), ("okeechobee", "Okeechobee"),
    ("pompano", "Pompano_Beach"), ("pbc", "Palm Beach County"),
]


def tag_ahj(n):
    n = n.lower()
    for kw, label in AHJ_MAP:
        if kw in n:
            return label
    return ""


def tag_system(n):
    n = n.lower()
    out = []
    for s in ("shingle", "tile", "metal", "flat"):
        if s in n:
            out.append(s)
    return ",".join(out)


def main():
    db.init_db()  # ensure schema (incl. library_docs) exists
    existing = {r["original_name"] for r in db.all_rows("library_docs")}
    added = 0
    for fn in sorted(os.listdir(SRC)):
        src = os.path.join(SRC, fn)
        if not os.path.isfile(src) or fn in existing:
            continue
        shutil.copy2(src, os.path.join(LIB_DIR, fn))
        db.insert("library_docs", {
            "created": db.now(), "filename": fn, "original_name": fn,
            "category": categorize(fn), "ahj": tag_ahj(fn), "system": tag_system(fn),
            "size": os.path.getsize(src), "tags": "", "notes": ""})
        added += 1
    print("imported", added, "files; total in library:", len(db.all_rows("library_docs")))
    from collections import Counter
    cc = Counter(r["category"] for r in db.all_rows("library_docs"))
    for cat, n in sorted(cc.items()):
        print("  %-28s %d" % (cat, n))


if __name__ == "__main__":
    main()
