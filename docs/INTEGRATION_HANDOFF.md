# Integration Handoff — SiteCam · Roof Engine · Permit Builder

**To:** boss dev agent (integrator, `agent/gc-consolidation`)
**From:** SSO/feature session
**As of:** `d12db85` · 2026-06-10 — *living doc; re-ground this header (commit +
counts) whenever you revisit, and treat the operator steps below as point-in-time.*
**Branch:** CRM `agent/gc-consolidation` (~79 ahead of `main`); SiteCam `main`.
**Scope:** finish wiring SiteCam, the roof-measurement engine, and the permit
builder. Status is grounded against the code, not narration.

Status legend: ✅ done & committed · 🟡 code-complete, needs deploy/secret/verify ·
⛔ blocked on something external · 🔎 review/decision needed.

---

## 1. SiteCam ⇄ CRM — Unified SSO

**What it does:** log into the CRM (Google or password) → the embedded Site
Photos tab auto-logs into SiteCam in the correct tenant, no second login.

### Done (committed)
- ✅ **CRM mint + registry** — `modules/sso.py` (`GET /sso/token/<app_id>`,
  `GET /sso/apps`, HMAC-signed ~90s single-use assertion). Registered in `app.py`.
- ✅ **Origin-checked handoff** — `templates/sitecam.html` postMessages the
  assertion to SiteCam's exact origin only; `modules/sitecam.py` passes the origin.
- ✅ **Gmail Part 2** — `modules/auth.py` `_after_login_redirect()` auto-triggers
  the one-time `gmail.modify` connect after login (identity-only login kept; see
  `docs/SSO.md §10`).
- ✅ **SiteCam verify side** — `main aa643c1`: `auth.service.ts ssoLogin()` (tenant
  from the *signed* claim, not the host → fixes the La-Gala 401), `POST
  /api/auth/sso`, web bootstrap `apps/web/src/lib/sso.ts`, `render.yaml`
  `VITE_SSO_PARENT_ORIGINS`, and a test `apps/api/test/sso.test.ts`.
- ✅ Contract documented — `docs/SSO.md`.

### Remaining — operator/integrator (no code) 🟡
1. **Shared secret.** Set `SEABREEZE_CRM_WEBHOOK_SECRET` to the **same** strong
   value in CRM (Vercel) **and** SiteCam (Render). On SiteCam, redeploy **once**
   with `SEED_FORCE=true` so the encrypted `Tenant.webhookSecretEnc` row is
   rewritten. Until set, both sides fall back to the dev default
   `seabreeze-webhook-secret` (works, insecure — the `/sso/token` response flags
   `"dev_secret": true`).
2. **Google console** (only for SiteCam's standalone Google button; SSO doesn't
   need it): add `https://sitecam-web.onrender.com` to Authorized JavaScript
   origins, and the four CRM redirect URIs (see `docs/SSO.md §12` / the OAuth
   note). Needs Danny's Google account.
