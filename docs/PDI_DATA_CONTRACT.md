# PDI Export — Data Contract (Phase 2 Reverse Engineering)

Produced from 17 additional real accepted EDI files (beyond the original 3
used to build the formatter) plus 5 photographed supplier invoices and one
sample of our own system's OCR/extraction output. This document is the
Phase 1 deliverable of the PDI/EDI compatibility effort: a field-by-field
account of what the ground truth proves, what it contradicts in the
current formatter, and what remains genuinely unknown.

**Redaction note:** the source files named real vendor and customer
businesses, real product brand names, and real invoice numbers. None of
that identity is load-bearing for the *encoding* findings below, so it
has been replaced with fictional stand-ins throughout this document
(`Acme Distribution Co` as the wholesale vendor, `Northgate Grocery` as
the receiving store, generic item labels like "Item A"). The numeric
codes, digit positions, and dollar amounts are preserved exactly as
observed — those are the actual evidence.

**Method note, stated up front:** none of the newly supplied files form a
matched (photographed invoice) → (accepted EDI) pair. The 5 invoice
photos are Northgate Grocery receiving from several different regional
beverage/grocery suppliers, on invoice numbers that do not appear in any
of the 17 EDI ground-truth files, which are themselves a *different*
Acme Distribution Co → Northgate Grocery delivery stream (cigarettes,
candy, snacks, tobacco accessories). So this analysis is **cross-file
structural reverse engineering of the EDI side alone** — extremely
strong for recovering the EDI's internal encoding rules (comparing the
same item code across many invoices), but it cannot confirm that our
OCR/extraction pipeline produces the correct *input* values for those
rules, because no invoice/EDI pair exists in this batch to check that
against.

---

## 0. Two structurally distinct ground-truth formats were supplied

**Format A ("AMOUNT")** — 11 files, all Acme Distribution Co → Northgate
Grocery, e.g. `sample-01.txt`, `sample-02.txt`. This is the format the
current formatter targets and was built from.

**Format B ("AHLA")** — 6 files, e.g. `store-warehouse-sample-04.txt`.
Structurally incompatible with Format A:

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
of quantity — e.g. one small snack-bag item ("Item A") shows `cost_tail =
00179001` at delivered quantities of 1, 1, 1, and 4, with zero variation.
Across all 908 records, not one shows `cost_tail` scaling with quantity.

What **does** change `cost_tail`: the invoice date. 32 items appear with
two or more distinct `cost_tail` values, and every one of those splits
lines up with a different source invoice (different pricing period), not
a different quantity.

**What `cost_tail` actually looks like, decomposed:**
- `[62:67]` (5 digits): a plausible price in cents. One item whose
  description literally prints a "2 for $2" promo price ("Item B")
  decodes to exactly `$2.00`. Dozens of other items decode to plausible
  per-unit retail prices ($1.79, $2.99, $7.59, $12.09, $25.29 for a
  2-gallon gas can, etc.).
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
invoice. What `cost_tail` decodes to (confirmed via the "2 for $2" match)
is closer to a **retail/shelf price**, which is not printed on a
wholesale supplier invoice at all. **This is product-master data that
lives in PDI's own database, keyed by item code — not something
extraction can produce from a photographed invoice, no matter how good
OCR gets.**

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

   | Sample file | Carton qty | CPPT | $/carton |
   |---|---|---|---|
   | sample-03.txt | 27 | $337.50 | **$12.50** |
   | sample-01.txt | 47 | $587.50 | **$12.50** |
   | sample-11.txt | 35 | $437.50 | **$12.50** |
   | sample-04.txt | 39 | $437.50 | $11.22 |
   | sample-05.txt | 29 | $325.00 | $11.21 |
   | sample-06.txt | 43 | $500.00 | $11.63 |
   | sample-07.txt | 24 | $275.00 | $11.46 |
   | sample-08.txt | 31 | $350.00 | $11.29 |

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

## 3. Side finding: two $0.00 line items — corrected root cause

**Original assessment (this section, as first written) was wrong.**
Comparing our own system's TXT export for one processed invoice against
the photographed picklist for that same invoice initially looked like an
OCR/AI extraction failure: two items showed Unit Price/Line Total
$0.00 where the picklist showed real prices ($36.00/$37.20 and
$18.96/$18.96).

