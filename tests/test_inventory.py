"""
tests/test_inventory.py — store inventory: receipts, opening counts, balances.

  * A receipt covers exactly the lines the PDI export would carry (no charges,
    voided, duplicate or zero-quantity shorted rows), each converted to
    sellable units by the approved units per case; any such line without an
    approved conversion blocks the whole receipt and is named.
  * Every line's accepted quantity is stated; one that differs from expected
    needs a reason; negatives and fractions are refused; nothing posts partly.
  * A balance exists only once an audited opening count does; the count is
    the closing stock of its date, so movements on or before it are in it.
  * The API answers 404 while INVENTORY_ENABLED is off, refuses USER, refuses
    a source identity (47708760) as a store, and records the signed-in account.
  * Migration 0033 is additive, chains from 0032, matches the models, and
    enables row level security on both tables.
  * A POSTED receipt locks its invoice: every path that changes lines, store
    or document standing calls the guard; before 0033 is applied the guard
    only locks. Reversal needs a reason and is flag-gated like the rest.
"""

from __future__ import annotations

import ast
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api.v1 import inventory as api
from app.core.exceptions import ValidationError
from app.models.inventory import InventoryMovement, InventoryReceipt
from app.models.store import Store
from app.models.user import User, UserRole
from app.services import inventory_service as inv
from app.services.product_master import commercial_resolution as cr

ROOT = Path(__file__).resolve().parent.parent
MIGRATION = ROOT / "alembic/versions/20261023_0033_store_inventory_ledger.py"

BUD = "01820053030"            # 11-digit PDI code (UPC-A 018200530304 without its check digit)
TEA = "08769283102"
MINI = "08800404783"


def item(sort, sku, qty, line_type="product", description=None):
    return SimpleNamespace(id=uuid.uuid4(), sort_order=sort, product_sku=sku, quantity=Decimal(str(qty)),
                           line_type=line_type, description=description or f"line {sort}")


def resolution(**paths):
    lines = {}
    for code, (path, units) in paths.items():
        lines[code] = cr.CodeResolution(code, path, units, mapping_id=f"m-{code}", scope_label="Global")
    return cr.CommercialResolution(lines, enabled=True)


APPROVED = {BUD: (cr.PATH_PRODUCT_MASTER_GLOBAL, 24), TEA: (cr.PATH_PRODUCT_MASTER_PHYSICAL, 4)}


# ---- receipt lines ---------------------------------------------------------------------

class TestReceiptLines:
    def test_cases_become_sellable_units_with_the_conversion_recorded(self):
        invoice = SimpleNamespace(items=[item(1, BUD, 2), item(2, TEA, 3)])
        lines, blockers = inv.build_receipt_lines(invoice, resolution(**APPROVED))
        assert blockers == []
        assert [(x.item_code, x.cases, x.units_per_case, x.expected_units, x.conversion_path) for x in lines] == [
            (BUD, 2, 24, 48, cr.PATH_PRODUCT_MASTER_GLOBAL), (TEA, 3, 4, 12, cr.PATH_PRODUCT_MASTER_PHYSICAL)]
        assert lines[0].conversion_mapping_id == f"m-{BUD}"

    def test_charges_voided_duplicates_and_shorted_lines_are_not_received(self):
        invoice = SimpleNamespace(items=[
            item(1, BUD, 1), item(2, None, 1, line_type="charge", description="FUEL SURCHARGE"),
            item(3, TEA, 2, line_type="voided"), item(4, TEA, 2, line_type="duplicate"),
            item(5, MINI, 0, description="SHORTED"),
        ])
        lines, blockers = inv.build_receipt_lines(invoice, resolution(**APPROVED))
        assert [x.sort_order for x in lines] == [1] and blockers == []   # MINI is unmapped but shorted: no block

    def test_a_line_without_an_approved_conversion_blocks_and_is_named(self):
        invoice = SimpleNamespace(items=[item(1, BUD, 1), item(2, MINI, 2, description="FIREBALL 50ML"),
                                         item(3, TEA, 1), item(4, None, 1, description="NO UPC")])
        res = resolution(**{BUD: APPROVED[BUD], MINI: (cr.PATH_REQUIRES_MAPPING, None),
                            TEA: (cr.PATH_CONFLICT, None)})
        lines, blockers = inv.build_receipt_lines(invoice, res)
        assert [x.item_code for x in lines] == [BUD]
        assert [(b.sort_order, b.item_code, b.reason) for b in blockers] == [
            (2, MINI, "no approved units-per-case mapping"),
            (3, TEA, "Product Master mappings for this item disagree"),
            (4, None, "no item code: the line cannot be keyed to a product"),
        ]
        assert blockers[0].description == "FIREBALL 50ML"

    def test_the_legacy_fallback_is_accepted_exactly_as_the_export_accepts_it(self):
        invoice = SimpleNamespace(items=[item(1, BUD, 1)])
        lines, blockers = inv.build_receipt_lines(invoice, resolution(**{BUD: (cr.PATH_LEGACY_FALLBACK, 12)}))
        assert blockers == [] and lines[0].expected_units == 12
        assert cr.PRODUCT_MASTER_PATHS | {cr.PATH_LEGACY_FALLBACK} == inv.APPROVED_PATHS

    def test_a_12_digit_upc_is_keyed_by_the_11_digit_pdi_code(self):
        invoice = SimpleNamespace(items=[item(1, "018200530304", 1)])
        lines, _ = inv.build_receipt_lines(invoice, resolution(**APPROVED))
        assert lines[0].item_code == BUD


