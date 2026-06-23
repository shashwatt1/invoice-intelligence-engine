# Sample Workflow

## Purpose

This document traces the complete lifecycle of a single invoice as it moves through the Invoice Intelligence Platform — from the moment a PDF is uploaded to the final ERP-ready export and beyond into analytics. Every processing stage is illustrated with realistic data, showing exactly what the system receives, what it produces, and how each component transforms the document into structured, validated, and actionable business intelligence.

This workflow serves as the **guiding reference** for the entire project:

- **Founders** can use it to understand the end-to-end value proposition.
- **Product Managers** can use it to scope features and define acceptance criteria.
- **Engineers** can use it to understand data contracts between components.
- **Future Contributors** can use it as onboarding material to see how all layers connect.

Every example in this document reflects the system's actual data models, validation rules, and database schemas as defined in the project's [architecture](architecture.md), [design](design.md), and [implementation plan](implementation_plan.md).

---

## Example Business Scenario

**FreshMart Groceries** is a mid-size retail store that receives invoices from multiple food and beverage distributors. Every week, delivery trucks arrive with product shipments, and the drivers hand over printed invoices — some as clean digital PDFs attached to emails, others as paper invoices that are scanned at the receiving dock.

Today, FreshMart receives a shipment from **Pacific Coast Distributors**, a long-standing beverage supplier. The delivery driver hands the store manager a one-page invoice in PDF format. Instead of manually keying every line item into a spreadsheet, the store manager uploads the invoice to the Invoice Intelligence Platform.

This document follows that single invoice through every stage of the system.

---

## Step 1 — Invoice Received

The invoice arrives as a standard vendor document. Here are the key details visible on the face of the document:

| Field | Value |
|---|---|
| **Vendor** | Pacific Coast Distributors |
| **Vendor Tax ID** | 94-3218756 |
| **Vendor Address** | 2400 Harbor Blvd, Suite 110, Oakland, CA 94607 |
| **Invoice Number** | PCD-2026-08743 |
| **Invoice Date** | June 23, 2026 |
| **Due Date** | July 23, 2026 |
| **Payment Terms** | Net 30 |
| **Ship To** | FreshMart Groceries — Store #12, 880 Market St, San Francisco, CA 94102 |

The invoice contains a table of 5 line items — various beverages delivered in cases — followed by a subtotal, tax calculation, and grand total at the bottom.

This is a typical vendor invoice: structured enough for a human to read, but formatted in Pacific Coast Distributors' own layout, fonts, and column arrangement — not a standardized template that a rigid parser could rely on.

---

## Step 2 — Document Ingestion

### 2.1 Upload

The store manager opens the platform interface and uploads the PDF file via the `POST /upload` API endpoint. The system immediately performs the following intake checks:

| Check | Action | Result |
|---|---|---|
| **MIME Type Validation** | Verify file is `application/pdf`, `image/png`, or `image/jpeg` | ✅ `application/pdf` |
| **File Size Validation** | Reject files larger than 25MB or smaller than 1KB | ✅ 142 KB |
| **SHA-256 Hash Calculation** | Compute `SHA-256` hash of raw file bytes | `a3c7f9e1d2b4...` |
| **Duplicate Check** | Query `invoices` table: does this hash already exist for FreshMart's `organization_id`? | ✅ No match — new document |
| **Object Storage Write** | Upload raw PDF to S3/Supabase Storage at path `{bucket}/org_freshmart/2026/06/{uuid}.pdf` | ✅ Stored |
| **Database Record Creation** | Insert initial row into `invoices` table with `status = INGESTED` | ✅ `document_uuid: a7f3d912-...` |

The API returns immediately:

```json
{
  "success": true,
  "data": {
    "document_uuid": "a7f3d912-4b6c-4e8a-b3d1-9f2e7a8c5d04",
    "status": "INGESTED",
    "file_url": "https://storage.example.com/invoices/org_freshmart/2026/06/a7f3d912.pdf",
    "created_at": "2026-06-23T14:32:07Z"
  }
}
```

