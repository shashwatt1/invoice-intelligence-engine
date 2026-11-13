"""
tests/test_inventory_approval.py — the invoice-to-inventory receipt approval lifecycle (no database).

  * Marking Retailed submits a receipt only with inventory switched on, at a
    physical store, never over a POSTED receipt; the submission writes no
    movement, records the submitter, attestation, export and proposed lines,
    and replaces (SUPERSEDED) a pending one, which it points back to.
  * Approval locks the invoice before anything else, authorizes the receipt's
    own store, refuses anything not pending, stale, ineligible, blocked or
    mis-entered (nothing changes), and otherwise posts once: POSTED with the
    approver separate from the submitter, one movement per line, the same
    idempotency keys. Adjusted quantities keep the proposed figure and need
    a reason.
  * Rejection needs a reason and is terminal; withdrawal (Retailed reopened,
    invoice deleted) moves nothing.
  * Deleting a received invoice reverses its receipt first — only with
    inventory on and the deleter's own grant; otherwise refused unchanged.
  * Staleness and line comparison are pure and name every reason.
  * The ORM refuses any receipt change outside the allowed transitions, and
    any delete; the database CHECKs in migration 0035 equal the model's.

The database end (locking, concurrency, constraints, rollback, the real
Retailed flow and deletion) is tests/integration/test_inventory_approval_db.py.
"""

from __future__ import annotations

import ast
import re
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from importlib import util
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.orm.attributes import set_committed_value

from app.core.exceptions import InvoiceReceivedError, RecordNotFoundError, ValidationError
from app.models import inventory as models
from app.models.inventory import (
    RECEIPT_POSTED,
    RECEIPT_REJECTED,
    RECEIPT_REVERSED,
    RECEIPT_SUBMITTED,
    RECEIPT_SUPERSEDED,
    RECEIPT_WITHDRAWN,
    ImmutableReceiptError,
    InventoryMovement,
    InventoryReceipt,
)
from app.models.store import KIND_PHYSICAL
from app.services import inventory_approval as approval
from app.services import inventory_service as inv
from app.services import store_access
from app.services.product_master import commercial_resolution as cr

ROOT = Path(__file__).resolve().parent.parent
VERSIONS = ROOT / "alembic/versions"
MIGRATION = VERSIONS / "20261023_0035_inventory_receipt_approval.py"

BUD, TEA = "01820053030", "08769283102"
APPROVED = {BUD: (cr.PATH_PRODUCT_MASTER_GLOBAL, 24), TEA: (cr.PATH_PRODUCT_MASTER_PHYSICAL, 4)}
STORE = SimpleNamespace(id=uuid.uuid4(), kind=KIND_PHYSICAL, label="AF NV Midler")
FINGERPRINT = "f" * 64


def item(sort, sku, qty, line_type="product"):
    return SimpleNamespace(id=uuid.uuid4(), sort_order=sort, product_sku=sku, quantity=Decimal(str(qty)),
                           line_type=line_type, description=f"line {sort}")


def resolution(paths=None):
    paths = APPROVED if paths is None else paths
    return cr.CommercialResolution(
        {code: cr.CodeResolution(code, path, units, mapping_id=f"m-{code}", scope_label="Global")
         for code, (path, units) in paths.items()}, enabled=True)


def account(role="MANAGER", name="barj"):
    return SimpleNamespace(id=uuid.uuid4(), username=name, role=role, is_active=True)


def invoice(**overrides):
    values = {"id": uuid.uuid4(), "invoice_number": "508655", "items": [item(1, BUD, 2), item(2, TEA, 3)],
              "status": "VALIDATED", "store_id": STORE.id, "document_id": uuid.uuid4(),
              "document": SimpleNamespace(status="COMPLETED"), "retailed_at": datetime.now(UTC),
              "retailed_attestation_sha256": FINGERPRINT}
    values.update(overrides)
    return SimpleNamespace(**values)


def lines_of(inv_, res=None):
    return inv.build_receipt_lines(inv_, res or resolution())


def submission(inv_, submitter=None, **overrides):
    lines, blockers = lines_of(inv_)
    values = {"id": uuid.uuid4(), "invoice_id": inv_.id, "store_id": STORE.id, "status": RECEIPT_SUBMITTED,
              "attestation_sha256": FINGERPRINT, "submitted_by_user_id": (submitter or account()).id,
              "submitted_by": "vivek", "line_count": len(lines),
              "submission_snapshot": {"lines": [approval.line_snapshot(x) for x in lines],
                                      "blockers": [vars(b) for b in blockers]}}
    values.update(overrides)
    return InventoryReceipt(**values)


