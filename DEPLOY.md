# Deploying the CRM (collaborativeconceptsfl.com → crm subdomain)

The CRM is a **stateful Flask + SQLite app with file uploads**, so it needs a host that
keeps a persistent disk between deploys. **Vercel won't work** for the app itself (its
filesystem is ephemeral/read-only — the database and uploads would reset). Vercel hosts
the marketing site; the CRM runs on Render (or Railway/Fly) and is linked from
`/solutions`, reachable at **crm.collaborativeconceptsfl.com**.

## Recommended: Render (one-time, ~10 min)
1. Push this `whitelabel-crm/` folder to a Git repo (its own repo, or a subdir Render points at).
2. Render Dashboard → **New → Blueprint** → select the repo. It reads `render.yaml`:
   - Python web service, `gunicorn app:app`, **1 GB persistent disk mounted at `/data`**.
   - Env already set: `CRM_DATA_DIR=/data`, `CRM_UPLOAD_DIR=/data/uploads`, random `CRM_SECRET`.
   - Plan **starter** (free tier has no persistent disk — the DB would not survive).
3. Deploy. You get a URL like `https://collab-crm.onrender.com`.
4. **Custom domain**: Render → Settings → Custom Domains → add `crm.collaborativeconceptsfl.com`.
   Then in your DNS (where collaborativeconceptsfl.com is managed), add the CNAME Render shows.
5. First login: `owner@seabreezeroofing.com` / `seabreeze2026` — **change it immediately**
   under Account, and rebrand under Settings (it's white-label).

## Env vars the app honors
| Var | Purpose | Render value |
|---|---|---|
| `CRM_DATA_DIR` | SQLite DB location | `/data` |
| `CRM_UPLOAD_DIR` | photos/docs/permits | `/data/uploads` |
| `CRM_DB_PATH` | override full DB path (optional) | — |
| `CRM_SECRET` | Flask session signing key | auto-generated |
| `PORT` | bind port | set by Render |

## Notes
- **Permit packet builder** needs the external `SeaBreeze_Permit_Library` (hundreds of MB of
  county PDFs). It is NOT bundled here, so on the host the Permits *tracker* works but
  one-click packet generation shows its "engine not detected" fallback. Bundle the library
  into the deploy (and set `SEABREEZE_LIB`) only if you want packet generation in the cloud.
- This is single-tenant. To resell to other contractors, deploy one instance per contractor
  (each gets its own disk/DB) and rebrand via Settings — that's the white-label model.
- Railway/Fly.io work too: same `Procfile`, just attach a volume and set `CRM_DATA_DIR` to it.

## PRODUCTION: real data + lockdown (the live business CRM)
This is the runbook to turn `crm.collaborativeconceptsfl.com` into the real, persistent CRM
holding the actual book of business — NOT a public demo.

1. **Deploy to Render with the disk** (steps above). The app is already hardened:
   - the public "Try the demo" login button is removed,
   - no demo user is seeded,
   - the seed/default password reads from `CRM_DEFAULT_PASSWORD`.
2. **Set these env vars on the Render service** (Settings → Environment):
   - `CRM_DEFAULT_PASSWORD` = a strong private password (so the owner/office logins are NOT
     the public `seabreeze2026`).
   - `CRM_DATA_DIR=/data`, `CRM_UPLOAD_DIR=/data/uploads`, `CRM_SECRET` = (auto-generated).
3. **Load the real data** onto the disk (one time). The live DB to upload is the local
   `whitelabel-crm/data/crm.db` (55 leads / 23 jobs / 77 contacts / 9 estimates). Options:
   - Render **Shell** → `cd /data` then upload `crm.db` (drag-drop in the shell, or
     `curl -o /data/crm.db <a temporary signed URL you host it at>`), OR
   - run once with the DB pre-placed at `/data/crm.db` before first request.
   Keep `crm.db` OUT of the Git repo (it contains customer PII) — transfer it to the disk
   directly. The repo stays code-only.
4. **Re-point the domain**: remove `crm.collaborativeconceptsfl.com` from the Vercel
   `whitelabel-crm` project, then add it to the Render service (Render → Custom Domains) and
   update the CNAME. The URL stays identical; testers/users notice nothing.
5. **First login** with the owner account + your `CRM_DEFAULT_PASSWORD`, then change it under
   Account and add per-user logins under Settings → Users (each real teammate gets their own).

After this, the public Vercel deployment can be deleted (or repurposed as a fake-data demo).

