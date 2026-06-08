# -*- coding: utf-8 -*-
"""Diagnostic: read the live API key from Neon and call AccuLynx /jobs with our
exact params, printing HTTP status + response shape so we can see what's failing."""
import os, re, sys, json, ssl, urllib.request, urllib.parse

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
for line in open(os.path.join(HERE, ".env.production"), encoding="utf-8-sig"):
    line = line.strip()
    if line.startswith("DATABASE_URL="):
        u = line.split("=", 1)[1].strip().strip('"').strip("'")
        os.environ["DATABASE_URL"] = re.sub(r"\s", "", u.replace("\\r", "").replace("\\n", "")).replace("﻿", "")
        break
os.environ["CRM_NOBROWSER"] = "1"
import db

co = db.get_company()
key = (co.get("acculynx_api_key") or "").strip()
base = (co.get("acculynx_api_base") or "https://api.acculynx.com/api/v2").strip()
print("key set:", bool(key), "| len:", len(key), "| base:", base)
print("cursor group:", co.get("acculynx_group"), "start:", co.get("acculynx_cursor"))

try:
    import certifi
    ctx = ssl.create_default_context(cafile=certifi.where())
except Exception:
    ctx = ssl.create_default_context()

for params in [
    {"milestones": "prospect", "recordStartIndex": 0, "pageSize": 25, "sortBy": "MilestoneDate", "sortOrder": "Descending"},
    {"milestones": "approved", "recordStartIndex": 0, "pageSize": 25},
    {"recordStartIndex": 0, "pageSize": 25},
]:
    url = base.rstrip("/") + "/jobs?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + key, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
            d = json.loads(r.read().decode())
        items = d.get("items") if isinstance(d, dict) else d
        print("OK", params.get("milestones", "(none)"), "-> HTTP", r.status, "| items:", len(items or []),
              "| totalCount:", (d.get("totalCount") if isinstance(d, dict) else "?"))
        if items:
            j = items[0]
            print("   sample keys:", list(j.keys())[:12])
    except urllib.error.HTTPError as e:
        print("HTTPError", params.get("milestones", "(none)"), "->", e.code, e.read().decode()[:200])
    except Exception as e:
        print("ERROR", params.get("milestones", "(none)"), "->", type(e).__name__, str(e)[:200])
