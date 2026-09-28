# -*- coding: utf-8 -*-
"""Capture one element of the demo portal (demo chrome hidden) as a fixed-size frame.
Usage: python tools/hero/sectionshot.py <url> <out.png> <selector> <w> <h> <dsf> [mobile] [yoffset]
The clip starts at the element's top (+yoffset css px) and is exactly w x h css px, so every
frame shares one ratio. Prints the element's size so you can see how much is out of frame.
"""
import base64, io, sys, time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from PIL import Image
CHROME = r"C:/Program Files/Google/Chrome/Application/chrome.exe"
HIDE = ".demobar,.salecta,.cartfab,#demoToast{display:none!important}"
url, out, sel, w, h, dsf = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5]), int(sys.argv[6])
mobile = len(sys.argv) > 7 and sys.argv[7] == "mobile"
yoff = int(sys.argv[8]) if len(sys.argv) > 8 else 0
opts = Options(); opts.binary_location = CHROME
for a in ("--headless=new", "--no-sandbox", "--hide-scrollbars", "--disable-gpu", "--window-size=%d,%d" % (w, h)):
    opts.add_argument(a)
if mobile:
    opts.add_argument("--user-agent=Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
drv = webdriver.Chrome(service=Service(), options=opts)
try:
    drv.execute_cdp_cmd("Emulation.setDeviceMetricsOverride", {"width": w, "height": h, "deviceScaleFactor": dsf, "mobile": mobile})
    drv.get(url)
    drv.execute_script("return document.fonts.ready")
    drv.execute_script("var s=document.createElement('style');s.textContent=arguments[0];document.head.appendChild(s);", HIDE)
    # lazy images below the fold stay grey placeholders unless we force them
    drv.execute_script("document.querySelectorAll('img[loading=lazy]').forEach(function(i){i.loading='eager';if(i.dataset.src)i.src=i.dataset.src;});")
    rect = drv.execute_script("var e=document.querySelector(arguments[0]);if(!e)return null;e.scrollIntoView({block:'start'});var r=e.getBoundingClientRect();return [r.left+scrollX,r.top+scrollY,r.width,r.height];", sel)
    if not rect:
        raise SystemExit("selector not found: " + sel)
    for _ in range(20):
        pending = drv.execute_script("return Array.from(document.images).filter(function(i){return !i.complete}).length")
        if not pending: break
        time.sleep(0.5)
    time.sleep(1.0)
    # back to the top so the sticky header is not painted over the clip
    drv.execute_script("window.scrollTo(0,0)")
    time.sleep(0.4)
    top = int(rect[1]) + yoff
    shot = drv.execute_cdp_cmd("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True,
        "clip": {"x": 0, "y": top, "width": w, "height": h, "scale": 1}})
    im = Image.open(io.BytesIO(base64.b64decode(shot["data"]))).convert("RGB")
    im.save(out); print(out, im.size, "element(w,h)=", int(rect[2]), int(rect[3]), "top=", top)
finally:
    drv.quit()