**Direct inspection of the real database record's `raw_extraction_json`
disproved this.** The LLM's actual output for both items was
`unit_price: null, line_total: null, confidence: 0.5` — exactly the
correct behavior for an illegible field per the extraction prompt's own
rules. Normalization correctly preserved `None`. The deterministic
`LINE_ITEM_MATH` validation check correctly fired a `WARNING` on both
items ("missing quantity, unit price, or line total; math not
verifiable") before persistence, contributing to the invoice's low
composite confidence and correct `REVIEW_REQUIRED` routing.

**The actual defect is in persistence, not extraction:**
`app/repositories/invoice_repository.py` (constructing `InvoiceItem`)
coerces `None → Decimal("0")` for `quantity`, `unit_price`, and
`line_total`, because those columns are `NOT NULL` on
`InvoiceItem`. This silently converts a correctly-flagged "unknown"
value into a confident-looking `$0.00` — indistinguishable from a
genuinely free item — in the persisted row and therefore in every
export (JSON/TXT/CSV/PDI) and any later view of the invoice. The
granular per-item `WARNING` that correctly diagnosed the problem is
never persisted anywhere; only the coarse `composite_confidence` /
`status` survive past the initial processing response.

Every other field checked against the real picklist for this invoice —
vendor name, dates, subtotal, discount, grand total, and UPCs on every
legible line — matched exactly. Extraction was not the problem here.

This is upstream of the PDI formatter (out of this phase's scope) but
directly relevant to the following milestone's extraction-quality work,
where it's addressed as the primary finding rather than "extraction
improvement."

---

## 4. Root cause summary

| Gap | Root cause | Formatter-only fix? |
|---|---|---|
| Cost block/tail wrong values | **Missing data** — fields encode product-master info (retail price, case pack, a secondary code) we don't capture, not a calculation we got wrong | No — no formatter change can produce data we don't have |
| CPPT wrong value | **Missing data** — a cigarette-specific excise calculation, not `tax_amount` | No — requires new classification data |
| CFUE not implemented | **Missing business input** — flat constant, needs confirmation | Yes, once the constant is confirmed |
| CTAX not implemented | **Insufficient data** — only 3 samples, formula unknown | Unknown — needs more samples |
| Format B (AHLA) unsupported | **Scope question** — may be a different system entirely | N/A — business decision first |
| Two $0.00 line items on the sample invoice | **Persistence-layer coercion** (`None → Decimal("0")` forced by NOT NULL columns), not an extraction failure — extraction and validation both worked correctly | No — different layer entirely, out of scope here |

---

## 5. Implementation plan — status

The plan below was proposed in this phase and has since been **applied**
(see the "revert cost/CPPT calculations" milestone): cost block/tail and
the CPPT trailer were reverted to honest placeholders rather than
continue emitting values this analysis disproved. Recorded here for
history.

1. Revert `_pdi_cost_block` / `_pdi_cost_tail` from `unit_price ×
   quantity` back to a documented, honestly-uncertain placeholder — done.
2. Revert `_pdi_trailer_lines`'s `CPPT = invoice.tax_amount` mapping —
   done.
3. Leave `CFUE`, `CTAX`, and Format B entirely unimplemented — done (no
   change proposed).
4. Update `docs/PDI_OPEN_QUESTIONS.md` — done.
5. Add regression tests pinning the reverted, non-fabricating behavior —
   done.
6. Full test suite green, no regressions — confirmed.

**Not proposed, and why (still holds):**
- Implementing a "case pack size" or "retail price" field — would
  require a new extraction/schema capability with no confirmed source on
  a generic supplier invoice; a scope expansion, not a formatter fix.
- Implementing `CFUE` as a hardcoded `$12.45` — technically trivial, but
  fabricating a business constant without confirmation violates the same
  "don't guess" principle as everything else in this document.
- Any Format B (AHLA) work — scope not yet confirmed.
- Fixing the two OCR $0.00 extraction misses — a different layer,
  picked up separately as Priority 1 in the following milestone rather
  than folded into this formatter-focused analysis.

---

## 6. First real PDI import attempt — line-ending fix

A generated file was uploaded to a live PDI account through "Upload EDI
File" (no format selector — a plain file picker) and rejected outright
with a generic *"file format was not right one"* error. No byte offset
or field name was given — a coarse, structural rejection, not a
field-level one.

**Root cause, confirmed directly against the original attachment
bytes** (not a re-derived copy): every real ground-truth file — both
Format A and Format B, all 18 files — uses `\r\n` (CRLF) line endings
throughout, including the trailer records and the final line. Our
formatter emitted plain `\n` (LF) only. This is a classic rejection
cause for legacy fixed-width import systems that expect DOS/Windows-
style text.

**Fixed:** `build_pdi_export` now joins records with `\r\n` and ends the
file with `\r\n`. This is a transport/encoding-level change, orthogonal
to the still-open content questions in §2 (cost fields, CPPT/CFUE) — it
does not touch any of the placeholder logic. Confirmed working end to end
through the live backend against a real invoice.

**Not yet confirmed:** whether this alone resolves the real PDI
rejection, or whether it was masking a second, content-level issue. The
next real import attempt is the only way to know.

## 7. First real invoice imported end to end — 15 Sep 2026 (P0)

A.L. George / Onondaga Bev invoice **1000540** (photo `IMG_6473.jpg`; 4
lines, per-line deposits, no discount) went OCR → structuring →
store-scoped reference matching → four case-mapping proposals approved by
`data-team:shashwat` → deterministic export → manual PDI import. The file
(323 bytes, CRLF, SHA-256 `72b6bfe5…386fe15`, audit 31/31) was **accepted
without manual editing** and PDI showed every item exactly as emitted:

| UPC (EDI) | Qty | Units/case | Case Cost | PDI displayed |
|---|---|---|---|---|
| 07199048024 | 4 | 12 | 14.50 | 14.50 (previous cost 15.10) |
| 07199047712 | 6 | 12 | 9.00 | 9.00 |
| 08066095757 | 1 | 2 | 31.05 | 31.05 |
| 08066095680 | 1 | 1 | 21.65 | 21.65 |

Two facts settled by this import, both now under regression test:

1. **PDI's Invoice Total is the detail economics.** Header `AMOUNT`
   carried the printed Invoice Total ($172.80 = $164.70 goods + $8.10
   deposits); PDI displayed **$164.70**, raised no balance warning, and
   showed no deposit. Deposits and fuel are outside PDI's merchandise
   total. The header mapping is unchanged by decision (see Q7).
2. **A cost error PDI cannot see.** The model read `unit_price` from a
   `NET` column that is PRICE + DEP; every line balanced, validation
   passed, and the file would have loaded deposit-inclusive costs. Fixed
   deterministically by reconciliation Rule D (invoice-level proof from
   the printed subtotal and deposit total) and, as the cause, by prompt
   v4 ("NET is ambiguous; unit_price never includes the deposit").

**What this import did not settle:** G1, gross vs net case cost — the
invoice had no discount, so gross = net. A discounted invoice is next.
