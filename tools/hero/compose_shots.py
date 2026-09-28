# -*- coding: utf-8 -*-
"""Product-shot compositor: one demo-section frame -> a 1600x1200 (4:3) card image with a
phone frame or browser chrome, a soft shadow and a light backdrop. Same family as
compose_hero.py so the "What your customer opens" cards match the hero.

Usage: python tools/hero/compose_shots.py phone   <frame.png> <out.jpg>
       python tools/hero/compose_shots.py browser <frame.png> <out.jpg> [address]
phone frames: 390x816 css (any DSF); browser frames: 1024x596 css (any DSF).
"""
import sys
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1600, 1200
mode, src, out = sys.argv[1], sys.argv[2], sys.argv[3]
address = sys.argv[4] if len(sys.argv) > 4 else "portal.summitroofingco.com"

def rrect(size, r, fill, outline=None, ow=0):
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), r, fill=fill, outline=outline, width=ow)
    return im

def shadow(canvas, box, r, blur=40, dy=28, alpha=70):
    x0, y0, x1, y1 = box; pad = blur * 3
    sh = Image.new("RGBA", (x1 - x0 + 2 * pad, y1 - y0 + 2 * pad), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle((pad, pad, pad + x1 - x0, pad + y1 - y0), r, fill=(7, 28, 43, alpha))
    canvas.alpha_composite(sh.filter(ImageFilter.GaussianBlur(blur)), (x0 - pad, y0 - pad + dy))

def font(sz):
    for f in ("C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf"):
        try: return ImageFont.truetype(f, sz)
        except Exception: pass
    return ImageFont.load_default()

# ---- backdrop: warm off-white with a faint navy-tinted radial glow ---------------
canvas = Image.new("RGBA", (W, H), (247, 245, 240, 255))
glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
gd = ImageDraw.Draw(glow)
cx, cy = W // 2, int(H * 0.42)
for i in range(60, 0, -1):
    rr = int(i * 16)
    a = int(38 * (1 - i / 60) ** 1.2)
    gd.ellipse((cx - rr * 1.25, cy - rr, cx + rr * 1.25, cy + rr), fill=(215, 225, 232, a))
canvas.alpha_composite(glow.filter(ImageFilter.GaussianBlur(30)))
# faint horizon line to ground the device
ImageDraw.Draw(canvas).line([(0, int(H * 0.86)), (W, int(H * 0.86))], fill=(226, 228, 226, 255), width=1)

frame = Image.open(src).convert("RGB")

if mode == "phone":
    PW, PH = 720, 1506                     # 390x816 css * 1.846 - readable at card size
    pbez = 22
    px, py = (W - PW) // 2, 96             # top margin; the bottom of the phone runs off-canvas
    pbody = (px - pbez, py - pbez, px + PW + pbez, py + PH + pbez)
    shadow(canvas, pbody, 100, blur=50, dy=30, alpha=110)
    canvas.alpha_composite(rrect((pbody[2] - pbody[0], pbody[3] - pbody[1]), 104, (14, 17, 21, 255), (78, 82, 90, 255), 4), (pbody[0], pbody[1]))
    scr = frame.resize((PW, PH), Image.LANCZOS).convert("RGBA")
    scr.putalpha(rrect((PW, PH), 86, (255, 255, 255, 255)).split()[3])
    canvas.alpha_composite(scr, (px, py))
    d = ImageDraw.Draw(canvas)
    d.rounded_rectangle((px + PW // 2 - 92, py + 20, px + PW // 2 + 92, py + 70), 25, fill=(10, 12, 15, 255))
    d.rounded_rectangle((pbody[2], py + 280, pbody[2] + 6, py + 390), 3, fill=(60, 64, 72, 255))
    d.rounded_rectangle((pbody[0] - 6, py + 220, pbody[0], py + 295), 3, fill=(60, 64, 72, 255))
    d.rounded_rectangle((pbody[0] - 6, py + 325, pbody[0], py + 445), 3, fill=(60, 64, 72, 255))
else:
    CW, CH = 1460, 850                     # 1024x596 css * 1.426 - readable at card size
    TB = 52
    wx, wy = (W - CW) // 2, (H - (CH + TB)) // 2
    win = (wx, wy, wx + CW, wy + TB + CH)
    shadow(canvas, win, 18, blur=44, dy=30, alpha=95)
    canvas.alpha_composite(rrect((CW, TB + CH), 18, (255, 255, 255, 255), (214, 218, 222, 255), 2), (wx, wy))
    # toolbar
    tb = Image.new("RGBA", (CW, TB), (0, 0, 0, 0))
    td = ImageDraw.Draw(tb)
    td.rounded_rectangle((0, 0, CW - 1, TB + 30), 18, fill=(236, 239, 242, 255))
    td.rectangle((0, TB - 1, CW, TB), fill=(214, 218, 222, 255))
    for i, c in enumerate(((255, 95, 87), (255, 189, 46), (40, 201, 64))):
        td.ellipse((22 + i * 22, 19, 36 + i * 22, 33), fill=c)
    pill = (120, 13, CW - 120, TB - 13)
    td.rounded_rectangle(pill, 13, fill=(255, 255, 255, 255), outline=(214, 218, 222, 255))
    f = font(17)
    tw = td.textlength(address, font=f)
    td.text(((CW - tw) // 2 + 10, 16), address, fill=(52, 69, 79, 255), font=f)
    # tiny padlock
    lx = int((CW - tw) // 2) - 14
    td.rounded_rectangle((lx - 6, 24, lx + 4, 32), 2, fill=(110, 123, 130, 255))
    td.arc((lx - 5, 17, lx + 3, 27), 180, 360, fill=(110, 123, 130, 255), width=2)
    canvas.alpha_composite(tb, (wx, wy))
    content = frame.resize((CW - 2, CH - 2), Image.LANCZOS).convert("RGBA")
    m = rrect((CW - 2, CH - 2), 16, (255, 255, 255, 255))
    ImageDraw.Draw(m).rectangle((0, 0, CW, 30), fill=(255, 255, 255, 255))
    content.putalpha(m.split()[3])
    canvas.alpha_composite(content, (wx + 1, wy + TB))

canvas.convert("RGB").save(out, "JPEG", quality=90, optimize=True, progressive=True)
print(out, canvas.size)
