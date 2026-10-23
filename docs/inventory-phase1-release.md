# Inventory Phase 1 — release plan

Status: integrated locally onto release `e81ebad` (branch `integ/inventory-ledger`; per-user store access uncommitted);
not deployed. `INVENTORY_ENABLED` defaults to off. The pilot is expected at revision `0032` (the head
of the deployed release); that is unverified and must be confirmed (step 1) before anything is
applied. No migration has been applied to any database as part of this work.

## 1. Migration graph

Committed history is linear and has one head. The ledger migration was written as `0031` on `0030`
and never released. Release `b5f09bb` (v1.2.0) added `0032` on `0030` and deliberately left `0031`
unused, requiring the ledger migration to be re-parented onto `0032` before release. It now is:
revision `0033`, `down_revision = "0032"`. Revision id `0031` stays unused.

```
0001 → … → 0023 → 0024 ─┬→ 0026 → 0027 → 0028 → 0029 → 0030 → 0032 → 0033 → 0034 → 0035   (one head: 0035)
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
| 0034 | 0033 | new: `user_store_access` (per-user physical-store grants and their history), RLS; additive only; grants nothing |
| 0035 | 0034 | alters `inventory_receipts` only: receipt approval lifecycle (§4a); no row changed; downgrade refused once approval history exists |

Consequences:

- **`alembic heads` → `0035 (head)`** on this branch, and upgrading an empty database to head leaves
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
          to_regclass('public.inventory_movements')             AS inventory_movements,
          to_regclass('public.user_store_access')               AS user_store_access;    -- expect all NULL
   ```
   Stop if the revision is not exactly `0032` (in particular if it is `0031`) or any table exists.
   `0032` is the **expected pilot starting revision**. The other states this code knows about are
   handled as follows. Anything else, stop and investigate.

   | Database state | Meaning | What to apply before deploying this code |
   |---|---|---|
   | `0032`, no inventory tables | expected pilot state | `0033`, `0034`, `0035` together (step 4) |
   | `0033` or `0034` | an earlier inventory release was applied | the remaining revisions up to `0035`, guarded on the actual revision |
   | `0035` | already current | nothing |
   | `0031`, or tables present at `0032` | unexpected | stop; do not apply anything |

2. **Commit the release** (when asked). The migration chain on that commit is
   `0030 → 0032 → 0033 → 0034 → 0035`. 0025 and the Identifier Resolution files are not in it.
3. **Verify the commit in a clean checkout:** `alembic heads` → `0035` only; render the SQL with
   `alembic upgrade 0032:0035 --sql` and review it. 0035 alters `inventory_receipts` only and
   changes no row.
   **3a. Confirm the target database and the application's role** (read-only, before step 4).
   Write down each answer. Stop if any answer is unclear.
   - **Database identity.** Open Render, service `invoice-intelligence-pilot`, Environment. Read
     only the host part of `DATABASE_URL`; never copy or display the password. In Supabase, open
     the project whose connection settings show that same host. Run step 4 only in that
     project's SQL editor. Stop if the hosts differ, or if more than one project could match.
   - **Application role.** From the same `DATABASE_URL`, read only the username (between `//`
     and `:`). The pilot guide documents `postgres`. If the username carries a suffix (for
     example a project reference after a dot), confirm in Supabase's connection settings which
     PostgreSQL role it maps to. Do not assume it is the role the SQL editor runs as.
   - **Ownership, privileges and role attributes.** Check these three separately: they are
     different things.
     - *Ownership:* which role owns a table.
     - *Privileges:* what a role has been granted on it.
     - *Role attributes:* superuser, `BYPASSRLS`.

     In that project's SQL editor (read-only), substitute the application role:
     ```sql
     SELECT current_user;                                   -- the role the SQL editor runs as
     SELECT tablename, tableowner FROM pg_tables
      WHERE schemaname = 'public' AND tablename IN ('invoices', 'edi_exports');
     SELECT rolname, rolsuper, rolbypassrls FROM pg_roles
      WHERE rolname IN (current_user, '<application role>');
     -- One privilege per call: a comma-separated list is true if ANY one of them is held.
     SELECT has_table_privilege('<application role>', 'public.invoices', 'SELECT') AS can_select,
            has_table_privilege('<application role>', 'public.invoices', 'INSERT') AS can_insert,
            has_table_privilege('<application role>', 'public.invoices', 'UPDATE') AS can_update,
            has_table_privilege('<application role>', 'public.invoices', 'DELETE') AS can_delete;
     ```
   - **Expected arrangement** (the only one this procedure covers):
     - the SQL editor's role (`current_user`) and the application role are the same role;
     - that role owns `invoices` and `edi_exports`;
     - all four privilege checks are true.
   - **Why ownership is what matters.**
     - Step 4 then creates the inventory tables owned by the role the application connects as.
     - Those tables enable row level security without `FORCE` and with no policies. PostgreSQL
       then denies every row to any role that does not bypass row security; a table's owner
       normally does.
     - So ownership, not the privilege check, is what lets the application read and write
       them. The privilege check only shows that the role already works with existing data.
       It does not prove that row level security will be bypassed.
     - `edi_exports` relies on the same arrangement, but it has not been exercised on the pilot
       yet because the ledger has been off.
     - Once this release is deployed, every invoice edit reads `inventory_receipts`, with the
       flags on or off, so this must hold before the deploy.
   - **If anything differs, stop for review.** That includes:
     - the two roles are different;
     - the application role is only a member of the owning role, not the owner;
     - another role owns the tables;
     - a privilege check is false.

     Do not run step 4 as another role, and do not change ownership, grants, role attributes or
     policies ad hoc. That is a separate, reviewed decision.

