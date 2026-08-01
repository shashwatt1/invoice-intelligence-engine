# Implementation Plan

## Invoice Intelligence Platform

---

## Executive Summary

The Invoice Intelligence Platform is being built in four sequential phases, each one shipping independently usable value while laying the technical foundation for the next. The project begins with a **Core Extraction MVP** (Phase 1) that converts invoice PDFs and images into validated, structured database records with zero manual data entry. This output is immediately useful as a standalone product and generates real-world data that all future phases depend on.

Phase 2 adds human review workflows, workflow automation, and direct ERP synchronization, connecting the extraction engine to existing business accounting systems. Phase 3 introduces vendor intelligence and a product memory system, using historical extraction data to reduce API costs and automate SKU cataloguing. Phase 4 closes the loop with business intelligence dashboards, automated reporting, and natural language querying over the consolidated invoice dataset.

The plan is structured to minimize risk by deferring complexity. Each phase produces a complete, usable product increment rather than an incomplete platform fragment.

---

## Development Strategy

The project follows a **pipeline-first, intelligence-later** strategy:

1. **Build the data pipeline before building on top of it.** Natural language querying, vendor intelligence, and anomaly detection only have value if the underlying extraction data is clean and reliable. Phase 1 builds and verifies that reliability before any downstream layer is started.
2. **Validate with real invoices early.** Unit tests are necessary but not sufficient. Phase 1 includes a structured testing sprint using real-world invoice samples from at least three distinct vendor layouts to establish extraction accuracy baselines.
3. **Design every interface as a contract.** Service boundaries (Upload → Processing → AI Structuring → Validation → Storage) are formalized as typed Python interfaces from day one. This allows components to be replaced, upgraded, or parallelized without regressions.
4. **Defer infrastructure complexity.** No message queues, no background workers, and no vector databases in Phase 1. The synchronous pipeline must first be proven correct. Scalability infrastructure is introduced in Phase 2 once workload characteristics are understood.
5. **Instrument everything from the start.** Logs, metrics, and cost tracking are built into Phase 1. Post-hoc observability is always incomplete.

---

## Team Assumptions

This plan assumes a small, focused engineering team operating without formal project management overhead:

| Role | Assumed Count | Responsibilities |
|---|---|---|
| Backend Engineer | 1–2 | Pipeline services, database schema, API endpoints, validation logic |
| AI/ML Engineer | 1 | Prompt engineering, schema design, OCR evaluation, model selection |
| DevOps / Infrastructure | 0.5 (part-time) | Cloud storage setup, PostgreSQL provisioning, secrets management |
| QA / Testing | 0.5 (part-time) | Integration test suites, real-invoice accuracy benchmarking |

Estimates assume team members are full-time and have Python proficiency. Engineers new to `instructor`, `pdfplumber`, or `paddleocr` should budget 2–3 days of ramp-up time per library. All timeline estimates are working-day estimates and exclude onboarding time.

---

## Risks & Dependencies

### Cross-Phase Dependencies

```
Phase 1 (Core Extraction MVP)
  ↓   [Clean PostgreSQL data is the prerequisite]
Phase 2 (Workflow & ERP Integration)
  ↓   [Validated + corrected historical data enables learning]
Phase 3 (Vendor Intelligence & Product Memory)
  ↓   [Mature data model enables safe analytics and NLQ]
Phase 4 (Business Intelligence & Analytics)
```

No phase may begin development until the preceding phase has passed its success criteria. Starting Phase 3 without reliable extraction accuracy (Phase 1) or without human-corrected training data (Phase 2) would produce a vendor template engine trained on noisy inputs.

### Global Risk Register

| Risk | Impact | Probability | Mitigation |
|---|---|---|---|
| OpenAI API rate limits block processing | High | Medium | Implement exponential backoff retry; use `gpt-4o-mini` for Phase 1 to stay within higher rate-limit tiers |
| OCR accuracy too low for complex scans | High | Medium | Establish accuracy benchmarks in Week 1; integrate Google Cloud Vision as a fallback in Sprint 2 |
| LLM schema hallucinations on ambiguous invoices | High | Medium | Use `instructor` schema enforcement with 2 internal retries; route low-confidence docs to review queue |
| PostgreSQL schema changes mid-development | Medium | Low | Enforce Alembic migrations from day one; no raw SQL schema changes outside migration files |
| Object Storage cost overruns | Low | Low | Set S3 lifecycle policies on day one; alert on bucket size weekly |
| Vendor invoice layouts break extraction | High | High | Build layout-agnostic prompts; never hardcode field positions |

