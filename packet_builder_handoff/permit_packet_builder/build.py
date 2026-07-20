# -*- coding: utf-8 -*-
"""SeaBreeze Permit Packet Builder - engine.
Assembles a single pre-filled permit packet PDF for a client/AHJ/system."""
import os, io, re, subprocess, tempfile
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor, white
from reportlab.pdfbase.pdfmetrics import stringWidth

# Locate the PBC form library portably: env override -> sibling of this folder
# (roof measurements\SeaBreeze_Permit_Library) -> original absolute path.
_HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.environ.get('SEABREEZE_LIB') or os.path.join(os.path.dirname(_HERE), 'SeaBreeze_Permit_Library')
if not os.path.isdir(LIB):
    LIB = r'C:\Users\DBivi\roof measurements\SeaBreeze_Permit_Library'
MB   = os.path.join(LIB,'Palm_Beach_County','PBC Municipalities')
PBCPK= os.path.join(LIB,'Palm_Beach_County','01_Permit_Application_Packet')
PA   = os.path.join(LIB,'Product_Approvals_Library')
SPEC = os.path.join(LIB,'_System_Spec_Sheets')
# Broward County (HVHZ) — uses SeaBreeze's pre-filled templates
BLIB = os.path.join(LIB,'Broward_County')
BMB  = os.path.join(BLIB,'Broward Municipalities')
BPK  = os.path.join(BLIB,'_Prefilled')
BPA  = os.path.join(BLIB,'Product_Approvals_Library')
BSPEC= os.path.join(BLIB,'_System_Templates')
_PA_SUB = {'Shingle':'Shingle','Tile':'Tile','Metal':'Metal_Roof','Flat':'Flat_Roof'}
NAVY=HexColor('#1a3a5c'); BLUE=HexColor('#4a90b8'); INK=HexColor('#10357a'); RED=HexColor('#c00000')

CONST_UNI={'Contractor-cert-holder':'Jacintho Carreiro','License-number':'CCC1328689','company-name':'SEABREEZE ROOFING & SHEET METAL INC',
 'contact-person':'Sarah C.','contractor-address':'2600 High Ridge Rd','company-city':'Boynton Beach','company state':'FL','company-zip':'33426',
 'company-phone':'561-292-3457','company-email':'permits@seabreezeroof.com','contractor-printed-name':'Jacintho Carreiro','contractor-notary-county':'Palm Beach'}
CONST_UNI_B={'Trade':'Roofing','Owner Builder or Contractor':'Contractor/Qualifier','Kind of Permit (Select One)':'Primary Permit',
 'USE ( Select One)':'1 & 2 Family','Type of Work':'New','bonding-company-not-applicable':'Yes','design-professional-not-applicable':'Yes','mortgage-lender-not-applicable':'Yes'}

# Universal SeaBreeze contractor fill — applied to EVERY fillable library form; fill_form matches these
# by field name (exact first, then prefix). Field names harvested across all 161 city app-packet forms.
SB_CONTRACTOR={
 'Company Name':'SEABREEZE ROOFING & SHEET METAL INC','company-name':'SEABREEZE ROOFING & SHEET METAL INC',
 'DBA COMPANY NAME':'SEABREEZE ROOFING & SHEET METAL INC','Company of designee':'SEABREEZE ROOFING & SHEET METAL INC',
 'Contractors Name':'SeaBreeze Roofing & Sheet Metal, Inc.','Contractor Qualifying Agent Name':'Jacintho Carreiro',
 'Contractors address':'2600 High Ridge Rd, Boynton Beach, FL 33426','contractor-address':'2600 High Ridge Rd',
 'CONTRACTOR Name & Address':'SeaBreeze Roofing & Sheet Metal, Inc., 2600 High Ridge Rd, Boynton Beach, FL 33426',
 '4 a CONTRACTOR Name  Address':'SeaBreeze Roofing & Sheet Metal, Inc., 2600 High Ridge Rd, Boynton Beach, FL 33426',
 'company-suite':'','company-city':'Boynton Beach','company state':'FL','company-zip':'33426',
 'company-phone':'561-292-3457','Contractor Phone number':'561-292-3457','company-email':'permits@seabreezeroof.com','company-fax':'',
 'Contractor-cert-holder':'Jacintho Carreiro','CONTRACTOR CERT HOLDER':'Jacintho Carreiro','contractor-printed-name':'Jacintho Carreiro',
 'NAME OF QUALIFIER':'Jacintho Carreiro','Qualifier Name':'Jacintho Carreiro','Qualifiers Name':'Jacintho Carreiro',
 'Qualifier print name':'Jacintho Carreiro','Qualifier Name Printed':'Jacintho Carreiro','Qualifier or OwnerBuilder Name Print':'Jacintho Carreiro',
 'License':'CCC1328689','License-number':'CCC1328689','License Number':'CCC1328689','CONTRACTOR LICENSE':'CCC1328689',
 'bonding-company-not-applicable':'Yes','contact-person':'Sarah C.','contractor-notary-county':'Palm Beach',
 # additional field-name variants harvested from city NOC / affidavit / registration forms
 "CONTRACTOR'S NAME":'SeaBreeze Roofing & Sheet Metal, Inc.','Name of Firm':'SEABREEZE ROOFING & SHEET METAL INC',
 'Firm Mailing Address':'2600 High Ridge Rd, Boynton Beach, FL 33426','QUALIFIER NAME':'Jacintho Carreiro','Name of Qualifier':'Jacintho Carreiro',
 '4 CONTRACTOR - Name  Address':'SeaBreeze Roofing & Sheet Metal, Inc., 2600 High Ridge Rd, Boynton Beach, FL 33426',
 '4 CONTRACTORS NAME ADDRESS AND PHONE NUMBER 1':'SeaBreeze Roofing & Sheet Metal, Inc., 2600 High Ridge Rd, Boynton Beach, FL 33426  561-292-3457',
 '4  a CONTRACTORS NAME':'SeaBreeze Roofing & Sheet Metal, Inc.','Contractor Qualifying Agent Name':'Jacintho Carreiro',
}
_SB_ADDR_KEYS=['Job Address','Site Address','SITE ADDRESS','PROJECT ADDRESS','Property address','PROPERTY ADDRESS','Property Address','Project Address','Permit Address']
_SB_OWNER_KEYS=['Property Owner','Owners Name','OwnersName','Owner Name','OwnerName','Owner print name','Name of Owner','Printed_Name_of_Property_Owner','Owner print']

