# Store inventory baselines — production initialization procedure

Status: **not run anywhere except local synthetic databases.** No production database has migration
`0036`, and no production store has a baseline. Every step below is a separate, deliberate action,
performed by the release owner after explicit approval. Nothing here runs automatically.

## 1. What a baseline is

A store baseline is a store's inventory start. Products at that store **without** a product-level
opening count start at zero on the baseline date, so "opening 0 + received 30" shows on-hand 30.

| Store at initialization | Baseline date | Basis recorded |
|---|---|---|
| Has at least one POSTED receipt | arrival date (`received_on`) of its earliest POSTED receipt | `EARLIEST_POSTED_RECEIPT`, with that receipt as evidence |
| Has no POSTED receipt | the explicit `--initialization-date` | `INITIALIZATION_DATE` |
| Already has a baseline | unchanged | skipped |
| Source identity (not a physical store) | — | skipped |

A baseline is one row in `store_inventory_baselines` (migration `0036`): at most one per store,
never edited and never deleted by the application. It writes **no** inventory movements and changes
no existing row.

## 2. How balances use it (calculation assumptions)

Verified against the ledger code (`app/services/inventory_service.py`, `inventory_approval.py`):

- **Only approval moves stock.** A receipt writes RECEIPT movements once, when it is approved
  (POSTED), dated with its arrival date (`received_on`). Pending (SUBMITTED), rejected, withdrawn and
  superseded receipts have no movements, so they never count.
- **A reversal** writes one negative movement per original, with the same date, so a reversed receipt
  nets to zero.
- **A product-level opening count always takes precedence** for its product, with its existing rule:
  it is the closing stock of its count date, so movements on or before that date are already in it.
  Baselines never overwrite or hide an opening count, and a product without one can still get its own
  count later (which then takes precedence).
- **Against a baseline, every RECEIPT movement counts.** A receipt can be approved days after its
  goods arrived, with an arrival date before the baseline date. A zero start cannot already contain
  goods nobody had recorded, so omitting it would lose stock. Each movement is counted once, so nothing
  is double-counted.
- **Other movement types** (adjustments, and sales once imported) count from the baseline date on.
  Revisit this rule when sales import is designed.
- Before `0036` is applied, the code behaves exactly as today (no store has a baseline).

## 3. Preconditions

1. The release containing this work is reviewed and approved. The same release also contains
   migration `0037` (Recycle Bin), which adds a column the code reads, so **migrations come before
   the deploy**: apply `0036` (step 2 below), then `0037` (see [recycle-bin.md](recycle-bin.md)), then
   deploy. Run the initialization script only after the deploy.
2. The person running the script has an **active ADMIN** account in the application. It is recorded
   on every baseline.
3. The script runs from a trusted machine with the backend's dependencies installed and
   `DATABASE_URL` pointing at the production database. Credentials stay in that machine's
   environment; they are never pasted into chats, tickets or commits.
4. Agree the business **initialization date** (the go-live date) and the **reason** text beforehand.

## 4. Steps

For a production release, the order and approvals come from
[release-runbook-inventory-store-access.md](release-runbook-inventory-store-access.md): steps 1–2 here are
its state check and Gate 2, and steps 3–6 are its Gate 5, run only after the release is deployed.

### Step 1 — confirm the database state (read-only, Supabase SQL editor)

```sql
SELECT version_num FROM alembic_version;                                  -- expect exactly: 0035
SELECT to_regclass('public.store_inventory_baselines') AS baselines;      -- expect: NULL
```

Stop if the revision is not exactly `0035` or the table already exists.

### Step 2 — apply migration 0036 (reviewed SQL)

Render the SQL from the release commit and compare it with the reviewed copy below:

```bash
alembic upgrade 0035:0036 --sql
```

