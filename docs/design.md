# System Design

## Purpose

This document is the **Technical Design Document (TDD)** for the Invoice Intelligence Platform. It describes, at an engineering implementation level, how each component of the platform is built, wired together, and tested. It is the primary reference for engineers beginning implementation and covers internal service contracts, database schemas, API specifications, error handling patterns, and scalability strategies.

This document is **not** a repeat of `architecture.md` (which defines the why and what) or `requirements.md` (which defines acceptance criteria). This document defines **how** the system is built.

---

## Design Goals

1. **Correctness over Speed**: Every document must pass mathematical validation checks before being committed to the database. A fast but incorrect result is always worse than a slower correct one.
2. **Fail Loudly, Recover Gracefully**: Pipeline stages must surface errors explicitly with structured error codes. Retry logic must be applied to transient failures (network timeouts, rate limits), not to logic failures (bad schemas, invalid documents).
3. **Separation of Concerns**: Each service component owns its input contract, output contract, and error domain. No service may write to another service's table directly.
4. **Cost Awareness by Design**: The ingestion router determines the cheapest viable extraction path for each document type. Digital-native PDFs always bypass OCR. Prompt sizes are trimmed before any LLM call.
5. **Schema-First Engineering**: All data flowing between services is typed using Pydantic models. Untyped raw dictionaries are not passed between layers.
6. **Auditability from Day One**: Every state transition in a document's lifecycle is appended to the audit log table. This cannot be disabled or bypassed.

---

## High-Level System Workflow

The following is the complete end-to-end pipeline for a single document ingestion event.

```
[Client: API or CLI]
        |
        | POST /upload  (multipart/form-data)
        v
+------------------------------+
|      Upload Service          |
|  - MIME type validation      |
|  - File size validation      |
|  - SHA-256 hash calculation  |
|  - Duplicate check           |
|  - Write raw file to Object  |
|    Storage (S3/Supabase)     |
|  - Create invoice record     |
|    in PostgreSQL (status:    |
|    INGESTED)                 |
+------------------------------+
        |
        | (document_uuid, file_url, mime_type)
        v
+------------------------------+
|  Document Processing Service |
|  - Route detection:          |
|    Digital PDF → Text        |
|    Scanned PDF / Image → OCR |
+------------------------------+
       /                \
      /                  \
[Digital PDF]         [Scanned / Image]
     v                       v
+------------+      +------------------+
| PDF Text   |      |   OCR Service    |
| Extractor  |      | (PaddleOCR,      |
| (pdfplumber|      |  EasyOCR, Cloud) |
| pypdf)     |      +------------------+
+------------+              |
      \                     /
       \                   /
        v                 v
       (Unified OCRTokenList / RawTextBlock)
               |
               v
+------------------------------+
|   AI Structuring Service     |
|  - Build extraction prompt   |
|  - Call OpenAI API           |
|  - Enforce Pydantic schema   |
|    (Instructor)              |
|  - Return InvoiceSchema      |
+------------------------------+
               |
               v
+------------------------------+
|     Validation Service       |
|  - Line item math checks     |
|  - Subtotal / total checks   |
|  - Tax checks                |
|  - Date integrity checks     |
|  - Rounding tolerance        |
|  - Confidence scoring        |
|  - Set document status:      |
|    VALIDATED or REVIEW       |
+------------------------------+
              / \
             /   \
     [VALIDATED] [REVIEW]
          |            |
          |            | → flag record, notify
          v
+------------------------------+
|      Storage Service         |
|  - Write InvoiceSchema to    |
|    invoices + invoice_items  |
|    tables                    |
|  - Write audit log entry     |
+------------------------------+
               |
               v
+------------------------------+
|      Export Service          |
|  - GET /export → CSV         |
|  - Future: ERP adapter push  |
+------------------------------+
```

---

## Component Design

---

### Upload Service

#### Responsibilities
- Accept and validate raw document files (PDF, PNG, JPEG).
- Perform an early-stage duplicate check on file hashes and invoice headers.
- Write the raw file to Object Storage.
- Create the initial database record for the document with status `INGESTED`.
- Return a `document_uuid` to the calling client.

#### Inputs
- `POST /upload` multipart form body:
  - `file`: Binary file content (PDF, PNG, JPEG).
  - `organization_id`: UUID string (required for multi-tenant routing).

#### Outputs
- `202 Accepted` response with JSON body:
  ```json
  {
    "document_uuid": "a7f3d912-...",
    "status": "INGESTED",
    "file_url": "https://storage.example.com/invoices/a7f3d912-....pdf",
    "created_at": "2026-06-18T10:00:00Z"
  }
  ```

