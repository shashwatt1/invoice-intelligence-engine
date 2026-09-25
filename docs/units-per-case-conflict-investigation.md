# Cross-store Units/Case Conflicts — Read-only Investigation

**Nothing was changed.** No case mapping, product identity, identifier, invoice, EDI output or normalization rule was modified; the database session was opened read-only. This records what the evidence supports so a person can decide.

## Evidence table

| | `01820096721` | `08769200057` |
| --- | --- | --- |
| Product | MICHELOB ULTRA C-18 12OZ | TWISTED TEA HALF & HALF C-18 12OZ |
| Reconstructed UPC-12 | `018200967214` | `087692000570` |
| Stores and Units/Case | unresolved:51e39a69 (unresolved): **1**<br>unresolved:a07b83b2 (unresolved): **1**<br>Apple Foods II (confirmed): **18** | unresolved:a07b83b2 (unresolved): **1**<br>Apple Foods II (confirmed): **18** |
| Existing Units/Case values | 1, 18 | 1, 18 |
| Reference evidence | Sheet1 r60: items/case column = 1.0 | Monarch Package r190: unit-cost formula L190 divides by 1<br>Monarch Package r190: unit-cost formula O190 divides by 1 |
| Supported by reference | 1 | 1 |
| Contradicted by reference | 18 | 18 |
| Affected invoices | 101497, 228245, 450033 | 101497, 450033 |
| Identity confirmed by | app identifier record, invoice SKU | invoice SKU |
| Difference level | store/commercial-data-level (the product identity is not in question) | store/commercial-data-level (the product identity is not in question) |
| Verdict | **INCORRECT_EXISTING_MAPPING** | **INCORRECT_EXISTING_MAPPING** |

## What the evidence says

Both item codes denote **one canonical product each**, not two. The 11-digit code in the case mappings and the 12-digit SKU on the invoices are the same product: the application already stores both forms under a single `item_code` in `product_identifier`, and the invoice lines carry the 12-digit form.

The disagreement is therefore **not identity-level**. It is a disagreement about what *units per case* counts — sellable units, or individual cans.

The distributor sheets answer that directly:

- **01820096721** — Sheet1 r60: items/case column = 1.0
- **08769200057** — Monarch Package r190: unit-cost formula L190 divides by 1; Monarch Package r190: unit-cost formula O190 divides by 1

A case whose unit cost equals its case price contains one sellable unit. That is what `items/case = 1` and a `/1` divisor both state, and it is consistent with the reference cost matching the invoice line price. The `C-18` in the description describes the pack configuration — eighteen cans — not the number of sellable units the case breaks into.

This matters because the case and the can are **different products with different barcodes**. For Michelob Ultra the application already records both: `retail_upc_raw` for the 18-pack and `unit_upc` for the single can. A units-per-case of 18 attached to the case barcode is only meaningful as "eighteen of the *unit* barcode" — a relationship between two identities, not a number on one.

## Recommended data-model treatment — not applied

1. **Do not merge or split any identity.** Both products are single canonical identities and the existing records are correct on that point.
2. **Treat the `18` mappings as candidates for correction, through the existing proposal workflow** — not by direct edit. The proposal history shows the `18` values came from `suggestion_source: "pack_size"`, i.e. parsed from the `C-18` string with `reference_avg_cost: null`, while the `1` values were entered where reference cost data was present.
3. **Make the unit of account explicit.** `units_per_case` is ambiguous on its own; the same number means different things depending on whether the unit is the pack or the can. Expressing pack composition as a relation between the retail barcode and the unit barcode would make `18` and `1` unambiguous instead of contradictory.
4. **Keep the per-store mapping shape.** Nothing here argues against store-specific units-per-case; the conflict is not a legitimate store difference, but the model that allows one is still the right model.
5. **Resolve the two placeholder stores.** Mappings are currently split across `unresolved` store records, which fragments the evidence and makes a single product look like it disagrees with itself.

## Limitation found in the bridge analysis

`08769200057` is reported as `NO_BRIDGE_EVIDENCE` by `scripts/analyze_identity_bridge.py`, yet its counterpart is present in the corpus. The distributor row stores the UPC in Excel's float form (`8.769200057E10`), which normalizes to an 11-digit value and therefore lands in the `id11` namespace rather than `upc12`. The bridge only looks for reconstructions that land on an existing 12-digit UPC, so a link between the two 11-digit populations — one missing its leading zero, the other its check digit, both resolving to the same UPC — is invisible to it.

Both reconstructions converge here:

```
08769200057    + check digit  -> 087692000570   (Item Sales scan code)
8.769200057E10 -> 87692000570
               prepend zero   -> 087692000570   (Monarch distributor row)
invoice SKU                      087692000570   (invoices 101497, 450033)
```

That convergence is strong evidence for the reconstruction rules, and it is the case the current bridge cannot see. Reported only — the bridge logic was not changed.

