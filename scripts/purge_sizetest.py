# -*- coding: utf-8 -*-
"""One-off: remove the 'SizeTest' junk documents created while probing the
large-upload threshold for /sync/doc-import. Targets category='SizeTest' only."""
import os
import re
import sys

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

rows = [d for d in db.all_rows("documents") if (d.get("category") or "") == "SizeTest"]
print("Found %d SizeTest junk docs" % len(rows))
for d in rows:
    print("  deleting id=%s name=%s" % (d["id"], d.get("original_name")))
    db.delete("documents", d["id"])
print("Done.")
