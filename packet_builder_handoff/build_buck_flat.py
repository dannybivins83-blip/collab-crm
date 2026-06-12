# -*- coding: utf-8 -*-
"""One-off: build the CORRECTED Flat / Hot-Mop BUR permit packet for
Lawrence Buck, 1963 NE 6th St, Deerfield Beach (R-26040).
The original packet on disk was a TILE packet (wrong system); the signed
estimate is a flat hot-mopped built-up roof on a wood deck.

System (authoritative, from the SeaBreeze library): Johns Manville GlasPly
hot-mop BUR over wood deck, NOA 25-0911.04 (max design pressure -90 psf).
Wind pressures + zone fastening are left for the PE's sealed calc; a
preliminary RAS-128 / ASCE 7-22 worksheet is included.
"""
import os, io
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.colors import HexColor

LIB = r"C:\Users\kjburnz\acculynx roofr reprot\packet_builder_handoff\SeaBreeze_Permit_Library"
BMB = os.path.join(LIB, "Broward_County", "Broward Municipalities")
DEER = os.path.join(BMB, "Deerfield_Beach", "_Prefilled")
TPL  = os.path.join(LIB, "Broward_County", "_System_Templates", "Flat",
                    "HVHZ_Section_C_Flat_HotMop_BUR_WoodDeck_TEMPLATE.pdf")
JMNOA= os.path.join(LIB, "Broward_County", "Product_Approvals_Library", "Flat_Roof",
                    "BUR_HotMop_JM_GlasPly_MiamiDade_NOA_25-0911.04.pdf")
OUT  = r"C:\Users\kjburnz\Downloads\Lawrence_Buck_Deerfield_Beach_Flat_HotMop_BUR_Permit_Packet.pdf"

NAVY=HexColor('#1a3a5c'); BLUE=HexColor('#4a90b8'); INK=HexColor('#10357a'); RED=HexColor('#c00000')

CLIENT = dict(owner="Lawrence Buck", address="1963 Northeast 6th Street",
              city="Deerfield Beach", zip="33441", phone="(561) 756-5945",
              legal="OCEAN VUE 3-34 B LOT 28,29 W1/2 BLK 7", pcn="", value="45,622",
              job="R-26040")

# ---- wind worksheet inputs (ASCE 7-22 / RAS-128, h=12 ft, Exp C) ----
V=170.0; Kh=0.85; Kzt=1.0; Kd=0.85; Ke=1.0
qh = 0.00256*Kh*Kzt*Kd*Ke*V*V          # velocity pressure, psf
MRH=12.0
a_perim = round(0.6*MRH,1)             # P2 perimeter width  = .6h
p3_len  = round(0.6*MRH,1)             # P3 corner length    = .6h
p3_wid  = round(0.2*MRH,1)             # P3 corner width     = .2h

# ---------- Broward BC_01 / BC_02 stamp maps (from build.py, measured) ----------
_BC01=[(0,96,644,'addr',8,305),(0,433,644,'city',8,110),(0,82,615,'pcn',8,200),
       (0,116,468,'legal',8,440),(0,106,433,'owner',8,290),(0,109,404,'addr',8,190),
       (0,328,404,'city',8,110),(0,468,404,'st',8,30),(0,521,404,'zip',8,40)]
_BC02=[(0,385,637,'pcn',8,175),(0,40,608,'legaddr',8,510),(0,176,554,'owner',8,250)]
def bvals():
    legaddr=' -- '.join([s for s in (CLIENT['address'],CLIENT['legal']) if s])
    return {'addr':CLIENT['address'],'city':CLIENT['city'],'st':'FL','zip':CLIENT['zip'],
            'owner':CLIENT['owner'],'pcn':CLIENT['pcn'],'legal':CLIENT['legal'],'legaddr':legaddr}

def _overlay(pg, draws):
    """draws: list of (x,y,text,size,color). Merge onto page."""
    W=float(pg.mediabox.width); H=float(pg.mediabox.height)
    buf=io.BytesIO(); c=canvas.Canvas(buf,pagesize=(W,H))
    for x,y,text,size,color in draws:
        if text is None or str(text).strip()=='' : continue
        c.setFillColor(color); c.setFont('Helvetica',size)
        c.drawString(x,y,str(text).encode('latin-1','replace').decode('latin-1'))
    c.showPage(); c.save(); buf.seek(0)
    try: pg.merge_page(PdfReader(buf).pages[0])
    except Exception: pass
    return pg

