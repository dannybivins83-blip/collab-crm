# -*- coding: utf-8 -*-
"""Permits module — per-job permit tracking + the SeaBreeze permit-packet builder.

Folds in the existing build.py engine (SeaBreeze_Permit_Library) so a fully
pre-filled county permit packet PDF can be generated straight from a job: pick
AHJ + system + underlayment + product, attach the RoofGraf report, click Build.
"""
import os
import re
import sys
import time

from flask import Blueprint, render_template, request, redirect, url_for, flash

import config
import db

bp = Blueprint("permits", __name__, url_prefix="/permits")

PERMIT_SYSTEMS = ["shingle", "tile", "metal", "flat"]
PERMIT_STATUS = ["prep", "submitted", "approved", "closed"]
FIELDS = ["job_id", "ahj", "county", "system", "permit_number", "submitted_date",
          "approved_date", "notes"]

# Make the permit-packet builder importable (engine + SeaBreeze_Permit_Library).
# packet_builder_handoff/ is now inside the repo (whitelabel-crm/packet_builder_handoff/)
# so Render can reach it.  Fall back to the legacy sibling-dir location for local
# installs that haven't pulled the new copy yet.
_BUILDER_DIR = os.path.normpath(os.path.join(
    config.HERE, "packet_builder_handoff", "permit_packet_builder"))
if not os.path.isdir(_BUILDER_DIR):
    # Legacy: sibling directory outside the repo root (pre-copy fallback)
    _BUILDER_DIR = os.path.normpath(os.path.join(
        config.HERE, "..", "packet_builder_handoff", "permit_packet_builder"))
if _BUILDER_DIR not in sys.path and os.path.isdir(_BUILDER_DIR):
    sys.path.insert(0, _BUILDER_DIR)

# System key map: our lowercase -> build.py's capitalized.
_SYS_MAP = {"shingle": "Shingle", "tile": "Tile", "metal": "Metal", "flat": "Flat"}


def _build():
    """Import the build engine lazily; None if unavailable."""
    try:
        import build
        return build
    except Exception:
        return None


def _builder_available():
    return _build() is not None


def _norm_ahj(s):
    """Normalize an AHJ string for matching: 'Boca Raton' == 'Boca_Raton' == 'boca-raton'."""
    return re.sub(r"[^a-z0-9]+", "_", str(s or "").strip().lower()).strip("_")


def builder_meta(system_lower=None, current_ahj=None):
    """AHJ list + system/underlayment/product options for the wizard.
    `available` requires the ENGINE *and* the 1.1GB form library — on hosts where
    build.py imports but the library folder is absent (Render), the old check
    rendered a dead form with an empty required AHJ dropdown. `sel_ahj` is the
    library key matching the permit tracker's AHJ ('Boca Raton' → 'Boca_Raton')
    so the dropdown pre-fills from the tracker field."""
    b = _build()
    empty = {"available": False, "ahjs": [], "systems": PERMIT_SYSTEMS,
             "uls": [], "products": [], "sel_ahj": ""}
    if not b:
        return empty
    ahj_keys = b.list_ahjs()
    if not ahj_keys:          # engine importable but the form library isn't on this host
        return empty
    sysname = _SYS_MAP.get(system_lower or "", "")
    # Match the tracker's AHJ to a library key: exact normalized match first,
    # then a unique containment match ('City of Boca Raton' → 'Boca_Raton').
    sel = ""
    want = _norm_ahj(current_ahj)
    if want:
        by_norm = {_norm_ahj(k): k for k in ahj_keys}
        if want in by_norm:
            sel = by_norm[want]
        else:
            cands = [k for n, k in by_norm.items() if n and (n in want or want in n)]
            if len(cands) == 1:
                sel = cands[0]
    return {
        "available": True,
        "ahjs": [(a, a.replace("_", " ")) for a in ahj_keys],
        "systems": list(b.SYSTEMS.keys()),
        "uls": b.ul_choices(sysname) if sysname else [],
        "products": b.prod_choices(sysname) if sysname else [],
        "sel_ahj": sel,
    }


