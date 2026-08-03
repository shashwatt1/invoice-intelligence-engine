# PDI Export — Data Contract (Phase 2 Reverse Engineering)

Produced from 17 additional real accepted EDI files (beyond the original 3
used to build the formatter) plus 5 photographed supplier invoices and one
sample of our own system's OCR/extraction output. This document is the
Phase 1 deliverable of the PDI/EDI compatibility effort: a field-by-field
account of what the ground truth proves, what it contradicts in the
current formatter, and what remains genuinely unknown.

**Method note, stated up front:** none of the newly supplied files form a
matched (photographed invoice) → (accepted EDI) pair. The 5 invoice photos
are Apple Food & Grocery receiving from Rocco J. Testani / PepsiCo /
Coca-Cola / Balkan Beverage on invoice numbers that do not appear in any
of the 17 EDI ground-truth files, which are themselves a *different*
Balkan Beverage → Apple Food Mart delivery stream (cigarettes, candy,
snacks, tobacco accessories). So this analysis is **cross-file structural
reverse engineering of the EDI side alone** — extremely strong for
recovering the EDI's internal encoding rules (comparing the same item
code across many invoices), but it cannot confirm that our OCR/extraction
pipeline produces the correct *input* values for those rules, because no
invoice/EDI pair exists in this batch to check that against.

---

## 0. Two structurally distinct ground-truth formats were supplied

**Format A ("AMOUNT")** — 11 files, all Balkan Beverage LLC → Apple Food
Mart, e.g. `992990.txt`, `933133.txt`. This is the format the current
formatter targets and was built from.

**Format B ("AHLA")** — 6 files, e.g. `Store-302165 Invoice-3173409
(1).txt`. Structurally incompatible with Format A:

| | Format A (AMOUNT) | Format B (AHLA) |
|---|---|---|
| Header keyword | `AMOUNT` | `AHLA` |
| Header length | 34 chars | 38–39 chars (inconsistent across samples) |
| Detail line length | 70 chars | 76 chars |
| Detail line fields | item code, desc, cost block, sign, qty, cost tail | item code, desc, **UPC (12 digit)**, **unit code (`UN`/`EA`)**, qty(6), sign, amount(18) |
| Tax representation | separate `CPPT` trailer record | a literal detail line: `NYS CIG PREPAID SALES TAX` |
| Fuel charge | `CFUE` + `FUEL SURCHARGE`, always `$12.45` | `CFUEL CHARGE` (different literal string), always `$13.50` |
| Extra record types | none beyond CFUE/CPPT/CTAX | `ALLOWANCE` (rebate) lines not seen in Format A at all |

**This is almost certainly a different downstream system, or at minimum
a materially different store/route configuration** — not a formatting
variance our current formatter could accommodate. Per the explicit
instruction to avoid expanding scope, **Format B is flagged as a
business question, not an engineering task**: do we need to support it
at all, and if so, is it actually "PDI," or a second, separate target
system? No implementation work is proposed for it in this document.

Everything below concerns Format A only, which is the one the frozen
formatter (`build_pdi_export`) currently targets.

---

## 1. Fields CONFIRMED correct — no change needed

These match what was already established in the prior milestone and are
reconfirmed by the larger sample (908 detail lines across 11 files):

| Field | Position | Rule | Confidence |
|---|---|---|---|
| Record type | `[0]` | `"B"` for every detail line | Certain (908/908) |
| Item code | `[1:12]` | 11-digit, UPC-12 check-digit dropped, zero-padded; blank → `"00000" + 6 spaces` | High (established in prior milestone) |
| Description | `[12:37]` | 25 chars, left-justified, space-padded/truncated | Certain |
| Sign | `[57]` | `+`/`-`, matches delivery vs. return | Certain |
| Quantity | `[58:62]` | 4-digit zero-padded delivered quantity | Certain |
| Header format | `AMOUNT {batch:7}   {date:6}{sign}{amount:9}` | | Certain |
| Detail record width | 70 chars, always | | Certain (908/908) |

## 2. Fields CORRECTED by this analysis — hard evidence contradicts the current implementation

### 2.1 Cost block (`[37:57]`, 20 digits) and cost tail (`[62:70]`, 8 digits)

