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

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.vendor import Vendor
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
