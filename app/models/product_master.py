"""
Product Master — app/models/product_master.py

The authoritative product model. Additive and unused by the invoice,
mapping-queue and EDI paths: nothing in this file is read or written by
an existing code path, and the legacy `product_identity`,
`product_identifier` and `product_case_mappings` tables continue to own
production behaviour until a later phase migrates them deliberately.

Five tables, because the research established that five different
questions were being answered by one column:

    master_products               what a product IS
    master_product_identifiers    how a source NAMES it
    master_product_descriptions   what a source CALLS it
    master_pack_compositions      what is physically INSIDE it
    master_commercial_mappings    how a store ACCOUNTS for it

The split that forced this apart is units-per-case. PDI computes
Case Retail = Item Retail x units_per_case, so the EDI field is a
commercial multiplier between two price points, not a count of what is in
the box. For MICHELOB ULTRA C-18 12OZ the package physically contains 18
cans (a pack composition, store-independent) while the PDI item for the
case accounts for 1 selling unit (a commercial mapping, store-specific).
Both are true; one column could only hold one of them.

Provenance is carried on each row rather than in a separate table. Every
row here is already one assertion from one source — an identifier as a
workbook wrote it, a description as a source spelled it, a composition as
a distributor stated it — so a provenance table would be 1:1 with these
rows and add a join without adding information. Canonical state never
overwrites an assertion: a disagreement produces another row, and the
status columns say it is unresolved.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

# ---------------------------------------------------------------------------
# Shared vocabularies
# ---------------------------------------------------------------------------

# How certain we are of a fact. Used by every table here so one word means
# one thing across the master. CONFLICT and UNRESOLVED are terminal states
# for a machine — only a person moves a row out of them.
STATE_CONFIRMED = "CONFIRMED"              # a person confirmed it, or the source is authoritative
STATE_AUTO_MATCHED = "AUTO_MATCHED"        # derived deterministically, corroborated, not yet reviewed
STATE_REVIEW_REQUIRED = "REVIEW_REQUIRED"  # derived, but something about it needs a human
STATE_CONFLICT = "CONFLICT"                # two sources disagree; neither was chosen
STATE_UNRESOLVED = "UNRESOLVED"            # not enough evidence to assert anything
STATE_PENDING = "PENDING"                  # a proposal awaits a MANAGER/ADMIN decision
STATE_APPROVED = "APPROVED"                # a reviewer accepted it; master-data only, not EDI
STATE_REJECTED = "REJECTED"                # a reviewer refused it; kept for audit, never deleted
VALID_STATES = frozenset({
    STATE_CONFIRMED, STATE_AUTO_MATCHED, STATE_REVIEW_REQUIRED,
    STATE_CONFLICT, STATE_UNRESOLVED, STATE_PENDING, STATE_APPROVED, STATE_REJECTED,
})

# What a reviewer did. Resolving a conflict is not its own decision: it is
# an approval that had to supply the interpretation the evidence could not.
DECISION_PROPOSE = "PROPOSE"
DECISION_APPROVE = "APPROVE"
DECISION_REJECT = "REJECT"
VALID_DECISIONS = frozenset({DECISION_PROPOSE, DECISION_APPROVE, DECISION_REJECT})

# What a canonical identity rests on. A product with no barcode is still a
# product; it is not silently invented from a description.
BASIS_UPC_A = "UPC_A"                      # 12-digit barcode, check digit validates
BASIS_EAN_13 = "EAN_13"
BASIS_SUPPLIER_ID = "SUPPLIER_ID"          # only a supplier's own product id is known
BASIS_UNRESOLVED = "UNRESOLVED"            # no trustworthy identifier at all
VALID_IDENTITY_BASES = frozenset({
    BASIS_UPC_A, BASIS_EAN_13, BASIS_SUPPLIER_ID, BASIS_UNRESOLVED,
})

# What kind of identifier a source value is. Length alone never decides
# this: an 11-digit value is a PDI item code in one source and a UPC that
# lost its leading zero in another.
ID_UPC_A = "UPC_A"
ID_EAN_13 = "EAN_13"
ID_PDI_ITEM_CODE = "PDI_ITEM_CODE"              # UPC-A with the check digit removed
ID_SUPPLIER_ITEM_ID = "SUPPLIER_ITEM_ID"        # a manufacturer/supplier product id
ID_DISTRIBUTOR_ITEM_CODE = "DISTRIBUTOR_ITEM_CODE"
ID_SHORT_CODE = "SHORT_CODE"                    # PLU-like; not a barcode
ID_UNRESOLVED = "UNRESOLVED"
VALID_IDENTIFIER_TYPES = frozenset({
    ID_UPC_A, ID_EAN_13, ID_PDI_ITEM_CODE, ID_SUPPLIER_ITEM_ID,
    ID_DISTRIBUTOR_ITEM_CODE, ID_SHORT_CODE, ID_UNRESOLVED,
})

# The transformation that produced normalized_value from raw_value. Named
# and stored rather than applied invisibly, because the same 11 digits
# need opposite repairs depending on which source wrote them, and a future
# manufacturer-specific derivation belongs here as another named rule
# rather than as another special case inside a generic normalizer.
DERIVE_NONE = "NONE"
DERIVE_STRIP_SEPARATORS = "STRIP_SEPARATORS"                # "8-51133-00676-2" / "01820096539 5"
DERIVE_STRIP_EXCEL_DECIMAL = "STRIP_EXCEL_DECIMAL"          # "130.0" -> "130"
DERIVE_EXPAND_SCIENTIFIC = "EXPAND_SCIENTIFIC_NOTATION"     # "8.769200057E10" -> "87692000570"
DERIVE_RESTORE_LEADING_ZERO = "RESTORE_LEADING_ZERO"        # Excel float dropped it
DERIVE_APPEND_CHECK_DIGIT = "APPEND_UPC_CHECK_DIGIT"        # PDI item code -> UPC-A
VALID_DERIVATIONS = frozenset({
    DERIVE_NONE, DERIVE_STRIP_SEPARATORS, DERIVE_STRIP_EXCEL_DECIMAL,
    DERIVE_EXPAND_SCIENTIFIC, DERIVE_RESTORE_LEADING_ZERO, DERIVE_APPEND_CHECK_DIGIT,
})

# What a description is FOR. The canonical description is the one EDI
# would eventually emit; an invoice description is evidence and search
# material and must never overwrite it.
DESC_CANONICAL = "CANONICAL"
DESC_SOURCE = "SOURCE"                  # as a reference workbook spelled it
DESC_INVOICE = "INVOICE"                # as an invoice was read
DESC_HISTORICAL_ALIAS = "HISTORICAL_ALIAS"
VALID_DESCRIPTION_ROLES = frozenset({
    DESC_CANONICAL, DESC_SOURCE, DESC_INVOICE, DESC_HISTORICAL_ALIAS,
})

# Where a pack composition came from. Deliberately excludes "parsed from
# an arbitrary description" — that inference is not made in this phase.
PACK_PACKAGE_NOTATION = "PACKAGE_NOTATION"      # an explicit package column, e.g. "18/12 CAN"
PACK_REFERENCE_WORKBOOK = "REFERENCE_WORKBOOK"  # an items/case column or unit-cost divisor
PACK_MANUFACTURER_DATA = "MANUFACTURER_DATA"
PACK_OPERATOR_ENTERED = "OPERATOR_ENTERED"
VALID_PACK_BASES = frozenset({
    PACK_PACKAGE_NOTATION, PACK_REFERENCE_WORKBOOK,
    PACK_MANUFACTURER_DATA, PACK_OPERATOR_ENTERED,
})

# Which product the PDI item's Item Retail refers to. This is the fact
# that decides whether units_accounted_for is 1 or 18, and it was implicit
# and unrecorded in the legacy mapping.
COMMERCIAL_CASE_IS_SELLING_UNIT = "CASE_IS_SELLING_UNIT"   # Item Retail prices the whole case
COMMERCIAL_UNIT_IS_SELLING_UNIT = "UNIT_IS_SELLING_UNIT"   # Item Retail prices one contained unit
COMMERCIAL_UNKNOWN = "UNKNOWN"                             # no admissible evidence either way
COMMERCIAL_CONFLICT = "CONFLICT"                           # sources support incompatible readings
VALID_COMMERCIAL_BASES = frozenset({
    COMMERCIAL_CASE_IS_SELLING_UNIT, COMMERCIAL_UNIT_IS_SELLING_UNIT,
    COMMERCIAL_UNKNOWN, COMMERCIAL_CONFLICT,
})

# What a recorded cost actually measures. A number copied without its basis
# is not reusable, so an unestablished meaning is recorded as unresolved
# rather than assumed to be a case price.
COST_DISTRIBUTOR_CASE_PRICE = "DISTRIBUTOR_CASE_PRICE"     # the sheet's own case/promo price column
COST_UNRESOLVED = "UNRESOLVED"                             # present but meaning not established
COST_CONFLICTING_SOURCES = "CONFLICTING_SOURCES"           # sources disagree; no value recorded
VALID_COST_BASES = frozenset({
    COST_DISTRIBUTOR_CASE_PRICE, COST_UNRESOLVED, COST_CONFLICTING_SOURCES,
})

# The EDI field is 4 digits and a case accounts for at least one unit.
MIN_UNITS_ACCOUNTED_FOR = 1
MAX_UNITS_ACCOUNTED_FOR = 9999


class MasterProduct(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    One product, independent of any invoice, store, cost or description.

    `canonical_key` is the identity, not `canonical_upc`, because a
    product whose barcode is unknown is still a product: a supplier-id
    identity keys as "supplier:Monarch:50335" and an unresolved one keys
    on its own uuid so it can never silently collide with another.
    """

    __tablename__ = "master_products"

    canonical_key: Mapped[str] = mapped_column(
        String(128), nullable=False,
        doc="Namespaced identity, e.g. 'upc12:018200967214'. Stable and unique.",
    )
    canonical_upc: Mapped[str | None] = mapped_column(
        String(14), nullable=True,
        doc="The barcode when identity rests on one; NULL when it does not.",
    )
    identity_basis: Mapped[str] = mapped_column(
        String(32), nullable=False,
        doc="What the identity rests on — see VALID_IDENTITY_BASES.",
    )
    identity_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=STATE_UNRESOLVED,
        doc="Confidence in the identity itself — see VALID_STATES.",
    )
    canonical_description: Mapped[str | None] = mapped_column(
        String(255), nullable=True,
        doc="The description EDI would emit. NULL until a person confirms one.",
    )
    brand: Mapped[str | None] = mapped_column(String(128), nullable=True)
    supplier: Mapped[str | None] = mapped_column(String(128), nullable=True)
    product_class: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        UniqueConstraint("canonical_key", name="uq_master_products_canonical_key"),
        Index("idx_master_products_canonical_upc", "canonical_upc"),
        Index("idx_master_products_identity_state", "identity_state"),
    )


