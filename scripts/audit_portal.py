# -*- coding: utf-8 -*-
"""Full HO-portal audit: crawl every portal route + feature on a COPY of the DB,
flag broken links, dead anchors, unrendered Jinja, 500s, and emoji/format issues."""
import os, re, sys, shutil

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

# Force SQLite against a throwaway copy so POST routes can't hurt the real DB.
src = os.path.join(HERE, "data", "crm.db")
dst = os.path.join(HERE, "data", "_audit.db")
shutil.copyfile(src, dst)
os.environ["DATABASE_URL"] = ""
os.environ["CRM_DB_PATH"] = dst
os.environ["CRM_NOBROWSER"] = "1"

import app as appmod
import db
from modules import portal as P

flask_app = appmod.app
flask_app.testing = True
C = flask_app.test_client()

EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF☀-➿←-⇿⬀-⯿️™ℹ]"
)

def pick_rich_job():
    jobs = db.all_rows("jobs")
    ests = db.all_rows("estimates")
    docs = db.all_rows("documents")
    invs = db.all_rows("invoices")
    def score(j):
        jid = j["id"]
        return (sum(1 for e in ests if e.get("job_id") == jid)
                + sum(1 for d in docs if d.get("job_id") == jid)
                + sum(1 for i in invs if i.get("job_id") == jid))
    jobs = [j for j in jobs if j.get("id")]
    jobs.sort(key=score, reverse=True)
    return jobs[0] if jobs else None

def scan(path, body, status):
    issues = []
    if status >= 500:
        issues.append("HTTP %s (server error)" % status)
        m = re.search(r"(BuildError|jinja2\.\w+|werkzeug\.routing\.\w+|KeyError|TypeError|AttributeError)[^\n<]{0,160}", body)
        if m: issues.append("  ↳ " + m.group(0).strip())
    if status == 404:
        issues.append("HTTP 404 (not found)")
    if "{{" in body or "{%" in body:
        issues.append("unrendered Jinja in output")
    if "url_for(" in body:
        issues.append("literal url_for( leaked into HTML (broken link)")
    for bad in ['href="None"', 'src="None"', 'href=""', 'action="None"']:
        if bad in body:
            issues.append("empty/None link: %s" % bad)
    em = set(EMOJI.findall(body))
    if em:
        issues.append("emoji present: " + " ".join(sorted(em))[:60])
    # dead in-page anchors: href="#x" whose id="x" is absent
    for anc in set(re.findall(r'href="#([A-Za-z0-9_\-]+)"', body)):
        if anc and ('id="%s"' % anc) not in body:
            issues.append('dead anchor #%s (no matching id)' % anc)
    return issues

def hit(method, path, **kw):
    try:
        r = C.open(path, method=method, **kw)
        body = r.get_data(as_text=True)
        return r.status_code, body
    except Exception as e:
        return 599, "EXC %r" % e