class FakeSession:
    def __init__(self):
        self.added, self.flushes, self.objects = [], 0, {}

    def add(self, obj):
        self.added.append(obj)

    def add_all(self, objs):
        self.added.extend(objs)

    async def flush(self):
        self.flushes += 1

    async def get(self, _model, key):
        return self.objects.get(key)

    async def execute(self, _statement):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: None))


# ---- staleness and comparison (pure) -----------------------------------------------------------

class TestStaleness:
    def test_an_unchanged_retailed_invoice_is_current(self):
        inv_ = invoice()
        lines, _ = lines_of(inv_)
        assert approval.staleness(submission(inv_), inv_, FINGERPRINT, lines) == []

    @pytest.mark.parametrize("change, reason", [
        ({"retailed_at": None}, approval.STALE_NOT_RETAILED),
        ({"retailed_attestation_sha256": "a" * 64}, approval.STALE_REMARKED),
        ({"status": "REVIEW_REQUIRED"}, approval.STALE_NOT_VALIDATED),
        ({"store_id": uuid.uuid4()}, approval.STALE_STORE_CHANGED),
    ])
    def test_each_change_to_the_invoice_is_named(self, change, reason):
        inv_ = invoice()
        receipt = submission(inv_)
        lines, _ = lines_of(inv_)
        assert reason in approval.staleness(receipt, invoice(**{**vars(inv_), **change}), FINGERPRINT, lines)

    def test_a_changed_attested_value_or_line_is_stale(self):
        inv_ = invoice()
        receipt = submission(inv_)
        assert approval.staleness(receipt, inv_, "b" * 64, lines_of(inv_)[0]) == [approval.STALE_INVOICE_CHANGED]
        changed = invoice(**{**vars(inv_), "items": [inv_.items[0], item(2, TEA, 5)]})
        assert approval.STALE_LINES_CHANGED in approval.staleness(receipt, changed, FINGERPRINT, lines_of(changed)[0])

    def test_a_deleted_invoice_is_stale(self):
        assert approval.staleness(submission(invoice()), None, None, []) == [approval.STALE_INVOICE_DELETED]


class TestCompareLines:
    def test_added_removed_and_changed_lines_are_listed_in_line_order(self):
        inv_ = invoice()
        before, _ = lines_of(inv_)
        proposed = [approval.line_snapshot(x) for x in before]
        third = item(3, BUD, 1)
        after_invoice = invoice(items=[inv_.items[1], third])
        after, _ = lines_of(after_invoice, resolution({BUD: (cr.PATH_PRODUCT_MASTER_GLOBAL, 24),
                                                       TEA: (cr.PATH_PRODUCT_MASTER_PHYSICAL, 6)}))
        changes = approval.compare_lines(proposed, after)
        assert [(c.change, c.proposed_units, c.current_units) for c in changes] == [
            ("removed", Decimal(48), None), ("changed", Decimal(12), Decimal(18)), ("added", None, Decimal(24))]
        assert (changes[1].proposed_units_per_case, changes[1].current_units_per_case) == (4, 6)

    def test_identical_lines_compare_equal_whatever_the_number_formatting(self):
        inv_ = invoice()
        lines, _ = lines_of(inv_)
        proposed = [{**approval.line_snapshot(x), "cases": f"{x.cases}.000"} for x in lines]
        assert approval.compare_lines(proposed, lines) == []


class TestTheBusinessDate:
    def test_the_approver_states_it_and_it_is_required(self):
        assert approval.received_on_for_approval(date(2026, 10, 9)) == date(2026, 10, 9)
        with pytest.raises(ValidationError):
            approval.received_on_for_approval(None)


# ---- submission -------------------------------------------------------------------------------

@pytest.fixture
def enabled(monkeypatch):
    # schema: the database's inventory schema state (inv.SCHEMA_*); 0035 unless a test says otherwise.
    state = SimpleNamespace(flag=True, schema=inv.SCHEMA_APPROVAL, active=[], store=STORE, previous=None,
                            active_reads=0, posted=None)

    async def schema(_session):
        return state.schema

    async def ready(_session):
        return state.schema == inv.SCHEMA_APPROVAL

    async def posted(_session, _invoice_id):
        return state.posted

    async def active(_session, _invoice_id):
        state.active_reads += 1                 # loads full receipt rows: only valid once 0035 is applied
        assert state.schema == inv.SCHEMA_APPROVAL, "full receipt rows read before 0035"
        return state.active

    async def res(_session, _invoice):
        return resolution()

    class Stores:
        def __init__(self, _session):
            pass

        async def get(self, _sid):
            return state.store

    logs = []

    async def log(_session, document_id, message, payload):
        logs.append((document_id, message, payload))

    monkeypatch.setattr(approval, "get_settings", lambda: SimpleNamespace(inventory_enabled=state.flag))
    monkeypatch.setattr(inv, "inventory_schema", schema)
    monkeypatch.setattr(inv, "approval_schema_ready", ready)
    monkeypatch.setattr(inv, "posted_receipt", posted)
    monkeypatch.setattr(approval, "_active_receipts", active)
    monkeypatch.setattr(approval, "invoice_commercial_resolution", res)
    monkeypatch.setattr(approval, "StoreRepository", Stores)
    monkeypatch.setattr(approval, "_log", log)
    state.logs = logs
    return state


