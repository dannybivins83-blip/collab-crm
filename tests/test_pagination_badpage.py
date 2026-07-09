# -*- coding: utf-8 -*-
"""Regression: the record-browsing routes must not 500 on normal or edge input.

Two distinct bugs are guarded here, both of which made browsing records return
HTTP 500:

1. templates/contacts.html began with ``{% extends "base.html" %}`` written with
   CURLY quotes (U+201C/U+201D) instead of ASCII. Jinja can't tokenize that, so
   EVERY load of the Contacts list page (``/contacts/``) 500'd — normal usage,
   not an edge case.

2. The leads / jobs / contacts list views parsed the page number with
   ``int(request.args.get("page") or 1)``, which raises ``ValueError`` (HTTP 500)
   on any non-integer input — e.g. a garbled link or crawler hitting
   ``/leads/list?page=abc``. Fixed to ``request.args.get("page", type=int)`` so an
   unparseable page falls back to 1 instead of crashing the route.

Runs in an isolated subprocess against a fresh temp SQLite DB (mirrors
test_permits_routes.py) so it neither depends on nor mutates the dev database.
"""
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHECK = r'''
import os, sys
sys.path.insert(0, REPO_PLACEHOLDER)
os.chdir(REPO_PLACEHOLDER)
import app as appmod
import db

a = appmod.app
a.config["WTF_CSRF_ENABLED"] = False
c = a.test_client()

uid = db.insert("users", {"name": "smoke", "email": "smoke@test.local",
                          "role": "admin"})
# A little data so the paginators actually run their COUNT + LIMIT/OFFSET path.
db.insert("contacts", {"first_name": "Pat", "last_name": "Example"})
db.insert("leads", {"name": "Lead One", "department": "REROOF Department"})
db.insert("jobs", {"name": "Job One", "department": "REROOF Department"})

with c.session_transaction() as s:
    s["user_id"] = uid
    s["user_name"] = "smoke"
    s["user_role"] = "admin"

# Each of these previously raised ValueError -> HTTP 500. They must now 200.
# Includes non-numeric, empty, negative, zero, float, and overflow-past-end pages.
checks = [
    "/leads/list?page=abc",
    "/leads/list?page=",
    "/leads/list?page=-4",
    "/leads/list?page=0",
    "/leads/list?page=1.5",
    "/leads/list?page=99999",
    "/jobs/list?page=abc",
    "/jobs/list?page=",
    "/jobs/list?page=-4",
    "/contacts/?page=abc",
    "/contacts/?page=",
    "/contacts/?page=-4",
]

bad = []
for path in checks:
    r = c.get(path, follow_redirects=True)
    if r.status_code != 200:
        bad.append((path, r.status_code))

# Normal (valid) loads must render too. The bare "/contacts/" guards bug #1
# (the curly-quote {% extends %} that broke the Contacts page for everyone).
for path in ("/contacts/", "/leads/list", "/jobs/list", "/leads/list?page=2"):
    if c.get(path, follow_redirects=True).status_code != 200:
        bad.append((path, "valid load regressed"))

if bad:
    for b in bad:
        print("FAIL", b)
    sys.exit(1)
print("ALL_OK", len(checks))
sys.exit(0)
'''


def test_list_views_survive_bad_page_param():
    tmpdb = os.path.join(tempfile.mkdtemp(prefix="badpage_smoke_"), "crm.db")
    env = dict(os.environ)
    env["DATABASE_URL"] = ""            # force SQLite
    env["CRM_DB_PATH"] = tmpdb          # throwaway DB
    env["CRM_NOBROWSER"] = "1"
    env["CRM_PORT"] = "5096"
    env.pop("RENDER", None)
    env.pop("CRM_ENV", None)

    code = _CHECK.replace("REPO_PLACEHOLDER", repr(REPO))
    r = subprocess.run([sys.executable, "-c", code], env=env,
                       capture_output=True, text=True, cwd=REPO, timeout=180)
    assert r.returncode == 0, (
        "bad-page route smoke failed:\nSTDOUT:\n%s\nSTDERR:\n%s"
        % (r.stdout, r.stderr))
    assert "ALL_OK" in r.stdout
