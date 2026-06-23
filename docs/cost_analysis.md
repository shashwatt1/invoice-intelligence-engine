# Cost Analysis

## Purpose

This document provides a comprehensive, stage-by-stage cost analysis of the Invoice Intelligence Platform. It is designed to answer a founder's core question:

**"What will this solution cost today, and what will it cost when we scale?"**

The analysis begins at MVP level — a single developer running the system for internal use — and scales progressively through early customer adoption, growth stage, and full production deployment. Every estimate uses current vendor pricing (as of June 2026), realistic assumptions about invoice characteristics, and the platform's actual architectural decisions.

This document should be used for:

- **Budget planning** — How much runway is needed for each phase.
- **Pricing decisions** — What per-invoice cost must be covered to achieve profitability.
- **Vendor selection** — Which providers offer the best value at each stage.
- **Investor conversations** — Demonstrating cost-awareness and unit economics.

### Three-Scenario Framework

All cost projections in this document are presented under three scenarios:

| Scenario | Meaning |
|---|---|
| **Optimistic** | Best-case assumptions hold: high digital PDF ratio, low retry rates, compact invoices, free tiers fully utilized |
| **Expected** | Realistic operating conditions based on industry norms and platform architecture |
| **Conservative** | Pessimistic but plausible: higher scanned ratio, complex invoices, more LLM retries, production-grade monitoring |

---

## Costing Assumptions

All estimates in this document are based on the following baseline assumptions:

| Assumption | Optimistic | Expected | Conservative | Rationale |
|---|---|---|---|---|
| Average invoice page count | 1.2 pages | 1.5 pages | 2.0 pages | Most vendor invoices are 1–2 pages; wholesale invoices can reach 3+ |
| Average file size (digital PDF) | 120 KB | 150 KB | 200 KB | Depends on embedded fonts and formatting |
| Average file size (scanned image) | 600 KB | 800 KB | 1.2 MB | Higher-resolution scans produce larger files |
| Digital PDF ratio | 80% | 70% | 50% | Enterprise customers skew digital; grocery/wholesale skews scanned |
| Scanned/image ratio | 20% | 30% | 50% | Inverse of digital ratio |
| Average line items per invoice | 5 items | 8 items | 15 items | Varies by vendor and industry |
| Average input tokens per invoice | ~900 tokens | ~1,100 tokens | ~1,400 tokens | System prompt (120) + schema hint (80) + document text (700–1,200) |
| Average output tokens per invoice | ~300 tokens | ~450 tokens | ~600 tokens | JSON output scales with line item count |
| GPT-4o fallback rate | 5% | 10% | 15% | Higher in Phase 1 before vendor prompt caching |
| LLM retry rate (Instructor) | 3% | 5% | 8% | Schema enforcement retries on malformed output |
| Working days per month | 22 days | 22 days | 22 days | Standard business month |
| Data retention period | 7 years | 7 years | 7 years | Standard financial record retention |
| Currency | USD | USD | USD | All costs in US Dollars |

> **Why three scenarios?** Token estimates and digital/scanned ratios are the two largest variables in the cost model. A single-page digital PDF with 5 items generates ~900 input tokens. A 2-page scanned invoice with 15 items generates ~1,400 input tokens. The difference in LLM cost is 55%. Presenting a single point estimate would be misleading.

### Scale Tiers

| Tier | Invoices / Month | Typical User |
|---|---|---|
| **MVP** | 100–500 | Internal testing, single store |
| **Early Customers** | 1,000–3,000 | 1–5 small businesses |
| **Growth** | 5,000–15,000 | 10–30 businesses, multi-store |
| **Production** | 30,000–100,000 | Enterprise SaaS, regional chains |

---

## Database Costs

The platform uses PostgreSQL as its primary data store. Three managed hosting options are evaluated.

### PostgreSQL Local (Development Only)

| Aspect | Details |
|---|---|
| **Cost** | $0 |
| **Use Case** | Local development and testing |
| **Limitations** | No remote access, no backups, no high availability |
| **Recommendation** | Development only — never for production |

### Supabase

Supabase provides a managed PostgreSQL instance with built-in auth, storage, and API layers.

| Plan | Monthly Cost | Database Storage | File Storage | Best For |
|---|---|---|---|---|
| **Free** | $0 | 500 MB | 1 GB | MVP prototyping, demos |
| **Pro** | $25 | 8 GB included | 100 GB included | Early customers through Growth |
| **Team** | $599 | 8 GB + overages | 100 GB + overages | Enterprise features (SSO, priority support) |

**Storage overage**: $0.125/GB-month beyond included quota.

**Important clarification**: Most Growth-stage startups (10–30 customers) will stay on Supabase Pro for months or years. The Pro plan with storage overages is significantly cheaper than the Team plan. For example, a 20 GB database on Pro costs $25 + (12 GB × $0.125) = **$26.50/month** — not $599. The Team plan is needed for **organizational features** (SSO, priority support, compliance reporting), not for storage capacity.

**Supabase Advantages**:
- Built-in object storage (no separate S3 setup).
- Auth, Row-Level Security, and real-time subscriptions included.
- Dashboard for database management.
- Integrated Edge Functions for lightweight API logic.
- Pro plan includes daily backups with 7-day retention.

**Supabase Limitations**:
- Pro plan limited to 8 GB database storage before overages.
- Projects pause after 7 days of inactivity on the Free tier.
- Maximum of 2 active projects on Free tier.
- Direct connection limit (~60 concurrent connections on Pro). At Growth/Production scale with multiple workers, connection pooling (built-in PgBouncer) must be used.

### Neon

Neon is a serverless PostgreSQL provider with scale-to-zero capability and usage-based billing.

