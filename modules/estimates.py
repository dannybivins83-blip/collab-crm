# -*- coding: utf-8 -*-
"""Estimates — AccuLynx-style: Estimate → Sections (narrative scope) → cost lines,
with the Cost / Price / Profit-Margin model. Price = Cost / (1 - margin)."""
import re

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, abort

import db
import theme
import constants

bp = Blueprint("estimates", __name__, url_prefix="/estimates")


def _require_estimate(est_id):
    """Fetch estimate and verify caller's department owns the parent job/lead. Aborts 404/403."""
    e = db.get("estimates", est_id)
    if not e:
        abort(404)
    from modules.auth import current_user as _cu
    from theme import current_department
    u = _cu() or {}
    if u.get("role") == "admin":
        return e
    dept = current_department()
    parent = None
    if e.get("job_id"):
        parent = db.get("jobs", e["job_id"])
    elif e.get("lead_id"):
        parent = db.get("leads", e["lead_id"])
    if parent and parent.get("department") != dept:
        abort(403)
    return e


# ---- money math (margin model, mirrors AccuLynx) --------------------------

def line_cost(l):
    return (l.get("qty") or 0) * (1 + (l.get("waste_pct") or 0) / 100.0) * (l.get("cost") or 0)


def _margin_price(cost, margin_pct):
    m = (margin_pct or 0) / 100.0
    if m >= 0.99:
        m = 0.99
    return cost / (1 - m) if (1 - m) else cost


def line_price(l, margin_pct):
    """Use the stored per-line price if set (manual override); else derive from margin."""
    if l.get("price"):
        return l["price"]
    return _margin_price(line_cost(l), margin_pct)


def estimate_totals(est, sections):
    # Upgrade OPTION groups are a customer-facing menu — priced for display but kept
    # OUT of the running total until a rep explicitly accepts one (otherwise auto-filled
    # upgrade lines silently inflate the estimate). Base scope only drives the total.
    base = [s for s in sections if not s.get("_is_option")]
    cost = sum(s["_cost"] for s in base)
    subtotal = sum(s["_price"] for s in base)
    tax = subtotal * (est.get("tax_pct") or 0) / 100.0
    total = subtotal + tax
    net = total - cost
    margin = (net / total * 100.0) if total else 0
    return {"cost": cost, "subtotal": subtotal, "tax": tax, "total": total,
            "net": net, "margin": margin}


def _load_sections(est_id):
    sections = db.all_rows("estimate_sections", "estimate_id=?", (est_id,), "sort, id")
    for s in sections:
        lines = db.all_rows("estimate_lines", "section_id=?", (s["id"],), "sort, id")
        for l in lines:
            l["_cost"] = line_cost(l)
            l["_price"] = line_price(l, s.get("margin_pct"))
        s["_lines"] = lines
        s["_cost"] = sum(l["_cost"] for l in lines)
        s["_price"] = sum(l["_price"] for l in lines)
        # Option/upgrade groups carry the "Declined / Accepted" marker in their scope_text
        # (added to every upgrade group in build_estimate). Flag them so estimate_totals
        # keeps the priced menu out of the running total.
        s["_is_option"] = "Declined" in (s.get("scope_text") or "")
    return sections


def _draws(total):
    return [{"label": p["label"], "amount": (total * p["pct"] if p["pct"] else None)}
            for p in constants.DRAW_SCHEDULE]


def _next_number():
    # Highest existing EST- number + 1 (not the row id) so deletes can't collide.
    # Serialized write txn prevents duplicate numbers under concurrent inserts (dual-engine).
    conn = db.begin_immediate(lock_table="estimates")
    try:
        mx = 0
        for r in conn.execute("SELECT number FROM estimates").fetchall():
            n = (r["number"] or "")
            if n.startswith("EST-"):
                try:
                    mx = max(mx, int(n[4:]))
                except Exception:
                    pass
        nxt = "EST-%04d" % (mx + 1)
        conn.commit()
        return nxt
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---- routes ---------------------------------------------------------------

