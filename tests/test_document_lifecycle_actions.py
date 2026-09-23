"""
tests/test_document_lifecycle_actions.py — STOP / MOVE-TO-BIN authorization
and status-classification logic (no DB).

`_authorize`/`ensure_document_visible` never touch the session, so they
are tested directly against plain detached Document/User instances, the
same way test_export_service.py tests underscore-prefixed formatter
internals. DB-backed behavior (stop_document/move_document_to_bin
actually mutating a persisted document, idempotency, the pipeline
withdrawal race, real authenticated sessions) is covered in
tests/integration/test_document_lifecycle_actions.py.
"""

from __future__ import annotations

import uuid

import pytest

from app.core.exceptions import PermissionDeniedError
from app.models.document import Document, DocumentStatus
from app.models.user import User, UserRole
from app.services.document_lifecycle import (
    ACTIVE_DOCUMENT_STATUSES,
    WITHDRAWN_DOCUMENT_STATUSES,
    _authorize,
    ensure_document_visible,
)


def make_document(*, uploaded_by_user_id: uuid.UUID | None = None, status: str = DocumentStatus.UPLOADED) -> Document:
    return Document(
        id=uuid.uuid4(), filename="invoice.pdf", mime_type="application/pdf",
        file_size_bytes=1024, file_path="/uploads/x.pdf", file_hash="a" * 64,
        status=status, uploaded_by_user_id=uploaded_by_user_id,
    )


def make_user(*, role: str = UserRole.USER.value, user_id: uuid.UUID | None = None) -> User:
    return User(
        id=user_id or uuid.uuid4(), username="person", password_hash="x",
        role=role, is_active=True,
    )


class TestStatusClassification:
    def test_active_and_withdrawn_sets_are_disjoint(self):
        assert not (ACTIVE_DOCUMENT_STATUSES & WITHDRAWN_DOCUMENT_STATUSES)

    def test_withdrawn_set_is_exactly_stopped_and_binned(self):
        assert {DocumentStatus.STOPPED, DocumentStatus.BINNED} == WITHDRAWN_DOCUMENT_STATUSES

    def test_terminal_dispositions_are_not_active(self):
        for status in (DocumentStatus.COMPLETED, DocumentStatus.REVIEW_REQUIRED,
                      DocumentStatus.FAILED, DocumentStatus.STOPPED, DocumentStatus.BINNED):
            assert status not in ACTIVE_DOCUMENT_STATUSES

    def test_mid_pipeline_states_are_active(self):
        for status in (DocumentStatus.UPLOADED, DocumentStatus.OCR_IN_PROGRESS,
                      DocumentStatus.OCR_COMPLETED, DocumentStatus.AI_PROCESSING,
                      DocumentStatus.STORE_CONFIRMATION_REQUIRED):
            assert status in ACTIVE_DOCUMENT_STATUSES


class TestAuthorization:
    def test_admin_may_act_on_any_document_including_someone_elses(self):
        doc = make_document(uploaded_by_user_id=uuid.uuid4())
        admin = make_user(role=UserRole.ADMIN.value)
        _authorize(doc, admin)  # must not raise

    def test_manager_may_act_on_any_document_including_someone_elses(self):
        doc = make_document(uploaded_by_user_id=uuid.uuid4())
        manager = make_user(role=UserRole.MANAGER.value)
        _authorize(doc, manager)  # must not raise

    def test_user_may_act_on_their_own_document(self):
        owner_id = uuid.uuid4()
        doc = make_document(uploaded_by_user_id=owner_id)
        owner = make_user(role=UserRole.USER.value, user_id=owner_id)
        _authorize(doc, owner)  # must not raise

    def test_user_is_forbidden_from_someone_elses_document(self):
        doc = make_document(uploaded_by_user_id=uuid.uuid4())
        other = make_user(role=UserRole.USER.value)
        with pytest.raises(PermissionDeniedError):
            _authorize(doc, other)

    def test_user_is_forbidden_from_a_document_with_no_recorded_uploader(self):
        # Fails closed, not open: a NULL uploaded_by_user_id (pre-existing
        # rows from before authentication existed) can never be claimed
        # by a USER.
        doc = make_document(uploaded_by_user_id=None)
        user = make_user(role=UserRole.USER.value)
        with pytest.raises(PermissionDeniedError):
            _authorize(doc, user)

    def test_ensure_document_visible_shares_the_same_ownership_rule(self):
        doc = make_document(uploaded_by_user_id=uuid.uuid4())
        other = make_user(role=UserRole.USER.value)
        with pytest.raises(PermissionDeniedError):
            ensure_document_visible(doc, other)

    def test_admin_and_manager_can_see_any_document(self):
        doc = make_document(uploaded_by_user_id=uuid.uuid4())
        ensure_document_visible(doc, make_user(role=UserRole.ADMIN.value))
        ensure_document_visible(doc, make_user(role=UserRole.MANAGER.value))
