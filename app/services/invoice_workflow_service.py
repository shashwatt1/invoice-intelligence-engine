"""
Invoice workflow — app/services/invoice_workflow_service.py

Two invoice-scoped workflow facts, both set by a MANAGER or ADMIN:

1. The original invoice / chat link. An https:// URL a reviewer pastes so
   the source image or its chat message can be reopened while mapping. It is
   only stored and shown as a link: never fetched, previewed or checked for
   reachability (a private chat link still needs the viewer's own access).

2. Retailed — the team's "this invoice is finished" mark. It is a workflow
   completion status, NOT goods receipt: it never creates or changes
   inventory, and never touches a mapping or Product Master.

   It can be set only when the EDI export ledger proves the work is done: a
   delivered PDI file is recorded for the invoice, and the PDI that would be
   built from the invoice right now is byte-for-byte that file (same SHA-256).
   A blocked export writes no ledger row, so a failed attempt can never count.
   With the ledger off there is no proof, so nothing can be marked.

   It is never trusted blindly afterwards: the SHA-256 of the file it was set
   against is stored, and the status reads "retailed" only while the current
   PDI still matches. Any later change to the invoice or to its mappings that
   would change the file shows it as "retailed_stale" until a person exports
   again and re-marks it, or reopens it.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import ValidationError
from app.models.edi_export import EdiExport
from app.models.invoice import Invoice
from app.models.processing_log import PipelineStage
from app.repositories.invoice_repository import InvoiceRepository
from app.repositories.processing_log_repository import ProcessingLogRepository
from app.services.case_mapping_service import invoice_commercial_resolution
from app.services.export_service import build_pdi_export, persisted_pdi_export_eligibility

MAX_REFERENCE_URL = 2048

STATUS_NOT_RETAILED = "not_retailed"
STATUS_RETAILED = "retailed"
STATUS_RETAILED_STALE = "retailed_stale"


# ---- reference link ----------------------------------------------------------------------------

def validate_reference_url(value: str | None) -> str | None:
    """The URL to store, or None to clear it. Only absolute https:// URLs; no credentials, no whitespace."""
    if value is None or not value.strip():
        return None
    url = value.strip()
    problem = None
    if len(url) > MAX_REFERENCE_URL:
        problem = f"The link is longer than {MAX_REFERENCE_URL} characters."
    elif any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in url):
        problem = "The link must not contain spaces or control characters."
    else:
        parts = urlsplit(url)
        if parts.scheme.lower() != "https" or not url.lower().startswith("https://"):
            problem = "Only https:// links can be saved."
        elif not parts.hostname:
            problem = "The link has no host."
        elif parts.username is not None or parts.password is not None:
            problem = "The link must not contain a username or password."
    if problem:
        raise ValidationError(message=problem, detail={"field": "reference_url"})
    return url


async def set_reference_link(session: AsyncSession, invoice: Invoice, value: str | None, user) -> Invoice:
    """Save (or clear) the link with who and when; logged on the invoice's audit trail. Flushes, never commits."""
    url = validate_reference_url(value)
    previous = invoice.reference_url
    invoice.reference_url = url
    invoice.reference_url_set_by = user.username if url else None
    invoice.reference_url_set_by_user_id = user.id if url else None
    invoice.reference_url_set_at = datetime.now(UTC) if url else None
    await ProcessingLogRepository(session).add(
        document_id=invoice.document_id,
        stage=PipelineStage.LIFECYCLE,
        message=f"Original invoice link {'saved' if url else 'removed'} by {user.username}.",
        payload={"event": "reference_link_saved" if url else "reference_link_removed", "by": user.username,
                 "had_previous": previous is not None},
    )
    await session.flush()
    return invoice


# ---- Retailed ----------------------------------------------------------------------------------

@dataclass(frozen=True)
class CompletionState:
    status: str                         # STATUS_*
    can_mark: bool
    reason: str | None                  # why it cannot be marked now
    latest_export_number: int | None
    latest_export_at: datetime | None
    ledger_enabled: bool
    retailed_at: datetime | None
    retailed_by: str | None


def pdi_sha256(content: str) -> str:
    """Exactly how the ledger hashes a delivered file (edi_export_ledger.record_pdi_export)."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def status_for(invoice: Invoice, current_sha: str | None) -> str:
    """Retailed only while the file it was marked against is still the file the invoice would produce."""
    if invoice.retailed_at is None:
        return STATUS_NOT_RETAILED
    return STATUS_RETAILED if current_sha is not None and current_sha == invoice.retailed_pdi_sha256 \
        else STATUS_RETAILED_STALE


async def current_pdi_sha(session: AsyncSession, invoice: Invoice) -> tuple[str | None, str | None]:
    """(SHA-256 of the PDI the invoice would produce now, why none) — the export endpoint's own gate and builder."""
    if invoice.store_id is None:
        return None, "The invoice has no store yet."
    resolution = await invoice_commercial_resolution(session, invoice)
    eligibility = persisted_pdi_export_eligibility(invoice, resolution.units)
    if not eligibility.allowed:
        return None, eligibility.blocked_reason or "The invoice cannot be exported for PDI right now."
    return pdi_sha256(build_pdi_export(invoice, resolution.units)), None


