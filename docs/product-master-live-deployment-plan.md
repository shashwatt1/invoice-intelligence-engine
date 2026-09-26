# Product Master — Live Pilot Deployment Plan

> **Superseded in part.** The pilot has since been migrated to `0024`. It is seeded by
> `scripts/pilot_seed.py` (verified target, in-memory `--dry-run` plan), not by the
> individual commands in step 5. The pilot starts with **0** `product_case_mappings`, not
> 132, so its commercial baseline is **412 READY / 2 CONFLICT**, not the local 410 / 4.
> Seeding it also needs a store for Item Sales code `47708760`. See
> `docs/product-master-pilot-baseline.md`.

**Status: PLAN ONLY. Nothing was executed against the pilot.** No migration was run
remotely, no Supabase row was written, and no Render deploy was triggered.

## Current state

| | Value |
| --- | --- |
| Local HEAD | `5dd5ee7` (**unpushed**) |
| `origin/main` | `2b2d2a4` |
| Render deployed SHA | **unknown from here** — no Render API key is configured; Render deploys `origin/main`, so it is at most `2b2d2a4` |
| Local Alembic head | **0024** |
| Supabase Alembic revision | **unverifiable from here** — the configured `DATABASE_URL` is `localhost`; the pilot password is not available to this session |
| 0022 / 0023 / 0024 deployed? | **No.** They are not committed, so they cannot be on `origin/main` or in the container |

Supabase is therefore at **0021** at best — the last revision that was committed and pushed.

## Local counts (the data to be moved)

| Table | Rows |
| --- | --- |
| master_products | 9,914 |
| master_product_identifiers | 12,208 |
| master_product_descriptions | 11,693 |
| master_pack_compositions | 259 |
| master_commercial_mappings | 414 (all REVIEW_REQUIRED) |
| master_commercial_reviews | **0** |
| product_case_mappings | 132 (untouched) |
| invoices / invoice_items / documents | 14 / 297 / 16 (untouched) |

## Deployment sequence — run in this order

1. **Commit and push** the Product Master work to `main`. Migrations 0022–0024 must exist in
   the repo before Render can apply them. `alembic/env.py` carries an unrelated local change
   — exclude it or commit it deliberately.
2. **Verify the pilot's current revision** before anything else:
   ```
   DATABASE_URL_SYNC='<pilot url>' python -m alembic current
   ```
   Expect `0021`. If it is not, stop.
3. **Apply the migrations** to the pilot:
   ```
   DATABASE_URL_SYNC='<pilot url>' python -m alembic upgrade head
   ```
   All three are additive: they create five new tables and add columns to
   `master_commercial_mappings` only. No existing table is altered.
4. **Deploy** the Render service from the new `main`.
5. **Seed the pilot, in this order** — each command has a local-only guard today, so seeding
   the pilot requires running them with the pilot `DATABASE_URL` and **temporarily**
   permitting a remote host. That guard exists deliberately; loosening it is a decision, not
   an oversight, and is the one step below that is not yet safe to run as written.
   ```
   python scripts/seed_product_master_identity.py --dry-run   # expect ~9,914 products
   python scripts/seed_product_master_identity.py
   python scripts/seed_product_master_commercial.py           # expect 414, all REVIEW_REQUIRED
   python scripts/backfill_commercial_source_snapshot.py      # expect 414 snapshots
   python scripts/seed_product_master_descriptions.py         # expect 56 canonical
   ```
6. **Verify the pilot** matches the expected starting state:
   - 414 commercial candidates
   - **0** approved
   - **0** rows in `master_commercial_reviews`
   - `product_case_mappings` still 132
   - invoices / invoice_items / documents unchanged

## What must be true before the team gets the URL

- [ ] Migrations 0022–0024 applied to Supabase
- [ ] Render deployed from a SHA containing them
- [ ] 414 candidates seeded with `source_snapshot` populated
- [ ] 0 approvals, 0 reviews
- [ ] Login works for Vivek / Barj / Prabh / Shashwat with the right roles
- [ ] Legacy `product_case_mappings` unchanged at 132

## Deliberate non-goals

The seed scripts' local-only database guard is **not** removed here. Seeding the pilot needs
an explicit decision about how that guard is satisfied — that is the remaining blocker, and
it is a judgement call rather than an engineering gap.
