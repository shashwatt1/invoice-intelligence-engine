"""
Inventory receipt approval — app/services/inventory_approval.py

The only way stock enters the ledger from an invoice:

  1. Submit. Marking an invoice Retailed with inventory switched on creates a
     SUBMITTED receipt in the same transaction (submit_on_retailed): who
     marked it, when, the Retailed attestation fingerprint, the EDI export the
     mark rested on and a snapshot of the proposed lines. No movement is
     written. Marking needs no store grant — Retailed is unchanged — and a
     submission is only a proposal.
  2. Approve or reject. A MANAGER with an active grant for the receipt's
     store, or an ADMIN (implicit access to every active physical store;
     self-approval allowed and recorded as such) approves it, optionally adjusting accepted quantities
     (whole sellable units; any difference from proposed needs a reason), or
     rejects it with a reason. Approval re-checks everything under the
     invoice and receipt locks — the submission is still pending, the invoice
     is still Retailed under the same attestation, nothing attested changed,
     the store, eligibility, conversions and lines are as submitted — and
     then posts exactly once (POSTED, one movement per line, idempotency keys
     unchanged). A rejection is terminal and moves nothing.
  3. Withdraw, supersede. Removing the Retailed mark withdraws a pending
     submission; marking Retailed again replaces it (SUPERSEDED) and the new
     submission points at the one it follows. A pending submission whose
     invoice changed is stale and cannot be approved until the invoice is
     re-verified and re-marked.
  4. Reverse. A POSTED receipt is reversed with compensating movements
     (inventory_service.reverse_receipt); the Retailed mark is untouched.
  5. Delete. Deleting an invoice withdraws a pending submission, and reverses
     a POSTED receipt first, in the same transaction as the delete
     (release_invoice_for_deletion). Reversing needs inventory switched on
     and the deleting account's own grant for the store; otherwise the delete
     is refused and nothing changes. Receipts and movements are never
     deleted: the invoice's deletion only clears their link to it (SET NULL).

A wrong-store invoice is corrected with the existing flows: reverse (by
deleting the invoice), upload it again for the right store, validate, map,
export and mark Retailed, and approve the new submission there.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import InvoiceReceivedError, RecordNotFoundError, ValidationError
from app.core.logging import get_logger
from app.models.inventory import (
    RECEIPT_ACTIVE,
    RECEIPT_POSTED,
    RECEIPT_REJECTED,
    RECEIPT_SUBMITTED,
    RECEIPT_SUPERSEDED,
    RECEIPT_WITHDRAWN,
    InventoryReceipt,
)
from app.models.invoice import Invoice
from app.models.processing_log import PipelineStage
from app.models.store import KIND_PHYSICAL
from app.repositories.invoice_repository import InvoiceRepository
from app.repositories.processing_log_repository import ProcessingLogRepository
from app.repositories.store_repository import StoreRepository
from app.services import inventory_service as inventory
from app.services import store_access
from app.services.case_mapping_service import invoice_commercial_resolution
from app.services.invoice_workflow_service import STATUS_VALIDATED, current_facts
from app.services.receiving_eligibility import receiving_eligibility

logger = get_logger(__name__)

SNAPSHOT_VERSION = 1
MAX_REASON = 1000
MAX_QUEUE = 200

# Why a pending submission can no longer be approved as it stands.
STALE_INVOICE_DELETED = "invoice_deleted"
STALE_NOT_RETAILED = "retailed_mark_removed"
STALE_REMARKED = "retailed_marked_again"
STALE_INVOICE_CHANGED = "invoice_changed_since_retailed"
STALE_NOT_VALIDATED = "invoice_not_validated"
STALE_STORE_CHANGED = "store_changed"
STALE_LINES_CHANGED = "lines_changed_since_submission"

# Why marking Retailed did not create a submission.
SKIP_DISABLED = "inventory_disabled"
SKIP_SCHEMA = "inventory_schema_not_migrated"     # 0035 not applied yet
SKIP_ALREADY_RECEIVED = "already_received"
SKIP_NOT_PHYSICAL = "store_not_physical"


# ---- snapshots and comparison (pure) ---------------------------------------------------------

def line_snapshot(line: inventory.ReceiptLine) -> dict[str, Any]:
    return {"invoice_item_id": str(line.invoice_item_id), "sort_order": line.sort_order,
            "description": line.description, "item_code": line.item_code, "cases": str(line.cases),
            "units_per_case": line.units_per_case, "expected_units": str(line.expected_units),
            "conversion_path": line.conversion_path, "conversion_mapping_id": line.conversion_mapping_id,
            "conversion_scope": line.conversion_scope}


def _line_key(line: Mapping[str, Any]) -> tuple:
    return (str(line["invoice_item_id"]), line["item_code"], Decimal(str(line["cases"])),
            int(line["units_per_case"]), Decimal(str(line["expected_units"])))


@dataclass(frozen=True)
class LineChange:
    invoice_item_id: str
    item_code: str | None
    description: str | None
    change: str                       # "added" | "removed" | "changed"
    proposed_units: Decimal | None
    current_units: Decimal | None
    proposed_units_per_case: int | None
    current_units_per_case: int | None


def compare_lines(proposed: Sequence[Mapping[str, Any]], current: Sequence[inventory.ReceiptLine]) -> list[LineChange]:
    """Every difference between the submitted lines and the lines a receipt would cover now, by sort order."""
    before = {str(p["invoice_item_id"]): p for p in proposed}
    now = {str(c.invoice_item_id): line_snapshot(c) for c in current}
    changes: list[tuple[int, LineChange]] = []
    for key in before.keys() | now.keys():
        p, c = before.get(key), now.get(key)
        if p is not None and c is not None and _line_key(p) == _line_key(c):
            continue
        ref = c or p
        assert ref is not None
        changes.append((int(ref["sort_order"]), LineChange(
            invoice_item_id=key, item_code=ref["item_code"], description=ref["description"],
            change="added" if p is None else "removed" if c is None else "changed",
            proposed_units=Decimal(str(p["expected_units"])) if p else None,
            current_units=Decimal(str(c["expected_units"])) if c else None,
            proposed_units_per_case=p["units_per_case"] if p else None,
            current_units_per_case=c["units_per_case"] if c else None,
        )))
    return [change for _, change in sorted(changes, key=lambda pair: (pair[0], pair[1].invoice_item_id))]


def staleness(receipt: InventoryReceipt, invoice: Invoice | None, current_fingerprint: str | None,
              current_lines: Sequence[inventory.ReceiptLine]) -> list[str]:
    """Every reason a pending submission no longer matches its invoice (empty: it does). Pure."""
    if invoice is None:
        return [STALE_INVOICE_DELETED]
    reasons: list[str] = []
    if invoice.retailed_at is None:
        reasons.append(STALE_NOT_RETAILED)
    elif invoice.retailed_attestation_sha256 != receipt.attestation_sha256:
        reasons.append(STALE_REMARKED)
    if current_fingerprint != receipt.attestation_sha256:
        reasons.append(STALE_INVOICE_CHANGED)
    if str(invoice.status) != STATUS_VALIDATED:
        reasons.append(STALE_NOT_VALIDATED)
    if invoice.store_id != receipt.store_id:
        reasons.append(STALE_STORE_CHANGED)
    proposed = (receipt.submission_snapshot or {}).get("lines", [])
    if compare_lines(proposed, current_lines):
        reasons.append(STALE_LINES_CHANGED)
    return reasons


def received_on_for_approval(value: date | None) -> date:
    """
    The business date the approved receipt is recorded on. Today: the date
    the approver states, required — exactly the behaviour of the former direct
    receipt. Kept in one place so the policy can change on its own later.
    """
    if value is None:
        raise ValidationError(message="Say which day the goods were received.",
                              detail={"field": "received_on", "reason": "required"})
    return value


def _reason(value: str | None, field_name: str, message: str) -> str:
    reason = (value or "").strip()
    if not reason:
        raise ValidationError(message=message, detail={"field": field_name, "reason": "required"})
    if len(reason) > MAX_REASON:
        raise ValidationError(message=f"Keep the reason under {MAX_REASON} characters.",
                              detail={"field": field_name, "reason": "too_long"})
    return reason


# ---- database helpers --------------------------------------------------------------------------

async def _active_receipts(session: AsyncSession, invoice_id: uuid.UUID) -> list[InventoryReceipt]:
    """The invoice's SUBMITTED/POSTED receipts (at most one), locked, re-read from the database."""
    return list((await session.execute(
        select(InventoryReceipt)
        .where(InventoryReceipt.invoice_id == invoice_id, InventoryReceipt.status.in_(RECEIPT_ACTIVE))
        .with_for_update().execution_options(populate_existing=True)
    )).scalars().all())


