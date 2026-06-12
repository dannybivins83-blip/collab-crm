# SeaBreeze Job Management — Lead to Approved

A local **Flask + SQLite** job-tracking app for SeaBreeze Roofing & Sheet Metal, Inc.
It tracks every roofing job across a Kanban pipeline and fires **automation** as jobs
move through stages — including **auto-building the permit packet** when a job reaches
the *Permit* stage. It is the umbrella around the existing **Permit Packet Builder**
(`..\permit_packet_builder\`), whose engine it reuses directly.

---

## Quick start

**Double-click `Start_Job_Manager.bat`.**

It seeds a few example jobs on first run, starts the server on
`http://127.0.0.1:5000`, and opens your browser. Leave the console window open;
close it to stop the app.

> **Port note:** if port 5000 is already in use (e.g. the standalone **Permit
> Packet Builder** is running — it also uses 5000), the Job Manager automatically
> falls back to the next free port (5001, 5002, …) and opens your browser there.
> The console prints the exact URL it's running on. Force a specific port with the
> `SEABREEZE_PORT` environment variable.

To run manually:

```
cd SeaBreeze_Ops
python app.py
```

---

## Pipeline & automation

```
Lead → Inspection → Estimate → Signed → Permit → Approved → Production → Final Inspection → Completed
```

Each time a job **enters** a stage, the app logs an activity entry, creates a task
with a due date, and updates the job's *next action / next due*:

| Stage             | Auto-task created                                  | Due    |
|-------------------|----------------------------------------------------|--------|
| Lead              | Call client & schedule inspection                  | +1 day |
| Inspection        | Complete roof measurement / RoofGraf report        | +2 days|
| Estimate          | Send proposal & follow up                          | +3 days|
| Signed            | Collect deposit; prep permit packet                | +2 days|
| **Permit**        | **Auto-builds the permit packet**, then: Submit packet to AHJ portal | +1 day|
| Approved          | Schedule production crew                           | +3 days|
| Production        | Order materials; begin install                     | —      |
| Final Inspection  | Schedule final inspection                          | —      |
| Completed         | Collect final payment; register manufacturer warranty (clears next action) | — |

### Auto-built permit packet
When a job enters **Permit** and has an **AHJ + system + address**, the app calls
`build.build_packet(...)` from the Permit Packet Builder and saves the PDF to
`output\`. The filename is stored on the job; download it from the job detail page.
If required fields are missing or the build errors, it logs the problem and continues
(no crash). You can also **rebuild on demand** with the *Build Permit Packet* button.

---

## Using the app

- **Board (`/`)** — Kanban columns per stage. Cards show owner, address, system,
  value, days-in-stage, and a red flag when a follow-up is overdue. Each card has a
  quick **→ advance** button; click the name to open the job. The top shows per-stage
  counts and a **Follow-ups due** list.
- **New Job (`/job/new`)** — AHJ dropdown (39 PBC jurisdictions) and system dropdown
  (Shingle / Tile / Metal / Flat). New jobs start in *Lead* and fire its automation.
- **Job detail (`/job/<id>`)** — all fields, activity timeline, open tasks with *Done*
  buttons, **Advance Stage**, **Quick Build Packet**, **Open in Packet Builder**,
  **Download Packet**, **Edit**, and notes.

## Permit Packet Builder (embedded)

The full **Permit Packet Builder** wizard is built right into this app at **`/builder`**
(header → *Packet Builder*) — no separate app or port. It's the same 6-step flow
(Client Info → Attach Docs → County → AHJ → System → Build) and uses the shared
`build.build_packet()` engine, writing PDFs to `output\`.

Two ways to use it:
- **Standalone** — open *Packet Builder* from the header and build a packet for any
  client, attaching PDFs (RoofGraf, signed estimate, etc.).
- **From a job** — on a job's detail page click **Open in Packet Builder**; the wizard
  opens pre-filled with that job's owner/address/AHJ/system. When built, the packet is
  automatically **attached to the job** and logged in its activity timeline.

> This replaces the old standalone `permit_packet_builder\app.py` — you no longer need
> to run that separately (it was the second app competing for port 5000). Everything
> now lives in one Job Manager app.

---

## Files

| File | Purpose |
|------|---------|
| `app.py` | Flask routes + server (127.0.0.1:5000) |
| `db.py` | SQLite schema + CRUD helpers (`data\jobs.db`) |
| `workflow.py` | Stage list, automation map, `advance()`, packet build |
| `seed.py` | Seeds 2–3 example jobs at different stages |
| `templates\` | `base / dashboard / job / new_job` (Jinja) |
| `static\style.css` | SeaBreeze branding (navy / accent / tint) |
| `data\jobs.db` | SQLite database (created on first run) |
| `output\` | Generated permit packet PDFs (downloadable) |
| `Start_Job_Manager.bat` | Double-click launcher |

The Permit Packet Builder engine is imported from
`C:\Users\DBivi\roof measurements\permit_packet_builder\build.py`.

### Reset the example data
Delete `data\jobs.db` and the files in `output\`, then run `python seed.py`.