def add_pdf(w, path):
    rr=PdfReader(path)
    if rr.is_encrypted: rr.decrypt('')
    for p in rr.pages: w.add_page(p)

def stamp_bc(w, path, stamps):
    vals=bvals(); r=PdfReader(path)
    if r.is_encrypted: r.decrypt('')
    by={}
    for s in stamps: by.setdefault(s[0],[]).append(s)
    for pi,pg in enumerate(r.pages):
        draws=[]
        for _,x,y,key,fs,maxw in by.get(pi,[]):
            v=vals.get(key,'')
            if v: draws.append((x,y,v,fs,INK))
        if draws: _overlay(pg,draws)
        w.add_page(pg)

# ---------------- cover page ----------------
def cover(w):
    buf=io.BytesIO(); c=canvas.Canvas(buf,pagesize=letter); W,H=letter
    c.setFillColor(NAVY); c.setFont('Helvetica-Bold',16); c.drawCentredString(W/2,H-70,'SEABREEZE ROOFING & SHEET METAL, INC.')
    c.setFillColor(BLUE); c.setFont('Helvetica',9); c.drawCentredString(W/2,H-84,'Certified Roofing Contractor  -  State of Florida License CCC-1328689')
    c.setStrokeColor(NAVY); c.setLineWidth(2); c.line(54,H-96,W-54,H-96)
    c.setFillColor(NAVY); c.setFont('Helvetica-Bold',22); c.drawCentredString(W/2,H-140,'ROOF PERMIT PACKAGE')
    c.setFillColor(BLUE); c.setFont('Helvetica-Bold',12); c.drawCentredString(W/2,H-160,'Deerfield Beach, Broward County (FBC 8th Ed. 2023, HVHZ)  -  Flat / Hot-Mop BUR')
    rows=[('Owner',CLIENT['owner']),('Property Address','%s, %s, FL %s'%(CLIENT['address'],CLIENT['city'],CLIENT['zip'])),
      ('Job #',CLIENT['job']),('Legal',CLIENT['legal']),
      ('Roof System','Johns Manville hot-mopped BUR over wood deck (GlasPly IV / GlasKap Plus)'),
      ('Product Approval','Miami-Dade NOA 25-0911.04  (max design pressure -90 psf)'),
      ('Scope','REROOF - FLAT / BUILT-UP (hot mop): tear off, #75/GlasBase, 2-3 ply GlasPly IV, mineral cap'),
      ('Est. Value','$'+CLIENT['value']),
      ('Contractor','SeaBreeze Roofing & Sheet Metal, Inc. - CCC-1328689 (Jacintho Carreiro)')]
    y=H-200
    for k,v in rows:
        c.setFillColor(NAVY); c.setFont('Helvetica-Bold',10); c.drawString(60,y,k+':')
        c.setFillColor(HexColor('#222')); c.setFont('Helvetica',9); c.drawString(170,y,str(v)[:92]); y-=20
    c.setFillColor(RED); c.setFont('Helvetica-Bold',9)
    c.drawString(60,y-6,'NOTE: This packet replaces the earlier "Tile" packet, which was the wrong system for this job.')
    c.setFillColor(HexColor('#555')); c.setFont('Helvetica',8)
    c.drawString(60,118,'Roof system components are pre-filled per the approved JM NOA. The wind-uplift pressures and zone fastening on the')
    c.drawString(60,107,'HVHZ Section C form must be confirmed by the enclosed (preliminary) wind worksheet, then computed & SEALED by a')
    c.drawString(60,96,'Florida PE per RAS-128 / ASCE 7-22 before submittal. Sign & notarize where indicated; record the NOC.')
    c.setStrokeColor(BLUE); c.setLineWidth(0.75); c.line(54,70,W-54,70)
    c.setFillColor(NAVY); c.setFont('Helvetica',8); c.drawString(54,58,'SeaBreeze Roofing & Sheet Metal, Inc.  |  CCC-1328689  |  (561) 970-9627  |  dannyb@seabreezeroof.com')
    c.showPage(); c.save(); buf.seek(0)
    for p in PdfReader(buf).pages: w.add_page(p)

