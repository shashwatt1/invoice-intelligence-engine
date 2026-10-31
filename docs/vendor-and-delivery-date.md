# Vendor and delivery date, declared at upload

Two things a person knows and a document cannot say reliably are now asked for when an invoice is uploaded, beside the store:

* **Vendor** — the vendor the invoice belongs to, chosen from the vendor list. It is what vendor-wise filtering and mapping group by.
* **Delivery date** — the day the goods physically arrived at the store (not the invoice date). It dates the inventory receipt.

Neither replaces what the document prints. The vendor and the dates read from the document stay on the invoice exactly as read.

## Rules

| Rule | Behaviour |
|---|---|
| Required at upload | A USER or MANAGER must give both. A request without them is refused (422) before any file is stored. An ADMIN may skip either and set it later. |
| Required before Retailed | For every role, an invoice cannot be marked Retailed until the vendor was chosen from the list (not merely read from the document, and not still waiting for approval) and the delivery date is set. The reason is shown where Retailed is. |
| Date format | A plain calendar day `YYYY-MM-DD`, exactly as typed. There is no default and the browser's "today" is never sent. Refused: not a real date, before 2000-01-01, or later than UTC today + 1 (so a US-evening delivery is never "the future"). |
| Vendor mismatch | If the document prints a vendor that does not match the one chosen (same tax id, or a matching name after punctuation and generic words such as Inc/Distributing are ignored), the invoice is flagged `REVIEW_REQUIRED` with the check `VENDOR_MATCHES_CHOICE`. This is a review reason, never a pause: the choice stands, a person decides which is right. A document that printed no vendor is not a mismatch. |
| Vendor not listed | A MANAGER or ADMIN adds it directly; it is confirmed and listed at once. A USER asks for it; the invoice is processed and waits for approval. A name that already matches a listed vendor is refused with the existing vendor named. Two people asking for the same name share one request. |
| Approval | A MANAGER or ADMIN approves (creates the vendor, or adopts the unconfirmed row the invoice's printed vendor already created), or names the existing vendor the request duplicates, or rejects with a reason. Every waiting invoice then has the approved vendor, as a person's choice, and is revalidated. |
| Reprocessing | A vendor a person chose is never replaced by what the document prints on reprocess. |
| After upload | A MANAGER or ADMIN can set the delivery date or the vendor on the invoice (`PATCH /invoices/{id}/delivery-date`, `/vendor`). Changes are kept in the correction history. |
| Changeable until Retailed | The vendor, the delivery date **and the store** can be set (if unset) or changed on an invoice until it is marked Retailed — by a MANAGER or ADMIN, before and after the invoice is VALIDATED, while the line maths and checks are still being worked out. Once Retailed (even if it later needs re-verifying) they are refused with 409 `ERR_INVOICE_RETAILED`; reopen the Retailed mark first. They are also refused while a POSTED receipt holds the invoice (reverse the receipt first). |
| Invoices already processed | Nothing is forced on them. Their vendor stays as the document read it and they have no delivery date; the vendor and date are set on the invoice itself (the card above the line items, or the "Set the vendor and delivery date" button beside Mark Retailed), and only Mark Retailed waits for them. |
| Moving an invoice to another store | `POST /invoices/{id}/change-store`. The store is replaced on the invoice and its document, and the new store's case mappings and review queue apply from then on. Refused while product proposals made from the invoice are still PENDING (decide them in Master Data Review first). Decided proposals stay as history at the store they were made for, and mappings they applied stay with that store. Export files already generated carry the old store and must be generated again (the response and the screen say how many). Not a physical store, or the same store: refused. |
| Receipt | The receipt approval dialog starts with the declared delivery date. The approver may enter another day; it is allowed, posts as entered, and is logged as `receipt_date_differs_from_declared_delivery`. |

## API

* `POST /invoices/process` — new form fields `vendor_id` **or** `vendor_request_id`, and `delivery_date`.
* `GET /vendors/options?store_id=` — the picker: names only, vendors that last supplied that store first, then confirmed, then by name. Any authenticated account.
* `POST /vendors` `{name}` — MANAGER/ADMIN.
* `POST /vendor-requests` `{name}` — any account. `GET /vendor-requests` — MANAGER/ADMIN. `POST /vendor-requests/{id}/approve` `{existing_vendor_id?, note?}` and `/reject` `{note}` — MANAGER/ADMIN.
* `PATCH /invoices/{id}/delivery-date` `{delivery_date, note?}`, `PATCH /invoices/{id}/vendor` `{vendor_id | vendor_request_id, note?}` and `POST /invoices/{id}/change-store` `{store_id, note?}` — MANAGER/ADMIN, until Retailed.
* Invoice detail gains `vendor_source` (`extracted`, `operator`, `requested`), `delivery_date`, `vendor_request`, `declarations_missing`, `vendor_mismatch` (all roles). The receipt preview gains `declared_delivery_date`.

## Data

Migration `0041_vendor_merge` (after 0040): `vendors.merged_into_id` and the `vendor_merges` record, both described under "Merging duplicate vendors" below. Additive; nothing is rewritten.

Migration `0040_vendor_and_delivery_date_at_upload`: table `vendor_requests` (row-level security on, like the other ledgers; one open request per normalised name); `documents.chosen_vendor_id`, `vendor_request_id`, `declared_delivery_date`; `invoices.vendor_source`, `delivery_date`. All new columns are nullable; nothing is back-filled. Existing invoices have no declared date and a vendor from the document; they need both declared before they can be marked Retailed.

The Retailed attestation fingerprint adds the delivery date only when one is set, so an invoice already marked Retailed keeps its mark.

## Merging duplicate vendors (`scripts/merge_vendors.py`)

Extraction creates a vendor per spelling, so one supplier can exist twice (two T.J. Sheehan rows, two PepsiCo rows). The picker would list both, so they are merged first. The vendor that **stays** is the winner; the duplicate is the loser.

* **Nothing is deleted.** The loser is kept as a tombstone (`merged_into_id`): its identity-review history is an audit record that must keep pointing at it, and an invoice that later prints its old spelling resolves to the winner instead of creating the duplicate again. The picker, the Vendor Master list and name checks never show a tombstone; a picker loaded before a merge still works (the merged-away vendor is read as the one that stays).
* **What moves:** invoices, product proposals of every status, documents' chosen vendor, vendor requests and the vendor item crosswalk. A crosswalk link the winner already holds for the same vendor item code is redundant when it names the same item code (the loser's is retired, with the reason) and a conflict when it names a different one (refused: decide which is right first).
* **What does not change:** lines, totals, stores, statuses, mappings, exports and inventory records. A Retailed mark does not cover the vendor, so a merge never makes one stale (proven by a test on a marked invoice).
* **Safeguards:** `--list-duplicates` and the dry run are read-only (the database itself refuses a write); an apply needs an active ADMIN `--actor`, a `--basis`, the plan fingerprint the dry run printed (a new invoice on the loser since then makes it stale) and `--expect-database host/db`. It refuses: a confirmed vendor merged into an unconfirmed one, two confirmed vendors (`--allow-both-confirmed`), different tax ids (`--allow-different-tax-ids`), names with nothing in common (`--allow-different-names`), an invoice with a POSTED receipt (`--allow-received-invoices`; only its vendor pointer moves) and a vendor already merged. Every moved invoice gets an audit entry; the merge is recorded once in `vendor_merges`.
* **Rehearsed** on a throwaway copy of the 0038 restore migrated to 0041 (dropped afterwards): the finder reported exactly the two known pairs among 14 vendors, both merges applied, 14 → 12 vendors, no invoice left on a tombstone, no duplicate group left.

