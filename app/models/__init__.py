"""
app/models/__init__.py

ORM model package. All SQLAlchemy models are imported here so they
register on Base.metadata. Alembic's env.py also imports from here
to ensure autogenerate detects all tables.
"""

from app.models.document import Document, DocumentStatus
from app.models.document_page import DocumentPage
from app.models.edi_export import EdiExport
from app.models.export_override import ExportOverride
from app.models.inventory import InventoryMovement, InventoryReceipt, StoreInventoryBaseline
from app.models.invoice import Invoice
from app.models.invoice_item import InvoiceItem
from app.models.processing_log import LogStatus, PipelineStage, ProcessingLog
from app.models.product_case_mapping import ProductCaseMapping
from app.models.product_data_proposal import ProductDataProposal
from app.models.product_master import (
    MasterCommercialMapping,
    MasterCommercialReview,
    MasterPackComposition,
    MasterProduct,
    MasterProductDescription,
    MasterProductIdentifier,
)
from app.models.product_reference import ProductIdentifier, ProductIdentity, ProductPricing
from app.models.recycle_bin import BinDeletionRequest, RecycleBinEntry
from app.models.store import Store, StoreIdentifier
from app.models.store_product_reference import StoreProductReference
from app.models.user import ROLE_RANK, User, UserRole
from app.models.user_store_access import UserStoreAccess
from app.models.vendor import Vendor, VendorMerge
from app.models.vendor_item_crosswalk import VendorItemCrosswalk
from app.models.vendor_request import VendorRequest

__all__ = [
    "Document",
    "EdiExport",
    "ExportOverride",
    "VendorRequest",
    "MasterCommercialMapping",
    "MasterCommercialReview",
    "MasterPackComposition",
    "MasterProduct",
    "MasterProductDescription",
    "MasterProductIdentifier",
    "DocumentPage",
    "DocumentStatus",
    "InventoryMovement",
    "InventoryReceipt",
    "StoreInventoryBaseline",
    "Invoice",
    "InvoiceItem",
    "LogStatus",
    "PipelineStage",
    "ProcessingLog",
    "RecycleBinEntry",
    "BinDeletionRequest",
    "ProductCaseMapping",
    "ProductDataProposal",
    "ProductIdentifier",
    "ProductIdentity",
    "ProductPricing",
    "ROLE_RANK",
    "Store",
    "StoreIdentifier",
    "StoreProductReference",
    "User",
    "UserRole",
    "Vendor",
    "VendorMerge",
    "VendorItemCrosswalk",
    "UserStoreAccess",
]
