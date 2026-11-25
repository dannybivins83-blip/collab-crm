# -*- coding: utf-8 -*-
"""Homeowner side of MyRoofPortal — compare roof quotes apples to apples.

The roofer-facing front door sells the portal. This is the other audience: the
homeowner holding three bids who cannot tell why one is $9,000 more than the
next. They are not the same roof, and the difference is never in the price — it
is in the twenty lines underneath it that only one of the three quotes bothered
to write down.

So the tool is not a price calculator. It is a SPEC SHEET: the homeowner marks,
per quote, which of these line items the contractor actually put in writing.
What falls out is (a) the real comparison, (b) a list of questions to ask each
contractor, generated from that quote's own gaps, and (c) a project tracker for
after they pick someone.

Deliberately NOT here: any claim about what a roof should cost, any ranking of
contractors, any "recommended" bid. We are not licensed to advise on their job
and we do not see it. Every judgment stays with the homeowner; this only makes
sure nothing is invisible when they make it.
"""
import json
import os
import re
import secrets
import threading

from flask import (Blueprint, render_template, request, redirect, url_for,
                   abort, jsonify)

import db

bp = Blueprint("compare", __name__)

# --------------------------------------------------------------------------
# Storage. Separate from portal_leads/jobs: a homeowner comparing bids has no
# contractor and no job yet, and may never have one here.
# --------------------------------------------------------------------------
try:
    db.execute("""CREATE TABLE IF NOT EXISTS ho_projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created TEXT, token TEXT, email TEXT, name TEXT,
        address TEXT, city TEXT, state TEXT, zip TEXT,
        stage TEXT, notes TEXT, source_host TEXT, last_seen TEXT,
        roof_type TEXT)""")
except Exception:
    pass
# What the homeowner uploaded, and what the reader made of it. Kept separate from
# ho_quotes so a failed or disputed read never silently becomes "the quote".
try:
    db.execute("""CREATE TABLE IF NOT EXISTS ho_uploads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER, quote_id INTEGER, created TEXT,
        filename TEXT, stored TEXT, bytes INTEGER,
        status TEXT, error TEXT, extracted TEXT)""")
except Exception:
    pass
try:
    db.execute("""CREATE TABLE IF NOT EXISTS ho_quotes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER, created TEXT,
        contractor TEXT, phone TEXT, email TEXT, license TEXT,
        price TEXT, spec TEXT, notes TEXT,
        source TEXT, reviewed INTEGER DEFAULT 0)""")
except Exception:
    pass
# Paperwork collected as the job runs, filed against the stage it belongs to.
# Separate from ho_uploads: those are quotes being read into the sheet, these
# are the contract, the permit, the delivery ticket and the warranty -- the
# things a homeowner needs in ten years and cannot get back afterwards.
try:
    db.execute("""CREATE TABLE IF NOT EXISTS ho_docs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER, stage TEXT, created TEXT,
        filename TEXT, stored TEXT, bytes INTEGER,
        status TEXT, error TEXT, extracted TEXT, note TEXT)""")
except Exception:
    pass
for _t, _c, _d in (("ho_projects", "roof_type", "TEXT"),
                   ("ho_projects", "chosen_quote_id", "INTEGER"),
                   ("ho_quotes", "source", "TEXT"),
                   ("ho_quotes", "reviewed", "INTEGER DEFAULT 0")):
    try:
        db._ensure_column(_t, _c, _d)
    except Exception:
        pass
db._COLCACHE.clear()


# --------------------------------------------------------------------------
# The spec sheet. This list IS the product — the UI, the gap analysis and the
# printed question list all read from it, so adding a row adds it everywhere.
#
# `ask` is written to be read aloud on the phone, in the homeowner's words, not
# ours. `why` says what it costs them to leave it unanswered, because a
# checklist nobody understands gets skipped.
# --------------------------------------------------------------------------
# The criteria live in a generated module, built from the developer handoff's
# comparison_rules.json. 104 lines, each carrying the document and page it came
# from and how much authority that source has. Regenerate with
# scratchpad/build_criteria.py; never hand-edit compare_criteria.py.
from modules.compare_criteria import SPEC   # noqa: E402
from modules import compare_scope          # noqa: E402

SPEC_INDEX = {i["key"]: i for g in SPEC for i in g["items"]}
SPEC_KEYS = [i["key"] for g in SPEC for i in g["items"]]

# Roof types the sheet knows. "unsure" keeps every group visible rather than
# hiding a question from someone who does not yet know what they have.
ROOF_TYPES = [("tile", "Tile"), ("shingle", "Shingle"), ("metal", "Metal"),
              ("flat", "Flat / low-slope"), ("unsure", "Not sure yet")]
ROOF_TYPE_KEYS = [k for k, _ in ROOF_TYPES]


def spec_for(roof_type):
    """The groups that apply to this roof, and inside them only the criteria
    that apply. An unknown or missing type shows everything, because hiding a
    question is worse than asking one that does not apply.

    The groups themselves are generated and bind a roof type to a whole group,
    which only the metal group actually uses -- so the per-criterion scoping in
    compare_scope sits on top. Without it a tile homeowner was asked about
    shingle nail counts and flat-roof leak response, and the answers came back
    "Not stated" because no tile proposal mentions either."""
    rt = (roof_type or "").strip().lower()
    if rt not in ROOF_TYPE_KEYS or rt == "unsure":
        return list(SPEC)
    out = []
    for g in SPEC:
        types = g.get("types") or ("all",)
        if not ("all" in types or rt in types):
            continue
        items = [i for i in g["items"] if compare_scope.applies(i, rt)]
        if items:
            out.append(dict(g, items=items))
    return out


def keys_for(roof_type, featured_only=False):
    rt = (roof_type or "").strip().lower()
    return [i["key"] for g in spec_for(roof_type) for i in g["items"]
            if not featured_only or compare_scope.is_featured(i, rt)]


def spec_view(roof_type, show_all=False):
    """Groups for the page. 104 lines is the right size for a rule set and far
    too many to hand-fill, so the default is the FEATURED subset for this roof
    and "show everything" opens the rest. A group with nothing featured in it
    disappears rather than rendering empty."""
    rt = (roof_type or "").strip().lower()
    out = []
    for g in spec_for(roof_type):
        items = g["items"] if show_all else             [i for i in g["items"] if compare_scope.is_featured(i, rt)]
        if items:
            out.append(dict(g, items=items))
    return out


def counts_for(roof_type):
    full = keys_for(roof_type)
    return {"featured": len(keys_for(roof_type, True)), "all": len(full)}

