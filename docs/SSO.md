# Unified Single Sign-On (SSO) — Connected Apps Contract

The CRM is the **identity provider**. When a user is logged into the CRM (by
Google or password), they are automatically signed into every **connected app**
— SiteCam today, a roof-measurement app tomorrow — with no separate login.

This document is the contract. Any new app that implements the **verify steps**
below and is added to the CRM **registry** plugs in with no other CRM changes.

---

## 1. Roles

| Side | Role | Where |
|------|------|-------|
| Identity provider (mint) | CRM | `whitelabel-crm/modules/sso.py` |
| Handoff (parent page) | CRM SiteCam embed | `whitelabel-crm/templates/sitecam.html` |
| Relying party (verify) | SiteCam | `sitecam/apps/api/src/auth/auth.service.ts` (`ssoLogin`) + `apps/web/src/lib/sso.ts` |

---

## 2. The assertion

A compact, JWS-like token. Both sides sign/verify the **exact base64url string**,
so there is no JSON canonicalization to disagree on.

```
assertion = base64url(claims_json) + "." + hex( HMAC-SHA256( base64url(claims_json), secret ) )
```

- `base64url` = URL-safe base64, **no padding** (`=`) — RFC 4648 §5.
- `secret` = the per-tenant shared secret (see §4). Never in the token, URL, or logs.
- Signature = lowercase hex HMAC-SHA256 over the base64url payload bytes.

### Claims

| Claim    | Type   | Meaning |
|----------|--------|---------|
| `v`      | int    | Schema version. Currently `1`. |
| `iss`    | string | Issuer. Always `"crm"`. |
| `app`    | string | Audience — the connected app id (`"sitecam"`). Verifier MUST reject a mismatch. |
| `tenant` | string | Tenant key the user belongs to (`"seabreeze"`). **Authoritative** — see §6. |
| `email`  | string | User email, lowercased. |
| `name`   | string | Display name. |
| `role`   | string | CRM role (`admin`/`sales`/`production`/`office`). Mapped per §7. |
| `iat`    | int    | Issued-at (Unix seconds). |
| `exp`    | int    | Expiry (Unix seconds). `exp - iat ≤ 120`. |
| `nonce`  | string | Random, single-use. Verifier MUST reject replays. |

TTL is **90 seconds** in practice (hard ceiling 120s).

---

## 3. Verify steps (relying party)

A connected app's `/auth/sso` endpoint MUST, in order:

1. Split on the **first** `.` → `payloadB64`, `sig`. Reject if malformed.
2. base64url-decode `payloadB64` → `claims`. Reject if unreadable.
3. Reject unless `claims.v === 1`, `claims.app === <this app's id>`, and
   `claims.tenant` + `claims.email` are present.
4. Resolve the tenant **named in `claims.tenant`** (not from the request host).
5. Load that tenant's shared secret (§4). Reject if none.
6. Recompute `HMAC-SHA256(payloadB64, secret)` and compare to `sig` in
   **constant time**. Reject on mismatch. *(A forged tenant fails here — wrong
   secret.)*
7. Reject if `exp < now`, `iat > now + 60` (skew), or `exp - iat > 120`.
8. Reject if `nonce` was already used (store nonces per tenant; unique index).
9. **Find-or-provision** the user by email in that tenant.
10. Issue the app's own session tokens.

SiteCam's implementation: `AuthService.ssoLogin()` (`apps/api/src/auth/auth.service.ts`),
exposed at `POST /api/auth/sso` (`auth.controller.ts`). Nonce replay uses the
existing `ProcessedWebhook` table (`externalId = "sso:<nonce>"`, unique per
tenant). Tenant lookup is `TenantService.getByKey()` in system context; the user
find/provision + token issue run inside `TenantContext.run(tenantId, …)`.

---

## 4. Trust — the shared secret

The signing secret is the **same value** as the connected app's per-tenant CRM
**webhook secret**. Reusing the existing trust bridge means no new key material.