# --- Multi-tenant contractor support -----------------------------------------
# _C / _contractor_overrides allow any caller to pass a contractor dict that
# overrides the hardcoded SeaBreeze defaults throughout the packet engine.
# Fallback = SeaBreeze constants, so all existing callers remain unchanged.
_SB = {
    'name':'SEABREEZE ROOFING & SHEET METAL INC',
    'name_title':'SeaBreeze Roofing & Sheet Metal, Inc.',
    'name_short':'Seabreeze Roofing',
    'display':'SEABREEZE ROOFING & SHEET METAL, INC.',
    'license':'CCC1328689','license_dashes':'CCC-1328689',
    'qualifier':'Jacintho Carreiro',
    'address':'2600 High Ridge Rd','address_long':'2600 High Ridge Road',
    'city':'Boynton Beach','state':'FL','zip':'33426',
    'phone':'561-292-3457','email':'permits@seabreezeroof.com',
    'contact_person':'Sarah C.','notary_county':'Palm Beach',
}
def _C(contractor, key):
    """Semantic contractor field accessor with SeaBreeze fallback."""
    if not contractor:
        return _SB.get(key,'')
    c=contractor
    if key in ('name','display'):
        return (c.get('company_name') or _SB['name']).upper()
    if key=='name_title':
        return c.get('company_name') or _SB['name_title']
    if key=='name_short':
        return c.get('company_name') or _SB['name_short']
    if key=='license':
        return c.get('license_number') or _SB['license']
    if key=='license_dashes':
        l=c.get('license_number') or _SB['license']
        return l[:3]+'-'+l[3:] if '-' not in l and len(l)>=10 else l
    if key=='qualifier':
        return c.get('qualifier_name') or _SB['qualifier']
    if key=='address':
        return c.get('address') or _SB['address']
    if key=='address_long':
        return c.get('address') or _SB['address_long']
    if key=='city':
        return c.get('city') or _SB['city']
    if key=='state':
        return c.get('state') or _SB['state']
    if key=='zip':
        return c.get('zip') or _SB['zip']
    if key=='phone':
        return c.get('phone') or _SB['phone']
    if key=='email':
        return c.get('email') or _SB['email']
    if key=='contact_person':
        return c.get('contact_person') or _SB['contact_person']
    if key=='notary_county':
        return c.get('notary_county') or c.get('county') or _SB['notary_county']
    return _SB.get(key,'')

def _contractor_overrides(contractor):
    """Build a dict of SB_CONTRACTOR-keyed overrides from a contractor profile."""
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
                  '4 CONTRACTOR - Name  Address','Contractors address','Firm Mailing Address']:
            ov[k]=addr_full if addr_full else nm
        ov['4 CONTRACTORS NAME ADDRESS AND PHONE NUMBER 1']=addr_phone
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

