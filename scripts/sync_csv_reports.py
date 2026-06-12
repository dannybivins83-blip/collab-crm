# -*- coding: utf-8 -*-
"""Import all AccuLynx CSV exports into the CRM database (idempotent).

Usage:
    python scripts/sync_csv_reports.py [--dry-run] [--reports-dir PATH]

Processes in dependency order:
    contacts → jobs → leads → invoices → orders
Each table is idempotent: re-running updates existing records, inserts new ones.
"""
import sys
import os
import re
import csv
import glob
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MILESTONE_MAP = {
    "approved":             "approved",
    "completed":            "completed",
    "invoiced":             "invoiced",
    "closed":               "closed",
    "dead":                 "lost",
    "lost":                 "lost",
    "prospect":             "prospect",
    "assigned lead":        "assigned",
    "assigned":             "assigned",
    "long term follow up":  "long_term",
    "long term":            "long_term",
    "negotiation":          "negotiation",
    "canceled":             "canceled",
    "cancelled":            "canceled",
}


def _stage(milestone_str):
    key = (milestone_str or "").strip().lower()
    return _MILESTONE_MAP.get(key, "assigned")


def _money(s):
    if not s:
        return None
    return s.replace("$", "").replace(",", "").strip() or None


def _rid_from_name(name):
    """Extract R-##### from 'R-26034: Client Name ...'"""
    m = re.search(r'(R-\d{4,6})', (name or ""))
    return m.group(1) if m else None


def _parse_address(full):
    """Split 'Street, City, ST Zip US' into parts dict."""
    if not full:
        return {"address": "", "city": "", "state": "", "zip": ""}
    s = re.sub(r'\s+US\s*$', '', full.strip())
    parts = [p.strip() for p in s.split(',')]
    if len(parts) >= 3:
        state_zip = parts[-1].strip()
        m = re.match(r'([A-Z]{2})\s+(\S+)', state_zip)
        state = m.group(1) if m else ""
        zipcode = m.group(2) if m else ""
        city = parts[-2].strip()
        address = ', '.join(parts[:-2])
    elif len(parts) == 2:
        address, city = parts[0], parts[1]
        state = zipcode = ""
    else:
        address = full
        city = state = zipcode = ""
    return {"address": address, "city": city, "state": state, "zip": zipcode}


def _find_csv(reports_dir, pattern):
    """Find the most-recently-modified CSV matching the glob pattern."""
    matches = sorted(glob.glob(os.path.join(reports_dir, pattern)),
                     key=os.path.getmtime, reverse=True)
    return matches[0] if matches else None


def _read_csv(path):
    if not path or not os.path.exists(path):
        return []
    with open(path, newline='', encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))


def _ensure_columns():
    db._ensure_column("contacts", "ext_id", "TEXT")
    db._ensure_column("jobs",     "acculynx_url", "TEXT")
    db._ensure_column("leads",    "acculynx_url", "TEXT")


# ---------------------------------------------------------------------------
# Contacts import
# ---------------------------------------------------------------------------

def sync_contacts(rows, dry_run=False):
    """Import Contacts Report rows → contacts table."""
    # Build lookup index
    existing = db.all_rows("contacts")
    by_email = {(c.get("email") or "").lower(): c for c in existing if c.get("email")}
    by_key = {}
    for c in existing:
        k = "%s|%s" % ((c.get("first_name") or "").lower(), (c.get("last_name") or "").lower())
        by_key[k] = c

    added = updated = skipped = 0
    for row in rows:
        fn = (row.get("Contact: First Name") or "").strip()
        ln = (row.get("Contact: Last Name") or "").strip()
        email = (row.get("Contact: Email") or "").strip().lower()
        phone = (row.get("Contact: Phone") or "").strip()
        kind_raw = (row.get("Contact: Types") or "Customer").strip()
        kind = "company" if "company" in kind_raw.lower() else "person"
        addr_full = row.get("Contact: Mailing Address") or ""
        addr = _parse_address(addr_full)
        created = (row.get("Contact: Created Date") or "").strip()
        # AccuLynx URL is in First Name Url column
        ax_url = (row.get("Contact: First Name Url") or "").strip()
        ext_id = None
        if ax_url:
            m = re.search(r'/contacts/([0-9a-f-]{30,})', ax_url)
            ext_id = m.group(1) if m else None

        rec = {
            "first_name": fn, "last_name": ln, "email": email, "phone": phone,
            "kind": kind, "source": "AccuLynx CSV import",
            **addr,
        }
        if ext_id:
            rec["ext_id"] = ext_id
        if created:
            rec["created"] = created

        match = (by_email.get(email) if email else None) or by_key.get("%s|%s" % (fn.lower(), ln.lower()))
        if match:
            if not dry_run:
                db.update("contacts", match["id"], **{k: v for k, v in rec.items() if v})
            updated += 1
        else:
            if not dry_run:
                nid = db.insert("contacts", rec)
                rec["id"] = nid
                if email:
                    by_email[email] = rec
                by_key["%s|%s" % (fn.lower(), ln.lower())] = rec
            added += 1

    return {"added": added, "updated": updated, "skipped": skipped}


