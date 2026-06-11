#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Provision a tenant's secrets to its hosts in one command — the white-label
onboarding seed. Reads names+values from a local gitignored env file and pushes
them to Render (and optionally Vercel) via their env-var APIs.

SAFETY:
  * DRY-RUN by default — prints WHAT it would set (names only, never values).
    Pass --apply to actually push.
  * Reads host API tokens from the environment (RENDER_API_KEY, VERCEL_TOKEN) —
    never from chat, never hardcoded, never printed.
  * Never prints a secret VALUE. Only names + a masked length.
  * Setting prod env is an outward-facing action: --apply is the owner's one click.

Usage:
  python scripts/provision_secrets.py                 # dry-run from secrets/keys.local.env
  python scripts/provision_secrets.py --apply         # push to Render
  python scripts/provision_secrets.py --file path.env --service srv-xxxx --apply

Env file format (secrets/keys.local.env, gitignored):
  CRM_SECRET=...
  CRM_SYNC_SECRET=...
  MEASURE_CRM_WEBHOOK_SECRET=...
  SEABREEZE_CRM_WEBHOOK_SECRET=...
"""
import argparse, json, os, sys, urllib.request, urllib.error

DEFAULT_ENV = "secrets/keys.local.env"
DEFAULT_RENDER_SERVICE = os.environ.get("RENDER_SERVICE_ID", "srv-d8kq47jbc2fs73crtnug")  # collab-crm
RENDER_API = "https://api.render.com/v1"


def load_env(path):
    """Parse KEY=VALUE lines (ignores blanks/#comments). Returns dict."""
    if not os.path.exists(path):
        sys.exit("env file not found: %s (copy from a private source; it's gitignored)" % path)
    out = {}
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def mask(v):
    """Never reveal a secret — just its shape."""
    return "set (%d chars)" % len(v) if v else "EMPTY"


def render_put_env(service_id, token, kv, apply):
    """Render: PUT /v1/services/{id}/env-vars replaces the whole env-var set, so we
    GET current, merge our keys, and PUT back. Dry-run prints the plan."""
    hdr = {"Authorization": "Bearer " + token, "Accept": "application/json",
           "Content-Type": "application/json"}
    # GET current env vars
    req = urllib.request.Request(RENDER_API + "/services/%s/env-vars?limit=100" % service_id, headers=hdr)
    try:
        cur = json.loads(urllib.request.urlopen(req, timeout=30).read().decode())
    except urllib.error.HTTPError as e:
        sys.exit("Render GET env-vars failed: %s (check RENDER_API_KEY + service id)" % e.code)
    existing = {}
    for row in cur:
        ev = row.get("envVar", row)
        existing[ev.get("key")] = ev.get("value")
    merged = dict(existing)
    plan = []
    for k, v in kv.items():
        action = "update" if k in existing else "add"
        plan.append((k, action))
        merged[k] = v
    print("  Render service %s — plan:" % service_id)
    for k, action in plan:
        print("    %-32s %-6s  %s" % (k, action, mask(kv[k])))
    if not apply:
        print("  (dry-run — pass --apply to push)")
        return
    body = json.dumps([{"key": k, "value": v} for k, v in merged.items()]).encode()
    req = urllib.request.Request(RENDER_API + "/services/%s/env-vars" % service_id,
                                 data=body, headers=hdr, method="PUT")
    try:
        urllib.request.urlopen(req, timeout=30)
        print("  ✓ Render env-vars updated (a deploy is triggered).")
    except urllib.error.HTTPError as e:
        sys.exit("Render PUT failed: %s %s" % (e.code, e.read().decode()[:200]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=DEFAULT_ENV)
    ap.add_argument("--service", default=DEFAULT_RENDER_SERVICE)
    ap.add_argument("--apply", action="store_true", help="actually push (default: dry-run)")
    a = ap.parse_args()

    kv = load_env(a.file)
    if not kv:
        sys.exit("no keys parsed from %s" % a.file)
    print("Provisioning %d secret(s) from %s  [%s]" % (
        len(kv), a.file, "APPLY" if a.apply else "DRY-RUN"))
    for k in kv:
        print("  - %-32s %s" % (k, mask(kv[k])))

    token = os.environ.get("RENDER_API_KEY", "").strip()
    if not token:
        print("\n! RENDER_API_KEY not in env — cannot reach Render. Set it (never in chat) to push.")
        if a.apply:
            sys.exit(2)
        print("  (dry-run continues without it.)")
        return
    print("")
    render_put_env(a.service, token, kv, a.apply)
    # Vercel is being retired (DNS cutover to Render). Add a vercel_put_env() here only
    # if the tenant still runs on Vercel. Left out by design — one vendor is the goal.


if __name__ == "__main__":
    main()