@bp.route("/")
def index():
    # Scope to the current department via each estimate's parent lead/job.
    from theme import current_department
    dept = current_department()
    dept_leads = {l["id"] for l in db.all_rows("leads", "department=?", (dept,))}
    dept_jobs = {j["id"] for j in db.all_rows("jobs", "department=?", (dept,))}
    _parts, _params = ["(lead_id IS NULL AND job_id IS NULL)"], []
    if dept_leads:
        _ph = ",".join("?" * len(dept_leads))
        _parts.append("lead_id IN (%s)" % _ph)
        _params.extend(dept_leads)
    if dept_jobs:
        _ph = ",".join("?" * len(dept_jobs))
        _parts.append("job_id IN (%s)" % _ph)
        _params.extend(dept_jobs)
    estimates = db.all_rows("estimates", " OR ".join(_parts), tuple(_params), "id DESC")
    if not estimates:
        return render_template("estimates.html", estimates=[], q="", status_f="", statuses=[])
    # Batch-compute totals with a single IN() query per table instead of one
    # _load_sections() call per estimate (was 1 + 2×N queries for N estimates).
    est_ids = [e["id"] for e in estimates]
    id_ph = ",".join("?" * len(est_ids))
    conn = db.connect()
    try:
        # Fetch all sections + lines for these estimates in 2 queries (vs 1+2N before).
        all_sections = conn.execute(
            "SELECT * FROM estimate_sections WHERE estimate_id IN (%s) ORDER BY sort, id" % id_ph,
            est_ids).fetchall()
        sec_ids = [s["id"] for s in all_sections]
        all_lines = {}
        if sec_ids:
            line_ph = ",".join("?" * len(sec_ids))
            for ln in conn.execute(
                "SELECT * FROM estimate_lines WHERE section_id IN (%s) ORDER BY sort, id" % line_ph,
                sec_ids).fetchall():
                all_lines.setdefault(ln["section_id"], []).append(ln)
    finally:
        conn.close()
    # Roll up totals in Python using the same line_cost / line_price logic.
    price_map = {}
    for s in all_sections:
        if "Declined" in (s["scope_text"] or ""):
            continue  # option section — excluded from total
        lines = all_lines.get(s["id"], [])
        sec_price = sum(line_price(dict(ln), s["margin_pct"]) for ln in lines)
        price_map[s["estimate_id"]] = price_map.get(s["estimate_id"], 0.0) + sec_price
    for e in estimates:
        subtotal = price_map.get(e["id"], 0.0)
        tax = subtotal * (e.get("tax_pct") or 0) / 100.0
        e["_total"] = subtotal + tax
    # Statuses from the full dept-scoped set (before search filter) so the dropdown
    # always shows every reachable status, not just the currently-visible ones.
    statuses = sorted({e.get("status") for e in estimates if e.get("status")})
    q = request.args.get("q", "").strip().lower()
    status_f = request.args.get("status", "").strip()
    if q:
        estimates = [e for e in estimates if
                     q in (e.get("number") or "").lower() or
                     q in (e.get("title") or "").lower() or
                     q in (e.get("work_type") or "").lower()]
    if status_f:
        estimates = [e for e in estimates if e.get("status") == status_f]
    return render_template("estimates.html", estimates=estimates,
                           q=q, status_f=status_f, statuses=statuses)


def _resolve_template(template_id, work_type):
    """Return (name, work_type, scope_text, lines[{description,unit,qty,cost}]) from the
    DB templates table, or fall back to the code defaults."""
    row = None
    if template_id:
        row = db.get("templates", template_id)
    if not row:
        # best-fit by work type
        wt = (work_type or "").strip()
        matches = db.all_rows("templates", "work_type=?", (wt,)) if wt else []
        row = matches[0] if matches else None
    if row:
        return (row["name"], row["work_type"], row.get("scope_text", ""),
                db.load_json(row.get("lines"), []))
    key = constants.template_for_work_type(work_type)
    tpl = constants.ESTIMATE_TEMPLATES.get(key, constants.ESTIMATE_TEMPLATES["blank"])
    lines = [{"description": l["desc"], "unit": l["unit"], "qty": l.get("qty", 0),
              "cost": l["price"], "q": l.get("q"), "sec": l.get("sec")} for l in tpl["lines"]]
    return (tpl["name"], work_type, constants.scope_for_template(key), lines)


