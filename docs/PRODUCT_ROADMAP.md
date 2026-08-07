# Invoice Intelligence Platform — Product & Architecture Roadmap

**KPI:** minimize manual effort per invoice, measured as *manual field
edits required inside PDI after import*. Not EDI byte-fidelity. The EDI
is the delivery mechanism, not the product.

**Baseline (measured, invoice 3376587, 5 line items):** ~5 manual cost
edits + 1 total correction = **~6 edits/invoice, 100% of lines touched.**
Target: <0.5 edits/invoice.

---

## 1. Current platform assessment

Verified working, and genuinely hard to have built:

| Subsystem | Status | Evidence |
|---|---|---|
| Upload, storage, dedup | Solid | Hash-based dedup, UUID paths, integration-tested |
| PDF text extraction | Solid | pdfplumber, digital-PDF routing works |
| LLM structured extraction | Works, **not reliable** | See §2.1 — non-deterministic |
| Validation engine | Solid, well-designed | Deterministic, collects all results, never short-circuits |
| Persistence | Works, **one data-loss bug** | See §2.4 |
| PDI formatter | Structurally correct | PDI accepts the file; products/UPC/Product Master all resolve |
| Frontend | Read-only viewer | See §2.2 — **no correction capability at all** |
| Test suite | Strong | 168 offline + 41 integration, real Postgres |

**Three corrections to the "technical feasibility is complete" framing.**
Not pedantry — each one is a top-5 roadmap item:

1. **Case Cost still imports as \$0.00**, so *every line of every invoice*
   needs a manual cost entry in PDI. That is not a leftover detail — by
   the KPI above it is currently ~100% of the remaining manual work.
   Experiment 2 is built but untested.
2. **There is no way to correct an extraction error in our system.**
   Verified: the API exposes only upload/process/delete — no PUT, no
   PATCH, no correction endpoint. The frontend has no edit UI. Every
   correction therefore happens inside PDI, where we cannot observe it.
   **This makes the entire "every invoice makes the platform smarter"
   vision structurally impossible today** — there is no data pathway.
3. **Extraction is non-deterministic.** Directly observed this session:
   the same document processed twice produced `unit_price` \$18.96 on one
   run and \$19.41 on another.

---

## 2. Remaining weaknesses, root causes, impact

### 2.1 Non-deterministic extraction — *the highest-leverage bug in the platform*

**Problem:** the same invoice processed twice yields different prices.

**Root cause (verified in code):** `app/services/llm/openai_provider.py`
calls `chat.completions.parse()` with **no `temperature` and no `seed`**
— so it runs at the API default (1.0), i.e. maximum sampling randomness,
for a task that is pure deterministic transcription.

**Why it matters far beyond the immediate errors:** non-determinism makes
regression testing meaningless (can't distinguish a real improvement from
sampling noise), makes benchmarking impossible, and makes continuous
learning unmeasurable. **Every other AI improvement on this roadmap is
un-verifiable until this is fixed.** It is the foundation.

- Solvable from the invoice alone? **YES** — it's a config bug, not an
  information problem.
- Approach: set `temperature=0`, `seed=<fixed>`. Add a determinism test
  that runs one fixture twice and asserts byte-identical extraction.
- Impact: **Very High** (unblocks everything else) · Effort: **Small**

### 2.2 No correction workflow — *the biggest product gap*

**Problem:** when extraction is wrong, the user fixes it in PDI. We never
learn. The same vendor's same quirk fails identically forever.

**Root cause:** architectural — the system was built as a one-way
pipeline. Invoices are immutable after processing (verified: no update
endpoints exist).

- Solvable from the invoice alone? **YES** — needs no external data, just
  a schema + endpoint + UI.
- Approach: an `invoice_corrections` table (original value, corrected
  value, field path, who, when), a PATCH endpoint, and an inline edit UI
  on the existing invoice detail page. Corrections must be applied
  *before* EDI generation so the download is already right.
