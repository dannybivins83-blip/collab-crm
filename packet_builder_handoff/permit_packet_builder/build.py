# -*- coding: utf-8 -*-
"""Permit Packet Builder - engine (contractor-neutral copy, owned by this repo).
Assembles a single pre-filled permit packet PDF for a client/AHJ/system.

IDENTITY: there is NO house contractor. Every contractor value on a packet comes from the
`contractor` profile the caller passes (the customer's own worksheet). The legally
identifying fields (company, license, qualifier) fail closed when missing; every other
missing value is left BLANK and reported (see build_packet_report / LAST_REPORT), never
filled with a default.

COMPLETENESS: build_packet() records every required form or value it could not fill and,
in strict mode (default), raises PacketIncomplete instead of returning a silently
incomplete packet. build_packet_report() returns the packet plus the explicit list."""
import os, io, re, subprocess, tempfile
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor, white
from reportlab.pdfbase.pdfmetrics import stringWidth

# Locate the form library: PERMIT_LIB env var (the worker sets it), else a sibling folder of
# this engine whose name ends in "Permit_Library" (the office copy keeps a legacy name
# prefix, so it is found by suffix and no company name is written here).
_HERE = os.path.dirname(os.path.abspath(__file__))


def _find_lib():
    env = os.environ.get('PERMIT_LIB')
    if env:
        return env
    for base in (os.path.dirname(_HERE), os.path.dirname(os.path.dirname(_HERE))):
        try:
            names = sorted(d for d in os.listdir(base) if d.endswith('Permit_Library')
                           and os.path.isdir(os.path.join(base, d)))
        except OSError:
            names = []
        if names:
            return os.path.join(base, 'Permit_Library' if 'Permit_Library' in names else names[0])
    return os.path.join(os.path.dirname(_HERE), 'Permit_Library')


LIB = _find_lib()
MB   = os.path.join(LIB,'Palm_Beach_County','PBC Municipalities')
PBCPK= os.path.join(LIB,'Palm_Beach_County','01_Permit_Application_Packet')
PA   = os.path.join(LIB,'Product_Approvals_Library')
SPEC = os.path.join(LIB,'_System_Spec_Sheets')
# Broward County (HVHZ). The library's `_Prefilled` folders carry ANOTHER company's
# identity baked into flattened copies, so this engine never reads them (no BPK).
BLIB = os.path.join(LIB,'Broward_County')
BMB  = os.path.join(BLIB,'Broward Municipalities')
BPA  = os.path.join(BLIB,'Product_Approvals_Library')
BSPEC= os.path.join(BLIB,'_System_Templates')
_PA_SUB = {'Shingle':'Shingle','Tile':'Tile','Metal':'Metal_Roof','Flat':'Flat_Roof'}

# --- PBC county roofing forms: exactly ONE per packet, keyed to the roof system ---
# Until 2026-07-20 the library held only Form 200 (Tile), and the step-4b folder loop
# attached EVERY pdf it found with no system filter — so a shingle/metal/flat job in an
# AHJ that stocked the tile form shipped with the TILE form stapled on. Forms 100/300/400
# were sourced from the county (discover.pbcgov.org) and installed; this map + the skip in
# step 4b make the choice explicit instead of "whatever is in the folder".
_PBC_ROOF_FORM = {
    'Shingle': 'PBC_04_Shingle_Roofing_Form100.pdf',
    'Tile':    'PBC_04_Concrete_Clay_Tile_Roofing_Form200.pdf',
    'Metal':   'PBC_04_Metal_Roofing_Form300.pdf',
    'Flat':    'PBC_04_Flat_Roofing_Form400.pdf',
}
# Form number -> system. 100 Shingle / 200 Tile / 300 Metal / 400 Flat (county numbering).
_ROOF_FORM_NUM = {'100':'Shingle','200':'Tile','300':'Metal','400':'Flat'}
_ROOF_FORM_WORD = {'shingle':'Shingle','tile':'Tile','metal':'Metal','flat':'Flat'}


def roof_form_system(fn):
    """Which roof system a county roofing-form filename belongs to, else None.
    Matches both the county names (PBC_04_Metal_Roofing_Form300.pdf) and the
    city variants (PBG_Form200_Concrete_Clay_Tile_Roofing.pdf, NPB_03_Roofing_Form_200_Tile.pdf)."""
    s = (fn or '').replace(' ', '_')
    # NB: no \b after the digits — these filenames continue with '_' (a word char), so
    # \b would never match e.g. 'PBG_Form200_Concrete_Clay_Tile_Roofing.pdf'.
    m = re.search(r'Form_?([1234])00(?!\d)', s, re.I)
    if m:
        return _ROOF_FORM_NUM.get(m.group(1) + '00')
    m = re.search(r'_(shingle|tile|metal|flat)_roofing_form', s, re.I)
    if m:
        return _ROOF_FORM_WORD.get(m.group(1).lower())
    return None
NAVY=HexColor('#1a3a5c'); BLUE=HexColor('#4a90b8'); INK=HexColor('#10357a'); RED=HexColor('#c00000')

CONST_UNI={}   # no house contractor values: identity comes ONLY from the caller's contractor profile
# Type of Work: the county universal application offers New / Addition / Alteration / Repair /
# Demo / Temporary / Other - there is no "Reroof" box. "New" is wrong on a reroof (2026-11-18
# review; two approved samples ticked it out of habit, Greenacres R-25005 left it blank and wrote
# "Re-Roof" in the description). We tick OTHER and the Further Work Description says exactly what
# the work is ("REROOF (ROOF REPLACEMENT) - ...").
CONST_UNI_B={'Trade':'Roofing','Owner Builder or Contractor':'Contractor/Qualifier','Kind of Permit (Select One)':'Primary Permit',
 'USE ( Select One)':'1 & 2 Family','Type of Work':'Other','bonding-company-not-applicable':'Yes','design-professional-not-applicable':'Yes','mortgage-lender-not-applicable':'Yes'}

# Contractor fields that are not identity and not job data (applied to every fillable form).
# The contractor's own values are layered on by _contractor_overrides(); nothing else.
BASE_FILL={'company-suite':'','company-fax':'','bonding-company-not-applicable':'Yes'}
_ADDR_KEYS=['Job Address','Site Address','SITE ADDRESS','PROJECT ADDRESS','Property address','PROPERTY ADDRESS','Property Address','Project Address','Permit Address']
_OWNER_KEYS=['Property Owner','Owners Name','OwnersName','Owner Name','OwnerName','Owner print name','Name of Owner','Printed_Name_of_Property_Owner','Owner print']

# Kept only so an older worker loader that clears `_SB` keeps working. It is empty and unused.
_SB = {}

# Legally identifying fields. These may NEVER fall back to any other company's values:
# putting one contractor's certificate/qualifier on another's permit application is
# prohibited by Fla. Stat. s.489.127(1)(a),(c). Fail closed.
_IDENTITY_KEYS = {'name','display','name_title','name_short',
                  'license','license_dashes','qualifier'}
REQUIRED_IDENTITY = ('company_name', 'license_number', 'qualifier_name')


class IdentityMissing(ValueError):
    """The customer's own contractor identity is incomplete. Never build with another's."""
    def __init__(self, missing):
        self.missing = list(missing)
        ValueError.__init__(self, "packet_builder: refusing to build - missing contractor %s. "
                            "Identity fields never fall back to another company. Fill the "
                            "contractor profile (company_name, license_number, qualifier_name) "
                            "and re-run." % ', '.join(self.missing))


def identity_missing(contractor):
    """The REQUIRED_IDENTITY keys the profile lacks ([] = complete)."""
    c = contractor or {}
    return [k for k in REQUIRED_IDENTITY if not str(c.get(k) or '').strip()]


# PACKET MODES (the customer's choice, p.packet.mode -> job params `mode`):
#   'ask'   (default) prebuilt draft + questions until complete, then the final packet.
#   'asis'  "fill in what you have": build now with whatever data exists. Unknown values stay BLANK
#           (never guessed), no questions, and the checklist lists every blank for the customer.
#           A missing company / license / qualifier is left BLANK too - never filled with any
#           other company's identity (fail closed still means: no house or default identity).
#   'blank' "just the blank forms": the right set of government forms for the AHJ, applicant type
#           and roof system(s), with nothing stamped in; a cover + checklist say what each is for.
MODES = ('ask', 'asis', 'blank')
_MODE = ['ask']
APPLICANT = ['contractor']      # 'contractor' | 'owner_builder' (owner-builder only in 'blank' mode)


def _identity_missing(key, who):
    if _MODE[0] in ('asis', 'blank'):
        _need('identity_' + key, {'company_name': 'Contractor company name', 'license_number': 'Contractor license number',
                                  'qualifier_name': 'Qualifier name'}.get(key, key),
              'not provided; left blank on every form', 'customer', None, rank=0)
        return ''
    raise IdentityMissing([key])


# --- Completeness report ------------------------------------------------------------
# Every value or form the engine could not fill is recorded here during a build and
# returned by build_packet_report(). who = 'customer' (the customer can answer / upload)
# or 'staff' (only staff can fix: a government blank missing from our library, a PE item).
_REPORT = {'missing': [], 'confirm': [], 'pe': [], 'forms': [], 'hand': [], 'nofield': [], 'dropped': []}


def _rep_reset():
    for k in _REPORT:
        _REPORT[k] = []


def _need(key, item, why, who='customer', question=None, rank=50):
    """Record a required value/form that is NOT on the packet."""
    for m in _REPORT['missing']:
        if m['key'] == key:
            return
    _REPORT['missing'].append({'key': key, 'item': item, 'why': why, 'who': who,
                               'question': question, 'rank': rank})


def _confirm(text):
    if text not in _REPORT['confirm']:
        _REPORT['confirm'].append(text)


def _hand(text):
    if text not in _REPORT['hand']:
        _REPORT['hand'].append(text)


_CONTRACTOR_Q = {
    'address': ("Contractor business street address", "Your business street address (as on your license)"),
    'city': ("Contractor business city", "The city of your business address"),
    'zip': ("Contractor business ZIP", "The ZIP code of your business address"),
    'phone': ("Contractor phone", "Your business phone number"),
    'email': ("Contractor e-mail", "Your business e-mail address"),
}


def _C(contractor, key):
    """Semantic contractor field accessor.

    Identity fields fail closed (IdentityMissing). Every other field returns the
    contractor's own value or '' - never a default - and a blank is reported."""
    c = contractor or {}
    if key in ('name','display'):
        return (c.get('company_name') or _identity_missing('company_name','this job') or '').upper()
    if key in ('name_title','name_short'):
        return c.get('company_name') or _identity_missing('company_name','this job')
    if key=='license':
        return c.get('license_number') or _identity_missing('license_number','this job')
    if key=='license_dashes':
        l=c.get('license_number') or _identity_missing('license_number','this job') or ''
        return l[:3]+'-'+l[3:] if '-' not in l and len(l)>=10 else l
    if key=='qualifier':
        return c.get('qualifier_name') or _identity_missing('qualifier_name','this job')
    src={'address':'address','address_long':'address','city':'city','state':'state','zip':'zip',
         'phone':'phone','email':'email','contact_person':'contact_person'}.get(key)
    if src:
        v=c.get(src) or ('FL' if src=='state' else '')
        if not v and src in _CONTRACTOR_Q:
            item,q=_CONTRACTOR_Q[src]
            _need('contractor_'+src, item, 'printed on the application / NOC contractor block', 'customer', q, rank=12)
        return v
    if key=='notary_county':
        v=c.get('notary_county') or c.get('county') or ''
        if not v:
            _hand('Notary county (the "County of" line on sworn forms) - left blank; the notary completes it.')
        return v
    return ''


def _contractor_overrides(contractor):
    """Contractor-profile values keyed by every known AcroForm field-name variant."""
    if not contractor:
        return {}
    c=contractor
    nm=c.get('company_name') or ''
    lic=c.get('license_number') or ''
    qual=c.get('qualifier_name') or ''
    phone=c.get('phone') or ''
    email=c.get('email') or ''
    addr=c.get('address') or ''
    city=c.get('city') or ''
    state=c.get('state') or 'FL'
    zip_=c.get('zip') or ''
    contact=c.get('contact_person') or ''
    notary=c.get('notary_county') or ''
    nm_u=nm.upper() if nm else ''
    addr_full=', '.join(p for p in [addr, city, (state+' '+zip_).strip()] if p)
    addr_phone=(addr_full+'  '+phone).strip() if phone else addr_full
    ov={}
    if nm:
        for k in ['Company Name','company-name','DBA COMPANY NAME','Company of designee','Name of Firm']:
            ov[k]=nm_u
        for k in ['Contractors Name',"CONTRACTOR'S NAME",'4  a CONTRACTORS NAME']:
            ov[k]=nm
        for k in ['CONTRACTOR Name & Address','4 a CONTRACTOR Name  Address',
                  '4 CONTRACTOR - Name  Address']:
            ov[k]=', '.join(p for p in [nm, addr_full] if p)
        for k in ['Contractors address','Firm Mailing Address']:
            if addr_full: ov[k]=addr_full
        ov['4 CONTRACTORS NAME ADDRESS AND PHONE NUMBER 1']=', '.join(p for p in [nm, addr_phone] if p)
    if lic:
        for k in ['License','License-number','License Number','CONTRACTOR LICENSE']:
            ov[k]=lic
    if qual:
        for k in ['Contractor-cert-holder','CONTRACTOR CERT HOLDER','contractor-printed-name',
                  'NAME OF QUALIFIER','Qualifier Name','Qualifiers Name','Qualifier print name',
                  'Qualifier Name Printed','Qualifier or OwnerBuilder Name Print',
                  'QUALIFIER NAME','Name of Qualifier','Contractor Qualifying Agent Name']:
            ov[k]=qual
    if phone:
        for k in ['company-phone','Contractor Phone number']:
            ov[k]=phone
    if email: ov['company-email']=email
    if addr:  ov['contractor-address']=addr
    if city:  ov['company-city']=city
    if state: ov['company state']=state
    if zip_:  ov['company-zip']=zip_
    if contact: ov['contact-person']=contact
    if notary: ov['contractor-notary-county']=notary
    return {k:v for k,v in ov.items() if v}

def _tenant_short(contractor):
    """Short contractor name for reviewer-annotation topnotes (NOT a legal form field)."""
    if contractor and contractor.get('company_name'):
        return contractor['company_name']
    return 'Contractor'

# DETECTION-ONLY. Tokens of a previous library author's identity that are baked into some
# STATIC library PDFs (system templates, annotated NOAs). They exist only so the engine can
# redact them before such a page is bound into a customer's packet; the delivering worker's
# leak scan then blocks the packet if any survive. Never written onto a packet.
_FOREIGN_TOKENS = ('SeaBreeze', 'SEABREEZE', 'Seabreeze', 'Sea Breeze', 'SEA BREEZE')


def _strip_foreign_text(path, contractor):
    """Redact foreign-identity tokens from a STATIC library PDF's text layer. Returns a
    sanitized temp copy, or the original path when there is nothing to redact. If the
    redaction itself fails the original path is returned and the delivery leak scan
    blocks the packet (fail closed at the gate)."""
    try:
        import fitz
    except Exception:
        return path
    try:
        d=fitz.open(path); hit=False
        for pg in d:
            rects=[]
            for variant in _FOREIGN_TOKENS:
                rects+=pg.search_for(variant)
            for r in rects:
                pg.add_redact_annot(r, fill=(1,1,1)); hit=True
            if rects:
                pg.apply_redactions()
        if not hit:
            d.close(); return path
        out=os.path.join(tempfile.gettempdir(),'wl_'+os.path.basename(path))
        d.save(out, garbage=3); d.close()
        return out
    except Exception:
        return path

def contractor_fill_T(client, ahj, contractor=None):
    """Full contractor + this-job fill dict for any fillable form (matched by field name)."""
    city=_city(client, ahj)
    addr=', '.join([s for s in [client.get('address',''), city, ('FL '+client.get('zip','')) if client.get('zip','') else ''] if s])
    T=dict(BASE_FILL)
    if contractor: T.update(_contractor_overrides(contractor))
    for k in _ADDR_KEYS: T[k]=addr
    for k in _OWNER_KEYS: T[k]=client.get('owner','')
    return T

SYSTEMS={
 'Shingle':{'desc':'REROOF - ASPHALT SHINGLE','mfr':'Owens Corning','prodfl':'FL10674','prod':'TruDefinition Duration architectural shingle',
   'ul':'Polystick IR-XE','ulfl':'FL5259','box':'Asphalt Shingles','swb_box':'1 The entire roof deck','swb':'Polystick IR-XE',
   'approvals':['Shingle/Shingle_OwensCorning_TruDef_Duration_FL10674-R20.pdf']},
 # 'box' = the roof-covering checkbox on the permit forms. the reference installer sets tile ADHESIVE-SET
 # with ICP Polyset AH-160 (NOA 22-0411.02, attached in every tile packet), so the adhesive/
 # mortar-set box is the correct tick. Confirmed by the owner 2026-07-21 — the Broward
 # Section A tick had been reading "Mechanically Fastened Tile", which contradicted the
 # Polyset approval sitting in the same submittal.
 'Tile':{'desc':'REROOF - CONCRETE TILE','mfr':'Westlake Royal','prodfl':'FL7849','prod':'Saxony 900 flat concrete tile',
   'ul':'Polystick TU Plus','ulfl':'FL5259','box':'Mortar/Foam Set tile','swb_box':'2 Clay and concrete tile','swb':'Polystick TU Plus',
   'attach':'adhesive',   # adhesive-set, NOT mechanically fastened
   'adh':'ICP / Polyset AH-160','adhfl':'FL6332',
   'approvals':['Tile/Tile_Westlake_Saxony900_FL7849-ER412.pdf','Tile/Tile_Adhesive_Polyset_AH-160_FL6332-R13.pdf','Tile/Tile_Underlayment_Polyglass_TU-Plus_FL5259-R50.pdf']},
 'Metal':{'desc':'REROOF - STANDING SEAM METAL','mfr':'Dynamic Metals','prodfl':'FL41724.08','prod':'DM Class 1500, 1.5" Mech Seam, 16" wide',
   'ul':'Polystick MTS','ulfl':'FL5259','box':'Metal Panel/shingle','swb_box':'1 The entire roof deck','swb':'Polystick MTS',
   'approvals':['Metal_Roof/Metal_Dynamic_Class1500_26ga_FL41724-R3_Submittal.pdf','Metal_Roof/Metal_Underlayment_2ply_MTS_FL5259-R50.pdf']},
 'Flat':{'desc':'REROOF - FLAT / MOD-BIT','mfr':'Polyglass','prodfl':'FL1654','prod':'SA 2-ply self-adhered modified bitumen',
   'ul':'Polyglass SA 2-ply Mod-Bit','ulfl':'FL1654','box':'Flat roof','swb_box':'1 Roof slopes 212','swb':'Polyglass SA 2-ply Mod-Bit FL1654',
   # Governing listing inside FL1654-R44 for the the reference installer 2-ply SA assembly over wood deck:
   # Table 1K (Wood Deck, Non-Insulated, Bonded Roof Cover) System W-179 — min 15/32" APA CDX,
   # SBS-SA base (Elastobase SA), APP-SA cap (Polyflex SA P FR), fully self-adhered, MDP -90.0 psf.
   'fl_system':'W-179','fl_mdp':'-90.0 psf',
   'hl':{'token':'W-179','note':'THIS PROJECT: FL1654 Table 1K System W-179 (highlighted inside) — '
         'wood deck, non-insulated, Elastobase SA base + Polyflex SA P FR cap, fully self-adhered. MDP -90.0 psf.'},
   'approvals':['Flat_Roof/Flat_Polyglass_SA_ModBit_FL1654-R44_NonHVHZ_AE.pdf']},
}

# Underlayment options per system: choice -> (label, FL#, product-approval relative path).
UNDERLAYMENTS={
 'Shingle':{'single':('Polystick IR-XE','FL5259','Shingle/Shingle_Underlayment_Polyglass_IR-XE_FL5259-R50_NonHVHZ.pdf')},
 'Tile':{'single':('Polystick TU Plus (1-ply)','FL5259','Tile/Tile_Underlayment_Polyglass_TU-Plus_FL5259-R50.pdf'),
         '2ply':('Polystick TU-Plus + MTS (2-ply)','FL5259','Tile/Tile_Underlayment_2ply_TU-Plus+MTS_FL5259-R50.pdf')},
 'Metal':{'single':('Polystick MTS','FL1654','Metal_Roof/Metal_Underlayment_Polyglass_MTS_FL1654-R41.pdf'),
          '2ply':('Polystick MTS (2-ply)','FL5259','Metal_Roof/Metal_Underlayment_2ply_MTS_FL5259-R50.pdf')},
 'Flat':{'single':('Polyglass SA 2-ply Mod-Bit','FL1654','Flat_Roof/Flat_Polyglass_SA_ModBit_FL1654-R44_NonHVHZ_II.pdf')},
}
DEFAULT_UL={'Shingle':'single','Tile':'single','Metal':'single','Flat':'single'}
def ul_choices(system):
    return list(UNDERLAYMENTS.get(system,{}).keys())

# Primary roof-covering product options per system: choice -> (label, mfr, product, FL#, approval rel path).
# Lets the wizard pick the manufacturer (e.g. Owens Corning vs GAF shingle, Westlake vs Eagle tile).
PRODUCTS={
 'Shingle':{
   'oc': ('Owens Corning TruDefinition Duration','Owens Corning','TruDefinition Duration architectural shingle','FL10674','Shingle/Shingle_OwensCorning_TruDef_Duration_FL10674-R20.pdf'),
   'gaf':('GAF Timberline HDZ','GAF','Timberline HDZ architectural shingle','FL10124','Shingle/Shingle_GAF_Shingles_FL10124-R35.pdf'),
 },
 # Tile profiles. Every entry below is backed by an approval PDF already in the library —
 # the approval number was read OUT of that PDF, never assumed. Added 2026-07-21: Barcelona
 # 900 plus the three Eagle profiles, whose approvals were on disk but had no way to be
 # selected, so every tile job was forced onto Saxony 900 regardless of what was sold.
 #
 # Barcelona 900 carries a Miami-Dade NOA only — no statewide Florida Product Approval
 # number appears in the document, so the NOA number is what goes on the form. Do not
 # invent an "FL" number for it.
 'Tile':{
   'westlake':  ('Westlake Royal Saxony 900 (flat/slate)','Westlake Royal','Saxony 900 flat concrete tile','FL7849','Tile/Tile_Westlake_Saxony900_FL7849-ER412.pdf'),
   'barcelona': ('Westlake Royal Barcelona 900 (S-tile/barrel)','Westlake Royal','Barcelona 900 S-profile concrete tile','NOA 24-1008.13','Tile/Tile_Westlake_Barcelona900_NOA_24-1008.13.pdf'),
   'eagle':     ('Eagle Concrete Tile (statewide)','Eagle','Eagle concrete tile','FL7473','Tile/Tile_Eagle_ConcreteTile_FL7473-R11_NonHVHZ.pdf'),
   'eagle_high':('Eagle High Profile (barrel)','Eagle','Eagle High Profile concrete tile','NOA 24-1008.07','Tile/Tile_Eagle_HighProfile_MiamiDade_NOA_24-1008.07.pdf'),
   'eagle_med': ('Eagle Medium Profile','Eagle','Eagle Medium Profile concrete tile','NOA 25-0313.04','Tile/Tile_Eagle_MediumProfile_MiamiDade_NOA_25-0313.04.pdf'),
   'eagle_low': ('Eagle Low Profile (flat)','Eagle','Eagle Low Profile concrete tile','NOA 25-0313.05','Tile/Tile_Eagle_LowProfile_MiamiDade_NOA_25-0313.05.pdf'),
 },
}

# Approval expiry, read off each document. The builder warns on the cover when the chosen
# product's approval is expired or within 60 days of it — a lapsed approval is a rejection.
PRODUCT_EXPIRY={
 'barcelona':'2027-04-26', 'westlake':'2029-05-02',
 'eagle_high':'2026-10-05', 'eagle_med':'2026-10-05', 'eagle_low':'2026-10-05',
}
DEFAULT_PRODUCT={'Shingle':'oc','Tile':'westlake'}
def prod_choices(system):
    return list(PRODUCTS.get(system,{}).keys())
def _apply_product(sysd, system, product):
    """Swap the chosen primary roof-covering product (mfr / product / FL# + its approval PDF)
    into an already-built system dict. Underlayment & adhesive approvals are preserved.
    index 0 of 'approvals' is always the primary product approval. Returns the sysd."""
    pmap=PRODUCTS.get(system,{})
    if not pmap: return sysd
    choice=product if product in pmap else DEFAULT_PRODUCT.get(system,'')
    p=pmap.get(choice)
    if not p: return sysd
    label,mfr,prod,prodfl,appr=p
    sysd=dict(sysd); sysd['approvals']=list(sysd.get('approvals',[]))
    sysd['mfr'],sysd['prod'],sysd['prodfl'],sysd['product_key']=mfr,prod,prodfl,choice
    if sysd['approvals']: sysd['approvals'][0]=appr
    else: sysd['approvals']=[appr]
    return sysd

def parse_roofgraf(path):
    """Pull roof area (sf), squares, and predominant pitch from a RoofGraf
    'roof report' PDF so the wizard doesn't need them typed in by hand.
    Returns {} for anything that isn't a RoofGraf report (no false positives)."""
    out={}
    try:
        r=PdfReader(path); txt='\n'.join((p.extract_text() or '') for p in r.pages[:4])
    except Exception:
        return out
    if 'roofgraf' not in txt.lower() and 'SQ before waste' not in txt and 'Predominant pitch' not in txt:
        return out
    # base (no-waste) roof area in sq.ft = first number under the "Area (sq.ft)" header
    m=re.search(r'Area\s*\(sq\.?\s*ft\.?\)\s*[\r\n]+\s*([\d][\d,]*\.?\d*)', txt, re.I)
    if m:
        try: out['area']='{:,}'.format(int(round(float(m.group(1).replace(',','')))))
        except Exception: pass
    # the "SQ before waste / Predominant pitch / ..." labels are followed by their values in order
    if 'SQ before waste' in txt:
        blk=txt[txt.find('SQ before waste'):txt.find('SQ before waste')+400]
        nums=re.findall(r'-?\d+\.?\d*', blk)
        if nums:
            out['squares']=nums[0]
            if len(nums)>1: out['pitch']=nums[1].split('.')[0]   # whole-number x/12
    if 'area' not in out and out.get('squares'):
        try: out['area']='{:,}'.format(int(round(float(out['squares'])*100)))
        except Exception: pass
    return out

# --- Roof measurement reports (RoofGraf, Roofr, EagleView, ...) ----------------------
# The customer's uploaded measurement report is bound into the packet (the approved permits carry
# it: Wellington R-26004 p5, R-25060 p25-30) and its PITCH TABLE tells us when a job has a second
# roof system: RoofGraf prints "Pitch / Area (SQ) / % of Roof", e.g. 6/12 33.72 90.86 + 0/12 3.39
# 9.14 for R-26004 - a shingle roof with a 3.39-square flat section, which is exactly the
# shingle + flat permit the Village approved.
_ROOF_REPORT_MARKS = (('roofgraf', 'RoofGraf'), ('roofr', 'Roofr'), ('eagleview', 'EagleView'),
                      ('hover inc', 'Hover'), ('quickmeasure', 'GAF QuickMeasure'), ('roofsnap', 'RoofSnap'),
                      ('pictometry', 'EagleView'), ('nearmap', 'Nearmap'))
FLAT_PITCH_MAX = 2.0          # under 2/12 is a low-slope / flat section (FBC low-slope threshold)
FLAT_MIN_SQ = 0.5             # smaller than half a square is a trace artifact, not a roof section


def roof_report_info(path):
    """{} unless `path` is a roof measurement report. Otherwise:
        {'provider', 'pitches': [(rise, squares, pct)], 'steep_sq', 'flat_sq', 'pitch' (predominant
         steep rise), 'area' (total sf, from the report's own total when it prints one)}"""
    try:
        r = PdfReader(path)
        txt = '\n'.join((pg.extract_text() or '') for pg in r.pages[:12])
    except Exception:
        return {}
    low = txt.lower()
    prov = next((name for mark, name in _ROOF_REPORT_MARKS if mark in low), None)
    if not prov:
        return {}
    out = {'provider': prov, 'pitches': []}
    # "6/12 33.72 90.86" triples (RoofGraf's Pitch / Area (SQ) / % of Roof table)
    i = low.find('pitch')
    seg = txt[i:] if i >= 0 else txt
    for m in re.finditer(r'(?<![\d.])(\d{1,2}(?:\.\d+)?)\s*/\s*12\s+([\d,]+\.?\d*)\s+(\d{1,3}(?:\.\d+)?)(?![\d/])', seg):
        try:
            rise, sq, pct = float(m.group(1)), float(m.group(2).replace(',', '')), float(m.group(3))
        except ValueError:
            continue
        if pct > 100 or sq <= 0:
            continue
        if any(abs(rise - a) < 1e-9 and abs(sq - b) < 1e-9 for a, b, _ in out['pitches']):
            continue
        out['pitches'].append((rise, sq, pct))
    if out['pitches']:
        flat = sum(sq for rise, sq, _ in out['pitches'] if rise < FLAT_PITCH_MAX)
        steep = sum(sq for rise, sq, _ in out['pitches'] if rise >= FLAT_PITCH_MAX)
        out['flat_sq'] = round(flat, 2)
        out['steep_sq'] = round(steep, 2)
        st = [(sq, rise) for rise, sq, _ in out['pitches'] if rise >= FLAT_PITCH_MAX]
        if st:
            out['pitch'] = ('%g' % max(st)[1])
        out['area'] = '%d' % round((flat + steep) * 100)
    g = parse_roofgraf(path)
    if g.get('area') and 'area' not in out:
        out['area'] = g['area'].replace(',', '')
    if g.get('pitch') and 'pitch' not in out:
        out['pitch'] = g['pitch']
    return out


def list_ahjs():
    out=[]
    for base in (MB, BMB):
        if os.path.isdir(base):
            for a in os.listdir(base):
                if os.path.isdir(os.path.join(base,a)): out.append(a)
    return sorted(set(out))

