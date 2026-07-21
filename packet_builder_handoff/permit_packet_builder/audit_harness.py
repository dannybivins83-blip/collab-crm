# -*- coding: utf-8 -*-
"""Permit-packet audit harness.

Builds a packet for one (AHJ, system) pair and reports what's IN it, so an
auditor can compare it against a real approved packet from
`approved_permit_examples/`.

Usage (one combo):
    python audit_harness.py --ahj boca_raton --system Shingle --job R-26054 \
        --owner "Iconomou" --address "297 NW 64th Street" --city "Boca Raton" \
        --zip 33487 --area 2400 --slope 4 --out D:\\audit\\boca_shingle.pdf

Usage (inspect a REAL approved example instead of building):
    python audit_harness.py --inspect "..\\approved_permit_examples\\Boca_Raton_Flat_R26054"

Prints a JSON report to stdout. Never touches the network unless --fetch-pa.
"""
import argparse, json, os, re, sys, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Fields that must never render blank on a submitted permit packet.
REQUIRED_CLIENT_FIELDS = ['owner', 'address', 'city', 'zip', 'phone', 'pcn', 'legal',
                          'existing', 'area', 'slope', 'mrh', 'exposure', 'value']
# Placeholder tokens that are ACCEPTABLE in a blank (reviewer knows to fill it).
OK_PLACEHOLDERS = ('N/A', 'NA', 'DATA NEEDED', 'SEE ATTACHED')

# Text markers that indicate a required packet component is present.
COMPONENT_MARKERS = {
    'permit_application': ('permit application', 'building permit application',
                           'uniform building permit', 'application for permit'),
    'noc': ('notice of commencement', 'noc'),
    'roofing_form': ('roofing permit', 'roof permit application', 'roofing application'),
    'product_approval': ('florida product approval', 'noa', 'notice of acceptance',
                         'fl#', 'product approval'),
    'spec_sheet': ('specification', 'spec sheet', 'installation instruction'),
    'wind_calc': ('wind load', 'asce', 'design pressure', 'uplift'),
}


def pdf_text(path, max_pages=None):
    """Extract text from a PDF. Returns (text, page_count)."""
    try:
        from pypdf import PdfReader
    except ImportError:
        from PyPDF2 import PdfReader          # older env fallback
    r = PdfReader(path)
    pages = r.pages if max_pages is None else r.pages[:max_pages]
    return '\n'.join((p.extract_text() or '') for p in pages), len(r.pages)


def find_blanks(text, client):
    """Values we passed in that never made it onto the paper, plus obviously
    empty labelled blanks (label followed by nothing / underscores)."""
    missing = []
    for k in REQUIRED_CLIENT_FIELDS:
        v = str(client.get(k) or '').strip()
        if not v:
            missing.append({'field': k, 'issue': 'no value supplied',
                            'severity': 'blank-input'})
            continue
        if v.upper() in OK_PLACEHOLDERS:
            continue
        probe = re.sub(r'[^A-Za-z0-9]', '', v)[:10]
        flat = re.sub(r'[^A-Za-z0-9]', '', text)
        if probe and probe.lower() not in flat.lower():
            missing.append({'field': k, 'value': v,
                            'issue': 'supplied value not found in packet text',
                            'severity': 'not-rendered'})
    return missing


def components_present(text):
    low = text.lower()
    return {name: any(m in low for m in markers)
            for name, markers in COMPONENT_MARKERS.items()}


def inspect_pdf(path):
    txt, pages = pdf_text(path)
    return {'file': os.path.basename(path), 'pages': pages,
            'bytes': os.path.getsize(path), 'components': components_present(txt),
            'text_chars': len(txt)}


def inspect_folder(folder):
    out = {'folder': os.path.basename(folder), 'files': [], 'errors': []}
    for fn in sorted(os.listdir(folder)):
        p = os.path.join(folder, fn)
        if not os.path.isfile(p):
            continue
        if fn.lower().endswith('.pdf'):
            try:
                out['files'].append(inspect_pdf(p))
            except Exception as e:
                out['errors'].append({'file': fn, 'error': str(e)})
        else:
            out['files'].append({'file': fn, 'bytes': os.path.getsize(p),
                                 'note': 'non-PDF (photo/scan)'})
    agg = {}
    for f in out['files']:
        for k, v in (f.get('components') or {}).items():
            agg[k] = agg.get(k, False) or v
    out['components_union'] = agg
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--inspect')
    ap.add_argument('--ahj'); ap.add_argument('--system')
    ap.add_argument('--job', default='')
    ap.add_argument('--out')
    ap.add_argument('--product', default=None)
    ap.add_argument('--underlayment', default=None)
    ap.add_argument('--fetch-pa', action='store_true')
    for f in REQUIRED_CLIENT_FIELDS:
        ap.add_argument('--' + f, default='')
    a = ap.parse_args()

    if a.inspect:
        print(json.dumps(inspect_folder(a.inspect) if os.path.isdir(a.inspect)
                         else inspect_pdf(a.inspect), indent=2))
        return 0

    if not (a.ahj and a.system and a.out):
        ap.error('--ahj, --system and --out are required when not using --inspect')

    import build
    client = {f: getattr(a, f) for f in REQUIRED_CLIENT_FIELDS}
    rep = {'job': a.job, 'ahj': a.ahj, 'system': a.system,
           'client_in': client, 'ok': False}
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    try:
        build.build_packet(client, a.ahj, a.system, [], a.out,
                           a.underlayment, a.product, fetch_pa=a.fetch_pa)
        rep['ok'] = True
    except Exception as e:
        rep['error'] = '%s: %s' % (type(e).__name__, e)
        rep['traceback'] = traceback.format_exc()[-2000:]
        print(json.dumps(rep, indent=2))
        return 1

    txt, pages = pdf_text(a.out)
    rep.update({'out': a.out, 'bytes': os.path.getsize(a.out), 'pages': pages,
                'components': components_present(txt),
                'blanks': find_blanks(txt, client), 'text_chars': len(txt)})
    print(json.dumps(rep, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