| Tenant | CRM env var | SiteCam env var | SiteCam storage |
|--------|-------------|-----------------|-----------------|
| seabreeze | `SEABREEZE_CRM_WEBHOOK_SECRET` | `SEABREEZE_CRM_WEBHOOK_SECRET` | encrypted into `Tenant.webhookSecretEnc` at seed time |
| lagala | `LAGALA_CRM_WEBHOOK_SECRET` | `LAGALA_CRM_WEBHOOK_SECRET` | same |

- **CRM** reads the secret from the env var named in the registry
  (`modules/sso.py` → `secret_env`). If unset it falls back to the documented dev
  default `"<tenant>-webhook-secret"` (which the SiteCam seed also uses), and the
  `/sso/token` response includes `"dev_secret": true` so it's obvious the prod
  secret isn't configured. **Production must set a real secret on both sides.**
- **SiteCam** reads it from `Tenant.webhookSecretEnc` (decrypted with
  `CREDENTIAL_ENC_KEY`). The seed encrypts `process.env[<tenant>_CRM_WEBHOOK_SECRET]`
  into the row. ⚠️ **Changing the secret after the first seed requires a redeploy
  with `SEED_FORCE=true`** so the encrypted row is rewritten.

The CRM also exposes its own tenant key via `SSO_TENANT_KEY` (defaults to
`seabreeze`). A La Gala CRM would set `SSO_TENANT_KEY=lagala`.

---

## 5. Handoff — origin-checked `postMessage`

The raw token is **never** put in the iframe `src` URL. Instead:

```
CRM parent page (templates/sitecam.html)          SiteCam iframe (apps/web/src/lib/sso.ts)
─────────────────────────────────────────────────────────────────────────────────────────
                                          ← postMessage {type:"sitecam-sso-ready"}   (on boot, no session)
GET /sso/token/sitecam  → {assertion, origin}
postMessage {type:"crm-sso-assertion",
             assertion}  → (to SiteCam origin only)
                                          → POST /api/auth/sso {assertion}
                                          → store tokens, log in
                                          ← postMessage {type:"sitecam-sso-ok"}
```

Origin checks (both directions):
- CRM posts the assertion **only** to SiteCam's exact origin (`urlparse(sitecam_url)`),
  never `"*"`, and only honors `ready`/status pings from that origin.
- SiteCam **only accepts** `crm-sso-assertion` from an allowlisted CRM origin
  (`VITE_SSO_PARENT_ORIGINS`, default `https://crm.collaborativeconceptsfl.com`;
  `localhost`/`127.0.0.1` always allowed in dev). The readiness ping is
  content-free, so posting it to the parent is safe.

Message types: `sitecam-sso-ready` (child→parent, request token), `crm-sso-assertion`
(parent→child, deliver token), `sitecam-sso-ok` / `sitecam-sso-fail` (child→parent, status).

---

## 6. Tenant comes from the assertion (the bug fix)

The original failure: switching the SiteCam **theme** to "La Gala" set a local
`x-tenant` override, so a SeaBreeze email logged in against the La Gala tenant →
`401`. Under SSO the tenant is taken from the **signed `tenant` claim** and
verified with that tenant's secret, so the client can't pick the wrong tenant. On
the web side, after a successful exchange we **pin** the local tenant override to
the assertion's tenant so every subsequent authed request's `x-tenant` matches
the token's `tid` (otherwise `JwtAuthGuard` would 401 on a stale override). The
standalone login screen also now surfaces any active override with a one-click
reset.

---

## 7. Role mapping (provisioning only)

Applied **only when SSO provisions a brand-new user**; existing users keep their
SiteCam role (admins manage roles in-app afterward).

| CRM role | SiteCam role |
|----------|--------------|
| `admin` | `admin` |
| `office` | `manager` |
| `sales` | `manager` |
| `production` | `field` |
| *(anything else)* | `field` |

---

## 8. Connected-apps registry — adding an app

In the CRM, add one entry to `CONNECTED_APPS` (`modules/sso.py`):

