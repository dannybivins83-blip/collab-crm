# -*- coding: utf-8 -*-
"""Sales pipeline (leads) — Kanban board, detail, drag-to-advance, convert-to-job."""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify

import db
import theme
import constants
from theme import current_department

bp = Blueprint("leads", __name__, url_prefix="/leads")

# AccuLynx "Create New Lead" parity — extra columns on the lead record. Added
# idempotently so local SQLite and Neon both pick them up without a migration.
# (`rank` is ensured in modules/customfields.py; reused here for Lead Rank.)
for _col, _decl in [
    ("company", "TEXT"), ("cross_ref", "TEXT"),
    ("priority", "TEXT DEFAULT 'Normal'"),
    ("phone_type", "TEXT"), ("phone_ext", "TEXT"), ("sms_opt", "INTEGER DEFAULT 0"),
    ("phone2", "TEXT"), ("phone2_type", "TEXT"), ("email_type", "TEXT"),
    ("mail_street", "TEXT"), ("mail_city", "TEXT"), ("mail_state", "TEXT"), ("mail_zip", "TEXT"),
]:
    db._ensure_column("leads", _col, _decl)

EDITABLE = ["rid", "name", "company", "cross_ref", "phone", "email", "address",
            "work_type", "rep", "source", "estimate", "narrative", "todo", "notes",
            "next_follow", "external_url", "contact_id",
            "priority", "phone_type", "phone_ext", "phone2", "phone2_type", "email_type",
            "mail_street", "mail_city", "mail_state", "mail_zip"]


def _rank_val(raw):
    """Clamp the Lead Rank input to AccuLynx's 0–4 scale (blank → 0)."""
    try:
        return max(0, min(4, int(raw or 0)))
    except (TypeError, ValueError):
        return 0


def _decorate(l):
    sd = constants.lead_stage(l["stage"])
    fs = theme.follow_status(sd, l.get("last_contact") or l.get("created"), l.get("snooze_until"))
    checks = db.load_json(l.get("checks"), {})
    done = sum(1 for i in range(len(sd["checklist"])) if checks.get("%s:%d" % (sd["key"], i)))
    l["_stage"] = sd
    l["_fs"] = fs
    l["_checks"] = checks
    l["_done"] = done
    l["_total"] = len(sd["checklist"])
    l["_pct"] = round(100 * done / len(sd["checklist"])) if sd["checklist"] else 0
    return l


@bp.route("/")
def board():
    leads = [_decorate(l) for l in db.all_rows("leads", "department=?", (current_department(),))]
    sort = request.args.get("sort", "clock")
    if sort == "est":
        leads.sort(key=lambda l: -theme.est_num(l.get("estimate")))
    elif sort == "name":
        leads.sort(key=lambda l: (l.get("name") or "").lower())
    else:
        leads.sort(key=lambda l: -l["_fs"]["days"])
    cols = []
    grand = 0
    overdue = 0
    for s in constants.LEAD_STAGES:
        items = [l for l in leads if l["stage"] == s["key"]]
        tot = sum(theme.est_num(l.get("estimate")) for l in items)
        if s["key"] not in ("won", "lost"):
            grand += tot
            overdue += sum(1 for l in items if l["_fs"]["level"] == "hot")
        cols.append({"stage": s, "cards": items, "total": tot})
    return render_template("leads_board.html", cols=cols, grand=grand, overdue=overdue, sort=sort)


@bp.route("/list")
def list_view():
    """AccuLynx 'Assigned Leads & Jobs' style list with a milestone filter sidebar."""
    leads = [_decorate(l) for l in db.all_rows("leads", "department=?", (current_department(),))]
    stage_f = request.args.get("stage")
    bucket = request.args.get("bucket")
    q = (request.args.get("q") or "").strip().lower()
    counts = {s["key"]: 0 for s in constants.LEAD_STAGES}
    for l in leads:
        counts[l["stage"]] = counts.get(l["stage"], 0) + 1
    rows = leads
    if stage_f:
        rows = [l for l in rows if l["stage"] == stage_f]
    if bucket:
        rows = [l for l in rows if l["_stage"].get("bucket") == bucket]
    if q:
        rows = [l for l in rows if q in ((l.get("name") or "") + (l.get("address") or "") +
                                         (l.get("rid") or "")).lower()]
    rows.sort(key=lambda l: -l["_fs"]["days"])
    return render_template("leads_list.html", rows=rows, counts=counts, stage_f=stage_f, q=q,
                           total=len(leads))


