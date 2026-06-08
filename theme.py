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
        load_json=db.load_json, rep_options=rep_options)


def rep_options():
    """Active users' names for the Sales Rep dropdown (white-label: from Users)."""
    try:
        names = [u["name"] for u in db.all_rows("users", "active=1", order="name") if u.get("name")]
    except Exception:
        names = []
    return names
