"""
Revalidate-only tool — app/services/revalidation_tool.py

Brings an EXISTING invoice under the validation and completeness rules (app/services/completeness.py and the
critical checks) without editing it. Invoices stored before those rules have `blocking_checks` NULL, so nothing
blocks their export; the deployed app has no way to revalidate one except by editing it (a date or totals
correction marks the field as hand-corrected even when the value is unchanged).

What it does
------------
* `plan()` judges the stored invoice exactly as the deployed revalidation does — the same `build_report`, the same
  stored OCR text, the OCR and model confidence carried forward from the last validation log. No OCR, no model, no
  network. It runs in a READ ONLY transaction and writes nothing. It returns the exact before and after state, what
  already exists around the invoice (historical EDI exports, inventory receipts, a Retailed mark) and every reason
  an apply would be refused. The plan carries a fingerprint of all of that.
* `apply()` repeats the plan under the invoice's row lock and writes only if: the actor is an active ADMIN, nothing
  refuses it, no stored line would be rewritten, and the fresh plan's fingerprint equals the one the operator
  reviewed (`expect_plan`). It then sets the invoice's status, confidence and `blocking_checks`, syncs the document's
  lifecycle status exactly as revalidation does, and appends a VALIDATION log entry (so the invoice screen reads the
  new report) and an audit entry (who, before, after, fingerprint).

What it never does
------------------
Touch extracted lines, totals, proposals, mappings, historical EDI exports, EDI ledger rows or inventory records. It
refuses an invoice with a POSTED receipt, an invoice out of the active workflow (Recycle Bin, reference copy), a
number that matches more than one invoice, and any invoice where the deployed revalidation would have rewritten a
stored line (Rule D / Rule E) — because applying then would not be the revalidate-only change that was reviewed.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import INACTIVE_DOCUMENT_STATUSES, Document
from app.models.edi_export import EdiExport
from app.models.inventory import RECEIPT_POSTED, RECEIPT_SUBMITTED, InventoryReceipt
from app.models.invoice import Invoice
from app.models.processing_log import LogStatus, PipelineStage
from app.models.user import UserRole
from app.repositories.invoice_repository import InvoiceRepository
from app.repositories.processing_log_repository import ProcessingLogRepository
from app.repositories.user_repository import UserRepository
from app.services import inventory_service
from app.services.document_lifecycle import (
    SYNCED_DOCUMENT_STATUSES,
    document_status_for,
    sync_document_status,
)
from app.services.revalidation_service import build_report, normalized_from_persisted
from app.services.validation.reconciliation import reconcile_invoice
from app.services.validation.report import ValidationReport
from app.services.validation.service import ValidationService

REFUSE_NOT_FOUND = "invoice_not_found"
REFUSE_AMBIGUOUS = "invoice_number_ambiguous"
REFUSE_POSTED_RECEIPT = "posted_inventory_receipt"
REFUSE_INACTIVE = "document_out_of_active_workflow"
REFUSE_WOULD_REWRITE = "stored_lines_would_be_rewritten"

AUDIT_EVENT = "revalidated_by_admin_tool"


class ToolRefusal(Exception):  # noqa: N818 — a refusal, not an error: nothing was written
    def __init__(self, message: str, reasons: list[str] | None = None) -> None:
        super().__init__(message)
        self.reasons = reasons or []


@dataclass(frozen=True)
class State:
    status: str
    composite_confidence: str | None
    blocking_checks: list[str] | None
    document_status: str


@dataclass(frozen=True)
class Plan:
    invoice_id: str
    invoice_number: str
    vendor: str | None
    document_id: str
    before: State
    after: State
    completeness: str | None
    missing_codes: list[str]
    failed_checks: list[str]
    review_reasons: list[str]
    historical_edi_exports: int
    posted_receipts: int
    submitted_receipts: int
    retailed: bool
    stored_ocr_text: bool
    rows_that_would_be_rewritten: int
    refusals: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    fingerprint: str = ""

    @property
    def changes(self) -> bool:
        return self.before != self.after

    @property
    def applicable(self) -> bool:
        return not self.refusals

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "changes": self.changes, "applicable": self.applicable}


def _money(value: Decimal | None) -> str | None:
    return None if value is None else format(Decimal(value), "f")


def _composite(report: ValidationReport) -> str:
    # exactly what revalidate_invoice stores, shown at the column's four decimals
    return format(Decimal(str(report.confidence.composite)).quantize(Decimal("0.0001")), "f")


def _fingerprint(parts: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def rows_the_deployed_revalidation_would_rewrite(invoice: Invoice) -> int:
    """
    How many stored rows `revalidate_invoice` would change through Rule D (a deposit folded into the unit price) or
    Rule E (a quantity the invoice's own arithmetic proves wrong). Mirrors `_persist_proven_reconciliation`'s
    conditions without writing anything.
    """
    normalized, _ = reconcile_invoice(normalized_from_persisted(invoice), ValidationService()._tolerance)
    by_order = {item.sort_order: item for item in invoice.items}
    count = 0
    for line in normalized.line_items:
        row = by_order.get(line.sort_order)
        if row is None:
            continue
        if (line.unit_price_reconciled and line.unit_price is not None and row.unit_price is not None
                and row.unit_price != line.unit_price):
            count += 1
        if line.quantity_reconciled and line.quantity is not None and row.quantity != line.quantity:
            count += 1
    return count


async def _invoice_for_number(session: AsyncSession, number: str) -> Invoice:
    ids = (await session.execute(select(Invoice.id).where(Invoice.invoice_number == number))).scalars().all()
    if not ids:
        raise ToolRefusal(f"No invoice numbered {number!r}.", [REFUSE_NOT_FOUND])
    if len(ids) > 1:
        raise ToolRefusal(f"{len(ids)} invoices share the number {number!r}; this tool will not guess which.",
                          [REFUSE_AMBIGUOUS])
    invoice = await InvoiceRepository(session).get_detail(ids[0], fresh=True)
    assert invoice is not None
    return invoice


async def _receipt_counts(session: AsyncSession, invoice_id: uuid.UUID) -> tuple[int, int]:
    """(POSTED, SUBMITTED) inventory receipts of the invoice; zero before the inventory tables exist."""
    if await inventory_service.inventory_schema(session) == inventory_service.SCHEMA_ABSENT:
        return 0, 0
    rows = dict((await session.execute(
        select(InventoryReceipt.status, func.count()).where(InventoryReceipt.invoice_id == invoice_id)
        .group_by(InventoryReceipt.status))).all())
    return int(rows.get(RECEIPT_POSTED, 0)), int(rows.get(RECEIPT_SUBMITTED, 0))


async def _plan_for(session: AsyncSession, invoice: Invoice) -> tuple[Plan, ValidationReport]:
    document = await session.get(Document, invoice.document_id)
    assert document is not None
    previous = [log.payload for log in await ProcessingLogRepository(session).for_document(invoice.document_id)
                if log.stage == PipelineStage.VALIDATION and log.payload]
    confidence = ((previous[-1] if previous else None) or {}).get("confidence") or {}
    report = build_report(invoice, ocr_confidence=confidence.get("ocr_confidence"),
                          ai_confidence=confidence.get("ai_confidence"),
                          ocr_text=getattr(document, "raw_ocr_text", None))

    exports = int((await session.execute(
        select(func.count()).select_from(EdiExport).where(EdiExport.invoice_id == invoice.id))).scalar_one())
    posted, submitted = await _receipt_counts(session, invoice.id)
    rewrites = rows_the_deployed_revalidation_would_rewrite(invoice)
    synced = document.status in {s.value for s in SYNCED_DOCUMENT_STATUSES}

    before = State(status=str(invoice.status), composite_confidence=_money(invoice.composite_confidence),
                   blocking_checks=list(invoice.blocking_checks) if invoice.blocking_checks is not None else None,
                   document_status=str(document.status))
    after = State(status=report.decision.value, composite_confidence=_composite(report),
                  blocking_checks=report.blocking_check_names(),
                  document_status=(document_status_for(report.decision).value if synced else str(document.status)))

    refusals: list[str] = []
    if posted:
        refusals.append(REFUSE_POSTED_RECEIPT)
    if document.status in INACTIVE_DOCUMENT_STATUSES:
        refusals.append(REFUSE_INACTIVE)
    if rewrites:
        refusals.append(REFUSE_WOULD_REWRITE)

    warnings: list[str] = []
    if before.status == "VALIDATED" and after.status != "VALIDATED":
        warnings.append("The invoice would leave VALIDATED: a Retailed mark shows 're-verify' and its document "
                        "moves out of COMPLETED.")
    if submitted:
        warnings.append("A submitted (not yet approved) inventory receipt would read as stale for this invoice.")
    if exports and after.blocking_checks:
        warnings.append("Historical EDI exports stay exactly as delivered; new PDI/TXT/CSV exports would be "
                        "blocked until the failing checks pass or an administrator records an override.")
    if not getattr(document, "raw_ocr_text", None):
        warnings.append("No stored OCR text: completeness cannot be judged (UNVERIFIED); only the financial "
                        "checks can block.")

    completeness = report.completeness or {}
    fingerprint = _fingerprint({
        "invoice_id": str(invoice.id), "before": asdict(before), "after": asdict(after),
        "exports": exports, "posted": posted, "submitted": submitted, "retailed": invoice.retailed_at is not None,
        "rewrites": rewrites, "refusals": refusals, "missing": completeness.get("missing_codes") or [],
    })
    plan = Plan(
        invoice_id=str(invoice.id), invoice_number=str(invoice.invoice_number), vendor=invoice.vendor_name,
        document_id=str(invoice.document_id), before=before, after=after,
        completeness=completeness.get("status"), missing_codes=list(completeness.get("missing_codes") or []),
        failed_checks=sorted({c.name for c in report.failed_checks}),
        review_reasons=list(report.review_reasons)[:8], historical_edi_exports=exports, posted_receipts=posted,
        submitted_receipts=submitted, retailed=invoice.retailed_at is not None,
        stored_ocr_text=bool(getattr(document, "raw_ocr_text", None)), rows_that_would_be_rewritten=rewrites,
        refusals=refusals, warnings=warnings, fingerprint=fingerprint,
    )
    return plan, report


async def plan(session: AsyncSession, invoice_number: str) -> Plan:
    """
    The dry run. Runs in a READ ONLY transaction (the database itself refuses any write) and takes no lock.
    Raises ToolRefusal when the number matches no invoice or more than one.
    """
    await session.execute(text("SET TRANSACTION READ ONLY"))
    invoice = await _invoice_for_number(session, invoice_number)
    return (await _plan_for(session, invoice))[0]


async def _admin_actor(session: AsyncSession, username: str | None):
    user = await UserRepository(session).get_by_username(username) if username else None
    if user is None or not user.is_active or user.role != UserRole.ADMIN.value:
        raise ToolRefusal("--actor must be the username of an active ADMIN account; nothing was written.")
    return user


async def apply(session: AsyncSession, invoice_number: str, *, expect_plan: str | None,
                actor_username: str | None) -> tuple[Plan, bool]:
    """
    Write the reviewed revalidation. Returns (plan, wrote). Raises ToolRefusal, with nothing written, unless the
    actor is an active ADMIN, no refusal applies, `expect_plan` equals the freshly computed plan's fingerprint and
    the plan is a change. The caller commits (or rolls back).
    """
    actor = await _admin_actor(session, actor_username)
    ids = (await session.execute(select(Invoice.id).where(Invoice.invoice_number == invoice_number))).scalars().all()
    if len(ids) != 1:
        await _invoice_for_number(session, invoice_number)          # raises the right refusal
    await inventory_service.lock_invoice(session, ids[0])           # the receipt, export and edit paths take it too
    invoice = await _invoice_for_number(session, invoice_number)
    current, report = await _plan_for(session, invoice)
    if current.refusals:
        raise ToolRefusal(f"Refused: {', '.join(current.refusals)}. Nothing was written.", current.refusals)
    if not expect_plan or expect_plan != current.fingerprint:
        raise ToolRefusal("The invoice no longer matches the plan that was reviewed (or no plan fingerprint was "
                          "given). Re-run the dry run and pass its fingerprint. Nothing was written.")
    if not current.changes:
        return current, False

    invoice.status = report.decision.value
    invoice.blocking_checks = report.blocking_check_names()
    invoice.composite_confidence = Decimal(str(report.confidence.composite))
    await session.flush()
    synced = await sync_document_status(session, invoice, report.decision)

    logs = ProcessingLogRepository(session)
    await logs.add(
        document_id=invoice.document_id, stage=PipelineStage.VALIDATION, status=LogStatus.SUCCESS,
        message="Revalidated by an administrator from the stored text; no OCR and no model call.",
        payload={**report.to_dict(), "document_status": document_status_for(report.decision).value,
                 **({"document_status_changed": [synced[0].value, synced[1].value]} if synced else {}),
                 "revalidation_tool": {"by": actor.username, "plan_sha256": current.fingerprint}},
        duration_ms=report.duration_ms,
    )
    await logs.add(
        document_id=invoice.document_id, stage=PipelineStage.LIFECYCLE,
        message=f"Revalidated by {actor.username} (revalidate-only tool): "
                f"{current.before.status} -> {current.after.status}.",
        payload={"event": AUDIT_EVENT, "invoice_id": current.invoice_id, "invoice_number": current.invoice_number,
                 "by": actor.username, "by_user_id": str(actor.id), "plan_sha256": current.fingerprint,
                 "before": asdict(current.before), "after": asdict(current.after),
                 "historical_edi_exports": current.historical_edi_exports, "rewrote_stored_lines": False},
    )
    await session.flush()
    return current, True