| Plan | Monthly Cost | Storage | Compute | Best For |
|---|---|---|---|---|
| **Free** | $0 | 0.5 GB | 100 CU-hours | Prototyping, hobby projects |
| **Launch** | Pay-as-you-go | $0.35/GB-month | $0.106/CU-hour | Early production |
| **Scale** | Pay-as-you-go | $0.35/GB-month | $0.222/CU-hour | Production workloads |

**Neon Advantages**:
- **Scale-to-zero**: Compute costs drop to $0 when the database is idle — ideal for MVP and low-traffic periods.
- Usage-based billing means you never pay for idle capacity.
- Instant database branching for staging/testing environments ($1.50/branch-month — useful for testing Alembic migrations).

**Neon Limitations**:
- Costs can be unpredictable under sustained high-traffic loads.
- No built-in object storage or auth (requires separate services).
- CU-hour billing requires careful monitoring.

### Database Cost Projections

Estimated monthly database costs at each scale tier:

| Scale Tier | Invoices/Month | Est. DB Size | Supabase | Neon (Launch) |
|---|---|---|---|---|
| **MVP** | 500 | ~50 MB | $0 (Free) | $0 (Free) |
| **Early Customers** | 3,000 | ~500 MB | $25 (Pro) | ~$3–8 |
| **Growth** | 15,000 | ~3 GB | $25 (Pro) | ~$15–30 |
| **Production** | 100,000 | ~20 GB | $25 + ~$1.50 overage | ~$40–80 |

> **Database size estimate**: ~10 KB per invoice record (header + 8 line items + audit log + indexes). 10,000 invoices ≈ 100 MB of relational data, plus indexes and overhead. Database size grows **cumulatively** — production databases after 12 months of operation at 100K invoices/month will reach ~120 GB, requiring careful capacity planning.

### Staging Database

A staging database is required from the Growth stage onward to safely test Alembic migrations and schema changes before applying them to production.

| Stage | Staging DB Cost | Rationale |
|---|---|---|
| **MVP** | $0 | Use local PostgreSQL for testing |
| **Early Customers** | $0 | Neon free-tier branch or local |
| **Growth** | $25/month | Supabase Pro second project or Neon branch |
| **Production** | $25–80/month | Dedicated staging instance mirroring production |

### Database Recommendation

| Stage | Recommendation | Rationale |
|---|---|---|
| **MVP** | **Supabase Free** | Zero cost, built-in storage + auth, sufficient for prototyping |
| **Early Customers** | **Supabase Pro** ($25/mo) | Stable pricing, built-in object storage eliminates S3 setup, daily backups included |
| **Growth** | **Supabase Pro + overages** | Stays at $25 base; overages are minimal until 20+ GB |
| **Production** | **Supabase Pro + overages** or **AWS RDS** | Evaluate based on query volume, compliance, and connection concurrency requirements |

---

## Backup and Disaster Recovery Costs

Financial data requires robust backup and disaster recovery (DR) infrastructure. These costs are often overlooked but are critical for a platform handling invoice records with a 7-year retention requirement.

### Backup Coverage by Provider

| Provider | Included Backups | Limitations |
|---|---|---|
| **Supabase Free** | None | No automated backups — unacceptable for any customer-facing use |
| **Supabase Pro** | Daily backups, 7-day retention | Sufficient for Early Customers and Growth |
| **Neon** | Point-in-time recovery (PITR) on paid plans | Good coverage; branching provides additional safety |
| **AWS RDS** | Automated daily snapshots, 35-day retention | Backup storage beyond provisioned DB size costs $0.095/GB-month |

### Additional DR Costs at Production

| DR Component | Monthly Cost | When Needed |
|---|---|---|
| Cross-region backup replication | $30–70 | Production (regulatory compliance) |
| Read replica for analytics/NLQ | $40–80 | Phase 4 (NLQ requires read-only replica) |
| Backup storage overages (AWS) | $5–15 | Production with large DB |
| **Total DR overhead** | **$0 (MVP–Growth)** | **$75–165 (Production)** |

### Backup Recommendation

| Stage | Approach | Cost |
|---|---|---|
| **MVP** | Supabase Free — manual `pg_dump` weekly | $0 |
| **Early Customers** | Supabase Pro — daily automated backups | $0 (included in $25) |
| **Growth** | Supabase Pro — daily automated backups | $0 (included in $25) |
| **Production** | AWS RDS automated + cross-region replication | $75–165/month |

---

## Object Storage Costs

Raw invoice files (PDFs, images) are stored in object storage. PostgreSQL stores only URI references.

### Supabase Storage

Included with Supabase plans — no additional vendor to manage.

| Plan | Included Storage | Overage Rate |
|---|---|---|
| Free | 1 GB | — (hard limit) |
| Pro | 100 GB | $0.021/GB-month |

### AWS S3 (Standard Tier)

| Pricing Component | Rate |
|---|---|
| Storage | $0.023/GB-month |
| PUT requests | $0.005 per 1,000 requests |
| GET requests | $0.0004 per 1,000 requests |
| Data transfer out | $0.09/GB (first 10 TB) |

### Storage Cost Projections

Using blended average file size (Expected scenario): `(0.70 × 150 KB) + (0.30 × 800 KB) = 345 KB per invoice`.

| Invoices Processed | Cumulative Storage | Supabase Pro | AWS S3 |
|---|---|---|---|
| **100** | 34.5 MB | $0 (included) | $0.001 |
| **1,000** | 345 MB | $0 (included) | $0.008 |
| **10,000** | 3.45 GB | $0 (included) | $0.08 |
| **100,000** | 34.5 GB | $0 (included) | $0.79 |
| **1,000,000** (cumulative after ~2 years) | 345 GB | $5.15/month overage | $7.94/month |

> **Note**: These are **cumulative** storage costs. Object storage grows over time as invoices are retained for 7 years. After 2 years of processing 500,000 invoices/year, storage reaches ~170 GB — still under $4/month on either platform.

