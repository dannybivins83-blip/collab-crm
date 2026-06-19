# AccuLynx → White-Label CRM Migration Map
**Generated 2026-06-19 by 27-agent mapping workflow**
**242 fields mapped across 6 entity types | 129 unique mappings documented**

---

## Status Legend
- ✅ **synced** — flowing live via API or confirmed import path (48 fields)
- ⚠️ **partial** — mapped but incomplete; collector not run to completion (39 fields)
- ❌ **not-synced** — code-complete endpoint; collector never ran (19 fields)
- 🚫 **no-equivalent** — AccuLynx has it; no CRM column/table exists (23 fields)
- 🔒 **api-unavailable** — not exposed in AccuLynx public REST API

---

## Current Database Inventory (Render Production)

| Table | Count | Notes |
|-------|-------|-------|
| jobs | 1,231 | 1,142 at $0 contract value |
| contacts | 1,952 | |
| job_documents | 3,013 | ~900 un-mirrored from AccuLynx |
| invoices | 429 | 0 marked paid |
| payments | 178 | $1.008M unlinked to invoices |
| measurements | 0 | 847 Roof Report PDFs never pulled |
| job_stage_history | 1,698 | From last CSV import run |
| job_photos | 2 | bmphotos never run |

---

## JOB ENTITY — Field Map

| AccuLynx Field | AccuLynx Path | CRM Table | CRM Column | Sync Method | Status | Notes |
|----------------|--------------|-----------|-----------|-------------|--------|-------|
| Job GUID (id) | `GET /jobs → id` | jobs | external_url | api-live | ✅ | GUID embedded in URL, extracted via regex. No dedicated column. |
| jobName / displayName | `GET /jobs → jobName` | jobs | name | api-live | ✅ | SeaBreeze format: R-YY###: Client (AHJ) (Code+Sq) (Rep) |
| jobNumber / refNumber | `GET /jobs → jobNumber` | jobs | rid | api-live | ✅ | E.g. R-25179 |
| currentMilestone | `GET /jobs → currentMilestone` | jobs | stage | api-live | ✅ | _resolve_stage() maps to 21 CRM stages |
| milestoneDate | `GET /jobs → milestoneDate` | jobs | stage_since | api-live | ⚠️ | Exact date stored in todo text; stage_since = sync date |
| workType / tradeTypes[] | `GET /jobs/{guid} → workType` | jobs | work_type | api-live | ✅ | Derives department + system code |
| leadSource / source | `GET /jobs → leadSource` | jobs | source | api-live | ✅ | Also from CSV Sales Report |
| salesRep / assignedTo | `GET /jobs → salesRep` | jobs | rep | api-live | ✅ | Defaults to 'Danny Bivins' when blank |
| locationAddress.street1 | `GET /jobs/{guid}` | jobs | address | api-live | ✅ | Detail endpoint required |
| locationAddress.city | `GET /jobs/{guid}` | jobs | city | api-live | ✅ | AHJ inferred from city |
| locationAddress.state | `GET /jobs/{guid}` | jobs | state | api-live | ✅ | _name_of() extracts abbreviation |
| locationAddress.zipCode | `GET /jobs/{guid}` | jobs | zip | api-live | ✅ | |
| contact.firstName+lastName | `GET /jobs → contacts[isPrimary]` | jobs | name | api-live | ✅ | |
| contact.phoneNumbers[0] | `GET /jobs → contacts[isPrimary]` | jobs | phone | api-live | ✅ | |
| contact.emailAddresses[0] | `GET /jobs → contacts[isPrimary]` | jobs | email | api-live | ✅ | |
| contact.id (GUID) | CSV Contacts Report only | contacts | ext_id | csv-import | ⚠️ | API sync does not store AccuLynx contact GUID |
| contact FK | Resolved from contacts table | jobs | contact_id | api-live | ✅ | _ensure_contact() inserts/looks up |
| **contractValue / jobValue** | **Browser-bridge bmbillwalk** | **jobs** | **contract_value** | **browser-bridge** | **❌** | **1,142 of 1,231 jobs = $0 — bmbillwalk never run to completion** |
| balanceDue / arBalance | Browser-bridge bmbillwalk | jobs | balance | browser-bridge | ⚠️ | |
| paymentsReceived / collected | Browser-bridge bmbillwalk | jobs | collected | browser-bridge | ⚠️ | Derived as contract_value - balance |
| Invoice (id, number, amount) | Browser-bridge bmbillwalk | invoices | number, amount, ext_id | browser-bridge | ⚠️ | 0 invoices marked paid despite $1.008M in payments |
| Payment (id, amount, method) | Browser-bridge bmbillwalk | payments | amount, method, ext_id | browser-bridge | ⚠️ | 178 rows, not linked to invoices |
| Worksheet (lines, contract) | Browser-bridge bmworksheet | worksheets | contract_value, lines | browser-bridge | ❌ | Code-complete, never run |
| Estimate sections + lines | Browser-bridge bmestlines | estimate_sections/lines | * | browser-bridge | ⚠️ | Partial |
| Roof Report PDFs | Browser-bridge bmroof | measurements | * | browser-bridge | ❌ | 0 of ~847 pulled |
| All documents | Browser-bridge bmalldocs | job_documents | * + Drive | browser-bridge | ⚠️ | 146 of 1,046+ |
| Photos | Browser-bridge bmphotos | job_photos | * | browser-bridge | ❌ | 2 rows only |
| InsuranceClaimNumber | 🔒 Not in public API | — | — | — | 🚫 | Custom browser-bridge collector needed |
| AdjusterName | 🔒 Not in public API | — | — | — | 🚫 | |
| InsuranceCompany | 🔒 Not in public API | — | — | — | 🚫 | |
| DeductibleAmount | 🔒 Not in public API | — | — | — | 🚫 | |
| SoldDate / ContractDate | 🔒 Not in public API | — | — | — | 🚫 | Approximated via stage_since |
| StartDate / CompletionDate | 🔒 Not in public API | — | — | — | 🚫 | Inferred from job_stage_history |
| ProjectManagerId | 🔒 Not in public API | — | — | — | 🚫 | |
| CrewId / Crew assignment | 🔒 Not in public API | — | — | — | 🚫 | Production Scheduler module |
| CommissionAmount / PreComm | 🔒 Not in public API | — | — | — | 🚫 | |
| Tags | Not mapped | — | — | — | 🚫 | No CRM equivalent |
| Job CustomFields | Not mapped | — | — | — | 🚫 | |

