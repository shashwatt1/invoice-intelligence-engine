# Staging deployment plan — PROPOSAL, nothing deployed

Status: **awaiting review**. Written 17 Sep 2026 against commit `36677c7`;
re-checked 18 Sep 2026 against `82e90f5` (migrations now `0017`; store-pending
invoices, manual corrections and `POST /stores` exist — none of it changes the
deployment shape). Nothing in this document has been provisioned, built or
deployed.

## 0. What the application actually is (inspected, not assumed)

| Concern | Today |
|---|---|
| Backend | FastAPI + uvicorn, Python 3.11, `Dockerfile` present (multi-stage, non-root, `HEALTHCHECK /api/v1/health`, **1 worker**) |
| Frontend | Vite/React SPA; `npm run build` → static `web/dist`; talks to `/api/v1` **same-origin** (dev proxy) — no CORS needed when served from one host |
| Database | PostgreSQL 16 via asyncpg; Alembic migrations `0001`–`0015`; current local DB **41 MB**, ~44k reference rows, 94 mappings, 121 proposals, 6 invoices |
| Files | `STORAGE_BACKEND=local` only — S3/Supabase are stubs (`storage_service.py`), not implemented. Uploads = 18 MB on disk today |
| Background work | FastAPI `BackgroundTasks` **in the API process** (OCR → LLM → validate → persist). A restart mid-run leaves that document in `OCR_IN_PROGRESS`/`AI_PROCESSING` |
| External services | Google Vision (API key), OpenAI `gpt-4o` (API key) |
| Auth | **None.** `X-API-Key` is mentioned in the OpenAPI description but no middleware exists. `/docs` is open unless `APP_ENV=production` |
| Logging | structlog JSON to stdout + rotating file (`./logs/app.log`) |
| Monitoring | None (no Sentry/OTel) |
| Secrets | `.env` (pydantic-settings), `.env.example` documents every key |
| Seed/import | `scripts/import_store_reference.py`, `import_item_sales_pricing.py`, `import_beer_inventory.py` (idempotent upserts) from `data/reference/` (gitignored — business data) |
| Regression fixtures | Balkan, A.L. George 1000540, T.J. Sheehan 101497, Testani live **in the DB** (invoices + uploads), audited by `scripts/invoice_audit.py` |

## 1. Recommended architecture — one VM, Docker Compose, Caddy

The simplest thing that gives the team a persistent DB, on-disk uploads
(no new storage code), HTTPS and a login, with one moving part to update.

```
team browser ──HTTPS──▶ Caddy (auto TLS, login gate, serves web/dist, proxies /api → api:8000)
                            │
                            ├── api   (Dockerfile as-is; 1 worker; uploads + logs on a volume)
                            └── db    (postgres:16, data volume, NOT exposed publicly)
                                        └── nightly pg_dump → encrypted object storage
```

Answers to the twenty questions:

1. **Platform:** one small VM (Hetzner CX22 ≈ €4.5/mo or DigitalOcean 2 GB ≈ $12/mo), Docker Compose. Chosen because the app already ships a Dockerfile + compose, stores files on local disk, and runs background work in-process — all of which a VM supports unchanged. Managed PaaS (Render/Railway/Fly) is Option B below; it forces an S3 storage backend first.
2. **Frontend:** `web/dist` built in a tiny Node build stage and served by Caddy as static files (same origin as the API).
3. **FastAPI:** the existing `Dockerfile`, `--workers 1` (keep: in-process background tasks + one DB pool).
4. **PostgreSQL:** `postgres:16` container with a named volume on the VM. Persistent across deploys; port **not** published. (Managed Postgres — DO $15/mo — is a fine upgrade later; nothing in the app cares.)
5. **Uploads:** Docker volume `uploads` mounted at `/app/uploads` (`STORAGE_BACKEND=local`, unchanged). Included in the nightly backup.
6. **EDI files:** generated on request (`GET /invoices/{id}/export?format=pdi`) and streamed to the browser; nothing is written server-side except the audit script's `--edi-dir` (dev tool). No change.
7. **Secrets:** a `/srv/invoice/.env` on the VM, `chmod 600`, owned by the deploy user, never in git; `.env.example` is the checklist. Keys: `DATABASE_URL`, `DATABASE_URL_SYNC`, `GOOGLE_VISION_API_KEY`, `OPENAI_API_KEY`, `SECRET_KEY`, `APP_ENV=staging`, `ALLOWED_ORIGINS=https://staging.<domain>`. Set spend caps on the OpenAI project and a Vision quota.
8. **Authentication:** gate at the edge, no app changes:
   - **Preferred:** Cloudflare Access (Zero Trust, free ≤ 50 users) in front of the VM: team logs in with Google/email OTP, per-person identity, revocable, and the request carries `Cf-Access-Authenticated-User-Email` which the app can later read as a default `proposed_by`/`reviewed_by`. Requires the domain's DNS on Cloudflare.
   - **Fallback:** Caddy `basic_auth` with one bcrypt entry per team member. Works anywhere; identity is the browser's basic-auth user.
   - Either way the app's reviewer string (`data-team:shashwat`) stays the governance identity; roles PROCESSOR/REVIEWER/ADMIN remain future work.
9. **CORS:** none needed — Caddy serves the SPA and proxies `/api` on the same origin. `ALLOWED_ORIGINS` is set to the staging origin anyway.
10. **HTTPS:** Caddy obtains and renews Let's Encrypt certificates automatically for `staging.<domain>`.
11. **Migrations:** `docker compose run --rm api alembic upgrade head` as an explicit deploy step (not on container start — a failed migration must stop the deploy, not crash-loop the API).
12. **Reference data:** `pg_dump --data-only` of the reference/master tables from the current DB (`stores`, `store_identifiers`, `product_identity`, `product_identifier`, `product_pricing`, `store_product_references`, `product_case_mappings`, `product_data_proposals`, `vendors`) restored after `alembic upgrade head`. Exact, 41 MB, five minutes. Re-running the importers from `data/reference/*.xlsx` is the alternative and is idempotent, but the dump also carries the 94 approved mappings and their proposal history, which the importers do not.
13. **Staging seed data:** the same dump can include `documents`, `document_pages`, `invoices`, `invoice_items`, `processing_logs` plus the `uploads/` directory (18 MB) so the four regression invoices are present and `invoice_audit.py` can prove the goldens on staging. **Decision needed:** these are real supplier invoices (business data) — include them on staging, or seed reference data only?
14. **Logs:** `docker compose logs -f api` (JSON, structlog) and the rotating `/app/logs/app.log` on the volume; Caddy access log. Request IDs already thread through every line.
15. **Errors:** `HEALTHCHECK` + an external uptime ping (UptimeRobot, free) on `/api/v1/health`; documents that end `FAILED` are already visible in the History page with the stage and message. Optional (one dependency, ~20 lines): `sentry-sdk[fastapi]` on the free tier — proposed, not done.
16. **Updating:** `deploy.sh` on the VM: `git pull --ff-only` → `docker compose build` → `pg_dump` snapshot → `alembic upgrade head` → `docker compose up -d` → smoke (`/api/v1/health`, Balkan audit sha `88516117…`). Manual, from a tagged commit.
17. **Rollback:** `git checkout <previous tag>` → rebuild → `up -d`; for a bad migration, `alembic downgrade <rev>` (every migration has a `downgrade`) or restore the pre-deploy `pg_dump`. Keep the last 7 dumps.
18. **Cost:** VM €4.5–$12/mo · Cloudflare $0 · domain ≈ $12/yr · backups to Backblaze B2/S3 < $1/mo · Google Vision ≈ $1.50 per 1,000 photos · OpenAI gpt-4o ≈ $0.03–0.08 per invoice (1–3 photos). **≈ $10–20/month fixed + usage** (100 invoices/month ≈ $5–10).
19. **Security risks:** see §3.
20. **Simplest architecture that meets the need:** the one above. One host, four containers, one script.