def _is_broward(ahj):
    return bool(ahj) and os.path.isdir(os.path.join(BMB, ahj))

# --- Unincorporated Palm Beach County -----------------------------------------
# Added 2026-07-21. A parcel that is not inside any of the 39 PBC municipalities is
# permitted by the PALM BEACH COUNTY BUILDING DIVISION (2300 N. Jog Road, West Palm
# Beach FL 33411) — even though its MAILING address says "Boca Raton", "Lake Worth",
# "WPB" etc. (verified on the approved unincorporated submittals R-25216 Marrero and
# R-25183 Rothstein; the PBC Property Appraiser record for R-25216 literally reads
# "Municipality: UNINCORPORATED" while the mailing city is West Palm Beach).
#
# The AHJ slug is 'Palm_Beach_County'. TWO string rules follow from that:
#   1. Anywhere the AHJ is PRINTED as a jurisdiction it must read "Unincorporated
#      Palm Beach County" — not the raw slug, which would render the cover line as
#      "Palm Beach County, Palm Beach County".
#   2. Anywhere the AHJ is used as a CITY fallback it must NOT be substituted at all.
#      The city on a permit application is the property's mailing city (Boca Raton for
#      R-26057 Goren); stamping "Palm Beach County" into a CITY box would be wrong on
#      the face of the document. The approved Marrero application shows the mailing
#      city "WPB" in both the owner-city and project-city boxes.
UNINCORPORATED_PBC = 'Palm_Beach_County'


def _is_unincorporated_pbc(ahj):
    return ahj == UNINCORPORATED_PBC


def ahj_label(ahj):
    """The AHJ as it should PRINT (cover page, jurisdiction line)."""
    if _is_unincorporated_pbc(ahj):
        return 'Unincorporated Palm Beach County'
    return (ahj or '').replace('_', ' ')


def _city(client, ahj):
    """The CITY to stamp on a form. Always the property's mailing city; the AHJ slug is
    only a last-resort fallback for municipal AHJs, where city == jurisdiction. For
    unincorporated county jobs there is no such equivalence, so we never fall back."""
    c = (client.get('city', '') or '').strip()
    if c:
        return c
    if _is_unincorporated_pbc(ahj):
        return ''
    return (ahj or '').replace('_', ' ')

LIB_PRESENT = os.path.isdir(MB)

def _wstate(o):
    parent=o.get('/Parent'); name=o.get('/T')
    if name is None and parent is not None: name=parent.get_object().get('/T')
    ft=o.get('/FT')
    if ft is None and parent is not None: ft=parent.get_object().get('/FT')
    on=None; ap=o.get('/AP')
    if ap is not None and '/N' in ap:
        for k in ap['/N'].get_object().keys():
            if k!='/Off': on=str(k)[1:]
    return (str(name) if name else None),(str(ft) if ft else None),on

def fill_form(src, T, B, topnote=None, stamps=None):
    """Overlay-fill + flatten an AcroForm PDF. Returns list of pypdf pages.
    stamps: optional [(page_index, x, y_bottom_origin, text, font_size)] for marks the form
    has no widget for (e.g. Boca_06's 'free draining ___Yes' is a bare underscore line)."""
    _REPORT['forms'].append(os.path.basename(src))
    if _MODE[0]=='blank':
        T,B,stamps={},{},None       # "just the blank forms": nothing is stamped in
    r=PdfReader(src)
    names=set()
    for pg in r.pages:
        for a in (pg.get('/Annots') or []):
            n,_,_=_wstate(a.get_object())
            if n: names.add(n)
    def res(d):
        o={}
        for w,v in d.items():
            if w in names: o[w]=v
            else:
                m=[k for k in names if k.startswith(w[:26])]
                if m: o[m[0]]=v
        return o
    T=res(T); B=res(B)
    w=PdfWriter()
    for pi,pg in enumerate(r.pages):
        W=float(pg.mediabox.width); H=float(pg.mediabox.height)
        buf=io.BytesIO(); c=canvas.Canvas(buf,pagesize=(W,H))
        for a in (pg.get('/Annots') or []):
            o=a.get_object()
            if o.get('/Subtype')!='/Widget': continue
            name,ft,on=_wstate(o); rect=o.get('/Rect')
            if not rect: continue
            x0,y0,x1,y1=[float(v) for v in rect]; rw=abs(x1-x0); rh=abs(y1-y0)
            x0,x1=min(x0,x1),max(x0,x1); y0,y1=min(y0,y1),max(y0,y1)
            try:
                if ft=='/Tx' and name in T and str(T[name]).strip():
                    val=str(T[name]).encode('latin-1','replace').decode('latin-1'); fs=min(9.0,rh-2.5) if rh>6 else 7.0
                    while fs>5 and stringWidth(val,'Helvetica',fs)>rw-3: fs-=0.5
                    c.setFillColor(INK); c.setFont('Helvetica',fs); c.drawString(x0+2,y0+(rh-fs)/2+1,val)
                elif ft=='/Btn' and name in B and str(B[name]).strip() and (on is None or _on_norm(B[name])==_on_norm(on)):
                    # on is None when the widget has no /AP on-state (some flattened-ish forms,
                    # e.g. the PBC Roof Spec Sheet checkboxes); still mark it when explicitly requested.
                    fs=min(11.0,rh+1); c.setFillColor(INK); c.setFont('Helvetica-Bold',fs); c.drawCentredString((x0+x1)/2,y0+(rh-fs*0.7)/2,'X')
            except Exception: pass
        for _stp in (stamps or []):
            # (page, x, y, text, size) or (page, x, y, text, size, max_width): city-map stamps carry a
            # max width, and the font shrinks (to 5 pt) until the text fits it.
            sp,sx,sy,st,sfs=_stp[:5]
            if sp==pi:
                st=str(st).encode('latin-1','replace').decode('latin-1'); sfs=float(sfs)
                _mw=float(_stp[5]) if len(_stp)>5 and _stp[5] else None
                while _mw and sfs>5 and stringWidth(st,'Helvetica-Bold',sfs)>_mw: sfs-=0.5
                c.setFillColor(INK); c.setFont('Helvetica-Bold',sfs); c.drawString(sx,sy,st)
        # NO topnote is printed on a government form any more (2026-11-18 review: a red
        # "pre-filled" banner does not belong on anything the building department receives).
        # Callers put anything a person must act on into the checklist (_hand / _confirm).
        c.showPage(); c.save(); buf.seek(0)
        try: pg.merge_page(PdfReader(buf).pages[0])
        except Exception: pass
        # True flatten: drop the interactive widget annotations now that values are stamped into
        # the page content. Otherwise empty checkbox widgets render ON TOP of the stamped 'X'
        # (hiding it) and a viewer could still edit the fields. Text overlays are unaffected.
        try:
            if '/Annots' in pg: del pg[NameObject('/Annots')]
        except Exception: pass
        w.add_page(pg)
    bio=io.BytesIO(); w.write(bio); bio.seek(0)
    return PdfReader(bio).pages

def _work_description(pairs):
    """Further Work Description for the permit application: the scope in plain words, one entry per
    roof system with its area, e.g. 'REROOF (ROOF REPLACEMENT) - ASPHALT SHINGLE 33.7 SQ + FLAT / MOD-BIT 3.4 SQ'."""
    parts=[]
    for s,d,a in pairs:
        what=d.get('desc','').replace('REROOF - ','')
        sq=_sqrs(a)
        parts.append('%s%s'%(what,(' %s SQ'%sq) if sq else ''))
    return 'REROOF (ROOF REPLACEMENT) - '+' + '.join(parts)


def _pcn_digits(pcn):
    return ''.join(ch for ch in (pcn or '') if ch.isdigit())[:17]


def pcn_display(pcn):
    """A Palm Beach County parcel control number in the county's own printed format,
    00-00-00-00-00-000-0000 (2-2-2-2-2-3-4 = 17 digits; e.g. 73-41-44-11-01-028-0040 as on
    the Property Appraiser record). Anything that is not a 17-digit PCN (a Broward folio,
    a partial number) is returned exactly as given - never reformatted by guesswork."""
    raw=str(pcn or '').strip()
    d=''.join(ch for ch in raw if ch.isdigit())
    if len(d)!=17: return raw
    return '-'.join((d[0:2],d[2:4],d[4:6],d[6:8],d[8:10],d[10:13],d[13:17]))


# =============================================================================
# PERMIT VALUE SETTING (owner decision) - the ONE place the declared valuation is set.
# =============================================================================
# Every permit application / NOC / city form in a packet prints the declared job value as
#     contract price x VALUE_FACTOR
# 1.0 = the FULL contract price (the owner's decision, 2026-11-18 evening).
# 0.9 = "contract price less 10%" (the previous practice). Several cities ask for the TOTAL
#       replacement value of all work, materials and labor (e.g. Wellington's Reroof Checklist,
#       FBC 109.3), which is the full price. To change it for every packet, edit the number below.
VALUE_FACTOR = 1.0


def _value_label():
    """How the permit value is described on the cover / checklist."""
    if abs(VALUE_FACTOR-1.0)<1e-9: return 'full contract price'
    return 'contract price x %s (%s)'%(('%g'%VALUE_FACTOR),
        ('less %g%%'%round((1-VALUE_FACTOR)*100,2)) if VALUE_FACTOR<1 else 'plus %g%%'%round((VALUE_FACTOR-1)*100,2))


def _permit_value(client):
    """Declared permit valuation = contract price x VALUE_FACTOR (see the setting above).
    `value` comes from the worksheet's contract price.

    NOTHING BLANK: a non-numeric value (including the 'DATA NEEDED' / 'N/A'
    placeholders) must survive to the page instead of being stripped to ''. A
    silent blank on the permit-value line reads as "reviewed and empty"; a
    visible placeholder reads as "not filled in yet". Audit 2026-07-20."""
    import re as _re
    raw=str(client.get('value','') or '').strip()
    n=_re.sub(r'[^0-9.]','',raw)
    if not n: return raw   # '' stays '', but 'DATA NEEDED'/'N/A' now print
    try: return '{:,.0f}'.format(round(float(n)*VALUE_FACTOR))
    except Exception: return raw


# --- Boca 706.8 roof-to-wall mitigation: the two answerable questions ----------
# Page 3 of Boca_02_ReRoofing_Mitigation_Package asks:
#   Q1 "Was the building permit for the home construction applied for on or after
#       January 1, 1988?"                        -> from PAPA "Year Built"
#   Q4 "is the value of the building $300,000 or more?"
#                                                -> from PAPA "Improvement Value"
#       (the form's own note: "the appraised value of the IMPROVEMENT determines the
#        threshold amount" -- NOT Total Market Value, which includes land and would
#        flip the answer: Tota is $253,727 improvement vs $391,733 market.)
#
# WHY COORDINATE STAMPS AND NOT fill_form(): on this form the Yes widget and the No
# widget SHARE a field name ('Building Permit' for Q1, 'Value of Building' for Q4).
# fill_form() resolves names document-wide, so setting the name would tick BOTH boxes
# on a signed certification. Same root cause as the PBO-94 'undefined' defect found in
# the 2026-07-21 re-audit. Each answer is therefore stamped at its own widget rect.
#
# Rects measured on the library copy (PyMuPDF, top-left origin, letter 792pt tall):
#   Q1 Yes [75,178,111,188]   Q1 No [138,177,174,189]
#   Q4 Yes [81,324,117,335]   Q4 No [136,324,172,335]
_BOCA_MIT_PAGE = 2                      # 0-based page index
_BOCA_MIT_XY = {                        # (x, y_baseline) in PDF bottom-left coords
    ('q1', 'Yes'): (79, 605), ('q1', 'No'): (142, 605),
    ('q4', 'Yes'): (85, 459), ('q4', 'No'): (140, 459),
}


def _boca_mitigation_answers(client):
    """(q1, q4) as 'Yes'/'No'/None from the PBC Property Appraiser record.

    None = leave blank. This is a certification the qualifier SIGNS, and the form
    warns misrepresentation "may be construed as willful code violation", so every
    uncertain case stays blank rather than guessing:
      * PAPA unreachable / unparseable        -> both None
      * Year Built 1987 or 1988               -> q1 None. Year built is a PROXY for the
        permit application date, not the same thing; a 1988 home could have been
        permitted in 1987. Only answer where the proxy cannot straddle the cutoff.
      * Improvement Value missing             -> q4 None
    """
    import re as _re, urllib.request as _u, html as _h
    digits = ''.join(c for c in str(client.get('pcn', '') or '') if c.isdigit())
    if len(digits) != 17:
        return (None, None)
    try:
        req = _u.Request('https://pbcpao.gov/Property/RenderPrintSum?parcelId=%s&flag=ALL' % digits,
                         headers={'User-Agent': 'PermitPacket/1.0'})
        with _u.urlopen(req, timeout=15) as r:
            page = r.read().decode('utf-8', 'replace')
    except Exception:
        return (None, None)

    q1 = q4 = None
    # PAPA puts the value in a sibling table cell, so several HTML tags sit between the
    # label and the digits - a tight [^0-9]{0,40} gap never matches. Strip tags first.
    flat = _re.sub(r'<[^>]+>', ' ', page)
    flat = _h.unescape(' '.join(flat.split()))
    m = _re.search(r'Year\s*Built\s*:?\s*(\d{4})', flat, _re.I)
    if m:
        yr = int(m.group(1))
        if yr >= 1989:
            q1 = 'Yes'
        elif yr <= 1986:
            q1 = 'No'
        # 1987-1988 deliberately left None
    m = _re.search(r'Improvement\s*Value(.{0,200})', flat, _re.I)
    if m:
        nums = _re.findall(r'\$?([\d,]{4,})', m.group(1))
        if nums:
            try:
                q4 = 'Yes' if float(nums[0].replace(',', '')) >= 300000 else 'No'
            except Exception:
                pass
    return (q1, q4)


def _net_sf(client):
    """Total roof area in SQUARE FEET for the 'NET S.F (for SFD's)' field, or ''.

    Only emits a real measured figure. Returns '' for the 'DATA NEEDED'/'N/A'
    placeholders and for anything under 120, which cannot be a roof in sq ft and
    is almost certainly a squares figure that reached us mislabelled - writing
    '3.44' into NET S.F. would be worse than leaving it blank. Audit 2026-07-21."""
    import re as _re
    raw=str(client.get('area','') or '').strip()
    n=_re.sub(r'[^0-9.]','',raw)
    if not n:
        return ''
    try: v=float(n)
    except Exception: return ''
    if v < 120:
        return ''
    return '{:,.0f}'.format(v)


def _permit_value_display(client):
    """Permit value as it should PRINT: '$12,345', or the bare placeholder text.
    Never emits a lone '$' with no number behind it."""
    v=_permit_value(client)
    if not v: return 'DATA NEEDED'
    return ('$'+v) if v[:1].isdigit() else v

# =============================================================================
# Roof shape - All Hip vs Gable / Partial Gable-Hip
# =============================================================================
# Section D of the HVHZ Uniform Roofing Application asks:
#     Roof Shape:  __ All Hip Roof   __ Gable Roof or Partial Gable/Hip Roof
# It shipped blank on every Broward packet (report 11, open item 1). It is NOT a
# PE-seal item - it is a geometric fact about the building, not a calculated
# pressure - but it SELECTS which design-pressure table a reviewer applies
# (1-G gable vs 1-H hip), so a wrong tick is worse than a blank one. Everything
# below is therefore either a measured fact or an explicit refusal to answer.
#
# SOURCE: the RoofGraf linear-footage measurements the CRM `measurements` table
# carries on all 869 rows as hip_lf / rake_lf. A RAKE is the sloped edge of a
# GABLE end, so a roof with no rake has no gable. (Verified 2026-07-21: zero
# NULLs in either column - every empty is stored as float 0.0 - so the only
# genuinely unanswerable case is hip == 0 AND rake == 0.)
#
# NOISE FLOOR - the reason this is not a bare `rake > 0` test. 75 of the 869 rows
# carry hip > 0 with rake <= 15 LF, and the extremes are plainly tracing
# artifacts: R-24243 is hip 442.25 / rake 0.25, R-24284 is hip 178.08 / rake 0.50,
# R-22184 is hip 204.92 / rake 0.75. A quarter-foot of rake against 442 feet of
# hip is a rounding residue in the RoofGraf trace, not a gable end, and ticking
# "Gable" on it would contradict the RoofGraf drawing bound into the same packet.
# The smallest real gable end is ~8-10 LF per side, so a 3.0 LF floor sits an
# order of magnitude below any true gable while recovering 27 genuinely all-hip
# roofs that a bare `rake > 0` would have mis-ticked.
_RAKE_NOISE_LF=3.0

_ROOF_SHAPE_LABEL={'hip':'All Hip','gable':'Gable or Partial Gable/Hip'}
# BC_03 Section D field 'Group11'. On-states read off the form's own /AP /N and
# confirmed BY RECTANGLE, not by name (the audit N-1 trap from report 11):
# page index 3, Choice4 rect [213.2,498.2,224.0,509.0] sits 2.9 pt left of the
# word "All" (x0 226.9); Choice5 rect [309.7,497.9,320.5,508.7] sits 3.5 pt left
# of "Gable" (x0 324.0), both on the same text line (y 496.7-512.7).
# NB report 11 recorded that no widget existed for this row and that it would have
# to be coordinate-stamped. That was wrong - re-checked against all 114 fields of
# the raw form, Group11 is a real two-widget radio, so no stamp is involved.
_BC03_SHAPE_CHOICE={'hip':'Choice4','gable':'Choice5'}


def _roof_shape(client):
    """('hip'|'gable', basis) for this job, or (None, why-not).

    'gable' is the form's SECOND option, "Gable Roof or Partial Gable/Hip Roof",
    which by its own wording covers a pure gable AND a mixed hip/gable roof - so any
    genuine rake means that box, whether or not hip is also present. Only a roof with
    hip and no meaningful rake is "All Hip".

    An explicit client['roof_shape'] always wins in both directions: a human who has
    looked at the roof outranks a derivation."""
    ov=str(client.get('roof_shape') or '').strip().lower()
    if ov:
        if 'gable' in ov or 'partial' in ov:
            return 'gable','set explicitly on the job'
        if 'hip' in ov:
            return 'hip','set explicitly on the job'
        return None,'roof_shape %r not understood'%(client.get('roof_shape'),)
    def _lf(k):
        try: return float(re.sub(r'[^0-9.]','',str(client.get(k) or '')) or 0)
        except Exception: return 0.0
    hip,rake=_lf('hip_lf'),_lf('rake_lf')
    basis='derived from RoofGraf hip %.1f LF / rake %.1f LF'%(hip,rake)
    if hip<=0 and rake<=0:
        # Overwhelmingly the flat/low-slope population (of the 76 such rows, 71 are
        # pitch 0:12 or 1:12 or have no pitch at all), where Section D does not apply
        # anyway. Never guessed.
        return None,'no hip/rake measurements on file'
    if hip<=0: return 'gable',basis            # rake but no hip -> gable, no floor needed
    if rake>_RAKE_NOISE_LF: return 'gable',basis
    return 'hip',basis


def _jurisdiction_info(ahj):
    """Where to submit, from the site's data/jurisdictions.json (portal, phone, notes), or {}."""
    try:
        import json as _js
        d=_js.load(open(os.path.join(os.path.dirname(os.path.dirname(_HERE)),'data','jurisdictions.json'),encoding='utf8'))
        key='Palm Beach County (unincorporated)' if _is_unincorporated_pbc(ahj) else ahj_label(ahj)
        for j in d.get('jurisdictions',[]):
            if j.get('ahj')==ahj or j.get('name')==key or (j.get('slug')=='palm-beach-county-unincorporated' and _is_unincorporated_pbc(ahj)):
                return j
    except Exception:
        pass
    return {}


def _submit_line(ahj):
    if _is_unincorporated_pbc(ahj):
        return 'PBC Building Division, 2300 N. Jog Rd, West Palm Beach FL 33411'
    j=_jurisdiction_info(ahj)
    bits=['%s Building Department'%ahj_label(ahj)]
    if j.get('platform'): bits.append(str(j['platform']))
    if j.get('phone'): bits.append(str(j['phone']))
    return ' - '.join(bits)


def _blank_cover(ahj, systems):
    buf=io.BytesIO(); c=canvas.Canvas(buf,pagesize=letter); W,H=letter
    c.setFillColor(NAVY); c.setFont('Helvetica-Bold',22); c.drawCentredString(W/2,H-110,'ROOF PERMIT FORMS')
    c.setFillColor(BLUE); c.setFont('Helvetica-Bold',12)
    c.drawCentredString(W/2,H-132,'%s  -  %s'%(ahj_label(ahj), ' + '.join(systems)+(' Systems' if len(systems)>1 else ' System')))
    c.setFillColor(HexColor('#222')); c.setFont('Helvetica',10)
    y=H-180
    for k,v in (('Jurisdiction',ahj_label(ahj)+(', Broward County (HVHZ)' if _is_broward(ahj) else ', Palm Beach County')),
                ('Applicant',('Owner-builder' if APPLICANT[0]=='owner_builder' else 'Licensed contractor')),
                ('Submit to',_submit_line(ahj)),
                ('What this is','The blank government forms this permit needs, in submittal order. Nothing is filled in.')):
        c.setFillColor(NAVY); c.setFont('Helvetica-Bold',10); c.drawString(70,y,k+':')
        c.setFillColor(HexColor('#222')); c.setFont('Helvetica',10); c.drawString(170,y,str(v)[:85]); y-=22
    c.setFillColor(HexColor('#555')); c.setFont('Helvetica',8)
    c.drawString(70,108,'Fill in every form, sign and notarize where indicated, record the Notice of Commencement, and attach the')
    c.drawString(70,96,'product approvals, the roof measurement report and any PE-sealed calculation before submitting.')
    c.showPage(); c.save(); buf.seek(0)
    return PdfReader(buf).pages


def cover(client, ahj, system, ul_label=None, prod_label=None, warn=None, contractor=None, system2=None):
    if _MODE[0]=='blank':
        return _blank_cover(ahj, [system]+([system2] if system2 else []))
    buf=io.BytesIO(); c=canvas.Canvas(buf,pagesize=letter); W,H=letter
    c.setFillColor(NAVY); c.setFont('Helvetica-Bold',16); c.drawCentredString(W/2,H-70,_C(contractor,'display'))
    _ld=_C(contractor,'license_dashes')
    c.setFillColor(BLUE); c.setFont('Helvetica',9)
    if _ld: c.drawCentredString(W/2,H-84,'Certified Roofing Contractor  -  State of Florida License '+_ld)
    c.setStrokeColor(NAVY); c.setLineWidth(2); c.line(54,H-96,W-54,H-96)
    c.setFillColor(NAVY); c.setFont('Helvetica-Bold',22); c.drawCentredString(W/2,H-140,'ROOF PERMIT PACKAGE')
    _systitle=('%s + %s Systems'%(system,system2)) if system2 else ('%s System'%system)
    c.setFillColor(BLUE); c.setFont('Helvetica-Bold',12); c.drawCentredString(W/2,H-160,'%s  -  %s'%(ahj_label(ahj), _systitle))
    if _is_broward(ahj):
        juris='%s, Broward County (FBC 8th Ed. 2023, HVHZ)'%ahj_label(ahj)
    elif _is_unincorporated_pbc(ahj):
        # No municipality in the string: this parcel IS the county's jurisdiction.
        # (Keep it under the 80-char row clip; the submitting office gets its own row.)
        juris='Unincorporated Palm Beach County (FBC 8th Ed. 2023, Non-HVHZ)'
    else:
        juris='%s, Palm Beach County (FBC 8th Ed. 2023, Non-HVHZ)'%ahj_label(ahj)
    rows=[('Owner',client.get('owner','')),('Property Address','%s, %s, FL %s'%(client.get('address',''),_city(client, ahj),client.get('zip',''))),
      ('PCN',pcn_display(client.get('pcn',''))),('Legal',client.get('legal','')),('Jurisdiction',juris)]
    if _is_unincorporated_pbc(ahj):
        # The mailing city (Boca Raton, Lake Worth, WPB...) is NOT the permitting office.
        # Spell the office out so nobody walks this packet into a city building department.
        rows.append(('Submit To','PBC Building Division, 2300 N. Jog Rd, West Palm Beach FL 33411'))
    _roofsys=(prod_label or SYSTEMS[system]['prod'])+' / '+(ul_label or SYSTEMS[system]['ul'])
    _scope=SYSTEMS[system]['desc']
    rows.append(('Roof System',_roofsys))
    if system2:
        rows.append(('Roof System 2',SYSTEMS[system2]['prod']+' / '+SYSTEMS[system2]['ul']))
        _scope='%s + %s'%(_scope, SYSTEMS[system2]['desc'])
    # Roof shape, with its basis, so the tick on Section D is checkable at a glance
    # against the RoofGraf bound into the same packet. Printed ONLY when it is known: an
    # unknown shape is asked as a question where a form needs it (Broward Section D), and a
    # "DATA NEEDED" line never goes out on a packet (2026-11-18 review).
    _shape,_shape_basis=_roof_shape(client)
    if _shape:
        rows.append(('Roof Shape',_ROOF_SHAPE_LABEL[_shape]))     # the shape only; its basis is internal
    rows+=[
      ('Scope',_scope),
      ('Permit Value',_permit_value_display(client))]
    _ctr=[x for x in (_C(contractor,'name_title'),_C(contractor,'license_dashes')) if x]
    _q=_C(contractor,'qualifier')
    if _ctr or _q:
        rows.append(('Contractor',' - '.join(_ctr)+(' (%s)'%_q if _q else '')))
    y=H-200
    for k,v in rows:
        c.setFillColor(NAVY); c.setFont('Helvetica-Bold',10); c.drawString(70,y,k+':')
        c.setFillColor(HexColor('#222')); c.setFont('Helvetica',10); c.drawString(190,y,str(v)[:80]); y-=22
    if warn:
        y-=4; c.setFillColor(RED); c.setFont('Helvetica-Bold',8.6)
        _cur=''
        for _wd in warn.split(' '):                     # word wrap (never split a word)
            _t=(_cur+' '+_wd).strip()
            if stringWidth(_t,'Helvetica-Bold',8.6)>W-140 and _cur:
                c.drawString(70,y,_cur); y-=11; _cur=_wd
            else:
                _cur=_t
        if _cur: c.drawString(70,y,_cur); y-=11
    c.setFillColor(HexColor('#555')); c.setFont('Helvetica',8)
    c.drawString(70,120,'This packet pre-fills contractor data, the roof system product approvals, and the client/property')
    c.drawString(70,108,'information above. Sign & notarize where indicated, record the Notice of Commencement, and attach the')
    c.drawString(70,96,'PE-stamped wind uplift calculation + roof measurement report before submitting to the building department.')
    c.setStrokeColor(BLUE); c.setLineWidth(0.75); c.line(54,70,W-54,70)
    c.setFillColor(NAVY); c.setFont('Helvetica',8)
    c.drawString(54,58,'  |  '.join(x for x in (_C(contractor,'name_title'),_C(contractor,'license_dashes'),_C(contractor,'phone'),_C(contractor,'email')) if x))
    c.showPage(); c.save(); buf.seek(0)
    return PdfReader(buf).pages

def _add_pdf(w, path):
    _REPORT['forms'].append(os.path.basename(path))
    try:
        rr=PdfReader(path)
        if rr.is_encrypted: rr.decrypt('')
        for p in rr.pages: w.add_page(p)
        return True
    except Exception:
        return False

def _highlight_pa(path, token, note):
    """Product approvals like FL1654 list hundreds of systems across 100+ pages; the plans
    examiner needs the ONE governing listing flagged. Yellow-band + red-box every row whose
    system number matches `token`, caption it, and stamp `note` on the approval's first page.
    Returns a stamped temp-file path, or the original path untouched on any failure
    (missing PyMuPDF, encrypted file, token not found) — never fatal to the build."""
    try:
        import fitz
    except Exception:
        return path
    try:
        d=fitz.open(path)
        hit_pages=[]
        for pg in d:
            rects=pg.search_for(token)
            if not rects: continue
            hit_pages.append(pg.number+1)
            for r in rects:
                band=fitz.Rect(34, r.y0-4, pg.rect.width-24, r.y1+14)
                pg.draw_rect(band, fill=(1,1,0.25), fill_opacity=0.35, color=(0.8,0,0), width=1.5, overlay=True)
            pg.insert_textbox(fitz.Rect(24, pg.rect.height-34, pg.rect.width-24, pg.rect.height-8),
                'YELLOW-HIGHLIGHTED ROW ABOVE = APPLICABLE SYSTEM FOR THIS PROJECT (%s)'%token,
                fontsize=10, color=(0.8,0,0), align=1)
        if not hit_pages:
            d.close(); return path
        p1=d[0]
        box=fitz.Rect(28, p1.rect.height-64, p1.rect.width-28, p1.rect.height-10)
        p1.draw_rect(box, color=(0.8,0,0), width=1.2, fill=(1,1,0.85), fill_opacity=0.9, overlay=True)
        p1.insert_textbox(fitz.Rect(box.x0+6, box.y0+4, box.x1-6, box.y1-2),
            note+'  (see highlighted row, sheet %s of this document)'%'/'.join(map(str,hit_pages)),
            fontsize=8.5, color=(0.8,0,0), align=1)
        out=os.path.join(tempfile.gettempdir(),'pa_hl_'+os.path.basename(path))
        d.save(out); d.close()
        return out
    except Exception:
        return path


