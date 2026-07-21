# -*- coding: utf-8 -*-
"""Backfill CLOSED / CANCELED records from AccuLynx (the buckets the normal sync skips).

Efficient: THIN import — no per-record detail enrichment (these are historical),
just the list payload. Resumable via a cursor in company_settings. Run repeatedly;
idempotent (upserts by AccuLynx GUID, else by name WITHIN the same kind).

Kind matters: AccuLynx's 'cancelled' bucket is mostly DEAD LEADS, not canceled jobs.
An earlier version of this script hardcoded `stage = "closed"|"canceled"` with no kind
resolution and wrote every record into `jobs`, which buried thousands of dead leads in
the jobs table and made the jobs count and the pipeline disagree. Kind is now resolved
with `acculynx_sync._resolve_stage()` (the same helper the live sync uses) so a dead
LEAD lands in `leads`.
"""
import sys
import os
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import re
import db
from modules import acculynx_sync as S

BASE = S.DEFAULT_BASE
PAGE = 25

# Coarse bucket → (kind, stage) used only when the record carries no usable milestone.
_GROUP_FALLBACK = {
    "closed": ("job", "closed"),
    "cancelled": ("job", "canceled"),
    "canceled": ("job", "canceled"),
    "dead": ("lead", "lost"),
}


def _api_key():
    """Read the tenant's AccuLynx key lazily (module import must not need a DB)."""
    return (db.get_company().get("acculynx_api_key") or "").strip()


# Back-compat for anything that referenced the old module-level constant.
try:
    KEY = _api_key()
except Exception:      # pragma: no cover - no DB yet at import time
    KEY = ""


def _guid(u):
    m = re.search(r"/jobs/([0-9a-f-]{30,})", u or "")
    return m.group(1) if m else None


def _milestone_of(job):
    m = S._g(job, "currentMilestone", "milestone", "milestoneName",
             "currentMilestoneName", "status")
    if isinstance(m, dict):
        m = S._g(m, "name", "title", "milestoneName")
    return (m or "").strip()


def resolve_kind_stage(job, group):
    """Return (kind, stage) for a historical record.

    Kind comes from the record's own milestone via `_resolve_stage()`; the coarse
    group bucket is only a fallback for records with no/unknown milestone. The group
    then decides the terminal stage: a LEAD in the cancelled/dead bucket is 'lost',
    a lead in the closed bucket is 'won'; jobs take 'closed' / 'canceled'.
    """
    g = (group or "").strip().lower()
    milestone = _milestone_of(job)
    m_norm = milestone.lower()
    if m_norm:
        kind, _stage = S._resolve_stage(milestone)
    else:
        kind, _stage = _GROUP_FALLBACK.get(g, ("job", "canceled"))
    if kind == "lead":
        return "lead", ("won" if g == "closed" else "lost")
    return "job", ("closed" if g == "closed" else "canceled")


class _Index(object):
    """GUID-first, name-second lookup over the existing leads + jobs rows.

    Name matching is only ever used INSIDE one kind and only when the incoming
    record has no AccuLynx GUID — a GUID is authoritative, so once we have one we
    never fall back to a name (which would merge unrelated records that happen to
    share a customer name).
    """

    def __init__(self):
        leads = db.all_rows("leads")
        jobs = db.all_rows("jobs")
        self.g = {"lead": {}, "job": {}}
        self.n = {"lead": {}, "job": {}}
        for kind, rows in (("lead", leads), ("job", jobs)):
            for r in rows:
                gid = _guid(r.get("external_url"))
                if gid:
                    self.g[kind][gid] = r
                nm = (r.get("name") or "").lower()
                if nm:
                    self.n[kind].setdefault(nm, r)

    def lookup(self, kind, jid, name):
        """Return (row, table_kind). Job outranks lead on a cross-kind GUID hit."""
        if jid:
            hit = self.g[kind].get(jid)
            if hit is not None:
                return hit, kind
            other = "job" if kind == "lead" else "lead"
            hit = self.g[other].get(jid)
            if hit is not None and other == "job":
                # Already promoted to a job — never re-create it as a lead.
                return hit, "job"
            if hit is not None:
                # Stored as a lead, arriving as a (closed/canceled) job: update the
                # lead in place rather than minting a duplicate job row.
                return hit, "lead"
            return None, kind
        nm = (name or "").lower()
        if nm:
            hit = self.n[kind].get(nm)
            if hit is not None:
                return hit, kind
        return None, kind

    def add(self, kind, jid, name, row):
        if jid:
            self.g[kind][jid] = row
        if name:
            self.n[kind].setdefault(name.lower(), row)


