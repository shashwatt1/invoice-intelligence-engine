"""
Store-access schemas — app/schemas/store_access.py

Request and response bodies for the ADMIN store-access endpoints
(/users/{user_id}/store-access). A grant row is audit history: these bodies
show it exactly as recorded and never accept actor fields from the client;
the acting administrator is always the authenticated session.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.processing import StoreRef

REASON_FIELD = Field(min_length=1, max_length=500, description="Why; kept with the store-access record.")


class StoreAccessGrantRequest(BaseModel):
    store_id: uuid.UUID
    reason: str = REASON_FIELD


class StoreAccessRevokeRequest(BaseModel):
    reason: str = REASON_FIELD


class StoreAccessOut(BaseModel):
    """One grant as recorded: who was granted which store, by whom and why, and its revocation if any."""

    id: uuid.UUID
    user_id: uuid.UUID
    username: str
    store_id: uuid.UUID
    store: StoreRef | None = None
    active: bool
    self_granted: bool = Field(description="True when the administrator granted the access to their own account.")
    granted_at: datetime
    granted_by: str
    granted_by_role: str
    grant_reason: str
    revoked_at: datetime | None = None
    revoked_by: str | None = None
    revoked_by_role: str | None = None
    revoke_reason: str | None = None

    @classmethod
    def from_grant(cls, grant, store=None) -> StoreAccessOut:
        return cls(
            id=grant.id, user_id=grant.user_id, username=grant.username, store_id=grant.store_id,
            store=StoreRef.from_store(store) if store is not None else None, active=grant.revoked_at is None,
            self_granted=grant.granted_by_user_id == grant.user_id, granted_at=grant.granted_at,
            granted_by=grant.granted_by, granted_by_role=grant.granted_by_role, grant_reason=grant.grant_reason,
            revoked_at=grant.revoked_at, revoked_by=grant.revoked_by, revoked_by_role=grant.revoked_by_role,
            revoke_reason=grant.revoke_reason,
        )


class StoreAccessGrantResult(BaseModel):
    grant: StoreAccessOut
    created: bool = Field(description="False when an active grant already existed; nothing was written.")