def _pa_excerpt(path, token, note, cover=4, ctx=1):
    """Cover pages + the governing-system pages of a product approval, highlighted.

    FL1654-R44 is a 125-page P.E. evaluation report listing hundreds of assemblies; only
    one of them (System W-179) is this job's. Binding all 125 pages - twice, once per
    approval file - is what made the Flat packets 258 pages against the 47-page accepted
    Tota permit. PB-O-131 Attachment B asks for "Complete Product Approval Information
    Including Cover Pages" and "Product Approval with Specific System Descriptions
    circled" - i.e. the cover pages plus the governing listing, which is exactly this.

    Keeps pages 1..`cover` (the PEER title page, FL number, revision date, scope and
    signature block - the pages that identify WHICH approval this is) plus every page
    carrying `token`, with `ctx` pages either side so a table row keeps its header.
    The kept pages are the ORIGINAL pages, unmodified except for the highlight band and
    a footer stating that this is an excerpt and naming the omitted range - a reviewer
    must never be able to mistake an excerpt for the complete report.

    Returns the original path untouched if PyMuPDF is missing, the token is absent, or
    the excerpt would not actually be smaller - never fatal, never silently lossy."""
    try:
        import fitz
    except Exception:
        return path
    try:
        d=fitz.open(path)
        n=d.page_count
        hits=[p.number for p in d if p.search_for(token)]
        if not hits:
            d.close(); return path
        keep=set(range(0,min(cover,n)))
        for h in hits:
            keep.update(range(max(0,h-ctx), min(n,h+ctx+1)))
        keep=sorted(keep)
        if len(keep)>=n:
            d.close(); return path
        for pg in d:
            if pg.number not in keep: continue
            for r in pg.search_for(token):
                band=fitz.Rect(34, r.y0-4, pg.rect.width-24, r.y1+14)
                pg.draw_rect(band, fill=(1,1,0.25), fill_opacity=0.35, color=(0.8,0,0),
                             width=1.5, overlay=True)
            pg.insert_textbox(fitz.Rect(24, pg.rect.height-30, pg.rect.width-24, pg.rect.height-6),
                'EXCERPT of a %d-page product approval - pages %s only. Full report on file '
                'and available on request. Highlighted row = governing system (%s).'
                %(n, ','.join(str(k+1) for k in keep), token),
                fontsize=7, color=(0.8,0,0), align=1)
        d.select(keep)
        p1=d[0]
        box=fitz.Rect(28, p1.rect.height-64, p1.rect.width-28, p1.rect.height-34)
        p1.draw_rect(box, color=(0.8,0,0), width=1.2, fill=(1,1,0.85), fill_opacity=0.9, overlay=True)
        p1.insert_textbox(fitz.Rect(box.x0+6, box.y0+3, box.x1-6, box.y1-2), note,
                          fontsize=8, color=(0.8,0,0), align=1)
        out=os.path.join(tempfile.gettempdir(),'pa_ex_'+os.path.basename(path))
        d.save(out); d.close()
        return out
    except Exception:
        return path

# --- Broward overlay stamping -------------------------------------------------
# The county BC_xx forms in <muni>\_Prefilled are FLATTENED (no AcroForm fields),
# so client folio/legal/owner/address can't be filled like the live PBC forms.
# Instead we stamp them onto the empty labeled blanks at coordinates measured
# empirically from Deerfield_Beach (letter, 612x792, bottom-left origin). The
# BC_xx forms are the shared Broward County forms, so these positions hold across
# munis. Each stamp: (page_index, x, y_baseline, key, font_size, max_width).
# key is resolved against the per-build values dict from _broward_values().
_BC01_STAMPS=[
 (0,  96, 644, 'addr',  8, 305),   # Job Address: (street)
 (0, 433, 644, 'city',  8, 110),   # ...City:
 (0,  82, 615, 'pcn',   8, 200),   # Tax Folio
 (0, 116, 468, 'legal', 8, 440),   # Legal Description:
 (0, 106, 433, 'owner', 8, 290),   # Property Owner:
 (0, 109, 404, 'addr',  8, 190),   # Owner's Address:
 (0, 328, 404, 'city',  8, 110),   # ...City:
 (0, 468, 404, 'st',    8,  30),   # ...State:
 (0, 521, 404, 'zip',   8,  40),   # ...Zip:
 (0, 510, 616, 'value', 8, 150),   # Job Value: (estimate -10%)
]
_BC02_STAMPS=[
 (0, 385, 637, 'pcn',     8, 175),   # TAX FOLIO NO. (section 1 header line)
 (0,  40, 608, 'legaddr', 8, 510),   # 1. Description of property (legal + street)
 (0, 176, 554, 'owner',   8, 250),   # 3a. Owner Name
]
_BC03_STAMPS=[
 # Page index 1 = Section A of the 5-page HVHZ Roofing Application.
 # Coords: PDF y_baseline = 792 - pdfplumber_top - 6 (Helvetica 8pt cap-height offset).
 (1, 137, 627, 'addrfull',       8, 390),  # Job Address (street, city, ST zip on one line)
 # Roof Category checkboxes — only the matching system gets 'X'; others evaluate to '' and are skipped
 (1,  39, 575, 'cat_flat_x',     8,  10),  # Low Slope
 (1,  39, 553, 'cat_shingle_x',  8,  10),  # Asphaltic Shingles
 (1, 183, 553, 'cat_metal_x',    8,  10),  # Metal Panel / Shingles
 (1, 183, 575, 'cat_tile_mech_x',8,  10),  # Mechanically Fastened Tile
 # --- Audit fix 2026-07-20: the caller-supplied roof measurements previously rendered
 # NOWHERE on any Broward packet. Section A roof-area buckets (values sit on the blank
 # underscore line beneath each label, y=414.5 -> baseline 417).
 (1,  40, 415, 'area_low',       8, 190),  # Low Slope Roof Area (ft2)
 (1, 243, 415, 'area_steep',     8, 180),  # Steep Sloped Roof Area (ft2)
 (1, 436, 415, 'area_total',     8, 100),  # Total (ft2)
 # Section C = page 2, used by LOW-SLOPE (Flat) systems only. The *_ls keys are empty
 # for steep systems, and _stamp_pdf silently skips an empty value.
 (2, 146, 457, 'deck_ls',        8, 165),  # Deck ... Gauge / Thickness
 (2, 105, 439, 'slope_ls',       8,  85),  # Deck ... Slope
 # Section D = page 3, used by STEEP-SLOPED (Shingle / Tile / Metal) systems only.
 (3, 220, 466, 'deck_ss',        8, 190),  # Deck Type:
 (3,  62, 388, 'slope_ss',       8,  42),  # Roof Slope:  ____ : 12
 (3, 137, 200, 'mrh',            8,  95),  # Mean Roof Height:
]
# --- the reference installer _System_Templates coordinate maps (Broward / HVHZ) ------------------
# These templates are FLATTENED (no AcroForm), so the job's slope / mean roof height /
# deck type / roof area have to be overlay-stamped. Coordinates measured with PyMuPDF
# word boxes against the real approved Plantation submittal set (R-24352) layout; all
# four AB templates and all six Flat Section-C templates share one identical layout,
# verified file-by-file.
_HVHZ_AB_STAMPS=[
 (0, 157, 606, 'addrfull',   8, 380),   # Job Address:
 (0, 134, 414, 'area_low',   8,  70),   # Low slope roof area (ft.2):
 (0, 318, 414, 'area_steep', 8,  70),   # Steep Sloped area (ft.2):
 (0, 443, 414, 'area_total', 8,  70),   # Total (ft.2):
 (0, 528, 523, 'mrh',        8,  45),   # Roof Mean Height (h) ____ ft.
]
_HVHZ_SECTION_D_STAMPS={
 'HVHZ_Section_D_Shingle_TEMPLATE.pdf':[
   (0,  82, 413, 'slope', 8,  40), (0, 112, 382, 'mrh', 8, 38), (0, 321, 586, 'deck', 8, 200)],
 'HVHZ_Section_D_Tile_TEMPLATE.pdf':[
   (0, 132, 531, 'slope', 8,  50), (0, 305, 531, 'mrh', 8, 48), (0,  49, 491, 'deck', 8, 200)],
 'HVHZ_Section_D_Metal_TEMPLATE.pdf':[
   (0,  72, 380, 'slope', 8,  30), (0, 102, 348, 'mrh', 8, 28), (0, 310, 552, 'deck', 8, 200)],
}
# Flat Section C templates: Roof Slope ___ /12  and  Roof Mean Height ___ ft.
_HVHZ_SECTION_C_STAMPS=[(0, 75, 558, 'slope', 8, 46), (0, 237, 558, 'mrh', 8, 40)]
# Flat Section C template variants — deck type -> filename.
# 'WoodDeck' is the residential reroof default; pass sysd['deck'] to select a different one.
_FLAT_SECTION_C={
 'WoodDeck':       'HVHZ_Section_C_Flat_WoodDeck_Insulated_TEMPLATE.pdf',
 'ConcreteDeck':   'HVHZ_Section_C_Flat_ConcreteDeck_Insulated_TEMPLATE.pdf',
 'SteelDeck':      'HVHZ_Section_C_Flat_SteelDeck_Insulated_TEMPLATE.pdf',
 'BUR':            'HVHZ_Section_C_BUR_Flat_TEMPLATE.pdf',
 'HotMopWood':     'HVHZ_Section_C_Flat_HotMop_BUR_WoodDeck_TEMPLATE.pdf',
 'HotMopConcrete': 'HVHZ_Section_C_Flat_HotMop_BUR_ConcreteDeck_TEMPLATE.pdf',
}
_FLAT_SECTION_C_DEFAULT='WoodDeck'
_BC06_STAMPS=[
 # Page 0 = FDEP Asbestos Notification (2-page form). Contractor (Section III) is pre-filled.
 # Section I: Facility = the job property
 (0, 102, 611, 'owner', 8, 450),  # I.   Facility Name (owner name as facility identifier)
 (0,  65, 598, 'addr',  8, 450),  # I.   Address
 (0,  55, 585, 'city',  8, 115),  # I.   City
 (0, 200, 585, 'st',    8,  20),  # I.   State
 (0, 256, 585, 'zip',   8,  50),  # I.   Zip
 # Section II: Facility Owner  ("Owner" label ends at x1=103.5; stamp starts after it)
 (0, 108, 515, 'owner', 8, 160),  # II.  Facility Owner name
 (0,  65, 502, 'addr',  8, 450),  # II.  Address
 (0,  55, 489, 'city',  8, 190),  # II.  City
 (0, 360, 489, 'zip',   8,  55),  # II.  Zip
]
_BC01_CONTRACTOR_STAMPS=[
 (0, 106, 378, 'c_name',  7, 175), (0, 288, 376, 'c_phone', 9, 135), (0, 429, 376, 'c_email', 9, 140),
 (0, 114, 346, 'c_addr',  9, 215), (0, 333, 346, 'c_city',  9, 135), (0, 473, 346, 'c_st',    9,  30),
 (0, 527, 346, 'c_zip',   9,  50), (0, 109, 317, 'c_qual',  9, 300), (0, 501, 317, 'c_lic',   9,  80),
]
_BC02_CONTRACTOR_STAMPS=[
 (0,  37, 575, 'improvement', 9, 200),   # 2. General description of improvement
 (0,  39, 476, 'c_line',      9, 470),   # 4. Contractor name, address, phone
]
_BC06_CONTRACTOR_STAMPS=[
 (0, 310, 587, 'county',  9,  80),       # facility county
 (0, 122, 480, 'c_name',  7, 150), (0, 302, 481, 'c_phone', 6, 115), (0, 475, 481, 'c_email', 6, 95),
 (0,  67, 465, 'c_addr',  9, 400), (0,  53, 453, 'c_city',  9, 190), (0, 255, 453, 'c_st',    9,  30),
 (0, 306, 453, 'c_zip',   9,  60),
]
_BC04_STAMPS=[]   # BC_04 is fillable by field name (PROPERTY ADDRESS / STATE / ZIP); signatures stay blank

_BROWARD_STAMP_FORMS={
 'BC_01_Uniform_Building_Permit_Application_PREFILLED.pdf': _BC01_STAMPS,
 'BC_02_Notice_of_Commencement_PREFILLED.pdf': _BC02_STAMPS,
 'BC_03_HVHZ_Uniform_Roofing_Application_PREFILLED.pdf': _BC03_STAMPS,
 'BC_06_FDEP_Asbestos_Notification_PREFILLED.pdf': _BC06_STAMPS,
}

# =============================================================================
# BC_03 (HVHZ Uniform Roofing Application) — NAMED-FIELD fill
# =============================================================================
# Audit 2026-07-21 (report 11_BC03_SECTION_D). Section D (Steep Sloped Roof System)
# shipped with 10 of its 13 answer boxes BLANK on every Broward tile/shingle/metal
# packet. Root cause: the builder overlay-stamped measured coordinates onto the
# FLATTENED _Prefilled copy (0 AcroForm fields), so only the three fields that had a
# stamp coordinate (deck / slope / mean roof height) could ever print.
#
# FIX: fill the RAW form by FIELD NAME instead. The raw
# 01_Permit_Application_Packet\BC_03_HVHZ_Uniform_Roofing_Application.pdf carries 114
# real AcroForm fields and is byte-identical across all 31 Broward municipalities +
# the county copy (verified by md5). Diffing raw vs _PREFILLED showed the prefilled
# copy adds EXACTLY three things, all reproducible here:
#   1. page 1 "Contractor's Name" = "<previous author's name + license>"
#   2. page 1 "Reroofing" checkbox = X
#   3. page 0 red top-note
# so nothing is lost by switching, and the contractor line now honours a caller-
# supplied `contractor` dict instead of being baked in for one company only.
#
# WIDGET-NAME vs PRINTED-LABEL: two Section-D widgets are misleadingly named. Verified
# by rectangle against the page's own text (never by name):
#   widget 'Adhesive Type'   sits on the printed line "Cap Sheet Type:"
#   widget 'Type Cap Sheet'  sits on the printed line "Cap Sheet Attachment:"
# We answer the printed label — that is what the plans examiner reads.
#
# DELIBERATELY LEFT BLANK (PE-seal rule — a sealed Florida PE calc owns these):
#   * zone1 / zone2 / zone3  (Minimum Design Wind Pressures, RAS 127)
#   * every page-2 P1'/P1/P2/P3 and page-5 uplift / RAS-128 grid field
#   * 'Group11' Roof Shape (All Hip vs Gable/Partial Gable-Hip) — not a PE item, but
#     nothing in `client` or the property record carries roof form, and guessing hip
#     vs gable changes the design pressures. Human fills it.
_BC03_RAW='BC_03_HVHZ_Uniform_Roofing_Application.pdf'

# Miami-Dade NOA for the Polyglass Polystick underlayment family (IR-Xe / TU-Plus /
# MTS / MTS-Plus are all listed under the one NOA). Source: the four
# Underlayment_Polyglass_Polystick_*_MiamiDade_NOA_24-1022.13.pdf files in
# Broward_County\Product_Approvals_Library, and the library's
# _System_Templates\Tile\HVHZ_Section_D_Tile_TEMPLATE.pdf ("Self-adhered to deck
# (NOA 24-1022.13)"). This is the HVHZ number; FL5259 / FL1654 are the NON-HVHZ
# statewide numbers carried in UNDERLAYMENTS[] for the Palm Beach County forms and
# must NOT be printed on a Broward form.
_HVHZ_UL_NOA='NOA 24-1022.13'

# Roof-covering approval as it must print on an HVHZ form. Every number below was read
# off a document that is physically in the library — never assumed.
#   Shingle oc  -> Shingle_OwensCorning_TruDef_Duration_MiamiDade_NOA_21-0518.04.pdf
#                  (also the number on the APPROVED Plantation R-24352 Section D)
#   Shingle gaf -> Shingle_GAF_Timberline_HDZ_MiamiDade_NOA_22-1221.04.pdf
#   Tile westlake -> Tile_Westlake_Saxony900_MiamiDade_NOA_24-0320.02.pdf
#   Tile eagle_low -> Tile_Eagle_LowProfile_Flat_MiamiDade_NOA_25-0313.05.pdf
#   Tile barcelona / eagle_high / eagle_med -> the Miami-Dade NOA already carried in
#                  PRODUCTS[] (each read out of its own approval PDF).
#   Metal       -> _System_Templates\Metal\HVHZ_Section_D_Metal_TEMPLATE.pdf, which
#                  states "FL41724.10 (HVHZ / TAS-tested)". Note this is NOT the
#                  FL41724.08 in SYSTEMS[] — that is the non-HVHZ listing.
# 'eagle' (statewide FL7473) has NO HVHZ approval document on disk, so it prints
# DATA NEEDED rather than an invented number.
_HVHZ_COVER_APPROVAL={
 'Shingle':{'oc':'NOA 21-0518.04','gaf':'NOA 22-1221.04'},
 # 'eagle' = the statewide Eagle listing; its HVHZ variant on disk
 # (Tile_Eagle_ConcreteTile_FL7473-R11_HVHZ.pdf) is a NEMO P.E. Evaluation Report
 # issued against the FBC 8th Ed. (2023) HVHZ sections, so FL7473-R11 (HVHZ) is the
 # number that belongs on a Broward form — not the FL7473 in PRODUCTS[].
 'Tile':{'westlake':'NOA 24-0320.02','eagle_low':'NOA 25-0313.05','eagle':'FL7473-R11 (HVHZ)'},
 'Metal':{'_':'FL41724.10 (HVHZ)'},
}

def _hvhz_cover_approval(system, sysd):
    """Roof-covering product approval number for an HVHZ (Broward) Section D."""
    m=_HVHZ_COVER_APPROVAL.get(system) or {}
    pk=sysd.get('product_key') or DEFAULT_PRODUCT.get(system) or '_'
    if pk in m: return m[pk]
    if '_' in m: return m['_']
    # PRODUCTS[] already carries a Miami-Dade NOA for barcelona / eagle_high / eagle_med.
    fl=str(sysd.get('prodfl') or '').strip()
    if fl.upper().startswith('NOA'): return fl
    return 'DATA NEEDED'

def _bc03_section_d(client, system, sysd):
    """Section D (Steep Sloped Roof System) answers, keyed by RAW BC_03 field name.

    Steep systems only — a Flat job answers Section C and Section D stays untouched.
    Every value traces to SPEC_SHEET / SYSTEMS / UNDERLAYMENTS / PRODUCTS or to a
    document in Broward_County\\Product_Approvals_Library. Answer SHAPE (ALL CAPS
    shorthand avoided, 'N/A' for non-applicable components) is calibrated against the
    APPROVED Plantation R-24352 Section D, which reads:
        Deck Type: CDX | Type Underlayment: POLYGLASS IRXE DIRECT TO DECK 24-1022.13
        Roof Slope: 3 | Insulation: N/A | Fire Barrier: N/A | Ridge Ventilation? N/A
        Fastener Type & Spacing: 2.5" RING SHANK, 4" OC | Adhesive Type: N/A
        Type Cap Sheet: N/A | Mean Roof Height: 15' | Roof Covering: O/C SHINGLES
        Type & Size Drip Edge: GALV 3X3 26GA 11GA RING SHANK 4" OC
    Note the approved sheet leaves Zone1/2/3 blank — same PE rule we apply.
    """
    if system not in ('Shingle','Tile','Metal'): return {}
    spec=SPEC_SHEET.get(system,{})
    ul='%s (%s)'%(sysd.get('ul',''), _HVHZ_UL_NOA) if sysd.get('ul') else 'DATA NEEDED'
    # Product + approval only. The manufacturer has its own box two lines up, and the
    # "Roof Covering" widget is the narrowest on the sheet (148 pt) - repeating the
    # manufacturer here pushed the metal string past the box edge at the 5 pt floor.
    cover=' '.join(x for x in [sysd.get('prod',''),
                               _hvhz_cover_approval(system, sysd)] if x).strip()
    T={
      'Roof System Manufacturer':   sysd.get('mfr','') or 'DATA NEEDED',
      'Notice of Acceptance Number':_hvhz_cover_approval(system, sysd),
      'Deck Type':                  spec.get('decking','') or 'DATA NEEDED',
      'Type Underlayment':          ul,
      'Roof Slope':                 str(client.get('slope') or '').strip(),
      'Mean Roof Height':           str(client.get('mrh') or '').strip(),
      'Insulation':                 spec.get('insulation','') or 'N/A',
      # the reference installer re-roofs a wood deck with a self-adhered underlayment; no thermal
      # barrier is part of any of the three steep assemblies. The approved R-24352
      # Section D answers this line 'N/A'.
      'Fire Barrier':               'N/A',
      'Ridge Ventilation':          spec.get('ridge','') or 'N/A',
      'Fastener Type  Spacing':     spec.get('deck_fastener','') or 'DATA NEEDED',
      'Roof Covering':              cover or 'DATA NEEDED',
      'Type  Size':                 spec.get('drip','') or 'DATA NEEDED',   # Drip Edge Type & Size
    }
    # --- Cap Sheet Type / Cap Sheet Attachment (see widget-name note above) --------
    # Shingle & Metal have no cap sheet -> N/A on both lines (matches R-24352).
    # TILE does: the HVHZ form calls the tile underlayment the "cap sheet", and
    # the library's HVHZ_Section_D_Tile_TEMPLATE.pdf labels it verbatim
    # "Tile Underlayment (Cap Sheet Type:)  Polystick TU-Plus" and
    # "Tile Underlayment Attachment Method:  Self-adhered to deck (NOA 24-1022.13)".
    if system=='Tile':
        T['Adhesive Type']=ul                      # printed label: Cap Sheet Type
        T['Type Cap Sheet']='Self-adhered to deck' # printed label: Cap Sheet Attachment
    else:
        T['Adhesive Type']='N/A'
        T['Type Cap Sheet']='N/A'
    return {k:v for k,v in T.items() if str(v).strip()}

# Section D "Slope Range" radio (field 'Group10'). Purely derived from the job's own
# slope — no judgement, no invention. Widget on-states read off the form's /AP /N.
_BC03_SLOPE_RANGE=[(4.0,'Choice1'),(6.0,'Choice2'),(12.0,'Choice3')]

def _bc03_slope_choice(slope):
    try: s=float(str(slope).strip().split(':')[0].split('/')[0])
    except Exception: return None
    if s<2: return None                    # below the form's own >=2:12 range
    for hi,ch in _BC03_SLOPE_RANGE:
        if s<=hi: return ch
    return None                            # >12:12 is off the form's scale

# Section A (page 2 of the form / page index 1) roof-category checkbox per system.
# TILE CORRECTED 2026-07-21 (owner-approved). The form had stamped "Mechanically Fastened
# Tile" since it was first wired up, but the reference installer installs tile ADHESIVE-SET with ICP
# Polyset AH-160 — whose NOA 22-0411.02 is attached in the same packet. The tick therefore
# contradicted the approval a reviewer holds in their hand, and SYSTEMS['Tile']['box']
# ("Mortar/Foam Set tile") already said so. Flagged in report 11, corrected here.
_BC03_CATEGORY={'Flat':'Low Slow','Shingle':'Asphaltic Shingles_2',
                'Metal':'Metal Panel Shingles','Tile':'Mortar  Adhesive Set Tile'}

# =============================================================================
# Broward product-approval selection
# =============================================================================
# Audit 2026-07-21. _build_broward() attached EVERY pdf in
# Broward_County\Product_Approvals_Library\<system>\ regardless of the product the
# caller chose, so a Barcelona tile packet shipped with Saxony 900 AND Eagle Low
# Profile approvals stapled behind it — three manufacturers' coverings on one permit,
# the same wrong-system-document class as the PBC Form 200 leak fixed 2026-07-20. A
# shingle packet likewise carried both Owens Corning and GAF, and a metal packet both
# Dynamic and PAC-CLAD.
#
# Rule: a file is a COVERING approval (attaches only when it matches the chosen
# product) or a SHARED component — underlayment, adhesive — which always attaches.
# The classification is an EXPLICIT filename map, not a fuzzy name match, so adding a
# file to the library can never silently change which approvals ship.
#   '_'    = the only covering choice this system has (no PRODUCTS[] menu for it)
#   '_alt' = alternate-manufacturer coverings that exist in the library for reference
#            but are not selectable and must never auto-attach.
_HVHZ_COVER_FILES={
 'Shingle':{
   'oc'   :['Shingle_OwensCorning_TruDef_Duration_MiamiDade_NOA_21-0518.04.pdf'],
   'gaf'  :['Shingle_GAF_Timberline_HDZ_MiamiDade_NOA_22-1221.04.pdf'],
 },
 'Tile':{
   'westlake' :['Tile_Westlake_Saxony900_MiamiDade_NOA_24-0320.02.pdf'],
   'eagle_low':['Tile_Eagle_LowProfile_Flat_MiamiDade_NOA_25-0313.05.pdf'],
 },
 'Metal':{
   '_'    :['Metal_Dynamic_DMClass1500_FL41724-R3_HVHZ_Eval.pdf',
            'Metal_Dynamic_DMClass1500_FL41724-R3_HVHZ_Submittal.pdf'],
   '_alt' :['Metal_PACCLAD_SnapClad_24ga_MiamiDade_NOA_24-0924.06_ALT.pdf',
            'Metal_PACCLAD_TiteLocPlus_MiamiDade_NOA_24-1220.06_ALT.pdf'],
 },
 'Flat':{
   '_'    :['Flat_Polyglass_SA_ModBit_FL1654-R44_HVHZ_AE.pdf',
            'Flat_Polyglass_SA_ModBit_FL1654-R44_HVHZ_II.pdf',
            'Flat_Polyglass_Polyflex_Elastoflex_SA_MiamiDade_NOA_23-1211.03.pdf'],
   '_alt' :['BUR_HotMop_GAF_GAFGLAS_MiamiDade_NOA_25-1016.02.pdf',
            'BUR_HotMop_JM_GlasPly_MiamiDade_NOA_25-0911.04.pdf'],
 },
}

class MissingApprovalError(Exception):
    """The chosen product has no product-approval document anywhere in the library.

    Raised instead of silently shipping a different manufacturer's approval: a MISSING
    approval is a correctable review comment, a WRONG one is a rejected permit and a
    document that says we are installing something we are not."""

def _broward_approvals(system, sysd):
    """Ordered list of approval PDFs for a Broward/HVHZ packet.

    [chosen covering] + [shared components]. HVHZ variants in the Broward folder are
    preferred (they are the Miami-Dade NOAs the HVHZ form asks for); the statewide PBC
    Product_Approvals_Library is used ONLY when Broward holds no covering for the
    chosen product. Raises MissingApprovalError when neither has one."""
    sub=os.path.join(BPA, _PA_SUB.get(system,''))
    have=set(os.listdir(sub)) if os.path.isdir(sub) else set()
    cmap=_HVHZ_COVER_FILES.get(system,{})
    covering_all={f for lst in cmap.values() for f in lst}
    pk=sysd.get('product_key') or DEFAULT_PRODUCT.get(system) or '_'
    chosen=[f for f in (cmap.get(pk) or cmap.get('_') or []) if f in have]
    out=[os.path.join(sub,f) for f in chosen]
    if not out:
        # No HVHZ covering on file for this product. Fall back to the statewide copy the
        # PBC path uses — sysd['approvals'][0] is always the primary covering approval.
        # For the tile profiles this is a Miami-Dade NOA anyway (Barcelona 24-1008.13,
        # Eagle High 24-1008.07, Eagle Medium 25-0313.04), i.e. valid in the HVHZ; only
        # 'eagle' (statewide FL7473) is not, and it has no NOA on disk at all.
        rel=(sysd.get('approvals') or [None])[0]
        alt=os.path.join(PA, rel) if rel else None
        # Prefer the HVHZ sibling when the library holds both variants of the same
        # listing. PRODUCTS['Tile']['eagle'] points at FL7473-R11_NonHVHZ.pdf, but
        # FL7473-R11_HVHZ.pdf sits beside it — a NEMO P.E. Evaluation Report issued
        # against the 8th Ed. (2023) FBC High Velocity Hurricane Zone sections. That is
        # the one a Broward reviewer needs.
        if alt and 'NonHVHZ' in os.path.basename(alt):
            _h=alt.replace('NonHVHZ','HVHZ')
            if os.path.exists(_h): alt=_h
        if alt and os.path.exists(alt) and 'nonhvhz' not in os.path.basename(alt).lower():
            out=[alt]
        else:
            # A file marked NonHVHZ is a statewide-only listing. Attaching it to a
            # Broward permit would assert HVHZ approval the document does not carry.
            raise MissingApprovalError(
                'No product approval on file for %s / %s (%s). Add the approval PDF to '
                '%s before building this packet — the packet was NOT written.'
                % (system, pk, sysd.get('prodfl',''), sub))
    # shared components: everything in the Broward folder that is not a covering approval
    out += [os.path.join(sub,f) for f in sorted(have)
            if f.lower().endswith('.pdf') and f not in covering_all]
    return out

def _bc03_fill(client, ahj, system, sysd, contractor=None):
    """(T, B) named-field fill for the RAW BC_03. Section A always; Section D only for
    steep systems (a Flat job's Section D must stay blank, and vice versa)."""
    bv=_broward_values(client, ahj, system)
    name=_C(contractor,'name_title')
    lic=_C(contractor,'license') or ''
    T={"Contractor's Name":('%s   Lic. %s'%(name,lic)).strip(),
       'Job Address': bv.get('addrfull',''),
       'Low Slope Roof Area SF': bv.get('area_low',''),
       'Steep Sloped Roof AREA SSF': bv.get('area_steep',''),
       'Total SF': bv.get('area_total','')}
    B={'Reroofing':'On'}
    cb=_BC03_CATEGORY.get(system)
    if cb: B[cb]='On'
    if system in ('Shingle','Tile','Metal'):
        T.update(_bc03_section_d(client, system, sysd))
        ch=_bc03_slope_choice(client.get('slope'))
        if ch: B['Group10']=ch
        # Roof Shape (Group11) - the last non-PE blank on Section D. Steep systems only:
        # a Flat packet answers Section C, where the row does not exist. Blank stays
        # blank when the measurements cannot answer it.
        _sh,_=_roof_shape(client)
        if _sh: B['Group11']=_BC03_SHAPE_CHOICE[_sh]
    elif system=='Flat':
        # Section C (Low Sloped Roof System), BC_03 page index 2. Parity with the
        # coordinate stamps this replaces (deck gauge/thickness + slope), plus the three
        # header fields, which are read off the library's
        # _System_Templates\Flat\HVHZ_Section_C_Flat_WoodDeck_Insulated_TEMPLATE.pdf
        # ("Roof System Manufacturer: Polyglass U.S.A. / Product Approval NOA:
        # 23-1211.03 / SA 2-ply mod-bit over polyiso insulation").
        # The rest of Section C's assembly grid (anchor/base/ply/top-ply sheets,
        # insulation layers, fastener rows) is NOT filled here — see report 11's
        # follow-up note. Every one of those rows needs the same rectangle-verified
        # widget mapping Section D got, and several are RAS-128 / PE-seal items.
        T['System Manufacturer']=sysd.get('mfr','')
        T['Product Approval']='NOA 23-1211.03'
        T['approval system']='SA 2-ply mod-bit over polyiso insulation'
        T['Type']='Wood / Plywood'
        T['Gauge  Thickness']=SPEC_SHEET.get('Flat',{}).get('decking','')
        T['Slope']=str(client.get('slope') or '').strip()
    return {k:v for k,v in T.items() if str(v).strip()}, B

