"""
Invoice workflow — app/services/invoice_workflow_service.py

Two invoice-scoped workflow facts, both set by a MANAGER or ADMIN:

1. The original invoice / chat link. An https:// URL a reviewer pastes so
   the source image or its chat message can be reopened while mapping. It is
   only stored and shown as a link: never fetched, previewed or checked for
   reachability (a private chat link still needs the viewer's own access).

2. Retailed — a person's attestation that the invoice was verified against
   the source document and is finished. NOT goods receipt: it never creates
   or changes inventory, and never touches a mapping or Product Master.

   It can be set only when the invoice is VALIDATED and the EDI export ledger
   records a delivered PDI file byte-for-byte equal to the file the invoice
   produces now. A blocked export writes no ledger row, so a failed attempt
   never counts; with the ledger off there is no proof, so nothing can be
   marked. It is never trusted blindly afterwards: an attestation fingerprint
   is stored, and the status reads "retailed" only while the invoice is still
   VALIDATED and every attested value is unchanged ("retailed_stale" until a
   person re-verifies and re-marks it, or reopens it). See the Retailed
   section below for what is automatic and what is the person's check.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
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
    # Store the scheme in lower case ("HTTPS://" -> "https://"): the database
    # CHECK (reference_url LIKE 'https://%') is case-sensitive, and one
    # canonical form keeps the two consistent. Host and path are kept as given.
    return "https://" + url[len("https://"):]


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
#
# Retailed is a person's attestation that THIS invoice — its lines, quantities,
# prices, line totals, subtotal, discounts, deposits and totals, and the
# mappings that turn it into a PDI file — has been checked against the source
# document and is finished. It is not goods receipt and never touches inventory.
#
# Automatic (enforced here):
#   * the invoice's status is VALIDATED: the deterministic checks passed (line
#     arithmetic, subtotal, reconciliation, required fields, date) with enough
#     confidence. A REVIEW_REQUIRED invoice cannot be marked;
#   * a delivered PDI file is recorded in the EDI export ledger and is exactly
#     the file the invoice produces now (same SHA-256);
#   * staleness: an attestation fingerprint — a SHA-256 over every value the
#     attestation covers (see attestation_fingerprint) — is stored when marked;
#     any later change to those values, or the invoice leaving VALIDATED, shows
#     the mark as no longer current until a person re-verifies and re-marks it.
# Manual (the person marking it):
#   * comparing the extracted lines with the paper invoice. No software check
#     can tell from the extracted data alone that a line on the paper was never
#     read; marking Retailed is the person's statement that they compared them.

STATUS_VALIDATED = "VALIDATED"


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


def _canonical(value: Any) -> Any:
    """Stable text for hashing: Decimals by value (30 == 30.0000), dates ISO, ids as text."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def attestation_fingerprint(invoice: Invoice, units_by_item_code: Mapping[str, int], pdi_sha: str | None) -> str:
    """
    SHA-256 over everything a Retailed attestation covers: the header figures,
    every line (including its type, so an added, voided or duplicate-decided row
    counts), the units-per-case used for each code, and the PDI file the
    invoice produces now (None when it cannot be exported). Pure.
    """
    header = {name: _canonical(getattr(invoice, name)) for name in (
        "invoice_number", "invoice_date", "currency", "store_id", "subtotal", "tax_amount",
        "discount_amount", "deposit_total", "fuel_surcharge", "grand_total")}
    lines = [
        {name: _canonical(getattr(item, name)) for name in (
            "sort_order", "line_type", "product_sku", "description", "pack_size", "quantity",
            "unit_price", "line_total", "discount", "deposit", "tax_rate")}
        for item in sorted(invoice.items, key=lambda i: i.sort_order)
    ]
    mappings = {code: units_by_item_code[code] for code in sorted(units_by_item_code)}
    payload = json.dumps({"header": header, "lines": lines, "mappings": mappings, "pdi_sha256": pdi_sha},
                         sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def status_for(invoice: Invoice, current_fingerprint: str | None) -> str:
    """Retailed only while the invoice is VALIDATED and everything attested is unchanged."""
    if invoice.retailed_at is None:
        return STATUS_NOT_RETAILED
    current = (invoice.status == STATUS_VALIDATED and current_fingerprint is not None
               and current_fingerprint == invoice.retailed_attestation_sha256)
    return STATUS_RETAILED if current else STATUS_RETAILED_STALE


def current_facts(invoice: Invoice, units_by_item_code: Mapping[str, int]) -> tuple[str | None, str | None, str]:
    """
    (SHA-256 of the PDI the invoice produces now or None, why not exportable,
    attestation fingerprint) — the export endpoint's own gate and builder.
    """
    if invoice.store_id is None:
        return None, "The invoice has no store yet.", attestation_fingerprint(invoice, units_by_item_code, None)
    eligibility = persisted_pdi_export_eligibility(invoice, units_by_item_code)
    if not eligibility.allowed:
        return (None, eligibility.blocked_reason or "The invoice cannot be exported for PDI right now.",
                attestation_fingerprint(invoice, units_by_item_code, None))
    pdi = pdi_sha256(build_pdi_export(invoice, units_by_item_code))
    return pdi, None, attestation_fingerprint(invoice, units_by_item_code, pdi)


async def latest_export(session: AsyncSession, invoice_id: uuid.UUID) -> EdiExport | None:
    return (await session.execute(
        select(EdiExport).where(EdiExport.invoice_id == invoice_id)
        .order_by(EdiExport.export_number.desc()).limit(1)
    )).scalar_one_or_none()


async def _evaluate(session: AsyncSession, invoice: Invoice) -> tuple[CompletionState, str, EdiExport | None]:
    ledger_enabled = get_settings().edi_export_ledger
    units = ((await invoice_commercial_resolution(session, invoice)).units
             if invoice.store_id is not None else {})
    current_pdi, blocked, fingerprint = current_facts(invoice, units)
    latest = await latest_export(session, invoice.id)
    status = status_for(invoice, fingerprint)

    reason: str | None
    if status == STATUS_RETAILED:
        reason = "Already marked Retailed."
    elif invoice.status != STATUS_VALIDATED:
        reason = (f"The invoice must be VALIDATED before it can be marked Retailed (it is {invoice.status}). "
                  "Resolve the failed checks or correct the invoice first.")
    elif current_pdi is None:
        reason = f"The invoice cannot be exported now: {blocked}"
    elif latest is None:
        reason = "No delivered PDI file is recorded for this invoice yet." + (
            "" if ledger_enabled else " The EDI export ledger is off, so exports are not recorded.")
    elif latest.sha256 != current_pdi:
        reason = (f"The invoice or its mappings changed after the last recorded export (#{latest.export_number}). "
                  "Generate the PDI file again, then mark it.")
    else:
        reason = None
    state = CompletionState(
        status=status, can_mark=reason is None, reason=reason,
        latest_export_number=latest.export_number if latest else None,
        latest_export_at=latest.created_at if latest else None,
        ledger_enabled=ledger_enabled,
        retailed_at=invoice.retailed_at, retailed_by=invoice.retailed_by,
    )
    return state, fingerprint, latest


async def completion_state(session: AsyncSession, invoice: Invoice) -> CompletionState:
    return (await _evaluate(session, invoice))[0]


async def _locked(session: AsyncSession, invoice_id: uuid.UUID) -> Invoice | None:
    """
    Lock the invoice row for the rest of the transaction, then re-read it from
    the database (fresh=True): a copy this session loaded earlier — before
    another transaction marked or reopened it — is overwritten, so competing
    marks and reopens are decided one at a time, each on committed state.
    """
    found = (await session.execute(
        select(Invoice.id).where(Invoice.id == invoice_id).with_for_update())).scalar_one_or_none()
    return await InvoiceRepository(session).get_detail(invoice_id, fresh=True) if found else None


async def mark_retailed(session: AsyncSession, invoice_id: uuid.UUID, user) -> CompletionState | None:
    """
    Record the person's attestation. Refused unless the invoice is VALIDATED
    and the ledger records its current PDI file. None when it does not exist.
    """
    invoice = await _locked(session, invoice_id)
    if invoice is None:
        return None
    state, fingerprint, latest = await _evaluate(session, invoice)
    if not state.can_mark:
        raise ValidationError(message=state.reason or "This invoice cannot be marked Retailed now.",
                              detail={"invoice_id": str(invoice.id), "status": state.status})
    assert latest is not None                                # can_mark guarantees it
    invoice.retailed_at = datetime.now(UTC)
    invoice.retailed_by = user.username
    invoice.retailed_by_user_id = user.id
    invoice.retailed_edi_export_id = latest.id
    invoice.retailed_attestation_sha256 = fingerprint
    await ProcessingLogRepository(session).add(
        document_id=invoice.document_id,
        stage=PipelineStage.LIFECYCLE,
        message=f"Marked Retailed by {user.username} (PDI export #{latest.export_number}).",
        payload={"event": "invoice_retailed", "by": user.username, "edi_export_id": str(latest.id),
                 "export_number": latest.export_number, "pdi_sha256": latest.sha256,
                 "attestation_sha256": fingerprint, "invoice_status": invoice.status,
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
    invoice.retailed_attestation_sha256 = None
    await ProcessingLogRepository(session).add(
        document_id=invoice.document_id,
        stage=PipelineStage.LIFECYCLE,
        message=f"Retailed mark removed by {user.username}.",
        payload={"event": "invoice_retail_reopened", "by": user.username, "note": note, "previous": previous},
    )
    await session.flush()
    return await completion_state(session, invoice)
