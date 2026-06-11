# White-Label Roofing CRM — agent operating manual

You are one **lane** in a multi-agent build. The product is a **white-label CRM for roofing
& trade contractors** (an AccuLynx replacement). **SeaBreeze Roofing** is the reference
tenant; **WWS** is a second tenant. Everything tenant-specific must stay data-driven
(`company_settings`, env), never hardcoded — we resell this.

## Cardinal rule: verify, don't absorb
This project runs many parallel agents. Status you're told (by the owner relaying another
lane, by a handoff doc, by a prior session) is **a claim until you confirm it with tools.**
Before acting on git/host/state claims, check them (`git ls-remote`, `git status`, read the
file). We have repeatedly been burned by stale narration. Re-grounding is never wasted.

## Coordination — how lanes talk WITHOUT routing through the owner
The single source of truth is **`coordination/BOARD.md`** (git is the message bus):

1. **Start of turn:** `git fetch` + read `coordination/BOARD.md`. Find your lane's row and the
   task queue. Confirm the git tip the board claims is real.
2. **Do your work** on the files your lane owns. Don't touch another lane's in-flight files.
3. **End of turn:** update your row on the board (status + tip), then commit & push.
   **One pusher at a time** — if a push rejects, pull/rebase and retry; never force.
4. **Need another lane to act now?** Use `send_message` (one owner-approval click) AND leave
   it on the board. Routine status goes on the board only — don't make the owner relay.
5. **Owner decision needed?** Add it to the board's `## DECISIONS NEEDED (owner)` section.
   Don't block silently and don't guess on irreversible/spend/authority calls.

## Cross-project comms — the OVERLORD bus
To talk to agents in OTHER projects, use the shared message bus (NOT the owner as relay):
`C:\Users\kjburnz\acculynx roofr reprot\_OVERLORD\bus\` — see `bus/PROTOCOL.md`, addresses in
`bus/registry.json`. On start + each tick, check `inbox/<your-slug>/` for `status: new`; to reach
another agent, drop a message file in `inbox/<their-slug>/`. The OVERLORD heartbeat routes +
escalates. Never put secret values in a message.

## Secrets — never through chat
- Real values live ONLY in `secrets/keys.local.env` (gitignored) + each host's dashboard.
- Registry (names/purpose/status, NO values) = `docs/SECRETS.md`. Keep it current.
- **Never** paste a secret into chat, a screenshot, a commit, or a doc. If one appears in
  any of those, it is **burned** — regenerate and replace everywhere.
- Generate privately: Render "Generate" button, or `python -c "import secrets; print(secrets.token_hex(32))"`.
- In prod (`config.IS_PROD`) integration secrets **fail closed** — unset ⇒ reject, never a
  guessable fallback.

## Living docs (read before large work; keep current, don't spawn new boards)
- `coordination/BOARD.md` — who's doing what, now (the spine).
- `docs/INTEGRATION_HANDOFF.md` — the integration seams + their status.
- `docs/AUDIT_2026-06-10.md` — the security/QA findings + fix order.
- `docs/SECRETS.md` — secret registry.
- `docs/SESSION_STATE.md` — host/git grounding.

## Hosts
- **Render** (`collab-crm`, SQLite on disk) — canonical target; auto-deploys from
  `agent/gc-consolidation`; `RENDER=true ⇒ IS_PROD`.
- **Vercel** (Neon) — current live domain `crm.collaborativeconceptsfl.com`; deploys via
  `vercel --prod` from the local tree. **Set secrets there before any deploy** or sync/ingest 401s.
- DNS cutover Vercel→Render is the plan; Vercel is the rollback.

## Tech
Flask + Jinja + vanilla JS; ~40 blueprints in `modules/`; `db.py` dual-engine (SQLite local /
Postgres via `DATABASE_URL`). Test locally on SQLite (`DATABASE_URL=""`, `CRM_NOBROWSER=1`,
unique `CRM_PORT`) — never against a live host.
