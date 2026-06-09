# -*- coding: utf-8 -*-
"""Refresh the built-in estimate templates in the DB from constants.py.

Run AFTER intentionally editing constants.ESTIMATE_TEMPLATES so the live estimate
builder picks up the new line items / costs (the DB rows otherwise shadow the
code). Touches only is_builtin=1 rows; custom templates are left alone.

    python refresh_templates.py          # against whatever DATABASE_URL points to
    # force local SQLite instead:  set DATABASE_URL= && python refresh_templates.py
"""
import db

if __name__ == "__main__":
    print("Target:", "Postgres (live)" if db.IS_PG else "SQLite (local)")
    updated, inserted = db.sync_builtin_templates()
    print("Built-in templates refreshed: %d updated, %d inserted." % (updated, inserted))