@bp.route("/new", methods=["GET", "POST"])
def new():
    if request.method == "POST":
        work_type = request.form.get("work_type", "")
        template_id = request.form.get("template_id") or None
        # Single source of truth: build_estimate injects the base scope section AND the
        # "Upgrades & Options" option groups (qty 0 accept/decline menu). Doing the insert
        # here by hand previously skipped the upgrade menu entirely (the EST-0154 bug).
        eid = build_estimate(
            lead_id=request.form.get("lead_id") or None,
            job_id=request.form.get("job_id") or None,
            template_id=template_id, work_type=work_type)
        # Honor an explicit title typed on the New form (build_estimate defaults to the
        # lead/job/template name); the form is the only path that offers a title field.
        title = (request.form.get("title") or "").strip()
        if title:
            db.update("estimates", eid, title=title)
        name = _resolve_template(template_id, work_type)[0]
        flash("Estimate created from %s — base scope + system upgrades." % name, "ok")
        return redirect(url_for("estimates.detail", est_id=eid))
    pre = {}
    if request.args.get("lead_id"):
        l = db.get("leads", request.args["lead_id"])
        if l:
            pre = {"lead_id": l["id"], "contact_id": l.get("contact_id"),
                   "work_type": l.get("work_type"), "title": l.get("name")}
    elif request.args.get("job_id"):
        j = db.get("jobs", request.args["job_id"])
        if j:
            pre = {"job_id": j["id"], "contact_id": j.get("contact_id"),
                   "work_type": j.get("work_type"), "title": j.get("name")}
    return render_template("estimate_new.html", pre=pre,
                           templates=db.all_rows("templates", order="name"))


def _apply_measurement(est_id, m):
    """Server-side mirror of the estimate builder's 'Apply measurements' — fills line
    quantities from a roof measurement so a quick estimate is ready immediately."""
    if not m:
        return
    def num(k):
        try:
            return float(m.get(k) or 0)
        except Exception:
            return 0.0
    sq = num("squares")
    sqW = sq * (1 + num("waste_pct") / 100.0)
    ridgehip = num("ridge_lf") + num("hip_lf")
    ridge = ridgehip  # alias used by the keyword fallback below
    valley = num("valley_lf")
    rake = num("rake_lf")
    eave = num("eave_lf")
    drip = eave + rake
    # Drivers for the per-line AccuLynx-mirror formulas (qrule).
    DRV = {"sq": sqW, "deck": sq, "ridgehip": ridgehip, "rake": rake, "valley": valley,
           "eave": eave, "driprake": drip, "ridgehipvalley": ridgehip + valley}

    def eval_qrule(rule):
        """Return the computed qty for a stored qrule dict, or None if it can't."""
        if not isinstance(rule, dict):
            return None
        if "fixed" in rule:
            return float(rule["fixed"])
        if "lf" in rule:
            return DRV.get(rule["lf"], 0) * float(rule.get("c", 1.0))
        for k in ("sq", "deck"):
            if k in rule:
                return DRV[k] * float(rule[k])
        return None

    for ln in db.all_rows("estimate_lines", "estimate_id=?", (est_id,)):
        d = (ln.get("description") or "").lower()
        # AccuLynx-mirror lines carry an explicit formula — use it verbatim (even for
        # upgrade options like Premium/Color Coat tile, which auto-fill to the squares).
        rule = db.load_json(ln.get("qrule"), None) if ln.get("qrule") else None
        if rule is not None:
            qv = eval_qrule(rule)
            if qv is not None:
                db.update("estimate_lines", ln["id"], qty=round(qv, 2))
            continue
        if d.startswith("upgrade") or d.startswith("add-on"):
            continue  # optional upgrades (no formula) stay at qty 0 until the rep turns them on
        u = (ln.get("unit") or "").upper()
        if u == "LS":
            # lump-sum lines (permit, dumpster) are always qty 1 — never square-driven
            if float(ln.get("qty") or 0) != 1:
                db.update("estimate_lines", ln["id"], qty=1)
            continue

        q = None
        if re.search(r"ridge|hip", d):
            q = ridge
        elif "valley" in d:
            q = valley
        elif re.search(r"drip edge|eave|rake", d):
            q = drip
        elif u == "SQ" or re.search(r"tear ?off|deck|re-?nail|underlay|shingle|tile|membrane|\biso\b|base sheet|cap|gravel", d):
            # Tear-off and re-nail/re-deck are billed by actual deck area (no
            # material waste); everything else carries the waste factor.
            q = sq if re.search(r"tear|re-?nail|re-?deck", d) else sqW
        if q and q > 0:
            db.update("estimate_lines", ln["id"], qty=round(q, 2))