# Where a homeowner actually is, start to finish. The checklist per stage is the
# point: "what should I have in my hand right now."
STAGES = [
    dict(key="comparing", label="Comparing quotes",
         todo=["Get at least three written quotes",
               "Fill in the spec sheet for each one",
               "Ask every open question below and write the answer down",
               "Look up each license number"]),
    dict(key="chosen", label="Contractor chosen",
         todo=["Get the final quote re-issued with every answer written into it",
               "Get the insurance certificates sent directly from the insurer",
               "Read the change-order clause before signing"]),
    dict(key="signed", label="Contract signed",
         todo=["Keep a signed copy with the spec sheet attached",
               "Confirm the deposit amount matches the contract",
               "Get the start window in writing"]),
    dict(key="permit", label="Permit applied for",
         todo=["Confirm the permit is in the contractor's name",
               "Note the permit number",
               "Check it's issued before anyone starts"]),
    dict(key="scheduled", label="Materials ordered and scheduled",
         todo=["Check the delivery ticket: the product and color match what you agreed",
               "Move cars, patio furniture and anything on the walls",
               "Agree where the dumpster goes"]),
    dict(key="install", label="Install under way",
         todo=["Photograph the bare deck before the new roof covers it",
               "Photograph any wood they replace, before it's covered",
               "Get any change order in writing before you agree to it"]),
    dict(key="inspection", label="Final inspection",
         todo=["Confirm the county inspection passed",
               "Walk the property for nails and damage before final payment",
               "Hold final payment until the inspection is signed off"]),
    dict(key="done", label="Done — paperwork in hand",
         todo=["The written manufacturer warranty and your proof of purchase, kept together",
               "If your warranty program requires registration, confirm it was filed",
               "Written workmanship warranty",
               "Final invoice marked paid in full",
               "Closed permit"]),
]
STAGE_INDEX = {s["key"]: s for s in STAGES}
STAGE_KEYS = [s["key"] for s in STAGES]

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _tok():
    return secrets.token_urlsafe(12)


def _spec_of(q):
    """A quote's answers as {key: {"v": yes|no|?, "d": detail}}. Fail-soft: a
    corrupt blob must never 500 the page the homeowner is working in."""
    try:
        d = json.loads(q.get("spec") or "{}")
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _val(spec, key):
    v = (spec.get(key) or {})
    return (v.get("v") or "?") if isinstance(v, dict) else "?"


def gaps_for(q, roof_type=None, featured_only=False):
    """Every spec line this quote does NOT put in writing, as questions to ask.
    'no' and 'not addressed' both land here: the homeowner needs to raise both,
    they just mean different things once they do."""
    spec = _spec_of(q)
    out = []
    for key in (keys_for(roof_type, featured_only) if roof_type else SPEC_KEYS):
        v = _val(spec, key)
        if v != "yes":
            item = SPEC_INDEX[key]
            out.append(dict(item, state=v))
    return out


# ---------------------------------------------------------------------------
# Scoring
#
# What is scored here is THE PROPOSAL, never the contractor. The source
# material is explicit about that -- "no automatic quality, reliability or
# compliance score from employee versus subcontractor status", "do not penalize
# a qualified business because the salesperson is not the license holder" --
# and a page that turned a reading of a PDF into a rating of a company would be
# making a claim it cannot support about a business it has never seen.
#
# So the number means one thing: how much of this job is settled in writing.
#
# The consequence worth understanding is that an EXPLICIT EXCLUSION scores the
# same as an inclusion. "Gutters are not included" is a settled fact you can
# price and plan around. "Not stated" is the one that costs money later, because
# it gets decided after the old roof is off and you are negotiating from the
# worst position available. Silence is the risk, not exclusion.
SCORE_WEIGHTS = {"yes": 1.0, "no": 1.0, "?": 0.0}

# The featured lines are the ones that move money on nearly every job, so they
# carry more of the score than the long tail of technical detail.
FEATURED_WEIGHT = 3
OTHER_WEIGHT = 1

# What an unanswered line actually exposes you to, by the part of the job it
# belongs to. These are consequences, not cost estimates: we have never seen the
# roof and inventing a dollar figure would be a fabrication dressed as a number.
WORST_CASE = {
    "The company and the paperwork":
        "If this is not settled before work starts, you are relying on an "
        "assurance you cannot produce later — and the time you need it is "
        "after something has gone wrong.",
    "Tear-off, deck and substrate":
        "Rotten wood is found once the old roof is off. With no allowance and "
        "no unit price agreed, the number is set while your roof is open and "
        "the crew is already on site.",
    "The layers that keep water out":
        "The layer that fails is the one nobody specified. You will not see "
        "this again until the tile or shingles come back off.",
    "How the roof is fastened and assembled":
        "Fastening decides whether the roof holds in a storm and whether the "
        "manufacturer honors its warranty. It is invisible once covered.",
    "Metal systems":
        "Panel, finish and clip are what the warranty is written against. "
        "A substitution found later is a different roof from the one quoted.",
    "Warranty":
        "You find out what the warranty really covers at claim time, which is "
        "the worst possible moment to read it for the first time.",
    "Money, schedule and the day itself":
        "An open money line is priced after you have committed, when changing "
        "contractor costs you more than paying the difference.",
}
_WORST_DEFAULT = ("Left open, this gets decided after you have signed, when "
                  "you have the least room to negotiate.")

# Underlayment is filed under "how the roof is fastened and assembled", so an
# underlayment line was being warned about in terms of fastening. The group
# sentence is a fallback, not a straitjacket: a few lines earn their own.
WORST_BY_KEY = {
    "vet_15": ("How many layers go on, and which products, is decided on the day "
               "unless it is written down \u2014 and you cannot check it once the "
               "tile or shingles are on top of it."),
    "vet_26": ("Rotten wood is found once the old roof is off. With no allowance "
               "and no agreed rate per sheet, the price is set while your roof is "
               "open."),
    "vet_31": ("What the warranty actually covers, and for how long, is the thing "
               "you will be reading for the first time at claim time."),
    "vet_07": ("Permits, engineering and disposal are real costs. If the quote "
               "does not say they are included, treat them as a bill you have not "
               "seen yet and ask."),
    "vet_02": ("A certificate you never collected is one you cannot produce when "
               "something goes wrong, which is the only time you will want it."),
}


def _group_of(key):
    for g in SPEC:
        for i in g["items"]:
            if i["key"] == key:
                return g["name"]
    return ""


def score_quote(q, roof_type=None):
    """How much of this proposal is settled, and what is not.

    Returns points/possible/pct plus the counts behind them, so the page can
    show the working rather than asking anyone to trust a number.
    """
    spec = _spec_of(q)
    keys = keys_for(roof_type) if roof_type else SPEC_KEYS
    points = possible = 0.0
    counts = {"yes": 0, "no": 0, "?": 0}
    open_lines = []
    for key in keys:
        item = SPEC_INDEX[key]
        w = FEATURED_WEIGHT if item.get("featured") else OTHER_WEIGHT
        v = _val(spec, key)
        counts[v if v in counts else "?"] += 1
        possible += w
        points += w * SCORE_WEIGHTS.get(v, 0.0)
        if v == "?":
            cell = (spec.get(key) or {}) if isinstance(spec.get(key), dict) else {}
            open_lines.append(dict(
                item, state=v, weight=w,
                said=(cell.get("d") or "").strip(),
                worst=WORST_BY_KEY.get(
                      key, WORST_CASE.get(_group_of(key), _WORST_DEFAULT)),
                group=_group_of(key)))
    # Worst first: the featured lines, then the rest, each alphabetical so the
    # order is stable between loads rather than shuffling under the reader.
    open_lines.sort(key=lambda r: (-r["weight"], r["label"]))
    return {
        "quote": q, "points": round(points, 1), "possible": round(possible, 1),
        "pct": int(round(100.0 * points / possible)) if possible else 0,
        "settled": counts["yes"] + counts["no"], "in_writing": counts["yes"],
        "excluded": counts["no"], "open": counts["?"], "lines": len(keys),
        "open_lines": open_lines,
    }


def scoreboard(quotes, roof_type=None):
    """Every quote scored, best-settled first."""
    rows = [score_quote(q, roof_type) for q in quotes]
    rows.sort(key=lambda r: -r["pct"])
    return rows


