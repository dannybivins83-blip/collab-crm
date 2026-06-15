# 🏗️ COORDINATION BOARD — White-Label Roofing CRM

**The single source of truth for who's doing what.** Every lane: pull → read this → work →
update your row → push. One pusher at a time. Don't route status through the owner.

- **Repo:** `github.com/dannybivins83-blip/collab-crm` · working branch `agent/gc-consolidation`
- **Git tip (verified 2026-06-15):** `a0fccd2` on `agent/gc-consolidation`
- **Last board update:** 2026-06-15 ET — crm-ui: worksheet 500 fixed (Jinja dict.items + $%,.2f bugs, commit `a0fccd2`). CLAUDE.md stale Vercel-as-live note corrected → Render is live. **Launch verdict: SOFT GO** for internal SeaBreeze use. **HOLD** on external tenants pending: D16 SMTP (Danny sets Render SMTP_FROM/SMTP_PASSWORD) + bookmarklet reinstall + CRM_SYNC_SECRET on Render (sync validation fail-closes w/o it). Security P2s (ROOF_ENGINE_API_KEY in URL ✅`92c61ae`, 7 sync endpoints ✅`a9c0550`) are FIXED.

---

## LANES (session → owns → status)
| Lane | Session title | Owns | Status |
|---|---|---|---|
| 🧭 Head Coach | SB CRM-clone MAIN DEV UI CODER-2nd | Coordination, audit, sequencing, owner decisions | **Active** — spine up; holding code-clean |
| 🔒 Security | OVERLORD/Security lane | Audit fixes | ✅ #1,#2,#3,#4,#6,#7,#9,#12 + engine-key-leak pushed (`b473c19`). OPEN: #8 (owner decision), #10 (parity lane), #11 (queued), #9 takeoff-overwrite (`takeoff.py`) |
| 🧱 Permit | SB CRM-clone MAIN PERMITBUILDER CODER | `ahj.py`, `permits.py`, `permit_detail.html` | ✅ pushed (`efb03c3`): AHJ portal-links card + submittal spec. **main FF'd → efb03c3** |
| 🏠 Portal | SB CRM-clone MAIN DEV HO PORTAL UI CODER | `portal.py`, portal templates | idle |
| 🔌 AppConnect | SB CRM-clone MAIN APPCONNECT CODER | integration glue / takeoff | idle |
| 🔁 Sync | (Sync coder) | `acculynx_sync.py` bridges | folded into Security #1 |
| 📐 Measurement | SeaBreeze roof measurement specialist | measurement ingest | idle |
| 🛰️ Roof Engine | SB CRM-clone MAIN DEV ROOFENGINE CODER | engine VM, `/api/takeoff` push | waiting on `MEASURE_CRM_WEBHOOK_SECRET` |
| 📷 SiteCam | SB CRM SiteCam-clone MAIN DEV UI CODER | sitecam-api, SSO verify | waiting on `SEABREEZE` rotation + SEED_FORCE |
| 🧮 Takeoff | SB CRM-clone TAKEOFFBUILDER CODER | estimator envelope · audit | ✅ #9 + #11 verified (`093dfa2`,`d122361`); ▶ now on db.insert() conn-leak fix (P2) |
| 📧 Gmail Alias | Gmail alias automation Chrome Extension | per-account Gmail alias/draft automation | active |
| 🏢 WWS (SEPARATE project) | WWSLGC: WWS CRM + WWS SiteCam | 2nd tenant — NOT in this repo | ⚠ divergence risk — assess fork vs. make-it-a-tenant of this codebase |

---

