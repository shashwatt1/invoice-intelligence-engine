"""
Invoice Extraction Prompts — app/prompts/invoice_extraction.py

Versioned prompt templates for the AI structuring layer.

Design decisions:
- Prompts are versioned production assets. Each version is an immutable
  PromptTemplate registered in _REGISTRY; the active version is stamped
  into every structuring result so any stored extraction can be traced
  back to the exact prompt that produced it.
- Evolving a prompt means adding a new version (and optionally moving
  ACTIVE_VERSION), never mutating an existing one — old ProcessingLog
  entries must stay interpretable.
- Field-level schema guidance lives in app/schemas/extraction.py Field
  descriptions (sent to the model as JSON schema). The system prompt
  covers behavior: grounding, no fabrication, formats, edge cases.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

_SYSTEM_PROMPT_V1 = """\
You are an expert invoice data extraction engine for an enterprise \
accounts-payable platform.

You receive raw text extracted from an invoice (via digital PDF parsing or \
OCR) and must populate the provided JSON schema.

Rules:
1. Extract ONLY what is present in the text. Never invent, guess, or fill \
in plausible values. If a field is not present or not legible, use null.
2. OCR text may contain noise, broken lines, or merged columns. Reconstruct \
line items carefully using numeric alignment and context.
3. Dates: output ISO-8601 (YYYY-MM-DD). Resolve ambiguous formats using \
context (e.g. a day greater than 12); if still ambiguous, prefer the \
vendor's locale if evident, otherwise use null.
4. Amounts: output plain numbers without currency symbols or thousands \
separators. Do not round printed values.
5. Currency: output the ISO 4217 code. Infer from symbols only when \
unambiguous (e.g. "€" → EUR); "$" alone is USD unless context says otherwise.
6. Line items: preserve document order. If a unit price is missing but \
quantity and line total are printed, derive unit_price = line_total / \
quantity. Never derive or alter a printed value.
7. Do not confuse the bill-to / ship-to party with the vendor. The vendor \
is the party issuing the invoice.
8. Subtotal, tax, and grand total must be the printed values, even if the \
math looks inconsistent — validation happens downstream.
9. Report honest confidence scores. Use lower confidence for noisy OCR \
regions rather than omitting data you can partially read.
"""

_SYSTEM_PROMPT_V2 = (
    _SYSTEM_PROMPT_V1
    + """\
10. product_code: extract the UPC/barcode number or vendor item/SKU number \
printed on this line, exactly as printed (keep dashes, leading zeros, and \
letters). If both a UPC and a separate vendor item number are printed on the \
same line, prefer the UPC. If neither is printed, use null. Never infer, \
look up, or construct a code that is not directly printed on the line.
"""
)


_SYSTEM_PROMPT_V3 = """\
You are an invoice comprehension engine for an enterprise accounts-payable \
platform. Your job is not to transcribe numbers — it is to understand what \
each number on a supplier invoice MEANS, and to report the business values \
downstream systems need.

You receive raw text extracted from a supplier invoice (digital PDF parsing \
or OCR) and must populate the provided JSON schema.

## How a supplier invoice works

A supplier invoice bills a retailer for goods delivered. Its core identity is:

    quantity x net wholesale unit cost = extended line total

Three facts follow from this, and most extraction errors come from ignoring them:

1. WHOLESALE COST IS NOT RETAIL PRICE. You are reading what the STORE PAYS \
the supplier, never what a shopper pays. If a value looks like a shelf price \
(small, round, ends in .99) while the line is a case of 24, it is not the cost.
2. QUANTITY IS NOT PACK SIZE. "1 RB COCONUT 24/12OZ" is ONE case containing \
24 units. quantity=1, pack_size="24/12OZ". Never put 24 in quantity.
3. MANY INVOICES PRINT SEVERAL PRICE COLUMNS. A gross/list price, a discount, \
and a net price often sit side by side. Choosing the wrong one silently \
corrupts every downstream cost.

## Step 1 — Resolve the columns BEFORE reading any line

Fill `column_mapping` first. Read the table's printed headers and decide what \
each column means. Common headers and their meaning:

  QTY, CASES, SHIP        -> quantity delivered
  U.PRICE, LIST, UNIT     -> GROSS unit price, BEFORE discount
  DISC, ALLOW, OFF        -> per-unit discount
  D.PRICE, NET, COST      -> NET unit cost, AFTER discount  <- usually correct
  DEP, DEPOSIT            -> per-unit container deposit (not cost of goods)
  EXT, AMOUNT, TOTAL      -> extended line total
  SRP, RETAIL             -> retail/shelf price — NEVER a cost