# ---------------------------------------------------------------------------
# Jobs import
# ---------------------------------------------------------------------------

def sync_jobs(rows, dry_run=False):
    """Import Sales Report rows → jobs table."""
    existing = db.all_rows("jobs")
    by_rid = {(j.get("rid") or ""): j for j in existing if j.get("rid")}
    contacts = db.all_rows("contacts")
    by_cname = {}
    for c in contacts:
        name = ("%s %s" % (c.get("first_name", ""), c.get("last_name", ""))).strip().lower()
        if name:
            by_cname[name] = c

    added = updated = 0
    for row in rows:
        rid = (row.get("Job Number") or "").strip()
        if not rid:
            continue
        milestone = row.get("Current Milestone") or ""
        rep = (row.get("Primary Salesperson") or "").strip()
        contact_name = (row.get("Contact Name") or "").strip()
        approved_date = (row.get("Approved Date") or "").strip()
        contract_val = _money(row.get("Contract Amount"))
        collected_val = _money(row.get("Payments Received"))
        balance_val = _money(row.get("Balance Due"))
        ax_url = (row.get("Job Number Url") or "").strip()
        stage = _stage(milestone)

        # Extract address from Jobs Report if available (Sales Report has no address)
        address = (row.get("Location Address") or "").strip()
        addr = _parse_address(address) if address else {}

        rec = {
            "rid": rid, "rep": rep, "stage": stage, "name": contact_name,
            "contract_value": contract_val, "collected": collected_val, "balance": balance_val,
            "department": "REROOF Department",
        }
        if approved_date:
            rec["stage_since"] = approved_date
        if ax_url:
            rec["acculynx_url"] = ax_url
        if addr:
            rec.update(addr)

        match = by_rid.get(rid)
        if match:
            if not dry_run:
                db.update("jobs", match["id"], **{k: v for k, v in rec.items() if v})
            updated += 1
        else:
            # Try to link contact
            contact_id = None
            cname_key = contact_name.lower()
            if cname_key in by_cname:
                contact_id = by_cname[cname_key]["id"]
            if not dry_run:
                rec["contact_id"] = contact_id
                rec["stage_since"] = rec.get("stage_since") or db.today()
                nid = db.insert("jobs", rec)
                rec["id"] = nid
                by_rid[rid] = rec
            added += 1

    return {"added": added, "updated": updated}


# ---------------------------------------------------------------------------
# Jobs import from Jobs Report (enriches address/email/phone)
# ---------------------------------------------------------------------------