class MasterProductIdentifier(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    One source's way of naming a product, with the derivation that turned
    it into a canonical value kept beside it.

    This is the formalisation of the resolution architecture: a raw value
    resolves independently to a canonical value, and identity is whatever
    two raw values share afterwards. There is no pairwise bridge rule, so
    a new source format adds one derivation rather than comparisons
    against every format already supported.
    """

    __tablename__ = "master_product_identifiers"

    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("master_products.id", ondelete="CASCADE"), nullable=False,
    )
    raw_value: Mapped[str] = mapped_column(
        String(128), nullable=False,
        doc="Exactly what the source held, including '8.769200057E10'. Never overwritten.",
    )
    normalized_value: Mapped[str | None] = mapped_column(
        String(64), nullable=True,
        doc="The canonical form the derivation produced. NULL when none could be.",
    )
    identifier_type: Mapped[str] = mapped_column(
        String(32), nullable=False,
        doc="What kind of identifier this is — see VALID_IDENTIFIER_TYPES.",
    )
    derivation: Mapped[str] = mapped_column(
        String(48), nullable=False, default=DERIVE_NONE,
        doc="The named transformation applied — see VALID_DERIVATIONS.",
    )
    derivation_detail: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict,
        doc="Why that derivation was chosen: the source population, flags, intermediate value.",
    )
    evidence_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=STATE_AUTO_MATCHED,
    )
    source_system: Mapped[str] = mapped_column(
        String(64), nullable=False,
        doc="Which system wrote the raw value, e.g. 'item_sales_summary', 'monarch'.",
    )
    source_distributor: Mapped[str | None] = mapped_column(
        String(64), nullable=True,
        doc="Whose code this is when the type is distributor/supplier scoped.",
    )
    source_file: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_sheet: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "product_id", "identifier_type", "raw_value", "source_system",
            name="uq_master_identifier_source_value",
        ),
        # The resolution path: given a canonical value, find the product.
        Index("idx_master_identifier_lookup", "identifier_type", "normalized_value"),
        Index("idx_master_identifier_normalized", "normalized_value"),
        Index("idx_master_identifier_product", "product_id"),
    )


class MasterProductDescription(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    Every description ever observed for a product, with what it is for.

    Descriptions are never identity. Keeping invoice descriptions here
    rather than on the product is what lets EDI eventually emit the
    canonical description while the invoice wording stays searchable and
    auditable.
    """

    __tablename__ = "master_product_descriptions"

    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("master_products.id", ondelete="CASCADE"), nullable=False,
    )
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_description: Mapped[str] = mapped_column(
        String(255), nullable=False, doc="Upper-cased, punctuation-collapsed; for comparison only.",
    )
    role: Mapped[str] = mapped_column(
        String(32), nullable=False, doc="What this description is for — see VALID_DESCRIPTION_ROLES.",
    )
    evidence_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=STATE_AUTO_MATCHED,
    )
    source_system: Mapped[str] = mapped_column(String(64), nullable=False)
    source_file: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_sheet: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    observed_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1,
        doc="How many source rows carried it. Frequency is evidence, never a tiebreak.",
    )

    __table_args__ = (
        UniqueConstraint(
            "product_id", "normalized_description", "role", "source_system",
            name="uq_master_description_role_source",
        ),
        Index("idx_master_description_product", "product_id"),
        Index("idx_master_description_normalized", "normalized_description"),
    )