@bp.route("/")
def index():
    import theme as _theme
    dept = _theme.current_department()
    _dept_jobs = db.all_rows("jobs", "department=?", (dept,))
    dept_job_ids = {j["id"] for j in _dept_jobs}
    job_map = {j["id"]: j for j in _dept_jobs}
    if dept_job_ids:
        _id_ph = ",".join("?" * len(dept_job_ids))
        rows = db.all_rows("permits", "job_id IS NULL OR job_id IN (%s)" % _id_ph,
                           tuple(dept_job_ids), "id DESC")
    else:
        rows = db.all_rows("permits", "job_id IS NULL", order="id DESC")
    for p in rows:
        p["_job"] = job_map.get(p["job_id"])
    q = request.args.get("q", "").strip().lower()
    status_f = request.args.get("status", "").strip()
    if q:
        rows = [p for p in rows if
                q in (p.get("permit_number") or "").lower() or
                q in (p.get("ahj") or "").lower() or
                q in (p.get("county") or "").lower() or
                q in ((p["_job"] or {}).get("name") or "").lower()]
    if status_f:
        rows = [p for p in rows if p.get("status") == status_f]
    return render_template("permits.html", permits=rows, status_list=PERMIT_STATUS,
                           q=q, status_f=status_f)


@bp.route("/new", methods=["GET", "POST"])
def new():
    if request.method == "POST":
        data = {f: request.form.get(f, "").strip() for f in FIELDS}
        data["status"] = request.form.get("status", "prep")
        # Validate job_id FK before insert to prevent orphan permits.
        if data.get("job_id"):
            if not db.get("jobs", data["job_id"]):
                flash("Job #%s not found — permit not created." % data["job_id"], "error")
                return redirect(url_for("permits.new"))
        pid = db.insert("permits", data)
        if data.get("job_id"):
            db.add_activity("job", int(data["job_id"]), "automation",
                            "Permit record created (%s · %s)" % (data.get("ahj"), data.get("system")))
        flash("Permit created.", "ok")
        return redirect(url_for("permits.detail", permit_id=pid))
    job_id = request.args.get("job_id", "")
    job = db.get("jobs", job_id) if job_id else None
    pre = {}
    if job:
        pre = {"job_id": job["id"], "ahj": job.get("ahj"), "county": job.get("county"),
               "system": job.get("system")}
    import theme as _theme
    dept = _theme.current_department()
    return render_template("permit_form.html", permit=pre,
                           jobs=db.all_rows("jobs", "department=?", (dept,), "name"),
                           systems=PERMIT_SYSTEMS, mode="new",
                           company=db.get_company())


@bp.route("/<int:permit_id>")
def detail(permit_id):
    p = db.get("permits", permit_id)
    if not p:
        return redirect(url_for("permits.index"))
    sys_for_meta = request.args.get("sys") or p.get("system")
    if request.args.get("sys"):
        p["system"] = request.args.get("sys")  # reflect the picker selection
    from modules import ahj as ahj_mod
    return render_template("permit_detail.html", p=p,
                           job=db.get("jobs", p["job_id"]) if p.get("job_id") else None,
                           systems=PERMIT_SYSTEMS, status_list=PERMIT_STATUS,
                           meta=builder_meta(sys_for_meta, current_ahj=p.get("ahj")),
                           portal=ahj_mod.ahj_portal(p.get("ahj")))


@bp.route("/<int:permit_id>/save", methods=["POST"])
def save(permit_id):
    data = {f: request.form.get(f, "").strip() for f in FIELDS if f != "job_id"}
    data["status"] = request.form.get("status", "prep")
    db.update("permits", permit_id, **data)
    flash("Permit updated.", "ok")
    return redirect(url_for("permits.detail", permit_id=permit_id))