def sync_jobs_detail(rows, dry_run=False):
    """Import Jobs Report rows → enrich jobs with address/email/phone/source."""
    existing = db.all_rows("jobs")
    by_rid = {(j.get("rid") or ""): j for j in existing if j.get("rid")}
    by_name = {(j.get("name") or "").lower(): j for j in existing if j.get("name")}

    updated = skipped = 0
    for row in rows:
        job_name = (row.get("Job Name") or "").strip()
        rid = _rid_from_name(job_name)
        email = (row.get("Contact Email") or "").strip()
        phone = (row.get("Phone Number") or "").strip()
        address = (row.get("Location Address") or "").strip()
        source = (row.get("Lead Source") or "").strip()
        work_type = ""  # Jobs Report has no work_type column

        match = by_rid.get(rid) if rid else None
        if not match:
            name_key = job_name.lower()
            match = by_name.get(name_key)
        if not match:
            skipped += 1
            continue

        updates = {}
        if email and not match.get("email"):
            updates["email"] = email
        if phone and not match.get("phone"):
            updates["phone"] = phone
        if source and not match.get("source"):
            updates["source"] = source
        if address and not match.get("address"):
            updates.update(_parse_address(address))

        if updates and not dry_run:
            db.update("jobs", match["id"], **updates)
        updated += 1

    return {"updated": updated, "skipped": skipped}


# ---------------------------------------------------------------------------
# Leads import
# ---------------------------------------------------------------------------

def sync_leads(rows, dry_run=False):
    """Import Lead Status Report rows → leads table."""
    existing = db.all_rows("leads")
    by_rid = {(l.get("rid") or ""): l for l in existing if l.get("rid")}
    by_name = {(l.get("name") or "").lower(): l for l in existing if l.get("name")}

    added = updated = 0
    for row in rows:
        job_name = (row.get("Job Name") or "").strip()
        rid = _rid_from_name(job_name)
        # Name: if has rid prefix, use just the contact part, else use as-is
        if rid:
            name = re.sub(r'^R-\d{4,6}:\s*', '', job_name).strip()
        else:
            name = job_name

        milestone = row.get("Current Milestone") or ""
        rep = (row.get("Primary Salesperson") or "").strip()
        source = (row.get("Lead Source") or "").strip()
        estimate = _money(row.get("Primary Estimate Total"))
        address = (row.get("Location Address") or "").strip()
        phone = (row.get("Phone Number") or "").strip()
        lead_date = (row.get("Lead Date") or "").strip()
        ax_url = (row.get("Job Name Url") or "").strip()
        stage = _stage(milestone)

        # Skip won/converted leads (they're in jobs)
        if stage in ("approved", "completed", "invoiced", "closed"):
            continue

        addr = _parse_address(address) if address else {}
        rec = {
            "name": name, "rep": rep, "source": source, "stage": stage,
            "estimate": estimate, "phone": phone, "department": "REROOF Department",
        }
        if rid:
            rec["rid"] = rid
        if ax_url:
            rec["acculynx_url"] = ax_url
        if lead_date:
            rec["created"] = lead_date
            rec["stage_since"] = lead_date
        if addr:
            rec.update(addr)

        match = (by_rid.get(rid) if rid else None) or by_name.get(name.lower())
        if match:
            if not dry_run:
                db.update("leads", match["id"], **{k: v for k, v in rec.items() if v})
            updated += 1
        else:
            if not dry_run:
                rec["stage_since"] = rec.get("stage_since") or db.today()
                nid = db.insert("leads", rec)
                rec["id"] = nid
                if rid:
                    by_rid[rid] = rec
                by_name[name.lower()] = rec
            added += 1

    return {"added": added, "updated": updated}


# ---------------------------------------------------------------------------
# Invoices import
# ---------------------------------------------------------------------------