```sql
BEGIN;
CREATE TABLE store_inventory_baselines (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    store_id UUID NOT NULL,
    baseline_date DATE NOT NULL,
    basis VARCHAR(32) NOT NULL,
    earliest_receipt_id UUID,
    initialization_date DATE NOT NULL,
    initialized_by_user_id UUID,
    initialized_by VARCHAR(64) NOT NULL,
    initialized_by_role VARCHAR(16) NOT NULL,
    reason TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(store_id) REFERENCES stores (id) ON DELETE RESTRICT,
    FOREIGN KEY(earliest_receipt_id) REFERENCES inventory_receipts (id) ON DELETE RESTRICT,
    FOREIGN KEY(initialized_by_user_id) REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT uq_store_inventory_baselines_store UNIQUE (store_id),
    CONSTRAINT ck_store_inventory_baselines_basis CHECK (basis IN ('EARLIEST_POSTED_RECEIPT', 'INITIALIZATION_DATE')),
    CONSTRAINT ck_store_inventory_baselines_evidence CHECK ((basis = 'EARLIEST_POSTED_RECEIPT') = (earliest_receipt_id IS NOT NULL)),
    CONSTRAINT ck_store_inventory_baselines_dates CHECK (baseline_date <= initialization_date),
    CONSTRAINT ck_store_inventory_baselines_reason CHECK (length(trim(reason)) > 0)
);
ALTER TABLE store_inventory_baselines ENABLE ROW LEVEL SECURITY;
UPDATE alembic_version SET version_num='0036' WHERE alembic_version.version_num = '0035';
COMMIT;
```

The migration is additive (one new table) and initializes nothing. Row level security is enabled
with no policies, like the other inventory tables. Re-run step 1: expect `0036` and a non-NULL table.

### Step 3 — dry run (writes nothing)

```bash
python scripts/initialize_inventory_baselines.py \
  --actor <admin-username> \
  --initialization-date <YYYY-MM-DD> \
  --reason "<agreed reason>"
```

The report starts with `DRY RUN — nothing was written` and lists every store as
`WOULD INITIALIZE`, `SKIPPED` or `FAILED`, with its baseline date and why, then totals.
Review it with the business owner:

- each store with receipts shows the date of its first received invoice;
- each store without receipts shows the initialization date;
- source identities are skipped;
- **any `FAILED` line is resolved before continuing** (for example, a posted receipt dated after the
  initialization date).

To initialize only some stores first, add `--store <uuid>` once per store.

### Step 4 — apply

Run the same command with `--apply`. Each store is its own transaction: a failure for one store
never blocks or undoes another. Exit status is 1 if any store failed.

### Step 5 — confirm it is repeatable

Run the same `--apply` command again. Every store must now be `SKIPPED`, with
`Initialized: 0  Failed: 0`. Nothing is written or changed.

### Step 6 — verify (read-only)

```sql
SELECT s.display_name, b.baseline_date, b.basis, b.initialization_date, b.initialized_by
FROM store_inventory_baselines b JOIN stores s ON s.id = b.store_id ORDER BY s.display_name;

-- Opening counts and movements are untouched: compare with counts taken before step 4.
SELECT movement_type, count(*), sum(quantity) FROM inventory_movements GROUP BY movement_type;
```

In the application, open Inventory for a store. It should say "Inventory started at zero on …",
products without their own count should show "store start <date>", and on-hand should equal the
approved receipts since.

## 5. Corrections and rollback

- **Code rollback** is safe at any time: the previous release ignores the new table.
- **A wrong baseline** cannot be edited or deleted through the application. Correcting one is a
  deliberate, reviewed SQL change, approved separately, that deletes that store's row so the script
  can be re-run for that store. Record the reason. Product-level opening counts are the normal way
  to set a product's real stock.
- **Migration rollback** (`alembic downgrade 0035`) is refused while any baseline exists. Dropping
  baselines would silently change every balance that rests on them.

## 6. Must not happen

- Running `--apply` without a reviewed dry run, or with an unagreed initialization date.
- Running against production without explicit approval.
- Editing `inventory_movements` or opening counts to "fix" a baseline.
