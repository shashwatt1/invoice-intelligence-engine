"""
Product Master services — app/services/product_master/

Source-aware identifier resolution and candidate construction for the new
Product Master. Nothing here is wired into invoice processing, the mapping
queue or the EDI writer; it exists to build reviewable candidates from
reference data.
"""

from app.services.product_master.identifiers import (
    DerivedIdentifier,
    SourceProfile,
    canonical_key_for,
    derive_identifier,
    upc_a_check_digit,
)

__all__ = [
    "DerivedIdentifier",
    "SourceProfile",
    "canonical_key_for",
    "derive_identifier",
    "upc_a_check_digit",
]