#### Validation Rules
1. Accepted MIME types: `application/pdf`, `image/png`, `image/jpeg`.
2. Maximum file size: 25MB.
3. Minimum file size: 1KB (reject empty files).
4. SHA-256 hash must not match any existing `invoice.file_hash` for the same `organization_id`.

#### Error Handling
| Condition | HTTP Status | Error Code |
|---|---|---|
| Unsupported MIME type | 415 | `ERR_UNSUPPORTED_FORMAT` |
| File too large | 413 | `ERR_FILE_TOO_LARGE` |
| Duplicate file hash | 409 | `ERR_DUPLICATE_DOCUMENT` |
| Storage write failure | 503 | `ERR_STORAGE_UNAVAILABLE` |

---

### Document Processing Service

#### Responsibilities
- Classify each uploaded document as either a **digital-native PDF** or a **scanned/image** document.
- Route to the correct extraction pipeline.
- Normalize all extraction outputs into a single `TextExtractionResult` type before passing to the AI Structuring Service.

#### Document Type Detection

```python
def detect_document_type(file_path: str) -> Literal["digital_pdf", "scan_or_image"]:
    """
    Opens the PDF and checks for embedded text layers.
    Falls back to scan route if no readable text is found.
    """
    if file_path.endswith(".pdf"):
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                if page.extract_text():
                    return "digital_pdf"
        return "scan_or_image"
    return "scan_or_image"  # PNG / JPEG
```

#### Digital PDF Flow
1. Open file with `pdfplumber`.
2. Extract text from each page: `page.extract_text()`.
3. Extract page-level word positions for tables: `page.extract_words()`.
4. Concatenate all pages into a single `TextExtractionResult` struct with page metadata.
5. No image preprocessing is applied.

#### OCR Flow
1. For PDFs: Convert each page to PNG using `pdf2image` at 300 DPI.
2. Apply preprocessing: deskew, contrast enhancement.
3. Pass each page image to the active OCR driver (see OCR Service).
4. Collect `OCRToken` lists per page.
5. Flatten all tokens into a single `TextExtractionResult` struct.

#### Unified Output Contract

```python
@dataclass
class TextExtractionResult:
    document_uuid: str
    source_type: Literal["digital_pdf", "ocr"]
    pages: List[PageTextBlock]
    full_text: str                   # Concatenated, whitespace-trimmed
    extraction_duration_ms: int

@dataclass
class PageTextBlock:
    page_number: int
    raw_text: str
    words: List[OCRToken]

@dataclass
class OCRToken:
    text: str
    confidence: float                # 0.0 – 1.0
    x0: float
    y0: float
    x1: float
    y1: float
    page: int
```

---

### OCR Service

#### Provider Abstraction

All OCR providers are wrapped behind a common interface. Swapping providers or adding new ones does not require changes to the Document Processing Service.

```python
from abc import ABC, abstractmethod

class OCRDriver(ABC):
    @abstractmethod
    def extract(self, image_bytes: bytes) -> List[OCRToken]:
        """
        Accepts raw image bytes.
        Returns a list of OCRToken objects for each recognized word.
        """
        pass
```

#### PaddleOCR (Primary Local Driver)
- Library: `paddleocr`
- Language model: `en` (configurable).
- Returns bounding boxes, text, and confidence scores.
- Runs in-process; no external API call.

```python
class PaddleOCRDriver(OCRDriver):
    def __init__(self):
        from paddleocr import PaddleOCR
        self._engine = PaddleOCR(use_angle_cls=True, lang="en", show_log=False)

    def extract(self, image_bytes: bytes) -> List[OCRToken]:
        result = self._engine.ocr(image_bytes, cls=True)
        tokens = []
        for line in result[0]:
            box, (text, confidence) = line
            x0, y0 = box[0]
            x1, y1 = box[2]
            tokens.append(OCRToken(text=text, confidence=confidence,
                                   x0=x0, y0=y0, x1=x1, y1=y1, page=0))
        return tokens
```

#### EasyOCR (Fallback Local Driver)
- Library: `easyocr`
- Used if PaddleOCR installation fails or returns zero tokens.
- Initialized with `["en"]` language model.

#### Cloud Vision (Future Driver)
- Target: Google Cloud Vision API `document_text_detection` endpoint.
- Activated via environment flag: `OCR_PROVIDER=google_vision`.
- Must implement the same `OCRDriver` interface.

#### Driver Selection Logic
```python
def get_ocr_driver() -> OCRDriver:
    provider = os.getenv("OCR_PROVIDER", "paddleocr")
    if provider == "paddleocr":
        return PaddleOCRDriver()
    elif provider == "easyocr":
        return EasyOCRDriver()
    elif provider == "google_vision":
        return GoogleVisionDriver()
    raise ValueError(f"Unknown OCR provider: {provider}")
```

