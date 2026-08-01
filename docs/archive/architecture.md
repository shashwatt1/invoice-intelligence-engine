# Architecture

## Introduction

The **Invoice Intelligence Platform** is designed to transform unstructured financial documents—such as invoices, receipts, purchase orders, and billing statements—into structured, validated, and actionable business intelligence. Moving beyond simple Optical Character Recognition (OCR) tools, this system establishes an intelligent, end-to-end data processing pipeline. It integrates deep layout understanding, large language models (LLMs) for semantic processing, multi-level validation checks, database storage, enterprise resource planning (ERP) sync, and a natural language query interface for business analytics.

By decoupling ingestion, text extraction, semantic processing, and downstream reporting, the platform ensures robustness, high scalability, and vendor-agnostic adaptability.

---

## Business Problem

Manual invoice processing is a notorious bottleneck for organizations of all sizes. The challenges include:

1. **High Operational Costs**: Accounts Payable (AP) teams spend significant manual hours keying invoice data into databases and ERP systems.
2. **Typographical & Calculation Errors**: Manual entry leads to costly data discrepancies, leading to payment errors, auditing overheads, and strained supplier relationships.
3. **Scale & Layout Volatility**: Invoices have no single standard. Every vendor designs their invoices differently, arranging columns, headers, tax summaries, and line items in unique patterns.
4. **Table & Line-Item Complexity**: Extracting multi-line, tabular item descriptions, quantities, unit prices, and discounts across multiple pages is highly prone to misalignment when using coordinate-based approaches.
5. **Rule-Based Fragility**: Traditional automated solutions rely on rigid templates or custom regular expressions (regex). These systems break whenever a vendor tweaks a font size, adds a new column, or changes the layout, incurring massive maintenance overhead.

---

## Project Evolution

### Initial OCR-Based Architecture

The project began as an OCR-centric pipeline tailored for basic extraction from structured invoice images. The prototype executed a linear, synchronous workflow:

```
+---------------+      +-------------+      +---------------+
| Invoice Image | ---> | OCR Engine  | ---> | Row Detection |
+---------------+      +-------------+      +---------------+
                                                   |
                                                   v
+---------------+      +-------------+      +---------------+
|  Structured   | <--- |    Regex    | <--- |    Column     |
|     JSON      |      |   Parsing   |      |    Mapping    |
+---------------+      +-------------+      +---------------+
```

1. **OCR Processing**: Raw images were passed to open-source OCR engines (EasyOCR, PaddleOCR) to retrieve coordinate bounding boxes and raw text chunks.
2. **Row & Column Detection**: Geometric clustering algorithms attempted to align text fragments horizontally into rows and vertically into columns.
3. **Column Mapping**: Custom heuristics mapped identified text columns to target fields (e.g., Description, Qty, Unit Price) based on spatial overlap.
4. **Regex Parsing**: Regular expressions scanned the aligned texts to isolate metadata like Invoice Dates, Tax Identifiers, Subtotals, and Invoice Numbers.

### Challenges Identified

During repository auditing and evaluation, several core technical bottlenecks were identified:

* **OCR is not the primary bottleneck**: Modern open-source libraries (PaddleOCR, EasyOCR) perform character extraction with high accuracy (>95% on clean documents). The breakdown occurs in processing the extracted text, not in reading the characters.
* **Layout reconstruction is unstable across vendors**: Bounding-box alignment algorithms fail on invoices without clear table borders, multi-line item descriptions, page-spanning tables, or complex layouts (e.g., nested columns or sidebar-based metadata).
* **Regex-based parsing is difficult to scale**: Writing and maintaining regex patterns to capture dates, totals, and addresses across hundreds of differing vendor layouts results in a fragile codebase with ballooning technical debt.
* **Lack of semantic context**: A rule-based parser struggles to distinguish between "Subtotal," "Taxable Amount," "Net Amount," and "Grand Total" when they are positioned adjacent to one another or styled identically.

### Key Learnings

1. **Semantic understanding is the primary challenge**: The core issue is translating a set of raw text coordinates into structured entities (semantic mapping).
2. **Layout-aware AI is necessary**: Multimodal approaches (combining visual layout coordinates and text tokens) are drastically superior to flat-text regex filters.
3. **Separation of Extraction and Logic**: Text extraction (OCR) must be decoupled from structuring (AI Parser) and business rule checks (Validation). This allows the system to upgrade its language models and validation logic independently of the underlying OCR engines.

---

## MVP Scope (Phase 1)

