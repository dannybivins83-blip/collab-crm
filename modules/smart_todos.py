# -*- coding: utf-8 -*-
"""Smart To-Do — a prioritized, derived next-actions list.

Sources (no fabrication — every todo points back to real content):
  1. Unread inbound customer email on a known CRM record -> "Reply to {name}".
  2. A communication / note / narrative with an intent verb (send estimate,
     schedule, permit, collect deposit, follow up, send invoice) that has no
     later completion -> a todo.
  3. Overdue follow-ups (reuses theme.follow_status) -> a todo.

It is computed live on every request and never written to the DB, so refreshing
can't pile up duplicates — each todo carries a stable id derived from its source.
DRAFT-ONLY: a reply todo offers the existing /gmail/draft route; nothing sends.
"""
import re

from flask import Blueprint, jsonify, session, url_for

import db
import theme
import constants
from theme import current_department

bp = Blueprint("smart_todos", __name__, url_prefix="/todos")

# Intent families: (key, verb, intent regex, done regex). A note matching the
# intent with no later note matching `done` (same entity) becomes a todo.
_FAMILIES = [
    ("estimate", "Send the estimate",
     r"(send|sent out|prepare|draft|build|write|finish|get|email)[\w\s]{0,15}"
     r"(estimate|quote|proposal|bid)|(estimate|quote|proposal|bid)[\w\s]{0,10}"
     r"(needed|to send|owed|requested|requested)",
     r"(estimate|quote|proposal|bid)[\w\s]{0,15}"
     r"(sent|emailed|delivered|signed|approved|declined)"),
    ("schedule", "Schedule the appointment / inspection",
     r"\bschedule\b|set up[\w\s]{0,10}(appt|appointment|inspection|meeting)|"
     r"\bbook\b[\w\s]{0,10}(inspection|appointment)|need[\w\s]{0,10}schedul",
     r"(scheduled|booked|set for|confirmed for|on the calendar|appt set)"),
    ("permit", "Move the permit forward",
     r"\bpermit\b[\w\s]{0,15}(needed|apply|submit|pull|file|owed|to submit)|"
     r"(submit|apply for|pull|file)[\w\s]{0,10}permit",
     r"permit[\w\s]{0,15}(submitted|applied|approved|issued|pulled|filed|in hand)"),
    ("deposit", "Collect the deposit / payment",
     r"(collect|get|request|need)[\w\s]{0,12}(deposit|down ?payment|payment|balance)|"
     r"deposit[\w\s]{0,8}(owed|due|needed)",
     r"(deposit|payment|balance)[\w\s]{0,12}(collected|received|paid)"),
    ("followup", "Follow up",
     r"\b(follow ?up|follow-up|call back|call ?back|reach out|circle back|"
     r"touch base|check in)\b",
     r"(followed up|reached out|talked to|spoke with|called them|"
     r"left[\w\s]{0,3}(vm|voicemail|message)|connected with)"),
    ("invoice", "Send the invoice",
     r"(send|create|generate|prepare)[\w\s]{0,10}invoice|"
     r"invoice[\w\s]{0,8}(needed|owed|to send)",
     r"invoice[\w\s]{0,12}(sent|created|emailed|paid)"),
]
_FAMILIES = [(k, v, re.compile(i, re.I), re.compile(d, re.I)) for k, v, i, d in _FAMILIES]

_PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}


def _rec_url(et, rid):
    if et == "lead":
        return url_for("leads.detail", lead_id=rid)
    if et == "job":
        return url_for("jobs.detail", job_id=rid)
    return url_for("contacts.detail", contact_id=rid)


def _rec_label(et, rec):
    base = rec.get("rid") or ""
    name = rec.get("name") or ""
    if base and name:
        return "%s · %s" % (base, name)
    return base or name or ("%s #%s" % (et, rec["id"]))


def _record_block(et, rec):
    return {"type": et, "id": rec["id"], "label": _rec_label(et, rec),
            "name": rec.get("name") or "", "url": _rec_url(et, rec["id"])}


# ---------------------------------------------------------------------------
# Rule 1 — unread inbound email on a known record
# ---------------------------------------------------------------------------

