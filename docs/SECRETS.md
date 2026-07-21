# Secrets Registry

The **single index of every secret the CRM uses** — what it's for, where it must be set,
and its current status. **This doc contains NO values** (safe to commit). The real values
live in exactly two places:

1. **`secrets/keys.local.env`** — your local source of truth (`.gitignored`, never committed).
2. **Each host's dashboard env** — Render, Vercel, the engine VM, Google Console.

## The rule (why this exists)
Secrets kept leaking by being **pasted into chat**. So:
- **Never** put a value in chat, a screenshot, a commit, or any doc — only in the two places above.
- **Generate privately:** Render's "Generate" button, or `python -c "import secrets; print(secrets.token_hex(32))"`.
- **If a value ever appears in chat/transcript/screenshot, it is BURNED** — regenerate and replace it everywhere.
- Agents may **read** `secrets/keys.local.env` to *use* a value (e.g. set it on a dashboard); they must **never echo it back** into the conversation.

## Registry

| Key | Purpose | Set on | Status |
|---|---|---|---|
| `CRM_SECRET` | Flask session signing | Render, Vercel | ⚠️ **Audit #5** — falls back to public `white-label-crm-dev-secret` if unset (forgeable admin cookies). Set it. |
| `CRM_SYNC_SECRET` | Gates `/sync/*` bookmarklet bridge (Critical #1) | Render, Vercel | 🔴 Code shipped (fail-closed in prod). **Set on Render now**, Vercel before its next deploy. Reinstall bookmarklets after. |
| `MEASURE_CRM_WEBHOOK_SECRET` | HMAC for `/api/takeoff` + `/measurements/ingest`; **must match the engine VM** | Render, Vercel, **engine VM** | 🔴 **PENDING.** Earlier value was leaked in chat = burned. Generate fresh, set identical CRM+VM. Nothing live-signs yet, so safe to set anytime. |
| `SEABREEZE_CRM_WEBHOOK_SECRET` | HMAC for SSO / SiteCam | Render, Vercel, **sitecam-api** | 🟢 **Rotated 2026-06-26** (fresh 64-char hex; SEED_FORCE=true applied on sitecam-api in same deploy, then removed). Sitecam lane to verify SSO end-to-end. |
| `GOOGLE_OAUTH_CLIENT_SECRET` | Gmail + Google sign-in | Render, Vercel | 🔴 **LEAKED (screenshot) — rotate** in Google Console, then update env. |
| `GOOGLE_OAUTH_CLIENT_ID` | OAuth client id (not secret, but paired) | Render, Vercel | ✅ Set. |
| `DB_RESTORE_TOKEN` | Gates `/admin/db-restore` (upload) **and** `/admin/db-download` (local `scripts/sync_live_db.py` pulls the live DB before local runs) | Render + local env file | 🟢 Set on Render 2026-07-20 (same value both sides; endpoints are fail-closed 404 without it) |
| `R2_ACCOUNT_ID` | Cloudflare R2 account ID (32-char hex) | Render | ⬜ **Pending** — create bucket at dash.cloudflare.com/r2, then set |
| `R2_ACCESS_KEY_ID` | R2 API token access key | Render | ⬜ Pending |
| `R2_SECRET_ACCESS_KEY` | R2 API token secret | Render | ⬜ Pending |
| `R2_BUCKET_NAME` | R2 bucket name (e.g. `crm-files`) | Render | ⬜ Pending |
| `GDRIVE_FOLDER_ID`, `GDRIVE_SA_JSON` | Drive file-storage legacy fallback (keep until R2 backfill complete) | Render, Vercel | ✅ Set. **Local copy repaired 2026-07-20** — see the multi-line trap below. |
| `SMTP_FROM` | Gmail address for CRM outbound notifications | Render | ✅ **Set** (verified 2026-07-01 via Render API) — SMTP fallback send path is live. |
| `SMTP_PASSWORD` | Gmail App Password for `SMTP_FROM` account | Render | ✅ **Set** (verified 2026-07-01). |
| `ANTHROPIC_API_KEY` | Claude AI — ZIP/PDF parse-zip + AI Plans Takeoff (`/leads/<id>/takeoff`) | Render, Vercel | ✅ Set on Render. Set on Vercel before next deploy. |
| `CRM_INTAKE_TOKEN` | Enables `/leads/intake*` (503 until set) | Render, Vercel | ✅ **Set on Render, verified live** (2026-07-01: `POST /leads/intake` returns 403 bad-token, not 503 disabled — intake is armed). |
| `GOOGLE_MAPS_API_KEY` | Places autocomplete on lead/job address fields (client-side, referrer-restricted) | Render | ✅ **Set on Render, verified live** (2026-07-01: Places API (New) Text Search confirmed working; autocomplete script renders on `/leads/new` + `/jobs/new`). |
| `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_WEBHOOK_SECRET` | Native Stripe Checkout card payments (`modules/payments_stripe.py`) | Render | ⬜ **Not set on Render — feature dark by design (fail-closed).** Code built + test-verified (real `sk_test` checkout session created, webhook HMAC verified). The only Stripe key on this machine is a **personal test sandbox**, not SeaBreeze's live "cc" account — do NOT deploy it to prod. Activate by setting the 3 real `sk_live`/`pk_live`/`whsec_` values from the live Stripe dashboard on Render. |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM` | SMS channel for the follow-up drip engine (`modules/comms.py` send_sms) | Render | ⬜ **Not set anywhere — no Twilio account exists yet.** Code is fail-closed (logs an 'sms' activity, never crashes, when unset). Needs a new Twilio (or Vonage/Telnyx/Plivo) account + a purchased number; checked for an existing RingCentral SMS shortcut — RingCentral's own API app on this account ("ghl") is a separate live integration (likely GoHighLevel), not reusable for this without risking that integration. |
| `SITECAM_API_KEY_WWS` | Auth between WWS frontend and sitecam-api | Render (sitecam-api), Vercel (collaborativeconcepts) | ✅ **Set + matched on both hosts, verified 2026-07-01** (sitecam-api was also boot-crashing on a separately-wiped env — fixed same session; service confirmed live via 401 on an auth-gated route). |
| `ROOF_ENGINE_URL`, `ROOF_ENGINE_API_KEY`, `ROOF_BRAND` | Roof-report engine link | Render, Vercel | 🟢 **rotated 2026-06-26** (fresh token_urlsafe; old leaked key dead). Engine reads `ROOF_API_KEYS` on the VM = this value; insecure dev-key fallback closed. URL/brand not secret. |
| `CRON_SECRET` | Guards `/sync/cron` | Render | ⬜ Set if using the background cron. |
| `QXO_API_BASE`, `QXO_API_KEY` | QXO/Beacon materials (dark scaffold) | — | ⬜ Not live. Fill only after `go.qxo.com/qxoapi` partner access. |

## Where each host's env lives
- **Render (`collab-crm`):** Dashboard → Environment. `RENDER=true` ⇒ `IS_PROD` ⇒ integration secrets fail **closed** when unset (reject, don't fall back).
- **Vercel:** Project → Settings → Environment Variables. Set `CRM_ENV=production` so `IS_PROD` is true there too. **Set the secrets before any `vercel --prod`** or sync/ingest will 401.
- **Engine VM (`150.136.152.240`):** the engine agent sets `MEASURE_CRM_WEBHOOK_SECRET` in its own env — give it the *fact* it's set, not the value.
- **Google Console:** https://console.cloud.google.com/apis/credentials — OAuth client secret + redirect URIs.

## ⚠ The multi-line trap in `keys.local.env` (bit us 2026-07-20)
`scripts/run_local.py::load_secrets` treats any line that doesn't start a new `KEY=` as a
**continuation and rejoins it with `\n`**. That's needed for PEM/JSON blobs — but it
**corrupts a base64 value that was pasted across several lines**: the newlines get baked in
and the string no longer decodes.

**Symptom:** `GDRIVE_SA_JSON` was stored across 35 lines. `load_secrets` returned 4,737 chars
(true value: 3,112) with `len % 4 == 1` — impossible for valid base64. `gdrive.enabled()`
silently returned **False**, so `gdrive.download()` returned `None` and **every local Drive
download failed silently** — no exception, no log, just empty files.

**Fix applied:** recovered the known-good value from Render via the API (no new key ⇒ nothing
rotated, nothing else broke), re-stored it as **one single line** of base64, verified
`enabled()` → True and a real 1.56 MB PDF downloaded. Backup at `keys.local.env.bak-<epoch>`.

**Rule:** store every secret in `keys.local.env` as a **single line**. Base64-encode anything
that contains newlines (JSON, PEM). If a Drive/API call starts returning empty instead of
erroring, check the value's length `% 4` before assuming the key is revoked.

## Rotation checklist (when a value is burned)
1. Generate a fresh value privately.
2. Update `secrets/keys.local.env`.
3. Update every host in the "Set on" column.
4. For `SEABREEZE_*`: redeploy sitecam-api with `SEED_FORCE=true` once, then flip back.
5. For Google: reset in Google Console first, then propagate.
