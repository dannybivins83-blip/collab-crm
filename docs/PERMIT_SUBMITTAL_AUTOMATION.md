# Permit Submittal Automation — Connector Spec

**Audience:** the App Connector coder who will build the portal-submission integration.
**Goal:** after the CRM generates the permit packet, automate (as far as legally/technically possible) **logging into the AHJ's online portal, filling the application, and uploading the documents**, then hand off to a human for payment + final submit.
**Scope:** Palm Beach County (39 AHJs) + Broward County (31 AHJs) — both already in the permit library and the portal dataset. **Martin County = Phase 2** (not in the library yet; needs its own AHJ form set + portal research before wiring).

Companion data: [`data/ahj_portals.json`](../data/ahj_portals.json) — per-AHJ portal/login/registration URLs + process notes (the "sitemap").

---

## 1. Where this ties into the permit builder (the handoff)

Today's pipeline (already built):

```
Job (CRM) ──► permits.build_packet(permit_id)  [modules/permits.py]
                   │  builds `client` dict from the job (owner, address, pcn, system, roof data)
                   ▼
              b.build_packet(client, ahj, system, attachments, out_path, ...)  [packet_builder_handoff/.../build.py]
                   │  assembles the pre-filled packet PDF (cover, app, NOC, affidavits,
                   │  spec sheet, product approvals)
                   ▼
              permits stores packet_file + inserts a "Permit" document on the job
```

**New step the connector adds** — a `submit_packet(permit_id)` action that fires *after* the packet exists:

```
permit (with packet_file) ──► submit_packet(permit_id)
                                   │  loads: packet PDF, the job/client data, the AHJ portal
                                   │  record (ahj_portals.json), the document list (w/ categories),
                                   │  and the SeaBreeze credential for that portal's platform
                                   ▼
                              Connector  →  Accela API adapter  (only where enabled)
                                         →  Playwright RPA adapter (everything else)
                                   │  drives login → apply → fill → upload → STOP at payment
                                   ▼
                              writes back: submission status, confirmation/record #, portal URL,
                              and a "needs human: pay + submit" flag onto the permit record
```

### Data contract the connector receives (from the CRM)
The CRM already holds all of this; the connector should take a single JSON envelope:

```json
{
  "permit_id": 123,
  "ahj": "Royal_Palm_Beach",
  "system": "metal",
  "packet_pdf": "/abs/path/Permit_123_..._Metal.pdf",
  "documents": [
    {"path": "...app.pdf",  "category": "Application"},
    {"path": "...noc.pdf",  "category": "Notice of Commencement"},
    {"path": "...noa.pdf",  "category": "Florida Product Approval / NOA"},
    {"path": "...spec.pdf", "category": "Roof Submittals / Manufacturer Literature"}
  ],
  "job": { "owner": "...", "address": "...", "city": "...", "zip": "...",
           "pcn": "...", "legal": "...", "value": "...", "scope": "REROOF - ...",
           "area": "...", "slope": "...", "mrh": "..." },
  "contractor": { "name": "SeaBreeze Roofing & Sheet Metal, Inc.",
                  "license": "CCC1328689", "qualifier": "Jacintho Carreiro",
                  "email": "permits@seabreezeroof.com", "phone": "561-292-3457" },
  "portal": { /* the matching record from ahj_portals.json */ },
  "credential_ref": "platform:mygovernmentonline"   // points to the secure vault, NOT a plaintext password
}
```

> **Build a packet "splitter" first.** `build.py` emits ONE merged PDF. Most portals want documents uploaded into typed slots/folders (Application, NOC, Product Approval, Roof Submittals). The connector (or a new builder option) needs the packet as **separate categorized PDFs** — and CitizenServe needs the *opposite* (one combined PDF). Plan for both.

---

## 2. The decisive finding: API vs. RPA

| Platform | Contractor submission API? | Auth | Path |
|---|---|---|---|
| **Accela** (Civic Platform / Construct) | **YES** — `POST /v4/records`, `POST /v4/records/{id}/documents` — **but agency-gated** | OAuth2 (`auth.accela.com`), per `agency_name` | **API where the agency enables it**, else RPA |
| Tyler EnerGov / Civic Access | No public contractor API (EnerGov Web API is internal/agency-licensed) | Tyler Identity/Portico SSO | RPA |
| Tyler eSuite | No | per-city user/pass | RPA |
| MyGovernmentOnline | No (export-only data API) | one login, all MGO cities | RPA |
| Avolve ProjectDox | No (agency connectors only) | per-tenant + email code; **invite-driven** | RPA (reactive) |
| Broward ePermitsOneStop | No public API — **but runs on Accela** (Construct API *may* be enablable, unconfirmed) | one login, many Broward cities | RPA (or Accela API if Broward grants it) |
| CitizenServe / eTRAKiT / Click2Gov / BS&A / SagesGov / MaintStar / SmartGov / CityView / GeoCivix / Gov-Easy / Community Core | No contractor submission API | per-platform | RPA |