### Storage Recommendation

| Stage | Recommendation | Rationale |
|---|---|---|
| **MVP → Growth** | **Supabase Storage** | Included with Supabase Pro plan, zero additional cost up to 100 GB |
| **Production** | **AWS S3** or **Supabase Storage** | Both are negligible cost; S3 offers more granular lifecycle policies for 7-year retention |

**Object storage is never a meaningful cost driver.** Even at 100,000 invoices, cumulative storage costs remain under $1/month. Focus cost optimization efforts elsewhere.

---

## OCR Costs

OCR is required only for **scanned PDFs and images**. Digital-native PDFs bypass OCR entirely, extracting text directly via `pdfplumber` at zero cost.

The proportion of invoices requiring OCR varies by customer segment:

| Scenario | Scanned Ratio | Rationale |
|---|---|---|
| **Optimistic** | 20% | Enterprise customers, mostly digital workflows |
| **Expected** | 30% | Mixed customer base |
| **Conservative** | 50% | Grocery/wholesale, paper-heavy suppliers |

### Provider Comparison

#### EasyOCR (Open Source — Local)

| Aspect | Details |
|---|---|
| **License** | Apache 2.0 (free) |
| **API Cost** | $0 |
| **RAM Requirement** | **~1.5 GB minimum** (language models loaded in memory) |
| **Compute Cost** | CPU-only: included in hosting; GPU: requires GPU instance ($50–200/month) |
| **Accuracy** | Good on clean documents (>90%); degrades on low-contrast or handwritten text |
| **Speed** | 2–5 seconds per page (CPU); 0.5–1 second per page (GPU) |
| **Languages** | 80+ languages supported |

**Pros**:
- Zero marginal cost per page.
- No external API dependency.
- Full data privacy — documents never leave your infrastructure.

**Cons**:
- Lower accuracy than cloud providers on complex layouts.
- **Requires a minimum 2 GB RAM hosting instance.** EasyOCR loads language models (~1.5 GB) into memory. Instances with 512 MB or 1 GB RAM will crash with out-of-memory errors. This means the Render Hobby tier ($0, 512 MB) and Render Starter tier ($7, 512 MB) **cannot run EasyOCR**.
- No built-in layout analysis or table detection.
- CPU processing adds 2–5 seconds per page to pipeline latency.

---

#### Google Cloud Vision API

| Aspect | Details |
|---|---|
| **Pricing** | $1.50 per 1,000 pages |
| **Free Tier** | First 1,000 pages/month free |
| **Volume Discount** | $0.60 per 1,000 pages above 5M pages/month |
| **Accuracy** | Excellent (>97% on printed text) |
| **Speed** | 0.5–2 seconds per page |

**Pros**:
- Highest accuracy among evaluated providers.
- No local compute required — fully managed cloud service.
- Excellent handling of rotated, skewed, and low-quality scans.
- Free tier covers MVP entirely.
- No local RAM overhead — offloads compute to Google.

**Cons**:
- Per-page cost scales linearly.
- Documents are sent to Google's servers (data residency considerations).
- Requires Google Cloud account and billing setup.

---

#### Google Document AI (Invoice Parser)

| Aspect | Details |
|---|---|
| **Pricing** | $10–30 per 1,000 pages (depends on processor type) |
| **Free Tier** | Limited trial pages |
| **Accuracy** | Very high — purpose-built for invoices |

**Pros**:
- Purpose-built invoice and receipt parsers.
- Returns structured fields directly (vendor, totals, line items).
- Could potentially replace both OCR and AI structuring layers.

**Cons**:
- Significantly more expensive per page ($10–30 vs. $1.50).
- Reduces platform flexibility — vendor lock-in to Google's field mappings.
- Less control over extraction logic and prompt engineering.
- Would make the AI Structuring Layer redundant, conflicting with the platform's architecture.

---

### OCR Cost Projections

Three scenarios based on scanned invoice ratio (at 1.5 pages per invoice):

| Monthly Volume | Optimistic (20%) | Expected (30%) | Conservative (50%) |
|---|---|---|---|
| | OCR Pages → Cost | OCR Pages → Cost | OCR Pages → Cost |
| **500** | 150 → $0 (free) | 225 → $0 (free) | 375 → $0 (free) |
| **3,000** | 900 → $0 (free) | 1,350 → $0.53 | 2,250 → $1.88 |
| **15,000** | 4,500 → $5.25 | 6,750 → $8.63 | 11,250 → $15.38 |
| **100,000** | 30,000 → $43.50 | 45,000 → $67.50 | 75,000 → $112.50 |

> **Note**: Costs above assume Google Vision API at $1.50/1,000 pages after free tier. EasyOCR costs $0 in API fees but requires a hosting instance with ≥2 GB RAM.

### OCR Recommendation

| Stage | Recommendation | Rationale |
|---|---|---|
| **MVP** | **EasyOCR (local)** on a ≥2 GB RAM instance | Zero API cost; sufficient accuracy for development and testing |
| **Early Customers** | **EasyOCR primary + Google Vision fallback** | Free tier covers most usage; Vision API only for failed extractions |
| **Growth** | **Google Vision API primary** | $8.63/month (expected) is negligible; accuracy improvement reduces manual review costs |
| **Production** | **Google Vision API** | Even at 100K invoices (conservative), OCR costs only $112.50/month |

> **Avoid**: Google Document AI for this platform. It costs 7–20× more than Vision API and conflicts with the platform's AI Structuring Layer architecture. The platform already handles structured extraction via OpenAI — paying for Document AI's built-in parsing is redundant.

---

## LLM Costs

The AI Structuring Layer is the platform's primary variable cost driver. All invoices — both digital PDFs and OCR-processed documents — pass through OpenAI's API for semantic structuring.

