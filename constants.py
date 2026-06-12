# -*- coding: utf-8 -*-
"""Domain model for the white-label roofing CRM.

These definitions mirror the working SeaBreeze `job-manager.html` (pipeline
stages, per-stage checklists, follow-up clocks, draw schedule) and the AccuLynx
estimate-template mapping. They are brand-agnostic: nothing here names a company.
Everything brandable lives in the `company_settings` table (see theme.py).
"""

# ---------------------------------------------------------------------------
# Top-level pipeline buckets (the L/P/A/C/I circles on the dashboard).
# Colors + letters verified live from my.acculynx.com (see docs/ACCULYNX_LIVE_CAPTURE.md).
# ---------------------------------------------------------------------------
BUCKETS = [
    {"key": "lead",      "letter": "L", "name": "Lead",      "color": "#F2C000"},
    {"key": "prospect",  "letter": "P", "name": "Prospect",  "color": "#F78300"},
    {"key": "approved",  "letter": "A", "name": "Approved",  "color": "#8CC63F"},
    {"key": "completed", "letter": "C", "name": "Completed", "color": "#29ABE2"},
    {"key": "invoiced",  "letter": "I", "name": "Invoiced",  "color": "#E25050"},
]
BUCKET_BY_KEY = {b["key"]: b for b in BUCKETS}

# ---------------------------------------------------------------------------
# Sales pipeline (Leads module) -- AccuLynx Lead/Prospect milestones.
# ---------------------------------------------------------------------------
LEAD_STAGES = [
    {"key": "assigned",    "name": "Assigned",     "bucket": "lead",     "color": "#F2C000", "follow_after": 2,
     "checklist": ["Called", "Texted", "Emailed", "Left voicemail"]},
    {"key": "prospect",    "name": "Prospect",     "bucket": "prospect", "color": "#F78300", "follow_after": 3,
     "checklist": ["Spoke with customer", "Inspection / measurements set", "Estimate created", "Estimate sent"]},
    {"key": "negotiation", "name": "Negotiation",  "bucket": "prospect", "color": "#F78300", "follow_after": 4,
     "checklist": ["Reviewed estimate with customer", "Addressed objections", "Offered financing", "Asked for the sale"]},
    {"key": "long_term",   "name": "Long Term",    "bucket": "prospect", "color": "#d9a23a", "follow_after": 14,
     "checklist": ["Reason logged", "Added to long-term nurture list", "Next touch scheduled"]},
    {"key": "won",         "name": "Approved → Job", "bucket": "approved", "color": "#8CC63F", "follow_after": 0,
     "checklist": ["Contract signed", "Deposit collected", "Moved to Jobs"]},
    {"key": "lost",        "name": "Dead / Lost",  "bucket": "lead",     "color": "#9aa6bb", "follow_after": 0,
     "checklist": ["Reason logged", "Removed from active pipeline"]},
]

# ---------------------------------------------------------------------------
# Production pipeline (Jobs module) -- AccuLynx milestones, EXACT names + order
# captured live for SeaBreeze REROOF. Each tagged with its top-level bucket.
# ---------------------------------------------------------------------------
JOB_STAGES = [
    {"key": "approved",          "name": "Approved",                    "bucket": "approved", "follow_after": 2,
     "checklist": ["Signed contract on file", "Deposit collected", "Job folder created"]},
    {"key": "finance_ntp",       "name": "IF NEEDED - Finance NTP",     "bucket": "approved", "follow_after": 3,
     "checklist": ["Financing approved", "Notice to proceed received"]},
    {"key": "documentation",     "name": "Documentation Needed",        "bucket": "approved", "follow_after": 3,
     "checklist": ["Signed proposal", "Color/material selection confirmed", "COI / scope docs",
                   "Photos uploaded", "Measurement report uploaded"]},
    {"key": "permit_applied",    "name": "Permit Applied For",          "bucket": "approved", "follow_after": 10,
     "checklist": ["NOC recorded", "Permit package submitted", "Fees paid", "Permit # received"]},
    {"key": "permit_approved",   "name": "Permit Approved",             "bucket": "approved", "follow_after": 5,
     "checklist": ["Permit approved", "Permit posted at job site", "Customer notified of start date"]},
    {"key": "precon_needed",     "name": "Pre Con Needed",              "bucket": "approved", "follow_after": 3,
     "checklist": ["Pre-con walkthrough scheduled", "Material ordered", "Crew assigned"]},
    {"key": "precon_complete",   "name": "Pre Con Complete",            "bucket": "approved", "follow_after": 3,
     "checklist": ["Pre-con walkthrough done", "Yard sign installed", "Dumpster scheduled"]},
    {"key": "ready_teardown",    "name": "Ready To Schedule Tear Off",  "bucket": "approved", "follow_after": 3,
     "checklist": ["Material delivered", "Crew scheduled", "Customer confirmed date"]},
    {"key": "teardown_started",  "name": "Tear Off Started",            "bucket": "approved", "follow_after": 2,
     "checklist": ["Tear-off in progress", "Permit box on site"]},
    {"key": "teardown_complete", "name": "Tear Off Completed",          "bucket": "approved", "follow_after": 2,
     "checklist": ["Tear-off complete", "Dry-in complete", "Dry-in inspection passed"]},
    {"key": "install_started",   "name": "Roof Install Started",        "bucket": "approved", "follow_after": 2,
     "checklist": ["Install in progress"]},
    {"key": "install_complete",  "name": "Roof Install Completed",      "bucket": "approved", "follow_after": 2,
     "checklist": ["Install complete", "Site cleaned / magnet sweep"]},
    {"key": "punch_needed",      "name": "Punch Out Needed",            "bucket": "approved", "follow_after": 3,
     "checklist": ["Punch list created", "Crew scheduled for punch out"]},
    {"key": "punch_complete",    "name": "Punch Out Completed",         "bucket": "approved", "follow_after": 2,
     "checklist": ["Punch list complete", "Final photos uploaded"]},
    {"key": "final_needed",      "name": "Needs Final Inspection",      "bucket": "approved", "follow_after": 4,
     "checklist": ["Final inspection requested"]},
    {"key": "final_scheduled",   "name": "Final Inspection Scheduled",  "bucket": "approved", "follow_after": 4,
     "checklist": ["Inspection date confirmed"]},
    {"key": "final_passed",      "name": "Final Inspection Passed",     "bucket": "approved", "follow_after": 2,
     "checklist": ["Final inspection passed", "Permit closed"]},
    {"key": "completed",         "name": "Completed",                   "bucket": "completed", "follow_after": 3,
     "checklist": ["Warranty issued", "Review requested", "Asked customer for 3 referrals"]},
    {"key": "invoiced",          "name": "Invoiced",                    "bucket": "invoiced", "follow_after": 5,
     "checklist": ["Final invoice sent", "Final payment received"]},
    {"key": "closed",            "name": "Closed",                      "bucket": "invoiced", "follow_after": 0,
     "checklist": ["Paid in full", "Job archived"]},
    {"key": "canceled",          "name": "Canceled",                    "bucket": "lead",      "follow_after": 0,
     "checklist": ["Reason logged"]},
]

# Inherit each job stage's color from its bucket.
for _s in JOB_STAGES:
    _s.setdefault("color", BUCKET_BY_KEY[_s["bucket"]]["color"])

LEAD_STAGE_INDEX = {s["key"]: i for i, s in enumerate(LEAD_STAGES)}
JOB_STAGE_INDEX = {s["key"]: i for i, s in enumerate(JOB_STAGES)}
LEAD_DEFAULT_STAGE = "assigned"
JOB_DEFAULT_STAGE = "approved"

# Stages that count as still "active" (excluded from closed/done totals).
JOB_INACTIVE = {"closed", "canceled"}
LEAD_INACTIVE = {"won", "lost"}


def lead_stage(key):
    return LEAD_STAGES[LEAD_STAGE_INDEX.get(key, 0)]


def job_stage(key):
    return JOB_STAGES[JOB_STAGE_INDEX.get(key, 0)]


def job_bucket(stage_key):
    return BUCKET_BY_KEY[job_stage(stage_key)["bucket"]]


