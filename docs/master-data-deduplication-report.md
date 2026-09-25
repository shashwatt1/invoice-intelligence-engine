# Master Data Deduplication — Analysis Report

**Status: ANALYSIS ONLY. This dataset is NOT ready to seed.** No database was contacted, no migration exists, and no source workbook was modified. Every number below describes what the reference corpus contains, not a decision about it.

## 1. Files and sheets inspected

| File | Sheet | Source rows |
| --- | --- | --- |
| `data/reference/store_47708760/Beer Inventory.xlsx` | Monarch Frontline | 975 |
| `data/reference/store_47708760/Beer Inventory.xlsx` | Monarch Package | 1840 |
| `data/reference/store_47708760/Beer Inventory.xlsx` | Monarch Rung | 35 |
| `data/reference/store_47708760/Beer Inventory.xlsx` | Monarch Singles | 315 |
| `data/reference/store_47708760/Beer Inventory.xlsx` | Sheet1 | 82 |
| `data/reference/store_47708760/Beer Inventory.xlsx` | Sheet2 | 17 |
| `data/reference/store_47708760/Beer Inventory.xlsx` | Sheet3 | 15 |
| `data/reference/store_47708760/Beer Inventory.xlsx` | Zink - Tiki | 623 |
| `data/reference/store_47708760/Item_Sales_Summary_2026-09-07T16_04_18.998Z.xlsx` | data | 875 |
| `data/reference/store_47708760/Item_Sales_Summary_2026-09-07T16_07_36.325Z.xlsx` | data | 4620 |
| `data/reference/store_47708760/Item_Sales_Summary_2026-09-07T16_10_50.919Z.xlsx` | data | 643 |
| `data/reference/store_47708760/Mckinley-07-24_to_07-26.xlsx` | data | 2294 |
| `data/reference/store_86357232/Item_Sales_Summary_2026-09-14T15_45_30.014Z.xlsx` | data | 4289 |
| `data/reference/store_86357232/Item_Sales_Summary_2026-09-14T15_56_01.537Z.xlsx` | data | 1041 |

Total raw source records: **17664**

## 2. Identifier population

| Identifier kind | Count |
| --- | --- |
| `identifier11` | 15200 |
| `upc12` | 2084 |
| `upc12_check_failed` | 301 |
| `short_code` | 50 |
| `placeholder_zeros` | 14 |
| `missing` | 8 |
| `ean13` | 4 |
| `unresolved_length` | 2 |
| `unresolved_non_numeric` | 1 |

### Length distribution

| Digits | Count |
| --- | --- |
| 1 | 10 |
| 2 | 8 |
| 3 | 6 |
| 4 | 2 |
| 5 | 24 |
| 6 | 1 |
| 8 | 1 |
| 11 | 15200 |
| 12 | 2399 |
| 13 | 4 |
| 15 | 1 |

### Normalizations actually applied

| Flag | Count |
| --- | --- |
| `leading_zero_present` | 8568 |
| `scientific_notation_expanded` | 3177 |
| `leading_zero_indeterminate` | 3177 |
| `internal_spaces_removed` | 469 |
| `hyphen_groups_removed` | 146 |
| `excel_integer_decimal_stripped` | 2 |

Deliberately **not** implemented: `zfill / left-padding of any identifier`, `treating a short number as a UPC`, `manufacturer-28476 five-zero reconstruction`, `automatic merge of id11 into upc12 via check-digit reconstruction`, `description-based deduplication`, `choosing a winning description, pack size, or cost`

## 3. Canonical candidates

- **total**: 10671
- **safe_duplicate**: 3799
- **duplicate_with_conflict**: 1166
- **single_source**: 5341
- **unresolved_identity**: 365
- **exact_duplicate_rows_collapsed**: 6993

## 4. Conflicts requiring human review

| Conflict | Count |
| --- | --- |
| `DESCRIPTION_VARIANTS` | 1165 |
| `MANUFACTURER_CONFLICT` | 112 |
| `ITEM_CODE_CONFLICT` | 63 |
| `PACK_SIZE_CONFLICT` | 2 |

### Highest-risk examples

**Pack-size conflicts** — one product, disagreeing units-per-case. No winner was chosen:

- `upc12:652682012217` — units seen: 12, 24 · descriptions: 3 FLOYDS GUMBALLHEAD C12 19.2OZ | 3 FLOYDS GUMBALLHEAD C24 19.2OZ · sources: 1
- `upc12:865024000494` — units seen: 12, 24 · descriptions: WARPIGS FOGGY GEEZER C12 19.2OZ | WARPIGS FOGGY GEEZER C24 19.2OZ · sources: 1