### 2.2 Document Type Detection

Before any text extraction begins, the system determines **how** to read the document. Not all PDFs are created equal:

- **Digital PDF** — Created by software (e.g., exported from an accounting system). Contains a native text layer that can be read programmatically with 100% character accuracy. No OCR required.
- **Scanned PDF / Image** — A photograph or scan of a paper document. Contains only pixel data. Text must be extracted using OCR (Optical Character Recognition).

The system opens the PDF with `pdfplumber` and checks for an embedded text layer:

```
Document: PCD-2026-08743.pdf
Text layer detected: YES
Pages with readable text: 1 / 1
Classification: digital_pdf
```

**Result**: This invoice is a **digital-native PDF**. It bypasses the entire OCR pipeline, saving GPU/API costs and eliminating OCR error risk. Text extraction will run in milliseconds instead of seconds.

> **If this were a scanned document or image**, the system would route it to the OCR path:
> 1. Convert each page to a 300 DPI PNG image.
> 2. Apply preprocessing (deskew, contrast enhancement, binarization).
> 3. Run the image through the active OCR driver (PaddleOCR primary, EasyOCR fallback).
> 4. Collect text tokens with bounding boxes and confidence scores.

---

## Step 3 — Text Extraction

Since this is a digital PDF, the system uses `pdfplumber` to extract the raw text directly. Here is the literal text output the system captures from the document — exactly as it appears, with all the layout noise, spacing quirks, and raw formatting:

```
PACIFIC COAST DISTRIBUTORS
2400 Harbor Blvd, Suite 110
Oakland, CA 94607
Tax ID: 94-3218756
Phone: (510) 555-0142

INVOICE

Invoice Number: PCD-2026-08743
Invoice Date: 06/23/2026
Due Date: 07/23/2026
Terms: Net 30

Bill To:                              Ship To:
FreshMart Groceries                   FreshMart Groceries
Accounts Payable                      Store #12
PO Box 4420                           880 Market St
San Francisco, CA 94101               San Francisco, CA 94102

---------------------------------------------------------------------------------
Item          Description                   Qty    Unit Price    Amount
---------------------------------------------------------------------------------
BEV-1001      Sparkling Water 24pk          12       $4.25        $51.00
BEV-1002      Organic Orange Juice 1L       30       $3.80       $114.00
BEV-1003      Cold Brew Coffee 12pk          8      $12.50       $100.00
BEV-1004      Coconut Water 12pk            20       $6.75       $135.00
BEV-1005      Lemonade Concentrate 2L       15       $2.90        $43.50
---------------------------------------------------------------------------------
                                                   Subtotal:    $443.50
                                                   Tax (8.5%):   $37.70
                                                   Grand Total: $481.20

Payment Instructions: Wire to Pacific Coast Distributors
Bank: First National Bank | Routing: 121000358 | Account: 8834201957

Thank you for your business!
```

The system packages this text into a structured `TextExtractionResult` object:

| Field | Value |
|---|---|
| `document_uuid` | `a7f3d912-4b6c-4e8a-b3d1-9f2e7a8c5d04` |
| `source_type` | `digital_pdf` |
| `pages` | 1 page |
| `full_text` | *(the raw text above, whitespace-trimmed)* |
| `extraction_duration_ms` | 47 |

This raw text is what gets passed to the AI Structuring Layer. The system does not attempt to parse it with regex or spatial heuristics — that is the AI's job.

---

## Step 4 — AI Structuring

### 4.1 Prompt Construction

The AI Structuring Service takes the raw extracted text and constructs a prompt for the OpenAI API. The prompt has four components:

1. **System Prompt** — Defines the model's role as a strict invoice parser.
2. **Schema Hint** — Tells the model exactly which fields to extract.
3. **Document Text** — The whitespace-trimmed raw text from Step 3.
4. **Completion Guard** — Instructs the model to return `null` for missing fields, never to hallucinate values.