# ---------------------------------------------------------------------------
# UNIFIED LIFECYCLE — the single end-to-end pipeline a customer flows through,
# spanning BOTH the lead module and the job module:
#   Lead -> Prospect -> Approved -> Completed -> Invoiced -> Closed
# Every granular lead/job stage maps onto one of these six steps so the whole
# app can show one consistent progress bar + board.
# ---------------------------------------------------------------------------
LIFECYCLE = [
    {"key": "lead",      "name": "Lead",      "color": "#F2C000"},
    {"key": "prospect",  "name": "Prospect",  "color": "#F78300"},
    {"key": "approved",  "name": "Approved",  "color": "#8CC63F"},
    {"key": "completed", "name": "Completed", "color": "#29ABE2"},
    {"key": "invoiced",  "name": "Invoiced",  "color": "#E25050"},
    {"key": "closed",    "name": "Closed",    "color": "#6B7A90"},
]
LIFECYCLE_INDEX = {s["key"]: i for i, s in enumerate(LIFECYCLE)}


def lifecycle_step(kind, stage_key):
    """Map any lead/job stage onto a unified lifecycle key."""
    if kind == "lead":
        if stage_key == "won":
            return "approved"          # a won lead has become a job
        if stage_key == "lost":
            return "closed"
        return lead_stage(stage_key).get("bucket", "lead")   # lead / prospect
    # job
    if stage_key in ("closed", "canceled"):
        return "closed"
    return job_stage(stage_key).get("bucket", "approved")    # approved / completed / invoiced


def lifecycle_pos(kind, stage_key):
    """0-based index of the record on the 6-step lifecycle."""
    return LIFECYCLE_INDEX.get(lifecycle_step(kind, stage_key), 0)


# Forward "next step" for the big primary button on a record's detail page.
# Leads walk the sales path to the win; the job module takes over from there.
_LEAD_NEXT = {"assigned": "prospect", "prospect": "negotiation",
              "long_term": "negotiation"}


def next_step(kind, stage_key):
    """Return {action, label, stage} for advancing one step, or None at a terminal.
    action: 'lead_stage' | 'convert' | 'job_stage'."""
    if kind == "lead":
        if stage_key in ("won", "lost"):
            return None
        if stage_key == "negotiation":
            return {"action": "convert", "label": "Approve → Create Job", "stage": "won"}
        nxt = _LEAD_NEXT.get(stage_key)
        if not nxt:
            return None
        return {"action": "lead_stage", "label": "Move to %s" % lead_stage(nxt)["name"], "stage": nxt}
    # job
    idx = JOB_STAGE_INDEX.get(stage_key, 0)
    if stage_key in ("closed", "canceled") or idx >= len(JOB_STAGES) - 2:
        if stage_key == "invoiced":
            return {"action": "job_stage", "label": "Mark Closed", "stage": "closed"}
        if stage_key in ("closed", "canceled"):
            return None
    nxt_stage = JOB_STAGES[min(idx + 1, len(JOB_STAGES) - 1)]
    if nxt_stage["key"] == "canceled":   # never auto-advance into Canceled
        return None
    return {"action": "job_stage", "label": "Advance to %s" % nxt_stage["name"], "stage": nxt_stage["key"]}


def stage_checklist(kind, stage_key):
    """The guided checklist items for a record's current stage."""
    sd = lead_stage(stage_key) if kind == "lead" else job_stage(stage_key)
    return sd.get("checklist", [])


# Migration map: old stage keys -> new AccuLynx milestone keys.
_OLD_JOB_STAGE_MAP = {
    "docs": "documentation", "hoa": "documentation", "permit_sub": "permit_applied",
    "permit_app": "permit_approved", "precon": "precon_needed", "started": "teardown_started",
    "final": "final_needed",
}
_OLD_LEAD_STAGE_MAP = {
    "new": "assigned", "contacted": "prospect", "appt": "prospect",
    "quoted": "prospect", "negotiating": "negotiation",
}


# ---------------------------------------------------------------------------
# Payment draw schedule (jobs only)  -- 30 / 30 / 30 / 10 (performance-based)
# ---------------------------------------------------------------------------
DRAW_SCHEDULE = [
    {"key": "p1", "label": "30% deposit — permit cost, material order & admin", "pct": 0.30},
    {"key": "p2", "label": "30% at job start — mobilization (crew shows up)",   "pct": 0.30},
    {"key": "p3", "label": "30% at 2 of 3 inspections passed (performance)",    "pct": 0.30},
    {"key": "p4", "label": "10% at final inspection / completion",              "pct": 0.10},
]

# ---------------------------------------------------------------------------
# Lead sources (AccuLynx-style)
# ---------------------------------------------------------------------------
LEAD_SOURCES = ["Referral", "Repeat Customer", "Website", "Google", "Facebook",
                "Door Knock", "Yard Sign", "Insurance", "Storm Canvass", "Other"]

# ---------------------------------------------------------------------------
# Voice intake onboarding script (white-label / tenant-configurable)
# ---------------------------------------------------------------------------
# Drives the mic-guided "Create New Lead" wizard in lead_form.html. Each entry:
#   q     — the question Karla reads to the customer (spoken prompt)
#   kind  — "field" fills/parses a real form input; "notes" appends a labeled
#           line to the Notes textarea; "close" is the read-back confirmation.
#   field — for kind="field": the form input name to fill (name/phone/email/
#           address/work_type/source). For kind="notes": the label prefixed to
#           the spoken answer in Notes (e.g. "Timeline: next 2 months").
# Flow order: contact -> scope -> qualify -> schedule -> close. A tenant can
# reorder/relabel/extend this list without touching the template JS.
ONBOARDING_QUESTIONS = [
    # --- contact ---
    {"q": "What's the customer's full name?", "kind": "field", "field": "name"},
    {"q": "Best phone number to reach them — and is it OK to text?", "kind": "field", "field": "phone"},
    {"q": "Email address to send the estimate to?", "kind": "field", "field": "email"},
    {"q": "Property address — street, city, and zip?", "kind": "field", "field": "address"},
    # --- scope ---
    {"q": "Is it shingle, tile, metal, or flat roofing?", "kind": "field", "field": "work_type"},
    {"q": "What's prompting the call — leak, age, storm/insurance, selling, or an HOA notice?", "kind": "notes", "field": "Reason"},
    # --- qualify ---
    {"q": "How old is the roof, and are there active leaks or any interior damage?", "kind": "notes", "field": "Roof age / damage"},
    {"q": "When are they hoping to have it done by?", "kind": "notes", "field": "Timeline"},
    {"q": "Have they gotten other estimates — how many, and from who?", "kind": "notes", "field": "Other estimates"},
    {"q": "Are they financing or paying out of pocket?", "kind": "notes", "field": "Financing"},
    {"q": "Is this an insurance claim — is an adjuster involved?", "kind": "notes", "field": "Insurance claim"},
    {"q": "Any budget range in mind? (soft ask)", "kind": "notes", "field": "Budget"},
    {"q": "Who are the decision-makers — them, a spouse, or the HOA?", "kind": "notes", "field": "Decision-makers"},
    {"q": "Is there an HOA or is it a gated community?", "kind": "notes", "field": "HOA / gated"},
    # --- schedule ---
    {"q": "How did they hear about us?", "kind": "field", "field": "source"},
    {"q": "Best 2-hour inspection window — which days, and morning or afternoon?", "kind": "notes", "field": "Inspection window"},
    {"q": "Any access notes — dogs, gate code, parking, number of stories?", "kind": "notes", "field": "Access notes"},
    # --- close ---
    {"q": "Confirm: we'll email the estimate to <email> and follow up at <phone>.", "kind": "close", "field": ""},
]

# ---------------------------------------------------------------------------
# Lead onboarding pick-lists (AccuLynx "Create New Lead" parity)
# ---------------------------------------------------------------------------
# Notes / Tools sidebar — Priority Level dropdown (AccuLynx default "Normal").
PRIORITY_LEVELS = ["Low", "Normal", "High", "Urgent"]
# Phone / Email "Type" selectors on the contact block.
PHONE_TYPES = ["Mobile", "Home", "Work", "Other"]
EMAIL_TYPES = ["Personal", "Work", "Other"]

# ---------------------------------------------------------------------------
# Work types
# ---------------------------------------------------------------------------
WORK_TYPES = ["Roofing - Shingle", "Roofing - Tile", "Roofing - Metal (Galvalume)",
              "Roofing - Metal (Standard Color)", "Roofing - Metal (5V Crimp)",
              "Roofing - Metal (Nailstrip)", "Roofing - Metal (Snap Lock)",
              "Roofing - Flat (TPO)", "Roofing - Flat (3-ply SA)", "Roofing - Flat (Hot-Mop)",
              "Shingle + Flat", "Tile + Flat", "Metal + Flat", "Repair", "Other"]