EXPORT = SimpleNamespace(id=uuid.uuid4(), export_number=3, sha256="e" * 64)


class TestSubmission:
    async def test_inventory_off_submits_nothing_and_touches_nothing(self, enabled):
        enabled.flag = False

        class Untouchable:
            def __getattr__(self, name):
                raise AssertionError(f"session.{name} used with inventory off")

        outcome = await approval.submit_on_retailed(Untouchable(), invoice(), fingerprint=FINGERPRINT,
                                                    export=EXPORT, user=account())
        assert (outcome.receipt, outcome.skipped) == (None, approval.SKIP_DISABLED)

    async def test_a_submission_records_who_what_and_the_proposed_lines_and_moves_nothing(self, enabled):
        session, inv_, user = FakeSession(), invoice(), account(name="vivek")
        outcome = await approval.submit_on_retailed(session, inv_, fingerprint=FINGERPRINT, export=EXPORT, user=user)
        receipt = outcome.receipt
        assert session.added == [receipt] and not any(isinstance(o, InventoryMovement) for o in session.added)
        assert (receipt.status, receipt.invoice_id, receipt.store_id, receipt.submitted_by, receipt.submitted_role,
                receipt.submitted_by_user_id, receipt.attestation_sha256, receipt.edi_export_id) == (
            RECEIPT_SUBMITTED, inv_.id, STORE.id, "vivek", "MANAGER", user.id, FINGERPRINT, EXPORT.id)
        assert receipt.received_on is None and receipt.confirmed_by is None and receipt.approved_at is None
        snapshot = receipt.submission_snapshot
        assert [(x["item_code"], x["expected_units"]) for x in snapshot["lines"]] == [(BUD, "48"), (TEA, "12")]
        assert snapshot["export"] == {"id": str(EXPORT.id), "number": 3, "sha256": "e" * 64}
        assert snapshot["eligibility_reasons"] == [] and snapshot["blockers"] == []
        assert enabled.logs[0][2]["event"] == "inventory_receipt_submitted"

    async def test_marking_again_supersedes_the_pending_one_and_links_to_it(self, enabled):
        inv_ = invoice()
        pending = submission(inv_)
        enabled.active = [pending]
        session = FakeSession()
        outcome = await approval.submit_on_retailed(session, inv_, fingerprint="c" * 64, export=EXPORT, user=account())
        assert (pending.status, outcome.superseded) == (RECEIPT_SUPERSEDED, pending)
        assert pending.superseded_at is not None
        assert outcome.receipt.previous_receipt_id == pending.id and outcome.receipt.status == RECEIPT_SUBMITTED
        assert session.flushes == 2                     # the old one leaves the active slot first

    async def test_a_posted_receipt_is_never_resubmitted(self, enabled):
        enabled.active = [SimpleNamespace(status=RECEIPT_POSTED)]
        session = FakeSession()
        outcome = await approval.submit_on_retailed(session, invoice(), fingerprint=FINGERPRINT, export=EXPORT,
                                                    user=account())
        assert (outcome.receipt, outcome.skipped, session.added) == (None, approval.SKIP_ALREADY_RECEIVED, [])

    @pytest.mark.parametrize("schema", [inv.SCHEMA_ABSENT, inv.SCHEMA_LEDGER])
    async def test_before_0035_marking_retailed_submits_nothing_and_reads_no_receipt(self, enabled, schema):
        enabled.schema = schema
        session = FakeSession()
        outcome = await approval.submit_on_retailed(session, invoice(), fingerprint=FINGERPRINT, export=EXPORT,
                                                    user=account())
        assert (outcome.receipt, outcome.skipped, session.added) == (None, approval.SKIP_SCHEMA, [])
        assert enabled.active_reads == 0

    async def test_a_source_identity_store_gets_no_submission(self, enabled):
        enabled.store = SimpleNamespace(id=STORE.id, kind="SOURCE_IDENTITY", label="Item Sales 47708760")
        outcome = await approval.submit_on_retailed(FakeSession(), invoice(), fingerprint=FINGERPRINT,
                                                    export=EXPORT, user=account())
        assert outcome.skipped == approval.SKIP_NOT_PHYSICAL

    def test_mark_retailed_submits_and_reopen_withdraws_in_their_own_transaction(self):
        source = (ROOT / "app/services/invoice_workflow_service.py").read_text()
        mark = source.split("async def mark_retailed(")[1].split("\nasync def ")[0]
        assert mark.index("invoice.retailed_attestation_sha256 = fingerprint") < mark.index(
            "await submit_on_retailed(session, invoice, fingerprint=fingerprint, export=latest, user=user)")
        assert "commit" not in mark
        reopen = source.split("async def reopen_retailed(")[1].split("\nasync def ")[0]
        assert reopen.index("invoice.retailed_at = None") < reopen.index("await withdraw_pending(")
        assert "commit" not in reopen


