"""
Source-aware product reference — app/models/product_reference.py

Three tables that hold what the store's reference sources say about a
product, WITHOUT collapsing sources into one flat truth:

  product_identity     what the product is        (one row per store+UPC)
  product_identifier   other names for it         (many per product)
  product_pricing      what a source charged       (one row per source row)

Every row remembers exactly which file, sheet and row it came from.

Why three tables and not one
----------------------------
The Beer Inventory workbook is three distributors' price lists plus
three hand-built worksheets. The same UPC appears on several of them
with different prices (different distributors, different dates) and
occasionally different package readings. A flat table would have to pick
a winner at import time, silently, and the evidence for the choice would
be gone. Keeping one pricing row per source row means the choice is made
at read time, by a rule that can say why (see store_reference_service),
and can be revisited when a new source arrives.

What is deliberately NOT here
-----------------------------
No units_per_case column that the formatter reads. product_case_mappings
remains the sole authority for that. product_pricing records what a
source SAID (items_per_case_stated) or what its own arithmetic IMPLIES
(items_per_case_derived) — both are evidence feeding a proposal, and a
reviewer decides whether they become a mapping.

Distributor-specific codes
--------------------------
Testani's item 349, Zink's item 118 and Monarch's product 66782 are each
that distributor's own SKU. Two distributors using the same number are
not talking about the same product, so an identifier row always carries
its distributor and is only ever matched within it.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, Index, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

# --- product_identifier.kind ---
KIND_UNIT_UPC = "unit_upc"                       # single-container barcode; an alias
KIND_RETAIL_UPC_RAW = "retail_upc_raw"           # the sellable-pack barcode as printed
KIND_DISTRIBUTOR_ITEM_CODE = "distributor_item_code"
KIND_DISTRIBUTOR_PRODUCT_ID = "distributor_product_id"

# --- product_pricing.pricing_basis ---
BASIS_PERIOD_AVERAGE = "period_average"   # Item Sales Summary Avg Cost
BASIS_PROMO = "promo"                      # a promotional case price
BASIS_FRONTLINE = "frontline"              # the standing (non-promo) case price
BASIS_PRICE_CHANGE = "price_change"        # an old->new price notice
VALID_BASES = frozenset({BASIS_PERIOD_AVERAGE, BASIS_PROMO, BASIS_FRONTLINE, BASIS_PRICE_CHANGE})

# --- product_pricing.items_per_case_derivation ---
DERIVED_FROM_PACKAGE = "package"   # decoded from a package string such as "24/12OZ 2/12 CANS"
DERIVED_FROM_RATIO = "ratio"       # the source's own case_cost / unit_cost


class ProductIdentity(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """What a product is, merged from sources with each field's origin kept."""

    __tablename__ = "product_identity"

    store_number: Mapped[str] = mapped_column(String(32), nullable=False)
    item_code: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    brand: Mapped[str | None] = mapped_column(String(128), nullable=True)
    supplier: Mapped[str | None] = mapped_column(String(128), nullable=True)
    product_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # field -> {source_file, source_sheet, source_row}. Says which source
    # each populated field came from, so a merged row is still auditable.
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint("store_number", "item_code", name="uq_product_identity_store_item"),
        Index("idx_product_identity_item_code", "item_code"),
    )


class ProductIdentifier(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """An alternate way a source names a product. Never the primary key."""

    __tablename__ = "product_identifier"

    store_number: Mapped[str] = mapped_column(String(32), nullable=False)
    item_code: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    value: Mapped[str] = mapped_column(String(64), nullable=False)
    distributor: Mapped[str | None] = mapped_column(
        String(64), nullable=True,
        doc="Whose code this is. Required for distributor kinds; codes never match across.",
    )
    source_file: Mapped[str] = mapped_column(String(255), nullable=False)
    source_sheet: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("store_number", "item_code", "kind", "value", "distributor",
                         name="uq_product_identifier"),
        Index("idx_product_identifier_lookup", "store_number", "kind", "value", "distributor"),
    )


class ProductPricing(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """What one source row said a product costs. One row per source row."""

    __tablename__ = "product_pricing"

    store_number: Mapped[str] = mapped_column(String(32), nullable=False)
    item_code: Mapped[str] = mapped_column(String(32), nullable=False)
    distributor: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pricing_basis: Mapped[str] = mapped_column(String(32), nullable=False)

    case_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)
    previous_case_cost: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 4), nullable=True, doc="The 'old/current' column on a price-change notice."
    )
    unit_retail: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 4), nullable=True,
        doc=(
            "What the scanned unit sold for on average (Item Sales 'Avg Price'). "
            "Direct evidence of the store's sellable unit; never written to an EDI."
        ),
    )
    package: Mapped[str | None] = mapped_column(
        String(128), nullable=True, doc="The package descriptor exactly as the source printed it."
    )
    items_per_case_stated: Mapped[int | None] = mapped_column(
        Integer, nullable=True, doc="Only when the source has an explicit items/case cell."
    )
    items_per_case_derived: Mapped[int | None] = mapped_column(
        Integer, nullable=True, doc="What the package string or the source's own arithmetic implies."
    )
    items_per_case_derivation: Mapped[str | None] = mapped_column(String(16), nullable=True)

    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)

    # When the source disagrees with itself — its formula divisor and its
    # case/unit ratio give different answers — the row is kept for the
    # record but excluded from evidence, and the disagreement is written
    # down rather than resolved by guessing.
    is_conflicted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    conflict_detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    source_file: Mapped[str] = mapped_column(String(255), nullable=False)
    source_sheet: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_identifier: Mapped[str | None] = mapped_column(
        String(64), nullable=True, doc="The UPC cell exactly as the source printed it."
    )
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        # A source row belongs to a store's file: two stores may each
        # have a "Beer Inventory.xlsx", and neither may touch the other's.
        UniqueConstraint("store_number", "source_file", "source_sheet", "source_row",
                         name="uq_product_pricing_source_row"),
        Index("idx_product_pricing_item", "store_number", "item_code"),
        Index("idx_product_pricing_conflicted", "is_conflicted"),
    )