# ---------------------------------------------------------------------------
# Estimate templates -> default line items per work type.
# Mirrors the AccuLynx template mapping (memory: acculynx-estimate-templates).
# Each template carries starter line items so the builder is pre-populated;
# qty/price are editable. unit: SQ (square = 100 sf), EA, LF, LS (lump sum).
# ---------------------------------------------------------------------------
# NOTE on costs: the `price` field below is the unit COST that seeds each line
# (the builder derives the sell PRICE from it via the 30% margin model). Per-SQ
# and per-LF lines carry qty 0 — _apply_measurement fills them from the roof
# measurement. EA flashing lines carry a small default count (typical residence).
#
# CALIBRATION: shingle / tile / metal SQ+LF+labor costs are set so a typical job
# lands on the REAL contract $/installed-square observed in live job data
# (shingle ~$600/sq, tile ~$1,060/sq, metal ~$1,100/sq — derived from job-name
# system/square codes cross-referenced with real contract_value). They are NOT
# extracted from supplier purchase orders (no real material-order data exists in
# the DB yet — see reference_acculynx_sync_depth). Flat systems are uncalibrated
# (only one usable flat data point) and use market-standard FL costs.
ESTIMATE_TEMPLATES = {
    "shingle": {
        "name": "Shingle Estimating Template",
        "work_types": ["Roofing - Shingle"],
        "lines": [
            {"desc": "Tear off existing roof to deck", "unit": "SQ", "qty": 0, "price": 40.0, "sec": "Tear-Off & Deck Prep"},
            {"desc": "Re-nail / re-deck fasteners to current code", "unit": "SQ", "qty": 0, "price": 22.0, "sec": "Tear-Off & Deck Prep"},
            {"desc": "Self-adhered underlayment (Polystick IR-XE / synthetic)", "unit": "SQ", "qty": 0, "price": 32.0, "sec": "Underlayment"},
            {"desc": "Architectural shingles — material (Owens Corning TruDefinition Duration)", "unit": "SQ", "qty": 0, "price": 139.0, "sec": "Field Materials"},
            {"desc": "Shingle install labor", "unit": "SQ", "qty": 0, "price": 70.0, "sec": "Labor"},
            {"desc": "Starter strip + hip & ridge cap shingles", "unit": "LF", "qty": 0, "price": 4.0, "sec": "Accessories & Flashing"},
            {"desc": "Drip edge / metal edge", "unit": "LF", "qty": 0, "price": 3.5, "sec": "Accessories & Flashing"},
            {"desc": "Pipe boots / lead flashings", "unit": "EA", "qty": 4, "price": 35.0, "sec": "Accessories & Flashing"},
            {"desc": "Permit + inspections", "unit": "LS", "qty": 1, "price": 650.0, "sec": "Permits & Disposal"},
            {"desc": "Dumpster + disposal", "unit": "LS", "qty": 1, "price": 500.0, "sec": "Permits & Disposal"},
        ],
    },
    "tile": {
        "name": "Tile Estimating Template",
        "work_types": ["Roofing - Tile"],
        # MIRRORS SeaBreeze's real AccuLynx "Tile Roof Replacement" estimate line-for-line
        # (verified vs the Lidik T31 job): same SKUs, same per-unit costs, and a per-line
        # quantity formula keyed to the roof measurement so the totals reproduce AccuLynx.
        #   q drivers: {"sq":c}=c x waste-squares · {"deck":c}=c x deck-squares ·
        #   {"lf":<ridgehip|rake|valley|ridgehipvalley|driprake>,"c":c} · {"fixed":n}
        "lines": [
            {"desc": "8d Nails", "unit": "BX", "price": 69.66, "q": {"sq": 0.0334}, "sec": "Underlayment & Hardware"},
            {"desc": "1.25\" NON-DADE Nails", "unit": "BX", "price": 26.81, "q": {"sq": 0.018}, "sec": "Underlayment & Hardware"},
            {"desc": "Tin Tags - 50lb Box", "unit": "BX", "price": 58.74, "q": {"sq": 0.018}, "sec": "Underlayment & Hardware"},
            {"desc": "Polyglass TU+ (field)", "unit": "RL", "price": 115.30, "q": {"sq": 0.5}, "sec": "Underlayment & Hardware"},
            {"desc": "Polyglass TU+ (hip/ridge/valley)", "unit": "RL", "price": 115.30, "q": {"lf": "ridgehipvalley", "c": 0.0087}, "sec": "Underlayment & Hardware"},
            {"desc": "16\" Galvanized Valley Metal", "unit": "RL", "price": 69.00, "q": {"lf": "valley", "c": 0.03}, "sec": "Underlayment & Hardware"},
            {"desc": "3\" x 3\" Galvanized Drip - White/Brown", "unit": "EA", "price": 10.70, "q": {"lf": "driprake", "c": 0.1}, "sec": "Underlayment & Hardware"},
            {"desc": "Polystick IR-Xe (valley/eave underlayment)", "unit": "RL", "price": 58.42, "q": {"sq": 0.0305}, "sec": "Underlayment & Hardware"},
            {"desc": "Spray Primer", "unit": "EA", "price": 18.53, "q": {"sq": 0.0601}, "sec": "Underlayment & Hardware"},
            {"desc": "Lead Stack 2\"", "unit": "EA", "price": 12.04, "q": {"fixed": 2}, "sec": "Accessories & Flashings"},
            {"desc": "Lead Stack 3\"", "unit": "EA", "price": 17.12, "q": {"fixed": 1}, "sec": "Accessories & Flashings"},
            {"desc": "Gooseneck - 10\" HIGH", "unit": "EA", "price": 42.49, "q": {"fixed": 2}, "sec": "Accessories & Flashings"},
            {"desc": "4\" Membrane", "unit": "EA", "price": 11.34, "q": {"sq": 0.0601}, "sec": "Accessories & Flashings"},
            {"desc": "Roof Cement 5 GAL", "unit": "EA", "price": 44.41, "q": {"sq": 0.0601}, "sec": "Accessories & Flashings"},
            {"desc": "Hip & Ridge Support", "unit": "EA", "price": 15.00, "q": {"sq": 0.497}, "sec": "Field Materials"},
            {"desc": "Labor - Tile Tear Off", "unit": "SQ", "price": 110.00, "q": {"deck": 1.0}, "sec": "Tear-Off"},
            {"desc": "Standard Tile (Westlake Royal Saxony 900)", "unit": "SQ", "price": 133.46, "q": {"sq": 1.0}, "sec": "Field Materials"},
            {"desc": "Hip & Ridge Tile", "unit": "EA", "price": 4.05, "q": {"lf": "ridgehip", "c": 1.0}, "sec": "Field Materials"},
            {"desc": "Rake Tiles", "unit": "EA", "price": 4.05, "q": {"lf": "rake", "c": 1.0}, "sec": "Field Materials"},
            {"desc": "Pallets", "unit": "EA", "price": 8.55, "q": {"sq": 0.3}, "sec": "Field Materials"},
            {"desc": "Eave Closure", "unit": "EA", "price": 26.75, "q": {"sq": 0.6316}, "sec": "Field Materials"},
            {"desc": "Oxide", "unit": "EA", "price": 31.57, "q": {"sq": 0.2}, "sec": "Field Materials"},
            {"desc": "Tile loading (Standard tile and load)", "unit": "SQ", "price": 43.00, "q": {"sq": 1.0}, "sec": "Labor"},
            {"desc": "Tile Tite 40lb - Gray", "unit": "EA", "price": 17.55, "q": {"sq": 0.582}, "sec": "Labor"},
            {"desc": "Dumps - Tile Tear Off", "unit": "EA", "price": 250.00, "q": {"sq": 0.0601}, "sec": "Permits & Disposal"},
            {"desc": "Dumps - Tile Install", "unit": "EA", "price": 250.00, "q": {"sq": 0.0151}, "sec": "Permits & Disposal"},
            {"desc": "Engineering - Tile Uplift Test", "unit": "EA", "price": 175.00, "q": {"deck": 0.01}, "sec": "Permits & Disposal"},
            {"desc": "Permit Fees - Tile", "unit": "SQ", "price": 22.00, "q": {"deck": 1.0}, "sec": "Permits & Disposal"},
            {"desc": "Permit Courier Fee", "unit": "EA", "price": 200.00, "q": {"deck": 0.01}, "sec": "Permits & Disposal"},
            {"desc": "Labor - Tile Install", "unit": "SQ", "price": 165.00, "q": {"deck": 1.0}, "sec": "Labor"},
        ],
    },
    "metal_galvalume": {
        "name": "Standing Seam Galvalume Estimating Template",
        "work_types": ["Roofing - Metal (Galvalume)"],
        # Costs from real SeaBreeze metal worksheets (Didier Maillet M12 etc.).
        # NOTE: SeaBreeze's common metal is 1.5" mechanical .032 aluminum (~$330/SQ
        # material, $180/SQ install); galvalume 24-ga is comparable. Underlayment is
        # Polyglass MTS hi-temp (~$115/roll = $64/SQ); tear-off + dry-in labor ~$110/SQ.
        "lines": [
            {"desc": "Tear off existing roof + dry-in labor", "unit": "SQ", "qty": 0, "price": 100.0, "sec": "Tear-Off & Deck Prep"},
            {"desc": "Re-nail / re-deck fasteners to current code", "unit": "SQ", "qty": 0, "price": 22.0, "sec": "Tear-Off & Deck Prep"},
            {"desc": "2-ply self-adhered underlayment (Polyglass MTS, hi-temp)", "unit": "SQ", "qty": 0, "price": 64.0, "sec": "Underlayment"},
            {"desc": "Standing seam panels 24-ga / 1.5\" .032 aluminum — material", "unit": "SQ", "qty": 0, "price": 270.0, "sec": "Field Materials"},
            {"desc": "Panel fabrication + install labor", "unit": "SQ", "qty": 0, "price": 175.0, "sec": "Labor"},
            {"desc": "Ridge / hip / eave & gable trim (.032)", "unit": "LF", "qty": 0, "price": 9.0, "sec": "Trim & Flashing"},
            {"desc": "Valley metal / closed valley pan", "unit": "LF", "qty": 0, "price": 12.0, "sec": "Trim & Flashing"},
            {"desc": "Pipe boots / flashings", "unit": "EA", "qty": 4, "price": 65.0, "sec": "Trim & Flashing"},
            {"desc": "Permit + inspections + uplift test", "unit": "LS", "qty": 1, "price": 850.0, "sec": "Permits & Disposal"},
            {"desc": "Dumpster + disposal", "unit": "LS", "qty": 1, "price": 500.0, "sec": "Permits & Disposal"},
        ],
    },
    "metal_color": {
        "name": "Standing Seam Standard Color Estimating Template",
        "work_types": ["Roofing - Metal (Standard Color)"],
        "lines": [
            {"desc": "Tear off existing roof + dry-in labor", "unit": "SQ", "qty": 0, "price": 100.0, "sec": "Tear-Off & Deck Prep"},
            {"desc": "Re-nail / re-deck fasteners to current code", "unit": "SQ", "qty": 0, "price": 22.0, "sec": "Tear-Off & Deck Prep"},
            {"desc": "2-ply self-adhered underlayment (Polyglass MTS, hi-temp)", "unit": "SQ", "qty": 0, "price": 64.0, "sec": "Underlayment"},
            {"desc": "Standing seam standard color panels 24-ga — material", "unit": "SQ", "qty": 0, "price": 310.0, "sec": "Field Materials"},
            {"desc": "Panel fabrication + install labor", "unit": "SQ", "qty": 0, "price": 175.0, "sec": "Labor"},
            {"desc": "Ridge / hip / eave & gable trim", "unit": "LF", "qty": 0, "price": 9.0, "sec": "Trim & Flashing"},
            {"desc": "Valley metal / closed valley pan", "unit": "LF", "qty": 0, "price": 12.0, "sec": "Trim & Flashing"},
            {"desc": "Pipe boots / flashings", "unit": "EA", "qty": 4, "price": 65.0, "sec": "Trim & Flashing"},
            {"desc": "Permit + inspections + uplift test", "unit": "LS", "qty": 1, "price": 850.0, "sec": "Permits & Disposal"},
            {"desc": "Dumpster + disposal", "unit": "LS", "qty": 1, "price": 500.0, "sec": "Permits & Disposal"},
        ],
    },
    "metal_5v": {
        "name": "5V Crimp Estimating Template",
        "work_types": ["Roofing - Metal (5V Crimp)"],
        # Calibrated from a real SeaBreeze 5V job: DM500 5V Crimp 24-ga panel $148.62/SQ,
        # 5V install $100/SQ, tear-off + dry-in $110/SQ, Polyglass MTS underlayment $64/SQ.
        # 5V is the budget metal system (exposed-fastener screw-down) vs standing seam.
        "lines": [
            {"desc": "Tear off existing roof + dry-in labor", "unit": "SQ", "qty": 0, "price": 110.0, "sec": "Tear-Off & Deck Prep"},
            {"desc": "2-ply self-adhered underlayment (Polyglass MTS, hi-temp)", "unit": "SQ", "qty": 0, "price": 64.0, "sec": "Underlayment"},
            {"desc": "5V Crimp panels 24-ga (DM500, Galvalume) — material", "unit": "SQ", "qty": 0, "price": 148.0, "sec": "Field Materials"},
            {"desc": "5V panel install labor", "unit": "SQ", "qty": 0, "price": 100.0, "sec": "Labor"},
            {"desc": "Ridge cap / eave & gable trim (26-ga mill)", "unit": "LF", "qty": 0, "price": 9.0, "sec": "Trim & Flashing"},
            {"desc": "Valley metal", "unit": "LF", "qty": 0, "price": 12.0, "sec": "Trim & Flashing"},
            {"desc": "Pipe boots / flashings", "unit": "EA", "qty": 4, "price": 65.0, "sec": "Trim & Flashing"},
            {"desc": "Permit + inspections + uplift test", "unit": "LS", "qty": 1, "price": 850.0, "sec": "Permits & Disposal"},
            {"desc": "Dumpster + disposal", "unit": "LS", "qty": 1, "price": 500.0, "sec": "Permits & Disposal"},
        ],
    },
    "metal_nailstrip": {
        "name": "Nailstrip Metal Estimating Template",
        "work_types": ["Roofing - Metal (Nailstrip)"],
        # Concealed-fastener nail-flange standing seam (snapped). UNCALIBRATED — no real
        # nailstrip jobs in the worksheet data; costs are industry-standard, rep-adjustable.
        "lines": [
            {"desc": "Tear off existing roof + dry-in labor", "unit": "SQ", "qty": 0, "price": 100.0, "sec": "Tear-Off & Deck Prep"},
            {"desc": "2-ply self-adhered underlayment (Polyglass MTS, hi-temp)", "unit": "SQ", "qty": 0, "price": 64.0, "sec": "Underlayment"},
            {"desc": "Nailstrip standing-seam panels 24-ga — material", "unit": "SQ", "qty": 0, "price": 220.0, "sec": "Field Materials"},
            {"desc": "Panel install labor (nail-flange / snap)", "unit": "SQ", "qty": 0, "price": 130.0, "sec": "Labor"},
            {"desc": "Ridge / hip / eave & gable trim", "unit": "LF", "qty": 0, "price": 9.0, "sec": "Trim & Flashing"},
            {"desc": "Valley metal / closed valley pan", "unit": "LF", "qty": 0, "price": 12.0, "sec": "Trim & Flashing"},
            {"desc": "Pipe boots / flashings", "unit": "EA", "qty": 4, "price": 65.0, "sec": "Trim & Flashing"},
            {"desc": "Permit + inspections + uplift test", "unit": "LS", "qty": 1, "price": 850.0, "sec": "Permits & Disposal"},
            {"desc": "Dumpster + disposal", "unit": "LS", "qty": 1, "price": 500.0, "sec": "Permits & Disposal"},
        ],
    },
    "metal_snaplock": {
        "name": "Snap Lock Metal Estimating Template",
        "work_types": ["Roofing - Metal (Snap Lock)"],
        # Concealed-fastener snap-together standing seam (no seaming tool). UNCALIBRATED —
        # no real snap-lock jobs in the worksheet data; industry-standard, rep-adjustable.
        "lines": [
            {"desc": "Tear off existing roof + dry-in labor", "unit": "SQ", "qty": 0, "price": 100.0, "sec": "Tear-Off & Deck Prep"},
            {"desc": "2-ply self-adhered underlayment (Polyglass MTS, hi-temp)", "unit": "SQ", "qty": 0, "price": 64.0, "sec": "Underlayment"},
            {"desc": "Snap-lock standing-seam panels 24-ga — material", "unit": "SQ", "qty": 0, "price": 240.0, "sec": "Field Materials"},
            {"desc": "Panel install labor (snap-together)", "unit": "SQ", "qty": 0, "price": 135.0, "sec": "Labor"},
            {"desc": "Ridge / hip / eave & gable trim", "unit": "LF", "qty": 0, "price": 9.0, "sec": "Trim & Flashing"},
            {"desc": "Valley metal / closed valley pan", "unit": "LF", "qty": 0, "price": 12.0, "sec": "Trim & Flashing"},
            {"desc": "Pipe boots / flashings", "unit": "EA", "qty": 4, "price": 65.0, "sec": "Trim & Flashing"},
            {"desc": "Permit + inspections + uplift test", "unit": "LS", "qty": 1, "price": 850.0, "sec": "Permits & Disposal"},
            {"desc": "Dumpster + disposal", "unit": "LS", "qty": 1, "price": 500.0, "sec": "Permits & Disposal"},
        ],
    },
    "flat_tpo": {
        "name": "Commercial TPO Estimate",
        "work_types": ["Roofing - Flat (TPO)"],
        # SeaBreeze's real flat work is hot-mop built-up + 3-ply SA (see flat_hotmop /
        # flat_sa, calibrated from 8 real jobs); no TPO jobs exist to calibrate against,
        # so this commercial-TPO template stays market-standard FL costs.
        "lines": [
            {"desc": "Tear off existing flat roof to deck", "unit": "SQ", "qty": 0, "price": 55.0, "sec": "Tear-Off"},
            {"desc": "ISO insulation board", "unit": "SQ", "qty": 0, "price": 60.0, "sec": "Insulation"},
            {"desc": "60-mil TPO membrane — material", "unit": "SQ", "qty": 0, "price": 120.0, "sec": "Membrane System"},
            {"desc": "TPO install labor (mechanically fastened, heat-welded)", "unit": "SQ", "qty": 0, "price": 85.0, "sec": "Membrane System"},
            {"desc": "Termination bar / edge metal", "unit": "LF", "qty": 0, "price": 8.0, "sec": "Trim & Flashing"},
            {"desc": "Pipe boots / curb flashing", "unit": "EA", "qty": 2, "price": 85.0, "sec": "Trim & Flashing"},
            {"desc": "Permit + inspections", "unit": "LS", "qty": 1, "price": 850.0, "sec": "Permits & Disposal"},
            {"desc": "Dumpster + disposal", "unit": "LS", "qty": 1, "price": 650.0, "sec": "Permits & Disposal"},
        ],
    },
    "flat_sa": {
        "name": "Flat - 3-ply SA Estimate",
        "work_types": ["Roofing - Flat (3-ply SA)"],
        # Calibrated from real SeaBreeze flat worksheets (8 flat jobs). 3-ply Polyglass
        # SA system, itemized per ply; flat tear-off + SA install labor ~$150/SQ.
        "lines": [
            {"desc": "Flat tear-off + 3-ply SA membrane install labor", "unit": "SQ", "qty": 0, "price": 150.0, "sec": "Tear-Off & Labor"},
            {"desc": "SA base ply — Polyglass Elastobase SA", "unit": "SQ", "qty": 0, "price": 123.0, "sec": "Membrane System"},
            {"desc": "SA mid ply — Polyglass Elastoflex SA V Base", "unit": "SQ", "qty": 0, "price": 119.0, "sec": "Membrane System"},
            {"desc": "SA cap ply — Polyglass Polyflex SAP", "unit": "SQ", "qty": 0, "price": 111.0, "sec": "Membrane System"},
            {"desc": "Edge metal / gravel stop (break metal)", "unit": "LF", "qty": 0, "price": 7.0, "sec": "Trim & Flashing"},
            {"desc": "Lead pipe stacks / flashings", "unit": "EA", "qty": 3, "price": 16.0, "sec": "Trim & Flashing"},
            {"desc": "Permit + inspections + engineered calcs", "unit": "LS", "qty": 1, "price": 850.0, "sec": "Permits & Disposal"},
            {"desc": "Dumpster + disposal", "unit": "LS", "qty": 1, "price": 500.0, "sec": "Permits & Disposal"},
        ],
    },
    "flat_hotmop": {
        "name": "Flat - Hot-Mop Estimate",
        "work_types": ["Roofing - Flat (Hot-Mop)"],
        # Calibrated from real SeaBreeze hot-mop worksheets. 3-ply built-up (Certainteed
        # Glass Base + Ply IV + Glascap cap), hot-mopped; tear-off + install labor ~$250/SQ.
        "lines": [
            {"desc": "Flat tear-off + 3-ply hot-mop install labor", "unit": "SQ", "qty": 0, "price": 250.0, "sec": "Tear-Off & Labor"},
            {"desc": "Glass base sheet — Certainteed Glass Base", "unit": "SQ", "qty": 0, "price": 64.0, "sec": "Membrane System"},
            {"desc": "Fiberglass ply — Certainteed Ply IV", "unit": "SQ", "qty": 0, "price": 40.0, "sec": "Membrane System"},
            {"desc": "Mineral-surfaced cap sheet — Certainteed Glascap", "unit": "SQ", "qty": 0, "price": 86.0, "sec": "Membrane System"},
            {"desc": "Hot asphalt + granule surfacing", "unit": "SQ", "qty": 0, "price": 25.0, "sec": "Membrane System"},
            {"desc": "Edge metal / gravel stop", "unit": "LF", "qty": 0, "price": 7.0, "sec": "Trim & Flashing"},
            {"desc": "Lead pipe stacks / flashings", "unit": "EA", "qty": 3, "price": 16.0, "sec": "Trim & Flashing"},
            {"desc": "Permit + inspections + engineered calcs", "unit": "LS", "qty": 1, "price": 850.0, "sec": "Permits & Disposal"},
            {"desc": "Dumpster + disposal", "unit": "LS", "qty": 1, "price": 500.0, "sec": "Permits & Disposal"},
        ],
    },
    "shingle_flat": {
        "name": "Shingle & Flat Split Estimating Template",
        "work_types": ["Shingle + Flat"],
        # Split: measurement-apply fills SQ lines with the SAME squares for both the
        # sloped and flat sections — the rep splits squares per section by hand.
        "lines": [
            {"desc": "Tear off existing roof to deck (shingle area)", "unit": "SQ", "qty": 0, "price": 40.0, "sec": "Shingle — Tear-Off & Prep"},
            {"desc": "Self-adhered underlayment (sloped)", "unit": "SQ", "qty": 0, "price": 32.0, "sec": "Shingle — Materials"},
            {"desc": "Architectural shingles — material (sloped area)", "unit": "SQ", "qty": 0, "price": 139.0, "sec": "Shingle — Materials"},
            {"desc": "Shingle install labor (sloped)", "unit": "SQ", "qty": 0, "price": 70.0, "sec": "Shingle — Materials"},
            {"desc": "Starter strip + hip & ridge cap shingles", "unit": "LF", "qty": 0, "price": 4.0, "sec": "Shingle — Materials"},
            {"desc": "Drip edge / metal edge (sloped)", "unit": "LF", "qty": 0, "price": 3.5, "sec": "Shingle — Materials"},
            {"desc": "SBS SA base ply — Polyglass Elastobase SA (flat area)", "unit": "SQ", "qty": 0, "price": 123.0, "sec": "Flat — Membrane System"},
            {"desc": "SBS SA interply — Polyglass Elastoflex SA V Base (flat area)", "unit": "SQ", "qty": 0, "price": 119.0, "sec": "Flat — Membrane System"},
            {"desc": "SBS SA granulated cap — Polyglass Polyflex SAP (flat area)", "unit": "SQ", "qty": 0, "price": 111.0, "sec": "Flat — Membrane System"},
            {"desc": "Flat tear-off + 3-ply SA install labor (flat area)", "unit": "SQ", "qty": 0, "price": 150.0, "sec": "Flat — Membrane System"},
            {"desc": "Edge metal / gravel stop (flat)", "unit": "LF", "qty": 0, "price": 7.0, "sec": "Trim & Flashing"},
            {"desc": "Pipe boots / flashing", "unit": "EA", "qty": 4, "price": 35.0, "sec": "Trim & Flashing"},
            {"desc": "Permit + inspections", "unit": "LS", "qty": 1, "price": 850.0, "sec": "Permits & Disposal"},
            {"desc": "Dumpster + disposal", "unit": "LS", "qty": 1, "price": 500.0, "sec": "Permits & Disposal"},
        ],
    },
    "tile_flat": {
        "name": "Tile & Flat Split Estimating Template",
        "work_types": ["Tile + Flat"],
        "lines": [
            {"desc": "Tear off existing tile to deck (sloped area, labor)", "unit": "SQ", "qty": 0, "price": 110.0, "sec": "Tile — Tear-Off"},
            {"desc": "Re-nail / re-deck fasteners to current code", "unit": "SQ", "qty": 0, "price": 22.0, "sec": "Tile — Tear-Off"},
            {"desc": "Self-adhered tile underlayment (Polystick TU Plus, sloped)", "unit": "SQ", "qty": 0, "price": 80.0, "sec": "Tile — Materials"},
            {"desc": "Concrete tile — material (Westlake Royal Saxony 900)", "unit": "SQ", "qty": 0, "price": 131.0, "sec": "Tile — Materials"},
            {"desc": "Mortar set (Tile Tite + oxide) — material", "unit": "SQ", "qty": 0, "price": 18.0, "sec": "Tile — Materials"},
            {"desc": "Tile install labor (sloped)", "unit": "SQ", "qty": 0, "price": 165.0, "sec": "Tile — Materials"},
            {"desc": "Tile loading / handling", "unit": "SQ", "qty": 0, "price": 43.0, "sec": "Tile — Materials"},
            {"desc": "Hip & ridge tile, mortar / weather-block", "unit": "LF", "qty": 0, "price": 8.0, "sec": "Tile — Materials"},
            {"desc": "SBS SA base ply — Polyglass Elastobase SA (flat area)", "unit": "SQ", "qty": 0, "price": 123.0, "sec": "Flat — Membrane System"},
            {"desc": "SBS SA interply — Polyglass Elastoflex SA V Base (flat area)", "unit": "SQ", "qty": 0, "price": 119.0, "sec": "Flat — Membrane System"},
            {"desc": "SBS SA granulated cap — Polyglass Polyflex SAP (flat area)", "unit": "SQ", "qty": 0, "price": 111.0, "sec": "Flat — Membrane System"},
            {"desc": "Flat tear-off + 3-ply SA install labor (flat area)", "unit": "SQ", "qty": 0, "price": 150.0, "sec": "Flat — Membrane System"},
            {"desc": "Edge metal / gravel stop (flat)", "unit": "LF", "qty": 0, "price": 7.0, "sec": "Trim & Flashing"},
            {"desc": "Lead pipe flashings", "unit": "EA", "qty": 4, "price": 65.0, "sec": "Trim & Flashing"},
            {"desc": "Permit + inspections + tile uplift test", "unit": "LS", "qty": 1, "price": 950.0, "sec": "Permits & Disposal"},
            {"desc": "Dumpster + disposal (tear-off + install)", "unit": "LS", "qty": 1, "price": 500.0, "sec": "Permits & Disposal"},
        ],
    },
    "metal_flat": {
        "name": "Metal & Flat Split Estimating Template",
        "work_types": ["Metal + Flat"],
        "lines": [
            {"desc": "Tear off existing roof + dry-in labor (sloped area)", "unit": "SQ", "qty": 0, "price": 100.0, "sec": "Metal — Tear-Off & Prep"},
            {"desc": "Re-nail / re-deck fasteners to current code", "unit": "SQ", "qty": 0, "price": 22.0, "sec": "Metal — Tear-Off & Prep"},
            {"desc": "Self-adhered underlayment (Polyglass MTS, sloped)", "unit": "SQ", "qty": 0, "price": 64.0, "sec": "Metal — Materials"},
            {"desc": "Standing-seam panels 24-ga / 1.5\" .032 aluminum — material", "unit": "SQ", "qty": 0, "price": 270.0, "sec": "Metal — Materials"},
            {"desc": "Panel fabrication + install labor (sloped)", "unit": "SQ", "qty": 0, "price": 175.0, "sec": "Metal — Materials"},
            {"desc": "Trim metal / drip edge / valleys (sloped)", "unit": "LF", "qty": 0, "price": 9.0, "sec": "Metal — Materials"},
            {"desc": "SBS SA base ply — Polyglass Elastobase SA (flat area)", "unit": "SQ", "qty": 0, "price": 123.0, "sec": "Flat — Membrane System"},
            {"desc": "SBS SA interply — Polyglass Elastoflex SA V Base (flat area)", "unit": "SQ", "qty": 0, "price": 119.0, "sec": "Flat — Membrane System"},
            {"desc": "SBS SA granulated cap — Polyglass Polyflex SAP (flat area)", "unit": "SQ", "qty": 0, "price": 111.0, "sec": "Flat — Membrane System"},
            {"desc": "Flat tear-off + 3-ply SA install labor (flat area)", "unit": "SQ", "qty": 0, "price": 150.0, "sec": "Flat — Membrane System"},
            {"desc": "Edge metal / gravel stop (flat)", "unit": "LF", "qty": 0, "price": 7.0, "sec": "Trim & Flashing"},
            {"desc": "Pipe boots / flashing", "unit": "EA", "qty": 4, "price": 65.0, "sec": "Trim & Flashing"},
            {"desc": "Permit + inspections + uplift test", "unit": "LS", "qty": 1, "price": 950.0, "sec": "Permits & Disposal"},
            {"desc": "Dumpster + disposal", "unit": "LS", "qty": 1, "price": 500.0, "sec": "Permits & Disposal"},
        ],
    },
    "repair": {
        "name": "Repair / T&M Estimate",
        "work_types": ["Repair"],
        "lines": [
            {"desc": "Roof repair labor", "unit": "LS", "qty": 1, "price": 450.0},
            {"desc": "Materials", "unit": "LS", "qty": 1, "price": 150.0},
        ],
    },
    "blank": {
        "name": "Blank Estimate",
        "work_types": ["Other"],
        "lines": [],
    },
}