#### Failure Handling
- If OCR returns zero tokens from a non-empty image, raise `OCRExtractionError`.
- The Document Processing Service catches `OCRExtractionError`, marks the document `FAILED_OCR`, and writes an audit log entry.
- Retrying an `OCRExtractionError` is not automatic; it requires a manual re-trigger from the review queue.

---

### AI Structuring Service

#### OpenAI Integration
- Library: `openai` (Python SDK, v1+).
- Library: `instructor` (for structured outputs from OpenAI).
- Model selection: `gpt-4o-mini` for Phase 1 (lower cost); `gpt-4o` as fallback for complex layouts.
- API key sourced from environment variable `OPENAI_API_KEY`.

#### Prompt Engineering Strategy

Prompts are constructed in four stages:

1. **System Prompt**: Declares the role as a structured invoice parser with strict schema adherence.
2. **Schema Context Hint**: Inlines the target field names as a minimal hint to guide field extraction.
3. **Document Text Block**: The trimmed, whitespace-normalized full document text.
4. **Completion Guard**: Instructs the model to return `null` for fields it cannot locate with reasonable confidence, never to hallucinate values.

```python
SYSTEM_PROMPT = """
You are an invoice extraction engine.
Extract all fields from the provided invoice text and return a valid JSON object
matching the given schema exactly. If a field is not present in the document,
return null for that field. Do NOT invent or estimate values.
"""

def build_extraction_prompt(document_text: str) -> str:
    trimmed = " ".join(document_text.split())   # Collapse whitespace
    return f"Extract all invoice fields from the following text:\n\n{trimmed}"
```

#### Structured Output Design (Pydantic Models)

```python
from pydantic import BaseModel
from typing import Optional, List
from datetime import date
from decimal import Decimal

class InvoiceLineItem(BaseModel):
    description: str
    quantity: Decimal
    unit_price: Decimal
    line_total: Decimal
    tax_rate: Optional[Decimal] = None
    discount: Optional[Decimal] = None

class InvoiceSchema(BaseModel):
    invoice_number: Optional[str]
    invoice_date: Optional[date]
    due_date: Optional[date]
    vendor_name: Optional[str]
    vendor_tax_id: Optional[str]
    vendor_address: Optional[str]
    subtotal: Optional[Decimal]
    tax_amount: Optional[Decimal]
    discount_amount: Optional[Decimal]
    grand_total: Optional[Decimal]
    currency: Optional[str] = "USD"
    line_items: List[InvoiceLineItem] = []
    extraction_notes: Optional[str] = None
```

#### Instructor Integration

```python
import instructor
from openai import OpenAI

client = instructor.from_openai(OpenAI())

def structure_invoice(text: str) -> InvoiceSchema:
    return client.chat.completions.create(
        model="gpt-4o-mini",
        response_model=InvoiceSchema,
        messages=[
            {"role": "system",  "content": SYSTEM_PROMPT},
            {"role": "user",    "content": build_extraction_prompt(text)},
        ],
        max_retries=2,
    )
```

#### Retry Logic
- `instructor` handles automatic retry up to `max_retries=2` when the model returns a JSON structure that does not conform to the schema.
- Network-level errors (timeouts, 500-series OpenAI errors) are caught by the calling service and retried with exponential backoff: delays of 2s, 4s, 8s, up to 3 attempts.
- After 3 total failures the document is marked `FAILED_AI_STRUCTURING` and written to the audit log.

#### Timeout Handling
- Per-request HTTP timeout: 30 seconds.
- If timeout is hit, raises `OpenAITimeoutError`; treated as a retriable transient failure.

#### Token Cost Monitoring
- After each API call, log `prompt_tokens` and `completion_tokens` from the usage object to the `llm_usage_log` table.
- Alert if a single document consumes more than 6,000 tokens (indicates an extremely long or noisy document).

---

### Validation Service

#### Mathematical Validation

Each rule is evaluated in isolation. Failures are collected and returned as a list, not short-circuited.