def disagreement_detail(quotes, roof_type=None):
    """The lines the quotes do not agree on, with what each one actually says.

    The plain list answered 'what' and left 'who' and 'why' to the reader. This
    carries the criterion's own question and caution alongside every quote's
    answer, so one line can be understood without hunting across the sheet.
    """
    out = []
    for item in disagreements(quotes, roof_type):
        says = []
        for q in quotes:
            spec = _spec_of(q)
            cell = (spec.get(item["key"]) or {}) if isinstance(spec.get(item["key"]), dict) else {}
            says.append({"contractor": q.get("contractor") or "Unnamed contractor",
                         "state": _val(spec, item["key"]),
                         "said": (cell.get("d") or "").strip()})
        out.append(dict(item, says=says,
                        worst=WORST_BY_KEY.get(
                            item["key"],
                            WORST_CASE.get(_group_of(item["key"]), _WORST_DEFAULT))))
    return out


def disagreements(quotes, roof_type=None):
    """Spec lines the quotes do not agree on — the actual reason the prices
    differ. Shown above the per-quote gaps because it is the whole question the
    homeowner came here with."""
    out = []
    for key in (keys_for(roof_type) if roof_type else SPEC_KEYS):
        vals = {}
        for q in quotes:
            vals[_val(_spec_of(q), key)] = True
        if "yes" in vals and len(vals) > 1:
            out.append(SPEC_INDEX[key])
    return out


# ---------------------------------------------------------------------------
# Reading an uploaded proposal
#
# Typing 22 lines by hand is the reason most people never finish a comparison.
# So the homeowner uploads the PDF the contractor actually gave them and the
# reader fills the sheet in.
#
# Two rules this must never break:
#   1. The reader answers from the SAME `SPEC` list the form and the grid use,
#      so there is one definition of what a quote must cover and the three can
#      never drift apart.
#   2. A machine reading of somebody's contract is a DRAFT, never a finding.
#      Every extracted quote is stored `reviewed = 0` and shown as unchecked
#      until a person confirms it. We are not going to tell a homeowner their
#      contractor excluded something because a model inferred it.
# A page that cannot be read at all is kept, with the error, and the homeowner
# fills that quote in by hand -- the upload is a shortcut, not a dependency.
# ---------------------------------------------------------------------------
MAX_UPLOAD_BYTES = 12 * 1024 * 1024
UPLOAD_SUBDIR = "ho_quotes"
# Proven in this codebase by modules/takeoff.py, which runs the same
# document-plus-tool-schema call in production. Override with COMPARE_MODEL.
READER_MODEL = os.environ.get("COMPARE_MODEL", "").strip() or "claude-sonnet-4-6"


def _upload_dir():
    import config
    d = os.path.join(config.UPLOAD_DIR, UPLOAD_SUBDIR)
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def short_ask(q, limit=88):
    """A one-line version of a question, for the cards on the front page.

    The full questions average 102 characters and run to 156, which on a card
    is three ragged lines and the reason that section read as clutter. They are
    almost all several questions joined by "and", so the first one on its own is
    a complete, honest question rather than a truncation -- and nothing is lost,
    because the workspace shows every question in full.
    """
    q = (q or "").strip()
    if len(q) <= limit:
        return q
    m = re.match(r"^(.{15,%d}?\?)" % limit, q)      # already a short question
    if m:
        return m.group(1)
    head = q[:limit]                                 # else cut at a clause and close it
    cut = max(head.rfind(", and "), head.rfind(", or "), head.rfind("; "),
              head.rfind(", "))
    if cut > 24:
        return head[:cut].rstrip(" ,;") + "?"
    return head.rsplit(" ", 1)[0].rstrip(" ,;") + "?"


def _reader_tool(keys=None):
    """The tool schema, generated from SPEC so a new line item is readable the
    day it is added and no second list has to be kept in step."""
    props = {
        "contractor": {"type": "string", "description": "Company name on the proposal. Empty if not stated."},
        "price": {"type": "string", "description": "Total price exactly as written, e.g. '$14,200'. Empty if no total is given."},
        "phone": {"type": "string"},
        "email": {"type": "string"},
        "license": {"type": "string", "description": "Contractor license number if printed anywhere on the document."},
        "roof_type": {"type": "string", "enum": ["tile", "shingle", "metal", "flat", "mixed", "unclear"]},
        "unreadable": {"type": "boolean", "description": "true if the document is not a roofing proposal, or is too unclear to read."},
    }
    for key in (keys or SPEC_KEYS):
        item = SPEC_INDEX[key]
        props["v_" + key] = {
            "type": "string", "enum": ["yes", "no", "?"],
            "description": (
                "'%s'. yes = the document COMMITS to this in writing. "
                "no = the document explicitly excludes it or makes it the owner's "
                "responsibility. ? = the document is silent on it. "
                "Silence is '?', never 'no'." % item["label"]),
        }
        props["d_" + key] = {
            "type": "string",
            "description": "Short quote or figure from the document supporting the answer above, e.g. '3 sheets incl, $95/sheet after'. Empty if silent.",
        }
    return {
        "name": "read_roofing_proposal",
        "description": "Record what a roofing proposal does and does not put in writing.",
        "input_schema": {"type": "object", "properties": props,
                         "required": ["contractor", "unreadable"]},
    }


def _blocks(data, spoken=""):
    """The content blocks for one read: a PDF, one or more photos, or just what
    the homeowner said. Photos go in as images, which is what a page snapped in
    a driveway actually is."""
    import base64
    out = []
    has_doc = False
    kind, mime = sniff(data or b"")
    if kind == "pdf":
        out.append({"type": "document", "source": {
            "type": "base64", "media_type": mime,
            "data": base64.b64encode(data).decode("ascii")}})
        has_doc = True
    elif kind == "image":
        out.append({"type": "image", "source": {
            "type": "base64", "media_type": mime,
            "data": base64.b64encode(data).decode("ascii")}})
        has_doc = True
    if spoken:
        out.append({"type": "text", "text":
                    "The homeowner described this quote out loud:\n\n" + spoken})
    # Decide on whether a DOCUMENT is present, not on whether the block list is
    # non-empty: the spoken text is itself a block, so the old check handed
    # spoken-only reads the document prompt -- the one that treats what it sees
    # as written commitments. That is the exact way recall becomes a false
    # "in writing".
    out.append({"type": "text",
                "text": _READER_PROMPT if has_doc else _SPOKEN_ONLY_PROMPT})
    return out


_SPOKEN_ONLY_PROMPT = (
    "There is no document here -- only what the homeowner remembers being told. "
    "Record ONLY what they actually said. Spoken recall is not a written "
    "commitment, so a line they mention is '?' unless they say plainly that it "
    "is written into the quote. Put their own words in the detail field. Never "
    "fill in what a normal quote would contain."
)

_READER_PROMPT = (
    "You are reading a roofing proposal a homeowner received, to fill in a "
    "comparison sheet.\n\n"
    "Answer ONLY from what the document says. Do not infer what a contractor "
    "probably intends, and do not use what is typical for the trade. If the "
    "document does not address a line, the answer is '?'. Reserve 'no' for an "
    "explicit exclusion -- wording like 'not included', 'by owner', 'additional "
    "cost', or an allowance stated as 'priced as needed' with no unit price.\n\n"
    "For every 'yes' and every 'no', put the supporting words from the document "
    "in the matching detail field, short and verbatim where you can. A 'yes' with "
    "nothing to quote is a '?'.\n\n"
    "If the file is not a roofing proposal, set unreadable = true and stop.\n\n"
    "If this is a PHOTOGRAPH, read only what is legible. A page that is cut off, "
    "blurred or angled away may be hiding the very line you are judging, so when "
    "you cannot read a region, that line is '?'. Do not guess at cropped text."
)


