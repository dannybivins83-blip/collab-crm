# Measurement App → CRM: Roof-Report Ingest

How the in-house roof-measurement app pushes a finished report into the CRM. This is
self-contained — you do **not** need to read the SSO doc to implement it.

When a measurement is finished, POST it to the CRM. The CRM matches the right
lead/job, stores the measurement (squares / pitch / linear footage), attaches the PDF
under the record's Documents, auto-parses anything you didn't send, and logs activity.
The call is **idempotent** per record (re-posting updates in place).

---

## Endpoint

```
POST https://crm.collaborativeconceptsfl.com/measurements/ingest
```

(Local/dev: `POST http://127.0.0.1:5050/measurements/ingest`.)

CORS is open; `OPTIONS` preflight is handled. No login/session required — auth is the
signature below.

## Authentication — HMAC signature

Sign the **exact raw request body bytes** and send the hex digest in a header:

```
X-Signature: <hex>          # = hmac_sha256(MEASURE_CRM_WEBHOOK_SECRET, raw_body)
```

- Algorithm: **HMAC-SHA256**, output as **lowercase hex**.
- **No `sha256=` prefix** — send the bare hex digest only.
- Sign the **raw bytes you actually send** (the serialized JSON, or the multipart body).
- Shared secret: env var **`MEASURE_CRM_WEBHOOK_SECRET`** — must be set to the **same
  value** on both the measurement app and the CRM. (If unset on the CRM in dev, it falls
  back to `<tenant>-webhook-secret`; in production always set a real shared secret.)

Bad/missing signature → `401 {"ok": false, "reason": "bad_signature"}`.

## Request body

JSON (`Content-Type: application/json`) **or** `multipart/form-data`. Flat keys.

### Match the record (send at least one — first match wins, in this order)
| key            | matches                                                        |
|----------------|----------------------------------------------------------------|
| `job_id`       | CRM job id (exact)                                             |
| `lead_id`      | CRM lead id (exact)                                            |
| `external_ref` | AccuLynx GUID (substring of the record's `external_url`)       |
| `address`      | substring match against job/lead address                       |
| `name`         | exact (case-insensitive) record name                           |

No match → `404 {"ok": false, "reason": "no_match", "hint": "..."}`.

### Measurement fields (all optional; numbers may be strings)
`squares`, `pitch`, `stories`, `ridge_lf`, `hip_lf`, `valley_lf`, `rake_lf`,
`eave_lf`, `step_flash_lf`, `facets`, `waste_pct`, `source`, `notes`.

Anything you omit, the CRM tries to auto-parse from the PDF.

### The report PDF (optional — any one of)
- `report_url` — a URL the CRM will fetch the PDF from, **or**
- `pdf_base64` — the PDF bytes, base64-encoded, **or**
- multipart `file` — the PDF as a file part.
- `filename` — original file name (optional; used for the stored document).

## Responses
| code | body                                                                 |
|------|----------------------------------------------------------------------|
| 200  | `{"ok": true, "matched": "job"|"lead", "record": "<name>", "id": <measurement_id>, "squares": <n>}` |
| 401  | `{"ok": false, "reason": "bad_signature"}`                           |
| 404  | `{"ok": false, "reason": "no_match", "hint": "..."}`                 |

---

## Example — Python

```python
import hmac, hashlib, json, urllib.request

SECRET = "<MEASURE_CRM_WEBHOOK_SECRET>"        # same on both sides
URL = "https://crm.collaborativeconceptsfl.com/measurements/ingest"

payload = {
    "external_ref": "d63d68db-7cb4-428b-b2f8-e50893cc296c",   # or job_id / lead_id / address
    "squares": 36.3, "pitch": "5:12",
    "ridge_lf": 100, "hip_lf": 83, "valley_lf": 11, "eave_lf": 190, "rake_lf": 124,
    "report_url": "https://your-measure-app/reports/abc.pdf",  # or pdf_base64
    "source": "Measurement App",
}

raw = json.dumps(payload).encode("utf-8")      # sign the EXACT bytes you send
sig = hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()

req = urllib.request.Request(
    URL, data=raw, method="POST",
    headers={"Content-Type": "application/json", "X-Signature": sig})
print(urllib.request.urlopen(req).read().decode())
```

## Example — Node / JavaScript

```js
const crypto = require("crypto");

const SECRET = process.env.MEASURE_CRM_WEBHOOK_SECRET;   // same on both sides
const URL = "https://crm.collaborativeconceptsfl.com/measurements/ingest";

const payload = {
  external_ref: "d63d68db-7cb4-428b-b2f8-e50893cc296c",   // or job_id / lead_id / address
  squares: 36.3, pitch: "5:12",
  ridge_lf: 100, hip_lf: 83, valley_lf: 11, eave_lf: 190, rake_lf: 124,
  report_url: "https://your-measure-app/reports/abc.pdf",  // or pdf_base64
  source: "Measurement App",
};

const raw = Buffer.from(JSON.stringify(payload), "utf8");  // sign the EXACT bytes you send
const sig = crypto.createHmac("sha256", SECRET).update(raw).digest("hex");

const res = await fetch(URL, {
  method: "POST",
  headers: { "Content-Type": "application/json", "X-Signature": sig },
  body: raw,
});
console.log(res.status, await res.json());
```

---

## Setup (one time, both sides)
1. Generate a strong shared secret and set **`MEASURE_CRM_WEBHOOK_SECRET`** to the same
   value in the CRM's environment **and** the measurement app's environment.
2. Point the measurement app's "report finished" webhook at the endpoint above, signing
   each request as shown.

## Smoke test (confirm connectivity before wiring your app)

Run this from a shell where `MEASURE_CRM_WEBHOOK_SECRET` is set to the shared secret.
It signs at call time (no secret in any file) and sends a deliberately **non-matching**
body, so a **`404 no_match`** is the expected success result — it proves the URL is
reachable **and** your signature is correct, without needing any real lead to exist.

```bash
# Self-signing — nothing pre-baked. Requires: openssl, curl.
SECRET="$MEASURE_CRM_WEBHOOK_SECRET"
URL="https://crm.collaborativeconceptsfl.com/measurements/ingest"
BODY='{"address":"__connectivity_check__no_such_address__"}'

SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$SECRET" | sed 's/^.*= //')
curl -sS -i -X POST "$URL" \
  -H "Content-Type: application/json" -H "X-Signature: $SIG" -d "$BODY"
```

Read the result:
- **`404 {"reason":"no_match"}`** → ✅ connectivity + signature are correct. You're ready
  to send real pushes (swap in a `job_id`/`lead_id`/`external_ref`/`address` that matches).
- **`401 {"reason":"bad_signature"}`** → secret mismatch, or you signed different bytes
  than you sent (don't re-serialize after signing).
- **HTML / connection error / CORS** → wrong URL or host unreachable.

> Note: this signs the literal `BODY` bytes above. If your shell or client alters the
> body (whitespace, encoding), sign the exact bytes you transmit — same rule as production.

## Gotchas
- Header is **`X-Signature`** with a **bare hex** digest — **no `sha256=` prefix**.
- Sign the **exact serialized bytes** you transmit (re-serializing differently breaks the
  signature). For multipart, sign the raw multipart body.
- Idempotent: re-posting for the same record updates its measurement + replaces the
  document, never duplicates.
