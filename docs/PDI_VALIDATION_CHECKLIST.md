# PDI Export — First Real Import Validation Checklist

The formatter (`app/services/export_service.py::build_pdi_export`) is
**frozen** as of this milestone. It has reached the structural
completeness needed for a first real import attempt — header, detail,
and trailer records all follow byte layouts confirmed against the three
supplied ground-truth files. What it has *not* had is a validated
round-trip through the actual PDI system. This checklist is for that
first real import.

Do not change the formatter based on assumptions. Change it only in
response to an actual PDI import result, and only in the specific
function the result points to — see "If the import fails" below.

---

## Before the import

- [ ] Export a **normal (non-return) invoice** with at least one line item
      that has a known `product_sku`, using `format=pdi` from the UI
      (invoice detail → Developer panel → Structured extraction → Export →
      Download PDI) or `GET /invoices/{id}/export?format=pdi`.
- [ ] Export a **second invoice with a non-zero `tax_amount`**, to exercise
      the `CPPT` trailer line.
- [ ] If a return/credit invoice is available (`grand_total` negative),
      export one of those too, to exercise the `-` sign path.
- [ ] Re-export the same invoice a second time and confirm the output is
      byte-for-byte identical (this is covered by an automated test —
      `TestPdiDeterminism` in `tests/test_export_service.py` and
      `test_repeated_pdi_export_is_byte_identical` in
      `tests/integration/test_export_api.py` — but worth a manual spot
      check before a live import).
- [ ] Have the source invoice (the original PDF/image) on hand next to the
      generated `.txt` file for line-by-line comparison during review.

## During the import

- [ ] Import the normal invoice first. Note whether PDI accepts the file
      at all (structural acceptance) before checking whether the imported
      values are correct.
- [ ] Compare every imported line item's cost/price against the source
      invoice. This is the highest-risk field — see Q1 below.
- [ ] Compare the imported batch/reference number against what PDI
      actually stored or expected.
- [ ] Import the taxed invoice and confirm the prepaid-tax figure PDI
      records matches the `CPPT` trailer's amount.
- [ ] If a return/credit invoice was exported, confirm PDI applies it as a
      credit/negative adjustment, not a duplicate positive delivery.

## After the import

- [ ] Record the exact byte offsets and values PDI complains about, if
      any — not just "it failed." A specific field/position is what lets
      a fix be scoped to one function.
- [ ] Keep the accepted (or rejected) `.txt` file and PDI's response/error
      output as a new ground-truth artifact, the same way the original
      three sample files were used to build this formatter.

---

## Assumptions to verify during this import

These are the specific, named assumptions baked into the current
implementation. Each maps to exactly one function, per
`docs/PDI_OPEN_QUESTIONS.md`.

| # | Assumption | Where | Risk if wrong |
|---|---|---|---|
| 1 | The 20-digit cost block and 8-digit cost tail are plain right-justified, zero-padded cents (no sub-fields, no different scale) | `_pdi_cost_block()`, `_pdi_cost_tail()` | Every line item's cost imports as a wrong number, not a missing one — the most consequential assumption in the file |
| 2 | `invoice.tax_amount` is the same figure PDI's `CPPT` trailer expects | `_pdi_trailer_lines()` | Tax imports as a real but incorrect number, or fails a totals reconciliation if PDI checks trailer + detail against the header amount |
| 3 | The header amount is the invoice grand total, magnitude-only, with direction carried solely by the sign character | `_pdi_amount_cents()`, `_pdi_sign()` | Return invoices could double-apply or fail to net against the original delivery |
| 4 | The header date is the invoice date (not a file-generation date or a due date) | `_pdi_date()` | Low risk — cosmetic/reporting impact only, unlikely to block acceptance |
| 5 | A 12-digit UPC's PDI item code is the UPC with its trailing check digit dropped | `_pdi_item_code()` | Item could fail to match an existing PDI product record even though the invoice data itself was read correctly |
| 6 | Fuel surcharge (`CFUE`) is out of scope — no source field exists in extraction, so it's never emitted | `_pdi_trailer_lines()` | If PDI reconciles a file's total against detail + trailer lines and a real invoice has a fuel surcharge, that file may fail a totals check even though every emitted line is individually correct |

## If the import fails

Resist the instinct to guess a fix. Instead:

1. Identify which of the six assumptions above the failure maps to (or
   whether it's a new, previously-unseen field).
2. Get the specific byte offset or field PDI's error refers to, if the
   import tooling provides one.
3. Change only the one function that owns that field — the formatter's
   confirmed/isolated structure means every fix should be a single-function
   change, not a formatter redesign.
4. Add or update the corresponding test in `tests/test_export_service.py`
   (and `tests/integration/test_export_api.py` if it involves the full
   pipeline) using the real value that failed, so the fix is pinned by a
   ground-truth case rather than a guess.
