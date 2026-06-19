# Software Requirements Specification (SRS)

## Project: Invoice Intelligence Platform

---

## Section 1: Introduction

### 1.1 Purpose
This document specifies the software requirements for the **Invoice Intelligence Platform**. It provides a comprehensive outline of the system's functional and non-functional requirements, data constraints, integration points, and evolutionary roadmap. This document serves as the single source of truth for engineering teams, product managers, quality assurance testers, and future contributors.

### 1.2 Scope
The platform covers the automated ingestion, text extraction, semantic structuring, rules-based validation, auditing, database storage, and downstream integration of invoice data. The scope spans a four-phase implementation plan, starting with a lightweight, high-performance Phase 1 Core Extraction MVP and expanding to a full Business Intelligence and Natural Language Query system.

### 1.3 Intended Audience
* **Engineering Teams**: To guide database schema design, extraction driver coding, AI schema integrations, and validation checks.
* **Product Managers**: To verify requirements alignment against client expectations and release milestones.
* **Founders and Stakeholders**: To audit project progress against business goals.
* **Future Platform Contributors**: To understand architectural guardrails and engineering decisions.

### 1.4 Project Overview
Transitioning from a fragile coordinate-based OCR prototype, the Invoice Intelligence Platform uses a layout-agnostic, LLM-enabled architecture. By decoupling text extraction from semantic mapping, the platform parses highly variable invoice layouts without requiring custom regex scripts or hardcoded coordinate maps. It introduces programmatic text extraction to bypass OCR for digital-native PDFs, minimizing token costs and latency.

---

## Section 2: Business Objectives

1. **Reduce Manual Invoice Processing Costs**: Reduce the manual operational overhead of Accounts Payable (AP) departments by automating document reading, matching, and data entry.
2. **Improve Financial Data Accuracy**: Eliminate typographical errors, rounding discrepancies, and calculations oversight. Enforce algebraic check logic before records write to target database tables.
3. **Deliver ERP-Ready Data Feeds**: Structure raw documents into unified schemas conforming to relational database definitions, preparing the organization for automated synchronization with corporate ERP ledgers.
4. **Enable Enterprise Business Intelligence**: Establish a data foundation of normalized supplier bills, enabling price anomaly alerts, SKU mapping catalogs, price trend queries, and natural language analytics.

---

## Section 3: Project Scope

### 3.1 In Scope (MVP - Phase 1)
* **Document Ingestion**: Multi-page upload capabilities for PDF, PNG, and JPEG documents.
* **Dual Ingestion Paths**: Programmatic character extraction for digital PDFs; local OCR (PaddleOCR, EasyOCR) for scans and images.
* **AI Structuring Layer**: Schema-enforced semantic extraction using text-only LLMs and Python Pydantic models.
* **Validation Layer**: Ingestion checks for line-item math, subtotals, tax rates, and chronological date logic with a designated rounding tolerance.
* **Database Persistence**: PostgreSQL tables storing normalized invoice fields, vendors, line items, and audit trails.
* **Object Storage**: Storing raw physical files in object buckets (S3, Supabase Storage) and logging secure URI pointers in PostgreSQL.
* **Deduplication Checks**: Hash checks of raw files and database unique constraints on vendor transaction headers.
* **Local State Tracking**: Flagging failed validation runs as `Flagged for Review` to block automated ledger routing.
* **Exporting**: A bulk export action compiling validated tables into CSV files.

### 3.2 Out of Scope (Phase 1 MVP - Deferred)
* **Human-in-the-Loop (HITL) Web Interface**: Rejection queue dashboard interfaces (Phase 2).
* **Asynchronous Queue Management**: Worker nodes, rate-limit controllers, and queue systems (Phase 2).
* **Direct ERP Sync Adapters**: Direct API push integrations to SAP, Odoo, QuickBooks, and NetSuite (Phase 2).
* **Vector Database Embeddings**: Product catalog matching and SKU vector search indexes (Phase 3).
* **Vendor Template Optimizations**: Domain prompt caching and specialized validation caches (Phase 3).
* **Natural Language Queries**: Chat interfaces utilizing Text-to-SQL or Retrieval-Augmented Generation (Phase 4).
* **Purchase Order Matching**: Automating 3-way matching between invoice, PO, and receipt documents (Future Scope).

