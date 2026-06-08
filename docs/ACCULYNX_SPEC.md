# AccuLynx UI/UX Specification — Clone Reference

Authoritative spec for mimicking AccuLynx (my.acculynx.com) in our white-label CRM
(`whitelabel-crm/`, Flask + SQLite + Jinja). Compiled from AccuLynx's public marketing
site, support knowledge base, software-review sites, and the project's own memory notes
(verified AccuLynx URLs and template mapping from live sessions).

> **Confidence key:** `[V]` verified from a cited public source · `[M]` from project memory
> captured during live AccuLynx sessions · `[E]` best-effort ESTIMATE (clearly not a
> verified value — confirm against the live logged-in account). Precise in-app hex codes
> and pixel metrics are behind a login; everything marked `[E]` needs live confirmation.

---

## 1. Brand & color palette

AccuLynx runs **two visually distinct color systems**:

- **Marketing site (acculynx.com):** logo described in their own asset name as
  `acculynx-logo-navy-orange-gray` — i.e. **navy + orange + gray**, white backgrounds,
  orange call-to-action buttons ("See Demo", "Get Pricing"). `[V]`
- **In-app (my.acculynx.com):** a cooler **blue / teal-forward** workspace UI — the
  application chrome is a saturated medium blue header/nav, not the marketing orange.
  Status is conveyed with green (approved/won/paid), amber/orange (pending/warning),
  red (overdue/dead/lost), and neutral grays. `[E]` (exact hexes need live confirmation)

### Color table

| Token | Hex | Confidence | Where AccuLynx uses it | Our current value |
|---|---|---|---|---|
| Brand navy (logo) | `#1B3A5B` – `#13293D` range | `[E]` | Logo wordmark, marketing headings | n/a |
| Brand orange (CTA) | `#F4811F` / `#F47920` | `[E]` | Marketing buttons, accents, logo "swoosh" | `--warn #d98200` (close) |
| **App primary blue** (header/nav, primary buttons, active states) | `#2C7BE5`–`#1D6FD0` | `[E]` | Top bar, left-nav active item, primary action buttons, links | `--accent #2f6df6` (very close) |
| App teal accent | `#17A2B8`-ish | `[E]` | Secondary highlights, some icons/badges | n/a |
| Success green (Approved / Won / Paid milestones, "signed") | `#27AE60` / `#2E9E5B` | `[E]` | Milestone pills, signed status, money totals | `--accent2 / --ok #0f9e82` |
| Warning amber (pending, due-soon) | `#F0A422` / `#F4811F` | `[E]` | Aging/stalled, pending statuses | `--warn #d98200` |
| Danger red (overdue, Dead/Lost, disputes) | `#E03B3B` / `#D0021B` | `[E]` | Overdue badges, dead/lost milestone, payment disputes | `--danger #d93a36` |
| Text primary | `#1F2A37` / `#222` | `[E]` | Body text | `--txt #1d2a44` |
| Text muted | `#6B7280` | `[E]` | Secondary/meta text | `--mut #64748b` |
| App background | `#F4F6F9` / `#EEF2F7` | `[E]` | Page background behind cards | `--bg #eef2f8` (close) |
| Card/panel | `#FFFFFF` | `[V]` | Cards, job file panels | `--panel #ffffff` |
| Border/line | `#E2E8F0` / `#DDE3EC` | `[E]` | Card borders, table rules | `--line #dde4ef` |

> **Net finding:** our clone's blue (`#2f6df6`) and grays are already a very good match for
> AccuLynx's **in-app** scheme. The main brand gap is that AccuLynx's **identity** color is
> navy+orange, surfaced as an **orange CTA**. If we want the AccuLynx "feel," keep blue as
> the app primary but introduce an orange secondary/CTA token. Our green (`#0f9e82`) reads
> more teal than AccuLynx's grassier `#27AE60` — nudge greener for status.

### Recommended CSS variables (drop-in for `static/app.css`)

