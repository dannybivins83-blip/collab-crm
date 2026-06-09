# Multi-Agent Coordination — White-Label CRM

Several agents work this repo in parallel, each in its own git worktree/branch.
This file is the source of truth for **who owns what**, **merge order**, and the
**rules** that keep them from clobbering each other. The integrator (main session)
reviews each branch, merges in order, and does the single production deploy.

## Golden rules
1. **Never commit straight to `main`.** Work on your task branch (below). The
   integrator merges to `main`.
2. **Test against local SQLite, never live Neon.** Run with an EMPTY
   `DATABASE_URL` (see `reference_local_web_preview.md`). Only the integrator runs
   schema changes / writes to Neon / deploys to Vercel.
3. **Stay in your lane.** Editing files outside your lane = merge conflicts. If you
   must touch a shared file, note it in your report so the integrator expects it.
4. **No secrets in git** (`.env` is gitignored). Don't commit data/scratch.
5. **Emails are draft-only. Never auto-send. Never fabricate data** — if real data
   is missing, report the gap.

## Lanes, branches, and merge order
Merge **low-conflict first, QA/UX last** (QA rebases on the finished result so it
polishes the final state instead of fighting it).

| # | Task | Branch | Owns (lane) | Conflict risk |
|---|------|--------|-------------|---------------|
| 1 | Permit spec-sheet fill | `agent/permit-spec-sheet` | `packet_builder_handoff/build.py` (+ library) | none — separate folder |
| 2 | Estimate templates from real orders | `agent/estimate-templates` | `constants.py` (templates/upgrades), `modules/estimates.py`, `modules/orders.py` | low |
| 3 | Unified SSO (CRM → all apps) | `agent/unified-sso` | `modules/auth.py`, `modules/gmail.py`, `modules/sitecam.py`, `templates/login.html`, `docs/SSO.md` + the **`sitecam` repo** | low (except auth/sitecam) |
| 4 | QA + mobile/UX + lead-intake | `agent/qa-ux` | **all `templates/*`, `static/app.css`, `_icons.html`** + intake/phone prototypes | **HIGH — touches everything; merge LAST** |

Shared hot files (coordinate before editing): `db.py` (schema), `base.html`,
`dashboard.html`, `app.py` (blueprint registration), `constants.py`.

## Integrator process (main session)
For each finished branch, in the order above:
1. Review the diff + the agent's report.
2. Merge to `main`; resolve conflicts (favor the lane owner for its files).
3. Smoke-test locally on SQLite.
4. After all four (or a logical batch): run the QA agent over merged `main`, then
   **one** `vercel deploy --prod --yes --scope dannybivins83-blips-projects`.
5. Run schema/data migrations against Neon once, deliberately.

## Status board
| Task | Branch | Status | Notes |
|------|--------|--------|-------|
| Permit spec-sheet | `agent/permit-spec-sheet` | queued | chip created |
| Estimate templates | `agent/estimate-templates` | queued | chip created |
| Unified SSO | `agent/unified-sso` | queued | chip created |
| QA / mobile / UX | `agent/qa-ux` | queued | chip created; merge last |

_Update Status as branches land. Baseline snapshot commit precedes all branches._

## LIVE STATUS (reality update)
The QA/UX and Estimate-template agents were STARTED and commit **directly to `main`**
(not isolated branches) — they own the active trunk. SSO CRM-side is also landing on
`main` (`modules/sso.py`, `/sso/token/sitecam`, postMessage in `sitecam.html`).

- QA/UX + intake → RUNNING on main (icons, lead_intake.py, RingCentral, QA reports).
- Estimate templates → RUNNING on main (constants.py, order-BOM/worksheet memory).
- Unified SSO CRM-side → on main. **Pending SSO agent must BUILD ON this** — its lane
  is the **`sitecam` repo** (`/api/auth/sso`, Google sign-in, Render deploy) + the
  measurement webhook seam. Do NOT rebuild `modules/sso.py`.
- Permit spec-sheet → pending; isolated in `packet_builder_handoff/build.py` — safe to run anytime.

Integrator (main session): watch `main`, smoke-test after commits, run the single
Vercel deploy when stable. Branch anchors exist but live agents are on main trunk.