# ---- approval -----------------------------------------------------------------------------------

@pytest.fixture
def approving(monkeypatch):
    state = SimpleNamespace(order=[], logs=[], fingerprint=FINGERPRINT, preview=None, receipt=None)

    async def lock(_session, invoice_id):
        state.order.append(("lock", invoice_id))

    async def locked_receipt(_session, invoice_id, receipt_id, user):
        state.order.append(("receipt", receipt_id, user.username))
        return state.receipt

    async def preview(_session, invoice_id, user, *, fresh=False):
        state.order.append(("preview", fresh))
        return state.preview

    async def log(_session, document_id, message, payload):
        state.logs.append(payload)

    monkeypatch.setattr(inv, "lock_invoice", lock)
    monkeypatch.setattr(approval, "_locked_receipt", locked_receipt)
    monkeypatch.setattr(inv, "receipt_preview", preview)
    monkeypatch.setattr(approval, "current_facts", lambda _i, _u: ("p", None, state.fingerprint))
    monkeypatch.setattr(approval, "_log", log)

    def setup(inv_=None, submitter=None, **preview_overrides):
        inv_ = inv_ or invoice()
        state.receipt = submission(inv_, submitter)
        lines, blockers = lines_of(inv_)
        values = {"invoice": inv_, "store": STORE, "eligibility_reasons": [], "lines": lines, "blockers": blockers,
                  "receipt": None, "resolution": resolution(), "history": [state.receipt],
                  "submission": state.receipt}
        values.update(preview_overrides)
        state.preview = inv.ReceiptPreview(**values)
        state.invoice, state.lines = inv_, lines
        return state

    state.setup = setup
    return state


async def _approve(state, session, user, accepted=None, reasons=None, received_on=date(2026, 10, 9)):
    accepted = accepted if accepted is not None else {x.invoice_item_id: x.expected_units for x in state.lines}
    return await approval.approve_receipt(session, state.invoice.id, receipt_id=state.receipt.id, accepted=accepted,
                                          reasons=reasons or {}, received_on=received_on, note=" dock 2 ", user=user)


