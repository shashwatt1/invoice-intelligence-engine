"""
PM Approvals work assignment — app/services/review_assignment_service.py

Reviewing the Product Master queue is shared work. An administrator hands it out IN QUEUE ORDER — the order every
reviewer sees in PM Approvals (conflicts first, then by UPC) — e.g. the first 250 undecided candidates to one
reviewer, the next 250 to another, so two people never work the same candidate:

  * assign_in_order  — the next N unassigned, undecided candidates per person, in order. Candidates already
                       assigned or already decided are skipped, so running it again only hands out what is left.
  * reassign         — move chosen candidates to another person, or unassign them.
  * unassign_undecided — take back the undecided work of one person (or everyone), e.g. to hand it out again.
  * ensure_may_decide — a MANAGER deciding on a candidate assigned to SOMEONE ELSE is refused (409); an
                       ADMIN may always decide. Unassigned candidates stay open to every reviewer.

Assignment never changes a candidate, its review version or history, and never reaches the PDI export.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AssignedElsewhereError, RecordNotFoundError, ValidationError
from app.models.commercial_review_assignment import CommercialReviewAssignment
from app.models.product_master import (
    COMMERCIAL_CONFLICT,
    STATE_PENDING,
    STATE_REVIEW_REQUIRED,
    MasterCommercialMapping,
    MasterProduct,
)
from app.models.user import User, UserRole

MAX_PER_PERSON = 5000
DECIDING_ROLES = (UserRole.MANAGER.value, UserRole.ADMIN.value)

_UNDECIDED = MasterCommercialMapping.approval_state.in_((STATE_REVIEW_REQUIRED, STATE_PENDING))
# Exactly the PM Approvals list order (MasterCommercialRepository.list_candidates).
_QUEUE_ORDER = ((MasterCommercialMapping.commercial_unit_basis != COMMERCIAL_CONFLICT), MasterProduct.canonical_upc,
                MasterCommercialMapping.id)


@dataclass(frozen=True)
class AssignedBlock:
    user_id: uuid.UUID
    username: str
    requested: int
    assigned: int
    first_upc: str | None
    last_upc: str | None


@dataclass(frozen=True)
class PersonWorkload:
    user_id: uuid.UUID
    username: str
    awaiting: int      # assigned and still undecided
    decided: int       # assigned and decided since


async def _reviewers(session: AsyncSession, user_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, User]:
    users = {u.id: u for u in (await session.execute(select(User).where(User.id.in_(user_ids)))).scalars()}
    for user_id in user_ids:
        user = users.get(user_id)
        if user is None:
            raise RecordNotFoundError(message="Account not found.", detail={"user_id": str(user_id)})
        if not user.is_active or user.role not in DECIDING_ROLES:
            raise ValidationError(
                message=f"{user.username} cannot be given review work: only active managers and administrators "
                        "decide in PM Approvals.",
                detail={"user_id": str(user_id), "reason": "not_a_reviewer"})
    return users


async def assign_in_order(session: AsyncSession, plan: Sequence[tuple[uuid.UUID, int]], *,
                          actor: User) -> tuple[list[AssignedBlock], int]:
    """
    Hand out the queue in order: for each (person, count) the next `count` unassigned, undecided candidates.
    Returns what each person got (with the first and last UPC of their block) and how many unassigned
    candidates are still waiting afterwards. The candidates taken are locked, so two administrators assigning
    at once never hand out the same candidate twice.
    """
    if not plan:
        raise ValidationError(message="Name at least one person and how many candidates they get.",
                              detail={"reason": "empty_plan"})
    for user_id, count in plan:
        if count < 1 or count > MAX_PER_PERSON:
            raise ValidationError(message=f"Each count must be between 1 and {MAX_PER_PERSON}.",
                                  detail={"user_id": str(user_id), "count": count})
    users = await _reviewers(session, [user_id for user_id, _ in plan])

    already = select(CommercialReviewAssignment.mapping_id).where(
        CommercialReviewAssignment.mapping_id == MasterCommercialMapping.id).exists()
    rows = (await session.execute(
        select(MasterCommercialMapping.id, MasterProduct.canonical_upc)
        .join(MasterProduct, MasterProduct.id == MasterCommercialMapping.product_id)
        .where(_UNDECIDED, ~already)
        .order_by(*_QUEUE_ORDER)
        .limit(sum(count for _, count in plan))
        .with_for_update(of=MasterCommercialMapping, skip_locked=True)
    )).all()

    blocks: list[AssignedBlock] = []
    cursor = 0
    for user_id, count in plan:
        chunk = rows[cursor: cursor + count]
        cursor += len(chunk)
        user = users[user_id]
        session.add_all([
            CommercialReviewAssignment(mapping_id=mapping_id, assigned_user_id=user.id,
                                       assigned_username=user.username, assigned_by_user_id=actor.id,
                                       assigned_by=actor.username)
            for mapping_id, _ in chunk
        ])
        blocks.append(AssignedBlock(user.id, user.username, count, len(chunk),
                                    chunk[0][1] if chunk else None, chunk[-1][1] if chunk else None))
    await session.flush()
    return blocks, await unassigned_awaiting(session)


async def reassign(session: AsyncSession, mapping_ids: Sequence[uuid.UUID], user_id: uuid.UUID | None, *,
                   actor: User) -> int:
    """Give chosen candidates to one person, or unassign them (user_id None). Returns how many changed."""
    ids = list(dict.fromkeys(mapping_ids))
    if not ids:
        return 0
    found = set((await session.execute(
        select(MasterCommercialMapping.id).where(MasterCommercialMapping.id.in_(ids)))).scalars())
    missing = [str(i) for i in ids if i not in found]
    if missing:
        raise RecordNotFoundError(message="Candidate not found.", detail={"mapping_ids": missing[:20]})
    await session.execute(delete(CommercialReviewAssignment).where(CommercialReviewAssignment.mapping_id.in_(ids)))
    if user_id is not None:
        user = (await _reviewers(session, [user_id]))[user_id]
        session.add_all([CommercialReviewAssignment(mapping_id=i, assigned_user_id=user.id,
                                                    assigned_username=user.username, assigned_by_user_id=actor.id,
                                                    assigned_by=actor.username) for i in ids])
    await session.flush()
    return len(ids)


async def unassign_undecided(session: AsyncSession, user_id: uuid.UUID | None = None) -> int:
    """Take back undecided work (one person's, or everyone's). Decided candidates keep who they were assigned to."""
    undecided = select(MasterCommercialMapping.id).where(_UNDECIDED)
    statement = delete(CommercialReviewAssignment).where(CommercialReviewAssignment.mapping_id.in_(undecided))
    if user_id is not None:
        statement = statement.where(CommercialReviewAssignment.assigned_user_id == user_id)
    result = await session.execute(statement)
    await session.flush()
    return result.rowcount or 0


async def ensure_may_decide(session: AsyncSession, mapping_ids: Sequence[uuid.UUID], user: User) -> None:
    """Refuse a MANAGER deciding on a candidate assigned to someone else. ADMIN and unassigned: allowed."""
    if user.role == UserRole.ADMIN.value or not mapping_ids:
        return
    others = (await session.execute(
        select(CommercialReviewAssignment.mapping_id, CommercialReviewAssignment.assigned_username)
        .where(CommercialReviewAssignment.mapping_id.in_(list(mapping_ids)),
               CommercialReviewAssignment.assigned_user_id != user.id)
    )).all()
    if others:
        names = sorted({name for _, name in others})
        raise AssignedElsewhereError(
            message=(f"{'This candidate is' if len(mapping_ids) == 1 else f'{len(others)} of these candidates are'} "
                     f"assigned to {', '.join(names)}. Ask an administrator to reassign it if you should take it."),
            detail={"reason": "assigned_elsewhere", "assigned_to": names,
                    "mapping_ids": [str(m) for m, _ in others][:50]})


async def unassigned_awaiting(session: AsyncSession) -> int:
    already = select(CommercialReviewAssignment.mapping_id).where(
        CommercialReviewAssignment.mapping_id == MasterCommercialMapping.id).exists()
    return (await session.execute(
        select(func.count()).select_from(MasterCommercialMapping).where(_UNDECIDED, ~already))).scalar_one()


async def workloads(session: AsyncSession) -> list[PersonWorkload]:
    """Per person: assigned candidates still awaiting a decision, and those decided since."""
    undecided = func.count().filter(_UNDECIDED)
    rows = (await session.execute(
        select(CommercialReviewAssignment.assigned_user_id, User.username, undecided,
               func.count().filter(~_UNDECIDED))
        .join(MasterCommercialMapping, MasterCommercialMapping.id == CommercialReviewAssignment.mapping_id)
        .join(User, User.id == CommercialReviewAssignment.assigned_user_id)
        .group_by(CommercialReviewAssignment.assigned_user_id, User.username)
        .order_by(User.username)
    )).all()
    return [PersonWorkload(user_id, username, awaiting, decided) for user_id, username, awaiting, decided in rows]
