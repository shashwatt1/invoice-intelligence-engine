# Backlog — Fix 13-digit EAN handling in `normalize_item_code()`

**Status: NOT IMPLEMENTED. Deliberately out of scope for the Product Master phase.**
Recorded here so it is not lost and not fixed by accident inside an unrelated change.

## Current behaviour

`normalize_item_code()` in `app/services/export_service.py`:

```python
digits = _NON_DIGITS.sub("", product_code)
if len(digits) == 12:
    digits = digits[:-1]          # drop the UPC-A check digit
return digits[:PDI_ITEM_CODE_WIDTH]   # truncate to 11
```

The check digit is dropped **only** when the input is exactly 12 digits. A 13-digit EAN is
not 12, so nothing is dropped — and the final truncation to 11 characters then removes the
last two digits positionally.

```
4101010013663   (EAN-13)
  → no check-digit branch (length 13, not 12)
  → digits[:11]
  → 41010100136   two digits lost, not one check digit dropped
```

For a UPC-A the truncation is semantically correct: the removed digit *is* the check digit.
For an EAN-13 it is a positional cut that discards a significant digit, so two different
EAN-13s can normalise to the same item code.

## Affected rows found by research

Three rows in `product_identifier` hold an Excel scientific form that expands to 13 digits:

| `item_code` | stored `value` | expands to |
| --- | --- | --- |
| `41010100136` | `4.101010013663E12` | `4101010013663` |
| `41010100135` | `4.101010013533E12` | `4101010013533` |
| *(third row same family)* | | |

Note those two share the prefix `41010100136` / `41010100135` — they differ in digits that
survive truncation here, so no collision is currently demonstrated. **No production
mis-export has been observed**, and this is recorded as a latent correctness issue rather
than a live defect.

The reference corpus separately contains 4 EAN-13 values, and
`app/services/product_master/identifiers.py` already keeps them whole
(`identifier_type = EAN_13`, no truncation) rather than repeating this behaviour.

## Expected semantics

Decide and then encode explicitly:

1. Is an EAN-13 ever a valid PDI item code, given the field is 11 digits wide?
2. If it is: which 11 digits, and is the EAN-13 check digit dropped first (leaving 12, then
   what)?
3. If it is not: `normalize_item_code()` should return `None` for a 13-digit input rather
   than silently truncating, so the line exports with the existing blank item-code
   convention instead of a wrong code.

Option 3 is the conservative reading — a wrong item code makes PDI product-match the wrong
product, while a blank one declines to match.

## Required tests

- a 12-digit UPC-A still drops exactly its check digit (**existing golden behaviour must
  not change**);
- a 13-digit EAN-13 produces the agreed result, asserted explicitly rather than incidentally;
- two EAN-13s differing only in digits beyond position 11 do not normalise to the same value;
- an 11-digit PDI code is returned unchanged;
- the placeholder `000000000000` still returns `None`;
- **regression gate and golden EDI hashes unchanged** — this function keys the units-per-case
  mapping lookup *and* emits the EDI item code, so any change alters both together.

## Risk note

`normalize_item_code()` is used both as the mapping lookup key and as the emitted EDI item
code, deliberately (`app/services/export_service.py` documents that they must come from one
function). Changing it changes historical lookups as well as new exports, so this needs its
own phase with the regression gate run before and after.