---

## Phase 1: Core Extraction MVP

### Objectives
- Build a functional end-to-end pipeline that accepts invoice PDFs and images and produces validated, structured database records.
- Prove that the AI-based semantic extraction approach outperforms the retired coordinate/regex approach on layout-diverse vendor invoices.
- Establish a reliable data foundation that all future phases build on.

### Deliverables
- `POST /upload` API endpoint with file validation, hashing, and object storage write.
- Document type router (digital PDF vs. scanned image detection).
- Digital PDF text extractor (`pdfplumber` + `pypdf`).
- OCR extraction driver abstraction with PaddleOCR primary and EasyOCR fallback.
- AI structuring service using OpenAI + Instructor with Pydantic `InvoiceSchema`.
- Validation engine: line item math, subtotal, grand total, date integrity, rounding tolerance.
- Composite confidence scoring system (OCR confidence + LLM logprobs + validation pass ratio).
- PostgreSQL schema: `organizations`, `users`, `vendors`, `invoices`, `invoice_items`, `audit_logs`, `llm_usage_log`.
- Object storage integration (S3 or Supabase Storage) with URI reference pattern.
- SHA-256 file hash deduplication and database unique constraint guard.
- Status lifecycle: `INGESTED → VALIDATED / REVIEW / FAILED_*`.
- Structured JSON logging and LLM token usage tracking.
- `GET /invoices`, `GET /invoice/{id}`, and `GET /export` (CSV) endpoints.
- `GET /health` endpoint for infrastructure monitoring.
- Integration test suite with at least 15 real-world invoice samples.

### Tasks

#### Sprint 1: Foundation & Infrastructure (Days 1–7)
- [ ] Initialize Python project with `pyproject.toml` (or `setup.cfg`), `requirements.txt`, and folder structure (`services/`, `models/`, `api/`, `db/`, `tests/`).
- [ ] Configure PostgreSQL locally and in staging environment.
- [ ] Write and run Alembic initial migration for all Phase 1 tables.
- [ ] Set up object storage bucket with environment-based configuration (`STORAGE_BACKEND`, `STORAGE_BUCKET`).
- [ ] Implement secrets management pattern — no secrets in codebase.
- [ ] Write `GET /health` endpoint verifying database + storage connectivity.
- [ ] Configure structured JSON logging (`structlog`).

#### Sprint 2: Ingestion & Extraction (Days 8–18)
- [ ] Build `POST /upload` endpoint: MIME validation, size check, SHA-256 hash, object storage write, initial DB record creation (`status=INGESTED`).
- [ ] Implement document type classifier (PDF text-layer detection using `pdfplumber`).
- [ ] Implement digital PDF extractor: page-by-page text extraction, `TextExtractionResult` output struct.
- [ ] Implement `OCRDriver` abstract interface.
- [ ] Implement `PaddleOCRDriver` with preprocessing (deskew, contrast).
- [ ] Implement `EasyOCRDriver` as fallback.
- [ ] Implement `OCR_PROVIDER` environment variable-based driver selection.
- [ ] Write unit tests for both extraction paths with synthetic fixtures.

#### Sprint 3: AI Structuring (Days 19–28)
- [ ] Define all Pydantic models: `InvoiceSchema`, `InvoiceLineItem`.
- [ ] Write system prompt and user prompt construction functions.
- [ ] Integrate `instructor` library with `openai` SDK for schema-enforced completions.
- [ ] Implement 3-attempt exponential backoff wrapper for transient OpenAI errors.
- [ ] Implement per-call token logging to `llm_usage_log`.
- [ ] Write `POST /process` endpoint wiring extraction output into the AI structuring call.
- [ ] Test against 5 real vendor invoice samples; document extraction accuracy.

#### Sprint 4: Validation & Storage (Days 29–38)
- [ ] Implement `ValidationService` with all 5 check functions (line, subtotal, grand total, tax, dates).
- [ ] Implement rounding tolerance configuration (default ±0.02).
- [ ] Implement composite confidence scoring function.
- [ ] Implement document status assignment logic (`VALIDATED` vs. `REVIEW`).
- [ ] Implement atomic database transaction: `INSERT invoices + invoice_items + audit_logs`.
- [ ] Implement duplicate detection: hash check on upload; unique constraint guard on DB write.