class TestApproval:
    async def test_posts_once_with_the_approver_apart_from_the_submitter(self, approving):
        state = approving.setup()
        session, user = FakeSession(), account(role="ADMIN", name="prabh")
        receipt = await _approve(state, session, user)
        assert state.order[0] == ("lock", state.invoice.id) and state.order[1][0] == "receipt"
        assert ("preview", True) in state.order                       # re-read after the lock
        assert (receipt.status, receipt.received_on, receipt.confirmed_by, receipt.confirmed_role,
                receipt.confirmed_by_user_id, receipt.note, receipt.discrepancy_count, receipt.line_count) == (
            RECEIPT_POSTED, date(2026, 10, 9), "prabh", "ADMIN", user.id, "dock 2", 0, 2)
        assert receipt.submitted_by == "vivek" and receipt.approved_at is not None
        movements = [o for o in session.added if isinstance(o, InventoryMovement)]
        assert [(m.item_code, m.quantity, m.expected_quantity, m.receipt_id, m.store_id) for m in movements] == [
            (BUD, 48, 48, receipt.id, STORE.id), (TEA, 12, 12, receipt.id, STORE.id)]
        assert all(m.idempotency_key == f"receipt:{receipt.id}:{m.invoice_item_id}" for m in movements)
        assert state.logs[-1]["self_approved"] is False

    async def test_self_approval_is_allowed_and_recorded_as_such(self, approving):
        user = account(role="MANAGER", name="vivek")
        state = approving.setup(submitter=user)
        await _approve(state, FakeSession(), user)
        assert state.logs[-1]["self_approved"] is True and state.logs[-1]["submitted_by"] == "vivek"

    async def test_an_adjusted_quantity_needs_a_reason_and_keeps_the_proposed_figure(self, approving):
        state = approving.setup()
        bud = state.lines[0]
        accepted = {bud.invoice_item_id: Decimal(46), state.lines[1].invoice_item_id: Decimal(12)}
        session = FakeSession()
        with pytest.raises(ValidationError, match="cannot be received as entered"):
            await _approve(state, session, account(), accepted=accepted)
        assert state.receipt.status == RECEIPT_SUBMITTED and session.added == []
        with pytest.raises(ValidationError):
            await _approve(state, session, account(), accepted={**accepted, bud.invoice_item_id: Decimal("46.5")},
                           reasons={bud.invoice_item_id: "half"})
        receipt = await _approve(state, session, account(), accepted=accepted,
                                 reasons={bud.invoice_item_id: "2 cans crushed"})
        moved = {m.item_code: m for m in session.added if isinstance(m, InventoryMovement)}
        assert (moved[BUD].quantity, moved[BUD].expected_quantity, moved[BUD].reason) == (46, 48, "2 cans crushed")
        assert receipt.discrepancy_count == 1
        assert state.receipt.submission_snapshot["lines"][0]["expected_units"] == "48"   # proposal kept as made

    @pytest.mark.parametrize("status", [RECEIPT_POSTED, RECEIPT_REJECTED, RECEIPT_WITHDRAWN, RECEIPT_SUPERSEDED,
                                        RECEIPT_REVERSED])
    async def test_only_a_pending_submission_can_be_approved(self, approving, status):
        state = approving.setup()
        state.receipt.status = status
        session = FakeSession()
        with pytest.raises(ValidationError) as exc:
            await _approve(state, session, account())
        assert exc.value.detail["reason"] == "not_pending" and session.added == []

    async def test_a_stale_submission_is_refused_and_nothing_changes(self, approving):
        state = approving.setup()
        state.fingerprint = "9" * 64                      # an attested value changed since Retailed
        session = FakeSession()
        with pytest.raises(ValidationError) as exc:
            await _approve(state, session, account())
        assert exc.value.detail["reason"] == "stale_submission"
        assert approval.STALE_INVOICE_CHANGED in exc.value.detail["stale_reasons"]
        assert state.receipt.status == RECEIPT_SUBMITTED and session.added == [] and session.flushes == 0

    @pytest.mark.parametrize("overrides, reason", [
        ({"eligibility_reasons": ["document_withdrawn"]}, "not_receivable"),
        ({"blockers": [inv.ReceiptBlocker(2, "TEA", TEA, "no approved units-per-case mapping")]},
         "unresolved_conversions"),
    ])
    async def test_ineligible_or_blocked_is_refused(self, approving, overrides, reason):
        state = approving.setup(**overrides)
        session = FakeSession()
        with pytest.raises(ValidationError) as exc:
            await _approve(state, session, account())
        assert exc.value.detail["reason"] == reason and session.added == []

    async def test_no_business_date_is_refused(self, approving):
        state = approving.setup()
        with pytest.raises(ValidationError):
            await _approve(state, FakeSession(), account(), received_on=None)
        assert state.receipt.status == RECEIPT_SUBMITTED


class TestTheReceiptLookup:
    async def test_an_ungranted_store_is_the_same_404_as_an_unknown_receipt(self, monkeypatch):
        receipt = submission(invoice())

        class Session:
            async def execute(self, _statement):
                return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: receipt))

        async def no(_session, _user, _store):
            return False

        monkeypatch.setattr(store_access, "has_store_access", no)
        with pytest.raises(RecordNotFoundError) as exc:
            await approval._locked_receipt(Session(), receipt.invoice_id, receipt.id, account())
        assert exc.value.message == "Receipt not found for this invoice."

    def test_the_lookup_locks_and_re_reads_the_row(self):
        body = (ROOT / "app/services/inventory_approval.py").read_text().split(
            "async def _locked_receipt(")[1].split("\nasync def ")[0]
        assert ".with_for_update().execution_options(populate_existing=True)" in body


# ---- rejection and withdrawal -----------------------------------------------------------------

