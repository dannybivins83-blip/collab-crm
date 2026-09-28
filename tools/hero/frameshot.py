# -*- coding: utf-8 -*-
"""Capture a demo-portal frame with the demo chrome hidden.
Usage: python .preview/frameshot.py <url> <out> <w> <h> <clipY> <clipH> <dsf> [mobile]
"""
import base64, io, sys, time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from PIL import Image
CHROME = r"C:/Program Files/Google/Chrome/Application/chrome.exe"
HIDE = ".demobar,.salecta,.cartfab,#demoToast{display:none!important}"
url, out, w, h, cy, chh, dsf = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5]), int(sys.argv[6]), int(sys.argv[7])
mobile = len(sys.argv) > 8
opts = Options(); opts.binary_location = CHROME
for a in ("--headless=new","--no-sandbox","--hide-scrollbars","--disable-gpu","--window-size=%d,%d"%(w,h)):
    opts.add_argument(a)
if mobile:
    opts.add_argument("--user-agent=Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
drv = webdriver.Chrome(service=Service(), options=opts)
try:
    drv.execute_cdp_cmd("Emulation.setDeviceMetricsOverride", {"width": w, "height": h, "deviceScaleFactor": dsf, "mobile": mobile})
    drv.get(url)
    drv.execute_script("return document.fonts.ready")
    drv.execute_script("var s=document.createElement('style');s.textContent=arguments[0];document.head.appendChild(s);", HIDE)
    time.sleep(2)
    shot = drv.execute_cdp_cmd("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True,
        "clip": {"x": 0, "y": cy, "width": w, "height": chh, "scale": dsf}})
    im = Image.open(io.BytesIO(base64.b64decode(shot["data"]))).convert("RGB"); im.save(out); print(out, im.size)
finally:
    drv.quit()