@bp.route("/new", methods=["GET", "POST"])
def new():
    if request.method == "POST":
        data = {f: request.form.get(f, "").strip() for f in EDITABLE}
        if not data.get("contact_id"):
            data["contact_id"] = None  # integer FK: blank "" is invalid in Postgres
        data["sms_opt"] = 1 if request.form.get("sms_opt") else 0  # "Opt in Texting/SMS"
        data["priority"] = data.get("priority") or "Normal"
        data["rank"] = _rank_val(request.form.get("rank"))  # Lead Rank (Notes/Tools)
        data["stage"] = request.form.get("stage", constants.LEAD_DEFAULT_STAGE)
        data["stage_since"] = db.today()
        data["last_contact"] = db.today()
        data["checks"] = "{}"
        data["department"] = current_department()
        lid = db.insert("leads", data)
        db.add_activity("lead", lid, "stage", "Lead created in %s" % constants.lead_stage(data["stage"])["name"])
        # Auto-resolve the permit office (AHJ) from the address + the roof system
        # from the work type, so it's ready to drive the permit when this lead sells.
        from modules import ahj as ahj_mod
        resolved_ahj = ahj_mod.resolve_ahj(data.get("address", ""), "", db.get_company().get("default_county", ""))
        system = ahj_mod.work_type_to_system(data.get("work_type", ""))
        db.update("leads", lid, ahj=resolved_ahj, county=db.get_company().get("default_county", ""), system=system)
        if resolved_ahj:
            db.add_activity("lead", lid, "automation",
                            "AHJ auto-set to %s%s" % (resolved_ahj, (" · system: " + system) if system else ""))
        # Auto-build a starter estimate from the matching system template (base
        # scope + every system upgrade at qty 0) the moment a work type is set.
        est_msg = ""
        if data.get("work_type"):
            try:
                from modules import estimates as est_mod
                eid = est_mod.build_estimate(lead_id=lid, work_type=data["work_type"])
                e = db.get("estimates", eid)
                db.add_activity("lead", lid, "automation",
                                "Estimate %s auto-created from %s template (with upgrades)" % (
                                    (e or {}).get("number", ""), data["work_type"]))
                est_msg = " · estimate %s drafted" % (e or {}).get("number", "")
            except Exception:
                pass
        # Populate the Communications tab with the intake details (verified facts only),
        # so the timeline starts with a record of what came in at lead entry.
        bits = []
        if data.get("phone"):
            bits.append("Phone: %s" % data["phone"])
        if data.get("email"):
            bits.append("Email: %s" % data["email"])
        if data.get("address"):
            bits.append("Property: %s" % data["address"])
        if data.get("work_type"):
            bits.append("Work type: %s" % data["work_type"])
        if resolved_ahj:
            bits.append("AHJ: %s%s" % (resolved_ahj, (" · " + system) if system else ""))
        if data.get("source"):
            bits.append("Source: %s" % data["source"])
        if data.get("rep"):
            bits.append("Rep: %s" % data["rep"])
        summary = "New lead intake — %s%s.\n%s" % (
            data.get("name") or "lead",
            (" (" + data["company"] + ")") if data.get("company") else "",
            " · ".join(bits))
        for extra in (data.get("notes"), data.get("narrative")):
            if extra and extra.strip():
                summary += "\nNotes: %s" % extra.strip()
        db.add_activity("lead", lid, "note", summary)
        # Auto-create a Gmail DRAFT notifying the team of the new lead. DRAFT ONLY —
        # never auto-sent (house rule); Danny/Jacin review and send from Gmail.
        notify_msg = ""
        try:
            from flask import session as _session
            from modules import gmail as _gmail
            uid = _session.get("user_id")
            notify_to = (db.get_company().get("lead_notify_to")
                         or "jacin@seabreezeroof.com, dannyb@seabreezeroof.com")
            if uid:
                link = url_for("leads.detail", lead_id=lid, _external=True)
                subj = "New Lead: %s%s%s" % (
                    data.get("name") or "lead",
                    (" — " + data["work_type"]) if data.get("work_type") else "",
                    (" — " + data["address"]) if data.get("address") else "")
                did = _gmail.create_draft(uid, notify_to, subj, summary + "\n\nOpen in CRM: " + link)
                if did:
                    db.add_activity("lead", lid, "draft",
                                    "Team-notification draft created for %s — review & send in Gmail." % notify_to)
                    notify_msg = " · team draft ready in Gmail"
        except Exception:
            pass
        flash("Lead created. AHJ: %s%s%s%s" % (
            resolved_ahj or "—", (" · " + system) if system else "", est_msg, notify_msg), "ok")
        return redirect(url_for("leads.detail", lead_id=lid))
    return render_template("lead_form.html", lead={}, contacts=db.all_rows("contacts", order="last_name"),
                           mode="new")