# ---- accepted quantities -------------------------------------------------------------------

def _lines():
    invoice = SimpleNamespace(items=[item(1, BUD, 2), item(2, TEA, 3)])
    return inv.build_receipt_lines(invoice, resolution(**APPROVED))[0]


class TestAcceptedQuantities:
    def test_the_expected_quantity_needs_no_reason(self):
        lines = _lines()
        got = inv.accepted_quantities(lines, {x.invoice_item_id: x.expected_units for x in lines}, {})
        assert [(q, r) for _, q, r in got] == [(48, None), (12, None)]

    def test_a_discrepancy_with_a_reason_is_kept_as_entered(self):
        lines = _lines()
        got = inv.accepted_quantities(lines, {lines[0].invoice_item_id: Decimal("46"),
                                              lines[1].invoice_item_id: Decimal("12")},
                                      {lines[0].invoice_item_id: "  2 cans crushed  "})
        assert [(q, r) for _, q, r in got] == [(46, "2 cans crushed"), (12, None)]

    @pytest.mark.parametrize("quantity, reason, message", [
        (Decimal("46"), None, "a reason is required"),
        (Decimal("46"), "   ", "a reason is required"),
        (Decimal("-1"), "returned", "cannot be negative"),
        (Decimal("47.5"), "half a pack", "whole number"),
    ])
    def test_a_bad_line_refuses_the_whole_receipt(self, quantity, reason, message):
        lines = _lines()
        with pytest.raises(ValidationError) as exc:
            inv.accepted_quantities(lines, {lines[0].invoice_item_id: quantity, lines[1].invoice_item_id: 12},
                                    {lines[0].invoice_item_id: reason} if reason else {})
        problems = exc.value.detail["lines"]
        assert list(problems) == [str(lines[0].invoice_item_id)] and message in problems[str(lines[0].invoice_item_id)]

    def test_every_line_must_be_stated_and_no_other_line_may_be(self):
        lines = _lines()
        stranger = uuid.uuid4()
        with pytest.raises(ValidationError) as exc:
            inv.accepted_quantities(lines, {lines[0].invoice_item_id: 48, stranger: 5}, {})
        assert exc.value.detail["lines"] == {str(stranger): "not a line this receipt covers",
                                             str(lines[1].invoice_item_id): "accepted quantity missing"}

    def test_zero_accepted_is_a_discrepancy_like_any_other(self):
        lines = _lines()
        got = inv.accepted_quantities(lines, {lines[0].invoice_item_id: 0, lines[1].invoice_item_id: 12},
                                      {lines[0].invoice_item_id: "refused at the door: damaged"})
        assert got[0][1] == 0


# ---- balances -----------------------------------------------------------------------------

def mv(code, kind, qty, day, description=None):
    return {"item_code": code, "movement_type": kind, "quantity": Decimal(str(qty)), "business_date": day,
            "description": description}