---

## Section 4: Stakeholders

* **Business Owners**: Focus on cost per page, labor savings, processing speeds, and investment ROI.
* **ERP Teams**: Require clean, validated database inputs matching target Ledger configurations.
* **Operations & AP Teams**: Manage exceptions, perform manual audits, and edit invoices flagged as low-confidence.
* **Store & Purchasing Managers**: Upload raw bills and receipts; receive price trend and pricing deviation alerts.
* **System Administrators**: Configure API keys, manage database privileges, monitor rate limits, and set confidence thresholds.
* **Future Analytics Users**: Query database aggregates to analyze category spend, pricing anomalies, and supplier performance.

---

## Section 5: Functional Requirements

### FR-001: Document Upload
* **Description**: Allows users to upload invoices via API endpoints or CLI tools.
* **Priority**: High (Must-Have for Phase 1)
* **Acceptance Criteria**:
  * Accepts PDF, PNG, and JPEG file types.
  * Rejects uploads exceeding 25MB.
  * Generates and returns a unique `document_uuid` for tracking.

### FR-002: Ingestion Route Classifier
* **Description**: Inspects incoming PDF uploads to classify them as digital-native or scanned.
* **Priority**: High (Must-Have for Phase 1)
* **Acceptance Criteria**:
  * Scans PDF files to check for text layers.
  * If text layers exist, routes the file to the digital text extractor.
  * If no text layers exist (image-only PDF) or the file is PNG/JPEG, routes it to the OCR layer.

### FR-003: Programmatic Digital PDF Extraction
* **Description**: Extracts raw text strings from digital PDFs bypassing image processing.
* **Priority**: High (Must-Have for Phase 1)
* **Acceptance Criteria**:
  * Extracts text characters and spatial page locations programmatically (e.g. using `pdfplumber`).
  * Finishes processing within 1500ms for a standard 3-page document.
  * Bypasses local OCR libraries entirely, resulting in 100% character spelling accuracy.

### FR-004: Scanned OCR Processing
* **Description**: Processes scanned PDFs and image files via OCR engines.
* **Priority**: High (Must-Have for Phase 1)
* **Acceptance Criteria**:
  * Applies deskewing and contrast optimization to raw images.
  * Extracts text words, page coordinates, and character confidence scores using PaddleOCR/EasyOCR.

### FR-005: AI-Based Semantic Structuring
* **Description**: Feeds extracted text blocks to text-based LLMs to map fields.
* **Priority**: High (Must-Have for Phase 1)
* **Acceptance Criteria**:
  * Uses structured schemas (Pydantic models) to parse text inputs.
  * Returns header fields (invoice number, date, tax IDs, totals) and line-item arrays (description, qty, rate, line totals) conforming strictly to target JSON structures.

### FR-006: Duplicate Ingestion Prevention
* **Description**: Blocks the processing of duplicate files and transactions.
* **Priority**: High (Must-Have for Phase 1)
* **Acceptance Criteria**:
  * Calculates a SHA-256 hash of the uploaded file and compares it to stored hashes.
  * Checks database headers for matching `(vendor_tax_id, invoice_number, invoice_date)` triplets.
  * Flags duplicate attempts instantly and blocks downstream LLM processing.

### FR-007: Validation Check Engine
* **Description**: Evaluates mathematical and logical constraints on extracted invoice structures.
* **Priority**: High (Must-Have for Phase 1)
* **Acceptance Criteria**:
  * Confirms `Quantity * Unit Price == Line Total` for all items, allowing a ±0.02 rounding tolerance.
  * Verifies `Sum(Line Totals) + Tax Amount - Discount == Grand Total`.
  * Validates date relationships (`Due Date >= Invoice Date`).

