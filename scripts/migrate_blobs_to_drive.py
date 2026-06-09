# -*- coding: utf-8 -*-
"""Move file bytes OUT of the Neon `file_blobs` store INTO Google Drive, to reclaim
Postgres space (Neon's 512 MB project cap was hit). For each blob: upload its bytes
to the shared Drive folder, stamp the matching document/photo/library row's
`drive_id`, then DELETE the blob — but ONLY after a confirmed Drive upload, so no
file is ever lost. Afterward gdrive.serve_fallback() serves the files from Drive
(it already falls back to a live Drive name-search), so downloads keep working.

DRY RUN works with no credentials (just reports counts/size). APPLY needs the same
Drive service account the live site uses.

Usage:
  python scripts/migrate_blobs_to_drive.py                 # DRY RUN (report only)
  set GDRIVE_SA_JSON=... & set GDRIVE_FOLDER_ID=... & python scripts/migrate_blobs_to_drive.py --apply
  ... --apply --limit=100   # do a first batch of 100 to verify, then the rest
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
APPLY = "--apply" in sys.argv
LIMIT = None
for a in sys.argv:
    if a.startswith("--limit="):
        LIMIT = int(a.split("=", 1)[1])

if not os.environ.get("DATABASE_URL"):
    envf = os.path.join(HERE, ".env.production")
    if os.path.exists(envf):
        for line in open(envf, encoding="utf-8-sig"):
            line = line.strip()
            if line.startswith("DATABASE_URL="):
                os.environ["DATABASE_URL"] = re.sub(r"\s", "", line.split("=", 1)[1].strip().strip('"').strip("'"))
                break
os.environ["CRM_NOBROWSER"] = "1"
import db
from modules import gdrive

conn = db.connect()


def _v(r, i):
    try:
        return list(dict(r).values())[i]
    except Exception:
        return r[i]


rows = conn.execute("SELECT filename, mime, octet_length(data) AS sz FROM file_blobs ORDER BY octet_length(data) DESC").fetchall()
total = sum((_v(r, 2) or 0) for r in rows)
print("Mode: %s | %d blobs, %.1f MB in Postgres" % ("APPLY" if APPLY else "DRY RUN", len(rows), total / 1e6))

if not APPLY:
    print("Dry run — would upload each blob to Google Drive and delete it from Postgres,")
    print("freeing ~%.0f MB. Re-run with --apply (and GDRIVE_SA_JSON + GDRIVE_FOLDER_ID set)." % (total / 1e6))
    sys.exit(0)

if not gdrive.enabled():
    sys.exit("APPLY needs Drive: set GDRIVE_SA_JSON + GDRIVE_FOLDER_ID (the same the live site uses) and re-run.")

FILE_TABLES = ("documents", "photos", "library_docs")
moved = freed = failed = 0
for i, r in enumerate(rows):
    if LIMIT is not None and i >= LIMIT:
        break
    fn, sz = _v(r, 0), (_v(r, 2) or 0)
    got = gdrive.blob_get(fn)
    if not got:
        failed += 1
        continue
    data, mime = got
    did = gdrive.upload(fn, data, mime)
    if not did:                      # upload failed -> KEEP the blob (never lose a file)
        failed += 1
        print("  upload FAILED, kept blob:", (fn or "")[:50])
        continue
    for t in FILE_TABLES:            # stamp drive_id on the matching record(s)
        try:
            for row in db.all_rows(t, where="filename=?", params=(fn,)):
                db.update(t, row["id"], drive_id=did)
        except Exception:
            pass
    try:                             # delete only after confirmed Drive upload
        db.execute("DELETE FROM file_blobs WHERE filename=?", (fn,))
        moved += 1
        freed += sz
    except Exception as e:
        print("  delete error:", str(e)[:60])
    if moved % 50 == 0 and moved:
        print("  ... %d moved, %.0f MB freed" % (moved, freed / 1e6))

print("Done. moved=%d  freed=%.1f MB  failed=%d" % (moved, freed / 1e6, failed))
print("NOTE: run VACUUM (Neon auto-vacuums, or `VACUUM file_blobs;`) so Postgres reclaims the freed space.")
