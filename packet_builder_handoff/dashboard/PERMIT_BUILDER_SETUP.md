# Permit Packet Builder — how it connects to the dashboard

Each **Job** card in `job-manager.html` now has a green **🧾 Build Permit Packet**
button (open a job → bottom of the pop-up). It opens the **SeaBreeze Permit Packet
Builder** pre-filled with that job's owner, address, city, zip, phone, and price.
You then pick the **AHJ** (city) and **roof system**, attach any PDFs (RoofGraf, signed
estimate), and click **Build** to get the finished permit packet PDF.

## Why a separate little app?
The packet PDFs are assembled by a small **Python** program that fills your ~40 Palm
Beach County building-department forms and staples in the product approvals. A plain
double-click web page can't do that, so the builder runs as a tiny local app. The
dashboard just hands the job's info to it.

## Day-to-day use
1. **Start the builder once** (leave it running): double-click
   **`Start_Job_Manager.bat`** in the `SeaBreeze_Ops` folder. It opens in your browser.
2. In the dashboard, open a job → **🧾 Build Permit Packet**.
3. Pick AHJ + system, attach docs if any, **Build**, **Download**.

> If the button shows a "can't connect" page, the builder app isn't running — start
> `Start_Job_Manager.bat` and click the button again. The builder uses
> **http://127.0.0.1:5000** (the address the dashboard button points to). If you ever
> run it on a different port, update `BUILDER_URL` near the top of the
> `<script>` in `job-manager.html`.

## Running it on BOTH computers
The builder + its form library currently live on the **DBivi** PC. To use the button on
the **kjburnz** PC too, copy these three folders from the DBivi PC to the same drive on
kjburnz (keep them side-by-side in one parent folder so the builder finds its library):

```
roof measurements\
  ├─ SeaBreeze_Ops\            (the app + Start_Job_Manager.bat)
  ├─ permit_packet_builder\    (build.py — the PDF engine)
  └─ SeaBreeze_Permit_Library\ (the ~40 PBC forms + product approvals)
```

Then on the kjburnz PC, one time:
1. Install Python 3 (python.org), check "Add to PATH".
2. In a terminal: `pip install flask pypdf reportlab pypdfium2`
3. Double-click `SeaBreeze_Ops\Start_Job_Manager.bat`.

The builder locates the form library by its folder position (sibling of
`permit_packet_builder`), so it works on either PC as long as the three folders stay
together. (You can also force a location with the `SEABREEZE_LIB` environment variable.)

## Keeping the dashboard in sync
This `job-manager.html` (with the new button) lives in your Google Drive folder. If you
also keep a copy on the kjburnz PC at `C:\Users\kjburnz\acculynx roofr reprot\`, copy
this updated file there too so a future edit-and-copy doesn't overwrite the button.