class TestRejectionAndWithdrawal:
    async def test_rejection_needs_a_reason_and_is_terminal(self, approving):
        state = approving.setup()
        session = FakeSession()
        with pytest.raises(ValidationError):
            await approval.reject_receipt(session, state.invoice.id, receipt_id=state.receipt.id, reason="  ",
                                          user=account())
        assert state.order == []                          # refused before anything is locked or read
        user = account(name="prabh", role="ADMIN")
        receipt = await approval.reject_receipt(session, state.invoice.id, receipt_id=state.receipt.id,
                                                reason=" short-shipped ", user=user)
        assert (receipt.status, receipt.rejected_by, receipt.rejected_role, receipt.rejection_reason) == (
            RECEIPT_REJECTED, "prabh", "ADMIN", "short-shipped")
        assert not any(isinstance(o, InventoryMovement) for o in session.added)
        with pytest.raises(ValidationError) as exc:
            await approval.reject_receipt(session, state.invoice.id, receipt_id=state.receipt.id, reason="again",
                                          user=user)
        assert exc.value.detail["reason"] == "not_pending"
        with pytest.raises(ValidationError):
            await _approve(state, session, user)

    async def test_withdrawal_moves_nothing_and_records_who_and_why(self, enabled):
        pending = submission(invoice())
        enabled.active = [pending]
        receipt = await approval.withdraw_pending(FakeSession(), pending.invoice_id, reason="Retailed mark removed",
                                                  user=account(name="vivek"))
        assert (receipt.status, receipt.withdrawn_by, receipt.withdrawal_reason) == (
            RECEIPT_WITHDRAWN, "vivek", "Retailed mark removed")

    async def test_withdrawal_leaves_a_posted_receipt_alone(self, enabled):
        posted = SimpleNamespace(status=RECEIPT_POSTED)
        enabled.active = [posted]
        assert await approval.withdraw_pending(FakeSession(), uuid.uuid4(), reason="x", user=account()) is None
        assert posted.status == RECEIPT_POSTED

    @pytest.mark.parametrize("schema", [inv.SCHEMA_ABSENT, inv.SCHEMA_LEDGER])
    async def test_before_0035_there_is_nothing_to_withdraw_and_no_receipt_row_is_read(self, enabled, schema):
        enabled.schema = schema
        assert await approval.withdraw_pending(FakeSession(), uuid.uuid4(), reason="x", user=account()) is None
        assert enabled.active_reads == 0


# ---- deletion ---------------------------------------------------------------------------------

@pytest.fixture
def deleting(enabled, monkeypatch):
    enabled.access = True
    enabled.reversed = []

    async def lock(_session, _invoice_id):
        return None

    async def access(_session, _user, _store):
        return enabled.access

    async def reverse(_session, invoice_id, *, receipt_id, reason, user):
        enabled.reversed.append((invoice_id, receipt_id, reason, user.username))
        return inv.ReversalResult(SimpleNamespace(id=receipt_id), [object(), object()])

    monkeypatch.setattr(inv, "lock_invoice", lock)
    monkeypatch.setattr(store_access, "has_store_access", access)
    monkeypatch.setattr(inv, "reverse_receipt", reverse)
    return enabled


class TestDeletion:
    async def test_a_posted_receipt_is_reversed_before_the_delete(self, deleting):
        posted = SimpleNamespace(id=uuid.uuid4(), status=RECEIPT_POSTED, store_id=STORE.id)
        deleting.active = [posted]
        session, invoice_id = FakeSession(), uuid.uuid4()
        session.objects[invoice_id] = SimpleNamespace(invoice_number="508655")
        release = await approval.release_invoice_for_deletion(session, invoice_id, account(role="ADMIN", name="prabh"))
        assert deleting.reversed == [(invoice_id, posted.id, "Invoice #508655 deleted by prabh", "prabh")]
        assert release.movements_reversed == 2

    @pytest.mark.parametrize("flag, access, blocked_by", [(False, True, "inventory_disabled"),
                                                          (True, False, "no_store_access")])
    async def test_without_inventory_or_a_grant_the_delete_is_refused_unchanged(self, deleting, flag, access,
                                                                                blocked_by):
        deleting.flag, deleting.access = flag, access
        deleting.active = [SimpleNamespace(id=uuid.uuid4(), status=RECEIPT_POSTED, store_id=STORE.id)]
        with pytest.raises(InvoiceReceivedError) as exc:
            await approval.release_invoice_for_deletion(FakeSession(), uuid.uuid4(), account(role="ADMIN"))
        assert exc.value.http_status == 409
        assert exc.value.detail["blocked_by"] == blocked_by and exc.value.detail["action"] == "delete_invoice"
        assert "receipt_id" not in exc.value.detail and deleting.reversed == []

    async def test_a_pending_submission_is_withdrawn_even_with_inventory_off(self, deleting):
        deleting.flag = False
        pending = submission(invoice())
        deleting.active = [pending]
        release = await approval.release_invoice_for_deletion(FakeSession(), pending.invoice_id,
                                                              account(role="ADMIN", name="prabh"))
        assert release.withdrawn is pending and pending.status == RECEIPT_WITHDRAWN
        assert pending.withdrawal_reason.endswith("deleted by prabh") and deleting.reversed == []

    async def test_before_0035_a_posted_receipt_still_refuses_the_delete_unchanged(self, deleting):
        deleting.schema = inv.SCHEMA_LEDGER
        deleting.posted = SimpleNamespace(id=uuid.uuid4(), received_on=date(2026, 10, 9), confirmed_by="barj")
        with pytest.raises(InvoiceReceivedError) as exc:
            await approval.release_invoice_for_deletion(FakeSession(), uuid.uuid4(), account(role="ADMIN"))
        assert exc.value.http_status == 409
        assert exc.value.detail["blocked_by"] == "inventory_schema_not_migrated"
        assert deleting.reversed == [] and deleting.active_reads == 0

    @pytest.mark.parametrize("schema", [inv.SCHEMA_ABSENT, inv.SCHEMA_LEDGER])
    async def test_before_0035_an_unreceived_invoice_is_released_without_reading_receipt_rows(self, deleting, schema):
        deleting.schema = schema
        release = await approval.release_invoice_for_deletion(FakeSession(), uuid.uuid4(), account(role="ADMIN"))
        assert (release.reversed, release.withdrawn) == (None, None)
        assert deleting.reversed == [] and deleting.active_reads == 0

    def test_the_delete_route_releases_under_the_lock_then_deletes_and_commits_once(self):
        tree = ast.parse((ROOT / "app/api/v1/invoices.py").read_text())
        [fn] = [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "delete_invoice"]
        body = ast.unparse(fn)
        release = body.index("inventory_approval.release_invoice_for_deletion(db, invoice_id, user)")
        assert release < body.index("get_detail(invoice_id, fresh=True)") < body.index(
            "DocumentRepository(db).delete(document)") < body.index("db.commit()")
        assert body.count("db.commit()") == 1 and "ensure_invoice_changeable" not in body


