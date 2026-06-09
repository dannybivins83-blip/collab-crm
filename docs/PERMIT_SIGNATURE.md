# Permit Packet — Stamping the Owner Signature

The CRM captures the homeowner's signature once (at estimate e-sign, with explicit
authorization to apply it to the permit packet) and passes it into the permit build
engine. This is what `build.py`'s `build_packet(...)` needs to do with it.

## What the CRM passes you

`build_packet(client, ahj, system, attachments, out_path, underlayment, product)` —
the `client` dict now includes three extra keys:

| key           | type | value                                                                 |
|---------------|------|-----------------------------------------------------------------------|
| `signature`   | str  | A **PNG data URL** — `data:image/png;base64,<...>` — the drawn signature. **Empty string `""`** if the customer did not authorize applying it to the permit packet. |
| `signed_name` | str  | The customer's typed name (e.g. `"Brian Wetherington"`).             |
| `signed_at`   | str  | ISO-ish timestamp string of when they signed (e.g. `"2026-06-09 14:32:10"`). |

**Contract:** only stamp the signature when `client.get("signature")` is non-empty.
Empty means "not authorized / not signed yet" — leave the owner-signature lines blank
for a wet signature, exactly as today.

## What to do

On the forms the engine already generates that have an **owner / applicant signature
line** (typically the **permit application** and the **NOC**), draw the signature image
on that line, with the name + date beneath or beside it.

Do **not** stamp it on a notary block or any line that legally requires a separate
witnessed/notarized signature — only the owner/applicant signature lines.

## Decode + draw (reportlab example)

```python
import base64, io
from reportlab.lib.utils import ImageReader

def _sig_reader(data_url):
    """data:image/png;base64,XXXX  ->  ImageReader, or None if absent/invalid."""
    if not data_url or "," not in data_url:
        return None
    try:
        raw = base64.b64decode(data_url.split(",", 1)[1])
        return ImageReader(io.BytesIO(raw))
    except Exception:
        return None

# at the owner-signature line on the canvas `c` (x, y in points):
sig = _sig_reader(client.get("signature"))
if sig:
    c.drawImage(sig, x, y, width=140, height=40,
                preserveAspectRatio=True, mask="auto")
    c.setFont("Helvetica", 8)
    c.drawString(x, y - 10, "%s   %s" % (client.get("signed_name", ""),
                                         (client.get("signed_at") or "")[:10]))
# else: leave the line blank (wet signature)
```

If the engine uses a fillable-PDF / overlay approach instead of drawing on a canvas,
write the decoded PNG into the signature field's rectangle the same way (any PDF lib
that supports image stamping works — pypdf overlay, pdf-lib, etc.).

## Test
- **With signature:** pass a `client` where `signature` is a real data URL → the owner
  signature line shows the image + name + date.
- **Without:** pass `signature=""` → the line is blank (unchanged from today).

The data URL is small (a hand-drawn PNG, typically a few KB). Sign over nothing — just
render it; the CRM already handled capture, authorization, and storage.