```css
:root{
  /* App workspace (matches AccuLynx in-app blue scheme) */
  --accent:        #2c7be5;   /* primary blue — header, nav-active, primary buttons */
  --accent-dark:   #1d6fd0;   /* hover/pressed primary */
  --accent2:       #27ae60;   /* success green — won/approved/paid/signed */
  --warn:          #f0a422;   /* amber — pending / due soon / stalled */
  --danger:        #e03b3b;   /* red — overdue / dead / lost / disputes */
  --ok:            #27ae60;

  /* Brand identity (AccuLynx marketing navy+orange) — use for CTAs/logo area */
  --brand-navy:    #173a5b;
  --brand-orange:  #f4811f;   /* big call-to-action buttons */

  /* Neutrals */
  --bg:    #f4f6f9;
  --panel: #ffffff;
  --panel2:#eef2f7;
  --line:  #e2e8f0;
  --txt:   #1f2a37;
  --mut:   #6b7280;
}
```

---

## 2. Typography & component styles

- **Fonts** `[V/E]`: marketing + app use a clean humanist **sans-serif**. Public pages read
  as a system/Google sans stack (Roboto/Open-Sans/Inter family). Safe clone stack:
  `"Inter", "Helvetica Neue", Arial, "Segoe UI", Roboto, sans-serif`. Our current
  `-apple-system, Segoe UI, Roboto…` stack is fine; adding Inter would sharpen the match. `[E]`
- **Density:** medium. AccuLynx is **card-based with tabbed job files** — not ultra-dense
  spreadsheet UI, not airy marketing whitespace. Lists/tables for pipeline and tracking
  views; cards/panels inside the job file. `[V]`
- **Buttons:** flat, lightly rounded (~4–6px). Primary = solid blue in-app; CTA = solid
  orange on marketing. Secondary = outline/ghost. Our 8px radius + flat fill already matches. `[E]`
- **Badges/pills:** rounded status pills colored by milestone (green/amber/red/blue/gray) —
  exactly the pattern our `.pill`/kanban dots use. `[V]` (milestones described as color stages)
- **Tabs:** the **job file is a tabbed workspace** (Overview/Messages/Photos/Documents/
  Estimates/Worksheets/Orders/etc.) — a top tab strip inside the record. We currently render
  job detail as a two-column page with no tab strip. `[V]`

---

## 3. Sitemap / navigation

AccuLynx's primary navigation (left/main menu + Tracking submenu). Labels below are taken
from the support KB dashboard article, the public feature list, and the project's verified
URL map. `[V]`/`[M]`

**Main navigation (top-level):**
1. **Dashboard** `[V]` — pipeline snapshot, customizable widgets, bookmarked reports
2. **Leads** `[M]` — Unassigned / Distributed / My Distributed; "Create Lead"
3. **Jobs** (the pipeline; AccuLynx calls records "**jobs**") `[M]` — filtered by milestone:
   Assigned, Prospect, Approved, Completed, Invoiced, Closed, Dead/Cancelled
4. **Contacts** `[V/M]`
5. **Photos** (Job Photo Activity; CompanyCam-synced) `[V]`
6. **Reports** (ReportsPlus — reports & dashboards, bookmarking) `[V]`
7. **Calendar** `[M]`
8. **Task Manager** `[M]`
9. **Automation Manager** (Workflow Manager / Automations) `[V/M]`
10. **Production** → Production Scheduler, Order Manager `[M]`