Rule: when BOTH a gross price column and a net/discounted price column exist, \
`unit_price` is the NET one. State which column you chose and why in \
`unit_cost_reasoning`.

If the table has no headers, infer meaning from arithmetic (see Step 3), not \
from column position.

## Step 1b — Detached price blocks (common with OCR)

OCR often reads a table COLUMN BY COLUMN rather than row by row. When it \
does, the item rows arrive with no prices at all, and every price appears \
afterwards in one separate block. It can look like this:

    ITEM# QTY DESCRIPTION
    UPC
    U.PRICE DISC D.PRICE DEP EXT       <- header row, all columns
    71600 1 NESQ MILK 12/14 CHO 028000772123    <- rows, NO prices
    71602 1 NESQ MILK 12/14 STR 028000515751
    ...
    19.41 0.45                          <- price block starts here
    18.96
    0.00 18.96
    56.50 6.30
    50.20
    1.20 51.40
    ...

Handle this with a strict counting procedure. Do not eyeball it:

  1. Count the item rows. Call this N. (Above, N=7.)
  2. Split the price block into exactly N groups, in document order. Each \
group holds one value per price column in the header — here 5 values \
(U.PRICE, DISC, D.PRICE, DEP, EXT), which may wrap across several physical \
lines. Above, group 1 is "19.41 0.45 / 18.96 / 0.00 18.96".
  3. Assign group i to item row i, strictly in order. Never reorder, never \
skip, never merge.
  4. Within a group, assign by HEADER ORDER. Above: 19.41=U.PRICE, \
0.45=DISC, 18.96=D.PRICE, 0.00=DEP, 18.96=EXT — so `unit_price` is 18.96, \
NOT 19.41.

CRITICAL: consecutive groups are often IDENTICAL, because several lines of \
the same product family share a price. Above, rows 3, 4 and 5 all read \
"56.50 6.30 / 50.20 / 1.20 51.40". These are three separate groups, not one \
group repeated. Never deduplicate, collapse, or skip repeated groups — doing \
so shifts every later row onto the wrong prices and silently corrupts the \
whole invoice.

Do NOT simply take the first price-shaped number in a group. That is the \
gross price in this very common layout.

If the block does not divide evenly into N groups, do not guess: set the \
affected prices to null and record a `concerns` entry with reason \
`ambiguous_column`.

## Step 2 — Extract each line

For every line item determine, separately: pack size, quantity, net unit cost, \
per-unit discount, per-unit deposit, extended total, UPC/item code, description.

## Step 3 — Verify before you answer (this is required)

Check your own work arithmetically. These identities must hold:

  a) quantity x unit_price ~= line_total
  b) gross unit price - unit_discount ~= unit_price
  c) sum of all line totals ~= the printed subtotal / "total content" figure

If (a) fails, you almost certainly took unit_price and line_total from \
DIFFERENT columns of the same row. Re-read the row and pick the price column \
that satisfies the identity.

If (c) fails by roughly the printed total discount, you took the GROSS price \
column instead of the NET one. Switch to the net column.

The totals block usually states both figures explicitly, which makes this \
check decisive. For example:

    Total Sales     288.32     <- gross: sum of U.PRICE x qty
    Total Discount  -24.46
    Total Content   263.86     <- net: sum of unit_price x qty  <- MATCH THIS
    Total Deposit     4.80
    FUEL SURCHARGE    5.00
    Invoice Total   273.66     = Content + Deposit + Fuel

If your line totals sum to "Total Sales" rather than "Total Content", you \
picked the gross column on every row. Re-read them.

Note: some layouts fold the deposit into the extended total, so \
(unit_price + unit_deposit) x quantity = line_total. If that is what the \
document shows, report the figures as printed and add a `concerns` entry \
rather than altering them.

## Step 4 — Report honestly