### Option B — managed PaaS (Render / Railway / Fly)

Web service (API) + static site (SPA) + managed Postgres ≈ $25–40/mo.
Less to operate, but their disks are ephemeral, so the **S3 storage
backend must be implemented first** (`storage_service.py` has the
abstraction; ~half a day + a bucket), background tasks still live in
the web process, and each platform's auth story is weaker than
Cloudflare Access. Recommended only if nobody wants to own a VM.

## 2. Code changes the plan needs (proposed, not made)

| # | Change | Why |
|---|---|---|
| 1 | `compose.staging.yml` (caddy, api, web-build, db; volumes `pgdata`, `uploads`, `logs`, `caddy`) and a `Caddyfile` | The deployable unit |
| 2 | `web/Dockerfile` (node build stage → `dist` into a volume Caddy serves) | Frontend build on the VM, no CDN needed |
| 3 | `deploy.sh` + `backup.sh` (pg_dump → object storage, 7-day retention) | §16–17 |
| 4 | **Staging label:** `GET /api/v1/health` already exists — add `app_env` to its body; the SPA reads it once and renders a fixed banner **"STAGING — TEAM TESTING"** when it is not `production`. No build-time env, one banner component | Required by the brief |
| 5 | `/docs`, `/redoc`, `/openapi.json` behind the same edge login (Caddy) — or off when `APP_ENV != development` | Open API docs on a public host |
| 6 | Optional: default `proposed_by`/`reviewed_by` from the edge identity header when present | Ties review records to a person without building login |
| 7 | Optional: `sentry-sdk` | §15 |

No change to the pipeline, formatter, governance, store architecture or
data model is required for staging.

## 3. Security considerations

- **No application auth exists.** The edge login is the only gate; the
  compose file must not publish `api:8000` or `db:5432` on the host —
  Caddy is the sole listener (80/443). Verify with `ss -ltnp` after deploy.
- **API keys** (Vision, OpenAI) live only in the VM's `.env`; put spend
  caps on both providers; rotate if a team member leaves.
- **Business data:** the DB holds supplier invoices, store references and
  pricing. Backups must go to a private, encrypted bucket; the VM disk is
  the only other copy. Restrict SSH to key auth, no password login.
- **Uploads:** MIME is checked from the client's `content-type` header,
  not sniffed; 25 MB cap. Acceptable behind a login; note for production.
- **In-process background tasks:** a container restart during a run
  strands that document (`OCR_IN_PROGRESS`). Acceptable on staging;
  reprocess after delete. A worker queue is a production concern.
- **Single worker:** one slow LLM call delays other requests only
  slightly (async), but 10 simultaneous uploads will queue. Fine for a
  team.
- **`SECRET_KEY`** is unused today (no sessions); set a real one anyway.
- **Rate limiting:** none; the edge login is the mitigation.
- **Deleting an invoice is a hard delete** (by design, dev workflow);
  proposals and mappings survive. On staging that is what the team
  should expect and it is now labelled in the UI.

## 4. Deployment sequence (when approved)

1. Domain + DNS (`staging.<domain>` → VM). If Cloudflare: enable Access
   for that hostname, add the team's emails.
2. VM: Ubuntu 24.04, Docker + compose plugin, deploy user, SSH keys
   only, `ufw` allowing 22/80/443.
3. `git clone` at the approved tag; write `/srv/invoice/.env` from
   `.env.example` with staging values; `chmod 600`.
4. `docker compose -f compose.staging.yml build`.
5. `docker compose run --rm api alembic upgrade head` → expect `0015 (head)`.
6. Restore the reference dump (and, if approved, the invoice/upload
   seed); `rsync uploads/`.
7. `docker compose up -d`; check `/api/v1/health` shows `app_env: staging`.
8. Smoke: log in, History shows the seeded invoices, Data Review shows
   the 121 proposals (27 marked "invoice deleted"), download Balkan EDI and
   compare sha `88516117…`, process one sample photo end to end.