The objective is: **transform unstructured text into a typed, schema-validated JSON object.**

The model used is `gpt-4o-mini` (cost-efficient for Phase 1). The response is enforced against a strict Pydantic schema using the `instructor` library — if the model returns malformed JSON, `instructor` automatically retries up to 2 times.

### 4.2 Structured JSON Output

The AI returns the following structured invoice data, conforming exactly to the platform's `InvoiceSchema`:

```json
{
  "invoice_number": "PCD-2026-08743",
  "invoice_date": "2026-06-23",
  "due_date": "2026-07-23",
  "vendor_name": "Pacific Coast Distributors",
  "vendor_tax_id": "94-3218756",
  "vendor_address": "2400 Harbor Blvd, Suite 110, Oakland, CA 94607",
  "subtotal": 443.50,
  "tax_amount": 37.70,
  "discount_amount": 0.00,
  "grand_total": 481.20,
  "currency": "USD",
  "line_items": [
    {
      "description": "Sparkling Water 24pk",
      "quantity": 12,
      "unit_price": 4.25,
      "line_total": 51.00,
      "tax_rate": null,
      "discount": 0.00
    },
    {
      "description": "Organic Orange Juice 1L",
      "quantity": 30,
      "unit_price": 3.80,
      "line_total": 114.00,
      "tax_rate": null,
      "discount": 0.00
    },
    {
      "description": "Cold Brew Coffee 12pk",
      "quantity": 8,
      "unit_price": 12.50,
      "line_total": 100.00,
      "tax_rate": null,
      "discount": 0.00
    },
    {
      "description": "Coconut Water 12pk",
      "quantity": 20,
      "unit_price": 6.75,
      "line_total": 135.00,
      "tax_rate": null,
      "discount": 0.00
    },
    {
      "description": "Lemonade Concentrate 2L",
      "quantity": 15,
      "unit_price": 2.90,
      "line_total": 43.50,
      "tax_rate": null,
      "discount": 0.00
    }
  ],
  "extraction_notes": null
}
```

### 4.3 Token Usage

The API call is logged to the `llm_usage_log` table:

| Field | Value |
|---|---|
| `model` | `gpt-4o-mini` |
| `prompt_tokens` | 847 |
| `completion_tokens` | 312 |
| `total_tokens` | 1,159 |
| `cost_usd` | $0.000267 |
| `duration_ms` | 1,843 |

This invoice cost less than **$0.001** to process with AI — well within the project's target of **< $0.03 per invoice**.

---

## Step 5 — Validation

The Validation Service runs the structured output through a series of programmatic business rules. Every check is independent — failures are collected, not short-circuited.

### 5.1 Line Item Math Checks

For each line item: does `Quantity × Unit Price == Line Total`?

| # | Description | Qty | Unit Price | Expected | Actual | Status |
|---|---|---|---|---|---|---|
| 1 | Sparkling Water 24pk | 12 | $4.25 | $51.00 | $51.00 | ✅ Pass |
| 2 | Organic Orange Juice 1L | 30 | $3.80 | $114.00 | $114.00 | ✅ Pass |
| 3 | Cold Brew Coffee 12pk | 8 | $12.50 | $100.00 | $100.00 | ✅ Pass |
| 4 | Coconut Water 12pk | 20 | $6.75 | $135.00 | $135.00 | ✅ Pass |
| 5 | Lemonade Concentrate 2L | 15 | $2.90 | $43.50 | $43.50 | ✅ Pass |

### 5.2 Subtotal Check

Does `Sum(all line totals) == Invoice Subtotal`?

```
$51.00 + $114.00 + $100.00 + $135.00 + $43.50 = $443.50

Invoice Subtotal: $443.50

Difference: $0.00 (Tolerance: ±$0.02)

Result: ✅ Pass
```

### 5.3 Tax Check

Does `Subtotal × Tax Rate == Tax Amount`?