async def _locked_receipt(session: AsyncSession, invoice_id: uuid.UUID, receipt_id: uuid.UUID,
                          user) -> InventoryReceipt:
    """
    The receipt, locked and re-read, if it belongs to the invoice and `user`
    may use its store. Otherwise the one 404 — an unknown receipt and one at a
    store the account cannot use look the same.
    """
    receipt = (await session.execute(
        select(InventoryReceipt).where(InventoryReceipt.id == receipt_id, InventoryReceipt.invoice_id == invoice_id)
        .with_for_update().execution_options(populate_existing=True)
    )).scalars().first()
    if receipt is None or not await store_access.has_store_access(session, user, receipt.store_id):
        raise RecordNotFoundError(message="Receipt not found for this invoice.",
                                  detail={"invoice_id": str(invoice_id), "receipt_id": str(receipt_id)})
    return receipt


def _not_pending(receipt: InventoryReceipt, action: str) -> ValidationError:
    return ValidationError(
        message=f"This submission is {receipt.status.lower()} and can no longer be {action}.",
        detail={"receipt_id": str(receipt.id), "reason": "not_pending", "status": receipt.status},
    )


async def _log(session: AsyncSession, document_id: uuid.UUID | None, message: str, payload: dict) -> None:
    if document_id is not None:
        await ProcessingLogRepository(session).add(document_id=document_id, stage=PipelineStage.LIFECYCLE,
                                                   message=message, payload=payload)


