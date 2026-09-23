# Pilot Deployment — Invoice Intelligence Platform

Status: **repository-side preparation complete, not yet deployed.** This
document supersedes the "VM + Docker Compose" option in
`STAGING_DEPLOYMENT_PLAN.md` for this specific deployment — that document's
"Option B" (managed PaaS, S3-compatible storage backend implemented first)
is what this document actually builds. Nothing here has been provisioned on
a real Render or Supabase account; every provider-side step below is a
manual action for a person with those accounts.

This is a **free internal pilot**, not a production launch: no bulk upload
(P4), no autoscaling, no paid tier, single application instance.

---

## 1. Architecture

```
https://<pilot-app>.onrender.com
        │
        ├── React (built once, served as static files by the same process)
        └── FastAPI (same origin, same container — no CORS needed)
                │
                ├── Supabase Postgres   (all relational data — persistent)
                └── Supabase Storage    (original invoice PDFs/photos — persistent)
```

One Render **Web Service**, built from `Dockerfile`, running one process
(`uvicorn`, 1 worker). No Kubernetes, no Redis, no Celery, no queue — the
existing in-process `BackgroundTasks` pipeline is unchanged.

The application container is **disposable**: Render can restart, redeploy,
or replace it at any time, and every free-tier instance spins down after a
period of inactivity and cold-starts on the next request. Nothing that
matters lives in the container:

| State | Lives in |
|---|---|
| Users, documents, invoices, line items, vendors, stores, case mappings, proposals, correction/audit history, ownership | **Supabase Postgres** |
| Original uploaded PDF/JPG/PNG files | **Supabase Storage** (private bucket) |
| Session | An httpOnly cookie in the browser (JWT `sub` = user UUID only; role/`is_active` re-read from Postgres on every request) |

---

## 2. Required accounts/services

1. A **Supabase** account (free tier) — provides both the Postgres database
   and the Storage bucket for this pilot, in one project.
2. A **Render** account (free tier) — hosts the Docker web service.
3. Existing provider keys this application already needs, reused as-is:
   an **OpenAI** API key and a **Google Cloud Vision** API key.

---

## 3. Supabase setup — MANUAL, provider-side

> **STOP HERE if you have not done this yet.** I cannot create a Supabase
> project, database, or bucket for you — this needs your account.