```
$443.50 × 8.5% = $37.6975 → rounded to $37.70

Invoice Tax Amount: $37.70

Difference: $0.00 (Tolerance: ±$0.02)

Result: ✅ Pass
```

### 5.4 Grand Total Check

Does `Subtotal + Tax − Discount == Grand Total`?

```
$443.50 + $37.70 − $0.00 = $481.20

Invoice Grand Total: $481.20

Difference: $0.00 (Tolerance: ±$0.02)

Result: ✅ Pass
```

### 5.5 Date Integrity Check

Is `Due Date >= Invoice Date`?

```
Invoice Date: 2026-06-23
Due Date:     2026-07-23

Result: ✅ Pass (Due Date is 30 days after Invoice Date)
```

### 5.6 Duplicate Detection

Has this invoice been processed before?

```
Query: SELECT id FROM invoices
       WHERE organization_id = 'org_freshmart'
       AND vendor_tax_id = '94-3218756'
       AND invoice_number = 'PCD-2026-08743'
       AND invoice_date = '2026-06-23'
       LIMIT 1;

Result: ✅ No duplicate found
```

### 5.7 Confidence Scoring

The system calculates a **composite confidence score** to determine whether the invoice can be committed automatically or needs human review:

```
Composite Confidence Score:

  = 0.30 × mean_ocr_confidence
  + 0.50 × mean_llm_logprob_normalized
  + 0.20 × validation_pass_ratio

  = 0.30 × 1.00    (digital PDF — no OCR, perfect confidence)
  + 0.50 × 0.96    (high LLM generation confidence)
  + 0.20 × 1.00    (all 8 validation rules passed)

  = 0.30 + 0.48 + 0.20

  = 0.98
```

| Metric | Value |
|---|---|
| **Composite Confidence** | **0.98** |
| **Threshold for Auto-Commit** | 0.85 |
| **Decision** | ✅ **VALIDATED** — No human review required |

### 5.8 Validation Summary

```json
{
  "document_uuid": "a7f3d912-4b6c-4e8a-b3d1-9f2e7a8c5d04",
  "validation_status": "VALIDATED",
  "composite_confidence": 0.98,
  "checks_run": 8,
  "checks_passed": 8,
  "checks_failed": 0,
  "errors": [],
  "requires_review": false
}
```

The document status transitions from `INGESTED` → **`VALIDATED`**.

---

## Step 6 — Database Storage

Once validation passes, the system commits the structured data to PostgreSQL in a **single atomic transaction**. If any write fails, the entire transaction is rolled back — no orphaned records.

### 6.1 Vendor Record

The system first checks if Pacific Coast Distributors already exists in the `vendors` table (matched by `organization_id` + `tax_id`). If not, a new vendor record is created:

```
Table: vendors
```

| Column | Value |
|---|---|
| `id` | `v_8b2e1a04-...` |
| `organization_id` | `org_freshmart` |
| `name` | Pacific Coast Distributors |
| `tax_id` | 94-3218756 |
| `address` | 2400 Harbor Blvd, Suite 110, Oakland, CA 94607 |
| `phone` | (510) 555-0142 |
| `email` | *null* |
| `created_at` | 2026-06-23T14:32:07Z |

### 6.2 Invoice Record

```
Table: invoices
```

| Column | Value |
|---|---|
| `id` | `a7f3d912-4b6c-4e8a-b3d1-9f2e7a8c5d04` |
| `organization_id` | `org_freshmart` |
| `vendor_id` | `v_8b2e1a04-...` |
| `invoice_number` | PCD-2026-08743 |
| `invoice_date` | 2026-06-23 |
| `due_date` | 2026-07-23 |
| `subtotal` | 443.50 |
| `tax_amount` | 37.70 |
| `discount_amount` | 0.00 |
| `grand_total` | 481.20 |
| `currency` | USD |
| `status` | VALIDATED |
| `source_type` | digital_pdf |
| `composite_confidence` | 0.9800 |
| `raw_file_url` | `s3://invoices/org_freshmart/2026/06/a7f3d912.pdf` |
| `file_hash` | `a3c7f9e1d2b4...` |
| `extraction_model` | gpt-4o-mini |
| `created_at` | 2026-06-23T14:32:07Z |