def build_estimate(lead_id=None, job_id=None, template_id=None, work_type="", apply_meas=True):
    """Create a draft estimate from the matching system template: a base scope
    section + an 'Upgrades & Options' section (every system upgrade, qty 0 until the
    rep turns it on), prefilled from the lead/job, with measurements applied. Returns
    the new estimate id. Shared by the quick button, the New form, and lead entry."""
    name, wt, scope, lines = _resolve_template(template_id, work_type)
    title, contact_id = name, None
    if lead_id:
        l = db.get("leads", lead_id)
        if l:
            title = l.get("name") or name
            contact_id = l.get("contact_id")
            work_type = work_type or l.get("work_type") or ""
    elif job_id:
        j = db.get("jobs", job_id)
        if j:
            title = j.get("name") or name
            contact_id = j.get("contact_id")
            work_type = work_type or j.get("work_type") or ""
    eid = db.insert("estimates", {
        "number": _next_number(), "title": title, "job_id": job_id, "lead_id": lead_id,
        "contact_id": contact_id, "work_type": work_type, "template_key": template_id or "",
        "status": "draft", "margin_pct": 30, "tax_pct": 0, "terms": db.get_company().get("terms", "")})
    # Group template lines into named sections; fall back to a single section (template name)
    # for templates with no "sec" keys (blank, repair, DB-loaded templates without sections).
    sec_names = []
    sec_line_map = {}
    for line in lines:
        sn = line.get("sec") or name
        if sn not in sec_line_map:
            sec_names.append(sn)
            sec_line_map[sn] = []
        sec_line_map[sn].append(line)
    if not sec_names:
        db.insert("estimate_sections", {"estimate_id": eid, "sort": 0, "name": name,
                                        "scope_text": scope, "margin_pct": 30})
        nsec = 1
    else:
        for si, sn in enumerate(sec_names):
            sid = db.insert("estimate_sections", {
                "estimate_id": eid, "sort": si, "name": sn,
                "scope_text": scope if si == 0 else "", "margin_pct": 30})
            for i, line in enumerate(sec_line_map[sn]):
                db.insert("estimate_lines", {"estimate_id": eid, "section_id": sid, "sort": i,
                                             "description": line.get("description", ""),
                                             "unit": line.get("unit", "EA"), "qty": line.get("qty", 0),
                                             "waste_pct": 0, "cost": line.get("cost", 0),
                                             "qrule": db.dump_json(line["q"]) if line.get("q") else ""})
        nsec = len(sec_names)
    # Upgrade OPTION GROUPS (AccuLynx-style): each upgrade is its own collapsible section
    # with a customer-facing scope + a Declined/Accepted line + its line items. Falls back
    # to the template's own work type (wt) so tile/metal/flat never get generic upgrades.
    groups = constants.upgrade_groups(work_type or wt or (template_id or ""))
    for gi, g in enumerate(groups):
        scope_text = (g.get("scope") or "")
        if "Declined" not in scope_text:
            scope_text += constants._ACCEPT_LINE
        gsid = db.insert("estimate_sections", {
            "estimate_id": eid, "sort": nsec + gi, "name": g["name"], "margin_pct": 30,
            "scope_text": scope_text})
        for i, u in enumerate(g.get("lines", [])):
            db.insert("estimate_lines", {"estimate_id": eid, "section_id": gsid, "sort": i,
                                         "description": u.get("desc", ""), "unit": u.get("unit", "EA"),
                                         "qty": u.get("qty", 0), "waste_pct": 0, "cost": u.get("cost", 0),
                                         "qrule": db.dump_json(u["q"]) if u.get("q") else ""})
    if apply_meas:
        from modules import measurements as meas
        m = meas.for_lead(lead_id) if lead_id else (meas.for_job(job_id) if job_id else None)
        _apply_measurement(eid, m)
    return eid


@bp.route("/quick", methods=["POST"])
def quick():
    """One-click estimate from a template (with upgrades + measurements)."""
    eid = build_estimate(lead_id=request.form.get("lead_id") or None,
                         job_id=request.form.get("job_id") or None,
                         template_id=request.form.get("template_id") or None,
                         work_type=request.form.get("work_type", ""))
    flash("Quick estimate created — base scope + system upgrades, measurements applied.", "ok")
    return redirect(url_for("estimates.detail", est_id=eid))