class TestBalances:
    def test_without_an_opening_count_there_is_no_on_hand_figure(self):
        [b] = inv.fold_balances([mv(BUD, "RECEIPT", 48, date(2026, 10, 1), "BUD LIGHT")])
        assert (b.status, b.on_hand, b.received, b.description) == (inv.STATUS_OPENING_REQUIRED, None, 48, "BUD LIGHT")

    def test_the_count_is_the_closing_stock_of_its_date(self):
        rows = [mv(BUD, "RECEIPT", 48, date(2026, 10, 1)),       # before the count: already in it
                mv(BUD, "RECEIPT", 24, date(2026, 10, 5)),       # on the count date: already in it
                mv(BUD, "OPENING_COUNT", 30, date(2026, 10, 5)),
                mv(BUD, "RECEIPT", 24, date(2026, 10, 6)),
                mv(BUD, "SALE", -10, date(2026, 10, 7)),
                mv(BUD, "ADJUSTMENT", -2, date(2026, 10, 8))]
        [b] = inv.fold_balances(rows)
        assert (b.opening_date, b.opening_quantity, b.received, b.sold, b.other) == (date(2026, 10, 5), 30, 24, -10, -2)
        assert (b.on_hand, b.status, b.last_movement) == (42, inv.STATUS_IN_STOCK, date(2026, 10, 8))

    def test_zero_or_less_is_out_of_stock_and_products_are_kept_apart(self):
        rows = [mv(BUD, "OPENING_COUNT", 0, date(2026, 10, 5)),
                mv(TEA, "OPENING_COUNT", 4, date(2026, 10, 5)), mv(TEA, "SALE", -6, date(2026, 10, 6)),
                mv(MINI, "RECEIPT", 12, date(2026, 10, 6))]
        balances = {b.item_code: b for b in inv.fold_balances(rows)}
        assert [balances[c].status for c in (BUD, TEA, MINI)] == [
            inv.STATUS_OUT_OF_STOCK, inv.STATUS_OUT_OF_STOCK, inv.STATUS_OPENING_REQUIRED]
        assert balances[TEA].on_hand == -2 and balances[MINI].on_hand is None
        assert list(balances) == sorted(balances)


# ---- API -----------------------------------------------------------------------------------

STORE = Store(id=uuid.uuid4(), display_name="AF NV Midler", address_line_1="1 Main St",
              identity_status="confirmed", identifiers=[])


class _Session:
    def __init__(self):
        self.committed = 0

    async def commit(self):
        self.committed += 1


@pytest.fixture
def endpoint(app, monkeypatch):
    from app.core.dependencies import get_current_user
    from app.database.session import get_db

    session = _Session()
    state = SimpleNamespace(session=session, role=UserRole.MANAGER.value, calls=[], flag=True)

    async def fake_db():
        yield session

    def current_user():
        return User(id=uuid.uuid4(), username="barj", password_hash="x", role=state.role, is_active=True)

    async def physical_store(_db, sid):
        if sid != STORE.id:
            raise ValidationError(message="Item Sales · 47708760 is an unresolved source identity",
                                  detail={"field": "store_id", "reason": "source_identity"})
        return STORE

    async def balances(_db, sid):
        return inv.fold_balances([mv(BUD, "OPENING_COUNT", 0, date(2026, 10, 5), "BUD LIGHT"),
                                  mv(TEA, "OPENING_COUNT", 8, date(2026, 10, 5), "TWISTED TEA"),
                                  mv(MINI, "RECEIPT", 12, date(2026, 10, 6), "FIREBALL")])

    async def post_receipt(_db, invoice_id, **kwargs):
        state.calls.append(("receipt", invoice_id, kwargs))

    async def preview(_db, invoice_id):
        return SimpleNamespace(invoice=SimpleNamespace(id=invoice_id, invoice_number="508655"), store=STORE,
                               eligibility_reasons=[], lines=[], receipt=None, history=[], can_post=False,
                               blockers=[inv.ReceiptBlocker(2, "FIREBALL 50ML", MINI, "no approved units-per-case mapping")])

    async def opening(_db, sid, **kwargs):
        await physical_store(_db, sid)
        state.calls.append(("opening", sid, kwargs))
        return [object()] * len(kwargs["entries"])

    async def nothing(*_a, **_k):
        return None

    async def none_list(*_a, **_k):
        return []

    monkeypatch.setattr(inv, "physical_store", physical_store)
    monkeypatch.setattr(inv, "store_balances", balances)
    monkeypatch.setattr(inv, "post_receipt", post_receipt)
    monkeypatch.setattr(inv, "receipt_preview", preview)
    monkeypatch.setattr(inv, "record_opening_counts", opening)
    monkeypatch.setattr(inv, "last_recorded", nothing)
    monkeypatch.setattr(inv, "recent_receipts", none_list)
    monkeypatch.setattr(api, "get_settings", lambda: SimpleNamespace(inventory_enabled=state.flag))
    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_current_user] = current_user
    yield state
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_current_user, None)