def _email_todos(uid):
    from modules import gmail
    todos, replied_records = [], set()
    for m in gmail.unread_inbound(uid, 15):
        match = m.get("match")
        if not match:
            continue  # unknown sender/subject — don't invent a todo
        replied_records.add((match["type"], match["id"]))
        name = match.get("name") or m.get("fromName") or m.get("from")
        subj = m.get("subject") or "(no subject)"
        reply_subj = subj if subj.lower().startswith("re:") else "Re: " + subj
        todos.append({
            "id": "email:%s" % m["id"],
            "priority": "high",
            "title": "Reply to %s re: %s" % (name, subj),
            "sub": m.get("snippet", "")[:140],
            "url": match["url"],
            "record": {"type": match["type"], "id": match["id"],
                       "label": match["label"], "name": name, "url": match["url"]},
            "source": {"kind": "email", "email_id": m["id"], "from": m.get("from")},
            # Everything the existing /gmail/draft route needs for a threaded reply.
            "draft": {"to": m.get("from", ""), "subject": reply_subj,
                      "threadId": m.get("threadId", "")},
        })
    return todos, replied_records


# ---------------------------------------------------------------------------
# Rule 2 — open intent in comms / narrative
# ---------------------------------------------------------------------------

def _intent_todos(records, acts_by_entity):
    """records: {(et,id): rec}. acts_by_entity: {(et,id): [activity,...]}."""
    todos = []
    for (et, eid), rec in records.items():
        acts = sorted(acts_by_entity.get((et, eid), []), key=lambda a: a.get("created") or "")
        # Synthesize the record's narrative as one more piece of text to scan.
        scan = list(acts)
        narr = (rec.get("narrative") or "").strip()
        if narr:
            scan.append({"id": "narr", "kind": "narrative",
                         "created": rec.get("updated") or rec.get("created") or "",
                         "text": narr})
        for fam_key, verb, intent_re, done_re in _FAMILIES:
            hits = [a for a in scan if a.get("text") and intent_re.search(a["text"])]
            if not hits:
                continue
            latest = hits[-1]  # most recent open intent of this family
            cstamp = latest.get("created") or ""
            # Completed if any same-or-later activity says it's done.
            done = any((a.get("text") and done_re.search(a["text"])
                        and (a.get("created") or "") >= cstamp) for a in scan)
            if done:
                continue
            if theme.days_since(cstamp) > 90:  # stale; drop it
                continue
            src_kind = "narrative" if latest.get("kind") == "narrative" else "comm"
            todos.append({
                "id": "intent:%s:%s:%s:%s" % (et, eid, fam_key, latest.get("id")),
                "priority": "medium",
                "title": "%s for %s" % (verb, rec.get("name") or _rec_label(et, rec)),
                "sub": (latest.get("text") or "")[:140],
                "url": _rec_url(et, eid),
                "record": _record_block(et, rec),
                "source": {"kind": src_kind, "family": fam_key,
                           "activity_id": latest.get("id"), "at": cstamp},
            })
        # An explicit non-empty `todo` field is itself an open next action.
        # Skip AccuLynx sync metadata (e.g. "Milestone: Prospect (as of 2026-06-22)") — that's
        # status info written by the sync, not an actionable task.
        todo_field = (rec.get("todo") or "").strip()
        if todo_field and not todo_field.startswith("Milestone:"):
            todos.append({
                "id": "todofield:%s:%s" % (et, eid),
                "priority": "medium",
                "title": "%s: %s" % (rec.get("name") or _rec_label(et, rec),
                                     todo_field[:90]),
                "sub": todo_field[:140],
                "url": _rec_url(et, eid),
                "record": _record_block(et, rec),
                "source": {"kind": "todo_field"},
            })
    return todos


# ---------------------------------------------------------------------------
# Rule 3 — overdue follow-ups (mirrors dashboard.home)
# ---------------------------------------------------------------------------