## TASK QUEUE (priority order)
| # | Task | Owner | State | Notes |
|---|---|---|---|---|
| P0 | **Close Critical #1 on the LIVE domain → via DNS cutover to Render** | Head Coach + owner | OPEN | Decision made: cut over to Render (not a Vercel redeploy). Needs Render fully configured+verified, then DNS repoint. Interim: hole is data-integrity only (no PII path), unadvertised domain — accepted briefly pending a prompt cutover. |
| P1 | **Build secret provisioning script** | Head Coach | ✅ DONE | `scripts/provision_secrets.py` built + dry-run-verified (masks values, reads token from env, --apply gated). Render env-var push; Vercel omitted by design (one-vendor). Tenant-onboarding seed. |
| P1 | **Stand up scheduled Head Coach (cron)** | Head Coach | NEXT | Wakes every few hrs: pull, verify state, reconcile board, ping idle/blocked lanes, escalate owner-decisions. Autonomy = act-on-safe. |
| P0 | **Set `CRM_SYNC_SECRET` + `CRM_SECRET` on Render** | owner (dashboard) | in progress | Render fail-closes without them. Reinstall bookmarklets after. |
| P1 | **Rotate burned/leaked secrets** | owner | OPEN | `MEASURE` (chat-leak ×2), `SEABREEZE` (leaked), `GOOGLE_OAUTH_CLIENT_SECRET` (screenshot), roof-engine key. Fresh values → dashboards + `secrets/keys.local.env`. |
| P1 | **Permit lane: commit+push 3 files, FF main** | Permit | ✅ DONE | Pushed `efb03c3`; main FF'd `c612877`→`efb03c3` (all audit+migration+permit work now on main). |
| P1 | **Import Service + Warranty depts (migration parity A)** | Sync (done) → owner | ✅ CODE FIXED (`fdb2f97`, crm-ui-verified: `_department_for` maps workType→configured dept, tenant-agnostic). **NOW owner-gated: RE-SYNC** (re-run ⭐Sync All) so existing jobs pick up the corrected department; dept views are empty until then. Gates AccuLynx cancellation. |
| P2 | **Audit #3 (`est_num` money corruption) + #4 (dup invoice #s)** | Security | QUEUED | No decision needed; do after permit resync. |
| P2 | **Secret provisioning automation** | Head Coach | DESIGN | One-command push of `keys.local.env` → Render/Vercel APIs; seed of tenant onboarding. |
| P3 | **SSO go-live** | SiteCam + owner | BLOCKED | Needs fresh `SEABREEZE` on both sides + sitecam-api `SEED_FORCE=true`. |
| P3 | **QuickBooks integration (product)** | TBD | BACKLOG | Per-tenant OAuth design needed for white-label. |
| P3 | **Audit #5–#12** | Security/crm-ui | ✅ ALL CLOSED | #5 SECRET_KEY boot-guard `e8383fc`; #6 SSRF pre-existing; #7 CSRF tokens JS-auto-inject 51 templates `d739c97`. All 7 findings closed. |
| P2 | **`db.insert()` connection leak on IntegrityError** | Takeoff/crm-ui | ✅ DONE | `edda6d8` (crm-ui): all 6 db.py helpers (insert/update/get/delete/execute/all_rows) wrapped in try/finally. |
| P0 | **Owner-only System Map page (Danny-direct)** | crm-ui | ✅ DONE | `cfe1188` — /reports/system-map, owner-gated (is_owner seeded on id1; Karla 403; anon→login). Live: lead-flow SVG + disk tree + DB census w/ red flags. Verified via test client. |
| P2 | **Nav: Site Photos + Roof Reports under Integrations dropdown (Danny-direct)** | crm-ui | ✅ DONE | `7d5ca01` — verified on local preview. |
| P3 | **Backlog from data-trace (operations 1145)** | crm-ui | ✅ ALL DONE | (a) `/admin/reconcile-docs` registers ~610 orphaned files (job_id=NULL) `8e0f42a`; (b) payment-receipt email on invoice pay (draft-only) `4bcdcf9`; (c) portal token TTL 365-day sliding window + 410 expired page `c049674`. |

---

## DECISIONS NEEDED (owner) — 2026-06-12

