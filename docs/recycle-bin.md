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
history, vendor statistics and store invoice counts, so nothing is counted twice. The status
filter still finds them.

### Scenarios

- **A failed invoice is binned and processed again before an administrator acts.** The new upload
  proceeds at once. A pending deletion request for the binned run stays open; approving it deletes
  only the binned run (each upload has its own stored files).
- **An administrator restores a binned document while a newer copy is active.** A normal restore is
  refused; the administrator may restore it as a read-only reference copy for debugging. It cannot
  affect EDI, inventory or counts.

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

1. **Confirm the state (read-only, Supabase SQL editor).**
   ```sql
   SELECT version_num FROM alembic_version;                               -- expect exactly: 0036
   SELECT to_regclass('public.recycle_bin_entries') AS entries,
          to_regclass('public.bin_deletion_requests') AS requests;        -- expect: NULL, NULL
   SELECT count(*) FROM documents WHERE status = 'BINNED';                -- note the number
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

4. **Documents already binned before 0037** have no bin entry, so they do not appear on the
   Recycle Bin page (no backfill by design). They stay BINNED, read-only and uncounted, and can
   still be found with the Invoices status filter. If they should be listed, that is a separate,
   approved step.

5. **Verify in the application** with a test document:
   - bin it with a reason;
   - see it in the Recycle Bin;
   - restore it.
   
   Then confirm (read-only):
   ```sql
   SELECT status, binned_by, reason, closed_by FROM recycle_bin_entries ORDER BY binned_at DESC LIMIT 5;
   ```

## 4. Rollback

- **Code rollback** is safe: the previous release ignores the new tables and column. Reference
  copies would then show as an unknown status, so restore or purge any before rolling back code.
- **Migration rollback** (`alembic downgrade 0036`) is refused while any bin history or reference
  copy exists, because that history explains deletions.
