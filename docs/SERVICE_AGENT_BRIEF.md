# MyRoofPortal — operating brief for the service agent

Everything an agent needs to run this as an online app service. Written 2026-11-14 from a
session that shipped the homeowner product, rebranded the install and cleaned a departed
tenant out of it. Facts here were verified against the live host, not recalled.

**Read this before touching anything.** The traps in section 6 each cost real time to find,
and every one of them fails *silently*.

---

## 1. What the service is

A white-label CRM for roofing contractors, plus a homeowner-facing product on top of it.
One Flask app serves both audiences on two domains.

| URL | Audience | What it is |
| --- | --- | --- |
| `myroofportal.com/` | Homeowners | Compare roof quotes apples-to-apples. The front door since 2026-11-13. |
| `myroofportal.com/c/<token>` | One homeowner | Their private workspace. Unguessable token, `noindex`. |
| `myroofportal.com/roofers` | Roofing contractors | The pitch: buy a branded portal for your customers. |
| `myroofportal.com/demo/roof-portal` | Contractors evaluating | The portal running on sample data. |
| `myroofportal.com/acquire` | Buyers | The domain + software for sale. |
| `myroofportal.com/blog` | Both | 7 guides. Posts declare their own audience. |
| `crm.collaborativeconceptsfl.com` | Operator | The CRM itself. Login-gated. |

Both domains are **the same app on the same Render service**. A billing suspension on
2026-10-29 took down the sales site, the CRM and SiteCam simultaneously, for seven days,
because they share one host. That is the known cost of the consolidation.

### The homeowner product, in one paragraph

Three roof quotes for the same house are rarely the same scope, and the cheapest is usually
the shortest. The tool is a **22-line spec sheet** in 5 groups (tear-off and decking, the
water layers, the roof itself, paperwork and warranty, money and the day). The homeowner
marks what each *written* quote commits to. Out of that falls: where the quotes disagree
(the real answer to "why is one cheaper"), a question per gap worded to read aloud on the
phone, and a stage tracker that runs past signing to closed permit and warranty in hand.

`compare.SPEC` **is the product.** The entry form, the comparison grid and the generated
questions all read from that one list, so adding a line adds it in all three places.

### Hard product boundary — do not cross

No price guidance. No ranking of contractors. No "recommended" bid. The service never sees
the roof and is not licensed to advise on it. The tool makes gaps visible; every judgement
stays with the homeowner. This is also what keeps the service clear of giving contractor
advice without a license.

---

## 2. Identity — get this wrong and it is a legal problem

- The company is **Collaborative Concept LLC**. Singular. No "s". FL Doc `L20000345198`.
- The product is **MyRoofPortal**.
- **The `license` field in company settings must stay EMPTY.** Collaborative Concept is a
  software company and does not hold a contractor license. Until 2026-11-14 the install
  carried a real contractor's live license numbers, which printed onto estimate and permit
  PDFs the software generated. Never put a contractor license on this entity.
- **SeaBreeze is removed and stays removed.** It was the original reference tenant. As of
  2026-11-14 no SeaBreeze string survives in `company_settings`, all five of its logins are
  deactivated, and its OAuth grants are gone. Historical job records still mention it —
  leave those alone; they are records of work that happened.
- "Summit Roofing Co." is the fictional sample contractor shown *inside* the demo. That is
  deliberate and is not the service's brand.

---

## 3. Architecture

Flask + Jinja + vanilla JS. ~56 blueprints in `modules/`, ~86,000 lines of Python.

- **Host:** Render, service `srv-d8kq47jbc2fs73crtnug`, name `collab-crm`.
- **Database:** **SQLite on a Render persistent disk** (`dsk-d8kq47jbc2fs73crto4g`,
  `crm-data`, 5 GB, mounted `/data`). It is NOT Postgres. The repo's `DATABASE_URL`
  points at a *different* project's database and will silently return wrong tables.
  Locally, set `DATABASE_URL=""` to force SQLite.
- **Live data, for scale:** 8,547 jobs, 104,879 activities, 19,342 documents, 6,720 leads,
  8,951 contacts, 3,257 estimates.
- **Multi-host routing:** `app.py`'s `_demo_host_root` before-request renders the homeowner
  page in place for `/` on hosts in `CRM_DEMO_HOSTS`. On the CRM host, `/` is the dashboard.
  Anything host-conditional must handle both.

### Reading the live database

```
GET https://crm.collaborativeconceptsfl.com/admin/db-download
Header: X-Restore-Token: <DB_RESTORE_TOKEN from the Render env>
```
Returns a consistent snapshot via SQLite's backup API — safe mid-write. This is the only
honest way to check production state. ~55 MB.

---

## 4. Deploy runbook

Render **auto-deploys from `agent/gc-consolidation`**, not `main`. This has caused a
multi-day phantom stall before, when everyone pushed `main` and nothing shipped.

```bash
git push origin agent/gc-consolidation
git push origin agent/gc-consolidation:main     # keep both at the same tip
```

Then **verify the deploy landed** — never assume:

