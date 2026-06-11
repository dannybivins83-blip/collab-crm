# AccuLynx → CRM — owner collector click-list (fills the parity gap)

Hand this to Danny. These are browser bookmarklets on the CRM **Sync page** — they run on a
**logged-in AccuLynx tab** and POST into the CRM. They can't run headless, which is why they're
owner-gated. Each is **resumable** (~20–25 records/click) and tells you when a **full pass is
complete** — click it again until it says so.

## Pre-reqs (once)
1. `CRM_SYNC_SECRET` is set on the CRM host (else every POST 401s — fail-closed).
2. **Reinstall the bookmarklets** from the Sync page *after* the secret is set — they embed the
   key from the server-rendered page; old ones won't carry it.
3. Be **logged into AccuLynx** (`my.acculynx.com`) in the same browser.

## Run in THIS order (it closes the gaps from `docs/PARITY_2026-06-11.md`)
| # | Bookmarklet | Fills | Closes gap |
|---|---|---|---|
| 1 | ⭐ **AccuLynx Sync All → CRM** (`bmall`) | jobs+leads+contacts AND per-job **financial-progress (value/balance/collected)** + invoices + payments + estimates + comms, active pipeline (~800) | **#1 job values, #2 payments/invoices** — the big one |
| 2 | 💳 **AccuLynx Billing (all buckets)** (`bmbillwalk`) | billing for the **Closed/Canceled** buckets the active sync skips (662 closed jobs need this) | remaining #1/#2 for closed jobs |
| 3 | 🧾 **AccuLynx Estimate Lines** (`bmestlines`) | estimate line-item detail | estimate completeness |
| 4 | 📂 **AccuLynx Documents** (`bmalldocs`) | job documents (the ~900 un-mirrored) | #4 docs partial |
| 5 | 📸 **AccuLynx Photos** (`bmphotos`) | job/site photos | #3 photos (only 2 now) |
| 6 | 📐 **AccuLynx Roof Reports** (`bmroof`) | roof-report PDFs → measurements | #5 roof reports (0 now) |
| 7 | 💬 **AccuLynx Comms** (`bmcomm`) | only if step 1 didn't fully cover comms | #6 comms verify |

Steps 1–2 are the financial parity unblock; **#3 (`est_num`) + #4 fixes are already in (`b601082`)**,
so values/collected will parse + roll up correctly as they land. Click each repeatedly until "full
pass complete." After all passes, the parity-compare re-run gives the cancel-ready verdict.

## Render DB confirmation (can't verify headless)
After Render secrets + deploy, confirm Render serves the **~1,231-job** migrated DB (not the
25-job `crm.db`): log into the Render CRM and check the jobs count, or run a row count via the
Render shell. If it shows ~25, the migration must be reloaded onto Render's disk.
