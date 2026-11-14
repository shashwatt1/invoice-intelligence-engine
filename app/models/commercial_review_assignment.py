"""
Who reviews which PM Approvals candidate — app/models/commercial_review_assignment.py

Reviewing the Product Master queue is shared work. An administrator hands out the queue in order ("the first 250
to one reviewer, the next 250 to another"), so two people never work the same candidate. One row per assigned
candidate; a candidate with no row is unassigned.

Assignment is about WHO decides, never WHAT: it does not change the candidate, its review version or its
history, and it is never part of the PDI export.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class CommercialReviewAssignment(Base):
    __tablename__ = "commercial_review_assignments"

    mapping_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("master_commercial_mappings.id", ondelete="CASCADE"), primary_key=True,
    )
    assigned_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    assigned_username: Mapped[str] = mapped_column(String(64), nullable=False,
                                                   doc="Username at assignment time, for display and audit.")
    assigned_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    assigned_by: Mapped[str] = mapped_column(String(64), nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"),
                                                  nullable=False)

    __table_args__ = (Index("idx_commercial_review_assignments_user", "assigned_user_id"),)