@bp.route("/<int:permit_id>/build", methods=["POST"])
def build_packet(permit_id):
    """Generate a permit packet PDF via the SeaBreeze build engine."""
    p = db.get("permits", permit_id)
    if not p:
        return redirect(url_for("permits.index"))
    ahj = request.form.get("ahj", "").strip()
    system_lower = request.form.get("system", "").strip()
    underlayment = request.form.get("underlayment", "").strip() or None
    product = request.form.get("product", "").strip() or None
    # Persist the chosen AHJ/system back onto the permit.
    db.update("permits", permit_id, ahj=ahj, system=system_lower)

    # Sanitize user-supplied strings used in filename construction (path traversal fix).
    ahj = re.sub(r'[^A-Za-z0-9 _-]', '', ahj)[:64]
    system_lower = re.sub(r'[^A-Za-z0-9 _-]', '', system_lower)[:32]
    b = _build()
    if not b:
        flash("Permit builder engine (build.py) not available on this host.", "error")
        return redirect(url_for("permits.detail", permit_id=permit_id))
    system = _SYS_MAP.get(system_lower)
    if not ahj or system not in b.SYSTEMS:
        flash("Pick an AHJ and a valid system before building.", "error")
        return redirect(url_for("permits.detail", permit_id=permit_id))

    job = db.get("jobs", p["job_id"]) if p.get("job_id") else {}
    job = job or {}
    client = {"owner": job.get("name", ""), "name": job.get("name", ""),
              "address": job.get("address", ""),
              "city": job.get("city", ""), "zip": job.get("zip", ""),
              "phone": job.get("phone", ""), "pcn": job.get("pcn", ""),
              "legal": job.get("legal", ""), "existing": job.get("existing", ""),
              "area": job.get("area", ""), "slope": job.get("slope", ""),
              "mrh": job.get("mrh", ""), "exposure": job.get("exposure", ""),
              "value": job.get("contract_value", "")}
    # Contractor profile — use default profile for current tenant if set; else None (SB defaults).
    _contractor = None
    try:
        _cp_rows = db.all_rows("contractor_profiles", "is_default=1")
        _contractor = dict(_cp_rows[0]) if _cp_rows else None
    except Exception:
        pass
    # SAFETY (docs/PERMIT_SIGNATURE.md): the captured owner e-signature is deliberately NOT
    # forwarded into the permit packet. Permit forms — the Notice of Commencement and the
    # re-roof nailing affidavit — are NOTARIZED: the owner's signature on them IS the
    # notarized signature and must be wet-signed or RON-signed in the notary's presence.
    # Stamping a pre-captured signature there would be forgery of a notarized instrument.
    # Captured-signature auto-apply is limited to the estimate proposal and the
    # (non-notarized) sign-up package.

    # Optional RoofGraf attachment.
    attachments = []
    f = request.files.get("attachment")
    if f and f.filename:
        ap = os.path.join(config.PERMIT_DIR, "att_%d_%s" % (
            int(time.time() * 1000), re.sub(r"[^A-Za-z0-9._-]+", "_", f.filename)))
        f.save(ap)
        attachments.append(ap)
        # Pull squares/pitch from the report if the job lacks them.
        if not client.get("area") or not client.get("slope"):
            meas = b.parse_roofgraf(ap) or {}
            client["area"] = client.get("area") or meas.get("area", "")
            client["slope"] = client.get("slope") or meas.get("pitch", "")

    safe = re.sub(r"[^A-Za-z0-9]+", "_", client["owner"] or "client").strip("_") or "client"
    fname = "Permit_%d_%s_%s_%s.pdf" % (permit_id, safe, ahj, system_lower)
    out_path = os.path.join(config.PERMIT_DIR, fname)
    try:
        b.build_packet(client, ahj, system, attachments, out_path, underlayment, product, contractor=_contractor)
    except Exception as e:
        flash("Build failed: %s" % e, "error")
        return redirect(url_for("permits.detail", permit_id=permit_id))

    db.update("permits", permit_id, packet_file="permits/" + fname)
    # Mirror to Google Drive so a packet built on the desktop is downloadable from
    # the cloud (Vercel can't host the 1.1 GB library, but it serves the finished PDF).
    try:
        from modules import gdrive
        if gdrive.enabled():
            gdrive.mirror(out_path, fname)
    except Exception:
        pass
    if p.get("job_id"):
        db.insert("documents", {"job_id": p["job_id"], "category": "Permit",
                                "filename": fname, "original_name": fname,
                                "size": os.path.getsize(out_path) if os.path.exists(out_path) else 0,
                                "notes": "Permit packet (%s, %s)" % (ahj.replace("_", " "), system)})
        db.add_activity("job", p["job_id"], "automation",
                        "Permit packet built: %s (%s)" % (ahj.replace("_", " "), system))
    flash("Permit packet built: %s" % fname, "ok")
    return redirect(url_for("permits.detail", permit_id=permit_id))


@bp.route("/<int:permit_id>/delete", methods=["POST"])
def delete(permit_id):
    db.delete("permits", permit_id)
    flash("Permit deleted.", "ok")
    return redirect(url_for("permits.index"))


# --- Portal account registration tracker (Consolidation #4) ------------------
# Tracks per-(platform/AHJ) contractor registration status so the team knows
# which portals are ready for auto-submit vs. need one-time registration first.

