# SESSION STATE — start here (as of 2026-06-10, grounded against the repo)

Read this cold to continue the white-label CRM work. **Verify claims with tools — this
project has had multiple parallel agents and stale-narration confusion all session.**

## Repo / hosts (the important part)
- **Repo:** `…/acculynx roofr reprot/whitelabel-crm`, remote `github.com/dannybivins83-blip/collab-crm`. Working branch: **`agent/gc-consolidation`**.
- **Git reality (verify with `git log --oneline -1` + `git rev-parse origin/main origin/agent/gc-consolidation`):**
  - local HEAD `1c7a17d` (adds `POST /api/takeoff`) — **NOT pushed**.
  - `origin/agent/gc-consolidation` = `212d40e` (one behind local; **Render builds from this branch**).
  - `origin/main` = `b3f134e` (behind both).
  - **Uncommitted:** `docs/INTEGRATION_HANDOFF.md` (modified). **Untracked:** `modules/qxo.py` (a parallel agent's — inspect before committing/deleting).
- **TWO live deployments (not yet unified):**
  - **Vercel** — `crm.collaborativeconceptsfl.com` (custom domain currently points here) + `whitelabel-crm-rho.vercel.app`. DB = **Neon Postgres**. Deployed via CLI from local tree → **has `/api/takeoff`**. Roof-engine env vars set here.
  - **Render** — `collab-crm-bwsl.onrender.com`, service `collab-crm`. DB = **SQLite on a 5GB disk** (`/data`), `DATABASE_URL` intentionally omitted. **Migration agent loaded 26,258 rows Neon→SQLite here.** Builds from `origin/agent/gc-consolidation` (`212d40e`) → **does NOT yet have `/api/takeoff`** (it's in the unpushed `1c7a17d`).
- **Canonical target = Render** (1 vendor: SQLite+uploads on disk, Drive read-fallback). **DNS cutover Vercel→Render is PENDING** (still baking on the onrender URL; Vercel kept as instant rollback).

## To unify (recommended first actions for the new session)
1. **Inspect `modules/qxo.py`** (untracked, unknown) — decide commit vs delete.
2. **Commit** `docs/INTEGRATION_HANDOFF.md`, then **push `agent/gc-consolidation`** so `origin/gc` gets `1c7a17d` (→ Render redeploys **with `/api/takeoff`**).
3. **Fast-forward `main` → `gc-consolidation`** and push (clean FF; verified earlier `origin/main` is an ancestor).
4. **DNS cutover** when ready: repoint `crm.collaborativeconceptsfl.com` Vercel→Render; keep Vercel as rollback.

## Secrets (HMAC for the ingest endpoints)
- `MEASURE_CRM_WEBHOOK_SECRET` is **deliberately UNSET** on both hosts → both derive the fallback **`seabreeze-webhook-secret`**. So the **roof engine + Estimator must sign with `seabreeze-webhook-secret`** (the fallback) — NOT a random value, NOT the SEABREEZE one — OR set a strong value on **both** the live CRM host **and** the engine VM (safe now; nothing live-signs with it yet, more secure than the public fallback).
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
