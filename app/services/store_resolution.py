"""
Store resolution for importers — app/services/store_resolution.py

An Item Sales workbook names its store in the preamble ("Store: 47708760").
That is the POS's code for the location, not the location. An importer
must turn it into a Store.id through the store master, and must refuse
when what the operator asked for and what the file says disagree.

A Beer Inventory workbook has no store preamble at all, so the operator
must name the store; nothing can be read off the file.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.store import SOURCE_ITEM_SALES, TYPE_STORE_CODE, Store
from app.repositories.store_repository import StoreRepository


class StoreResolutionError(ValueError):
    """The importer cannot say which store this data belongs to."""


@dataclass(frozen=True)
class ResolvedStore:
    store: Store
    source_code: str | None       # the code the file carried, if any
    created: bool                 # True if a new unresolved store was created for the code


async def resolve_item_sales_store(
    session: AsyncSession,
    *,
    source_code: str | None,
    requested: str | None,
    filename: str,
    create_missing: bool = False,
) -> ResolvedStore:
    """
    The Store for an Item Sales workbook.

    `source_code` is what the preamble says; `requested` is what the
    operator passed (--store: a Store id or a store code). Rules:
      * code in the file that resolves to a store → that store, unless
        `requested` names a different one (refused);
      * code in the file that resolves to nothing → refused, unless
        create_missing, which creates an UNRESOLVED store carrying the
        code as its only identifier;
      * no code in the file → `requested` must name a store.
    """
    repo = StoreRepository(session)
    wanted = await repo.resolve(requested) if requested else None
    if requested and wanted is None:
        raise StoreResolutionError(
            f"--store {requested!r} is not a known store id or Item Sales store code."
        )

    if source_code:
        by_code = await repo.by_item_sales_code(source_code)
        if by_code is not None:
            if wanted is not None and wanted.id != by_code.id:
                raise StoreResolutionError(
                    f"{filename}: the file says Item Sales store code {source_code}, which is store "
                    f"{by_code.label!r}, but --store names {wanted.label!r}. Refusing to file one "
                    "store's data under another."
                )
            return ResolvedStore(store=by_code, source_code=source_code, created=False)
        # the code is new to the master
        if wanted is not None:
            raise StoreResolutionError(
                f"{filename}: the file says Item Sales store code {source_code}, which no store "
                f"carries, but --store names {wanted.label!r}. A code is attached to a store by a "
                "person, not by an import; add the identifier in the Store Directory first."
            )
        if not create_missing:
            raise StoreResolutionError(
                f"{filename}: Item Sales store code {source_code} is not attached to any store. "
                "Pass --create-store to create an unresolved store for it (name and address to be "
                "confirmed later), or attach the code to an existing store first."
            )
        store = await repo.create(notes=(
            f"Created by import of {filename}; known only as Item Sales store code {source_code}. "
            "Name and address not yet confirmed by a person."
        ))
        await repo.add_identifier(store, SOURCE_ITEM_SALES, TYPE_STORE_CODE, source_code,
                                  evidence={"origin": f"preamble of {filename}", "verified": True})
        return ResolvedStore(store=store, source_code=source_code, created=True)

    if wanted is None:
        raise StoreResolutionError(
            f"{filename}: no store code in the file and no --store given; nothing can say whose "
            "data this is."
        )
    return ResolvedStore(store=wanted, source_code=None, created=False)


async def resolve_explicit_store(session: AsyncSession, requested: str | None, filename: str) -> Store:
    """For workbooks with no store preamble: the operator's choice, or nothing."""
    if not requested:
        raise StoreResolutionError(
            f"{filename}: this workbook carries no store identification; --store (a Store id or "
            "Item Sales store code) is required."
        )
    store = await StoreRepository(session).resolve(requested)
    if store is None:
        raise StoreResolutionError(f"--store {requested!r} is not a known store id or Item Sales store code.")
    return store
