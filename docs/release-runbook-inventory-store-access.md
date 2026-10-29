# Release runbook — store access, inventory baselines and Recycle Bin (Phases A, B, C)

Status: **proposed; nothing here has been run.** Branch `feat/inventory-store-access` (based on
`f803456`). Companion documents: [release notes](release-notes-inventory-store-access.md),
[inventory-baseline-initialization.md](inventory-baseline-initialization.md),
[recycle-bin.md](recycle-bin.md).

**Rules**

- No production action happens without the release owner's explicit approval at its gate. Each
  gate below is a separate decision, and the release owner performs every production step.
- **No production smoke-test step writes data.** A write in production needs its own specific,
  separately approved purpose. Write paths are verified in the pre-release rehearsal (section 2).
- If any check fails, stop at that step; nothing later runs.
- **No feature flag changes.** This release adds no flag. `STORE_SCOPED_OPERATIONS` does not exist in
  the code (store-scoped manager operations are parked); it must not be added or enabled without
  separate approval. `INVENTORY_ENABLED` and `EDI_EXPORT_LEDGER` stay as they are.

### Gates and checks at a glance

| # | Kind | What | Where |
|---|---|---|---|
| E1 | External check (read only) | Commit currently deployed on Render: the rollback target | step 1 |
| V | Local | Validation of the exact release SHA, and the rehearsal on that SHA | steps 2–6 |
| E2 | External check (read only) | A confirmed database backup and the invoice-storage check | steps 7–8 |
| E3 | External check (read only) | Production revision `0035` (or `0036`), no new tables | step 9 |
| G1 | Approval | Publish the release commit (fast-forward `main`, push) | step 10 |
| G2 | Approval | Apply `0036` | step 11 |
| G3 | Approval | Apply `0037` | step 12 |
| G4 | Approval | Deploy (needs V for the same SHA, and E1–E3) | step 13 |
| — | Read only | Production smoke tests | section 6 |
| G5 | Approval, later | Baseline initialization | step 15 |

## 0. What is being released

| Phase | Change | Schema |
|---|---|---|
| A | Admin store-access screen; grant-scoped Store Directory for managers; adding stores and editing store identity become ADMIN-only | none |
| B | Store inventory baselines (zero start) in the on-hand calculation; admin initialization script | `0036` (new table) |
| C | Recycle Bin: reasons, re-processing, restore, reference copies, deletion requests, audited purge; binned invoices read-only and not counted | `0037` (two new tables, one new column on `documents`) |

**Order is fixed: validate → back up → migrate `0036` → migrate `0037` → deploy → read-only smoke
test.** The code reads `documents.reference_of_document_id`, so it must not start before `0037`.
Both migrations are additive, and the currently deployed release works unchanged on the migrated
schema, so migrating first is safe.

## 1. Release commit validation (no production access)

CI is not configured on `main` (the only workflow is on another branch), so these checks are run
by hand and recorded.

1. **Production commit (read only; unresolved until verified).** In Render → the web service →
   Events/Deploys, record the commit currently deployed. **Do not assume it**: past notes mention
   `12c292c` and an unconfirmed deploy of `f803456`. This commit is the rollback target. If it is
   older than `f803456`, the release also ships every commit in between; list them in the release
   record.
2. **The release commit.** `main` is at `f803456` and the branch is strictly ahead of it, so merging
   is a fast-forward: the release commit is exactly the branch tip. Record its SHA. If anything is
   added to the branch later, the SHA changes and every check below is repeated.
