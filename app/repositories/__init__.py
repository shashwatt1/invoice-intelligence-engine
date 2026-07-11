"""
app/repositories/__init__.py

Repository pattern package. Each repository owns all database
read/write operations for a single aggregate root.

Repositories accept an AsyncSession injected via FastAPI's Depends(get_db)
and never call session.commit() — that is the service layer's responsibility.
"""

from app.repositories.document_repository import DocumentRepository
from app.repositories.invoice_repository import InvoiceRepository
from app.repositories.processing_log_repository import ProcessingLogRepository
from app.repositories.vendor_repository import VendorRepository

__all__ = [
    "DocumentRepository",
    "InvoiceRepository",
    "ProcessingLogRepository",
    "VendorRepository",
]
