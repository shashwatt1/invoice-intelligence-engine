"""
Vendor item crosswalk — app/services/vendor_item_crosswalk.py

Confirm once that a vendor's item number is a product, then reuse it.

A route vendor (Frito-Lay, Red Bull, ...) prints its own item number and often
no UPC. The line cannot be exported until someone enters the UPC. When the
reviewer who enters it also confirms it for the vendor, that decision is kept
as a vendor_item_crosswalk link (through an approved vendor_item proposal, so
it has the same audit trail as any other master-data decision), and every
later line from that vendor carrying the same item number receives the UPC
as a recorded correction:

    corrected_by  "system:vendor-item-crosswalk"
    note          which vendor item and which link supplied it

Safeguards:
  * the key is (vendor record, item number) exactly — nothing fuzzy;
  * only CONFIRMED links are applied; a line whose printed code is already a
    usable barcode is never overwritten, and a disagreement between the
    printed barcode and the link is reported, not resolved;
  * a misread barcode (bad check digit) is not a vendor item number and is
    never crosswalked: it must be corrected;
  * invoices a POSTED receipt locks, invoices in the Recycle Bin or kept as a
    reference, and invoices already exported are left untouched.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import InvoiceBaseException, ValidationError
from app.core.logging import get_logger
from app.models.edi_export import EdiExport
from app.models.invoice import Invoice
from app.models.invoice_item import InvoiceItem
from app.models.product_data_proposal import (
    ENTITY_VENDOR_ITEM,
    FIELD_ITEM_CODE,
    SOURCE_UPC_ENTRY,
    ProductDataProposal,
)
from app.models.product_master import MasterProduct
from app.models.vendor_item_crosswalk import (
    CROSSWALK_CONFIRMED,
    CROSSWALK_RETIRED,
    VendorItemCrosswalk,
)
from app.repositories.document_repository import document_is_active
from app.repositories.product_data_proposal_repository import ProductDataProposalRepository
from app.services import gtin
from app.services.export_service import NON_PDI_LINE_TYPES
from app.services.product_master.identifiers import upc_a_check_digit

logger = get_logger(__name__)

APPLIED_BY = "system:vendor-item-crosswalk"
# Printed values that are a vendor's own code for the item rather than a misread barcode.
_VENDOR_CODE_KINDS = frozenset({gtin.KIND_SHORT, gtin.KIND_GTIN_14, gtin.KIND_EAN_13})


class CrosswalkRefusedError(ValidationError):
    """A crosswalk entry that cannot be confirmed as asked."""


# ---- what a line offers ------------------------------------------------------------

def vendor_code_for(supplier_item_id: str | None, product_sku: str | None) -> tuple[str, str] | None:
    """
    (normalized, raw) vendor code for a line: its supplier item number, or a
    printed code that is the vendor's own (a short number, a case code) rather
    than a barcode. None when the line offers neither.
    """
    key = gtin.normalize_vendor_item_code(supplier_item_id)
    if key:
        return key, supplier_item_id.strip()
    printed = gtin.classify(product_sku)
    if printed.kind in _VENDOR_CODE_KINDS:
        return printed.digits, (product_sku or "").strip()
    return None


def target_from_entry(entered: str | None) -> tuple[str, str | None]:
    """(11-digit item code, full UPC or None) for a typed barcode; refused if it is not one PDI can use."""
    printed = gtin.classify(entered)
    if not printed.exportable or printed.item_code is None:
        raise CrosswalkRefusedError(
            message=("Only a usable barcode can be confirmed for a vendor item: an 11-digit PDI code or a "
                     f"UPC whose check digit is right ({printed.reason or 'no barcode given'})."),
            detail={"field": "product_code", "reason": "not_a_usable_barcode", "kind": printed.kind},
        )
    return printed.item_code, printed.upc_a


# ---- proposing and confirming ---------------------------------------------------------

async def confirmed_link(session: AsyncSession, vendor_id: uuid.UUID, vendor_item_code: str) -> VendorItemCrosswalk | None:
    return (await session.execute(select(VendorItemCrosswalk).where(
        VendorItemCrosswalk.vendor_id == vendor_id, VendorItemCrosswalk.vendor_item_code == vendor_item_code,
        VendorItemCrosswalk.state == CROSSWALK_CONFIRMED,
    ))).scalars().first()


async def propose(
    session: AsyncSession, *, invoice: Invoice, sort_order: int, vendor_code: tuple[str, str], entered_code: str,
    description: str | None, proposed_by: str,
) -> ProductDataProposal:
    """A PENDING vendor_item proposal: this vendor's item number is the product with this barcode."""
    if invoice.vendor_id is None:
        raise CrosswalkRefusedError(message="This invoice has no vendor, so a vendor item cannot be remembered.",
                               detail={"reason": "no_vendor"})
    if invoice.store_id is None:
        raise CrosswalkRefusedError(message="Assign the invoice's store first.", detail={"reason": "no_store"})
    item_code, upc = target_from_entry(entered_code)
    key, raw = vendor_code
    current = await confirmed_link(session, invoice.vendor_id, key)
    return await ProductDataProposalRepository(session).create(
        store_id=invoice.store_id, entity_type=ENTITY_VENDOR_ITEM, entity_key=key[:64], field=FIELD_ITEM_CODE,
        proposed_value=item_code, current_value=current.item_code if current else None, source=SOURCE_UPC_ENTRY,
        proposed_by=proposed_by, invoice_id=invoice.id, vendor_id=invoice.vendor_id,
        evidence={"vendor_item_code_raw": raw[:128], "upc": upc, "entered": entered_code,
                  "sort_order": sort_order, "invoice_number": invoice.invoice_number,
                  "invoice_description": description},
        reason=f"UPC entered for vendor item {raw} on invoice {invoice.invoice_number}.",
    )