```bash
curl -s https://myroofportal.com/healthz        # {"ok":true,"sha":"<commit>"}
```

Timing, measured: build 1–10 minutes, then `update_in_progress` while it restarts, during
which the site returns **502**. A 502 right after a push is the restart, not a failure.
Check `GET /v1/services/<id>/deploys?limit=1` for `status`, and treat `live` plus a matching
`/healthz` sha as the only proof.

**Before trusting any deploy claim, check `suspended` on the service.** Billing suspensions
have hit twice (2026-09-30 and 2026-10-29). The API's `/resume` only clears *user*
suspensions; a billing hold needs the owner to pay. A daily `render-uptime-watchdog`
scheduled task now checks uptime, suspension state and unread Render billing mail.

---

## 5. Current state

**Live and working:** the homeowner comparison tool and workspace, the roofer pitch, the
demo portal, 7 blog posts, the CRM, first-party funnel tracking in `portal_events`.

**Broken or unfinished — in priority order:**

1. **Owner lead alerts do not send.** Every row in `portal_leads` has `notified = 0`, going
   back to the first test in August. Root cause is settled (section 6). Mitigations are in
   place: a dashboard card for un-alerted leads, and a 2-hourly `myroofportal-lead-alert`
   scheduled task. The real fix is arming Resend — `ops/arm_resend.py`.
2. **No logo.** `company_settings.logo_path` is empty, so pages and generated PDFs render
   without a mark. Assets exist (section 8) but have not been uploaded through Settings.
3. **The AccuLynx API key needs rotating.** It was printed into a session transcript on
   2026-11-13. Treat as exposed.
4. **SiteCam API is down** since 2026-09-30 — its free Render Postgres was deleted. Needs a
   new database and a reseed. Resuming billing does not fix it.
5. **Inbound is all bots.** Every `portal_leads` row between 2026-09-29 and 2026-10-28 was
   bot-farmed: gmail dot-trick addresses, keyboard-mash names, "Xxxxxx LLC" companies.
   Zero genuine inbound leads have ever arrived. 150 cold emails to roofers produced one
   genuine click and no leads. Do not read the lead table as demand.

---

## 6. The traps

Every one of these fails silently. This is the most valuable section here.

**Auth gates by ENDPOINT NAME, not by path.** `modules/auth.py` has a `PUBLIC` set of
endpoint names. Adding a route under `/admin` or any public path does nothing on its own —
an unlisted endpoint 302s to `/login` and its own token gate is never reached. Every new
public or token-gated route must be added to `PUBLIC`. Note `_CSRF_EXEMPT = PUBLIC | {...}`,
so listing an endpoint also exempts its POST from CSRF.

**`db.py` has a `TABLE_ALLOWLIST`.** Every query is checked against it. A new table raises
`ValueError` until it is added.

**`portal_base.html`'s `{% block style %}` sits INSIDE its `<style>` element.** A child
template that wraps its CSS in its own `<style>` tag nests one inside the other and
**silently kills the first rule in the block**. Emit bare CSS in that block. This produced
white text on a cream background and looked like a color bug.

**`db.get_company()` caches per request on `flask.g`.** Reading it straight back after
`save_company()` returns the PRE-save row. An endpoint that does so will report the old
value as the new one and appear to work while telling you nothing. Pop `_company_cache`
before re-reading.

**Gmail SMTP cannot work from this host at all.** Probed layer by layer from the live
server: DNS resolves, TCP opens on both 587 and 465, the TLS handshake succeeds, and then
Google **drops the connection at AUTH** — it never returns `535`. That is Google declining
to authenticate from a datacenter IP, not a password being refused. No credential rotation
or port change fixes it. `api.resend.com` over HTTPS **is** reachable, so an HTTP mail
transport is the only road. `GET /admin/mail-status?probe=1` re-runs this diagnostic.

**A health check based on configuration will lie to you.** The sales inbox showed "Alerts
are on" for three months while every send failed, because it judged on `smtp_configured()`,
which only means the env vars are *set*. Base health on evidence of a successful send.

**Never conclude a credential is dead without a control that behaves differently.** A
deliberately bogus password produced the identical error to the real one from a developer
machine, because that network also cannot complete a Gmail SMTP login. Several days of
reasoning rested on that un-controlled test.

**Do not infer the tenant from `company_settings`.** That row was stale for months. Two
sessions concluded the install belonged to a contractor when it is the generic product site.

**Match misspellings when cleaning up a brand.** The departed tenant's logo file was
`seabreese-logo-website-2.png` — an `s` where the brand has a `z`. A single-spelling needle
missed the single most visible artifact on the site.

**Guard the last admin.** A cleanup that deactivates every matching account can strip every
admin and lock the owner out; a maintenance token is not a login. `/admin/debrand` now
returns 409 unless an active admin survives.

**Anchor targets land under the sticky header** (77px) unless `[id]` carries
`scroll-margin-top`. Fixed in `portal_base.html`.

