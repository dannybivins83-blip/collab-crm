# -*- coding: utf-8 -*-
"""SeaBreeze Job Management - Flask app (Kanban + automated workflow).
Includes the embedded Permit Packet Builder wizard (/builder)."""
import os, re, time, socket, webbrowser, threading
from datetime import datetime
from flask import (Flask, render_template, request, redirect, url_for,
                   send_from_directory, jsonify, abort)

import db
import workflow
import build

HERE = os.path.dirname(os.path.abspath(__file__))
# Save finished packets to Google Drive (syncs to the laptop). Try the tidy
# subfolder, then the main Drive folder, then local — and WRITE-TEST each so a
# flaky Drive folder never breaks builds.
def _pick_output():
    for c in [os.environ.get('SEABREEZE_OUTPUT'),
              r'G:\My Drive\SeaBreeze Roofing\Permit Packets',
              r'G:\My Drive\SeaBreeze Roofing',
              os.path.join(HERE, 'output')]:
        if not c:
            continue
        try:
            os.makedirs(c, exist_ok=True)
            t = os.path.join(c, '.wtest')
            open(t, 'w').close(); os.remove(t)
            return c
        except Exception:
            continue
    return os.path.join(HERE, 'output')
OUTPUT_DIR = _pick_output()
UPLOAD_DIR = os.path.join(HERE, 'uploads')
os.makedirs(UPLOAD_DIR, exist_ok=True)
print('  Packets save to: %s' % OUTPUT_DIR)

# Job fields the builder wizard collects (client dict build_packet expects).
BUILDER_FIELDS = ['owner', 'address', 'city', 'zip', 'phone', 'pcn', 'legal',
                  'existing', 'area', 'slope', 'mrh', 'exposure', 'value']

app = Flask(__name__)
db.init_db()


def _today():
    return datetime.now().strftime('%Y-%m-%d')


def _days_in_stage(job):
    ts = job.get('updated') or job.get('created')
    if not ts:
        return 0
    try:
        d = datetime.strptime(ts.split(' ')[0], '%Y-%m-%d')
        return (datetime.now() - d).days
    except Exception:
        return 0


def _overdue(job):
    nd = job.get('next_due')
    return bool(nd) and nd <= _today()


app.jinja_env.globals.update(days_in_stage=_days_in_stage, overdue=_overdue, today=_today)


@app.route('/')
def dashboard():
    # The real board is the standalone HTML dashboard; this app is just the
    # permit packet builder, so send the root straight to the builder.
    return redirect(url_for('builder'))


@app.route('/job/new', methods=['GET', 'POST'])
def job_new():
    if request.method == 'POST':
        data = {f: request.form.get(f, '').strip() for f in db.JOB_FIELDS}
        data['stage'] = 'Lead'
        jid = db.add_job(data)
        # Fire Lead automation for the brand-new job.
        workflow.run_automation(jid, 'Lead')
        return redirect(url_for('job_detail', job_id=jid))
    return render_template('new_job.html', job={}, ahjs=build.list_ahjs(),
                           systems=list(build.SYSTEMS.keys()), stages=workflow.STAGES,
                           mode='new')


@app.route('/job/<int:job_id>')
def job_detail(job_id):
    job = db.get_job(job_id)
    if not job:
        abort(404)
    return render_template('job.html', job=job, activity=db.job_activity(job_id),
                           tasks=db.open_tasks(job_id), stages=workflow.STAGES)


@app.route('/job/<int:job_id>/advance', methods=['POST'])
def job_advance(job_id):
    workflow.advance(job_id)
    nxt = request.form.get('next')
    if nxt == 'dashboard':
        return redirect(url_for('dashboard'))
    return redirect(url_for('job_detail', job_id=job_id))


@app.route('/job/<int:job_id>/build', methods=['POST'])
def job_build(job_id):
    workflow.build_packet_for_job(job_id)
    return redirect(url_for('job_detail', job_id=job_id))


@app.route('/job/<int:job_id>/note', methods=['POST'])
def job_note(job_id):
    text = request.form.get('text', '').strip()
    if text:
        db.add_activity(job_id, 'note', text)
    return redirect(url_for('job_detail', job_id=job_id))


@app.route('/task/<int:task_id>/done', methods=['POST'])
def task_done(task_id):
    db.complete_task(task_id)
    job_id = request.form.get('job_id')
    if job_id:
        return redirect(url_for('job_detail', job_id=job_id))
    return redirect(url_for('dashboard'))


