"""
Case Mapping Service — app/services/case_mapping_service.py

Bridges an Invoice aggregate to the UPC → units-per-case mapping table.

Kept separate from export_service so that module stays pure and
synchronous (it takes a plain dict), and separate from the repository so
the repository stays unaware of invoices. Both the export endpoint and
the invoice-detail endpoint use these helpers, so the rule they enforce
cannot drift between "can I download?" and "what does the UI show?".
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import Invoice
from app.repositories.product_case_mapping_repository import ProductCaseMappingRepository
from app.services.export_service import (
    normalize_item_code,
    pack_candidates,
    suggest_units_per_case,
)

SUGGESTION_FROM_DATABASE = "database"


@dataclass(frozen=True)
class CaseMappingStatus:
    """One line item's units-per-case state, for the review UI."""

    item_code: str | None          # normalized; None when the line has no usable code
    description: str | None
    units_per_case: int | None     # confirmed value, when one exists
    suggested_units_per_case: int | None  # from the document, needs confirmation
    suggestion_source: str | None  # where the number came from; see SUGGESTION_*
    suggestion_candidates: list[int]  # readings an ambiguous pack could support
    pack_size: str | None          # raw printed pack descriptor, shown as evidence
    mapped: bool


async def invoice_units_by_item_code(
    session: AsyncSession, invoice: Invoice
) -> dict[str, int]:
    """Confirmed units-per-case for every product on this invoice."""
    codes = [
        code
        for code in (normalize_item_code(item.product_sku) for item in invoice.items)
        if code
    ]
    return await ProductCaseMappingRepository(session).units_by_item_code(codes)


def build_case_mapping_status(
    invoice: Invoice, units_by_item_code: dict[str, int]
) -> list[CaseMappingStatus]:
    """
    Per-line mapping state, in document order.

    Lines without a usable product code are reported as mapped: there is
    nothing to key a mapping on, they already export with the blank
    item-code convention, and blocking on them would make such an invoice
    permanently un-exportable.
    """
    statuses: list[CaseMappingStatus] = []
    for item in sorted(invoice.items, key=lambda i: i.sort_order):
        code = normalize_item_code(item.product_sku)
        units = units_by_item_code.get(code or "") if code else None
        suggestion, source = suggest_units_per_case(item.pack_size, item.description)
        statuses.append(
            CaseMappingStatus(
                item_code=code,
                description=item.description,
                units_per_case=units,
                suggested_units_per_case=suggestion,
                # A confirmed mapping outranks any suggestion, so report the
                # database as the source rather than whatever the document
                # happened to say — that is the value actually used.
                suggestion_source=SUGGESTION_FROM_DATABASE if units is not None else source,
                # Only populated for a structurally ambiguous description,
                # and only while the product is still unmapped: once a
                # human has decided, the candidates are history.
                suggestion_candidates=(
                    [] if units is not None else pack_candidates(item.description)
                ),
                pack_size=item.pack_size,
                mapped=units is not None or code is None,
            )
        )
    return statuses
