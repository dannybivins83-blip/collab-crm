# Full pull: jobs with city + tradeTypes so the AHJ x system matrix comes from data.
import sqlite3, json, urllib.request, urllib.parse, sys
DB = r"C:\Users\kjburnz\acculynx roofr reprot\whitelabel-crm\data\crm_migration.db"
key = sqlite3.connect(DB).execute("select acculynx_api_key from company_settings").fetchone()[0].strip()
def get(path, params):
    url = "https://api.acculynx.com/api/v2" + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + key, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())
jobs = []
for grp in ("approved", "completed", "invoiced", "closed"):
    start = 0
    while True:
        try:
            d = get("/jobs", {"milestones": grp, "pageStartIndex": start, "pageSize": 25,
                              "sortBy": "MilestoneDate", "sortOrder": "Descending"})
        except Exception as e:
            print(f"{grp} @{start}: {e}", file=sys.stderr); break
        items = d.get("items") or []
        if not items: break
        for j in items:
            la = j.get("locationAddress") or {}
            jobs.append({
                "name": j.get("jobName") or "", "id": j.get("id"), "group": grp,
                "city": la.get("city") or "", "zip": la.get("zipCode") or "",
                "street": la.get("street1") or "",
                "trades": [t.get("name") for t in (j.get("tradeTypes") or [])],
                "workType": (j.get("workType") or {}).get("name"),
                "milestoneDate": (j.get("milestoneDate") or "")[:10],
                "created": (j.get("createdDate") or "")[:10]})
        start += len(items)
        if len(items) < 25: break
    print(grp, len(jobs), file=sys.stderr)
json.dump(jobs, open("ax_jobs_full.json", "w"), indent=1)
from collections import Counter
print("trades:", Counter(t for j in jobs for t in j["trades"]).most_common(30))
print("cities:", Counter(j["city"].strip().title() for j in jobs).most_common(40))
