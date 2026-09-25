# Product Master Phase 2A — Store Resolution: Research and Design

**Status: RESEARCH AND DESIGN ONLY. Nothing was changed.** No store, mapping, identity,
identifier, invoice or proposal was modified; no Product Master row was created; the five
Phase 1 tables remain empty. Every database read used a read-only session.

---

## 1. The mechanism already exists

`store_identifiers` is already the source-code → store relation this phase was going to
design, and `app/models/store.py` already states the principle in its own words:

> The numbers the reference exports carry ("Store: 47708760") are what ONE source system
> calls that location; invoices carry other numbers again. **None of those is the store.**
> They are identifiers OF the store, kept in `store_identifiers` and resolved to `Store.id`.

It is populated. 15 rows across 4 stores:

| source_system | type | value | resolves to | identity |
| --- | --- | --- | --- | --- |
| `item_sales` | `store_code` | `47708760` | `51e39a69` | **unresolved** |
| `item_sales` | `store_code` | `86357232` | `a07b83b2` | **unresolved** |
| `document` | customer_name / address_line / postal_code | 7 values | Apple Foods II | confirmed |
| `document` | customer_name / address_line / postal_code | 6 values | RCM | confirmed |

A `UniqueConstraint(source_system, identifier_type, identifier_value)` already guarantees
that within one source system a value names at most one store.

### What this changes about the Phase 1 finding

The Phase 1 preview reported `store_id_resolved = false` on all 491 commercial candidates.
That was **not** because no mapping exists — it was because the preview never consulted
`store_identifiers`. Both workbook store codes resolve deterministically, in one hop, to a
real `Store.id` today.

The genuine gap is narrower and different: **the store those codes resolve to has no
confirmed physical identity.** That is a separate question from resolution, and conflating
the two is what this phase has to prevent.

---

## 2. The trap this phase exists to avoid

The most available "evidence" for resolving these codes is the worst evidence available.

Invoices 101497 and 1000540 are currently assigned to `a07b83b2` (code 86357232). Their
`STORE_IDENTIFICATION` logs show what actually happened:

- the matcher offered **Apple Foods II**, on document evidence — address `800 WOLF ST`,
  customer `PB WOLF GROUP INC`;
- the operator assigned a *different* store, and `was_a_candidate: false`;
- the recorded justification is explicit:

  > `"data-team:shashwat (G1 test — reference-store assignment, not a physical identity declaration)"`
  > `"data-team:shashwat (P0 test — reference-store assignment, not a physical identity declaration)"`

So the invoice→store links on the unresolved stores were deliberately made as *reference-data
attachments* and were labelled, at the time, as **not** identity claims.

Any resolution rule of the form "a store code means the store its invoices belong to" would
read those test assignments back as though they were identity evidence — and would return
`86357232 → Apple Foods II` with false confidence, having laundered an explicitly
non-identity-bearing assignment into an identity. **This rule must not be built.**

---

## 3. Evidence inventory, with measured strength

| Evidence | What it establishes | Strength |
| --- | --- | --- |
| `document` identifiers (address, customer name, postal code) | that a **document** belongs to a physical store | **Strong** — human-verified, `verified_by` recorded |
| `item_sales` `store_code` | that a **reference export** belongs to a source-system store | **Strong for scope, silent on identity** |
| Workbook directory ↔ invoice vendor agreement | that the directory→code mapping is sound | **Corroborating** — `store_47708760/` carries Testani price sheets and its invoice 228245 is from Rocco J. Testani |
| Invoice→store assignment on unresolved stores | nothing | **Must not be used** — explicitly labelled non-identity |
| Product catalogue overlap | nothing usable | **Measured and rejected — see below** |

### Product overlap was measured and does not work

Canonical UPC overlap between each source code's Item Sales reference set and each physical
store's invoice SKUs:

| Physical store | vs code `47708760` | vs code `86357232` | invoice SKUs |
| --- | --- | --- | --- |
| Apple Foods II | 23 (77%) | **27 (90%)** | 30 |
| RCM | 49 (35%) | 35 (25%) | 139 |
| unresolved `51e39a69` | **42 (93%)** | 33 (73%) | 45 |
| unresolved `a07b83b2` | 27 (79%) | **31 (91%)** | 34 |

Each unresolved store overlaps its own code most (93%, 91%), which independently confirms
the directory→code mapping is sound. But **Apple Foods II overlaps code 86357232 at 90%,
against that code's own store at 91%** — a one-point difference. Two convenience stores in
the same market buying from the same distributors stock the same things, so catalogue
overlap cannot separate them. It discriminates only geography (RCM, in Utah, sits at 25-35%),
which is the case that never needed help.

