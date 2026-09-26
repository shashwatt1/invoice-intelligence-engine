# Product Master — Canonical Description Policy

**Status: policy derived from the corpus, implemented, and deliberately narrow.** The
headline finding is uncomfortable and load-bearing: **this corpus contains no
product-master description source at meaningful coverage.** The policy below says so
explicitly rather than manufacturing canonical wording to fill the gap.

---

## 1. The constraint the format imposes

The PDI B-record description field is **25 characters**, left-justified and space-padded
(`_pdi_description`, `PDI_DESCRIPTION_WIDTH`). Anything longer is truncated on the way out.

The second fact matters more: **PDI product-matches on the UPC**, not on the description
(documented in `export_service.py`'s B-record map). The description we emit is what PDI
*displays*; it is not an identity key. A canonical description therefore improves
legibility — it does not improve matching, and getting it wrong does not mis-identify a
product the way a wrong multiplier corrupts Case Retail.

## 2. What the corpus actually contains

| Source | Rows | Avg length | Over 25 chars | Shape |
| --- | --- | --- | --- | --- |
| Item Sales Summary (`data`) | 9,277 | 19.0 | 1,974 | POS shorthand — `Mich ultra 18cans`, `Bud lt 16oz 6 cans` |
| Monarch Package/Frontline/… | 1,655 | 34.0–37.5 | 1,443 | product name; pack lives in a *separate* column |
| Zink - Tiki | 607 | 27.9 | 340 | brand and product name columns, frequently identical → `MICHELOB ULTRA MICHELOB ULTRA` |
| Beer Inventory `Sheet1/2/3` | 98 | ~25 | 42 | **brand + explicit package notation** — `MICHELOB ULTRA 18/12 CAN` |

Best available source **per product**, across all 9,914:

| Best source available | Products | Share |
| --- | --- | --- |
| Item Sales only | 7,700 | 77.7% |
| Monarch | 1,401 | 14.1% |
| Zink | 539 | 5.4% |
| Distributor product sheet | 97 | **1.0%** |
| No description at all | 177 | 1.8% |

**1,079 products have a single source that disagrees with itself.**

## 3. Why the obvious hierarchies fail

A ranked hierarchy (`PDI description → distributor master → reviewed source → alias`) reads
well but does not survive the data:

- There is **no PDI description source** in the corpus. PDI holds its own product master; we
  never received an export of it.
- The only source shaped like a product-master description — brand plus explicit package
  notation, uppercase, roughly the field width — is the Beer Inventory product sheet, and it
  covers **1.0%** of products.
- Item Sales, at 77.7% coverage, is transaction shorthand: abbreviated (`Mich ultra`),
  inconsistently cased (`Bud lt 16oz 6 cans`), and written for a cashier's screen.
- Monarch names average 34–37 characters and keep pack size in a different column, so using
  them means **lossy truncation of the identifying part**.
- Zink's brand and product-name columns are frequently the same value, producing degenerate
  doubled text.

Choosing any of these to raise coverage would be choosing by availability, which is the same
error as choosing by frequency.

## 4. The policy

### Tier 1 — sanctioned canonical source

A description becomes `CANONICAL` only when **all** of the following hold:

1. it comes from a **distributor product sheet** (`Beer Inventory` `Sheet1`, `Sheet2`,
   `Sheet3`) — a purpose-built product reference, not a transaction record and not a price
   feed with pack size held elsewhere;
2. that source states **exactly one** description for the product (no self-disagreement);
3. the description **fits the 25-character PDI field without truncation**.

Every other source is an alias. Nothing is promoted to canonical by frequency, length,
recency, order of appearance, or invoice wording.

**Coverage: 56 of 9,914 products (0.6%).** That is the honest ceiling this corpus supports.

### Explicitly not sanctioned

| Rejected rule | Why |
| --- | --- |
| most frequent | availability is not authority; Item Sales dominates by volume |
| longest | Monarch names are longest and lose pack size to truncation |
| latest / first | source order carries no product-master meaning |
| invoice description | a vendor's wording for one transaction, not a master fact |
| truncate a Tier-1 source to fit | the package notation is the part that gets cut |

### Field-level rules

- **Abbreviations are preserved exactly as the source wrote them.** `18/12 CAN`, `1/2 BBL`
  and `4/6` are the distributor's own notation and carry pack meaning; expanding or
  normalising them would invent information.
- **Package notation is kept** and is the reason a Tier-1 description is preferred over a
  longer product name that drops it.
- **Capitalisation is preserved** from the source, which is already upper-case in Tier 1. No
  title-casing — it would alter tokens like `NRLN` and `BBL`.
- **Punctuation is preserved.** `/` and `-` are part of pack notation.
- **Brand naming is not normalised.** `BUD LIGHT` and `BUDWEISER` stay as written; a brand
  table would be an invention this corpus does not support.
- **Historical aliases are never removed.** Every observed description stays as a `SOURCE`
  row, which is what keeps invoice wording searchable.
- **Reviewer override outranks Tier 1.** A description set by a person is authoritative over
  a derived one and is recorded with that attribution.

### Conflicts

If the Tier-1 source disagrees with itself, no canonical description is set and the product
is reported for review. One product is in this state today.

### Fallback

There is none, by design. A product without a Tier-1 description **keeps no canonical
description**, and the EDI path continues to emit the invoice line description exactly as it
does today. That is the current production behaviour, and this policy does not change it.

## 5. Consequence for cutover

Canonical-description coverage is **0.6%**, and no defensible rule in this corpus raises it.
Cutover therefore cannot depend on the Product Master supplying descriptions. Either the
shadow EDI keeps using invoice descriptions — which makes description differences a
non-issue — or a genuine product-master description source (a PDI Product Master export, or
operator-entered wording) has to be obtained first. That is a data-acquisition question, not
an engineering one, and it is recorded as a cutover blocker rather than solved by guessing.

## 6. Display names in the review workflow

A reviewer needs to recognise a product without decoding its UPC. The review queue, the
evidence panel and the human review package therefore show a **display name**, decided by
one function (`app/services/product_master/display_name.py`) so all three agree.

A display name is a **label for reading, never an identity**. It is not read by identity
resolution, deduplication, identifier matching, the commercial decision or the seed, and it
is never written back. Nothing in §4 changes: a source wording shown here is still an alias,
not a canonical description.

### Rules, in order

| Basis | When | What is shown |
| --- | --- | --- |
| `CANONICAL` | the product has a canonical description (§4 Tier 1) | that description |
| `SOURCE` | no canonical description, and the compared source wordings are effectively **one wording** | that wording, labelled source-derived, with its source row |
| `AMBIGUOUS_SOURCE` | the compared source wordings **differ materially** | "Multiple source names", with **every** distinct wording and the rows it came from; none is chosen |
| `UNAVAILABLE` | no description at all | "Product name unavailable" |

### Which source wordings are compared

Only what this policy already states is used:

1. **Distributor product sheets** (`Sheet1`–`Sheet3`), if the product has any — §4 names them
   the only purpose-built product reference.
2. Otherwise **all other distributor price-sheet wordings together** (Monarch, Zink and any
   other sheet). **No precedence among them is assumed** — this policy does not rank Monarch
   against Zink, so a disagreement between them is shown, not resolved.
3. **Item Sales POS descriptions** only when nothing else describes the product — §3 calls
   them transaction shorthand.

### When two wordings are the same wording

Only when they differ in **formatting**: letter case, spacing, punctuation other than the
`/` and `.` that carry pack and size notation (`18/12`, `19.2OZ`), or Zink's doubled
brand-and-name (`MICHELOB ULTRA MICHELOB ULTRA`, see §2). **Any difference in a word or a
number is material.** Flavour (`OBERON ALE` / `OBERON ECLIPSE RASPBERRY`), size (`11.2OZ` /
`12OZ`), pack (`C12` / `C24`, `B12` / `B15`), variety pack (`BRUNCH` / `POOL PARTY`) and an
added word (`FOEDER FIEND` / `FOEDER FIEND MANGO`) all mean the wordings may describe
different products, so no word is treated as harmless. Where one wording is shown, the
spelling displayed is chosen deterministically among its formatting-equivalent spellings;
that choice is cosmetic.

### What this found in the pilot

Of the 414 commercial candidates: 56 `CANONICAL`, 324 `SOURCE`, **34 `AMBIGUOUS_SOURCE`**,
0 `UNAVAILABLE`. The 34 are one UPC carrying different flavours, sizes, packs or variety
packs in the price sheets — consistent with breweries reusing a UPC for rotating or seasonal
slots, but not proven to be that. They include both commercial `CONFLICT` candidates, whose
disputed `C12` / `C24` pack sizes appear in their names. Which product each of these UPCs is
remains a human question; the display name deliberately does not answer it.