# ---------------- Section C template: stamp the per-job blanks ----------------
def section_c(w):
    r=PdfReader(TPL); pg=r.pages[0]
    # (x, y, text, size, color) -- coordinates measured from the template text layer
    D=[
      # geometry
      (250,558.9,'12',9,INK),                 # Roof Mean Height: __ ft
      (96,558.9,'1/4',9,INK),                 # Roof Slope: __ /12  (flat; verify per roof report)
      (128,540.1,'X',10,INK),                 # Parapet Walls: Yes
      # elevated pressure-zone widths (deterministic from MRH: a'=.6h, corner=.6h x .2h)
      (245,606.6,str(a_perim),9,INK),         # (P2) Width: __ ft
      (112,584.3,str(p3_len),9,INK),          # (P3) Length: __ ft
      (228,584.3,str(p3_wid),9,INK),          # (P3) Width: __ ft
      # NOA capacity
      (140,607.1,'-90 psf (NOA max; sys. -52.5 psf)',8,INK),  # NOA Design Pressure
      # unused components -> N/A (form requires marking unused as n/a)
      (200,428.8,'N/A',9,INK),                # Fire Barrier
      (200,398.2,'N/A',9,INK),                # Vapor Barrier
      (210,299.6,'N/A (no insulation)',9,INK),# Insulation Base Layer Size
      (200,267.9,'N/A',9,INK),                # Insulation Base Layer Fastener
      (210,237.3,'N/A',9,INK),                # Insulation Top Layer Size
      (200,204.9,'N/A',9,INK),                # Insulation Top Layer Fastener
      (230,171.2,'N/A',9,INK),                # Fasteners per Insulation Board
      (95,496.7,'N/A',9,INK),                 # LWIC Manufacturer
      (210,456.9,'N/A - tear off to deck',9,INK),  # If roof recovery, existing system
      # nailers / edge metal
      (470,325.7,'2x PT, per FBC',8,INK),     # Wood Nailer Type and Size
      (455,287.7,'per FBC 1507',8,INK),       # Wood Nailer Fastener Type/Spacing
      (392,174.0,'26ga 3"x3"; nail 4" o/c',8,INK),  # Drip Edge Metal Attachment
      (388,78.0,'Cleated coping cap; fasten per FBC ~12" o/c',8,INK),  # Parapet Coping Metal Attachment
      # PE-sealed items -> explicit pointers (do NOT invent final pressures on a legal form)
      (95,640.0,'by sealed calc',7.5,RED),    # (P1') Field psf
      (210,640.0,'by sealed calc',7.5,RED),   # (P1) Field psf
      (60,618.0,'by sealed calc',7.5,RED),    # (P2) Perimeter psf
      (210,618.0,'by sealed calc',7.5,RED),   # (P3) Corner psf
    ]
    _overlay(pg,D)
    # red banner across the top reminding the pressures/fastening are PE-sealed
    _overlay(pg,[(150,724.0,'SeaBreeze pre-filled system. Wind pressures & zone fastening = PE sealed calc (see worksheet).',6.6,RED)])
    w.add_page(pg)