4. **Apply 0033, 0034 and 0035 before deploying the code**, as one transaction that refuses to run
   unless the database is at 0032.
   - **Schedule a quiet period.** Creating the new foreign keys briefly locks `invoices`,
     `invoice_items`, `stores`, `users` and `edi_exports` against writes until the transaction
     commits. Pause invoice work for those few moments.
   - **Re-check first.** Immediately before running, repeat step 1's checks in the same SQL
     editor: still exactly `0032`, and no inventory tables.
   ```sql
   BEGIN;
   DO $$ BEGIN
     IF (SELECT count(*) FROM alembic_version) <> 1
        OR (SELECT version_num FROM alembic_version) <> '0032' THEN
       RAISE EXCEPTION 'expected alembic_version 0032; aborting 0033-0035';
     END IF;
   END $$;
   -- … the reviewed body of `alembic upgrade 0032:0035 --sql`, without its own BEGIN/COMMIT …
   COMMIT;
   ```
   The running code (`e81ebad`) never touches the inventory tables, so this is safe before the deploy.

   **If the transaction fails** (any error, including the 0032 guard): stop.
   - Do not retry it, in part or in full. A new attempt needs the error investigated and fresh
     approval. Keep the error text.
   - **Close the session's transaction if it is still open.** If the SQL editor session is still
     inside the failed transaction, further statements are refused with "current transaction is
     aborted". Run `ROLLBACK;` to end it. This only closes the session's transaction; it says
     nothing about the database's state.
   - **Check the actual state, in a new query:**
     ```sql
     SELECT version_num FROM alembic_version;                  -- expect exactly one row: 0032
     SELECT to_regclass('public.inventory_receipts')  AS inventory_receipts,
            to_regclass('public.inventory_movements') AS inventory_movements,
            to_regclass('public.user_store_access')   AS user_store_access;  -- expect all NULL
     ```
     PostgreSQL rolls a failed transaction back as a whole, so this clean 0032 state is what to
     expect.
   - **If the state is anything else** (another revision, more than one row, or any of the
     three tables present): stop for manual review. Do not apply, drop or repair anything.

   On success, verify:
   ```sql
   SELECT version_num FROM alembic_version;                                   -- 0035
   SELECT relname, relrowsecurity FROM pg_class
    WHERE relname IN ('inventory_receipts', 'inventory_movements', 'user_store_access');  -- all true
   SELECT count(*) FROM user_store_access;                                    -- 0: nothing granted
   SELECT count(*) FROM information_schema.columns
    WHERE table_name = 'inventory_receipts' AND column_name = 'submitted_at';  -- 1: 0035 present
   SELECT tablename, tableowner FROM pg_tables
    WHERE schemaname = 'public'
      AND tablename IN ('inventory_receipts', 'inventory_movements', 'user_store_access');
   -- all three owned by the application role confirmed in 3a; if not, stop: do not deploy
   ```