3. **Deploy + verify.** CRM SSO is on `gc-consolidation`, not `main` — ship it
   (integrator's call). Then: log into CRM → Site Photos tab → confirm auto-login
   shows the SeaBreeze tenant with no separate login. Run `sso.test.ts`.

---

## 2. Roof-Measurement Engine ⇄ CRM

**What it does:** the in-house measurement app pushes finished roof reports into
the CRM (auto-match + store + attach + parse), and (future) embeds with SSO.

### Done (committed, live)
- ✅ **Inbound ingest** — `POST /measurements/ingest` (`modules/measurements.py`,
  commit `bf359bb`): HMAC `X-Signature` over raw body with
  `MEASURE_CRM_WEBHOOK_SECRET`; matches by `job_id|lead_id|external_ref|address|name`;
  upserts the `measurements` row; attaches the PDF under Documents; auto-parses
  gaps; idempotent. In `auth.PUBLIC` (server-to-server, no session).
- ✅ **Handoff spec** — `docs/MEASUREMENT_INGEST.md` (self-contained: contract,
  Python + Node signers, self-signing smoke test where `404 no_match` = success).

### Remaining — depends on the app being built ⛔ / 🔎
1. **App's `/auth/sso` verify** — implement the `docs/SSO.md §3` steps (copy
   SiteCam's `ssoLogin`). Contract is fixed; nothing CRM-side changes.
2. **Enable the registry slot** — uncomment the `"measure"` entry in
   `modules/sso.py:92`, set `MEASURE_URL` + `MEASURE_CRM_WEBHOOK_SECRET`, add its
   origin to the embed page's postMessage target + its own
   `VITE_SSO_PARENT_ORIGINS`.
3. **Point the app's "report finished" webhook** at `/measurements/ingest` per
   `docs/MEASUREMENT_INGEST.md`.

> **Shared secret — RESOLVED (2026-06-10).** `MEASURE_CRM_WEBHOOK_SECRET` is now
> **SET** to a strong value on Render (it was never on Vercel, so both sides used the
> documented `seabreeze-webhook-secret` fallback — a forgery hole). The engine VM must
> sign with the **same** value — *not* the fallback, *not* `SEABREEZE_…`. This
> supersedes any earlier "copy the Render MEASURE value" note that predated it being set.
> Verify: a signed push returns `404 no_match` (auth passed) rather than `401`.

> Nothing here is blocked on the CRM — the seam is ready to receive. It's blocked
> only on the measurement app existing.

---

## 3. Permit Builder ⇄ Roof Engine

**What it does:** the permit wizard builds an AHJ-specific packet PDF, pulling
roof geometry from the job's measurement.

### Done (committed)
- ✅ **Consumes the measurement** — `modules/permits.py:164-168` reads
  `squares`/`pitch` from the job's measurement (`for_job`) when the job lacks
  them, attaches the RoofGraf report, writes the packet to Documents, logs it.
  So the roof engine's output already flows into permits via the shared
  `measurements` table — ingesting a report makes its geometry available to the
  builder with no extra wiring.
- ✅ **E-signature safety** — `permits.py:148` (commit `5d027cb`): the captured
  owner e-signature is deliberately **not** forwarded into the packet; consent
  text corrected (`a3772db`). Authority: `docs/PERMIT_SIGNATURE.md`.

### Remaining — review/decision 🔎
1. **Geometry depth.** The builder reads `squares` + `pitch` only. If an AHJ's
   wind/uplift worksheet needs ridge/hip/valley/eave LF or facets (now present in
   `measurements`), decide whether to feed them in. (PE-seal rule still applies to
   wind calcs — see memory `project_permit_packet_system`.)
2. **SiteCam photos → permit packet.** Not wired. Permits attach the RoofGraf
   report, not field photos. If any AHJ submittal needs site/progress photos,
   that's a new tie-in (SiteCam already exposes per-project galleries). Decision
   needed before building.
3. **System/deck pairing + PE-seal** logic — confirm still correct after the
   e-sig commits.

---

## Cross-cutting (integrator owns)

- **Merge to `main`:** `gc-consolidation` is ~79 ahead and carries GC/portal/sitecam
  work **and** SSO/measurement/permit features intermixed. Two SSO branches exist
  (`agent/unified-sso`, `feat/unified-sso`) — dedupe against this branch. Decide
  merge order so the agent branches don't collide.
- **Uncommitted on `gc-consolidation`:** `modules/sitecam.py` (changes atop the
  SSO-bus commit), plus untracked `WIP_NOTICE.md`, `billing_console.txt`,
  `scripts/import_closed.py`, `scripts/import_contacts.py`. Commit or gitignore
  before any clean redeploy (a checkout would lose the uncommitted edits). Delete
  `WIP_NOTICE.md` once `sitecam.py` lands — it's transient, not a status board.
- **Deploy:** one owner per deploy, on explicit go. Any CRM deploy ships the whole
  `gc-consolidation` tree; any SiteCam push to `main` triggers Render.

## Single critical detail per seam
- SSO: secret must be **identical** on both sides; SiteCam needs `SEED_FORCE=true`
  once after setting it.
- Ingest: header is `X-Signature` with a **bare hex** digest (no `sha256=`), over
  the **exact** transmitted bytes.
- Permit: never forward the captured e-signature into the packet.

---

## Hosting — Render migration (verified 2026-06-10) ✅
Moved CRM off **3 vendors → 1**: Vercel + Neon + Google Drive → a single **Render**
service `collab-crm` (https://collab-crm-bwsl.onrender.com), **SQLite + uploads on one
5 GB persistent disk** (`/data`). Drive kept only as a read-fallback for old uploads.

- **Verified:** 26,258 rows loaded Neon→SQLite (jobs 1231, leads 309, est 177, ws 580 +
  3972 lines, inv 429, pay 178, meas 850, lib 146, users 6); dashboard/pipeline/to-dos/$
  correct; the "saved-but-won't-open" doc bug **fixed** (real roof-report PDF served from
  Drive on Render).
- **`render.yaml`** is the blueprint. **`DATABASE_URL` is intentionally OMITTED** →
  `db.IS_PG=False` → SQLite. **Do NOT add `DATABASE_URL` to Render** or it reverts to Neon.
- `config.py` reads `CRM_DB_PATH`/`CRM_DATA_DIR`/`CRM_UPLOAD_DIR` (under `/data`); WAL mode
  for the 2-worker gunicorn. One-time export: `scripts/neon_to_sqlite.py` (resumable).
- Render secrets (not in git): `GDRIVE_SA_JSON` (base64), `GDRIVE_FOLDER_ID`,
  `GOOGLE_OAUTH_CLIENT_ID/SECRET`, `SEABREEZE_CRM_WEBHOOK_SECRET`, and now
  `MEASURE_CRM_WEBHOOK_SECRET` (strong; see §2). Google OAuth: added 4 redirect URIs
  (`/auth/google/callback` + `/gmail/callback` × onrender + custom domain).
- **Branch:** Render builds `agent/gc-consolidation`; `main` is fast-forwarded to it on
  GitHub (same commit), so switching Render → `main` after merge is a content no-op.
- **Pending, non-blocking:** DNS cutover of `crm.collaborativeconceptsfl.com` Vercel→Render
  (Vercel stays as instant rollback); optional Drive→`/data/uploads` backfill to drop Drive.