```python
"measure": {
    "id": "measure",
    "name": "Roof Measurement",
    "base_url": (os.environ.get("MEASURE_URL") or "").strip().rstrip("/"),
    "secret_env": "MEASURE_CRM_WEBHOOK_SECRET",
    "sso_path": "/auth/sso",
    "embed": True,
},
```

Then on the new app: implement the §3 verify steps at its `/auth/sso`, set
`MEASURE_CRM_WEBHOOK_SECRET` to the same value on both sides, and (if embedded)
add its origin to the embed page's `postMessage` target + its own
`VITE_SSO_PARENT_ORIGINS` allowlist. Nothing else in the CRM changes.

The CRM mint endpoints:
- `GET /sso/token/<app_id>` → `{app, origin, assertion, expires_in, exp}` (login required).
- `GET /sso/apps` → embeddable apps + origins (login required).

---

## 9. Roof-measurement app (inbound auth: design · result push: built)

A measurement app is coming that auto-pulls roof reports. Two seams — the login
seam is designed (waiting on the app), the result-push seam is **already built**:

### (a) Inbound auth — same SSO *(design)*
Add the registry entry above; implement the §3 verify. The measurement app's web
UI embeds in the CRM exactly like SiteCam.

### (b) Outbound result — measurement push → CRM `measurements` ✅ BUILT & LIVE
When a report completes, the app pushes it back into the CRM for the matching
job/lead. This endpoint is **implemented and deployed**
(`modules/measurements.py` → `ingest()`; verified end-to-end: bad signature → 401,
valid push stored, no-match → 404).

**`POST /measurements/ingest`** (CRM, public, HMAC-verified; CORS preflight OK)

Header:
- `X-Signature: <hex>` — lowercase hex `HMAC-SHA256(MEASURE_CRM_WEBHOOK_SECRET,
  <raw request body bytes>)`, constant-time compared. **No `sha256=` prefix.**
  The signature is over the **exact bytes sent** (for multipart, sign the raw
  multipart body).

Shared secret: `MEASURE_CRM_WEBHOOK_SECRET` on both sides (same convention as the
SSO secret; dev fallback `"<tenant>-webhook-secret"`).

Body — **JSON** (or `multipart/form-data` with a `file` field for the PDF). Keys
are **flat** (not nested):

| Group | Keys |
|-------|------|
| Match (any one) | `job_id`, `lead_id`, `external_ref` (matched as a substring of the job/lead AccuLynx `external_url`), `address` (substring), `name` (exact, case-insensitive) |
| Measurements | `squares`, `pitch`, `stories`, `ridge_lf`, `hip_lf`, `valley_lf`, `rake_lf`, `eave_lf`, `step_flash_lf`, `facets`, `waste_pct`, `source` (defaults `"Measurement App"`), `notes` |
| PDF (optional, any one) | `report_url` (CRM fetches it), `pdf_base64`, or a multipart `file` + `filename` |

```json
{
  "external_ref": "AL-1042",
  "address": "4210 Palm Lakes Blvd, West Palm Beach, FL",
  "source": "RoofGraf",
  "report_url": "https://…/report.pdf",
  "squares": 38.5, "pitch": "6/12", "stories": "1",
  "ridge_lf": 120, "hip_lf": 0, "valley_lf": 64, "rake_lf": 88,
  "eave_lf": 140, "step_flash_lf": 22, "facets": 9, "waste_pct": 15
}
```

Behavior on receipt: **match** (job_id/lead_id → external_ref → address/name) →
**upsert** the lead's/job's `measurements` row (idempotent — re-pushes update the
same row) → **attach** the PDF under Documents (category `Measurement`) →
**auto-parse** the PDF to fill any measurement the app didn't send → log an
activity. Numeric fields are sanitized (stripped to digits/decimal).

Responses:
- `200 {ok:true, matched:"lead"|"job", record, id, squares}`
- `401 {ok:false, reason:"bad_signature"}`
- `404 {ok:false, reason:"no_match", hint:"send lead_id/job_id, external_ref, or address/name"}`

