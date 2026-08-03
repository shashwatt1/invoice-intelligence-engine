# PDI Export — Open Business Questions

The PDI formatter (`app/services/export_service.py`, `build_pdi_export`)
produces a fixed-width file reverse-engineered from real PDI import
files: 3 in the original milestone, 18 total as of this update. Some
fields are confirmed correct by direct byte-level comparison against
those files; others have no known encoding and are deliberately left as
documented placeholders rather than guessed values.

Each unresolved field is isolated behind its own small function
(`_pdi_cost_block`, `_pdi_cost_tail`, `_pdi_trailer_lines`) so that
confirming an answer below means changing exactly one function — nothing
else in the formatter, the API, or the frontend needs to change.

**This document was substantially revised after a cross-file analysis of
18 real accepted PDI files disproved two rules that had previously been
implemented as "confirmed" (Q1's cost calculation, Q4's CPPT mapping).**
Both were reverted to safe placeholders. The full evidence trail —
including the exact records compared and the arithmetic behind each
finding — lives in `docs/PDI_DATA_CONTRACT.md`; this document states the
conclusions and what remains open.

**The formatter is frozen pending a real PDI import.** See
`docs/PDI_VALIDATION_CHECKLIST.md` for the checklist to run when that
happens.

---

## Q1 — Detail line cost block/tail (28 digits per line item)

**Corrected, not resolved.** A prior milestone implemented these fields
as `unit_cost` and `unit_cost × quantity` (in cents), per a
business-provided rule. Cross-file analysis of 908 detail lines across
11 real accepted files disproved this: 59 items were observed at
multiple different delivered quantities across different invoices, and
in every case both fields stayed **constant regardless of quantity** —
they only change between pricing periods (different invoice dates).

**What the data does show**, decomposed:
- `cost_tail[0:5]` (5 digits): a plausible price in cents. One item
  whose description literally prints "2/$2" decodes to exactly `$2.00`.
- `cost_tail[5:8]`: a fixed `"001"` suffix in all 908 records, meaning
  unconfirmed.
- `cost_block[0:8]`: constant per item even across price changes — likely
  a secondary product code.
- `cost_block[8:14]`: changes in step with `cost_tail` when price
  changes; no clean formula found.
- `cost_block[14:20]`: matches real case-pack sizes (1, 5, 6, 8, 10, 12,
  24, 36...) and stays constant across price changes — likely case/pack
  quantity.

**Why this is now a data problem, not a digit-layout problem:** none of
retail price, a secondary product code, or case-pack size exists
anywhere in our schema or extraction output, and a wholesale supplier
invoice does not print a retail/shelf price. This looks like
product-master data that lives in PDI's own database, keyed by item
code — not something extraction can ever produce from a photographed
invoice.

**Current state:** `_pdi_cost_block()` / `_pdi_cost_tail()` emit
zero-padded placeholders. Not fabricated.

**Can development continue without it?** Yes — item identification and
quantity are unaffected and correct.

**What would resolve it:** either (a) direct documentation/access from
PDI describing what these fields hold and where that data lives, or (b)
a real supplier invoice paired with its accepted PDI file where an
independent price is known, to back-solve the remaining sub-fields.

---

## Q2 — Header batch/reference number semantics — RESOLVED

**Resolution:** the batch/reference field is populated from the store's
own invoice/reference number (`invoice_number`, as extracted from the
document), not a PDI-assigned sequence number.

**Implementation:** `_pdi_batch_number()` — non-digit characters are
stripped from `invoice_number` and the result is fit to the fixed 7-digit
field (right-aligned, truncating leading digits if longer, zero-padded if
shorter).

**Secondary, minor note (still open, low risk):** the header date's
*format* is confirmed (MMDDYY) but not whether it should be the invoice
date (what we currently use, in `_pdi_date()`) or some other date (e.g.
the date the file is generated).

---

## Q3 — Return / credit invoice handling — RESOLVED

**Resolution:** return/credit invoices use negative amounts. A return is
detected from the invoice's own `grand_total` being negative — no new
data capture was required, since extraction and validation already
preserve whatever sign was printed on the total.