# ---- 1. submit -----------------------------------------------------------------------------------

@dataclass
class SubmissionOutcome:
    receipt: InventoryReceipt | None = None
    superseded: InventoryReceipt | None = None
    skipped: str | None = None


async def submit_on_retailed(session: AsyncSession, invoice: Invoice, *, fingerprint: str, export, user
                             ) -> SubmissionOutcome:
    """
    Create the SUBMITTED receipt for an invoice just marked Retailed. Called by
    invoice_workflow_service.mark_retailed under its invoice lock and in its
    transaction, so the mark and the submission commit or fail together.
    Writes no movement. Flushes; the caller commits.
    """
    if not get_settings().inventory_enabled:
        return SubmissionOutcome(skipped=SKIP_DISABLED)
    if not await inventory.approval_schema_ready(session):
        return SubmissionOutcome(skipped=SKIP_SCHEMA)
    store = await StoreRepository(session).get(invoice.store_id) if invoice.store_id else None
    if store is None or store.kind != KIND_PHYSICAL:
        return SubmissionOutcome(skipped=SKIP_NOT_PHYSICAL)
    active = await _active_receipts(session, invoice.id)
    if any(r.status == RECEIPT_POSTED for r in active):
        return SubmissionOutcome(skipped=SKIP_ALREADY_RECEIVED)     # reverse it first to receive again

    now = datetime.now(UTC)
    pending = next((r for r in active if r.status == RECEIPT_SUBMITTED), None)
    if pending is not None:
        pending.status = RECEIPT_SUPERSEDED
        pending.superseded_at = now
        await session.flush()            # free the one-active slot before the new submission
        previous: InventoryReceipt | None = pending
    else:
        previous = (await session.execute(
            select(InventoryReceipt).where(InventoryReceipt.invoice_id == invoice.id)
            .order_by(InventoryReceipt.created_at.desc()).limit(1)
        )).scalars().first()

    resolution = await invoice_commercial_resolution(session, invoice)
    lines, blockers = inventory.build_receipt_lines(invoice, resolution)
    eligibility = receiving_eligibility(invoice, store, invoice.document)
    resolution_summary = {"enabled": resolution.enabled, "paths": resolution.paths()}
    receipt = InventoryReceipt(
        invoice_id=invoice.id, invoice_number=invoice.invoice_number, store_id=store.id, status=RECEIPT_SUBMITTED,
        submitted_at=now, submitted_by_user_id=user.id, submitted_by=user.username, submitted_role=user.role,
        attestation_sha256=fingerprint, edi_export_id=export.id if export is not None else None,
        previous_receipt_id=previous.id if previous is not None else None,
        line_count=len(lines), resolution_snapshot=resolution_summary,
        submission_snapshot={
            "version": SNAPSHOT_VERSION,
            "invoice": {"invoice_number": invoice.invoice_number, "status": str(invoice.status),
                        "store_id": str(store.id), "store_label": store.label},
            "export": ({"id": str(export.id), "number": export.export_number, "sha256": export.sha256}
                       if export is not None else None),
            "lines": [line_snapshot(line) for line in lines],
            "blockers": [vars(b) for b in blockers],
            "eligibility_reasons": list(eligibility.reasons),
            "resolution": resolution_summary,
        },
    )
    session.add(receipt)
    await session.flush()
    await _log(session, invoice.document_id,
               f"Inventory receipt submitted for approval by {user.username} ({len(lines)} line(s)).",
               {"event": "inventory_receipt_submitted", "receipt_id": str(receipt.id), "by": user.username,
                "store_id": str(store.id), "line_count": len(lines), "blockers": len(blockers),
                "attestation_sha256": fingerprint,
                "superseded_receipt_id": str(pending.id) if pending is not None else None,
                "previous_receipt_id": str(previous.id) if previous is not None else None})
    return SubmissionOutcome(receipt=receipt, superseded=pending)


