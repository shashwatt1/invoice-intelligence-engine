# Invoice Intelligence Engine

Converts supplier invoices (PDF, PNG, JPEG) into structured, validated
data through an OCR + AI extraction pipeline, with deterministic export
formats for downstream systems.

## Overview

The system ingests an uploaded invoice, extracts its text, structures it
into a canonical schema using an LLM, validates the result with
deterministic business rules, and persists it to PostgreSQL. From there
the invoice can be exported as JSON, a human-readable text summary, CSV,
or a fixed-width positional format for an external import system.

A React frontend provides a dashboard, an upload/processing view with a
live status timeline, invoice history, and an invoice detail view
(validation report, structured data viewer, developer panel). The
frontend consumes the backend exclusively through its REST API.

## Current architecture

```
Supplier Invoice (PDF / PNG / JPEG)
        │
        ▼
  Text Extraction        pdfplumber for digital PDFs; Google Vision OCR
        │                for scanned/image documents
        ▼
  AI Structured Extraction   OpenAI Structured Outputs, versioned prompt,
        │                    schema-constrained JSON response
        ▼
  Validation Engine       deterministic math checks, confidence scoring,
        │                 VALIDATED / REVIEW_REQUIRED decision — no AI
        ▼
  PostgreSQL Persistence  one atomic transaction: vendor + invoice + items
        │
        ▼
  Export                  JSON / TXT / CSV / fixed-width, all derived from
                           the same persisted record
```

The LLM's role is strictly document understanding — reading what is
printed on the page. Every decision made after extraction (validation
math, confidence scoring, export formatting) is deterministic code with
no AI involvement, so results are reproducible and auditable.

## Technology stack

**Backend** — Python 3.11+, FastAPI, SQLAlchemy 2.0 (async), Alembic,
PostgreSQL, pdfplumber, Google Cloud Vision API, OpenAI API (Structured
Outputs), structlog.

**Frontend** — React 19, TypeScript, Vite, TailwindCSS, shadcn/ui,
TanStack Query, Framer Motion, Recharts.

**Testing** — pytest (offline unit tests + Postgres-backed integration
tests), ruff, oxlint.

## Project principles

These constraints have shaped every change made to this codebase and
should continue to:

- **The backend is the single source of truth.** The frontend never
  makes a business decision — it renders what the API returns.
- **Business logic lives in the backend**, never in the frontend and
  never in a repository (repositories perform data access only).
- **AI is used only for document understanding** — extracting what is
  printed on a page. It never makes a validation, matching, or
  formatting decision.
- **Validation and formatting are deterministic.** Given the same input,
  they always produce the same output, with no model call involved.
- **Existing working functionality is preserved.** Changes are additive
  by default; modifying an existing, working component requires a
  specific, stated reason.

## Repository structure

```
app/
  api/v1/            FastAPI routers (one file per resource)
  core/               Configuration, exceptions, logging
  database/           Engine/session setup, declarative base
  middleware/          Request-ID and exception-handling middleware
  models/             SQLAlchemy ORM models
  repositories/       Data-access layer — one repository per aggregate,
                        no business logic
  schemas/            Pydantic contracts (LLM-boundary, normalized, API)
  services/           Business logic
    ocr/               OCR provider abstraction (pdfplumber, Google Vision)
    llm/                LLM provider abstraction (OpenAI)
    validation/         Deterministic validation engine
  prompts/            Versioned extraction prompts (never mutate a
                        shipped version — add a new one)
alembic/              Database migrations
web/                  React frontend
tests/                Offline unit tests (no external dependencies)
tests/integration/    Postgres-backed integration tests
docs/archive/         Superseded early planning documents (historical
                        reference only — see docs/archive/README.md)
```

## Environment configuration

Copy the template and fill in your own values:

```bash
cp .env.example .env
```

Required for full functionality:

