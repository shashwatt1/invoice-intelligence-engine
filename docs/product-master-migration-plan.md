# Product Master — Migration Plan

**Status: PLAN ONLY. Nothing has been migrated.** The Product Master tables created by
migration `0022` are empty and unreferenced: no invoice path, mapping queue, proposal
workflow or EDI writer reads or writes them. The legacy tables remain authoritative and
untouched.

This document says how each existing structure would eventually map, and — more usefully —
which parts must not be moved automatically.

---

## Target tables

| Table | Holds |
| --- | --- |
| `master_products` | canonical identity, independent of store, invoice, cost and wording |
| `master_product_identifiers` | every raw source form, with the derivation that canonicalised it |
| `master_product_descriptions` | every observed description, with what it is for |
| `master_pack_compositions` | what a package physically contains |
| `master_commercial_mappings` | how one store accounts for the product in PDI |

---

## Source-by-source assessment

### `product_identity` — **REQUIRES REVIEW**

8,927 rows, keyed `(store_id, item_code)`.

Maps to `master_products` plus a `SOURCE`-role row in `master_product_descriptions`. Two
problems prevent a mechanical copy:

1. It is **store-scoped**, but canonical identity is not. The same `item_code` under three
   stores is one product, and collapsing them is a merge decision — 7,655 distinct item
   codes across 8,927 rows means roughly 1,272 rows are repeat identities awaiting a merge
   that a person should sanction.
2. Its `item_code` is an 11-digit PDI code. Promoting it to a canonical UPC means applying
   the check-digit derivation, which is exactly the inference this phase refuses to make
   silently. Each promotion should be recorded as a `PDI_ITEM_CODE` identifier with
   `derivation = APPEND_UPC_CHECK_DIGIT`, leaving the canonical UPC visible as derived
   rather than asserted.

### `product_identifier` — **SAFE TO MIGRATE (structurally)**

12,176 rows. This is the closest existing analogue to `master_product_identifiers` and the
mapping is nearly one-to-one:

| Legacy column | Target column |
| --- | --- |
| `value` | `raw_value` |
| `item_code` | resolved via the product's identifier set |
| `kind` | `identifier_type` (`retail_upc_raw` → `UPC_A`/`PDI_ITEM_CODE` by length and source) |
| `distributor` | `source_distributor` |
| `source_file` / `source_sheet` / `source_row` | same names |
| `imported_at` | `observed_at` |

One caveat that makes it *structurally* rather than *fully* safe: 1,416 rows hold raw Excel
scientific notation (`8.769200057E10`). Those must be run through the source-aware
derivation on import so `normalized_value` is populated — copying `value` into both columns
would bake the unnormalised form in as canonical.

### `StoreProductReference` — **REQUIRES REVIEW**

Store-scoped reference data. Its identity columns map to `master_products` +
`master_product_identifiers`; its commercial columns map to `master_commercial_mappings`.
The split has to be decided field by field, because this is precisely the table where
identity and commercial facts were previously mixed.

### `product_case_mappings` — **DO NOT MIGRATE AUTOMATICALLY**

132 rows, 96 distinct item codes. This is the highest-risk migration in the system.

`units_per_case` becomes `master_commercial_mappings.units_accounted_for`, **but the value
alone is not enough**: the new model also requires `commercial_unit_basis`, which says
whether Item Retail prices the case or the contained unit. That fact was never recorded, so
it cannot be copied — it has to be established per row.

The investigation already found two rows where the existing value is contradicted by
distributor evidence (`01820096721` and `08769200057`, governed as 18 where the distributor
states 1) and two item codes carrying **different pack sizes in different stores**. A blind
copy would import those contradictions as though they were confirmed.

Recommended: migrate with `commercial_unit_basis = UNKNOWN` and
`approval_state = REVIEW_REQUIRED` for every row, and let the existing proposal workflow
confirm each one. Nothing about the legacy table changes; the EDI writer keeps reading it
until a later phase switches the source.

### `product_data_proposals` — **REQUIRES REVIEW (as evidence, not state)**

159 rows. These are the audit trail that explains *why* a mapping holds its value, and the
research showed how much it matters: the contested `18` values came from
`suggestion_source: "pack_size"` with `reference_avg_cost: null`, while the `1` values were
entered where reference cost corroborated them.

They should land in the `evidence` JSONB of the commercial mapping they justify, not become
master rows. Approved proposals do **not** license an automatic write — an approval was
given against the old single-column model and does not carry an opinion about
`commercial_unit_basis`.

### `invoice_items` — **DO NOT MIGRATE**

297 rows. Invoice lines are transactional evidence, not master data. Their descriptions
belong in `master_product_descriptions` with `role = INVOICE`, which is what makes it
possible for EDI to eventually emit the canonical description while invoice wording stays
searchable. The lines themselves stay exactly where they are.

`product_sku` is useful as an identifier observation: invoices carry the **12-digit** form
(`018200967214`) while mappings carry the 11-digit form, and that pairing is independent
corroboration of the check-digit derivation.

### Existing evidence / history — **DO NOT MIGRATE**

Processing logs, correction history and proposal review notes stay in place. The new model
references them; it does not absorb them.

---

## Summary

| Source | Verdict |
| --- | --- |
| `product_identifier` | **SAFE TO MIGRATE** (structurally; must re-derive `normalized_value`) |
| `product_identity` | **REQUIRES REVIEW** (store-scoped → canonical merge decisions) |
| `StoreProductReference` | **REQUIRES REVIEW** (field-by-field identity/commercial split) |
| `product_case_mappings` | **DO NOT MIGRATE AUTOMATICALLY** (missing `commercial_unit_basis`; known contradictions) |
| `product_data_proposals` | **REQUIRES REVIEW** (import as evidence, never as approved state) |
| `invoice_items` | **DO NOT MIGRATE** (transactional; contribute descriptions only) |
| logs / correction history | **DO NOT MIGRATE** (reference in place) |

---

## Blocking prerequisites

1. **Store resolution.** The reference workbooks carry store *numbers* (`47708760`,
   `86357232`) while `master_commercial_mappings.store_id` is a FK to `stores.id`. The
   preview deliberately leaves `store_id_resolved = false` on all 491 commercial candidates
   rather than guessing the association. Nothing commercial can be seeded until this
   mapping exists.
2. **Two unresolved store records.** Two `stores` rows have `identity_status = unresolved`
   and no display name, and they already hold case mappings. Migrating while they are
   unresolved would fragment one product's commercial configuration across placeholder
   stores.
3. **Canonical description policy.** `master_products.canonical_description` is deliberately
   left NULL. No rule for choosing one has been sanctioned, and the research explicitly
   rejected longest/newest/most-frequent.

## Not in scope

No cutover of the EDI writer, no change to `normalize_item_code()`, no resolution of the
two known units-per-case conflicts, and no implementation of the unproven 28476 derivation.