- Impact: **Very High** — converts the review step from "fix in PDI
  forever" into "fix once, here, and the platform captures it" ·
  Effort: **Medium**

### 2.3 OCR→text→LLM destroys column structure — *root cause of the price errors*

**Problem:** on image invoices the model picks the wrong price column.

**Root cause — this is the important one.** The invoice prints five
adjacent numeric columns:

```
ITEM# QTY DESCRIPTION      UPC         U.PRICE  DISC  D.PRICE  DEP    EXT
71600  1  NESQ MILK 12/14  028000772123  19.41  0.45   18.96   0.00  18.96
68068  1  RB COCONUT 24/12 611269321210  56.50  6.30   50.20   1.20  51.40
```

Our pipeline runs Google Vision OCR → **flattens to plain text** → hands
the text to the LLM. Column alignment — the only thing that distinguishes
`U.PRICE` from `D.PRICE` from `DEP` — is destroyed before the model ever
sees it. The \$19.41 vs \$18.96 non-determinism is *exactly* this: two
adjacent columns on the same row. Same root cause as missing deposits
and discounts.

- Solvable from the invoice alone? **YES** — the data is on the page; we
  are throwing away the spatial information needed to read it.
- Approach: **send the image directly to a vision-capable model** instead
  of OCR-then-text. This removes an entire lossy stage. Keep pdfplumber
  for digital PDFs (already lossless there). Optionally retain Vision OCR
  output as a secondary signal for cross-checking.
- Impact: **Very High** · Effort: **Medium**

### 2.4 Persistence coerces unknown → \$0.00

**Problem:** an illegible price is stored as `0`, indistinguishable from
a genuinely free item, and exports as a confident \$0.00.

**Root cause (verified):** `app/repositories/invoice_repository.py`
coerces `None → Decimal("0")` for `quantity`/`unit_price`/`line_total`
because those columns are `NOT NULL`. The LLM correctly returned `null`
with low confidence; validation correctly warned; **persistence
destroyed the signal.**

- Solvable from the invoice alone? **YES** — pure data-modelling fix.
- Approach: make the three columns nullable (migration), drop the
  coercion, render "unknown" distinctly from zero in all exports.
- Impact: **High** — unknowns become visible and reviewable instead of
  silently wrong · Effort: **Small**

### 2.5 Deposits and discounts never captured

**Problem:** `DEP` (\$1.20/case on Red Bull) and `DISC` are printed on the
invoice, absent from our schema. Deposits are real money in
bottle-deposit states.

**Root cause:** the extraction schema has no field for them (§2.3 makes
them hard to read even if it did).

- Solvable from the invoice alone? **YES for capture.** Whether PDI
  *accepts* them is a separate, unanswered question.
- Approach: add `deposit_amount` / `discount_amount` to
  `ExtractedLineItem` (new prompt version — never mutate a shipped one).
  Capture first, decide EDI mapping later.
- Impact: **Medium** (High in deposit states) · Effort: **Small**

### 2.6 Case Cost = \$0.00 in PDI

**Problem:** the single biggest source of manual work today.

**Root cause:** unresolved. Cost tail eliminated (Experiment 1). Cost
block untested (Experiment 2, built and pending).

- Solvable from the invoice alone? **Unknown** — we have the cost; we
  don't know which bytes carry it.
- Approach: finish the experiment sequence. **If Experiment 2 also fails,
  stop guessing at the byte layout** and ask PDI/the vendor directly for
  the field spec. Two failed experiments is enough evidence that further
  black-box probing is not the efficient path.
- Impact: **Very High** · Effort: **Small** (test) / **Unknown** (fix)

### 2.7 No product/vendor memory

**Problem:** every invoice is processed from scratch. A vendor whose
layout we've seen 50 times gets no benefit from those 50 examples.

