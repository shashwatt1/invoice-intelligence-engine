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