@bp.route("/<int:est_id>")
def detail(est_id):
    e = _require_estimate(est_id)
    sections = _load_sections(est_id)
    totals = estimate_totals(e, sections)
    from modules import measurements as meas
    measurement = None
    if e.get("job_id"):
        measurement = meas.for_job(e["job_id"])
    if not measurement and e.get("lead_id"):
        measurement = meas.for_lead(e["lead_id"])
    try:
        catalog = db.all_rows("material_catalog", order="name")
    except Exception:
        catalog = []
    return render_template("estimate_detail.html", e=e, sections=sections, totals=totals,
                           draws=_draws(totals["total"]), measurement=measurement,
                           scope_templates=constants.SCOPE_TEMPLATES, catalog=catalog,
                           job=db.get("jobs", e["job_id"]) if e.get("job_id") else None,
                           lead=db.get("leads", e["lead_id"]) if e.get("lead_id") else None)


@bp.route("/<int:est_id>/save", methods=["POST"])
def save(est_id):
    _require_estimate(est_id)
    data = request.get_json(silent=True) or {}
    # Wrap the multi-step delete → insert in a single serialized transaction so a
    # crash mid-save can't leave the estimate with no sections/lines (half-written state).
    conn = db.begin_immediate()
    try:
        conn.execute(
            "UPDATE estimates SET title=?,tax_pct=?,notes=?,terms=? WHERE id=?",
            (data.get("title", ""), float(data.get("tax_pct") or 0),
             data.get("notes", ""), data.get("terms", ""), est_id))
        conn.execute("DELETE FROM estimate_sections WHERE estimate_id=?", (est_id,))
        conn.execute("DELETE FROM estimate_lines WHERE estimate_id=?", (est_id,))
        for si, sec in enumerate(data.get("sections", [])):
            cur = conn.execute(
                "INSERT INTO estimate_sections (estimate_id,sort,name,scope_text,margin_pct) "
                "VALUES (?,?,?,?,?)",
                (est_id, si, sec.get("name", ""), sec.get("scope_text", ""),
                 float(sec.get("margin_pct") or 0)))
            sid = cur.lastrowid
            for li, ln in enumerate(sec.get("lines", [])):
                if not (ln.get("description") or "").strip():
                    continue
                conn.execute(
                    "INSERT INTO estimate_lines "
                    "(estimate_id,section_id,sort,description,unit,qty,waste_pct,cost,price) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (est_id, sid, li, ln.get("description", ""), ln.get("unit", "EA"),
                     float(ln.get("qty") or 0), float(ln.get("waste_pct") or 0),
                     float(ln.get("cost") or 0), float(ln.get("price") or 0)))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return jsonify({"ok": True})


@bp.route("/<int:est_id>/status", methods=["POST"])
def status(est_id):
    _require_estimate(est_id)
    st = request.form.get("status")
    if st in ("draft", "sent", "signed", "declined"):
        db.update("estimates", est_id, status=st)
        flash("Marked %s." % st, "ok")
    return redirect(url_for("estimates.detail", est_id=est_id))


@bp.route("/<int:est_id>/sign", methods=["POST"])
def sign(est_id):
    _require_estimate(est_id)
    name = request.form.get("signed_name", "")
    sig = request.form.get("signature", "")
    when = db.now()
    consent = "1" if request.form.get("consent") else ""
    db.update("estimates", est_id, status="signed", signed_name=name,
              signed_at=when, signature=sig)
    e = db.get("estimates", est_id)
    # With consent, store the signature on the job/lead so it auto-applies to the
    # sign-up documents and permit packet too (one signature, applied everywhere).
    for et, eid in (("job", e.get("job_id")), ("lead", e.get("lead_id"))):
        if eid:
            if consent and sig:
                db.update(et + "s", eid, signature=sig, signed_name=name,
                          signed_at=when, sign_consent=consent)
            db.add_activity(et, eid, "automation",
                            "Estimate %s e-signed by %s%s" % (
                                e["number"], name,
                                " — signature authorized for sign-up docs + permit packet"
                                if consent else ""))
    return jsonify({"ok": True})


@bp.route("/<int:est_id>/print")
def print_view(est_id):
    e = _require_estimate(est_id)
    sections = _load_sections(est_id)
    totals = estimate_totals(e, sections)
    return render_template("estimate_print.html", e=e, sections=sections, totals=totals,
                           draws=_draws(totals["total"]))


@bp.route("/<int:est_id>/delete", methods=["POST"])
def delete(est_id):
    _require_estimate(est_id)
    db.delete("estimates", est_id)
    db.execute("DELETE FROM estimate_sections WHERE estimate_id=?", (est_id,))
    db.execute("DELETE FROM estimate_lines WHERE estimate_id=?", (est_id,))
    flash("Estimate deleted.", "ok")
    return redirect(url_for("estimates.index"))
