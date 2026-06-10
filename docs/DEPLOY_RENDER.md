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

---

## ✅ Migration dry-run — VERIFIED (2026-06-10)
The data export + app boot were run and tested **before any go-live**:
- `python scripts/neon_to_sqlite.py "$NEON_URL" ./data/crm_migration.db` →
  **26,258 rows** copied (jobs 1231, leads 309, estimates 177, worksheets 580 + 3,972 lines,
  invoices 429, payments 178, measurements 850, library_docs 146, signup_packets 6, users 6).
- App booted on that SQLite file (`CRM_DB_PATH=…/crm_migration.db`, `DATABASE_URL=""`) and all
  key routes returned **200**: `/login`, `/portal/<token>`, dashboard `/`, `/jobs/list`,
  `/pipeline/`, a job detail. Engine confirmed **SQLite**, all counts present.
- `data/` (the customer DB + uploads) is **gitignored** — never committed.

## Parallel go-live (Vercel stays up the whole time)
Render runs **alongside** Vercel on its own URL; you only move the domain at the very end.

1. **Create the service (parallel):** Render Dashboard → **New → Blueprint** → connect
   `dannybivins83-blip/collab-crm`. Render reads `render.yaml` and provisions the web service +
   5 GB disk at `/data`. It comes up at `https://collab-crm.onrender.com` — **Vercel/Neon are
   untouched.**
2. **Set the secrets** (Render → the service → Environment): `GDRIVE_SA_JSON`,
   `GDRIVE_FOLDER_ID` (so existing files still serve), `GOOGLE_OAUTH_CLIENT_ID/SECRET`,
   `MEASURE_CRM_WEBHOOK_SECRET`, `SEABREEZE_CRM_WEBHOOK_SECRET`.
3. **Load the data:** open Render → **Shell**, then upload `data/crm_migration.db` to
   **`/data/crm.db`** (drag-drop in the Shell file UI, or `scp`/`curl` it in). This is the
   tested export from the dry-run above.
4. **TEST on the Render URL** (not the domain): log in, open the dashboard, a job, a homeowner
   portal, and **open a document** (the persistent-disk fix — should open cleanly). Run the
   measurement-ingest smoke test against the Render URL too.
5. **Cut over only after it passes:** Render → Settings → **Custom Domains** → add
   `crm.collaborativeconceptsfl.com`; update the DNS record from Vercel to Render's target.
   Vercel can stay as a warm fallback for a day, then be retired.

## Files on Render (existing vs new)
- **New** uploads/PDFs write to `/data/uploads` on the disk (persistent — this is what fixes
  "saved but won't open").
- **Existing** files (currently in Drive; `file_blobs` came over empty) serve via the Drive
  fallback as long as `GDRIVE_SA_JSON`/`GDRIVE_FOLDER_ID` are set. To go fully Drive-free later,
  backfill Drive → `/data/uploads` and remove those two env vars.

## Re-export note
`neon_to_sqlite.py` refuses to overwrite an existing target. Between dry-run and the real
cutover, **re-run the export** (delete the old `crm_migration.db` first) so the SQLite copy
includes any records created on Vercel/Neon in the meantime — then upload the fresh file.
