# -*- coding: utf-8 -*-
"""Shared helpers: brand context injection + follow-up clock + money math.

These mirror the follow-up engine and draw-schedule math from job-manager.html
so the Python board behaves the same as the original vanilla-JS board.
"""
import re
from datetime import datetime

import db
import constants


def days_between(a, b):
    try:
        da = datetime.strptime((a or "").split(" ")[0], "%Y-%m-%d")
        dbb = datetime.strptime((b or "").split(" ")[0], "%Y-%m-%d")
        return (dbb - da).days
    except Exception:
        return 0


def days_since(stamp):
    return days_between(stamp, db.today())


def follow_status(stage_def, clock_value, snooze_until=None):
    """Return dict(level, label, days). level: ok | stalled | hot.

    stage_def: a stage dict with 'follow_after'. clock_value: the date string the
    follow-up clock runs from (stage_since for jobs, last_contact for leads).
    """
    d = days_since(clock_value)
    if snooze_until and db.today() < snooze_until:
        return {"level": "ok", "label": "snoozed", "days": d, "snoozed": True}
    fa = stage_def.get("follow_after", 0) if stage_def else 0
    if not fa:
        return {"level": "ok", "label": "—", "days": d}
    if d >= fa * 2:
        return {"level": "hot", "label": "OVERDUE", "days": d}
    if d >= fa:
        return {"level": "stalled", "label": "Follow up", "days": d}
    return {"level": "ok", "label": "On track", "days": d}


# ---------------------------------------------------------------------------
# Money helpers
# ---------------------------------------------------------------------------

def est_num(text):
    """Pull a number out of a money string like '$14,500'."""
    if text is None:
        return 0.0
    digits = re.sub(r"[^0-9.]", "", str(text))
    try:
        return float(digits) if digits else 0.0
    except Exception:
        return 0.0


def money(n):
    if not n:
        return "$0"
    return "$" + format(int(round(n)), ",")


def money_k(n):
    """Compact $K formatting for board column totals."""
    if n >= 1000:
        v = n / 1000.0
        s = ("%.0f" if n >= 10000 else "%.1f") % v
        return "$" + s.replace(".0", "") + "K"
    return "$" + str(int(round(n)))


def paid_pct(payments):
    paid = 0.0
    for p in constants.DRAW_SCHEDULE:
        if p["pct"] and payments.get(p["key"]):
            paid += p["pct"]
    return paid


def draw_amount(contract_value, draw, payments):
    if draw["pct"]:
        return money(est_num(contract_value) * draw["pct"])
    wood = payments.get("woodAmt") if payments else None
    return money(est_num(wood)) if wood else ""


# ---------------------------------------------------------------------------
# SeaBreeze job-name composition (reference_job_naming_convention)
#   R-26179: Richard Reis (PBC) (S17) (DB)
#   = [job_no]: [client] ([AHJ]) ([roof_code]) ([rep])
# Structured parts (client_name, ahj_abbrev, roof_letter, squares, rep_code,
# job_seq/job_no) are stored on the row so the composed `name` is always
# regenerable and reports/filters can use the pieces. Unknown AHJ/rep codes are
# blanked + flagged (see modules/ahj.ahj_abbrev) — never invented. The roof-letter
# + AHJ-abbrev maps live in modules/ahj; the rep map is here.
# ---------------------------------------------------------------------------

# Documented rep -> code (confirmed in reference_job_naming_convention). Anything
# not listed falls back to a deterministic rule (single name -> UPPER, multi-word
# -> initials) and is flagged as derived, not trusted as the official shop code.
REP_CODES = {
    "danny bivins": "DB",
    "francis ferrer": "FF",
    "jacin carreiro": "Jacin",
    "scott": "SCOTT",
}


def rep_code(rep):
    """(code, documented) for a salesperson. documented=False means the code was
    derived (not an official shop code) and should be flagged for confirmation."""
    raw = (rep or "").strip()
    if not raw:
        return "", True
    norm = re.sub(r"\s+", " ", raw).lower()
    if norm in REP_CODES:
        return REP_CODES[norm], True
    toks = [t for t in re.split(r"\s+", raw) if t]
    if len(toks) == 1:
        return toks[0].upper(), False
    return "".join(t[0] for t in toks).upper(), False


def _squares_int(squares):
    try:
        n = int(round(float(squares)))
    except (TypeError, ValueError):
        return 0
    return n if n > 0 else 0