**Tracking submenu** (AccuLynx's "track everything in one place" hub) `[M]/[V]`:
Submitted Leads & Jobs · Job Progress · Job Signatures · Work Schedule · Worksheets ·
Invoices · Submitted Orders · Financing Offers (AccuFi) · Pre-Commissions · Commissions ·
Permits · Supplements · Mortgage Checks · Measurement (report providers).

**Top bar:** logo (left), global search, account/company switcher, notifications,
help/profile (right). `[V]`

> **Clone gap:** our left nav is Dashboard · Contacts · Sales Pipeline · Jobs/Production ·
> Estimates · Invoicing · Permits · Materials · Calendar · Tasks · Communications · Reports ·
> Settings. AccuLynx does **not** expose "Estimates" or "Permits" as top-level modules — they
> live **inside the job file** and under **Tracking**. See discrepancy report.

---

## 4. Pipeline milestones / stages

AccuLynx's core concept is the **Milestone** (a.k.a. milestone list). A record is a **Job**
that moves through milestones; the dashboard pipeline shows counts/value per milestone. `[V]`

**Milestone list (verified from live URL filters + KB):** `[M]/[V]`

| Order | Milestone | Notes |
|---|---|---|
| Leads | **Unassigned → Distributed → My Distributed → Assigned** | Lead intake sub-stages |
| 1 | **Prospect** | Lead working as an opportunity |
| 2 | **Approved** | Job sold / contract approved (the Prospect→Approved transition is the canonical automation trigger) |
| 3 | **Completed** | Production finished |
| 4 | **Invoiced** | Billed |
| 5 | **Closed** | Paid & finalized |
| — | **Dead / Cancelled** | Lost/cancelled (terminal) |

Milestones are **customizable** per account, and each milestone can carry **checklists**,
**automations/workflows**, and **proposal templates**. `[V]` Cards/list rows show customer,
address, job value, rep, and aging; milestone is a colored pill/stage. `[V]`

> Our clone splits this into two boards (LEAD_STAGES + JOB_STAGES) with different stage names
> (New Lead/Contacted/Appt/Estimate Sent/… and Approved/Sign-Up Docs/HOA/Permit/…). That is a
> richer roofing back-office than AccuLynx's stock milestones — fine to keep, but rename the
> top-level buckets to AccuLynx's words (Prospect/Approved/Completed/Invoiced/Closed/Dead).

---

## 5. Estimating workflow (CRITICAL)

AccuLynx separates three financial documents, all living inside the **Job File**: `[V]`

- **Estimate** — the customer-facing sell document; becomes a **branded, signable Proposal**.
- **Order** — material **and** labor orders generated from the estimate (Material Order /
  Labor Order, sent to suppliers like ABC Supply, SRS, QXO). `[V]`
- **Worksheet** (**Financial Worksheet** / **Invoice Worksheet**) — internal job-costing &
  billing: one **Financial Worksheet per job**, tracks estimates, invoices, payments,
  expenses, real-time profitability, "approved job value" and "outstanding balance," with
  **collapsible sections**, **amendments** (change orders, discounts, insurance claims,
  supplements, upgrades, "work not doing") and direct editing. `[V]`

### Step-by-step: create an Estimate `[V]`

1. **Open the Job File → Estimates tab → "Create Estimate"** (or build from Field App).
2. **Choose a template.** Templates live in the **Template Manager** and are organized by
   **Trade** (roofing/siding/gutters), frequently-used materials, roof type, insurance jobs,
   etc. `[V/M]` (Our verified template names: *2025 Shingle Estimating Template*, *Tile*,
   *Standing Seam Galvalume/Standard Color*, *…& Flat Split* variants, *Commercial TPO*,
   *Flat – 3ply SA*, *Flat – Hot-Mop*. `[M]`)
3. **Measurements auto-populate.** Aerial report data (EagleView, Hover, Geospan, GAF
   QuickMeasure, RoofSnap) flows in automatically, filling quantities. `[V]`
4. **Line items are grouped by section/trade.** Each line has: **description**, **quantity**,
   **unit**, **waste factor (per line)**, **unit price**, **amount**. Real-time supplier
   pricing can populate material costs. `[V]`
5. **Adjust** — add/delete lines, edit labor lines, set **per-line waste factor**, and set
   margin with the **Profit Margin slider** (drag to a % and all prices adjust automatically).
   `[V]` On mobile you can **group by trade**, update quantities, **calculate taxes**, and
   adjust profit margin & waste. `[V]`
6. **Totals** compute subtotal → margin/markup → tax → total. `[V]`
7. **Convert to Proposal** — a fully **branded, signable proposal** (company logo, customer
   info, scope, totals, terms). `[V]`
8. **Deliver & sign** — send to customer; capture a **digital/e-signature**; offer
   **financing** (AccuFi) and **payments** (AccuPay: credit/debit/ACH). `[V]`

### Margin vs. markup — IMPORTANT difference from our clone

AccuLynx's slider is a **PROFIT MARGIN %** (margin = profit / sell price), not a markup on
cost. Our clone applies **Markup %** (`total = cost × (1+markup)`). To match AccuLynx wording
and math, expose a **"Profit Margin %"** control where `sell = cost / (1 − margin)`. `[V]`

### Example line-item wording (AccuLynx-style, grouped by section) `[V/E]`

AccuLynx estimates group lines under section headers (Tear-Off, Underlayment, Shingles/Field,
Accessories/Flashing, Labor, Permits/Disposal). Representative descriptions:

- *Tear-Off* — "Remove existing roofing down to deck and haul away" (unit **SQ**)
- *Underlayment* — "Synthetic underlayment, full coverage" / "Ice & water shield at eaves &
  valleys" (**SQ** / **LF**)
- *Field* — "Architectural (laminate) shingles — [color]" (**SQ**, with waste %)
- *Accessories* — "Hip & ridge cap shingles" (**LF**), "Starter strip" (**LF**),
  "Pipe boot flashing" (**EA**), "Drip edge / metal edge" (**LF**)
- *Labor* — "Roofing labor — install" (**SQ** or **LS**)
- *Permits/Disposal* — "Building permit & inspections" (**LS**), "Dumpster & disposal" (**LS**)

Units AccuLynx uses: **SQ** (square = 100 sf), **LF**, **EA**, **LS**, **HR**. Our template
descriptions are *more specific* (named products) than AccuLynx's generic stock lines — keep
ours, just adopt the **section grouping** and **per-line waste** + **margin slider**.

---

## 6. Terminology glossary

| AccuLynx term | Meaning | Our clone's word |
|---|---|---|
| **Lead** | Inbound opportunity before it's worked | Lead (match) |
| **Job** | The central record (a lead becomes a job) | Lead **and** Job (we split) |
| **Job File** | The tabbed record workspace for a job | Job detail page (no tabs) |
| **Milestone** | Pipeline stage of a job | "Stage" |
| **Prospect / Approved / Completed / Invoiced / Closed / Dead** | Stock milestones | our custom stages |
| **Estimate** | Customer sell document | Estimate (match) |
| **Proposal** | Branded, signable output of an estimate | "Print / PDF" (we call it estimate print) |
| **Order** (Material Order / Labor Order) | Supplier/crew orders from the estimate | Materials (partial) |
| **Worksheet** (Financial / Invoice Worksheet) | Job-costing & billing ledger | Invoicing (partial) |
| **Amendment** | Change to a worksheet preserving history (change order, discount, supplement…) | none |
| **Profit Margin** | Margin % set via slider | "Markup %" (different math) |
| **Waste factor** | Per-line waste % | none |
| **Supplement** | Insurance scope add-on | none (Permits-only) |
| **Mortgage Check** | Insurance restoration check tracking | none |
| **Pre-Commission / Commission** | Rep payout tracking | none |
| **AccuFi** | Financing offers | none |
| **AccuPay** | Payment processing (credit/debit/ACH) | none |
| **Template Manager** | Where estimate templates live | hardcoded `ESTIMATE_TEMPLATES` |
| **Automation / Workflow Manager** | Trigger-based automations on milestone change | none |
| **Field App** | Mobile app | none |
| **CompanyCam** | Photo capture that syncs to Photos tab | Photos (basic) |
| **ReportsPlus / Data Mart** | Reporting suite | Reports (basic) |
| **Rep / Sales Rep** | Assigned salesperson | "rep" |

---

## 7. Module-by-module UI notes

- **Dashboard** `[V]` — "command center": pipeline snapshot (counts/value per milestone),
  customizable **widgets**, **recent jobs**, bookmarked reports/dashboards refreshable in one
  click. Real-time. Build as a KPI + pipeline-summary + recent-activity layout (our KPI cards
  are close).
- **Leads** `[M]` — list views by sub-stage (Unassigned/Distributed/My Distributed/Assigned),
  "Create Lead" form, assign-to-rep.
- **Jobs / Pipeline** `[V/M]` — primary list filtered by milestone; rows show customer,
  address, value, rep, milestone pill, aging. Opening a job → **Job File** (tabbed).
- **Job File tabs** `[V]` — Overview/Summary · **Messages** (praised; threaded comms,
  email/text replies auto-logged) · **Photos** · **Documents** (Smart(er) Docs) · **Estimates**
  · **Worksheets** · **Orders** · Measurements · Tasks/Notes · Activity feed.
- **Photos** `[V]` — Job Photo Activity page; CompanyCam sync; annotations; image-quality
  setting on upload.
- **Documents** `[V]` — Smart(er) Docs: auto-populate customer info, create/share company &
  customer docs, e-sign.
- **Messages/Comms** `[V]` — in-job messaging + **text messaging**; email replies thread back
  into the job automatically.
- **Calendar / Scheduling** `[V/M]` — calendar with color-coded sidebar, search, advanced
  filters; Production Scheduler for crews; Work Schedule for labor orders/deliveries.
- **Reports** `[V]` — ReportsPlus suite: custom reports + dashboards, favorites/bookmarking,
  Data Mart for raw data.
- **Material / Labor Orders** `[V]` — generated from the estimate; supplier integrations (ABC,
  SRS, QXO); Order Manager + Submitted Orders tracking.
- **Payments / Financing** `[V]` — AccuPay (credit/debit/ACH, disputes tracking) and AccuFi
  (financing offers) surfaced on the proposal and in Tracking.

---

## 8. DISCREPANCY REPORT

| Area | How AccuLynx does it | How our clone does it now | What to change (actionable) |
|---|---|---|---|
| **App color scheme** | Blue/teal in-app workspace; navy+orange brand identity with **orange CTA** | Blue `#2f6df6` + teal-green `#0f9e82` (no orange identity) | Keep blue (already close). Add `--brand-orange #f4811f` + `--brand-navy #173a5b` tokens for logo area & primary CTAs. Nudge success green grassier (`#27ae60`). |
| **Record model** | One record = **Job** that moves through milestones; lead becomes job | Two separate boards: Leads and Jobs with different stage sets | Optionally unify, or at minimum rename top buckets to AccuLynx milestones (Prospect/Approved/Completed/Invoiced/Closed/Dead). |
| **Job detail layout** | **Tabbed Job File** (Overview/Messages/Photos/Documents/Estimates/Worksheets/Orders…) | Single page, two-column, no tabs | Add a tab strip inside job detail with those exact tab names. |
| **Nav information architecture** | Estimates/Permits/Worksheets live **inside the job file** & under **Tracking**, not as top-level modules | Estimates, Invoicing, Permits, Materials are top-level nav items | Move Estimates/Invoicing/Permits/Materials into the Job File as tabs; add a **Tracking** menu group for cross-job lists. Keep them reachable, just re-home. |
| **Estimate margin control** | **Profit Margin %** slider: `sell = cost/(1−margin)`; drag updates all prices | Numeric **Markup %**: `total = cost×(1+markup)` | Rename to "Profit Margin %", switch math to margin, and render as a **slider** (with numeric box) that live-updates totals. |
| **Per-line waste factor** | Each line item has an editable **waste %** | No waste field | Add a `waste_pct` column to `estimate_lines` and the builder; effective qty = qty×(1+waste). |
| **Estimate sectioning** | Line items **grouped by section/trade** (Tear-Off, Underlayment, Shingles, Accessories, Labor, Permits) | Flat list of lines | Add an optional **section/group** label per line and render grouped subtotals. |
| **Estimate → Proposal** | Estimate converts to a **branded signable Proposal** | "Print / PDF" view + canvas e-sign (good!) | Rename output "Proposal"; ensure logo/customer/scope/terms layout. E-sign already present — keep. |
| **Templates** | **Template Manager** UI; templates by trade; user-editable | Hardcoded `ESTIMATE_TEMPLATES` in `constants.py` | Build a Template Manager page (CRUD) backed by DB so users edit templates; seed from current constants. |
| **Worksheets / job costing** | Financial Worksheet per job with **amendments** & real-time profitability | Basic Invoicing module | Add a Financial Worksheet (sections, payments, expenses, amendments, outstanding balance) per job. |
| **Material & Labor Orders** | Orders generated from estimate, sent to suppliers | Materials module (basic) | Add "Generate Order from Estimate" producing a Material Order + Labor Order. |
| **Automations** | **Workflow/Automation Manager** triggered on milestone change | Follow-up clocks only | Add rule builder: on milestone change → task/email/checklist. (Our follow-up clocks are a partial version.) |
| **Tracking hub** | Single Tracking menu (Signatures, Worksheets, Invoices, Orders, Financing, Commissions, Permits, Supplements, Mortgage Checks) | Scattered / missing | Add a **Tracking** nav group aggregating these cross-job lists. |
| **Messages tab** | Threaded in-job messaging; email/SMS replies auto-log | Communications module (separate) | Surface a per-job **Messages** tab; auto-append comms to the job. |
| **Lead sources / work types** | Configurable | Hardcoded lists (good set already) | Fine; consider making editable in Settings to match AccuLynx config. |
| **Draw schedule** | AccuLynx uses **customizable billing schedules** on the Financial Worksheet | Fixed 25/wood/25/40/10 draw schedule | Make draw/billing schedule editable per job (AccuLynx allows custom billing schedules). |

---

## 9. NEEDS LIVE-ACCOUNT CONFIRMATION

Confirm these from a logged-in my.acculynx.com session (screenshots / DevTools):

1. **Exact in-app hex codes** — primary header/nav blue, link blue, success green, amber,
   danger red, background gray, border gray. (All color values above are `[E]` estimates.)
2. **Exact font family** used in the app (and weights).
3. **Exact left-nav order and labels** as they currently render (icons + text) — verify
   against §3 which blends KB + memory.
4. **Job File tab names and order** verbatim (Overview vs Summary; whether it's
   "Worksheets" vs "Financials"; presence of "Orders" tab).
5. **Estimate builder field labels** — confirm the slider literally says "Profit Margin",
   the per-line column literally says "Waste", and the section/group header UX.
6. **Milestone names exactly as configured** for this account (SeaBreeze/REROOF may have
   customized them beyond the stock Prospect/Approved/Completed/Invoiced/Closed/Dead).
7. **Proposal PDF layout** — header/logo placement, signature block, financing/payment
   presentation, terms section.
8. **Whether the marketing orange appears anywhere in-app** (e.g., primary CTA buttons) or
   the app is purely blue/teal.

---

## 10. Sources

- AccuLynx — Best Roofing CRM Software (home; brand colors navy/orange/gray, features, CTAs): https://acculynx.com/ `[V]`
- AccuLynx CRM product page: https://www-acculynx.com/crm/ (403 on fetch; via search) `[V]`
- AccuLynx Features overview (module list): https://acculynx.com/features/ `[V]`
- Roof Estimating Software (templates, measurement auto-populate, profit-margin slider, signable proposals, financing/payments): https://acculynx.com/features/roof-estimating-software/ `[V]`
- The Ultimate Guide to Roof Estimates (estimate contents/sections): https://acculynx.com/roofing-estimate/ `[V]`
- How to Price a Roofing Job (sections: tear-off/underlayment/shingles/accessories/labor/permits/dumpster; waste, profit margin): https://acculynx.com/how-to-price-a-roofing-job/ `[V]`
- 3 Tricks for Faster Estimates (Template Manager; templates by trade; build steps): https://acculynx.com/tricks-for-faster-estimates/ `[V]`
- The Ultimate Guide to Financial Worksheets (worksheet vs estimate/order; amendments; profitability; one-per-job): https://acculynx.com/guide-to-financial-worksheets-in-acculynx/ `[V]`
- Workflow Manager & New Financial Worksheets (milestones Prospect→Approved, automations): https://acculynx.com/workflow-manager-and-new-financial-worksheets/ `[V]`
- The Top 14 Things Contractors Can Track (Tracking categories; milestone words approved/completed/invoiced; AccuFi/AccuPay/commissions/permits/supplements/mortgage checks): https://acculynx.com/the-top-14-things-contractors-can-track-using-acculynx/ `[V]`
- Navigating the AccuLynx Dashboard (KB; dashboard widgets, nav, Photos page): https://support.acculynx.com/hc/en-us/articles/21963918948621-Navigating-The-AccuLynx-Dashboard `[V]`
- Account Settings (KB; Job File/Estimate/Worksheet settings, Pipeline Overview, milestones, user roles): https://support.acculynx.com/hc/en-us/articles/7791523475469-Account-Settings `[V]`
- ReportsPlus launch (reports/dashboards/bookmarking): https://acculynx.com/product-release-reportsplus/ `[V]`
- New AccuLynx Field App (mobile dashboard, recent jobs, color-coded calendar): https://acculynx.com/new-acculynx-field-app/ `[V]`
- WG Restoration AccuLynx training (lead/job workflow, Documents/Measurements/Notes/Create Invoice tabs): https://training.wgrestoration.com/pages/acculynx `[V]`
- Capterra reviews (job file tabs, Messages tab praised, navigable stages/tabs, central file system): https://www.capterra.com/p/116187/Acculynx/reviews/ `[V]`
- G2 reviews (dashboard snapshot, ease of navigation): https://www.g2.com/products/acculynx/reviews `[V]`
- Project memory — verified AccuLynx URLs (milestone filter URLs, Tracking submenu, Template Manager, Production scheduler/order-manager): `memory/reference_acculynx_urls.md` `[M]`
- Project memory — verified estimate templates (2025 template names by roofing system): `memory/reference_estimate_templates.md` `[M]`