# ---------------- preliminary wind worksheet ----------------
def worksheet(w):
    buf=io.BytesIO(); c=canvas.Canvas(buf,pagesize=letter); W,H=letter
    c.setFillColor(NAVY); c.setFont('Helvetica-Bold',14); c.drawString(54,H-60,'PRELIMINARY WIND-UPLIFT WORKSHEET  -  RAS-128 / ASCE 7-22')
    c.setFillColor(RED); c.setFont('Helvetica-Bold',10); c.drawString(54,H-78,'FOR PE REVIEW & SEAL  -  not a final engineering certification')
    c.setStrokeColor(NAVY); c.line(54,H-86,W-54,H-86)
    c.setFillColor(HexColor('#222')); c.setFont('Helvetica',10)
    y=H-110
    def line(s,b=False,col=HexColor('#222'),dx=0):
        nonlocal y
        c.setFillColor(col); c.setFont('Helvetica-Bold' if b else 'Helvetica',10); c.drawString(54+dx,y,s); y-=16
    line('Project: %s, %s, FL %s  (%s)'%(CLIENT['owner'],CLIENT['address'],CLIENT['zip'],CLIENT['job']),b=True)
    line('System: Johns Manville hot-mopped BUR over wood deck  -  Miami-Dade NOA 25-0911.04',b=True); y-=4
    line('1) Velocity pressure  qh = 0.00256 * Kh * Kzt * Kd * Ke * V^2',b=True)
    line('   V = %.0f mph (ASCE 7-22 ultimate, Risk Cat II, Deerfield Beach/Broward HVHZ - verify ASCE Hazard Tool)'%V)
    line('   Kh = %.2f (Exposure C, h = %.0f ft)   Kzt = %.1f   Kd = %.2f (C&C)   Ke = %.1f'%(Kh,MRH,Kzt,Kd,Ke))
    line('   qh = 0.00256 * %.2f * %.1f * %.2f * %.1f * %.0f^2  =  %.1f psf'%(Kh,Kzt,Kd,Ke,V,qh),b=True); y-=4
    line('2) Elevated roof pressure zones (per form: a\' = 0.6h ; corner = 0.6h x 0.2h)',b=True)
    line('   Mean roof height h = %.0f ft   ->   P2 perimeter width a\' = %.1f ft'%(MRH,a_perim))
    line('   P3 corner = %.1f ft (length) x %.1f ft (width)'%(p3_len,p3_wid)); y-=4
    line('3) System capacity (from NOA 25-0911.04, wood deck)',b=True)
    line('   Base system (Type E1/E2, UltraFast plates 9" o/c lap, 12" o/c field):  -52.5 psf')
    line('   Maximum design pressure with enhanced fastening:                       -90.0 psf'); y-=4
    line('4) Design demand & zone fastening  ->  TO BE COMPLETED & SEALED BY FLORIDA PE',b=True,col=RED)
    for s in ['   The PE shall compute net C&C uplift demand for Zones 1\', 1, 2, 3 per ASCE 7-22 / RAS-128',
              '   and confirm each zone demand <= NOA capacity, increasing base-sheet fastening density at the',
              '   perimeter (P2) and corner (P3) zones as required. Corner demand at this site can approach or',
              '   exceed the -90 psf NOA maximum; enhanced anchorage or an alternate assembly may be needed.',
              '   Enter the sealed P1\'/P1/P2/P3 pressures and the per-zone fastener spacing on HVHZ Section C.']:
        c.setFillColor(HexColor('#444')); c.setFont('Helvetica',9.5); c.drawString(54,y,s); y-=14
    c.setStrokeColor(BLUE); c.line(54,90,W-54,90)
    c.setFillColor(NAVY); c.setFont('Helvetica',8)
    c.drawString(54,76,'Prepared by SeaBreeze Roofing as a preliminary aid. Final wind-uplift design must bear the seal and signature of a')
    c.drawString(54,66,'Florida Professional Engineer and be submitted with the permit application per FBC HVHZ requirements.')
    c.showPage(); c.save(); buf.seek(0)
    for p in PdfReader(buf).pages: w.add_page(p)

# ---------------- assemble ----------------
def main():
    w=PdfWriter()
    cover(w)
    stamp_bc(w, os.path.join(DEER,'BC_01_Uniform_Building_Permit_Application_PREFILLED.pdf'), _BC01)
    stamp_bc(w, os.path.join(DEER,'BC_02_Notice_of_Commencement_PREFILLED.pdf'), _BC02)
    section_c(w)
    worksheet(w)
    add_pdf(w, os.path.join(DEER,'BC_06_FDEP_Asbestos_Notification_PREFILLED.pdf'))
    add_pdf(w, JMNOA)
    with open(OUT,'wb') as fh: w.write(fh)
    print('WROTE', OUT)
    print('pages:', len(PdfReader(OUT).pages))

if __name__=='__main__':
    main()
