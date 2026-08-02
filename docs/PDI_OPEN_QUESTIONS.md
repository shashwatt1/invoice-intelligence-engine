# PDI Export — Open Business Questions

The PDI formatter (`app/services/export_service.py`, `build_pdi_export`)
produces a fixed-width file reverse-engineered from three real PDI import
files. Some fields are confirmed correct by direct byte-level comparison
against those files; others have no known encoding and are deliberately
left as documented placeholders rather than guessed values.

Each unresolved field is isolated behind its own small function
(`_pdi_cost_block`, `_pdi_cost_tail`, `_pdi_trailer_lines`) so that
confirming an answer below means changing exactly one function — nothing
else in the formatter, the API, or the frontend needs to change.

---

## Q1 — Detail line cost/price digit layout (28 digits per line item)

**Resolved in part:** the business rule is confirmed — the cost section
is calculated from the line's unit cost and quantity (`_pdi_unit_cost_cents`,
`_pdi_extended_cost_cents`: unit cost in cents, and unit cost × quantity
in cents). Every detail line now carries real, non-zero cost data.

**What's still unknown:** the exact fixed-width DIGIT LAYOUT the 20-digit
"cost block" and 8-digit "cost tail" pack those calculated values into.
The current encoding (`_pdi_cost_block`, `_pdi_cost_tail`) is a plain
right-justified, zero-padded cents value — a reasonable default, but not
verified against a matched ground-truth PDI file. It's possible PDI
expects a different scale (e.g. whole cents vs. a fixed-decimal format)
or additional sub-fields packed into either block.

**Why it matters:** if the digit layout differs from the current
right-justified-cents assumption, imported cost/price values would be
wrong (not zero, but incorrect) rather than simply missing.

**Affects:** `_pdi_cost_block()`, `_pdi_cost_tail()` — two functions,
called once per detail line. `_pdi_unit_cost_cents()` /
`_pdi_extended_cost_cents()` (the calculation itself) are CONFIRMED and
unaffected.

**Can development continue without it?** Yes. Item identification and
quantity are unaffected. The current encoding is a documented best-effort,
not a placeholder — it should be treated as unverified rather than wrong
until checked against a ground-truth file.

**What would resolve it:** one real supplier invoice paired with the
actual PDI file your system accepted for that same delivery. With a
known price/cost on one line, the exact byte encoding can be back-solved
with certainty instead of estimated.

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

## Q4 — Fuel surcharge / itemized tax trailer records

**What's unknown:** whether fuel surcharge and prepaid-tax line items
(the `CFUE`/`CPPT` trailer records seen in the sample PDI files) need to
be included, and if so, how they should be captured from the source
invoice.

**Why it matters:** these are real, material amounts — one of your own
supplier invoices shows a $5.00 fuel surcharge as its own line. If PDI
reconciles a file's total against its detail + trailer lines, an import
missing these could fail a totals check even when every item line is
correct.

**Affects:** `_pdi_trailer_lines()` — currently a no-op.

**Can development continue without it?** Yes, with one caveat: unlike
Q1–Q3, closing this gap isn't a formatter-only change. It would first
require capturing deposit/fuel-surcharge/tax data at extraction time
(an addition to the extraction schema, similar to how `product_code` was
added) — which was explicitly deferred as its own scope decision in an
earlier milestone, not yet approved.

---

## Summary

| # | Question | Status | Blocks item/qty accuracy? | Formatter-only fix? |
|---|---|---|---|---|
| Q1 | Cost/price digit layout | Calculation resolved; layout open | No | Yes |
| Q2 | Batch number semantics | RESOLVED | No | Yes |
| Q3 | Return/credit sign | RESOLVED | No | Yes |
| Q4 | Fuel surcharge / tax trailers | Open | No | No — needs extraction schema change first |

None of the remaining open items block the parts of the export that most
directly reduce manual entry today (which item, how many, and now cost).
Each is isolated to a single named function, ready to be filled in as
soon as an answer is available.
