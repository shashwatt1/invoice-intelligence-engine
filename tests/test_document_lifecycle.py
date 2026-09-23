"""
The one mapping from a validation decision to the document's lifecycle
state, shared by first persistence and every governed revalidation.
"""

from app.models.document import DocumentStatus
from app.services.document_lifecycle import SYNCED_DOCUMENT_STATUSES, document_status_for
from app.services.validation.report import ProcessingDecision


class TestDocumentStatusForDecision:
    def test_validated_completes_the_document(self):
        assert document_status_for(ProcessingDecision.VALIDATED) is DocumentStatus.COMPLETED
        assert document_status_for("VALIDATED") is DocumentStatus.COMPLETED

    def test_anything_needing_review_keeps_the_document_in_review(self):
        assert document_status_for(ProcessingDecision.REVIEW_REQUIRED) is DocumentStatus.REVIEW_REQUIRED
        assert document_status_for("REVIEW_REQUIRED") is DocumentStatus.REVIEW_REQUIRED

    def test_only_terminal_persisted_states_are_ever_synced(self):
        # FAILED and STORE_CONFIRMATION_REQUIRED are facts about the
        # document, not about the invoice's arithmetic — never overridden.
        assert {DocumentStatus.COMPLETED, DocumentStatus.REVIEW_REQUIRED} == SYNCED_DOCUMENT_STATUSES
