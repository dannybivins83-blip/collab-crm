# -*- coding: utf-8 -*-
"""Backfill CLOSED jobs from AccuLynx (the bucket the normal sync deliberately skips).

Efficient: THIN import — no per-job detail enrichment (closed jobs are historical),
just the list payload. Resumable via a cursor in company_settings. Optionally also
'cancelled'. Run repeatedly; idempotent (upserts by AccuLynx GUID, else by name)."""
import sys
import os
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import re
import db
from modules import acculynx_sync as S

KEY = (db.get_company().get("acculynx_api_key") or "").strip()
BASE = S.DEFAULT_BASE
PAGE = 25


def _guid(u):
    m = re.search(r"/jobs/([0-9a-f-]{30,})", u or "")
    return m.group(1) if m else None


def run(group="closed", budget=420, limit=None):
    cur_key = "acculynx_%s_cursor" % group
    start = int(db.get_company().get(cur_key) or 0)
    jobs = db.all_rows("jobs")
    by_guid = {_guid(j.get("external_url")): j for j in jobs if _guid(j.get("external_url"))}
    by_name = {(j.get("name") or "").lower(): j for j in jobs}
    t0 = time.time()
    added = updated = 0
    done = False
    stage = "closed" if group == "closed" else "canceled"
    while True:
        if budget and time.time() - t0 > budget:
            break
        d = S._api_get(BASE, "/jobs", KEY, {"milestones": group, "pageStartIndex": start,
                                            "pageSize": PAGE, "sortBy": "MilestoneDate",
                                            "sortOrder": "Descending"})
        items = d.get("items") if isinstance(d, dict) else (d or [])
        if not items:
            done = True
            break
        for job in items:
            jid = S._g(job, "id", "jobId")
            name = (S._g(job, "jobName", "name", "displayName") or "").strip()
            if not name:
                continue
            val = S._money_val(job)
            cb = S._contact_basics(job, BASE, KEY, fetch=False)
            addr = S._flatten_address(S._g(job, "locationAddress", "address", "jobAddress", default={}))
            rec = {
                "name": name, "rid": S._g(job, "jobNumber", "number", "refNumber"),
                "phone": cb.get("phone"), "email": cb.get("email"),
                "work_type": S._name_of(S._g(job, "workType", "tradeType")) or S._join_list(job.get("tradeTypes")),
                "source": S._name_of(S._g(job, "leadSource", "source")),
                "rep": S._name_of(S._g(job, "salesRep", "assignedTo")) or "Danny Bivins",
                "external_url": "https://my.acculynx.com/jobs/%s" % jid if jid else "",
                "contract_value": val, "stage": stage, "department": "REROOF Department",
            }
            parts = [p.strip() for p in (addr or "").split(",")]
            rec["address"] = parts[0] if parts else addr
            rec["city"] = parts[1] if len(parts) > 1 else ""
            cur = by_guid.get(jid) or by_name.get(name.lower())
            if cur:
                db.update("jobs", cur["id"], stage=stage, external_url=rec["external_url"],
                          contract_value=val or cur.get("contract_value"))
                updated += 1
            else:
                cid = S._ensure_contact(name, rec)
                nid = db.insert("jobs", {**rec, "contact_id": cid, "stage_since": db.today(),
                                         "narrative": "Backfilled from AccuLynx (%s)." % group})
                rec["id"] = nid
                by_guid[jid] = rec
                by_name[name.lower()] = rec
                added += 1
        start += len(items)
        db.save_company({cur_key: start})
        if len(items) < PAGE:
            done = True
            break
        if limit and (added + updated) >= limit:
            break
    db.save_company({cur_key: 0 if done else start})
    return {"group": group, "added": added, "updated": updated, "done": done, "cursor": start}


if __name__ == "__main__":
    g = sys.argv[1] if len(sys.argv) > 1 else "closed"
    bud = int(sys.argv[2]) if len(sys.argv) > 2 else 420
    print(run(group=g, budget=bud))