To establish a rapid, high-confidence feedback loop, the development of the Invoice Intelligence Platform is divided into focused stages. The MVP (Phase 1) concentrates entirely on achieving robust extraction and validation on core document formats while deferring downstream integrations and complex memory layers.

### Included in Phase 1:
* **Invoice Upload**: Simple API endpoint and CLI interface to submit invoice files.
* **PDF Processing**: Direct text extraction from digital-native PDFs to bypass OCR.
* **Image Processing**: Basic rotation, deskewing, and contrast adjustments for raw scans.
* **OCR Extraction**: Local open-source OCR engines (PaddleOCR, EasyOCR) to process images and scanned documents.
* **OpenAI Structuring Layer**: Extraction of header fields and tabular line items using OpenAI text-based models paired with strict Pydantic/Instructor schema validation.
* **Validation Layer**: Basic algebraic checks (math totals, item subtotals) and logprob-based parsing confidence thresholds.
* **PostgreSQL Storage**: Standard relational database to persist structured invoices, vendors, and line items.
* **CSV Export**: Ability to download extracted invoice data in standard table format.
* **Manual Review Workflow**: Local review status tracking where documents falling below confidence limits are flagged for human review.

### Excluded from Phase 1:
* **Vector Database**: PostgreSQL handles all relational search; vector similarity search is deferred to Phase 3.
* **Product Memory System**: SKU mapping is a post-extraction enrichment task and is deferred to Phase 3.
* **Natural Language Query Engine**: Conversational reporting is deferred to Phase 4 until data assets are clean and aggregated.
* **Advanced Analytics**: Multi-store reporting and dashboards are deferred to Phase 4.
* **Automatic Anomaly Detection**: Price deviations and historical comparison alerts are deferred to Phase 3.
* **ERP Direct Connectors**: Direct SAP, NetSuite, and Odoo integrations are deferred to Phase 2.
* **Predictive Intelligence**: Purchase order matching (3-way match) and cash flow forecasting are deferred to Future Scope.

*Rationale for Deferral*: Attempting to build semantic mapping, ERP connectors, and NLP query systems concurrently with the extraction core introduces high failure risks. Isolating the core extraction and validation pipeline first ensures we have clean, verified data before feeding downstream systems.

---

## Current Prototype Architecture

The current prototype operates as a synchronous CLI utility. The diagram below illustrates the exact data flow:

```
[Invoice File]
      |
      v
+------------------------------------------+
|          OCR / Text Extraction           |
|  - paddleocr / easyocr local processing  |
|  - Raw text & bounding box coordinates    |
+------------------------------------------+
      |
      v
+------------------------------------------+
|          Layout Reconstruction           |
|  - Spatial clustering of coordinates     |
|  - Heuristic-based row grouping          |
+------------------------------------------+
      |
      v
+------------------------------------------+
|          Regex Parsing Engine            |
|  - Pre-defined text search rules         |
|  - Manual column mapping checks          |
+------------------------------------------+
      |
      v
[Output: structured_invoice.json]
```

*Limitations of this model*:
* No validation step (e.g., no checking if mathematical equations balance).
* Hard failure on novel templates.
* No persistence layer or historical analytics.
* Linear, synchronous processing with zero queue management.

---

## Repository Audit Summary

To transition the prototype into a production-ready AI-powered platform, the codebase will be audited and organized as follows:

### Keep
* **OCR Drivers**: Keep abstractions for PaddleOCR and EasyOCR as local backup engines when cloud services or multimodal API engines are unavailable or offline.
* **Image Preprocessing Utilities**: Keep helper functions for image rotation, binarization, deskewing, and contrast adjustment.
* **JSON Schema Specifications**: Maintain the target JSON schemas defining standard invoice fields.

### Refactor
* **Extraction Core**: Refactor the pipeline to isolate OCR execution. The OCR engine should only output unified OCR tokens (text, bounding boxes, confidence scores) instead of attempting parsing.
* **Data Models**: Migrate from ad-hoc JSON structures to robust typed data definitions (e.g., Python Pydantic models) representing invoice line-items, headers, and metadata.
* **Modular Pipeline**: Transition the script-based structure to a class-based workflow configuration (e.g., `Ingestor -> OCRManager -> AIParser -> Validator -> StorageAdapter`).