def _broward_values(client, ahj, system=None):
    addr=client.get('address',''); city=_city(client, ahj)
    zip_c=client.get('zip','')
    addrfull=', '.join(s for s in [addr, city, ('FL '+zip_c) if zip_c else ''] if s)
    legal=client.get('legal',''); pcn=client.get('pcn','')
    legaddr=' -- '.join([s for s in (addr, legal) if s])
    _is=lambda s: 'X' if system==s else ''
    # --- roof measurements (audit fix 2026-07-20) ---------------------------------
    # Everything below is CALLER-SUPPLIED job data (slope / mean roof height / total
    # roof area) plus the library's documented deck standard from SPEC_SHEET. No
    # wind pressure, uplift or RAS-127/128 value is derived here - those need a
    # sealed Florida PE calculation and stay blank on every Broward form.
    slope=str(client.get('slope') or '').strip()
    mrh=str(client.get('mrh') or '').strip()      # bare number: every HVHZ field prints its own "ft."
    area=str(client.get('area') or '').strip()
    deck=SPEC_SHEET.get(system,{}).get('decking','') if system else ''
    # Which roof-area bucket the HVHZ forms want. The form offers only two: "low slope"
    # (which covers flat) and "steep sloped". Reuse the same slope rule as the PBC forms.
    _bucket=_spec_markone(system, slope) if system else 'sloped'
    _low=(system=='Flat') or _bucket in ('flat','lowslope')
    return {'addr':addr,'city':city,'st':'FL','zip':zip_c,'addrfull':addrfull,
            'owner':client.get('owner',''),'pcn':pcn,'legal':legal,'legaddr':legaddr,
            'value':_permit_value_display(client),
            'cat_flat_x':_is('Flat'),'cat_shingle_x':_is('Shingle'),
            'cat_metal_x':_is('Metal'),'cat_tile_mech_x':_is('Tile'),
            'slope':slope,'mrh':mrh,'deck':deck,
            'area_total':area,
            'area_low':(area if _low else ''),
            'area_steep':('' if _low else area),
            # section-scoped copies: the county BC_03 has a Section C (low slope) AND a
            # Section D (steep slope); only the one for THIS system may be filled.
            'slope_ls':(slope if _low else ''),'deck_ls':(deck if _low else ''),
            'slope_ss':('' if _low else slope),'deck_ss':('' if _low else deck)}

# --- PBC flattened-form stamping (city affidavits with no AcroForm fields) -----
# Coords measured on Boynton Beach's Re-Roof Affidavit (letter 612x792, affidavit = page index 1).
# (page_idx, x, y_baseline, value-key, font_size, max_width). Per-file because each city's form differs.
_PBC_REROOF_AFFIDAVIT=[
 (1,112,445,'contractor',9,400),       # From: ____ (Contractor)
 (1,112,426,'contractor_addr',9,400),  # ____ (Contractor's Address)
 (1,112,407,'owner',9,400),            # ____ (Owner/s Name)
 (1,112,388,'addr',9,400),             # ____ (Property Address)
 (1,52,296,'qualifier',9,135),         # I, ____ am certified as a roofing contractor
 (1,40,283,'license',9,115),           # (License No. ____)
]
# Scanned-image forms (no text layer) — coords measured by eye from grid renders & visually verified.
_PBC_HYP02_AFFIDAVIT=[
 (0,112,458,'contractor',9,300),(0,112,438,'contractor_addr',9,300),(0,112,418,'owner',9,300),
 (0,112,398,'addr',9,300),(0,52,322,'qualifier',9,140),(0,40,309,'license',9,120),
]
_PBC_LP03_AFFIDAVIT=[   # Lake Park Roof Metal Sheathing Affidavit (affidavit on page 2)
 (1,112,469,'contractor',9,300),(1,112,456,'contractor_addr',9,300),(1,112,443,'owner',9,300),
 (1,112,430,'addr',9,300),(1,52,287,'qualifier',9,150),(1,100,275,'license',9,120),
]
_PBC_LP04_AFFIDAVIT=[   # Lake Park TinTag Sheathing Affidavit (page 2): only License # + "I, ___ do hereby affirm"
 (1,155,497,'license',9,150),(1,135,467,'qualifier',9,160),
]
_PBC_HYP01_APP=[        # Hypoluxo Permit Application (page 1): right-column contractor block
 (0,360,600,'contractor_addr',7,230),(0,358,540,'qualifier',8,210),(0,378,518,'license',8,110),(0,358,490,'owner',8,210),
]
# Delray Beach FBC 706.8 "ROOF MITIGATION" disclosure (flattened, rev. 7/30/2025).
# Audit 2026-07-20: shipped with NO option selected and no owner block - i.e. an
# unanswered mandatory disclosure. The real APPROVED Calvagno job (R-26019, permit
# BLDR-014053-2026) satisfied it by selecting the "cost of roof-to-wall connections at
# gable ends or all corners cannot be completed for 15% of the cost of roof replacement,
# therefore no roof-to-wall connections will be completed" option - verbatim option (b)
# on the current revision - and attaching the cost estimate (Permit__Mitigation Job
# Cost.pdf in that same approved folder). We pre-select (b) and fill the owner's printed
# name + property address. Owner SIGNATURE and DATE stay blank: the owner executes it.
_DEL_ROOF_MITIGATION=[
 (0,  43, 276, 'x',      11,  14),  # option (b) checkbox
 (0, 100,  84, 'owner',   9, 195),  # Owner: Print  (on the rule above the label)
 (0, 311,  84, 'street',  9, 210),  # Address       (street only, as on the approved job)
]
_PBC_STAMP_FORMS={
 'DEL_Roof_Mitigation.pdf': _DEL_ROOF_MITIGATION,
 'BB_ReRoof_Affidavit_Application.pdf': _PBC_REROOF_AFFIDAVIT,
 'HYP_02_Roofing_Contractor_Affidavit.pdf': _PBC_HYP02_AFFIDAVIT,
 'LP_03_Roof_Metal_Sheathing_Affidavit.pdf': _PBC_LP03_AFFIDAVIT,
 'LP_04_TinTag_Sheathing_Affidavit.pdf': _PBC_LP04_AFFIDAVIT,
 'HYP_01_Permit_Application.pdf': _PBC_HYP01_APP,
}
# Merge auto-generated coordinate maps (built by gen_stamp_maps.py from PyMuPDF word boxes) for the
# flattened city forms. Explicit maps above win; generated maps fill the rest.
try:
    from stamp_maps import PBC_STAMP_MAPS as _GEN_STAMP_MAPS
    for _gk, _gv in _GEN_STAMP_MAPS.items():
        _PBC_STAMP_FORMS.setdefault(_gk, [tuple(t) for t in _gv])
except Exception:
    pass
def _pbc_supp_values(client, ahj, contractor=None):
    city=_city(client, ahj)
    addr=', '.join([s for s in [client.get('address',''), city, ('FL '+client.get('zip','')) if client.get('zip','') else ''] if s])
    c=contractor or {}
    _ca=', '.join(x for x in [c.get('address',''), c.get('city',''), ((c.get('state') or 'FL')+' '+c.get('zip','')).strip() if c.get('zip') else ''] if x)
    return {'contractor':_C(contractor,'name'),'company':_C(contractor,'name'),
            'contractor_addr':_ca,
            'owner':client.get('owner',''),'addr':addr,'street':client.get('address',''),
            'qualifier':_C(contractor,'qualifier'),'license':_C(contractor,'license'),
            'x':'X'}   # generic checkbox mark for coordinate-stamped (flattened) forms

# --- "Roof Construction Specifications Sheet" fill (e.g. RPB_Roof_Spec_Sheet) ---
# The PBC city "Roof Construction Specifications Sheet" is a real fillable AcroForm
# (ROOF HEIGHT / RIDGE VENTING / DECKING / SECONDARY WATER BARRIER / UNDERLAYMENT /
# INSULATION / DECK FASTENER & SPACING / CAP SHEET / ROOF COVERING / DRIP EDGE /
# ROOF SLOPE / TOTAL ROOF AREA + MARK-ONE / ROOF CATEGORY / ROOF TYPE checkboxes).
# Previously it fell through the city-supplemental loop with only the contractor fill,
# so every non-Boca packet emitted it BLANK. We now fill the system spec + job roof data.
#
# The per-system component values below are the reference installer's documented standards, lifted
# verbatim from the reference installer's pre-filled _System_Templates / _System_Spec_Sheets (NOT invented).
# RIDGE VENTING is intentionally blank in the reference installer's templates (job-specific) so we leave it
# blank. Roof Covering / Underlayment are derived from the live system dict so the chosen
# product / underlayment flow through.
# decking / ridge / deck_fastener values calibrated to the reference installer's APPROVED permit R-25117 (Fawcett,
# Metal): Ridge Venting "N/A", Decking "19/32\" CDX Plywood", Deck Fastener "8d Ring Shank Nails, 6\" o.c.".
SPEC_SHEET={
 'Shingle':{'decking':'19/32" CDX Plywood','swb':'Polystick IR-XE (self-adhered, entire deck)','insulation':'N/A',
            'deck_fastener':'8d Ring Shank Nails, 6" o.c.','cap':'N/A','drip':'Aluminum drip edge','ridge':'N/A',
            'covering':'OC TruDefinition Duration shingle FL10674','category':'shingles','markone':'sloped'},
 'Tile':{'decking':'19/32" CDX Plywood','swb':'Polystick TU Plus','insulation':'N/A',
         'deck_fastener':'8d Ring Shank Nails, 6" o.c.','cap':'N/A','drip':'Aluminum drip edge','ridge':'N/A',
         'covering':'Westlake Saxony 900 flat conc tile FL7849 (AH-160 foam)','category':'other','markone':'sloped'},
 'Metal':{'decking':'19/32" CDX Plywood','swb':'Polystick MTS (self-adhered, entire deck)','insulation':'N/A',
          'deck_fastener':'8d Ring Shank Nails, 6" o.c.','cap':'N/A','drip':'26ga metal drip edge','ridge':'N/A',
          'covering':'Dynamic DM Class 1500 24ga standing seam FL41724.08','category':'metal','markone':'sloped'},
 'Flat':{'decking':'19/32" CDX Plywood','swb':'Polyglass SA 2-ply Mod-Bit FL1654','insulation':'N/A',
         'deck_fastener':'8d Ring Shank Nails, 6" o.c.','cap':'Polyflex SA P FR cap','drip':'26ga metal drip edge','ridge':'N/A',
         'covering':'Polyglass SA 2-ply SA mod-bit FL1654','category':'membrane','markone':'flat'},
}
# --- Section C (Low & Steep Sloped Roof System) wind-attachment fill -------------
# Audit 2026-07-20: the Boca_06 Section C attachment block was filled for Flat only;
# Shingle / Tile / Metal shipped with head lap, fastener counts, clip spacing, hook
# strip and drip edge blank even on fully-known jobs.
#
# Every value below is lifted VERBATIM from the reference installer's pre-filled Boca templates
# (Permit_Library/.../Boca_Raton/_System_Templates/<system>/
#  Boca_Supplemental_Roofing_<system>_TEMPLATE.pdf, page 4 = Section C). Nothing is
# invented: each string was read back off the template's text layer and matched to the
# form widget it sits on by rectangle.
#
# DELIBERATELY LEFT BLANK (do not "complete" these):
#   * Zone 1 / Zone 2 / Zone 3 minimum design wind pressures  -> require a sealed
#     Florida PE calculation (RAS 127/128, ASCE 7-22). PE-seal rule.
#   * Metal Perimeter / Corners clip spacing -> wind-zone dependent, same PE rule
#     (the library's template fills Field only, and leaves these two blank).
#   * Ridge Vent Product Approval # and Skylight -> job-specific accessories, blank in
#     the reference installer's templates because they are not on every job.
#   * Shingle / Tile drip edge, and Tile head lap -> blank in the library's approved
#     templates; guessing a size/spacing here would be inventing permit content.
_SECTION_C={
 'Shingle':{'Head lap in inches':'2"',
            'Number of fasteners per shingle':'6 nails'},
 'Tile':{'Number of patties and size':'2 paddies / per RAS 120'},
 'Metal':{'Field':'16"',                                   # clip/fastener spacing, field
          'Hook stripcleat Ga Or Weight':'24 ga',
          'Drip Edge  Size  Fastener Spacing':'2x2 24ga, ring shank 4" o.c.'},
}

# checkbox slot -> field name (these widget names are NOT garbled / are stable on the PBC form)
_SPEC_CB={'lowslope':'Check Box1','flat':'Check Box2','sloped':'Check Box3',
          'builtup':'Check Box4','shingles':'Check Box5','metal':'Check Box6',
          'membrane':'Check Box7','other':'Check Box8',
          'newroof':'Check Box9','reroof':'Check Box10','recover':'Check Box11','repair':'Check Box12'}

def _spec_norm(s):
    """Normalize a field label for matching. The PBC form's embedded font drops the
    letters A, B and C from /T names (DECKING->'DE KING', UNDERLAYMENT->'UNDERLYMENT',
    BARRIER->'RRIER'), so we strip non-letters then remove A/B/C from BOTH sides
    before comparing. (Verified collision-free across this form's 11 text labels.)"""
    s=''.join(ch for ch in str(s or '').upper() if 'A'<=ch<='Z')
    return s.replace('A','').replace('B','').replace('C','')

# expected label -> spec slot key (matched against actual /T names via _spec_norm)
_SPEC_TEXT_LABELS=[('ROOF HEIGHT','height'),('RIDGE VENTING','ridge'),('DECKING TYPE','decking'),
 ('SECONDARY WATER BARRIER TYPE','swb'),('UNDERLAYMENT','underlayment'),('INSULATION','insulation'),
 ('DECK FASTENER AND SPACING','deck_fastener'),('CAP SHEET','cap'),('ROOF COVERING','covering'),
 ('DRIP EDGE','drip'),('TOTAL ROOF AREA SQUARE FEET','area')]

def _is_spec_sheet(fields):
    """True when an AcroForm field set looks like the Roof Construction Specifications Sheet."""
    if not fields: return False
    norm={_spec_norm(k) for k in fields}
    have=lambda lbl: _spec_norm(lbl) in norm
    return have('ROOF HEIGHT') and (have('DRIP EDGE') or have('ROOF COVERING'))

def _spec_markone(system, slope):
    """MARK-ONE slope bucket from the job slope (x/12): <2 or Flat system -> flat,
    2-4 -> low slope, >4 -> sloped. Unknown slope (non-Flat) -> the reference installer template default."""
    s=str(slope or '').strip()
    sn=None
    if s:
        try: sn=float(s.split('/')[0].split(':')[0])
        except Exception: sn=None
    if system=='Flat' or (sn is not None and sn<2): return 'flat'
    if sn is None: return SPEC_SHEET.get(system,{}).get('markone','sloped')
    if sn<=4: return 'lowslope'
    return 'sloped'

def _spec_sheet_fill(form_path, client, ahj, system, sysd):
    """Build (T, B) AcroForm fill dicts for the Roof Construction Specifications Sheet,
    keyed by the form's ACTUAL field names. Returns None if it isn't that form.
    Missing job values are simply omitted (left blank), never printed as 'None'."""
    spec=SPEC_SHEET.get(system)
    if not spec: return None
    try: flds=PdfReader(form_path).get_fields()
    except Exception: flds=None
    if not _is_spec_sheet(flds): return None
    names=list(flds.keys())
    norm_to_name={}
    for n in names: norm_to_name.setdefault(_spec_norm(n), n)
    def field_for(label):
        return norm_to_name.get(_spec_norm(label))
    # job-level roof data (blank when absent)
    mrh=str(client.get('mrh') or '').strip()
    # append a foot-mark only when the value is a bare number (e.g. "15" -> "15'"); leave
    # already-annotated values like "15ft mean" or "15'" untouched.
    if mrh and re.fullmatch(r"\d+(\.\d+)?", mrh): mrh+="'"
    slope=str(client.get('slope') or '').strip()
    area=str(client.get('area') or '').strip()
    # Roof covering: the reference installer's compact template string for the default product (fits the narrow
    # box); derive from the live system dict only when a non-default product was swapped in.
    pk=sysd.get('product_key')
    if pk and pk!=DEFAULT_PRODUCT.get(system):
        covering=' '.join([s for s in [sysd.get('prod',''),sysd.get('prodfl','')] if s])
    else:
        covering=spec['covering']
    underlayment=' '.join([s for s in [sysd.get('ul',''),sysd.get('ulfl','')] if s])
    vals={'height':mrh,'ridge':spec['ridge'],'decking':spec['decking'],'swb':spec['swb'],
          'underlayment':underlayment,'insulation':spec['insulation'],'deck_fastener':spec['deck_fastener'],
          'cap':spec['cap'],'covering':covering,'drip':spec['drip'],'area':area}
    T={}
    for label,slot in _SPEC_TEXT_LABELS:
        fn=field_for(label); v=str(vals.get(slot,'') or '').strip()
        if fn and v: T[fn]=v
    # ROOF SLOPE -> the small unlabeled text box (named 'undefined' on the PBC form)
    if slope:
        assigned=set(T.keys())
        slope_fn=None
        for n in names:
            if n in assigned: continue
            if str(n).lower()=='undefined' or 'SLOPE' in str(n).upper(): slope_fn=n; break
        if slope_fn: T[slope_fn]=slope
    # checkboxes: MARK ONE (slope bucket), Roof Category (by system), Roof Type (re-roof)
    B={}
    def check(slot):
        fn=_SPEC_CB.get(slot)
        if fn and fn in flds: B[fn]='Yes'
    check(_spec_markone(system, slope))
    check(spec['category'])
    check('reroof')   # the reference installer scope is always a re-roof (system desc == 'REROOF - ...')
    return T, B

# --- PBC "FORM 200 - REROOFING INSTALLATION SUMMARY (Concrete or Clay Tile)" ------
# Audit 2026-07-20: this form rendered 100% blank on every Tile packet. _is_spec_sheet()
# is tuned to the city "Roof Construction Specifications Sheet" field names and does not
# match Form 200, so the form fell through to the generic contractor-only fill.
#
# Form 200 ships in TWO field-name dialects across the library, verified widget-by-widget:
#   "pbc"  (PBC_04 / Tequesta / Village_of_Golf): site address = 'Site Address'
#   "pbg"  (PBG / North_Palm_Beach):              site address = 'CONCRETE or CLAY TILE'
# Pages 4-5 of the same PDF are the PBO-94 "Mandated Retrofits of Roof-to-Wall
# Connection" form (mandatory on every wood-deck reroof) - filled here too.
_F200_PBC={'site':'Site Address','tile_pa':'Product Approval 4','foam_pa':'Product Approval 5',
           'qualifier':'Qualifier Name','sqrs':None,   # name collides with the LPZ box - see below
           'ply':'Single or Double Ply','ply_single':'Single Ply','ply_double':'Double Ply',
           'basesheet_attach':'Base Sheet or Cap Sheet Type'}
_F200_PBG={'site':'CONCRETE or CLAY TILE','tile_pa':'Product Approval Row1','foam_pa':'FL Product Approval',
           'qualifier':'FULLY provided ALL the information requested',
           # sqrs was 'undefined' - an unnamed widget whose /T resolves DOCUMENT-WIDE, so the
           # roof area was also stamped onto the PBO-94 "Exterior Wall Construction: Other
           # explain" line, making the form declare the wall construction to be e.g. "29.4".
           # Re-audit 2026-07-21 caught it. Leave blank rather than write into the wrong box;
           # an unfilled SQRs line is a gap, a wrong wall-construction answer is a false
           # statement on a permit application.
           'sqrs':None,
           'ply':None,'ply_single':'Single Ply','ply_double':'Double Ply','basesheet_attach':None}

def _is_form200(fields):
    """True when an AcroForm field set is the PBC Concrete/Clay Tile Form 200."""
    if not fields: return False
    return 'Sloped Roof Pitch' in fields and ('Sloped Roof Area SQRs' in fields or 'Ft' in fields)

def _sqrs(area):
    """Roof area in squares from the caller-supplied square-foot figure (Form 200 asks
    for SQRs, the caller supplies sf). Pure arithmetic on supplied data - not a new fact."""
    try: n=float(re.sub(r'[^0-9.]','',str(area or '')))
    except Exception: return ''
    if not n: return ''
    s='%.1f'%(n/100.0)
    return s[:-2] if s.endswith('.0') else s

def _form200_fill(form_path, client, ahj, system, sysd, contractor=None):
    """(T, B) fill dicts for PBC Form 200 + its PBO-94 roof-to-wall retrofit pages.
    Returns None if the form isn't Form 200, or if the system isn't Tile (Form 200 is
    the CONCRETE or CLAY TILE form - it must not be pre-filled for another system).

    LEFT BLANK ON PURPOSE - these need a sealed Florida PE calculation (RAS 127 /
    FRSA-TRI uplift moments) and MUST NOT be machine-filled:
      * Design Pressures LPZ / HPZ
      * Paddy Size, Paddy Weight (g), Moment Resistance / Allowable Moment Resistance
    Also blank because the value is not supplied and must not be guessed: roof style
    (gable/hip), structure type, the supplemental-attachment checkboxes (they assert what
    is physically attached), the base-sheet Product Approval # / System columns (the two
    dialects name those boxes inconsistently, so a value could land in the wrong column),
    and every PBO-94 question about building age / value / chosen compliance path.
    """
    if system!='Tile': return None
    try: flds=PdfReader(form_path).get_fields()
    except Exception: flds=None
    if not _is_form200(flds): return None
    M=_F200_PBG if 'CONCRETE or CLAY TILE' in flds else _F200_PBC
    city=_city(client, ahj)
    addr=', '.join([s for s in [client.get('address',''), city,
                                ('FL '+client.get('zip','')) if client.get('zip','') else ''] if s])
    slope=str(client.get('slope') or '').strip()
    mrh=str(client.get('mrh') or '').strip()
    T={}
    def put(name, val):
        if name and name in flds and str(val or '').strip(): T[name]=str(val).strip()
    put(M['site'], addr)
    put('Sloped Roof Pitch', slope)
    put('Ft', mrh)
    # Roof area in squares. On the "pbc" dialect the SQRs widget shares its /T name with
    # the LPZ design-pressure widget, so writing it would ALSO stamp a number into a
    # PE-sealed pressure box. Refuse rather than do that; it stays blank on that dialect.
    put(M['sqrs'], _sqrs(client.get('area')))
    # ROOF TILE row + the foam-adhesive attachment column (the reference installer sets tile in adhesive)
    put('ManufacturerRow1', sysd.get('mfr'))
    put('Product NameRow1', sysd.get('prod'))
    put('Material TypeRow1', 'Concrete Tile')
    put(M['tile_pa'], sysd.get('prodfl'))
    put(M['foam_pa'], sysd.get('adhfl'))
    # BASE SHEET / underlayment type (the PA#/System columns stay blank - see docstring)
    put('Base Sheet', ' '.join([s for s in [sysd.get('ul',''), sysd.get('ulfl','')] if s]))
    put(M['qualifier'], _C(contractor,'qualifier'))
    # --- PBO-94 (pages 4-5): identification only. Everything that decides the retrofit
    # obligation (build date, valuation, exception, compliance path, existing strap
    # details) is left blank for the qualifier/owner to complete.
    put('Address', addr)
    put('Qualifier or Owner/Builder Name (print)', _C(contractor,'qualifier'))
    put('Qualifier or OwnerBuilder Name Print_2', _C(contractor,'qualifier'))
    B={}
    ulkey=(sysd.get('ul','') or '')
    _double='2-ply' in ulkey.lower() or '2ply' in ulkey.lower()
    if M['ply'] and M['ply'] in flds:
        B[M['ply']]='Double Ply' if _double else 'Single Ply'
    else:
        k=M['ply_double'] if _double else M['ply_single']
        if k in flds: B[k]='On'
    # Polystick TU-Plus is a self-adhered base sheet (from UNDERLAYMENTS/SYSTEMS, not invented)
    if M['basesheet_attach'] and M['basesheet_attach'] in flds:
        B[M['basesheet_attach']]='Self Adhered'
    return T, B


# --- PBC "FORM 100 - REROOFING INSTALLATION SUMMARY (ASPHALT SHINGLES / WOOD SHAKES)" ---
# Audit 2026-07-21: Forms 100/300/400 were installed 2026-07-20 but only 200/400 had fill
# functions, so every Shingle and Metal packet across all 40 PBC AHJs shipped its mandated
# county installation summary materially blank (no pitch, mean roof height, area, underlayment
# method or product spec). This adds 100 + 300.
#
# ONE DIALECT ONLY. Unlike Form 200 (five city dialects: PBC/PBG/NPB/PS/TEQ/VG), Forms
# 100/300/400 exist in the library solely as the county master in
# Palm_Beach_County/01_Permit_Application_Packet/ - verified by enumerating every
# Form_?[1234]00 PDF under Permit_Library. No city republishes them, so there is
# no second field-name dialect to guard against.
#
# WIDGET-UNIQUENESS RULE (the 2026-07-21 severity-1 lesson): fill_form() resolves a /T name
# DOCUMENT-WIDE, so a name carried by more than one widget stamps ALL of them. Every text
# field written below was confirmed to be carried by exactly ONE widget, on the expected
# page, at the expected rectangle. Duplicated names are left blank on purpose - see below.
# Checkboxes are the exception: duplicate-named /Btn widgets here carry DISTINCT /AP
# on-states, and fill_form's `str(B[name])==on` guard selects exactly the matching widget.
#
# CALIBRATED against the approved packet Palm_Beach_Gardens_Shingle_R24311
# ("Permit__kim vo raw.pdf", a live AcroForm whose 50 field names match this blank exactly),
# permit issued 11/13/2024. What the human actually entered:
#   SITE ADDRESS='11138 CURRY DR PALM BEACH GARDENS FL 33418'   Sloped Roof Pitch='8'
#   Ft='15'   Sloped Roof Area SQRs='11'   Underlayment Options='/Choice B'
#   Product Approval 2='FL12328-R10 & FL17084-R10'  (the UNDERLAYMENT approval line)
#   ManufacturerRow1='GAF'  Product NameRow1='DRIFTWOOD'  Material TypeRow1='HDZ'
#   Product Approval Row1='FL10124-R35'   Qualifier Name='<qualifier>'
# -> confirms 'Ft' is Mean Roof Height (label prints "Mean Roof Height: ___ Ft") and that
#    'Sloped Roof Area SQRs' takes SQUARES, not square feet.
#
# LEFT BLANK ON PURPOSE:
#   * 'Product Approval' (p0 rect y459-489) - this is the SKYLIGHTS/VENTS replacement
#     approval line, NOT the roof product. Near-identical name to 'Product Approval 2'
#     (the underlayment line, y416-443); distinguished by rectangle against the printed
#     labels. Skylight scope is job-specific, so it stays blank.
#   * 'Main House/Duplex or Accessory Structure' - asserts which structure is being
#     re-roofed. Not carried in `client`; a detached-garage re-roof is a real scenario.
#     (NB: _form400_fill sets the analogous 'Main House or Duplex'. Left as found -
#     flagged in audit_reports/10_FORM100_300_AND_LEGAL.md rather than changed here.)
#   * 'previous permit number', 'Straps / Anchor', 'Fasteners', 'Fasteners_2',
#     'Manufacturer', 'Type Model' (p3) - these are the PBO-94 EXISTING roof-to-wall
#     strap observations, not the reference installer's new-work spec.
#   * 'Other (Explain)' (p3 y612) - the PBO-94 "Exterior Wall Construction: Other explain"
#     line. THIS IS THE FIELD THE 2026-07-21 DEFECT WRONGLY STAMPED. Never written.
#   * 'Roof Geometry' / 'Roof Geometry Other (Explain)', 'Exterior Wall Construction' -
#     observations of the existing structure we do not hold.
#   * Every PBO-94 question about build date / valuation / exception / compliance path,
#     and all signature + date widgets - the qualifier completes and wet-signs those.
#   * No design-pressure or uplift field exists on Form 100, so the PE-seal rule has
#     nothing to suppress here; it is enforced by never adding one.
_F100_PRODUCT_MATERIAL={'Shingle':'Asphalt Shingle'}


def _is_form100(fields):
    """True only for the PBC Asphalt Shingle Form 100.

    Deliberately keyed to fields NO other county roofing form carries. An earlier
    candidate ('Material TypeRow1' + 'Sloped Roof Area SQRs' + 'Product Approval 2')
    was rejected because it ALSO matched Form 200 in the PBC/Tequesta/Village_of_Golf
    dialects. Verified exclusive against Forms 200 (all 5 dialects), 300 and 400."""
    if not fields: return False
    return ('Underlayment Options' in fields and 'Material TypeRow1' in fields
            and 'Roof Geometry Other (Explain)' in fields)