def seabreeze_fill_T(client, ahj, contractor=None):
    """Full contractor + this-job fill dict for any fillable form (matched by field name)."""
    city=client.get('city','') or ahj.replace('_',' ')
    addr=', '.join([s for s in [client.get('address',''), city, ('FL '+client.get('zip','')) if client.get('zip','') else ''] if s])
    T=dict(SB_CONTRACTOR)
    if contractor: T.update(_contractor_overrides(contractor))
    for k in _SB_ADDR_KEYS: T[k]=addr
    for k in _SB_OWNER_KEYS: T[k]=client.get('owner','')
    return T

SYSTEMS={
 'Shingle':{'desc':'REROOF - ASPHALT SHINGLE','mfr':'Owens Corning','prodfl':'FL10674','prod':'TruDefinition Duration architectural shingle',
   'ul':'Polystick IR-XE','ulfl':'FL5259','box':'Asphalt Shingles','swb_box':'1 The entire roof deck','swb':'Polystick IR-XE',
   'approvals':['Shingle/Shingle_OwensCorning_TruDef_Duration_FL10674-R20.pdf']},
 'Tile':{'desc':'REROOF - CONCRETE TILE','mfr':'Westlake Royal','prodfl':'FL7849','prod':'Saxony 900 flat concrete tile',
   'ul':'Polystick TU Plus','ulfl':'FL5259','box':'Mortar/Foam Set tile','swb_box':'2 Clay and concrete tile','swb':'Polystick TU Plus',
   'adh':'ICP / Polyset AH-160','adhfl':'FL6332',
   'approvals':['Tile/Tile_Westlake_Saxony900_FL7849-ER412.pdf','Tile/Tile_Adhesive_Polyset_AH-160_FL6332-R13.pdf','Tile/Tile_Underlayment_Polyglass_TU-Plus_FL5259-R50.pdf']},
 'Metal':{'desc':'REROOF - STANDING SEAM METAL','mfr':'Dynamic Metals','prodfl':'FL41724.08','prod':'DM Class 1500, 1.5" Mech Seam, 16" wide',
   'ul':'Polystick MTS','ulfl':'FL5259','box':'Metal Panel/shingle','swb_box':'1 The entire roof deck','swb':'Polystick MTS',
   'approvals':['Metal_Roof/Metal_Dynamic_Class1500_26ga_FL41724-R3_Submittal.pdf','Metal_Roof/Metal_Underlayment_2ply_MTS_FL5259-R50.pdf']},
 'Flat':{'desc':'REROOF - FLAT / MOD-BIT','mfr':'Polyglass','prodfl':'FL1654','prod':'SA 2-ply self-adhered modified bitumen',
   'ul':'Polystick MTS Plus','ulfl':'FL1654','box':'Flat roof','swb_box':'1 Roof slopes 212','swb':'Polystick MTS Plus',
   'approvals':['Flat_Roof/Flat_Polyglass_SA_ModBit_FL1654-R44_NonHVHZ_AE.pdf']},
}

