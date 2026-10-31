"""
Export override and consistent export gating — tests/test_export_override.py

While critical checks (completeness, financial reconciliation) fail, PDI, TXT and CSV all refuse; an ADMIN's
recorded override lifts only that block for the exact state it reviewed, and never edits the invoice. These tests pin
the pure rules; tests/integration/test_export_override_db.py pins the routes, permissions and audit trail.
"""

from __future__ import annotations

import csv
import io
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.exceptions import PermissionDeniedError, ValidationError
from app.services import export_override_service as svc
from app.services.export_service import (
    REVIEW_COPY_TITLE,
    build_export_payload,
    build_items_csv,
    build_txt,
    critical_check_gate,
    export_status,
    override_banner,
    persisted_pdi_export_eligibility,
    review_banner,
)
from app.services.gtin import gtin_check_digit
from tests.test_export_service import make_invoice, units_for

BLOCKERS = ["COMPLETENESS_MISSING_LINES", "GRAND_TOTAL_MATH"]
STORE = uuid.UUID(int=7)


def upc(n: int) -> str:
    body = f"9{n:010d}"
    return body + gtin_check_digit(body)


def blocked_invoice(**overrides):
    invoice = make_invoice(**{"store_id": STORE, "blocking_checks": list(BLOCKERS),
                              "status": "REVIEW_REQUIRED", "other_charges": None, **overrides})
    invoice.items[0].product_sku, invoice.items[1].product_sku = upc(1), upc(2)
    return invoice


def grant(invoice, **fields):
    return SimpleNamespace(id=uuid.uuid4(), revoked_at=None, granted_by="admin1", blocked_checks=list(BLOCKERS),
                           state_sha256=svc.state_fingerprint(invoice), **fields)


class TestOneGateForEveryFile:
    def test_failing_critical_checks_block_with_the_check_names(self):
        reason, overridden = critical_check_gate(blocked_invoice())
        assert overridden is False
        assert all(name in reason for name in BLOCKERS) and "administrator" in reason

    def test_an_override_lifts_only_that_block(self):
        invoice = blocked_invoice()
        assert critical_check_gate(invoice, grant(invoice)) == (None, True)

    def test_nothing_failing_is_not_blocked_and_not_overridden(self):
        assert critical_check_gate(blocked_invoice(blocking_checks=None)) == (None, False)
        assert critical_check_gate(blocked_invoice(blocking_checks=[]), grant(blocked_invoice())) == (None, False)

    def test_pdi_with_an_override_is_allowed_unconfirmed_and_marked_overridden(self):
        invoice = blocked_invoice()
        override = grant(invoice)
        gate = persisted_pdi_export_eligibility(invoice, units_for(invoice), override)
        assert (gate.allowed, gate.overridden, gate.override_id) == (True, True, override.id)
        assert gate.requires_confirmation is False

    def test_pdi_without_an_override_stays_blocked(self):
        invoice = blocked_invoice()
        gate = persisted_pdi_export_eligibility(invoice, units_for(invoice))
        assert (gate.allowed, gate.overridden) == (False, False)

    def test_an_override_never_lifts_the_pdi_specific_rules(self):
        invoice = blocked_invoice()
        override = grant(invoice)
        no_mapping = persisted_pdi_export_eligibility(invoice, {}, override)       # units per case unknown
        assert no_mapping.allowed is False and "critical checks" not in (no_mapping.blocked_reason or "")
        invoice.items[0].unit_price = None                                           # a missing cost
        assert persisted_pdi_export_eligibility(invoice, units_for(invoice), override).allowed is False
        pending = blocked_invoice(store_id=None)                                     # store not yet known
        assert persisted_pdi_export_eligibility(pending, {}, grant(pending)).allowed is False


class TestTheOverrideCoversOneExactState:
    def test_the_fingerprint_is_stable(self):
        assert svc.state_fingerprint(blocked_invoice(id=uuid.UUID(int=1))) == \
            svc.state_fingerprint(blocked_invoice(id=uuid.UUID(int=1)))

    @pytest.mark.parametrize("change", [
        lambda i: setattr(i, "grand_total", Decimal("99.99")),
        lambda i: setattr(i, "subtotal", Decimal("1.00")),
        lambda i: setattr(i.items[0], "quantity", Decimal("9.0000")),
        lambda i: setattr(i.items[0], "product_sku", upc(3)),
        lambda i: i.items.pop(),
        lambda i: setattr(i, "other_charges", [{"kind": "delivery", "label": None, "amount": 5}]),
        lambda i: setattr(i, "blocking_checks", ["COMPLETENESS_MISSING_LINES"]),
        lambda i: setattr(i, "blocking_checks", None),
    ])
    def test_any_change_to_the_invoice_or_its_failing_checks_changes_it(self, change):
        invoice = blocked_invoice(id=uuid.UUID(int=1))
        before = svc.state_fingerprint(invoice)
        change(invoice)
        assert svc.state_fingerprint(invoice) != before

    def test_mappings_and_status_are_not_part_of_it(self):
        invoice = blocked_invoice(id=uuid.UUID(int=1))
        before = svc.state_fingerprint(invoice)
        invoice.status = "VALIDATED"
        assert svc.state_fingerprint(invoice) == before

    def test_states(self):
        invoice = blocked_invoice()
        row = grant(invoice)
        assert svc.override_state(row, invoice) == svc.STATE_ACTIVE
        invoice.grand_total = Decimal("1.00")
        assert svc.override_state(row, invoice) == svc.STATE_STALE
        invoice.grand_total = Decimal("46.68")
        row.revoked_at = object()
        assert svc.override_state(row, invoice) == svc.STATE_REVOKED
        assert svc.override_state(grant(invoice), None) == svc.STATE_STALE


