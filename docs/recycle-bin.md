# Recycle Bin — behaviour and production procedure

Status: **built and tested locally; not deployed.** Migration `0037` is applied nowhere except local
and test databases. Every production step below needs separate approval and is performed by the
release owner.

## 1. Behaviour

| Action | Who | Rules |
|---|---|---|
| Move to the bin | USER (own uploads), MANAGER, ADMIN | Reason required. Refused while the invoice is received into inventory (reverse the receipt first) and once it was exported to EDI (an EDI ledger record, or marked Retailed). Nothing is deleted. |
| See the bin | USER: items they uploaded or binned; MANAGER, ADMIN: all | Sidebar → Recycle Bin. |
| Process the same file again | anyone who may upload | Allowed at once while the original is in the bin; no administrator needed. The bin entry shows "Processed again". A duplicate of an active document is still refused. |
| Restore | whoever can see the item | Back to its previous status; a run that was mid-pipeline comes back STOPPED. Refused when an active document holds the same file, a shared photo, or the same invoice (same store, vendor and invoice number). |
| Restore as reference copy | ADMIN, only when such an active document exists | Status REFERENCE, linked to the active document. Read-only: never corrected, exported (PDI/CSV), marked Retailed, received, reprocessed or counted. |
| Request permanent deletion | whoever can see the item | Reason required; one pending request per item; the requester can withdraw it. Restoring cancels it. |
| Approve / reject a request | ADMIN | Approve = permanent deletion. Reject needs a note; the item stays in the bin. |
| Delete permanently | ADMIN | Reason required. The document, its invoice, lines, photos and processing history are deleted; stored files are removed after the database commit. |

**Audit:** every binning is a `recycle_bin_entries` row (who, when, why, previous status, and a
snapshot of the file name, hash, invoice number, vendor and store). It closes once: RESTORED,
RESTORED_AS_REFERENCE or PURGED. A purged entry keeps the snapshot and the deleting
administrator's reason after the document is gone. An administrator's direct delete from an
invoice or document page (existing routes) leaves the same PURGED record. Rows are never edited
or deleted (database constraints and ORM guards).

**Inactive documents** (in the bin, or reference copies) stay readable but are left out of the
default invoice list, dashboard counts and confidence, the mapping queue, store invoices, supply
history, vendor statistics and store invoice counts, so nothing is counted twice. Binned documents
are found in the Recycle Bin; reference copies through the Invoices "Reference copies" filter. The
Invoices status filter no longer offers a bin view.

### Scenarios

- **A failed invoice is binned and processed again before an administrator acts.** The new upload
  proceeds at once. A pending deletion request for the binned run stays open; approving it deletes
  only the binned run (each upload has its own stored files).
- **An administrator restores a binned document while a newer copy is active.** A normal restore is
  refused; the administrator may restore it as a read-only reference copy for debugging. It cannot
  affect EDI, inventory or counts.

### Limitations (verified 2026-10-29)

- **Documents binned before `0037`** are not in the Recycle Bin and cannot be restored through it.
- **Exports made before the EDI export ledger existed** leave no record, so binning cannot detect
  them; such an invoice can be binned (and, by an administrator, purged).
- **A purge deletes the stored invoice files for good.** Database backups do not include them.

## 2. Compatibility and order

- **Migrations before code.** This release adds `documents.reference_of_document_id` to the
  document model, so the new code needs `0037` applied before it starts: on a database without the
  column, every document read fails. The previous release ignores the new column and tables, so
  applying `0036` and `0037` first and deploying afterwards is safe.
- `0037` depends on `0036` (store inventory baselines). Apply `0036` first; see
  [inventory-baseline-initialization.md](inventory-baseline-initialization.md).
- Behaviour change on release: a binned invoice can no longer be corrected, exported or marked
  Retailed (until now it could), and it is no longer counted in the dashboard and lists above.

## 3. Production procedure (not run)

For a production release, follow
[release-runbook-inventory-store-access.md](release-runbook-inventory-store-access.md), which sets the
order and approval gates. The steps below are the `0037`-specific checks it relies on.

1. **Confirm the state (read-only, Supabase SQL editor).**
   ```sql
   SELECT version_num FROM alembic_version;                               -- expect exactly: 0036
   SELECT to_regclass('public.recycle_bin_entries') AS entries,
          to_regclass('public.bin_deletion_requests') AS requests;        -- expect: NULL, NULL
   ```
   Stop if the revision is not exactly `0036`.

2. **Render and review the SQL** from the release commit: `alembic upgrade 0036:0037 --sql`.
   It must contain only:
   - `CREATE TABLE recycle_bin_entries` with its four CHECK constraints;
   - `CREATE TABLE bin_deletion_requests` with its three CHECK constraints;
   - `ENABLE ROW LEVEL SECURITY` on both;
   - the partial unique indexes `uq_recycle_bin_entries_open` and `uq_bin_deletion_requests_pending`,
     plus the other indexes;
   - `ALTER TABLE documents ADD COLUMN reference_of_document_id`, with its foreign key and index;
   - the `alembic_version` update to `0037`.
   
   No `UPDATE`, `DELETE` or data change.

3. **Apply** the reviewed SQL. Re-run step 1: expect `0037` and both tables present. Only then
   deploy the release (section 2).

4. **Verify.** The write paths (bin, restore, reference copy, requests, purge) are verified in the
   pre-release rehearsal, not in production (see the release runbook). In production, check read
   only: the Recycle Bin page opens and both tabs are empty, and
   ```sql
   SELECT count(*) FROM recycle_bin_entries;   -- expect 0 right after the release
   ```

## 4. Rollback

- **Code rollback** keeps working on the new schema (the previous release ignores the new tables
  and column), with two caveats:
  - reference copies would show an unknown status, so restore or purge any first;
  - **the previous release cannot delete a document that has an open bin entry**: its delete
    routes do not close the entry, and the database refuses to leave an open entry without its
    document. Restore or purge such documents before rolling back code, or roll forward.
- **A purge cannot be undone** by restoring the database: the stored files are deleted, and
  database backups do not include them.
- **Migration rollback** (`alembic downgrade 0036`) is refused while any bin history or reference
  copy exists, because that history explains deletions.