# ---------------------------------------------------------------------------
# Scope-of-work narratives per template (AccuLynx-style lettered clauses).
# The shingle scope is the real SeaBreeze proposal language captured live from
# my.acculynx.com. These print on the proposal under each estimate section.
# ---------------------------------------------------------------------------
SCOPE_TEMPLATES = {
    "shingle": (
        "Shingle Roof:\n"
        "a. Remove existing materials on sloped roof(s) and haul away to licensed land fill.\n"
        "b. Remove and replace decayed decking and fascia in accordance with unit prices listed on "
        "Wood Replacement sheet. Painting of fascia to be done by others. This proposal includes 25 LF "
        "of fascia and (3) three sheets of plywood.\n"
        "c. Fasten decking in accordance with Florida Building Code.\n"
        "d. Furnish and install Self Adhered membrane (Polyglass IR-XE or equivalent) to roof deck in "
        "accordance with current Florida Building Code. This qualifies as your Secondary Water Barrier.\n"
        "e. Furnish and install new 26-gauge galvanized valley metal. Metal will be nailed at 4\" on center.\n"
        "f. Furnish and install new 26-gauge, 3\"x 3\" galvanized drip edge. Prefinished white.\n"
        "g. Furnish and install new lead plumbing stacks, set in roof cement and fastened to plywood "
        "decking, folded over outside of pipes to prevent water intrusion.\n"
        "h. Furnish and install architectural shingles (Owens Corning / CertainTeed) per manufacturer's "
        "recommendations. Shingles to be nailed with 6 nails each.\n"
        "i. Standard Color to be selected by owner."
    ),
    "tile": (
        "Tile Roof:\n"
        "a. Remove existing tile and underlayment on sloped roof(s) and haul away to licensed land fill.\n"
        "b. Remove and replace decayed decking and fascia per unit prices on the Wood Replacement sheet.\n"
        "c. Re-nail decking in accordance with current Florida Building Code.\n"
        "d. Furnish and install 2-ply self-adhered underlayment (Polyglass Polystick TU Plus or equivalent) "
        "as Secondary Water Barrier per FBC.\n"
        "e. Furnish and install new 26-gauge galvanized valley and flashing metal.\n"
        "f. Furnish and install new concrete tile (Westlake Royal Saxony 900 or equivalent) set in approved "
        "adhesive foam (Polyset AH-160) per NOA and manufacturer specifications.\n"
        "g. Hip and ridge tile to be mortar-set and pointed.\n"
        "h. Color and profile to be selected by owner."
    ),
    "metal_galvalume": (
        "Standing Seam Metal Roof:\n"
        "a. Remove existing roof materials on sloped roof(s) and haul away to licensed land fill.\n"
        "b. Remove and replace decayed decking and fascia per unit prices on the Wood Replacement sheet.\n"
        "c. Re-nail decking in accordance with current Florida Building Code.\n"
        "d. Furnish and install 2-ply self-adhered underlayment (Polystick MTS or equivalent) as Secondary "
        "Water Barrier per FBC.\n"
        "e. Furnish and install new 24-gauge standing seam Galvalume panels per NOA, mechanically seamed.\n"
        "f. Furnish and install matching ridge, hip, eave and gable trim.\n"
        "g. All penetrations flashed and sealed per manufacturer specifications."
    ),
    "metal_color": (
        "Standing Seam Metal Roof (Standard Color):\n"
        "a. Remove existing roof materials on sloped roof(s) and haul away to licensed land fill.\n"
        "b. Remove and replace decayed decking and fascia per unit prices on the Wood Replacement sheet.\n"
        "c. Re-nail decking in accordance with current Florida Building Code.\n"
        "d. Furnish and install 2-ply self-adhered underlayment (Polystick MTS or equivalent) as Secondary "
        "Water Barrier per FBC.\n"
        "e. Furnish and install new 24-gauge standing seam panels in owner-selected standard color per NOA.\n"
        "f. Furnish and install matching trim and flashings.\n"
        "g. Color to be selected by owner."
    ),
    "flat_tpo": (
        "Flat Roof (TPO):\n"
        "a. Remove existing flat roof materials to deck and haul away to licensed land fill.\n"
        "b. Replace decayed decking per unit prices on the Wood Replacement sheet.\n"
        "c. Mechanically fasten ISO insulation board to deck.\n"
        "d. Furnish and install 60-mil TPO membrane, mechanically fastened and heat-welded at all seams "
        "per NOA and manufacturer specifications.\n"
        "e. Furnish and install new termination bar, edge metal, and flashings at all penetrations and curbs."
    ),
    "flat_sa": (
        "Flat Roof (3-Ply Self-Adhered):\n"
        "a. Remove existing flat roof materials to deck and haul away to licensed land fill.\n"
        "b. Replace decayed decking per unit prices on the Wood Replacement sheet.\n"
        "c. Mechanically fasten base sheet to deck per FBC.\n"
        "d. Furnish and install 3-ply self-adhered modified bitumen system (Polyglass SA) per NOA.\n"
        "e. Furnish and install new edge metal / gravel stop and flash all penetrations."
    ),
    "flat_hotmop": (
        "Flat Roof (Hot-Mop Built-Up):\n"
        "a. Remove existing flat roof materials to deck and haul away to licensed land fill.\n"
        "b. Replace decayed decking per unit prices on the Wood Replacement sheet.\n"
        "c. Furnish and install 3-ply hot-mop built-up roofing system per manufacturer specifications and FBC.\n"
        "d. Furnish and install gravel surfacing for UV protection and ballast.\n"
        "e. Furnish and install new edge metal / gravel stop and flash all penetrations."
    ),
    "shingle_flat": (
        "Sloped Section &mdash; Shingle:\n"
        "a. Remove existing shingles on sloped roof(s) to deck and haul away to licensed land fill.\n"
        "b. Re-nail deck to current code; replace decayed decking per Wood Replacement unit prices.\n"
        "c. Furnish and install synthetic / self-adhered underlayment per NOA.\n"
        "d. Furnish and install architectural shingles, drip edge, ridge cap, and pipe flashings per manufacturer specifications.\n"
        "\n"
        "Flat Section &mdash; 3-Ply Self-Adhered:\n"
        "e. Remove existing flat roof materials to deck and haul away.\n"
        "f. Mechanically fasten base sheet to deck per FBC.\n"
        "g. Furnish and install 3-ply self-adhered modified bitumen system (Polyglass SA) per NOA.\n"
        "h. Furnish and install edge metal / gravel stop and flash all penetrations."
    ),
    "tile_flat": (
        "Sloped Section &mdash; Tile:\n"
        "a. Remove existing tile on sloped roof(s) to deck and haul away to licensed land fill.\n"
        "b. Re-nail deck to current code; replace decayed decking per Wood Replacement unit prices.\n"
        "c. Furnish and install tile underlayment (self-adhered) per NOA.\n"
        "d. Furnish and install concrete tile with adhesive foam-set system per manufacturer specifications, including lead pipe flashings and valley metal.\n"
        "\n"
        "Flat Section &mdash; 3-Ply Self-Adhered:\n"
        "e. Remove existing flat roof materials to deck and haul away.\n"
        "f. Mechanically fasten base sheet to deck per FBC.\n"
        "g. Furnish and install 3-ply self-adhered modified bitumen system (Polyglass SA) per NOA.\n"
        "h. Furnish and install edge metal / gravel stop and flash all penetrations."
    ),
    "metal_flat": (
        "Sloped Section &mdash; Metal:\n"
        "a. Remove existing roof materials on sloped roof(s) to deck and haul away to licensed land fill.\n"
        "b. Re-nail deck to current code; replace decayed decking per Wood Replacement unit prices.\n"
        "c. Furnish and install self-adhered underlayment per NOA.\n"
        "d. Furnish and install standing-seam metal panels, trim metal, drip edge, valley flashings, and pipe boots per manufacturer specifications.\n"
        "\n"
        "Flat Section &mdash; 3-Ply Self-Adhered:\n"
        "e. Remove existing flat roof materials to deck and haul away.\n"
        "f. Mechanically fasten base sheet to deck per FBC.\n"
        "g. Furnish and install 3-ply self-adhered modified bitumen system (Polyglass SA) per NOA.\n"
        "h. Furnish and install edge metal / gravel stop and flash all penetrations."
    ),
    "repair": "Roof Repair: Scope per inspection. Furnish labor and materials to repair the affected area "
              "and restore weather-tightness. Work performed on a time-and-materials basis unless noted.",
}