# Underlayment options per system: choice -> (label, FL#, product-approval relative path).
UNDERLAYMENTS={
 'Shingle':{'single':('Polystick IR-XE','FL5259','Shingle/Shingle_Underlayment_Polyglass_IR-XE_FL5259-R50_NonHVHZ.pdf')},
 'Tile':{'single':('Polystick TU Plus (1-ply)','FL5259','Tile/Tile_Underlayment_Polyglass_TU-Plus_FL5259-R50.pdf'),
         '2ply':('Polystick TU-Plus + MTS (2-ply)','FL5259','Tile/Tile_Underlayment_2ply_TU-Plus+MTS_FL5259-R50.pdf')},
 'Metal':{'single':('Polystick MTS','FL1654','Metal_Roof/Metal_Underlayment_Polyglass_MTS_FL1654-R41.pdf'),
          '2ply':('Polystick MTS (2-ply)','FL5259','Metal_Roof/Metal_Underlayment_2ply_MTS_FL5259-R50.pdf')},
 'Flat':{'single':('Polystick MTS Plus','FL1654','Flat_Roof/Flat_Polyglass_SA_ModBit_FL1654-R44_NonHVHZ_II.pdf')},
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
 'Tile':{
   'westlake':('Westlake Royal Saxony 900','Westlake Royal','Saxony 900 flat concrete tile','FL7849','Tile/Tile_Westlake_Saxony900_FL7849-ER412.pdf'),
   'eagle':   ('Eagle Concrete Tile','Eagle','Eagle flat concrete tile','FL7473','Tile/Tile_Eagle_ConcreteTile_FL7473-R11_HVHZ.pdf'),
 },
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

def list_ahjs():
    out=[]
    for base in (MB, BMB):
        if os.path.isdir(base):
            for a in os.listdir(base):
                if os.path.isdir(os.path.join(base,a)): out.append(a)
    return sorted(set(out))

def _is_broward(ahj):
    return bool(ahj) and os.path.isdir(os.path.join(BMB, ahj))

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

def fill_form(src, T, B, topnote=None):
    """Overlay-fill + flatten an AcroForm PDF. Returns list of pypdf pages."""
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
                elif ft=='/Btn' and name in B and str(B[name]).strip() and (on is None or str(B[name])==on):
                    # on is None when the widget has no /AP on-state (some flattened-ish forms,
                    # e.g. the PBC Roof Spec Sheet checkboxes); still mark it when explicitly requested.
                    fs=min(11.0,rh+1); c.setFillColor(INK); c.setFont('Helvetica-Bold',fs); c.drawCentredString((x0+x1)/2,y0+(rh-fs*0.7)/2,'X')
            except Exception: pass
        if pi==0 and topnote:
            c.setFillColor(RED); c.setFont('Helvetica-Bold',7.4); c.drawString(36,H-12,topnote)
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

def _pcn_digits(pcn):
    return ''.join(ch for ch in (pcn or '') if ch.isdigit())[:17]

def _permit_value(client):
    """Declared permit valuation = primary estimate price minus 10% (SeaBreeze policy).
    `value` comes from the AccuLynx primary estimate worksheet total."""
    import re as _re
    n=_re.sub(r'[^0-9.]','',str(client.get('value','') or ''))
    if not n: return ''
    try: return '{:,.0f}'.format(round(float(n)*0.9))
    except Exception: return str(client.get('value','') or '')

def cover(client, ahj, system, ul_label=None, prod_label=None, warn=None, contractor=None):
    buf=io.BytesIO(); c=canvas.Canvas(buf,pagesize=letter); W,H=letter
    c.setFillColor(NAVY); c.setFont('Helvetica-Bold',16); c.drawCentredString(W/2,H-70,_C(contractor,'display'))
    c.setFillColor(BLUE); c.setFont('Helvetica',9); c.drawCentredString(W/2,H-84,'Certified Roofing Contractor  -  State of Florida License '+_C(contractor,'license_dashes'))
    c.setStrokeColor(NAVY); c.setLineWidth(2); c.line(54,H-96,W-54,H-96)
    c.setFillColor(NAVY); c.setFont('Helvetica-Bold',22); c.drawCentredString(W/2,H-140,'ROOF PERMIT PACKAGE')
    c.setFillColor(BLUE); c.setFont('Helvetica-Bold',12); c.drawCentredString(W/2,H-160,'%s  -  %s System'%(ahj.replace('_',' '), system))
    juris=('%s, Broward County (FBC 8th Ed. 2023, HVHZ)'%ahj.replace('_',' ')) if _is_broward(ahj) else ('%s, Palm Beach County (FBC 8th Ed. 2023, Non-HVHZ)'%ahj.replace('_',' '))
    rows=[('Owner',client.get('owner','')),('Property Address','%s, %s, FL %s'%(client.get('address',''),client.get('city','') or ahj.replace('_',' '),client.get('zip',''))),
      ('PCN',client.get('pcn','')),('Legal',client.get('legal','')),('Jurisdiction',juris),
      ('Roof System',(prod_label or SYSTEMS[system]['prod'])+' / '+(ul_label or SYSTEMS[system]['ul'])),('Scope',SYSTEMS[system]['desc']),
      ('Permit Value (est. -10%)',('$'+_permit_value(client)) if client.get('value') else ''),('Contractor','%s - %s (%s)'%(_C(contractor,'name_title'),_C(contractor,'license_dashes'),_C(contractor,'qualifier')))]
    y=H-200
    for k,v in rows:
        c.setFillColor(NAVY); c.setFont('Helvetica-Bold',10); c.drawString(70,y,k+':')
        c.setFillColor(HexColor('#222')); c.setFont('Helvetica',10); c.drawString(190,y,str(v)[:80]); y-=22
    if warn:
        y-=4; c.setFillColor(RED); c.setFont('Helvetica-Bold',8.6)
        for seg in [warn[j:j+96] for j in range(0,len(warn),96)]:
            c.drawString(70,y,seg); y-=11
    c.setFillColor(HexColor('#555')); c.setFont('Helvetica',8)
    c.drawString(70,120,'This packet pre-fills contractor data, the roof system product approvals, and the client/property')
    c.drawString(70,108,'information above. Sign & notarize where indicated, record the Notice of Commencement, and attach the')
    c.drawString(70,96,'PE-stamped wind uplift calculation + roof measurement report before submitting to the building department.')
    c.setStrokeColor(BLUE); c.setLineWidth(0.75); c.line(54,70,W-54,70)
    c.setFillColor(NAVY); c.setFont('Helvetica',8); c.drawString(54,58,'%s  |  %s  |  %s  |  %s'%(_C(contractor,'name_title'),_C(contractor,'license_dashes'),_C(contractor,'phone'),_C(contractor,'email')))
    c.showPage(); c.save(); buf.seek(0)
    return PdfReader(buf).pages

def _add_pdf(w, path):
    try:
        rr=PdfReader(path)
        if rr.is_encrypted: rr.decrypt('')
        for p in rr.pages: w.add_page(p)
        return True
    except Exception:
        return False

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
]
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
_BROWARD_STAMP_FORMS={
 'BC_01_Uniform_Building_Permit_Application_PREFILLED.pdf': _BC01_STAMPS,
 'BC_02_Notice_of_Commencement_PREFILLED.pdf': _BC02_STAMPS,
 'BC_03_HVHZ_Uniform_Roofing_Application_PREFILLED.pdf': _BC03_STAMPS,
 'BC_06_FDEP_Asbestos_Notification_PREFILLED.pdf': _BC06_STAMPS,
}

