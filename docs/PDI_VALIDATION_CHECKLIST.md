# PDI Export — First Real Import Validation Checklist

The formatter (`app/services/export_service.py::build_pdi_export`) is
**frozen on content** as of this milestone. Header, detail, and trailer
record byte layouts are all confirmed against 18 real accepted PDI files
(up from the original 3) — see `docs/PDI_DATA_CONTRACT.md` for the full
evidence trail.

**Update — first real import attempted.** A generated file (a real,
`VALIDATED` invoice's `format=pdi` export) was uploaded to a live PDI
account via "Upload EDI File" and rejected with a generic *"file format
was not right one"* error — a coarse, pre-parse rejection, not a
field-level one. Root cause: the file used plain `\n` line endings; every
real ground-truth file (both formats) uses `\r\n` (CRLF), confirmed
directly against the original attachment bytes. Fixed — `build_pdi_export`
now emits CRLF throughout. **Not yet re-tested against real PDI** — the
next real import attempt should confirm whether this alone resolves the
rejection, or whether a deeper content issue was masked behind it.

**Known gap, stated up front:** the 20-digit cost block, 8-digit cost
tail, and the `CFUE`/`CPPT` trailer records are currently emitted as
zero/omitted placeholders, not fabricated values. Cross-file analysis
proved a prior "unit cost × quantity" calculation and a prior
"`CPPT` = `tax_amount`" mapping were both wrong, and both were reverted
rather than continue shipping confident-looking incorrect numbers. **Any
real import will show $0.00 cost fields and no trailer records.** This is
expected — do not treat it as a new bug.

Do not change the formatter based on assumptions. Change it only in
response to an actual PDI import result, and only in the specific
function the result points to — see "If the import fails" below.

---

## Before the import

- [ ] Export a **normal (non-return) invoice** with at least one line item
      that has a known `product_sku`, using `format=pdi` from the UI
      (invoice detail → Developer panel → Structured extraction → Export →
      Download PDI) or `GET /invoices/{id}/export?format=pdi`.
- [ ] If a return/credit invoice is available (`grand_total` negative),
      export one of those too, to exercise the `-` sign path.
- [ ] Re-export the same invoice a second time and confirm the output is
      byte-for-byte identical (covered by automated tests —
      `TestPdiDeterminism` in `tests/test_export_service.py` and
      `test_repeated_pdi_export_is_byte_identical` in
      `tests/integration/test_export_api.py` — but worth a manual spot
      check before a live import).
- [ ] Have the source invoice (the original PDF/image) on hand next to the
      generated `.txt` file for line-by-line comparison during review.

## During the import

- [ ] Import the normal invoice first. Note whether PDI accepts the file
      **structurally** — i.e. does it accept a file with $0.00 cost fields
      and no trailer records at all, or does it reject/flag on a totals
      reconciliation check? This is the single most useful thing this
      first import can tell us.
- [ ] Compare the imported batch/reference number against what PDI
      actually stored or expected.
- [ ] If a return/credit invoice was exported, confirm PDI applies it as a
      credit/negative adjustment, not a duplicate positive delivery.
- [ ] If PDI's import UI or logs show what it expected for the cost
      fields or trailer records, capture that verbatim — it directly
      resolves Q1/Q4/Q5 in `docs/PDI_OPEN_QUESTIONS.md`.

## After the import

- [ ] Record the exact byte offsets and values PDI complains about, if
      any — not just "it failed." A specific field/position is what lets
      a fix be scoped to one function.
- [ ] Keep the accepted (or rejected) `.txt` file and PDI's response/error
      output as a new ground-truth artifact, the same way the original
      sample files were used to build this formatter.

---

## What's confirmed vs. what's a known gap

| # | Field | Status | Where | Risk |
|---|---|---|---|---|
| 1 | Item code, description, quantity | Confirmed | `_pdi_item_code()`, `_pdi_description()`, `_pdi_quantity()` | Low |
| 2 | Header amount, sign, batch number | Confirmed | `_pdi_amount_cents()`, `_pdi_sign()`, `_pdi_batch_number()` | Low |
| 3 | Cost block / cost tail | **Known gap** — emitted as zero | `_pdi_cost_block()`, `_pdi_cost_tail()` | Every line item's cost imports as zero/missing, not wrong — most consequential gap in the file |
| 4 | `CFUE`/`CPPT` trailer content | **Known gap** — never emitted | `_pdi_trailer_lines()` | If PDI reconciles a file's total against detail + trailer lines, a file missing these could fail a totals check even though every item line is individually correct |
| 5 | 12-digit UPC → 11-digit item code (check digit dropped) | Documented convention, not independently verified | `_pdi_item_code()` | Item could fail to match an existing PDI product record even though the invoice data itself was read correctly |
| 6 | Header date = invoice date | Confirmed format, semantics open | `_pdi_date()` | Low — cosmetic/reporting impact only |
| 7 | Format B ("AHLA") support | **Not implemented, scope unconfirmed** | N/A | If any of your invoices actually need this format, nothing in this formatter produces it |

## If the import fails

Resist the instinct to guess a fix. Instead:

1. Identify which row above the failure maps to (or whether it's a new,
   previously-unseen field).
2. Get the specific byte offset or field PDI's error refers to, if the
   import tooling provides one.
3. Change only the one function that owns that field — the formatter's
   confirmed/isolated structure means every fix should be a single-function
   change, not a formatter redesign.
4. Add or update the corresponding test in `tests/test_export_service.py`
   (and `tests/integration/test_export_api.py` if it involves the full
   pipeline) using the real value that failed, so the fix is pinned by a
   ground-truth case rather than a guess.
