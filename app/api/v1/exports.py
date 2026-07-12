"""
Invoice Export Endpoint — app/api/v1/exports.py

    GET /invoices/{id}/export?format=json|txt|csv

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

from app.core.exceptions import RecordNotFoundError
from app.database.session import get_db
from app.repositories.invoice_repository import InvoiceRepository
from app.services.export_service import (
    build_export_payload,
    build_items_csv,
    build_txt,
    export_basename,
)

router = APIRouter(tags=["Invoices"])

ExportFormat = Literal["json", "txt", "csv"]


@router.get(
    "/invoices/{invoice_id}/export",
    summary="Export the validated structured invoice",
    description=(
        "The final validated invoice object (post-validation, as persisted) "
        "in an ERP-consumable format.\n\n"
        "- `format=json` — structured invoice document (default)\n"
        "- `format=txt` — human-readable summary\n"
        "- `format=csv` — line items for accounting systems\n\n"
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
    else:
        content = json.dumps(build_export_payload(invoice), indent=2, ensure_ascii=False)
        media_type = "application/json"
        filename = f"{basename}.json"

    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