**Implementation:** `_pdi_is_return()` / `_pdi_sign()` — "+" for a normal
invoice, "-" for a return (`grand_total < 0`), applied uniformly to the
header and every detail line. The amount/cost/quantity digit fields
themselves remain magnitude-only (`abs()`); direction is carried solely
by the sign character, matching every real sample file (a file is either
entirely a delivery or entirely a return, never mixed line-by-line).

---

## Q4 — CFUE / CPPT trailer records

**Byte layout resolved:** every real sample ends with the same 38-char
shape: `C` + 3-char subtype code + 25-char label + sign + 8-digit cents
(`_pdi_trailer_line()`).

**Content corrected, not resolved.** A prior milestone populated `CPPT`
from `invoice.tax_amount`. Cross-file analysis disproved this:

1. In a structurally separate format also supplied (`AHLA`, see Q6), the
   equivalent line is spelled out explicitly as `NYS CIG PREPAID SALES
   TAX` — a cigarette excise prepayment, not a general sales tax.
2. Testing "$/cigarette-carton" (cartons identified via the Q1 case-pack
   finding) against all 9 real `CPPT`-bearing invoices: 3 land on exactly
   $12.50/carton, the rest cluster $11.2–$11.6/carton. A real signal, not
   fully solved, but clearly not a copy of a generic tax total.

`CFUE` (fuel surcharge) is `$12.45` in every one of 9 real samples —
constant, not derived from invoice content at all. Reads as a flat
per-delivery or per-route fee.

**Current state:** `_pdi_trailer_lines()` returns `[]` unconditionally.
Neither trailer is emitted. Not fabricated.

**Can development continue without it?** Yes. Neither trailer affects
item/quantity accuracy.

**What would resolve it:** for `CPPT`, either real documentation of the
excise formula or enough matched invoice/EDI pairs with cigarette-carton
detail to fully solve the per-carton rate. For `CFUE`, business
confirmation of the constant (and whether it varies by route/contract).

---

## Q5 — CTAX trailer (new, previously unknown)

A third trailer type, `CTAX` / `SALES TAX`, appears 3 times across the 18
files, always for small amounts (`$1.96`, `$0.32`, `$1.96`). Not
previously known to the formatter and not implemented. Likely a genuine
general sales tax, distinct from `CPPT`'s cigarette-specific excise —
but 3 samples is not enough to derive a formula.

**Can development continue without it?** Yes — amounts observed are
small and infrequent.

**What would resolve it:** more samples, or business documentation of
when `CTAX` applies and how it's calculated.

---

## Q6 — Format B ("AHLA") — is it in scope at all?

6 of the 18 supplied files use a structurally distinct format: header
keyword `AHLA` (not `AMOUNT`), 76-char detail lines (not 70) with an
embedded 12-digit UPC and a `UN`/`EA` unit code, tax as a literal detail
line instead of a trailer, a differently-worded fuel charge at a
different constant ($13.50, not $12.45), and an `ALLOWANCE` (rebate)
record type not seen anywhere in Format A.

This reads as either a different downstream system entirely, or a
materially different store/route configuration — not a variance the
current formatter could reasonably absorb.

**Not implemented, and not proposed for implementation** until this is
answered: is `AHLA` actually PDI, a different target system, or a
different customer/warehouse config? Do we need to support it at all?

---

## Summary

| # | Question | Status | Blocks item/qty accuracy? | Formatter-only fix? |
|---|---|---|---|---|
| Q1 | Cost block/tail | Corrected to placeholder — real cause is missing product-master data | No | No — data doesn't exist in our system |
| Q2 | Batch number semantics | RESOLVED | No | Yes |
| Q3 | Return/credit sign | RESOLVED | No | Yes |
| Q4 | CFUE/CPPT trailer content | Layout resolved; content corrected to placeholder | No | CFUE: yes, once constant confirmed. CPPT: no — needs excise classification data we don't have |
| Q5 | CTAX trailer | New, unimplemented | No | Unknown — too few samples |
| Q6 | Format B (AHLA) scope | Open business question | No | N/A — scope decision first |

None of the open items block the parts of the export that most directly
reduce manual entry today (which item, how many). Q1 and Q4's `CPPT` half
are the two most consequential open items, and both are now understood
to be missing-data problems rather than encoding puzzles — no formatter
change can resolve them without a new data source.