- Solvable from the invoice alone? **NO** — requires accumulated history.
- Required: a Product Master (UPC → canonical product, case pack, last
  known cost) and vendor profiles. **Its highest-value use is not
  autofill — it's validation:** "this vendor billed \$48.70 last week and
  \$50.20 today" is a cheap, powerful error detector.
- Impact: **High** · Effort: **Large** — sequence it *after* the
  correction loop (§2.2), which is what populates it with trustworthy data.

---

## 3. Roadmap, ordered by (impact ÷ effort)

| # | Item | Impact | Effort | Why this order |
|---|---|---|---|---|
| 1 | Determinism (§2.1) | Very High | Small | Nothing downstream is measurable without it |
| 2 | Case Cost experiment (§2.6) | Very High | Small | Largest single manual-work item; already built |
| 3 | Nullable money columns (§2.4) | High | Small | Stops silently-wrong \$0.00 |
| 4 | Correction workflow (§2.2) | Very High | Medium | Unlocks the learning loop; nothing else can |
| 5 | Vision-direct extraction (§2.3) | Very High | Medium | Removes the lossy stage causing price errors |
| 6 | Deposits/discounts (§2.5) | Medium | Small | Cheap once §2.3 lands |
| 7 | Product Master (§2.7) | High | Large | Needs §4's data to be worth building |

---

## 4. Immediate sprint

**Sprint goal: make extraction trustworthy and capture the first
correction.** Not new features — reliability plus the learning loop.

1. **Determinism.** `temperature=0` + fixed `seed`. Determinism test.
   *(Small)*
2. **Finish the Case Cost experiment.** Upload Experiment 2, record the
   result. If negative → escalate to PDI for the field spec instead of
   Experiment 3. *(Small)*
3. **Nullable money columns.** Migration + drop the coercion + render
   unknown ≠ zero. *(Small)*
4. **Correction workflow, minimum viable.** `invoice_corrections` table,
   PATCH endpoint, inline edit on line items, regenerate EDI from
   corrected values. **Log every correction from day one even before
   anything consumes them** — this is the training set, and it only
   accumulates if we start now. *(Medium)*
5. **Instrument the KPI.** Count corrections per invoice, per field, per
   vendor. Without this we cannot tell whether any of the above worked.
   *(Small)*

Explicitly out of sprint: Product Master, multi-ERP translators, vendor
prompts, analytics.

---

## 5. Long-term architecture

The proposed layering is right. Refined:

```
Document (PDF / image)
  ↓
Ingestion              format detect, dedup, storage
  ↓
Understanding          vision-direct for images; pdfplumber for digital PDFs
  ↓  ExtractedInvoice (raw model output + per-field confidence)
Reconciliation         NEW — recover derivable values, resolve column
  ↓                    ambiguity via arithmetic (qty × unit = ext?)
Validation             deterministic checks (existing, unchanged)
  ↓
Enrichment             NEW — Product Master / vendor history
  ↓  CanonicalInvoice  ← the stable contract; the actual product
  ↓
Translators            PDI | SAP | Oracle | NetSuite | QuickBooks
```

**Two design points worth arguing for:**

**Reconciliation as its own stage, before validation.** Today validation
only *judges* ("these don't add up"). Reconciliation *repairs*: given
`qty=1`, `ext=18.96`, and candidate prices `{19.41, 18.96}`, arithmetic
alone identifies 18.96 as the one consistent with the line total. That
single rule fixes the exact error we observed — deterministically, no AI,
no external data. **This is the cheapest accuracy win on the roadmap and
it does not exist yet.**

**CanonicalInvoice is the product; translators are plugins.** The PDI
formatter should be one implementation of a `Translator` interface
(`translate(CanonicalInvoice) -> bytes`), not the pipeline's endpoint.
Adding SAP must never require touching extraction. The current
`export_service.py` is already close to this — it reads persisted state
and emits bytes — it just needs the interface made explicit. **Do not
build other translators until a second ERP is actually needed.**

**Challenging existing decisions honestly:**

