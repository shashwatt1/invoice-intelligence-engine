"""
Document Lifecycle — app/services/document_lifecycle.py

The one place that says what a validation decision means for the
document row. Two lifecycle states are kept in step:

    invoices.status   — the invoice's current validation decision
                        (VALIDATED / REVIEW_REQUIRED)
    documents.status  — the document's lifecycle state, which the invoice
                        list, the dashboard counters and status filters
                        read (COMPLETED / REVIEW_REQUIRED once persisted)

Initial persistence has always mapped the decision onto the document.
Governed corrections re-judge the invoice and move `invoices.status`;
until this module existed they left `documents.status` where the
pipeline put it, so a corrected-and-validated invoice kept showing as
"Needs review" everywhere the document status is read. Every path that
revalidates now goes through `sync_document_status`, and the backfill
below repairs rows that diverged before that.

Only terminal post-persistence states are ever touched: a document that
is FAILED, or still waiting on a store, keeps that state — those are
facts about the document, not about the invoice's arithmetic.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    DocumentNotActiveError,
    DocumentWithdrawnError,
    PermissionDeniedError,
)
from app.core.logging import get_logger
from app.models.document import Document, DocumentStatus
from app.models.invoice import Invoice
from app.models.processing_log import LogStatus, PipelineStage
from app.models.user import ROLE_RANK, User, UserRole
from app.repositories.document_repository import DocumentRepository
from app.repositories.processing_log_repository import ProcessingLogRepository
from app.services.validation.report import ProcessingDecision

logger = get_logger(__name__)

# The document states an invoice's decision is allowed to move between.
SYNCED_DOCUMENT_STATUSES: frozenset[DocumentStatus] = frozenset(
    {DocumentStatus.COMPLETED, DocumentStatus.REVIEW_REQUIRED}
)

# Mid-pipeline: a document here has an attempt genuinely in progress, so
# STOP is meaningful. Anything outside this set has already reached some
# disposition (terminal, or already withdrawn) and has nothing active to
# cancel.
ACTIVE_DOCUMENT_STATUSES: frozenset[DocumentStatus] = frozenset({
    DocumentStatus.UPLOADED, DocumentStatus.OCR_IN_PROGRESS, DocumentStatus.OCR_COMPLETED,
    DocumentStatus.AI_PROCESSING, DocumentStatus.VALIDATED, DocumentStatus.STORE_CONFIRMATION_REQUIRED,
})

# A document sitting in one of these states was withdrawn from the
# active workflow by a user action. Every pipeline stage boundary checks
# for this (see ensure_document_active) so an in-flight OCR/LLM call
# that finishes late can never advance a withdrawn document further.
WITHDRAWN_DOCUMENT_STATUSES: frozenset[DocumentStatus] = frozenset(
    {DocumentStatus.STOPPED, DocumentStatus.BINNED}
)


def document_status_for(decision: ProcessingDecision | str) -> DocumentStatus:
    """The document lifecycle state a validation decision maps onto."""
    value = decision.value if isinstance(decision, ProcessingDecision) else str(decision)
    return (
        DocumentStatus.COMPLETED
        if value == ProcessingDecision.VALIDATED.value
        else DocumentStatus.REVIEW_REQUIRED
    )


async def sync_document_status(
    session: AsyncSession, invoice: Invoice, decision: ProcessingDecision | str
) -> tuple[DocumentStatus, DocumentStatus] | None:
    """
    Bring the invoice's document into the state its decision implies.

    Returns (old, new) when the document changed, None when it was
    already right or is not in a state this module governs. Flushes
    only; the caller owns the transaction. Safe to call repeatedly.
    """
    document = await session.get(Document, invoice.document_id)
    if document is None:
        return None
    current = DocumentStatus(document.status)
    if current not in SYNCED_DOCUMENT_STATUSES:
        return None
    target = document_status_for(decision)
    if current == target:
        return None
    await DocumentRepository(session).set_status(document, target)
    logger.info(
        "document_status_synced",
        invoice_id=str(invoice.id),
        document_id=str(document.id),
        old=current.value,
        new=target.value,
    )
    return current, target


@dataclass(frozen=True)
class DocumentStatusRepair:
    invoice_id: uuid.UUID
    document_id: uuid.UUID
    invoice_number: str | None
    invoice_status: str
    old: DocumentStatus
    new: DocumentStatus


async def find_divergent_documents(session: AsyncSession) -> list[DocumentStatusRepair]:
    """
    Persisted invoices whose document sits in a governed terminal state
    that does not match the invoice's own decision. Read-only.
    """
    rows = (
        await session.execute(
            select(Invoice, Document)
            .join(Document, Document.id == Invoice.document_id)
            .where(Document.status.in_([s.value for s in SYNCED_DOCUMENT_STATUSES]))
            .order_by(Invoice.created_at)
        )
    ).all()
    repairs: list[DocumentStatusRepair] = []
    for invoice, document in rows:
        if invoice.status not in {d.value for d in ProcessingDecision}:
            continue  # not a validation decision we can map; leave it alone
        target = document_status_for(invoice.status)
        current = DocumentStatus(document.status)
        if current != target:
            repairs.append(DocumentStatusRepair(
                invoice_id=invoice.id, document_id=document.id, invoice_number=invoice.invoice_number,
                invoice_status=invoice.status, old=current, new=target,
            ))
    return repairs


async def backfill_document_status(
    session: AsyncSession, *, apply: bool, actor: str = "script:sync_document_status"
) -> list[DocumentStatusRepair]:
    """
    Repair every divergent document. Touches `documents.status` and
    appends one log entry per repair — nothing else on the invoice, its
    lines, mappings or history. Idempotent: a second run finds nothing.
    With apply=False it only reports. Flushes; the caller commits.
    """
    repairs = await find_divergent_documents(session)
    if not apply:
        return repairs
    for repair in repairs:
        invoice = await session.get(Invoice, repair.invoice_id)
        changed = await sync_document_status(session, invoice, repair.invoice_status)
        if changed is None:
            continue
        # Logged as a governed correction so the invoice's own VALIDATION
        # report (the latest entry of that stage) is left in place.
        await ProcessingLogRepository(session).add(
            document_id=repair.document_id,
            stage=PipelineStage.MANUAL_CORRECTION,
            status=LogStatus.SUCCESS,
            message="Document lifecycle status synchronized with the invoice's validation decision (backfill).",
            payload={"event": "document_status_synced", "by": actor,
                     "old": repair.old.value, "new": repair.new.value,
                     "invoice_status": repair.invoice_status},
        )
    return repairs


# ---------------------------------------------------------------------------
# STOP / MOVE TO BIN — backend-authoritative document workflow controls
#
# Both are document-level actions only: they change documents.status and
# append one LIFECYCLE audit entry. Neither ever touches the linked
# invoice (status, fields, corrections), a case mapping, a proposal, or
# generates EDI — so a COMPLETED invoice is never silently mutated by
# either action, by construction, not by a status check.
# ---------------------------------------------------------------------------


def ensure_document_visible(document: Document, user: User) -> None:
    """
    The ownership rule shared by every USER-scoped document action:
    ADMIN and MANAGER may see/act on any document; USER only one whose
    uploaded_by_user_id is exactly their own id. A NULL uploader (no
    recorded owner — e.g. from before authentication existed) fails
    closed for USER, never open; only MANAGER/ADMIN can still manage it.
    Raises PermissionDeniedError otherwise.
    """
    if ROLE_RANK.get(user.role, 0) >= ROLE_RANK[UserRole.MANAGER.value]:
        return
    if document.uploaded_by_user_id is None or document.uploaded_by_user_id != user.id:
        raise PermissionDeniedError(
            message="You do not have access to this document.",
            detail={"document_id": str(document.id), "role": user.role},
        )


def _authorize(document: Document, user: User) -> None:
    """
    Backend-authoritative authorization — enforced here, not left to the
    frontend to hide a button.

    P3 replaces P2's caller-supplied actor/role with the authenticated
    session: `user` comes from app.core.dependencies.get_current_user
    (a verified JWT, re-checked against the live users row on every
    request), never from a client-controlled field. Same ownership rule
    as ensure_document_visible — a USER can only act on what they can
    see.
    """
    try:
        ensure_document_visible(document, user)
    except PermissionDeniedError as exc:
        raise PermissionDeniedError(
            message="You may only stop or move to bin documents you uploaded yourself.",
            detail=exc.detail,
        ) from exc


async def ensure_document_active(session: AsyncSession, document: Document) -> None:
    """
    Re-reads the document's live status straight from the database —
    session.refresh, not session.get, because the same Document object
    is held in memory for an entire pipeline run and the session's
    identity map would otherwise silently keep returning its stale,
    already-loaded status instead of re-querying. Raises
    DocumentWithdrawnError if the document has been STOPPED or BINNED
    since this run began.

    Call this after each expensive external call (OCR, LLM) returns and
    before committing the next stage transition. Nothing in this
    architecture can interrupt a network request already sent — the
    guarantee this provides is narrower and sufficient: the result of
    that call can never advance a withdrawn document to a successful
    workflow state, because every commit that would do so is preceded by
    this check.
    """
    await session.refresh(document, attribute_names=["status"])
    current = DocumentStatus(document.status)
    if current in WITHDRAWN_DOCUMENT_STATUSES:
        raise DocumentWithdrawnError(
            detail={"document_id": str(document.id), "status": current.value},
        )


async def stop_document(session: AsyncSession, document: Document, user: User) -> Document:
    """
    Backend-authoritative cancellation of the current active processing
    attempt, on behalf of the authenticated `user`.

    Idempotent: calling this on an already-STOPPED document is a no-op
    success (returns the document unchanged, no new audit entry).
    Refuses with DocumentNotActiveError when there is nothing active to
    cancel — already COMPLETED, REVIEW_REQUIRED, FAILED or BINNED — so a
    finished invoice is never silently moved by a stray STOP call.

    Flushes only; the caller commits.
    """
    _authorize(document, user)
    current = DocumentStatus(document.status)
    if current == DocumentStatus.STOPPED:
        return document
    if current not in ACTIVE_DOCUMENT_STATUSES:
        raise DocumentNotActiveError(
            detail={"document_id": str(document.id), "status": current.value},
        )
    await DocumentRepository(session).set_status(document, DocumentStatus.STOPPED)
    await ProcessingLogRepository(session).add(
        document_id=document.id,
        stage=PipelineStage.LIFECYCLE,
        status=LogStatus.SUCCESS,
        message=f"Processing stopped by {user.username} ({user.role}).",
        payload={"event": "document_stopped", "actor_user_id": str(user.id),
                 "actor_username": user.username, "role": user.role, "previous_status": current.value},
    )
    logger.info("document_stopped", document_id=str(document.id), actor_user_id=str(user.id),
               role=user.role, previous_status=current.value)
    return document


async def move_document_to_bin(session: AsyncSession, document: Document, user: User) -> Document:
    """
    Backend-authoritative, recoverable removal from the active workflow,
    on behalf of the authenticated `user`.

    Idempotent: calling this on an already-BINNED document is a no-op
    success. Works from any non-BINNED state, including a terminal one
    (COMPLETED, REVIEW_REQUIRED, FAILED) or an active one — a document
    mid-pipeline is withdrawn the same way STOP withdraws one, via
    ensure_document_active's WITHDRAWN_DOCUMENT_STATUSES check at each
    stage boundary. Never deletes the source file, the document row, the
    invoice, or any history: recoverable by restoring documents.status.

    Flushes only; the caller commits.
    """
    _authorize(document, user)
    current = DocumentStatus(document.status)
    if current == DocumentStatus.BINNED:
        return document
    await DocumentRepository(session).set_status(document, DocumentStatus.BINNED)
    await ProcessingLogRepository(session).add(
        document_id=document.id,
        stage=PipelineStage.LIFECYCLE,
        status=LogStatus.SUCCESS,
        message=f"Moved to bin by {user.username} ({user.role}).",
        payload={"event": "document_binned", "actor_user_id": str(user.id),
                 "actor_username": user.username, "role": user.role, "previous_status": current.value},
    )
    logger.info("document_binned", document_id=str(document.id), actor_user_id=str(user.id),
               role=user.role, previous_status=current.value)
    return document
