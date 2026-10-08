# Product Master Architecture — Addendum: Identity, Packaging and Commercial Units

**Status: RESEARCH ONLY. Nothing was changed.** No mapping, product identity, identifier,
invoice, EDI output, extraction path, database schema or normalization rule was modified.
Every database read in this work used a read-only session. The target model below is a
proposal for review, not a migration.

This addendum extends the master-data research with a requirement the Units/Case
investigation surfaced: **`units_per_case` is not an intrinsic property of a product.** It is
the answer to a question that only makes sense once you know *which* product an identifier
denotes, and *whose* commercial system is asking.

---

## 1. The finding that forces the change

`_pdi_units_per_case` in `app/services/export_service.py` documents what the field does,
confirmed against live PDI:

> whatever lands here is displayed verbatim as "Units Per Case", and PDI computes
> **Case Retail = Item Retail × this value**.

So the EDI field is a **commercial multiplier between two price points**, not a count of what
is physically inside a box. Its correct value depends entirely on what the PDI item's *Item
Retail* refers to:

| If the PDI item is… | Item Retail is the price of… | Correct units_per_case |
| --- | --- | --- |
| the 18-pack (case barcode) | one 18-pack | **1** |
| the single can (unit barcode) | one can | **18** |

Both answers are correct. They answer different questions. Today the schema has one column,
so the two answers collide — and that collision is exactly what the cross-store conflict was.

---

## 2. The two worked examples

### `01820096721` — MICHELOB ULTRA C-18 12OZ

| Concept | Value | Where it lives today |
| --- | --- | --- |
| Canonical identity | the Michelob Ultra 18-pack of 12oz cans | `product_identity.item_code` |
| Case/retail identifier | `018200967214` (UPC-A) | `product_identifier.kind='retail_upc_raw'` |
| Unit/sellable identifier | `018200003349` (the single can) | `product_identifier.kind='unit_upc'` |
| Units contained in the package | **18** | nowhere — only inferable from the string `C-18` |
| Units accounted for by the PDI item | **1** | `product_case_mappings.units_per_case` |
| Distributor's own statement | Beer Inventory `Sheet1` r60: `items/case = 1.0`, cost/item = case cost = 16.70 | source workbook |

The case and the can are **two different products with two different barcodes**, and the
application already records both. `18` is not wrong as a fact about the world — it is the
answer to "how many cans are in the pack". It is wrong in the `units_per_case` column
because that column is keyed on the *case* barcode, where the answer is 1.

### `08769200057` — TWISTED TEA HALF & HALF C-18 12OZ

| Concept | Value | Where it lives today |
| --- | --- | --- |
| Canonical identity | the Twisted Tea Half & Half 18-pack | `product_identity.item_code` |
| Case/retail identifier | `087692000570` | `product_identifier` (as `08769200057` *and* `8.769200057E10`) |
| Unit/sellable identifier | **not recorded** | — |
| Units contained in the package | **18** | nowhere — only the string `C18 12OZ` |
| Units accounted for by the PDI item | **1** | `product_case_mappings.units_per_case` |
| Distributor's own statement | Monarch Package r190: unit cost formula `L190 = I190/1` | source workbook |

Monarch encodes pack size as the divisor in the unit-cost formula. For this product it
divides by **1**; for the C12 24OZ variant two rows later it divides by **12**. The
distributor is stating the commercial unit directly, in a form no column header announces.

---

## 3. The eight concepts, evaluated

