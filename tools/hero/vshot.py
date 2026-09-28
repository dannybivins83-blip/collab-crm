# -*- coding: utf-8 -*-
"""True-viewport screenshots (any width, DSF 1) via Selenium + CDP device metrics.
Usage: python .preview/vshot.py <url> <out_png> <width> <height> [full]
  default: capture exactly the viewport (the fold). 'full' = full page.
"""
import base64, io, sys
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from PIL import Image

CHROME = r"C:/Program Files/Google/Chrome/Application/chrome.exe"

def main():
    url, out, w, h = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
    full = len(sys.argv) > 5 and sys.argv[5] == "full"
    opts = Options()
    opts.binary_location = CHROME
    for a in ("--headless=new", "--no-sandbox", "--hide-scrollbars", "--disable-gpu",
              "--window-size=%d,%d" % (w, h)):
        opts.add_argument(a)
    drv = webdriver.Chrome(service=Service(), options=opts)
    try:
        drv.execute_cdp_cmd("Emulation.setDeviceMetricsOverride", {
            "width": w, "height": h, "deviceScaleFactor": 1, "mobile": w < 768})
        drv.get(url)
        drv.execute_script("return document.fonts.ready")
        import time; time.sleep(1.5)
        vw = drv.execute_script("return [innerWidth, innerHeight, document.querySelector('.wrap')&&document.querySelector('.wrap').getBoundingClientRect().width]")
        ch = h
        if full:
            m = drv.execute_cdp_cmd("Page.getLayoutMetrics", {})
            css = m.get("cssContentSize") or m.get("contentSize")
            ch = min(int(css["height"]), 20000)
        shot = drv.execute_cdp_cmd("Page.captureScreenshot", {
            "format": "png", "captureBeyondViewport": True,
            "clip": {"x": 0, "y": 0, "width": w, "height": ch, "scale": 1}})
        Image.open(io.BytesIO(base64.b64decode(shot["data"]))).convert("RGB").save(out)
        print("viewport(innerW,innerH,wrapW)=", vw, "->", out)
    finally:
        drv.quit()

if __name__ == "__main__":
    main()
