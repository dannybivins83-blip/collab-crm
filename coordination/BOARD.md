# 🏗️ COORDINATION BOARD — White-Label Roofing CRM

**The single source of truth for who's doing what.** Every lane: pull → read this → work →
update your row → push. One pusher at a time. Don't route status through the owner.

- **Repo:** `github.com/dannybivins83-blip/collab-crm` · working branch `agent/gc-consolidation`
- **Git tip (verify with `git ls-remote`):** `a9c0550` on `origin/agent/gc-consolidation`; `origin/main` = `c612877` (behind, FF after permit work lands)
- **Last board update:** 2026-06-10 — by Head Coach lane (coordination spine created)

---

## LANES (session → owns → status)
| Lane | Session title | Owns | Status |
|---|---|---|---|
| 🧭 Head Coach | SB CRM-clone MAIN DEV UI CODER-2nd | Coordination, audit, sequencing, owner decisions | **Active** — spine up; holding code-clean |
| 🔒 Security | (UI CODER lane) | Audit fixes #1–#7 | #1+#2 pushed (`a9c0550`); next: #3/#4 (queued) |
| 🧱 Permit | SB CRM-clone MAIN PERMITBUILDER CODER | `ahj.py`, `permits.py`, `permit_detail.html` | **3 files uncommitted** — needs commit+push, then FF main |
| 🏠 Portal | SB CRM-clone MAIN DEV HO PORTAL UI CODER | `portal.py`, portal templates | idle |
| 🔌 AppConnect | SB CRM-clone MAIN APPCONNECT CODER | integration glue / takeoff | idle |
| 🔁 Sync | (Sync coder) | `acculynx_sync.py` bridges | folded into Security #1 |
| 📐 Measurement | SeaBreeze roof measurement specialist | measurement ingest | idle |
| 🛰️ Roof Engine | SB CRM-clone MAIN DEV ROOFENGINE CODER | engine VM, `/api/takeoff` push | waiting on `MEASURE_CRM_WEBHOOK_SECRET` |
| 📷 SiteCam | SB CRM SiteCam-clone MAIN DEV UI CODER | sitecam-api, SSO verify | waiting on `SEABREEZE` rotation + SEED_FORCE |
| 🏢 WWS (tenant 2) | WWS CRM-clone / WWS SiteCam-clone | 2nd white-label tenant | parallel — keep in sync with product changes |

---

## TASK QUEUE (priority order)
| # | Task | Owner | State | Notes |
|---|---|---|---|---|
| P0 | **Close Critical #1 on the LIVE domain → via DNS cutover to Render** | Head Coach + owner | OPEN | Decision made: cut over to Render (not a Vercel redeploy). Needs Render fully configured+verified, then DNS repoint. Interim: hole is data-integrity only (no PII path), unadvertised domain — accepted briefly pending a prompt cutover. |
| P1 | **Build secret provisioning script** | Head Coach | NEXT | `scripts/provision_secrets.py`: reads `secrets/keys.local.env`, pushes to Render + Vercel via env-var APIs (host API tokens from local env, never chat). Seed of tenant onboarding. |
| P1 | **Stand up scheduled Head Coach (cron)** | Head Coach | NEXT | Wakes every few hrs: pull, verify state, reconcile board, ping idle/blocked lanes, escalate owner-decisions. Autonomy = act-on-safe. |
| P0 | **Set `CRM_SYNC_SECRET` + `CRM_SECRET` on Render** | owner (dashboard) | in progress | Render fail-closes without them. Reinstall bookmarklets after. |
| P1 | **Rotate burned/leaked secrets** | owner | OPEN | `MEASURE` (chat-leak ×2), `SEABREEZE` (leaked), `GOOGLE_OAUTH_CLIENT_SECRET` (screenshot), roof-engine key. Fresh values → dashboards + `secrets/keys.local.env`. |
| P1 | **Permit lane: commit+push 3 files, FF main** | Permit | OPEN | Unblocks main catch-up; stops interleaving. |
| P2 | **Audit #3 (`est_num` money corruption) + #4 (dup invoice #s)** | Security | QUEUED | No decision needed; do after permit resync. |
| P2 | **Secret provisioning automation** | Head Coach | DESIGN | One-command push of `keys.local.env` → Render/Vercel APIs; seed of tenant onboarding. |
| P3 | **SSO go-live** | SiteCam + owner | BLOCKED | Needs fresh `SEABREEZE` on both sides + sitecam-api `SEED_FORCE=true`. |
| P3 | **QuickBooks integration (product)** | TBD | BACKLOG | Per-tenant OAuth design needed for white-label. |
| P3 | **Audit #5–#12** | Security | BACKLOG | SECRET_KEY default, CSRF, SSRF, checklist gating, etc. |

---

## DECISIONS — RESOLVED (2026-06-10)
- [x] **Head-coach autonomy:** ACT on safe/reversible (board, pings, FF main, doc commits, local tests); ESCALATE irreversible/outward-facing/spend (deploys, DNS, secrets, sends).
- [x] **Secret automation:** build a **provisioning script** now (reads `secrets/keys.local.env` → pushes to Render/Vercel APIs). No new vendor; becomes the tenant-onboarding seed.
- [x] **Live-#1 close path:** owner delegated → **Head Coach call = DNS cutover to Render** (closes the hole + collapses to one vendor, the stated direction). Sequence: set/rotate all Render secrets → verify Render healthy on onrender URL → repoint DNS → keep Vercel as rollback. **Blocked on:** owner's DNS host (for exact records) + Render secrets finished.

## PROTOCOL
Pull before, push after. Update your row. `send_message` for "your turn now" pings only.
Verify git/host claims with tools. Secrets never in chat/commits. See `CLAUDE.md`.