def roof_code(roof_letter, squares=None):
    """Roof code like 'S17' (letter + squares). Letter alone ('S') until squares
    are known; '' when there's no material letter."""
    if not roof_letter:
        return ""
    n = _squares_int(squares)
    return "%s%s" % (roof_letter, n if n else "")


def compose_job_name(client_name, ahj_abbrev="", roof_letter="", squares=None,
                     rep_code="", job_no=""):
    """Assemble the SeaBreeze display name from already-resolved parts. Empty
    parts are omitted (no stray '()'); job_no prefixes once it exists."""
    client = (client_name or "").strip() or "Unknown"
    segs = []
    if ahj_abbrev:
        segs.append("(%s)" % ahj_abbrev)
    rc = roof_code(roof_letter, squares)
    if rc:
        segs.append("(%s)" % rc)
    if rep_code:
        segs.append("(%s)" % rep_code)
    name = client + (" " + " ".join(segs) if segs else "")
    return ("%s: %s" % (job_no, name)) if job_no else name


def naming_parts(row, squares=None, job_no=""):
    """Resolve the structured naming parts + flags for a lead/job row.
    Returns (name, parts_dict, flags_list). parts_dict keys: client_name,
    ahj_abbrev, roof_letter, squares, rep_code."""
    from modules import ahj as ahj_mod
    client = (row.get("client_name") or row.get("name") or "").strip()
    resolved_ahj = row.get("ahj") or ""
    abbr, _ahj_ok = ahj_mod.ahj_abbrev(resolved_ahj)
    letter = ahj_mod.roof_letter(row.get("work_type") or "")
    rep_raw = row.get("rep") or ""
    rcode, rep_ok = rep_code(rep_raw)
    if squares is None:
        squares = row.get("squares")
    name = compose_job_name(client, abbr, letter, squares, rcode, job_no)
    parts = {"client_name": client, "ahj_abbrev": abbr, "roof_letter": letter,
             "squares": _squares_int(squares) or None, "rep_code": rcode}
    flags = []
    if resolved_ahj and not abbr:
        flags.append("AHJ '%s' has no documented abbreviation" % resolved_ahj)
    if (row.get("work_type") or "") and not letter:
        flags.append("work type '%s' has no roof-code letter" % row.get("work_type"))
    if rep_raw and not rep_ok:
        flags.append("rep code '%s' derived from '%s' (confirm)" % (rcode, rep_raw))
    return name, parts, flags


def _apply_name(table, row_id, row, squares, job_no):
    """Shared writer: recompose + persist name + structured parts; log flags.
    Never nulls an existing `squares` (only writes a positive value)."""
    name, parts, flags = naming_parts(row, squares=squares, job_no=job_no)
    upd = {"name": name, "ahj_abbrev": parts["ahj_abbrev"],
           "roof_letter": parts["roof_letter"], "rep_code": parts["rep_code"]}
    sq = _squares_int(squares if squares is not None else row.get("squares"))
    if sq:
        upd["squares"] = sq
    db.update(table, row_id, **upd)
    if flags:
        db.add_activity(table[:-1], row_id, "automation",
                        "Job-name flags: " + "; ".join(flags))
    return name


def refresh_lead_name(lead_id, squares=None):
    """Recompose a lead's display name from its stored parts — capturing the raw
    client name into client_name the FIRST time so the real name is never lost —
    update the structured fields, and log any unknown-code flags. Returns name."""
    l = db.get("leads", lead_id)
    if not l:
        return ""
    if not (l.get("client_name") or "").strip():
        raw = (l.get("name") or "").strip()
        db.update("leads", lead_id, client_name=raw)
        l["client_name"] = raw
    return _apply_name("leads", lead_id, l, squares, job_no="")


def next_job_number(year2=None, prefix="R"):
    """Next sequential SeaBreeze job number, e.g. ('R-26179', 179). Sequence is
    max existing job_seq + 1 across ALL jobs (also parsed from legacy 'R-YYNNN'
    names/rids), stamped with the current 2-digit year. Returns (job_no, seq)."""
    if year2 is None:
        year2 = db.today()[2:4]
    seq = 0
    try:
        rows = db.all_rows("jobs")
    except Exception:
        rows = []
    pat = re.compile(r"%s-?\d{2}(\d{1,4})\b" % re.escape(prefix), re.I)
    for j in rows:
        try:
            s = int(j.get("job_seq") or 0)
        except (TypeError, ValueError):
            s = 0
        if not s:
            for field in (j.get("job_no"), j.get("name"), j.get("rid")):
                m = pat.search(str(field or ""))
                if m:
                    s = int(m.group(1))
                    break
        if s > seq:
            seq = s
    nseq = seq + 1
    return "%s-%s%03d" % (prefix, year2, nseq), nseq