async def confirm_from_proposal(session: AsyncSession, proposal: ProductDataProposal, *, reviewer: str,
                                reviewer_role: str | None = None) -> VendorItemCrosswalk:
    """
    Write the CONFIRMED link an APPROVED vendor_item proposal stands for. An
    identical confirmed link is returned unchanged; a different one is retired
    (with the reason) and replaced — the proposal recorded what it replaced.
    """
    if proposal.vendor_id is None:
        raise CrosswalkRefusedError(message="A vendor item proposal must name its vendor.", detail={"reason": "no_vendor"})
    item_code = str(proposal.proposed_value)
    if not (len(item_code) == 11 and item_code.isdigit()):
        raise CrosswalkRefusedError(message="The proposed item code is not an 11-digit PDI code.",
                               detail={"reason": "bad_item_code", "value": item_code})
    evidence = dict(proposal.evidence or {})
    existing = await confirmed_link(session, proposal.vendor_id, proposal.entity_key)
    now = datetime.now(UTC)
    if existing is not None:
        if existing.item_code == item_code:
            return existing
        existing.state = CROSSWALK_RETIRED
        existing.retired_at = now
        existing.retired_by = reviewer[:128]
        existing.retire_reason = f"Replaced by proposal {proposal.id} ({existing.item_code} -> {item_code})."
        await session.flush()
    upc = evidence.get("upc") or None
    product = (await session.execute(select(MasterProduct).where(
        MasterProduct.canonical_upc == item_code + upc_a_check_digit(item_code)))).scalars().first()
    link = VendorItemCrosswalk(
        vendor_id=proposal.vendor_id, vendor_item_code=proposal.entity_key,
        vendor_item_code_raw=str(evidence.get("vendor_item_code_raw") or proposal.entity_key)[:128],
        item_code=item_code, upc=upc if isinstance(upc, str) and len(upc) <= 14 else None,
        product_id=product.id if product else None, state=CROSSWALK_CONFIRMED, proposal_id=proposal.id,
        confirmed_by=reviewer[:128], confirmed_by_role=reviewer_role, confirmed_at=now,
        evidence={k: evidence.get(k) for k in ("invoice_number", "sort_order", "invoice_description", "entered")}
        | {"invoice_id": str(proposal.invoice_id or "")},
    )
    session.add(link)
    await session.flush()
    return link


async def retire(session: AsyncSession, link_id: uuid.UUID, *, reason: str, retired_by: str) -> VendorItemCrosswalk:
    """Stop applying a link. Nothing it already wrote is changed: those are recorded line corrections."""
    reason = (reason or "").strip()
    if not reason:
        raise CrosswalkRefusedError(message="Say why the link is wrong; the reason is kept.", detail={"reason": "required"})
    link = await session.get(VendorItemCrosswalk, link_id, with_for_update=True)
    if link is None or link.state != CROSSWALK_CONFIRMED:
        raise CrosswalkRefusedError(message="Only a confirmed link can be retired.", detail={"reason": "not_confirmed"})
    link.state = CROSSWALK_RETIRED
    link.retired_at = datetime.now(UTC)
    link.retired_by = retired_by[:128]
    link.retire_reason = reason[:1000]
    await session.flush()
    return link


# ---- applying ---------------------------------------------------------------------------

@dataclass
class ApplyResult:
    applied: list[int] = field(default_factory=list)                      # sort orders given a UPC
    conflicts: list[dict[str, Any]] = field(default_factory=list)         # printed barcode disagrees with a link


