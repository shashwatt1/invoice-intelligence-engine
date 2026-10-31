"""
Vendor merge — app/services/vendor_merge_service.py

Two rows for one supplier (the same vendor extracted under two spellings) are merged into one, so that vendor-wise filtering,
mapping and the upload picker see a single vendor.

The vendor that stays is the WINNER. The duplicate, the LOSER, is kept as a tombstone (`vendors.merged_into_id`), never
deleted: its identity-review history is an audit record that must keep pointing at it, and an invoice that later prints the
loser's old spelling resolves to the winner (VendorRepository.resolve) instead of creating the duplicate again.

What a merge repoints from the loser to the winner: invoices, product proposals of any status (vendor-item proposals name the
vendor whose item number it is), documents' chosen vendor, vendor requests, and the vendor item crosswalk. A crosswalk link the
winner already holds for the same vendor item code is redundant when it names the same item code (the loser's is retired, with
the reason) and a conflict when it names a different one (refused: a person decides which item code is right).

What it never does: change a line, a total, a store, a status, a mapping, an export or an inventory record; and it does not
touch the identity-review history. The Retailed attestation does not cover the vendor, so a merge never makes a Retailed mark
stale (tests/integration/test_vendor_merge_db.py proves that on a marked invoice).

`plan()` runs in a READ ONLY transaction and writes nothing; `apply()` repeats it under the vendors' row locks and writes only
for an active ADMIN, when nothing refuses it and the freshly computed plan's fingerprint equals the one that was reviewed.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.models.inventory import RECEIPT_POSTED, InventoryReceipt
from app.models.invoice import Invoice
from app.models.processing_log import PipelineStage
from app.models.product_data_proposal import ProductDataProposal
from app.models.user import UserRole
from app.models.vendor import VENDOR_CONFIRMED, Vendor, VendorIdentityReview, VendorMerge
from app.models.vendor_item_crosswalk import (
    CROSSWALK_CONFIRMED,
    CROSSWALK_RETIRED,
    VendorItemCrosswalk,
)
from app.models.vendor_request import VendorRequest
from app.repositories.processing_log_repository import ProcessingLogRepository
from app.repositories.user_repository import UserRepository
from app.services.vendor_matching import ExpectedVendor, names_match, tax_ids_match, vendor_agrees

AUDIT_EVENT = "vendor_merged"
MAX_LISTED = 25          # ids shown per group in a printed plan (the fingerprint covers all of them)


class VendorMergeRefusal(Exception):  # noqa: N818 — a refusal, not an error: nothing was written
    def __init__(self, message: str, reasons: list[str] | None = None) -> None:
        super().__init__(message)
        self.reasons = reasons or []


@dataclass(frozen=True)
class MergeOptions:
    """Explicit acknowledgements the operator must give for a merge that looks unusual. All default to refusing."""

    allow_both_confirmed: bool = False       # two vendors a person confirmed as distinct identities
    allow_different_tax_ids: bool = False    # both carry a tax id and they differ
    allow_different_names: bool = False      # no name of one matches a name of the other
    allow_received_invoices: bool = False    # an invoice of the loser has a POSTED inventory receipt


@dataclass
class MergePlan:
    winner_id: str
    loser_id: str
    winner: str
    loser: str
    refusals: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    invoice_ids: list[str] = field(default_factory=list)
    invoice_numbers: list[str] = field(default_factory=list)
    retailed_invoices: int = 0
    posted_receipt_invoices: int = 0
    proposal_ids: list[str] = field(default_factory=list)
    proposals_by_status: dict[str, int] = field(default_factory=dict)
    document_ids: list[str] = field(default_factory=list)
    request_ids: list[str] = field(default_factory=list)
    crosswalk_move: list[str] = field(default_factory=list)
    crosswalk_retire: list[str] = field(default_factory=list)
    crosswalk_conflicts: list[dict[str, str]] = field(default_factory=list)
    identity_reviews_left_on_loser: int = 0
    options: dict[str, bool] = field(default_factory=dict)
    fingerprint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _fingerprint(plan: MergePlan) -> str:
    """Over everything the operator reviewed: the pair, every row that would move, the refusals and the acknowledgements."""
    body = {k: v for k, v in plan.to_dict().items() if k not in {"fingerprint", "warnings"}}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def _label(vendor: Vendor) -> str:
    return f"{vendor.label} ({vendor.id})"


async def _plan_for(session: AsyncSession, winner_id: uuid.UUID, loser_id: uuid.UUID, options: MergeOptions) -> MergePlan:
    winner = await session.get(Vendor, winner_id, populate_existing=True)
    loser = await session.get(Vendor, loser_id, populate_existing=True)
    plan = MergePlan(
        winner_id=str(winner_id), loser_id=str(loser_id),
        winner=winner.label if winner else "(no such vendor)", loser=loser.label if loser else "(no such vendor)",
        options=asdict(options))
    refusals = plan.refusals
    if winner_id == loser_id:
        refusals.append("same_vendor")
    if winner is None:
        refusals.append("winner_not_found")
    if loser is None:
        refusals.append("loser_not_found")
    if refusals:
        plan.fingerprint = _fingerprint(plan)
        return plan

    if loser.merged_into_id is not None:
        refusals.append(f"loser_already_merged_into:{loser.merged_into_id}")
    if winner.merged_into_id is not None:
        refusals.append(f"winner_was_merged_into:{winner.merged_into_id} (merge into that vendor instead)")
    winner_confirmed = winner.identity_status == VENDOR_CONFIRMED
    loser_confirmed = loser.identity_status == VENDOR_CONFIRMED
    if loser_confirmed and not winner_confirmed:
        refusals.append("loser_is_confirmed_but_winner_is_not (merge the other way round: the confirmed vendor stays)")
    if loser_confirmed and winner_confirmed and not options.allow_both_confirmed:
        refusals.append("both_vendors_are_confirmed (pass --allow-both-confirmed if a person decided they are one supplier)")
    if (winner.tax_id and loser.tax_id and not tax_ids_match(winner.tax_id, loser.tax_id)
            and not options.allow_different_tax_ids):
        refusals.append("different_tax_ids (pass --allow-different-tax-ids to merge anyway)")
    winner_names = [n for n in (winner.display_name, winner.name) if n]
    loser_names = [n for n in (loser.display_name, loser.name) if n]
    if (not any(names_match(a, b) for a in winner_names for b in loser_names)
            and not (winner.tax_id and tax_ids_match(winner.tax_id, loser.tax_id))
            and not options.allow_different_names):
        refusals.append("no_name_in_common (pass --allow-different-names if they are the same supplier under different names)")

    # ---- what would move
    invoices = (await session.execute(
        select(Invoice.id, Invoice.invoice_number, Invoice.retailed_at, Invoice.vendor_name, Invoice.vendor_source)
        .where(Invoice.vendor_id == loser_id).order_by(Invoice.id))).all()
    plan.invoice_ids = [str(r.id) for r in invoices]
    plan.invoice_numbers = [r.invoice_number or "(no number)" for r in invoices]
    plan.retailed_invoices = sum(1 for r in invoices if r.retailed_at is not None)
    if invoices:
        plan.posted_receipt_invoices = int((await session.execute(
            select(func.count(func.distinct(InventoryReceipt.invoice_id))).where(
                InventoryReceipt.invoice_id.in_([r.id for r in invoices]),
                InventoryReceipt.status == RECEIPT_POSTED))).scalar_one())
        if plan.posted_receipt_invoices and not options.allow_received_invoices:
            # Only the vendor pointer moves and the receipt does not cover it, but an invoice whose goods are recorded as
            # received is locked against every other change: touching one is a decision, not a side effect.
            refusals.append(f"{plan.posted_receipt_invoices}_invoice(s)_with_a_posted_receipt (only their vendor moves; pass "
                            "--allow-received-invoices to include them)")
    expected = ExpectedVendor.of(winner)
    for row in invoices:
        if row.vendor_source == "operator" and expected is not None and row.vendor_name and not vendor_agrees(
                expected.names, row.vendor_name, selected_tax_id=expected.tax_id):
            plan.warnings.append(
                f"invoice {row.invoice_number or row.id}: the document prints {row.vendor_name!r}, which does not match "
                f"{winner.label!r}; its next revalidation will flag a vendor mismatch.")

    proposals = (await session.execute(
        select(ProductDataProposal.id, ProductDataProposal.status).where(ProductDataProposal.vendor_id == loser_id)
        .order_by(ProductDataProposal.id))).all()
    plan.proposal_ids = [str(r.id) for r in proposals]
    for row in proposals:
        plan.proposals_by_status[row.status] = plan.proposals_by_status.get(row.status, 0) + 1
    plan.document_ids = [str(i) for i in (await session.execute(
        select(Document.id).where(Document.chosen_vendor_id == loser_id).order_by(Document.id))).scalars().all()]
    plan.request_ids = [str(i) for i in (await session.execute(
        select(VendorRequest.id).where(VendorRequest.vendor_id == loser_id).order_by(VendorRequest.id))).scalars().all()]

    # ---- the crosswalk: the unique key is (vendor, vendor item code) among CONFIRMED links
    winner_links = {r.vendor_item_code: r for r in (await session.execute(
        select(VendorItemCrosswalk).where(VendorItemCrosswalk.vendor_id == winner_id,
                                          VendorItemCrosswalk.state == CROSSWALK_CONFIRMED))).scalars().all()}
    for link in (await session.execute(select(VendorItemCrosswalk).where(VendorItemCrosswalk.vendor_id == loser_id)
                                       .order_by(VendorItemCrosswalk.id))).scalars().all():
        held = winner_links.get(link.vendor_item_code) if link.state == CROSSWALK_CONFIRMED else None
        if held is None:
            plan.crosswalk_move.append(str(link.id))
        elif held.item_code == link.item_code:
            plan.crosswalk_retire.append(str(link.id))
        else:
            plan.crosswalk_conflicts.append({"vendor_item_code": link.vendor_item_code, "loser_item_code": link.item_code,
                                             "winner_item_code": held.item_code, "loser_link": str(link.id)})
    if plan.crosswalk_conflicts:
        refusals.append("crosswalk_conflict: the same vendor item code maps to different item codes ("
                        + ", ".join(f"{c['vendor_item_code']}: {c['loser_item_code']} vs {c['winner_item_code']}"
                                    for c in plan.crosswalk_conflicts) + "); decide which is right first")
    plan.identity_reviews_left_on_loser = int((await session.execute(
        select(func.count()).select_from(VendorIdentityReview).where(VendorIdentityReview.vendor_id == loser_id))).scalar_one())
    plan.fingerprint = _fingerprint(plan)
    return plan


async def plan(session: AsyncSession, winner_id: uuid.UUID, loser_id: uuid.UUID,
               options: MergeOptions | None = None) -> MergePlan:
    """The dry run: a READ ONLY transaction (the database itself refuses a write) and no lock."""
    await session.execute(text("SET TRANSACTION READ ONLY"))
    return await _plan_for(session, winner_id, loser_id, options or MergeOptions())


async def _admin_actor(session: AsyncSession, username: str | None):
    user = await UserRepository(session).get_by_username(username) if username else None
    if user is None or not user.is_active or user.role != UserRole.ADMIN.value:
        raise VendorMergeRefusal("--actor must be the username of an active ADMIN account; nothing was written.")
    return user


async def apply(session: AsyncSession, winner_id: uuid.UUID, loser_id: uuid.UUID, *, actor_username: str | None,
                basis: str | None, expect_plan: str | None, options: MergeOptions | None = None) -> tuple[MergePlan, VendorMerge]:
    """
    Write the reviewed merge. Raises VendorMergeRefusal, with nothing written, unless the actor is an active ADMIN, a basis
    is given, nothing refuses the merge and `expect_plan` equals the freshly computed fingerprint. The caller commits.
    """
    options = options or MergeOptions()
    actor = await _admin_actor(session, actor_username)
    reason = (basis or "").strip()
    if not reason:
        raise VendorMergeRefusal("--basis is required: say why these are the same supplier. Nothing was written.")
    if len(reason) > 1000:
        raise VendorMergeRefusal("--basis is too long (1000 characters at most). Nothing was written.")

    # Both vendor rows, in id order so two merges cannot deadlock; then the plan is judged under the locks.
    await session.execute(select(Vendor.id).where(Vendor.id.in_([winner_id, loser_id])).order_by(Vendor.id).with_for_update())
    current = await _plan_for(session, winner_id, loser_id, options)
    if current.refusals:
        raise VendorMergeRefusal(f"Refused: {'; '.join(current.refusals)}. Nothing was written.", current.refusals)
    if not expect_plan or expect_plan != current.fingerprint:
        raise VendorMergeRefusal("The vendors no longer match the plan that was reviewed (or no plan fingerprint was given). "
                                 "Re-run the dry run and pass its fingerprint. Nothing was written.")

    # Serialize with the export, receipt and correction paths, which take the invoice's row lock too.
    if current.invoice_ids:
        await session.execute(select(Invoice.id).where(Invoice.id.in_([uuid.UUID(i) for i in current.invoice_ids]))
                              .order_by(Invoice.id).with_for_update())
    now = datetime.now(UTC)
    ids = lambda values: [uuid.UUID(v) for v in values]  # noqa: E731

    if current.invoice_ids:
        await session.execute(update(Invoice).where(Invoice.id.in_(ids(current.invoice_ids))).values(vendor_id=winner_id)
                              .execution_options(synchronize_session=False))
    if current.proposal_ids:
        await session.execute(update(ProductDataProposal).where(ProductDataProposal.id.in_(ids(current.proposal_ids)))
                              .values(vendor_id=winner_id).execution_options(synchronize_session=False))
    if current.document_ids:
        await session.execute(update(Document).where(Document.id.in_(ids(current.document_ids)))
                              .values(chosen_vendor_id=winner_id).execution_options(synchronize_session=False))
    if current.request_ids:
        await session.execute(update(VendorRequest).where(VendorRequest.id.in_(ids(current.request_ids)))
                              .values(vendor_id=winner_id).execution_options(synchronize_session=False))
    if current.crosswalk_retire:
        await session.execute(update(VendorItemCrosswalk).where(VendorItemCrosswalk.id.in_(ids(current.crosswalk_retire))).values(
            vendor_id=winner_id, state=CROSSWALK_RETIRED, retired_at=now, retired_by=actor.username,
            retire_reason=f"Vendor {current.loser} was merged into {current.winner}, which already holds this link."[:1000],
        ).execution_options(synchronize_session=False))
    # every other link of the loser, retired or confirmed, follows to the winner
    await session.execute(update(VendorItemCrosswalk).where(VendorItemCrosswalk.vendor_id == loser_id)
                          .values(vendor_id=winner_id).execution_options(synchronize_session=False))

    loser = await session.get(Vendor, loser_id, populate_existing=True)
    loser.merged_into_id = winner_id
    details = {k: v for k, v in current.to_dict().items()
               if k in {"invoice_ids", "invoice_numbers", "retailed_invoices", "posted_receipt_invoices", "proposal_ids",
                        "proposals_by_status", "document_ids", "request_ids", "crosswalk_move", "crosswalk_retire",
                        "identity_reviews_left_on_loser", "options", "warnings"}}
    merge = VendorMerge(loser_vendor_id=loser_id, winner_vendor_id=winner_id, merged_by=actor.username,
                        merged_by_user_id=actor.id, basis=reason, plan_sha256=current.fingerprint, details=details)
    session.add(merge)
    await session.flush()

    logs = ProcessingLogRepository(session)
    document_of: dict[uuid.UUID, uuid.UUID] = {}
    if current.invoice_ids:
        document_of = dict((await session.execute(
            select(Invoice.id, Invoice.document_id).where(Invoice.id.in_(ids(current.invoice_ids))))).all())
    for invoice_id, document_id in document_of.items():
        await logs.add(
            document_id=document_id, stage=PipelineStage.MANUAL_CORRECTION,
            message=f"Vendor merged by {actor.username}: {current.loser} -> {current.winner}.",
            payload={"event": AUDIT_EVENT, "invoice_id": str(invoice_id), "by": actor.username, "from_vendor_id": str(loser_id),
                     "to_vendor_id": str(winner_id), "plan_sha256": current.fingerprint, "basis": reason})
    await session.flush()
    return current, merge
