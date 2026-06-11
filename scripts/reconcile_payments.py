#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Read-only payments/invoices -> jobs.collected reconciliation REPORT.

Verification tool (NOT a mutator — appconnect owns the live rollup fix in acculynx_sync.py).
Shows where stored job financials diverge from what the linked payments/invoices imply, so we
can tell at a glance whether the rollup is correct after the collectors run.

    python scripts/reconcile_payments.py [--db data/crm_migration.db]
"""
import argparse, re, sqlite3, sys, os


def money(s):
    if s is None:
        return 0.0
    s = str(s)
    neg = s.strip().startswith("(") or s.strip().startswith("-")
    v = re.sub(r"[^0-9.]", "", s)
    try:
        f = float(v) if v and v.count(".") < 2 else 0.0
    except ValueError:
        f = 0.0
    return -f if neg else f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/crm_migration.db")
    a = ap.parse_args()
    if not os.path.exists(a.db):
        sys.exit("DB not found: %s" % a.db)
    c = sqlite3.connect(a.db)
    cur = c.cursor()

    # recompute collected per job from linked payments
    pay_by_job = {}
    for jid, amt in cur.execute("select job_id, amount from payments where job_id is not null"):
        pay_by_job[jid] = pay_by_job.get(jid, 0.0) + money(amt)
    inv_by_job = {}
    paid_inv = 0
    inv_total = 0.0
    for jid, amt, status, pd in cur.execute("select job_id, amount, status, paid_date from invoices"):
        inv_total += money(amt)
        if (status or "").lower() == "paid" or (pd or "").strip():
            paid_inv += 1
        if jid is not None:
            inv_by_job[jid] = inv_by_job.get(jid, 0.0) + money(amt)

    stored_collected = 0.0
    recomputed_collected = sum(pay_by_job.values())
    mismatch = 0
    for jid, co in cur.execute("select id, collected from jobs"):
        s = money(co)
        stored_collected += s
        r = pay_by_job.get(jid, 0.0)
        if abs(s - r) > 0.5:
            mismatch += 1
    njobs = cur.execute("select count(*) from jobs").fetchone()[0]
    c.close()

    print("PAYMENTS -> jobs.collected RECONCILIATION  (db=%s)" % a.db)
    print("=" * 60)
    print("  jobs:                          %d" % njobs)
    print("  jobs w/ linked payments:       %d" % len(pay_by_job))
    print("  stored SUM jobs.collected:     $%s" % format(stored_collected, ",.2f"))
    print("  recomputed from payments:      $%s" % format(recomputed_collected, ",.2f"))
    print("  jobs where stored != linked:   %d" % mismatch)
    print("  invoices total:                $%s" % format(inv_total, ",.2f"))
    print("  invoices marked paid:          %d  (payment records exist: %d)" % (
        paid_inv, sum(1 for v in pay_by_job.values() if v)))
    print("=" * 60)
    gap = abs(stored_collected - recomputed_collected)
    if gap > 0.5:
        print("RESULT: rollup INCOMPLETE - $%s of payments not reflected in jobs.collected." % format(gap, ",.2f"))
        print("        -> appconnect's billing-import rollup (or a one-time backfill) must run.")
    else:
        print("RESULT: collected rollup matches linked payments. OK.")


if __name__ == "__main__":
    main()
