"""
Receiving eligibility — app/services/receiving_eligibility.py

A FUTURE CONTRACT, deliberately unused: the single rule the receiving /
purchase-transaction layer (Phase E) will apply to decide whether an invoice
may count as goods received. Nothing calls it yet and no receiving record
exists; it is here, pure and tested, so that layer cannot drift from the rule.

An invoice counts for receiving only when ALL hold:
  1. invoices.status is VALIDATED;
  2. it has a store, and that store is a PHYSICAL store — a source identity
     (e.g. Item Sales 47708760) never counts, however it got there;
  3. its document was not withdrawn (STOPPED or BINNED). Binning leaves the
     invoice's own status VALIDATED, so the invoice alone cannot say this.

Why no COMPLETED requirement: document_status_for(VALIDATED) is COMPLETED, and
the lifecycle only ever moves a VALIDATED invoice's document out of COMPLETED
by withdrawing it (STOPPED/BINNED) — which rule 3 already excludes. Requiring
COMPLETED as well would restate that, not add a condition.

Vendor confirmation does not gate receiving (an agreed business decision);
per-line product/commercial interpretation is a separate, later question.
Pure: reads attributes of the objects given, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.models.document import DocumentStatus
from app.models.store import KIND_PHYSICAL
from app.services.validation.report import ProcessingDecision

NOT_VALIDATED = "invoice_not_validated"
STORE_PENDING = "store_pending"
STORE_NOT_LOADED = "store_record_missing"
NOT_PHYSICAL_STORE = "store_is_not_physical"
DOCUMENT_WITHDRAWN = "document_withdrawn"

_WITHDRAWN = frozenset({DocumentStatus.STOPPED.value, DocumentStatus.BINNED.value})


@dataclass(frozen=True)
class ReceivingEligibility:
    eligible: bool
    reasons: list[str] = field(default_factory=list)   # every reason it is not, in rule order


def receiving_eligibility(invoice: Any, store: Any | None, document: Any) -> ReceivingEligibility:
    """Whether `invoice` may count for receiving, and every reason it may not."""
    reasons: list[str] = []
    if str(invoice.status) != ProcessingDecision.VALIDATED.value:
        reasons.append(NOT_VALIDATED)
    if invoice.store_id is None:
        reasons.append(STORE_PENDING)
    elif store is None or store.id != invoice.store_id:
        reasons.append(STORE_NOT_LOADED)
    elif store.kind != KIND_PHYSICAL:
        reasons.append(NOT_PHYSICAL_STORE)
    if str(document.status) in _WITHDRAWN:
        reasons.append(DOCUMENT_WITHDRAWN)
    return ReceivingEligibility(eligible=not reasons, reasons=reasons)
