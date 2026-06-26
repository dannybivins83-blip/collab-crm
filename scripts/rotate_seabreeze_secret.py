#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""One-shot SEABREEZE_CRM_WEBHOOK_SECRET rotation.

Rotation chain (per provision_secrets.py comment + sitecam lane message):
  1. Generate fresh value.
  2. Update keys.local.env in-place.
  3. Push to CRM Render service.
  4. Push to sitecam-api Render service WITH SEED_FORCE=true in the SAME PUT
     (one redeploy — re-encrypts the tenant webhookSecretEnc row so SSO works).
  5. Wait ~5 seconds, then remove SEED_FORCE from sitecam-api (second redeploy).

Values never appear in output — masked to (n chars, ...last4) only.
Reads RENDER_API_KEY from Windows User-scope env (set-once via set_secret.ps1).

Usage (from whitelabel-crm/):
  python scripts/rotate_seabreeze_secret.py            # dry-run
  python scripts/rotate_seabreeze_secret.py --apply    # execute rotation
"""
import argparse, json, os, secrets, sys, time, urllib.request, urllib.error

RENDER_API = "https://api.render.com/v1"
CRM_SVC    = os.environ.get("RENDER_CRM_SERVICE_ID",     "srv-d8kq47jbc2fs73crtnug")
SC_SVC     = os.environ.get("RENDER_SITECAM_SERVICE_ID", "srv-d8j221btqb8s73bmgugg")
KEY_NAME   = "SEABREEZE_CRM_WEBHOOK_SECRET"
ENV_FILE   = os.path.join(os.path.dirname(__file__), "..", "secrets", "keys.local.env")

def mask(v):
    return "set (%d chars, ...%s)" % (len(v), v[-4:]) if v else "EMPTY"

def render_get_env(svc_id, token):
    req = urllib.request.Request(
        "%s/services/%s/env-vars?limit=100" % (RENDER_API, svc_id),
        headers={"Authorization": "Bearer " + token, "Accept": "application/json"})
    rows = json.loads(urllib.request.urlopen(req, timeout=30).read().decode())
    return {(r.get("envVar") or r).get("key"): (r.get("envVar") or r).get("value") for r in rows}

def render_put_env(svc_id, token, kv, apply, label):
    body = json.dumps([{"key": k, "value": v} for k, v in kv.items()]).encode()
    print("  [%s] PUT %d env-vars → service %s" % (label, len(kv), svc_id))
    for k, v in kv.items():
        print("    %-40s  %s" % (k, mask(v)))
    if not apply:
        print("    (dry-run — pass --apply to push)")
        return
    req = urllib.request.Request(
        "%s/services/%s/env-vars" % (RENDER_API, svc_id),
        data=body, method="PUT",
        headers={"Authorization": "Bearer " + token,
                 "Accept": "application/json",
                 "Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=30)
        print("    OK — Render deploy triggered")
    except urllib.error.HTTPError as e:
        print("    FAIL — %s %s" % (e.code, e.read().decode()[:200]))

def update_env_file(path, key, new_val):
    lines = open(path, encoding="utf-8-sig").readlines()
    updated = False
    out = []
    for ln in lines:
        stripped = ln.strip()
        if stripped.startswith(key + "="):
            out.append("%s=%s\n" % (key, new_val))
            updated = True
        else:
            out.append(ln)
    if not updated:
        out.append("%s=%s\n" % (key, new_val))
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(out)
    print("  keys.local.env updated — %s %s" % (key, mask(new_val)))

def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    token = os.environ.get("RENDER_API_KEY", "").strip()
    if not token:
        sys.exit("RENDER_API_KEY not set in env — run set_secret.ps1 -Name RENDER_API_KEY first")

    print("\n=== SEABREEZE_CRM_WEBHOOK_SECRET rotation [%s] ===" % ("APPLY" if a.apply else "DRY-RUN"))

    # 1. Generate
    new_val = secrets.token_hex(32)
    print("\n[1] Generated fresh value — %s" % mask(new_val))

    # 2. Update keys.local.env
    env_path = os.path.normpath(ENV_FILE)
    print("\n[2] Updating keys.local.env")
    if a.apply:
        update_env_file(env_path, KEY_NAME, new_val)
    else:
        print("  (dry-run — would write %s to %s)" % (mask(new_val), env_path))

    # 3. Push to CRM Render
    print("\n[3] CRM Render service")
    crm_env = render_get_env(CRM_SVC, token)
    crm_merged = dict(crm_env)
    crm_merged[KEY_NAME] = new_val
    render_put_env(CRM_SVC, token, crm_merged, a.apply, "Render-CRM")

    # 4. Push to sitecam-api WITH SEED_FORCE=true (same PUT → single redeploy)
    print("\n[4] sitecam-api — SEABREEZE secret + SEED_FORCE=true (one redeploy)")
    sc_env = render_get_env(SC_SVC, token)
    sc_merged = dict(sc_env)
    sc_merged[KEY_NAME] = new_val
    sc_merged["SEED_FORCE"] = "true"
    render_put_env(SC_SVC, token, sc_merged, a.apply, "Render-SiteCam+SEED_FORCE")

    # 5. Remove SEED_FORCE after a short pause (second redeploy)
    if a.apply:
        print("\n[5] Waiting 8s before removing SEED_FORCE ...")
        time.sleep(8)
        sc_merged2 = {k: v for k, v in sc_merged.items() if k != "SEED_FORCE"}
        render_put_env(SC_SVC, token, sc_merged2, True, "Render-SiteCam (remove SEED_FORCE)")
        print("\nRotation complete. Verify SSO with sitecam lane.")
    else:
        print("\n[5] (dry-run — would remove SEED_FORCE in a second PUT)")
        print("\nDry-run complete. Re-run with --apply to execute.")

if __name__ == "__main__":
    main()
