"""Arm Resend as the CRM owner-alert transport.

Everything happens inside this one process. Secret values are read into memory,
used, and never printed, never written to a file this script leaves behind, and
never returned to the agent's context. The only output is status lines.

Order matters: a NEW dedicated key is minted for the CRM and PROVEN with a real
send BEFORE anything is written to Render. A key that cannot send never reaches
production.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FSE_REPO = r"C:\Users\kjburnz\florida-solar-exit"
SERVICE = "srv-d8kq47jbc2fs73crtnug"
VERIFY_TO = "dannybivins83@gmail.com"
RENDER_KEY = os.environ.get("RENDER_API_KEY", "").strip()


def die(msg):
    print("STOP: %s" % msg)
    sys.exit(1)


def api(url, method="GET", token=None, body=None, hdr=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer %s" % token
    headers.update(hdr or {})
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode("utf-8")
            return r.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        raw = ""
        try:
            raw = e.read().decode("utf-8")
        except Exception:
            pass
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw[:300]}


# ---- 1/6 read the existing Resend credential out of the FSE Vercel project ----
print("--- 1/6 reading the FSE Resend credential (never printed) ---")
tmpdir = tempfile.mkdtemp()
envfile = os.path.join(tmpdir, "e")
p = subprocess.run(
    ["npx", "--yes", "vercel", "env", "pull", envfile,
     "--environment=production", "--yes"],
    cwd=FSE_REPO, capture_output=True, text=True, shell=True, timeout=300)
if not os.path.exists(envfile):
    die("could not pull the FSE env (%s)" % (p.stderr or p.stdout)[-200:])
env = {}
with open(envfile, encoding="utf-8") as fh:
    for line in fh:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"')
os.remove(envfile)
os.rmdir(tmpdir)

src_key = env.get("RESEND_API_KEY", "").strip()
from_addr = env.get("RESEND_FROM_EMAIL", "").strip()
if not src_key.startswith("re_"):
    die("no usable Resend key in the FSE production env")
if "@" not in from_addr:
    die("no RESEND_FROM_EMAIL in the FSE production env")
domain = from_addr.split("@")[-1]
print("  source key: found (%d chars)" % len(src_key))
print("  verified sending domain: %s" % domain)

# ---- 2/6 mint a dedicated key so rotating FSE's cannot break the CRM ----
print("--- 2/6 minting a dedicated 'collab-crm' key ---")
code, body = api("https://api.resend.com/api-keys", "POST", src_key,
                 {"name": "collab-crm", "permission": "sending_access"})
if code not in (200, 201) or not body.get("token"):
    print("  could not mint a separate key (%s %s)" % (code, body))
    print("  falling back to the existing shared key")
    crm_key, dedicated = src_key, False
else:
    crm_key, dedicated = body["token"], True
    print("  minted: id %s" % body.get("id"))

# ---- 3/6 PROVE it before production hears about it ----
print("--- 3/6 proving the key with one real send BEFORE deploying ---")
code, body = api("https://api.resend.com/emails", "POST", crm_key, {
    "from": "MyRoofPortal <%s>" % from_addr,
    "to": [VERIFY_TO],
    "subject": "CRM transport check - Resend",
    "text": ("Sent before the key was deployed. If you are reading this, Resend "
             "can send from this domain and CRM lead alerts will work."),
})
if code != 200 or not body.get("id"):
    die("Resend rejected the send (%s %s) - nothing was changed" % (code, body))
print("  RESEND SEND OK, id %s -> %s" % (body["id"], VERIFY_TO))

# ---- 4/6 write both vars to Render ----
if not RENDER_KEY:
    # Prompt rather than fail: the key is in the Git Bash profile but not always
    # in a PowerShell session. getpass keeps it off the screen and out of history.
    import getpass
    RENDER_KEY = getpass.getpass("Render API key (not echoed): ").strip()
if not RENDER_KEY:
    die("no Render API key")
print("--- 4/6 setting RESEND_API_KEY + RESEND_FROM_EMAIL on Render ---")
for k, v in (("RESEND_API_KEY", crm_key), ("RESEND_FROM_EMAIL", from_addr)):
    code, body = api(
        "https://api.render.com/v1/services/%s/env-vars/%s" % (SERVICE, k),
        "PUT", RENDER_KEY, {"value": v})
    if code not in (200, 201):
        die("Render rejected %s (%s %s)" % (k, code, body))
    print("  set %s" % k)

# ---- 5/6 wait out the env-var redeploy ----
print("--- 5/6 waiting for the redeploy ---")
deadline = time.time() + 600
status = "?"
while time.time() < deadline:
    time.sleep(15)
    code, body = api(
        "https://api.render.com/v1/services/%s/deploys?limit=1" % SERVICE,
        token=RENDER_KEY)
    d = body[0].get("deploy", body[0]) if isinstance(body, list) and body else {}
    status = d.get("status", "?")
    print("  deploy: %s" % status)
    if status in ("live", "build_failed", "update_failed", "canceled"):
        break
if status != "live":
    die("deploy ended as '%s'" % status)

# ---- 6/6 make the LIVE server send one, and report its verdict ----
print("--- 6/6 asking the live server to send through the new transport ---")
code, body = api(
    "https://api.render.com/v1/services/%s/env-vars?limit=100" % SERVICE,
    token=RENDER_KEY)
tok = ""
for it in body:
    e = it.get("envVar", it)
    if e.get("key") == "DB_RESTORE_TOKEN":
        tok = e.get("value") or ""
code, st = api("https://crm.collaborativeconceptsfl.com/admin/mail-status?send=1",
               hdr={"X-Restore-Token": tok})
print("  resend_armed: %s | smtp_armed: %s" % (st.get("resend_armed"), st.get("smtp_armed")))
print("  alerts go to: %s" % st.get("notify_to"))
print("  test_sent:    %s" % st.get("test_sent"))
if st.get("last_error"):
    print("  last_error:   %s" % st["last_error"])
print()
if st.get("test_sent"):
    print("DONE - owner lead alerts are LIVE.%s" %
          ("" if dedicated else " (using FSE's shared key - see note)"))
    print("Two e-mails should be in %s." % VERIFY_TO)
else:
    print("FAILED - the live server still cannot send. Read last_error above.")
    sys.exit(1)
