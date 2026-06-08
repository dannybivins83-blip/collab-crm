# -*- coding: utf-8 -*-
"""Load DATABASE_URL from .env.production (BOM-safe) and run the Neon migration."""
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
env_path = os.path.join(HERE, ".env.production")

url = None
for line in open(env_path, encoding="utf-8-sig"):
    line = line.strip()
    if line.startswith("DATABASE_URL="):
        import re
        url = line.split("=", 1)[1].strip().strip('"').strip("'")
        url = url.replace("\\r", "").replace("\\n", "").replace("\\t", "")  # literal escapes
        url = re.sub(r"\s", "", url).replace("﻿", "")  # real whitespace + BOM
        break

if not url or not url.startswith("postgres"):
    sys.exit("Could not read a Postgres DATABASE_URL from .env.production")

os.environ["DATABASE_URL"] = url
os.environ["CRM_NOBROWSER"] = "1"
print("Migrating into:", url.split("@")[-1].split("?")[0])

# Run the migration in this process (it reads os.environ at import time).
os.chdir(HERE)
exec(open(os.path.join(HERE, "migrate_to_neon.py"), encoding="utf-8").read())
