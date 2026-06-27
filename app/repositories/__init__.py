"""
app/repositories/__init__.py

Repository pattern package. Each repository owns all database
read/write operations for a single aggregate root.

Repositories accept an AsyncSession injected via FastAPI's Depends(get_db)
and never call session.commit() — that is the service layer's responsibility.

Sprint 2+ repositories:
    - InvoiceRepository
    - VendorRepository
    - AuditLogRepository
"""