# ---- the transition guard -----------------------------------------------------------------------

def _stored(status, **values):
    receipt = InventoryReceipt()
    for key, value in {"id": uuid.uuid4(), "status": status, **values}.items():
        set_committed_value(receipt, key, value)
    return receipt


def _connection(status):
    return SimpleNamespace(execute=lambda _s: SimpleNamespace(scalar=lambda: status))


class TestTheTransitionGuard:
    @pytest.mark.parametrize("old, new, fields", [
        (RECEIPT_SUBMITTED, RECEIPT_POSTED, {"received_on": date(2026, 10, 9), "confirmed_by": "p",
                                             "confirmed_role": "ADMIN", "discrepancy_count": 0}),
        (RECEIPT_SUBMITTED, RECEIPT_REJECTED, {"rejected_by": "p", "rejection_reason": "x"}),
        (RECEIPT_SUBMITTED, RECEIPT_WITHDRAWN, {"withdrawn_by": "p", "withdrawal_reason": "x"}),
        (RECEIPT_SUBMITTED, RECEIPT_SUPERSEDED, {"superseded_at": datetime.now(UTC)}),
        (RECEIPT_POSTED, RECEIPT_REVERSED, {"reversed_by": "p", "reversal_reason": "x"}),
    ])
    def test_the_allowed_transitions_pass(self, old, new, fields):
        receipt = _stored(old)
        receipt.status = new
        for key, value in fields.items():
            setattr(receipt, key, value)
        models._receipts_change_only_by_transition(None, _connection(old), receipt)

    @pytest.mark.parametrize("old, new", [
        (RECEIPT_REJECTED, RECEIPT_SUBMITTED), (RECEIPT_REJECTED, RECEIPT_POSTED), (RECEIPT_WITHDRAWN, RECEIPT_POSTED),
        (RECEIPT_REVERSED, RECEIPT_POSTED), (RECEIPT_SUPERSEDED, RECEIPT_SUBMITTED), (RECEIPT_SUBMITTED, RECEIPT_REVERSED),
        (RECEIPT_POSTED, RECEIPT_WITHDRAWN), (RECEIPT_POSTED, RECEIPT_POSTED),
    ])
    def test_every_other_transition_is_refused(self, old, new):
        receipt = _stored(old)
        receipt.status = new
        receipt.note = "edited"
        with pytest.raises(ImmutableReceiptError):
            models._receipts_change_only_by_transition(None, _connection(old), receipt)

    def test_the_stored_status_decides_not_the_loaded_copy(self):
        receipt = _stored(RECEIPT_SUBMITTED)            # loaded while pending ...
        receipt.status = RECEIPT_POSTED
        with pytest.raises(ImmutableReceiptError):      # ... but another transaction rejected it meanwhile
            models._receipts_change_only_by_transition(None, _connection(RECEIPT_REJECTED), receipt)

    def test_a_transition_cannot_carry_another_states_fields(self):
        receipt = _stored(RECEIPT_SUBMITTED, submission_snapshot={"lines": []})
        receipt.status = RECEIPT_REJECTED
        receipt.rejection_reason = "x"
        receipt.submission_snapshot = {"lines": ["rewritten"]}
        with pytest.raises(ImmutableReceiptError, match="submission_snapshot"):
            models._receipts_change_only_by_transition(None, _connection(RECEIPT_SUBMITTED), receipt)

    def test_a_receipt_is_never_deleted(self):
        with pytest.raises(ImmutableReceiptError):
            models._receipts_are_never_deleted(None, None, InventoryReceipt())