---

## CONTACT ENTITY — Field Map

| AccuLynx Field | AccuLynx Path | CRM Table | CRM Column | Status | Notes |
|----------------|--------------|-----------|-----------|--------|-------|
| ContactId (GUID) | CSV Contacts Report | contacts | ext_id | ⚠️ | CSV-only; API sync does not carry GUID |
| firstName / lastName | `GET /jobs → contacts[isPrimary]` | contacts | name | ✅ | |
| emailAddresses[0] | `GET /jobs → contacts[isPrimary]` | contacts | email | ✅ | |
| phoneNumbers[0] (primary) | `GET /jobs → contacts[isPrimary]` | contacts | phone | ✅ | |
| phoneNumbers[1] (cell/mobile) | Not extracted | — | — | 🚫 | Second phone silently lost |
| address / mailingAddress | CSV Contacts Report | contacts | address,city,state,zip | ⚠️ | API gives location via job; mailing via CSV |
| ContactType | Not mapped | — | — | 🚫 | |
| LeadSource | CSV Contacts Report | contacts | source | ⚠️ | CSV only |
| Tags / CustomFields | Not mapped | — | — | 🚫 | |
| isPrimary flag | Not stored | — | — | 🚫 | |
| RelatedJobs count | Implicit via jobs.contact_id | — | — | ✅ | Derivable via FK |
| is_gc (GC flag) | CRM-only field | contacts | is_gc | N/A | CRM extension, no AccuLynx equivalent |
| GC parent relationship | CRM-only | contacts | gc_contact_id | N/A | CRM extension |

---

## DOCUMENTS — Field Map

| Doc Type | AccuLynx Folder | CRM Storage | Bookmarklet | Status | Count Est. |
|----------|----------------|-------------|------------|--------|-----------|
| Roof Report PDF | "Roof Report" folder | measurements + Drive | bmroof | ❌ | ~847 |
| Contract / Proposal | Contracts folder | job_documents + Drive | bmalldocs | ⚠️ | ~1,046 |
| Permit docs (NOC, card, approval) | Permit Documents | job_documents + Drive | bmalldocs | ⚠️ | ~1,252 |
| HOA Documents | HOA folder | job_documents + Drive | bmalldocs | ⚠️ | ~541 |
| Notice of Commencement | NOC folder | job_documents + Drive | bmalldocs | ⚠️ | ~303 |
| Insurance Documents | Insurance folder | job_documents + Drive | bmalldocs | ⚠️ | unknown |
| Supplement Docs | Supplement folder | job_documents + Drive | bmalldocs | ⚠️ | unknown |
| Warranty | Warranty folder | job_documents + Drive | bmalldocs | ⚠️ | unknown |
| Inspection Reports | Inspection folder | job_documents + Drive | bmalldocs | ⚠️ | unknown |
| Job Photos | Photos section | job_photos + Drive | bmphotos | ❌ | ~0 pulled |
| Communications / Messages | Activity Feed | comms table | bmcomm | ❌ | optional |

