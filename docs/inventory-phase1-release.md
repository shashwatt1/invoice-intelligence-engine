# Inventory Phase 1 — release plan

Status: implemented, not committed, not deployed. `INVENTORY_ENABLED` defaults to off.
Pilot is reported at revision `0030`; that must be confirmed (step 1) before anything is applied.

## 1. Migration graph

Committed history is linear. The working tree has two heads because the unreleased
Identifier Resolution migration `0025` is untracked and branches from `0024`, beside
the already-deployed `0026`.

```
0001 → … → 0023 → 0024 ─┬→ 0026 → 0027 → 0028 → 0029 → 0030 → 0031   (committed chain; 0031 new, untracked)
                        └→ 0025                                      (Identifier Resolution; untracked, unreleased)
```

| Revision | down_revision | State |
|---|---|---|
| 0024 | 0023 | deployed; local dev DB is at 0024 |
| 0025 | 0024 | **untracked**, applied nowhere: local dev 0024, pilot 0030 (to confirm), test DBs built by `create_all` |
| 0026–0029 | linear from 0024 | deployed |
| 0030 | 0029 | deployed (`edi_exports`, RLS) |
| 0031 | 0030 | new: `inventory_receipts`, `inventory_movements`, RLS; additive only |

Consequences:

- **A commit containing 0031 but not 0025 has one head (`0031`).** Verified on a clean worktree of
  `a4e3da5` + 0031: `alembic heads` → `0031 (head)`, and upgrading an empty database to head leaves
  nothing for autogenerate (`test_migrations_match_models`, `test_rcm_store`: 7 passed).
- In this working tree, `alembic upgrade head` fails with "Multiple head revisions", as it already
  did with 0025 + 0030. Locally use an explicit target (`alembic upgrade 0031`), never `heads`,
  which would also apply 0025.
- No merge migration is needed or proposed now. When Identifier Resolution is released, **re-parent
  0025** onto the then-current head (renumbered, e.g. `0032`, `down_revision = "0031"`). That is
  safe only while no database has 0025 applied; if one ever does, a merge migration becomes
  necessary instead. Re-parenting changes the IR work and is not done here. It also needs:
  - `tests/test_identifier_persistence.py:268` (pins `down_revision = "0024"`);
  - the note in 0025 about `scripts/pilot_seed_guard.py`;
  - the guard's `EXPECTED_REVISION = "0024"`, which is already stale against the pilot (0030),
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
   SELECT version_num FROM alembic_version;                       -- expect exactly one row: 0030
   SELECT to_regclass('public.invoice_identifier_observations') AS ir_observations,
          to_regclass('public.invoice_line_resolutions')        AS ir_resolutions,
          to_regclass('public.inventory_receipts')              AS inventory_receipts,
          to_regclass('public.inventory_movements')             AS inventory_movements;  -- expect all NULL
   ```
   Stop if the revision is not exactly `0030` or any table exists.
2. **Commit only the inventory work** (when asked). Leave 0025 and the Identifier Resolution files
   untracked and unstaged. `app/models/__init__.py`, `app/api/v1/invoices.py` and
   `app/api/v1/router.py` also hold unrelated uncommitted lines; stage only the inventory hunks.
3. **Verify the commit in a clean checkout:** `alembic heads` → `0031` only; render the SQL with
   `alembic upgrade 0030:0031 --sql` and review it.
4. **Apply 0031 before deploying the code**, as one transaction that refuses to run unless the
   database is at 0030:
   ```sql
   BEGIN;
   DO $$ BEGIN
     IF (SELECT count(*) FROM alembic_version) <> 1
        OR (SELECT version_num FROM alembic_version) <> '0030' THEN
       RAISE EXCEPTION 'expected alembic_version 0030; aborting 0031';
     END IF;
   END $$;
   -- … the reviewed body of `alembic upgrade 0030:0031 --sql`, without its own BEGIN/COMMIT …
   COMMIT;
   ```
   The running code (`a4e3da5`) never touches the new tables, so this is safe before the deploy.
   Then verify:
   ```sql
   SELECT version_num FROM alembic_version;                                   -- 0031
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
- Downgrading 0031 drops both tables. Do it only while they are empty.

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

