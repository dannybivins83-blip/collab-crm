#!/usr/bin/env python3
"""Push AccuLynx CSV exports to the live Render CRM via the admin import endpoints.

Usage:
    python scripts/push_acculynx_reports.py

Reads DB_RESTORE_TOKEN from secrets/keys.local.env (or env).
Uploads both CSVs in one pass and prints the result.
"""
import os
import sys
import requests

BASE = "https://crm.collaborativeconceptsfl.com"

REPORTS_DIR = r"C:\Users\kjburnz\Downloads\AccuLynx_Reports_ZIP"

FILES = {
    "import-job-expenses":   "Job Expenses Report-6_12_2026_18_6_34.csv",
    "import-workflow-status": "Workflow Status Report-6_12_2026_18_6_7.csv",
}


def _load_token():
    """Read DB_RESTORE_TOKEN from env or keys.local.env."""
    tok = os.environ.get("DB_RESTORE_TOKEN", "").strip()
    if tok:
        return tok
    kf = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "secrets", "keys.local.env")
    if os.path.exists(kf):
        for line in open(kf):
            line = line.strip()
            if line.startswith("DB_RESTORE_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def main():
    token = _load_token()
    if not token:
        print("ERROR: DB_RESTORE_TOKEN not found in env or secrets/keys.local.env")
        sys.exit(1)

    headers = {"X-Restore-Token": token}

    for endpoint, filename in FILES.items():
        path = os.path.join(REPORTS_DIR, filename)
        if not os.path.exists(path):
            print(f"SKIP {endpoint}: file not found at {path}")
            continue
        url = f"{BASE}/admin/{endpoint}"
        print(f"Uploading {filename} -> {url} ...", end=" ", flush=True)
        with open(path, "rb") as f:
            resp = requests.post(url, headers=headers, files={"file": (filename, f, "text/csv")})
        if resp.status_code == 200:
            data = resp.json()
            print(f"OK  imported={data.get('imported')}  unmatched={data.get('unmatched')}")
        else:
            print(f"FAIL  status={resp.status_code}  body={resp.text[:200]}")


if __name__ == "__main__":
    main()
