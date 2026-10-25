"""
Identifier Observation Schemas — app/schemas/identifier_observation.py

The response contract of the OBSERVATION-ONLY call (prompt obs-v1,
app/prompts/identifier_observation.py), an offline shadow experiment.

Standalone by design: nothing here subclasses or embeds ExtractedInvoice or
ExtractedLineItem, and no property in the schema tree carries an operational
value (code fields, quantities, prices, amounts, taxes, descriptions, dates,
totals). The call therefore cannot drift the v11 extraction: v11 remains the
only source of every operational field. A Phase 2 experiment that returned
observations inside the v11 response changed v11's own fields on 5 of 8
gold invoices despite explicit prompt guards; making drift structurally
impossible is the point of this module.

An observation records what is printed and where — never what it means.
There is deliberately no normalized value, identifier type, check-digit
status, packaging level or confidence: those are decisions for deterministic
code, and role_hint is a hint that code may overrule.

Association: the prompt numbers the invoice's existing (v11) line
descriptions as input-only anchors; each ObservedLine names its row by that
number (line_index), or null for a printed item row the list does not
contain. line_index lives on the container, not on the observation.

Used only by scripts/identifier_shadow_run.py. Nothing in the production
pipeline imports this module.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

IdentifierRoleHint = Literal[
    "gtin_candidate", "supplier_item_id", "manufacturer_number",
    "item_reference_fragment", "unknown",
]


class IdentifierObservation(BaseModel):
    """One identifier-like value exactly as it is printed on the document."""

    raw_text: str = Field(
        description=(
            "The value EXACTLY as printed: every digit, letter, dash, space and comma "
            "kept. If one printed value wraps onto the next print line, join the pieces "
            "in reading order and change nothing else. A cell printing several values "
            "together is ONE observation whose raw_text is the whole cell as printed. "
            "Never pad, strip, add or remove a check digit, split, choose one value, "
            "correct a suspected typo, or copy it from another row."
        )
    )
    printed_label: str | None = Field(
        description=(
            "The label or column header printed for this value, exactly as printed "
            "(e.g. 'UPC', 'UPC CODE', 'UPC:', 'ID', 'ITEM', 'ITEM#', 'SKU', 'Stock #', "
            "'PRODUCT CODE', 'MANUFACTURER'). Null when nothing labels it."
        )
    )
    role_hint: IdentifierRoleHint = Field(
        description=(
            "What the document's layout suggests this value is — an observation, not a "
            "decision. A value in an ID/ITEM/ITEM#/PRODUCT CODE/Stock # column is "
            "supplier_item_id whatever its digits. A short number in a column labelled "
            "UPC that is not a complete barcode is item_reference_fragment. A "
            "manufacturer number printed in a header is manufacturer_number. Use "
            "'unknown' whenever the layout does not say."
        )
    )
    scope: Literal["line", "section", "document"] = Field(
        description=(
            "'line' for a value printed on a line item (including its sub-rows); "
            "'section' for a value printed once for a section of lines; 'document' for "
            "a value printed once for the whole document."
        )
    )
    section_label: str | None = Field(
        description=(
            "The printed heading of the section this value belongs to (e.g. 'SALES', "
            "'RETURNS'), exactly as printed — for a line value too, when the layout "
            "clearly places the line under that heading. Null when the document does "
            "not establish a section; never invent one."
        )
    )
    position: Literal["main_line", "sub_row"] | None = Field(
        description=(
            "For a line value: 'main_line' when it belongs to the same logical invoice "
            "row, including a value in one of the table's columns whose text wraps onto "
            "a second physical line; 'sub_row' only for a distinct subordinate row under "
            "the item that is not a table column (e.g. a separate 'UPC CODE:' row or an "
            "unlabelled barcode row). Null for section/document values."
        )
    )
    page: int | None = Field(
        description=(
            "Only for multi-photo text: k from the '--- PHOTO k of n ---' header of the "
            "photo this value was read from. Null for a single photo."
        )
    )


class ObservedLine(BaseModel):
    """The identifier observations of one printed item row."""

    line_index: int | None = Field(
        description=(
            "The number of this row in the numbered LINES list given with the document. "
            "Every number in that list appears exactly once, even when the row prints no "
            "identifier. Null only for a printed item row that the list does not contain; "
            "such a row is returned once and never takes the place of a numbered row."
        )
    )
    observations: list[IdentifierObservation] = Field(
        description=(
            "Every identifier-like value printed on this row, including its sub-rows, "
            "each exactly as printed, with scope 'line'. Empty list if none."
        )
    )


class IdentifierObservationResult(BaseModel):
    """Identifier-like text of one document and its context. Nothing else."""

    lines: list[ObservedLine] = Field(
        description=(
            "One entry per numbered row in LINES order (1..N), then one entry per printed "
            "item row not in the list (line_index null)."
        )
    )
    context_observations: list[IdentifierObservation] = Field(
        description=(
            "Identifier-like values printed once for a section (scope 'section', with its "
            "section_label) or for the whole document (scope 'document') that relate to "
            "the products, e.g. a 'MANUFACTURER: 28400' line above a section's rows. Not "
            "identifiers: invoice, account, customer, route, store, tax-exemption and "
            "cheque/MICR numbers. Empty list if none."
        )
    )