```python
ROUNDING_TOLERANCE = Decimal("0.02")

def validate_line_items(invoice: InvoiceSchema) -> List[ValidationError]:
    errors = []
    for i, item in enumerate(invoice.line_items):
        expected = (item.quantity * item.unit_price).quantize(Decimal("0.01"))
        if abs(expected - item.line_total) > ROUNDING_TOLERANCE:
            errors.append(ValidationError(
                rule="LINE_ITEM_MATH",
                field=f"line_items[{i}].line_total",
                expected=str(expected),
                actual=str(item.line_total),
            ))
    return errors

def validate_subtotal(invoice: InvoiceSchema) -> List[ValidationError]:
    if invoice.subtotal is None or not invoice.line_items:
        return []
    computed = sum(item.line_total for item in invoice.line_items)
    if abs(computed - invoice.subtotal) > ROUNDING_TOLERANCE:
        return [ValidationError(rule="SUBTOTAL_MISMATCH",
                                expected=str(computed),
                                actual=str(invoice.subtotal))]
    return []

def validate_grand_total(invoice: InvoiceSchema) -> List[ValidationError]:
    if None in (invoice.subtotal, invoice.grand_total):
        return []
    computed = invoice.subtotal + (invoice.tax_amount or 0) - (invoice.discount_amount or 0)
    if abs(computed - invoice.grand_total) > ROUNDING_TOLERANCE:
        return [ValidationError(rule="GRAND_TOTAL_MISMATCH",
                                expected=str(computed),
                                actual=str(invoice.grand_total))]
    return []
```

#### Tax Validation
- Validates that `tax_amount == subtotal * (tax_rate / 100)` when tax rate is available.
- Flags mismatches exceeding the rounding tolerance.
- Does not block processing when `tax_rate` is absent from the document.

#### Date Integrity Validation
```python
def validate_dates(invoice: InvoiceSchema) -> List[ValidationError]:
    errors = []
    if invoice.invoice_date and invoice.due_date:
        if invoice.due_date < invoice.invoice_date:
            errors.append(ValidationError(rule="DUE_DATE_BEFORE_INVOICE_DATE"))
    return errors
```

#### Duplicate Detection
- **Hash check**: `SELECT id FROM invoices WHERE file_hash = :hash AND organization_id = :org_id LIMIT 1` is executed on upload, before any processing.
- **Header check**: On write, a unique constraint on `(organization_id, vendor_tax_id, invoice_number, invoice_date)` causes a `UniqueConstraintError` which is caught and converted to a `409 Conflict` API response.

#### Confidence Scoring

```
Composite Confidence Score (0.0 – 1.0):

  = 0.30 × mean_ocr_confidence
  + 0.50 × mean_llm_logprob_normalized
  + 0.20 × validation_pass_ratio

Where:
  mean_ocr_confidence     = average OCRToken.confidence across all tokens
                            (1.0 for digital PDF path, since no OCR is run)
  mean_llm_logprob_normalized = maps raw logprob to [0,1] range
  validation_pass_ratio   = (rules_passed / total_rules_run)
```

The composite score is stored on the `invoices.composite_confidence` column (float, 0.0–1.0).

#### Manual Review Triggers
A document is set to status `REVIEW` if any of the following are true:
- `composite_confidence < 0.85`
- Any validation rule failure is present.
- `invoice.grand_total` is `null` after extraction.
- Zero line items were extracted.

---

### Storage Service

#### Object Storage Design
- **Provider**: AWS S3 or Supabase Storage (configured via `STORAGE_BACKEND` env var).
- **Bucket structure**: `{bucket_name}/{organization_id}/{year}/{month}/{document_uuid}.pdf`
- **Access**: Pre-signed URLs with 1-hour expiry are returned to the client. Files are never publicly readable by default.
- **PostgreSQL column**: `invoices.raw_file_url` stores the permanent bucket path (not the signed URL).

#### PostgreSQL Design
See **Database Design** section for full schema.

#### Metadata Storage
After validation, the following writes are executed in a single database transaction:
1. `INSERT INTO invoices (...)` with all header fields.
2. `INSERT INTO invoice_items (...) VALUES (...)` for each line item in bulk.
3. `INSERT INTO audit_logs (...)` with action `VALIDATED` or `REVIEW`.

If any write fails, the transaction is rolled back. The document status remains `INGESTED` and an error is logged.

#### Document References
- `invoices.raw_file_url`: The permanent object storage path.
- `invoices.file_hash`: SHA-256 hex digest of the raw file, used for deduplication.

#### Retention Strategy
- Database records: Retained for 7 years minimum.
- Object storage files: Lifecycle rules set to 7 years, then archived to cold storage (Glacier or equivalent).
- Soft-delete pattern: Records are never hard-deleted; a `deleted_at` timestamp is set instead.

---

### Export Service

#### CSV Export
- Endpoint: `GET /export?organization_id=:id&status=VALIDATED`
- Queries the `invoices` and `invoice_items` tables, joining on `invoice_id`.
- Uses Python's `csv` module to write headers + rows to an in-memory `StringIO` buffer.
- Returns as `Content-Type: text/csv; charset=utf-8` with a `Content-Disposition: attachment; filename=invoices_export.csv` header.

**CSV Column Order**:
```
invoice_uuid, invoice_number, invoice_date, due_date, vendor_name, vendor_tax_id,
item_description, quantity, unit_price, line_total, tax_rate,
subtotal, tax_amount, discount_amount, grand_total, currency, status
```