### Deprecate
* **Vendor-Specific Regular Expressions**: Remove scripts that maintain hardcoded regex files for specific suppliers.
* **Coordinate-Based Row Merger**: Deprecate spatial heuristics that assume columns are always vertically aligned, as these fail on complex responsive document layouts.
* **Direct File System Outputs**: Remove tight coupling where intermediate parsers write temporary JSON files directly to local directories.

---

## Proposed Future Architecture

The proposed system adopts a highly modular architecture designed to scale. It leverages LLMs for semantic extraction, implements robust rules-based checking, logs data to relational and object stores, and supports downstream enrichment (vector-based SKU lookup in Phase 3) and analytical queries (NLQ in Phase 4).

Instead of forcing all invoices through OCR, the system establishes a dual ingestion path:

```
                                 +-----------------------------------+
                                 |         Invoice Ingestion         |
                                 |  (API, Upload, Webhooks, Email)   |
                                 +-----------------------------------+
                                   /                               \
                     [Digital PDF]/                                 \[Scanned PDF / Image]
                                 v                                   v
                  +----------------------------+       +-----------------------------+
                  |  Direct Text Extraction    |       |     OCR & Vision Layer      |
                  | (pypdf, pdfplumber, etc.)  |       | (PaddleOCR/EasyOCR / Cloud) |
                  +----------------------------+       +-----------------------------+
                                 \                                 /
                                  \                               /
                                   v                             v
                  +--------------------------------------------------+
                  |               AI Structuring Layer               |
                  |          (LLM Parser / Schema Enforcer)          |
                  +--------------------------------------------------+
                                           |
                                           v
                  +--------------------------------------------------+
                  |                 Validation Layer                 |
                  |        (Math integrity, tax compliance)          |
                  +--------------------------------------------------+
                                          / \
                                         /   \
                                [Pass]  /     \ [Fail]
                                       /       \
                                      v         v
       +---------------------------------+   +---------------------------------+
       |         Database Layer          |   |      Human-in-the-Loop          |
       | - PostgreSQL (Financials)       |   |      (Rejection Queue)          |
       | - Vector DB (Phase 3 Embedding)*|   +---------------------------------+
       +---------------------------------+                    |
              |                   |                           | (Corrected)
              |                   +---------------+-----------+
              v                                   v
       +---------------------------------+   +---------------------------------+
       |      ERP Integration Layer      |   |   Analytics & Reporting Layer   |
       | (Sync to SAP, NetSuite, Odoo)   |   |   (Aggregations, NLQ Engine)    |
       +---------------------------------+   +---------------------------------+
```
\*Note: Relational PostgreSQL is used in Phase 1. Vector Database is introduced in Phase 3.

**Dual Ingestion Processing Paths**:
To optimize extraction efficiency, the system divides incoming files into two processing channels:
* **Digital PDF Path (Direct Extraction)**: Documents containing native text layers bypass the resource-heavy image preprocessing and OCR models. Text extraction libraries (e.g., `pdfplumber`, `pypdf`) pull raw character lists directly, conserving computational resources.
* **Scanned PDF / Image Path (OCR)**: Scanned documents, photos, and faxes are routed through visual filters (deskewing, binarization) and processed via local or cloud OCR engines to generate spatial word tokens.

**Benefits of Dual Ingestion Paths**:
* **Lower API & GPU Costs**: Bypassing OCR engines for digital-native PDFs eliminates unnecessary neural network computation.
* **Higher Accuracy**: Direct programmatic text extraction retrieves text characters with 100% accuracy, eliminating OCR errors (e.g., misreading 'B' as '8').
* **Faster Processing**: Programmatic text extraction runs in milliseconds, compared to several seconds for OCR models.
* **Reduced OCR Dependency**: Prevents bottlenecking of heavy visual extraction nodes under peak volumes.

---

## Core System Components

### OCR / Vision Layer
* **Responsibility**: Converts incoming files into standardized digitized text based on document type.
* **Details**: 
  * For digital-native PDFs, it programmatically extracts text characters directly, bypassing visual processing.
  * For scanned PDFs and images, it applies visual preprocessing (rotation, deskewing) and runs them through unified local (PaddleOCR/EasyOCR) or cloud OCR interfaces. Outputs a list of text tokens paired with page locations and confidence scores.

### AI Structuring Layer
* **Responsibility**: Transforms the raw text tokens or document image into a structured document representation.
* **Details**: In Phase 1, it utilizes text-based OpenAI models combined with schema enforcement tools (such as Pydantic and Instructor) to extract structured fields. It outputs parsed headers (vendor tax ID, invoice number, due date) and detailed item lists (description, quantity, rate, tax, line totals) conforming strictly to target JSON structures.

