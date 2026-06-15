#!/usr/bin/env python3
"""Import AccuLynx Job Expenses Report CSV into CRM job_expenses table.

Usage:
    python scripts/import_job_expenses.py <path-to-csv>
    python scripts/import_job_expenses.py "C:/Users/kjburnz/Downloads/AccuLynx_Reports_ZIP/Job Expenses Report-6_12_2026_18_6_34.csv"

Maps each row to a job_id by matching the AccuLynx job name against jobs.name.
Rows with no matching CRM job are counted as unmatched (not dropped — they're
stored with job_id=None so they can be reconciled later).
"""
import csv
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "")
os.environ.setdefault("CRM_NOBROWSER", "1")

import db

db.init_db()


def parse_amount(val):
    try:
        return float(str(val).replace(",", "").replace("$", "").strip() or 0)
    except ValueError:
        return 0.0


def _job_map():
    """name (AccuLynx-style) → CRM job id.  Pre-loads all jobs once."""
    jobs = db.all_rows("jobs")
    mapping = {}
    for j in jobs:
        n = (j.get("name") or "").strip()
        if n:
            mapping[n] = j["id"]
    return mapping


def main(csv_path):
    job_map = _job_map()

    db.execute("DELETE FROM job_expenses")

    added = 0
    unmatched = 0
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            job_name = (row.get("Job Name") or "").strip()
            job_id = job_map.get(job_name)
            if not job_id:
                unmatched += 1

            db.insert("job_expenses", {
                "job_id": job_id,
                "acculynx_job_name": job_name,
                "payment_date": (row.get("Payment Date") or "").strip(),
                "payment_type": (row.get("Payment Type") or "").strip(),
                "amount": parse_amount(row.get("Payment Amount")),
                "to_method": (row.get("To/Method") or "").strip(),
                "check_ref": (row.get("Check Number/Reference") or "").strip(),
                "memo": (row.get("Memo/Notes") or "").strip(),
                "job_value": parse_amount(row.get("Job Value")),
                "balance_due": parse_amount(row.get("Balance Due")),
                "account_type": (row.get("Account Type") or "").strip(),
                "paid_in_full": (row.get("Paid in Full") or "").strip(),
                "rep": (row.get("Company Representative") or "").strip(),
            })
            added += 1

    print(f"Imported {added} expense rows ({unmatched} unmatched to a CRM job)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/import_job_expenses.py <path-to-csv>")
        sys.exit(1)
    main(sys.argv[1])
