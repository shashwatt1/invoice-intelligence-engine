"""
Vendor request — app/models/vendor_request.py

A USER who finds the vendor of an invoice missing from the picker at upload does not create a vendor: they ask for one.
The request carries the name they typed and who typed it; the invoice they upload is processed as usual and shows
"vendor pending approval" until a MANAGER or ADMIN decides. Approving creates the vendor (or links the request to the
vendor it turns out to be) and points every waiting invoice at it; rejecting leaves the invoices as they are, each needing
a vendor chosen before it can be marked Retailed. A MANAGER or ADMIN who adds a vendor does it directly and never makes
a request.

Rows are written once and decided once: `status` PENDING until decided, and decided rows are never changed.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

REQUEST_PENDING = "PENDING"
REQUEST_APPROVED = "APPROVED"
REQUEST_REJECTED = "REJECTED"
REQUEST_STATUSES = frozenset({REQUEST_PENDING, REQUEST_APPROVED, REQUEST_REJECTED})


class VendorRequest(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "vendor_requests"

    requested_name: Mapped[str] = mapped_column(String(255), nullable=False, doc="The name as typed.")
    normalized_name: Mapped[str] = mapped_column(
        String(255), nullable=False, doc="Comparison form of the name (see vendor_matching.normalize_vendor_name).")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=REQUEST_PENDING,
                                        server_default=REQUEST_PENDING)
    requested_by: Mapped[str] = mapped_column(String(64), nullable=False, doc="The signed-in account's username.")
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("vendors.id", ondelete="SET NULL"), nullable=True,
        doc="The vendor an approved request became (new, or an existing one it was found to be).")

    __table_args__ = (
        CheckConstraint("status IN ('PENDING', 'APPROVED', 'REJECTED')", name="ck_vendor_requests_status"),
        CheckConstraint("(status = 'PENDING') = (decided_at IS NULL)", name="ck_vendor_requests_decided"),
        CheckConstraint("status <> 'APPROVED' OR vendor_id IS NOT NULL", name="ck_vendor_requests_approved_vendor"),
        # One open request per name: a second person asking for the same vendor joins the first request.
        Index("uq_vendor_requests_pending_name", "normalized_name", unique=True,
              postgresql_where=text("status = 'PENDING'")),
        Index("idx_vendor_requests_status", "status", "created_at"),
    )