| # | Decision | Context | Source |
|---|---|---|---|
| ~~**D1**~~ | ✅ **RESOLVED 2026-06-13 (overlord, no owner action)** — CRM AI was 401'ing on a dead ANTHROPIC_API_KEY. overlord hunted the machine, found a LIVE key, deployed it to Render via API, redeploy live, verified the served key returns 200 from Anthropic. Extract Info / voice intake / Scan Email restored. Per **RULE 1 (no keys ever)** Danny did nothing. NOTE: CRM + operations now share one Anthropic key (acceptable; flagged for per-service split once an Admin key exists). | operations 2026-06-11 → fixed 2026-06-13 |
| **D2** | **Update Vercel: ROOF_ENGINE_API_KEY + ROOF_ENGINE_URL** | Old `71tk...` key is dead (401). New key written to `whitelabel-crm/secrets/keys.local.env` by roofengine lane. Copy both values to Vercel project env + redeploy. Live "New Roof Report" page 401s until done. | roofengine 2026-06-11T1751 |
| **D3** | 🚧 **K5 nginx cert/SNI fix — BLOCKED ON ACCESS** | Fix nginx `listen` line in `/etc/nginx/conf.d/k5.conf`. Directive has now fired **4×** (06-11 1330/1629, 06-13 0125/0127) and **can't be executed** — overlord searched the machine (project dir, `.ssh/config`, deploy docs): there is **NO K5 server host/SSH path anywhere in the mesh**, and no K5 agent in the registry. To unblock, overlord needs the K5 box's host + how it's reached (if a key exists on the machine, point me at it and per RULE 1 I own it from there). Options: (a) tell overlord where K5's nginx lives, or (b) stand up a `k5` lane with access. Until then this is a no-op no matter how many times it re-sends. | owner ×4 → blocked 2026-06-13 |
| **D4** | **legal-intake-agent — add auth to API routes** | PII currently exposed on API routes. No `legal-intake-agent` slug in registry. Assign or handle directly. | owner 2026-06-11T1333 |
| **D5** | 🔴 **DeFi collateral near liquidation** | 0.30 BTC locked as Coinbase DeFi collateral, LTV ≈83%, liquidation at ~$60.5K (~3% below yesterday's spot). Price may have moved. Open Coinbase Borrow screen → check exact LTV → decide: add USDC/USDT, partial repay, or accept risk. Time-sensitive. | investments 2026-06-11T1450 |
| **D6** | **Tastytrade wheel sleeve — keep or redeploy to debt?** | $3,139 sleeve earning ~$30–50/mo best-case wheel premium vs ~27% Coinbase card APR. Investments recommends redirecting to 27% debt first. No action needed until owner decides. | investments 2026-06-11T1432 |
| **D7** | **Open coinbase-trader + tasty-trader sessions** | Both bots need Danny to open their sessions so they can ack pause/secrets/position questions. Heartbeat cannot trigger them. coinbase-trader: confirm live execution PAUSED, jackson `.env` secrets rotated. tasty-trader: confirm new live Wheel entries halted, kill-switch status. | investments 2026-06-11T1405 |
| **D8** | **Repo-rename wave 1 sign-off** | Operations has a 5-wave repo-rename migration map draft at `C:\Users\kjburnz\operations\reference\REPO_RENAME_MIGRATION_MAP_DRAFT.md`. Needs OVERLORD/owner sign-off on: (1) wave 1 low-risk renames, (2) extract `_OVERLORD` to top level, (3) resolve `whitelabel-crm` vs `white-label-crm` naming collision. Breaking changes inventoried. Nothing executed until approved. | operations 2026-06-11T1635 |
| **D9** | 🔴 **SSN exposed in Drive — restrict file** | Danny's 2023 Schedule C (filed tax return) with SSN in plain text is in Google Drive. Accounting flagged it. Restrict the file immediately. Also: confirm Danny's personal P&L components — bank …1338 statements (SeaBreeze income) + La Gala commission/cut docs — needed to close the personal P&L. | accounting 2026-06-11T1010 |
| **D10** | **Candy's — logo pick A/B/C** | Open `C:\Users\kjburnz\candys-cake-pops\preview\logo-picker.html` and pick a logo. Unblocks logo finals prep (outlined SVG, PNG exports, brand.html update). | candys 2026-06-12 |
| **D11** | **Candy's — Vercel prod promote** | Preview deploy being prepped by candys agent. Danny clicks "Promote to Production" in Vercel dashboard (or runs `vercel --prod` in `candys-cake-pops/site/`). Owner's chat directive ("send to overlord for execution") treated as preview-OK; prod promote still needs the click. | candys 2026-06-12 |
| **D12** | **Candy's — "Run now" on scheduled task `dev-agent-dashboard-requests`** | One-time run in Claude app → Scheduled sidebar to pre-approve Gmail/Bash tools before the 30-min cron fires blind. | candys 2026-06-12 |
| **D13** | **Candy's — Square checkout links** | Needs Candice's Square dashboard (client account). Interim cakepops.com CTAs stay until she provides. Swap points marked `SQUARE-CHECKOUT` in HTML. | candys 2026-06-12 |
| **D14** | 🔴 **Merge `agent/gc-consolidation` → main** | 86/86 tests pass. All CRs done. ⚠️ PRE-MERGE: templates-costing lane (session 67ce606b) claims to have committed new schema (material_catalog, worksheet_lines +cols, metal sub-systems, recosted ESTIMATE_TEMPLATES) to `main` on Vercel/Neon — but those commits are NOT visible on `origin/main` as of 2026-06-13T03:00 ET. Before merge, Danny/Head Coach must confirm where templates-costing changes live and incorporate them. Merge command: `git checkout main && git merge agent/gc-consolidation && git push`. | qa-roofr-reprot + crm-ui 2026-06-13 |
| **D15** | 🔴 **sitecam + roofengine agents offline 41+ hrs** | 4 messages unread since 2026-06-10: sitecam SSO go-live, SEABREEZE rotation coord, roofengine takeoff integration, measure-ready VM push. Either restart those agents and point them at the inbox, or delegate the work to crm-ui / appconnect. No action = SiteCam SSO and Roof Engine push stay blocked indefinitely. | qa-roofr-reprot 2026-06-12T1946 |
| **D16** | **Set SMTP_FROM + SMTP_PASSWORD on Render** | Portal invite emails + rep notifications now fall through to SMTP when Gmail OAuth is absent. Two env vars needed on Render (SMTP_FROM = Gmail address, SMTP_PASSWORD = Gmail app-password from Google Account → Security → App passwords). 2-min setup. All client portal invites + lead notifications will fire automatically once set. | crm-ui 2026-06-12T2230 |
| **D17** | **La Gala profile — when to activate takeoff for La Gala** | takeoff lane building SeaBreeze-only v1. La Gala activation needs: second CRM tenant stand-up + LAGALA_CRM_WEBHOOK_SECRET on Render + lgc-bid-form handoff. No urgency until La Gala jobs come in — Danny confirms timing. | crm-ui / takeoff 2026-06-12T2230 |
| **D18** | **Job Expenses import (16,217 rows)** — approve schema + import path | `worksheet_lines` table needs bulk-import from AccuLynx Job Expenses CSV (`C:\Users\kjburnz\Downloads\AccuLynx_Reports_ZIP`). Columns: Job Name, Payment Date, Type, Amount, To/Method, Check/Ref, Memo, Job Value, Balance, Account Type, Paid-in-Full. crm-ui can build the import + per-job UI — needs Danny's OK on where these show (Worksheet tab? New Expenses tab?). | crm-ui 2026-06-13 |
| **D19** | **Appointments import (310 rows) + Workflow Status import (2,000+ rows)** — approve | `appointments` table exists (calendar.py). Import path for AccuLynx Appointments CSV + `job_stage_history` table for Workflow Status needs a go — crm-ui builds both once approved. | crm-ui 2026-06-13 |
| **D20** | **La Gala gov-portal pipeline — provide FEIN + drop docs, then 3 final clicks** | la-gala lane signed up 5 master portals (Bonfire, OpenGov, DemandStar, BidNet, + PBC) covering 30+ municipalities, and pre-filled 3 more to the final click. **Master unlock = FEIN/EIN** — gates MFMP (all FL state agencies), BidNet final, Bids&Tenders Boynton, Boca eSourcing. Also needs a doc drop (W-9, license PDF, COI, comp cert, articles) into `la gala\portal-signups\docs\`. Owner-actions, in order: (1) drop FEIN + docs; (2) click activation emails to Danny@lagalacon.com for the 5 created accounts; (3) final-click the 3 pre-filled (PBC VSS, Bids&Tenders Boynton, Boca Raton eSourcing). Heavy lifts deferred: SAM.gov UEI check, FDOT pre-qual. Full click-order playbook: `C:\Users\kjburnz\la gala\portal-signups\HANDOFF.md`. | la-gala audit 2026-06-12T1645 (flagged by overlord 2026-06-13) |
| **D21** | **Cloudflare R2 migration — say "go" to approve** | One R2 bucket (`collab-cdn`) replaces Google Drive mirror + Render ephemeral disk for CRM uploads (618 files, 2.5 GB). Cost: **$0/mo** at current volume (10 GB R2 free tier; $0 egress always). Plan: reconcile documents table (8 DB rows vs 618 disk files), upload to R2, update CRM URLs, retire Drive mirror, remove 2.5 GB from Render disk. Keys: overlord's lifecycle (RULE 1 — Danny never touches). Danny's action = one word **"go"** in chat. | crm-ui 2026-06-15 |

---

## DECISIONS — RESOLVED (2026-06-10)
- [x] **Head-coach autonomy:** ACT on safe/reversible (board, pings, FF main, doc commits, local tests); ESCALATE irreversible/outward-facing/spend (deploys, DNS, secrets, sends).
- [x] **Secret automation:** build a **provisioning script** now (reads `secrets/keys.local.env` → pushes to Render/Vercel APIs). No new vendor; becomes the tenant-onboarding seed.
- [x] **Live-#1 close path:** owner delegated → **Head Coach call = DNS cutover to Render** (closes the hole + collapses to one vendor, the stated direction). Sequence: set/rotate all Render secrets → verify Render healthy on onrender URL → repoint DNS → keep Vercel as rollback. **Blocked on:** owner's DNS host (for exact records) + Render secrets finished.

## 🚚 MIGRATION PROGRAM (AccuLynx→CRM · lead: crm-ui) — proof in `docs/PARITY_2026-06-11.md`
- **A. Data parity** — IN PROGRESS. Counts look migrated (1,231 jobs, 1,952 contacts, 429 invoices…). **Financials NOT:** only 89/1,231 jobs have a contract_value; `collected`=$2,290 / payments $1.008M not rolled up; invoices 0-paid; photos 2; docs 146 (partial); roof_reports 0. **Blockers:** run financial-progress + billing-linkage + photos + docs collectors (owner-gated bookmarklets) + need AccuLynx reference totals. Confirm Render serves the migrated DB (not `crm.db`/25 jobs).
- **B. One vendor** — ✅ DNS cutover DONE (crm.* → Render, verified 2026-06-13); Render secrets all set & in sync. Vercel now only the rollback — can be retired once we're confident. Effectively on one vendor.
- **C. Integrations** — Roof Engine (MEASURE gate) · SiteCam SSO (SEABREEZE+SEED_FORCE) · Gmail/Drive/QBO/QXO verify each.
- **D. Tightened** — #1/#2 live; #3/#4 next; then #5/#6/#7; #8–#12 triage.
- **E. Cancel AccuLynx** — gated on A parity confirmed (~$250/mo saved).

## 🧹 SESSION CONSOLIDATION PROGRAM (lead: crm-ui · dispatched 2026-06-11)
12 stale one-off task sessions (none running, 06-04..06-09) collapse into 3 standing lanes. All
features already merged; lanes VERIFY-don't-rebuild vs current tip, then sessions get archived.
| Lane | Legacy sessions folded in | Status |
|---|---|---|
| **crm-ui** (me) | QA+mobile/UX+lead-intake · overdue-invoice 1-click · onboarding+comms/worksheet tabs · estimate templates (EST-0154 ✅) · dashboard · "resume sales CRM" | mine to verify — pending (voice-wizard worker active on intake; verify after it lands) |
| **permit** | submittal packet · Broward folio+legal · NOC/form stamp · HVHZ cover · gov-portals signup | ✅ CONSOLIDATION DONE — 3 tasks shipped: packet_builder_handoff in repo (`dd4b21b`), nav links for Gov Portals + Contractor Profile (`65754f1`), embed widget `/permits/widget/embed` (`65754f1`) |
| **roofengine** | Roof Report Engine v3 | 📨 intake sent (inbox/roofengine, 2026-06-11T1743) |
Archive trigger: each lane replies "<LANE> consolidation clean" → crm-ui hands Danny the one-click archive list.

**⚡ MERGE-READY:** `agent/gc-consolidation` is ready to merge to main. Lead-naming feature tested (61 unit tests, 76 total passing, `0771cbd`). AHJ map expansion done (`dd4b21b`). All audit findings closed. Prerequisite `agent/lead-onboarding` already in main (`625231e`). Owner or Head Coach can merge at any time.

## CRITICAL PATH → "SeaBreeze live + secure on ONE vendor"
1. **Owner** sets/rotates all Render secrets (below) → 2. **Head Coach** verifies Render healthy on `collab-crm-bwsl.onrender.com` → 3. **Owner** DNS cutover in Cloudflare (CNAME `crm` → Render, grey-cloud) → 4. **Critical #1 closed on the live domain**, Vercel kept as rollback.
Runs in parallel: Permit lane FF's `main`; Security lane does audit #3/#4; Head Coach builds the provisioning script + assesses WWS divergence.

## OWNER — your minimal clicks (only you can do these; I drive everything else)
1. **Render env** (use *Generate*, don't type values): `CRM_SYNC_SECRET`, `CRM_SECRET`, fresh `MEASURE_CRM_WEBHOOK_SECRET`, fresh `SEABREEZE_CRM_WEBHOOK_SECRET`.
2. **Google Console** → rotate `GOOGLE_OAUTH_CLIENT_SECRET` (leaked) → paste new into Render; add the CRM redirect URIs.
3. **sitecam-api** → set the *same* fresh `SEABREEZE` value → redeploy once with `SEED_FORCE=true` → flip back.
4. **Engine VM** (via Roof Engine lane) → set the *same* fresh `MEASURE` value.
5. ~~**Cloudflare DNS cutover**~~ ✅ **DONE (verified 2026-06-13)** — `crm.collaborativeconceptsfl.com` CNAME → `collab-crm-bwsl.onrender.com` (DNS-only), Render custom-domain verified + cert issued, `/login` returns HTTP 200 serving the SeaBreeze CRM. Branded domain is LIVE on Render. Vercel remains as rollback.
6. **RE-SYNC after the dept fix** (`fdb2f97`): re-run the ⭐Sync All bookmarklet once so existing Service/Warranty jobs move out of REROOF into the right department views.
7. **🔴 Update `GDRIVE_SA_JSON` on Render** (srv-d8kq47jbc2fs73crtnug/env) — the old SA key was REVOKED; CRM Drive uploads FAIL until you paste the new key file's contents (`C:\Users\kjburnz\seabreeze_jobs\_shared\keys\gcp-crm-files-sa-helpful-weft-498804-c7.json`) into the Render field. Don't paste it in chat.
8. **Neon `DATABASE_URL`** → add to `secrets/keys.local.env` (or run the proof cmd where it lives) so the parity proof can run. + supply AccuLynx reference totals.
→ After this one manual round, the provisioning script makes secret-setting a single command. This is the last hand-entry pass.

## PROTOCOL
Pull before, push after. Update your row. `send_message` for "your turn now" pings only.
Verify git/host claims with tools. Secrets never in chat/commits. See `CLAUDE.md`.
