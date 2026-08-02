# PDI Export — Open Business Questions

The PDI formatter (`app/services/export_service.py`, `build_pdi_export`)
produces a fixed-width file reverse-engineered from three real PDI import
files. Some fields are confirmed correct by direct byte-level comparison
against those files; others have no known encoding and are deliberately
left as documented placeholders rather than guessed values.

Each placeholder is isolated behind its own small function
(`_pdi_cost_block`, `_pdi_cost_tail`, `_pdi_batch_number`,
`_pdi_trailer_lines`) so that confirming an answer below means changing
exactly one function — nothing else in the formatter, the API, or the
frontend needs to change.

---

## Q1 — Detail line cost/price encoding (28 digits per line item)

**What's unknown:** the 20-digit "cost block" and 8-digit "cost tail" on
every detail line have no verified structure. We know they exist and
where they sit; we don't know how price, cost, or unit-of-measure data is
packed into them.

**Why it matters:** without this, an imported invoice carries no
cost/price data — every line item's financial value is silently zero in
the imported file, even though the item and quantity are correct.

**Affects:** `_pdi_cost_block()`, `_pdi_cost_tail()` — two functions,
called once per detail line.

**Can development continue without it?** Yes. Item identification and
quantity — the fields that most directly reduce manual re-typing — are
unaffected and already confirmed correct. The placeholder is safe
(zeros, not a fabricated number) and fully isolated.

**What would resolve it:** one real supplier invoice paired with the
actual PDI file your system accepted for that same delivery. With a
known price/cost on one line, the exact byte encoding can be back-solved
with certainty instead of estimated.

---

## Q2 — Header batch/reference number semantics

**What's unknown:** whether the 7-digit number at the start of the
`AMOUNT` line is expected to be derived from the vendor's invoice number,
or is something PDI assigns itself (its own sequence numbers in the
sample files looked assigned, not derived from a vendor invoice number).

**Why it matters:** if PDI expects a specific number it tracks itself,
our current placeholder could collide with an existing batch, get
silently misfiled, or be rejected outright.

**Affects:** `_pdi_batch_number()` — one function, called once per file.

**Can development continue without it?** Yes. This is a single, isolated
header field; every detail line and the rest of the header are
unaffected by whatever the answer turns out to be.

**Secondary, minor note:** the header date's *format* is confirmed
(MMDDYY) but not whether it should be the invoice date (what we currently
use, in `_pdi_date()`) or some other date (e.g. the date the file is
generated). Low risk either way — flagging for completeness, not raising
as a full question.

---

## Q3 — Return / credit invoice handling

**What's unknown:** one supplied ground-truth PDI file is entirely a
return/credit transaction, using `-` throughout instead of `+`. Whether
and how return invoices should flow through this same PDI export path is
unknown.

**Why it matters:** if return invoices need to be imported this way,
they'll currently be exported with the wrong sign — an accounting error,
not just a cosmetic one.

**Affects:** `_pdi_sign()`.

**Can development continue without it?** Yes. Every real invoice
processed by this system to date has been a standard delivery. There is
also no upstream concept of a "credit invoice" anywhere in extraction or
validation today — resolving this would likely require more than a
formatter change, so it's worth confirming whether it's in scope at all
before any implementation work.

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

| # | Question | Blocks item/qty accuracy? | Formatter-only fix? |
|---|---|---|---|
| Q1 | Cost/price encoding | No | Yes |
| Q2 | Batch number semantics | No | Yes |
| Q3 | Return/credit sign | No | No — needs upstream data too |
| Q4 | Fuel surcharge / tax trailers | No | No — needs extraction schema change first |

None of these block the parts of the export that most directly reduce
manual entry today (which item, how many). All four are isolated to a
single named function each, ready to be filled in as soon as an answer
is available.
