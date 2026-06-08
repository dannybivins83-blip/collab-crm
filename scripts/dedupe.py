# -*- coding: utf-8 -*-
"""Smart de-dupe — collapse duplicate JOBS (by AccuLynx GUID) and LEADS (by GUID,
then by name), KEEPING THE NEWEST (and AccuLynx-linked) record and deleting the
older copies. Re-parents EVERY child row (activities, estimates, documents,
photos, invoices, payments, appointments, worksheets, orders, commissions,
materials, …) onto the survivor FIRST, so nothing is orphaned.

Survivor pick: prefer the record that carries the AccuLynx link (external_url),
then the newest id. Jobs are never collapsed by name (a customer can have several
distinct jobs); leads are.

Usage:
  python scripts/dedupe.py                 # DRY RUN, local SQLite (report only)
  python scripts/dedupe.py --apply         # execute, local
  python scripts/dedupe.py --live          # DRY RUN against prod Neon
  python scripts/dedupe.py --live --apply  # execute against prod Neon
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
            u = line.split("=", 1)[1].strip().strip('"').strip("'")
            os.environ["DATABASE_URL"] = re.sub(r"\s", "", u.replace("\\r", "").replace("\\n", "")).replace("﻿", "")
            break
os.environ["CRM_NOBROWSER"] = "1"
import db


def guid(u):
    m = re.search(r"/jobs/([0-9a-f-]{30,})", u or "")
    return m.group(1) if m else None


def _one(r):
    """Return the single value of a one-column row (dict-row on PG, Row on SQLite)."""
    try:
        return list(dict(r).values())[0]
    except Exception:
        return r[0]


def all_tables():
    conn = db.connect()
    try:
        if getattr(db, "IS_PG", False):
            rows = conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'").fetchall()
        else:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        return [_one(r) for r in rows]
    finally:
        conn.close()


def cols(table):
    conn = db.connect()
    try:
        if getattr(db, "IS_PG", False):
            rows = conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name=?", (table,)).fetchall()
            return [_one(r) for r in rows]
        return [r["name"] for r in conn.execute("PRAGMA table_info(%s)" % table).fetchall()]
    finally:
        conn.close()


TABLES = all_tables()
JOB_CHILD = [t for t in TABLES if t != "jobs" and "job_id" in cols(t)]
LEAD_CHILD = [t for t in TABLES if t != "leads" and "lead_id" in cols(t)]
print("Engine:", "Postgres/Neon (LIVE)" if getattr(db, "IS_PG", False) else "local SQLite",
      "| mode:", "APPLY (will write)" if APPLY else "DRY RUN (report only)")
print("job-child tables:", JOB_CHILD)
print("lead-child tables:", LEAD_CHILD)
print()


def survivor(group):
    """Keep the BEST record, not just the newest: prefer the canonical AccuLynx name
    (has the 'R-####:' prefix), then the most-populated record, then the GUID-linked,
    then newest id. (The newer dupes are browser scrapes with stripped names / fewer
    fields, so plain keep-newest would lose the good data.)"""
    def score(r):
        nm = r.get("name") or ""
        prefix = 1 if re.match(r"\s*R-\d", nm) else 0
        filled = sum(1 for f in ("system", "squares", "ahj", "rep", "work_type",
                                 "address", "phone", "email", "contract_value")
                     if str(r.get(f) or "").strip())
        linked = 1 if guid(r.get("external_url")) else 0
        return (prefix, filled, linked, r["id"])
    return max(group, key=score)


def reparent_and_delete(entity, keep_id, dead_ids, child_tables, fk):
    moved = 0
    for did in dead_ids:
        for t in child_tables:
            for r in db.all_rows(t, where=fk + "=?", params=(did,)):
                if APPLY:
                    db.update(t, r["id"], **{fk: keep_id})
                moved += 1
        for a in db.all_rows("activities", where="entity_type=? AND entity_id=?", params=(entity, did)):
            if APPLY:
                db.update("activities", a["id"], entity_id=keep_id)
            moved += 1
        if APPLY:
            db.delete(entity + "s", did)
    return moved


total_deleted = total_moved = 0


def collapse(entity, groups, child_tables, fk, label):
    global total_deleted, total_moved
    dup_groups = {k: v for k, v in groups.items() if len(v) > 1}
    print("%s: %d %s duplicate group(s)" % (entity, len(dup_groups), label))
    shown = 0
    for k, grp in dup_groups.items():
        keep = survivor(grp)
        dead = [r["id"] for r in grp if r["id"] != keep["id"]]
        moved = reparent_and_delete(entity, keep["id"], dead, child_tables, fk)
        total_deleted += len(dead)
        total_moved += moved
        if shown < 12:
            print("  keep #%d (%s) | delete %s | re-parent %d child row(s) — %s"
                  % (keep["id"], "linked" if guid(keep.get("external_url")) else "no-link",
                     dead, moved, (keep.get("name") or k)[:40]))
            shown += 1
    if len(dup_groups) > 12:
        print("  … and %d more group(s)" % (len(dup_groups) - 12))


print("BEFORE — leads:%d jobs:%d" % (len(db.all_rows("leads")), len(db.all_rows("jobs"))))
print()

# Jobs by GUID only.
jg = {}
for j in db.all_rows("jobs"):
    g = guid(j.get("external_url"))
    if g:
        jg.setdefault(g, []).append(j)
collapse("job", jg, JOB_CHILD, "job_id", "GUID")

# Leads by GUID, then by name.
lg = {}
for l in db.all_rows("leads"):
    g = guid(l.get("external_url"))
    if g:
        lg.setdefault(g, []).append(l)
collapse("lead", lg, LEAD_CHILD, "lead_id", "GUID")

ln = {}
for l in db.all_rows("leads"):  # re-read (some may be gone if APPLY)
    nm = (l.get("name") or "").strip().lower()
    if nm:
        ln.setdefault(nm, []).append(l)
collapse("lead", ln, LEAD_CHILD, "lead_id", "name")

print()
print("AFTER  — leads:%d jobs:%d" % (len(db.all_rows("leads")), len(db.all_rows("jobs"))))
print("Removed %d duplicate record(s); re-parented %d child row(s)." % (total_deleted, total_moved))
if not APPLY:
    print("\n** DRY RUN — nothing was changed. Re-run with --apply to execute. **")