class TestReasonAndRole:
    @pytest.mark.parametrize("reason", [None, "", "   ", "too short", "         x         "])
    def test_a_missing_or_token_reason_is_refused(self, reason):
        with pytest.raises(ValidationError):
            svc.clean_reason(reason, what="why")

    def test_a_real_reason_is_trimmed_and_kept(self):
        assert svc.clean_reason("  Vendor confirmed the missing rows are credits  ", what="why") == \
            "Vendor confirmed the missing rows are credits"

    @pytest.mark.parametrize("role", ["MANAGER", "USER"])
    async def test_only_an_admin_can_grant_or_revoke(self, role):
        user = SimpleNamespace(role=role, username="u", id=uuid.uuid4())
        with pytest.raises(PermissionDeniedError):
            await svc.grant_override(None, uuid.uuid4(), user, "a perfectly good reason")
        with pytest.raises(PermissionDeniedError):
            await svc.revoke_override(None, uuid.uuid4(), user, "a perfectly good reason")


class TestReviewCopiesCannotBeMistakenForExports:
    def test_the_banner_names_the_failing_checks_and_the_purpose(self):
        banner = review_banner(blocked_invoice())
        assert banner[0] == REVIEW_COPY_TITLE and "DO NOT IMPORT" in banner[0]
        assert all(name in banner[1] for name in BLOCKERS)

    def test_a_txt_review_copy_starts_with_the_label(self):
        text = build_txt(blocked_invoice(), banner=review_banner(blocked_invoice()))
        assert text.splitlines()[0] == REVIEW_COPY_TITLE
        assert "Vendor" in text                          # the review content is still all there

    def test_a_csv_review_copy_cannot_be_read_as_an_import_file(self):
        body = build_items_csv(blocked_invoice(), banner=review_banner(blocked_invoice()))
        assert body.startswith(f"# {REVIEW_COPY_TITLE}\r\n")
        first = next(csv.reader(io.StringIO(body)))
        assert first[0].startswith("#")                  # an importer expecting the header row rejects it
        rows = list(csv.reader(io.StringIO(body)))
        assert rows[3][:2] == ["Description", "Quantity"]  # three label lines, then the normal header

    def test_without_a_banner_the_files_are_exactly_as_before(self):
        invoice = blocked_invoice()
        assert build_items_csv(invoice).startswith("Description,Quantity")
        assert build_txt(invoice).startswith("Vendor\n------")

    def test_the_override_banner_names_the_admin_and_the_checks(self):
        invoice = blocked_invoice()
        text = build_txt(invoice, banner=override_banner(grant(invoice)), override=grant(invoice))
        head = "\n".join(text.splitlines()[:2])
        assert "OVERRIDE" in head and "admin1" in head and "GRAND_TOTAL_MATH" in head


class TestTheJsonRecordStatesItsOwnReadiness:
    def test_blocked_and_ready_and_overridden(self):
        invoice = blocked_invoice()
        assert export_status(invoice) == {"export_ready": False, "blocking_checks": BLOCKERS,
                                          "overridden_by_admin": False, "override_id": None}
        override = grant(invoice)
        status = export_status(invoice, override)
        assert status["export_ready"] is True and status["overridden_by_admin"] is True
        assert status["blocking_checks"] == BLOCKERS and status["override_id"] == str(override.id)
        clean = export_status(blocked_invoice(blocking_checks=None))
        assert clean == {"export_ready": True, "blocking_checks": [], "overridden_by_admin": False, "override_id": None}

    def test_the_payload_carries_it_and_still_reports_the_real_validation(self):
        invoice = blocked_invoice()
        payload = build_export_payload(invoice, grant(invoice))
        assert payload["export_status"]["overridden_by_admin"] is True
        assert payload["validation"]["status"] == "REVIEW_REQUIRED"      # the override changed nothing about it
