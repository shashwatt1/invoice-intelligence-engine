# Product Master — Clean Pilot Baseline

**Status: PLANNED, NOT SEEDED.** The pilot is at Alembic `0024` with every Product
Master table empty. This records what the pilot seed is expected to produce there,
why that differs from the local database, and the one precondition still open.

The expectations below come from `scripts/pilot_seed.py --dry-run` (the in-memory
plan in `scripts/pilot_seed_plan.py`) run over the full reference corpus against an
empty Product Master.

## Expected result on the clean pilot

| Table | Rows |
| --- | --- |
| master_products | 9,914 |
| master_product_identifiers | 12,208 |
| master_product_descriptions | 11,693 (11,637 source/invoice + 56 canonical) |
| master_pack_compositions | 259 |
| master_commercial_mappings | **414** — once the store precondition below is met; **0** until then |
| master_commercial_reviews | 0 |

Commercial mappings, by derived review status:

| Status | Count |
| --- | --- |
| READY_FOR_REVIEW | **412** |
| CONFLICT | **2** (both source-internal) |
| PENDING / APPROVED / REJECTED | 0 |
| `approval_state` | 414 × `REVIEW_REQUIRED` |
| `source_snapshot` | 414 of 414 |

Not seeded, by design: 383 identity candidates with no trustworthy identifier,
10 pack compositions with an end that is not a seedable product, 15 product/store
pairs with no master product.

## 412 / 2 is the pilot baseline; 410 / 4 was the local legacy-data result

The local database produced 410 READY / 4 CONFLICT. Two of those four conflicts
were caused by the **98 legacy `product_case_mappings` rows** in the local
database, not by the reference data:

| PDI item | Reference data | Local governed mapping | Local | Clean pilot |
| --- | --- | --- | --- | --- |
| `01820096721` (MICHELOB ULTRA C-18) | 1 | 1 and 18 | CONFLICT | READY, multiplier 1 |
| `08769200057` | 1 | 1 and 18 | CONFLICT | READY, multiplier 1 |
| `65268201221` | 12 and 24 | — | CONFLICT | **CONFLICT** |
| `86502400049` | 12 and 24 | — | CONFLICT | **CONFLICT** |

This is the Product Master design working as written, not a discrepancy:

- `app/services/product_master/commercial.py` admits a governed mapping **only to
  dissent**. It is read from the database being seeded. Where one exists and
  disagrees, the value is withheld; where none exists there is nothing to dissent
  with, and the distributor statement stands on its own.
- The pilot deliberately starts with **0** `product_case_mappings`. Importing or
  recreating the 98 local rows to reproduce 410 / 4 would copy legacy EDI authority
  into the pilot to manufacture a conflict, and it is not done.
- Legacy agreement is not frozen into the Product Master. The review workbench
  recomputes it live (`legacy_agreement()` in
  `app/services/master_commercial_review_service.py`), so on the pilot these two
  candidates show `NO_LEGACY_MAPPING`, which is true there.
- The two **source-internal** conflicts (distributors state 12 and 24) come from
  the reference data. They are CONFLICT in every environment and stay that way.

The dry-run report states the split explicitly, as
`commercial.conflict_origin = {source_internal, legacy_dissent}`. On the clean
pilot it is `{2, 0}`. A non-zero `legacy_dissent` means the target has governed
mappings.

**For reviewers:** the two Michelob / `08769200057` candidates will arrive as READY
with a multiplier of 1. The local environment's governed history held 18 for them
too; see `docs/product-master-commercial-conflicts.md`. That is context for the
human decision, not a reason to seed them as conflicts.

## Open precondition — store code `47708760` has no pilot store

All 491 commercial candidates carry the Item Sales source store code `47708760`.
Until a pilot `store_identifiers` row
`(item_sales, store_code, 47708760)` exists, the store lookup finds nothing, and
the seed would create **0** commercial mappings. The dry run reports this as a
blocker and exits non-zero.

**The owner of that code is not one of the two pilot stores.**

- Locally, `47708760` belongs to its own store, `51e39a69-11b3-4023-afc3-9ca7f8664de0`
  (identity `unresolved`). Migration `0013` created it from the `store_number`
  values it found in the data; all 414 local candidates are attached to it.
- The pilot was migrated from empty, so `0013` found no `store_number` values and
  created no code stores. The pilot holds only:
  - **Apple Foods II** (`cea91748-…`). It was created by `0013` with the note
    *"NOT linked to any Item Sales store code — whether this is the location behind
    47708760 (or another) is a human decision that has not been made."*
  - **RCM** (`c3c31b86-…`). It was created by `0018` with the same statement.
- `docs/store-resolution-phase-2a.md` measured and rejected product overlap as
  evidence, and records that *"Neither 47708760 → Apple Foods II nor any other
  pairing is supported by admissible evidence."* Overlap even favours `86357232`
  for Apple Foods II (90% against 77%).

Attaching `47708760` to either existing pilot store would therefore be a physical
store identification. The architecture reserves that for a person. Resolving the
precondition is a decision:

1. **Mirror the local architecture.** Create one new store on the pilot with
   identity `unresolved` and no name, carrying only
   `(item_sales, store_code, 47708760)`, exactly as `0013` does for a code it finds.
   No existing store is renamed, merged or confirmed, and no invoice or document
   moves. This is a new store row, not an identifier on an existing store.
2. **A person confirms** that `47708760` is Apple Foods II (or RCM), with evidence
   recorded. Only then is the identifier added to that store.

**Option 1 was chosen.** It is implemented as `scripts/pilot_store_precondition.py`, an
explicit step run before the seed and kept separate from it:

- **Target check:** the same `pilot_seed_guard` check as the seed (the database name,
  a Supabase host, Alembic `0024`).
- **What it writes:** one store and one identifier, in a single transaction. The
  result is verified before commit.
- **Idempotent:** if the identifier already names an identical bare store, it
  reports "already satisfied".
- **Refusals:** it refuses if the code names any other store. `--dry-run` prints
  the exact rows and the evidence, and writes nothing.
