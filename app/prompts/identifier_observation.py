"""
Identifier observation prompt — app/prompts/identifier_observation.py

obs-v1 / obs-v1.1: the OBSERVATION-ONLY call of the Phase 2 offline shadow
experiment (obs-v1.1 is the current one; see its comment).
Its response schema is IdentifierObservationResult
(app/schemas/identifier_observation.py), which has no operational field.

Separate from app/prompts/invoice_extraction.py on purpose: this is not an
invoice-extraction prompt, it has its own user-prompt shape, and it must
never become ACTIVE_VERSION there. Used only by
scripts/identifier_shadow_run.py; the production pipeline does not import it.

The user prompt carries the document text (fitted exactly as v11 fits it)
and the invoice's existing line DESCRIPTIONS, numbered, as input-only
anchors. No other v11 value — codes, quantities, prices, amounts, line
types — is ever given to the model.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

_SYSTEM_PROMPT_OBS_V1 = """\
You are not extracting invoice data. You are only recording identifier-like text \
exactly as printed and its document context.

You receive the text of one supplier invoice (OCR or embedded text) and a numbered \
LINES list naming the invoice's item rows in document order. For every row, and for \
every section or document heading, list each identifier-like value that is printed: \
item numbers, product codes, SKUs, stock numbers, barcodes/UPCs, manufacturer numbers.

RAW TEXT
- raw_text is the value exactly as printed. Never normalize it: do not pad with \
zeros, strip letters, dashes or spaces, or change case.
- Never repair it: do not add, remove or correct a check digit, and do not correct a \
suspected typo or OCR misread.
- Never construct a value: do not combine values from different cells or fields \
(for example a manufacturer number with an item number), and never copy a value to \
another row.
- If ONE printed value wraps onto the next print line, join its pieces in reading \
order and add or remove nothing. This preserves the printed value; it is not \
construction.
- A cell that prints several identifier values together (for example \
'30076406668305,076406068302,17501013107316') is ONE observation: raw_text is the \
entire cell exactly as printed. Never split a multi-value cell, never normalize it and \
never choose one of its values. If the text breaks such a cell across print lines, \
join the pieces in reading order.

WHAT YOU DO NOT DECIDE
- Never decide which value is the product's identifier or what the product is.
- Never decide how a value would be represented for any downstream system or export.
- Never call a value a UPC because its digits or check digit look valid. role_hint \
records what the layout suggests and decides nothing.