### Validation Layer
* **Responsibility**: Executes programmatic business rules and calculates composite confidence metrics.
* **Details**:
  * **Confidence Scoring Framework**: Calculates a composite confidence score for every document. It aggregates OCR character confidence, LLM token logprobs (generation confidence), and mathematical consistency flags. Any document scoring below the threshold (e.g., 85% confidence) is flagged and routed directly to the Human Review Queue.
  * **Financial Validation Rules**:
    * **Line Check**: Ensures `Quantity * Unit Price == Item Line Total`.
    * **Subtotal Check**: Verifies `Sum(Line Items) == Invoice Subtotal`.
    * **Tax & Total Check**: Verifies `Subtotal + Tax Amount - Discount == Grand Total`.
    * **Rounding Tolerance**: Allows an arithmetic rounding tolerance of up to ±0.02 units (e.g., cents) to account for float calculation variances and vendor rounding discrepancies.
    * **Data Integrity**: Assures dates are chronologically valid (`Due Date >= Invoice Date`) and tax numbers match target patterns.

### Database Layer
* **Responsibility**: Persists system records, files, and relationships.
* **Details**:
  * **Relational Database (PostgreSQL)**: Serves as the primary system of record for Phase 1. It stores normalized invoices, vendor records, line items, audit logs, and extraction statuses.
  * **Object Storage (e.g., S3, Supabase Storage)**: Holds raw PDF files, preprocessed images, and JSON payloads. PostgreSQL only stores secure URI links to these objects rather than storing files directly as binary blobs (`bytea`).
  * **Vector Database (Phase 3)**: Introduced in Phase 3 (using `pgvector` or Qdrant) to handle embeddings for vendor matching and item categorization. PostgreSQL is the sole database in Phase 1 to minimize deployment overhead.

### ERP Integration Layer
* **Responsibility**: Synchronizes verified document records with client enterprise resource planning databases.
* **Details**: Provides pluggable adapters implementing a standard interface:
  ```python
  class ERPAdapter(ABC):
      @abstractmethod
      def sync_invoice(self, invoice_data: InvoiceSchema) -> SyncResult:
          pass
  ```
  Implementations include REST API connectors, SFTP file exports, or database triggers targeting platforms like SAP, QuickBooks Online, NetSuite, or Odoo.

### Analytics & Reporting Layer
* **Responsibility**: Aggregates structured data for business operations.
* **Details**: Compiles metrics such as total monthly spend, average vendor processing cycle times, and tax liabilities. Exposes reporting endpoints that feed into web dashboards and generate scheduled PDF summaries.

---

## Future Intelligence Layer

To turn raw extraction into an optimization engine, the platform integrates three long-term intelligence modules:

```
                  +-----------------------------------+
                  |      Future Intelligence Layer    |
                  +-----------------------------------+
                   /               |                 \
                  v                v                  v
    +-------------------+  +-------------------+  +-------------------+
    |  Vendor Template  |  |  Product Memory   |  |    Historical     |
    |      Engine       |  |      System       |  |   Intelligence    |
    +-------------------+  +-------------------+  +-------------------+
```

### Vendor Template Engine (Phase 3)
* **Purpose**: Optimizes token consumption and latency of the AI Structuring Layer for recurring vendors.
* **Mechanism**: The platform avoids fragile coordinate-based templates. Instead, the Vendor Template Engine focuses on a learned prompt strategy and extraction optimization. When a vendor is identified (e.g., via Tax ID), the engine retrieves a cached prompt strategy, schema hints (such as expected line item headers), and custom validation adjustments. This guides the LLM to extract fields accurately and quickly, reducing token overhead.

### Product Memory System (Phase 3)
* **Purpose**: Maps vendor-specific descriptions to a unified internal Product Catalog / SKU master list.
* **Mechanism**: Positioned as a Phase 3 enrichment layer that operates *after* extraction quality has been verified. When a line item (e.g., `"M4 steel bolt - hex head"`) is extracted and saved to PostgreSQL, it is transformed into a vector embedding. The engine queries the Phase 3 Vector Database for matching internal SKU entries (e.g., SKU `#BLT-M4-01`) using semantic similarity, mapping discrepancies dynamically.

### Historical Intelligence Repository
* **Purpose**: Provides audit trails, anomalous billing detection, and commercial trends.
* **Mechanism**: Maintains a time-series record of unit costs, delivery timelines, and tax rates per vendor. If an invoice presents an unexpected unit price deviation (e.g., a 15% hike on a component compared to the historical average), the system alerts procurement teams prior to ERP payment approval.

