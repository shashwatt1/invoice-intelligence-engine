# Revalidating existing invoices (revalidate-only tool)

Invoices stored before the completeness and financial rules (PR #7) have `invoices.blocking_checks` NULL, so nothing
blocks their export and the confidence they carry predates the completeness cap. `scripts/revalidate_invoices.py` brings
named invoices under the rules **without editing them**. The deployed application has no way to do that: a date or totals
correction revalidates, but it also stamps the field as hand-corrected, even when the value is unchanged.

## What it does and does not do

| It does | It never does |
|---|---|
| Judge the stored invoice with the deployed `build_report`, from the **stored OCR text** and the stored lines | OCR, a model call, or any network call other than the database |
| Show the exact before/after of status, confidence, `blocking_checks` and document status | Edit lines, totals, proposals, mappings |
| Show historical EDI exports, POSTED/submitted receipts and the Retailed mark around the invoice | Touch EDI exports, the EDI ledger or any inventory record |
| On `--apply`: set the invoice's status, confidence and `blocking_checks`, sync the document status as revalidation does, and append a VALIDATION entry (so the screen shows the new report) and an audit entry | Run Rule D/E (the stored-line rewrites of the deployed revalidation) |

**Refused before anything is written:** a database other than the one named by `--expect-database`; an invoice with a POSTED inventory receipt; a document in the Recycle Bin or a
reference copy; an invoice number matching none or more than one invoice; an invoice where the deployed revalidation
would rewrite a stored line (Rule D/E); an `--actor` that is not an active ADMIN; an `--expect-plan` that is missing or
no longer matches the invoice.

There is no "all invoices" mode. Every invoice is named.

## Before anything: a backup, and the targets

1. Supabase → Database → Backups: confirm a backup that is later than your last data change and record its timestamp.
2. Decide the invoice numbers. The initial targets are **48288200** and **501315**. **2037594523 is not applied**: it
   has a POSTED receipt (the tool refuses it) and its photograph has not been checked.

## Step 1 — dry run (writes nothing)

From a checkout of the merged code, with the production connection string for **this command only** (an asyncpg URL,
the same form the application uses; a read-only role is better where one exists, but the dry run opens a READ ONLY
transaction regardless and the database refuses any write in it):

```
DATABASE_URL='postgresql+asyncpg://…' python scripts/revalidate_invoices.py \
    --invoice-number 48288200 --invoice-number 501315 --invoice-number 2037594523
```

Including 2037594523 here is read-only and confirms the refusal. For each invoice it prints:

* `invoice status`, `composite confidence`, `blocking_checks`, `document lifecycle` as `before -> after`
* completeness, the printed codes that have no line, the failing checks
* historical EDI exports, POSTED receipts, submitted receipts, Retailed
* `stored lines that the deployed revalidation would rewrite` (must be 0 to apply)
* `REFUSED (would not be applied): …` or `WOULD CHANGE the invoice row   plan sha256 <64 hex>`

The first line, `TARGET DATABASE: host=… database=…`, names the database actually connected to (host and name only, never a
credential) and the `--expect-database host/db` text to pass to an apply. Check that it is the production database you mean.
Exit code 2 means at least one invoice is refused.

The tool reads `DATABASE_URL` from the environment and, failing that, from a `.env` file in the **current directory**; with
neither it uses the local development database. Run it from a directory without a `.env`, or export `DATABASE_URL`
explicitly, and read that line before going on.

**Review it, then stop.** Send the output for approval. Expected for 2037594523: `REFUSED … posted_inventory_receipt`.

## Step 2 — apply (only after separate written approval, and only for approved invoices)

```
DATABASE_URL='postgresql+asyncpg://…' python scripts/revalidate_invoices.py \
    --invoice-number 48288200 --invoice-number 501315 --apply --actor <your ADMIN username> \
    --expect-plan 48288200=<sha256 from step 1> --expect-plan 501315=<sha256 from step 1> \
    --expect-database <host/db printed by the dry run>
```

`--expect-database` is compared with the database actually connected to (host and name from the connection, and the name
the server itself reports) **before anything is read**. If it differs, nothing is read or written. A dry run accepts the
same argument and checks it the same way.

`--expect-plan` binds the apply to exactly what was reviewed: if the invoice changed since the dry run (a new export, a
correction, a receipt) the fingerprints differ and nothing is written. Each invoice is its own transaction.

## Exports while it runs

PDI, TXT and CSV requests share-lock the invoice for the length of the request, and the apply takes the exclusive lock
first. So no operational pause is needed: an export that starts during an apply waits for it and is then refused by the
new block; an apply that finds an export in flight waits for it, and, because the export landed, its plan fingerprint no
longer matches and nothing is written (re-run the dry run, which now counts the export, and apply again). JSON and the
labelled review copies deliver no export file and take no lock. Historical exports are never touched.

## After applying

```sql
select invoice_number, status, composite_confidence, blocking_checks from invoices
 where invoice_number in ('48288200','501315');                       -- blocking_checks now set
select count(*) from edi_exports e join invoices i on i.id = e.invoice_id where i.invoice_number = '501315'; -- unchanged
```

and compare the row counts and the exports with the numbers recorded before. The invoice screen now shows the
INCOMPLETE banner, the export is blocked, and a labelled review copy and the administrator override are available.

## Rollback

The tool changes four values per invoice and appends two log entries. The dry run printed the previous values. To undo,
restore them with one targeted update per invoice (a production write; needs approval):

```sql
update invoices set status = '<before>', composite_confidence = <before>, blocking_checks = null where invoice_number = '<n>';
update documents set status = '<before>' where id = (select document_id from invoices where invoice_number = '<n>');
```

The appended log entries are history and are left. No line, proposal, mapping, export or inventory record was changed, so
there is nothing else to restore.
