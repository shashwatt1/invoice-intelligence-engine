# Inventory store visibility and opening state

## Why only some stores appeared

The Inventory page lists the physical stores the signed-in account holds an active grant for
(`user_store_access`, `revoked_at IS NULL`). An empty store was never dropped; it was simply not granted.
In the production snapshot the administrator account held grants for the five stores that had receipts and a
manager held all fourteen. No filter on invoices, receipts, exports or Retailed status exists.

A second problem hid behind it: a store with no opening count and no baseline looked the same as a store that
counted zero.

## What changed (no migration)

* `GET /api/v1/inventory/overview` — every physical store the account may see, with its summary:
  * MANAGER: granted stores only.
  * ADMIN: granted stores with figures, plus every other **active** physical store as a locked entry
    (`access_granted: false`, `overview: null`). The locked entry carries no figures and cannot be opened:
    every store route still answers the same 404 without a grant.
  * USER: 403. Source identities are never listed.
* `InventoryOverview` gains `opening_state`, `opening_counted_products`, `opening_last_counted_on`,
  `opening_last_counted_by`, `on_hand_units`.
* `POST /api/v1/inventory/stores/{id}/zero-start` (ADMIN, needs the admin's own grant) — wraps the existing
  baseline service.
* Frontend: one request for the store grid (was one per store), opening-state pill, "Available units"
  (a dash when unknown), "Opening not entered" and "No access for me" filters, locked cards, a banner and an
  admin-only "Start at zero" dialog on the store view.

## Opening state (derived, never stored)

| State | Meaning | Source |
| --- | --- | --- |
| `NOT_SET` | Stock is **unknown**, not zero. No on-hand figure is shown. | no baseline, no opening-count movement |
| `ZERO_START` | An administrator declared the store started at zero. Not a physical count. | one `store_inventory_baselines` row |
| `COUNTED` | People entered physical opening counts (a counted 0 is a count). | `OPENING_COUNT` movements |

A physical count outranks the declaration. Counts go through the existing opening-count dialog/API
(who, date, reason; one per product per store — a second is refused).

## Idempotency and protection

* Zero start writes one baseline row (immutable by trigger) and nothing else: no movement, no receipt, no
  change to a product with its own count. Repeating it returns `created: false`, keeps the first reason and
  writes nothing.
* Refused: missing reason, future date, a date after the store's first posted receipt, a non-physical or
  ungranted store, any non-ADMIN.
* Nothing resets balances, rewrites posted receipts, or changes Retailed status.

## Rollback

No migration. Reverting the code removes the endpoints and UI; any baseline rows created through
`zero-start` are ordinary 0036 baselines and stay (they are immutable by design).

## Follow-up: administrator access and zero-start semantics

### Administrators reach every active physical store

The original rule ("no implicit all-store access for any role, ADMIN included") was enforced in three
functions in `app/services/store_access.py`, so it applied everywhere at once. It is now:

* **ADMIN** (active): every *active* physical store, no grant needed. An explicit grant to an inactive
  store is still honoured.
* **MANAGER**: only stores with an active grant, exactly as before. **USER**: no inventory (403).
* A source identity (for example the unresolved Item Sales code 47708760), an unknown id and an inactive
  store without a grant are one and the same 404, for everyone.

`ensure_store_access`, `has_store_access`, `accessible_stores` and `visible_physical_stores` implement it, so
the Inventory list, every store route, Supply History, receipt approval, opening counts, invoice
deletion's receipt reversal and zero start all follow the one rule. Granting and revoking remain audited
actions (a reason, immutable history); they simply change nothing for an administrator.

### What zero start does to each product

The ledger has no per-product row for a product that never had a count, and none is needed: a store
baseline already *is* an explicit zero for every product at the store that has no opening count of its own.

| Product situation | Without a zero start | With a zero start |
| --- | --- | --- |
| Has movements, no own count | unknown, **needs an opening count**, no on-hand figure | explicit opening 0 on the start date, on hand = receipts, no longer pending |
| Has its own physical count | count, then later movements | unchanged (a count always wins) |
| First received after the start | unknown | starts from 0 |

The product count never changes (products come from movements). Available units for a store that started at
zero but has no product yet is 0, not unknown. Nothing is written per product, so nothing can be duplicated.

### Stores with history

`GET /inventory/stores/{id}/zero-start/preview` (administrator, writes nothing) reports the start date, products
given an explicit zero, products with their own count, products still needing a count, and available units
afterwards. The same figures are shown in the dialog before anything is recorded.

* **No receipts or movements**: confirmed zero start on the chosen date (default today, never future).
* **Posted receipts or movements exist**: the store may have held stock before them. The start date is the
  arrival date of the earliest posted receipt (so every receipt counts exactly once and none is skipped), a
  date before it is refused, and the request must also carry `acknowledge_prior_stock`. The acknowledgement
  is kept in the baseline's reason. The alternative is to record physical opening counts.
* Every request must carry `confirm`; repeating a zero start is a no-op and needs neither.

No migration, no new table, no second baseline system.

### Products a store carries before any stock movement

Inventory products used to come only from movements, so a store with a product list but no stock event
showed no products at all. The store's own catalogue — `store_product_references`, one row per
(store, normalized item code) imported from that store's POS Item Sales export by
`scripts/import_store_reference.py` — is now part of the inventory population:

* A catalogue product with no movement is a balance with nothing moved. Without a zero start it **needs an
  opening count**; with one it sits at an **explicit zero** (opening 0, on hand 0, out of stock, source
  "store start"). No movement, receipt or per-product row is created for it.
* A product that has movements and a catalogue row is one balance. Receipts count once, from the ledger.
* The overview, the product list, the opening-count list and the zero-start preview all fold the same
  population (`inventory_service.store_catalogue` + the movement rows), so the preview's "products at an
  explicit zero" equals what the overview shows after the start.
* A catalogue is not stock history: a store with only a catalogue needs no prior-stock acknowledgement.
* One store's catalogue never appears in another.

No migration: the table has existed since 0006. In the 30 Oct production snapshot it is **empty for all 14
stores**, so this changes nothing in production until a store's export is imported. Stores' approved case
mappings (`product_case_mappings`) are deliberately *not* treated as a catalogue: they record which products were
reviewed from invoices, not which products a store carries.

### Reviewed commercial references (units per case, descriptions)

Which products a store carries (its catalogue and movements) is **separate** from what reviewed commercial
data says about them. `app/services/product_master/reference_resolution.py` annotates an inventory product
with two values and never adds, removes or changes a product, a movement, a receipt, an opening count, a
catalogue row, a proposal or a mapping. It only reads.

It reuses the existing resolvers rather than copying their rules:

* **Units per case** — `commercial_resolution.resolve_store_codes`, the one resolver the EDI export, the invoice
  detail and the Requires Mapping queue already share, so inventory cannot disagree with them. In order: an
  APPROVED store-specific Product Master mapping; an APPROVED shared / global mapping (only where no store
  decision of its own disagrees); the store's confirmed case mapping (`product_case_mappings`); otherwise
  nothing. A store-specific value that disagrees with a shared one wins only with a recorded `override_reason`;
  without one — or when a store's confirmed case mapping disagrees with a shared value, or two approved values
  disagree — the code is **HELD** and nothing is chosen. A confirmed store value is never silently replaced.
* **Description** — `commercial_resolution.canonical_names`: the confirmed canonical description (or name) of the
  one master product the code reaches.

What the layer adds:

* Every value carries `status` (RESOLVED, MISSING, HELD), `source` (resolution path), `scope`, `approval`
  (APPROVED / STORE_CONFIRMED / CONFIRMED), `mapping_id` and notes.
* **Ambiguous product identity is held.** If a code's UPC names more than one master product, or the approved
  mapping belongs to a different product than the UPC names, neither units nor description derived from the
  product is used.
* A description is shown in place of the store's own only when the store's is **missing**
  (`description_from_reference`); otherwise the reviewed one appears beside it under `reference`.
* Mappings not yet approved (PENDING, REVIEW_REQUIRED) are never used; they are reported in the notes.
* It follows `PRODUCT_MASTER_COMMERCIAL_RESOLUTION`: while that is off, only the store's confirmed case mapping
  resolves, exactly as the EDI export does.

**Impact on inventory calculations: none.** Quantities are in sellable units from receipts; units per case is
reference information and no balance, status, opening or available figure reads it. **No migration.**

**Primary description.** The store's POS description stays the product's description. The confirmed reference
description is shown separately (`reference.description`) and replaces the primary one only when the store's own
is missing (`description_from_reference`).

**Known limitation — search.** The product list resolves reference values only for the page it returns, after
searching, filtering and sorting. Product search therefore matches the item code and the *primary* description
(the store's own, or none) — it does **not** match a reviewed description that the store's record lacks. A
product whose only description is the reviewed one is shown with it, but cannot be found by typing that
description. Resolving every product before searching is possible, at the cost of a Product Master lookup for
the whole catalogue on every search; it was not done.

**Feature flag.** `PRODUCT_MASTER_COMMERCIAL_RESOLUTION` controls which sources resolve. This change does not
alter it or any other environment variable.

### Which date a zero start uses

The server decides "today" in **UTC** and refuses any start date after it (`future_date`, 422). The dialog therefore
**never sends the browser's own current date**: a browser east of UTC can be a day ahead (03:09 IST on 31 Oct is
still 21:39 UTC on 30 Oct), and its "today" was refused, which made a start impossible for those hours.

* **No date chosen** (the default): the dialog sends none, and the server applies its own default to the preview
  and to the start alike, so what was previewed is what is recorded.
  * A store **with posted receipts** starts on its earliest receipt's arrival date, as before; the default only
    becomes the recorded run date. No date is offered for these stores.
  * A store **without receipts** starts on the server's UTC date today (previously the browser's date).
* **A date the person picks** is sent to the preview and the start, kept as chosen, and still refused if it is after
  the server's today or (with receipts) before the first receipt. The date picker's ceiling is the server's today.
* A failed preview shows the server's message, error code and **request id** (for Render logs), and the start stays
  disabled; it can never be reached from a failed or loading preview.

Related, not changed here: the receipt and opening-count dialogs also default to the browser's date. A store whose
earliest posted receipt is dated "tomorrow in UTC" (entered from a browser ahead of UTC) would be refused a zero start
with `after_first_receipt` until the UTC date reaches it.
