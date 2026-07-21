# Example Jobs by AHJ × Roof System (most recent per combo)

Built 2026-07-20 from 1,723 AccuLynx jobs (approved/completed/invoiced/closed milestones,
canceled excluded), restricted to R-24000+. "Most recent" = highest job number.
Regenerate with `find_example_jobs.py` (pulls fresh from the AccuLynx API; key read from
`whitelabel-crm/data/crm_migration.db`, never printed).

**AHJ rule used:** `PBC` token in job name → PBC (unincorporated); mailing city "Lake Worth"
→ PBC* (unincorporated, verify parcel); otherwise AHJ = mailing city. `DB` token is ambiguous
(Deerfield Beach vs Delray Beach) — resolved by street address. Systems from `tradeTypes`
plus name codes `S/T/M/F/5V + squares`.

## Palm Beach County (unincorporated)
| System | Job | Client | Date / milestone | Address |
|---|---|---|---|---|
| Shingle | R-26074 | Silvennoinen S49 | 2026-07-09 approved | 6887 Wilson Rd, WPB 33413 |
| Metal | R-26051 | Telford M24 | 2026-05-31 | 6272 Branchwood Dr, Greenacres 33467 |
| Tile | R-26068 | Levy T27 | 2026-07-01 | 5314 Indianwood Village Ln, Greenacres 33463 |
| Flat | R-26057 | Goren F3 | 2026-06-03 | 3668 Mykonos Ct, Boca Raton 33487 (current job) |

## Boca Raton
| System | Job | Client | Date | Address |
|---|---|---|---|---|
| Shingle | R-26075 | Allison House (Tamko Thunderstorm Grey) | 2026-07-14 | 528 NW 15th Ave |
| Metal | R-25118 | Heather Bush M50 ⚠ trades say Tile — verify | 2025-08-18 invoiced | 2281 NW 39th Dr |
| Tile | R-26025 | Costolo T55 | 2026-03-30 | 760 NE 37th St |
| Flat | R-26054 | Iconomou S28+F21 | 2026-06-02 | 297 NW 64th St |

## Delray Beach
| System | Job | Client | Date | Address |
|---|---|---|---|---|
| Shingle | R-26077 | Zamir S18 | 2026-07-15 | 3002 Cardinal Dr |
| Metal | R-26022 | Capano M26 | 2026-03-23 | 4061 NW 1st Ln |
| Tile | R-26019 | Calvagno T29 | 2026-03-11 | 4560 S Barwick Ranch Cir |
| Flat | R-26016 | DuBois S18+F2 | 2026-03-10 | 236 Cardinal Ln |

## Boynton Beach
| System | Job | Client | Date |
|---|---|---|---|
| Shingle | R-26072 | Winsted S29 | 2026-07-09 |
| Metal | R-26066 | Banushi M15+F5 | 2026-06-19 |
| Tile | R-26076 | Guerline T14 | 2026-07-14 |
| Flat | R-26067 | Cairo S22+F2 | 2026-06-26 |

## West Palm Beach
| System | Job | Client | Date |
|---|---|---|---|
| Shingle | R-25216 | Marrero S18 | 2025 |
| Metal | R-25210 | Googe M24 | 2025 |
| Tile | R-26044 | McCray T17 | 2026-05-19 |
| Flat | R-24336 | Torres F13 | 2025-01-24 ⚠ oldest example — no recent WPB flat |

## Lake Worth Beach
All four systems: **R-26078 Cleary S15+F3** (2026-07-15, 1443 Crest Dr) — multi-trade job; only recent LWB example.

## Royal Palm Beach
Shingle+Flat **R-26038 Boursiquot S28+F3** · Metal **R-26033 Dorsey M37** · Tile **R-26012 Pollock T47**

## Wellington
Shingle+Flat **R-26004 Forsythe S34+F3** · Metal **R-25183 Rothstein M33** ⚠ name says "LW" — verify AHJ · Tile **R-25060 Haloostock SQ57**

## Lantana
Shingle **R-25188 McCoy S17+F8** · Metal **R-24146 Usrey M22** · Tile **R-25057 Czoch T21+F8** · Flat **R-25195 2875 Ocean LLC F8** ⚠ name says Manalapan — likely Manalapan AHJ

## Palm Beach Gardens
Shingle **R-24311 Vo S11** · Metal+Flat **R-24253 Berkoff S24+F3** · Tile **R-25066 Timothee**

## Greenacres (city)
Shingle **R-25005 Estrella S32** · Tile **R-26053 Georges T22 (GA)** · Metal/Flat: **none R-24000+** (gap)

## Jupiter
Shingle **R-26030 Orange Reality S16** · Metal **R-26073 Robinson M68** (2026-07-08) · Tile **R-26023 Raines T9** · Flat **R-24259 Nook Diner** (2025-01, old)

## Deerfield Beach (Broward — HVHZ)
Shingle **R-24257 GFWC Woman's Club S43+F5** · Metal+Flat **R-26046 Chirillo M28+F2** (2026-05-21) · Tile **R-26041 Forsyth T30**