@bp.route("/<int:lead_id>")
def detail(lead_id):
    l = db.get("leads", lead_id)
    if not l:
        return redirect(url_for("leads.board"))
    _decorate(l)
    from modules import measurements as meas
    # Quick Estimate is scoped to THIS client's system (the work type tagged at lead
    # entry) — show only the matching template(s), not the whole catalog.
    key = constants.template_for_work_type(l.get("work_type") or "")
    quick_templates = [t for t in db.all_rows("templates", order="name")
                       if constants.template_for_work_type(t.get("work_type") or "") == key
                       and key not in ("blank",)]
    return render_template("lead_detail.html", l=l,
                           activity=db.entity_activity("lead", lead_id),
                           estimates=db.all_rows("estimates", "lead_id=?", (lead_id,)),
                           measurement=meas.for_lead(lead_id),
                           quick_templates=quick_templates,
                           documents=db.all_rows("documents", "lead_id=?", (lead_id,)),
                           reps=[u["name"] for u in db.all_rows("users", "active=1", order="name")])


@bp.route("/<int:lead_id>/edit", methods=["GET", "POST"])
def edit(lead_id):
    l = db.get("leads", lead_id)
    if not l:
        return redirect(url_for("leads.board"))
    if request.method == "POST":
        data = {f: request.form.get(f, "").strip() for f in EDITABLE}
        data["sms_opt"] = 1 if request.form.get("sms_opt") else 0
        data["priority"] = data.get("priority") or "Normal"
        data["rank"] = _rank_val(request.form.get("rank"))
        db.update("leads", lead_id, **data)
        flash("Lead updated.", "ok")
        return redirect(url_for("leads.detail", lead_id=lead_id))
    return render_template("lead_form.html", lead=l, contacts=db.all_rows("contacts", order="last_name"),
                           mode="edit")


@bp.route("/<int:lead_id>/stage", methods=["POST"])
def set_stage(lead_id):
    stage = request.form.get("stage")
    if stage in constants.LEAD_STAGE_INDEX:
        db.update("leads", lead_id, stage=stage, stage_since=db.today())
        db.add_activity("lead", lead_id, "stage", "Moved to %s" % constants.lead_stage(stage)["name"])
        if request.form.get("ajax"):
            return jsonify({"ok": True})
        flash("Stage updated.", "ok")
    return redirect(url_for("leads.detail", lead_id=lead_id))


@bp.route("/<int:lead_id>/move", methods=["POST"])
def move(lead_id):
    """Drag-to-advance endpoint (AJAX)."""
    stage = (request.get_json(silent=True) or {}).get("stage") or request.form.get("stage")
    if stage in constants.LEAD_STAGE_INDEX:
        db.update("leads", lead_id, stage=stage, stage_since=db.today())
        db.add_activity("lead", lead_id, "stage", "Moved to %s" % constants.lead_stage(stage)["name"])
        return jsonify({"ok": True})
    return jsonify({"ok": False}), 400