@app.route('/job/<int:job_id>/edit', methods=['GET', 'POST'])
def job_edit(job_id):
    job = db.get_job(job_id)
    if not job:
        abort(404)
    if request.method == 'POST':
        data = {f: request.form.get(f, '').strip() for f in db.JOB_FIELDS}
        db.update_job(job_id, **data)
        return redirect(url_for('job_detail', job_id=job_id))
    return render_template('new_job.html', job=job, ahjs=build.list_ahjs(),
                           systems=list(build.SYSTEMS.keys()), stages=workflow.STAGES,
                           mode='edit')


@app.route('/builder')
def builder():
    """Embedded Permit Packet Builder wizard.
    Pre-fills from a Job Manager job (?job_id=) OR from URL params passed by the
    external SeaBreeze HTML dashboard (?owner=&address=&phone=&value=...)."""
    pf, job_id = {}, request.args.get('job_id', '')
    if job_id.isdigit():
        j = db.get_job(int(job_id))
        if j:
            pf = dict(j)
    # Query-param prefill (used by the standalone HTML dashboard) augments/overrides.
    for k in BUILDER_FIELDS + ['ahj', 'system']:
        v = request.args.get(k)
        if v:
            pf[k] = v
    return render_template('builder.html', ahjs=build.list_ahjs(),
                           systems=list(build.SYSTEMS.keys()), pf=pf, job_id=job_id)


def _parse_feed(path, group):
    """Parse a dashboard jobs-data.js / prospects-data.js feed into client dicts."""
    out = []
    try:
        txt = open(path, encoding='utf-8').read()
    except Exception:
        return out
    for obj in re.findall(r'\{[^{}]*\}', txt):
        def g(k):
            m = re.search(k + r':"((?:[^"\\]|\\.)*)"', obj)
            return (m.group(1).replace('\\"', '"') if m else '')
        name = g('name')
        if not name:
            continue
        addr = g('addr') or g('address')
        parts = [p.strip() for p in addr.split(',')]
        street = parts[0] if parts else ''
        city = parts[1] if len(parts) > 1 else ''
        mz = re.search(r'(\d{5})', addr)
        out.append({
            'label': ((g('rid') + ' · ') if g('rid') else '') + name +
                     (('  — ' + g('type')) if g('type') else ''),
            'owner': name, 'address': street, 'city': city,
            'zip': (mz.group(1) if mz else ''),
            'phone': g('phone'), 'value': re.sub(r'[^0-9.]', '', g('estimate')),
            'group': group})
    return out


@app.route('/clients')
def clients():
    """Client list (jobs + prospects) from the dashboard feeds, for the builder picker."""
    root = os.path.dirname(os.path.dirname(HERE))  # the 'acculynx roofr reprot' project root
    return jsonify({
        'jobs': _parse_feed(os.path.join(root, 'jobs-data.js'), 'Job Process'),
        'prospects': _parse_feed(os.path.join(root, 'prospects-data.js'), 'Prospects'),
    })


# ---------------------------------------------------------------------------
# Broward County (BCPA) folio + legal auto-lookup.
# Mirrors the PBC path: address -> folio (BCPA ArcGIS), folio -> legal (BCPA web API).
# ---------------------------------------------------------------------------
_BWD_AHJS = ('deerfield', 'fort_lauderdale', 'pompano', 'margate', 'coral_springs',
             'hollywood', 'davie', 'plantation', 'sunrise', 'tamarac', 'lauderdale',
             'miramar', 'weston', 'parkland', 'coconut_creek', 'oakland_park', 'wilton',
             'hallandale', 'cooper_city', 'pembroke', 'dania')
_BWD_DIR = {'N': 'N', 'S': 'S', 'E': 'E', 'W': 'W', 'NE': 'NE', 'NW': 'NW', 'SE': 'SE', 'SW': 'SW',
            'NORTH': 'N', 'SOUTH': 'S', 'EAST': 'E', 'WEST': 'W',
            'NORTHEAST': 'NE', 'NORTHWEST': 'NW', 'SOUTHEAST': 'SE', 'SOUTHWEST': 'SW'}