### FR-008: PostgreSQL Metadata Storage
* **Description**: Logs processed transactional fields, items, and verification audits to PostgreSQL tables.
* **Priority**: High (Must-Have for Phase 1)
* **Acceptance Criteria**:
  * Normalizes document data into tables with foreign key constraints.
  * Saves audit logs capturing validation failures, verification runs, and data edits.

### FR-009: File Object Storage Integration
* **Description**: Stores physical files in secure storage buckets instead of PostgreSQL binary blobs.
* **Priority**: High (Must-Have for Phase 1)
* **Acceptance Criteria**:
  * Uploads raw PDF/image files to S3 or Supabase storage buckets.
  * Saves secure file URL reference pointers inside the relational database record.

### FR-010: Confidence Scoring Evaluation
* **Description**: Aggregates parser metrics to compute a composite document confidence score.
* **Priority**: High (Must-Have for Phase 1)
* **Acceptance Criteria**:
  * Evaluates a composite score (0-100%) incorporating OCR read confidence, LLM logprob values, and validation rule metrics.
  * Documents scoring below 85% or failing mathematical validation rules are set to `Flagged for Review`.

### FR-011: Local CSV Export
* **Description**: Allows administrators to download extracted invoice files as a bulk CSV tables.
* **Priority**: High (Must-Have for Phase 1)
* **Acceptance Criteria**:
  * Compiles selected database columns (headers and nested line items) into a downloadable CSV file.

### FR-012: Asynchronous Ingestion Queue
* **Description**: Places ingestion tasks on a background job queue.
* **Priority**: Medium (Phase 2)
* **Acceptance Criteria**:
  * Ingests files and immediately returns status `Processing` to the client.
  * Background worker nodes pull tasks from RabbitMQ/Redis to execute OCR and LLM calls asynchronously.

### FR-013: Web-Based Manual Review UI
* **Description**: Dashboard interface for manual validation audits by accounts payable teams.
* **Priority**: Medium (Phase 2)
* **Acceptance Criteria**:
  * Displays the raw scanned document next to extracted editable fields.
  * Highlights invalid fields or failed mathematical formulas in red.
  * Allows reviewers to correct values and save them with status `Validated`.

### FR-014: ERP Synchronization Adapter
* **Description**: Synchronizes validated PostgreSQL records with ERP platforms.
* **Priority**: Medium (Phase 2)
* **Acceptance Criteria**:
  * Implements modular adapters to export data via webhook calls or REST APIs to QuickBooks, Odoo, NetSuite, or SAP.

### FR-015: Learned Vendor Prompt Strategies
* **Description**: Optimizes structuring prompts dynamically per vendor.
* **Priority**: Low (Phase 3)
* **Acceptance Criteria**:
  * Identifies the vendor domain and retrieves custom extraction schema prompts.
  * Optimizes token payloads by stripping layout noise based on cached vendor prompt histories.

### FR-016: Semantic Product Memory SKUs
* **Description**: Maps vendor line descriptions to internal product catalogs.
* **Priority**: Low (Phase 3)
* **Acceptance Criteria**:
  * Embeds item strings and performs vector similarity search to pair descriptions with internal SKUs.

### FR-017: Natural Language Query Chatbot
* **Description**: Natural language search interface for corporate invoice aggregates.
* **Priority**: Low (Phase 4)
* **Acceptance Criteria**:
  * Exposes a chatbot interface executing Text-to-SQL queries over consolidated database schemas.
  * Restricts SQL execution to read-only database connections inside isolated security sandboxes.

---

## Section 6: Non-Functional Requirements

### 6.1 Performance
* **Processing Latency**: programmatically extracted digital PDFs must parse within 3 seconds. Scanned files routed to local OCR must parse within 10 seconds.
* **Query Latency**: Standard analytical queries over PostgreSQL reporting tables must return results within 500ms.

