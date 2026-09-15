"""
Store master — app/models/store.py

A store is a physical location with a stable internal identity. The
numbers the reference exports carry ("Store: 47708760") are what ONE
source system calls that location; invoices carry other numbers again
(customer, account, PO). None of those is the store. They are
identifiers OF the store, kept in store_identifiers and resolved to
Store.id, which is the only thing the operational tables reference.

A Store's human identity (name, address) is only ever set from
confirmed evidence. Until a person confirms it, identity_status is
"unresolved" and the store is shown by its source identifier, marked as
such — never by a guessed name.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

STATUS_ACTIVE = "active"
STATUS_INACTIVE = "inactive"

IDENTITY_CONFIRMED = "confirmed"      # a person confirmed name/address for this location
IDENTITY_UNRESOLVED = "unresolved"    # known only by a source identifier, or by unconfirmed evidence

# Source systems and identifier types with actual evidence today. Future
# ones (vendor account, customer number, ship-to, outlet) are added when
# a real document shows them — not before.
SOURCE_ITEM_SALES = "item_sales"      # the POS/back-office "Item Sales Summary" export
SOURCE_DOCUMENT = "document"          # text observed on invoice documents, as reported
TYPE_STORE_CODE = "store_code"
TYPE_CUSTOMER_NAME = "customer_name"
TYPE_ADDRESS_LINE = "address_line"
TYPE_POSTAL_CODE = "postal_code"


class Store(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "stores"

    display_name: Mapped[str | None] = mapped_column(
        String(128), nullable=True,
        doc="Human-facing name. NULL until confirmed — the UI then shows the source identifier.",
    )
    customer_name: Mapped[str | None] = mapped_column(
        String(128), nullable=True, doc="Legal / customer name as suppliers bill it, if known."
    )
    address_line_1: Mapped[str | None] = mapped_column(String(128), nullable=True)
    address_line_2: Mapped[str | None] = mapped_column(String(128), nullable=True)
    city: Mapped[str | None] = mapped_column(String(64), nullable=True)
    state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=STATUS_ACTIVE)
    identity_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=IDENTITY_UNRESOLVED,
        doc="confirmed once a person has vouched for name/address; unresolved otherwise.",
    )
    notes: Mapped[str | None] = mapped_column(
        Text, nullable=True, doc="Where the identity evidence came from, and what is still open."
    )

    identifiers: Mapped[list[StoreIdentifier]] = relationship(
        back_populates="store", cascade="all, delete-orphan", lazy="selectin",
        order_by="StoreIdentifier.source_system, StoreIdentifier.identifier_type, StoreIdentifier.identifier_value",
    )

    # ---- presentation helpers (no I/O) -----------------------------------
    def identifier_values(self, source_system: str | None = None,
                          identifier_type: str | None = None) -> list[str]:
        return [
            i.identifier_value for i in self.identifiers
            if (source_system is None or i.source_system == source_system)
            and (identifier_type is None or i.identifier_type == identifier_type)
        ]

    @property
    def source_codes(self) -> list[str]:
        """The Item Sales store codes attached to this store (usually one)."""
        return self.identifier_values(SOURCE_ITEM_SALES, TYPE_STORE_CODE)

    @property
    def label(self) -> str:
        """
        What an operator sees. A confirmed name; otherwise the source
        code, and the word that says the location is not yet confirmed.
        """
        if self.identity_status == IDENTITY_CONFIRMED and self.display_name:
            return self.display_name
        if self.display_name:
            return f"{self.display_name} (identity unconfirmed)"
        codes = self.source_codes
        if codes:
            return f"Store {codes[0]} (location not yet confirmed)"
        return f"Store {str(self.id)[:8]} (unidentified)"

    @property
    def address_summary(self) -> str | None:
        parts = [self.address_line_1, self.address_line_2]
        locality = ", ".join(p for p in (self.city, self.state) if p)
        if self.postal_code:
            locality = f"{locality} {self.postal_code}".strip()
        parts.append(locality or None)
        text = ", ".join(p for p in parts if p)
        return text or None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id), "display_name": self.display_name, "label": self.label,
            "identity_status": self.identity_status, "address": self.address_summary,
        }

    def __repr__(self) -> str:
        return f"<Store {self.label!r} id={str(self.id)[:8]}>"


class StoreIdentifier(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One way one source system refers to a store."""

    __tablename__ = "store_identifiers"

    store_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("stores.id", ondelete="CASCADE"), nullable=False
    )
    source_system: Mapped[str] = mapped_column(String(32), nullable=False)
    identifier_type: Mapped[str] = mapped_column(String(32), nullable=False)
    identifier_value: Mapped[str] = mapped_column(String(128), nullable=False)
    # Where this identifier was seen and whether a person vouched for it.
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    store: Mapped[Store] = relationship(back_populates="identifiers")

    __table_args__ = (
        # Within one source system an identifier names at most one store;
        # the same value may legitimately mean something else elsewhere.
        UniqueConstraint("source_system", "identifier_type", "identifier_value",
                         name="uq_store_identifier_source_value"),
        Index("idx_store_identifiers_store", "store_id"),
        Index("idx_store_identifiers_value", "identifier_value"),
    )

    def __repr__(self) -> str:
        return f"<StoreIdentifier {self.source_system}:{self.identifier_type}={self.identifier_value!r}>"
