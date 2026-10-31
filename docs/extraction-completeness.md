# Extraction completeness and reconciliation

A confidence score says how well text was read. It never said the invoice was whole: one real 61-row invoice,
photographed in four overlapping pieces and stored bottom-first, kept 40 rows, balanced every one of them with
itself, and showed "90.3%". This document describes what now stands between an incomplete extraction and an export.

## 1. The completeness gate (`app/services/completeness.py`)

Deterministic, from the OCR text the model was given — independent evidence of what the document printed. No model.

| Check | Fails when |
| --- | --- |
| `COMPLETENESS_MISSING_LINES` | a printed product code (hyphenated UPC or plain 11-14 digit code) has no extracted line; keys are the exact 11-digit item code, so sibling products never stand in for each other |
| `COMPLETENESS_PARTIAL_CODES` | a code is cut off by the printer (the row exists, its code cannot be named) and fewer rows were extracted than the document shows |
| `COMPLETENESS_DUPLICATE_LINES` | a code is extracted on more lines than any single photo printed it (an unmerged overlap); a row printed twice in one photo may be extracted twice |
| `COMPLETENESS_SUMMARY_CASES` / `_UNITS` | the printed Sales Summary ("Cases 68", "Total Units 958") differs from the extracted cases / printed units |
| `COMPLETENESS_SECTION_TOTALS` | a printed section subtotal (pack-size heading, then `SUBTOTAL cases units`) differs from the cases extracted under that heading — a missing, merged or misfiled row |

Statuses: **COMPLETE** (the evidence was compared and every difference explained), **INCOMPLETE** (any unexplained
difference), **UNVERIFIED** (no OCR text, or nothing printed to compare). Never COMPLETE by default. Per-row arithmetic
passing is irrelevant to it. Not codes: phone, fax, account and contact numbers and all-zero placeholders.

The gate runs on first validation, on reprocess, and on every revalidation after a correction (using the stored OCR
text), so a reviewer who adds the missing rows by hand can turn an INCOMPLETE invoice COMPLETE.

**Confidence.** An INCOMPLETE invoice's composite confidence is capped at 0.60 and the uncapped reading quality is
kept beside it (`composite_uncapped`); the report and screen say why. OCR, model and completeness are reported
separately.

## 2. Export policy

`invoices.blocking_checks` records the CRITICAL checks that fail now: the completeness checks above plus
`SUBTOTAL_MATCHES_ITEMS` and `GRAND_TOTAL_MATH`. Completeness failures and financial reconciliation failures are
blocked by default, and **one rule covers every file that leaves the system** (`export_service.critical_check_gate`):

| Route | While critical checks fail |
|---|---|
| `format=pdi` | refused (422); PDI-specific rules (store, mapping, cost, quantity, identity) still apply on top |
| `format=txt`, `format=csv` | refused (422) with the failing check names and a pointer to the review copy |
| `format=json` | readable (it is the structured record the screen is built on); carries `export_status.export_ready: false` and the failing checks |
| `purpose=review` (TXT/CSV only) | a labelled internal-investigation copy, see below |

Every correction revalidates, so the block lifts by itself when the checks pass. Other review reasons (a missing date, a
tax-rate inconsistency) still allow a PDI export on confirmation, as before. Invoices stored before this existed have no
value and are unaffected until revalidated or reprocessed through the normal workflows; nothing is backfilled.

**Review copies.** `GET /invoices/{id}/export?format=txt|csv&purpose=review` (MANAGER/ADMIN) downloads a copy for
investigation even while blocked. It cannot be mistaken for an export: the file starts with `REVIEW COPY - NOT
EXPORT-READY - DO NOT IMPORT` and the failing checks (CSV: `#` comment rows, so an importer expecting a header row
rejects it), the filename contains `REVIEW-ONLY_not-for-import`, and the response carries `X-Export-Purpose:
review-only`. There is no review copy of the PDI file. Review downloads write nothing.

**Administrator override** (`app/services/export_override_service.py`, table `export_overrides`):

* `POST /invoices/{id}/export-override` (ADMIN only; a reason of at least 10 characters is required) records who allows
  what and why, plus the latest stored validation report verbatim (`validation_snapshot`). `GET
  /invoices/{id}/export-overrides` (MANAGER/ADMIN) lists the history; `POST /export-overrides/{id}/revoke` (ADMIN, reason
  required) withdraws one.
* It **never edits the invoice**: status, `blocking_checks`, confidence, the validation report and the validation log are
  untouched, so an incomplete invoice is never silently turned into a validated one. Revalidation keeps recomputing the
  blocks; the override only lets the files be produced.
* It covers **one exact state**: a fingerprint of the figures, lines, typed charges and failing checks. Any change
  (a correction, a reprocess) makes it STALE and the block applies again until an administrator looks again.