IMAGE_TYPES = {b"\xff\xd8\xff": "image/jpeg",
               b"\x89PNG": "image/png",
               b"GIF8": "image/gif",
               b"RIFF": "image/webp"}


def sniff(data):
    """(kind, media_type). A phone photo of a proposal is the common case -- most
    people are handed paper, not a PDF -- so the reader takes pictures as well."""
    if data[:5] == b"%PDF-":
        return "pdf", "application/pdf"
    for magic, mime in IMAGE_TYPES.items():
        if data[:len(magic)] == magic:
            return "image", mime
    return "", ""


def read_proposal(data, filename="", spoken="", roof_type=""):
    """(fields, error). Never raises: a failed read must leave the homeowner a
    quote they can still fill in by hand.

    `data` is a PDF or a photo. `spoken` is the homeowner describing the quote
    out loud, used alone when there is no document at all. `roof_type` narrows
    the questions to the ones that apply to this roof.
    """
    import base64
    try:
        import anthropic
        import config as _config
    except Exception as e:
        return None, "reader unavailable: %s" % e
    key = (getattr(_config, "ANTHROPIC_API_KEY", "") or "").strip()
    if not key:
        return None, "reader not configured"
    try:
        from modules.ai import anthropic_create
        client = anthropic.Anthropic(api_key=key)
        resp = anthropic_create(
            client,
            model=READER_MODEL,
            # 104 criteria means 208 schema properties, and a proposal that
            # actually says something about most of them runs to roughly 4,000
            # output tokens. At 3,000 the tool call was cut off part-way and
            # whatever had not been emitted yet silently came back as "not
            # stated" -- so a detailed quote lost its later answers while a
            # sparse one kept all of them, which reads as the reader ignoring
            # the materials on some quotes and not others.
            max_tokens=16000,
            temperature=0,   # reading a document, not composing one
            tools=[_reader_tool(keys_for(roof_type) if roof_type else None)],
            tool_choice={"type": "tool", "name": "read_roofing_proposal"},
            messages=[{"role": "user", "content": _blocks(data, spoken)}],
        )
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, str(e)[:200])
    # A truncated tool call still arrives as a tool_use block with whatever
    # parsed, which is why this failed quietly before. Treat it as the failure
    # it is rather than passing half a reading off as a whole one.
    if getattr(resp, "stop_reason", "") == "max_tokens":
        return None, ("the reader ran out of room before it finished this "
                      "document -- nothing has been filled in, so the quote is "
                      "yours to complete by hand")
    for block in getattr(resp, "content", []) or []:
        if getattr(block, "type", "") == "tool_use":
            return dict(getattr(block, "input", {}) or {}), ""
    return None, "the reader returned no answer"


def _fields_to_quote(fields):
    """Turn the reader's answers into a ho_quotes row. Unknown keys are dropped
    rather than trusted, so a hallucinated line item cannot enter the sheet."""
    spec = {}
    for key in SPEC_KEYS:
        v = str((fields.get("v_" + key) or "?")).strip().lower()
        if v not in ("yes", "no", "?"):
            v = "?"
        d = str(fields.get("d_" + key) or "").strip()[:300]
        if v != "?" or d:
            spec[key] = {"v": v, "d": d}
    return {
        "contractor": str(fields.get("contractor") or "").strip()[:160] or "Unnamed contractor",
        "price": str(fields.get("price") or "").strip()[:40],
        "phone": str(fields.get("phone") or "").strip()[:40],
        "email": str(fields.get("email") or "").strip()[:200],
        "license": str(fields.get("license") or "").strip()[:80],
        "spec": json.dumps(spec),
        "notes": "",
        "source": "upload",
        "reviewed": 0,
    }


def ingest_spoken(project, spoken):
    """A quote described out loud and nothing else. Recorded as its own upload so
    it sits in the same log, and marked so the sheet shows where it came from."""
    spoken = (spoken or "").strip()[:6000]
    up = {"project_id": project["id"], "quote_id": None, "created": db.now(),
          "filename": "Described out loud", "stored": "", "bytes": len(spoken),
          "status": "pending", "error": "", "extracted": ""}
    if len(spoken) < 25:
        up["status"] = "failed"
        up["error"] = "Too short to read anything from. Say the contractor's name, the price, and what they said is included."
        return db.insert("ho_uploads", up)
    fields, err = read_proposal(b"", "", spoken=spoken)
    if err or not fields:
        up["status"] = "unread"
        up["error"] = (err or "")[:300]
        return db.insert("ho_uploads", up)
    row = _fields_to_quote(fields)
    row["project_id"] = project["id"]
    row["created"] = db.now()
    row["source"] = "spoken"
    row["notes"] = "Described out loud: " + spoken[:1500]
    qid = db.insert("ho_quotes", row)
    up["quote_id"] = qid
    up["status"] = "read"
    up["extracted"] = json.dumps(fields)[:8000]
    return db.insert("ho_uploads", up)


def store_upload(project, fileobj):
    """Validate and save one uploaded proposal. Returns (upload_id, data).

    Reading it is NOT done here. Each read costs about 25 seconds against the
    model, and doing that inside the request meant two proposals took 50s and
    sometimes returned 502, while six -- the number the form invites -- would
    pass the worker timeout every time. Store the bytes, answer the browser,
    and let read_queued() do the slow part on its own thread.

    data is returned so the reader does not have to go back to disk; it is
    None when there is nothing worth reading.
    """
    name = os.path.basename(getattr(fileobj, "filename", "") or "proposal.pdf")[:180]
    data = fileobj.read(MAX_UPLOAD_BYTES + 1)
    up = {"project_id": project["id"], "quote_id": None, "created": db.now(),
          "filename": name, "stored": "", "bytes": len(data),
          "status": "pending", "error": "", "extracted": ""}
    if len(data) > MAX_UPLOAD_BYTES:
        up["status"] = "failed"
        up["error"] = "That file is over 12 MB. Most proposals are well under 1 MB."
        return db.insert("ho_uploads", up), None
    kind, mime = sniff(data)
    if not kind:
        up["status"] = "failed"
        up["error"] = ("That file is neither a PDF nor a photo. Upload the proposal "
                       "the contractor sent, or take a picture of it.")
        return db.insert("ho_uploads", up), None

    ext = {"application/pdf": "pdf", "image/jpeg": "jpg", "image/png": "png",
           "image/gif": "gif", "image/webp": "webp"}.get(mime, "bin")
    stored = "%s/%s-%s.%s" % (UPLOAD_SUBDIR, project["token"][:8],
                              secrets.token_hex(6), ext)
    try:
        import config
        # _upload_dir() is what creates the subfolder. Without this call the
        # write fails with ENOENT and the homeowner's document is lost while the
        # read still appears to run -- the exact failure this ordering exists to
        # prevent.
        _upload_dir()
        with open(os.path.join(config.UPLOAD_DIR, stored), "wb") as fh:
            fh.write(data)
        up["stored"] = stored
    except Exception as e:
        up["error"] = "could not store the file: %s" % str(e)[:120]

    up["status"] = "queued"
    return db.insert("ho_uploads", up), data