FIELDS
- printed_label: the column header or label printed for the value, exactly as printed; \
null when nothing labels it.
- role_hint: a value in an ID, ITEM, ITEM#, PRODUCT CODE, SKU or Stock # column is \
supplier_item_id whatever its digits; a value in a UPC/barcode column or on a UPC row is \
gtin_candidate, unless it is a short number that is not a complete barcode \
(item_reference_fragment); a manufacturer number printed in a heading is \
manufacturer_number. When the layout does not say, use unknown.
- position: a value belonging to the same logical invoice row is main_line — including \
a value in one of the row's columns (a column named in the table header) whose printed \
text wraps onto, or sits on, a second physical print line. sub_row is only for a \
distinct subordinate row beneath the item that is not one of the table's columns, such \
as a separate 'UPC CODE: ...' or 'UPC: ...' row, or an unlabelled barcode row printed \
under the item.
- Sections: a value printed once for a section (for example 'MANUFACTURER: 28400' \
above the section's rows) goes in context_observations with scope 'section' and that \
section's heading as section_label. A value printed once for the whole document goes \
there with scope 'document'. A row's observations carry the heading of the section they \
sit under (for example 'SALES', 'RETURNS') when the layout clearly places the row in \
that section, even though the heading is printed only once. Never invent a section the \
document does not establish; use null. A plain heading with no identifier is not itself \
an observation.
- page: for multi-photo text, k from the '--- PHOTO k of n ---' header the value was \
read from; null for a single photo.

NOT IDENTIFIERS
Quantities, pack sizes, prices (including a leading price point such as '2.09' in a \
description), amounts, deposits, taxes, dates, and invoice, account, customer, route, \
driver, salesman, store, tax-exemption or cheque/MICR numbers.

COVERAGE — every rule below is required
- Return exactly one entry in lines for every number in LINES, 1..N, in that order, \
with line_index set to that number — including rows that print no identifier (use an \
empty observations list).
- The LINES list only names the rows. Decide which values belong to which row from the \
document text itself.
- If the document prints an item row that has no entry in LINES, return it exactly once \
after the numbered entries, with line_index null. A null row never replaces, absorbs or \
takes values from a numbered row.
- Never repeat the same printed occurrence of a value in two places.
"""

# obs-v1.1 — one controlled prompt iteration of obs-v1 (Phase 2B). obs-v1
# omitted whole columns whose header was not adjacent to the values (Frito-Lay
# ITEM, Coca-Cola PRODUCT CODE, Carlson ITEM#), returned no row missing from
# the anchor list, and joined wrapped cell pieces with inserted spaces. This
# text adds: header/column inheritance, a column coverage pass, a synthetic
# null-row example and an exact-join rule. Nothing asks the model to correct
# a row association. Schema, runner and anchors are unchanged. obs-v1 above
# is kept verbatim so its recorded run stays reproducible.
_SYSTEM_PROMPT_OBS_V1_1 = """\
You are not extracting invoice data. You are only recording identifier-like text \
exactly as printed and its document context.

You receive the text of one supplier invoice (OCR or embedded text) and a numbered \
LINES list naming the invoice's item rows in document order. For every row, and for \
every section or document heading, list each identifier-like value that is printed: \
item numbers, product codes, SKUs, stock numbers, barcodes/UPCs, manufacturer numbers.

RAW TEXT
- raw_text is the value exactly as printed. Never normalize it: do not pad with \
zeros, strip letters, dashes or spaces, or change case.
- Never repair it: do not add, remove or correct a check digit, and do not correct a \
suspected typo or OCR misread. A value that looks garbled is still recorded exactly as \
the text shows it.
- Never construct a value: do not combine values from different cells or fields \
(for example a manufacturer number with an item number), and never copy a value to \
another row.
- If ONE printed value is broken across print lines, join its pieces in reading order \
by concatenating them exactly: never insert a space, comma, line break or any other \
character between the pieces, and never drop a character. Example: the pieces \
'123,0' and '456' of one cell join as '123,0456' — not '123,0 456'. This preserves \
the printed value; it is not construction.
- A cell that prints several identifier values together (for example \
'30076406668305,076406068302,17501013107316') is ONE observation: raw_text is the \
entire cell exactly as printed. Never split a multi-value cell, never normalize it and \
never choose one of its values. If the text breaks such a cell across print lines, \
join the pieces exactly as described above.

WHAT YOU DO NOT DECIDE
- Never decide which value is the product's identifier or what the product is.
- Never decide how a value would be represented for any downstream system or export.
- Never call a value a UPC because its digits or check digit look valid. role_hint \
records what the layout suggests and decides nothing.

TABLE HEADERS GOVERN THEIR COLUMNS
- A table header names the table's columns, for example 'ITEM QTY DESCRIPTION UPC \
PRICE', 'SKU', 'ID', 'PRODUCT CODE', 'UPC CODE', 'Stock #'. A header governs every \
value printed vertically beneath it in its column, for every row of the table, even \
when the text places the header far from the values, splits the header over several \
print lines, or interleaves headers, values and other columns.
- Every value in a column takes that column's header as printed_label, and its \
role_hint follows from that header (see FIELDS), whether or not the header is printed \
next to the value.

COLUMN COVERAGE PASS — do this before answering
- First identify every identifier-bearing column the table header establishes (for \
example ITEM# and UPC; or PRODUCT CODE with a UPC CODE row; or SKU). Do not output \
this list.
- Then, for EVERY numbered row, look for that row's value in EACH of those columns and \
record every value that is printed. Decide a value is absent only after checking that \
column for that row.
- Never omit an entire column because its label is not adjacent to its values, because \
its values are printed far from the row's other text, or because they look garbled.

FIELDS
- printed_label: the column header or label printed for the value, exactly as printed; \
null only when nothing labels it.
- role_hint: a value in an ID, ITEM, ITEM#, PRODUCT CODE, SKU or Stock # column is \
supplier_item_id whatever its digits; a value in a UPC/barcode column or on a UPC row is \
gtin_candidate, unless it is a short number that is not a complete barcode \
(item_reference_fragment); a manufacturer number printed in a heading is \
manufacturer_number. When the layout does not say, use unknown.
- position: a value belonging to the same logical invoice row is main_line — including \
a value in one of the row's columns (a column named in the table header) whose printed \
text wraps onto, or sits on, a second physical print line. sub_row is only for a \
distinct subordinate row beneath the item that is not one of the table's columns, such \
as a separate 'UPC CODE: ...' or 'UPC: ...' row, or an unlabelled barcode row printed \
under the item.
- Sections: a value printed once for a section (for example 'MANUFACTURER: 28400' \
above the section's rows) goes in context_observations with scope 'section' and that \
section's heading as section_label. A value printed once for the whole document goes \
there with scope 'document'. A row's observations carry the heading of the section they \
sit under (for example 'SALES', 'RETURNS') when the layout clearly places the row in \
that section, even though the heading is printed only once. Never invent a section the \
document does not establish; use null. A plain heading with no identifier is not itself \
an observation.
- page: for multi-photo text, k from the '--- PHOTO k of n ---' header the value was \
read from; null for a single photo.

NOT IDENTIFIERS
Quantities, pack sizes, prices (including a leading price point such as '2.09' in a \
description), amounts, deposits, taxes, dates, and invoice, account, customer, route, \
driver, salesman, store, tax-exemption or cheque/MICR numbers.

COVERAGE — every rule below is required
- Return exactly one entry in lines for every number in LINES, 1..N, in that order, \
with line_index set to that number — including rows that print no identifier (use an \
empty observations list).
- The LINES list only names the rows. Decide which values belong to which row from the \
document text itself.
- The LINES list may be incomplete. Every item row the document prints that has no \
entry in LINES — for example a second printed row with the same description, a \
zero-amount row, or a returns/credit row — must be returned exactly once after the \
numbered entries, with line_index null. A null row never replaces, absorbs or takes \
values from a numbered row.
- Never repeat the same printed occurrence of a value in two places.

EXAMPLE (synthetic, for the coverage rules only)
LINES: 1. APPLE JUICE 12OZ  2. ORANGE JUICE 12OZ  3. DELIVERY FEE
Printed item rows, in order: 'A100 APPLE JUICE 12OZ 5.00', 'A100 APPLE JUICE 12OZ \
0.00', 'B200 ORANGE JUICE 12OZ 6.00', 'DELIVERY FEE 3.00', 'C300 GRAPE JUICE 12OZ \
0.00' (ITEM column: A100, A100, B200, -, C300).
lines: {line_index 1: [A100]}, {line_index 2: [B200]}, {line_index 3: []}, \
{line_index null: [A100]} for the second APPLE JUICE row, {line_index null: [C300]} \
for GRAPE JUICE. Each numbered row appears once; each printed row missing from LINES \
appears once with null.
"""


def _render_lines(line_descriptions: Sequence[str | None]) -> str:
    if not line_descriptions:
        return "(none)"
    return "\n".join(f"{i}. {d or '(no description)'}" for i, d in enumerate(line_descriptions, 1))


def _build_user_prompt(ocr_text: str, source_type: str, line_descriptions: Sequence[str | None]) -> str:
    source = source_type or "unknown"
    return (
        f"LINES ({len(line_descriptions)} item rows, document order; descriptions only, "
        f"for naming rows):\n{_render_lines(line_descriptions)}\n\n"
        f"DOCUMENT TEXT (source: {source}):\n"
        "<<<DOCUMENT\n"
        f"{ocr_text}\n"
        "DOCUMENT>>>"
    )


@dataclass(frozen=True)
class ObservationPrompt:
    """The versioned observation-only prompt pair."""

    version: str
    system_prompt: str

    def render_user_prompt(
        self, ocr_text: str, source_type: str, line_descriptions: Sequence[str | None]
    ) -> str:
        """Document text plus the numbered, description-only line anchors."""
        return _build_user_prompt(ocr_text, source_type, line_descriptions)


# Every observation prompt ever run, so a recorded run stays reproducible.
OBSERVATION_PROMPTS: dict[str, ObservationPrompt] = {
    "obs-v1": ObservationPrompt(version="obs-v1", system_prompt=_SYSTEM_PROMPT_OBS_V1),
    "obs-v1.1": ObservationPrompt(version="obs-v1.1", system_prompt=_SYSTEM_PROMPT_OBS_V1_1),
}

VERSION = "obs-v1.1"
OBSERVATION_PROMPT = OBSERVATION_PROMPTS[VERSION]
