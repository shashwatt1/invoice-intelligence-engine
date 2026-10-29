# Product Master — shared mappings, vendor items and item-code safety (0038)

Status: **implemented on `feat/product-master-shared-mappings`; not released.** Nothing here has been
run against production. Migration `0038` is additive.

## 1. What changes for the data team

| Before | After |
|---|---|
| A units-per-case approval applied to **one store**. The same product was mapped again at each of the 14 stores. | Approve **for every store** (the default). Other stores resolve the same item code from that one decision. A store that sells it differently keeps an explicit, reasoned **store override**. |
| A UPC typed for a Frito-Lay/Red Bull line was kept on that invoice only. | Tick **Remember for this vendor's item** (on by default when the row has a vendor item number): the vendor's other open invoices and every later one get the UPC automatically, recorded as a correction. |
| A vendor item number, a 13-digit EAN or a 14-digit case code could reach the PDI item code, zero-padded or cut to 11 digits. | Such a line is **blocked from export** with the reason, until the product's UPC is entered (once per vendor item, with the crosswalk). |
| Two stores holding different approved values (e.g. Michelob Ultra C-18: 1 vs 18) went unnoticed. | Listed under **Product Master → Approvals → different approved values across stores**, settled once: one shared value, explicit exceptions, a required note. |

## 2. Data model (migration 0038, additive)

| Table / column | Purpose |
|---|---|
| `master_commercial_mappings.store_id` → nullable | `NULL` is the **shared scope**: one value for every physical store. At most one `APPROVED` shared value per item code (`uq_master_commercial_shared_approved`). |
| `master_commercial_mappings.override_reason` | Only on a physical store's mapping that deliberately differs from the shared value (`ck_master_commercial_override_store_scoped`). |
| `vendor_item_crosswalk` | Confirmed `(vendor, vendor item number) → 11-digit PDI item code` links. One `CONFIRMED` link per key; a wrong link is `RETIRED` (who, when, why), never edited. Each links to the approved proposal that created it. RLS on, no policies. |
| `product_data_proposals.vendor_id` | Whose item number a `vendor_item` proposal is about. `NULL` for every existing row. |
| `invoice_items.supplier_item_id` | The vendor's item number as extraction read it (previously only inside the raw model response). Never exported. |

No existing row changes meaning. No third product model or review queue: shared values live in the
Product Master; invoice-originated decisions (units-per-case and vendor items) still go through
`product_data_proposals` and Data Review; source-originated candidates (distributor sheets, item-master
imports) still go through Product Master Approvals. Both write into the same commercial model.

## 3. How a line resolves now

Identity (`app/services/gtin.py`):

| Printed | Key (mapping, inventory) | Exportable |
|---|---|---|
| 11 digits (PDI item code) | as printed | yes |
| 12-digit UPC-A, check digit right | first 11 | yes |
| 12 digits, check digit wrong | first 11 (unchanged key) | **no** — likely misread |
| 13 digits `0` + UPC-A / 14 digits `00` + UPC-A | the UPC-A's first 11 | yes |
| other 13/14 digits (EAN-13, case GTIN-14), 15+ | **every digit** (was: cut to 11) | **no** |
| 1–10 digits (vendor number) | as printed (unchanged key) | **no** |

Units-per-case (`app/services/product_master/commercial_resolution.py`, Product Master resolution on):

1. the store's approved Product Master mapping (an override with a reason wins over a different shared value; without a reason a disagreement is a CONFLICT);
2. the approved shared value / distributor-global mapping (must agree with each other);
3. the store's legacy mapping;
4. Requires Mapping.

**Never a silent choice:** a shared value that differs from the store's own approved legacy value is a
CONFLICT (before 0038 the global value won with a note). A conflicting line blocks export until settled.

## 4. Reviewer workflow

* **Data Review → proposal → Approve**: "Use for every store" is ticked by default. If another store
  already holds a different approved value the approval is refused and the stores are named; then either
  tick "Keep other stores' different values as store overrides", or untick "every store" to approve for
  this store only (a reason is required when the shared value differs — recorded as a store override).
* **Bulk approve** offers the same choice and stays all-or-nothing.
* **Invoice → No UPC / not a barcode → Enter the UPC**: with "Remember for this vendor's item" ticked, the
  link is confirmed in the same step (MANAGER/ADMIN, recorded as an approved `vendor_item` proposal) and
  applied to the vendor's other open, unexported, unlocked invoices. Lines printing a usable barcode are
  never overwritten; a disagreement is logged, not resolved.