* It lifts **only** the critical-check block. Every PDI-specific rule still applies, and **Mark Retailed — hence the
  inventory receipt — still requires a VALIDATED invoice with no failing critical check**: an override can export a file,
  it cannot attest the invoice as received.
* Files produced under an override say so: the PDI file's bytes are untouched (the contract), but the response carries
  `X-Export-Override-Id`, the EDI ledger row stores `export_override_id`, TXT starts with an override banner and its
  filename and the CSV filename carry `admin-override`, and the invoice screen shows an "Export block overridden by an
  administrator" panel and an "Admin override" status in lists.
* Every grant, revocation and override-authorised export (PDI, TXT and CSV, every time) is written to the document's
  processing log. An export entry (`exported_under_override`) names the invoice (id and number), the format, the
  override id, the user (name, id, role), the UTC timestamp, the SHA-256 and size of the bytes actually delivered, the
  failing checks, and a `validation_snapshot_ref` (override id, invoice-state fingerprint and a digest of the validation
  snapshot kept on the override). Review-only downloads are separate and write nothing.

`scripts/completeness_exposure_report.py` (read-only) lists the stored invoices the gate calls INCOMPLETE and which of
them were already exported or received.

## 3. Photo order (`app/services/photo_order.py`)

The upload sequence is no longer assumed top-to-bottom. Header cues (invoice/customer block), closing-totals cues,
overlap between neighbouring photos and capture time in the file name are weighed; a reorder happens only on STRONG
evidence (a header photo and a different closing-totals photo). Sections keep their stored photo number, so
`source_pages` still names the stored photo. The evidence is logged with the text extraction; a still-doubtful
multi-photo order is a `PHOTO_ORDER` warning.

Not done: extracting each photograph separately and stitching rows in code. It costs one model call per photo and
needs a paid replay to prove; the gate is the safety net meanwhile.

## 4. Financial schema

`invoices.other_charges` holds typed additional charges `[{kind, label, amount}]` (`state_container_deposit`,
`jurisdiction_charge`, `delivery`, `fuel`, `service`, `other`). They are not tax and not merchandise; the grand-total check
is merchandise + tax + deposit + fuel + other charges (delivery/fuel entries are not counted twice beside a
`fuel_surcharge`). Per line: `printed_units` and `section_label`.

## 5. Units per case

The number up front in "20OZ PL 1/24S" is the container size; the case count is the denominator of `1/N`. A bare size is
never a count. Printed units ÷ cases is preferred when it divides evenly; when it disagrees with the pack text nothing is
prefilled and both readings are offered (`suggestion_source: conflict`).

## 6. Approval integrity

An approved case-mapping proposal has its mapping. `approve()` checks that the mapping landed with the approved value
and a link back, and fails the whole approval (nothing committed) if not. `GET /proposals/integrity` lists approved
proposals whose mapping is missing or different and was not superseded; the invoice review shows each proposal as
applied / superseded / **not applied**. Nothing repairs, revives or rewrites a proposal.

## 7. Prompt

v12 (typed charges, sections, "return rows seen in only one photo") is registered but **not active**: v11 stays active
until v12 has been replayed against real invoices (a paid step).

`scripts/extraction_replay.py` prepares that one controlled replay of #48288200. It re-runs a single model call over the
OCR text already stored (no OCR is repeated), scores the answer with the real validation and completeness gate against
the acceptance figures (61 lines, 68 cases, 958 units, subtotal 1,821.18, charges 23.30, tax 0, amount due 1,844.48,
COMPLETE, nothing blocking, confidence not capped), and writes only an output file. The default is an estimate (no
network, no key). Spending needs `--execute`, an explicit `--prompt-version`, `--max-cost-usd` at or above the HARD
BOUND, and an `--output` outside the repository that does not yet exist. The call is capped in output tokens
(`OpenAIProvider(max_output_tokens=...)`; the application never sets it), so its cost has a bound; an answer that needs
more is cut and fails as `length`. Retries are switched off and the timeout raised, because the longer v12 answer would
otherwise time out at the 30 s default.

First replay (2026-10-31, approved ceiling $0.093, output cap 6,000): the call was made once and **cut by the cap** — v12
needs more than 6,000 output tokens on this invoice, so no result was produced and about $0.09 was spent. The estimate
constants were recalibrated from that (at least 26 extra output tokens per line, not 12); a second replay needs a new
approval at a higher ceiling.

## Release

Migration `0039` is additive: nullable columns `invoices.other_charges`, `invoices.blocking_checks`,
`invoice_items.printed_units`, `invoice_items.section_label`, `edi_exports.export_override_id`, and a new empty table
`export_overrides` (row level security on). No existing row is rewritten. No environment variable changes. Deploy order:
migrate, then deploy.