5. **Deploy the code with `INVENTORY_ENABLED` unset.**
   - Inventory routes answer 404, the sidebar hides Inventory, and
     `GET /api/v1/inventory/status` → `{"enabled": false}`.
   - The receipt lock on invoice corrections is active but blocks nothing, because no receipts
     exist.
   - Deploy only after step 4's verification shows 0035. The code is written to cope with other
     states:
     - The guard detects the schema itself: no inventory tables; 0033/0034 without the approval
       columns; or 0035.
     - Before 0035 it reads only columns that exist since 0033, and still refuses changes to an
       invoice with a POSTED receipt.
     - Invoice delete, reopening Retailed and marking Retailed do not touch approval data.
     - The inventory routes answer 404 even if switched on.
   - That compatibility is covered by unit tests, and by a check against a real database migrated
     to 0034 (`test_migrations_match_models`, passed 2026-10-24). That check covers the guard,
     delete, reopening Retailed and marking Retailed.
   - 0033 has the same `inventory_receipts` columns as 0034 and was not run separately.
   - Neither check is a full application smoke test. Treat deploying ahead of 0035 as a fallback
     that fails safe, not as the plan: apply 0035 first.
6. **Smoke test:** correct, add and void a line on a test invoice; export PDI for a validated invoice.
7. **Grant store access explicitly** (no account has any by default, ADMIN included). Each grant is
   audited (who, role, when, why); an administrator may grant their own account, recorded as such:
   ```
   python scripts/grant_store_access.py grant --actor <active ADMIN> --username <MANAGER or ADMIN> \
       --store-id <physical store id> --reason "<why>"
   ```
   or `POST /api/v1/users/{user_id}/store-access` as an ADMIN. Revoke the same way (`revoke`, or
   `POST /api/v1/users/{user_id}/store-access/{store_id}/revoke`); `list` shows the history.
8. **Later, separately:** switch the workflow on. The Retailed-to-inventory approval workflow needs
   **both** flags:
   - `EDI_EXPORT_LEDGER=true`: marking Retailed requires a ledger-recorded PDI export. With the
     ledger off, nothing can be marked Retailed, so nothing is ever submitted for approval.
   - `INVENTORY_ENABLED=true`: marking Retailed submits the receipt, and the inventory routes and
     approval queue are available.

   Redeploy, then record opening counts. Invoices already marked Retailed before the switch-on are
   **not backfilled**. To receive one, reopen its Retailed mark and mark it again, which submits it.
   Not for pilot users until this release is approved for them.

**Rollback.**
- Turning `INVENTORY_ENABLED` off hides inventory, but received invoices stay locked against
  correction and cannot be deleted. Reverse their receipts first, which needs the flag on.
- Pending submissions are not withdrawn by switching off. They wait, and may be stale when inventory
  is switched on again. Reopening Retailed or deleting the invoice still withdraws them while it is
  off.
- Redeploying `e81ebad` is safe with 0033–0035 applied: it never touches the inventory tables.
- Downgrading 0035 is refused (the migration raises) while any receipt went through the approval
  workflow, so that history is never discarded; with none, it restores 0034's table exactly.
- Downgrading 0034 drops `user_store_access`, and with it the grant history; downgrading 0033
  (back to 0032) drops the ledger tables. Do either only while the tables are empty; with grants
  or receipts on record, redeploy the earlier code instead and leave the tables. A Render rollback
  alone does not undo a migration.

## 3a. Per-user store access (migration 0034)

Inventory is per user and per physical store. An account may list, view or change a store's
inventory only while all of these hold, checked on every request (nothing is cached):

- the account is active (`get_current_user` refuses inactive accounts: 401);
- its role is MANAGER or ADMIN (USER is refused, grant or not: 403);
- the store is a physical store;
- the account holds an active grant for that store in `user_store_access`.

There is no implicit all-store access for any role, ADMIN included.