**Current implementation** (from the last milestone's business rule):
`cost_block` = unit cost in cents, `cost_tail` = unit cost × quantity in
cents ("extended cost").

**This is disproven by the data.** Tracked 59 items that appear ≥3 times
across different invoices with *different delivered quantities*. In every
single case, `cost_tail` (and `cost_block`) stayed **constant** regardless
of quantity — e.g. `TURKEY CRK OLD FASHION 2OZ` shows `cost_tail =
00179001` at delivered quantities of 1, 1, 1, and 4, with zero variation.
Across all 908 records, not one shows `cost_tail` scaling with quantity.

What **does** change `cost_tail`: the invoice date. 32 items appear with
two or more distinct `cost_tail` values, and every one of those splits
lines up with a different source invoice (different pricing period), not
a different quantity.

**What `cost_tail` actually looks like, decomposed:**
- `[62:67]` (5 digits): a plausible price in cents. `GURLEY 2/$2 SMARTIES`
  — whose description literally prints "2/$2" — decodes to exactly
  `$2.00`. Dozens of other items decode to plausible per-unit retail
  prices ($1.79, $2.99, $7.59, $12.09, $25.29 for a 2-gallon gas can,
  etc.).
- `[67:70]` (3 digits): **`"001"` in all 908 records, without exception.**
  A fixed suffix of unconfirmed meaning (candidate: a unit-of-measure or
  record-subtype flag).

**What `cost_block` looks like, decomposed** (checked against 24 items
with multiple observed values):
- `[37:45]` (8 digits): **constant per item, even across price changes**
  — never varies for a given item code in this sample. Likely a
  secondary product code; not present anywhere in our schema.
- `[45:51]` (6 digits): changes in lockstep with `cost_tail` whenever the
  item's price changes, but no clean arithmetic relationship to
  `cost_tail`'s price was found. **Left open** — flagged, not guessed.
- `[51:57]` (6 digits): matches known real-world case-pack sizes exactly
  (1, 5, 6, 8, 9, 10, 12, 18, 24, 36, 50, 100, 500) and — like the first
  8 digits — stays constant across price changes for the same item.
  High-confidence read: **case/pack quantity**, a static product
  attribute.

**The central finding:** none of this — retail price, a second product
code, case-pack size — is data our system captures. `item.unit_price` in
our database is the wholesale price the store paid, as printed on their
invoice. What `cost_tail` decodes to (confirmed via the "2/$2" match) is
closer to a **retail/shelf price**, which is not printed on a wholesale
supplier invoice at all. **This is product-master data that lives in
PDI's own database, keyed by item code — not something extraction can
produce from a photographed invoice, no matter how good OCR gets.**

### 2.2 CPPT trailer ("PREPAID SALES TAX")

**Current implementation:** `CPPT` amount = `invoice.tax_amount`.

**This is very likely wrong**, for two independent reasons:

1. In Format B (AHLA), the equivalent line is spelled out explicitly as
   `NYS CIG PREPAID SALES TAX` — a **New York cigarette excise
   prepayment**, not a general sales tax.
2. Tested the hypothesis "`CPPT` = a flat per-carton rate × count of
   cigarette-carton line items" (cigarette cartons identified via the
   case-pack-size finding above, pack size = 10) across all 9 blocks that
   carry a `CPPT` trailer:

   | File | Carton qty | CPPT | $/carton |
   |---|---|---|---|
   | 933133.txt | 27 | $337.50 | **$12.50** |
   | 992990.txt | 47 | $587.50 | **$12.50** |
   | 992990 (1).txt | 35 | $437.50 | **$12.50** |
   | 992990 (2).txt | 39 | $437.50 | $11.22 |
   | 933133 (2).txt | 29 | $325.00 | $11.21 |
   | 901587 (2).txt | 43 | $500.00 | $11.63 |
   | 933130 (1).txt | 24 | $275.00 | $11.46 |
   | 933130.txt | 31 | $350.00 | $11.29 |

   Three of eight land on *exactly* $12.50/carton; the rest cluster
   $11.2–$11.6/carton rather than matching `invoice.tax_amount` (which we
   don't even have for these files — no matched invoice exists). This is
   a strong, non-random signal that `CPPT` is a **per-carton cigarette
   excise calculation**, not a straight copy of a generic tax total —
   but the exact formula (rate table probably varies by brand/tax
   category) isn't fully solved.

**Neither finding is implementable today**: computing a real `CPPT` would
require classifying which line items are cigarette cartons and at what
excise rate — data our extraction schema does not capture and which the
generic supplier invoice may not even print.

### 2.3 CFUE trailer ("FUEL SURCHARGE") — reclassified, not wrong

`CFUE` is `+00001245` ($12.45) in **all 9 Format-A samples**, with zero
exceptions — and a different but equally constant `$13.50` in Format B.
This is not derived from invoice content at all; it reads as a flat
per-delivery/route fee. Nothing to correct (we don't currently emit it),
but it reframes Q4: if ever implemented, it should be a **configured
constant**, not a calculation — and confirming that constant (and
whether it's route- or contract-specific) is a business question, not an
engineering one.

### 2.4 New record type: `CTAX`

A third trailer type, `CTAX` / `SALES TAX`, appears 3 times in the
sample, always for small amounts (`$1.96`, `$0.32`, `$1.96`). Not
previously known to the formatter. Likely a genuine general sales tax,
distinct from `CPPT`'s cigarette-specific excise. Not enough samples to
derive a formula. New open item, not implemented.

---

## 3. Side finding: two concrete OCR/AI extraction errors (upstream of the formatter)

Comparing `invoice_3712823.txt` (our system's own TXT export, supplied in
this batch) against the photographed picklist for that same invoice
(Balkan Beverage → Apple Food Mart #07, `Invoice# 3712823`):

| Item | Our extraction | Real picklist |
|---|---|---|
| `RB PINK BRY 2 24/8.4OZ CN` (line 4) | Unit Price **$0.00**, Line Total **$0.00** | D.PRICE **$36.00**, EXT **$37.20** |
| `NESQ MILK 12/14 CHOCOLAT` (line 17) | Unit Price **$0.00**, Line Total **$0.00** | D.PRICE **$18.96**, EXT **$18.96** |

Both are real, verifiable extraction failures (correctly resulted in this
invoice landing in `REVIEW_REQUIRED` at 75.9% confidence — the validation
engine did its job). This is upstream of the PDI formatter and out of
this phase's scope per the explicit instruction not to touch OCR/AI
extraction unless required for EDI compatibility — noted here for
awareness, not proposed for a fix.

---

## 4. Root cause summary

| Gap | Root cause | Formatter-only fix? |
|---|---|---|
| Cost block/tail wrong values | **Missing data** — fields encode product-master info (retail price, case pack, a secondary code) we don't capture, not a calculation we got wrong | No — no formatter change can produce data we don't have |
| CPPT wrong value | **Missing data** — a cigarette-specific excise calculation, not `tax_amount` | No — requires new classification data |
| CFUE not implemented | **Missing business input** — flat constant, needs confirmation | Yes, once the constant is confirmed |
| CTAX not implemented | **Insufficient data** — only 3 samples, formula unknown | Unknown — needs more samples |
| Format B (AHLA) unsupported | **Scope question** — may be a different system entirely | N/A — business decision first |
| Two $0.00 line items on invoice 3712823 | **OCR/AI extraction failure**, not a formatter issue | No — different layer entirely, out of scope here |

---

## 5. Implementation plan (smallest possible change, pending approval)

Only the formatter is touched. No schema changes, no new extraction
fields, no architecture changes, no Format B support.

1. **Revert `_pdi_cost_block` / `_pdi_cost_tail`** from `unit_price ×
   quantity` back to a documented, honestly-uncertain placeholder. We now
   have *evidence* this calculation is wrong, not just "unconfirmed" —
   continuing to emit a confident-looking wrong number is worse than the
   zero-value placeholder it replaced. Isolate the change entirely inside
   these two functions, per the existing pattern.
2. **Revert `_pdi_trailer_lines`'s `CPPT = invoice.tax_amount` mapping.**
   Same reasoning: evidence points to a cigarette-excise calculation we
   cannot currently perform, not a straight copy of a field we do have.
   Stop emitting `CPPT` until a real source is confirmed, rather than
   emitting a plausible-looking but evidenced-wrong number.
3. **Leave `CFUE`, `CTAX`, and Format B entirely unimplemented** — each is
   a business question (confirm a constant, gather more samples, decide
   scope) rather than an engineering gap.
4. **Update `docs/PDI_OPEN_QUESTIONS.md`** to replace the "calculation
   confirmed, digit-layout open" framing of Q1 with the corrected
   understanding above, and to add Q5 (CPPT is not tax_amount) and Q6
   (Format B / AHLA scope question).
5. **Add regression tests** pinning the new behavior: cost fields no
   longer scale with quantity/unit_price (since that relationship is now
   known to be false), and `CPPT` is no longer emitted from
   `tax_amount`.
6. Run the full test suite, confirm no regressions, report line-by-line
   change list.

This plan touches `app/services/export_service.py` (two functions
reverted) plus its two test files and `docs/PDI_OPEN_QUESTIONS.md`.
Nothing else in the backend, frontend, or schema changes.

**Not proposed, and why:**
- Implementing a "case pack size" or "retail price" field — would
  require a new extraction/schema capability with no confirmed source on
  a generic supplier invoice; a scope expansion, not a formatter fix.
- Implementing `CFUE` as a hardcoded `$12.45` — technically trivial, but
  fabricating a business constant without confirmation violates the same
  "don't guess" principle as everything else in this document.
- Any Format B (AHLA) work — scope not yet confirmed.
- Fixing the two OCR $0.00 extraction misses — different layer, not
  requested, and already correctly caught by the validation gate.

Waiting for approval before making any of the above changes.
