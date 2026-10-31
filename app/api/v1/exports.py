"""
Invoice Export Endpoint — app/api/v1/exports.py

    GET /invoices/{id}/export?format=json|txt|csv|pdi

Read-only, additive: serves the final validated invoice (as persisted)
in ERP-consumable formats with download-friendly Content-Disposition
headers. Reuses InvoiceRepository — no business logic here.
"""

from __future__ import annotations

import json
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.dependencies import require_manager
from app.core.exceptions import RecordNotFoundError, ValidationError
from app.database.session import get_db
from app.models.edi_export import EdiExport
from app.models.invoice import Invoice
from app.models.user import User
from app.repositories.invoice_repository import InvoiceRepository
from app.schemas.base import APIResponse
from app.schemas.processing import EdiExportRecord
from app.services.case_mapping_service import invoice_commercial_resolution
from app.services.document_lifecycle import ensure_document_operable
from app.services.edi_export_ledger import record_pdi_export
from app.services.export_override_service import active_override, log_override_export
from app.services.export_service import (
    build_export_payload,
    build_items_csv,
    build_pdi_export,
    build_txt,
    critical_check_blockers,
    critical_check_gate,
    export_basename,
    override_banner,
    persisted_pdi_export_eligibility,
    review_banner,
    unmapped_item_codes,
)

router = APIRouter(tags=["Invoices"])

ExportFormat = Literal["json", "txt", "csv", "pdi"]
ExportPurpose = Literal["export", "review"]

REVIEW_FILE_TAG = "REVIEW-ONLY_not-for-import"
OVERRIDE_FILE_TAG = "admin-override"


