# AccuLynx Knowledge Base — Complete Map & Learnings

Source: support.acculynx.com Help Center, pulled via its Zendesk Help Center API
(11 categories, 50 sections, 307 articles), captured 2026-06-07 from Danny's logged-in
session. The article bodies are mostly embedded training videos, so the **taxonomy +
article titles are the actionable signal** — they enumerate AccuLynx's complete feature
set. This doc is the reference for what the clone should mirror.

## The 11 categories

1. Administrator Training Path — video course (17 lessons)
2. Manager Training Path — video course (14)
3. Office Training Path — video course
4. Sales Training Path — video course
5. AccuLynx In 5 — 14 five-minute quick-start videos
6. Weekly Live Webinars — trainer-led enrollment
7. **Leads & Jobs** — core CRM/pipeline docs
8. **Estimating & Ordering** — estimates, orders, scheduler
9. **Administrative Settings & Tools** — admin/config
10. **QuickBooks Integration**
11. **Add-Ons & Integrations** — AppConnections, measurement providers, AccuFi, AccuPay

The "AccuLynx In 5" quick-start curriculum is the cleanest summary of the product's spine:
> Dashboard → Create a Lead & advance through **Job Milestones** → Job Overview (Messaging,
> Photos, Documents) → **Measurements** (enter / upload / order) → build **Estimate & Order
> Templates** → create & share **Estimates** → order **Materials & Labor** → create a
> **Worksheet to set the job value** → **Invoices & Payments** → **Workflow Manager** →
> **Commissions** (Pre-Commission → Commission) → **Field App**.

---

## Category 7 — Leads & Jobs (the CRM core)

- **Creating & Assigning Leads**: How to Create a Lead · Assign Leads · View Assigned Leads
- **Working in a Lead/Job**: Job Overview · **Lead Intelligence – Ranking** (lead scoring; the
  green "Lead Rank" bars we saw live) · Submitting a Job for Approval · **Profit Analysis**
- **Managing Unassigned Leads**: Unassigned Leads · My Distributed Leads · Distributed Leads
  (lead-distribution queue to reps)
- **Reassigning Leads and Jobs**: single + **bulk** reassignment
- **Lead Creation Tools**: **Web-to-Lead Form** · API Keys · **Zapier** Zap
- **Jobs**: Reopening Dead/Cancelled Jobs · Unlinking a job from wrong primary contact ·
  Edit Job Progress · **Approving Jobs** · Manually Entering Payments & Expenses · Delete a
  Job · **Change History** · Additional Jobs for an existing customer/contact · Edit Job Name ·
  **Create Job Permits** · Export History & Comments · Jobs Menu · Manage Users

Key concepts: a record is a **Job** from lead stage onward; it advances through **Milestones**
(the L/P/A/C/I buckets we already mirror). Leads can be **unassigned → distributed → assigned**.
**Lead Ranking** scores lead quality. **Profit Analysis** shows job profitability.

## Category 8 — Estimating & Ordering

- **Admin – Estimate Settings**: Tax Rates · Disclaimer · Settings
- **Supplier Integrations**: QXO · SRS · ABC Supply · Beacon (order materials directly to a distributor)
- **Creating & Editing Estimates**: Mobile Estimating · Estimate Updates · **Copying Estimates
  to another Job** · Advanced Estimating Tips
- **Creating Orders**: Editing PO Numbers · **Orders** (material + labor POs generated from the estimate)
- **Order Manager**: central queue of all material/labor orders across jobs
- **Scheduler**: Overview · **Timeline** view · Accessing Order Info · **Scheduling Orders**
  (production scheduling of crews/deliveries)
- **Job Measurements & Writing Estimates**: **Units of Measure – Defined** (SQ, LF, EA, etc.)

Key flow: **Estimate & Order Templates** → estimate → **Orders** (materials to a supplier +
labor) → **Order Manager** → **Scheduler**. The estimate drives purchase orders; a separate
**Worksheet** sets the actual job value/budget (see AccuLynx In 5).

## Category 9 — Administrative Settings & Tools (white-label/config)

- **Custom Fields**: create/manage · place into AccuLynx · in Automations · in Reporting
- **Administrative Setup**: Security & 2FA · **Updating Your Logo** · **Permission Settings**
  (roles) · General Job Costs/Expenses · Add/Edit **Vendors** · Dashboard Controls · **Contact
  Types** · **Lead Source** · **Dead Lead Settings** · Import/Export Contacts & Leads ·
  **Required Lead Form Fields** · Managing & Inviting Users · Track **Insurance Companies** ·
  Purchase Order Header · **Manage All Locations** · **Parent Company Information** · Payment
  Info · Billing History · Legacy Features