### 6.2 Scalability
* **Asynchronous Ingestion**: In Phase 2, the ingestion service must decouple via task queues, permitting scaling to 10,000 document processing runs daily without API gateway timeout issues.

### 6.3 Availability
* **Uptime target**: API ingestion endpoints must maintain a 99.9% monthly uptime target (excluding scheduled maintenance).

### 6.4 Security
* **Data Encryption**: All raw documents in Object Storage and tables in PostgreSQL must be encrypted at rest (AES-256) and in transit (TLS 1.3).
* **Row-Level Security**: Relational databases must enable Postgres Row-Level Security (RLS) to prevent cross-contamination of tenant datasets.

### 6.5 Maintainability
* **Modular Pipeline Design**: Decouple OCR drivers, parser engines, business rules, database actions, and ERP sync adapters.

### 6.6 Cost Efficiency
* **Token Budget Guardrails**: Limit visual LLM page calls. Digital PDF bypass pathways must be used on all digital-native documents. Prompts must trim OCR spacing and empty lines to optimize input costs.

### 6.7 Accuracy
* **Target metrics**: Target extraction accuracy must exceed 98% for clear digital PDF documents. Core validation math consistency must reach 100% before database writes.

### 6.8 Auditability
* **Immutable Logs**: Database tables must log all changes, capturing user identifiers, timestamps, database state snapshots, and extraction versions.

### 6.9 Regulatory Compliance
* **Personal Data Protection**: Adhere to GDPR/CCPA regulations, sanitizing sensitive personal contact fields or emails from the storage database where appropriate.

### 6.10 Data Retention
* **Duration**: Keep relational financial tables and associated raw PDF files in storage for 7 years to comply with statutory accounting audits.

### 6.11 Multi-Tenancy
* **Isolation**: Every transaction, vendor, catalog product, and dashboard log must include a tenant identifier (`organization_id` / `tenant_id`) enforced by API and database route scopes.

---

## Section 7: Data Requirements

```
  +-------------------------------------------------------------+
  |                        organization                         |
  |  - id (PK)                                                  |
  |  - tenant_name                                              |
  +-------------------------------------------------------------+
         |
         | (1 to Many)
         v
  +-------------------------+                 +-------------------------+
  |         vendor          |                 |          user           |
  |  - id (PK)              |                 |  - id (PK)              |
  |  - organization_id (FK) |                 |  - organization_id (FK) |
  |  - tax_id (VAT/GST)     |                 |  - email                |
  |  - name, address        |                 |  - role                 |
  +-------------------------+                 +-------------------------+
         |                                                 |
         | (1 to Many)                                     | (1 to Many edits)
         v                                                 v
  +-------------------------+                 +-------------------------+
  |         invoice         | <-------------- |        audit_log        |
  |  - id (PK)              | (1 to Many logs)|  - id (PK)              |
  |  - organization_id (FK) |                 |  - invoice_id (FK)      |
  |  - vendor_id (FK)       |                 |  - user_id (FK)         |
  |  - invoice_number, date |                 |  - action, payload      |
  |  - grand_total, status  |                 +-------------------------+
  |  - raw_file_url, hash   |
  +-------------------------+
         |
         | (1 to Many)
         v
  +-------------------------+                 +-------------------------+
  |      invoice_item       |                 |     product (Phase 3)   |
  |  - id (PK)              |                 |  - id (PK)              |
  |  - invoice_id (FK)      |                 |  - organization_id (FK) |
  |  - description, qty     |                 |  - sku                  |
  |  - unit_price, total    |                 |  - embedding_vector     |
  +-------------------------+                 +-------------------------+
```

### 7.1 Entity Specifications