- **Every inventory route** resolves its store through `app.services.store_access`. A store id that
  is unknown, a source identity, or not granted gets the same 404 (`Store not found.`, echoing only
  the requested id), so ids cannot be probed for existence.
- **Invoice-linked routes** (receipt review, approval, rejection, reversal) authorize inside the
  service, on the invoice's store (review) or the receipt's own store (approval, rejection,
  reversal), after taking the invoice row lock. Refusals are the same 404 as an unknown invoice or
  receipt, and write nothing. The approval queue (`GET /inventory/stores/{id}/approvals`) is a store
  route like the others.
- **The store picker** lists only `GET /inventory/stores`: the account's granted physical stores.
- **Grants are history.** Grant and revoke are ADMIN-only (API or CLI), need a reason, and lock the
  target account's row, so concurrent changes for one account are serialized; a partial unique
  index allows one active grant per account and store. A revocation stamps the row once; a revoked
  row never changes again (refused in the service, in an ORM guard, and by a CHECK constraint), and
  granting again inserts a new row. Rows are never deleted; foreign keys to the account and store
  RESTRICT deletion.
- **Demotion, deactivation and revocation take effect on the account's next request.** A demoted or
  deactivated account keeps its grant rows as history, but they confer nothing.
- **Scope:** inventory only. Invoices, the dashboard, Requires Mapping, Data Review and the store
  directory are not store-scoped yet; every MANAGER/ADMIN still sees every store there (app-wide
  store access is later work, B7/C3).

## 4. Received invoices: correction lock and reversal

- **What a POSTED receipt blocks:** every change that would alter what it recorded (HTTP 409
  `ERR_INVOICE_RECEIVED`, naming the receipt):
  - line correction, adding a line, voiding a line;
  - the duplicate decision;
  - store reassignment;
  - move-to-bin;
  - reprocess.

  Invoice delete is no longer refused: it reverses the receipt first, in the same transaction
  (§4a), and needs inventory on and the deleting ADMIN's own grant for the store.

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

  A repeated request changes nothing and reports `already_reversed`. Only a `POSTED` receipt can be
  reversed. Reversal never reopens Retailed. Afterwards the invoice can be corrected, re-exported
  and marked Retailed again, which submits a new receipt (linked to the reversed one) for approval.
- **Schema rules:**
  - at most one `SUBMITTED` or `POSTED` receipt per invoice (partial unique index, 0035);
  - a `REVERSED` receipt must carry who, when and why (CHECK);
  - receipt movement keys are scoped to the receipt (`receipt:{receipt}:{line}`), so the same
    invoice line can be received again after a reversal.

## 4a. Receipt approval (migration 0035)

A receipt is never posted directly; `POST /inventory/invoices/{id}/receipt` no longer exists.

- **Submit.** Marking an invoice Retailed with `INVENTORY_ENABLED` on creates a `SUBMITTED` receipt
  in the same transaction, with no movements. It records the submitter and role, the time, the
  Retailed attestation fingerprint, the EDI export the mark rested on, and a snapshot of the
  proposed lines, blockers and conversions. Marking needs no store grant (Retailed is unchanged).
  Nothing is submitted with inventory off, at a non-physical store, or over a `POSTED` receipt.
  There is no backfill: invoices marked Retailed before the flag was on have no submission.
- **Flags.** The workflow needs both `INVENTORY_ENABLED` and `EDI_EXPORT_LEDGER` (rollout step 8).
  Retailed itself is unchanged: without inventory it is an attestation only.