#### Sprint 5: Export & Integration Testing (Days 39–47)
- [ ] Build `GET /invoices` with pagination and status filtering.
- [ ] Build `GET /invoice/{id}` returning full structured JSON.
- [ ] Build `GET /export` generating CSV from joined `invoices + invoice_items`.
- [ ] Write integration tests covering: happy path (digital PDF), scanned image path, duplicate rejection, validation failure routing, confidence score threshold.
- [ ] Benchmark extraction accuracy on 15 diverse real-world invoice samples. Document pass/fail rates.
- [ ] Fix regressions identified by real-world benchmark.

### Milestones

| Milestone | Target Day | Gate Condition |
|---|---|---|
| M1.1: Infrastructure Ready | Day 7 | `GET /health` returns 200; database tables created via migration |
| M1.2: Extraction Working | Day 18 | Both PDF and OCR paths return `TextExtractionResult` without errors |
| M1.3: AI Structuring Live | Day 28 | OpenAI returns valid `InvoiceSchema` for 3 test invoices |
| M1.4: Validation Complete | Day 38 | Math checks pass/fail correctly on all 5 validation test cases |
| M1.5: Phase 1 Complete | Day 47 | All 15 benchmark invoices processed; extraction accuracy ≥ 95% |

### Dependencies
- OpenAI API key provisioned and tested.
- PostgreSQL instance running (local or cloud).
- Object storage bucket created with write credentials.
- At least 15 real-world invoice PDFs and images collected for benchmark testing.

### Risks
| Risk | Mitigation |
|---|---|
| LLM returns structurally invalid JSON | `instructor` enforces schema; 2 internal retries before marking `FAILED_AI_STRUCTURING` |
| PaddleOCR installation fails on target OS | EasyOCR is available as immediate fallback; `OCR_PROVIDER` env var switches without code changes |
| Real-world accuracy falls below 95% target | Iterate prompt engineering before declaring Phase 1 complete; lower acceptance threshold temporarily to 90% only with documented justification |

### Success Criteria
- `POST /upload` + `POST /process` pipeline completes end-to-end without unhandled exceptions.
- 95%+ extraction accuracy across the 15-invoice benchmark set.
- Math validation catches 100% of deliberately injected calculation errors in test fixtures.
- `GET /export` produces a valid CSV file parseable by Excel and LibreOffice Calc.
- All database writes are inside transactions; no orphaned records on failure.
- `llm_usage_log` records are written for every OpenAI API call.

### Timeline Estimate
**Total: 9–10 working weeks** (47 working days with buffer for iteration and QA).

---

## Phase 2: Workflow & ERP Integration

### Objectives
- Add human oversight tooling for low-confidence and failed invoices.
- Connect the validated data store to real ERP systems, eliminating manual data re-entry.
- Introduce asynchronous processing to decouple the API gateway from slow AI and OCR operations.
- Harden multi-tenant data isolation for organizational-scale use.

### Deliverables
- Web-based Human-in-the-Loop (HITL) review dashboard (read-only view + edit + approve flow).
- Review queue: invoices in `REVIEW` status surface to the dashboard automatically.
- Asynchronous ingestion queue (Redis + Celery or RabbitMQ) decoupling `POST /upload` from processing.
- Webhook delivery endpoint for ERP systems to poll processing results.
- Pluggable `ERPAdapter` interface with first implementations: Odoo, QuickBooks Online.
- `erp_sync_logs` table tracking sync attempts, results, and ERP-side IDs.
- ERP sync retry cron job (every 15 minutes for failed syncs).
- Expanded audit trail capturing reviewer edits before and after.
- Row-Level Security (RLS) enforced at the PostgreSQL level.
- JWT-based authentication for the web review UI.
- `PATCH /invoice/{id}` endpoint for reviewer corrections.
- `POST /invoice/{id}/approve` endpoint triggering ERP sync.

### Tasks

#### Sprint 6: Async Queue Infrastructure (Days 1–8)
- [ ] Set up Redis instance (or RabbitMQ) in staging environment.
- [ ] Implement Celery worker configuration with `BROKER_URL` from environment.
- [ ] Refactor `POST /upload` to publish a `document.uploaded` Celery task instead of calling the pipeline synchronously.
- [ ] Implement worker task `process_document(document_uuid)` executing the existing Phase 1 pipeline.
- [ ] Verify `POST /upload` returns `202 Accepted` immediately; processing continues in worker.
- [ ] Add queue depth metric to monitoring.