**Manufacturer conflicts** — same barcode, different supplier product id:

- `id11:34100012427` — ids: 54682, 58240 · LEINENKUGEL CHOCOLATE DUNKEL C24 12OZ 6P | LEINENKUGEL SUNSET C24 12OZ 6P
- `id11:34100013233` — ids: 69308, 62853, 53873, 63939 · LEINENKUGEL WHITE MOCHA STOUT C24 12OZ 6P | LEINENKUGEL RED LAGER C24 12OZ 6P
- `id11:34100013424` — ids: 58536, 56478 · LEINENKUGEL FALL WINTER VARIETY C24 12OZ 12P | LEINENKUGEL LODGE PACK VARIETY C24 12OZ 12P
- `id11:34100013479` — ids: 70371, 11054 · LEINENKUGEL WHITE MOCHA STOUT B24 12OZ 6P | LEINENKUGEL OKTOBERFEST B24 12OZ 6P
- `id11:34100013493` — ids: 70372, 11053 · LEINENKUGEL WHITE MOCHA STOUT B24 12OZ 12P | LEINENKUGEL OKTOBERFEST B24 12OZ 12P

**Description variants** — all preserved as aliases; none promoted to canonical:

- `upc12:638489001883` — 10 descriptions: DOGFISH HEAD SUPER DOPPIO C24 12OZ 6P | DOGFISH HEAD COVERED IN NUGGS C24 12OZ 6P | DOGFISH HEAD MANDARIN AND MANGO C24 12OZ 6P | DOGFISH HEAD CRIMSON CRU C24 12OZ 6P
- `upc12:816021020343` — 10 descriptions: SUN KING MIDNIGHT MONSTER C24 16OZ 4P | SUN KING FOURTH RIDER OF LIGHT C24 16OZ 4P | SUN KING JAVA HOUSE C24 16OZ 4P | SUN KING SCREAMING SPIDERS IPA C24 16OZ 4P
- `upc12:816021020169` — 9 descriptions: SUN KING LAP LANE C24 12OZ 6P | SUN KING SALTED CARAMEL BROWNIE C24 12OZ 6P | SUN KING SCOUT BADGE C24 12OZ 6P | SUN KING SUMMER GLOW C24 12OZ 6P
- `upc12:652682132250` — 8 descriptions: 3 FLOYDS CRUSHING MASS BARREL AGED COFFEE STOUT B24 12OZ 4P | 3 FLOYDS BLACK COLOSSUS B24 12OZ 4P | 3 FLOYDS BARREL AGED COCOMUNGO B24 12OZ 4P | 3 FLOYDS BARREL AGED BEHEMOTH B24 12OZ 4P
- `upc12:754527011109` — 8 descriptions: NEW BELGIUM VOODOO RANGER BLAZE LIGHTNING C24 12OZ 6P | NEW BELGIUM GRAPE FIZZ ALE C24 12OZ 6P | NEW BELGIUM VOODOO RANGER 1985 MANGO IPA C24 12OZ 6P | NEW BELGIUM VOODOO RANGER CASHMERIZE C24 12OZ 6P

## 5. 11-digit vs 12-digit identifiers

- 11-digit: **15200**
- 12-digit: **2399**
- Leading-zero cases preserved: **8568**

`319` 11-digit codes reconstruct (via UPC-A check digit) onto a 12-digit UPC that exists elsewhere in the corpus. These are reported in `possible_matches.csv` as **POSSIBLE_MATCH** and were **not merged** — the PDI 11-digit convention is not applied as a generic deduplication rule.

| 11-digit | Reconstructed UPC-12 | 11-digit description | UPC-12 description |
| --- | --- | --- | --- |
| `01820000018` | `018200000188` | Bud 16oz 6pk cans | BUDWEISER 24/16 CAN 4/6 || BUDWEISER BUDWEIS |
| `01820000063` | `018200000638` | Busch 6pack cans | BUSCH 24/16 CAN 4/6 || BUSCH BUSCH |
| `01820000078` | `018200000782` | Natural ice 6pk cans | NATURAL ICE NATURAL ICE |
| `01820000115` | `018200001154` | Bud lt 16oz 6 cans | BUD LIGHT 24/16 CAN 4/6 || BUD LIGHT BUD LIG |
| `01820000426` | `018200004261` | Busch lt 6pack cans | BUSCH LIGHT 24/16 CAN 4/6 || BUSCH LIGHT BUS |
| `01820000769` | `018200007699` | Bud light 12 Pack btls | BUD LIGHT 24/12 NRLN 2/12 || BUD LIGHT BUD L |
| `01820000771` | `018200007712` | Budweiser 12bottels | BUDWEISER 24/12 NRLN 2/12 || BUDWEISER BUDWE |
| `01820000795` | `018200007958` | Bud light 16oz | BUD LIGHT BUD LIGHT |

