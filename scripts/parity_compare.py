#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AccuLynx -> CRM parity verdict.

Compares the migrated CRM dataset against AccuLynx's own reference totals and prints a
cancel-ready verdict: counts + financial totals within tolerance, per-entity gaps listed.

Usage:
    python scripts/parity_compare.py [--db data/crm_migration.db] [--ref docs/acculynx_reference.json]

The CRM side is read live from the SQLite DB. The AccuLynx side comes from a small JSON the
owner fills in from AccuLynx's reports (see docs/acculynx_reference.example.json). Without it,
the script prints the CRM baseline only and reports "reference missing" (not a pass).

No third-party deps. Money columns are TEXT; parsed the same way the fixed theme.est_num does
(honors leading '-' / surrounding parens as negative; multi-dot -> 0).
"""
import argparse, json, os, re, sqlite3, sys

COUNT_TABLES = ["leads", "contacts", "jobs", "estimates", "invoices", "payments",
                "measurements", "library_docs", "photos", "roof_reports"]
# default tolerances: counts must match within 1% (migration can legitimately drop a few
# canceled/junk rows); financial totals within 1%.
DEFAULT_TOL = {"count": 0.01, "money": 0.01}


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


def crm_side(db):
    if not os.path.exists(db):
        sys.exit("DB not found: %s" % db)
    c = sqlite3.connect(db)
    cur = c.cursor()
    tabs = {r[0] for r in cur.execute("select name from sqlite_master where type='table'")}
    counts = {}
    for t in COUNT_TABLES:
        counts[t] = cur.execute("select count(*) from %s" % t).fetchone()[0] if t in tabs else None
    fin = {"contract_value": 0.0, "collected": 0.0, "invoiced": 0.0, "payments": 0.0}
    if "jobs" in tabs:
        for cv, co in cur.execute("select contract_value, collected from jobs"):
            fin["contract_value"] += money(cv)
            fin["collected"] += money(co)
    if "invoices" in tabs:
        fin["invoiced"] = sum(money(r[0]) for r in cur.execute("select amount from invoices"))
    if "payments" in tabs:
        fin["payments"] = sum(money(r[0]) for r in cur.execute("select amount from payments"))
    c.close()
    return counts, fin


def verdict(crm, ref, tol, kind):
    """Return (ok, pct_gap_str). ref None -> unknown."""
    if ref is None:
        return None, "no reference"
    if crm is None:
        return False, "CRM table missing"
    if ref == 0:
        return (crm == 0), "ref=0"
    gap = abs(crm - ref) / float(ref)
    return gap <= tol[kind], "%.1f%%" % (gap * 100)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/crm_migration.db")
    ap.add_argument("--ref", default="docs/acculynx_reference.json")
    a = ap.parse_args()

    counts, fin = crm_side(a.db)
    ref = json.load(open(a.ref)) if os.path.exists(a.ref) else None
    tol = DEFAULT_TOL

    print("AccuLynx -> CRM PARITY  (db=%s)" % a.db)
    print("=" * 64)
    if ref is None:
        print("!! AccuLynx reference missing (%s) - CRM baseline only, NOT a pass." % a.ref)
        print("   Fill it from docs/acculynx_reference.example.json\n")

    rc = (ref or {}).get("counts", {}) if ref else {}
    rf = (ref or {}).get("financials", {}) if ref else {}
    fails = []

    print("COUNTS                 CRM        AccuLynx     gap   verdict")
    for t in COUNT_TABLES:
        crmv = counts.get(t)
        refv = rc.get(t)
        ok, g = verdict(crmv, refv, tol, "count")
        mark = "-" if ok is None else ("PASS" if ok else "FAIL")
        if ok is False:
            fails.append("count:%s" % t)
        print("  %-18s %8s   %8s   %6s   %s" % (
            t, "-" if crmv is None else crmv, "?" if refv is None else refv, g, mark))

    print("\nFINANCIALS             CRM            AccuLynx       gap   verdict")
    for k in ["contract_value", "invoiced", "collected", "payments"]:
        crmv = fin.get(k, 0.0)
        refv = rf.get(k)
        ok, g = verdict(crmv, refv, tol, "money")
        mark = "-" if ok is None else ("PASS" if ok else "FAIL")
        if ok is False:
            fails.append("money:%s" % k)
        refstr = "?" if refv is None else ("$" + format(refv, ",.2f"))
        print("  {:<18} ${:>13}   {:>13}   {:>6}   {}".format(
            k, format(crmv, ",.2f"), refstr, g, mark))

    print("\n" + "=" * 64)
    if ref is None:
        print("VERDICT: BLOCKED - provide AccuLynx reference totals to judge parity.")
        sys.exit(2)
    if fails:
        print("VERDICT: NOT cancel-ready. Gaps: %s" % ", ".join(fails))
        sys.exit(1)
    print("VERDICT: PASS - parity within tolerance; AccuLynx is safe to cancel.")
    sys.exit(0)


if __name__ == "__main__":
    main()