def _followup_todos(leads, jobs, replied_records):
    todos = []
    for et, rows, stage_fn, clock in (
            ("lead", leads, constants.lead_stage, "last_contact"),
            ("job", jobs, constants.job_stage, "stage_since")):
        inactive = constants.LEAD_INACTIVE if et == "lead" else constants.JOB_INACTIVE
        for r in rows:
            if r["stage"] in inactive:
                continue
            if (et, r["id"]) in replied_records:
                continue  # a concrete reply todo already covers this record
            sd = stage_fn(r["stage"])
            fs = theme.follow_status(sd, r.get(clock) or r.get("created"), r.get("snooze_until"))
            if fs["level"] == "ok":
                continue
            todos.append({
                "id": "follow:%s:%s" % (et, r["id"]),
                "priority": "high" if fs["level"] == "hot" else "medium",
                "title": "%s %s — %s" % (
                    "Overdue follow-up:" if fs["level"] == "hot" else "Follow up:",
                    r.get("name") or _rec_label(et, r), sd["name"]),
                "sub": "%s · %d days in stage" % (fs["label"], fs["days"]),
                "url": _rec_url(et, r["id"]),
                "record": _record_block(et, r),
                "source": {"kind": "follow", "level": fs["level"], "days": fs["days"]},
                "_days": fs["days"],
            })
    return todos


# ---------------------------------------------------------------------------
# Assemble
# ---------------------------------------------------------------------------

_FOLLOW_CAP = 10   # max overdue-follow-up todos shown (prevents 895-row flooding)
_TODO_CAP   = 50   # max total todos returned to the widget


def generate(uid):
    import constants as _c
    dept = current_department()
    # Pre-filter inactive stages in SQL — _followup_todos skips them anyway,
    # so loading won/lost/closed/canceled rows is pure waste.
    _li_ph = ",".join("?" * len(_c.LEAD_INACTIVE))
    _ji_ph = ",".join("?" * len(_c.JOB_INACTIVE))
    leads = db.all_rows("leads", "department=? AND stage NOT IN (%s)" % _li_ph,
                        (dept,) + tuple(_c.LEAD_INACTIVE))
    jobs  = db.all_rows("jobs",  "department=? AND stage NOT IN (%s)" % _ji_ph,
                        (dept,) + tuple(_c.JOB_INACTIVE))
    records = {}
    for l in leads:
        records[("lead", l["id"])] = l
    for j in jobs:
        records[("job", j["id"])] = j

    # Load only activities for entities in this department — avoids a full-table
    # scan of the activities table (which grows to 10k+ rows with AccuLynx imports).
    acts_by_entity = {}
    if records:
        lead_ids = [k[1] for k in records if k[0] == "lead"]
        job_ids  = [k[1] for k in records if k[0] == "job"]
        wanted_kinds = ["note", "call", "email", "sms", "task", "stage", "automation"]
        kinds_ph = ",".join("?" * len(wanted_kinds))
        acts = []
        if lead_ids:
            id_ph = ",".join("?" * len(lead_ids))
            acts += db.all_rows("activities",
                "entity_type='lead' AND entity_id IN (%s) AND kind IN (%s)" % (id_ph, kinds_ph),
                tuple(lead_ids) + tuple(wanted_kinds), "id ASC")
        if job_ids:
            id_ph = ",".join("?" * len(job_ids))
            acts += db.all_rows("activities",
                "entity_type='job' AND entity_id IN (%s) AND kind IN (%s)" % (id_ph, kinds_ph),
                tuple(job_ids) + tuple(wanted_kinds), "id ASC")
        for a in acts:
            key = (a.get("entity_type"), a.get("entity_id"))
            acts_by_entity.setdefault(key, []).append(a)

    email_todos, replied = _email_todos(uid)
    intent_todos  = _intent_todos(records, acts_by_entity)
    follow_todos  = _followup_todos(leads, jobs, replied)

    # Sort follow-ups by urgency and cap — 895 overdue entries would flood the widget.
    follow_todos.sort(key=lambda t: -t.get("_days", 0))
    follow_todos = follow_todos[:_FOLLOW_CAP]

    # Merge + dedupe by id.
    seen, todos = set(), []
    for t in email_todos + follow_todos + intent_todos:
        if t["id"] in seen:
            continue
        seen.add(t["id"])
        todos.append(t)

    todos.sort(key=lambda t: (_PRIORITY_RANK.get(t["priority"], 9), -t.get("_days", 0)))
    todos = todos[:_TODO_CAP]
    for t in todos:
        t.pop("_days", None)
    return todos


@bp.route("/smart")
def smart():
    uid = session.get("user_id")
    todos = generate(uid)
    counts = {"high": 0, "medium": 0, "low": 0}
    for t in todos:
        counts[t["priority"]] = counts.get(t["priority"], 0) + 1
    return jsonify({"ok": True, "todos": todos, "total": len(todos), "counts": counts})