#### Sprint 7: Review UI (Days 9–20)
- [ ] Design review dashboard wireframes and data requirements.
- [ ] Build minimal web frontend (React or server-rendered template) with:
  - Invoice list view filtered by `status=REVIEW`.
  - Side-by-side view: raw document image + extracted fields.
  - Inline editable fields with field-level validation feedback.
  - Approve / Reject action buttons.
- [ ] Implement `PATCH /invoice/{id}` endpoint validating and saving reviewer corrections.
- [ ] Implement `POST /invoice/{id}/approve` endpoint changing status to `VALIDATED` and triggering ERP sync task.
- [ ] Ensure audit log captures `user_id`, `before_state`, and `after_state` for every correction.

#### Sprint 8: ERP Integration (Days 21–32)
- [ ] Define `ERPAdapter` abstract interface (`sync(invoice: InvoiceSchema) → ERPSyncResult`).
- [ ] Create `erp_sync_logs` database table (migration).
- [ ] Implement `OdooAdapter` using Odoo XML-RPC or REST API.
- [ ] Implement `QuickBooksAdapter` using QuickBooks Online REST API (OAuth 2.0).
- [ ] Implement sync Celery task triggered on invoice approval.
- [ ] Implement ERP sync retry cron (failed syncs retried every 15 minutes, max 5 attempts).
- [ ] Write integration tests using mocked ERP API responses.

#### Sprint 9: Security & Multi-Tenancy Hardening (Days 33–40)
- [ ] Implement PostgreSQL Row-Level Security (RLS) policies on all tables.
- [ ] Implement `SET LOCAL app.current_org_id = :org_id` injection in all DB sessions.
- [ ] Implement JWT authentication for the review dashboard.
- [ ] Implement RBAC role enforcement at API middleware level (`admin`, `reviewer`, `viewer`).
- [ ] Penetration test: verify that API keys from Tenant A cannot read Tenant B records.

### Milestones

| Milestone | Target Day | Gate Condition |
|---|---|---|
| M2.1: Async Queue Live | Day 8 | `POST /upload` returns 202 in < 200ms; worker processes document in background |
| M2.2: Review UI Functional | Day 20 | Reviewer can view, edit, and approve a flagged invoice end-to-end |
| M2.3: ERP Sync Working | Day 32 | Approved invoice appears in Odoo or QuickBooks after approval |
| M2.4: Phase 2 Complete | Day 40 | RLS verified; JWT auth live; full Phase 2 integration test suite green |

### Dependencies
- Phase 1 `VALIDATED` → `REVIEW` status logic is stable and tested.
- ERP developer accounts (Odoo staging instance or QuickBooks Sandbox) provisioned.
- Redis or RabbitMQ instance available in staging.
- Frontend build toolchain decided (React + Vite, or server-side rendering).

### Risks
| Risk | Mitigation |
|---|---|
| ERP API authentication complexity (OAuth 2.0 flows) | Allocate 3 dedicated days per ERP adapter; use official SDK libraries where available |
| Review UI scope creep | Fix UI to the minimum viable review flow: list → detail → edit → approve; defer advanced filters |
| Celery task failures silently swallowed | Implement dead-letter queue; alert on task failure rate > 2% |

### Success Criteria
- `POST /upload` response time < 300ms with async queue enabled.
- A reviewer can correct and approve a flagged invoice within 5 clicks.
- Approved invoices appear in the ERP system within 60 seconds.
- Cross-tenant data leakage test returns zero violations.

### Timeline Estimate
**Total: 8 working weeks** (40 working days).

---

## Phase 3: Vendor Intelligence & Product Memory

### Objectives
- Reduce per-invoice OpenAI API costs by learning vendor-specific extraction patterns.
- Automate the mapping of vendor line-item descriptions to internal product SKUs.
- Introduce price anomaly detection to protect against supplier overcharging.
- Build the Vector Database foundation that Phase 4 analytics depend on.

### Deliverables
- `vendor_prompt_cache` database table storing per-vendor prompt strategies and schema hints.
- Vendor identification logic: matching incoming documents to known vendors via `vendor_tax_id`.
- Prompt strategy retrieval: pulling cached prompt templates on recognized vendors.
- Cache update feedback loop: reviewer corrections in Phase 2 UI update the vendor prompt cache automatically.
- pgvector extension enabled on PostgreSQL (or Qdrant deployed as standalone service).
- Product catalog import tooling: bulk CSV import of SKU master data.
- Product Memory enrichment worker: post-validation background job embedding `invoice_item.description` strings.
- Cosine similarity SKU matching against the product catalog.
- Manual SKU assignment UI (extension of Phase 2 review dashboard).
- Price deviation alerts: `invoice_item.unit_price` compared against vendor historical averages.
- Cost analytics endpoint: daily token usage and cost summary per organization.