3. **Checks on that exact SHA**, in a clean checkout (a fresh worktree, no local changes), run by
   the release owner, or by the assistant on request:

   | Check | Command (from the repository root) | Pass |
   |---|---|---|
   | Backend, with database tests | `RUN_DB_TESTS=1 python -m pytest -q` (local PostgreSQL running) | 0 failed; the 25 skips are the known data-file and live-API tests |
   | Frontend tests | `cd web && npx vitest run` | 0 failed |
   | Type check | `cd web && npx tsc -b` | exit 0 |
   | Lint | `cd web && npm run lint` | no errors |
   | Frontend production build | `cd web && npm run build` | succeeds |
   | Production image | `docker build -t hor-edi-release:<sha> .` (the Dockerfile Render builds) | succeeds |
   | Migrations | `alembic heads` → `0037 (head)`; `alembic upgrade 0035:0036 --sql` and `0036:0037 --sql` match the reviewed copies (only `CREATE TABLE`/`INDEX`, `ADD COLUMN`/`CONSTRAINT`, `ENABLE ROW LEVEL SECURITY`, the `alembic_version` update) | as stated |

   Record the SHA, date, who ran it, each result, the skipped tests and any warnings in the release
   record (kept outside the repository, so recording it does not change the SHA). **Deployment
   approval (Gate 4) requires this record for the same SHA.**

## 2. Pre-release rehearsal (local or test environment; synthetic data only)

No staging environment exists (the staging plan was never provisioned). The rehearsal runs the
exact release SHA against a throwaway database built the way production will be:

4. Create an empty local database and run `alembic upgrade 0035`. Then apply the reviewed `0036` and
   `0037` SQL exactly as in production, and seed it with synthetic users, stores and invoices. No
   customer data, and no copy of the production database.
5. Run the production image (or the app) from the release SHA against it, and verify every write
   path that production smoke tests will not touch:
   - move an invoice to the bin with a reason; it leaves the lists and dashboard; its page is read-only;
   - restore it; re-process the same file while binned (with a non-production OCR/LLM key, if one is
     available; otherwise rely on the integration tests, which run the real pipeline with a fake model);
   - restore as a reference copy (admin); request deletion, reject with a note, approve or purge with
     a reason;
   - PDI export of an export-ready invoice (writes the export ledger, here only). If the synthetic
     data has no export-ready invoice, rely on the integration suite, which runs the real export and
     ledger;
   - managers' Stores scoping and refused store edits; admin grant and revoke;
   - baseline dry run and apply (`scripts/initialize_inventory_baselines.py`).
6. Record the results with the validation record.

**Why no production processing test.** Phases A–C change only two things in processing: the
duplicate check now ignores binned documents, and a re-upload is linked to its bin entry. Both are
covered by database integration tests and the rehearsal. A production test would call the paid
OCR/LLM services (with the OpenAI rate-limit issue still open) and leave a test invoice in pilot
data. Instead, after the release, watch the first invoice the data team processes in the normal
course of work (read only: it completes, appears in the list, and the logs are clean).

## 3. Backups (production, read and export only)

7. **Database backup.** In the Supabase dashboard (Database → Backups), confirm a backup completed
   after the last data change, and record its timestamp. If the plan offers point-in-time recovery,
   record the restore point instead. If no recent backup exists, take a manual export (`pg_dump`
   from a trusted machine, kept off the repository) and confirm the file is non-empty.
   **Stop if no backup can be confirmed.**
8. **Invoice-storage recovery check (separate).** Database backups do not include stored invoice
   files.
   - Record the invoice bucket's object count from the Supabase dashboard (Storage).
   - Open two or three recent invoice photos in the application to confirm reads work.
   - Accept that files deleted by a purge cannot be recovered from any backup. An additional bucket
     copy is optional; decide before release.
   - `scripts/verify_supabase_storage.py` writes and deletes a probe object. It is a production
     write: run it only with separate approval.

## 4. Pre-deployment state check (read-only, Supabase SQL editor)

9. Run:
   ```sql
   SELECT version_num FROM alembic_version;                                     -- expect: 0035
   SELECT to_regclass('public.store_inventory_baselines') AS baselines,
          to_regclass('public.recycle_bin_entries')      AS bin_entries,
          to_regclass('public.bin_deletion_requests')    AS bin_requests;         -- expect: NULL, NULL, NULL
   SELECT count(*) AS active_grants FROM user_store_access WHERE revoked_at IS NULL;
   ```

   | State found | Action |
   |---|---|
   | `0035`, no new tables | expected; continue |
   | `0036`, baselines table only | `0036` already applied; skip step 10 |
   | anything else | **stop and investigate** |

