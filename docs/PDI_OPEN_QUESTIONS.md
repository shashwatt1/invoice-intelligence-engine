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

## Q7 — Must the AMOUNT header balance against the detail lines? — RESOLVED (observed 15 Sep 2026)

**Observed on the first real import.** A.L. George / Onondaga Bev invoice
1000540 (4 lines, deposits per line, no discount, no fuel) was exported
with header `AMOUNT … +000017280` ($172.80, the printed Invoice Total)
and four `B` records whose case cost × quantity sum to $164.70 (the
printed Total Content). PDI accepted the file **without manual editing**,
raised no out-of-balance warning, read every item as emitted, and
displayed **Invoice Total = $164.70** — the detail-line economics, not
the header. The $8.10 of deposits appeared nowhere.

That is the third row of the table below: **PDI's merchandise Invoice
Total follows the B-record detail economics.** The header did not drive
the displayed total and was not rejected. Deposits (and, by the same
logic, fuel) are outside PDI's merchandise total and outside the item
file; they are an accounts-payable matter, not an EDI one.

**Decision: `_pdi_amount_cents` stays as it is** — the header keeps
carrying the supplier's printed Invoice Total. Changing it to the goods
total would alter the Balkan golden (header $273.66) for no observed
benefit, and the printed total is what a clerk reconciles against the
physical invoice. If the manager later wants the header to equal the
merchandise total, that is a one-line formatter change plus a new
Balkan golden — a deliberate decision, not a correction.
`app/services/pdi_audit.py` continues to report the header/detail gap
as information, never as a failure. The golden for 1000540 is pinned in
`tests/integration/test_p0_invoice_1000540.py`.

The original analysis follows for the record.


**This is the highest-value unresolved question in the contract, and it
is not answerable from anything currently in our possession.**

### The observation

For Balkan invoice 3376587 the file we generate today contains:

| | |
|---|---|
| `AMOUNT` header | **$273.66** — the printed Invoice Total |
| Σ (case cost × quantity) over the 7 `B` records | **$263.86** — the printed Total Content |
| **Difference** | **$9.80** |

The $9.80 is fully explained and is not an extraction error. The invoice
prints it explicitly:

```
Total Content     263.86      <- goods, net of discount
Total Deposit       4.80
FUEL SURCHARGE      5.00
Invoice Total     273.66      = 263.86 + 4.80 + 5.00
```

Extraction captures all four figures correctly (`subtotal`,
`deposit_total`, `fuel_surcharge`, `grand_total`). The gap exists because
**deposits and fuel have no confirmed home in the EDI**: the `B` record
byte map is fully accounted for (Q1, resolved), and the trailer records
that would carry them — `CFUE` for fuel, and whatever carries container
deposits — have a confirmed *layout* but unconfirmed *content* (Q4).

### Why we are not guessing

There are at least three plausible contracts and no evidence separating
them:

1. **Header = invoice total; PDI derives the rest.** The detail lines are
   goods only and PDI never cross-foots them. Current behaviour.
2. **Header = invoice total; the difference must appear in trailers.**
   The batch balances only once `CFUE`/deposit trailers are emitted. This
   would explain why every ground-truth Format-A file carries a `CFUE`
   trailer — all 9 of them do.
3. **Header = goods total only.** Deposits and fuel are handled entirely
   outside the EDI, and our header is currently $9.80 too high.

Every accepted ground-truth file we hold belongs to a **different
distributor** with its own 1,206,xxx–1,208,xxx batch sequence, and none
of them has a matching photographed invoice. So we can read what those
files *contain* but cannot compare them to what their invoice *said* —
which is precisely the comparison needed here. Guessing between the three
would mean changing a header total that PDI has already accepted, on no
evidence, and a wrong choice silently misstates the value of every
delivery.

### The exact test that resolves this

One import settles it. It requires no code change.

1. In PDI, **Delete All** pending/unposted rows so the batch starts empty
   (PDI accumulates uploads — see the forensic finding on "25 rows from 7
   records"; without this step the totals cannot be read).
2. Confirm the 7 case mappings for Balkan 3376587 and download the file.
   It will contain header `$273.66` and detail lines summing to `$263.86`.
3. Import it, and **before posting**, record from the PDI screen:
   - the **batch/invoice total PDI displays** for the import;
   - whether PDI raises any **out-of-balance / does-not-foot** warning;
   - the **sum of the detail lines as PDI shows them**;
   - whether any deposit or fuel line appears **that we did not send**.

**How to read the result:**

| What PDI shows | Conclusion |
|---|---|
| Batch total `$273.66`, no warning, details `$263.86` | Contract 1 — header is the invoice total, PDI does not cross-foot. **Change nothing.** |
| Any out-of-balance warning, or PDI refuses to post | Contract 2 — the file must balance. Next step is emitting `CFUE` for the $5.00 fuel and identifying the deposit trailer, both from Q4. |
| Batch total `$263.86` (PDI ignored our header and re-derived it) | Contract 3 — the header is advisory or goods-only. Re-open the header mapping with that evidence. |

Until one of those three rows is observed, `_pdi_amount_cents` stays as
it is. `app/services/pdi_audit.py` measures the gap on every generated
file and deliberately does **not** report it as a pass or a failure.

---

## Summary

| # | Question | Status | Blocks item/qty accuracy? | Formatter-only fix? |
|---|---|---|---|---|
| Q1 | Cost block/tail | RESOLVED — case cost at [43:49], units/case at [53:57] | No | Yes — done |
| Q2 | Batch number semantics | RESOLVED | No | Yes |
| Q3 | Return/credit sign | RESOLVED | No | Yes |
| Q4 | CFUE/CPPT trailer content | Layout resolved; content corrected to placeholder | No | CFUE: yes, once constant confirmed. CPPT: no — needs excise classification data we don't have |
| Q5 | CTAX trailer | New, unimplemented | No | Unknown — too few samples |
| Q6 | Format B (AHLA) scope | Open business question | No | N/A — scope decision first |
| Q7 | AMOUNT header vs detail balance | RESOLVED 15 Sep 2026 — PDI displays Σ detail economics; header not cross-footed; deposits/fuel outside PDI | No | No — header unchanged by decision |

None of the open items block the parts of the export that most directly
reduce manual entry today (which item, how many). Q1 and Q4's `CPPT` half
are the two most consequential open items, and both are now understood
to be missing-data problems rather than encoding puzzles — no formatter
change can resolve them without a new data source.