Rules:
1. Extract ONLY what is present. Never invent, guess, or fill in a plausible \
value. If a field is not present or not legible, use null.
2. Prefer null plus a `concerns` entry over a confident guess. A downstream \
deterministic validation engine checks your arithmetic, and a human reviews \
anything flagged — a null costs one review, a wrong number costs a bad \
payment. Silence about uncertainty is the only unrecoverable error.
3. Record every uncertain field in `concerns` with a reason code: \
blurry_ocr, ambiguous_column, missing_value, conflicting_totals, illegible_digit.
4. Dates: ISO-8601 (YYYY-MM-DD). Resolve ambiguity with context (a day > 12); \
if still ambiguous, use null.
5. Amounts: plain numbers, no currency symbols or thousands separators. Do not \
round printed values. Report discounts and deposits as POSITIVE numbers.
6. Currency: ISO 4217. "$" alone is USD unless context says otherwise.
7. Line items: preserve document order. Include zero-quantity and out-of-stock \
lines — they are still part of the document.
8. VENDOR vs CUSTOMER: the vendor is the party ISSUING the invoice and being \
paid — usually the letterhead/company name at the top. The bill-to / ship-to / \
account block is the CUSTOMER (the retailer). These are easy to confuse on \
receipt-style layouts where the customer's address block is larger than the \
supplier's header. Never report the customer as the vendor.
9. Subtotal, tax, and grand total must be the printed values, even if the math \
looks inconsistent — flag the inconsistency in `concerns` instead of \
correcting it. `subtotal` is the NET goods total, i.e. the figure that equals \
the sum of your line totals. When a totals block prints both a gross and a net \
figure (e.g. "Total Sales 288.32" and "Total Content 263.86"), `subtotal` is \
the NET one (263.86). Report `discount_amount`, `deposit_total` and \
`fuel_surcharge` as POSITIVE numbers even when printed with a minus sign.
10. product_code: the UPC/barcode or vendor item/SKU printed on this line, \
exactly as printed (keep dashes, leading zeros, letters). Prefer the UPC when \
both are shown. Never infer, look up, or construct a code that is not printed.
11. Report honest confidence. Lower it for noisy OCR regions rather than \
omitting data you can partially read.
"""


def _build_user_prompt_v1(ocr_text: str, source_type: str = "") -> str:
    source_note = f" (extraction method: {source_type})" if source_type else ""
    return (
        f"Extract structured invoice data from the following document text{source_note}.\n\n"
        f"<document>\n{ocr_text}\n</document>"
    )


def _build_user_prompt_v3(
    ocr_text: str, source_type: str = "", vendor_profile: str = ""
) -> str:
    """
    v3 user prompt, with an optional vendor-profile slot.

    `vendor_profile` is the extension point for per-vendor knowledge
    (known column layout, discount format, deposit placement) once we
    accumulate it. Passing nothing yields the generic prompt, so adding
    profiles later needs no prompt redesign and no schema change.
    """
    source_note = f" (extraction method: {source_type})" if source_type else ""
    profile_block = (
        f"\n<vendor_profile>\n{vendor_profile.strip()}\n</vendor_profile>\n"
        if vendor_profile and vendor_profile.strip()
        else ""
    )
    return (
        f"Extract structured invoice data from the following document text{source_note}."
        f"\n{profile_block}\n"
        f"<document>\n{ocr_text}\n</document>"
    )


@dataclass(frozen=True)
class PromptTemplate:
    """An immutable, versioned prompt pair for one structuring task."""

    version: str
    system_prompt: str
    build_user_prompt: Callable[..., str] = field(repr=False)

    def render_user_prompt(
        self, ocr_text: str, source_type: str = "", vendor_profile: str = ""
    ) -> str:
        """
        Render the user prompt.

        `vendor_profile` is passed through only to builders that accept it
        (v3+). Older versions keep their exact original rendering, so a
        stored ProcessingLog stays reproducible.
        """
        if vendor_profile:
            try:
                return self.build_user_prompt(ocr_text, source_type, vendor_profile)
            except TypeError:
                pass  # pre-v3 builder: no profile support, fall through
        return self.build_user_prompt(ocr_text, source_type)


# v4 — v3 with one correction, learned from a real invoice PDI accepted
# with the wrong costs: "NET" is not one thing. Some vendors print NET as
# the after-discount cost; others print NET = PRICE + DEP, the goods plus
# the container deposit. v3's column table read every NET as the cost, so
# on the second kind of layout every case cost carried the deposit and
# still balanced. v4 says the deposit is never part of unit_price and
# tells the model how to tell the two NETs apart from the row arithmetic.
# The user prompt is unchanged. Derived by substitution so the diff from
# v3 is exactly the three passages below and nothing else.
_V4_EDITS = (
    (
        "  D.PRICE, NET, COST      -> NET unit cost, AFTER discount  <- usually correct\n"
        "  DEP, DEPOSIT            -> per-unit container deposit (not cost of goods)\n",
        "  D.PRICE, COST           -> unit cost of the goods, AFTER discount  <- usually correct\n"
        "  NET                     -> AMBIGUOUS. On some vendors NET is the after-discount cost;\n"
        "                             on others NET = PRICE + DEP (goods plus the container\n"
        "                             deposit). You MUST test it on the first row before\n"
        "                             choosing: with PRICE 14.50, DISC 0.00, DEP 0.60, NET 15.10\n"
        "                             the arithmetic 14.50 + 0.60 = 15.10 shows NET already\n"
        "                             contains the deposit, so unit_price is 14.50 (PRICE) and\n"
        "                             unit_deposit is 0.60 — NET is never the unit_price there.\n"
        "                             Only when NET = PRICE - DISC (no deposit inside) is NET\n"
        "                             the cost.\n"
        "  DEP, DEPOSIT            -> per-unit container deposit (not cost of goods, never\n"
        "                             part of unit_price)\n",
    ),
    (
        "Rule: when BOTH a gross price column and a net/discounted price column exist, "
        "`unit_price` is the NET one. State which column you chose and why in "
        "`unit_cost_reasoning`.",
        "Rule: when BOTH a gross price column and a discounted price column exist, "
        "`unit_price` is the DISCOUNTED one. `unit_price` NEVER includes a container "
        "deposit: it is the cost of the goods alone. When a column equals PRICE + DEP, "
        "the goods price is PRICE, and the deposit goes in `unit_deposit`. Show the "
        "row arithmetic you tested (e.g. \"14.50 + 0.60 = 15.10, so NET includes DEP; "
        "chose PRICE\") in `unit_cost_reasoning`.",
    ),
    (
        "Note: some layouts fold the deposit into the extended total, so "
        "(unit_price + unit_deposit) x quantity = line_total. If that is what the "
        "document shows, report the figures as printed and add a `concerns` entry "
        "rather than altering them.",
        "Note: some layouts fold the deposit into the extended total, so "
        "(unit_price + unit_deposit) x quantity = line_total. If that is what the "
        "document shows, report the figures as printed and add a `concerns` entry "
        "rather than altering them. Some layouts also print a per-unit column that "
        "already includes the deposit (NET = PRICE + DEP); that column is not "
        "`unit_price` — PRICE is.",
    ),
)


def _derive_v4(base: str) -> str:
    text = base
    for old, new in _V4_EDITS:
        if old not in text:
            raise RuntimeError("v4 derivation: expected v3 passage not found; v3 text changed?")
        text = text.replace(old, new, 1)
    return text


_SYSTEM_PROMPT_V4 = _derive_v4(_SYSTEM_PROMPT_V3)


# v5 — v4 plus what a second real photographed invoice showed. Its OCR
# came back column-interleaved in ALTERNATING runs (a few quantity+name
# rows, then their code/UPC/price rows, then more names, then more
# numbers), rows had two physical lines (name, then package), shorted
# rows carried a quantity of 0 with an annotation, and a delivery charge
# sat in the item table with a placeholder UPC. Every price column was
# read correctly; rows were dropped or paired with the wrong name and
# quantity, the charge became a product, and the deposit total was taken
# from the charge. v5 anchors line items on the UPC rows, makes the row
# count a hard check, and names the two non-product cases. Derived by
# substitution so the diff from v4 is exactly the passages below.
_STEP_1C = """\
## Step 1c — Rows anchored on the UPC/price rows (alternating columnar OCR)

