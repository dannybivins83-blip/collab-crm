# AccuLynx — LIVE-ACCOUNT CAPTURE (verified)

Captured directly from the logged-in **my.acculynx.com** account (SeaBreeze
Roofing REROOF Department) via the browser on 2026-06-07. These are **verified
real values** — they supersede the estimated values in `ACCULYNX_SPEC.md` wherever
the two disagree. Tag: `[LIVE]` = read from the live DOM.

---

## 1. Brand color palette — EXACT (in-app) `[LIVE]`

| Token | Hex | RGB | Where used |
|-------|-----|-----|------------|
| Masthead / top bar | `#24476C` | 36,71,108 | top navy bar (company/department picker, user menu) |
| Primary nav bar | `#4680BF` | 70,128,191 | second nav row (New/Dashboard/Contacts/Leads/Jobs… icons) + nav icon color |
| CTA / Help orange | `#F5863B` | 245,134,59 | Help button; AccuLynx's signature orange accent |
| Body text | `#313131` | 49,49,49 | default text |
| Muted text | `#4D4D4D` | 77,77,77 | secondary labels |
| Input background | `#EFEFEF` | 239,239,239 | search + form inputs |
| Page background | `#FFFFFF` / very light gray | — | white panels on light gray |

### Milestone (pipeline) colors — EXACT `[LIVE]`
These are the L/P/A/C/I circles on the dashboard + job milestone tracker:

| Letter | Milestone | Hex | RGB |
|--------|-----------|-----|-----|
| **L** | Lead | `#F2C000` | 242,192,0 (gold) |
| **P** | Prospect | `#F78300` | 247,131,0 (orange) |
| **A** | Approved | `#8CC63F` | 140,198,63 (green) |
| **C** | Completed | `#29ABE2` | 41,171,226 (sky blue) |
| **I** | Invoiced | `#E25050` | 226,80,80 (red) |

> Correction to spec: the research agent estimated nav blue ≈ `#2c7be5`. The real
> in-app values are masthead `#24476C` + nav `#4680BF`. Use these.

### Typography `[LIVE]`
- Font family: **`Roboto, sans-serif`** (everywhere). Not Open Sans.
- Body 12–13px; nav icons ~13px; weights 400 normal.

### Drop-in CSS variables (matches AccuLynx exactly)
```css
:root{
  --masthead:#24476C;     /* top navy bar            */
  --accent:#4680BF;       /* primary blue nav/links  */
  --accent2:#8CC63F;      /* approved/success green  */
  --brand-orange:#F5863B; /* CTA / Help / accent     */
  --warn:#F78300;         /* prospect orange         */
  --danger:#E25050;       /* invoiced/overdue red    */
  --info:#29ABE2;         /* completed blue          */
  --gold:#F2C000;         /* lead gold               */
  --txt:#313131; --mut:#4D4D4D; --input:#EFEFEF;
  --font:'Roboto',-apple-system,Segoe UI,sans-serif;
}
```

---

## 2. Global navigation / sitemap — EXACT labels & order `[LIVE]`