def scope_for_template(tpl_key):
    return SCOPE_TEMPLATES.get(tpl_key, "")


# Map a free-text work type to the best-fit template key.
WORK_TYPE_TEMPLATE = {}
for _key, _tpl in ESTIMATE_TEMPLATES.items():
    for _wt in _tpl["work_types"]:
        WORK_TYPE_TEMPLATE[_wt] = _key


def template_for_work_type(work_type):
    """Best-fit estimate template key for a work type string."""
    wt = (work_type or "").strip()
    if wt in WORK_TYPE_TEMPLATE:
        return WORK_TYPE_TEMPLATE[wt]
    low = wt.lower()
    if "tile" in low and "flat" in low:
        return "tile"
    if "shingle" in low and "flat" in low:
        return "shingle_flat"
    if "shingle" in low:
        return "shingle"
    if "tile" in low:
        return "tile"
    if "metal" in low or "5v" in low or "nailstrip" in low or "nail strip" in low or "snap" in low:
        if "5v" in low or "5-v" in low or "crimp" in low:
            return "metal_5v"
        if "nailstrip" in low or "nail strip" in low or "nail-strip" in low:
            return "metal_nailstrip"
        if "snap" in low:
            return "metal_snaplock"
        return "metal_color" if "color" in low else "metal_galvalume"
    if "tpo" in low:
        return "flat_tpo"
    if "flat" in low:
        return "flat_sa"
    if "repair" in low:
        return "repair"
    return "blank"


