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
and we do not see it. Every judgement stays with the homeowner; this only makes
sure nothing is invisible when they make it.
"""
import json
import os
import re
import secrets

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
for _t, _c, _d in (("ho_projects", "roof_type", "TEXT"),
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

SPEC_INDEX = {i["key"]: i for g in SPEC for i in g["items"]}
SPEC_KEYS = [i["key"] for g in SPEC for i in g["items"]]

# Roof types the sheet knows. "unsure" keeps every group visible rather than
# hiding a question from someone who does not yet know what they have.
ROOF_TYPES = [("tile", "Tile"), ("shingle", "Shingle"), ("metal", "Metal"),
              ("flat", "Flat / low-slope"), ("unsure", "Not sure yet")]
ROOF_TYPE_KEYS = [k for k, _ in ROOF_TYPES]


def spec_for(roof_type):
    """The groups that apply to this roof. Common groups always; a type-specific
    group only for its own type. An unknown or missing type shows everything,
    because hiding a question is worse than asking one that does not apply."""
    rt = (roof_type or "").strip().lower()
    if rt not in ROOF_TYPE_KEYS or rt == "unsure":
        return list(SPEC)
    out = []
    for g in SPEC:
        types = g.get("types") or ("all",)
        if "all" in types or rt in types:
            out.append(g)
    return out


def keys_for(roof_type, featured_only=False):
    return [i["key"] for g in spec_for(roof_type) for i in g["items"]
            if not featured_only or i.get("featured")]


def spec_view(roof_type, show_all=False):
    """Groups for the page. 104 lines is the right size for a rule set and far
    too many to hand-fill, so the default is the FEATURED subset for this roof
    and "show everything" opens the rest. A group with nothing featured in it
    disappears rather than rendering empty."""
    out = []
    for g in spec_for(roof_type):
        items = g["items"] if show_all else [i for i in g["items"] if i.get("featured")]
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


def read_proposal(data, filename="", spoken=""):
    """(fields, error). Never raises: a failed read must leave the homeowner a
    quote they can still fill in by hand.

    `data` is a PDF or a photo. `spoken` is the homeowner describing the quote
    out loud, used alone when there is no document at all.
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
            max_tokens=3000,
            temperature=0,   # reading a document, not composing one
            tools=[_reader_tool()],
            tool_choice={"type": "tool", "name": "read_roofing_proposal"},
            messages=[{"role": "user", "content": _blocks(data, spoken)}],
        )
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, str(e)[:200])
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


def ingest_upload(project, fileobj):
    """Store one uploaded proposal, read it, and create its quote. Returns the
    ho_uploads row id. The FILE is saved before the read is attempted, so a
    reader outage never costs the homeowner the document."""
    name = os.path.basename(getattr(fileobj, "filename", "") or "proposal.pdf")[:180]
    data = fileobj.read(MAX_UPLOAD_BYTES + 1)
    up = {"project_id": project["id"], "quote_id": None, "created": db.now(),
          "filename": name, "stored": "", "bytes": len(data),
          "status": "pending", "error": "", "extracted": ""}
    if len(data) > MAX_UPLOAD_BYTES:
        up["status"] = "failed"
        up["error"] = "That file is over 12 MB. Most proposals are well under 1 MB."
        return db.insert("ho_uploads", up)
    kind, mime = sniff(data)
    if not kind:
        up["status"] = "failed"
        up["error"] = ("That file is neither a PDF nor a photo. Upload the proposal "
                       "the contractor sent, or take a picture of it.")
        return db.insert("ho_uploads", up)

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

    fields, err = read_proposal(data, name)
    if err or not fields:
        up["status"] = "unread"
        up["error"] = (up["error"] + " " + (err or "")).strip()[:300]
        return db.insert("ho_uploads", up)
    if fields.get("unreadable"):
        up["status"] = "unread"
        up["error"] = "This does not read as a roofing proposal."
        up["extracted"] = json.dumps(fields)[:8000]
        return db.insert("ho_uploads", up)

    row = _fields_to_quote(fields)
    row["project_id"] = project["id"]
    row["created"] = db.now()
    qid = db.insert("ho_quotes", row)
    up["quote_id"] = qid
    up["status"] = "read"
    up["extracted"] = json.dumps(fields)[:8000]
    return db.insert("ho_uploads", up)


def _project_by_token(token):
    rows = db.all_rows("ho_projects", "token=?", (token,), "id DESC", 1)
    if not rows:
        abort(404)
    return rows[0]


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
    return render_template("compare_landing.html", spec=SPEC,
                           spec_count=len(SPEC_KEYS), stages=STAGES,
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
    # One workspace per e-mail: coming back with the same address returns the
    # project instead of silently starting an empty second one.
    prior = db.all_rows("ho_projects", "email=?", (email,), "id DESC", 1)
    if prior:
        db.update("ho_projects", prior[0]["id"], last_seen=db.now())
        _log("compare_return")
        return redirect(url_for("compare.project", token=prior[0]["token"]))
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
    email = (request.form.get("email") or "").strip()[:200]
    if not _EMAIL_RE.match(email):
        return redirect(url_for("compare.landing") + "?err=1#start")
    rt = _posted_roof_type()
    prior = db.all_rows("ho_projects", "email=?", (email,), "id DESC", 1)
    if prior:
        p = prior[0]
        # Returning with an answer fills a blank, but never overwrites a system
        # the homeowner already set inside their own workspace.
        fields = {"last_seen": db.now()}
        if rt and not (p.get("roof_type") or "").strip():
            fields["roof_type"] = rt
        db.update("ho_projects", p["id"], **fields)
        p = db.all_rows("ho_projects", "id=?", (p["id"],), "id DESC", 1)[0]
    else:
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
    """Read each file independently. One bad PDF in a batch of three must not
    cost the homeowner the other two."""
    done = 0
    for f in (files or [])[:6]:
        if not getattr(f, "filename", ""):
            continue
        try:
            ingest_upload(project, f)
            done += 1
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
    return render_template(
        "compare_project.html", p=p, quotes=view,
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
