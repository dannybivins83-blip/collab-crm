# -*- coding: utf-8 -*-
"""Generate coordinate stamp maps for flattened PBC city permit forms using PyMuPDF word boxes.
Writes stamp_maps.py (PBC_STAMP_MAPS) consumed by build.py. Coords are reportlab (bottom-left origin).
Each stamp: (page_index, x, y_baseline, value_key, font_size, max_width)."""
import os, re, glob, fitz, json

LIB = os.environ.get('SEABREEZE_LIB') or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'SeaBreeze_Permit_Library')

# label phrase -> value key. Order matters (first match wins per word-run).
LABELS = [
 ('company',   r'\b(dba\b.*company name|company name|name of firm|firm name|business name|contractor.?s? name|name of contractor|contractor\s*\(|contracting company)\b'),
 ('qualifier', r'\b(cert\.?\s*holder|qualifier(?:.s)? name|name of qualifier|qualifying agent|qualifier(?:.s)? print|q\.?a\.? name)\b'),
 ('license',   r'\b(license\s*(#|no\.?|number)?|lic\.?\s*#|state\s*license|cert(?:ificate)?\s*(#|no\.?))\b'),
 ('addr',      r'\b(job\s*(site)?\s*address|property address|site address|project address|permit address|address of (job|property|jobsite)|location of (work|job)|property location)\b'),
 ('owner',     r'\b(property owner|owner.?s? name|name of owner|owner of record)\b'),
 ('contractor',r"\b(contractor.?s? name and address|contractor name/address)\b"),
]
# keys we are confident to auto-place (constant SeaBreeze values + per-job address/owner)
WANT = {'company','qualifier','license','addr','owner','contractor'}

def line_words(page):
    """Group words into lines: list of (y_mid, [ (x0,y0,x1,y1,text) sorted by x ])."""
    words = page.get_text('words')  # x0,y0,x1,y1,word,block,line,wno
    lines = {}
    for w in words:
        key = (round(w[1]), w[5], w[6])
        lines.setdefault(key, []).append(w)
    out = []
    for k, ws in lines.items():
        ws = sorted(ws, key=lambda w: w[0])
        out.append(ws)
    return out

def analyze(path):
    doc = fitz.open(path)
    stamps = []
    used = set()  # (page,key) only once each (first occurrence)
    for pi in range(len(doc)):
        page = doc[pi]
        H = page.rect.height
        for ws in line_words(page):
            text = ' '.join(w[4] for w in ws).lower()
            for key, rx in LABELS:
                if key in used and (pi, key) in used:
                    pass
                m = re.search(rx, text)
                if not m:
                    continue
                if (pi, key) in used:
                    continue
                # find label's right edge: the word that contains the end of the match
                # approximate: take the x1 of the last word whose char-span overlaps the match end
                # build char offsets
                # right edge of the matched label phrase
                pos = 0; label_x1 = ws[0][2]; label_y1 = ws[0][3]
                for w in ws:
                    seg = (pos, pos + len(w[4]))
                    if seg[1] <= m.end() + 1:
                        label_x1 = w[2]; label_y1 = w[3]
                    pos = seg[1] + 1
                # words to the right of the label on this same line
                after = [w for w in ws if w[0] > label_x1 + 1]
                # SAFETY: if real prose follows the label (a sentence), this is body text, not a field.
                prose = any(re.search(r'[A-Za-z]', w[4]) and w[4].count('_') < 3 for w in after)
                if prose:
                    continue
                # place on the underscore blank if present, else just past the label
                under_x = None
                for w in after:
                    if w[4].count('_') >= 3:
                        under_x = w[0]; label_y1 = max(label_y1, w[3]); break
                fill_x = (under_x + 2) if under_x is not None else (label_x1 + 6)
                y_rl = H - label_y1 + 2           # baseline in reportlab coords
                stamps.append((pi, round(fill_x), round(y_rl), key, 9, 300))
                used.add((pi, key))
    doc.close()
    return stamps

NEEDED = ['BG_01_Building_Permit_Application','BG_02_Reroof_Mitigation','BRB_01_Building_Permit_Packet',
 'DEL_Asbestos_Affidavit','DEL_Notice_of_Commencement','DEL_Roof_Mitigation','GA_01_Building_Permit_Application',
 'GA_02_Re_Roof_Package','HAV_01_Building_Permit_Application','HYP_01_Permit_Application',
 'HYP_02_Roofing_Contractor_Affidavit','JUNO_01_Building_Permit_Application','JIC_01_Building_Permit_Application',
 'JIC_02_Roofing_Contractor_Affidavit','LP_01_Building_Permit_Application','LP_03_Roof_Metal_Sheathing_Affidavit',
 'LP_04_TinTag_Sheathing_Affidavit','LP_05_Notice_of_Commencement','MP_03_Notice_of_Commencement',
 'NPB_01_Building_Permit_Application','NPB_02_Roofing_Inspection_Affidavit','OR_01_Building_Permit_Application',
 'TPB_01_Application_for_Construction_Permit','TPB_02_Roof_Affidavit','PBG_01_Building_Permit_Application']

if __name__ == '__main__':
    maps = {}
    for fn in NEEDED:
        g = glob.glob(os.path.join(LIB, '**', fn + '.pdf'), recursive=True)
        if not g:
            print('  MISSING', fn); continue
        try:
            s = analyze(g[0])
        except Exception as e:
            print('  ERR', fn, e); continue
        if s:
            maps[fn + '.pdf'] = s
        keys = sorted({x[3] for x in s})
        print('  %-45s %2d stamps  keys=%s' % (fn, len(s), ','.join(keys)))
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'stamp_maps.py'), 'w', encoding='utf-8') as fh:
        fh.write('# -*- coding: utf-8 -*-\n# AUTO-GENERATED by gen_stamp_maps.py — coordinate stamp maps for flattened PBC city forms.\n')
        fh.write('# (page_index, x, y_baseline, value_key, font_size, max_width)\n')
        fh.write('PBC_STAMP_MAPS = ' + json.dumps(maps, indent=1) + '\n')
    print('\nwrote stamp_maps.py with', len(maps), 'forms')
