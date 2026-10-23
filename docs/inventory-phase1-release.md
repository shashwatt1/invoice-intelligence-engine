# Inventory Phase 1 — release plan

Status: integrated locally onto release `e81ebad` (branch `integ/inventory-ledger`, uncommitted);
not deployed. `INVENTORY_ENABLED` defaults to off. The pilot is expected at revision `0032` (the head
of the deployed release); that is unverified and must be confirmed (step 1) before anything is
applied. No migration has been applied to any database as part of this work.

## 1. Migration graph

Committed history is linear and has one head. The ledger migration was written as `0031` on `0030`
and never released. Release `b5f09bb` (v1.2.0) added `0032` on `0030` and deliberately left `0031`
unused, requiring the ledger migration to be re-parented onto `0032` before release. It now is:
revision `0033`, `down_revision = "0032"`. Revision id `0031` stays unused.

```
0001 → … → 0023 → 0024 ─┬→ 0026 → 0027 → 0028 → 0029 → 0030 → 0032 → 0033   (one head: 0033)
                        └→ 0025          (Identifier Resolution; untracked in the main tree only, unreleased)
```

| Revision | down_revision | State |
|---|---|---|
| 0024 | 0023 | deployed |
| 0025 | 0024 | **untracked in the main development tree only**, not in this branch, applied nowhere known |
| 0026–0029 | linear from 0024 | deployed |
| 0030 | 0029 | deployed (`edi_exports`, RLS) |
| 0032 | 0030 | deployed with v1.2.0 (reference link, Retailed columns); expected pilot head (to confirm) |
| 0033 | 0032 | new: `inventory_receipts`, `inventory_movements`, RLS; additive only |

Consequences:

- **`alembic heads` → `0033 (head)`** on this branch, and upgrading an empty database to head leaves
  nothing for autogenerate (`test_migrations_match_models`).
- The re-parent is safe only while no database has the old `0031` applied. Step 1 checks the pilot
  for it (and for the inventory tables) before anything is applied; any database that ever had
  `0031` would need a merge migration instead.
- When Identifier Resolution is released, **re-parent 0025** onto the then-current head. That is
  safe only while no database has 0025 applied; if one ever does, a merge migration becomes
  necessary instead. Re-parenting changes the IR work and is not done here. It also needs:
  - `tests/test_identifier_persistence.py:268` (pins `down_revision = "0024"`);
  - the note in 0025 about `scripts/pilot_seed_guard.py`;
  - the guard's `EXPECTED_REVISION = "0024"`, which is stale against the pilot,
    independent of this work.

## 2. How migrations reach the pilot

Per `docs/PILOT_DEPLOYMENT.md` §5–6: the app never migrates on startup; Render runs no
`preDeployCommand`. Migrations are applied by hand. 0030 was applied as reviewed SQL in the
Supabase SQL editor. The applied revision is read from `alembic_version`. The EDI export ledger
also records it per export (`edi_exports.build.alembic_revision`).

## 3. Rollout sequence

Nothing below has been run. Each step is a separate, deliberate action.

1. **Confirm the pilot's state (read-only, Supabase SQL editor).**
   ```sql
   SELECT version_num FROM alembic_version;                       -- expect exactly one row: 0032
   SELECT to_regclass('public.invoice_identifier_observations') AS ir_observations,
          to_regclass('public.invoice_line_resolutions')        AS ir_resolutions,
          to_regclass('public.inventory_receipts')              AS inventory_receipts,
          to_regclass('public.inventory_movements')             AS inventory_movements;  -- expect all NULL
   ```
   Stop if the revision is not exactly `0032` (in particular if it is `0031`) or any table exists.
2. **Commit the integration** (when asked) from branch `integ/inventory-ledger`. It contains only
   the ledger work merged onto `e81ebad`; 0025 and the Identifier Resolution files are not in it.
3. **Verify the commit in a clean checkout:** `alembic heads` → `0033` only; render the SQL with
   `alembic upgrade 0032:0033 --sql` and review it.
4. **Apply 0033 before deploying the code**, as one transaction that refuses to run unless the
   database is at 0032:
   ```sql
   BEGIN;
   DO $$ BEGIN
     IF (SELECT count(*) FROM alembic_version) <> 1
        OR (SELECT version_num FROM alembic_version) <> '0032' THEN
       RAISE EXCEPTION 'expected alembic_version 0032; aborting 0033';
     END IF;
   END $$;
   -- … the reviewed body of `alembic upgrade 0032:0033 --sql`, without its own BEGIN/COMMIT …
   COMMIT;
   ```
   The running code (`e81ebad`) never touches the new tables, so this is safe before the deploy.
   Then verify:
   ```sql
   SELECT version_num FROM alembic_version;                                   -- 0033
   SELECT relname, relrowsecurity FROM pg_class
    WHERE relname IN ('inventory_receipts', 'inventory_movements');           -- both true
   ```
