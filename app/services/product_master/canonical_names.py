"""
Canonical product names chosen by a person — app/services/product_master/canonical_names.py

One place that makes a name the product's CANONICAL description, used by:
  * the PM Approvals "Edit name" action (a MANAGER/ADMIN types the name), and
  * scripts/apply_canonical_names.py (an owner-sanctioned list, docs/product-master-description-policy.md §7).

The name is stored as a CANONICAL description (evidence CONFIRMED, with its provenance) and as
master_products.canonical_description, so every review surface's resolve_display_name shows it.
Earlier canonical names are never deleted: they become HISTORICAL_ALIAS rows, kept for audit. Source
wordings (role SOURCE, INVOICE) are untouched. The PDI export is unaffected: it writes the invoice line's
own wording (export_service._pdi_description).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.models.product_master import (
    DESC_CANONICAL,
    DESC_HISTORICAL_ALIAS,
    STATE_CONFIRMED,
    MasterProduct,
    MasterProductDescription,
)
from app.services.product_master.candidates import normalize_description

NAME_MAX_LENGTH = 100
MANUAL_SOURCE = "manual:product_master"      # names typed by a person in the review screens

Demote = Literal["none", "other_sources", "all_others"]


@dataclass
class NameChange:
    changed: bool
    demoted: int
    previous: str | None


def clean_name(raw: object) -> str:
    """Trim and collapse whitespace; nothing else is altered."""
    return " ".join(str(raw or "").split())


def validate_name(raw: object) -> str:
    name = clean_name(raw)
    if not name:
        raise ValidationError(message="Enter a product name.", detail={"field": "name", "reason": "required"})
    if len(name) > NAME_MAX_LENGTH:
        raise ValidationError(message=f"The name must be at most {NAME_MAX_LENGTH} characters.",
                              detail={"field": "name", "reason": "too_long"})
    if any(ord(ch) < 32 for ch in name):
        raise ValidationError(message="The name must not contain control characters.",
                              detail={"field": "name", "reason": "control_characters"})
    if normalize_description(name) is None:
        raise ValidationError(message="The name must contain letters or digits.",
                              detail={"field": "name", "reason": "no_text"})
    return name


async def _description(session: AsyncSession, product_id, normalized: str, role: str, source: str):
    return (await session.execute(select(MasterProductDescription).where(
        MasterProductDescription.product_id == product_id, MasterProductDescription.role == role,
        MasterProductDescription.normalized_description == normalized,
        MasterProductDescription.source_system == source))).scalars().first()


async def set_canonical_name(session: AsyncSession, product: MasterProduct, raw_name: object, *, source_system: str,
                             source_file: str | None, source_sheet: str | None = None,
                             demote: Demote = "all_others") -> NameChange:
    """
    Make `raw_name` the product's canonical name. Flushes; the caller commits.

    demote: "none" keeps other canonical names as they are; "other_sources" demotes those from other sources;
    "all_others" demotes every other canonical name, including an earlier one from the same source (a person's
    second edit replaces their first).
    """
    name = validate_name(raw_name)
    normalized = normalize_description(name)
    now, previous = datetime.now(UTC), product.canonical_description
    row = await _description(session, product.id, normalized, DESC_CANONICAL, source_system)
    changed = not (row is not None and row.description == name and product.canonical_description == name)
    if changed:
        if row is None:
            row = MasterProductDescription(
                product_id=product.id, description=name, normalized_description=normalized, role=DESC_CANONICAL,
                evidence_state=STATE_CONFIRMED, source_system=source_system, source_file=source_file,
                source_sheet=source_sheet, observed_count=1, created_at=now, updated_at=now)
            session.add(row)
            await session.flush()
        else:
            row.description, row.source_file, row.source_sheet, row.updated_at = name, source_file, source_sheet, now
        product.canonical_description, product.updated_at = name, now

    demoted = 0
    if demote != "none":
        others = (await session.execute(select(MasterProductDescription).where(
            MasterProductDescription.product_id == product.id, MasterProductDescription.role == DESC_CANONICAL,
            MasterProductDescription.id != row.id))).scalars().all()
        for other in others:
            if demote == "other_sources" and other.source_system == source_system:
                continue
            if await _description(session, product.id, other.normalized_description, DESC_HISTORICAL_ALIAS,
                                  other.source_system):
                await session.delete(other)          # an identical alias already keeps this wording
            else:
                other.role, other.updated_at = DESC_HISTORICAL_ALIAS, now
            demoted += 1
    await session.flush()
    return NameChange(changed=changed, demoted=demoted, previous=previous)