### Model Selection

The platform uses **GPT-4o-mini** as the primary model for Phase 1, with **GPT-4o** as a fallback for complex layouts.

| Model | Input Cost | Output Cost | Use Case |
|---|---|---|---|
| **GPT-4o-mini** | $0.15 / 1M tokens | $0.60 / 1M tokens | Primary extraction |
| **GPT-4o** | $2.50 / 1M tokens | $10.00 / 1M tokens | Fallback for complex/ambiguous layouts |

### Per-Invoice Token Calculation

Based on the platform's prompt engineering strategy (system prompt + schema hint + trimmed document text):

| Component | Optimistic | Expected | Conservative |
|---|---|---|---|
| System prompt | 120 tokens | 120 tokens | 120 tokens |
| Schema context hint | 80 tokens | 80 tokens | 80 tokens |
| Document text | 700 tokens | 900 tokens | 1,200 tokens |
| **Total input** | **~900 tokens** | **~1,100 tokens** | **~1,400 tokens** |
| Structured JSON response | ~300 tokens | ~450 tokens | ~600 tokens |

> **Why the range?** A single-page invoice with 5 items produces ~700 tokens of document text. A 2-page invoice with 15 items produces ~1,200 tokens. Output tokens scale similarly — each line item in the JSON response adds ~35–40 tokens (field names, values, braces).

### GPT-4o Fallback Rate

The fallback rate — the percentage of invoices that fail on GPT-4o-mini and require the more expensive GPT-4o model — is a significant cost variable, especially in Phase 1 before vendor prompt caching exists.

| Scenario | Fallback Rate | Rationale |
|---|---|---|
| **Optimistic** | 5% | Clean invoices, well-tuned prompts |
| **Expected** | 10% | Mixed vendor layouts, Phase 1 without prompt cache |
| **Conservative** | 15% | Complex multi-vendor layouts, unusual formatting |

**With Instructor retry rate** (5% of calls retried, consuming double tokens), the effective average tokens per invoice:

| Scenario | Effective Input | Effective Output |
|---|---|---|
| **Optimistic** | 927 tokens | 309 tokens |
| **Expected** | 1,155 tokens | 473 tokens |
| **Conservative** | 1,512 tokens | 648 tokens |

### Per-Invoice Cost Calculation

**GPT-4o-mini cost per invoice**:

| Scenario | Input Cost | Output Cost | Total |
|---|---|---|---|
| Optimistic | 927 × $0.15/1M = $0.000139 | 309 × $0.60/1M = $0.000185 | **$0.000324** |
| Expected | 1,155 × $0.15/1M = $0.000173 | 473 × $0.60/1M = $0.000284 | **$0.000457** |
| Conservative | 1,512 × $0.15/1M = $0.000227 | 648 × $0.60/1M = $0.000389 | **$0.000616** |

**GPT-4o cost per invoice** (fallback):

| Scenario | Input Cost | Output Cost | Total |
|---|---|---|---|
| Optimistic | 927 × $2.50/1M = $0.002318 | 309 × $10.00/1M = $0.003090 | **$0.005408** |
| Expected | 1,155 × $2.50/1M = $0.002888 | 473 × $10.00/1M = $0.004730 | **$0.007618** |
| Conservative | 1,512 × $2.50/1M = $0.003780 | 648 × $10.00/1M = $0.006480 | **$0.010260** |

**Blended per-invoice cost** (weighted by fallback rate):

| Scenario | Calculation | Per Invoice |
|---|---|---|
| **Optimistic** | (0.95 × $0.000324) + (0.05 × $0.005408) | **$0.000578** |
| **Expected** | (0.90 × $0.000457) + (0.10 × $0.007618) | **$0.001173** |
| **Conservative** | (0.85 × $0.000616) + (0.15 × $0.010260) | **$0.002063** |

### Monthly LLM Cost Projections

| Monthly Volume | Optimistic | Expected | Conservative |
|---|---|---|---|
| **100** | $0.06 | $0.12 | $0.21 |
| **500** | $0.29 | $0.59 | $1.03 |
| **1,000** | $0.58 | $1.17 | $2.06 |
| **3,000** | $1.73 | $3.52 | $6.19 |
| **5,000** | $2.89 | $5.87 | $10.32 |
| **15,000** | $8.67 | $17.60 | $30.95 |
| **30,000** | $17.34 | $35.19 | $61.89 |
| **100,000** | $57.80 | $117.30 | $206.30 |

### Embeddings Cost (Phase 3)

The Product Memory System uses `text-embedding-3-small` to embed line-item descriptions for SKU matching.

| Model | Cost |
|---|---|
| `text-embedding-3-small` | $0.02 / 1M tokens |

At 8 line items per invoice (expected), ~15 tokens per description:

```
Embedding tokens per invoice: 8 × 15 = 120 tokens
Cost per invoice: 120 × $0.02 / 1,000,000 = $0.0000024
```

Embeddings cost is **negligible** — less than $0.25/month even at 100,000 invoices.

### LLM Key Insight

> **LLM costs are low, but not as trivially low as a single-scenario analysis suggests.** At 100,000 invoices/month, the Expected scenario produces an OpenAI bill of ~$117/month — affordable, but roughly 2× the optimistic estimate. Under conservative assumptions (complex invoices, 15% GPT-4o fallback), costs reach ~$206/month. Even at the conservative end, the AI Structuring Layer is not a cost bottleneck — hosting and infrastructure dominate the budget.

---

## Hosting Costs

The platform requires hosting for:
- **FastAPI backend** (API server + processing pipeline)
- **Streamlit frontend** (upload interface + dashboards)
- **Celery workers** (Phase 2 — async processing)
- **Redis** (Phase 2 — task queue broker)