# ---- 2. review, approve, reject ----------------------------------------------------------------

@dataclass
class Review:
    preview: inventory.ReceiptPreview
    stale_reasons: list[str] = field(default_factory=list)
    line_changes: list[LineChange] = field(default_factory=list)

    @property
    def can_approve(self) -> bool:
        p = self.preview
        return (p.submission is not None and not self.stale_reasons and not p.eligibility_reasons
                and not p.blockers and bool(p.lines))


async def _review(session: AsyncSession, preview: inventory.ReceiptPreview) -> Review:
    submission = preview.submission
    if submission is None:
        return Review(preview)
    _pdi, _blocked, fingerprint = current_facts(preview.invoice, preview.resolution.units)
    proposed = (submission.submission_snapshot or {}).get("lines", [])
    return Review(preview, staleness(submission, preview.invoice, fingerprint, preview.lines),
                  compare_lines(proposed, preview.lines))


async def review(session: AsyncSession, invoice_id: uuid.UUID, user) -> Review:
    """The invoice's receipt state for a reviewer: the same 404 as an unknown invoice without store access."""
    return await _review(session, await inventory.receipt_preview(session, invoice_id, user))


async def approve_receipt(
    session: AsyncSession, invoice_id: uuid.UUID, *, receipt_id: uuid.UUID, accepted: Mapping[uuid.UUID, Decimal],
    reasons: Mapping[uuid.UUID, str], received_on: date | None, note: str | None, user,
) -> InventoryReceipt:
    """
    Approve a pending submission and post it, exactly once. Every condition is
    re-checked under the invoice lock (taken first, as by every guarded
    change) and the receipt's own row lock. Flushes; the caller commits.
    """
    await inventory.lock_invoice(session, invoice_id)
    receipt = await _locked_receipt(session, invoice_id, receipt_id, user)
    if receipt.status != RECEIPT_SUBMITTED:
        raise _not_pending(receipt, "approved")
    business_date = received_on_for_approval(received_on)
    preview = await inventory.receipt_preview(session, invoice_id, user, fresh=True)
    checked = await _review(session, preview)
    if checked.stale_reasons:
        raise ValidationError(
            message=("The invoice changed after this submission was made; nothing was posted. Re-verify the invoice "
                     "and mark it Retailed again to submit it afresh."),
            detail={"invoice_id": str(invoice_id), "receipt_id": str(receipt.id), "reason": "stale_submission",
                    "stale_reasons": checked.stale_reasons},
        )
    if preview.eligibility_reasons:
        raise ValidationError(message="This invoice cannot be received; nothing was posted.",
                              detail={"invoice_id": str(invoice_id), "reason": "not_receivable",
                                      "reasons": preview.eligibility_reasons})
    if preview.blockers:
        raise ValidationError(
            message=f"{len(preview.blockers)} product line(s) have no approved units-per-case conversion; "
                    "nothing was posted.",
            detail={"invoice_id": str(invoice_id), "reason": "unresolved_conversions",
                    "blockers": [vars(b) for b in preview.blockers]},
        )
    if not preview.lines:
        raise ValidationError(message="This invoice has no product lines to receive.",
                              detail={"invoice_id": str(invoice_id), "reason": "no_lines"})
    confirmed = inventory.accepted_quantities(preview.lines, accepted, reasons)

    receipt.status = RECEIPT_POSTED
    receipt.received_on = business_date
    receipt.confirmed_by_user_id = user.id
    receipt.confirmed_by = user.username
    receipt.confirmed_role = user.role
    receipt.approved_at = datetime.now(UTC)
    receipt.note = (note or "").strip() or None
    receipt.line_count = len(confirmed)
    receipt.discrepancy_count = sum(1 for line, quantity, _ in confirmed if quantity != line.expected_units)
    receipt.resolution_snapshot = {"enabled": preview.resolution.enabled, "paths": preview.resolution.paths()}
    await session.flush()
    session.add_all(inventory.receipt_movements(receipt, confirmed, invoice_id=preview.invoice.id,
                                                received_on=business_date, user=user))
    await session.flush()
    self_approved = receipt.submitted_by_user_id == user.id
    await _log(session, preview.invoice.document_id,
               f"Inventory receipt approved by {user.username}{' (self-approved)' if self_approved else ''}: "
               f"{len(confirmed)} line(s), {receipt.discrepancy_count} adjusted.",
               {"event": "inventory_receipt_approved", "receipt_id": str(receipt.id), "by": user.username,
                "role": user.role, "submitted_by": receipt.submitted_by, "self_approved": self_approved,
                "received_on": business_date.isoformat(), "line_count": len(confirmed),
                "discrepancy_count": receipt.discrepancy_count})
    return receipt