**Implication:** the connector is fundamentally a **Playwright RPA engine with a per-platform adapter**, plus **one optional API adapter (Accela)** for agencies that enable it. Worth a direct phone call to **Broward County Building Code Division** asking whether they expose the Accela Construct API to third-party/contractor apps — if yes, that single API adapter covers a large share of Broward via OneStop.

---

## 3. Platform → AHJ map (build adapters by platform, not by city)

The 70 cities collapse into ~12 platforms. Build one adapter per platform; the login model tells you how much it scales.

### Single-login platforms (one SeaBreeze account → many cities — highest leverage)
- **MyGovernmentOnline** *(one login, all MGO cities)* — PBC: Gulf_Stream, Highland_Beach, Juno_Beach, Jupiter_Inlet_Colony, Lake_Clarke_Shores, Loxahatchee_Groves, Manalapan, North_Palm_Beach, Ocean_Ridge, Palm_Beach_Shores, South_Palm_Beach · Broward: Parkland
- **Broward ePermitsOneStop** *(one login, many Broward cities; Accela underneath)* — Deerfield_Beach, Hallandale_Beach, Lauderdale_By_The_Sea, Lauderdale_Lakes, Southwest_Ranches (+ Coconut_Creek, Hollywood partial)
- **SagesGov** *(one credential, per-city "Request Access")* — Boynton_Beach
- **Tyler Civic Access** *(Global Login, but per-city verification + each city a separate subdomain)* — PBC: Boca_Raton, Delray_Beach, Jupiter, Palm_Beach, Palm_Beach_Gardens, Riviera_Beach, West_Palm_Beach · Broward: North_Lauderdale, Oakland_Park, Pembroke_Pines

### Per-city-account platforms (separate login each city)
- **Accela ACA** — Cooper_City, Fort_Lauderdale (LauderBuild), Miramar, Plantation, Weston, Hollywood
- **Avolve ProjectDox** *(invite-driven; reactive automation only)* — Royal_Palm_Beach, Wellington, Davie, Margate, Pompano_Beach, Tamarac
- **Tyler eSuite** — Greenacres, Dania_Beach
- **CitizenServe** — West_Park, Wilton_Manors
- **eTRAKiT** — Coral_Springs
- **BS&A Online** — Tequesta · **MaintStar** — Palm_Springs · **CityView** — Westlake · **SmartGov** — Lighthouse_Point · **GeoCivix/CAP** — Lake_Park · **iWorQ** — Pahokee · **Gov-Easy** — Pembroke_Park · **Community Core** — Hillsboro_Beach

### No online submittal (paper/email — connector can't help; flag as manual)
Atlantis, Belle_Glade, Briny_Breezes, Cloud_Lake, Glen_Ridge, Haverhill, Hypoluxo, Lantana, Mangonia_Park, South_Bay, Village_of_Golf · Lauderhill, Lazy_Lake, Sea_Ranch_Lakes (unverified/county)

---

## 4. Generic submission workflow (the shared step model)

Every online platform fits this skeleton; adapters specialize each step.

1. **Login** — credential from the vault; handle MFA/CAPTCHA via human-in-the-loop on first auth, then **persist the authenticated session** (cookies) for reuse.
2. **Start application** — "Apply" / "Request a Permit" / "Submit Application"; pick jurisdiction (single-login platforms) and the **roofing/re-roof permit type**.
3. **Location** — search property by address/parcel; let it auto-populate owner/folio.
4. **Contacts** — attach the SeaBreeze **Contractor** contact (license must already be on file — see §5) + **Owner**.
5. **More info / details** — scope, valuation (**accurate value = top auto-rejection avoidance**), squares, slope, mean roof height.
6. **Attachments** — upload the categorized PDFs into the right typed slots/folders (Application, NOC, Product Approval/NOA, Roof Submittals). **PDF only, unlocked/flattened, not password-protected.**
7. **Signature** — type/draw where the portal collects an applicant signature (NOT the notarized owner signature — see `PERMIT_SIGNATURE.md`; NOC/affidavit stay wet/RON-signed).
8. **STOP → human checkpoint** — review, **pay the fee**, click final **Submit**. (Fees are usually only due after a sufficiency review, so this is a natural pause.)
9. **Track / resubmit** — poll status; on plan-review corrections, surface the comments in the CRM and re-upload versioned files (ProjectDox/GeoCivix require the **exact same filename** on resubmit).

