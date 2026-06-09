# -*- coding: utf-8 -*-
import os, re, sys, json
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
for line in open(os.path.join(HERE, ".env.production"), encoding="utf-8-sig"):
    line = line.strip()
    if line.startswith("DATABASE_URL="):
        u = line.split("=", 1)[1].strip().strip('"').strip("'")
        os.environ["DATABASE_URL"] = re.sub(r"\s", "", u.replace("\r","").replace("\n","")).replace("﻿","")
        break
os.environ["CRM_NOBROWSER"] = "1"
import db

def guid(u):
    m = re.search(r"/jobs/([0-9a-f-]{30,})", u or "")
    return m.group(1) if m else None

conn = db.connect()
try:
    rows = conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'").fetchall()
    tabs = [list(dict(r).values())[0] for r in rows]
    print("worksheet tables:", [t for t in tabs if "worksheet" in t])
    for t in ("worksheets","worksheet_lines"):
        if t in tabs:
            cs = conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name=?", (t,)).fetchall()
            print(f"  {t} cols:", [list(dict(c).values())[0] for c in cs])
        else:
            print(f"  {t}: MISSING")
finally:
    conn.close()

jobs = db.all_rows("jobs")
guids = []
for j in jobs:
    g = guid(j.get("external_url"))
    if g:
        guids.append({"id": j["id"], "guid": g, "name": j.get("name"), "stage": j.get("stage"), "cv": j.get("contract_value")})
from collections import Counter
print(f"\n{len(guids)} jobs with GUID (of {len(jobs)} total)")
print(Counter(g["stage"] for g in guids))
json.dump(guids, open(os.path.join(HERE, "scripts", "_ws_guids.json"), "w"), indent=0)
print("wrote scripts/_ws_guids.json")
