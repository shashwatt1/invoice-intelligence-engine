"""
Recycle Bin schemas — app/schemas/recycle_bin.py

Request and response bodies for the Recycle Bin routes. Actors are always
the signed-in account; no body accepts a "who".
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.processing import StoreRef

REASON = Field(min_length=1, max_length=1000, description="Why; kept with the Recycle Bin record.")


class MoveToBinRequest(BaseModel):
    reason: str = REASON


class RestoreRequest(BaseModel):
    as_reference: bool = Field(
        default=False, description="ADMIN only: restore as a read-only reference copy because an active "
                                   "document holds the same invoice.")
    note: str | None = Field(default=None, max_length=1000)


class DeletionRequestCreate(BaseModel):
    reason: str = REASON


class DeletionDecision(BaseModel):
    note: str | None = Field(default=None, max_length=1000, description="Required to reject.")


class PurgeRequest(BaseModel):
    reason: str = REASON


class DeletionRequestOut(BaseModel):
    id: uuid.UUID
    entry_id: uuid.UUID
    requested_at: datetime
    requested_by: str
    requested_by_role: str
    reason: str
    status: str
    decided_at: datetime | None = None
    decided_by: str | None = None
    decision_note: str | None = None
    # What it is about, so an administrator can decide from the queue.
    filename: str | None = None
    invoice_number: str | None = None
    vendor_name: str | None = None


class RecycleBinEntryOut(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID | None
    filename: str
    invoice_id: uuid.UUID | None = None
    invoice_number: str | None = None
    vendor_name: str | None = None
    store: StoreRef | None = None
    previous_status: str
    binned_at: datetime
    binned_by: str
    binned_by_role: str
    reason: str
    status: str
    closed_at: datetime | None = None
    closed_by: str | None = None
    close_note: str | None = None
    reuploaded_as_document_id: uuid.UUID | None = None
    conflicting_document_id: uuid.UUID | None = Field(
        default=None, description="An active document holding the same file or invoice: restoring it as it "
                                  "was is refused; an administrator can restore it as a reference copy.")
    pending_request: DeletionRequestOut | None = None


class RestoreResultOut(BaseModel):
    entry: RecycleBinEntryOut
    document_status: str
    as_reference: bool


class PurgeResultOut(BaseModel):
    entry: RecycleBinEntryOut


class DecisionResultOut(BaseModel):
    request: DeletionRequestOut
    entry: RecycleBinEntryOut | None = None


class RecycleBinSummary(BaseModel):
    in_bin: int = Field(description="Items in the bin that this account can see.")
    pending_requests: int = Field(description="Deletion requests waiting: all of them for an ADMIN, own otherwise.")