async def reject_receipt(session: AsyncSession, invoice_id: uuid.UUID, *, receipt_id: uuid.UUID,
                         reason: str | None, user) -> InventoryReceipt:
    """Reject a pending submission with a reason. Terminal; no stock changes. Flushes; the caller commits."""
    reason = _reason(reason, "reason", "Say why the submission is being rejected.")
    await inventory.lock_invoice(session, invoice_id)
    receipt = await _locked_receipt(session, invoice_id, receipt_id, user)
    if receipt.status != RECEIPT_SUBMITTED:
        raise _not_pending(receipt, "rejected")
    receipt.status = RECEIPT_REJECTED
    receipt.rejected_at = datetime.now(UTC)
    receipt.rejected_by_user_id = user.id
    receipt.rejected_by = user.username
    receipt.rejected_role = user.role
    receipt.rejection_reason = reason
    await session.flush()
    invoice = await session.get(Invoice, invoice_id)
    await _log(session, invoice.document_id if invoice else None,
               f"Inventory receipt rejected by {user.username}: {reason}",
               {"event": "inventory_receipt_rejected", "receipt_id": str(receipt.id), "by": user.username,
                "role": user.role, "reason": reason})
    return receipt


# ---- 3. withdraw ---------------------------------------------------------------------------------

async def withdraw_pending(session: AsyncSession, invoice_id: uuid.UUID, *, reason: str, user
                           ) -> InventoryReceipt | None:
    """
    Withdraw the invoice's pending submission, if any (no stock effect). Used
    when the Retailed mark is removed and when the invoice is deleted; runs
    whether or not inventory is switched on, so no submission outlives its
    reason. Before 0035 no submission can exist, so there is nothing to do.
    The caller holds the invoice lock. Flushes; the caller commits.
    """
    if not await inventory.approval_schema_ready(session):
        return None
    pending = next((r for r in await _active_receipts(session, invoice_id) if r.status == RECEIPT_SUBMITTED), None)
    if pending is None:
        return None
    pending.status = RECEIPT_WITHDRAWN
    pending.withdrawn_at = datetime.now(UTC)
    pending.withdrawn_by_user_id = user.id
    pending.withdrawn_by = user.username
    pending.withdrawn_role = user.role
    pending.withdrawal_reason = reason
    await session.flush()
    return pending


# ---- 5. delete -----------------------------------------------------------------------------------

@dataclass
class DeletionRelease:
    withdrawn: InventoryReceipt | None = None
    reversed: InventoryReceipt | None = None
    movements_reversed: int = 0


