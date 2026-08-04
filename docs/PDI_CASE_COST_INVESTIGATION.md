# PDI Case Cost — Root Cause Investigation

Investigation only. No code changed. Triggered by live behavior observed
importing a real generated EDI into a live PDI account: the file is now
structurally accepted (see the CRLF fix), and every line item's Product
Name, Department, Retail Price, Units Per Case, and Case Retail populate
correctly — except **Case Cost, which imports as \$0.00 on every line**,
producing a \$0.00 invoice total, 100% margin, and "Cost Changed"
warnings.

This document supersedes part of `docs/PDI_DATA_CONTRACT.md` §2.1. Where
it does, that's stated explicitly, with the reasoning for the reversal.

---

## 1. How PDI appears to populate Case Cost

**Answer: (A) — from the incoming EDI bytes, not from PDI's internal
Product Master.**

Reasoning, from the live evidence stated in this session:

- Product Name, Department, Retail Price, Units Per Case, and Case
  Retail all populate with real, correct values on import.
- Our EDI currently sends **zero, unconditionally**, for every
  cost-shaped byte range on every detail line (`_pdi_cost_block` /
  `_pdi_cost_tail` in `app/services/export_service.py` both
  unconditionally return `"0" * width` — read directly, not from
  memory, before writing this report).
- Therefore the five correctly-populated fields cannot be coming from
  our EDI at all — nothing in our output could produce a real retail
  price or a real department from an all-zero byte stream. They must be
  coming from a lookup PDI performs against its own Product Master,
  keyed by the item code we *do* send correctly (confirmed separately:
  UPC/item-code recognition and product matching work).
- Case Cost is the **one** field that isn't populated. It imports as
  exactly \$0.00 — not blank, not "N/A", not a stale prior value.
  \$0.00 is also exactly what our zero bytes decode to under the
  existing digit-only encoding convention used everywhere else in this
  formatter.

If Case Cost were *also* a Product Master lookup like the other four
fields, there is no mechanism by which it would independently and
uniformly come back \$0.00 for every distinct product on the invoice —
that would require every single product's Product Master cost record to
independently be exactly zero, which is not a plausible coincidence
across a multi-line invoice of different items. A direct, exact,
per-line match between "what we sent" and "what came back wrong" is far
more consistent with (A) than with (B) or (C).

This is also consistent with plain domain logic: retail price,
department, and units-per-case are stable *product* attributes that
belong in a central catalog. Cost is a *transactional* value — it's what
this specific delivery cost, and it's supposed to change between
deliveries as vendor pricing changes. An invoice-import EDI is
structurally the right and expected place for a receiving system to read
cost from, and the wrong place to read retail price from. PDI's observed
behavior matches that design, not a coincidence.

---

## 2. Which field(s) in our EDI are responsible

Confirmed detail-record layout (`app/services/export_service.py`,
`_pdi_detail_line`, read fresh for this report): 70 bytes —
`[0]`=`"B"`, `[1:12]`=item code, `[12:37]`=description, **`[37:57]`=cost
block (20 digits)**, `[57]`=sign, `[58:62]`=quantity, **`[62:70]`=cost
tail (8 digits)**.

Both cost fields are zero today. Between them, the stronger candidate
for "Case Cost" specifically is **cost tail `[62:70]`**:

- It decodes cleanly as a **5-digit price in cents, followed by a fixed
  3-digit `"001"` suffix** — confirmed across all 908 real detail lines
  in the ground-truth sample re-checked for this report, zero exceptions
  on the suffix.
- The decoded magnitudes are tightly bounded and realistic for a
  **per-case wholesale cost**: min \$0.00, max \$37.55, mean \$6.28
  across 908 lines, with 861 of 908 landing between \$1–\$200. Nothing
  decodes to an implausibly large or tiny number.
- Cost tail does **not** scale with the quantity delivered on any given
  invoice — re-confirmed in this investigation, 59 items observed at
  multiple different delivered quantities, always constant per item
  within a pricing period. This is exactly what a fixed **per-case cost**
  should do: it doesn't change because you ordered 3 cases instead of 1
  — it only changes when the vendor's price changes, which is exactly
  when we do see it change (different values line up with different
  invoice dates, never with different quantities).

Cost block `[37:57]` is a weaker candidate for Case Cost specifically:
at 20 digits it's too wide to be a plain price, and re-confirmed
decomposition shows three sub-parts — `[37:45]` a per-item constant of
unknown meaning, `[45:51]` a value that changes in step with cost tail
but with no solved formula, `[51:57]` a count matching real case-pack
sizes (1, 5, 6, 8, 9, 10, 12, 18, 24, 36, 50, 100, 500). That last
sub-field is very likely **redundant with PDI's own "Units Per Case"**,
which we've now confirmed populates correctly from Product Master
independent of anything we send — so cost block's case-pack portion
probably isn't what's blocking Case Cost. The middle sub-field
(`[45:51]`) remains a genuine unknown and could still matter; it's
flagged, not ruled out.

---

## 3. Evidence for every conclusion

