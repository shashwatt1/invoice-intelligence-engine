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
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import RecordNotFoundError, ValidationError
from app.database.session import get_db
from app.repositories.invoice_repository import InvoiceRepository
from app.services.case_mapping_service import invoice_units_by_item_code
from app.services.export_service import (
    build_export_payload,
    build_items_csv,
    build_pdi_export,
    build_txt,
    export_basename,
    pdi_export_eligibility,
    unmapped_item_codes,
)

router = APIRouter(tags=["Invoices"])

ExportFormat = Literal["json", "txt", "csv", "pdi"]


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
    db: AsyncSession = Depends(get_db),
) -> Response:
    invoice = await InvoiceRepository(db).get_detail(invoice_id)
    if invoice is None:
        raise RecordNotFoundError(
            message="Invoice not found.", detail={"invoice_id": str(invoice_id)}
        )

    basename = export_basename(invoice)
    if format == "txt":
        content = build_txt(invoice)
        media_type = "text/plain"
        filename = f"{basename}.txt"
    elif format == "csv":
        content = build_items_csv(invoice)
        media_type = "text/csv"
        filename = f"{basename}_items.csv"
    elif format == "pdi":
        units = await invoice_units_by_item_code(db, invoice)
        eligibility = pdi_export_eligibility(invoice, units)
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
    else:
        content = json.dumps(build_export_payload(invoice), indent=2, ensure_ascii=False)
        media_type = "application/json"
        filename = f"{basename}.json"

    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
