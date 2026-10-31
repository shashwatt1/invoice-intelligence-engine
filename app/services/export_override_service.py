"""
Export override — app/services/export_override_service.py

While an invoice's critical checks fail (completeness, financial reconciliation) no file leaves the system: PDI,
TXT and CSV share one rule (export_service.critical_check_gate). An ADMIN can record, with a required reason, that
ONE exact state of the invoice may be exported anyway. This module owns that record.

What an override is, and is not
-------------------------------
* It is a ledger row (ExportOverride): who, when, why, which checks were failing, and the latest stored validation
  report verbatim. It is never an edit of the invoice: status, `blocking_checks`, confidence and the validation log
  stay exactly as the checks left them, so an incomplete invoice is never silently turned into a validated one.
* It covers a fingerprint of the invoice as reviewed (figures, lines, charges, failing checks). Any correction,
  revalidation that changes the result, or reprocess changes the fingerprint and the override stops applying by
  itself — the next export is blocked until an administrator looks again.
* It lifts only the critical-check block. Every PDI-specific rule (mapping, cost, quantity, identity, store) still
  applies, and Mark Retailed — hence inventory receipt — still requires a VALIDATED invoice and no failing critical
  checks: an override can export a file; it cannot attest the invoice as received.
* Rows are written once; the only later change is revocation (who, when, why). Every grant, revocation and
  override-authorised export is also written to the document's processing log.
"""

from __future__ import annotations

import copy
import hashlib
import json
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import PermissionDeniedError, RecordNotFoundError, ValidationError
from app.models.export_override import ExportOverride
from app.models.invoice import Invoice
from app.models.processing_log import PipelineStage
from app.models.user import UserRole
from app.repositories.invoice_repository import InvoiceRepository
from app.repositories.processing_log_repository import ProcessingLogRepository
from app.services.document_lifecycle import ensure_document_operable
from app.services.export_service import critical_check_blockers
from app.services.invoice_workflow_service import attestation_fingerprint

MIN_REASON_LENGTH = 10

STATE_ACTIVE = "ACTIVE"
STATE_STALE = "STALE"          # the invoice changed after the override was granted
STATE_REVOKED = "REVOKED"


def clean_reason(reason: str | None, *, what: str) -> str:
    text = (reason or "").strip()
    if len(text) < MIN_REASON_LENGTH:
        raise ValidationError(
            message=f"A reason of at least {MIN_REASON_LENGTH} characters is required: {what}.",
            detail={"field": "reason", "min_length": MIN_REASON_LENGTH})
    return text