### Tasks

#### Sprint 10: Vendor Template Cache (Days 1–10)
- [ ] Create `vendor_prompt_cache` table: `(id, organization_id, vendor_id, prompt_template, schema_hints_json, success_rate, last_used_at)`.
- [ ] Write vendor identification function: `identify_vendor(extracted_tax_id) → Vendor | None`.
- [ ] Modify AI Structuring Service to check cache before building generic prompt.
- [ ] Implement cache write-back: after a successful extraction with high confidence, persist the effective prompt strategy.
- [ ] Implement cache update on reviewer correction: when a reviewer edits fields, reduce confidence of the cached strategy and log the correction pattern.
- [ ] Write benchmark comparing token usage before and after cache introduction on a corpus of 50 invoices from 5 known vendors.

#### Sprint 11: Vector Database & Embeddings Setup (Days 11–18)
- [ ] Enable `pgvector` extension on PostgreSQL staging instance (`CREATE EXTENSION vector`).
- [ ] Add `embedding_vector VECTOR(1536)` column to `products` table (migration).
- [ ] Create HNSW index: `CREATE INDEX ON products USING hnsw (embedding_vector vector_cosine_ops)`.
- [ ] Build bulk product catalog import CLI: accepts CSV (`sku`, `name`, `description`, `category`), generates embeddings, inserts rows.
- [ ] Validate index query performance on a catalog of 10,000+ products.

#### Sprint 12: Product Memory Enrichment (Days 19–28)
- [ ] Implement `ProductMemoryWorker` Celery task triggered after each invoice reaches `VALIDATED`.
- [ ] For each `invoice_item`, call `text-embedding-3-small` to generate a 1536-dimension embedding.
- [ ] Query `products` table via cosine similarity; apply match threshold (default 0.92).
- [ ] On match: write `product_sku` to `invoice_items.product_sku`; log match confidence.
- [ ] On no-match: surface item in review dashboard under "Unmatched Items" panel.
- [ ] Manual SKU assignment: reviewer assigns SKU in dashboard; save and use as training signal.

#### Sprint 13: Price Anomaly Detection & Cost Optimization (Days 29–36)
- [ ] Build `vendor_price_history` view: aggregates `unit_price` per `(vendor_id, product_sku)` over time.
- [ ] Implement price deviation check: flag items where current `unit_price` exceeds rolling 90-day average by more than a configurable threshold (default 15%).
- [ ] Write `price_alerts` database table and alert writer.
- [ ] Build `GET /alerts?organization_id=:id` endpoint returning active price alerts.
- [ ] Build cost optimization report: token usage per vendor, cost per invoice, projected savings from cache hit rate.

### Milestones

| Milestone | Target Day | Gate Condition |
|---|---|---|
| M3.1: Vendor Cache Live | Day 10 | Known vendors hit cache; token usage drops ≥ 20% vs. baseline on test corpus |
| M3.2: Vector DB Ready | Day 18 | Product catalog indexed; cosine similarity queries return results in < 100ms |
| M3.3: Product Memory Working | Day 28 | ≥ 80% of recognized product SKUs correctly matched on benchmark invoice set |
| M3.4: Phase 3 Complete | Day 36 | Price alerts fire correctly; cost dashboard live |

### Dependencies
- Phase 2 human review workflow is stable (reviewer corrections drive cache updates).
- A product catalog master data file (CSV) is available for the initial Vector DB load.
- `pgvector` extension is supported by the PostgreSQL hosting provider (or Qdrant is deployed).
- At least 3 months of Phase 1 + Phase 2 extraction history available to build meaningful price baselines.

### Risks
| Risk | Mitigation |
|---|---|
| Product catalog embeddings are too generic to distinguish similar items | Use `name + description` concatenation rather than `name` alone for embedding input |
| Vendor prompt cache degrades accuracy for layout-updated vendors | Monitor cache hit accuracy score; auto-invalidate cache entries below 85% success rate |
| pgvector HNSW index build time on large catalogs | Run index build offline on a replica; promote replica to primary after build completes |

