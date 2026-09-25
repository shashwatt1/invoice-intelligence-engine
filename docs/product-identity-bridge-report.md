# Product Identity Bridge — 11-digit to 12-digit Analysis

**Status: READ-ONLY EVIDENCE. No normalization rule is implemented.** Nothing in the database, the case mappings, the EDI path, extraction or the existing master data was modified. Every 11-digit identifier below keeps its own identity; the reconstructions are candidates for a person to accept or reject.

## 1. The two 11-digit populations

An 11-digit identifier is not one kind of thing. The corpus contains two, they are indistinguishable by length, and **the correct repair is the opposite in each case**.

| | Distributor (scientific notation) | Item Sales (scan code) |
| --- | --- | --- |
| How it is written | Excel float, e.g. `1.8200250002E10` | Text, e.g. `01820000018` |
| What is missing | the **leading zero** | the **check digit** |
| Still present | the check digit | the leading zero |
| Repair | prepend `0` | recompute and append the check digit |
| Wrong repair produces | a valid-looking code for another product | a valid-looking code for another product |

- **scientific_notation** — Distributor sheets store the UPC as a number, and Excel's float form cannot carry a leading zero. The digits are intact but one is missing from the front, so prepending '0' restores a check-digit-valid UPC. The check digit is still present in these values.
- **sales_scan_code** — The sales system stores a PDI-style item code, which is the 12-digit UPC with its check digit removed. The leading zero is present; the trailing check digit is what is missing, so the repair is to recompute and append it. Prepending a zero here performs no better than chance.
- **why_they_must_not_share_one_rule** — Both are 11 digits and neither can be told from the other by length. Applying prepend-zero to a scan code, or append-check to a float-derived value, produces a plausible-looking identifier for a different product.

### Evidence-suggested hypothesis by source population

| Source population | append_check_digit | none | prepend_zero | prepend_zero_unconfirmed |
| --- | --- | --- | --- | --- |
| other | 0 | 0 | 0 | 1 |
| sales_scan_code | 319 | 7158 | 0 | 787 |
| scientific_notation | 0 | 0 | 4 | 739 |

## 2. Evidence strength of every bridge candidate

Strength is decided by evidence outside the identifier itself: whether the reconstruction lands on a UPC that actually exists in the distributor sheets or in the application's identifier tables, and whether the descriptions agree.

| Class | Meaning | Count |
| --- | --- | --- |
| `NO_BRIDGE_EVIDENCE` | neither reconstruction matches anything known | 7158 |
| `STRUCTURAL_ONLY` | padded form is a valid UPC, but no counterpart exists anywhere | 1527 |
| `STRONG` | reconstruction lands on an existing UPC **and** descriptions share brand words | 300 |
| `MODERATE` | lands on an existing UPC, but descriptions do not corroborate it | 13 |
| `WEAK_CONFLICTING_DESCRIPTION` | lands on an existing UPC whose description disagrees — review first | 10 |

### Strongest examples

| 11-digit | Reconstruction | Target UPC-12 | Shared words | Source population |
| --- | --- | --- | --- | --- |
| `01820000063` | append_check_digit | `018200000638` | BUSCH | sales_scan_code |
| `01820000078` | append_check_digit | `018200000782` | ICE|NATURAL | sales_scan_code |
| `01820000115` | append_check_digit | `018200001154` | BUD | sales_scan_code |
| `01820000426` | append_check_digit | `018200004261` | BUSCH | sales_scan_code |
| `01820000769` | append_check_digit | `018200007699` | BUD|LIGHT | sales_scan_code |
| `01820000771` | append_check_digit | `018200007712` | BUDWEISER | sales_scan_code |
| `01820000795` | append_check_digit | `018200007958` | BUD|LIGHT | sales_scan_code |
| `01820000801` | append_check_digit | `018200008016` | BUSCH | sales_scan_code |
| `01820000833` | append_check_digit | `018200008337` | BUD | sales_scan_code |
| `01820000834` | append_check_digit | `018200008344` | BUDWEISER | sales_scan_code |

### Ambiguous cases — 0

None — no 11-digit value in this corpus has both repairs landing on a known UPC.

## 3. Reconciliation of the existing case mappings

A case mapping exists per store, so rows and distinct item codes differ; both are given.

| | Rows | Distinct item codes |
| --- | --- | --- |
| governed case mappings inspected (read-only) | 132 | 96 |
| with an 11-digit item code | 125 | 89 |
| reconstruction lands on a corpus UPC-12 | 55 | 41 |
| **corroborated** (corpus pack size agrees) | 34 | **23** |
| contradicted by the corpus | 1 | — |

#### Pre-existing inconsistency found in the governed mappings

These item codes already carry **different pack sizes in different stores**. That disagreement exists in the application today and is not caused by the bridge — the bridge only made it visible. Nothing here was changed.

| Item code | Governed units-per-case across stores |
| --- | --- |
| `01820096721` | 1, 18 |
| `08769200057` | 1, 18 |

| Outcome | Count |
| --- | --- |
| `no_corpus_evidence` | 77 |
| `agrees` | 34 |
| `bridged_but_no_pack_evidence` | 20 |
| `DISAGREES` | 1 |

