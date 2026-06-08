# Homeowner Portal — Session Handoff

Condensed context to continue **perfecting the homeowner UI**. App = white-label
roofing CRM (Flask) at `whitelabel-crm/`. Live: https://whitelabel-crm-rho.vercel.app
Data = Neon Postgres. Files = Neon blob store (`gdrive.blob_put`/`serve_fallback`,
4 MB cap) with a Google Drive fallback (dormant until a service account is set).

## Where the portal lives
- `modules/portal.py` — token-gated public routes (`/portal/<token>`). Homeowners get
  a **magic link** per job (`jobs.portal_token`); no login. `portal_link(job_id)` Jinja
  global builds it; job header has 🏠 Portal / 🔗 Copy link.
- `templates/portal_dashboard.html` — the single branded portal page (own HTML, not
  base.html). Sections: status/milestones, what's-next checklist + timeframes, sign-up
  packet prompt, docs-to-sign, estimate approve, payments, photos, documents, product
  info (system-matched library sheets), upload doc/photo, message/request, rep contact.
- `modules/signups.py` + `templates/signup_packet.html` — per-system Sign-Up Package:
  pre-filled, homeowner initials each clause + draws signature → generates a signed PDF
  (`_generate_pdf`) saved as the job's Contract doc. `SYSTEM_EXTRAS` adds the real form
  fields (metal panel style/brand/color, tile profile, etc.). `_prefill_pdf` overlays the
  customer header onto the actual company PDFs (best-effort, label-locating).
- Auth: portal endpoints are in `auth.PUBLIC` (no login). Signing = typed initials +
  drawn signature pad + consent statement + timestamp.

## What works today
- Magic-link access, status/milestone tracker, what's-next + timeframes.
- E-sign: any document (Request Signature → sign in portal), the estimate/proposal, and
  the full Sign-Up Package (initials per clause + drawn signature → signed PDF).
- Payments: per-job `pay_url` (Stripe/Square/QBO/PayPal) → "Pay your balance" button;
  QBO invoice links when present.
- Uploads: homeowner uploads docs (HOA) + photos. Product info auto-matched to roof system.
- Payment schedule = 30/30/30/10 (performance-based).

## Known gaps / polish targets (the "perfecting" list)
1. **Mobile layout pass** — portal_dashboard.html is desktop-first; verify on phone
   (the section cards, signature pad sizing, pay buttons).
2. **Visual design** — it's functional but plain; tighten spacing, hero, progress bar,
   brand polish to feel premium.
3. **Part B overlay precision** — `_prefill_pdf` places values next to found labels but
   isn't pixel-perfect per form, and doesn't check the panel-style box on the paper PDF.
4. **File downloads on live** — 8 huge brochures exceed the 4 MB Neon-blob cap (fine
   locally / on a disk host). Render disk (`render.yaml`) is the clean fix.
5. **Notifications** — when homeowner signs/uploads/messages, it logs activity + sets a
   follow-up; no email/SMS to the rep yet.
6. **Drawn signature on the per-document e-sign** is a pad; the Sign-Up Package final
   signature is a pad + typed name. Initials are typed (consider initial-pads).
7. **Portal nav** — currently one long scroll; consider section tabs/anchors.

## How to run / verify
- Local: `python app.py` (SQLite). Test client pattern: set session `user_id` to an admin
  user; portal routes need no auth.
- Deploy: `git push origin main` then `vercel --prod --yes` (or it auto-deploys once the
  GitHub↔Vercel connect is approved). DB/env already wired (`DATABASE_URL` on Vercel).
- A real signed packet + pre-filled company PDFs were verified end-to-end (valid PDFs).

## Kickoff prompt for the new session
> Continue perfecting the homeowner portal in `whitelabel-crm/`. Read
> `docs/PORTAL_HANDOFF.md` first. Focus: mobile/visual polish of
> `templates/portal_dashboard.html` + `signup_packet.html`, then work the gap list.
> App is live on Vercel (Neon DB); deploy with `vercel --prod`.