@router.get(
    "/invoices/{invoice_id}/export",
    summary="Export the validated structured invoice",
    description=(
        "The final validated invoice object (post-validation, as persisted) "
        "in an ERP-consumable format.\n\n"
        "- `format=json` — structured invoice document (default)\n"
        "- `format=txt` — human-readable summary\n"
        "- `format=csv` — line items for accounting systems\n"
        "- `format=pdi` — fixed-width PDI import format (header and detail "
        "record byte layout confirmed against real PDI samples; item code, "
        "description, quantity, batch number, and return/credit sign are "
        "confirmed; cost fields and CFUE/CPPT trailer content are emitted as "
        "documented zero-value placeholders — evidence shows they encode "
        "product-master data not present on a supplier invoice, see "
        "docs/PDI_DATA_CONTRACT.md before relying on this for a live import). "
        "Available for VALIDATED and REVIEW_REQUIRED invoices with at least "
        "one extracted line item; blocked only when there's no usable "
        "extracted data (see pdi_export_allowed on GET /invoices/{id}).\n\n"
        "PDI, TXT and CSV files share one rule: while the invoice's critical checks (completeness, "
        "financial reconciliation) fail they are refused (422) unless an administrator has recorded an "
        "export override for the invoice as it stands. JSON is the structured record and stays readable; it "
        "states `export_status.export_ready` itself.\n\n"
        "`purpose=review` downloads a TXT or CSV copy for internal investigation even while blocked. It is "
        "labelled inside the file and in its filename (`REVIEW-ONLY_not-for-import`) and header "
        "(`X-Export-Purpose: review-only`), so it cannot be taken for an export. There is no review copy of the "
        "PDI file.\n\n"
        "Responses carry a `Content-Disposition` attachment header with a "
        "filename derived from the invoice number."
    ),
    responses={
        200: {
            "content": {
                "application/json": {},
                "text/plain": {},
                "text/csv": {},
            }
        },
        404: {"description": "Invoice not found"},
        422: {"description": "format=pdi requested for an invoice with no usable extracted data"},
    },
)
async def export_invoice(
    invoice_id: uuid.UUID,
    format: ExportFormat = Query(default="json", description="Export format."),
    purpose: ExportPurpose = Query(
        default="export", description="`review` = a labelled internal-investigation copy (TXT/CSV only)."),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
) -> Response:
    review = purpose == "review"
    delivering = not review and format in ("pdi", "txt", "csv")
    if delivering:
        # Share-lock the invoice row for the whole request, taken BEFORE the invoice is read. Every writer of the
        # invoice (edits, receipts, a revalidation that sets blocking_checks) takes the exclusive lock first, so:
        # a revalidation that commits first is seen by this export (it is read afterwards and refused if blocked);
        # one that starts while an export is in flight waits for it. Exports share the lock with each other, and
        # JSON and review copies, which deliver no file, take none.
        await db.execute(select(Invoice.id).where(Invoice.id == invoice_id).with_for_update(read=True))
    invoice = await InvoiceRepository(db).get_detail(invoice_id, fresh=delivering)
    if invoice is None:
        raise RecordNotFoundError(
            message="Invoice not found.", detail={"invoice_id": str(invoice_id)}
        )

    if review and format not in ("txt", "csv"):
        raise ValidationError(
            message="A review copy exists only as TXT or CSV. A PDI file is produced only for an export-ready "
                    "invoice.",
            detail={"invoice_id": str(invoice.id), "format": format, "purpose": purpose})
    if format in ("pdi", "csv") and not review:
        # Files for EDI and accounting systems are never produced for an invoice in the Recycle Bin or for a
        # reference copy, so nothing is imported twice. JSON, the text summary and review copies stay readable.
        ensure_document_operable(invoice.document, f"export_{format}")

    # One rule for every file that leaves the system: failing critical checks block it unless an admin override
    # covers this exact state of the invoice. A review copy skips the rule because it is labelled as not
    # export-ready; JSON is the structured record and is never gated (it states export_status itself).
    found = None if review else await active_override(db, invoice)
    override = found if found is not None and critical_check_blockers(invoice) else None
    if format in ("txt", "csv") and not review:
        blocked, _ = critical_check_gate(invoice, override)
        if blocked:
            raise ValidationError(
                message=blocked,
                detail={"invoice_id": str(invoice.id), "blocking_checks": critical_check_blockers(invoice),
                        "review_copy": f"/api/v1/invoices/{invoice.id}/export?format={format}&purpose=review"})
    headers: dict[str, str] = {}
    suffix = ""
    if review:
        headers = {"X-Export-Purpose": "review-only", "X-Export-Ready": "false"}
        suffix = f"_{REVIEW_FILE_TAG}"
    elif override is not None:
        headers = {"X-Export-Override-Id": str(override.id)}
        suffix = f"_{OVERRIDE_FILE_TAG}" if format != "pdi" else ""
    basename = export_basename(invoice)

    if format == "txt":
        content = build_txt(invoice, banner=review_banner(invoice) if review else
                            override_banner(override) if override is not None else None, override=override)
        media_type = "text/plain"
        filename = f"{basename}{suffix}.txt"
    elif format == "csv":
        content = build_items_csv(invoice, banner=review_banner(invoice) if review else None)
        media_type = "text/csv"
        filename = f"{basename}_items{suffix}.csv"
    elif format == "pdi":
        resolution = await invoice_commercial_resolution(db, invoice)
        units = resolution.units
        eligibility = persisted_pdi_export_eligibility(invoice, units, override)
        if not eligibility.allowed:
            raise ValidationError(
                message=eligibility.blocked_reason
                or "This invoice cannot be exported for PDI import.",
                detail={
                    "invoice_id": str(invoice.id),
                    "status": invoice.status,
                    "unmapped_item_codes": unmapped_item_codes(invoice, units),
                },
            )
        content = build_pdi_export(invoice, units)
        media_type = "text/plain"
        filename = f"{basename}_pdi.txt"
        if get_settings().edi_export_ledger:
            # The delivered file is recorded before it leaves, exactly as sent.
            record = await record_pdi_export(db, invoice, content, resolution, user, override=override)
            headers = {**headers, "X-EDI-Export-Id": str(record.id), "X-EDI-Export-Number": str(record.export_number),
                       "X-EDI-SHA256": record.sha256}
            if override is not None:
                await log_override_export(db, invoice, override, user, export_format="pdi", content=content,
                                          export_number=record.export_number)
            await db.commit()
        elif override is not None:
            await log_override_export(db, invoice, override, user, export_format="pdi", content=content)
            await db.commit()
    else:
        content = json.dumps(build_export_payload(invoice, override), indent=2, ensure_ascii=False)
        media_type = "application/json"
        filename = f"{basename}.json"

    if override is not None and format in ("txt", "csv"):  # the PDI branch logs its own, with the ledger row
        await log_override_export(db, invoice, override, user, export_format=format, content=content)
        await db.commit()

    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"', **headers},
    )


@router.get(
    "/invoices/{invoice_id}/edi-exports",
    response_model=APIResponse[list[EdiExportRecord]],
    summary="Every PDI file delivered for this invoice, newest first (MANAGER/ADMIN)",
)
async def list_edi_exports(
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
) -> APIResponse[list[EdiExportRecord]]:
    rows = (await db.execute(
        select(EdiExport).where(EdiExport.invoice_id == invoice_id).order_by(EdiExport.export_number.desc())
    )).scalars().all()
    return APIResponse(data=[EdiExportRecord.model_validate(r, from_attributes=True) for r in rows])


@router.get(
    "/edi-exports/{export_id}/file",
    summary="The exact PDI file that was delivered, byte for byte (MANAGER/ADMIN)",
    responses={200: {"content": {"text/plain": {}}}, 404: {"description": "Not found"}},
)
async def download_edi_export(
    export_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
) -> Response:
    row = await db.get(EdiExport, export_id)
    if row is None:
        raise RecordNotFoundError(message="EDI export not found.", detail={"export_id": str(export_id)})
    stem = row.invoice_number or str(row.invoice_id or row.id)[:8]
    return Response(
        content=row.content, media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{stem}_pdi_export{row.export_number}.txt"',
                 "X-EDI-SHA256": row.sha256},
    )