def _broward_values(client, ahj, system=None):
    addr=client.get('address',''); city=client.get('city','') or ahj.replace('_',' ')
    zip_c=client.get('zip','')
    addrfull=', '.join(s for s in [addr, city, ('FL '+zip_c) if zip_c else ''] if s)
    legal=client.get('legal',''); pcn=client.get('pcn','')
    legaddr=' -- '.join([s for s in (addr, legal) if s])
    _is=lambda s: 'X' if system==s else ''
    return {'addr':addr,'city':city,'st':'FL','zip':zip_c,'addrfull':addrfull,
            'owner':client.get('owner',''),'pcn':pcn,'legal':legal,'legaddr':legaddr,
            'value':('$'+_permit_value(client)) if client.get('value') else '',
            'cat_flat_x':_is('Flat'),'cat_shingle_x':_is('Shingle'),
            'cat_metal_x':_is('Metal'),'cat_tile_mech_x':_is('Tile')}

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
_PBC_STAMP_FORMS={
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
def _pbc_supp_values(client, ahj):
    city=client.get('city','') or ahj.replace('_',' ')
    addr=', '.join([s for s in [client.get('address',''), city, ('FL '+client.get('zip','')) if client.get('zip','') else ''] if s])
    return {'contractor':'SEABREEZE ROOFING & SHEET METAL INC','company':'SEABREEZE ROOFING & SHEET METAL INC',
            'contractor_addr':'2600 High Ridge Rd, Boynton Beach, FL 33426',
            'owner':client.get('owner',''),'addr':addr,
            'qualifier':'Jacintho Carreiro','license':'CCC1328689'}

# --- "Roof Construction Specifications Sheet" fill (e.g. RPB_Roof_Spec_Sheet) ---
# The PBC city "Roof Construction Specifications Sheet" is a real fillable AcroForm
# (ROOF HEIGHT / RIDGE VENTING / DECKING / SECONDARY WATER BARRIER / UNDERLAYMENT /
# INSULATION / DECK FASTENER & SPACING / CAP SHEET / ROOF COVERING / DRIP EDGE /
# ROOF SLOPE / TOTAL ROOF AREA + MARK-ONE / ROOF CATEGORY / ROOF TYPE checkboxes).
# Previously it fell through the city-supplemental loop with only the contractor fill,
# so every non-Boca packet emitted it BLANK. We now fill the system spec + job roof data.
#
# The per-system component values below are SeaBreeze's OWN documented standards, lifted
# verbatim from SeaBreeze's pre-filled _System_Templates / _System_Spec_Sheets (NOT invented).
# RIDGE VENTING is intentionally blank in SeaBreeze's templates (job-specific) so we leave it
# blank. Roof Covering / Underlayment are derived from the live system dict so the chosen
# product / underlayment flow through.
# decking / ridge / deck_fastener values calibrated to SeaBreeze's APPROVED permit R-25117 (Fawcett,
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
 'Flat':{'decking':'19/32" CDX Plywood','swb':'Polystick MTS Plus','insulation':'N/A',
         'deck_fastener':'8d Ring Shank Nails, 6" o.c.','cap':'Polyflex SA P FR cap','drip':'26ga metal drip edge','ridge':'N/A',
         'covering':'Polyglass SA 2-ply SA mod-bit FL1654','category':'membrane','markone':'flat'},
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
    2-4 -> low slope, >4 -> sloped. Unknown slope (non-Flat) -> SeaBreeze template default."""
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
    # Roof covering: SeaBreeze's compact template string for the default product (fits the narrow
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
    check('reroof')   # SeaBreeze scope is always a re-roof (system desc == 'REROOF - ...')
    return T, B

def _stamp_pdf(w, path, stamps, values):
    """Merge a reportlab text overlay onto a flattened PDF and add to writer.
    Falls back to a plain add if anything goes wrong."""
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

def _build_broward(client, ahj, system, attachment_paths, out_path, underlayment=None, product=None, contractor=None):
    """Broward (HVHZ) packet: cover + contractor pre-filled BC forms + system template + approvals.
    NOTE: the BSPEC Section-D template is pre-filled with the default manufacturer; the cover and
    the (all-inclusive) product-approval set reflect the chosen product, but a product-specific
    pre-filled Section-D template must be added to fully switch manufacturers on the HVHZ form."""
    sysd=_apply_product(_apply_underlayment(system, underlayment), system, product); w=PdfWriter()
    pk=sysd.get('product_key')
    _hvhz_warn=None
    if pk and pk!=DEFAULT_PRODUCT.get(system) and not os.path.isdir(os.path.join(BSPEC, system, pk)):
        _hvhz_warn=('ACTION REQUIRED: the HVHZ Section-D %s form in this packet is pre-filled for the standard product. '
                    'Update it to %s (%s) before submitting to the building department.'%(system, sysd.get('mfr',''), sysd.get('prodfl','')))
    for p in cover(client, ahj, system, sysd['ul'], sysd.get('prod'), _hvhz_warn, contractor=contractor): w.add_page(p)
    muni=os.path.join(BMB, ahj, '_Prefilled')
    forms=['BC_01_Uniform_Building_Permit_Application_PREFILLED.pdf',
           'BC_02_Notice_of_Commencement_PREFILLED.pdf',
           'BC_03_HVHZ_Uniform_Roofing_Application_PREFILLED.pdf',
           'BC_06_FDEP_Asbestos_Notification_PREFILLED.pdf']
    bvals=_broward_values(client, ahj, system)
    for fn in forms:
        p=os.path.join(muni, fn)
        if not os.path.exists(p): p=os.path.join(BPK, fn)  # fall back to county-level prefilled
        if not os.path.exists(p): continue
        stamps=_BROWARD_STAMP_FORMS.get(fn)
        if stamps: _stamp_pdf(w, p, stamps, bvals)   # overlay folio/legal/owner onto flattened BC form
        else: _add_pdf(w, p)
    # system templates (HVHZ roofing system + Section D).
    # Prefer a product-specific subfolder (e.g. _System_Templates/Tile/eagle) when present so the
    # pre-filled Section-D form matches the chosen manufacturer; otherwise use the default template.
    st=os.path.join(BSPEC, system)
    pk=sysd.get('product_key')
    if pk and os.path.isdir(os.path.join(st, pk)): st=os.path.join(st, pk)
    if os.path.isdir(st):
        if system=='Flat' and st==os.path.join(BSPEC,'Flat'):
            # Select AB template + one Section C matching the deck type (prevents all 6 variants appending)
            deck=sysd.get('deck',_FLAT_SECTION_C_DEFAULT)
            sec_c=_FLAT_SECTION_C.get(deck,_FLAT_SECTION_C[_FLAT_SECTION_C_DEFAULT])
            for fn in ['HVHZ_RoofingSystem_AB_Flat_TEMPLATE.pdf', sec_c]:
                fp=os.path.join(st,fn)
                if os.path.exists(fp): _add_pdf(w,fp)
        else:
            for fn in sorted(os.listdir(st)):
                if fn.lower().endswith('.pdf'): _add_pdf(w, os.path.join(st, fn))
    # product approvals for the system
    sub=os.path.join(BPA, _PA_SUB.get(system,''))
    if os.path.isdir(sub):
        for fn in sorted(os.listdir(sub)):
            if fn.lower().endswith('.pdf'): _add_pdf(w, os.path.join(sub, fn))
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

def build_packet(client, ahj, system, attachment_paths, out_path, underlayment=None, product=None, contractor=None, fetch_pa=True):
    if _is_broward(ahj):
        return _build_broward(client, ahj, system, attachment_paths, out_path, underlayment, product, contractor=contractor)
    sysd=_apply_product(_apply_underlayment(system, underlayment), system, product); w=PdfWriter()
    def add(pages):
        for p in pages: w.add_page(p)
    # 1) cover
    add(cover(client,ahj,system,sysd['ul'],sysd.get('prod'),contractor=contractor))
    # 2) Universal Building Permit Application (in every AHJ folder)
    uni=os.path.join(MB,ahj,'01_Permit_Application_Packet','PBC_01_Building_Permit_Application.pdf')
    if not os.path.exists(uni): uni=os.path.join(PBCPK,'PBC_01_Building_Permit_Application.pdf')
    if os.path.exists(uni):
        T=dict(CONST_UNI)
        if contractor: T.update(_contractor_overrides(contractor))
        T.update({'Property_Owner':client.get('owner',''),'Property_owner_address':client.get('address',''),
          'Property_owner_city':client.get('city','') or ahj.replace('_',' '),'Property_owner_state':'FL','Property_owner_zip':client.get('zip',''),
          'Property-owner-phone':client.get('phone',''),'Project-address':client.get('address',''),'project-city':client.get('city','') or ahj.replace('_',' '),
          'Legal-description':client.get('legal',''),'permit-value':_permit_value(client),'owner-printed-name':client.get('owner',''),
          'further_work_description':sysd['desc']})
        for i,d in enumerate(_pcn_digits(client.get('pcn','')),start=1): T['PCN-%d'%i]=d
        add(fill_form(uni,T,CONST_UNI_B,'SeaBreeze pre-filled - sign & notarize; record NOC before first inspection'))
    # 3) Notice of Commencement
    noc=os.path.join(MB,ahj,'01_Permit_Application_Packet','PBC_02_Notice_of_Commencement.pdf')
    if not os.path.exists(noc): noc=os.path.join(PBCPK,'PBC_02_Notice_of_Commencement.pdf')
    if os.path.exists(noc):
        _ctr_city=_C(contractor,'city'); _ctr_st=_C(contractor,'state'); _ctr_zip=_C(contractor,'zip')
        T={'Name':_C(contractor,'name_short'),'Address 1':_C(contractor,'address_long'),
          'Address 2':'%s, %s %s'%(_ctr_city,_ctr_st,_ctr_zip),'State of':'Florida','County of':'Palm Beach',
          'PROPERTY CONTROL NUMBER PCNTAX FOLIO NO':client.get('pcn',''),'2 GENERAL DESCRIPTION OF IMPROVEMENT':'Reroof',
          '1 DESCRIPTION OF PROPERTY Legal description':(client.get('address','')+((' -- '+client.get('legal','')) if client.get('legal','') else '')),
          'a Name and address':'%s, %s, %s FL %s'%(client.get('owner',''),client.get('address',''),client.get('city','') or ahj.replace('_',' '),client.get('zip','')),
          'b Interest in property':'Owner',
          '4 a CONTRACTOR Name  Address':'%s, %s, %s, %s FL %s'%(_C(contractor,'name_short'),_C(contractor,'address_long'),_ctr_city,_ctr_st,_ctr_zip),
          'b Phone number':_C(contractor,'phone')}
        add(fill_form(noc,T,{},'SeaBreeze pre-filled - owner to sign & record with PBC Clerk'))
    # 4) Boca-specific roofing forms (full system fill); other AHJs -> system spec sheet
    if ahj=='Boca_Raton':
        sup=os.path.join(MB,ahj,'01_Permit_Application_Packet','Boca_06_Supplemental_Roof.pdf')
        mit=os.path.join(MB,ahj,'01_Permit_Application_Packet','Boca_02_ReRoofing_Mitigation_Package.pdf')
        if os.path.exists(sup):
            T={'Contractors Name':_C(contractor,'name_short'),'License':_C(contractor,'license'),'OwnersName':client.get('owner',''),'Job Address':client.get('address',''),
              'Existing Roofing Type Matl':client.get('existing','Asphalt Shingle'),'DeckType':'CDX','Roof Slope':client.get('slope',''),
              'Mean Roof Height':client.get('mrh','15')+"'",'Total Roof Area This Perm':client.get('area',''),'ROOF COVERING MANUFACTURER':sysd['mfr'],
              'Roof System Manufacturer':sysd['mfr'],
              'Product Approval':sysd['prodfl'],'Product':sysd['prod'],'Base Sheet':sysd['ul'],'Product Approval_2':sysd['ulfl'],'Product Approval_3':sysd['ulfl']}
            # Flat system: Section D needs Cap Sheet + Top Ply (2-ply SA: base=MTS Plus, cap=Polyflex SA P FR)
            _flat_cap=SPEC_SHEET.get('Flat',{}).get('cap','') if system=='Flat' else ''
            if _flat_cap: T['Cap Sheet']=_flat_cap; T['Top Ply']=_flat_cap
            B={'Use of Building':'1 or 2 Family','Re-Roofing/ Re-Covering -Attach Mitigation Package':'On',sysd['box']:'On','Minimum 3':'On'}
            if client.get('exposure'): B['Exposure Category']=client['exposure']
            if system=='Tile': T['ManufacturerProduct']=sysd['adh']; T['Product Approval_6']=sysd['adhfl']; T['Tile Profile']='Flat'
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
            add(fill_form(sup,T,B,'SeaBreeze SYSTEM pre-filled - %s'%system))
        if os.path.exists(mit):
            T={'Property address':'%s, %s, FL %s'%(client.get('address',''),client.get('city','') or 'Boca Raton',client.get('zip','')),
              'Specify Secondary Water Barrier':sysd['swb'],'Product approval number':sysd['ulfl'],'Qualifier print name':_C(contractor,'qualifier'),'QualifierOwner Builder print':_C(contractor,'qualifier'),
              'Owner print name':client.get('owner',''),'County of':'Palm Beach','County of_2':'Palm Beach'}
            add(fill_form(mit,T,{sysd['swb_box']:'On'},'SeaBreeze SYSTEM pre-filled - %s'%system))
    else:
        sp=os.path.join(SPEC,'SeaBreeze_System_Spec_%s.pdf'%system)
        if os.path.exists(sp): add(PdfReader(sp).pages)
    # 4b) City-specific supplemental forms — the SeaBreeze Permit Library IS the database: pull every
    #     form in the AHJ's application-packet folder that we didn't already fill above (re-roof
    #     affidavit, asbestos notification, wind-load chart, owner-builder, hot-process, etc.).
    #     If the raw form is fillable, fill the JOB ADDRESS + SeaBreeze contractor info; otherwise fall
    #     back to the pre-filled copy. Skip the city's duplicate app/NOC (covered by PBC_01/02).
    T_supp=seabreeze_fill_T(client, ahj, contractor=contractor)   # universal contractor + this-job fill
    apk=os.path.join(MB,ahj,'01_Permit_Application_Packet')
    pre=os.path.join(MB,ahj,'_Prefilled')
    if os.path.isdir(apk):
        for fn in sorted(os.listdir(apk)):
            low=fn.lower()
            if not low.endswith('.pdf'): continue
            stem=fn[:-4]
            if stem.startswith('PBC_01') or stem.startswith('PBC_02'): continue
            # Boca_01 is the city's master reference PDF (redundant with 02+06); Boca_02/06 filled in step 4
            if stem in ('Boca_01_Boca_Permit_Packet','Boca_02_ReRoofing_Mitigation_Package','Boca_06_Supplemental_Roof'): continue
            if 'notice_of_commencement' in low or 'building_permit_application' in low: continue
            raw=os.path.join(apk,fn)
            _flds=None
            try: _flds=PdfReader(raw).get_fields()
            except Exception: pass
            if _flds:                                   # fillable -> stamp job address + contractor, flatten
                _sf=_spec_sheet_fill(raw, client, ahj, system, sysd)
                if _sf:                                  # Roof Construction Specifications Sheet -> fill system spec + roof data
                    _Ts,_Bs=_sf; _T=dict(T_supp); _T.update(_Ts)
                    add(fill_form(raw, _T, _Bs, 'SeaBreeze %s system spec pre-filled - verify roof height/slope/area & sign per job'%system))
                else:
                    add(fill_form(raw, T_supp, {}, None))
            elif fn in _PBC_STAMP_FORMS:                 # flattened but coords known -> overlay-stamp
                _stamp_pdf(w, raw, _PBC_STAMP_FORMS[fn], _pbc_supp_values(client, ahj))
            else:                                       # flattened, no coords -> use pre-filled copy as-is
                pf=os.path.join(pre,stem+'_PREFILLED.pdf')
                _add_pdf(w, pf if os.path.exists(pf) else raw)
    # 5) product approvals for the system
    for rel in sysd['approvals']:
        p=os.path.join(PA,rel)
        if os.path.exists(p):
            try:
                rr=PdfReader(p)
                if rr.is_encrypted: rr.decrypt('')
                add(rr.pages)
            except Exception: pass
    # 6) PBC Property Appraiser value page (required for mitigation threshold verification)
    if fetch_pa and client.get('pcn'):
        pa_pdf=_fetch_pbc_pa_pdf(client['pcn'])
        if pa_pdf: _add_pdf(w,pa_pdf)
    # 7) attached docs (RoofGraf, etc.)
    for ap in attachment_paths or []:
        try:
            rr=PdfReader(ap)
            if rr.is_encrypted: rr.decrypt('')
            add(rr.pages)
        except Exception: pass
    with open(out_path,'wb') as fh: w.write(fh)
    return out_path