1. **Invoices**: Tracks transaction metadata. Includes `id`, `uuid`, `organization_id`, `vendor_id`, `invoice_number`, `invoice_date`, `due_date`, `subtotal`, `tax_amount`, `discount_amount`, `grand_total`, `currency`, `composite_confidence`, `status` (Draft, Ingestion, Validated, Review, Synced), `raw_file_url`, `file_hash`, and timestamps.
2. **Invoice Items**: Detail lines of the document. Includes `id`, `invoice_id`, `description`, `quantity`, `unit_price`, `line_total`, `tax_rate`, and `mapped_product_sku`.
3. **Vendors**: Profiles of suppliers. Includes `id`, `organization_id`, `name`, `tax_id` (VAT/GST Registration), `address`, `phone`, `email`, and timestamps.
4. **Products**: Enterprise master catalog items (Phase 3). Includes `id`, `organization_id`, `sku`, `name`, `description`, `category`, and `embedding_vector` (vector index for SKU matching).
5. **Users**: User profiles and permissions. Includes `id`, `organization_id`, `email`, `name`, `role` (Admin, Reviewer, Buyer), and timestamps.
6. **Reports**: Outputs of compiled audits. Includes `id`, `organization_id`, `title`, `type`, `parameters_json`, `output_url`, `created_by`, and timestamps.
7. **Audit Logs**: Trace of document transitions. Includes `id`, `invoice_id`, `user_id` (system or user code), `action` (Ingested, Math Checked, Corrected, Synced), `before_state_json`, `after_state_json`, and `timestamp`.

---

## Section 8: Validation Requirements

### 8.1 Mathematical Validation
* **Equation checks**: Every extracted item row is verified: `Quantity * Unit Price == Line Total`.
* **Subtotal balance**: System verifies that the sum of line totals matches the document's extracted subtotal: `Sum(line_totals) == Subtotal`.
* **Tax and Total balance**: Relates totals to headers: `Subtotal + Tax Amount - Discount == Grand Total`.
* **Rounding Tolerance**: Implements a maximum floating-point mismatch buffer of ±0.02. Mismatches within this limit are resolved; errors beyond it trigger validation failures.

### 8.2 Ingestion Duplication Checks
* **File check**: Compares incoming file SHA-256 hashes against database indexes.
* **Logical check**: Blocks records matching identical `(vendor_tax_id, invoice_number, invoice_date)` combinations.

### 8.3 Confidence Scoring Aggregation
* **Scoring formula**:
  $$\text{Composite Score} = 0.3 \times \text{Mean OCR Confidence} + 0.5 \times \text{LLM Logprob Mean} + 0.2 \times \text{Validation Check Pass Flag (0 or 1)}$$
  * Fails in algebraic checks drop the Validation component to 0, dragging the composite score below 85% to trigger review routing.

---

## Section 9: Integration Requirements

### 9.1 OpenAI API
* **Integration details**: Connection with OpenAI models (`gpt-4o`, `gpt-4o-mini`) via Instructor. Implements a 15-second timeout and exponential backoff retry patterns to handle rate-limit warnings.

### 9.2 Local / Cloud OCR
* **Integration details**: Modular drivers matching standard interfaces:
  ```python
  class OCRDriver(ABC):
      @abstractmethod
      def extract_tokens(self, image_bytes: bytes) -> List[OCRWordToken]:
          pass
  ```
  Implements local PaddleOCR/EasyOCR fallback code alongside cloud-based vision API overrides.

### 9.3 PostgreSQL Relational Engine
* **Integration details**: Standard connection pools (e.g. SQLAlchemy, `pg_pool`). Configured with strict constraint keys, unique compound indexes, and session tenant variables context matching RLS.

### 9.4 CSV Exporting
* **Integration details**: Tabular compilation matching header profiles with list items for local downloads.

### 9.5 ERP Synchronization Adapters
* **Integration details**: Pluggable adapter interfaces sending JSON files to Odoo, NetSuite, QuickBooks, or SAP APIs.

---