class TestTheInventoryApi:
    async def test_off_by_default_every_route_answers_404(self, client, endpoint):
        from app.core.config import Settings

        assert Settings.model_fields["inventory_enabled"].default is False
        endpoint.flag = False
        for method, path in (("get", f"/api/v1/inventory/stores/{STORE.id}/overview"),
                             ("get", f"/api/v1/inventory/stores/{STORE.id}/items"),
                             ("get", f"/api/v1/inventory/invoices/{uuid.uuid4()}/receipt"),
                             ("post", f"/api/v1/inventory/invoices/{uuid.uuid4()}/receipt")):
            r = await client.request(method.upper(), path, json={})
            assert r.status_code == 404, (path, r.text)
        assert endpoint.calls == []

    async def test_the_status_route_says_whether_inventory_is_on_and_only_to_managers(self, client, endpoint):
        endpoint.flag = False
        r = await client.get("/api/v1/inventory/status")
        assert r.status_code == 200 and r.json()["data"] == {"enabled": False}
        endpoint.flag = True
        assert (await client.get("/api/v1/inventory/status")).json()["data"] == {"enabled": True}
        endpoint.role = UserRole.USER.value
        assert (await client.get("/api/v1/inventory/status")).status_code == 403

    async def test_a_user_below_manager_is_refused(self, client, endpoint):
        endpoint.role = UserRole.USER.value
        r = await client.get(f"/api/v1/inventory/stores/{STORE.id}/overview")
        assert r.status_code == 403, r.text

    async def test_a_source_identity_is_not_a_store(self, client, endpoint):
        r = await client.get(f"/api/v1/inventory/stores/{uuid.uuid4()}/items")
        assert r.status_code == 422 and r.json()["error"]["detail"]["reason"] == "source_identity"

    async def test_overview_counts_and_the_opening_required_label(self, client, endpoint):
        r = await client.get(f"/api/v1/inventory/stores/{STORE.id}/overview")
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert (data["tracked_products"], data["opening_required"], data["out_of_stock"], data["in_stock"]) == (3, 1, 1, 1)

    async def test_items_search_status_and_paging(self, client, endpoint):
        base = f"/api/v1/inventory/stores/{STORE.id}/items"
        page = (await client.get(base, params={"page_size": 2})).json()
        assert page["total"] == 3 and [i["item_code"] for i in page["items"]] == [BUD, TEA]
        mini = (await client.get(base, params={"search": "fireball"})).json()["items"]
        assert [(i["item_code"], i["status"], i["on_hand"]) for i in mini] == [(MINI, "OPENING_STOCK_REQUIRED", None)]
        out = (await client.get(base, params={"status": "OUT_OF_STOCK"})).json()["items"]
        assert [i["item_code"] for i in out] == [BUD]

    async def test_a_receipt_records_the_signed_in_account_and_each_line(self, client, endpoint):
        invoice_id, line_a, line_b = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        r = await client.post(f"/api/v1/inventory/invoices/{invoice_id}/receipt", json={
            "received_on": "2026-10-09", "note": "driver Mike",
            "lines": [{"invoice_item_id": str(line_a), "accepted_units": 46, "reason": "2 crushed"},
                      {"invoice_item_id": str(line_b), "accepted_units": 12}]})
        assert r.status_code == 201, r.text
        [(kind, iid, kwargs)] = endpoint.calls
        assert (kind, iid, kwargs["user"].username, kwargs["received_on"]) == ("receipt", invoice_id, "barj",
                                                                                 date(2026, 10, 9))
        assert kwargs["accepted"] == {line_a: Decimal("46"), line_b: Decimal("12")}
        assert kwargs["reasons"] == {line_a: "2 crushed"}
        assert endpoint.session.committed == 1

    async def test_the_preview_names_the_blocking_products(self, client, endpoint):
        r = await client.get(f"/api/v1/inventory/invoices/{uuid.uuid4()}/receipt")
        data = r.json()["data"]
        assert data["can_post"] is False
        assert data["blockers"] == [{"sort_order": 2, "description": "FIREBALL 50ML", "item_code": MINI,
                                     "reason": "no approved units-per-case mapping"}]

    async def test_opening_counts_need_a_reason_and_a_physical_store(self, client, endpoint):
        body = {"count_date": "2026-10-05", "reason": "full count by barj and vivek",
                "entries": [{"item_code": BUD, "quantity": 30}]}
        r = await client.post(f"/api/v1/inventory/stores/{STORE.id}/opening-counts", json=body)
        assert r.status_code == 201 and r.json()["data"] == {"recorded": 1}
        assert endpoint.calls[0][2]["user"].username == "barj"
        r = await client.post(f"/api/v1/inventory/stores/{STORE.id}/opening-counts", json={**body, "reason": ""})
        assert r.status_code == 422
        r = await client.post(f"/api/v1/inventory/stores/{uuid.uuid4()}/opening-counts", json=body)
        assert r.status_code == 422 and len(endpoint.calls) == 1


