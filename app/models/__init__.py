"""
app/models/__init__.py

ORM model package. All SQLAlchemy models are imported here so they
register on Base.metadata. Alembic's env.py also imports from here
to ensure autogenerate detects all tables.
"""

from app.models.document import Document, DocumentStatus
from app.models.invoice import Invoice
from app.models.invoice_item import InvoiceItem
from app.models.processing_log import LogStatus, PipelineStage, ProcessingLog
from app.models.product_case_mapping import ProductCaseMapping
from app.models.product_data_proposal import ProductDataProposal
from app.models.product_reference import ProductIdentifier, ProductIdentity, ProductPricing
from app.models.store_product_reference import StoreProductReference
from app.models.vendor import Vendor

__all__ = [
    "Document",
    "DocumentStatus",
    "Invoice",
    "InvoiceItem",
    "LogStatus",
    "PipelineStage",
    "ProcessingLog",
    "ProductCaseMapping",
    "ProductDataProposal",
    "ProductIdentifier",
    "ProductIdentity",
    "ProductPricing",
    "StoreProductReference",
    "Vendor",
]
