# 🏗️ COORDINATION BOARD — White-Label Roofing CRM

**The single source of truth for who's doing what.** Every lane: pull → read this → work →
update your row → push. One pusher at a time. Don't route status through the owner.

- **Repo:** `github.com/dannybivins83-blip/collab-crm` · working branch `agent/gc-consolidation`
- **Git tip (verified 2026-06-11 `git rev-parse`):** `f9cf05c` on `origin/agent/gc-consolidation` (latest: takeoff assigned db.insert leak fix)
- **Last board update:** 2026-06-11 — by crm-ui: audit #10 DONE (billing cursor GUID seen-set dedup, `acculynx_sync.py`); #10 added to done list

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
| P3 | **Audit #5–#12** | Security | BACKLOG | SECRET_KEY default, CSRF, SSRF, checklist gating, etc. (#8,#9,#10,#11 now DONE) |
| P2 | **`db.insert()` connection leak on IntegrityError** | Takeoff | ▶ IN PROGRESS | takeoff found + assigned to fix (confirmed db.py:495 no try/finally). Fix insert + update/execute consistently + test; db.py clean, guarded-push db.py + test only. |
| P0 | **Owner-only System Map page (Danny-direct)** | crm-ui | ✅ DONE | `cfe1188` — /reports/system-map, owner-gated (is_owner seeded on id1; Karla 403; anon→login). Live: lead-flow SVG + disk tree + DB census w/ red flags. Verified via test client. |
| P2 | **Nav: Site Photos + Roof Reports under Integrations dropdown (Danny-direct)** | crm-ui | ✅ DONE | `7d5ca01` — verified on local preview. |
| P3 | **Backlog from data-trace (operations 1145)** | TBD | OPEN | (a) link 610 unregistered uploads/documents files into `documents` table; (b) payment-receipt email on `/invoices/<id>/pay` (no email today); (c) portal token TTL/expiry (`token_urlsafe(12)` never expires). |

---

## DECISIONS — RESOLVED (2026-06-10)
- [x] **Head-coach autonomy:** ACT on safe/reversible (board, pings, FF main, doc commits, local tests); ESCALATE irreversible/outward-facing/spend (deploys, DNS, secrets, sends).
- [x] **Secret automation:** build a **provisioning script** now (reads `secrets/keys.local.env` → pushes to Render/Vercel APIs). No new vendor; becomes the tenant-onboarding seed.
- [x] **Live-#1 close path:** owner delegated → **Head Coach call = DNS cutover to Render** (closes the hole + collapses to one vendor, the stated direction). Sequence: set/rotate all Render secrets → verify Render healthy on onrender URL → repoint DNS → keep Vercel as rollback. **Blocked on:** owner's DNS host (for exact records) + Render secrets finished.

## 🚚 MIGRATION PROGRAM (AccuLynx→CRM · lead: crm-ui) — proof in `docs/PARITY_2026-06-11.md`
- **A. Data parity** — IN PROGRESS. Counts look migrated (1,231 jobs, 1,952 contacts, 429 invoices…). **Financials NOT:** only 89/1,231 jobs have a contract_value; `collected`=$2,290 / payments $1.008M not rolled up; invoices 0-paid; photos 2; docs 146 (partial); roof_reports 0. **Blockers:** run financial-progress + billing-linkage + photos + docs collectors (owner-gated bookmarklets) + need AccuLynx reference totals. Confirm Render serves the migrated DB (not `crm.db`/25 jobs).
- **B. One vendor** — owner: Render secrets + DNS cutover (see owner list).
- **C. Integrations** — Roof Engine (MEASURE gate) · SiteCam SSO (SEABREEZE+SEED_FORCE) · Gmail/Drive/QBO/QXO verify each.
- **D. Tightened** — #1/#2 live; #3/#4 next; then #5/#6/#7; #8–#12 triage.
- **E. Cancel AccuLynx** — gated on A parity confirmed (~$250/mo saved).

## 🧹 SESSION CONSOLIDATION PROGRAM (lead: crm-ui · dispatched 2026-06-11)
12 stale one-off task sessions (none running, 06-04..06-09) collapse into 3 standing lanes. All
features already merged; lanes VERIFY-don't-rebuild vs current tip, then sessions get archived.
| Lane | Legacy sessions folded in | Status |
|---|---|---|
| **crm-ui** (me) | QA+mobile/UX+lead-intake · overdue-invoice 1-click · onboarding+comms/worksheet tabs · estimate templates (EST-0154 ✅) · dashboard · "resume sales CRM" | mine to verify — pending (voice-wizard worker active on intake; verify after it lands) |
| **permit** | submittal packet · Broward folio+legal · NOC/form stamp · HVHZ cover · gov-portals signup | 📨 intake sent (inbox/permit, 2026-06-11T1743). Flagged likely-gap: gov-portals signup (no grep hit) |
| **roofengine** | Roof Report Engine v3 | 📨 intake sent (inbox/roofengine, 2026-06-11T1743) |
Archive trigger: each lane replies "<LANE> consolidation clean" → crm-ui hands Danny the one-click archive list.

## CRITICAL PATH → "SeaBreeze live + secure on ONE vendor"
1. **Owner** sets/rotates all Render secrets (below) → 2. **Head Coach** verifies Render healthy on `collab-crm-bwsl.onrender.com` → 3. **Owner** DNS cutover in Cloudflare (CNAME `crm` → Render, grey-cloud) → 4. **Critical #1 closed on the live domain**, Vercel kept as rollback.
Runs in parallel: Permit lane FF's `main`; Security lane does audit #3/#4; Head Coach builds the provisioning script + assesses WWS divergence.

## OWNER — your minimal clicks (only you can do these; I drive everything else)
1. **Render env** (use *Generate*, don't type values): `CRM_SYNC_SECRET`, `CRM_SECRET`, fresh `MEASURE_CRM_WEBHOOK_SECRET`, fresh `SEABREEZE_CRM_WEBHOOK_SECRET`.
2. **Google Console** → rotate `GOOGLE_OAUTH_CLIENT_SECRET` (leaked) → paste new into Render; add the CRM redirect URIs.
3. **sitecam-api** → set the *same* fresh `SEABREEZE` value → redeploy once with `SEED_FORCE=true` → flip back.
4. **Engine VM** (via Roof Engine lane) → set the *same* fresh `MEASURE` value.
5. **Cloudflare DNS cutover** — only after I green-light Render health.
6. **RE-SYNC after the dept fix** (`fdb2f97`): re-run the ⭐Sync All bookmarklet once so existing Service/Warranty jobs move out of REROOF into the right department views.
7. **🔴 Update `GDRIVE_SA_JSON` on Render** (srv-d8kq47jbc2fs73crtnug/env) — the old SA key was REVOKED; CRM Drive uploads FAIL until you paste the new key file's contents (`C:\Users\kjburnz\seabreeze_jobs\_shared\keys\gcp-crm-files-sa-helpful-weft-498804-c7.json`) into the Render field. Don't paste it in chat.
8. **Neon `DATABASE_URL`** → add to `secrets/keys.local.env` (or run the proof cmd where it lives) so the parity proof can run. + supply AccuLynx reference totals.
→ After this one manual round, the provisioning script makes secret-setting a single command. This is the last hand-entry pass.

## PROTOCOL
Pull before, push after. Update your row. `send_message` for "your turn now" pings only.
Verify git/host claims with tools. Secrets never in chat/commits. See `CLAUDE.md`.