## 5. Regression baseline (clean `HEAD` a4e3da5, local Postgres)

| Suite | Clean HEAD | This working tree |
|---|---|---|
| Integration (`RUN_DB_TESTS=1 tests/integration`) | 192 failed, 170 passed, 1 skipped, 7 errors | 192 failed, 178 passed, 1 skipped, 10 errors |
| Failing test IDs | — | identical set; +3 errors from the two-heads tree (below) |
| Backend unit (`tests --ignore=tests/integration`) | — | 1 failed, 1750 passed |
| Frontend (vitest) | — | 229 passed |

**Root cause of all 199 HEAD failures and errors:** each one's first failure is the same refusal:
`ERR_VALIDATION_FAILED … "Store 47708760 / 86357232 (location not yet confirmed) is an unresolved
source identity … not a physical store"`. The integration fixtures seed stores known only by an
Item Sales code and upload invoices to them. The store guard committed with the Store Master work
now refuses such stores. These failures predate inventory and are not fixed or masked here.

By file: line_item_correction 20, manual_corrections 17, mapping_queue 15, export_api 15,
multi_photo 14, case_mapping_api 14, reprocess 12, reference_proposals 12,
invoice_detail_role_visibility 11, proposal_governance 10, api_db 10, document_lifecycle_actions 9,
first_invoice_store_86357232 7 (errors), data_team_user_permissions 6, multi_store 5,
store_identification 4, **p0_invoice_1000540 4**, auth 4, store_pending 3, bulk_review_api 3,
proposal_review_api 2, credential_management 2.

**Golden EDI for invoice 1000540:** its four integration tests fail at HEAD for the same reason, so
no passing suite currently reproduces the golden bytes end to end. Unit-level EDI and PDI
regressions pass (export_service, pdi_audit, case_mapping, edi_export_ledger, non_product_lines,
commercial_resolution, regression_gate: 229 passed). Tracked fixtures are unchanged.

**Diagnostic run, not a test result:** a throwaway pytest plugin, kept outside the repository,
treated every store as physical. It was run on clean HEAD and on this working tree over
p0_invoice_1000540, export_api, line_item_correction, manual_corrections, reprocess,
document_lifecycle_actions, store_pending, multi_photo and case_mapping_api. Both gave
130 passed, 1 failed, and the failure is the same test in both: it checks how a code-only store is
presented, which the plugin deliberately breaks. Under it, the four 1000540 golden tests pass on
both trees.

**The +3 errors in this working tree** (`test_migrations_match_models` ×2, `test_rcm_store` ×1) are
"Multiple head revisions": untracked 0025 alongside 0031. They pass on a clean worktree with 0031.

**Backend unit failure:** `test_identifier_persistence::test_the_impact_report_finds_every_export_call_site`
also fails on clean HEAD (unclassified `normalize_item_code` call sites in `commercial_resolution`
and `shadow_trace`). Inventory adds two more unclassified call sites
(`inventory_service.build_receipt_lines`, `record_opening_counts`). The classification table lives
in the uncommitted Identifier Resolution script `scripts/normalize_item_code_impact.py` and was not
edited.

## 6. Deployment prerequisites

- Step 1 confirms the pilot at exactly 0030 with none of the four tables present.
- The commit excludes 0025 and the Identifier Resolution files, and `alembic heads` on that commit
  shows only 0031.
- 0031 is applied (guarded SQL) and verified before the code deploy.
- `INVENTORY_ENABLED` stays unset for the first deploy.
- Known red integration baseline acknowledged (section 5). Fixing the fixtures is separate work.
