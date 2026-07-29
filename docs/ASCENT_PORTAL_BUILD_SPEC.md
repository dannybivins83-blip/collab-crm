# Roof Portal "Ascent" — Build Wiring Spec (for the design agent)

Direction locked: **2a · Ascent** (scroll-driven step tracker, "Your roof is being installed",
Pay balance / Today's photos / Call Sam). This is everything you need to bind the design to the
real CRM backend. **Verdict: build it for REAL — 4 of 5 features already have live endpoints.**
Only 2 pieces are greenfield; exact contracts below so swap-in is trivial.

App = Flask + Jinja + vanilla JS. Portal templates live in `whitelabel-crm/templates/`
(`portal_dashboard.html` = job, `lead_portal.html` = prospect, `_journey.html` = the roadmap
partial). The homeowner view is rendered by `modules/portal.py::home()`.

---

## THE 4 DEV ANSWERS

**1. Auth — token-based, login-FREE.**
The homeowner URL is `/portal/<token>` (resolves a job OR a lead). No login, no cookies, no CSRF
token — **the unguessable `<token>` in the path IS the auth**, and every action route carries the
token. All portal routes are on the public allowlist (`modules/auth.py` `PUBLIC`). So every form
`action` / link / fetch you build must include the token: `/portal/{{ token }}/...`. Per-resource
ownership is server-checked (e.g. an estimate must belong to that token's record). Don't add a
login step or auth headers to any portal UI.

**2. Doc/photo upload endpoint — EXISTS, use directly.**
- Documents: `POST /portal/<token>/upload-doc` — multipart form, fields: `file` (required),
  `category` (one of HOA/Insurance/COI/Permit/Other). Redirects back to the portal.
- Photos: `POST /portal/<token>/upload-photo` — multipart, field: `file` (image/*).
- Storage: saved to the persistent disk (`/data/uploads/{documents,photos}/`) AND mirrored to
  Google Drive; served back via `/uploads/<subpath>` with a 3-tier fallback (disk → SQL blob →
  Drive) and a "servable" guard that hides any link whose file is missing. So: a normal
  `<form method="post" enctype="multipart/form-data">` posting to those URLs is all you wire.

**3. Payment — LINK-based, processor-AGNOSTIC (not a native Stripe embed).**
The "Pay balance $9,920" button links to `GET /portal/<token>/pay` (or `/pay/<inv_id>` for a
specific invoice). The route 302-redirects to the payment link on file:
`invoices.payment_link` (per-invoice, e.g. a QuickBooks link) → else `jobs.pay_url` (a
Stripe/Square/QBO/PayPal Payment Link pasted by the office). If none is set it flashes "contact
us" and returns to `#payments`. **So the processor is "whatever link the office pastes" — you
don't integrate a specific SDK.** The button is just `<a href="/portal/{{ token }}/pay">Pay
balance {{ j._balance|money }}</a>`. (If Danny wants native Stripe Checkout later, that's a
backend swap behind the same `/pay` URL — the UI never changes.)

**4. SiteCam (photo feed) product/API — EXISTS as a linked gallery.**
SiteCam is the SeaBreeze field-photo app. When a job's gallery is shared, SiteCam POSTs its
read-only gallery URL to `POST /sitecam/gallery` and the CRM stores it on `jobs.sitecam_url`.
The portal then shows the live feed two ways, both already wired in `home()`:
- `j.sitecam_url` → embed as an `<iframe>` and/or a "Today's photos" button (`target=_blank`).
- `photos` (list, from the `photos` table, served `/uploads/photos/<file>`) → the homeowner's own
  + job-site uploads as a gallery grid.
So "Today's photos" = `<a href="{{ j.sitecam_url }}">` when present, else link to the `#photos`
gallery section. No SiteCam API key needed in the portal — the URL is pre-attached to the job.

---

## THE HERO STEP-TRACKER — bind to real data (all fields already passed to the template)

`home()` passes these template variables — use these exact names:
- `j._phase` → int 0–5 = current phase index. Phases (`phases` / `CUSTOMER_PHASES`):
  `0 Approved · 1 Permitting · 2 Scheduling · 3 Installation · 4 Final Inspection · 5 Complete`.
  Your "Step 4 of 6 · Installation" = `{{ j._phase + 1 }} of 6` + `{{ phases[j._phase] }}`.
- `checklist` → list of `{name, desc, timeframe, you[], done, current}` per phase — drives the
  "What happens next" lift-in rows (done ✓ / current highlighted / upcoming).
- `j._balance` (float, use the `money` filter) → the "Pay balance $X" figure; `j._paid_pct` →
  progress. `j._client` / `j._first` → "Hi Jordan". `j.address/city/state/zip`.
- `rep` (`{name}`) / `company.phone` → "Call Sam" = `<a href="tel:{{ company.phone }}">Call
  {{ rep.name.split(' ')[0] }}</a>`.
- `updates` → milestone feed ("Updated 12 min ago"). `estimates`, `documents`, `photos`,
  `invoices`, `signup_packet`, `product_docs`, `tutorials`, `referral`.
- The unified 10-step roadmap is already computed: `journey` (list of
  `{n,title,blurb,icon,status,cta_label,cta_url,locked}`) + `progress {done,total,pct}` — the
  Ascent tracker can render straight off `journey` if you want the full prospect→job flow.

---

## THE 5 BUILD-OUTS

**A. Doc uploads — REAL now.** Wire the two multipart forms in #2. Existing doc/photo lists:
loop `documents` (each `{original_name, category, filename}`, link `/uploads/documents/<filename>`)
and `photos` (`/uploads/photos/<filename>`).

**B. Real payment links — REAL now.** Button → `/portal/{{ token }}/pay`. Amount = `j._balance`.
Show the "Pay" CTA only when `j._has_billing` and `j._balance > 0`.

**C. SiteCam photo feed — REAL now.** Per #4. "Today's photos" → `j.sitecam_url` (iframe/button)
or `#photos`. Gallery grid off `photos`.

**D. Training section — REUSE Roof School (exists) + optional new content.**
`GET /portal/<token>/learn` (Roof School: systems, materials, Roof IQ quiz) and
`/portal/<token>/seminar` (HOA lunch-&-learn) already exist and are public. The Ascent "training"
tab should link to `/portal/{{ token }}/learn`. Content data lives in `portal.py`
(`ROOF_EDU`, `FEATURES`, `GLOSSARY`, `ROOF_QUIZ`, `PROCESS_STEPS`). If Ascent wants a richer
in-page training module, design it against that data shape and I'll expose a
`training` context var — tell me the fields you want.

**E. HO checklist write-back — GREENFIELD (build to this contract; I'll ship the endpoint).**
Today `checklist[].you[]` (the homeowner's per-phase to-dos) is **read-only**. To let the
homeowner check items off and have it persist + notify the office, I'll add:
- Table `portal_tasks (id, job_id, key TEXT, label TEXT, done INT, done_at TEXT)`.
- `POST /portal/<token>/task` — form/JSON `{key, done}` → upserts the row for that job, logs an
  activity, returns `{ok:true, done, done_at}`. Token-gated + ownership-checked like the rest.
- `home()` will pass `ho_tasks` (list of `{key,label,done,done_at}`) so you render checkboxes.
Build the UI against `ho_tasks` + a `fetch('/portal/{{ token }}/task', {method:'POST', body:...})`
optimistic toggle. **Placeholder now, real swap = zero UI change.** Give me your final task list
(keys + labels) and I'll seed them.

---

## BUILD RECOMMENDATION
Wire A/B/C/D for REAL immediately (endpoints live). For E, code against the `ho_tasks` +
`POST /task` contract above as a placeholder — I'll land the endpoint + table to match so you swap
nothing. Net: no throwaway placeholder wiring except the one greenfield piece, and even that is
contract-locked. When your `.dc.html` is ready, hand it back and I (or crm-ui) port it into the
Jinja templates against these exact variable names so it drops in clean.