def _form100_fill(form_path, client, ahj, system, sysd, contractor=None):
    """(T, B) for PBC Form 100. None unless this is Form 100 AND the system is Shingle -
    Form 100 is the ASPHALT SHINGLE form and must never be pre-filled for another system."""
    if system!='Shingle': return None
    try: flds=PdfReader(form_path).get_fields()
    except Exception: flds=None
    if not _is_form100(flds): return None
    addr=', '.join([s for s in [client.get('address',''), _city(client, ahj),
                                ('FL '+client.get('zip','')) if client.get('zip','') else ''] if s])
    T={}
    def put(name, val):
        if name and name in flds and str(val or '').strip(): T[name]=str(val).strip()
    # --- page 1: the installation summary itself
    put('SITE ADDRESS', addr)
    put('Sloped Roof Pitch', str(client.get('slope') or '').strip())
    put('Ft', str(client.get('mrh') or '').strip())          # label: "Mean Roof Height: ___ Ft"
    put('Sloped Roof Area SQRs', _sqrs(client.get('area')))  # form asks SQRs; caller supplies sf
    # UNDERLAYMENT product approval # (the line above the A/B/C method table)
    put('Product Approval 2', sysd.get('ulfl'))
    # PRODUCT Specifications row
    put('ManufacturerRow1', sysd.get('mfr'))
    put('Product NameRow1', sysd.get('prod'))
    put('Material TypeRow1', _F100_PRODUCT_MATERIAL.get(system))
    put('Product Approval Row1', sysd.get('prodfl'))
    put('Qualifier Name', _C(contractor,'qualifier'))
    # --- PBO-94 (pages 3-4): identification only, mirroring _form200_fill's policy.
    put('Address', addr)
    put('Qualifier or Owner/Builder Name (print)', _C(contractor,'qualifier'))
    put('Qualifier or OwnerBuilder Name Print_2', _C(contractor,'qualifier'))
    B={}
    # UNDERLAYMENT Method: Choice A = "Self-Adhered (Direct to Deck) ... Polymer-Modified
    # Bitumen Underlayment applied directly to entire roof deck". Every the reference installer shingle
    # underlayment in UNDERLAYMENTS['Shingle'] is Polystick IR-XE, a self-adhered PMB sheet,
    # and SPEC_SHEET already declares swb_box='1 The entire roof deck' for it - so this
    # restates an existing reviewed the reference installer constant rather than asserting a new fact.
    # (The R24311 human chose Choice B, but that was a different assembly - GAF over
    # FL12328/FL17084 - so we mirror HOW the field is filled, not their product.)
    # On-state 'Choice A' exists only on the page-1 widget; the three page-2 detail widgets
    # use 'Option A/B/C' and stay untouched - the approved copy left them blank too.
    if 'Underlayment Options' in flds: B['Underlayment Options']='Choice A'
    return T, B


# --- PBC "FORM 300 - REROOFING INSTALLATION SUMMARY FORM - METAL ROOFING" ----------
# CALIBRATED against the approved packet PBC_Metal_Rothstein_R25183
# ("Permit__2-Roof Assembly Worksheet.pdf", flattened - values read from page text),
# permit B-2025-042262-0000. What the human actually entered:
#   SITE ADDRESS='13296 80TH LN N'  Sloped Roof Pitch='5'  Mean Roof Height='11'
#   Sloped Roof Area SQRs='3866'    Manufacturer='Dynamic Metals'
#   Product Name='DM CLASS 1500'    Panel Type='Mechanical Seam'
#   Product Approval #='41724.10'   psf='-219' (x2)  Fastener/Clip Spacing='4" From Ends'
#   / '12" OC'  Screws='#10X1 Pancake Fasteners 2 per clip'  Qualifier='<qualifier>'
#
# NOTE - we deliberately do NOT copy that packet's Sloped Roof Area value. '3866' with a
# pitch of 5/12 and an 11ft mean roof height is 3,866 SQUARE FEET (38.66 squares) entered
# into a box the form labels "Sloped Roof Area (SQRs)". The printed label, the approved
# Form 100 (which used 11 = squares), and the existing _sqrs() convention shared by
# _form200_fill/_form400_fill all agree on squares, so we emit squares. Reported as a
# defect found in that approved packet.
#
# LEFT BLANK ON PURPOSE:
#   * 'psf' (x2) - "DESIGN WIND UPLIFT Pressure" and "Max. Allowed Pressure (Product
#     Approval)". PE-SEAL RULE. Also a duplicated name, so unwritable safely regardless.
#   * 'Product Approval Number' - DUPLICATED across two unrelated lines: the SKYLIGHT
#     approval (p0 y513) and the UNDERLAYMENT approval (p0 y464). fill_form would stamp
#     BOTH, declaring the underlayment FL# to be the skylight approval. The underlayment
#     FL# therefore cannot be placed on Form 300 and is left to the qualifier. (This is a
#     real gap vs Form 100, where the same value has a uniquely-named box.)
#   * 'Fastener Type Description', 'Fastener/Clip Spacing', 'Screws (size & Quantity',
#     and the 'Fastener Type'/'Clips' checkboxes - the panel attachment schedule is
#     selected FROM the design pressure (the approved copy pairs 12" OC with -219 psf).
#     Pressure-derived => PE territory, and not carried in `sysd`.
#   * 'Design Wind Uplift' checkbox - certifies the sealed uplift calculation is attached.
#   * 'Type of Structure  2' - both widgets would need distinct targeting AND it asserts
#     main-house vs accessory; not carried in `client`.
#   * 'Previous Permit Number', 'Other' (p4 - the PBO-94 "Other explain" wall-construction
#     line, the 2026-07-21 defect field), 'Other_2', 'StrapAnchor', 'Fasteners',
#     'Fasteners_2', 'Manufacturer4', 'Type Model' - existing-structure observations.
#   * All PBO-94 build-date / valuation / exception / compliance answers and signatures.
_F300_PANEL_TYPES=(('mechanical seam','Mechanical Seam'),('mech seam','Mechanical Seam'),
                   ('standing seam','Standing Seam'))


def _is_form300(fields):
    """True only for the PBC Metal Form 300. Verified exclusive against Forms 100,
    200 (all 5 dialects) and 400."""
    if not fields: return False
    return ('Mean Roof Height' in fields and 'Panel Type' in fields
            and 'Product Approval #' in fields)


def _panel_type(sysd):
    """Panel type read out of the library's product string (e.g. 'DM Class 1500,
    1.5" Mech Seam, 16" wide' -> 'Mechanical Seam'). Returns '' when the product string
    does not name a seam type - we do not guess one. Calibrated against R25183, whose
    approved packet used this identical Dynamic Metals panel and wrote 'Mechanical Seam'."""
    s=(sysd.get('prod','') or '').lower()
    for token,label in _F300_PANEL_TYPES:
        if token in s: return label
    return ''


def _form300_fill(form_path, client, ahj, system, sysd, contractor=None):
    """(T, B) for PBC Form 300. None unless this is Form 300 AND the system is Metal."""
    if system!='Metal': return None
    try: flds=PdfReader(form_path).get_fields()
    except Exception: flds=None
    if not _is_form300(flds): return None
    addr=', '.join([s for s in [client.get('address',''), _city(client, ahj),
                                ('FL '+client.get('zip','')) if client.get('zip','') else ''] if s])
    T={}
    def put(name, val):
        if name and name in flds and str(val or '').strip(): T[name]=str(val).strip()
    # --- page 1: the installation summary itself
    put('Site Address', addr)
    put('Sloped Roof Pitch', str(client.get('slope') or '').strip())
    put('Mean Roof Height', str(client.get('mrh') or '').strip())
    put('Sloped Roof Area SQRs', _sqrs(client.get('area')))
    # METAL PANEL SPECIFICATIONS row. 'Product Approval #' (with the hash) is the panel
    # approval and is unique; the hash-less 'Product Approval Number' is the duplicated
    # skylight/underlayment pair and is never written - see the note above.
    put('Manufacturer', sysd.get('mfr'))
    put('Product Name', sysd.get('prod'))
    put('Panel Type', _panel_type(sysd))
    put('Product Approval #', sysd.get('prodfl'))
    put('Qualifier Name', _C(contractor,'qualifier'))
    # --- PBO-94 (pages 4-5): identification only.
    put('Address', addr)
    put('Qualifier or Owner/Builder Name (print)', _C(contractor,'qualifier'))
    put('Qualifier or OwnerBuilder Name Print_2', _C(contractor,'qualifier'))
    B={}
    # UNDERLAYMENT Method. Form 300 names this group 'Underlayment' (page 1) with explicit
    # on-states; the page-2 detail widgets are the separate 'Underlayment Options' group and
    # stay untouched. Every the reference installer metal underlayment (Polystick MTS, 1- or 2-ply) is a
    # self-adhered sheet applied to the deck - SPEC_SHEET swb_box='1 The entire roof deck'.
    if 'Underlayment' in flds: B['Underlayment']='Self Adhered'
    return T, B


# --- PBC "EXPEDITED RE-ROOF WORKSHEET - SFD only" (PB-O-131 Attachments A + B) ----
# County-only form. It does NOT appear in any of the 39 municipal packet folders and it
# is NOT part of the Form 100/200/300/400 series; it is the Building Division's expedited
# single-family reroof route (the alternative to the PB-O-094 route). Sourced unmodified
# from https://discover.pbcgov.org/pzb/building/Forms/Expedited_Reroofing_Checklist_and_
# Worksheet.pdf - the same Forms/ directory that a re-download of Form 200 proves the rest
# of the library came from (SHA-256 byte-identical). Verified against the APPROVED
# unincorporated job R-25216 (Marrero, permit B-2026-003128-0000), whose packet contains
# this exact 4-page document: Attachment A worksheet, Attachment B checklist (rev 1-9-25),
# and the two ASCE 7-22 design-pressure tables.
#
# The PDF is 4 pages: 0 = Attachment A (worksheet, fillable), 1 = Attachment B (checklist,
# 6 initial lines), 2-3 = design-pressure reference tables (not fillable).
#
# DELIBERATELY LEFT BLANK - do not "complete" these:
#   * Initials / Initials2..6 (Attachment B) - each is the QUALIFIER certifying a fact
#     about the structure we do not hold (build date after 1-1-1987, improved value
#     < $300,000, like-for-like, no skylights, no solar). Machine-initialing a
#     certification is forging a certification. The qualifier initials by hand.
#   * 'I certify' checkbox, Qualifier's Signature, Date - wet-signed.
#   * 'Existing Deck ...' - an observation of the EXISTING structure, not a the reference installer standard.
#   * Underlayment Choice 7/8/9 ('Attachment per Product Approval / FRSA 7th / Engineers
#     Design') - asserts which document governs tile attachment; the qualifier picks it.
#   * Check Box1/2/3 (Electrical / Mechanical / Plumbing sub-permits) - scope-dependent.
#   * Everything on pages 2-3: those are ASCE 7-22 design-pressure tables. PE-seal rule.
_EXP_PROPOSED={'Tile':'Proposed Type Tile','Shingle':'Shingle_2','Metal':'Metal_2','Flat':'Flat_2'}
_EXP_EXISTING={'Tile':'Existing Roof Covering Tile','Shingle':'Shingle','Metal':'Metal','Flat':'Flat'}


# --- PB-O-131 DESIGN CRITERIA: the county's own supplied chart ------------------
# The worksheet's two DESIGN CRITERIA lines read:
#     1) Indicate required wind pressures from supplied chart
#     2) Indicate wind pressures from proposed system  (This must be >/= to above)
# "supplied chart" is page 3 of the county's OWN PB-O-131 packet ("DESIGN PRESSURES FOR
# UNDERLAYMENT AND RIDGE ATTACHMENT ... PER ASCE 7-22 (psf)"). So line 1 is a TABLE
# LOOKUP, not a sealed PE calculation, and line 2 is transcribed from the product
# approval we already carry in sysd['fl_mdp'].
#
# Values below were read off a 150-dpi render of that page and cross-checked against the
# scanned copy bound into the accepted Tota permit (page 8). NB Table 1-G (Gable) carries
# the HIGHER pressures and Table 1-H (Hip) the LOWER ones - gable ends see more uplift.
# (A briefing note reached this engine with those two columns transposed; the render is
# authoritative and is what is encoded here.)
#
# STILL BLANK, NO EXCEPTIONS - none of this touches them: Zone 1'/1/2/3, P1'-P3,
# RAS-127/128, and the Table 2 GC/2 HC tile moment values (Ma / LPZ / HPZ, paddy size
# and weight). Note 3 on the county page defers tile adhesive/mortar uplift to the
# adhesive manufacturer's approval, so tile moments are never auto-filled.
_PBO131_MRH_BANDS=(15,20,30,40,50,60)
_PBO131_PRESSURE={
 'gable':{'B':{15:95.1, 20:95.1, 30:95.1, 40:100.5, 50:107.3, 60:112.7},
          'C':{15:115.5,20:122.3,30:133.1,40:141.3, 50:148.1, 60:153.5},
          'D':{15:139.9,20:146.7,30:157.6,40:165.7, 50:172.5, 60:177.9}},
 'hip':  {'B':{15:68.7, 20:68.7, 30:68.7, 40:72.6,  50:77.5,  60:81.4},
          'C':{15:83.4, 20:88.3, 30:96.1, 40:102.0, 50:106.9, 60:110.9},
          'D':{15:101.0,20:106.0,30:113.8,40:119.7, 50:124.6, 60:128.5}},
}


def _pbo131_required_pressure(exposure, mrh, roof_form, slope):
    """(psf, basis) from the county chart, or (None, why-not).

    Every input must be a REAL value. If exposure, mean roof height or the gable/hip
    roof form is missing or a placeholder, we return None and BOTH design-criteria
    lines stay blank - we never guess a row. The chart itself is scoped to
    "CATEGORY II BUILDINGS HAVING A 3:12 AND GREATER PITCH", so a shallower roof is
    off-chart and also returns None."""
    ex=str(exposure or '').strip().upper()[:1]
    if ex not in ('B','C','D'): return None,'no exposure category on file'
    if roof_form not in _PBO131_PRESSURE: return None,'gable/hip roof form unknown'
    try: h=float(re.sub(r'[^0-9.]','',str(mrh or '')))
    except Exception: h=0.0
    if not h: return None,'no mean roof height on file'
    s=str(slope or '').strip()
    try: sn=float(s.split('/')[0].split(':')[0]) if s else None
    except Exception: sn=None
    if sn is None: return None,'no slope on file'
    if sn<3: return None,'chart applies to 3:12 and greater pitch only'
    band=next((b for b in _PBO131_MRH_BANDS if h<=b), None)
    if band is None: return None,'mean roof height above the 60 ft chart limit'
    psf=_PBO131_PRESSURE[roof_form][ex].get(band)
    if psf is None: return None,'no chart row'
    return psf,'PB-O-131 Table 1-%s (%s), Exp %s, MRH %s ft, 170 Vult'%(
        'G' if roof_form=='gable' else 'H', roof_form.title(), ex,
        '0-15' if band==15 else str(band))


# --- PBC "FORM 400 - REROOFING INSTALLATION SUMMARY FORM-FLAT ROOFING" -----------
# Minimal, deliberately narrow fill. Form 400 has NO approved filled counterpart in
# approved_permit_examples/ (see pending_forms/FORMS_REPORT.md section 3), so there is
# no way to calibrate the semantics of its ambiguous fields against a copy the county
# actually accepted. We therefore fill ONLY the three fields whose meaning is
# unambiguous from their own printed label, and leave everything else to the qualifier.
#
# LEFT BLANK ON PURPOSE:
#   * 'DESIGN WIND UPLIFT Pressure Field (Zone 1)', 'Perimeter/Corner (Zones 2,3)',
#     'Design Uplift Pressure', 'Max Allowable Uplift Pressure', 'Design Pressure',
#     'Max Allowable Pressure' -> sealed Florida PE calculation. PE-seal rule.
#   * The BUR vs SINGLE-Ply blocks: the form carries TWO near-identical field names
#     ('Product Approval  with Design pressures Number' and 'Product Approval with
#     Design pressures  Number'), one per block. Writing the FL# without a verified
#     approved copy risks putting it in the wrong system block, which reads as
#     declaring the wrong roof system. Not worth the risk; the qualifier fills it.
#   * Every checkbox that asserts a site condition (tie-in, re-nailing, recover,
#     moisture survey, pull-test, sheath-over, retrofits).
_F400_KEYS=('DESIGN WIND UPLIFT Pressure Field Zone 1','Roof Area SQRs','SITE ADDRESS')


def _is_form400(fields):
    if not fields: return False
    return all(k in fields for k in _F400_KEYS)


def _form400_fill(form_path, client, ahj, system, sysd, contractor=None):
    """(T, B) for PBC Form 400. None unless this is Form 400 AND the system is Flat."""
    if system!='Flat': return None
    try: flds=PdfReader(form_path).get_fields()
    except Exception: flds=None
    if not _is_form400(flds): return None
    addr=', '.join([s for s in [client.get('address',''), _city(client, ahj),
                                ('FL '+client.get('zip','')) if client.get('zip','') else ''] if s])
    mrh=str(client.get('mrh') or '').strip()
    T={}
    def put(name, val):
        if name in flds and str(val or '').strip(): T[name]=str(val).strip()
    put('SITE ADDRESS', addr)
    put('Roof Area SQRs', _sqrs(client.get('area')))   # form asks SQRs; caller supplies sf
    # NOTHING BLANK (same doctrine as _permit_value, audit 2026-07-20): mean roof height is
    # a measured field dimension, not a PE-derived pressure, so leaving it silently empty
    # reads as "reviewed and empty" rather than "still to be measured". We never guess it.
    put('Roof Height', mrh or 'DATA NEEDED')
    put('Qualifier Name', _C(contractor,'qualifier'))
    # Existing flat system, only when the caller stated it unambiguously. This is a
    # description of what is on the roof today, not a certification - but we still refuse
    # to infer it, because "what is up there now" decides recover-vs-tear-off.
    if _existing_bucket(client.get('existing'))=='Flat':
        put('EXISTING Flat Roof System', client.get('existing'))
    # --- PBO-94 (pages 3-4): identification only, mirroring _form300_fill's policy.
    # On the PB-O-094 route this sheet is no longer an extra - it IS the mandated
    # "Roof Assembly Worksheet" retrofit component (evidence: the ISSUED unincorporated-PBC
    # permit R25183 Rothstein files exactly these pages as "2-Roof Assembly Worksheet").
    # Everything that DECIDES the retrofit - construction date, documented value, the
    # >/< $300K threshold, the claimed exception, the chosen compliance option, and every
    # exterior-wall / roof-shape / strap / fastener observation on page 4 - is a field
    # finding the qualifier signs for, and is deliberately left blank.
    put('Address', addr)
    put('Qualifier or Owner/Builder Name (print)', _C(contractor,'qualifier'))
    put('Qualifier or OwnerBuilder Name Print_2', _C(contractor,'qualifier'))
    B={}
    if 'Main House or Duplex' in flds: B['Main House or Duplex']='On'   # widget on-state is /On
    return T, B


def _is_expedited_worksheet(fields):
    """True when an AcroForm field set is PB-O-131 Attachment A."""
    if not fields: return False
    return 'Address of Structure' in fields and 'Gable Hip  of Squares' in fields


# --- Is the expedited PB-O-131 route even LEGAL for this structure? -------------
# Attachment B makes the contractor certify, by initialling:
#   "This is a detached Single Family Dwelling (SFD) and/or a free-standing
#    residential accessory structure."
# and states: "If unable to certify all of the above statements as true, this will
# disqualify the use of this form. Please refer to PB-O-094 and follow the procedure."
#
# A townhouse, duplex, condo or any multi-family unit is an ATTACHED dwelling and
# cannot make that certification. Emitting PB-O-131 for one asks the qualifier to
# initial a false statement on a permit application - so we refuse to emit it
# silently. PAPA's "Property Use Code" is the signal (observed values:
# Tota 0100-SINGLE FAMILY qualifies; Goren 0110-TOWNHOUSE and DuBois
# 0400-CONDOMINIUM do not).
_PBO131_USE_OK=('SINGLE FAMILY',)
_PBO131_USE_BAD=('TOWNHOUSE','CONDOMINIUM','DUPLEX','MULTIFAMILY','MULTI-FAMILY',
                 'COOPERATIVE','CO-OP','APARTMENT','VILLA')


def pbo131_qualifies(client):
    """(status, detail) for the expedited-route gate.

    'ok'      - PAPA says detached single family; the route is valid.
    'no'      - PAPA says an attached/multi-family use; PB-O-131 is NOT a legal route.
    'unknown' - no usable signal (fetch failed, or a use code we have not classified).

    We only FAIL LOUD on 'no'. On 'unknown' the packet is built as before but carries a
    warning, because refusing to build whenever the Property Appraiser is unreachable
    would take a working offline path away for no evidence-backed reason."""
    # The worksheet's own property-appraiser lookup (propUse) counts too, so an offline build can decide.
    use=(str(client.get('property_use') or '') or (papa_record(client.get('pcn','')) or {}).get('Property Use Code','') or '').upper()
    if not use: return 'unknown','no PBC Property Appraiser record for this PCN'
    for bad in _PBO131_USE_BAD:
        if bad in use: return 'no',use
    for good in _PBO131_USE_OK:
        if good in use: return 'ok',use
    return 'unknown',use


def _existing_bucket(existing):
    """Map the caller's free-text existing roof covering onto one of the form's four
    buckets. Returns None when it isn't unambiguous - we do not guess."""
    s=(existing or '').lower()
    if not s: return None
    hits=[k for k,words in (('Tile',('tile',)),('Shingle',('shingle','shake')),
                            ('Metal',('metal','standing seam')),
                            ('Flat',('flat','mod-bit','modified bitumen','bur','built-up','tpo','rolled')))
          if any(wd in s for wd in words)]
    return hits[0] if len(hits)==1 else None


def _expedited_fill(form_path, client, ahj, system, sysd, contractor=None, pairs=None):
    """(T, B) fill dicts for the PBC Expedited Re-Roof Worksheet. None if not that form.

    MULTI-TRADE: `pairs` is [(system, sysd, area), ...] - one entry per roof system on
    this permit, primary first. PB-O-131 has a separate "Proposed Type" blank for each of
    Tile/Shingle/Metal/Flat and a separate area box for steep vs flat work, so a
    shingle+flat job fills BOTH on the one worksheet. That is exactly what the accepted
    Tota permit (B-2025-050070-0000) does: Shingle 21-0518.04 + Flat 1654.2, Gable/Hip
    2058 + Flat/Low Slope 577. Defaults to the single-system behaviour."""
    try: flds=PdfReader(form_path).get_fields()
    except Exception: flds=None
    if not _is_expedited_worksheet(flds): return None
    if not pairs: pairs=[(system, sysd, client.get('area'))]
    addr=', '.join([s for s in [client.get('address',''), _city(client, ahj),
                                ('FL '+client.get('zip','')) if client.get('zip','') else ''] if s])
    slope=str(client.get('slope') or '').strip()
    T={}
    def put(name, val):
        if name and name in flds and str(val or '').strip(): T[name]=str(val).strip()
    put('Address of Structure', addr)
    # Proposed roof covering -> the FL product approval # on THAT system's own blank.
    # Distinct /T per system (Proposed Type Tile / Shingle_2 / Metal_2 / Flat_2), so two
    # systems never collide. Tota: Shingle_2='21-0518.04', Flat_2='1654.2'.
    for _sys,_sd,_ar in pairs:
        put(_EXP_PROPOSED.get(_sys), _sd.get('prodfl'))
    # Existing roof covering: caller's own value wins; otherwise fall back to the PBC
    # Property Appraiser record (Tota: PAPA 'ASPHALT/COMPOSITION' -> 'Shingle', which is
    # what the county accepted). _papa_existing_cover() refuses to answer on a flat-only
    # scope, where PAPA's parcel-dominant covering is not the section being replaced.
    _eb=_existing_bucket(client.get('existing')) or _papa_existing_cover(client,[p[0] for p in pairs])
    if _eb: put(_EXP_EXISTING.get(_eb), 'X')
    put('Slope', slope)
    # AREA ROUTING - calibrated against the ACCEPTED Tota worksheet, and deliberately NOT
    # the _spec_markone() slope rule used elsewhere in this file. PB-O-131 splits the roof
    # by FORM, not by slope band: "Gable/Hip" means the pitched sections, "Flat/Low Slope"
    # the flat ones. Tota's shingle roof is 4/12 - which _spec_markone calls 'lowslope' -
    # yet its 2058 went in the GABLE/HIP box and only the flat 577 went in Flat/Low Slope.
    # Routing a 4/12 shingle roof to the flat box (the old behaviour) contradicts the
    # permit the county actually issued.
    #
    # UNITS: the Gable/Hip blank is LABELLED "# of Squares" but Tota's accepted worksheet
    # carries 2058 - square FEET (2058 squares would be 205,800 sf on a 1,523 sf footprint).
    # We match what the county accepted rather than "correcting" the label.
    for _sys,_sd,_ar in pairs:
        _a=re.sub(r'[^0-9,.]','',str(_ar or '').strip())
        if not _a: continue
        if _sys=='Flat': put('FlatLow Slope Sq Ft', _a)
        else: put('Gable Hip  of Squares', _a)
    # "Specify System Type, Details, and Pages" - one entry per system on the permit.
    _spec=[]
    for _sys,_sd,_ar in pairs:
        _n=_sd.get('fl_system')
        _spec.append('%s System %s (%s)'%(_sd.get('prodfl',''),_n,_sd.get('fl_mdp','')) if _n
                     else '%s - %s'%(_sd.get('prodfl',''),_sd.get('prod','')))
    put('Specify System Type Details and Pages', ' | '.join(x for x in _spec if x.strip(' -')))
    # The 1) / 2) / 3) product-approval lines are CHILD widgets: get_fields() reports them
    # fully qualified ('3. Roof Covering - Product Approval / N.O.A') but fill_form matches
    # on the leaf /T ('A'). Gate on the qualified name, write under the leaf name.
    def put_child(qualified, leaf, val):
        if qualified in flds and str(val or '').strip(): T[leaf]=str(val).strip()
    put_child('3. Roof Covering - Product Approval / N.O.A', 'A',
              ' / '.join(x for x in [p[1].get('prodfl') for p in pairs] if x))
    _tile=next((p for p in pairs if p[0]=='Tile'), None)
    if _tile:
        put_child('1.  Underlayment Tile  Product Approval',
                  '  Underlayment Tile  Product Approval', _tile[1].get('ulfl'))
        put_child('2.  Adhesive if applicable  Product Approval',
                  '  Adhesive if applicable  Product Approval', _tile[1].get('adhfl'))
    put('Print Name', _C(contractor,'qualifier'))
    put('LICENSE #', _C(contractor,'license'))
    B={}
    if 'Type of Structure' in flds:
        B['Type of Structure']='Choice1 if Main House'   # a dwelling reroof, not an accessory structure
    # Underlayment method: the reference installer's documented standard on shingle & metal is a
    # self-adhered polymer-modified bitumen underlayment applied directly to the entire
    # deck (Polystick IR-XE / MTS - see SPEC_SHEET['swb']), which is verbatim the form's
    # option "Self-Adhered (ASTM D1970) Polymer-Modified Bitumen Underlayment Applied
    # directly to entire roof deck". The form's underlayment block is scoped to "Asphalt &
    # Metal Shingles, Non-Wood Shake, Metal Panels", so Tile and Flat select nothing here.
    if any(p[0] in ('Shingle','Metal') for p in pairs) and 'Underlayment Choice 4' in flds:
        B['Underlayment Choice 4']='Yes'
    return T, B


def _stamp_pdf(w, path, stamps, values):
    """Merge a reportlab text overlay onto a flattened PDF and add to writer.
    Falls back to a plain add if anything goes wrong."""
    if _MODE[0]=='blank':
        return _add_pdf(w, path)
    _REPORT['forms'].append(os.path.basename(path))
    try:
        r=PdfReader(path)
        if r.is_encrypted: r.decrypt('')
        by_page={}
        for st in stamps: by_page.setdefault(st[0],[]).append(st)
        for pi,pg in enumerate(r.pages):
            ovl=by_page.get(pi)
            if ovl:
                W=float(pg.mediabox.width); H=float(pg.mediabox.height)
                buf=io.BytesIO(); c=canvas.Canvas(buf,pagesize=(W,H)); c.setFillColor(INK)
                for _,x,y,key,fs,maxw in ovl:
                    val=str(values.get(key,'') or '').strip()
                    if not val: continue
                    val=val.encode('latin-1','replace').decode('latin-1')
                    f=float(fs)
                    while f>5 and stringWidth(val,'Helvetica',f)>maxw: f-=0.5
                    c.setFont('Helvetica',f); c.drawString(x,y,val)
                c.showPage(); c.save(); buf.seek(0)
                try: pg.merge_page(PdfReader(buf).pages[0])
                except Exception: pass
            w.add_page(pg)
        return True
    except Exception:
        return _add_pdf(w, path)

# =============================================================================
# CITY FORM MAPS - each city's OWN forms, page by page (city_maps/<AHJ>.json)
# =============================================================================
# The 2026-11-18 end-to-end review of a Wellington shingle+flat packet found the city's own
# Shingle and Flat installation checklists attached BLANK, the city's 19-page "Reroof Packet"
# bound whole (a duplicate permit application, owner-builder affidavits on a licensed
# contractor's job, the Tile and Metal checklists on a shingle job), and the county's Form 100
# filled instead - a form the approved Wellington permits never carried. The city map fixes
# that as DATA, one JSON file per AHJ, so each city is described once and checked against its
# approved permits:
#   * which library files / pages go into a packet, by role:
#       drop          never (instructions, duplicates of PBC_01/PBC_02, design-professional
#                     certifications, one-time contractor registration...)
#       owner_builder only for an owner-builder applicant. This engine only builds licensed-
#                     contractor packets, so these are always left out.
#       system        only when that roof system is on the permit
#       keep          always
#   * how each kept page is filled: text fields and checkbox / radio on-states from job tokens
#     ("{slope}", "{area_sq}", "{prodfl}"...), coordinate stamps for flattened forms,
#   * which fields are REQUIRED (calibrated on the approved permits). A required field the job
#     data cannot fill makes the packet INCOMPLETE: a prebuilt draft + a customer question (or a
#     staff item), never a silently blank form.
#   * own_roof_forms: the systems whose county Reroofing Installation Summary (PBC Form
#     100/200/300/400) the city's own checklist replaces - the approved permits for those cities
#     carry the city form, not the county one.
# A file with no entry in the map keeps the previous generic behavior, except two filename
# rules that hold everywhere: owner-builder affidavits are left out of a contractor's packet,
# and one-time contractor registration forms are left out with a checklist reminder.
_CITY_MAP_DIR = os.path.join(_HERE, 'city_maps')
_CITY_MAPS = {}


def city_map(ahj):
    """The city map for an AHJ ({} when there is none). Cached; a broken file raises (fail loud)."""
    if ahj not in _CITY_MAPS:
        import json as _js
        p = os.path.join(_CITY_MAP_DIR, '%s.json' % ahj)
        _CITY_MAPS[ahj] = _js.load(open(p, encoding='utf8')) if os.path.exists(p) else {}
    return _CITY_MAPS[ahj]


_OWNER_BUILDER_FN = re.compile(r'owner[\W_]*builder', re.I)
_REGISTRATION_FN = re.compile(r'contractor[\W_]*(?:business[\W_]*)?(?:registration|certificate[\W_]*of[\W_]*registration)', re.I)


