# -*- coding: utf-8 -*-
import os, re, sys
HERE=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0,HERE)
for line in open(os.path.join(HERE,".env.production"),encoding="utf-8-sig"):
    line=line.strip()
    if line.startswith("DATABASE_URL="):
        u=line.split("=",1)[1].strip().strip('"').strip("'")
        os.environ["DATABASE_URL"]=re.sub(r"\s","",u.replace("\r","").replace("\n","")).replace("﻿","")
        break
os.environ["CRM_NOBROWSER"]="1"
import db
from collections import Counter
ws=db.all_rows("worksheets"); wl=db.all_rows("worksheet_lines")
print(f"worksheets: {len(ws)} | worksheet_lines: {len(wl)}")
contract=sum(float(w.get('contract_value') or 0) for w in ws)
cost=sum(float(l.get('actual_cost') or 0) for l in wl)
gp=contract-cost
print(f"contract total: ${contract:,.2f}")
print(f"cost total:     ${cost:,.2f}")
print(f"gross profit:   ${gp:,.2f}  ({100*gp/contract if contract else 0:.1f}%)")
cc=Counter(l.get('category') for l in wl)
print("category counts:", dict(cc))
# cost by category
costcat={}
for l in wl: costcat[l.get('category')]=costcat.get(l.get('category'),0)+float(l.get('actual_cost') or 0)
print("cost by category:", {k:round(v) for k,v in sorted(costcat.items(),key=lambda x:-x[1])})
# spot check Richard Reis 1087
r=[w for w in ws if w.get('job_id')==1087]
if r:
    rid=r[0]['id']; rl=[l for l in wl if l.get('worksheet_id')==rid]
    print(f"\nRichard Reis (1087): contract ${float(r[0].get('contract_value') or 0):,.0f}")
    for l in sorted(rl,key=lambda x:x.get('sort') or 0):
        print(f"   [{l.get('category')}] {l.get('description')[:55]} = ${float(l.get('actual_cost') or 0):,.2f}")
