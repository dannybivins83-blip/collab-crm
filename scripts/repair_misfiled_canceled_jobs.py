# -*- coding: utf-8 -*-
"""REPORT (and, only with --apply, repair) dead AccuLynx LEADS misfiled into `jobs`.

Background
----------
An earlier version of `scripts/import_closed.py` hardcoded
    stage = "closed" if group == "closed" else "canceled"
with no kind resolution, so EVERY record AccuLynx returned in the 'cancelled' bucket
was written into the `jobs` table as a canceled job — including thousands of dead
LEADS that never became jobs. That inflated the jobs table and is why the jobs list
count and the milestone pipeline disagree.

`import_closed.py` is now fixed at the source. This script deals with the rows the old
version already wrote. Those rows are PRODUCTION DATA, so:

  * DRY RUN IS THE DEFAULT — it only reports.
  * Nothing is written unless you pass --apply.
  * Even with --apply, nothing is deleted: each candidate row is COPIED into `leads`
    (stage 'lost') and the original job row is marked so it can be audited/reversed.

Candidates are identified narrowly: jobs with stage='canceled' whose narrative carries
the old importer's signature "Backfilled from AccuLynx (canceled)." — nothing else is
touched. Rows that look like real work (a contract value, a linked estimate, permit,
measurement, invoice or document) are reported as SKIPPED, never moved.

Usage
-----
    DATABASE_URL="" CRM_NOBROWSER=1 python scripts/repair_misfiled_canceled_jobs.py
    DATABASE_URL="" CRM_NOBROWSER=1 python scripts/repair_misfiled_canceled_jobs.py --apply
    ... --limit 50        # cap how many rows are considered
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db  # noqa: E402

# The exact narrative the old importer stamped on every misfiled row.
NARRATIVE_SIGNATURE = "Backfilled from AccuLynx (canceled)."

# Tables that, if they reference a job, mean the job is real work — never move it.
_ATTACHMENT_TABLES = ("estimates", "permits", "measurements", "invoices", "documents")

# Columns carried over from the job row to the new lead row (leads has no city/state/zip).
_CARRY = ("contact_id", "rid", "name", "phone", "email", "address", "work_type",
          "rep", "source", "external_url", "department", "narrative")


def _has_attachments(job_id):
    for t in _ATTACHMENT_TABLES:
        try:
            rows = db.all_rows(t, where="job_id=?", params=(job_id,), limit=1)
        except Exception:
            continue          # table may not exist on a slim tenant schema
        if rows:
            return t
    return None


def _money(v):
    try:
        return float(str(v).replace("$", "").replace(",", "") or 0)
    except Exception:
        return 0.0


def find_candidates(limit=None):
    """Jobs that carry the old importer's canceled-backfill signature."""
    rows = db.all_rows("jobs", where="stage=? AND narrative=?",
                       params=("canceled", NARRATIVE_SIGNATURE),
                       order="id ASC", limit=limit)
    movable, skipped = [], []
    for r in rows:
        reason = None
        if _money(r.get("contract_value")) > 0:
            reason = "has contract_value %s" % r.get("contract_value")
        else:
            att = _has_attachments(r["id"])
            if att:
                reason = "has linked %s" % att
        (skipped if reason else movable).append((r, reason))
    return movable, skipped


def repair(apply=False, limit=None):
    movable, skipped = find_candidates(limit=limit)
    total_jobs = len(db.all_rows("jobs", order="id ASC"))
    moved = 0
    if apply:
        for r, _ in movable:
            lead = {k: r.get(k) for k in _CARRY if r.get(k) is not None}
            lead["stage"] = "lost"
            lead["stage_since"] = r.get("stage_since") or db.today()
            lead["narrative"] = ("Backfilled from AccuLynx (canceled). "
                                 "Moved out of jobs by repair_misfiled_canceled_jobs "
                                 "(was job #%s)." % r["id"])
            lid = db.insert("leads", lead)
            # Nothing is deleted: the job row is retired + annotated so this is auditable
            # and reversible. Purging the stubs is a separate, explicit owner decision.
            db.update("jobs", r["id"], stage="canceled",
                      narrative="Misfiled dead lead — moved to lead #%s by repair script." % lid)
            moved += 1
    return {"apply": apply, "jobs_total": total_jobs, "candidates": len(movable),
            "skipped": len(skipped), "moved": moved,
            "movable_rows": movable, "skipped_rows": skipped}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="actually perform the move (default is a dry run that only reports)")
    ap.add_argument("--limit", type=int, default=None, help="cap rows considered")
    ap.add_argument("--show", type=int, default=15, help="how many sample rows to print")
    args = ap.parse_args(argv)

    res = repair(apply=args.apply, limit=args.limit)
    mode = "APPLY" if args.apply else "DRY RUN (no changes written)"
    print("=" * 72)
    print("repair_misfiled_canceled_jobs — %s" % mode)
    print("=" * 72)
    print("jobs table rows            : %d" % res["jobs_total"])
    print("matching old-importer sig  : %d" % (res["candidates"] + res["skipped"]))
    print("  would move jobs -> leads : %d" % res["candidates"])
    print("  skipped (looks real)     : %d" % res["skipped"])
    if args.apply:
        print("  MOVED                    : %d" % res["moved"])
    print()
    if res["movable_rows"]:
        print("Sample of rows that would move:")
        for r, _ in res["movable_rows"][:args.show]:
            print("  job #%-6s %-42s %s" % (r["id"], (r.get("name") or "")[:42],
                                            r.get("external_url") or ""))
        if len(res["movable_rows"]) > args.show:
            print("  ... and %d more" % (len(res["movable_rows"]) - args.show))
        print()
    if res["skipped_rows"]:
        print("Sample of rows SKIPPED (kept as jobs):")
        for r, why in res["skipped_rows"][:args.show]:
            print("  job #%-6s %-42s %s" % (r["id"], (r.get("name") or "")[:42], why))
        print()
    if not args.apply:
        print("Nothing was written. Re-run with --apply to perform the move.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
