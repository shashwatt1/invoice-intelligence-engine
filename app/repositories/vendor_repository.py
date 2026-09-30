"""
Vendor Repository — app/repositories/vendor_repository.py

Vendor upsert from extracted invoice data. Vendors are never pre-created
in the MVP — they materialize the first time an invoice mentions them.

Matching strategy (per the model design — tax_id is the business key):
    1. tax_id present  → match on tax_id alone. Two invoices with the
       same tax registration are the same vendor even if the printed
       name varies ("Acme Corp" vs "Acme Corp Ltd").
    2. tax_id absent   → match on exact name where tax_id IS NULL.
       Postgres treats NULLs as distinct in the (name, tax_id) unique
       constraint, so this code-level match is what prevents duplicates.

Concurrency: two concurrent pipelines may race to create the same
vendor; the loser's INSERT violates the unique constraint and is
resolved by re-selecting inside the caller's transaction.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import Invoice
from app.models.vendor import VENDOR_CONFIRMED, Vendor, VendorIdentityReview
from app.schemas.normalized import NormalizedInvoice


class VendorRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_or_create(self, invoice: NormalizedInvoice) -> tuple[Vendor | None, bool]:
        """
        Upsert the vendor referenced by a normalized invoice.

        Returns:
            (vendor, created). vendor is None when the invoice carries no
            vendor name — there is nothing to upsert against.
        """
        if invoice.vendor_name is None:
            return None, False

        existing = await self._find(invoice.vendor_name, invoice.vendor_tax_id)
        if existing is not None:
            self._fill_missing_contact_fields(existing, invoice)
            await self._session.flush()
            return existing, False

        vendor = Vendor(
            name=invoice.vendor_name,
            tax_id=invoice.vendor_tax_id,
            address=invoice.vendor_address,
            phone=invoice.vendor_phone,
            email=invoice.vendor_email,
        )
        try:
            # SAVEPOINT so a lost creation race only undoes this insert,
            # never the caller's enclosing transaction.
            async with self._session.begin_nested():
                self._session.add(vendor)
                await self._session.flush()
        except IntegrityError:
            existing = await self._find(invoice.vendor_name, invoice.vendor_tax_id)
            if existing is None:  # pragma: no cover — constraint guarantees a row
                raise
            return existing, False
        return vendor, True

    async def _find(self, name: str, tax_id: str | None) -> Vendor | None:
        if tax_id is not None:
            query = select(Vendor).where(Vendor.tax_id == tax_id).limit(1)
        else:
            query = (
                select(Vendor)
                .where(Vendor.name == name, Vendor.tax_id.is_(None))
                .limit(1)
            )
        result = await self._session.execute(query)
        return result.scalar_one_or_none()

    @staticmethod
    def _fill_missing_contact_fields(vendor: Vendor, invoice: NormalizedInvoice) -> None:
        """Enrich blank contact fields from newer extractions; never overwrite."""
        if vendor.address is None and invoice.vendor_address:
            vendor.address = invoice.vendor_address
        if vendor.phone is None and invoice.vendor_phone:
            vendor.phone = invoice.vendor_phone
        if vendor.email is None and invoice.vendor_email:
            vendor.email = invoice.vendor_email

    # ---- Vendor Master reads (governance; never called by the pipeline) ----
    def _filtered(self, *, identity_status: str | None, search: str | None):
        query = select(Vendor)
        if identity_status:
            query = query.where(Vendor.identity_status == identity_status)
        if search:
            pattern = f"%{search.strip()}%"
            observed = exists().where(Invoice.vendor_id == Vendor.id, Invoice.vendor_name.ilike(pattern))
            query = query.where(or_(Vendor.name.ilike(pattern), Vendor.display_name.ilike(pattern),
                                    Vendor.tax_id.ilike(pattern), observed))
        return query

    async def list_page(self, *, page: int, page_size: int, identity_status: str | None = None,
                   search: str | None = None) -> Sequence[Vendor]:
        query = (self._filtered(identity_status=identity_status, search=search)
                 .order_by(func.lower(func.coalesce(Vendor.display_name, Vendor.name)), Vendor.id)
                 .offset((page - 1) * page_size).limit(page_size))
        return (await self._session.execute(query)).scalars().all()

    async def count(self, *, identity_status: str | None = None, search: str | None = None) -> int:
        query = select(func.count()).select_from(
            self._filtered(identity_status=identity_status, search=search).subquery())
        return int((await self._session.execute(query)).scalar_one())

    async def get(self, vendor_id: uuid.UUID) -> Vendor | None:
        return await self._session.get(Vendor, vendor_id)

    async def invoice_stats(self, vendor_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, dict[str, Any]]:
        """Per vendor: invoices, distinct observed names, last seen — one grouped query for the page."""
        if not vendor_ids:
            return {}
        query = (select(Invoice.vendor_id, func.count(), func.count(func.distinct(Invoice.vendor_name)),
                        func.max(Invoice.created_at))
                 .where(Invoice.vendor_id.in_(vendor_ids)).group_by(Invoice.vendor_id))
        return {vid: {"invoices": n, "observed_names": names, "last_seen_at": last}
                for vid, n, names, last in (await self._session.execute(query)).all()}

    async def observed_names(self, vendor_id: uuid.UUID) -> list[dict[str, Any]]:
        """Each distinct vendor wording printed on this vendor's invoices, as extracted."""
        query = (select(Invoice.vendor_name, func.count(), func.min(Invoice.created_at), func.max(Invoice.created_at))
                 .where(Invoice.vendor_id == vendor_id, Invoice.vendor_name.is_not(None))
                 .group_by(Invoice.vendor_name).order_by(func.count().desc(), Invoice.vendor_name))
        return [{"name": name, "invoices": n, "first_seen_at": first, "last_seen_at": last}
                for name, n, first, last in (await self._session.execute(query)).all()]

    async def observed_tax_ids(self, vendor_id: uuid.UUID) -> list[dict[str, Any]]:
        query = (select(Invoice.vendor_tax_id, func.count())
                 .where(Invoice.vendor_id == vendor_id, Invoice.vendor_tax_id.is_not(None))
                 .group_by(Invoice.vendor_tax_id).order_by(func.count().desc()))
        return [{"tax_id": tax_id, "invoices": n} for tax_id, n in (await self._session.execute(query)).all()]

    async def recent_invoices(self, vendor_id: uuid.UUID, limit: int = 25) -> Sequence[Invoice]:
        query = (select(Invoice).where(Invoice.vendor_id == vendor_id)
                 .order_by(Invoice.created_at.desc()).limit(limit))
        return (await self._session.execute(query)).scalars().all()

    async def history(self, vendor_id: uuid.UUID) -> Sequence[VendorIdentityReview]:
        query = (select(VendorIdentityReview).where(VendorIdentityReview.vendor_id == vendor_id)
                 .order_by(VendorIdentityReview.created_at, VendorIdentityReview.id))
        return (await self._session.execute(query)).scalars().all()

    async def confirmed_with_name(self, display_name: str, *, excluding: uuid.UUID) -> Vendor | None:
        """Another confirmed vendor already holding this canonical name (case-insensitive)."""
        query = (select(Vendor).where(Vendor.identity_status == VENDOR_CONFIRMED, Vendor.id != excluding,
                                      func.lower(Vendor.display_name) == display_name.lower()).limit(1))
        return (await self._session.execute(query)).scalar_one_or_none()

    async def add_review(self, review: VendorIdentityReview) -> VendorIdentityReview:
        self._session.add(review)
        await self._session.flush()
        return review

