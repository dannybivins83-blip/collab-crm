# -*- coding: utf-8 -*-
"""One-click desktop launcher — run the CRM locally with ALL features.

What it does (so `python app.py` foot-guns can't bite):
  1. Loads secrets/keys.local.env into the environment (integration keys:
     AI takeoff, roof engine, Google/Drive, webhook secrets). Multi-line
     values (the Drive service-account JSON) are handled correctly.
  2. Forces DATABASE_URL="" — the desktop app ALWAYS runs on its local
     SQLite (data/crm.db), never on a leftover cloud DATABASE_URL from .env.
  3. Boots the app on http://127.0.0.1:5000 and opens the browser.

Real OS env vars still win (setdefault), except DATABASE_URL which is
deliberately overridden for local runs.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SECRETS = os.path.join(ROOT, "secrets", "keys.local.env")

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def load_secrets(path):
    """KEY=VALUE parser that tolerates multi-line values: a line that doesn't
    start a new KEY= is appended (with newline) to the previous key's value —
    this keeps the Drive service-account JSON/private key intact."""
    if not os.path.exists(path):
        print("  ! %s not found — integrations that need keys will be off" % path)
        return {}
    out, cur = {}, None
    for raw in open(path, encoding="utf-8-sig"):
        line = raw.rstrip("\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if _KEY_RE.match(line.strip()):
            k, _, v = line.strip().partition("=")
            cur = k.strip()
            out[cur] = v.strip().strip('"').strip("'")
        elif cur:
            out[cur] += "\n" + line  # continuation (multi-line JSON / PEM)
    return out


def main():
    kv = load_secrets(SECRETS)
    loaded = []
    for k, v in kv.items():
        if v and k not in os.environ:
            os.environ[k] = v
            loaded.append(k)
    # Local desktop = SQLite, always. (.env may carry a cloud DATABASE_URL.)
    os.environ["DATABASE_URL"] = ""
    os.environ.setdefault("CRM_PORT", "5000")

    print("SeaBreeze CRM — desktop mode (all features)")
    print("  secrets loaded: %d key(s)" % len(loaded))   # names/values never printed
    print("  database:       local SQLite (data/crm.db)")

    sys.path.insert(0, ROOT)
    os.chdir(ROOT)
    import config  # noqa: F401  (loads .env — our env wins where already set)
    # Feature availability at a glance:
    import importlib
    checks = []
    checks.append(("AI plans takeoff (ANTHROPIC_API_KEY)",
                   bool(os.environ.get("ANTHROPIC_API_KEY"))))
    checks.append(("Aerial roof reports (ROOF_ENGINE_URL+KEY)",
                   bool(os.environ.get("ROOF_ENGINE_URL") and os.environ.get("ROOF_ENGINE_API_KEY"))))
    try:
        permits = importlib.import_module("modules.permits")
        checks.append(("Permit packet builder (1.1GB library)",
                       permits.builder_meta().get("available", False)))
    except Exception:
        checks.append(("Permit packet builder (1.1GB library)", False))
    checks.append(("Google sign-in / Gmail widget",
                   bool(os.environ.get("GOOGLE_OAUTH_CLIENT_ID"))))
    checks.append(("Drive doc mirroring (GDRIVE_*)",
                   bool(os.environ.get("GDRIVE_SA_JSON") and os.environ.get("GDRIVE_FOLDER_ID"))))
    for label, ok in checks:
        print("  %s %s" % ("[on] " if ok else "[off]", label))

    import app as appmod
    port = int(os.environ.get("CRM_PORT", "5000"))
    print("\n  → http://127.0.0.1:%d\n" % port)
    if not os.environ.get("CRM_NOBROWSER"):
        try:
            import webbrowser
            import threading
            threading.Timer(1.5, lambda: webbrowser.open("http://127.0.0.1:%d" % port)).start()
        except Exception:
            pass
    appmod.app.run(host="127.0.0.1", port=port, debug=False)


if __name__ == "__main__":
    main()