- **User Profile Setup**: iCal calendar · mobile shortcut

This is the white-label/admin surface — much of which our Settings already covers (logo, colors,
license, company info), but AccuLynx adds: custom fields, permission/roles, vendors, configurable
contact types & lead sources, required-field rules, multi-location, parent company.

## Category 10 — QuickBooks Integration

QuickBooks Online (Plus/Advanced) + Desktop two-way sync: accounts mapping, settings, invoice/
payment/job sync, troubleshooting. (Accounting export — out of scope for an offline clone, but
a CSV export to QuickBooks is the lightweight equivalent.)

## Category 11 — Add-Ons & Integrations

- **AppConnections**: RoofScope · CallRail · RoofSnap · RoofQuotePRO · HubSpot · Angi ·
  **CompanyCam** (photos) · Hatch · **Hover** · SalesRabbit · Spotio · Zapier
- **Measurement providers**: Geospan · **GAF QuickMeasure** · **EagleView** · Storm Analysis
  (this is the measurement-ordering layer — our RoofGraf upload + parse is the equivalent)
- **AccuFi**: homeowner **financing** offers
- **AccuPay**: integrated **card/ACH payments** (apply, refunds)
- Smart(er) Docs, Supplementing (insurance), Mobile Communications / Call Logging

---

## Gap analysis — AccuLynx vs our clone

| AccuLynx feature | In our clone? | Recommendation |
|---|---|---|
| Leads → Milestones pipeline (L/P/A/C/I) | ✅ Yes | matches |
| Contacts + activity timeline | ✅ Yes | matches |
| Measurements (enter/upload/**parse**) | ✅ Yes (RoofGraf auto-fill) | matches; could add "order measurement" |
| Estimate & Order **Templates** | ✅ Yes (Template Manager) | matches |
| Estimates (sections, margin, e-sign, PDF) | ✅ Yes | matches |
| Permits | ✅ Yes (+ packet builder, beyond AccuLynx) | exceeds |
| Photos / Documents per job | ✅ Yes | matches |
| Invoicing + draw schedule | ✅ Basic | matches core |
| Departments picker | ✅ Yes | matches |
| **Worksheet** (set job value / job costing budget vs actual) | ❌ No | **add** — core AccuLynx concept |
| **Orders** (material + labor POs from the estimate) | ⚠️ Materials only, basic | expand to true POs w/ PO #, vendor |
| **Order Manager** (cross-job order queue) | ❌ No | add a queue view over materials/orders |
| **Scheduler** (crew/delivery scheduling, timeline) | ⚠️ Calendar only | add a production scheduler/timeline |
| **Commissions** (pre-commission → commission) | ❌ No | add a commissions module (sales rep payouts) |
| **Automations / Workflow Manager** (auto tasks/emails on milestone) | ⚠️ Follow-up clocks only | add rule engine: on milestone → create task/draft email |
| **Custom Fields** | ❌ No | add admin-defined custom fields on lead/job |
| **Lead Intelligence – Ranking** | ❌ No | add a lead-rank/score field + bars |
| **Profit Analysis** per job | ❌ No | add (contract value − costs = profit) |
| **Permission Settings / Roles** | ⚠️ Users table only | enforce role-based access |
| Configurable **Contact Types / Lead Sources / Dead-Lead** | ⚠️ Hardcoded | move to settings |
| **Vendors** / Insurance Companies / Locations | ❌ No | add vendor + insurance + multi-location |
| Financing (**AccuFi**) / **AccuPay** | ❌ No (compliance-sensitive) | leave to user; payments are a prohibited action |
| QuickBooks / supplier / measurement integrations | ❌ No | offline clone: CSV export equivalents |

### Highest-value additions to mirror AccuLynx next
1. **Worksheet** — set/track job value & costs (budget vs actual) → feeds Profit Analysis.
2. **Orders** — real material + labor purchase orders off the estimate, with PO #, vendor, Order Manager queue.
3. **Automations / Workflow Manager** — on milestone change, auto-create the stage's tasks and draft the follow-up email (we already draft follow-ups; formalize as rules).
4. **Commissions** — sales-rep commission tracking (pre-commission → commission).
5. **Custom Fields** + **Lead Ranking** + configurable Contact Types / Lead Sources in Settings.

### Terminology to match (from the KB)
Job (record from lead onward) · Milestone (stage) · Job Overview · Worksheet · Order /
Order Manager · Scheduler · Pre-Commission / Commission · Workflow Manager (automations) ·
Lead Rank · Profit Analysis · Distributed / Unassigned / Assigned Leads · AppConnections ·
Units of Measure (SQ/LF/EA) · Field App (mobile).