# ---------------------------------------------------------------------------
# Material resources — product data sheets, brochures, and DIGITAL color
# selectors shown on the estimate so the rep can share them with the customer.
# Keyed by a material family; matched to the estimate's work type.
# ---------------------------------------------------------------------------
MATERIAL_RESOURCES = {
    "shingle": [
        {"name": "Owens Corning TruDefinition Duration — product page", "url": "https://www.owenscorning.com/en-us/roofing/shingles/duration"},
        {"name": "Owens Corning Design EyeQ — digital color visualizer", "url": "https://www.owenscorning.com/en-us/roofing/design-eyeq", "digital": True},
        {"name": "Owens Corning Duration color brochure (PDF)", "url": "https://dcpd.owenscorning.com/is/content/dpor/oc-duration-series-brochure"},
        {"name": "GAF Timberline HDZ — product page", "url": "https://www.gaf.com/en-us/roofing-materials/residential-roofing-products/shingles/timberline/timberline-hdz"},
        {"name": "GAF Virtual Home Remodeler — digital color tool", "url": "https://www.gaf.com/en-us/virtual-home-remodeler", "digital": True},
    ],
    "tile": [
        {"name": "Westlake Royal Roofing — concrete tile", "url": "https://westlakeroyalroofing.com/products/concrete-tile/"},
        {"name": "Westlake Royal — color & profile selector", "url": "https://westlakeroyalroofing.com/color-tools/", "digital": True},
        {"name": "Eagle Roofing — tile color visualizer", "url": "https://eagleroofing.com/products/", "digital": True},
    ],
    "metal": [
        {"name": "Standing seam metal — color chart & profiles", "url": "https://www.drexmet.com/colors/"},
        {"name": "Galvalume / standard color samples", "url": "https://www.englertinc.com/colors/", "digital": True},
    ],
    "flat": [
        {"name": "Polyglass SA modified bitumen — product data", "url": "https://polyglass.us/products/"},
        {"name": "GAF / commercial TPO — product data", "url": "https://www.gaf.com/en-us/commercial-roofing/products"},
    ],
    "underlayment": [
        {"name": "Polyglass Polystick underlayments — data sheets", "url": "https://polyglass.us/products/underlayments/"},
    ],
}


