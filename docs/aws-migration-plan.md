# AWS migration plan

Status: **approved direction, Phase 0 in progress.** Nothing in AWS exists yet.
Render and Supabase stay exactly as they are until the cutover in Phase 4; until
then every invoice is still processed there.

## Why move

| Today | Effect |
|---|---|
| Render **free** plan | Sleeps when idle, cold-starts on the next visit, small shared CPU |
| App in Render **Oregon**, database at Supabase | Every page makes many database calls; each crosses the network between providers |
| Manual deploys, no CI | Nothing checks a change before it reaches the pilot |

On AWS the application and its database sit in the same region and network, the
service never sleeps, and every change is tested and deployed by a pipeline.

What it will **not** change much: invoice processing time (about a minute), which
is OCR and OpenAI time. OpenAI rate-limit errors are an OpenAI account matter.

## Decision

| Need | Choice | Why this, here |
|---|---|---|
| Run the app | **ECS Fargate** (1 task to start), behind an **Application Load Balancer** | Runs the existing Docker image unchanged; no servers to patch; always on; rolling deploys with automatic rollback |
| HTTPS address | **CloudFront** in front of the load balancer | Login cookies are `Secure` in production, so HTTPS is required from day one. CloudFront gives `https://<id>.cloudfront.net` before the company domain exists; the domain attaches later without rebuilding anything. Also caches the static frontend |
| Database | **RDS for PostgreSQL 16**, private subnets | Same engine as today; point-in-time recovery; same region as the app |
| Invoice files | **S3**, private, encrypted, versioned | Same object keys as Supabase Storage, so no database row changes (`STORAGE_BACKEND=s3`, implemented) |
| Secrets | **Secrets Manager** | Nothing secret in the repo, the image or GitHub |
| Infrastructure | **Terraform**, in this repo | Every resource reviewed in a pull request and reproducible; staging and production from the same code |
| CI/CD | **GitHub Actions**, AWS access through **OIDC** | GitHub stays the source of truth; no long-lived AWS keys anywhere |

Considered and not chosen: **EC2 with Docker Compose** (cheapest, but a server to
patch, back up and keep alive by hand); **Lightsail** (simple, but weak CI/CD and
networking for a growing product); **Elastic Beanstalk** (adds a layer without
benefit over ECS for one container); **EKS/Kubernetes** (far more than one
service needs); **App Runner** (simplest, but less control over networking and
deploy steps than ECS).

Two environments, same Terraform: **staging** (rehearsals, testing; can be
stopped when idle) and **production**.

## Phases

### Phase 0: preparation (no change to Render or Supabase)
- S3 storage backend (done, off unless `STORAGE_BACKEND=s3`).
- CI on every pull request and push to `main` (done: `.github/workflows/ci.yml`).
- Terraform for the network, RDS, S3, ECR, ECS, load balancer, CloudFront, secrets, logs and alarms.
- Deploy workflows (below), copy and verify scripts, runbooks.
- AWS account access from the company (see the request list).

### Phase 1: build staging
Apply Terraform for staging. Every merge to `main` deploys there automatically.

### Phase 2: rehearse with a copy of the data
1. A person with Supabase access runs a **read-only** dump (`pg_dump`, schema `public`, no owners or privileges). Nobody writes to Supabase.
2. Restore into staging RDS; copy Supabase Storage objects to S3 under the same keys.
3. Verify before going further: schema at the same Alembic revision; row counts equal for every table; invoices open with their photos; **PDI files exported from staging byte-identical to production's** for the same invoices.
4. Staging is labelled "STAGING" in the app name so nobody confuses it with the pilot.

### Phase 3: reference data (on staging, in parallel)
Load the ~6,200 vendor workbook products and the ~430 Product Master records as
reference **evidence**, together with the approved units-per-case mappings, using
the existing importers (`app/services/reference_workbooks.py`,
`scripts/propose_from_reference.py`). Nothing becomes approved master data
without review. Staging data is replaced at cutover, so the work is captured as
scripts and replayed on production after cutover.

### Phase 4: cutover (one planned window, about an hour)
1. Agree a time with no client testing; pause processing on Render.
2. Final read-only dump; restore into production RDS; copy objects added since the rehearsal.
3. Run the Phase 2 checks.
4. Point users at the new address; smoke test.

**Rollback:** Render and Supabase are only ever read during the move, so they
stay intact. Within the first hours, go back to the Render address. After that,
fix forward.

### Phase 5: retire
Render and Supabase stay paused but intact for 30 days, with the final dump
archived in S3. Then they are shut down.

## CI/CD

- `main` is protected: changes arrive only through pull requests that pass CI.
- **Pull request:** lint, unit tests, database tests on a temporary Postgres, frontend types/lint/tests/build, Docker build.
- **Merge to `main`:** build the image once (tagged with the commit), push to ECR, run migrations as a one-off ECS task, deploy to staging, smoke test.
- **Production:** a GitHub Environment that needs a named approver. It deploys the **same image** as staging, takes an **RDS snapshot before migrations**, then rolls out; ECS rolls back automatically if health checks fail.
- Terraform changes go through pull requests too: the plan is shown in the PR and applied on approval.

## Backups

- RDS automated backups with point-in-time recovery (7 days on staging, 35 on production).
- A snapshot before every production migration (automatic, in the deploy workflow).
- S3 versioning on the invoice bucket.
- A nightly logical dump to a separate S3 bucket.

## Cost (estimate, us-east-1)

Production about **$80–120 a month**: RDS small instance with storage and
backups, one Fargate task, the load balancer, CloudFront, and small amounts for
S3, logs and secrets. Staging is smaller and can be stopped between rehearsals.
The network is designed without a NAT gateway (about $35 a month saved).

## What to request from the company

1. **An AWS account for this product** (a dedicated account in the company's AWS Organization is best).
2. **Sign-in through IAM Identity Center (SSO)** with administrator access to that account for the build phase. Terraform must create IAM roles (for ECS tasks and for GitHub's OIDC sign-in), which narrower roles usually cannot.
3. **Region us-east-1 allowed**, with no organization policy blocking ECS, ECR, RDS, S3, CloudFront, ACM, Secrets Manager, CloudWatch, IAM or the GitHub OIDC identity provider.
4. **A budget alert** on the account (for example at $150 a month).
5. **Domain (later):** a subdomain such as `invoices.<company-domain>`, either delegated to Route 53 in this account (NS records) or with the company's DNS team adding one CNAME and one certificate-validation record. Not needed to start.

## Open decisions

- AWS account details from the company.
- The domain name.
- Who besides the owner may approve production deploys.