@bp.route("/<int:lead_id>/touch", methods=["POST"])
def touch(lead_id):
    db.update("leads", lead_id, last_contact=db.today(), snooze_until="")
    db.add_activity("lead", lead_id, "call", "Logged touch (call/text/email).")
    if request.form.get("ajax"):
        return jsonify({"ok": True})
    flash("Touch logged.", "ok")
    return redirect(url_for("leads.detail", lead_id=lead_id))


@bp.route("/<int:lead_id>/check", methods=["POST"])
def check(lead_id):
    l = db.get("leads", lead_id)
    checks = db.load_json(l.get("checks"), {})
    key = request.form.get("key")
    checks[key] = not checks.get(key)
    db.update("leads", lead_id, checks=db.dump_json(checks))
    if request.form.get("ajax"):
        return jsonify({"ok": True, "checked": checks[key]})
    return redirect(url_for("leads.detail", lead_id=lead_id))


@bp.route("/<int:lead_id>/note", methods=["POST"])
def note(lead_id):
    text = request.form.get("text", "").strip()
    kind = request.form.get("kind", "note")
    if text:
        db.add_activity("lead", lead_id, kind, text)
        if kind in ("call", "email", "sms"):
            db.update("leads", lead_id, last_contact=db.today())
    return redirect(url_for("leads.detail", lead_id=lead_id))


@bp.route("/<int:lead_id>/field", methods=["POST"])
def field(lead_id):
    f = request.form.get("field")
    if f in EDITABLE + ["snooze_until"]:
        db.update("leads", lead_id, **{f: request.form.get("value", "")})
    return jsonify({"ok": True})


@bp.route("/<int:lead_id>/assign", methods=["POST"])
def assign(lead_id):
    rep = request.form.get("rep", "").strip()
    db.update("leads", lead_id, rep=rep)
    db.add_activity("lead", lead_id, "automation", "Assigned to %s" % (rep or "—"))
    flash("Lead assigned to %s." % (rep or "—"), "ok")
    return redirect(url_for("leads.detail", lead_id=lead_id))


@bp.route("/<int:lead_id>/convert", methods=["POST"])
def convert(lead_id):
    """Won → create a production Job from this lead."""
    l = db.get("leads", lead_id)
    if not l:
        return redirect(url_for("leads.board"))
    parts = [p.strip() for p in (l.get("address") or "").split(",")]
    # Auto-compose the canonical job number + name:  R-YY###: Client (AHJ) (RoofCode+Sq) (Rep)
    from modules import acculynx_sync as S
    from modules import measurements as _meas
    _m = _meas.for_lead(lead_id)
    _sq = (_m or {}).get("squares") or ""
    rid = S.next_job_number()
    job_name = S.compose_job_name(
        l.get("name"), ahj=l.get("ahj") or "", work_type=l.get("work_type") or "",
        system=l.get("system") or "", squares=_sq, rep=l.get("rep") or "", rid=rid)
    job = {
        "contact_id": l.get("contact_id"), "lead_id": lead_id,
        "rid": rid, "name": job_name,
        "phone": l.get("phone"), "email": l.get("email"),
        "address": parts[0] if parts else l.get("address"),
        "city": parts[1] if len(parts) > 1 else "",
        "work_type": l.get("work_type"), "rep": l.get("rep"), "source": l.get("source"),
        "stage": "approved", "stage_since": db.today(),
        "contract_value": l.get("estimate"), "narrative": l.get("narrative"),
        "county": l.get("county") or db.get_company().get("default_county", ""),
        "ahj": l.get("ahj") or "", "system": l.get("system") or "",
        "department": l.get("department") or current_department(),
    }
    jid = db.insert("jobs", job)
    # Auto-create the permit record (pre-filled from the lead's AHJ + roof system).
    from modules import ahj as ahj_mod
    p_ahj = l.get("ahj") or ahj_mod.resolve_ahj(l.get("address", ""), job.get("city", ""), job["county"])
    p_system = l.get("system") or ahj_mod.work_type_to_system(l.get("work_type", ""))
    if p_system:
        pid = db.insert("permits", {"job_id": jid, "ahj": p_ahj, "county": job["county"],
                                    "system": p_system, "status": "prep",
                                    "notes": "Auto-created from lead conversion (%s roof)" % p_system})
        db.add_activity("job", jid, "automation",
                        "Permit auto-created — AHJ %s · %s system" % (p_ahj or "—", p_system))
    # Carry the lead's RoofGraf measurement + documents onto the new job.
    from modules import measurements as meas
    m = meas.for_lead(lead_id)
    if m:
        db.update("measurements", m["id"], job_id=jid)
        if m.get("squares"):
            db.update("jobs", jid, area=str(m.get("squares")), slope=m.get("pitch") or "")
    db.execute("UPDATE documents SET job_id=? WHERE lead_id=?", (jid, lead_id))
    db.update("leads", lead_id, stage="won", stage_since=db.today())
    db.add_activity("lead", lead_id, "stage", "Won — converted to Job #%d" % jid)
    db.add_activity("job", jid, "stage", "Job created from Lead #%d" % lead_id)
    flash("Converted to job.", "ok")
    return redirect(url_for("jobs.detail", job_id=jid))