**Critical consideration**: If using EasyOCR for local OCR processing, the hosting instance **must have at least 2 GB of RAM**. EasyOCR loads language models (~1.5 GB) into memory on first use. Instances with 512 MB or 1 GB RAM will crash with out-of-memory errors. This eliminates the free/hobby tiers of most hosting providers from consideration if local OCR is part of the stack.

### Provider Comparison

#### Render

| Plan | Cost | Compute | Best For |
|---|---|---|---|
| **Hobby (Free)** | $0/month | Shared CPU, 512 MB RAM | Demos without OCR only |
| **Starter** | $7/month | 0.5 CPU, 512 MB RAM | Frontend-only hosting |
| **Standard** | $25/month | 1 CPU, 2 GB RAM | MVP with EasyOCR (minimum viable) |
| **Pro** | $85/month | 2 CPU, 4 GB RAM | Growth |

**Render Advantages**: Predictable pricing, free hobby tier (for non-OCR services), built-in SSL, automatic deploys from Git.

**Render Limitations**: Hobby tier services spin down after 15 minutes of inactivity (cold start delays). Hobby and Starter tiers lack sufficient RAM for EasyOCR. No built-in Redis on free tier.

---

#### Railway

| Plan | Cost | Included Credits | Best For |
|---|---|---|---|
| **Hobby** | $5/month | $5 in usage credits | Lightweight services |
| **Pro** | $20/month | $20 in usage credits | Growth |

**Usage rates**: ~$20/vCPU-month, ~$10/GB-RAM-month.

A typical FastAPI app consuming 0.5 vCPU + 2 GB RAM (minimum for EasyOCR) costs approximately **$20/month** in usage.

**Railway Advantages**: Usage-based billing (pay only for what you use), fast deployment, built-in Redis and PostgreSQL add-ons.

**Railway Limitations**: No permanent free tier. Costs can be unpredictable under variable load.

---

#### AWS (EC2 + ECS)

| Instance | Cost | Compute | Best For |
|---|---|---|---|
| **t3.micro** | ~$8/month | 2 vCPU (burstable), 1 GB RAM | Frontend only (no OCR) |
| **t3.small** | ~$15/month | 2 vCPU (burstable), 2 GB RAM | MVP with EasyOCR |
| **t3.medium** | ~$30/month | 2 vCPU (burstable), 4 GB RAM | Growth |
| **m6i.large** | ~$70/month | 2 vCPU, 8 GB RAM | Production |

**AWS Advantages**: Maximum flexibility, 12-month free tier (t3.micro), mature ecosystem, compliance certifications.

**AWS Limitations**: Significant operational overhead (networking, security groups, load balancers). Not recommended for small teams without DevOps experience.

---

### Hosting Cost Projections

Estimated monthly hosting costs per deployment stage:

| Component | MVP | Early Customers | Growth | Production |
|---|---|---|---|---|
| **FastAPI Backend** | $25 (Render Standard) | $25–50 | $50–85 | $70–150 |
| **Streamlit Frontend** | $0 (Render Hobby) | $7 (Render Starter) | $7–25 | $25–85 |
| **Celery Worker** | — | — | $25–50 | $70–150 |
| **Redis** | — | — | $15–30 | $30–50 |
| **Total Hosting** | **$25** | **$32–57** | **$97–190** | **$195–435** |

> **Note on MVP hosting**: The FastAPI backend must run on at least a 2 GB RAM instance if using EasyOCR. The Render Hobby ($0, 512 MB) and Starter ($7, 512 MB) tiers do not have sufficient RAM. The minimum viable backend host for the MVP is Render Standard ($25/month) or Railway with ≥2 GB RAM (~$20/month). The Streamlit frontend has no such requirement and can run on any tier, including free.

### Hosting Recommendation

| Stage | Recommendation | Rationale |
|---|---|---|
| **MVP** | **Render Standard** ($25) for backend + **Render Hobby** (free) for frontend | Minimum RAM for EasyOCR; frontend can use free tier |
| **Early Customers** | **Railway Pro** | Usage-based billing keeps costs proportional to actual load; built-in Redis |
| **Growth** | **Railway Pro** or **Render Standard + Pro** | Predictable performance at reasonable cost |
| **Production** | **AWS ECS** or **Render Pro** | AWS for compliance and scale; Render for simplicity |

---

## Monitoring and Observability Costs

The design document specifies structured JSON logging, Prometheus/Datadog metrics, OpenTelemetry distributed tracing, and multiple alert rules. These are not optional at production scale — they are necessary to maintain SLA compliance and debug pipeline failures.

### Monitoring Tool Options

| Tool | Pricing | Coverage |
|---|---|---|
| **Sentry (Free)** | $0/month | Error tracking only, 5K events/month |
| **Sentry (Team)** | $26/month | Error tracking, 100K events/month |
| **Datadog (Infrastructure)** | $15/host/month | Metrics, dashboards |
| **Datadog (APM + Infra)** | $46/host/month | Metrics + distributed tracing |
| **Datadog (Log Management)** | $0.10/GB ingested | Log indexing and search |
| **Grafana Cloud (Free)** | $0/month | 10K metrics, 50 GB logs/month |
| **UptimeRobot (Free)** | $0/month | 50 monitors, 5-min intervals |
| **Better Uptime (Starter)** | $24/month | Status pages, incident management |

### Monitoring Cost by Stage

| Stage | Recommended Stack | Monthly Cost |
|---|---|---|
| **MVP** | Sentry Free + UptimeRobot Free | **$0** |
| **Early Customers** | Sentry Team + UptimeRobot Free | **$26** |
| **Growth** | Sentry Team + Grafana Cloud Free + UptimeRobot | **$26–50** |
| **Production** | Datadog (3 hosts × APM) + Sentry Team + Log Management | **$150–250** |

