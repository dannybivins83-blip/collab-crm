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
# Work types
# ---------------------------------------------------------------------------
WORK_TYPES = ["Roofing - Shingle", "Roofing - Tile", "Roofing - Metal (Galvalume)",
              "Roofing - Metal (Standard Color)", "Roofing - Flat (TPO)",
              "Roofing - Flat (3-ply SA)", "Roofing - Flat (Hot-Mop)",
              "Shingle + Flat", "Tile + Flat", "Metal + Flat", "Repair", "Other"]

# ---------------------------------------------------------------------------
# Estimate templates -> default line items per work type.
# Mirrors the AccuLynx template mapping (memory: acculynx-estimate-templates).
# Each template carries starter line items so the builder is pre-populated;
# qty/price are editable. unit: SQ (square = 100 sf), EA, LF, LS (lump sum).
# ---------------------------------------------------------------------------
ESTIMATE_TEMPLATES = {
    "shingle": {
        "name": "Shingle Estimating Template",
        "work_types": ["Roofing - Shingle"],
        "lines": [
            {"desc": "Tear off existing roof to deck", "unit": "SQ", "qty": 0, "price": 65.0},
            {"desc": "Re-nail deck to current code", "unit": "SQ", "qty": 0, "price": 35.0},
            {"desc": "Synthetic / self-adhered underlayment (Polystick IR-XE)", "unit": "SQ", "qty": 0, "price": 55.0},
            {"desc": "Architectural shingles (Owens Corning TruDefinition Duration)", "unit": "SQ", "qty": 0, "price": 285.0},
            {"desc": "Ridge cap shingles", "unit": "LF", "qty": 0, "price": 6.5},
            {"desc": "Drip edge / metal edge", "unit": "LF", "qty": 0, "price": 4.0},
            {"desc": "Pipe boots / flashing", "unit": "EA", "qty": 0, "price": 45.0},
            {"desc": "Permit + inspections", "unit": "LS", "qty": 1, "price": 650.0},
            {"desc": "Dumpster + disposal", "unit": "LS", "qty": 1, "price": 550.0},
        ],
    },
    "tile": {
        "name": "Tile Estimating Template",
        "work_types": ["Roofing - Tile"],
        "lines": [
            {"desc": "Tear off existing tile to deck", "unit": "SQ", "qty": 0, "price": 95.0},
            {"desc": "Re-nail deck to current code", "unit": "SQ", "qty": 0, "price": 35.0},
            {"desc": "2-ply self-adhered underlayment (Polystick TU Plus)", "unit": "SQ", "qty": 0, "price": 110.0},
            {"desc": "Concrete tile (Westlake Royal Saxony 900)", "unit": "SQ", "qty": 0, "price": 425.0},
            {"desc": "Tile adhesive foam set (Polyset AH-160)", "unit": "SQ", "qty": 0, "price": 95.0},
            {"desc": "Hip & ridge / mortar", "unit": "LF", "qty": 0, "price": 12.0},
            {"desc": "Lead pipe flashing / valley metal", "unit": "EA", "qty": 0, "price": 65.0},
            {"desc": "Permit + inspections", "unit": "LS", "qty": 1, "price": 850.0},
            {"desc": "Dumpster + disposal", "unit": "LS", "qty": 1, "price": 750.0},
        ],
    },
    "metal_galvalume": {
        "name": "Standing Seam Galvalume Estimating Template",
        "work_types": ["Roofing - Metal (Galvalume)"],
        "lines": [
            {"desc": "Tear off existing roof to deck", "unit": "SQ", "qty": 0, "price": 75.0},
            {"desc": "Re-nail deck to current code", "unit": "SQ", "qty": 0, "price": 35.0},
            {"desc": "2-ply self-adhered underlayment (Polystick MTS)", "unit": "SQ", "qty": 0, "price": 110.0},
            {"desc": "Standing seam Galvalume panels 24ga", "unit": "SQ", "qty": 0, "price": 575.0},
            {"desc": "Ridge / hip / eave trim", "unit": "LF", "qty": 0, "price": 9.0},
            {"desc": "Pipe boots / flashing", "unit": "EA", "qty": 0, "price": 65.0},
            {"desc": "Permit + inspections", "unit": "LS", "qty": 1, "price": 850.0},
            {"desc": "Dumpster + disposal", "unit": "LS", "qty": 1, "price": 650.0},
        ],
    },
    "metal_color": {
        "name": "Standing Seam Standard Color Estimating Template",
        "work_types": ["Roofing - Metal (Standard Color)"],
        "lines": [
            {"desc": "Tear off existing roof to deck", "unit": "SQ", "qty": 0, "price": 75.0},
            {"desc": "Re-nail deck to current code", "unit": "SQ", "qty": 0, "price": 35.0},
            {"desc": "2-ply self-adhered underlayment (Polystick MTS)", "unit": "SQ", "qty": 0, "price": 110.0},
            {"desc": "Standing seam standard color panels 24ga", "unit": "SQ", "qty": 0, "price": 625.0},
            {"desc": "Ridge / hip / eave trim", "unit": "LF", "qty": 0, "price": 9.0},
            {"desc": "Pipe boots / flashing", "unit": "EA", "qty": 0, "price": 65.0},
            {"desc": "Permit + inspections", "unit": "LS", "qty": 1, "price": 850.0},
            {"desc": "Dumpster + disposal", "unit": "LS", "qty": 1, "price": 650.0},
        ],
    },
    "flat_tpo": {
        "name": "Commercial TPO Estimate",
        "work_types": ["Roofing - Flat (TPO)"],
        "lines": [
            {"desc": "Tear off existing flat roof to deck", "unit": "SQ", "qty": 0, "price": 85.0},
            {"desc": "ISO insulation board", "unit": "SQ", "qty": 0, "price": 95.0},
            {"desc": "60-mil TPO membrane (mechanically fastened)", "unit": "SQ", "qty": 0, "price": 295.0},
            {"desc": "Termination bar / edge metal", "unit": "LF", "qty": 0, "price": 8.0},
            {"desc": "Pipe boots / curb flashing", "unit": "EA", "qty": 0, "price": 85.0},
            {"desc": "Permit + inspections", "unit": "LS", "qty": 1, "price": 850.0},
            {"desc": "Dumpster + disposal", "unit": "LS", "qty": 1, "price": 650.0},
        ],
    },
    "flat_sa": {
        "name": "Flat - 3-ply SA Estimate",
        "work_types": ["Roofing - Flat (3-ply SA)"],
        "lines": [
            {"desc": "Tear off existing flat roof to deck", "unit": "SQ", "qty": 0, "price": 85.0},
            {"desc": "Base sheet (mechanically fastened)", "unit": "SQ", "qty": 0, "price": 75.0},
            {"desc": "3-ply self-adhered modified bitumen (Polyglass SA)", "unit": "SQ", "qty": 0, "price": 245.0},
            {"desc": "Edge metal / gravel stop", "unit": "LF", "qty": 0, "price": 7.0},
            {"desc": "Pipe boots / flashing", "unit": "EA", "qty": 0, "price": 65.0},
            {"desc": "Permit + inspections", "unit": "LS", "qty": 1, "price": 750.0},
            {"desc": "Dumpster + disposal", "unit": "LS", "qty": 1, "price": 600.0},
        ],
    },
    "flat_hotmop": {
        "name": "Flat - Hot-Mop Estimate",
        "work_types": ["Roofing - Flat (Hot-Mop)"],
        "lines": [
            {"desc": "Tear off existing flat roof to deck", "unit": "SQ", "qty": 0, "price": 85.0},
            {"desc": "Hot-mop built-up roofing (3-ply)", "unit": "SQ", "qty": 0, "price": 265.0},
            {"desc": "Gravel surfacing", "unit": "SQ", "qty": 0, "price": 45.0},
            {"desc": "Edge metal / gravel stop", "unit": "LF", "qty": 0, "price": 7.0},
            {"desc": "Permit + inspections", "unit": "LS", "qty": 1, "price": 750.0},
            {"desc": "Dumpster + disposal", "unit": "LS", "qty": 1, "price": 600.0},
        ],
    },
    "shingle_flat": {
        "name": "Shingle & Flat Split Estimating Template",
        "work_types": ["Shingle + Flat"],
        "lines": [
            {"desc": "Tear off existing roof to deck (shingle area)", "unit": "SQ", "qty": 0, "price": 65.0},
            {"desc": "Architectural shingles (sloped area)", "unit": "SQ", "qty": 0, "price": 285.0},
            {"desc": "Self-adhered underlayment (sloped)", "unit": "SQ", "qty": 0, "price": 55.0},
            {"desc": "3-ply SA modified bitumen (flat area)", "unit": "SQ", "qty": 0, "price": 245.0},
            {"desc": "Permit + inspections", "unit": "LS", "qty": 1, "price": 850.0},
            {"desc": "Dumpster + disposal", "unit": "LS", "qty": 1, "price": 650.0},
        ],
    },
    "tile_flat": {
        "name": "Tile & Flat Split Estimating Template",
        "work_types": ["Tile + Flat"],
        "lines": [
            {"desc": "Tear off existing tile to deck (sloped area)", "unit": "SQ", "qty": 0, "price": 95.0},
            {"desc": "Tear off existing flat roof to deck", "unit": "SQ", "qty": 0, "price": 85.0},
            {"desc": "Re-nail deck to current code", "unit": "SQ", "qty": 0, "price": 35.0},
            {"desc": "Self-adhered tile underlayment (sloped)", "unit": "SQ", "qty": 0, "price": 75.0},
            {"desc": "Concrete tile (Westlake Royal Saxony 900)", "unit": "SQ", "qty": 0, "price": 425.0},
            {"desc": "Tile adhesive foam set (Polyset AH-160)", "unit": "SQ", "qty": 0, "price": 95.0},
            {"desc": "3-ply SA modified bitumen (flat area)", "unit": "SQ", "qty": 0, "price": 245.0},
            {"desc": "Edge metal / gravel stop (flat)", "unit": "LF", "qty": 0, "price": 7.0},
            {"desc": "Pipe boots / flashing", "unit": "EA", "qty": 0, "price": 65.0},
            {"desc": "Permit + inspections", "unit": "LS", "qty": 1, "price": 950.0},
            {"desc": "Dumpster + disposal", "unit": "LS", "qty": 1, "price": 750.0},
        ],
    },
    "metal_flat": {
        "name": "Metal & Flat Split Estimating Template",
        "work_types": ["Metal + Flat"],
        "lines": [
            {"desc": "Tear off existing roof to deck (sloped area)", "unit": "SQ", "qty": 0, "price": 65.0},
            {"desc": "Tear off existing flat roof to deck", "unit": "SQ", "qty": 0, "price": 85.0},
            {"desc": "Re-nail deck to current code", "unit": "SQ", "qty": 0, "price": 35.0},
            {"desc": "Self-adhered underlayment (sloped)", "unit": "SQ", "qty": 0, "price": 55.0},
            {"desc": "Standing-seam metal panels (Galvalume, 24-ga)", "unit": "SQ", "qty": 0, "price": 645.0},
            {"desc": "3-ply SA modified bitumen (flat area)", "unit": "SQ", "qty": 0, "price": 245.0},
            {"desc": "Trim metal / drip edge / valleys (sloped)", "unit": "LF", "qty": 0, "price": 9.0},
            {"desc": "Edge metal / gravel stop (flat)", "unit": "LF", "qty": 0, "price": 7.0},
            {"desc": "Pipe boots / flashing", "unit": "EA", "qty": 0, "price": 85.0},
            {"desc": "Permit + inspections", "unit": "LS", "qty": 1, "price": 950.0},
            {"desc": "Dumpster + disposal", "unit": "LS", "qty": 1, "price": 750.0},
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
    if "metal" in low:
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
UPGRADES = {
    "shingle": [
        {"desc": "UPGRADE: Premium architectural shingle (GAF Timberline HDZ / OC Duration)", "unit": "SQ", "qty": 0, "cost": 40.0},
        {"desc": "UPGRADE: Designer / luxury shingle (Grand Sequoia, Camelot)", "unit": "SQ", "qty": 0, "cost": 120.0},
        {"desc": "UPGRADE: Full ice & water shield (entire deck, vs eaves only)", "unit": "SQ", "qty": 0, "cost": 45.0},
        {"desc": "UPGRADE: Full peel-and-stick synthetic underlayment", "unit": "SQ", "qty": 0, "cost": 30.0},
        {"desc": "UPGRADE: Ridge vent system (replace static vents)", "unit": "LF", "qty": 0, "cost": 9.0},
        {"desc": "UPGRADE: Premium hip & ridge cap shingles", "unit": "LF", "qty": 0, "cost": 4.0},
        {"desc": "UPGRADE: Lifetime pipe boots / lead jacks", "unit": "EA", "qty": 0, "cost": 35.0},
        {"desc": "UPGRADE: Re-deck / replace plywood", "unit": "SQ", "qty": 0, "cost": 250.0},
        {"desc": "UPGRADE: Extended workmanship warranty (GAF Golden Pledge)", "unit": "LS", "qty": 0, "cost": 600.0},
    ],
    "tile": [
        {"desc": "UPGRADE: 2-ply self-adhered underlayment", "unit": "SQ", "qty": 0, "cost": 55.0},
        {"desc": "UPGRADE: Premium tile profile / color (Eagle Capistrano, Saxony slate)", "unit": "SQ", "qty": 0, "cost": 90.0},
        {"desc": "UPGRADE: Foam-set adhesive system (vs mortar)", "unit": "SQ", "qty": 0, "cost": 60.0},
        {"desc": "UPGRADE: Mortar hip & ridge, color-matched finish", "unit": "LF", "qty": 0, "cost": 5.0},
        {"desc": "ADD-ON: Birdstop / eave closure", "unit": "LF", "qty": 0, "cost": 6.0},
        {"desc": "UPGRADE: Lead / copper pipe flashings", "unit": "EA", "qty": 0, "cost": 65.0},
    ],
    "metal": [
        {"desc": "UPGRADE: Kynar 500 premium color finish", "unit": "SQ", "qty": 0, "cost": 75.0},
        {"desc": "UPGRADE: 24-gauge panel (vs 26-gauge)", "unit": "SQ", "qty": 0, "cost": 90.0},
        {"desc": "UPGRADE: 2-ply self-adhered underlayment", "unit": "SQ", "qty": 0, "cost": 55.0},
        {"desc": "ADD-ON: Ridge vent (vented closure)", "unit": "LF", "qty": 0, "cost": 10.0},
        {"desc": "ADD-ON: Snow guards", "unit": "EA", "qty": 0, "cost": 12.0},
        {"desc": "UPGRADE: Custom trim / flashing color match", "unit": "LF", "qty": 0, "cost": 6.0},
    ],
    "flat": [
        {"desc": "UPGRADE: 80-mil TPO membrane (vs 60-mil)", "unit": "SQ", "qty": 0, "cost": 45.0},
        {"desc": "UPGRADE: Tapered ISO / crickets for positive drainage", "unit": "SQ", "qty": 0, "cost": 120.0},
        {"desc": "UPGRADE: Reflective cool-roof coating", "unit": "SQ", "qty": 0, "cost": 40.0},
        {"desc": "ADD-ON: Walk pads at equipment", "unit": "EA", "qty": 0, "cost": 45.0},
        {"desc": "ADD-ON: Additional roof drain / scupper", "unit": "EA", "qty": 0, "cost": 350.0},
    ],
    "common": [
        {"desc": "ADD-ON: Skylight replacement", "unit": "EA", "qty": 0, "cost": 650.0},
        {"desc": "ADD-ON: New seamless aluminum gutters", "unit": "LF", "qty": 0, "cost": 9.0},
        {"desc": "ADD-ON: Gutter guards / leaf protection", "unit": "LF", "qty": 0, "cost": 8.0},
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
