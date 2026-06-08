# -*- coding: utf-8 -*-
"""Reset the AccuLynx sync cursor in the live (Neon) DB so the next sync starts
at the first milestone group (leads)."""
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

url = None
for line in open(os.path.join(HERE, ".env.production"), encoding="utf-8-sig"):
    line = line.strip()
    if line.startswith("DATABASE_URL="):
        url = line.split("=", 1)[1].strip().strip('"').strip("'")
        url = url.replace("\\r", "").replace("\\n", "").replace("\\t", "")
        url = re.sub(r"\s", "", url).replace("﻿", "")
        break
os.environ["DATABASE_URL"] = url
os.environ["CRM_NOBROWSER"] = "1"

import db                       # connects to Neon
from modules import acculynx_sync  # noqa: F401  (runs _ensure_schema -> adds cursor cols)
db.save_company({"acculynx_group": 0, "acculynx_cursor": 0})
co = db.get_company()
print("cursor reset -> group=%s start=%s" % (co.get("acculynx_group"), co.get("acculynx_cursor")))