> **Production monitoring breakdown**: 3 hosts (API + Worker + Frontend) × $46/host = $138 for Datadog APM. Add $26 for Sentry error tracking. Add ~$10–50/month for log management depending on volume. Add $24 for Better Uptime with status pages. Total: **$150–250/month**.

---

## Developer Tooling and SaaS Costs

These are operational costs required to develop, deploy, and maintain the platform. They are often omitted from infrastructure cost analyses but are real recurring expenses.

| Service | Monthly Cost | Purpose | Required From |
|---|---|---|---|
| **GitHub (Free / Team)** | $0–$4/user | Source control, CI/CD Actions | Day 1 |
| **CI/CD (GitHub Actions)** | $0–40 | Build/test/deploy minutes beyond free tier | Day 1 |
| **Secrets Manager** | $0–5 | AWS Secrets Manager or Vault | Day 1 |
| **Domain Name** | ~$1 | Custom API and app domain | Early Customers |
| **SSL Certificates** | $0 | Free via Let's Encrypt / ACM | Early Customers |
| **OpenAI Prepaid Credit** | $5 minimum | Maintain API access tier | Day 1 |
| **Error Tracking (Sentry)** | $0–26 | Included in monitoring above | Day 1 |

### Developer Tooling Cost by Stage

| Stage | Monthly Cost |
|---|---|
| **MVP** | **$5–15** |
| **Early Customers** | **$10–30** |
| **Growth** | **$20–50** |
| **Production** | **$30–80** |

---

## Total System Cost

### Monthly Cost Summary — Three Scenarios

---

#### MVP Stage (100–500 invoices/month)

| Component | Optimistic | Expected | Conservative |
|---|---|---|---|
| Database (Supabase) | $0 (Free) | $0 (Free) | $25 (Pro) |
| Object Storage | $0 | $0 | $0 |
| OCR | $0 (EasyOCR) | $0 (EasyOCR) | $0 (EasyOCR) |
| LLM (OpenAI) | $0.06–0.29 | $0.12–0.59 | $0.21–1.03 |
| Hosting | $25 | $25 | $25 |
| Monitoring | $0 | $0 | $0 |
| Backups / DR | $0 | $0 | $0 |
| Developer Tooling | $5 | $10 | $15 |
| **Total** | **$30–$35** | **$35–$40** | **$65–$70** |

> The MVP requires a minimum ~$30/month in external costs — not $0. The primary fixed costs are hosting (EasyOCR needs ≥2 GB RAM) and developer tooling (OpenAI prepaid credit). LLM costs are negligible.

---

#### Early Customers Stage (1,000–3,000 invoices/month)

| Component | Optimistic | Expected | Conservative |
|---|---|---|---|
| Database (Supabase Pro) | $25 | $25 | $25 |
| Object Storage (included) | $0 | $0 | $0 |
| OCR (EasyOCR + Vision fallback) | $0 | $0–0.53 | $0–1.88 |
| LLM (OpenAI) | $0.58–1.73 | $1.17–3.52 | $2.06–6.19 |
| Hosting | $32 | $45 | $57 |
| Monitoring (Sentry Team) | $0 | $26 | $26 |
| Backups / DR | $0 | $0 | $0 |
| Developer Tooling | $10 | $20 | $30 |
| **Total** | **$68–$74** | **$117–$120** | **$140–$146** |

> At $70–145/month, the platform can serve 1–5 small businesses. If charging $50/month per customer, two customers cover all infrastructure costs.

---

#### Growth Stage (5,000–15,000 invoices/month)

| Component | Optimistic | Expected | Conservative |
|---|---|---|---|
| Database (Supabase Pro) | $25 | $25 | $25 |
| Object Storage (included) | $0 | $0 | $0 |
| OCR (Google Vision API) | $5.25 | $8.63 | $15.38 |
| LLM (OpenAI) | $2.89–8.67 | $5.87–17.60 | $10.32–30.95 |
| Hosting | $97 | $140 | $190 |
| Monitoring | $26 | $40 | $50 |
| Backups / DR | $0 | $0 | $0 |
| Staging Database | $25 | $25 | $25 |
| Developer Tooling | $20 | $35 | $50 |
| Embeddings (Phase 3) | $0.01 | $0.04 | $0.07 |
| Redis | $15 | $20 | $30 |
| **Total** | **$216–$222** | **$300–$311** | **$396–$416** |

> Hosting and infrastructure dominate at this stage — not AI or OCR. Optimize by right-sizing compute instances before cutting AI features.

---

#### Production Scale (30,000–100,000 invoices/month)

| Component | Optimistic | Expected | Conservative |
|---|---|---|---|
| Database (Supabase Pro + overages) | $26 | $27 | $30 |
| Object Storage (S3) | $1 | $3 | $8 |
| OCR (Google Vision API) | $13.50–43.50 | $22.50–67.50 | $33.75–112.50 |
| LLM (OpenAI) | $17.34–57.80 | $35.19–117.30 | $61.89–206.30 |
| Hosting (API + Workers) | $195 | $315 | $435 |
| Monitoring (Datadog + Sentry) | $150 | $200 | $250 |
| Backups / DR | $75 | $120 | $165 |
| Staging Database | $25 | $50 | $80 |
| Developer Tooling | $30 | $55 | $80 |
| Redis | $30 | $40 | $50 |
| Embeddings (Phase 3) | $0.07 | $0.24 | $0.50 |
| **Total (30K invoices)** | **$563** | **$868** | **$1,181** |
| **Total (100K invoices)** | **$643** | **$995** | **$1,417** |

---

### Cost Per Invoice at Each Stage

