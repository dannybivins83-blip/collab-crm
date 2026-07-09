#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Push secrets to ALL platforms in one command — Danny's one-and-done.

Reads names+values from secrets/keys.local.env (gitignored) and pushes to:
  --platform render    Render CRM service   (uses RENDER_API_KEY)
  --platform sitecam   Render SiteCam svc   (uses RENDER_API_KEY)
  --platform vercel    Vercel project       (uses VERCEL_TOKEN)
  --platform vm        Roof engine VM       (uses SSH_KEY_PATH or ssh-agent)
  --platform all       All of the above     (default)

SAFETY:
  * DRY-RUN by default — prints WHAT it would set (names only, never values).
    Pass --apply to actually push.
  * API tokens read from environment (RENDER_API_KEY, VERCEL_TOKEN) — never chat.
  * Never prints a secret VALUE. Only names + a masked length hint.
  * --apply is the owner's one click — the only manual step required.

Usage:
  cd whitelabel-crm
  python scripts/provision_secrets.py                         # dry-run all
  python scripts/provision_secrets.py --apply                 # push all platforms
  python scripts/provision_secrets.py --platform render --apply
  python scripts/provision_secrets.py --platform vm --apply
  python scripts/provision_secrets.py --file path.env --apply
"""
import argparse, json, os, subprocess, sys, urllib.request, urllib.error

# ── Platform config ────────────────────────────────────────────────────────────
DEFAULT_ENV = "secrets/keys.local.env"
RENDER_API  = "https://api.render.com/v1"
VERCEL_API  = "https://api.vercel.com"

# Render service IDs (read from env to allow override; hardcoded fallback)
RENDER_CRM_SERVICE_ID     = os.environ.get("RENDER_CRM_SERVICE_ID",     "srv-d8kq47jbc2fs73crtnug")
RENDER_SITECAM_SERVICE_ID = os.environ.get("RENDER_SITECAM_SERVICE_ID", "srv-d8j221btqb8s73bmgugg")  # sitecam-api (same workspace as CRM)
VERCEL_PROJECT_ID         = os.environ.get("VERCEL_PROJECT_ID",         "prj_D0ERrWZCSpGRMfqdiuu0z8jUjDsl")

# VM SSH settings
VM_HOST     = os.environ.get("VM_HOST",     "150.136.152.240")
VM_USER     = os.environ.get("VM_USER",     "ubuntu")
VM_SSH_KEY  = os.environ.get("SSH_KEY_PATH", "")           # e.g. ~/.ssh/roof_engine_key.pem
VM_SERVICE  = os.environ.get("VM_SERVICE",  "roof-engine") # systemctl service name

# Keys that belong on each platform (None = all non-skipped keys)
PLATFORM_KEYS = {
    "render":  None,   # push everything
    "sitecam": {       # only these go to sitecam-api
        "SEABREEZE_CRM_WEBHOOK_SECRET",
        "SITECAM_API_KEY",
        "SITECAM_API_KEY_WWS",
        "SITECAM_API_KEY_SEABREEZE",
    },
    "vercel":  None,   # push everything (transitional — Vercel still serves live domain)
    "vm": {            # only these go to the roof engine VM
        "MEASURE_CRM_WEBHOOK_SECRET",
        "ROOF_ENGINE_API_KEY",
    },
}

# Per-platform env-var RENAMES applied on the way OUT to a host. The CRM stores the
# engine's API key under ROOF_ENGINE_API_KEY, but the engine (service/app.py on the VM)
# reads valid keys from ROOF_API_KEYS (comma-separated). Without this rename a
# `--platform vm --apply` writes ROOF_ENGINE_API_KEY into /etc/roof-engine.env, the engine
# sees no ROOF_API_KEYS and silently falls back to the insecure `dev-key`, and the CRM 401s.
# Root-caused live by crm-ui 2026-06-25 (bus: ROOTCAUSE-fix-provisioning-var-name); this is
# the permanent provisioning fix so it never recurs on a VM redeploy.
PLATFORM_RENAMES = {
    "vm": {"ROOF_ENGINE_API_KEY": "ROOF_API_KEYS"},
}

# Keys that should NEVER be pushed to any remote host
LOCAL_ONLY = {
    "RENDER_API_KEY",
    "SITECAM_RENDER_API_KEY",
    "VERCEL_TOKEN",
    "DB_RESTORE_TOKEN",  # one-shot; delete after use
}

# Keys to DELETE from Render (one-shot tokens that must not persist)
DELETE_FROM_RENDER = {"DB_RESTORE_TOKEN"}

# ── Helpers ────────────────────────────────────────────────────────────────────
def load_env(path):
    if not os.path.exists(path):
        sys.exit("env file not found: %s" % path)
    out = {}
    # utf-8-sig strips a leading BOM if the file was saved by PowerShell Set-Content -Encoding utf8,
    # otherwise a BOM corrupts the first key name (﻿RENDER_API_KEY).
    for line in open(path, encoding="utf-8-sig"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out

def mask(v):
    if not v:
        return "EMPTY"
    return "set (%d chars, ...%s)" % (len(v), v[-4:])

def filter_keys(kv, platform):
    allowed = PLATFORM_KEYS.get(platform)
    rename = PLATFORM_RENAMES.get(platform, {})
    result = {}
    for k, v in kv.items():
        if k in LOCAL_ONLY:
            continue
        if not v:
            continue
        if allowed is not None and k not in allowed:
            continue
        # Rename the var to the name the target host actually reads (e.g. the engine VM
        # reads ROOF_API_KEYS, not the CRM-side ROOF_ENGINE_API_KEY). See PLATFORM_RENAMES.
        result[rename.get(k, k)] = v
    return result

# ── Render ─────────────────────────────────────────────────────────────────────
def render_push(service_id, token, kv, delete_keys, apply, label="Render"):
    if not service_id:
        print("  [%s] SKIP — no service ID configured (set RENDER_%s_SERVICE_ID)" % (label, label.upper()))
        return
    hdr = {"Authorization": "Bearer " + token, "Accept": "application/json",
           "Content-Type": "application/json"}
    req = urllib.request.Request(
        RENDER_API + "/services/%s/env-vars?limit=100" % service_id, headers=hdr)
    try:
        cur = json.loads(urllib.request.urlopen(req, timeout=30).read().decode())
    except urllib.error.HTTPError as e:
        print("  [%s] GET env-vars failed: %s" % (label, e.code))
        return
    existing = {(row.get("envVar") or row).get("key"): (row.get("envVar") or row).get("value")
                for row in cur}
    merged = {k: v for k, v in existing.items() if k not in delete_keys}
    plan = []        # real changes only (add / changed) — these drive the deploy
    unchanged = []   # value already current on the host — skipped, no deploy impact
    for k, v in kv.items():
        if k in existing and existing[k] == v:
            unchanged.append(k)
            continue
        plan.append((k, "CHANGE" if k in existing else "add"))
        merged[k] = v
    deletes = [k for k in delete_keys if k in existing]

    print("  [%s] service %s" % (label, service_id))
    for k, action in plan:
        print("    %-36s %-6s  %s" % (k, action, mask(kv[k])))
    for k in deletes:
        print("    %-36s DELETE" % k)
    if unchanged:
        print("    (%d already current, skipped: %s)" % (len(unchanged), ", ".join(sorted(unchanged))))
    if not plan and not deletes:
        print("    Nothing to push — host already matches local. No deploy triggered.")
        return
    if not apply:
        print("    (dry-run — pass --apply to push %d change(s))" % (len(plan) + len(deletes)))
        return
    body = json.dumps([{"key": k, "value": v} for k, v in merged.items()]).encode()
    req2 = urllib.request.Request(
        RENDER_API + "/services/%s/env-vars" % service_id,
        data=body, headers=hdr, method="PUT")
    try:
        urllib.request.urlopen(req2, timeout=30)
        print("    OK — %d updated, %d deleted (Render deploy triggered)" % (len(plan), len(deletes)))
    except urllib.error.HTTPError as e:
        print("    FAIL — %s %s" % (e.code, e.read().decode()[:200]))

# ── Vercel ─────────────────────────────────────────────────────────────────────
def vercel_push(project_id, token, kv, apply):
    if not project_id:
        print("  [Vercel] SKIP — set VERCEL_PROJECT_ID env var")
        return
    hdr = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
    # GET existing env vars
    req = urllib.request.Request(
        "%s/v9/projects/%s/env" % (VERCEL_API, project_id), headers=hdr)
    try:
        data = json.loads(urllib.request.urlopen(req, timeout=30).read().decode())
        existing = {e["key"]: e["id"] for e in data.get("envs", [])}
    except urllib.error.HTTPError as e:
        print("  [Vercel] GET env failed: %s" % e.code)
        return

    print("  [Vercel] project %s" % project_id)
    for k, v in kv.items():
        action = "update" if k in existing else "add"
        print("    %-36s %-6s  %s" % (k, action, mask(v)))
    if not apply:
        print("    (dry-run — pass --apply to push)")
        return

    for k, v in kv.items():
        payload = json.dumps({
            "key": k, "value": v, "type": "encrypted",
            "target": ["production", "preview"]
        }).encode()
        if k in existing:
            # PATCH existing
            req2 = urllib.request.Request(
                "%s/v9/projects/%s/env/%s" % (VERCEL_API, project_id, existing[k]),
                data=payload, headers=hdr, method="PATCH")
        else:
            req2 = urllib.request.Request(
                "%s/v10/projects/%s/env" % (VERCEL_API, project_id),
                data=payload, headers=hdr, method="POST")
        try:
            urllib.request.urlopen(req2, timeout=30)
            print("    OK  %s" % k)
        except urllib.error.HTTPError as e:
            print("    FAIL %s — %s" % (k, e.code))

# ── VM SSH ─────────────────────────────────────────────────────────────────────
def vm_push(kv, apply):
    print("  [VM] %s@%s  service: %s" % (VM_USER, VM_HOST, VM_SERVICE))
    for k, v in kv.items():
        print("    %-36s  %s" % (k, mask(v)))
    if not apply:
        print("    (dry-run — pass --apply to push)")
        return

    # Build export commands + service restart
    exports = " && ".join('export %s="%s"' % (k, v.replace('"', '\\"'))
                          for k, v in kv.items())
    # Write to /etc/roof-engine.env (persists across reboots)
    env_lines = "\n".join("%s=%s" % (k, v) for k, v in kv.items())
    remote_cmd = (
        'echo "%s" | sudo tee /etc/roof-engine.env > /dev/null && '
        'sudo systemctl restart %s && '
        'sudo systemctl is-active %s'
    ) % (env_lines.replace('"', '\\"'), VM_SERVICE, VM_SERVICE)

    ssh_cmd = ["ssh", "-o", "StrictHostKeyChecking=no"]
    if VM_SSH_KEY:
        ssh_cmd += ["-i", VM_SSH_KEY]
    ssh_cmd += ["%s@%s" % (VM_USER, VM_HOST), remote_cmd]

    try:
        result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
            print("    OK — VM env updated, service restarted (%s)" % result.stdout.strip())
        else:
            print("    FAIL — %s" % result.stderr.strip()[:300])
    except subprocess.TimeoutExpired:
        print("    FAIL — SSH timed out")
    except FileNotFoundError:
        print("    FAIL — ssh not found in PATH")

# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows console is cp1252; avoid charmap crashes
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Push secrets to all platforms in one command.")
    ap.add_argument("--file",     default=DEFAULT_ENV,  help="env file to read (default: secrets/keys.local.env)")
    ap.add_argument("--platform", default="all",
                    help="render, sitecam, vercel, vm, or all (default: all)")
    ap.add_argument("--apply",    action="store_true",  help="actually push (default: dry-run)")
    ap.add_argument("--service",  default="",           help="override Render CRM service ID")
    a = ap.parse_args()

    platforms = ["render", "sitecam", "vercel", "vm"] if a.platform == "all" else [p.strip() for p in a.platform.split(",")]

    kv_all = load_env(a.file)
    if not kv_all:
        sys.exit("No keys parsed from %s" % a.file)

    # Durable store = Windows User env vars (set-once via set_secret.ps1; survives reboots).
    # For any expected secret that's blank/missing in the env FILE, fall back to the process
    # environment, which on Windows includes User-scope vars. The file wins when non-blank.
    expected = set(kv_all) | LOCAL_ONLY
    for _names in PLATFORM_KEYS.values():
        if _names:
            expected |= _names
    overlaid = []
    for _name in expected:
        if not kv_all.get(_name):
            _envval = os.environ.get(_name, "").strip()
            if _envval:
                kv_all[_name] = _envval
                overlaid.append(_name)
    if overlaid:
        print("  (sourced %d secret(s) from User env vars: %s)" % (len(overlaid), ", ".join(sorted(overlaid))))

    mode = "APPLY" if a.apply else "DRY-RUN"
    print("\n=== provision_secrets.py [%s] ===" % mode)
    print("Source: %s  (%d keys)" % (a.file, len(kv_all)))
    print("Platforms: %s\n" % ", ".join(platforms))

    render_token  = os.environ.get("RENDER_API_KEY", "").strip()
    vercel_token  = os.environ.get("VERCEL_TOKEN",   "").strip()
    crm_service   = a.service or RENDER_CRM_SERVICE_ID

    for platform in platforms:
        kv = filter_keys(kv_all, platform)
        if not kv:
            print("[%s] No applicable keys — skipping." % platform)
            continue

        if platform == "render":
            if not render_token:
                print("[render] SKIP — RENDER_API_KEY not in env")
                continue
            render_push(crm_service, render_token, kv, DELETE_FROM_RENDER, a.apply, "Render-CRM")

        elif platform == "sitecam":
            # SiteCam runs in the SAME Render workspace as the CRM — one API key sees all three
            # services (collab-crm, sitecam-api, sitecam-web). Verified 2026-06-13 via /v1/services.
            if not render_token:
                print("[sitecam] SKIP — RENDER_API_KEY not in env")
                continue
            # COUPLING: rotating SEABREEZE_CRM_WEBHOOK_SECRET on sitecam-api REQUIRES a one-shot
            # SEED_FORCE=true on the SAME redeploy, or SSO silently breaks. This script does NOT
            # manage SEED_FORCE — coordinate the rotation with the sitecam lane. See memory
            # webhook-secret-rotation-pairs.
            if "SEABREEZE_CRM_WEBHOOK_SECRET" in kv and not a.apply:
                print("  [Render-SiteCam] WARNING: SEABREEZE rotation must pair with one-shot "
                      "SEED_FORCE=true on the same redeploy or SSO breaks. Coordinate w/ sitecam lane.")
            render_push(RENDER_SITECAM_SERVICE_ID, render_token, kv, set(), a.apply, "Render-SiteCam")

        elif platform == "vercel":
            if not vercel_token:
                print("[vercel] SKIP — VERCEL_TOKEN not in env")
                continue
            vercel_push(VERCEL_PROJECT_ID, vercel_token, kv, a.apply)

        elif platform == "vm":
            vm_push(kv, a.apply)

        else:
            print("[%s] Unknown platform — skipping." % platform)

    print("\nDone. %s" % ("All changes applied." if a.apply else "Re-run with --apply to push."))


if __name__ == "__main__":
    main()