# A read takes ~25s. Doing them one at a time keeps a homeowner with six
# proposals from opening six concurrent model calls from one worker.
_READ_LOCK = threading.Lock()


def read_queued(project_id, jobs, roof_type=""):
    """Read stored proposals and turn each into a quote. Runs off-request.

    db.connect() opens its own connection per call and the only Flask-bound
    thing in db is get_company()'s per-request cache, which already tolerates
    being outside a request, so this needs no app context.
    """
    with _READ_LOCK:
        for upload_id, data, name in jobs:
            patch = {}
            try:
                fields, err = read_proposal(data, name, roof_type=roof_type)
                if err or not fields:
                    patch = {"status": "unread", "error": (err or "")[:300]}
                elif fields.get("unreadable"):
                    patch = {"status": "unread",
                             "error": "This does not read as a roofing proposal.",
                             "extracted": json.dumps(fields)[:8000]}
                else:
                    row = _fields_to_quote(fields)
                    row["project_id"] = project_id
                    row["created"] = db.now()
                    patch = {"status": "read", "quote_id": db.insert("ho_quotes", row),
                             "extracted": json.dumps(fields)[:8000]}
            except Exception as e:
                patch = {"status": "unread",
                         "error": ("%s: %s" % (type(e).__name__, e))[:300]}
            try:
                db.update("ho_uploads", upload_id, **patch)
            except Exception:
                pass



# ---------------------------------------------------------------------------
# Verifying the company and the license
#
# Checked against both registries before this was written, because a broken
# "verify here" link on a page telling people to verify is worse than no link:
#
#   Sunbiz DOES deep-link. search.sunbiz.org/Inquiry/CorporationSearch/
#   SearchResults?inquiryType=EntityName&searchTerm=... returns the real result
#   list in a browser, so the company name becomes one click. (It answers 403
#   to a plain script, which is bot protection, not a broken URL -- confirmed by
#   loading it in a real browser and reading the rows back.)
#
#   DBPR DOES NOT. myfloridalicense.com/wl11.asp now redirects to
#   /portalsearches/VerifyLicensee, whose search is a POST carrying an
#   anti-forgery token. No GET form of it returns a record -- three variants
#   tested, all either 302 to the empty form or ignore the number. So the
#   license number cannot be deep-linked by anyone, us included. The honest
#   build is the verify page plus the number ready to paste.
SUNBIZ_SEARCH = ("https://search.sunbiz.org/Inquiry/CorporationSearch/SearchResults"
                 "?inquiryType=EntityName&searchNameOrder=&searchTerm=%s")
DBPR_VERIFY = "https://www.myfloridalicense.com/portalsearches/VerifyLicensee"


def license_audit(quotes):
    """One row per quote: where to look it up, and what is missing.

    This checks the PAPERWORK, not the contractor. A missing license number on
    a proposal means the proposal does not state one -- it establishes nothing
    about whether the company holds a license, and the wording says so.
    """
    import urllib.parse
    seen_lic = {}
    for q in quotes:
        lic = re.sub(r"[^A-Za-z0-9]", "", (q.get("license") or "")).upper()
        if lic:
            seen_lic.setdefault(lic, []).append(q.get("contractor") or "Unnamed contractor")

    rows = []
    for q in quotes:
        name = (q.get("contractor") or "").strip()
        lic_raw = (q.get("license") or "").strip()
        lic = re.sub(r"[^A-Za-z0-9]", "", lic_raw).upper()
        flags = []
        if not name:
            flags.append("This proposal does not name the company anywhere we could read.")
        if not lic_raw:
            flags.append("No license number appears on this proposal. That is a question "
                         "to ask, not a finding \u2014 plenty of licensed contractors "
                         "leave it off the quote.")
        if lic and len(seen_lic.get(lic, [])) > 1:
            others = [n for n in seen_lic[lic] if n != name]
            # "KLR Roofing Corp.." -- a company name that already ends in a
            # full stop should not collect a second one.
            joined = " and ".join(n.rstrip(".") for n in others) or "another quote"
            flags.append("The same license number appears on %s. Worth asking who is "
                         "actually contracting." % joined)
        rows.append({
            "contractor": name or "Unnamed contractor",
            "license": lic_raw,
            "phone": (q.get("phone") or "").strip(),
            "email": (q.get("email") or "").strip(),
            "sunbiz": (SUNBIZ_SEARCH % urllib.parse.quote(name)) if name else "",
            "dbpr": DBPR_VERIFY,
            "flags": flags,
        })
    return rows


def duplicate_groups(quotes):
    """Quotes that are the same bid entered twice.

    Same contractor and same quoted price is the signal. It happens because a
    slow upload looked like a dead one and the homeowner pressed the button
    again -- so the duplicates are byte-identical reads of one PDF, not two
    genuinely similar offers. Anything with a different price is left alone:
    two real bids from one company at two prices is a revision, and deciding
    which of those to drop is the homeowner's call, not ours.
    """
    seen = {}
    for q in quotes:
        key = ((q.get("contractor") or "").strip().lower(),
               (q.get("price") or "").strip().lower())
        if not key[0] and not key[1]:
            continue
        seen.setdefault(key, []).append(q)
    return [g for g in seen.values() if len(g) > 1]


DOC_SUBDIR = "ho_docs"


def store_doc(project, stage, fileobj):
    """Save one job document. Returns (id, data). Reading is somebody else's job."""
    name = os.path.basename(getattr(fileobj, "filename", "") or "document.pdf")[:180]
    data = fileobj.read(MAX_UPLOAD_BYTES + 1)
    row = {"project_id": project["id"], "stage": stage, "created": db.now(),
           "filename": name, "stored": "", "bytes": len(data),
           "status": "filed", "error": "", "extracted": "", "note": ""}
    if len(data) > MAX_UPLOAD_BYTES:
        row["status"] = "failed"
        row["error"] = "That file is over 12 MB."
        return db.insert("ho_docs", row), None
    kind, mime = sniff(data)
    if not kind:
        row["status"] = "failed"
        row["error"] = "That is neither a PDF nor a photo."
        return db.insert("ho_docs", row), None
    ext = {"application/pdf": "pdf", "image/jpeg": "jpg", "image/png": "png",
           "image/gif": "gif", "image/webp": "webp"}.get(mime, "bin")
    stored = "%s/%s-%s.%s" % (DOC_SUBDIR, project["token"][:8],
                              secrets.token_hex(6), ext)
    try:
        import config
        d = os.path.join(config.UPLOAD_DIR, DOC_SUBDIR)
        if not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        with open(os.path.join(config.UPLOAD_DIR, stored), "wb") as fh:
            fh.write(data)
        row["stored"] = stored
    except Exception as e:
        row["error"] = "could not store the file: %s" % str(e)[:120]
    if stage == "signed":
        row["status"] = "queued"
    return db.insert("ho_docs", row), data


def read_signed(project_id, quote_id, jobs, roof_type=""):
    """Read a signed contract and record how it differs from what was quoted."""
    with _READ_LOCK:
        for doc_id, data, name in jobs:
            patch = {}
            try:
                fields, err = read_proposal(data, name, roof_type=roof_type)
                if err or not fields:
                    patch = {"status": "filed", "error": (err or "")[:300]}
                else:
                    patch = {"status": "read", "extracted": json.dumps(fields)[:8000]}
            except Exception as e:
                patch = {"status": "filed",
                         "error": ("%s: %s" % (type(e).__name__, e))[:300]}
            try:
                db.update("ho_docs", doc_id, **patch)
            except Exception:
                pass