| Stage | Monthly Volume | Optimistic | Expected | Conservative |
|---|---|---|---|---|
| **MVP** | 500 | $35 → **$0.070** | $40 → **$0.080** | $70 → **$0.140** |
| **Early Customers** | 3,000 | $74 → **$0.025** | $120 → **$0.040** | $146 → **$0.049** |
| **Growth** | 15,000 | $222 → **$0.015** | $311 → **$0.021** | $416 → **$0.028** |
| **Production** | 100,000 | $643 → **$0.006** | $995 → **$0.010** | $1,417 → **$0.014** |

> **Unit economics improve dramatically at scale.** Even under conservative assumptions, the cost per invoice drops from $0.049 at early customer stage to $0.014 at production scale. This is because the largest cost components (hosting, monitoring, database) are fixed costs amortized across more invoices, while variable costs (LLM, OCR) remain extremely low per unit.

---

### Annual Cost Projections

| Stage | Optimistic (Annual) | Expected (Annual) | Conservative (Annual) |
|---|---|---|---|
| **MVP** | **$360–$420** | **$420–$480** | **$780–$840** |
| **Early Customers** | **$816–$888** | **$1,404–$1,440** | **$1,680–$1,752** |
| **Growth** | **$2,592–$2,664** | **$3,600–$3,732** | **$4,752–$4,992** |
| **Production** | **$6,756–$7,716** | **$10,416–$11,940** | **$14,172–$17,004** |

---

## Cost Optimization Strategy

The platform is designed with multiple cost optimization mechanisms built into its architecture. These are not future aspirations — they are structural decisions that reduce costs from Day 1.

### 1. Digital PDF Extraction Bypass

**Impact: Eliminates OCR costs for 50–80% of invoices.**

Digital-native PDFs contain embedded text layers that can be read programmatically with `pdfplumber` in milliseconds — with 100% character accuracy. No OCR engine is invoked.

```
Without bypass:  100% of invoices → OCR → $1.50/1,000 pages
With bypass:     Only 20–50% of invoices → OCR (depending on customer segment)

Savings at 15,000 invoices/month (Expected — 30% scanned):
  Without: 15,000 × 1.5 pages × $0.0015 = $33.75
  With:     4,500 × 1.5 pages × $0.0015 =  $8.63

Monthly savings: $25.12 (74% reduction)
```

### 2. Prompt Optimization

**Impact: Reduces LLM token consumption by 30–50%.**

The platform implements several prompt compression techniques:

- **Whitespace collapsing**: `" ".join(text.split())` eliminates redundant spaces, newlines, and formatting characters. A typical invoice's raw text shrinks by 30–40%.
- **System prompt minimization**: The system prompt is 120 tokens — deliberately concise. Every token in the system prompt is repeated on every API call.
- **Schema context hints**: Instead of embedding the full Pydantic schema definition in the prompt, the platform provides compact field-name hints.
- **Selective page processing**: If line items end on page 2 of a 10-page document, pages 3–10 are not sent to the LLM.

### 3. Vendor Intelligence Cache (Phase 3)

**Impact: Reduces per-invoice token consumption by 20%+ for known vendors.**

When the system recognizes a returning vendor (via `vendor_tax_id`), it retrieves a cached prompt strategy optimized for that vendor's layout. This cached strategy:

- Provides specific schema hints (e.g., "This vendor always includes a tax rate column").
- Skips unnecessary extraction instructions for fields the vendor consistently includes.
- Reduces prompt size by eliminating generic fallback instructions.

```
Before cache (generic prompt):  ~1,100 input tokens (expected)
After cache (vendor-specific):  ~800 input tokens

Token reduction: 27%
Cost reduction at 15,000 invoices/month: ~$4.75 saved (expected scenario)
```

> **Note**: Vendor caching also reduces the GPT-4o fallback rate. Known vendors with cached strategies produce more reliable extractions, potentially dropping the fallback rate from 10% to 3–5%, which compounds the savings.

### 4. Model Tiering

**Impact: 85–95% of invoices use the cheapest model.**

The platform routes the majority of invoices through `gpt-4o-mini` ($0.15/1M input tokens) and only falls back to `gpt-4o` ($2.50/1M input tokens) for documents that fail schema validation on the first attempt.

```
If 100% used gpt-4o (expected tokens):
  $0.007618/invoice × 15,000 = $114.27/month

With 90/10 tiering (expected):
  $0.001173/invoice × 15,000 =  $17.60/month

Monthly savings: $96.67 (85% reduction)
```

### 5. Storage Lifecycle Policies

**Impact: Reduces long-term storage costs by 60–80%.**

Invoice files must be retained for 7 years, but they are rarely accessed after 90 days. The platform uses tiered storage:

| Age | Storage Tier | Cost/GB-month |
|---|---|---|
| 0–90 days | S3 Standard | $0.023 |
| 90 days – 1 year | S3 Infrequent Access | $0.0125 |
| 1–7 years | S3 Glacier Instant Retrieval | $0.004 |

```
Without lifecycle:  345 GB × $0.023 × 84 months (7 years) = $666.54
With lifecycle:     345 GB × ~$0.006 average   × 84 months = $173.88

Total savings over 7-year retention: ~$493 per 1M invoices
```

### 6. Batch Processing (Phase 2)

**Impact: Reduces hosting costs through efficient resource utilization.**

Instead of processing invoices synchronously through the API, Phase 2 introduces Celery workers that process documents from a queue. This allows:

- Workers to be scaled down during off-hours (nights, weekends).
- Batch processing during low-rate windows.
- Spot/preemptible instances for worker nodes (50–70% compute cost reduction on AWS).

---

## Final Recommendation

### What should we use today? (MVP → Early Customers)