A resolver using overlap would be confidently wrong on exactly the pair that matters.

---

## 4. Design

### 4.1 Two relations, kept apart

```
source store code ──(A)──▶ Store record ──(B)──▶ physical identity
  "47708760"               51e39a69              name, address, confirmed
```

**(A) is resolution.** It already exists, it is deterministic, and it is a lookup — not an
inference. `store_identifiers(source_system='item_sales', identifier_type='store_code')`.

**(B) is identification.** It is `stores.identity_status` plus confirmed evidence, and it is
only ever set by a person. It is not a prerequisite for (A).

The failure mode this separation prevents is treating (A) as though it produced (B).

### 4.2 Commercial mappings should attach at (A), not (B)

`master_commercial_mappings.store_id` should be resolved through `store_identifiers` and
attached to the `Store.id` that comes back — **including when that store is `unresolved`.**

An unresolved store record is not a missing store. It is a real, stable identity that means
precisely "the location known to the Item Sales export as 47708760, whose name nobody has
confirmed". Commercial configuration genuinely belongs to it: the reference workbook that
produced the configuration is scoped to that same code. Requiring `identity_status =
confirmed` before attaching would block seeding on a question that does not need answering
first, and would tempt exactly the shortcut in §2.

Proposed resolution outcomes for the seed, each recorded rather than assumed:

| Outcome | Meaning | Seedable |
| --- | --- | --- |
| `RESOLVED` | the code matched one `store_identifiers` row | yes |
| `UNKNOWN_SOURCE_CODE` | no identifier row for this code | no — needs a store record first |
| `AMBIGUOUS` | more than one row matched (constraint makes this unreachable today; kept so it cannot pass silently if the constraint changes) | no |

Applying this to the current data resolves **491 of 491** commercial candidates, with 0
unknown and 0 ambiguous — without asserting anything about physical identity.

### 4.3 Identification stays a human act, with evidence

If code 86357232 is ever to mean Apple Foods II, that is a **store merge**, not a lookup.
It needs:

1. evidence recorded as `store_identifiers` rows with `evidence.verified = true` and a
   `verified_by`, exactly as the 13 existing `document` identifiers already do;
2. an explicit person-made decision, since the strongest available signal (§2) is
   disqualified and the next strongest (§3) is not discriminating;
3. a recorded merge trail, because commercial mappings, case mappings and invoices already
   hang off both store records and a merge has to say what happened to each.

Nothing in Phase 2A should perform or pre-stage that merge.

### 4.4 What the preview should do

One additional read-only step, `resolve_store_context()`: take each candidate's
`store_context`, look it up in `store_identifiers`, and emit `store_id`, `store_identity_status`
and `store_resolution_outcome`. It stays read-only, it consults the existing table rather
than a new mapping file, and it never falls back to a guess — an unmatched code produces
`UNKNOWN_SOURCE_CODE`, not a nearest match.

---

## 5. Data-quality observations (reported, not fixed)

1. **Invoice 1012818** — vendor ONONDAGA BEVERAGE, a Syracuse NY distributor — is assigned
   to **RCM**, which is in St George, Utah. Its log records `outcome: "no store matched;
   awaiting manual identification"`, `candidates_offered: []` and `confirmed_by: null`. The
   assignment has no recorded basis.
2. **Invoice 3376587 appears twice** on store `51e39a69` (documents `balkan-3376587.jpg` and
   `Image from iOS.jpg`), same number, same total 273.66, one with a vendor and one without.
3. **Both unresolved stores carry case mappings** (`product_case_mappings`), so the units/case
   conflicts found earlier are split across placeholder store records. A merge would change
   which mappings collide.

---

## 6. What this establishes

**Establishes**: that resolution (A) is a solved, deterministic lookup covering 100% of
current commercial candidates; that identification (B) is unsolved for both codes; that the
most convenient evidence for (B) is disqualified by its own recorded provenance; and that
catalogue overlap is measurably unfit for (B).

**Does not establish**: what either source store code physically is. Neither
`47708760 → Apple Foods II` nor any other pairing is supported by admissible evidence in the
current data.

**Recommended next step**: implement §4.4 — the read-only `resolve_store_context()` lookup in
the seed preview — which makes all 491 commercial candidates store-resolved without touching
identity, and leaves (B) where it belongs, with a person.

---

*Note: the Phase 2A brief appears to have been cut off mid-sentence after the Store Identity
Principle. This document covers research and resolution design as stated; if the brief
continued with further parts, they are not reflected here.*