9. Hand the URL and logins to the team; note the banner.
10. Nightly backup cron verified by a test restore into a scratch container.

## 4a. The eighteen questions, answered in one place

| # | Question | Answer (see the section referenced) |
|---|---|---|
| 1 | Hosting option | One small VM (Hetzner CX22 / DO 2 GB) — §1.1 |
| 2 | VM vs managed platform | VM: the app stores files on local disk and runs background work in-process; a PaaS needs the S3 backend first — §1.1, Option B |
| 3 | Docker/Compose architecture | Caddy + api (existing Dockerfile) + db, one `compose.staging.yml` — §1, §2 |
| 4 | PostgreSQL strategy | `postgres:16` container on a named volume, port not published; managed Postgres is a drop-in later — §1.4 |
| 5 | Persistent upload storage | Docker volume at `/app/uploads`, `STORAGE_BACKEND=local` unchanged, in the nightly backup — §1.5 |
| 6 | HTTPS/domain | `staging.<domain>` on Caddy with automatic Let's Encrypt — §1.10 |
| 7 | Authentication/access control | Cloudflare Access (per-person, free ≤ 50) or Caddy basic-auth; app code unchanged — §1.8 |
| 8 | Backup strategy | Nightly `pg_dump` + uploads to an encrypted private bucket, 7-day retention, test restore — §1.17, §4.10 |
| 9 | Secrets management | `/srv/invoice/.env`, `chmod 600`, deploy user only, never in git; `.env.example` is the checklist — §1.7 |
| 10 | Google Vision credentials | `GOOGLE_VISION_API_KEY` in the VM `.env` only; quota set in the Google console; never in the SPA — §1.7, §3 |
| 11 | OpenAI credentials | `OPENAI_API_KEY` in the VM `.env` only; project spend cap; never in the SPA — §1.7, §3 |
| 12 | Logging | structlog JSON on stdout (`docker compose logs`) + rotating file on a volume; request ids on every line — §1.14 |
| 13 | Monitoring | container HEALTHCHECK + external uptime ping on `/api/v1/health`; FAILED documents visible in History; optional Sentry — §1.15 |
| 14 | Database migrations | `docker compose run --rm api alembic upgrade head` as an explicit deploy step; the migration/model drift test (`test_migrations_match_models.py`) runs in CI/before deploy — §1.11, §22 of the brief |
| 15 | Deployment/rollback | `deploy.sh` (pull → build → pg_dump → migrate → up → smoke); rollback = previous tag + `alembic downgrade` or restore the pre-deploy dump — §1.16–1.17 |
| 16 | Approximate monthly cost | ≈ $10–20 fixed + usage (Vision ≈ $1.50/1,000 photos; gpt-4o ≈ $0.03–0.08/invoice) — §1.18 |
| 17 | How team members access it | Browser → `https://staging.<domain>` → edge login (their email via Cloudflare Access, or their basic-auth user) → the SPA; the API is reachable only through that same origin; no VPN, no ports — §1.8 |
| 18 | Test data isolation from production | There is no production yet. Staging is its own VM, database, bucket and provider keys (separate OpenAI project / Vision key so usage is attributable); the UI banner says STAGING — TEAM TESTING; when production exists it gets its own VM + DB and staging never shares credentials or a database with it — §2.4, §3 |

## 5. Decisions required before anything is provisioned

1. Hosting: **VM + Compose (recommended)** or managed PaaS (needs S3 first).
2. Auth: **Cloudflare Access (recommended, needs DNS on Cloudflare)** or Caddy basic auth.
3. Seed: reference/master data only, or also the four regression invoices and their photos (real business documents) on a shared staging host.
4. Domain name and who owns DNS.
5. Who holds the provider accounts and spend caps (Vision, OpenAI).
6. Backup destination (B2/S3 bucket) and retention.
7. Whether to add Sentry now.