#### JSON Export
- Endpoint: `GET /invoice/{id}` returns the full `InvoiceSchema`-compatible JSON object.
- Used for ERP adapter integration in Phase 2.

#### Future ERP Export Layer
- A pluggable `ERPAdapter` interface will be called after validation in Phase 2:
  ```python
  class ERPAdapter(ABC):
      @abstractmethod
      def sync(self, invoice: InvoiceSchema) -> ERPSyncResult:
          pass
  ```
- Phase 2 implementations: `OdooAdapter`, `QuickBooksAdapter`, `NetSuiteAdapter`.
- Sync results are written to a `erp_sync_logs` table.

---

## Database Design

### Schema

#### `organizations`
```sql
CREATE TABLE organizations (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name          VARCHAR(255) NOT NULL,
    slug          VARCHAR(100) NOT NULL UNIQUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

#### `users`
```sql
CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id),
    email           VARCHAR(255) NOT NULL UNIQUE,
    name            VARCHAR(255),
    role            VARCHAR(50) NOT NULL DEFAULT 'reviewer',  -- admin, reviewer, viewer
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ
);
CREATE INDEX idx_users_org ON users(organization_id);
```

#### `vendors`
```sql
CREATE TABLE vendors (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id),
    name            VARCHAR(255) NOT NULL,
    tax_id          VARCHAR(100),
    address         TEXT,
    phone           VARCHAR(50),
    email           VARCHAR(255),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (organization_id, tax_id)
);
CREATE INDEX idx_vendors_org ON vendors(organization_id);
```

#### `invoices`
```sql
CREATE TABLE invoices (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id      UUID NOT NULL REFERENCES organizations(id),
    vendor_id            UUID REFERENCES vendors(id),
    invoice_number       VARCHAR(100),
    invoice_date         DATE,
    due_date             DATE,
    subtotal             NUMERIC(14, 2),
    tax_amount           NUMERIC(14, 2),
    discount_amount      NUMERIC(14, 2) DEFAULT 0,
    grand_total          NUMERIC(14, 2),
    currency             VARCHAR(10) DEFAULT 'USD',
    status               VARCHAR(50) NOT NULL DEFAULT 'INGESTED',
    source_type          VARCHAR(20) NOT NULL,              -- digital_pdf | ocr
    composite_confidence NUMERIC(5, 4),
    raw_file_url         TEXT NOT NULL,
    file_hash            VARCHAR(64) NOT NULL,
    extraction_model     VARCHAR(100),
    ingested_by          UUID REFERENCES users(id),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at           TIMESTAMPTZ,
    UNIQUE (organization_id, vendor_id, invoice_number, invoice_date)
);
CREATE INDEX idx_invoices_org       ON invoices(organization_id);
CREATE INDEX idx_invoices_status    ON invoices(status);
CREATE INDEX idx_invoices_file_hash ON invoices(organization_id, file_hash);
```

#### `invoice_items`
```sql
CREATE TABLE invoice_items (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id      UUID NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    description     TEXT NOT NULL,
    quantity        NUMERIC(12, 4) NOT NULL,
    unit_price      NUMERIC(14, 4) NOT NULL,
    line_total      NUMERIC(14, 2) NOT NULL,
    tax_rate        NUMERIC(6, 4),
    discount        NUMERIC(14, 2) DEFAULT 0,
    product_sku     VARCHAR(100),         -- Populated in Phase 3
    sort_order      INT NOT NULL DEFAULT 0
);
CREATE INDEX idx_items_invoice ON invoice_items(invoice_id);
```

#### `products` (Phase 3)
```sql
CREATE TABLE products (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id),
    sku             VARCHAR(100) NOT NULL,
    name            VARCHAR(255) NOT NULL,
    description     TEXT,
    category        VARCHAR(100),
    -- embedding_vector VECTOR(1536),  -- Enabled in Phase 3 with pgvector
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (organization_id, sku)
);
```

#### `audit_logs`
```sql
CREATE TABLE audit_logs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id      UUID NOT NULL REFERENCES invoices(id),
    user_id         UUID REFERENCES users(id),         -- NULL if system action
    action          VARCHAR(100) NOT NULL,              -- INGESTED, VALIDATED, REVIEW, CORRECTED, SYNCED
    before_state    JSONB,
    after_state     JSONB,
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_audit_invoice ON audit_logs(invoice_id);
CREATE INDEX idx_audit_created ON audit_logs(created_at DESC);
```

#### `llm_usage_log`
```sql
CREATE TABLE llm_usage_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id      UUID REFERENCES invoices(id),
    model           VARCHAR(100) NOT NULL,
    prompt_tokens   INT NOT NULL,
    completion_tokens INT NOT NULL,
    total_tokens    INT NOT NULL,
    cost_usd        NUMERIC(10, 6),
    duration_ms     INT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

