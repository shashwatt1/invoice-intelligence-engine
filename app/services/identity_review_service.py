"""
Identity Review Service — app/services/identity_review_service.py

Human review for identifier resolution, built on the EXISTING proposal
workflow (product_data_proposals + proposal_service.approve), not a second
review system. Phase 3C, shadow only.

    proposal_specs()           pure: which resolver outcomes deserve a proposal,
                               and with what evidence — a dry-run list
    propose_line_identity()    record ONE pending proposal, when explicitly called
    propose_supplier_crosswalk()
    apply_approved()           called by proposal_service.approve() AFTER a person
                               approved: appends a REVIEW_CONFIRMED resolution row,
                               or inserts one CONFIRMED supplier identifier

Nothing here approves anything, and nothing is called by the resolver, the
pipeline or the EDI writer. The resolver has no write path; the only writes
below happen on a human approval, in the reviewer's transaction:

  * a line identity -> a NEW invoice_line_resolutions row (REVIEW_CONFIRMED);
    the resolver's earlier row stays;
  * a supplier crosswalk -> a NEW master_product_identifiers row
    (SUPPLIER_ITEM_ID, evidence_state CONFIRMED, source_system
    'invoice_identity_review') on the one product whose canonical UPC is the
    approved GTIN. Existing Product Master rows are never updated.

Refusals raise ValueError, which proposal_service.approve's caller rolls back.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product_data_proposal import (
    ENTITY_LINE_IDENTITY,
    ENTITY_SUPPLIER_CROSSWALK,
    FIELD_CANONICAL_GTIN,
    IDENTITY_SOURCES,
    SOURCE_ASSOCIATION_PROPOSAL,
    SOURCE_RESOLVER_DERIVED,
    SOURCE_TEMPLATE_COMPOSITION,
    STATUS_APPROVED,
    ProductDataProposal,
)
from app.models.product_master import (
    DERIVE_NONE,
    ID_SUPPLIER_ITEM_ID,
    STATE_CONFIRMED,
    MasterProduct,
    MasterProductIdentifier,
)
from app.repositories.identifier_resolution_repository import IdentifierResolutionRepository
from app.repositories.product_data_proposal_repository import ProductDataProposalRepository
from app.services.product_master.identifiers import has_valid_upc_check
from app.services.product_master.resolution_records import review_confirmed_row

CROSSWALK_SOURCE_SYSTEM = "invoice_identity_review"


def _require_gtin12(gtin: str) -> None:
    if not (isinstance(gtin, str) and gtin.isdigit() and len(gtin) == 12 and has_valid_upc_check(gtin)):
        raise ValueError(f"{gtin!r} is not a complete, check-valid GTIN-12; nothing is repaired or padded.")


def proposal_specs(result: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Dry run: the proposals a reviewer would be asked to decide for one resolved
    invoice. Pure; creates nothing.

      association_proposal   an OCR-order proposal whose value is a valid GTIN-12
                             (the suggested row, never applied automatically)
      resolver_derived       a single CORROBORATED or VALID_UNMATCHED GTIN-12
      template_composition   a DRAFT/APPROVED rule hypothesis (D12 governs rules)
    """
    specs = []
    by_line = {ln["line_index"]: ln for ln in result["lines"] if ln["line_index"] is not None}
    for prop in result.get("association_proposals", []):
        raw = "".join(ch for ch in prop["raw"] if ch.isdigit())
        if len(raw) == 12 and has_valid_upc_check(raw):
            specs.append({"entity_type": ENTITY_LINE_IDENTITY, "line_index": prop["ocr_order_suggests_line"],
                          "gtin": raw, "source": SOURCE_ASSOCIATION_PROPOSAL,
                          "evidence": {"proposal": prop, "auto_applied": False}})
    for line in by_line.values():
        if line["status"] in ("CORROBORATED", "VALID_UNMATCHED") and line["chosen_kind"] == "GTIN_12":
            specs.append({"entity_type": ENTITY_LINE_IDENTITY, "line_index": line["line_index"],
                          "gtin": line["chosen_gtin"], "source": SOURCE_RESOLVER_DERIVED,
                          "evidence": {"status": line["status"], "reasons": line["reasons"]}})
        for cand in line["candidates"]:
            if cand.get("kind") == "COMPOSED_GTIN_12_HYPOTHESIS":
                specs.append({"entity_type": ENTITY_LINE_IDENTITY, "line_index": line["line_index"],
                              "gtin": cand["value"], "source": SOURCE_TEMPLATE_COMPOSITION,
                              "evidence": {"rule": cand.get("rule"), "rule_state": cand.get("rule_state")}})
    return specs


