# -*- coding: utf-8 -*-
"""Back-fill structured fields on existing jobs by decoding the SeaBreeze job name
(e.g. 'R-25179: Richard Reis (PBC) (T28) (SCOTT)' -> ahj=Palm Beach County,
system=Tile, squares=28, rep=Scott). Uses _parse_job_name from the sync module.

Usage:
  python scripts/parse_job_names.py            # DRY RUN on local SQLite
  python scripts/parse_job_names.py --live     # DRY RUN against prod Neon
  python scripts/parse_job_names.py --live --apply   # execute on prod
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
LIVE = "--live" in sys.argv
APPLY = "--apply" in sys.argv
if LIVE:
    for line in open(os.path.join(HERE, ".env.production"), encoding="utf-8-sig"):
        line = line.strip()
        if line.startswith("DATABASE_URL="):
            os.environ["DATABASE_URL"] = re.sub(r"\s", "", line.split("=", 1)[1].strip().strip('"').strip("'"))
            break
os.environ["CRM_NOBROWSER"] = "1"
import db
from modules.acculynx_sync import _parse_job_name  # importing adds the `squares` column

print("Mode:", "APPLY (writing)" if APPLY else "DRY RUN (report only)",
      "| target:", "prod Neon" if LIVE else "local SQLite")
jobs = db.all_rows("jobs")
would = 0
samples = []
for j in jobs:
    p = _parse_job_name(j.get("name") or "")
    upd = {c: p[c] for c in ("system", "squares", "ahj", "rep") if p.get(c)}
    if not upd:
        continue
    would += 1
    if APPLY:
        db.update("jobs", j["id"], **upd)
    if len(samples) < 12:
        samples.append(((j.get("name") or "")[:48], upd))

print("%d jobs scanned | %s %d" % (len(jobs), "updated" if APPLY else "would update", would))
for nm, upd in samples:
    print("  %-48s -> %s" % (nm, upd))
if not APPLY:
    print("\n** DRY RUN — nothing written. Add --apply to execute. **")