---

## Natural Language Query Architecture (Phase 4)

*Note: This architecture is implemented in Phase 4. Natural language querying requires a clean, mature, and consolidated database model to ensure Text-to-SQL generation and semantic retrieval run safely and accurately.*

The platform allows non-technical business users to run complex analytical queries over invoice datasets using natural language.

```
       [User Query: "Show total spent on office supplies last month"]
                                      |
                                      v
                        +----------------------------+
                        |       Intent Parser        |
                        | (LLM Categorizer / Router) |
                        +----------------------------+
                               /              \
                   [Structured]               [Unstructured / Semantic]
                              /                \
                             v                  v
              +--------------------+      +--------------------+
              |   Text-to-SQL      |      | Vector Search /    |
              |   Generator        |      | Hybrid RAG         |
              +--------------------+      +--------------------+
                             |                      |
                             v                      v
              +--------------------+      +--------------------+
              | Relational DB      |      | Vector DB          |
              | Query Execution    |      | Retrieval          |
              +--------------------+      +--------------------+
                             \                      /
                              \                    /
                               v                  v
                        +----------------------------+
                        |    Response Synthesizer    |
                        | (LLM Context Formatter)    |
                        +----------------------------+
                                      |
                                      v
                     [Final Natural Language Answer]
```

1. **User Query**: Users query the system via chat (e.g., *"Did vendor Acme Corp charge us shipping fees in Q2?"*).
2. **Intent Parser**: An LLM determines the execution path:
   * **Structured Query**: Requires calculations or aggregations (SQL generation).
   * **Semantic Query**: Requires search within document notes, terms, line item descriptions, or attached policies.
3. **Execution Engine**:
   * **Text-to-SQL**: Translates the query into a postgres-compatible SQL command using a strict database schema catalog. The system executes the SQL inside a read-only transaction sandbox.
   * **Hybrid RAG**: Translates the query into a semantic embedding vector to search line-item content, returning relevant rows, documents, or audit logs.
4. **Response Synthesizer**: Formats the database results into a natural, easy-to-read response, complete with references back to specific invoices.

---

## Four-Phase Architecture Evolution

```
+---------------------------------------------------------------------------------+
| Phase 1: Core Extraction MVP                                                    |
| Digital PDF / Scanned OCR -> OpenAI Structuring -> Validation -> PostgreSQL -> CSV|
+---------------------------------------------------------------------------------+
                                       |
                                       v
+---------------------------------------------------------------------------------+
| Phase 2: Workflow & ERP Integration                                             |
| Human Review UI -> ERP Connectors (Odoo, QuickBooks) -> Workflow Automation     |
+---------------------------------------------------------------------------------+
                                       |
                                       v
+---------------------------------------------------------------------------------+
| Phase 3: Vendor Intelligence & Product Memory                                   |
| Vendor Prompt templates -> Product Memory (Vector DB) -> Cost Optimization      |
+---------------------------------------------------------------------------------+
                                       |
                                       v
+---------------------------------------------------------------------------------+
| Phase 4: Business Intelligence & Analytics                                      |
| Analytics Dashboards -> Natural Language Querying (NLQ) -> Reporting            |
+---------------------------------------------------------------------------------+
```

### Phase 1 – Core Extraction MVP
* **Goal**: Establish a robust, high-confidence extraction and validation baseline.
* **Milestones**:
  * Deploy Digital PDF text parsing (direct extraction) and local/fallback OCR interfaces (PaddleOCR, EasyOCR).
  * Build the AI Structuring Layer using text-based LLMs (OpenAI) with strict Pydantic schemas.
  * Establish the validation layer covering basic algebraic constraints (subtotals, math verification, rounding tolerance) and logprob confidence scoring.
  * Configure simple PostgreSQL relational tables and Object Storage references to persist document metadata.
  * Provide a standard CSV export utility for extracted invoice listings.

### Phase 2 – Workflow & ERP Integration
* **Goal**: Connect extraction with human review and core business systems.
* **Milestones**:
  * Develop the Human-in-the-Loop (HITL) manual review dashboard to audit flagged/low-confidence documents.
  * Build pluggable adapters for ERP systems (QuickBooks Online, NetSuite, Odoo).
  * Automate file-reception triggers (emails, folders) and webhook delivery endpoints.

