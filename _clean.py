import re, json, ast
import db
print("IS_PG:", db.IS_PG)

def name_from_blob(s):
    for parser in (json.loads, ast.literal_eval):
        try:
            d = parser(s)
            if isinstance(d, dict):
                return d.get("name") or d.get("abbreviation") or d.get("title")
        except Exception:
            pass
    return None

def clean_field(val):
    if not val: return val
    s = str(val).strip()
    if s.startswith("{") and "name" in s:
        return name_from_blob(s) or val
    return val

def clean_address(val):
    if not val: return val
    s = str(val)
    if "{" not in s: return val
    def repl(m):
        blob = m.group(0)
        for parser in (ast.literal_eval, json.loads):
            try:
                d = parser(blob)
                if isinstance(d, dict):
                    return d.get("abbreviation") or d.get("name") or ""
            except Exception:
                pass
        return blob
    s = re.sub(r"\{[^{}]*\}", repl, s)
    s = re.sub(r"\s+", " ", s).replace(" ,", ",")
    return re.sub(r",\s*,", ",", s).strip().strip(",").strip()

fixed = {"leads":0,"jobs":0,"contacts":0}
for table, fields in (("leads",["work_type","source","address"]),
                      ("jobs",["work_type","source","address"]),
                      ("contacts",["source","address"])):
    for r in db.all_rows(table):
        upd = {}
        for f in fields:
            if f not in r: continue
            new = clean_address(r[f]) if f=="address" else clean_field(r[f])
            if new != r[f]:
                upd[f] = new
        if upd:
            db.update(table, r["id"], **upd)
            fixed[table]+=1
print("rows repaired:", fixed)
# show Eugene now
e = db.all_rows("leads","name LIKE ?",("%Eugene Bright%",))
if e:
    e=e[0]; print("Eugene now -> addr:",repr(e.get("address")),"| work_type:",repr(e.get("work_type")),"| source:",repr(e.get("source")))