_BWD_TYPE = {'ST': 'ST', 'STREET': 'ST', 'AVE': 'AVE', 'AV': 'AVE', 'AVENUE': 'AVE',
             'DR': 'DR', 'DRIVE': 'DR', 'CT': 'CT', 'COURT': 'CT', 'TER': 'TER', 'TERR': 'TER',
             'TERRACE': 'TER', 'BLVD': 'BLVD', 'BOULEVARD': 'BLVD', 'RD': 'RD', 'ROAD': 'RD',
             'LN': 'LN', 'LANE': 'LN', 'WAY': 'WAY', 'PL': 'PL', 'PLACE': 'PL', 'CIR': 'CIR',
             'CIRCLE': 'CIR', 'PKWY': 'PKWY', 'PARKWAY': 'PKWY', 'TRL': 'TRL', 'TRAIL': 'TRL',
             'PLZ': 'PLZ', 'PLAZA': 'PLZ', 'LOOP': 'LOOP', 'RUN': 'RUN', 'CV': 'CV', 'COVE': 'CV',
             'PT': 'PT', 'POINT': 'PT'}
_BWD_GIS = 'https://gisweb-adapters.bcpa.net/arcgis/rest/services'
_BWD_UA = {'User-Agent': 'Mozilla/5.0'}
_BWD_CACHE = {}


def _is_broward_ahj(ahj):
    a = (ahj or '').lower()
    return any(k in a for k in _BWD_AHJS)


def _broward_service():
    """Newest BCPA_EXTERNAL_<MON><YY> MapServer (cached). Falls back to JAN26."""
    if 'svc' in _BWD_CACHE:
        return _BWD_CACHE['svc']
    import urllib.request, json as _json
    svc = 'BCPA_EXTERNAL_JAN26'
    mon = {'JAN': 1, 'FEB': 2, 'MAR': 3, 'APR': 4, 'MAY': 5, 'JUN': 6, 'JUL': 7,
           'AUG': 8, 'SEP': 9, 'OCT': 10, 'NOV': 11, 'DEC': 12}
    try:
        req = urllib.request.Request(_BWD_GIS + '?f=json', headers=_BWD_UA)
        with urllib.request.urlopen(req, timeout=12) as r:
            d = _json.loads(r.read().decode())
        best = None
        for s in d.get('services', []):
            nm = s.get('name', '').split('/')[-1]
            m = re.match(r'BCPA_EXTERNAL_([A-Z]{3})(\d{2})$', nm)
            if m and m.group(1) in mon:
                key = (int(m.group(2)), mon[m.group(1)])
                if best is None or key > best[0]:
                    best = (key, nm)
        if best:
            svc = best[1]
    except Exception:
        pass
    _BWD_CACHE['svc'] = svc
    return svc


def _broward_info_layer():
    """Layer id of the BCPA_INFO situs-address table in the chosen service (cached). Default 36."""
    if 'lyr' in _BWD_CACHE:
        return _BWD_CACHE['lyr']
    import urllib.request, json as _json
    lyr = 36
    try:
        url = '%s/%s/MapServer?f=json' % (_BWD_GIS, _broward_service())
        req = urllib.request.Request(url, headers=_BWD_UA)
        with urllib.request.urlopen(req, timeout=12) as r:
            d = _json.loads(r.read().decode())
        for t in d.get('tables', []):
            if t.get('name', '').upper().endswith('BCPA_INFO'):
                lyr = t.get('id')
                break
    except Exception:
        pass
    _BWD_CACHE['lyr'] = lyr
    return lyr


def _bwd_parse_street(raw):
    """Split a street string into BCPA parts: (name, direction, type).
    'NE 6th Street' -> ('6','NE','ST'); 'Northeast 15th Avenue' -> ('15','NE','AVE');
    'Magnolia Drive' -> ('MAGNOLIA','','DR'). BCPA stores the name with the ordinal
    suffix and the directional/type pulled out into their own fields."""
    toks = re.sub(r'[^A-Za-z0-9 ]', ' ', raw or '').upper().split()
    direction = stype = ''
    if toks and toks[0] in _BWD_DIR:
        direction = _BWD_DIR[toks.pop(0)]
    if toks and toks[-1] in _BWD_TYPE:
        stype = _BWD_TYPE[toks[-1]]
        toks = toks[:-1]
    if toks and toks[-1] in _BWD_DIR:  # trailing post-direction (rare) -> drop
        toks = toks[:-1]
    out = []
    for t in toks:
        m = re.match(r'^(\d+)(ST|ND|RD|TH)$', t)  # 6TH -> 6, 1ST -> 1
        out.append(m.group(1) if m else t)
    return ' '.join(out).strip(), direction, stype


