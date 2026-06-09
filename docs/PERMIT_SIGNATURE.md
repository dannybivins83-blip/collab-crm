# Permit Packet — Signatures (CRM behavior)

**Authoritative rule:** see the **Permit Signature & Notarization Runbook**
(`../../docs/PERMIT_SIGNATURE.md`, the parent project folder). This file only documents
what the CRM code does.

## The CRM does NOT put a captured signature on the permit packet — by design

Earlier plumbing forwarded the customer's captured e-signature into `build.py` via
`client["signature"]`. **That was removed** (`modules/permits.py`, `build_packet`).

Why: the permit packet's owner-signature forms — the **Notice of Commencement** and the
**re-roof nailing affidavit** — are **notarized**. The owner's signature on them *is* the
notarized signature and must be **wet-signed or RON-signed in the notary's presence**.
Stamping a pre-captured signature there would be forgery of a notarized instrument.

So `build_packet()` receives only data fields (owner, address, PCN, area, slope, etc.) and
leaves every signature / date / notary block **blank**, exactly as the build engine already
does.

## Where the captured signature DOES apply (non-notarized only)
- **Estimate proposal** — acceptance + signature block (the customer accepting their own quote).
- **Sign-up / contract package** — auto-signed from the authorized signature; this is a
  plain contract, not a notarized form (audited: `signups.py` references no notarized form).

The estimate e-sign consent text reflects this — it authorizes the proposal + sign-up docs
only, and explicitly notes notarized permit forms are signed separately.

## If the permit engine ever needs a NON-notarized owner line stamped
It would have to be a line that is genuinely not notarized (rare on these packets). Only
then revisit forwarding `client["signature"]`. Default: don't.
