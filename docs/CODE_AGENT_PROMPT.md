# Code-Agent Prompt — extend the white-label AccuLynx clone

Copy everything below the line into the coding agent.

---

You are extending a **self-hostable, white-label AccuLynx clone** for roofing contractors.
It already runs. Your job is to add the next set of AccuLynx-parity features **without
breaking what works**, matching the existing conventions and the AccuLynx look/terminology.

## Project
- Location: `C:\Users\kjburnz\acculynx roofr reprot\whitelabel-crm\`
- Stack: **Python Flask + SQLite + server-rendered Jinja + vanilla JS**. No build step, no
  paid SaaS. Run with `python app.py` (serves http://127.0.0.1:5050, auth required).
- Default brand: SeaBreeze Roofing, but **everything brandable comes from `company_settings`** —
  never hardcode a company name/color/license in a module or template.

## Read these first (do not skip)
- `docs/ACCULYNX_KB.md` — the complete AccuLynx feature taxonomy + the gap table you're closing.
- `docs/ACCULYNX_LIVE_CAPTURE.md` and `docs/ACCULYNX_SPEC.md` — verified colors, milestones, layout.
- `constants.py` — pipeline buckets (L/P/A/C/I), JOB_STAGES milestones, DRAW_SCHEDULE, estimate templates.
- `db.py`, `theme.py`, and any one module (e.g. `modules/permits.py`) for the conventions below.

## Conventions you MUST follow (match the existing code exactly)
- **Blueprints**: one module per feature in `modules/<name>.py` exposing `bp = Blueprint(...)`;
  register it in `app.py`'s blueprint loop. URL prefix `/<name>`.
- **DB**: use the generic helpers in `db.py` — `insert(table, dict)`, `update(table, id, **f)`,
  `get(table, id)`, `all_rows(table, where, params, order)`, `execute(sql, params)`,
  `add_activity(entity_type, entity_id, kind, text)`, `load_json`/`dump_json`. Add new tables to
  the `SCHEMA` string. For columns on existing tables use `_ensure_column(table, col, decl)` in
  `init_db()` (never destructive migrations).
- **Department scoping**: list views filter by department, e.g.
  `db.all_rows("jobs", "department=?", (theme.current_department(),))`. New job-scoped records
  inherit the job's department.
- **Auth/roles**: `from modules.auth import current_user`; admin-only config lives under paths in
  `auth.ADMIN_ONLY_PATHS`. Add new admin pages there if they're configuration.
- **Templates**: `{% extends "base.html" %}`, use the existing CSS classes (`.card`, `.grid`,
  `.kpi`, `.board`, `.btn/.primary/.accent`, `.pill`, `.badge`, `.detail`, `.toolbar`). Use CSS
  vars (`--accent`, `--accent2`, `--warn`, `--danger`) — never literal hex in templates.
- **Money math**: reuse `theme.est_num`, `theme.money`, `theme.money_k`. Estimate totals already
  live in `modules/estimates.py` (`estimate_totals`, `_load_sections`); reuse, don't duplicate.
- **Keep it runnable after every phase.** After each phase: restart, log in
  (`owner@seabreezeroofing.com` / `seabreeze2026`), and confirm no page 500s
  (smoke-test pattern: `app.test_client()` POST `/login` then GET each new route).
- Match AccuLynx **terminology**: Worksheet, Order, Order Manager, Workflow Manager, Commission,
  Profit Analysis, Milestone, Lead Rank.

## Build these, in order (each independently shippable)

### Phase 1 — Worksheet + Profit Analysis  (highest priority)
AccuLynx's "Worksheet" sets the job's value and tracks budget vs actual cost → profit.
- New table `worksheets` (one per job): job_id, contract_value REAL, plus derived rollups.
- New table `worksheet_lines`: worksheet_id, category (Material|Labor|Permit|Overhead|Other),
  description, budget_cost REAL, actual_cost REAL, sort.
- Seed a worksheet from the job's signed estimate (pull section line costs as budget lines).
- `modules/worksheet.py`: view/edit worksheet on the job (a new **Worksheet tab** on
  `job_detail.html`), inline-editable budget/actual per line, add/remove lines.
- **Profit Analysis** panel: contract value, total budget cost, total actual cost,
  gross profit ($ and %), variance (budget vs actual). Show it on the job detail and surface
  job profit on the jobs list. Reuse the estimate margin math style.
- Acceptance: a job with a signed estimate auto-builds a worksheet; editing actual costs
  updates profit live; numbers reconcile with the estimate.

### Phase 2 — Orders + Order Manager
AccuLynx generates **material + labor orders (POs)** from the estimate and queues them.
- Extend/replace the thin `materials` module into `orders`: table `orders` (job_id, type
  Material|Labor, vendor, po_number, status draft|ordered|delivered|received, ordered_date,
  delivery_date, notes, department) + `order_lines` (order_id, description, unit, qty, cost).
- "Generate order from estimate": create a Material order pre-filled from the estimate's
  material lines and a Labor order from labor lines (infer by line description/unit).
- New table `vendors` (name, type, phone, email, address) managed in Settings (admin).
- **Order Manager** page (`/orders`): cross-job queue of all orders with filters
  (status, type, vendor, department), matching the jobs-list layout.
- Order detail with editable lines, PO number, vendor picker, status workflow; "Mark ordered/
  delivered" logs a job activity. PO print view using the company letterhead (like estimate_print).
- Acceptance: from a job estimate you can spin up a material + labor PO; Order Manager lists
  them across jobs; status changes log to the job timeline.

### Phase 3 — Workflow Manager (Automations)
AccuLynx fires tasks/emails on milestone changes. We already have follow-up clocks; formalize as rules.
- Table `automations` (admin-configured): name, trigger_stage (a JOB_STAGE key or lead stage),
  action_type (create_task|draft_email|create_reminder), template_text, offset_days, active.
- On stage change in `jobs.py`/`leads.py` `set_stage`/`advance`/`move`, evaluate matching active
  automations and execute: create an `activities` task (kind='task', due=today+offset) and/or a
  draft in `comms` (NEVER auto-send — draft only, per house rules).
- Admin UI under Settings → **Workflow Manager**: list/create/edit/toggle automations.
- Seed a few sensible defaults mirroring the per-stage checklists/todos already in `constants.py`.
- Acceptance: advancing a job to "Permit Applied For" auto-creates the configured task + email draft;
  toggling the rule off stops it.

### Phase 4 — Commissions
- Table `commissions`: job_id, rep, basis (contract_value|profit), rate_pct, amount REAL,
  status (pre|approved|paid), notes. "Pre-Commission" = computed on sale; "Commission" = approved on completion.
- Compute from the worksheet profit or contract value; surface on the rep leaderboard + a
  `/commissions` list; a per-rep summary in Reports.
- Acceptance: a sold job shows a pre-commission; on completion it can be approved → appears in payouts.

### Phase 5 — Config + intelligence (smaller, do last)
- **Custom Fields**: table `custom_fields` (entity lead|job, label, type text|number|select,
  options, sort) + `custom_values` (field_id, entity_type, entity_id, value); render on lead/job
  forms + detail; admin-managed in Settings.
- Move **Contact Types** and **Lead Sources** from the hardcoded `constants.LEAD_SOURCES` into
  editable Settings tables (keep constants as the seed).
- **Lead Rank**: add a `rank` (1–4) field on leads with the green-bars UI already seen in AccuLynx;
  show on the leads list/board and let it sort.

## Guardrails
- Do **not** auto-send any email/SMS or take any payment — draft only; payments stay manual.
- Do not break auth, department scoping, or the existing estimate/measurement/permit flows.
- Keep diffs surgical; follow the file/naming patterns already in the repo.
- After each phase, update `docs/ACCULYNX_CHANGELOG.md` with what shipped and run the smoke test.

Deliver Phase 1 first and confirm it runs before starting Phase 2.
