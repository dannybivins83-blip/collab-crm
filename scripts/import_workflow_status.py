#!/usr/bin/env python3
"""Import AccuLynx Workflow Status Report CSV into CRM job_stage_history table.

Usage:
    python scripts/import_workflow_status.py <path-to-csv>
    python scripts/import_workflow_status.py "C:/Users/kjburnz/Downloads/AccuLynx_Reports_ZIP/Workflow Status Report-6_12_2026_18_6_7.csv"
"""
import csv
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "")
os.environ.setdefault("CRM_NOBROWSER", "1")

import db

db.init_db()


def _job_map():
    jobs = db.all_rows("jobs")
    mapping = {}
    for j in jobs:
        n = (j.get("name") or "").strip()
        if n:
            mapping[n] = j["id"]
    return mapping


def _parse_int(val):
    try:
        return int(str(val).strip())
    except (ValueError, TypeError):
        return 0


def main(csv_path):
    job_map = _job_map()

    db.execute("DELETE FROM job_stage_history")

    added = 0
    unmatched = 0
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            job_name = (row.get("Job Name") or "").strip()
            job_id = job_map.get(job_name)
            if not job_id:
                unmatched += 1

            db.insert("job_stage_history", {
                "job_id": job_id,
                "acculynx_job_name": job_name,
                "status_name": (row.get("Status Name") or "").strip(),
                "milestone": (row.get("Status Milestone") or "").strip(),
                "started_at": (row.get("Status Start Date") or "").strip(),
                "ended_at": (row.get("Status End Date") or "").strip(),
                "duration_days": _parse_int(row.get("Status Duration")),
                "set_by": (row.get("Set By") or "").strip(),
                "checklist_pct": (row.get("Checklist Percentage Completed") or "").strip(),
                "checklist_done": (row.get("Checklist Completed") or "").strip(),
            })
            added += 1

    print(f"Imported {added} stage-history rows ({unmatched} unmatched to a CRM job)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/import_workflow_status.py <path-to-csv>")
        sys.exit(1)
    main(sys.argv[1])
