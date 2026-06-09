# -*- coding: utf-8 -*-
"""Duplicate-lead finder + categorizer + safe merge.

Stands alone (own blueprint, no edits to leads.py) so it doesn't collide with the
leads-module lane. Groups likely-duplicate leads, labels each group by WHY they
matched (same contact / phone / email / name+address), suggests a survivor (most
advanced stage, then most recent), and merges on a two-phase preview→confirm —
re-pointing the dupes' jobs/estimates/docs/appts/measurements/activities onto the
survivor before deleting the empty duplicate lead rows. No history is lost.
"""
import re
from flask import Blueprint, render_template, request, redirect, url_for, flash

import db
import constants
from theme import current_department

bp = Blueprint("leads_dedupe", __name__, url_prefix="/leads/dupes")

# Tables that reference a lead via lead_id (history that must follow the survivor).
_LEAD_FK_TABLES = ["jobs", "estimates", "documents", "appointments", "measurements"]


def _digits(s):
    return re.sub(r"\D", "", s or "")


def _norm(s):
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _keys(l):
    """Strong match keys for a lead, each tagged with a human category."""
    out = []
    if l.get("contact_id"):
        out.append(("contact", "c:%s" % l["contact_id"], "Same contact"))
    ph = _digits(l.get("phone"))
    if len(ph) >= 10:
        out.append(("phone", "p:%s" % ph[-10:], "Same phone"))
    em = _norm(l.get("email"))
    if em and "@" in em:
        out.append(("email", "e:%s" % em, "Same email"))
    name, addr = _norm(l.get("name")), _norm(l.get("address"))
    if name and addr:
        out.append(("nameaddr", "na:%s|%s" % (name, addr), "Same name + address"))
    return out


def _survivor_rank(l):
    """Higher = better survivor: most advanced stage, then most recent. A 'lost'
    lead is the WORST survivor even though it sorts last in the pipeline list, so
    force it to the bottom — never keep a dead lead over a live one."""
    stage = l.get("stage")
    not_lost = 0 if stage == "lost" else 1
    return (not_lost, constants.LEAD_STAGE_INDEX.get(stage, 0),
            l.get("created") or "", l.get("id"))


def find_groups(department=None):
    """Union-find leads that share any strong key; return categorized dup groups
    (size >= 2 only). Each group: members (survivor first), categories, key count."""
    where, params = ("department=?", (department,)) if department else ("", ())
    leads = db.all_rows("leads", where, params)
    parent = {l["id"]: l["id"] for l in leads}
    cats = {l["id"]: set() for l in leads}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    seen = {}  # key -> first lead id that used it
    for l in leads:
        for _kind, key, _label in _keys(l):
            cats[l["id"]].add(_label)
            if key in seen:
                union(seen[key], l["id"])
                cats[seen[key]].add(_label)
            else:
                seen[key] = l["id"]

    by_root = {}
    lead_by_id = {l["id"]: l for l in leads}
    for l in leads:
        by_root.setdefault(find(l["id"]), []).append(l)

    groups = []
    for root, members in by_root.items():
        if len(members) < 2:
            continue
        members.sort(key=_survivor_rank, reverse=True)
        for m in members:
            m["_stage"] = constants.lead_stage(m.get("stage"))
        group_cats = set()
        for m in members:
            group_cats |= cats[m["id"]]
        groups.append({
            "members": members,
            "survivor": members[0],
            "dupes": members[1:],
            "categories": sorted(group_cats),
            "count": len(members),
        })
    # Biggest / most-categorized groups first.
    groups.sort(key=lambda g: (-g["count"], g["survivor"].get("name") or ""))
    return groups


@bp.route("/")
def index():
    dept = current_department()
    groups = find_groups(dept)
    total_dupes = sum(len(g["dupes"]) for g in groups)
    return render_template("lead_dupes.html", groups=groups, total_dupes=total_dupes,
                           dept=dept)


def _merge_plan(survivor_id, dupe_ids):
    moves = {t: 0 for t in _LEAD_FK_TABLES}
    moves["activities"] = 0
    detail = []
    for did in dupe_ids:
        d = db.get("leads", did)
        if not d:
            continue
        row = {"lead": d, "tables": {}}
        for t in _LEAD_FK_TABLES:
            n = len(db.all_rows(t, "lead_id=?", (did,)))
            moves[t] += n
            row["tables"][t] = n
        acts = len(db.entity_activity("lead", did))
        moves["activities"] += acts
        row["tables"]["activities"] = acts
        detail.append(row)
    return moves, detail


def _do_merge(survivor_id, dupe_ids):
    moved = {t: 0 for t in _LEAD_FK_TABLES}
    moved["activities"] = 0
    for did in dupe_ids:
        if did == survivor_id:
            continue
        for t in _LEAD_FK_TABLES:
            for r in db.all_rows(t, "lead_id=?", (did,)):
                db.update(t, r["id"], lead_id=survivor_id)
                moved[t] += 1
        for a in db.entity_activity("lead", did):
            db.update("activities", a["id"], entity_id=survivor_id)
            moved["activities"] += 1
        db.delete("leads", did)
    return moved


@bp.route("/merge", methods=["POST"])
def merge():
    """Two-phase: first POST previews; confirm=1 executes the merge into the survivor."""
    survivor_id = int(request.form.get("survivor_id", 0))
    survivor = db.get("leads", survivor_id)
    if not survivor:
        flash("Survivor lead not found.", "error")
        return redirect(url_for("leads_dedupe.index"))
    dupe_ids = [int(x) for x in request.form.getlist("dupe_ids")
                if x.isdigit() and int(x) != survivor_id]
    if not dupe_ids:
        flash("Select at least one duplicate lead to merge.", "error")
        return redirect(url_for("leads_dedupe.index"))

    if request.form.get("confirm"):
        moved = _do_merge(survivor_id, dupe_ids)
        summary = ", ".join("%d %s" % (n, t) for t, n in moved.items() if n)
        db.add_activity("lead", survivor_id, "note",
                        "Merged %d duplicate lead(s) into this lead (%s)." % (
                            len(dupe_ids), summary or "no linked records"))
        flash("Merged %d duplicate lead(s). Moved: %s." % (
            len(dupe_ids), summary or "nothing to move"), "ok")
        return redirect(url_for("leads_dedupe.index"))

    moves, detail = _merge_plan(survivor_id, dupe_ids)
    return render_template("lead_merge_preview.html", survivor=survivor,
                           moves=moves, detail=detail, dupe_ids=dupe_ids)