#### `reports`
```sql
CREATE TABLE reports (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id),
    title           VARCHAR(255) NOT NULL,
    type            VARCHAR(100) NOT NULL,   -- csv_export, spend_summary, vendor_report
    parameters      JSONB,
    output_url      TEXT,
    created_by      UUID REFERENCES users(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### ASCII ER Diagram

```
+-------------------+            +-------------------+
|   organizations   |1          *|      users        |
|-------------------|<-----------|-------------------|
| id (PK)           |            | id (PK)           |
| name              |            | organization_id   |
| slug              |            | email             |
+-------------------+            | role              |
        |1                       +-------------------+
        |
        |*
+-------------------+            +-------------------+
|     vendors       |            |     products      |
|-------------------|            |  (Phase 3)        |
| id (PK)           |            |-------------------|
| organization_id   |            | id (PK)           |
| name, tax_id      |            | organization_id   |
+-------------------+            | sku, name         |
        |1                       +-------------------+
        |
        |*
+-------------------+
|     invoices      |
|-------------------|
| id (PK)           |
| organization_id   |
| vendor_id (FK)    |
| invoice_number    |
| grand_total       |
| status            |
| file_hash         |
| raw_file_url      |
+-------------------+
        |1         |1
        |           +------------------+
        |*                             |*
