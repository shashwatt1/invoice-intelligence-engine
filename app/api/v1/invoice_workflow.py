"""
Invoice workflow endpoints — app/api/v1/invoice_workflow.py

  PUT  /invoices/{id}/reference-link   save or clear the original invoice / chat link
  POST /invoices/{id}/retailed         mark Retailed (ledger-proven PDI export only)
  POST /invoices/{id}/retailed/reopen  remove the Retailed mark, with an optional reason

MANAGER or ADMIN only, on an invoice the user may see. Invoice-scoped: nothing
here touches inventory, a mapping, or Product Master. See
app/services/invoice_workflow_service.py for the rules.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.invoices import completion_data, reference_link_data
from app.core.dependencies import require_manager
from app.core.exceptions import RecordNotFoundError
from app.database.session import get_db
from app.models.user import User
from app.repositories.invoice_repository import InvoiceRepository
from app.schemas.base import APIResponse
from app.schemas.processing import (
    CompletionData,
    ReferenceLinkData,
    ReferenceLinkUpdate,
    RetailReopenRequest,
)
from app.services.document_lifecycle import ensure_document_visible
from app.services.invoice_workflow_service import mark_retailed, reopen_retailed, set_reference_link

router = APIRouter(tags=["Invoices"])


async def _visible_invoice(db: AsyncSession, invoice_id: uuid.UUID, user: User):
    invoice = await InvoiceRepository(db).get_detail(invoice_id)
    if invoice is None:
        raise RecordNotFoundError(message="Invoice not found.", detail={"invoice_id": str(invoice_id)})
    ensure_document_visible(invoice.document, user)
    return invoice


@router.put(
    "/invoices/{invoice_id}/reference-link",
    response_model=APIResponse[ReferenceLinkData | None],
    summary="Save or remove the original invoice / chat link (MANAGER/ADMIN)",
    description=("Stores an https:// URL with who saved it and when. The link is never fetched, previewed or "
                 "checked: a private chat link still needs the viewer's own access to the message."),
    responses={404: {"description": "Invoice not found"}, 422: {"description": "Not an acceptable https:// URL"}},
)
async def save_reference_link(
    invoice_id: uuid.UUID,
    body: ReferenceLinkUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
) -> APIResponse[ReferenceLinkData | None]:
    invoice = await _visible_invoice(db, invoice_id, user)
    await set_reference_link(db, invoice, body.url, user)
    await db.commit()
    return APIResponse(data=reference_link_data(invoice))


@router.post(
    "/invoices/{invoice_id}/retailed",
    response_model=APIResponse[CompletionData],
    summary="Mark an invoice Retailed (MANAGER/ADMIN)",
    description=("Workflow completion, not goods receipt — no inventory, mapping or Product Master effect. "
                 "Allowed only when the EDI export ledger records a delivered PDI file identical to the one the "
                 "invoice produces now."),
    responses={404: {"description": "Invoice not found"}, 422: {"description": "Not provably complete, or already marked"}},
)
async def mark_invoice_retailed(
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
) -> APIResponse[CompletionData]:
    await _visible_invoice(db, invoice_id, user)
    state = await mark_retailed(db, invoice_id, user)
    if state is None:
        raise RecordNotFoundError(message="Invoice not found.", detail={"invoice_id": str(invoice_id)})
    await db.commit()
    return APIResponse(data=completion_data(state))


@router.post(
    "/invoices/{invoice_id}/retailed/reopen",
    response_model=APIResponse[CompletionData],
    summary="Remove the Retailed mark (MANAGER/ADMIN)",
    responses={404: {"description": "Invoice not found"}, 422: {"description": "Not marked Retailed"}},
)
async def reopen_invoice_retailed(
    invoice_id: uuid.UUID,
    body: RetailReopenRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
) -> APIResponse[CompletionData]:
    await _visible_invoice(db, invoice_id, user)
    state = await reopen_retailed(db, invoice_id, user, body.note)
    if state is None:
        raise RecordNotFoundError(message="Invoice not found.", detail={"invoice_id": str(invoice_id)})
    await db.commit()
    return APIResponse(data=completion_data(state))