### Success Criteria
- Average token consumption per invoice drops ≥ 20% compared to Phase 1 baseline after vendor cache is live.
- ≥ 80% of line items in benchmark set receive a correct automatic SKU assignment.
- Price deviation alerts fire for 100% of deliberately overpriced test invoices.
- `pgvector` SKU similarity queries complete in < 100ms on a catalog of 50,000 products.

### Timeline Estimate
**Total: 7–8 working weeks** (36 working days).

---

## Phase 4: Business Intelligence & Analytics

### Objectives
- Give non-technical stakeholders a self-service interface to query invoice history.
- Automate scheduled financial reporting to reduce manual AP summarization work.
- Deliver dashboards that surface spend trends, vendor performance, and tax liabilities.
- Introduce the Natural Language Query engine as the final layer of the intelligence platform.

### Deliverables
- Materialized SQL views for core analytics: monthly spend by vendor, spend by category, tax liability summaries.
- Nightly scheduled view refresh cron jobs.
- Analytics REST API layer: endpoints serving aggregated report data to frontend consumers.
- Web-based analytics dashboard with charts: spend-over-time, vendor ranking, category breakdown.
- Automated monthly and weekly spend report generator (PDF export or email delivery).
- Report scheduling UI: allow users to configure recurring report cadences.
- Natural Language Query (NLQ) interface: chat input → Text-to-SQL → read-only PostgreSQL execution → formatted answer.
- NLQ security sandbox: read-only replica, table allowlist, 5-second query timeout enforcement.
- NLQ query history log: stores queries, generated SQL, and results for audit and debugging.

### Tasks

#### Sprint 14: Analytics Data Layer (Days 1–10)
- [ ] Design materialized views: `mv_monthly_vendor_spend`, `mv_category_spend`, `mv_tax_liability`, `mv_invoice_volume`.
- [ ] Write migrations creating all materialized views.
- [ ] Implement nightly refresh cron task using Celery Beat.
- [ ] Build analytics API endpoints: `GET /analytics/spend-by-vendor`, `GET /analytics/spend-by-category`, `GET /analytics/tax-summary`.
- [ ] Write query parameter support: `from_date`, `to_date`, `organization_id`.

#### Sprint 15: Dashboard Frontend (Days 11–22)
- [ ] Design dashboard page layout: date range selector, vendor filter, chart panels.
- [ ] Implement line chart: monthly invoice volume and spend over time.
- [ ] Implement bar chart: top 10 vendors by total spend.
- [ ] Implement donut chart: spend distribution by product category.
- [ ] Implement tax liability summary card.
- [ ] Add export button: download dashboard view as PDF report.
- [ ] Implement report scheduling UI: frequency selector (weekly/monthly), recipient email input.

#### Sprint 16: Automated Reporting Engine (Days 23–30)
- [ ] Build `ReportGenerator` service: queries analytics views, assembles structured report data.
- [ ] Implement PDF report template (using `WeasyPrint` or `reportlab`).
- [ ] Implement email delivery task (SMTP or SendGrid integration).
- [ ] Implement Celery Beat schedule for triggered reports: on-demand and recurring.
- [ ] Write records to `reports` table: output URL, parameters, recipient, delivery status.

#### Sprint 17: Natural Language Query Engine (Days 31–42)
- [ ] Deploy read-only PostgreSQL replica for NLQ execution.
- [ ] Build schema context document: curated description of all permitted tables and columns for LLM input.
- [ ] Implement NLQ service: accepts free-text query → builds prompt with schema context → calls LLM → extracts SQL → validates against table allowlist → executes on read-only replica → formats answer.
- [ ] Implement 5-second query timeout enforcement.
- [ ] Implement SQL allowlist validator: block any `INSERT`, `UPDATE`, `DELETE`, `DROP` statements.
- [ ] Build `POST /nlq` API endpoint.
- [ ] Build chat interface UI component in dashboard.
- [ ] Write NLQ query history to `nlq_query_log` table.
- [ ] Test with 20 representative natural language queries; document success/failure rates.

### Milestones

| Milestone | Target Day | Gate Condition |
|---|---|---|
| M4.1: Analytics Views Live | Day 10 | All materialized views refresh without error; analytics endpoints return data |
| M4.2: Dashboard Live | Day 22 | All dashboard charts render correctly against real Phase 1–3 data |
| M4.3: Reports Automated | Day 30 | Scheduled monthly report delivered to test inbox with correct figures |
| M4.4: NLQ Functional | Day 42 | 80%+ of benchmark queries return a correct, formatted answer |
| M4.5: Phase 4 Complete | Day 42 | Full platform integration test suite passes across all four phases |