- **Approve or reject** (`POST …/receipt/approve`, `POST …/receipt/reject`, MANAGER/ADMIN with an
  active grant for the receipt's store; no implicit ADMIN access).
  - Self-approval is allowed; submitter and approver are recorded separately (`self_approved`).
  - Approval re-checks under the invoice and receipt locks that the submission is still pending
    and the invoice still Retailed under the same attestation. It also re-checks that nothing
    attested changed and that the store, eligibility, conversions and lines match the submission.
    It then posts once (`POSTED`; movement keys unchanged).
  - Accepted quantities may be adjusted: whole sellable units, and a reason for any difference.
    The proposal stays in the snapshot; each movement keeps the expected and accepted quantities.
  - The business date is the approver's, and required, exactly as before; it is isolated in
    `received_on_for_approval` so the policy can change later.
  - A rejection needs a reason, is terminal, and moves nothing. The Retailed mark stays; to
    submit again, reopen it and mark it Retailed again. The new submission points back to the
    rejected one.
- **Stale, withdrawn, superseded.**
  - A pending submission whose invoice changed is stale and cannot be approved; the review shows
    why and which lines changed.
  - Marking Retailed again supersedes it, and the new submission points back to it.
  - Reopening Retailed withdraws it.
- **Delete.**
  - Deleting an invoice withdraws a pending submission. It reverses a `POSTED` receipt first,
    atomically with the delete; this needs inventory on and the ADMIN's own grant, otherwise 409
    and nothing changes.
  - A failure anywhere rolls the whole operation back. A retry reverses once, because of the
    invoice lock and the reversal idempotency keys.
  - Receipts and movements are never deleted: they keep the invoice number and lose only their
    link to the invoice (SET NULL). The ledger's own foreign keys RESTRICT.
- **Wrong store.** There is no store move. Delete the invoice, which reverses its receipt at the
  wrong store. Upload it again for the right store, then validate, map, export and mark Retailed,
  and approve the new submission there.
- **Database rules.**
  - One CHECK per state keeps each state's audit fields consistent: posting fields for
    `POSTED`/`REVERSED`; submission fields; rejection, withdrawal and supersession fields set
    exactly in their state.
  - An ORM guard refuses any update outside the allowed transitions, and any delete.
  - Existing directly posted receipts satisfy every rule unchanged.
- **UI.**
  - A per-store Approvals tab, with a count in the overview.
  - A review dialog: the submission against the current invoice, stale reasons, changed lines,
    blockers, and the approve, adjust and reject controls.
  - An inventory status beside the Retailed control. It shows only when the server confirms this
    account's access to the store; a 404 shows nothing.

## 5. Regression baseline (inventory receipt approval, isolated local test database)

Run on 2026-10-23 against `feat/inventory-receipt-approval` (uncommitted, on local `main` `870fb9a`).
`INVENTORY_ENABLED` was unset in the environment; the tests switch it on per test. The pre-flight
checked every database URL resolves only to the isolated test server (`127.0.0.1:55433`:
`postgres`, `invoice_test`, `invoice_test_migrations`).

| Suite | Result |
|---|---|
| Integration (`RUN_DB_TESTS=1 tests/integration`) | **449 passed, 1 skipped, 0 failed** (10m08s, 2026-10-24, on the final code). The skip is the live OpenAI test. |
| Backend unit (`tests`, excluding integration) | 1660 passed, 24 skipped, 0 failed |
| Frontend (vitest) | 388 passed, 38 files |
| Type check, lint, build | pass (lint: 7 pre-existing warnings) |
| `alembic heads` | `0035 (head)`. `test_migrations_match_models` passes: the 0035 downgrade refusal, downgrade to 0034, the always-on paths checked at 0034, upgrade back to head, and the same paths checked at 0035. |

The integration result was taken on exactly the code under review (a scratch copy identical to the
worktree). An earlier run, before the pre-0035 compatibility changes, gave the same 449/1/0.

The integration fixtures upload to physical fixture stores (release `b101eb0`). The golden-EDI tests
for invoice 1000540 run and pass. The UPC correction (`c57de40`) goes through the posted-receipt lock
like every other line correction.

## 6. Deployment prerequisites

- Step 1 confirms the pilot at exactly 0032 with none of the four tables present.
- The commit excludes 0025 and the Identifier Resolution files, and `alembic heads` on that commit
  shows only 0035.
- 0033, 0034 and 0035 are applied together (guarded SQL) and verified before the code deploy.
- `EDI_EXPORT_LEDGER` and `INVENTORY_ENABLED` are both needed for the approval workflow, and both
  are switched on only as a separate, approved step (step 8).
- `INVENTORY_ENABLED` stays unset for the first deploy, and inventory stays unexposed to pilot users
  until per-user store access is enforced on every inventory read and write route.