def _generic_file_role(fn):
    """(role, why) for a library file the city map does not describe."""
    if _OWNER_BUILDER_FN.search(fn):
        return 'owner_builder', 'owner-builder affidavit; this packet is for a licensed contractor'
    if _REGISTRATION_FN.search(fn):
        return 'registration', 'one-time contractor registration with the city, filed separately from the permit'
    return None, ''


def _on_norm(s):
    """Checkbox / radio on-state names compared without PDF name escapes (#20 = space) or punctuation."""
    s = re.sub(r'#([0-9A-Fa-f]{2})', lambda m: chr(int(m.group(1), 16)), str(s or ''))
    return re.sub(r'[^a-z0-9]', '', s.lower())


def _slope_rise(slope):
    m = re.match(r'\s*(\d+(?:\.\d+)?)', str(slope or ''))
    return m.group(1) if m else ''


def _area_sf(area):
    """Square feet as a plain number string ('3,372' -> '3372'), or ''."""
    n = re.sub(r'[^0-9.]', '', str(area or ''))
    try: v = float(n)
    except Exception: return ''
    if not v: return ''
    return '%d' % round(v)


# Shingle / shake type per covering product (the "Shingle Type" column on city checklists).
# Both library shingles are laminated architectural shingles (their approvals say so).
_SHINGLE_TYPE = {'oc': 'Architectural (laminated)', 'gaf': 'Architectural (laminated)'}
_TILE_PROFILE = {'westlake': 'Flat', 'barcelona': 'S-tile', 'eagle': 'Per product approval',
                 'eagle_high': 'High profile', 'eagle_med': 'Medium profile', 'eagle_low': 'Low profile (flat)'}

# Token -> how to ask for it when a REQUIRED city field cannot be filled.
#   (report key, item, who, question, rank). The report key matches _precheck's keys so the
# same gap is asked once. Tokens not listed here are staff items.
_TOKEN_NEED = {
    'site_address': ('address', 'Job address', 'customer', 'What is the job street address?', 0),
    'street': ('address', 'Job address', 'customer', 'What is the job street address?', 0),
    'zip': ('zip', 'Job ZIP code', 'customer', 'What is the ZIP code of the job address?', 8),
    'owner': ('owner', 'Property owner name', 'customer', 'Who is the property owner, exactly as on the deed?', 1),
    'value': ('value', 'Contract price (permit value)', 'customer', 'What is the contract price for this work?', 2),
    'mrh': ('mrh', 'Mean roof height', 'customer', "What is the roof's mean height in feet?", 3),
    'slope': ('slope', 'Roof slope', 'customer', 'What is the roof slope (for example 5/12)?', 5),
    'slope_x12': ('slope', 'Roof slope', 'customer', 'What is the roof slope (for example 5/12)?', 5),
    'color': ('roof_color', 'Roof material color (zoning review)', 'customer',
              'What is the color of the roof covering you will install, as the manufacturer names it (for example "Driftwood")?', 14),
    'has_flat': ('system2', 'Second roof system (is a flat / low-slope section included?)', 'customer',
                 'Does this job include a second roof system, such as a flat / low-slope section? If yes, which system and how big is it?', 7),
    'roof_gable': ('roof_shape', 'Roof shape (all hip, or gable / partial gable)', 'customer',
                   'Is the roof all hip, or does it have any gable ends?', 9),
    'roof_hip': ('roof_shape', 'Roof shape (all hip, or gable / partial gable)', 'customer',
                 'Is the roof all hip, or does it have any gable ends?', 9),
    'existing_tile': ('existing', 'Existing roof covering', 'customer',
                      'What roof covering is on the building now (shingle, tile, metal or flat)?', 12),
    'qualifier': ('identity_qualifier_name', 'Qualifier name', 'customer', None, 0),
    'license': ('identity_license_number', 'Contractor license number', 'customer', None, 0),
    'company': ('identity_company_name', 'Contractor company name', 'customer', None, 0),
    'company_title': ('identity_company_name', 'Contractor company name', 'customer', None, 0),
    'skylights_no': ('skylights', 'Skylights / roof tubes replaced?', 'customer',
                     'Will any skylights or solar tubes be replaced with the roof? If yes, upload their product approval.', 15),
    'pcn': ('pcn', 'Parcel number (PCN / folio)', 'customer',
            "What is the property's parcel number (PCN or folio)? It is on the tax bill and the property appraiser's site.", 10),
    'legal': ('legal', 'Legal description', 'customer',
              'Please upload the deed, or the property appraiser page that shows the legal description.', 11),
    'owner_phone': ('owner_phone', "Property owner's phone", 'customer', "What is the property owner's phone number?", 16),
    'owner_email': ('owner_email', "Property owner's e-mail", 'customer', "What is the property owner's e-mail address?", 17),
}


def _job_tokens(client, ahj, contractor, pairs, system, sysd, area):
    """Every value a city map may print, for ONE roof system's page (a shingle checklist reads the
    shingle system's area / product; the flat checklist reads the flat system's). '' = unknown."""
    c = contractor or {}
    city = _city(client, ahj)
    zip_ = str(client.get('zip') or '').strip()
    street = str(client.get('address') or '').strip()
    systems = [s for s, _, _ in pairs]
    pk = sysd.get('product_key') or DEFAULT_PRODUCT.get(system) or ''
    spec = SPEC_SHEET.get(system, {})
    total = 0.0
    for _s, _d, _a in pairs:
        try: total += float(re.sub(r'[^0-9.]', '', str(_a or '')) or 0)
        except Exception: pass
    # The job slope belongs to the PRIMARY system; a secondary flat section has its own (slope_flat).
    slope = _slope_rise(client.get('slope')) if (system != 'Flat' or pairs[0][0] == 'Flat') else _slope_rise(client.get('slope_flat'))
    ca = ', '.join(x for x in [c.get('address', ''), c.get('city', ''),
                               ((c.get('state') or 'FL') + ' ' + c.get('zip', '')).strip() if c.get('zip') else ''] if x)
    t = {
        'site_address': ', '.join(s for s in [street, city, ('FL ' + zip_) if zip_ else ''] if s) if street else '',
        'street': street, 'city': city, 'zip': zip_, 'state': 'FL',
        'owner': str(client.get('owner') or '').strip(),
        'owner_phone': str(client.get('phone') or '').strip(),
        'owner_email': str(client.get('owner_email') or '').strip(),
        'pcn': pcn_display(client.get('pcn', '')), 'legal': str(client.get('legal') or '').strip(),
        'slope': slope, 'slope_x12': (slope + '/12') if slope else '',
        'mrh': re.sub(r'[^0-9.]', '', str(client.get('mrh') or '')),
        'area_sf': _area_sf(area), 'area_sq': _sqrs(area),
        'total_area_sf': ('%d' % round(total)) if total else '', 'total_area_sq': _sqrs(total) if total else '',
        'mfr': sysd.get('mfr', ''), 'prod': sysd.get('prod', ''), 'prodfl': sysd.get('prodfl', ''),
        'prod_short': re.sub(r'\s+(architectural shingle|flat concrete tile|concrete tile|S-profile concrete tile)$', '',
                             sysd.get('prod', ''), flags=re.I),
        'prod_type': _SHINGLE_TYPE.get(pk, '') if system == 'Shingle' else _TILE_PROFILE.get(pk, '') if system == 'Tile'
                     else _panel_type(sysd) if system == 'Metal' else '',
        'ul': sysd.get('ul', ''), 'ulfl': sysd.get('ulfl', ''),
        'adh': sysd.get('adh', ''), 'adhfl': sysd.get('adhfl', ''),
        'fl_system': sysd.get('fl_system', ''), 'fl_mdp': sysd.get('fl_mdp', ''),
        'deck': spec.get('decking', ''), 'deck_fastener': spec.get('deck_fastener', ''), 'drip': spec.get('drip', ''),
        'flat_ply': 'Elastobase SA base sheet, self-adhered' if system == 'Flat' else '',
        'flat_cap': 'Polyflex SA P FR cap sheet, self-adhered' if system == 'Flat' else '',
        'qualifier': _C(contractor, 'qualifier'), 'license': _C(contractor, 'license'),
        'company': _C(contractor, 'name'), 'company_title': _C(contractor, 'name_title'),
        'company_phone': c.get('phone', ''), 'company_email': c.get('email', ''), 'company_address': ca,
        'company_street': c.get('address', ''), 'company_city': c.get('city', ''),
        'company_state': c.get('state') or 'FL', 'company_zip': c.get('zip', ''),
        'value': _permit_value(client) if _permit_value(client)[:1].isdigit() else '',
        'color': str(client.get('roof_color') or '').strip(),
        'existing': str(client.get('existing') or '').strip(),
        'system': system, 'systems': ' + '.join(systems),
    }
    return t


def _job_conditions(client, pairs, system):
    """Tri-state conditions a map's checkbox can depend on: True / False / None (unknown -> blank)."""
    systems = [s for s, _, _ in pairs]
    known2 = client.get('system2_known')
    has_flat = True if 'Flat' in systems else (None if known2 is False else False)
    desc = str(client.get('scope') or '').lower()
    sky = str(client.get('skylights') or '').strip().lower()
    if sky.startswith('n'): sky_no = True
    elif sky.startswith('y'): sky_no = False
    elif 'skylight' in desc or 'solar tube' in desc or 'sun tunnel' in desc: sky_no = None
    else: sky_no = True        # nothing in the scope says skylights are replaced; the checklist asks to confirm
    shape, _ = _roof_shape(client)
    if not shape:
        try: shape = _papa_roof_form(client) if client.get('pcn') else None
        except Exception: shape = None
    eb = _existing_bucket(client.get('existing'))
    _st = set(systems)
    return {'has_shingle': 'Shingle' in _st, 'has_tile': 'Tile' in _st, 'has_metal': 'Metal' in _st,
            'has_steep': bool(_st & {'Shingle', 'Tile', 'Metal'}),
            'page_shingle': system == 'Shingle', 'page_tile': system == 'Tile', 'page_metal': system == 'Metal',
            'page_flat': system == 'Flat',
            'roof_gable': (None if not shape else shape == 'gable'), 'roof_hip': (None if not shape else shape == 'hip'),
            'existing_tile': (None if not eb else eb == 'Tile'), 'existing_shingle': (None if not eb else eb == 'Shingle'),
            'existing_flat': (None if not eb else eb == 'Flat'), 'existing_metal': (None if not eb else eb == 'Metal'),
            'commercial': False,
            'has_flat': has_flat, 'no_flat': (None if has_flat is None else not has_flat),
            'skylights_no': sky_no, 'skylights_yes': (None if sky_no is None else not sky_no),
            'multi': len(systems) > 1, 'single': len(systems) == 1,
            'residential': True, 'reroof': True, 'always': True}


_TOKEN_RX = re.compile(r'\{([a-z0-9_]+)\}')


def _render_value(spec, tokens):
    """'{mfr} {prod}' -> text; '' when ANY token in it is unknown (so a half-filled value
    never prints and the required-field check sees the gap). Literal text passes through."""
    missing = []
    def sub(m):
        v = str(tokens.get(m.group(1), '') or '').strip()
        if not v: missing.append(m.group(1))
        return v
    out = _TOKEN_RX.sub(sub, str(spec))
    return ('' if missing else out.strip()), missing


def _map_page_fill(pg, tokens, conds):
    """(T, B, stamps, unresolved) for one mapped page. `unresolved` = {field: [tokens or condition]}."""
    T, B, stamps, unresolved = {}, {}, [], {}
    for fname, spec in (pg.get('text') or {}).items():
        v, miss = _render_value(spec, tokens)
        if v: T[fname] = v
        else: unresolved[fname] = miss or ['?']
    for fname, spec in (pg.get('buttons') or {}).items():
        if isinstance(spec, dict):
            cond = spec.get('if', 'always')
            val = conds.get(cond)
            on = spec.get('then') if val is True else spec.get('else') if val is False else None
            if on: B[fname] = on
            else: unresolved[fname] = [cond] if val is None else ['?']
        elif spec:
            B[fname] = spec
    for st in pg.get('stamps') or []:
        cond = st.get('if')
        if cond and conds.get(cond) is not True:
            if conds.get(cond) is None and st.get('required'):
                unresolved['stamp:%s@%s,%s' % (st.get('text'), st.get('x'), st.get('y'))] = [cond]
            continue
        v, miss = _render_value(st.get('text', ''), tokens)
        if not v:
            if st.get('required'): unresolved['stamp:%s@%s,%s' % (st.get('text'), st.get('x'), st.get('y'))] = miss or ['?']
            continue
        stamps.append((int(st.get('page', pg['page'])), float(st['x']), float(st['y']), v, float(st.get('size', 9)), float(st.get('maxw', 400))))
    return T, B, stamps, unresolved


def _need_city_field(ahj, title, field, toks, page_system, primary):
    """Record a REQUIRED city-form field the job data could not fill."""
    for tok in toks or ['?']:
        if tok in ('area_sq', 'area_sf'):
            if page_system == primary:
                _need('area', 'Roof area', 'printed on the permit application / roofing forms; left blank', 'customer',
                      'What is the total roof area being replaced (square feet or squares)?', 4)
            else:
                _need('area2', 'Roof area of the %s section' % page_system, 'second system on the permit', 'customer',
                      'What is the roof area of the %s section?' % page_system.lower(), 6)
            continue
        if tok in _TOKEN_NEED:
            k, item, who, q, rank = _TOKEN_NEED[tok]
            _need(k, item, '%s asks for it (%s)' % (title, ahj_label(ahj)), who, q, rank)
            continue
        _need('cityfield:%s:%s' % (title, field), '%s: field %s' % (title, field),
              'required on the %s form and not fillable from the job data' % ahj_label(ahj), 'staff', rank=5)


def _city_file(w, ahj, fn, raw, fmap, client, contractor, pairs, primary, T_supp, req_ids):
    """Bind ONE city library file per its city-map entry. Returns the set of systems whose
    city roof checklist went into the packet."""
    systems = [s for s, _, _ in pairs]
    stem = fn[:-4]
    role = fmap.get('role', 'by_page' if fmap.get('pages') else 'keep')
    if fmap.get('note'):
        _confirm(fmap['note'])
    if role == 'owner_builder' and APPLICANT[0] == 'owner_builder':
        role = 'keep'
    if role in ('drop', 'owner_builder', 'registration', 'reference'):
        _REPORT.setdefault('dropped', []).append('%s (%s)' % (stem, fmap.get('why') or role))
        return set()
    if role == 'system' and not (set(fmap.get('systems') or [fmap.get('system')]) & set(systems)):
        _REPORT.setdefault('dropped', []).append('%s (other roof system)' % stem)
        return set()
    try:
        n = len(PdfReader(raw).pages)
    except Exception:
        _need('form:ahj:' + stem, stem.replace('_', ' '), 'the library copy could not be read', 'staff', rank=5)
        return set()
    default = fmap.get('default', 'keep')
    plan = {}
    for pg in fmap.get('pages') or []:
        idxs = pg.get('pages', [pg.get('page')])
        for i in idxs:
            if i is not None and 0 <= int(i) < n:
                plan[int(i)] = dict(pg, page=int(i))
    if role != 'by_page' and not plan and n:
        # A plain keep / system entry: the whole file, filled from the entry's own text / buttons / stamps
        # (listed once, on page 0; field names are document-wide and stamps carry their own page).
        plan = {0: {'page': 0, 'role': 'keep', 'generic_fill': fmap.get('generic_fill', True),
                    'text': fmap.get('text'), 'buttons': fmap.get('buttons'), 'stamps': fmap.get('stamps'),
                    'required': fmap.get('required'), 'title': fmap.get('title'), 'hand': fmap.get('hand')}}
        for i in range(1, n):
            plan[i] = {'page': i, 'role': 'keep', 'generic_fill': fmap.get('generic_fill', True)}
    if fmap.get('bake_annots'):
        # Forms whose printed content lives in annotations (fill_form drops every annotation when it
        # flattens): bake the non-widget annotations into the page first, on a temp copy.
        try:
            import fitz as _fz
            _d = _fz.open(raw); _d.bake(annots=True, widgets=False)
            _tmp = os.path.join(tempfile.gettempdir(), 'baked_' + os.path.basename(raw))
            _d.save(_tmp); _d.close(); raw = _tmp
        except Exception:
            pass
    keep, T, B, stamps, covered = [], {}, {}, [], set()
    any_fill = False
    for i in range(n):
        pg = plan.get(i) or {'page': i, 'role': default}
        prole = pg.get('role', 'keep')
        psys = pg.get('system')
        if prole == 'owner_builder' and APPLICANT[0] == 'owner_builder':
            prole = 'keep'
        if prole in ('drop', 'owner_builder', 'reference', 'registration'):
            continue
        if prole == 'system' and psys not in systems:
            continue
        keep.append(i)
        sys_here = psys if psys in systems else primary
        _sd = next((d for s, d, a in pairs if s == sys_here), pairs[0][1])
        _ar = next((a for s, d, a in pairs if s == sys_here), pairs[0][2])
        toks = _job_tokens(client, ahj, contractor, pairs, sys_here, _sd, _ar)
        conds = _job_conditions(client, pairs, sys_here)
        if pg.get('generic_fill'):
            for k, v in T_supp.items():
                T.setdefault(k, v)
        t, b, st, unresolved = _map_page_fill(pg, toks, conds)
        if t or b or st:
            any_fill = True
        T.update(t); B.update(b); stamps += st
        title = pg.get('title') or '%s %s' % (ahj_label(ahj), stem.replace('_', ' '))
        for fld in pg.get('required') or []:
            if fld in unresolved:
                _need_city_field(ahj, title, fld, unresolved[fld], sys_here, primary)
        for key, toks_ in unresolved.items():
            if key.startswith('stamp:'):
                _need_city_field(ahj, title, key[6:], toks_, sys_here, primary)
        if prole == 'system' and psys:
            covered.add(psys)
        for h in pg.get('hand') or []:
            _hand('%s: %s' % (title, h))
    if not keep:
        _REPORT.setdefault('dropped', []).append('%s (no page applies)' % stem)
        return covered
    try:
        flds = PdfReader(raw).get_fields()
    except Exception:
        flds = None
    if flds or stamps:
        pages = fill_form(raw, T if flds else {}, B if flds else {}, stamps=stamps or None)
    else:
        rr = PdfReader(raw)
        if rr.is_encrypted: rr.decrypt('')
        pages = rr.pages
        _REPORT['forms'].append(os.path.basename(raw))
    for i in keep:
        w.add_page(pages[i])
    if not any_fill and not (flds and any(k in flds for k in T)):
        if ('ahj:' + stem) in req_ids:
            _need('form:ahj:%s:blank' % stem, stem.replace('_', ' '),
                  'required by the approved %s permits and attached BLANK (no fillable fields mapped yet)' % ahj_label(ahj),
                  'staff', rank=5)
        else:
            _hand('%s: attached blank - the job and contractor details are filled in by hand at signing.' % stem.replace('_', ' '))
    return covered


def _build_broward(client, ahj, system, attachment_paths, out_path, underlayment=None, product=None, contractor=None):
    """Broward (HVHZ) packet: cover + contractor pre-filled BC forms + system template + approvals.
    NOTE: the BSPEC Section-D template is pre-filled with the default manufacturer; the cover and
    the (all-inclusive) product-approval set reflect the chosen product, but a product-specific
    pre-filled Section-D template must be added to fully switch manufacturers on the HVHZ form."""
    sysd=_apply_product(_apply_underlayment(system, underlayment), system, product); w=PdfWriter()
    pk=sysd.get('product_key')
    _hvhz_warn=None   # the roof system is stated on the county HVHZ application itself (BC_03, filled below)
    for p in cover(client, ahj, system, sysd['ul'], sysd.get('prod'), _hvhz_warn, contractor=contractor): w.add_page(p)
    # County HVHZ forms, RAW government blanks only (the library's flattened _Prefilled copies
    # carry another company's identity and are never used). BC_04 (HVHZ Owner's Notification,
    # FBC 1524) is in every approved Broward set in the answer key and is now included.
    bvals=_broward_values(client, ahj, system)
    c=contractor or {}
    _cphone=c.get('phone','') or _C(contractor,'phone')
    _cline='%s, %s, %s, %s %s%s'%(_C(contractor,'name_title'), c.get('address',''), c.get('city',''),
                                   c.get('state') or 'FL', c.get('zip',''), ('   Ph: '+_cphone) if _cphone else '')
    cvals=dict(bvals, c_name=_C(contractor,'name'), c_phone=_cphone, c_email=c.get('email',''),
               c_addr=c.get('address',''), c_city=c.get('city',''), c_st=(c.get('state') or 'FL'),
               c_zip=c.get('zip',''), c_qual=_C(contractor,'qualifier'), c_lic=_C(contractor,'license'),
               c_line=_cline.replace(', ,', ','), improvement='Reroof', county='Broward')
    _FORMS=[('BC_01_Uniform_Building_Permit_Application.pdf', _BC01_STAMPS+_BC01_CONTRACTOR_STAMPS),
            ('BC_02_Notice_of_Commencement.pdf', _BC02_STAMPS+_BC02_CONTRACTOR_STAMPS),
            (_BC03_RAW, None),
            ('BC_04_HVHZ_Owners_Notification_FBC1524.pdf', _BC04_STAMPS),
            ('BC_06_FDEP_Asbestos_Notification.pdf', _BC06_STAMPS+_BC06_CONTRACTOR_STAMPS)]
    for fn, stamps in _FORMS:
        raw=os.path.join(BMB, ahj, '01_Permit_Application_Packet', fn)
        if not os.path.exists(raw):
            raw=os.path.join(BLIB, '01_Permit_Application_Packet', fn)
        if not os.path.exists(raw):
            _need('form_'+fn[:-4], fn[:-4].replace('_',' '),
                  'the county blank is not in the form library', 'staff', rank=5)
            continue
        if fn==_BC03_RAW:
            _t,_b=_bc03_fill(client, ahj, system, sysd, contractor)
            _note=('%s - HVHZ. Signatures, and the PE-sealed design pressures, are completed per job.'
                   % _tenant_short(contractor))
            if system in ('Shingle','Tile','Metal'):
                _sh,_shb=_roof_shape(client)
                _note+=('  |  Roof shape: %s (%s)'%(_ROOF_SHAPE_LABEL[_sh],_shb) if _sh
                        else '  |  Roof shape LEFT BLANK - %s; tick All Hip or Gable by hand'%_shb)
                if not _sh:
                    _need('roof_shape', 'Roof shape (all hip, or gable / partial gable)',
                          'HVHZ application Section D selects the design-pressure table from it', 'customer',
                          'Is the roof all hip, or does it have any gable ends?', rank=9)
            if system=='Flat':
                _hand('HVHZ application Section C assembly grid (anchor/base/ply sheets, insulation, '
                      'fastening): completed from the membrane product approval system you use.')
            for _p in fill_form(raw, _t, _b, topnote=_note): w.add_page(_p)
            continue
        # These raw county forms carry generic / numeric widget names, so a name-based contractor fill
        # double-prints over the measured stamps (seen on BC_02). Stamps only; BC_04 by its own names.
        T={}
        if fn.startswith('BC_04'):
            T={'PROPERTY ADDRESS': client.get('address',''), 'STATE':'FL', 'ZIP': client.get('zip','')}
        _st=[]
        for (pg,x,y,key,fs,maxw) in (stamps or []):
            v=str(cvals.get(key,'') or '').strip()
            if not v: continue
            v=v.encode('latin-1','replace').decode('latin-1')
            f=float(fs)
            while f>5 and stringWidth(v,'Helvetica-Bold',f)>maxw: f-=0.5
            _st.append((pg,x,y,v,f))
        for _p in fill_form(raw, T, {}, topnote=None, stamps=_st or None): w.add_page(_p)
    # The library's _System_Templates for Broward are another company's pre-filled pages of the
    # same HVHZ application (their standard products printed in). BC_03 above is the county's own
    # blank filled from THIS job, so those templates are not bound into a customer's packet.
    # product approvals for the system — CHOSEN covering only, plus shared components.
    # Some Broward Miami-Dade NOAs carry a baked "<author> submittal" note on page 1;
    # redact that token for a customer (NOA content untouched).
    if _MODE[0]!='blank':
        for _ap in _broward_approvals(system, sysd): _add_pdf(w, _strip_foreign_text(_ap, contractor))
        for ap in attachment_paths or []: _add_pdf(w, ap)
    with open(out_path,'wb') as fh: w.write(fh)
    return out_path

def _apply_underlayment(system, underlayment):
    """Return a copy of the system dict with the chosen underlayment swapped in."""
    sysd=dict(SYSTEMS[system])
    ulmap=UNDERLAYMENTS.get(system,{})
    choice=underlayment if underlayment in ulmap else DEFAULT_UL.get(system)
    ul=ulmap.get(choice)
    if ul:
        sysd['ul'],sysd['ulfl']=ul[0],ul[1]
        sysd['approvals']=[a for a in sysd.get('approvals',[]) if 'nderlayment' not in a]+[ul[2]]
    return sysd

# --- PBC Property Appraiser (PAPA) record scrape -------------------------------
# The PAPA property-detail page is itself a REQUIRED submittal component (it is
# Danny's "Submittal2", and PB-O-131 Attachment B item 2 wants the supporting
# record set). It also carries two facts the re-roof worksheet asks for:
#     Roof Cover     : ASPHALT/COMPOSITION      -> "Existing Roof Covering"
#     Roof Structure : GABLE/HIP                -> which design-pressure table
# Same URL + same failure discipline as the office app: any
# fetch or parse failure returns {} and every downstream field stays BLANK. We
# never guess a bucket, and we never fall back to a derived value.
#
# CAVEAT THAT MUST TRAVEL WITH THE VALUE: this is the county's *tax record*, not
# an inspection, and it describes the parcel's DOMINANT roof covering. On a house
# with a tile field and a small flat section, PAPA says "CONCRETE TILE" even
# though the flat section is what is being replaced. It can also be years stale.
# See _papa_existing_cover() for the gate that keeps that from misdescribing a job.
_PAPA_LABELS=('Legal Description','Municipality','Roof Structure','Roof Cover',
              'Location Address','Property Use Code')
_PAPA_CACHE={}


def papa_record(pcn):
    """{label: value} scraped from the PBC Property Appraiser for a PCN. {} on any failure."""
    import urllib.request, html as _html
    digits=''.join(ch for ch in str(pcn or '') if ch.isdigit())
    if len(digits)!=17 or not digits.strip('0'): return {}     # all zeros = a sample / placeholder PCN: no lookup
    if digits in _PAPA_CACHE: return _PAPA_CACHE[digits]
    url='https://pbcpao.gov/Property/RenderPrintSum?parcelId=%s&flag=ALL'%digits
    try:
        req=urllib.request.Request(url,headers={'User-Agent':'PermitPacket/1.0'})
        with urllib.request.urlopen(req,timeout=15) as r:
            page=r.read().decode('utf-8','replace')
    except Exception:
        _PAPA_CACHE[digits]={}; return {}
    out={}
    for lbl in _PAPA_LABELS:
        m=re.search(re.escape(lbl)+r'\s*:?\s*</td>\s*<td[^>]*>\s*(?:<span[^>]*>)?(.*?)(?:</span>|</td>)',
                    page, re.I|re.S)
        if m:
            v=' '.join(_html.unescape(re.sub(r'<[^>]+>',' ',m.group(1))).split())
            if v: out[lbl]=v
    # Owner mailing address (second cell of the Owner Information table): street line + "CITY ST ZIP".
    m=re.search(r'Mailing Address\s*</td>\s*</tr>\s*<tr>\s*<td>.*?</td>\s*<td>(.*?)</td>', page, re.I|re.S)
    if m:
        lines=[' '.join(_html.unescape(x).split()) for x in re.split(r'<br\s*/?>', m.group(1), flags=re.I)]
        lines=[re.sub(r'<[^>]+>','',x).strip() for x in lines if x.strip()]
        if lines: out['Mailing Address']=' | '.join(lines)
    _PAPA_CACHE[digits]=out
    return out


def papa_site_zip(client):
    """The job's 5-digit ZIP from the PBC Property Appraiser record, or ''.

    PAPA prints the SITE address without a ZIP, but the owner's mailing address with one. The
    mailing ZIP is used ONLY when the mailing street is the site street (owner-occupied: e.g.
    11722 GREENBRIAR CIR / WELLINGTON FL 33414 5912). A different mailing address says nothing
    about the site's ZIP, so it is never used."""
    rec=papa_record(client.get('pcn','')) or {}
    site=rec.get('Location Address','') or client.get('address','')
    mail=rec.get('Mailing Address','')
    if not site or not mail: return ''
    parts=mail.split(' | ')
    norm=lambda x: re.sub(r'[^A-Z0-9]','',str(x).upper())
    if norm(parts[0])!=norm(site) and norm(parts[0])!=norm(client.get('address','')): return ''
    m=re.search(r'\b(\d{5})(?:[\s-]?\d{4})?\s*$', parts[-1])
    return m.group(1) if m else ''


# PAPA's roof-cover vocabulary -> the worksheet's four buckets. Only unambiguous
# mappings appear here; anything else (or an unseen value) yields None = leave blank.
# ASPHALT/COMPOSITION -> Shingle is confirmed by Tota's ACCEPTED worksheet, whose
# PAPA record reads ASPHALT/COMPOSITION and whose worksheet ticks Shingle.
_PAPA_COVER_BUCKET={
    'ASPHALT/COMPOSITION':'Shingle',   # confirmed against Tota B-2025-050070-0000
    'ASPHALT SHINGLE':'Shingle',
    'WOOD SHINGLE/SHAKE':None,         # the form has no wood bucket - never force one
    'CONCRETE TILE':'Tile',
    'CLAY TILE':'Tile',
    'TILE':'Tile',
    'METAL':'Metal',
    'BUILT-UP':'Flat',
    'BUILT UP':'Flat',
}