### Phase 3 – Vendor Intelligence & Product Memory
* **Goal**: Optimize API costs, automate SKU mapping, and implement supplier memory.
* **Milestones**:
  * Deploy the Vendor Template Engine using learned prompt strategies to optimize token usage.
  * Integrate pgvector/Qdrant Vector Database to host product catalog embeddings.
  * Launch the Product Memory System to automatically map vendor line descriptions to internal product SKUs.
  * Set up price deviation tracking, anomaly alerts, and vendor comparison algorithms.

### Phase 4 – Business Intelligence & Analytics
* **Goal**: Deliver user-facing conversational search and advanced management dashboards.
* **Milestones**:
  * Implement the Natural Language Query (NLQ) interface supporting Text-to-SQL and RAG.
  * Create analytical dashboard widgets for accounts payable metrics.
  * Implement background cron tasks generating weekly/monthly spend reports automatically.

---

## Architectural Principles

* **Separation of Concerns**: Each step in the processing pipeline (ingestion, OCR, parsing, validation, database, external sync) must be implemented as an isolated service. The failure of one step (such as ERP sync) must not cause data loss in other steps.
* **Idempotency & Auditability**: Every document ingestion attempt must generate a unique tracking UUID. Every stage of validation, human edit, or external synchronization must log audit trails to verify changes.
* **Graceful Degradation**: If advanced visual LLMs are unavailable, the parser must fall back to local OCR text processing with backup rule engines.
* **Data Security & Isolation**: Financial data must be encrypted in transit (TLS 1.3) and at rest (AES-256). Document access must be controlled using Role-Based Access Control (RBAC).

---

## Duplicate Invoice Prevention

Processing duplicate invoices introduces severe operational risks, leading to double-payments and accounting errors. The platform implements a multi-tiered duplicate prevention check:
* **Document Hashing**: Calculates a unique cryptographic hash (SHA-256) of every incoming raw file. If the hash matches an already processed file, the document is flagged as a duplicate.
* **Database Unique Constraints**: Enforces a unique index constraint in PostgreSQL on the combination of `(vendor_tax_id, invoice_number, invoice_date)`.
* **Detection Workflow**: Duplicate flags immediately bypass LLM processing, returning the existing database records or routing the document to the Review Queue for validation.

---

## Cost Optimization Strategy

To maintain profitability and scale, the system implements a strict cost control plan:
* **Digital PDF Bypass**: Direct text extraction from digital PDFs avoids GPU/API costs entirely.
* **OCR Provider Tiering**: Utilizes lightweight open-source OCR (Tesseract/PaddleOCR) for clear layouts, routing only low-contrast or complex documents to expensive cloud-based Vision APIs.
* **Prompt Minimization**: Compresses OCR coordinate descriptions and eliminates whitespace before sending text payloads to the AI Structuring Layer.
* **Structured Outputs Optimization**: Utilizes JSON mode/grammar guides to avoid LLM token loops, reducing input and output token consumption.
* **Selective Processing**: Extracts page bounds before AI parsing. If item listings are completed on page 3 of a 10-page document, the system terminates downstream page calls.

---

## Multi-Tenancy Considerations

To support multi-store or SaaS operations, data isolation is built into the architecture from day one:
* **Organization Isolation**: Every database table includes an `organization_id` (or `tenant_id`) foreign key.
* **Row-Level Security (RLS)**: Employs PostgreSQL Row-Level Security to ensure tenant queries can never cross-pollinate.
* **Tenant-Aware Access**: API gateways route requests and authenticate API keys isolated by tenant namespaces.

---

## Future Scaling: Queue-Based Processing

*Note: This is deferred to Phase 2 to avoid early-stage architectural complexity.*
* **Asynchronous Ingestion**: In Phase 2, direct API sync is replaced by an asynchronous ingestion queue (e.g., Celery/Redis or RabbitMQ).
* **Worker Nodes**: Dedicated worker instances consume tasks from the queue to run OCR models and call language APIs independently of the main API gateway, shielding the interface from network timeouts.

---

## Future Scope

* **Three-Way Matching**: Automate verification matching the invoice against the Purchase Order (PO) and the Goods Receipt Note (GRN) to ensure invoice legitimacy prior to ERP entry.
* **Predictive Cash Flow Analytics**: Analyze historical invoice payment cycles and vendor payment terms to forecast future company cash outflows.
* **Multi-Currency & Cross-Border Compliance**: Track tax systems across regions (VAT, GST, Sales Tax) and automatically convert currencies using real-time API integrations.
