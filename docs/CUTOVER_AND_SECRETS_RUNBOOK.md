# Cutover & Secrets Runbook (you click — agent can't reach these systems)

Written 2026-06-10. The agent only has the Vercel CLI authed (`dannybivins83-blip`); it
has **no** Cloudflare / Render / GitHub / engine-VM access. Each step below is something
**you** run at a dashboard or shell. **No secret values live in this file** — you generate
fresh ones locally and paste them into the dashboards. Never paste a secret into a chat.

Verified preconditions (2026-06-10): git unified at `8f2c2c1` on all three refs; Render
`collab-crm-bwsl.onrender.com` serves `/api/takeoff` + `/measurements/ingest` (OPTIONS→200);
`crm.collaborativeconceptsfl.com` still resolves to **Vercel** (`Server: Vercel`, IPs
216.198.79.65 / 64.29.17.1 via Cloudflare DNS-only).

**Generate any secret with** (cross-platform, no openssl needed):
```
python -c "import secrets; print(secrets.token_hex(32))"
```

**Urgency:** the two leaked secrets (§3, §4) are security-urgent — do them first. The DNS
cutover (§1) is at-your-leisure: Vercel works today and is the rollback.

---

## §0 — Sequencing & the rollback caveat (read first)
- Set every rotated/new secret on **BOTH CRM hosts (Render *and* Vercel)** + the peer system
  (SiteCam / engine VM). Reason: the domain can serve either host (Vercel is the rollback),
  so if the secret only lives on one, a rollback silently desyncs SSO/ingest auth.
- Recommended order: **§3 + §4 (rotate leaked) → §2 (MEASURE strong) → §1 (DNS cutover) → §5 (revoke PAT, last)**.
  Rotating before cutover means Render is fully provisioned when the domain lands on it.

---

## §1 — DNS cutover: Vercel → Render

**Precondition (do NOT skip — avoids a TLS gap):** add the custom domain on Render and let its
cert issue *before* flipping DNS.
1. Render dashboard → service **collab-crm** → **Settings → Custom Domains → Add** →
   `crm.collaborativeconceptsfl.com`. Render shows a **DNS target** (a CNAME like
   `collab-crm-bwsl.onrender.com`, or an A/ALIAS — use exactly what Render shows).
2. Render will say "verifying / issuing certificate." It can't fully issue until DNS points
   at it, but add it now so issuance starts the moment you flip.

**Flip DNS at Cloudflare** (NS = `pranab.ns.cloudflare.com`):
3. Cloudflare dashboard → zone **collaborativeconceptsfl.com** → **DNS → Records**.
4. Edit the `crm` record (currently pointing at Vercel). Replace with Render's target from
   step 1. **Keep it DNS-only (grey cloud)** to start — proxying can interfere with Render's
   ACME. TTL: drop to **Auto/120s** for a fast revert.
5. Wait for issuance: Render's Custom Domains panel turns green ("Certificate issued").
   Typical 2–15 min.

**Verify:**
```
nslookup crm.collaborativeconceptsfl.com          # should resolve to Render's target now
curl -sI https://crm.collaborativeconceptsfl.com/ | findstr /I "server"   # expect: render (not Vercel)
curl -s -o NUL -w "%{http_code}\n" -X OPTIONS https://crm.collaborativeconceptsfl.com/api/takeoff   # 200
```
Log in, click a job, open a portal link — confirm uploads/photos render (Render serves from
the /data disk + Drive read-fallback).

**Rollback (instant):** revert the `crm` Cloudflare record to the Vercel target (the old
value — screenshot it before editing in step 4). Low TTL makes this ~minutes. Vercel is left
running untouched, so rollback is just the DNS edit.

---

## §2 — MEASURE secret: strong value on both sides (your decision: strong, both sides)

Today both hosts fall back to the public-ish `seabreeze-webhook-secret` (because
`MEASURE_CRM_WEBHOOK_SECRET` is unset → `_ingest_secret()` derives `{SSO_TENANT_KEY|seabreeze}-webhook-secret`).
Nothing live currently signs `/api/takeoff` (the engine push is dormant), so this is safe now.