### The corroborated mappings — 34 rows, 23 distinct item codes

Independent agreement between a governed mapping and the distributor sheets. This is **not** authority to change anything — the mappings already hold these values and remain untouched.

| Item code | Reconstructed UPC-12 | Governed units | Corpus units | Shared words | Governed description |
| --- | --- | --- | --- | --- | --- |
| `01820000063` | `018200000638` | 4 | 4 | BUSCH | BUSCH 4/6/160Z CAN |
| `01820000115` | `018200001154` | 4 | 4 | BUD | BUD LT 4/6/160Z CAN |
| `01820000769` | `018200007699` | 2 | 2 | BUD | BUD LT 2/12 NR |
| `01820000771` | `018200007712` | 2 | 2 | BUDWEISER | BUDWEISER B-2/12 12OZ |
| `01820000771` | `018200007712` | 2 | 2 | — | BUD 2/12 NR |
| `01820000771` | `018200007712` | 2 | 2 | BUDWEISER | BUDWEISER B-2/12 120Z |
| `01820000801` | `018200008016` | 2 | 2 | BUSCH | BUSCH LT 2/12 CAN |
| `01820008989` | `018200089893` | 3 | 3 | BUD | BUD LT 3/8 162 CAN |
| `01820011047` | `018200110474` | 2 | 2 | — | BUD 2/12 CAN |
| `01820011218` | `018200112188` | 1 | 1 | — | BUD 18 PACK CANS |
| `01820023986` | `018200239861` | 3 | 3 | ULTRA | ULTRA 3/8/16 |
| `01820025000` | `018200250002` | 15 | 15 | BUDWEISER | BUDWEISER C-15 25OZ |
| `01820025000` | `018200250002` | 15 | 15 | BUDWEISER | BUDWEISER C-15 250Z |
| `01820025000` | `018200250002` | 15 | 15 | — | BUD 15/250Z CAN |
| `01820025001` | `018200250019` | 15 | 15 | BUD|LIGHT | BUD LIGHT C-15 250Z |
| `01820025001` | `018200250019` | 15 | 15 | BUD|LIGHT | BUD LIGHT C-15 25OZ |
| `01820025004` | `018200250040` | 15 | 15 | BUSCH|LIGHT | BUSCH LIGHT C-15 25OZ |
| `01820025004` | `018200250040` | 15 | 15 | BUSCH | BUSCH LT 15/250Z CAN |
| `01820025004` | `018200250040` | 15 | 15 | BUSCH|LIGHT | BUSCH LIGHT C-15 25OZ |
| `01820025007` | `018200250071` | 15 | 15 | BUSCH|ICE | BUSCH ICE C-15 250Z |
| `01820025013` | `018200250132` | 15 | 15 | DADDY|NATTY | NATTY DADDY C-15 25OZ |
| `01820025013` | `018200250132` | 15 | 15 | DADDY | NAT DADDY 15/250Z CA |
| `01820025013` | `018200250132` | 15 | 15 | DADDY|NATTY | NATTY DADDY C-15 25OZ |
| `01820053047` | `018200530470` | 2 | 2 | BUD | BUD LT 2/12 CAN |
| `01820061047` | `018200610479` | 2 | 2 | BUSCH | BUSCH C-2/12 12OZ |
| `01820061047` | `018200610479` | 2 | 2 | BUSCH | BUSCH C-2/12 120Z |
| `01820096721` | `018200967214` | 1 | 1 | MICHELOB|ULTRA | MICHELOB ULTRA C-18 12OZ |
| `01820096721` | `018200967214` | 1 | 1 | ULTRA | ULTRA 18 PACK CANS |
| `06206705162` | `062067051623` | 12 | 12 | — | LAB 12/24 OZ CAN |
| `06206738062` | `062067380624` | 12 | 12 | ICE | LAB ICE 12/24 02 CAN |
| `85005919510` | `850059195109` | 12 | 12 | BEATBOX|CHERRY|MALT | BEATBOX MALT CHERRY |
| `85005919529` | `850059195291` | 12 | 12 | BEATBOX|MALT|MYSTIC | BEATBOX MALT MYSTIC |
| `85005919539` | `850059195390` | 12 | 12 | BEATBOX|MALT | BEATBOX MALT BLUEBER |
| `85113300676` | `851133006762` | 12 | 12 | BEATBOX|BLUE|MALT | BEATBOX MALT BLUE RA |

### Contradictions — review before any rule is written

| Item code | Reconstructed UPC-12 | Governed units | Corpus units |
| --- | --- | --- | --- |
| `01820096721` | `018200967214` | 18 | 1 |

## 4. What this does and does not establish

**Establishes**: the two 11-digit populations are real, separable by how the value was written rather than by its digits, and each has a reconstruction supported by independent evidence.

**Does not establish**: that any individual bridge is correct. A reconstruction landing on a real UPC is evidence, not proof — two products can differ in ways no barcode arithmetic will reveal.

**Not done here**: no normalization rule, no identity merge, no change to `product_case_mappings`, `product_identity`, `product_identifier`, extraction or EDI. `AMBIGUOUS` and `WEAK_CONFLICTING_DESCRIPTION` rows must be resolved by a person before any rule is considered.

