# -*- coding: utf-8 -*-
"""Compose the roofer-landing hero: laptop + phone (real demo frames) over a roof photo.
Usage: python tools/hero/compose_hero.py <backdrop> <out.jpg> [laptop_frame.png] [phone_frame.png]
(frames come from tools/hero/frameshot.py against /demo/<slug>; see git log for the recipe)
"""
import sys
from PIL import Image, ImageDraw, ImageFilter, ImageEnhance

W, H = 2000, 1300
back_path, out = sys.argv[1], sys.argv[2]
NAVY = (7, 28, 43)

def cover(im, w, h):
    s = max(w / im.width, h / im.height)
    im = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
    x = (im.width - w) // 2; y = (im.height - h) // 2
    return im.crop((x, y, x + w, y + h))

def rrect(size, r, fill, outline=None, ow=0):
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), r, fill=fill, outline=outline, width=ow)
    return im

def shadow(canvas, box, r, blur=44, dy=34, alpha=150):
    x0, y0, x1, y1 = box
    pad = blur * 3
    sh = Image.new("RGBA", (x1 - x0 + 2 * pad, y1 - y0 + 2 * pad), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle((pad, pad, pad + x1 - x0, pad + y1 - y0), r, fill=(0, 0, 0, alpha))
    sh = sh.filter(ImageFilter.GaussianBlur(blur))
    canvas.alpha_composite(sh, (x0 - pad, y0 - pad + dy))

# ---- backdrop -------------------------------------------------------------
bg = cover(Image.open(back_path).convert("RGB"), W, H)
bg = bg.filter(ImageFilter.GaussianBlur(2.2))
bg = ImageEnhance.Color(bg).enhance(0.85)
bg = ImageEnhance.Brightness(bg).enhance(0.72)
canvas = bg.convert("RGBA")
# navy wash, heavier on the left so it melts into the hero's navy gradient
grad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
gd = ImageDraw.Draw(grad)
for x in range(W):
    a = int(210 * max(0.0, 1 - x / (W * 0.62)) ** 1.5) + 40
    gd.line([(x, 0), (x, H)], fill=NAVY + (min(255, a),))
canvas.alpha_composite(grad)
# bottom vignette
vg = Image.new("RGBA", (W, H), (0, 0, 0, 0))
vd = ImageDraw.Draw(vg)
for y in range(H):
    a = int(140 * max(0.0, (y - H * 0.55) / (H * 0.45)) ** 1.6)
    vd.line([(0, y), (W, y)], fill=NAVY + (a,))
canvas.alpha_composite(vg)

# ---- laptop ---------------------------------------------------------------
lap = Image.open(sys.argv[3] if len(sys.argv) > 3 else ".preview/frame_laptop.png").convert("RGB")  # 2880x1720 (1440x860 @2x)
SW, SH = 1180, 738  # 16:10 screen
sx, sy = 90, 118
bez = 16
body = (sx - bez, sy - bez - 8, sx + SW + bez, sy + SH + bez)
shadow(canvas, (body[0], body[1], body[2] + 60, body[3] + 40), 26)
canvas.alpha_composite(rrect((body[2] - body[0], body[3] - body[1]), 24, (24, 27, 32, 255), (70, 74, 82, 255), 2), (body[0], body[1]))
screen = lap.crop((0, 0, 2880, round(2880 * SH / SW))).resize((SW, SH), Image.LANCZOS)
canvas.paste(screen, (sx, sy))
# camera dot
ImageDraw.Draw(canvas).ellipse((sx + SW // 2 - 5, sy - bez - 2, sx + SW // 2 + 5, sy - bez + 8), fill=(50, 54, 62, 255))
# base / deck (slim aluminium foot with a lit top edge)
bx0, bx1 = body[0] - 48, body[2] + 48
by0 = body[3]
DH = 30
deck = Image.new("RGBA", (bx1 - bx0, DH), (0, 0, 0, 0))
dd = ImageDraw.Draw(deck)
for y in range(DH):
    t = y / (DH - 1)
    c = (int(214 - 90 * t), int(218 - 90 * t), int(224 - 90 * t), 255)
    dd.line([(0, y), (bx1 - bx0, y)], fill=c)
dd.line([(0, 0), (bx1 - bx0, 0)], fill=(240, 242, 245, 255))
m = Image.new("L", (bx1 - bx0, DH), 0)
ImageDraw.Draw(m).rounded_rectangle((0, 0, bx1 - bx0 - 1, DH - 1), 10, fill=255)
ImageDraw.Draw(m).rectangle((0, 0, bx1 - bx0, 6), fill=255)
deck.putalpha(m)
shadow(canvas, (bx0, by0, bx1, by0 + DH), 10, blur=30, dy=22, alpha=120)
canvas.alpha_composite(deck, (bx0, by0))
ImageDraw.Draw(canvas).rounded_rectangle((sx + SW // 2 - 120, by0 + 1, sx + SW // 2 + 120, by0 + 8), 4, fill=(120, 124, 132, 255))

# ---- phone (front and centre) -------------------------------------------
ph = Image.open(sys.argv[4] if len(sys.argv) > 4 else ".preview/frame_phone.png").convert("RGB")  # 1170x4200 (390x1400 @3x)
PW, PH = 430, 930  # screen size on canvas (390x844 css * 1.1025)
crop = ph.crop((0, 306 * 3, 1170, (306 + 844) * 3)).resize((PW, PH), Image.LANCZOS)
pbez = 15
px, py = 1062 - PW // 2, 246
pbody = (px - pbez, py - pbez, px + PW + pbez, py + PH + pbez)
shadow(canvas, pbody, 72, blur=50, dy=40, alpha=190)
canvas.alpha_composite(rrect((pbody[2] - pbody[0], pbody[3] - pbody[1]), 74, (14, 17, 21, 255), (78, 82, 90, 255), 3), (pbody[0], pbody[1]))
scr = crop.convert("RGBA")
scr.putalpha(rrect((PW, PH), 60, (255, 255, 255, 255)).split()[3])
canvas.alpha_composite(scr, (px, py))
# dynamic island
ImageDraw.Draw(canvas).rounded_rectangle((px + PW // 2 - 62, py + 14, px + PW // 2 + 62, py + 48), 17, fill=(10, 12, 15, 255))
# side buttons
d = ImageDraw.Draw(canvas)
d.rounded_rectangle((pbody[2], py + 190, pbody[2] + 4, py + 260), 2, fill=(60, 64, 72, 255))
d.rounded_rectangle((pbody[0] - 4, py + 150, pbody[0], py + 200), 2, fill=(60, 64, 72, 255))
d.rounded_rectangle((pbody[0] - 4, py + 220, pbody[0], py + 300), 2, fill=(60, 64, 72, 255))

canvas.convert("RGB").save(out, "JPEG", quality=86, optimize=True, progressive=True)
print(out, canvas.size)