### Platform-specific notes the adapters must encode
- **Accela ACA / Broward OneStop:** document **Category** matters (Application / Notice of Commencement / "Roof Submittals, Manufacturer's Literature" / "Florida Product Approval"). Requires popups enabled. Plan upload often hands off to a **Digital Plan Room (ProjectDox)**. Plans may need a **third-party digital signature** to be accepted.
- **Tyler Civic Access:** 7-step Application Assistant (Location→Type→Contacts→More Info→Attachments→Signature→Review). "More Info" fields **lock after submit**. Registering a portal profile ≠ being an approved permit-pulling contractor.
- **Avolve ProjectDox:** **you cannot start cold** — the agency creates the project and emails an "Upload and Submit" task; the bot must **react to that email**, accept the task, upload to the named folders, and **must click Submit** (3 mandatory steps) or the city never receives it.
- **MyGovernmentOnline:** Docs tab → **Customer** → **Add New File** (PDF). Use "Submission to an Existing Project" for a trade sub-permit.
- **CitizenServe:** **single file only** at submittal → merge the whole packet into one PDF.
- **GeoCivix/CAP:** some FL tenants split into **two portals** (apply in one, upload in another) — the adapter must hand off between them.

---

## 5. Credentials & registration model

- **Credentials are per-platform, NOT one-per-city and NOT "all the same."** One MGO login covers all MGO cities; one Broward OneStop login covers many Broward cities; SagesGov is one credential with per-city access requests; **Accela/EnerGov/Avolve/eSuite are separate per city.** Store credentials keyed by **platform** in a **secure, encrypted vault** (see the credential-vault note — do **not** put portal passwords in the database as plaintext; key from a secret env var).
- **The unautomatable bottleneck is one-time contractor registration per jurisdiction.** Before any bot can pull a permit, SeaBreeze (CCC1328689) must already be an **approved contractor on file** in that city — typically: state/county license + **COI** naming the city + **workers' comp** (or exemption) + local **Business Tax Receipt**, emailed to the building dept, sometimes with admin approval (~1 business day). The connector should **track registration status per (platform/city)** and only enable auto-submit once "registered."

---

## 6. The honest automation ceiling

Tell stakeholders plainly — "fully unattended log-in-and-submit across 70 cities" is not realistic. Hard stops:
- **Per-city contractor registration** — manual, one-time, human.
- **Online fee payment** — never store card credentials; hand to a human (fees are post-sufficiency-review anyway).
- **MFA / CAPTCHA** — present on some portals; needs human-in-the-loop first auth + session reuse.
- **ProjectDox** — invite/task-driven; the bot reacts, it can't initiate.
- **Digital-signature/seal requirements** — sealed wind calcs / digitally-signed plans must be produced upstream; the portal won't seal them.

**Realistic value:** the connector auto-drives login → application → categorized uploads, then parks the job at "ready to pay + submit" for a human — turning a 20-minute manual data-entry-and-upload chore per permit into a 1-minute review-and-submit. That is the deliverable to aim for.

---

## 7. Recommended build order (for the connector coder)

1. **PDF splitter/merger** — categorize the packet into typed PDFs (and a single-merged variant for CitizenServe). Prereq for every adapter.
2. **Secure credential vault** + per-(platform/city) **registration-status tracker**.
3. **MyGovernmentOnline adapter** (RPA) — one login, ~12 cities; biggest single-adapter coverage.
4. **Broward ePermitsOneStop adapter** — one login, many Broward cities; **first phone Broward re: Accela Construct API** (API would beat RPA here).
5. **Tyler Civic Access adapter** — covers Boca/Delray/WPB/Jupiter/PBG + 3 Broward cities.
6. **Accela ACA API adapter** (where agencies enable it) + ACA RPA fallback.
7. **SagesGov, Avolve ProjectDox (reactive), CitizenServe, eTRAKiT**, then the per-city long tail by job volume.
8. Mark the paper/email AHJs as **manual** in the CRM (no adapter).

---

## 8. Phase 2 — Martin County

Not in the current permit library or `ahj_portals.json`. Before wiring: (a) build the Martin AHJ form set (Stuart, Sewall's Point, Ocean Breeze, Jupiter Island, unincorporated Martin County) into the library the same way as PBC/Broward, (b) run the same portal + workflow research, (c) add the records to `ahj_portals.json`, (d) reuse whichever platform adapters they turn out to use (Martin County unincorporated uses its own system; the towns vary).

---

## 9. Sitemap appendix

The full per-AHJ portal/login/registration URLs, online-submission/upload flags, NOC recording location, building-dept phone/email, and confidence are in [`data/ahj_portals.json`](../data/ahj_portals.json). Render that on each permit's detail page as the clickable "Open [city] portal" + login link (the CRM-side feature), and feed the same record into the connector's `portal` field.

*Research basis: official municipal .gov pages + portal-vendor documentation, captured 2026-06-09. Verify login URLs and roofing-submittal specifics at filing time; per-record confidence + caveats are in the dataset.*