def main():
    job = pick_rich_job()
    if not job:
        print("NO JOBS in DB"); return
    jid = job["id"]
    token = P.ensure_token(jid)
    # a lead token too
    leads = db.all_rows("leads")
    ltoken = P.ensure_lead_token(leads[0]["id"]) if leads else None
    # ids for sub-resources
    ests = [e for e in db.all_rows("estimates") if e.get("job_id") == jid]
    invs = [i for i in db.all_rows("invoices") if i.get("job_id") == jid]
    docs = [d for d in db.all_rows("documents") if d.get("job_id") == jid]
    est_id = ests[0]["id"] if ests else None
    inv_id = invs[0]["id"] if invs else None
    doc_id = docs[0]["id"] if docs else None

    print("=" * 70)
    print("AUDIT job #%s '%s'  token=%s" % (jid, job.get("name", "")[:40], token))
    print("  estimates=%d invoices=%d documents=%d  lead_token=%s"
          % (len(ests), len(invs), len(docs), bool(ltoken)))
    print("=" * 70)

    routes = [
        ("GET",  "/portal/%s" % token, {}),
        ("GET",  "/portal/%s/design" % token, {}),
        ("GET",  "/portal/%s/learn" % token, {}),
        ("GET",  "/portal/%s/seminar" % token, {}),
    ]
    if est_id: routes.append(("GET", "/portal/%s/proposal/%s" % (token, est_id), {}))
    routes.append(("GET", "/portal/%s/pay" % token, {}))
    if inv_id: routes.append(("GET", "/portal/%s/pay/%s" % (token, inv_id), {}))
    if ltoken: routes.append(("GET", "/portal/%s" % ltoken, {}))
    # POST features (against the throwaway DB)
    routes += [
        ("POST", "/portal/%s/message" % token, {"data": {"kind": "question", "text": "audit test"}}),
        ("POST", "/portal/%s/refer/share" % token, {"data": {}}),
        ("POST", "/portal/%s/refer/msg" % token, {"data": {"to": "x@y.com"}}),
        ("POST", "/portal/%s/seminar" % token, {"data": {"name": "T", "email": "t@t.com"}}),
        ("POST", "/portal/%s/design/request" % token, {"data": {"color": "Weathered Wood"}}),
    ]
    if est_id: routes.append(("POST", "/portal/%s/sign/%s" % (token, est_id), {"data": {"sig": "data:,", "name": "T"}}))
    if doc_id: routes.append(("POST", "/portal/%s/sign-doc/%s" % (token, doc_id), {"data": {"sig": "data:,", "name": "T"}}))

    total_issues = 0
    for method, path, kw in routes:
        st, body = hit(method, path, **kw)
        issues = scan(path, body, st)
        flag = "OK " if not issues else "!! "
        total_issues += len(issues)
        print("%s[%s %3s] %s" % (flag, method, st, path))
        for it in issues:
            print("        - %s" % it)

    # standalone templates rendered via their routes already covered; report tally
    print("=" * 70)
    print("TOTAL ISSUES FLAGGED: %d" % total_issues)

    # ---- Exhaustive link resolution across many record states ----------------
    print("\n" + "#" * 70)
    print("# EXHAUSTIVE LINK RESOLUTION (multiple job/lead states)")
    print("#" * 70)
    jobs = db.all_rows("jobs")
    ests = db.all_rows("estimates"); docs = db.all_rows("documents"); invs = db.all_rows("invoices")
    def jscore(j):
        jid = j["id"]
        return (sum(e.get("job_id") == jid for e in ests) + sum(d.get("job_id") == jid for d in docs)
                + sum(i.get("job_id") == jid for i in invs))
    sample = sorted([j for j in jobs if j.get("id")], key=jscore, reverse=True)[:6]
    sample += [j for j in jobs if j.get("id")][:4]   # a few arbitrary phases too
    seen_tokens = []
    upload_dir = os.environ["CRM_UPLOAD_DIR"] if os.environ.get("CRM_UPLOAD_DIR") else os.path.join(HERE, "data", "uploads")
    broken = []
    href_re = re.compile(r'(?:href|action|src)="([^"]+)"')
    for j in sample:
        tok = P.ensure_token(j["id"])
        if tok in seen_tokens: continue
        seen_tokens.append(tok)
        st, body = hit("GET", "/portal/%s" % tok)
        ids = set(re.findall(r'id="([A-Za-z0-9_\-]+)"', body))
        for raw in set(href_re.findall(body)):
            u = raw.strip()
            if u.startswith(("mailto:", "tel:", "sms:", "data:", "javascript:")): continue
            if u.startswith("http"):  # external — note, can't verify here
                continue
            if u.startswith("#"):
                anc = u[1:]
                if anc and anc not in ids:
                    broken.append(("job %s" % j["id"], "DEAD ANCHOR", u))
                continue
            if "/uploads/" in u:
                rel = u.split("/uploads/", 1)[1].split("?")[0]
                fp = os.path.join(upload_dir, *rel.split("/"))
                if not os.path.exists(fp):
                    broken.append(("job %s" % j["id"], "FILE MISSING (local; Drive-fallback live)", u))
                continue
            # internal route → resolve
            sc, _ = hit("GET", u)
            if sc >= 400:
                broken.append(("job %s" % j["id"], "HTTP %s" % sc, u))
    if not broken:
        print("All internal links + anchors resolved across %d states. (Only /uploads may 404 locally — served from Drive live.)" % len(seen_tokens))
    else:
        # dedupe
        uniq = {}
        for who, kind, u in broken:
            uniq.setdefault((kind, u), set()).add(who)
        for (kind, u), whos in sorted(uniq.items()):
            print("  [%s] %s   (%s)" % (kind, u, ", ".join(sorted(whos))[:50]))

if __name__ == "__main__":
    main()