```
python scripts/merge_vendors.py --list-duplicates
python scripts/merge_vendors.py --winner <id> --loser <id>                    # dry run; prints the plan fingerprint
python scripts/merge_vendors.py --winner <id> --loser <id> --apply --actor <admin> --basis "why" \
       --expect-plan <sha256> --expect-database <host/db>
```

## Setting

`UPLOAD_REQUIRES_VENDOR_AND_DELIVERY` (default `true`) — whether a USER or MANAGER upload without them is refused. Turn off only to roll back the requirement; the screen still asks, and Retailed still requires both.

## Rollout order (production steps are the operator's and need separate approval)

1. Back up. Apply migrations 0040 and 0041 (both additive; the previous release ignores the new columns and table).
2. **Merge the duplicate vendors first.** The picker lists every vendor, so duplicates (the same supplier under two rows) would be chosen at random. Run `scripts/merge_vendors.py --list-duplicates`, then a dry run per pair, review, then apply (see above; the pair with a POSTED receipt needs `--allow-received-invoices`).
3. Deploy the application.
4. Tell users: choose the vendor and enter the delivery day at upload; a vendor that is missing is added by a manager, or asked for.

Rollback: set `UPLOAD_REQUIRES_VENDOR_AND_DELIVERY=false` (or redeploy the previous release). The migration needs no rollback to do so. `alembic downgrade` drops the new tables and columns and loses what was declared (and the merge pointers: invoices already repointed stay on the vendor that stayed).