+-------------------+      +-------------------+
|  invoice_items    |      |    audit_logs     |
|-------------------|      |-------------------|
| id (PK)           |      | id (PK)           |
| invoice_id (FK)   |      | invoice_id (FK)   |
| description       |      | user_id (FK)      |
| quantity          |      | action            |
| unit_price        |      | before_state      |
| line_total        |      | after_state       |
+-------------------+      +-------------------+
```

---

## API Design

All API responses follow a standard envelope:
```json
{
  "success": true,
  "data": {},
  "error": null,
  "request_id": "req_abc123"
}
```

---

### `POST /upload`
Uploads a raw invoice file to the platform.

**Request**:
- `Content-Type: multipart/form-data`
- Fields: `file` (binary), `organization_id` (string UUID)

**Success Response `202 Accepted`**:
```json
{
  "success": true,
  "data": {
    "document_uuid": "a7f3d912-...",
    "status": "INGESTED",
    "file_url": "https://storage.../invoices/a7f3d912.pdf",
    "created_at": "2026-06-18T10:00:00Z"
  }
}
```

**Error Responses**:
| Status | Code | Reason |
|---|---|---|
| 400 | `ERR_MISSING_FILE` | No file attached |
| 409 | `ERR_DUPLICATE_DOCUMENT` | File hash already exists |
| 413 | `ERR_FILE_TOO_LARGE` | File exceeds 25MB |
| 415 | `ERR_UNSUPPORTED_FORMAT` | Non-PDF/PNG/JPEG MIME type |

---

### `POST /process`
Triggers the full extraction pipeline for an uploaded document.

**Request Body**:
```json
{ "document_uuid": "a7f3d912-..." }
```

**Success Response `200 OK`**:
```json
{
  "success": true,
  "data": {
    "document_uuid": "a7f3d912-...",
    "status": "VALIDATED",
    "composite_confidence": 0.96,
    "validation_errors": [],
    "processing_duration_ms": 4210
  }
}
```

**Error Responses**:
| Status | Code | Reason |
|---|---|---|
| 404 | `ERR_DOCUMENT_NOT_FOUND` | UUID not in database |
| 422 | `ERR_EXTRACTION_FAILED` | OCR or AI structuring produced no usable output |
| 500 | `ERR_PIPELINE_FAILURE` | Unrecoverable internal processing error |

---

### `GET /invoice/{id}`
Returns the fully structured data for a single invoice.

**Response `200 OK`**:
```json
{
  "success": true,
  "data": {
    "id": "a7f3d912-...",
    "invoice_number": "INV-2026-009",
    "invoice_date": "2026-06-01",
    "vendor_name": "Acme Supplies Ltd.",
    "grand_total": "1842.50",
    "status": "VALIDATED",
    "line_items": [
      {
        "description": "M4 Hex Bolts x100",
        "quantity": "10",
        "unit_price": "12.50",
        "line_total": "125.00"
      }
    ]
  }
}
```

---

### `GET /invoices`
Returns a paginated list of invoices for an organization.

**Query Parameters**:
- `organization_id` (required)
- `status` (optional): `VALIDATED`, `REVIEW`, `INGESTED`, `FAILED_*`
- `page` (default: 1), `page_size` (default: 50, max: 200)

**Response `200 OK`**:
```json
{
  "success": true,
  "data": {
    "items": [...],
    "total": 482,
    "page": 1,
    "page_size": 50
  }
}
```

---

### `GET /export`
Generates and downloads a CSV export of validated invoices.

**Query Parameters**:
- `organization_id` (required)
- `status` (default: `VALIDATED`)
- `from_date`, `to_date` (ISO date format, optional)

**Response `200 OK`**:
- `Content-Type: text/csv; charset=utf-8`
- `Content-Disposition: attachment; filename=invoices_2026_06.csv`

---

### `GET /health`
Health check endpoint for load balancers and uptime monitoring.

**Response `200 OK`**:
```json
{
  "status": "healthy",
  "database": "connected",
  "storage": "connected",
  "version": "1.0.0"
}
```

---

## Error Handling Design

### OCR Failures
- **Cause**: Zero tokens returned, image too blurred to read, or driver crash.
- **Action**: Mark document `FAILED_OCR`. Write audit log entry with raw exception text. Do not retry automatically.
- **Recovery**: Document can be manually re-queued from the review workflow (Phase 2).

### OpenAI API Failures
- **Transient** (timeout, 503): Retry up to 3 times with exponential backoff (2s, 4s, 8s). After 3 failures, mark document `FAILED_AI_STRUCTURING`.
- **Client error** (400, 401): Log immediately, do not retry. Mark as `FAILED_AI_STRUCTURING`.
- **Schema validation failure**: `instructor` retries internally up to 2 times. After failure, mark document `FAILED_AI_STRUCTURING`.

### Validation Failures
- Not treated as system errors. Validation failures are business outcomes.
- Store the list of `ValidationError` objects in the `invoices.validation_errors` JSON column.
- Set `status = REVIEW`, write audit log.

### Storage Failures
- Object Storage write failure: Retry twice, then return `503 ERR_STORAGE_UNAVAILABLE` to the client. Do not create a database record.
- PostgreSQL write failure: Roll back the full transaction. Document remains in `INGESTED` state.

### Retry Logic Summary

| Failure Type | Retried? | Max Retries | Backoff |
|---|---|---|---|
| Network timeout (OpenAI) | Yes | 3 | Exponential (2s base) |
| OCR zero-token result | No | — | Manual only |
| Schema validation (Instructor) | Yes (internal) | 2 | Immediate |
| S3/Storage write error | Yes | 2 | 1s fixed |
| PostgreSQL write error | No | — | Transaction rollback |

---

## Security Design

### Authentication
- Phase 1: API key authentication via `Authorization: Bearer <api_key>` header.
- API keys are stored hashed (SHA-256) in a `api_keys` table with `organization_id` binding.
- Phase 2: JWT-based authentication for the web review UI.

### Authorization
- Role-based access control (RBAC): `admin`, `reviewer`, `viewer`.
- `admin`: Full CRUD on all resources.
- `reviewer`: Can view, correct, and approve flagged invoices.
- `viewer`: Read-only on validated invoices and reports.

### Encryption
- **In transit**: All endpoints served over HTTPS (TLS 1.3). Internal service communication uses TLS on private subnets.
- **At rest**: Object Storage buckets use server-side AES-256 encryption. PostgreSQL instance uses volume-level encryption.

### Secrets Management
- No secrets in source code or environment files committed to the repository.
- `OPENAI_API_KEY`, `DATABASE_URL`, `STORAGE_SECRET_KEY` are sourced from a secrets manager (AWS Secrets Manager, HashiCorp Vault, or Supabase secrets).

### Audit Logging
- Every document state transition writes an immutable row to `audit_logs`.
- Audit logs are append-only. Application users cannot update or delete them.
- Enforce at the PostgreSQL level with a trigger that blocks `UPDATE` and `DELETE` on `audit_logs`.

### Multi-Tenant Considerations
- Every database query must include a `WHERE organization_id = :org_id` filter.
- PostgreSQL Row-Level Security (RLS) enforces tenant scoping as a database-level safeguard.
- A dedicated RLS policy is set per table: `USING (organization_id = current_setting('app.current_org_id')::uuid)`.
- The application layer sets `SET LOCAL app.current_org_id = :org_id` on every connection before any query.

---

## Scalability Design

### Phase 1: Synchronous Processing
- In Phase 1, `POST /process` runs the full pipeline synchronously and returns the result.
- Acceptable for low-volume usage (< 100 invoices/day).

### Phase 2: Queue-Based Asynchronous Processing
- `POST /upload` writes to S3 and creates the database record, then publishes a `document.uploaded` event to a message queue (Redis Streams or RabbitMQ).
- A dedicated **Worker Service** consumes the queue and executes the OCR, AI Structuring, and Validation pipeline.
- Workers are horizontally scalable. Concurrency is limited by OpenAI rate limits (managed via a semaphore).

```
[API Gateway]  → POST /upload → publish event → [Queue]
                                                    ↓
                                              [Worker Pool]
                                                    ↓
                                         (OCR → AI → Validate → DB)