def signed_vs_proposed(project, quotes):
    """What the signed contract says that the accepted quote did not, and vice versa.

    This compares two readings of two documents. It is not legal advice and it
    is not a finding that anybody changed anything: a reader can misread, and
    the page says so. It is a list of lines worth looking at again with both
    documents open.
    """
    qid = project.get("chosen_quote_id")
    if not qid:
        return None
    quote = None
    for q in quotes:
        if q["id"] == qid:
            quote = q
            break
    if not quote:
        return None
    docs = db.all_rows("ho_docs", "project_id=? AND stage=? AND status=?",
                       (project["id"], "signed", "read"), "id DESC", 1)
    if not docs:
        return None
    try:
        fields = json.loads(docs[0].get("extracted") or "{}")
    except Exception:
        return None

    qspec = _spec_of(quote)
    rt = (project.get("roof_type") or "").strip().lower()
    changed, added, dropped = [], [], []
    for key in (keys_for(rt) if rt else SPEC_KEYS):
        item = SPEC_INDEX[key]
        was = _val(qspec, key)
        now = str((fields.get("v_" + key) or "?")).strip().lower()
        if now not in ("yes", "no", "?"):
            now = "?"
        wd = ((qspec.get(key) or {}).get("d") or "").strip() if isinstance(qspec.get(key), dict) else ""
        nd = (fields.get("d_" + key) or "").strip()
        row = dict(item, was=was, now=now, was_detail=wd, now_detail=nd)
        if was == now:
            # Same state, different wording, is still worth a second look.
            if wd and nd and wd.lower() != nd.lower():
                changed.append(row)
            continue
        if was == "yes" and now != "yes":
            dropped.append(row)
        elif now == "yes" and was != "yes":
            added.append(row)
        else:
            changed.append(row)
    price_q = (quote.get("price") or "").strip()
    price_s = (fields.get("price") or "").strip()
    return {
        "quote": quote, "doc": docs[0],
        "price_quoted": price_q, "price_signed": price_s,
        "price_differs": bool(price_q and price_s
                              and re.sub(r"[^0-9.]", "", price_q) != re.sub(r"[^0-9.]", "", price_s)),
        "dropped": dropped, "added": added, "changed": changed,
        "total": len(dropped) + len(added) + len(changed),
    }


def _project_by_token(token):
    rows = db.all_rows("ho_projects", "token=?", (token,), "id DESC", 1)
    if not rows:
        abort(404)
    return rows[0]


def docs_by_stage(pid):
    """Everything filed, grouped by the stage it belongs to."""
    out = {}
    for d in db.all_rows("ho_docs", "project_id=?", (pid,), "id DESC", 200):
        out.setdefault(d.get("stage") or "comparing", []).append(d)
    return out


def _quotes_for(pid):
    return db.all_rows("ho_quotes", "project_id=?", (pid,), "id ASC")


def _demo_slug():
    try:
        from modules.demos import DEMO_SLUG
        return DEMO_SLUG
    except Exception:
        return "roof-portal"


def _log(kind, path=""):
    try:
        db.insert("portal_events", {
            "created": db.now(), "kind": kind, "path": path or request.path,
            "referrer": (request.referrer or "")[:300],
            "ua": (request.headers.get("User-Agent") or "")[:300],
            "source_host": (request.host or "")[:120]})
    except Exception:
        pass


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------
@bp.route("/compare", endpoint="landing")
def landing():
    """The homeowner front door.

    On a demo host (myroofportal.com) this IS "/" — app.py renders this view in
    place for the bare domain. /compare then 301s to "/" so the same page is not
    indexed at two URLs. On the CRM host "/" is the dashboard, so /compare stays
    a real page there and must not redirect.
    """
    try:
        from modules.demos import _on_demo_host
        if _on_demo_host() and request.path.rstrip("/") == "/compare":
            return redirect("/", code=301)
    except Exception:
        pass
    _log("compare_view")
    # portal_base's footer links these; without them the anchors render
    # href="" and silently go nowhere.
    # The page shows a handful of real questions rather than the whole sheet:
    # 104 line items printed on a front page is a wall, not an argument. These
    # are the ones that decide money on nearly every quote.
    # The roof cards promise questions for the system you pick, and the page
    # was showing the same four whatever you clicked -- and one of them was
    # tile attachment, so a metal owner was told metal questions and shown
    # tile. Build a set per system: the lines that apply ONLY to that roof
    # first, because those are what make it system-specific, then the shared
    # ones that move money on every job.
    pool = [i for g in SPEC for i in g["items"] if i.get("featured") and i.get("ask")]
    general = ["vet_15", "vet_26", "vet_31", "vet_07"]
    # Two lead every set regardless of roof. Whether the company is licensed AND
    # insured is the one question that applies before any roofing detail does,
    # and direct-to-deck versus a fastened base sheet is the assembly decision
    # that separates two tile quotes that otherwise read the same.
    LEAD = ["vet_02", "vet_13"]
    by_key = {i["key"]: i for g in SPEC for i in g["items"] for i in [i]
              if i.get("ask")}

    def _four(rt):
        out, seen = [], set()
        for k in LEAD:
            i = by_key.get(k)
            if i and (not rt or rt in (i.get("types") or ())):
                out.append(i); seen.add(k)
        if rt:
            # Most specific first: a line tagged for this roof alone says more
            # about it than one shared with two others. Without this a membrane
            # line led the tile, shingle AND metal sets, which is the opposite
            # of system-specific.
            cands = [i for i in pool
                     if rt in (i.get("types") or ()) and len(i.get("types") or ()) < 4]
            cands.sort(key=lambda i: (len(i.get("types") or ()), i["label"]))
            for i in cands:
                if i["key"] not in seen:
                    out.append(i); seen.add(i["key"])
        for k in general:                        # then the shared money lines
            i = by_key.get(k)
            if i and i["key"] not in seen and (not rt or rt in (i.get("types") or ())):
                out.append(i); seen.add(i["key"])
        for i in pool:                           # then anything else that fits
            if len(out) >= 4:
                break
            if i["key"] not in seen and (not rt or rt in (i.get("types") or ())):
                out.append(i); seen.add(i["key"])
        return out[:4]

    def _carded(items):
        return [dict(i, short=short_ask(i["ask"])) for i in items]

    questions = _carded(_four(""))
    questions_by_roof = {rt: _carded(_four(rt))
                         for rt in ROOF_TYPE_KEYS if rt != "unsure"}
    return render_template("compare_landing.html", spec=SPEC,
                           beacon_kind="homeowner_view",
                           spec_count=len(SPEC_KEYS), stages=STAGES,
                           questions=questions, questions_by_roof=questions_by_roof,
                           demo_url="/demo/%s" % _demo_slug(),
                           acquire_url="/acquire")


def _posted_roof_type():
    """The front page now asks which system before the files go up, because the
    sheet it fills in is different per system. "Not sure" posts empty, which is
    a real answer -- it means ask everything -- so it is stored as empty rather
    than rejected."""
    rt = (request.form.get("roof_type") or "").strip().lower()
    return rt if rt in ROOF_TYPE_KEYS else ""


