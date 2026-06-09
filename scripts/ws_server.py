# -*- coding: utf-8 -*-
"""Local worksheet-import bridge — runs on this machine, writes to the PROD Neon DB.

The browser (logged into AccuLynx) fetches each job's financial worksheet from
AccuLynx's internal API and POSTs the flattened rows here; we upsert them into the
prod worksheets / worksheet_lines tables. This bypasses the stuck Vercel deploy of
/sync/worksheet-import while using the exact same data model.

Endpoints (all CORS-open, incl. Private-Network preflight so an https page may call
http://localhost):
  GET  /ping        -> {"ok":true, jobs:<n>}
  GET  /seed.json   -> [[job_id, guid], ...]   (authoritative GUID list from prod)
  POST /ws          -> {rows:[{guid, contract_value, cost_total, lines:[{description,cost}]}]}
                       upserts; returns {imported, matched, skipped, samples}

Run:  python scripts/ws_server.py     (binds 127.0.0.1:5057)
"""
import os
import re
import sys
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

# Load prod DATABASE_URL before importing db.
for line in open(os.path.join(HERE, ".env.production"), encoding="utf-8-sig"):
    line = line.strip()
    if line.startswith("DATABASE_URL="):
        u = line.split("=", 1)[1].strip().strip('"').strip("'")
        os.environ["DATABASE_URL"] = re.sub(r"\s", "", u.replace("\\r", "").replace("\\n", "")).replace("﻿", "")
        break
os.environ["CRM_NOBROWSER"] = "1"
import db  # noqa: E402

PORT = 5057


def guid_of(u):
    m = re.search(r"/jobs/([0-9a-f-]{30,})", u or "")
    return m.group(1).lower() if m else None


def build_guid_map():
    """guid -> job_id from prod jobs.external_url."""
    m = {}
    for j in db.all_rows("jobs"):
        g = guid_of(j.get("external_url"))
        if g:
            m[g] = j["id"]
    return m


def ws_cat(desc):
    d = (desc or "").lower()
    if any(k in d for k in ("labor", "install", "tear", "removal", "dry in", "dry-in", "crew", "subcontract")):
        return "Labor"
    if "permit" in d or "inspection" in d or "notice of commencement" in d or "noc" in d:
        return "Permit"
    if any(k in d for k in ("overhead", "commission", "office", "admin", "dumpster", "fuel", "warranty")):
        return "Overhead"
    if any(k in d for k in ("material", "tile", "shingle", "metal", "underlayment", "felt", "drip",
                            "flashing", "nail", "screw", "vent", "ridge", "valley", "paint", "stucco",
                            "fascia", "sheathing", "plywood", "coating")):
        return "Material"
    return "Other"


def import_rows(rows, guid_map):
    """Bulk-upsert all rows in ONE Neon connection (fast). Returns counts."""
    matched = skipped = 0
    samples = []
    conn = db.connect()
    raw = conn._raw
    cur = raw.cursor()
    ts = db.now()
    for rec in rows:
        g = (rec.get("guid") or "").strip().lower()
        job_id = guid_map.get(g)
        if not job_id:
            skipped += 1
            continue
        cv = rec.get("contract_value")
        lines = rec.get("lines") or []
        cur.execute("SELECT id FROM worksheets WHERE job_id=%s LIMIT 1", (job_id,))
        row = cur.fetchone()
        if row:
            ws_id = row[0]
            cur.execute("UPDATE worksheets SET contract_value=%s, updated=%s WHERE id=%s",
                        (cv, ts, ws_id))
        else:
            cur.execute("INSERT INTO worksheets (created,updated,job_id,contract_value,notes) "
                        "VALUES (%s,%s,%s,%s,%s) RETURNING id",
                        (ts, ts, job_id, cv, "Imported from AccuLynx financial worksheet"))
            ws_id = cur.fetchone()[0]
        cur.execute("DELETE FROM worksheet_lines WHERE worksheet_id=%s", (ws_id,))
        if lines:
            vals = []
            for i, ln in enumerate(lines):
                cost = ln.get("cost") or 0
                desc = (ln.get("description") or "").strip()
                cat = ln.get("category") or ws_cat(desc)  # exact category from AccuLynx if provided
                vals.append((ws_id, i, cat, desc, cost, cost))
            cur.executemany(
                "INSERT INTO worksheet_lines (worksheet_id,sort,category,description,budget_cost,actual_cost) "
                "VALUES (%s,%s,%s,%s,%s,%s)", vals)
        matched += 1
        if len(samples) < 5:
            samples.append({"guid": g, "job_id": job_id, "cv": cv, "nlines": len(lines)})
    raw.commit()
    cur.close()
    conn.close()
    return matched, skipped, samples


GUID_MAP = build_guid_map()
SEED_PATH = os.path.join(HERE, "scripts", "_ws_guids.json")


class H(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "content-type")
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if self.path.startswith("/ping"):
            return self._json({"ok": True, "jobs": len(GUID_MAP)})
        if self.path.startswith("/seed.json"):
            pairs = json.load(open(SEED_PATH))
            return self._json([[p["id"], p["guid"]] for p in pairs])
        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        if not self.path.startswith("/ws"):
            return self._json({"error": "not found"}, 404)
        n = int(self.headers.get("Content-Length") or 0)
        data = json.loads(self.rfile.read(n) or b"{}")
        rows = data.get("rows") or []
        try:
            matched, skipped, samples = import_rows(rows, GUID_MAP)
        except Exception as e:
            return self._json({"error": str(e)[:300]}, 500)
        return self._json({"imported": matched, "matched": matched, "skipped": skipped, "samples": samples})

    def log_message(self, *a):
        pass  # quiet


if __name__ == "__main__":
    print("guid_map:", len(GUID_MAP), "jobs | serving on http://127.0.0.1:%d" % PORT, flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
