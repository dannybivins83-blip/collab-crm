# -*- coding: utf-8 -*-
"""Shrink image-heavy PDFs in uploads/ using pypdf + Pillow (no Ghostscript):
downsample large embedded images, re-encode JPEG, and compress content streams.
Keeps the optimized file only when it's actually smaller. Lossless-ish at the
text level; images are recompressed at a sensible quality for screen/print.

Usage:  python scripts/optimize_pdfs.py [subdir ...]   (default: library documents permits)
"""
import os
import sys
import io

from pypdf import PdfReader, PdfWriter
from PIL import Image

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import config  # noqa

MAX_DIM = 1700      # cap the longest image edge (px) — ~150dpi on a letter page
JPEG_Q = 58         # recompression quality
MIN_BYTES = 250_000  # skip files already small


def optimize(path):
    try:
        writer = PdfWriter(clone_from=path)
    except Exception as e:
        return None, "open failed: %s" % e
    changed = False
    for page in writer.pages:
        try:
            imgs = list(page.images)
        except Exception:
            imgs = []
        for img in imgs:
            try:
                im = img.image
                if im is None:
                    continue
                w, h = im.size
                scale = MAX_DIM / float(max(w, h)) if max(w, h) > MAX_DIM else 1.0
                if scale < 1.0:
                    im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
                if im.mode in ("RGBA", "P", "LA"):
                    im = im.convert("RGB")
                img.replace(im, quality=JPEG_Q)
                changed = True
            except Exception:
                continue
        try:
            page.compress_content_streams()
        except Exception:
            pass
    if not changed:
        return None, "no images to optimize"
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue(), "ok"


def main():
    subdirs = sys.argv[1:] or ["library", "documents", "permits"]
    grand_before = grand_after = 0
    n_done = 0
    for sub in subdirs:
        d = os.path.join(config.UPLOAD_DIR, sub)
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.lower().endswith(".pdf"):
                continue
            path = os.path.join(d, fn)
            before = os.path.getsize(path)
            if before < MIN_BYTES:
                continue
            data, msg = optimize(path)
            if not data:
                continue
            after = len(data)
            if after < before * 0.92:  # only keep if we saved >8%
                with open(path, "wb") as f:
                    f.write(data)
                grand_before += before
                grand_after += after
                n_done += 1
                print("  %-52s %5.1fMB -> %5.1fMB (-%d%%)" % (
                    fn[:52], before / 1e6, after / 1e6, round(100 * (1 - after / before))))
    print("\nOptimized %d files: %.1f MB -> %.1f MB  (saved %.1f MB)" % (
        n_done, grand_before / 1e6, grand_after / 1e6, (grand_before - grand_after) / 1e6))


if __name__ == "__main__":
    main()