async def apply_to_invoice(session: AsyncSession, invoice: Invoice, *, links: dict[str, VendorItemCrosswalk] | None = None) -> ApplyResult:
    """
    Give each of the invoice's lines the confirmed UPC for its vendor item, as
    a recorded correction. The caller has already checked the invoice may
    change, revalidates afterwards when anything was applied, and commits.
    """
    from app.repositories.invoice_repository import (
        InvoiceRepository,  # local: repository imports services
    )

    result = ApplyResult()
    if invoice.vendor_id is None:
        return result
    candidates: list[tuple[Any, tuple[str, str]]] = []
    for item in invoice.items:
        if (item.line_type or "product") in NON_PDI_LINE_TYPES:
            continue
        code = vendor_code_for(getattr(item, "supplier_item_id", None), item.product_sku)
        if code is not None:
            candidates.append((item, code))
    if not candidates:
        return result
    if links is None:
        rows = (await session.execute(select(VendorItemCrosswalk).where(
            VendorItemCrosswalk.vendor_id == invoice.vendor_id, VendorItemCrosswalk.state == CROSSWALK_CONFIRMED,
            VendorItemCrosswalk.vendor_item_code.in_({c[0] for _, c in candidates}),
        ))).scalars().all()
        links = {row.vendor_item_code: row for row in rows}
    repository = InvoiceRepository(session)
    for item, (key, raw) in candidates:
        link = links.get(key)
        if link is None:
            continue
        printed = gtin.classify(item.product_sku)
        if printed.exportable:
            if printed.item_code != link.item_code:
                result.conflicts.append({"sort_order": item.sort_order, "printed": item.product_sku,
                                         "link_item_code": link.item_code, "vendor_item_code": raw})
            continue
        if printed.kind in (gtin.KIND_UPC_A_BAD_CHECK, gtin.KIND_BAD_CHECK):
            continue                       # a misread barcode is corrected by a person, never crosswalked
        await repository.correct_item(
            invoice.id, item.sort_order, {"product_code": link.upc or link.item_code},
            corrected_by=APPLIED_BY, note=f"UPC from confirmed vendor item {raw} (link {link.id}).",
        )
        result.applied.append(item.sort_order)
    if result.conflicts:
        logger.warning("vendor_item_crosswalk_conflicts", invoice_id=str(invoice.id), conflicts=result.conflicts)
    return result


async def apply_to_open_invoices(session: AsyncSession, link: VendorItemCrosswalk, *,
                                 exclude_invoice_id: uuid.UUID | None = None) -> list[uuid.UUID]:
    """
    Apply a newly confirmed link to the vendor's other open invoices carrying
    that item number. Skips invoices that may not change; revalidates each one
    that did. Returns the invoices changed.
    """
    from app.repositories.invoice_repository import InvoiceRepository
    from app.services.document_lifecycle import ensure_invoice_operable
    from app.services.inventory_service import ensure_invoice_changeable
    from app.services.revalidation_service import revalidate_invoice

    raw_digits = gtin.classify(link.vendor_item_code_raw).digits
    statement = (
        select(Invoice).join(InvoiceItem, InvoiceItem.invoice_id == Invoice.id)
        .where(Invoice.vendor_id == link.vendor_id, document_is_active(Invoice.document_id),
               ~exists().where(EdiExport.invoice_id == Invoice.id))
        .options(selectinload(Invoice.items)).distinct()
    )
    if exclude_invoice_id is not None:
        statement = statement.where(Invoice.id != exclude_invoice_id)
    changed: list[uuid.UUID] = []
    for invoice in (await session.execute(statement)).scalars().unique().all():
        offers = {vendor_code_for(getattr(i, "supplier_item_id", None), i.product_sku) for i in invoice.items}
        if not any(o and (o[0] == link.vendor_item_code or (raw_digits and o[0] == raw_digits)) for o in offers):
            continue
        try:
            async with session.begin_nested():
                await ensure_invoice_changeable(session, invoice.id, "apply_vendor_item_crosswalk")
                await ensure_invoice_operable(session, invoice.id, "apply_vendor_item_crosswalk")
                result = await apply_to_invoice(session, invoice, links={link.vendor_item_code: link})
                if result.applied:
                    await revalidate_invoice(session, await InvoiceRepository(session).get_detail(invoice.id))
                    changed.append(invoice.id)
        except InvoiceBaseException as exc:
            logger.info("vendor_item_crosswalk_skipped", invoice_id=str(invoice.id), reason=str(exc)[:200])
    return changed