| Conclusion | Evidence |
|---|---|
| PDI performs an independent Product Master lookup for retail-side fields | Real Retail Price/Department/Units Per Case/Case Retail appear on import despite our EDI sending zero/placeholder data for anything that could produce them |
| Case Cost comes from the EDI, not Product Master | Uniform, exact \$0.00 across every line, matching our literal zero bytes exactly — not plausible as independent per-product Product Master coincidence |
| Cost tail `[62:70]` is the stronger candidate field | Decodes as price(5)+suffix(3) with a 908/908-consistent suffix; realistic case-cost magnitudes; invariant to delivered quantity, consistent with "cost per case" rather than "cost for this delivery" |
| Cost block's case-pack sub-field is likely not the blocker | Matches real case-pack sizes but is probably duplicated by PDI's independently-working "Units Per Case" |
| `_pdi_cost_block`/`_pdi_cost_tail` currently emit literal zero | Read directly from `app/services/export_service.py` for this report, not recalled from memory |

---

## 4. Verifying the previous conclusion — reversed, and why

`docs/PDI_DATA_CONTRACT.md` §2.1 concluded cost tail most likely encodes
a **retail price** sourced from product-master data we can't derive from
a supplier invoice — largely on the strength of one item whose printed
description ("...2/\$2...") decoded to exactly \$2.00.

**That specific interpretation is superseded.** The new live evidence is
direct and first-party: PDI's *actual* retail price comes from somewhere
else entirely (confirmed working, independent of our EDI), while the
*actual* broken field is Case Cost, and it fails in a way that points
directly at the bytes we control. A \$2.00 "2 for \$2" match is also not
strong evidence against a cost interpretation on reflection — a \$2.00
**case cost** for a 12-count box of inexpensive bulk candy (≈17¢/piece
wholesale) is an entirely ordinary wholesale economics, so that data
point is consistent with either interpretation and shouldn't have been
weighted as strongly toward "retail" as it was.

**What still stands from that document:** the byte positions, the
5-digit-price + 3-digit-suffix decomposition of cost tail, the
non-scaling-with-quantity finding, and the case-pack read of cost
block's tail sub-field are all structural, evidence-based findings that
remain correct — and are in fact exactly what makes the new
interpretation possible. Only the semantic label ("this is retail price,
therefore unownable") is reversed; the underlying decoding work was not
wrong, it was under-interpreted.

---

## 5. Ranked fixes, by confidence

**Fix #1 — Populate cost tail `[62:70]` from `item.unit_price`, magnitude
only, unscaled by quantity, keeping the `"001"` suffix.**

Concretely: change `_pdi_cost_tail()` to emit
`round(abs(item.unit_price) * 100)` right-justified into 5 digits,
followed by the literal `"001"` suffix, instead of `"0" * 8`. This uses
data already captured by extraction — no new schema, no new prompt work,
one function changed. `_pdi_cost_block()` is left untouched (still zero).

*Why this is "smallest possible fix":* one function, no new data source,
directly targeted at the one broken field, reuses a value we already
have on hand and is not the same calculation already disproven (that was
`unit_price × quantity`; this is `unit_price` alone — the disproof was
specifically about quantity-scaling, which this fix doesn't do).

**Fix #2 — Also populate cost block `[45:51]`** with a related cost
value, once its formula is solved. Not proposed now: no confirmed
decode exists for this sub-field, and item #1 may be sufficient on its
own since Units Per Case is independently available to PDI.

**Fix #3 — Populate cost block `[51:57]` with the item's case-pack size**
(we don't currently extract this). Lowest priority: very likely
redundant with data PDI already sources from Product Master correctly.

---

## 6. Confidence if Fix #1 is implemented

**~60%.**

What raises confidence: the fix is directly targeted at the one field
live-confirmed as broken, uses data we already have, and is consistent
with every piece of ground-truth evidence re-examined for this report,
not just the live observation.

What keeps it from being higher — named, not hand-waved:

- We don't have independent confirmation that cost tail specifically
  (rather than cost block, or some combination) is what PDI reads for
  Case Cost. This is inferred from elimination and magnitude
  plausibility, not observed directly.
- The `"001"` suffix's meaning is still unconfirmed. If it's a
  significant flag PDI checks (a unit-of-measure code, a record-subtype
  marker) rather than an inert constant, reproducing it verbatim should
  work — but we don't know that for certain.
- The "unit_price on this invoice = per-case cost" assumption depends on
  this vendor's (and this PDI account's) invoicing convention matching
  the one observed on a different, unrelated set of ground-truth
  invoices. It hasn't been checked against the specific account
  currently under test.

---

## 7. What's still needed — the minimum, not generic documentation

One concrete data point would resolve most of the remaining uncertainty:
**a real, known-correct Case Cost value for one specific line item on
this PDI account**, to check our derivation against. Either of these
would supply it:

- On the "Retail Invoice" screen already used for this import, there is
  an editable **Net Cost** field next to "Add To Invoice." Manually
  enter a value for one line item, submit, and see what PDI accepts —
  this tells us directly whether the expected value is per-unit or
  per-case, and at what scale.
- Alternatively, look that same item up in PDI's own **Price Book**
  section (visible in the left nav) and read off its stored cost, if one
  exists from a prior import.

Either gives one real number to check `unit_price` (as extracted from
that same invoice) against — enough to confirm or reject Fix #1 with
much higher confidence before writing any code.