5. **Deploy the code with `INVENTORY_ENABLED` unset.** Inventory routes answer 404, the sidebar hides
   Inventory, `GET /api/v1/inventory/status` → `{"enabled": false}`. The receipt lock on invoice
   corrections is active but blocks nothing, because no receipts exist. The guard checks for the
   tables itself (`to_regclass`), so a deploy that accidentally precedes step 4 does not fail
   corrections.
6. **Smoke test:** correct, add and void a line on a test invoice; export PDI for a validated invoice.
7. **Later, separately:** set `INVENTORY_ENABLED=true`, redeploy, then record opening counts.

**Rollback.**
- Turning the flag off hides inventory, but received invoices stay locked against correction.
  Reverse their receipts first (that needs the flag on).
- Redeploying `e81ebad` is safe with 0033 applied: it never touches the new tables.
- Downgrading 0033 (back to 0032) drops both tables. Do it only while they are empty. A Render
  rollback alone does not undo the migration.

## 4. Received invoices: correction lock and reversal

- **What a POSTED receipt blocks:** every change that would alter what it recorded (HTTP 409
  `ERR_INVOICE_RECEIVED`, naming the receipt):
  - line correction, adding a line, voiding a line;
  - the duplicate decision;
  - store reassignment;
  - invoice delete;
  - move-to-bin;
  - reprocess.

  Date and totals corrections stay allowed; they do not change received quantities. Stop only
  applies to documents still in flight, and confirm-store and defer-store only to documents
  waiting for a store, so neither can reach a received invoice.
- **Locking:** posting, reversing and every guarded change first take `SELECT … FOR UPDATE` on the
  invoice row. A correction cannot interleave with a receipt; whichever commits second sees the
  other's result. Tested with two connections.
- **Reversal** (`POST /inventory/invoices/{id}/receipt/reverse`, MANAGER+, reason required, names the
  receipt id):
  - adds one compensating movement per receipt movement: negated, same business date,
    `reversal_of_id` set, key `reversal:{movement}`;
  - marks the receipt `REVERSED` with who, when and why;
  - deletes and edits nothing.

  A repeated request changes nothing and reports `already_reversed`. Afterwards the invoice can be
  corrected and received again through the same eligibility and approved-conversion checks.
- **Schema rules:**
  - at most one `POSTED` receipt per invoice (partial unique index);
  - a `REVERSED` receipt must carry who, when and why (CHECK);
  - receipt movement keys are scoped to the receipt (`receipt:{receipt}:{line}`), so the same
    invoice line can be received again after a reversal.

## 5. Regression baseline (integration onto `e81ebad`, isolated local test database)

Run on 2026-10-23 against branch `integ/inventory-ledger` (uncommitted merge of `feat/inventory-ledger`
`4e16e09` onto `e81ebad`), with `INVENTORY_ENABLED` unset and the database URLs pre-checked to point
only at the isolated test server (`invoice_test`, `invoice_test_migrations`):

| Suite | Result |
|---|---|
| Integration (`RUN_DB_TESTS=1 tests/integration`) | 422 passed, 1 skipped, 0 failed (410 before the ledger + its 12) |
| Backend unit (`tests`) | 1538 passed, 447 skipped, 0 failed |
| Frontend (vitest) | 373 passed, 38 files |
| Type check, lint, build | pass (lint: 7 pre-existing warnings) |
| `alembic heads` | `0033 (head)`; `test_migrations_match_models` passes |

The integration fixtures now upload to physical fixture stores (release `b101eb0`), so the red
baseline recorded before that release no longer applies; the golden-EDI tests for invoice 1000540 run
and pass in the suite above. The UPC correction added in `c57de40` goes through the posted-receipt
lock like every other line correction (`test_a_upc_correction_is_refused_too_and_nothing_changes`).

## 6. Deployment prerequisites

- Step 1 confirms the pilot at exactly 0032 with none of the four tables present.
- The commit excludes 0025 and the Identifier Resolution files, and `alembic heads` on that commit
  shows only 0033.
- 0033 is applied (guarded SQL) and verified before the code deploy.
- `INVENTORY_ENABLED` stays unset for the first deploy, and inventory stays unexposed to pilot users
  until per-user store access is enforced on every inventory read and write route.