1. Generate one strong value → call it `$MEASURE`.
2. **Render** → collab-crm → Environment → set `MEASURE_CRM_WEBHOOK_SECRET = $MEASURE` → save (redeploys).
3. **Vercel** (so rollback stays valid):
   ```
   vercel env add MEASURE_CRM_WEBHOOK_SECRET production   # paste $MEASURE when prompted
   vercel --prod                                          # redeploy to pick it up
   ```
4. **Engine VM** (`150.136.152.240`) + **Estimator** `post_takeoff.py`: set the same `$MEASURE`
   as the signing secret. Sign exactly as the CRM verifies:
   `X-Signature: hex(hmac_sha256($MEASURE, raw_request_body))` (see `modules/measurements.py:_verify_sig`).
5. **Coordinate the flip:** because the CRM verifies against `MEASURE` the instant it's set,
   set it on the CRM host(s) and every signer (engine + Estimator helper) in the same window.
   Until the engine is wired, nothing breaks (it's dormant).

**Verify** (a correctly-signed body returns non-401; a wrong/blank signature returns 401):
```
# wrong sig should now be rejected:
curl -s -o NUL -w "%{http_code}\n" -X POST https://collab-crm-bwsl.onrender.com/api/takeoff \
  -H "X-Signature: deadbeef" -H "Content-Type: application/json" -d "{}"   # expect 401
```

---

## §3 — Rotate `SEABREEZE_CRM_WEBHOOK_SECRET` (LEAKED `7938…efbb87`)

This is the **SSO + SiteCam** shared secret (`modules/sso.py`, `modules/sitecam.py`). Both the
CRM and the SiteCam app must hold the **same** value or SSO breaks — rotate them **together**.

1. Generate `$SEABREEZE_NEW`.
2. **CRM — Render**: Environment → `SEABREEZE_CRM_WEBHOOK_SECRET = $SEABREEZE_NEW` → save.
3. **CRM — Vercel**: `vercel env rm SEABREEZE_CRM_WEBHOOK_SECRET production` then
   `vercel env add SEABREEZE_CRM_WEBHOOK_SECRET production` → `vercel --prod`.
4. **SiteCam app** (separate Render account, `sitecam-api.onrender.com`): set its
   `SEABREEZE_CRM_WEBHOOK_SECRET = $SEABREEZE_NEW` → redeploy. Do this in the **same window** as 2–3.
5. **Verify SSO end-to-end:** from the CRM, open the SiteCam/measurement SSO hand-off (the
   "View project photos" / measurement-app launch). A successful round-trip = secrets match.
   If it 401s, one side still has the old value.

---

## §4 — Rotate the roof-engine API key (LEAKED `71tk…`)

CRM→engine auth: the CRM sends `X-API-Key: $ROOF_ENGINE_API_KEY` (`modules/roof_reports.py:46`).

1. On the **engine VM** (`150.136.152.240`): issue a new X-API-Key (rotate in the engine's own
   config/secret store); invalidate `71tk…`.
2. Update `ROOF_ENGINE_API_KEY` on **both CRM hosts**: Render env + `vercel env add ROOF_ENGINE_API_KEY production` → `vercel --prod`.
3. **Verify:** in the CRM, Roof Reports → start a report for a test address → it should reach
   the engine (no "Could not reach the roof engine" / 401).

⚠️ **In-code leak vector — worse than the rotated key, fix it too:** `modules/roof_reports.py:111-112`
builds `takeoff_url = f"{ENGINE_URL}/takeoff?api_key={ENGINE_KEY}"` and ships it into **client-side
JS** (`roof_reports_detail.html:18` → `const TAKEOFF = {{ takeoff_url | tojson }}`). So the engine
key reaches the **browser** — directly contradicting the module's own docstring (`roof_reports.py:6`:
"the API key never reaches the browser"). A header won't fix a browser-opened link; the real fix is
to **proxy the takeoff call through the CRM server** (key stays server-side) or mint a **short-lived
signed token** the browser carries instead of the raw key. Ask the agent to patch this — it's a
clean in-repo change.