**Top bar (masthead, `#24476C`):** AccuLynx logo · **[Department picker ▾]** ("SeaBreeze
Roofing REROOF Department") · Release Notes · 🔖 (pin, badge) · 📅 calendar · 🔔
alerts · **@Me** (badge) · **[User name] ⚙**

**Primary nav (`#4680BF`):** New · Recent · Dashboard · Contacts · Leads · Jobs ·
Photos · Track · Reports · Production · Tools · Market · **[Search: "Job #, Customer
Name or Address"]** · Help (orange)

**Jobs menu dropdown:** Prospects · Approved · Completed · Invoiced · Closed · Canceled

---

## 3. Pipeline milestones — EXACT (this account) `[LIVE]`

Top-level: **Lead → Prospect → Approved → Completed → Invoiced → Closed** (+ Canceled).
Lead sub-stages: **Assigned · Prospect · Negotiation · Long Term**

Full "Milestone Pipeline" filter list as configured for SeaBreeze (in order):
1. Assigned
2. Prospect
3. Approved
4. IF NEEDED - Finance NTP
5. Documentation Needed
6. Permit Applied For
7. Permit Approved
8. Pre Con Needed
9. Pre Con Complete
10. Ready To Schedule Tear Off
11. Tear Off Started
12. Tear Off Completed
13. Roof Install Started
14. Roof Install Completed
15. Punch Out Needed
16. Punch Out Completed
17. Needs Final Inspection
18. Final Inspection Scheduled
19. Final Inspection Passed
20. Completed
21. Invoiced
22. Closed
23. Canceled

> These are the **exact production sub-milestones** to mirror in our jobs pipeline.
> Our current clone stages (approved/docs/hoa/permit_sub/permit_app/precon/started/
> final/closed) are close but should be expanded/renamed to match these.

---

## 4. Job File layout — EXACT `[LIVE]`

A job opens as a single workspace (URL `/jobs/<guid>`), with tabs **Overview** +
contextual tabs (e.g. **Estimates**), and a **JOB MENU** (hamburger) top-right.

**Header row:** ‹ back · **[milestone badge]** · `R-#####: <Customer> (codes)` ·
address link · **Job Priority: Normal ▾** · **$Total / Balance Due $X / A/R-DETAILS**
· **[% complete ring]** · **[Assigned rep ▾]**

**Milestones panel:** `STATUS: <current>` · `NEXT STEP: <next milestone>` ·
**[Advance Job]** (green) · visual tracker L—P—A—C—I—Closed with **dates + days
between** each.

**Job Activity tabs (counts shown):** **Communications · Estimates · Worksheet ·
Invoices · Orders · Photos · Documents**

---

## 5. Estimating — EXACT structure `[LIVE]` (the priority area)

Path: open a Job → **Estimates** tab.

**Header:** "Estimates" · **[Actions ▾]** · **[New Estimate]** (blue). Columns: **Cost**
| **Price**.

**Hierarchy:**
```
Estimate  (badge "Primary/A", named by property, e.g. "10 Islington")   ⋯ menu
  └─ Section            e.g. "Roofing - Shingle Dry In Section"     Cost | Price
       └─ Slope Section (sub-section)                               Cost | Price
            └─ Scope-of-work NARRATIVE (lettered a., b., c. … clauses)
  ── Section Total ──                                          $9,760.79 | $14,000.00
ESTIMATE TOTAL (green bar)                                     $9,760.79 | $14,000.00
  Taxes & Discounts:  Taxes $0.00 · Discounts $0.00
  Profit:  Margin 29.88% – 30.28% · Net Profit $4,183.21 – $4,239.21
  footer: "↑ = Cost of Materials are rounded up to the full order quantity."
```

### KEY INSIGHT — estimates are narrative scope, not bare line items
Each section carries a **full written scope-of-work** in lettered clauses. This is
the customer-facing proposal language. The real SeaBreeze **Shingle Dry In** scope
(verbatim, captured live) is:

> Shingle Roof: **a.** Remove existing materials on sloped roof(s) and haul away to
> licensed land fill. **b.** Remove and replace decayed decking and fascia in
> accordance with unit prices listed on Wood Replacement sheet. Painting of fascia
> to be done by others. This proposal includes 25 LF of fascia and (3) three sheets
> of plywood. **c.** Fasten decking in accordance with Florida Building Code. **d.**
> Furnish and install Self Adhered membrane (Polyglass IR-XE or equivalent) to roof
> deck in accordance with current Florida Building Code. This qualifies as your
> Secondary Water Barrier. **e.** Furnish and install new 26-gauge galvanized valley
> metal. Metal will be nailed at 4" on center. **f.** Furnish and install new
> 26-gauge, 3"x 3" galvanized drip edge. Prefinished white. **g.** Furnish and
> install new lead plumbing stacks, set in roof cement and fastened to plywood
> decking, folded over outside of pipes to prevent water intrusion. **h.** Furnish
> and install [shingles] (CertainTeed) per manufacturer's recommendations. Shingles
> to be nailed with 6 nails each. **i.** Standard Color to be selected by owner.

### Pricing model — MARGIN, not markup `[LIVE]`
- Columns are **Cost** and **Price**. The spread is expressed as **Margin %** and
  **Net Profit $** (shown as a *range* because material cost rounds up to full order
  quantity). So `Price = Cost / (1 − margin)`, NOT `Cost × (1 + markup)`.
- Concepts present: Sections by trade, Slope sub-sections, narrative scope, Cost vs
  Price, Taxes, Discounts, Margin %, Net Profit.

---

## 6. Terminology glossary `[LIVE]`
- **Lead / Prospect / Approved / Completed / Invoiced / Closed** — pipeline milestones
- **Job File** — the per-job workspace
- **Milestone** / **Advance Job** / **NEXT STEP** — stage progression
- **Estimate → Section → Slope Section** — estimate hierarchy
- **Worksheet** — job-costing financial worksheet (separate tab)
- **Order** (Material/Labor Orders) — generated from estimate
- **A/R-DETAILS** / **Balance Due** — receivables
- **R-#####** — job reference number (e.g. R-26058)

---

## 7b. Estimate EDITOR + Proposal — second live capture `[LIVE]`

**Estimate menu (⋯):** Edit Estimate · Duplicate · View Log · Preview · Delete.

**Editor layout** (Edit Estimate). Column order: **Unit | Cost/Unit | Cost | Price**.
- Estimate level: "<Property> #1" with rolled-up Cost and an editable green **Price** box; an "Add Estimate Description" link.
- **Section** (drag-handle ⠿, ⋯ menu) → **Slope Section** (sub-section, delete icon) → scope narrative → **product line items**.
- Each **line row**: ⠿ drag · Product name · 🗑 · **Qty** (editable) · **Unit/UOM** (dropdown: BX, RL, SQ…) · **Cost/Unit** · **Cost** (=Qty×Cost/Unit) · **Price** (editable, margin applied) · ⋯.
  - Real lines seen: "8d Nails (BX) · 0.92 · BX · $69.66 · $64.09 · $91.56"; "SDI - Polystick IR-Xe (1.8sq/roll) · 19.07 · RL · $58.43 · $1,114.26 · $1,591.80". Products come from a **catalog**.
- Bottom toolbar: **Quit · Save · Preview · Add Section**. Quit prompts "Save before quitting?" → CANCEL / QUIT NOW / SAVE & QUIT.
- Verified math: 25 SQ @ $390.43 cost ÷ (1−0.3028 margin) = **$14,000** price. Our builder reproduces this exactly.

**Proposal (Preview)** — print-options panel on the left (toggle what prints):
- Job Name · Title · ( ) Section Description ONLY / (•) **Show Line Items** → Section Description · Collapse Groups · Show Descriptions · Quantities/Units · Per Unit Charge · Line Item Price · Section Totals · **Estimate Totals** → Sub Total · Tax.
- Letterhead: **logo (top-left)** + company block (name, address, license "CCC-1328689", phone) · **Company Representative** block (rep name, phone, email) · date top-right · customer block + "Job: R-#####" · gray section header bars · scope narrative · totals. Bottom: Back · **Actions** (print/email/PDF).

**Real SeaBreeze contact (from letterhead):** phone **(561) 292-3457**, rep Danny Bivins (561) 970-9627, **dannyb@seabreezeroof.com**. (Applied to company settings.)

## 7. Still not captured (would require more clicks / risk creating records)
- The **New Estimate** builder's per-line edit fields (Waste %, quantity, product
  picker) — avoided to not create a junk estimate on a live job.
- The **Worksheet** tab internals.
- The **Proposal PDF** output layout.
- Production / Scheduling / Reports module internals.

(Next live session can capture these read-only on an existing estimate's edit view.)