These measurements feed estimates (see `reference_measurement_workflow.md`).

#### Example signer — Python
```python
import hmac, hashlib, json, requests

SECRET = "…MEASURE_CRM_WEBHOOK_SECRET…"
URL = "https://crm.collaborativeconceptsfl.com/measurements/ingest"

body = json.dumps({
    "external_ref": "AL-1042",
    "address": "4210 Palm Lakes Blvd, West Palm Beach, FL",
    "source": "RoofGraf", "squares": 38.5, "pitch": "6/12",
    "ridge_lf": 120, "valley_lf": 64, "eave_lf": 140,
    "report_url": "https://example.com/report.pdf",
}, separators=(",", ":")).encode("utf-8")          # sign the EXACT bytes you send

sig = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
r = requests.post(URL, data=body,
                  headers={"Content-Type": "application/json", "X-Signature": sig})
print(r.status_code, r.json())
```

#### Example signer — Node / JS
```js
import { createHmac } from 'node:crypto';

const SECRET = '…MEASURE_CRM_WEBHOOK_SECRET…';
const URL = 'https://crm.collaborativeconceptsfl.com/measurements/ingest';

const body = JSON.stringify({
  external_ref: 'AL-1042',
  address: '4210 Palm Lakes Blvd, West Palm Beach, FL',
  source: 'RoofGraf', squares: 38.5, pitch: '6/12',
  ridge_lf: 120, valley_lf: 64, eave_lf: 140,
  report_url: 'https://example.com/report.pdf',
});                                                   // sign the EXACT string you send

const sig = createHmac('sha256', SECRET).update(body).digest('hex');
const res = await fetch(URL, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json', 'X-Signature': sig },
  body,
});
console.log(res.status, await res.json());
```

Two setup steps when the app is ready: (1) set `MEASURE_CRM_WEBHOOK_SECRET` to the
same value on both sides; (2) point the app's "report finished" webhook at the URL
above.

---

## 10. Gmail (Part 2 decision)

CRM login stays **identity-only** (non-sensitive Google scopes, no app review),
and the restricted `gmail.modify` inbox connect is **auto-triggered once per
session** right after login (`auth._after_login_redirect`). We did **not** bundle
`gmail.modify` into the login itself: that would force the restricted-scope
consent + Google CASA verification on *every* user and break login for
non-test-users until verification completes. Auto-trigger gets the inbox widget
connected seamlessly while keeping login fast and review-free.

---

## 11. Security checklist

- [x] Short TTL (≤120s, 90s in practice) + `iat`/skew bounds.
- [x] Single-use nonce, rejected on replay (unique per tenant).
- [x] HMAC-SHA256, constant-time compare.
- [x] Tenant-bound; verified with that tenant's secret (forged tenant fails).
- [x] Origin-checked `postMessage`, both directions; never `"*"` for the token.
- [x] No token in the iframe URL; secret never sent to the browser or logged.
- [x] Secrets in env vars; `.env` gitignored on both repos.
- [x] Provisions only the asserted (cryptographically trusted) user.

---

## 12. Operator steps (handed back to the user)

1. **Shared secret.** Pick a strong value. Set `SEABREEZE_CRM_WEBHOOK_SECRET` to
   it in **both** the CRM (Vercel env) and SiteCam (Render dashboard). On SiteCam,
   redeploy with `SEED_FORCE=true` once so the encrypted tenant row is rewritten.
2. **Google sign-in on SiteCam** (only needed for the standalone Google button):
   in Google Cloud Console → the OAuth client
   `639719647495-mrs7aeofovjlcu8k65973un2uhql89v0`, add
   `https://sitecam-web.onrender.com` to **Authorized JavaScript origins**. (SSO
   from the CRM does not need this.)
3. Deploy CRM (Vercel) and SiteCam (Render) — see each repo's `DEPLOY.md`.
