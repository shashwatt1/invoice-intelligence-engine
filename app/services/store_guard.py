"""
Physical-store guard — app/services/store_guard.py

The one rule every endpoint that assigns a PHYSICAL store applies: the Store
Master record must be a physical store, not a source identity.

A source identity is a store record known only by a source-system code (for
example an Item Sales store code) with no name, address or store-directory
listing — Store.kind == KIND_SOURCE_IDENTITY. Which physical location such a
code belongs to is a data-team decision; until a person makes it, the record
cannot be where an invoice or document was delivered. The rule reads the
record's classification only. No store code is named here.

Used by: invoice processing/upload (store chosen up front), paused-document
store confirmation, and store assignment for a store-pending invoice.
"""

from __future__ import annotations

from app.core.exceptions import ValidationError
from app.models.store import KIND_SOURCE_IDENTITY, Store
from app.models.user import UserRole

REASON_SOURCE_IDENTITY = "source_identity"


def require_store_choice(role: str, value: str | None) -> None:
    """
    USER and MANAGER must name the physical store an invoice is for before it is
    processed; only an ADMIN may process without one and resolve it later.
    Checks presence only — the chosen id is still resolved and refused unless it
    is a physical store (resolve_chosen_store → ensure_physical_store).
    """
    if role != UserRole.ADMIN.value and not (value or "").strip():
        raise ValidationError(
            message="Choose the physical store this invoice is for before processing it.",
            detail={"field": "store_id", "reason": "required_for_role", "role": role},
        )


def ensure_physical_store(store: Store) -> Store:
    """Return the store if it is a physical store; refuse a source identity."""
    if store.kind == KIND_SOURCE_IDENTITY:
        raise ValidationError(
            message=(f"{store.label} is an unresolved source identity — known only by a source-system code — "
                     "not a physical store. Choose a physical store from the directory."),
            detail={"field": "store_id", "reason": REASON_SOURCE_IDENTITY, "value": str(store.id),
                    "store_kind": store.kind},
        )
    return store