# ---- migration 0035 -----------------------------------------------------------------------------

def _load(path):
    spec = util.spec_from_file_location(path.stem, path)
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestTheMigration:
    def test_0035_follows_0034_and_the_graph_has_one_head(self):
        revisions = {}
        for path in VERSIONS.glob("*.py"):
            source = path.read_text()
            rev = re.search(r'^revision = "([^"]+)"', source, re.M)
            down = re.search(r'^down_revision = (?:"([^"]+)"|None)', source, re.M)
            if rev:
                revisions[rev.group(1)] = down.group(1) if down else None
        assert revisions["0035"] == "0034"
        assert revisions["0036"] == "0035"          # store inventory baselines, on top of 0035
        assert revisions["0037"] == "0036"          # recycle bin, on top of 0036
        assert revisions["0038"] == "0037"          # shared mappings + vendor item crosswalk
        assert revisions["0039"] == "0038"          # typed other charges + printed units
        assert revisions["0040"] == "0039"          # vendor and delivery date at upload, vendor requests
        assert revisions["0041"] == "0040"          # vendor merge: tombstone pointer and the merge record
        assert revisions["0042"] == "0041"          # one active session per account: users.session_version
        heads = set(revisions) - {d for d in revisions.values() if d}
        # 0025 is an unreleased branch off 0024, re-parented when released (see 0033's docstring).
        assert heads - {"0025"} == {"0042"}, heads

    def test_the_database_checks_are_exactly_the_models(self):
        m0035 = _load(MIGRATION)
        model = {c.name: str(c.sqltext) for c in InventoryReceipt.__table__.constraints
                 if c.name and c.name.startswith("ck_")}
        # The reversal check is 0033's, unchanged.
        assert model == {**m0035.CHECKS, "ck_inventory_receipts_reversal": models.RECEIPT_CHECKS[
            "ck_inventory_receipts_reversal"]}
        assert "ck_inventory_receipts_reversal" in (VERSIONS / "20261023_0033_store_inventory_ledger.py").read_text()

    def test_one_active_receipt_per_invoice_and_the_pending_queue_index(self):
        source = MIGRATION.read_text()
        assert '"uq_inventory_receipts_invoice_active"' in source and "status IN ('POSTED', 'SUBMITTED')" in source
        assert 'op.drop_index("uq_inventory_receipts_invoice_posted"' in source
        indexes = {i.name: i for i in InventoryReceipt.__table__.indexes}
        assert indexes["uq_inventory_receipts_invoice_active"].unique
        assert str(indexes["uq_inventory_receipts_invoice_active"].dialect_options["postgresql"]["where"]) == \
            models.ACTIVE_INDEX_WHERE == "status IN ('POSTED', 'SUBMITTED')"

    def test_the_upgrade_changes_no_row_and_the_downgrade_refuses_to_discard_history(self):
        source = MIGRATION.read_text()
        upgrade = source.split("def upgrade()")[1].split("def downgrade()")[0]
        for forbidden in ("UPDATE ", "DELETE ", "TRUNCATE", "op.drop_column", "op.drop_table", "op.execute("):
            assert forbidden not in upgrade, forbidden
        downgrade = source.split("def downgrade()")[1]
        assert downgrade.index("RAISE EXCEPTION") < downgrade.index("op.drop_column")
        assert "submitted_at IS NOT NULL" in downgrade

    def test_existing_directly_posted_receipts_satisfy_every_new_rule(self):
        # A pre-0035 POSTED/REVERSED row has no submission fields: each check passes it.
        checks = models.RECEIPT_CHECKS
        assert "submitted_at IS NULL OR approved_at IS NOT NULL" in checks["ck_inventory_receipts_posting"]
        assert checks["ck_inventory_receipts_submission"].startswith("(submitted_at IS NULL OR")

    def test_the_earlier_migrations_are_untouched_by_this_change(self):
        for name in ("20261013_0032_invoice_reference_link_and_retailed.py", "20261023_0033_store_inventory_ledger.py",
                     "20261023_0034_user_store_access.py"):
            assert "0035" not in (VERSIONS / name).read_text(), name