```

### Horizontal Scaling
- The API service is stateless and can be scaled horizontally behind a load balancer.
- Workers scale independently based on queue depth metrics.
- PostgreSQL scales with read replicas for reporting queries; write operations use the primary.

### Future SaaS Readiness
- Tenant isolation is implemented from day one via RLS and `organization_id` columns.
- When moving to SaaS, add billing metering by reading `llm_usage_log.total_tokens` and `invoices` counts per tenant.

---

## Monitoring & Observability

### Logs
- **Format**: Structured JSON logs (using `structlog` or `python-json-logger`).
- **Fields**: `timestamp`, `level`, `service`, `document_uuid`, `organization_id`, `event`, `duration_ms`, `error`.
- **Level guidelines**:
  - `INFO`: Document state transitions, successful completions.
  - `WARNING`: Confidence scores below 0.85, retried API calls.
  - `ERROR`: Failed pipeline stages, storage failures.

### Metrics
Emit metrics to Prometheus/Datadog with these key gauges and counters:
- `invoices_ingested_total` (counter, labels: `org_id`, `source_type`)
- `invoices_validated_total` (counter, labels: `org_id`, `status`)
- `pipeline_duration_seconds` (histogram, labels: `stage`)
- `llm_tokens_used_total` (counter, labels: `model`, `org_id`)
- `ocr_confidence_score` (histogram)

### Distributed Tracing
- Use OpenTelemetry (OTEL) SDK with a trace ID propagated from the `POST /upload` request through every downstream pipeline stage.
- Trace spans per stage: `upload`, `detect_type`, `extract_text`, `ai_structure`, `validate`, `store`.

### Alerts
| Alert | Trigger | Severity |
|---|---|---|
| High manual review rate | `review_rate > 30%` over 1 hour | Warning |
| AI structuring failure spike | `>5%` of documents fail AI stage | Critical |
| Token budget exceeded | Single doc > 8,000 tokens | Warning |
| Storage write failures | Any storage write error | Critical |
| Database latency spike | P99 query latency > 1s | Warning |

### Performance Monitoring
- Track `pipeline_duration_seconds` per stage. Alert if the `ai_structure` stage exceeds 20s on average.
- Track `p50`, `p95`, `p99` latency per endpoint via the API gateway.

### Cost Monitoring
- Compute daily cost per `organization_id` from `llm_usage_log`.
- Formula: `total_tokens * model_price_per_1k_tokens / 1000`.
- Expose `/admin/cost-report` endpoint for organization-level spend summaries.

---

## Future Design Extensions

### ERP Connectors (Phase 2)
- Implemented as pluggable `ERPAdapter` classes behind a factory.
- Sync is triggered after a document moves to `VALIDATED` status.
- Sync results and ERP-side IDs are stored in an `erp_sync_logs` table.
- Failed syncs are retried on a cron schedule, not inline.

### Vendor Intelligence (Phase 3)
- A `vendor_prompt_cache` table stores per-vendor extraction strategies: `(vendor_id, prompt_template, schema_hints_json, last_used_at)`.
- The AI Structuring Service checks this cache before building a generic prompt.
- Cache hits skip generic context building, reducing prompt token sizes.
- Cache entries are updated when a reviewer manually corrects extracted fields, improving the strategy automatically.

### Product Memory (Phase 3)
- After extraction and validation, a background enrichment job runs for each `invoice_item`.
- Each `item.description` is embedded using OpenAI `text-embedding-3-small`.
- The embedding is compared via `pgvector` cosine similarity against the `products` catalog.
- If a match above a configurable threshold (e.g., 0.92) is found, `invoice_items.product_sku` is populated.
- Unmatched items are surfaced in the review UI for manual catalog assignment.

### Analytics Engine (Phase 4)
- SQL materialized views are created for common aggregations (monthly spend per vendor, category breakdowns).
- Views are refreshed on a scheduled basis (e.g., nightly) using a background cron.
- An analytics API layer exposes pre-aggregated report endpoints for dashboard consumption.

### Natural Language Query Engine (Phase 4)
- Only activated once the database schema and data quality are stable.
- An NLQ service accepts a free-text query, sends it with the database schema context to an LLM to generate a SQL statement, executes it against a **read-only replica** in a sandboxed session, and returns the result formatted as a natural language answer.
- SQL injection is mitigated by: (a) only ever connecting to a read-only replica; (b) validating the generated SQL against an allowlist of permitted table names; (c) enforcing query timeouts of 5 seconds.
