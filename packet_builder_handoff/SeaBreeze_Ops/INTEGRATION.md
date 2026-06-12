# Permit Packet Builder — integration handoff (for the job-manager dev)

This is everything needed to wire the **SeaBreeze HTML Job Manager** (`job-manager.html`)
to the **Permit Packet Builder**. The builder is a small local Flask app; the dashboard
just opens it pre-filled.

## The three pieces (ship all three, keep them side-by-side)

```
roof measurements\
  ├─ SeaBreeze_Ops\             ← the runnable app (Flask) + launcher
  │    app.py                   ← routes, incl. /builder and /builder/build
  │    build  → imported from ../permit_packet_builder/build.py
  │    db.py, workflow.py       ← (job-manager side; not needed just for the builder)
  │    templates\builder.html   ← the 6-step wizard UI
  │    static\style.css
  │    Start_Job_Manager.bat    ← double-click launcher (serves 127.0.0.1:5000)
  │    output\                  ← generated packet PDFs land here
  │    uploads\                 ← attached PDFs (RoofGraf, etc.)
  ├─ permit_packet_builder\
  │    build.py                 ← PDF ENGINE: build_packet(), SYSTEMS, list_ahjs()
  └─ SeaBreeze_Permit_Library\  ← ~990 MB: the ~40 PBC forms + product approvals
```

`build.py` finds the library by relative position (sibling of `permit_packet_builder`),
so it's portable as long as the three folders stay together. Override with the
`SEABREEZE_LIB` env var if needed.

### Requirements
- Python 3.x + `pip install flask pypdf reportlab pypdfium2`
- Start: double-click `SeaBreeze_Ops\Start_Job_Manager.bat` → serves `http://127.0.0.1:5000`

## The contract (this is the actual integration)

The dashboard opens the builder in a new tab, pre-filled via URL query params:

```
GET http://127.0.0.1:5000/builder?owner=...&address=...&city=...&zip=...&phone=...&value=...&ahj=...&system=...
```

Accepted params (all optional; the wizard lets the user fill/fix the rest):
`owner, address, city, zip, phone, email, pcn, legal, existing, area, slope, mrh,
exposure, value, ahj, system`

- `ahj`  must be one of `list_ahjs()` (e.g. `West_Palm_Beach`, `Boca_Raton` — underscores).
- `system` must be one of `Shingle | Tile | Metal | Flat`.

The wizard's **Build** button then POSTs to:

```
POST http://127.0.0.1:5000/builder/build   (multipart/form-data)
fields: owner, address, city, zip, phone, pcn, legal, existing, area, slope, mrh,
        exposure, value, ahj, system, attachments[] (PDF files)
→ 200 {"ok":true, "file":"<name>.pdf"}     download at /download/<file>
→ 400 {"error":"..."}                       (missing owner/address or AHJ/system)
```

## The dashboard side (already implemented in job-manager.html)

Near the top of the `<script>`:
```js
const BUILDER_URL = "http://127.0.0.1:5000";   // change port here if needed
```
`buildPacket(id)` maps a job → query string (parses the address into street/city/zip,
strips `$`/commas from the price) and `window.open`s `BUILDER_URL + "/builder?" + params`.
The button is rendered in the job modal footer (jobs pipeline only):
```js
${pipe.key==="jobs" ? `<button ... onclick="buildPacket('${j.id}')">🧾 Build Permit Packet</button>` : ""}
```

## Notes for the dev
- The builder must be **running** for the button to work (it's a local server, not in-page).
- Port is fixed at **5000** by the dashboard link; keep nothing else on 5000.
- The dashboard passes only what it knows (owner/address/phone/price). AHJ, system, PCN,
  legal, area/slope/MRH are chosen/typed in the wizard.
- `build.build_packet(client, ahj, system, attachment_paths, out_path)` is the single
  engine entry point if they want to call it directly (headless) instead of via HTTP.