### 6.3 Invoice Item Records

```
Table: invoice_items
```

| `sort_order` | `description` | `quantity` | `unit_price` | `line_total` | `tax_rate` | `discount` | `product_sku` |
|---|---|---|---|---|---|---|---|
| 1 | Sparkling Water 24pk | 12.0000 | 4.2500 | 51.00 | *null* | 0.00 | *null* (Phase 3) |
| 2 | Organic Orange Juice 1L | 30.0000 | 3.8000 | 114.00 | *null* | 0.00 | *null* (Phase 3) |
| 3 | Cold Brew Coffee 12pk | 8.0000 | 12.5000 | 100.00 | *null* | 0.00 | *null* (Phase 3) |
| 4 | Coconut Water 12pk | 20.0000 | 6.7500 | 135.00 | *null* | 0.00 | *null* (Phase 3) |
| 5 | Lemonade Concentrate 2L | 15.0000 | 2.9000 | 43.50 | *null* | 0.00 | *null* (Phase 3) |

> **Note**: The `product_sku` column remains `null` in Phase 1. In Phase 3, the Product Memory System will automatically map each line item description to an internal SKU using vector similarity search.

### 6.4 Audit Log Entry

```
Table: audit_logs
```

| Column | Value |
|---|---|
| `id` | `log_c4a1f7...` |
| `invoice_id` | `a7f3d912-...` |
| `user_id` | *null* (system action) |
| `action` | VALIDATED |
| `before_state` | `{"status": "INGESTED"}` |
| `after_state` | `{"status": "VALIDATED", "composite_confidence": 0.98}` |
| `notes` | All 8 validation checks passed. Auto-committed. |
| `created_at` | 2026-06-23T14:32:09Z |

---

## Step 7 — Export

Once the invoice is stored and validated, it is available for export in multiple formats.

### 7.1 CSV Export

Endpoint: `GET /export?organization_id=org_freshmart&status=VALIDATED`

The system joins the `invoices` and `invoice_items` tables and generates a downloadable CSV file. Each line item becomes one row:

```csv
invoice_uuid,invoice_number,invoice_date,due_date,vendor_name,vendor_tax_id,item_description,quantity,unit_price,line_total,tax_rate,subtotal,tax_amount,discount_amount,grand_total,currency,status
a7f3d912-...,PCD-2026-08743,2026-06-23,2026-07-23,Pacific Coast Distributors,94-3218756,Sparkling Water 24pk,12,4.25,51.00,,443.50,37.70,0.00,481.20,USD,VALIDATED
a7f3d912-...,PCD-2026-08743,2026-06-23,2026-07-23,Pacific Coast Distributors,94-3218756,Organic Orange Juice 1L,30,3.80,114.00,,443.50,37.70,0.00,481.20,USD,VALIDATED
a7f3d912-...,PCD-2026-08743,2026-06-23,2026-07-23,Pacific Coast Distributors,94-3218756,Cold Brew Coffee 12pk,8,12.50,100.00,,443.50,37.70,0.00,481.20,USD,VALIDATED
a7f3d912-...,PCD-2026-08743,2026-06-23,2026-07-23,Pacific Coast Distributors,94-3218756,Coconut Water 12pk,20,6.75,135.00,,443.50,37.70,0.00,481.20,USD,VALIDATED
a7f3d912-...,PCD-2026-08743,2026-06-23,2026-07-23,Pacific Coast Distributors,94-3218756,Lemonade Concentrate 2L,15,2.90,43.50,,443.50,37.70,0.00,481.20,USD,VALIDATED
```

This CSV is compatible with Excel, Google Sheets, and LibreOffice Calc. It can be directly imported into most accounting software.