def _papa_existing_cover(client, systems):
    """The 'Existing Roof Covering' bucket for this job from the PAPA record, or None.

    GATED ON PURPOSE. PAPA reports the parcel's DOMINANT roof covering, which is the
    steep-slope field on a mixed roof. On a flat-only scope (e.g. re-roofing a 344 sf
    flat section of an otherwise tile house) the dominant value describes a part of the
    roof that is NOT in the scope of work, so stamping it would misdescribe the permit.
    We therefore use PAPA only when a steep-slope system is actually in the packet -
    which is exactly the Tota case (Shingle+Flat -> PAPA 'ASPHALT/COMPOSITION' ->
    worksheet ticks 'Shingle', matching what the county accepted).
    """
    if all(s=='Flat' for s in systems):
        return None
    cover=(papa_record(client.get('pcn','')) or {}).get('Roof Cover','')
    return _PAPA_COVER_BUCKET.get(cover.strip().upper())


def _papa_roof_form(client):
    """'gable' or 'hip' from PAPA's Roof Structure, else None.

    PAPA returns a FRAMING type as often as a shape ('WOOD TRUSS' on both Goren and
    DuBois), and a framing type says nothing about gable vs hip. Only an explicit
    GABLE/HIP answer is usable; everything else -> None -> no pressure lookup."""
    rs=((papa_record(client.get('pcn','')) or {}).get('Roof Structure','') or '').upper()
    if 'GABLE' in rs and 'HIP' in rs: return 'gable'   # 'GABLE/HIP' -> use the higher (gable) table
    if 'GABLE' in rs: return 'gable'
    if 'HIP' in rs: return 'hip'
    return None


def _pbo131_roof_form(client):
    """Gable/hip for the PB-O-131 design-pressure table lookup, or None.

    ASYMMETRIC ON PURPOSE, and deliberately stricter than the Section-D tick.

    * An explicit client['roof_shape'] wins outright - a person who looked at the roof
      outranks every derivation.
    * Otherwise PAPA (the county's own public record) wins when it answers at all.
    * Only if PAPA is silent do we fall back to the RoofGraf derivation, AND ONLY when
      it says 'gable' - table 1-G, the HIGHER pressures. A derived 'hip' would LOWER
      the required psf (Exp C at 15 ft: 115.5 -> 83.4 psf), and understating a required
      pressure off inferred geometry is precisely the failure the PE-seal rule exists
      to prevent. A derived 'hip' therefore leaves both design-criteria lines blank,
      exactly as before this change.

    Net effect: this can only ever ADD the conservative answer or stay blank. It can
    never lower a pressure that was previously printed."""
    if str(client.get('roof_shape') or '').strip():
        return _roof_shape(client)[0]
    papa=_papa_roof_form(client)
    if papa: return papa
    shape,_=_roof_shape(client)
    return shape if shape=='gable' else None


_CHROME_PATHS=[
    r'C:\Program Files\Google\Chrome\Application\chrome.exe',
    r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
]
def _fetch_pbc_pa_pdf(pcn):
    """Render the PBC Property Appraiser detail page for a PCN to a temp PDF.
    URL: pbcpao.gov/Property/Details?parcelId=<pcn_no_dashes>
    Returns path to temp PDF, or None on any failure (non-fatal)."""
    pcn_clean=re.sub(r'[\s\-]','',pcn or '')
    if not pcn_clean: return None
    url='https://pbcpao.gov/Property/Details?parcelId=%s'%pcn_clean
    chrome=next((p for p in _CHROME_PATHS if os.path.exists(p)),None)
    if not chrome: return None
    tmp=os.path.join(tempfile.gettempdir(),'pbc_pa_%s.pdf'%pcn_clean)
    try:
        subprocess.run([
            chrome,'--headless=new','--disable-gpu','--no-sandbox',
            '--disable-dev-shm-usage','--run-all-compositor-stages-before-draw',
            '--print-to-pdf=%s'%tmp,'--print-to-pdf-no-header',
            '--virtual-time-budget=8000',url
        ],capture_output=True,timeout=30)
        return tmp if os.path.exists(tmp) and os.path.getsize(tmp)>1000 else None
    except Exception: return None

class UnknownAHJError(ValueError):
    """Raised when `ahj` is not a real jurisdiction in the library."""


def validate_ahj(ahj):
    """FAIL LOUD on an unknown AHJ.

    Before this check, any string — a typo, 'Palm_Beach_County', even '' —
    silently produced a ~10MB plausible-looking packet built from generic
    fallbacks, addressed to a building department that doesn't exist. A packet
    that looks right and is filed in the wrong jurisdiction is worse than no
    packet, because nobody re-reads it. Audit 2026-07-20."""
    known = list_ahjs()
    if ahj in known:
        return ahj
    hint = ''
    if ahj:
        near = [a for a in known if ahj.lower().replace(' ', '_') in a.lower()
                or a.lower() in ahj.lower().replace(' ', '_')]
        if near:
            hint = ' Did you mean: %s?' % ', '.join(near[:5])
    raise UnknownAHJError(
        "Unknown AHJ %r - refusing to build. A packet must name a real "
        "jurisdiction.%s (For a parcel that is not inside any municipality, use "
        "%r - Unincorporated Palm Beach County, permitted by the PBC Building "
        "Division at 2300 N. Jog Rd.)" % (ahj, hint, UNINCORPORATED_PBC))


def _build_packet_impl(client, ahj, system, attachment_paths, out_path, underlayment=None, product=None, contractor=None, fetch_pa=True, system2=None):
    """Build one permit packet.

    MULTI-TRADE (`system2`): a single re-roof permit routinely covers two roof systems -
    e.g. "Full Roof Replacement Shingle to Shingle & Flat to Flat", which is the scope of
    BOTH accepted multi-trade examples we hold (PBC B-2025-050070-0000 Tota, and Delray
    BLDC-016009-2026 DuBois). One cover, one application, one NOC, but BOTH systems'
    product approvals and BOTH area figures. `client['area2']` carries the secondary
    system's area; `client['area']` stays the primary's. Omit `system2` and every existing
    caller behaves exactly as before."""
    validate_ahj(ahj)
    if system not in SYSTEMS:
        raise ValueError("Unknown roof system %r - expected one of %s"
                         % (system, ', '.join(SYSTEMS)))
    if system2 is not None:
        if system2 not in SYSTEMS:
            raise ValueError("Unknown secondary roof system %r - expected one of %s"
                             % (system2, ', '.join(SYSTEMS)))
        if system2 == system:
            raise ValueError("Secondary roof system %r duplicates the primary system; "
                             "a multi-trade permit covers two DIFFERENT systems" % (system2,))
    if _is_broward(ahj):
        # Broward/HVHZ has its own form set and is not calibrated for multi-trade yet.
        if system2: raise ValueError("Multi-trade packets are not supported for Broward AHJs yet (%r)" % ahj)
        return _build_broward(client, ahj, system, attachment_paths, out_path, underlayment, product, contractor=contractor)
    sysd=_apply_product(_apply_underlayment(system, underlayment), system, product); w=PdfWriter()
    # Systems on this permit, primary first. `pairs` = [(system, sysd, area)].
    systems=[system]+([system2] if system2 else [])
    sysd2=_apply_product(_apply_underlayment(system2, None), system2, None) if system2 else None
    pairs=[(system, sysd, client.get('area'))]
    if system2: pairs.append((system2, sysd2, client.get('area2')))
    def add(pages):
        for p in pages: w.add_page(p)
    # Expedited-route gate (unincorporated PBC only): PB-O-131 Attachment B requires
    # certifying a DETACHED single-family dwelling. Decide once, here, so the cover page
    # can carry the warning and step 4b can drop the form.
    _pbo131_status,_pbo131_detail=('ok','')
    if _is_unincorporated_pbc(ahj):
        _pbo131_status,_pbo131_detail=pbo131_qualifies(client)
    _warn=None
    if _pbo131_status=='no':
        # ROUTE SUBSTITUTION, not just omission. Dropping PB-O-131 and leaving nothing in
        # its place produced a packet that looked like an incomplete expedited filing. The
        # non-expedited route is PB-O-094, and its submittal component is the county
        # REROOFING INSTALLATION SUMMARY form for this system WITH the PBO-94 "Mandated
        # Retrofits of Roof-to-Wall Connection" pages that ship inside the same PDF.
        # Step 4c already attaches exactly that (Form %s here), so the packet IS a
        # PB-O-094 filing - it just never said so. Evidence that this is the correct
        # composition: the ISSUED unincorporated-PBC permit R25183 (Rothstein) files these
        # same pages as its numbered submittal "2-Roof Assembly Worksheet".
        _warn=('ROUTE: PB-O-094 (non-expedited). PBC Property Appraiser use code %s is an ATTACHED '
               'dwelling, so PB-O-131 Attachment B ("detached Single Family Dwelling") CANNOT be '
               'certified and the expedited worksheet is correctly NOT in this packet. This packet is '
               'filed on PB-O-094: the Form %s Reroofing Installation Summary and its PBO-94 Mandated '
               'Roof-to-Wall Connection Retrofit pages, enclosed, are the required worksheet.'
               %(_pbo131_detail, (_PBC_ROOF_FORM.get(system,'') or '').split('Form')[-1].replace('.pdf','') or '?'))
    elif _pbo131_status=='unknown' and _is_unincorporated_pbc(ahj):
        _warn=('VERIFY BEFORE SIGNING: could not confirm detached single-family status (%s). PB-O-131 is '
               'valid ONLY for a detached SFD; if this is a townhouse/duplex/condo, use PB-O-094.'%_pbo131_detail)
    # MULTI-TRADE disclosure. Most of the packet is genuinely per-system: both systems'
    # product approvals (step 5), one county roofing form each (step 4c), a spec
    # sheet each (step 4), and the PB-O-131 worksheet fills BOTH proposed types and BOTH
    # area boxes (_expedited_fill). But some supplemental forms are single-system by
    # construction - the Roof Construction Specifications Sheet has one Roof Covering row,
    # one deck, one fastener schedule, and physically cannot describe two systems. Those
    # are filled for the PRIMARY system, and we SAY SO here rather than silently picking
    # one and letting a reviewer assume it covers the whole job.
    if system2:
        _mt=('MULTI-TRADE PERMIT (%s + %s): each system carries its own product approvals and its own '
             'roofing form or city checklist, and worksheets that ask for it list both systems and both '
             'areas. Any SINGLE-SYSTEM supplemental form in this packet (e.g. a Roof Construction '
             'Specifications Sheet) is filled for the PRIMARY system (%s) only.'%(system,system2,system))
        _warn=(_warn+'  |  '+_mt) if _warn else _mt
    # 1) cover
    add(cover(client,ahj,system,sysd['ul'],sysd.get('prod'),warn=_warn,contractor=contractor,system2=system2))
    # 2) Universal Building Permit Application (in every AHJ folder)
    uni=os.path.join(MB,ahj,'01_Permit_Application_Packet','PBC_01_Building_Permit_Application.pdf')
    if not os.path.exists(uni): uni=os.path.join(PBCPK,'PBC_01_Building_Permit_Application.pdf')
    if os.path.exists(uni):
        T=dict(CONST_UNI)
        if contractor: T.update(_contractor_overrides(contractor))
        T.update({'Property_Owner':client.get('owner',''),'Property_owner_address':client.get('address',''),
          'Property_owner_city':_city(client, ahj),'Property_owner_state':'FL','Property_owner_zip':client.get('zip',''),
          'Property-owner-phone':client.get('phone',''),'Project-address':client.get('address',''),'project-city':_city(client, ahj),
          'Legal-description':client.get('legal',''),'permit-value':_permit_value(client),'owner-printed-name':client.get('owner',''),
          'property-owner-email':client.get('owner_email',''),
          'further_work_description':_work_description(pairs)})
        _miss=[lbl for lbl,k in (('phone','phone'),('e-mail','owner_email')) if not str(client.get(k) or '').strip()]
        if _miss:
            _hand("Property owner's %s: not provided - write it in box 2 of the permit application."%' and '.join(_miss))
        # NET S.F. (for SFD's) = total roof area in square feet. Confirmed against the
        # APPROVED City of Boca permit BPQ-2023-01531 (Hudson, 455 NE 28th Rd), which
        # carries "NET S.F (for SFD's): 3274" on this same form. We already hold the
        # figure as client['area'] and were simply never writing it. Audit C3, 2026-07-21.
        _nsf=_net_sf(client)
        if _nsf: T['net-square-feet']=_nsf
        for i,d in enumerate(_pcn_digits(client.get('pcn','')),start=1): T['PCN-%d'%i]=d
        add(fill_form(uni,T,CONST_UNI_B,'%s pre-filled - sign & notarize; record NOC before first inspection'%_tenant_short(contractor)))
    # 3) Notice of Commencement
    noc=os.path.join(MB,ahj,'01_Permit_Application_Packet','PBC_02_Notice_of_Commencement.pdf')
    if not os.path.exists(noc): noc=os.path.join(PBCPK,'PBC_02_Notice_of_Commencement.pdf')
    if os.path.exists(noc):
        _ctr_city=_C(contractor,'city'); _ctr_st=_C(contractor,'state'); _ctr_zip=_C(contractor,'zip')
        T={'Name':_C(contractor,'name_short'),'Address 1':_C(contractor,'address_long'),
          'Address 2':'%s, %s %s'%(_ctr_city,_ctr_st,_ctr_zip),'State of':'Florida','County of':_C(contractor,'notary_county'),
          'PROPERTY CONTROL NUMBER PCNTAX FOLIO NO':pcn_display(client.get('pcn','')),'2 GENERAL DESCRIPTION OF IMPROVEMENT':'Reroof',
          '1 DESCRIPTION OF PROPERTY Legal description':(client.get('address','')+((' -- '+client.get('legal','')) if client.get('legal','') else '')),
          'a Name and address':'%s, %s, %s FL %s'%(client.get('owner',''),client.get('address',''),_city(client, ahj),client.get('zip','')),
          'b Interest in property':'Owner',
          '4 a CONTRACTOR Name  Address':'%s, %s, %s, %s %s'%(_C(contractor,'name_short'),_C(contractor,'address_long'),_ctr_city,_ctr_st,_ctr_zip),
          'b Phone number':_C(contractor,'phone')}
        add(fill_form(noc,T,{},'%s pre-filled - owner to sign & record with PBC Clerk'%_tenant_short(contractor)))
    # 4) Boca-specific roofing forms (full system fill); other AHJs -> system spec sheet
    if ahj=='Boca_Raton':
        sup=os.path.join(MB,ahj,'01_Permit_Application_Packet','Boca_06_Supplemental_Roof.pdf')
        mit=os.path.join(MB,ahj,'01_Permit_Application_Packet','Boca_02_ReRoofing_Mitigation_Package.pdf')
        if os.path.exists(sup):
            T={'Contractors Name':_C(contractor,'name_short'),'License':_C(contractor,'license'),'OwnersName':client.get('owner',''),'Job Address':client.get('address',''),
              'Existing Roofing Type Matl':(client.get('existing') or ''),'DeckType':'CDX','Roof Slope':client.get('slope',''),
              'Mean Roof Height':(str(client.get('mrh'))+"'") if client.get('mrh') else '','Total Roof Area This Perm':client.get('area',''),'ROOF COVERING MANUFACTURER':sysd['mfr'],
              'Product Approval':sysd['prodfl'],'Product':sysd['prod'],'Base Sheet':sysd['ul'],'Product Approval_2':sysd['ulfl'],'Product Approval_3':sysd['ulfl']}
            # Flat system: Section D (page 4 of the Boca_06 form) needs its own set of fields —
            # separate from Section A (sloped), which uses different field names.
            # Product Approval_7 / System Number / Mean Roof Height_2 / Deck Type (spaced) are
            # the Section D variants; without them the top of Section D stays blank.
            _flat_cap=SPEC_SHEET.get('Flat',{}).get('cap','') if system=='Flat' else ''
            if _flat_cap: T['Cap Sheet']=_flat_cap; T['Top Ply']=_flat_cap
            if system=='Flat':
                # Section D is the FLAT roof section: its manufacturer line is filled only on a flat job
                # (a shingle job printed its shingle maker there - 2026-11-18 eye check).
                T['Roof System Manufacturer']=sysd['mfr']
                T['Product Approval_7']=sysd['prodfl']            # Section D: Product Approval #
                T['System Number']=sysd.get('fl_system','W-179')+' (Table 1K)'  # governing listing in FL1654
                T['Mean Roof Height_2']=(str(client.get('mrh'))+"'") if client.get('mrh') else ''  # Section D MRH field
                T['Deck Type']='CDX'                              # Section D (spaced) vs DeckType (pg 1)
                T['Support Spacing']='6" o.c.'
                T['Fire or Vapor Barrier']='N/A'
                # Section C laps/fasteners: membrane is fully self-adhered (Table 1K W-179) —
                # laps per Polyglass spec, no mechanical attachment of the membrane.
                T['Product Approval_4']=sysd['prodfl']            # Cap Sheet PA# (Section C)
                T['Head lap in inches']='3" side / 6" end'
                T['Lap']='N/A'; T['Field_1']='N/A'; T['Rows']='N/A'
                T['Drip Edge  Size  Fastener Spacing']='26ga metal - 4" o.c.'
                # Section D assembly rows. Non-insulated, no nailer, both plies self-adhered.
                T['Maximum Design Pressure from the specific product approval system']=sysd.get('fl_mdp','-90.0 psf')
                T['Wood Nailer']='N/A'; T['Nailer Fastener Type and Spacing']='N/A'
                T['Insulation Base Layer size  Thickness']='N/A'; T['FastenerBonding Matl']='N/A'
                T['Insulation Top Layer Size  Thickness']='N/A'; T['FastenerBonding Matl_2']='N/A'
                T['Fastener  Per Insulation Board  Zone 11']='N/A'; T['Zone 1_2']='N/A'; T['Zone 2_3']='N/A'; T['Zone 3_3']='N/A'
                T['Fastener Type']='N/A - fully self-adhered'
                T['Ply Sheets of Plys']='N/A'; T['FastenerBondg Matl']='N/A'
                T['AnchorBase Sheet   of Plys']='Elastobase SA - 1 ply'; T['FastenerBondg Matl_2']='Self-adhered'
                # Base-sheet fastener spacing grid (per-zone laps/rows): no fasteners on an SA system.
                T['Zone 11']='N/A'; T['laps']='N/A'; T['rows']='N/A'
                T['Zone 1_3']='N/A'; T['laps_2']='N/A'; T['rows_2']='N/A'
                T['Zone 2_4']='N/A'; T['laps_3']='N/A'; T['rows_3']='N/A'
                T['Zone 3_4']='N/A'; T['laps_4']='N/A'; T['rows_4']='N/A'
                T['FastenerBondg Matl_3']='Self-adhered'          # Top Ply attachment
                T['Drip Edge Size  Fastening Spacing']='26ga metal - 4" o.c.'
            B={'Use of Building':'1 or 2 Family','Re-Roofing/ Re-Covering -Attach Mitigation Package':'On',sysd['box']:'On','Minimum 3':'On'}
            if client.get('exposure'): B['Exposure Category']=client['exposure']
            if system=='Tile': T['ManufacturerProduct']=sysd['adh']; T['Product Approval_6']=sysd['adhfl']; T['Tile Profile']='Flat'
            # Section C wind-attachment block for the sloped systems (Flat uses Section D,
            # filled above). Values come from _SECTION_C, which mirrors the library's
            # approved Boca templates. PE-sealed pressure fields stay blank by design.
            T.update(_SECTION_C.get(system,{}))
            # Certification: qualifier is always known, owner from the job. Signatures stay blank (wet-sign/notary).
            T[u'Qualifier’s Name']=_C(contractor,'qualifier'); T['Property Owners Name']=client.get('owner','')
            # "Area of roofing work by slope": route the total area into the bucket the form asks for,
            # by the entered slope (x/12): <2 = flat, 2-4 = low slope, >4 = steep. Flat system -> flat.
            _area=client.get('area','')
            if _area:
                _s=str(client.get('slope','')).strip()
                try: _sn=float(_s.split('/')[0].split(':')[0]) if _s else (0.0 if system=='Flat' else 6.0)
                except Exception: _sn=(0.0 if system=='Flat' else 6.0)
                if system=='Flat' or _sn<2: T['Flat Roof Area']=_area
                elif _sn<=4: T['Low Slope Roof Area']=_area
                else: T['Steep Slope Roof Area']=_area
            # Section D "Flat roof is free draining ___Yes ___No" has no AcroForm widget —
            # stamp the X on the Yes blank (page 4, measured coords). Drip-edge perimeter
            # drainage = free draining; a No answer would require drainage calcs.
            _stmp=[(4,213,487,'X',10)] if system=='Flat' else None
            add(fill_form(sup,T,B,'%s SYSTEM pre-filled - %s'%(_tenant_short(contractor),system),stamps=_stmp))
        if os.path.exists(mit):
            T={'Property address':'%s, %s, FL %s'%(client.get('address',''),client.get('city','') or 'Boca Raton',client.get('zip','')),
              'Specify Secondary Water Barrier':sysd['swb'],'Product approval number':sysd['ulfl'],'Qualifier print name':_C(contractor,'qualifier'),'QualifierOwner Builder print':_C(contractor,'qualifier'),
              'Owner print name':client.get('owner',''),'County of':_C(contractor,'notary_county'),'County of_2':_C(contractor,'notary_county')}
            # 706.8 Q1/Q4 from the county property record. Stamped per-widget (NOT via the
            # field name: Yes and No share one name, so filling it would tick both).
            _q1,_q4=_boca_mitigation_answers(client)
            _st=[]
            for _k,_a in (('q1',_q1),('q4',_q4)):
                if _a:
                    _x,_y=_BOCA_MIT_XY[(_k,_a)]
                    _st.append((_BOCA_MIT_PAGE,_x,_y,'X',11))
            _note=None
            if _st:
                _confirm('Boca 706.8 Mitigation Package Q1/Q4 are answered from the PBC Property Appraiser record '
                         '(year built / improvement value). Verify both before signing.')
            if _q1 is None or _q4 is None:
                _hand('Boca 706.8 Mitigation Package: answer Q1 (permit applied for on/after 1/1/1988) and Q4 '
                      '(building value $300,000 or more) where the property record could not.')
            add(fill_form(mit,T,{sysd['swb_box']:'On'},_note,stamps=_st or None))
    # 4b) City-specific supplemental forms — the Permit Library IS the database: pull every
    #     form in the AHJ's application-packet folder that we didn't already fill above (re-roof
    #     affidavit, asbestos notification, wind-load chart, owner-builder, hot-process, etc.).
    #     If the raw form is fillable, fill the JOB ADDRESS + customer contractor info; otherwise fall
    #     back to the pre-filled copy. Skip the city's duplicate app/NOC (covered by PBC_01/02).
    T_supp=contractor_fill_T(client, ahj, contractor=contractor)   # universal contractor + this-job fill
    apk=os.path.join(MB,ahj,'01_Permit_Application_Packet')
    pre=os.path.join(MB,ahj,'_Prefilled')
    _cmap=city_map(ahj) or {}
    _cfiles=_cmap.get('files') or {}
    _req_ids={f for f,_ in required_forms(ahj, systems)[0]}
    _city_covered=set()       # systems whose city roof checklist is in the packet
    _exp_in=False             # the PB-O-131 expedited worksheet is in the packet (unincorporated PBC)
    if os.path.isdir(apk):
        for fn in sorted(os.listdir(apk)):
            low=fn.lower()
            if not low.endswith('.pdf'): continue
            stem=fn[:-4]
            if stem.startswith('PBC_01') or stem.startswith('PBC_02'): continue
            if fn in _cfiles:
                _city_covered |= _city_file(w, ahj, fn, os.path.join(apk,fn), _cfiles[fn], client, contractor,
                                            pairs, system, T_supp, _req_ids)
                continue
            _grole,_gwhy=_generic_file_role(fn)
            if _grole=='owner_builder' and APPLICANT[0]=='owner_builder':
                _grole=None
            if _grole:
                _REPORT.setdefault('dropped', []).append('%s (%s)'%(stem,_gwhy))
                if _grole=='registration':
                    _confirm('%s registers contractors once, separately from the permit (%s). It is not part of this '
                             'packet: confirm you are registered with %s before you submit.'
                             %(ahj_label(ahj), stem.replace('_',' '), ahj_label(ahj)))
                continue
            # County roofing forms are attached once, system-matched, in step 4c below.
            # Never let the blanket loop staple another system's form onto this packet.
            if roof_form_system(fn): continue
            # Boca_01 is the city's master reference PDF (redundant with 02+06); Boca_02/06 filled in step 4
            if stem in ('Boca_01_Boca_Permit_Packet','Boca_02_ReRoofing_Mitigation_Package','Boca_06_Supplemental_Roof'): continue
            if 'notice_of_commencement' in low or 'building_permit_application' in low: continue
            raw=os.path.join(apk,fn)
            _flds=None
            try: _flds=PdfReader(raw).get_fields()
            except Exception: pass
            if _flds:                                   # fillable -> stamp job address + contractor, flatten
                _sf=_spec_sheet_fill(raw, client, ahj, system, sysd)
                _f2=None if _sf else _form200_fill(raw, client, ahj, system, sysd, contractor)
                _fx=None if (_sf or _f2) else _expedited_fill(raw, client, ahj, system, sysd, contractor, pairs=pairs)
                if _fx and _pbo131_status=='no':
                    # NOT a legal route for this structure. Emitting it would ask the qualifier
                    # to initial a false certification. Drop the form; the cover carries _warn.
                    continue
                if _fx:                                  # PBC Expedited Re-Roof Worksheet (PB-O-131 A+B)
                    _exp_in=True
                    _Ts,_Bs=_fx; _T=dict(T_supp); _T.update(_Ts)
                    _hand('PB-O-131 Attachment B: the QUALIFIER initials all 6 certifications by hand, then signs and dates.')
                    _note=None
                    if _EXP_EXISTING.get(_existing_bucket(client.get('existing')) or
                                         _papa_existing_cover(client,systems)) and not client.get('existing'):
                        _confirm('PB-O-131 existing roof covering is taken from the PBC Property Appraiser record '
                                 '(public record, may be stale): verify on site.')
                    # DESIGN CRITERIA lines 1) & 2): the county SUPPLIES the chart (PB-O-131 p3),
                    # so line 1 is a lookup, not a sealed calculation. The form has NO AcroForm
                    # widget for either line, so we do NOT stamp them - a blind coordinate stamp
                    # is exactly how a value lands on the wrong field. We surface the looked-up
                    # value and its basis in the topnote for the qualifier to enter by hand.
                    _psf,_basis=_pbo131_required_pressure(client.get('exposure'), client.get('mrh'),
                                                          _pbo131_roof_form(client), client.get('slope'))
                    if _psf:
                        _mdp=' / '.join(x for x in [p[1].get('fl_mdp') for p in pairs] if x)
                        _hand('PB-O-131 DESIGN CRITERIA (enter by hand): required %.1f psf per %s; proposed system %s.'%(
                            _psf,_basis,_mdp or 'see product approval'))
                    add(fill_form(raw, _T, _Bs, _note))
                elif _sf:                                # Roof Construction Specifications Sheet -> fill system spec + roof data
                    _Ts,_Bs=_sf; _T=dict(T_supp); _T.update(_Ts)
                    add(fill_form(raw, _T, _Bs, '%s %s system spec pre-filled - verify roof height/slope/area & sign per job'%(_tenant_short(contractor),system)))
                elif _f2:                                # PBC Form 200 (tile) + PBO-94 roof-to-wall retrofit
                    _Ts,_Bs=_f2; _T=dict(T_supp); _T.update(_Ts)
                    add(fill_form(raw, _T, _Bs,
                        '%s pre-filled - design pressures (LPZ/HPZ) & tile uplift values require the sealed PE calculation'%_tenant_short(contractor)))
                else:
                    add(fill_form(raw, T_supp, {}, None))
            elif fn in _PBC_STAMP_FORMS:                 # flattened but coords known -> overlay-stamp
                _stamp_pdf(w, raw, _PBC_STAMP_FORMS[fn], _pbc_supp_values(client, ahj, contractor))
                if fn=='DEL_Roof_Mitigation.pdf':
                    _confirm('Delray FBC 706.8 Roof Mitigation: option (b) is pre-selected (roof-to-wall connections '
                             'cannot be done within 15% of the reroof cost), as on the approved Delray permit. Confirm it '
                             'applies to this job; attach the cost estimate. The owner signs and dates it.')
            else:                                       # flattened, no coords -> raw blank, filled by hand
                if _add_pdf(w, raw):
                    if ('ahj:'+stem) in _req_ids:
                        _need('form:ahj:%s:blank'%stem, stem.replace('_',' '),
                              'required by the approved %s permits and attached BLANK (non-fillable; no stamp map yet)'%ahj_label(ahj),
                              'staff', rank=5)
                    else:
                        _hand('%s: non-fillable form, attached blank - the job and contractor details are filled in by hand at signing.' % stem.replace('_',' '))
    if APPLICANT[0]=='owner_builder' and not _is_broward(ahj):
        _ob_have=os.path.isdir(apk) and any(_OWNER_BUILDER_FN.search(f) for f in os.listdir(apk))
        _ob=os.path.join(PBCPK,'PBC_05_Owner_Builder_Affidavit_(if_applicable).pdf')
        if not _ob_have and os.path.exists(_ob):
            add(fill_form(_ob,{},{}))
    # 4c) The county roofing form for THIS system — exactly one, never another system's.
    #     Prefer the AHJ's own copy of that system's form (cities republish the county form
    #     and some add local edits); fall back to the county master in PBCPK.
    #     MULTI-TRADE: exactly one form PER SYSTEM ON THE PERMIT, and never a system's form
    #     that is not on this permit. (Evidence note: the accepted Tota packet carried NO
    #     Form 100/200/300/400 at all - the PB-O-131 expedited worksheet stood in for them.
    #     We keep attaching the system-matched form because it is additive and harmless,
    #     whereas a WRONG system's form is the actual defect this map exists to prevent.)
    _own=_cmap.get('own_roof_forms') or {}
    for _sysN, _sdN, _arN in pairs:
      _rf=_PBC_ROOF_FORM.get(_sysN)
      if _rf and _exp_in and _pbo131_status=='ok':
        # Unincorporated PBC, EXPEDITED route (PB-O-131, detached single-family confirmed): the worksheet IS the
        # roofing submittal. The approved expedited permits (Tota B-2025-050070-0000, Marrero R-25216) carry
        # PB-O-131 and no Form 100-400; the Form + its PBO-94 pages belong to the PB-O-094 route (Rothstein).
        _REPORT.setdefault('dropped', []).append('PBC Form %s (expedited PB-O-131 route: the worksheet is the roofing submittal)'
                                                 %{'Shingle':'100','Tile':'200','Metal':'300','Flat':'400'}[_sysN])
        continue
      if _rf and _cmap.get('county_roof_form') is False:
        # This city's approved permits carry no county Reroofing Installation Summary at all (and its
        # PBO-94 pages are the county's own policy); the city's own 706.8 form is in the packet instead.
        _REPORT.setdefault('dropped', []).append('PBC Form %s (not in %s approved permits)'
                                                 %({'Shingle':'100','Tile':'200','Metal':'300','Flat':'400'}[_sysN], ahj_label(ahj)))
        continue
      if _rf and _sysN in _own and (_sysN in _city_covered or _own.get(_sysN, {}).get('always')):
        # The city's own roofing checklist for this system is in the packet; the approved
        # permits for this city carry that form, not the county Reroofing Installation Summary.
        _REPORT.setdefault('dropped', []).append('PBC Form %s (%s uses its own %s checklist)'
                                                 %({'Shingle':'100','Tile':'200','Metal':'300','Flat':'400'}[_sysN], ahj_label(ahj), _sysN))
        continue
      if _rf:
        _src=None
        if os.path.isdir(apk):
            for fn in sorted(os.listdir(apk)):
                if fn.lower().endswith('.pdf') and roof_form_system(fn)==_sysN:
                    _src=os.path.join(apk,fn); break
        if not _src:
            _c=os.path.join(PBCPK,_rf)
            _src=_c if os.path.exists(_c) else None
        if _src:
            _flds=None
            try: _flds=PdfReader(_src).get_fields()
            except Exception: pass
            if _flds:
                # One filler per system: 100 Shingle / 200 Tile / 300 Metal / 400 Flat.
                # Each returns None for a non-matching system AND for a non-matching form,
                # so a form can never be pre-filled for the wrong roof system. Evaluated
                # first-wins; the guards are mutually exclusive, the chaining is belt-and-
                # braces (NB _is_form200() alone also matches Forms 100/300 - it is the
                # system!='Tile' check inside _form200_fill that keeps them apart).
                _f1=_form100_fill(_src, client, ahj, _sysN, _sdN, contractor)
                _f2=None if _f1 else _form200_fill(_src, client, ahj, _sysN, _sdN, contractor)
                _f3=None if (_f1 or _f2) else _form300_fill(_src, client, ahj, _sysN, _sdN, contractor)
                _f4=None if (_f1 or _f2 or _f3) else _form400_fill(_src, client, ahj, _sysN, _sdN, contractor)
                _T=dict(T_supp); _Bs={}
                _fx=_f1 or _f2 or _f3 or _f4
                if _fx:
                    _Ts,_Bs=_fx; _T.update(_Ts)
                add(fill_form(_src, _T, _Bs,
                    '%s pre-filled - design pressures & uplift values require the sealed PE calculation'%_tenant_short(contractor)))
            else:
                if _add_pdf(w, _src):
                    _hand('%s: non-fillable form, attached blank - filled in by hand.' % os.path.basename(_src)[:-4].replace('_',' '))
    # 5) product approvals — for EVERY system on the permit. Each system's primary approval
    #    (index 0) gets the governing system row highlighted so the reviewer can find the one
    #    applicable listing. Multi-trade: both systems' approvals, de-duplicated (Shingle and
    #    Flat can share an underlayment approval and we must not bind it twice).
    _seen=set()
    for _sysN,_sdN,_arN in (pairs if _MODE[0]!='blank' else []):
        _hl=_sdN.get('hl')
        for i,rel in enumerate(_sdN['approvals']):
            if rel in _seen: continue
            _seen.add(rel)
            p=os.path.join(PA,rel)
            if os.path.exists(p):
                if _hl:
                    # Excerpt EVERY approval that actually carries the governing system,
                    # not just the primary (i==0). For Flat, the underlayment approval is
                    # the same 100+ page FL1654 PEER report as the membrane approval, so
                    # highlighting only the first still bound ~120 redundant pages.
                    # _pa_excerpt no-ops (returns the whole file) when the token is absent.
                    p=_pa_excerpt(p, _hl['token'], _hl['note'])
                try:
                    rr=PdfReader(p)
                    if rr.is_encrypted: rr.decrypt('')
                    add(rr.pages)
                except Exception: pass
    # 6) PBC Property Appraiser value page (required for mitigation threshold verification)
    if fetch_pa and client.get('pcn') and _MODE[0]!='blank':
        pa_pdf=_fetch_pbc_pa_pdf(client['pcn'])
        if pa_pdf: _add_pdf(w,pa_pdf)
    # 7) attached docs (RoofGraf, etc.)
    for ap in (attachment_paths or []) if _MODE[0]!='blank' else []:
        try:
            rr=PdfReader(ap)
            if rr.is_encrypted: rr.decrypt('')
            add(rr.pages)
        except Exception: pass
    with open(out_path,'wb') as fh: w.write(fh)
    return out_path