---

## §5 — Revoke the `roof-crm-deploy` GitHub PAT (do LAST, after §1–§4 verified)

The repo's git remote is clean (`https://github.com/dannybivins83-blip/collab-crm.git`, no
embedded token); pushes auth via **Windows Credential Manager** (`credential.helper=manager`),
so the PAT is *not* in the repo. Revoke it on GitHub:
1. GitHub → Settings → Developer settings → **Personal access tokens** → find `roof-crm-deploy` → **Revoke**.
2. Your next `git push` will re-prompt: create a fresh fine-scoped PAT (repo: `collab-crm` only)
   and let Credential Manager store it. Push a trivial commit to confirm auth still works.
3. Only revoke once §1–§4 are confirmed (in case any deploy needs a push mid-cutover).

---

## §6 — Audit #1 / #2 code landed: new env + deploy notes

The security fix (commit lands on `agent/gc-consolidation`) gates the AccuLynx→CRM
bookmarklet bridge and makes the signed seams **fail closed in prod**. That adds env +
a re-install step, and the deploy is fail-closed — sequence it:

**New / required env (set BEFORE or WITH the deploy, else these 401):**
1. **`CRM_SYNC_SECRET`** — generate one (`python -c "import secrets;print(secrets.token_hex(32))"`),
   set on **Render** and **Vercel**. Gates `/sync/*` + `/leads/import`. Unset in prod ⇒ the
   whole bookmarklet bridge returns 401 (fail closed).
2. **`CRM_ENV=production` on Vercel** (Render needs nothing — it auto-sets `RENDER=true`).
   Without it, Vercel can't tell itself apart from the local `.env` (which is `vercel env
   pull`-ed and carries `VERCEL=1`/`VERCEL_ENV`), so it would NOT fail closed. Set via
   `vercel env add CRM_ENV production` → `vercel --prod`.
3. **`CRON_SECRET`** (both hosts) — `/sync/cron` is otherwise an open sync trigger. Vercel
   Cron sends it automatically once set; an external scheduler must use `?key=$CRON_SECRET`.
4. `MEASURE_CRM_WEBHOOK_SECRET` (§2) is now **mandatory in prod** — unset ⇒ `/api/takeoff`
   + `/measurements/ingest` reject ALL requests (was: forgeable fallback). `SEABREEZE_*` is
   already set, so SSO keeps working; if it were ever unset, SSO mint now 503s instead of
   using the guessable fallback.

**Re-install the bookmarklets:** open the **Sync** page (logged in) and re-download the
"all bookmarks" folder — the new bookmarklets carry `?k=$CRM_SYNC_SECRET`. Old (un-keyed)
bookmarks will 401 after deploy.

**Verify after deploy + secrets set:**
```
# bridge rejects anonymous, accepts the key:
curl -s -o NUL -w "%{http_code}\n" -X POST https://collab-crm-bwsl.onrender.com/sync/catalog-import      # 401
curl -s -o NUL -w "%{http_code}\n" "https://collab-crm-bwsl.onrender.com/sync/job-guids?k=$CRM_SYNC_SECRET"  # 200
# forged measurement signature rejected:
curl -s -o NUL -w "%{http_code}\n" -X POST https://collab-crm-bwsl.onrender.com/api/takeoff -H "X-Signature: deadbeef" -d "{}"  # 401
```
Then click a sync bookmarklet on a my.acculynx.com tab — the banner should still import.

---

## Done-when
- [ ] §3 SiteCam SSO round-trips with the new SEABREEZE secret (both hosts + SiteCam)
- [ ] §4 Roof Reports reaches the engine with the new key; query-string leak patched
- [ ] §2 `MEASURE_CRM_WEBHOOK_SECRET` set on Render+Vercel+engine+Estimator; bad-sig POST → 401
- [ ] §1 `crm.collaborativeconceptsfl.com` serves `Server: render`; `/api/takeoff` OPTIONS→200; portal/uploads OK
- [ ] §5 `roof-crm-deploy` PAT revoked; fresh PAT pushes fine
