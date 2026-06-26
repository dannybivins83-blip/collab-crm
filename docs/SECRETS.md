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
| `SEABREEZE_CRM_WEBHOOK_SECRET` | HMAC for SSO / SiteCam | Render, Vercel, **sitecam-api** | 🔴 **LEAKED — rotate.** Fresh value on both sides, then redeploy sitecam-api once with `SEED_FORCE=true`. |
| `GOOGLE_OAUTH_CLIENT_SECRET` | Gmail + Google sign-in | Render, Vercel | 🔴 **LEAKED (screenshot) — rotate** in Google Console, then update env. |
| `GOOGLE_OAUTH_CLIENT_ID` | OAuth client id (not secret, but paired) | Render, Vercel | ✅ Set. |
| `R2_ACCOUNT_ID` | Cloudflare R2 account ID (32-char hex) | Render | ⬜ **Pending** — create bucket at dash.cloudflare.com/r2, then set |
| `R2_ACCESS_KEY_ID` | R2 API token access key | Render | ⬜ Pending |
| `R2_SECRET_ACCESS_KEY` | R2 API token secret | Render | ⬜ Pending |
| `R2_BUCKET_NAME` | R2 bucket name (e.g. `crm-files`) | Render | ⬜ Pending |
| `GDRIVE_FOLDER_ID`, `GDRIVE_SA_JSON` | Drive file-storage legacy fallback (keep until R2 backfill complete) | Render, Vercel | ✅ Set. |
| `SMTP_FROM` | Gmail address for CRM outbound notifications (e.g. `seabreezeroof@gmail.com`) | Render | 🔴 Missing — portal emails + lead notifications silent |
| `SMTP_PASSWORD` | Gmail App Password for `SMTP_FROM` account | Render | 🔴 Missing — generate at myaccount.google.com/apppasswords |
| `ANTHROPIC_API_KEY` | Claude AI — ZIP/PDF parse-zip + AI Plans Takeoff (`/leads/<id>/takeoff`) | Render, Vercel | ✅ Set on Render. Set on Vercel before next deploy. |
| `CRM_INTAKE_TOKEN` | Enables `/leads/intake*` (503 until set) | Render, Vercel | ⬜ Optional — set when you want intake live. |
| `ROOF_ENGINE_URL`, `ROOF_ENGINE_API_KEY`, `ROOF_BRAND` | Roof-report engine link | Render, Vercel | 🟢 **rotated 2026-06-26** (fresh token_urlsafe; old leaked key dead). Engine reads `ROOF_API_KEYS` on the VM = this value; insecure dev-key fallback closed. URL/brand not secret. |
| `CRON_SECRET` | Guards `/sync/cron` | Render | ⬜ Set if using the background cron. |
| `QXO_API_BASE`, `QXO_API_KEY` | QXO/Beacon materials (dark scaffold) | — | ⬜ Not live. Fill only after `go.qxo.com/qxoapi` partner access. |

## Where each host's env lives
- **Render (`collab-crm`):** Dashboard → Environment. `RENDER=true` ⇒ `IS_PROD` ⇒ integration secrets fail **closed** when unset (reject, don't fall back).
- **Vercel:** Project → Settings → Environment Variables. Set `CRM_ENV=production` so `IS_PROD` is true there too. **Set the secrets before any `vercel --prod`** or sync/ingest will 401.
- **Engine VM (`150.136.152.240`):** the engine agent sets `MEASURE_CRM_WEBHOOK_SECRET` in its own env — give it the *fact* it's set, not the value.
- **Google Console:** https://console.cloud.google.com/apis/credentials — OAuth client secret + redirect URIs.

## Rotation checklist (when a value is burned)
1. Generate a fresh value privately.
2. Update `secrets/keys.local.env`.
3. Update every host in the "Set on" column.
4. For `SEABREEZE_*`: redeploy sitecam-api with `SEED_FORCE=true` once, then flip back.
5. For Google: reset in Google Console first, then propagate.
