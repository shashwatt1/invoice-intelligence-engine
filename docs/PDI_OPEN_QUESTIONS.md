# PDI Export — Open Business Questions

The PDI formatter (`app/services/export_service.py`, `build_pdi_export`)
produces a fixed-width file reverse-engineered from three real PDI import
files. Some fields are confirmed correct by direct byte-level comparison
against those files; others have no known encoding and are deliberately
left as documented placeholders rather than guessed values.

Each unresolved field is isolated behind its own small function
(`_pdi_cost_block`, `_pdi_cost_tail`, `_pdi_trailer_lines`) so that
confirming an answer below means changing exactly one function — nothing
else in the formatter, the API, or the frontend needs to change. As of
this milestone, every record type real PDI files use (header, detail,
trailer) is structurally implemented with a confirmed byte layout; what
remains open is narrower — specific digit scaling and one missing data
source, not missing record types.

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

**Resolved in part:** the trailer record's byte LAYOUT is confirmed — all
three ground-truth files end with the same 38-char shape: `C` + 3-char
subtype code + 25-char label + sign + 8-digit cents (`_pdi_trailer_line()`).
The formatter now emits a real `CPPT` (prepaid sales tax) trailer whenever
`invoice.tax_amount` is present and non-zero — using data this system
already captures, not a new extraction field.

**What's still unknown:**
1. Whether `invoice.tax_amount` (our only invoice-level tax figure) is the
   same figure PDI's `CPPT` expects, or something more specific (e.g.
   excluding certain tax types).
2. `CFUE` (fuel surcharge) — no source field exists anywhere in extraction
   today (not deposit, not fuel surcharge), so it is never emitted. One of
   your own supplier invoices shows a $5.00 fuel surcharge as its own
   line; if PDI reconciles a file's total against its detail + trailer
   lines, an import missing this could fail a totals check even when
   every item line is correct.

**Affects:** `_pdi_trailer_lines()` (content), `_pdi_trailer_line()`
(layout — confirmed, unaffected).

**Can development continue without it?** Yes. The `CPPT` trailer is now
populated with real, non-fabricated data where available. Resolving
`CFUE` remains a data-capture gap, not a formatter gap — it requires
adding fuel-surcharge capture at extraction time (similar to how
`product_code` was added), which is out of this milestone's scope.

**What would resolve it:** confirmation that `tax_amount` maps to `CPPT`
(or the correct source if not), plus a business decision on whether
fuel-surcharge capture is worth adding to extraction.

---

## Summary

| # | Question | Status | Blocks item/qty accuracy? | Formatter-only fix? |
|---|---|---|---|---|
| Q1 | Cost/price digit layout | Calculation resolved; layout open | No | Yes |
| Q2 | Batch number semantics | RESOLVED | No | Yes |
| Q3 | Return/credit sign | RESOLVED | No | Yes |
| Q4 | Fuel surcharge / tax trailers | Layout resolved; CPPT content live, CFUE open | No | CPPT: done. CFUE: no — needs extraction schema change |

None of the remaining open items block the parts of the export that most
directly reduce manual entry today (which item, how many, and now cost).
The full record structure (header, detail, trailer) is now in place and
byte-layout-confirmed end to end — what remains open is verifying exact
digit scaling (Q1) and the `CFUE` data gap (Q4), both isolated to a single
named function each.
