"""
Export overrides — app/api/v1/export_overrides.py

    POST /invoices/{id}/export-override      grant (ADMIN, reason required)
    GET  /invoices/{id}/export-overrides     history, newest first (MANAGER/ADMIN)
    POST /export-overrides/{id}/revoke       withdraw (ADMIN, reason required)

An override lets ONE exact state of an invoice whose critical checks fail be exported (PDI, TXT, CSV). It never
changes the invoice, its status or its validation results; see app/services/export_override_service.py.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import require_admin, require_manager
from app.core.exceptions import RecordNotFoundError
from app.database.session import get_db
from app.models.user import User
from app.repositories.invoice_repository import InvoiceRepository
from app.schemas.base import APIResponse
from app.schemas.processing import ExportOverrideRecord, ExportOverrideRequest
from app.services.export_override_service import (
    grant_override,
    list_overrides,
    override_state,
    revoke_override,
)

router = APIRouter(tags=["Invoices"])


def _record(row, invoice) -> ExportOverrideRecord:
    return ExportOverrideRecord(
        id=row.id, state=override_state(row, invoice), reason=row.reason, blocked_checks=list(row.blocked_checks),
        granted_by=row.granted_by, created_at=row.created_at, invoice_id=row.invoice_id,
        invoice_number=row.invoice_number, granted_by_role=row.granted_by_role,
        validation_snapshot=row.validation_snapshot, revoked_at=row.revoked_at, revoked_by=row.revoked_by,
        revoke_reason=row.revoke_reason)


@router.post(
    "/invoices/{invoice_id}/export-override",
    response_model=APIResponse[ExportOverrideRecord],
    summary="Allow this invoice, as it stands, to be exported although its critical checks fail (ADMIN)",
    description=(
        "Records who allows it and why, and keeps the failed validation results as they were. The invoice itself "
        "is not changed: it stays REVIEW_REQUIRED with the same failed checks, and cannot be marked Retailed. The "
        "override stops applying as soon as the invoice changes."
    ),
)
async def create_export_override(
    invoice_id: uuid.UUID,
    body: ExportOverrideRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
) -> APIResponse[ExportOverrideRecord]:
    row = await grant_override(db, invoice_id, user, body.reason)
    invoice = await InvoiceRepository(db).get_detail(invoice_id)
    record = _record(row, invoice)
    await db.commit()
    return APIResponse(data=record)


@router.get(
    "/invoices/{invoice_id}/export-overrides",
    response_model=APIResponse[list[ExportOverrideRecord]],
    summary="Every export override recorded for this invoice, newest first (MANAGER/ADMIN)",
)
async def get_export_overrides(
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
) -> APIResponse[list[ExportOverrideRecord]]:
    invoice = await InvoiceRepository(db).get_detail(invoice_id)
    rows = await list_overrides(db, invoice_id)
    if invoice is None and not rows:
        raise RecordNotFoundError(message="Invoice not found.", detail={"invoice_id": str(invoice_id)})
    return APIResponse(data=[_record(r, invoice) for r in rows])


@router.post(
    "/export-overrides/{override_id}/revoke",
    response_model=APIResponse[ExportOverrideRecord],
    summary="Withdraw an export override (ADMIN)",
)
async def revoke_export_override(
    override_id: uuid.UUID,
    body: ExportOverrideRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
) -> APIResponse[ExportOverrideRecord]:
    row = await revoke_override(db, override_id, user, body.reason)
    invoice = await InvoiceRepository(db).get_detail(row.invoice_id) if row.invoice_id else None
    record = _record(row, invoice)
    await db.commit()
    return APIResponse(data=record)
