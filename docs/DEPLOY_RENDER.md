# Consolidate to ONE host — Render + SQLite (drop Vercel + Neon + Drive)

This moves the CRM onto a single always-on Render service with the database (SQLite) and
all uploads on one persistent disk. It replaces **three** vendors with one:

| Job | Was | Now |
|---|---|---|
| Hosting | Vercel (serverless, deploy cap) | **Render web service** |
| Database | Neon Postgres (transfer quota) | **SQLite file on the disk** ($0 egress) |
| File storage | Google Drive | **the same disk** |

Why the caps disappear: SQLite is a local file (no network egress = no "transfer quota"),
and you deploy by pushing to git (no 100-deploys/day cap).

## What's already in the repo (done)
- `render.yaml` — Render Blueprint: web service + 5 GB disk at `/data`, gunicorn, and the
  env that forces SQLite (`CRM_DB_PATH=/data/crm.db`, **no `DATABASE_URL`**).
- `db.py` — SQLite runs in **WAL mode + busy-timeout**, so multiple gunicorn workers share
  the one file safely.
- `scripts/neon_to_sqlite.py` — one-time data export (Neon → `crm.db`), preserves IDs and
  adds any missing columns so nothing is dropped.

## Cutover runbook
1. **Get Neon reachable once** (upgrade briefly, or wait for the monthly transfer reset).
2. **Export the data → SQLite:**
   ```bash
   python scripts/neon_to_sqlite.py "<NEON_URL>" ./data/crm.db
   ```
   (Copy `<NEON_URL>` from Vercel → Settings → Environment Variables → `DATABASE_URL`.)
   It prints a per-table row count and writes `./data/crm.db`.
3. **Create the Render service:** Render Dashboard → New → **Blueprint** → connect the repo
   (`dannybivins83-blip/collab-crm`, branch `main`). Render reads `render.yaml` and provisions
   the service + disk. (Leave `DATABASE_URL` unset — that's deliberate.)
4. **Load the data onto the disk:** open the service's **Shell** in Render and upload
   `crm.db` to `/data/crm.db` (drag-drop in the Shell, or `scp`/`curl` it up). Restart.
   - Alternative: use the CRM's built-in **Backup/Restore** to seed it.
5. **Point the domain:** Render → Settings → **Custom Domains** → add
   `crm.collaborativeconceptsfl.com`; update the DNS CNAME as Render instructs.
6. **Decommission:** once the Render URL serves your real data, delete the Vercel project
   and the Neon database. Done — one vendor.

## Notes
- **Google login / Gmail widget:** keep it by adding `GOOGLE_OAUTH_CLIENT_ID` /
  `GOOGLE_OAUTH_CLIENT_SECRET` in the Render env (commented in `render.yaml`). Drop it to be
  truly single-vendor — the CRM has its own username/password login.
- **Backups:** the disk persists across deploys; snapshot `crm.db` on a schedule (the
  Backup feature + Google Drive mirroring still work as an off-site copy).
- **Branch:** `render.yaml` watches `main`. Merge the working branch to `main` (or change
  the Blueprint's branch) so Render builds the intended code.