@bp.route("/portal-accounts")
def portal_accounts():
    from modules import ahj as ahj_mod
    accounts = db.all_rows("portal_accounts", order="platform, ahj")
    portals = ahj_mod._load_portals()
    # Pre-compute once (was an O(n×m) set rebuild inside the list comprehension + unused `registered` var).
    _have = {(r.get("platform", ""), r.get("ahj", "")) for r in accounts}
    missing = [(k, p) for k, p in portals.items()
               if p.get("online") and (p.get("platform", ""), k) not in _have]
    return render_template("portal_accounts.html", accounts=accounts, missing=missing[:50])


@bp.route("/portal-accounts/seed", methods=["POST"])
def portal_accounts_seed():
    """Seed the portal_accounts table from ahj_portals.json for all online AHJs."""
    from modules import ahj as ahj_mod
    portals = ahj_mod._load_portals()
    existing = {(r.get("platform", ""), r.get("ahj", "")) for r in db.all_rows("portal_accounts")}
    added = 0
    for ahj_key, p in portals.items():
        if not p.get("online"):
            continue
        key = (p.get("platform", ""), ahj_key)
        if key in existing:
            continue
        db.insert("portal_accounts", {
            "created": db.now(), "updated": db.now(),
            "platform": p.get("platform", ""),
            "ahj": ahj_key, "city": p.get("city", ""), "county": p.get("county", ""),
            "registration_status": "pending",
        })
        added += 1
    flash("Seeded %d portal account records." % added, "ok")
    return redirect(url_for("permits.portal_accounts"))


@bp.route("/portal-accounts/<int:account_id>/update", methods=["POST"])
def portal_account_update(account_id):
    status = request.form.get("registration_status", "pending")
    notes = request.form.get("notes", "")
    username = request.form.get("username", "")
    db.update("portal_accounts", account_id,
              registration_status=status, notes=notes, username=username,
              updated=db.now(), last_checked=db.today())
    flash("Portal account updated.", "ok")
    return redirect(url_for("permits.portal_accounts"))


# --- Contractor profile management -------------------------------------------

@bp.route("/contractor-profile")
def contractor_profile():
    rows = db.all_rows("contractor_profiles")
    return render_template("contractor_profile.html", profiles=rows)


@bp.route("/widget/embed")
def widget_embed():
    """Phase 4 — self-contained iframe embed widget.

    GET /permits/widget/embed?api_key=pk_live_...

    Returns a standalone HTML page (no Flask session required) that any contractor
    can drop into their own site with one line:
      <iframe src="https://crm.collaborativeconceptsfl.com/permits/widget/embed?api_key=pk_live_..."
              width="600" height="500"></iframe>

    The widget:
      - Collects address, AHJ (auto-suggest from /api/v1/permits/ahjs), system type
      - Posts to /api/v1/permits/build (async), polls /api/v1/permits/build/<id>/status
      - Shows progress bar; offers download when complete
    """
    from modules import permit_api as _papi
    api_key = request.args.get("api_key", "").strip()
    if not api_key:
        return ("<html><body style='font-family:sans-serif;padding:20px'>"
                "<b>Missing api_key parameter.</b><br>"
                "Usage: <code>/permits/widget/embed?api_key=pk_live_...</code>"
                "</body></html>"), 400

    key_row = _papi._validate_key(api_key)
    if not key_row:
        return ("<html><body style='font-family:sans-serif;padding:20px'>"
                "<b>Invalid API key.</b>"
                "</body></html>"), 401

    base_url = request.host_url.rstrip("/")
    html = _WIDGET_HTML.replace("__API_KEY__", api_key).replace("__BASE_URL__", base_url)
    return html, 200, {"Content-Type": "text/html; charset=utf-8",
                       "X-Frame-Options": "ALLOWALL",
                       "Content-Security-Policy": "frame-ancestors *"}