| Decision | Verdict |
|---|---|
| Deterministic validation, no AI | **Keep.** Auditable, testable, correct. A real strength. |
| Versioned immutable prompts | **Keep.** Exactly right for a learning system. |
| Postgres + SQLAlchemy async | **Keep.** No scale pressure anywhere near this. |
| OCR → text → LLM | **Change.** Lossy; direct cause of §2.3. |
| Default LLM temperature | **Change.** Bug. |
| `NOT NULL` money columns | **Change.** Forces the §2.4 data loss. |
| Immutable invoices, no corrections | **Change.** Blocks the entire learning vision. |
| PDI formatter as terminal stage | **Evolve** into a translator interface — cheap, not urgent. |

---

## 6. Training data strategy

The asset is **corrections**, not invoices. An invoice alone is unlabeled;
an invoice plus "the human changed \$19.41 → \$18.96 on line 3" is a
labeled example of a real, recurring failure.

```
training_data/
  {invoice_id}/
    source.{pdf,jpg}          original document
    extraction.json           raw model output + prompt version + model id
    corrections.json          field path, original, corrected, actor, timestamp
    canonical.json            final accepted invoice
    metadata.json             vendor, layout fingerprint, confidence, KPI counts
```

`corrections.json` — one row per human edit:

```json
{
  "field_path": "line_items[3].unit_price",
  "original": 19.41,
  "corrected": 18.96,
  "error_class": "wrong_column",
  "corrected_at": "2026-08-05T10:00:00Z"
}
```

`error_class` is what makes this a *dataset* rather than a log — it lets
us count failure modes and target the biggest one. Start with a small
fixed vocabulary (`wrong_column`, `ocr_misread`, `missing_field`,
`wrong_product`) and extend only as real cases demand.

**Use it in this order** (cheapest first, which is also
most-effective-first here): (1) count error classes to find the top
failure mode; (2) turn recurring cases into regression fixtures; (3) add
corrected examples as few-shot prompts for that vendor's layout; (4)
fine-tune *only* if 1–3 plateau. Most teams reach for step 4 first and
it is almost never the right first move.

---

## 7. Continuous improvement

**Golden set.** ~20 real invoices with human-verified canonical JSON.
Every prompt or model change runs against it before ship. Requires
determinism (§2.1) to mean anything.

**Metrics, in priority order:**
1. **Corrections per invoice** — the KPI. Everything else is diagnostic.
2. Field-level accuracy vs. golden set (per field: which fails most?)
3. REVIEW_REQUIRED rate
4. Straight-through rate: % needing zero human touch

**Regression gate:** golden-set accuracy must not drop. Prompt versions
are already immutable, so a bad version is a one-line revert — that
existing design pays off here.

---

## 8. What NOT to build

Real engineering cost, minimal KPI movement:

- **Other ERP translators** (SAP/Oracle/NetSuite) — zero value until a
  second ERP customer exists. Design the interface; don't implement.
- **Analytics/dashboards/reporting** — does not reduce manual effort.
- **Fine-tuning** — expensive, slow, and dominated by prompt+few-shot
  until the correction dataset is large and stable.
- **Multi-OCR ensembling/voting** — complexity for a problem
  vision-direct extraction removes outright.
- **Full AHLA/Format-B support** — scope unconfirmed; may not be needed.
- **Soft-delete, audit trails, RBAC, multi-tenancy** — real production
  needs eventually, but no user is blocked today.
- **Further black-box byte probing on Case Cost** — if Experiment 2
  fails, asking PDI is faster and cheaper than Experiment 3+.

---

## 9. The one-line summary

Don't optimize for producing an EDI. Optimize for eliminating manual
invoice processing. The EDI is simply the delivery mechanism.

Concretely, for the next sprint that means: **make extraction
deterministic, stop destroying the "unknown" signal, resolve Case Cost,
and start capturing corrections** — because until corrections are
captured, the platform cannot get smarter, and every other improvement is
a one-off rather than compounding.