| Variable | Purpose |
|---|---|
| `OPENAI_API_KEY` | AI structured extraction |
| `GOOGLE_VISION_API_KEY` | OCR for scanned/image invoices (not needed for digital PDFs, which are parsed directly) |
| `DATABASE_URL` / `DATABASE_URL_SYNC` | PostgreSQL connection (async / sync-for-Alembic) |

`DATABASE_URL` defaults to the credentials used by `docker-compose.yml`
for local development only. **Rotate these before using any shared,
staging, or production database** — they are not safe to reuse outside a
local machine.

See `.env.example` for the full list of configuration options (OCR/LLM
provider selection, validation tolerances, logging, storage backend).

## Local development setup

Prerequisites: Python 3.11+, Node.js 20+, Docker.

```bash
git clone <repo-url>
cd invoice-intelligence-engine

# Backend
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

# Frontend
cd web && npm install && cd ..

# Environment
cp .env.example .env   # then fill in OPENAI_API_KEY / GOOGLE_VISION_API_KEY
```

## Running database migrations

```bash
docker compose up -d db          # start PostgreSQL
.venv/bin/python -m alembic upgrade head
```

To create a new migration after changing a model:

```bash
.venv/bin/python -m alembic revision --autogenerate -m "describe the change"
.venv/bin/python -m alembic upgrade head
```

Always review an autogenerated migration before applying it — autogenerate
detects schema drift but does not understand intent.

## Running the backend

```bash
.venv/bin/uvicorn app.main:app --port 8000
```

API docs: `http://localhost:8000/docs`

## Running the frontend

```bash
cd web
npm run dev
```

Dashboard: `http://localhost:5173` (proxies API calls to `:8000` in
development).

## Running tests

```bash
# Offline suite — no external dependencies required
.venv/bin/pytest -q

# Integration suite — requires the Postgres container running
RUN_DB_TESTS=1 .venv/bin/pytest tests/integration -q

# Optional: live-LLM smoke test (uses real OpenAI API credits)
RUN_LIVE_LLM_TESTS=1 .venv/bin/pytest tests/integration -q
```

Frontend:

```bash
cd web
npm run lint
npm run build
```

## Development workflow

- Prefer additive changes. If a working component needs to change, state
  why before changing it.
- Run the offline test suite before every commit; run the integration
  suite before anything touching persistence, the pipeline, or the API.
- Evolving an extraction prompt means adding a new version in
  `app/prompts/`, never editing a version already in use — a persisted
  invoice's `prompt_version` must stay interpretable.
- Database schema changes are additive migrations (new tables/columns
  with safe defaults) unless a genuine defect requires otherwise.

## Troubleshooting

**`docker compose up -d db` fails / Docker commands hang** — Docker
Desktop may be stopped or paused; start it and retry.

**A processed document lands in `FAILED` at the `AI_STRUCTURING`
stage** — check `GET /api/v1/documents/{id}` for the specific error.
Common causes: missing/invalid `OPENAI_API_KEY`, or an
`insufficient_quota` response from OpenAI (a billing state, not a code
error — confirm via the OpenAI dashboard).

**Re-uploading the same file returns `409 ERR_DUPLICATE_DOCUMENT`** —
expected behavior. Documents are deduplicated by SHA-256 content hash.

**`alembic upgrade head` fails on a fresh database** — ensure the
Postgres container is healthy (`docker compose ps`) and `DATABASE_URL_SYNC`
in `.env` matches the running container's credentials.

## Security notes

- Never commit `.env` — it is gitignored; only `.env.example` (placeholder
  values only) is tracked.
- The default local database credentials in `docker-compose.yml` /
  `.env.example` are for local development only and must be rotated
  before use in any shared environment.
- No authentication or authorization layer currently exists on the API —
  do not expose this service outside a trusted network without adding
  one.
- Test fixtures use fictional company names and synthetic product codes
  by convention; do not introduce real business or customer data into
  committed test files.

## Contribution guidelines

- Commits should be scoped to one logical change with a clear message
  explaining *why*, not just *what*.
- Every change to backend logic should include or update tests; run the
  full relevant test suite before committing.
- Do not amend or force-push shared history.