**3.1 — Create the project**
- Go to [supabase.com](https://supabase.com) → New Project.
- Name it something like `invoice-intelligence-pilot`. Pick a strong
  database password (you'll need it in step 3.2) and a region close to
  where Render will run (`oregon` in `render.yaml` → pick a nearby Supabase
  region, e.g. `us-west-1`).
- Wait for provisioning (a couple of minutes).

**3.2 — Get the database connection strings**
- In the Supabase dashboard: **Project Settings → Database → Connection
  string**. Supabase gives you a pooled (pgbouncer) URI and a direct URI.
- You need **two** URLs for this app, built from the same credentials but
  with different drivers:
  - `DATABASE_URL` (async, used by the running app) —
    `postgresql+asyncpg://postgres:<password>@<host>:5432/postgres`
  - `DATABASE_URL_SYNC` (sync, used only by `alembic upgrade head`) —
    `postgresql+psycopg2://postgres:<password>@<host>:5432/postgres`
  - **Where this goes:** paste both into Render's environment variables for
    the web service (Section 6) — never into a committed file.
- Prefer Supabase's **connection pooler** (port `6543`, pgbouncer) for
  `DATABASE_URL` if you hit connection-limit issues on the free tier —
  Supabase's dashboard shows the exact pooled URI to copy.

**3.3 — Create the private Storage bucket**
- In the Supabase dashboard: **Storage → New bucket**.
- Name it (e.g. `invoice-sources`) and leave it **Private** — do NOT toggle
  "Public bucket". This application only ever accesses it server-side with
  the service-role key; nothing serves these files to a browser.
- **Where this goes:** the bucket name → `SUPABASE_BUCKET_NAME` in Render.

**3.4 — Get the Storage API credentials**
- **Project Settings → API**:
  - **Project URL** (e.g. `https://xxxx.supabase.co`) → `SUPABASE_URL`
  - **`service_role` secret key** (NOT the `anon` public key — the
    service-role key bypasses row-level security and must never reach the
    frontend) → `SUPABASE_SERVICE_KEY`
- **Where this goes:** both into Render's environment variables (Section 6).

---

## 4. Render setup — MANUAL, provider-side

> **STOP HERE if you have not done this yet.** I cannot create a Render
> service or paste your secrets into it — this needs your account.

**4.1 — Create the service from the Blueprint**
- Push this repository to GitHub (if not already) — Render deploys from a
  Git repo, and I have not pushed anything per your git-safety instructions.
- In the Render dashboard: **New → Blueprint**, point it at this repo. It
  will read `render.yaml` at the repo root and propose one Web Service
  (`invoice-intelligence-pilot`, Docker runtime, free plan, health check at
  `/api/v1/health`).
- Approve it. Render will NOT deploy successfully yet — every `sync: false`
  environment variable in `render.yaml` is currently empty and must be
  filled in by hand (next step).

**4.2 — Fill in the environment variables**
- In the service's **Environment** tab, set every variable `render.yaml`
  marked `sync: false`:

| Variable | Value |
|---|---|
| `DATABASE_URL` | From Supabase, step 3.2 |
| `DATABASE_URL_SYNC` | From Supabase, step 3.2 |
| `SUPABASE_URL` | From Supabase, step 3.4 |
| `SUPABASE_SERVICE_KEY` | From Supabase, step 3.4 (service_role key) |
| `SUPABASE_BUCKET_NAME` | From Supabase, step 3.3 |
| `OPENAI_API_KEY` | Your existing OpenAI key |
| `GOOGLE_VISION_API_KEY` | Your existing Google Vision API key |
| `SECRET_KEY` | A strong random string — generate with `python -c "import secrets; print(secrets.token_urlsafe(48))"` and paste the output. This signs every session JWT; treat it exactly like a password. |

- Everything else (`APP_ENV=production`, `STORAGE_BACKEND=supabase`,
  `LOG_LEVEL`, etc.) is already set by `render.yaml` and needs no action.

**4.3 — First deploy**
- Trigger a manual deploy (Render's "Deploy latest commit" button —
  `render.yaml` sets `autoDeployTrigger: off` deliberately, so nothing
  auto-deploys while this pilot is new).
- Watch the build logs. Expected: frontend build (`npm ci && npm run
  build`) → Python dependency install → image push → container start.
- **The first deploy will start successfully but the app will not be
  usable yet** — the database has no schema. That's step 5.

---

## 5. Database migration process

The application never runs migrations automatically on startup (by
design — see `app/main.py`'s `lifespan()`, which only ensures local
directories exist; a failed migration must stop a deploy, not
crash-loop the running API). Apply the schema explicitly, once, after
the database exists and before the first real use:

**From your own machine, pointed at the PILOT database (not local dev):**

```bash
DATABASE_URL_SYNC="postgresql+psycopg2://postgres:<password>@<supabase-host>:5432/postgres" \
  alembic upgrade head
```

Expected result: `0021 (head)` (this repo's current migration chain — see
`alembic history`). The chain is linear, single-headed, and was verified
against this exact HEAD before writing this document.

**Alternative, if you'd rather not run Alembic locally against a remote
DB:** Render's paid plans support a `preDeployCommand` in `render.yaml`
that would run this automatically before each deploy; the free plan this
pilot uses may not support it. If your plan does, add:

```yaml
preDeployCommand: alembic upgrade head
```

under the service in `render.yaml` — but verify this actually runs (check
the deploy logs for `0021 (head)`) rather than assuming it's supported.

**Never**: run `alembic downgrade`, drop tables, or reset the pilot
database as part of a routine deploy. If a migration needs to be undone,
that's a deliberate, manual `alembic downgrade <revision>`.

---

## 6. Deployment process (after first-time setup above)

1. `git push` to the branch Render is watching (main, typically).
2. In Render: **Manual Deploy → Deploy latest commit** (auto-deploy is off
   on purpose for this pilot — see 4.3).
3. If the deploy included a new migration, run `alembic upgrade head`
   against the pilot database (Section 5) **before** directing users to the
   new version, or immediately after if the migration is additive and
   backward-compatible with the previous code (check the migration file).
4. Confirm `/api/v1/health` and `/api/v1/ready` both return 200 (Section 9).
5. Run the smoke-test checklist (Section 15).

---

## 7. Health check

- **`GET /api/v1/health`** — liveness only, always 200 if the process is
  running. Render's `healthCheckPath` (set in `render.yaml`) uses this.
- **`GET /api/v1/ready`** — readiness: actually pings the database and
  returns 503 if it's unreachable. Useful for manually confirming the
  Supabase connection is live after a deploy or a credential change.
- Neither endpoint returns secrets, credentials, or raw configuration —
  confirmed by reading `app/api/v1/health.py` and `app/schemas/health.py`.

---

## 8. Local development vs. pilot — how they differ

| | Local development | Pilot |
|---|---|---|
| `APP_ENV` | `development` | `production` |
| Database | `docker compose` Postgres, `localhost:5432` | Supabase Postgres |
| File storage | `STORAGE_BACKEND=local`, `./uploads/` | `STORAGE_BACKEND=supabase` |
| Frontend | `npm run dev` (Vite, port 5173, proxies `/api` to `:8000`) | Built once (`npm run build`), served by FastAPI same-origin |
| Cookie | `secure=False` (no HTTPS locally) — automatic, `secure=settings.is_production` | `secure=True` (Render terminates HTTPS) — automatic, same code path |
| `/docs`, `/openapi.json` | Enabled | **Disabled** (`create_app()` already does this whenever `APP_ENV=production`) |
| Secrets | `.env` (gitignored, never committed) | Render environment variables (never in `render.yaml`) |

There is **no code path** that lets local development accidentally write
to the pilot database or bucket — each environment supplies its own
`DATABASE_URL`/`SUPABASE_*` values, and nothing in the codebase hardcodes
either. Double-check your own shell's exported environment variables don't
leak a pilot `DATABASE_URL` into a local `alembic`/`pytest` run — the
simplest way to avoid this is to only ever export pilot variables inline,
on the single command that needs them (as shown in Section 5), never in
your shell profile.

---

## 9. Storage behavior

- **Local development**: unchanged. `LocalStorageService` writes to
  `./uploads/{org}/{year}/{month}/{uuid}.{ext}` on disk, exactly as before
  this pilot work — nothing about local dev changed.
- **Pilot**: `SupabaseStorageService` (new — `app/services/storage_service.py`)
  writes to the private Supabase bucket at the identical key scheme
  (`{org}/{year}/{month}/{uuid}.{ext}`), over the Supabase Storage REST API
  using `httpx` (already a project dependency — no new SDK). The object key
  returned by `save()` — never a public URL — is exactly what gets
  persisted on the `documents`/`document_pages` row, and is what `read()`
  and `delete()` receive back later. Every existing call site
  (`upload_service.py`, `pipeline_service.py`, `reprocess_service.py`,
  invoice delete) already goes through the `StorageService` abstraction and
  needed zero changes.
- **Existing local files are NOT automatically migrated.** This pilot
  database and bucket start empty except for the schema (Section 5) and
  whatever pilot users upload from here on. Your local `./uploads/` and
  local Postgres data stay exactly where they are, untouched.
- **Future migration/backfill**, if you ever want historical local
  invoices in the pilot: for each `documents`/`document_pages` row, read
  the file from local disk (the existing `file_path` column), call
  `SupabaseStorageService.save()` with the same `document_uuid`, and update
  the row's `file_path` to the returned object key. This is a deliberate,
  reviewed, one-off script if/when needed — not something to run casually,
  since it would mix real historical invoice data into a supposedly-empty
  pilot database.

---

## 10. Authentication setup

Unchanged from the existing architecture, already environment-aware with
no code changes needed for this pilot:

- Username + password → bcrypt verification (`app/core/security.py`).
- httpOnly JWT cookie; `sub` claim is the user's UUID only — no role/
  permission data cached in the token.
- `app.core.dependencies.get_current_user` re-reads `role` and `is_active`
  from Postgres on **every** request — a deactivated pilot user loses
  access immediately, without waiting for their token to expire.
- Cookie flags: `httponly=True`, `samesite="lax"` always;
  `secure=settings.is_production` — automatically `True` once
  `APP_ENV=production` is set (which `render.yaml` does), so the pilot gets
  `Secure` cookies with zero extra configuration.

---

## 11. Creating pilot users manually

**Do NOT create these automatically — you run this yourself, once the
service is deployed and reachable.**

From your own machine, pointed at the pilot database:

```bash
DATABASE_URL_SYNC="postgresql+psycopg2://postgres:<password>@<supabase-host>:5432/postgres" \
DATABASE_URL="postgresql+asyncpg://postgres:<password>@<supabase-host>:5432/postgres" \
  python scripts/create_user.py
```

It will prompt for `Username:`, `Role:`, `Password:`, `Confirm Password:`
(hidden input, never echoed or logged). Run it four times for:

| Username | Role |
|---|---|
| `shashwatt1` | `ADMIN` |
| `barj` | `MANAGER` |
| `prabh` | `MANAGER` |
| `vivek` | `USER` |

If you'd rather not run this from your own machine against a remote
database, Render's dashboard offers a **Shell** tab on the web service that
gives you a terminal inside the running container, already carrying the
correct environment variables — run the same command there instead.

---

## 12. Smoke-test checklist

Run this after the first successful deploy + migration + user creation.
This is a **plan**, not a report of results — I have not run it, since it
needs the real deployed URL and real credentials.

**ADMIN** (`shashwatt1`):
- [ ] Log in at the pilot URL.
- [ ] Dashboard loads.
- [ ] Process one real invoice (upload a PDF/photo).
- [ ] Open the processed invoice — full Admin detail renders (Intelligence,
      Validation, Database persistence, Developer panel all present).
- [ ] Inspect the mapping section for at least one product.
- [ ] If eligible, download the PDI export.

**MANAGER** (`barj` or `prabh`):
- [ ] Log in.
- [ ] Open the invoice ADMIN processed.
- [ ] Business detail renders: header, totals, line items, UPC, price, line
      total, mapping status, store, EDI readiness — and no developer/
      technical sections.
- [ ] Open **Requires Mapping** — the global queue loads.
- [ ] Open the mapping workbench on one unresolved product, propose a
      value.
- [ ] Open **Master Data Review**, approve or reject that proposal.

**USER** (`vivek`):
- [ ] Log in.
- [ ] Upload an invoice.
- [ ] Watch processing status update.
- [ ] Open the invoice they own — line items, mapping state visible.
- [ ] Submit a mapping proposal from the invoice.
- [ ] Confirm no approve/reject action is available anywhere.

**Refresh test** (for the blank-page regression this pilot must not
reintroduce): hard-refresh the browser on `/invoices/<id>` while logged in
as MANAGER and as USER — the page must render, not go blank.

---

## 13. Backup/export considerations

- Supabase's free tier includes automatic daily backups with a short
  retention window (check your plan's current retention in the Supabase
  dashboard — this changes over time, verify rather than assume).
- For anything you cannot afford to lose, use Supabase's manual "Database
  → Backups" export, or `pg_dump` against `DATABASE_URL_SYNC`, on whatever
  cadence matters before the free tier's automatic retention would drop it.
- Storage bucket contents are not covered by Postgres backups — they are a
  separate concern. Supabase Storage itself is durable object storage, but
  a deliberate "delete this project" action would remove both; keep that in
  mind before ever deleting the pilot project.

---

## 14. Known free-tier limitations

- **Render free web services spin down after inactivity** and cold-start
  (10–60s) on the next request. Expect a slow first request after idle
  periods — this is normal, not a bug.
- **Render free tier has no persistent disk** — this is exactly why
  Supabase Storage exists in this design; do not "fix" a future
  slow-upload complaint by writing to local disk again.
- **Supabase free tier** has connection, database size, and bandwidth
  caps that change over time — check current limits in the Supabase
  dashboard rather than relying on numbers written here.
- **Single application instance, one worker** — this pilot is sized for a
  small internal team, not concurrent load. If uploads start queueing
  noticeably, that's a signal for the next phase (P4), not something to
  patch here.
- **In-process background tasks**: a document mid-OCR/LLM processing when
  the free instance spins down or redeploys will be left in
  `OCR_IN_PROGRESS`/`AI_PROCESSING`. Acceptable for a pilot; reprocess
  (delete and re-upload) if it happens. A real job queue is future work
  (P4+), not part of this pilot.

---

## 15. How to redeploy

See Section 6. In short: push → Render manual deploy → migrate if needed →
confirm health → smoke test.

---

## 16. How to inspect logs

Render's dashboard **Logs** tab streams the container's stdout directly —
this application already logs structured JSON (or, if you ever set
`LOG_FORMAT=console`, colorized text) to stdout regardless of environment,
so no extra configuration is needed. `LOG_FILE_ENABLED=false` in
`render.yaml` deliberately skips writing a log file inside the disposable
container — Render's log stream is the pilot's only log destination, and a
local file there would be lost on every restart anyway.

Every log line carries a `request_id` that also comes back to the client
in the `X-Request-ID` response header and in every API error body — when a
pilot user reports "it broke," ask for that reference and grep the Render
log stream for it.

---

## 17. How to recover from a failed deployment

- **Build failed**: check the Render build log. Common causes: a missing
  environment variable Render needs at build time (none currently — all
  required env vars are runtime-only), or a frontend/backend build error
  (reproduce locally with `docker build -t test --target runtime .` before
  retrying on Render).
- **Deployed but `/api/v1/ready` returns 503**: almost always the database
  connection — re-check `DATABASE_URL`/`DATABASE_URL_SYNC` in Render's
  environment tab for typos, and confirm the Supabase project isn't paused
  (free-tier Supabase projects pause after a period of inactivity and need
  a manual "restore" click in the Supabase dashboard).
- **Deployed and healthy, but the app 500s on real use**: check
  `SUPABASE_URL`/`SUPABASE_SERVICE_KEY`/`SUPABASE_BUCKET_NAME` — a storage
  failure surfaces as `ERR_STORAGE_UNAVAILABLE` (503) on upload, logged as
  `supabase_storage_write_failed` with the reason. Run
  `python scripts/verify_supabase_storage.py` (pointed at the pilot's env
  vars) to isolate whether it's the bucket, the key, or something else.
- **Bad migration**: `alembic downgrade <previous-revision>` against the
  pilot database, then roll the Render deploy back to the previous commit
  via Render's dashboard (**Deploys → select a previous deploy → Redeploy**).
- **Nothing works and you're not sure why**: roll back the Render deploy to
  the last known-good commit first (fast, reversible), diagnose calmly
  second.

---

## Appendix — files this pilot phase added/changed

| File | What |
|---|---|
| `app/services/storage_service.py` | `SupabaseStorageService` implementation (new); `LocalStorageService` refactored to share the same object-key scheme, behavior unchanged |
| `app/main.py` | Serves `web/dist` same-origin when it exists; local backend-only dev (no build present) is unaffected |
| `Dockerfile` | New frontend build stage (Node); copies `web/dist` into the runtime image; respects `$PORT`; fixed a pre-existing bug (missing `README.md` in the builder stage broke `pip install .` before this pilot work even started) |
| `.dockerignore` | New — keeps `.env`, `.venv`, `node_modules`, local uploads/logs, and other local-only content out of the build context |
| `render.yaml` | New — Render Blueprint; every secret is `sync: false` (set by hand in Render's dashboard, never in this file) |
| `tests/test_storage_service.py` | New — offline unit tests for both storage backends (local: real temp-dir round-trip; Supabase: mocked HTTP transport proving request shape) |
| `scripts/verify_supabase_storage.py` | New — manual smoke test a person runs against the real pilot bucket once it exists; not part of the automated suite |
| `docs/PILOT_DEPLOYMENT.md` | This document |

Nothing in the pipeline, formatter, EDI/PDI logic, validation rules,
governance model, role definitions, or data model changed for this pilot.