* **Product Master → Approvals → different approved values across stores → Settle**: choose the value,
  tick stores to keep (each becomes an override with the note), others adopt it through an approved
  `conflict_resolution` proposal on their own mapping. A note is required.
* **Wrong vendor item link**: `POST /api/v1/product-master/vendor-items/{id}/retire` with a reason. Lines
  it already filled keep their recorded correction.

## 5. Reference-data import

`scripts/import_item_master.py FILE --mapping scripts/item_master_mappings/<name>.json` — preview by
default. Rows are classified NEW / EXISTING / POSSIBLE_UPDATE / DUPLICATE / CONFLICT / INVALID with
source file, sheet and row. `--apply` writes exactly the preview, then re-previews to prove a second run
writes nothing. Units-per-case enters only as a `REVIEW_REQUIRED` shared candidate from an **explicit**
units column — never from a formula or price ratio; approved values and open candidates are never
overwritten. `pdi_item_master.TEMPLATE.json` is a placeholder: no PDI/CStorePro export has been seen.
Writing to a non-local database needs `--allow-remote` and approval.

## 6. Compatibility and migration notes

* 11-digit, valid 12-digit and short codes keep their keys: existing `product_case_mappings`, inventory
  movements and golden EDI files are unaffected.
* 13/14-digit codes change key. Their old keys were truncations (wrong); a line carrying one now shows
  Requires Mapping under the correct key instead of matching a wrong mapping. Inventory movements already
  posted under an old truncated key stay as history; check readiness query 5 for affected open invoices.
* Lines with vendor numbers or non-UPC codes that exported before are now blocked until a UPC is entered.
  In the local corpus: Red Cliff Petroleum (fuel, 36 lines), UniFirst (uniform service, 10), Red Bull (7),
  M&N (10). Rows that are not merchandise (fuel, service) should be typed as charges.
* `scripts/backfill_supplier_item_ids.py` copies vendor item numbers from stored responses onto existing
  lines (fail-closed, dry run by default). Optional; the crosswalk also falls back to the raw response.

## 7. Rollout (each step approved and performed by the release owner)

1. Verified production backup (still an open item from the inventory release).
2. Run `docs/sql/product-master-0038-readiness.sql` (read-only) and review blocks 3–5:
   block 3 lines become conflicts after deploy; block 5 invoices become export-blocked.
3. Apply `0038` (`alembic upgrade 0037:0038 --sql` to review the SQL), then deploy.
4. Confirm `PRODUCT_MASTER_COMMERCIAL_RESOLUTION=true` — shared values only take effect with it on.
   `COMMERCIAL_SOURCE_IDENTITY` is no longer needed for shared values (still used for the distributor seed).
5. Optional: backfill supplier item ids (dry run, then `--apply --allow-remote`).
6. Settle any differences listed in Product Master → Approvals.

## 8. Rollback

* **Code**: the previous release ignores the new column/table. Shared values and overrides stop applying;
  store legacy mappings apply as before. Vendor item corrections already written stay on their lines.
* **Migration**: `alembic downgrade 0037` refuses while any shared value, override, crosswalk link or
  vendor_item proposal exists (reviewed decisions are not discarded silently). `supplier_item_id` is
  dropped (recoverable from the raw responses).

## 9. Measuring the improvement

`scripts/mapping_replay.py` replays invoices in processing order and counts, per line, what could have
been resolved from decisions made **before** that invoice, under today's store-only rule and the shared
rule — with incorrect automatic values (versus the store's final value) counted separately and never as
automation. Run it against a local copy of a database (it refuses non-local hosts).

Local corpus (14 real September invoices processed on a local copy — not production; 276 product lines):

| | store only | shared |
|---|---|---|
| lines resolved automatically and correctly | 3 | 37 |
| automatic but **incorrect** (store later decided differently) | 0 | 2 |
| manual (store, code) work items | 193 | 157 |
| lines blocked as non-exportable codes | 63 | 63 |

The 2 incorrect lines are the C-18 pattern: a store that sells the same barcode differently. They are why
shared values are refused, not chosen, when stores disagree. Production numbers need the readiness SQL and
a replay on a production copy.

## 10. Open items needing a business decision

1. Confirm the 2026-10-08 rule still holds: one units-per-case per product for every store, with
   explicit store exceptions.
2. Whether fuel (Red Cliff) and service (UniFirst) invoices should be exported to PDI at all.
3. Whether the PDI/CStorePro item-master export can be obtained, and whether its units column is
   authoritative (today every imported value still needs review).
