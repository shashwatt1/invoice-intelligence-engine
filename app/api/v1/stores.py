"""
Store Directory — app/api/v1/stores.py

The stores the system knows, as an operator needs to see them: a name
where one is confirmed, the source identifiers that name the store in
each system, and how much data each store holds. Also the one place a
person confirms a store's human identity.

Confirmation is explicit and recorded. Nothing here links an observed
name to a source store code on its own.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import RecordNotFoundError, ValidationError
from app.database.session import get_db
from app.models.store import IDENTITY_CONFIRMED, SOURCE_DOCUMENT, Store
from app.repositories.store_repository import StoreRepository
from app.schemas.base import APIResponse
from app.schemas.processing import (
    StoreDirectoryEntry,
    StoreIdentifierOut,
    StoreIdentityUpdate,
)

router = APIRouter(tags=["Stores"])


def _entry(store: Store, counts: dict[str, int]) -> StoreDirectoryEntry:
    return StoreDirectoryEntry(
        id=store.id, label=store.label, identity_status=store.identity_status,
        display_name=store.display_name, address=store.address_summary,
        source_codes=store.source_codes,
        customer_name=store.customer_name, address_line_1=store.address_line_1,
        address_line_2=store.address_line_2, city=store.city, state=store.state,
        postal_code=store.postal_code, status=store.status, notes=store.notes,
        identifiers=[
            StoreIdentifierOut(
                source_system=i.source_system, identifier_type=i.identifier_type,
                identifier_value=i.identifier_value,
                verified=bool((i.evidence or {}).get("verified", i.source_system != SOURCE_DOCUMENT)),
            )
            for i in store.identifiers
        ],
        **{k: counts.get(k, 0) for k in ("invoices", "pricing_rows", "identities", "catalogue_rows",
                                          "case_mappings", "pending_proposals")},
    )


@router.get(
    "/stores",
    response_model=APIResponse[list[StoreDirectoryEntry]],
    summary="The Store Directory",
    description=(
        "Every store in the master, with its confirmed identity (or the source code it is "
        "known by, marked unresolved), the identifiers each source system uses for it, and "
        "how much data it holds. Not an authorisation boundary."
    ),
)
async def list_stores(db: AsyncSession = Depends(get_db)) -> APIResponse[list[StoreDirectoryEntry]]:
    repo = StoreRepository(db)
    stores = await repo.list()
    counts = await repo.counts()
    return APIResponse(data=[_entry(s, counts.get(s.id, {})) for s in stores])


@router.get("/stores/{store_id}", response_model=APIResponse[StoreDirectoryEntry], summary="One store")
async def get_store(store_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> APIResponse[StoreDirectoryEntry]:
    repo = StoreRepository(db)
    store = await repo.get(store_id)
    if store is None:
        raise RecordNotFoundError(message="Store not found.", detail={"store_id": str(store_id)})
    counts = (await repo.counts()).get(store.id, {})
    return APIResponse(data=_entry(store, counts))


@router.patch(
    "/stores/{store_id}",
    response_model=APIResponse[StoreDirectoryEntry],
    summary="Confirm or correct a store's human identity",
    description=(
        "A person states what this store is called and where it is. With `confirm=true` the "
        "identity is marked confirmed and `confirmed_by` is recorded in the notes. This never "
        "moves data between stores and never attaches a source code."
    ),
)
async def update_store_identity(
    store_id: uuid.UUID, body: StoreIdentityUpdate, db: AsyncSession = Depends(get_db)
) -> APIResponse[StoreDirectoryEntry]:
    repo = StoreRepository(db)
    store = await repo.get(store_id)
    if store is None:
        raise RecordNotFoundError(message="Store not found.", detail={"store_id": str(store_id)})
    for name in ("display_name", "customer_name", "address_line_1", "address_line_2",
                 "city", "state", "postal_code"):
        value = getattr(body, name)
        if value is not None:
            setattr(store, name, value.strip() or None)
    if body.notes is not None:
        store.notes = body.notes.strip() or None
    if body.confirm:
        if not (body.confirmed_by or "").strip():
            raise ValidationError(message="confirmed_by is required to confirm a store's identity.",
                                  detail={"field": "confirmed_by"})
        if not store.display_name:
            raise ValidationError(message="A store needs a display_name before its identity can be confirmed.",
                                  detail={"field": "display_name"})
        store.identity_status = IDENTITY_CONFIRMED
        stamp = f"Identity confirmed by {body.confirmed_by.strip()} on {datetime.now(UTC).date().isoformat()}."
        store.notes = f"{store.notes}\n{stamp}" if store.notes else stamp
        for ident in store.identifiers:
            if ident.source_system == SOURCE_DOCUMENT:
                ident.evidence = {**(ident.evidence or {}), "verified": True,
                                  "verified_by": body.confirmed_by.strip()}
    await db.commit()
    await db.refresh(store)
    counts = (await repo.counts()).get(store.id, {})
    return APIResponse(data=_entry(store, counts))