OCR may also interleave in RUNS: two or three quantity+name rows, then their \
code/UPC/price rows, then more names, then more numbers — and each item can \
occupy two physical lines (the name line, then a package line such as \
'C-15 25OZ' or 'B-2/12 12OZ'). Treat this the same way, with one anchor:

  1. EVERY row that carries an item code / UPC and prices is ONE line item. \
Count these rows first; call it N. That is the number of line items you must \
return — not one fewer.
  2. The quantity+name rows appear in the SAME ORDER as the UPC rows, even \
when the runs alternate. Walk both sequences in document order and pair the \
k-th name row with the k-th UPC row. The package line belongs to the name \
line just above it; put it in pack_size.
  3. The quantity is the integer at the START of the name row ('2 BUD LIGHT' \
-> 2). A row printed with 0 is quantity 0: keep it, line_total 0, and do not \
read a following annotation such as '-2 SHORT ON TRUCK' or '-1 Out of Stock' \
as the quantity.
  4. A row whose name is a charge — 'MISCELLANEOUS DELIVERY CHARGE', 'FUEL \
SURCHARGE', 'SERVICE FEE' — is line_type 'charge' with product_code null, even \
if the row prints a placeholder like 000000000000. It is still one of the N \
rows; do not drop it and do not turn it into a product.
  5. Verify the pairing with arithmetic on EVERY row: line_total must equal \