async def latest_export(session: AsyncSession, invoice_id: uuid.UUID) -> EdiExport | None:
    return (await session.execute(
        select(EdiExport).where(EdiExport.invoice_id == invoice_id)
        .order_by(EdiExport.export_number.desc()).limit(1)
    )).scalar_one_or_none()


async def completion_state(session: AsyncSession, invoice: Invoice) -> CompletionState:
    ledger_enabled = get_settings().edi_export_ledger
    current_sha, blocked = await current_pdi_sha(session, invoice)
    latest = await latest_export(session, invoice.id)
    status = status_for(invoice, current_sha)

    reason: str | None
    if status == STATUS_RETAILED:
        reason = "Already marked Retailed."
    elif current_sha is None:
        reason = f"The invoice cannot be exported now: {blocked}"
    elif latest is None:
        reason = "No delivered PDI file is recorded for this invoice yet." + (
            "" if ledger_enabled else " The EDI export ledger is off, so exports are not recorded.")
    elif latest.sha256 != current_sha:
        reason = (f"The invoice or its mappings changed after the last recorded export (#{latest.export_number}). "
                  "Generate the PDI file again, then mark it.")
    else:
        reason = None
    return CompletionState(
        status=status, can_mark=reason is None, reason=reason,
        latest_export_number=latest.export_number if latest else None,
        latest_export_at=latest.created_at if latest else None,
        ledger_enabled=ledger_enabled,
        retailed_at=invoice.retailed_at, retailed_by=invoice.retailed_by,
    )


async def _locked(session: AsyncSession, invoice_id: uuid.UUID) -> Invoice | None:
    """
    Lock the invoice row for the rest of the transaction, then load it: two
    concurrent marks are decided one at a time, each on the state it can see.
    """
    found = (await session.execute(
        select(Invoice.id).where(Invoice.id == invoice_id).with_for_update())).scalar_one_or_none()
    return await InvoiceRepository(session).get_detail(invoice_id) if found else None


async def mark_retailed(session: AsyncSession, invoice_id: uuid.UUID, user) -> CompletionState | None:
    """
    Mark the invoice Retailed against its current, recorded PDI file. Refused
    unless the ledger proves it. None when the invoice does not exist.
    """
    invoice = await _locked(session, invoice_id)
    if invoice is None:
        return None
    state = await completion_state(session, invoice)
    if not state.can_mark:
        raise ValidationError(message=state.reason or "This invoice cannot be marked Retailed now.",
                              detail={"invoice_id": str(invoice.id), "status": state.status})
    latest = await latest_export(session, invoice.id)
    assert latest is not None                                # can_mark guarantees it
    invoice.retailed_at = datetime.now(UTC)
    invoice.retailed_by = user.username
    invoice.retailed_by_user_id = user.id
    invoice.retailed_edi_export_id = latest.id
    invoice.retailed_pdi_sha256 = latest.sha256
    await ProcessingLogRepository(session).add(
        document_id=invoice.document_id,
        stage=PipelineStage.LIFECYCLE,
        message=f"Marked Retailed by {user.username} (PDI export #{latest.export_number}).",
        payload={"event": "invoice_retailed", "by": user.username, "edi_export_id": str(latest.id),
                 "export_number": latest.export_number, "sha256": latest.sha256,
                 "previous_status": state.status},
    )
    await session.flush()
    return await completion_state(session, invoice)


async def reopen_retailed(session: AsyncSession, invoice_id: uuid.UUID, user,
                          note: str | None) -> CompletionState | None:
    """Clear the Retailed mark explicitly (the invoice needs more work). Logged with who and why."""
    invoice = await _locked(session, invoice_id)
    if invoice is None:
        return None
    if invoice.retailed_at is None:
        raise ValidationError(message="This invoice is not marked Retailed.", detail={"invoice_id": str(invoice.id)})
    previous = {"retailed_at": invoice.retailed_at.isoformat(), "retailed_by": invoice.retailed_by,
                "edi_export_id": str(invoice.retailed_edi_export_id) if invoice.retailed_edi_export_id else None}
    invoice.retailed_at = None
    invoice.retailed_by = None
    invoice.retailed_by_user_id = None
    invoice.retailed_edi_export_id = None
    invoice.retailed_pdi_sha256 = None
    await ProcessingLogRepository(session).add(
        document_id=invoice.document_id,
        stage=PipelineStage.LIFECYCLE,
        message=f"Retailed mark removed by {user.username}.",
        payload={"event": "invoice_retail_reopened", "by": user.username, "note": note, "previous": previous},
    )
    await session.flush()
    return await completion_state(session, invoice)
