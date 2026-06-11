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
    """Pull a number out of a money string like '$14,500' or '($500)'.
    Honors a leading minus or accounting parentheses as negative, and collapses
    stray thousand-dots (e.g. '1.234.50') so they don't zero the value."""
    if text is None:
        return 0.0
    s = str(text).strip()
    neg = s.startswith("-") or (s.startswith("(") and s.endswith(")"))
    digits = re.sub(r"[^0-9.]", "", s)
    if digits.count(".") > 1:  # treat all but the last dot as separators
        head, _, tail = digits.rpartition(".")
        digits = head.replace(".", "") + "." + tail
    try:
        val = float(digits) if digits else 0.0
    except Exception:
        return 0.0
    return -val if neg else val


def money(n):
    if not n:
        return "$0"
    # Show cents only when present (matches AccuLynx: $6,870.01 but $9,160 stays clean).
    r = round(float(n), 2)
    if abs(r - round(r)) < 0.005:
        return "$" + format(int(round(r)), ",")
    return "$" + format(r, ",.2f")


def money_k(n):
    """Compact $K formatting for board column totals."""
    if n >= 1000:
        v = n / 1000.0
        s = ("%.0f" if n >= 10000 else "%.1f") % v
        return "$" + s.replace(".0", "") + "K"
    return "$" + str(int(round(n)))


def money_abbr(n):
    """Compact headline money for stat cards — keeps big totals inside the card.

    $818,750 -> $818.8K, $6,288,855 -> $6.3M, $32,967,628.92 -> $33.0M, $1.2B.
    One decimal of significance; pair with title=money(n) for the exact value.
    """
    try:
        v = float(n or 0)
    except Exception:
        return "$0"
    neg = v < 0
    v = abs(v)
    if v >= 1e12:
        s = "$%.1fT" % (v / 1e12)
    elif v >= 1e9:
        s = "$%.1fB" % (v / 1e9)
    elif v >= 1e6:
        s = "$%.1fM" % (v / 1e6)
    elif v >= 1e3:
        s = "$%.1fK" % (v / 1e3)
    else:
        # Under $1K: show the plain value (cents only when present).
        return ("-" if neg else "") + money(v)
    return ("-" if neg else "") + s


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
            "money": money, "money_k": money_k, "money_abbr": money_abbr, "est_num": est_num,
            "days_since": days_since, "today": db.today,
            "constants": constants,
        }

    app.jinja_env.globals.update(
        follow_status=follow_status, paid_pct=paid_pct, draw_amount=draw_amount,
        load_json=db.load_json, rep_options=rep_options,
        estimate_templates=estimate_templates, followup_email=followup_email,
        address_line=address_line, closeout_email=closeout_email,
        # Lifecycle helpers as globals so they work inside imported macros too.
        lifecycle_steps=(lambda: constants.LIFECYCLE),
        lifecycle_pos=constants.lifecycle_pos, lifecycle_step=constants.lifecycle_step,
        next_step=constants.next_step)


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