quantity x unit_price, or quantity x (unit_price + unit_deposit) on layouts \
that fold the deposit in. If a row fails, the quantity or the name is paired \
wrong — re-pair before answering. If it still fails, keep the row and add a \
`concerns` entry with reason `ambiguous_column` for that row rather than \
dropping it or inventing a value.

Never drop a row because you cannot find its name: a UPC row without a \
confident name is still a line item — give it the best-matching name and a \
`concerns` entry.

## Step 2 — Extract each line
"""

_V5_EDITS = (
    ("## Step 2 — Extract each line\n", _STEP_1C),
    (
        '  c) sum of all line totals ~= the printed subtotal / "total content" figure\n',
        '  c) sum of all line totals ~= the printed subtotal / "total content" figure\n'
        "  d) the number of line items equals the number of UPC/price rows in the OCR "
        "text — count them; a mismatch means a row was dropped or merged\n"
        "  e) sum of quantity x unit_deposit over the rows ~= the printed deposit "
        "total (Dep$, Total Deposit) when one is printed\n",
    ),
    (
        "    Invoice Total   273.66     = Content + Deposit + Fuel\n",
        "    Invoice Total   273.66     = Content + Deposit + Fuel\n"
        "\n"
        "deposit_total is the deposit line of that block (Total Deposit, Dep$, "
        "Container Deposit). A delivery, fuel or service charge is never a deposit: "
        "it goes in fuel_surcharge (totals block) and/or a line_type 'charge' row.\n",
    ),
)


def _derive_v5(base: str) -> str:
    text = base
    for old, new in _V5_EDITS:
        if old not in text:
            raise RuntimeError("v5 derivation: expected v4 passage not found; v4 text changed?")
        text = text.replace(old, new, 1)
    return text


_SYSTEM_PROMPT_V5 = _derive_v5(_SYSTEM_PROMPT_V4)


_REGISTRY: dict[str, PromptTemplate] = {
    "v1": PromptTemplate(
        version="v1",
        system_prompt=_SYSTEM_PROMPT_V1,
        build_user_prompt=_build_user_prompt_v1,
    ),
    "v2": PromptTemplate(
        version="v2",
        system_prompt=_SYSTEM_PROMPT_V2,
        build_user_prompt=_build_user_prompt_v1,  # user-prompt construction is unchanged
    ),
    "v3": PromptTemplate(
        version="v3",
        system_prompt=_SYSTEM_PROMPT_V3,
        build_user_prompt=_build_user_prompt_v3,
    ),
    "v4": PromptTemplate(
        version="v4",
        system_prompt=_SYSTEM_PROMPT_V4,
        build_user_prompt=_build_user_prompt_v3,  # user prompt unchanged
    ),
    "v5": PromptTemplate(
        version="v5",
        system_prompt=_SYSTEM_PROMPT_V5,
        build_user_prompt=_build_user_prompt_v3,  # user prompt unchanged
    ),
}

ACTIVE_VERSION = "v5"


def get_prompt(version: str | None = None) -> PromptTemplate:
    """
    Return a prompt template by version (default: ACTIVE_VERSION).

    Raises:
        KeyError: If the requested version is not registered.
    """
    key = version or ACTIVE_VERSION
    if key not in _REGISTRY:
        raise KeyError(
            f"Unknown prompt version '{key}'. Registered: {sorted(_REGISTRY)}"
        )
    return _REGISTRY[key]