**Render's deploy is not git-driven everywhere.** The *marketing* site in
`collaborativeconcepts-repo` deploys by `npx vercel --prod` from the CLI; pushing its branch
only builds a Preview. Different repo, different rule.

---

## 7. Maintenance endpoints

All token-gated by `X-Restore-Token` (`DB_RESTORE_TOKEN`) and 404 when unarmed or wrong —
indistinguishable from a route that does not exist.

| Endpoint | What it does |
| --- | --- |
| `GET /admin/db-download` | Consistent SQLite snapshot of production. |
| `GET /admin/mail-status` | Which transports are armed, where alerts go, the last error. |
| `GET /admin/mail-status?send=1` | Attempts one real send and returns the verdict. |
| `GET /admin/mail-status?probe=1` | Layered SMTP probe: DNS, TCP 587/465, handshake, login, Resend reachability. |
| `GET/POST /admin/debrand` | Removes a departed brand. GET previews. Deactivates, never deletes. |
| `GET/POST /admin/company-identity` | Sets whitelisted identity fields. `name`/`legal_name`/`license`/`address` need `?confirm=rebrand`. |

---

## 8. Brand

Tokens live in `static/portal_sale/brand/design-tokens.css` and are mirrored in
`portal_base.html`'s `:root`. They are the source of truth; do not invent new colors.

| Token | Value | Use |
| --- | --- | --- |
| `navy-950` | `#071c2b` | Page ground on dark, all headings, the mark's field |
| `navy-900` / `navy-800` | `#0d2b3a` / `#123a4c` | Raised surfaces on dark |
| `sky-500` | `#178fe5` | Links, the `.com` in the wordmark, primary buttons |
| `teal-600` / `teal-500` | `#14858a` / `#1e9ba0` | Section eyebrows, secondary accents |
| `gold-500` | `#d7a438` | **Calls to action only.** Never body text, never decoration. |
| `gold-100` | `#fff3d5` | The "needs attention" card ground |
| `warm-50` | `#f7f5f0` | Page ground on light. A warm neutral, not white. |
| `slate-700` / `slate-500` | `#34454f` / `#6e7b82` | Body text / secondary text |
| `line` | `#d9dfe1` | Borders and rules |
| `success` / `danger` | `#17845b` / `#b8443b` | State only, never accent |

**Type:** Inter, weights 400–800. Headings `letter-spacing: -.02em`, `line-height: 1.12`.
Uppercase labels get `letter-spacing: .06em`.

**Logo:** the wordmark is `myroofportal` in 800 weight with `.com` in `sky-500`. A mark was
drafted this session at `static/brand/mark.svg` and `mark-mono.svg` — a roofline over a lit
opening, with a one-color version for faxed permits. **It has not been visually reviewed;
treat it as a draft and look at it before shipping it.** The existing
`myroofportal-wordmark.svg` is white-only and therefore invisible on the light background
and on printed PDFs; it needs a navy variant.

**Voice:** plain, specific, never salesy. Short sentences. Name what a thing costs the
reader if they skip it. No invented statistics, no testimonials, no fabricated logos or ROI
figures — the funnel has none of these and must not pretend otherwise.

---

## 9. Operating rules for the agent

1. **Verify against the live system, not against memory or a prior session's claim.** This
   codebase has burned multiple sessions on stale narration. `/healthz`, the Render API and
   a fresh `db-download` are the sources of truth.
2. **State the verification method before claiming something works.** "Deployed" means a
   matching sha from `/healthz`.
3. **Never fabricate proof** — no invented stats, testimonials, logos or ROI numbers.
4. **Secrets never enter chat, commits, logs or screenshots.** Names and status only. A
   secret that appears in any of those is burned and must be rotated. This brief exists
   partly because that rule was broken on 2026-11-13.
5. **Money and irreversible actions are the owner's.** Prepare them to a single click.
6. **Test a cleanup against a copy of production before running it on production.** Both
   faults in the de-branding tool were found that way and would have been damaging live.
7. **American spelling in all customer-facing text** — "license", not "licence". Two British
   spellings shipped live this session before being caught.
8. **Every email stays a draft** unless the owner says to send that specific one.

---

## 10. Where things are

| Thing | Path |
| --- | --- |
| The app | `whitelabel-crm/` |
| Homeowner product | `modules/compare.py`, `templates/compare_*.html` |
| Sales pages + demo | `modules/demos.py`, `templates/portal_*.html` |
| Live portal | `modules/portal.py` → renders `templates/demo_portal.html` with `live=True` |
| Blog | `modules/blog.py`, `content/blog/*.md` |
| Maintenance | `modules/dbadmin.py` |
| Auth + the PUBLIC set | `modules/auth.py` |
| Shared chrome + tokens | `templates/portal_base.html` |
| Mail transports | `modules/gmail.py` (`send_alert` → Resend, then SMTP) |
| Arming Resend | `ops/arm_resend.py` |
| Brand assets | `static/portal_sale/brand/`, `static/brand/` |

A separate marketing site and the Somerset field-survey PWA live in
`collaborativeconcepts-repo` and ship by `npx vercel --prod`, not by git push.
