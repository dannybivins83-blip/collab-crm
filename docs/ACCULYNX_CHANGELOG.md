# AccuLynx Replica — Changelog

## Research watch

Recurring public-surface research passes. Each entry: sources checked + NEW/CHANGED
items vs our specs (`ACCULYNX_SPEC.md`) + LIVE_CAPTURE + the clone, or "no public
changes detected." LIVE_CAPTURE values stay authoritative; exact in-app values
(hex, pixel sizes, verbatim labels) require a live my.acculynx.com capture — public
sources only surface announced/approximate changes.

### 2026-06-08 — First research-watch pass (baseline)

This is the first dated entry under this heading (prior "Pass 5" on 2026-06-07 set up
the watch but logged no findings), so everything below is flagged relative to our
existing specs + clone rather than "since last watch."

**Sources checked:**
- changelog.acculynx.com (official release feed) — CHECKED FIRST
- acculynx.com/spring-2026-product-updates/ (Spring '26 / March '26 release)
- acculynx.com/fall-2025-product-updates/ (Fall '25 release)
- Web search: "AccuLynx new feature release 2026", "AccuLynx 2026 navigation/milestone/brand color change"
- (support.acculynx.com help-center surfaced via the above; G2/Capterra/SoftwareAdvice/GetApp 2026 review listings referenced but not deep-fetched)

**NEW / CHANGED vs our specs + clone** (each = announced publicly; exact in-app labels still need live capture):

1. **Custom Fields Manager (Spring '26)** — AccuLynx now has a native UI to "create and
   manage your own custom fields for both contacts and jobs" via Account Settings,
   deployable on contact forms, lead forms, job overview pages, and **reports**, and
   exposed through API/HubSpot/Spotio. Source: acculynx.com/spring-2026-product-updates/ (2026).
   *Clone status:* we ALREADY have a Custom Fields module (Phase 5, `modules/customfields.py`)
   for lead/job/contact. Gap vs AccuLynx: ours doesn't surface custom fields in **reports**
   or on the **job overview** page (only lead detail). Minor parity gap, not a contradiction.

2. **Appointment Outcomes tracking (Spring '26)** — new system to "record and track specific
   outcomes of calendar appointments," with custom outcomes defined in settings and an
   **Appointments Report**; also exposed in the Field App calendar view. Source:
   changelog.acculynx.com; acculynx.com/spring-2026-product-updates/ (2026).
   *Clone status:* not present. New feature we haven't cloned.

3. **Estimating enhancements (Spring '26)** — three concrete changes to the estimate/proposal flow:
   - **Smart Fields now pull from multiple estimates** to enable **"Good, Better, Best"** pricing presentations.
   - **Hide/remove the tax line item** (and **hide O&P**) on proposal PDFs for lump-sum presentation.
   - **Pitch-based labor** — templates can **calculate labor by roof pitch** (auto rate per roof angle).
   Source: changelog.acculynx.com; acculynx.com/spring-2026-product-updates/ (2026).
   *Clone status:* our estimate builder has sections/lines/margin/measurements but none of these
   three. Proposal print-options panel exists (has a Tax toggle already) — could add O&P hide +
   "Good/Better/Best" multi-estimate compare + a per-pitch labor multiplier. These are the most
   relevant changes to our priority area (estimating).

4. **Mobile (Field App) full estimating (Fall '25)** — build complete estimates on phone
   (materials, labor, tax, profit margin). Confirms our margin/tax/section model is correct;
   no web-UI change. Source: acculynx.com/fall-2025-product-updates/.

5. **DataMart add-on (Fall '25)** — enterprise data-export layer for Tableau/Power BI/Klipfolio.
   Out of scope for the clone. Source: acculynx.com/fall-2025-product-updates/.

6. **Two-Factor Authentication (Fall '25)** — phone-code 2FA, account- or user-level. Source:
   acculynx.com/fall-2025-product-updates/. *Clone status:* not present (auth is single-factor).

7. **Calendar overhaul + multi-calendar (Fall '25 / Spring '26)** — real-time Outlook + iCal
   (Apple iCloud) sync, multi-attendee appointments w/ notifications, new filters (trade/work
   type/category/crew), schedule from lead form, and **multiple calendars viewed side-by-side**.
   Source: acculynx.com/fall-2025-product-updates/, /spring-2026-product-updates/, changelog.acculynx.com.

8. **Trade-level financial reporting (Fall '25)** — Financial Worksheet now ties revenue/expense
   to specific **trades**; new "Job Value by Trade" + "Job Worksheet" reports. Relevant to our
   Worksheet module if we want per-trade profit. Source: acculynx.com/fall-2025-product-updates/.

9. **Integrations (Fall '25–Spring '26)** — Geospan **3D rendering** ("View 3D Rendering" button
   in Measurements), **RoofScope** aerial-report ordering embedded in estimating (Feb '26, ~12 hr
   turnaround), **Hover** inspection-report + photo import, **CallRail** call/lead capture, Sage
   Intacct roll-up. Measurement-provider list is broader than our spec notes. Source:
   changelog.acculynx.com; web search (2026).

10. **Lead Form redesign (Spring '26)** — "modernized" lead form with improved contact/project-
    location layout and a **"possible matches"** dedupe panel. Cosmetic; suggests a refreshed lead
    intake screen. Exact new layout NOT publicly pixel-detailed — needs live capture. Source:
    acculynx.com/spring-2026-product-updates/.

11. **SmarterDocs (Spring '26)** — "Edit Company SmartDocs" permission role, viewed-packet
    notifications for reps, e-signed watermark display setting, additional estimate smart fields.
    Source: changelog.acculynx.com.

**NO change detected** in: top/primary **navigation structure & labels**, **pipeline milestone
names**, **brand colors** (masthead #24476C / nav #4680BF / Help orange #F5863B), **Roboto**
typography, or the core **Estimate → Section → Slope → narrative-scope + Cost/Price/Margin**
hierarchy. No public source indicates a nav redesign, milestone rename, or palette change since
our LIVE_CAPTURE. Our LIVE_CAPTURE values therefore remain GROUND TRUTH and are not contradicted.

> Caveat: exact in-app labels for the new Spring '26 features (e.g. whether the multi-estimate
> control literally reads "Good, Better, Best", the O&P-hide toggle wording, the pitch-labor field)
> are **not publicly stated; needs live capture** in a logged-in my.acculynx.com session.

---

## 2026-06-07 — Major replica pass (live-capture driven)

Built from the verified live capture (`ACCULYNX_LIVE_CAPTURE.md`). All changes
applied to `whitelabel-crm/` and smoke-tested (28/28 routes 200).

### Look
- **Two-tier top navigation** replacing the left sidebar: navy masthead (#24476C)
  with brand + department picker + license + user; blue primary nav (#4680BF) with
  New▾ / Dashboard / Contacts / Leads / Jobs▾ / Estimates / Reports / Tools▾ +
  search ("Job #, Customer Name or Address") + orange Help.
- **Exact AccuLynx palette + Roboto font** applied app-wide and to the company seed.
- **Dashboard = "Current Pipeline"** with the L/P/A/C/I milestone circles in exact
  colors (#F2C000 / #F78300 / #8CC63F / #29ABE2 / #E25050) + counts + $K, plus an
  Activity Feed and Overdue Follow-Ups.

### Functionality
- **AccuLynx milestone model** — jobs now use the exact 21 production milestones
  (Approved → IF NEEDED Finance NTP → Documentation Needed → Permit Applied For →
  Permit Approved → Pre Con → Tear Off → Roof Install → Punch Out → Final
  Inspection → Completed → Invoiced → Closed → Canceled). Each tagged to an
  L/P/A/C/I bucket. Legacy stage keys auto-migrated.
- **Job workspace rebuilt** to match the AccuLynx Job File: header with
  value/Balance Due/% ring, a **milestone tracker** (L-P-A-C-I) with **Advance Job**
  + jump-to-stage dropdown, and **tabbed Job Activity**: Communications · Estimates ·
  Worksheet · Invoices · Orders · Photos · Documents · Checklist.
- **Estimates rebuilt to AccuLynx structure**: Estimate → **Sections** (named by
  trade) → **cost line items** (Description / Unit / Qty / Waste % / Unit Cost /
  Line Cost) + a per-section **narrative scope-of-work** (lettered clauses; the real
  SeaBreeze shingle/tile/metal/flat scopes are seeded). **Cost vs Price** with a
  **Profit Margin %** model (`Price = Cost / (1 − margin)`), green ESTIMATE TOTAL
  bar, Taxes & Discounts + **Margin % / Net Profit**. Proposal print view renders
  sections + scope. E-signature retained.

### 2026-06-07 — Second live-capture refinements
- Estimate builder columns now match the AccuLynx editor exactly: **Product/Description | Qty | Unit | Cost/Unit | Cost | Price** (per-line Price computed from section margin). Verified our math reproduces AccuLynx's real estimate ($9,760.79 cost → $14,000 @ 30.28% margin).
- **Proposal print-options panel** added (Section Description · Show Line Items · Quantities/Units · Line Item Price · Section Totals · Tax) — mirrors AccuLynx's Preview, with letterhead = logo + company block + Company Representative + parties + section scope + totals.
- Company contact info updated to the real letterhead values (phone (561) 292-3457, dannyb@seabreezeroof.com).
- Enabled template auto-reload (`TEMPLATES_AUTO_RELOAD`) so template edits show without a server restart.

### 2026-06-07 — Pass 3 (per-line price, templates, list view)
- **Per-line editable Price** on estimates: edit margin to bulk-apply, or override a single line's price (margin re-derives). Matches AccuLynx's editable Price column.
- **Template Manager** (Tools → Estimate Templates): DB-backed, editable name / work-type / scope narrative / line items. Seeded from the code defaults; new estimates build from these.
- **Leads list view** = AccuLynx "Assigned Leads & Jobs": Milestone-Pipeline filter sidebar with live counts, milestone-badge result cards, status pills, List/Board toggle. Nav "Leads" now opens the list (board still one click away).
- **Jobs list view** matching the same layout, with the full 21-milestone filter sidebar (Approved sub-milestones indented). Jobs nav dropdown now lists by bucket/status. Both Leads and Jobs have List⇄Board toggles.

### 2026-06-07 — Pass 4 (Roof Measurements → Estimates)
- New **Measurements** model + Job File **Measurements tab**: squares, pitch, stories, facets, waste %, and ridge/hip/valley/rake/eave/step-flash linear footage. Upload a **RoofGraf/EagleView PDF** (auto-parses squares + pitch via build.py when available; also filed under job Documents).
- **Estimate builder Measurements panel** with **"Apply measurements → quantities"**: fills line Qty by type — SQ lines = squares (×waste; tear-off no waste), ridge/hip lines = ridge+hip LF, valley = valley LF, drip edge = eave+rake LF. Verified across the full 9-line shingle template.
- Matches the SeaBreeze workflow (RoofGraf, not Roofr; Karla uploads to job documents).

### 2026-06-07 — Pass 5 (AccuLynx lead sync + permit builder)
- **Synced 50 live AccuLynx prospects** into the CRM leads (read from my.acculynx.com Jobs→Prospects, deduped by name, each with a contact). Added a CORS-open `POST /leads/import` endpoint for future syncs + a local file-based import path.
- **Permit Packet Builder folded into the Permits module**: imports the real `build.py` engine + SeaBreeze_Permit_Library. Permit detail now has a wizard — pick AHJ (70 PBC/Broward municipalities), system (Shingle/Tile/Metal/Flat), underlayment, product, attach RoofGraf → **builds a fully pre-filled county permit packet PDF** (SeaBreeze contractor info + owner/address from the job + county forms + product approvals). Verified: generated a 5.2 MB packet for Peterson/Delray Beach/metal; auto-files it under job Documents.
- Recurring research watch updated to include **changelog.acculynx.com** + **support.acculynx.com**.

### Still to do (toward pixel-exact)
- Leads list/preview-pane view (AccuLynx "Assigned Leads & Jobs" style) alongside the kanban.
- Worksheet amendments/change-orders ledger.
- Template Manager (DB-backed) to replace hardcoded estimate templates.
- Material/Labor order generation directly from an estimate.
- Live capture of the New-Estimate line editor fields + Worksheet internals + Proposal PDF.

## 2026-06-07 — Phase 1: Worksheet + Profit Analysis
- New tables `worksheets` (one per job: contract_value) + `worksheet_lines` (category Material/Labor/Permit/Overhead/Other, description, budget_cost, actual_cost).
- `modules/worksheet.py`: `/worksheet/<job_id>` page — inline-editable budget vs actual per line, add/remove lines, contract value, draw schedule, live **Profit Analysis** (contract, budget cost, actual cost, gross profit $/%, variance budget−actual).
- **Seed from estimate**: a worksheet auto-builds from the job's signed (or latest) estimate — each estimate section line → a budget+actual line, category inferred from description; contract value = estimate total. Verified profit reconciles with the estimate margin (30% test case).
- Profit surfaced on **job detail** (Worksheet tab → Profit Analysis KPI strip + "Open Worksheet") and on the **jobs list** (Profit $/% per row), via a `job_profit()` Jinja global (no per-view wiring).
- Conventions followed: tables in SCHEMA string, generic db helpers, reused `estimates.line_cost`/`estimate_totals`, CSS vars + `.kpi`/`.card` classes, theme money helpers. Smoke-tested: login + all worksheet/job routes 200/302.

## 2026-06-07 — Phase 2: Orders + Order Manager
- New tables `orders` (job_id, type Material|Labor, vendor, po_number, status draft|ordered|delivered|received, dates, department) + `order_lines` + `vendors`.
- `modules/orders.py` (new, alongside `materials` — non-breaking): **Order Manager** `/orders` cross-job queue with status/type/vendor filters (jobs-list layout, department-scoped); order detail with editable lines + vendor picker (datalist) + PO number + status workflow; **PO print** view on company letterhead; delete.
- **Generate from estimate**: Job → Orders tab → one click creates a Material PO (material/permit/overhead lines) + Labor PO (labor lines), categorized via `worksheet._category_for`. Verified PO-M + PO-L created from a job estimate.
- Status changes (ordered/delivered/received) stamp dates and **log to the job timeline**. Vendors admin-managed at `/orders/vendors` (added to `auth.ADMIN_ONLY_PATHS`).
- Job-detail Orders tab migrated to the new module via a `job_orders()` Jinja global (no `jobs.py` changes). Smoke-tested: login + all order/vendor/job routes 200/302; activity logged.

## 2026-06-07 — Phase 3: Workflow Manager (Automations)
- New table `automations` (name, trigger_stage, action_type create_task|draft_email|create_reminder, template_text, offset_days, active).
- `modules/automations.py`: rules fire on milestone change. Hooked **without editing jobs.py/leads.py** — `init_automations()` wraps `db.add_activity`, so every stage-change activity (kind='stage', e.g. "Moved to Permit Approved") is matched against active rules for that stage and executed: creates an `activities` task (due = today+offset), a "⏰ Reminder" task, and/or a "✉️ DRAFT (auto)" in the timeline — **never auto-sent**. Template tokens {customer}/{address}/{ahj}/{stage}/{company} substituted from the record.
- Admin **Workflow Manager** at `/workflow` (added to `auth.ADMIN_ONLY_PATHS`): list/create/edit/toggle/delete rules. Linked under Tools → Workflow Manager.
- Seeded 6 sensible defaults mirroring the per-stage todos (permit submitted/approved, pre-con, final, lead follow-up).
- Smoke-tested: advancing a job to "Permit Applied For" auto-created the configured task + email draft; toggling the rule off stopped it; non-admin blocked (302).

## 2026-06-07 — Phase 4: Commissions
- New `commissions` table (job_id, rep, basis contract_value|profit, rate_pct, amount, status pre|approved|paid). `modules/commissions.py` (table self-created, per the auth.py convention).
- Sold jobs auto-get a **pre-commission** (computed from worksheet gross profit or contract value × rate, default 10%); recomputes while 'pre'. `/commissions` list with per-rep **summary** (pre/approved/paid totals) + status filters; inline edit rep/basis/rate; **Approve** (on completion) → **Mark paid**. Status changes log to the job timeline. `job_commission()` Jinja global.
- Linked under Tools → Commissions. Verified: pre-commission created, approve→paid worked, per-rep summary rolls up.

## 2026-06-07 — Phase 5: Config + intelligence
- **Custom Fields**: replaced the stub `modules/customfields.py` with a full impl — `custom_fields` (+options) / `custom_values` tables; admin page (`/customfields`, admin-checked) to define fields per entity (lead/job/contact) with type text/number/date/select/checkbox; rendered + saved on **lead detail** via `custom_fields()`/`custom_values()` Jinja globals + a `/customfields/values/<et>/<id>` save endpoint (no edits to lead/job view handlers).
- **Editable Lead Sources + Contact Types**: new `lead_sources`/`contact_types` tables seeded from `constants.LEAD_SOURCES` + defaults; admin-managed on the same config page; lead & contact forms now use the DB lists via `db_lead_sources()` global.
- **Lead Rank (1–4)**: `leads.rank` column + AccuLynx green-bars on the leads list (click to set via `/customfields/rank/<id>`, AJAX). 
- Config routes admin-gated inline (value-editing/rank stay open to all users). Smoke-tested: fields add/save, sources/types editable, rank persists, non-admin blocked from config (302).
