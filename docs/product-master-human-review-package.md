# Product Master — Human Review Package

**Status: READ-ONLY. No decision was made and nothing was written.** Every one of the 414 commercial candidates appears below exactly once, grouped by what the evidence produced and whether the existing governed mapping agrees.

The counts are statistics. **A group is not a recommendation** — "legacy agrees" means two sources match, not that a candidate is safe to approve.

Full per-candidate detail: `analysis/master-data/product_master_human_review_package.csv`.

## Groups

| Group | Count | What it means |
| --- | --- | --- |
| `1_READY_LEGACY_AGREES` | 24 | one distributor statement settled it and the governed mapping holds the same value |
| `2_READY_NO_LEGACY_MAPPING` | 386 | one distributor statement settled it; no governed mapping exists either way |
| `3_READY_LEGACY_DISSENTS` | 0 | one distributor statement settled it but the governed mapping differs |
| `4_CONFLICT` | 4 | the evidence produced no multiplier; an explicit decision is required |
| `5_APPROVED` | 0 | a reviewer approved it |
| `6_REJECTED` | 0 | a reviewer rejected it |

## The 410 READY_FOR_REVIEW candidates

- legacy agrees: **24**
- no legacy mapping: **386**
- legacy dissents: **0**

### By commercial basis

| Basis | Count |
| --- | --- |
| `CASE_IS_SELLING_UNIT` | 75 |
| `UNIT_IS_SELLING_UNIT` | 335 |

### By multiplier

| Multiplier | Count |
| --- | --- |
| 1 | 75 |
| 2 | 101 |
| 3 | 11 |
| 4 | 94 |
| 5 | 1 |
| 6 | 43 |
| 12 | 53 |
| 15 | 19 |
| 20 | 1 |
| 24 | 12 |

### By store

| Store | Count |
| --- | --- |
| `51e39a69` | 410 |

### By evidence source

| Source | Count |
| --- | --- |
| Monarch Package units/case column or unit-cost divisor | 267 |
| Sheet1 units/case column or unit-cost divisor | 63 |
| Monarch Package units/case column or unit-cost divisor || Monarch Package units/case column or unit-cost divisor | 22 |
| Sheet2 units/case column or unit-cost divisor | 17 |
| Monarch Package units/case column or unit-cost divisor || Monarch Singles units/case column or unit-cost divisor | 17 |
| Sheet3 units/case column or unit-cost divisor | 15 |
| Monarch Package units/case column or unit-cost divisor || Monarch Package units/case column or unit-cost divisor || Monarch Package units/case column or unit-cost divisor | 4 |
| Monarch Package units/case column or unit-cost divisor || Monarch Package units/case column or unit-cost divisor || Monarch Singles units/case column or unit-cost divisor | 3 |
| Sheet1 units/case column or unit-cost divisor || Sheet1 units/case column or unit-cost divisor || Sheet1 units/case column or unit-cost divisor || Sheet1 units/case column or unit-cost divisor | 1 |
| Monarch Singles units/case column or unit-cost divisor | 1 |

## The 4 conflicts — unresolved

Carried through from the existing dossier unchanged. None was resolved here, and none carries a multiplier.

| Canonical UPC | States | Legacy | Evidence interpretation |
| --- | --- | --- | --- |
| `018200967214` | 1 | 1, 18 | `EVIDENCE_SUPPORTS_SINGLE_INTERPRETATION` |
| `087692000570` | 1 | 1, 18 | `EVIDENCE_SUPPORTS_SINGLE_INTERPRETATION` |
| `652682012217` | 12, 24 | none | `EVIDENCE_SUPPORTS_MULTIPLE_INTERPRETATIONS` |
| `865024000494` | 12, 24 | none | `EVIDENCE_SUPPORTS_MULTIPLE_INTERPRETATIONS` |

Full dossier, including source rows and why each was classified: `docs/product-master-commercial-conflicts.md`.

## What this package does not do

It makes no business decision, approves nothing, and does not mark any group as safe. Approving a candidate remains a reviewer action through the Product Master review workbench, and approving one still changes no EDI output — `product_case_mappings` remains the authority.