**⚠️ AUTOMATION IS NOT POSSIBLE.** AccuLynx documents API returns 404. All doc pulls require a human logged into my.acculynx.com running bookmarklets in 20-job batches.

---

## FINANCIAL ENTITY — Field Map

| AccuLynx Field | Method | CRM Table.Column | Status |
|----------------|--------|-----------------|--------|
| contractValue | bmbillwalk | jobs.contract_value | ❌ 93% missing |
| balanceDue | bmbillwalk | jobs.balance | ❌ |
| paymentsReceived | bmbillwalk | jobs.collected | ❌ |
| Invoice number/amount/date | bmbillwalk | invoices.* | ⚠️ partial |
| Invoice status (paid/unpaid) | Payment reconciliation SQL | invoices.status | ❌ SQL not run |
| Payment amount/method/date | bmbillwalk | payments.* | ⚠️ partial |
| Estimate header (total) | bmestlines | estimates.total | ⚠️ partial |
| Estimate line items | bmestlines | estimate_lines.* | ⚠️ partial |
| Estimate waste_pct per line | bmestlines | estimate_lines.waste_pct | ❌ defaults to 0 |
| Estimate margin_pct | bmestlines | estimate_lines.margin_pct | ❌ defaults to 30 |
| Worksheet lines | bmworksheet | worksheet_lines.* | ❌ never run |
| Expense category/amount/vendor | CSV import_job_expenses.py | job_expenses.* | ⚠️ 1,322 rows pending push |
| Commissions | 🔒 Browser-only | commissions.* | 🚫 no sync path |
| Order Manager / POs | 🔒 Browser-only | — | 🚫 no bookmarklet built |

---

## MIGRATION PHASES

| # | Phase | Method | Effort | Blocker |
|---|-------|--------|--------|---------|
| 1 | **Reinstall bookmarklets** (5 min) | manual | LOW | None — do first |
| 2 | **Financial parity via bmbillwalk** | browser-bridge | HIGH | Phase 1 |
| 3 | **Payment reconciliation SQL** | script | LOW | Phase 2 |
| 4 | **Document sync via bmalldocs** | browser-bridge | HIGH | Phase 1 |
| 5 | **Roof Reports via bmroof** | browser-bridge | HIGH | Phase 1 (run with Phase 4) |
| 6 | **Worksheet sync via bmworksheet** | browser-bridge | MEDIUM | Phase 2 |
| 7 | **CSV backfill** (contacts, sales, workflow) | csv-import | MEDIUM | None |
| 8 | **Closed/canceled jobs backfill** (~7,989 jobs) | script | MEDIUM | API key |
| 9 | **AHJ/system audit + stage-since backfill** | script | LOW | Phase 7 |
| 10 | **Insurance fields schema + collector** | browser-bridge | MEDIUM | Phase 1 |
| 11 | **Photos + comms** | browser-bridge | MEDIUM | Phase 4 |
| 12 | **Parity verification** | manual | LOW | All prior |

---

## P1 GAPS — Must Fix

| Entity | Field | Reason | Priority |
|--------|-------|--------|---------|
| Job | InsuranceClaimNumber | 🔒 Not in public API | P1 |
| Job | AdjusterName | 🔒 Not in public API | P1 |
| Job | InsuranceCompany | 🔒 Not in public API | P1 |
| Job | DeductibleAmount | 🔒 Not in public API | P1 |
| Job | contract_value (1,142 jobs at $0) | bmbillwalk never run | P1 |
| Job | Invoice.status (0 paid) | Reconciliation SQL never run | P1 |
| Job | Order Manager / POs | No bookmarklet built | P1 |
| Financial | Worksheet sync | Code-complete, never executed | P1 |
| Documents | Roof Report PDFs → measurements | bmroof never run | P1 |

---

## QUICK WINS (start today)

1. **Reinstall all 7 bookmarklets** from CRM `/sync` tab — 5 min
2. **Run `bmbillwalk`** — 20-job increments until all 1,231 done — recovers contract values for 93% of jobs
3. **Run payment reconciliation SQL** — links $1.008M to invoices
4. **Run `bmroof` in parallel** (second tab) — 847 PDFs, 0 pulled
5. **Export fresh CSVs** (Contacts + Sales) → run `sync_csv_reports.py`
6. **Run `import_workflow_status.py`** → restores 1,698-row stage history
7. **Run `bmalldocs`** → closes the ~900 un-mirrored document gap