| Component | Recommended Provider | Monthly Cost (Expected) |
|---|---|---|
| Database | **Supabase Pro** | $25 |
| Object Storage | **Supabase Storage** (included) | $0 |
| OCR | **EasyOCR** (local) with **Google Vision** fallback | $0–1 |
| LLM | **OpenAI GPT-4o-mini** via Instructor | $1–4 |
| Hosting | **Render Standard** (backend) + **Render Hobby** (frontend) | $25–45 |
| Monitoring | **Sentry Team** | $26 |
| Backups | Included in Supabase Pro | $0 |
| Developer Tooling | GitHub + OpenAI credit + domain | $10–20 |
| **Total** | | **$87–$121/month** |

**Why this stack?**

- **Supabase** consolidates database + storage + auth + backups into a single $25/month service, eliminating multi-vendor management overhead.
- **EasyOCR** costs nothing in API fees but requires a 2 GB RAM host (Render Standard $25/month minimum).
- **GPT-4o-mini** delivers excellent extraction quality at a cost of ~$0.001 per invoice.
- **Sentry** provides error tracking for pipeline failures — essential even at MVP stage.

**This stack can serve the first 3,000 invoices/month for ~$120/month (expected scenario).**

---

### What should we use when we scale? (Growth → Production)

| Component | Scale Provider | Monthly Cost at 50K invoices (Expected) |
|---|---|---|
| Database | **Supabase Pro + overages** or **AWS RDS PostgreSQL** | $27–80 |
| Object Storage | **AWS S3** with lifecycle policies | $2–5 |
| OCR | **Google Vision API** | $30–45 |
| LLM | **OpenAI GPT-4o-mini** + vendor prompt cache | $30–60 |
| Hosting | **AWS ECS** or **Render Pro** | $200–315 |
| Redis | **AWS ElastiCache** or **Railway Redis** | $30–40 |
| Monitoring | **Datadog + Sentry** | $175–225 |
| Backups / DR | AWS RDS automated + cross-region | $100–140 |
| Staging Database | Dedicated staging instance | $40–60 |
| Developer Tooling | GitHub Team + CI/CD + secrets | $40–60 |
| **Total** | | **$674–$1,030/month** |

**Why this stack?**

- **AWS** provides the compliance certifications (SOC 2, HIPAA), SLAs, and scaling headroom that enterprise customers require.
- **Google Vision API** replaces EasyOCR as OCR accuracy becomes critical for reducing manual review rates.
- **Vendor prompt caching** (Phase 3) reduces LLM costs by 20%+ and lowers the GPT-4o fallback rate.
- **S3 lifecycle policies** keep 7-year retention affordable.
- **Datadog APM** provides the distributed tracing and alerting required for production SLA compliance.

**This stack processes 50,000 invoices/month for ~$850/month (expected) — a cost per invoice of $0.017.**

---

### What should we avoid?

| Avoid | Reason |
|---|---|
| **Google Document AI** as the primary extraction engine | At $10–30/1,000 pages, it costs 7–20× more than Vision API + LLM combined, and it conflicts with the platform's AI Structuring Layer architecture |
| **GPT-4o as the primary model** | At $2.50/1M input tokens, it is 17× more expensive than GPT-4o-mini with marginal accuracy improvement on standard invoices |
| **AWS for MVP** | Operational overhead (VPC, security groups, IAM) is disproportionate for a small team; use Railway or Render until >10K invoices/month |
| **Self-hosted PostgreSQL in production** | Backup management, failover, and patching are not worth the cost savings for a team of 1–3 engineers |
| **GPU instances for OCR at MVP** | EasyOCR runs on CPU; GPU instances ($50–200/month) are not justified until OCR volume exceeds 10,000 pages/month |
| **Over-provisioning hosting** | Right-size compute instances quarterly; most startups over-spend on hosting by 40–60% |
| **Skipping token usage logging** | Without the `llm_usage_log` table, you cannot identify cost spikes, inefficient prompts, or anomalous invoices consuming excessive tokens |
| **Skipping monitoring until production** | Pipeline failures in development go unnoticed without error tracking; Sentry free tier costs $0 |
| **Running EasyOCR on <2 GB RAM instances** | EasyOCR's language models require ~1.5 GB RAM; under-provisioned instances will OOM-crash |

---

### Costs Not Included in This Analysis

The following costs are real but fall outside the scope of infrastructure cost analysis:

| Cost Category | Estimated Range | Notes |
|---|---|---|
| **Developer salaries** | Market rate | The largest real cost; not infrastructure |
| **Legal / compliance** | $1,000–5,000/year | Business registration, terms of service, privacy policy |
| **Accounting software** | $20–50/month | QuickBooks, Xero for the business itself |
| **Insurance** | Varies | Cyber liability, E&O insurance |
| **Customer support tooling** | $0–50/month | Intercom, Zendesk (Growth+) |
| **Marketing / acquisition** | Varies | Not an infrastructure cost |

---

### Cost Scaling Curve (Expected Scenario)

```
Monthly Cost ($)
     │
$1400│                                                     ╭─ Conservative
     │                                                ╭────╯
$1200│                                           ╭────╯
     │                                      ╭────╯
$1000│                                 ╭────╯─────────── Expected
     │                            ╭────╯
 $800│                       ╭────╯
     │                  ╭────╯
 $600│             ╭────╯──────────────────── Optimistic
     │        ╭────╯
 $400│   ╭────╯ Growth ($216–416)
     │╭──╯
 $200│╯
     │ Early ($68–146)
 $100│──── MVP ($30–70)
   $0├────────┬──────────┬──────────┬──────────┬─────────
     0      1,000      5,000     15,000     50,000   100,000
                     Invoices / Month
```

> **Key takeaway**: The cost curve is sub-linear. Doubling invoice volume does **not** double costs. Fixed infrastructure costs (hosting, monitoring, database) are amortized across more invoices, while per-invoice variable costs (LLM + OCR) remain under $0.003 per unit. Under Expected assumptions, the platform achieves a **cost per invoice of $0.010 at 100K invoices/month** — roughly 200–500× cheaper than manual data entry ($2–5 per invoice).