@bp.route("/<int:lead_id>/delete", methods=["POST"])
def delete(lead_id):
    db.delete("leads", lead_id)
    flash("Lead deleted.", "ok")
    return redirect(url_for("leads.board"))


# ---------------------------------------------------------------------------
# Automated single-lead intake (Part C prototype)
# Public, token-guarded webhook for inbound leads from email parsers, web forms,
# Craigslist relays, RingCentral call/voicemail events, etc. Unlike /import (a
# CORS-open bulk scraper feed), this runs the SAME enrichment as a hand-keyed
# lead: AHJ resolve + roof system + auto-starter-estimate. Drafts only — it never
# emails anyone. Set CRM_INTAKE_TOKEN to enable; unset = endpoint disabled.
# ---------------------------------------------------------------------------

def _default_intake_department():
    co = db.get_company() or {}
    depts = [d.strip() for d in (co.get("departments") or "").split(",") if d.strip()]
    return depts[0] if depts else "REROOF Department"


def _intake_authorized():
    import os
    want = os.environ.get("CRM_INTAKE_TOKEN")
    if not want:
        return None  # not configured
    got = request.headers.get("X-Intake-Token") or request.args.get("token") or ""
    return got == want


def _create_lead_from_intake(data):
    """Create contact + lead from a normalized intake dict, with the same AHJ +
    starter-estimate enrichment as leads.new. Dedupes by email or name+phone.
    Returns (lead_id, created_bool)."""
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    phone = (data.get("phone") or "").strip()
    # Dedupe: same email, or same name+phone, already in the pipeline.
    for l in db.all_rows("leads"):
        le = (l.get("email") or "").strip().lower()
        if email and le == email:
            return l["id"], False
        if name and phone and (l.get("name") or "").strip().lower() == name.lower() \
                and (l.get("phone") or "").strip() == phone:
            return l["id"], False
    dept = _default_intake_department()
    parts = [p.strip() for p in (data.get("address") or "").split(",")]
    cid = db.insert("contacts", {
        "kind": "person", "first_name": name.split(" ")[0] if name else "",
        "last_name": " ".join(name.split(" ")[1:]) if name else "",
        "email": email, "phone": phone,
        "address": parts[0] if parts else data.get("address", ""),
        "city": parts[1] if len(parts) > 1 else "",
        "state": "FL", "source": data.get("source", ""), "tags": "Auto-intake",
        "department": dept})
    lid = db.insert("leads", {
        "contact_id": cid, "name": name, "phone": phone, "email": email,
        "address": data.get("address", ""), "work_type": data.get("work_type", ""),
        "rep": data.get("rep") or "", "source": data.get("source", ""),
        "stage": constants.LEAD_DEFAULT_STAGE, "stage_since": db.today(),
        "last_contact": db.today(), "checks": "{}", "department": dept,
        "notes": data.get("notes", "")})
    db.add_activity("lead", lid, "automation",
                    "Lead auto-captured from %s" % (data.get("source") or "intake"))
    # AHJ + roof system, mirroring leads.new.
    try:
        from modules import ahj as ahj_mod
        county = db.get_company().get("default_county", "")
        resolved = ahj_mod.resolve_ahj(data.get("address", ""), "", county)
        system = ahj_mod.work_type_to_system(data.get("work_type", ""))
        db.update("leads", lid, ahj=resolved, county=county, system=system)
    except Exception:
        pass
    # Auto-starter estimate when a work type came through.
    if data.get("work_type"):
        try:
            from modules import estimates as est_mod
            eid = est_mod.build_estimate(lead_id=lid, work_type=data["work_type"])
            e = db.get("estimates", eid)
            db.add_activity("lead", lid, "automation",
                            "Estimate %s auto-drafted from %s template"
                            % ((e or {}).get("number", ""), data["work_type"]))
        except Exception:
            pass
    return lid, True


