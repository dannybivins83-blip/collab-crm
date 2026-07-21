# -*- coding: utf-8 -*-
"""SeaBreeze Job Management - pipeline stages + automation engine."""
import os, sys, re
from datetime import datetime, timedelta

import db

# Make the existing Permit Packet Builder engine importable — it's the sibling
# folder, so resolve it relatively (a hardcoded user path breaks on any move).
PPB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   'permit_packet_builder')
if os.path.isdir(PPB) and PPB not in sys.path:
    sys.path.insert(0, PPB)
import build  # noqa: E402  exposes build_packet, SYSTEMS, list_ahjs

HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(HERE, 'output')

STAGES = ['Lead', 'Inspection', 'Estimate', 'Signed', 'Permit',
          'Approved', 'Production', 'Final Inspection', 'Completed']

# Automation map: stage -> (task text, days_offset). days_offset None => no due date.
AUTOMATION = {
    'Lead':             ('Call client & schedule inspection', 1),
    'Inspection':       ('Complete roof measurement / RoofGraf report', 2),
    'Estimate':         ('Send proposal & follow up', 3),
    'Signed':           ('Collect deposit; prep permit packet', 2),
    'Permit':           ('Submit packet to AHJ portal', 1),
    'Approved':         ('Schedule production crew', 3),
    'Production':       ('Order materials; begin install', None),
    'Final Inspection': ('Schedule final inspection', None),
    'Completed':        ('Collect final payment; register manufacturer warranty', None),
}


def _due(days):
    if days is None:
        return None
    return (datetime.now() + timedelta(days=days)).strftime('%Y-%m-%d')


def _slug(s):
    return re.sub(r'[^A-Za-z0-9]+', '_', (s or '')).strip('_') or 'job'


def _job_as_client(job):
    """Map a job row to the client dict build_packet expects."""
    return {
        'owner': job.get('owner', ''), 'address': job.get('address', ''),
        'city': job.get('city', ''), 'zip': job.get('zip', ''),
        'phone': job.get('phone', ''), 'pcn': job.get('pcn', ''),
        'legal': job.get('legal', ''), 'existing': job.get('existing', ''),
        'area': job.get('area', ''), 'slope': job.get('slope', ''),
        'mrh': job.get('mrh', ''), 'exposure': job.get('exposure', ''),
        'value': job.get('value', ''),
    }


def build_packet_for_job(job_id):
    """Build (or rebuild) the permit packet for a job. Returns (filename, message)."""
    job = db.get_job(job_id)
    if not job:
        return None, 'Job not found'
    ahj, system, address = job.get('ahj'), job.get('system'), job.get('address')
    if not (ahj and system and address):
        msg = 'Skipped packet build: missing AHJ, system, or address'
        db.add_activity(job_id, 'automation', msg)
        return None, msg
    if system not in build.SYSTEMS:
        msg = 'Skipped packet build: unknown system "%s"' % system
        db.add_activity(job_id, 'automation', msg)
        return None, msg
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    fname = 'Permit_Packet_%d_%s_%s.pdf' % (job_id, _slug(job.get('owner')), system)
    out_path = os.path.join(OUTPUT_DIR, fname)
    try:
        build.build_packet(_job_as_client(job), ahj, system, [], out_path)
        db.update_job(job_id, packet=fname)
        msg = 'Permit packet built: %s' % fname
        db.add_activity(job_id, 'automation', msg)
        return fname, msg
    except Exception as e:
        msg = 'Permit packet build FAILED: %s' % e
        db.add_activity(job_id, 'automation', msg)
        return None, msg


def run_automation(job_id, stage):
    """Fire the automation for entering `stage`: activity log + task + next_action/next_due."""
    db.add_activity(job_id, 'stage', 'Entered stage: %s' % stage)

    # Permit stage auto-builds the packet.
    if stage == 'Permit':
        build_packet_for_job(job_id)

    task_text, days = AUTOMATION.get(stage, (None, None))
    if task_text:
        due = _due(days)
        db.add_activity(job_id, 'task', task_text, due=due)
        if stage == 'Completed':
            db.update_job(job_id, next_action='', next_due='')
        else:
            db.update_job(job_id, next_action=task_text, next_due=due or '')
        db.add_activity(job_id, 'automation',
                        'Task created: %s%s' % (task_text, (' (due %s)' % due) if due else ''))


def advance(job_id):
    """Move a job to the next stage and run that stage's automation."""
    job = db.get_job(job_id)
    if not job:
        return None
    cur = job.get('stage', 'Lead')
    try:
        idx = STAGES.index(cur)
    except ValueError:
        idx = 0
    if idx >= len(STAGES) - 1:
        return cur  # already Completed
    nxt = STAGES[idx + 1]
    db.set_stage(job_id, nxt)
    run_automation(job_id, nxt)
    return nxt