def resources_for(work_type):
    """Pick the relevant material-resource links for an estimate's work type."""
    low = (work_type or "").lower()
    out = []
    if "shingle" in low:
        out += MATERIAL_RESOURCES["shingle"]
    if "tile" in low:
        out += MATERIAL_RESOURCES["tile"]
    if "metal" in low:
        out += MATERIAL_RESOURCES["metal"]
    if "flat" in low or "tpo" in low:
        out += MATERIAL_RESOURCES["flat"]
    if not out:
        out = MATERIAL_RESOURCES["shingle"]
    out += MATERIAL_RESOURCES["underlayment"]
    return out


# ---------------------------------------------------------------------------
# System UPGRADES / add-ons — dropped into a new estimate as an "Upgrades &
# Options" section (qty 0 = not included until the rep turns it on). Matched to
# the lead/job work type so the right premium options are always presented.
# ---------------------------------------------------------------------------
# These mirror SeaBreeze's real AccuLynx upgrade templates (Shingle/Tile/Metal
# "Upgrades"), which are customer accept/decline scope options priced per job.
# Costs here are best-effort defaults (qty 0 — the rep turns one on and adjusts the
# price). The shared options (skylights, gutters, hurricane clips, wall flashing &
# stucco) live under "common" since they appear on every system in AccuLynx.
UPGRADES = {
    "shingle": [
        {"desc": "UPGRADE: 26GA 5V Crimp metal panels (Galvalume) vs shingle", "unit": "SQ", "qty": 0, "cost": 375.0},
    ],
    "tile": [
        {"desc": "UPGRADE: 2-ply mechanically-attached base underlayment (Polyanchor HV + Polyglass TU Plus)", "unit": "SQ", "qty": 0, "cost": 50.0},
        {"desc": "UPGRADE: 2-ply self-adhered underlayment (Polyglass MTS + TU Plus)", "unit": "SQ", "qty": 0, "cost": 80.0},
        {"desc": "UPGRADE: Premium / Designer tile selection", "unit": "SQ", "qty": 0, "cost": 90.0},
        {"desc": "UPGRADE: Color-Coat / Slurry-Coat tile selection", "unit": "SQ", "qty": 0, "cost": 60.0},
        {"desc": "ADD-ON: Saltwater package — copper drip edge & valley flashing", "unit": "LF", "qty": 0, "cost": 20.0},
    ],
    "metal": [
        {"desc": "UPGRADE: 2-ply #30 felt underlayment system", "unit": "SQ", "qty": 0, "cost": 45.0},
        {"desc": "UPGRADE: 2-ply self-adhered underlayment (Polyglass MTS)", "unit": "SQ", "qty": 0, "cost": 64.0},
        {"desc": "UPGRADE: Standard color panels & trim (vs Galvalume)", "unit": "SQ", "qty": 0, "cost": 40.0},
        {"desc": "ADD-ON: Saltwater package — .032 aluminum panels, drip & valley", "unit": "SQ", "qty": 0, "cost": 30.0},
    ],
    "flat": [
        {"desc": "UPGRADE: 80-mil TPO membrane (vs 60-mil)", "unit": "SQ", "qty": 0, "cost": 45.0},
        {"desc": "UPGRADE: Tapered ISO / crickets for positive drainage", "unit": "SQ", "qty": 0, "cost": 120.0},
        {"desc": "UPGRADE: Reflective cool-roof coating", "unit": "SQ", "qty": 0, "cost": 40.0},
        {"desc": "ADD-ON: Walk pads at equipment", "unit": "EA", "qty": 0, "cost": 45.0},
        {"desc": "ADD-ON: Additional roof drain / scupper", "unit": "EA", "qty": 0, "cost": 350.0},
    ],
    "common": [
        {"desc": "ADD-ON: Hurricane impact-rated skylights (Bronze / Smoke lens)", "unit": "EA", "qty": 0, "cost": 650.0},
        {"desc": "ADD-ON: Gutters & downspouts — 6\" aluminum K-style (all eaves)", "unit": "LF", "qty": 0, "cost": 9.0},
        {"desc": "ADD-ON: Gutters & downspouts — 6\" aluminum K-style (zero side, per FBC)", "unit": "LF", "qty": 0, "cost": 9.0},
        {"desc": "UPGRADE: Hurricane clips — Simpson gussets + re-deck above tie beam", "unit": "LF", "qty": 0, "cost": 12.0},
        {"desc": "UPGRADE: Wall flashing & stucco (replace; per wood sheet if corroded)", "unit": "LF", "qty": 0, "cost": 25.0},
    ],
}


