# -*- coding: utf-8 -*-
"""Lead-intake normalizer + email parser.

Turns inbound lead notifications (AccuLynx "Lead Assigned", Angi/HomeAdvisor,
generic web-to-lead forms, Craigslist relays) into the normalized lead dict the
CRM understands:  {name, phone, email, address, work_type, source}.

Pure functions — no Flask, no DB — so they are unit-testable and reusable by the
intake webhook, a Gmail watcher, or a future poller.  The HTTP surface lives in
modules/leads.py (intake / intake_email routes).
"""
import re

# --- field extraction helpers ---------------------------------------------

_PHONE_RE = re.compile(r"(?:\+?1[\s.\-]?)?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# A US street address line: number + street, optionally city/state/zip.
# Street address on a single line: number + street + suffix, optional city/state/zip.
# Restricted to spaces/tabs (no newline) so it can't bleed into the next field.
_ADDR_RE = re.compile(
    r"\d{1,6}[ \t]+[A-Za-z0-9.][A-Za-z0-9. \t]*?(?:St|Street|Ave|Avenue|Rd|Road|Dr|Drive|"
    r"Ln|Lane|Blvd|Boulevard|Ct|Court|Way|Pl|Place|Ter|Terrace|Cir|Circle|Hwy|Highway|Pkwy)"
    r"\b[A-Za-z0-9,.\- \t]*?(?:FL|Florida)?[ \t]*\d{0,5}", re.IGNORECASE)

# work-type keyword -> canonical work type label used by the estimate templates.
_WORK_KEYWORDS = [
    (r"metal", "Metal Roof Replacement"),
    (r"tile", "Tile Roof Replacement"),
    (r"flat|tpo|modified|low.?slope", "Flat/Low-Slope Roof"),
    (r"shingle|asphalt|re-?roof|replace", "Shingle Roof Replacement"),
    (r"repair|leak|patch", "Roof Repair"),
    (r"inspect", "Roof Inspection"),
]


def clean_phone(s):
    """Keep digits; drop a leading US country code; format as (xxx) xxx-xxxx."""
    if not s:
        return ""
    d = re.sub(r"\D", "", s)
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    if len(d) == 10:
        return "(%s) %s-%s" % (d[:3], d[3:6], d[6:])
    return s.strip()


def _guess_work_type(text):
    t = (text or "").lower()
    for pat, label in _WORK_KEYWORDS:
        if re.search(pat, t):
            return label
    return ""


def _labeled(body, *labels):
    """Pull the value after a 'Label: value' line for any of the given labels."""
    for lab in labels:
        m = re.search(r"%s\s*[:\-]\s*(.+)" % re.escape(lab), body, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return ""


def normalize(payload):
    """Clean a structured dict (from a web form / webhook JSON) into a lead dict."""
    name = (payload.get("name") or "").strip()
    if not name and (payload.get("first_name") or payload.get("last_name")):
        name = ("%s %s" % (payload.get("first_name", ""), payload.get("last_name", ""))).strip()
    return {
        "name": name,
        "phone": clean_phone(payload.get("phone") or payload.get("phone_number") or ""),
        "email": (payload.get("email") or "").strip().lower(),
        "address": (payload.get("address") or payload.get("street") or "").strip(),
        "work_type": (payload.get("work_type") or payload.get("service")
                      or _guess_work_type(payload.get("message") or "")).strip(),
        "source": (payload.get("source") or "Web Form").strip(),
    }


# --- email parsing ---------------------------------------------------------

def detect_source(sender, subject):
    s = "%s %s" % ((sender or "").lower(), (subject or "").lower())
    if "acculynx" in s or "lead assigned" in s:
        return "AccuLynx"
    if "angi" in s or "angie" in s or "homeadvisor" in s:
        return "Angi"
    if "thumbtack" in s:
        return "Thumbtack"
    if "craigslist" in s:
        return "Craigslist"
    if "offerup" in s or "offer up" in s:
        return "OfferUp"
    if "facebook" in s or "fb lead" in s:
        return "Facebook"
    return "Email"


def parse_email(sender, subject, body):
    """Best-effort parse of an inbound lead email into a normalized lead dict.

    Returns the dict, or None if no usable name/contact could be extracted.
    Works on plain-text bodies (strip HTML before calling for HTML mail).
    """
    body = body or ""
    source = detect_source(sender, subject)

    # 1) Try explicit "Label: value" lines first — AccuLynx, Angi and most
    #    web-to-lead emails use them and they are the most reliable signal.
    name = _labeled(body, "Name", "Customer", "Customer Name", "Contact", "Lead Name", "Full Name")
    phone = _labeled(body, "Phone", "Phone Number", "Mobile", "Cell", "Tel")
    email = _labeled(body, "Email", "Email Address", "E-mail")
    address = _labeled(body, "Address", "Property Address", "Service Address", "Job Address", "Street")
    work = _labeled(body, "Service", "Work Type", "Project", "Job Type", "Trade", "Interested In")

    # 2) Fall back to free-text regex scans for anything still missing.
    if not phone:
        m = _PHONE_RE.search(body)
        phone = m.group(0) if m else ""
    if not email:
        m = _EMAIL_RE.search(body)
        # avoid grabbing the sending platform's own address
        email = next((e for e in _EMAIL_RE.findall(body)
                      if not re.search(r"(acculynx|angi|noreply|no-reply|craigslist|mailer)", e, re.I)), "")
    if not address:
        m = _ADDR_RE.search(body)
        address = m.group(0).strip() if m else ""
    if not work:
        work = _guess_work_type(subject + " " + body)

    # 3) If still no name, try the subject ("New lead: Jane Homeowner").
    if not name:
        m = re.search(r"(?:lead|customer|request)[:\-]\s*([A-Z][a-z]+\s+[A-Z][a-z]+)", subject or "")
        if m:
            name = m.group(1).strip()

    if not name and not phone and not email:
        return None  # nothing actionable

    return {
        "name": name or "(unknown — see notes)",
        "phone": clean_phone(phone),
        "email": (email or "").strip().lower(),
        "address": address,
        "work_type": work,
        "source": source,
    }


# --- RingCentral telephony / voicemail webhook ----------------------------

def _dig(obj, *path):
    """Safe nested get across dicts/lists (first element of any list)."""
    cur = obj
    for key in path:
        if isinstance(cur, list):
            cur = cur[0] if cur else None
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    if isinstance(cur, list):
        cur = cur[0] if cur else None
    return cur


def parse_ringcentral(payload):
    """Normalize a RingCentral webhook event (telephony session or voicemail/
    message-store) into {name, phone, source, direction, event, notes}.

    Tolerant of the several event shapes RingCentral emits — it looks for the
    inbound caller's number in the common locations. Returns None if it can't
    find a phone number (e.g. an outbound call or a non-call event).
    """
    payload = payload or {}
    body = payload.get("body") if isinstance(payload.get("body"), dict) else payload

    # Direction: 'Inbound' / 'Outbound' (telephony) — default Inbound for VM.
    direction = (_dig(body, "parties", "direction")
                 or body.get("direction") or "Inbound")

    # Caller's number, trying telephony-session then voicemail/message shapes.
    phone = (_dig(body, "parties", "from", "phoneNumber")
             or _dig(body, "from", "phoneNumber")
             or body.get("from")
             or _dig(body, "changes", "newCount") and None  # message-count event: no number
             or "")
    name = (_dig(body, "parties", "from", "name")
            or _dig(body, "from", "name") or "")

    # Classify the event for the activity note / source label.
    is_vm = bool(_dig(body, "attachments", "type")) or "voicemail" in str(payload).lower()
    event = "Voicemail" if is_vm else "Call"
    source = "RingCentral VM" if is_vm else "RingCentral Call"

    if not phone:
        return None
    return {
        "name": (name or "").strip() or "Caller %s" % clean_phone(phone),
        "phone": clean_phone(phone),
        "email": "",
        "address": "",
        "work_type": "",
        "source": source,
        "direction": direction,
        "event": event,
        "notes": "%s %s from %s" % (direction, event, clean_phone(phone)),
    }