def _broward_legal(folio):
    """Abbreviated legal description for a Broward folio (BCPA web API)."""
    import urllib.request, json as _json
    folio = re.sub(r'[^0-9]', '', folio or '')
    if not folio:
        return ''
    body = _json.dumps({'folioNumber': folio, 'taxyear': str(datetime.now().year),
                        'action': 'CURRENT', 'use': ''}).encode()
    hdr = {'Content-Type': 'application/json'}
    hdr.update(_BWD_UA)
    try:
        req = urllib.request.Request(
            'https://web.bcpa.net/BcpaClient/search.aspx/getParcelInformation', data=body, headers=hdr)
        with urllib.request.urlopen(req, timeout=15) as r:
            d = _json.loads(r.read().decode())
        info = (d.get('d') or {}).get('parcelInfok__BackingField') or []
        if info:
            return (info[0].get('legal') or '').strip()
    except Exception:
        return ''
    return ''


def _broward_lookup():
    """Resolve a Broward address to {pcn (folio), legal, matched}. Returns {} if no match."""
    import urllib.request, urllib.parse, json as _json
    no = re.sub(r'[^0-9]', '', request.args.get('street_no', ''))
    raw = request.args.get('street', '') or request.args.get('street_name', '')
    zipc = re.sub(r'[^0-9]', '', request.args.get('zip', ''))[:5]
    name, direction, stype = _bwd_parse_street(raw)
    if not no or not name:
        return {'pcn': ''}
    base = '%s/%s/MapServer/%s/query' % (_BWD_GIS, _broward_service(), _broward_info_layer())
    of = ('FOLIO_NUMBER,SITUS_STREET_NUMBER,SITUS_STREET_DIRECTION,SITUS_STREET_NAME,'
          'SITUS_STREET_TYPE,SITUS_ZIP_CODE')

    def run(where):
        q = urllib.parse.urlencode({'where': where, 'outFields': of,
                                    'returnGeometry': 'false', 'f': 'json'})
        req = urllib.request.Request(base + '?' + q, headers=_BWD_UA)
        with urllib.request.urlopen(req, timeout=15) as r:
            return _json.loads(r.read().decode()).get('features', [])

    e = lambda v: v.replace("'", "''")
    conds = ["SITUS_STREET_NUMBER='%s'" % e(no), "SITUS_STREET_NAME='%s'" % e(name)]
    if direction:
        conds.append("SITUS_STREET_DIRECTION='%s'" % direction)
    if stype:
        conds.append("SITUS_STREET_TYPE='%s'" % stype)
    try:
        feats = run(' AND '.join(conds))
        if not feats and stype:  # relax street type
            feats = run(' AND '.join(c for c in conds if 'STREET_TYPE' not in c))
        if not feats and direction:  # relax direction
            feats = run("SITUS_STREET_NUMBER='%s' AND SITUS_STREET_NAME='%s'" % (e(no), e(name)))
        if not feats:  # last resort: prefix match on name
            feats = run("SITUS_STREET_NUMBER='%s' AND SITUS_STREET_NAME LIKE '%s%%'" % (e(no), e(name)))
    except Exception as ex:
        return {'pcn': '', 'error': str(ex)}
    if not feats:
        return {}
    if zipc and len(feats) > 1:  # disambiguate remaining matches by zip
        for f in feats:
            if (f['attributes'].get('SITUS_ZIP_CODE') or '')[:5] == zipc:
                feats = [f]
                break
    a = feats[0]['attributes']
    folio = re.sub(r'[^0-9]', '', str(a.get('FOLIO_NUMBER', '')))
    fmt = folio
    if len(folio) == 12:
        fmt = '%s-%s-%s-%s-%s' % (folio[0:2], folio[2:4], folio[4:6], folio[6:8], folio[8:12])
    matched = ' '.join(str(a.get(k) or '') for k in
                       ('SITUS_STREET_NUMBER', 'SITUS_STREET_DIRECTION', 'SITUS_STREET_NAME', 'SITUS_STREET_TYPE')).split()
    return {'pcn': fmt, 'legal': _broward_legal(folio),
            'matched': ' '.join(matched), 'count': len(feats)}