## Section 10: MVP Requirements (Phase 1)

### 10.1 Phase 1 Deliverables
* Standard ingestion endpoints parsing digital PDF texts and scanned image documents.
* Local OCR execution engine (PaddleOCR/EasyOCR) as a processing fallback.
* AI parser utilizing OpenAI schemas to structure extracted text.
* Financial validation engine executing math verification checks with rounding tolerance.
* Normalized PostgreSQL database structure mapping invoices, vendors, and item records.
* Object storage file sync tracking URI pointer references.
* Immediate SHA-256 document hashing and duplicate prevention checks.
* Status logger flagging low-confidence documents.
* Local CSV bulk data export utilities.

### 10.2 Must-Have Features
* Programmatic digital PDF extraction bypassing OCR.
* LLM parser schema validation using Pydantic/Instructor models.
* Relational PostgreSQL storage of validated values.
* Composite confidence calculations.
* Basic duplicate detection (unique indexes).

### 10.3 Nice-to-Have Features
* Automatic categorizations of purchase departments.
* Simple CLI logs showing processed page metrics.

---

## Section 11: Assumptions

1. **Document Formats**: Native digital PDFs represent the majority (>70%) of electronic invoices, allowing the system to run the high-performance digital PDF path in most cases.
2. **Connectivity**: Stable API connectivity remains active for LLM endpoints.
3. **Scan Quality**: Scans will have enough contrast to prevent complete OCR word drops; corrupt files will trigger standard ingestion exceptions.
4. **Environment**: Databases and object storage are hosted inside secure VPC networks behind standard IAM credentials.

---

## Section 12: Constraints

1. **API Costs**: Frequent visual LLM processing of multi-page documents is expensive. Prompt engineering must trim OCR space and bypass visual models when digital text exists.
2. **OCR Limitations**: Local engines (EasyOCR, PaddleOCR) have difficulty reading handwritten notes or nested complex table borders.
3. **Vendor Variability**: Layout configurations are infinite. The system cannot rely on coordinate-based layout engines, requiring semantic LLM extraction.
4. **Image Quality**: Skewing, blurs, and low resolutions degrade OCR accuracy.
5. **LLM Limits**: Strict rate limits require queuing architectures for asynchronous processing in Phase 2.

---

## Section 13: Success Metrics

1. **Extraction Accuracy**: Target $>95\%$ correct field mapping for clear digital or high-contrast scanned pages.
2. **Validation Integrity**: $100\%$ math accuracy on database-committed invoice logs. Zero mathematical failures synced to databases.
3. **Processing Latency**: $<3$ seconds for digital PDFs; $<10$ seconds for scanned OCR images.
4. **Cost Efficiency**: Average processing costs under $\$0.03$ per document page.
5. **Manual Review Rate**: Under $20\%$ of clear digital documents flagged for manual review checks.

---

## Section 14: Future Scope (Roadmap)

### 14.1 Phase 2: Workflow & ERP Integration
* Human-in-the-Loop web dashboard allowing AP editors to review, edit, and approve flagged invoices.
* Pluggable sync adapters integrating NetSuite, SAP, Odoo, and QuickBooks.
* Asynchronous task queuing using Redis/Celery to handle background worker processing and manage LLM rate limits.

### 14.2 Phase 3: Vendor Intelligence & Product Memory
* Cache prompt strategies and schema patterns in the Vendor Template Engine to optimize token costs.
* Build out pgvector/Qdrant Vector Database indexes to host product SKU embeddings.
* Launch the Product Memory System, mapping vendor description texts to internal database catalog items.
* Implement price tracking and anomalous expenditure alert systems.

### 14.3 Phase 4: Business Intelligence & Analytics
* Setup the Natural Language Query (NLQ) interface supporting Text-to-SQL conversions.
* Expose interactive analytical dashboards tracking vendor processing efficiency and tax logs.
* Automate scheduled email summaries of monthly accounts payable expenditures.