### 7.2 JSON Export

Endpoint: `GET /invoice/a7f3d912-4b6c-4e8a-b3d1-9f2e7a8c5d04`

Returns the full `InvoiceSchema`-compatible JSON object — the same structured output from Step 4, enriched with database IDs, validation status, and confidence score. This is the format consumed by programmatic integrations and ERP adapters.

```json
{
  "id": "a7f3d912-4b6c-4e8a-b3d1-9f2e7a8c5d04",
  "status": "VALIDATED",
  "composite_confidence": 0.98,
  "invoice_number": "PCD-2026-08743",
  "invoice_date": "2026-06-23",
  "due_date": "2026-07-23",
  "vendor": {
    "id": "v_8b2e1a04-...",
    "name": "Pacific Coast Distributors",
    "tax_id": "94-3218756"
  },
  "subtotal": 443.50,
  "tax_amount": 37.70,
  "discount_amount": 0.00,
  "grand_total": 481.20,
  "currency": "USD",
  "line_items": [
    {
      "sort_order": 1,
      "description": "Sparkling Water 24pk",
      "quantity": 12,
      "unit_price": 4.25,
      "line_total": 51.00
    },
    {
      "sort_order": 2,
      "description": "Organic Orange Juice 1L",
      "quantity": 30,
      "unit_price": 3.80,
      "line_total": 114.00
    },
    {
      "sort_order": 3,
      "description": "Cold Brew Coffee 12pk",
      "quantity": 8,
      "unit_price": 12.50,
      "line_total": 100.00
    },
    {
      "sort_order": 4,
      "description": "Coconut Water 12pk",
      "quantity": 20,
      "unit_price": 6.75,
      "line_total": 135.00
    },
    {
      "sort_order": 5,
      "description": "Lemonade Concentrate 2L",
      "quantity": 15,
      "unit_price": 2.90,
      "line_total": 43.50
    }
  ]
}
```

### 7.3 ERP-Ready Output (Phase 2)

In Phase 2, validated invoices are automatically pushed to connected ERP systems via pluggable adapters. The system transforms the internal `InvoiceSchema` into the target ERP's format. For example, an Odoo-compatible payload:

```json
{
  "partner_id": 4217,
  "ref": "PCD-2026-08743",
  "invoice_date": "2026-06-23",
  "invoice_date_due": "2026-07-23",
  "currency_id": 2,
  "invoice_line_ids": [
    [0, 0, {
      "name": "Sparkling Water 24pk",
      "quantity": 12,
      "price_unit": 4.25,
      "tax_ids": [[6, 0, [1]]]
    }],
    [0, 0, {
      "name": "Organic Orange Juice 1L",
      "quantity": 30,
      "price_unit": 3.80,
      "tax_ids": [[6, 0, [1]]]
    }],
    [0, 0, {
      "name": "Cold Brew Coffee 12pk",
      "quantity": 8,
      "price_unit": 12.50,
      "tax_ids": [[6, 0, [1]]]
    }],
    [0, 0, {
      "name": "Coconut Water 12pk",
      "quantity": 20,
      "price_unit": 6.75,
      "tax_ids": [[6, 0, [1]]]
    }],
    [0, 0, {
      "name": "Lemonade Concentrate 2L",
      "quantity": 15,
      "price_unit": 2.90,
      "tax_ids": [[6, 0, [1]]]
    }]
  ]
}
```

The ERP sync result is logged to `erp_sync_logs`:

| Field | Value |
|---|---|
| `invoice_id` | `a7f3d912-...` |
| `erp_provider` | odoo |
| `erp_record_id` | INV/2026/00487 |
| `sync_status` | SUCCESS |
| `synced_at` | 2026-06-23T14:32:15Z |

---

## Step 8 — Future Intelligence

The invoice's journey does not end at export. Every validated invoice contributes to a growing intelligence layer that transforms raw transaction data into strategic business insight. Here is how this single Pacific Coast Distributors invoice feeds into future platform capabilities:

### 8.1 Vendor Intelligence (Phase 3)

The system learns from every interaction with Pacific Coast Distributors:

- **Prompt Template Caching** — The successful extraction prompt and schema hints used for this invoice are cached in the `vendor_prompt_cache` table, keyed by `vendor_tax_id = 94-3218756`. The next time an invoice arrives from this vendor, the system retrieves the cached strategy instead of building a generic prompt — reducing token consumption by 20%+ and improving extraction speed.
- **Vendor Profile Enrichment** — Pacific Coast Distributors' record accumulates metadata over time: average invoice value, typical line item count, delivery frequency, payment terms patterns, and historical spend volume.
- **Price Deviation Tracking** — If Pacific Coast Distributors charges $5.50 per unit for Sparkling Water 24pk on a future invoice (up from the $4.25 baseline), the system flags a **29.4% price deviation** alert, giving the procurement team time to investigate before payment.

### 8.2 Product Intelligence (Phase 3)

Each line item becomes a data point in the product knowledge base:

- **SKU Auto-Mapping** — "Sparkling Water 24pk" is embedded as a vector and matched against FreshMart's internal product catalog using cosine similarity. If SKU `#BEV-SW-24` exists, the system automatically links it — no manual lookup required.
- **Price History** — The platform tracks that Sparkling Water 24pk was purchased at $4.25/unit on June 23, 2026. Over time, this builds a price-over-time trend for every product across every vendor.
- **Demand Forecasting** — Purchase quantities feed into demand models: FreshMart bought 12 cases of sparkling water, 30 units of organic orange juice. These patterns help predict future inventory needs.

### 8.3 Analytics & Reporting (Phase 4)

The validated invoice data powers real-time dashboards and automated reports:

- **Spend Analytics** — This $481.20 invoice is aggregated into FreshMart's monthly vendor spend totals. Dashboard widgets show spend trends over time, top vendors by volume, and category-level breakdowns.
- **Tax Liability Reports** — The $37.70 tax amount rolls into automated tax liability summaries, broken down by tax jurisdiction and rate.
- **Automated Monthly Reports** — At month-end, the system generates a PDF spend summary including all invoices from June 2026, delivered automatically to configured email recipients.

### 8.4 Natural Language Querying (Phase 4)

Once enough data accumulates, stakeholders can query the invoice repository in plain English:

> **"How much did we spend on beverages from Pacific Coast Distributors this quarter?"**

The NLQ engine translates this to SQL, executes it against a read-only database replica, and returns:

> *"FreshMart spent $2,847.60 on beverages from Pacific Coast Distributors in Q2 2026, across 6 invoices. This represents a 12% increase compared to Q1 2026."*

> **"Which product had the highest price increase from Pacific Coast Distributors in the last 6 months?"**

> *"Cold Brew Coffee 12pk increased from $11.00 to $12.50 per unit (+13.6%) between January and June 2026."*

---

## Complete Visual Flow