# ---------------------------------------------------------------------------
# Self-contained embed widget HTML (no Jinja — must work cross-origin).
# __API_KEY__ and __BASE_URL__ are replaced at serve time.
# ---------------------------------------------------------------------------
_WIDGET_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Permit Packet Builder</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
       font-size:14px;background:#f5f7fa;color:#1a2a40;padding:16px}
  h2{font-size:16px;font-weight:700;margin-bottom:14px;color:#0d47a1}
  label{display:block;font-size:12px;font-weight:600;color:#4a5568;margin-bottom:2px;margin-top:10px}
  input,select{width:100%;padding:8px 10px;border:1px solid #cbd5e0;border-radius:6px;
               font-size:13px;background:#fff;color:#1a2a40}
  input:focus,select:focus{outline:none;border-color:#1565c0;box-shadow:0 0 0 2px rgba(21,101,192,.15)}
  button#build-btn{margin-top:16px;width:100%;padding:10px;background:#1565c0;color:#fff;
                   border:none;border-radius:7px;font-size:14px;font-weight:600;cursor:pointer}
  button#build-btn:disabled{background:#90a4ae;cursor:not-allowed}
  #progress-wrap{margin-top:14px;display:none}
  #progress-bar-bg{background:#e2e8f0;border-radius:99px;height:8px;overflow:hidden}
  #progress-bar{height:8px;background:#1565c0;border-radius:99px;width:0%;transition:width .4s}
  #status-msg{font-size:12px;color:#555;margin-top:6px}
  #download-btn{display:none;margin-top:12px;width:100%;padding:10px;background:#2e7d32;
                color:#fff;border:none;border-radius:7px;font-size:14px;font-weight:600;cursor:pointer}
  #error-msg{display:none;margin-top:10px;padding:8px 12px;background:#fdecea;
             border:1px solid #f5c6cb;border-radius:6px;color:#b71c1c;font-size:12px}
  #ahj-suggestions{position:absolute;background:#fff;border:1px solid #cbd5e0;border-radius:6px;
                    box-shadow:0 4px 12px rgba(0,0,0,.12);max-height:150px;overflow-y:auto;
                    z-index:99;width:100%}
  #ahj-suggestions div{padding:7px 12px;cursor:pointer;font-size:13px}
  #ahj-suggestions div:hover{background:#e8f0fe}
  .ahj-wrap{position:relative}
  .powered{margin-top:14px;font-size:10px;color:#aaa;text-align:center}
</style>
</head>
<body>
<h2>Build Permit Packet</h2>
<form id="widget-form" autocomplete="off">
  <label for="w-address">Property Address</label>
  <input id="w-address" type="text" placeholder="123 Main St, City, FL 33400" required>

  <label for="w-ahj">Jurisdiction (AHJ)</label>
  <div class="ahj-wrap">
    <input id="w-ahj" type="text" placeholder="Start typing a city or county..." autocomplete="off" required>
    <input id="w-ahj-key" type="hidden">
    <div id="ahj-suggestions" style="display:none"></div>
  </div>

  <label for="w-system">Roof System</label>
  <select id="w-system" required>
    <option value="">-- Select system --</option>
    <option value="shingle">Shingle</option>
    <option value="metal">Metal</option>
    <option value="tile">Tile</option>
    <option value="flat">Flat</option>
  </select>

  <button type="submit" id="build-btn">Build Permit Packet</button>
  <div id="error-msg"></div>
  <div id="progress-wrap">
    <div id="progress-bar-bg"><div id="progress-bar"></div></div>
    <div id="status-msg">Starting build...</div>
  </div>
  <button type="button" id="download-btn">Download Permit Packet (PDF)</button>
</form>
<div class="powered">Powered by Collaborative Concepts CRM</div>

<script>
(function(){
  var BASE = "__BASE_URL__";
  var KEY  = "__API_KEY__";
  var _ahjs = [];
  var _poll = null;

  // Pre-load AHJ list (best-effort; widget still works if unavailable).
  fetch(BASE + "/api/v1/permits/ahjs", {
    headers: {"X-Permit-API-Key": KEY}
  }).then(function(r){return r.json()}).then(function(d){
    if(d.ok && d.ahjs) _ahjs = d.ahjs;
  }).catch(function(){});

  // AHJ auto-suggest
  var ahjIn = document.getElementById("w-ahj");
  var ahjKey = document.getElementById("w-ahj-key");
  var sug = document.getElementById("ahj-suggestions");

  ahjIn.addEventListener("input", function(){
    var q = ahjIn.value.toLowerCase().replace(/_/g," ");
    if(!q || q.length < 2){ sug.style.display="none"; return; }
    var matches = _ahjs.filter(function(a){
      return a.toLowerCase().replace(/_/g," ").indexOf(q) !== -1;
    }).slice(0,10);
    if(!matches.length){ sug.style.display="none"; return; }
    sug.innerHTML = "";
    matches.forEach(function(a){
      var d = document.createElement("div");
      d.textContent = a.replace(/_/g," ");
      d.addEventListener("mousedown", function(e){ e.preventDefault(); });
      d.addEventListener("click", function(){
        ahjIn.value = a.replace(/_/g," ");
        ahjKey.value = a;
        sug.style.display = "none";
      });
      sug.appendChild(d);
    });
    sug.style.display = "block";
  });
  ahjIn.addEventListener("blur", function(){ setTimeout(function(){ sug.style.display="none"; }, 150); });

  // Progress helpers
  function setProgress(pct, msg){
    document.getElementById("progress-bar").style.width = pct + "%";
    document.getElementById("status-msg").textContent = msg;
  }
  function showError(msg){
    var el = document.getElementById("error-msg");
    el.textContent = msg; el.style.display = "block";
  }

  // Form submit
  document.getElementById("widget-form").addEventListener("submit", function(e){
    e.preventDefault();
    var address = document.getElementById("w-address").value.trim();
    var ahjVal  = (ahjKey.value || ahjIn.value).trim().replace(/ /g,"_");
    var system  = document.getElementById("w-system").value;
    if(!address || !ahjVal || !system){ showError("Please fill in all fields."); return; }

    document.getElementById("error-msg").style.display = "none";
    document.getElementById("build-btn").disabled = true;
    document.getElementById("progress-wrap").style.display = "block";
    document.getElementById("download-btn").style.display = "none";
    setProgress(10, "Submitting build request...");

    fetch(BASE + "/api/v1/permits/build", {
      method: "POST",
      headers: {"Content-Type":"application/json","X-Permit-API-Key": KEY},
      body: JSON.stringify({
        job: {address: address, ahj: ahjVal, system: system},
        api_key: KEY
      })
    }).then(function(r){return r.json()}).then(function(d){
      if(!d.ok){ showError(d.error || "Build submission failed."); document.getElementById("build-btn").disabled=false; return; }
      setProgress(25, "Build queued — polling for status...");
      pollStatus(d.job_id, d.download_url);
    }).catch(function(err){ showError("Network error: " + err); document.getElementById("build-btn").disabled=false; });
  });

  function pollStatus(jobId, dlUrl){
    var attempts = 0;
    _poll = setInterval(function(){
      attempts++;
      var pct = Math.min(25 + attempts * 5, 90);
      setProgress(pct, "Building packet... (" + attempts + "s)");
      fetch(BASE + "/api/v1/permits/build/" + jobId + "/status", {
        headers: {"X-Permit-API-Key": KEY}
      }).then(function(r){return r.json()}).then(function(d){
        if(d.status === "complete"){
          clearInterval(_poll);
          setProgress(100, "Complete! Your permit packet is ready.");
          var btn = document.getElementById("download-btn");
          btn.style.display = "block";
          btn.onclick = function(){ window.open(BASE + dlUrl + "?api_key=" + encodeURIComponent(KEY), "_blank"); };
          document.getElementById("build-btn").disabled = false;
        } else if(d.status === "error"){
          clearInterval(_poll);
          showError("Build failed: " + (d.error || "Unknown error"));
          document.getElementById("build-btn").disabled = false;
        }
        if(attempts > 120){ clearInterval(_poll); showError("Timed out waiting for build. Try again."); document.getElementById("build-btn").disabled = false; }
      }).catch(function(){ /* keep polling */ });
    }, 2000);
  }
})();
</script>
</body>
</html>"""


@bp.route("/contractor-profile/save", methods=["POST"])
def contractor_profile_save():
    # Coerce the hidden id defensively: a hand-crafted / stale POST can send a
    # non-numeric ``id`` ('abc', '1.5') that used to reach int() and 500 (ValueError).
    # Junk -> None -> treat as a new-profile insert instead of crashing.
    _pid_raw = request.form.get("id", "").strip()
    try:
        pid = int(_pid_raw) if _pid_raw else None
    except (TypeError, ValueError):
        pid = None
    data = {f: request.form.get(f, "").strip() for f in
            ["company_name", "license_number", "qualifier_name", "address", "city",
             "state", "zip", "phone", "email", "contact_person", "notary_county"]}
    data["is_default"] = 1 if request.form.get("is_default") else 0
    if data["is_default"]:
        db.execute("UPDATE contractor_profiles SET is_default=0")
    if pid:
        db.update("contractor_profiles", pid, **data)
        flash("Profile updated.", "ok")
    else:
        data["created"] = db.now()
        data["tenant_id"] = 1
        db.insert("contractor_profiles", data)
        flash("Profile saved.", "ok")
    return redirect(url_for("permits.contractor_profile"))
