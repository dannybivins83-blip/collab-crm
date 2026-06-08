# -*- coding: utf-8 -*-
"""Runtime paths + app config for the white-label CRM."""
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def _load_dotenv():
    """Load a local .env (gitignored) so `python app.py` can pick up DATABASE_URL etc.
    without exporting it each time. Real OS env vars always win (setdefault)."""
    path = os.path.join(HERE, ".env")
    if not os.path.exists(path):
        return
    try:
        for line in open(path, encoding="utf-8-sig"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except Exception:
        pass


_load_dotenv()

# Data + uploads default to the app folder, but a host (Render/Railway/etc.) can
# point them at a mounted persistent disk via env so SQLite + files survive deploys.
DATA_DIR = os.environ.get("CRM_DATA_DIR") or os.path.join(HERE, "data")
UPLOAD_DIR = os.environ.get("CRM_UPLOAD_DIR") or os.path.join(HERE, "uploads")
DB_PATH = os.environ.get("CRM_DB_PATH") or os.path.join(DATA_DIR, "crm.db")

# Sub-folders under uploads/ for each file kind.
PHOTO_DIR = os.path.join(UPLOAD_DIR, "photos")
DOC_DIR = os.path.join(UPLOAD_DIR, "documents")
LOGO_DIR = os.path.join(UPLOAD_DIR, "branding")
ESTIMATE_PDF_DIR = os.path.join(UPLOAD_DIR, "estimates")
PERMIT_DIR = os.path.join(UPLOAD_DIR, "permits")
MEAS_DIR = os.path.join(UPLOAD_DIR, "measurements")

SECRET_KEY = os.environ.get("CRM_SECRET", "white-label-crm-dev-secret")
PORT = int(os.environ.get("CRM_PORT", "5050"))

for _d in (DATA_DIR, UPLOAD_DIR, PHOTO_DIR, DOC_DIR, LOGO_DIR, ESTIMATE_PDF_DIR, PERMIT_DIR, MEAS_DIR):
    os.makedirs(_d, exist_ok=True)