def import_items(items, group, idx=None, company=None):
    """Upsert a page of AccuLynx list records. Pure — no network, no cursor writes.

    Returns {"added_leads", "added_jobs", "updated", "skipped"}.
    """
    idx = idx if idx is not None else _Index()
    company = company if company is not None else db.get_company()
    key = (company.get("acculynx_api_key") or "").strip()
    added_l = added_j = updated = skipped = 0
    for job in items or []:
        jid = S._g(job, "id", "jobId", "uid")
        name = (S._g(job, "jobName", "name", "displayName") or "").strip()
        if not name:
            skipped += 1
            continue
        kind, stage = resolve_kind_stage(job, group)
        val = S._money_val(job)
        cb = S._contact_basics(job, BASE, key, fetch=False)
        addr = S._flatten_address(S._g(job, "locationAddress", "address", "jobAddress", default={}))
        work_type = (S._name_of(S._g(job, "workType", "tradeType"))
                     or S._join_list(job.get("tradeTypes")))
        url = "https://my.acculynx.com/jobs/%s" % jid if jid else ""
        rec = {
            "name": name, "rid": S._g(job, "jobNumber", "number", "refNumber"),
            "phone": cb.get("phone"), "email": cb.get("email"),
            "work_type": work_type,
            "source": S._name_of(S._g(job, "leadSource", "source")),
            "rep": S._name_of(S._g(job, "salesRep", "assignedTo")) or "Danny Bivins",
            "external_url": url,
            "stage": stage,
            "department": S._department_for(work_type, company),
        }
        parts = [p.strip() for p in (addr or "").split(",")]
        rec["address"] = parts[0] if parts else addr
        city = parts[1] if len(parts) > 1 else ""

        cur, table_kind = idx.lookup(kind, jid, name)
        if cur is not None:
            # Cross-kind hit → keep the record where it already lives and do NOT
            # force this pass's stage onto a row of the other kind.
            upd = {"external_url": url or cur.get("external_url")}
            if table_kind == kind:
                upd["stage"] = stage
            if table_kind == "job":
                upd["contract_value"] = val or cur.get("contract_value")
            elif val:
                upd["estimate"] = val or cur.get("estimate")
            db.update(table_kind + "s", cur["id"], **upd)
            updated += 1
            continue

        cid = S._ensure_contact(name, rec)
        row = {**rec, "contact_id": cid, "stage_since": db.today(),
               "narrative": "Backfilled from AccuLynx (%s)." % group}
        if kind == "lead":
            if val:
                row["estimate"] = val
            nid = db.insert("leads", row)
            added_l += 1
        else:
            row["city"] = city
            row["contract_value"] = val
            nid = db.insert("jobs", row)
            added_j += 1
        row["id"] = nid
        idx.add(kind, jid, name, row)
    return {"added_leads": added_l, "added_jobs": added_j,
            "updated": updated, "skipped": skipped}


def run(group="closed", budget=420, limit=None):
    cur_key = "acculynx_%s_cursor" % group
    start = int(db.get_company().get(cur_key) or 0)
    idx = _Index()
    company = db.get_company()
    key = _api_key()
    t0 = time.time()
    added_l = added_j = updated = 0
    done = False
    while True:
        if budget and time.time() - t0 > budget:
            break
        d = S._api_get(BASE, "/jobs", key, {"milestones": group, "pageStartIndex": start,
                                            "pageSize": PAGE, "sortBy": "MilestoneDate",
                                            "sortOrder": "Descending"})
        items = d.get("items") if isinstance(d, dict) else (d or [])
        if not items:
            done = True
            break
        res = import_items(items, group, idx=idx, company=company)
        added_l += res["added_leads"]
        added_j += res["added_jobs"]
        updated += res["updated"]
        start += len(items)
        db.save_company({cur_key: start})
        if len(items) < PAGE:
            done = True
            break
        if limit and (added_l + added_j + updated) >= limit:
            break
    db.save_company({cur_key: 0 if done else start})
    return {"group": group, "added": added_l + added_j, "added_leads": added_l,
            "added_jobs": added_j, "updated": updated, "done": done, "cursor": start}


if __name__ == "__main__":
    g = sys.argv[1] if len(sys.argv) > 1 else "closed"
    bud = int(sys.argv[2]) if len(sys.argv) > 2 else 420
    print(run(group=g, budget=bud))