# ---- service refusals (no database) ----------------------------------------------------------

class TestPostingRefusals:
    @pytest.fixture
    def preview(self, monkeypatch):
        state = SimpleNamespace(value=None)

        async def fake(_session, _invoice_id):
            return state.value

        async def no_lock(_session, _invoice_id):
            return None
        monkeypatch.setattr(inv, "receipt_preview", fake)
        monkeypatch.setattr(inv, "lock_invoice", no_lock)       # no database here; locking is tested against Postgres
        return state

    def _preview(self, **overrides):
        invoice = SimpleNamespace(id=uuid.uuid4(), invoice_number="508655", items=[item(1, BUD, 2)])
        lines, blockers = inv.build_receipt_lines(invoice, resolution(**APPROVED))
        values = {"invoice": invoice, "store": STORE, "eligibility_reasons": [], "lines": lines, "blockers": blockers,
                      "receipt": None, "resolution": resolution(**APPROVED)}
        values.update(overrides)
        return inv.ReceiptPreview(**values)

    async def _post(self, accepted=None):
        return await inv.post_receipt(object(), uuid.uuid4(), accepted=accepted or {}, reasons={},
                                      received_on=date(2026, 10, 9), note=None,
                                      user=SimpleNamespace(id=uuid.uuid4(), username="barj", role="MANAGER"))

    async def test_an_invoice_is_received_once(self, preview):
        preview.value = self._preview(receipt=SimpleNamespace(id=uuid.uuid4()))
        with pytest.raises(ValidationError) as exc:
            await self._post()
        assert exc.value.detail["reason"] == "already_received"

    async def test_validation_alone_is_not_receipt_and_an_ineligible_invoice_is_refused(self, preview):
        preview.value = self._preview(eligibility_reasons=["not_validated"])
        with pytest.raises(ValidationError) as exc:
            await self._post()
        assert exc.value.detail["reasons"] == ["not_validated"]

    async def test_one_unresolved_conversion_blocks_the_whole_receipt(self, preview):
        preview.value = self._preview(blockers=[inv.ReceiptBlocker(2, "FIREBALL", MINI, "no approved units-per-case mapping")])
        with pytest.raises(ValidationError) as exc:
            await self._post()
        assert exc.value.detail["reason"] == "unresolved_conversions"
        assert exc.value.detail["blockers"][0]["item_code"] == MINI


# ---- migration -----------------------------------------------------------------------------------