| # | Concept | Separate entity? | Reasoning |
| --- | --- | --- | --- |
| 1 | **Canonical product identity** | **Yes — already exists** (`product_identity`) | The thing every other fact hangs from. Must be independent of any one source's way of naming it. |
| 2 | **Case/retail identifier** | **Yes — as a typed identifier, not a column** | One product legitimately has many: `018200967214`, `01820096721`, `01820096721 4`, `8.769200057E10` are all the same product. Already modelled as `product_identifier.kind`. |
| 3 | **Unit/sellable identifier** | **Yes — same table, different `kind`** | Already exists as `unit_upc` for 258 item codes. It is a *different product* from the case, and conflating the two is the root of the conflict. |
| 4 | **Pack/composition relationship** | **Yes — a new relationship, currently missing** | "This case contains N of that unit" is an edge between two identities (#2 → #3), not a scalar. Nowhere in the schema today; only recoverable by parsing `C-18` out of a description. |
| 5 | **PDI commercial unit** | **Yes — a property of the mapping, not the product** | Which identity the PDI item code denotes. Determines whether #8 is 1 or 18. Currently implicit and unrecorded. |
| 6 | **Store-specific commercial mapping** | **Yes — already exists** (`product_case_mappings.store_id`) | Correct as-is. Different stores genuinely may account differently. Keep. |
| 7 | **Units contained in the package** | **Yes — belongs on #4, not on the mapping** | An intrinsic, store-independent fact about the packaging (18). Stable across every store and invoice. |
| 8 | **Units sold/accounted for by the PDI item** | **Yes — this is what `units_per_case` should mean, exclusively** | A store-specific commercial decision (1). Derivable from #4 + #5, but must remain explicitly confirmable because PDI multiplies retail by it. |

**Verdict: these should not be collapsed.** Today #7 and #8 share one column, and #4 and #5 do
not exist at all. That is why a human had to choose between `1` and `18` with no way to record
that *both* were true of different things.

### The critical split

```
units_per_case  (today, one column, two meanings)
   │
   ├── #7 units contained in the package ...... 18   intrinsic, store-independent
   │      belongs to the pack/composition edge:
   │      case 018200967214 ──contains 18──▶ unit 018200003349
   │
   └── #8 units accounted for by PDI item ..... 1    commercial, store-specific
          belongs to the store mapping, and follows from
          which identity the PDI item code denotes (#5)
```

Once separated, both values are recorded, neither contradicts the other, and the EDI writer
consumes only #8 — which is what it already does, just without knowing that is what it means.

---

## 4. Identifier resolution: generic relationship, not another bridge rule

### The convergence case

```
08769200057      + check digit                    → 087692000570   (Item Sales scan code)
8.769200057E10   → 87692000570  + leading zero    → 087692000570   (Monarch distributor row)
invoice SKU                                         087692000570   (invoices 101497, 450033)
```

Two source forms, two different repairs, one product. The current bridge misses it because it
only asks "does this reconstruction land on a **literal 12-digit UPC** that already exists?" —
and here neither form is ever written as 12 digits anywhere in the corpus.

### How much is missed

| Measure | Count |
| --- | --- |
| Distinct scientific-notation 11-digit values | 743 |
| Distinct sales-scan-code 11-digit values | 8,264 |
| **Canonical UPCs both populations resolve to** | **246** |
| …of which a literal 12-digit counterpart exists (bridge finds these) | 2 |
| **…invisible to the current bridge** | **244** |
| Description corroboration across the 246 (exact shared brand word) | **227 (92%)** |

A 92% independent description-agreement rate says these are real product identities, not
arithmetic coincidences.

### Why a generic resolver, not a third rule

The bridge today encodes: *scan code → append check digit → look for literal UPC*. Adding
*scientific → prepend zero → compare against reconstructed scan codes* would be a second
special case, and a third source format would need a third. Each rule has to know about every
other rule to avoid double-resolving.

The alternative inverts it. Every source identifier resolves **independently** to a canonical
form, and identity is whatever two source identifiers share afterwards:

```
source identifier            derivation                     canonical identity
─────────────────────────────────────────────────────────────────────────────
"08769200057"       → append UPC-A check digit          ┐
"8.769200057E10"    → expand float, restore leading 0   ├──▶  087692000570
"087692000570"      → already canonical                 │
"0-87692-00057-0"   → strip separators                  ┘
```

No rule needs to know about any other. A new source format adds one derivation, not N²
comparisons. Equality in canonical space *is* the bridge — there is no bridge step left to
maintain.

### This is not a new idea in this codebase — it is half-built already

`product_identifier` stores `(item_code, kind, value, distributor)` where `value` is the **raw
source form** and `item_code` is the **canonical key**. That is precisely the shape above. The
evidence that it is already working this way:

| Stored raw `value` form | Rows | Canonical `item_code` derivation |
| --- | --- | --- |
| Scientific, 11-digit expansion (leading zero lost) | 743 | correct — matches the item code's own reconstruction |
| Scientific, 12-digit expansion (nothing lost) | 670 | correct |
| Scientific, 13-digit (EAN-13) expansion | 3 | **see caveat below** |

1,416 of 9,812 barcode identifier rows are stored in raw Excel scientific notation, and the
canonical `item_code` was nonetheless derived correctly for 1,413 of them. The relationship
exists; what is missing is that it is **implicit**. There is no record of *which* derivation
was applied, how confident it was, or what to do when two source identifiers disagree about
which canonical identity they belong to.

**Recommendation: formalise the existing `product_identifier` relationship rather than adding
a bridge rule.** Give each row an explicit derivation (what transformation produced the
canonical key), and the resolver becomes inspectable, testable and extensible — and the
244 currently-invisible convergences resolve as a side effect rather than as a feature.

---

## 5. Caveats and open questions

**A real caveat on the current normalizer.** `normalize_item_code()` drops the check digit
only when the input is exactly 12 digits, then truncates to 11. A 13-digit EAN
(`4101010013663`) is neither 12 digits nor already canonical, so it is truncated to
`41010100136` — losing a digit rather than dropping a check digit. Three identifier rows are
in this state. This is a latent correctness question for non-UPC-A barcodes, not a current
production failure, and it is **not** something this research changed.

**What this establishes.** That `units_per_case` carries two distinct meanings; that the case
and unit barcodes are separate identities the schema already distinguishes for 258 products;
that the PDI field is a commercial multiplier; and that a canonical-resolution relationship
would subsume the bridge entirely.

**What it does not establish.** Which value any *specific* store should use. That remains a
commercial decision per store, and the existing proposal workflow is the right place for it —
this model would only make the two candidate answers expressible instead of contradictory.

**Open questions for a person.**

1. Is the PDI item code always the case/retail barcode, or do some stores key PDI on the unit
   barcode? The model must support both; the evidence here only covers the former.
2. Should pack composition be single-level (case → unit) or recursive (pallet → case → 6-pack
   → can)? Monarch descriptors like `C24 12OZ 6P` suggest at least three levels exist in the
   source data.
3. Should a confirmed pack composition be allowed to *suggest* #8 automatically, or must every
   commercial mapping stay human-confirmed? The EDI risk argues for the latter — a wrong value
   silently corrupts Case Retail in PDI.

**Not done here.** No schema, no migration, no normalization change, no mapping edits, no
bridge modification. The two `18` mappings identified in the conflict investigation remain
exactly as they are.

---

## Decision — 2026-10-08: global commercial mappings, store overrides

Adopted as a transitional business rule. APPROVED commercial mappings derived from
distributor/product evidence are **global**: they apply to every physical store. A physical
store's own Product Master mapping is an explicit **store-specific override**. Where an
override and the global mapping disagree on basis or units, resolution is `CONFLICT` and
EDI is blocked — neither is chosen silently.

The approved mappings are held under the Item Sales location `47708760` because that is
where their evidence was imported from. That location is provenance only: it is not a
vendor, not a physical store, and not itself the global scope. The rows are not moved and
no schema changes; `commercial_resolution.py` reads the mappings held under the configured
location as the global set. Rows 6 and 8 of the table above ("store-specific") remain
possible as overrides; the evidence recorded so far found no genuine store difference.