class MasterPackComposition(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    What is physically inside a package: parent contains N x child.

    018200967214 (the 18-pack) contains 18 x 018200003349 (the can).

    This is store-independent and is NOT the PDI multiplier. `child_product_id`
    is nullable because a count can be evidenced before the contained
    product has been identified — recording "18 units" without inventing a
    product for the can is better than losing the 18.
    """

    __tablename__ = "master_pack_compositions"

    parent_product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("master_products.id", ondelete="CASCADE"), nullable=False,
    )
    child_product_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("master_products.id", ondelete="RESTRICT"), nullable=True,
        doc="The contained product when it is known; NULL when only the count is evidenced.",
    )
    child_quantity: Mapped[int] = mapped_column(
        Integer, nullable=False, doc="How many of the child the parent physically contains.",
    )
    composition_basis: Mapped[str] = mapped_column(
        String(32), nullable=False, doc="Where the count came from — see VALID_PACK_BASES.",
    )
    evidence_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=STATE_REVIEW_REQUIRED,
    )
    evidence: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict,
        doc="The literal statement relied on, e.g. the package notation or workbook cell.",
    )
    source_system: Mapped[str] = mapped_column(String(64), nullable=False)
    source_file: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_sheet: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_row: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "parent_product_id", "child_product_id", "child_quantity", "source_system",
            name="uq_master_pack_composition",
        ),
        Index("idx_master_pack_parent", "parent_product_id"),
        Index("idx_master_pack_child", "child_product_id"),
    )


class MasterCommercialMapping(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    How one store commercially accounts for one product in PDI.

    `units_accounted_for` is what the legacy `product_case_mappings.units_per_case`
    becomes: the multiplier PDI applies as Case Retail = Item Retail x this.
    `commercial_unit_basis` is the fact that was missing — it says which
    product the Item Retail refers to, and therefore why the multiplier is
    1 rather than 18 for a case that physically holds 18 cans.

    Store-scoped, so two stores may account differently without either
    touching the canonical product.
    """

    __tablename__ = "master_commercial_mappings"

    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("master_products.id", ondelete="CASCADE"), nullable=False,
    )
    store_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("stores.id", ondelete="RESTRICT"), nullable=False,
        doc="Commercial configuration is never inherited by another store.",
    )
    pdi_item_code: Mapped[str] = mapped_column(
        String(32), nullable=False, doc="The code PDI product-matches on, as emitted to EDI.",
    )
    commercial_unit_basis: Mapped[str] = mapped_column(
        String(32), nullable=False, default=COMMERCIAL_UNKNOWN,
        doc="Which product Item Retail prices — see VALID_COMMERCIAL_BASES.",
    )
    units_accounted_for: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
        doc=(
            "The PDI multiplier (1-9999). NULL when unknown — never defaulted, "
            "because a wrong value silently corrupts Case Retail in PDI."
        ),
    )
    case_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    cost_basis: Mapped[str | None] = mapped_column(
        String(32), nullable=True,
        doc=(
            "What case_cost measures — see VALID_COST_BASES. A column rather than "
            "an evidence key because cost status is reviewed independently of the "
            "commercial unit: a mapping may be approved while its cost stays unresolved."
        ),
    )
    approval_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=STATE_REVIEW_REQUIRED,
    )
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # A USER may propose a multiplier but never write one. The proposal sits
    # here until a MANAGER/ADMIN approves or rejects it through the same
    # transactional review service; it is not authoritative on its own.
    proposed_units_accounted_for: Mapped[int | None] = mapped_column(Integer, nullable=True)
    proposed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    proposed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    proposed_note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # The raw source values a reviewer needs, captured at seed time so the
    # deployed dashboard never has to read the reference workbooks.
    source_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict,
        doc="Raw source fields as written, with unknowns left absent rather than invented.",
    )
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    source_system: Mapped[str] = mapped_column(String(64), nullable=False)
    source_file: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_sheet: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_row: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "store_id", "product_id", "effective_from",
            name="uq_master_commercial_store_product_effective",
        ),
        Index("idx_master_commercial_store_item", "store_id", "pdi_item_code"),
        Index("idx_master_commercial_product", "product_id"),
    )


