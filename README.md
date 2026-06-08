# White-Label Roofing/Construction CRM

A self-hostable, offline, **re-brandable AccuLynx-style CRM** for roofing &
construction contractors. Default skin is **SeaBreeze Roofing & Sheet Metal,
Inc.**, but every brand element (name, logo, colors, license #, contact info,
terms) is loaded from one `company_settings` row — change it in **Settings** and
the whole app + every generated PDF re-brands instantly. Resell to any contractor.

## Run

```bash
cd whitelabel-crm
pip install -r requirements.txt      # just Flask
python app.py                        # opens http://127.0.0.1:5050
```

No paid SaaS, no internet required. Data lives in `data/crm.db` (SQLite);
uploads (photos, docs, logos, PDFs) live in `uploads/`.

Environment overrides: `CRM_PORT`, `CRM_SECRET`, `CRM_NOBROWSER=1`, `CRM_DEBUG=1`.

## Modules

| Module | What it does |
|--------|--------------|
| **Dashboard** | Pipeline value, win rate, jobs-by-stage, overdue follow-ups, leaderboard |
| **Contacts** | People/companies, tags, lead source, per-contact activity timeline |
| **Sales Pipeline** | Kanban: New → Contacted → Appt → Estimate Sent → Deciding → Won/Lost. Drag-to-advance, per-stage checklists, follow-up clocks, stale-lead flags |
| **Jobs / Production** | Approved → Docs → HOA → Permit → Pre-Con → Started → Final → Closed. Checklists + 25/25/40/10 draw schedule + payments |
| **Estimates** | Line-item builder, per-work-type templates (shingle/tile/metal/flat + splits), markup/tax, live totals, e-signature pad, print-to-PDF proposal |
| **Invoicing** | Invoices against the draw schedule, paid/outstanding tracking |
| **Permits** | Per-job permit tracker (AHJ/system/status/permit#), optional packet-builder fold-in |
| **Materials** | Per-job supplier order sheets |
| **Calendar** | Appointments / crew scheduling with reminders |
| **Tasks** | Assignable follow-ups with due dates + overdue flags |
| **Reports** | Pipeline by stage, win rate, revenue, A/R, leaderboard, source performance |
| **Communications** | Call/email/SMS log + draft emails for review (never auto-sent) |
| **Settings** | White-label: company profile, logo upload, theme colors, license #, users/roles |

## Architecture

```
app.py          Flask entry — registers one blueprint per module, serves /uploads
config.py       paths + port
db.py           SQLite schema + generic insert/update/get/all_rows helpers + seed
constants.py    domain model: pipeline stages, checklists, draw schedule, estimate templates
theme.py        brand context injection + follow-up clock + money math
modules/        one blueprint per module
templates/      Jinja templates (base.html pulls brand from company_settings)
static/app.css  theme via CSS variables fed from company colors
```

The pipeline stages, per-stage checklists, draw schedule, and estimate-template
mapping mirror the working SeaBreeze `job-manager.html` and the AccuLynx template
set, so the domain model matches the real workflow.

## Re-branding for another contractor

1. Go to **Settings**, set company name / license / address / phone / email.
2. Upload a logo and pick theme colors.
3. Save — the topbar, sidebar, and every PDF proposal now show the new brand.

Nothing in the code hard-codes "SeaBreeze"; the default is just the seed row in
`db.py` (`_seed_if_empty`).
