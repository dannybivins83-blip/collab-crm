# Estimator → CRM: Takeoff Ingest (`POST /api/takeoff`)

One atomic call that accepts the Estimator's `estimator-takeoff/v1` envelope and fans it
out: **match-or-create job → measurement → estimate + line items → submittal components
(NOA index) → heads-up items → id-map response.** Works whether the Estimator runs
standalone or is folded into the Roof Report Engine (the engine just POSTs this after a
drawing upload).

## Endpoint & auth
```
POST https://crm.collaborativeconceptsfl.com/api/takeoff        (local: http://127.0.0.1:5050)
Header: X-Signature: <hex>     # = hmac_sha256(MEASURE_CRM_WEBHOOK_SECRET, raw_body), bare hex, no prefix
Body:   application/json — the estimator-takeoff/v1 envelope
```
Same HMAC scheme/secret as `/measurements/ingest`. Bad/missing signature → `401 {"ok":false,"reason":"bad_signature"}`.

## Matching & idempotency
- Job is resolved by, in order: `job_id` → `lead_id` → `external_ref` (AccuLynx GUID substring) →
  `project.address_line1` (substring) → `project.name` (exact). **No match → a new job is created**
  from `project` + `wind_design` + `roof_system`.
- Send **`idempotency_key`** (a per-takeoff UUID). Re-POST with the same key is a **no-op** that
  returns the original result (no duplicate job/lines). A new key = a new write.

## What maps where
| Envelope | CRM |
|---|---|
| `project.*` | `jobs` (name, address, city/state/zip, county, `permit_jurisdiction→ahj`, architect_firm, plan_set_label) |
| `wind_design.*` | `jobs` (wind_speed_mph, asce_version, risk_category, `exposure_category→exposure`) |
| `roof_system.*` | `jobs` (`primary_type→system/work_type`, `predominant_pitch→slope`) |
| `measurements[].steep_slope` | `measurements` (total_sq→squares, ridges/hips/valleys/rakes/eaves/step_flashing→*_lf). low_slope/demo → notes |
| `line_items[]` | one `estimate` + `estimate_lines` grouped by `section` (item→description, unit, qty, unit_price_usd→price) |
| `submittal_components[]` | `submittal_components` table (NOA#, expiration, url, status) |
| `heads_up_items[]` | job activities (notes); `severity:HIGH` also creates a follow-up **task** |
| NOA `status` contains EXPIR* or expires within 6 months | added to `warnings[]` + logged on the job |

## Response
```json
{ "ok": true, "job_id": 36, "measurement_ids": [6], "line_item_ids": [452,453],
  "submittal_component_ids": [1], "attachment_ids": [], "warnings": ["NOA 'Eagle Bel Air' (25-0313.05) expires soon — verify at permit."] }
```
Validation failure → `422 {"ok":false,"error_code":"VALIDATION_FAILED","field_errors":{...}}`.

## Notes / open items
- **Units** are free-text (no enum rejects `RL`/`SQ`/`LF`/etc.). Send as-is.
- **Pricing:** `unit_price_usd` is stored as the line price (and cost). Omit it and the CRM's
  per-system template price book prices new estimates instead.
- **Attachments** (RFI/submittal/plan PDFs) are **not** handled by this JSON endpoint — POST them
  separately to `POST /sync/doc-import` (multipart, chunked) with the returned `job_id`, or pass
  a fetchable URL. `attachment_ids` is reserved for when that's folded in.
- Signing: sign the **exact raw JSON bytes** you transmit (don't re-serialize after signing).