## Other Broward (HVHZ)
- **Fort Lauderdale**: Shingle R-25138 Kelley S40 · Tile R-25015 Yeater T14
- **Parkland**: Tile R-24328 Rosen T23 · Flat R-24191 Gibson T51+F1
- **Plantation**: Shingle+Flat R-25048 Rodriguez S22+F4 · Metal R-24352 Houmes
- **Hypoluxo (PBC)**: Shingle R-25038 Boger S14 · Tile R-24143 Tincler T16
- **Loxahatchee (PBC*)**: Shingle R-24330 Lasala only

## Known gaps (no R-24000+ example)
Greenacres metal/flat · WPB flat (only 2025-01) · Jupiter flat (only 2025-01) · Hypoluxo metal/flat · Loxahatchee metal/tile/flat · Fort Lauderdale metal/flat · Parkland shingle/metal · Hypoluxo→PBC fallback fine.

## Pulled documents (2026-07-20)

The permit/NOC/CoC documents for these jobs are downloaded to
**`packet_builder_handoff/approved_permit_examples/<AHJ>_<System>_<Rnum>/`** — 205 files,
39 job folders, ~555 MB, every file magic-byte verified (%PDF/JPEG/PNG). File prefixes:
`Permit__`/`NOC__`/`CertificateofCompletion__`
(pulled from the CRM's Google Drive store) or `<FolderShort>__`/`EmailDocs__` (pulled
straight from AccuLynx).

Two source pipelines (both scripted, reusable from the session scratchpad or rewritable):
1. **CRM DB → Google Drive** — 21 jobs already imported into the CRM: query `documents`
   (category Permit/NOC/CoC) in `data/crm_migration.db`, download via `modules/gdrive.py`
   service account. 123 files.
2. **AccuLynx direct** — 18 jobs not in the CRM doc store: v4
   `job-documents/{guid}/job-document-folders` inventory (needs a **logged-in browser
   session** — Bearer API key now returns the sign-in page), then download each file via
   `https://my.acculynx.com/store/companies/{co}/jobdocuments/{job}/{file}/{slug}` which
   301-redirects **without any auth** to a signed files.acculynx.com URL (valid ≈2028).
   Python follows the redirect directly. 79 files.

**Jobs with NO permit docs in AccuLynx yet** (2026 permits still in flight — recheck later):
R-26078 Cleary (LWB), R-26076 Guerline, R-26075 House, R-26074 Silvennoinen, R-26073 Robinson,
R-26072 Winsted, R-26068 Levy, R-26053 Georges, R-26046 Chirillo, R-26044 McCray,
R-26041 Forsyth, R-26030 Orange Reality, R-24259 Nook Diner.

**Content flag:** `PBC_Metal_R26051/` (Telford, PBC **Metal**) contains
`Zach_Telford_Greenacres_Tile_2ply_Permit_Packet.pdf` — a **Greenacres Tile** packet on a
PBC metal job. Same failure shape as the Lawrence Buck wrong-system incident. Verify before
using this row as a metal calibration example.

## Suspect rows — RESOLVED 2026-07-20 from the actual permit documents
The 5-agent audit read the recorded permits/NOCs themselves. Results:

1. **R-25118 Bush (Boca "Metal")** — ✅ **METAL CONFIRMED.** The clerk-stamped
   recorded NOC reads "Re-roof: tile to metal". AccuLynx `tradeTypes` (Tile
   Install/Tile Dry In) is the stale/wrong field. Safe to use as the Boca metal example.
2. **R-25183 Rothstein ("Wellington Metal")** — ❌ **NOT WELLINGTON.** The permit card
   reads "PROPERTY ADDRESS: 9493 Sedgewood Dr, Lake Worth, 33467" on a **Palm Beach
   County Building Division** permit. "LW" = Lake Worth, not a Wellington reference.
   This is a **PBC-unincorporated** job. Wellington therefore has no verified metal example.
3. **R-25195 2875 Ocean LLC (Lantana "Flat")** — still unverified; treat as Manalapan.

## Additional corrections found during the audit (documents beat the matrix)
4. **R-24253 Berkoff (PBG "Metal+Flat")** — actually **Shingle + Flat**, per the real
   permit documents. ⇒ **Palm Beach Gardens has NO real approved Metal example.**
5. **R-24352 Houmes (Plantation "Metal")** — actually a **Shingle** reroof (Owens
   Corning NOA 21-0518.04) on its 76-page approved permit. ⇒ **Plantation has NO real
   approved Metal example.**
6. **R-26051 Telford wrong-system flag** — ⚠ **NOT a build.py bug.** The file
   `Zach_Telford_Greenacres_Tile_2ply_Permit_Packet.pdf` is a genuine, *correctly built*
   Greenacres Tile packet that was **misfiled** onto the PBC Metal job — a filing mix-up
   between two Telford jobs. (A separate, real wrong-system defect does exist: see
   `audit_reports/00_CONSOLIDATED_AUDIT.md` C2 — when a system's county form is missing,
   the builder attaches the Tile form instead.)

**Evidence-quality note:** only 4 folders hold a genuinely complete submittal
(R-26019 ×12, R-24352 ×21, R-25183 ×11, R-24253 ×10). Several others hold a single
incidental document (scanned deed, bare NOC, county issuance printout) — form-for-form
fidelity can only be proven where a full baseline exists.

➡ **Full findings: `audit_reports/00_CONSOLIDATED_AUDIT.md`** (+ 5 per-AHJ reports).
