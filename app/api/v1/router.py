"""
API v1 Router — app/api/v1/router.py

Aggregates all v1 endpoint routers into a single router
that is mounted on the FastAPI application.

Adding a new endpoint group:
    1. Create app/api/v1/your_feature.py with an APIRouter.
    2. Import the router here.
    3. Call api_router.include_router(...).
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    auth,
    dashboard,
    documents,
    exports,
    health,
    invoices,
    mapping_queue,
    product_master,
    proposals,
    stores,
    upload,
    users,
    vendors,
)

api_router = APIRouter()

# System endpoints — health, readiness, version
api_router.include_router(health.router, prefix="")

# Auth / accounts — no public registration; see scripts/create_user.py
api_router.include_router(auth.router, prefix="")
api_router.include_router(users.router, prefix="")

# Upload endpoint — Phase 1 (superseded by /invoices/process for the full pipeline)
api_router.include_router(upload.router, prefix="")

# Processing pipeline + read models
api_router.include_router(invoices.router, prefix="")
api_router.include_router(exports.router, prefix="")
api_router.include_router(documents.router, prefix="")
api_router.include_router(dashboard.router, prefix="")
api_router.include_router(proposals.router, prefix="")
api_router.include_router(mapping_queue.router, prefix="")
api_router.include_router(stores.router, prefix="")
api_router.include_router(product_master.router, prefix="")
api_router.include_router(vendors.router, prefix="")
