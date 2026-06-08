# -*- coding: utf-8 -*-
"""Update the payment schedule to 30/30/30/10 in existing DB rows (signup_templates
payment clause + company terms). Run once for local, once with DATABASE_URL for Neon."""
import os
import re
import sys
import json

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

if "--live" in sys.argv:
    for line in open(os.path.join(HERE, ".env.production"), encoding="utf-8-sig"):
        line = line.strip()
        if line.startswith("DATABASE_URL="):
            u = line.split("=", 1)[1].strip().strip('"').strip("'")
            os.environ["DATABASE_URL"] = re.sub(r"\s", "", u.replace("\\r", "").replace("\\n", "")).replace("﻿", "")
            break
os.environ["CRM_NOBROWSER"] = "1"
import db

NEW_BODY = ("I agree to the payment schedule: 30% deposit (permit, material order & admin), "
            "30% at job start (mobilization), 30% at 2 of 3 inspections passed, and 10% at final "
            "inspection / completion. Total contract: {value}.")

target = "Neon (live)" if "--live" in sys.argv else "local SQLite"
print("Updating:", target)

n = 0
for t in db.all_rows("signup_templates"):
    items = db.load_json(t.get("items"), [])
    hit = False
    for it in items:
        if it.get("key") == "payment":
            it["body"] = NEW_BODY
            hit = True
    if hit:
        db.update("signup_templates", t["id"], items=json.dumps(items))
        n += 1
print("  signup_templates updated:", n)

co = db.get_company()
terms = co.get("terms") or ""
if "25%" in terms or "40%" in terms:
    new_terms = re.sub(r"25% deposit.*?completion\.?",
                       "30% deposit, 30% at job start, 30% at 2 of 3 inspections passed, 10% at completion.",
                       terms)
    db.save_company({"terms": new_terms})
    print("  company terms updated.")
else:
    print("  company terms: no 25/40 text found (left as-is).")