def sync_invoices(rows, dry_run=False):
    """Import Invoice Report / AR Age Report rows → invoices table."""
    jobs = db.all_rows("jobs")
    by_rid = {(j.get("rid") or ""): j for j in jobs if j.get("rid")}
    existing_inv = db.all_rows("invoices")
    by_number = {(i.get("number") or ""): i for i in existing_inv if i.get("number")}

    added = updated = skipped = 0
    for row in rows:
        inv_num = (row.get("Invoice Number") or "").strip()
        if not inv_num:
            continue
        # Derive job rid: R-26034-2 → R-26034
        m = re.match(r'(R-\d{4,6})-(\d+)', inv_num)
        if not m:
            skipped += 1
            continue
        job_rid = m.group(1)
        draw_num = int(m.group(2))
        draw_key = "p%d" % draw_num if draw_num <= 5 else "p%d" % draw_num

        job = by_rid.get(job_rid)
        if not job:
            skipped += 1
            continue

        amount = _money(row.get("Invoice Total"))
        balance_due = _money(row.get("Invoice Balance Due"))
        inv_date = (row.get("Invoice Date") or "").strip()
        status_raw = (row.get("Invoice Status") or "unpaid").strip().lower()
        status = "paid" if status_raw == "paid" else ("partial" if balance_due and float(balance_due or 0) > 0 and amount and float(amount or 0) > float(balance_due or 0) else "unpaid")

        rec = {
            "job_id": job["id"], "number": inv_num, "draw_key": draw_key,
            "amount": float(amount) if amount else 0.0,
            "status": status,
        }
        if inv_date:
            rec["due_date"] = inv_date

        match = by_number.get(inv_num)
        if match:
            if not dry_run:
                db.update("invoices", match["id"], **{k: v for k, v in rec.items() if v is not None})
            updated += 1
        else:
            if not dry_run:
                rec["created"] = db.now()
                nid = db.insert("invoices", rec)
                rec["id"] = nid
                by_number[inv_num] = rec
            added += 1

    return {"added": added, "updated": updated, "skipped": skipped}


# ---------------------------------------------------------------------------
# Orders import
# ---------------------------------------------------------------------------

def sync_orders(rows, dry_run=False):
    """Import Production Trades/Status Report rows → orders table."""
    jobs = db.all_rows("jobs")
    by_rid = {(j.get("rid") or ""): j for j in jobs if j.get("rid")}
    existing = db.all_rows("orders")
    by_po = {(o.get("po_number") or ""): o for o in existing if o.get("po_number")}

    added = updated = skipped = 0
    for row in rows:
        po = (row.get("P.O. Number") or "").strip()
        if not po:
            continue
        job_name = (row.get("Job Name") or "").strip()
        job_rid = _rid_from_name(job_name)
        job = by_rid.get(job_rid) if job_rid else None
        if not job:
            skipped += 1
            continue

        order_name = (row.get("Order Name") or "").strip()
        trade = (row.get("Order Trade") or row.get("Trade Name") or "").strip()
        status_raw = (row.get("Order Status") or "draft").strip().lower()
        status = {"approved": "ordered", "completed": "received"}.get(status_raw, "draft")
        vendor = (row.get("Supplier") or "").strip()
        crew = (row.get("Crew Name") or "").strip()
        crew_start = (row.get("Crew Start Date") or "").strip()
        # Determine type from trade name
        otype = "Labor" if "labor" in trade.lower() or "install" in trade.lower() or "dry in" in trade.lower() else "Material"

        rec = {
            "job_id": job["id"], "po_number": po, "type": otype,
            "vendor": vendor or crew or "AccuLynx", "status": status,
            "notes": order_name,
        }
        if crew_start:
            rec["ordered_date"] = crew_start

        match = by_po.get(po)
        if match:
            if not dry_run:
                db.update("orders", match["id"], **{k: v for k, v in rec.items() if v})
            updated += 1
        else:
            if not dry_run:
                rec["created"] = db.now()
                nid = db.insert("orders", rec)
                rec["id"] = nid
                by_po[po] = rec
            added += 1

    return {"added": added, "updated": updated, "skipped": skipped}


# ---------------------------------------------------------------------------
# Payments import — update job balance/collected fields
# ---------------------------------------------------------------------------

