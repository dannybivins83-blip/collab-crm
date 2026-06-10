# Homeowner Portal — Handoff to Lead Dev

**Branch:** `agent/gc-consolidation` (78 commits ahead of `main`).
**Live:** Vercel prod (`crm.collaborativeconceptsfl.com`), Neon Postgres. All items below are
**deployed and verified HTTP 200**. Deploys this session were **committed-code-only** (other
agents' uncommitted files were stashed during each deploy, then restored — nothing unreviewed
shipped).

## TL;DR — what shipped
A full customer-engagement portal that starts the moment a lead is created, all token-based
and login-free (`/portal/<token>`, works for a **lead or a job**):

1. **Design Studio** (`/portal/<token>/design`) — pick system → tap a color → live SVG roof
   recolors; toggle options; request samples. **Upload a photo of your house → brush your
   chosen color onto your actual roof** (HTML canvas, `globalCompositeOperation="color"` so the
   shingle/tile texture shows through — no ML). "Save my mockup" posts the recolored image to
   the record so the rep sees the homeowner's desired color on their real house.
2. **Roof School** (`/portal/<token>/learn`) — system accordion (photo-fronted, deep:
   SWB/layers/FL-code), a **Roof IQ quiz** (score/badge/confetti), a glossary, deep **component
   features** (underlayment types + brands, ridge vents, decking/re-nail, valley, flashing,
   drip) each linking to a **real product data sheet** from the Document Library, and a
   **"documented in SiteCam" gallery** pulling the latest real job photos of the homeowner's
   system.
3. **HOA Lunch & Learn** (`/portal/<token>/seminar`) — resident/board member requests a catered
   Q&A; choose systems + a required manufacturer/color (flags "bring the reps"). Logs to the
   record + saves `seminar_request`.
4. **Referral Game** (in `portal_dashboard.html`) — personal link, send-tracking, level badges,
   reward ladder; public landing `/portal/r/<code>`.
5. **Welcome Packet** (consolidated proposal section) — proposal e-sign + sign-up package +
   documents-to-sign + permit docs, in one place.
6. **Live tracker** + milestone updates feed + confetti; **SiteCam album embedded**.
7. **UI pass** — flat theme (no gradients), monochrome line-icon set (`_portal_icons.html`,
   `pic()` macro), thick-outline buttons, back buttons, rep contact moved to top.

## Files
- `modules/portal.py` — all portal routes + data (`ROOF_COLORS`, `ROOF_OPTIONS`, `ROOF_EDU`,
  `FEATURES`, `GLOSSARY`, `ROOF_QUIZ`, `PROCESS_STEPS`), helpers (`product_docs_for`,
  `latest_system_job_photos`, `referral_ctx`, `on_phase_advance`).
- Templates: `portal_dashboard.html` (job), `lead_portal.html` (lead), `design_studio.html`,
  `learn.html`, `seminar.html`, `_portal_icons.html` (icon macro).
- `modules/gmail.py` — `send_message(uid, to, subj, body)` (uses the existing `gmail.modify`
  grant). Powers the optional auto portal-invite.
- `modules/auth.py` — public (login-free) allowlist: `portal.design`, `design_request`,
  `design_photo`, `learn`, `seminar`, `referral_land`, `refer_share`, `refer_msg`.

## Data model additions (auto-migrated at module load / via `_ensure_column`)
- **leads & jobs:** `portal_token`, `portal_invited`, `design_selection` (JSON),
  `design_photo`, `seminar_request` (JSON), `referral_code` + referral counters,
  `signature`/`signed_name`/`signed_at`/`sign_consent` (reusable e-sign).
- **company_settings:** `auto_portal_invite` (default 0), `portal_perks`, `portal_notify`
  (default 0).
- **new table:** `portal_updates` (job_id, phase, title, created, seen) — milestone feed.

## Things that need YOUR call
1. **Merge `agent/gc-consolidation` → `main`.** 78 commits ahead, intermixed with other agents'
   work (Roof Report engine, sitecam, pipeline, lead-naming, SSO). Needs the integrator to
   sequence the branches and dedupe (two SSO branches exist).
2. **Render + SQLite migration** (prepped, NOT executed): `docs/DEPLOY_RENDER.md`, `render.yaml`,
   `scripts/neon_to_sqlite.py`. Goal: drop Vercel + Neon (both hit free-tier caps this session —
   Neon's data-transfer quota took prod down once). Needs a brief Neon export → cut over to one
   always-on host running Flask + SQLite-on-disk (db.py is already dual-engine). This permanently
   ends the deploy-cap and DB-transfer-quota problems.
3. **Auto-send toggles (default OFF — safety):**
   - `auto_portal_invite` — emails a new lead their portal link automatically. Built + gated
     (email present + Gmail connected + dedupe). **Test with one real email before enabling.**
   - `portal_notify` — emails the homeowner on each milestone. Same gating.
4. **Finish the icon sweep** — structural icons (headers/banners/CTAs) are done; some decorative
   inline emoji remain (system tabs, option chips, seminar perks, feature icons).

## Safety / correctness notes (important)
- **Permit packet signatures:** the captured e-signature is **deliberately NOT forwarded** into
  the permit packet (`modules/permits.py`) — NOC/nailing-affidavit are **notarized**, must be
  wet/RON-signed in person. See `docs/PERMIT_SIGNATURE.md`. The e-sign consent text is scoped to
  the proposal + (non-notarized) sign-up package only.
- **Emails are draft-first by house rule;** the only auto-send paths are the two gated,
  default-off toggles above.

## Coordination
- A **branded demo-portal generator** was spun off to its own session (sales tool to generate a
  branded demo portal for prospects) — not in this branch.
- The **`casa-del-monte-portal`** Error deploys on Vercel are a **different project / session**
  (a Next.js construction portal) — unrelated to the CRM.
- Other agents have in-flight uncommitted work (`sitecam.py`, etc.) — keep deploying
  committed-only until the integration cut.

## Verify quickly
`curl -s -o /dev/null -w "%{http_code}" https://crm.collaborativeconceptsfl.com/portal/<token>`
→ 200. Open `/portal/<token>/design` and `/learn` to see the studio + Roof School.
