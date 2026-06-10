# SESSION STATE — start here (as of 2026-06-10, grounded against the repo)

Read this cold to continue the white-label CRM work. **Verify claims with tools — this
project has had multiple parallel agents and stale-narration confusion all session.**

## Repo / hosts (the important part)
- **Repo:** `…/acculynx roofr reprot/whitelabel-crm`, remote `github.com/dannybivins83-blip/collab-crm`. Working branch: **`agent/gc-consolidation`**.
- **Git reality (verified 2026-06-10 via `git rev-parse HEAD origin/main origin/agent/gc-consolidation` + `git status`):**
  - **UNIFIED at `9dd3f02`** ("Add CRM sitemaps") — tip; parent `8f2c2c1` ("session-state + integration handoff + QXO scaffold"). local HEAD == `origin/main` == `origin/agent/gc-consolidation` == `9dd3f02`.
  - History folds in the former `1c7a17d` (`POST /api/takeoff`), the `INTEGRATION_HANDOFF.md` edit, and `modules/qxo.py` (inert "QXO dark scaffold"); the old `1c7a17d`/`212d40e`/`b3f134e` divergence is RESOLVED.
  - **Uncommitted (this session, intentional):** `docs/SESSION_STATE.md` (this regrounding) + new `docs/CUTOVER_AND_SECRETS_RUNBOOK.md`. Nav aid: `docs/SITEMAP.html` (4 maps — full system / backend / portal / workflow).
- **TWO live deployments (not yet unified):**
  - **Vercel** — `crm.collaborativeconceptsfl.com` (custom domain currently points here) + `whitelabel-crm-rho.vercel.app`. DB = **Neon Postgres**. Deployed via CLI from local tree → **has `/api/takeoff`**. Roof-engine env vars set here.
  - **Render** — `collab-crm-bwsl.onrender.com`, service `collab-crm`. DB = **SQLite on a 5GB disk** (`/data`), `DATABASE_URL` intentionally omitted. **Migration agent loaded 26,258 rows Neon→SQLite here.** Builds from `origin/agent/gc-consolidation` (now `8f2c2c1`) → **HAS `/api/takeoff`** (verified 2026-06-10: `OPTIONS /api/takeoff` → 200, and `/measurements/ingest` → 200).
- **Canonical target = Render** (1 vendor: SQLite+uploads on disk, Drive read-fallback). **DNS cutover Vercel→Render is PENDING** (still baking on the onrender URL; Vercel kept as instant rollback).

## To unify
1. ✅ **DONE** — `modules/qxo.py` committed (inert "QXO dark scaffold") in `8f2c2c1`.
2. ✅ **DONE** — `INTEGRATION_HANDOFF.md` committed + `agent/gc-consolidation` pushed; Render redeployed **with `/api/takeoff`** (verified 200).
3. ✅ **DONE** — `main` fast-forwarded to `8f2c2c1` and pushed (all three refs equal).
4. ⏳ **PENDING — DNS cutover** `crm.collaborativeconceptsfl.com` Vercel→Render. Still served by Vercel (verified `Server: Vercel`; resolves to Vercel IPs 216.198.79.65 / 64.29.17.1 via Cloudflare DNS-only). **Precondition: add the custom domain on Render + let its TLS cert issue BEFORE flipping DNS; keep Vercel as instant rollback.** Blocked on Cloudflare + Render dashboard access (no token in the agent env).

## Secrets (HMAC for the ingest endpoints)
- ▶ **Step-by-step runbook for everything in this section + the DNS cutover: `docs/CUTOVER_AND_SECRETS_RUNBOOK.md`** (the agent can't reach Cloudflare/Render/GitHub/the VM — you click; no secret values in the file).
- **DECIDED 2026-06-10:** MEASURE secret → **strong value on both sides** (set `MEASURE_CRM_WEBHOOK_SECRET` on Render+Vercel + the engine VM/Estimator; stop using the fallback). Execution path → **runbooks, user clicks.**
- `MEASURE_CRM_WEBHOOK_SECRET` is currently **UNSET** on both hosts → both derive the fallback **`seabreeze-webhook-secret`** (`_ingest_secret()` → `{SSO_TENANT_KEY|seabreeze}-webhook-secret`). Safe to set a strong value now — nothing live-signs `/api/takeoff` yet (engine push dormant). See runbook §2.
- 🔒 **Rotate `SEABREEZE_CRM_WEBHOOK_SECRET`** (`7938…efbb87`) and the **roof-engine API key** (`71tk…`) — both leaked into the prior chat transcript. (SEABREEZE = the SiteCam SSO secret.)
- 🔒 **Revoke the `roof-crm-deploy` GitHub PAT** once integrations confirmed.

## The integration seams (who does what)
- **CRM (this repo / me):** ✅ `POST /measurements/ingest` (measurements) and `POST /api/takeoff` (full Estimator envelope: job+measurement+estimate+submittals, idempotent). Contracts: `docs/MEASUREMENT_INGEST.md`, `docs/TAKEOFF_INGEST.md`.
- **Roof Engine** (Oracle VM `150.136.152.240`): measures roofs; CRM push **built but dormant** until `MEASURE_CRM_WEBHOOK_SECRET` is on the VM (= same as the CRM verifies). Plan: fold the Estimator's LLM plan-read into the engine so a drawing upload → measure+takeoff → one POST to `/api/takeoff`.
- **Estimator agent:** now emits the `estimator-takeoff/v1` envelope + a `post_takeoff.py` helper instead of the old AccuLynx JSON.
- **SiteCam app** (separate Render account, `sitecam-api.onrender.com`): SSO + a `/api/public/showcase` photo endpoint the portal Roof School consumes (keep it warm — Render free cold-start ~11s vs portal 4s timeout).

## What shipped this session (all live on Vercel; on Render once `1c7a17d` is pushed)
Billing sync + AccuLynx-style paid-% ring + real payment records (LinkedPayments); worksheet auto-materialize; lifecycle/Pipeline board + per-card quick actions; portal billing display + Roof School + Design Studio + Welcome Packet; estimate line-item mirror + AccuLynx tile template; the full 10-tool sync-bookmarklet suite + one-click installer; roof-reports tie-in; demo-portal generator; Gmail→CRM links + Smart To-Do; voice intake; New-Lead estimate-field removed; Contacts→Tools submenu; lead-name auto-compose; AHJ resolvers.

## Open product decisions (not bugs)
- Permit builder: feed full LF/facets into wind calcs? wire SiteCam photos→packet? (e-sig is correctly NOT forwarded to notarized forms — `docs/PERMIT_SIGNATURE.md`.)
- LLM plan-reader for the engine (line items / NOA index / wind design) — needs an LLM key + budget, or point at the Estimator's code to fold in.
- Auto-send toggles (`auto_portal_invite`, `portal_notify`) default OFF — test with one real email before enabling.