def sync_payments(rows, dry_run=False):
    """Import Payments Received Report → update jobs.balance / jobs.collected."""
    jobs = db.all_rows("jobs")
    by_rid = {(j.get("rid") or ""): j for j in jobs if j.get("rid")}
    by_name = {re.sub(r'^R-\d{4,6}:\s*', '', j.get("name") or "").lower(): j for j in jobs}

    updated = skipped = 0
    # Aggregate totals per job
    totals = {}  # rid → {"collected": float, "balance": float, "job_value": float}
    for row in rows:
        job_name = (row.get("Job Name") or "").strip()
        rid = _rid_from_name(job_name)
        job_value = _money(row.get("Job Value"))
        balance = _money(row.get("Balance Due"))
        if not rid:
            skipped += 1
            continue
        totals[rid] = {"contract_value": job_value, "balance": balance}

    for rid, vals in totals.items():
        match = by_rid.get(rid)
        if not match:
            skipped += 1
            continue
        updates = {}
        if vals.get("contract_value") and not match.get("contract_value"):
            updates["contract_value"] = vals["contract_value"]
        if vals.get("balance") is not None:
            updates["balance"] = vals["balance"]
        if updates and not dry_run:
            db.update("jobs", match["id"], **updates)
        updated += 1

    return {"updated": updated, "skipped": skipped}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Sync AccuLynx CSV exports into CRM")
    parser.add_argument("--dry-run", action="store_true", help="Parse only, no DB writes")
    parser.add_argument("--reports-dir", default=os.path.expanduser("~/Downloads"),
                        help="Directory containing AccuLynx CSV exports")
    args = parser.parse_args()

    rdir = args.reports_dir
    dry = args.dry_run

    if dry:
        print("DRY RUN — no DB writes")

    _ensure_columns()

    # ---- Contacts ----
    f = _find_csv(rdir, "Contacts Report*.csv")
    if f:
        rows = _read_csv(f)
        r = sync_contacts(rows, dry)
        print("contacts:  +%d added  %d updated  [%s]" % (r["added"], r["updated"], os.path.basename(f)))
    else:
        print("contacts:  file not found")

    # ---- Jobs (Sales Report → primary) ----
    f = _find_csv(rdir, "Sales Report*.csv")
    if f:
        rows = _read_csv(f)
        r = sync_jobs(rows, dry)
        print("jobs:      +%d added  %d updated  [%s]" % (r["added"], r["updated"], os.path.basename(f)))
    else:
        print("jobs:      Sales Report file not found")

    # ---- Jobs detail enrichment (Jobs Report) ----
    f = _find_csv(rdir, "Jobs Report*.csv")
    if f:
        rows = _read_csv(f)
        r = sync_jobs_detail(rows, dry)
        print("jobs(enr):         %d updated  %d skipped  [%s]" % (r["updated"], r["skipped"], os.path.basename(f)))
    else:
        print("jobs(enr): Jobs Report file not found")

    # ---- Leads ----
    f = _find_csv(rdir, "Lead Status Report*.csv")
    if f:
        rows = _read_csv(f)
        r = sync_leads(rows, dry)
        print("leads:     +%d added  %d updated  [%s]" % (r["added"], r["updated"], os.path.basename(f)))
    else:
        print("leads:     Lead Status Report file not found")

    # ---- Invoices (AR Age Report New = most complete) ----
    f = _find_csv(rdir, "AR Age Report (New)*.csv") or _find_csv(rdir, "AR Age Report*.csv")
    if f:
        rows = _read_csv(f)
        r = sync_invoices(rows, dry)
        print("invoices:  +%d added  %d updated  %d skipped  [%s]" % (r["added"], r["updated"], r["skipped"], os.path.basename(f)))
    else:
        # Fall back to Invoice Report CSV
        f = _find_csv(rdir, "Invoice Report*.csv")
        if f:
            rows = _read_csv(f)
            r = sync_invoices(rows, dry)
            print("invoices:  +%d added  %d updated  %d skipped  [%s]" % (r["added"], r["updated"], r["skipped"], os.path.basename(f)))
        else:
            print("invoices:  file not found")

    # ---- Orders (Production Trades Report) ----
    f = _find_csv(rdir, "Production Trades Report*.csv")
    if f:
        rows = _read_csv(f)
        r = sync_orders(rows, dry)
        print("orders:    +%d added  %d updated  %d skipped  [%s]" % (r["added"], r["updated"], r["skipped"], os.path.basename(f)))
    else:
        print("orders:    Production Trades Report file not found")

    # ---- Payments enrichment ----
    f = _find_csv(rdir, "Payments Received Report*.csv")
    if f:
        rows = _read_csv(f)
        r = sync_payments(rows, dry)
        print("payments:          %d updated  %d skipped  [%s]" % (r["updated"], r["skipped"], os.path.basename(f)))
    else:
        print("payments:  Payments Received Report file not found")

    print("\nDone.")


if __name__ == "__main__":
    main()