# =============================================================================
# COMPLETENESS: required forms + required values, fail closed
# =============================================================================
# The answer key (answer_key/answer_key.json) was mined from the real APPROVED / ISSUED
# permit submittals: for each AHJ + roof system, which government forms the building
# department actually received. fingerprints.json holds the text patterns that identify
# each form on a page. Every build is checked against it, and against the job values a
# reviewer needs, and anything not on the paper is reported - never left as a silent blank.
import json as _json

ENGINE_NEUTRAL = True        # marker: this engine carries no house identity (worker loads it as-is)
LAST_REPORT = None
_AK_DIR = os.path.join(_HERE, 'answer_key')
_AK = None


class PacketIncomplete(Exception):
    """A strict build found required forms or values it could not fill."""
    def __init__(self, report):
        self.report = report
        Exception.__init__(self, 'Packet incomplete - %d required item(s) missing: %s' % (
            len(report['missing']), '; '.join(m['item'] for m in report['missing'])[:900]))


def _answer_key():
    global _AK
    if _AK is None:
        try:
            ak = _json.load(open(os.path.join(_AK_DIR, 'answer_key.json'), encoding='utf8'))
            fp = _json.load(open(os.path.join(_AK_DIR, 'fingerprints.json'), encoding='utf8'))
        except Exception:
            ak, fp = [], {}
        _AK = (ak, fp)
    return _AK


def _norm_page(t):
    return re.sub(r'[^A-Z0-9]+', ' ', (t or '').upper()).strip()


# System-specific forms: required only for the matching system on the permit.
_FORM_SYSTEM = {'county:PBC_04_Shingle_Roofing_Form100': 'Shingle',
                'county:PBC_04_Concrete_Clay_Tile_Roofing_Form200': 'Tile',
                'county:PBC_04_Metal_Roofing_Form300': 'Metal',
                'county:PBC_04_Flat_Roofing_Form400': 'Flat'}
# Pages of the county application that the library's prefilled templates duplicated; BC_03
# itself carries Sections A-D, so these are never required separately.
_NOT_REQUIRED_PREFIX = ('broward:HVHZ_', 'shared:')


def required_forms(ahj, systems):
    """[(form_id, title)] the approved submittals for this AHJ show are required, and
    [gap dicts] for forms those submittals carried that the library does NOT have."""
    ak, fp = _answer_key()
    req, gaps = {}, {}
    broward = _is_broward(ahj)
    for ex in ak:
        if ex.get('strength') != 'complete':
            continue
        same = ex.get('ahj') == ahj
        for g in ex.get('gaps', []):
            gid = g.get('gap_id', '')
            countywide = 'countywide' in (g.get('note', '') + g.get('title', '')).lower()
            if same or (broward and countywide and ex.get('region') == 'Broward'):
                gaps.setdefault(gid, g)
        if not same:
            continue
        for fid in ex.get('required_form_ids', []):
            if fid.startswith(_NOT_REQUIRED_PREFIX) or fid not in fp:
                continue
            s = _FORM_SYSTEM.get(fid)
            if s and s not in systems:
                continue
            req.setdefault(fid, fid.split(':', 1)[1].replace('_', ' '))
    # A system-specific county form is required whenever its system is on a PBC permit,
    # whether or not an approved example of that exact combination exists.
    if not broward:
        _cm = city_map(ahj) or {}
        own = dict(_cm.get('own_roof_forms') or {})
        if _cm.get('county_roof_form') is False:
            own.update({s_: {} for s_ in _FORM_SYSTEM.values()})
        for fid in [f for f, s in _FORM_SYSTEM.items() if s in own]:
            req.pop(fid, None)       # the city's own checklist for that system stands in for the county form
        for fid, s in _FORM_SYSTEM.items():
            if s in own:
                continue
            if s in systems and fid in fp:
                req.setdefault(fid, fid.split(':', 1)[1].replace('_', ' '))
    return sorted(req.items()), list(gaps.values())


def detect_forms(pdf_path):
    """{form_id: [page numbers]} of the answer-key forms present in a built PDF."""
    _, fp = _answer_key()
    pats = {k: [re.compile(x) for x in v.get('patterns', [])] for k, v in fp.items()
            if isinstance(v, dict) and v.get('patterns') and ':' in k}
    out = {}
    try:
        import fitz
        doc = fitz.open(pdf_path)
        texts = [pg.get_text() or '' for pg in doc]
        doc.close()
    except Exception:
        texts = [(pg.extract_text() or '') for pg in PdfReader(pdf_path).pages]
    for i, t in enumerate(texts, 1):
        n = _norm_page(t)
        for k, rxs in pats.items():
            if any(r.search(n) for r in rxs):
                out.setdefault(k, []).append(i)
    return out, texts


_VALUE_QUESTIONS = [
    # key, client field, item, question, rank
    ('owner', 'owner', 'Property owner name', 'Who is the property owner, exactly as on the deed?', 1),
    ('value', 'value', 'Contract price (permit value)', 'What is the contract price for this work?', 2),
    ('mrh', 'mrh', 'Mean roof height', "What is the roof's mean height in feet?", 3),
    ('area', 'area', 'Roof area', 'What is the total roof area being replaced (square feet or squares)?', 4),
    ('slope', 'slope', 'Roof slope', 'What is the roof slope (for example 5/12)?', 5),
    ('zip', 'zip', 'Job ZIP code', 'What is the ZIP code of the job address?', 8),
    ('pcn', 'pcn', 'Parcel number (PCN / folio)',
     "What is the property's parcel number (PCN or folio)? It is on the tax bill and the property appraiser's site.", 10),
    ('legal', 'legal', 'Legal description',
     'Please upload the deed, or the property appraiser page that shows the legal description.', 11),
]


def _precheck(client, ahj, systems, underlayment, product, product_confirmed):
    for key, fld, item, q, rank in _VALUE_QUESTIONS:
        if not str(client.get(fld) or '').strip() or str(client.get(fld)).strip().upper() in ('DATA NEEDED', 'N/A'):
            _need(key, item, 'printed on the permit application / roofing forms; left blank', 'customer', q, rank)
    if not str(client.get('address') or '').strip():
        _need('address', 'Job address', 'every form', 'customer', 'What is the job street address?', 0)
    if (ahj == 'Boca_Raton' or _is_unincorporated_pbc(ahj)) and not client.get('existing'):
        _need('existing', 'Existing roof covering', 'the roofing forms ask what is being removed', 'customer',
              'What roof covering is on the building now (shingle, tile, metal or flat)?', 12)
    if 'area2' in client or len(systems) > 1:
        if len(systems) > 1 and not str(client.get('area2') or '').strip():
            _need('area2', 'Roof area of the %s section' % systems[1], 'second system on the permit', 'customer',
                  'What is the roof area of the %s section?' % systems[1].lower(), 6)
    if product_confirmed is None:
        product_confirmed = bool(product)
    if not product_confirmed:
        names = ', '.join('%s %s' % (SYSTEMS[s]['mfr'], SYSTEMS[s]['prod']) for s in systems)
        _need('product', 'Roof covering product (manufacturer, product, approval #)',
              'not confirmed - this draft shows %s as a placeholder' % names, 'customer',
              'Which roof covering product will you install (manufacturer and product name)? '
              'Upload its Florida product approval or Miami-Dade NOA if you have it.', 6)
    if not underlayment:
        _need('underlayment', 'Underlayment product',
              'not confirmed - this draft shows the standard self-adhered underlayment as a placeholder', 'customer',
              'Which underlayment will you use? Upload its product approval if you have it.', 13)


def _value_variants(key, v):
    v = str(v or '').strip()
    if not v:
        return []
    out = {v}
    num = re.sub(r'[^0-9.]', '', v)
    if key in ('area', 'area2') and num:
        try:
            f = float(num)
            out |= {'{:,.0f}'.format(f), '%d' % round(f), ('%.1f' % (f / 100)).rstrip('0').rstrip('.'),
                    '%.2f' % (f / 100)}
        except ValueError:
            pass
    if key == 'slope':
        m = re.match(r'\s*(\d+(?:\.\d+)?)', v)
        if m:
            out.add(m.group(1))
    if key == 'mrh' and num:
        out.add(num.rstrip('0').rstrip('.') if '.' in num else num)
    return [x for x in out if x]


def _render_check(client, texts, systems, forms_found):
    """Roof values the caller supplied must reach a FORM page (not just the cover). If no
    form in this AHJ's set has a field for one, the cover checklist says so explicitly."""
    body = '\n'.join(texts[1:])           # page 1 is the cover
    flat = re.sub(r'\s+', ' ', body)
    labels = {'area': 'roof area', 'slope': 'roof slope', 'mrh': 'mean roof height'}
    for key in ('area', 'slope', 'mrh'):
        vs = _value_variants(key, client.get(key))
        if not vs:
            continue
        if not any(re.search(r'(?<![\d.])' + re.escape(x) + r'(?![\d])', flat) for x in vs):
            _REPORT['nofield'].append(
                'No form in this jurisdiction\'s set has a field for the %s (%s) - it is stated on the '
                'cover; write it on the application if the reviewer asks.' % (labels[key], client.get(key)))


def _checklist_pages(report, draft, mode='ask'):
    buf = io.BytesIO(); c = canvas.Canvas(buf, pagesize=letter); W, H = letter
    y = [H - 60]

    def line(txt, font='Helvetica', size=9, color=HexColor('#222'), indent=70, gap=12):
        width = W - indent - 54
        words = str(txt).split(' ')
        cur = ''
        for wd in words:
            t = (cur + ' ' + wd).strip()
            if stringWidth(t, font, size) > width and cur:
                _emit(cur, font, size, color, indent); cur = wd
            else:
                cur = t
        if cur:
            _emit(cur, font, size, color, indent)
        y[0] -= gap - 12

    def _emit(t, font, size, color, indent):
        if y[0] < 60:
            c.showPage(); y[0] = H - 60
        c.setFillColor(color); c.setFont(font, size)
        c.drawString(indent, y[0], t.encode('latin-1', 'replace').decode('latin-1')); y[0] -= 12

    if mode == 'blank':
        c.setFillColor(NAVY); c.setFont('Helvetica-Bold', 15)
        c.drawCentredString(W / 2, y[0], "WHAT'S IN THIS PACKET"); y[0] -= 26
        for i, f in enumerate(report.get('blank_forms') or [], 1):
            nm = re.sub(r'\.pdf$', '', f, flags=re.I).replace('_', ' ')
            line('%d.  %s' % (i, nm), 'Helvetica-Bold', 9.5, NAVY, 60)
            line(_purpose(f), indent=78)
            y[0] -= 4
        y[0] -= 6
        line('WHERE TO SUBMIT', 'Helvetica-Bold', 10, NAVY, 60)
        line(report.get('submit_to') or '', indent=70)
        y[0] -= 8
        line('ALWAYS: fill in every form, sign and notarize where indicated, record the Notice of Commencement, and attach the '
             'product approvals, the roof measurement report and any PE-sealed calculation before submitting.',
             'Helvetica', 8.5, HexColor('#555'), 60)
        c.showPage(); c.save(); buf.seek(0)
        return PdfReader(buf).pages
    if mode == 'asis':
        c.setFillColor(NAVY); c.setFont('Helvetica-Bold', 13)
        c.drawCentredString(W / 2, y[0], 'FILLED WITH THE INFORMATION YOU GAVE US'); y[0] -= 16
        c.setFont('Helvetica', 9); c.setFillColor(HexColor('#222'))
        c.drawCentredString(W / 2, y[0], 'Blanks are for you to complete. Every blank field or form is listed below.'); y[0] -= 26
        blanks = sorted(report['missing'], key=lambda x: x.get('rank', 50))
        if blanks:
            line('BLANK - FOR YOU TO COMPLETE', 'Helvetica-Bold', 10, RED, 60)
            for m in blanks:
                line('[ ]  %s - %s' % (m['item'], m['why']), indent=70)
            y[0] -= 8
    elif draft:
        c.setFillColor(RED); c.setFont('Helvetica-Bold', 15)
        c.drawCentredString(W / 2, y[0], 'PREBUILT: PENDING YOUR CHANGES'); y[0] -= 18
        c.setFont('Helvetica', 9)
        c.drawCentredString(W / 2, y[0], 'This draft is not ready to submit. The items below are blank on the forms until answered.')
        y[0] -= 26
    else:
        c.setFillColor(NAVY); c.setFont('Helvetica-Bold', 15)
        c.drawCentredString(W / 2, y[0], 'PACKET CHECKLIST'); y[0] -= 30
    sections = [] if mode == 'asis' else [
        ('STILL NEEDED FROM YOU', [m for m in report['missing'] if m['who'] == 'customer'], RED),
        ('BEING HANDLED BY OUR STAFF', [m for m in report['missing'] if m['who'] == 'staff'], RED),
    ]
    for title, items, col in sections:
        if not items:
            continue
        line(title, 'Helvetica-Bold', 10, col, 60)
        for m in sorted(items, key=lambda x: x.get('rank', 50)):
            line('[ ]  %s - %s' % (m['item'], m['why']), indent=70)
        y[0] -= 8
    other = [('CONFIRM BEFORE SIGNING', report['confirm']),
             ('COMPLETE BY HAND AT SIGNING', report['hand']),
             ('NOT ON ANY FORM IN THIS SET', report['nofield']),
             ('FOR YOUR FLORIDA ENGINEER (PE-SEALED - LEFT BLANK ON PURPOSE)', report['pe'])]
    for title, items in other:
        if not items:
            continue
        line(title, 'Helvetica-Bold', 10, NAVY, 60)
        for t in items:
            line('[ ]  ' + t, indent=70)
        y[0] -= 8
    line('ALWAYS: sign and notarize where indicated, record the Notice of Commencement, and attach the '
         'roof measurement report before submitting.', 'Helvetica', 8.5, HexColor('#555'), 60)
    c.showPage(); c.save(); buf.seek(0)
    return PdfReader(buf).pages


def _finalize(out_path, report, draft, mode='ask'):
    """Insert the checklist after the cover; on a draft, label every page."""
    r = PdfReader(out_path)
    w = PdfWriter()
    pages = list(r.pages)
    if mode == 'blank':
        report['submit_to'] = _submit_line(report.get('ahj') or '') if report.get('ahj') else ''
    chk = list(_checklist_pages(report, draft, mode))
    order = pages[:1] + chk + pages[1:]
    for i, pg in enumerate(order):
        if draft:
            Wd = float(pg.mediabox.width); Hd = float(pg.mediabox.height)
            buf = io.BytesIO(); cv = canvas.Canvas(buf, pagesize=(Wd, Hd))
            cv.setFillColor(RED); cv.setFont('Helvetica-Bold', 8)
            cv.drawCentredString(Wd / 2, 6, 'PREBUILT DRAFT - PENDING YOUR CHANGES - NOT READY TO SUBMIT  (page %d of %d)'
                                 % (i + 1, len(order)))
            if i == 0:
                cv.setFont('Helvetica-Bold', 13)
                cv.drawCentredString(Wd / 2, Hd - 30, 'PREBUILT: PENDING YOUR CHANGES')
            cv.showPage(); cv.save(); buf.seek(0)
            try:
                pg.merge_page(PdfReader(buf).pages[0])
            except Exception:
                pass
        w.add_page(pg)
    w.add_metadata({'/Title': 'Permit packet%s' % (' (prebuilt draft)' if draft else ' (filled with the information you gave us)'
                                                    if mode == 'asis' else ' (blank forms)' if mode == 'blank' else ''),
                    '/Producer': 'Permit packet engine'})
    tmp = out_path + '.tmp'
    with open(tmp, 'wb') as fh:
        w.write(fh)
    os.replace(tmp, out_path)


def build_packet_report(client, ahj, system, attachment_paths, out_path, underlayment=None, product=None,
                        contractor=None, fetch_pa=True, system2=None, product_confirmed=None, mode='ask',
                        applicant='contractor'):
    """Build the packet and return the completeness report:
        {'path', 'complete': bool, 'missing': [{key,item,why,who,question,rank}],
         'questions': [customer questions, most important first], 'confirm': [...], 'hand': [...],
         'nofield': [...], 'pe': [...], 'forms_found': {...}, 'forms_required': [...]}
    Raises IdentityMissing (nothing is built) when the customer's own company / license /
    qualifier is missing: identity never falls back to anyone else's."""
    global LAST_REPORT
    _rep_reset()
    _REPORT['nofield'] = []
    validate_ahj(ahj)                      # bad input fails loud first, before anything else
    if mode not in MODES:
        raise ValueError('Unknown packet mode %r - expected one of %s' % (mode, ', '.join(MODES)))
    if applicant not in ('contractor', 'owner_builder'):
        raise ValueError('Unknown applicant %r' % (applicant,))
    if applicant == 'owner_builder' and mode != 'blank':
        raise ValueError('Owner-builder packets are prepared as blank forms (mode "blank") or by staff')
    _MODE[0] = mode
    APPLICANT[0] = applicant
    try:
        return _build_packet_report(client, ahj, system, attachment_paths, out_path, underlayment, product,
                                    contractor, fetch_pa, system2, product_confirmed, mode)
    finally:
        _MODE[0] = 'ask'
        APPLICANT[0] = 'contractor'


def _build_packet_report(client, ahj, system, attachment_paths, out_path, underlayment, product,
                         contractor, fetch_pa, system2, product_confirmed, mode):
    global LAST_REPORT
    systems = [system] + ([system2] if system2 else [])
    for s in systems:
        if s not in SYSTEMS:
            raise ValueError("Unknown roof system %r - expected one of %s" % (s, ', '.join(SYSTEMS)))
    if system2 is not None and system2 == system:
        raise ValueError("Secondary roof system %r duplicates the primary system" % (system2,))
    if system2 and _is_broward(ahj):
        raise ValueError("Multi-trade packets are not supported for Broward AHJs yet (%r)" % ahj)
    miss = identity_missing(contractor)
    if miss and mode == 'ask':
        raise IdentityMissing(miss)
    if mode == 'blank':
        return _blank_report(client, ahj, system, system2, systems, out_path, contractor)
    # Job ZIP from the county parcel record when the worksheet has none (Palm Beach only; see
    # papa_site_zip for why only an owner-occupied mailing ZIP is trusted).
    if not str(client.get('zip') or '').strip() and client.get('pcn') and not _is_broward(ahj):
        _z = papa_site_zip(client)
        if _z:
            client = dict(client, zip=_z)
            _confirm('Job ZIP %s is taken from the PBC Property Appraiser record (owner mailing address = site address).' % _z)
    _precheck(client, ahj, systems, underlayment, product, product_confirmed)
    _REPORT['pe'].append('Design wind pressures / uplift (zones 1-3, RAS 127/128, tile moments) are left blank: '
                         'they come from a sealed Florida PE calculation, which is attached separately.')
    _confirm('Installation details pre-filled on the roofing forms (deck type, fasteners, laps, drip edge) follow the '
             "standard installation for the listed product approval - confirm they match your job.")
    if _permit_value(client)[:1].isdigit() and abs(VALUE_FACTOR - 1.0) > 1e-9:
        _confirm('Permit value is printed as the %s: %s. Confirm that is the valuation you declare; some building '
                 'departments ask for the total replacement value (the full contract price).'
                 % (_value_label(), _permit_value_display(client)))
    _build_packet_impl(client, ahj, system, attachment_paths, out_path, underlayment, product,
                       contractor=contractor, fetch_pa=fetch_pa, system2=system2)
    found, texts = detect_forms(out_path)
    req, gaps = required_forms(ahj, systems)
    unincorp_route_094 = any('PB-O-094' in (t or '') for t in texts[:1])
    attached = {f.lower() for f in _REPORT['forms']}
    _, _fp = _answer_key()
    for fid, title in req:
        if fid in found:
            continue
        _lp = str((_fp.get(fid) or {}).get('path', '')).replace('\\', '/').split('/')[-1].lower()
        if _lp and _lp in attached:
            found[fid] = ['attached (scanned form, no text layer)']
            continue
        if 'PBC_06' in fid and unincorp_route_094:
            continue      # expedited worksheet correctly dropped for an attached dwelling (cover says so)
        if fid in _FORM_SYSTEM and any('expedited PB-O-131 route' in d for d in _REPORT.get('dropped', [])):
            continue      # expedited route: the PB-O-131 worksheet stands in for the county Form 100-400
        _need('form:' + fid, title, 'required by the approved submittals for %s and not in this packet' % ahj_label(ahj),
              'staff', rank=5)
    for g in gaps:
        _need(g.get('gap_id', 'gap'), g.get('title', 'form'),
              'seen in approved %s submittals; the government blank is not in our form library yet' % ahj_label(ahj),
              'staff', rank=6)
    _render_check(client, texts, systems, found)
    rep = {k: list(v) for k, v in _REPORT.items()}
    rep['missing'] = sorted(rep['missing'], key=lambda m: m.get('rank', 50))
    rep['questions'] = [m['question'] for m in rep['missing'] if m['who'] == 'customer' and m.get('question')]
    rep['complete'] = not rep['missing']
    rep['forms_found'] = found
    rep['forms_required'] = [f for f, _ in req]
    rep['path'] = out_path
    rep['mode'] = mode
    _finalize(out_path, rep, draft=(mode == 'ask' and not rep['complete']), mode=mode)
    LAST_REPORT = rep
    return rep


# What a form is for, by filename / title words (blank-mode checklist). First match wins.
_FORM_PURPOSE = (
    (r'building[_ ]permit[_ ]application|permit[_ ]application|application[_ ]for[_ ]construction|uniform[_ ]building',
     'Permit application: owner, property, contractor and job value. Owner and contractor sign; notarized.'),
    (r'notice[_ ]of[_ ]commencement|BC_02|PBC_02', 'Notice of Commencement: signed by the owner, notarized and RECORDED with the county clerk; '
     'a certified copy goes to the building department (required when the job value is over $5,000).'),
    (r'hvhz[_ ]uniform[_ ]roofing|BC_03', 'HVHZ Uniform Roofing Application: roof system, materials, slope, height; design pressures from a sealed PE calculation.'),
    (r'owners?[_ ]notification|BC_04', "Owner's notification for roofing (FBC 1524): signed by the owner."),
    (r'asbestos', 'Asbestos notification / statement for roof removal.'),
    (r'owner[_ ]?builder', 'Owner-builder affidavit and disclosure (Fla. Stat. 489.103): the owner signs before the building official or a notary.'),
    (r'form[_ ]?100|shingle', 'Shingle installation form / checklist: pitch, area, underlayment and shingle product approval.'),
    (r'form[_ ]?200|tile', 'Tile installation form / checklist: pitch, height, area, underlayment, tile and adhesive approvals.'),
    (r'form[_ ]?300|metal', 'Metal roof installation form / checklist: pitch, height, area, underlayment and panel approval.'),
    (r'form[_ ]?400|flat', 'Flat roof installation form / checklist: area, membrane system and approval.'),
    (r'expedited', 'PB-O-131 expedited reroof worksheet + checklist (detached single-family only); the qualifier initials each certification.'),
    (r'mitigation|retrofit|706', 'Roof-to-wall / hurricane mitigation (FBC 706.8) form: completed and signed by the qualifier and owner.'),
    (r'affidavit', 'City roofing affidavit: completed and signed by the qualifier.'),
    (r'spec', 'Roof construction specifications sheet: the roof system components.'),
    (r'checklist|package|packet|worksheet', "The city's own reroof worksheet / checklist: completed and signed by the qualifier."),
)


def _purpose(name):
    for rx, txt in _FORM_PURPOSE:
        if re.search(rx, name, re.I):
            return txt
    return 'City form: complete and sign as indicated.'


def _blank_report(client, ahj, system, system2, systems, out_path, contractor):
    """mode 'blank': the form set only, nothing stamped; checklist = what each form is and where it goes."""
    global LAST_REPORT
    _build_packet_impl(client, ahj, system, [], out_path, None, None, contractor=contractor, fetch_pa=False, system2=system2)
    rep = {k: list(v) for k, v in _REPORT.items()}
    rep['missing'] = []
    seen, forms = set(), []
    for f in rep['forms']:
        if f in seen: continue
        seen.add(f); forms.append(f)
    rep['blank_forms'] = forms
    rep['complete'] = True
    rep['mode'] = 'blank'
    rep['ahj'] = ahj
    rep['path'] = out_path
    rep['forms_required'] = [f for f, _ in required_forms(ahj, systems)[0]]
    _finalize(out_path, rep, draft=False, mode='blank')
    LAST_REPORT = rep
    return rep


def build_packet(client, ahj, system, attachment_paths, out_path, underlayment=None, product=None, contractor=None,
                 fetch_pa=True, system2=None, strict=None, product_confirmed=None):
    """Build one permit packet. Same call shape as before.

    strict (default: env PERMIT_STRICT, else ON): raise PacketIncomplete when any required
    form or value is missing, so no caller can ever deliver a silently incomplete packet.
    The PDF is still written, clearly labeled as a prebuilt draft, for staff to inspect;
    the report is on the exception (.report) and in LAST_REPORT."""
    if strict is None:
        strict = os.environ.get('PERMIT_STRICT', '1') != '0'
    rep = build_packet_report(client, ahj, system, attachment_paths, out_path, underlayment, product,
                              contractor=contractor, fetch_pa=fetch_pa, system2=system2,
                              product_confirmed=product_confirmed)
    if strict and not rep['complete']:
        raise PacketIncomplete(rep)
    return out_path