def _cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Intake-Token"
    return resp


@bp.route("/intake", methods=["POST", "OPTIONS"])
def intake():
    """Single normalized lead → enriched CRM lead. JSON body:
    {name, phone, email, address, work_type, source}."""
    if request.method == "OPTIONS":
        from flask import make_response
        return _cors(make_response("", 204))
    auth = _intake_authorized()
    if auth is None:
        return _cors(jsonify({"ok": False, "error": "intake disabled — set CRM_INTAKE_TOKEN"})), 503
    if not auth:
        return _cors(jsonify({"ok": False, "error": "bad token"})), 403
    from modules import lead_intake
    payload = request.get_json(force=True, silent=True) or {}
    data = lead_intake.normalize(payload)
    if not (data.get("name") or data.get("phone") or data.get("email")):
        return _cors(jsonify({"ok": False, "error": "need at least a name, phone, or email"})), 400
    lid, created = _create_lead_from_intake(data)
    return _cors(jsonify({"ok": True, "lead_id": lid, "created": created,
                          "url": url_for("leads.detail", lead_id=lid)}))


@bp.route("/intake/email", methods=["POST", "OPTIONS"])
def intake_email():
    """Raw inbound lead email → parsed + enriched CRM lead. JSON body:
    {from, subject, body}. Pair with a Gmail watcher or an email-relay webhook."""
    if request.method == "OPTIONS":
        from flask import make_response
        return _cors(make_response("", 204))
    auth = _intake_authorized()
    if auth is None:
        return _cors(jsonify({"ok": False, "error": "intake disabled — set CRM_INTAKE_TOKEN"})), 503
    if not auth:
        return _cors(jsonify({"ok": False, "error": "bad token"})), 403
    from modules import lead_intake
    payload = request.get_json(force=True, silent=True) or {}
    data = lead_intake.parse_email(payload.get("from", ""), payload.get("subject", ""),
                                   payload.get("body", ""))
    if not data:
        return _cors(jsonify({"ok": False, "error": "could not parse a lead from this email"})), 422
    lid, created = _create_lead_from_intake(data)
    return _cors(jsonify({"ok": True, "lead_id": lid, "created": created, "parsed": data,
                          "url": url_for("leads.detail", lead_id=lid)}))