def state_fingerprint(invoice: Invoice) -> str:
    """
    SHA-256 over the invoice as an override reviews it: the header figures and every line (the Retailed
    attestation's own fingerprint, reused so both agree on what 'the same invoice' means), the typed charges,
    and the set of failing critical checks. Mappings and the PDI bytes are deliberately excluded: an override is
    about the extraction and its totals, not about units per case.
    """
    payload = json.dumps({
        "invoice": attestation_fingerprint(invoice, {}, None),
        "other_charges": getattr(invoice, "other_charges", None) or [],
        "blocking_checks": sorted(critical_check_blockers(invoice)),
    }, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def override_state(row: ExportOverride, invoice: Invoice | None) -> str:
    if row.revoked_at is not None:
        return STATE_REVOKED
    if invoice is None or row.state_sha256 != state_fingerprint(invoice):
        return STATE_STALE
    return STATE_ACTIVE


async def active_overrides(session: AsyncSession, invoices: Iterable[Invoice]) -> dict[uuid.UUID, ExportOverride]:
    """
    The override in force for each given invoice (at most one: the newest un-revoked row whose fingerprint
    still matches). One query for any number of invoices. Invoices with nothing failing never need one.
    """
    needing = {i.id: i for i in invoices if critical_check_blockers(i)}
    if not needing:
        return {}
    rows = (await session.execute(
        select(ExportOverride)
        .where(ExportOverride.invoice_id.in_(list(needing)), ExportOverride.revoked_at.is_(None))
        .order_by(ExportOverride.created_at.desc())
    )).scalars().all()
    found: dict[uuid.UUID, ExportOverride] = {}
    for row in rows:
        invoice = needing.get(row.invoice_id) if row.invoice_id else None
        if invoice is not None and row.invoice_id not in found and override_state(row, invoice) == STATE_ACTIVE:
            found[row.invoice_id] = row
    return found


async def active_override(session: AsyncSession, invoice: Invoice) -> ExportOverride | None:
    return (await active_overrides(session, [invoice])).get(invoice.id)


async def list_overrides(session: AsyncSession, invoice_id: uuid.UUID) -> list[ExportOverride]:
    return list((await session.execute(
        select(ExportOverride).where(ExportOverride.invoice_id == invoice_id)
        .order_by(ExportOverride.created_at.desc())
    )).scalars().all())


async def _latest_validation_payload(session: AsyncSession, document_id: uuid.UUID) -> dict[str, Any]:
    payloads = [log.payload for log in await ProcessingLogRepository(session).for_document(document_id)
                if log.stage == PipelineStage.VALIDATION and log.payload]
    return copy.deepcopy(payloads[-1]) if payloads else {}


def _require_admin(user) -> None:
    if user.role != UserRole.ADMIN.value:
        raise PermissionDeniedError(message="Only an administrator can override an export block.",
                                    detail={"required_role": UserRole.ADMIN.value, "actual_role": user.role})


async def grant_override(session: AsyncSession, invoice_id: uuid.UUID, user, reason: str) -> ExportOverride:
    """
    Record the override for the invoice as it stands now. Refused for a non-admin, without a real reason, when
    nothing is failing (there is nothing to override), for an invoice out of the active workflow, and when an
    override already covers this exact state. Flushes; the caller commits. The invoice is not modified.
    """
    _require_admin(user)
    text = clean_reason(reason, what="say why this invoice may be exported although its critical checks fail")
    # Lock, then read fresh: the fingerprint must be of committed state, not a stale copy.
    found = (await session.execute(
        select(Invoice.id).where(Invoice.id == invoice_id).with_for_update())).scalar_one_or_none()
    invoice = await InvoiceRepository(session).get_detail(invoice_id, fresh=True) if found else None
    if invoice is None:
        raise RecordNotFoundError(message="Invoice not found.", detail={"invoice_id": str(invoice_id)})
    ensure_document_operable(invoice.document, "export_override")
    blockers = critical_check_blockers(invoice)
    if not blockers:
        raise ValidationError(
            message="No critical check is failing on this invoice, so there is nothing to override.",
            detail={"invoice_id": str(invoice.id)})
    if await active_override(session, invoice) is not None:
        raise ValidationError(
            message="An export override already covers this invoice as it stands. Revoke it first to replace it.",
            detail={"invoice_id": str(invoice.id)})

    snapshot = {
        "invoice_status": invoice.status,
        "composite_confidence": str(invoice.composite_confidence) if invoice.composite_confidence is not None else None,
        "blocking_checks": list(blockers),
        "validation_report": await _latest_validation_payload(session, invoice.document_id),
    }
    row = ExportOverride(
        invoice_id=invoice.id, document_id=invoice.document_id, invoice_number=invoice.invoice_number,
        reason=text, blocked_checks=list(blockers), state_sha256=state_fingerprint(invoice),
        validation_snapshot=snapshot, granted_by_user_id=user.id, granted_by=user.username,
        granted_by_role=user.role,
    )
    session.add(row)
    await session.flush()
    await ProcessingLogRepository(session).add(
        document_id=invoice.document_id, stage=PipelineStage.LIFECYCLE,
        message=f"Export override granted by {user.username}: {text}",
        payload={"event": "export_override_granted", "override_id": str(row.id), "by": user.username,
                 "reason": text, "blocked_checks": list(blockers), "state_sha256": row.state_sha256,
                 "invoice_status": invoice.status},
    )
    await session.flush()
    return row


async def revoke_override(session: AsyncSession, override_id: uuid.UUID, user, reason: str) -> ExportOverride:
    _require_admin(user)
    text = clean_reason(reason, what="say why the override is withdrawn")
    row = (await session.execute(
        select(ExportOverride).where(ExportOverride.id == override_id).with_for_update())).scalar_one_or_none()
    if row is None:
        raise RecordNotFoundError(message="Export override not found.", detail={"override_id": str(override_id)})
    if row.revoked_at is not None:
        raise ValidationError(message="This override is already revoked.", detail={"override_id": str(row.id)})
    row.revoked_at = datetime.now(UTC)
    row.revoked_by = user.username
    row.revoked_by_user_id = user.id
    row.revoke_reason = text
    if row.document_id is not None:
        await ProcessingLogRepository(session).add(
            document_id=row.document_id, stage=PipelineStage.LIFECYCLE,
            message=f"Export override revoked by {user.username}: {text}",
            payload={"event": "export_override_revoked", "override_id": str(row.id), "by": user.username,
                     "reason": text},
        )
    await session.flush()
    return row


def snapshot_sha256(override: ExportOverride) -> str:
    """Digest of the validation snapshot stored on the override, so a log entry can prove which one it refers to."""
    return hashlib.sha256(json.dumps(override.validation_snapshot, sort_keys=True, separators=(",", ":"),
                                     default=str).encode("utf-8")).hexdigest()


async def log_override_export(session: AsyncSession, invoice: Invoice, override: ExportOverride, user, *,
                              export_format: str, content: str, export_number: int | None = None) -> None:
    """
    Audit entry for a file delivered under an override — every format (PDI, TXT, CSV), every time. It names the
    invoice, the format, the override, the user and the moment, the digest and size of the delivered bytes, and
    refers to the validation snapshot kept on the override (id, invoice fingerprint, snapshot digest). Review-only
    downloads are a different thing and never write one.
    """
    raw = content.encode("utf-8")
    exported_at = datetime.now(UTC).isoformat()
    await ProcessingLogRepository(session).add(
        document_id=invoice.document_id, stage=PipelineStage.LIFECYCLE,
        message=f"{export_format.upper()} exported under an administrator override by {user.username}.",
        payload={"event": "exported_under_override", "invoice_id": str(invoice.id),
                 "invoice_number": invoice.invoice_number, "format": export_format,
                 "override_id": str(override.id), "by": user.username, "by_user_id": str(user.id),
                 "by_role": user.role, "exported_at": exported_at, "export_number": export_number,
                 "sha256": hashlib.sha256(raw).hexdigest(), "byte_size": len(raw),
                 "blocked_checks": list(override.blocked_checks), "granted_by": override.granted_by,
                 "validation_snapshot_ref": {"override_id": str(override.id), "state_sha256": override.state_sha256,
                                             "snapshot_sha256": snapshot_sha256(override)}},
    )