## 5. Gates

10. **GATE 1 — publish the release commit.** Fast-forward `main` to the validated SHA and push to
    `origin`. Then confirm that `origin/main` equals the validated SHA; if not, stop. Render
    auto-deploy is off, so nothing deploys yet.
11. **GATE 2 — apply `0036`** (reviewed SQL, Supabase SQL editor, one transaction). Re-run step 9:
    expect `0036`, with the baselines table present.
12. **GATE 3 — apply `0037`** (reviewed SQL, one transaction). Re-run step 9: expect `0037`, with
    all three tables present. The running application (the commit from step 1) keeps working;
    confirm the invoice list loads.
13. **GATE 4 — deploy.** Requires the step 3 validation record and the step 5 rehearsal record for
    the same SHA. Render → the web service → **Manual Deploy → Deploy latest commit**. Do not
    change environment variables or flags; read `INVENTORY_ENABLED` and `EDI_EXPORT_LEDGER` in the
    Environment tab and record them.
14. When the deploy is live: `GET /api/v1/health` returns 200, `GET /api/v1/ready` returns 200, and
    the Render deploy shows the validated SHA.

Each migration is a single transaction. If it fails, nothing from it is applied: stop, keep the
current code, and investigate. Do not retry blindly.

## 6. Production smoke tests (read only)

Use one ADMIN and one MANAGER account. **Look, don't change.** No uploads, no exports, no grants,
no bin actions.

| Area | Check (read only) |
|---|---|
| Permissions | MANAGER: Stores lists only their granted stores; no Add store; no identity editing. ADMIN: Users → Store access opens and lists grants (close without changes). |
| Inventory | Open a granted store: products, balances and Supply history load. No "started at zero" note yet (no baselines). |
| Invoices | The Invoices list loads; open an existing active invoice: lines and totals show as before. |
| Exports | On an active, export-ready invoice, the **Download PDI Format** button shows as available. **Do not click it**: downloading records an EDI export. |
| Recycle Bin | The page opens; "In the bin" is empty (nothing binned since `0037`); the deletion-requests tab is empty. |
| Dashboards | The dashboard loads; its counts are consistent with the Invoices list. |
| Logs | The Render logs show no new errors during these checks. |

After the release, observe the first normal invoice processed by the data team (section 2, read
only).

## 7. Failure handling and recovery

| Situation | Action |
|---|---|
| A validation or rehearsal check fails | Do not proceed to Gate 1 or Gate 4. Fix on the branch; the SHA changes; repeat the checks. |
| A migration fails | Nothing from it is applied (one transaction). Keep the current code; investigate. |
| The deploy fails to build or start | Render keeps the previous deploy running. Investigate; the migrated schema is compatible with the previous code. |
| Deployed, but smoke tests fail | Roll back the code in Render (Deploys → the commit from step 1 → Redeploy). Leave the migrations in place. |
| Code rollback caveats | The previous release cannot delete a document that has an open bin entry, and it shows reference copies with an unknown status. Before rolling back, restore or purge any such documents, or prefer rolling forward with a fix. |
| Migration rollback | Not part of routine recovery. `downgrade` refuses once baselines or bin history exist, because they record decisions. Only with explicit approval, and only while those tables are empty. |
| A purge was a mistake | Cannot be undone: the stored files are gone, and restoring the database would not bring them back and would lose later data. Purges are ADMIN-only and need a reason; brief admins before release. |
| Full disaster | Restore the database from the backup recorded in step 7, and redeploy the commit from step 1. Data entered after the backup is lost. Files purged after the backup are gone regardless. |

## 8. After the release

15. **GATE 5 — baseline initialization (separate).** Not part of this release. When approved,
    follow inventory-baseline-initialization.md: agree the initialization date and reason, run the
    dry run, review it, apply, then repeat the run to confirm every store is skipped.
16. Communicate the [release notes](release-notes-inventory-store-access.md) to the data team.