@bp.route("/import", methods=["POST", "OPTIONS"])
def import_leads():
    """Bulk-import scraped AccuLynx prospects (JSON list). CORS-open so it can be
    POSTed from the AccuLynx tab. Dedupes by name; creates a contact + lead each."""
    import json
    from flask import make_response
    if request.method == "OPTIONS":
        r = make_response("", 204)
    else:
        records = request.get_json(force=True, silent=True)
        if records is None:
            try:
                records = json.loads(request.get_data(as_text=True) or "[]")
            except Exception:
                records = []
        existing = {(l.get("name") or "").strip().lower() for l in db.all_rows("leads")}
        added = 0
        for rec in (records or []):
            name = (rec.get("name") or "").strip()
            if not name or name.lower() in existing:
                continue
            parts = [p.strip() for p in (rec.get("address") or "").split(",")]
            cid = db.insert("contacts", {
                "kind": "person", "first_name": name.split(" ")[0],
                "last_name": " ".join(name.split(" ")[1:]),
                "email": rec.get("email", ""), "phone": rec.get("phone", ""),
                "address": parts[0] if parts else rec.get("address", ""),
                "city": parts[1] if len(parts) > 1 else "",
                "state": "FL", "source": rec.get("source", ""), "tags": "AccuLynx import"})
            db.insert("leads", {
                "contact_id": cid, "name": name, "phone": rec.get("phone", ""),
                "email": rec.get("email", ""), "address": rec.get("address", ""),
                "work_type": rec.get("work_type", ""), "rep": rec.get("rep") or "Danny Bivins",
                "source": rec.get("source", ""), "stage": "prospect",
                "stage_since": db.today(), "last_contact": db.today(),
                "external_url": rec.get("url", "")})
            existing.add(name.lower())
            added += 1
        r = jsonify({"ok": True, "added": added, "received": len(records or [])})
    r.headers["Access-Control-Allow-Origin"] = "*"
    r.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return r


@bp.route("/intake/ringcentral", methods=["POST", "OPTIONS"])
def intake_ringcentral():
    """RingCentral telephony / voicemail webhook ("Ping Central" on the build list).
    Inbound call or voicemail → log a call activity on the matching lead/contact by
    phone, or create a new lead (source RingCentral) if the caller is unknown.

    Register the webhook URL with ?token=<CRM_INTAKE_TOKEN> appended. RingCentral's
    one-time subscription handshake sends a Validation-Token header that we echo back
    (no auth needed for that ping)."""
    from flask import make_response
    # 1) Subscription validation handshake — echo the token, 200, done.
    vtok = request.headers.get("Validation-Token")
    if vtok:
        resp = make_response("", 200)
        resp.headers["Validation-Token"] = vtok
        return _cors(resp)
    if request.method == "OPTIONS":
        return _cors(make_response("", 204))
    auth = _intake_authorized()
    if auth is None:
        return _cors(jsonify({"ok": False, "error": "intake disabled — set CRM_INTAKE_TOKEN"})), 503
    if not auth:
        return _cors(jsonify({"ok": False, "error": "bad token"})), 403
    from modules import lead_intake
    payload = request.get_json(force=True, silent=True) or {}
    data = lead_intake.parse_ringcentral(payload)
    if not data:
        # Non-call event (e.g. message-count ping) — accept so RC doesn't retry.
        return _cors(jsonify({"ok": True, "ignored": True}))
    if (data.get("direction") or "Inbound") != "Inbound":
        return _cors(jsonify({"ok": True, "ignored": "outbound"}))
    phone = data["phone"]
    digits = "".join(ch for ch in phone if ch.isdigit())[-10:]
    # Try to link to an existing lead, then contact, by last-10-digits of phone.
    def _digits(v):
        return "".join(ch for ch in (v or "") if ch.isdigit())[-10:]
    lead = next((l for l in db.all_rows("leads") if _digits(l.get("phone")) == digits and digits), None)
    if lead:
        db.add_activity("lead", lead["id"], "call", data["notes"])
        return _cors(jsonify({"ok": True, "linked": "lead", "lead_id": lead["id"]}))
    contact = next((c for c in db.all_rows("contacts") if _digits(c.get("phone")) == digits and digits), None)
    if contact:
        db.add_activity("contact", contact["id"], "call", data["notes"])
        # Surface the call as a fresh lead too, so it lands in the pipeline.
    lid, created = _create_lead_from_intake({
        "name": data["name"], "phone": phone, "source": data["source"],
        "notes": data["notes"]})
    db.add_activity("lead", lid, "call", data["notes"])
    return _cors(jsonify({"ok": True, "linked": "new" if created else "existing",
                          "lead_id": lid, "created": created}))