### Dependencies
- At least 6 months of Phase 1 + Phase 2 + Phase 3 extraction data available to populate analytics views meaningfully.
- Phase 3 product SKU mapping is working (enables spend-by-category analytics).
- A read-only PostgreSQL replica is available or can be provisioned.
- Email delivery infrastructure (SMTP server or SendGrid account) is provisioned.

### Risks
| Risk | Mitigation |
|---|---|
| NLQ generates dangerous SQL | Read-only replica + allowlist validator + timeout; no writes are possible even if SQL is malformed |
| Analytics views are slow on large datasets | Ensure indexes on `invoice_date`, `vendor_id`, `organization_id`; materialized views precalculate aggregations |
| NLQ accuracy too low for business use | Limit NLQ to pre-defined query patterns in Phase 4.0; expand to free-form queries in Phase 4.1 after accuracy tuning |

### Success Criteria
- All four materialized views refresh nightly without failure for 30 consecutive days.
- Dashboard page load time < 2 seconds on a dataset of 10,000 invoices.
- Monthly report PDF is generated and delivered within 5 minutes of the scheduled trigger.
- NLQ returns a correct answer for ≥ 80% of the 20-query benchmark set.
- Zero successful SQL injection or data-exfiltration attempts in NLQ penetration test.

### Timeline Estimate
**Total: 8–9 working weeks** (42 working days).

---

## Project Management

### Resource Requirements

| Resource | Phase 1 | Phase 2 | Phase 3 | Phase 4 |
|---|---|---|---|---|
| Backend Engineers | 2 FTE | 2 FTE | 1.5 FTE | 1.5 FTE |
| AI/ML Engineer | 1 FTE | 0.5 FTE | 1 FTE | 1 FTE |
| Frontend Engineer | — | 1 FTE | 0.5 FTE | 1 FTE |
| DevOps (part-time) | 0.5 FTE | 0.5 FTE | 0.5 FTE | 0.5 FTE |
| QA (part-time) | 0.5 FTE | 0.5 FTE | 0.5 FTE | 0.5 FTE |

### Infrastructure Requirements

| Component | Phase 1 | Phase 2 | Phase 3 | Phase 4 |
|---|---|---|---|---|
| PostgreSQL | ✅ Primary instance | ✅ + RLS enforced | ✅ + pgvector extension | ✅ + Read replica |
| Object Storage | ✅ S3/Supabase | ✅ | ✅ | ✅ |
| OCR Compute | ✅ Local process | ✅ Worker node | ✅ Worker node | ✅ Worker node |
| Task Queue | — | ✅ Redis + Celery | ✅ | ✅ Celery Beat |
| Vector Database | — | — | ✅ pgvector or Qdrant | ✅ |
| Web Frontend Hosting | — | ✅ Static + API | ✅ | ✅ |
| Email Delivery | — | — | — | ✅ SMTP / SendGrid |

### Third-Party Services

| Service | Purpose | Required From |
|---|---|---|
| OpenAI API | AI structuring, embeddings (Phase 3+) | Phase 1 |
| AWS S3 or Supabase Storage | Raw file storage | Phase 1 |
| PaddleOCR / EasyOCR | Local OCR (open-source, no cost) | Phase 1 |
| Google Cloud Vision API | High-quality OCR fallback | Phase 1 Sprint 2 |
| Redis | Task queue broker | Phase 2 |
| Odoo / QuickBooks API | ERP synchronization | Phase 2 |
| SendGrid or SMTP | Automated report delivery | Phase 4 |

### Budget Considerations

| Cost Item | Estimated Range | Notes |
|---|---|---|
| OpenAI API (Phase 1) | $20–$80/month | Based on `gpt-4o-mini` at $0.15/1M input tokens; ~3,000 invoices/month |
| OpenAI Embeddings (Phase 3) | $5–$15/month | `text-embedding-3-small` at $0.02/1M tokens |
| AWS S3 Storage | $2–$10/month | Based on 5–50GB raw invoice files |
| PostgreSQL Hosting | $25–$80/month | Managed instance (AWS RDS, Supabase, Neon) |
| Redis | $15–$30/month | Small managed Redis instance (Phase 2+) |
| Google Cloud Vision | $0–$15/month | $1.50/1000 pages; only used for fallback OCR |
| Infrastructure Total | ~$70–$230/month | Scales proportionally with invoice volume |

