# -*- coding: utf-8 -*-
"""SeaBreeze Job Management - SQLite layer (schema + CRUD helpers)."""
import os, sqlite3
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, 'data', 'jobs.db')

# Job columns the form/edit routes accept (everything except id/created/updated/stage).
JOB_FIELDS = ['owner', 'phone', 'email', 'address', 'city', 'zip', 'pcn', 'legal',
              'county', 'ahj', 'system', 'existing', 'area', 'slope', 'mrh',
              'exposure', 'value', 'notes', 'next_action', 'next_due', 'packet']


def _now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = connect()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created TEXT, updated TEXT, stage TEXT,
        owner TEXT, phone TEXT, email TEXT,
        address TEXT, city TEXT, zip TEXT,
        pcn TEXT, legal TEXT,
        county TEXT DEFAULT 'Palm Beach County',
        ahj TEXT, system TEXT, existing TEXT,
        area TEXT, slope TEXT, mrh TEXT, exposure TEXT, value TEXT,
        notes TEXT, next_action TEXT, next_due TEXT, packet TEXT
    );
    CREATE TABLE IF NOT EXISTS activity (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER,
        created TEXT,
        kind TEXT,
        text TEXT,
        due TEXT,
        done INTEGER DEFAULT 0
    );
    """)
    conn.commit()
    conn.close()


# ---------- jobs ----------

def add_job(data):
    """Insert a job from a dict of JOB_FIELDS. Returns new job id."""
    conn = connect()
    now = _now()
    cols = ['created', 'updated', 'stage'] + JOB_FIELDS
    vals = [now, now, data.get('stage', 'Lead')] + [data.get(f, '') for f in JOB_FIELDS]
    if not data.get('county'):
        vals[cols.index('county')] = 'Palm Beach County'
    placeholders = ','.join('?' * len(cols))
    cur = conn.execute('INSERT INTO jobs (%s) VALUES (%s)' % (','.join(cols), placeholders), vals)
    conn.commit()
    jid = cur.lastrowid
    conn.close()
    return jid


def get_job(job_id):
    conn = connect()
    row = conn.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def all_jobs():
    conn = connect()
    rows = conn.execute('SELECT * FROM jobs ORDER BY id DESC').fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_job(job_id, **fields):
    """Update arbitrary job columns; always bumps updated timestamp."""
    fields = {k: v for k, v in fields.items() if k in JOB_FIELDS or k == 'stage'}
    if not fields:
        return
    fields['updated'] = _now()
    sets = ','.join('%s=?' % k for k in fields)
    conn = connect()
    conn.execute('UPDATE jobs SET %s WHERE id=?' % sets, list(fields.values()) + [job_id])
    conn.commit()
    conn.close()


def set_stage(job_id, stage):
    update_job(job_id, stage=stage)


# ---------- activity ----------

def add_activity(job_id, kind, text, due=None):
    conn = connect()
    cur = conn.execute(
        'INSERT INTO activity (job_id, created, kind, text, due, done) VALUES (?,?,?,?,?,0)',
        (job_id, _now(), kind, text, due))
    conn.commit()
    aid = cur.lastrowid
    conn.close()
    return aid


def job_activity(job_id):
    """All activity for a job, newest first."""
    conn = connect()
    rows = conn.execute('SELECT * FROM activity WHERE job_id=? ORDER BY id DESC', (job_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def complete_task(activity_id):
    conn = connect()
    conn.execute('UPDATE activity SET done=1 WHERE id=?', (activity_id,))
    conn.commit()
    conn.close()


def open_tasks(job_id=None):
    """Open (not done) tasks, optionally for one job. Newest first."""
    conn = connect()
    if job_id is None:
        rows = conn.execute(
            "SELECT * FROM activity WHERE kind='task' AND done=0 ORDER BY due IS NULL, due ASC").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM activity WHERE kind='task' AND done=0 AND job_id=? ORDER BY id DESC",
            (job_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]