def upgrades_for(work_type):
    """Upgrade/add-on line items matched to a work type (+ common add-ons)."""
    low = (work_type or "").lower()
    out = []
    if "shingle" in low:
        out += UPGRADES["shingle"]
    if "tile" in low:
        out += UPGRADES["tile"]
    if "metal" in low:
        out += UPGRADES["metal"]
    if "flat" in low or "tpo" in low:
        out += UPGRADES["flat"]
    if not out:
        out = UPGRADES["shingle"]
    return out + UPGRADES["common"]


# ---------------------------------------------------------------------------
# UPGRADE OPTION GROUPS — mirrors AccuLynx's design: each upgrade is its own named
# option with a customer-facing scope + a "Declined / Accepted" line + line items
# (vs. a flat list). build_estimate renders each group as its own collapsible
# section. Quantities use the same q-formula engine as the base template.
# ---------------------------------------------------------------------------
_ACCEPT_LINE = "\n\nDeclined ____________     Accepted ____________"

UPGRADE_GROUPS = {
    "tile": [
        {"name": "Upgrade to Premium Tile Selection",
         "scope": "Furnish and install \"Premium\" or \"Designer\" tile selection.",
         "lines": [{"desc": "Upgrade to Premium Tile Selection", "unit": "SQ", "cost": 5.81, "q": {"sq": 1.0}}]},
        {"name": "Upgrade to Color Coat Tile (Slurry Coated)",
         "scope": "Furnish and install \"Color Coat\" or \"Slurry Coat\" tile selection.",
         "lines": [{"desc": "Color Coat (Slurry Coated Tile)", "unit": "SQ", "cost": 66.88, "q": {"sq": 1.0}}]},
        {"name": "Saltwater Considerations",
         "scope": "Due to the proximity of salt or brackish water, please consider using copper drip "
                  "edge flashing and copper valley flashing.",
         "lines": [{"desc": "Copper Drip Edge 3\" x 3\"", "unit": "EA", "cost": 53.00, "q": {"lf": "driprake", "c": 0.1}},
                   {"desc": "Copper Valley Metal", "unit": "EA", "cost": 530.07, "q": {"lf": "valley", "c": 0.17}}]},
        {"name": "Wall Flashing & Stucco",
         "scope": "a. Remove and dispose of existing wall flashing.\nb. Furnish and install new wall flashing.\n"
                  "c. Furnish and install new stucco.\nIf wall flashing is rusted and/or corroded, it will be "
                  "removed and replaced and billed in accordance with the wood pricing sheet.",
         "lines": [{"desc": "4x5 Wall Flashing", "unit": "EA", "cost": 4.00, "qty": 0},
                   {"desc": "Stucco Disposal", "unit": "EA", "cost": 1.00, "qty": 0},
                   {"desc": "Labor - Stucco Cut", "unit": "EA", "cost": 5.00, "qty": 0},
                   {"desc": "Labor - New Stucco", "unit": "EA", "cost": 25.00, "qty": 0}]},
        {"name": "Gutters and Downspouts (Zero Side)",
         "scope": "Furnish and install new 6\" aluminum K-style gutters and downspouts on the \"zero side\" of "
                  "the property. This is required by Florida Building Code.",
         "lines": [{"desc": "6\" Aluminum K-Style Gutters", "unit": "LF", "cost": 5.25, "qty": 0}]},
    ],
}


def upgrade_groups(work_type):
    """Return AccuLynx-style upgrade OPTION GROUPS for a work type. Systems without an
    explicit grouped menu fall back to a single group wrapping their flat upgrade list."""
    key = template_for_work_type(work_type)
    if key in UPGRADE_GROUPS:
        return UPGRADE_GROUPS[key]
    flat = upgrades_for(work_type)
    if not flat:
        return []
    return [{"name": "Upgrades & Options",
             "scope": "Optional upgrades for this roof — included only when a quantity is entered." + _ACCEPT_LINE,
             "lines": [{"desc": u["desc"], "unit": u.get("unit", "EA"),
                        "cost": u.get("cost", 0), "qty": u.get("qty", 0)} for u in flat]}]