> **Cost Control Note**: The digital PDF bypass path ensures that the majority of invoices never touch paid OCR services. Prompt engineering in Phase 3 further reduces per-invoice token consumption. The system is designed to process up to 5,000 invoices/month under $100 in API costs.

---

## Success Metrics

### Technical KPIs

| KPI | Phase 1 Target | Phase 2 Target | Phase 3 Target | Phase 4 Target |
|---|---|---|---|---|
| Extraction accuracy (clear digital PDFs) | ≥ 95% | ≥ 97% | ≥ 98% | ≥ 98% |
| Extraction accuracy (OCR scans) | ≥ 85% | ≥ 88% | ≥ 90% | ≥ 90% |
| Math validation pass rate (committed records) | 100% | 100% | 100% | 100% |
| Digital PDF pipeline latency | < 3 seconds | < 3 seconds | < 2 seconds | < 2 seconds |
| OCR pipeline latency | < 10 seconds | < 10 seconds | < 8 seconds | < 8 seconds |
| API response time (`POST /upload`) | < 500ms | < 300ms | < 300ms | < 300ms |
| Test suite coverage | ≥ 80% | ≥ 85% | ≥ 85% | ≥ 90% |

### Business KPIs

| KPI | Target | Measurement |
|---|---|---|
| Manual review rate (digital PDFs) | < 10% | `(REVIEW / total)` per week |
| Manual review rate (scanned images) | < 30% | `(REVIEW / total)` per week |
| AP data-entry time saved | > 80% vs. manual | Estimated hours saved per invoice |
| ERP sync success rate | > 98% | `(synced / approved)` per day |
| Duplicate invoice prevention | 100% | Zero duplicate DB records |

### Operational KPIs

| KPI | Target | Measurement |
|---|---|---|
| Cost per invoice (OpenAI) | < $0.03 | `total_openai_cost / invoices_processed` |
| API uptime | 99.9% monthly | Uptime monitoring (Phase 2+) |
| Mean time to recovery (MTTR) on pipeline failures | < 30 minutes | Incident log average |
| Token budget violation rate | < 1% of invoices | `invoices > 6,000 tokens / total` |
| Product SKU auto-match rate | > 80% | `(matched / total_items)` (Phase 3+) |

---

## Final Roadmap

| Phase | Duration | Key Deliverables | Phase Dependencies | Expected Outcomes |
|---|---|---|---|---|
| **Phase 1** Core Extraction MVP | 9–10 weeks | Upload API, PDF/OCR extraction, OpenAI structuring, Validation engine, PostgreSQL storage, CSV export | OpenAI API key, PostgreSQL instance, Object storage bucket, 15+ real invoice samples | Fully functional, zero-manual-entry invoice extraction pipeline. Validated data foundation ready for all downstream phases. |
| **Phase 2** Workflow & ERP Integration | 8 weeks | Human review dashboard, async queue, ERP adapters (Odoo, QuickBooks), JWT auth, RLS enforcement | Phase 1 VALIDATED/REVIEW status flow stable. ERP developer accounts. Redis available. | AP teams can manage flagged invoices via web UI. Validated data automatically syncs to ERP, eliminating manual re-entry. |
| **Phase 3** Vendor Intelligence & Product Memory | 7–8 weeks | Vendor prompt cache, pgvector + product catalog, Product Memory enrichment worker, price anomaly alerts | Phase 2 reviewer corrections available as training signals. Product catalog CSV ready. pgvector supported. 3+ months of extraction history. | 20%+ reduction in per-invoice API token costs. 80%+ of line items auto-catalogued by SKU. Price deviation alerts active. |
| **Phase 4** Business Intelligence & Analytics | 8–9 weeks | Analytics materialized views, spend dashboards, automated reporting, NLQ chat interface | Phase 3 product SKU mapping stable. 6+ months of extraction history. Read-only DB replica. Email delivery configured. | Non-technical stakeholders query invoice data in plain English. Monthly reports delivered automatically. Full platform intelligence layer live. |
| **Future Scope** | TBD | Three-way PO matching, predictive cash flow, multi-currency compliance | Phase 4 NLQ and analytics layer fully stable | End-to-end intelligent invoice processing, from receipt to ERP entry to strategic business forecasting. |