def refresh_job_name(job_id, squares=None):
    """Recompose a job's standardized name (R-26###: Client (AHJ) (S17) (Rep))
    from its stored parts, capturing client_name the first time (stripping any
    existing job-number prefix). Returns the new name."""
    j = db.get("jobs", job_id)
    if not j:
        return ""
    if not (j.get("client_name") or "").strip():
        raw = (j.get("name") or "").strip()
        m = re.match(r"^[A-Za-z]-?\d{2,6}:\s*(.*)$", raw)
        captured = m.group(1).strip() if m else raw
        db.update("jobs", job_id, client_name=captured)
        j["client_name"] = captured
    job_no = (j.get("job_no") or j.get("rid") or "").strip()
    return _apply_name("jobs", job_id, j, squares, job_no=job_no)


# ---------------------------------------------------------------------------
# Flask wiring: inject brand + helpers into every template
# ---------------------------------------------------------------------------

def departments(company):
    raw = (company.get("departments") or "").strip()
    return [d.strip() for d in raw.split(",") if d.strip()] or ["Main Department"]


def current_department():
    """The department selected in the masthead (session), or the first one."""
    from flask import session
    depts = departments(db.get_company())
    cur = session.get("department")
    return cur if cur in depts else depts[0]


def register(app):
    from flask import session

    @app.context_processor
    def _inject():
        company = db.get_company()
        depts = departments(company)
        current = session.get("department")
        if current not in depts:
            current = depts[0]
        return {
            "company": company,
            "departments": depts, "current_department": current,
            "money": money, "money_k": money_k, "est_num": est_num,
            "days_since": days_since, "today": db.today,
            "constants": constants,
        }

    app.jinja_env.globals.update(
        follow_status=follow_status, paid_pct=paid_pct, draw_amount=draw_amount,
        load_json=db.load_json, rep_options=rep_options,
        estimate_templates=estimate_templates, followup_email=followup_email,
        address_line=address_line, closeout_email=closeout_email)


def rep_options():
    """Active users' names for the Sales Rep dropdown (white-label: from Users)."""
    try:
        names = [u["name"] for u in db.all_rows("users", "active=1", order="name") if u.get("name")]
    except Exception:
        names = []
    return names


def estimate_templates():
    """Editable estimate templates for the one-click Quick Estimate buttons."""
    try:
        return db.all_rows("templates", order="name")
    except Exception:
        return []


def _payment_link_for_job(job_id):
    if not job_id:
        return ""
    try:
        for inv in db.all_rows("invoices", "job_id=?", (job_id,), "id DESC"):
            if inv.get("payment_link"):
                return inv["payment_link"]
    except Exception:
        pass
    return ""


def followup_email(kind, rec):
    """Build a one-click Gmail-compose URL for an overdue follow-up: greeting, where
    it stands + next step, balance due with remaining draws, and a payment link if we
    have one (else check drop-off / mail-in details). Opens a draft — never sends."""
    import urllib.parse
    company = db.get_company()
    cname = company.get("name", "")
    phone = company.get("phone", "")
    email = rec.get("email", "") or ""
    first = (rec.get("name") or "there").split(" ")[0]
    lines = []
    if kind == "job":
        sd = constants.job_stage(rec.get("stage"))
        su = "%s — your roof project update & balance" % cname
        lines.append("Quick update on your roof: we're at the “%s” stage." % sd["name"])
        if rec.get("todo"):
            lines.append("Next step: %s" % rec["todo"])
        val = est_num(rec.get("contract_value"))
        payments = db.load_json(rec.get("payments"), {})
        pct = paid_pct(payments)
        if val:
            lines.append("")
            lines.append("Balance due: %s of %s (%d%% collected)." % (money(val * (1 - pct)), money(val), round(pct * 100)))
            remaining = [p["label"] for p in constants.DRAW_SCHEDULE if p.get("pct") and not payments.get(p["key"])]
            if remaining:
                lines.append("Remaining payments: " + "; ".join(remaining) + ".")
        link = rec.get("pay_url") or _payment_link_for_job(rec.get("id"))
        lines.append("")
        if link:
            lines.append("Pay securely online here: %s" % link)
        else:
            addr = ", ".join([p for p in [company.get("address"), company.get("city"),
                              ("%s %s" % (company.get("state", ""), company.get("zip", ""))).strip()] if p])
            lines.append("To pay by check: make it out to %s and drop off / mail to %s, or just reply and we'll schedule a pickup." % (cname, addr))
    else:
        sd = constants.lead_stage(rec.get("stage"))
        su = "Following up on your roof — %s" % cname
        lines.append("Following up on your roofing project (current stage: %s)." % sd["name"])
        if rec.get("todo"):
            lines.append("Next step: %s" % rec["todo"])
        if rec.get("estimate"):
            lines.append("")
            lines.append("Your estimate is %s — happy to answer questions or set up financing." % rec["estimate"])
    body = "Hi %s,\n\n%s\n\nQuestions? Just reply here or call %s.\n\nThank you,\n%s\n%s\n%s" % (
        first, "\n".join(lines), phone, rec.get("rep") or company.get("qualifier", ""), cname, phone)
    return "https://mail.google.com/mail/?view=cm&fs=1&to=%s&su=%s&body=%s" % (
        urllib.parse.quote(email), urllib.parse.quote(su), urllib.parse.quote(body))


