# Product Master — Commercial Conflicts

**Status: READ-ONLY. Nothing was resolved.** Each entry below is a decision waiting for a person; the system deliberately holds no multiplier for any of them, and approving one requires an explicit interpretation through the review workbench.

Conflicts open: **4**

## `018200967214` (PDI item `01820096721`)

- **Store**: `51e39a69` (physical identity unresolved)
- **Current state**: `CONFLICT`, multiplier `None`, `REVIEW_REQUIRED`
- **Evidence interpretation**: `EVIDENCE_SUPPORTS_SINGLE_INTERPRETATION`

**Conflicting assertions**

| Units | Source | Sheet | Row |
| --- | --- | --- | --- |
| 1 | Sheet1 units/case column or unit-cost divisor | Sheet1 | 60 |

**Legacy mapping (current EDI authority)**

| Units/case | Source | Description |
| --- | --- | --- |
| 1 | APPROVED | MICHELOB ULTRA C-18 12OZ |
| 18 | APPROVED | MICHELOB ULTRA C-18 12OZ |
| 1 | VERIFIED_FROM_INVOICE | ULTRA 18 PACK CANS |

**Physical pack composition**: contains 18 unit(s). This is packaging, not the PDI multiplier, and must not be used to resolve the conflict.

**Why the system called it a conflict**: The reference data asserts one count, but an existing governed mapping holds a different value, so the commercial question is open.

**Decision required**: Decide whether the governed mapping or the distributor statement describes how this store accounts for the product in PDI.

## `087692000570` (PDI item `08769200057`)

- **Store**: `51e39a69` (physical identity unresolved)
- **Current state**: `CONFLICT`, multiplier `None`, `REVIEW_REQUIRED`
- **Evidence interpretation**: `EVIDENCE_SUPPORTS_SINGLE_INTERPRETATION`

**Conflicting assertions**

| Units | Source | Sheet | Row |
| --- | --- | --- | --- |
| 1 | Monarch Package units/case column or unit-cost divisor | Monarch Package | 190 |

**Legacy mapping (current EDI authority)**

| Units/case | Source | Description |
| --- | --- | --- |
| 1 | APPROVED | TWISTED TEA HALF & HALF C-18 12OZ |
| 18 | APPROVED | TWISTED TEA HALF & HALF C-18 12OZ |

**Why the system called it a conflict**: The reference data asserts one count, but an existing governed mapping holds a different value, so the commercial question is open.

**Decision required**: Decide whether the governed mapping or the distributor statement describes how this store accounts for the product in PDI.

## `652682012217` (PDI item `65268201221`)

- **Store**: `51e39a69` (physical identity unresolved)
- **Current state**: `CONFLICT`, multiplier `None`, `REVIEW_REQUIRED`
- **Evidence interpretation**: `EVIDENCE_SUPPORTS_MULTIPLE_INTERPRETATIONS`

**Conflicting assertions**

| Units | Source | Sheet | Row |
| --- | --- | --- | --- |
| 12 | Monarch Package units/case column or unit-cost divisor | Monarch Package | 36 |
| 24 | Monarch Package units/case column or unit-cost divisor | Monarch Package | 59 |
| 12 | Monarch Singles units/case column or unit-cost divisor | Monarch Singles | 2 |
| 24 | Monarch Singles units/case column or unit-cost divisor | Monarch Singles | 5 |

**Legacy mapping**: none.

**Why the system called it a conflict**: Two or more distributor statements assert different sellable-unit counts for the same product and store.

**Decision required**: Decide which distributor statement describes this product's commercial unit; the other is either a different pack or a stale row.

## `865024000494` (PDI item `86502400049`)

- **Store**: `51e39a69` (physical identity unresolved)
- **Current state**: `CONFLICT`, multiplier `None`, `REVIEW_REQUIRED`
- **Evidence interpretation**: `EVIDENCE_SUPPORTS_MULTIPLE_INTERPRETATIONS`

**Conflicting assertions**

| Units | Source | Sheet | Row |
| --- | --- | --- | --- |
| 12 | Monarch Package units/case column or unit-cost divisor | Monarch Package | 1764 |
| 24 | Monarch Package units/case column or unit-cost divisor | Monarch Package | 1781 |
| 12 | Monarch Singles units/case column or unit-cost divisor | Monarch Singles | 311 |
| 24 | Monarch Singles units/case column or unit-cost divisor | Monarch Singles | 312 |

**Legacy mapping**: none.

**Why the system called it a conflict**: Two or more distributor statements assert different sellable-unit counts for the same product and store.

**Decision required**: Decide which distributor statement describes this product's commercial unit; the other is either a different pack or a stale row.