@bp.route("/compare/start", methods=["POST"], endpoint="start")
def start():
    email = (request.form.get("email") or "").strip()[:200]
    if not _EMAIL_RE.match(email):
        return redirect(url_for("compare.landing") + "?err=1#start")
    name = (request.form.get("name") or "").strip()[:120]
    # An e-mail address is not a secret. Returning somebody's workspace to
    # anyone who types their address handed over their uploaded proposals and
    # their property address to whoever guessed it -- and there is no mail
    # going out, so there was not even a notification. The link is the key; an
    # address that already has a workspace is told to use it.
    if db.all_rows("ho_projects", "email=?", (email,), "id DESC", 1):
        _log("compare_exists")
        return redirect(url_for("compare.landing") + "?exists=1#start")
    token = _tok()
    db.insert("ho_projects", {
        "created": db.now(), "token": token, "email": email, "name": name,
        "address": (request.form.get("address") or "").strip()[:200],
        "city": "", "state": "", "zip": "", "stage": "comparing", "notes": "",
        "roof_type": _posted_roof_type(),
        "source_host": (request.host or "")[:120], "last_seen": db.now()})
    _log("compare_start")
    return redirect(url_for("compare.project", token=token))


@bp.route("/compare/upload", methods=["POST"], endpoint="landing_upload")
def landing_upload():
    """Upload straight from the front page: the homeowner has the PDFs open and
    no reason yet to care what an account is. The e-mail still creates the
    workspace -- it is how they get back in -- but the files come first."""
    # The hero uploader asks for files and nothing else: a homeowner holding
    # three PDFs should not have to think about an account first. A blank email
    # is allowed through and the workspace asks for one straight away, so the
    # only thing at risk is the way back in -- and that is said plainly there.
    # A MALFORMED address is still refused, because silently dropping what
    # someone typed would lose their way back without telling them.
    email = (request.form.get("email") or "").strip()[:200]
    if email and not _EMAIL_RE.match(email):
        return redirect(url_for("compare.landing") + "?err=1#start")
    rt = _posted_roof_type()
    # An e-mail address is not a secret. Returning somebody's workspace to
    # anyone who types their address handed over their uploaded proposals and
    # their property address to whoever guessed it -- and there is no mail
    # going out, so there was not even a notification. The link is the key; an
    # address that already has a workspace is told to use it.
    if email and db.all_rows("ho_projects", "email=?", (email,), "id DESC", 1):
        _log("compare_exists")
        return redirect(url_for("compare.landing") + "?exists=1#start")
    if True:
        token = _tok()
        db.insert("ho_projects", {
            "created": db.now(), "token": token, "email": email,
            "name": (request.form.get("name") or "").strip()[:120],
            "address": (request.form.get("address") or "").strip()[:200],
            "city": "", "state": "", "zip": "", "stage": "comparing", "notes": "",
            "roof_type": rt,
            "source_host": (request.host or "")[:120], "last_seen": db.now()})
        p = db.all_rows("ho_projects", "token=?", (token,), "id DESC", 1)[0]
    n = _ingest_files(p, request.files.getlist("quotes"))
    _log("compare_upload_start" if n else "compare_start")
    return redirect(url_for("compare.project", token=p["token"]) +
                    ("#quotes" if n else "#add"))


@bp.route("/c/<token>/upload", methods=["POST"], endpoint="upload")
def upload(token):
    p = _project_by_token(token)
    n = _ingest_files(p, request.files.getlist("quotes"))
    _log("compare_upload", "/c/<token>/upload")
    return redirect(url_for("compare.project", token=token) +
                    ("#quotes" if n else "#upload"))


def _ingest_files(project, files):
    """Store each file independently, then read them all on one thread.

    One bad PDF in a batch of three must not cost the homeowner the other two,
    and the browser must not wait on any of it.
    """
    done, jobs = 0, []
    for f in (files or [])[:6]:
        if not getattr(f, "filename", ""):
            continue
        try:
            upload_id, data = store_upload(project, f)
            done += 1
            if data:
                jobs.append((upload_id, data,
                             os.path.basename(getattr(f, "filename", "") or "")[:180]))
        except Exception as e:
            try:
                db.insert("ho_uploads", {
                    "project_id": project["id"], "quote_id": None,
                    "created": db.now(),
                    "filename": os.path.basename(getattr(f, "filename", "") or "")[:180],
                    "stored": "", "bytes": 0, "status": "failed",
                    "error": ("%s: %s" % (type(e).__name__, e))[:300], "extracted": ""})
            except Exception:
                pass
    if jobs:
        t = threading.Thread(target=read_queued,
                             args=(project["id"], jobs,
                                   (project.get("roof_type") or "").strip().lower()),
                             name="roofread", daemon=True)
        t.start()
    return done


@bp.route("/c/<token>/describe", methods=["POST"], endpoint="describe")
def describe(token):
    """No PDF, no photo -- the homeowner just says what they were told. Common
    when the quote was verbal, or the paper copy is at the office."""
    p = _project_by_token(token)
    try:
        ingest_spoken(p, request.form.get("spoken") or "")
    except Exception as e:
        try:
            db.insert("ho_uploads", {
                "project_id": p["id"], "quote_id": None, "created": db.now(),
                "filename": "Described out loud", "stored": "", "bytes": 0,
                "status": "failed", "error": ("%s: %s" % (type(e).__name__, e))[:300],
                "extracted": ""})
        except Exception:
            pass
    _log("compare_spoken", "/c/<token>/describe")
    return redirect(url_for("compare.project", token=token) + "#quotes")


@bp.route("/c/<token>/quote/<int:qid>/confirm", methods=["POST"], endpoint="confirm_quote")
def confirm_quote(token, qid):
    """The homeowner says the machine read this one correctly. Until this is
    pressed the quote is shown as unchecked everywhere it appears."""
    p = _project_by_token(token)
    q = db.get("ho_quotes", qid)
    if not q or q.get("project_id") != p["id"]:
        abort(404)
    db.update("ho_quotes", qid, reviewed=1)
    _log("compare_quote_confirmed", "/c/<token>/quote/confirm")
    return redirect(url_for("compare.project", token=token) + "#quotes")


@bp.route("/c/<token>", endpoint="project")
def project(token):
    p = _project_by_token(token)
    quotes = _quotes_for(p["id"])
    try:
        db.update("ho_projects", p["id"], last_seen=db.now())
    except Exception:
        pass
    _log("compare_project", "/c/<token>")
    rt = (p.get("roof_type") or "").strip().lower()
    show_all = request.args.get("all") == "1"
    view = []
    for q in quotes:
        view.append(dict(q, _spec=_spec_of(q),
                         _gaps=gaps_for(q, rt, not show_all)))
    uploads = db.all_rows("ho_uploads", "project_id=?", (p["id"],), "id DESC", 20)
    # A queued upload is being read right now on a background thread. The page
    # has to say so and come back for the result, or a homeowner lands on an
    # empty workspace seconds after uploading and concludes it failed.
    reading = [u for u in uploads if (u.get("status") or "") == "queued"]
    return render_template(
        "compare_project.html", beacon_kind="homeowner_workspace",
        p=p, quotes=view, reading=len(reading),
        dupes=sum(len(g) - 1 for g in duplicate_groups(quotes)),
        scores=scoreboard(quotes, rt), detail=disagreement_detail(quotes, rt),
        licenses=license_audit(quotes),
        docs_by_stage=docs_by_stage(p["id"]),
        chosen_id=p.get("chosen_quote_id"),
        signed_diff=signed_vs_proposed(p, quotes),
        spec=spec_view(rt, show_all), uploads=uploads,
        roof_types=ROOF_TYPES, roof_type=rt,
        show_all=show_all, counts=counts_for(rt),
        spec_keys=SPEC_KEYS, stages=STAGES,
        stage=STAGE_INDEX.get(p.get("stage") or "comparing", STAGES[0]),
        stage_keys=STAGE_KEYS,
        disagree=disagreements(quotes, rt))