@app.route('/pcn')
def pcn_lookup():
    """Look up a county parcel id + legal by street number + name.
    Broward (BCPA) when ?county=broward or a Broward AHJ is passed; else Palm Beach County."""
    import urllib.request, urllib.parse, json as _json
    if request.args.get('county', '').strip().lower() == 'broward' or _is_broward_ahj(request.args.get('ahj', '')):
        return jsonify(_broward_lookup())
    sn = re.sub(r'[^0-9]', '', request.args.get('street_no', ''))
    name = re.sub(r"[^A-Za-z0-9 ]", '', request.args.get('street_name', '')).strip().upper()
    if not sn or not name:
        return jsonify({'pcn': ''})
    url = 'https://maps.co.palm-beach.fl.us/arcgis/rest/services/OpenData/open_data_v2/FeatureServer/0/query'
    where = "STREET_NO='%s' AND UPPER(STREET_NAME) LIKE '%%%s%%'" % (sn, name)
    q = urllib.parse.urlencode({'where': where, 'outFields': 'PCN,STREET_NO,STREET_NAME,CITY',
                                'returnGeometry': 'false', 'f': 'json'})
    try:
        with urllib.request.urlopen(url + '?' + q, timeout=12) as r:
            d = _json.loads(r.read().decode())
        feats = d.get('features', [])
        if feats:
            raw = ''.join(ch for ch in str(feats[0]['attributes'].get('PCN', '')) if ch.isdigit())
            fmt = raw
            if len(raw) == 17:
                fmt = '%s-%s-%s-%s-%s-%s-%s' % (raw[0:2], raw[2:4], raw[4:6], raw[6:8], raw[8:10], raw[10:13], raw[13:17])
            a = feats[0]['attributes']
            return jsonify({'pcn': fmt, 'legal': _pbc_legal(raw),
                            'matched': '%s %s, %s' % (a.get('STREET_NO', ''), a.get('STREET_NAME', ''), a.get('CITY', '')),
                            'count': len(feats)})
    except Exception as e:
        return jsonify({'pcn': '', 'error': str(e)})
    return jsonify({'pcn': ''})


def _pbc_legal(pcn_digits):
    """Build a legal description for a PBC parcel from its PCN (subdivision + lot + block)."""
    import urllib.request, urllib.parse, json as _json
    if not pcn_digits:
        return ''
    url = 'https://maps.co.palm-beach.fl.us/arcgis/rest/services/Parcels/labels/MapServer/0/query'
    of = 'PAO.PROPINFO_PUB.SUBDIV_NAME,PAO.PARCELS.BLK,PAO.PARCELS.LOT,PAO.PARCELS.SEC,PAO.PARCELS.TWP,PAO.PARCELS.RNG'
    q = urllib.parse.urlencode({'where': "PAO.PROPINFO_PUB.PARCEL_NUMBER='%s'" % pcn_digits,
                                'outFields': of, 'returnGeometry': 'false', 'f': 'json'})
    try:
        with urllib.request.urlopen(url + '?' + q, timeout=12) as r:
            d = _json.loads(r.read().decode())
        fs = d.get('features', [])
        if not fs:
            return ''
        a = fs[0]['attributes']
        sub = (a.get('PAO.PROPINFO_PUB.SUBDIV_NAME') or '').strip()
        blk = (str(a.get('PAO.PARCELS.BLK') or '')).strip()
        lot = (str(a.get('PAO.PARCELS.LOT') or '')).strip()
        parts = []
        if sub:
            parts.append(sub)
        # Standard FL legal order is LOT then BLOCK. WARNING: county GIS lot/block codes don't always
        # match the recorded plat (GIS LOT '0040' vs recorded 'LT 4'), and the plat book/page (e.g.
        # 'IN PB49P179') isn't in this layer — so this is an APPROXIMATION, never authoritative. The
        # wizard flags it for verification against PBCPA so a wrong legal never lands on a recorded NOC.
        if lot and lot.strip('0'):
            parts.append('LT %s' % lot.lstrip('0'))
        if blk and blk.strip('0'):
            parts.append('BLK %s' % blk.lstrip('0'))
        if not parts:  # metes & bounds fallback
            sec, twp, rng = a.get('PAO.PARCELS.SEC'), a.get('PAO.PARCELS.TWP'), a.get('PAO.PARCELS.RNG')
            if sec:
                parts.append('SEC %s TWP %s RNG %s' % (sec, twp, rng))
        return ' '.join(parts)
    except Exception:
        return ''


