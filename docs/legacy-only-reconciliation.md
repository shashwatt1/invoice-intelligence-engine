# LEGACY_ONLY Reconciliation

**Status: READ-ONLY. No legacy mapping was modified or deleted.** These are the case mappings the live EDI path depends on that the Product Master does not yet cover. The purpose is to make sure none is lost at cutover.

Records: **92 legacy rows** covering **70 distinct item codes** — the figure the legacy reconciliation reports as `LEGACY_ONLY`. A case mapping exists per store, so one item code can appear more than once. Every row is listed in the CSV.

## Classification

| Class | Count | Meaning |
| --- | --- | --- |
| `BRIDGEABLE` | 82 | the Product Master already holds this identifier; commercial evidence for the store is what is missing |
| `NEEDS_COMMERCIAL_REVIEW` | 0 | product and store mapping both exist, but the mapping is unapproved |
| `NEEDS_IDENTITY_REVIEW` | 10 | no master identity holds this item code under any recorded source form |
| `NEEDS_SOURCE_EVIDENCE` | 0 | identity exists but carries no canonical barcode |
| `RETAIN_LEGACY_ONLY` | 0 | keep as legacy only |

## What was deliberately not done

No bridge was inferred from description, cost, pack notation, leading-zero padding or check-digit manipulation. A row is `BRIDGEABLE` only where the Product Master already records the same identifier, by the identity evidence captured at seed time.

No legacy mapping was modified, deleted or migrated. Until each of these is either bridged or explicitly retained, **switching EDI authority would remove mappings the current export depends on** — which is why this is a cutover blocker rather than a cleanup task.