def _addr_ok(v):
    """A field value is displayable only if it's non-empty, not the literal
    'None', and not a corrupted object/dict fragment (e.g. \"{'id': 9\")."""
    if not v:
        return False
    s = str(v).strip()
    if not s or s.lower() == "none":
        return False
    if s[:1] in "{[" or "'id'" in s or '"id"' in s:
        return False
    return True


def address_line(rec):
    """Clean single-line address for display — skips empty / None / corrupted
    parts so the UI never shows 'None' or a raw '{...}' object."""
    addr = (rec.get("address") or "") if _addr_ok(rec.get("address")) else ""
    city = (rec.get("city") or "") if _addr_ok(rec.get("city")) else ""
    state = (rec.get("state") or "") if _addr_ok(rec.get("state")) else ""
    zc = (rec.get("zip") or "") if _addr_ok(rec.get("zip")) else ""
    sz = ("%s %s" % (state, zc)).strip()
    head = ", ".join([p for p in [str(addr).strip(), str(city).strip()] if p])
    return (head + (" " + sz if sz else "")).strip().strip(",").strip()


def closeout_email(rec):
    """One-click Gmail draft for a COMPLETED/INVOICED job: thank-you, closeout
    docs (warranty + Certificate of Completion) waiting in the portal, and the
    final balance + payment link. Draft only — never sends. (House rule.)"""
    import urllib.parse
    company = db.get_company()
    cname = company.get("name", "")
    phone = company.get("phone", "")
    email = rec.get("email", "") or ""
    if not email and rec.get("contact_id"):
        c = db.get("contacts", rec["contact_id"])
        email = (c or {}).get("email", "") if c else ""
    first = (rec.get("name") or "there").split(" ")[0]
    val = est_num(rec.get("contract_value"))
    payments = db.load_json(rec.get("payments"), {})
    pct = paid_pct(payments)
    link = rec.get("pay_url") or _payment_link_for_job(rec.get("id"))
    lines = ["Great news — your roof project is complete. Thank you for choosing %s!" % cname, ""]
    lines.append("Your closeout documents — workmanship & manufacturer warranties and the "
                 "Certificate of Completion — are ready in your homeowner portal.")
    if val:
        bal = val * (1 - pct)
        if bal > 0.5:
            lines += ["", "Final balance due: %s of %s." % (money(bal), money(val))]
            if link:
                lines.append("Pay your final balance securely here: %s" % link)
            else:
                lines.append("Reply here and we'll send your final payment link or schedule a check pickup.")
        else:
            lines += ["", "Your account is paid in full — thank you!"]
    su = "%s — your roof is complete: closeout docs & final payment" % cname
    body = "Hi %s,\n\n%s\n\nQuestions? Reply here or call %s.\n\nThank you,\n%s\n%s\n%s" % (
        first, "\n".join(lines), phone, rec.get("rep") or company.get("qualifier", ""), cname, phone)
    return "https://mail.google.com/mail/?view=cm&fs=1&to=%s&su=%s&body=%s" % (
        urllib.parse.quote(email), urllib.parse.quote(su), urllib.parse.quote(body))