async def propose_line_identity(
    session: AsyncSession, *, store_id: uuid.UUID, invoice_id: uuid.UUID, invoice_item_id: uuid.UUID,
    gtin: str, source: str, evidence: dict[str, Any], proposed_by: str,
) -> ProductDataProposal:
    """Record one PENDING line-identity proposal. It is not approved and changes nothing else."""
    _require_gtin12(gtin)
    if source not in IDENTITY_SOURCES:
        raise ValueError(f"source must be one of {sorted(IDENTITY_SOURCES)}")
    return await ProductDataProposalRepository(session).create(
        store_id=store_id, entity_type=ENTITY_LINE_IDENTITY, entity_key=str(invoice_item_id),
        field=FIELD_CANONICAL_GTIN, proposed_value=gtin, current_value=None, source=source,
        proposed_by=proposed_by, invoice_id=invoice_id, evidence=evidence,
        reason="Identifier resolution needs a person to confirm this line's GTIN.",
    )


async def propose_supplier_crosswalk(
    session: AsyncSession, *, store_id: uuid.UUID, supplier_scope: str, supplier_item_id: str, gtin: str,
    source: str, evidence: dict[str, Any], proposed_by: str, invoice_id: uuid.UUID | None = None,
) -> ProductDataProposal:
    """Record one PENDING (supplier scope, supplier item id) -> GTIN crosswalk proposal."""
    _require_gtin12(gtin)
    if source not in IDENTITY_SOURCES:
        raise ValueError(f"source must be one of {sorted(IDENTITY_SOURCES)}")
    key = f"{supplier_scope}:{supplier_item_id}"
    if len(key) > 64:
        raise ValueError("supplier crosswalk key exceeds the proposal key width")
    return await ProductDataProposalRepository(session).create(
        store_id=store_id, entity_type=ENTITY_SUPPLIER_CROSSWALK, entity_key=key,
        field=FIELD_CANONICAL_GTIN, proposed_value=gtin, current_value=None, source=source,
        proposed_by=proposed_by, invoice_id=invoice_id, evidence=evidence,
        reason="Supplier item id to GTIN crosswalk awaiting a person's confirmation.",
    )


async def apply_approved(session: AsyncSession, proposal: ProductDataProposal, *, reviewed_by: str) -> str:
    """Apply one APPROVED identity proposal. Returns what was written, for ApprovalResult.applied_to."""
    if proposal.status != STATUS_APPROVED:
        raise ValueError("only an APPROVED proposal can be applied")
    gtin = str(proposal.proposed_value)
    _require_gtin12(gtin)
    if proposal.entity_type == ENTITY_LINE_IDENTITY:
        return await _apply_line_identity(session, proposal, gtin, reviewed_by)
    if proposal.entity_type == ENTITY_SUPPLIER_CROSSWALK:
        return await _apply_supplier_crosswalk(session, proposal, gtin)
    raise ValueError(f"not an identity proposal: {proposal.entity_type}")


async def _apply_line_identity(session: AsyncSession, proposal: ProductDataProposal, gtin: str,
                               reviewed_by: str) -> str:
    item_id = uuid.UUID(proposal.entity_key)
    repo = IdentifierResolutionRepository(session)
    history = await repo.history_for_item(item_id)
    if not history:
        raise ValueError("no resolution record exists for this line; resolve it before confirming")
    previous = history[-1]
    row = review_confirmed_row(
        previous={c.name: getattr(previous, c.name) for c in previous.__table__.columns},
        gtin=gtin, proposal_id=proposal.id, reviewed_by=reviewed_by, run_id=uuid.uuid4())
    [record] = await repo.append_resolutions([row])
    return f"invoice_line_resolutions:{record.id}"


async def _apply_supplier_crosswalk(session: AsyncSession, proposal: ProductDataProposal, gtin: str) -> str:
    scope, _, supplier_item_id = proposal.entity_key.partition(":")
    if not scope or not supplier_item_id:
        raise ValueError("malformed supplier crosswalk key")
    products = list((await session.execute(
        select(MasterProduct).where(MasterProduct.canonical_upc == gtin))).scalars())
    if len(products) != 1:
        raise ValueError(f"exactly one Product Master product must hold canonical UPC {gtin}; found {len(products)}")
    product = products[0]
    existing = (await session.execute(select(MasterProductIdentifier).where(
        MasterProductIdentifier.product_id == product.id,
        MasterProductIdentifier.identifier_type == ID_SUPPLIER_ITEM_ID,
        MasterProductIdentifier.raw_value == supplier_item_id,
        MasterProductIdentifier.source_system == CROSSWALK_SOURCE_SYSTEM,
    ))).scalars().first()
    if existing is not None:
        return f"master_product_identifiers:{existing.id} (already recorded)"
    identifier = MasterProductIdentifier(
        product_id=product.id, raw_value=supplier_item_id, normalized_value=supplier_item_id,
        identifier_type=ID_SUPPLIER_ITEM_ID, derivation=DERIVE_NONE,
        derivation_detail={"proposal_id": str(proposal.id), "gtin": gtin, "supplier_scope": scope},
        evidence_state=STATE_CONFIRMED, source_system=CROSSWALK_SOURCE_SYSTEM, source_distributor=scope,
    )
    session.add(identifier)
    await session.flush()
    return f"master_product_identifiers:{identifier.id}"