### The 11-digit question — two populations, opposite repairs

Both populations are 11 digits. Left-padding one and check-digit-appending the other are the correct repairs; applying either rule generically would corrupt the other population. This is the evidence against a blanket zfill.

| Population | Rows | Prepend `0` passes UPC check | Prepend `0` found in corpus | Append check digit found in corpus |
| --- | --- | --- | --- | --- |
| scientific_notation | 1780 | 1780 (100.0%) | 5 | 0 |
| sales_scan_code | 13419 | 1331 (9.9%) | 0 | 729 |
| other | 1 | 1 (100.0%) | 0 | 0 |

- **scientific_notation** — Excel float representation dropped a leading zero. Prepending '0' restores a check-digit-valid UPC-A at the rate shown — treat as a 12-digit UPC identity once confirmed.
- **sales_scan_code** — Consistent with the PDI convention (12-digit UPC minus check digit). Prepending '0' performs at roughly the ~10% rate random digits would, i.e. no signal; appending the computed check digit lands on UPCs that actually exist in the distributor sheets.

- **False-merge check**: `0` `id11` group(s) mix the two populations. **NO reconstruction rule is applied. This is evidence for review only.**

### 12-digit check-digit outcome by source

| Source type | Outcome |
| --- | --- |
| distributor_price_sheet | {'placeholder_zeros': 14, 'upc12': 2084, 'upc12_check_failed': 8} |
| item_sales_summary | {'upc12_check_failed': 293} |

## 6. Manufacturer 28476 — Identifier Pattern Investigation

- **records_with_28476_anywhere**: 0
- **records_with_manufacturer_id_28476**: 0
- **manufacturer_id_column_present_in**: ['Beer Inventory.xlsx :: Monarch * sheets (Product ID)']
- **manufacturer_id_population**: 3165
- **manufacturer_id_length_distribution**: {5: 3164, 11: 1}
- **manufacturer_id_numeric_range**: {'min': 10035, 'max': 34100008710}
- **28476_within_observed_range**: True
- **conclusion**: No record anywhere in the corpus carries 28476. The rule cannot be designed or validated from this data — the five-zero reconstruction remains UNIMPLEMENTED and unproven.

## 7. Overlap with the master data the application already holds

Read-only counts (see `scripts/compare_master_with_existing.py`). None of these rows were modified, and nothing in this phase proposes changing them.

- **product_case_mappings_distinct_item_codes**: 96
- **product_identity_distinct_item_codes**: 7655
- **product_identifier_rows**: 12176
- **product_identifier_kinds**: {'retail_upc_raw': 9552, 'distributor_item_code': 706, 'unit_upc': 260, 'distributor_product_id': 1658}
- **case_mapping_item_code_lengths**: {'11': 89, '6': 5, '4': 2}

- **case_mappings_found_in_id11_candidates**: 86
- **case_mappings_found_in_upc12_candidates**: 0
- **product_identity_found_in_id11_candidates**: 5950
- **product_identifier_values_in_upc12_candidates**: 82
- **product_identifier_values_in_id11_candidates**: 5927

> The application's canonical item_code lives in the 11-digit space; none of the governed case mappings match a 12-digit UPC directly. Pack-size evidence in the corpus is keyed by 12-digit UPC. The two therefore cannot be joined without resolving the 11-to-12 digit relationship.

If the 11-to-12 digit reconstruction were approved, **23** governed case mappings would gain independent pack-size evidence from the corpus: **23** agree with the governed units-per-case and **0** disagree. *NOT APPLIED. Reported as evidence for human review only.*

## 8. What needs human review

1. Every `DUPLICATE_WITH_CONFLICT` row in `deduplication_conflicts.csv`.
2. Every `POSSIBLE_MATCH` in `possible_matches.csv` — confirm or reject the 11-digit to 12-digit relationship before any rule is written.
3. Pack-size conflicts, which belong in the existing governed case-mapping proposal workflow rather than being resolved here.
4. `unresolved_identifiers.csv` — rows with no trustworthy identifier. None of these were given an identity from their description.