class MasterCommercialReview(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    One reviewer decision about one commercial mapping, append-only.

    A dedicated table rather than a reuse of `product_data_proposals`:
    that table governs the legacy case-mapping workflow, is keyed by
    entity_key/field for it, and is authoritative for EDI — overloading it
    would either blur those semantics or require writing to a legacy table
    this phase must not modify. It is also not a generic audit framework;
    it records exactly the transitions this one workflow makes.

    Nothing here is ever updated or deleted. The mapping carries its
    current state; this carries how it got there, including the values it
    held before, so an approval can never quietly erase what the evidence
    originally said.
    """

    __tablename__ = "master_commercial_reviews"

    mapping_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("master_commercial_mappings.id", ondelete="CASCADE"), nullable=False,
    )
    decision: Mapped[str] = mapped_column(
        String(16), nullable=False, doc="APPROVE or REJECT — see VALID_DECISIONS.",
    )
    previous_approval_state: Mapped[str] = mapped_column(String(32), nullable=False)
    new_approval_state: Mapped[str] = mapped_column(String(32), nullable=False)
    previous_commercial_unit_basis: Mapped[str] = mapped_column(String(32), nullable=False)
    new_commercial_unit_basis: Mapped[str] = mapped_column(String(32), nullable=False)
    previous_units_accounted_for: Mapped[int | None] = mapped_column(Integer, nullable=True)
    new_units_accounted_for: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reviewer: Mapped[str] = mapped_column(
        String(64), nullable=False, doc="The username that made the decision.",
    )
    note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    evidence_considered: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict,
        doc="The candidate's evidence as it stood when the decision was made.",
    )

    __table_args__ = (
        Index("idx_master_commercial_review_mapping", "mapping_id"),
        Index("idx_master_commercial_review_created", "created_at"),
    )