async def release_invoice_for_deletion(session: AsyncSession, invoice_id: uuid.UUID, user) -> DeletionRelease:
    """
    Take the invoice lock and clear its receipts so it can be deleted in the
    same transaction: a pending submission is withdrawn; a POSTED receipt is
    reversed (compensating movements, idempotency keys as for any reversal),
    which needs inventory switched on and `user`'s own grant for the store —
    otherwise InvoiceReceivedError and nothing changes. If anything later in
    the transaction fails, all of it rolls back with the delete. Before
    0035 (inventory tables without the approval columns) a POSTED receipt
    refuses the delete outright, as it did before this release. Flushes;
    the caller deletes and commits.
    """
    await inventory.lock_invoice(session, invoice_id)
    schema = await inventory.inventory_schema(session)
    if schema == inventory.SCHEMA_ABSENT:
        return DeletionRelease()
    if schema == inventory.SCHEMA_LEDGER:
        # Before 0035 no submission can exist and a receipt cannot be reversed by this code, so a
        # POSTED receipt keeps its invoice exactly as locked as it was before this release.
        held = await inventory.posted_receipt(session, invoice_id)
        if held is not None:
            raise InvoiceReceivedError(
                message=("This invoice was received into inventory. It cannot be deleted until the inventory "
                         "database migration (0035) is applied and the receipt is reversed. Nothing was changed."),
                detail={"invoice_id": str(invoice_id), "reason": "invoice_received", "action": "delete_invoice",
                        "blocked_by": "inventory_schema_not_migrated"},
            )
        return DeletionRelease()
    active = await _active_receipts(session, invoice_id)
    posted = next((r for r in active if r.status == RECEIPT_POSTED), None)
    if posted is not None:
        blocked = ("inventory_disabled" if not get_settings().inventory_enabled
                   else None if await store_access.has_store_access(session, user, posted.store_id)
                   else "no_store_access")
        if blocked:
            raise InvoiceReceivedError(
                message=("This invoice was received into inventory. Deleting it first reverses that receipt, "
                         "which needs inventory switched on and your own access to the invoice's store. "
                         "Nothing was changed."),
                detail={"invoice_id": str(invoice_id), "reason": "invoice_received", "action": "delete_invoice",
                        "blocked_by": blocked},
            )
    invoice = await session.get(Invoice, invoice_id)
    number = f"#{invoice.invoice_number}" if invoice and invoice.invoice_number else str(invoice_id)
    release = DeletionRelease()
    if posted is not None:
        result = await inventory.reverse_receipt(
            session, invoice_id, receipt_id=posted.id, reason=f"Invoice {number} deleted by {user.username}",
            user=user)
        release.reversed, release.movements_reversed = result.receipt, len(result.movements)
    release.withdrawn = await withdraw_pending(session, invoice_id,
                                               reason=f"Invoice {number} deleted by {user.username}", user=user)
    return release


# ---- the approval queue --------------------------------------------------------------------------

@dataclass
class PendingApproval:
    receipt: InventoryReceipt
    invoice: Invoice | None
    stale_reasons: list[str]
    blockers: int


async def pending_approvals(session: AsyncSession, store_id: uuid.UUID, user) -> list[PendingApproval]:
    """A store's pending submissions, oldest first — only for an account with a grant for that store."""
    store = await store_access.ensure_store_access(session, user, store_id)
    receipts = (await session.execute(
        select(InventoryReceipt).where(InventoryReceipt.store_id == store.id,
                                       InventoryReceipt.status == RECEIPT_SUBMITTED)
        .order_by(InventoryReceipt.submitted_at).limit(MAX_QUEUE)
    )).scalars().all()
    repository = InvoiceRepository(session)
    rows: list[PendingApproval] = []
    for receipt in receipts:
        invoice = await repository.get_detail(receipt.invoice_id) if receipt.invoice_id else None
        if invoice is None:
            rows.append(PendingApproval(receipt, None, [STALE_INVOICE_DELETED], 0))
            continue
        resolution = await invoice_commercial_resolution(session, invoice)
        lines, blockers = inventory.build_receipt_lines(invoice, resolution)
        _pdi, _blocked, fingerprint = current_facts(invoice, resolution.units)
        rows.append(PendingApproval(receipt, invoice, staleness(receipt, invoice, fingerprint, lines), len(blockers)))
    return rows


async def pending_count(session: AsyncSession, store_id: uuid.UUID) -> int:
    """How many submissions await approval at the store (the caller has authorized the store)."""
    return (await session.execute(
        select(func.count()).select_from(InventoryReceipt)
        .where(InventoryReceipt.store_id == store_id, InventoryReceipt.status == RECEIPT_SUBMITTED)
    )).scalar() or 0


def log_deletion(release: DeletionRelease, invoice_id: uuid.UUID, user) -> None:
    """The invoice's own processing log goes with it; the application log keeps what the delete did to inventory."""
    if release.reversed is not None or release.withdrawn is not None:
        logger.info("invoice_delete_released_inventory", invoice_id=str(invoice_id), by=user.username,
                    reversed_receipt_id=str(release.reversed.id) if release.reversed else None,
                    movements_reversed=release.movements_reversed,
                    withdrawn_receipt_id=str(release.withdrawn.id) if release.withdrawn else None)