@bp.route("/c/<token>/quote", methods=["POST"], endpoint="save_quote")
def save_quote(token):
    p = _project_by_token(token)
    qid = (request.form.get("qid") or "").strip()
    spec = {}
    for key in SPEC_KEYS:
        v = (request.form.get("v_" + key) or "?").strip()
        if v not in ("yes", "no", "?"):
            v = "?"
        d = (request.form.get("d_" + key) or "").strip()[:300]
        if v != "?" or d:
            spec[key] = {"v": v, "d": d}
    row = {
        "contractor": (request.form.get("contractor") or "").strip()[:160],
        "phone": (request.form.get("phone") or "").strip()[:40],
        "email": (request.form.get("email") or "").strip()[:200],
        "license": (request.form.get("license") or "").strip()[:80],
        "price": (request.form.get("price") or "").strip()[:40],
        "notes": (request.form.get("notes") or "").strip()[:2000],
        "spec": json.dumps(spec),
    }
    if not row["contractor"]:
        return redirect(url_for("compare.project", token=token) + "?err=name")
    # Anything a person typed or corrected by hand counts as reviewed -- that is
    # exactly the confirmation the badge on an uploaded quote is waiting for.
    row["reviewed"] = 1
    if qid.isdigit():
        existing = db.get("ho_quotes", int(qid))
        if not existing or existing.get("project_id") != p["id"]:
            abort(404)
        db.update("ho_quotes", int(qid), **row)
    else:
        row["project_id"] = p["id"]
        row["source"] = "manual"
        row["created"] = db.now()
        db.insert("ho_quotes", row)
    _log("compare_quote_saved", "/c/<token>/quote")
    return redirect(url_for("compare.project", token=token) + "#quotes")


@bp.route("/c/<token>/quote/<int:qid>/delete", methods=["POST"], endpoint="del_quote")
def del_quote(token, qid):
    p = _project_by_token(token)
    q = db.get("ho_quotes", qid)
    if not q or q.get("project_id") != p["id"]:
        abort(404)
    db.delete("ho_quotes", qid)
    return redirect(url_for("compare.project", token=token) + "#quotes")


@bp.route("/c/<token>/dedupe", methods=["POST"], endpoint="dedupe")
def dedupe(token):
    """Drop the extra copies, keeping the first of each group.

    The oldest row is kept because it is the one the homeowner has had longest
    and may already have corrected by hand.
    """
    p = _project_by_token(token)
    removed = 0
    for group in duplicate_groups(_quotes_for(p["id"])):
        for q in sorted(group, key=lambda r: r["id"])[1:]:
            try:
                db.delete("ho_quotes", q["id"])
                removed += 1
            except Exception:
                pass
    _log("compare_dedupe")
    return redirect(url_for("compare.project", token=token) +
                    ("?removed=%d#compare" % removed if removed else "#compare"))


@bp.route("/c/<token>/email", methods=["POST"], endpoint="set_email")
def set_email(token):
    """Attach an email to a workspace that was started without one."""
    p = _project_by_token(token)
    email = (request.form.get("email") or "").strip()[:200]
    if not _EMAIL_RE.match(email):
        return redirect(url_for("compare.project", token=token) + "?emailerr=1")
    db.update("ho_projects", p["id"], email=email, last_seen=db.now())
    _log("compare_email_added", "/c/<token>/email")
    return redirect(url_for("compare.project", token=token) + "?saved=1")


@bp.route("/c/<token>/choose", methods=["POST"], endpoint="choose_quote")
def choose_quote(token):
    """Record which quote was accepted.

    Nothing else can compare a signed contract against what was proposed until
    this is known, and it is the homeowner's decision rather than ours -- the
    sheet never picks for them.
    """
    p = _project_by_token(token)
    qid = (request.form.get("qid") or "").strip()
    chosen = None
    if qid.isdigit():
        q = db.get("ho_quotes", int(qid))
        if q and q.get("project_id") == p["id"]:
            chosen = int(qid)
    db.update("ho_projects", p["id"], chosen_quote_id=chosen, last_seen=db.now())
    _log("compare_quote_chosen", "/c/<token>/choose")
    return redirect(url_for("compare.project", token=token) + "#track")


@bp.route("/c/<token>/doc", methods=["POST"], endpoint="upload_doc")
def upload_doc(token):
    """File a document against a stage of the job."""
    p = _project_by_token(token)
    stage = (request.form.get("stage") or "").strip()
    if stage not in STAGE_INDEX:
        stage = (p.get("stage") or "comparing")
    jobs = []
    for f in request.files.getlist("docs")[:6]:
        if not getattr(f, "filename", ""):
            continue
        try:
            doc_id, data = store_doc(p, stage, f)
            # Only the signed contract is worth reading: it is the one document
            # that can be checked against something we already hold.
            if data and stage == "signed" and p.get("chosen_quote_id"):
                jobs.append((doc_id, data,
                             os.path.basename(getattr(f, "filename", ""))[:180]))
        except Exception:
            pass
    if jobs:
        t = threading.Thread(target=read_signed, name="roofsigned", daemon=True,
                             args=(p["id"], int(p["chosen_quote_id"]), jobs,
                                   (p.get("roof_type") or "").strip().lower()))
        t.start()
    _log("compare_doc_" + stage, "/c/<token>/doc")
    return redirect(url_for("compare.project", token=token) + "#track")


@bp.route("/c/<token>/doc/<int:did>/delete", methods=["POST"], endpoint="del_doc")
def del_doc(token, did):
    p = _project_by_token(token)
    d = db.get("ho_docs", did)
    if not d or d.get("project_id") != p["id"]:
        abort(404)
    db.delete("ho_docs", did)
    return redirect(url_for("compare.project", token=token) + "#track")


@bp.route("/c/<token>/roof", methods=["POST"], endpoint="set_roof")
def set_roof(token):
    """Which roof this is. It decides which questions the sheet asks, so it is a
    real answer and not a label: a tile owner has no use for starter strips and
    a shingle owner has none for foam paddies."""
    p = _project_by_token(token)
    rt = (request.form.get("roof_type") or "").strip().lower()
    if rt in ROOF_TYPE_KEYS:
        db.update("ho_projects", p["id"], roof_type=rt)
        _log("compare_roof_" + rt, "/c/<token>/roof")
    return redirect(url_for("compare.project", token=token) + "#compare")


@bp.route("/c/<token>/stage", methods=["POST"], endpoint="set_stage")
def set_stage(token):
    p = _project_by_token(token)
    s = (request.form.get("stage") or "").strip()
    if s in STAGE_INDEX:
        db.update("ho_projects", p["id"], stage=s)
        _log("compare_stage_" + s, "/c/<token>/stage")
    return redirect(url_for("compare.project", token=token) + "#track")


@bp.route("/c/<token>/note", methods=["POST"], endpoint="save_note")
def save_note(token):
    p = _project_by_token(token)
    db.update("ho_projects", p["id"],
              notes=(request.form.get("notes") or "").strip()[:8000])
    return redirect(url_for("compare.project", token=token) + "#notes")
