# -*- coding: utf-8 -*-
"""Match an email (sender + subject) to a CRM lead / job / contact.

Used by the dashboard Gmail widget to turn each inbox row into a "→ Open R-####"
link, and by the Smart To-Do generator to anchor every todo to a real record.

Match order (first hit wins), mirroring the spec:
  (a) a job number  R-#####  in the subject   -> lead/job by rid
  (b) the sender email == a lead/job/contact email
  (c) a customer name in the subject == a record name
  (d) a street address in the subject == a record address   (extra: permit mail)

Jobs are indexed before leads so the more-advanced record wins a tie (a job
keeps its lead's rid/name when converted). Returns None on no confident match —
we never guess wildly.
"""
import re
from email.utils import parseaddr

from flask import url_for

import db

# R-26061 / R26061 / "Job R-26061:" — capture the 5-digit core.
_RID_RE = re.compile(r"\bR-?(\d{5})\b", re.I)
# A street address: number + street words (stop at comma / paren / dash).
_ADDR_RE = re.compile(r"\b(\d{2,6}\s+[A-Za-z0-9.'\- ]{3,40}?)(?=[,()\-]|$)")
_STREET_WORD = re.compile(
    r"\b(st|street|ave|avenue|rd|road|dr|drive|ln|lane|way|ct|court|blvd|"
    r"boulevard|cir|circle|pl|place|ter|terrace|trl|trail|hwy|pkwy|loop)\b", re.I)


def _norm_email(e):
    return (e or "").strip().lower()


def _norm_addr(a):
    """Street portion of an address, lowercased, whitespace-collapsed."""
    a = (a or "").split(",")[0]
    return re.sub(r"\s+", " ", a).strip().lower()


def build_index(department=None):
    """Load leads/jobs/contacts once into lookup tables for fast repeat matching."""
    flt = ("department=?", (department,)) if department else ("", ())
    idx = {"rid": {}, "email": {}, "names": [], "addrs": []}

    def add(rec, rtype, name, rid, email, address):
        r = {"type": rtype, "id": rec["id"], "name": (name or "").strip(),
             "rid": rid or "", "email": email or ""}
        if rid:
            m = _RID_RE.search(rid)
            digits = m.group(1) if m else re.sub(r"\D", "", rid)
            if digits:
                idx["rid"].setdefault(digits, r)
        if email:
            idx["email"].setdefault(_norm_email(email), r)
        nm = (name or "").strip()
        # Only match on a specific name: a full person name (has a space) or a
        # company name >= 6 chars. Stops "Bob" / "Roof" style false hits.
        if nm and ((" " in nm and len(nm) >= 5) or len(nm) >= 6):
            idx["names"].append((nm.lower(), r))
        na = _norm_addr(address)
        if na and len(na) >= 6 and _STREET_WORD.search(na):
            idx["addrs"].append((na, r))

    for j in db.all_rows("jobs", *flt):
        add(j, "job", j.get("name"), j.get("rid"), j.get("email"), j.get("address"))
    for l in db.all_rows("leads", *flt):
        add(l, "lead", l.get("name"), l.get("rid"), l.get("email"), l.get("address"))
    for c in db.all_rows("contacts", *flt):
        nm = ("%s %s" % (c.get("first_name") or "", c.get("last_name") or "")).strip() \
            or (c.get("company") or "")
        add(c, "contact", nm, None, c.get("email"), c.get("address"))

    # Longest names/addresses first so the most specific substring wins.
    idx["names"].sort(key=lambda t: -len(t[0]))
    idx["addrs"].sort(key=lambda t: -len(t[0]))
    return idx


def _result(r):
    t = r["type"]
    if t == "lead":
        url = url_for("leads.detail", lead_id=r["id"])
    elif t == "job":
        url = url_for("jobs.detail", job_id=r["id"])
    else:
        url = url_for("contacts.detail", contact_id=r["id"])
    if r.get("rid") and r.get("name"):
        label = "%s · %s" % (r["rid"], r["name"])
    else:
        label = r.get("rid") or r.get("name") or ("%s #%s" % (t, r["id"]))
    return {"type": t, "id": r["id"], "url": url, "label": label,
            "name": r.get("name", ""), "rid": r.get("rid", "")}


def match_one(idx, from_addr, subject):
    """Best CRM match for one email, or None. `idx` from build_index()."""
    subject = subject or ""
    # (a) job number in subject
    m = _RID_RE.search(subject)
    if m and m.group(1) in idx["rid"]:
        return _result(idx["rid"][m.group(1)])
    # (b) sender email
    addr = _norm_email(parseaddr(from_addr or "")[1] or from_addr)
    if addr and addr in idx["email"]:
        return _result(idx["email"][addr])
    # (c) customer name in subject
    subj_l = subject.lower()
    for name, r in idx["names"]:
        if name in subj_l:
            return _result(r)
    # (d) street address in subject (permit / inspection mail)
    for na, r in idx["addrs"]:
        if na in subj_l:
            return _result(r)
    return None


def match(from_addr, subject, department=None):
    """One-shot convenience: build the index and match a single email."""
    return match_one(build_index(department), from_addr, subject)