@app.route('/builder/build', methods=['POST'])
def builder_build():
    """Build a packet straight from the wizard (mirrors the standalone builder)."""
    client = {k: request.form.get(k, '').strip() for k in BUILDER_FIELDS}
    ahj = request.form.get('ahj', '').strip()
    system = request.form.get('system', '').strip()
    if not ahj or system not in build.SYSTEMS:
        return jsonify({'error': 'Please select an AHJ and a system type.'}), 400
    if not client['owner'] or not client['address']:
        return jsonify({'error': 'Owner name and property address are required (Step 1).'}), 400
    # Save any attached PDFs.
    att = []
    for f in request.files.getlist('attachments'):
        if f and f.filename and f.filename.lower().endswith('.pdf'):
            dest = os.path.join(UPLOAD_DIR, '%d_%s' % (int(time.time() * 1000),
                                                       f.filename.replace(' ', '_')))
            f.save(dest)
            att.append(dest)
    # Auto-pull roof squares + pitch from an attached RoofGraf report when not typed in.
    if not client.get('area') or not client.get('slope'):
        for p in att:
            meas = build.parse_roofgraf(p)
            if meas:
                if not client.get('area') and meas.get('area'):
                    client['area'] = meas['area']
                if not client.get('slope') and meas.get('pitch'):
                    client['slope'] = meas['pitch']
                break
    underlayment = request.form.get('underlayment', '').strip() or None
    product = request.form.get('product', '').strip() or None
    safe = re.sub(r'[^A-Za-z0-9]+', '_', client['owner']).strip('_') or 'client'
    ultag = ('_2ply' if underlayment == '2ply' else '')
    ptag = ('_' + product) if (product and product not in ('oc', 'westlake')) else ''
    outname = '%s_%s_%s%s%s_Permit_Packet.pdf' % (safe, ahj, system, ptag, ultag)
    try:
        build.build_packet(client, ahj, system, att, os.path.join(OUTPUT_DIR, outname), underlayment, product)
    except Exception as e:
        return jsonify({'error': 'Build failed: %s' % e}), 500
    # If launched from a job, record the packet on it.
    job_id = request.form.get('job_id', '')
    if job_id.isdigit():
        db.update_job(int(job_id), packet=outname)
        db.add_activity(int(job_id), 'automation', 'Permit packet built via Builder: %s' % outname)
    return jsonify({'ok': True, 'file': outname})


@app.route('/builder/measure', methods=['POST'])
def builder_measure():
    """Parse an uploaded RoofGraf report and return {area, squares, pitch} so the
    wizard can auto-fill roof area + pitch the moment the report is attached."""
    f = request.files.get('file')
    if not f or not f.filename.lower().endswith('.pdf'):
        return jsonify({})
    tmp = os.path.join(UPLOAD_DIR, '_measure_%d.pdf' % int(time.time() * 1000))
    try:
        f.save(tmp)
        return jsonify(build.parse_roofgraf(tmp))
    except Exception:
        return jsonify({})
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass


@app.route('/download/<path:file>')
def download(file):
    safe = os.path.basename(file)
    if not os.path.exists(os.path.join(OUTPUT_DIR, safe)):
        abort(404)
    return send_from_directory(OUTPUT_DIR, safe, as_attachment=True)


def _free_port(preferred):
    """Return the preferred port if free, else the next available one.
    Avoids silently colliding with the Permit Packet Builder (also on 5000)."""
    for p in range(preferred, preferred + 25):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if s.connect_ex(('127.0.0.1', p)) != 0:  # nothing listening -> free
                return p
    return preferred


PORT = _free_port(int(os.environ.get('SEABREEZE_PORT', '5000')))


def _open_browser():
    if os.environ.get('SEABREEZE_NOBROWSER'):
        return
    try:
        webbrowser.open('http://127.0.0.1:%d' % PORT)
    except Exception:
        pass


if __name__ == '__main__':
    if os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
        print('\n  SeaBreeze Job Management running at  http://127.0.0.1:%d\n' % PORT)
        threading.Timer(1.2, _open_browser).start()
    app.run(host='127.0.0.1', port=PORT, debug=False)