class TestTheMigration:
    def test_0033_chains_from_0032_and_is_additive(self):
        source = MIGRATION.read_text()
        assert 'revision = "0033"' in source and 'down_revision = "0032"' in source
        assert source.count("op.create_table(") == 2
        for forbidden in ("op.alter_column", "op.drop_column", "UPDATE ", "DELETE ", "FORCE ROW LEVEL SECURITY",
                          "CREATE POLICY"):
            assert forbidden not in source, forbidden
        executed = sorted(line.strip() for line in source.splitlines() if "op.execute(" in line)
        assert executed == ['op.execute("ALTER TABLE inventory_movements ENABLE ROW LEVEL SECURITY")',
                            'op.execute("ALTER TABLE inventory_receipts ENABLE ROW LEVEL SECURITY")']

    def test_the_migration_matches_the_models_column_for_column(self):
        tree = ast.parse(MIGRATION.read_text())
        tables: dict[str, set[str]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "create_table":
                tables[node.args[0].value] = {a.args[0].value for a in node.args[1:]
                                              if isinstance(a, ast.Call) and getattr(a.func, "attr", None) == "Column"}
        assert tables == {"inventory_receipts": set(InventoryReceipt.__table__.columns.keys()),
                          "inventory_movements": set(InventoryMovement.__table__.columns.keys())}

    def test_constraints_that_make_posting_idempotent_are_in_both(self):
        source = MIGRATION.read_text()
        for name in ("uq_inventory_receipts_invoice_posted", "uq_inventory_movements_idempotency",
                     "ck_inventory_movements_sign", "ck_inventory_movements_type", "ck_inventory_receipts_status"):
            assert name in source, name


# ---- the correction guard -------------------------------------------------------------------

class _GuardSession:
    def __init__(self, ledger: bool, receipt=None):
        self.ledger, self.receipt, self.statements = ledger, receipt, []

    async def execute(self, statement):
        sql = str(statement)
        self.statements.append(sql)
        value = self.ledger if "to_regclass" in sql else self.receipt
        return SimpleNamespace(scalar=lambda: value, scalars=lambda: SimpleNamespace(first=lambda: value))


class TestTheCorrectionGuard:
    async def test_before_0033_is_applied_it_only_locks(self, monkeypatch):
        monkeypatch.setattr(inv, "_ledger_present", False)
        session = _GuardSession(ledger=False)
        await inv.ensure_invoice_changeable(session, uuid.uuid4(), "correct_line_item")
        assert "FOR UPDATE" in session.statements[0] and len(session.statements) == 2

    async def test_a_posted_receipt_refuses_the_change_and_names_it(self, monkeypatch):
        from app.core.exceptions import InvoiceReceivedError

        monkeypatch.setattr(inv, "_ledger_present", False)
        receipt = SimpleNamespace(id=uuid.uuid4(), received_on=date(2026, 10, 9), confirmed_by="barj")
        with pytest.raises(InvoiceReceivedError) as exc:
            await inv.ensure_invoice_changeable(_GuardSession(ledger=True, receipt=receipt), uuid.uuid4(), "void_line_item")
        assert exc.value.http_status == 409
        assert exc.value.detail["action"] == "void_line_item" and exc.value.detail["receipt_id"] == str(receipt.id)
        assert "Reverse the receipt" in exc.value.message

    def test_every_path_that_changes_received_lines_calls_it(self):
        guarded = {
            "app/api/v1/invoices.py": {"delete_invoice", "correct_line_item", "duplicate_decision", "assign_store",
                                       "add_line_item", "void_line_item"},
            "app/services/document_lifecycle.py": {"move_to_bin"},
            "app/services/reprocess_service.py": {"reprocess"},
        }
        for path, actions in guarded.items():
            tree = ast.parse((ROOT / path).read_text())
            called = {node.args[2].value for node in ast.walk(tree)
                      if isinstance(node, ast.Call) and getattr(node.func, "id", "") in
                      ("ensure_invoice_changeable", "ensure_document_changeable")}
            assert called == actions, path

    async def test_reversal_is_flag_gated_and_needs_a_reason(self, client, endpoint):
        url = f"/api/v1/inventory/invoices/{uuid.uuid4()}/receipt/reverse"
        assert (await client.post(url, json={"receipt_id": str(uuid.uuid4()), "reason": ""})).status_code == 422
        endpoint.flag = False
        assert (await client.post(url, json={"receipt_id": str(uuid.uuid4()), "reason": "x"})).status_code == 404

    def test_the_migration_keeps_one_posted_receipt_and_reversal_history(self):
        source = MIGRATION.read_text()
        assert '"uq_inventory_receipts_invoice_posted"' in source and "status = 'POSTED'" in source
        assert "ck_inventory_receipts_reversal" in source
        assert 'sa.UniqueConstraint("invoice_id"' not in source
