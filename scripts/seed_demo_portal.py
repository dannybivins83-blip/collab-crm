# -*- coding: utf-8 -*-
"""Seed a branded, shareable DEMO homeowner portal for roofportal.com.

Idempotent: creates (or reuses) a `demos` row with a stable slug so the public,
login-free demo lives at  /demo/<slug>  on the running CRM. Safe to run on the
live Render service — it only writes ONE demos row and never touches real
jobs/leads/portal tokens.

Usage (on the box the CRM runs on):
    python scripts/seed_demo_portal.py
    python scripts/seed_demo_portal.py --name "Roof Portal" --slug roof-portal --system shingle
Prints the demo path (e.g. /demo/roof-portal). Compose with the CRM base URL.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db

SLUG = "roof-portal"
NAME = "KLR Roofing"
TAGLINE = "Roofs done right — on time, every time."
SYSTEM = "shingle"
# Coastal, 4-color palette (matches the flat portal theme).
MASTHEAD = "#0B2B40"   # harbor navy
PRIMARY = "#1F8A9C"    # sea teal
ACCENT = "#E0A338"     # sand gold


def main(argv):
    name, slug, system = NAME, SLUG, SYSTEM
    it = iter(argv)
    for a in it:
        if a == "--name":
            name = next(it, NAME)
        elif a == "--slug":
            slug = next(it, SLUG)
        elif a == "--system":
            system = next(it, SYSTEM)

    # Ensure the demos table exists (module import also does this, but be safe).
    try:
        from modules import demos  # noqa: F401  (creates the table on import)
    except Exception:
        db.execute("""CREATE TABLE IF NOT EXISTS demos (
            id INTEGER PRIMARY KEY AUTOINCREMENT, created TEXT, slug TEXT,
            company_name TEXT, logo_url TEXT, tagline TEXT, phone TEXT, website TEXT,
            color_masthead TEXT, color_primary TEXT, color_accent TEXT,
            sample_system TEXT DEFAULT 'shingle', created_by TEXT)""")
        db._COLCACHE.clear()

    existing = db.all_rows("demos", "slug=?", (slug,))
    if existing:
        print("EXISTS /demo/%s (id=%s)" % (slug, existing[0]["id"]))
        return
    rid = db.insert("demos", {
        "created": db.now(), "slug": slug, "company_name": name,
        "logo_url": "", "tagline": TAGLINE, "phone": "(555) 018-2440",
        "website": "https://klrroofing.com",
        "color_masthead": MASTHEAD, "color_primary": PRIMARY, "color_accent": ACCENT,
        "sample_system": system, "created_by": "seed"})
    print("CREATED /demo/%s (id=%s)" % (slug, rid))


if __name__ == "__main__":
    main(sys.argv[1:])
