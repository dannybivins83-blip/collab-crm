# -*- coding: utf-8 -*-
"""Duplicate-lead finder that tells two very different cases apart:

  1. TRUE DUPLICATE  — same client AND same property (the same lead entered twice).
     Safe to MERGE: re-point the dupe's jobs/estimates/docs/appts/measurements/
     activities onto the survivor, delete the empty duplicate lead row.

  2. REPEAT CLIENT   — same client, DIFFERENT properties (separate real projects).
     Must NOT be merged. Instead LINK all the client's leads under one client
     contact (consolidating the duplicate contact records the CRM auto-created per
     lead) so every project shows together — but each project's lead stays intact.

Clustering is by client identity (contact_id / phone / email / name); the property
ADDRESS then decides duplicate-vs-different-project inside each client cluster.
Stands alone (own blueprint, no edits to leads.py).
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


def _client_keys(l):
    """Identity keys for the CLIENT (person), independent of which property/project.
    Address is deliberately excluded here — that's what separates a repeat client's
    projects from a true duplicate."""
    out = []
    if l.get("contact_id"):
        out.append(("c:%s" % l["contact_id"], "Same contact"))
    ph = _digits(l.get("phone"))
    if len(ph) >= 10:
        out.append(("p:%s" % ph[-10:], "Same phone"))
    em = _norm(l.get("email"))
    if em and "@" in em:
        out.append(("e:%s" % em, "Same email"))
    nm = _norm(l.get("name"))
    if nm:
        out.append(("n:%s" % nm, "Same name"))
    return out


def _street(s):
    """Street line only — drop city/state/zip and normalize punctuation so the SAME
    property written two ways ('812 SW 3rd Ave' vs '812 SW 3rd Ave, Boynton Beach, FL
    33426') maps to one project key."""
    a = _norm(s).split(",")[0]          # everything before the first comma
    a = re.sub(r"[.#]", " ", a)         # '#12' / 'Ave.' punctuation
    return re.sub(r"\s+", " ", a).strip()


def _project_key(l):
    """Identifies the PROPERTY/project by street line. Leads with no address can't be
    assumed to share a project, so each gets its own bucket (never merged on a blank)."""
    a = _street(l.get("address"))
    return a if a else "noaddr:%d" % l["id"]


def _project_label(l):
    return (l.get("address") or "").strip() or "(no address on file)"


def _survivor_rank(l):
    """Higher = better survivor: most advanced stage, then most recent. A 'lost'
    lead is forced to the bottom — never keep a dead lead over a live one."""
    stage = l.get("stage")
    not_lost = 0 if stage == "lost" else 1
    return (not_lost, constants.LEAD_STAGE_INDEX.get(stage, 0),
            l.get("created") or "", l.get("id"))


def find_clusters(department=None):
    """Union-find leads by client identity, then classify each cluster by property.

    Returns clusters (2+ leads) each carrying:
      projects        — leads grouped by property (address), survivor-sorted
      dupe_sets       — projects with 2+ leads at the SAME address (mergeable)
      distinct_props  — count of real (addressed) projects
      is_repeat_client— True when the client spans 2+ different properties
      categories      — why the leads matched as the same client
      contact_ids     — distinct contact_ids across the cluster
    """
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

    seen = {}
    for l in leads:
        for key, label in _client_keys(l):
            cats[l["id"]].add(label)
            if key in seen:
                union(seen[key], l["id"])
                cats[seen[key]].add(label)
            else:
                seen[key] = l["id"]

    by_root = {}
    for l in leads:
        by_root.setdefault(find(l["id"]), []).append(l)

    clusters = []
    for root, members in by_root.items():
        if len(members) < 2:
            continue
        members.sort(key=_survivor_rank, reverse=True)
        for m in members:
            m["_stage"] = constants.lead_stage(m.get("stage"))

        # Group the client's leads by property.
        proj_map = {}
        for m in members:
            proj_map.setdefault(_project_key(m), []).append(m)
        projects = []
        for key, plist in proj_map.items():
            plist.sort(key=_survivor_rank, reverse=True)
            projects.append({
                "key": key, "label": _project_label(plist[0]),
                "leads": plist, "survivor": plist[0],
                "is_dupe": len(plist) >= 2 and not key.startswith("noaddr:"),
                "is_addressed": not key.startswith("noaddr:"),
            })
        projects.sort(key=lambda p: (-len(p["leads"]), p["label"]))
        dupe_sets = [p for p in projects if p["is_dupe"]]
        distinct_props = sum(1 for p in projects if p["is_addressed"])

        client_cats = set()
        for m in members:
            client_cats |= cats[m["id"]]
        contact_ids = sorted({m["contact_id"] for m in members if m.get("contact_id")})

        clusters.append({
            "survivor": members[0], "members": members, "count": len(members),
            "projects": projects, "dupe_sets": dupe_sets,
            "distinct_props": distinct_props,
            "is_repeat_client": distinct_props >= 2,
            "categories": sorted(client_cats),
            "contact_ids": contact_ids,
            "member_ids": [m["id"] for m in members],
        })
    # Repeat clients (more projects) first, then bigger clusters.
    clusters.sort(key=lambda c: (-c["distinct_props"], -c["count"],
                                 c["survivor"].get("name") or ""))
    return clusters


@bp.route("/")
def index():
    dept = current_department()
    clusters = find_clusters(dept)
    repeat_clients = [c for c in clusters if c["is_repeat_client"]]
    duplicates = [c for c in clusters if not c["is_repeat_client"]]  # single-property dupes
    total_dupe_leads = sum(sum(len(p["leads"]) - 1 for p in c["dupe_sets"]) for c in clusters)
    return render_template("lead_dupes.html", repeat_clients=repeat_clients,
                           duplicates=duplicates, total_dupe_leads=total_dupe_leads,
                           clusters=clusters, dept=dept)


# ---------------------------------------------------------------------------
# Merge true duplicates (same property)
# ---------------------------------------------------------------------------

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
    """Two-phase: first POST previews; confirm=1 executes the merge into the survivor.
    Guards against merging leads at DIFFERENT addresses (those are separate projects)."""
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

    # Safety: refuse to merge leads that sit at different real addresses.
    surv_key = _project_key(survivor)
    for did in dupe_ids:
        d = db.get("leads", did)
        if d and not surv_key.startswith("noaddr:") and not _project_key(d).startswith("noaddr:") \
           and _project_key(d) != surv_key:
            flash("Those leads are at different properties — that's a repeat client with "
                  "separate projects, not a duplicate. Use “Link under one client” instead.",
                  "error")
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


# ---------------------------------------------------------------------------
# Link a repeat client's leads under ONE client contact (no leads merged)
# ---------------------------------------------------------------------------

def _resolve_client_contact(members):
    """Pick the surviving client contact for a repeat-client cluster and the
    duplicate contact records to fold into it. Survivor = the contact of the most
    advanced lead that has one; otherwise the lowest distinct contact id; if no lead
    has a contact, one is created from the survivor lead."""
    contact_ids = []
    for m in members:  # members are already survivor-sorted
        cid = m.get("contact_id")
        if cid and cid not in contact_ids:
            contact_ids.append(cid)
    if contact_ids:
        survivor_contact = contact_ids[0]
        created = False
    else:
        lead = members[0]
        parts = (lead.get("name") or "").split()
        survivor_contact = db.insert("contacts", {
            "kind": "person",
            "first_name": parts[0] if parts else (lead.get("name") or "Client"),
            "last_name": " ".join(parts[1:]) if len(parts) > 1 else "",
            "phone": lead.get("phone") or "", "email": lead.get("email") or "",
            "source": "Repeat client (lead dedupe)"})
        created = True
    dupe_contacts = [c for c in contact_ids if c != survivor_contact]
    return survivor_contact, dupe_contacts, created


@bp.route("/link-client", methods=["POST"])
def link_client():
    """Two-phase: link all of a repeat client's leads under one client contact.
    Consolidates the duplicate contact shells (re-pointing their leads/jobs) and
    points any contact-less leads at the survivor. NO leads/projects are deleted."""
    from modules import contacts as contacts_mod
    member_ids = [int(x) for x in request.form.getlist("member_ids") if x.isdigit()]
    members = [db.get("leads", i) for i in member_ids]
    members = [m for m in members if m]
    if len(members) < 2:
        flash("Nothing to link.", "error")
        return redirect(url_for("leads_dedupe.index"))
    members.sort(key=_survivor_rank, reverse=True)

    if request.form.get("confirm"):
        survivor_contact, dupe_contacts, created = _resolve_client_contact(members)
        if dupe_contacts:
            contacts_mod._do_merge(survivor_contact, dupe_contacts)  # re-points leads+jobs
        # Any lead still without a contact (or a created-fresh contact) → attach it.
        linked = 0
        for m in members:
            if (db.get("leads", m["id"]) or {}).get("contact_id") != survivor_contact:
                db.update("leads", m["id"], contact_id=survivor_contact)
                linked += 1
        db.add_activity("contact", survivor_contact, "note",
                        "Linked %d leads (separate projects) under this client; "
                        "consolidated %d duplicate contact record(s)." % (len(members), len(dupe_contacts)))
        flash("Linked %d leads under one client%s. %d duplicate contact record(s) consolidated." % (
            len(members), " (new client contact created)" if created else "", len(dupe_contacts)), "ok")
        return redirect(url_for("contacts.detail", contact_id=survivor_contact))

    # Preview
    survivor_contact, dupe_contacts, created = _resolve_client_contact(members)
    survivor_c = db.get("contacts", survivor_contact) if not created else None
    projects = sorted({(m.get("address") or "(no address)").strip() for m in members})
    return render_template("lead_link_preview.html", members=members, projects=projects,
                           survivor_c=survivor_c, dupe_count=len(dupe_contacts),
                           created=created, member_ids=member_ids)