The following diagram shows the entire end-to-end journey of this invoice through the platform:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          INVOICE LIFECYCLE                             │
└─────────────────────────────────────────────────────────────────────────┘

    ┌──────────────────────┐
    │   📄 INVOICE         │   Pacific Coast Distributors
    │   PCD-2026-08743     │   Invoice Date: June 23, 2026
    │   $481.20            │   5 line items
    └──────────┬───────────┘
               │
               ▼
    ┌──────────────────────┐
    │   📤 UPLOAD          │   POST /upload
    │                      │   • MIME validation ✅
    │   Status: INGESTED   │   • SHA-256 hash ✅
    │                      │   • Duplicate check ✅
    │                      │   • Store to S3 ✅
    └──────────┬───────────┘
               │
               ▼
    ┌──────────────────────┐
    │   🔍 DETECTION       │   Is there an embedded text layer?
    │                      │
    │   Result: digital_pdf│   YES → Direct text extraction
    │                      │   (OCR bypass — faster, cheaper, 100% accurate)
    └──────────┬───────────┘
               │
               ▼
    ┌──────────────────────┐
    │   📝 EXTRACTION      │   pdfplumber → raw text
    │                      │
    │   47ms               │   Full document text captured
    │   1 page             │   including headers, items, totals
    └──────────┬───────────┘
               │
               ▼
    ┌──────────────────────┐
    │   🤖 AI STRUCTURING  │   OpenAI gpt-4o-mini + Instructor
    │                      │
    │   1,159 tokens       │   Raw text → Structured JSON
    │   $0.000267          │   Schema-enforced via Pydantic
    │   1,843ms            │   5 line items extracted
    └──────────┬───────────┘
               │
               ▼
    ┌──────────────────────┐
    │   ✅ VALIDATION      │   8 checks run:
    │                      │   • 5× line item math ✅
    │   Confidence: 0.98   │   • Subtotal check ✅
    │   Status: VALIDATED  │   • Tax check ✅
    │                      │   • Grand total check ✅
    │                      │   • Date integrity ✅
    │                      │   • Duplicate check ✅
    └──────────┬───────────┘
               │
               ▼
    ┌──────────────────────┐
    │   💾 DATABASE        │   PostgreSQL (atomic transaction)
    │                      │
    │   vendors ──── 1 row │   • Vendor record (upsert)
    │   invoices ─── 1 row │   • Invoice header
    │   invoice_items 5 rows   • 5 line items
    │   audit_logs ─ 1 row │   • Processing audit trail
    └──────────┬───────────┘
               │
               ▼
    ┌──────────────────────┐
    │   📊 EXPORT          │   Multiple output formats:
    │                      │
    │   CSV ─────── ✅     │   • Spreadsheet-compatible
    │   JSON ────── ✅     │   • API / integration-ready
    │   ERP Push ── 🔜     │   • Odoo / QuickBooks (Phase 2)
    └──────────┬───────────┘
               │
               ▼
    ┌──────────────────────┐
    │   🧠 INTELLIGENCE    │   Future platform capabilities:
    │                      │
    │   Vendor Memory ─ 🔜 │   • Prompt caching, price tracking
    │   Product Memory 🔜  │   • SKU auto-mapping via vectors
    │   Analytics ──── 🔜  │   • Spend dashboards, tax reports
    │   NLQ ────────── 🔜  │   • "How much did we spend on...?"
    └──────────────────────┘


    ┌─────────────────────────────────────────────────────────────────────┐
    │                                                                     │
    │  Total Processing Time:  ~2 seconds (digital PDF path)             │
    │  Total AI Cost:          < $0.001                                  │
    │  Human Intervention:     None required (confidence 0.98)           │
    │  Data Accuracy:          All 8 validation checks passed            │
    │                                                                     │
    └─────────────────────────────────────────────────────────────────────┘
```

---

## Summary

This workflow demonstrates the core value proposition of the Invoice Intelligence Platform:

1. **A single invoice upload replaces an entire manual data-entry workflow.** What previously required a human to read, interpret, type, double-check, and file now happens automatically in under 2 seconds.

2. **AI handles the semantic complexity.** Different vendors, different layouts, different formatting — the AI structuring layer understands what the document means, not just what characters appear on the page.

3. **Validation catches errors that humans miss.** Every mathematical relationship is verified before data enters the system. No rounding errors, no transposed digits, no miscalculated totals.

4. **Every invoice makes the system smarter.** Today's invoice becomes tomorrow's training signal — teaching the system Pacific Coast Distributors' layout, building price baselines, and enriching the product catalog.

5. **The data is immediately actionable.** CSV for spreadsheets, JSON for APIs, ERP push for accounting systems, dashboards for executives, natural language queries for anyone.

This is the journey of one invoice. Multiply it by thousands, across dozens of vendors, over months and years — and the platform transforms from an extraction tool into a **business intelligence engine**.
